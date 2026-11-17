from __future__ import annotations

import base64
import hashlib
import hmac
import mimetypes
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.models import (
    Document,
    Exercise,
    ExerciseWorkspace,
    KnowledgeConcept,
    StudentProgress,
    StudyCard,
    WhatsAppDocumentJob,
    WhatsAppLinkChallenge,
    WhatsAppMessage,
    WhatsAppProfile,
    utc_now,
)
from app.services.evolution_client import EvolutionClient
from app.services.language import normalize_language
from app.services.user_errors import friendly_document_error

SUPPORTED_DOCUMENTS = {".pdf", ".docx", ".md"}
MAX_WHATSAPP_TEXT = 3500

# The fast worker handles only commands which do not run LLM generation or mutate study state.
# Normalize on ingest, rather than SQLite lower(), for correct Unicode behavior.
FAST_WHATSAPP_COMMANDS = frozenset({
    "oi", "olá", "ola", "menu", "ajuda", "help", "/start",
    "biblioteca", "materiais", "meus materiais", "fila", "minha fila",
})


def whatsapp_message_priority(text: str, message_type: str) -> int:
    kind = (message_type or "").casefold()
    if any(item in kind for item in ("document", "image", "audio", "video")):
        return 2  # media handoff/download in its own worker, not the text queue
    return 1 if " ".join((text or "").casefold().split()) in FAST_WHATSAPP_COMMANDS else 0



def normalize_phone(raw: str) -> str:
    raw_value = str(raw or "").strip()
    explicit_international = raw_value.startswith("+") or raw_value.startswith("00")
    digits = re.sub(r"\D", "", raw_value)
    if raw_value.startswith("00"):
        digits = digits[2:]
        explicit_international = True
    if not explicit_international and len(digits) in {10, 11}:
        digits = f"{settings.whatsapp_default_country_code}{digits}"
    if not 10 <= len(digits) <= 15:
        raise ValueError("Informe um telefone válido com DDI e DDD.")
    return digits


def hash_link_code(phone: str, code: str) -> str:
    return hashlib.sha256(f"{phone}:{code}".encode("utf-8")).hexdigest()


def split_message(text: str, limit: int = MAX_WHATSAPP_TEXT) -> list[str]:
    text = str(text or "").strip()
    if not text:
        return []
    chunks: list[str] = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = text.rfind(" ", 0, limit)
        if cut < limit // 2:
            cut = limit
        chunks.append(text[:cut].strip())
        text = text[cut:].strip()
    if text:
        chunks.append(text)
    return chunks


def send_whatsapp_text(
    db: Session,
    phone: str,
    text: str,
    *,
    log_message: bool = True,
) -> None:
    client = EvolutionClient()
    for part in split_message(text):
        try:
            result = client.send_text(phone, part)
            if log_message:
                db.add(
                    WhatsAppMessage(
                        phone_e164=phone,
                        direction="outbound",
                        message_type="text",
                        text=part,
                        payload={"evolution_response": result},
                        status="sent",
                    )
                )
                db.commit()
        except Exception as exc:
            if log_message:
                db.add(
                    WhatsAppMessage(
                        phone_e164=phone,
                        direction="outbound",
                        message_type="text",
                        text=part,
                        payload={},
                        status="failed",
                        error_message=str(exc)[:1000],
                    )
                )
                db.commit()
            raise


def extract_message_text(data: dict[str, Any]) -> str:
    message = data.get("message") or {}
    if not isinstance(message, dict):
        return ""
    candidates = [
        message.get("conversation"),
        (message.get("extendedTextMessage") or {}).get("text"),
        (message.get("imageMessage") or {}).get("caption"),
        (message.get("videoMessage") or {}).get("caption"),
        (message.get("documentMessage") or {}).get("caption"),
        (message.get("buttonsResponseMessage") or {}).get("selectedDisplayText"),
        (message.get("listResponseMessage") or {}).get("title"),
        (message.get("templateButtonReplyMessage") or {}).get("selectedDisplayText"),
    ]
    return next((str(item).strip() for item in candidates if item), "")


def extract_sender_phone(data: dict[str, Any]) -> str | None:
    key = data.get("key") or {}
    jid = (
        key.get("remoteJid")
        or key.get("remoteJidAlt")
        or key.get("participantAlt")
        or key.get("participant")
        or ""
    )
    if "@g.us" in jid or "status@" in jid:
        return None
    if "@lid" in jid:
        jid = key.get("remoteJidAlt") or key.get("participantAlt") or ""
    local = str(jid).split("@", 1)[0].split(":", 1)[0]
    digits = re.sub(r"\D", "", local)
    return digits if 10 <= len(digits) <= 15 else None


def get_profile(db: Session) -> WhatsAppProfile | None:
    return db.get(WhatsAppProfile, 1)


def _selected_document_ids(profile: WhatsAppProfile) -> list[int] | None:
    ids = [int(item) for item in (profile.selected_document_ids or []) if str(item).isdigit()]
    return ids or None


def _profile_phone(profile: WhatsAppProfile) -> str:
    if not profile.phone_e164 or not profile.verified:
        raise RuntimeError("O WhatsApp ainda não está vinculado.")
    return profile.phone_e164


