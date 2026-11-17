import base64
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.models import (
    Document,
    Exercise,
    ExerciseAttempt,
    ExerciseWorkspace,
    StudyCard,
    WhatsAppLinkChallenge,
    WhatsAppMessage,
    WhatsAppOutbox,
    WhatsAppProfile,
    utc_now,
)
from app.services import whatsapp_assistant as assistant
from app.services import whatsapp_scheduler as scheduler
from app.services.evolution_client import EvolutionClient
from app.services.whatsapp_assistant import (
    extract_message_text,
    extract_sender_phone,
    hash_link_code,
    normalize_phone,
)


def make_db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return engine


def test_normalize_phone_adds_brazil_country_code_and_keeps_international():
    assert normalize_phone("(12) 99999-1234") == "5512999991234"
    assert normalize_phone("+1 415 555 2671") == "14155552671"
    with pytest.raises(ValueError):
        normalize_phone("123")


def test_extract_sender_and_text_from_evolution_payload():
    data = {
        "key": {"remoteJid": "5512999991234@s.whatsapp.net", "fromMe": False},
        "messageType": "extendedTextMessage",
        "message": {"extendedTextMessage": {"text": "Explique RAG"}},
    }
    assert extract_sender_phone(data) == "5512999991234"
    assert extract_message_text(data) == "Explique RAG"

    group = {"key": {"remoteJid": "120363000000@g.us"}}
    assert extract_sender_phone(group) is None


def test_link_code_is_phone_bound_one_time_and_enables_profile(monkeypatch):
    engine = make_db()
    sent = []
    monkeypatch.setattr(assistant, "send_whatsapp_text", lambda db, phone, text, **kwargs: sent.append(text))
    with Session(engine) as db:
        phone = "5512999991234"
        challenge = WhatsAppLinkChallenge(
            phone_e164=phone,
            code_hash=hash_link_code(phone, "123456"),
            expires_at=utc_now() + timedelta(minutes=10),
        )
        profile = WhatsAppProfile(id=1, phone_e164=phone, verified=False)
        db.add_all([challenge, profile])
        db.commit()

        assert assistant._handle_link_code(db, phone, "VINCULAR 123456") is True
        db.refresh(profile)
        db.refresh(challenge)
        assert profile.verified is True
        assert profile.chat_jid == f"{phone}@s.whatsapp.net"
        assert profile.reminders_enabled is False
        assert challenge.consumed_at is not None
        assert len(sent) == 1

        assert assistant._handle_link_code(db, phone, "VINCULAR 123456") is False


def test_link_code_wrong_attempt_is_counted_and_does_not_verify(monkeypatch):
    engine = make_db()
    monkeypatch.setattr(assistant, "send_whatsapp_text", lambda *args, **kwargs: None)
    with Session(engine) as db:
        phone = "5512999991234"
        profile = WhatsAppProfile(id=1, phone_e164=phone, verified=False)
        challenge = WhatsAppLinkChallenge(
            phone_e164=phone,
            code_hash=hash_link_code(phone, "123456"),
            expires_at=utc_now() + timedelta(minutes=10),
        )
        db.add_all([profile, challenge])
        db.commit()

        assert assistant._handle_link_code(db, phone, "VINCULAR 000000") is True
        db.refresh(profile)
        db.refresh(challenge)
        assert profile.verified is False
        assert challenge.attempts == 1


def test_link_code_cannot_be_used_from_another_phone():
    engine = make_db()
    with Session(engine) as db:
        db.add(
            WhatsAppLinkChallenge(
                phone_e164="5511999990000",
                code_hash=hash_link_code("5511999990000", "123456"),
                expires_at=utc_now() + timedelta(minutes=10),
            )
        )
        db.commit()
        assert assistant._handle_link_code(db, "5511999991111", "VINCULAR 123456") is False


