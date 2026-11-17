import json
import logging
import re
import time
from collections import OrderedDict
from functools import lru_cache
from threading import RLock
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.prompts import (
    CARD_SYSTEM_PROMPT,
    ERROR_DIAGNOSIS_SYSTEM_PROMPT,
    EVALUATION_SYSTEM_PROMPT,
    EXERCISE_SYSTEM_PROMPT,
    FEYNMAN_SYSTEM_PROMPT,
    GRADING_SYSTEM_PROMPT,
    INSIGHTS_SYSTEM_PROMPT,
    LEARNING_ENGINE_SYSTEM_PROMPT,
    SOCRATIC_TUTOR_SYSTEM_PROMPT,
    STUDY_AGENT_SYSTEM_PROMPT,
    TUTOR_SYSTEM_PROMPT,
)
from app.services.agents import agent_for, specialize_prompt, tag_operation
from app.services.language import language_instruction, normalize_language
from app.services.llm_providers import (
    ProviderRequestError,
    chat_completion,
    list_provider_models,
)
from app.services.llm_settings import (
    PROVIDER_CATALOG,
    get_active_provider,
    get_provider_credential,
)
from app.services.observability import record_llm_call


@lru_cache(maxsize=4)
def _http_client(timeout_seconds: int) -> httpx.Client:
    """One connection pool per process, reused by web requests and workers."""
    return httpx.Client(
        timeout=httpx.Timeout(timeout_seconds, connect=10.0),
        limits=httpx.Limits(max_connections=8, max_keepalive_connections=4),
    )


_embedding_cache: OrderedDict[tuple[str, str, str], tuple[float, list[float]]] = OrderedDict()
_embedding_cache_lock = RLock()


def _cached_embedding(key: tuple[str, str, str]) -> list[float] | None:
    with _embedding_cache_lock:
        cached = _embedding_cache.get(key)
        if not cached:
            return None
        if time.monotonic() - cached[0] > 600:
            del _embedding_cache[key]
            return None
        _embedding_cache.move_to_end(key)
        return list(cached[1])


def _remember_embedding(key: tuple[str, str, str], vector: list[float]) -> None:
    with _embedding_cache_lock:
        _embedding_cache[key] = (time.monotonic(), list(vector))
        _embedding_cache.move_to_end(key)
        while len(_embedding_cache) > 256:
            _embedding_cache.popitem(last=False)


log = logging.getLogger(__name__)


class OllamaError(RuntimeError):
    """Erro de compatibilidade mantido para as rotas e serviços existentes."""


def parse_json_payload(raw: str) -> dict[str, Any]:
    content = raw.strip()
    content = re.sub(r"^```(?:json)?\s*", "", content, flags=re.IGNORECASE)
    content = re.sub(r"\s*```$", "", content)
    decoder = json.JSONDecoder()
    for index, character in enumerate(content):
        if character in "{[":
            try:
                value, _ = decoder.raw_decode(content[index:])
                if isinstance(value, list):
                    return {"items": value}
                if isinstance(value, dict):
                    return value
            except json.JSONDecodeError:
                continue
    raise ValueError("O modelo não retornou um JSON válido.")


