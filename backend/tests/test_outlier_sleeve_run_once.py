"""Broker contract of the outlier sleeve wrapper."""
import json
import os
import re
import sys
from datetime import datetime, timezone

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

import strategies.outlier_sleeve as mod  # noqa: E402
from strategies.outlier_sleeve import OutlierSleeve, SLOTS_KEY  # noqa: E402
from outlier_sleeve import DEFAULTS, LAST_SCREEN_KEY  # noqa: E402
from outlier_features import FEATURES_TABLE, PEERS_TABLE, feature_id  # noqa: E402

#: Called Thursday 2026-06-04 09:35 NY: Wednesday 2026-06-03's row is visible.
DECIDES = datetime(2026, 6, 4, 13, 35, tzinfo=timezone.utc)
SKIPS = datetime(2026, 6, 5, 13, 35, tzinfo=timezone.utc)      # sees Thursday
VISIBLE = "2026-06-03"


class FakeEmulator:
    def __init__(self, cash=10000.0, positions=None):
        self._cash = cash
        self._positions = dict(positions or {})

    def get_cash(self):
        return self._cash

    def get_buying_power(self, reserved=0.0, prices=None):
        return self._cash

    def get_positions(self):
        return dict(self._positions)

    def get_portfolio_value(self, prices=None):
        px = prices or {}
        return self._cash + sum(q * float(px.get(s, 0.0)) for s, q in self._positions.items())


def cfg(**over):
    c = dict(DEFAULTS)
    c["outlier_sleeve_enabled"] = True
    c["confirm_enabled"] = False
    c.update(over)
    return c


def seed(store, date=VISIBLE, rows=None):
    rows = rows or [
        {"symbol": "AAA", "close": 100.0, "hi252": 100.0, "ret126": 0.8, "adv20": 5e7, "sma200": 70.0, "n_bars": 300},
        {"symbol": "BBB", "close": 50.0, "hi252": 50.0, "ret126": 0.6, "adv20": 5e7, "sma200": 40.0, "n_bars": 300},
        {"symbol": "CCC", "close": 10.0, "hi252": 20.0, "ret126": -0.3, "adv20": 5e7, "sma200": 12.0, "n_bars": 300},
    ]
    docs = []
    for r in rows:
        d = dict(r)
        d["date"] = date
        d["id"] = feature_id(date, r["symbol"])
        d.setdefault("rs_rank", 0.95 if r["ret126"] > 0 else 0.1)
        docs.append(d)
    store.insert(FEATURES_TABLE, docs, conflict="replace")


def test_disabled_or_no_emulator_emits_nothing(store, monkeypatch):
    monkeypatch.setattr(mod, "store", store)
    seed(store)
    assert OutlierSleeve().run_once([], {}, DECIDES, dict(DEFAULTS), {},
                                    portfolio_emulator=FakeEmulator()) == {}
    assert OutlierSleeve().run_once([], {}, DECIDES, cfg(), {}, portfolio_emulator=None) == {}


def test_a_screen_day_buys_ranked_candidates_at_one_and_a_half_percent(store, monkeypatch):
    monkeypatch.setattr(mod, "store", store)
    seed(store)
    cache = {}
    out = OutlierSleeve().run_once([], {}, DECIDES, cfg(), {}, portfolio_emulator=FakeEmulator(),
                                   strategy_cache=cache)
    assert out["AAA"] == 1 and out["BBB"] == 1 and "CCC" not in out
    sizes = out["_nexus_position_sizes"]
    assert sizes["AAA"] == {"buy_cash": 150.0} and sizes["BBB"] == {"buy_cash": 150.0}
    assert sizes["_cash_reserve_floor_pct"] == 0.0
    assert set(out["_nexus_discovered"]) == {"AAA", "BBB"}
    assert out["_nexus_executable_buys"] == ["AAA", "BBB"]
    assert cache[SLOTS_KEY]["AAA"]["entry_px"] == 100.0
    assert cache[SLOTS_KEY]["AAA"]["entry_cost"] == 150.0
    assert cache[LAST_SCREEN_KEY] > 0