def test_reminder_scheduler_enqueues_once_with_local_schedule():
    engine = make_db()
    with Session(engine) as db:
        now = datetime(2026, 11, 17, 19, 4)
        profile = WhatsAppProfile(
            id=1,
            phone_e164="5512999991234",
            chat_jid="5512999991234@s.whatsapp.net",
            verified=True,
            notifications_enabled=True,
            reminders_enabled=True,
            timezone="UTC",
            reminder_days=[1],
            reminder_times=["19:00"],
            quiet_start="22:00",
            quiet_end="08:00",
            daily_goal_minutes=60,
            reminder_template="Meta {daily_goal_minutes} min: {due_count} revisões.",
        )
        db.add(profile)
        db.commit()

        assert scheduler._enqueue_due_reminders(db, profile, now) == 1
        assert scheduler._enqueue_due_reminders(db, profile, now) == 0
        job = db.query(WhatsAppOutbox).one()
        assert job.status == "pending"
        assert job.dedupe_key == "study-reminder:2026-11-17:1900"
        assert "0 revisões" in job.message_text
        assert "Meta 60 min" in job.message_text


def test_reminder_scheduler_respects_quiet_hours():
    engine = make_db()
    with Session(engine) as db:
        now = datetime(2026, 11, 17, 23, 5)
        profile = WhatsAppProfile(
            id=1,
            phone_e164="5512999991234",
            verified=True,
            notifications_enabled=True,
            reminders_enabled=True,
            timezone="UTC",
            reminder_days=[1],
            reminder_times=["23:00"],
            quiet_start="22:00",
            quiet_end="08:00",
        )
        db.add(profile)
        db.commit()
        assert scheduler._enqueue_due_reminders(db, profile, now) == 0
        assert db.query(WhatsAppOutbox).count() == 0


def test_outbox_retries_failed_delivery_and_records_success(monkeypatch):
    engine = make_db()
    calls = {"count": 0}

    def fake_send(self, phone, text):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("temporary Evolution outage")
        return {"key": {"id": "message-1"}}

    monkeypatch.setattr(EvolutionClient, "send_text", fake_send)
    with Session(engine) as db:
        phone = "5512999991234"
        db.add(WhatsAppProfile(id=1, phone_e164=phone, verified=True, notifications_enabled=True, reminders_enabled=True))
        db.add(WhatsAppOutbox(
            dedupe_key="test-reminder-1",
            phone_e164=phone,
            message_text="Hora de estudar.",
            status="pending",
            next_attempt_at=utc_now(),
        ))
        db.commit()

        now = utc_now()
        assert scheduler._dispatch_outbox(db, now) == 0
        job = db.query(WhatsAppOutbox).one()
        assert job.status == "pending"
        assert job.attempts == 1
        job.next_attempt_at = utc_now() - timedelta(seconds=1)
        db.commit()

        assert scheduler._dispatch_outbox(db, utc_now()) == 1
        db.refresh(job)
        assert job.status == "sent"
        assert db.query(WhatsAppMessage).filter_by(direction="outbound").count() == 1


def test_evolution_client_uses_global_key_for_admin_and_instance_token_for_chat(monkeypatch):
    from app.services import evolution_client as module

    monkeypatch.setattr(module.settings, "evolution_api_url", "http://evolution.local")
    monkeypatch.setattr(module.settings, "evolution_instance_name", "openstudyai")
    monkeypatch.setattr(module.settings, "evolution_global_api_key", "global-secret")
    monkeypatch.setattr(module.settings, "evolution_instance_token", "instance-secret")
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return SimpleNamespace(
            content=b'{"ok":true}',
            is_error=False,
            json=lambda: {"ok": True},
        )

    monkeypatch.setattr(module.httpx, "request", fake_request)
    client = EvolutionClient()
    client._request("POST", "/instance/create", payload={}, use_global_key=True)
    client.send_text("5512999991234", "Oi")

    assert calls[0][2]["headers"]["apikey"] == "global-secret"
    assert calls[1][2]["headers"]["apikey"] == "instance-secret"
    assert calls[1][1].endswith("/message/sendText/openstudyai")


