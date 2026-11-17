"""Regression tests: protect web/API without breaking Evolution inbound messages."""
import os
import sqlite3

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import security
from app.config import settings


def _client():
    app = FastAPI()
    app.middleware("http")(security.security_middleware)
    app.include_router(security.router)

    @app.get("/")
    def home():
        return {"private": True}

    @app.post("/api/data")
    def write():
        return {"ok": True}

    @app.get("/api/health/live")
    def live():
        return {"status": "ok"}

    @app.post("/api/whatsapp/webhook/fake")
    def callback():
        return {"ok": True}

    @app.get("/login")
    def login():
        return {"page": True}

    return TestClient(app)


def _configure_temp_auth(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "openstudy_auth_mode", "basic")
    monkeypatch.setattr(settings, "uploads_dir", tmp_path / "uploads")
    monkeypatch.setattr(settings, "openstudy_admin_password", "")
    monkeypatch.setattr(settings, "openstudy_admin_secret_file", "")
    security._ATTEMPTS.clear()


def test_authentication_private_health_and_webhook(monkeypatch, tmp_path):
    _configure_temp_auth(monkeypatch, tmp_path)
    client = _client()
    assert client.get("/", follow_redirects=False).status_code == 303
    assert client.get("/api/health/live").json() == {"status": "ok"}
    assert client.post("/api/whatsapp/webhook/fake").status_code == 200
    assert client.get("/login").status_code == 200
    assert client.get("/api/auth/status").json()["registration_open"] is True
    created = client.post("/api/auth/register", json={"username": "student", "password": "A-secure-password-1234"})
    assert created.status_code == 200
    assert client.get("/").status_code == 200
    assert client.get("/api/auth/status").json()["registration_open"] is False
    assert client.post("/api/auth/register", json={"username": "other", "password": "Another-password-123"}).status_code == 409
    assert client.post("/api/auth/logout", headers={security.CSRF_HEADER: security.csrf_token(client_request(client))}).status_code == 200
    assert client.get("/api/data").status_code == 401
    assert client.get("/", follow_redirects=False).status_code == 303
    assert client.post("/api/auth/login", json={"username": "student", "password": "wrong"}).status_code == 401
    assert client.post("/api/auth/login", json={"username": "student", "password": "A-secure-password-1234"}).status_code == 200
    assert client.get("/").status_code == 200


def client_request(client):
    from starlette.requests import Request
    cookie = client.cookies.get(security.COOKIE, "")
    scope = {"type": "http", "method": "GET", "headers": [], "scheme": "http", "path": "/"}
    request = Request(scope)
    request._cookies = {security.COOKIE: cookie}
    return request


def test_csrf_cross_origin_and_security_headers(monkeypatch, tmp_path):
    _configure_temp_auth(monkeypatch, tmp_path)
    client = _client()
    client.post("/api/auth/register", json={"username": "admin", "password": "A-secure-password-1234"})
    csrf = security.csrf_token(client_request(client))
    assert client.post("/api/data").status_code == 403
    assert client.post("/api/data", headers={security.CSRF_HEADER: csrf, "Origin": "https://evil.example"}).status_code == 403
    allowed = client.post("/api/data", headers={security.CSRF_HEADER: csrf, "Origin": "http://testserver"})
    assert allowed.status_code == 200
    assert allowed.headers["referrer-policy"] == "no-referrer"
    assert allowed.headers["x-frame-options"] == "DENY"
    assert allowed.headers["cache-control"] == "no-store"


def test_legacy_password_migrates_without_open_registration(monkeypatch, tmp_path):
    _configure_temp_auth(monkeypatch, tmp_path)
    secret = tmp_path / "secret"
    secret.write_text("legacy-admin-password-12345")
    monkeypatch.setattr(settings, "openstudy_admin_secret_file", str(secret))
    client = _client()
    assert client.get("/api/auth/status").json()["legacy_proof_required"] is True
    assert client.post("/api/auth/register", json={"username": "owner", "password": "My-new-password-123"}).status_code == 403
    signed_up = client.post("/api/auth/register", json={"username": "owner", "password": "My-new-password-123", "legacy_password": "legacy-admin-password-12345"})
    assert signed_up.status_code == 200
    assert client.get("/").status_code == 200


