"""Crash keep-alive guard in instance.py.

instance.py imports python-socketio at module load; stub it so the module imports
in a test env without that dependency (prod containers have it).
"""
from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest

sys.modules.setdefault("socketio", MagicMock())

import instance as inst  # noqa: E402


class _Exit(BaseException):
    """Stand-in for os._exit so a test can assert it fired and stop the function.

    Subclasses BaseException (not Exception) so production `except Exception:`
    blocks don't swallow it — real os._exit hard-exits and is never caught."""


def _exit_recorder(store):
    def _e(code):
        store.append(code)
        raise _Exit()
    return _e


@pytest.fixture(autouse=True)
def _reset_instance_state(monkeypatch):
    monkeypatch.setattr(inst, "_crash_entered", False)
    monkeypatch.setattr(inst, "_crash_loop_latched", False)
    monkeypatch.setattr(inst, "_KEEPALIVE_ENABLED", True)
    monkeypatch.setattr(inst, "broker_process", None)
    monkeypatch.setattr(inst, "args_list", ["instance.py", "test-id"])
    yield
    inst._crash_entered = False
    inst._crash_loop_latched = False


# --- gating + helpers ------------------------------------------------------

def test_keepalive_enabled_for_kind():
    assert inst._keepalive_enabled_for_kind("kalshi") is False
    assert inst._keepalive_enabled_for_kind("KALSHI") is False
    assert inst._keepalive_enabled_for_kind(" Kalshi ") is False
    assert inst._keepalive_enabled_for_kind(None) is True
    assert inst._keepalive_enabled_for_kind("") is True
    assert inst._keepalive_enabled_for_kind("equities") is True


def test_changefeed_backoff_caps():
    assert inst._changefeed_backoff(0) == 2.0
    assert inst._changefeed_backoff(1) == 2.0
    assert inst._changefeed_backoff(3) == 6.0
    assert inst._changefeed_backoff(99) == 10.0


def test_operator_stop_requested(monkeypatch):
    fake_store = MagicMock()
    get = fake_store.get
    monkeypatch.setattr(inst, "store", fake_store)
    get.return_value = {"runCommand": False}
    assert inst._operator_stop_requested(None, "id") is True
    get.return_value = {"runCommand": True}
    assert inst._operator_stop_requested(None, "id") is False
    get.return_value = {}  # field absent defaults to running
    assert inst._operator_stop_requested(None, "id") is False
    get.return_value = None  # no doc
    assert inst._operator_stop_requested(None, "id") is False
    get.side_effect = RuntimeError("db down")  # DB error never ends the block
    assert inst._operator_stop_requested(None, "id") is False


# --- trip_crash idempotency + side effects ---------------------------------

def test_trip_crash_runs_side_effects_once(monkeypatch):
    monkeypatch.setattr(inst, "args_list", ["instance.py", "alpaca-main"])
    writes, marks, alerts = [], [], []
    monkeypatch.setattr(inst, "_write_crash_to_logfile", lambda iid, text: writes.append((iid, text)))
    monkeypatch.setattr(inst, "_mark_instance_crashed", lambda iid, reason: marks.append((iid, reason)))
    import live_alerts
    monkeypatch.setattr(live_alerts, "alert_instance_crash", lambda **kw: alerts.append(kw))

    inst.trip_crash("boom one")
    inst.trip_crash("boom two")  # idempotent — ignored

    assert inst._crash_entered is True
    assert len(writes) == 1 and len(marks) == 1 and len(alerts) == 1
    assert marks[0] == ("alpaca-main", "boom one")
    assert alerts[0]["reason"] == "boom one"
    assert alerts[0]["instance_id"] == "alpaca-main"


def test_trip_crash_terminates_broker(monkeypatch):
    proc = MagicMock()
    proc.poll.return_value = None  # alive
    monkeypatch.setattr(inst, "broker_process", proc)
    monkeypatch.setattr(inst, "_write_crash_to_logfile", lambda *a, **k: None)
    monkeypatch.setattr(inst, "_mark_instance_crashed", lambda *a, **k: None)
    import live_alerts
    monkeypatch.setattr(live_alerts, "alert_instance_crash", lambda **kw: None)
    inst.trip_crash("halt now")
    proc.terminate.assert_called_once()


# --- the indefinite block --------------------------------------------------

def test_block_exits_only_on_operator_stop(monkeypatch):
    monkeypatch.setattr(inst, "_operator_stop_requested", lambda conn, iid: True)
    exits = []
    monkeypatch.setattr(inst.os, "_exit", _exit_recorder(exits))
    with pytest.raises(_Exit):
        inst._block_until_operator_stop(poll_sec=0, max_iterations=5)
    assert exits == [0]


def test_block_keeps_blocking_when_no_stop(monkeypatch):
    monkeypatch.setattr(inst, "_operator_stop_requested", lambda conn, iid: False)

    def _no_exit(code):
        raise AssertionError("os._exit must NOT fire without an operator Stop")
    monkeypatch.setattr(inst.os, "_exit", _no_exit)
    # max_iterations bounds the otherwise-infinite block; returns cleanly
    inst._block_until_operator_stop(poll_sec=0, max_iterations=3)


# --- changefeed retry-then-keepalive policy --------------------------------

def _fake_watch(monkeypatch, feed):
    """Replace instance.watch with a stub whose feed() the test controls."""
    fake = MagicMock()
    fake.feed = feed
    monkeypatch.setattr(inst, "watch", fake)
    return fake