def test_webhook_secret_instance_validation_and_message_deduplication(monkeypatch):
    from app.routers import whatsapp as routes

    engine = make_db()
    app = FastAPI()
    app.include_router(routes.router)

    def override_db():
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr(routes.settings, "whatsapp_enabled", True)
    monkeypatch.setattr(routes.settings, "evolution_webhook_secret", "unit-test-secret")
    monkeypatch.setattr(routes.settings, "evolution_instance_name", "openstudyai")
    processed = []

    payload = {
        "event": "MESSAGES_UPSERT",
        "instance": "openstudyai",
        "data": {
            "key": {
                "id": "external-message-1",
                "remoteJid": "5512999991234@s.whatsapp.net",
                "fromMe": False,
            },
            "messageType": "conversation",
            "message": {"conversation": "Oi OpenStudy"},
        },
    }
    with TestClient(app) as client:
        assert client.post("/api/whatsapp/webhook/wrong", json=payload).status_code == 404
        assert client.post(
            "/api/whatsapp/webhook/unit-test-secret",
            json={**payload, "instance": "unexpected"},
        ).status_code == 403
        first = client.post("/api/whatsapp/webhook/unit-test-secret", json=payload)
        second = client.post("/api/whatsapp/webhook/unit-test-secret", json=payload)

    assert first.status_code == 200
    assert first.json()["queued"] == 1
    assert second.status_code == 200
    assert second.json()["queued"] == 0
    assert len(processed) == 0  # Processing is durable; the Docker worker consumes it.
    with Session(engine) as db:
        assert db.query(WhatsAppMessage).count() == 1
        assert db.query(WhatsAppMessage).one().status == "received"


def test_webhook_acknowledges_message_insert_race(monkeypatch):
    from app.routers import whatsapp as routes

    engine = make_db()
    app = FastAPI()
    app.include_router(routes.router)

    def override_db():
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr(routes.settings, "whatsapp_enabled", True)
    monkeypatch.setattr(routes.settings, "evolution_webhook_secret", "unit-test-secret")
    monkeypatch.setattr(routes.settings, "evolution_instance_name", "openstudyai")

    def duplicate_insert(_session):
        raise IntegrityError("insert", {}, Exception("duplicate"))

    monkeypatch.setattr(Session, "commit", duplicate_insert)
    payload = {
        "event": "MESSAGES_UPSERT",
        "instance": "openstudyai",
        "data": {
            "key": {
                "id": "external-message-race",
                "remoteJid": "5512999991234@s.whatsapp.net",
                "fromMe": False,
            },
            "messageType": "conversation",
            "message": {"conversation": "VINCULAR 123456"},
        },
    }

    with TestClient(app) as client:
        response = client.post("/api/whatsapp/webhook/unit-test-secret", json=payload)

    assert response.status_code == 200
    assert response.json()["queued"] == 0


def test_reminder_settings_reject_invalid_time_and_timezone():
    from app.routers.whatsapp import WhatsAppReminderUpdate

    with pytest.raises(Exception):
        WhatsAppReminderUpdate(times=["29:99"])
    with pytest.raises(Exception):
        WhatsAppReminderUpdate(timezone="Mars/Olympus")



def test_whatsapp_exercises_command_generates_server_side_questions(monkeypatch):
    from app.routers import api
    from app.services import whatsapp_assistant

    engine = make_db()
    sent = []

    class FakeClient:
        def __init__(self, db=None):
            self.db = db

        def generate_exercises(self, cards, count, difficulty, **kwargs):
            return [
                {
                    "question": f"Questão {index + 1} sobre RAG?",
                    "kind": "multiple_choice",
                    "options": ["Recuperação", "Treinamento", "Compilação"],
                    "correct_answer": "Recuperação",
                    "explanation": "RAG recupera contexto relevante.",
                    "difficulty": "intermediario",
                    "card_ids": [cards[index]["id"]],
                    "purpose": "practice",
                    "bloom_level": "understand",
                    "topic": "RAG",
                    "hints": [],
                    "source_excerpt": "",
                    "objective": "Verificar o conceito.",
                }
                for index in range(count)
            ]

    monkeypatch.setattr(api, "OllamaClient", FakeClient)
    monkeypatch.setattr(whatsapp_assistant, "send_whatsapp_text", lambda db, phone, text, **kwargs: sent.append(text))
    with Session(engine) as db:
        document = Document(original_filename="rag.md", stored_path="/tmp/rag.md", file_type=".md")
        db.add(document)
        db.flush()
        for index in range(4):
            db.add(StudyCard(
                document_id=document.id,
                front=f"RAG conceito {index}",
                back="Recuperação aumentada",
                tags=["RAG"],
            ))
        profile = WhatsAppProfile(id=1, phone_e164="5512999991234", verified=True)
        db.add(profile)
        db.commit()

        assert whatsapp_assistant._handle_command(db, profile, profile.phone_e164, "exercicios 3 RAG") is True
        state = profile.conversation_state
        assert state["mode"] == "shared_exercise"
        workspace = db.get(ExerciseWorkspace, state["workspace_id"])
        assert len(workspace.exercise_ids) == 3
        assert workspace.source == "whatsapp"
        assert db.query(Exercise).filter_by(purpose="practice").count() == 3
        assert any("Preparei *3 exercícios*" in item for item in sent)
        assert any("Questão 1/3" in item for item in sent)


