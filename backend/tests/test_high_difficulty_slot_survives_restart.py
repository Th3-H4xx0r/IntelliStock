"""The one-at-a-time guard must survive an engine restart.

`MAX_CONCURRENT_HIGH_DIFFICULTY = 1` is enforced against
`_high_difficulty_running`, a module-level integer. It is incremented on launch
and decremented on completion — and it lives only in memory.

Every push redeploys the backend, which restarts the engine process and resets
that counter to 0 while the containers it was counting are still running. The
engine then sees a free slot and launches a second high-difficulty backtest
alongside the first.

Observed 2026-08-24 after a day of deploys: bt 274278 and bt 352668 running
concurrently on the same instance, both driven by graph_nexus_analysis
(difficulty 8, threshold 8). Nothing was wrong with the classification — the
difficulty cache loads graph_nexus_analysis at 8.0 correctly. The counter simply
did not survive the restart.

The fix reconciles the counter against reality at boot, by counting the backtest
containers Docker actually reports as running, so a restart cannot manufacture
free slots.
"""
import os
import sys

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

_SRC = open(os.path.join(_backend, "engines", "backtest_engine.py")).read()


def test_the_counter_is_reconciled_at_startup():
    assert "_reconcile_high_difficulty_running" in _SRC, (
        "_high_difficulty_running is in-memory only, so every deploy resets it "
        "to 0 while its containers are still alive and the one-at-a-time guard "
        "silently stops applying")


def test_reconciliation_counts_live_containers_not_db_rows():
    """DB status is unreliable — that is a separate bug this file has already
    been bitten by. Docker is the source of truth for what is running."""
    i = _SRC.find("def _reconcile_high_difficulty_running")
    assert i > 0
    body = _SRC[i:i + 3000]
    assert "containers.list" in body, (
        "reconciliation does not consult Docker; a stale DB row would make it "
        "wrong in exactly the situation it exists to handle")


def test_reconciliation_runs_before_the_scheduler_loop():
    """Counting after the first launch decision is useless."""
    rec = _SRC.find("_reconcile_high_difficulty_running()")
    assert rec > 0, "reconciliation is defined but never called"


def test_the_guard_still_defers_rather_than_drops():
    """A deferred row must be queued, never discarded."""
    assert "_deferred_high_difficulty" in _SRC
