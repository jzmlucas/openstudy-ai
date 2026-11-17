"""Adaptadores HTTP para os provedores remotos de modelos de linguagem."""

from __future__ import annotations

import copy
import json
import re
import time
from typing import Any
from urllib.parse import quote

import httpx

from app.services.observability import record_llm_call

PROVIDER_URLS = {
    "openai": "https://api.openai.com/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta",
    "claude": "https://api.anthropic.com/v1",
    "groq": "https://api.groq.com/openai/v1",
}


class ProviderRequestError(RuntimeError):
    """Erro de provedor com causa estruturada para decidir fallback sem analisar textos."""

    def __init__(self, message: str, *, retryable: bool = False,
                 reason: str = "other", status_code: int | None = None) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.reason = reason
        self.status_code = status_code


def _safe_detail(response: httpx.Response, api_key: str | None) -> str:
    try:
        payload = response.json()
        if isinstance(payload, dict):
            error = payload.get("error", payload)
            if isinstance(error, dict):
                detail = str(error.get("message") or error.get("detail") or error)
            else:
                detail = str(error)
        else:
            detail = response.text
    except ValueError:
        detail = response.text
    detail = re.sub(r"\s+", " ", detail).strip()[:700]
    if api_key:
        detail = detail.replace(api_key, "[chave ocultada]")
    return detail or "O provedor não informou detalhes adicionais."


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        pieces = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                pieces.append(item["text"])
        return "\n".join(pieces).strip()
    return ""


def _normalize_openai_messages(messages: list[dict[str, str]]) -> list[dict[str, str]]:
    return [
        {"role": str(item.get("role", "user")), "content": str(item.get("content", ""))}
        for item in messages
        if item.get("role") in {"system", "user", "assistant"}
    ]


def _request_payload(
    provider: str,
    model: str,
    messages: list[dict[str, str]],
    json_mode: bool,
) -> tuple[str, dict[str, str], dict[str, Any]]:
    """Monta endpoint, cabeçalhos e corpo para cada API oficial."""
    base = PROVIDER_URLS[provider]

    if provider in {"openai", "groq"}:
        headers = {"Authorization": "Bearer {api_key}", "Content-Type": "application/json"}
        payload: dict[str, Any] = {
            "model": model,
            "messages": _normalize_openai_messages(messages),
        }
        if json_mode and provider == "openai":
            payload["response_format"] = {"type": "json_object"}
        return f"{base}/chat/completions", headers, payload

    if provider == "claude":
        system_parts = [item["content"] for item in messages if item.get("role") == "system"]
        conversation = [
            {"role": item["role"], "content": item["content"]}
            for item in messages
            if item.get("role") in {"user", "assistant"}
        ]
        payload = {
            "model": model,
            "max_tokens": 4096,
            "messages": conversation,
        }
        if system_parts:
            payload["system"] = "\n\n".join(system_parts)
        return (
            f"{base}/messages",
            {
                "Authorization": "Bearer {api_key}",
                "anthropic-version": "2026-10-21",
                "Content-Type": "application/json",
            },
            payload,
        )

    if provider == "gemini":
        system_parts = [item["content"] for item in messages if item.get("role") == "system"]
        contents = []
        for item in messages:
            role = item.get("role")
            if role not in {"user", "assistant"}:
                continue
            contents.append({
                "role": "model" if role == "assistant" else "user",
                "parts": [{"text": item.get("content", "")}],
            })
        payload = {
            "contents": contents,
            "generationConfig": {"temperature": 0.25, "maxOutputTokens": 4096},
        }
        if system_parts:
            payload["systemInstruction"] = {"parts": [{"text": "\n\n".join(system_parts)}]}
        if json_mode:
            payload["generationConfig"]["responseMimeType"] = "application/json"
        return (
            f"{base}/models/{quote(model, safe='')}:generateContent",
            {"x-goog-api-key": "{api_key}", "Content-Type": "application/json"},
            payload,
        )

    raise ProviderRequestError(f"Provedor remoto não suportado: {provider}")


def _extract_response_text(provider: str, data: dict[str, Any]) -> str:
    try:
        if provider in {"openai", "groq"}:
            return _content_to_text(data["choices"][0]["message"]["content"])
        if provider == "claude":
            return _content_to_text(data.get("content", []))
        if provider == "gemini":
            parts = data["candidates"][0]["content"]["parts"]
            return _content_to_text(parts)
    except (KeyError, IndexError, TypeError) as exc:
        raise ProviderRequestError("O provedor retornou uma resposta em formato inesperado.") from exc
    raise ProviderRequestError("Não foi possível interpretar a resposta do provedor.")