def _send_help(db: Session, phone: str) -> None:
    send_whatsapp_text(
        db,
        phone,
        (
            "*OpenStudy AI · Assistente de estudos*\n\n"
            "Você pode conversar normalmente comigo ou usar estes comandos:\n\n"
            "📚 *biblioteca* — listar materiais\n"
            "*usar 3* — estudar um material específico\n"
            "*todos* — voltar a usar a biblioteca inteira\n"
            "🧠 *analisar [pergunta]* — conversar com o tutor usando seus materiais\n"
            "🎯 *exercicios [quantidade] [assunto]* — gerar e responder questões\n"
            "*continuar* — abrir no WhatsApp a sessão de exercícios iniciada no computador\n"
            "*dica* — receber uma pista da questão atual; *explicar questão* — pedir ajuda sem revelar a resposta\n"
            "*diagnostico* — iniciar um diagnóstico\n"
            "*aula [assunto]* — gerar uma microaula\n"
            "*explique [assunto]* — iniciar o método Feynman\n"
            "📈 *progresso* — consultar sua evolução\n"
            "*plano [dias] [minutos]* — criar um plano de estudos\n"
            "⚙️ *ia [ollama|gemini|openai|claude|groq]* — trocar o provedor configurado\n"
            "*modelo [id]* — trocar o modelo do provedor ativo\n"
            "⏰ *lembretes on/off* e *horario HH:MM*\n"
            "*cancelar* — cancelar o fluxo atual\n"
            "*parar* — pausar lembretes; *começar* — reativar\n\n"
            "Envie também PDF, DOCX ou Markdown para adicionar à biblioteca."
        ),
    )


def _send_next_exercise(db: Session, profile: WhatsAppProfile, phone: str) -> None:
    state = dict(profile.conversation_state or {})
    ids = state.get("exercise_ids") or []
    index = int(state.get("exercise_index", 0))
    if index >= len(ids):
        profile.conversation_state = {}
        db.commit()
        progress = db.get(StudentProgress, 1)
        xp = progress.xp if progress else 0
        send_whatsapp_text(
            db,
            phone,
            f"🏁 *Rodada concluída!*\nVocê respondeu {len(ids)} exercícios.\nSeu total atual é *{xp} XP*.\n\nEnvie *exercicios* para praticar novamente.",
        )
        return

    exercise = db.get(Exercise, int(ids[index]))
    if exercise is None:
        state["exercise_index"] = index + 1
        profile.conversation_state = state
        db.commit()
        _send_next_exercise(db, profile, phone)
        return

    state["current_exercise_started_at"] = time.time()
    state["mode"] = "exercise"
    profile.conversation_state = state
    db.commit()

    message = f"🎯 *Questão {index + 1}/{len(ids)} · {exercise.topic or 'Geral'}*\n\n{exercise.question}"
    if exercise.options:
        message += "\n\n" + "\n".join(
            f"*{letter}.* {option}" for letter, option in zip("ABCDEFGH", exercise.options)
        )
        message += "\n\nResponda com a letra, o número ou o texto da alternativa."
    else:
        message += "\n\nResponda com suas palavras."
    send_whatsapp_text(db, phone, message)


def _workspace_first_unanswered(workspace: ExerciseWorkspace) -> int:
    results = workspace.results or {}
    for index, exercise_id in enumerate(workspace.exercise_ids or []):
        if str(exercise_id) not in results:
            return index
    return len(workspace.exercise_ids or [])


def _activate_shared_workspace(
    db: Session,
    profile: WhatsAppProfile,
    exercise_ids: list[int],
    *,
    purpose: str = "practice",
    source: str = "whatsapp",
    focus_session_id: int | None = None,
) -> ExerciseWorkspace:
    for previous in db.query(ExerciseWorkspace).filter_by(status="active").all():
        previous.status = "superseded"
        previous.updated_at = utc_now()
    workspace = ExerciseWorkspace(
        exercise_ids=list(dict.fromkeys(int(item) for item in exercise_ids)),
        current_index=0,
        status="active",
        purpose=purpose,
        source=source,
        focus_session_id=focus_session_id,
        results={},
    )
    db.add(workspace)
    db.flush()
    profile.conversation_state = {"mode": "shared_exercise", "workspace_id": workspace.id, "hint_level": 0}
    db.commit()
    db.refresh(workspace)
    return workspace


def _send_shared_workspace_question(db: Session, profile: WhatsAppProfile, phone: str) -> bool:
    state = dict(profile.conversation_state or {})
    workspace = db.get(ExerciseWorkspace, int(state.get("workspace_id"))) if state.get("workspace_id") else None
    if workspace is None or workspace.status in {"superseded", "cancelled"}:
        workspace = db.query(ExerciseWorkspace).filter_by(status="active").order_by(ExerciseWorkspace.id.desc()).first()
    if workspace is None:
        profile.conversation_state = {}
        db.commit()
        send_whatsapp_text(db, phone, "Não há uma sessão de exercícios ativa no computador. Abra uma sessão no OpenStudy ou envie *exercicios* para começar por aqui.")
        return False
    if workspace.status == "completed":
        profile.conversation_state = {}
        db.commit()
        progress = db.get(StudentProgress, 1)
        send_whatsapp_text(db, phone, f"🏁 *Sessão concluída!* Você respondeu {len(workspace.exercise_ids or [])} questões. Seu total é *{progress.xp if progress else 0} XP*. Os resultados também estão no OpenStudy do computador.")
        return True
    if workspace.status != "active":
        send_whatsapp_text(db, phone, "Essa sessão não está mais ativa. Abra uma nova sessão no OpenStudy ou envie *exercicios*.")
        return True

    index = _workspace_first_unanswered(workspace)
    ids = workspace.exercise_ids or []
    workspace.current_index = index
    if index >= len(ids):
        workspace.status = "completed"
        profile.conversation_state = {}
        db.commit()
        progress = db.get(StudentProgress, 1)
        send_whatsapp_text(db, phone, f"🏁 *Sessão concluída!*. Você respondeu {len(ids)} questões. Seu total é *{progress.xp if progress else 0} XP*. Os resultados também estão no OpenStudy do computador.")
        return True

    exercise = db.get(Exercise, int(ids[index]))
    if exercise is None:
        results = dict(workspace.results or {})
        results[str(ids[index])] = {"skipped": True, "feedback": "Questão indisponível."}
        workspace.results = results
        workspace.current_index = index + 1
        db.commit()
        return _send_shared_workspace_question(db, profile, phone)

    state = dict(profile.conversation_state or {})
    state.update({"mode": "shared_exercise", "workspace_id": workspace.id, "exercise_id": exercise.id, "hint_level": int(state.get("hint_level", 0))})
    profile.conversation_state = state
    workspace.current_index = index
    db.commit()

    message = f"🔄 *Sessão compartilhada com o computador*\n🎯 *Questão {index + 1}/{len(ids)} · {exercise.topic or 'Geral'}*\n\n{exercise.question}"
    if exercise.options:
        message += "\n\n" + "\n".join(f"*{letter}.* {option}" for letter, option in zip("ABCDEFGH", exercise.options))
        message += "\n\nResponda com a letra, o número ou o texto da alternativa."
    else:
        message += "\n\nResponda com suas palavras."
    message += "\n\nEnvie *dica* para uma pista ou *explicar questão* para entender o raciocínio."
    send_whatsapp_text(db, phone, message)
    return True


