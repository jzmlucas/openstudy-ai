from app.services.language import language_instruction, normalize_language
from app.services.ollama_client import OllamaClient


def test_normalize_language_supports_locales_and_browser_aliases():
    assert normalize_language("pt-BR") == "pt-BR"
    assert normalize_language("es") == "es"
    assert normalize_language("en") == "en"
    assert normalize_language("en-US") == "en"
    assert normalize_language("unknown") == "pt-BR"


def test_language_instruction_keeps_json_schema_stable():
    english = language_instruction("en")
    spanish = language_instruction("es")
    assert "English" in english
    assert "español" in spanish
    assert "Do not translate JSON keys" in english


def test_json_generation_injects_selected_language_into_system_prompt():
    client = object.__new__(OllamaClient)
    captured = {}

    def fake_chat(messages, operation, json_mode=False):
        captured["system"] = messages[0]["content"]
        return '{"ok": true}'

    client._chat = fake_chat
    result = client._chat_json("Base prompt", "User prompt", "test", language="en")

    assert result == {"ok": True}
    assert "output language is English" in captured["system"]
    assert "Base prompt" in captured["system"]


def test_true_false_exercises_use_selected_locale_labels():
    client = object.__new__(OllamaClient)

    def fake_chat_json(system_prompt, user_prompt, operation, language="pt-BR"):
        return {
            "exercises": [{
                "question": "¿RAG recupera contexto?",
                "kind": "true_false",
                "options": ["Verdadero", "Falso"],
                "correct_answer": "Verdadero",
                "explanation": "La recuperación aporta contexto al modelo.",
                "difficulty": "intermediario",
                "card_ids": [1],
            }]
        }

    client._chat_json = fake_chat_json
    exercises = client.generate_exercises(
        [{"id": 1, "front": "RAG", "back": "Retrieval", "explanation": "", "difficulty": "facil"}],
        count=1,
        difficulty="mista",
        language="es",
    )

    assert exercises[0]["options"] == ["Verdadero", "Falso"]
    assert exercises[0]["correct_answer"] == "Verdadero"
