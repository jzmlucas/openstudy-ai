import pytest

from app.services.document_parser import extract_text, validate_extension


def test_markdown_extraction():
    content = b"# Redes neurais\n\nUma rede neural aprende padroes."
    text = extract_text(content, ".md")
    assert "Redes neurais" in text
    assert "aprende padroes" in text


def test_extension_validation():
    assert validate_extension("material.PDF") == ".pdf"
    assert validate_extension("anotacoes.md") == ".md"
    with pytest.raises(ValueError):
        validate_extension("arquivo.exe")


def test_empty_markdown_rejected():
    with pytest.raises(ValueError):
        extract_text(b"   ", ".md")
