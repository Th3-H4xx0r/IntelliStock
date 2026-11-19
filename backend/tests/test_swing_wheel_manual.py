"""Operator-triggered wheel work: regrade one card, run a scan now
(swing_trader/wheel_manual.py), and their three API routes."""
import importlib.util
import os
import sys
import threading
import time
from datetime import date, datetime, timedelta, timezone

import pytest

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

import interactive_utils  # noqa: E402
from swing_trader import signals_store, wheel_manual, wheel_rules  # noqa: E402
from swing_trader.constants import WHEEL_DEFAULTS  # noqa: E402
from swing_trader.refdata import RUNS_TABLE, SCANS_TABLE  # noqa: E402

IID = "swing-paper"
TODAY = date(2026, 11, 20)            # a Friday
NOW = "2026-11-20T15:00:00+00:00"
CFG = {**WHEEL_DEFAULTS, "instance_id": IID}


@pytest.fixture
def db(store, monkeypatch):
    monkeypatch.setattr(signals_store, "store", store)
    monkeypatch.setattr(wheel_manual, "store", store)
    monkeypatch.setattr(interactive_utils, "store", store)
    monkeypatch.setattr(wheel_manual, "_CONTEXT", {})
    monkeypatch.setattr(wheel_manual, "_ACTIVE", {})
    sent = []
    monkeypatch.setattr(wheel_manual.notify, "send",
                        lambda *a, **k: sent.append((a, k)))
    store.sent = sent
    return store


def wheel_signal(symbol="AAPL", *, status="pending", score=62, session="2026-11-16",
                 strike=95.0, price=100.0, lane="wheel", instance_id=IID):
    doc = signals_store.new_signal(
        instance_id=instance_id, lane=lane, symbol=symbol, session=session, score=score,
        recommendation="review", reasoning="monday's view", key_risks=["old risk"],
        size_adjustment=None,
        proposal={"contract": None, "strike": strike, "expiry": "2026-11-27", "qty": 1,
                  "limit_price": None, "premium_est": 0.8, "delta": None},
        status=status, created_at="2026-11-16T15:40:00+00:00",
        context={"stock_price": price, "otm_pct": 5.0, "rsi": 55.0, "atr_pct": 3.2,
                 "earnings_days": None})
    signals_store.insert_signal(doc)
    return doc["id"]


def candidate(symbol="AAPL", price=108.0, strike=103.5):
    return {"symbol": symbol, "stock_price": price, "strike_price": strike,
            "otm_pct": 4.17, "expiry": "2026-11-27", "est_premium": 1.1,
            "est_premium_pct": 1.02, "rsi": 61.0, "sma50": 99.0, "atr": 4.4,
            "atr_pct": 4.07, "earnings_days": 30}


def scored(score=71, rec="review", contracts=2):
    return {"conviction_score": score, "recommendation": rec, "reasoning": "friday's view",
            "position_size_contracts": contracts, "key_risks": ["new risk"]}


def ctx(adapter=None):
    return {"cfg": dict(CFG), "adapter": adapter}


# -- the pure part -------------------------------------------------------------

def test_a_regrade_rewrites_score_strike_and_reasoning_and_keeps_the_old_values(db):
    sid = wheel_signal()
    doc = wheel_manual.build_regrade(signals_store.get_signal(sid), candidate=candidate(),
                                     scored=scored(), reason=None, now_iso=NOW)
    assert doc["status"] == "pending"
    assert (doc["score"], doc["recommendation"], doc["reasoning"]) == (71, "REVIEW",
                                                                      "friday's view")
    assert doc["proposal"]["strike"] == 103.5 and doc["proposal"]["qty"] == 2
    assert doc["proposal"]["premium_est"] == 1.1
    assert doc["context"]["stock_price"] == 108.0
    block = doc["regrade"]
    assert block["status"] == "done" and block["qualifies"] is True and block["rescored"]
    assert block["count"] == 1 and block["finished_at"] == NOW
    assert block["previous"] == {"score": 62, "recommendation": "REVIEW",
                                 "stock_price": 100.0, "strike": 95.0, "premium_est": 0.8,
                                 "expiry": "2026-11-27",
                                 "at": "2026-11-16T15:40:00+00:00"}


def test_a_score_under_the_review_threshold_is_flagged_and_stays_pending(db):
    sid = wheel_signal()
    doc = wheel_manual.build_regrade(signals_store.get_signal(sid), candidate=candidate(),
                                     scored=scored(41, "reject"), reason=None, now_iso=NOW)
    assert doc["status"] == "pending" and doc["score"] == 41
    assert doc["regrade"]["qualifies"] is False
    assert "below the review threshold" in doc["regrade"]["reason"]


def test_a_name_that_fails_the_screen_keeps_its_old_numbers_and_says_why(db):
    sid = wheel_signal()
    before = signals_store.get_signal(sid)
    doc = wheel_manual.build_regrade(before, candidate=None, scored=None,
                                     reason="RSI 28.0 out of [35,70]", now_iso=NOW)
    assert doc["score"] == 62 and doc["proposal"] == before["proposal"]
    assert doc["regrade"]["qualifies"] is False and doc["regrade"]["rescored"] is False
    assert doc["regrade"]["reason"] == "RSI 28.0 out of [35,70]"


def test_a_second_regrade_counts_up_and_dates_previous_from_the_first(db):
    sid = wheel_signal()
    first = wheel_manual.build_regrade(signals_store.get_signal(sid), candidate=candidate(),
                                       scored=scored(), reason=None, now_iso=NOW)
    second = wheel_manual.build_regrade(first, candidate=candidate(price=110.0),
                                        scored=scored(75, "approve"), reason=None,
                                        now_iso="2026-11-20T16:00:00+00:00")
    assert second["regrade"]["count"] == 2
    assert second["regrade"]["previous"]["score"] == 71
    assert second["regrade"]["previous"]["at"] == NOW
    assert second["status"] == "pending"          # 75+ never auto-trades here


