"""The watchdog sidecar heals itself (2026-10-02).

On 2026-10-01 at 05:48:52 UTC, during a DNS outage, the watchdog sidecar of
swing-paper and of alpaca-main both stopped writing `control_health`. Each
process stayed alive, and nothing supervised it. The order gate then refused
every opening order on `dependency.watchdog.unhealthy,dependency.watchdog.stale`
for 37 hours, until someone restarted the instances.

- The poll made three Alpaca calls through alpaca-py, which sends requests
  with no timeout. The broker's own adapter patches exactly this hang
  ("half-open TCP through Docker NAT ... wedges get_account forever"); the
  watchdog's client did not.
- The loop caught exceptions, but nothing bounded a poll that never returned.
- instance.py restarted a dead broker and never looked at the watchdog again.
"""
import io
import os
import sys
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

import benchmark_alpha.watchdog_main as runtime  # noqa: E402
from benchmark_alpha.watchdog import AlphaWatchdog  # noqa: E402


# ---------------------------------------------------------------------------
# watchdog_main: every network call is time-bounded
# ---------------------------------------------------------------------------

def test_every_alpaca_call_the_watchdog_makes_carries_a_timeout(monkeypatch):
    import requests

    seen = []

    def fake_request(self, method, url, *args, **kwargs):
        seen.append(kwargs.get("timeout"))
        raise requests.ConnectionError(
            "Failed to resolve 'paper-api.alpaca.markets'")

    monkeypatch.setattr(requests.Session, "request", fake_request)
    monkeypatch.setenv("ALPACA_WATCHDOG_KEY", "PKTEST")
    monkeypatch.setenv("ALPACA_WATCHDOG_SECRET", "SECRETTEST")
    monkeypatch.setenv("ALPACA_WATCHDOG_PAPER", "1")
    watchdog = runtime._build_runtime("swing-paper")
    probe = watchdog._probe
    for call in (probe.broker_equity, probe.broker_positions,
                 probe.cancel_entry_orders):
        with pytest.raises(requests.ConnectionError):
            call()
    assert len(seen) == 3
    assert all(t is not None and 0 < float(t) <= 30 for t in seen), seen


def test_a_caller_timeout_is_kept():
    calls = []

    class Session:
        def request(self, method, url, *args, **kwargs):
            calls.append(kwargs.get("timeout"))

    client = type("Client", (), {})()
    client._session = Session()
    runtime._bound_http_session(client, 15.0)
    runtime._bound_http_session(client, 15.0)  # idempotent
    client._session.request("GET", "/v2/account")
    client._session.request("GET", "/v2/account", timeout=3)
    assert calls == [15.0, 3]


# ---------------------------------------------------------------------------
# watchdog_main: one failure never kills the loop
# ---------------------------------------------------------------------------

class _BrokenStream:
    def write(self, _text):
        raise BrokenPipeError("stderr closed")

    def flush(self):
        raise BrokenPipeError("stderr closed")


def test_a_failing_poll_a_failing_failure_record_and_a_broken_stderr_never_kill_the_loop():
    class Failing:
        polls = 0
        records = 0

        def poll_once(self, now):
            self.polls += 1
            raise ConnectionError("Failed to resolve 'paper-api.alpaca.markets'")

        def record_failure(self, now, exc):
            self.records += 1
            raise RuntimeError("postgres unavailable")

    wd = Failing()
    exits = []
    runtime.run_loop(wd, poll_seconds=0, poll_deadline=5, sleep=lambda _s: None,
                     exit_process=exits.append, out=_BrokenStream(),
                     err=_BrokenStream(), max_polls=3)
    assert wd.polls == 3 and wd.records == 3
    assert exits == []


