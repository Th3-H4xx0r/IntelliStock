"""Plan B Task 21: the swing and wheel routes, over real HTTP (TestClient),
with the shapes interfaces §9 pins for plan C's UIs."""
import os
import sys
from datetime import date, datetime, timedelta, timezone

import pytest

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

import interactive_utils  # noqa: E402
import live_broker_fetch  # noqa: E402
from swing_trader import market_data, signals_store  # noqa: E402

IID = "swing-paper"


@pytest.fixture
def api(store, monkeypatch):
    from fastapi.testclient import TestClient

    from api import main

    monkeypatch.setattr(interactive_utils, "store", store)
    monkeypatch.setattr(signals_store, "store", store)
    store.insert("Instances", [{"id": IID, "name": IID, "runCommand": True},
                               {"id": "stopped", "name": "stopped", "runCommand": False},
                               {"id": "other", "name": "other", "runCommand": True},
                               {"id": "crashed", "name": "crashed", "runCommand": True,
                                "crashed": True}])
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


def signal(symbol="AAPL", *, instance_id=IID, status="pending", lane="swing",
           created_at="2026-09-24T13:20:00+00:00"):
    doc = signals_store.new_signal(
        instance_id=instance_id, lane=lane, symbol=symbol, session="2026-09-24", score=62,
        recommendation="review", reasoning="r", key_risks=["x"], size_adjustment=0.5,
        proposal={"entry": 98.0, "stop": 92.12, "target": 106.82, "shares": 76},
        status=status, created_at=created_at)
    signals_store.insert_signal(doc)
    return doc["id"]


def decision_url(sid, instance_id=IID):
    return f"/instances/{instance_id}/swing/signals/{sid}/decision"


# -- GET signals ---------------------------------------------------------------

def test_signals_are_wrapped_filtered_and_newest_first(api):
    signal("AAA", created_at="2026-09-24T13:20:00+00:00")
    signal("BBB", created_at="2026-09-24T13:40:00+00:00")
    signal("CCC", status="ai_rejected")
    signal("DDD", instance_id="other")
    res = api.get(f"/instances/{IID}/swing/signals", params={"status": "pending"})
    assert res.status_code == 200
    body = res.json()
    assert list(body) == ["signals"]
    assert [s["symbol"] for s in body["signals"]] == ["BBB", "AAA"]
    assert set(body["signals"][0]) >= {"id", "instance_id", "lane", "symbol", "session",
                                       "created_at", "score", "recommendation", "reasoning",
                                       "key_risks", "size_adjustment", "proposal", "status",
                                       "decided_by", "decided_at", "decision_reason",
                                       "order_client_id", "outcome"}
    everything = api.get(f"/instances/{IID}/swing/signals").json()["signals"]
    assert {s["symbol"] for s in everything} == {"AAA", "BBB", "CCC"}
    assert api.get("/instances/nope/swing/signals").status_code == 404


# -- POST decision ---------------------------------------------------------------

def test_an_approval_is_final_and_queues_one_broker_command(api):
    sid = signal()
    res = api.post(decision_url(sid), json={"decision": "approve", "reason": "looks fine"})
    assert res.status_code == 200
    body = res.json()
    assert body["command_id"] == "cmd-1"
    assert (body["signal"]["status"], body["signal"]["decided_by"],
            body["signal"]["decision_reason"]) == ("approved", "pranav", "looks fine")
    assert api.commands == [(IID, "submit_order",
                             {"source": "swing_approval", "signal_id": sid}, "pranav")]
    assert signals_store.get_signal(sid)["status"] == "approved"


@pytest.mark.parametrize("body", [{"decision": "approve_half"},
                                  {"decision": "approve_half", "reason": None}])
def test_reason_may_be_absent_or_null(api, body):
    sid = signal()
    res = api.post(decision_url(sid), json=body)
    assert res.status_code == 200
    assert res.json()["signal"]["status"] == "approved_half"
    assert res.json()["signal"]["decision_reason"] is None