def test_in_progress_expires_so_a_dead_regrade_can_be_asked_again():
    now = datetime(2026, 11, 20, 15, 0, tzinfo=timezone.utc)
    fresh = {"regrade": {"status": "queued", "requested_at": (now - timedelta(seconds=60)).isoformat()}}
    dead = {"regrade": {"status": "running", "requested_at": (now - timedelta(seconds=400)).isoformat()}}
    assert wheel_manual.regrade_in_progress(fresh, now) is True
    assert wheel_manual.regrade_in_progress(dead, now) is False
    assert wheel_manual.regrade_in_progress({"regrade": {"status": "done"}}, now) is False
    assert wheel_manual.regrade_in_progress({}, now) is False


# -- run_regrade ---------------------------------------------------------------

def _fresh(monkeypatch, result):
    monkeypatch.setattr(wheel_manual, "fresh_candidate",
                        lambda symbol, cfg, today, **k: result)


def test_run_regrade_writes_the_new_card_and_mirrors_the_scan_row(db, monkeypatch):
    sid = wheel_signal()
    signals_store.insert_wheel_scan({"instance_id": IID, "session": "2026-11-16",
                                     "symbol": "AAPL", "strike": 95.0, "score": 62,
                                     "status": "pending"})
    _fresh(monkeypatch, (candidate(), None))
    block = wheel_manual.run_regrade(sid, ctx(), today=TODAY, now_iso=NOW,
                                     score_fn=lambda c, **k: scored())
    assert block["status"] == "done" and block["qualifies"] is True
    row = signals_store.get_signal(sid)
    assert row["score"] == 71 and row["status"] == "pending"
    assert row["proposal"]["strike"] == 103.5
    scan = db.get(SCANS_TABLE, signals_store.signal_id_for(IID, "wheelscan",
                                                          "2026-11-16", "AAPL"))
    assert scan["strike"] == 103.5 and scan["score"] == 71


def test_a_failed_model_call_never_overwrites_the_score_with_zero(db, monkeypatch):
    sid = wheel_signal()
    _fresh(monkeypatch, (candidate(), None))
    failed = {"conviction_score": 0, "recommendation": "reject",
              "reasoning": "AI scoring failed: timeout", "key_risks": []}
    block = wheel_manual.run_regrade(sid, ctx(), today=TODAY, now_iso=NOW,
                                     score_fn=lambda c, **k: failed)
    assert block["status"] == "failed" and "timeout" in block["error"]
    row = signals_store.get_signal(sid)
    assert row["score"] == 62 and row["proposal"]["strike"] == 95.0


def test_a_decision_made_during_the_regrade_wins(db, monkeypatch):
    sid = wheel_signal()
    _fresh(monkeypatch, (candidate(), None))

    def score_then_decide(c, **k):
        signals_store.update_signal(sid, {"status": "approved"})
        return scored()

    block = wheel_manual.run_regrade(sid, ctx(), today=TODAY, now_iso=NOW,
                                     score_fn=score_then_decide)
    assert block["status"] == "failed" and "decided" in block["error"]
    row = signals_store.get_signal(sid)
    assert row["status"] == "approved" and row["score"] == 62


def test_only_a_pending_wheel_signal_is_regraded(db, monkeypatch):
    _fresh(monkeypatch, (candidate(), None))
    for sid in (wheel_signal("MSFT", status="rejected"), wheel_signal("NVDA", lane="swing")):
        block = wheel_manual.run_regrade(sid, ctx(), today=TODAY, now_iso=NOW,
                                         score_fn=lambda c, **k: scored())
        assert block["status"] == "failed"
        assert signals_store.get_signal(sid)["score"] == 62


def test_a_bars_error_is_a_recorded_failure_not_a_crash(db, monkeypatch):
    sid = wheel_signal()

    def boom(*a, **k):
        raise RuntimeError("no market data returned for AAPL")

    monkeypatch.setattr(wheel_manual, "fresh_candidate", boom)
    block = wheel_manual.run_regrade(sid, ctx(), today=TODAY, now_iso=NOW,
                                     score_fn=lambda c, **k: scored())
    assert block["status"] == "failed" and "no market data" in block["error"]


def test_no_linked_model_refuses(db):
    sid = wheel_signal()
    block = wheel_manual.run_regrade(sid, ctx(), today=TODAY, now_iso=NOW)
    assert block["status"] == "failed" and "no conviction model" in block["error"]


def test_fresh_candidate_reports_the_screens_own_reason(monkeypatch):
    def screen(raw, symbols, *, cfg=None, log=None, stats=None):
        stats["failed"] = {"AAPL": ["rsi_range"]}
        log("  [wheel] AAPL: FILTERED — RSI 28.0 out of [35,70]")
        return []

    monkeypatch.setattr(wheel_rules, "screen_technicals", screen)
    got = wheel_manual.fresh_candidate("AAPL", CFG, TODAY, bars_fn=lambda s: {"AAPL": object()},
                                       earnings_fn=lambda s: None)
    assert got == (None, "RSI 28.0 out of [35,70]")


def test_fresh_candidate_applies_the_earnings_block(monkeypatch):
    pre = {k: v for k, v in candidate().items() if k not in ("expiry", "earnings_days")}
    monkeypatch.setattr(wheel_rules, "screen_technicals", lambda *a, **k: [pre])
    monkeypatch.setattr(wheel_rules, "next_friday", lambda today, days: "2026-11-27")
    blocked = wheel_manual.fresh_candidate("AAPL", CFG, TODAY,
                                           bars_fn=lambda s: {"AAPL": object()},
                                           earnings_fn=lambda s: 3)
    assert blocked[0] is None and "earnings in 3 days" in blocked[1]
    ok, why = wheel_manual.fresh_candidate("AAPL", CFG, TODAY,
                                           bars_fn=lambda s: {"AAPL": object()},
                                           earnings_fn=lambda s: 40)
    assert why is None and ok["expiry"] == "2026-11-27" and ok["earnings_days"] == 40


# -- the manual scan -----------------------------------------------------------

