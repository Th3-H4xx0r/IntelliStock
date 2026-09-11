"""Wrapper tests for Strategy HX: the broker contract and cache behaviour."""
import os
import sys
from datetime import datetime, timedelta, timezone

# ONLY backend/ goes on the path. Adding backend/strategies/ too would make
# `strategy_x` resolve to the WRAPPER rather than the pure module — they share
# a name — and the wrapper imports the pure one, so it self-imports.
_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from strategies.strategy_hx import StrategyHx  # noqa: E402
from strategy_hx import DEFAULTS  # noqa: E402

NOW = datetime(2026, 6, 1, 20, 0, tzinfo=timezone.utc)
PRICES = {"QQQ": 400.0, "TQQQ": 60.0, "BIL": 91.0, "PSQ": 10.0}


def bars(closes, end_day=None):
    end_day = end_day or datetime(2026, 6, 1, tzinfo=timezone.utc)
    n = len(closes)
    return [{"t": (end_day - timedelta(days=(n - i))).isoformat(), "c": c}
            for i, c in enumerate(closes)]


def wobbly(n, start=100.0, rate=0.001, amp=0.004):
    return [start * ((1.0 + rate) ** i) * (1.0 + (amp if i % 2 else -amp))
            for i in range(n)]


def crash(rise=110, days=10, pct=0.09):
    closes = wobbly(rise)
    top = max(closes)
    for i in range(1, days + 1):
        closes.append(top * (1.0 - pct * i / days))
    return closes


class FakeEmulator:
    def __init__(self, cash=10000.0, positions=None):
        self._cash = cash
        self._positions = dict(positions or {})

    def get_cash(self):
        return self._cash

    def get_positions(self):
        return dict(self._positions)

    def get_portfolio_value(self, prices=None):
        px = prices or PRICES
        return self._cash + sum(q * float(px.get(s, 0.0))
                                for s, q in self._positions.items())


def cfg(**overrides):
    value = dict(DEFAULTS)
    value["strategy_hx_enabled"] = True
    value.update(overrides)
    return value


def data_for(closes):
    out = {"QQQ": {"bars": bars(closes)}}
    for symbol in ("TQQQ", "BIL", "PSQ"):
        out[symbol] = {"bars": bars([50.0] * len(closes))}
    return out


def test_a_disabled_flag_or_a_missing_emulator_emits_nothing():
    assert StrategyHx().run_once(["QQQ"], PRICES, NOW, dict(DEFAULTS), {},
                                 data=data_for(wobbly(120)),
                                 portfolio_emulator=FakeEmulator()) == {}
    assert StrategyHx().run_once(["QQQ"], PRICES, NOW, cfg(), {},
                                 data=data_for(wobbly(120))) == {}


def test_a_blind_tick_does_nothing_rather_than_flattening_the_book():
    """Live dispatch passes data=None. A strategy that cannot see its own
    reference index must do NOTHING, not exit to cash."""
    cache = {}
    assert StrategyHx().run_once(["QQQ"], PRICES, NOW, cfg(), {}, data=None,
                                 portfolio_emulator=FakeEmulator(),
                                 strategy_cache=cache) == {}
    assert "_strategy_hx_state" not in cache


def test_bull_buys_the_core_and_the_remainder():
    cache = {}
    out = StrategyHx().run_once(["QQQ"], PRICES, NOW, cfg(), {},
                                data=data_for(wobbly(120)),
                                portfolio_emulator=FakeEmulator(),
                                strategy_cache=cache)
    assert out["TQQQ"] == 1 and out["QQQ"] == 1
    assert out["_nexus_position_sizes"]["TQQQ"]["buy_cash"] > 0
    assert out["_nexus_position_sizes"]["_cash_reserve_floor_pct"] == 0.0
    assert cache["_strategy_hx_state"] == "BULL"


def test_bear_sells_the_core_and_every_sell_carries_an_action_intent():
    """broker.py's Z2.1 check reads action_intent off the strategy summary.
    Strategy X shipped without it and all 965 of its sells logged
    would_block_in_phase2=True."""
    emu = FakeEmulator(cash=0.0, positions={"TQQQ": 100.0})
    cache = {}
    out = StrategyHx().run_once(["QQQ"], PRICES, NOW, cfg(), {},
                                data=data_for(crash()),
                                portfolio_emulator=emu, strategy_cache=cache)
    assert cache["_strategy_hx_state"] == "BEAR"
    sells = [s for s, d in out.items() if not s.startswith("_") and d == -1]
    assert sells == ["TQQQ"]
    assert out["_nexus_action_intents"]["TQQQ"] == "etf_sell"


def test_short_history_parks_the_book_in_cash():
    cache = {}
    out = StrategyHx().run_once(["QQQ"], PRICES, NOW, cfg(), {},
                                data=data_for(wobbly(30)),
                                portfolio_emulator=FakeEmulator(),
                                strategy_cache=cache)
    assert cache["_strategy_hx_state"] == "UNKNOWN"
    assert out["BIL"] == 1 and "TQQQ" not in out


def test_a_book_already_at_target_sends_nothing():
    """The band is what makes this a rarely-trading strategy. 75 TQQQ at $60
    and 13.75 QQQ at $400 is exactly 45/55 of a $10,000 NAV."""
    emu = FakeEmulator(cash=0.0, positions={"TQQQ": 75.0, "QQQ": 13.75})
    assert StrategyHx().run_once(
        ["QQQ"], PRICES, NOW, cfg(), {}, data=data_for(wobbly(120)),
        portfolio_emulator=emu,
        strategy_cache={"_strategy_hx_state": "BULL",
                        "_strategy_hx_confirm": 0}) == {}