def test_whatsapp_multiple_choice_answer_advances_and_awards_learning_progress(monkeypatch):
    from app.services import whatsapp_assistant

    engine = make_db()
    sent = []
    monkeypatch.setattr(whatsapp_assistant, "send_whatsapp_text", lambda db, phone, text, **kwargs: sent.append(text))
    with Session(engine) as db:
        document = Document(original_filename="rag.md", stored_path="/tmp/rag.md", file_type=".md")
        db.add(document)
        db.flush()
        card = StudyCard(document_id=document.id, front="O que é RAG?", back="Recuperação.", tags=["RAG"])
        db.add(card)
        db.flush()
        exercise = Exercise(
            question="O que RAG faz?",
            kind="multiple_choice",
            options=["Recupera contexto", "Treina o modelo"],
            correct_answer="Recupera contexto",
            explanation="RAG recupera contexto para fundamentar a resposta.",
            card_ids=[card.id],
            topic="RAG",
        )
        db.add(exercise)
        db.flush()
        profile = WhatsAppProfile(
            id=1,
            phone_e164="5512999991234",
            verified=True,
            conversation_state={
                "mode": "exercise",
                "exercise_ids": [exercise.id],
                "exercise_index": 0,
                "current_exercise_started_at": 1,
            },
        )
        db.add(profile)
        db.commit()

        handled = whatsapp_assistant._answer_active_exercise(
            db, profile, profile.phone_e164, "1"
        )
        assert handled is True
        assert profile.conversation_state == {}
        assert db.query(WhatsAppMessage).count() == 0
        assert any("Correto" in item for item in sent)
        assert any("Rodada concluída" in item for item in sent)



def test_link_endpoint_sends_one_time_code_and_persists_only_hash(monkeypatch):
    from app.routers import whatsapp as routes

    engine = make_db()
    app = FastAPI()
    app.include_router(routes.router)

    def override_db():
        with Session(engine) as db:
            yield db

    class FakeEvolution:
        configured = True

        def connection_state(self):
            return {"instance": {"state": "open"}}

        def configure_webhook(self):
            return {"enabled": True}

    sent = []
    monkeypatch.setattr(routes, "EvolutionClient", FakeEvolution)
    monkeypatch.setattr(routes, "send_whatsapp_text", lambda db, phone, text, **kwargs: sent.append((phone, text)))
    monkeypatch.setattr(routes.settings, "whatsapp_enabled", True)
    app.dependency_overrides[get_db] = override_db

    with TestClient(app) as client:
        response = client.post("/api/whatsapp/link", json={"phone": "+55 12 99999-1234"})

    assert response.status_code == 200
    assert response.json()["verified"] is False
    assert sent and sent[0][0] == "5512999991234"
    with Session(engine) as db:
        challenge = db.query(WhatsAppLinkChallenge).one()
        assert len(challenge.code_hash) == 64
        assert challenge.code_hash not in sent[0][1]
        assert db.get(WhatsAppProfile, 1).verified is False