class OllamaClient:
    """Cliente unificado de IA. O nome histórico é mantido para compatibilidade.

    A geração usa o provedor ativo (Ollama, Gemini, OpenAI, Claude ou Groq).
    Embeddings continuam independentes e locais via Ollama, com fallback lexical no RAG.
    """

    def __init__(self, db: Session | None = None, provider_override: str | None = None) -> None:
        self.base_url = settings.ollama_base_url.rstrip("/")
        self.embedding_model = settings.ollama_embedding_model
        self.timeout = httpx.Timeout(settings.ollama_timeout_seconds, connect=10.0)
        owned_db = None
        if db is None:
            from app.database import SessionLocal
            owned_db = SessionLocal()
            db = owned_db
        try:
            provider = provider_override or get_active_provider(db)
            config = get_provider_credential(db, provider)
        finally:
            if owned_db is not None:
                owned_db.close()
        self.provider = provider
        self.provider_label = PROVIDER_CATALOG[provider]["label"]
        self.model = config["model"]
        self.api_key = config["api_key"]
        self.configured = bool(config["configured"]) and bool(self.model)
        self.model_ref = f"{self.provider}:{self.model}"

    def _post(self, endpoint: str, payload: dict, operation: str, model: str) -> dict:
        """Requisições exclusivas ao Ollama local (embeddings e health)."""
        started = time.perf_counter()
        prompt_chars = len(json.dumps(payload.get("messages", payload.get("input", "")), ensure_ascii=False))
        try:
            response = _http_client(settings.ollama_timeout_seconds).post(
                f"{self.base_url}{endpoint}", json=payload,
            )
            response.raise_for_status()
            data = response.json()
            elapsed = int((time.perf_counter() - started) * 1000)
            message = data.get("message", {}).get("content", "")
            response_chars = len(message) if isinstance(message, str) else len(str(data))
            record_llm_call(
                operation=tag_operation(operation), provider="ollama", model=model, elapsed_ms=elapsed,
                prompt_chars=prompt_chars, response_chars=response_chars,
                prompt_tokens=data.get("prompt_eval_count", 0) or 0,
                generated_tokens=data.get("eval_count", 0) or 0,
            )
            return data
        except httpx.ConnectError as exc:
            elapsed = int((time.perf_counter() - started) * 1000)
            record_llm_call(
                operation=tag_operation(operation), provider="ollama", model=model, elapsed_ms=elapsed,
                prompt_chars=prompt_chars, success=False, error_message="Conexão recusada pelo Ollama",
            )
            raise OllamaError(
                "Não foi possível conectar ao Ollama. Inicie o serviço local e tente novamente."
            ) from exc
        except httpx.HTTPStatusError as exc:
            elapsed = int((time.perf_counter() - started) * 1000)
            detail = exc.response.text[:800]
            record_llm_call(
                operation=tag_operation(operation), provider="ollama", model=model, elapsed_ms=elapsed,
                prompt_chars=prompt_chars, success=False, error_message=detail,
            )
            raise OllamaError(f"Ollama respondeu com erro HTTP {exc.response.status_code}: {detail}") from exc
        except (httpx.HTTPError, ValueError) as exc:
            elapsed = int((time.perf_counter() - started) * 1000)
            record_llm_call(
                operation=tag_operation(operation), provider="ollama", model=model, elapsed_ms=elapsed,
                prompt_chars=prompt_chars, success=False, error_message=str(exc),
            )
            raise OllamaError(f"Falha na comunicação com o Ollama: {exc}") from exc

    def _local_chat(self, model: str, messages: list[dict[str, str]],
                    operation: str, json_mode: bool) -> str:
        """Executa uma única geração Ollama, mantendo modo JSON e prompts originais."""
        payload = {
            "model": model,
            "stream": False,
            "messages": messages,
            "options": {
                "temperature": 0.15 if json_mode else 0.25,
                "num_ctx": settings.ollama_num_ctx,
                "num_predict": (agent_for(operation).max_output_tokens if agent_for(operation)
                                else (2048 if json_mode else 1400)),
            },
            "keep_alive": settings.ollama_keep_alive,
        }
        if json_mode:
            payload["format"] = "json"
        data = self._post("/api/chat", payload, operation, model)
        try:
            result = str(data["message"]["content"]).strip()
        except (KeyError, TypeError) as exc:
            raise OllamaError("O modelo local retornou uma resposta inesperada.") from exc
        if not result:
            raise OllamaError("O modelo local retornou uma resposta vazia.")
        return result

    def _local_fallback_available(self, model: str) -> tuple[bool, str]:
        """Confere disponibilidade real sem baixar modelos e sem usar aliases arbitrários."""
        try:
            response = _http_client(5).get(f"{self.base_url}/api/tags")
            response.raise_for_status()
            models = response.json().get("models", [])
            names = {str(item.get(key, "")) for item in models if isinstance(item, dict)
                     for key in ("name", "model")}
            expected = model if ":" in model else f"{model}:latest"
            if expected in names or model in names:
                return True, "available"
            return False, "missing"
        except (httpx.HTTPError, ValueError, TypeError, AttributeError):
            return False, "offline"

    def _chat(self, messages: list[dict[str, str]], operation: str, json_mode: bool = False) -> str:
        if not self.configured:
            if self.provider == "ollama":
                raise OllamaError("O modelo local não está configurado. Confira as configurações do Ollama.")
            raise OllamaError(
                f"Configure sua chave de API e o modelo de {self.provider_label} em Configurar IA."
            )

        if self.provider == "ollama":
            return self._local_chat(self.model, messages, operation, json_mode)

        try:
            return chat_completion(
                provider=self.provider,
                model=self.model,
                api_key=self.api_key or "",
                messages=messages,
                operation=tag_operation(operation),
                json_mode=json_mode,
                timeout_seconds=settings.remote_llm_timeout_seconds,
            )
        except ProviderRequestError as exc:
            # Problemas de chave, autorização, modelo inexistente e payload inválido
            # não são indisponibilidade transitória. Não mascarar erros de configuração.
            if not exc.retryable or not settings.ollama_fallback_enabled:
                raise OllamaError(str(exc)) from exc

            local_model = (settings.ollama_fallback_model or settings.ollama_model).strip()
            primary_name = f"{self.provider_label} ({self.model})"
            if exc.reason == "rate_limited":
                summary = f"O modelo {primary_name} atingiu o limite de solicitações no momento."
            elif exc.reason == "network":
                summary = f"Não consegui acessar o modelo {primary_name} agora."
            else:
                summary = f"O modelo {primary_name} está com alta demanda no momento."
            guidance = "Você pode escolher outro modelo em Configurar IA ou aguardar um momento e tentar novamente."

            if not local_model:
                raise OllamaError(f"{summary} Não há um modelo local de reserva configurado. {guidance}") from exc
            available, reason = self._local_fallback_available(local_model)
            if not available:
                explanation = (
                    f"O modelo local de reserva ({local_model}) não está instalado no Ollama."
                    if reason == "missing" else
                    "O Ollama local não está acessível para assumir o atendimento."
                )
                raise OllamaError(f"{summary} {explanation} {guidance}") from exc

            log.warning("Provedor %s temporariamente indisponível (%s); usando Ollama %s para %s.",
                        self.provider, exc.reason, local_model, operation)
            try:
                # A mesma solicitação é reenviada apenas uma vez ao modelo local.
                # O provedor escolhido pelo usuário permanece inalterado no banco.
                return self._local_chat(local_model, messages, operation, json_mode)
            except OllamaError as local_exc:
                log.warning("Falha no Ollama de reserva (%s) para %s: %s", local_model, operation, local_exc)
                raise OllamaError(
                    f"{summary} Também não consegui concluir a resposta com o modelo local de reserva ({local_model}). "
                    f"{guidance}"
                ) from local_exc

    def _chat_text(
        self,
        system_prompt: str,
        user_prompt: str | None = None,
        history: list[dict[str, str]] | None = None,
        operation: str = "chat",
        language: str = "pt-BR",
    ) -> str:
        localized_prompt = f"{specialize_prompt(operation, system_prompt)}\n\n{language_instruction(language)}"
        messages = [{"role": "system", "content": localized_prompt}]
        if history:
            messages.extend(history[-8:])
        if user_prompt is not None:
            messages.append({"role": "user", "content": user_prompt})
        return self._chat(messages, operation, json_mode=False)

    def _chat_json(
        self, system_prompt: str, user_prompt: str, operation: str, language: str = "pt-BR"
    ) -> dict[str, Any]:
        localized_prompt = f"{specialize_prompt(operation, system_prompt)}\n\n{language_instruction(language)}"
        messages = [
            {"role": "system", "content": localized_prompt},
            {"role": "user", "content": user_prompt},
        ]
        try:
            return parse_json_payload(self._chat(messages, operation, json_mode=True))
        except (TypeError, ValueError) as exc:
            raise OllamaError(f"Resposta JSON inválida do modelo: {exc}") from exc

    def ollama_health(self) -> dict[str, Any]:
        """Estado do Ollama local, usado principalmente para embeddings."""
        try:
            with httpx.Client(timeout=httpx.Timeout(5.0, connect=2.0)) as client:
                response = client.get(f"{self.base_url}/api/tags")
                response.raise_for_status()
                data = response.json()
            models = [item.get("name", "") for item in data.get("models", [])]
            configured_llm_model = settings.ollama_model
            installed = any(
                name == configured_llm_model or name.split(":")[0] == configured_llm_model.split(":")[0]
                for name in models
            )
            embedding_installed = any(
                name == self.embedding_model or name.split(":")[0] == self.embedding_model.split(":")[0]
                for name in models
            )
            return {
                "online": True,
                "model": configured_llm_model,
                "model_installed": installed,
                "embedding_model": self.embedding_model,
                "embedding_model_installed": embedding_installed,
                "available_models": models,
                "base_url": self.base_url,
            }
        except (httpx.HTTPError, ValueError):
            return {
                "online": False,
                "model": settings.ollama_model,
                "model_installed": False,
                "embedding_model": self.embedding_model,
                "embedding_model_installed": False,
                "available_models": [],
                "base_url": self.base_url,
            }

    def health(self) -> dict[str, Any]:
        if self.provider == "ollama":
            local = self.ollama_health()
            configured = local["online"] and local["model_installed"]
            return {
                "provider": self.provider,
                "provider_label": self.provider_label,
                "model": self.model,
                "configured": configured,
                "online": local["online"],
                "model_installed": local["model_installed"],
            }
        return {
            "provider": self.provider,
            "provider_label": self.provider_label,
            "model": self.model,
            "configured": self.configured,
            "online": self.configured,
            "model_installed": self.configured,
        }

    def test_connection(self) -> str:
        return self._chat_text(
            "Você é um teste de conectividade. Responda exatamente com uma frase curta.",
            "Responda apenas: Conexão funcionando.",
            operation="provider_test",
        )

    def available_models(self) -> list[str]:
        try:
            return list_provider_models(
                provider=self.provider,
                api_key=self.api_key,
                ollama_base_url=self.base_url,
                timeout_seconds=20,
            )
        except ProviderRequestError as exc:
            raise OllamaError(str(exc)) from exc

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embeddings seguem locais via Ollama, independente do LLM selecionado."""
        if not texts:
            return []
        cache_key = (self.base_url, self.embedding_model, texts[0]) if len(texts) == 1 and len(texts[0]) <= 2000 else None
        if cache_key is not None:
            cached = _cached_embedding(cache_key)
            if cached is not None:
                return [cached]
        payload = {"model": self.embedding_model, "input": texts, "keep_alive": settings.ollama_keep_alive}
        data = self._post("/api/embed", payload, "embedding", self.embedding_model)
        embeddings = data.get("embeddings")
        if not isinstance(embeddings, list) or len(embeddings) != len(texts):
            raise OllamaError("Ollama não retornou a quantidade esperada de embeddings.")
        try:
            vectors = [[float(value) for value in vector] for vector in embeddings]
            if cache_key is not None:
                _remember_embedding(cache_key, vectors[0])
            return vectors
        except (TypeError, ValueError) as exc:
            raise OllamaError("Ollama retornou embeddings em formato inválido.") from exc

    def generate_cards(
        self, text: str, source_name: str, language: str = "pt-BR"
    ) -> list[dict[str, Any]]:
        prompt = f"""
