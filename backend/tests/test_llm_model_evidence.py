from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest
from pydantic import BaseModel

from backend import llm_utils
from backend.model_evidence import (
    ModelEvidenceContext,
    ModelEvidenceError,
    ModelEvidenceLedger,
    ModelEvidenceRecord,
    ModelEvidenceSession,
    activate_model_evidence_session,
    clear_model_evidence_session,
)


class _Decision(BaseModel):
    action: str
    score: float


def _context(sequence: int, *, subject: str = "AAPL") -> ModelEvidenceContext:
    return ModelEvidenceContext(
        decision_at="2026-01-05T15:30:00+00:00",
        call_site="test.decision",
        role="trade_decision",
        subject=subject,
        local_sequence=sequence,
    )


@pytest.fixture(autouse=True)
def _clear_evidence():
    clear_model_evidence_session()
    yield
    clear_model_evidence_session()


def test_plain_record_persists_final_text_without_api_key(monkeypatch):
    session = ModelEvidenceSession(mode="record", arm_id="baseline")
    activate_model_evidence_session(session)
    monkeypatch.setattr(
        llm_utils,
        "_call_with_capture",
        lambda *args, **kwargs: ("BUY", 200, None, None),
    )

    result = llm_utils._call_llm_with_critical_guard(
        "openai",
        "super-secret-key",
        "gpt-test",
        "complete prompt",
        evidence_context=_context(0),
        system_prompt="complete system",
        max_output_tokens=31,
        temperature=0.1,
        attribution_keys={},
    )

    assert result == "BUY"
    record = session.ledger.records[0]
    assert record.outcome == "BUY"
    assert record.envelope["prompt"] == "complete prompt"
    assert record.envelope["system_prompt"] == "complete system"
    assert "super-secret-key" not in str(record.canonical_value())


def test_plain_replay_returns_record_without_provider_call(monkeypatch):
    recorded = ModelEvidenceSession(mode="record", arm_id="baseline")
    activate_model_evidence_session(recorded)
    monkeypatch.setattr(
        llm_utils,
        "_call_with_capture",
        lambda *args, **kwargs: ("HOLD", 200, None, None),
    )
    kwargs = dict(
        evidence_context=_context(1),
        system_prompt="system",
        max_output_tokens=17,
        attribution_keys={},
    )
    llm_utils._call_llm_with_critical_guard("openai", "key", "model", "prompt", **kwargs)
    semantic_id = recorded.ledger.records[0].semantic_id

    replay = ModelEvidenceSession(
        mode="replay",
        ledger=recorded.ledger,
        arm_id="baseline",
        declared_occurrences=frozenset({semantic_id}),
    )
    activate_model_evidence_session(replay)
    monkeypatch.setattr(
        llm_utils,
        "_call_with_capture",
        lambda *args, **kwargs: pytest.fail("provider called during replay"),
    )

    assert (
        llm_utils._call_llm_with_critical_guard(
            "openai", "different-runtime-key", "model", "prompt", **kwargs
        )
        == "HOLD"
    )


def test_structured_record_replay_validates_requested_schema_and_bypasses_cache(monkeypatch):
    recorded = ModelEvidenceSession(mode="record", arm_id="candidate")
    activate_model_evidence_session(recorded)
    observed_kwargs = {}

    def fake_provider(provider, api_key, model, prompt, output_type, **kwargs):
        observed_kwargs.update(kwargs)
        llm_utils._LAST_STRUCTURED_LLM_CALL.data = {
            "ok": True,
            "error": "",
            "attempted_models": [model],
            "effective_model": model,
            "fallback_used": False,
            "raw_json_fallback_used": False,
        }
        return _Decision(action="buy", score=0.75)

    monkeypatch.setattr(llm_utils, "call_structured_llm_by_provider", fake_provider)
    kwargs = dict(
        evidence_context=_context(2),
        system_prompt="system",
        use_prompt_cache=True,
        max_output_tokens=64,
        temperature=0.0,
        attribution_keys={},
    )
    result = llm_utils._call_structured_llm_with_critical_guard(
        "openai", "key", "model", "prompt", _Decision, **kwargs
    )
    assert result == _Decision(action="buy", score=0.75)
    assert observed_kwargs["use_prompt_cache"] is False
    record = recorded.ledger.records[0]
    assert dict(record.outcome) == {"action": "buy", "score": 0.75}

    replay = ModelEvidenceSession(
        mode="replay",
        ledger=recorded.ledger,
        arm_id="candidate",
        declared_occurrences=frozenset({record.semantic_id}),
    )
    activate_model_evidence_session(replay)
    monkeypatch.setattr(
        llm_utils,
        "call_structured_llm_by_provider",
        lambda *args, **kwargs: pytest.fail("provider called during replay"),
    )
    replayed = llm_utils._call_structured_llm_with_critical_guard(
        "openai", "runtime-key", "model", "prompt", _Decision, **kwargs
    )
    assert replayed == _Decision(action="buy", score=0.75)


