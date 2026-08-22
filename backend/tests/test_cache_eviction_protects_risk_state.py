"""Losing a trailing-stop peak across a restart is not a cache miss, it is a risk change.

`save_strategy_cache_to_db` caps the persisted blob and evicts keys until it fits. That
loop sorted purely by size, so the keys that decide money — per-position peaks, re-entry
cooldowns, regime latches, drawdown-episode state — survived only because they happen to
be small. Sort order is not a guarantee, and the eviction was silent.

Each of these fails in the expensive direction:
  * a lost `_peak_*` re-bases the trailing stop to the current price, so every point of
    accumulated protection on a winner disappears;
  * a lost cooldown lets the bot immediately re-buy what it just sold, and turnover is
    this system's known leak;
  * a lost latch lets the regime flap on the first bar after a restart.

These tests drive the REAL `save_strategy_cache_to_db` through a fake rethinkdb, so they
exercise the real eviction path rather than a re-implementation of it.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import strategy_cache_persistence as scp  # noqa: E402
from strategy_cache_persistence import (  # noqa: E402
    _is_risk_critical,
    save_strategy_cache_to_db,
)


# --------------------------------------------------------------------------
# the predicate
# --------------------------------------------------------------------------
def test_risk_critical_keys_are_recognised():
    assert _is_risk_critical("_peak_SNDK_2026-01-05")
    assert _is_risk_critical("_peak_AAOI")
    assert _is_risk_critical("_momentum_sell_cooldown")
    assert _is_risk_critical("_rotation_evict_cooldown")
    assert _is_risk_critical("_post_sell_breakout_reentry_cooldown")
    assert _is_risk_critical("_recovery_bull_latch")
    assert _is_risk_critical("_bear_capacity_latch")
    assert _is_risk_critical("_dd_kill_blocks_entries")


def test_ordinary_caches_are_not_protected():
    """Over-protecting is its own failure: if everything is critical the cap
    cannot be honoured and the row stops being written at all."""
    for k in ("_momentum_watchlist", "_backfill_queue", "_eta_sector_map",
              "_yf_market_cap_cache", "_market_regime_diag", "_neo"):
        assert not _is_risk_critical(k), k
    assert not _is_risk_critical(None)
    assert not _is_risk_critical(123)


# --------------------------------------------------------------------------
# the real save path
# --------------------------------------------------------------------------
class _FakeStore:
    """db.store stand-in that records every written row.

    Postgres port (G11): strategy_cache_persistence keeps its ``(conn, r)``
    parameters but ignores them and talks to the module-level ``store``, so
    this is monkeypatched in rather than passed.
    """

    def __init__(self, sink):
        self._sink = sink

    def insert(self, _table, row, conflict=None):
        self._sink.append(row)
        return {"inserted": 1}


def _save(cache, max_blob_bytes, monkeypatch):
    sink = []
    monkeypatch.setattr(scp, "store", _FakeStore(sink))
    monkeypatch.setattr(scp, "_ensure_table", lambda conn=None, r=None: True)
    ok = save_strategy_cache_to_db(
        object(), None, "inst", "graph_nexus_analysis", cache,
        max_blob_bytes=max_blob_bytes,
    )
    written = json.loads(sink[-1]["cache_json"]) if sink else {}
    return ok, written


def _oversized_cache():
    """One huge expendable blob plus the small keys that decide money."""
    return {
        "_backfill_queue": ["x" * 200] * 400,          # ~80KB, expendable
        "_momentum_watchlist": ["y" * 200] * 400,      # ~80KB, expendable
        "_peak_SNDK_2026-01-05": 154.76,
        "_peak_AAOI_2026-04-20": 184.61,
        "_momentum_sell_cooldown": {"VICR": 3},
        "_recovery_bull_latch": True,
        "_dd_kill_blocks_entries": True,
    }


def test_the_cache_really_is_oversized_first():
    """ANTI-VACUITY GUARD.

    If the fixture fit under the cap, the test below would pass with the whole
    eviction change reverted and certify nothing.
    """
    blob = json.dumps(_oversized_cache(), default=str)
    assert len(blob) > 20_000, len(blob)


def test_risk_state_survives_eviction_and_the_bulk_is_dropped(monkeypatch):
    ok, written = _save(_oversized_cache(), max_blob_bytes=20_000, monkeypatch=monkeypatch)
    assert ok is True

    # the money keys are all still there
    assert written.get("_peak_SNDK_2026-01-05") == 154.76
    assert written.get("_peak_AAOI_2026-04-20") == 184.61
    assert written.get("_momentum_sell_cooldown") == {"VICR": 3}
    assert written.get("_recovery_bull_latch") is True
    assert written.get("_dd_kill_blocks_entries") is True

    # and something expendable actually had to go, or the cap never bound
    assert "_backfill_queue" not in written or "_momentum_watchlist" not in written


def test_a_cache_that_fits_is_written_untouched(monkeypatch):
    small = {"_peak_X_1": 10.0, "_momentum_watchlist": ["a", "b"]}
    ok, written = _save(small, max_blob_bytes=1_000_000, monkeypatch=monkeypatch)
    assert ok is True
    assert written == small


def test_risk_keys_are_evicted_only_as_a_last_resort(monkeypatch):
    """When even the risk keys cannot all fit, the row is still written.

    Refusing to write at all would be worse: the previous boot's state would go
    stale silently rather than partially.
    """
    cache = {f"_peak_SYM{i}_2026-01-01": float(i) for i in range(200)}
    ok, written = _save(cache, max_blob_bytes=500, monkeypatch=monkeypatch)
    assert ok is True
    assert len(written) < len(cache)
    assert len(json.dumps(written)) <= 500