def test_a_wedged_poll_is_recorded_and_the_process_exits_for_a_restart():
    release = threading.Event()

    class Wedged:
        def __init__(self):
            self.failures = []

        def poll_once(self, now):
            release.wait(10)

        def record_failure(self, now, exc):
            self.failures.append(exc)

    wd = Wedged()
    exits = []
    err = io.StringIO()
    started = time.monotonic()
    try:
        runtime.run_loop(wd, poll_seconds=0, poll_deadline=0.2,
                         sleep=lambda _s: None, exit_process=exits.append,
                         err=err, max_polls=5)
    finally:
        release.set()
    assert time.monotonic() - started < 5
    assert exits == [3]
    (failure,) = wd.failures
    assert "did not finish" in str(failure)
    assert "restart" in err.getvalue()


def test_a_wedged_failure_record_still_exits():
    release = threading.Event()

    class Wedged:
        def poll_once(self, now):
            release.wait(10)

        def record_failure(self, now, exc):
            release.wait(10)

    exits = []
    started = time.monotonic()
    try:
        runtime.run_loop(Wedged(), poll_seconds=0, poll_deadline=0.2,
                         sleep=lambda _s: None, exit_process=exits.append,
                         err=io.StringIO(), max_polls=5,
                         failure_record_timeout=0.2)
    finally:
        release.set()
    assert time.monotonic() - started < 5
    assert exits == [3]


def test_main_exits_the_process_on_a_wedged_poll():
    """main() wires the real process exit, not a no-op."""
    import inspect
    source = inspect.getsource(runtime.main)
    assert "run_loop(" in source
    assert "os._exit" in inspect.getsource(runtime)


# ---------------------------------------------------------------------------
# A transient DNS failure recovers on the next successful poll
# ---------------------------------------------------------------------------

def test_an_unhealthy_dns_failure_recovers_on_the_next_successful_poll():
    class Probe:
        calls = 0

        def broker_equity(self):
            self.calls += 1
            if self.calls == 1:
                raise ConnectionError(
                    "Failed to resolve 'paper-api.alpaca.markets'")
            return 100.0

        def broker_positions(self):
            return {}

        def cancel_entry_orders(self):
            raise AssertionError("a healthy poll never cancels")

        def halt_instance(self):
            raise AssertionError("a healthy poll never halts")

    class Store:
        def get_state(self, _key):
            return type("Record", (), {"payload": {"equity": 100.0, "marks": {}}})()

    writes = []
    watchdog = AlphaWatchdog(probe=Probe(), rethink_store=Store(), thresholds={},
                             instance_id="swing-paper", reduce_executor=None,
                             health_writer=writes.append)
    runtime.run_loop(watchdog, poll_seconds=0, poll_deadline=5,
                     sleep=lambda _s: None, exit_process=lambda _c: None,
                     err=io.StringIO(), out=io.StringIO(), max_polls=2)
    assert [w.status for w in writes] == ["unhealthy", "healthy"]
    assert writes[0].result_status == "ERROR:ConnectionError"
    assert writes[1].result_status == "OK" and writes[1].degraded_audit is False


# ---------------------------------------------------------------------------
# instance.py: the supervisor restarts a dead or silent watchdog
# ---------------------------------------------------------------------------

def _instance_module():
    from unittest.mock import MagicMock
    sys.modules.setdefault("socketio", MagicMock())
    import instance
    instance.alpha_watchdog_process = None
    instance._alpha_watchdog_wanted = False
    instance._alpha_watchdog_started_at = 0.0
    instance._alpha_watchdog_last_supervised = 0.0
    instance._alpha_watchdog_last_restart = 0.0
    return instance


class _Proc:
    def __init__(self, code=None):
        self.code = code
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.code

    def terminate(self):
        self.terminated = True
        self.code = -15

    def kill(self):
        self.killed = True
        self.code = -9

    def wait(self, timeout=None):
        return self.code


