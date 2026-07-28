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
    context = _context(sequence)
    return ModelEvidenceRecord.from_response(
        semantic_id=semantic_request_id(envelope, context=context),
        envelope=envelope,
        context=context,
        outcome=outcome,
        attempted_models=("openai/gpt-5",),
        effective_model="openai/gpt-5",
        raw_response='{"sentiment":"bullish"}',
        fallback_state="not_used",
    )


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("requested_provider", "bedrock"),
        ("requested_model", "anthropic/claude-sonnet"),
        ("adapter_identity", "backend.llm_utils.bedrock"),
        ("prompt", [{"role": "user", "content": "Analyse MSFT."}]),
        ("system_prompt", "Return a plain-text rationale."),
        ("schema", b'{"type":"array"}'),
        ("tools", [{"type": "function", "function": {"name": "news"}}]),
        ("tool_choice", "none"),
        ("generation_settings", {"temperature": 0.1, "max_tokens": 128}),
        ("fallback_policy", {"allow_fallback": False, "models": []}),
        ("canonicalization_version", "model-evidence-v2"),
    ],
)
def test_canonical_identity_covers_every_request_affecting_field(field, replacement):
    baseline = _envelope()
    changed = _envelope(**{field: replacement})

    assert baseline != changed
    assert semantic_request_id(baseline, context=_context()) != semantic_request_id(changed, context=_context())


def test_canonical_identity_stores_schema_as_bytes_and_requires_complete_envelope():
    baseline = _envelope()

    assert baseline == _envelope()
    assert baseline["schema_bytes"] == "eyJ0eXBlIjoib2JqZWN0In0="
    with pytest.raises(ModelEvidenceError, match="canonical envelope"):
        semantic_request_id({}, context=_context())


@pytest.mark.parametrize(
    "secret_key",
    ["api_key", "nestedSecret", "Authorization", "accessToken", "refreshToken", "idToken"],
)
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


def test_direct_record_constructor_rejects_unverified_identity_and_incomplete_metadata():
    envelope = _envelope()
    context = _context()
    with pytest.raises(ModelEvidenceError, match="semantic_id"):
        ModelEvidenceRecord(
            semantic_id="0" * 64,
            envelope=envelope,
            context=context,
            outcome={"sentiment": "bullish"},
            response_metadata={},
        )
    with pytest.raises(ModelEvidenceError, match="response_metadata"):
        ModelEvidenceRecord(
            semantic_id=semantic_request_id(envelope, context=context),
            envelope=envelope,
            context=context,
            outcome={"sentiment": "bullish"},
            response_metadata={},
        )
    with pytest.raises(ModelEvidenceError, match="secret"):
        ModelEvidenceRecord(
            semantic_id=semantic_request_id(envelope, context=context),
            envelope=envelope,
            context=context,
            outcome={"refreshToken": "never-persist"},
            response_metadata=_record().canonical_value()["response_metadata"],
        )


@pytest.mark.parametrize(
    ("field", "replacement", "error"),
    [
        ("attempted_models", [], "attempted_models"),
        ("effective_model", "other/model", "effective_model"),
        ("raw_response_hash", "not-a-hash", "raw_response_hash"),
        ("validated_response_hash", "0" * 64, "validated_response_hash"),
        ("fallback_state", "", "fallback_state"),
        ("successful", False, "successful"),
        ("outcome_is_none", True, "outcome_is_none"),
    ],
)
def test_direct_record_constructor_validates_every_required_response_field(field, replacement, error):
    record = _record()
    metadata = record.canonical_value()["response_metadata"]
    metadata[field] = replacement

    with pytest.raises(ModelEvidenceError, match=error):
        ModelEvidenceRecord(
            semantic_id=record.semantic_id,
            envelope=record.canonical_value()["envelope"],
            context=record.context,
            outcome={"sentiment": "bullish"},
            response_metadata=metadata,
        )


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
        declared_occurrences=frozenset({first.semantic_id, second.semantic_id}),
    )

    assert session.replay(first.semantic_id) == {"sentiment": "bullish"}
    assert session.replay(second.semantic_id) == {"sentiment": "bullish"}
    session.finalize()


def test_record_dynamically_observes_reserved_provider_occurrences():
    record = _record()
    session = ModelEvidenceSession(mode="record", arm_id="baseline")

    reservation = session.reserve(record.semantic_id)
    assert reservation.replay_hit is False
    assert reservation.provider_required is True
    assert session.record(record) == {"sentiment": "bullish"}
    receipt = session.finalize()

    assert receipt["observed_occurrences"] == (record.semantic_id,)
    assert receipt["recorded_occurrences"] == (record.semantic_id,)


def test_off_reservation_and_completion_are_no_ops():
    record = _record()
    ledger = ModelEvidenceLedger()
    session = ModelEvidenceSession(mode="off", ledger=ledger)

    reservation = session.reserve(record.semantic_id)
    assert reservation.replay_hit is False
    assert reservation.provider_required is True
    assert session.record(record) == {"sentiment": "bullish"}
    assert session.finalize() == {"mode": "off"}
    assert ledger.records == ()


