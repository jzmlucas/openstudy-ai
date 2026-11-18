import random
import re
import uuid

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
)
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import (
    CardReviewState,
    Document,
    DocumentChunk,
    ErrorDiagnosis,
    Exercise,
    ExerciseAttempt,
    ExerciseWorkspace,
    GameChallenge,
    GenerationEvaluation,
    KnowledgeConcept,
    LearningEvent,
    LLMCallLog,
    RewardEvent,
    StudentProgress,
    StudyCard,
    StudyPlan,
    StudyPlanTask,
    StudySession,
    TutorMessage,
    WhatsAppDocumentJob,
    utc_now,
)
from app.schemas import (
    CardRead,
    CardUpdate,
    DiagnosticRequest,
    DocumentRead,
    EvaluationRequest,
    ExerciseAnswerRead,
    ExerciseAnswerRequest,
    ExerciseGenerateRequest,
    ExerciseHintRequest,
    ExerciseRead,
    ExerciseWorkspaceStartRequest,
    FeynmanRequest,
    LearningTopicRequest,
    LessonCheckRequest,
    LLMActiveProviderUpdate,
    LLMProviderConfigUpdate,
    MatchGameStartRequest,
    MatchGameSubmitRequest,
    PlanTaskUpdate,
    ReviewRequest,
    SessionFinishRequest,
    SessionStartRequest,
    StudyPlanRequest,
    TutorChatRequest,
)
from app.services.agents import AgentOrchestrator, agent_catalog
from app.services.analytics_service import build_analytics
from app.services.document_cleanup import (
    cleanup_card_learning_data,
    cleanup_document_learning_data,
)
from app.services.fsrs_scheduler import review_card
from app.services.language import normalize_language
from app.services.learning_engine import (
    build_daily_quests,
    build_learning_dashboard,
    claim_daily_quests,
    get_recommendations,
    get_student_progress,
    record_activity,
    sync_concepts,
    update_topic_after_attempt,
)
from app.services.learning_memory import record_exercise_result
from app.services.llm_settings import (
    PROVIDER_CATALOG,
    clear_provider_key,
    get_provider_credential,
    public_provider_settings,
    save_provider_credential,
    set_active_provider,
)
from app.services.ollama_client import OllamaClient, OllamaError
from app.services.retrieval import retrieve_context
from app.services.study_service import process_document

router = APIRouter(prefix="/api", tags=["OpenStudy API"])


def _public_exercise(exercise: Exercise) -> dict:
    return {
        "id": exercise.id,
        "question": exercise.question,
        "kind": exercise.kind,
        "options": exercise.options or [],
        "explanation": exercise.explanation,
        "difficulty": exercise.difficulty,
        "purpose": exercise.purpose,
        "bloom_level": exercise.bloom_level,
        "topic": exercise.topic,
        "hints": exercise.hints or [],
        "objective": exercise.objective,
        "remediation_error_id": exercise.remediation_error_id,
    }


def _plan_dict(plan: StudyPlan) -> dict:
    return {
        "id": plan.id,
        "title": plan.title,
        "objective": plan.objective,
        "summary": plan.summary,
        "days": plan.days,
        "minutes_per_day": plan.minutes_per_day,
        "status": plan.status,
        "created_at": plan.created_at,
        "tasks": [
            {
                "id": task.id,
                "day_number": task.day_number,
                "title": task.title,
                "description": task.description,
                "topic": task.topic,
                "estimated_minutes": task.estimated_minutes,
                "card_ids": task.card_ids or [],
                "completed": task.completed,
            }
            for task in sorted(plan.tasks, key=lambda item: (item.day_number, item.id))
        ],
    }


@router.get("/health")
def health(db: Session = Depends(get_db)) -> dict:
    client = OllamaClient(db=db)
    return {
        "status": "ok",
        "app": settings.app_name,
        "ai": client.health(),
        "ollama": client.ollama_health(),
        "features": {
            "rag": True,
            "embedding_model": settings.ollama_embedding_model,
            "adaptive_review": "FSRS-inspired",
            "study_agent": True,
            "multi_agent_orchestrator": True,
            "llm_evaluation": True,
            "observability": True,
            "multi_provider": True,
            "providers": list(PROVIDER_CATALOG),
        },
    }


@router.get("/llm/settings")
def llm_settings(db: Session = Depends(get_db)) -> dict:
    return public_provider_settings(db)


@router.put("/llm/providers/{provider}")
def update_llm_provider(
    provider: str,
    payload: LLMProviderConfigUpdate,
    db: Session = Depends(get_db),
) -> dict:
    if provider not in PROVIDER_CATALOG:
        raise HTTPException(status_code=404, detail="Provedor não suportado.")
    if provider != "ollama" and not payload.clear_api_key:
        existing = get_provider_credential(db, provider)
        if not payload.api_key and not existing["api_key"]:
            raise HTTPException(status_code=400, detail="Informe sua chave de API para este provedor.")
    try:
        config = save_provider_credential(
            db,
            provider,
            payload.model,
            api_key=payload.api_key,
            clear_api_key=payload.clear_api_key,
        )
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "provider": provider,
        "model": config["model"],
        "configured": config["configured"],
        "message": "Configuração salva. A chave é armazenada criptografada e nunca é exibida novamente.",
    }


@router.put("/llm/active")
def activate_llm_provider(
    payload: LLMActiveProviderUpdate,
    db: Session = Depends(get_db),
) -> dict:
    config = get_provider_credential(db, payload.provider)
    if not config["configured"]:
        raise HTTPException(
            status_code=400,
            detail="Este provedor ainda não tem uma chave de API configurada.",
        )
    if not config["model"]:
        raise HTTPException(status_code=400, detail="Informe um modelo antes de ativar o provedor.")
    set_active_provider(db, payload.provider)
    return {"active_provider": payload.provider, "model": config["model"]}


@router.delete("/llm/providers/{provider}/key", status_code=204)
def remove_llm_provider_key(provider: str, db: Session = Depends(get_db)):
    if provider not in PROVIDER_CATALOG:
        raise HTTPException(status_code=404, detail="Provedor não suportado.")
    clear_provider_key(db, provider)
    active = public_provider_settings(db)["active_provider"]
    if active == provider and provider != "ollama":
        set_active_provider(db, "ollama")
    return None


@router.post("/llm/providers/{provider}/test")
def test_llm_provider(provider: str, db: Session = Depends(get_db)) -> dict:
    if provider not in PROVIDER_CATALOG:
        raise HTTPException(status_code=404, detail="Provedor não suportado.")
    try:
        client = OllamaClient(db=db, provider_override=provider)
        message = client.test_connection()
    except OllamaError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"ok": True, "provider": provider, "model": client.model, "message": message}


@router.get("/llm/providers/{provider}/models")
def list_llm_provider_models(provider: str, db: Session = Depends(get_db)) -> dict:
    if provider not in PROVIDER_CATALOG:
        raise HTTPException(status_code=404, detail="Provedor não suportado.")
    try:
        client = OllamaClient(db=db, provider_override=provider)
        models = client.available_models()
    except OllamaError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"provider": provider, "models": models}


@router.get("/stats")
def stats(db: Session = Depends(get_db)) -> dict:
    return {
        "documents": db.query(func.count(Document.id)).scalar() or 0,
        "cards": db.query(func.count(StudyCard.id)).filter(StudyCard.archived.is_(False)).scalar() or 0,
        "exercises": db.query(func.count(Exercise.id)).scalar() or 0,
        "reviews": db.query(func.coalesce(func.sum(StudyCard.review_count), 0)).scalar() or 0,
    }


@router.get("/analytics/overview")
def analytics_overview(db: Session = Depends(get_db)) -> dict:
    return build_analytics(db)