def _wire(monkeypatch, inst, *, report=None, report_raises=None):
    starts = []

    def fake_start(instance_id, instance_doc=None, brokerage_doc=None):
        starts.append((instance_id, instance_doc, brokerage_doc))
        return True

    def fake_report(instance_id):
        if report_raises is not None:
            raise report_raises
        return report

    monkeypatch.setattr(inst, "_maybe_start_alpha_watchdog", fake_start)
    monkeypatch.setattr(inst, "_load_instance_and_brokerage",
                        lambda _id: ({"id": _id}, {"brokerage_type": "alpaca"}))
    monkeypatch.setattr(inst, "_alpha_watchdog_last_report", fake_report)
    return starts


NOW = 1_800_000_000.0


def test_the_supervisor_restarts_a_watchdog_that_exited(monkeypatch):
    inst = _instance_module()
    starts = _wire(monkeypatch, inst)
    inst._alpha_watchdog_wanted = True
    inst._alpha_watchdog_started_at = NOW - 3600
    inst.alpha_watchdog_process = _Proc(code=3)
    reason = inst._supervise_alpha_watchdog("swing-paper", now=NOW)
    assert reason is not None and "exited" in reason
    assert starts == [("swing-paper", {"id": "swing-paper"},
                       {"brokerage_type": "alpaca"})]


def test_the_supervisor_restarts_a_watchdog_that_is_alive_but_silent(monkeypatch):
    inst = _instance_module()
    last = datetime.fromtimestamp(NOW - 37 * 3600, timezone.utc)
    starts = _wire(monkeypatch, inst, report=last)
    inst._alpha_watchdog_wanted = True
    inst._alpha_watchdog_started_at = NOW - 40 * 3600
    inst.alpha_watchdog_process = _Proc(code=None)
    reason = inst._supervise_alpha_watchdog("swing-paper", now=NOW)
    assert reason is not None and "not reported" in reason
    assert len(starts) == 1


def test_a_watchdog_that_reports_is_left_alone(monkeypatch):
    inst = _instance_module()
    last = datetime.fromtimestamp(NOW - 20, timezone.utc)
    starts = _wire(monkeypatch, inst, report=last)
    inst._alpha_watchdog_wanted = True
    inst._alpha_watchdog_started_at = NOW - 3600
    inst.alpha_watchdog_process = _Proc(code=None)
    assert inst._supervise_alpha_watchdog("swing-paper", now=NOW) is None
    assert starts == []


def test_a_new_watchdog_gets_its_grace_before_it_counts_as_silent(monkeypatch):
    inst = _instance_module()
    last = datetime.fromtimestamp(NOW - 37 * 3600, timezone.utc)
    starts = _wire(monkeypatch, inst, report=last)
    inst._alpha_watchdog_wanted = True
    inst._alpha_watchdog_started_at = NOW - 10  # just restarted
    inst.alpha_watchdog_process = _Proc(code=None)
    assert inst._supervise_alpha_watchdog("swing-paper", now=NOW) is None
    assert starts == []


def test_an_unreadable_health_row_never_restarts_a_running_watchdog(monkeypatch):
    inst = _instance_module()
    starts = _wire(monkeypatch, inst,
                   report_raises=ConnectionError("postgres unavailable"))
    inst._alpha_watchdog_wanted = True
    inst._alpha_watchdog_started_at = NOW - 3600
    inst.alpha_watchdog_process = _Proc(code=None)
    assert inst._supervise_alpha_watchdog("swing-paper", now=NOW) is None
    assert starts == []


def test_restart_attempts_are_rate_limited(monkeypatch):
    inst = _instance_module()
    starts = []

    def failing_start(instance_id, instance_doc=None, brokerage_doc=None):
        starts.append(instance_id)
        inst.alpha_watchdog_process = None
        return False

    _wire(monkeypatch, inst)
    monkeypatch.setattr(inst, "_maybe_start_alpha_watchdog", failing_start)
    inst._alpha_watchdog_wanted = True
    inst.alpha_watchdog_process = _Proc(code=1)
    assert inst._supervise_alpha_watchdog("swing-paper", now=NOW) is not None
    assert inst._supervise_alpha_watchdog("swing-paper", now=NOW + 2) is None
    assert inst._supervise_alpha_watchdog(
        "swing-paper", now=NOW + inst.WATCHDOG_RESTART_BACKOFF_SEC + 1) is not None
    assert starts == ["swing-paper", "swing-paper"]


