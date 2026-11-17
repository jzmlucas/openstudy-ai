"""Queue concurrency and fast-lane regression tests."""
from datetime import timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import Document, WhatsAppDocumentJob, WhatsAppMessage, utc_now
from app.routers.whatsapp import _whatsapp_queue_metrics
from app.services import whatsapp_worker as worker
from app.services.whatsapp_assistant import whatsapp_message_priority


def engine():
    e = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(e)
    return e


def test_commands_get_fast_priority_but_stateful_tasks_do_not():
    assert whatsapp_message_priority("  MENU  ", "conversation") == 1
    assert whatsapp_message_priority("olá", "extendedTextMessage") == 1
    assert whatsapp_message_priority("FILA", "conversation") == 1
    assert whatsapp_message_priority("biblioteca", "documentMessage") == 2
    for command in ("Exercícios 5", "continuar", "dica", "explique o tema", "usar 3"):
        assert whatsapp_message_priority(command, "conversation") == 0


def test_fast_worker_claim_is_atomic_and_main_skips_processing(monkeypatch):
    e = engine()
    monkeypatch.setattr(worker, "SessionLocal", lambda: Session(e))
    with Session(e) as db:
        db.add_all([
            WhatsAppMessage(id=1, phone_e164="5511999990000", direction="inbound", status="received", priority=0),
            WhatsAppMessage(id=2, phone_e164="5511999990000", direction="inbound", status="received", priority=1),
            WhatsAppMessage(id=3, phone_e164="5511999990000", direction="inbound", status="received", priority=2),
        ])
        db.commit()
    assert worker.next_chat(fast_only=True) == 2
    assert worker.next_chat(fast_only=True) is None
    assert worker.next_chat() == 1
    assert worker.next_chat() is None
    assert worker.next_chat(media_only=True) == 3
    assert worker.next_chat(media_only=True) is None
    with Session(e) as db:
        assert db.get(WhatsAppMessage, 2).status == "processing"
        assert db.get(WhatsAppMessage, 2).processing_started_at is not None


def test_metrics_report_queue_age_without_disclosing_messages():
    e = engine()
    with Session(e) as db:
        doc = Document(original_filename="material.md", stored_path="/tmp/xx", file_type=".md")
        db.add(doc)
        db.flush()
        message = WhatsAppMessage(id=1, phone_e164="5511999990000", direction="inbound",
                                  text="texto privado", status="received", created_at=utc_now() - timedelta(seconds=35))
        db.add(message)
        db.flush()
        db.add(WhatsAppDocumentJob(message_id=message.id, document_id=doc.id, phone_e164=message.phone_e164, status="pending"))
        db.commit()
        data = _whatsapp_queue_metrics(db)
        assert data['pending'] == 1
        assert data['pending_documents'] == 1
        assert data['oldest_pending_seconds'] >= 30
        assert 'texto privado' not in str(data)
