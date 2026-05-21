"""Integration tests for broker live-boot snapshot wiring (Phase 1).

Targets ``backend.broker_snapshot_helpers`` because ``backend.broker``
itself is not import-safe (it runs argparse + the main backtest/live
loop at module load — see the comment near broker.py:2910 referencing
the extraction pattern). broker.py imports ``_invoke_persist_backtest_snapshot``
lazily from this module at the end-of-backtest call site, so the
helpers here are the actual production code path.
"""

from __future__ import annotations

import datetime as dt

import pytest

from backend import broker_snapshot_helpers as bsh
from backend import strategy_cache_persistence as scp


def _today_iso():
    return dt.date.today().isoformat()


def test_backtest_persist_snapshot_called_with_expected_fields(monkeypatch):
    """When ``_invoke_persist_backtest_snapshot`` is called, it computes
    hashes from the provided ``config_dict``, resolves the nexus module
    path, and delegates to ``persist_backtest_snapshot`` with the right
    args.
    """
    captured = {}

    def _fake_persist(conn, r, *, instance_id, strategy_name, cache,
                      config_hash, module_hash, start_date, end_date,
                      max_blob_bytes=5_000_000):
        captured.update({
            "instance_id": instance_id,
            "strategy_name": strategy_name,
            "cache": cache,
            "config_hash": config_hash,
            "module_hash": module_hash,
            "start_date": start_date,
            "end_date": end_date,
        })
        return True

    monkeypatch.setattr(scp, "persist_backtest_snapshot", _fake_persist)
    # Ensure env-gate doesn't short-circuit the call.
    monkeypatch.delenv("NEXUS_BACKTEST_SNAPSHOT_WRITE", raising=False)

    ok = bsh._invoke_persist_backtest_snapshot(
        conn=object(),
        r=object(),
        base_instance_id="main",
        strategy_name="graph_nexus_analysis",
        strategy_cache={"_momentum_watchlist": ["AAPL"]},
        config_dict={
            "strategy_name": "graph_nexus_analysis",
            "prompt_versions": {"sentiment": "v1"},
            "llm_stages": {},
            "history_scope_id_inputs": {},
            "lookback_learning_days": 120,
        },
        start_date="2025-11-10",
        end_date=_today_iso(),
    )
    assert ok is True
    assert captured["instance_id"] == "main"
    assert captured["strategy_name"] == "graph_nexus_analysis"
    assert captured["cache"] == {"_momentum_watchlist": ["AAPL"]}
    assert captured["start_date"] == "2025-11-10"
    assert captured["end_date"] == _today_iso()
    # config_hash and module_hash are computed inside the helper; sanity-check format
    assert len(captured["config_hash"]) == 16
    # module_hash is 16 hex chars if file readable; "missing" (7 chars) otherwise
    assert len(captured["module_hash"]) in (7, 16)


def test_invoke_persist_returns_false_when_disabled(monkeypatch):
    """When NEXUS_BACKTEST_SNAPSHOT_WRITE=off, the helper short-circuits
    without calling persist."""
    monkeypatch.setenv("NEXUS_BACKTEST_SNAPSHOT_WRITE", "off")
    called = {"count": 0}

    def _fake_persist(*a, **kw):
        called["count"] += 1
        return True

    monkeypatch.setattr(scp, "persist_backtest_snapshot", _fake_persist)
    ok = bsh._invoke_persist_backtest_snapshot(
        conn=object(), r=object(),
        base_instance_id="main", strategy_name="x",
        strategy_cache={}, config_dict={},
        start_date="", end_date="2026-05-21",
    )
    assert ok is False
    assert called["count"] == 0


def test_live_boot_loads_snapshot_when_present(monkeypatch):
    """Snapshot returned by load_with_fallback -> cache hydrated, gap=[]."""
    monkeypatch.setattr(scp, "load_with_fallback", lambda *a, **kw: (
        {"_momentum_watchlist": ["AAPL"]}, "ok", {"end_date": _today_iso()}
    ))
    from backend import broker_snapshot_helpers as bsh
    cache, reason, gap_dates = bsh._invoke_load_snapshot_with_gap(
        conn=object(), r=object(),
        instance_id="main", strategy_name="graph_nexus_analysis",
        current_config_hash="abc", current_module_hash="mod",
    )
    assert reason == "ok"
    assert cache == {"_momentum_watchlist": ["AAPL"]}
    assert gap_dates == []


def test_live_boot_returns_gap_dates_when_snapshot_partial(monkeypatch):
    """Snapshot ends 3 days ago -> gap_dates is the trading days between."""
    three_days_ago = (dt.date.today() - dt.timedelta(days=3)).isoformat()
    monkeypatch.setattr(scp, "load_with_fallback", lambda *a, **kw: (
        {"k": 1}, "ok", {"end_date": three_days_ago}
    ))
    from backend import broker_snapshot_helpers as bsh
    cache, reason, gap_dates = bsh._invoke_load_snapshot_with_gap(
        conn=object(), r=object(),
        instance_id="main", strategy_name="graph_nexus_analysis",
        current_config_hash="abc", current_module_hash="mod",
    )
    assert reason == "ok"
    assert isinstance(gap_dates, list)
    # all returned dates are weekdays between snapshot end (exclusive) and today (inclusive)
    assert all(d > three_days_ago for d in gap_dates)
    assert all(d <= _today_iso() for d in gap_dates)
    for d in gap_dates:
        dd = dt.date.fromisoformat(d)
        assert dd.weekday() < 5, f"{d} is not a weekday"


def test_live_boot_falls_back_when_snapshot_missing(monkeypatch):
    """Snapshot not found -> cache=None, gap=None (signal full lookback)."""
    monkeypatch.setattr(scp, "load_with_fallback", lambda *a, **kw: (None, "no_match", None))
    from backend import broker_snapshot_helpers as bsh
    cache, reason, gap_dates = bsh._invoke_load_snapshot_with_gap(
        conn=object(), r=object(),
        instance_id="main", strategy_name="graph_nexus_analysis",
        current_config_hash="abc", current_module_hash="mod",
    )
    assert cache is None
    assert reason == "no_match"
    assert gap_dates is None