def test_an_unpriced_leg_sends_its_weight_to_cash_not_to_the_core():
    """Missing a price for a leg must never concentrate the book into the
    legs that DO have one — least of all a 3x fund."""
    cache = {}
    out = StrategyHx().run_once(
        ["QQQ"], {"QQQ": 400.0, "BIL": 91.0}, NOW, cfg(), {},
        data={"QQQ": {"bars": bars(crash())},
              "BIL": {"bars": bars([91.0] * 120)}},
        portfolio_emulator=FakeEmulator(), strategy_cache=cache)
    assert cache["_strategy_hx_last"]["targets"] == {"BIL": 1.0}
    assert out["BIL"] == 1


def test_it_publishes_its_own_universe():
    out = StrategyHx().run_once(["QQQ"], PRICES, NOW, cfg(), {},
                                data=data_for(wobbly(120)),
                                portfolio_emulator=FakeEmulator(),
                                strategy_cache={})
    assert out["_nexus_discovered"] == ["BIL", "PSQ", "QQQ", "TQQQ"]


def test_the_schema_header_contains_every_default():
    import json
    import re
    path = os.path.join(_backend, "strategies", "strategy_hx.py")
    header = re.search(r"# INTELLISTOCK_SCHEMA: (.*)", open(path).read())
    schema = json.loads(header.group(1))
    assert schema["strategy"] == "strategy_hx"
    assert schema["execution_scope"] == "run_once"
    assert schema["config"] == DEFAULTS


def test_the_class_name_matches_what_the_broker_derives_from_the_id():
    """broker.py resolves a run-once strategy by CamelCasing its id, so the
    class name is part of the contract. Strategy XS shipped once as
    `StrategyXS` and BT634331 ran 1,259 sessions completely inert."""
    import ast
    import importlib

    module = importlib.import_module("strategies.strategy_hx")
    assert hasattr(getattr(module, "StrategyHx"), "run_once")

    broker = os.path.join(_backend, "broker.py")
    tree = ast.parse(open(broker).read())
    fn = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef)
              and n.name == "_strategy_name_to_module_and_class")
    # `re` goes in as a GLOBAL rather than as a synthesised import node: an
    # ast.Import built by hand has no lineno and compile() rejects it.
    ns = {"re": __import__("re")}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), broker, "exec"), ns)
    assert ns["_strategy_name_to_module_and_class"]("strategy_hx") == (
        "strategy_hx", "StrategyHx")


def test_the_sync_script_reproduces_the_header_byte_for_byte():
    """The header is what the UI and /strategies/available read. Letting it
    drift from DEFAULTS means an operator configures a key the strategy does
    not have, or misses one it does."""
    import json
    import re
    import subprocess

    root = os.path.dirname(_backend)
    script = os.path.join(root, "scripts", "strategy_hx_sync_schema.py")
    assert os.path.exists(script)
    path = os.path.join(_backend, "strategies", "strategy_hx.py")
    before = open(path).read()
    result = subprocess.run([sys.executable, script], cwd=root,
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    after = open(path).read()
    assert after == before, "the committed header is not what DEFAULTS says"
    schema = json.loads(re.search(r"# INTELLISTOCK_SCHEMA: (.*)",
                                  after).group(1))
    assert schema["config"] == DEFAULTS
    assert schema["execution_position"] == 10


def bars_forward(closes, start_day=datetime(2026, 1, 1, tzinfo=timezone.utc)):
    """Daily bars anchored at the START, so appending a close adds a SESSION.

    `bars()` anchors on the end, so appending there shifts the whole series a
    day earlier and the last visible session_id never changes — which is the
    opposite of what a new trading day looks like."""
    return [{"t": (start_day + timedelta(days=i)).isoformat(), "c": c}
            for i, c in enumerate(closes)]


def data_forward(closes):
    out = {"QQQ": {"bars": bars_forward(closes)}}
    for symbol in ("TQQQ", "BIL", "PSQ"):
        out[symbol] = {"bars": bars_forward([50.0] * len(closes))}
    return out


def test_the_bear_exit_counter_counts_sessions_not_ticks():
    """`run_once` is called on every TICK, and the backtest clock has no
    holiday calendar: a holiday produces a second tick over an identical
    visible close series. Advancing the counter on each of those completes a
    five-session bear exit in four — a 3x fund back on a session early, which
    is the one direction this strategy may never fail in. The machine advances
    once per session; every other tick of that session replays the answer."""
    closes = crash()
    top = max(closes)
    closes.append(top * 1.10)
    cache = {"_strategy_hx_state": "BEAR", "_strategy_hx_confirm": 0}
    call = dict(data=data_forward(closes), portfolio_emulator=FakeEmulator(),
                strategy_cache=cache)
    StrategyHx().run_once(["QQQ"], PRICES, NOW, cfg(), {}, **call)
    assert cache["_strategy_hx_state"] == "BEAR"
    assert cache["_strategy_hx_confirm"] == 1
    first_session = cache["_strategy_hx_session"]

    # A second tick of the SAME session — a holiday, a restart, a re-run.
    StrategyHx().run_once(["QQQ"], PRICES, NOW, cfg(), {}, **call)
    assert cache["_strategy_hx_confirm"] == 1
    assert cache["_strategy_hx_state"] == "BEAR"
    assert cache["_strategy_hx_session"] == first_session

    # A genuinely new session does advance it.
    closes.append(top * 1.10)
    StrategyHx().run_once(["QQQ"], PRICES, NOW, cfg(), {},
                          data=data_forward(closes),
                          portfolio_emulator=FakeEmulator(),
                          strategy_cache=cache)
    assert cache["_strategy_hx_session"] != first_session
    assert cache["_strategy_hx_confirm"] == 2
