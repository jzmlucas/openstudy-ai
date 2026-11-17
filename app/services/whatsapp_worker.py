"""Durable WhatsApp workers. No browser session or FastAPI request is required.

Run one chat worker and one document worker on the same mounted /app/data directory.
Workers are intentionally single-consumer per queue because the default database is SQLite.
"""
from __future__ import annotations

import argparse
import logging
import signal
import time
from datetime import timedelta

from sqlalchemy import or_, update

from app.config import settings
from app.database import SessionLocal, init_db
from app.models import Document, StudyCard, WhatsAppDocumentJob, WhatsAppMessage, utc_now
from app.services.language import normalize_language
from app.services.whatsapp_assistant import process_incoming_message, send_whatsapp_text

log = logging.getLogger(__name__)
_running = True


def _stop(*_args: object) -> None:
    global _running
    _running = False


def reset_interrupted(kind: str) -> None:
    """Recover only *stale* processing leases.

    An unrelated worker may still be handling a long LLM request. Reclaiming
    all rows at startup can generate duplicate answers and exercises.
    """
    with SessionLocal() as db:
        if kind == "chat":
            cutoff = utc_now() - timedelta(minutes=30)
            db.query(WhatsAppMessage).filter(
                WhatsAppMessage.direction == "inbound",
                WhatsAppMessage.status == "processing",
                or_(WhatsAppMessage.processing_started_at.is_(None),
                    WhatsAppMessage.processing_started_at < cutoff),
            ).update({WhatsAppMessage.status: "queued", WhatsAppMessage.processing_started_at: None}, synchronize_session=False)
        elif kind == "documents":
            cutoff = utc_now() - timedelta(hours=2)
            db.query(WhatsAppDocumentJob).filter(
                WhatsAppDocumentJob.status == "processing",
                or_(WhatsAppDocumentJob.started_at.is_(None), WhatsAppDocumentJob.started_at < cutoff),
            ).update({WhatsAppDocumentJob.status: "pending", WhatsAppDocumentJob.started_at: None}, synchronize_session=False)
        db.commit()


def next_chat(*, fast_only: bool = False, media_only: bool = False) -> int | None:
    """Claim a message exactly once, including when fast + full workers run concurrently.

    Both workers may read the same ID, but only one may transition the status to
    'processing'. The other sees zero affected rows and retries on its next poll.
    """
    with SessionLocal() as db:
        query = db.query(WhatsAppMessage.id).filter(
            WhatsAppMessage.direction == "inbound",
            WhatsAppMessage.status.in_(("received", "queued")),
        )
        if fast_only:
            query = query.filter(WhatsAppMessage.priority == 1)
        elif media_only:
            query = query.filter(WhatsAppMessage.priority == 2)
        else:
            # Media downloads are a distinct queue. A slow PDF fetch cannot block
            # normal text messages or the model-backed tutor.
            query = query.filter(WhatsAppMessage.priority != 2)
        item = query.order_by(WhatsAppMessage.created_at, WhatsAppMessage.id).first()
        if not item:
            return None
        ident = item[0]
        result = db.execute(
            update(WhatsAppMessage).where(
                WhatsAppMessage.id == ident,
                WhatsAppMessage.direction == "inbound",
                WhatsAppMessage.status.in_(("received", "queued")),
            ).values(status="processing", processing_started_at=utc_now())
        )
        db.commit()
        return ident if result.rowcount else None


def next_document() -> int | None:
    with SessionLocal() as db:
        row = (
            db.query(WhatsAppDocumentJob.id)
            .filter(WhatsAppDocumentJob.status == "pending")
            .order_by(WhatsAppDocumentJob.id)
            .first()
        )
        if row is None:
            return None
        claimed = db.execute(
            update(WhatsAppDocumentJob)
            .where(WhatsAppDocumentJob.id == row.id, WhatsAppDocumentJob.status == "pending")
            .values(status="processing", started_at=utc_now())
        )
        db.commit()
        return row.id if claimed.rowcount else None


def process_document_job(job_id: int) -> None:
    with SessionLocal() as db:
        job = db.get(WhatsAppDocumentJob, job_id)
        if job is None or job.status != "processing":
            return
        document_id, phone, language = job.document_id, job.phone_e164, normalize_language(job.language)
    try:
        from app.services.study_service import process_document
        process_document(document_id, language)
        with SessionLocal() as db:
            job = db.get(WhatsAppDocumentJob, job_id)
            document = db.get(Document, document_id)
            if not job:
                return
            if document and document.status == "ready":
                count = db.query(StudyCard).filter(StudyCard.document_id == document_id, StudyCard.archived.is_(False)).count()
                notice = (
                    f"✅ *Material processado!*\n\n*{document.original_filename}* está disponível no computador e no WhatsApp. "
                    f"Flashcards gerados/atualizados: *{count}*.\n\n"
                    "Envie *biblioteca* ou *exercicios 5* para praticar."
                )
                job.status = "completed"
            else:
                from app.services.user_errors import friendly_document_error
                notice = (
                    f"⚠️ Não consegui finalizar *{document.original_filename if document else 'o material'}* agora.\n\n"
                    f"{friendly_document_error(document.error_message if document else None)}"
                )
                job.status = "failed"
                job.error_message = (document.error_message or "Falha no processamento")[:1000] if document else "Documento não encontrado"
            job.finished_at = utc_now()
            db.commit()
            try:
                send_whatsapp_text(db, phone, notice)
            except Exception:
                log.exception("Falha ao enviar confirmação do documento %s", document_id)
    except Exception as exc:
        log.exception("Falha ao processar documento %s", document_id)
        with SessionLocal() as db:
            job = db.get(WhatsAppDocumentJob, job_id)
            if job:
                job.status = "failed"
                job.finished_at = utc_now()
                job.error_message = str(exc)[:1000]
                db.commit()
        # Jobs remain visible in the desktop; failure will not block subsequent messages.


def run(kind: str, interval: float | None = None) -> None:
    interval = interval if interval is not None else settings.whatsapp_poll_interval_seconds
    if not settings.whatsapp_enabled:
        raise SystemExit("Ative WHATSAPP_ENABLED=true no .env para iniciar workers do WhatsApp.")
    init_db()
    # Only the main worker requeues incomplete jobs at startup. Never reset all
    # processing rows when starting the fast-lane alongside an active main worker.
    if kind not in {"fast", "media"}:
        reset_interrupted(kind)
    log.info("Worker WhatsApp %s iniciado (independente do navegador).", kind)
    while _running:
        try:
            job = next_chat(fast_only=(kind == "fast"), media_only=(kind == "media")) if kind in {"chat", "fast", "media"} else next_document()
            if job is None:
                time.sleep(interval)
                continue
            if kind in {"chat", "fast", "media"}:
                started = time.monotonic()
                process_incoming_message(job, claimed=True)
                log.info("Mensagem WhatsApp %s processada por %s em %.2fs", job, kind, time.monotonic() - started)
            else:
                process_document_job(job)
        except Exception:
            log.exception("Erro no worker WhatsApp %s", kind)
            time.sleep(2)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("kind", choices=("chat", "fast", "media", "documents"))
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    run(args.kind)
