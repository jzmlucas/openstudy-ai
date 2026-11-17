"""Diagnostic summary for WhatsApp support, without disclosing phone numbers or tokens.

Usage: docker compose --profile whatsapp exec app python -m app.services.whatsapp_diagnostics
"""
from __future__ import annotations

import httpx
from sqlalchemy import func

from app.config import settings
from app.database import SessionLocal
from app.models import WhatsAppDocumentJob, WhatsAppMessage, utc_now


def main() -> None:
    print("OpenStudy • Diagnóstico seguro do WhatsApp")
    print("WhatsApp ativo:", "sim" if settings.whatsapp_enabled else "NÃO (confira WHATSAPP_ENABLED no .env)")
    with SessionLocal() as db:
        rows = db.query(WhatsAppMessage.status, func.count(WhatsAppMessage.id)).filter(
            WhatsAppMessage.direction == "inbound"
        ).group_by(WhatsAppMessage.status).all()
        counts = dict(rows)
        print("Mensagens recebidas/na fila:", counts.get("received", 0) + counts.get("queued", 0))
        print("Mensagens em processamento:", counts.get("processing", 0))
        print("Mensagens com falha:", counts.get("failed", 0))
        oldest = db.query(func.min(WhatsAppMessage.created_at)).filter(
            WhatsAppMessage.direction == "inbound",
            WhatsAppMessage.status.in_(("received", "queued")),
        ).scalar()
        print("Aguardando há (segundos):", max(0, int((utc_now() - oldest).total_seconds())) if oldest else 0)
        active_docs = db.query(WhatsAppDocumentJob).filter(
            WhatsAppDocumentJob.status.in_(("pending", "processing"))
        ).count()
        print("Documentos pendentes/processando:", active_docs)
        # Do not print message payloads, phone numbers, keys or secrets.
        recent_failures = db.query(WhatsAppMessage.id).filter(
            WhatsAppMessage.direction == "inbound", WhatsAppMessage.status == "failed"
        ).order_by(WhatsAppMessage.id.desc()).limit(5).all()
        print("IDs das últimas falhas (sem conteúdo pessoal):", [r[0] for r in recent_failures])

    try:
        with httpx.Client(timeout=4.0) as client:
            response = client.get(settings.ollama_base_url.rstrip("/") + "/api/tags")
            response.raise_for_status()
            print("Ollama HTTP:", "OK")
    except httpx.HTTPError as exc:
        print("Ollama HTTP:", type(exc).__name__, "(verifique Ollama local e OLLAMA_DOCKER_URL)")

    if not settings.evolution_api_url:
        print("Evolution API: URL não configurada")
    else:
        try:
            with httpx.Client(timeout=4.0) as client:
                response = client.get(settings.evolution_api_url.rstrip("/") + "/")
            print("Evolution API HTTP:", response.status_code, "(resposta HTTP confirma alcance da porta)")
        except httpx.HTTPError as exc:
            print("Evolution API:", type(exc).__name__, "(verifique os containers/rede)")


if __name__ == "__main__":
    main()