def test_an_instance_that_never_ran_a_watchdog_is_not_given_one(monkeypatch):
    inst = _instance_module()
    starts = _wire(monkeypatch, inst)
    inst._alpha_watchdog_wanted = False
    inst.alpha_watchdog_process = None
    assert inst._supervise_alpha_watchdog("kalshi-1", now=NOW) is None
    assert starts == []


def test_a_successful_start_marks_the_watchdog_as_wanted(monkeypatch):
    inst = _instance_module()
    monkeypatch.setenv("ALPHA_MARK_WATCHDOG_ENABLED", "1")
    monkeypatch.setattr(inst, "WATCHDOG_START_GRACE_SEC", 0.05)
    monkeypatch.setattr(inst.subprocess, "Popen", lambda *a, **k: _Proc(None))
    assert inst._maybe_start_alpha_watchdog("swing-paper", {}, {}) is True
    assert inst._alpha_watchdog_wanted is True
    assert inst._alpha_watchdog_started_at > 0


def test_a_disabled_watchdog_is_not_wanted(monkeypatch):
    inst = _instance_module()
    inst._alpha_watchdog_wanted = True
    monkeypatch.setenv("ALPHA_MARK_WATCHDOG_ENABLED", "0")
    assert inst._maybe_start_alpha_watchdog("swing-paper", {}, {}) is False
    assert inst._alpha_watchdog_wanted is False


def test_the_last_report_is_the_evidence_observed_at(monkeypatch):
    inst = _instance_module()
    rows = {"control_health:swing-paper": {
        "id": "control_health:swing-paper", "version": 7,
        "payload": {"status": "unhealthy",
                    "observed_at": "2026-10-01T05:48:52.123456+00:00"}}}

    class Store:
        def get(self, table, key):
            assert table == "AlphaState"
            return rows.get(key)

    monkeypatch.setattr(inst, "store", Store())
    assert inst._alpha_watchdog_last_report("swing-paper") == datetime(
        2026, 10, 1, 5, 48, 52, 123456, tzinfo=timezone.utc)
    assert inst._alpha_watchdog_last_report("alpaca-main") is None


def test_stop_kills_a_watchdog_that_ignores_terminate():
    inst = _instance_module()

    class Stubborn(_Proc):
        def terminate(self):
            self.terminated = True

        def wait(self, timeout=None):
            if not self.killed:
                import subprocess
                raise subprocess.TimeoutExpired("watchdog", timeout)
            return self.code

    proc = Stubborn(code=None)
    inst.alpha_watchdog_process = proc
    inst._stop_alpha_watchdog()
    assert proc.terminated and proc.killed
    assert inst.alpha_watchdog_process is None


def test_the_supervisor_loop_supervises_the_watchdog_and_never_dies_of_it():
    import ast
    src = open(os.path.join(_backend, "instance.py")).read()
    tree = ast.parse(src)
    run = next(n for n in tree.body
               if isinstance(n, ast.FunctionDef) and n.name == "run")
    loop = next(n for n in ast.walk(run) if isinstance(n, ast.While)
                and "_crash_entered" in ast.unparse(n.test))
    body = ast.unparse(loop)
    assert "_supervise_alpha_watchdog(" in body
    # Wrapped: the loop's own `except BaseException` trips crash keep-alive.
    call = next(n for n in ast.walk(loop) if isinstance(n, ast.Try)
                and "_supervise_alpha_watchdog(" in ast.unparse(n.body))
    assert any(h.type is not None and ast.unparse(h.type) == "Exception"
               for h in call.handlers)
