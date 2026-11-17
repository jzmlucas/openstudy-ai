import math
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.config import settings
from app.models import CardReviewState, LearningEvent, StudyCard, TopicMastery, utc_now

try:
    from fsrs import Card as FSRSCard, Rating as FSRSRating, Scheduler as FSRSScheduler, State as FSRSState
except ImportError:
    FSRSCard = FSRSRating = FSRSScheduler = FSRSState = None


RATING_INITIAL_STABILITY = {
    "again": 0.25,
    "hard": 1.0,
    "good": 2.4,
    "easy": 4.5,
}


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def retrievability(stability: float, elapsed_days: float) -> float:
    """Curva de esquecimento exponencial usada como aproximação FSRS."""
    if stability <= 0:
        return 0.0
    return math.exp(math.log(0.9) * max(0.0, elapsed_days) / stability)


def calculate_schedule(
    *,
    stability: float,
    difficulty: float,
    reps: int,
    lapses: int,
    last_review: datetime | None,
    rating: str,
    now: datetime | None = None,
) -> dict:
    """
    Agendador adaptativo inspirado no princípio de retrievability/stability do FSRS.

    Esta implementação é uma aproximação didática própria, não o pacote FSRS oficial.
    Ela mantém os conceitos de estabilidade, dificuldade, retenção desejada e lapsos.
    """
    if rating not in RATING_INITIAL_STABILITY:
        raise ValueError("Rating deve ser again, hard, good ou easy.")

    now = now or utc_now()
    if last_review is None or reps == 0:
        new_stability = RATING_INITIAL_STABILITY[rating]
        new_difficulty = 5.0 + {"again": 1.3, "hard": 0.5, "good": 0.0, "easy": -0.8}[rating]
        elapsed = 0.0
        current_retrievability = 0.0
    else:
        if last_review.tzinfo is not None:
            last_review = last_review.replace(tzinfo=None)
        elapsed = max(0.0, (now - last_review).total_seconds() / 86400)
        current_retrievability = retrievability(stability, elapsed)
        difficulty_delta = {"again": 1.0, "hard": 0.35, "good": -0.15, "easy": -0.55}[rating]
        new_difficulty = _clamp(difficulty + difficulty_delta, 1.0, 10.0)

        if rating == "again":
            new_stability = max(0.2, stability * (0.45 + 0.15 * current_retrievability))
            lapses += 1
        else:
            growth = {
                "hard": 1.18,
                "good": 1.65 + (10.0 - new_difficulty) * 0.075,
                "easy": 2.15 + (10.0 - new_difficulty) * 0.12,
            }[rating]
            new_stability = stability * growth * (0.85 + 0.15 * current_retrievability)

    target = _clamp(settings.target_retention, 0.75, 0.97)
    interval_days = new_stability * (math.log(target) / math.log(0.9))
    if rating == "again":
        interval = timedelta(minutes=10)
        state = "relearning"
    else:
        interval_days = _clamp(interval_days, 1.0, 3650.0)
        interval = timedelta(days=interval_days)
        state = "review" if reps >= 1 else "learning"

    return {
        "stability": round(_clamp(new_stability, 0.1, 3650.0), 4),
        "difficulty": round(new_difficulty, 3),
        "reps": reps + 1,
        "lapses": lapses,
        "state": state,
        "due_at": now + interval,
        "last_review": now,
        "retrievability_before_review": round(current_retrievability, 4),
        "scheduled_days": round(interval.total_seconds() / 86400, 4),
    }


def _record_topic(db: Session, topic: str, correct: bool, card_id: int, rating: str) -> None:
    normalized = (topic or "Geral").strip()[:160] or "Geral"
    mastery = db.query(TopicMastery).filter(TopicMastery.topic == normalized).one_or_none()
    if mastery is None:
        mastery = TopicMastery(
            topic=normalized, attempts=0, correct=0, mastery_score=0.5,
            confidence_score=0.0, retention_score=0.0, transfer_score=0.0,
            forgetting_rate=0.0,
        )
        db.add(mastery)
    mastery.attempts += 1
    if correct:
        mastery.correct += 1
    mastery.mastery_score = round((mastery.correct + 1) / (mastery.attempts + 2), 4)
    mastery.last_practiced_at = utc_now()
    confidence_value = {"again": 0.1, "hard": 0.4, "good": 0.75, "easy": 0.95}[rating]
    mastery.confidence_score = round((mastery.confidence_score * (mastery.attempts - 1) + confidence_value) / mastery.attempts, 4)
    mastery.retention_score = round((mastery.retention_score * (mastery.attempts - 1) + float(correct)) / mastery.attempts, 4)
    db.add(
        LearningEvent(
            event_type="card_review",
            card_id=card_id,
            topic=normalized,
            correct=correct,
            event_data={"rating": rating},
        )
    )


