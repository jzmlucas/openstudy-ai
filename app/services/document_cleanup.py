from sqlalchemy.orm import Session

from app.models import (
    CardReviewState,
    Exercise,
    ExerciseAttempt,
    GenerationEvaluation,
    LearningEvent,
    StudyCard,
    StudyPlanTask,
    TopicMastery,
    KnowledgeConcept,
)


def cleanup_card_learning_data(db: Session, card_ids: list[int]) -> None:
    """Remove dados derivados de cards excluídos e recalcula domínio por assunto."""
    card_id_set = set(card_ids)
    if not card_id_set:
        return

    affected_exercises = [
        exercise.id
        for exercise in db.query(Exercise).all()
        if card_id_set.intersection(exercise.card_ids or [])
    ]

    if affected_exercises:
        db.query(ExerciseAttempt).filter(
            ExerciseAttempt.exercise_id.in_(affected_exercises)
        ).delete(synchronize_session=False)
        db.query(LearningEvent).filter(
            LearningEvent.exercise_id.in_(affected_exercises)
        ).delete(synchronize_session=False)
        db.query(Exercise).filter(
            Exercise.id.in_(affected_exercises)
        ).delete(synchronize_session=False)

    db.query(CardReviewState).filter(
        CardReviewState.card_id.in_(card_id_set)
    ).delete(synchronize_session=False)
    db.query(LearningEvent).filter(
        LearningEvent.card_id.in_(card_id_set)
    ).delete(synchronize_session=False)
    db.query(GenerationEvaluation).filter(
        GenerationEvaluation.target_type == "flashcard",
        GenerationEvaluation.target_id.in_(card_id_set),
    ).delete(synchronize_session=False)
    db.query(StudyCard).filter(
        StudyCard.id.in_(card_id_set)
    ).delete(synchronize_session=False)

    for task in db.query(StudyPlanTask).all():
        task.card_ids = [
            card_id for card_id in (task.card_ids or [])
            if card_id not in card_id_set
        ]

    events = db.query(LearningEvent).filter(LearningEvent.topic.is_not(None)).all()
    totals: dict[str, list[int]] = {}
    for event in events:
        if event.correct is None:
            continue
        bucket = totals.setdefault(event.topic, [0, 0])
        bucket[0] += 1
        bucket[1] += int(event.correct)

    for mastery in db.query(TopicMastery).all():
        values = totals.get(mastery.topic)
        if not values:
            db.delete(mastery)
            continue
        mastery.attempts, mastery.correct = values
        mastery.mastery_score = round((mastery.correct + 1) / (mastery.attempts + 2), 4)


def cleanup_document_learning_data(db: Session, document_id: int) -> None:
    """Remove cards e dados derivados quando um material é excluído definitivamente."""
    card_ids = [
        row[0]
        for row in db.query(StudyCard.id).filter(StudyCard.document_id == document_id).all()
    ]
    cleanup_card_learning_data(db, card_ids)
    for concept in db.query(KnowledgeConcept).all():
        concept.source_card_ids = [value for value in (concept.source_card_ids or []) if value not in card_ids]
        concept.source_document_ids = [value for value in (concept.source_document_ids or []) if value != document_id]
        if not concept.source_card_ids and not concept.source_document_ids:
            db.delete(concept)
