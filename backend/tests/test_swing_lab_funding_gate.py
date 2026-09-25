"""FW-bt-I1: the swing lab's same-tick exit funds its entry THROUGH the
broker's backtest buy gate, not only in the strategy's own arithmetic.

broker.py cannot be imported under pytest (argparse and the main loop run at
import), and the buy gate is inline module-level loop code, not a function.
So the three pieces of that loop the funding depends on are lifted out of
broker.py's syntax tree and executed, verbatim, in a namespace that binds
what they read:

  1. the per-cycle credit switch   `_scp_enabled = True` .. `_scp_credit_on = ...`
  2. the sell-submit booking       `if _scp_credit_on and decision == -1 ...`
  3. the non-anchor buy gate       `_cash_floor_pct = ...` .. `cash_to_use = min(...)`

Around them run the real producers: StrategySwing's backtest decision, the
lab document scripts/swing_lab_setup.py builds, broker.py's
`_submit_portfolio_signal` and `_process_backtest_bar_events`, and a
PortfolioEmulator from `create_backtest_emulator` -- the broker's own factory.
The book is test_f1_same_tick_exit_proceeds_fund_the_entry_pass's: $100 cash,
an RSI exit worth 110 x $104 = $11,440, and six other names.
"""
from __future__ import annotations

import ast
import importlib.util
import math
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ROOT = os.path.dirname(_BACKEND)
_TESTS = os.path.dirname(os.path.abspath(__file__))
for _p in (_BACKEND, _TESTS):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import backtest_bar_events as bbe  # noqa: E402
import swing_broker_harness as harness  # noqa: E402
from bar_time import bar_time_to_datetime  # noqa: E402
from nexus_broker_utils import buy_ceiling  # noqa: E402
from portfolio_emulator import PortfolioEmulator, create_backtest_emulator  # noqa: E402
from swing_port_calendar_fixtures import AVAILABLE, daily_bar  # noqa: E402
from swing_trader.constants import SWING_DEFAULTS  # noqa: E402

UTC = timezone.utc
MODE_BACKTEST, MODE_LIVE = "BACKTEST", "LIVE"
TICK = datetime(2026, 6, 2, 12, 0)            # 08:00 ET Tuesday, the broker's naive UTC
NEXT_TICK = TICK + timedelta(days=1)
HELD = {"EEE": 110.0, **{f"H{i}": 100.0 for i in range(1, 7)}}


# -- lifting inline main-loop code out of broker.py ---------------------------

def _statement_lists():
    for node in ast.walk(harness.tree()):
        for field in ("body", "orelse", "finalbody"):
            stmts = getattr(node, field, None)
            if isinstance(stmts, list) and stmts and isinstance(stmts[0], ast.stmt):
                yield stmts


def loop_block(first, last=None):
    """The consecutive broker.py statements from the one whose first line
    starts with `first` through the one starting with `last` (default: just
    the first), compiled as they stand in the file."""
    lines = harness.source().splitlines(keepends=True)

    def head(stmt):
        return lines[stmt.lineno - 1][stmt.col_offset:]

    found = []
    for stmts in _statement_lists():
        starts = [k for k, s in enumerate(stmts) if head(s).startswith(first)]
        for i in starts:
            ends = ([k for k in range(i, len(stmts)) if head(stmts[k]).startswith(last)]
                    if last else [i])
            if ends:
                found.append((stmts[i], stmts[ends[0]]))
    assert len(found) == 1, f"{first!r}..{last!r}: {len(found)} matches in broker.py"
    begin, end = found[0]
    block = "".join(lines[begin.lineno - 1:end.end_lineno])
    # Kept at its original indentation under an `if True:`, so comments and
    # continuation lines parse exactly as they do in the file.
    return compile("if True:\n" + block, harness.BROKER_PATH, "exec")


CREDIT_SWITCH = loop_block("_scp_enabled = True", "_scp_credit_on = ")
SELL_BOOKING = loop_block(
    "if _scp_credit_on and decision == -1 and _mpg_submit_ok and _scp_enabled:")
BUY_GATE = loop_block("_cash_floor_pct = float((nexus_position_sizes",
                      "cash_to_use = min(cash_per_trade, available)")