def chat_completion(
    *,
    provider: str,
    model: str,
    api_key: str,
    messages: list[dict[str, str]],
    operation: str,
    json_mode: bool = False,
    timeout_seconds: int = 300,
) -> str:
    if provider not in PROVIDER_URLS:
        raise ProviderRequestError(f"Provedor remoto não suportado: {provider}")
    if not api_key:
        raise ProviderRequestError(f"Cadastre sua chave de API do provedor {provider} nas configurações de IA.")

    endpoint, header_template, payload = _request_payload(provider, model, messages, json_mode)
    headers = {name: value.replace("{api_key}", api_key) for name, value in header_template.items()}
    started = time.perf_counter()
    prompt_chars = len(json.dumps(messages, ensure_ascii=False))
    try:
        with httpx.Client(timeout=httpx.Timeout(timeout_seconds, connect=15.0)) as client:
            response = client.post(endpoint, headers=headers, json=payload)
            if json_mode and response.status_code in {400, 422}:
                fallback_payload = copy.deepcopy(payload)
                if provider == "openai":
                    fallback_payload.pop("response_format", None)
                elif provider == "gemini":
                    fallback_payload.get("generationConfig", {}).pop("responseMimeType", None)
                if fallback_payload != payload:
                    response = client.post(endpoint, headers=headers, json=fallback_payload)
            response.raise_for_status()
            data = response.json()
        result = _extract_response_text(provider, data)
        if not result:
            raise ProviderRequestError("O provedor retornou uma resposta vazia.")
        elapsed = int((time.perf_counter() - started) * 1000)
        usage = data.get("usage", {}) if isinstance(data, dict) else {}
        prompt_tokens = usage.get("prompt_tokens", usage.get("input_tokens", 0)) or 0
        generated_tokens = usage.get("completion_tokens", usage.get("output_tokens", 0)) or 0
        record_llm_call(
            operation=operation,
            provider=provider,
            model=model,
            elapsed_ms=elapsed,
            prompt_chars=prompt_chars,
            response_chars=len(result),
            prompt_tokens=prompt_tokens,
            generated_tokens=generated_tokens,
        )
        return result
    except httpx.HTTPStatusError as exc:
        elapsed = int((time.perf_counter() - started) * 1000)
        detail = _safe_detail(exc.response, api_key)
        record_llm_call(
            operation=operation, provider=provider, model=model, elapsed_ms=elapsed,
            prompt_chars=prompt_chars, success=False, error_message=detail,
        )
        status = exc.response.status_code
        if status in {401, 403}:
            message = "Chave de API inválida ou sem permissão. Confira a chave cadastrada no provedor."
        elif status == 429:
            message = "O modelo escolhido está recebendo muitas solicitações no momento. Tente novamente em instantes."
        elif status == 404:
            message = "O modelo escolhido não foi encontrado. Selecione outro modelo nas configurações de IA."
        elif status in {500, 502, 503, 504, 529}:
            message = "O modelo escolhido está temporariamente indisponível ou com alta demanda."
        else:
            message = "Não foi possível obter uma resposta do modelo escolhido. Tente novamente."
        # 429 também pode significar cota/créditos esgotados: o fallback local é seguro,
        # mas a mensagem sem fallback não deve garantir que seja apenas alta demanda.
        reason = "overloaded" if status in {500, 502, 503, 504, 529} else "rate_limited" if status == 429 else "other"
        raise ProviderRequestError(
            message, retryable=status in {429, 500, 502, 503, 504, 529},
            reason=reason, status_code=status,
        ) from exc
    except (httpx.HTTPError, ValueError) as exc:
        elapsed = int((time.perf_counter() - started) * 1000)
        detail = str(exc).replace(api_key, "[chave ocultada]") if api_key else str(exc)
        record_llm_call(
            operation=operation, provider=provider, model=model, elapsed_ms=elapsed,
            prompt_chars=prompt_chars, success=False, error_message=detail[:700],
        )
        raise ProviderRequestError(
            "O serviço de IA está demorando para responder ou está temporariamente inacessível.",
            retryable=True, reason="network",
        ) from exc
    except ProviderRequestError as exc:
        elapsed = int((time.perf_counter() - started) * 1000)
        record_llm_call(
            operation=operation, provider=provider, model=model, elapsed_ms=elapsed,
            prompt_chars=prompt_chars, success=False, error_message=str(exc)[:700],
        )
        raise


def list_provider_models(
    *, provider: str, api_key: str | None, ollama_base_url: str, timeout_seconds: int = 20
) -> list[str]:
    """Consulta o catálogo real de modelos da conta do usuário."""
    if provider == "ollama":
        url = f"{ollama_base_url.rstrip('/')}/api/tags"
        headers: dict[str, str] = {}
    else:
        if not api_key:
            raise ProviderRequestError("Cadastre uma chave de API antes de consultar os modelos.")
        base = PROVIDER_URLS.get(provider)
        if provider == "gemini":
            url = f"{base}/models"
            headers = {"x-goog-api-key": api_key}
        elif provider == "claude":
            url = f"{base}/models"
            headers = {"Authorization": f"Bearer {api_key}", "anthropic-version": "2026-10-21"}
        else:
            url = f"{base}/models"
            headers = {"Authorization": f"Bearer {api_key}"}
    try:
        with httpx.Client(timeout=httpx.Timeout(timeout_seconds, connect=8.0)) as client:
            response = client.get(url, headers=headers)
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as exc:
        detail = _safe_detail(exc.response, api_key)
        if exc.response.status_code in {401, 403}:
            raise ProviderRequestError("Chave de API inválida ou sem permissão.") from exc
        raise ProviderRequestError(f"Não foi possível consultar modelos ({exc.response.status_code}): {detail}") from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise ProviderRequestError(f"Falha ao consultar modelos de {provider}: {exc}") from exc

    if provider == "ollama":
        models = [str(item.get("name", "")) for item in data.get("models", [])]
    elif provider == "gemini":
        models = []
        for item in data.get("models", []):
            methods = item.get("supportedGenerationMethods", [])
            name = str(item.get("name", "")).removeprefix("models/")
            if name and (not methods or "generateContent" in methods):
                models.append(name)
    else:
        models = [str(item.get("id", "")) for item in data.get("data", [])]
    return sorted({model for model in models if model})[:250]
