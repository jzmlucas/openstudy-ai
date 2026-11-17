from app.services.text_chunker import chunk_text


def test_short_text_returns_single_chunk():
    assert chunk_text("Conteúdo curto") == ["Conteúdo curto"]


def test_long_text_is_split_with_overlap():
    text = ("Conceito importante. " * 500).strip()
    chunks = chunk_text(text, max_chars=1200, overlap=150)
    assert len(chunks) > 1
    assert all(len(chunk) <= 1200 for chunk in chunks)
    assert all(chunk.strip() for chunk in chunks)


def test_empty_text_returns_no_chunks():
    assert chunk_text("   ") == []
