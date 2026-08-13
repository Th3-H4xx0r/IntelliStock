"""Contract tests for durable deterministic backtest replay evidence."""
from datetime import datetime, timezone
import re

import pytest

from backtest_replay import (
    InMemoryReplayStore,
    ReplayError,
    ExperimentMatrixManifest,
    FixtureBuild,
    ReplayReceipt,
    RethinkReplayStore,
    trade_ledger_hash,
    validate_replay_source,
)
from experiment_registry import ExperimentSpec
from model_evidence import (
    ModelEvidenceContext,
    ModelEvidenceRecord,
    canonical_request_envelope,
    semantic_request_id,
)


NOW = datetime(2026, 7, 28, 9, 30, tzinfo=timezone.utc)


def _source_manifest(name):
    return {
        "manifest_id": name,
        "source_hashes": {name: f"sha256:{name}"},
        "created_at": NOW.isoformat(),
    }


def _benchmark_manifest():
    return {
        "manifest_id": "spy-1",
        "symbol": "SPY",
        "timeframe": "1Day",
        "adjustment": "all",
        "price_field": "c",
        "total_return": True,
        "feed": "iex",
        "start_date": "2024-01-02",
        "end_date": "2026-06-30",
        "valuation_rule": "xnys_session_close",
        "valuation_timestamps": ["2024-01-02T20:00:00Z", "2026-06-30T20:00:00Z"],
        "content_hash": "spy-sha256-" + "4" * 64,
    }


def _spec(experiment_id, *, cost_version="cost-v1", fee_bps=1.0):
    return ExperimentSpec(
        experiment_id=experiment_id,
        search_scope="bull-rally",
        commit_sha="f07d43c",
        source_tree_hash="sha256:tree",
        effective_config={"arm": experiment_id},
        model_provider="openai",
        model_name="research-model",
        prompt_hashes={"decision": "sha256:prompt"},
        model_settings={"temperature": 0.0},
        seed=179,
        predeclared_repeats=3,
        dataset_manifest=_source_manifest("dataset"),
        graph_manifest=_source_manifest("graph"),
        universe_manifest=_source_manifest("universe"),
        benchmark_manifest=_benchmark_manifest(),
        execution_cost_model={
            "version": cost_version,
            "spread_bps": 1.0,
            "slippage_bps": 1.0,
            "fee_bps": fee_bps,
            "latency_seconds": 0.0,
        },
        start_date="2024-01-02",
        end_date="2026-06-30",
        fold="walk-forward-03",
        actor="research-orchestrator",
    )


def _matrix(*, stressed=False):
    baseline = _spec("baseline")
    candidate = _spec("candidate")
    cost_scenarios = {"base": {"baseline": baseline, "candidate": candidate}}
    if stressed:
        cost_scenarios["stressed"] = {
            "baseline": _spec("baseline-stressed", cost_version="cost-v2", fee_bps=4.0),
            "candidate": _spec("candidate-stressed", cost_version="cost-v2", fee_bps=4.0),
        }
    return ExperimentMatrixManifest(
        arms={"baseline": baseline, "candidate": candidate},
        combinations=(("baseline", "candidate"),),
        windows=("walk-forward-03",),
        cost_scenarios=cost_scenarios,
        fixture_count=2,
        arm_recording_order=("baseline", "candidate"),
        trial_count=3,
        bootstrap_seed=991,
        failure_rules={"on_replay_miss": "fail"},
        selection_rule={"metric": "sharpe"},
        implementation_hashes={"engine": "sha256:engine"},
    )


def _record(label="shared", sequence=1, *, outcome=None):
    context = ModelEvidenceContext(
        decision_at="2024-01-02T15:00:00Z",
        call_site="strategy.score",
        role="analyst",
        subject=label,
        local_sequence=sequence,
    )
    envelope = canonical_request_envelope(
        requested_provider="openai",
        requested_model="research-model",
        adapter_identity="adapter-v1",
        prompt=label,
        system_prompt="system",
    )
    semantic_id = semantic_request_id(envelope, context=context)
    return ModelEvidenceRecord.from_response(
        semantic_id=semantic_id,
        envelope=envelope,
        context=context,
        outcome={"label": label} if outcome is None else outcome,
        attempted_models=("research-model",),
        effective_model="research-model",
        raw_response={"label": label},
    )


def _build(matrix, *, store=None, cost_scenario_id="base"):
    if store is None:
        store = InMemoryReplayStore()
        store.publish_matrix(matrix)
    return FixtureBuild(
        matrix=matrix,
        window="walk-forward-03",
        fixture_ordinal=0,
        cost_scenario_id=cost_scenario_id,
        rng_seed_manifest={"simulation": 179},
        benchmark_manifest=_benchmark_manifest(),
        store=store,
    )