def _feed_always_errors(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("feed boom")
    _fake_watch(monkeypatch, _boom)
    monkeypatch.setattr(inst.time, "sleep", lambda s: None)


def test_changefeed_kalshi_exits_immediately(monkeypatch):
    monkeypatch.setattr(inst, "_KEEPALIVE_ENABLED", False)
    _feed_always_errors(monkeypatch)
    trips = []
    monkeypatch.setattr(inst, "trip_crash", lambda *a, **k: trips.append(a))
    exits = []
    monkeypatch.setattr(inst.os, "_exit", _exit_recorder(exits))
    with pytest.raises(_Exit):
        inst.terminate_thread_on_command(None)
    assert exits == [0]      # kalshi keeps original exit-on-error behavior
    assert trips == []       # never trips crash keep-alive


def test_changefeed_kalshi_graceful_close_returns_quietly(monkeypatch):
    # Kalshi preserves the original behavior: a graceful changefeed close ends the
    # daemon thread quietly (no os._exit, no crash keep-alive).
    monkeypatch.setattr(inst, "_KEEPALIVE_ENABLED", False)
    _fake_watch(monkeypatch, lambda *a, **k: iter([]))  # closes with no events
    trips = []
    monkeypatch.setattr(inst, "trip_crash", lambda *a, **k: trips.append(a))

    def _no_exit(code):
        raise AssertionError("kalshi graceful close must NOT os._exit")
    monkeypatch.setattr(inst.os, "_exit", _no_exit)
    inst.terminate_thread_on_command(None)  # returns quietly
    assert trips == []


def test_changefeed_equities_trips_after_retries(monkeypatch):
    monkeypatch.setattr(inst, "_KEEPALIVE_ENABLED", True)
    monkeypatch.setattr(inst, "CHANGEFEED_RETRY_MAX", 2)
    _feed_always_errors(monkeypatch)
    trips = []
    monkeypatch.setattr(inst, "trip_crash", lambda reason, exc=None: trips.append((reason, exc)))

    def _no_exit(code):
        raise AssertionError("equities must NOT os._exit on a changefeed blip")
    monkeypatch.setattr(inst.os, "_exit", _no_exit)
    inst.terminate_thread_on_command(None)  # returns after tripping
    assert len(trips) == 1
    assert "changefeed lost after 3 attempts" in trips[0][0]


def test_stocks_changefeed_equities_trips_after_retries(monkeypatch):
    monkeypatch.setattr(inst, "_KEEPALIVE_ENABLED", True)
    monkeypatch.setattr(inst, "CHANGEFEED_RETRY_MAX", 2)
    _feed_always_errors(monkeypatch)
    trips = []
    monkeypatch.setattr(inst, "trip_crash", lambda reason, exc=None: trips.append((reason, exc)))
    monkeypatch.setattr(inst.os, "_exit", lambda code: (_ for _ in ()).throw(AssertionError("no exit")))
    inst.run_instance_stocks_changefeed()
    assert len(trips) == 1
    assert "stocks) changefeed lost after 3 attempts" in trips[0][0]


def test_operator_stop_still_exits_never_trips(monkeypatch):
    """The critical safety property: pressing Stop (runCommand=False) exits cleanly
    and is NEVER treated as a crash, even with keep-alive enabled."""
    monkeypatch.setattr(inst, "_KEEPALIVE_ENABLED", True)
    _fake_watch(monkeypatch, lambda *a, **k: iter([{"new_val": {"runCommand": False}}]))
    monkeypatch.setattr(inst, "store", MagicMock())
    exits = []
    monkeypatch.setattr(inst.os, "_exit", _exit_recorder(exits))
    trips = []
    monkeypatch.setattr(inst, "trip_crash", lambda *a, **k: trips.append(a))
    with pytest.raises(_Exit):
        inst.terminate_thread_on_command(None)
    assert exits == [0]
    assert trips == []


# --- start_broker guard ----------------------------------------------------

def test_start_broker_does_not_resurrect_after_crash(monkeypatch):
    monkeypatch.setattr(inst, "_crash_entered", True)
    popen_calls = []
    monkeypatch.setattr(inst.subprocess, "Popen", lambda *a, **k: popen_calls.append((a, k)))
    inst.start_broker(["AAPL"])
    assert popen_calls == []  # broker never spawned once crashed


def test_crash_loop_latch_trips_keepalive(monkeypatch):
    monkeypatch.setattr(inst, "_KEEPALIVE_ENABLED", True)
    monkeypatch.setattr(inst, "store", MagicMock())
    monkeypatch.setattr(inst, "CRASH_LOOP_MAX_RESTARTS", 1)
    # Pre-load the restart window so the next start_broker latches.
    monkeypatch.setattr(inst, "_broker_restart_times", inst.deque(maxlen=8))
    import live_alerts
    monkeypatch.setattr(live_alerts, "alert_crash_loop", lambda **kw: None)
    trips = []
    monkeypatch.setattr(inst, "trip_crash", lambda *a, **k: trips.append(a))
    monkeypatch.setattr(inst.subprocess, "Popen", lambda *a, **k: MagicMock())
    # Drive enough restarts in-window to exceed the cap.
    for _ in range(4):
        inst.start_broker([])
    assert len(trips) >= 1
    assert "crash-loop latched" in trips[0][0]