Nome do material: {source_name}

Crie flashcards a partir do trecho abaixo. Caso haja marcadores TRECHO 1/2 e TRECHO 2/2,
crie flashcards de ambos os trechos (3 por trecho, se houver conteúdo suficiente),
sem ignorar nenhum deles. Cada flashcard deve ter uma fonte fiel ao texto fornecido.
Retorne:
{{
  "cards": [
    {{
      "front": "Pergunta clara",
      "back": "Resposta correta",
      "explanation": "Explicação complementar",
      "difficulty": "facil|intermediario|dificil",
      "tags": ["assunto"],
      "source_excerpt": "Trecho que fundamenta o card"
    }}
  ]
}}

TRECHO DO MATERIAL:
<material>
{text}
</material>
"""
        data = self._chat_json(CARD_SYSTEM_PROMPT, prompt, "generate_cards", language=language)
        result = []
        for item in data.get("cards", []):
            if not isinstance(item, dict):
                continue
            front, back = str(item.get("front", "")).strip(), str(item.get("back", "")).strip()
            if len(front) < 5 or len(back) < 2:
                continue
            difficulty = str(item.get("difficulty", "intermediario")).lower()
            if difficulty not in {"facil", "intermediario", "dificil"}:
                difficulty = "intermediario"
            tags = item.get("tags", [])
            if isinstance(tags, str):
                tags = [tags]
            if not isinstance(tags, list):
                tags = []
            result.append({
                "front": front[:3000], "back": back[:5000],
                "explanation": str(item.get("explanation", ""))[:5000],
                "difficulty": difficulty,
                "tags": [str(tag)[:60] for tag in tags[:8]],
                "source_excerpt": str(item.get("source_excerpt", ""))[:1500],
            })
        return result[:7]

    def generate_exercises(
        self, cards: list[dict[str, Any]], count: int, difficulty: str,
        language: str = "pt-BR", purpose: str = "practice",
        bloom_level: str = "mixed", topic: str = "",
    ) -> list[dict[str, Any]]:
        material = [
            {key: card.get(key) for key in ("id", "front", "back", "explanation", "difficulty", "tags", "source_excerpt")}
            for card in cards
        ]
        prompt = f"""
