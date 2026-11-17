"""OpenStudy multi-agent coordinator.

Specialists are explicit task owners, not extra LLM calls or separate loaded models.
The same orchestration is used by the HTTP API and WhatsApp (which calls API services).
A lightweight deterministic dispatcher avoids LLM routing latency; quality validation stays
in the task's existing domain services.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session


@dataclass(frozen=True)
class AgentSpec:
    id: str
    title: str
    responsibility: str
    operations: tuple[str, ...]
    instruction: str
    max_output_tokens: int


AGENTS: tuple[AgentSpec, ...] = (
    AgentSpec(
        "biblioteca", "Agente de Biblioteca", "Busca semântica e fundamentação em fontes.",
        ("embedding",), "Não invente fontes e preserve a proveniência dos trechos.", 512,
    ),
    AgentSpec(
        "tutor", "Agente Tutor", "Explicações, tutoria socrática e dicas.",
        ("rag_answer", "exercise_hint", "generate_adaptive_lesson"),
        "Explique com clareza. Use as fontes fornecidas; não invente citações. Na tutoria socrática, não entregue a resposta antecipadamente.", 1400,
    ),
    AgentSpec(
        "avaliador", "Agente Avaliador", "Correção criteriosa e diagnóstico de erros.",
        ("grade_answer", "diagnose_learning_error", "feynman_evaluation", "evaluate_card"),
        "Avalie a resposta de forma justa, sem usar critérios não apresentados. Respeite equivalências semânticas.", 1200,
    ),
    AgentSpec(
        "exercicios", "Agente de Exercícios", "Elaboração de questões fundamentadas.",
        ("generate_exercises",),
        "Elabore questões resolúveis apenas com o material fornecido; valide alternativas, gabarito e justificativa.", 3200,
    ),
    AgentSpec(
        "documentos", "Agente de Documentos", "Transformação dos materiais em flashcards.",
        ("generate_cards", "extract_learning_concepts"),
        "Extraia conceitos sem inventar conteúdo. Em lotes numerados, cubra cada trecho e mantenha exemplos verificáveis.", 2200,
    ),
    AgentSpec(
        "planejador", "Agente Planejador", "Planos adaptativos e análises de progresso.",
        ("study_agent_plan", "learning_insights"),
        "Priorize necessidades observadas, prazo realista e recomendações concretas.", 1600,
    ),
)

_BY_OPERATION = {operation: spec for spec in AGENTS for operation in spec.operations}


def agent_for(operation: str) -> AgentSpec | None:
    return _BY_OPERATION.get(operation)


def agent_catalog() -> list[dict[str, Any]]:
    return [
        {"id": spec.id, "name": spec.title, "responsibility": spec.responsibility,
         "operations": list(spec.operations), "model_strategy": "shared_active_provider"}
        for spec in AGENTS
    ]


def tag_operation(operation: str) -> str:
    agent = agent_for(operation)
    return f"agent:{agent.id}:{operation}" if agent else operation


def specialize_prompt(operation: str, system_prompt: str) -> str:
    agent = agent_for(operation)
    if agent is None:
        return system_prompt
    return f"{system_prompt}\n\nPAPEL ESPECIALIZADO ({agent.title}): {agent.instruction}"


class AgentOrchestrator:
    """Small, typed orchestration of existing tools; shared by both channels.

    Each tool is called at most once per normal task. There is no expensive LLM router,
    debate, or unbounded agent loop. The same validation in OllamaClient remains active.
    """

    def __init__(self, db: Session):
        self.db = db

    def tutor(self, question: str, *, document_ids: list[int] | None = None,
              history: list[dict[str, str]] | None = None,
              language: str = "pt-BR", mode: str = "teacher",
              client_factory: Callable | None = None) -> tuple[str, list[dict]]:
        from app.services.ollama_client import OllamaClient
        from app.services.retrieval import retrieve_context

        contexts = retrieve_context(self.db, question, document_ids)
        answer = (client_factory or OllamaClient)(db=self.db).answer_with_context(
            question, contexts, history=history, language=language, mode=mode,
        )
        return answer, contexts

    def exercises(self, cards: list[dict[str, Any]], count: int, difficulty: str,
                  *, language: str = "pt-BR", purpose: str = "practice",
                  bloom_level: str = "mixed", topic: str = "",
                  client_factory: Callable | None = None) -> list[dict[str, Any]]:
        from app.services.ollama_client import OllamaClient

        return (client_factory or OllamaClient)(db=self.db).generate_exercises(
            cards, count, difficulty, language=language, purpose=purpose,
            bloom_level=bloom_level, topic=topic,
        )


    def diagnose(self, question: str, expected: str, submitted: str,
                 explanation: str, source: str, *, language: str = "pt-BR",
                 client_factory: Callable | None = None) -> dict[str, Any]:
        """Assessment specialist: validates learner answers without rerouting other tasks."""
        from app.services.ollama_client import OllamaClient
        return (client_factory or OllamaClient)(db=self.db).diagnose_learning_error(
            question, expected, submitted, explanation, source, language=language,
        )

    def plan(self, context: dict[str, Any], *, language: str = "pt-BR",
             client_factory: Callable | None = None) -> dict[str, Any]:
        """Planning specialist: learns from shared progress and prior errors."""
        from app.services.ollama_client import OllamaClient
        return (client_factory or OllamaClient)(db=self.db).generate_study_plan(
            context, language=language,
        )

    def document_cards(self, client: Any, chunks: list[str], filename: str,
                       *, language: str = "pt-BR", enabled: bool = True) -> list[dict]:
        return generate_document_cards(client, chunks, filename, language=language, enabled=enabled)


def group_document_chunks(
    chunks: list[str], *, max_chars: int = 14000, max_items: int = 2,
) -> list[list[str]]:
    """Batch consecutive chunks, respecting a conservative prompt budget.

    Large individual chunks are never truncated; no document text is discarded.
    """
    groups: list[list[str]] = []
    current: list[str] = []
    current_size = 0
    for chunk in chunks:
        size = len(chunk) + 70  # per-part delimiter/headroom
        if current and (current_size + size > max_chars or len(current) >= max_items):
            groups.append(current)
            current = []
            current_size = 0
        current.append(chunk)
        current_size += size
    if current:
        groups.append(current)
    return groups


def generate_document_cards(client: Any, chunks: list[str], filename: str,
                            language: str = "pt-BR", *, enabled: bool = True) -> list[dict]:
    """Document specialist: fewer LLM calls, with an individual fallback on empty batches.

    The source-specific provenance stays available in the prompt. Existing card validation
    and learning-history reconciliation are retained by OllamaClient / study_service.
    """
    batches = group_document_chunks(chunks) if enabled else [[chunk] for chunk in chunks]
    cards: list[dict] = []
    for group in batches:
        if len(group) == 1:
            cards.extend(client.generate_cards(group[0], filename, language=language))
            continue
        content = "\n\n".join(
            f"--- TRECHO {index}/{len(group)} ---\n{chunk}\n--- FIM TRECHO {index} ---"
            for index, chunk in enumerate(group, 1)
        )
        result = client.generate_cards(content, filename, language=language)
        if result:
            cards.extend(result)
        else:
            # Best-effort fallback preserves material coverage on batch failures.
            for chunk in group:
                cards.extend(client.generate_cards(chunk, filename, language=language))
    return cards