@router.get("/agents/status")
def agents_status(db: Session = Depends(get_db)) -> dict:
    """Per-role latency and error counters from the last 500 model calls."""
    calls = db.query(LLMCallLog).filter(LLMCallLog.operation.like("agent:%")).order_by(
        LLMCallLog.id.desc()
    ).limit(500).all()
    grouped = {}
    for call in calls:
        parts = call.operation.split(":", 2)
        if len(parts) < 3:
            continue
        name = parts[1]
        metrics = grouped.setdefault(name, {"calls": 0, "errors": 0, "latencies_ms": []})
        metrics["calls"] += 1
        metrics["errors"] += not call.success
        metrics["latencies_ms"].append(call.elapsed_ms)
    result = []
    for agent in agent_catalog():
        stats = grouped.get(agent["id"], {"calls": 0, "errors": 0, "latencies_ms": []})
        ordered = sorted(stats["latencies_ms"])
        agent["metrics"] = {
            "calls": stats["calls"], "errors": int(stats["errors"]),
            "mean_ms": round(sum(ordered) / len(ordered)) if ordered else None,
            "p95_ms": ordered[max(0, (95 * len(ordered) + 99) // 100 - 1)] if ordered else None,
        }
        result.append(agent)
    return {"architecture": "shared_model_specialists", "agents": result,
            "execution": "deterministic_dispatch", "document_batching": settings.agent_batch_documents}


@router.get("/observability/summary")
def observability_summary(db: Session = Depends(get_db)) -> dict:
    calls = db.query(LLMCallLog).order_by(LLMCallLog.created_at.desc()).limit(500).all()
    successful = [call for call in calls if call.success]
    return {
        "calls": len(calls),
        "successes": len(successful),
        "errors": len(calls) - len(successful),
        "average_latency_ms": (
            round(sum(call.elapsed_ms for call in successful) / len(successful))
            if successful else 0
        ),
        "prompt_tokens": sum(call.prompt_tokens for call in calls),
        "generated_tokens": sum(call.generated_tokens for call in calls),
        "recent": [
            {
                "operation": call.operation,
                "provider": call.provider,
                "model": call.model,
                "elapsed_ms": call.elapsed_ms,
                "success": call.success,
                "error": call.error_message,
                "created_at": call.created_at,
            }
            for call in calls[:30]
        ],
    }


@router.post("/documents/upload", response_model=DocumentRead, status_code=202)
def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    language: str = Form(default="pt-BR"),
    db: Session = Depends(get_db),
):
    original_name = (file.filename or "material").replace("\\", "/").split("/")[-1]
    extension = original_name.rsplit(".", 1)[-1].lower() if "." in original_name else ""
    extension = f".{extension}"
    if extension not in {".pdf", ".docx", ".md"}:
        raise HTTPException(status_code=415, detail="Utilize arquivos PDF, DOCX ou Markdown (.md).")

    max_bytes = settings.max_upload_mb * 1024 * 1024
    content = file.file.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"Arquivo maior que o limite de {settings.max_upload_mb} MB.",
        )
    if not content:
        raise HTTPException(status_code=400, detail="O arquivo está vazio.")

    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    import uuid

    stored_path = settings.uploads_dir / f"{uuid.uuid4().hex}{extension}"
    stored_path.write_bytes(content)

    document = Document(
        original_filename=original_name[:255],
        stored_path=str(stored_path),
        file_type=extension,
        status="uploaded",
        rag_status="pending",
    )
    db.add(document)
    db.commit()
    db.refresh(document)
    background_tasks.add_task(process_document, document.id, normalize_language(language))
    return document


@router.get("/documents", response_model=list[DocumentRead])
def list_documents(db: Session = Depends(get_db)):
    return db.query(Document).order_by(Document.created_at.desc()).limit(200).all()


@router.get("/documents/{document_id}", response_model=DocumentRead)
def get_document(document_id: int, db: Session = Depends(get_db)):
    document = db.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Material não encontrado.")
    return document


@router.post("/documents/{document_id}/reprocess", response_model=DocumentRead, status_code=202)
def reprocess_document(
    document_id: int,
    background_tasks: BackgroundTasks,
    language: str = Query(default="pt-BR", pattern="^(pt-BR|es|en)$"),
    db: Session = Depends(get_db),
):
    document = db.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Material não encontrado.")
    if document.status == "processing":
        raise HTTPException(status_code=409, detail="Este material já está sendo processado.")
    document.status = "uploaded"
    document.rag_status = "pending"
    document.error_message = None
    db.commit()
    db.refresh(document)
    background_tasks.add_task(process_document, document.id, normalize_language(language))
    return document


@router.delete("/documents/{document_id}", status_code=204)
def delete_document(document_id: int, db: Session = Depends(get_db)):
    document = db.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Material não encontrado.")
    cleanup_document_learning_data(db, document_id)
    db.query(WhatsAppDocumentJob).filter(
        WhatsAppDocumentJob.document_id == document_id
    ).delete(synchronize_session=False)
    db.query(DocumentChunk).filter(DocumentChunk.document_id == document_id).delete(
        synchronize_session=False
    )
    path = __import__("pathlib").Path(document.stored_path)
    path.unlink(missing_ok=True)
    db.delete(document)
    db.commit()
    return None


@router.get("/cards", response_model=list[CardRead])
def list_cards(
    document_id: int | None = None,
    q: str | None = None,
    limit: int = 300,
    db: Session = Depends(get_db),
):
    query = db.query(StudyCard).filter(StudyCard.archived.is_(False))
    if document_id is not None:
        query = query.filter(StudyCard.document_id == document_id)
    if q:
        term = f"%{q.strip()}%"
        query = query.filter(or_(StudyCard.front.ilike(term), StudyCard.back.ilike(term)))
    return query.order_by(StudyCard.id.asc()).limit(min(max(limit, 1), 500)).all()


@router.patch("/cards/{card_id}", response_model=CardRead)
def update_card(card_id: int, payload: CardUpdate, db: Session = Depends(get_db)):
    card = db.get(StudyCard, card_id)
    if card is None:
        raise HTTPException(status_code=404, detail="Flashcard não encontrado.")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(card, field, value)
    db.commit()
    db.refresh(card)
    return card


@router.post("/cards/{card_id}/review")
def mark_card_reviewed(
    card_id: int,
    payload: ReviewRequest = ReviewRequest(),
    db: Session = Depends(get_db),
):
    card = db.get(StudyCard, card_id)
    if card is None or card.archived:
        raise HTTPException(status_code=404, detail="Flashcard não encontrado ou arquivado.")
    state = review_card(db, card, payload.rating)
    reward = record_activity(db, "card_review", f"card-review:{card.id}:{card.review_count}", 5 if payload.rating in {"good", "easy"} else 3)
    db.commit()
    db.refresh(state)
    return {
        "card_id": card.id,
        "review_count": card.review_count,
        "rating": payload.rating,
        "stability": state.stability,
        "difficulty": state.difficulty,
        "reps": state.reps,
        "lapses": state.lapses,
        "state": state.state,
        "due_at": state.due_at,
        "reward": reward,
    }


@router.get("/reviews/queue")
def review_queue(limit: int = 50, document_id: int | None = None, db: Session = Depends(get_db)):
    now = utc_now()
    query = (
        db.query(StudyCard, CardReviewState)
        .outerjoin(CardReviewState, CardReviewState.card_id == StudyCard.id)
        .filter(StudyCard.archived.is_(False))
        .filter((CardReviewState.id.is_(None)) | (CardReviewState.due_at <= now))
    )
    if document_id is not None:
        query = query.filter(StudyCard.document_id == document_id)
    rows = query.order_by(CardReviewState.id.is_(None), CardReviewState.due_at.asc(), StudyCard.review_count.asc(), StudyCard.id.asc()).limit(min(max(limit, 1), 200)).all()
    return [{
        "card": CardRead.model_validate(card).model_dump(),
        "due_at": state.due_at if state else now,
        "state": state.state if state else "new",
        "stability": state.stability if state else 0,
        "retrievability": None if state is None or state.last_review is None else __import__("app.services.fsrs_scheduler", fromlist=["retrievability"]).retrievability(
            state.stability, max(0, (now - (state.last_review.replace(tzinfo=None) if state.last_review.tzinfo else state.last_review)).total_seconds() / 86400)
        ),
    } for card, state in rows]


@router.get("/reviews/due")
def due_reviews(limit: int = 50, db: Session = Depends(get_db)):
    now = utc_now()
    rows = (
        db.query(StudyCard, CardReviewState)
        .outerjoin(CardReviewState, CardReviewState.card_id == StudyCard.id)
        .filter(StudyCard.archived.is_(False))
        .filter((CardReviewState.id.is_(None)) | (CardReviewState.due_at <= now))
        .order_by(CardReviewState.due_at.asc())
        .limit(min(max(limit, 1), 200))
        .all()
    )
    return [
        {
            "card": CardRead.model_validate(card).model_dump(),
            "due_at": state.due_at if state else now,
            "state": state.state if state else "new",
            "stability": state.stability if state else 0,
        }
        for card, state in rows
    ]


@router.delete("/cards/{card_id}", status_code=204)
def delete_card(card_id: int, db: Session = Depends(get_db)):
    card = db.get(StudyCard, card_id)
    if card is None:
        raise HTTPException(status_code=404, detail="Flashcard não encontrado.")
    cleanup_card_learning_data(db, [card.id])
    db.commit()
    return None


