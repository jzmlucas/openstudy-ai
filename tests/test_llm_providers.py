from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import LLMRuntimeSettings
from app.services.llm_providers import _extract_response_text, _request_payload
from app.services import llm_settings
from app.services import ollama_client as ollama_client_module
from app.services.llm_settings import (
    get_provider_credential,
    public_provider_settings,
    save_provider_credential,
    set_active_provider,
)
from app.services.ollama_client import OllamaClient


MESSAGES = [
    {"role": "system", "content": "Você é um tutor."},
    {"role": "user", "content": "Explique RAG."},
    {"role": "assistant", "content": "RAG recupera contexto."},
    {"role": "user", "content": "Dê um exemplo."},
]


def test_openai_and_groq_use_openai_compatible_chat_payloads():
    endpoint, headers, payload = _request_payload("openai", "gpt-example", MESSAGES, True)
    assert endpoint == "https://api.openai.com/v1/chat/completions"
    assert headers["Authorization"] == "Bearer {api_key}"
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["messages"][-1]["content"] == "Dê um exemplo."

    endpoint, _, payload = _request_payload("groq", "llama-example", MESSAGES, True)
    assert endpoint == "https://api.groq.com/openai/v1/chat/completions"
    assert "response_format" not in payload


def test_claude_moves_system_prompt_outside_messages():
    endpoint, headers, payload = _request_payload("claude", "claude-example", MESSAGES, True)
    assert endpoint == "https://api.anthropic.com/v1/messages"
    assert headers["anthropic-version"] == "2026-10-21"
    assert payload["system"] == "Você é um tutor."
    assert all(item["role"] != "system" for item in payload["messages"])
    assert payload["max_tokens"] > 0


def test_gemini_converts_assistant_role_to_model_role():
    endpoint, headers, payload = _request_payload("gemini", "gemini-example", MESSAGES, True)
    assert endpoint.endswith("/models/gemini-example:generateContent")
    assert headers["x-goog-api-key"] == "{api_key}"
    assert payload["systemInstruction"]["parts"][0]["text"] == "Você é um tutor."
    assert [item["role"] for item in payload["contents"]] == ["user", "model", "user"]
    assert payload["generationConfig"]["responseMimeType"] == "application/json"


def test_provider_response_text_extraction():
    assert _extract_response_text("openai", {
        "choices": [{"message": {"content": " resposta "}}]
    }) == "resposta"
    assert _extract_response_text("claude", {
        "content": [{"type": "text", "text": "parte 1"}, {"type": "text", "text": "parte 2"}]
    }) == "parte 1\nparte 2"
    assert _extract_response_text("gemini", {
        "candidates": [{"content": {"parts": [{"text": "Gemini respondeu"}]}}]
    }) == "Gemini respondeu"


def test_byok_credentials_are_encrypted_and_not_returned_in_public_settings():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        saved = save_provider_credential(
            db, "openai", "gpt-test-model", api_key="sk-test-secret-value"
        )
        assert saved["configured"] is True
        assert saved["api_key"] == "sk-test-secret-value"

        public = public_provider_settings(db)
        openai = next(item for item in public["providers"] if item["id"] == "openai")
        assert openai["configured"] is True
        assert "api_key" not in openai
        assert "sk-test-secret-value" not in str(public)

        set_active_provider(db, "openai")
        client = OllamaClient(db=db)
        assert client.provider == "openai"
        assert client.model == "gpt-test-model"
        assert client.api_key == "sk-test-secret-value"
        assert db.get(LLMRuntimeSettings, 1).active_provider == "openai"


def test_active_remote_provider_uses_unified_chat_adapter(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        save_provider_credential(db, "gemini", "gemini-test-model", api_key="gemini-test-secret")
        set_active_provider(db, "gemini")
        calls = {}

        def fake_chat_completion(**kwargs):
            calls.update(kwargs)
            return "resposta simulada do Gemini"

        monkeypatch.setattr(ollama_client_module, "chat_completion", fake_chat_completion)
        client = OllamaClient(db=db)
        answer = client._chat_text("system prompt", "user prompt", operation="unit_test")

        assert answer == "resposta simulada do Gemini"
        assert calls["provider"] == "gemini"
        assert calls["model"] == "gemini-test-model"
        assert calls["api_key"] == "gemini-test-secret"


def test_decryption_does_not_silently_create_a_replacement_key(monkeypatch, tmp_path):
    monkeypatch.setattr(llm_settings, "Path", lambda _path: tmp_path / ".llm_encryption_key")
    monkeypatch.setattr(llm_settings.settings, "llm_encryption_key", None)
    llm_settings._fernet.cache_clear()
    try:
        import pytest
        with pytest.raises(RuntimeError, match="não foi encontrada"):
            llm_settings.decrypt_api_key("encrypted-value")
        assert not (tmp_path / ".llm_encryption_key").exists()
    finally:
        llm_settings._fernet.cache_clear()
