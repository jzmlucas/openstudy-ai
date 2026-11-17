from app.database import SessionLocal
from app.models import LLMCallLog


def record_llm_call(
    *,
    operation: str,
    model: str,
    provider: str = "ollama",
    elapsed_ms: int,
    prompt_chars: int = 0,
    response_chars: int = 0,
    prompt_tokens: int = 0,
    generated_tokens: int = 0,
    success: bool = True,
    error_message: str | None = None,
) -> None:
    """Persiste métricas de inferência sem interromper a operação principal."""
    db = SessionLocal()
    try:
        db.add(
            LLMCallLog(
                operation=operation,
                provider=provider,
                model=model,
                elapsed_ms=max(0, int(elapsed_ms)),
                prompt_chars=max(0, int(prompt_chars)),
                response_chars=max(0, int(response_chars)),
                prompt_tokens=max(0, int(prompt_tokens)),
                generated_tokens=max(0, int(generated_tokens)),
                success=success,
                error_message=(error_message or "")[:1500] or None,
            )
        )
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()