def test_a_second_decision_is_a_400_and_enqueues_nothing(api):
    sid = signal()
    assert api.post(decision_url(sid), json={"decision": "approve"}).status_code == 200
    again = api.post(decision_url(sid), json={"decision": "approve"})
    assert again.status_code == 400 and "not pending" in again.json()["detail"]
    late_reject = api.post(decision_url(sid), json={"decision": "reject"})
    assert late_reject.status_code == 400
    assert len(api.commands) == 1
    submitted = signal("SUB", status="submitted")
    assert api.post(decision_url(submitted), json={"decision": "approve"}).status_code == 400


def test_a_reject_queues_nothing_and_works_on_a_stopped_instance(api):
    sid = signal(instance_id="stopped")
    res = api.post(decision_url(sid, "stopped"), json={"decision": "reject"})
    assert res.status_code == 200 and res.json()["command_id"] is None
    assert signals_store.get_signal(sid)["status"] == "rejected"
    assert api.commands == []


def test_an_approval_on_a_stopped_instance_is_a_503_and_stays_pending(api):
    sid = signal(instance_id="stopped")
    res = api.post(decision_url(sid, "stopped"), json={"decision": "approve"})
    assert res.status_code == 503 and "not running" in res.json()["detail"]
    assert signals_store.get_signal(sid)["status"] == "pending"
    assert api.commands == []


def test_a_command_that_cannot_be_queued_is_a_503_and_puts_the_signal_back(api, monkeypatch):
    def down(*a, **k):
        raise ValueError("submit_command failed: database unavailable")

    monkeypatch.setattr(interactive_utils, "action_submit_live_command", down)
    sid = signal()
    res = api.post(decision_url(sid), json={"decision": "approve"})
    detail = res.json()["detail"]
    assert res.status_code == 503 and "pending again" in detail
    # FW-api-I1: the 503 is kept for the one case that is provably safe to retry.
    assert "not queued" in detail and "try again" in detail
    assert "manually" not in detail
    assert signals_store.get_signal(sid)["status"] == "pending"


def test_a_click_that_loses_the_race_is_a_409_and_queues_nothing(api, monkeypatch):
    sid = signal()
    # Another click decided between this one's read and its compare-and-swap.
    monkeypatch.setattr(signals_store, "cas_signal", lambda *a, **k: False)
    res = api.post(decision_url(sid), json={"decision": "approve"})
    assert res.status_code == 409 and "another click" in res.json()["detail"]
    assert api.commands == []


def test_unknown_and_foreign_signals_are_404(api):
    assert api.post(decision_url("nope"), json={"decision": "approve"}).status_code == 404
    foreign = signal(instance_id="other")
    assert api.post(decision_url(foreign), json={"decision": "approve"}).status_code == 404
    assert signals_store.get_signal(foreign)["status"] == "pending"
    assert api.post(decision_url(foreign, "nope"), json={"decision": "approve"}).status_code == 404


@pytest.mark.parametrize("body", [{}, {"decision": "maybe"}, {"reason": "x"},
                                  {"decision": 5}, {"decision": "approve", "reason": 3}])
def test_a_malformed_body_is_422(api, body):
    sid = signal()
    assert api.post(decision_url(sid), json=body).status_code == 422
    assert signals_store.get_signal(sid)["status"] == "pending"


# -- GET wheel ---------------------------------------------------------------------

def occ(u, strike, expiry, kind="P"):
    return f"{u}{expiry[2:4]}{expiry[5:7]}{expiry[8:10]}{kind}{int(strike * 1000):08d}"


def option_row(u, strike, expiry, qty, *, entry=1.23, last=0.85, pnl=38.0, meta=True, kind="P"):
    row = {"symbol": occ(u, strike, expiry, kind), "qty": float(qty), "avg_entry_price": entry,
           "last_price": last, "market_value": None if last is None else last * 100 * qty,
           "unrealized_pnl": pnl, "unrealized_pnl_pct": None, "asset_class": "us_option",
           "side": "short" if qty < 0 else "long", "multiplier": 100,
           "underlying": None, "strike": None, "expiry": None}
    if meta:                         # A-live Task 14 fills these from Alpaca's contract
        row.update(underlying=u, strike=strike, expiry=expiry)
    return row


