"""StrategySwing owns its data: the first-run reference sync and, in a
backtest, its own daily bars for the window's point-in-time universe.

broker.py is unchanged. The end-to-end test runs the lane with an EMPTY
watchlist through the engine's real on-demand loader and bar hook
(AST-extracted from broker.py), the path a traded symbol takes in a backtest.
Every network call is stubbed; conftest turns SWING_AUTODATA off by default
and these tests turn it on.
"""
from __future__ import annotations

import importlib.util
import math
import os
import sys
from datetime import date, datetime, timedelta, timezone

import pytest

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)
_tests = os.path.dirname(os.path.abspath(__file__))
if _tests not in sys.path:
    sys.path.insert(0, _tests)

import backtest_bar_events as bbe  # noqa: E402
import datetime as datetime_module  # noqa: E402
import swing_broker_harness as harness  # noqa: E402
from bar_time import bar_time_to_datetime  # noqa: E402
from portfolio_emulator import PortfolioEmulator  # noqa: E402
from simulated_execution import ExecutionCostModel, NextEventExecutionSimulator  # noqa: E402
from swing_port_calendar_fixtures import AVAILABLE  # noqa: E402
from swing_trader.constants import DEFENSIVE_UNIVERSE, SWING_DEFAULTS  # noqa: E402

PATH = os.path.join(_backend, "strategies", "strategy_swing.py")
UTC = timezone.utc
BT_ID = 4242
FIRST, END = date(2026, 3, 3), date(2026, 3, 6)


