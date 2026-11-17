from collections import Counter
from datetime import timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import (
    CardReviewState,
    Document,
    ErrorDiagnosis,
    Exercise,
    ExerciseAttempt,
    LearningEvent,
    LLMCallLog,
    StudyCard,
    TopicMastery,
    utc_now,
)


def _retention_for_window(events: list[LearningEvent], minimum_days: float, maximum_days: float) -> dict:
    sample = [
        event for event in events
        if event.event_type == "card_review"
        and minimum_days <= float((event.event_data or {}).get("elapsed_days", -1)) <= maximum_days
        and event.correct is not None
    ]
    return {
        "accuracy": round(sum(bool(item.correct) for item in sample) / len(sample), 4) if sample else None,
        "reviews": len(sample),
    }


def build_analytics(db: Session) -> dict:
    now = utc_now()
    total_cards = db.query(func.count(StudyCard.id)).filter(StudyCard.archived.is_(False)).scalar() or 0
    total_documents = db.query(func.count(Document.id)).scalar() or 0
    total_exercises = db.query(func.count(Exercise.id)).scalar() or 0
    total_attempts = db.query(func.count(ExerciseAttempt.id)).scalar() or 0
    correct_attempts = db.query(func.count(ExerciseAttempt.id)).filter(ExerciseAttempt.is_correct.is_(True)).scalar() or 0
    due_cards = (
        db.query(func.count(CardReviewState.id)).join(StudyCard, StudyCard.id == CardReviewState.card_id)
        .filter(StudyCard.archived.is_(False), CardReviewState.due_at <= now).scalar() or 0
    )
    new_cards_without_state = (
        db.query(func.count(StudyCard.id)).outerjoin(CardReviewState, CardReviewState.card_id == StudyCard.id)
        .filter(StudyCard.archived.is_(False), CardReviewState.id.is_(None)).scalar() or 0
    )

    topics = db.query(TopicMastery).order_by(TopicMastery.mastery_score.asc()).all()
    weak_topics = [
        {"topic": item.topic, "mastery": item.mastery_score, "attempts": item.attempts,
         "confidence": item.confidence_score, "retention": item.retention_score}
        for item in topics if item.attempts >= 2 and item.mastery_score < 0.7
    ][:8]
    strong_topics = [
        {"topic": item.topic, "mastery": item.mastery_score, "attempts": item.attempts,
         "confidence": item.confidence_score, "retention": item.retention_score}
        for item in sorted(topics, key=lambda item: item.mastery_score, reverse=True)
        if item.attempts >= 2 and item.mastery_score >= 0.75
    ][:8]

    since = now - timedelta(days=6)
    events = db.query(LearningEvent).filter(LearningEvent.created_at >= since).all()
    activity_counter = Counter(event.created_at.strftime("%Y-%m-%d") for event in events)
    activity = []
    for offset in range(6, -1, -1):
        day = (now - timedelta(days=offset)).strftime("%Y-%m-%d")
        activity.append({"date": day, "events": activity_counter.get(day, 0)})

    all_events = db.query(LearningEvent).all()
    retention_24h = _retention_for_window(all_events, 0.5, 1.5)
    retention_7d = _retention_for_window(all_events, 5, 9)
    retention_30d = _retention_for_window(all_events, 25, 35)
    error_count = db.query(func.count(ErrorDiagnosis.id)).scalar() or 0
    unresolved_errors = db.query(func.count(ErrorDiagnosis.id)).filter(ErrorDiagnosis.resolved.is_(False)).scalar() or 0
    error_rows = db.query(ErrorDiagnosis).all()
    error_type_counts = Counter(item.error_type for item in error_rows)
    unresolved_by_type = Counter(item.error_type for item in error_rows if not item.resolved)
    attempts = db.query(ExerciseAttempt).all()
    confidence = [float(item.confidence) / 5 for item in attempts if item.confidence is not None]
    accuracy = [float(item.score) for item in attempts if item.score is not None]
    confidence_mean = sum(confidence) / len(confidence) if confidence else None
    score_mean = sum(accuracy) / len(accuracy) if accuracy else None
    transfer_exercises = db.query(Exercise).filter(
        (Exercise.purpose.in_(["transfer", "exam"])) | (Exercise.bloom_level.in_(["apply", "analyze", "evaluate", "create"]))
    ).all()
    transfer_ids = [item.id for item in transfer_exercises]
    transfer_attempts = [item for item in attempts if item.exercise_id in transfer_ids]
    transfer_accuracy = sum(float(item.score or 0) for item in transfer_attempts) / len(transfer_attempts) if transfer_attempts else None

    llm_calls = db.query(LLMCallLog).all()
    successful_calls = [call for call in llm_calls if call.success]
    avg_latency = round(sum(call.elapsed_ms for call in successful_calls) / len(successful_calls)) if successful_calls else 0
    llm_errors = len(llm_calls) - len(successful_calls)
    prompt_tokens = sum(call.prompt_tokens for call in llm_calls)
    generated_tokens = sum(call.generated_tokens for call in llm_calls)

    return {
        "documents": total_documents,
        "cards": total_cards,
        "exercises": total_exercises,
        "exercise_attempts": total_attempts,
        "exercise_accuracy": round(correct_attempts / total_attempts, 4) if total_attempts else 0,
        "average_score": round(score_mean, 4) if score_mean is not None else None,
        "average_confidence": round(confidence_mean, 4) if confidence_mean is not None else None,
        "confidence_gap": round(confidence_mean - score_mean, 4) if confidence_mean is not None and score_mean is not None else None,
        "transfer_accuracy": round(transfer_accuracy, 4) if transfer_accuracy is not None else None,
        "retention": {"24h": retention_24h, "7d": retention_7d, "30d": retention_30d},
        "error_count": error_count,
        "unresolved_errors": unresolved_errors,
        "error_types": [{"type": key, "count": value, "unresolved": unresolved_by_type.get(key, 0)} for key, value in error_type_counts.most_common()],
        "due_cards": due_cards + new_cards_without_state,
        "scheduled_cards": db.query(func.count(CardReviewState.id)).scalar() or 0,
        "topics": [
            {"topic": item.topic, "mastery": item.mastery_score, "attempts": item.attempts,
             "confidence": item.confidence_score, "retention": item.retention_score,
             "transfer": item.transfer_score, "forgetting_rate": item.forgetting_rate}
            for item in topics
        ],
        "weak_topics": weak_topics,
        "strong_topics": strong_topics,
        "activity_7_days": activity,
        "llm": {
            "calls": len(llm_calls), "successful_calls": len(successful_calls), "errors": llm_errors,
            "average_latency_ms": avg_latency, "prompt_tokens": prompt_tokens,
            "generated_tokens": generated_tokens,
        },
    }
