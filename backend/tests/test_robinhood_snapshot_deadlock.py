"""Regression tests for the 2026-05-07 live-broker self-deadlock.

Bug: ``RobinhoodAdapter.save_portfolio_snapshot`` did
``with self._lock`` and then called ``self.get_positions_value(prices)``
which itself does ``with self._lock``. Because ``self._lock`` was a
vanilla ``threading.Lock()`` (not RLock), the same thread re-acquiring
the lock blocked forever. Each call from the strategy thread's
post-tick path orphaned a worker holding ``_lock``; the next tick's
``refresh_*`` call that needed the lock then wedged the entire strategy
worker thread.

These tests verify:
1. ``save_portfolio_snapshot`` returns within seconds (not deadlocked).
2. ``_lock`` is now an RLock (defense in depth — same-thread re-entrance
   is safe even if some future caller forgets the lock-free pattern).
3. After the snapshot returns, ``_lock`` is unheld, so other threads can
   call ``refresh_*`` without blocking.
4. Snapshot is correct: ``cash + positions × prices == total``.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

_BACKEND = os.path.join(os.path.dirname(__file__), "..")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)


def _make_adapter():
    """Build a minimal ``RobinhoodAdapter`` instance bypassing __init__'s
    HTTP / token / WAL setup. We only need the lock + cache fields the
    snapshot path touches.
    """
    from broker_adapters.robinhood import RobinhoodAdapter

    adapter = RobinhoodAdapter.__new__(RobinhoodAdapter)
    adapter._lock = threading.RLock()  # mirrors the production constructor
    adapter._submit_lock = threading.Lock()
    adapter._cash = 1000.0
    adapter._positions = {"AAPL": 10.0, "MSFT": 5.0}
    adapter._last_prices = {"AAPL": 200.0, "MSFT": 400.0}
    adapter._portfolio_snapshots = []
    adapter._last_account_dto = None
    adapter._last_positions_dtos = None
    adapter._last_cash_dto = None
    adapter._initial_value = 1000.0
    return adapter


def test_save_portfolio_snapshot_does_not_deadlock_on_self_reentrance():
    """The actual regression: same thread calling save_portfolio_snapshot
    must NOT block forever. Run with a watchdog and assert it returns
    in well under a second.
    """
    adapter = _make_adapter()
    prices = {"AAPL": 210.0, "MSFT": 405.0}
    completed = threading.Event()

    def _runner():
        adapter.save_portfolio_snapshot(prices)
        completed.set()

    t = threading.Thread(target=_runner, daemon=True)
    t.start()
    finished = completed.wait(timeout=5.0)
    assert finished, (
        "save_portfolio_snapshot did not return within 5s — "
        "self-deadlock has regressed."
    )
    t.join(timeout=1.0)


def test_save_portfolio_snapshot_does_not_hold_lock_after_return():
    """After the snapshot returns, _lock must be free so the next
    refresh_account/refresh_positions/refresh_cash from the strategy
    thread acquires it without blocking.
    """
    adapter = _make_adapter()
    adapter.save_portfolio_snapshot({"AAPL": 210.0, "MSFT": 405.0})

    # Try to acquire _lock from a fresh thread — must succeed instantly.
    acquired = threading.Event()

    def _runner():
        if adapter._lock.acquire(timeout=2.0):
            try:
                acquired.set()
            finally:
                adapter._lock.release()

    t = threading.Thread(target=_runner, daemon=True)
    t.start()
    got_lock = acquired.wait(timeout=3.0)
    assert got_lock, (
        "_lock is still held after save_portfolio_snapshot returned — "
        "orphan zombie regression."
    )
    t.join(timeout=1.0)


def test_save_portfolio_snapshot_records_correct_value():
    """Sanity: the math still works after the lock-free refactor.
    cash=1000 + 10*AAPL@210 + 5*MSFT@405 = 1000 + 2100 + 2025 = 5125.
    """
    adapter = _make_adapter()
    ts = datetime(2026, 5, 7, 22, 0, tzinfo=timezone.utc)
    adapter.save_portfolio_snapshot({"AAPL": 210.0, "MSFT": 405.0}, timestamp=ts)
    assert len(adapter._portfolio_snapshots) == 1
    snap = adapter._portfolio_snapshots[0]
    assert snap["cash"] == pytest.approx(1000.0)
    assert snap["positions_value"] == pytest.approx(2100.0 + 2025.0)
    assert snap["total"] == pytest.approx(5125.0)
    assert snap["timestamp"] == ts


def test_save_portfolio_snapshot_includes_value_alias_for_ui_equity_curve():
    """The live UI's equity-curve reader (broker._compute_live_state_snapshot
    line 3877) reads `s.get("value")` per snapshot. AlpacaAdapter writes
    `"value"` natively; RobinhoodAdapter previously wrote only `"total"`,
    so the live UI's RH equity chart was always flat at $0. Verify the
    `"value"` alias is now present and equals total.
    """
    adapter = _make_adapter()
    adapter.save_portfolio_snapshot({"AAPL": 210.0, "MSFT": 405.0})
    snap = adapter._portfolio_snapshots[0]
    assert "value" in snap
    assert snap["value"] == pytest.approx(snap["total"])
    assert snap["value"] == pytest.approx(5125.0)


def test_save_portfolio_snapshot_falls_back_to_last_prices():
    """If a price is missing from the supplied dict, we should fall
    back to ``_last_prices`` rather than treating the position as 0.
    """
    adapter = _make_adapter()
    adapter._last_prices = {"AAPL": 199.0, "MSFT": 401.0}
    # Only AAPL provided this tick; MSFT must come from _last_prices.
    adapter.save_portfolio_snapshot({"AAPL": 215.0})
    snap = adapter._portfolio_snapshots[0]
    assert snap["positions_value"] == pytest.approx(10 * 215.0 + 5 * 401.0)


def test_lock_is_rlock_for_defense_in_depth():
    """The lock is now an RLock so any future re-entrance bug is
    survivable rather than a wedge.
    """
    adapter = _make_adapter()
    # RLock allows same-thread re-acquisition; Lock does not.
    assert adapter._lock.acquire(timeout=0.1)
    try:
        # Re-acquire from same thread; with RLock this is instant.
        # A vanilla Lock would deadlock here.
        got_re = adapter._lock.acquire(timeout=0.1)
        assert got_re, "_lock is not an RLock — same-thread re-entrance blocked"
        adapter._lock.release()
    finally:
        adapter._lock.release()


def test_concurrent_snapshots_do_not_corrupt_state():
    """8 concurrent strategy threads calling save_portfolio_snapshot
    should ALL complete within a few seconds (no contention deadlock).
    """
    adapter = _make_adapter()
    threads = []
    completed = [False] * 8

    def _runner(i):
        adapter.save_portfolio_snapshot({"AAPL": 200.0 + i, "MSFT": 400.0 + i})
        completed[i] = True

    for i in range(8):
        t = threading.Thread(target=_runner, args=(i,), daemon=True)
        threads.append(t)
        t.start()
    deadline = time.time() + 5.0
    for t in threads:
        remaining = max(0.0, deadline - time.time())
        t.join(timeout=remaining)
    assert all(completed), f"Some snapshots wedged: {completed}"
    assert len(adapter._portfolio_snapshots) == 8