def _send_shared_exercise_hint(db: Session, profile: WhatsAppProfile, phone: str) -> bool:
    state = dict(profile.conversation_state or {})
    workspace = db.get(ExerciseWorkspace, int(state.get("workspace_id"))) if state.get("workspace_id") else None
    if workspace is None or workspace.status != "active":
        workspace = db.query(ExerciseWorkspace).filter_by(status="active").order_by(ExerciseWorkspace.id.desc()).first()
    if workspace is None:
        send_whatsapp_text(db, phone, "Não há uma questão compartilhada aberta. Envie *continuar* para retomar a sessão do computador.")
        return True
    index = _workspace_first_unanswered(workspace)
    if index >= len(workspace.exercise_ids or []):
        _send_shared_workspace_question(db, profile, phone)
        return True
    exercise = db.get(Exercise, int(workspace.exercise_ids[index]))
    if exercise is None:
        _send_shared_workspace_question(db, profile, phone)
        return True
    level = min(3, int(state.get("hint_level", 0)) + 1)
    from app.routers.api import exercise_hint
    from app.schemas import ExerciseHintRequest
    result = exercise_hint(exercise.id, ExerciseHintRequest(level=level, language=normalize_language(profile.language)), db)
    state.update({"mode": "shared_exercise", "workspace_id": workspace.id, "hint_level": level})
    profile.conversation_state = state
    db.commit()
    send_whatsapp_text(db, phone, f"💡 *Dica {level}/3*\n{result.get('hint', '')}")
    return True


def _explain_shared_exercise(db: Session, profile: WhatsAppProfile, phone: str) -> bool:
    state = dict(profile.conversation_state or {})
    workspace = db.get(ExerciseWorkspace, int(state.get("workspace_id"))) if state.get("workspace_id") else None
    if workspace is None or workspace.status != "active":
        workspace = db.query(ExerciseWorkspace).filter_by(status="active").order_by(ExerciseWorkspace.id.desc()).first()
    if workspace is None:
        send_whatsapp_text(db, phone, "Não há uma questão ativa. Envie *continuar* para retomar a sessão do computador.")
        return True
    index = _workspace_first_unanswered(workspace)
    if index >= len(workspace.exercise_ids or []):
        _send_shared_workspace_question(db, profile, phone)
        return True
    exercise = db.get(Exercise, int(workspace.exercise_ids[index]))
    if exercise is None:
        _send_shared_workspace_question(db, profile, phone)
        return True
    profile.conversation_state = {"mode": "shared_exercise", "workspace_id": workspace.id, "exercise_id": exercise.id, "hint_level": 0}
    db.commit()
    from app.routers.api import tutor_chat
    from app.schemas import TutorChatRequest
    options = "\n".join(f"- {item}" for item in (exercise.options or []))
    prompt = (
        "Ajude-me a compreender a questão abaixo sem revelar a resposta correta e sem resolvê-la por mim. "
        "Explique os conceitos envolvidos, faça uma pergunta orientadora e dê uma pista gradual.\n\n"
        f"Questão: {exercise.question}\nAlternativas:\n{options}\n"
        f"Assunto: {exercise.topic or 'Geral'}\nContexto do material: {exercise.source_excerpt[:1200]}"
    )
    result = tutor_chat(TutorChatRequest(message=prompt, language=normalize_language(profile.language), document_ids=_selected_document_ids(profile), mode="socratic"), db)
    send_whatsapp_text(db, phone, result.get("answer", "Vamos analisar a questão por partes."))
    return True