def _scan_world(monkeypatch, names):
    pres = [{k: v for k, v in candidate(n).items() if k not in ("expiry", "earnings_days")}
            for n in names]
    monkeypatch.setattr(wheel_manual.universe, "get_wheel_universe", lambda: list(names))
    monkeypatch.setattr(wheel_rules, "screen_technicals", lambda *a, **k: pres)
    monkeypatch.setattr(wheel_rules, "next_friday", lambda today, days: "2026-11-27")
    monkeypatch.setattr(wheel_rules, "sector_filter", lambda c, max_per_sector: list(c))


def _run(db, score_fn, **kw):
    run = wheel_manual.new_run(IID, "pranav", NOW)
    return wheel_manual.run_manual_scan(run, ctx(), today=TODAY,
                                        bars_fn=lambda s: {n: object() for n in s},
                                        earnings_fn=lambda s: None, score_fn=score_fn,
                                        workers=1, **kw)


def test_a_manual_scan_makes_cards_and_never_an_auto_approved_row(db, monkeypatch):
    _scan_world(monkeypatch, ["AAPL", "MSFT", "NVDA"])
    answers = {"AAPL": scored(82, "approve"), "MSFT": scored(60, "review"),
               "NVDA": scored(30, "reject")}
    run = _run(db, lambda c, **k: answers[c["symbol"]])
    assert run["status"] == "done" and run["session"] == "2026-11-20"
    assert (run["scanned"], run["to_score"], run["scored"]) == (3, 3, 3)
    assert (run["new_pending"], run["rejected"]) == (2, 1)
    rows = {r["symbol"]: r for r in signals_store.all_signals(IID)}
    assert rows["AAPL"]["status"] == "pending" and rows["AAPL"]["recommendation"] == "APPROVE"
    assert rows["MSFT"]["status"] == "pending"
    assert rows["NVDA"]["status"] == "ai_rejected"
    assert all(r["source"] == "manual_scan" and r["run_id"] == run["id"]
               for r in rows.values())
    assert not any(r["status"] == "auto_approved" for r in rows.values())
    assert db.get(RUNS_TABLE, run["id"])["status"] == "done"
    assert len(db.sent) == 1                      # one summary, not one per card


def test_names_with_an_open_card_or_todays_row_are_not_scored_again(db, monkeypatch):
    wheel_signal("AAPL")                                      # Monday's pending card
    wheel_signal("MSFT", status="rejected")                   # decided: fair game
    wheel_signal("NVDA", status="ai_rejected", session="2026-11-20")   # today's row
    _scan_world(monkeypatch, ["AAPL", "MSFT", "NVDA", "KO"])
    seen = []

    def score(c, **k):
        seen.append(c["symbol"])
        return scored()

    run = _run(db, score)
    assert sorted(seen) == ["KO", "MSFT"]
    assert run["skipped_existing"] == 2 and run["new_pending"] == 2


def test_one_bad_name_does_not_stop_the_run(db, monkeypatch):
    _scan_world(monkeypatch, ["AAPL", "MSFT"])

    def score(c, **k):
        if c["symbol"] == "AAPL":
            raise RuntimeError("provider down")
        return scored()

    run = _run(db, score)
    assert run["status"] == "done" and run["errors"] == 1 and run["new_pending"] == 1


def test_a_run_with_no_market_data_is_recorded_as_failed(db, monkeypatch):
    _scan_world(monkeypatch, ["AAPL"])
    run = wheel_manual.run_manual_scan(wheel_manual.new_run(IID, "pranav", NOW), ctx(),
                                       today=TODAY, bars_fn=lambda s: {},
                                       score_fn=lambda c, **k: scored())
    assert run["status"] == "failed" and "no market data" in run["error"]
    assert db.get(RUNS_TABLE, run["id"])["status"] == "failed"


def test_run_in_progress_needs_a_live_heartbeat():
    now = datetime(2026, 11, 20, 15, 0, tzinfo=timezone.utc)
    live = {"status": "running", "heartbeat_at": (now - timedelta(seconds=30)).isoformat()}
    dead = {"status": "running", "heartbeat_at": (now - timedelta(seconds=900)).isoformat()}
    assert wheel_manual.run_in_progress(live, now) is True
    assert wheel_manual.run_in_progress(dead, now) is False
    assert wheel_manual.run_in_progress({"status": "done"}, now) is False
    assert wheel_manual.run_in_progress(None, now) is False


# -- the command entry point ---------------------------------------------------

def test_a_command_before_the_first_tick_is_refused_and_clears_the_queued_mark(db):
    sid = wheel_signal()
    signals_store.update_signal(sid, {"regrade": {"status": "queued", "requested_at": NOW}})
    ok, error, _ = wheel_manual.execute_command("swing_regrade", {"signal_id": sid},
                                                instance_id=IID)
    assert ok is False and "has not completed a tick" in error
    assert signals_store.get_signal(sid)["regrade"]["status"] == "failed"


def test_commands_hand_the_work_to_a_thread_and_return_at_once(db):
    wheel_manual.remember_context(IID, CFG, None)
    sid = wheel_signal()
    spawned = []
    spawn = lambda target, *args: spawned.append((target, args))
    ok, _, result = wheel_manual.execute_command("swing_regrade", {"signal_id": sid},
                                                 instance_id=IID, spawn=spawn)
    assert ok and result == {"signal_id": sid, "started": True}
    ok, error, _ = wheel_manual.execute_command("swing_regrade", {"signal_id": sid},
                                                instance_id=IID, spawn=spawn)
    assert ok is False and "already running" in error and len(spawned) == 1
    ok, _, result = wheel_manual.execute_command("wheel_manual_scan", {"run_id": "r1"},
                                                 instance_id=IID, spawn=spawn, today=TODAY)
    assert ok and result == {"run_id": "r1", "started": True} and len(spawned) == 2
    assert wheel_manual.execute_command("nope", {}, instance_id=IID)[0] is False