@router.post("/exercises/generate", response_model=list[ExerciseRead], status_code=201)
def create_exercises(payload: ExerciseGenerateRequest, db: Session = Depends(get_db)):
    query = db.query(StudyCard).filter(StudyCard.archived.is_(False))
    if payload.card_ids:
        query = query.filter(StudyCard.id.in_(payload.card_ids))
    cards = query.order_by(StudyCard.id.desc()).limit(100).all()
    if len(cards) < 3:
        raise HTTPException(
            status_code=400,
            detail="São necessários pelo menos 3 flashcards para gerar exercícios.",
        )

    card_data = [
        {
            "id": card.id,
            "front": card.front,
            "back": card.back,
            "explanation": card.explanation,
            "difficulty": card.difficulty,
            "tags": card.tags or [],
            "source_excerpt": card.source_excerpt,
        }
        for card in cards
    ]
    try:
        generated = AgentOrchestrator(db).exercises(
            card_data, payload.count, payload.difficulty, language=payload.language,
            purpose=payload.purpose, bloom_level=payload.bloom_level, topic=payload.topic,
            client_factory=OllamaClient,
        )
    except OllamaError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    if not generated:
        raise HTTPException(status_code=502, detail="O modelo não gerou exercícios válidos.")
    saved = []
    for item in generated:
        exercise = Exercise(**item)
        db.add(exercise)
        saved.append(exercise)
    db.commit()
    for exercise in saved:
        db.refresh(exercise)
    return [_public_exercise(item) for item in saved]


def _workspace_next_index(workspace: ExerciseWorkspace) -> int:
    results = workspace.results or {}
    for index, exercise_id in enumerate(workspace.exercise_ids or []):
        if str(exercise_id) not in results:
            return index
    return len(workspace.exercise_ids or [])


def _workspace_payload(db: Session, workspace: ExerciseWorkspace) -> dict:
    ids = [int(item) for item in (workspace.exercise_ids or [])]
    exercises = []
    for exercise_id in ids:
        exercise = db.get(Exercise, exercise_id)
        if exercise is not None:
            item = _public_exercise(exercise)
            item["result"] = (workspace.results or {}).get(str(exercise_id))
            exercises.append(item)
    current_index = _workspace_next_index(workspace)
    return {
        "id": workspace.id,
        "status": workspace.status,
        "purpose": workspace.purpose,
        "source": workspace.source,
        "exercise_ids": ids,
        "current_index": current_index,
        "focus_session_id": workspace.focus_session_id,
        "results": workspace.results or {},
        "exercises": exercises,
        "updated_at": workspace.updated_at,
    }


@router.post("/exercises/workspace", status_code=201)
def start_exercise_workspace(payload: ExerciseWorkspaceStartRequest, db: Session = Depends(get_db)) -> dict:
    exercise_ids = list(dict.fromkeys(payload.exercise_ids))
    if len(exercise_ids) != len(payload.exercise_ids):
        raise HTTPException(status_code=422, detail="A lista contém exercícios repetidos.")
    existing = db.query(Exercise).filter(Exercise.id.in_(exercise_ids)).all()
    if len(existing) != len(exercise_ids):
        raise HTTPException(status_code=404, detail="Um ou mais exercícios não foram encontrados.")

    previous = db.query(ExerciseWorkspace).filter_by(status="active").all()
    for item in previous:
        item.status = "superseded"
        item.updated_at = utc_now()
    workspace = ExerciseWorkspace(
        exercise_ids=exercise_ids,
        current_index=0,
        status="active",
        purpose=payload.purpose,
        source=payload.source,
        focus_session_id=payload.focus_session_id,
        results={},
    )
    db.add(workspace)
    db.commit()
    db.refresh(workspace)
    return _workspace_payload(db, workspace)


@router.get("/exercises/workspace/active")
def get_active_exercise_workspace(db: Session = Depends(get_db)) -> dict:
    workspace = db.query(ExerciseWorkspace).order_by(ExerciseWorkspace.id.desc()).first()
    if workspace is None:
        return {"active": False}
    payload = _workspace_payload(db, workspace)
    payload["active"] = workspace.status == "active"
    return payload


@router.post("/exercises/workspace/{workspace_id}/cancel")
def cancel_exercise_workspace(workspace_id: int, db: Session = Depends(get_db)) -> dict:
    workspace = db.get(ExerciseWorkspace, workspace_id)
    if workspace is None:
        raise HTTPException(status_code=404, detail="Sessão compartilhada não encontrada.")
    if workspace.status == "active":
        workspace.status = "cancelled"
        workspace.updated_at = utc_now()
        db.commit()
    return _workspace_payload(db, workspace)


@router.post("/exercises/{exercise_id}/answer", response_model=ExerciseAnswerRead)
def answer_exercise(
    exercise_id: int,
    payload: ExerciseAnswerRequest,
    db: Session = Depends(get_db),
):
    exercise = db.get(Exercise, exercise_id)
    if exercise is None:
        raise HTTPException(status_code=404, detail="Exercício não encontrado.")

    workspace = None
    if payload.workspace_id is not None:
        workspace = db.get(ExerciseWorkspace, payload.workspace_id)
        if workspace is None or exercise_id not in (workspace.exercise_ids or []):
            raise HTTPException(status_code=404, detail="Esta questão não pertence à sessão compartilhada.")
        stored_result = (workspace.results or {}).get(str(exercise_id))
        if stored_result:
            return stored_result
        if workspace.status != "active":
            raise HTTPException(status_code=409, detail="A sessão compartilhada não está mais ativa.")

    submitted = payload.answer.strip()
    expected = exercise.correct_answer.strip()
    diagnosis = None
    score = 0.0
    if exercise.kind == "short_answer":
        try:
            diagnosis = AgentOrchestrator(db).diagnose(
                exercise.question, expected, submitted, exercise.explanation,
                exercise.source_excerpt, language=payload.language, client_factory=OllamaClient,
            )
            score = float(diagnosis["score"])
            correct = score >= 0.7 and bool(diagnosis["is_correct"])
            feedback = diagnosis["explanation"]
        except OllamaError:
            normalize = lambda value: re.sub(r"\s+", " ", value).strip().casefold()
            correct = normalize(submitted) == normalize(expected)
            score = 1.0 if correct else 0.0
            feedback = "Resposta equivalente." if correct else "Não foi possível avaliar semanticamente agora. Compare com a resposta e revise a explicação."
            diagnosis = {
                "score": score, "is_correct": correct, "error_type": "knowledge_gap",
                "severity": "medium", "explanation": feedback,
                "misconception": "", "intervention": "Revise o conceito e tente explicar com suas palavras.",
                "practice_prompt": "Crie um exemplo próprio para o conceito.", "missing_concepts": [],
            }
    else:
        normalize = lambda value: re.sub(r"\s+", " ", value).strip().casefold()
        correct = normalize(submitted) == normalize(expected)
        score = 1.0 if correct else 0.0
        feedback = {
            "pt-BR": ("Resposta correta!" if correct else "Vamos entender o raciocínio por trás do erro."),
            "es": ("¡Respuesta correcta!" if correct else "Vamos a entender el razonamiento detrás del error."),
            "en": ("Correct answer!" if correct else "Let's understand the reasoning behind the mistake."),
        }[payload.language]
        if not correct:
            try:
                diagnosis = AgentOrchestrator(db).diagnose(
                    exercise.question, expected, submitted, exercise.explanation,
                    exercise.source_excerpt, language=payload.language, client_factory=OllamaClient,
                )
                score = min(0.69, float(diagnosis["score"]))
                correct = False
                feedback = diagnosis["explanation"]
            except OllamaError:
                diagnosis = {
                    "score": 0.0, "is_correct": False, "error_type": "knowledge_gap",
                    "severity": "medium", "explanation": feedback,
                    "misconception": "", "intervention": "Revise a explicação e tente uma questão semelhante.",
                    "practice_prompt": "Explique por que a alternativa correta faz sentido.", "missing_concepts": [],
                }

    attempt = ExerciseAttempt(
        exercise_id=exercise.id, user_answer=submitted, is_correct=correct,
        feedback=feedback, score=score, confidence=payload.confidence,
        error_type=None if correct else (diagnosis or {}).get("error_type", "knowledge_gap"),
        response_time_seconds=payload.response_time_seconds,
        feedback_data=diagnosis or {},
    )
    db.add(attempt)
    db.flush()
    cards = db.query(StudyCard).filter(StudyCard.id.in_(exercise.card_ids or [])).all()
    topics = {str(tag) for card in cards for tag in (card.tags or []) if str(tag).strip()}
    if exercise.topic:
        topics.add(exercise.topic)
    record_exercise_result(
        db, exercise.id, exercise.card_ids or [], correct, submitted,
        score=score, confidence=payload.confidence,
        error_type=None if correct else (diagnosis or {}).get("error_type"),
        response_time_seconds=payload.response_time_seconds,
        session_id=payload.session_id,
        is_transfer=exercise.purpose in {"transfer", "exam"} or exercise.bloom_level in {"apply", "analyze", "evaluate", "create"},
        extra_topics={exercise.topic} if exercise.topic else set(),
    )

    if payload.session_id:
        study_session = db.get(StudySession, payload.session_id)
        if study_session and study_session.status == "active":
            study_session.attempt_count += 1
            study_session.correct_count += int(correct)

    error_payload = None
    if not correct and diagnosis:
        topic = exercise.topic or next(iter(topics), "Geral")
        error = ErrorDiagnosis(
            exercise_id=exercise.id, attempt_id=attempt.id, topic=topic,
            error_type=diagnosis.get("error_type", "knowledge_gap"),
            severity=diagnosis.get("severity", "medium"),
            explanation=diagnosis.get("explanation", feedback),
            misconception=diagnosis.get("misconception", ""),
            intervention=diagnosis.get("intervention", "Revise o conceito."),
            practice_prompt=diagnosis.get("practice_prompt", "Tente novamente."),
        )
        db.add(error)
        db.flush()
        error_payload = {
            "id": error.id, "error_type": error.error_type, "severity": error.severity,
            "explanation": error.explanation, "misconception": error.misconception,
            "intervention": error.intervention, "practice_prompt": error.practice_prompt,
        }

    progress = get_student_progress(db)
    reward_xp = 12 if score >= 0.9 else 8 if score >= 0.7 else 3
    reward = record_activity(db, "exercise_attempt", f"exercise-attempt:{attempt.id}", reward_xp)
    remediation_progress = None
    if exercise.purpose == "challenge" and correct:
        bonus = record_activity(db, "challenge-bonus", f"challenge-attempt:{attempt.id}", 3)
        reward["xp_awarded"] += bonus["xp_awarded"]
        reward["xp"] = bonus["xp"]
    if exercise.remediation_error_id and correct:
        original_error = db.get(ErrorDiagnosis, exercise.remediation_error_id)
        if original_error and not original_error.resolved:
            successful_rechecks = (
                db.query(func.count(func.distinct(ExerciseAttempt.exercise_id)))
                .join(Exercise, Exercise.id == ExerciseAttempt.exercise_id)
                .filter(
                    Exercise.remediation_error_id == original_error.id,
                    ExerciseAttempt.is_correct.is_(True),
                )
                .scalar() or 0
            )
            remediation_progress = {
                "successful_rechecks": successful_rechecks,
                "required_rechecks": 2,
                "resolved": successful_rechecks >= 2,
            }
            if successful_rechecks >= 2:
                original_error.resolved = True
                bonus = record_activity(db, "error_resolved", f"error-resolved:{original_error.id}", 8)
                reward["xp_awarded"] += bonus["xp_awarded"]
                reward["xp"] = bonus["xp"]
    response = {
        "correct": correct, "score": round(score, 2), "feedback": feedback,
        "explanation": exercise.explanation, "correct_answer": expected,
        "error_diagnosis": error_payload, "reward": reward,
        "remediation_progress": remediation_progress,
        "submitted_answer": submitted,
    }
    if workspace is not None:
        results = dict(workspace.results or {})
        results[str(exercise_id)] = response
        workspace.results = results
        workspace.current_index = _workspace_next_index(workspace)
        if workspace.current_index >= len(workspace.exercise_ids or []):
            workspace.status = "completed"
        workspace.updated_at = utc_now()
    db.commit()
    return response