def _answer_active_exercise(
    db: Session,
    profile: WhatsAppProfile,
    phone: str,
    incoming: str,
) -> bool:
    state = dict(profile.conversation_state or {})
    shared_mode = state.get("mode") == "shared_exercise"
    if state.get("mode") not in {"exercise", "shared_exercise"}:
        return False
    workspace = db.get(ExerciseWorkspace, int(state.get("workspace_id"))) if shared_mode and state.get("workspace_id") else None
    if shared_mode:
        if workspace is None or workspace.status != "active":
            profile.conversation_state = {}
            db.commit()
            send_whatsapp_text(db, phone, "A sessão compartilhada não está mais ativa. Envie *continuar* para verificar o computador.")
            return True
        ids = workspace.exercise_ids or []
        index = _workspace_first_unanswered(workspace)
    else:
        ids = state.get("exercise_ids") or []
        index = int(state.get("exercise_index", 0))
    if index >= len(ids):
        if shared_mode:
            _send_shared_workspace_question(db, profile, phone)
        else:
            profile.conversation_state = {}
            db.commit()
        return shared_mode
    exercise = db.get(Exercise, int(ids[index]))
    if exercise is None:
        state["exercise_index"] = index + 1
        profile.conversation_state = state
        db.commit()
        _send_next_exercise(db, profile, phone)
        return True

    answer = incoming.strip()
    if exercise.options:
        option_match = re.fullmatch(r"([a-h]|[1-8])[\).]?", answer.casefold())
        if option_match:
            token = option_match.group(1)
            option_index = ord(token) - ord("a") if token.isalpha() else int(token) - 1
            if 0 <= option_index < len(exercise.options):
                answer = exercise.options[option_index]

    from app.routers.api import answer_exercise
    from app.schemas import ExerciseAnswerRequest

    started = float(state.get("current_exercise_started_at") or time.time())
    result = answer_exercise(
        exercise.id,
        ExerciseAnswerRequest(
            answer=answer,
            language=normalize_language(profile.language),
            confidence=3,
            response_time_seconds=max(0.0, min(3600.0, time.time() - started)),
            session_id=workspace.focus_session_id if shared_mode and workspace else None,
            workspace_id=workspace.id if shared_mode and workspace else None,
        ),
        db,
    )
    score = int(round(float(result.get("score", 0)) * 100))
    status = "✅ *Correto!*" if result.get("correct") else "📌 *Vamos corrigir juntos.*"
    response = (
        f"{status} · {score}/100\n\n"
        f"{result.get('feedback', '')}\n\n"
        f"*Resposta esperada:* {result.get('correct_answer', '')}\n"
        f"*Por quê:* {result.get('explanation', '')}\n"
        f"*Recompensa:* +{(result.get('reward') or {}).get('xp_awarded', 0)} XP"
    )
    remediation = result.get("remediation_progress")
    if remediation:
        response += (
            f"\n\nReforço: {remediation.get('successful_rechecks', 0)}/"
            f"{remediation.get('required_rechecks', 2)} verificações corretas em questões diferentes."
        )
    send_whatsapp_text(db, phone, response)

    if shared_mode:
        profile.conversation_state = {"mode": "shared_exercise", "workspace_id": workspace.id, "hint_level": 0}
        db.commit()
        _send_shared_workspace_question(db, profile, phone)
    else:
        state["exercise_index"] = index + 1
        state["mode"] = "exercise"
        profile.conversation_state = state
        db.commit()
        _send_next_exercise(db, profile, phone)
    return True


def _selected_cards(db: Session, profile: WhatsAppProfile) -> list[StudyCard]:
    query = db.query(StudyCard).filter(StudyCard.archived.is_(False))
    document_ids = _selected_document_ids(profile)
    if document_ids:
        query = query.filter(StudyCard.document_id.in_(document_ids))
    return query.order_by(StudyCard.id.desc()).limit(100).all()