def test_reminder_preferences_endpoint_persists_timezone_days_and_goal(monkeypatch):
    from app.routers import whatsapp as routes

    engine = make_db()
    app = FastAPI()
    app.include_router(routes.router)

    def override_db():
        with Session(engine) as db:
            yield db

    with Session(engine) as db:
        db.add(WhatsAppProfile(id=1, phone_e164="5512999991234", verified=True))
        db.commit()

    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr(routes.settings, "whatsapp_enabled", True)
    payload = {
        "enabled": True,
        "days": [0, 2, 4],
        "times": ["07:30", "19:15"],
        "daily_goal_minutes": 60,
        "timezone": "America/Sao_Paulo",
        "quiet_start": "22:00",
        "quiet_end": "06:30",
        "template": "Estude por {daily_goal_minutes} min. Revisões: {due_count}.",
    }
    with TestClient(app) as client:
        updated = client.put("/api/whatsapp/reminders", json=payload)
        fetched = client.get("/api/whatsapp/reminders")

    assert updated.status_code == 200
    assert fetched.status_code == 200
    assert fetched.json()["enabled"] is True
    assert fetched.json()["daily_goal_minutes"] == 60
    assert fetched.json()["days"] == [0, 2, 4]
    assert fetched.json()["times"] == ["07:30", "19:15"]



def test_whatsapp_document_upload_reuses_existing_ingestion_pipeline(monkeypatch, tmp_path):
    from app.services import whatsapp_assistant

    engine = make_db()
    file_content = b"# RAG\n\nRetrieval augmented generation."
    sent = []

    class FakeEvolution:
        def download_media(self, message_data):
            return {"base64": base64.b64encode(file_content).decode("ascii")}

    monkeypatch.setattr(whatsapp_assistant, "EvolutionClient", FakeEvolution)
    monkeypatch.setattr(whatsapp_assistant, "send_whatsapp_text", lambda db, phone, text, **kwargs: sent.append(text))
    monkeypatch.setattr(whatsapp_assistant.settings, "uploads_dir", tmp_path)

    with Session(engine) as db:
        profile = WhatsAppProfile(id=1, phone_e164="5512999991234", verified=True)

        def fake_process_document(document_id, language):
            document = db.get(Document, document_id)
            document.status = "ready"
            db.add(StudyCard(
                document_id=document.id,
                front="O que é RAG?",
                back="Recuperação aumentada.",
                tags=["RAG"],
            ))
            db.commit()

        from app.services import study_service
        monkeypatch.setattr(study_service, "process_document", fake_process_document)

        whatsapp_assistant._process_document_message(
            db,
            profile,
            profile.phone_e164,
            {
                "messageType": "documentMessage",
                "key": {"id": "file-1"},
                "message": {
                    "documentMessage": {
                        "fileName": "anotacoes.md",
                        "mimetype": "text/markdown",
                        "fileLength": len(file_content),
                    }
                },
            },
        )

        document = db.query(Document).one()
        assert document.status == "ready"
        assert document.original_filename == "anotacoes.md"
        assert Path(document.stored_path).read_bytes() == file_content
        assert db.query(StudyCard).filter_by(document_id=document.id).count() == 1
        assert any("Material processado" in item for item in sent)


def test_whatsapp_document_upload_rejects_oversized_file_before_download(monkeypatch):
    from app.services import whatsapp_assistant

    engine = make_db()
    sent = []

    class FakeEvolution:
        def download_media(self, message_data):
            raise AssertionError("Oversized media must not be downloaded")

    monkeypatch.setattr(whatsapp_assistant, "EvolutionClient", FakeEvolution)
    monkeypatch.setattr(whatsapp_assistant, "send_whatsapp_text", lambda db, phone, text, **kwargs: sent.append(text))
    monkeypatch.setattr(whatsapp_assistant.settings, "whatsapp_max_media_mb", 1)

    with Session(engine) as db:
        profile = WhatsAppProfile(id=1, phone_e164="5512999991234", verified=True)
        whatsapp_assistant._process_document_message(
            db,
            profile,
            profile.phone_e164,
            {
                "messageType": "documentMessage",
                "message": {
                    "documentMessage": {
                        "fileName": "grande.pdf",
                        "mimetype": "application/pdf",
                        "fileLength": 2 * 1024 * 1024,
                    }
                },
            },
        )
        assert db.query(Document).count() == 0
        assert any("excede o limite" in item for item in sent)