def _broker():
    logged = []
    ns = harness.extract(
        ["_submit_portfolio_signal", "_backtest_credit_pending_sell_proceeds",
         "_core_sleeve_cfg_raw", "_core_sleeve_cfg", "_residual_sleeve_config",
         "_chop_ret20_cfg", "_process_backtest_bar_events", "_backtest_bar_open_resolver"],
        namespace={
            "math": math, "datetime": __import__("datetime"), "_bbe": bbe,
            "_bar_time_to_datetime": bar_time_to_datetime,
            "_backtest_bar_interval": lambda: timedelta(days=1),
            "_aware_backtest_clock": (
                lambda t: t.replace(tzinfo=UTC) if t.tzinfo is None else t.astimezone(UTC)),
            "_backtest_bar_availability_resolver": lambda: AVAILABLE,
            "_backtest_fill_snapshot_marks": lambda p, prices, data, now: dict(prices or {}),
            "_apply_backtest_confirmed_fill_state": lambda fill, marks: None,
            "_log": lambda message, color=None: logged.append(str(message)),
            "buy_ceiling": buy_ceiling,
            "MODE_BACKTEST": MODE_BACKTEST, "MODE_LIVE": MODE_LIVE,
        })
    ns["logged"] = logged
    return ns


# -- the swing lab, end to end ---------------------------------------------------

