from datetime import timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import (
    Document, ErrorDiagnosis, Exercise, ExerciseAttempt, GameChallenge,
    KnowledgeConcept, LearningEvent, RewardEvent, StudentProgress, StudyCard,
    TopicMastery, utc_now,
)
from app.schemas import MatchGameStartRequest, MatchGameSubmitRequest
from app.services.learning_engine import build_daily_quests, claim_daily_quests
from app.services.learning_memory import record_exercise_result
from app.routers import api


def make_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def add_cards(db, count=5, tags=None):
    document = Document(original_filename="study.md", stored_path="/tmp/study.md", file_type=".md")
    db.add(document)
    db.flush()
    cards = []
    for index in range(count):
        card = StudyCard(
            document_id=document.id,
            front=f"Conceito {index + 1}?",
            back=f"Definição {index + 1}",
            explanation=f"Explicação do conceito {index + 1}",
            tags=tags or ["Aprendizagem"],
        )
        db.add(card)
        cards.append(card)
    db.flush()
    return document, cards


def test_daily_quests_require_distinct_learning_actions_and_claim_once():
    engine = make_db()
    with Session(engine) as db:
        _, cards = add_cards(db, 5)
        for card in cards:
            db.add(LearningEvent(event_type="card_review", card_id=card.id, correct=True))
        for index in range(3):
            db.add(LearningEvent(event_type="exercise_attempt", exercise_id=index + 1, correct=True))
        db.add(LearningEvent(event_type="feynman_attempt", correct=True))
        db.commit()

        quests = build_daily_quests(db)
        assert quests["claimable"] is True
        assert [item["progress"] for item in quests["quests"]] == [5, 3, 1]

        reward = claim_daily_quests(db)
        db.commit()
        assert reward["xp_awarded"] == 25
        second = claim_daily_quests(db)
        db.commit()
        assert second["xp_awarded"] == 0
        assert second["already_claimed"] is True


def test_daily_quests_are_not_available_without_active_materials():
    engine = make_db()
    with Session(engine) as db:
        quests = build_daily_quests(db)
        assert quests["available"] is False
        assert quests["quests"] == []


def test_partial_credit_does_not_count_as_fully_correct_when_model_says_false():
    engine = make_db()
    with Session(engine) as db:
        _, cards = add_cards(db, 1)
        exercise = Exercise(
            question="Explique", kind="short_answer", correct_answer="Resposta",
            card_ids=[cards[0].id], topic="Aprendizagem",
        )
        db.add(exercise)
        db.flush()
        attempt = ExerciseAttempt(
            exercise_id=exercise.id, user_answer="Parcial", is_correct=False, score=0.86,
            confidence=5,
        )
        db.add(attempt)
        db.flush()

        record_exercise_result(
            db, exercise.id, [cards[0].id], False, "Parcial",
            score=0.86, confidence=5, error_type="incomplete_answer",
        )
        db.commit()

        progress = db.get(StudentProgress, 1)
        mastery = db.query(TopicMastery).filter_by(topic="Aprendizagem").one()
        event = db.query(LearningEvent).filter_by(event_type="exercise_attempt").one()
        assert progress.total_attempts == 1
        assert progress.total_correct == 0
        assert mastery.correct == 0
        assert event.correct is False
        assert event.event_data["score"] == 0.86


def test_matching_game_is_graded_server_side_and_cannot_be_submitted_twice():
    engine = make_db()
    with Session(engine) as db:
        _, cards = add_cards(db, 4)
        game_data = api.start_matching_game(MatchGameStartRequest(pair_count=4), db)
        game = db.get(GameChallenge, game_data["id"])
        correct_matches = dict(game.answer_key)

        result = api.submit_matching_game(
            game.id, MatchGameSubmitRequest(matches=correct_matches), db
        )
        assert result["correct_pairs"] == 4
        assert result["score"] == 1
        assert result["xp"] == 13
        assert game.completed is True

        try:
            api.submit_matching_game(
                game.id, MatchGameSubmitRequest(matches=correct_matches), db
            )
        except Exception as exc:
            assert getattr(exc, "status_code", None) == 409
        else:
            raise AssertionError("A completed game must not be submitted twice")


