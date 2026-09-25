"""StrategySwing: header contract and the backtest path (spec §5.1)."""
import ast
import importlib.util
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

import pytest

# ONLY backend/ on the path; the wrapper is loaded by file path (adding
# backend/strategies/ would shadow strategy_x and friends).
_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from swing_trader.constants import SWING_DEFAULTS  # noqa: E402

PATH = os.path.join(_backend, "strategies", "strategy_swing.py")
TICK = datetime(2026, 6, 2, 12, 0, tzinfo=timezone.utc)      # 08:00 ET Tuesday
SESSION = "2026-06-02"


def _load():
    spec = importlib.util.spec_from_file_location("strategies.strategy_swing", PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def ind(close, rsi=45.0, rsi_prev=40.0, **kw):
    out = {"close": close, "volume": 2e6, "rsi": rsi, "rsi_prev": rsi_prev,
           "macd_hist": 0.2, "macd_hist_prev": 0.1, "macd_hist_prev2": 0.05,
           "sma200": close * 0.9, "vol_avg20": 1e6, "adx": 20.0}
    out.update(kw)
    return out


SPY = ind(450.0, rsi=60.0, sma200=400.0)


class BtEmulator:
    def __init__(self, cash=100_000.0, positions=None, trades=None, pending=()):
        self._cash = cash
        self._positions = dict(positions or {})
        self._trades = list(trades or [])
        self._pending = tuple(pending)

    def get_cash(self):
        return self._cash

    def get_buying_power(self, reserved=0.0, *, prices=None):
        return self._cash

    def get_positions(self):
        return dict(self._positions)

    def get_portfolio_value(self, prices):
        return self._cash + sum(q * float((prices or {}).get(s, 0.0))
                                for s, q in self._positions.items())

    def get_trade_history(self):
        return list(self._trades)

    def pending_execution_symbols(self):
        return self._pending


@pytest.fixture
def mod(store, monkeypatch):
    m = _load()
    monkeypatch.setattr(m, "store", store)
    store.insert("SwingMacroDaily", [
        {"id": "VIX|2026-05-29", "series": "VIX", "date": "2026-05-29", "close": 14.0, "source": "cboe"},
        {"id": "VIX|2026-06-01", "series": "VIX", "date": "2026-06-01", "close": 15.0, "source": "cboe"},
        {"id": "VIX|2026-06-02", "series": "VIX", "date": "2026-06-02", "close": 40.0, "source": "cboe"},
    ], conflict="replace")
    store.insert("SwingIndexMembership", [
        {"id": "SPX|2026-01-02", "index": "SPX", "date": "2026-01-02",
         "members": ["AAA", "BBB", "CCC", "XOM"]}], conflict="replace")
    store.insert("SwingSectorMap", [
        {"id": s, "symbol": s, "sector": sec, "as_of": "2026-09-24", "source": "yfinance"}
        for s, sec in (("AAA", "technology"), ("BBB", "technology"),
                       ("CCC", "energy"), ("ZZZ", "utilities"))], conflict="replace")
    return m


def cfg(**over):
    c = dict(SWING_DEFAULTS, strategy_swing_enabled=True)
    c.update(over)
    return c


def run(mod, monkeypatch, indicators, *, emu=None, cache=None, at=TICK, config=None,
        data=None):
    monkeypatch.setattr(mod, "swing_indicators", lambda frames, c: indicators)
    return mod.StrategySwing().run_once(
        sorted(indicators), {s: v["close"] for s, v in indicators.items()}, at,
        config or cfg(), {}, data=data if data is not None else {s: [] for s in indicators},
        portfolio_emulator=emu or BtEmulator(), strategy_cache=cache if cache is not None else {})


# -- header and broker contract ----------------------------------------------

def test_the_schema_header_is_exactly_the_defaults():
    header = re.search(r"# INTELLISTOCK_SCHEMA: (.*)", open(PATH).read())
    schema = json.loads(header.group(1))
    assert schema["strategy"] == "strategy_swing"
    assert schema["execution_scope"] == "run_once"
    assert schema["decision_phase"] == "pre"
    assert schema["execution_position"] == 10
    assert schema["config"] == SWING_DEFAULTS
    assert list(schema["config"]) == list(SWING_DEFAULTS)


def test_the_header_is_line_one_and_carries_a_description():
    from strategies_meta import _parse_header_meta
    text = open(PATH).read()
    assert text.startswith("# INTELLISTOCK_SCHEMA: ")
    schema, description = _parse_header_meta(text)
    assert schema["strategy"] == "strategy_swing" and "ST" in description


def test_the_class_name_matches_what_the_broker_derives():
    broker = os.path.join(_backend, "broker.py")
    tree = ast.parse(open(broker).read())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
              and n.name == "_strategy_name_to_module_and_class")
    ns = {"re": __import__("re")}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), broker, "exec"), ns)
    assert ns["_strategy_name_to_module_and_class"]("strategy_swing") == (
        "strategy_swing", "StrategySwing")
    assert hasattr(_load().StrategySwing, "run_once")


