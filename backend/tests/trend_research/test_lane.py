"""TR engine lane: the weekly ledger replay, the daily stop and the broker
wiring (plan Task 9).

No network and no broker import: the emulator is a small fake that fills each
queued order at the next tick's price (the engine's next-bar rule), bars are
synthetic 15-minute bars (granularity 900), and the ledger lives in a FakeStore
the lane module reads through its monkeypatched ``store``.
"""
from __future__ import annotations

import ast
import os
import sys
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

_backend = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from db.fake import FakeStore  # noqa: E402
from strategies import strategy_trend_research as lane  # noqa: E402
from strategies_meta import _parse_header_meta  # noqa: E402
from swing_trader import clock  # noqa: E402
from trend_research import book, ledger  # noqa: E402

ET = ZoneInfo("America/New_York")
CHAIN, MODEL, PV = "honest", "claude-test", "tr-v1"
NAMES = ("AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "GGG", "HHH")
CFG = {"strategy_trend_research_enabled": True, "tr_chain_id": CHAIN, "tr_model": MODEL,
       "tr_prompt_version": PV, "max_positions": 8}
W1, W2, W3 = date(2025, 8, 11), date(2025, 8, 18), date(2025, 8, 25)


# -- fixtures ---------------------------------------------------------------------


def ticks_of(day: date, *, start=time(9, 30), n=26):
    t0 = datetime.combine(day, start, tzinfo=ET)
    return [(t0 + timedelta(minutes=15 * i)).astimezone(timezone.utc) for i in range(n)]


def sessions(lo: date, hi: date):
    return sorted(clock.trading_days(lo, hi))


def make_bars(symbols, lo, hi, price=None):
    """{sym: [15-minute bars]} for every NYSE session in [lo, hi].
    ``price(sym, day, i)`` defaults to a flat 100."""
    price = price or (lambda s, d, i: 100.0)
    data = {}
    for s in symbols:
        bars = []
        for d in sessions(lo, hi):
            for i, t in enumerate(ticks_of(d)):
                bars.append({"t": t.strftime("%Y-%m-%dT%H:%M:%SZ"), "c": price(s, d, i)})
        data[s] = bars
    return data


def put_row(st, monday, picks, *, status="ok", recommended=None, notes=None):
    as_of = ledger.week_as_of(monday).strftime("%Y-%m-%dT%H:%M:%SZ")
    row = {"id": ledger.week_id(CHAIN, monday, MODEL, PV), "chain_id": CHAIN,
           "week_start": monday.isoformat(), "as_of": as_of, "available_at": as_of,
           "model": MODEL, "provider": "claude-cli", "prompt_version": PV, "status": status,
           "notes": notes if notes is not None else
           [{"symbol": s, "conviction": 7.0, "thesis_status": "intact"} for s, _ in picks],
           "recommended": recommended if recommended is not None else
           [{"symbol": s} for s, _ in picks],
           "book": {"defensive_share": 0.0,
                    "picks": [{"symbol": s, "target_weight": w, "conviction": 7.0,
                               "role": "core"} for s, w in picks]}}
    ledger.put_week(row, store=st)
    return row


class FakeEmu:
    """Positions, cash and next-tick fills; enough of PortfolioEmulator."""

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
        for s, p in (prices or {}).items():
            self.last[s] = p
        return self.cash + sum(q * self.last.get(s, 0.0) for s, q in self.pos.items())

    def pending_execution_symbols(self):
        return tuple(sorted({s for s, _ in self.queue}))

    def submit(self, payload):
        sizes = payload.get("_nexus_position_sizes") or {}
        for s, d in sorted(payload.items()):
            if s.startswith("_") or d not in (1, -1):
                continue
            self.queue.append((s, (d, sizes.get(s) or {})))

    def fill(self, prices):
        for s, (d, hint) in self.queue:
            px = prices.get(s)
            if not px:
                continue
            if d == 1:
                spend = min(float(hint.get("buy_cash", 0.0)), self.cash)
                self.pos[s] = self.pos.get(s, 0.0) + spend / px
                self.cash -= spend
            else:
                qty = self.pos.get(s, 0.0) * float(hint.get("sell_fraction", 1.0))
                self.pos[s] = self.pos.get(s, 0.0) - qty
                self.cash += qty * px
                if self.pos[s] <= 1e-9:
                    del self.pos[s]
        self.queue = []


def prices_at(data, t):
    stamp = t.strftime("%Y-%m-%dT%H:%M:%SZ")
    out = {}
    for s, bars in data.items():
        for b in bars:
            if b["t"] <= stamp:
                out[s] = b["c"]
            else:
                break
    return out


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


def drive(data, ticks, *, cfg=CFG, emu=None, cache=None, strat=None, prices=True):
    """Run the lane over `ticks`; returns [(tick, payload)] and the emulator."""
    emu = emu or FakeEmu()
    cache = {} if cache is None else cache
    strat = strat or lane.StrategyTrendResearch()
    out = []
    for t in ticks:
        px = prices_at(data, t)
        emu.fill(px)
        payload = strat.run_once([], px if prices else {}, t, dict(cfg), {}, data=data,
                                 portfolio_emulator=emu, strategy_cache=cache,
                                 time_increment="900", mode="backtest")
        emu.submit(payload)
        out.append((t, payload))
    return out, emu, cache


def week_ticks(lo, hi):
    return [t for d in sessions(lo, hi) for t in ticks_of(d)]


def buys(payload):
    return sorted(s for s, d in payload.items() if not s.startswith("_") and d == 1)


def sells(payload):
    return sorted(s for s, d in payload.items() if not s.startswith("_") and d == -1)


EVEN = [(s, 0.11) for s in NAMES]


# -- schema --------------------------------------------------------------------------


def test_schema_header_declares_the_lane_and_its_defaults():
    meta, desc = _parse_header_meta(open(lane.__file__).read())
    assert meta["strategy"] == "strategy_trend_research"
    assert meta["execution_scope"] == "run_once" and meta["decision_phase"] == "pre"
    cfg = meta["config"]
    assert cfg["strategy_trend_research_enabled"] is False
    assert cfg["tr_prompt_version"] == "tr-v1" and cfg["max_llm_calls_per_build"] == 2000
    assert cfg["honour_single_position_cap"] is True
    assert cfg["broker_max_single_position_pct"] == 0.25
    assert {k: cfg[k] for k in cfg} == {k: lane.DEFAULTS[k] for k in cfg}
    assert desc.strip()


# -- the weekly latch -----------------------------------------------------------------


def test_weekly_latch_once_at_900s(st, logs):
    data = make_bars(NAMES + ("BIL",), date(2025, 7, 1), date(2025, 9, 12))
    put_row(st, W1, EVEN)
    week = week_ticks(W1, W1 + timedelta(days=4))
    early = datetime.combine(W1, time(9, 15), tzinfo=ET).astimezone(timezone.utc)
    out, emu, cache = drive(data, [early] + week)
    decided = [line for line in logs if "| WEEK " in line]
    assert len(decided) == 1 and "2025-08-11" in decided[0]
    assert out[0][1] == {}                                   # 09:15: before the decision
    first = out[1]
    assert first[0] == ticks_of(W1)[0] and buys(first[1]) == sorted(NAMES + ("BIL",))
    assert cache["_tr_last_week"] == "2025-08-11"
    assert set(emu.pos) == set(NAMES) | {"BIL"}

    # Labor Day: the week of 2025-09-01 decides on Tuesday's first tick, once.
    logs.clear()
    labor = date(2025, 9, 1)
    put_row(st, labor, EVEN)
    ticks = [t for d in (labor, labor + timedelta(days=1), labor + timedelta(days=2))
             for t in ticks_of(d)]                           # the engine ticks holidays too
    out, _, cache = drive(data, ticks, emu=emu, cache=cache)
    decided = [line for line in logs if "| WEEK " in line]
    assert len(decided) == 1 and "2025-09-02" in decided[0]
    assert all(p == {} for t, p in out if t.astimezone(ET).date() == labor)
    assert cache["_tr_last_week"] == "2025-09-01"


# -- missing week ------------------------------------------------------------------------


def test_missing_ledger_week_holds_book(st, logs):
    """W2 has only a failed row; latest_row then returns W1's row, which is
    stale for W2 -- the lane holds: no buys, no liquidation, stops still run."""
    def price(s, d, i):
        return 70.0 if (s == "CCC" and d >= date(2025, 8, 19)) else 100.0
    data = make_bars(NAMES + ("BIL",), date(2025, 7, 1), date(2025, 8, 29), price)
    put_row(st, W1, EVEN)
    put_row(st, W2, [("ZZZ", 0.2)], status="failed")
    _, emu, cache = drive(data, week_ticks(W1, W1 + timedelta(days=4)))
    book_w1 = dict(emu.pos)
    out, emu, cache = drive(data, week_ticks(W2, W2 + timedelta(days=4)), emu=emu, cache=cache)
    assert any("no ok ledger row for week 2025-08-18" in line for line in logs)
    assert sum("no ok ledger row" in line for line in logs) == 1
    sold = sorted({s for _, p in out for s in sells(p)})
    bought = sorted({s for _, p in out for s in buys(p)})
    assert sold == ["CCC"]                                    # the stop, on Wednesday
    assert set(bought) <= {"BIL"}
    stop_tick = next(t for t, p in out if sells(p))
    assert stop_tick == ticks_of(date(2025, 8, 20))[0]
    for s in NAMES:
        if s != "CCC":
            assert emu.pos[s] == pytest.approx(book_w1[s])
    assert cache["_tr_last_week"] == "2025-08-18" and cache["_tr_book_week"] == "2025-08-11"


def test_ledger_read_error_holds_and_retries(st, logs, monkeypatch):
    data = make_bars(NAMES + ("BIL",), date(2025, 7, 1), date(2025, 8, 22))
    put_row(st, W1, EVEN)
    calls = []

    def boom(*a, **k):
        calls.append(1)
        raise RuntimeError("db down")
    monkeypatch.setattr(lane.ledger, "latest_row", boom)
    out, emu, cache = drive(data, ticks_of(W1)[:3])
    assert all(p == {} for _, p in out) and emu.pos == {}
    assert len(calls) == 3 and "_tr_last_week" not in cache      # not latched
    assert sum("ledger unreadable" in line for line in logs) == 1


# -- the daily stop ------------------------------------------------------------------------


def test_stop_runs_daily_and_blocks_rebuy_same_week(st, logs):
    """AAA peaks at 120 Tuesday and closes 90 (75% of peak) Wednesday: the stop
    reads only prior-session closes, so it fires on Thursday's first tick, not
    during Wednesday. AAA stays out (the sweep buys BIL) until W2's decision,
    whose research ranks it again."""
    def price(s, d, i):
        if s != "AAA":
            return 100.0
        if d == date(2025, 8, 12):
            return 120.0
        if d >= date(2025, 8, 13):
            return 90.0
        return 100.0
    data = make_bars(NAMES + ("BIL",), date(2025, 7, 1), date(2025, 8, 22), price)
    put_row(st, W1, EVEN)
    put_row(st, W2, EVEN)
    out, emu, cache = drive(data, week_ticks(W1, W1 + timedelta(days=4)))
    stop_ticks = [t for t, p in out if "AAA" in sells(p)]
    assert stop_ticks == [ticks_of(date(2025, 8, 14))[0]]
    assert cache["_tr_peaks"].get("AAA") is None              # forgotten once sold
    assert "AAA" not in emu.pos
    later = [p for t, p in out if t > stop_ticks[0]]
    assert all("AAA" not in buys(p) for p in later)
    assert any(buys(p) == ["BIL"] for p in later)              # proceeds -> BIL
    assert cache["_tr_stopped"] == {"AAA"}
    assert sum("| STOP " in line for line in logs) == 1

    out, emu, cache = drive(data, ticks_of(W2)[:3], emu=emu, cache=cache)
    # Ranked again: W2's decision trims BIL to fund it, and the next tick's
    # sweep buys AAA with the settled proceeds.
    assert sells(out[0][1]) == ["BIL"] and buys(out[1][1]) == ["AAA"]
    assert "AAA" in emu.pos
    assert cache["_tr_stopped"] == set()


def test_stop_on_the_decision_tick_sits_the_week_out(st, logs):
    def price(s, d, i):
        if s == "AAA" and d == date(2025, 8, 14):
            return 130.0
        if s == "AAA" and d >= date(2025, 8, 15):
            return 100.0
        return 100.0
    data = make_bars(NAMES + ("BIL",), date(2025, 7, 1), date(2025, 8, 22), price)
    put_row(st, W1, EVEN)
    put_row(st, W2, EVEN)
    _, emu, cache = drive(data, week_ticks(W1, W1 + timedelta(days=4)))
    assert "AAA" in emu.pos                                    # 100/130 = 77% -> not yet
    # Friday close 100 vs peak 130 -> 76.9% <= 80%: stopped on Monday's first tick,
    # which is also W2's decision; AAA sits W2 out.
    out, emu, cache = drive(data, week_ticks(W2, W2 + timedelta(days=4)), emu=emu,
                            cache=cache)
    first = out[0][1]
    assert "AAA" in sells(first) and "AAA" not in buys(first)
    assert all("AAA" not in buys(p) for _, p in out)
    assert cache["_tr_targets"].get("AAA") is None
    assert cache["_tr_stopped"] == {"AAA"}


# -- selling what the lane owns ------------------------------------------------------------


def test_owned_symbols_are_sellable(st, logs):
    data = make_bars(NAMES + ("BIL", "III"), date(2025, 7, 1), date(2025, 8, 29))
    put_row(st, W1, EVEN)
    put_row(st, W2, [(s, 0.11) for s in NAMES[:7]] + [("III", 0.11)])
    _, emu, cache = drive(data, week_ticks(W1, W1 + timedelta(days=4)))
    assert "HHH" in emu.pos
    out, emu, cache = drive(data, ticks_of(W2)[:4], emu=emu, cache=cache)
    assert "HHH" in sells(out[0][1])                           # absent from W2's research
    assert out[0][1]["_nexus_action_intents"]["HHH"] == "etf_sell"
    assert out[0][1]["_nexus_position_sizes"]["_cash_reserve_floor_pct"] == 0.0
    assert "HHH" not in emu.pos


def test_a_held_name_without_bars_is_sold_when_its_price_returns(st, logs):
    """GGG has no bars at W2's decision (the engine dropped it): target_book
    drops it, the exit has no price and waits; once bars return it is sold."""
    data = make_bars(NAMES + ("BIL",), date(2025, 7, 1), date(2025, 8, 29))
    put_row(st, W1, EVEN)
    put_row(st, W2, EVEN)
    _, emu, cache = drive(data, week_ticks(W1, W1 + timedelta(days=4)))
    assert "GGG" in emu.pos
    full = data["GGG"]
    data["GGG"] = []
    emu.last.pop("GGG", None)
    emu.last["GGG"] = 100.0                                     # the emulator still marks it
    out, emu, cache = drive(data, ticks_of(W2)[:2], emu=emu, cache=cache)
    assert "GGG" not in sells(out[0][1]) and "GGG" in emu.pos
    assert any("drop GGG (holding): no bars" in line for line in logs)
    assert any("exit of GGG waits" in line for line in logs)
    assert "GGG" not in cache["_tr_targets"] and "GGG" in cache["_tr_owned"]
    data["GGG"] = full
    out, emu, cache = drive(data, ticks_of(W2)[2:4], emu=emu, cache=cache)
    assert "GGG" in sells(out[0][1])
    assert "GGG" not in emu.pos


# -- determinism, disabled, point in time ------------------------------------------------------


def test_replay_deterministic(st, logs):
    def price(s, d, i):
        return 100.0 + (hash((s, d.isoformat(), i)) % 7) - (15.0 if s == "BBB" and
                                                           d >= date(2025, 8, 13) else 0.0)
    data = make_bars(NAMES + ("BIL", "III"), date(2025, 7, 1), date(2025, 8, 29), price)
    put_row(st, W1, EVEN)
    put_row(st, W2, [(s, 0.11) for s in NAMES[2:]] + [("III", 0.2)])
    ticks = week_ticks(W1, W2 + timedelta(days=4))
    a, emu_a, _ = drive(data, ticks)
    b, emu_b, _ = drive(data, ticks)
    assert [p for _, p in a] == [p for _, p in b]
    assert any(p for _, p in a)
    assert not any("REFUSING" in line for line in logs)
    assert emu_a.pos == emu_b.pos


@pytest.mark.parametrize("flag", [False, "false", None])
def test_disabled_flag_inert(st, logs, monkeypatch, flag):
    data = make_bars(NAMES + ("BIL",), date(2025, 7, 1), date(2025, 8, 15))
    put_row(st, W1, EVEN)
    monkeypatch.setattr(lane.ledger, "latest_row",
                        lambda *a, **k: pytest.fail("a disabled lane read the ledger"))
    cfg = dict(CFG, strategy_trend_research_enabled=flag)
    out, emu, cache = drive(data, ticks_of(W1)[:5], cfg=cfg)
    assert all(p == {} for _, p in out) and cache == {} and emu.pos == {}


def test_live_ticks_are_refused(st, logs):
    put_row(st, W1, EVEN)
    emu, cache = FakeEmu(), {}
    out = lane.StrategyTrendResearch().run_once(
        [], {"AAA": 100.0}, ticks_of(W1)[0], dict(CFG), {}, data=None,
        portfolio_emulator=emu, strategy_cache=cache)
    assert out == {}
    assert any("backtest-only" in line for line in logs)


def test_uses_only_prior_bars(st, logs):
    """JJJ lists on the decision day: its bars start that session, so it is
    dropped. And the decision tick reads nothing later than itself: cutting
    every bar after it gives the identical payload."""
    data = make_bars(NAMES + ("BIL",), date(2025, 7, 1), date(2025, 8, 22))
    data.update(make_bars(("JJJ",), W1, date(2025, 8, 22)))
    put_row(st, W1, EVEN[:7] + [("JJJ", 0.11)])
    t0 = ticks_of(W1)[0]
    out, _, cache = drive(data, [t0])
    assert "JJJ" not in out[0][1] and "JJJ" not in cache["_tr_targets"]
    assert any("drop JJJ (pick): no bars at the decision date" in line for line in logs)

    stamp = t0.strftime("%Y-%m-%dT%H:%M:%SZ")
    cut = {s: [b for b in bars if b["t"] <= stamp] for s, bars in data.items()}
    # Future bars that would change everything if read: a crash in every name.
    for s, bars in data.items():
        for b in bars:
            if b["t"] > stamp:
                b["c"] = 1.0
    full_out, _, _ = drive(data, [t0])
    cut_out, _, _ = drive(cut, [t0])
    assert full_out[0][1] == cut_out[0][1] == out[0][1]


def test_unreadable_nav_fails_closed(st, logs):
    data = make_bars(NAMES + ("BIL",), date(2025, 7, 1), date(2025, 8, 15))
    put_row(st, W1, EVEN)
    emu = FakeEmu(cash=0.0)
    out, _, cache = drive(data, ticks_of(W1)[:2], emu=emu)
    assert all(p == {} for _, p in out) and "_tr_last_week" not in cache
    assert any("NAV reads" in line for line in logs)


# -- broker wiring ---------------------------------------------------------------------------


def test_universe_is_the_fixed_etfs_plus_the_chain_window(st):
    put_row(st, W1, [("aaa", 0.1), ("SPY", 0.1), ("NASDAQ:BBB", 0.1)],
            recommended=[{"symbol": "CCC"}, {"symbol": "brk-b"}])
    put_row(st, W2, [("DDD", 0.1)])
    put_row(st, W3, [("EEE", 0.1)], status="failed")
    other = dict(put_row(st, date(2025, 9, 8), [("FFF", 0.1)]))
    st.insert(ledger.TABLE, [dict(other, id=other["id"].replace(MODEL, "other-model"),
                                  model="other-model", book={"picks": [{"symbol": "XXX"}]})])
    fixed = sorted(book.ALL_ALLOWED_ETFS)
    got = lane.trend_research_universe(CFG, store_=st)
    assert got[:len(fixed)] == fixed
    assert got[len(fixed):] == ["AAA", "BBB", "BRK.B", "CCC", "DDD", "FFF"]
    window = lane.trend_research_universe(CFG, start=date(2025, 8, 13),
                                          end=datetime(2025, 8, 22, 23, 59), store_=st)
    assert window[len(fixed):] == ["AAA", "BBB", "BRK.B", "CCC", "DDD"]
    assert lane.trend_research_universe(dict(CFG, tr_chain_id=""), store_=st) == fixed


def test_universe_survives_an_unreadable_ledger():
    class Broken:
        def between(self, *a, **k):
            raise RuntimeError("down")
    assert lane.trend_research_universe(CFG, store_=Broken()) == sorted(book.ALL_ALLOWED_ETFS)


_BROKER = os.path.join(_backend, "broker.py")


def _broker_fn(name):
    """AST-extract a broker function (broker.py argparses at import)."""
    tree = ast.parse(open(_BROKER).read())
    keep = {name, "_truthy", "_merged_strategy_settings"}
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in keep]
    assert {n.name for n in nodes} == keep
    ns = {"_log": lambda *a, **k: None}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), _BROKER, "exec"), ns)
    return ns[name]


def test_broker_helper_declares_the_universe_only_when_enabled(st):
    put_row(st, W1, [("AAA", 0.1)])
    fn = _broker_fn("_trend_research_universe_symbols")
    spec = {"strategy": "strategy_trend_research", "config": dict(CFG)}
    got = fn([spec], start=W1, end=W1 + timedelta(days=4))
    assert got == sorted(book.ALL_ALLOWED_ETFS) + ["AAA"]
    assert fn([{**spec, "config": dict(CFG, strategy_trend_research_enabled="false")}]) == []
    assert fn([{"strategy": "strategy_eb", "config": {"strategy_eb_enabled": True}}]) == []
    for junk in (None, [], [None], ["strategy_trend_research"], [{"strategy": None}]):
        assert fn(junk) == []


def test_fetch_site_wired_and_lane_not_registered_live():
    source = open(_BROKER).read()
    assert source.count("_trend_research_universe_symbols(") == 2   # definition + fetch site
    tree = ast.parse(source)
    flags = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                 and any(isinstance(t, ast.Name) and t.id == "_LANE_ENABLE_FLAGS"
                         for t in n.targets))
    assert not any("trend_research" in k or "trendresearch" in k for k in flags)
