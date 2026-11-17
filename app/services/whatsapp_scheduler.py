from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, time as clock_time
from zoneinfo import ZoneInfo

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.database import SessionLocal
from app.models import (
    CardReviewState,
    StudyCard,
    WhatsAppMessage,
    WhatsAppOutbox,
    WhatsAppProfile,
    utc_now,
)
from app.services.evolution_client import EvolutionClient


REMINDER_GRACE_MINUTES = 10
MAX_OUTBOX_ATTEMPTS = 5


def _parse_time(value: str) -> clock_time:
    hour, minute = value.split(":")
    return clock_time(int(hour), int(minute))


def _inside_quiet_hours(now: clock_time, start: clock_time, end: clock_time) -> bool:
    if start == end:
        return False
    if start < end:
        return start <= now < end
    return now >= start or now < end


def _enqueue_due_reminders(db, profile: WhatsAppProfile, now_utc: datetime) -> int:
    if (
        not profile.verified
        or not profile.phone_e164
        or not profile.notifications_enabled
        or not profile.reminders_enabled
    ):
        return 0
    try:
        zone = ZoneInfo(profile.timezone)
    except Exception:
        zone = ZoneInfo("America/Sao_Paulo")
    local_now = now_utc.replace(tzinfo=ZoneInfo("UTC")).astimezone(zone)
    local_minute = local_now.hour * 60 + local_now.minute
    quiet_start = _parse_time(profile.quiet_start or "22:00")
    quiet_end = _parse_time(profile.quiet_end or "08:00")
    if _inside_quiet_hours(local_now.time().replace(second=0, microsecond=0), quiet_start, quiet_end):
        return 0
    if local_now.weekday() not in (profile.reminder_days or []):
        return 0

    due_count = (
        db.query(CardReviewState)
        .join(StudyCard, StudyCard.id == CardReviewState.card_id)
        .filter(StudyCard.archived.is_(False), CardReviewState.due_at <= now_utc)
        .count()
    )
    queued = 0
    for scheduled in profile.reminder_times or []:
        try:
            scheduled_time = _parse_time(scheduled)
        except (ValueError, TypeError):
            continue
        scheduled_minute = scheduled_time.hour * 60 + scheduled_time.minute
        lateness = local_minute - scheduled_minute
        if not 0 <= lateness <= REMINDER_GRACE_MINUTES:
            continue
        if _inside_quiet_hours(scheduled_time, quiet_start, quiet_end):
            continue
        dedupe_key = f"study-reminder:{local_now.date().isoformat()}:{scheduled_time.strftime('%H%M')}"
        message = (profile.reminder_template or "").replace("{due_count}", str(due_count)).replace(
            "{daily_goal_minutes}", str(profile.daily_goal_minutes)
        )
        if not message.strip():
            message = f"📚 Hora de estudar! Você tem {due_count} revisões pendentes."
        db.add(
            WhatsAppOutbox(
                dedupe_key=dedupe_key,
                phone_e164=profile.phone_e164,
                message_text=message,
                status="pending",
                attempts=0,
                next_attempt_at=now_utc,
            )
        )
        try:
            db.commit()
            queued += 1
        except IntegrityError:
            db.rollback()
    return queued


def _dispatch_outbox(db, now_utc: datetime, limit: int = 10) -> int:
    db.query(WhatsAppOutbox).filter(
        WhatsAppOutbox.status == "processing",
        WhatsAppOutbox.locked_at < now_utc - timedelta(minutes=10),
    ).update(
        {
            WhatsAppOutbox.status: "pending",
            WhatsAppOutbox.locked_at: None,
            WhatsAppOutbox.next_attempt_at: now_utc,
        },
        synchronize_session=False,
    )
    db.commit()

    ids = [
        row.id
        for row in db.query(WhatsAppOutbox.id)
        .filter(
            WhatsAppOutbox.status == "pending",
            WhatsAppOutbox.next_attempt_at <= now_utc,
            WhatsAppOutbox.attempts < MAX_OUTBOX_ATTEMPTS,
        )
        .order_by(WhatsAppOutbox.created_at.asc())
        .limit(limit)
        .all()
    ]
    client = EvolutionClient()
    sent = 0
    for job_id in ids:
        claim = db.execute(
            update(WhatsAppOutbox)
            .where(
                WhatsAppOutbox.id == job_id,
                WhatsAppOutbox.status == "pending",
                WhatsAppOutbox.next_attempt_at <= now_utc,
            )
            .values(
                status="processing",
                locked_at=now_utc,
                attempts=WhatsAppOutbox.attempts + 1,
            )
        )
        db.commit()
        if claim.rowcount != 1:
            continue
        job = db.get(WhatsAppOutbox, job_id)
        profile = db.get(WhatsAppProfile, 1)
        if not job:
            continue
        if (
            not profile
            or not profile.verified
            or not profile.notifications_enabled
            or profile.phone_e164 != job.phone_e164
        ):
            job.status = "cancelled"
            job.last_error = "Destinatário não está mais vinculado ou autorizou notificações."
            db.commit()
            continue
        if not profile.reminders_enabled:
            job.status = "cancelled"
            job.last_error = "Lembretes desativados pelo aluno."
            db.commit()
            continue
        try:
            response = client.send_text(job.phone_e164, job.message_text)
            job.status = "sent"
            job.sent_at = utc_now()
            job.locked_at = None
            job.last_error = None
            db.add(
                WhatsAppMessage(
                    phone_e164=job.phone_e164,
                    direction="outbound",
                    message_type="reminder",
                    text=job.message_text,
                    payload={"evolution_response": response, "outbox_id": job.id},
                    status="sent",
                )
            )
            db.commit()
            sent += 1
        except Exception as exc:
            attempts = job.attempts
            job.last_error = str(exc)[:1200]
            job.locked_at = None
            if attempts >= MAX_OUTBOX_ATTEMPTS:
                job.status = "failed"
            else:
                job.status = "pending"
                backoff_seconds = [30, 120, 600, 1800, 3600][min(attempts - 1, 4)]
                job.next_attempt_at = utc_now() + timedelta(seconds=backoff_seconds)
            db.commit()
    return sent


def whatsapp_scheduler_tick() -> dict[str, int]:
    db = SessionLocal()
    try:
        now = utc_now()
        profile = db.get(WhatsAppProfile, 1)
        queued = _enqueue_due_reminders(db, profile, now) if profile else 0
        sent = _dispatch_outbox(db, now)
        return {"queued": queued, "sent": sent}
    finally:
        db.close()


async def run_whatsapp_scheduler() -> None:
    if not settings.whatsapp_enabled:
        return
    while True:
        try:
            if settings.whatsapp_scheduler_enabled:
                await asyncio.to_thread(whatsapp_scheduler_tick)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        await asyncio.sleep(settings.whatsapp_scheduler_interval_seconds)