def test_matrix_must_be_preregistered_before_fixture_building_and_is_immutable():
    matrix = _matrix()
    store = InMemoryReplayStore()

    with pytest.raises(ReplayError, match="preregistered"):
        _build(matrix, store=store)

    store.publish_matrix(matrix)
    assert _build(matrix, store=store).matrix_id == matrix.matrix_id
    assert ExperimentMatrixManifest(
        **{**matrix.constructor_args(), "bootstrap_seed": 992}
    ).matrix_id != matrix.matrix_id


def test_fixture_build_requires_a_published_store_declared_window_and_reservation():
    matrix = _matrix()
    args = {
        "matrix": matrix,
        "window": "walk-forward-03",
        "fixture_ordinal": 0,
        "cost_scenario_id": "base",
        "rng_seed_manifest": {"simulation": 179},
        "benchmark_manifest": _benchmark_manifest(),
    }
    with pytest.raises(ReplayError, match="durable store"):
        FixtureBuild(**args)
    store = InMemoryReplayStore()
    with pytest.raises(ReplayError, match="preregistered"):
        FixtureBuild(**args, store=store)
    store.publish_matrix(matrix)
    with pytest.raises(ReplayError, match="window"):
        FixtureBuild(**{**args, "window": "undeclared-window"}, store=store)
    build = FixtureBuild(**args, store=store)
    assert store.reserved_build(build.build_id)["matrix_id"] == matrix.matrix_id


def test_fixture_build_id_is_preregistered_and_sealed_id_is_content_addressed():
    matrix = _matrix()
    first = _build(matrix)
    second = _build(matrix)
    assert first.build_id == second.build_id

    first.record_pit("2024-01-02T15:00:00Z", {"as_of": "2024-01-02T14:59:00Z", "source_hash": "sha256:pit"})
    fixture = first.seal()
    assert fixture.fixture_id != first.build_id
    assert fixture.fixture_id == first.seal().fixture_id


def test_pit_chain_preserves_decision_order_and_rejects_future_visibility():
    build = _build(_matrix())
    build.record_pit("2024-01-02T15:00:00Z", {"as_of": "2024-01-02T14:59:00Z", "source_hash": "sha256:one"})
    build.record_pit("2024-01-03T15:00:00Z", {"as_of": "2024-01-03T14:59:00Z", "source_hash": "sha256:two"})
    assert [entry["decision_at"] for entry in build.seal().pit_chain] == [
        "2024-01-02T15:00:00Z", "2024-01-03T15:00:00Z"
    ]
    with pytest.raises(ReplayError, match="as_of"):
        _build(_matrix()).record_pit(
            "2024-01-02T15:00:00Z",
            {"as_of": "2024-01-02T15:01:00Z", "source_hash": "sha256:future"},
        )


def test_common_rows_are_identical_and_branch_only_rows_do_not_cross_arm_audits():
    build = _build(_matrix())
    shared = _record("shared")
    branch_only = _record("candidate", 2)
    build.record_model_row("baseline", shared)
    build.record_model_row("candidate", shared)
    build.record_model_row("candidate", branch_only)
    with pytest.raises(ReplayError, match="divergent immutable row"):
        build.record_model_row("baseline", _record("shared", 1, outcome={"label": "changed"}))
    fixture = build.seal()
    assert fixture.request_set("baseline") == frozenset({shared.semantic_id})
    assert fixture.request_set("candidate") == frozenset({shared.semantic_id, branch_only.semantic_id})


def test_cost_scenario_is_part_of_build_identity_and_arm_request_set_identity():
    matrix = _matrix(stressed=True)
    store = InMemoryReplayStore()
    store.publish_matrix(matrix)
    assert _build(matrix, store=store, cost_scenario_id="base").build_id != _build(
        matrix, store=store, cost_scenario_id="stressed"
    ).build_id
    fixture = _build(matrix, store=store, cost_scenario_id="stressed").seal()
    experiment = matrix.experiment_for("baseline", "stressed")
    receipt = ReplayReceipt(
        matrix=matrix,
        arm_name="baseline",
        arm_id=matrix.arm_id("baseline"),
        fixture=fixture,
        experiment=experiment,
        executed_source_tree_hash="sha256:tree",
        dependency_runtime_digest="sha256:" + "a" * 64,
        executed_cost_model_hash=experiment.execution_cost_model_hash,
        trade_ledger_hash=trade_ledger_hash([], []),
        replay_audit={"complete": True, "ledger_content_hash": fixture.model_ledger_hash},
        pit_audit=True,
        execution_audit=True,
        benchmark_audit=True,
        accounting_audit=True,
    )
    assert receipt.promotion_eligible is True


