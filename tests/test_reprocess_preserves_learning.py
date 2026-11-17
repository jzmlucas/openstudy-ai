from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.database import Base
from app.models import CardReviewState, Document, LearningEvent, StudyCard
from app.services import study_service


class FakeClient:
    model = "fake"

    def __init__(self, db=None):
        self.db = db

    def embed(self, texts):
        return [[0.1, 0.2, 0.3] for _ in texts]

    def generate_cards(self, chunk, filename, language="pt-BR"):
        return [{
            "front": "O que é RAG?",
            "back": "Geração aumentada por recuperação.",
            "explanation": "Combina recuperação de contexto e geração.",
            "difficulty": "intermediario",
            "tags": ["RAG", "GenAI"],
            "source_excerpt": "RAG recupera trechos relevantes.",
        }]


def test_reprocessing_reconciles_cards_without_erasing_review_history(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    source = tmp_path / "source.md"
    source.write_text("RAG recupera contexto para responder perguntas.", encoding="utf-8")

    with Session(engine) as db:
        document = Document(
            original_filename="source.md", stored_path=str(source), file_type=".md", status="ready"
        )
        db.add(document)
        db.flush()
        card = StudyCard(
            document_id=document.id, front="O que é RAG?", back="RAG antigo",
            explanation="Explicação antiga", difficulty="facil", tags=["RAG"], review_count=4,
        )
        db.add(card)
        db.flush()
        review_state = CardReviewState(card_id=card.id, stability=8.0, difficulty=4.0, reps=4)
        db.add(review_state)
        db.add(LearningEvent(event_type="card_review", card_id=card.id, topic="RAG", correct=True))
        db.commit()
        document_id, card_id = document.id, card.id

    monkeypatch.setattr(study_service, "SessionLocal", factory)
    monkeypatch.setattr(study_service, "OllamaClient", FakeClient)
    monkeypatch.setattr(study_service, "extract_text_pages", lambda *_: [(3, "RAG recupera contexto para responder perguntas.")])
    monkeypatch.setattr(study_service, "chunk_text", lambda text, *_: [text])

    study_service.process_document(document_id, "pt-BR")

    with Session(engine) as db:
        document = db.get(Document, document_id)
        card = db.get(StudyCard, card_id)
        state = db.query(CardReviewState).filter_by(card_id=card_id).one()
        events = db.query(LearningEvent).filter_by(card_id=card_id).all()
        assert document.status == "ready"
        assert card.archived is False
        assert card.version_number == 2
        assert card.review_count == 4
        assert card.back == "Geração aumentada por recuperação."
        assert state.stability == 8.0
        assert len(events) == 1
        chunk = db.query(study_service.DocumentChunk).filter_by(document_id=document_id).one()
        assert chunk.page_number == 3
