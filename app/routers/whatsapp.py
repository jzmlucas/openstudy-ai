from __future__ import annotations

import hmac
import json
import re
import secrets
import time
from datetime import timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import (
    WhatsAppLinkChallenge,
    WhatsAppMessage,
    WhatsAppProfile,
    utc_now,
)
from app.services.evolution_client import EvolutionAPIError, EvolutionClient
from app.services.whatsapp_assistant import (
    extract_message_text,
    extract_sender_phone,
    hash_link_code,
    normalize_phone,
    send_whatsapp_text,
    whatsapp_message_priority,
)

router = APIRouter(prefix="/api/whatsapp", tags=["WhatsApp Learning Assistant"])


def _require_whatsapp_enabled() -> None:
    if not settings.whatsapp_enabled:
        raise HTTPException(
            status_code=503,
            detail=(
                "A integração com WhatsApp está desativada neste modo de execução. "
                "Ative WHATSAPP_ENABLED e inicie o Docker Compose com o perfil whatsapp."
            ),
        )


class WhatsAppLinkRequest(BaseModel):
    phone: str = Field(min_length=10, max_length=24)


class WhatsAppReminderUpdate(BaseModel):
    enabled: bool = False
    days: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4, 5, 6], min_length=1, max_length=7)
    times: list[str] = Field(default_factory=lambda: ["19:00"], min_length=1, max_length=4)
    daily_goal_minutes: int = Field(default=30, ge=10, le=240)
    timezone: str = Field(default="America/Sao_Paulo", min_length=3, max_length=80)
    quiet_start: str = Field(default="22:00", pattern=r"^\d{2}:\d{2}$")
    quiet_end: str = Field(default="08:00", pattern=r"^\d{2}:\d{2}$")
    template: str = Field(
        default="📚 Hora de estudar! Sua meta hoje é {daily_goal_minutes} min e você tem {due_count} revisões pendentes. Vamos avançar um pouco hoje?",
        min_length=10,
        max_length=500,
    )

    @field_validator("days")
    @classmethod
    def validate_days(cls, value: list[int]) -> list[int]:
        if any(day < 0 or day > 6 for day in value):
            raise ValueError("Os dias devem usar 0 (segunda) até 6 (domingo).")
        return sorted(set(value))

    @field_validator("times")
    @classmethod
    def validate_times(cls, value: list[str]) -> list[str]:
        normalized = []
        for item in value:
            hour, minute = item.split(":")
            if int(hour) > 23 or int(minute) > 59:
                raise ValueError("Horário inválido.")
            normalized.append(f"{int(hour):02d}:{int(minute):02d}")
        if len(set(normalized)) != len(normalized):
            raise ValueError("Não repita horários.")
        return sorted(normalized)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("Fuso horário IANA inválido.") from exc
        return value

    @field_validator("quiet_start", "quiet_end")
    @classmethod
    def validate_quiet_time(cls, value: str) -> str:
        hour, minute = value.split(":")
        if int(hour) > 23 or int(minute) > 59:
            raise ValueError("Horário silencioso inválido.")
        return value


def _require_profile(db: Session) -> WhatsAppProfile:
    profile = db.get(WhatsAppProfile, 1)
    if profile is None or not profile.verified or not profile.phone_e164:
        raise HTTPException(status_code=409, detail="Vincule e verifique seu WhatsApp primeiro.")
    return profile


def _connection_state(payload: dict[str, Any]) -> str:
    instance = payload.get("instance") or {}
    return str(instance.get("state") or payload.get("state") or payload.get("status") or "unknown").casefold()