def test_store_publishes_calls_before_fixture_and_receipt_and_rejects_divergence():
    store = InMemoryReplayStore()
    matrix = _matrix()
    store.publish_matrix(matrix)
    build = _build(matrix, store=store)
    row = _record()
    build.record_model_row("baseline", row)
    fixture = build.seal()
    store.publish_fixture(fixture)
    assert store.write_order == ("matrix", "build", "call", "fixture")

    receipt = ReplayReceipt(
        matrix=matrix,
        arm_name="baseline",
        arm_id=matrix.arm_id("baseline"),
        fixture=fixture,
        experiment=matrix.arms["baseline"],
        executed_source_tree_hash="sha256:tree",
        dependency_runtime_digest="sha256:" + "a" * 64,
        executed_cost_model_hash=matrix.arms["baseline"].execution_cost_model_hash,
        trade_ledger_hash=trade_ledger_hash([{"id": "decision-1"}], [{"id": "fill-1"}]),
        replay_audit={"complete": True, "ledger_content_hash": fixture.model_ledger_hash},
        pit_audit=True,
        execution_audit=True,
        benchmark_audit=True,
        accounting_audit=True,
    )
    store.publish_receipt(receipt)
    assert store.write_order[-1] == "receipt"
    assert receipt.promotion_eligible is True
    assert receipt.to_doc()["arm_id"] == matrix.arm_id("baseline")

    with pytest.raises(ReplayError, match="arm ID"):
        ReplayReceipt(
            matrix=matrix,
            arm_name="baseline",
            arm_id=matrix.arm_id("candidate"),
            fixture=fixture,
            experiment=matrix.arms["baseline"],
            executed_source_tree_hash="sha256:tree",
            dependency_runtime_digest="sha256:" + "a" * 64,
            executed_cost_model_hash=matrix.arms["baseline"].execution_cost_model_hash,
            trade_ledger_hash=trade_ledger_hash([], []),
            replay_audit={"complete": True},
            pit_audit=True,
            execution_audit=True,
            benchmark_audit=True,
            accounting_audit=True,
        )


def test_receipts_are_promotion_ineligible_until_every_audit_passes_and_reject_bad_hashes():
    matrix = _matrix()
    fixture = _build(matrix).seal()
    receipt = ReplayReceipt(
        matrix=matrix, arm_name="baseline", arm_id=matrix.arm_id("baseline"), fixture=fixture,
        experiment=matrix.arms["baseline"], executed_source_tree_hash="sha256:tree",
        dependency_runtime_digest="sha256:" + "a" * 64,
        executed_cost_model_hash=matrix.arms["baseline"].execution_cost_model_hash,
        trade_ledger_hash=trade_ledger_hash([], []), replay_audit={"complete": False},
        pit_audit=True, execution_audit=True, benchmark_audit=True, accounting_audit=True,
    )
    assert receipt.promotion_eligible is False
    with pytest.raises(ReplayError, match="trade ledger hash"):
        ReplayReceipt(
            matrix=matrix, arm_name="baseline", arm_id=matrix.arm_id("baseline"), fixture=fixture,
            experiment=matrix.arms["baseline"], executed_source_tree_hash="sha256:tree",
            dependency_runtime_digest="sha256:" + "a" * 64,
            executed_cost_model_hash=matrix.arms["baseline"].execution_cost_model_hash,
            trade_ledger_hash="ledger", replay_audit={"complete": False},
            pit_audit=True, execution_audit=True, benchmark_audit=True, accounting_audit=True,
        )
    with pytest.raises(ReplayError, match="dependency_runtime_digest"):
        ReplayReceipt(
            matrix=matrix, arm_name="baseline", arm_id=matrix.arm_id("baseline"), fixture=fixture,
            experiment=matrix.arms["baseline"], executed_source_tree_hash="sha256:tree",
            dependency_runtime_digest="runtime",
            executed_cost_model_hash=matrix.arms["baseline"].execution_cost_model_hash,
            trade_ledger_hash=trade_ledger_hash([], []), replay_audit={"complete": False},
            pit_audit=True, execution_audit=True, benchmark_audit=True, accounting_audit=True,
        )