def test_evolution_instance_creation_uses_documented_token_and_webhook_contract(monkeypatch):
    from app.services import evolution_client as module

    monkeypatch.setattr(module.settings, "evolution_api_url", "http://evolution.local")
    monkeypatch.setattr(module.settings, "evolution_global_api_key", "global-secret")
    monkeypatch.setattr(module.settings, "evolution_instance_token", "a" * 64)
    monkeypatch.setattr(module.settings, "evolution_instance_name", "openstudyai")
    monkeypatch.setattr(module.settings, "evolution_webhook_secret", "webhook-secret")
    monkeypatch.setattr(module.settings, "evolution_webhook_base_url", "http://app:8000")
    client = EvolutionClient()
    calls = []

    def fake_request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return {"ok": True}

    monkeypatch.setattr(client, "_request", fake_request)
    result = client.initialize_instance()

    assert result["instance"]["ok"] is True
    create_method, create_path, create_kwargs = calls[0]
    assert create_method == "POST"
    assert create_path == "/instance/create"
    assert create_kwargs["use_global_key"] is True
    assert create_kwargs["payload"] == {
        "instanceName": "openstudyai",
        "token": "a" * 64,
        "qrcode": True,
        "integration": "WHATSAPP-BAILEYS",
        "webhook": {
            "enabled": True,
            "url": "http://app:8000/api/whatsapp/webhook/webhook-secret",
            "byEvents": False,
            "base64": False,
            "events": [
                "QRCODE_UPDATED",
                "CONNECTION_UPDATE",
                "MESSAGES_UPSERT",
                "MESSAGES_UPDATE",
                "SEND_MESSAGE",
            ],
        },
    }
    webhook_method, webhook_path, webhook_kwargs = calls[1]
    assert webhook_method == "POST"
    assert webhook_path == "/webhook/set/openstudyai"
    assert webhook_kwargs["payload"]["webhook"]["byEvents"] is False
    assert webhook_kwargs["payload"]["webhook"]["base64"] is False
    assert "MESSAGES_UPSERT" in webhook_kwargs["payload"]["webhook"]["events"]




def test_evolution_reuses_instance_when_name_is_already_in_use(monkeypatch):
    from app.services import evolution_client as module

    monkeypatch.setattr(module.settings, "evolution_api_url", "http://evolution.local")
    monkeypatch.setattr(module.settings, "evolution_global_api_key", "global-secret")
    monkeypatch.setattr(module.settings, "evolution_instance_token", "a" * 64)
    monkeypatch.setattr(module.settings, "evolution_instance_name", "openstudy-assistant")
    monkeypatch.setattr(module.settings, "evolution_webhook_secret", "webhook-secret")
    monkeypatch.setattr(module.settings, "evolution_webhook_base_url", "http://app:8000")
    client = EvolutionClient()
    calls = []

    def fake_request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        if path == "/instance/create":
            raise module.EvolutionAPIError(
                'Evolution API respondeu HTTP 403: {"message":["This name \\"openstudy-assistant\\" is already in use."]}',
                status_code=403,
            )
        return {"ok": True}

    monkeypatch.setattr(client, "_request", fake_request)
    result = client.initialize_instance()

    assert result["instance"]["already_exists"] is True
    assert result["webhook"]["ok"] is True
    assert calls[0][1] == "/instance/create"
    assert calls[1][1] == "/webhook/set/openstudy-assistant"
    assert "webhook" in calls[1][2]["payload"]

