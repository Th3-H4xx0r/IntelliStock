"""forward_mirror: the F14_F paper mirror over the frozen forward-test rule."""
import datetime as dt
import hashlib
import os
import types

import pytest

import forward_mirror as fm

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FROZEN = os.path.join(REPO, "research", "forward-test-2026-11")
UTC = dt.timezone.utc


# ------------------------------------------------------------- frozen dir

def _write(path, data: bytes):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


def _frozen_dir(tmp_path, files):
    block = "\n".join(f"{hashlib.sha256(b).hexdigest()}  {rel}" for rel, b in files.items())
    for rel, b in files.items():
        _write(str(tmp_path / rel), b)
    _write(str(tmp_path / "FROZEN.md"), f"# frozen\n\n```\n{block}\n```\n".encode())
    return str(tmp_path)


def test_parse_hash_block_reads_only_hash_lines():
    h = "a" * 64
    assert fm.parse_hash_block(f"text\n```\n{h}  snapshot/SPY.csv\nnot {h}  x\n```") == {"snapshot/SPY.csv": h}


def test_verify_frozen_passes_on_matching_files(tmp_path):
    d = _frozen_dir(tmp_path, {"a.py": b"x = 1\n", "snapshot/S.csv": b"1,2\n"})
    assert fm.verify_frozen(d) == []


def test_verify_frozen_reports_edit_and_missing_file(tmp_path):
    d = _frozen_dir(tmp_path, {"a.py": b"x = 1\n", "b.py": b"y = 2\n"})
    _write(os.path.join(d, "a.py"), b"x = 2\n")
    os.remove(os.path.join(d, "b.py"))
    assert fm.verify_frozen(d) == ["hash mismatch a.py", "missing b.py"]


def test_verify_frozen_refuses_without_a_block(tmp_path):
    _write(str(tmp_path / "FROZEN.md"), b"no hashes here\n")
    assert fm.verify_frozen(str(tmp_path)) == ["FROZEN.md has no SHA-256 block"]


def test_the_committed_frozen_dir_matches_its_pins():
    assert fm.verify_frozen(FROZEN) == []


def test_frozen_next_on_the_snapshot_reproduces_the_freeze_state():
    pytest.importorskip("pandas")
    last, nxt = fm.frozen_next(FROZEN, os.path.join(FROZEN, "snapshot"), fetch=False)
    assert last == dt.date(2026, 11, 6)
    assert nxt["action"] == "HOLD"          # FROZEN.md section 10
    w = fm.model_weights(nxt["current"])
    assert set(w) == {"SPY", "DBMF", "IWD", "MTUM"}
    assert w["SPY"] == pytest.approx(0.518, abs=1e-3)
    assert w["DBMF"] == pytest.approx(0.435, abs=1e-3)


# ------------------------------------------------------------- pure logic

def test_model_weights_maps_keys_and_drops_cash():
    assert fm.model_weights({"market": 0.5, "fund": 0.417, "bond10": 0.0, "tbill": -0.4}) == {
        "SPY": 0.5, "DBMF": 0.417}


@pytest.mark.parametrize("book", [{"gold": 0.1}, {"market": -0.2}])
def test_model_weights_rejects_unknown_keys_and_shorts(book):
    with pytest.raises(ValueError):
        fm.model_weights(book)


def test_choose_target_trade_uses_the_decision():
    nxt = {"action": "TRADE", "target": {"market": 0.77, "fund": 0.417, "tbill": -0.187}, "current": {"market": 1.0}}
    assert fm.choose_target(nxt, {"SPY": 1.0}, synced=True) == ({"SPY": 0.77, "DBMF": 0.417}, "trade")


def test_choose_target_hold_syncs_repairs_or_holds():
    nxt = {"action": "HOLD", "target": None, "current": {"market": 0.5, "fund": 0.4, "tbill": 0.1}}
    want = {"SPY": 0.5, "DBMF": 0.4}
    assert fm.choose_target(nxt, {}, synced=False) == (want, "initial_sync")
    assert fm.choose_target(nxt, {"SPY": 0.5, "DBMF": 0.32}, synced=True) == (want, "drift_repair")
    assert fm.choose_target(nxt, {"SPY": 0.52, "DBMF": 0.38}, synced=True) == (None, "hold")