@router.post("/exercises/{exercise_id}/hint")
def exercise_hint(exercise_id: int, payload: ExerciseHintRequest, db: Session = Depends(get_db)):
    exercise = db.get(Exercise, exercise_id)
    if exercise is None:
        raise HTTPException(status_code=404, detail="Exercício não encontrado.")
    hints = exercise.hints or []
    if len(hints) >= payload.level and hints[payload.level - 1]:
        hint = str(hints[payload.level - 1])
    else:
        try:
            hint = OllamaClient(db=db).generate_hint(
                exercise.question, exercise.correct_answer, exercise.explanation,
                exercise.source_excerpt, payload.level, language=payload.language,
            )
        except OllamaError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
    expected_key = re.sub(r"\s+", " ", exercise.correct_answer).strip().casefold()
    hint_key = re.sub(r"\s+", " ", hint).strip().casefold()
    if expected_key and len(expected_key) >= 8 and expected_key in hint_key:
        hint = {
            1: "Pense em qual conceito do material se relaciona diretamente com a pergunta.",
            2: "Identifique a ideia principal e elimine as alternativas que contradizem essa ideia.",
            3: "Recupere a definição e conecte-a ao exemplo apresentado, sem consultar a resposta.",
        }[payload.level]
    db.add(LearningEvent(event_type="hint_requested", exercise_id=exercise.id, event_data={"level": payload.level}))
    db.commit()
    return {"level": payload.level, "hint": hint}


@router.post("/tutor/chat")
def tutor_chat(payload: TutorChatRequest, db: Session = Depends(get_db)):
    history_rows = (
        db.query(TutorMessage).order_by(TutorMessage.created_at.desc()).limit(8).all()
    )
    history = [
        {"role": item.role, "content": item.content}
        for item in reversed(history_rows)
    ]
    db.add(TutorMessage(role="user", content=payload.message, sources=[]))
    db.commit()

    try:
        answer, contexts = AgentOrchestrator(db).tutor(
            payload.message, document_ids=payload.document_ids,
            history=history, language=payload.language, mode=payload.mode,
            client_factory=OllamaClient,
        )
    except OllamaError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    sources = [
        {
            "citation": item["citation"],
            "document_id": item["document_id"],
            "filename": item["filename"],
            "chunk_index": item["chunk_index"],
            "page_number": item.get("page_number"),
            "score": item["score"],
            "method": item["method"],
            "excerpt": item["content"][:700],
        }
        for item in contexts
    ]
    db.add(TutorMessage(role="assistant", content=answer, sources=sources))
    db.commit()
    return {"answer": answer, "sources": sources, "retrieval_count": len(sources)}


@router.get("/tutor/history")
def tutor_history(limit: int = 30, db: Session = Depends(get_db)):
    messages = (
        db.query(TutorMessage)
        .order_by(TutorMessage.created_at.desc())
        .limit(min(max(limit, 1), 100))
        .all()
    )
    return [
        {
            "role": item.role,
            "content": item.content,
            "sources": item.sources or [],
            "created_at": item.created_at,
        }
        for item in reversed(messages)
    ]


@router.delete("/tutor/history", status_code=204)
def clear_tutor_history(db: Session = Depends(get_db)):
    db.query(TutorMessage).delete()
    db.commit()
    return None


@router.get("/learning/dashboard")
def learning_dashboard(db: Session = Depends(get_db)) -> dict:
    data = build_learning_dashboard(db)
    db.commit()
    return data


@router.get("/learning/quests")
def learning_quests(db: Session = Depends(get_db)) -> dict:
    return build_daily_quests(db)


@router.post("/learning/quests/claim")
def claim_learning_quests(db: Session = Depends(get_db)) -> dict:
    try:
        result = claim_daily_quests(db)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    db.commit()
    return result


@router.post("/learning/games/match", status_code=201)
def start_matching_game(payload: MatchGameStartRequest, db: Session = Depends(get_db)) -> dict:
    query = db.query(StudyCard).filter(StudyCard.archived.is_(False))
    if payload.topic:
        from app.services.learning_engine import normalize_topic
        topic_key = normalize_topic(payload.topic)
        concept = db.query(KnowledgeConcept).filter(
            KnowledgeConcept.normalized_key == topic_key
        ).one_or_none()
        if concept and concept.source_card_ids:
            query = query.filter(StudyCard.id.in_(concept.source_card_ids))
        else:
            all_cards = query.order_by(StudyCard.id.asc()).all()
            cards = [
                card for card in all_cards
                if any(normalize_topic(str(tag)) == topic_key for tag in (card.tags or []))
            ]
            query = None
    else:
        cards = None
    if query is not None:
        cards = query.order_by(StudyCard.id.asc()).all()
    if not cards or len(cards) < 3:
        raise HTTPException(status_code=400, detail="São necessários pelo menos três cards do assunto para iniciar o jogo.")

    pair_count = min(payload.pair_count, len(cards), 8)
    selected = random.SystemRandom().sample(cards, pair_count)
    term_ids = [uuid.uuid4().hex for _ in selected]
    definition_ids = [uuid.uuid4().hex for _ in selected]
    answer_key = dict(zip(term_ids, definition_ids))
    terms = [
        {"id": token, "text": card.front[:260]}
        for token, card in zip(term_ids, selected)
    ]
    definitions = [
        {"id": token, "text": card.back[:360]}
        for token, card in zip(definition_ids, selected)
    ]
    random.SystemRandom().shuffle(definitions)
    game = GameChallenge(
        game_type="matching",
        topic=payload.topic or (selected[0].tags[0] if selected[0].tags else "Geral"),
        answer_key=answer_key,
        pair_count=pair_count,
    )
    db.add(game)
    db.commit()
    db.refresh(game)
    return {
        "id": game.id,
        "game_type": game.game_type,
        "topic": game.topic,
        "pair_count": game.pair_count,
        "terms": terms,
        "definitions": definitions,
        "instructions": "Associe cada conceito à definição correta. Você pode trocar uma associação antes de enviar.",
    }


