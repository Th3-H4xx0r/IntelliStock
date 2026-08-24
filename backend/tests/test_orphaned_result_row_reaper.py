"""A BacktestResults row with NO queue row must still be reconciled.

The liveness reaper is driven by `BacktestInstances`, the queue table, which
follows a delete-on-completion contract. So a `BacktestResults` row left at
status='running' AFTER its queue row is gone has nothing that will ever fix it:
the reaper iterates queue rows and never sees it.

Measured 2026-08-24: bt 984041 (instance v2-conv-trt) sat at status='running'
for **41 hours** with no queue row and no container, and the UI reported it as a
concurrently-running backtest alongside the one real run. An earlier fix
(dropping `run: True` from the queue filter) did not reach it, because the
problem is not the flag — it is that the row is not in that table at all.

Two consequences, both observed:
  * the UI shows backtests running that finished or died hours ago;
  * a stale running row BLOCKS that instance's next launch, which silently
    stopped several backtests from ever starting.
"""
import os
import sys

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

_SRC = open(os.path.join(_backend, "engines", "backtest_engine.py")).read()


def test_the_reaper_also_sweeps_result_rows_without_a_queue_row():
    assert "_sweep_orphaned_result_rows" in _SRC, (
        "nothing reconciles a BacktestResults row whose queue row is already "
        "gone; such a row stays 'running' forever")


def test_the_orphan_sweep_checks_the_container_before_touching_a_row():
    """It must never stop a run whose container is alive."""
    i = _SRC.find("def _sweep_orphaned_result_rows")
    assert i > 0
    body = _SRC[i:i + 4000]
    assert "running_names" in body, (
        "the orphan sweep does not check the live container set — it could "
        "stop a healthy run")


def test_the_orphan_sweep_respects_the_launch_grace_period():
    """A container that was just launched may not be listed by Docker yet."""
    i = _SRC.find("def _sweep_orphaned_result_rows")
    body = _SRC[i:i + 4000]
    assert "_container_launch_times" in body, (
        "the orphan sweep ignores the launch grace period and would reap a "
        "backtest that is still starting up")


def test_the_orphan_sweep_leaves_paused_runs_alone():
    """A paused run idles deliberately with its container alive."""
    i = _SRC.find("def _sweep_orphaned_result_rows")
    body = _SRC[i:i + 4000]
    assert "paused" in body, (
        "the orphan sweep does not exempt paused runs")
