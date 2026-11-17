from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class DocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    original_filename: str
    file_type: str
    status: str
    rag_status: str = "pending"
    error_message: str | None = None
    extracted_chars: int
    created_at: datetime


class CardRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    document_id: int
    front: str
    back: str
    explanation: str
    difficulty: str
    tags: list[str]
    source_excerpt: str
    review_count: int
    archived: bool = False
    version_number: int = 1


class CardUpdate(BaseModel):
    front: str | None = Field(default=None, min_length=2)
    back: str | None = Field(default=None, min_length=2)
    explanation: str | None = None


class ReviewRequest(BaseModel):
    rating: str = Field(default="good", pattern="^(again|hard|good|easy)$")


class ExerciseGenerateRequest(BaseModel):
    language: str = Field(default="pt-BR", pattern="^(pt-BR|es|en)$")
    count: int = Field(default=10, ge=3, le=20)
    card_ids: list[int] | None = None
    difficulty: str = Field(default="mista", pattern="^(mista|facil|intermediario|dificil)$")
    purpose: str = Field(default="practice", pattern="^(practice|diagnostic|challenge|transfer|exam|remediation)$")
    bloom_level: str = Field(default="mixed", pattern="^(mixed|remember|understand|apply|analyze|evaluate|create)$")
    topic: str = Field(default="", max_length=160)


class ExerciseRead(BaseModel):
    id: int
    question: str
    kind: str
    options: list[str]
    explanation: str | None = None
    difficulty: str
    purpose: str = "practice"
    bloom_level: str = "understand"
    topic: str = ""
    hints: list[str] = Field(default_factory=list)
    objective: str = ""
    remediation_error_id: int | None = None


class ExerciseAnswerRequest(BaseModel):
    language: str = Field(default="pt-BR", pattern="^(pt-BR|es|en)$")
    answer: str = Field(min_length=1, max_length=5000)
    confidence: int = Field(default=3, ge=1, le=5)
    response_time_seconds: float = Field(default=0, ge=0, le=3600)
    session_id: int | None = Field(default=None, ge=1)
    workspace_id: int | None = Field(default=None, ge=1)


class ExerciseWorkspaceStartRequest(BaseModel):
    exercise_ids: list[int] = Field(min_length=1, max_length=20)
    purpose: str = Field(default="practice", pattern="^(practice|diagnostic|challenge|transfer|exam|remediation)$")
    source: str = Field(default="desktop", pattern="^(desktop|whatsapp)$")
    focus_session_id: int | None = Field(default=None, ge=1)


class ExerciseAnswerRead(BaseModel):
    correct: bool
    score: float = 0.0
    feedback: str
    explanation: str
    correct_answer: str
    error_diagnosis: dict | None = None
    reward: dict | None = None
    remediation_progress: dict | None = None


class TutorChatRequest(BaseModel):
    language: str = Field(default="pt-BR", pattern="^(pt-BR|es|en)$")
    message: str = Field(min_length=2, max_length=6000)
    document_ids: list[int] | None = None
    mode: str = Field(default="teacher", pattern="^(teacher|socratic|simple|exam|review)$")


class StudyPlanRequest(BaseModel):
    language: str = Field(default="pt-BR", pattern="^(pt-BR|es|en)$")
    objective: str = Field(default="Dominar os assuntos dos meus materiais", min_length=5, max_length=500)
    days: int = Field(default=7, ge=1, le=30)
    minutes_per_day: int = Field(default=45, ge=10, le=240)


class EvaluationRequest(BaseModel):
    language: str = Field(default="pt-BR", pattern="^(pt-BR|es|en)$")
    card_ids: list[int] | None = None
    limit: int = Field(default=10, ge=1, le=30)


class PlanTaskUpdate(BaseModel):
    completed: bool


class LLMProviderConfigUpdate(BaseModel):
    model: str = Field(min_length=1, max_length=160)
    api_key: str | None = Field(default=None, max_length=4096)
    clear_api_key: bool = False


class LLMActiveProviderUpdate(BaseModel):
    provider: str = Field(pattern="^(ollama|gemini|openai|claude|groq)$")


class LearningTopicRequest(BaseModel):
    language: str = Field(default="pt-BR", pattern="^(pt-BR|es|en)$")
    topic: str = Field(min_length=2, max_length=180)
    document_ids: list[int] | None = None


class DiagnosticRequest(BaseModel):
    language: str = Field(default="pt-BR", pattern="^(pt-BR|es|en)$")
    topic: str = Field(default="", max_length=180)
    count: int = Field(default=8, ge=3, le=20)
    card_ids: list[int] | None = None


class FeynmanRequest(BaseModel):
    language: str = Field(default="pt-BR", pattern="^(pt-BR|es|en)$")
    topic: str = Field(min_length=2, max_length=180)
    explanation: str = Field(min_length=20, max_length=8000)


class SessionStartRequest(BaseModel):
    session_type: str = Field(default="practice", pattern="^(practice|review|lesson|challenge|diagnostic|feynman)$")


class SessionFinishRequest(BaseModel):
    summary: str = Field(default="", max_length=1500)


class ExerciseHintRequest(BaseModel):
    language: str = Field(default="pt-BR", pattern="^(pt-BR|es|en)$")
    level: int = Field(default=1, ge=1, le=3)


class LessonCheckRequest(BaseModel):
    language: str = Field(default="pt-BR", pattern="^(pt-BR|es|en)$")
    topic: str = Field(min_length=2, max_length=180)
    answer: str = Field(min_length=5, max_length=5000)
    confidence: int = Field(default=3, ge=1, le=5)
    document_ids: list[int] | None = None


class MatchGameStartRequest(BaseModel):
    topic: str = Field(default="", max_length=180)
    pair_count: int = Field(default=5, ge=3, le=8)


class MatchGameSubmitRequest(BaseModel):
    matches: dict[str, str] = Field(min_length=3, max_length=8)