def _extract_qr(payload: dict[str, Any], initialized: dict[str, Any] | None = None) -> str | None:
    candidates: list[Any] = [
        payload.get("base64"),
        payload.get("qrcode"),
        (payload.get("instance") or {}).get("qrcode") if isinstance(payload.get("instance"), dict) else None,
    ]
    if initialized:
        instance = initialized.get("instance") or {}
        if isinstance(instance, dict):
            candidates.extend([instance.get("base64"), instance.get("qrcode")])
    for candidate in candidates:
        if isinstance(candidate, dict):
            candidate = candidate.get("base64") or candidate.get("code")
        if isinstance(candidate, str) and candidate.strip().startswith("data:image/"):
            return candidate.strip()
        if isinstance(candidate, str) and len(candidate.strip()) > 100 and re.fullmatch(r"[A-Za-z0-9+/]+={0,2}", candidate.strip()):
            return candidate.strip()
    return None


def _connect_with_retry(client: EvolutionClient, attempts: int = 8) -> tuple[dict[str, Any], str | None]:
    last_error: EvolutionAPIError | None = None
    result: dict[str, Any] = {}
    for attempt in range(attempts):
        try:
            result = client.connect()
            if result.get("error"):
                last_error = EvolutionAPIError(str(result.get("message") or "Evolution não conseguiu iniciar a conexão."))
                result = {}
            qr = _extract_qr(result)
            if qr or _connection_state(result) == "open":
                return result, qr
        except EvolutionAPIError as exc:
            last_error = exc
            if exc.status_code not in {400, 404, 503}:
                raise
        if attempt < attempts - 1:
            time.sleep(1.5)
    if last_error and (not result or result.get("error")):
        raise last_error
    return result, _extract_qr(result)


def _connection_or_qr(client: EvolutionClient) -> tuple[dict[str, Any], str | None]:
    try:
        current = client.connection_state()
        if current.get("error"):
            raise EvolutionAPIError(str(current.get("message") or "Falha ao consultar conexão."))
        if _connection_state(current) == "open":
            return current, None
    except EvolutionAPIError as exc:
        if exc.status_code not in {400, 404, 503}:
            raise
    return _connect_with_retry(client)


def _whatsapp_queue_metrics(db: Session) -> dict:
    from sqlalchemy import func

    from app.models import WhatsAppDocumentJob

    queue = (
        db.query(WhatsAppMessage.status, func.count(WhatsAppMessage.id))
        .filter(WhatsAppMessage.direction == "inbound")
        .group_by(WhatsAppMessage.status).all()
    )
    counts = {str(status): int(count) for status, count in queue}
    oldest = (
        db.query(func.min(WhatsAppMessage.created_at))
        .filter(WhatsAppMessage.direction == "inbound", WhatsAppMessage.status.in_(("received", "queued")))
        .scalar()
    )
    age = max(0, int((utc_now() - oldest).total_seconds())) if oldest else 0
    return {
        "pending": counts.get("queued", 0) + counts.get("received", 0),
        "processing": counts.get("processing", 0),
        "failed": counts.get("failed", 0),
        "oldest_pending_seconds": age,
        "pending_documents": db.query(WhatsAppDocumentJob).filter(
            WhatsAppDocumentJob.status.in_(("pending", "processing"))
        ).count(),
    }