BOOK = {"cash": 25_000.0, "equity": 60_000.0, "broker_fetch_error": None, "positions": [
    option_row("APH", 130.0, "2026-10-02", -1),
    option_row("KO", 60.0, "2026-10-02", -2, last=None, pnl=None, meta=False),
    option_row("GIS", 55.0, "2026-10-09", -1),
    option_row("MSFT", 450.0, "2026-10-02", -1, kind="C"),
    {"symbol": "AAPL", "qty": 10.0, "avg_entry_price": 200.0, "last_price": 210.0,
     "market_value": 2100.0, "unrealized_pnl": 100.0, "unrealized_pnl_pct": 5.0}]}


def test_the_wheel_book_has_exactly_the_pinned_shape(api, monkeypatch):
    monkeypatch.setattr(live_broker_fetch, "fetch_broker_live_state", lambda conn, iid: BOOK)
    monkeypatch.setattr(interactive_utils, "_wheel_underlying_prices",
                        lambda iid, symbols: {"APH": 127.4, "KO": 63.0})
    base = datetime(2026, 9, 1, tzinfo=timezone.utc)
    for i in range(25):
        signals_store.insert_wheel_scan({
            "instance_id": IID, "session": "2026-09-21", "symbol": f"S{i:02d}",
            "created_at": (base + timedelta(minutes=i)).isoformat(), "stock_price": 50.0,
            "strike": 48.0, "expiry": "2026-10-02", "premium_est": 0.5, "score": 70,
            "recommendation": "REVIEW", "reasoning": "r", "status": "pending",
            "skip_reason": None})
    monkeypatch.setattr(interactive_utils, "_ny_today", lambda: date(2026, 9, 24))
    res = api.get(f"/instances/{IID}/wheel")
    assert res.status_code == 200
    body = res.json()
    assert list(body) == ["open_puts", "collateral_total", "cash", "recent_scans"]
    aph, ko, gis = body["open_puts"]
    assert aph == {"contract": "APH261002P00130000", "underlying": "APH", "strike": 130.0,
                   "expiry": "2026-10-02", "qty": 1, "avg_entry_price": 1.23,
                   "current_price": 0.85, "underlying_price": 127.4, "itm_pct": 2.0,
                   "dte": 8, "collateral": 13000.0, "unrealized_pl": 38.0}
    # No contract fields from Alpaca: the OCC symbol names them. Out of the money: negative.
    assert (ko["underlying"], ko["strike"], ko["expiry"], ko["qty"]) == ("KO", 60.0,
                                                                        "2026-10-02", 2)
    assert ko["itm_pct"] == -5.0 and ko["collateral"] == 12000.0
    assert ko["current_price"] is None and ko["unrealized_pl"] is None
    # No underlying quote: null, never 0.0.
    assert gis["underlying_price"] is None and gis["itm_pct"] is None and gis["dte"] == 15
    assert body["collateral_total"] == 30500.0 and body["cash"] == 25000.0
    scans = body["recent_scans"]
    assert len(scans) == 20 and scans[0]["symbol"] == "S24" and scans[-1]["symbol"] == "S05"


def test_an_unreadable_broker_is_503_not_an_empty_book(api, monkeypatch):
    monkeypatch.setattr(live_broker_fetch, "fetch_broker_live_state",
                        lambda conn, iid: {"broker_fetch_error": "broker_api_error: timeout"})
    res = api.get(f"/instances/{IID}/wheel")
    assert res.status_code == 503 and "timeout" in res.json()["detail"]
    assert api.get("/instances/nope/wheel").status_code == 404


def test_underlying_prices_use_the_instance_credentials(monkeypatch):
    seen = {}
    monkeypatch.setattr(live_broker_fetch, "_load_credentials",
                        lambda iid: {"error": None, "key": "k", "secret": "s"})
    monkeypatch.setattr(market_data, "data_client",
                        lambda k, s: seen.setdefault("creds", (k, s)) and "client")
    monkeypatch.setattr(market_data, "live_prices",
                        lambda syms, client=None, **kw: {s: 1.0 for s in syms})
    assert interactive_utils._wheel_underlying_prices(IID, ["APH"]) == {"APH": 1.0}
    assert seen["creds"] == ("k", "s")
    monkeypatch.setattr(live_broker_fetch, "_load_credentials",
                        lambda iid: {"error": "instance_not_found"})
    assert interactive_utils._wheel_underlying_prices(IID, ["APH"]) == {}


# -- GET calibration ---------------------------------------------------------------

