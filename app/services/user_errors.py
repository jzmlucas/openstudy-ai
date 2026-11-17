"""Mensagens de falha seguras para usuários do desktop e WhatsApp."""

from __future__ import annotations

import re


def friendly_document_error(raw: str | None) -> str:
    """Nunca envia stack traces, respostas brutas de APIs ou credenciais ao WhatsApp."""
    message = str(raw or "").strip()
    lower = message.casefold()
    # O OllamaClient já formata as falhas temporárias de maneira acessível.
    if ("modelo" in lower and ("alta demanda" in lower or "limite de solicitações" in lower
                                  or "não está instalado" in lower or "reserva" in lower)):
        # Não exibir conteúdos longos de erro armazenados anteriormente.
        return message[:550]
    if re.search(r"\b(503|502|504|529)\b|high demand|overload|temporarily unavailable|resource exhausted", lower):
        return ("O serviço de IA está com alta demanda agora. "
                "Você pode escolher outro modelo em Configurar IA ou aguardar um momento e tentar novamente.")
    if "429" in lower or "quota" in lower or "rate limit" in lower:
        return ("O modelo escolhido atingiu o limite de uso no momento. "
                "Experimente outro modelo em Configurar IA ou tente novamente mais tarde.")
    if "chave de api" in lower or "permissão" in lower:
        return "É necessário revisar a chave de acesso do modelo em Configurar IA."
    if "arquivo original não foi encontrado" in lower:
        return "Não encontrei o arquivo enviado. Envie-o novamente para tentar outra vez."
    if "flashcards válidos" in lower or "não gerou flashcards" in lower:
        return "O material foi recebido, mas a IA não conseguiu preparar os flashcards. Tente novamente ou escolha outro modelo."
    return "Não consegui processar o material agora. Tente novamente em instantes ou escolha outro modelo em Configurar IA."
