"""Contract tests for deterministic, immutable model-evidence primitives."""
from __future__ import annotations

import datetime as dt
import threading

import pytest

from backend.model_evidence import (
    ModelEvidenceContext,
    ModelEvidenceError,
    ModelEvidenceLedger,
    ModelEvidenceRecord,
    ModelEvidenceSession,
    activate_model_evidence_session,
    canonical_request_envelope,
    clear_model_evidence_session,
    get_model_evidence_session,
    semantic_request_id,
)


def _envelope(**overrides):
    values = {
        "requested_provider": "openrouter",
        "requested_model": "openai/gpt-5",
        "adapter_identity": "backend.llm_utils.openrouter",
        "prompt": [{"role": "user", "content": "Analyse AAPL."}],
        "system_prompt": "Return concise JSON.",
        "schema": b'{"type":"object"}',
        "tools": [{"type": "function", "function": {"name": "lookup"}}],
        "tool_choice": {"type": "function", "function": {"name": "lookup"}},
        "generation_settings": {"temperature": 0, "max_tokens": 128},
        "fallback_policy": {"allow_fallback": True, "models": ["openai/gpt-4.1"]},
        "canonicalization_version": "model-evidence-v1",
    }
    values.update(overrides)
    return canonical_request_envelope(**values)


def _context(sequence=0, *, subject="AAPL"):
    return ModelEvidenceContext(
        decision_at=dt.datetime(2025, 1, 2, 14, 30, tzinfo=dt.timezone.utc),
        call_site="graph_nexus.sentiment",
        role="sentiment",
        subject=subject,
        local_sequence=sequence,
    )


def _record(sequence=0, *, outcome={"sentiment": "bullish"}):
    envelope = _envelope()
    return ModelEvidenceRecord.from_response(
        semantic_id=semantic_request_id(envelope, context=_context(sequence)),
        envelope=envelope,
        outcome=outcome,
        attempted_models=("openai/gpt-5",),
        effective_model="openai/gpt-5",
        raw_response='{"sentiment":"bullish"}',
        fallback_state="not_used",
    )


def test_canonical_identity_covers_all_request_affecting_fields():
    baseline = _envelope()
    changed_temperature = _envelope(generation_settings={"temperature": 0.1, "max_tokens": 128})
    changed_schema = _envelope(schema=b'{"type":"array"}')

    assert baseline == _envelope()
    assert baseline["schema_bytes"] == "eyJ0eXBlIjoib2JqZWN0In0="
    assert baseline != changed_temperature
    assert baseline != changed_schema


@pytest.mark.parametrize("secret_key", ["api_key", "nestedSecret", "Authorization"])
def test_secret_bearing_request_values_are_rejected_recursively(secret_key):
    with pytest.raises(ModelEvidenceError, match="secret"):
        _envelope(generation_settings={"nested": {secret_key: "never-record-me"}})


def test_occurrence_key_requires_complete_context_and_changes_per_logical_call():
    envelope = _envelope()
    first = semantic_request_id(envelope, context=_context(0))
    second = semantic_request_id(envelope, context=_context(1))

    assert first != second
    assert _context(0).occurrence_key != _context(1).occurrence_key
    with pytest.raises(ModelEvidenceError, match="call_site"):
        ModelEvidenceContext(
            decision_at=dt.datetime(2025, 1, 2, tzinfo=dt.timezone.utc),
            call_site="",
            role="sentiment",
            subject="AAPL",
            local_sequence=0,
        )