@router.post("/learning/games/{game_id}/submit")
def submit_matching_game(
    game_id: int, payload: MatchGameSubmitRequest, db: Session = Depends(get_db)
) -> dict:
    game = db.query(GameChallenge).filter(GameChallenge.id == game_id).with_for_update().one_or_none()
    if game is None or game.game_type != "matching":
        raise HTTPException(status_code=404, detail="Jogo não encontrado.")
    if game.completed:
        raise HTTPException(status_code=409, detail="Este desafio já foi concluído.")
    expected = game.answer_key or {}
    valid_terms = set(expected)
    valid_definitions = set(expected.values())
    if len(payload.matches) != game.pair_count or any(
        str(term) not in valid_terms or str(definition) not in valid_definitions
        for term, definition in payload.matches.items()
    ):
        raise HTTPException(status_code=422, detail="A rodada contém associações inválidas ou incompletas.")
    submitted = {str(term): str(definition) for term, definition in payload.matches.items()}
    if len(set(submitted.values())) != len(submitted):
        raise HTTPException(status_code=422, detail="Associe todos os conceitos a definições diferentes.")
    correct_pairs = sum(expected.get(term) == definition for term, definition in submitted.items())
    score = correct_pairs / max(1, game.pair_count)
    xp = correct_pairs * 2 + (5 if correct_pairs == game.pair_count else 0)
    game.completed = True
    game.score = round(score, 4)
    game.completed_at = utc_now()
    reward = record_activity(db, "matching_game", f"matching-game:{game.id}", xp)
    game.xp_earned = reward["xp_awarded"]
    db.add(LearningEvent(
        event_type="matching_game",
        topic=game.topic,
        correct=score >= 0.7,
        event_data={"score": score, "correct_pairs": correct_pairs, "pair_count": game.pair_count},
    ))
    progress = get_student_progress(db)
    progress.total_attempts += 1
    if score >= 0.7:
        progress.total_correct += 1
    update_topic_after_attempt(
        db, {game.topic}, score, 3.0, is_transfer=True, is_correct=score >= 0.7
    )
    db.commit()
    return {
        "correct_pairs": correct_pairs,
        "total_pairs": game.pair_count,
        "score": round(score, 2),
        "xp": reward["xp_awarded"],
        "reward": reward,
        "correct_matches": expected,
        "message": "Excelente associação!" if score == 1 else "Boa tentativa! Revise as associações e tente novamente em outra rodada.",
    }


@router.get("/learning/diagnostic/report")
def diagnostic_report(session_id: int = Query(ge=1), db: Session = Depends(get_db)) -> dict:
    events = (
        db.query(LearningEvent)
        .filter(
            LearningEvent.event_type == "exercise_attempt",
            LearningEvent.session_id == session_id,
        )
        .order_by(LearningEvent.created_at.asc())
        .all()
    )
    exercise_ids = [item.exercise_id for item in events if item.exercise_id is not None]
    exercises = {
        item.id: item for item in db.query(Exercise).filter(Exercise.id.in_(exercise_ids)).all()
    } if exercise_ids else {}
    grouped: dict[str, list[dict]] = {}
    for event in events:
        exercise = exercises.get(event.exercise_id)
        if not exercise or exercise.purpose != "diagnostic":
            continue
        topic = exercise.topic or "Geral"
        grouped.setdefault(topic, []).append({
            "score": float((event.event_data or {}).get("score", 0)),
            "correct": bool(event.correct),
            "error_type": (event.event_data or {}).get("error_type"),
        })
    topics = []
    for topic, attempts in grouped.items():
        average = sum(item["score"] for item in attempts) / len(attempts)
        topics.append({
            "topic": topic,
            "attempts": len(attempts),
            "score": round(average, 2),
            "correct": sum(item["correct"] for item in attempts),
            "error_types": sorted({item["error_type"] for item in attempts if item["error_type"]}),
            "status": "strong" if average >= 0.8 else "developing" if average >= 0.55 else "needs_attention",
        })
    topics.sort(key=lambda item: item["score"])
    return {
        "session_id": session_id,
        "attempted": sum(item["attempts"] for item in topics),
        "topics": topics,
        "priority_topics": [item["topic"] for item in topics if item["status"] == "needs_attention"],
        "summary": (
            "Comece pelos assuntos que precisam de atenção; o plano pode ser ajustado com base neste diagnóstico."
            if topics else "Nenhuma resposta diagnóstica foi registrada nesta sessão."
        ),
    }


@router.get("/learning/recommendations")
def learning_recommendations(db: Session = Depends(get_db)) -> dict:
    return {"recommendations": get_recommendations(db)}


@router.get("/learning/concepts")
def learning_concepts(db: Session = Depends(get_db)) -> list[dict]:
    concepts = sync_concepts(db)
    db.commit()
    return [{
        "id": item.id, "title": item.title, "definition": item.definition,
        "mastery": item.mastery_score, "prerequisites": item.prerequisites or [],
        "card_count": len(item.source_card_ids or []),
    } for item in concepts]


@router.post("/learning/concepts/sync")
def sync_learning_concepts(language: str = Query(default="pt-BR", pattern="^(pt-BR|es|en)$"), db: Session = Depends(get_db)) -> dict:
    cards = db.query(StudyCard).filter(StudyCard.archived.is_(False)).order_by(StudyCard.id.asc()).limit(120).all()
    if len(cards) < 3:
        raise HTTPException(status_code=400, detail="Adicione pelo menos três flashcards antes de mapear os conceitos.")
    card_data = [{"id": card.id, "front": card.front, "back": card.back, "tags": card.tags, "source_excerpt": card.source_excerpt} for card in cards]
    try:
        extracted = OllamaClient(db=db).generate_learning_concepts(card_data, language=language)
    except OllamaError as exc:
        concepts = sync_concepts(db)
        db.commit()
        return {"count": len(concepts), "fallback": True, "message": str(exc)}
    from app.services.learning_engine import normalize_topic
    saved = []
    for data in extracted:
        key = normalize_topic(data["title"])
        concept = db.query(KnowledgeConcept).filter(KnowledgeConcept.normalized_key == key).one_or_none()
        if concept is None:
            concept = KnowledgeConcept(normalized_key=key, title=data["title"])
            db.add(concept)
        for field in ("title", "definition", "simple_explanation", "technical_explanation", "example", "application", "common_mistakes", "prerequisites", "learning_objectives", "source_excerpt"):
            setattr(concept, field, data[field])
        mapped_ids = list(data["card_ids"])
        if not mapped_ids:
            title_tokens = set(key.split())
            mapped_ids = [
                card.id for card in cards
                if any(
                    normalize_topic(str(tag)) == key
                    or title_tokens.intersection(normalize_topic(str(tag)).split())
                    for tag in (card.tags or [])
                )
            ]
        concept.source_card_ids = sorted(set((concept.source_card_ids or []) + mapped_ids))
        concept.source_document_ids = sorted(set((concept.source_document_ids or []) + [card.document_id for card in cards if card.id in mapped_ids]))
        saved.append(concept)
    db.commit()
    return {"count": len(saved), "fallback": False, "concepts": [item.title for item in saved]}


