from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.models import Document, WhatsAppDocumentJob, WhatsAppMessage
from app.routers.api import router


def test_delete_document_with_whatsapp_job(tmp_path):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    event.listen(
        engine,
        "connect",
        lambda connection, _: connection.execute("PRAGMA foreign_keys=ON"),
    )
    Base.metadata.create_all(engine)

    app = FastAPI()
    app.include_router(router)

    def override_db():
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    with Session(engine) as db:
        document = Document(
            original_filename="whatsapp.md",
            stored_path=str(tmp_path / "whatsapp.md"),
            file_type=".md",
            status="ready",
        )
        message = WhatsAppMessage(phone_e164="+5511999999999", direction="inbound")
        db.add_all([document, message])
        db.flush()
        db.add(
            WhatsAppDocumentJob(
                message_id=message.id,
                document_id=document.id,
                phone_e164=message.phone_e164,
            )
        )
        db.commit()
        document_id = document.id

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.delete(f"/api/documents/{document_id}")

    assert response.status_code == 204
    with Session(engine) as db:
        assert db.get(Document, document_id) is None
        assert db.query(WhatsAppDocumentJob).count() == 0