def test_calibration_reports_this_instance(api):
    res = api.get(f"/instances/{IID}/swing/calibration")
    assert res.status_code == 200
    assert set(res.json()) == {"swing", "wheel", "gate"}
    assert res.json()["gate"]["met"] is False


# -- G8a ruling 1: every route needs a session; the approval really enqueues --

_REAL_SUBMIT = interactive_utils.action_submit_live_command
SWING_ROUTES = [("GET", f"/instances/{IID}/swing/signals"),
                ("POST", f"/instances/{IID}/swing/signals/sid/decision"),
                ("GET", f"/instances/{IID}/wheel"),
                ("GET", f"/instances/{IID}/swing/calibration")]


@pytest.mark.parametrize("method,path", SWING_ROUTES)
def test_every_swing_route_refuses_a_caller_without_a_session(method, path):
    from fastapi.testclient import TestClient

    from api import main

    main.app.dependency_overrides[main.conn_dependency] = lambda: None
    try:
        res = TestClient(main.app).request(method, path, json={"decision": "approve"},
                                           headers={"Authorization": "Bearer not-a-token"})
    finally:
        main.app.dependency_overrides.pop(main.conn_dependency, None)
    assert res.status_code == 401


@pytest.mark.parametrize("method,path", [
    ("GET", "/instances/{instance_id}/swing/signals"),
    ("POST", "/instances/{instance_id}/swing/signals/{signal_id}/decision"),
    ("GET", "/instances/{instance_id}/wheel"),
    ("GET", "/instances/{instance_id}/swing/calibration")])
def test_every_swing_route_is_wired_to_the_session_dependency(method, path):
    import inspect

    from api import main

    (endpoint,) = [r.endpoint for r in main.app.routes
                   if getattr(r, "path", None) == path and method in r.methods]
    param = inspect.signature(endpoint).parameters["current_user"]
    assert param.default.dependency is main.get_current_user


def test_an_approval_enqueues_one_submit_order_live_command(api, store, monkeypatch):
    import live_state

    monkeypatch.setattr(interactive_utils, "action_submit_live_command", _REAL_SUBMIT)
    monkeypatch.setattr(live_state, "store", store)
    monkeypatch.setattr(live_state, "ensure_tables", lambda r=None, conn=None: None)
    sid = signal()
    res = api.post(decision_url(sid), json={"decision": "approve"})
    assert res.status_code == 200
    (cmd,) = store.get_all(live_state.LIVE_COMMANDS_TABLE, res.json()["command_id"])
    assert (cmd["instance_id"], cmd["type"], cmd["payload"], cmd["status"],
            cmd["submitted_by"]) == (IID, "submit_order",
                                     {"source": "swing_approval", "signal_id": sid},
                                     "pending", "pranav")


def test_a_malformed_broker_row_never_turns_the_book_into_a_400(api, monkeypatch):
    # A strike or expiry the broker could not fill in reads from the OCC symbol.
    row = option_row("APH", 130.0, "2026-10-02", -1)
    row.update(strike="n/a", expiry="soon", underlying="")
    monkeypatch.setattr(live_broker_fetch, "fetch_broker_live_state",
                        lambda conn, iid: {"cash": 1.0, "broker_fetch_error": None,
                                           "positions": [row]})
    monkeypatch.setattr(interactive_utils, "_wheel_underlying_prices", lambda iid, s: {})
    monkeypatch.setattr(interactive_utils, "_ny_today", lambda: date(2026, 9, 24))
    res = api.get(f"/instances/{IID}/wheel")
    assert res.status_code == 200
    (put,) = res.json()["open_puts"]
    assert (put["underlying"], put["strike"], put["expiry"], put["dte"]) == (
        "APH", 130.0, "2026-10-02", 8)


# -- G8a fix round 1 ---------------------------------------------------------------

def _queue_down(monkeypatch):
    def down(*a, **k):
        raise ValueError("submit_command failed: database unavailable")

    monkeypatch.setattr(interactive_utils, "action_submit_live_command", down)