@router.post("/learning/lesson")
def generate_learning_lesson(payload: LearningTopicRequest, db: Session = Depends(get_db)) -> dict:
    from app.services.learning_engine import normalize_topic
    key = normalize_topic(payload.topic)
    concept = db.query(KnowledgeConcept).filter(KnowledgeConcept.normalized_key == key).one_or_none()
    cards_query = db.query(StudyCard).filter(StudyCard.archived.is_(False))
    if payload.document_ids:
        cards_query = cards_query.filter(StudyCard.document_id.in_(payload.document_ids))
    if concept and concept.source_card_ids:
        cards_query = cards_query.filter(StudyCard.id.in_(concept.source_card_ids))
    cards = cards_query.limit(120).all()
    if not (concept and concept.source_card_ids):
        cards = [card for card in cards if payload.topic.casefold() in [str(tag).casefold() for tag in (card.tags or [])]][:30]
    requested_document_ids = sorted(set(payload.document_ids or (concept.source_document_ids if concept else []) or []))
    contexts = retrieve_context(
        db, payload.topic, requested_document_ids or None, top_k=6
    )
    mastery = concept.mastery_score if concept else 0.0
    recent_error_rows = (
        db.query(ErrorDiagnosis).filter(ErrorDiagnosis.resolved.is_(False))
        .order_by(ErrorDiagnosis.created_at.desc()).limit(30).all()
    )
    matching_error_rows = [
        item for item in recent_error_rows if normalize_topic(item.topic) == key
    ][:6]
    recent_errors = [
        {"type": item.error_type, "explanation": item.explanation, "misconception": item.misconception,
         "intervention": item.intervention}
        for item in matching_error_rows
    ]
    import hashlib
    error_signature = hashlib.sha256(
        "|".join(
            f"{item.id}:{item.error_type}:{item.topic}"
            for item in matching_error_rows
        ).encode("utf-8")
    ).hexdigest()[:20]
    prerequisite_concepts = []
    if concept:
        prerequisite_keys = {normalize_topic(item) for item in (concept.prerequisites or [])}
        prerequisite_concepts = [
            {"title": item.title, "mastery": item.mastery_score}
            for item in db.query(KnowledgeConcept).all()
            if normalize_topic(item.title) in prerequisite_keys
        ]
    cached_lesson = concept.lesson_content if concept else {}
    cache_valid = (
        isinstance(cached_lesson, dict)
        and cached_lesson.get("mastery_snapshot") is not None
        and abs(float(cached_lesson.get("mastery_snapshot", 0)) - float(mastery)) < 0.05
        and cached_lesson.get("error_signature_snapshot") == error_signature
        and cached_lesson.get("document_ids_snapshot") == requested_document_ids
    )
    if concept and cache_valid and concept.lesson_language == payload.language:
        lesson = dict(cached_lesson)
        lesson["cached"] = True
        lesson["sources"] = [{"citation": item["citation"], "filename": item["filename"], "page_number": item.get("page_number")} for item in contexts]
        return lesson
    try:
        lesson = OllamaClient(db=db).generate_lesson(
            payload.topic, contexts,
            [{"id": card.id, "front": card.front, "back": card.back} for card in cards],
            mastery=mastery, language=payload.language,
            recent_errors=recent_errors,
            prerequisites=[item["title"] for item in prerequisite_concepts],
        )
    except OllamaError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    lesson["cached"] = False
    lesson["mastery_snapshot"] = mastery
    lesson["error_count_snapshot"] = len(recent_errors)
    lesson["error_signature_snapshot"] = error_signature
    lesson["document_ids_snapshot"] = requested_document_ids
    lesson["sources"] = [{"citation": item["citation"], "filename": item["filename"], "page_number": item.get("page_number")} for item in contexts]
    if concept:
        concept.lesson_content = lesson
        concept.lesson_language = payload.language
        concept.last_studied_at = utc_now()
    db.add(LearningEvent(event_type="lesson_generated", topic=payload.topic, event_data={"source_count": len(contexts)}))
    reward = record_activity(db, "lesson_generated", f"lesson:{key}:{utc_now().date().isoformat()}", 5)
    db.commit()
    lesson["reward"] = reward
    return lesson


@router.post("/learning/lesson/check")
def check_learning_lesson(payload: LessonCheckRequest, db: Session = Depends(get_db)) -> dict:
    from app.services.learning_engine import normalize_topic

    concept = db.query(KnowledgeConcept).filter(
        KnowledgeConcept.normalized_key == normalize_topic(payload.topic)
    ).one_or_none()
    if concept is None or not concept.lesson_content:
        raise HTTPException(status_code=409, detail="Gere a microaula deste conceito antes de responder à verificação.")
    lesson = concept.lesson_content or {}
    question = str(lesson.get("check_question") or f"Explique o conceito {concept.title}.")
    expected = str(lesson.get("check_answer") or concept.definition or concept.simple_explanation)
    if not expected.strip():
        raise HTTPException(status_code=409, detail="A microaula não possui uma resposta de referência suficiente.")
    document_ids = payload.document_ids or concept.source_document_ids or None
    contexts = retrieve_context(db, payload.topic, document_ids, top_k=4)
    source_excerpt = "\n\n".join(item["content"] for item in contexts) or concept.source_excerpt
    try:
        diagnosis = OllamaClient(db=db).diagnose_learning_error(
            question, expected, payload.answer, concept.definition, source_excerpt,
            language=payload.language,
        )
    except OllamaError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    score = float(diagnosis["score"])
    correct = score >= 0.7 and bool(diagnosis["is_correct"])
    exercise = Exercise(
        question=question, kind="short_answer", options=[], correct_answer=expected,
        explanation=concept.definition or concept.simple_explanation,
        difficulty="intermediario", card_ids=concept.source_card_ids or [],
        purpose="lesson_check", bloom_level="understand", topic=concept.title,
        source_excerpt=source_excerpt, objective=f"Verificar a compreensão de {concept.title}.",
    )
    db.add(exercise)
    db.flush()
    attempt = ExerciseAttempt(
        exercise_id=exercise.id, user_answer=payload.answer.strip(),
        is_correct=correct, feedback=diagnosis["explanation"], score=score,
        confidence=payload.confidence, error_type=None if correct else diagnosis["error_type"],
        feedback_data=diagnosis,
    )
    db.add(attempt)
    db.flush()
    record_exercise_result(
        db, exercise.id, concept.source_card_ids or [], correct, payload.answer,
        score=score, confidence=payload.confidence,
        error_type=None if correct else diagnosis["error_type"],
        is_transfer=True, extra_topics={concept.title},
    )
    error_payload = None
    if not correct:
        error = ErrorDiagnosis(
            exercise_id=exercise.id, attempt_id=attempt.id, topic=concept.title,
            error_type=diagnosis["error_type"], severity=diagnosis["severity"],
            explanation=diagnosis["explanation"], misconception=diagnosis["misconception"],
            intervention=diagnosis["intervention"], practice_prompt=diagnosis["practice_prompt"],
        )
        db.add(error)
        db.flush()
        error_payload = {
            "id": error.id, "error_type": error.error_type,
            "explanation": error.explanation, "intervention": error.intervention,
            "practice_prompt": error.practice_prompt,
        }
    reward = record_activity(
        db, "lesson_check", f"lesson-check:{attempt.id}", 10 if correct else 2
    )
    db.commit()
    return {
        "correct": correct, "score": round(score, 2),
        "feedback": diagnosis["explanation"], "intervention": diagnosis["intervention"],
        "missing_concepts": diagnosis["missing_concepts"],
        "error_diagnosis": error_payload, "reward": reward,
    }


@router.post("/learning/diagnostic", status_code=201)
def create_diagnostic(payload: DiagnosticRequest, db: Session = Depends(get_db)) -> list[dict]:
    query = db.query(StudyCard).filter(StudyCard.archived.is_(False))
    if payload.card_ids:
        query = query.filter(StudyCard.id.in_(payload.card_ids))
    cards = query.order_by(StudyCard.review_count.asc(), StudyCard.id.asc()).limit(200).all()
    if payload.topic:
        from app.services.learning_engine import normalize_topic
        concept = db.query(KnowledgeConcept).filter(KnowledgeConcept.normalized_key == normalize_topic(payload.topic)).one_or_none()
        if concept and concept.source_card_ids:
            cards = [card for card in cards if card.id in concept.source_card_ids]
        else:
            cards = [card for card in cards if payload.topic.casefold() in [str(tag).casefold() for tag in (card.tags or [])]]
    cards = cards[:100]
    if len(cards) < 3:
        raise HTTPException(status_code=400, detail="São necessários pelo menos três cartões relacionados para o diagnóstico.")
    card_data = [{"id": card.id, "front": card.front, "back": card.back, "explanation": card.explanation, "difficulty": card.difficulty} for card in cards]
    try:
        generated = OllamaClient(db=db).generate_exercises(
            card_data, payload.count, "mista", language=payload.language,
            purpose="diagnostic", bloom_level="mixed", topic=payload.topic,
        )
    except OllamaError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if not generated:
        raise HTTPException(status_code=502, detail="A IA não conseguiu gerar questões de diagnóstico válidas.")
    card_by_id = {card.id: card for card in cards}
    saved = []
    for item in generated:
        item["purpose"] = "diagnostic"
        if not str(item.get("topic") or "").strip():
            source_tags = []
            for card_id in (item.get("card_ids") or []):
                source_card = card_by_id.get(card_id)
                if source_card:
                    source_tags.extend(
                        str(tag).strip() for tag in (source_card.tags or []) if str(tag).strip()
                    )
            if source_tags:
                from collections import Counter
                item["topic"] = Counter(source_tags).most_common(1)[0][0]
            else:
                item["topic"] = "Geral"
        db.add(Exercise(**item))
        saved.append(item)
    db.add(LearningEvent(event_type="diagnostic_started", topic=payload.topic or "Geral", event_data={"question_count": len(saved)}))
    db.commit()
    rows = db.query(Exercise).filter(Exercise.purpose == "diagnostic").order_by(Exercise.id.desc()).limit(len(saved)).all()
    return [_public_exercise(item) for item in reversed(rows)]