def test_off_days_hold_and_keep_the_held_names_in_the_universe(store, monkeypatch):
    monkeypatch.setattr(mod, "store", store)
    seed(store)
    seed(store, date="2026-06-04")
    cache = {}
    OutlierSleeve().run_once([], {}, DECIDES, cfg(), {}, portfolio_emulator=FakeEmulator(),
                             strategy_cache=cache)
    emu = FakeEmulator(cash=9700.0, positions={"AAA": 1.5, "BBB": 3.0})
    out = OutlierSleeve().run_once([], {"AAA": 100.0, "BBB": 50.0}, SKIPS, cfg(), {},
                                   portfolio_emulator=emu, strategy_cache=cache)
    assert out.get("AAA", 0) == 0 and out.get("BBB", 0) == 0
    assert set(out["_nexus_discovered"]) == {"AAA", "BBB"}
    assert out["_nexus_executable_buys"] == []


def test_a_trend_break_sells_with_the_etf_sell_intent(store, monkeypatch):
    monkeypatch.setattr(mod, "store", store)
    from strategy_eb import session_ordinal
    cache = {SLOTS_KEY: {"AAA": {"entry_px": 100.0, "entry_ordinal": session_ordinal("2026-01-05"),
                                 "entry_cost": 150.0, "proven": True, "below": 4, "last_eval": ""}}}
    seed(store, rows=[{"symbol": "AAA", "close": 60.0, "hi252": 100.0, "ret126": -0.2, "adv20": 5e7,
                       "sma200": 70.0, "n_bars": 300}])
    emu = FakeEmulator(cash=100.0, positions={"AAA": 1.5})
    out = OutlierSleeve().run_once([], {"AAA": 60.0}, SKIPS, cfg(), {}, portfolio_emulator=emu,
                                   strategy_cache=cache)
    assert out["AAA"] == -1
    assert out["_nexus_sell_enforcement"] == ["AAA"]
    assert out["_nexus_action_intents"] == {"AAA": "etf_sell"}
    assert "AAA" not in cache[SLOTS_KEY]


def test_winner_cap_emits_a_partial_sell_fraction(store, monkeypatch):
    monkeypatch.setattr(mod, "store", store)
    from strategy_eb import session_ordinal
    cache = {SLOTS_KEY: {"AAA": {"entry_px": 10.0, "entry_ordinal": session_ordinal("2026-01-05"),
                                 "entry_cost": 150.0, "proven": True, "below": 0, "last_eval": ""}}}
    seed(store, rows=[{"symbol": "AAA", "close": 100.0, "hi252": 100.0, "ret126": 3.0, "adv20": 5e7,
                       "sma200": 60.0, "n_bars": 300}])
    emu = FakeEmulator(cash=6000.0, positions={"AAA": 40.0})     # 4,000 of 10,000 = 40%
    out = OutlierSleeve().run_once([], {"AAA": 100.0}, SKIPS, cfg(), {}, portfolio_emulator=emu,
                                   strategy_cache=cache)
    assert out["AAA"] == -1
    assert out["_nexus_position_sizes"]["AAA"] == {"sell_fraction": 0.25}
    assert out["_nexus_action_intents"] == {"AAA": "etf_sell"}
    assert "AAA" in cache[SLOTS_KEY]        # a trim keeps the slot


def test_no_visible_session_refuses_to_trade(store, monkeypatch):
    monkeypatch.setattr(mod, "store", store)
    assert OutlierSleeve().run_once([], {}, DECIDES, cfg(), {}, portfolio_emulator=FakeEmulator(),
                                    strategy_cache={}) == {}


def test_the_schema_header_contains_exactly_every_default():
    path = os.path.join(_backend, "strategies", "outlier_sleeve.py")
    header = re.search(r"# INTELLISTOCK_SCHEMA: (.*)", open(path).read())
    schema = json.loads(header.group(1))
    assert set(schema["config"]) == set(DEFAULTS)
    assert schema["strategy"] == "outlier_sleeve"
    assert schema["execution_scope"] == "run_once"
    assert schema["decision_phase"] == "pre"
    assert schema["execution_position"] == 20


def test_the_class_name_matches_what_the_broker_derives_from_the_id():
    from strategies_meta import _module_to_class_name
    derived = _module_to_class_name("outlier_sleeve")
    assert derived == "OutlierSleeve" and hasattr(mod, derived)