def test_matching_game_rejects_duplicate_definition_mapping():
    engine = make_db()
    with Session(engine) as db:
        _, cards = add_cards(db, 3)
        game_data = api.start_matching_game(MatchGameStartRequest(pair_count=3), db)
        game = db.get(GameChallenge, game_data["id"])
        terms = list(game.answer_key)
        first_definition = next(iter(game.answer_key.values()))
        invalid = {terms[0]: first_definition, terms[1]: first_definition, terms[2]: list(game.answer_key.values())[1]}
        try:
            api.submit_matching_game(game.id, MatchGameSubmitRequest(matches=invalid), db)
        except Exception as exc:
            assert getattr(exc, "status_code", None) == 422
        else:
            raise AssertionError("Duplicate definitions must be rejected")


def test_diagnostic_report_groups_attempts_by_topic():
    engine = make_db()
    with Session(engine) as db:
        _, cards = add_cards(db, 3)
        exercise_a = Exercise(
            question="Pergunta A", kind="short_answer", correct_answer="A",
            card_ids=[cards[0].id], purpose="diagnostic", topic="RAG",
        )
        exercise_b = Exercise(
            question="Pergunta B", kind="short_answer", correct_answer="B",
            card_ids=[cards[1].id], purpose="diagnostic", topic="Embeddings",
        )
        db.add_all([exercise_a, exercise_b])
        db.flush()
        db.add_all([
            LearningEvent(
                event_type="exercise_attempt", exercise_id=exercise_a.id, session_id=77,
                correct=False, event_data={"score": 0.2, "error_type": "knowledge_gap"},
            ),
            LearningEvent(
                event_type="exercise_attempt", exercise_id=exercise_b.id, session_id=77,
                correct=True, event_data={"score": 0.9},
            ),
        ])
        db.commit()

        report = api.diagnostic_report(77, db)
        assert report["attempted"] == 2
        assert report["priority_topics"] == ["RAG"]
        assert report["topics"][0]["topic"] == "RAG"
        assert report["topics"][1]["status"] == "strong"


def test_lesson_check_creates_coaching_record_on_incorrect_answer(monkeypatch):
    engine = make_db()

    class FakeClient:
        def __init__(self, db=None):
            self.db = db

        def diagnose_learning_error(self, *args, **kwargs):
            return {
                "score": 0.3, "is_correct": False, "error_type": "misconception",
                "severity": "medium", "explanation": "Você confundiu os conceitos.",
                "misconception": "A e B são iguais.", "intervention": "Compare A e B.",
                "practice_prompt": "Dê um exemplo de cada.", "missing_concepts": ["diferença"],
            }

    monkeypatch.setattr(api, "OllamaClient", FakeClient)
    monkeypatch.setattr(api, "retrieve_context", lambda *args, **kwargs: [])
    with Session(engine) as db:
        _, cards = add_cards(db, 3, tags=["RAG"])
        concept = KnowledgeConcept(
            normalized_key="rag", title="RAG", definition="Definição de RAG",
            simple_explanation="Explicação simples", source_card_ids=[card.id for card in cards],
            source_document_ids=[cards[0].document_id],
            source_excerpt="RAG recupera contexto para apoiar a geração.",
            lesson_content={"check_question": "O que é RAG?", "check_answer": "RAG recupera contexto e gera resposta."},
            lesson_language="pt-BR",
        )
        db.add(concept)
        db.commit()

        result = api.check_learning_lesson(
            api.LessonCheckRequest(topic="RAG", answer="RAG e fine tuning são a mesma coisa.", language="pt-BR"),
            db,
        )
        assert result["correct"] is False
        error = db.query(ErrorDiagnosis).one()
        assert error.error_type == "misconception"
        assert error.intervention == "Compare A e B."


def test_false_string_from_llm_is_not_truthy(monkeypatch):
    from app.services.ollama_client import OllamaClient

    client = object.__new__(OllamaClient)
    client._chat_json = lambda *args, **kwargs: {
        "score": 0.9, "is_correct": "false", "error_type": "incomplete_answer",
        "severity": "medium", "explanation": "Parcial", "intervention": "Complete.",
        "misconception": "", "practice_prompt": "Tente novamente.", "missing_concepts": [],
    }
    result = client.diagnose_learning_error("Q", "A", "Resposta parcial", language="pt-BR")
    assert result["score"] == 0.9
    assert result["is_correct"] is False