def _review_card_with_official_fsrs(
    state: CardReviewState, rating: str, now: datetime
) -> dict | None:
    if FSRSScheduler is None:
        return None

    now = now.replace(tzinfo=timezone.utc) if now.tzinfo is None else now.astimezone(timezone.utc)
    rating_map = {
        "again": FSRSRating.Again,
        "hard": FSRSRating.Hard,
        "good": FSRSRating.Good,
        "easy": FSRSRating.Easy,
    }
    state_map = {
        "learning": FSRSState.Learning,
        "review": FSRSState.Review,
        "relearning": FSRSState.Relearning,
    }
    current_state = state_map.get((state.state or "").lower(), FSRSState.Learning)
    last_review = state.last_review
    if last_review is not None:
        last_review = (
            last_review.replace(tzinfo=timezone.utc)
            if last_review.tzinfo is None
            else last_review.astimezone(timezone.utc)
        )
    due = state.due_at
    due = due.replace(tzinfo=timezone.utc) if due.tzinfo is None else due.astimezone(timezone.utc)
    fsrs_card = FSRSCard(
        card_id=state.card_id,
        state=current_state,
        step=state.step if state.step is not None else (0 if current_state != FSRSState.Review else None),
        stability=state.stability if state.reps > 0 else None,
        difficulty=state.difficulty if state.reps > 0 else None,
        due=due,
        last_review=last_review,
    )
    scheduler = FSRSScheduler(
        desired_retention=_clamp(settings.target_retention, 0.75, 0.97),
        enable_fuzzing=False,
    )
    before = scheduler.get_card_retrievability(fsrs_card, current_datetime=now)
    previous_review = last_review
    updated, _review_log = scheduler.review_card(
        fsrs_card, rating_map[rating], review_datetime=now
    )
    elapsed_days = 0.0
    if previous_review is not None:
        elapsed_days = max(0.0, (now - previous_review).total_seconds() / 86400)
    scheduled_days = max(0.0, (updated.due - now).total_seconds() / 86400)
    old_state = (state.state or "").lower()
    lapses = state.lapses + int(rating == "again" and old_state == "review")
    return {
        "stability": round(float(updated.stability or 0.1), 4),
        "difficulty": round(float(updated.difficulty or 5.0), 3),
        "reps": state.reps + 1,
        "lapses": lapses,
        "step": updated.step,
        "state": updated.state.name.lower(),
        "due_at": updated.due.astimezone(timezone.utc).replace(tzinfo=None),
        "last_review": updated.last_review.astimezone(timezone.utc).replace(tzinfo=None),
        "retrievability_before_review": round(float(before), 4),
        "scheduled_days": round(scheduled_days, 4),
        "elapsed_days": round(elapsed_days, 4),
        "scheduler": "FSRS-6",
    }


def review_card(db: Session, card: StudyCard, rating: str) -> CardReviewState:
    now = utc_now()
    state = (
        db.query(CardReviewState)
        .filter(CardReviewState.card_id == card.id)
        .one_or_none()
    )
    if state is None:
        state = CardReviewState(card_id=card.id)
        db.add(state)
        db.flush()

    schedule = _review_card_with_official_fsrs(state, rating, now)
    if schedule is None:
        previous_review = state.last_review
        schedule = calculate_schedule(
            stability=state.stability,
            difficulty=state.difficulty,
            reps=state.reps,
            lapses=state.lapses,
            last_review=state.last_review,
            rating=rating,
            now=now,
        )
        schedule["step"] = 0 if schedule["state"] in {"learning", "relearning"} else None
        schedule["scheduler"] = "FSRS-inspired fallback"
        schedule["elapsed_days"] = max(0.0, (now - previous_review).total_seconds() / 86400) if previous_review else 0.0
    for key in ("stability", "difficulty", "reps", "lapses", "step", "state", "due_at", "last_review"):
        setattr(state, key, schedule[key])

    card.review_count += 1
    tags = card.tags if isinstance(card.tags, list) else []
    if not tags:
        tags = ["Geral"]
    elapsed_days = float(schedule.get("elapsed_days", 0.0))
    recalled = rating in {"hard", "good", "easy"}
    for tag in set(str(item) for item in tags):
        _record_topic(db, tag, recalled, card.id, rating)
        topic_card_ids = [
            candidate.id for candidate in db.query(StudyCard).all()
            if tag in (candidate.tags or []) and not candidate.archived
        ]
        topic_states = db.query(CardReviewState).filter(CardReviewState.card_id.in_(topic_card_ids)).all() if topic_card_ids else []
        total_reps = sum(item.reps for item in topic_states)
        total_lapses = sum(item.lapses for item in topic_states)
        mastery = db.query(TopicMastery).filter(TopicMastery.topic == tag).one_or_none()
        if mastery is not None:
            mastery.forgetting_rate = round(total_lapses / max(1, total_reps), 4)
        event = db.query(LearningEvent).filter(
            LearningEvent.card_id == card.id,
            LearningEvent.topic == tag,
            LearningEvent.event_type == "card_review",
        ).order_by(LearningEvent.id.desc()).first()
        if event is not None:
            event.event_data = {
                **(event.event_data or {}),
                "elapsed_days": round(elapsed_days, 3),
                "retrievability_before_review": schedule["retrievability_before_review"],
                "scheduled_days": schedule.get("scheduled_days", 0),
                "scheduler": schedule.get("scheduler", "unknown"),
            }
    db.flush()
    return state
