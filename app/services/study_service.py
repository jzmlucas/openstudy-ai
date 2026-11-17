import logging
import re
import unicodedata
from pathlib import Path

from app.config import settings
from app.database import SessionLocal
from app.models import Document, DocumentChunk, StudyCard
from app.services.agents import AgentOrchestrator
from app.services.document_parser import extract_text_pages
from app.services.learning_engine import sync_concepts
from app.services.ollama_client import OllamaClient
from app.services.text_chunker import chunk_text
from app.services.user_errors import friendly_document_error

log = logging.getLogger(__name__)


def _front_key(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", re.sub(r"[^a-zA-Z0-9 ]+", " ", text.casefold())).strip()[:500]


def process_document(document_id: int, language: str = "pt-BR") -> None:
    """Extrai, indexa e reconcilia cards sem apagar o histórico de aprendizagem."""
    db = SessionLocal()
    try:
        document = db.get(Document, document_id)
        if document is None:
            return
        document.status = "processing"
        document.rag_status = "indexing"
        document.error_message = None
        db.query(DocumentChunk).filter(DocumentChunk.document_id == document_id).delete(synchronize_session=False)
        db.commit()

        file_path = Path(document.stored_path)
        if not file_path.exists():
            raise FileNotFoundError("O arquivo original não foi encontrado no armazenamento.")

        pages = extract_text_pages(file_path.read_bytes(), document.file_type)
        full_text = "\n\n".join(content for _, content in pages)
        if len(full_text) > settings.max_document_chars:
            remaining = settings.max_document_chars
            limited_pages = []
            for page_number, content in pages:
                if remaining <= 0:
                    break
                part = content[:remaining]
                limited_pages.append((page_number, part))
                remaining -= len(part)
            pages = limited_pages
            full_text = "\n\n".join(content for _, content in pages)

        document.content_text = full_text
        document.extracted_chars = len(full_text)
        db.commit()

        chunk_records: list[tuple[int, str]] = []
        for page_number, page_text in pages:
            for chunk in chunk_text(page_text, settings.chunk_size, settings.chunk_overlap):
                chunk_records.append((page_number, chunk))

        chunk_rows = []
        for index, (page_number, chunk) in enumerate(chunk_records):
            row = DocumentChunk(
                document_id=document_id, chunk_index=index, page_number=page_number, content=chunk
            )
            db.add(row)
            chunk_rows.append(row)
        db.commit()

        client = OllamaClient(db=db)
        embedding_successes = 0
        chunk_texts = [content for _, content in chunk_records]
        for start in range(0, len(chunk_texts), 8):
            batch = chunk_texts[start:start + 8]
            try:
                vectors = client.embed(batch)
                for offset, vector in enumerate(vectors):
                    if start + offset < len(chunk_rows):
                        chunk_rows[start + offset].embedding = vector
                        embedding_successes += 1
                db.commit()
            except Exception:
                db.rollback()
                break

        if embedding_successes == len(chunk_records) and chunk_records:
            document.rag_status = "ready"
        elif embedding_successes:
            document.rag_status = "partial"
        else:
            document.rag_status = "lexical_only"
        db.commit()

        generated_cards = AgentOrchestrator(db).document_cards(
            client, chunk_texts, document.original_filename, language=language,
            enabled=settings.agent_batch_documents,
        )
        if not generated_cards:
            raise RuntimeError("O modelo não gerou flashcards válidos. Tente outro modelo ou revise o documento.")

        existing_cards = db.query(StudyCard).filter(StudyCard.document_id == document_id).all()
        by_front = {_front_key(card.front): card for card in existing_cards}
        for card in existing_cards:
            card.archived = True

        created_or_updated = 0
        used_ids: set[int] = set()
        for card_data in generated_cards:
            key = _front_key(card_data.get("front", ""))
            if not key:
                continue
            card = by_front.get(key)
            if card is not None and card.id in used_ids:
                card = None
            if card is None:
                card = StudyCard(document_id=document_id, **card_data)
                db.add(card)
                db.flush()
            else:
                for field in ("front", "back", "explanation", "difficulty", "tags", "source_excerpt"):
                    setattr(card, field, card_data.get(field, getattr(card, field)))
                card.archived = False
                card.version_number = (card.version_number or 1) + 1
            card.concept_key = _front_key(" ".join(card_data.get("tags", [])[:3])) or None
            used_ids.add(card.id)
            created_or_updated += 1

        if created_or_updated == 0:
            raise RuntimeError("Não foi possível reconciliar os flashcards gerados.")

        sync_concepts(db, document_id=document_id)
        document.status = "ready"
        document.error_message = None
        db.commit()

    except Exception as exc:
        db.rollback()
        log.exception("Falha ao processar documento %s", document_id)
        document = db.get(Document, document_id)
        if document is not None:
            document.status = "error"
            document.error_message = friendly_document_error(str(exc))[:1000]
            db.commit()
    finally:
        db.close()