def _handle_command(
    db: Session,
    profile: WhatsAppProfile,
    phone: str,
    text: str,
) -> bool:
    from app.routers.api import (
        create_diagnostic,
        create_exercises,
        create_study_plan,
        evaluate_feynman,
        generate_learning_lesson,
        tutor_chat,
    )
    from app.schemas import (
        DiagnosticRequest,
        ExerciseGenerateRequest,
        FeynmanRequest,
        LearningTopicRequest,
        StudyPlanRequest,
        TutorChatRequest,
    )
    from app.services.analytics_service import build_analytics
    from app.services.llm_settings import (
        PROVIDER_CATALOG,
        get_active_provider,
        get_provider_credential,
        save_provider_credential,
        set_active_provider,
    )

    clean = text.strip()
    normalized = re.sub(r"\s+", " ", clean.casefold()).strip()
    language = normalize_language(profile.language)

    if normalized in {"continuar", "continuar exercicio", "continuar exercício", "computador", "exercicio atual", "exercício atual", "retomar"}:
        _send_shared_workspace_question(db, profile, phone)
        return True

    if normalized in {"dica", "hint"}:
        return _send_shared_exercise_hint(db, profile, phone)

    if normalized in {"explicar questao", "explicar questão", "ajuda questao", "ajuda questão", "nao entendi a questao", "não entendi a questão"}:
        return _explain_shared_exercise(db, profile, phone)

    if normalized in {"fila", "minha fila"}:
        from app.models import WhatsAppDocumentJob
        pending = db.query(WhatsAppMessage).filter(
            WhatsAppMessage.direction == "inbound",
            WhatsAppMessage.status.in_(("received", "queued")),
        ).count()
        running = db.query(WhatsAppMessage).filter(
            WhatsAppMessage.direction == "inbound", WhatsAppMessage.status == "processing",
        ).count()
        documents = db.query(WhatsAppDocumentJob).filter(
            WhatsAppDocumentJob.status.in_(("pending", "processing")),
        ).count()
        send_whatsapp_text(
            db, phone,
            f"📊 *Atendimento OpenStudy*\n\nMensagens aguardando: *{pending}*\n"
            f"Mensagens em execução: *{running}*\nDocumentos em processamento/fila: *{documents}*.\n\n"
            "Posso receber comandos simples enquanto uma pergunta longa é processada.",
        )
        return True

    if normalized in {"ajuda", "menu", "help", "/start", "oi", "olá", "ola"}:
        _send_help(db, phone)
        return True

    if normalized in {"parar", "sair", "stop", "pausar notificações", "pausar notificacoes"}:
        profile.notifications_enabled = False
        profile.reminders_enabled = False
        profile.conversation_state = {}
        db.commit()
        send_whatsapp_text(
            db, phone,
            "🔕 Lembretes e mensagens proativas foram pausados. Para reativar, envie *COMEÇAR*.",
        )
        return True

    if normalized in {"começar", "comecar", "start", "ativar"}:
        profile.notifications_enabled = True
        profile.reminders_enabled = True
        db.commit()
        send_whatsapp_text(
            db, phone,
            "🔔 Notificações reativadas. Seus lembretes seguem os dias e horários configurados no OpenStudy.",
        )
        return True

    if normalized in {"cancelar", "cancel", "sair do exercício", "sair do exercicio"}:
        profile.conversation_state = {}
        db.commit()
        send_whatsapp_text(db, phone, "Fluxo cancelado. Envie *menu* para ver o que posso fazer.")
        return True

    if normalized in {"progresso", "meu progresso", "status"}:
        analytics = build_analytics(db)
        progress = db.get(StudentProgress, 1)
        due = int(analytics.get("due_cards", 0))
        weak = analytics.get("weak_topics") or []
        weak_text = ", ".join(
            str(item.get("topic", "Assunto")) for item in weak[:4]
        ) or "Nenhum assunto prioritário identificado."
        send_whatsapp_text(
            db,
            phone,
            (
                "📈 *Seu progresso no OpenStudy*\n\n"
                f"⭐ XP: *{progress.xp if progress else 0}*\n"
                f"🏅 Nível: *{progress.level if progress else 1}*\n"
                f"🃏 Flashcards ativos: *{db.query(StudyCard).filter(StudyCard.archived.is_(False)).count()}*\n"
                f"⏰ Revisões pendentes: *{due}*\n"
                f"🎯 Tentativas: *{progress.total_attempts if progress else 0}*\n"
                f"Assuntos para reforçar: {weak_text}"
            ),
        )
        return True

    if normalized in {"biblioteca", "materiais", "meus materiais"}:
        documents = db.query(Document).order_by(Document.created_at.desc()).limit(15).all()
        if not documents:
            send_whatsapp_text(db, phone, "Sua biblioteca está vazia. Envie um PDF, DOCX ou Markdown aqui para começar.")
            return True
        selected = set(profile.selected_document_ids or [])
        rows = []
        for document in documents:
            marker = "✓ " if document.id in selected else ""
            rows.append(
                f"*{document.id}.* {marker}{document.original_filename} — {document.status}"
            )
        send_whatsapp_text(
            db,
            phone,
            "📚 *Sua biblioteca*\n\n" + "\n".join(rows)
            + "\n\nEnvie *usar ID* para focar em um material ou *todos* para usar a biblioteca inteira.",
        )
        return True

    use_match = re.fullmatch(r"(?:usar|material)\s+#?(\d+)", normalized)
    if use_match:
        document_id = int(use_match.group(1))
        document = db.get(Document, document_id)
        if not document:
            send_whatsapp_text(db, phone, "Não encontrei esse material. Envie *biblioteca* para ver os IDs.")
        else:
            profile.selected_document_ids = [document_id]
            db.commit()
            send_whatsapp_text(db, phone, f"📚 Agora vou priorizar *{document.original_filename}* nas análises e exercícios.")
        return True

    if normalized in {"todos", "usar todos", "biblioteca inteira", "limpar seleção", "limpar selecao"}:
        profile.selected_document_ids = []
        db.commit()
        send_whatsapp_text(db, phone, "Biblioteca inteira selecionada para análises e exercícios.")
        return True

    reminder_match = re.fullmatch(r"lembretes?\s+(on|off|ligar|desligar)", normalized)
    if reminder_match:
        enabled = reminder_match.group(1) in {"on", "ligar"}
        profile.reminders_enabled = enabled
        profile.notifications_enabled = True
        db.commit()
        send_whatsapp_text(
            db, phone,
            "⏰ Lembretes ativados." if enabled else "⏸️ Lembretes desativados.",
        )
        return True

    time_match = re.fullmatch(r"(?:horário|horario|hora)\s+(\d{1,2}):(\d{2})", normalized)
    if time_match:
        hour, minute = int(time_match.group(1)), int(time_match.group(2))
        if hour > 23 or minute > 59:
            send_whatsapp_text(db, phone, "Horário inválido. Use HH:MM, por exemplo: *horario 19:30*.")
        else:
            profile.reminder_times = [f"{hour:02d}:{minute:02d}"]
            profile.reminders_enabled = True
            profile.notifications_enabled = True
            db.commit()
            send_whatsapp_text(db, phone, f"⏰ Seu lembrete diário foi configurado para *{hour:02d}:{minute:02d}* ({profile.timezone}).")
        return True

    provider_match = re.fullmatch(r"(?:ia|provedor)\s+(ollama|gemini|openai|claude|groq)", normalized)
    if provider_match:
        provider = provider_match.group(1)
        credential = get_provider_credential(db, provider)
        if provider != "ollama" and not credential.get("configured"):
            send_whatsapp_text(
                db, phone,
                f"O provedor *{PROVIDER_CATALOG[provider]['label']}* ainda não tem uma chave configurada. Salve a chave na tela *Configurar IA* do OpenStudy e depois envie *ia {provider}*.",
            )
        else:
            set_active_provider(db, provider)
            send_whatsapp_text(
                db, phone,
                f"🤖 Provedor ativo: *{PROVIDER_CATALOG[provider]['label']}* · Modelo: `{credential.get('model')}`.",
            )
        return True

    model_match = re.fullmatch(r"modelo\s+(.+)", clean, flags=re.IGNORECASE)
    if model_match:
        provider = get_active_provider(db)
        credential = get_provider_credential(db, provider)
        model = model_match.group(1).strip()[:160]
        if provider != "ollama" and not credential.get("configured"):
            send_whatsapp_text(db, phone, "Configure primeiro a chave do provedor na tela *Configurar IA*.")
        else:
            save_provider_credential(db, provider, model, api_key=None)
            send_whatsapp_text(db, phone, f"Modelo atualizado para `{model}` no provedor *{provider}*.")
        return True

    exercise_match = re.match(r"^(?:exercícios|exercicios|quiz|questões|questoes)(?:\s+(\d+))?(?:\s+(.+))?$", clean, flags=re.IGNORECASE)
    if exercise_match:
        count = min(10, max(3, int(exercise_match.group(1) or 5)))
        topic = (exercise_match.group(2) or "").strip()
        cards = _selected_cards(db, profile)
        if len(cards) < 3:
            send_whatsapp_text(db, phone, "Preciso de pelo menos 3 flashcards no material selecionado. Envie um arquivo ou escolha outro material.")
            return True
        from app.schemas import ExerciseGenerateRequest
        payload = ExerciseGenerateRequest(
            count=count,
            topic=topic,
            language=language,
            difficulty="mista",
            purpose="practice",
            bloom_level="mixed",
            card_ids=[card.id for card in cards],
        )
        generated = create_exercises(payload, db)
        _activate_shared_workspace(db, profile, [item["id"] for item in generated], purpose="practice", source="whatsapp")
        send_whatsapp_text(db, phone, f"🎯 Preparei *{len(generated)} exercícios*. Esta sessão também ficará disponível no OpenStudy do computador.")
        _send_shared_workspace_question(db, profile, phone)
        return True

    if normalized in {"diagnóstico", "diagnostico", "iniciar diagnóstico", "iniciar diagnostico"}:
        cards = _selected_cards(db, profile)
        if len(cards) < 3:
            send_whatsapp_text(db, phone, "Adicione pelo menos 3 flashcards antes de iniciar o diagnóstico.")
            return True
        result = create_diagnostic(
            DiagnosticRequest(topic="", count=5, language=language, card_ids=[card.id for card in cards]), db
        )
        _activate_shared_workspace(db, profile, [item["id"] for item in result], purpose="diagnostic", source="whatsapp")
        send_whatsapp_text(db, phone, "🧭 *Diagnóstico iniciado.* Responda sem consultar o material. O resultado também aparecerá no OpenStudy do computador.")
        _send_shared_workspace_question(db, profile, phone)
        return True

    lesson_match = re.match(r"^(?:aula|microaula)\s+(.+)$", clean, flags=re.IGNORECASE)
    if lesson_match:
        topic = lesson_match.group(1).strip()[:180]
        from app.services.learning_engine import normalize_topic, sync_concepts
        sync_concepts(db)
        db.commit()
        concept_exists = db.query(KnowledgeConcept).filter_by(
            normalized_key=normalize_topic(topic)
        ).one_or_none()
        if concept_exists is None:
            send_whatsapp_text(
                db,
                phone,
                "Não encontrei esse assunto no mapa de conceitos. Envie *biblioteca* e escolha um material com esse tema ou use uma tag existente dos seus flashcards.",
            )
            return True
        lesson = generate_learning_lesson(
            LearningTopicRequest(topic=topic, language=language, document_ids=_selected_document_ids(profile)), db
        )
        send_whatsapp_text(
            db,
            phone,
            (
                f"📘 *{lesson.get('title', topic)}*\n"
                f"*Objetivo:* {lesson.get('learning_goal', '')}\n\n"
                f"*Comece pelo simples*\n{lesson.get('simple_explanation', '')}\n\n"
                f"*Analogia*\n{lesson.get('analogy', '')}\n\n"
                f"*Aprofundamento*\n{lesson.get('technical_explanation', '')}\n\n"
                f"*Exemplo*\n{lesson.get('worked_example', '')}\n\n"
                f"*Verificação:* {lesson.get('check_question', '')}"
            ),
        )
        profile.conversation_state = {"mode": "lesson_check", "topic": topic}
        db.commit()
        return True

    explain_match = re.match(r"^(?:explique|feynman)\s+(.+)$", clean, flags=re.IGNORECASE)
    if explain_match:
        topic = explain_match.group(1).strip()[:180]
        profile.conversation_state = {"mode": "feynman", "topic": topic}
        db.commit()
        send_whatsapp_text(
            db, phone,
            f"🧠 *Método Feynman · {topic}*\n\nExplique esse conceito com suas próprias palavras, como se estivesse ensinando a alguém que nunca estudou o assunto. Envie sua explicação em uma única mensagem.",
        )
        return True

    plan_match = re.match(r"^plano(?:\s+(\d+))?(?:\s+(\d+))?$", normalized)
    if plan_match:
        days = min(30, max(1, int(plan_match.group(1) or 7)))
        minutes = min(240, max(10, int(plan_match.group(2) or 45)))
        plan = create_study_plan(
            StudyPlanRequest(
                objective="Dominar os assuntos dos meus materiais",
                days=days,
                minutes_per_day=minutes,
                language=language,
            ),
            db,
        )
        first_tasks = plan.get("tasks", [])[:5]
        task_text = "\n".join(
            f"Dia {task['day_number']}: {task['title']} ({task['estimated_minutes']} min)"
            for task in first_tasks
        )
        send_whatsapp_text(
            db, phone,
            f"🗓️ *{plan['title']}*\n{plan['summary']}\n\n*Primeiras tarefas:*\n{task_text}\n\nSeu plano completo está disponível no painel do OpenStudy.",
        )
        return True

    analyze_text = re.sub(r"^(?:analisar|analise|análise|perguntar|pergunta)\s*", "", clean, flags=re.IGNORECASE)
    if len(analyze_text) >= 2:
        document_ids = _selected_document_ids(profile)
        answer = tutor_chat(
            TutorChatRequest(
                message=analyze_text,
                language=language,
                document_ids=document_ids,
                mode="teacher",
            ),
            db,
        )
        response = answer.get("answer", "Não consegui formular uma resposta agora.")
        sources = answer.get("sources") or []
        if sources:
            citations = []
            for source in sources[:4]:
                page = f", pág. {source['page_number']}" if source.get("page_number") else ""
                citations.append(f"• {source['filename']}{page}")
            response += "\n\n*Fontes consultadas:*\n" + "\n".join(citations)
        send_whatsapp_text(db, phone, response)
        return True

    return False


