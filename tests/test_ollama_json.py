import pytest

from app.services.ollama_client import parse_json_payload


def test_parse_plain_json():
    result = parse_json_payload('{"cards": [{"front": "Pergunta"}]}')
    assert result["cards"][0]["front"] == "Pergunta"


def test_parse_json_inside_markdown_fence():
    result = parse_json_payload('```json\n{"ok": true}\n```')
    assert result["ok"] is True


def test_invalid_json_raises():
    with pytest.raises(ValueError):
        parse_json_payload("não é json")