def test_plan_orders_sells_first_and_sizes_each_leg():
    target = {"SPY": 0.5, "DBMF": 0.4, "MTUM": 0.1}
    positions = {"SPY": 10.0, "IWD": 3.0, "MTUM": 1.0}
    prices = {"SPY": 600.0, "DBMF": 30.0, "IWD": 200.0, "MTUM": 250.0}
    frac = {"SPY": True, "DBMF": False, "IWD": True, "MTUM": True}
    orders = fm.plan_orders(target, 10000.0, positions, prices, frac)
    assert [(o.symbol, o.side) for o in orders] == [("IWD", "sell"), ("SPY", "sell"), ("DBMF", "buy"),
                                                     ("MTUM", "buy")]
    iwd, spy, dbmf, mtum = orders
    assert iwd.qty == 3.0                                   # leaves the book: sold in full
    assert spy.qty == pytest.approx(1000.0 / 600.0, abs=1e-6)
    assert dbmf.qty == 133.0 and dbmf.notional is None      # not fractionable: whole shares
    assert mtum.notional == 750.0 and mtum.qty is None


def test_plan_orders_skips_legs_under_the_minimum_but_still_exits():
    orders = fm.plan_orders({"SPY": 0.5}, 10000.0, {"SPY": 8.34, "IEF": 0.01}, {"SPY": 600.0, "IEF": 95.0},
                            {"SPY": True, "IEF": True})
    assert [(o.symbol, o.side, o.qty) for o in orders] == [("IEF", "sell", 0.01)]


def test_in_act_window_bounds():
    close = dt.datetime(2026, 11, 9, 21, 0, tzinfo=UTC)
    at = lambda m: close - dt.timedelta(minutes=m)                       # noqa: E731
    assert not fm.in_act_window(True, at(21), close)
    assert fm.in_act_window(True, at(20), close)
    assert fm.in_act_window(True, at(3), close)
    assert not fm.in_act_window(True, at(2), close)
    assert not fm.in_act_window(False, at(10), close)


# ------------------------------------------------------------- the runner

def test_choose_target_caps_a_drifted_hold_book_but_refuses_an_overcap_trade():
    nxt = {"action": "HOLD", "target": None, "current": {"market": 1.6, "fund": 0.5}}
    target, reason = fm.choose_target(nxt, {}, synced=False)
    assert reason == "initial_sync_capped"
    assert sum(target.values()) == pytest.approx(2.0)
    assert target["SPY"] / target["DBMF"] == pytest.approx(1.6 / 0.5)
    with pytest.raises(fm.MirrorRefused, match="frozen cap"):
        fm.choose_target({"action": "TRADE", "target": {"market": 1.6, "fund": 0.5}}, {}, synced=True)


def test_the_frozen_subprocess_sees_no_secrets(monkeypatch):
    monkeypatch.setenv("INTELLISTOCK_CRED_KEY", "k")
    monkeypatch.setenv("POSTGRES_PASSWORD", "p")
    monkeypatch.setenv("PATH", "/usr/bin")
    env = fm._frozen_env()
    assert env["PATH"] == "/usr/bin" and env["PYTHONDONTWRITEBYTECODE"] == "1"
    assert "INTELLISTOCK_CRED_KEY" not in env and "POSTGRES_PASSWORD" not in env


# ------------------------------------------------------------- the runner

SESSIONS = [dt.date(2026, 11, 5), dt.date(2026, 11, 6), dt.date(2026, 11, 9)]
HOLD = {"action": "HOLD", "target": None,
        "current": {"market": 0.518, "MTUM": 0.156, "IWD": 0.327, "fund": 0.435, "tbill": -0.436}}
TRADE = {"action": "TRADE", "target": {"market": 0.77, "MTUM": 0.245, "IWD": 0.484, "fund": 0.417, "tbill": -0.916},
         "current": HOLD["current"]}


def _status_obj(v):
    return types.SimpleNamespace(value=v)