def _handle_user_text(db: Session, profile: WhatsAppProfile, phone: str, text: str) -> None:
    clean = text.strip()
    if not clean:
        return

    if _handle_command(db, profile, phone, clean):
        return

    state = dict(profile.conversation_state or {})
    if state.get("mode") == "exercise":
        _answer_active_exercise(db, profile, phone, clean)
        return

    if state.get("mode") == "feynman":
        if len(clean) < 20:
            send_whatsapp_text(
                db, phone,
                "Sua explicação precisa ter pelo menos 20 caracteres. Desenvolva um pouco mais e envie novamente; seu exercício continua aberto.",
            )
            return
        from app.routers.api import evaluate_feynman
        from app.schemas import FeynmanRequest

        topic = str(state.get("topic") or "Geral")
        result = evaluate_feynman(
            FeynmanRequest(topic=topic, explanation=clean, language=normalize_language(profile.language)),
            db,
        )
        profile.conversation_state = {}
        db.commit()
        send_whatsapp_text(
            db,
            phone,
            (
                f"🧠 *Avaliação Feynman · {topic}*\n"
                f"Pontuação: *{round(result['overall_score'] * 100)}/100*\n\n"
                f"{result['feedback']}\n\n"
                f"*Melhoria prioritária:* {result['priority_improvement']}\n"
                f"*Tente novamente:* {result['retry_prompt']}"
            ),
        )
        return

    if state.get("mode") == "lesson_check":
        if len(clean) < 5:
            send_whatsapp_text(
                db, phone,
                "Escreva uma resposta um pouco mais completa (pelo menos 5 caracteres) para eu avaliar sua compreensão.",
            )
            return
        from app.routers.api import check_learning_lesson
        from app.schemas import LessonCheckRequest

        topic = str(state.get("topic") or "")
        result = check_learning_lesson(
            LessonCheckRequest(
                topic=topic, answer=clean, language=normalize_language(profile.language),
                document_ids=_selected_document_ids(profile),
            ),
            db,
        )
        profile.conversation_state = {}
        db.commit()
        send_whatsapp_text(
            db,
            phone,
            (
                f"{'✅' if result.get('correct') else '📌'} *Verificação da microaula*\n"
                f"{result.get('feedback', '')}\n\n"
                f"Pontuação: {round(float(result.get('score', 0)) * 100)}/100\n"
                f"{result.get('intervention', 'Continue praticando o conceito.')}"
            ),
        )
        return

    from app.routers.api import tutor_chat
    from app.schemas import TutorChatRequest

    answer = tutor_chat(
        TutorChatRequest(
            message=clean[:6000],
            language=normalize_language(profile.language),
            document_ids=_selected_document_ids(profile),
            mode="teacher",
        ),
        db,
    )
    response = answer.get("answer", "Não consegui responder agora.")
    sources = answer.get("sources") or []
    if sources:
        response += "\n\n*Fontes:*\n" + "\n".join(
            f"• {item['filename']}" + (f" · pág. {item['page_number']}" if item.get("page_number") else "")
            for item in sources[:4]
        )
    send_whatsapp_text(db, phone, response)


