from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import (
    CardReviewState,
    Document,
    Exercise,
    ExerciseAttempt,
    GenerationEvaluation,
    LearningEvent,
    StudyCard,
    StudyPlan,
    StudyPlanTask,
    TopicMastery,
)
from app.services.document_cleanup import cleanup_document_learning_data


def test_cleanup_removes_dependent_learning_data_and_recalculates_topics():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        document = Document(
            original_filename="old.md",
            stored_path="/tmp/old.md",
            file_type=".md",
            status="ready",
        )
        db.add(document)
        db.flush()

        card = StudyCard(
            document_id=document.id,
            front="O que é RAG?",
            back="Retrieval augmented generation",
            tags=["GenAI"],
        )
        db.add(card)
        db.flush()

        review_state = CardReviewState(card_id=card.id)
        exercise = Exercise(
            question="Pergunta",
            kind="true_false",
            options=["Verdadeiro", "Falso"],
            correct_answer="Verdadeiro",
            card_ids=[card.id],
        )
        plan = StudyPlan(title="Plano", objective="Estudar", days=3)
        db.add_all([review_state, exercise, plan])
        db.flush()
        attempt = ExerciseAttempt(
            exercise_id=exercise.id,
            user_answer="Verdadeiro",
            is_correct=True,
        )
        task = StudyPlanTask(
            plan_id=plan.id,
            day_number=1,
            title="Revisar RAG",
            card_ids=[card.id],
        )
        evaluation = GenerationEvaluation(
            target_type="flashcard",
            target_id=card.id,
            scores={"accuracy": 5},
            overall_score=5,
        )
        db.add_all([
            attempt,
            task,
            evaluation,
            LearningEvent(event_type="card_review", card_id=card.id, topic="GenAI", correct=True),
            LearningEvent(event_type="exercise_topic_result", exercise_id=exercise.id, topic="GenAI", correct=True),
        ])
        db.add(TopicMastery(topic="GenAI", attempts=2, correct=2, mastery_score=0.75))
        db.flush()
        card_id, exercise_id, document_id, task_id = card.id, exercise.id, document.id, task.id
        db.commit()

        cleanup_document_learning_data(db, document_id)
        db.commit()

        assert db.query(StudyCard).filter_by(id=card_id).count() == 0
        assert db.query(CardReviewState).filter_by(card_id=card_id).count() == 0
        assert db.query(Exercise).filter_by(id=exercise_id).count() == 0
        assert db.query(ExerciseAttempt).filter_by(exercise_id=exercise_id).count() == 0
        assert db.query(GenerationEvaluation).filter_by(target_id=card_id).count() == 0
        assert db.query(TopicMastery).filter_by(topic="GenAI").count() == 0
        refreshed_task = db.get(StudyPlanTask, task_id)
        assert refreshed_task.card_ids == []