def _load():
    spec = importlib.util.spec_from_file_location("strategies.strategy_swing", PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _at(day):
    """05:00 PT on the broker's naive-UTC clock: the session's decision tick."""
    return datetime.fromisoformat(f"{day}T13:00:00")


def weekdays(lo, hi):
    d, out = lo, []
    while d <= hi:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def nyse_sessions(lo, hi):
    import live_calendar
    import pandas as pd
    return [d.date() for d in live_calendar._CAL.sessions_in_range(pd.Timestamp(lo),
                                                                    pd.Timestamp(hi))]


DAYS = nyse_sessions("2024-06-03", "2026-03-06")


def bar(d, o, h, l, c, v):
    return {"t": f"{d.isoformat()}T05:00:00Z", "o": o, "h": h, "l": l, "c": c, "v": v}


def spy_bars():
    return [bar(d, 300 + i * .5, 301 + i * .5, 299 + i * .5, 300.5 + i * .5, 5e7)
            for i, d in enumerate(DAYS)]


def aaa_bars():
    """A long uptrend, a 9-day dip from 2026-02-19 and a high-volume bounce
    from 2026-03-02: ST's entry fires on the 2026-03-03 decision (RSI 44.9
    and rising, MACD histogram improving, volume 3x its average, close over
    SMA200, ADX near 40). On 2026-03-04 it falls through the -6% stop."""
    out, price = [], 50.0
    dip = date(2026, 2, 19)
    for i, d in enumerate(DAYS):
        if d < dip:
            price *= 1.0015 * (1 + 0.004 * math.sin(i / 3.0))
            vol = 1e6
        elif (d - dip).days < 9:
            price *= 0.995
            vol = 1e6
        else:
            price *= 1.01
            vol = 3e6
        o, c = price * 0.998, price
        out.append(bar(d, round(o, 4), round(max(o, c) * 1.01, 4),
                       round(min(o, c) * 0.99, 4), round(c, 4), vol))
    by_day = {b["t"][:10]: b for b in out}
    by_day["2026-03-04"] = bar(date(2026, 3, 4), 94.0, 94.5, 85.0, 86.0, 4e6)
    by_day["2026-03-05"] = bar(date(2026, 3, 5), 86.0, 87.0, 85.5, 86.5, 2e6)
    by_day["2026-03-06"] = bar(date(2026, 3, 6), 86.5, 87.0, 86.0, 86.8, 2e6)
    return [by_day[k] for k in sorted(by_day)]


SPY, AAA = spy_bars(), aaa_bars()


def upto(bars, now):
    """The engine's causal view: bars whose session has closed by `now`."""
    clock = now.replace(tzinfo=UTC)
    return [b for b in bars if AVAILABLE(b) <= clock]


@pytest.fixture
def lane(store, monkeypatch):
    """The lane on a FakeStore that already holds its reference rows, with
    the sync and the Alpaca fetch stubbed and recorded."""
    monkeypatch.setenv("SWING_AUTODATA", "1")
    m = _load()
    monkeypatch.setattr(m, "store", store)
    store.insert("SwingMacroDaily", [
        {"id": f"VIX|{d}", "series": "VIX", "date": d.isoformat(), "close": 15.0,
         "source": "cboe"} for d in weekdays(date(2026, 1, 1), date(2026, 3, 6))],
        conflict="replace")
    store.insert("SwingIndexMembership", [
        {"id": "SPX|2026-01-02", "index": "SPX", "date": "2026-01-02",
         "members": ["AAA", "BBB"]}], conflict="replace")
    store.insert("BacktestInstances", {"id": BT_ID, "instance": "swing-paper",
                                       "start-date": "2026-03-03",
                                       "end-date": END.isoformat()}, conflict="replace")
    syncs, fetches = [], []

    def sync(start, end, *, defensive_universe=None, store=None):
        syncs.append((start, end, list(defensive_universe or []), store))
        return sorted({"AAA", "BBB", "SPY", "QQQ"} | set(DEFENSIVE_UNIVERSE))

    def fetch(symbols, start, end, *, key, secret, feed):
        fetches.append((sorted(symbols), start, end, key, secret, feed))
        return {s: list(b) for s, b in (("SPY", SPY), ("AAA", AAA)) if s in symbols}

    monkeypatch.setattr(m.refdata_sync, "sync_reference_data", sync)
    monkeypatch.setattr(m.backtest_bars, "fetch_daily_bars", fetch)
    m.syncs, m.fetches = syncs, fetches
    return m


def cfg(**over):
    c = dict(SWING_DEFAULTS, strategy_swing_enabled=True,
             _telemetry_backtest_id=str(BT_ID), alpaca_key="k", alpaca_secret="s",
             alpaca_data_feed="sip")
    c.update(over)
    return c


def emulator(cash=10_000.0):
    bbe.reset_label_cache()
    return PortfolioEmulator(
        cash, execution_simulator=NextEventExecutionSimulator(ExecutionCostModel(
            version="test-v1", spread_bps=0.0, slippage_bps=0.0, fee_bps=0.0,
            latency=timedelta(0))),
        execution_delay=timedelta(days=1))


def decide(m, now, data, *, emu=None, cache=None, config=None, prices=None):
    return m.StrategySwing().run_once(
        [], prices or {}, now, config or cfg(), {}, data=data,
        portfolio_emulator=emu or emulator(), strategy_cache=cache if cache is not None else {},
        time_increment="86400")


def capture_frames(m, monkeypatch):
    """Record the frames each session's indicators are computed from, and
    the indicators, while still computing them for real."""
    seen, real = [], m.swing_indicators

    def spy(frames, c):
        out = real(frames, c)
        seen.append((frames, out))
        return out

    monkeypatch.setattr(m, "swing_indicators", spy)
    return seen


# -- the first session prepares, once -------------------------------------------------

def test_the_first_session_syncs_and_fetches_its_own_bars_once(lane, monkeypatch):
    capture_frames(lane, monkeypatch)
    cache = {}
    engine = {"BBB": [b for b in upto(AAA, _at("2026-03-03"))]}      # the watchlist
    decide(lane, _at("2026-03-03"), engine, cache=cache)
    decide(lane, _at("2026-03-04"), engine, cache=cache)
    assert lane.syncs == [(FIRST, END, list(DEFENSIVE_UNIVERSE), lane.store)]
    # Every universe symbol the engine does not carry, from 450 days before
    # the first session (market_data.LIVE_WINDOW_DAYS) to the run's end.
    [(symbols, start, end, key, secret, feed)] = lane.fetches
    assert symbols == sorted({"AAA", "SPY", "QQQ"} | set(DEFENSIVE_UNIVERSE))
    assert (start, end, key, secret, feed) == (
        FIRST - timedelta(days=450), END, "k", "s", "sip")
    assert cache["_swing_bt_prep"] == {"run": f"{BT_ID}|{FIRST}|{END}|sip",
                                       "first_session": "2026-03-03",
                                       "end": END.isoformat()}



def test_ai_gate_in_backtest_also_fetches_the_sector_etfs(lane, monkeypatch):
    """ai_gate_in_backtest reads the sector ETF RSI off the run's own bars (no
    live client), so the ETFs join the fetch -- and only then."""
    from swing_trader.ai_analyst import SECTOR_ETFS
    capture_frames(lane, monkeypatch)
    cache = {}
    decide(lane, _at("2026-03-03"), {}, cache=cache,
           config=cfg(ai_gate_in_backtest=True))
    [(symbols, *_rest)] = lane.fetches
    assert symbols == sorted({"AAA", "BBB", "SPY", "QQQ"} | set(DEFENSIVE_UNIVERSE)
                             | set(SECTOR_ETFS))
    assert {"XLK", "XLF", "XLC", "XLRE"} <= set(SECTOR_ETFS)
    assert cache["_swing_bt_prep"]["run"] == f"{BT_ID}|{FIRST}|{END}|sip|ai"

def test_without_a_backtest_id_the_window_runs_through_yesterday(lane, monkeypatch):
    capture_frames(lane, monkeypatch)
    logs = []
    monkeypatch.setattr(lane, "_log", lambda msg, color="white": logs.append((color, msg)))
    decide(lane, _at("2026-03-03"), {}, config=cfg(_telemetry_backtest_id=None))
    expected = max(FIRST, date.today() - timedelta(days=1))
    assert lane.syncs[0][:2] == (FIRST, expected)
    assert any(c == "yellow" and "window end unknown" in m for c, m in logs)


def test_a_preparation_that_raises_is_logged_once_and_the_run_goes_on(lane, monkeypatch):
    seen = capture_frames(lane, monkeypatch)
    logs = []
    monkeypatch.setattr(lane, "_log", lambda msg, color="white": logs.append((color, msg)))

    def broken(*_a, **_k):
        raise RuntimeError("alpaca down")

    monkeypatch.setattr(lane.backtest_bars, "fetch_daily_bars", broken)
    cache = {}
    engine = {"SPY": upto(SPY, _at("2026-03-03"))}
    decide(lane, _at("2026-03-03"), engine, cache=cache)
    decide(lane, _at("2026-03-04"), {"SPY": upto(SPY, _at("2026-03-04"))}, cache=cache)
    assert len(lane.syncs) == 1                                  # not retried every tick
    assert [m for c, m in logs if "own data unavailable" in m and c == "yellow"]
    assert list(seen[0][0]) == ["SPY"]                            # the engine's bars


def test_autodata_off_touches_nothing(lane, monkeypatch):
    monkeypatch.setenv("SWING_AUTODATA", "0")
    seen = capture_frames(lane, monkeypatch)
    decide(lane, _at("2026-03-03"), {"SPY": upto(SPY, _at("2026-03-03"))})
    assert lane.syncs == [] and lane.fetches == []
    assert list(seen[0][0]) == ["SPY"]                    # the engine's bars only


# -- signals come off bars strictly before the session ------------------------------

def test_a_bar_dated_on_or_after_the_session_never_reaches_the_decision(lane, monkeypatch):
    seen = capture_frames(lane, monkeypatch)
    decide(lane, _at("2026-03-03"), {})
    frames, first_ind = seen[-1]
    assert set(frames) == {"SPY", "AAA"}
    for sym, frame in frames.items():
        assert frame.index.max() < datetime(2026, 3, 3), sym

    # The same run with every bar from the session on made absurd decides
    # exactly the same.
    poisoned = {s: [dict(b, o=1e6, h=1e6, l=1e6, c=1e6, v=1) if b["t"][:10] >= "2026-03-03"
                    else b for b in bars] for s, bars in (("SPY", SPY), ("AAA", AAA))}
    lane._OWN_BARS.clear()
    monkeypatch.setattr(lane.backtest_bars, "fetch_daily_bars",
                        lambda symbols, *a, **k: {s: poisoned[s] for s in poisoned
                                                  if s in symbols})
    decide(lane, _at("2026-03-03"), {})
    assert seen[-1][1] == first_ind


def test_the_engines_bars_win_for_a_symbol_it_carries(lane, monkeypatch):
    seen = capture_frames(lane, monkeypatch)
    engine_aaa = [dict(b, c=b["c"] + 1.0) for b in upto(AAA, _at("2026-03-03"))]
    decide(lane, _at("2026-03-03"), {"AAA": engine_aaa})
    frames = seen[-1][0]
    assert frames["AAA"]["Close"].iloc[-1] == pytest.approx(engine_aaa[-1]["c"])
    assert "AAA" not in lane.fetches[0][0]


# -- live: the first run starts a background sync, and never blocks ---------------

def test_the_live_first_run_starts_the_background_sync(lane, monkeypatch):
    started = []
    monkeypatch.setattr(lane.refdata_sync, "start_background_sync",
                        lambda day, **kw: started.append((day, kw)))
    saturday = datetime(2026, 3, 7, 14, 0, tzinfo=UTC)
    out = lane.StrategySwing().run_once([], {}, saturday, cfg(), {}, data=None,
                                        portfolio_emulator=emulator(), strategy_cache={})
    assert out == {}
    assert started == [("2026-03-07", {"defensive_universe": list(DEFENSIVE_UNIVERSE)})]
    monkeypatch.setenv("SWING_AUTODATA", "0")
    lane.StrategySwing().run_once([], {}, saturday, cfg(), {}, data=None,
                                  portfolio_emulator=emulator(), strategy_cache={})
    assert len(started) == 1


# -- end to end: an empty watchlist, through the engine's loader and bar hook ------

def _engine(loaded):
    """broker.py's on-demand loader, submission and bar hook, AST-extracted
    and run as the backtest runs them."""
    def fetch_alpaca_historical_bars(symbols, start, end, key=None, secret=None,
                                     timeframe=None, db_conn=None, feed=None, **_kw):
        loaded.append((sorted(symbols), timeframe, feed))
        return {s: list(b) for s, b in (("SPY", SPY), ("AAA", AAA)) if s in symbols}

    applied, logged = [], []
    ns = harness.extract(
        ["_ensure_backtest_history_for_symbols", "_submit_portfolio_signal",
         "_process_backtest_bar_events", "_backtest_bar_open_resolver"],
        namespace={
            "mode": "backtest", "MODE_BACKTEST": "backtest",
            "_backtest_no_history_symbols": set(),
            "_backtest_fetch_start_dt": datetime(2024, 4, 2),
            "_backtest_fetch_end_dt": datetime(2026, 3, 6, 23, 59, 59),
            "_backtest_alpaca_timeframe": "1Day",
            "get_conn_retry": lambda **_k: None,
            "_resolve_data_brokerage_creds_now": lambda: ("dk", "ds", "sip"),
            "fetch_alpaca_historical_bars": fetch_alpaca_historical_bars,
            "data_feed": "sip",
            "datetime": datetime_module,
            "_bbe": bbe,
            "_bar_time_to_datetime": bar_time_to_datetime,
            "_backtest_bar_interval": lambda: timedelta(days=1),
            "_aware_backtest_clock": (lambda t: t.replace(tzinfo=UTC) if t.tzinfo is None
                                      else t.astimezone(UTC)),
            "_backtest_bar_availability_resolver": lambda: AVAILABLE,
            "_backtest_fill_snapshot_marks": (
                lambda portfolio, prices, data, now: dict(prices or {})),
            "_apply_backtest_confirmed_fill_state": lambda fill, marks: applied.append(fill),
            "_log": lambda message, color=None: logged.append(message),
        })
    return ns, applied


def test_an_empty_watchlist_plans_off_its_own_bars_and_fills_through_the_loader(lane):
    loaded = []
    ns, fills = _engine(loaded)
    emu, cache, data = emulator(), {}, {}          # the engine fetched no bars at all

    # 2026-03-03: the lane decides off its own bars and emits AAA.
    now = _at("2026-03-03")
    out = decide(lane, now, {s: upto(b, now) for s, b in data.items()}, emu=emu, cache=cache)
    assert out.get("AAA") == 1
    hint = out["_nexus_position_sizes"]["AAA"]
    assert hint["fill_at_next_open"] is True and hint["whole_shares"] is True
    assert "AAA" in out["_nexus_discovered"] and "AAA" in out["_nexus_executable_buys"]

    # The engine: AAA is not in `data`, so the discovered-symbol path loads it
    # from the loader, prices it off the last visible close and submits.
    assert ns["_ensure_backtest_history_for_symbols"](data, ["AAA"]) == ["AAA"]
    assert loaded == [(["AAA"], "1Day", "sip")]
    price = upto(data["AAA"], now)[-1]["c"]
    ns["_submit_portfolio_signal"](
        emu, "AAA", 1, price, timestamp=now, cash_per_trade=hint["buy_cash"],
        order_source="main_signal",
        execution_hints=bbe.execution_hint_kwargs(hint, 1))
    assert emu.pending_execution_symbols() == ("AAA",)

    # 2026-03-04: the bar hook fills the buy at the 2026-03-03 open.
    now = _at("2026-03-04")
    ns["_process_backtest_bar_events"](emu, data, {}, now)
    buy = [f for f in fills if f.side == "buy"]
    assert len(buy) == 1
    assert buy[0].price == pytest.approx(next(b["o"] for b in AAA if b["t"][:10] == "2026-03-03"))
    assert buy[0].quote_timestamp == datetime(2026, 3, 3, 14, 30, tzinfo=UTC)
    shares = emu.get_positions()["AAA"]
    assert shares == int(hint["buy_cash"] // price) > 0
    # The lane's next session reuses its preparation: no second sync or fetch.
    decide(lane, now, {s: upto(b, now) for s, b in data.items()}, emu=emu, cache=cache)
    assert len(lane.syncs) == 1 and len(lane.fetches) == 1

    # 2026-03-05: 2026-03-04 traded through the -6% stop; the bracket leg fills.
    now = _at("2026-03-05")
    ns["_process_backtest_bar_events"](emu, data, {}, now)
    sells = [f for f in fills if f.side == "sell"]
    assert len(sells) == 1 and sells[0].source.startswith("bracket_sl")
    assert sells[0].price == pytest.approx(hint["bracket"]["stop_loss_price"])
    assert emu.get_positions().get("AAA", 0.0) == 0.0
