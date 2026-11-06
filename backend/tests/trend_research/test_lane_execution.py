"""TR lane execution rules (final fix wave B, sweep-lane #1-#6, final I5/I7).

Two drivers:

* ``run_engine`` -- the sweep's end-to-end harness, compacted: the REAL
  ``PortfolioEmulator`` (its cost model, the $1 order minimum, T+1 settlement
  of 5% of sell proceeds), 900 s ticks from 04:00 to 20:00 ET on every
  weekday (holidays too), extended-hours 15-minute bars, and orders that fill
  at the next tick only on a print newer than the order
  (``NextEventExecutionSimulator``'s rule) -- an order for a name whose bars
  stopped stays pending forever, as on the engine.
* ``drive`` -- a small fake emulator for single-rule checks; unfilled orders
  stay pending, as on the engine.
"""
from __future__ import annotations

import bisect
import contextlib
import io
import math
import os
import random
import sys
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

_backend = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from db.fake import FakeStore  # noqa: E402
from portfolio_emulator import PortfolioEmulator  # noqa: E402
from strategies import strategy_trend_research as lane  # noqa: E402
from strategies_meta import _parse_header_meta  # noqa: E402
from swing_trader import clock  # noqa: E402
from trend_research import ledger  # noqa: E402

ET = ZoneInfo("America/New_York")
CHAIN, MODEL, PV = "honest", "claude-test", "tr-v1"
CFG = {"strategy_trend_research_enabled": True, "tr_chain_id": CHAIN, "tr_model": MODEL,
       "tr_prompt_version": PV, "max_positions": 8}