class FakeClient:
    def __init__(self, now, positions=(), account_number=fm.DEFAULT_ACCOUNT_NUMBER, equity=10000.0, multiplier=2,
                 fractionable=None, open_orders=(), fail_submit=(), fail_poll=False, final_status="filled"):
        self.now = now
        self.positions = list(positions)
        self.account = types.SimpleNamespace(account_number=account_number, equity=str(equity),
                                             multiplier=str(multiplier), trading_blocked=False, account_blocked=False)
        self.fractionable = fractionable or {}
        self.open_orders = list(open_orders)
        self.fail_submit = set(fail_submit)
        self.fail_poll = fail_poll
        self.final_status = final_status
        self.submitted = []
        self.landed = {}

    def get_clock(self):
        close = dt.datetime.combine(self.now().date(), dt.time(21, 0), UTC)
        is_open = dt.datetime.combine(self.now().date(), dt.time(14, 30), UTC) <= self.now() < close
        return types.SimpleNamespace(is_open=is_open, timestamp=self.now(), next_close=close)

    def get_calendar(self, req):
        return [types.SimpleNamespace(date=d) for d in SESSIONS if req.start <= d <= req.end]

    def get_account(self):
        return self.account

    def get_all_positions(self):
        return self.positions

    def get_orders(self, req):
        return self.open_orders

    def get_asset(self, sym):
        return types.SimpleNamespace(fractionable=self.fractionable.get(sym, True), tradable=True, marginable=True)

    def submit_order(self, req):
        self.submitted.append(req)
        o = types.SimpleNamespace(id=f"o{len(self.submitted)}", status=_status_obj("accepted"),
                                  client_order_id=req.client_order_id, filled_qty="0", filled_avg_price=None)
        if req.symbol in self.fail_submit:
            raise TimeoutError("read timed out")
        return o

    def get_order_by_client_id(self, cid):
        if cid in self.landed:
            return self.landed[cid]
        raise LookupError(cid)

    def get_order_by_id(self, oid):
        if self.fail_poll:
            raise TimeoutError("poll timed out")
        return types.SimpleNamespace(status=_status_obj(self.final_status), filled_qty="1", filled_avg_price="100")


def _pos(weights, equity=10000.0, price=100.0):
    return [types.SimpleNamespace(symbol=s, qty=str(w * equity / price), market_value=str(w * equity),
                                  current_price=str(price)) for s, w in weights.items()]


def _mirror(store, client, clock, nxt=HOLD, last=dt.date(2026, 11, 6), advance=None, **kw):
    calls = []

    def next_fn(timeout=None):
        calls.append((clock[0], timeout))
        if advance:
            clock[0] += advance
        return last, nxt

    m = fm.Mirror(store=store, client=client, prices_fn=lambda syms: {s: 100.0 for s in syms},
                  brokerage_id=fm.DEFAULT_BROKERAGE_ID, frozen_dir="/nope", data_dir="/nope", next_fn=next_fn,
                  now_fn=lambda: clock[0], sleep_fn=lambda s: None, **kw)
    m.next_calls = calls
    return m


def _at(h, m):
    return [dt.datetime(2026, 11, 9, h, m, tzinfo=UTC)]


def _act_row(store, day="2026-11-09", kind="act"):
    return store.get(fm.TABLE, f"f14-paper|{day}|{kind}")


def test_first_session_syncs_the_empty_account_once(store):
    clock = _at(20, 45)                                     # 15:45 ET, 15 minutes before the close
    client = FakeClient(lambda: clock[0])
    m = _mirror(store, client, clock)
    m.step()
    assert sorted((r.symbol, r.side.value, r.notional) for r in client.submitted) == [
        ("DBMF", "buy", 4350.0), ("IWD", "buy", 3270.0), ("MTUM", "buy", 1560.0), ("SPY", "buy", 5180.0)]
    st = store.get(fm.TABLE, fm.STATE_ID)
    assert st["last_act_session"] == "2026-11-09" and st["synced"] is True
    row = _act_row(store)
    assert row["reason"] == "initial_sync" and len(row["orders"]) == 4 and row["status"] == "done"
    assert m.next_calls[0][1] == fm.FETCH_TIMEOUT_SECONDS
    clock[0] += dt.timedelta(minutes=6)
    m.step()                                                # same session: at most once
    assert len(client.submitted) == 4


def test_a_trade_day_sells_then_buys(store):
    clock = _at(20, 45)
    client = FakeClient(lambda: clock[0], positions=_pos({"SPY": 0.9, "MTUM": 0.3, "IWD": 0.3, "DBMF": 0.418}))
    store.insert(fm.TABLE, {"id": fm.STATE_ID, "mirror": fm.MIRROR, "synced": True})
    _mirror(store, client, clock, nxt=TRADE).step()
    # DBMF is $10 off target: under the minimum, untouched.
    assert [(r.symbol, r.side.value) for r in client.submitted] == [("MTUM", "sell"), ("SPY", "sell"), ("IWD", "buy")]
    assert _act_row(store)["reason"] == "trade"