def test_disabled_or_blind_is_inert(mod, monkeypatch):
    assert run(mod, monkeypatch, {"SPY": SPY, "AAA": ind(100.0)},
               config=cfg(strategy_swing_enabled=False)) == {}
    out = mod.StrategySwing().run_once(["AAA"], {}, TICK, cfg(), {}, data={},
                                       portfolio_emulator=None, strategy_cache={})
    assert out == {}


# -- entries -----------------------------------------------------------------

def test_a_backtest_entry_is_a_prior_close_bracket_filled_at_the_next_open(mod, monkeypatch):
    out = run(mod, monkeypatch, {"SPY": SPY, "AAA": ind(100.0)})
    assert out["AAA"] == 1
    sizes = out["_nexus_position_sizes"]
    assert sizes["_cash_reserve_floor_pct"] == 0.0
    assert sizes["AAA"] == {"buy_cash": 12_500.0,
                            "bracket": {"take_profit_price": 109.0, "stop_loss_price": 94.0},
                            "whole_shares": True, "fill_at_next_open": True}
    assert out["_nexus_discovered"] == ["AAA"]
    assert out["_nexus_executable_buys"] == ["AAA"]
    assert out["_nexus_sell_enforcement"] == []
    assert out["_nexus_action_intents"] == {"AAA": "swing_entry"}


def test_the_sector_set_is_updated_after_each_buy(mod, monkeypatch):
    # fix 4: AAA and BBB are both technology; ST entered both in one run.
    out = run(mod, monkeypatch, {"SPY": SPY, "AAA": ind(100.0), "BBB": ind(50.0),
                                 "CCC": ind(80.0)})
    assert out["_nexus_executable_buys"] == ["AAA", "CCC"]


def test_only_point_in_time_members_are_entered(mod, monkeypatch):
    out = run(mod, monkeypatch, {"SPY": SPY, "ZZZ": ind(10.0), "CCC": ind(80.0)})
    assert out["_nexus_executable_buys"] == ["CCC"]


def test_without_a_membership_row_entries_are_refused(mod, monkeypatch, store):
    store.delete("SwingIndexMembership", "SPX|2026-01-02")
    lines = []
    monkeypatch.setattr(mod, "_log", lambda msg, color="white": lines.append(msg))
    assert run(mod, monkeypatch, {"SPY": SPY, "AAA": ind(100.0)}) == {}
    assert any("survivorship" in line for line in lines)


def test_the_vix_row_dated_the_session_is_invisible(mod, monkeypatch):
    # VIX|2026-06-02 = 40 would block; the rule reads 2026-06-01 (15).
    assert run(mod, monkeypatch, {"SPY": SPY, "AAA": ind(100.0)})["AAA"] == 1


def test_a_vix_gap_blocks_entries_and_logs_once(mod, monkeypatch):
    lines = []
    monkeypatch.setattr(mod, "_log", lambda msg, color="white": lines.append(msg))
    late = datetime(2026, 6, 12, 12, 0, tzinfo=timezone.utc)
    cache = {}
    assert run(mod, monkeypatch, {"SPY": SPY, "AAA": ind(100.0)}, at=late, cache=cache) == {}
    cache.pop(mod._BT_SESSION_KEY)          # force a second evaluation of the session
    run(mod, monkeypatch, {"SPY": SPY, "AAA": ind(100.0)}, at=late, cache=cache)
    assert sum("VIX unavailable" in line for line in lines) == 1


def test_slots_and_buying_power_are_sts(mod, monkeypatch):
    held = {f"H{i}": 10.0 for i in range(7)}
    out = run(mod, monkeypatch, {"SPY": SPY, "AAA": ind(100.0), "CCC": ind(80.0)},
              emu=BtEmulator(positions=held))
    assert out["_nexus_executable_buys"] == ["AAA"]            # 8 - 7 = one slot
    # Equity $100,000 (cash + a $95,000 position priced through `prices`) but
    # $5,000 buying power: below half of the $12,500 allocation, so ST skips.
    rich = BtEmulator(cash=5_000.0, positions={"H0": 950.0})
    indicators = {"SPY": SPY, "AAA": ind(100.0), "H0": ind(100.0, rsi=60.0)}
    assert run(mod, monkeypatch, indicators, emu=rich) == {}