Crie {count} exercícios com dificuldade "{difficulty}" usando os flashcards abaixo.
Objetivo da sessão: {purpose}. Nível cognitivo: {bloom_level}. Assunto prioritário: {topic or "variado"}.
Distribua os níveis de Bloom (remember, understand, apply, analyze, evaluate, create) de acordo com o objetivo.
Distribua os tipos entre multiple_choice, true_false e short_answer.
Múltipla escolha: exatamente 4 alternativas distintas e plausíveis; correct_answer deve ser uma delas.
Verdadeiro/falso: options ["Verdadeiro", "Falso"] e correct_answer idêntico a uma delas.
Resposta curta: options [] e resposta esperada em correct_answer.

Formato:
{{
 "exercises": [
  {{
   "question": "Enunciado",
   "kind": "multiple_choice|true_false|short_answer",
   "options": [],
   "correct_answer": "Resposta",
   "explanation": "Explicação",
   "difficulty": "facil|intermediario|dificil",
   "bloom_level": "remember|understand|apply|analyze|evaluate|create",
   "topic": "assunto",
   "hints": ["dica 1", "dica 2", "dica 3"],
   "objective": "o que esta questão verifica",
   "source_excerpt": "trecho de apoio",
   "card_ids": [1]
  }}
 ]
}}

