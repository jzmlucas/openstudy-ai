from sqlalchemy.orm import Session

from app.models import LearningEvent, StudyCard
from app.services.learning_engine import get_student_progress, update_topic_after_attempt


def record_exercise_result(
    db: Session,
    exercise_id: int,
    card_ids: list[int],
    is_correct: bool,
    user_answer: str,
    *,
    score: float | None = None,
    confidence: int = 3,
    error_type: str | None = None,
    response_time_seconds: float = 0,
    session_id: int | None = None,
    is_transfer: bool = False,
    extra_topics: set[str] | None = None,
) -> None:
    normalized_score = float(score if score is not None else (1.0 if is_correct else 0.0))
    db.add(
        LearningEvent(
            event_type="exercise_attempt",
            exercise_id=exercise_id,
            correct=is_correct,
            confidence=float(confidence),
            session_id=session_id,
            event_data={
                "answer_length": len(user_answer),
                "score": normalized_score,
                "error_type": error_type,
                "response_time_seconds": max(0.0, float(response_time_seconds)),
            },
        )
    )
    cards = db.query(StudyCard).filter(StudyCard.id.in_(card_ids or [])).all()
    topics = {
        str(tag).strip()[:160]
        for card in cards
        for tag in (card.tags if isinstance(card.tags, list) else [])
        if str(tag).strip()
    }
    topics.update({str(value).strip()[:160] for value in (extra_topics or set()) if str(value).strip()})
    if not topics:
        topics = {"Geral"}

    update_topic_after_attempt(
        db, topics, normalized_score, float(confidence), is_transfer=is_transfer, is_correct=is_correct
    )
    for topic in topics:
        db.add(
            LearningEvent(
                event_type="exercise_topic_result",
                exercise_id=exercise_id,
                topic=topic,
                correct=is_correct,
                confidence=float(confidence),
                session_id=session_id,
                event_data={"score": normalized_score, "error_type": error_type},
            )
        )

    progress = get_student_progress(db)
    progress.total_attempts += 1
    if is_correct:
        progress.total_correct += 1
