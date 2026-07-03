"""Round-2 Task 3: Backtest PAUSE on critical LLM failure (credit exhaustion).

Incident: backtest 586767 simulated an ENTIRE month of trading days after the
OpenRouter credits died (every LLM call HTTP 402) — no abort, no alert,
misleading blind results. Task 2 (commit 8ac807b) made 402 classify as
`insufficient_credits`, a role-INDEPENDENT fatal class that raises
LLMCriticalFailure (a BaseException subclass). Task 3: when that exception
escapes a backtest simulation day the run must PAUSE cleanly —
BacktestResults row updated {status: "paused_credits",
error: "<class_tag>: <human message>", paused_at_date: <sim date>}, partial
results preserved, ONE operator alert fired via the `alert_strategy_error`
seam, and the sim-day loop stopped (no further days). Resume = operator tops
up credits and re-queues; the resume-date query skips processed days.

WHY it lives in backtest_critical_abort.py, not broker.py: broker.py runs
argparse + the main backtest/live loop at module load and is NOT import-safe
(confirmed: `import broker` SystemExits on argparse). The established codebase
convention (see test_broker_live_boot_with_snapshot.py) is to extract the
testable logic into an import-safe helper module and test that; broker.py's
outer-except just calls the helper. The loop-wiring test below reproduces
broker.py's outer-except credit branch 1:1 for the same reason.
"""
import datetime as _dt
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.modules.setdefault("socketio", MagicMock())
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import backtest_critical_abort as bca  # noqa: E402
from llm_critical_guard import (  # noqa: E402
    LLMCriticalFailure,
    failure_is_role_independent,
)


@pytest.fixture(autouse=True)
def cage_alerts(monkeypatch):
    """AUTOUSE cage — mirrors test_llm_critical_role_scope.py::cage_alerts.

    Cages EVERY production side effect the credit-pause path (and anything it
    lazily reaches) could fire, so no real Discord/push/RethinkDB send or live
    kill switch can escape regardless of what an individual test forgets to
    mock:

      - `live_alerts.alert_strategy_error` — the seam the credit pause fires
        through (resolved via a call-time `from live_alerts import ...`, so the
        where-it's-looked-up name is patched here).
      - `live_alerts.notify` + `notifications.notify` — the deeper boundary, so
        any other alert_* helper or direct notify() caller is caged too.
      - the live kill-switch seams (`live_critical_abort._halt_live_trading`
        and `live_kill_switch.halt_live_trading`) — defensive: the backtest
        pause path must NEVER touch the live kill switch, and this proves it.

    Also cages the DB seam `_write_backtest_credit_pause` so NO real RethinkDB
    write happens even if a test forgets to patch it.

    Returns a recorder dict so tests can PROVE the stub captured the call.
    """
    import live_alerts
    import live_kill_switch
    import live_critical_abort
    import notifications

    calls = {"alert_strategy_error": [], "notify": [], "halt": [], "db_write": []}

    def _rec_alert(**kw):
        calls["alert_strategy_error"].append(kw)

    def _rec_notify(*a, **kw):
        calls["notify"].append({"args": a, "kwargs": kw})

    def _rec_halt(**kw):
        calls["halt"].append(kw)
        return {"instances_halted": 0, "orders_canceled": 0}

    def _rec_db_write(conn, rrow_id, payload):
        calls["db_write"].append({"conn": conn, "rrow_id": rrow_id, "payload": dict(payload)})

    monkeypatch.setattr(live_alerts, "alert_strategy_error", _rec_alert)
    monkeypatch.setattr(live_alerts, "notify", _rec_notify)
    monkeypatch.setattr(notifications, "notify", _rec_notify)
    monkeypatch.setattr(live_critical_abort, "_halt_live_trading", _rec_halt)
    monkeypatch.setattr(live_kill_switch, "halt_live_trading", _rec_halt)
    monkeypatch.setattr(bca, "_write_backtest_credit_pause", _rec_db_write)
    return calls


