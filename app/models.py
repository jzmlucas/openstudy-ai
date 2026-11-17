from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    stored_path: Mapped[str] = mapped_column(String(600), nullable=False)
    file_type: Mapped[str] = mapped_column(String(10), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="uploaded", index=True)
    rag_status: Mapped[str] = mapped_column(String(30), default="pending")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    extracted_chars: Mapped[int] = mapped_column(Integer, default=0)
    content_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    cards: Mapped[list["StudyCard"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )
    chunks: Mapped[list["DocumentChunk"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class DocumentChunk(Base):
    __tablename__ = "document_chunks"
    __table_args__ = (UniqueConstraint("document_id", "chunk_index", name="uq_document_chunk"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), index=True)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    embedding: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    document: Mapped["Document"] = relationship(back_populates="chunks")


class StudyCard(Base):
    __tablename__ = "study_cards"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), index=True)
    front: Mapped[str] = mapped_column(Text, nullable=False)
    back: Mapped[str] = mapped_column(Text, nullable=False)
    explanation: Mapped[str] = mapped_column(Text, default="")
    difficulty: Mapped[str] = mapped_column(String(20), default="intermediario")
    tags: Mapped[list] = mapped_column(JSON, default=list)
    source_excerpt: Mapped[str] = mapped_column(Text, default="")
    review_count: Mapped[int] = mapped_column(Integer, default=0)
    archived: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    version_number: Mapped[int] = mapped_column(Integer, default=1)
    concept_key: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    document: Mapped["Document"] = relationship(back_populates="cards")
    review_state: Mapped["CardReviewState | None"] = relationship(
        back_populates="card", cascade="all, delete-orphan", uselist=False
    )


class CardReviewState(Base):
    __tablename__ = "card_review_states"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    card_id: Mapped[int] = mapped_column(ForeignKey("study_cards.id"), unique=True, index=True)
    stability: Mapped[float] = mapped_column(Float, default=1.0)
    difficulty: Mapped[float] = mapped_column(Float, default=5.0)
    reps: Mapped[int] = mapped_column(Integer, default=0)
    lapses: Mapped[int] = mapped_column(Integer, default=0)
    step: Mapped[int | None] = mapped_column(Integer, nullable=True)
    state: Mapped[str] = mapped_column(String(20), default="new")
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
    last_review: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    card: Mapped["StudyCard"] = relationship(back_populates="review_state")


class Exercise(Base):
    __tablename__ = "exercises"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    options: Mapped[list] = mapped_column(JSON, default=list)
    correct_answer: Mapped[str] = mapped_column(Text, nullable=False)
    explanation: Mapped[str] = mapped_column(Text, default="")
    difficulty: Mapped[str] = mapped_column(String(20), default="intermediario")
    card_ids: Mapped[list] = mapped_column(JSON, default=list)
    purpose: Mapped[str] = mapped_column(String(30), default="practice", index=True)
    bloom_level: Mapped[str] = mapped_column(String(30), default="understand")
    topic: Mapped[str] = mapped_column(String(160), default="")
    hints: Mapped[list] = mapped_column(JSON, default=list)
    source_excerpt: Mapped[str] = mapped_column(Text, default="")
    objective: Mapped[str] = mapped_column(Text, default="")
    remediation_error_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ExerciseAttempt(Base):
    __tablename__ = "exercise_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    exercise_id: Mapped[int] = mapped_column(ForeignKey("exercises.id"), index=True)
    user_answer: Mapped[str] = mapped_column(Text, nullable=False)
    is_correct: Mapped[bool] = mapped_column(Boolean, default=False)
    feedback: Mapped[str] = mapped_column(Text, default="")
    score: Mapped[float] = mapped_column(Float, default=0.0)
    confidence: Mapped[float] = mapped_column(Float, default=3.0)
    error_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    response_time_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    feedback_data: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ExerciseWorkspace(Base):
    """Single shared exercise queue visible to the desktop UI and linked WhatsApp."""

    __tablename__ = "exercise_workspaces"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    exercise_ids: Mapped[list] = mapped_column(JSON, default=list)
    current_index: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)
    purpose: Mapped[str] = mapped_column(String(30), default="practice")
    source: Mapped[str] = mapped_column(String(20), default="desktop")
    focus_session_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    results: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class LearningEvent(Base):
    __tablename__ = "learning_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    event_type: Mapped[str] = mapped_column(String(40), index=True)
    card_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    exercise_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    topic: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    event_data: Mapped[dict] = mapped_column(JSON, default=dict)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    session_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class TopicMastery(Base):
    __tablename__ = "topic_mastery"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    topic: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    correct: Mapped[int] = mapped_column(Integer, default=0)
    mastery_score: Mapped[float] = mapped_column(Float, default=0.0)
    confidence_score: Mapped[float] = mapped_column(Float, default=0.0)
    retention_score: Mapped[float] = mapped_column(Float, default=0.0)
    transfer_score: Mapped[float] = mapped_column(Float, default=0.0)
    forgetting_rate: Mapped[float] = mapped_column(Float, default=0.0)
    last_practiced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class StudyPlan(Base):
    __tablename__ = "study_plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    objective: Mapped[str] = mapped_column(Text, default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    days: Mapped[int] = mapped_column(Integer, default=7)
    minutes_per_day: Mapped[int] = mapped_column(Integer, default=45)
    status: Mapped[str] = mapped_column(String(20), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    tasks: Mapped[list["StudyPlanTask"]] = relationship(
        back_populates="plan", cascade="all, delete-orphan"
    )


class StudyPlanTask(Base):
    __tablename__ = "study_plan_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("study_plans.id"), index=True)
    day_number: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    topic: Mapped[str] = mapped_column(String(160), default="")
    estimated_minutes: Mapped[int] = mapped_column(Integer, default=20)
    card_ids: Mapped[list] = mapped_column(JSON, default=list)
    completed: Mapped[bool] = mapped_column(Boolean, default=False)

    plan: Mapped["StudyPlan"] = relationship(back_populates="tasks")


class KnowledgeConcept(Base):
    __tablename__ = "knowledge_concepts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    normalized_key: Mapped[str] = mapped_column(String(180), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(180), nullable=False)
    definition: Mapped[str] = mapped_column(Text, default="")
    simple_explanation: Mapped[str] = mapped_column(Text, default="")
    technical_explanation: Mapped[str] = mapped_column(Text, default="")
    example: Mapped[str] = mapped_column(Text, default="")
    application: Mapped[str] = mapped_column(Text, default="")
    common_mistakes: Mapped[list] = mapped_column(JSON, default=list)
    prerequisites: Mapped[list] = mapped_column(JSON, default=list)
    learning_objectives: Mapped[list] = mapped_column(JSON, default=list)
    source_card_ids: Mapped[list] = mapped_column(JSON, default=list)
    source_document_ids: Mapped[list] = mapped_column(JSON, default=list)
    source_excerpt: Mapped[str] = mapped_column(Text, default="")
    lesson_content: Mapped[dict] = mapped_column(JSON, default=dict)
    lesson_language: Mapped[str] = mapped_column(String(10), default="pt-BR")
    mastery_score: Mapped[float] = mapped_column(Float, default=0.0)
    last_studied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class StudentProgress(Base):
    __tablename__ = "student_progress"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    xp: Mapped[int] = mapped_column(Integer, default=0)
    level: Mapped[int] = mapped_column(Integer, default=1)
    streak_days: Mapped[int] = mapped_column(Integer, default=0)
    last_activity_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    total_sessions: Mapped[int] = mapped_column(Integer, default=0)
    total_attempts: Mapped[int] = mapped_column(Integer, default=0)
    total_correct: Mapped[int] = mapped_column(Integer, default=0)
    achievements: Mapped[list] = mapped_column(JSON, default=list)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class RewardEvent(Base):
    __tablename__ = "reward_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_key: Mapped[str] = mapped_column(String(180), unique=True, index=True)
    event_type: Mapped[str] = mapped_column(String(50), index=True)
    xp: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class StudySession(Base):
    __tablename__ = "study_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_type: Mapped[str] = mapped_column(String(40), default="practice")
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_seconds: Mapped[int] = mapped_column(Integer, default=0)
    correct_count: Mapped[int] = mapped_column(Integer, default=0)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    xp_earned: Mapped[int] = mapped_column(Integer, default=0)
    summary: Mapped[str] = mapped_column(Text, default="")


class ErrorDiagnosis(Base):
    __tablename__ = "error_diagnoses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    exercise_id: Mapped[int | None] = mapped_column(ForeignKey("exercises.id", ondelete="SET NULL"), nullable=True, index=True)
    attempt_id: Mapped[int | None] = mapped_column(ForeignKey("exercise_attempts.id", ondelete="SET NULL"), nullable=True, index=True)
    topic: Mapped[str] = mapped_column(String(160), default="Geral", index=True)
    error_type: Mapped[str] = mapped_column(String(50), default="knowledge_gap")
    severity: Mapped[str] = mapped_column(String(20), default="medium")
    explanation: Mapped[str] = mapped_column(Text, default="")
    misconception: Mapped[str] = mapped_column(Text, default="")
    intervention: Mapped[str] = mapped_column(Text, default="")
    practice_prompt: Mapped[str] = mapped_column(Text, default="")
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class LLMProviderCredential(Base):
    __tablename__ = "llm_provider_credentials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(30), unique=True, index=True, nullable=False)
    model: Mapped[str] = mapped_column(String(160), nullable=False)
    api_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class LLMRuntimeSettings(Base):
    __tablename__ = "llm_runtime_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    active_provider: Mapped[str] = mapped_column(String(30), default="ollama", nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class LLMCallLog(Base):
    __tablename__ = "llm_call_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    operation: Mapped[str] = mapped_column(String(50), index=True)
    provider: Mapped[str] = mapped_column(String(30), default="ollama", index=True)
    model: Mapped[str] = mapped_column(String(120), index=True)
    elapsed_ms: Mapped[int] = mapped_column(Integer, default=0)
    prompt_chars: Mapped[int] = mapped_column(Integer, default=0)
    response_chars: Mapped[int] = mapped_column(Integer, default=0)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    generated_tokens: Mapped[int] = mapped_column(Integer, default=0)
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class GenerationEvaluation(Base):
    __tablename__ = "generation_evaluations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    target_type: Mapped[str] = mapped_column(String(30), index=True)
    target_id: Mapped[int] = mapped_column(Integer, index=True)
    scores: Mapped[dict] = mapped_column(JSON, default=dict)
    overall_score: Mapped[float] = mapped_column(Float, default=0.0)
    issues: Mapped[list] = mapped_column(JSON, default=list)
    suggestion: Mapped[str] = mapped_column(Text, default="")
    model: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class TutorMessage(Base):
    __tablename__ = "tutor_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    sources: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)



class GameChallenge(Base):
    """Server-side state for one-time educational mini-game rounds."""

    __tablename__ = "game_challenges"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    game_type: Mapped[str] = mapped_column(String(40), default="matching", index=True)
    topic: Mapped[str] = mapped_column(String(160), default="Geral")
    answer_key: Mapped[dict] = mapped_column(JSON, default=dict)
    pair_count: Mapped[int] = mapped_column(Integer, default=0)
    completed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    xp_earned: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)



