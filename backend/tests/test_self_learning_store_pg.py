"""`self_learning.store` over `db.store` — the semantics the ReQL port must keep.

Every test runs against the shared `store` fixture: real Postgres when
PG_TEST_DSN is set, the FakeStore otherwise. `self_learning.store` binds the
store module at import, so the fixture is patched onto it rather than the other
way round.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from self_learning import store as sl


@pytest.fixture
def sl_store(store, monkeypatch):
    monkeypatch.setattr(sl, "store", store)
    return store


# ── the raw db.store guarantees this module leans on ─────────────────────────

def test_conflict_update_deep_merges_not_shallow(store):
    """`||` would drop the sibling key. jsonb_deep_merge must not."""
    store.insert("LearningObservations",
                 {"id": "o1", "features": {"a": 1, "b": 2}, "label": "x"})
    store.insert("LearningObservations",
                 {"id": "o1", "features": {"b": 9}}, conflict="update")
    got = store.get("LearningObservations", "o1")
    assert got["features"] == {"a": 1, "b": 9}
    assert got["label"] == "x"


def test_multi_insert_is_partial_success(store):
    store.insert("LearningObservations", {"id": "dup"})
    docs = [{"id": str(i)} for i in range(5)] + [{"id": "dup"}]
    res = store.insert("LearningObservations", docs)
    assert res["inserted"] == 5
    assert res["errors"] == 1
    assert "Duplicate primary key" in res["first_error"]


# ── sweeps: the r.minval / open-lower-bound range deletes ────────────────────

def test_sweep_expired_drops_the_lower_bound(sl_store):
    """`r.minval` becomes no lower bound at all: everything before the cutoff
    goes, and a row without an `as_of` is never swept."""
    sl_store.insert("LearningObservations", [
        {"id": "old", "as_of": "2026-01-01T00:00:00"},
        {"id": "new", "as_of": "2026-06-01T00:00:00"},
        {"id": "undated"},
    ])
    assert sl.sweep_expired(None, cutoff="2026-03-01T00:00:00") == 1
    assert {row["id"] for row in sl_store.run("LearningObservations")} \
        == {"new", "undated"}


def test_sweep_expired_outcomes_keeps_the_undated_row(sl_store):
    """The lower bound is "" and OPEN, so an `as_of == ""` row is never swept:
    a parse failure must not become data loss."""
    sl_store.insert("LearningOutcomes", [
        {"id": "old", "as_of": "2026-01-01T00:00:00"},
        {"id": "blank", "as_of": ""},
    ])
    assert sl.sweep_expired_outcomes(None, cutoff="2026-03-01T00:00:00") == 1
    assert sl_store.get("LearningOutcomes", "blank") is not None


def test_a_blank_cutoff_deletes_nothing(sl_store):
    sl_store.insert("LearningObservations", {"id": "x", "as_of": "2020-01-01"})
    assert sl.sweep_expired(None, cutoff="") == 0
    assert sl.sweep_expired_outcomes(None, cutoff=None) == 0
    assert sl_store.get("LearningObservations", "x") is not None


# ── config ───────────────────────────────────────────────────────────────────

def test_seed_then_get_config_returns_the_defaults(sl_store):
    sl.seed_config(None)
    config = sl.get_config(None)
    assert config["mode"] == "observe"
    assert config["document_allowlist"] == []


def test_put_config_writes_only_mutable_keys(sl_store):
    sl.seed_config(None)
    sl.put_config(None, {"retain_days": 30, "id": "hacked", "nope": 1})
    assert sl.get_config(None)["retain_days"] == 30
    assert sl_store.get(sl.CONFIG, sl.CONFIG_DOC_ID) is not None
    assert sl_store.get(sl.CONFIG, "hacked") is None


def test_mark_processed_is_set_insert(sl_store):
    sl.seed_config(None)
    sl.mark_processed(None, "run-1")
    sl.mark_processed(None, "run-1")
    sl.mark_processed(None, 2)
    assert sl.get_config(None)["processed_run_ids"] == ["run-1", "2"]


# ── the conflict-resolver ports (r.branch / set_union / merge) ───────────────

def test_findings_keep_an_operator_status_and_the_first_sighting(sl_store):
    class _F:
        def __init__(self, detected_at):
            self.detected_at = detected_at

        def to_doc(self):
            return {"id": "f1", "status": "open", "severity": "high",
                    "detected_at": self.detected_at}

    sl.put_findings(None, [_F("2026-01-01")])
    row = sl_store.get(sl.FINDINGS, "f1")
    assert "status" not in row          # popped: never written on first sight

    sl_store.update(sl.FINDINGS, "f1", {"status": "acknowledged"})
    sl.put_findings(None, [_F("2026-02-02")])
    row = sl_store.get(sl.FINDINGS, "f1")
    assert row["status"] == "acknowledged"        # not reopened
    assert row["first_detected_at"] == "2026-02-02"
    assert row["detected_at"] == "2026-02-02"


def test_findings_default_a_missing_status_to_open(sl_store):
    class _F:
        def to_doc(self):
            return {"id": "f2", "detected_at": "2026-01-01"}

    sl.put_findings(None, [_F()])
    sl.put_findings(None, [_F()])
    row = sl_store.get(sl.FINDINGS, "f2")
    assert row["status"] == "open"
    assert row["first_detected_at"] == "2026-01-01"


def test_experiment_status_is_monotonic_and_run_ids_only_grow(sl_store):
    spec = {"id": "e1", "status": "registered", "run_ids": ["a"],
            "hypothesis_id": "h1"}
    sl.put_experiment(None, spec)
    sl_store.update(sl.EXPERIMENTS, "e1",
                    {"status": "failed", "refusal_reason": "noise"})
    sl.put_experiment(None, {**spec, "run_ids": ["b", "a"]})
    row = sl_store.get(sl.EXPERIMENTS, "e1")
    assert row["status"] == "failed"           # never reset to registered
    assert row["run_ids"] == ["a", "b"]        # union, order preserved
    assert row["refusal_reason"] == "noise"    # a blank new reason never wins


def test_hypothesis_experiment_ids_union_without_reopening(sl_store):
    doc = {"id": "h1", "status": "open", "experiment_ids": ["e1"]}
    sl.put_hypothesis(None, doc)
    sl.set_hypothesis_status(None, "h1", "rejected", "disproved")
    sl.put_hypothesis(None, {**doc, "experiment_ids": ["e2"]})
    row = sl_store.get(sl.HYPOTHESES, "h1")
    assert row["status"] == "rejected"
    assert row["experiment_ids"] == ["e1", "e2"]


# ── ordered reads happen in the database, not after a slice ──────────────────

def test_list_findings_orders_newest_first_in_the_database(sl_store):
    sl_store.insert(sl.FINDINGS, [{"id": str(i), "detected_at": "2026-01-%02d" % i}
                                  for i in range(1, 6)])
    rows = sl.list_findings(None, limit=2)
    assert [row["id"] for row in rows] == ["5", "4"]


def test_list_hypotheses_filters_before_it_limits(sl_store):
    sl_store.insert(sl.HYPOTHESES,
                    [{"id": "n%d" % i, "target": "other", "created_at": "2026-01-01"}
                     for i in range(10)]
                    + [{"id": "mine", "target": "t", "created_at": "2026-02-01"}])
    rows = sl.list_hypotheses(None, limit=3, target="t")
    assert [row["id"] for row in rows] == ["mine"]


def test_list_observations_orders_by_as_of_within_the_run(sl_store):
    sl_store.insert(sl.OBSERVATIONS, [
        {"id": "b", "run_id": "1", "as_of": "2026-01-02"},
        {"id": "a", "run_id": "1", "as_of": "2026-01-01"},
        {"id": "z", "run_id": "2", "as_of": "2026-01-03"},
    ])
    assert [row["id"] for row in sl.list_observations(None, 1)] == ["a", "b"]


# ── counters computed over the whole table, never over a 500-row slice ───────

def test_counts_sees_every_row(sl_store):
    sl_store.insert(sl.FINDINGS, [
        {"id": "1", "severity": "high"},                    # no status -> open
        {"id": "2", "severity": "low", "status": "closed"},
        {"id": "3", "severity": "high", "status": "open"},
    ])
    sl_store.insert(sl.FUNNELS, [{"id": str(i), "decided": 2, "refused": 1}
                                 for i in range(600)])
    got = sl.counts(None)
    assert got["open_findings"] == 2
    assert got["by_severity"] == {"high": 2}
    assert got["runs_observed"] == 600
    assert got["decisions_observed"] == 1200
    assert got["refusals_observed"] == 600


# ── purge keeps the config and clears the watermark ──────────────────────────

def test_purge_clears_derived_tables_and_the_watermark(sl_store):
    sl.seed_config(None)
    sl.mark_processed(None, "run-1")
    sl_store.insert(sl.OBSERVATIONS, {"id": "o", "as_of": "2026-01-01"})
    out = sl.purge(None, confirm=True)
    assert out["deleted"][sl.OBSERVATIONS] == 1
    assert out["deleted"]["_watermark_cleared"] is True
    assert sl.get_config(None)["processed_run_ids"] == []
    assert sl.get_config(None)["mode"] == "observe"     # config survived


def test_purge_refuses_without_confirmation(sl_store):
    with pytest.raises(ValueError):
        sl.purge(None)


# ── a document with no id gets one, the way RethinkDB generated it ───────────

def test_put_intent_generates_a_primary_key(sl_store):
    sl.put_intent(None, {"kind": "idle", "reason": "nothing to do"}, at="2026-01-01")
    rows = sl.list_intents(None)
    assert len(rows) == 1 and rows[0]["kind"] == "idle" and rows[0]["id"]


# ── the running-backtest read is plucked and bounded ─────────────────────────

def test_running_backtests_plucks_only_id_and_status(sl_store):
    sl_store.insert("BacktestResults", [
        {"id": "1", "status": "running", "payload": "huge"},
        {"id": "2", "status": "finished"},
    ])
    assert sl.running_backtests(None) == [{"id": "1", "origin": "human"}]