def test_bear_mode_scans_the_defensive_universe_after_n_blocked_sessions(mod, monkeypatch):
    blocked_spy = ind(390.0, rsi=60.0, sma200=400.0)
    cache = {}
    days = [datetime(2026, 6, d, 12, 0, tzinfo=timezone.utc) for d in (2, 3)]
    indicators = {"SPY": blocked_spy, "XLP": ind(80.0), "AAA": ind(100.0)}
    assert run(mod, monkeypatch, indicators, at=days[0], cache=cache,
               config=cfg(bear_regime_days=2)) == {}
    out = run(mod, monkeypatch, indicators, at=days[1], cache=cache,
              config=cfg(bear_regime_days=2))
    assert cache[mod._BEAR_KEY]["blocked_days"] == 2
    assert out["_nexus_executable_buys"] == ["XLP"]
    assert out["_nexus_action_intents"] == {"XLP": "swing_defensive_entry"}


def test_one_decision_per_session(mod, monkeypatch):
    cache = {}
    assert run(mod, monkeypatch, {"SPY": SPY, "AAA": ind(100.0)}, cache=cache)["AAA"] == 1
    later = TICK + timedelta(hours=2)
    assert run(mod, monkeypatch, {"SPY": SPY, "AAA": ind(100.0)}, cache=cache, at=later) == {}


