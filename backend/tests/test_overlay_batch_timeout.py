"""The trade-overlay LLM batch must be able to give up.

`as_completed(future_map)` was called with NO timeout, so a single OpenRouter
worker that never returns wedges the entire run forever. Measured 2026-08-24:
two of four backtests hung this way and had to be killed by hand —

    bt 331865  hung at overlay 32/33
    bt 186584  hung at overlay 12/33
    bt 613323  hung at overlay 32/33, 1825s idle, killed at 48% progress

The sibling event-maintenance batch already has an abandon deadline
("abandoned 1 future(s) after 276.9s — results are INCOMPLETE"); this one did
not.

DEFAULT OFF. `_apply_ml_and_overlay_to_scores` is HIGH blast radius by impact
analysis (1 direct caller, 5 run_once flow hits, 3 modules) and this module runs
the LIVE real-money instance, so the timeout is opt-in: with the key unset the
call is exactly as before. Missing overlay results already degrade to `({}, {})`
at the consume site, so abandoning a straggler is an established path, not a new
failure mode.
"""
import os
import re
import sys

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

_SRC = open(os.path.join(_backend, "strategies",
                        "graph_nexus_analysis.py")).read()


def test_the_overlay_batch_can_time_out():
    assert "overlay_batch_timeout_sec" in _SRC, (
        "the trade-overlay batch has no abandon deadline; one hung LLM worker "
        "wedges the whole run forever")


def test_as_completed_is_not_called_bare_on_the_overlay_futures():
    """The exact defect: `as_completed(future_map)` with no timeout."""
    assert not re.search(r"as_completed\(future_map\)\s*,", _SRC), (
        "as_completed(future_map) is still called without a timeout")


def test_the_timeout_defaults_to_off():
    """Live trading must be byte-identical unless an operator opts in."""
    m = re.search(r'overlay_batch_timeout_sec["\']\s*,\s*([0-9.]+)', _SRC)
    assert m, "could not find the overlay_batch_timeout_sec default"
    assert float(m.group(1)) == 0.0, (
        f"overlay batch timeout defaults to {m.group(1)}, not 0 (off) — this "
        "module runs the live real-money instance")


def test_abandoned_workers_are_logged_not_silent():
    """A silently truncated overlay is worse than a slow one."""
    assert "overlay batch: abandoned" in _SRC.lower(), (
        "abandoning overlay workers must say which symbols were dropped")
