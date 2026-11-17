"""Regressões: fallback para Ollama e mensagens seguras durante saturação de APIs."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from app.services import ollama_client as module
from app.services.llm_providers import ProviderRequestError, chat_completion
from app.services.ollama_client import OllamaClient, OllamaError
from app.services.user_errors import friendly_document_error


def test_provider_http_503_has_structured_retryable_error(monkeypatch):
    class MockClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def post(self, url, **kwargs):
            return httpx.Response(
                503, request=httpx.Request("POST", url),
                json={"error": {"message": "This model is currently experiencing high demand"}},
            )

    monkeypatch.setattr("app.services.llm_providers.httpx.Client", MockClient)
    with pytest.raises(ProviderRequestError) as caught:
        chat_completion(provider="gemini", model="gemini-sample", api_key="test-key",
                        messages=[{"role": "user", "content": "oi"}], operation="test")
    assert caught.value.retryable is True
    assert caught.value.reason == "overloaded"
    assert caught.value.status_code == 503
    assert "HTTP" not in str(caught.value)
    assert "high demand" not in str(caught.value)


def fake_client(monkeypatch):
    client = OllamaClient.__new__(OllamaClient)
    client.provider = "gemini"
    client.provider_label = "Google Gemini"
    client.model = "gemini-test"
    client.api_key = "test-key"
    client.configured = True
    client.base_url = "http://localhost:11434"
    monkeypatch.setattr(module.settings, "ollama_fallback_enabled", True)
    monkeypatch.setattr(module.settings, "ollama_fallback_model", "")
    monkeypatch.setattr(module.settings, "ollama_model", "qwen2.5:7b")
    return client


def simulated_503(**_kwargs):
    raise ProviderRequestError("Modelo sobrecarregado", retryable=True,
                               reason="overloaded", status_code=503)


def test_503_routes_to_local_ollama_without_changing_active_provider(monkeypatch):
    client = fake_client(monkeypatch)
    monkeypatch.setattr(module, "chat_completion", simulated_503)
    monkeypatch.setattr(client, "_local_fallback_available", lambda model: (True, "available"))
    calls = []

    def local(model, messages, operation, json_mode):
        calls.append((model, messages, operation, json_mode))
        return '{"cards":[{"front":"Q?","back":"A"}]}'

    monkeypatch.setattr(client, "_local_chat", local)
    messages = [{"role": "system", "content": "Only JSON"}, {"role": "user", "content": "Cards"}]
    result = client._chat(messages, "generate_cards", json_mode=True)
    assert result.startswith('{"cards"')
    assert calls == [("qwen2.5:7b", messages, "generate_cards", True)]
    assert client.provider == "gemini"
    assert client.model == "gemini-test"


@pytest.mark.parametrize("reason, expected", [
    ("missing", "não está instalado"),
    ("offline", "não está acessível"),
])
def test_no_fallback_model_gives_helpful_message(monkeypatch, reason, expected):
    client = fake_client(monkeypatch)
    monkeypatch.setattr(module, "chat_completion", simulated_503)
    monkeypatch.setattr(client, "_local_fallback_available", lambda model: (False, reason))
    with pytest.raises(OllamaError) as caught:
        client._chat([{"role": "user", "content": "Ajude"}], "rag_answer")
    assert "alta demanda" in str(caught.value)
    assert expected in str(caught.value)
    assert "Configurar IA" in str(caught.value)
    assert "HTTP" not in str(caught.value)


def test_fallback_itself_fails_and_error_is_user_friendly(monkeypatch):
    client = fake_client(monkeypatch)
    monkeypatch.setattr(module, "chat_completion", simulated_503)
    monkeypatch.setattr(client, "_local_fallback_available", lambda model: (True, "available"))

    def local(*_args):
        raise OllamaError("[internal http 500] raw technical output")

    monkeypatch.setattr(client, "_local_chat", local)
    with pytest.raises(OllamaError) as caught:
        client._chat([{"role": "user", "content": "Ajude"}], "rag_answer")
    assert "local de reserva" in str(caught.value)
    assert "Configurar IA" in str(caught.value)
    assert "raw technical" not in str(caught.value)


def test_nontransient_failure_does_not_switch_models(monkeypatch):
    client = fake_client(monkeypatch)

    def auth_error(**_kwargs):
        raise ProviderRequestError("Chave de API inválida ou sem permissão.")

    monkeypatch.setattr(module, "chat_completion", auth_error)

    def unexpected_local(*_args):
        pytest.fail("Não usar fallback para credenciais inválidas")

    monkeypatch.setattr(client, "_local_chat", unexpected_local)
    with pytest.raises(OllamaError, match="Chave de API inválida"):
        client._chat([{"role": "user", "content": "oi"}], "rag_answer")


def test_friendly_document_messages_hide_raw_errors():
    raw = "gemini retornou HTTP 503: This model is currently experiencing high demand."
    msg = friendly_document_error(raw)
    assert "alta demanda" in msg
    assert "Configurar IA" in msg
    assert "HTTP" not in msg
    assert "gemini retornou" not in msg
    assert "outro modelo" in msg
    assert "arquivo" in friendly_document_error("O arquivo original não foi encontrado no armazenamento.")


def test_only_installed_model_is_accepted(monkeypatch):
    client = fake_client(monkeypatch)
    class DummyResponse:
        def raise_for_status(self):
            pass
        def json(self):
            return {"models": [{"name": "qwen2.5:3b"}, {"name": "llama3.2:latest"}]}

    monkeypatch.setattr(module, "_http_client", lambda timeout: SimpleNamespace(get=lambda uri: DummyResponse()))
    assert client._local_fallback_available("qwen2.5:7b") == (False, "missing")
    assert client._local_fallback_available("llama3.2") == (True, "available")
