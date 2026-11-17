"""Single-owner authentication for the local OpenStudy dashboard.

A persistent, scrypt-hashed owner account replaces browser Basic Auth.
Session cookies are signed, HttpOnly, SameSite=Lax; all browser mutations require
same-origin checks and a session-bound CSRF token. The Evolution webhook has an
independent secret and remains accessible without a browser session.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from collections import defaultdict, deque
from pathlib import Path
from threading import RLock
from urllib.parse import urlsplit

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, Field

from app.config import settings

COOKIE = "openstudy_session"
CSRF_HEADER = "x-csrf-token"
SESSION_AGE = 7 * 24 * 60 * 60
_LOCK = RLock()
_ATTEMPTS: dict[str, deque[float]] = defaultdict(deque)
router = APIRouter(prefix="/api/auth", tags=["Autenticação"])


def _data_dir() -> Path:
    return Path(settings.uploads_dir).parent


def _store_path() -> Path:
    return _data_dir() / ".openstudy-owner.json"


def _legacy_path() -> Path:
    return Path(settings.openstudy_admin_secret_file or _data_dir() / ".openstudy-admin-password")


def _read_store() -> dict | None:
    try:
        data = json.loads(_store_path().read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("version") != 1:
            raise ValueError("invalid store")
        return data
    except FileNotFoundError:
        return None


def _hash_password(password: str, salt: bytes) -> str:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1).hex()


def _new_store(username: str, password: str) -> dict:
    salt = secrets.token_bytes(24)
    return {"version": 1, "username": username, "salt": salt.hex(),
            "hash": _hash_password(password, salt), "key": secrets.token_hex(32)}


def _create_store(data: dict) -> bool:
    """Exclusive creation prevents two concurrent first-user registrations."""
    path = _store_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        with open(path, "x", encoding="utf-8", opener=lambda name, flags: os.open(name, flags, 0o600)) as fd:
            json.dump(data, fd, ensure_ascii=False)
        return True
    except FileExistsError:
        return False


def _legacy_password() -> str | None:
    if settings.openstudy_admin_password:
        return settings.openstudy_admin_password
    try:
        return _legacy_path().read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None


def _legacy_authorized(password: str) -> bool:
    expected = _legacy_password()
    return bool(expected) and hmac.compare_digest(password.encode(), expected.encode())


def _verify_store(store: dict, username: str, password: str) -> bool:
    if not hmac.compare_digest(username.encode(), store["username"].encode()):
        return False
    try:
        hashed = _hash_password(password, bytes.fromhex(store["salt"]))
        return hmac.compare_digest(hashed, store["hash"])
    except (ValueError, KeyError):
        return False


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _sign(raw: str, key: str) -> str:
    return _b64(hmac.new(bytes.fromhex(key), raw.encode(), hashlib.sha256).digest())


def _session_user(request: Request) -> str | None:
    token = request.cookies.get(COOKIE, "")
    if not token or len(token) > 1024:
        return None
    store = _read_store()
    if not store:
        return None
    try:
        payload, signature = token.split(".", 1)
        if not hmac.compare_digest(signature, _sign(payload, store["key"])):
            return None
        decoded = json.loads(_unb64(payload))
        now = time.time()
        if not (now - SESSION_AGE <= decoded["iat"] <= now + 30):
            return None
        if decoded["exp"] <= now or decoded["sub"] != store["username"]:
            return None
        return decoded["sub"]
    except (ValueError, KeyError, TypeError, UnicodeDecodeError, OverflowError):
        return None


def _service_authenticated(request: Request) -> bool:
    # Optional non-browser integration: n8n can send its own scoped header even
    # when a reverse proxy separately requires HTTP Basic Auth.
    token = settings.openstudy_service_api_token
    received = request.headers.get("x-openstudy-api-key", "")
    return (len(token) >= 32 and bool(received) and
            hmac.compare_digest(received.encode(), token.encode()))


def csrf_token(request: Request) -> str:
    store = _read_store()
    session = request.cookies.get(COOKIE, "")
    if not store or not session or not _session_user(request):
        return ""
    return _sign("csrf:" + session, store["key"])


def _set_session(response: Response, request: Request, username: str, key: str) -> None:
    now = int(time.time())
    payload = _b64(json.dumps({"sub": username, "iat": now, "exp": now + SESSION_AGE,
                                "nonce": secrets.token_hex(12)}, separators=(",", ":")).encode())
    token = f"{payload}.{_sign(payload, key)}"
    response.set_cookie(COOKIE, token, max_age=SESSION_AGE, httponly=True,
                        secure=request.url.scheme == "https", samesite="lax", path="/")


def _remote(request: Request) -> str:
    # Trust ASGI peer address, never untrusted X-Forwarded-For.
    return request.client.host if request.client else "unknown"


def _blocked(remote: str) -> bool:
    now = time.monotonic()
    with _LOCK:
        attempts = _ATTEMPTS[remote]
        while attempts and attempts[0] < now - 60:
            attempts.popleft()
        return len(attempts) >= 8


def _failed(remote: str) -> None:
    with _LOCK:
        attempts = _ATTEMPTS[remote]
        attempts.append(time.monotonic())
        while len(attempts) > 12:
            attempts.popleft()
        if len(_ATTEMPTS) > 4096:
            for key in list(_ATTEMPTS)[:1024]:
                if not _ATTEMPTS[key] or _ATTEMPTS[key][-1] < time.monotonic() - 60:
                    del _ATTEMPTS[key]


def _same_origin(request: Request) -> bool:
    origin = request.headers.get("origin")
    if origin:
        try:
            parsed = urlsplit(origin)
            if parsed.scheme not in ("http", "https"):
                return False
            if parsed.netloc.lower() == request.headers.get("host", "").lower():
                return True
            return origin in {s.strip() for s in settings.openstudy_allowed_origins.split(",") if s.strip()}
        except ValueError:
            return False
    # Browsers submit Sec-Fetch-Site; reject cross-site even if Origin is absent.
    return request.headers.get("sec-fetch-site", "").lower() != "cross-site"


def _security_headers(response: Response, request: Request) -> Response:
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = "frame-ancestors 'none'"
    if request.url.path.startswith("/api/") or request.url.path in ("/", "/login"):
        response.headers["Cache-Control"] = "no-store"
    if request.url.scheme == "https":
        response.headers["Strict-Transport-Security"] = "max-age=31536000"
    return response


def needs_legacy_proof() -> bool:
    return _read_store() is None and bool(_legacy_password())


def registration_open() -> bool:
    return _read_store() is None


class RegisterIn(BaseModel):
    username: str = Field(min_length=3, max_length=40)
    password: str = Field(min_length=10, max_length=200)
    legacy_password: str = Field(default="", max_length=200)


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=200)


def _bad(message: str, code: int = 400) -> JSONResponse:
    return JSONResponse({"detail": message}, status_code=code)


@router.get("/status", include_in_schema=False)
def status(request: Request):
    return {"registration_open": registration_open(), "legacy_proof_required": needs_legacy_proof(),
            "authenticated": settings.openstudy_auth_mode == "off" or bool(_session_user(request))}


@router.post("/register", include_in_schema=False)
def register(data: RegisterIn, request: Request):
    remote = _remote(request)
    if _blocked(remote):
        return _bad("Muitas tentativas. Aguarde um minuto.", 429)
    username = data.username.strip()
    if not (3 <= len(username) <= 40) or any(not(c.isalnum() or c in "_.-") for c in username):
        return _bad("Use um nome de usuário de 3 a 40 caracteres, sem espaços.")
    if len(data.password) < 10 or len(set(data.password)) < 5:
        return _bad("Escolha uma senha mais forte, com pelo menos 10 caracteres variados.")
    with _LOCK:
        if _read_store() is not None:
            return _bad("A conta principal já foi criada. Entre com suas credenciais.", 409)
        if needs_legacy_proof() and not _legacy_authorized(data.legacy_password):
            _failed(remote)
            return _bad("Informe a senha administrativa anterior para confirmar a atualização.", 403)
        store = _new_store(username, data.password)
        if not _create_store(store):
            return _bad("Uma conta já foi criada. Faça login.", 409)
    result = JSONResponse({"ok": True, "username": username})
    _set_session(result, request, username, store["key"])
    return result


@router.post("/login", include_in_schema=False)
def login(data: LoginIn, request: Request):
    remote = _remote(request)
    if _blocked(remote):
        return _bad("Muitas tentativas. Aguarde um minuto.", 429)
    store = _read_store()
    if store is None:
        # Transparent migration from the old browser Basic Auth to a hashed owner account.
        if data.username != settings.openstudy_admin_user or not _legacy_authorized(data.password):
            _failed(remote)
            return _bad("Usuário ou senha incorretos.", 401)
        with _LOCK:
            store = _read_store()
            if store is None:
                store = _new_store(data.username, data.password)
                _create_store(store)
                store = _read_store()
    if not store or not _verify_store(store, data.username, data.password):
        _failed(remote)
        return _bad("Usuário ou senha incorretos.", 401)
    result = JSONResponse({"ok": True, "username": store["username"]})
    _set_session(result, request, store["username"], store["key"])
    return result


@router.post("/logout", include_in_schema=False)
def logout():
    result = JSONResponse({"ok": True})
    result.delete_cookie(COOKIE, path="/", samesite="lax")
    return result


async def security_middleware(request: Request, call_next):
    path = request.url.path
    if path == "/api/health/live" or path.startswith("/static/"):
        return _security_headers(await call_next(request), request)
    if path.startswith("/api/whatsapp/webhook/"):
        if request.method != "POST":
            return _security_headers(_bad("Não encontrado.", 404), request)
        return _security_headers(await call_next(request), request)
    if settings.openstudy_auth_mode != "off":
        if path in ("/login", "/api/auth/status", "/api/auth/login", "/api/auth/register"):
            if request.method not in ("GET", "HEAD", "OPTIONS") and not _same_origin(request):
                return _security_headers(_bad("Origem não permitida.", 403), request)
            return _security_headers(await call_next(request), request)
        if path.startswith("/api/") and _service_authenticated(request):
            return _security_headers(await call_next(request), request)
        user = _session_user(request)
        if not user and not path.startswith("/api/") and request.method == "GET":
            return _security_headers(RedirectResponse("/login", status_code=303), request)
        if not user:
            return _security_headers(_bad("Sua sessão terminou. Faça login novamente.", 401), request)
        if request.method not in ("GET", "HEAD", "OPTIONS") and (
            not _same_origin(request) or not hmac.compare_digest(
                request.headers.get(CSRF_HEADER, ""), csrf_token(request)
            )
        ):
            return _security_headers(_bad("Sessão de segurança expirada. Atualize a página.", 403), request)
    return _security_headers(await call_next(request), request)
