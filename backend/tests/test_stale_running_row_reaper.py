"""A stop-requested row whose container died must still be reconciled.

The liveness reaper checks, every 2 minutes, that every backtest the DB believes
is running still has a live Docker container — and marks the dead ones
`stopped`. But it selected on ``{"run": True, "status": "running"}``.

A stop request sets ``run=False``. If the container then dies WITHOUT running its
own cleanup — killed, OOM, hung and reaped, or preempted — the row is left at
``run=False, status='running'`` and the reaper's own filter excludes it. Nothing
else reconciles it, so it stays "running" forever.

Observed 2026-08-24: three backtests showed RUNNING in the UI with ELAPSED
counters past 5 hours while exactly ONE container was alive. The two corpses had
made no LLM call in 4.9 and 5.3 hours. This is the same
"status='running'/run=False accumulated 100+ zombies" failure the delete path
further down this file already warns about.

It is not only cosmetic: a stale `running` row for an instance BLOCKS that
instance's next launch, which silently prevented two backtests from ever
starting (the API accepted them and returned an id; no container ever came up).
"""
import os
import re
import sys

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

_SRC = open(os.path.join(_backend, "engines", "backtest_engine.py")).read()


def test_the_reaper_does_not_filter_on_run_true():
    """`run: True` in the liveness query is what strands stopped-but-dead rows."""
    assert '{"run": True, "status": "running"}' not in _SRC, (
        "the liveness reaper still selects on run=True, so a row left at "
        "run=False/status=running by a stop request whose container died is "
        "never reconciled")


def test_the_reaper_still_selects_only_running_rows():
    """It must not start sweeping queued or finished rows."""
    assert '{"status": "running"}' in _SRC, (
        "the liveness reaper no longer selects status=running rows")


def test_the_paused_exemption_survives():
    """A paused run idles with its container alive and must NOT be reaped."""
    assert "is_paused" in _SRC and "run\": False" in _SRC.replace("'", '"'), (
        "the paused-row exemption was removed")


def test_the_grace_period_survives():
    """A container that was just launched may not be listed yet."""
    assert "_container_launch_times" in _SRC, (
        "the launch grace period was removed; freshly started containers would "
        "be reaped before Docker lists them")