@router.get("/status")
def whatsapp_status(db: Session = Depends(get_db)) -> dict:
    profile = db.get(WhatsAppProfile, 1)
    client = EvolutionClient()
    connection: dict[str, Any] = {
        "state": "not_configured" if settings.whatsapp_enabled else "disabled"
    }
    error = None
    if settings.whatsapp_enabled and client.configured:
        try:
            raw = client.connection_state()
            if raw.get("error"):
                raise EvolutionAPIError(str(raw.get("message") or "Falha ao consultar a instância."))
            connection = {"state": _connection_state(raw), "details": raw}
        except EvolutionAPIError as exc:
            connection = {"state": "unavailable"}
            error = str(exc)
    return {
        "enabled": settings.whatsapp_enabled,
        "configured": settings.whatsapp_enabled and client.configured,
        "instance_name": settings.evolution_instance_name,
        "bot_connection": connection,
        "bot_error": error,
        "webhook_ready": bool(
            settings.whatsapp_enabled
            and settings.evolution_webhook_secret
            and settings.evolution_webhook_base_url.startswith(("http://", "https://"))
        ),
        "webhook_mode": "internal_docker" if settings.whatsapp_enabled else "disabled",
        "queue": _whatsapp_queue_metrics(db),
        "profile": {
            "phone": profile.phone_e164 if profile else None,
            "verified": bool(profile and profile.verified),
            "notifications_enabled": bool(profile and profile.notifications_enabled),
            "reminders_enabled": bool(profile and profile.reminders_enabled),
            "timezone": profile.timezone if profile else "America/Sao_Paulo",
            "days": profile.reminder_days if profile else [0, 1, 2, 3, 4, 5, 6],
            "times": profile.reminder_times if profile else ["19:00"],
            "daily_goal_minutes": profile.daily_goal_minutes if profile else 30,
            "quiet_start": profile.quiet_start if profile else "22:00",
            "quiet_end": profile.quiet_end if profile else "08:00",
            "template": profile.reminder_template if profile else "",
            "selected_document_ids": profile.selected_document_ids if profile else [],
            "last_interaction_at": profile.last_interaction_at if profile else None,
        },
    }


@router.post("/instance/initialize", dependencies=[Depends(_require_whatsapp_enabled)])
def initialize_whatsapp_instance() -> dict:
    client = EvolutionClient()
    if not client.configured:
        raise HTTPException(
            status_code=503,
            detail="Configure EVOLUTION_API_URL, EVOLUTION_GLOBAL_API_KEY e EVOLUTION_INSTANCE_TOKEN no arquivo .env.",
        )
    try:
        initialized = client.initialize_instance()
        connection, qr = _connection_or_qr(client)
        if not qr:
            qr = _extract_qr(connection, initialized)
        return {
            "ok": True,
            "instance": settings.evolution_instance_name,
            "qrcode": qr,
            "connected": _connection_state(connection) == "open",
            "connection": connection,
        }
    except EvolutionAPIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/instance/connect", dependencies=[Depends(_require_whatsapp_enabled)])
def connect_whatsapp_instance() -> dict:
    client = EvolutionClient()
    if not client.configured:
        raise HTTPException(status_code=503, detail="Evolution API não configurada.")
    try:
        client.configure_webhook()
        result, qr = _connection_or_qr(client)
        qr = qr or _extract_qr(result)
        return {
            "ok": True,
            "qrcode": qr,
            "connected": _connection_state(result) == "open",
            "connection": result,
        }
    except EvolutionAPIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/instance/status", dependencies=[Depends(_require_whatsapp_enabled)])
def whatsapp_instance_status() -> dict:
    client = EvolutionClient()
    if not client.configured:
        raise HTTPException(status_code=503, detail="Evolution API não configurada.")
    try:
        result = client.connection_state()
        if result.get("error"):
            raise EvolutionAPIError(str(result.get("message") or "Falha ao consultar a instância."))
        return {"state": _connection_state(result), "details": result}
    except EvolutionAPIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/link", dependencies=[Depends(_require_whatsapp_enabled)])