def _handle_link_code(db: Session, phone: str, text: str) -> bool:
    match = re.fullmatch(r"(?:vincular|link|confirmar)\s+(\d{6})", text.strip(), flags=re.IGNORECASE)
    if not match:
        return False
    challenge = (
        db.query(WhatsAppLinkChallenge)
        .filter(
            WhatsAppLinkChallenge.phone_e164 == phone,
            WhatsAppLinkChallenge.consumed_at.is_(None),
        )
        .order_by(WhatsAppLinkChallenge.created_at.desc())
        .first()
    )
    if not challenge or challenge.expires_at < utc_now() or challenge.attempts >= 5:
        return False
    expected = hash_link_code(phone, match.group(1))
    if not hmac.compare_digest(challenge.code_hash, expected):
        challenge.attempts += 1
        if challenge.attempts >= 5:
            challenge.consumed_at = utc_now()
        db.commit()
        try:
            send_whatsapp_text(db, phone, "Código incorreto. Confira o código mais recente enviado pelo OpenStudy.")
        except Exception:
            pass
        return True

    challenge.consumed_at = utc_now()
    profile = db.get(WhatsAppProfile, 1)
    if profile is None:
        profile = WhatsAppProfile(id=1, phone_e164=phone)
        db.add(profile)
    profile.phone_e164 = phone
    profile.chat_jid = f"{phone}@s.whatsapp.net"
    profile.verified = True
    profile.verified_at = utc_now()
    profile.notifications_enabled = True
    profile.reminders_enabled = False
    profile.last_interaction_at = utc_now()
    profile.conversation_state = {}
    db.commit()
    send_whatsapp_text(
        db,
        phone,
        "✅ *WhatsApp vinculado com sucesso!*\n\nAgora você pode conversar comigo, enviar materiais e estudar por aqui.\n\nOs lembretes estão desativados até você ativá-los. Envie *lembretes on* ou configure dias e horários no painel.\n\nEnvie *menu* para conhecer os comandos.",
    )
    return True