def test_source_manifest_is_required_for_dirty_or_untracked_executed_trees():
    with pytest.raises(ReplayError, match="content manifest"):
        validate_replay_source(source_tree_hash="sha256:tree", dirty=True)
    manifest = {"files": {"engine.py": "sha256:file"}}
    assert validate_replay_source(
        source_tree_hash="sha256:tree", dirty=True, content_manifest=manifest
    ).startswith("source-content-sha256-")


def test_trade_ledger_hash_is_order_sensitive_and_canonical():
    first = trade_ledger_hash([{"id": "a"}, {"id": "b"}], [{"id": "fill"}])
    assert first == trade_ledger_hash([{"id": "a"}, {"id": "b"}], [{"id": "fill"}])
    assert first != trade_ledger_hash([{"id": "b"}, {"id": "a"}], [{"id": "fill"}])
    assert re.fullmatch(r"trade-ledger-sha256-[0-9a-f]{64}", first)


def test_invalid_fixture_never_persists_a_manifest_and_receipt_requires_fixture():
    class Backend:
        def __init__(self):
            self.rows = {}
            self.writes = []

        def insert_record(self, table, doc, *, durability):
            self.writes.append(table)
            prior = self.rows.get((table, doc["id"]))
            if prior is None:
                self.rows[(table, doc["id"])] = dict(doc)
            return prior

        def get_record(self, table, record_id):
            return self.rows.get((table, record_id))

    matrix = _matrix()
    store = RethinkReplayStore(Backend())
    store.publish_matrix(matrix)
    build = _build(matrix, store=store)
    fixture = build.seal()
    fixture_records = object.__getattribute__(fixture, "records")
    object.__setattr__(fixture, "model_ledger_hash", "not-the-ledger")
    with pytest.raises(ReplayError, match="ledger"):
        store.publish_fixture(fixture)
    assert "backtest_replay_fixtures" not in store.backend.writes
    object.__setattr__(fixture, "records", fixture_records)

    memory_store = InMemoryReplayStore()
    memory_store.publish_matrix(matrix)
    clean_fixture = _build(matrix, store=memory_store).seal()
    receipt = ReplayReceipt(
        matrix=matrix, arm_name="baseline", arm_id=matrix.arm_id("baseline"), fixture=clean_fixture,
        experiment=matrix.arms["baseline"], executed_source_tree_hash="sha256:tree",
        dependency_runtime_digest="sha256:" + "a" * 64,
        executed_cost_model_hash=matrix.arms["baseline"].execution_cost_model_hash,
        trade_ledger_hash=trade_ledger_hash([], []),
        replay_audit={"complete": True, "ledger_content_hash": clean_fixture.model_ledger_hash},
        pit_audit=True, execution_audit=True, benchmark_audit=True, accounting_audit=True,
    )
    with pytest.raises(ReplayError, match="fixture manifest"):
        memory_store.publish_receipt(receipt)


def test_rethink_store_uses_a_fake_backend_without_database_calls():
    class Backend:
        def __init__(self):
            self.rows = {}
            self.writes = []

        def insert_record(self, table, doc, *, durability):
            self.writes.append((table, doc["id"], durability))
            key = (table, doc["id"])
            prior = self.rows.get(key)
            if prior is None:
                self.rows[key] = dict(doc)
            return prior

        def get_record(self, table, record_id):
            return self.rows.get((table, record_id))

    store = RethinkReplayStore(Backend())
    matrix = _matrix()
    store.publish_matrix(matrix)
    assert store.get_matrix(matrix.matrix_id).matrix_id == matrix.matrix_id
    build = _build(matrix, store=store)
    build.record_model_row("baseline", _record())
    store.publish_fixture(build.seal())
    assert [table for table, _, _ in store.backend.writes] == [
        "backtest_replay_matrices",
        "backtest_replay_fixture_builds",
        "backtest_replay_calls",
        "backtest_replay_fixtures",
    ]


def test_fresh_rethink_adapter_reconstructs_fixture_and_exact_arm_ledger():
    class Backend:
        def __init__(self):
            self.rows = {}

        def insert_record(self, table, doc, *, durability):
            key = (table, doc["id"])
            prior = self.rows.get(key)
            if prior is None:
                self.rows[key] = dict(doc)
            return prior

        def get_record(self, table, record_id):
            row = self.rows.get((table, record_id))
            return dict(row) if row is not None else None

    backend = Backend()
    writer = RethinkReplayStore(backend)
    matrix = _matrix()
    writer.publish_matrix(matrix)
    build = _build(matrix, store=writer)
    baseline = _record("baseline")
    candidate = _record("candidate", 2)
    build.record_model_row("baseline", baseline)
    build.record_model_row("candidate", candidate)
    fixture = build.seal()
    writer.publish_fixture(fixture)

    reader = RethinkReplayStore(backend)
    loaded, ledger = reader.load_replay_fixture(fixture.fixture_id, "baseline")
    assert loaded.fixture_id == fixture.fixture_id
    assert tuple(row.semantic_id for row in ledger.records) == (baseline.semantic_id,)
    assert reader.get_fixture(fixture.fixture_id).to_doc() == fixture.to_doc()