FLASHCARDS:
{json.dumps(material, ensure_ascii=False)}
"""
        data = self._chat_json(EXERCISE_SYSTEM_PROMPT, prompt, "generate_exercises", language=language)
        result = []
        for item in data.get("exercises", []):
            if not isinstance(item, dict):
                continue
            question = str(item.get("question", "")).strip()
            kind = str(item.get("kind", "")).strip()
            correct = str(item.get("correct_answer", "")).strip()
            options = item.get("options", [])
            if kind not in {"multiple_choice", "true_false", "short_answer"} or not question or not correct:
                continue
            if not isinstance(options, list):
                options = []
            options = [str(option).strip() for option in options if str(option).strip()]
            if kind == "multiple_choice" and (len(options) != 4 or len(set(options)) != 4 or correct not in options):
                continue
            if kind == "true_false":
                locale = normalize_language(language)
                labels = {
                    "pt-BR": ("Verdadeiro", "Falso"),
                    "es": ("Verdadero", "Falso"),
                    "en": ("True", "False"),
                }[locale]
                true_aliases = {"verdadeiro", "verdadero", "true", "sí", "si"}
                false_aliases = {"falso", "false"}
                normalized_correct = correct.casefold().strip()
                if normalized_correct in true_aliases:
                    correct = labels[0]
                elif normalized_correct in false_aliases:
                    correct = labels[1]
                else:
                    continue
                options = list(labels)
            ids = item.get("card_ids", [])
            if not isinstance(ids, list):
                ids = []
            allowed_ids = {int(card["id"]) for card in cards if str(card.get("id", "")).isdigit()}
            clean_ids = [int(value) for value in ids if str(value).isdigit() and int(value) in allowed_ids]
            hints = item.get("hints", [])
            if not isinstance(hints, list):
                hints = []
            expected_key = re.sub(r"\s+", " ", correct).strip().casefold()
            safe_hints = []
            for raw_hint in hints[:3]:
                hint_text = str(raw_hint)[:500]
                hint_key = re.sub(r"\s+", " ", hint_text).strip().casefold()
                if expected_key and len(expected_key) >= 8 and expected_key in hint_key:
                    continue
                safe_hints.append(hint_text)
            bloom = str(item.get("bloom_level", "understand")).lower()
            if bloom not in {"remember", "understand", "apply", "analyze", "evaluate", "create"}:
                bloom = "understand"
            result.append({
                "question": question[:3000], "kind": kind, "options": options[:4],
                "correct_answer": correct[:2000],
                "explanation": str(item.get("explanation", ""))[:4000],
                "difficulty": str(item.get("difficulty", "intermediario")),
                "card_ids": clean_ids,
                "purpose": purpose[:30],
                "bloom_level": bloom,
                "topic": str(item.get("topic", topic))[:160],
                "hints": safe_hints,
                "source_excerpt": str(item.get("source_excerpt", ""))[:3000],
                "objective": str(item.get("objective", ""))[:1000],
            })
        return result[:count]

    def grade_short_answer(
        self, question: str, expected: str, submitted: str, language: str = "pt-BR"
    ) -> dict[str, Any]:
        prompt = f"""
Questão: {question}
Resposta esperada: {expected}
Resposta do estudante: {submitted}
Avalie entendimento e equivalência semântica. Retorne:
{{"is_correct": true, "feedback": "comentário objetivo"}}
"""
        data = self._chat_json(GRADING_SYSTEM_PROMPT, prompt, "grade_answer", language=language)
        raw_correct = data.get("is_correct", False)
        is_correct = raw_correct is True or (
            isinstance(raw_correct, str) and raw_correct.strip().casefold() in {"true", "yes", "sim", "verdadero"}
        )
        return {
            "is_correct": is_correct,
            "feedback": str(data.get("feedback", ""))[:1500],
        }

    def answer_with_context(
        self, question: str, contexts: list[dict], history: list[dict[str, str]] | None = None,
        language: str = "pt-BR", mode: str = "teacher",
    ) -> str:
        if not contexts:
            context_text = "Nenhum trecho relevante foi encontrado nos documentos."
        else:
            context_text = "\n\n".join(
                f"FONTE [{item['citation']}]\nDocumento: {item['filename']}\n"
                f"Trecho:\n{item['content'][:settings.agent_max_source_chars]}"
                for item in contexts
            )
        prompt = f"""
MATERIAIS RECUPERADOS:
<context>
{context_text}
</context>

PERGUNTA DO ESTUDANTE:
{question}

Responda com explicação didática. Cite as fontes recuperadas usando exatamente os identificadores
fornecidos. Se os trechos não forem suficientes, deixe isso explícito.
"""
        system_prompt = SOCRATIC_TUTOR_SYSTEM_PROMPT if mode == "socratic" else TUTOR_SYSTEM_PROMPT
        if mode == "simple":
            system_prompt += " Explique como para um iniciante, com frases curtas e analogias simples."
        elif mode == "exam":
            system_prompt += " Atue como examinador: faça uma pergunta desafiadora e não revele a resposta imediatamente."
        elif mode == "review":
            system_prompt += " Conduza uma revisão ativa: peça recuperação sem consulta e corrija lacunas."
        return self._chat_text(system_prompt, prompt, history, "rag_answer", language=language)

    def diagnose_learning_error(
        self, question: str, expected: str, submitted: str, explanation: str = "",
        source_excerpt: str = "", language: str = "pt-BR",
    ) -> dict[str, Any]:
        prompt = f"""
Questão: {question}
Resposta esperada: {expected}
Resposta do aluno: {submitted}
Explicação da questão: {explanation}
Trecho-fonte: {source_excerpt[:5000]}