def test_evolution_instance_creation_rejects_invalid_name_and_token(monkeypatch):
    from app.services import evolution_client as module

    monkeypatch.setattr(module.settings, "evolution_api_url", "http://evolution.local")
    monkeypatch.setattr(module.settings, "evolution_global_api_key", "global-secret")
    monkeypatch.setattr(module.settings, "evolution_instance_token", "invalid token with spaces")
    monkeypatch.setattr(module.settings, "evolution_instance_name", "OpenStudy-Assistant")
    client = EvolutionClient()
    with pytest.raises(Exception, match="EVOLUTION_INSTANCE_TOKEN"):
        client.initialize_instance()



def test_evolution_instance_creation_uses_baileys_and_accepts_hyphenated_name(monkeypatch):
    from app.services import evolution_client as module

    monkeypatch.setattr(module.settings, "evolution_api_url", "http://evolution.local")
    monkeypatch.setattr(module.settings, "evolution_global_api_key", "global-secret")
    monkeypatch.setattr(module.settings, "evolution_instance_token", "instance-token-1234567890abcdef")
    monkeypatch.setattr(module.settings, "evolution_instance_name", "openstudy-assistant")
    monkeypatch.setattr(module.settings, "evolution_webhook_secret", "webhook-secret")
    monkeypatch.setattr(module.settings, "evolution_webhook_base_url", "http://app:8000")
    calls = []

    def fake_request(self, method, path, *, payload=None, use_global_key=False, timeout=35.0):
        calls.append((method, path, payload, use_global_key))
        return {"ok": True}

    monkeypatch.setattr(EvolutionClient, "_request", fake_request)
    result = EvolutionClient().initialize_instance()

    assert result["instance"]["ok"] is True
    assert calls[0][0:2] == ("POST", "/instance/create")
    assert calls[0][2]["integration"] == "WHATSAPP-BAILEYS"
    assert calls[0][2]["instanceName"] == "openstudy-assistant"
    assert calls[0][3] is True
    assert calls[1][1].startswith("/webhook/set/")
    assert calls[1][2]["webhook"]["url"] == "http://app:8000/api/whatsapp/webhook/webhook-secret"


def test_whatsapp_is_explicitly_disabled_without_docker(monkeypatch):
    from app.routers import whatsapp as routes

    engine = make_db()
    app = FastAPI()
    app.include_router(routes.router)

    def override_db():
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr(routes.settings, "whatsapp_enabled", False)

    with TestClient(app) as client:
        status = client.get("/api/whatsapp/status")
        initialize = client.post("/api/whatsapp/instance/initialize")
        webhook = client.post("/api/whatsapp/webhook/test-secret", json={})

    assert status.status_code == 200
    assert status.json()["enabled"] is False
    assert status.json()["bot_connection"]["state"] == "disabled"
    assert initialize.status_code == 503
    assert "Docker Compose" in initialize.json()["detail"]
    assert webhook.status_code == 503



def test_outbox_cancels_pending_reminders_after_student_turns_them_off(monkeypatch):
    engine = make_db()
    monkeypatch.setattr(
        EvolutionClient,
        "send_text",
        lambda self, phone, text: (_ for _ in ()).throw(AssertionError("must not send")),
    )
    with Session(engine) as db:
        phone = "5512999991234"
        db.add(WhatsAppProfile(
            id=1,
            phone_e164=phone,
            verified=True,
            notifications_enabled=True,
            reminders_enabled=False,
        ))
        db.add(WhatsAppOutbox(
            dedupe_key="pending-while-paused",
            phone_e164=phone,
            message_text="Lembrete",
            status="pending",
            next_attempt_at=utc_now(),
        ))
        db.commit()

        assert scheduler._dispatch_outbox(db, utc_now()) == 0
        job = db.query(WhatsAppOutbox).one()
        assert job.status == "cancelled"
        assert "desativados" in job.last_error


