"""Wrapper tests for Strategy XS: the broker contract and cache behaviour."""
import os
import sys
from datetime import datetime, timedelta, timezone

# ONLY backend/ goes on the path. Adding backend/strategies/ too would make
# `strategy_x` resolve to the WRAPPER rather than the pure module — they share
# a name — and the wrapper imports the pure one, so it self-imports.
_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from strategies.strategy_xs import StrategyXS  # noqa: E402
from strategy_xs import DEFAULTS  # noqa: E402

NOW = datetime(2026, 6, 1, 20, 0, tzinfo=timezone.utc)


def bars(n, start=100.0, step=0.5, end_day=None):
    end_day = end_day or datetime(2026, 6, 1, tzinfo=timezone.utc)
    return [{"t": (end_day - timedelta(days=(n - 1 - i))).isoformat(),
             "c": start + i * step} for i in range(n)]


def falling(n):
    return bars(n, start=400.0, step=-0.8)


PRICES = {"TQQQ": 50.0, "QQQ": 400.0, "BIL": 91.0,
          "GLD": 200.0, "UUP": 28.0, "DBMF": 26.0}


class FakeEmulator:
    def __init__(self, cash=10000.0, positions=None, prices=None):
        self._cash = cash
        self._positions = dict(positions or {})
        self._prices = dict(prices or PRICES)

    def get_cash(self):
        return self._cash

    def get_positions(self):
        return dict(self._positions)

    def get_portfolio_value(self, prices=None):
        px = prices or self._prices
        return self._cash + sum(q * float(px.get(s, 0.0))
                                for s, q in self._positions.items())


def cfg(**overrides):
    value = dict(DEFAULTS)
    value["strategy_xs_enabled"] = True
    value.update(overrides)
    return value


def data_for(qqq_bars):
    out = {"QQQ": {"bars": qqq_bars}}
    for s in ("GLD", "UUP", "DBMF", "BIL", "TQQQ"):
        out[s] = {"bars": bars(260)}
    return out


def test_disabled_by_default_emits_nothing():
    out = StrategyXS().run_once(["TQQQ"], PRICES, NOW, dict(DEFAULTS), {},
                                data=data_for(bars(260)),
                                portfolio_emulator=FakeEmulator())
    assert out == {}


def test_an_uptrend_buys_the_levered_core_and_the_diversifier():
    out = StrategyXS().run_once(["TQQQ"], PRICES, NOW, cfg(), {},
                                data=data_for(bars(260)),
                                portfolio_emulator=FakeEmulator(),
                                strategy_cache={})
    assert out.get("TQQQ") == 1
    assert out.get("GLD") == 1 and out.get("UUP") == 1
    assert out["_nexus_position_sizes"]["TQQQ"]["buy_cash"] > 0


def test_a_downtrend_holds_cash_and_still_holds_the_diversifier():
    cache = {}
    out = StrategyXS().run_once(["TQQQ"], PRICES, NOW, cfg(), {},
                                data=data_for(falling(260)),
                                portfolio_emulator=FakeEmulator(),
                                strategy_cache=cache)
    assert out.get("TQQQ") is None or out.get("TQQQ") != 1
    assert out.get("BIL") == 1
    assert out.get("GLD") == 1
    assert cache["_strategy_xs_last"]["risk_on"] is False


def test_it_refuses_to_trade_without_enough_filter_history():
    """A cold start must never read as risk-on, and 'risk-off' here would be a
    real cash buy rather than a flat."""
    out = StrategyXS().run_once(["TQQQ"], PRICES, NOW, cfg(), {},
                                data=data_for(bars(30)),
                                portfolio_emulator=FakeEmulator(),
                                strategy_cache={})
    assert out == {}


def test_it_publishes_its_own_universe():
    out = StrategyXS().run_once(["TQQQ"], PRICES, NOW, cfg(), {},
                                data=data_for(bars(260)),
                                portfolio_emulator=FakeEmulator(),
                                strategy_cache={})
    assert set(out["_nexus_discovered"]) >= {"QQQ", "TQQQ", "BIL",
                                             "GLD", "UUP", "DBMF"}


def test_every_sell_carries_an_action_intent():
    """broker.py's Z2.1 check reads action_intent off the strategy summary and
    whitelists only graph_nexus's enum. Strategy X shipped without this and all
    965 of its sells logged would_block_in_phase2=True."""
    emu = FakeEmulator(cash=0.0, positions={"TQQQ": 100.0})
    out = StrategyXS().run_once(["TQQQ"], PRICES, NOW, cfg(), {},
                                data=data_for(falling(260)),
                                portfolio_emulator=emu, strategy_cache={})
    sells = [s for s, d in out.items()
             if not s.startswith("_") and d == -1]
    assert sells
    for symbol in sells:
        assert out["_nexus_action_intents"][symbol] == "etf_sell"


def test_a_missing_diversifier_price_does_not_raise_core_leverage():
    prices = dict(PRICES)
    prices.pop("DBMF")
    cache = {}
    StrategyXS().run_once(["TQQQ"], prices, NOW, cfg(), {},
                          data={"QQQ": {"bars": bars(260)},
                                "GLD": {"bars": bars(260)},
                                "UUP": {"bars": bars(260)},
                                "BIL": {"bars": bars(260)},
                                "TQQQ": {"bars": bars(260)}},
                          portfolio_emulator=FakeEmulator(prices=prices),
                          strategy_cache=cache)
    targets = cache["_strategy_xs_last"]["targets"]
    assert targets["TQQQ"] == 0.451
    assert round(sum(targets.values()), 6) == 1.0


def test_the_schema_header_contains_every_default():
    import json
    import re
    path = os.path.join(_backend, "strategies", "strategy_xs.py")
    header = re.search(r"# INTELLISTOCK_SCHEMA: (.*)", open(path).read())
    schema = json.loads(header.group(1))
    assert set(schema["config"]) == set(DEFAULTS)