def _setup_script():
    path = os.path.join(_ROOT, "scripts", "swing_lab_setup.py")
    spec = importlib.util.spec_from_file_location("_swing_lab_setup_gate", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _strategy_module():
    path = os.path.join(_BACKEND, "strategies", "strategy_swing.py")
    spec = importlib.util.spec_from_file_location("strategies.strategy_swing", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def ind(close, rsi=45.0, rsi_prev=40.0, **kw):
    out = {"close": close, "volume": 2e6, "rsi": rsi, "rsi_prev": rsi_prev,
           "macd_hist": 0.2, "macd_hist_prev": 0.1, "macd_hist_prev2": 0.05,
           "sma200": close * 0.9, "vol_avg20": 1e6, "adx": 20.0}
    out.update(kw)
    return out


@pytest.fixture
def swing(store, monkeypatch):
    m = _strategy_module()
    monkeypatch.setattr(m, "store", store)
    store.insert("SwingMacroDaily", [
        {"id": "VIX|2026-06-01", "series": "VIX", "date": "2026-06-01", "close": 15.0,
         "source": "cboe"}], conflict="replace")
    store.insert("SwingIndexMembership", [
        {"id": "SPX|2026-01-02", "index": "SPX", "date": "2026-01-02",
         "members": ["AAA", "EEE"] + [f"H{i}" for i in range(1, 7)]}], conflict="replace")
    store.insert("SwingSectorMap", [
        {"id": s, "symbol": s, "sector": sec, "as_of": "2026-09-24", "source": "yfinance"}
        for s, sec in [("AAA", "technology"), ("EEE", "energy")]
        + [(f"H{i}", f"sector{i}") for i in range(1, 7)]], conflict="replace")
    return m


def _f1_book(specs, ns):
    """The F1 book on the broker's own backtest emulator (86400 s increment)."""
    emu = create_backtest_emulator(initial_cash=71_540.0, taker_fee=0.0, is_crypto=False,
                                   execution_delay=timedelta(days=1))
    PortfolioEmulator._PASSIVE_OVERRIDE = None
    emu._cash = 100.0
    emu._positions = dict(HELD)
    emu._trades = [{"action": "buy", "ticker": s, "price": 100.0} for s in HELD]
    emu._last_prices = {"EEE": 104.0, **{f"H{i}": 100.0 for i in range(1, 7)}}
    # broker.py:18417: the emulator's own credit, through the any-lane helper.
    emu.credit_pending_sell_proceeds = ns["_backtest_credit_pending_sell_proceeds"](specs)
    return emu


def _run_tick(swing, monkeypatch, specs, *, aaa_price=100.0):
    """One broker backtest tick of the lab: decide, then submit the sells and
    the buys as the main loop does, through the lifted gate."""
    ns = _broker()
    emu = _f1_book(specs, ns)
    indicators = {"SPY": ind(450.0, rsi=60.0, sma200=400.0), "AAA": ind(aaa_price),
                  "EEE": ind(104.0, rsi=72.0, rsi_prev=68.0),
                  **{f"H{i}": ind(100.0, rsi=60.0) for i in range(1, 7)}}
    monkeypatch.setattr(swing, "swing_indicators", lambda frames, c: indicators)
    [lane] = specs
    payload = swing.StrategySwing().run_once(
        sorted(indicators), {s: v["close"] for s, v in indicators.items()},
        TICK.replace(tzinfo=UTC), lane["config"], {}, data={s: [] for s in indicators},
        portfolio_emulator=emu, strategy_cache={})
    sizes = payload["_nexus_position_sizes"]
    assert payload["_nexus_sell_enforcement"] == ["EEE"]
    assert payload["_nexus_executable_buys"] == ["AAA"]

    loop = dict(ns, _cached_strategies=specs, mode=MODE_BACKTEST, portfolio_emulator=emu,
                nexus_position_sizes=sizes, strategy_results=[], _anchor_policy=None,
                reserved_total=0.0)       # no capital_pct lane on the lab document
    exec(CREDIT_SWITCH, loop)
    # Sells sort first in _exec_order.
    loop.update(symbol="EEE", decision=-1, price=104.0, sell_fraction=1.0)
    result = ns["_submit_portfolio_signal"](
        emu, "EEE", -1, 104.0, timestamp=TICK, cash_per_trade=1000.0, sell_fraction=1.0,
        order_source="main_signal",
        execution_hints=bbe.execution_hint_kwargs(sizes["EEE"], -1))
    loop["_mpg_submit_ok"] = bool(result)
    exec(SELL_BOOKING, loop)
    # broker.py:19153 `cash_per_trade = nexus_hint['buy_cash']`.
    loop.update(symbol="AAA", decision=1, price=aaa_price,
                cash_per_trade=sizes["AAA"]["buy_cash"])
    exec(BUY_GATE, loop)
    ns["_submit_portfolio_signal"](
        emu, "AAA", 1, aaa_price, timestamp=TICK, cash_per_trade=loop["cash_to_use"],
        order_source="main_signal",
        execution_hints=bbe.execution_hint_kwargs(sizes["AAA"], 1))
    return ns, emu, sizes, loop


def _next_session(ns, emu, aaa_price):
    data = {"EEE": [daily_bar("2026-06-02", 104.0, 105.0, 103.0, 104.5)],
            "AAA": [daily_bar("2026-06-02", aaa_price, aaa_price * 1.01,
                              aaa_price * 0.99, aaa_price)]}
    return ns["_process_backtest_bar_events"](
        emu, data, {"EEE": 104.5, "AAA": aaa_price}, NEXT_TICK)


def _full_entry(emu, buy_cash, price):
    """What the entry buys when nothing clamps it below the strategy's
    buy_cash: the emulator's own whole-share count at that price."""
    return math.floor(emu._execution_simulator.affordable_buy_quantity(
        buy_cash, price, symbol="AAA") + 1e-9)


def test_the_lab_document_carries_both_funding_flags():
    lane = _setup_script().swing_lane(lab=True)
    assert lane["config"]["backtest_credit_pending_sell_proceeds"] is True
    assert lane["config"]["backtest_credit_sell_proceeds_enabled"] is True
    paper = _setup_script().swing_lane(lab=False)["config"]
    assert "backtest_credit_sell_proceeds_enabled" not in paper


@pytest.mark.parametrize("aaa_price", [100.0, 30.0])
def test_f1_the_exit_funds_the_full_entry_through_the_real_buy_gate(
        swing, monkeypatch, aaa_price):
    specs = _setup_script().lab_payload()["strategies"]
    ns, emu, sizes, loop = _run_tick(swing, monkeypatch, specs, aaa_price=aaa_price)
    buy_cash = sizes["AAA"]["buy_cash"]
    equity = 100.0 + 110 * 104.0 + 6 * 100 * 100.0
    assert buy_cash == round(equity * 0.125, 2)
    assert loop["_scp_credit_on"] is True
    assert loop["_scp_sell_proceeds"] == [pytest.approx(110 * 104.0)]
    # The gate lets the whole allocation through: $100 + 95% of $11,440.
    assert loop["cash_to_use"] == pytest.approx(buy_cash)
    [order] = [o for o in emu._execution_simulator.pending_orders if o.symbol == "AAA"]
    expected = _full_entry(emu, buy_cash, aaa_price)
    assert order.quantity == expected and expected >= 88

    fills = _next_session(ns, emu, aaa_price)
    assert [(f.symbol, f.side) for f in fills] == [("EEE", "sell"), ("AAA", "buy")]
    buy = fills[1]
    assert buy.cumulative_quantity == expected
    assert emu.get_positions()["AAA"] == expected
    assert "EEE" not in {s for s, q in emu.get_positions().items() if q > 0}
    assert emu.get_cash() >= 0.0


@pytest.mark.parametrize("aaa_price,without_flag", [(100.0, None), (30.0, 3.0)])
def test_f1_without_the_gate_flag_the_entry_is_skipped_or_a_runt(
        swing, monkeypatch, aaa_price, without_flag):
    """The bug the flag fixes: the gate clamps the entry to the $100 of raw
    cash (backtest-review I1)."""
    specs = _setup_script().lab_payload()["strategies"]
    specs[0]["config"].pop("backtest_credit_sell_proceeds_enabled", None)
    ns, emu, sizes, loop = _run_tick(swing, monkeypatch, specs, aaa_price=aaa_price)
    assert loop["_scp_credit_on"] is False
    assert loop["cash_to_use"] == pytest.approx(100.0)
    quantities = [o.quantity for o in emu._execution_simulator.pending_orders
                  if o.symbol == "AAA"]
    assert quantities == ([] if without_flag is None else [without_flag])