NAMES = ("AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "GGG", "HHH")


def utc(d, hh, mm):
    return datetime.combine(d, time(hh, mm), tzinfo=ET).astimezone(timezone.utc)


def session_bounds(d):
    """(open, close) in ET, early closes included; None on a non-session."""
    if not clock.is_trading_day(d):
        return None
    cal = clock._calendar()
    return (cal.session_open(d.isoformat()).tz_convert(ET).to_pydatetime(),
            cal.session_close(d.isoformat()).tz_convert(ET).to_pydatetime())


def rth_ticks(d, *, start=time(9, 30), n=None):
    sb = session_bounds(d)
    if sb is None:
        return []
    t = max(sb[0], datetime.combine(d, start, tzinfo=ET))
    out = []
    while t < sb[1] and (n is None or len(out) < n):
        out.append(t.astimezone(timezone.utc))
        t += timedelta(minutes=15)
    return out


def sessions(lo, hi):
    return sorted(clock.trading_days(lo, hi))


def make_bars(symbols, lo, hi, price=None, *, ext=False, stop_after=None):
    """{sym: [15-minute bars]}: regular session, or 04:00-20:00 ET with
    ``ext``. ``price(sym, day, t_et)``; ``stop_after={sym: last day}``."""
    price = price or (lambda s, d, t: 100.0)
    stop_after = stop_after or {}
    data = {s: [] for s in symbols}
    for d in sessions(lo, hi):
        o, c = session_bounds(d)
        t = datetime.combine(d, time(4, 0), tzinfo=ET) if ext else o
        end = datetime.combine(d, time(20, 0), tzinfo=ET) if ext else c
        while t < end:
            for s in symbols:
                if s in stop_after and d > stop_after[s]:
                    continue
                data[s].append({"t": t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                "c": price(s, d, t)})
            t += timedelta(minutes=15)
    return data


def put_row(st, monday, picks, *, status="ok", notes=None):
    """picks: (symbol, weight[, conviction])."""
    as_of = ledger.week_as_of(monday).strftime("%Y-%m-%dT%H:%M:%SZ")
    row = {"id": ledger.week_id(CHAIN, monday, MODEL, PV), "chain_id": CHAIN,
           "week_start": monday.isoformat(), "as_of": as_of, "available_at": as_of,
           "model": MODEL, "provider": "claude-cli", "prompt_version": PV, "status": status,
           "notes": notes if notes is not None else
           [{"symbol": p[0], "conviction": (p[2] if len(p) > 2 else 7.0),
             "thesis_status": "intact"} for p in picks],
           "recommended": [{"symbol": p[0]} for p in picks],
           "book": {"defensive_share": 0.0,
                    "picks": [{"symbol": p[0], "target_weight": p[1],
                               "conviction": (p[2] if len(p) > 2 else 7.0), "role": "core"}
                              for p in picks]}}
    ledger.put_week(row, store=st)
    return row


@pytest.fixture
def st(monkeypatch):
    fake = FakeStore()
    monkeypatch.setattr(lane, "store", fake)
    return fake


@pytest.fixture
def logs(monkeypatch):
    lines = []
    monkeypatch.setattr(lane, "_log", lambda msg, color="white": lines.append(str(msg)))
    return lines


_ENDS: dict = {}


def causal(data, t):
    """What the engine hands a tick: a fresh list per symbol of the bars
    whose interval has ENDED by `t` (the last 400: the lane reads no more)."""
    out = {}
    for s, bars in data.items():
        key = (id(bars), len(bars))
        if key not in _ENDS:
            _ENDS.clear() if len(_ENDS) > 64 else None
            _ENDS[key] = [clock.as_utc(b["t"]).timestamp() + 900 for b in bars]
        i = bisect.bisect_right(_ENDS[key], t.timestamp())
        out[s] = bars[max(0, i - 400):i]
    return out


def last_prints(view, t, *, fresh_only):
    px = {}
    for s, bars in view.items():
        if not bars:
            continue
        end = clock.as_utc(bars[-1]["t"]) + timedelta(minutes=15)
        if not fresh_only or t - end < timedelta(minutes=16):
            px[s] = bars[-1]["c"]
    return px


def orders_of(payload):
    return {s: d for s, d in (payload or {}).items() if not s.startswith("_") and d in (1, -1)}


# -- the real-emulator driver ---------------------------------------------------------------


class Emu(PortfolioEmulator):
    def __init__(self, cash):
        super().__init__(initial_cash=cash)
        self.queue = []

    def pending_execution_symbols(self):
        return tuple(sorted({s for s, *_ in self.queue}))


def run_engine(data, start, end, cfg=None):
    """Every weekday 04:00-20:00 ET at 900 s. Returns the events, the daily
    15:45 snapshots and the account."""
    cfg = dict(CFG, **(cfg or {}))
    emu, cache, strat = Emu(5500.0), {}, lane.StrategyTrendResearch()
    events, daily = [], []
    d = start
    while d <= end:
        if d.weekday() < 5:
            t = utc(d, 4, 0)
            while t <= utc(d, 20, 0):
                view = causal(data, t)
                fresh = last_prints(view, t, fresh_only=True)
                marks = last_prints(view, t, fresh_only=False)
                with contextlib.redirect_stdout(io.StringIO()):
                    emu._advance_clock(t)
                    emu.get_portfolio_value(marks)
                    keep = []
                    for s, side, hint in emu.queue:
                        p = fresh.get(s)
                        if p is None:
                            keep.append((s, side, hint))
                            continue
                        if side == 1:
                            spend = min(float(hint.get("buy_cash", 0)), emu.get_buying_power())
                            ok = spend > 0 and emu.buy(s, spend / (p * 1.004), p, timestamp=t)
                        else:
                            q = emu.get_positions().get(s, 0.0) * float(
                                hint.get("sell_fraction", 1.0))
                            ok = q > 0 and emu.sell(s, q, p, timestamp=t)
                        events.append((t, "FILL", s, side, bool(ok)))
                    emu.queue = keep
                payload = strat.run_once([], dict(fresh), t, dict(cfg), {}, data=view,
                                         portfolio_emulator=emu, strategy_cache=cache,
                                         time_increment="900", mode="backtest")
                sizes = (payload or {}).get("_nexus_position_sizes") or {}
                for s, side in sorted(orders_of(payload).items()):
                    emu.queue.append((s, side, sizes.get(s) or {}))
                    events.append((t, "ORDER", s, side, sizes.get(s)))
                sb = session_bounds(d)
                if sb and t == (sb[1] - timedelta(minutes=15)).astimezone(timezone.utc):
                    nav = emu.get_portfolio_value(marks)
                    pos = {s: q * marks.get(s, 0.0) / nav
                           for s, q in emu.get_positions().items() if q > 0}
                    daily.append((d, nav, emu.get_cash() / nav, pos))
                t += timedelta(minutes=15)
        d += timedelta(days=1)
    return events, daily, emu, cache


def _engine_scenario(st):
    """Six weeks from 2025-12-01: a rotation every week, a rocket, a crash
    through the stop, and GGG's bars stopping on 2025-12-05 while held."""
    lo, hi = date(2025, 10, 1), date(2026, 1, 9)
    rng = random.Random(3)
    walk = {}

    def price(s, d, t):
        if s == "BIL":
            return round(100.0 + 0.0002 * (d - lo).days, 4)
        key = (s, d, t)
        if key not in walk:
            drift = {"AAA": 0.015 if d >= date(2025, 12, 1) else 0.0,
                     "CCC": -0.05 if date(2025, 12, 10) <= d <= date(2025, 12, 16) else 0.0
                     }.get(s, 0.0)
            prev = walk.get(s, 100.0)
            in_rth = time(9, 30) <= t.timetz().replace(tzinfo=None) < time(16)
            walk[s] = prev * math.exp((drift / 26 if in_rth else 0.0)
                                      + 0.002 * rng.gauss(0, 1) * (1 if in_rth else 0.3))
            walk[key] = walk[s]
        return round(walk[key], 4)

    syms = NAMES + ("III", "JJJ", "TQQQ", "SQQQ", "BIL")
    data = make_bars(syms, lo, hi, price, ext=True, stop_after={"GGG": date(2025, 12, 5)})
    base = [(s, 0.11) for s in NAMES[:6]] + [("GGG", 0.11), ("TQQQ", 0.12), ("SQQQ", 0.06)]
    weeks = [date(2025, 12, 1) + timedelta(days=7 * i) for i in range(6)]
    for i, monday in enumerate(weeks):
        picks = list(base)
        if i % 2:                                   # rotate FFF <-> III/JJJ
            picks = [p for p in picks if p[0] != "FFF"] + [("III", 0.08), ("JJJ", 0.05)]
        put_row(st, monday, picks)
    return data, weeks[0], weeks[-1] + timedelta(days=4)


def test_engine_run_keeps_idle_cash_in_bil_and_never_wedges(st, logs):
    """sweep-lane #1/#2/#3/#5 end to end on the real emulator: cash outside
    BIL averages under 2% of NAV, no fill is ever rejected (no dust order),
    no order lands outside the regular session (2025-12-24 closes at 13:00),
    the delisted holding is written off once and its sell is not re-sent,
    and the sweep keeps working after it."""
    data, start, end = _engine_scenario(st)
    events, daily, emu, cache = run_engine(data, start, end)
    cash = [c for _d, _n, c, _p in daily]
    assert sum(cash) / len(cash) < 0.02, cash
    assert [e for e in events if e[1] == "FILL" and not e[4]] == []
    for t, kind, *_ in events:
        if kind == "ORDER":
            o, c = session_bounds(t.astimezone(ET).date())
            assert o <= t.astimezone(ET) < c, t
    # GGG's last bar is 12-05; five sessions later (12-15, a decision day) it
    # is written off before the decision, so no sell that can never fill is
    # ever sent (the old lane re-sent it every week and it blocked the sweep).
    off = [line for line in logs if "unpriced write-off: GGG" in line]
    assert len(off) == 1 and off[0].startswith("StrategyTrendResearch 2025-12-15")
    assert [e for e in events if e[1] == "ORDER" and e[2] == "GGG"
            and e[0].astimezone(ET).date() > date(2025, 12, 5)] == []
    assert "GGG" in cache["_tr_written_off"]
    after = [e for e in events if e[1] == "ORDER" and e[0].astimezone(ET).date()
             >= date(2025, 12, 16) and e[2] != "GGG"]
    assert after                                      # the lane still trades
    lev = max(sum(w for s, w in p.items() if s in ("TQQQ", "SQQQ")) for *_x, p in daily)
    assert lev < 0.22


# -- the fake-emulator driver -----------------------------------------------------------------


class FakeEmu:
    """Next-tick fills on a fresh print; an order without one stays pending."""

    def __init__(self, cash=5500.0):
        self.cash = cash
        self.pos: dict = {}
        self.queue: list = []
        self.last: dict = {}

    def get_positions(self):
        return dict(self.pos)

    def get_cash(self):
        return self.cash

    def get_buying_power(self, reserved=0.0, *, prices=None):
        return self.cash

    def get_portfolio_value(self, prices):
        self.last.update(prices or {})
        return self.cash + sum(q * self.last.get(s, 0.0) for s, q in self.pos.items())

    def pending_execution_symbols(self):
        return tuple(sorted({s for s, _ in self.queue}))

    def submit(self, payload):
        sizes = (payload or {}).get("_nexus_position_sizes") or {}
        for s, d in sorted(orders_of(payload).items()):
            self.queue.append((s, (d, sizes.get(s) or {})))

    def fill(self, fresh):
        keep = []
        for s, (d, hint) in self.queue:
            px = fresh.get(s)
            if not px:
                keep.append((s, (d, hint)))
                continue
            if d == 1:
                spend = min(float(hint.get("buy_cash", 0.0)), self.cash)
                self.pos[s] = self.pos.get(s, 0.0) + spend / px
                self.cash -= spend
            else:
                qty = self.pos.get(s, 0.0) * float(hint.get("sell_fraction", 1.0))
                self.pos[s] = self.pos.get(s, 0.0) - qty
                self.cash += qty * px
                if self.pos[s] <= 0:
                    del self.pos[s]
        self.queue = keep


def drive(data, ticks, *, cfg=None, emu=None, cache=None):
    emu = emu or FakeEmu()
    cache = {} if cache is None else cache
    strat = lane.StrategyTrendResearch()
    out = []
    for t in ticks:
        view = causal(data, t)
        emu.fill(last_prints(view, t, fresh_only=True))
        marks = last_prints(view, t, fresh_only=False)       # the broker's last prices
        payload = strat.run_once([], marks, t, dict(CFG, **(cfg or {})), {}, data=view,
                                 portfolio_emulator=emu, strategy_cache=cache,
                                 time_increment="900", mode="backtest")
        emu.submit(payload)
        out.append((t, payload))
    return out, emu, cache


def week(lo, hi):
    return [t for d in sessions(lo, hi) for t in rth_ticks(d)]


EVEN = [(s, 0.11) for s in NAMES]
W1, W2 = date(2025, 8, 11), date(2025, 8, 18)


# -- B1: the band, the caps and leftover cash ---------------------------------------------------


def test_schema_declares_the_one_percent_band():
    meta, _ = _parse_header_meta(open(lane.__file__).read())
    assert meta["config"]["core_band_pct"] == 0.01 == lane.DEFAULTS["core_band_pct"]


def _orders(weights, positions, prices, cash, cfg=None, pending=()):
    held = sorted(s for s, q in positions.items() if q > 0)
    nav = cash + sum(q * prices[s] for s, q in positions.items())
    return lane.StrategyTrendResearch._orders(
        weights, nav=nav, positions=positions, prices=prices, cash=cash,
        cfg={**lane.DEFAULTS, **(cfg or {})}, owned=set(weights) | set(held), held=held,
        written_off=set(), pending=set(pending))


def test_caps_are_trimmed_whatever_the_band():
    """A 50% band would hide every drift; the single-name and leveraged +
    inverse caps (C3) are enforced anyway."""
    prices = {"AAA": 100.0, "TQQQ": 100.0, "SQQQ": 100.0, "BBB": 100.0, "BIL": 100.0}
    positions = {"AAA": 30.0, "TQQQ": 18.0, "SQQQ": 6.0, "BBB": 46.0}   # NAV 10,000
    weights = {"AAA": 0.25, "TQQQ": 0.15, "SQQQ": 0.05, "BBB": 0.46, "BIL": 0.09}
    d, sz = _orders(weights, positions, prices, 0.0, cfg={"core_band_pct": 0.5})
    assert d == {"AAA": -1, "TQQQ": -1, "SQQQ": -1}
    assert sz["AAA"]["sell_fraction"] == pytest.approx(5 / 30)
    assert sz["TQQQ"]["sell_fraction"] == pytest.approx(3 / 18)
    assert sz["SQQQ"]["sell_fraction"] == pytest.approx(1 / 6)
    # Inside the caps the band rules: no order.
    d, _ = _orders(weights, {"AAA": 25.0, "TQQQ": 15.0, "SQQQ": 5.0, "BBB": 46.0},
                   prices, 900.0, cfg={"core_band_pct": 0.5})
    assert set(d) == {"BIL"}


def test_leftover_cash_goes_to_bil_and_nets_a_bil_trim():
    prices = {"AAA": 100.0, "BIL": 100.0}
    d, sz = _orders({"AAA": 1.0, "BIL": 0.0}, {"AAA": 99.0}, prices, 100.0)
    assert d == {"BIL": 1} and sz["BIL"]["buy_cash"] == pytest.approx(100.0 * (1 - 0.006))
    # A BIL trim alongside idle cash is a round trip: it shrinks instead.
    # NAV 10,000: BIL 4,500 -> 3,000 would sell 1,500 while 497 sits idle.
    d, sz = _orders({"AAA": 0.5, "BIL": 0.3}, {"AAA": 50.0, "BIL": 45.0}, prices, 500.0)
    assert d == {"BIL": -1}
    assert sz["BIL"]["sell_fraction"] == pytest.approx((1500 - 500 * 0.994) / 4500)
    d, sz = _orders({"AAA": 0.5, "BIL": 0.3}, {"AAA": 50.0, "BIL": 32.0}, prices, 1800.0)
    assert d == {"BIL": 1}                    # the 200 trim nets out; the rest is bought
    assert sz["BIL"]["buy_cash"] == pytest.approx(1800 * 0.994 - 200)


# -- B2: float residue and dust ------------------------------------------------------------------


def test_a_sell_leaving_under_a_dollar_sells_everything():
    prices = {"AAA": 100.0, "BIL": 100.0}
    d, sz = _orders({"AAA": 0.999, "BIL": 1e-16}, {"AAA": 1.0, "BIL": 99.0}, prices, 0.0,
                    cfg={"min_order_usd": 0.0})
    assert d["BIL"] == -1 and sz["BIL"]["sell_fraction"] == 1.0


def test_dust_never_blocks_the_sweep(st, logs):
    """A 6.5e-15-share BIL residue with a 0 target: the emulator refuses an
    order under $1, so emitting its exit every tick (sweep-lane #2) is
    pointless -- and it held the idle-cash sweep back for the whole run."""
    data = make_bars(NAMES + ("BIL",), date(2025, 7, 1), date(2025, 8, 15))
    put_row(st, W1, [(s, 0.125) for s in NAMES])
    out, emu, cache = drive(data, rth_ticks(W1, n=2))
    assert cache["_tr_targets"]["BIL"] == 0.0
    emu.pos["BIL"] = 6.5e-15
    emu.cash += 400.0                                 # e.g. a dividend or a stop's proceeds
    out, emu, cache = drive(data, rth_ticks(W1)[2:5], emu=emu, cache=cache)
    assert all(orders_of(p).get("BIL") != -1 for _, p in out)
    assert any(orders_of(p).get("BIL") == 1 for _, p in out)    # swept into BIL


# -- B3: a holding whose bars stop -----------------------------------------------------------------


def test_a_delisted_holding_is_written_off_after_five_sessions(st, logs):
    data = make_bars(NAMES + ("BIL",), date(2025, 7, 1), date(2025, 9, 5),
                     stop_after={"GGG": date(2025, 8, 15)})
    put_row(st, W1, EVEN)
    put_row(st, W2, [p for p in EVEN if p[0] != "GGG"])       # the research drops GGG
    out, emu, cache = drive(data, week(W1, date(2025, 8, 29)))
    ggg_sells = [t for t, p in out if orders_of(p).get("GGG") == -1]
    assert len(ggg_sells) == 1                                 # sent once, pending forever
    assert sum("unpriced write-off: GGG" in line for line in logs) == 1
    first_off = next(line for line in logs if "unpriced write-off: GGG" in line)
    assert first_off.startswith("StrategyTrendResearch 2025-08-25")   # 8/18-8/22: no bar
    assert cache["_tr_written_off"] == {"GGG"}
    # A stop's proceeds are swept despite GGG's dead order.
    assert "GGG" in emu.pending_execution_symbols()


def test_a_stuck_exit_does_not_block_the_sweep(st, logs):
    data = make_bars(NAMES + ("BIL",), date(2025, 7, 1), date(2025, 8, 29),
                     stop_after={"HHH": date(2025, 8, 15)})
    put_row(st, W1, EVEN)
    put_row(st, W2, [p for p in EVEN if p[0] != "HHH"])
    _, emu, cache = drive(data, week(W1, W1 + timedelta(days=4)))
    out, emu, cache = drive(data, rth_ticks(W2, n=3), emu=emu, cache=cache)
    assert orders_of(out[0][1]).get("HHH") == -1               # the decision's exit
    emu.cash += 300.0
    out, emu, cache = drive(data, rth_ticks(W2)[3:6], emu=emu, cache=cache)
    assert "HHH" in emu.pending_execution_symbols()            # can never fill
    assert any(orders_of(p).get("BIL") == 1 for _, p in out)  # the sweep still runs


# -- B4: regular-session closes ------------------------------------------------------------------


def test_after_hours_prints_are_not_the_close(st, logs):
    """AAA closes at 100 every session, but prints 70 after hours on Tuesday
    and 200 before Wednesday's open: the stop and the peak read only the
    regular session, so nothing fires."""
    def price(s, d, t):
        clk = t.timetz().replace(tzinfo=None)
        if s == "AAA" and d == date(2025, 8, 12) and clk >= time(16):
            return 70.0
        if s == "AAA" and d == date(2025, 8, 13) and clk < time(9, 30):
            return 200.0
        return 100.0
    data = make_bars(NAMES + ("BIL",), date(2025, 7, 1), date(2025, 8, 22), price, ext=True)
    put_row(st, W1, EVEN)
    out, emu, cache = drive(data, week(W1, W1 + timedelta(days=4)))
    assert all(orders_of(p).get("AAA") != -1 for _, p in out)
    assert not any("| STOP " in line for line in logs)
    assert cache["_tr_peaks"]["AAA"] == pytest.approx(100.0)


# -- B5: early closes ----------------------------------------------------------------------------


def test_no_order_after_an_early_close(st, logs):
    """2025-12-24 closes at 13:00 ET: a 13:00 or 15:00 tick decides nothing;
    the week's decision waits for the next session's open."""
    lo = date(2025, 12, 22)
    data = make_bars(NAMES + ("BIL",), date(2025, 11, 1), date(2025, 12, 31))
    put_row(st, lo, EVEN)
    late = [utc(date(2025, 12, 24), 13, 0), utc(date(2025, 12, 24), 15, 0)]
    out, emu, cache = drive(data, late)
    assert all(p == {} for _, p in out) and "_tr_last_week" not in cache
    out, emu, cache = drive(data, [utc(date(2025, 12, 24), 12, 45)])
    assert orders_of(out[0][1])                                # inside the short session


# -- B6: what the sweep may buy --------------------------------------------------------------------


def test_sweep_does_not_top_up_a_fallen_name_or_a_raised_winner(st, logs):
    """CCC is stopped Thursday; its proceeds are swept. BBB fell 15% (below
    target, 10%+ under its peak) and AAA is a raised winner that came back
    down: neither is bought -- the cash goes to BIL."""
    def price(s, d, t):
        if s == "CCC" and d >= date(2025, 8, 20):
            return 70.0
        if s == "BBB" and d >= date(2025, 8, 19):
            return 85.0
        if s == "AAA":
            return 200.0 if date(2025, 8, 13) <= d <= date(2025, 8, 18) else (
                185.0 if d > date(2025, 8, 18) else 100.0)
        return 100.0
    data = make_bars(NAMES + ("BIL",), date(2025, 7, 1), date(2025, 8, 29), price)
    put_row(st, W1, EVEN)
    put_row(st, W2, EVEN)
    _, emu, cache = drive(data, week(W1, W1 + timedelta(days=4)))
    out, emu, cache = drive(data, week(W2, W2 + timedelta(days=4)), emu=emu, cache=cache)
    assert cache["_tr_targets"]["AAA"] > cache["_tr_book_targets"]["AAA"]   # raised
    stop_t = next(t for t, p in out if orders_of(p).get("CCC") == -1)
    swept = [orders_of(p) for t, p in out if t > stop_t and orders_of(p)]
    assert swept and all(set(o) == {"BIL"} for o in swept)
    assert any("sweep skips BBB" in line for line in logs)


# -- B8: a week whose picks have no engine bars ----------------------------------------------------


def test_a_week_whose_picks_have_no_bars_is_held(st, logs):
    data = make_bars(NAMES + ("BIL",), date(2025, 7, 1), date(2025, 8, 29))
    put_row(st, W1, EVEN)
    put_row(st, W2, [(s, 0.11) for s in ("NEWA", "NEWB", "NEWC", "NEWD", "NEWE")]
            + [("AAA", 0.11)])
    _, emu, cache = drive(data, week(W1, W1 + timedelta(days=4)))
    before = dict(emu.pos)
    out, emu, cache = drive(data, week(W2, W2 + timedelta(days=1)), emu=emu, cache=cache)
    assert all(orders_of(p).get(s) != -1 for _, p in out for s in NAMES)
    assert emu.pos == pytest.approx(before)
    held = [line for line in logs if "HELD: 5 of 6 stock picks have no engine bars" in line]
    assert len(held) == 1 and "1 week(s) held this way" in held[0]
    assert cache["_tr_no_bar_weeks"] == 1 and cache["_tr_book_week"] == W1.isoformat()


# -- the TR tables are ensured before any ledger read -----------------------------------------


def test_every_ledger_read_ensures_the_tr_tables(st, logs, monkeypatch):
    """The lane's weekly read (ledger.latest_row), the broker's bar-fetch
    universe and the scorecard all read TrendResearchLedger; on a fresh
    database each must create the TR tables first (pit_store.ensure_tables,
    idempotent)."""
    from trend_research import pit_store, scorecard as sc
    seen = []
    monkeypatch.setattr(pit_store, "ensure_tables", lambda store=None: seen.append(store))
    put_row(st, W1, EVEN)

    lane.trend_research_universe(CFG, store_=st)
    assert seen and seen[-1] is st
    seen.clear()

    data = make_bars(NAMES + ("BIL",), date(2025, 7, 1), date(2025, 8, 15))
    drive(data, rth_ticks(W1, n=1))
    assert seen and all(s is st for s in seen)
    seen.clear()

    days = sessions(W1, W1 + timedelta(days=4))
    snaps = [{"timestamp": utc(d, 15, 45), "value": 5500.0} for d in days]
    sc.backtest_scorecard(CFG, snaps, start=days[0], end=days[-1],
                          benchmark_bars=lambda *a: {}, store=st, daily_bars=lambda *a: {},
                          n_random=5)
    assert seen and seen[0] is st


def test_the_universe_survives_a_failed_table_ensure(st, logs, monkeypatch):
    from trend_research import book, pit_store

    def boom(store=None):
        raise RuntimeError("db down")
    monkeypatch.setattr(pit_store, "ensure_tables", boom)
    assert lane.trend_research_universe(CFG, store_=st) == sorted(book.ALL_ALLOWED_ETFS)
    assert any("ledger unreadable" in line for line in logs)
