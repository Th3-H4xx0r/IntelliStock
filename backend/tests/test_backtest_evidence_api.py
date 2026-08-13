"""Task 4 (2026-07-28): the evidence run lifecycle boundary.

The invariant this file exists to protect: **only a successful, complete run
may seal a fixture.** Every other way a backtest can end -- an ordinary
exception, a critical LLM abort, a user stop, a pause termination, or a forced
`os._exit` -- must persist an INELIGIBLE outcome, clear the process-global
evidence session, and leave no finalized fixture behind.

That matters because a half-sealed fixture is worse than no fixture: it looks
like replayable evidence, so a later arm would replay a partial request set and
report a matched result that never happened. `try/finally` alone cannot carry
this, because `os._exit` bypasses it entirely -- hence the explicit terminal
hook.
"""
import os
import sys

import pytest

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

import model_evidence  # noqa: E402
from backtest_evidence_options import (  # noqa: E402
    EvidenceOptionError,
    execution_cost_model_hash,
)
from backtest_evidence_runtime import EvidenceRunLifecycle  # noqa: E402
from backtest_replay import (  # noqa: E402
    ExperimentMatrixManifest,
    InMemoryReplayStore,
)
from experiment_registry import ExperimentSpec  # noqa: E402

_WINDOW = {"start": "2026-03-02", "end": "2026-04-27"}
_SOURCE_TREE = "sha256:tree"
_RUNTIME_DIGEST = "sha256:" + "c" * 64
_COST_MODEL = {"version": "equity-next-event-v1", "spread_bps": 5.0,
               "slippage_bps": 10.0, "fee_bps": 0.3, "latency_seconds": 0.0}
_COST_HASH = execution_cost_model_hash(_COST_MODEL)


def _source_manifest(name):
    return {"manifest_id": name, "source_hashes": {name: f"sha256:{name}"},
            "created_at": "2026-07-28T09:30:00+00:00"}


_BENCHMARK = {
    "manifest_id": "spy-1", "symbol": "SPY", "timeframe": "1Day",
    "adjustment": "all", "price_field": "c", "total_return": True,
    "feed": "iex", "start_date": "2026-03-02", "end_date": "2026-04-27",
    "valuation_rule": "xnys_session_close",
    "valuation_timestamps": ["2026-03-02T21:00:00Z", "2026-04-27T20:00:00Z"],
    "content_hash": "spy-sha256-" + "4" * 64,
}


def _spec(label, cost_version="equity-next-event-v1"):
    return ExperimentSpec(
        experiment_id=f"exp-{label}-{cost_version}",
        search_scope="bull-rally",
        commit_sha="340c0e9",
        source_tree_hash=_SOURCE_TREE,
        effective_config={"arm": label},
        model_provider="openai",
        model_name="research-model",
        prompt_hashes={"decision": "sha256:prompt"},
        model_settings={"temperature": 0.0},
        seed=7,
        predeclared_repeats=2,
        dataset_manifest=_source_manifest("dataset"),
        graph_manifest=_source_manifest("graph"),
        universe_manifest=_source_manifest("universe"),
        benchmark_manifest=dict(_BENCHMARK),
        execution_cost_model=dict(_COST_MODEL, version=cost_version),
        start_date="2026-03-02",
        end_date="2026-04-27",
        fold="walk-forward-01",
        actor="research-orchestrator",
    )


def _matrix(arms=("baseline", "a4")):
    specs = {name: _spec(name) for name in arms}
    stressed = {name: _spec(name, "equity-next-event-v1+stress25bps") for name in arms}
    return ExperimentMatrixManifest(
        arms=specs,
        combinations=[list(arms)],
        windows=[_WINDOW],
        cost_scenarios={"base": specs, "25bps": stressed},
        fixture_count=2,
        arm_recording_order=list(arms),
        trial_count=2,
        bootstrap_seed=11,
        failure_rules={"stopped": "counts_as_failure"},
        selection_rule={"rule": "preregistered"},
        implementation_hashes={"graph_nexus": "d" * 64},
    )