def test_a_failed_sell_poll_still_sends_the_buys_and_writes_the_row(store):
    clock = _at(20, 45)
    client = FakeClient(lambda: clock[0], positions=_pos({"SPY": 0.9, "IWD": 0.3}), fail_poll=True)
    store.insert(fm.TABLE, {"id": fm.STATE_ID, "mirror": fm.MIRROR, "synced": True})
    _mirror(store, client, clock, nxt=TRADE).step()
    sides = [r.side.value for r in client.submitted]
    assert sides[0] == "sell" and "buy" in sides
    assert len(_act_row(store)["orders"]) == len(client.submitted)


def test_a_submit_timeout_that_landed_is_recorded_by_client_id(store):
    clock = _at(20, 45)
    client = FakeClient(lambda: clock[0], fail_submit={"SPY", "DBMF"})
    cid = "f14m-2026-11-09-buy-SPY-3"
    client.landed[cid] = types.SimpleNamespace(id="landed1", status=_status_obj("accepted"), filled_qty=None,
                                               filled_avg_price=None)
    _mirror(store, client, clock).step()
    by_sym = {o["symbol"]: o for o in _act_row(store)["orders"]}
    assert by_sym["SPY"]["id"] == "landed1" and by_sym["SPY"]["status"] == "accepted"
    assert by_sym["DBMF"]["status"] == "error"
    assert store.get(fm.TABLE, fm.STATE_ID)["synced"] is True     # a leg got through: the drift band repairs the rest


def test_a_sync_that_sends_nothing_stays_unsynced(store):
    clock = _at(20, 45)
    client = FakeClient(lambda: clock[0], fail_submit={"SPY", "DBMF", "IWD", "MTUM"})
    _mirror(store, client, clock).step()
    assert {o["status"] for o in _act_row(store)["orders"]} == {"error"}
    assert not store.get(fm.TABLE, fm.STATE_ID).get("synced")


def test_the_deadline_is_checked_before_every_order(store):
    clock = _at(20, 56)                                     # deadline 20:57
    client = FakeClient(lambda: clock[0])
    orig = client.submit_order

    def slow_submit(req):
        clock[0] += dt.timedelta(seconds=40)
        return orig(req)

    client.submit_order = slow_submit
    _mirror(store, client, clock).step()
    statuses = [o["status"] for o in _act_row(store)["orders"]]
    assert len(client.submitted) == 2 and statuses.count("skipped_deadline") == 2


def test_no_order_after_the_deadline_even_when_the_fetch_runs_long(store):
    clock = _at(20, 52)                                     # 8 minutes to the close
    client = FakeClient(lambda: clock[0])
    m = _mirror(store, client, clock, advance=dt.timedelta(minutes=6))
    m.step()
    assert m.next_calls[0][1] == pytest.approx(5 * 60)     # the fetch is bounded by the deadline
    assert client.submitted == []
    assert "deadline" in _act_row(store)["error"]
    assert not (store.get(fm.TABLE, fm.STATE_ID) or {}).get("last_act_session")


def test_a_stale_frame_trades_nothing_retries_after_the_cooldown_and_leaves_a_row(store):
    clock = _at(20, 41)
    client = FakeClient(lambda: clock[0])
    m = _mirror(store, client, clock, last=dt.date(2026, 11, 5))
    m.step()
    clock[0] += dt.timedelta(minutes=1)
    m.step()                                                # inside the cooldown: no re-fetch
    assert client.submitted == [] and len(m.next_calls) == 1
    assert store.get(fm.TABLE, fm.STATE_ID) is None
    row = _act_row(store)
    assert row["status"] == "pending" and "stale frame" in row["error"] and row["attempts"] == 1
    clock[0] += dt.timedelta(minutes=5)
    m.step()
    assert len(m.next_calls) == 2 and _act_row(store)["attempts"] == 2


def test_outside_the_window_nothing_happens(store):
    clock = _at(18, 0)                                      # 13:00 ET
    client = FakeClient(lambda: clock[0])
    m = _mirror(store, client, clock)
    m.step()
    assert client.submitted == [] and m.next_calls == []


def test_dry_run_records_the_plan_and_spends_only_its_own_session(store):
    clock = _at(20, 45)
    client = FakeClient(lambda: clock[0])
    _mirror(store, client, clock, dry_run=True).step()
    assert client.submitted == []
    row = _act_row(store, kind="dry")
    assert row["dry_run"] is True and {o["status"] for o in row["orders"]} == {"dry_run"}
    assert _act_row(store) is None                          # never the live act row
    st = store.get(fm.TABLE, fm.STATE_ID)
    assert st["last_dry_session"] == "2026-11-09" and not st.get("last_act_session") and not st.get("synced")
    _mirror(store, client, clock).step()                    # dry run off, same day: it trades
    assert len(client.submitted) == 4


