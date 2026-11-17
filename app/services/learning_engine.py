"""Pedagogical orchestration: concepts, recommendations, progress and rewards."""
from __future__ import annotations

import re
import unicodedata
from datetime import timedelta
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import (
    CardReviewState,
    ErrorDiagnosis,
    ExerciseAttempt,
    KnowledgeConcept,
    LearningEvent,
    RewardEvent,
    StudentProgress,
    StudyCard,
    StudySession,
    TopicMastery,
    utc_now,
)
from app.services.fsrs_scheduler import retrievability


def normalize_topic(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = re.sub(r"[^a-zA-Z0-9]+", " ", text.casefold()).strip()
    return re.sub(r"\s+", " ", text)[:180] or "geral"


def get_student_progress(db: Session) -> StudentProgress:
    progress = db.get(StudentProgress, 1)
    if progress is None:
        progress = StudentProgress(id=1)
        db.add(progress)
        db.flush()
    return progress


def award_xp(db: Session, event_key: str, event_type: str, xp: int) -> dict[str, Any]:
    """Idempotent XP ledger. Repeated requests cannot award the same event twice."""
    existing = db.query(RewardEvent).filter(RewardEvent.event_key == event_key).one_or_none()
    progress = get_student_progress(db)
    if existing:
        return {"xp_awarded": 0, "xp": progress.xp, "level": progress.level, "duplicate": True}

    reward = max(0, min(int(xp), 100))
    db.add(RewardEvent(event_key=event_key[:180], event_type=event_type[:50], xp=reward))
    progress.xp += reward
    progress.level = max(1, progress.xp // 250 + 1)
    today = utc_now().date().isoformat()
    if progress.last_activity_date != today:
        if progress.last_activity_date:
            from datetime import date
            previous = date.fromisoformat(progress.last_activity_date)
            delta = (date.fromisoformat(today) - previous).days
            progress.streak_days = progress.streak_days + 1 if delta == 1 else 1
        else:
            progress.streak_days = 1
        progress.last_activity_date = today

    achievements = set(progress.achievements or [])
    if progress.xp >= 100:
        achievements.add("first_100_xp")
    if progress.xp >= 500:
        achievements.add("knowledge_explorer")
    if progress.streak_days >= 3:
        achievements.add("three_day_streak")
    if progress.total_correct >= 10:
        achievements.add("ten_correct")
    progress.achievements = sorted(achievements)
    db.flush()
    return {"xp_awarded": reward, "xp": progress.xp, "level": progress.level, "duplicate": False}


def record_activity(db: Session, event_type: str, event_key: str, xp: int) -> dict[str, Any]:
    reward = award_xp(db, event_key, event_type, xp)
    progress = get_student_progress(db)
    return {**reward, "streak_days": progress.streak_days, "achievements": progress.achievements or []}


def sync_concepts(db: Session, document_id: int | None = None) -> list[KnowledgeConcept]:
    cards = db.query(StudyCard).filter(StudyCard.archived.is_(False)).order_by(StudyCard.id.asc()).all()
    grouped: dict[str, dict[str, Any]] = {}
    for card in cards:
        tags = card.tags if isinstance(card.tags, list) and card.tags else ["Geral"]
        for raw_tag in tags:
            title = str(raw_tag).strip()[:180]
            key = normalize_topic(title)
            bucket = grouped.setdefault(key, {"title": title, "cards": [], "documents": [], "excerpt": ""})
            bucket["cards"].append(card.id)
            bucket["documents"].append(card.document_id)
            if not bucket["excerpt"] and card.source_excerpt:
                bucket["excerpt"] = card.source_excerpt[:3000]

    results = []
    for key, data in grouped.items():
        concept = db.query(KnowledgeConcept).filter(KnowledgeConcept.normalized_key == key).one_or_none()
        if concept is None:
            concept = KnowledgeConcept(normalized_key=key, title=data["title"])
            db.add(concept)
        concept.title = data["title"]
        concept.source_card_ids = sorted(set(data["cards"]))
        concept.source_document_ids = sorted(set(data["documents"]))
        if data["excerpt"] and not concept.source_excerpt:
            concept.source_excerpt = data["excerpt"]
        results.append(concept)
    active_keys = set(grouped)
    active_card_ids = {card.id for card in cards}
    active_document_ids = {card.document_id for card in cards}
    for concept in db.query(KnowledgeConcept).all():
        if concept.normalized_key not in active_keys:
            surviving_ids = sorted(set(concept.source_card_ids or []).intersection(active_card_ids))
            if surviving_ids:
                concept.source_card_ids = surviving_ids
                concept.source_document_ids = sorted(set(concept.source_document_ids or []).intersection(active_document_ids))
                results.append(concept)
                active_keys.add(concept.normalized_key)
            else:
                db.delete(concept)
    db.flush()
    return results


def refresh_concept_mastery(db: Session) -> None:
    concepts = db.query(KnowledgeConcept).all()
    mastery_by_key = {normalize_topic(item.topic): item for item in db.query(TopicMastery).all()}
    for concept in concepts:
        mastery = mastery_by_key.get(concept.normalized_key)
        if mastery:
            concept.mastery_score = mastery.mastery_score
            concept.last_studied_at = mastery.last_practiced_at


def get_recommendations(db: Session) -> list[dict[str, Any]]:
    now = utc_now()
    recommendations: list[dict[str, Any]] = []
    due_rows = (
        db.query(StudyCard, CardReviewState)
        .outerjoin(CardReviewState, CardReviewState.card_id == StudyCard.id)
        .filter(StudyCard.archived.is_(False))
        .filter((CardReviewState.id.is_(None)) | (CardReviewState.due_at <= now))
        .order_by(CardReviewState.due_at.asc())
        .limit(8)
        .all()
    )
    if due_rows:
        due_total = db.query(func.count(StudyCard.id)).outerjoin(
            CardReviewState, CardReviewState.card_id == StudyCard.id
        ).filter(StudyCard.archived.is_(False)).filter(
            (CardReviewState.id.is_(None)) | (CardReviewState.due_at <= now)
        ).scalar() or 0
        recommendations.append({
            "kind": "review", "title": "Resgate o que está quase escapando da memória",
            "description": f"Você tem {due_total} cartões novos ou vencidos nesta fila de revisão.",
            "action": "cards", "priority": 1, "count": due_total,
        })

    weak = db.query(TopicMastery).filter(TopicMastery.attempts >= 2).order_by(TopicMastery.mastery_score.asc()).first()
    if weak and weak.mastery_score < 0.72:
        recommendations.append({
            "kind": "reinforce", "title": f"Reforce: {weak.topic}",
            "description": "Seu histórico mostra dificuldade neste assunto. Faça uma aula curta e um desafio de aplicação.",
            "action": "lesson", "topic": weak.topic, "priority": 2,
        })

    concepts = db.query(KnowledgeConcept).all()
    mastery_lookup = {normalize_topic(item.title): item.mastery_score for item in concepts}
    prerequisite_candidate = None
    for concept in sorted(concepts, key=lambda item: item.mastery_score):
        if concept.mastery_score >= 0.72:
            continue
        weak_prerequisites = [
            name for name in (concept.prerequisites or [])
            if mastery_lookup.get(normalize_topic(name), 0.0) < 0.65
        ]
        if weak_prerequisites:
            prerequisite_candidate = weak_prerequisites[0]
            break
    if prerequisite_candidate and not any(
        item.get("topic") == prerequisite_candidate for item in recommendations
    ):
        recommendations.append({
            "kind": "prerequisite", "title": f"Fortaleça a base: {prerequisite_candidate}",
            "description": "Este conceito é pré-requisito de outro assunto que ainda está em desenvolvimento.",
            "action": "lesson", "topic": prerequisite_candidate, "priority": 0,
        })

    unresolved = db.query(func.count(ErrorDiagnosis.id)).filter(ErrorDiagnosis.resolved.is_(False)).scalar() or 0
    if unresolved:
        recommendations.append({
            "kind": "errors", "title": "Transforme erros em progresso",
            "description": f"Há {unresolved} dificuldades que ainda precisam de reforço.",
            "action": "errors", "count": unresolved, "priority": 3,
        })

    new_count = db.query(func.count(StudyCard.id)).filter(
        StudyCard.archived.is_(False), StudyCard.review_count == 0
    ).scalar() or 0
    if new_count and not due_rows:
        recommendations.append({
            "kind": "new", "title": "Conheça novos conceitos",
            "description": f"Existem {new_count} cartões ainda não estudados.",
            "action": "cards", "count": new_count, "priority": 4,
        })
    return sorted(recommendations, key=lambda item: item["priority"])


def build_daily_quests(db: Session) -> dict[str, Any]:
    """Small, measurable missions that reward learning behaviors rather than time-on-page."""
    today = utc_now().date()
    start = utc_now().replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    events = (
        db.query(LearningEvent)
        .filter(LearningEvent.created_at >= start, LearningEvent.created_at < end)
        .all()
    )
    active_cards = db.query(func.count(StudyCard.id)).filter(StudyCard.archived.is_(False)).scalar() or 0
    if active_cards == 0:
        return {
            "date": today.isoformat(), "available": False, "claimable": False,
            "claimed": False, "bonus_xp": 25, "quests": [],
        }

    reviewed_ids = {
        event.card_id for event in events
        if event.event_type == "card_review" and event.card_id is not None
    }
    practiced_ids = {
        event.exercise_id for event in events
        if event.event_type == "exercise_attempt" and event.exercise_id is not None
    }
    feynman_count = sum(event.event_type == "feynman_attempt" for event in events)

    review_target = min(5, active_cards)
    practice_target = 3 if active_cards >= 3 else 1
    quests = [
        {
            "id": "recall",
            "icon": "🧠",
            "title": "Desperte sua memória",
            "description": "Recupere respostas sem consultar o material.",
            "progress": min(len(reviewed_ids), review_target),
            "target": review_target,
            "unit": "cards",
        },
        {
            "id": "practice",
            "icon": "⚡",
            "title": "Entre em ação",
            "description": "Resolva questões e aplique o que estudou.",
            "progress": min(len(practiced_ids), practice_target),
            "target": practice_target,
            "unit": "questões",
        },
        {
            "id": "teach",
            "icon": "💡",
            "title": "Ensine para aprender",
            "description": "Explique um conceito com suas próprias palavras.",
            "progress": min(feynman_count, 1),
            "target": 1,
            "unit": "explicação",
        },
    ]
    claimed = db.query(RewardEvent).filter(
        RewardEvent.event_key == f"daily-quests:{today.isoformat()}"
    ).one_or_none() is not None
    complete = all(item["progress"] >= item["target"] for item in quests)
    return {
        "date": today.isoformat(), "available": True,
        "claimable": complete and not claimed, "claimed": claimed,
        "bonus_xp": 25, "quests": quests,
    }


def claim_daily_quests(db: Session) -> dict[str, Any]:
    quest_state = build_daily_quests(db)
    if not quest_state["available"]:
        raise ValueError("Adicione um material de estudo para desbloquear as missões.")
    if not all(item["progress"] >= item["target"] for item in quest_state["quests"]):
        raise ValueError("Conclua todas as missões diárias antes de resgatar a recompensa.")
    if quest_state["claimed"]:
        return {
            **record_activity(db, "daily_quests_completed", f"daily-quests:{quest_state['date']}", 0),
            "already_claimed": True,
        }
    return {
        **record_activity(db, "daily_quests_completed", f"daily-quests:{quest_state['date']}", quest_state["bonus_xp"]),
        "already_claimed": False,
    }


def build_learning_dashboard(db: Session) -> dict[str, Any]:
    progress = get_student_progress(db)
    refresh_concept_mastery(db)
    db.flush()
    concepts = db.query(KnowledgeConcept).order_by(KnowledgeConcept.mastery_score.asc(), KnowledgeConcept.title.asc()).all()
    recent_errors = (
        db.query(ErrorDiagnosis).filter(ErrorDiagnosis.resolved.is_(False))
        .order_by(ErrorDiagnosis.created_at.desc()).limit(5).all()
    )
    today = utc_now().date().isoformat()
    due_count = db.query(func.count(StudyCard.id)).outerjoin(
        CardReviewState, CardReviewState.card_id == StudyCard.id
    ).filter(StudyCard.archived.is_(False)).filter(
        (CardReviewState.id.is_(None)) | (CardReviewState.due_at <= utc_now())
    ).scalar() or 0
    next_level_xp = progress.level * 250
    current_level_start = (progress.level - 1) * 250
    return {
        "progress": {
            "xp": progress.xp, "level": progress.level,
            "level_progress": max(0, min(100, round((progress.xp - current_level_start) / 250 * 100))),
            "xp_to_next_level": max(0, next_level_xp - progress.xp),
            "streak_days": progress.streak_days,
            "total_sessions": progress.total_sessions,
            "total_attempts": progress.total_attempts,
            "total_correct": progress.total_correct,
            "achievements": progress.achievements or [],
            "active_today": progress.last_activity_date == today,
        },
        "due_count": due_count,
        "concepts": [
            {"id": item.id, "title": item.title, "mastery": item.mastery_score,
             "prerequisites": item.prerequisites or [], "card_count": len(item.source_card_ids or [])}
            for item in concepts[:30]
        ],
        "weak_topics": [
            {"topic": item.topic, "mastery": item.mastery_score, "attempts": item.attempts,
             "confidence": item.confidence_score, "retention": item.retention_score}
            for item in db.query(TopicMastery).filter(TopicMastery.attempts >= 2)
            .order_by(TopicMastery.mastery_score.asc()).limit(6).all()
        ],
        "recent_errors": [
            {"id": item.id, "topic": item.topic, "error_type": item.error_type,
             "severity": item.severity, "explanation": item.explanation,
             "intervention": item.intervention, "created_at": item.created_at}
            for item in recent_errors
        ],
        "recommendations": get_recommendations(db),
        "daily_quests": build_daily_quests(db),
    }


def update_topic_after_attempt(
    db: Session, topics: set[str], score: float, confidence: float, is_transfer: bool = False,
    is_correct: bool | None = None,
) -> None:
    now = utc_now()
    for raw_topic in topics or {"Geral"}:
        topic = str(raw_topic).strip()[:160] or "Geral"
        mastery = db.query(TopicMastery).filter(TopicMastery.topic == topic).one_or_none()
        if mastery is None:
            mastery = TopicMastery(
                topic=topic, attempts=0, correct=0, mastery_score=0.5,
                confidence_score=0.0, retention_score=0.0, transfer_score=0.0,
                forgetting_rate=0.0,
            )
            db.add(mastery)
            db.flush()
        mastery.attempts += 1
        if (score >= 0.7 if is_correct is None else is_correct):
            mastery.correct += 1
        mastery.mastery_score = round((mastery.correct + 1) / (mastery.attempts + 2), 4)
        mastery.confidence_score = round((mastery.confidence_score * (mastery.attempts - 1) + confidence / 5) / mastery.attempts, 4)
        mastery.transfer_score = round((mastery.transfer_score * (mastery.attempts - 1) + (score if is_transfer else 0)) / mastery.attempts, 4)
        mastery.retention_score = round((mastery.retention_score * (mastery.attempts - 1) + score) / mastery.attempts, 4)
        mastery.last_practiced_at = now
        mastery.updated_at = now
