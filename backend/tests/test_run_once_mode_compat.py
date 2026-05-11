"""Regression tests for scheduler ``mode`` compatibility on run_once strategies."""

from __future__ import annotations

import inspect
import os
import sys
from datetime import datetime, timezone

import pytest

_BACKEND = os.path.join(os.path.dirname(__file__), "..")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from strategies import earnings, ml_news  # noqa: E402


def _utc_now():
    return datetime(2025, 11, 10, 13, 20, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("strategy_cls", "param"),
    [
        (ml_news.MlNews, "mode"),
        (earnings.Earnings, "mode"),
    ],
)
def test_shipped_run_once_strategies_accept_scheduler_mode_keyword(strategy_cls, param):
    """Broker forwards ``mode=...`` to run_once strategies; signatures must accept it."""
    assert param in inspect.signature(strategy_cls.run_once).parameters


@pytest.mark.parametrize("mode", ["IDLE", "MONITOR"])
def test_ml_news_non_full_modes_return_empty_without_ingest(monkeypatch, mode):
    """MlNews has no monitor/idle task, so it must not fetch news outside FULL."""

    def fail_fetch(*_args, **_kwargs):  # pragma: no cover - should never be reached
        raise AssertionError("MlNews attempted ingestion during a non-FULL scheduler tick")

    monkeypatch.setattr(ml_news, "_fetch_alpaca_news_batch", fail_fetch)

    out = ml_news.MlNews().run_once(
        ["SNDK"],
        {"SNDK": 10.0},
        _utc_now(),
        {},
        {},
        strategy_cache={},
        mode=mode,
    )

    assert out == {}


def test_ml_news_legacy_none_mode_still_runs_and_returns_holds(monkeypatch):
    """The broker also passes ``mode=None`` in backtests; this must not TypeError."""

    monkeypatch.setattr(ml_news, "_get_db_conn", lambda: None)
    monkeypatch.setattr(ml_news, "_fetch_alpaca_news_batch", lambda *_args, **_kwargs: [])

    out = ml_news.MlNews().run_once(
        ["SNDK"],
        {"SNDK": 10.0},
        _utc_now(),
        {},
        {},
        strategy_cache={},
        mode=None,
    )

    assert out == {"SNDK": {"score": 0, "reason": "No news found"}}


@pytest.mark.parametrize("mode", ["IDLE", "MONITOR"])
def test_earnings_non_full_modes_return_empty_fast(mode):
    """Earnings has no monitor/idle task and should skip expensive work there."""
    out = earnings.Earnings().run_once(
        ["SNDK"],
        {"SNDK": 10.0},
        _utc_now(),
        {},
        {},
        strategy_cache={},
        mode=mode,
    )

    assert out == {}