def test_a_synced_account_on_a_hold_day_is_left_alone(store):
    clock = _at(20, 45)
    client = FakeClient(lambda: clock[0], positions=_pos({"SPY": 0.52, "MTUM": 0.15, "IWD": 0.33, "DBMF": 0.43}))
    store.insert(fm.TABLE, {"id": fm.STATE_ID, "mirror": fm.MIRROR, "synced": True})
    _mirror(store, client, clock).step()
    assert client.submitted == []
    assert _act_row(store)["reason"] == "hold"


@pytest.mark.parametrize("setup, msg", [
    (dict(account_number="123456"), "not an Alpaca paper account"),
    (dict(account_number="PA0THER00000"), "not the pinned mirror account"),
    (dict(positions=_pos({"BTCUSD": 0.01})), "non-mirror positions"),
    (dict(open_orders=[types.SimpleNamespace(symbol="GLD", client_order_id="eb-123")]), "did not place"),
])
def test_refusals(store, setup, msg):
    clock = _at(20, 45)
    client = FakeClient(lambda: clock[0], **setup)
    m = _mirror(store, client, clock)
    with pytest.raises(fm.MirrorRefused, match=msg):
        m.step()
    assert client.submitted == []


def test_refuses_when_a_running_instance_shares_the_account(store):
    store.insert("Instances", {"id": "test", "brokerage_id": fm.DEFAULT_BROKERAGE_ID, "runCommand": True})
    clock = _at(20, 45)
    client = FakeClient(lambda: clock[0])
    with pytest.raises(fm.MirrorRefused, match="running instance"):
        _mirror(store, client, clock).step()
    assert client.submitted == []


def test_margin_is_required_above_gross_one(store):
    clock = _at(20, 45)
    client = FakeClient(lambda: clock[0], multiplier=1)
    _mirror(store, client, clock).step()
    assert client.submitted == []
    assert "margin unavailable" in _act_row(store)["error"]


def test_an_unexpected_error_leaves_a_pending_row_and_does_not_spend_the_session(store):
    clock = _at(20, 45)
    client = FakeClient(lambda: clock[0])
    m = _mirror(store, client, clock)
    m.prices_fn = lambda syms: (_ for _ in ()).throw(RuntimeError("no latest trade for ['DBMF']"))
    with pytest.raises(RuntimeError):
        m.step()
    row = _act_row(store)
    assert row["status"] == "pending" and "no latest trade" in row["error"]
    assert not (store.get(fm.TABLE, fm.STATE_ID) or {}).get("last_act_session")


def test_close_snapshot_records_fills_and_resyncs_after_a_rejection(store):
    clock = _at(20, 45)
    client = FakeClient(lambda: clock[0])
    m = _mirror(store, client, clock)
    m.step()
    client.final_status = "rejected"
    clock[0] = dt.datetime(2026, 11, 9, 23, 45, tzinfo=UTC)   # 18:45 ET
    m._fresh = None
    m.next_fn = lambda timeout=None: (dt.date(2026, 11, 9), HOLD)
    m.step()
    row = store.get(fm.TABLE, "f14-paper|2026-11-09|close")
    assert row["next_action"] == "HOLD" and row["acted"] is True and row["all_filled"] is False
    assert {o["status"] for o in _act_row(store)["orders"]} == {"rejected"}
    st = store.get(fm.TABLE, fm.STATE_ID)
    assert st["last_close_session"] == "2026-11-09" and st["synced"] is True


def test_after_a_rejected_leg_a_hold_inside_the_band_does_not_rebalance_the_book(store):
    clock = _at(20, 45)
    client = FakeClient(lambda: clock[0], final_status="rejected")
    m = _mirror(store, client, clock)
    m.step()
    n = len(client.submitted)
    # Next session: the account sits within the band of the model's book.
    clock[0] = dt.datetime(2026, 11, 10, 20, 45, tzinfo=UTC)
    SESSIONS.append(dt.date(2026, 11, 10))
    try:
        client.positions = _pos({"SPY": 0.5, "MTUM": 0.15, "IWD": 0.33, "DBMF": 0.42})
        m2 = _mirror(store, client, clock, last=dt.date(2026, 11, 9))
        m2.step()
    finally:
        SESSIONS.pop()
    assert len(client.submitted) == n
    assert _act_row(store, day="2026-11-10")["reason"] == "hold"