def request_whatsapp_link(payload: WhatsAppLinkRequest, db: Session = Depends(get_db)) -> dict:
    try:
        phone = normalize_phone(payload.phone)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    profile = db.get(WhatsAppProfile, 1)
    if profile and profile.verified and profile.phone_e164 != phone:
        raise HTTPException(
            status_code=409,
            detail="Já existe outro número vinculado. Desvincule-o antes de cadastrar um novo.",
        )
    if profile and profile.verified and profile.phone_e164 == phone:
        return {"ok": True, "verified": True, "message": "Este número já está vinculado."}

    since = utc_now() - timedelta(hours=1)
    recent = (
        db.query(WhatsAppLinkChallenge)
        .filter(
            WhatsAppLinkChallenge.phone_e164 == phone,
            WhatsAppLinkChallenge.created_at >= since,
        )
        .count()
    )
    if recent >= 3:
        raise HTTPException(status_code=429, detail="Limite de códigos atingido. Tente novamente em uma hora.")

    client = EvolutionClient()
    if not client.configured:
        raise HTTPException(status_code=503, detail="A integração com a Evolution API não está configurada.")
    try:
        connection = client.connection_state()
        if _connection_state(connection) != "open":
            raise HTTPException(status_code=409, detail="O número oficial do OpenStudy ainda não está conectado à Evolution API.")
        client.configure_webhook()
    except EvolutionAPIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    db.query(WhatsAppLinkChallenge).filter(
        WhatsAppLinkChallenge.consumed_at.is_(None)
    ).update({"consumed_at": utc_now()}, synchronize_session=False)
    code = f"{secrets.randbelow(1_000_000):06d}"
    challenge = WhatsAppLinkChallenge(
        phone_e164=phone,
        code_hash=hash_link_code(phone, code),
        expires_at=utc_now() + timedelta(minutes=10),
    )
    db.add(challenge)
    if profile is None:
        db.add(WhatsAppProfile(id=1, phone_e164=phone, verified=False))
    else:
        profile.phone_e164 = phone
        profile.verified = False
    db.commit()

    try:
        send_whatsapp_text(
            db,
            phone,
            (
                "🔐 *Verificação do OpenStudy AI*\n\n"
                f"Seu código é: *{code}*\n\n"
                "Envie este código respondendo a esta conversa no formato:\n"
                f"*VINCULAR {code}*\n\n"
                "O código expira em 10 minutos. Se você não solicitou esta vinculação, ignore esta mensagem."
            ),
            log_message=False,
        )
    except Exception as exc:
        challenge.consumed_at = utc_now()
        db.commit()
        raise HTTPException(status_code=502, detail=f"Não foi possível enviar o código pelo WhatsApp: {exc}") from exc

    return {
        "ok": True,
        "verified": False,
        "phone": phone,
        "expires_in_seconds": 600,
        "message": "Código enviado. Responda à conversa do número oficial do OpenStudy com VINCULAR e o código.",
    }


@router.post("/unlink", dependencies=[Depends(_require_whatsapp_enabled)])
def unlink_whatsapp(db: Session = Depends(get_db)) -> dict:
    profile = _require_profile(db)
    profile.verified = False
    profile.phone_e164 = None
    profile.chat_jid = None
    profile.reminders_enabled = False
    profile.notifications_enabled = False
    profile.conversation_state = {}
    db.query(WhatsAppLinkChallenge).filter(
        WhatsAppLinkChallenge.consumed_at.is_(None)
    ).update({"consumed_at": utc_now()}, synchronize_session=False)
    db.commit()
    return {"ok": True, "message": "Número desvinculado e lembretes desativados."}


@router.get("/reminders", dependencies=[Depends(_require_whatsapp_enabled)])
def get_whatsapp_reminders(db: Session = Depends(get_db)) -> dict:
    profile = _require_profile(db)
    return {
        "enabled": profile.reminders_enabled,
        "notifications_enabled": profile.notifications_enabled,
        "days": profile.reminder_days or [],
        "times": profile.reminder_times or [],
        "daily_goal_minutes": profile.daily_goal_minutes,
        "timezone": profile.timezone,
        "quiet_start": profile.quiet_start,
        "quiet_end": profile.quiet_end,
        "template": profile.reminder_template,
    }


@router.put("/reminders", dependencies=[Depends(_require_whatsapp_enabled)])
def update_whatsapp_reminders(
    payload: WhatsAppReminderUpdate,
    db: Session = Depends(get_db),
) -> dict:
    profile = _require_profile(db)
    profile.reminders_enabled = payload.enabled
    profile.notifications_enabled = True
    profile.reminder_days = payload.days
    profile.reminder_times = payload.times
    profile.daily_goal_minutes = payload.daily_goal_minutes
    profile.timezone = payload.timezone
    profile.quiet_start = payload.quiet_start
    profile.quiet_end = payload.quiet_end
    profile.reminder_template = payload.template
    db.commit()
    return {"ok": True, "enabled": profile.reminders_enabled, "days": profile.reminder_days, "times": profile.reminder_times}


