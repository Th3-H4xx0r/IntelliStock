"""Tests for scripts/clear_main_instance_lookback_state.py (Phase 1 extensions)."""

from __future__ import annotations

import sys
from pathlib import Path

# Add scripts/ to sys.path so we can import the module under test
_SCRIPTS_DIR = str(Path(__file__).resolve().parents[2] / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import clear_main_instance_lookback_state as cleaner  # noqa: E402


def test_build_targets_covers_all_14_phase1_tables():
    """Phase 1 design requires all 14 per-instance tables be cleared."""
    targets = cleaner._build_targets("main")
    table_names = {t[0] for t in targets}
    expected = {
        # Original 4 (already cleared by this script before Phase 1)
        "GraphNexusTradeContexts", "GraphNexusOutcomes",
        "NexusRuntimeState", "LiveState",
        # Phase 1 additions
        "NexusStrategyCache", "LiveOrderWAL",
        "GraphNexusDiscoveredStocks", "GraphNexusMarketTrends",
        "GraphNexusRotationCooldown", "GraphNexusTradeOutcomes",
        "GraphNexusLearningCache",
        "GraphNexusDiscoverySnapshots", "GraphNexusOutcomeSeries",
        "GraphNexusAnalystPanel",
    }
    missing = expected - table_names
    assert not missing, f"missing tables in TARGETS: {missing}"


def _criteria_of(entry):
    """Helper: return the criteria list from a (table, criteria) or
    (table, criteria, combine) entry."""
    return entry[1]


def _combine_of(entry):
    """Helper: return the combine mode ('or' default) from an entry."""
    return entry[2] if len(entry) == 3 else "or"


def test_nexus_strategy_cache_target_uses_and_mode_with_instance_filter():
    """Bug-sweep 2026-05-21: must AND-combine instance_id and origin filters.

    The previous OR-only filter on origin=live would delete every live-origin
    row across ALL instances. The new contract: delete only rows that match
    BOTH (instance_id == X) AND (origin != backtest).
    """
    targets = cleaner._build_targets("main")
    nsc = [t for t in targets if t[0] == "NexusStrategyCache"]
    assert len(nsc) == 1, "NexusStrategyCache should appear exactly once"
    criteria = _criteria_of(nsc[0])
    combine = _combine_of(nsc[0])
    assert combine == "and", (
        f"NexusStrategyCache must AND-combine criteria; got combine={combine!r}"
    )
    fields = [(c[0], c[1], c[2]) for c in criteria]
    assert ("instance_id", "main", "exact") in fields, (
        f"NexusStrategyCache must filter on instance_id; got {fields}"
    )
    has_special_origin = any(
        f == "origin_not_backtest" and mode == "special"
        for f, _v, mode in fields
    )
    assert has_special_origin, (
        f"NexusStrategyCache must include origin_not_backtest special; got {fields}"
    )


# Reusable ReQL-row-expression evaluator stub: every r.row[<field>] returns a
# comparator that captures field+op+value. The composed expression supports
# & / | which we mimic with a tree we can evaluate against a candidate row.
class _Pred:
    def __init__(self, fn):
        self.fn = fn
    def __and__(self, other):
        return _Pred(lambda row: bool(self.fn(row)) and bool(other.fn(row)))
    def __or__(self, other):
        return _Pred(lambda row: bool(self.fn(row)) or bool(other.fn(row)))


class _Field:
    def __init__(self, name):
        self.name = name
        self._default = None
    def default(self, v):
        f = _Field(self.name); f._default = v; return f
    def eq(self, val):
        name = self.name
        return _Pred(lambda row: row.get(name) == val)
    def __ne__(self, val):  # type: ignore[override]
        name = self.name; d = self._default
        return _Pred(lambda row: (row.get(name, d) if d is not None else row.get(name)) != val)
    def match(self, pat):
        import re as _re; name = self.name; d = self._default
        return _Pred(lambda row: bool(_re.search(pat, str(row.get(name, d) or ""))))


class _Row:
    def __getitem__(self, k): return _Field(k)


class _FakeR:
    row = _Row()


def test_nexus_strategy_cache_does_not_delete_other_instance_rows():
    """Bug-sweep 2026-05-21 regression: filter built for instance='foo' must
    NOT match a row that belongs to instance_id='main'."""
    targets = cleaner._build_targets("foo")
    nsc_entry = next(t for t in targets if t[0] == "NexusStrategyCache")
    criteria = _criteria_of(nsc_entry)
    combine = _combine_of(nsc_entry)
    pred = cleaner._build_filter(_FakeR(), criteria, combine=combine)
    assert pred is not None
    # Row from a different instance with live origin must NOT match.
    assert pred.fn({"instance_id": "main", "origin": "live"}) is False, (
        "filter for instance='foo' must not match a row with instance_id='main'"
    )
    # Row from the targeted instance with live origin SHOULD match.
    assert pred.fn({"instance_id": "foo", "origin": "live"}) is True
    # Backtest row in the targeted instance must NOT match (snapshots preserved).
    assert pred.fn({"instance_id": "foo", "origin": "backtest"}) is False
    # Legacy row (no origin field) in the targeted instance SHOULD match
    # (origin_not_backtest treats missing origin as "not backtest").
    assert pred.fn({"instance_id": "foo"}) is True


def test_build_filter_and_mode_combines_with_and():
    """Sanity-check _build_filter AND-mode against a synthetic row."""
    pred_and = cleaner._build_filter(
        _FakeR(),
        [("instance_id", "main", "exact"), ("origin_not_backtest", None, "special")],
        combine="and",
    )
    assert pred_and is not None
    assert pred_and.fn({"instance_id": "main", "origin": "live"}) is True
    assert pred_and.fn({"instance_id": "main", "origin": "backtest"}) is False
    assert pred_and.fn({"instance_id": "other", "origin": "live"}) is False


def test_build_filter_default_or_mode_unchanged():
    """Back-compat: criteria without an explicit combine mode still OR."""
    pred_or = cleaner._build_filter(
        _FakeR(),
        [("instance_id", "main", "exact"), ("base_instance_id", "main", "exact")],
        # default combine="or"
    )
    assert pred_or is not None
    assert pred_or.fn({"instance_id": "main"}) is True
    assert pred_or.fn({"base_instance_id": "main"}) is True
    assert pred_or.fn({"instance_id": "other"}) is False


def test_id_keyed_tables_filter_by_id_not_instance_id():
    """Bug-sweep 2026-05-21: 3 tables use id=instance_id as PK with no
    separate instance_id field. Filtering on instance_id matched 0 rows
    (silently no-op in production)."""
    targets = cleaner._build_targets("main")
    by_table = {t[0]: t for t in targets}
    for tbl in ("GraphNexusRotationCooldown",
                "GraphNexusLearningCache",
                "GraphNexusDiscoverySnapshots"):
        criteria = _criteria_of(by_table[tbl])
        fields = {c[0] for c in criteria}
        assert "id" in fields, (
            f"{tbl} must filter on 'id' (the table's per-instance PK); got fields={fields}"
        )
        assert "instance_id" not in fields, (
            f"{tbl} must NOT filter on 'instance_id' (no such field on this table); "
            f"got fields={fields}"
        )


def test_learning_cache_filter_covers_prefix_variant():
    """GraphNexusLearningCache also writes keys shaped like
    '{instance_id}|...' (e.g. 'cleanup_done|main'); cleanup must match
    those too."""
    targets = cleaner._build_targets("main")
    lc = next(t for t in targets if t[0] == "GraphNexusLearningCache")
    criteria = _criteria_of(lc)
    modes = {(c[0], c[2]) for c in criteria}
    assert ("id", "exact") in modes, criteria
    assert ("id", "prefix") in modes, (
        f"GraphNexusLearningCache must include a prefix match for 'main|...' keys; got {criteria}"
    )


def test_id_keyed_filter_matches_rows_with_id_only():
    """End-to-end: a row whose only identifier is ``id == 'main'`` must be
    matched by the cleanup filter for instance='main'."""
    targets = cleaner._build_targets("main")
    rc = next(t for t in targets if t[0] == "GraphNexusRotationCooldown")
    criteria = _criteria_of(rc)
    combine = _combine_of(rc)
    pred = cleaner._build_filter(_FakeR(), criteria, combine=combine)
    assert pred is not None
    assert pred.fn({"id": "main"}) is True
    assert pred.fn({"id": "other"}) is False


def test_build_targets_substitutes_instance_id():
    """_build_targets must use the passed instance_id, not hardcode 'main'."""
    targets = cleaner._build_targets("my-test-instance")
    # Look at any criterion that references INSTANCE_ID, confirm it picked up the param
    found_instance_ref = False
    for entry in targets:
        criteria = _criteria_of(entry)
        for field, value, mode in criteria:
            if "my-test-instance" in str(value):
                found_instance_ref = True
                break
        if found_instance_ref:
            break
    assert found_instance_ref, "Targets should reference the passed instance_id"


def test_instance_id_module_constant_is_main():
    """Default INSTANCE_ID constant should still be 'main' for back-compat."""
    assert cleaner.INSTANCE_ID == "main"
