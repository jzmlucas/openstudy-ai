"""Tests for browser-independent delivery and separate document processing."""
import base64
from datetime import timedelta
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import (
    Document,
    StudyCard,
    WhatsAppDocumentJob,
    WhatsAppMessage,
    WhatsAppProfile,
    utc_now,
)
from app.services import whatsapp_assistant as assistant
from app.services import whatsapp_worker as worker


def db_engine():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return engine


def test_uploaded_document_is_queued_without_blocking_chat(monkeypatch, tmp_path):
    engine = db_engine()
    sent = []
    class FakeEvolution:
        def download_media(self, _data):
            return {"base64": base64.b64encode(b"# Aula\nConteudo RAG").decode()}

    monkeypatch.setattr(assistant, "EvolutionClient", FakeEvolution)
    monkeypatch.setattr(assistant, "send_whatsapp_text", lambda db, phone, msg, **kw: sent.append(msg))
    monkeypatch.setattr(assistant.settings, "uploads_dir", tmp_path)
    with Session(engine) as db:
        phone = "5512999991234"
        profile = WhatsAppProfile(id=1, phone_e164=phone, verified=True)
        row = WhatsAppMessage(phone_e164=phone, direction="inbound", status="processing")
        db.add_all([profile, row])
        db.commit()
        assistant._process_document_message(
            db, profile, phone,
            {"message": {"documentMessage": {"fileName": "aula.md", "mimetype": "text/markdown"}}},
            defer_processing=True, message_row_id=row.id,
        )
        document = db.query(Document).one()
        job = db.query(WhatsAppDocumentJob).one()
        assert document.status == "uploaded"
        assert job.status == "pending"
        assert job.document_id == document.id and job.message_id == row.id
        assert Path(document.stored_path).read_bytes() == b"# Aula\nConteudo RAG"
        assert any("Recebi" in msg for msg in sent)


def test_recover_interrupted_chat_and_document_jobs(monkeypatch):
    engine = db_engine()
    monkeypatch.setattr(worker, "SessionLocal", lambda: Session(engine))
    with Session(engine) as db:
        doc = Document(original_filename="a.md", stored_path="/tmp/a.md", file_type=".md", status="uploaded")
        msg = WhatsAppMessage(phone_e164="5512999991234", direction="inbound", status="processing", processing_started_at=utc_now() - timedelta(hours=3))
        db.add_all([doc, msg])
        db.commit()
        db.add(WhatsAppDocumentJob(document_id=doc.id, message_id=msg.id, phone_e164=msg.phone_e164, status="processing", started_at=utc_now() - timedelta(hours=3)))
        db.commit()
    worker.reset_interrupted("chat")
    worker.reset_interrupted("documents")
    with Session(engine) as db:
        assert db.query(WhatsAppMessage).one().status == "queued"
        assert db.query(WhatsAppDocumentJob).one().status == "pending"


def test_document_job_reports_ready_document_using_shared_db(monkeypatch):
    engine = db_engine()
    sent = []
    monkeypatch.setattr(worker, "SessionLocal", lambda: Session(engine))
    monkeypatch.setattr(worker, "send_whatsapp_text", lambda db, phone, msg, **kw: sent.append(msg))
    from app.services import study_service
    def fake_document_processing(document_id, language):
        with Session(engine) as db:
            doc = db.get(Document, document_id)
            doc.status = "ready"
            db.add(StudyCard(document_id=document_id, front="Pergunta?", back="Resposta"))
            db.commit()
    monkeypatch.setattr(study_service, "process_document", fake_document_processing)
    with Session(engine) as db:
        doc = Document(original_filename="aula.md", stored_path="/tmp/aula.md", file_type=".md", status="uploaded")
        msg = WhatsAppMessage(phone_e164="5512999991234", direction="inbound", status="processed")
        db.add_all([doc, msg])
        db.commit()
        job = WhatsAppDocumentJob(document_id=doc.id, message_id=msg.id, phone_e164=msg.phone_e164, status="pending")
        db.add(job)
        db.commit()
        job_id=job.id
    assert worker.next_document() == job_id
    worker.process_document_job(job_id)
    with Session(engine) as db:
        assert db.get(WhatsAppDocumentJob, job_id).status == "completed"
        assert db.query(StudyCard).count() == 1
    assert "Material processado" in sent[0]
