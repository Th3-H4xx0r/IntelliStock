# backend/tests/test_backtest_bar_snapshot.py
import datetime as _dt
import importlib
import pytest


@pytest.fixture(autouse=True)
def _reset_state():
    from backend import backtest_bar_snapshot
    importlib.reload(backtest_bar_snapshot)
    yield


def test_no_snapshot_returns_none():
    from backend.backtest_bar_snapshot import restore, has_snapshot
    assert has_snapshot() is False
    assert restore() is None


def test_capture_and_restore_roundtrip():
    from backend.backtest_bar_snapshot import capture, restore, has_snapshot
    caches = {"nexus": {"key": "value", "list": [1, 2, 3]}}
    portfolio = {"cash": 100000.0, "positions": {"AAPL": 10}}
    ts = _dt.datetime(2025, 5, 10, tzinfo=_dt.timezone.utc)

    capture(strategy_caches=caches, portfolio_emulator=portfolio, current_time=ts)
    assert has_snapshot() is True

    restored = restore()
    assert restored is not None
    r_caches, r_portfolio, r_time = restored
    assert r_caches == caches
    assert r_portfolio == portfolio
    assert r_time == ts


def test_capture_overwrites_previous_snapshot():
    from backend.backtest_bar_snapshot import capture, restore
    t1 = _dt.datetime(2025, 5, 10, tzinfo=_dt.timezone.utc)
    t2 = _dt.datetime(2025, 5, 11, tzinfo=_dt.timezone.utc)
    capture(strategy_caches={"a": 1}, portfolio_emulator={}, current_time=t1)
    capture(strategy_caches={"a": 2}, portfolio_emulator={}, current_time=t2)
    r_caches, _, r_time = restore()
    assert r_caches == {"a": 2}
    assert r_time == t2


def test_portfolio_is_deep_copied():
    """Mutating the restored portfolio must not affect the live one."""
    from backend.backtest_bar_snapshot import capture, restore
    portfolio = {"cash": 100000.0, "positions": {"AAPL": 10}}
    ts = _dt.datetime(2025, 5, 10, tzinfo=_dt.timezone.utc)
    capture(strategy_caches={}, portfolio_emulator=portfolio, current_time=ts)
    portfolio["cash"] = 0  # mutate live
    _, r_portfolio, _ = restore()
    assert r_portfolio["cash"] == 100000.0  # restored kept original


def test_discard():
    from backend.backtest_bar_snapshot import capture, restore, discard, has_snapshot
    ts = _dt.datetime(2025, 5, 10, tzinfo=_dt.timezone.utc)
    capture(strategy_caches={"x": 1}, portfolio_emulator={}, current_time=ts)
    assert has_snapshot() is True
    discard()
    assert has_snapshot() is False
    assert restore() is None


def test_strategy_cache_serialize_skips_unpicklable():
    """Reuse the existing _serialize_cache_for_blob, which marks unpicklable
    entries via __skipped_fields__. Confirm a lambda doesn't blow up capture."""
    from backend.backtest_bar_snapshot import capture, restore
    ts = _dt.datetime(2025, 5, 10, tzinfo=_dt.timezone.utc)
    caches = {"nexus": {"data": 42, "unpicklable_lambda": lambda x: x}}
    capture(strategy_caches=caches, portfolio_emulator={}, current_time=ts)
    r_caches, _, _ = restore()
    # The picklable parts survive; the lambda is dropped via __skipped_fields__
    assert r_caches["nexus"]["data"] == 42
    assert "unpicklable_lambda" not in r_caches["nexus"]