class WhatsAppProfile(Base):
    """Perfil único do aluno vinculado ao número pessoal de WhatsApp."""

    __tablename__ = "whatsapp_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    phone_e164: Mapped[str | None] = mapped_column(String(20), unique=True, nullable=True, index=True)
    chat_jid: Mapped[str | None] = mapped_column(String(120), nullable=True)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    language: Mapped[str] = mapped_column(String(10), default="pt-BR")
    timezone: Mapped[str] = mapped_column(String(80), default="America/Sao_Paulo")
    notifications_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    reminders_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    reminder_days: Mapped[list] = mapped_column(JSON, default=lambda: [0, 1, 2, 3, 4, 5, 6])
    reminder_times: Mapped[list] = mapped_column(JSON, default=lambda: ["19:00"])
    daily_goal_minutes: Mapped[int] = mapped_column(Integer, default=30)
    quiet_start: Mapped[str] = mapped_column(String(5), default="22:00")
    quiet_end: Mapped[str] = mapped_column(String(5), default="08:00")
    reminder_template: Mapped[str] = mapped_column(
        Text, default="📚 Hora de estudar! Sua meta hoje é {daily_goal_minutes} min e você tem {due_count} revisões pendentes. Vamos avançar um pouco hoje?"
    )
    selected_document_ids: Mapped[list] = mapped_column(JSON, default=list)
    conversation_state: Mapped[dict] = mapped_column(JSON, default=dict)
    last_interaction_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class WhatsAppLinkChallenge(Base):
    __tablename__ = "whatsapp_link_challenges"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    phone_e164: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class WhatsAppMessage(Base):
    __tablename__ = "whatsapp_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    external_message_id: Mapped[str | None] = mapped_column(String(180), unique=True, nullable=True)
    phone_e164: Mapped[str] = mapped_column(String(20), index=True)
    direction: Mapped[str] = mapped_column(String(12), index=True)
    message_type: Mapped[str] = mapped_column(String(40), default="text")
    text: Mapped[str] = mapped_column(Text, default="")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="received", index=True)
    priority: Mapped[int] = mapped_column(Integer, default=0, index=True)
    processing_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class WhatsAppDocumentJob(Base):
    """Persistent handoff between WhatsApp media receipt and expensive Ollama processing."""
    __tablename__ = "whatsapp_document_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("whatsapp_messages.id"), unique=True, nullable=False)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), nullable=False)
    phone_e164: Mapped[str] = mapped_column(String(20), nullable=False)
    language: Mapped[str] = mapped_column(String(10), default="pt-BR")
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class WhatsAppOutbox(Base):
    __tablename__ = "whatsapp_outbox"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dedupe_key: Mapped[str] = mapped_column(String(180), unique=True, nullable=False)
    phone_e164: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    message_text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