def test_error_resolution_requires_two_distinct_successful_remediation_exercises():
    engine = make_db()
    with Session(engine) as db:
        _, cards = add_cards(db, 3, tags=["RAG"])
        source = Exercise(
            question="Questão original", kind="short_answer", correct_answer="Resposta",
            card_ids=[card.id for card in cards], topic="RAG",
        )
        db.add(source)
        db.flush()
        error = ErrorDiagnosis(
            exercise_id=source.id, topic="RAG", error_type="misconception",
            explanation="Confusão entre recuperação e treinamento.",
            intervention="Compare os dois processos.",
        )
        db.add(error)
        db.flush()

        remediation_a = Exercise(
            question="Reforço A", kind="short_answer", correct_answer="A",
            card_ids=[cards[0].id], topic="RAG", purpose="remediation",
            remediation_error_id=error.id,
        )
        db.add(remediation_a)
        db.flush()
        db.add_all([
            ExerciseAttempt(
                exercise_id=remediation_a.id, user_answer="A", is_correct=True, score=1.0
            ),
            ExerciseAttempt(
                exercise_id=remediation_a.id, user_answer="A", is_correct=True, score=1.0
            ),
        ])
        db.commit()

        try:
            api.resolve_learning_error(error.id, db)
        except Exception as exc:
            assert getattr(exc, "status_code", None) == 409
        else:
            raise AssertionError("Two attempts on the same exercise must not resolve the error")

        remediation_b = Exercise(
            question="Reforço B", kind="short_answer", correct_answer="B",
            card_ids=[cards[1].id], topic="RAG", purpose="remediation",
            remediation_error_id=error.id,
        )
        db.add(remediation_b)
        db.flush()
        db.add(ExerciseAttempt(
            exercise_id=remediation_b.id, user_answer="B", is_correct=True, score=1.0
        ))
        db.commit()

        result = api.resolve_learning_error(error.id, db)
        assert result["resolved"] is True
        assert db.get(ErrorDiagnosis, error.id).resolved is True


def test_error_remediation_does_not_fall_back_to_unrelated_cards():
    engine = make_db()
    with Session(engine) as db:
        add_cards(db, 4, tags=["Outro assunto"])
        error = ErrorDiagnosis(
            topic="RAG", error_type="knowledge_gap",
            explanation="Não compreendeu RAG.",
            intervention="Revise recuperação aumentada.",
        )
        db.add(error)
        db.commit()

        try:
            api.create_error_remediation(error.id, "pt-BR", db)
        except Exception as exc:
            assert getattr(exc, "status_code", None) == 400
            assert "relacionados" in str(getattr(exc, "detail", "")).lower()
        else:
            raise AssertionError("The coach must not use unrelated cards as remediation context")



def test_feynman_duplicate_explanation_does_not_duplicate_learning_events_or_xp(monkeypatch):
    engine = make_db()

    class FakeClient:
        def __init__(self, db=None):
            self.db = db

        def evaluate_feynman(self, *args, **kwargs):
            return {
                "overall_score": 0.8,
                "scores": {"accuracy": 4, "clarity": 4, "completeness": 4, "simplicity": 4},
                "strengths": ["Explicação clara."], "missing_concepts": [],
                "misconceptions": [], "priority_improvement": "Adicione um exemplo.",
                "retry_prompt": "Dê um exemplo próprio.", "feedback": "Boa explicação.",
            }

    monkeypatch.setattr(api, "OllamaClient", FakeClient)
    monkeypatch.setattr(api, "retrieve_context", lambda *args, **kwargs: [])
    with Session(engine) as db:
        payload = api.FeynmanRequest(
            topic="RAG", explanation="RAG recupera contexto relevante para apoiar a resposta."
        )
        first = api.evaluate_feynman(payload, db)
        second = api.evaluate_feynman(payload, db)

        assert first["reward"]["xp_awarded"] == 12
        assert first["duplicate_attempt"] is False
        assert second["reward"]["xp_awarded"] == 0
        assert second["duplicate_attempt"] is True
        assert db.query(LearningEvent).filter_by(event_type="feynman_attempt").count() == 1
        assert db.get(StudentProgress, 1).total_attempts == 1
        assert db.query(TopicMastery).filter_by(topic="RAG").one().attempts == 1



