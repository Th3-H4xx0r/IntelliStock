# backend/tests/test_broker_pause_resume_cycle.py
"""End-to-end test of the pause -> snapshot-restore -> resume cycle."""
import importlib
import datetime as _dt
import pytest
from unittest.mock import MagicMock, patch


@pytest.fixture(autouse=True)
def _reset_state():
    import sys
    from backend import llm_critical_guard, backtest_bar_snapshot, backtest_critical_abort
    importlib.reload(llm_critical_guard)
    importlib.reload(backtest_bar_snapshot)
    importlib.reload(backtest_critical_abort)
    # The backtest_critical_abort module imports `from backtest_bar_snapshot
    # import restore` via the BARE alias, which python registers as a distinct
    # sys.modules entry from `backend.backtest_bar_snapshot`. If we capture()
    # into the backend.* slot but restore() reads from the bare alias's slot,
    # the snapshot is invisible. Force both aliases to point at the same module
    # object so capture+restore share the single `_last_good_bar` global.
    sys.modules["backtest_bar_snapshot"] = backtest_bar_snapshot
    sys.modules["llm_critical_guard"] = llm_critical_guard
    sys.modules["backtest_critical_abort"] = backtest_critical_abort
    # The BacktestResults pause write goes through the split store now, so
    # patching _get_conn_and_r no longer cages it. Cage the seam itself: a
    # unit test must never touch a real database.
    backtest_critical_abort._write_backtest_pause_status = lambda *a, **kw: None
    yield


def test_capture_then_critical_then_restore_then_resume(monkeypatch):
    """Simulate a full pause/resume cycle."""
    from backend import backtest_bar_snapshot, backtest_critical_abort
    from backend.llm_critical_guard import LLMCriticalFailure, was_already_raised, reset_state, mark_raised

    # 1. Capture a "last good bar" snapshot at end of 5/10/2025
    good_caches = {"nexus": {"sentiment_5_10": "bullish"}}
    good_portfolio = {"cash": 100000.0, "positions": {}}
    good_time = _dt.datetime(2025, 5, 11, tzinfo=_dt.timezone.utc)
    backtest_bar_snapshot.capture(
        strategy_caches=good_caches,
        portfolio_emulator=good_portfolio,
        current_time=good_time,
    )

    # 2. Simulate strategy executing bar 5/11 and corrupting state
    # (the in-memory cache gets a partial bad write)
    bad_caches = {"nexus": {"sentiment_5_10": "bullish", "sentiment_5_11_PARTIAL": "broken"}}

    # 3. LLMCriticalFailure raised mid-bar
    failure = LLMCriticalFailure(
        class_tag="azure_403_blocked",
        provider="azure",
        model="gpt-5.4-mini",
        attribution={"backtest_id": "999", "instance_id": "main", "call_site": "overlay"},
        attempts=[{"attempt": i+1, "class_tag": "azure_403_blocked", "http_status": 403,
                  "body_sample": "blocked", "ts": float(i)} for i in range(4)],
    )

    # 4. handle() restores
    apply_calls = {}
    def fake_apply(*, strategy_caches, portfolio_emulator, current_time):
        apply_calls["caches"] = strategy_caches
        apply_calls["portfolio"] = portfolio_emulator
        apply_calls["time"] = current_time

    with patch.object(backtest_critical_abort, "_get_conn_and_r", return_value=(MagicMock(), MagicMock())), \
         patch.object(backtest_critical_abort, "_enqueue_discord", return_value=None), \
         patch.object(backtest_critical_abort, "_apply_restore", side_effect=fake_apply):
        backtest_critical_abort.handle(backtest_id="999", instance_id="main", failure=failure)

    # Verify restore happened with the GOOD snapshot
    assert apply_calls["caches"] == good_caches
    assert apply_calls["portfolio"] == good_portfolio
    assert apply_calls["time"] == good_time

    # 5. After handle(), critical-guard's was_already_raised is True (the mark_raised
    # from the wrapper fires before handle() is invoked — simulate that here)
    mark_raised()
    assert was_already_raised() is True

    # 6. Operator resumes — broker's changefeed thread calls reset_state on both modules
    reset_state()  # llm_critical_guard
    backtest_critical_abort.reset_state()

    assert was_already_raised() is False
    assert backtest_critical_abort._already_alerted is False

    # 7. Bar 5/11 re-executes — capture() fires again at start of bar
    backtest_bar_snapshot.capture(
        strategy_caches=apply_calls["caches"],  # restored state is the input
        portfolio_emulator=apply_calls["portfolio"],
        current_time=apply_calls["time"],
    )
    # Verify the snapshot is fresh
    assert backtest_bar_snapshot.has_snapshot() is True
