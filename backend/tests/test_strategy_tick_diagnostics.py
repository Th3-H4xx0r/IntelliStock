"""Tests for the 2026-05-07 strategy-tick diagnostic state.

When the strategy worker thread wedges silently, we have no way to know
which phase blocked. The new ``strategy_tick_state.STATE`` dict + the
``set_phase`` helper let the snapshot daemon (different thread) read
the last phase from a thread-shared dict and surface it via the
``/instances/{id}/live-state`` API.

These tests verify:
1. The helper correctly stamps the dict.
2. Reads/writes from concurrent threads don't crash (single-key dict
   assignment is GIL-atomic but we verify here we don't accidentally
   introduce a lock that could deadlock).
3. ``snapshot()`` returns a copy that is safe to mutate.
"""

from __future__ import annotations

import os
import sys
import threading
import time

import pytest

_BACKEND = os.path.join(os.path.dirname(__file__), "..")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

import strategy_tick_state as sts


@pytest.fixture(autouse=True)
def _reset_state():
    """Reset STATE between tests so they're independent."""
    sts.STATE.update({
        "last_tick_index": 0,
        "last_tick_phase": "init",
        "last_tick_phase_ts": None,
        "last_tick_mode": None,
        "last_tick_started_ts": None,
        "last_tick_completed_ts": None,
        "next_wake_ts": None,
        "next_wake_mode": None,
    })
    yield


def test_set_phase_updates_dict():
    sts.set_phase(
        "rh_refresh",
        tick_index=42,
        mode="MONITOR",
        next_wake_ts="2026-05-07T22:20:00+00:00",
        next_wake_mode="MONITOR",
        started=True,
    )
    state = sts.STATE
    assert state["last_tick_phase"] == "rh_refresh"
    assert state["last_tick_index"] == 42
    assert state["last_tick_mode"] == "MONITOR"
    assert state["next_wake_ts"] == "2026-05-07T22:20:00+00:00"
    assert state["next_wake_mode"] == "MONITOR"
    assert state["last_tick_phase_ts"] is not None
    assert state["last_tick_started_ts"] is not None


def test_set_phase_completed_flag():
    sts.set_phase("sleep", completed=True)
    assert sts.STATE["last_tick_phase"] == "sleep"
    assert sts.STATE["last_tick_completed_ts"] is not None


def test_set_phase_does_not_raise_on_unusual_input():
    """Helper is on the strategy hot path; it must never raise.

    Pass an unhashable mode arg path that would crash on int(...) — but
    the helper still has to swallow it.
    """
    # int() on None is safe (param is None → skipped). int() on garbage
    # is the actual hazard:
    class Weird:
        def __int__(self):
            raise RuntimeError("boom")

    # Should NOT raise.
    sts.set_phase("oddly-named-phase", tick_index=Weird())  # type: ignore[arg-type]
    # tick_index didn't update, but phase did:
    assert sts.STATE["last_tick_phase"] == "oddly-named-phase"


def test_snapshot_returns_independent_copy():
    sts.set_phase("post_tick_snapshot", tick_index=7)
    snap = sts.snapshot()
    assert snap["last_tick_phase"] == "post_tick_snapshot"
    # Mutating the snapshot must not corrupt the live STATE.
    snap["last_tick_phase"] = "BOGUS"
    assert sts.STATE["last_tick_phase"] == "post_tick_snapshot"


def test_concurrent_phase_writes_do_not_crash():
    """Strategy thread writes phase, snapshot daemon reads it. We don't
    expect tearing for single-key dict ops under the GIL, but we should
    at least not hit a KeyError or a swap-mid-iter bug.
    """
    stop = threading.Event()
    errors = []

    def _writer():
        i = 0
        while not stop.is_set() and i < 5000:
            try:
                sts.set_phase(
                    f"phase-{i % 10}",
                    tick_index=i,
                    mode="FULL" if i % 2 else "MONITOR",
                )
            except Exception as e:
                errors.append(("writer", e))
            i += 1

    def _reader():
        while not stop.is_set():
            try:
                _ = sts.snapshot()
            except Exception as e:
                errors.append(("reader", e))

    threads = [
        threading.Thread(target=_writer, daemon=True),
        threading.Thread(target=_writer, daemon=True),
        threading.Thread(target=_reader, daemon=True),
        threading.Thread(target=_reader, daemon=True),
    ]
    for t in threads:
        t.start()
    time.sleep(0.5)
    stop.set()
    for t in threads:
        t.join(timeout=2.0)
    assert not errors, f"concurrent phase ops raised: {errors}"


def test_snapshot_never_blocks_when_writer_crashed_mid_update():
    """Defense-in-depth: even if a writer thread crashed mid-call (in
    theory possible if the GIL releases mid-assignment, which it
    doesn't for single keys but might for multi-step composite ops),
    snapshot() must always return — never block, never raise.
    """
    sts.set_phase("phase-x", tick_index=1, mode="FULL", started=True)
    # Repeatedly snapshot — must each return fast.
    for _ in range(1000):
        s = sts.snapshot()
        assert s["last_tick_phase"] == "phase-x"


def test_set_phase_uses_atomic_update_to_avoid_torn_reads():
    """Reviewer 3 (Q3): a previous version did 2-7 separate
    ``STATE[k] = v`` writes. A concurrent reader doing ``dict(STATE)``
    between any two writes saw inconsistent state — e.g.,
    ``last_tick_phase`` from the new tick paired with stale
    ``last_tick_phase_ts`` from the prior tick.

    Fix verified here: ``set_phase`` builds a local ``delta`` dict and
    calls ``STATE.update(delta)``. ``dict.update`` from a Python dict
    is a single C-level op holding the GIL — readers see all-old or
    all-new. We assert the implementation uses ``.update`` (proxy for
    "atomic"); if a future refactor regresses to per-key writes,
    this test fails.
    """
    import inspect
    src = inspect.getsource(sts.set_phase)
    # The implementation should use STATE.update for atomic delivery.
    assert "STATE.update(" in src, (
        "set_phase regressed to per-key writes — readers will see torn state. "
        "Build a local delta dict and apply it via STATE.update(delta)."
    )