def test_diagnostic_assigns_topic_from_related_card_tags_when_model_omits_it(monkeypatch):
    engine = make_db()

    class FakeClient:
        def __init__(self, db=None):
            self.db = db

        def generate_exercises(self, cards, count, difficulty, **kwargs):
            return [
                {
                    "question": f"Pergunta {index + 1}", "kind": "short_answer",
                    "options": [], "correct_answer": "Resposta",
                    "explanation": "Explicação", "difficulty": "intermediario",
                    "card_ids": [cards[index]["id"]], "purpose": "diagnostic",
                    "bloom_level": "understand", "topic": "", "hints": [],
                    "source_excerpt": "", "objective": "Diagnosticar o conceito.",
                }
                for index in range(3)
            ]

    monkeypatch.setattr(api, "OllamaClient", FakeClient)
    with Session(engine) as db:
        _, cards = add_cards(db, 3, tags=["RAG"])
        result = api.create_diagnostic(
            api.DiagnosticRequest(topic="", count=3, language="pt-BR"), db
        )
        assert len(result) == 3
        saved = db.query(Exercise).filter_by(purpose="diagnostic").all()
        assert {item.topic for item in saved} == {"RAG"}



def test_lesson_cache_invalidates_when_error_identity_changes_but_count_stays_same(monkeypatch):
    engine = make_db()
    calls = {"count": 0}

    class FakeClient:
        def __init__(self, db=None):
            self.db = db

        def generate_lesson(self, topic, contexts, cards, **kwargs):
            calls["count"] += 1
            return {
                "title": topic, "learning_goal": "Compreender RAG.",
                "simple_explanation": "Explicação simples.",
                "analogy": "Analogia.", "technical_explanation": "Explicação técnica.",
                "worked_example": "Exemplo.", "real_world_application": "Aplicação.",
                "common_mistakes": [], "prerequisites": [],
                "check_question": "O que é RAG?",
                "check_answer": "RAG recupera contexto relevante.",
                "source_notes": [],
            }

    monkeypatch.setattr(api, "OllamaClient", FakeClient)
    monkeypatch.setattr(api, "retrieve_context", lambda *args, **kwargs: [])
    with Session(engine) as db:
        _, cards = add_cards(db, 3, tags=["RAG"])
        concept = KnowledgeConcept(
            normalized_key="rag", title="RAG", definition="Definição de RAG",
            source_card_ids=[card.id for card in cards],
            source_document_ids=[cards[0].document_id],
            source_excerpt="RAG recupera contexto relevante.",
        )
        first_error = ErrorDiagnosis(topic="RAG", error_type="knowledge_gap", explanation="Erro A")
        db.add_all([concept, first_error])
        db.commit()

        payload = api.LearningTopicRequest(topic="RAG", language="pt-BR")
        first = api.generate_learning_lesson(payload, db)
        second = api.generate_learning_lesson(payload, db)
        assert first["cached"] is False
        assert second["cached"] is True
        assert calls["count"] == 1

        db.delete(first_error)
        db.add(ErrorDiagnosis(topic="RAG", error_type="misconception", explanation="Erro B"))
        db.commit()

        third = api.generate_learning_lesson(payload, db)
        assert third["cached"] is False
        assert calls["count"] == 2



def test_matching_game_rejects_extra_unknown_identifiers():
    engine = make_db()
    with Session(engine) as db:
        add_cards(db, 3)
        game_data = api.start_matching_game(MatchGameStartRequest(pair_count=3), db)
        game = db.get(GameChallenge, game_data["id"])
        invalid = dict(game.answer_key)
        invalid["unknown-term"] = next(iter(game.answer_key.values()))
        try:
            api.submit_matching_game(game.id, MatchGameSubmitRequest(matches=invalid), db)
        except Exception as exc:
            assert getattr(exc, "status_code", None) == 422
        else:
            raise AssertionError("Unknown identifiers must be rejected")