Retorne JSON com:
{{
 "score": 0.0,
 "is_correct": false,
 "error_type": "knowledge_gap|misconception|misinterpretation|application_error|careless_error|incomplete_answer|retrieval_failure",
 "severity": "low|medium|high",
 "explanation": "o que ocorreu",
 "misconception": "ideia equivocada provável ou vazio",
 "intervention": "passo concreto para corrigir",
 "practice_prompt": "uma nova atividade curta para tentar novamente",
 "missing_concepts": ["conceito ausente"]
}}
A nota vai de 0 a 1. Considere respostas parcialmente corretas.
"""
        data = self._chat_json(ERROR_DIAGNOSIS_SYSTEM_PROMPT, prompt, "diagnose_learning_error", language=language)
        error_types = {"knowledge_gap", "misconception", "misinterpretation", "application_error", "careless_error", "incomplete_answer", "retrieval_failure"}
        try:
            score = max(0.0, min(1.0, float(data.get("score", 0))))
        except (TypeError, ValueError):
            score = 0.0
        kind = str(data.get("error_type", "knowledge_gap"))
        if kind not in error_types:
            kind = "knowledge_gap"
        raw_correct = data.get("is_correct", score >= 0.7)
        if isinstance(raw_correct, bool):
            is_correct = raw_correct
        elif isinstance(raw_correct, str):
            is_correct = raw_correct.strip().casefold() in {"true", "yes", "sim", "sí", "si", "verdadero"}
        else:
            is_correct = score >= 0.7
        return {
            "score": round(score, 2), "is_correct": is_correct,
            "error_type": kind,
            "severity": str(data.get("severity", "medium")) if data.get("severity") in {"low", "medium", "high"} else "medium",
            "explanation": str(data.get("explanation", "Revise o conceito e tente novamente."))[:1800],
            "misconception": str(data.get("misconception", ""))[:1200],
            "intervention": str(data.get("intervention", "Revise a explicação e resolva uma nova questão."))[:1800],
            "practice_prompt": str(data.get("practice_prompt", "Explique o conceito com suas próprias palavras."))[:1200],
            "missing_concepts": [str(x)[:160] for x in data.get("missing_concepts", [])[:8]] if isinstance(data.get("missing_concepts", []), list) else [],
        }

    def generate_hint(
        self, question: str, expected: str, explanation: str, source_excerpt: str,
        level: int, language: str = "pt-BR",
    ) -> str:
        stages = {
            1: "Dê uma pista leve que ajude a lembrar o conceito, sem revelar a resposta.",
            2: "Dê uma pista intermediária, apontando uma relação ou princípio necessário.",
            3: "Dê uma pista forte com parte do raciocínio, mas ainda deixe o aluno concluir.",
        }
        prompt = f"""
Questão: {question}
Resposta esperada (não revele diretamente): {expected}
Explicação: {explanation}
Fonte: {source_excerpt[:2500]}
Pedido: {stages.get(level, stages[1])}
Retorne somente a dica, em até 80 palavras.
"""
        return self._chat_text(LEARNING_ENGINE_SYSTEM_PROMPT, prompt, operation="exercise_hint", language=language)[:1000]

    def generate_learning_concepts(self, cards: list[dict[str, Any]], language: str = "pt-BR") -> list[dict[str, Any]]:
        prompt = f"""
Com base nos cartões, identifique de 3 a 20 conceitos de aprendizagem distintos. Não invente.
Para cada conceito retorne title, definition, simple_explanation, technical_explanation, example,
application, common_mistakes (lista), prerequisites (lista de títulos), learning_objectives (lista),
source_excerpt e card_ids.
Cartões: {json.dumps(cards[:120], ensure_ascii=False)}
JSON: {{"concepts": [{{"title":"...","definition":"...","simple_explanation":"...","technical_explanation":"...","example":"...","application":"...","common_mistakes":[],"prerequisites":[],"learning_objectives":[],"source_excerpt":"...","card_ids":[]}}]}}
"""
        data = self._chat_json(LEARNING_ENGINE_SYSTEM_PROMPT, prompt, "extract_learning_concepts", language=language)
        output = []
        valid_ids = {int(card["id"]) for card in cards if str(card.get("id", "")).isdigit()}
        for item in data.get("concepts", []) if isinstance(data.get("concepts", []), list) else []:
            if not isinstance(item, dict) or not str(item.get("title", "")).strip():
                continue
            output.append({
                "title": str(item.get("title", ""))[:180],
                "definition": str(item.get("definition", ""))[:2000],
                "simple_explanation": str(item.get("simple_explanation", ""))[:2500],
                "technical_explanation": str(item.get("technical_explanation", ""))[:3000],
                "example": str(item.get("example", ""))[:2000],
                "application": str(item.get("application", ""))[:2000],
                "common_mistakes": [str(x)[:500] for x in item.get("common_mistakes", [])[:8]] if isinstance(item.get("common_mistakes", []), list) else [],
                "prerequisites": [str(x)[:180] for x in item.get("prerequisites", [])[:8]] if isinstance(item.get("prerequisites", []), list) else [],
                "learning_objectives": [str(x)[:300] for x in item.get("learning_objectives", [])[:8]] if isinstance(item.get("learning_objectives", []), list) else [],
                "source_excerpt": str(item.get("source_excerpt", ""))[:3000],
                "card_ids": [int(x) for x in item.get("card_ids", []) if str(x).isdigit() and int(x) in valid_ids] if isinstance(item.get("card_ids", []), list) else [],
            })
        return output[:20]

    def generate_lesson(
        self, topic: str, contexts: list[dict[str, Any]], cards: list[dict[str, Any]],
        mastery: float = 0.0, language: str = "pt-BR",
        recent_errors: list[dict[str, Any]] | None = None,
        prerequisites: list[str] | None = None,
    ) -> dict[str, Any]:
        if mastery < 0.4:
            teaching_level = "iniciante: retome pré-requisitos, use analogia concreta e evite jargão sem definição"
        elif mastery < 0.75:
            teaching_level = "intermediário: conecte conceitos, compare ideias próximas e trabalhe aplicação"
        else:
            teaching_level = "avançado: priorize análise, exceções, transferência e problemas novos"
        prompt = f"""