def test_structured_replay_rejects_payload_that_no_longer_matches_schema(monkeypatch):
    recorded = ModelEvidenceSession(mode="record", arm_id="candidate")
    activate_model_evidence_session(recorded)

    def fake_provider(provider, api_key, model, prompt, output_type, **kwargs):
        llm_utils._LAST_STRUCTURED_LLM_CALL.data = {
            "ok": True,
            "error": "",
            "attempted_models": [model],
            "effective_model": model,
        }
        return _Decision(action="buy", score=0.5)

    monkeypatch.setattr(llm_utils, "call_structured_llm_by_provider", fake_provider)
    kwargs = dict(evidence_context=_context(3), system_prompt="system", attribution_keys={})
    llm_utils._call_structured_llm_with_critical_guard(
        "openai", "key", "model", "prompt", _Decision, **kwargs
    )
    good = recorded.ledger.records[0]
    bad = ModelEvidenceRecord.from_response(
        semantic_id=good.semantic_id,
        envelope=good.envelope,
        context=good.context,
        outcome={"action": "buy"},
        attempted_models=["model"],
        effective_model="model",
        raw_response={"action": "buy"},
    )
    replay = ModelEvidenceSession(
        mode="replay",
        ledger=ModelEvidenceLedger([bad]),
        arm_id="candidate",
        declared_occurrences=frozenset({bad.semantic_id}),
    )
    activate_model_evidence_session(replay)

    with pytest.raises(ModelEvidenceError, match="requested schema"):
        llm_utils._call_structured_llm_with_critical_guard(
            "openai", "key", "model", "prompt", _Decision, **kwargs
        )


def test_noncritical_structured_none_is_recorded_and_replayed(monkeypatch):
    recorded = ModelEvidenceSession(mode="record", arm_id="baseline")
    activate_model_evidence_session(recorded)
    provider_calls = 0

    def fake_provider(provider, api_key, model, prompt, output_type, **kwargs):
        nonlocal provider_calls
        provider_calls += 1
        llm_utils._LAST_STRUCTURED_LLM_CALL.data = {
            "ok": False,
            "error": "schema output was empty",
            "attempted_models": [model],
            "effective_model": model,
        }
        return None

    monkeypatch.setattr(llm_utils, "call_structured_llm_by_provider", fake_provider)
    kwargs = dict(evidence_context=_context(4), system_prompt="system", attribution_keys={})
    assert (
        llm_utils._call_structured_llm_with_critical_guard(
            "openai", "key", "model", "prompt", _Decision, **kwargs
        )
        is None
    )
    record = recorded.ledger.records[0]
    assert record.response_metadata["outcome_is_none"] is True

    replay = ModelEvidenceSession(
        mode="replay",
        ledger=recorded.ledger,
        arm_id="baseline",
        declared_occurrences=frozenset({record.semantic_id}),
    )
    activate_model_evidence_session(replay)
    assert (
        llm_utils._call_structured_llm_with_critical_guard(
            "openai", "key", "model", "prompt", _Decision, **kwargs
        )
        is None
    )
    assert provider_calls == 1


def test_enabled_evidence_requires_caller_context_before_provider(monkeypatch):
    activate_model_evidence_session(ModelEvidenceSession(mode="record", arm_id="baseline"))
    monkeypatch.setattr(
        llm_utils,
        "_call_with_capture",
        lambda *args, **kwargs: pytest.fail("provider called without context"),
    )
    with pytest.raises(ModelEvidenceError, match="evidence_context"):
        llm_utils._call_llm_with_critical_guard(
            "openai", "key", "model", "prompt", attribution_keys={}
        )


def test_structured_record_canonicalizes_callable_tool_contract(monkeypatch):
    session = ModelEvidenceSession(mode="record", arm_id="baseline")
    activate_model_evidence_session(session)

    def lookup_company(ticker: str) -> list[dict]:
        """Return public company matches for a ticker."""
        return []

    def fake_provider(provider, api_key, model, prompt, output_type, **kwargs):
        llm_utils._LAST_STRUCTURED_LLM_CALL.data = {
            "ok": True,
            "error": "",
            "attempted_models": [model],
            "effective_model": model,
        }
        return _Decision(action="hold", score=0.0)

    monkeypatch.setattr(llm_utils, "call_structured_llm_by_provider", fake_provider)
    llm_utils._call_structured_llm_with_critical_guard(
        "openai",
        "key",
        "model",
        "Today is 2026-01-05.",
        _Decision,
        evidence_context=_context(5),
        tools=[lookup_company],
        attribution_keys={},
    )

    tool = session.ledger.records[0].envelope["tools"][0]
    assert tool["name"].endswith("lookup_company")
    assert tool["signature"] == "(ticker: 'str') -> 'list[dict]'"
    assert tool["description"] == "Return public company matches for a ticker."


def test_concurrent_worker_contexts_record_distinct_semantic_occurrences(monkeypatch):
    session = ModelEvidenceSession(mode="record", arm_id="baseline")
    activate_model_evidence_session(session)
    monkeypatch.setattr(
        llm_utils,
        "_call_with_capture",
        lambda provider, api_key, model, prompt, **kwargs: (prompt, 200, None, None),
    )

    def invoke(index: int) -> str:
        return llm_utils._call_llm_with_critical_guard(
            "openai",
            "key",
            "model",
            "same prompt",
            evidence_context=_context(index, subject=f"batch-{index}"),
            attribution_keys={},
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(invoke, range(8))) == ["same prompt"] * 8
    assert len(session.ledger.records) == 8
    assert len({row.semantic_id for row in session.ledger.records}) == 8