def test_initialize_instance_includes_required_internal_webhook(monkeypatch):
    from app.services import evolution_client as module

    monkeypatch.setattr(module.settings, "evolution_api_url", "http://evolution-api:8080")
    monkeypatch.setattr(module.settings, "evolution_instance_name", "openstudy-assistant")
    monkeypatch.setattr(module.settings, "evolution_global_api_key", "global-secret")
    monkeypatch.setattr(module.settings, "evolution_instance_token", "instance-secret-1234567890")
    monkeypatch.setattr(module.settings, "evolution_webhook_secret", "webhook-secret")
    monkeypatch.setattr(module.settings, "evolution_webhook_base_url", "http://app:8000")

    calls = []
    def fake_request(self, method, path, **kwargs):
        calls.append((method, path, kwargs))
        return {"created": True}

    monkeypatch.setattr(EvolutionClient, "_request", fake_request)
    client = EvolutionClient()
    result = client.initialize_instance()

    create = calls[0]
    payload = create[2]["payload"]
    assert create[0:2] == ("POST", "/instance/create")
    assert payload["webhook"]["url"] == "http://app:8000/api/whatsapp/webhook/webhook-secret"
    assert payload["webhook"]["enabled"] is True
    assert payload["webhook"]["byEvents"] is False
    assert payload["webhook"]["base64"] is False
    assert "MESSAGES_UPSERT" in payload["webhook"]["events"]
    assert result["instance"] == {"created": True}
    assert len(calls) == 2



def test_desktop_workspace_can_be_resumed_and_answered_from_whatsapp(monkeypatch):
    from app.routers import api
    from app.schemas import ExerciseWorkspaceStartRequest

    engine = make_db()
    sent = []
    monkeypatch.setattr(assistant, "send_whatsapp_text", lambda db, phone, text, **kwargs: sent.append(text))
    with Session(engine) as db:
        exercise = Exercise(
            question="O que significa RAG?", kind="multiple_choice",
            options=["Retrieval-Augmented Generation", "Random Access Graph"],
            correct_answer="Retrieval-Augmented Generation", explanation="RAG recupera contexto.",
            topic="RAG", card_ids=[],
        )
        profile = WhatsAppProfile(id=1, phone_e164="5512999991234", verified=True)
        db.add_all([exercise, profile])
        db.commit()
        db.refresh(exercise)
        workspace = api.start_exercise_workspace(
            ExerciseWorkspaceStartRequest(
                exercise_ids=[exercise.id], purpose="practice", source="desktop"
            ),
            db,
        )
        assert workspace["status"] == "active"

        assert assistant._handle_command(db, profile, profile.phone_e164, "continuar") is True
        assert profile.conversation_state["mode"] == "shared_exercise"
        assert "O que significa RAG?" in sent[-1]

        assert assistant._answer_active_exercise(db, profile, profile.phone_e164, "A") is True
        db.refresh(workspace_obj := db.get(ExerciseWorkspace, workspace["id"]))
        assert workspace_obj.status == "completed"
        assert db.query(ExerciseAttempt).filter_by(exercise_id=exercise.id).count() == 1
        assert any("Sessão concluída" in item for item in sent)
        assert api.get_active_exercise_workspace(db)["status"] == "completed"


def test_shared_workspace_answer_is_idempotent_across_clients():
    from app.routers import api
    from app.schemas import ExerciseAnswerRequest, ExerciseWorkspaceStartRequest

    engine = make_db()
    with Session(engine) as db:
        exercise = Exercise(
            question="2 + 2?", kind="multiple_choice", options=["4", "5"],
            correct_answer="4", explanation="Soma básica.", topic="Matemática", card_ids=[],
        )
        db.add(exercise)
        db.commit()
        db.refresh(exercise)
        workspace = api.start_exercise_workspace(
            ExerciseWorkspaceStartRequest(exercise_ids=[exercise.id], source="desktop"), db
        )
        first = api.answer_exercise(
            exercise.id,
            ExerciseAnswerRequest(answer="4", workspace_id=workspace["id"]),
            db,
        )
        second = api.answer_exercise(
            exercise.id,
            ExerciseAnswerRequest(answer="5", workspace_id=workspace["id"]),
            db,
        )
        assert first["correct"] is True
        assert second["correct"] is True
        assert db.query(ExerciseAttempt).filter_by(exercise_id=exercise.id).count() == 1
        stored = db.get(ExerciseWorkspace, workspace["id"])
        assert stored.status == "completed"
        assert stored.results[str(exercise.id)]["submitted_answer"] == "4"