@router.post("/reminders/test", dependencies=[Depends(_require_whatsapp_enabled)])
def test_whatsapp_reminder(db: Session = Depends(get_db)) -> dict:
    profile = _require_profile(db)
    from app.models import CardReviewState, StudyCard

    due_count = (
        db.query(CardReviewState)
        .join(StudyCard, StudyCard.id == CardReviewState.card_id)
        .filter(StudyCard.archived.is_(False), CardReviewState.due_at <= utc_now())
        .count()
    )
    message = (
        profile.reminder_template
        .replace("{due_count}", str(due_count))
        .replace("{daily_goal_minutes}", str(profile.daily_goal_minutes))
    )
    try:
        send_whatsapp_text(db, profile.phone_e164, f"🧪 *Teste de lembrete*\n\n{message}")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Falha ao enviar mensagem: {exc}") from exc
    return {"ok": True}


@router.get("/messages", dependencies=[Depends(_require_whatsapp_enabled)])
def list_whatsapp_messages(limit: int = 30, db: Session = Depends(get_db)) -> list[dict]:
    _require_profile(db)
    rows = (
        db.query(WhatsAppMessage)
        .order_by(WhatsAppMessage.created_at.desc())
        .limit(min(max(limit, 1), 100))
        .all()
    )
    return [
        {
            "id": row.id,
            "direction": row.direction,
            "type": row.message_type,
            "text": row.text[:500],
            "status": row.status,
            "created_at": row.created_at,
        }
        for row in reversed(rows)
    ]


@router.post("/webhook/{token}", dependencies=[Depends(_require_whatsapp_enabled)])
async def evolution_webhook(
    token: str,
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    expected = settings.evolution_webhook_secret
    if not expected or not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=404, detail="Not found.")
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > 4 * 1024 * 1024:
                raise HTTPException(status_code=413, detail="Webhook payload too large.")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid content length.") from exc
    raw_body = await request.body()
    if len(raw_body) > 4 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Webhook payload too large.")
    try:
        payload = json.loads(raw_body)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON.") from exc
    if not isinstance(payload, dict):
        return {"ok": True, "ignored": True}

    instance = str(payload.get("instance") or "")
    if instance != settings.evolution_instance_name:
        raise HTTPException(status_code=403, detail="Unexpected or missing Evolution instance.")

    event = str(payload.get("event") or "").upper().replace(".", "_")
    if event not in {"MESSAGES_UPSERT", "MESSAGES_UPSERTS"}:
        return {"ok": True, "ignored": True}

    data = payload.get("data") or {}
    items = data if isinstance(data, list) else [data]
    queued = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        key = item.get("key") or {}
        if key.get("fromMe") is True or str(key.get("fromMe")).casefold() == "true":
            continue
        phone = extract_sender_phone(item)
        if not phone:
            continue
        external_id = str(key.get("id") or "") or None
        if external_id and db.query(WhatsAppMessage.id).filter_by(external_message_id=external_id).first():
            continue
        message_type = str(item.get("messageType") or "text")[:40]
        text = extract_message_text(item)[:6000]
        row = WhatsAppMessage(
            external_message_id=external_id,
            phone_e164=phone,
            direction="inbound",
            message_type=message_type,
            text=text,
            payload=item,
            status="received",
            priority=whatsapp_message_priority(text, message_type),
        )
        db.add(row)
        try:
            db.commit()
            db.refresh(row)
        except IntegrityError:
            db.rollback()
            continue
        # The dedicated Docker worker consumes this committed row; no in-request jobs.
        queued += 1

    return {"ok": True, "queued": queued}