@router.post("/learning/feynman")
def evaluate_feynman(payload: FeynmanRequest, db: Session = Depends(get_db)) -> dict:
    contexts = retrieve_context(db, payload.topic, top_k=5)
    try:
        result = OllamaClient(db=db).evaluate_feynman(
            payload.topic, payload.explanation, contexts, language=payload.language
        )
    except OllamaError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    import hashlib
    attempt_fingerprint = hashlib.sha256(
        (payload.topic + "\\0" + payload.explanation.strip()).encode("utf-8")
    ).hexdigest()[:24]
    event_key = f"feynman:{attempt_fingerprint}"
    duplicate_attempt = (
        db.query(RewardEvent).filter(RewardEvent.event_key == event_key).one_or_none()
        is not None
    )
    feynman_correct = result["overall_score"] >= 0.7

    if not duplicate_attempt:
        db.add(LearningEvent(
            event_type="feynman_attempt", topic=payload.topic,
            correct=feynman_correct, confidence=3.0,
            event_data={
                "score": result["overall_score"], "scores": result["scores"],
                "misconceptions": result["misconceptions"],
                "missing_concepts": result["missing_concepts"],
            },
        ))
        update_topic_after_attempt(
            db, {payload.topic}, result["overall_score"], 3.0,
            is_transfer=True, is_correct=feynman_correct,
        )
        progress = get_student_progress(db)
        progress.total_attempts += 1
        if feynman_correct:
            progress.total_correct += 1

        if not feynman_correct or result["misconceptions"] or result["missing_concepts"]:
            error = ErrorDiagnosis(
                topic=payload.topic,
                error_type="misconception" if result["misconceptions"] else "incomplete_answer",
                severity="high" if result["overall_score"] < 0.35 else "medium",
                explanation=result["feedback"],
                misconception="; ".join(str(item) for item in result["misconceptions"][:5]),
                intervention=result["priority_improvement"],
                practice_prompt=result["retry_prompt"],
            )
            db.add(error)
            db.flush()
            result["error_diagnosis"] = {
                "id": error.id, "error_type": error.error_type,
                "intervention": error.intervention, "practice_prompt": error.practice_prompt,
            }

    reward = record_activity(
        db, "feynman_attempt", event_key, 12 if feynman_correct else 5
    )
    result["reward"] = reward
    result["duplicate_attempt"] = duplicate_attempt
    db.commit()
    return result


@router.post("/learning/sessions/start", status_code=201)
def start_learning_session(payload: SessionStartRequest, db: Session = Depends(get_db)) -> dict:
    session = StudySession(session_type=payload.session_type, status="active")
    db.add(session)
    progress = get_student_progress(db)
    progress.total_sessions += 1
    db.commit()
    db.refresh(session)
    return {"id": session.id, "session_type": session.session_type, "started_at": session.started_at}