def _cas_second_call(monkeypatch, behaviour):
    """The first compare-and-swap (the decision) is real; the second (the
    revert after a failed enqueue) raises or returns False."""
    real, calls = signals_store.cas_signal, {"n": 0}

    def cas(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            return real(*a, **k)
        if isinstance(behaviour, Exception):
            raise behaviour
        return behaviour

    monkeypatch.setattr(signals_store, "cas_signal", cas)


def _assert_uncertain(res):
    """FW-api-I1: the accepted-but-uncertain answer. Never an instruction to
    place the order by hand, never a claim that nothing was queued."""
    assert res.status_code == 202
    body = res.json()
    assert body["uncertain"] is True
    detail = body["detail"]
    assert "approval received" in detail and "may be in flight" in detail
    assert "check the signal status and open orders" in detail
    assert "no broker command was queued" not in detail
    assert "place the order manually" not in detail and "pending again" not in detail
    return body


def test_important_2_a_revert_that_raises_is_uncertain_never_nothing_queued(
        api, monkeypatch):
    # G8a Important 2 said "no broker command was queued" here. FW-api-I1: a
    # revert that raised proves nothing (it may even have landed), so the
    # operator is told the order may be in flight, not to place it by hand.
    _queue_down(monkeypatch)
    _cas_second_call(monkeypatch, RuntimeError("db down"))
    lines = []
    monkeypatch.setattr(interactive_utils, "_swing_log",
                        lambda msg, color="white": lines.append((msg, color)))
    sid = signal()
    body = _assert_uncertain(api.post(decision_url(sid), json={"decision": "approve"}))
    assert body["signal"]["status"] == "approved" and body["command_id"] is None
    assert signals_store.get_signal(sid)["status"] == "approved" and api.commands == []
    assert any(color == "red" and sid in msg for msg, color in lines)


def test_important_2_a_revert_that_finds_the_row_moved_is_uncertain(api, monkeypatch):
    _queue_down(monkeypatch)
    _cas_second_call(monkeypatch, False)
    sid = signal()
    _assert_uncertain(api.post(decision_url(sid), json={"decision": "approve"}))


# -- FW-api-I1: a failed queue write is re-read before anything is said ------------

def _real_queue(monkeypatch, store):
    import live_state

    monkeypatch.setattr(live_state, "store", store)
    monkeypatch.setattr(live_state, "ensure_tables", lambda r=None, conn=None: None)
    return live_state


def _landed_then_raised(monkeypatch, store, *, claim=False):
    """The real producer: live_state.submit_command writes the LiveCommands row
    (the INSERT committed), then the acknowledgement is lost and the caller
    sees the wrapped error action_submit_live_command raises. With claim, the
    broker's command worker claims the signal (approved -> submitted) first."""
    _real_queue(monkeypatch, store)
    landed = []

    def submit(conn, instance_id, command_type, payload, submitted_by=None):
        landed.append(_REAL_SUBMIT(conn, instance_id, command_type, payload, submitted_by))
        if claim:
            row = dict(signals_store.get_signal(payload["signal_id"]))
            assert signals_store.cas_signal(row["id"], expect_status=row["status"],
                                            doc={**row, "status": "submitted"})
        raise ValueError("submit_command failed: server closed the connection unexpectedly")

    monkeypatch.setattr(interactive_utils, "action_submit_live_command", submit)
    return landed


def test_i1_a_landed_command_the_broker_claimed_is_never_place_it_manually(
        api, store, monkeypatch):
    # The final review's probe (test_row_moved_branch_denies_a_queued_command).
    landed = _landed_then_raised(monkeypatch, store, claim=True)
    sid = signal()
    body = _assert_uncertain(api.post(decision_url(sid), json={"decision": "approve"}))
    (cmd,) = landed
    assert body["command_id"] == cmd["command_id"]
    assert body["signal"]["status"] == "submitted"
    assert signals_store.get_signal(sid)["status"] == "submitted"


def test_i1_a_landed_command_not_yet_claimed_is_uncertain_and_left_approved(
        api, store, monkeypatch):
    landed = _landed_then_raised(monkeypatch, store)
    sid = signal()
    body = _assert_uncertain(api.post(decision_url(sid), json={"decision": "approve"}))
    assert body["command_id"] == landed[0]["command_id"]
    # Not reverted: the queued command still runs, and the broker's claim needs
    # the row to read approved.
    assert signals_store.get_signal(sid)["status"] == "approved"


def test_i1_a_signal_the_broker_already_moved_is_uncertain_without_a_command_row(
        api, monkeypatch):
    # No command row is visible, but the signal no longer reads approved. Only
    # the broker's claim moves it off approved, so a command was queued.
    def raised_after_the_claim(conn, instance_id, command_type, payload, submitted_by=None):
        row = dict(signals_store.get_signal(payload["signal_id"]))
        signals_store.cas_signal(row["id"], expect_status=row["status"],
                                 doc={**row, "status": "pending", "decided_by": None})
        raise ValueError("submit_command failed: connection reset")

    monkeypatch.setattr(interactive_utils, "action_submit_live_command",
                        raised_after_the_claim)
    sid = signal()
    body = _assert_uncertain(api.post(decision_url(sid), json={"decision": "approve"}))
    assert body["signal"]["status"] == "pending" and body["command_id"] is None


def test_i1_an_unreadable_queue_is_uncertain_and_nothing_is_reverted(api, monkeypatch):
    _queue_down(monkeypatch)

    def unreadable(*a, **k):
        raise RuntimeError("postgres unavailable")

    monkeypatch.setattr(interactive_utils, "_swing_approval_commands", unreadable)
    reverts = []
    real_cas = signals_store.cas_signal
    monkeypatch.setattr(signals_store, "cas_signal",
                        lambda *a, **k: reverts.append(k) or real_cas(*a, **k))
    sid = signal()
    _assert_uncertain(api.post(decision_url(sid), json={"decision": "approve"}))
    assert len(reverts) == 1                  # the decision itself; no revert
    assert signals_store.get_signal(sid)["status"] == "approved"


def test_i1_an_older_command_for_the_same_signal_does_not_count_as_queued(
        api, store, monkeypatch):
    # An earlier approval of this signal queued a command, which the broker
    # reset to pending (a transient refusal). This approval's own write failed
    # and nothing new landed: provably not queued, so revert and 503.
    live_state = _real_queue(monkeypatch, store)
    sid = signal()
    old_id = live_state.submit_command(None, None, instance_id=IID, type="submit_order",
                                       payload={"source": "swing_approval",
                                                "signal_id": sid})
    store.update(live_state.LIVE_COMMANDS_TABLE, old_id,
                 {"status": "completed", "created_at": "2026-09-24T13:21:00+00:00",
                  "created_at_iso": "2026-09-24T13:21:00+00:00"})
    _queue_down(monkeypatch)
    res = api.post(decision_url(sid), json={"decision": "approve"})
    assert res.status_code == 503 and "not queued" in res.json()["detail"]
    assert signals_store.get_signal(sid)["status"] == "pending"


def test_i1_a_command_for_another_signal_does_not_count_as_queued(api, store, monkeypatch):
    live_state = _real_queue(monkeypatch, store)
    sid, other = signal("AAA"), signal("BBB")
    live_state.submit_command(None, None, instance_id=IID, type="submit_order",
                              payload={"source": "swing_approval", "signal_id": other})
    _queue_down(monkeypatch)
    res = api.post(decision_url(sid), json={"decision": "approve"})
    assert res.status_code == 503 and signals_store.get_signal(sid)["status"] == "pending"


def test_i1_the_route_answers_202_with_a_json_body(api, store, monkeypatch):
    _landed_then_raised(monkeypatch, store)
    res = api.post(decision_url(signal()), json={"decision": "approve_half"})
    assert res.status_code == 202
    assert res.headers["content-type"].startswith("application/json")
    assert set(res.json()) == {"signal", "command_id", "uncertain", "detail"}
    assert res.json()["signal"]["status"] == "approved_half"


def test_m6_an_approval_on_a_crashed_instance_is_a_503_and_stays_pending(api):
    sid = signal(instance_id="crashed")
    res = api.post(decision_url(sid, "crashed"), json={"decision": "approve"})
    assert res.status_code == 503 and "crashed" in res.json()["detail"]
    assert signals_store.get_signal(sid)["status"] == "pending" and api.commands == []


def _book_with(monkeypatch, *rows):
    monkeypatch.setattr(live_broker_fetch, "fetch_broker_live_state",
                        lambda conn, iid: {"cash": 1.0, "broker_fetch_error": None,
                                           "positions": list(rows)})
    monkeypatch.setattr(interactive_utils, "_wheel_underlying_prices",
                        lambda iid, s: {"APH": 120.0, "KO": 50.0})
    monkeypatch.setattr(interactive_utils, "_ny_today", lambda: date(2026, 9, 24))


@pytest.mark.parametrize("value", ["nan", float("nan"), float("-inf"), "inf"])
def test_m3_a_non_finite_qty_skips_that_row_only(api, monkeypatch, value):
    bad = option_row("APH", 130.0, "2026-10-02", -1)
    bad["qty"] = value
    _book_with(monkeypatch, bad, option_row("KO", 60.0, "2026-10-02", -1))
    res = api.get(f"/instances/{IID}/wheel")
    assert res.status_code == 200
    assert [p["underlying"] for p in res.json()["open_puts"]] == ["KO"]


@pytest.mark.parametrize("value", ["nan", float("nan"), float("inf"), "inf"])
def test_m3_a_non_finite_strike_reads_from_the_occ_symbol(api, monkeypatch, value):
    bad = option_row("APH", 130.0, "2026-10-02", -1)
    bad["strike"] = value
    _book_with(monkeypatch, bad)
    res = api.get(f"/instances/{IID}/wheel")
    assert res.status_code == 200
    (aph,) = res.json()["open_puts"]
    assert (aph["strike"], aph["collateral"], aph["itm_pct"]) == (130.0, 13000.0, 7.69)


def test_m3_non_finite_prices_are_null_never_non_finite_json(api, monkeypatch):
    row = option_row("APH", 130.0, "2026-10-02", -1, entry=float("nan"), last=float("inf"),
                     pnl=float("-inf"))
    _book_with(monkeypatch, row)
    monkeypatch.setattr(interactive_utils, "_wheel_underlying_prices",
                        lambda iid, s: {"APH": float("nan")})
    res = api.get(f"/instances/{IID}/wheel")
    assert res.status_code == 200
    (aph,) = res.json()["open_puts"]
    assert (aph["avg_entry_price"], aph["current_price"], aph["unrealized_pl"],
            aph["underlying_price"], aph["itm_pct"]) == (None, None, None, None, None)


@pytest.mark.parametrize("multiplier,collateral", [(10, 1300.0), (None, 13000.0),
                                                   (0, 13000.0), ("nan", 13000.0)])
def test_m3_collateral_uses_the_row_multiplier(api, monkeypatch, multiplier, collateral):
    row = option_row("APH", 130.0, "2026-10-02", -1)
    row["multiplier"] = multiplier
    _book_with(monkeypatch, row)
    res = api.get(f"/instances/{IID}/wheel")
    (aph,) = res.json()["open_puts"]
    assert aph["collateral"] == collateral and res.json()["collateral_total"] == collateral


# -- FW item 3: re-send a stuck approval -------------------------------------------

def resend_url(sid, instance_id=IID):
    return f"/instances/{instance_id}/swing/signals/{sid}/resend"


def _queued(live_state, store, sid, status="pending", instance_id=IID):
    cid = live_state.submit_command(None, None, instance_id=instance_id, type="submit_order",
                                    payload={"source": "swing_approval", "signal_id": sid})
    if status != "pending":
        store.update(live_state.LIVE_COMMANDS_TABLE, cid, {"status": status})
    return cid


@pytest.mark.parametrize("status", ["approved", "approved_half"])
def test_resend_queues_the_same_approval_payload_again(api, status):
    sid = signal(status=status)
    res = api.post(resend_url(sid))
    assert res.status_code == 200
    assert res.json()["command_id"] == "cmd-1"
    assert res.json()["signal"]["status"] == status
    assert api.commands == [(IID, "submit_order",
                             {"source": "swing_approval", "signal_id": sid}, "pranav")]
    # The re-send changes nothing on the row: the broker's claim needs it approved.
    assert signals_store.get_signal(sid)["status"] == status


@pytest.mark.parametrize("status", ["pending", "submitted", "rejected", "failed",
                                    "ai_rejected", "auto_approved"])
def test_resend_of_a_signal_that_is_not_approved_is_409(api, status):
    sid = signal(status=status)
    res = api.post(resend_url(sid))
    assert res.status_code == 409 and "not approved" in res.json()["detail"]
    assert api.commands == []


@pytest.mark.parametrize("status", ["pending", "running"])
def test_resend_while_a_command_is_open_is_409(api, store, monkeypatch, status):
    live_state = _real_queue(monkeypatch, store)
    sid = signal(status="approved")
    cid = _queued(live_state, store, sid, status)
    res = api.post(resend_url(sid))
    assert res.status_code == 409
    assert cid in res.json()["detail"] and status in res.json()["detail"]
    assert api.commands == []


@pytest.mark.parametrize("status", ["completed", "failed", "reconciliation_required"])
def test_a_finished_command_does_not_block_a_resend(api, store, monkeypatch, status):
    live_state = _real_queue(monkeypatch, store)
    sid = signal(status="approved")
    _queued(live_state, store, sid, status)
    # Open commands for another signal or another instance do not block either.
    _queued(live_state, store, signal("OTHER", status="approved"))
    _queued(live_state, store, sid, instance_id="other")
    assert api.post(resend_url(sid)).status_code == 200
    assert len(api.commands) == 1


def test_a_second_resend_is_409_while_the_first_is_queued(api, store, monkeypatch):
    _real_queue(monkeypatch, store)
    monkeypatch.setattr(interactive_utils, "action_submit_live_command", _REAL_SUBMIT)
    sid = signal(status="approved")
    first = api.post(resend_url(sid))
    assert first.status_code == 200
    again = api.post(resend_url(sid))
    assert again.status_code == 409 and first.json()["command_id"] in again.json()["detail"]


def test_resend_of_an_unknown_or_foreign_signal_is_404(api):
    assert api.post(resend_url("nope")).status_code == 404
    foreign = signal(instance_id="other", status="approved")
    assert api.post(resend_url(foreign)).status_code == 404
    assert api.post(resend_url(foreign, "nope")).status_code == 404
    assert api.commands == []


@pytest.mark.parametrize("instance_id,why", [("stopped", "not running"), ("crashed", "crashed")])
def test_resend_to_an_instance_that_cannot_run_it_is_503(api, instance_id, why):
    sid = signal(instance_id=instance_id, status="approved")
    res = api.post(resend_url(sid, instance_id))
    assert res.status_code == 503 and why in res.json()["detail"]
    assert api.commands == []


def test_resend_with_an_unreadable_queue_is_503_and_queues_nothing(api, monkeypatch):
    def unreadable(*a, **k):
        raise RuntimeError("postgres unavailable")

    monkeypatch.setattr(interactive_utils, "_swing_approval_commands", unreadable)
    res = api.post(resend_url(signal(status="approved")))
    assert res.status_code == 503 and "could not be read" in res.json()["detail"]
    assert api.commands == []


def test_resend_whose_write_raised_but_landed_is_202_uncertain(api, store, monkeypatch):
    landed = _landed_then_raised(monkeypatch, store)
    sid = signal(status="approved")
    res = api.post(resend_url(sid))
    assert res.status_code == 202
    body = res.json()
    assert body["uncertain"] is True and body["command_id"] == landed[0]["command_id"]
    assert "may be in flight" in body["detail"] and "manually" not in body["detail"]


def test_resend_whose_write_provably_failed_is_503_try_again(api, monkeypatch):
    _queue_down(monkeypatch)
    sid = signal(status="approved")
    res = api.post(resend_url(sid))
    assert res.status_code == 503
    assert "not queued" in res.json()["detail"] and "try again" in res.json()["detail"]
    assert signals_store.get_signal(sid)["status"] == "approved"


def test_resend_route_is_authenticated_like_its_neighbours():
    import inspect

    from fastapi.testclient import TestClient

    from api import main

    path = "/instances/{instance_id}/swing/signals/{signal_id}/resend"
    (endpoint,) = [r.endpoint for r in main.app.routes
                   if getattr(r, "path", None) == path and "POST" in r.methods]
    assert inspect.signature(endpoint).parameters["current_user"].default.dependency \
        is main.get_current_user
    main.app.dependency_overrides[main.conn_dependency] = lambda: None
    try:
        res = TestClient(main.app).post(f"/instances/{IID}/swing/signals/sid/resend",
                                        headers={"Authorization": "Bearer not-a-token"})
    finally:
        main.app.dependency_overrides.pop(main.conn_dependency, None)
    assert res.status_code == 401