def _store_with_matrix():
    store = InMemoryReplayStore()
    matrix = _matrix()
    store.publish_matrix(matrix)
    return store, matrix


def _options(mode="record", **kw):
    store, matrix = kw.pop("_ctx", (None, None))
    opts = {
        "evidence_mode": mode,
        "matrix_manifest_id": kw.pop("matrix_id", None),
        "matrix_arm_id": kw.pop("arm_id", None),
        "cost_scenario_id": "base",
        "fixture_build_id": "build-0",
        "replay_fixture_id": None,
        "equity_total_cost_bps": None,
        "nexus_candidate_overrides": {},
    }
    opts.update(kw)
    return opts


def _lifecycle(store, matrix, mode="record", **kw):
    opts = _options(mode)
    opts["matrix_manifest_id"] = matrix.matrix_id
    opts["matrix_arm_id"] = matrix.arm_id("a4")
    opts.update(kw)
    return EvidenceRunLifecycle(
        options=opts, backtest_id="bt-1", store=store,
        window=_WINDOW, fixture_ordinal=0, benchmark_manifest=dict(_BENCHMARK),
        rng_seed_manifest={"seed": 7},
    )


@pytest.fixture(autouse=True)
def _clear_session():
    model_evidence.clear_model_evidence_session()
    yield
    model_evidence.clear_model_evidence_session()


# ------------------------------------------------------------------ disabled
def test_off_mode_is_completely_inert():
    store, matrix = _store_with_matrix()
    life = EvidenceRunLifecycle(
        options={"evidence_mode": "off", "matrix_manifest_id": None,
                 "matrix_arm_id": None, "cost_scenario_id": None,
                 "fixture_build_id": None, "replay_fixture_id": None,
                 "equity_total_cost_bps": None, "nexus_candidate_overrides": {}},
        backtest_id="bt-1", store=store, window=_WINDOW, fixture_ordinal=0,
        benchmark_manifest=dict(_BENCHMARK), rng_seed_manifest={"seed": 7})
    assert life.enabled is False
    life.begin()
    assert model_evidence.get_model_evidence_session() is None
    life.record_pit("2026-03-02T14:30:00+00:00", {"as_of": "2026-03-02T00:00:00+00:00"})
    assert life.abort("stopped")["evidence_mode"] == "off"
    assert store.write_order == ("matrix",), "an off run writes no evidence"


# ------------------------------------------------------------------ preflight
def test_begin_rejects_an_unknown_matrix():
    store, matrix = _store_with_matrix()
    life = _lifecycle(store, matrix, matrix_manifest_id="matrix-sha256-" + "f" * 64)
    with pytest.raises(EvidenceOptionError):
        life.begin()


def test_begin_rejects_an_arm_not_declared_by_the_matrix():
    store, matrix = _store_with_matrix()
    life = _lifecycle(store, matrix, matrix_arm_id="arm-sha256-" + "f" * 64)
    with pytest.raises(EvidenceOptionError):
        life.begin()


def test_begin_rejects_an_unpreregistered_cost_scenario():
    store, matrix = _store_with_matrix()
    life = _lifecycle(store, matrix, cost_scenario_id="99bps")
    with pytest.raises(EvidenceOptionError):
        life.begin()


def test_begin_activates_the_process_global_session():
    store, matrix = _store_with_matrix()
    life = _lifecycle(store, matrix)
    life.begin()
    session = model_evidence.get_model_evidence_session()
    assert session is not None
    assert session.mode == "record"
    assert session.arm_id == matrix.arm_id("a4")


# ----------------------------------------------------------- PIT then sealing
def _run_one_decision(life):
    life.record_pit("2026-03-02T14:30:00+00:00",
                    {"as_of": "2026-03-02T00:00:00+00:00", "id": "pit-1"})