def test_fresh_replay_load_rejects_tampered_fixture_call_and_missing_union_row():
    class Backend:
        def __init__(self):
            self.rows = {}

        def insert_record(self, table, doc, *, durability):
            key = (table, doc["id"])
            prior = self.rows.get(key)
            if prior is None:
                self.rows[key] = dict(doc)
            return prior

        def get_record(self, table, record_id):
            return self.rows.get((table, record_id))

    backend = Backend()
    writer = RethinkReplayStore(backend)
    matrix = _matrix()
    writer.publish_matrix(matrix)
    build = _build(matrix, store=writer)
    row = _record("baseline")
    build.record_model_row("baseline", row)
    build.record_model_row("candidate", row)
    fixture = build.seal()
    writer.publish_fixture(fixture)

    original_call = dict(backend.rows[("backtest_replay_calls", row.semantic_id)])
    backend.rows[("backtest_replay_calls", row.semantic_id)]["outcome"] = {"tampered": True}
    with pytest.raises(ReplayError, match="call"):
        RethinkReplayStore(backend).load_replay_fixture(fixture.fixture_id, "baseline")

    backend.rows[("backtest_replay_calls", row.semantic_id)] = original_call
    original_fixture = dict(backend.rows[("backtest_replay_fixtures", fixture.fixture_id)])
    backend.rows[("backtest_replay_fixtures", fixture.fixture_id)]["fixture_ordinal"] = 1
    with pytest.raises(ReplayError, match="fixture document identity"):
        RethinkReplayStore(backend).load_replay_fixture(fixture.fixture_id, "baseline")

    backend.rows[("backtest_replay_fixtures", fixture.fixture_id)] = original_fixture
    del backend.rows[("backtest_replay_calls", row.semantic_id)]
    with pytest.raises(ReplayError, match="call row is missing"):
        RethinkReplayStore(backend).load_replay_fixture(fixture.fixture_id, "candidate")


def test_replay_audit_cannot_claim_completeness_it_cannot_prove():
    matrix = _matrix()
    fixture = _build(matrix).seal()
    common = dict(
        matrix=matrix, arm_name="baseline", arm_id=matrix.arm_id("baseline"),
        fixture=fixture, experiment=matrix.arms["baseline"],
        executed_source_tree_hash="sha256:tree",
        dependency_runtime_digest="sha256:" + "a" * 64,
        executed_cost_model_hash=matrix.arms["baseline"].execution_cost_model_hash,
        trade_ledger_hash=trade_ledger_hash([], []),
        pit_audit=True, execution_audit=True, benchmark_audit=True, accounting_audit=True,
    )
    with pytest.raises(ReplayError, match="must bind its ledger hash"):
        ReplayReceipt(replay_audit={"complete": True}, **common)
    with pytest.raises(ReplayError, match="differs from its fixture"):
        ReplayReceipt(
            replay_audit={"complete": True, "ledger_content_hash": "sha256:lie"},
            **common,
        )


def test_persisted_fixture_build_address_cannot_be_repointed():
    from backtest_replay import ReplayFixture

    matrix = _matrix()
    fixture = _build(matrix).seal()
    doc = fixture.to_doc()
    assert ReplayFixture.from_doc(doc).fixture_id == fixture.fixture_id
    doc["build_id"] = "fixture-build-sha256-" + "0" * 64
    with pytest.raises(ReplayError, match="build address"):
        ReplayFixture.from_doc(doc)


def test_persisted_matrix_rejects_missing_or_extra_document_fields():
    matrix = _matrix()
    doc = matrix.to_doc()
    assert ExperimentMatrixManifest.from_doc(doc).matrix_id == matrix.matrix_id
    extra = dict(doc, unexpected=True)
    with pytest.raises(ReplayError, match="matrix document shape"):
        ExperimentMatrixManifest.from_doc(extra)
    missing = {key: value for key, value in doc.items() if key != "arm_ids"}
    with pytest.raises(ReplayError, match="matrix document shape"):
        ExperimentMatrixManifest.from_doc(missing)
