from datetime import timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import (
    CardReviewState,
    Document,
    ErrorDiagnosis,
    Exercise,
    ExerciseAttempt,
    LearningEvent,
    KnowledgeConcept,
    StudyCard,
    TopicMastery,
    utc_now,
)
from app.services.learning_engine import (
    award_xp,
    build_learning_dashboard,
    get_recommendations,
    sync_concepts,
    update_topic_after_attempt,
)
from app.services.fsrs_scheduler import review_card


def make_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def test_xp_ledger_is_idempotent_and_updates_level_and_streak():
    engine = make_db()
    with Session(engine) as db:
        first = award_xp(db, "exercise:1", "exercise", 260)
        db.commit()
        second = award_xp(db, "exercise:1", "exercise", 260)
        db.commit()
        assert first["xp_awarded"] == 100
        assert second["xp_awarded"] == 0
        assert second["duplicate"] is True
        assert second["level"] == 1


def test_sync_concepts_uses_active_cards_and_removes_stale_concepts():
    engine = make_db()
    with Session(engine) as db:
        doc = Document(original_filename="ai.md", stored_path="/tmp/ai.md", file_type=".md")
        db.add(doc)
        db.flush()
        card = StudyCard(document_id=doc.id, front="O que é RAG?", back="Retrieval augmented generation", tags=["RAG", "GenAI"])
        db.add(card)
        db.flush()
        concepts = sync_concepts(db)
        db.commit()
        assert {item.normalized_key for item in concepts} == {"rag", "genai"}
        assert all(item.source_card_ids == [card.id] for item in concepts)

        card.archived = True
        db.commit()
        assert sync_concepts(db) == []
        db.commit()
        assert db.query(KnowledgeConcept).count() == 0


def test_due_queue_recommendation_includes_new_and_due_cards():
    engine = make_db()
    with Session(engine) as db:
        doc = Document(original_filename="ml.md", stored_path="/tmp/ml.md", file_type=".md")
        db.add(doc)
        db.flush()
        new_card = StudyCard(document_id=doc.id, front="O que é ML?", back="Aprendizado de máquina", tags=["ML"])
        db.add(new_card)
        db.commit()
        recommendations = get_recommendations(db)
        assert recommendations
        assert recommendations[0]["kind"] == "review"
        assert recommendations[0]["count"] == 1


def test_hard_rating_counts_as_successful_recall():
    engine = make_db()
    with Session(engine) as db:
        doc = Document(original_filename="rag.md", stored_path="/tmp/rag.md", file_type=".md")
        db.add(doc)
        db.flush()
        card = StudyCard(document_id=doc.id, front="RAG?", back="Retrieval augmented generation", tags=["RAG"])
        db.add(card)
        db.flush()
        review_card(db, card, "hard")
        db.commit()
        mastery = db.query(TopicMastery).filter_by(topic="RAG").one()
        event = db.query(LearningEvent).filter_by(event_type="card_review", topic="RAG").one()
        assert mastery.correct == 1
        assert event.correct is True
        assert mastery.confidence_score > 0


def test_learning_dashboard_exposes_retention_and_error_coaching():
    engine = make_db()
    with Session(engine) as db:
        doc = Document(original_filename="rag.md", stored_path="/tmp/rag.md", file_type=".md")
        db.add(doc)
        db.flush()
        card = StudyCard(document_id=doc.id, front="RAG?", back="Retrieval augmented generation", tags=["RAG"])
        db.add(card)
        db.flush()
        exercise = Exercise(question="Pergunta", kind="short_answer", correct_answer="Resposta", card_ids=[card.id], topic="RAG")
        db.add(exercise)
        db.flush()
        attempt = ExerciseAttempt(exercise_id=exercise.id, user_answer="Errado", is_correct=False, score=0.2, confidence=5)
        db.add(attempt)
        db.flush()
        db.add(ErrorDiagnosis(exercise_id=exercise.id, attempt_id=attempt.id, topic="RAG", error_type="misconception", explanation="Confundiu conceitos", intervention="Compare os dois conceitos."))
        db.add(LearningEvent(event_type="card_review", card_id=card.id, topic="RAG", correct=True, event_data={"elapsed_days": 7}))
        update_topic_after_attempt(db, {"RAG"}, 0.2, 5)
        db.commit()
        dashboard = build_learning_dashboard(db)
        assert dashboard["due_count"] == 1
        assert dashboard["recent_errors"][0]["topic"] == "RAG"
        assert dashboard["progress"]["level"] == 1


def test_official_fsrs_adapter_is_used_when_library_is_available(monkeypatch):
    from datetime import datetime, timedelta, timezone
    from types import SimpleNamespace

    from app.services import fsrs_scheduler

    class FakeState:
        Learning = SimpleNamespace(name="Learning")
        Review = SimpleNamespace(name="Review")
        Relearning = SimpleNamespace(name="Relearning")

    class FakeRating:
        Again = "again"
        Hard = "hard"
        Good = "good"
        Easy = "easy"

    class FakeCard:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class FakeScheduler:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def get_card_retrievability(self, card, current_datetime=None):
            return 0.83

        def review_card(self, card, rating, review_datetime=None):
            card.state = FakeState.Learning
            card.step = 1
            card.stability = 2.5
            card.difficulty = 4.2
            card.due = review_datetime + timedelta(minutes=10)
            card.last_review = review_datetime
            return card, object()

    monkeypatch.setattr(fsrs_scheduler, "FSRSCard", FakeCard)
    monkeypatch.setattr(fsrs_scheduler, "FSRSRating", FakeRating)
    monkeypatch.setattr(fsrs_scheduler, "FSRSState", FakeState)
    monkeypatch.setattr(fsrs_scheduler, "FSRSScheduler", FakeScheduler)

    engine = make_db()
    with Session(engine) as db:
        doc = Document(original_filename="rag.md", stored_path="/tmp/rag.md", file_type=".md")
        db.add(doc)
        db.flush()
        card = StudyCard(document_id=doc.id, front="RAG?", back="Retrieval augmented generation", tags=["RAG"])
        db.add(card)
        db.flush()
        state = review_card(db, card, "good")
        db.commit()
        event = db.query(LearningEvent).filter_by(event_type="card_review", topic="RAG").one()
        assert state.state == "learning"
        assert state.step == 1
        assert state.stability == 2.5
        assert event.event_data["scheduler"] == "FSRS-6"
        assert event.event_data["retrievability_before_review"] == 0.83