def test_legacy_login_auto_migrates(monkeypatch, tmp_path):
    _configure_temp_auth(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "openstudy_admin_user", "admin")
    monkeypatch.setattr(settings, "openstudy_admin_password", "legacy-admin-password-12345")
    client = _client()
    assert client.post("/api/auth/login", json={"username": "admin", "password": "legacy-admin-password-12345"}).status_code == 200
    assert client.get("/api/auth/status").json()["registration_open"] is False
    assert client.get("/").status_code == 200

def test_migration_refuses_sqlite_destination(tmp_path):
    from scripts.migrate_sqlite_to_postgres import migrate
    source = tmp_path / "openstudy.db"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE sample (id integer)")
    try:
        migrate(source, f"sqlite:///{tmp_path / 'other.db'}")
    except RuntimeError as exc:
        assert "PostgreSQL" in str(exc)
    else:
        raise AssertionError("migration accepted unsupported database target")


def test_do_not_requeue_active_workers(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool

    from app.database import Base
    from app.models import Document, WhatsAppDocumentJob, WhatsAppMessage, utc_now
    from app.services import whatsapp_worker as worker
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    monkeypatch.setattr(worker, "SessionLocal", lambda: Session(eng))
    with Session(eng) as db:
        doc = Document(original_filename="a.md", stored_path="a.md", file_type=".md")
        msg = WhatsAppMessage(phone_e164="5512999991234", direction="inbound", status="processing", processing_started_at=utc_now())
        db.add_all([doc, msg])
        db.flush()
        db.add(WhatsAppDocumentJob(message_id=msg.id, document_id=doc.id, phone_e164=msg.phone_e164, status="processing", started_at=utc_now()))
        db.commit()
    worker.reset_interrupted("chat")
    worker.reset_interrupted("documents")
    with Session(eng) as db:
        assert db.query(WhatsAppMessage).one().status == "processing"
        assert db.query(WhatsAppDocumentJob).one().status == "processing"


def test_session_tampering_and_owner_file_protection(monkeypatch, tmp_path):
    _configure_temp_auth(monkeypatch, tmp_path)
    client = _client()
    response = client.post('/api/auth/register', json={
        'username': 'estudante', 'password': 'Password-sufficiently-strong123'
    })
    assert response.status_code == 200
    auth_file = tmp_path / '.openstudy-owner.json'
    data = auth_file.read_text(encoding='utf-8')
    assert 'Password-sufficiently-strong123' not in data
    if os.name == 'posix':
        assert not os.stat(auth_file).st_mode & 0o077
    cookie = client.cookies.get(security.COOKIE, '')
    client.cookies.set(security.COOKIE, cookie[:-1] + ('X' if cookie[-1] != 'X' else 'Y'))
    assert client.get('/', follow_redirects=False).status_code == 303


def test_registration_rejects_weak_password_and_invalid_username(monkeypatch, tmp_path):
    _configure_temp_auth(monkeypatch, tmp_path)
    client = _client()
    assert client.post('/api/auth/register', json={'username': 'x', 'password': 'Long-but-simple123'}).status_code == 422
    assert client.post('/api/auth/register', json={'username': 'okuser', 'password': 'aaaaaaaaaaaa'}).status_code == 400
    assert client.get('/api/auth/status').json()['registration_open'] is True


def test_logout_does_not_disable_whatsapp_webhook(monkeypatch, tmp_path):
    _configure_temp_auth(monkeypatch, tmp_path)
    client = _client()
    assert client.post('/api/whatsapp/webhook/fake').status_code == 200
    assert client.post('/api/whatsapp/webhook/fake', headers={'Origin':'https://example.org'}).status_code == 200


def test_service_api_token_allows_machine_calls_without_browser_session(monkeypatch, tmp_path):
    _configure_temp_auth(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, 'openstudy_service_api_token', 'test-service-key-longer-than-32-characters')
    client = _client()
    assert client.post('/api/data').status_code == 401
    assert client.post('/api/data', headers={'X-OpenStudy-Api-Key': 'short'}).status_code == 401
    assert client.post('/api/data', headers={'X-OpenStudy-Api-Key': 'test-service-key-longer-than-32-characters'}).status_code == 200
    assert client.get('/', headers={'X-OpenStudy-Api-Key': 'test-service-key-longer-than-32-characters'}, follow_redirects=False).status_code == 303