def test_the_ai_gate_and_earnings_are_off_in_backtests(mod, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("no AI or earnings call in a backtest")

    monkeypatch.setattr(mod.ai_analyst, "analyse", boom)
    monkeypatch.setattr(mod.ai_analyst, "days_until_earnings", boom)
    assert run(mod, monkeypatch, {"SPY": SPY, "AAA": ind(100.0)})["AAA"] == 1


# -- exits -------------------------------------------------------------------

def test_the_rsi_cross_and_close_stop_exits_sell_at_the_next_open(mod, monkeypatch):
    emu = BtEmulator(positions={"AAA": 10.0, "CCC": 5.0},
                     trades=[{"action": "buy", "ticker": "AAA", "price": 100.0},
                             {"action": "buy", "ticker": "CCC", "price": 100.0}])
    out = run(mod, monkeypatch, {"SPY": SPY,
                                 "AAA": ind(104.0, rsi=72.0, rsi_prev=68.0),
                                 "CCC": ind(93.0, rsi=40.0, rsi_prev=45.0)}, emu=emu)
    assert out["AAA"] == -1 and out["CCC"] == -1
    assert out["_nexus_position_sizes"]["AAA"] == {"sell_fraction": 1.0, "fill_at_next_open": True}
    assert out["_nexus_action_intents"] == {"AAA": "swing_rsi_exit", "CCC": "swing_stop_exit"}
    assert out["_nexus_sell_enforcement"] == ["AAA", "CCC"]


# -- review focus 1: NaN close on the scan day ---------------------------------

def test_a_nan_close_on_the_last_bar_uses_the_previous_session(mod, store):
    start = datetime(2025, 5, 1, 4, 0, tzinfo=timezone.utc)
    bars = []
    for i in range(260):
        c = 100.0 + (i % 7) - 3 + i * 0.1
        bars.append({"t": (start + timedelta(days=i)).isoformat(), "o": c, "h": c + 1,
                     "l": c - 1, "c": c, "v": 1_000_000})
    last_good = bars[-2]["c"]
    bars[-1]["c"] = None
    day_after = datetime.fromisoformat(bars[-1]["t"]) + timedelta(days=1, hours=8)
    cache = {}
    mod.StrategySwing().run_once(["AAA"], {}, day_after, cfg(), {},
                                 data={"AAA": bars, "SPY": bars},
                                 portfolio_emulator=BtEmulator(), strategy_cache=cache)
    memo = cache[mod._IND_MEMO_KEY]["ind"]
    assert memo["AAA"]["close"] == last_good


# -- controller rulings (G6) ---------------------------------------------------

def test_f1_same_tick_exit_proceeds_fund_the_entry_pass(mod, monkeypatch):
    # $100 cash, 7 names held, an RSI exit worth 110 x $104 = $11,440. ST
    # credited the exit before the entry pass (paper_trader.py:585), so AAA
    # is entered with min(equity x 0.125, $11,540).
    held = {"EEE": 110.0, **{f"H{i}": 100.0 for i in range(1, 7)}}
    indicators = {"SPY": SPY, "AAA": ind(100.0),
                  "EEE": ind(104.0, rsi=72.0, rsi_prev=68.0),
                  **{f"H{i}": ind(100.0, rsi=60.0) for i in range(1, 7)}}
    emu = BtEmulator(cash=100.0, positions=held,
                     trades=[{"action": "buy", "ticker": "EEE", "price": 100.0}])
    out = run(mod, monkeypatch, indicators, emu=emu)
    assert out["EEE"] == -1
    assert out["_nexus_executable_buys"] == ["AAA"]
    equity = 100.0 + 110 * 104.0 + 6 * 100 * 100.0
    assert out["_nexus_position_sizes"]["AAA"]["buy_cash"] == round(equity * 0.125, 2)


def test_f6_a_holiday_decides_nothing(mod, monkeypatch, store):
    # 2026-06-19 (Juneteenth) is not an NYSE session; the same book trades on
    # the next one.
    store.insert("SwingMacroDaily", {"id": "VIX|2026-06-18", "series": "VIX",
                                     "date": "2026-06-18", "close": 15.0,
                                     "source": "cboe"}, conflict="replace")
    indicators = {"SPY": SPY, "AAA": ind(100.0), "XOM": ind(104.0, rsi=72.0, rsi_prev=68.0)}

    def emu():
        return BtEmulator(positions={"XOM": 10.0},
                          trades=[{"action": "buy", "ticker": "XOM", "price": 100.0}])

    holiday = datetime(2026, 6, 19, 12, 0, tzinfo=timezone.utc)
    assert run(mod, monkeypatch, indicators, emu=emu(), at=holiday) == {}
    monday = datetime(2026, 6, 22, 12, 0, tzinfo=timezone.utc)
    out = run(mod, monkeypatch, indicators, emu=emu(), at=monday)
    assert out["XOM"] == -1 and out["AAA"] == 1


def test_f6_a_symbol_whose_order_is_still_pending_is_not_sold_twice(mod, monkeypatch):
    emu = BtEmulator(positions={"XOM": 10.0, "CCC": 5.0}, pending=("XOM",),
                     trades=[{"action": "buy", "ticker": "XOM", "price": 100.0},
                             {"action": "buy", "ticker": "CCC", "price": 100.0}])
    out = run(mod, monkeypatch, {"SPY": SPY,
                                 "XOM": ind(104.0, rsi=72.0, rsi_prev=68.0),
                                 "CCC": ind(104.0, rsi=72.0, rsi_prev=68.0)}, emu=emu)
    assert out["_nexus_sell_enforcement"] == ["CCC"]


def _bars(start, step, n=40, close=100.0):
    return [{"t": (start + i * step).isoformat(), "o": close, "h": close + 1,
             "l": close - 1, "c": close, "v": 1_000_000} for i in range(n)]


def test_g2_i2_sub_daily_bars_are_refused_and_logged_once(mod, monkeypatch):
    lines = []
    monkeypatch.setattr(mod, "_log", lambda msg, color="white": lines.append(msg))
    indicators = {"SPY": SPY, "AAA": ind(100.0)}
    quarter_hours = _bars(datetime(2026, 5, 29, 13, 30, tzinfo=timezone.utc),
                          timedelta(minutes=15))
    cache = {}
    assert run(mod, monkeypatch, indicators, cache=cache,
               data={"SPY": quarter_hours, "AAA": quarter_hours}) == {}
    wed = TICK + timedelta(days=1)
    assert run(mod, monkeypatch, indicators, cache=cache, at=wed,
               data={"SPY": quarter_hours, "AAA": quarter_hours}) == {}
    assert sum("daily bars" in line for line in lines) == 1
    # A granularity below a day refuses even before a bar can be judged.
    monkeypatch.setattr(mod, "swing_indicators", lambda frames, c: indicators)
    assert mod.StrategySwing().run_once(
        ["AAA"], {"SPY": 450.0, "AAA": 100.0}, TICK, cfg(), {},
        data={"SPY": [], "AAA": []}, portfolio_emulator=BtEmulator(),
        strategy_cache={}, time_increment="900") == {}
    # Daily bars trade.
    days = _bars(datetime(2026, 4, 1, 4, 0, tzinfo=timezone.utc), timedelta(days=1))
    assert run(mod, monkeypatch, indicators, data={"SPY": days, "AAA": days})["AAA"] == 1


def test_g1_minor_5_a_bad_frame_skips_only_that_symbol(mod, monkeypatch):
    import pandas as pd
    lines = []
    monkeypatch.setattr(mod, "_log", lambda msg, color="white": lines.append(msg))
    idx = pd.date_range("2025-01-01", periods=260, freq="D")
    good = pd.DataFrame({"Open": 100.0, "High": 101.0, "Low": 99.0,
                         "Close": [100.0 + (i % 7) for i in range(260)],
                         "Volume": 1e6}, index=idx)
    no_high = good.drop(columns=["High"])
    out = mod.swing_indicators({"AAA": no_high, "BBB": good}, cfg())
    assert "BBB" in out and "AAA" not in out
    assert any("AAA" in line and "skipped" in line for line in lines)


def test_g1_minor_5_a_bad_value_skips_only_that_symbol(mod, monkeypatch):
    lines = []
    monkeypatch.setattr(mod, "_log", lambda msg, color="white": lines.append(msg))
    emu = BtEmulator(positions={"BBB": 10.0, "XOM": 5.0},
                     trades=[{"action": "buy", "ticker": "BBB", "price": 100.0},
                             {"action": "buy", "ticker": "XOM", "price": 100.0}])
    out = run(mod, monkeypatch, {"SPY": SPY,
                                 "AAA": ind(100.0, rsi="n/a"),
                                 "CCC": ind(80.0),
                                 "BBB": dict(ind(100.0), close="n/a"),
                                 "XOM": ind(104.0, rsi=72.0, rsi_prev=68.0)}, emu=emu)
    assert out["_nexus_executable_buys"] == ["CCC"]
    assert out["_nexus_sell_enforcement"] == ["XOM"]
    assert any("AAA" in line for line in lines) and any("BBB" in line for line in lines)


def test_a6_hint_flags_are_python_bools(mod, monkeypatch):
    emu = BtEmulator(positions={"XOM": 5.0},
                     trades=[{"action": "buy", "ticker": "XOM", "price": 100.0}])
    out = run(mod, monkeypatch, {"SPY": SPY, "AAA": ind(100.0),
                                 "XOM": ind(104.0, rsi=72.0, rsi_prev=68.0)}, emu=emu)
    flags = [(sym, key, v) for sym, hint in out["_nexus_position_sizes"].items()
             if isinstance(hint, dict)
             for key, v in hint.items() if key in ("whole_shares", "fill_at_next_open")]
    assert {(s, k) for s, k, _ in flags} == {("AAA", "whole_shares"),
                                             ("AAA", "fill_at_next_open"),
                                             ("XOM", "fill_at_next_open")}
    assert all(type(v) is bool and v is True for _, _, v in flags)


# -- review fix round 1 (G6) -----------------------------------------------------

def test_fix_b_emit_coerces_numpy_bools_to_python_bools(mod):
    import numpy as np
    out = mod._emit({"AAA": 1, "XOM": -1},
                    {"AAA": {"buy_cash": 1.0, "whole_shares": np.bool_(True),
                             "fill_at_next_open": np.bool_(True)},
                     "XOM": {"sell_fraction": 1.0, "fill_at_next_open": np.bool_(True)}}, {})
    flags = [v for s in ("AAA", "XOM") for k, v in out["_nexus_position_sizes"][s].items()
             if k in ("whole_shares", "fill_at_next_open")]
    assert len(flags) == 3 and all(type(v) is bool and v is True for v in flags)


def test_fix_a_an_exit_without_an_entry_price_is_logged(mod, monkeypatch):
    lines = []
    monkeypatch.setattr(mod, "_log", lambda msg, color="white": lines.append(msg))
    out = run(mod, monkeypatch, {"SPY": SPY, "XOM": ind(104.0, rsi=72.0, rsi_prev=68.0)},
              emu=BtEmulator(positions={"XOM": 5.0}))
    assert "XOM" not in out
    assert any("XOM" in line and "entry price" in line for line in lines)


def test_fw_str_d_the_backtest_path_enters_at_any_tick_time(mod, monkeypatch):
    """The no-entries-after-the-open rule is live only: a backtest tick after
    09:30 ET still decides the session's entries on prior closes."""
    after_open = datetime(2026, 6, 2, 15, 0, tzinfo=timezone.utc)     # 11:00 ET
    out = run(mod, monkeypatch, {"SPY": SPY, "AAA": ind(100.0)}, at=after_open)
    assert out["_nexus_executable_buys"] == ["AAA"]