def test_the_broker_and_the_queue_know_both_commands():
    import live_state
    assert set(wheel_manual.COMMAND_TYPES) <= live_state.VALID_COMMAND_TYPES
    source = open(os.path.join(_BACKEND, "broker.py")).read()
    assert 'if ctype in ("swing_regrade", "wheel_manual_scan"):' in source
    wheel = open(os.path.join(_BACKEND, "strategies", "strategy_wheel.py")).read()
    assert "wheel_manual.remember_context(" in wheel


# -- the routes ----------------------------------------------------------------

@pytest.fixture
def api(db, monkeypatch):
    from fastapi.testclient import TestClient

    from api import main

    db.insert("Strategies", [
        {"id": 501, "name": "swing", "strategies": [
            {"strategy": "strategy_swing", "weight": 1.0,
             "config": {"strategy_swing_enabled": True}},
            {"strategy": "strategy_wheel", "weight": 1.0,
             "config": {"strategy_wheel_enabled": True}}]},
        {"id": 502, "name": "eb", "strategies": [
            {"strategy": "strategy_eb", "weight": 1.0, "config": {"strategy_eb_enabled": True}},
            {"strategy": "strategy_wheel", "weight": 1.0,
             "config": {"strategy_wheel_enabled": "false"}}]}])
    db.insert("Instances", [{"id": IID, "name": IID, "runCommand": True, "strategy_id": 501},
                            {"id": "stopped", "name": "stopped", "runCommand": False,
                             "strategy_id": 501},
                            {"id": "eb-live", "name": "eb-live", "runCommand": True,
                             "strategy_id": 502},
                            {"id": "bare", "name": "bare", "runCommand": True}])
    commands = []

    def submit(conn, instance_id, command_type, payload, submitted_by=None):
        commands.append((instance_id, command_type, dict(payload), submitted_by))
        return {"command_id": f"cmd-{len(commands)}", "status": "pending"}

    monkeypatch.setattr(interactive_utils, "action_submit_live_command", submit)
    main.app.dependency_overrides[main.conn_dependency] = lambda: None
    main.app.dependency_overrides[main.get_current_user] = lambda: {"id": "u1",
                                                                    "username": "pranav"}
    try:
        client = TestClient(main.app)
        client.commands = commands
        yield client
    finally:
        main.app.dependency_overrides.pop(main.conn_dependency, None)
        main.app.dependency_overrides.pop(main.get_current_user, None)


def regrade_url(sid, instance_id=IID):
    return f"/instances/{instance_id}/swing/signals/{sid}/regrade"


def test_regrade_marks_the_card_queued_and_queues_one_command(api):
    sid = wheel_signal()
    res = api.post(regrade_url(sid))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["command_id"] == "cmd-1"
    assert body["signal"]["regrade"]["status"] == "queued"
    assert body["signal"]["regrade"]["requested_by"] == "pranav"
    assert api.commands == [(IID, "swing_regrade", {"signal_id": sid, "instance_id": IID},
                             "pranav")]
    assert signals_store.get_signal(sid)["status"] == "pending"
    assert api.post(regrade_url(sid)).status_code == 409      # already under way


def test_regrade_refuses_what_it_cannot_do(api):
    assert api.post(regrade_url("nope")).status_code == 404
    assert api.post(regrade_url(wheel_signal("MSFT", status="rejected"))).status_code == 400
    assert api.post(regrade_url(wheel_signal("NVDA", lane="swing"))).status_code == 400
    foreign = wheel_signal("KO", instance_id="stopped")
    assert api.post(regrade_url(foreign)).status_code == 404
    assert api.post(regrade_url(foreign, "stopped")).status_code == 503
    assert api.commands == []


def test_a_regrade_that_cannot_be_queued_is_a_503_and_not_left_spinning(api, monkeypatch):
    sid = wheel_signal()

    def down(*a, **k):
        raise ValueError("queue down")

    monkeypatch.setattr(interactive_utils, "action_submit_live_command", down)
    assert api.post(regrade_url(sid)).status_code == 503
    assert signals_store.get_signal(sid)["regrade"]["status"] == "failed"


def test_the_manual_run_route_starts_one_run_and_reports_it(api):
    assert api.get(f"/instances/{IID}/wheel/scan").json() == {"run": None,
                                                              "in_progress": False}
    res = api.post(f"/instances/{IID}/wheel/scan")
    assert res.status_code == 200, res.text
    run = res.json()["run"]
    assert run["status"] == "running" and run["requested_by"] == "pranav"
    assert api.commands[0][1] == "wheel_manual_scan"
    assert api.commands[0][2]["run_id"] == run["id"]
    status = api.get(f"/instances/{IID}/wheel/scan").json()
    assert status["in_progress"] is True and status["run"]["id"] == run["id"]
    assert api.post(f"/instances/{IID}/wheel/scan").status_code == 409
    assert api.post("/instances/stopped/wheel/scan").status_code == 503
    assert len(api.commands) == 1


@pytest.mark.parametrize("method,path", [
    ("post", f"/instances/{IID}/swing/signals/x/regrade"),
    ("post", f"/instances/{IID}/wheel/scan"),
    ("get", f"/instances/{IID}/wheel/scan"),
])
def test_the_new_routes_refuse_a_caller_without_a_session(method, path):
    from fastapi.testclient import TestClient

    from api import main

    assert getattr(TestClient(main.app), method)(path).status_code in (401, 403)


# -- bug-sweep fixes (2026-11-19) ------------------------------------------------

MONDAY, TUESDAY, WEDNESDAY = date(2026, 11, 16), date(2026, 11, 17), date(2026, 11, 18)


def _running_row(db, run_id, started_at=NOW, status="running"):
    row = dict(wheel_manual.new_run(IID, "pranav", started_at, run_id), status=status)
    db.insert(RUNS_TABLE, row)
    return row


def _spy():
    spawned = []

    def spawn(target, *args):
        spawned.append((target, args))

    spawn.calls = spawned
    return spawn


