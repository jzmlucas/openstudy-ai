import math
import re
from collections import Counter

from sqlalchemy.orm import Session

from app.config import settings
from app.models import Document, DocumentChunk
from app.services.ollama_client import OllamaClient, OllamaError


TOKEN_RE = re.compile(r"[a-zA-ZÀ-ÿ0-9_]{2,}", re.UNICODE)
STOP_WORDS = {
    "para", "como", "qual", "quais", "uma", "umas", "uns", "dos", "das", "que",
    "com", "por", "sobre", "entre", "ser", "sua", "seu", "mais", "menos", "the",
    "and", "for", "from", "this", "that", "are", "was", "were", "you", "your",
}


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if not left_norm or not right_norm:
        return 0.0
    return dot / (left_norm * right_norm)


def lexical_similarity(query: str, content: str) -> float:
    query_terms = [term.casefold() for term in TOKEN_RE.findall(query) if term.casefold() not in STOP_WORDS]
    content_terms = [term.casefold() for term in TOKEN_RE.findall(content)]
    if not query_terms or not content_terms:
        return 0.0
    query_counts = Counter(query_terms)
    content_counts = Counter(content_terms)
    overlap = sum(min(count, content_counts[term]) for term, count in query_counts.items())
    coverage = overlap / max(1, len(query_counts))
    density = overlap / max(1, len(content_counts) ** 0.5)
    return min(1.0, coverage * 0.75 + density * 0.25)


def retrieve_context(
    db: Session,
    query: str,
    document_ids: list[int] | None = None,
    top_k: int | None = None,
) -> list[dict]:
    top_k = top_k or settings.rag_top_k
    statement = (
        db.query(DocumentChunk, Document)
        .join(Document, Document.id == DocumentChunk.document_id)
        .filter(Document.status == "ready")
    )
    if document_ids:
        statement = statement.filter(Document.id.in_(document_ids))
    rows = statement.order_by(DocumentChunk.document_id, DocumentChunk.chunk_index).limit(5000).all()
    if not rows:
        return []

    query_vector = None
    try:
        query_vector = OllamaClient(db=db).embed([query])[0]
    except OllamaError:
        query_vector = None

    ranked = []
    for chunk, document in rows:
        if query_vector is not None and chunk.embedding:
            score = cosine_similarity(query_vector, chunk.embedding)
            method = "semantic"
        else:
            score = lexical_similarity(query, chunk.content)
            method = "lexical"
        if score > 0:
            ranked.append((score, method, chunk, document))

    ranked.sort(key=lambda item: item[0], reverse=True)
    results = []
    for score, method, chunk, document in ranked[:top_k]:
        threshold = settings.rag_min_similarity if method == "semantic" else 0.015
        if score < threshold:
            continue
        results.append({
            "chunk_id": chunk.id,
            "document_id": document.id,
            "filename": document.original_filename,
            "chunk_index": chunk.chunk_index,
            "page_number": chunk.page_number,
            "content": chunk.content,
            "score": round(float(score), 4),
            "method": method,
            "citation": (
                f"D{document.id}-P{chunk.page_number}-C{chunk.chunk_index + 1}"
                if chunk.page_number else f"D{document.id}-C{chunk.chunk_index + 1}"
            ),
        })
    return results