def test_success_seals_the_fixture_and_publishes_an_eligible_receipt():
    store, matrix = _store_with_matrix()
    life = _lifecycle(store, matrix)
    life.begin()
    _run_one_decision(life)
    receipt = life.succeed(
        trade_ledger_hash="trade-ledger-sha256-" + "e" * 64,
        executed_source_tree_hash=_SOURCE_TREE,
        dependency_runtime_digest=_RUNTIME_DIGEST,
        executed_cost_model_hash=_COST_HASH,
        audits={"pit": True, "execution": True, "benchmark": True, "accounting": True},
    )
    assert receipt.promotion_eligible is True
    assert "fixture" in store.write_order and "receipt" in store.write_order
    assert model_evidence.get_model_evidence_session() is None, "session cleared"


def test_missing_audits_produce_an_ineligible_receipt_not_an_exception():
    store, matrix = _store_with_matrix()
    life = _lifecycle(store, matrix)
    life.begin()
    _run_one_decision(life)
    receipt = life.succeed(
        trade_ledger_hash="trade-ledger-sha256-" + "e" * 64,
        executed_source_tree_hash=_SOURCE_TREE,
        dependency_runtime_digest=_RUNTIME_DIGEST,
        executed_cost_model_hash=_COST_HASH,
        audits={"pit": True, "execution": False, "benchmark": True, "accounting": True},
    )
    assert receipt.promotion_eligible is False


def test_pit_out_of_order_is_refused():
    store, matrix = _store_with_matrix()
    life = _lifecycle(store, matrix)
    life.begin()
    life.record_pit("2026-03-05T14:30:00+00:00", {"as_of": "2026-03-05T00:00:00+00:00"})
    with pytest.raises(Exception):
        life.record_pit("2026-03-02T14:30:00+00:00",
                        {"as_of": "2026-03-02T00:00:00+00:00"})


def test_manifest_dated_after_its_decision_is_refused():
    store, matrix = _store_with_matrix()
    life = _lifecycle(store, matrix)
    life.begin()
    with pytest.raises(Exception):
        life.record_pit("2026-03-02T14:30:00+00:00",
                        {"as_of": "2026-03-03T00:00:00+00:00"})


# ------------------------------------------------------- non-success terminals
@pytest.mark.parametrize("reason", [
    "exception", "critical_llm_abort", "user_stop", "paused", "forced_exit",
])
def test_no_terminal_path_can_seal_a_fixture(reason):
    store, matrix = _store_with_matrix()
    life = _lifecycle(store, matrix)
    life.begin()
    _run_one_decision(life)
    outcome = life.abort(reason)
    assert outcome["eligible"] is False
    assert outcome["reason"] == reason
    assert "fixture" not in store.write_order, f"{reason} must not seal a fixture"
    assert model_evidence.get_model_evidence_session() is None


def test_abort_is_idempotent():
    store, matrix = _store_with_matrix()
    life = _lifecycle(store, matrix)
    life.begin()
    first = life.abort("user_stop")
    second = life.abort("exception")
    assert second["reason"] == first["reason"], "the first terminal reason wins"


def test_success_after_abort_is_refused():
    store, matrix = _store_with_matrix()
    life = _lifecycle(store, matrix)
    life.begin()
    life.abort("user_stop")
    with pytest.raises(EvidenceOptionError):
        life.succeed(
            trade_ledger_hash="trade-ledger-sha256-" + "e" * 64,
            executed_source_tree_hash=_SOURCE_TREE,
            dependency_runtime_digest=_RUNTIME_DIGEST,
        executed_cost_model_hash=_COST_HASH,
            audits={"pit": True, "execution": True, "benchmark": True,
                    "accounting": True})


def test_dirty_source_tree_is_still_recorded_but_flagged():
    """A dirty worktree must not silently pass as the preregistered source."""
    store, matrix = _store_with_matrix()
    life = _lifecycle(store, matrix)
    life.begin()
    _run_one_decision(life)
    receipt = life.succeed(
        trade_ledger_hash="trade-ledger-sha256-" + "e" * 64,
        executed_source_tree_hash=_SOURCE_TREE,
        dependency_runtime_digest=_RUNTIME_DIGEST,
        executed_cost_model_hash=_COST_HASH,
        executed_content_manifest={"files": {"backend/broker.py": "sha256:" + "f" * 64}},
        audits={"pit": True, "execution": True, "benchmark": True, "accounting": True},
    )
    assert receipt.executed_source_identity != _SOURCE_TREE