# #1: a manual card dated on the weekly scan's day takes the weekly scan's
# signal id, and the weekly scan then refuses to order on it.
def test_a_manual_run_waits_for_this_weeks_weekly_scan_on_its_days(db):
    marker = {"value": None}
    wheel_manual.remember_context(IID, CFG, None, weekly_marker=lambda: marker["value"])
    for day in (MONDAY, TUESDAY):                     # scan day, then the fallback day
        _running_row(db, f"r-{day}")
        spawn = _spy()
        ok, error, _ = wheel_manual.execute_command(
            "wheel_manual_scan", {"run_id": f"r-{day}"}, instance_id=IID, spawn=spawn,
            today=day)
        assert ok is False and spawn.calls == []
        assert "has not run yet this week" in error and "Monday at 10:30 ET" in error
        row = db.get(RUNS_TABLE, f"r-{day}")
        assert row["status"] == "failed" and row["error"] == error
    # Once Monday's scan completed, Tuesday is free; any other day always is.
    marker["value"] = "2026-11-16"
    _running_row(db, "r-tue-after")
    spawn = _spy()
    ok, _, _ = wheel_manual.execute_command("wheel_manual_scan", {"run_id": "r-tue-after"},
                                            instance_id=IID, spawn=spawn, today=TUESDAY)
    assert ok and len(spawn.calls) == 1
    wheel_manual._ACTIVE.clear()
    marker["value"] = None
    _running_row(db, "r-wed")
    ok, _, _ = wheel_manual.execute_command("wheel_manual_scan", {"run_id": "r-wed"},
                                            instance_id=IID, spawn=spawn, today=WEDNESDAY)
    assert ok and len(spawn.calls) == 2


def test_a_regrade_is_not_held_for_the_weekly_scan(db):
    wheel_manual.remember_context(IID, CFG, None, weekly_marker=lambda: None)
    sid = wheel_signal()
    spawn = _spy()
    ok, _, result = wheel_manual.execute_command("swing_regrade", {"signal_id": sid},
                                                 instance_id=IID, spawn=spawn, today=MONDAY)
    assert ok and result["started"] and len(spawn.calls) == 1


def test_the_weekly_scan_day_follows_the_lanes_own_schedule(db):
    cfg = dict(CFG, scan_weekday=2, scan_time_et="11:00")       # Wednesday
    wheel_manual.remember_context(IID, cfg, None, weekly_marker=lambda: None)
    _running_row(db, "r-mon")
    ok, _, _ = wheel_manual.execute_command("wheel_manual_scan", {"run_id": "r-mon"},
                                            instance_id=IID, spawn=_spy(), today=MONDAY)
    assert ok
    wheel_manual._ACTIVE.clear()
    _running_row(db, "r-wed")
    ok, error, _ = wheel_manual.execute_command("wheel_manual_scan", {"run_id": "r-wed"},
                                                instance_id=IID, spawn=_spy(), today=WEDNESDAY)
    assert ok is False and "Wednesday at 11:00 ET" in error


