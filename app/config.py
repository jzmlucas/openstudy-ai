from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "OpenStudy AI"
    database_url: str = "sqlite:///./data/openstudy.db"
    uploads_dir: Path = Path("data/uploads")
    max_upload_mb: int = 30
    max_document_chars: int = 300_000
    chunk_size: int = 6_000
    chunk_overlap: int = 450

    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen2.5:7b"
    ollama_embedding_model: str = "nomic-embed-text"
    ollama_timeout_seconds: int = 300
    ollama_keep_alive: str = Field(default="10m", validation_alias="OLLAMA_KEEP_ALIVE")
    # O fallback usa exclusivamente um modelo Ollama já instalado, sem downloads automáticos.
    ollama_fallback_enabled: bool = Field(default=True, validation_alias="OLLAMA_FALLBACK_ENABLED")
    ollama_fallback_model: str = Field(default="", validation_alias="OLLAMA_FALLBACK_MODEL")
    remote_llm_timeout_seconds: int = Field(default=60, ge=10, le=300, validation_alias="REMOTE_LLM_TIMEOUT_SECONDS")
    ollama_num_ctx: int = Field(default=8192, ge=2048, le=32768, validation_alias="OLLAMA_NUM_CTX")
    agent_batch_documents: bool = Field(default=True, validation_alias="AGENT_BATCH_DOCUMENTS")
    agent_max_source_chars: int = Field(default=2800, ge=500, le=6000, validation_alias="AGENT_MAX_SOURCE_CHARS")
    whatsapp_poll_interval_seconds: float = Field(default=0.4, ge=0.2, le=5, validation_alias="WHATSAPP_POLL_INTERVAL_SECONDS")
    llm_encryption_key: str | None = Field(default=None, validation_alias="APP_ENCRYPTION_KEY")

    # Admin protected by Basic Auth. The default generates a strong persistent password
    # at data/.openstudy-admin-password; keeping it outside the distributed ZIP.
    openstudy_auth_mode: str = Field(default="basic", pattern="^(basic|off)$", validation_alias="OPENSTUDY_AUTH_MODE")
    openstudy_admin_user: str = Field(default="admin", validation_alias="OPENSTUDY_ADMIN_USER")
    openstudy_admin_password: str = Field(default="", validation_alias="OPENSTUDY_ADMIN_PASSWORD")
    openstudy_admin_secret_file: str = Field(default="", validation_alias="OPENSTUDY_ADMIN_SECRET_FILE")
    openstudy_allowed_origins: str = Field(default="", validation_alias="OPENSTUDY_ALLOWED_ORIGINS")
    openstudy_service_api_token: str = Field(default="", validation_alias="OPENSTUDY_SERVICE_API_TOKEN")

    rag_top_k: int = 5
    rag_min_similarity: float = 0.12
    target_retention: float = 0.90

    evolution_api_url: str = Field(default="", validation_alias="EVOLUTION_API_URL")
    evolution_global_api_key: str = Field(default="", validation_alias="EVOLUTION_GLOBAL_API_KEY")
    evolution_instance_token: str = Field(default="", validation_alias="EVOLUTION_INSTANCE_TOKEN")
    evolution_instance_name: str = Field(default="openstudyai", validation_alias="EVOLUTION_INSTANCE_NAME")
    evolution_webhook_secret: str = Field(default="", validation_alias="EVOLUTION_WEBHOOK_SECRET")
    evolution_webhook_base_url: str = Field(
        default="http://app:8000", validation_alias="EVOLUTION_WEBHOOK_BASE_URL"
    )
    whatsapp_enabled: bool = Field(default=False, validation_alias="WHATSAPP_ENABLED")
    whatsapp_default_country_code: str = Field(default="55", validation_alias="WHATSAPP_DEFAULT_COUNTRY_CODE")
    whatsapp_max_media_mb: int = Field(default=15, ge=1, le=30, validation_alias="WHATSAPP_MAX_MEDIA_MB")
    whatsapp_scheduler_enabled: bool = Field(default=True, validation_alias="WHATSAPP_SCHEDULER_ENABLED")
    whatsapp_scheduler_interval_seconds: int = Field(default=20, ge=5, le=300, validation_alias="WHATSAPP_SCHEDULER_INTERVAL_SECONDS")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