def _credit_failure(*, provider="openrouter", model="nemotron", instance_id="alpaca-main",
                    body="This request requires more credits. You can only afford 10773.",
                    attempts=4):
    return LLMCriticalFailure(
        class_tag="insufficient_credits",
        provider=provider,
        model=model,
        attribution={"instance_id": instance_id, "call_site": "overlay", "backtest_id": "586767"},
        attempts=[{"attempt": i + 1, "class_tag": "insufficient_credits",
                   "http_status": 402, "body_sample": body} for i in range(attempts)],
    )


def _auth_failure():
    return LLMCriticalFailure(
        class_tag="auth_failure", provider="openrouter", model="nemotron",
        attribution={"instance_id": "alpaca-main"},
        attempts=[{"attempt": 1, "class_tag": "auth_failure", "http_status": 401,
                   "body_sample": "invalid_api_key"}],
    )


# --- payload builder --------------------------------------------------------

def test_payload_status_error_and_paused_at_date():
    failure = _credit_failure()
    sim_date = _dt.datetime(2026, 6, 15, 14, 30, tzinfo=_dt.timezone.utc)
    payload = bca._build_credit_pause_payload(failure, sim_date)
    assert payload["status"] == "paused_credits"
    assert payload["error"].startswith("insufficient_credits: ")
    assert "requires more credits" in payload["error"]
    assert payload["paused_at_date"] == "2026-06-15"


def test_payload_preserves_partial_results_by_touching_only_three_keys():
    """update() merges — so writing ONLY status/error/paused_at_date leaves
    every existing partial-result field (pnl, trades, progress, ...) intact."""
    payload = bca._build_credit_pause_payload(_credit_failure(), "2026-06-15")
    assert set(payload.keys()) == {"status", "error", "paused_at_date"}


def test_payload_sim_date_accepts_plain_string():
    payload = bca._build_credit_pause_payload(_credit_failure(), "2026-06-15")
    assert payload["paused_at_date"] == "2026-06-15"


def test_human_message_defaults_when_no_body_sample():
    failure = LLMCriticalFailure(
        class_tag="insufficient_credits", provider="openrouter", model="nemotron",
        attribution={}, attempts=[],
    )
    msg = bca._human_message_from_failure(failure)
    assert msg  # non-empty
    assert "credits" in msg.lower()


# --- helper: DB write + single alert ---------------------------------------

def test_pause_writes_row_and_fires_single_alert(cage_alerts):
    failure = _credit_failure()
    fake_conn = object()
    payload = bca._pause_backtest_on_credit_exhaustion(586767, failure, "2026-06-15", fake_conn)

    # DB seam captured exactly one write with the pause payload.
    assert len(cage_alerts["db_write"]) == 1
    w = cage_alerts["db_write"][0]
    assert w["rrow_id"] == 586767
    assert w["conn"] is fake_conn
    assert w["payload"]["status"] == "paused_credits"
    assert w["payload"]["paused_at_date"] == "2026-06-15"

    # Exactly ONE alert through the seam, addressed to the failing instance.
    assert len(cage_alerts["alert_strategy_error"]) == 1
    a = cage_alerts["alert_strategy_error"][0]
    assert a["instance_id"] == "alpaca-main"
    assert a["tag"] == "backtest_credit_exhaustion"
    assert a["message"].startswith("insufficient_credits: ")

    # Nothing slipped past the alert seam to the wire, and the live kill switch
    # was never touched.
    assert cage_alerts["notify"] == []
    assert cage_alerts["halt"] == []

    # Returned payload matches what was written.
    assert payload["status"] == "paused_credits"


def test_pause_alert_instance_id_falls_back_to_rrow_id(cage_alerts):
    failure = LLMCriticalFailure(
        class_tag="insufficient_credits", provider="openrouter", model="m",
        attribution={}, attempts=[{"attempt": 1, "body_sample": "requires more credits"}],
    )
    bca._pause_backtest_on_credit_exhaustion(999, failure, "2026-06-15", object())
    assert cage_alerts["alert_strategy_error"][0]["instance_id"] == "999"


