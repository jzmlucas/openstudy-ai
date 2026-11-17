from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Document, DocumentChunk
from app.services.ollama_client import OllamaClient, OllamaError
from app.services.retrieval import retrieve_context


def test_retrieval_uses_lexical_fallback_when_embeddings_unavailable(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    def unavailable(self, texts):
        raise OllamaError("embedding model unavailable")

    monkeypatch.setattr(OllamaClient, "embed", unavailable)

    with Session(engine) as db:
        document = Document(
            original_filename="genai.md",
            stored_path="/tmp/genai.md",
            file_type=".md",
            status="ready",
            rag_status="lexical_only",
        )
        db.add(document)
        db.flush()
        db.add(
            DocumentChunk(
                document_id=document.id,
                chunk_index=0,
                content="RAG combina recuperação de informação com geração por modelos de linguagem.",
                embedding=None,
            )
        )
        db.commit()

        results = retrieve_context(db, "Como funciona RAG e recuperação de informação?", top_k=3)
        assert results
        assert results[0]["method"] == "lexical"
        assert results[0]["citation"] == "D1-C1"
