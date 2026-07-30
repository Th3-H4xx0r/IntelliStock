"""Bounded collection of LLM futures, with PER-CALL timeouts.

Every LLM pool in the strategy layer waited unbounded, and the outer joins that
looked like guards sat above those waits — so they abandoned work instead of
stopping it, and the caller received a silently short list.

The timeout is deliberately per CALL, not per batch: a whole-batch budget would
cut off a large healthy batch (twenty legitimate 60s calls through four workers
is five honest minutes). The window restarts on every completion, so progress
keeps the pool alive and only a genuine stall abandons the remainder.
"""
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from llm_deadline import collect_bounded, shutdown_bounded  # noqa: E402


def test_all_fast_futures_are_collected():
    ex = ThreadPoolExecutor(4)
    out = collect_bounded([ex.submit(lambda i=i: i) for i in range(4)],
                          per_call_timeout=5)
    shutdown_bounded(ex)
    assert sorted(out) == [0, 1, 2, 3]
    assert out.abandoned == 0 and out.degraded is False


def test_a_stalled_call_is_abandoned_and_reported():
    """The whole point: one wedged worker must not pin the stage."""
    ex = ThreadPoolExecutor(3)
    futs = [ex.submit(lambda: 1), ex.submit(lambda: 2),
            ex.submit(lambda: time.sleep(30) or 99)]
    started = time.monotonic()
    out = collect_bounded(futs, per_call_timeout=1.0)
    elapsed = time.monotonic() - started
    shutdown_bounded(ex)
    assert sorted(out) == [1, 2]
    assert out.abandoned == 1 and out.degraded is True
    assert elapsed < 5, f"must return once progress stalls, took {elapsed:.1f}s"


def test_a_long_but_healthy_batch_is_NOT_cut_off():
    """This is why the budget is per call, not per batch.

    Six 0.3s calls through two workers is ~0.9s of honest work — longer than
    any single call's window. A whole-batch budget would abandon them.
    """
    ex = ThreadPoolExecutor(2)
    futs = [ex.submit(time.sleep, 0.3) for _ in range(6)]
    out = collect_bounded(futs, per_call_timeout=0.6)
    shutdown_bounded(ex)
    assert out.abandoned == 0, "healthy sequential progress must not be abandoned"
    assert len(out) == 6


def test_progress_restarts_the_window():
    """A trickle of completions keeps the pool alive; a stall does not."""
    ex = ThreadPoolExecutor(1)
    futs = [ex.submit(time.sleep, 0.25) for _ in range(4)]
    out = collect_bounded(futs, per_call_timeout=0.5)
    shutdown_bounded(ex)
    assert out.abandoned == 0 and len(out) == 4


def test_max_total_caps_a_pathological_trickle():
    """Calls arriving just fast enough to reset the window forever."""
    ex = ThreadPoolExecutor(1)
    futs = [ex.submit(time.sleep, 0.2) for _ in range(20)]
    started = time.monotonic()
    out = collect_bounded(futs, per_call_timeout=5, max_total=0.5)
    elapsed = time.monotonic() - started
    shutdown_bounded(ex)
    assert elapsed < 3.0, elapsed
    assert out.degraded is True


def test_a_raising_future_does_not_kill_the_batch():
    seen = []

    def boom():
        raise RuntimeError("provider exploded")

    ex = ThreadPoolExecutor(2)
    out = collect_bounded([ex.submit(boom), ex.submit(lambda: 7)],
                          per_call_timeout=5, on_error=lambda e, f: seen.append(e))
    shutdown_bounded(ex)
    assert list(out) == [7]
    assert len(seen) == 1 and isinstance(seen[0], RuntimeError)
    assert out.abandoned == 0, "a raised future completed; it was not abandoned"


def test_empty_input_is_safe():
    out = collect_bounded([], per_call_timeout=5)
    assert list(out) == [] and out.degraded is False
    assert list(collect_bounded(None, per_call_timeout=5)) == []


def test_degradation_is_logged_so_it_is_not_silent():
    msgs = []
    ex = ThreadPoolExecutor(2)
    collect_bounded([ex.submit(lambda: 1), ex.submit(lambda: time.sleep(30))],
                    per_call_timeout=0.5, log=lambda m, c=None: msgs.append(m),
                    label="unit-test")
    shutdown_bounded(ex)
    assert any("abandoned" in m and "INCOMPLETE" in m for m in msgs), msgs


def test_nonpositive_window_falls_back_rather_than_meaning_unbounded():
    """`x or None` style handling would turn 0 into NO timeout — the same
    non-positive-means-unbounded trap as requests."""
    ex = ThreadPoolExecutor(1)
    out = collect_bounded([ex.submit(lambda: 5)], per_call_timeout=0)
    shutdown_bounded(ex)
    assert list(out) == [5], "0 must fall back to the default window, not hang"


def test_shutdown_bounded_does_not_wait_on_a_straggler():
    """`with ThreadPoolExecutor` exits via shutdown(wait=True) and would
    re-block on the future we just abandoned, undoing the bound."""
    ex = ThreadPoolExecutor(1)
    ex.submit(time.sleep, 30)
    started = time.monotonic()
    shutdown_bounded(ex)
    assert time.monotonic() - started < 2.0