@router.post("/learning/sessions/{session_id}/finish")
def finish_learning_session(session_id: int, payload: SessionFinishRequest, db: Session = Depends(get_db)) -> dict:
    session = db.get(StudySession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Sessão não encontrada.")
    if session.status == "completed":
        return {"id": session.id, "duration_seconds": session.duration_seconds, "xp_earned": session.xp_earned}
    session.ended_at = utc_now()
    started = session.started_at.replace(tzinfo=None) if session.started_at.tzinfo else session.started_at
    session.duration_seconds = max(0, int((session.ended_at - started).total_seconds()))
    session.status = "completed"
    session.summary = payload.summary
    earned = min(30, max(0, session.duration_seconds // 300 * 5))
    reward = record_activity(db, "session_completed", f"study-session:{session.id}", earned)
    session.xp_earned = reward["xp_awarded"]
    db.add(LearningEvent(event_type="session_completed", session_id=session.id, event_data={"duration_seconds": session.duration_seconds, "xp": session.xp_earned}))
    db.commit()
    return {"id": session.id, "duration_seconds": session.duration_seconds, "xp_earned": session.xp_earned, "reward": reward}


@router.get("/learning/errors")
def list_learning_errors(limit: int = 30, include_resolved: bool = False, db: Session = Depends(get_db)) -> list[dict]:
    query = db.query(ErrorDiagnosis)
    if not include_resolved:
        query = query.filter(ErrorDiagnosis.resolved.is_(False))
    rows = query.order_by(ErrorDiagnosis.created_at.desc()).limit(min(max(limit, 1), 100)).all()
    result = []
    for item in rows:
        successes = (
            db.query(func.count(func.distinct(ExerciseAttempt.exercise_id)))
            .join(Exercise, Exercise.id == ExerciseAttempt.exercise_id)
            .filter(
                Exercise.remediation_error_id == item.id,
                ExerciseAttempt.is_correct.is_(True),
            )
            .scalar() or 0
        )
        result.append({
            "id": item.id, "exercise_id": item.exercise_id, "topic": item.topic,
            "error_type": item.error_type, "severity": item.severity,
            "explanation": item.explanation, "misconception": item.misconception,
            "intervention": item.intervention, "practice_prompt": item.practice_prompt,
            "resolved": item.resolved, "successful_rechecks": successes,
            "required_rechecks": 2, "created_at": item.created_at,
        })
    return result


@router.post("/learning/errors/{error_id}/practice", status_code=201)
def create_error_remediation(error_id: int, language: str = Query(default="pt-BR", pattern="^(pt-BR|es|en)$"), db: Session = Depends(get_db)) -> dict:
    error = db.get(ErrorDiagnosis, error_id)
    if error is None:
        raise HTTPException(status_code=404, detail="Dificuldade não encontrada.")
    if error.resolved:
        raise HTTPException(status_code=409, detail="Esta dificuldade já foi marcada como superada.")
    original = db.get(Exercise, error.exercise_id) if error.exercise_id else None
    card_ids = list(original.card_ids or []) if original else []
    active_query = db.query(StudyCard).filter(StudyCard.archived.is_(False))
    if card_ids:
        related = active_query.filter(StudyCard.id.in_(card_ids)).all()
    else:
        from app.services.learning_engine import normalize_topic
        topic_key = normalize_topic(error.topic)
        related = [
            card for card in active_query.order_by(StudyCard.id.desc()).all()
            if any(normalize_topic(str(tag)) == topic_key for tag in (card.tags or []))
        ]
    if not related:
        raise HTTPException(status_code=400, detail="Não encontrei cards relacionados a esta dificuldade. Atualize o mapa de conceitos ou envie o material de origem.")
    card_data = [{"id": card.id, "front": card.front, "back": card.back, "explanation": card.explanation, "difficulty": card.difficulty, "tags": card.tags, "source_excerpt": card.source_excerpt} for card in related[:30]]
    prompt_topic = error.topic
    try:
        generated = OllamaClient(db=db).generate_exercises(
            card_data, 1, "intermediario", language=language,
            purpose="remediation", bloom_level="apply", topic=prompt_topic,
        )
    except OllamaError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if not generated:
        raise HTTPException(status_code=502, detail="Não foi possível gerar a atividade de reforço.")
    item = generated[0]
    item["purpose"] = "remediation"
    item["remediation_error_id"] = error.id
    exercise = Exercise(**item)
    db.add(exercise)
    db.commit()
    db.refresh(exercise)
    return _public_exercise(exercise)


@router.post("/learning/errors/{error_id}/resolve")
def resolve_learning_error(error_id: int, db: Session = Depends(get_db)) -> dict:
    error = db.get(ErrorDiagnosis, error_id)
    if error is None:
        raise HTTPException(status_code=404, detail="Dificuldade não encontrada.")
    successful_rechecks = (
        db.query(func.count(func.distinct(ExerciseAttempt.exercise_id)))
        .join(Exercise, Exercise.id == ExerciseAttempt.exercise_id)
        .filter(
            Exercise.remediation_error_id == error.id,
            ExerciseAttempt.is_correct.is_(True),
        )
        .scalar() or 0
    )
    if successful_rechecks < 2:
        raise HTTPException(
            status_code=409,
            detail=f"São necessárias duas verificações corretas de reforço. Progresso: {successful_rechecks}/2.",
        )
    error.resolved = True
    reward = record_activity(db, "error_resolved", f"error-resolved:{error.id}", 8)
    db.commit()
    return {"id": error.id, "resolved": True, "reward": reward}


@router.post("/agent/plan")
def create_study_plan(payload: StudyPlanRequest, db: Session = Depends(get_db)):
    cards = db.query(StudyCard).filter(StudyCard.archived.is_(False)).order_by(StudyCard.id.asc()).limit(500).all()
    if not cards:
        raise HTTPException(status_code=400, detail="Adicione materiais e flashcards antes de criar um plano.")

    analytics = build_analytics(db)
    card_ids = [card.id for card in cards]
    context = {
        "objective": payload.objective,
        "days": payload.days,
        "minutes_per_day": payload.minutes_per_day,
        "weak_topics": analytics["weak_topics"],
        "strong_topics": analytics["strong_topics"],
        "recent_errors": [{"topic": item.topic, "type": item.error_type, "intervention": item.intervention} for item in db.query(ErrorDiagnosis).filter(ErrorDiagnosis.resolved.is_(False)).order_by(ErrorDiagnosis.created_at.desc()).limit(10).all()],
        "all_topics": analytics["topics"][:30],
        "due_cards": analytics["due_cards"],
        "due_card_ids": [row[0] for row in db.query(StudyCard.id).outerjoin(CardReviewState, CardReviewState.card_id == StudyCard.id).filter(StudyCard.archived.is_(False)).filter((CardReviewState.id.is_(None)) | (CardReviewState.due_at <= utc_now())).limit(100).all()],
        "available_card_ids": card_ids,
        "minutes_per_day": payload.minutes_per_day,
        "sample_cards": [
            {"id": card.id, "front": card.front, "tags": card.tags}
            for card in cards[:60]
        ],
    }
    try:
        plan_data = AgentOrchestrator(db).plan(context, language=payload.language, client_factory=OllamaClient)
    except OllamaError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    if not plan_data["tasks"]:
        raise HTTPException(status_code=502, detail="O agente não conseguiu criar tarefas válidas.")

    budget = payload.minutes_per_day
    normalized_tasks = []
    spent_by_day: dict[int, int] = {}
    for item in sorted(plan_data["tasks"], key=lambda task: (task["day_number"], task["estimated_minutes"])):
        day = item["day_number"]
        remaining = budget - spent_by_day.get(day, 0)
        if remaining < 5:
            continue
        item["estimated_minutes"] = min(item["estimated_minutes"], remaining)
        if item["estimated_minutes"] < 5:
            continue
        spent_by_day[day] = spent_by_day.get(day, 0) + item["estimated_minutes"]
        normalized_tasks.append(item)
    plan_data["tasks"] = normalized_tasks
    if not plan_data["tasks"]:
        raise HTTPException(status_code=502, detail="O plano gerado não respeitou o tempo diário disponível. Tente novamente.")

    plan = StudyPlan(
        title=plan_data["title"],
        objective=payload.objective,
        summary=plan_data["summary"],
        days=payload.days,
        minutes_per_day=payload.minutes_per_day,
        status="active",
    )
    db.add(plan)
    db.flush()
    for item in plan_data["tasks"]:
        db.add(StudyPlanTask(plan_id=plan.id, **item))
    db.commit()
    db.refresh(plan)
    record_activity(db, "plan_created", f"plan-created:{plan.id}", 15)
    db.commit()
    return _plan_dict(plan)


@router.get("/agent/plans")
def list_study_plans(db: Session = Depends(get_db)):
    plans = db.query(StudyPlan).order_by(StudyPlan.created_at.desc()).limit(20).all()
    return [_plan_dict(plan) for plan in plans]


@router.get("/agent/plans/{plan_id}")
def get_study_plan(plan_id: int, db: Session = Depends(get_db)):
    plan = db.get(StudyPlan, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="Plano não encontrado.")
    return _plan_dict(plan)


@router.post("/agent/plans/{plan_id}/adapt")
def adapt_study_plan(
    plan_id: int,
    language: str = Query(default="pt-BR", pattern="^(pt-BR|es|en)$"),
    db: Session = Depends(get_db),
):
    plan = db.get(StudyPlan, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="Plano não encontrado.")
    incomplete = [task for task in plan.tasks if not task.completed]
    if not incomplete:
        return _plan_dict(plan)
    current_day = min(task.day_number for task in incomplete)
    remaining_days = max(1, plan.days - current_day + 1)
    cards = db.query(StudyCard).filter(StudyCard.archived.is_(False)).order_by(StudyCard.id.asc()).limit(500).all()
    if not cards:
        raise HTTPException(status_code=400, detail="Não há materiais ativos para adaptar o plano.")
    analytics = build_analytics(db)
    available_ids = [card.id for card in cards]
    due_ids = [row[0] for row in db.query(StudyCard.id).outerjoin(CardReviewState, CardReviewState.card_id == StudyCard.id).filter(StudyCard.archived.is_(False)).filter((CardReviewState.id.is_(None)) | (CardReviewState.due_at <= utc_now())).limit(100).all()]
    context = {
        "objective": plan.objective, "days": remaining_days,
        "minutes_per_day": plan.minutes_per_day or 45,
        "weak_topics": analytics["weak_topics"], "strong_topics": analytics["strong_topics"],
        "recent_errors": [{"topic": item.topic, "type": item.error_type, "intervention": item.intervention} for item in db.query(ErrorDiagnosis).filter(ErrorDiagnosis.resolved.is_(False)).order_by(ErrorDiagnosis.created_at.desc()).limit(10).all()],
        "all_topics": analytics["topics"][:30], "due_cards": analytics["due_cards"],
        "due_card_ids": due_ids, "available_card_ids": available_ids,
        "sample_cards": [{"id": card.id, "front": card.front, "tags": card.tags} for card in cards[:60]],
    }
    try:
        generated = AgentOrchestrator(db).plan(context, language=language, client_factory=OllamaClient)
    except OllamaError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if not generated["tasks"]:
        raise HTTPException(status_code=502, detail="O agente não conseguiu adaptar as tarefas.")

    spent_by_day: dict[int, int] = {}
    for task in plan.tasks:
        if task.completed:
            spent_by_day[task.day_number] = spent_by_day.get(task.day_number, 0) + task.estimated_minutes
    normalized = []
    budget = plan.minutes_per_day or 45
    for item in sorted(generated["tasks"], key=lambda task: (task["day_number"], task["estimated_minutes"])):
        day = current_day + item["day_number"] - 1
        if day > plan.days:
            continue
        remaining = budget - spent_by_day.get(day, 0)
        if remaining < 5:
            continue
        item["day_number"] = day
        item["estimated_minutes"] = min(item["estimated_minutes"], remaining)
        if item["estimated_minutes"] < 5:
            continue
        spent_by_day[day] = spent_by_day.get(day, 0) + item["estimated_minutes"]
        normalized.append(item)
    if not normalized:
        raise HTTPException(status_code=502, detail="Não há espaço de tempo disponível para as novas tarefas.")

    for task in incomplete:
        db.delete(task)
    for item in normalized:
        db.add(StudyPlanTask(plan_id=plan.id, **item))
    plan.summary = generated["summary"]
    record_activity(db, "plan_adapted", f"plan-adapted:{plan.id}:{utc_now().isoformat()}", 5)
    db.commit()
    db.refresh(plan)
    return _plan_dict(plan)


@router.patch("/agent/tasks/{task_id}")
def update_study_task(task_id: int, payload: PlanTaskUpdate, db: Session = Depends(get_db)):
    task = db.get(StudyPlanTask, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Tarefa não encontrada.")
    task.completed = payload.completed
    db.commit()
    return {"id": task.id, "completed": task.completed}


@router.post("/agent/insights")
def learning_insights(
    language: str = Query(default="pt-BR", pattern="^(pt-BR|es|en)$"),
    db: Session = Depends(get_db),
):
    analytics = build_analytics(db)
    try:
        insights = OllamaClient(db=db).generate_insights(analytics, language=language)
    except OllamaError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"analytics": analytics, "insights": insights}


@router.post("/evaluations/run")
def run_generation_evaluation(payload: EvaluationRequest, db: Session = Depends(get_db)):
    query = db.query(StudyCard)
    if payload.card_ids:
        query = query.filter(StudyCard.id.in_(payload.card_ids))
    cards = query.order_by(StudyCard.id.desc()).limit(payload.limit).all()
    if not cards:
        raise HTTPException(status_code=400, detail="Não há flashcards para avaliar.")

    client = OllamaClient(db=db)
    results = []
    for card in cards:
        source = db.get(Document, card.document_id)
        card_data = {
            "front": card.front,
            "back": card.back,
            "explanation": card.explanation,
            "tags": card.tags,
            "source_excerpt": card.source_excerpt,
            "document": source.original_filename if source else "Documento indisponível",
        }
        try:
            evaluation = client.evaluate_card(card_data, language=payload.language)
        except OllamaError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        record = GenerationEvaluation(
            target_type="flashcard",
            target_id=card.id,
            scores=evaluation["scores"],
            overall_score=evaluation["overall_score"],
            issues=evaluation["issues"],
            suggestion=evaluation["suggestion"],
            model=client.model_ref,
        )
        db.add(record)
        results.append({
            "card_id": card.id,
            "front": card.front,
            **evaluation,
        })
    db.commit()
    return {
        "evaluated": len(results),
        "average_score": round(sum(item["overall_score"] for item in results) / len(results), 2),
        "results": results,
    }


@router.get("/evaluations/history")
def evaluation_history(limit: int = 50, db: Session = Depends(get_db)):
    records = (
        db.query(GenerationEvaluation)
        .order_by(GenerationEvaluation.created_at.desc())
        .limit(min(max(limit, 1), 200))
        .all()
    )
    return [
        {
            "id": item.id,
            "target_type": item.target_type,
            "target_id": item.target_id,
            "scores": item.scores,
            "overall_score": item.overall_score,
            "issues": item.issues,
            "suggestion": item.suggestion,
            "model": item.model,
            "created_at": item.created_at,
        }
        for item in records
    ]