# -------------------------------------------------------------- os._exit hook
def test_terminal_hook_aborts_before_a_forced_exit():
    """try/finally cannot intercept os._exit, so the hook must run first."""
    store, matrix = _store_with_matrix()
    life = _lifecycle(store, matrix)
    life.begin()
    exits = []
    guarded = life.install_terminal_hook(exits.append)
    guarded(3)
    assert exits == [3], "the real exit still happens"
    assert "fixture" not in store.write_order
    assert model_evidence.get_model_evidence_session() is None


def test_terminal_hook_exits_even_if_persistence_fails():
    """A broken store must never turn a forced exit into a hang."""
    class _Broken(InMemoryReplayStore):
        def publish_receipt(self, receipt):
            raise RuntimeError("rethink down")

    store = _Broken()
    matrix = _matrix()
    store.publish_matrix(matrix)
    life = _lifecycle(store, matrix)
    life.begin()
    exits = []
    life.install_terminal_hook(exits.append)(1)
    assert exits == [1]


def test_terminal_hook_is_a_noop_after_success():
    store, matrix = _store_with_matrix()
    life = _lifecycle(store, matrix)
    life.begin()
    _run_one_decision(life)
    life.succeed(
        trade_ledger_hash="trade-ledger-sha256-" + "e" * 64,
        executed_source_tree_hash=_SOURCE_TREE,
        dependency_runtime_digest=_RUNTIME_DIGEST,
        executed_cost_model_hash=_COST_HASH,
        audits={"pit": True, "execution": True, "benchmark": True, "accounting": True})
    before = store.write_order
    exits = []
    life.install_terminal_hook(exits.append)(0)
    assert exits == [0]
    assert store.write_order == before, "a completed run is not re-finalized"


def test_replay_lifecycle_loads_fresh_store_ledger_and_requires_complete_consumption():
    from backtest_replay import RethinkReplayStore
    from model_evidence import ModelEvidenceContext, ModelEvidenceRecord, canonical_request_envelope, semantic_request_id

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
    life = _lifecycle(writer, matrix)
    life.begin()
    context = ModelEvidenceContext(
        decision_at="2026-03-02T14:30:00Z", call_site="strategy.score",
        role="analyst", subject="AAA", local_sequence=0,
    )
    envelope = canonical_request_envelope(
        requested_provider="openai", requested_model="research-model",
        adapter_identity="adapter-v1", prompt="score", system_prompt="system",
    )
    semantic_id = semantic_request_id(envelope, context=context)
    row = ModelEvidenceRecord.from_response(
        semantic_id=semantic_id, envelope=envelope, context=context,
        outcome={"score": 1}, attempted_models=("research-model",),
        effective_model="research-model", raw_response={"score": 1},
    )
    life.record_model_row(row)
    _run_one_decision(life)
    recorded = life.succeed(
        trade_ledger_hash="trade-ledger-sha256-" + "e" * 64,
        executed_source_tree_hash=_SOURCE_TREE,
        dependency_runtime_digest=_RUNTIME_DIGEST,
        executed_cost_model_hash=_COST_HASH,
        audits={"pit": True, "execution": True, "benchmark": True, "accounting": True},
    )
    fixture_id = recorded.fixture.fixture_id

    reader = RethinkReplayStore(backend)
    replay = _lifecycle(
        reader, matrix, mode="replay", fixture_build_id=None,
        replay_fixture_id=fixture_id,
    )
    replay.begin()
    session = model_evidence.get_model_evidence_session()
    assert session.replay(semantic_id) == {"score": 1}
    receipt = replay.succeed(
        trade_ledger_hash="trade-ledger-sha256-" + "e" * 64,
        executed_source_tree_hash=_SOURCE_TREE,
        dependency_runtime_digest=_RUNTIME_DIGEST,
        executed_cost_model_hash=_COST_HASH,
        audits={"pit": True, "execution": True, "benchmark": True, "accounting": True},
    )
    assert receipt.promotion_eligible is True
    assert receipt.replay_audit["complete"] is True

    reader2 = RethinkReplayStore(backend)
    incomplete = _lifecycle(
        reader2, matrix, mode="replay", fixture_build_id=None,
        replay_fixture_id=fixture_id,
    )
    incomplete.begin()
    with pytest.raises(EvidenceOptionError, match="did not finalize"):
        incomplete.succeed(
            trade_ledger_hash="trade-ledger-sha256-" + "e" * 64,
            executed_source_tree_hash=_SOURCE_TREE,
            dependency_runtime_digest=_RUNTIME_DIGEST,
            executed_cost_model_hash=_COST_HASH,
            audits={"pit": True, "execution": True, "benchmark": True, "accounting": True},
        )