Crie uma microaula adaptativa sobre: {topic}.
Nível estimado do aluno: {mastery:.0%} (estimativa, não verdade absoluta).
Estratégia de ensino: {teaching_level}.
Pré-requisitos identificados: {json.dumps(prerequisites or [], ensure_ascii=False)}
Erros recentes do aluno (use para ajustar a explicação, sem constranger): {json.dumps(recent_errors or [], ensure_ascii=False)}
Fontes recuperadas: {json.dumps(contexts[:6], ensure_ascii=False)}
Flashcards relacionados: {json.dumps(cards[:12], ensure_ascii=False)}
Estruture JSON com: title, learning_goal, simple_explanation, analogy, technical_explanation,
worked_example, real_world_application, common_mistakes (lista), prerequisites (lista),
check_question, check_answer, source_notes (lista de citações já fornecidas).
A explicação simples deve vir antes da técnica. Use um exemplo concreto e um contraexemplo quando
as fontes permitirem. Se o aluno já domina o básico, não repita uma aula infantil: avance para
análise e aplicação. Se há erro recorrente, explique a diferença que o aluno confundiu e inclua
uma dica para evitar o mesmo erro. Inclua uma pergunta de recuperação ativa em check_question e
uma resposta de referência completa em check_answer. Não invente fatos não apoiados nas fontes.
"""
        data = self._chat_json(LEARNING_ENGINE_SYSTEM_PROMPT, prompt, "generate_adaptive_lesson", language=language)
        return {
            "title": str(data.get("title", topic))[:180],
            "learning_goal": str(data.get("learning_goal", "Compreender e aplicar o conceito."))[:800],
            "simple_explanation": str(data.get("simple_explanation", ""))[:4000],
            "analogy": str(data.get("analogy", ""))[:2000],
            "technical_explanation": str(data.get("technical_explanation", ""))[:5000],
            "worked_example": str(data.get("worked_example", ""))[:3500],
            "real_world_application": str(data.get("real_world_application", ""))[:2500],
            "common_mistakes": data.get("common_mistakes", []) if isinstance(data.get("common_mistakes", []), list) else [],
            "prerequisites": data.get("prerequisites", []) if isinstance(data.get("prerequisites", []), list) else [],
            "check_question": str(data.get("check_question", "Explique o conceito com suas palavras."))[:1200],
            "check_answer": str(data.get("check_answer", ""))[:2000],
            "source_notes": data.get("source_notes", []) if isinstance(data.get("source_notes", []), list) else [],
        }

    def evaluate_feynman(
        self, topic: str, explanation: str, contexts: list[dict[str, Any]], language: str = "pt-BR"
    ) -> dict[str, Any]:
        prompt = f"""
Conceito: {topic}
Explicação do estudante: {explanation}
Fontes: {json.dumps(contexts[:5], ensure_ascii=False)}
Retorne JSON: {{"scores":{{"accuracy":0,"clarity":0,"completeness":0,"simplicity":0}},
"overall_score":0,"strengths":[],"missing_concepts":[],"misconceptions":[],
"priority_improvement":"...","retry_prompt":"...","feedback":"..."}}
Notas inteiras de 0 a 5. Seja justo com explicações em linguagem própria.
"""
        data = self._chat_json(FEYNMAN_SYSTEM_PROMPT, prompt, "feynman_evaluation", language=language)
        raw = data.get("scores", {}) if isinstance(data.get("scores", {}), dict) else {}
        scores = {}
        for key in ("accuracy", "clarity", "completeness", "simplicity"):
            try:
                scores[key] = max(0, min(5, int(raw.get(key, 0))))
            except (TypeError, ValueError):
                scores[key] = 0
        overall = round(sum(scores.values()) / 20, 2)
        return {
            "scores": scores, "overall_score": overall,
            "strengths": data.get("strengths", []) if isinstance(data.get("strengths", []), list) else [],
            "missing_concepts": data.get("missing_concepts", []) if isinstance(data.get("missing_concepts", []), list) else [],
            "misconceptions": data.get("misconceptions", []) if isinstance(data.get("misconceptions", []), list) else [],
            "priority_improvement": str(data.get("priority_improvement", "Adicione um exemplo concreto."))[:1200],
            "retry_prompt": str(data.get("retry_prompt", "Tente explicar novamente com suas próprias palavras."))[:1200],
            "feedback": str(data.get("feedback", "Continue praticando a explicação."))[:2000],
        }

    def evaluate_card(self, card: dict[str, Any], language: str = "pt-BR") -> dict[str, Any]:
        prompt = f"""