def _process_document_message(
    db: Session,
    profile: WhatsAppProfile,
    phone: str,
    data: dict[str, Any],
    *,
    defer_processing: bool = False,
    message_row_id: int | None = None,
) -> None:
    message = data.get("message") or {}
    media_node = {}
    for key in ("documentMessage", "documentWithCaptionMessage", "imageMessage"):
        if isinstance(message.get(key), dict):
            media_node = message[key]
            nested_message = media_node.get("message")
            if isinstance(nested_message, dict) and isinstance(nested_message.get("documentMessage"), dict):
                media_node = nested_message["documentMessage"]
            break
    filename = str(
        media_node.get("fileName")
        or media_node.get("filename")
        or data.get("fileName")
        or ""
    ).strip()
    extension = Path(filename).suffix.lower()
    if extension not in SUPPORTED_DOCUMENTS:
        mimetype = str(media_node.get("mimetype") or media_node.get("mimeType") or "")
        guessed = mimetypes.guess_extension(mimetype) if mimetype else None
        extension = guessed.lower() if guessed else ""
    if extension not in SUPPORTED_DOCUMENTS:
        send_whatsapp_text(
            db,
            phone,
            "📎 Por enquanto consigo processar documentos PDF, DOCX e Markdown (.md). Envie um desses formatos como documento, não como foto.",
        )
        return

    expected_size = media_node.get("fileLength") or media_node.get("fileSize")
    max_bytes = settings.whatsapp_max_media_mb * 1024 * 1024
    try:
        expected_size = int(expected_size or 0)
    except (TypeError, ValueError):
        expected_size = 0
    if expected_size > max_bytes:
        send_whatsapp_text(
            db,
            phone,
            f"O arquivo excede o limite de {settings.whatsapp_max_media_mb} MB. Envie uma versão menor.",
        )
        return

    media_result = EvolutionClient().download_media(data)
    nested_data = media_result.get("data")
    encoded = media_result.get("base64")
    if not encoded and isinstance(nested_data, dict):
        encoded = nested_data.get("base64")
    elif not encoded and isinstance(nested_data, str):
        encoded = nested_data
    if not encoded:
        raise RuntimeError("A Evolution API não retornou o conteúdo do arquivo.")
    if isinstance(encoded, str) and encoded.startswith("data:"):
        encoded = encoded.split(",", 1)[-1]
    file_bytes = base64.b64decode(encoded, validate=False)
    if not file_bytes or len(file_bytes) > max_bytes:
        raise ValueError(f"O arquivo precisa ter até {settings.whatsapp_max_media_mb} MB.")

    original_name = Path((filename or f"material{extension}").replace("\\", "/").split("/")[-1]).name[:255]
    if not Path(original_name).suffix:
        original_name += extension
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    stored_path = settings.uploads_dir / f"{uuid.uuid4().hex}{extension}"
    stored_path.write_bytes(file_bytes)

    document = Document(
        original_filename=original_name,
        stored_path=str(stored_path),
        file_type=extension,
        status="uploaded",
        rag_status="pending",
    )
    db.add(document)
    db.commit()
    db.refresh(document)
    db.commit()

    try:
        send_whatsapp_text(
            db,
            phone,
            f"📥 Recebi *{original_name}* ({len(file_bytes) / 1024 / 1024:.1f} MB).\n\nVou extrair o texto, indexar o conteúdo e gerar flashcards. Isso pode levar alguns minutos.",
        )
    except Exception:
        pass

    if defer_processing:
        if message_row_id is None:
            raise ValueError("Uma mensagem persistida é necessária para agendar o documento.")
        db.add(WhatsAppDocumentJob(
            message_id=message_row_id,
            document_id=document.id,
            phone_e164=phone,
            language=normalize_language(profile.language),
            status="pending",
        ))
        db.commit()
        return

    from app.services.study_service import process_document

    process_document(document.id, normalize_language(profile.language))
    db.expire_all()
    db.refresh(document)
    if document.status == "ready":
        card_count = db.query(StudyCard).filter(
            StudyCard.document_id == document.id,
            StudyCard.archived.is_(False),
        ).count()
        try:
            send_whatsapp_text(
                db,
                phone,
                f"✅ *Material processado!*\n\n*{original_name}* foi adicionado à biblioteca.\nFlashcards gerados/atualizados: *{card_count}*.\n\nEnvie *biblioteca* para selecionar o material ou *exercicios 5* para praticar.",
            )
        except Exception:
            pass
    else:
        try:
            send_whatsapp_text(
                db,
                phone,
                f"⚠️ Não consegui finalizar *{original_name}* agora.\n\n"
                f"{friendly_document_error(document.error_message)}",
            )
        except Exception:
            pass


def process_incoming_message(message_row_id: int, *, claimed: bool = False) -> None:
    db = SessionLocal()
    try:
        row = db.get(WhatsAppMessage, message_row_id)
        if row is None:
            return
        if claimed:
            if row.status != "processing":
                return
        elif row.status in {"received", "queued"}:
            row.status = "processing"
            row.processing_started_at = utc_now()
            db.commit()
        else:
            return
        data = row.payload or {}
        text = extract_message_text(data)
        phone = row.phone_e164
        profile = get_profile(db)

        if not profile or not profile.verified or profile.phone_e164 != phone:
            handled_link = _handle_link_code(db, phone, text)
            if handled_link:
                row.status = "processed"
            else:
                row.status = "ignored"
                if re.match(r"^(?:vincular|link|confirmar)\s+\d{6}$", text.strip(), flags=re.IGNORECASE):
                    try:
                        send_whatsapp_text(
                            db,
                            phone,
                            "Esse código não é válido ou expirou. Solicite um novo código na tela WhatsApp do OpenStudy e tente novamente.",
                        )
                    except Exception:
                        pass
                elif text.casefold().strip() in {"oi", "olá", "ola", "menu", "ajuda", "help"}:
                    try:
                        send_whatsapp_text(
                            db,
                            phone,
                            "Olá! Para usar o OpenStudy por aqui, primeiro cadastre seu telefone no painel do OpenStudy e envie o código de verificação que receber.",
                        )
                    except Exception:
                        pass
            row.text = "Comando de vinculação recebido" if text.casefold().startswith(("vincular ", "link ", "confirmar ")) else ""
            row.payload = {}
            db.commit()
            return

        profile.last_interaction_at = utc_now()
        db.commit()
        message_type = str(data.get("messageType") or "").casefold()
        if any(kind in message_type for kind in ("document", "image", "audio", "video")):
            _process_document_message(db, profile, phone, data, defer_processing=True, message_row_id=row.id)
            row.status = "processed"
            db.commit()
            return

        if text:
            _handle_user_text(db, profile, phone, text)
        row.status = "processed"
        db.commit()
    except Exception as exc:
        db.rollback()
        row = db.get(WhatsAppMessage, message_row_id)
        if row:
            row.status = "failed"
            row.error_message = str(exc)[:1500]
            db.commit()
            profile = get_profile(db)
            if profile and profile.verified and profile.phone_e164 == row.phone_e164:
                try:
                    send_whatsapp_text(
                        db,
                        row.phone_e164,
                        "Não consegui concluir essa ação agora. Tente novamente em instantes ou envie *menu* para ver os comandos.",
                    )
                except Exception:
                    pass
    finally:
        # Record completion even for link messages and failed tasks; useful for queue diagnostics.
        try:
            db.expire_all()
            final_row = db.get(WhatsAppMessage, message_row_id)
            if final_row and final_row.status in {"processed", "ignored", "failed"} and not final_row.processed_at:
                final_row.processed_at = utc_now()
                db.commit()
        except Exception:
            db.rollback()
        db.close()
