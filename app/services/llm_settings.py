"""Persistência e criptografia das configurações BYOK dos provedores de IA."""

from __future__ import annotations

from functools import lru_cache
import os
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.orm import Session

from app.config import settings
from app.models import LLMProviderCredential, LLMRuntimeSettings


PROVIDER_CATALOG: dict[str, dict[str, Any]] = {
    "ollama": {
        "label": "Ollama",
        "description": "Modelos executados localmente no seu computador.",
        "key_required": False,
        "suggested_model": None,
        "key_url": "https://ollama.com/download",
    },
    "gemini": {
        "label": "Google Gemini",
        "description": "Modelos Gemini pela API do Google AI Studio.",
        "key_required": True,
        "suggested_model": "gemini-3.8-flash",
        "key_url": "https://aistudio.google.com/apikey",
    },
    "openai": {
        "label": "OpenAI · ChatGPT",
        "description": "Modelos GPT pela API da OpenAI Platform; cobrança separada da assinatura ChatGPT.",
        "key_required": True,
        "suggested_model": "gpt-6-astra",
        "key_url": "https://platform.openai.com/api-keys",
    },
    "claude": {
        "label": "Anthropic · Claude",
        "description": "Modelos Claude pela API da Anthropic.",
        "key_required": True,
        "suggested_model": "claude-opus-5-5",
        "key_url": "https://platform.claude.com/settings/keys",
    },
    "groq": {
        "label": "Groq",
        "description": "Inferência rápida com modelos disponíveis no GroqCloud.",
        "key_required": True,
        "suggested_model": "openai/gpt-oss-120b",
        "key_url": "https://console.groq.com/keys",
    },
}


def _default_model(provider: str) -> str:
    if provider == "ollama":
        return settings.ollama_model
    return str(PROVIDER_CATALOG[provider]["suggested_model"])


@lru_cache(maxsize=2)
def _fernet(create_if_missing: bool = True) -> Fernet:
    """Carrega uma chave configurada ou cria uma chave local persistente com permissão restrita."""
    if settings.llm_encryption_key:
        try:
            return Fernet(settings.llm_encryption_key.encode("utf-8"))
        except (ValueError, TypeError) as exc:
            raise RuntimeError(
                "APP_ENCRYPTION_KEY inválida. Gere uma chave Fernet com "
                "`uv run python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"`."
            ) from exc

    key_path = Path("data/.llm_encryption_key")
    key_path.parent.mkdir(parents=True, exist_ok=True)
    if not key_path.exists() and not create_if_missing:
        raise RuntimeError(
            "A chave local data/.llm_encryption_key não foi encontrada. "
            "Restaure-a do backup ou configure APP_ENCRYPTION_KEY para recuperar as credenciais salvas."
        )
    try:
        key = Fernet.generate_key()
        descriptor = None
        try:
            descriptor = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.write(descriptor, key)
        finally:
            if descriptor is not None:
                os.close(descriptor)
    except FileExistsError:
        key = key_path.read_bytes().strip()
    try:
        key_path.chmod(0o600)
    except OSError:
        pass
    try:
        return Fernet(key)
    except (ValueError, TypeError) as exc:
        raise RuntimeError(
            "A chave local de criptografia está inválida. Não a apague se já houver chaves de API salvas."
        ) from exc


def encrypt_api_key(api_key: str) -> str:
    return _fernet(create_if_missing=True).encrypt(api_key.strip().encode("utf-8")).decode("ascii")


def decrypt_api_key(encrypted: str | None) -> str | None:
    if not encrypted:
        return None
    try:
        return _fernet(create_if_missing=False).decrypt(encrypted.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise RuntimeError(
            "Não foi possível descriptografar uma chave salva. Verifique se APP_ENCRYPTION_KEY "
            "ou data/.llm_encryption_key corresponde à chave usada quando ela foi cadastrada."
        ) from exc


def get_provider_credential(db: Session, provider: str) -> dict[str, Any]:
    if provider not in PROVIDER_CATALOG:
        raise ValueError(f"Provedor não suportado: {provider}")
    row = db.query(LLMProviderCredential).filter_by(provider=provider).first()
    encrypted = row.api_key_encrypted if row else None
    api_key = decrypt_api_key(encrypted)
    return {
        "provider": provider,
        "model": (row.model if row and row.model else _default_model(provider)),
        "api_key": api_key,
        "configured": provider == "ollama" or bool(api_key),
        "updated_at": row.updated_at if row else None,
    }


def get_active_provider(db: Session) -> str:
    runtime = db.get(LLMRuntimeSettings, 1)
    return runtime.active_provider if runtime else "ollama"


def set_active_provider(db: Session, provider: str) -> None:
    if provider not in PROVIDER_CATALOG:
        raise ValueError(f"Provedor não suportado: {provider}")
    runtime = db.get(LLMRuntimeSettings, 1)
    if runtime is None:
        runtime = LLMRuntimeSettings(id=1, active_provider=provider)
        db.add(runtime)
    else:
        runtime.active_provider = provider
    db.commit()


def save_provider_credential(
    db: Session,
    provider: str,
    model: str,
    api_key: str | None = None,
    clear_api_key: bool = False,
) -> dict[str, Any]:
    if provider not in PROVIDER_CATALOG:
        raise ValueError(f"Provedor não suportado: {provider}")
    row = db.query(LLMProviderCredential).filter_by(provider=provider).first()
    if row is None:
        row = LLMProviderCredential(provider=provider, model=_default_model(provider))
        db.add(row)
    row.model = model.strip() or _default_model(provider)
    if clear_api_key:
        row.api_key_encrypted = None
    elif api_key and api_key.strip():
        row.api_key_encrypted = encrypt_api_key(api_key)
    db.commit()
    return get_provider_credential(db, provider)


def clear_provider_key(db: Session, provider: str) -> None:
    row = db.query(LLMProviderCredential).filter_by(provider=provider).first()
    if row:
        row.api_key_encrypted = None
        db.commit()


def public_provider_settings(db: Session) -> dict[str, Any]:
    active_provider = get_active_provider(db)
    providers = []
    for provider, metadata in PROVIDER_CATALOG.items():
        config = get_provider_credential(db, provider)
        providers.append({
            "id": provider,
            "label": metadata["label"],
            "description": metadata["description"],
            "key_required": metadata["key_required"],
            "key_url": metadata["key_url"],
            "model": config["model"],
            "configured": config["configured"],
            "active": provider == active_provider,
        })
    active = get_provider_credential(db, active_provider)
    return {
        "active_provider": active_provider,
        "active_model": active["model"],
        "providers": providers,
    }
