"""Task 5: authoritative RethinkDB event/state store for benchmark alpha.

Hard-durability append-only events, compare-and-swap versioned state, typed
run phases that survive crash/restart, and the invariant that storage failure
is NEVER translated into an empty successful read.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from benchmark_alpha.rethink_store import (
    AlphaIntegrityError,
    AlphaRethinkStore,
    AlphaStateConflictError,
    AlphaUnavailableError,
)
from benchmark_alpha.types import (
    AuthorizationContext,
    EventKind,
    ExecutionMode,
    PromotionTier,
    RunOrigin,
    RunPhase,
    SchedulerTickMode,
    authorized_active_cap,
    require_execution_mode,
)

TS = datetime(2026, 7, 11, tzinfo=timezone.utc)


class FakeBackend:
    def __init__(self):
        self.events = {}
        self.states = {}
        self.durabilities = []

    def insert_event(self, doc, *, durability):
        self.durabilities.append(durability)
        prior = self.events.get(doc["id"])
        if prior is not None:
            return prior
        self.events[doc["id"]] = doc
        return None

    def compare_and_swap_state(self, key, expected_version, doc, *, durability):
        self.durabilities.append(durability)
        prior = self.states.get(key)
        version = 0 if prior is None else prior["version"]
        if version != expected_version:
            return None
        self.states[key] = doc
        return doc

    def get_state_row(self, key):
        return self.states.get(key)

    def health_probe(self):
        return None  # healthy


class DownBackend(FakeBackend):
    def insert_event(self, doc, *, durability):
        raise ConnectionError("rethinkdb unreachable")

    def compare_and_swap_state(self, key, expected_version, doc, *, durability):
        raise ConnectionError("rethinkdb unreachable")

    def get_state_row(self, key):
        raise ConnectionError("rethinkdb unreachable")

    def health_probe(self):
        return "rethinkdb unreachable"


def test_rethink_store_uses_hard_writes_and_rejects_divergent_duplicate():
    backend = FakeBackend()
    store = AlphaRethinkStore.for_backend(backend)
    ts = datetime(2026, 7, 11, tzinfo=timezone.utc)
    assert store.append_event("e1", EventKind.PREDICTION, {"symbol": "AAPL"}, ts)
    assert store.append_event("e1", EventKind.PREDICTION, {"symbol": "AAPL"}, ts) is False
    with pytest.raises(AlphaIntegrityError):
        store.append_event("e1", EventKind.PREDICTION, {"symbol": "MSFT"}, ts)
    assert backend.durabilities == ["hard", "hard", "hard"]


def test_duplicate_detection_is_content_based_not_key_order():
    backend = FakeBackend()
    store = AlphaRethinkStore.for_backend(backend)
    assert store.append_event("e2", EventKind.GATE, {"a": 1, "b": 2}, TS)
    assert store.append_event("e2", EventKind.GATE, {"b": 2, "a": 1}, TS) is False


def test_append_event_requires_aware_timestamp_and_known_kind():
    store = AlphaRethinkStore.for_backend(FakeBackend())
    with pytest.raises(ValueError):
        store.append_event("e3", EventKind.RISK, {}, datetime(2026, 7, 11))
    with pytest.raises(ValueError):
        store.append_event("e4", "prediction", {}, TS)


def test_put_state_compare_and_swap_and_stale_version():
    backend = FakeBackend()
    store = AlphaRethinkStore.for_backend(backend)
    rec = store.put_state("risk:alpaca-main", {"level": "NORMAL"}, expected_version=0)
    assert rec.version == 1
    rec2 = store.put_state("risk:alpaca-main", {"level": "SOFT"}, expected_version=1)
    assert rec2.version == 2
    with pytest.raises(AlphaStateConflictError):
        store.put_state("risk:alpaca-main", {"level": "KILL"}, expected_version=1)
    got = store.get_state("risk:alpaca-main")
    assert got.version == 2
    assert got.payload == {"level": "SOFT"}


def test_storage_failure_is_never_an_empty_read():
    store = AlphaRethinkStore.for_backend(DownBackend())
    with pytest.raises(AlphaUnavailableError):
        store.append_event("e1", EventKind.PREDICTION, {"s": "A"}, TS)
    with pytest.raises(AlphaUnavailableError):
        store.get_state("risk:alpaca-main")
    with pytest.raises(AlphaUnavailableError):
        store.put_state("risk:alpaca-main", {}, expected_version=0)
    health = store.health()
    assert health.available is False
    assert health.error


def test_missing_state_reads_as_none_when_store_is_healthy():
    store = AlphaRethinkStore.for_backend(FakeBackend())
    assert store.get_state("run:alpaca-main:never-written") is None


def test_run_state_survives_restart_at_every_phase():
    backend = FakeBackend()
    store = AlphaRethinkStore.for_backend(backend)
    for idx, phase in enumerate(RunPhase):
        run_id = f"run-{idx}"
        store.put_run_state("alpaca-main", run_id, phase,
                            {"target": {"SPY": 0.98}}, expected_version=0)
        # Simulated restart: a NEW store over the same backend must resume the
        # exact persisted phase — no date-only completed marker.
        resumed = AlphaRethinkStore.for_backend(backend).get_run_state(
            "alpaca-main", run_id)
        assert resumed.phase is phase
        assert resumed.payload["target"] == {"SPY": 0.98}
        assert resumed.version == 1


# --- typed mode separation (E01) -------------------------------------------

def test_scheduler_tick_mode_is_not_an_execution_mode():
    assert set(m.value for m in SchedulerTickMode) == {"FULL", "MONITOR", "IDLE"}
    for tick in SchedulerTickMode:
        assert not isinstance(tick, ExecutionMode)
        with pytest.raises(TypeError):
            require_execution_mode(tick)
    with pytest.raises(TypeError):
        require_execution_mode("FULL")
    assert require_execution_mode(ExecutionMode.LIVE) is ExecutionMode.LIVE


def test_live_safeguards_asserted_for_every_scheduler_tick():
    from benchmark_alpha.types import live_invariants_expected
    for tick in SchedulerTickMode:
        assert live_invariants_expected(ExecutionMode.LIVE, tick) is True
    assert live_invariants_expected(ExecutionMode.OFF, SchedulerTickMode.FULL) is False


def test_run_origin_and_promotion_tiers_are_complete():
    assert {o.value for o in RunOrigin} == {
        "BACKTEST", "OBSERVE", "SHADOW_PORTFOLIO", "PAPER", "LIVE"}
    assert {t.value for t in PromotionTier} == {
        "OBSERVE", "SHADOW_PORTFOLIO", "PAPER", "LIVE_40", "LIVE_60", "LIVE_80"}


def test_authorization_context_caps_and_expiry():
    auth = AuthorizationContext(
        tier=PromotionTier.LIVE_40, active_cap=0.40,
        evidence_allowlist=("direct",), expires_at=TS + timedelta(days=7),
        artifact_hashes={"model": "abc"},
    )
    assert authorized_active_cap(auth, TS) == 0.40
    assert authorized_active_cap(auth, TS + timedelta(days=8)) == 0.0
    assert authorized_active_cap(None, TS) == 0.0
    with pytest.raises(ValueError):
        AuthorizationContext(
            tier=PromotionTier.LIVE_40, active_cap=0.95,  # above absolute ceiling
            evidence_allowlist=(), expires_at=TS, artifact_hashes={})
    with pytest.raises(ValueError):
        AuthorizationContext(
            tier=PromotionTier.LIVE_40, active_cap=0.40,
            evidence_allowlist=(), expires_at=datetime(2026, 7, 11),  # naive
            artifact_hashes={})


# --- migration -------------------------------------------------------------

def test_migration_plan_creates_tables_and_indexes_dry_run_prints_names_only(capsys):
    from scripts.migrate_alpha_tables import main, plan_migration
    actions = plan_migration(existing_tables=set(), existing_indexes={})
    created = {a["table"] for a in actions if a["action"] == "create_table"}
    assert {"AlphaEvents", "AlphaState"} <= created
    index_actions = [a for a in actions if a["action"] == "create_index"]
    wal_indexes = {a["index"] for a in index_actions if a["table"] == "LiveOrderWAL"}
    assert {"instance_id", "client_order_id"} <= wal_indexes

    class FakeMigrationBackend:
        def __init__(self):
            self.created = []

        def list_tables(self):
            return {"LiveOrderWAL"}

        def list_indexes(self, table):
            return set()

        def create_table(self, table):
            self.created.append(("table", table))

        def create_index(self, table, index):
            self.created.append(("index", table, index))

    fake = FakeMigrationBackend()
    rc = main(["--dry-run"], backend=fake)
    assert rc == 0
    assert fake.created == []  # dry-run performs zero mutations
    out = capsys.readouterr().out
    assert "AlphaEvents" in out
    rc = main(["--apply"], backend=fake)
    assert rc == 0
    assert ("table", "AlphaEvents") in fake.created
    assert ("index", "LiveOrderWAL", "instance_id") in fake.created


def test_retention_plan_is_executable_and_scoped():
    from datetime import datetime, timedelta, timezone
    from scripts.migrate_alpha_tables import RETENTION_DAYS, retention_plan
    now = datetime(2026, 7, 18, tzinfo=timezone.utc)
    plan = retention_plan(now)
    by_table = {(t, kind): cutoff for t, cutoff, kind in plan}
    assert ("AlphaPredictions", "as_of") in by_table
    assert by_table[("AlphaPredictions", "as_of")].startswith(
        (now - timedelta(days=400)).date().isoformat())
    assert ("AlphaEvents", "created_at:mark_health") in by_table
    # Indefinitely-retained tables never appear in the executable plan.
    for table, _, _ in plan:
        assert RETENTION_DAYS.get(table) is not None or table == "AlphaEvents"
    assert not any(t == "AlphaFills" for t, _, _ in plan)