def test_off_abort_does_not_clear_another_runs_session():
    store, matrix = _store_with_matrix()
    active = _lifecycle(store, matrix)
    active.begin()
    session = model_evidence.get_model_evidence_session()
    off = EvidenceRunLifecycle(
        options={"evidence_mode": "off", "matrix_manifest_id": None,
                 "matrix_arm_id": None, "cost_scenario_id": None,
                 "fixture_build_id": None, "replay_fixture_id": None,
                 "equity_total_cost_bps": None, "nexus_candidate_overrides": {}},
        backtest_id="bt-off", store=store, window=_WINDOW, fixture_ordinal=0,
        benchmark_manifest=dict(_BENCHMARK), rng_seed_manifest={"seed": 7})
    off.abort("stopped")
    assert model_evidence.get_model_evidence_session() is session


def test_replay_rejects_a_fixture_from_another_run_address():
    from backtest_replay import RethinkReplayStore

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
    life = _lifecycle(writer, matrix)
    life.begin()
    _run_one_decision(life)
    receipt = life.succeed(
        trade_ledger_hash="trade-ledger-sha256-" + "e" * 64,
        executed_source_tree_hash=_SOURCE_TREE,
        dependency_runtime_digest=_RUNTIME_DIGEST,
        executed_cost_model_hash=_COST_HASH,
        audits={"pit": True, "execution": True, "benchmark": True, "accounting": True},
    )
    wrong = EvidenceRunLifecycle(
        options=dict(_options("replay"), matrix_manifest_id=matrix.matrix_id,
                     matrix_arm_id=matrix.arm_id("a4"), cost_scenario_id="25bps",
                     fixture_build_id=None,
                     replay_fixture_id=receipt.fixture.fixture_id),
        backtest_id="bt-2", store=RethinkReplayStore(backend), window=_WINDOW,
        fixture_ordinal=0, benchmark_manifest=dict(_BENCHMARK),
        rng_seed_manifest={"seed": 7})
    with pytest.raises(EvidenceOptionError, match="preregistered address"):
        wrong.begin()


def test_record_run_seals_only_the_occurrences_the_session_published():
    store, matrix = _store_with_matrix()
    life = _lifecycle(store, matrix)
    life.begin()
    _run_one_decision(life)
    receipt = life.succeed(
        trade_ledger_hash="trade-ledger-sha256-" + "e" * 64,
        executed_source_tree_hash=_SOURCE_TREE,
        dependency_runtime_digest=_RUNTIME_DIGEST,
        executed_cost_model_hash=_COST_HASH,
        audits={"pit": True, "execution": True, "benchmark": True, "accounting": True},
    )
    assert receipt.fixture.request_set("a4") == frozenset()
    assert receipt.replay_audit["complete"] is True


def test_an_invalid_receipt_never_publishes_a_durable_fixture():
    store, matrix = _store_with_matrix()
    life = _lifecycle(store, matrix)
    life.begin()
    _run_one_decision(life)
    with pytest.raises(Exception):
        life.succeed(
            trade_ledger_hash="not-a-canonical-ledger-hash",
            executed_source_tree_hash=_SOURCE_TREE,
            dependency_runtime_digest=_RUNTIME_DIGEST,
            executed_cost_model_hash=_COST_HASH,
            audits={"pit": True, "execution": True, "benchmark": True,
                    "accounting": True})
    assert "fixture" not in store.write_order
    assert "receipt" not in store.write_order