def test_none_outcome_is_a_successful_replayable_response():
    record = _record(outcome=None)
    session = ModelEvidenceSession(
        mode="replay",
        ledger=ModelEvidenceLedger([record]),
        arm_id="baseline",
        declared_occurrences=frozenset({record.semantic_id}),
    )

    reservation = session.reserve(record.semantic_id)
    assert reservation.replay_hit is True
    assert reservation.provider_required is False
    assert reservation.outcome is None
    assert session.finalize()["consumed_occurrences"] == (record.semantic_id,)


def test_record_extend_atomically_replays_union_hit_and_reserves_only_union_miss():
    shared, branch_only = _record(0), _record(1)
    ledger = ModelEvidenceLedger([shared])
    session = ModelEvidenceSession(
        mode="record_extend",
        ledger=ledger,
        arm_id="candidate-a",
    )

    shared_reservation = session.reserve(shared.semantic_id)
    branch_reservation = session.reserve(branch_only.semantic_id)
    assert shared_reservation.replay_hit is True
    assert shared_reservation.outcome == {"sentiment": "bullish"}
    assert branch_reservation.provider_required is True
    assert session.record(branch_only) == {"sentiment": "bullish"}
    receipt = session.finalize()

    assert {row.semantic_id for row in ledger.records} == {shared.semantic_id, branch_only.semantic_id}
    assert receipt["observed_occurrences"] == tuple(sorted({shared.semantic_id, branch_only.semantic_id}))
    assert receipt["replayed_occurrences"] == (shared.semantic_id,)
    assert receipt["recorded_occurrences"] == (branch_only.semantic_id,)


def test_concurrent_pre_provider_reservation_claims_one_provider_slot():
    record = _record()
    session = ModelEvidenceSession(mode="record", arm_id="baseline")
    barrier = threading.Barrier(2)
    reserved, rejected = [], []

    def reserve_once():
        barrier.wait()
        try:
            reserved.append(session.reserve(record.semantic_id))
        except ModelEvidenceError as exc:
            rejected.append(str(exc))

    threads = [threading.Thread(target=reserve_once) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(reserved) == 1
    assert reserved[0].provider_required is True
    assert any("already reserved" in error for error in rejected)
    session.record(record)
    session.finalize()


def test_record_rejects_unreserved_and_duplicate_provider_completion():
    record = _record()
    session = ModelEvidenceSession(mode="record", arm_id="baseline")

    with pytest.raises(ModelEvidenceError, match="unreserved"):
        session.record(record)
    session.reserve(record.semantic_id)
    assert session.record(record) == {"sentiment": "bullish"}
    with pytest.raises(ModelEvidenceError, match="duplicate"):
        session.record(record)
    session.finalize()


def test_record_finalization_rejects_pending_provider_reservations():
    record = _record()
    session = ModelEvidenceSession(mode="record", arm_id="baseline")
    session.reserve(record.semantic_id)

    with pytest.raises(ModelEvidenceError, match="pending"):
        session.finalize()


def test_replay_requires_sealed_declaration_and_fails_on_undeclared_miss_and_unused():
    own, another_arm = _record(0), _record(1)
    ledger = ModelEvidenceLedger([own, another_arm])
    with pytest.raises(ModelEvidenceError, match="declared_occurrences"):
        ModelEvidenceSession(mode="replay", ledger=ledger, arm_id="baseline")
    with pytest.raises(ModelEvidenceError, match="immutable"):
        ModelEvidenceSession(
            mode="replay",
            ledger=ledger,
            arm_id="baseline",
            declared_occurrences={own.semantic_id},
        )

    session = ModelEvidenceSession(
        mode="replay",
        ledger=ledger,
        arm_id="baseline",
        declared_occurrences=frozenset({own.semantic_id}),
    )

    with pytest.raises(ModelEvidenceError, match="undeclared"):
        session.reserve(another_arm.semantic_id)
    missing_session = ModelEvidenceSession(
        mode="replay",
        ledger=ledger,
        arm_id="baseline",
        declared_occurrences=frozenset({"f" * 64}),
    )
    with pytest.raises(ModelEvidenceError, match="miss"):
        missing_session.reserve("f" * 64)
    with pytest.raises(ModelEvidenceError, match="unused"):
        session.finalize()
    assert session.reserve(own.semantic_id).replay_hit is True
    with pytest.raises(ModelEvidenceError, match="over-consumption"):
        session.reserve(own.semantic_id)
    session.finalize()


def test_recording_modes_reject_sealed_replay_declarations():
    for mode in ("record", "record_extend"):
        with pytest.raises(ModelEvidenceError, match="replay mode"):
            ModelEvidenceSession(
                mode=mode,
                arm_id="baseline",
                declared_occurrences=frozenset(),
            )


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

    def worker():
        with lock:
            seen.append(get_model_evidence_session())

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    clear_model_evidence_session()

    assert seen == [session] * 4
    assert get_model_evidence_session() is None