def _load_strategy_wheel():
    path = os.path.join(_BACKEND, "strategies", "strategy_wheel.py")
    spec = importlib.util.spec_from_file_location("strategies.strategy_wheel", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _tick(m, config, *, adapter, cache, at=datetime(2026, 11, 21, 15, 0, tzinfo=timezone.utc)):
    return m.StrategyWheel().run_once(["SPY"], {}, at, config, {}, portfolio_emulator=adapter,
                                      strategy_cache=cache, mode="MONITOR")


def test_the_tick_hands_over_a_live_view_of_the_weekly_completion_marker(db, monkeypatch):
    m = _load_strategy_wheel()
    monkeypatch.setattr(m.clock, "is_trading_day", lambda d: False)   # stop after the hand-over
    cache = {}
    _tick(m, dict(CFG, strategy_wheel_enabled=True), adapter=object(), cache=cache)
    marker = wheel_manual.context_for(IID)["weekly_marker"]
    assert marker() is None
    cache[m._COMPLETE_KEY] = "2026-11-16"           # the weekly scan finishes later
    assert marker() == "2026-11-16"


# #2: insert_signal does not raise on a duplicate id.
def test_a_row_the_weekly_scan_wrote_mid_run_is_left_alone(db, monkeypatch):
    _scan_world(monkeypatch, ["AAPL"])
    sid = signals_store.signal_id_for(IID, "wheel", "2026-11-20", "AAPL")
    scan_id = signals_store.signal_id_for(IID, "wheelscan", "2026-11-20", "AAPL")

    def score(c, **k):
        weekly = signals_store.new_signal(
            instance_id=IID, lane="wheel", symbol="AAPL", session="2026-11-20", score=80,
            recommendation="approve", reasoning="weekly", key_risks=[], size_adjustment=None,
            proposal={"contract": "AAPL261127P00100000", "strike": 100.0, "qty": 1},
            status="auto_approved")
        signals_store.insert_signal(weekly)
        signals_store.insert_wheel_scan({"instance_id": IID, "session": "2026-11-20",
                                         "symbol": "AAPL", "strike": 100.0, "score": 80,
                                         "status": "placed"})
        return scored(64, "review")

    run = _run(db, score)
    assert (run["new_pending"], run["skipped_existing"], run["scored"]) == (0, 1, 1)
    row = signals_store.get_signal(sid)
    assert row["status"] == "auto_approved" and row["proposal"]["strike"] == 100.0
    scan = db.get(SCANS_TABLE, scan_id)
    assert scan["status"] == "placed" and scan["strike"] == 100.0 and scan["score"] == 80
    summary = db.sent[0][0][3]
    assert "New cards: 0" in summary and "AAPL $103.5" not in summary


def test_insert_signal_once_says_whether_it_wrote(db):
    doc = signals_store.new_signal(
        instance_id=IID, lane="wheel", symbol="KO", session="2026-11-20", score=60,
        recommendation="review", reasoning="r", key_risks=[], size_adjustment=None,
        proposal={}, status="pending")
    assert signals_store.insert_signal_once(dict(doc)) is True
    assert signals_store.insert_signal_once(dict(doc, score=10)) is False
    assert signals_store.get_signal(doc["id"])["score"] == 60


# #3(a): a second run request while the first is alive.
def test_a_second_run_while_one_is_alive_removes_its_own_row(db):
    wheel_manual.remember_context(IID, CFG, None, weekly_marker=lambda: None)
    _running_row(db, "r1", started_at=NOW)
    spawn = _spy()
    assert wheel_manual.execute_command("wheel_manual_scan", {"run_id": "r1"},
                                        instance_id=IID, spawn=spawn, today=TODAY)[0]
    _running_row(db, "r2", started_at="2026-11-20T15:05:00+00:00")
    ok, _, result = wheel_manual.execute_command("wheel_manual_scan", {"run_id": "r2"},
                                                 instance_id=IID, spawn=spawn, today=TODAY)
    assert result.get("already_running") is True and result.get("active_run_id") == "r1"
    assert db.get(RUNS_TABLE, "r2") is None
    assert wheel_manual.latest_run(IID)["id"] == "r1"
    # A redelivered command for the live run itself deletes nothing.
    wheel_manual.execute_command("wheel_manual_scan", {"run_id": "r1"}, instance_id=IID,
                                 spawn=spawn, today=TODAY)
    assert db.get(RUNS_TABLE, "r1")["status"] == "running"
    assert len(spawn.calls) == 1


def test_a_regrade_already_running_is_refused_on_the_card(db):
    wheel_manual.remember_context(IID, CFG, None)
    sid = wheel_signal()
    spawn = _spy()
    wheel_manual.execute_command("swing_regrade", {"signal_id": sid}, instance_id=IID,
                                 spawn=spawn)
    signals_store.update_signal(sid, {"regrade": {"status": "queued", "requested_at": NOW}})
    ok, error, _ = wheel_manual.execute_command("swing_regrade", {"signal_id": sid},
                                                instance_id=IID, spawn=spawn)
    assert ok is False and error == "a regrade of this card is already running"
    block = signals_store.get_signal(sid)["regrade"]
    assert block["status"] == "failed" and block["error"] == error
    assert len(spawn.calls) == 1


# #3(b): the earnings phase is one yfinance call per name, with no heartbeat.
def test_the_earnings_phase_keeps_the_heartbeat_alive(db, monkeypatch):
    _scan_world(monkeypatch, ["AAPL", "MSFT", "NVDA"])
    ticks = iter(range(3600))

    def clock_tick():
        n = next(ticks)
        return f"2026-11-20T15:{n // 60:02d}:{n % 60:02d}+00:00"

    monkeypatch.setattr(wheel_manual, "_now_iso", clock_tick)
    run = wheel_manual.new_run(IID, "pranav", NOW)
    seen = []

    def earnings(symbol):
        seen.append(db.get(RUNS_TABLE, run["id"])["heartbeat_at"])
        return None

    wheel_manual.run_manual_scan(run, ctx(), today=TODAY, bars_fn=lambda s: {n: object() for n in s},
                                 earnings_fn=earnings, score_fn=lambda c, **k: scored(), workers=1)
    assert len(seen) == 3 and seen == sorted(set(seen))


# #3(c): the run is released before the summary goes out.
def test_the_run_is_released_before_the_summary_is_sent(db, monkeypatch):
    _scan_world(monkeypatch, ["AAPL"])
    order = []
    monkeypatch.setattr(wheel_manual.notify, "send", lambda *a, **k: order.append("notify"))
    run = wheel_manual.new_run(IID, "pranav", NOW)
    wheel_manual.run_manual_scan(run, ctx(), today=TODAY, bars_fn=lambda s: {n: object() for n in s},
                                 earnings_fn=lambda s: None, score_fn=lambda c, **k: scored(),
                                 workers=1, release=lambda: order.append("release"))
    assert order == ["release", "notify"]


def test_a_finished_threads_release_never_frees_a_newer_run():
    wheel_manual._ACTIVE.clear()
    wheel_manual._ACTIVE["scan:x"] = "r2"
    wheel_manual._release("scan:x", "r1")
    assert wheel_manual._ACTIVE["scan:x"] == "r2"
    wheel_manual._release("scan:x", "r2")
    assert "scan:x" not in wheel_manual._ACTIVE


def test_the_command_wires_the_release_to_its_own_run(db, monkeypatch):
    wheel_manual.remember_context(IID, CFG, None, weekly_marker=lambda: None)
    _running_row(db, "r1")
    seen = {}

    def fake_scan(run, ctx, *, release=None, **k):
        seen["before"] = dict(wheel_manual._ACTIVE)
        release()
        seen["after"] = dict(wheel_manual._ACTIVE)
        return run

    monkeypatch.setattr(wheel_manual, "run_manual_scan", fake_scan)
    wheel_manual.execute_command("wheel_manual_scan", {"run_id": "r1"}, instance_id=IID,
                                 spawn=lambda target, *args: target(*args), today=TODAY)
    assert seen["before"] == {f"scan:{IID}": "r1"} and seen["after"] == {}
    assert wheel_manual._ACTIVE == {}


# Final review, Minor 1: the hold check and the run read ONE New York date, so
# a run started just before midnight on the night before scan day cannot pass
# the hold on Sunday's date and then take Monday's session.
def test_the_hold_check_and_the_run_use_the_same_ny_date(db, monkeypatch):
    wheel_manual.remember_context(IID, CFG, None, weekly_marker=lambda: None)
    _running_row(db, "r1")
    dates = iter(["2026-11-15", "2026-11-16", "2026-11-16"])     # Sun 23:59:59.999, then Mon
    calls = []

    def ny_date(now):
        calls.append(now)
        return next(dates)

    monkeypatch.setattr(wheel_manual.clock, "ny_date", ny_date)
    seen = {}

    def fake_scan(run, ctx, *, today=None, release=None, **k):
        seen["today"] = today
        release()
        return run

    monkeypatch.setattr(wheel_manual, "run_manual_scan", fake_scan)
    ok, error, _ = wheel_manual.execute_command(
        "wheel_manual_scan", {"run_id": "r1"}, instance_id=IID,
        spawn=lambda target, *args: target(*args))
    assert ok, error                                  # Sunday: not a scan day, not held
    assert seen["today"] == date(2026, 11, 15)        # and the run is Sunday's too
    assert len(calls) == 1


# #5: a row the API already gave up on is not run behind its back.
def test_a_run_row_that_is_no_longer_running_is_not_started(db):
    wheel_manual.remember_context(IID, CFG, None, weekly_marker=lambda: None)
    _running_row(db, "r1", status="failed")
    spawn = _spy()
    ok, error, _ = wheel_manual.execute_command("wheel_manual_scan", {"run_id": "r1"},
                                                instance_id=IID, spawn=spawn, today=TODAY)
    assert ok is False and "failed" in error and spawn.calls == []
    assert db.get(RUNS_TABLE, "r1")["status"] == "failed"
    assert wheel_manual._ACTIVE == {}


# #6: the API writes phase "queued"; the run must say when it starts work.
def test_the_first_save_moves_the_run_to_screening(db, monkeypatch):
    _scan_world(monkeypatch, ["AAPL"])
    run = dict(wheel_manual.new_run(IID, "pranav", NOW), phase="queued")
    db.insert(RUNS_TABLE, dict(run))
    phases = []

    def bars(names):
        phases.append(db.get(RUNS_TABLE, run["id"])["phase"])
        return {n: object() for n in names}

    wheel_manual.run_manual_scan(run, ctx(), today=TODAY, bars_fn=bars,
                                 earnings_fn=lambda s: None, score_fn=lambda c, **k: scored(),
                                 workers=1)
    assert phases == ["screening"]


# #7: "previous.at" is when the card's numbers were last really scored.
def test_previous_at_is_the_last_real_score_not_the_last_attempt(db):
    sid = wheel_signal()
    created = "2026-11-16T15:40:00+00:00"
    t2, t3, t4 = ("2026-11-21T15:00:00+00:00", "2026-11-22T15:00:00+00:00",
                  "2026-11-23T15:00:00+00:00")
    first = wheel_manual.build_regrade(signals_store.get_signal(sid), candidate=candidate(),
                                       scored=scored(), reason=None, now_iso=NOW)
    assert first["regrade"]["scored_at"] == NOW
    second = wheel_manual.build_regrade(first, candidate=None, scored=None, reason="RSI",
                                        now_iso=t2)
    assert second["regrade"]["scored_at"] == NOW and second["regrade"]["previous"]["at"] == NOW
    failed = dict(second, regrade=wheel_manual.failed_block(second, "boom", t3))
    assert failed["regrade"]["scored_at"] == NOW
    third = wheel_manual.build_regrade(failed, candidate=None, scored=None, reason="RSI",
                                       now_iso=t4)
    assert third["regrade"]["previous"]["at"] == NOW
    assert wheel_manual.request_block(third, "pranav", t4)["scored_at"] == NOW
    # A card no regrade ever re-scored dates from its creation.
    only = wheel_manual.build_regrade(signals_store.get_signal(sid), candidate=None,
                                      scored=None, reason="RSI", now_iso=NOW)
    assert only["regrade"].get("scored_at") is None
    again = wheel_manual.build_regrade(only, candidate=None, scored=None, reason="RSI",
                                       now_iso=t2)
    assert again["regrade"]["previous"]["at"] == created


# #8: a disabled lane, or no adapter, takes the remembered config away.
def test_a_disabled_lane_or_no_adapter_forgets_the_context(db):
    m = _load_strategy_wheel()
    wheel_manual.remember_context(IID, CFG, object())
    _tick(m, dict(CFG, strategy_wheel_enabled=False), adapter=object(), cache={})
    assert wheel_manual.context_for(IID) is None
    wheel_manual.remember_context(IID, CFG, object())
    _tick(m, dict(CFG, strategy_wheel_enabled=True), adapter=None, cache={})
    assert wheel_manual.context_for(IID) is None


def test_forget_context_is_safe_for_an_unknown_instance(db):
    wheel_manual.forget_context("nobody")
    wheel_manual.remember_context(IID, CFG, None)
    wheel_manual.forget_context(IID)
    assert wheel_manual.context_for(IID) is None


# #10: the regrade copies the status it read; it must be the pending it expects.
def test_a_signal_decided_and_put_back_mid_regrade_stays_pending(db, monkeypatch):
    sid = wheel_signal()
    _fresh(monkeypatch, (candidate(), None))
    real_get = signals_store.get_signal

    def get_then_revert(signal_id):
        row = real_get(signal_id)
        if row and row.get("status") == "submitted":
            # The broker's back_to_pending lands right after this read.
            signals_store.update_signal(signal_id, {"status": "pending", "claimed_at": None})
        return row

    def score_then_claim(c, **k):
        signals_store.update_signal(sid, {"status": "submitted", "claimed_at": NOW})
        return scored()

    monkeypatch.setattr(signals_store, "get_signal", get_then_revert)
    block = wheel_manual.run_regrade(sid, ctx(), today=TODAY, now_iso=NOW,
                                     score_fn=score_then_claim)
    assert block["status"] == "failed" and "decided" in block["error"]
    row = real_get(sid)
    assert row["status"] == "pending" and row["score"] == 62


# #12 (round 2): regrades have their own two scoring slots; the manual scan
# is bounded by its own pool and never takes them.
def test_regrades_are_capped_at_their_own_slots(db, monkeypatch):
    _fresh(monkeypatch, (candidate(), None))
    lock, live, peak = threading.Lock(), [0], [0]

    def slow(c, **k):
        with lock:
            live[0] += 1
            peak[0] = max(peak[0], live[0])
        time.sleep(0.05)
        with lock:
            live[0] -= 1
        return scored()

    sids = [wheel_signal(sym) for sym in ("AAPL", "MSFT", "NVDA", "KO", "PEP", "GIS")]
    threads = [threading.Thread(target=wheel_manual.run_regrade, args=(sid, ctx()),
                                kwargs={"today": TODAY, "now_iso": NOW, "score_fn": slow})
               for sid in sids]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert 1 <= peak[0] <= wheel_manual.REGRADE_WORKERS
    assert all(signals_store.get_signal(s)["regrade"]["status"] == "done" for s in sids)


def test_a_regrade_gets_a_slot_while_every_scan_worker_is_busy(db, monkeypatch):
    names = [f"S{i}" for i in range(wheel_manual.SCAN_WORKERS)]
    _scan_world(monkeypatch, names)
    _fresh(monkeypatch, (candidate("KO"), None))
    gate, lock, busy = threading.Event(), threading.Lock(), [0]
    all_busy = threading.Event()

    def stuck(c, **k):
        with lock:
            busy[0] += 1
            if busy[0] == wheel_manual.SCAN_WORKERS:
                all_busy.set()
        gate.wait(10)
        return scored()

    scan = threading.Thread(target=wheel_manual.run_manual_scan,
                            args=(wheel_manual.new_run(IID, "pranav", NOW), ctx()),
                            kwargs={"today": TODAY, "bars_fn": lambda s: {n: object() for n in s},
                                    "earnings_fn": lambda s: None, "score_fn": stuck,
                                    "workers": wheel_manual.SCAN_WORKERS})
    scan.start()
    try:
        assert all_busy.wait(5), "the scan never filled its workers"
        sid = wheel_signal("KO")
        regrade = threading.Thread(target=wheel_manual.run_regrade, args=(sid, ctx()),
                                   kwargs={"today": TODAY, "now_iso": NOW,
                                           "score_fn": lambda c, **k: scored()})
        regrade.start()
        regrade.join(3)
        assert not regrade.is_alive(), "the regrade queued behind the manual run"
        assert signals_store.get_signal(sid)["regrade"]["status"] == "done"
    finally:
        gate.set()
        scan.join(10)


def test_the_manual_scan_never_takes_a_regrade_slot(db, monkeypatch):
    _scan_world(monkeypatch, ["AAPL", "MSFT"])
    held = [wheel_manual._REGRADE_SLOTS.acquire(timeout=1)
            for _ in range(wheel_manual.REGRADE_WORKERS)]
    try:
        assert all(held)
        done = []
        t = threading.Thread(target=lambda: done.append(_run(db, lambda c, **k: scored())))
        t.start()
        t.join(5)
        assert done and done[0]["status"] == "done" and done[0]["new_pending"] == 2
    finally:
        for got in held:
            if got:
                wheel_manual._REGRADE_SLOTS.release()


# #4: an approval names the regrade it was made against.
def decide_url(sid):
    return f"/instances/{IID}/swing/signals/{sid}/decision"


def test_an_approval_of_a_card_regraded_since_it_was_opened_is_refused(api):
    sid = wheel_signal()
    signals_store.update_signal(sid, {"regrade": {"status": "done", "count": 1}})
    for decision in ("approve", "approve_half"):
        res = api.post(decide_url(sid), json={"decision": decision, "expect_regrade_count": 0})
        assert res.status_code == 412      # not 409: that one means "decided elsewhere"
        assert res.json()["detail"] == ("This card was regraded since you opened it; review "
                                        "the new terms and approve again.")
    assert signals_store.get_signal(sid)["status"] == "pending" and api.commands == []
    res = api.post(decide_url(sid), json={"decision": "approve", "expect_regrade_count": 1})
    assert res.status_code == 200, res.text
    assert signals_store.get_signal(sid)["status"] == "approved"


def test_a_card_never_regraded_matches_count_zero_and_old_clients_are_not_checked(api):
    fresh = wheel_signal("KO")
    assert api.post(decide_url(fresh), json={"decision": "approve",
                                             "expect_regrade_count": 0}).status_code == 200
    old_client = wheel_signal("MSFT")
    signals_store.update_signal(old_client, {"regrade": {"status": "done", "count": 2}})
    assert api.post(decide_url(old_client), json={"decision": "approve"}).status_code == 200


def test_a_rejection_is_never_blocked_by_the_regrade_count(api):
    sid = wheel_signal()
    signals_store.update_signal(sid, {"regrade": {"status": "done", "count": 3}})
    res = api.post(decide_url(sid), json={"decision": "reject", "expect_regrade_count": 0})
    assert res.status_code == 200 and signals_store.get_signal(sid)["status"] == "rejected"


# #9: alpaca-main's queue must not accept wheel commands.
def test_both_actions_need_an_enabled_wheel_lane(api):
    eb_signal = wheel_signal("KO", instance_id="eb-live")
    bare_signal = wheel_signal("PEP", instance_id="bare")
    for path in (regrade_url(eb_signal, "eb-live"), "/instances/eb-live/wheel/scan",
                 regrade_url(bare_signal, "bare"), "/instances/bare/wheel/scan"):
        res = api.post(path)
        assert res.status_code == 400, (path, res.text)
        assert res.json()["detail"] == "this instance has no enabled wheel lane"
    assert api.commands == []
    assert wheel_manual.latest_run("eb-live") is None
    assert signals_store.get_signal(eb_signal).get("regrade") is None


def test_the_lane_check_reads_conditions_under_config_like_the_broker(db):
    db.insert("Strategies", [
        {"id": 601, "strategies": [{"strategy": "StrategyWheel", "weight": 1.0,
                                    "conditions": {"strategy_wheel_enabled": True}}]},
        {"id": 602, "strategies": [{"strategy": "strategy_wheel", "weight": 1.0,
                                    "conditions": {"strategy_wheel_enabled": True},
                                    "config": {"strategy_wheel_enabled": False}}]},
        {"id": 603, "strategies": [{"strategy": "strategy_wheel",
                                    "config": {"strategy_wheel_enabled": True}}]}])
    lane_on = interactive_utils._swing_lane_enabled
    assert lane_on({"strategy_id": 601}, "strategy_wheel") is True
    assert lane_on({"strategy_id": 602}, "strategy_wheel") is False
    assert lane_on({"strategy_id": 603}, "strategy_wheel") is False   # no weight: not loaded
    assert lane_on({"strategy_id": 999}, "strategy_wheel") is False
    assert lane_on({}, "strategy_wheel") is False
