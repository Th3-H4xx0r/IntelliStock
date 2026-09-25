"""StrategyWheel: inert in backtests, and the weekly put scan (spec §5.2)."""
import importlib.util
import json
import os
import re
import sys
from datetime import datetime, timezone
from types import SimpleNamespace as NS

import pytest

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from swing_trader import clock, signals_store  # noqa: E402
from swing_trader.constants import WHEEL_DEFAULTS  # noqa: E402

PATH = os.path.join(_backend, "strategies", "strategy_wheel.py")
MON_1040 = datetime(2026, 6, 1, 14, 40, tzinfo=timezone.utc)
MON_1100 = datetime(2026, 6, 1, 15, 0, tzinfo=timezone.utc)
MON_1020 = datetime(2026, 6, 1, 14, 20, tzinfo=timezone.utc)
TUE_1040 = datetime(2026, 6, 2, 14, 40, tzinfo=timezone.utc)
WED_1040 = datetime(2026, 6, 3, 14, 40, tzinfo=timezone.utc)
EXPIRY = "2026-06-12"


def _load():
    spec = importlib.util.spec_from_file_location("strategies.strategy_wheel", PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def pre(symbol, price, strike, prem):
    return {"symbol": symbol, "stock_price": price, "strike_price": strike,
            "otm_pct": round((price - strike) / price * 100, 2), "est_premium": prem,
            "est_premium_pct": round(prem / price * 100, 3), "rsi": 48.0,
            "sma50": price * 0.95, "atr": prem * 4, "atr_pct": 4.0}


PRE = [pre("APH", 131.2, 127.8, 1.7), pre("GIS", 60.0, 58.5, 0.8), pre("KO", 70.0, 68.2, 0.9)]
STRIKES = {"APH": (126.0, 127.0, 128.0), "GIS": (57.0, 58.0, 59.0), "KO": (67.0, 68.0, 69.0)}


def occ(underlying, strike, kind="P", expiry=EXPIRY):
    return f"{underlying}{expiry[2:4]}{expiry[5:7]}{expiry[8:10]}{kind}{int(strike * 1000):08d}"


class WheelAdapter:
    def __init__(self, option_positions=(), open_orders=(), cash=100_000.0,
                 equity=100_000.0, equities=None, broken_orders=False):
        self.option_positions = list(option_positions)
        self.orders = list(open_orders)
        self.cash, self.equity = cash, equity
        self.equities = dict(equities or {})
        self.broken_orders = broken_orders

    def get_positions(self):
        return {s: v[0] for s, v in self.equities.items()}

    def refresh_positions(self):
        return [NS(symbol=s, qty=v[0], avg_entry_price=v[1], market_value=v[0] * v[1])
                for s, v in self.equities.items()]

    def list_option_positions(self):
        return list(self.option_positions)

    def list_open_orders(self, limit=200):
        if self.broken_orders:
            raise RuntimeError("orders endpoint down")
        return list(self.orders)

    def get_account_options(self):
        return {"cash": self.cash, "equity": self.equity}

    def refresh_account(self):
        return NS(equity=self.equity)

    def get_cash(self):
        return self.cash

    def get_option_contracts(self, underlying, **kw):
        return [NS(symbol=occ(underlying, k), underlying=underlying, option_type="put",
                   strike=k, expiration=EXPIRY, open_interest=10, close_price=1.0)
                for k in STRIKES.get(underlying, ())]

    def get_option_snapshots(self, symbols):
        out = {}
        for s in symbols:
            strike = int(s[-8:]) / 1000
            middle = STRIKES[s[:-15]][1]
            out[s] = NS(bid=1.0, delta=(-0.25 if strike == middle else -0.10))
        return out

    def get_latest_trades(self, symbols):
        return {}


def scorer(results):
    calls = []

    def score(candidate, **kw):
        calls.append(candidate["symbol"])
        r = results[candidate["symbol"]]
        return dict(candidate, conviction_score=r[0], recommendation=r[1],
                    reasoning="r", position_size_contracts=r[2], key_risks=[])

    score.calls = calls
    return score


@pytest.fixture
def wheel(store, monkeypatch):
    m = _load()
    monkeypatch.setattr(m, "store", store)
    monkeypatch.setattr(signals_store, "store", store)
    monkeypatch.setattr(m.market_data, "data_client", lambda k, s: object())
    monkeypatch.setattr(m.market_data, "get_daily_bars", lambda syms, days, client: {"APH": object()})
    monkeypatch.setattr(m.universe, "get_wheel_universe", lambda: ["APH", "GIS", "KO"])
    monkeypatch.setattr(m.wheel_rules, "screen_technicals", lambda raw, syms, cfg=None: [dict(p) for p in PRE])
    monkeypatch.setattr(m.wheel_rules, "wheel_earnings_days", lambda s: None)
    monkeypatch.setattr(m.clock, "is_trading_day", lambda d: True)
    monkeypatch.setattr(m.calibration, "record_outcomes", lambda *a, **k: 0)
    monkeypatch.setattr(m.ai_analyst, "score_candidate", scorer(
        {"APH": (80, "approve", 1), "GIS": (60, "review", 1), "KO": (30, "reject", 1)}))
    # The daily monitor and the IV snapshot (Task 19) have their own tests.
    monkeypatch.setattr(m.StrategyWheel, "_monitor", lambda self, *a: [])
    monkeypatch.setattr(m.StrategyWheel, "_iv", lambda self, *a: [])
    sent = []
    monkeypatch.setattr(m.notify, "send",
                        lambda cat, iid, title, msg, priority=0: sent.append((cat, title)))
    m.sent = sent
    clock._DEADLINES.clear()
    return m


def cfg(**over):
    c = dict(WHEEL_DEFAULTS, strategy_wheel_enabled=True, instance_id="swing-paper",
             alpaca_key="k", alpaca_secret="s", conviction_llm_provider="claude-cli",
             conviction_llm_model="claude-sonnet-4-6", conviction_llm_api_key="")
    c.update(over)
    return c


def tick(m, at, adapter, cache, config=None, mode="MONITOR", data=None):
    return m.StrategyWheel().run_once(["SPY"], {}, at, config or cfg(), {}, data=data,
                                      portfolio_emulator=adapter, strategy_cache=cache,
                                      mode=mode)


def orders(out):
    return out.get("_nexus_option_orders", []) if out else []


# -- header and inert backtests ----------------------------------------------

def test_the_schema_header_is_exactly_the_defaults():
    schema = json.loads(re.search(r"# INTELLISTOCK_SCHEMA: (.*)", open(PATH).read()).group(1))
    assert schema["strategy"] == "strategy_wheel" and schema["execution_scope"] == "run_once"
    assert schema["execution_position"] == 20 and schema["decision_phase"] == "pre"
    assert schema["config"] == WHEEL_DEFAULTS and list(schema["config"]) == list(WHEEL_DEFAULTS)
    assert hasattr(_load().StrategyWheel, "run_once")


def test_backtests_are_inert_and_say_so_once(wheel, monkeypatch):
    lines = []
    monkeypatch.setattr(wheel, "_log", lambda msg, color="white": lines.append(msg))
    cache = {}
    assert tick(wheel, MON_1040, WheelAdapter(), cache, data={"APH": []}) == {}
    assert tick(wheel, MON_1100, WheelAdapter(), cache, data={"APH": []}) == {}
    assert sum("live-only" in line for line in lines) == 1
    assert tick(wheel, MON_1040, WheelAdapter(), {}, config=cfg(strategy_wheel_enabled=False)) == {}


# -- the Monday scan -----------------------------------------------------------

def test_monday_scan_sells_the_approved_put_and_records_every_score(wheel):
    cache = {}
    out = tick(wheel, MON_1040, WheelAdapter(), cache)
    sid = signals_store.signal_id_for("swing-paper", "wheel", "2026-06-01", "APH")
    assert out["_nexus_position_sizes"] == {"_cash_reserve_floor_pct": 0.0}
    assert orders(out) == [{
        "signal_id": sid, "session": "2026-06-01", "underlying": "APH",
        "contract": occ("APH", 127.0), "option_type": "put", "strike": 127.0,
        "expiry": EXPIRY, "position_intent": "sell_to_open", "qty": 1,
        "order_type": "limit", "limit_price": 0.95, "tif": "day",
        "reason": "wheel_sto_put"}]
    rows = {r["symbol"]: r for r in signals_store.all_signals("swing-paper")}
    assert {s: r["status"] for s, r in rows.items()} == {
        "APH": "auto_approved", "GIS": "pending", "KO": "ai_rejected"}
    assert rows["APH"]["proposal"]["contract"] == occ("APH", 127.0)
    assert rows["GIS"]["proposal"]["expiry"] == EXPIRY and rows["GIS"]["proposal"]["qty"] == 1
    scans = {r["symbol"]: r["status"] for r in signals_store.list_wheel_scans("swing-paper")}
    assert scans == {"APH": "placed", "GIS": "pending", "KO": "rejected"}
    assert cache[wheel._COMPLETE_KEY] == "2026-06-01" and wheel._SCAN_KEY not in cache
    cats = [c for c, _ in wheel.sent]
    assert cats == ["wheel_put_placed", "wheel_pending_review", "swing_run_summary"]
    assert tick(wheel, MON_1100, WheelAdapter(), cache) == {}          # done for the week


def test_the_scan_waits_for_its_day_its_time_and_regular_hours(wheel):
    assert tick(wheel, MON_1020, WheelAdapter(), {}) == {}
    assert tick(wheel, WED_1040, WheelAdapter(), {}) == {}
    after_close = datetime(2026, 6, 1, 20, 30, tzinfo=timezone.utc)
    assert tick(wheel, after_close, WheelAdapter(), {}) == {}
    assert wheel.ai_analyst.score_candidate.calls == []


def test_tuesday_fallback_runs_only_without_a_completion_marker(wheel):
    assert tick(wheel, TUE_1040, WheelAdapter(),
                {wheel._COMPLETE_KEY: "2026-06-01"}) == {}
    assert wheel.ai_analyst.score_candidate.calls == []
    cache = {wheel._COMPLETE_KEY: "2026-05-26"}                         # last week
    out = tick(wheel, TUE_1040, WheelAdapter(), cache)
    assert [o["underlying"] for o in orders(out)] == ["APH"]
    assert cache[wheel._COMPLETE_KEY] == "2026-06-02"


def test_a_crashed_monday_scan_resumes_without_rescoring(wheel, monkeypatch):
    budget = iter([100.0, 50.0, 50.0, 50.0, 60.0, 10.0])
    monkeypatch.setattr(wheel.clock, "time_left", lambda deadline: next(budget, 1_000.0))
    cache = {}
    first = tick(wheel, MON_1040, WheelAdapter(), cache)
    assert [o["underlying"] for o in orders(first)] == ["APH"]
    assert cache[wheel._SCAN_KEY]["phase"] == "scoring" and cache[wheel._SCAN_KEY]["cursor"] == 1
    assert wheel._COMPLETE_KEY not in cache
    # A fresh strategy object over the persisted cache: the broker restarted.
    working = WheelAdapter(open_orders=[NS(symbol=occ("APH", 127.0), side="sell", qty=1,
                                           filled_qty=0, position_intent="sell_to_open")])
    second = tick(wheel, MON_1100, working, cache, mode="FULL")
    assert orders(second) == []
    assert wheel.ai_analyst.score_candidate.calls == ["APH", "GIS", "KO"]
    assert cache[wheel._COMPLETE_KEY] == "2026-06-01"


def test_a_rerun_after_losing_the_cache_does_not_sell_a_second_put(wheel):
    # fix 1: the first run sold APH, then the process died before the cache
    # was saved. The rerun finds the recorded decision and the working order.
    signals_store.insert_signal(signals_store.new_signal(
        instance_id="swing-paper", lane="wheel", symbol="APH", session="2026-06-01",
        score=80, recommendation="approve", reasoning="r", key_risks=[],
        size_adjustment=None, proposal={"contract": occ("APH", 127.0)},
        status="auto_approved"))
    working = WheelAdapter(open_orders=[NS(symbol=occ("APH", 127.0), side="sell", qty=1,
                                           filled_qty=0, position_intent="sell_to_open")])
    out = tick(wheel, MON_1040, working, {})
    assert orders(out) == []
    assert "APH" not in wheel.ai_analyst.score_candidate.calls


def test_an_underlying_with_an_open_put_is_skipped(wheel):
    held = NS(symbol=occ("APH", 120.0, expiry="2026-06-05"), underlying="APH",
              option_type="put", strike=120.0, expiry="2026-06-05", qty=-1,
              avg_entry_price=1.0, market_value=-60.0)
    out = tick(wheel, MON_1040, WheelAdapter(option_positions=[held]), {})
    assert orders(out) == []
    scan = {r["symbol"]: r for r in signals_store.list_wheel_scans("swing-paper")}["APH"]
    assert scan["status"] == "skipped" and "duplicate" in scan["skip_reason"]
    sig = signals_store.get_signal(signals_store.signal_id_for(
        "swing-paper", "wheel", "2026-06-01", "APH"))
    assert sig["status"] == "failed" and "duplicate" in sig["error"]


def test_an_unreadable_order_book_sells_nothing_and_resumes(wheel):
    cache = {}
    assert orders(tick(wheel, MON_1040, WheelAdapter(broken_orders=True), cache)) == []
    assert cache[wheel._SCAN_KEY]["cursor"] == 0
    out = tick(wheel, MON_1100, WheelAdapter(), cache)
    assert [o["underlying"] for o in orders(out)] == ["APH"]


def test_no_model_linked_refuses_the_scan_with_one_alert(wheel):
    cache = {}
    no_model = cfg(conviction_llm_provider="", conviction_llm_model="")
    assert tick(wheel, MON_1040, WheelAdapter(), cache, config=no_model) == {}
    assert tick(wheel, MON_1100, WheelAdapter(), cache, config=no_model) == {}
    assert [c for c, _ in wheel.sent] == ["strategy_error"]
    assert wheel._COMPLETE_KEY not in cache


# -- controller rulings (G7) -------------------------------------------------------

class SwallowingAdapter(WheelAdapter):
    """AlpacaAdapter.list_open_orders answers an outage with [] (alpaca.py
    L1293-1297); only list_open_orders_strict raises."""

    def __init__(self, *a, strict_orders=None, strict_down=False, **kw):
        super().__init__(*a, **kw)
        self.strict_orders, self.strict_down = list(strict_orders or ()), strict_down

    def list_open_orders(self, limit=200):
        return []

    def list_open_orders_strict(self, limit=200):
        if self.strict_down:
            raise RuntimeError("orders endpoint down")
        return list(self.strict_orders)


def test_ruling_1_an_unreadable_strict_book_is_never_read_as_empty(wheel, monkeypatch):
    lines = []
    monkeypatch.setattr(wheel, "_log", lambda msg, color="white": lines.append(msg))
    cache = {}
    out = tick(wheel, MON_1040, SwallowingAdapter(strict_down=True), cache)
    assert orders(out) == []
    assert cache[wheel._SCAN_KEY]["phase"] == "scoring" and cache[wheel._SCAN_KEY]["cursor"] == 0
    assert wheel.ai_analyst.score_candidate.calls == []
    assert any("unreadable" in line and "no put" in line for line in lines)
    assert [o["underlying"] for o in orders(tick(wheel, MON_1100, WheelAdapter(), cache))] == ["APH"]


def test_ruling_1_the_scan_reads_working_orders_through_the_strict_reader(wheel):
    # The swallowing reader says "nothing working"; the strict one shows the
    # APH sell-to-open already at the broker, so no second APH put is sold.
    working = NS(symbol=occ("APH", 127.0), side="sell", qty=1, filled_qty=0,
                 position_intent="sell_to_open")
    out = tick(wheel, MON_1040, SwallowingAdapter(strict_orders=[working]), {})
    assert orders(out) == []
    scan = {r["symbol"]: r for r in signals_store.list_wheel_scans("swing-paper")}["APH"]
    assert scan["status"] == "skipped" and "working sell order" in scan["skip_reason"]


def test_ruling_6_a_started_but_unfinished_monday_scan_gets_the_tuesday_fallback(wheel):
    # Monday's scan STARTED (its resumable state is cached) but never
    # completed: only the completion marker ends the week.
    cache = {wheel._SCAN_KEY: {"session": "2026-06-01", "phase": "scoring", "cursor": 1,
                               "queue": [], "pre": [], "candidates": []}}
    out = tick(wheel, TUE_1040, WheelAdapter(), cache)
    assert [o["underlying"] for o in orders(out)] == ["APH"]
    assert wheel.ai_analyst.score_candidate.calls == ["APH", "GIS", "KO"]
    assert cache[wheel._COMPLETE_KEY] == "2026-06-02" and wheel._SCAN_KEY not in cache


def test_ruling_6_one_scan_never_sells_two_puts_on_one_underlying(wheel, monkeypatch):
    monkeypatch.setattr(wheel.wheel_rules, "screen_technicals",
                        lambda raw, syms, cfg=None: [dict(p) for p in PRE + PRE[:1]])
    # A $1M account: the collateral cap alone would allow a second APH put.
    rich = WheelAdapter(cash=1_000_000.0, equity=1_000_000.0)
    out = tick(wheel, MON_1040, rich, {})
    assert [o["underlying"] for o in orders(out)] == ["APH"]
    scans = {r["symbol"]: r["status"] for r in signals_store.list_wheel_scans("swing-paper")}
    assert scans["APH"] == "placed"
    assert [c for c, _ in wheel.sent].count("wheel_put_placed") == 1


@pytest.mark.parametrize("bad", ["10h30", "25:00", "", None, "10:75",
                                 "08:00", "09:29", "16:00", "17:15"])
def test_ruling_8_an_unparseable_scan_time_refuses_the_scan_and_says_so_once(
        wheel, monkeypatch, bad):
    lines = []
    monkeypatch.setattr(wheel, "_log", lambda msg, color="white": lines.append(msg))
    cache = {}
    # 00:05 ET would pass a 00:00 fallback; 10:40 would pass the default.
    for at in (datetime(2026, 6, 1, 4, 5, tzinfo=timezone.utc), MON_1040, MON_1100):
        assert tick(wheel, at, WheelAdapter(), cache, config=cfg(scan_time_et=bad)) == {}
    assert wheel.ai_analyst.score_candidate.calls == []
    assert wheel._SCAN_KEY not in cache and wheel._COMPLETE_KEY not in cache
    assert sum("scan_time_et" in line and "REFUSING" in line for line in lines) == 1


def test_ruling_8_a_padded_scan_time_is_read_as_that_time(wheel):
    assert tick(wheel, MON_1020, WheelAdapter(), {}, config=cfg(scan_time_et=" 10:30 ")) == {}
    out = tick(wheel, MON_1040, WheelAdapter(), {}, config=cfg(scan_time_et=" 10:30 "))
    assert [o["underlying"] for o in orders(out)] == ["APH"]


# -- Task 19: position checks, the daily monitor, the IV snapshot ------------

from datetime import date  # noqa: E402

from swing_trader import iv as _iv_module  # noqa: E402

_REAL_RUN_IV = _iv_module.run_iv_snapshot

MON_1540 = datetime(2026, 6, 1, 19, 40, tzinfo=timezone.utc)
MON_1520 = datetime(2026, 6, 1, 19, 20, tzinfo=timezone.utc)
MON_0900 = datetime(2026, 6, 1, 13, 0, tzinfo=timezone.utc)
MON_0920 = datetime(2026, 6, 1, 13, 20, tzinfo=timezone.utc)
MON_0940 = datetime(2026, 6, 1, 13, 40, tzinfo=timezone.utc)
TODAY = date(2026, 6, 1)


def short(underlying, strike, *, kind="P", expiry=EXPIRY, qty=-1, entry=1.0, value=-60.0):
    return NS(symbol=occ(underlying, strike, kind, expiry), underlying=underlying,
              option_type="put" if kind == "P" else "call", strike=strike, expiry=expiry,
              qty=qty, avg_entry_price=entry, market_value=value, current_price=None)


def assignment(underlying, shares=100, side="buy"):
    return {"activity_id": f"a-{underlying}-{side}", "contract": occ(underlying, 68.0),
            "underlying": underlying, "shares": shares, "side": side, "strike": 68.0,
            "date": "2026-05-29"}


@pytest.fixture
def daily(store, monkeypatch):
    m = _load()                  # a fresh module: the `wheel` fixture's patches do not apply
    monkeypatch.setattr(m, "store", store)
    monkeypatch.setattr(signals_store, "store", store)
    monkeypatch.setattr(m.clock, "is_trading_day", lambda d: True)
    monkeypatch.setattr(m.calibration, "record_outcomes", lambda *a, **k: 0)
    monkeypatch.setattr(m.market_data, "data_client", lambda k, s: object())
    monkeypatch.setattr(m.StrategyWheel, "_weekly", lambda self, *a: [])
    # The IV snapshot walks the whole wheel universe over the network; its
    # own two tests put it back.
    monkeypatch.setattr(m.iv, "run_iv_snapshot", lambda store, **kw: {"complete": True})
    sent = []
    monkeypatch.setattr(m.notify, "send",
                        lambda cat, iid, title, msg, priority=0: sent.append(
                            (cat, title, msg, priority)))
    m.sent = sent
    clock._DEADLINES.clear()
    return m


def checks(m, adapter, cache, config=None):
    return m.StrategyWheel()._position_checks("2026-06-01", TODAY, "swing-paper",
                                              config or cfg(), adapter, cache)


def test_the_monday_scan_buys_back_a_put_at_twice_its_premium(wheel):
    # ST check_open_wheel_positions: collected $100, $250 to close -> 2.5x.
    xyz = short("XYZ", 40.0, expiry="2026-06-05", entry=1.0, value=-250.0)
    cache = {}
    out = tick(wheel, MON_1040, WheelAdapter(option_positions=[xyz]), cache)
    # M2: the buy-back goes out alone; the scan starts on the next tick.
    assert [(o["contract"], o["reason"]) for o in orders(out)] == [(xyz.symbol, "wheel_btc_2x")]
    btc = orders(out)[0]
    assert (btc["position_intent"], btc["order_type"], btc["limit_price"], btc["qty"],
            btc["session"]) == ("buy_to_close", "market", None, 1, "2026-06-01")
    assert wheel.sent[0] == ("wheel_position_alert", "Auto-close ordered: XYZ")
    assert wheel.ai_analyst.score_candidate.calls == [] and wheel._SCAN_KEY not in cache
    later = tick(wheel, MON_1100, WheelAdapter(option_positions=[xyz]), cache)
    assert [(o["contract"], o["reason"]) for o in orders(later)] == [
        (occ("APH", 127.0), "wheel_sto_put")]


def test_a_put_with_a_working_buy_is_not_bought_back_twice(daily):
    xyz = short("XYZ", 40.0, entry=1.0, value=-250.0)
    buying = WheelAdapter(option_positions=[xyz],
                          open_orders=[NS(symbol=xyz.symbol, side="buy", qty=1, filled_qty=0)])
    assert checks(daily, buying, {}) == []


def test_assigned_shares_get_a_dry_run_covered_call_notice(daily):
    adapter = WheelAdapter(equities={"KO": (100, 68.0), "PEP": (300, 150.0)})
    cache = {"_engine_wheel_assignments": [assignment("KO")]}
    assert checks(daily, adapter, cache) == []
    (cat, title, msg, priority), = daily.sent
    # Ruling 2 (A-live F12): the engine's poller sends the only wheel_assignment
    # notification; the lane's dry run is a position alert.
    assert (cat, title, priority) == (
        "wheel_position_alert", "🔍 Covered-call candidate: KO (dry-run)", 1)
    assert "Would sell 1 covered call(s)" in msg and "$71.40" in msg and EXPIRY in msg
    # Ruling 4 (G3 minor 7): no promise that a flag places the call.
    assert "live call orders are not enabled in this port" in msg
    assert "To enable" not in msg and "auto_covered_call=true" not in msg
    # PEP was never assigned to this lane: ST would have written calls on it.


def test_auto_covered_call_is_refused_at_the_lane(daily):
    adapter = WheelAdapter(equities={"KO": (200, 68.0)})
    cache = {"_engine_wheel_assignments": [assignment("KO", 200)]}
    assert checks(daily, adapter, cache, config=cfg(auto_covered_call=True)) == []
    (_cat, _title, msg, _priority), = daily.sent
    assert "Would sell 2 covered call(s)" in msg
    assert "sell-to-open puts only" in msg and "no call was sent" in msg
    assert "To enable" not in msg


def test_a_call_assignment_or_a_covering_call_ends_the_notices(daily):
    adapter = WheelAdapter(equities={"KO": (100, 68.0)})
    netted = {"_engine_wheel_assignments": [assignment("KO"), assignment("KO", side="sell")]}
    assert checks(daily, adapter, netted) == [] and daily.sent == []
    covered = WheelAdapter(equities={"KO": (100, 68.0)},
                           option_positions=[short("KO", 72.0, kind="C")])
    assert checks(daily, covered, {"_engine_wheel_assignments": [assignment("KO")]}) == []
    assert daily.sent == []


# -- the daily monitor -----------------------------------------------------------

PRICES = {"AAA": 88.0, "BBB": 49.0, "CCC": 31.0}


def monitor_book():
    return [short("AAA", 100.0),                                   # 12% ITM -> close
            short("BBB", 50.0),                                    # 2% ITM, 11 DTE -> alert
            short("CCC", 30.0, expiry="2026-06-01"),               # OTM, expires today
            short("DDD", 20.0, kind="C")]                          # a call: not the wheel's (fix 6)


def test_the_monitor_buys_back_a_deep_itm_put_once_a_session(daily, monkeypatch):
    seen = []
    monkeypatch.setattr(daily.market_data, "live_prices",
                        lambda syms, adapter=None, client=None, now=None:
                        seen.append(sorted(syms)) or {s: PRICES[s] for s in syms if s in PRICES})
    cache = {}
    out = tick(daily, MON_1540, WheelAdapter(option_positions=monitor_book()), cache)
    assert [(o["contract"], o["reason"], o["position_intent"], o["order_type"], o["qty"])
            for o in orders(out)] == [
        (occ("AAA", 100.0), "wheel_btc_itm", "buy_to_close", "market", 1)]
    assert seen == [["AAA", "BBB", "CCC"]]
    assert [(c, t, p) for c, t, _m, p in daily.sent] == [
        ("wheel_position_alert", "Auto-close ordered: AAA", 2),
        ("wheel_position_alert", "⚠️ ITM PUT: BBB", 1),
        ("wheel_position_alert", "⏰ EXPIRING TODAY OTM: CCC", 0)]
    assert cache[daily._MONITOR_KEY] == "2026-06-01"
    assert tick(daily, datetime(2026, 6, 1, 19, 50, tzinfo=timezone.utc),
                WheelAdapter(option_positions=monitor_book()), cache) == {}


def test_the_monitor_waits_for_its_tick_and_skips_a_working_buy(daily, monkeypatch):
    monkeypatch.setattr(daily.market_data, "live_prices",
                        lambda syms, adapter=None, client=None, now=None:
                        {s: PRICES[s] for s in syms if s in PRICES})
    assert tick(daily, MON_1520, WheelAdapter(option_positions=monitor_book()), {}) == {}
    assert daily.sent == []
    working = WheelAdapter(option_positions=monitor_book(),
                           open_orders=[NS(symbol=occ("AAA", 100.0), side="buy", qty=1,
                                           filled_qty=0)])
    assert tick(daily, MON_1540, working, {}) == {}


def test_an_unreadable_book_on_the_last_tick_alerts_once_and_never_latches(
        daily, monkeypatch):
    # I-2: 15:40 is the monitor's only grid tick (16:00 is the close). The
    # failure is alerted there, once; the 16:00 tick adds nothing.
    monkeypatch.setattr(daily.market_data, "live_prices",
                        lambda syms, adapter=None, client=None, now=None:
                        {s: PRICES[s] for s in syms if s in PRICES})

    class Down(WheelAdapter):
        def list_option_positions(self):
            raise RuntimeError("positions endpoint down")

    cache = {}
    assert tick(daily, MON_1540, Down(), cache) == {}
    assert daily._MONITOR_KEY not in cache
    assert [(c, t, p) for c, t, _m, p in daily.sent] == [
        ("wheel_position_alert", "⚠️ Wheel monitor incomplete — check puts manually", 1)]
    assert "positions endpoint down" in daily.sent[0][2]
    close = datetime(2026, 6, 1, 20, 0, tzinfo=timezone.utc)
    assert tick(daily, close, WheelAdapter(option_positions=monitor_book()), cache) == {}
    assert len(daily.sent) == 1 and daily._MONITOR_KEY not in cache


# -- the IV snapshot ---------------------------------------------------------------

def test_the_iv_snapshot_runs_after_0915_and_latches_when_complete(daily, monkeypatch):
    calls = []
    results = iter([False, True])

    def run(store, *, adapter, spot_for, today, symbols=None, deadline=None, **kw):
        calls.append(today)
        return {"date": str(today), "recorded": [], "skipped": [], "failed": [],
                "complete": next(results)}

    monkeypatch.setattr(daily.iv, "run_iv_snapshot", run)
    cache = {}
    tick(daily, MON_0900, WheelAdapter(), cache)
    assert calls == []
    tick(daily, MON_0920, WheelAdapter(), cache)
    assert calls == [TODAY] and daily._IV_KEY not in cache       # budget ran out
    tick(daily, MON_0940, WheelAdapter(), cache)
    assert cache[daily._IV_KEY] == "2026-06-01"
    tick(daily, MON_1040, WheelAdapter(), cache)
    assert calls == [TODAY, TODAY]


def test_the_iv_snapshot_writes_rows_through_the_store(daily, monkeypatch, store):
    monkeypatch.setattr(daily.iv, "run_iv_snapshot", _REAL_RUN_IV)
    monkeypatch.setattr(daily.iv, "snapshot_iv", lambda symbol, today=None: 0.31)
    monkeypatch.setattr(daily.iv, "snapshot_iv_alpaca",
                        lambda symbol, *, adapter, spot, today: None)
    monkeypatch.setattr(daily.market_data, "get_latest_price", lambda s, client: 100.0)
    monkeypatch.setattr(daily.iv, "WHEEL_UNIVERSE", ["APH", "KO"])
    cache = {}
    tick(daily, MON_0920, WheelAdapter(), cache)
    assert store.get("SwingIvSnapshots", "KO|2026-06-01") == {
        "id": "KO|2026-06-01", "symbol": "KO", "date": "2026-06-01",
        "iv30": 0.31, "iv30_alpaca": None}
    assert cache[daily._IV_KEY] == "2026-06-01"


# -- controller rulings (G7, Task 19) ----------------------------------------------

def test_ruling_2_the_lane_never_sends_the_assignment_notification(wheel, monkeypatch):
    engine_notices = []
    monkeypatch.setattr(wheel.notify, "notify_wheel_assignment",
                        lambda *a, **k: engine_notices.append((a, k)))
    cache = {"_engine_wheel_assignments": [assignment("KO"), assignment("PEP", 200)]}
    tick(wheel, MON_1040, WheelAdapter(equities={"KO": (100, 68.0), "PEP": (200, 150.0)}),
         cache)
    assert "wheel_assignment" not in [c for c, _ in wheel.sent] and engine_notices == []
    assert [t for c, t in wheel.sent if c == "wheel_position_alert"] == [
        "🔍 Covered-call candidate: KO (dry-run)", "🔍 Covered-call candidate: PEP (dry-run)"]


def test_ruling_3_a_put_with_no_price_near_expiry_gets_one_check_manually_alert(
        daily, monkeypatch):
    monkeypatch.setattr(daily.market_data, "live_prices",
                        lambda syms, adapter=None, client=None, now=None: {"III": 0.0})
    book = [short("FFF", 30.0, expiry="2026-06-01"),               # no price, expires today
            short("GGG", 30.0, expiry="2026-06-02"),               # no price, 1 DTE
            short("HHH", 30.0),                                    # no price, 11 DTE: logged
            short("III", 30.0, expiry="2026-06-01")]               # a $0 print is no price
    cache = {}
    assert tick(daily, MON_1540, WheelAdapter(option_positions=book), cache) == {}
    alerts = [(c, t, m, p) for c, t, m, p in daily.sent]
    assert [(c, t, p) for c, t, _m, p in alerts] == [
        ("wheel_position_alert", "❓ No price — check manually: FFF", 2),
        ("wheel_position_alert", "❓ No price — check manually: GGG", 1),
        ("wheel_position_alert", "❓ No price — check manually: III", 2)]
    assert occ("GGG", 30.0, expiry="2026-06-02") in alerts[1][2]
    assert "check it manually" in alerts[1][2].lower()
    # Once per contract per session: a second evaluation the same session
    # (the monitor re-run after an error) does not repeat them.
    cache.pop(daily._MONITOR_KEY)
    daily.StrategyWheel()._monitor(MON_1540, "2026-06-01", TODAY, "swing-paper", cfg(),
                                   WheelAdapter(option_positions=book), cache, None)
    assert len(daily.sent) == 3
    # The next session is a new session.
    daily.StrategyWheel()._monitor(datetime(2026, 6, 2, 19, 40, tzinfo=timezone.utc),
                                   "2026-06-02", date(2026, 6, 2), "swing-paper", cfg(),
                                   WheelAdapter(option_positions=book[1:2]), cache, None)
    assert [t for _c, t, _m, _p in daily.sent[3:]] == ["❓ No price — check manually: GGG"]


def test_ruling_5_the_monitor_checks_short_puts_only(daily, monkeypatch):
    seen, lines = [], []
    monkeypatch.setattr(daily, "_log", lambda msg, color="white": lines.append(msg))
    monkeypatch.setattr(daily.market_data, "live_prices",
                        lambda syms, adapter=None, client=None, now=None:
                        seen.append(sorted(syms)) or {"DDD": 10.0, "EEE": 10.0})
    call = short("DDD", 20.0, kind="C")         # read as a put it would be 50% ITM
    long_put = short("EEE", 20.0, qty=1)        # long: not a short put either
    cache = {}
    assert tick(daily, MON_1540, WheelAdapter(option_positions=[call, long_put]), cache) == {}
    assert seen == [] and daily.sent == []
    assert any(call.symbol in line and "not a wheel put" in line for line in lines)
    assert cache[daily._MONITOR_KEY] == "2026-06-01"


@pytest.mark.parametrize("bad", ["3:45pm", "24:00", "", "08:45", "16:00", "16:30"])
def test_ruling_8_an_unparseable_monitor_time_refuses_the_monitor(daily, monkeypatch, bad):
    lines = []
    monkeypatch.setattr(daily, "_log", lambda msg, color="white": lines.append(msg))
    monkeypatch.setattr(daily.market_data, "live_prices",
                        lambda syms, adapter=None, client=None, now=None:
                        {s: PRICES[s] for s in syms if s in PRICES})
    cache = {}
    for at in (MON_1040, MON_1540):             # a 00:00 fallback would run at 10:40
        out = tick(daily, at, WheelAdapter(option_positions=monitor_book()), cache,
                   config=cfg(monitor_time_et=bad))
        assert orders(out) == []
    assert daily._MONITOR_KEY not in cache and daily.sent == []
    assert sum("monitor_time_et" in line and "REFUSING" in line for line in lines) == 1


def test_the_position_checks_run_once_a_session_and_retry_an_unreadable_book(
        wheel, monkeypatch):
    def no_client(key, secret):
        raise RuntimeError("Alpaca data credentials missing")

    monkeypatch.setattr(wheel.market_data, "data_client", no_client)   # prepare fails

    class Down(WheelAdapter):
        def list_option_positions(self):
            raise RuntimeError("positions endpoint down")

    cache = {"_engine_wheel_assignments": [assignment("KO")]}
    held = {"KO": (100, 68.0)}
    assert tick(wheel, MON_1040, Down(equities=held), cache) == {}
    assert wheel.sent == [] and wheel._CHECKS_KEY not in cache
    for at in (MON_1100, datetime(2026, 6, 1, 15, 20, tzinfo=timezone.utc)):
        tick(wheel, at, WheelAdapter(equities=held), cache)
    assert [c for c, _ in wheel.sent] == ["wheel_position_alert"]
    assert cache[wheel._CHECKS_KEY] == "2026-06-01"


def test_one_tick_never_emits_two_buy_to_closes_for_one_contract(daily, monkeypatch):
    # The monitor and the scan's 2x check both want AAA closed in one tick
    # (the position checks had not run yet that session): one order goes.
    aaa = short("AAA", 100.0, entry=1.0, value=-1300.0)
    itm = daily.wheel_rules.btc_order(aaa, 1, "wheel_btc_itm", session="2026-06-01")
    two_x = daily.wheel_rules.btc_order(aaa, 1, "wheel_btc_2x", session="2026-06-01")
    sto = {"contract": occ("APH", 127.0), "position_intent": "sell_to_open",
           "reason": "wheel_sto_put"}
    monkeypatch.setattr(daily.StrategyWheel, "_monitor", lambda self, *a: [dict(itm)])
    monkeypatch.setattr(daily.StrategyWheel, "_weekly", lambda self, *a: [dict(two_x), sto])
    out = tick(daily, MON_1540, WheelAdapter(option_positions=[aaa]), {})
    assert [(o["contract"], o["reason"]) for o in orders(out)] == [
        (aaa.symbol, "wheel_btc_itm"), (occ("APH", 127.0), "wheel_sto_put")]


# -- fix round 1 (G7 review): I-1 incomplete maps, I-2 the monitor's window, --
# -- I-3 intent wording, M1-M5 -------------------------------------------------

FRI = "2026-06-05"
FRI_1540 = datetime(2026, 6, 5, 19, 40, tzinfo=timezone.utc)
FRI_1600 = datetime(2026, 6, 5, 20, 0, tzinfo=timezone.utc)
HALF_DAY = "2026-11-27"                         # NYSE closes 13:00 ET (18:00 UTC)


def _prices(m, monkeypatch, table):
    monkeypatch.setattr(m.market_data, "live_prices",
                        lambda syms, adapter=None, client=None, now=None:
                        {s: table[s] for s in syms if s in table})


def meta_miss(underlying, strike, expiry, qty=-1):
    """A-live alpaca.py _collect_option_position when option_contract_meta
    misses: the signed quantity survives, the contract fields are empty."""
    return NS(symbol=occ(underlying, strike, expiry=expiry), underlying="", option_type="",
              strike=0.0, expiry="", qty=qty, avg_entry_price=1.0, market_value=-2000.0,
              current_price=None)


def incomplete(adapter, *, stale=False):
    """AlpacaAdapter's private health flags; list_option_positions still
    returns rows without raising."""
    adapter._option_positions_complete = False
    if stale:
        adapter._positions_stale_since = 1.0
    return adapter


def titles(m):
    return [(c, t, p) for c, t, _m, p in m.sent]


def test_i1_probe_a_meta_miss_short_put_is_alerted_never_skipped(daily, monkeypatch):
    # Probe 1: an expiry-Friday put 20% in the money whose contract fields
    # are missing, on a map flagged incomplete.
    lines = []
    monkeypatch.setattr(daily, "_log", lambda msg, color="white": lines.append(msg))
    _prices(daily, monkeypatch, {"AAA": 80.0})
    miss = meta_miss("AAA", 100.0, FRI)
    cache = {}
    out = tick(daily, FRI_1540, incomplete(WheelAdapter(option_positions=[miss])), cache)
    assert orders(out) == []
    assert not any("not a wheel put" in line for line in lines)
    assert titles(daily) == [
        ("wheel_position_alert", f"❓ Unreadable option — check manually: {miss.symbol}", 2),
        ("wheel_position_alert", "⚠️ Wheel monitor incomplete — check puts manually", 2)]
    assert "put $100.00 on AAA, expiring 2026-06-05 (0 day(s))" in daily.sent[0][2]
    assert daily._MONITOR_KEY not in cache


def test_i1_probe_an_emptied_incomplete_map_is_not_read_as_no_puts(daily, monkeypatch):
    # Probe 2: the cache Alpaca clears after a long outage.
    lines = []
    monkeypatch.setattr(daily, "_log", lambda msg, color="white": lines.append(msg))
    cache = {}
    tick(daily, FRI_1540, incomplete(WheelAdapter(option_positions=[]), stale=True), cache)
    assert not any("checked 0 short put positions" in line for line in lines)
    assert daily._MONITOR_KEY not in cache
    assert titles(daily) == [
        ("wheel_position_alert", "⚠️ Wheel monitor incomplete — check puts manually", 1)]
    assert "incomplete" in daily.sent[0][2]


def test_i1_probe_an_unreadable_book_at_1540_is_alerted_not_silently_dropped(
        daily, monkeypatch):
    # Probe 3: nothing retries it in regular hours, so the 15:40 tick alerts.
    _prices(daily, monkeypatch, {"AAA": 80.0})

    class Down(WheelAdapter):
        def list_option_positions(self):
            raise RuntimeError("down")

    cache = {}
    tick(daily, FRI_1540, Down(), cache)
    out = tick(daily, FRI_1600, WheelAdapter(option_positions=[short("AAA", 100.0, expiry=FRI)]),
               cache)
    assert orders(out) == [] and daily._MONITOR_KEY not in cache
    assert titles(daily) == [
        ("wheel_position_alert", "⚠️ Wheel monitor incomplete — check puts manually", 1)]


def test_i1_the_monitor_closes_what_it_can_read_on_an_incomplete_map(daily, monkeypatch):
    _prices(daily, monkeypatch, PRICES)
    book = [short("AAA", 100.0), meta_miss("ZZZ", 50.0, "2026-06-12")]
    cache = {}
    out = tick(daily, MON_1540, incomplete(WheelAdapter(option_positions=book)), cache)
    assert [(o["contract"], o["reason"]) for o in orders(out)] == [
        (occ("AAA", 100.0), "wheel_btc_itm")]
    assert [t for _c, t, _p in titles(daily)] == [
        f"❓ Unreadable option — check manually: {book[1].symbol}",
        "Auto-close ordered: AAA",
        "⚠️ Wheel monitor incomplete — check puts manually"]
    assert daily._MONITOR_KEY not in cache


def test_i1_a_short_row_without_a_type_on_a_complete_map_is_still_alerted(daily, monkeypatch):
    lines = []
    monkeypatch.setattr(daily, "_log", lambda msg, color="white": lines.append(msg))
    _prices(daily, monkeypatch, PRICES)
    cache = {}
    tick(daily, MON_1540, WheelAdapter(option_positions=[meta_miss("ZZZ", 50.0, EXPIRY)]),
         cache)
    assert [t for _c, t, _p in titles(daily)][0].startswith("❓ Unreadable option")
    assert not any("not a wheel put" in line for line in lines)
    assert daily._MONITOR_KEY not in cache


@pytest.mark.parametrize("stale", [False, True])
def test_i1_the_scan_and_the_checks_treat_an_unready_map_as_not_ready(wheel, stale):
    xyz = short("XYZ", 40.0, expiry="2026-06-05", entry=1.0, value=-250.0)
    unready = WheelAdapter(option_positions=[xyz])
    if stale:
        unready._positions_stale_since = 1.0
    else:
        unready._option_positions_complete = False
    cache = {}
    assert orders(tick(wheel, MON_1040, unready, cache)) == []      # no 2x, no put
    assert wheel._CHECKS_KEY not in cache
    assert cache[wheel._SCAN_KEY]["phase"] == "scoring" and cache[wheel._SCAN_KEY]["cursor"] == 0
    assert signals_store.all_signals("swing-paper") == []
    ready = tick(wheel, MON_1100, WheelAdapter(option_positions=[xyz]), cache)
    assert [o["reason"] for o in orders(ready)] == ["wheel_btc_2x"]
    assert cache[wheel._CHECKS_KEY] == "2026-06-01"


def test_i2_a_half_day_monitor_runs_at_1240(daily, monkeypatch):
    _prices(daily, monkeypatch, {"AAA": 80.0})
    book = [short("AAA", 100.0, expiry=HALF_DAY)]
    cache = {}
    at = lambda h, m: datetime(2026, 11, 27, h, m, tzinfo=timezone.utc)  # noqa: E731
    assert tick(daily, at(17, 20), WheelAdapter(option_positions=book), cache) == {}   # 12:20
    out = tick(daily, at(17, 40), WheelAdapter(option_positions=book), cache)          # 12:40
    assert [(o["contract"], o["reason"]) for o in orders(out)] == [
        (occ("AAA", 100.0, expiry=HALF_DAY), "wheel_btc_itm")]
    assert cache[daily._MONITOR_KEY] == HALF_DAY
    assert tick(daily, at(20, 40), WheelAdapter(option_positions=book), cache) == {}   # 15:40


def test_i2_a_half_day_failure_is_urgent_when_a_known_put_expires(daily, monkeypatch):
    # Wednesday's monitor saw the put that expires on the half day.
    _prices(daily, monkeypatch, {"AAA": 120.0})
    cache = {}
    book = [short("AAA", 100.0, expiry=HALF_DAY)]
    tick(daily, datetime(2026, 11, 25, 20, 40, tzinfo=timezone.utc),
         WheelAdapter(option_positions=book), cache)
    assert cache[daily._MONITOR_KEY] == "2026-11-25" and daily.sent == []

    class Down(WheelAdapter):
        def list_option_positions(self):
            raise RuntimeError("down")

    tick(daily, datetime(2026, 11, 27, 17, 40, tzinfo=timezone.utc), Down(), cache)
    assert titles(daily) == [
        ("wheel_position_alert", "⚠️ Wheel monitor incomplete — check puts manually", 2)]
    assert occ("AAA", 100.0, expiry=HALF_DAY) in daily.sent[0][2]


def test_i2_an_exception_on_the_last_tick_is_alerted(daily, monkeypatch):
    def boom(syms, adapter=None, client=None, now=None):
        raise RuntimeError("quote feed exploded")

    monkeypatch.setattr(daily.market_data, "live_prices", boom)
    cache = {}
    assert tick(daily, MON_1540, WheelAdapter(option_positions=monitor_book()), cache) == {}
    assert daily._MONITOR_KEY not in cache
    # Urgent: the book read before the failure shows CCC expiring today.
    assert titles(daily) == [
        ("wheel_position_alert", "⚠️ Wheel monitor incomplete — check puts manually", 2)]
    assert "quote feed exploded" in daily.sent[0][2]


def test_i2_an_earlier_window_retries_on_its_grid_without_alerting(daily, monkeypatch):
    _prices(daily, monkeypatch, PRICES)

    class Down(WheelAdapter):
        def list_option_positions(self):
            raise RuntimeError("down")

    cache, early = {}, cfg(monitor_time_et="15:00")
    tick(daily, datetime(2026, 6, 1, 18, 40, tzinfo=timezone.utc), Down(), cache, config=early)
    assert daily.sent == []                                         # 14:40: not yet
    tick(daily, datetime(2026, 6, 1, 19, 0, tzinfo=timezone.utc), Down(), cache, config=early)
    assert daily.sent == [] and daily._MONITOR_KEY not in cache    # 15:00 fails; 15:20 next
    out = tick(daily, datetime(2026, 6, 1, 19, 20, tzinfo=timezone.utc),
               WheelAdapter(option_positions=monitor_book()), cache, config=early)
    assert [o["reason"] for o in orders(out)] == ["wheel_btc_itm"]
    assert cache[daily._MONITOR_KEY] == "2026-06-01"
    assert "⚠️ Wheel monitor incomplete — check puts manually" not in [
        t for _c, t, _p in titles(daily)]


def test_i2_a_missed_window_is_alerted_once_after_the_close(daily, monkeypatch):
    cache = {daily._KNOWN_PUTS_KEY: {occ("AAA", 100.0, expiry="2026-06-02"): "2026-06-02"}}
    after = datetime(2026, 6, 1, 20, 20, tzinfo=timezone.utc)                    # 16:20
    tick(daily, after, WheelAdapter(option_positions=[short("AAA", 100.0, expiry="2026-06-02")]),
         cache)
    tick(daily, datetime(2026, 6, 1, 20, 40, tzinfo=timezone.utc),
         WheelAdapter(option_positions=[short("AAA", 100.0, expiry="2026-06-02")]), cache)
    assert titles(daily) == [
        ("wheel_position_alert", "⚠️ Wheel monitor incomplete — check puts manually", 2)]
    assert "no tick completed the daily monitor" in daily.sent[0][2]


def test_i2_a_missed_window_with_nothing_held_is_quiet(daily):
    cache = {}
    tick(daily, datetime(2026, 6, 1, 20, 20, tzinfo=timezone.utc), WheelAdapter(), cache)
    assert daily.sent == [] and cache[daily._MONITOR_KEY] == "2026-06-01"


def test_i3_pushes_describe_intent_not_outcome(store, monkeypatch, wheel):
    msgs = []
    monkeypatch.setattr(wheel.notify, "send",
                        lambda cat, iid, title, msg, priority=0: msgs.append((cat, title, msg)))
    xyz = short("XYZ", 40.0, expiry="2026-06-05", entry=1.0, value=-250.0)
    cache = {}
    tick(wheel, MON_1040, WheelAdapter(option_positions=[xyz]), cache)
    tick(wheel, MON_1100, WheelAdapter(option_positions=[xyz]), cache)
    by_title = {t: (c, m) for c, t, m in msgs}
    assert by_title["Auto-close ordered: XYZ"][0] == "wheel_position_alert"
    assert (f"Auto-close ordered: {xyz.symbol} — buy-to-close handed to the order gate; "
            "a refusal is alerted separately") in by_title["Auto-close ordered: XYZ"][1]
    assert by_title["Put order submitted: APH"][0] == "wheel_put_placed"
    assert "a refusal is alerted separately" in by_title["Put order submitted: APH"][1]
    text = " ".join(t + " " + m for _c, t, m in msgs)
    for claim in ("Put Sold", "PUT ORDER SENT", "BUY-TO-CLOSE placed", "Auto-Closed",
                  "was sent"):
        assert claim not in text


def test_m1_a_put_whose_build_raises_is_recorded_failed_and_pushed(wheel, monkeypatch):
    def broken(*a, **k):
        raise RuntimeError("chain endpoint 500")

    monkeypatch.setattr(wheel.wheel_rules, "build_put_order_live", broken)
    out = tick(wheel, MON_1040, WheelAdapter(), {})
    assert orders(out) == []
    sig = signals_store.get_signal(signals_store.signal_id_for(
        "swing-paper", "wheel", "2026-06-01", "APH"))
    assert sig["status"] == "failed" and "chain endpoint 500" in sig["error"]
    scan = {r["symbol"]: r for r in signals_store.list_wheel_scans("swing-paper")}["APH"]
    assert scan["status"] == "skipped" and "chain endpoint 500" in scan["skip_reason"]
    assert ("wheel_position_alert", "Wheel order failed — place manually: APH") in wheel.sent
    assert wheel.ai_analyst.score_candidate.calls == ["APH", "GIS", "KO"]


def test_m3_an_order_that_cannot_be_built_pushes_a_position_alert(wheel):
    # G8a ruling 3(f): a routine sizing skip (cash, the collateral cap) is a
    # run-summary line instead; any other build failure is still a push.
    class NoChain(WheelAdapter):
        def get_option_contracts(self, underlying, **kw):
            return []

    out = tick(wheel, MON_1040, NoChain(), {})
    assert orders(out) == []
    assert ("wheel_position_alert", "Wheel order failed — place manually: APH") in wheel.sent
    assert not [t for c, t in wheel.sent if c == "swing_run_summary" and "Failed" in t]


def test_m4_an_unreadable_account_is_not_ready_not_a_burned_candidate(wheel):
    class NoAccount(WheelAdapter):
        def get_account_options(self):
            raise RuntimeError("account endpoint down")

        def get_cash(self):
            raise RuntimeError("account endpoint down")

    cache = {}
    assert orders(tick(wheel, MON_1040, NoAccount(), cache)) == []
    assert cache[wheel._SCAN_KEY]["cursor"] == 0 and signals_store.all_signals("swing-paper") == []
    assert wheel.ai_analyst.score_candidate.calls == []
    out = tick(wheel, MON_1100, WheelAdapter(), cache)
    assert [o["underlying"] for o in orders(out)] == ["APH"]


def test_m4_a_real_zero_cash_read_is_still_a_read(wheel):
    class Broke(WheelAdapter):
        def get_account_options(self):
            return {"cash": 0.0, "equity": 100_000.0}

    tick(wheel, MON_1040, Broke(), {})
    scan = {r["symbol"]: r for r in signals_store.list_wheel_scans("swing-paper")}["APH"]
    assert scan["status"] == "skipped" and "Insufficient cash" in scan["skip_reason"]


# -- G8a ruling 3 (G7 carries c-f): per-row isolation, the monitor latch, -----
# -- push dedupe and routine sizing skips ----------------------------------------

def _capture(m, monkeypatch):
    sent = []
    monkeypatch.setattr(m.notify, "send", lambda cat, iid, title, msg, priority=0:
                        sent.append((cat, title, msg, priority)))
    return sent


def test_c_an_unreadable_qty_skips_that_row_and_never_raises_out_of_the_scan(
        wheel, monkeypatch):
    lines = []
    monkeypatch.setattr(wheel, "_log", lambda msg, color="white": lines.append(msg))
    bad = short("BAD", 40.0, expiry="2026-06-05", entry=1.0, value=-250.0)
    bad.qty = "n/a"
    xyz = short("XYZ", 40.0, expiry="2026-06-05", entry=1.0, value=-250.0)
    cache = {}
    out = tick(wheel, MON_1040, WheelAdapter(option_positions=[bad, xyz]), cache)
    assert [(o["contract"], o["reason"]) for o in orders(out)] == [(xyz.symbol, "wheel_btc_2x")]
    assert cache[wheel._CHECKS_KEY] == "2026-06-01"
    assert not any("weekly failed" in line for line in lines)
    assert any(bad.symbol in line and "unreadable qty" in line for line in lines)


def _flaky_decisions(m, monkeypatch, broken):
    real = m.wheel_rules.put_monitor_decision

    def decide(**kw):
        if kw["underlying"] in broken:
            raise RuntimeError(f"bad contract data for {kw['underlying']}")
        return real(**kw)

    monkeypatch.setattr(m.wheel_rules, "put_monitor_decision", decide)


def test_d_a_put_that_raises_keeps_the_monitor_unlatched_and_is_alerted(daily, monkeypatch):
    _prices(daily, monkeypatch, dict(PRICES, EEE=10.0))
    _flaky_decisions(daily, monkeypatch, {"EEE"})
    book = monitor_book() + [short("EEE", 20.0)]
    cache = {}
    out = tick(daily, MON_1540, WheelAdapter(option_positions=book), cache)
    # The readable puts are still handled...
    assert [(o["contract"], o["reason"]) for o in orders(out)] == [
        (occ("AAA", 100.0), "wheel_btc_itm")]
    # ...but the monitor is not "Done": 15:40 is its last tick, so it alerts.
    assert daily._MONITOR_KEY not in cache
    incomplete = [(t, m, p) for c, t, m, p in daily.sent
                  if t == "⚠️ Wheel monitor incomplete — check puts manually"]
    assert len(incomplete) == 1 and "EEE" in incomplete[0][1]


def test_d_a_put_that_raised_is_retried_on_the_next_grid_tick(daily, monkeypatch):
    _prices(daily, monkeypatch, PRICES)
    broken = {"BBB"}
    _flaky_decisions(daily, monkeypatch, broken)
    cache, early = {}, cfg(monitor_time_et="15:00")
    tick(daily, datetime(2026, 6, 1, 19, 0, tzinfo=timezone.utc),
         WheelAdapter(option_positions=monitor_book()), cache, config=early)
    assert daily._MONITOR_KEY not in cache
    broken.clear()
    working = WheelAdapter(option_positions=monitor_book(),
                           open_orders=[NS(symbol=occ("AAA", 100.0), side="buy", qty=1,
                                           filled_qty=0)])
    tick(daily, datetime(2026, 6, 1, 19, 20, tzinfo=timezone.utc), working, cache,
         config=early)
    assert cache[daily._MONITOR_KEY] == "2026-06-01"
    assert "⚠️ Wheel monitor incomplete — check puts manually" not in [
        t for _c, t, _p in titles(daily)]
    assert "⚠️ ITM PUT: BBB" in [t for _c, t, _p in titles(daily)]


def test_e_informational_pushes_are_not_repeated_on_retry_ticks(daily, monkeypatch):
    table = {"BBB": 49.0, "CCC": 31.0, "GGG": 52.0}
    _prices(daily, monkeypatch, table)
    _flaky_decisions(daily, monkeypatch, {"EEE"})     # keeps the monitor retrying
    book = [short("BBB", 50.0),                           # 2% ITM, 11 DTE: alert
            short("CCC", 30.0, expiry="2026-06-01"),      # OTM, expires today: info
            short("GGG", 50.0, expiry="2026-06-02"),      # OTM, expires tomorrow: info
            short("EEE", 20.0)]
    cache, early = {}, cfg(monitor_time_et="15:00")
    at = lambda h, m: datetime(2026, 6, 1, h, m, tzinfo=timezone.utc)  # noqa: E731
    tick(daily, at(19, 0), WheelAdapter(option_positions=book), cache, config=early)
    first = [t for _c, t, _p in titles(daily)]
    assert first == ["⚠️ ITM PUT: BBB", "⏰ EXPIRING TODAY OTM: CCC",
                     "⏰ EXPIRING TOMORROW OTM: GGG"]
    # A retry tick with nothing new: no repeats.
    tick(daily, at(19, 20), WheelAdapter(option_positions=book), cache, config=early)
    assert [t for _c, t, _p in titles(daily)] == first
    # GGG slips into the money: a new state for that contract is still told.
    table["GGG"] = 49.0
    tick(daily, at(19, 40), WheelAdapter(option_positions=book), cache, config=early)
    later = [t for _c, t, _p in titles(daily)][len(first):]
    assert later == ["⚠️ ITM PUT: GGG",
                     "⚠️ Wheel monitor incomplete — check puts manually"]
    # The next session starts over.
    daily.StrategyWheel()._monitor(datetime(2026, 6, 2, 19, 0, tzinfo=timezone.utc),
                                   "2026-06-02", date(2026, 6, 2), "swing-paper", early,
                                   WheelAdapter(option_positions=book[:1]), cache, None)
    assert [t for _c, t, _p in titles(daily)][-1] == "⚠️ ITM PUT: BBB"


@pytest.mark.parametrize("adapter,needle", [
    (WheelAdapter(cash=1_000.0), "Insufficient cash"),
    (WheelAdapter(equity=10_000.0), "Collateral cap"),
])
def test_f_a_routine_sizing_skip_goes_to_the_run_summary_not_a_push(
        wheel, monkeypatch, adapter, needle):
    sent = _capture(wheel, monkeypatch)
    assert orders(tick(wheel, MON_1040, adapter, {})) == []
    skips = [(c, t, m, p) for c, t, m, p in sent if t == "Wheel put skipped: APH"]
    assert len(skips) == 1
    cat, _t, msg, priority = skips[0]
    assert (cat, priority) == ("swing_run_summary", 0) and needle in msg
    assert not [t for c, t, _m, _p in sent if c == "wheel_position_alert"]
    sig = signals_store.get_signal(signals_store.signal_id_for(
        "swing-paper", "wheel", "2026-06-01", "APH"))
    assert sig["status"] == "failed" and needle in sig["error"]