def test_pause_survives_db_write_failure_and_still_alerts(cage_alerts, monkeypatch):
    """A failed DB write must not swallow the operator alert (durability of the
    page matters most when the DB is unreachable)."""
    def _boom(conn, rrow_id, payload):
        raise RuntimeError("rethink down")
    monkeypatch.setattr(bca, "_write_backtest_credit_pause", _boom)
    bca._pause_backtest_on_credit_exhaustion(1, _credit_failure(), "2026-06-15", object())
    assert len(cage_alerts["alert_strategy_error"]) == 1


# --- loop wiring (mirror of broker.py's outer-except credit branch) ---------

def _run_fake_backtest_loop(days, run_once, rrow_id, conn):
    """1:1 reproduction of broker.py's per-day loop + outer-except credit
    branch (broker.py is not import-safe). Any LLMCriticalFailure escaping a
    sim day is caught; role-INDEPENDENT-fatal (credit exhaustion) routes to the
    clean-stop pause and BREAKS the loop, other classes fall through to the
    existing paused_llm_critical idle-wait path (represented as 'idle_wait')."""
    processed = []
    outcome = {"route": "completed", "payload": None}
    for d in days:
        try:
            run_once(d)
            processed.append(d)
        except LLMCriticalFailure as exc:  # BaseException subclass
            if failure_is_role_independent(exc):
                outcome["route"] = "credit_pause"
                outcome["payload"] = bca._pause_backtest_on_credit_exhaustion(
                    rrow_id, exc, d, conn
                )
            else:
                outcome["route"] = "idle_wait"
            break
    return processed, outcome


def test_loop_stops_on_first_credit_failure(cage_alerts):
    days = [f"2026-06-{d:02d}" for d in range(1, 31)]  # a full month
    calls = {"n": 0}

    def run_once(day):
        calls["n"] += 1
        raise _credit_failure()  # day 1 fails: credits dead

    processed, outcome = _run_fake_backtest_loop(days, run_once, 586767, object())

    # Loop stopped on day 1 — NOT the 30 blind days of the incident.
    assert calls["n"] == 1
    assert processed == []
    assert outcome["route"] == "credit_pause"
    assert outcome["payload"]["status"] == "paused_credits"
    assert outcome["payload"]["paused_at_date"] == "2026-06-01"
    # Exactly one row write + one alert.
    assert len(cage_alerts["db_write"]) == 1
    assert len(cage_alerts["alert_strategy_error"]) == 1


def test_loop_runs_days_until_credits_die():
    """Days before the credit death are processed; the run pauses on the
    exact day the 402s start (paused_at_date == that day)."""
    days = ["2026-06-01", "2026-06-02", "2026-06-03", "2026-06-04"]

    def run_once(day):
        if day == "2026-06-03":
            raise _credit_failure()

    with patch.object(bca, "_write_backtest_credit_pause"):
        processed, outcome = _run_fake_backtest_loop(days, run_once, 1, object())
    assert processed == ["2026-06-01", "2026-06-02"]
    assert outcome["route"] == "credit_pause"
    assert outcome["payload"]["paused_at_date"] == "2026-06-03"


def test_loop_does_not_hijack_non_credit_critical(cage_alerts):
    """A non-credit critical class (auth_failure) must NOT take the
    paused_credits clean-stop path — it stays on the existing
    paused_llm_critical idle-wait flow. Guards the scoping so Task 3 doesn't
    disturb the generic critical-pause behavior."""
    days = ["2026-06-01", "2026-06-02"]

    def run_once(day):
        raise _auth_failure()

    processed, outcome = _run_fake_backtest_loop(days, run_once, 1, object())
    assert outcome["route"] == "idle_wait"
    assert outcome["payload"] is None
    # Credit pause never ran → no row write, no alert.
    assert cage_alerts["db_write"] == []
    assert cage_alerts["alert_strategy_error"] == []


def test_failure_is_role_independent_routes_only_credit_class():
    """Sanity anchor on the real routing predicate the loop uses."""
    assert failure_is_role_independent(_credit_failure()) is True
    assert failure_is_role_independent(_auth_failure()) is False
