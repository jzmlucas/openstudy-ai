"""Multiagent routing, bounded costs, batching and instrumentation without a running model."""
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import LLMCallLog
from app.services.agents import (
    AGENTS,
    AgentOrchestrator,
    agent_catalog,
    agent_for,
    generate_document_cards,
    group_document_chunks,
    specialize_prompt,
    tag_operation,
)


def test_router_has_unique_roles_and_no_extra_llm_router():
    operations = [op for agent in AGENTS for op in agent.operations]
    assert len(operations) == len(set(operations))
    assert agent_for('generate_cards').id == 'documentos'
    assert agent_for('rag_answer').id == 'tutor'
    assert agent_for('grade_answer').id == 'avaliador'
    assert agent_for('generate_exercises').id == 'exercicios'
    assert agent_for('study_agent_plan').id == 'planejador'
    assert agent_for('embedding').id == 'biblioteca'
    assert tag_operation('rag_answer') == 'agent:tutor:rag_answer'
    assert tag_operation('unknown') == 'unknown'
    assert len(agent_catalog()) == 6


def test_prompt_specialization_but_unknown_operation_unchanged():
    result = specialize_prompt('grade_answer', 'Avalie com critérios claros.')
    assert 'Avalie com critérios claros.' in result and 'Agente Avaliador' in result
    assert specialize_prompt('noop', 'base') == 'base'


def test_document_batch_preserves_text_and_limits_calls():
    parts = ['A' * 6000, 'B' * 6000, 'C' * 6000, 'D' * 6000, 'E' * 6000]
    groups = group_document_chunks(parts)
    assert groups == [parts[:2], parts[2:4], parts[4:]]
    assert [x for group in groups for x in group] == parts


def test_document_batching_uses_single_inference_per_group():
    calls = []
    class Model:
        def generate_cards(self, text, source, language='pt-BR'):
            calls.append((text, source, language))
            return [{'front': f'Pergunta {len(calls)}?', 'back': 'Resposta'}]
    result = generate_document_cards(Model(), ['A'*4000, 'B'*4000, 'C'*4000], 'aula.md')
    assert len(result) == 2 and len(calls) == 2
    assert 'TRECHO 1/2' in calls[0][0] and 'TRECHO 2/2' in calls[0][0]
    assert calls[0][1] == 'aula.md'


def test_empty_batch_has_individual_fallback():
    calls = []
    class Model:
        def generate_cards(self, text, source, language='pt-BR'):
            calls.append(text)
            return [] if 'TRECHO 1/2' in text else [{'front': 'OK?', 'back': text}]
    cards = generate_document_cards(Model(), ['A'*1000, 'B'*1000], 'doc.md')
    assert len(calls) == 3 and len(cards) == 2


def test_orchestrator_exercises_passes_through_client_factory():
    class FakeModel:
        def __init__(self, db): self.db = db
        def generate_exercises(self, cards, count, difficulty, **kwargs):
            assert cards[0]['id'] == 10 and count == 2
            return [{'question': 'Conteúdo?', 'correct_answer': 'Sim'}]
    assert AgentOrchestrator(object()).exercises([{'id': 10}], 2, 'mista', client_factory=FakeModel)[0]['question'] == 'Conteúdo?'


def test_embedding_cache_avoids_duplicate_network_calls(monkeypatch):
    from app.services import llm_settings
    from app.services.ollama_client import OllamaClient
    monkeypatch.setattr(llm_settings, 'get_active_provider', lambda db: 'ollama')
    monkeypatch.setattr(llm_settings, 'get_provider_credential', lambda db, provider: {'model': 'qwen2.5:7b', 'api_key': None, 'configured': True})
    monkeypatch.setattr(OllamaClient, '_post', lambda self, endpoint, payload, operation, model: {'embeddings': [[0.5, 0.7]]})
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        cli = OllamaClient(db=db)
        key = (cli.base_url, cli.embedding_model, 'query-exclusiva-teste-72918')
        assert cli.embed([key[2]]) == [[0.5, 0.7]]
        def no_network(*a, **k): raise AssertionError('should hit cache')
        monkeypatch.setattr(OllamaClient, '_post', no_network)
        assert cli.embed([key[2]]) == [[0.5, 0.7]]


def test_agent_status_reports_latency_and_errors():
    from app.routers.api import agents_status
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add_all([
            LLMCallLog(operation='agent:tutor:rag_answer', model='qwen2.5:7b', elapsed_ms=220, success=True),
            LLMCallLog(operation='agent:tutor:rag_answer', model='qwen2.5:7b', elapsed_ms=310, success=False),
        ])
        db.commit()
        agents = agents_status(db)['agents']
        tutor = next(agent for agent in agents if agent['id'] == 'tutor')
        assert tutor['metrics'] == {'calls': 2, 'errors': 1, 'mean_ms': 265, 'p95_ms': 310}