def test_semantic_ids_are_independent_of_concurrent_completion_order():
    envelope = _envelope()
    expected = {sequence: semantic_request_id(envelope, context=_context(sequence)) for sequence in range(8)}
    completed = {}
    lock = threading.Lock()

    def worker(sequence):
        result = semantic_request_id(envelope, context=_context(sequence))
        with lock:
            completed[sequence] = result

    threads = [threading.Thread(target=worker, args=(sequence,)) for sequence in reversed(range(8))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert completed == expected


def test_republishing_an_identical_immutable_row_is_idempotent():
    ledger = ModelEvidenceLedger()
    record = _record()

    assert ledger.publish(record) is record
    assert ledger.publish(record) is record
    assert ledger.records == (record,)


def test_distinct_occurrences_with_the_same_prompt_are_consumed_separately():
    ledger = ModelEvidenceLedger()
    first, second = _record(0), _record(1)
    ledger.publish(first)
    ledger.publish(second)
    session = ModelEvidenceSession(
        mode="replay",
        ledger=ledger,
        arm_id="candidate-a",
        declared_occurrences={first.semantic_id, second.semantic_id},
    )

    assert session.replay(first.semantic_id) == {"sentiment": "bullish"}
    assert session.replay(second.semantic_id) == {"sentiment": "bullish"}
    session.finalize()


def test_none_outcome_is_a_successful_replayable_response():
    record = _record(outcome=None)
    session = ModelEvidenceSession(
        mode="replay",
        ledger=ModelEvidenceLedger([record]),
        arm_id="baseline",
        declared_occurrences={record.semantic_id},
    )

    assert session.replay(record.semantic_id) is None
    assert session.finalize()["consumed_occurrences"] == [record.semantic_id]


def test_record_extend_reuses_union_row_and_records_only_new_branch_row():
    shared, branch_only = _record(0), _record(1)
    ledger = ModelEvidenceLedger([shared])
    session = ModelEvidenceSession(mode="record_extend", ledger=ledger, arm_id="candidate-a")

    assert session.resolve(shared) == {"sentiment": "bullish"}
    assert session.resolve(branch_only) == {"sentiment": "bullish"}
    receipt = session.finalize()

    assert {row.semantic_id for row in ledger.records} == {shared.semantic_id, branch_only.semantic_id}
    assert receipt["replayed_occurrences"] == [shared.semantic_id]
    assert receipt["recorded_occurrences"] == [branch_only.semantic_id]


def test_replay_fails_closed_on_missing_or_over_consumed_occurrences():
    record = _record()
    session = ModelEvidenceSession(
        mode="replay",
        ledger=ModelEvidenceLedger([record]),
        arm_id="baseline",
        declared_occurrences={record.semantic_id},
    )

    with pytest.raises(ModelEvidenceError, match="miss"):
        session.replay("f" * 64)
    assert session.replay(record.semantic_id) == {"sentiment": "bullish"}
    with pytest.raises(ModelEvidenceError, match="over-consumption"):
        session.replay(record.semantic_id)


def test_finalize_rejects_unused_records_for_the_current_arm_only():
    own, another_arm = _record(0), _record(1)
    ledger = ModelEvidenceLedger([own, another_arm])
    session = ModelEvidenceSession(
        mode="replay",
        ledger=ledger,
        arm_id="baseline",
        declared_occurrences={own.semantic_id},
    )

    with pytest.raises(ModelEvidenceError, match="unused"):
        session.finalize()
    assert session.replay(own.semantic_id) == {"sentiment": "bullish"}
    session.finalize()


def test_ledger_import_rejects_content_hash_tampering():
    record = _record()
    exported = ModelEvidenceLedger([record]).export()
    exported["records"][0]["response_metadata"]["effective_model"] = "attacker/model"

    with pytest.raises(ModelEvidenceError, match="hash"):
        ModelEvidenceLedger.from_export(exported)


def test_active_session_is_shared_by_worker_threads_and_can_be_cleared():
    clear_model_evidence_session()
    session = ModelEvidenceSession(mode="off")
    seen = []
    lock = threading.Lock()

    activate_model_evidence_session(session)
    threads = [
        threading.Thread(target=lambda: (lock.acquire(), seen.append(get_model_evidence_session()), lock.release()))
        for _ in range(4)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    clear_model_evidence_session()

    assert seen == [session] * 4
    assert get_model_evidence_session() is None