Avalie este flashcard.
FLASHCARD:
{json.dumps(card, ensure_ascii=False)}
Retorne:
{{
 "scores": {{"accuracy": 1, "clarity": 1, "atomicity": 1, "groundedness": 1}},
 "issues": ["problema observado"],
 "suggestion": "melhoria concreta"
}}
As notas devem ser números inteiros de 1 a 5.
"""
        data = self._chat_json(EVALUATION_SYSTEM_PROMPT, prompt, "evaluate_card", language=language)
        raw_scores = data.get("scores", {})
        score_names = ("accuracy", "clarity", "atomicity", "groundedness")
        scores = {}
        for name in score_names:
            try:
                scores[name] = max(1, min(5, int(raw_scores.get(name, 1))))
            except (TypeError, ValueError, AttributeError):
                scores[name] = 1
        issues = data.get("issues", [])
        if not isinstance(issues, list):
            issues = [str(issues)]
        return {
            "scores": scores,
            "overall_score": round(sum(scores.values()) / len(scores), 2),
            "issues": [str(issue)[:400] for issue in issues[:8]],
            "suggestion": str(data.get("suggestion", ""))[:1500],
        }

    def generate_study_plan(
        self, context: dict[str, Any], language: str = "pt-BR"
    ) -> dict[str, Any]:
        prompt = f"""
Crie um plano de estudo personalizado com base nestes dados:
{json.dumps(context, ensure_ascii=False)}
Retorne:
{{
 "title": "Nome do plano",
 "summary": "Resumo da estratégia",
 "tasks": [
  {{
   "day_number": 1,
   "title": "Título da tarefa",
   "description": "Instrução observável",
   "topic": "Assunto",
   "estimated_minutes": 25,
   "card_ids": [1]
  }}
 ]
}}
Gere tarefas distribuídas nos dias solicitados. Não invente card_ids.
Priorize due_card_ids no início do plano, sem repetir o mesmo card desnecessariamente.
Use recent_errors para criar tarefas de reforço específicas e comece por pré-requisitos quando forem frágeis.
Alterne recuperação ativa, comparação, explicação Feynman e aplicação; inclua pequenas verificações de retenção.
Respeite minutes_per_day: a soma das tarefas de cada dia deve ficar dentro desse limite.
Use recuperação ativa, prática intercalada e aplicação; evite recomendar apenas releitura passiva.
Cada tarefa deve ter um resultado observável (explicar, comparar, resolver, aplicar ou revisar).
"""
        data = self._chat_json(STUDY_AGENT_SYSTEM_PROMPT, prompt, "study_agent_plan", language=language)
        tasks = data.get("tasks", [])
        if not isinstance(tasks, list):
            tasks = []
        clean_tasks = []
        allowed_ids = {int(value) for value in context.get("available_card_ids", [])}
        for item in tasks:
            if not isinstance(item, dict):
                continue
            try:
                day = max(1, min(int(item.get("day_number", 1)), int(context["days"])))
                minutes = max(5, min(int(item.get("estimated_minutes", 20)), 240))
            except (TypeError, ValueError):
                continue
            ids = item.get("card_ids", [])
            if not isinstance(ids, list):
                ids = []
            clean_tasks.append({
                "day_number": day,
                "title": str(item.get("title", "Sessão de estudo"))[:255],
                "description": str(item.get("description", ""))[:2000],
                "topic": str(item.get("topic", ""))[:160],
                "estimated_minutes": minutes,
                "card_ids": [int(value) for value in ids if str(value).isdigit() and int(value) in allowed_ids],
            })
        return {
            "title": str(data.get("title", "Plano personalizado"))[:255],
            "summary": str(data.get("summary", ""))[:3000],
            "tasks": clean_tasks,
        }

    def generate_insights(self, analytics: dict[str, Any], language: str = "pt-BR") -> dict[str, Any]:
        prompt = f"""
Analise estes indicadores reais de aprendizagem:
{json.dumps(analytics, ensure_ascii=False)}
Retorne:
{{
 "summary": "Resumo honesto",
 "strengths": ["ponto forte observado"],
 "risks": ["lacuna observada"],
 "recommendations": ["ação prática"],
 "confidence_note": "limitação dos dados, se houver"
}}
"""
        data = self._chat_json(INSIGHTS_SYSTEM_PROMPT, prompt, "learning_insights", language=language)
        return {
            "summary": str(data.get("summary", "")),
            "strengths": data.get("strengths", []) if isinstance(data.get("strengths", []), list) else [],
            "risks": data.get("risks", []) if isinstance(data.get("risks", []), list) else [],
            "recommendations": data.get("recommendations", []) if isinstance(data.get("recommendations", []), list) else [],
            "confidence_note": str(data.get("confidence_note", "")),
        }
