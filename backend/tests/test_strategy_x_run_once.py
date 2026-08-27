"""StrategyX.run_once end-to-end against a fake emulator.

Five levers have shipped INERT in this project — each was invisible because its
live path left no fingerprint. These tests exist so that cannot happen here:
they assert the strategy actually emits orders, and that the two default-off
levers are genuinely off rather than accidentally load-bearing.
"""
import os
import sys
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from strategies.strategy_x import StrategyX  # noqa: E402
import strategies.strategy_x as strategy_x_module  # noqa: E402
from portfolio_emulator import PortfolioEmulator  # noqa: E402
from simulated_execution import (  # noqa: E402
    DEFAULT_EQUITY_EXECUTION_COST_MODEL,
    NextEventExecutionSimulator,
    SimulationQuote,
)
from strategy_x import DEFAULTS  # noqa: E402
from strategy_x_bear import BearSystemStateError  # noqa: E402


class FakeEmulator:
    def __init__(self, cash=10000.0, positions=None, prices=None,
                 pending_symbols=()):
        self._cash = cash
        self._positions = dict(positions or {})
        self._prices = dict(prices or {})
        self._pending_symbols = pending_symbols

    def get_cash(self):
        return self._cash

    def get_positions(self):
        return dict(self._positions)

    def get_portfolio_value(self, prices=None):
        px = prices or self._prices
        return self._cash + sum(q * float(px.get(s, 0.0))
                                for s, q in self._positions.items())

    def pending_execution_symbols(self):
        if isinstance(self._pending_symbols, BaseException):
            raise self._pending_symbols
        return self._pending_symbols


def bars(n, start=100.0, step=0.5, end_day=None, mult=None):
    """n daily bars ending on `end_day`, one bar per weekday-ish day."""
    end_day = end_day or datetime(2026, 6, 1, tzinfo=timezone.utc)
    out = []
    for i in range(n):
        ts = end_day - timedelta(days=(n - 1 - i))
        price = start + i * step
        if mult:
            price *= mult(i)
        out.append({"t": ts.isoformat(), "c": price})
    return out


def base_cfg(**over):
    c = {
        "strategy_x_enabled": True,
        "core_bull_symbol": "TQQQ",
        "core_chop_symbol": "SPY",
        "core_bear_symbol": "",
        "core_weight": 0.9,
        "core_band_pct": 0.05,
        "core_filter_symbol": "QQQ",
        "core_filter_ma_bars": 200,
        "core_vol_gate_mult": 1.2,
        "satellite_pct": 0.0,
    }
    c.update(over)
    return c


def ownership_evidence(symbol, role, target_weight):
    return {
        "version": 1,
        "records": {
            symbol: {
                "symbol": symbol,
                "role": role,
                "target_weight": target_weight,
            },
        },
    }


def bear_ready_downtrend_fixture():
    prices = {
        **PRICES,
        "BIL": 91.0,
        "DBMF": 30.0,
        "KMLM": 25.0,
        "CTA": 20.0,
        "SQQQ": 8.0,
    }
    data = {"QQQ": {"bars": bars(260, start=250.0, step=-0.5)}}
    for symbol, price in prices.items():
        if symbol in {"BIL", "DBMF", "KMLM", "CTA", "SQQQ"}:
            data[symbol] = {"bars": bars(80, start=price, step=0.01)}
    return data, prices


def crash_bars(*, follow_through=False, recovery=False):
    values = [100.0 + i * 0.1 for i in range(200)] + [50.0]
    if follow_through:
        values.append(49.0)
    if recovery:
        values.append(150.0)
    start = NOW - timedelta(days=len(values) + 2)
    return [
        {"t": (start + timedelta(days=i)).isoformat(), "c": value}
        for i, value in enumerate(values)
    ]


def bear_session_data(*, follow_through=False, recovery=False):
    data, prices = bear_ready_downtrend_fixture()
    data["QQQ"] = {"bars": crash_bars(
        follow_through=follow_through, recovery=recovery,
    )}
    return data, prices


def at_day(data, day):
    """The same tape, `day` sessions later.

    The kicker's hold and cooldown counters advance on the last COMPLETED
    filter session, not on the call — at 15m cadence run_once fires ~26 times
    inside one session, and a holiday row repeats the last completed close. A
    test that moves the wall clock without moving the tape is therefore asking
    the same question twice, and the answer must not change.

    Every bar is re-stamped rather than extended, because `fast_crash_signal`
    reads today against yesterday: padding the end with a repeated close would
    make every fresh breakdown look stale and silently disarm the kicker.
    """
    series = list((data.get("QQQ") or {}).get("bars") or [])
    if day <= 0 or not series:
        return data
    return {**data, "QQQ": {"bars": [
        {**bar, "t": (datetime.fromisoformat(str(bar["t"]))
                      + timedelta(days=day)).isoformat()}
        for bar in series]}}


NOW = datetime(2026, 6, 1, 20, 0, tzinfo=timezone.utc)
PRICES = {"TQQQ": 50.0, "SPY": 500.0, "QQQ": 400.0, "AAPL": 200.0}


def run(cfg, data, emu):
    return StrategyX().run_once(["TQQQ", "SPY"], PRICES, NOW, cfg, {},
                                data=data, portfolio_emulator=emu,
                                strategy_cache={})


def test_disabled_by_default_emits_nothing():
    cfg = base_cfg()
    cfg["strategy_x_enabled"] = False
    assert run(cfg, {"QQQ": {"bars": bars(260)}}, FakeEmulator()) == {}


def test_uptrend_buys_the_levered_core():
    out = run(base_cfg(), {"QQQ": {"bars": bars(260)}},
              FakeEmulator(cash=10000.0, prices=PRICES))
    assert out.get("TQQQ") == 1
    assert out["_nexus_position_sizes"]["TQQQ"]["buy_cash"] > 0
    assert "TQQQ" in out["_nexus_executable_buys"]


def test_downtrend_holds_spy_not_the_levered_core():
    data = {"QQQ": {"bars": bars(260, start=250.0, step=-0.5)}}
    out = run(base_cfg(), data, FakeEmulator(cash=10000.0, prices=PRICES))
    assert out.get("TQQQ") is None
    assert out.get("SPY") == 1


def test_shadow_matches_off_orders_and_sizing_exactly():
    data, prices = bear_ready_downtrend_fixture()
    off_cache, shadow_cache = {}, {}
    off = StrategyX().run_once(
        list(prices), prices, NOW, base_cfg(bear_system_mode="off"), {},
        data=data, portfolio_emulator=FakeEmulator(prices=prices),
        strategy_cache=off_cache,
    )
    shadow = StrategyX().run_once(
        list(prices), prices, NOW, base_cfg(bear_system_mode="shadow"), {},
        data=data, portfolio_emulator=FakeEmulator(prices=prices),
        strategy_cache=shadow_cache,
    )
    assert {k: v for k, v in shadow.items() if not k.startswith("_")} == {
        k: v for k, v in off.items() if not k.startswith("_")
    }
    assert shadow["_nexus_position_sizes"] == off["_nexus_position_sizes"]
    assert not any(key.startswith("_sx_bear_") for key in off_cache)
    assert shadow_cache["_sx_bear_shadow"]["proposed_targets"] != (
        shadow_cache["_sx_bear_shadow"]["baseline_targets"]
    )


def test_active_risk_off_buys_managed_futures_and_bil():
    data, prices = bear_ready_downtrend_fixture()
    cache = {}
    out = StrategyX().run_once(
        list(prices), prices, NOW, base_cfg(bear_system_mode="active"), {},
        data=data, portfolio_emulator=FakeEmulator(prices=prices),
        strategy_cache=cache, mode="backtest",
    )
    assert out["BIL"] == 1
    assert out["DBMF"] == out["KMLM"] == out["CTA"] == 1
    assert "SPY" not in out
    assert out["_nexus_position_sizes"]["BIL"]["buy_cash"] > (
        out["_nexus_position_sizes"]["DBMF"]["buy_cash"]
    )
    assert cache["_strategy_x_last"]["targets"] == {
        "DBMF": 0.166666,
        "KMLM": 0.166666,
        "CTA": 0.166666,
        "BIL": 0.500002,
    }


def test_single_manager_string_is_one_eligible_allocated_symbol_end_to_end():
    data, prices = bear_ready_downtrend_fixture()
    cache = {}
    out = StrategyX().run_once(
        list(prices), prices, NOW,
        base_cfg(
            bear_system_mode="active", crisis_alpha_symbols="DBMF",
        ),
        {}, data=data, portfolio_emulator=FakeEmulator(prices=prices),
        strategy_cache=cache, mode="backtest",
    )
    assert out["DBMF"] == 1
    assert cache["_sx_bear_shadow"]["eligible_managers"] == ["DBMF"]
    assert cache["_sx_bear_shadow"]["unavailable_managers"] == []
    assert cache["_strategy_x_last"]["targets"] == {
        "DBMF": 0.5,
        "BIL": 0.5,
    }


def test_active_request_cannot_change_orders_outside_backtest_runtime():
    data, prices = bear_ready_downtrend_fixture()
    baseline = StrategyX().run_once(
        list(prices), prices, NOW, base_cfg(bear_system_mode="off"), {},
        data=data, portfolio_emulator=FakeEmulator(prices=prices),
        strategy_cache={},
    )
    cache = {}
    refused = StrategyX().run_once(
        list(prices), prices, NOW, base_cfg(bear_system_mode="active"), {},
        data=data, portfolio_emulator=FakeEmulator(prices=prices),
        strategy_cache=cache, mode="FULL",
    )
    assert {k: v for k, v in refused.items() if not k.startswith("_")} == {
        k: v for k, v in baseline.items() if not k.startswith("_")
    }
    assert refused["_nexus_position_sizes"] == baseline["_nexus_position_sizes"]
    assert cache["_sx_bear_shadow"]["refusal_reason"] == "research-only runtime"
    assert cache["_strategy_x_last"]["bear_overlay_reason"] == (
        "research-only runtime"
    )


def test_research_refused_active_advances_state_exactly_like_shadow():
    prices = bear_session_data()[1]
    shadow_cache, active_cache = {}, {}
    for day, follow_through in enumerate((False, True, True)):
        data = at_day(bear_session_data(follow_through=follow_through)[0], day)
        current_time = NOW + timedelta(days=day)
        shadow = StrategyX().run_once(
            list(prices), prices, current_time,
            base_cfg(bear_system_mode="shadow"), {}, data=data,
            portfolio_emulator=FakeEmulator(prices=prices),
            strategy_cache=shadow_cache, mode="FULL",
        )
        refused = StrategyX().run_once(
            list(prices), prices, current_time,
            base_cfg(bear_system_mode="active"), {}, data=data,
            portfolio_emulator=FakeEmulator(prices=prices),
            strategy_cache=active_cache, mode="FULL",
        )
        assert {k: v for k, v in refused.items() if not k.startswith("_")} == {
            k: v for k, v in shadow.items() if not k.startswith("_")
        }
        assert refused.get("_nexus_position_sizes") == shadow.get(
            "_nexus_position_sizes"
        )
        assert {
            key: active_cache.get(key)
            for key in (
                "_sx_bear_system_state",
                "_sx_bear_kicker_bars",
                "_sx_bear_kicker_cooldown",
                "_sx_bear_kicker_entry_day",
            )
        } == {
            key: shadow_cache.get(key)
            for key in (
                "_sx_bear_system_state",
                "_sx_bear_kicker_bars",
                "_sx_bear_kicker_cooldown",
                "_sx_bear_kicker_entry_day",
            )
        }
        assert active_cache["_sx_bear_shadow"]["kicker"] == (
            shadow_cache["_sx_bear_shadow"]["kicker"]
        )
        assert active_cache["_sx_bear_shadow"]["refusal_reason"] == (
            "research-only runtime"
        )

    assert active_cache["_sx_bear_system_state"] == "holding"
    assert active_cache["_sx_bear_kicker_bars"] == 2


@pytest.mark.parametrize("runtime_mode", ["BACKTEST", " backtest "])
def test_active_requires_literal_backtest_runtime(runtime_mode):
    data, prices = bear_ready_downtrend_fixture()
    baseline = StrategyX().run_once(
        list(prices), prices, NOW, base_cfg(bear_system_mode="off"), {},
        data=data, portfolio_emulator=FakeEmulator(prices=prices),
        strategy_cache={},
    )
    cache = {}
    refused = StrategyX().run_once(
        list(prices), prices, NOW, base_cfg(bear_system_mode="active"), {},
        data=data, portfolio_emulator=FakeEmulator(prices=prices),
        strategy_cache=cache, mode=runtime_mode,
    )
    assert {k: v for k, v in refused.items() if not k.startswith("_")} == {
        k: v for k, v in baseline.items() if not k.startswith("_")
    }
    assert refused["_nexus_position_sizes"] == baseline["_nexus_position_sizes"]
    assert cache["_sx_bear_shadow"]["refusal_reason"] == "research-only runtime"
    assert cache["_strategy_x_last"]["bear_overlay_reason"] == (
        "research-only runtime"
    )


def test_research_only_reason_precedes_insufficient_history_and_owned_exit():
    _, prices = bear_ready_downtrend_fixture()
    cache = {
        "_sx_bear_owned": ["DBMF"],
        "_sx_bear_kicker_targeted": False,
    }
    out = StrategyX().run_once(
        list(prices), prices, NOW, base_cfg(bear_system_mode="active"), {},
        data={},
        portfolio_emulator=FakeEmulator(
            cash=0.0, positions={"DBMF": 10.0}, prices=prices,
        ),
        strategy_cache=cache, mode="FULL",
    )
    assert out["DBMF"] == -1
    assert out["_nexus_position_sizes"]["DBMF"]["sell_fraction"] == 1.0
    assert cache["_strategy_x_last"]["bear_overlay_reason"] == (
        "research-only runtime"
    )


def test_active_refuses_when_legacy_bear_symbol_is_configured():
    data, prices = bear_ready_downtrend_fixture()
    out = StrategyX().run_once(
        list(prices), prices, NOW,
        base_cfg(bear_system_mode="active", core_bear_symbol="SQQQ"), {},
        data=data, portfolio_emulator=FakeEmulator(prices=prices),
        strategy_cache={}, mode="backtest",
    )
    assert "BIL" not in out and "DBMF" not in out


def test_legacy_conflict_does_not_advance_new_bear_state():
    data, prices = bear_ready_downtrend_fixture()
    initial = {
        "_sx_bear_state_version": 1,
        "_sx_bear_system_state": "armed",
        "_sx_bear_kicker_bars": 0,
        "_sx_bear_kicker_cooldown": 0,
        "_sx_bear_kicker_entry_day": "",
        "_sx_bear_kicker_targeted": False,
        "_sx_bear_owned": [],
    }
    active_cache = dict(initial)
    off = StrategyX().run_once(
        list(prices), prices, NOW,
        base_cfg(bear_system_mode="off", core_bear_symbol="SQQQ"), {},
        data=data, portfolio_emulator=FakeEmulator(prices=prices),
        strategy_cache={}, mode="backtest",
    )
    active = StrategyX().run_once(
        list(prices), prices, NOW,
        base_cfg(bear_system_mode="active", core_bear_symbol="SQQQ"), {},
        data=data, portfolio_emulator=FakeEmulator(prices=prices),
        strategy_cache=active_cache, mode="backtest",
    )
    assert {k: v for k, v in active.items() if not k.startswith("_")} == {
        k: v for k, v in off.items() if not k.startswith("_")
    }
    assert active["_nexus_position_sizes"] == off["_nexus_position_sizes"]
    assert {key: active_cache[key] for key in initial} == initial


def test_dynamic_satellite_role_collision_refuses_overlay():
    data, prices = bear_ready_downtrend_fixture()
    data["conviction_scores"] = {"DBMF": 9.0, "AAPL": 1.0}
    cache = {}
    out = StrategyX().run_once(
        list(prices), prices, NOW,
        base_cfg(
            bear_system_mode="active",
            satellite_pct=0.2,
            satellite_max_names=1,
            satellite_min_price=1.0,
        ),
        {}, data=data, portfolio_emulator=FakeEmulator(prices=prices),
        strategy_cache=cache, mode="backtest",
    )
    assert "BIL" not in out
    assert cache["_sx_bear_shadow"]["reason"].startswith("role conflict")


def test_shadow_broker_added_manager_quote_does_not_change_satellite_ranking():
    data, prices = bear_ready_downtrend_fixture()
    data["conviction_scores"] = {"DBMF": 9.0, "AAPL": 1.0}
    data["AAPL"] = {"bars": bars(80, start=120.0, step=1.0)}
    data["DBMF"] = {"bars": []}
    original = {k: v for k, v in prices.items() if k != "DBMF"}
    cfg = base_cfg(
        satellite_pct=0.2,
        satellite_max_names=1,
        satellite_min_price=1.0,
    )
    off = StrategyX().run_once(
        list(original), original, NOW, dict(cfg, bear_system_mode="off"), {},
        data=data, portfolio_emulator=FakeEmulator(prices=original),
        strategy_cache={},
    )
    shadow = StrategyX().run_once(
        list(original), prices, NOW, dict(cfg, bear_system_mode="shadow"), {},
        data=data, portfolio_emulator=FakeEmulator(prices=prices),
        strategy_cache={},
    )
    assert {k: v for k, v in shadow.items() if not k.startswith("_")} == {
        k: v for k, v in off.items() if not k.startswith("_")
    }
    assert shadow["_nexus_position_sizes"] == off["_nexus_position_sizes"]


@pytest.mark.parametrize("bear_mode", ["off", "shadow"])
def test_nonactive_modes_do_not_claim_unprovenanced_bear_holdings(bear_mode):
    data, prices = bear_ready_downtrend_fixture()
    cache = {}
    out = StrategyX().run_once(
        list(prices), prices, NOW, base_cfg(bear_system_mode=bear_mode), {},
        data=data,
        portfolio_emulator=FakeEmulator(
            cash=0.0, positions={"BIL": 20.0, "DBMF": 10.0, "SQQQ": 5.0},
            prices=prices,
        ),
        strategy_cache=cache,
    )
    assert not {"BIL", "DBMF", "SQQQ"}.intersection(out)
    assert not {"BIL", "DBMF", "SQQQ"}.intersection(
        out.get("_nexus_sell_enforcement", [])
    )
    assert not cache.get("_sx_bear_owned")


@pytest.mark.parametrize("symbol", ["BIL", "DBMF", "SQQQ"])
@pytest.mark.parametrize(
    "bear_mode,runtime_mode", [("shadow", None), ("active", "FULL")],
)
def test_nonexecuting_bear_modes_refuse_unprovenanced_holding_without_overlay(
    symbol, bear_mode, runtime_mode,
):
    data, prices = bear_ready_downtrend_fixture()
    emu = FakeEmulator(cash=0.0, positions={symbol: 5.0}, prices=prices)
    baseline = StrategyX().run_once(
        list(prices), prices, NOW, base_cfg(bear_system_mode="off"), {},
        data=data, portfolio_emulator=emu, strategy_cache={}, mode=runtime_mode,
    )
    cache = {}
    refused = StrategyX().run_once(
        list(prices), prices, NOW, base_cfg(bear_system_mode=bear_mode), {},
        data=data, portfolio_emulator=emu, strategy_cache=cache,
        mode=runtime_mode,
    )
    assert refused == baseline
    telemetry = cache["_sx_bear_shadow"]
    assert telemetry["refusal_reason"] == (
        f"ownership conflict: unprovenanced bear holding(s): {symbol}"
    )
    assert telemetry["proposed_targets"] == telemetry["baseline_targets"]
    assert telemetry["target_delta"] == {}
    assert not cache.get("_sx_bear_owned")


@pytest.mark.parametrize("bear_mode", ["off", "shadow", "active"])
def test_raw_aapl_provenance_never_grants_sell_or_retention_authority(bear_mode):
    data, prices = bear_ready_downtrend_fixture()
    cache = {"_sx_bear_owned": ["AAPL"]}
    out = StrategyX().run_once(
        list(prices), prices, NOW, base_cfg(bear_system_mode=bear_mode), {},
        data=data,
        portfolio_emulator=FakeEmulator(
            cash=0.0, positions={"AAPL": 5.0}, prices=prices,
        ),
        strategy_cache=cache,
        mode="backtest" if bear_mode == "active" else None,
    )
    assert "AAPL" not in out
    assert "AAPL" not in out.get("_nexus_sell_enforcement", [])
    assert "AAPL" not in cache.get("_sx_bear_owned", [])


@pytest.mark.parametrize("symbol", ["BIL", "DBMF", "SQQQ"])
def test_active_rejects_unprovenanced_bear_holding(symbol):
    data, prices = bear_ready_downtrend_fixture()
    with pytest.raises(BearSystemStateError):
        StrategyX().run_once(
            list(prices), prices, NOW, base_cfg(bear_system_mode="active"), {},
            data=data,
            portfolio_emulator=FakeEmulator(
                cash=0.0, positions={symbol: 5.0}, prices=prices,
            ),
            strategy_cache={}, mode="backtest",
        )


@pytest.mark.parametrize(
    "conflicting_config,symbol",
    [
        ({"core_bear_symbol": "SQQQ"}, "SQQQ"),
        ({"bear_cash_symbol": "DBMF"}, "DBMF"),
    ],
)
def test_active_rejects_unprovenanced_holding_even_with_hard_conflict(
    conflicting_config, symbol,
):
    data, prices = bear_ready_downtrend_fixture()
    with pytest.raises(BearSystemStateError):
        StrategyX().run_once(
            list(prices), prices, NOW,
            base_cfg(bear_system_mode="active", **conflicting_config), {},
            data=data,
            portfolio_emulator=FakeEmulator(
                cash=0.0, positions={symbol: 5.0}, prices=prices,
            ),
            strategy_cache={}, mode="backtest",
        )


def test_zero_weight_managers_never_become_provenance_or_authorize_a_sell():
    data, prices = bear_ready_downtrend_fixture()
    cache = {}
    cfg = base_cfg(bear_system_mode="active", crisis_alpha_pct=0.0)
    StrategyX().run_once(
        list(prices), prices, NOW, cfg, {}, data=data,
        portfolio_emulator=FakeEmulator(prices=prices),
        strategy_cache=cache, mode="backtest",
    )
    assert not set(cache["_sx_bear_owned"]).intersection(
        {"DBMF", "KMLM", "CTA"}
    )
    with pytest.raises(BearSystemStateError):
        StrategyX().run_once(
            list(prices), prices, NOW + timedelta(days=1), cfg, {}, data=data,
            portfolio_emulator=FakeEmulator(
                cash=0.0, positions={"DBMF": 10.0}, prices=prices,
            ),
            strategy_cache=cache, mode="backtest",
        )


def test_pending_kicker_never_retains_retired_manager_provenance():
    data, prices = bear_session_data(follow_through=True)
    cache = {
        "_sx_bear_state_version": 1,
        "_sx_bear_system_state": "holding",
        "_sx_bear_kicker_bars": 1,
        "_sx_bear_kicker_cooldown": 0,
        "_sx_bear_kicker_entry_day": NOW.date().isoformat(),
        "_sx_bear_kicker_state_symbol": "SQQQ",
        "_sx_bear_kicker_targeted": True,
        "_sx_bear_pending_kicker_symbol": "SQQQ",
        "_sx_bear_owned": ["DBMF", "SQQQ"],
        "_sx_bear_ownership_evidence": ownership_evidence(
            "SQQQ", "kicker", 0.05,
        ),
        "_sx_bear_shadow": {"kicker": {"symbol": "SQQQ"}},
    }
    cfg = base_cfg(
        bear_system_mode="active", crisis_alpha_symbols=["KMLM", "CTA"],
    )
    StrategyX().run_once(
        list(prices), prices, NOW, cfg, {}, data=data,
        portfolio_emulator=FakeEmulator(prices=prices),
        strategy_cache=cache, mode="backtest",
    )
    assert "DBMF" not in cache["_sx_bear_owned"]
    assert "SQQQ" in cache["_sx_bear_owned"]

    next_out = StrategyX().run_once(
        list(prices), prices, NOW + timedelta(days=1), cfg, {}, data=data,
        portfolio_emulator=FakeEmulator(
            cash=0.0, positions={"DBMF": 10.0}, prices=prices,
        ),
        strategy_cache=cache, mode="backtest",
    )
    assert "DBMF" not in next_out


@pytest.mark.parametrize("pending_symbols", [("SQQQ",), RuntimeError("boom")])
@pytest.mark.parametrize("bear_mode,with_history", [("off", True), ("active", False)])
def test_pending_kicker_retention_never_carries_unrelated_provenance(
    bear_mode, with_history, pending_symbols,
):
    data, prices = bear_ready_downtrend_fixture()
    cache = {
        "_sx_bear_state_version": 1,
        "_sx_bear_system_state": "holding",
        "_sx_bear_kicker_bars": 1,
        "_sx_bear_kicker_cooldown": 0,
        "_sx_bear_kicker_entry_day": NOW.date().isoformat(),
        "_sx_bear_kicker_state_symbol": "SQQQ",
        "_sx_bear_kicker_targeted": True,
        "_sx_bear_pending_kicker_symbol": "SQQQ",
        "_sx_bear_owned": ["DBMF", "SQQQ"],
        "_sx_bear_ownership_evidence": ownership_evidence(
            "SQQQ", "kicker", 0.05,
        ),
        "_sx_bear_shadow": {"kicker": {"symbol": "SQQQ"}},
    }
    StrategyX().run_once(
        list(prices), prices, NOW, base_cfg(bear_system_mode=bear_mode), {},
        data=data if with_history else {},
        portfolio_emulator=FakeEmulator(
            prices=prices, pending_symbols=pending_symbols,
        ),
        strategy_cache=cache, mode="FULL",
    )
    assert cache["_sx_bear_owned"] == ["SQQQ"]
    assert cache["_sx_bear_kicker_targeted"] is True
    assert cache["_sx_bear_pending_kicker_symbol"] == "SQQQ"


@pytest.mark.parametrize(
    "pending_flag,provenance",
    [(True, []), (1, ["SQQQ"]), ("true", ["SQQQ"])],
)
def test_pending_kicker_requires_exact_true_flag_and_matching_provenance(
    pending_flag, provenance,
):
    data, prices = bear_session_data(follow_through=True)
    cache = {
        "_sx_bear_state_version": 1,
        "_sx_bear_system_state": "holding",
        "_sx_bear_kicker_bars": 1,
        "_sx_bear_kicker_cooldown": 0,
        "_sx_bear_kicker_entry_day": NOW.date().isoformat(),
        "_sx_bear_kicker_state_symbol": "SQQQ",
        "_sx_bear_kicker_targeted": pending_flag,
        "_sx_bear_owned": provenance,
        "_sx_bear_shadow": {"kicker": {"symbol": "SQQQ"}},
    }
    if provenance:
        cache["_sx_bear_pending_kicker_symbol"] = "SQQQ"
        cache["_sx_bear_ownership_evidence"] = ownership_evidence(
            "SQQQ", "kicker", 0.05,
        )
    out = StrategyX().run_once(
        list(prices), prices, NOW, base_cfg(bear_system_mode="active"), {},
        data=data, portfolio_emulator=FakeEmulator(prices=prices),
        strategy_cache=cache, mode="backtest",
    )
    assert "SQQQ" not in out
    assert cache["_sx_bear_system_state"] == "cooldown"
    assert "SQQQ" not in cache["_sx_bear_owned"]


@pytest.mark.parametrize(
    "pending_symbol", [True, 1, "sqqq", " SQQQ ", "AAPL", ""],
)
def test_pending_kicker_symbol_requires_strict_evidence_bound_identity(
    pending_symbol,
):
    data, prices = bear_session_data(follow_through=True)
    cache = {
        "_sx_bear_state_version": 1,
        "_sx_bear_system_state": "holding",
        "_sx_bear_kicker_bars": 1,
        "_sx_bear_kicker_cooldown": 0,
        "_sx_bear_kicker_entry_day": NOW.date().isoformat(),
        "_sx_bear_kicker_state_symbol": "SQQQ",
        "_sx_bear_kicker_targeted": True,
        "_sx_bear_pending_kicker_symbol": pending_symbol,
        "_sx_bear_owned": ["SQQQ"],
        "_sx_bear_ownership_evidence": ownership_evidence(
            "SQQQ", "kicker", 0.05,
        ),
    }
    out = StrategyX().run_once(
        list(prices), prices, NOW, base_cfg(bear_system_mode="active"), {},
        data=data, portfolio_emulator=FakeEmulator(prices=prices),
        strategy_cache=cache, mode="backtest",
    )
    assert "SQQQ" not in out
    assert cache["_sx_bear_system_state"] == "cooldown"
    assert cache["_sx_bear_kicker_targeted"] is False
    assert cache["_sx_bear_pending_kicker_symbol"] == ""
    assert "SQQQ" not in cache["_sx_bear_owned"]


def test_missing_kicker_identity_grants_no_old_or_new_kicker_authority():
    data, prices = bear_session_data(follow_through=True)
    prices["PSQ"] = 10.0
    data["PSQ"] = {"bars": bars(80, start=10.0, step=0.01)}
    cache = {
        "_sx_bear_state_version": 1,
        "_sx_bear_system_state": "holding",
        "_sx_bear_kicker_bars": 1,
        "_sx_bear_kicker_cooldown": 0,
        "_sx_bear_kicker_entry_day": NOW.date().isoformat(),
        "_sx_bear_kicker_targeted": True,
        "_sx_bear_owned": ["PSQ", "SQQQ"],
    }
    out = StrategyX().run_once(
        list(prices), prices, NOW,
        base_cfg(
            bear_system_mode="active",
            bear_kicker_symbol="PSQ",
            crisis_alpha_symbols=["KMLM", "CTA"],
        ),
        {}, data=data,
        portfolio_emulator=FakeEmulator(
            cash=0.0, positions={"SQQQ": 10.0}, prices=prices,
        ),
        strategy_cache=cache, mode="backtest",
    )
    assert "SQQQ" not in out
    assert "PSQ" not in out
    assert "SQQQ" not in cache["_sx_bear_owned"]
    assert "PSQ" not in cache["_sx_bear_owned"]
    assert cache["_sx_bear_system_state"] == "cooldown"


def test_prior_owned_manager_unwinds_after_switching_off():
    data, prices = bear_ready_downtrend_fixture()
    cache = {"_sx_bear_owned": ["DBMF"]}
    out = StrategyX().run_once(
        list(prices), prices, NOW, base_cfg(bear_system_mode="off"), {},
        data=data,
        portfolio_emulator=FakeEmulator(
            cash=0.0, positions={"DBMF": 10.0}, prices=prices,
        ),
        strategy_cache=cache,
    )
    assert out["DBMF"] == -1
    assert cache["_sx_bear_owned"] == ["DBMF"]

    StrategyX().run_once(
        list(prices), prices, NOW + timedelta(days=1),
        base_cfg(bear_system_mode="off"), {}, data=data,
        portfolio_emulator=FakeEmulator(prices=prices),
        strategy_cache=cache,
    )
    assert cache["_sx_bear_owned"] == []


def test_prior_owned_manager_unwinds_after_role_configuration_changes():
    data, prices = bear_ready_downtrend_fixture()
    data["conviction_scores"] = {"DBMF": 9.0, "AAPL": 1.0}
    cache = {
        "_sx_bear_owned": ["DBMF"],
        "_sx_bear_ownership_evidence": {
            "version": 1,
            "records": {
                "DBMF": {
                    "symbol": "DBMF",
                    "role": "manager",
                    "target_weight": 0.166666,
                },
            },
        },
        "_sx_bear_shadow": {
            "target_delta": {"DBMF": 0.166666},
            "kicker": {"symbol": "SQQQ"},
        },
    }
    out = StrategyX().run_once(
        list(prices), prices, NOW,
        base_cfg(
            bear_system_mode="active",
            crisis_alpha_symbols=["KMLM", "CTA"],
            satellite_pct=0.2,
            satellite_max_names=1,
            satellite_min_price=1.0,
        ),
        {}, data=data,
        portfolio_emulator=FakeEmulator(
            cash=0.0, positions={"DBMF": 10.0}, prices=prices,
        ),
        strategy_cache=cache, mode="backtest",
    )
    assert out["DBMF"] == -1
    assert out["_nexus_position_sizes"]["DBMF"]["sell_fraction"] == 1.0
    assert "DBMF" not in cache["_strategy_x_last"]["targets"]
    assert set(cache["_sx_bear_owned"]) >= {"DBMF", "KMLM", "CTA", "BIL"}


def test_state_reset_with_provenance_owned_kicker_exits_immediately():
    data, prices = bear_ready_downtrend_fixture()
    cache = {"_sx_bear_owned": ["SQQQ"]}
    out = StrategyX().run_once(
        list(prices), prices, NOW, base_cfg(bear_system_mode="active"), {},
        data=data,
        portfolio_emulator=FakeEmulator(
            cash=0.0, positions={"SQQQ": 10.0}, prices=prices,
        ),
        strategy_cache=cache, mode="backtest",
    )
    assert out["SQQQ"] == -1
    assert cache["_sx_bear_system_state"] == "cooldown"


def test_invalid_kicker_entry_day_forces_owned_holding_exit():
    data, prices = bear_session_data(follow_through=True)
    cache = {
        "_sx_bear_state_version": 1,
        "_sx_bear_system_state": "holding",
        "_sx_bear_kicker_bars": 1,
        "_sx_bear_kicker_cooldown": 0,
        "_sx_bear_kicker_entry_day": "not-a-date",
        "_sx_bear_kicker_state_symbol": "SQQQ",
        "_sx_bear_kicker_targeted": True,
        "_sx_bear_pending_kicker_symbol": "SQQQ",
        "_sx_bear_owned": ["SQQQ"],
        "_sx_bear_ownership_evidence": ownership_evidence(
            "SQQQ", "kicker", 0.05,
        ),
        "_sx_bear_shadow": {"kicker": {"symbol": "SQQQ"}},
    }
    out = StrategyX().run_once(
        list(prices), prices, NOW, base_cfg(bear_system_mode="active"), {},
        data=data,
        portfolio_emulator=FakeEmulator(
            cash=0.0, positions={"SQQQ": 10.0}, prices=prices,
        ),
        strategy_cache=cache, mode="backtest",
    )
    assert out["SQQQ"] == -1
    assert cache["_sx_bear_system_state"] == "cooldown"
    assert cache["_sx_bear_shadow"]["kicker"]["reason"] == (
        "invalid persisted kicker counter"
    )


def test_missing_filter_history_still_unwinds_priceable_provenance():
    _, prices = bear_ready_downtrend_fixture()
    cache = {"_sx_bear_owned": ["DBMF"]}
    out = StrategyX().run_once(
        list(prices), prices, NOW, base_cfg(bear_system_mode="off"), {},
        data={},
        portfolio_emulator=FakeEmulator(
            cash=0.0, positions={"DBMF": 10.0}, prices=prices,
        ),
        strategy_cache=cache,
    )
    assert out["DBMF"] == -1
    assert "BIL" not in out


@pytest.mark.parametrize("bad", [-1, 1.5, float("nan"), float("inf"), 10**20])
def test_corrupt_kicker_counters_cannot_manufacture_a_buy(bad):
    data, prices = bear_ready_downtrend_fixture()
    cache = {
        "_sx_bear_state_version": 1,
        "_sx_bear_system_state": "armed",
        "_sx_bear_kicker_bars": bad,
        "_sx_bear_kicker_cooldown": 0,
        "_sx_bear_kicker_state_symbol": "SQQQ",
        "_sx_bear_kicker_targeted": False,
        "_sx_bear_owned": [],
    }
    out = StrategyX().run_once(
        list(prices), prices, NOW, base_cfg(bear_system_mode="active"), {},
        data=data, portfolio_emulator=FakeEmulator(prices=prices),
        strategy_cache=cache, mode="backtest",
    )
    assert "SQQQ" not in out


def test_state_inconsistent_bounded_counter_cannot_manufacture_a_buy():
    data, prices = bear_session_data(follow_through=True)
    cache = {
        "_sx_bear_state_version": 1,
        "_sx_bear_system_state": "armed",
        "_sx_bear_kicker_bars": 6,
        "_sx_bear_kicker_cooldown": 0,
        "_sx_bear_kicker_entry_day": "",
        "_sx_bear_kicker_state_symbol": "SQQQ",
        "_sx_bear_kicker_targeted": False,
        "_sx_bear_owned": [],
        "_sx_bear_shadow": {"kicker": {"symbol": "SQQQ"}},
    }
    out = StrategyX().run_once(
        list(prices), prices, NOW,
        base_cfg(bear_system_mode="active", bear_kicker_max_bars=5), {},
        data=data, portfolio_emulator=FakeEmulator(prices=prices),
        strategy_cache=cache, mode="backtest",
    )
    assert "SQQQ" not in out
    assert cache["_sx_bear_system_state"] == "cooldown"


def test_changing_kicker_symbol_unwinds_old_without_buying_new_symbol():
    data, prices = bear_session_data(follow_through=True)
    prices["PSQ"] = 10.0
    data["PSQ"] = {"bars": bars(80, start=10.0, step=0.01)}
    cache = {
        "_sx_bear_state_version": 1,
        "_sx_bear_system_state": "holding",
        "_sx_bear_kicker_bars": 1,
        "_sx_bear_kicker_cooldown": 0,
        "_sx_bear_kicker_entry_day": NOW.date().isoformat(),
        "_sx_bear_kicker_state_symbol": "SQQQ",
        "_sx_bear_kicker_targeted": True,
        "_sx_bear_pending_kicker_symbol": "SQQQ",
        "_sx_bear_owned": ["SQQQ"],
        "_sx_bear_ownership_evidence": {
            "version": 1,
            "records": {
                "SQQQ": {
                    "symbol": "SQQQ",
                    "role": "kicker",
                    "target_weight": 0.05,
                },
            },
        },
        "_sx_bear_shadow": {"kicker": {"symbol": "SQQQ"}},
    }
    out = StrategyX().run_once(
        list(prices), prices, NOW,
        base_cfg(bear_system_mode="active", bear_kicker_symbol="PSQ"), {},
        data=data,
        portfolio_emulator=FakeEmulator(
            cash=0.0, positions={"SQQQ": 10.0}, prices=prices,
        ),
        strategy_cache=cache, mode="backtest",
    )
    assert out["SQQQ"] == -1
    assert out["_nexus_position_sizes"]["SQQQ"]["sell_fraction"] == 1.0
    assert "PSQ" not in out
    assert "PSQ" not in cache["_sx_bear_owned"]


def test_kicker_decision_clock_arms_then_targets_only_after_bull_is_absent():
    cache = {}
    fresh_data, prices = bear_session_data()
    first = StrategyX().run_once(
        list(prices), prices, NOW, base_cfg(bear_system_mode="active"), {},
        data=fresh_data,
        portfolio_emulator=FakeEmulator(
            cash=0.0, positions={"TQQQ": 180.0}, prices=prices,
        ),
        strategy_cache=cache, mode="backtest",
    )
    assert first["TQQQ"] == -1
    assert "SQQQ" not in first
    assert cache["_sx_bear_system_state"] == "armed"

    stacked_data, _ = bear_session_data(follow_through=True)
    second = StrategyX().run_once(
        list(prices), prices, NOW + timedelta(days=1),
        base_cfg(bear_system_mode="active"), {}, data=stacked_data,
        portfolio_emulator=FakeEmulator(prices=prices),
        strategy_cache=cache, mode="backtest",
    )
    assert second["SQQQ"] == 1
    assert cache["_sx_bear_system_state"] == "holding"
    assert cache["_sx_bear_kicker_bars"] == 1
    assert cache["_sx_bear_kicker_targeted"] is True
    assert "SQQQ" in cache["_sx_bear_owned"]


def test_kicker_orders_fill_on_next_row_and_exposure_respects_session_bound():
    prices = bear_session_data()[1]
    cache = {}
    positions = {"TQQQ": 180.0}
    cash = 1000.0
    pending = None
    events = []
    cfg = base_cfg(
        bear_system_mode="active",
        bear_kicker_max_bars=1,
        bear_kicker_cooldown_bars=10,
    )
    for row in range(4):
        if pending:
            if pending.get("TQQQ") == -1:
                cash += positions.pop("TQQQ", 0.0) * prices["TQQQ"]
                events.append(("tqqq_sell_fill", row))
            if pending.get("SQQQ") == 1:
                positions["SQQQ"] = 50.0
                cash -= positions["SQQQ"] * prices["SQQQ"]
                events.append(("sqqq_buy_fill", row))
            if pending.get("SQQQ") == -1:
                cash += positions.pop("SQQQ", 0.0) * prices["SQQQ"]
                events.append(("sqqq_sell_fill", row))

        data, _ = bear_session_data(follow_through=row > 0)
        out = StrategyX().run_once(
            list(prices), prices, NOW + timedelta(days=row), cfg, {}, data=data,
            portfolio_emulator=FakeEmulator(
                cash=cash, positions=positions, prices=prices,
            ),
            strategy_cache=cache, mode="backtest",
        )
        executable = {key: value for key, value in out.items()
                      if not key.startswith("_")}
        if executable.get("SQQQ") == 1:
            events.append(("sqqq_buy_target", row))
        if executable.get("SQQQ") == -1:
            events.append(("sqqq_sell_target", row))
        pending = executable

    assert events == [
        ("tqqq_sell_fill", 1),
        ("sqqq_buy_target", 1),
        ("sqqq_buy_fill", 2),
        ("sqqq_sell_target", 2),
        ("sqqq_sell_fill", 3),
    ]


def test_missing_kicker_fill_price_cancels_request_without_carry_forward():
    cache = {
        "_sx_bear_state_version": 1,
        "_sx_bear_system_state": "holding",
        "_sx_bear_kicker_bars": 1,
        "_sx_bear_kicker_cooldown": 0,
        "_sx_bear_kicker_entry_day": NOW.date().isoformat(),
        "_sx_bear_kicker_state_symbol": "SQQQ",
        "_sx_bear_kicker_targeted": True,
        "_sx_bear_pending_kicker_symbol": "SQQQ",
        "_sx_bear_owned": ["SQQQ"],
        "_sx_bear_ownership_evidence": ownership_evidence(
            "SQQQ", "kicker", 0.05,
        ),
    }
    data, prices = bear_session_data(follow_through=True)
    data["SQQQ"] = {"bars": []}
    prices.pop("SQQQ")
    out = StrategyX().run_once(
        list(prices), prices, NOW + timedelta(days=1),
        base_cfg(bear_system_mode="active"), {}, data=data,
        portfolio_emulator=FakeEmulator(prices=prices),
        strategy_cache=cache, mode="backtest",
    )
    assert "SQQQ" not in out
    assert cache["_sx_bear_system_state"] == "cooldown"
    assert cache["_sx_bear_kicker_targeted"] is False


def test_kicker_max_hold_targets_exit_and_requires_full_cooldown():
    data, prices = bear_session_data(follow_through=True)
    cache = {
        "_sx_bear_state_version": 1,
        "_sx_bear_system_state": "holding",
        "_sx_bear_kicker_bars": 1,
        "_sx_bear_kicker_cooldown": 0,
        "_sx_bear_kicker_entry_day": NOW.date().isoformat(),
        "_sx_bear_kicker_state_symbol": "SQQQ",
        "_sx_bear_kicker_targeted": True,
        "_sx_bear_pending_kicker_symbol": "SQQQ",
        "_sx_bear_owned": ["SQQQ"],
        "_sx_bear_ownership_evidence": ownership_evidence(
            "SQQQ", "kicker", 0.05,
        ),
        "_sx_bear_shadow": {"kicker": {"symbol": "SQQQ"}},
    }
    cfg = base_cfg(
        bear_system_mode="active",
        bear_kicker_max_bars=1,
        bear_kicker_cooldown_bars=10,
    )
    exit_order = StrategyX().run_once(
        list(prices), prices, NOW, cfg, {}, data=data,
        portfolio_emulator=FakeEmulator(
            cash=0.0, positions={"SQQQ": 100.0}, prices=prices,
        ),
        strategy_cache=cache, mode="backtest",
    )
    assert exit_order["SQQQ"] == -1
    assert cache["_sx_bear_kicker_cooldown"] == 10
    assert cache["_sx_bear_shadow"]["kicker"]["reason"] == (
        "kicker maximum hold reached"
    )

    for decision in range(1, 11):
        out = StrategyX().run_once(
            list(prices), prices, NOW + timedelta(days=decision), cfg, {},
            data=at_day(data, decision),
            portfolio_emulator=FakeEmulator(prices=prices),
            strategy_cache=cache, mode="backtest",
        )
        assert "SQQQ" not in out
    assert cache["_sx_bear_system_state"] == "idle"

    fresh, _ = bear_session_data()
    armed = StrategyX().run_once(
        list(prices), prices, NOW + timedelta(days=11), cfg, {},
        data=at_day(fresh, 11),
        portfolio_emulator=FakeEmulator(prices=prices),
        strategy_cache=cache, mode="backtest",
    )
    assert "SQQQ" not in armed
    assert cache["_sx_bear_system_state"] == "armed"


def test_kicker_recovery_targets_exit_on_the_decision_row():
    data, prices = bear_session_data(follow_through=True, recovery=True)
    cache = {
        "_sx_bear_state_version": 1,
        "_sx_bear_system_state": "holding",
        "_sx_bear_kicker_bars": 1,
        "_sx_bear_kicker_cooldown": 0,
        "_sx_bear_kicker_entry_day": NOW.date().isoformat(),
        "_sx_bear_kicker_state_symbol": "SQQQ",
        "_sx_bear_kicker_targeted": True,
        "_sx_bear_pending_kicker_symbol": "SQQQ",
        "_sx_bear_owned": ["SQQQ"],
        "_sx_bear_ownership_evidence": ownership_evidence(
            "SQQQ", "kicker", 0.05,
        ),
        "_sx_bear_shadow": {"kicker": {"symbol": "SQQQ"}},
    }
    out = StrategyX().run_once(
        list(prices), prices, NOW, base_cfg(bear_system_mode="active"), {},
        data=data,
        portfolio_emulator=FakeEmulator(
            cash=0.0, positions={"SQQQ": 100.0}, prices=prices,
        ),
        strategy_cache=cache, mode="backtest",
    )
    assert out["SQQQ"] == -1
    assert cache["_sx_bear_system_state"] == "cooldown"
    assert cache["_sx_bear_shadow"]["kicker"]["reason"] == (
        "kicker recovery exit"
    )


def test_residual_conflict_suppresses_only_kicker_and_keeps_defense():
    data, prices = bear_session_data(follow_through=True)
    cache = {
        "_sx_bear_state_version": 1,
        "_sx_bear_system_state": "armed",
        "_sx_bear_kicker_bars": 0,
        "_sx_bear_kicker_cooldown": 0,
        "_sx_bear_kicker_state_symbol": "SQQQ",
        "_sx_bear_kicker_targeted": False,
        "_sx_bear_owned": [],
    }
    out = StrategyX().run_once(
        list(prices), prices, NOW,
        base_cfg(
            bear_system_mode="active",
            _strategy_x_bear_residual_conflict=True,
        ),
        {}, data=data, portfolio_emulator=FakeEmulator(prices=prices),
        strategy_cache=cache, mode="backtest",
    )
    assert out["BIL"] == out["DBMF"] == out["KMLM"] == out["CTA"] == 1
    assert "SQQQ" not in out
    assert cache["_sx_bear_shadow"]["refusal_reason"] == (
        "broker residual-sleeve kicker conflict"
    )


def test_residual_conflict_with_existing_kicker_provenance_is_irreconcilable():
    data, prices = bear_ready_downtrend_fixture()
    with pytest.raises(BearSystemStateError):
        StrategyX().run_once(
            list(prices), prices, NOW,
            base_cfg(
                bear_system_mode="active",
                _strategy_x_bear_residual_conflict=True,
            ),
            {}, data=data, portfolio_emulator=FakeEmulator(prices=prices),
            strategy_cache={"_sx_bear_owned": ["SQQQ"]}, mode="backtest",
        )


def test_schema_header_contains_every_strategy_x_default():
    path = Path(__file__).resolve().parents[1] / "strategies" / "strategy_x.py"
    first = path.read_text().splitlines()[0]
    schema = json.loads(first.removeprefix("# INTELLISTOCK_SCHEMA: "))
    assert {
        key: schema["config"].get(key) for key in DEFAULTS
    } == DEFAULTS
    assert schema["config"]["bear_system_mode"] == "off"


def test_invalid_mode_is_clean_off_and_writes_no_bear_cache_keys():
    data, prices = bear_ready_downtrend_fixture()
    baseline = StrategyX().run_once(
        list(prices), prices, NOW, base_cfg(bear_system_mode="off"), {},
        data=data, portfolio_emulator=FakeEmulator(prices=prices),
        strategy_cache={},
    )
    cache = {}
    invalid = StrategyX().run_once(
        list(prices), prices, NOW, base_cfg(bear_system_mode="paper"), {},
        data=data, portfolio_emulator=FakeEmulator(prices=prices),
        strategy_cache=cache,
    )
    assert invalid == baseline
    assert not any(key.startswith("_sx_bear_") for key in cache)


def test_shadow_telemetry_is_plain_json_and_orders_are_built_once(monkeypatch):
    data, prices = bear_ready_downtrend_fixture()
    cache = {}
    calls = 0
    real = strategy_x_module.targets_to_orders

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(strategy_x_module, "targets_to_orders", counted)
    StrategyX().run_once(
        list(prices), prices, NOW, base_cfg(bear_system_mode="shadow"), {},
        data=data, portfolio_emulator=FakeEmulator(prices=prices),
        strategy_cache=cache,
    )
    assert calls == 1
    telemetry = cache["_sx_bear_shadow"]
    json.dumps(telemetry, allow_nan=False)
    assert telemetry["core_state"] == "risk_off"
    assert "below MA200" in telemetry["core_reason"]


def test_downtrend_sells_a_held_levered_core():
    data = {"QQQ": {"bars": bars(260, start=250.0, step=-0.5)}}
    emu = FakeEmulator(cash=0.0, positions={"TQQQ": 180.0}, prices=PRICES)
    out = run(base_cfg(), data, emu)
    assert out.get("TQQQ") == -1
    assert out["_nexus_position_sizes"]["TQQQ"]["sell_fraction"] == pytest.approx(1.0)
    assert "TQQQ" in out["_nexus_sell_enforcement"]


def test_blind_filter_does_not_buy_the_levered_core():
    """No QQQ bars = no signal. It must hold flat, not default to risk-on."""
    out = run(base_cfg(), {}, FakeEmulator(cash=10000.0, prices=PRICES))
    assert out.get("TQQQ") is None


def test_bear_leg_is_inert_at_its_default():
    data = {"QQQ": {"bars": bars(260, start=250.0, step=-0.5)}}
    out = run(base_cfg(), data, FakeEmulator(cash=10000.0, prices=PRICES))
    assert "SQQQ" not in out


def _crisis_bars(n=400, shock=14):
    """An ORDERLY decline is not a crisis. The gate wants depth AND disorder,
    so the shock has to be violent and recent enough to expand short-window vol
    against the long window."""
    px = [300.0 + i * 0.4 for i in range(n - shock)]
    last = px[-1]
    for i in range(shock):
        last *= (0.94 if i % 2 else 1.01)
        px.append(last)
    end = NOW
    return [{"t": (end - timedelta(days=(n - 1 - i))).isoformat(), "c": p}
            for i, p in enumerate(px)]


def test_bear_leg_does_NOT_fire_on_an_ordinary_downtrend():
    """Risk-off is common; a bad regime is rare. This is the case the symmetric
    flip got wrong, and it is most of the risk-off bars in a real run."""
    data = {"QQQ": {"bars": bars(260, start=250.0, step=-0.5)}}
    prices = dict(PRICES, SQQQ=20.0)
    out = StrategyX().run_once(["TQQQ"], prices, NOW,
                               base_cfg(core_bear_symbol="SQQQ"), {}, data=data,
                               portfolio_emulator=FakeEmulator(cash=10000.0,
                                                               prices=prices),
                               strategy_cache={})
    assert out.get("SQQQ") is None
    assert out.get("SPY") == 1          # risk-off routes to the chop occupant


def test_bear_leg_fires_on_a_detected_crisis():
    data = {"QQQ": {"bars": _crisis_bars()}}
    prices = dict(PRICES, SQQQ=20.0)
    out = StrategyX().run_once(["TQQQ"], prices, NOW,
                               base_cfg(core_bear_symbol="SQQQ"), {}, data=data,
                               portfolio_emulator=FakeEmulator(cash=10000.0,
                                                               prices=prices),
                               strategy_cache={})
    assert out.get("SQQQ") == 1


def test_bear_leg_stands_down_after_the_time_limit():
    data = {"QQQ": {"bars": _crisis_bars()}}
    prices = dict(PRICES, SQQQ=20.0)
    cache = {"_sx_bear_bars": 40}       # already at the limit
    out = StrategyX().run_once(["TQQQ"], prices, NOW,
                               base_cfg(core_bear_symbol="SQQQ",
                                        core_bear_max_bars=40), {}, data=data,
                               portfolio_emulator=FakeEmulator(cash=10000.0,
                                                               prices=prices),
                               strategy_cache=cache)
    assert out.get("SQQQ") is None


def _run_bear(cache, cfg_over=None, bars_override=None):
    prices = dict(PRICES, SQQQ=20.0)
    cfg = base_cfg(core_bear_symbol="SQQQ")
    cfg.update(cfg_over or {})
    data = {"QQQ": {"bars": bars_override or _crisis_bars()}}
    return StrategyX().run_once(["TQQQ"], prices, NOW, cfg, {}, data=data,
                                portfolio_emulator=FakeEmulator(cash=10000.0,
                                                                prices=prices),
                                strategy_cache=cache)


def test_the_time_limit_is_a_real_bound_not_a_cycle():
    """Resetting the counter on the stand-down bar made the limit meaningless:
    it cycled 40-on / 1-off / 40-on forever, and each cycle is a full round trip
    of a -3x leg at the widest spreads of the episode."""
    cache = {"_sx_bear_bars": 40}
    out = _run_bear(cache, {"core_bear_max_bars": 40,
                            "core_bear_cooldown_bars": 20})
    assert out.get("SQQQ") is None
    assert cache.get("_sx_bear_cooldown", 0) > 0, "no cooldown — it re-engages"
    out2 = _run_bear(cache, {"core_bear_max_bars": 40,
                             "core_bear_cooldown_bars": 20})
    assert out2.get("SQQQ") is None
    assert cache["_sx_bear_bars"] == 0


def test_the_counter_only_ticks_while_the_leg_is_actually_held():
    """The gate can be open while the trend filter is still risk-on, in which
    case no SQQQ exists and the -6*sigma^2 clock must not be running."""
    cache = {}
    _run_bear(cache, bars_override=bars(400))          # uptrend -> risk-on
    assert cache.get("_sx_bear_bars", 0) == 0


def test_the_leg_engages_and_starts_its_clock():
    cache = {}
    assert _run_bear(cache).get("SQQQ") == 1
    assert cache["_sx_bear_bars"] == 1
    assert cache["_sx_bear_grace"] > 0                 # exit hysteresis armed


def test_the_strategy_declares_its_own_universe():
    """It must not depend on the instance watchlist listing TQQQ/SQQQ/QQQ."""
    data = {"QQQ": {"bars": bars(260)}}
    out = StrategyX().run_once([], PRICES, NOW,
                               base_cfg(core_bear_symbol="SQQQ"), {}, data=data,
                               portfolio_emulator=FakeEmulator(cash=10000.0,
                                                               prices=PRICES),
                               strategy_cache={})
    assert set(out.get("_nexus_discovered") or []) >= {"QQQ", "TQQQ", "SPY",
                                                       "SQQQ"}


def test_holding_the_bull_leg_blocks_a_direct_flip_to_the_bear_leg():
    data = {"QQQ": {"bars": bars(260, start=250.0, step=-0.5)}}
    cfg = base_cfg(core_bear_symbol="SQQQ")
    prices = dict(PRICES, SQQQ=20.0)
    emu = FakeEmulator(cash=0.0, positions={"TQQQ": 180.0}, prices=prices)
    out = StrategyX().run_once(["TQQQ"], prices, NOW, cfg, {}, data=data,
                               portfolio_emulator=emu, strategy_cache={})
    assert out.get("TQQQ") == -1        # exit the levered long
    assert out.get("SQQQ") is None      # but do NOT enter the levered short


def test_satellite_is_inert_without_conviction_scores():
    data = {"QQQ": {"bars": bars(260)}}
    out = run(base_cfg(satellite_pct=0.2), data,
              FakeEmulator(cash=10000.0, prices=PRICES))
    assert "AAPL" not in out


def test_satellite_buys_ranked_names_when_enabled_and_scored():
    data = {"QQQ": {"bars": bars(260)}, "conviction_scores": {"AAPL": 1.5}}
    out = run(base_cfg(satellite_pct=0.2), data,
              FakeEmulator(cash=10000.0, prices=PRICES))
    assert out.get("AAPL") == 1


def test_commodity_sleeve_buys_a_trending_commodity_end_to_end():
    """Proves the sleeve is not inert through the real run_once path — the
    failure mode this repo hits most is a lever that ships and does nothing."""
    prices = dict(PRICES, GLD=200.0, USO=70.0)
    data = {
        "QQQ": {"bars": bars(260)},
        "GLD": {"bars": bars(200, start=100.0, step=0.6)},    # uptrend
        "USO": {"bars": bars(200, start=200.0, step=-0.4)},   # downtrend
    }
    cfg = base_cfg(commodity_pct=0.15, commodity_max_names=2,
                   commodity_symbols=["GLD", "USO"])
    out = StrategyX().run_once(["TQQQ", "SPY"], prices, NOW, cfg, {}, data=data,
                               portfolio_emulator=FakeEmulator(cash=10000.0,
                                                               prices=prices),
                               strategy_cache={})
    assert out.get("GLD") == 1          # trending -> held
    assert out.get("USO") is None       # falling -> not held
    assert out.get("TQQQ") == 1         # core still funded, just smaller


def test_commodity_sleeve_is_inert_at_its_default():
    out = run(base_cfg(), {"QQQ": {"bars": bars(260)}},
              FakeEmulator(cash=10000.0, prices=PRICES))
    assert "GLD" not in out


def test_cache_records_the_decision_for_the_operator():
    cache = {}
    StrategyX().run_once(["TQQQ"], PRICES, NOW, base_cfg(), {},
                         data={"QQQ": {"bars": bars(260)}},
                         portfolio_emulator=FakeEmulator(cash=10000.0,
                                                         prices=PRICES),
                         strategy_cache=cache)
    last = cache["_strategy_x_last"]
    assert last["risk_on"] is True
    # 259, not 260: these are DAILY bars, whose stamp is the session open while
    # the close is 16:00. The last bar shares its date with the decision, so its
    # close is not knowable yet and pit_daily_closes correctly withholds it.
    assert last["n_closes"] == 259
    assert last["ma"] > 0


def test_future_bars_are_not_visible_to_the_filter():
    """A bar stamped after the decision time must not move the signal.

    The vol gate is disabled here on purpose. With it on, a single injected
    outlier ALSO spikes realised vol, so the gate would force risk-off and the
    test would pass whether or not the cutoff works — passing for the wrong
    reason. Trend alone must carry the assertion.
    """
    cfg = base_cfg(core_vol_gate_mult=0.0)
    good = bars(260, start=250.0, step=-0.5)          # downtrend -> risk-off
    poisoned = good + [{"t": (NOW + timedelta(days=5)).isoformat(),
                        "c": 99999.0}]               # would flip trend risk-on
    emu = FakeEmulator(cash=10000.0, prices=PRICES)
    a = run(cfg, {"QQQ": {"bars": good}}, emu)
    b = run(cfg, {"QQQ": {"bars": poisoned}}, emu)
    assert a.get("TQQQ") is None
    assert a == b


@pytest.mark.parametrize("bear_mode", ["off", "shadow", "active"])
@pytest.mark.parametrize(
    "forged_shadow",
    [
        {
            "mode": "shadow",
            "target_delta": {"AAPL": 0.1},
            "kicker": {"symbol": "SQQQ"},
        },
        {
            "mode": "active",
            "baseline_targets": {"AAPL": 0.0},
            "proposed_targets": {"AAPL": 0.0},
            "target_delta": {"AAPL": 0.1},
            "kicker": {"symbol": "SQQQ"},
        },
        {
            "mode": "active",
            "target_delta": {"AAPL": "0.1"},
            "kicker": {"symbol": "SQQQ"},
        },
        {
            "mode": "shadow",
            "target_delta": {},
            "kicker": {"symbol": "AAPL", "state": "idle"},
        },
    ],
    ids=[
        "shadow-positive-delta",
        "inconsistent-targets",
        "numeric-string-delta",
        "idle-kicker-name",
    ],
)
def test_shadow_telemetry_never_authenticates_aapl_ownership(
    bear_mode, forged_shadow,
):
    data, prices = bear_ready_downtrend_fixture()
    cache = {
        "_sx_bear_owned": ["AAPL"],
        "_sx_bear_shadow": forged_shadow,
    }
    out = StrategyX().run_once(
        list(prices), prices, NOW, base_cfg(bear_system_mode=bear_mode), {},
        data=data,
        portfolio_emulator=FakeEmulator(
            cash=1000.0, positions={"AAPL": 5.0}, prices=prices,
        ),
        strategy_cache=cache,
        mode="backtest" if bear_mode == "active" else None,
    )
    assert "AAPL" not in out
    assert "AAPL" not in out.get("_nexus_sell_enforcement", [])
    assert "AAPL" not in cache.get("_sx_bear_owned", [])


@pytest.mark.parametrize(
    "bad_evidence",
    [
        {"version": "1", "records": {}},
        {"version": True, "records": {}},
        {
            "version": 1,
            "records": {
                "AAPL": {
                    "symbol": "AAPL", "role": "manager",
                    "target_weight": "0.1",
                },
            },
        },
        {
            "version": 1,
            "records": {
                "AAPL": {
                    "symbol": "AAPL", "role": "manager",
                    "target_weight": True,
                },
            },
        },
        {
            "version": 1,
            "records": {
                "AAPL": {
                    "symbol": "AAPL", "role": "manager",
                    "target_weight": float("nan"),
                },
            },
        },
        {
            "version": 1,
            "records": {
                "AAPL": {
                    "symbol": "AAPL", "role": "manager",
                    "target_weight": float("inf"),
                },
            },
        },
        {
            "version": 1,
            "records": {
                "AAPL": {
                    "symbol": "AAPL", "role": "manager",
                    "target_weight": 10**400,
                },
            },
        },
        {
            "version": 1,
            "records": {
                "aapl": {
                    "symbol": "aapl", "role": "manager",
                    "target_weight": 0.1,
                },
            },
        },
        {
            "version": 1,
            "records": {
                "AAPL": {
                    "symbol": "AAPL", "role": "satellite",
                    "target_weight": 0.1,
                },
            },
        },
    ],
    ids=[
        "string-version", "bool-version", "string-weight", "bool-weight",
        "nan-weight", "infinite-weight", "overflowing-weight",
        "unnormalized-symbol",
        "unknown-role",
    ],
)
def test_malformed_structured_evidence_never_authenticates_aapl(bad_evidence):
    data, prices = bear_ready_downtrend_fixture()
    cache = {
        "_sx_bear_owned": ["AAPL"],
        "_sx_bear_ownership_evidence": bad_evidence,
    }
    out = StrategyX().run_once(
        list(prices), prices, NOW, base_cfg(bear_system_mode="off"), {},
        data=data,
        portfolio_emulator=FakeEmulator(
            cash=1000.0, positions={"AAPL": 5.0}, prices=prices,
        ),
        strategy_cache=cache,
    )
    assert "AAPL" not in out
    assert "AAPL" not in out.get("_nexus_sell_enforcement", [])
    assert "AAPL" not in cache.get("_sx_bear_owned", [])
    assert cache.get("_sx_bear_ownership_evidence") == {
        "version": 1, "records": {},
    }
    assert type(cache["_sx_bear_ownership_evidence"]["version"]) is int


def _prime_active_manager_evidence():
    data, prices = bear_ready_downtrend_fixture()
    cache = {}
    StrategyX().run_once(
        list(prices), prices, NOW, base_cfg(bear_system_mode="active"), {},
        data=data, portfolio_emulator=FakeEmulator(prices=prices),
        strategy_cache=cache, mode="backtest",
    )
    return cache, data, prices


def test_exact_active_selection_writes_strict_manager_evidence():
    cache, _, _ = _prime_active_manager_evidence()
    record = cache["_sx_bear_ownership_evidence"]["records"]["DBMF"]
    assert record == {
        "symbol": "DBMF",
        "role": "manager",
        "target_weight": 0.166666,
    }
    assert cache["_sx_bear_ownership_evidence"]["version"] == 1


@pytest.mark.parametrize(
    "bear_mode,runtime_mode",
    [("off", None), ("shadow", None), ("active", "FULL")],
)
def test_nonexecuting_modes_never_mint_ownership_evidence(
    bear_mode, runtime_mode,
):
    data, prices = bear_ready_downtrend_fixture()
    cache = {}
    StrategyX().run_once(
        list(prices), prices, NOW, base_cfg(bear_system_mode=bear_mode), {},
        data=data, portfolio_emulator=FakeEmulator(prices=prices),
        strategy_cache=cache, mode=runtime_mode,
    )
    assert "_sx_bear_ownership_evidence" not in cache


def test_retired_manager_evidence_survives_missing_quote_then_authorizes_exit():
    cache, data, prices = _prime_active_manager_evidence()
    changed_cfg = base_cfg(
        bear_system_mode="active", crisis_alpha_symbols=["KMLM", "CTA"],
    )
    missing_data = {key: value for key, value in data.items() if key != "DBMF"}
    missing_prices = {key: value for key, value in prices.items() if key != "DBMF"}

    first = StrategyX().run_once(
        list(missing_prices), missing_prices, NOW + timedelta(days=1),
        changed_cfg, {}, data=missing_data,
        portfolio_emulator=FakeEmulator(
            cash=1000.0, positions={"DBMF": 10.0}, prices=missing_prices,
        ),
        strategy_cache=cache, mode="backtest",
    )
    assert "DBMF" not in first
    assert "DBMF" in cache["_sx_bear_ownership_evidence"]["records"]

    second = StrategyX().run_once(
        list(prices), prices, NOW + timedelta(days=2), changed_cfg, {},
        data=data,
        portfolio_emulator=FakeEmulator(
            cash=1000.0, positions={"DBMF": 10.0}, prices=prices,
        ),
        strategy_cache=cache, mode="backtest",
    )
    assert second["DBMF"] == -1
    assert second["_nexus_position_sizes"]["DBMF"]["sell_fraction"] == 1.0
    assert "DBMF" in cache["_sx_bear_ownership_evidence"]["records"]

    StrategyX().run_once(
        list(prices), prices, NOW + timedelta(days=3), changed_cfg, {},
        data=data, portfolio_emulator=FakeEmulator(prices=prices),
        strategy_cache=cache, mode="backtest",
    )
    assert "DBMF" not in cache["_sx_bear_ownership_evidence"]["records"]


@pytest.mark.parametrize("bear_mode", ["off", "shadow"])
def test_retired_manager_evidence_survives_nonactive_missing_quote(bear_mode):
    cache, data, prices = _prime_active_manager_evidence()
    changed_cfg = base_cfg(
        bear_system_mode=bear_mode, crisis_alpha_symbols=["KMLM", "CTA"],
    )
    missing_data = {key: value for key, value in data.items() if key != "DBMF"}
    missing_prices = {key: value for key, value in prices.items() if key != "DBMF"}

    first = StrategyX().run_once(
        list(missing_prices), missing_prices, NOW + timedelta(days=1),
        changed_cfg, {}, data=missing_data,
        portfolio_emulator=FakeEmulator(
            cash=1000.0, positions={"DBMF": 10.0}, prices=missing_prices,
        ),
        strategy_cache=cache,
    )
    assert "DBMF" not in first
    assert "DBMF" in cache["_sx_bear_ownership_evidence"]["records"]

    second = StrategyX().run_once(
        list(prices), prices, NOW + timedelta(days=2), changed_cfg, {},
        data=data,
        portfolio_emulator=FakeEmulator(
            cash=1000.0, positions={"DBMF": 10.0}, prices=prices,
        ),
        strategy_cache=cache,
    )
    assert second["DBMF"] == -1
    assert second["_nexus_position_sizes"]["DBMF"]["sell_fraction"] == 1.0
    assert "DBMF" in cache["_sx_bear_ownership_evidence"]["records"]


@pytest.mark.parametrize("remaining_quantity", [10.0, 4.0], ids=["unfilled", "partial"])
def test_retired_manager_evidence_survives_unfilled_or_partial_exit(
    remaining_quantity,
):
    cache, data, prices = _prime_active_manager_evidence()
    changed_cfg = base_cfg(
        bear_system_mode="active", crisis_alpha_symbols=["KMLM", "CTA"],
    )
    first = StrategyX().run_once(
        list(prices), prices, NOW + timedelta(days=1), changed_cfg, {},
        data=data,
        portfolio_emulator=FakeEmulator(
            cash=1000.0, positions={"DBMF": 10.0}, prices=prices,
        ),
        strategy_cache=cache, mode="backtest",
    )
    assert first["DBMF"] == -1

    second = StrategyX().run_once(
        list(prices), prices, NOW + timedelta(days=2), changed_cfg, {},
        data=data,
        portfolio_emulator=FakeEmulator(
            cash=1000.0, positions={"DBMF": remaining_quantity}, prices=prices,
        ),
        strategy_cache=cache, mode="backtest",
    )
    assert second["DBMF"] == -1
    assert second["_nexus_position_sizes"]["DBMF"]["sell_fraction"] == 1.0
    assert "DBMF" in cache["_sx_bear_ownership_evidence"]["records"]


def _prime_active_kicker_evidence():
    cache = {}
    first_data, prices = bear_session_data()
    StrategyX().run_once(
        list(prices), prices, NOW, base_cfg(bear_system_mode="active"), {},
        data=first_data,
        portfolio_emulator=FakeEmulator(
            cash=1000.0, positions={"TQQQ": 10.0}, prices=prices,
        ),
        strategy_cache=cache, mode="backtest",
    )
    second_data, _ = bear_session_data(follow_through=True)
    StrategyX().run_once(
        list(prices), prices, NOW + timedelta(days=1),
        base_cfg(bear_system_mode="active"), {}, data=second_data,
        portfolio_emulator=FakeEmulator(prices=prices),
        strategy_cache=cache, mode="backtest",
    )
    return cache, second_data, prices


def test_retired_kicker_evidence_survives_missing_quote_then_authorizes_exit():
    cache, data, prices = _prime_active_kicker_evidence()
    assert cache["_sx_bear_ownership_evidence"]["records"]["SQQQ"]["role"] == (
        "kicker"
    )
    changed_data = dict(data)
    changed_data["PSQ"] = {"bars": bars(80, start=10.0, step=0.01)}
    changed_prices = dict(prices, PSQ=10.0)
    changed_cfg = base_cfg(bear_system_mode="active", bear_kicker_symbol="PSQ")
    missing_data = {key: value for key, value in changed_data.items()
                    if key != "SQQQ"}
    missing_prices = {key: value for key, value in changed_prices.items()
                      if key != "SQQQ"}

    first = StrategyX().run_once(
        list(missing_prices), missing_prices, NOW + timedelta(days=2),
        changed_cfg, {}, data=missing_data,
        portfolio_emulator=FakeEmulator(
            cash=1000.0, positions={"SQQQ": 10.0}, prices=missing_prices,
        ),
        strategy_cache=cache, mode="backtest",
    )
    assert "SQQQ" not in first
    assert "SQQQ" in cache["_sx_bear_ownership_evidence"]["records"]

    second = StrategyX().run_once(
        list(changed_prices), changed_prices, NOW + timedelta(days=3),
        changed_cfg, {}, data=changed_data,
        portfolio_emulator=FakeEmulator(
            cash=1000.0, positions={"SQQQ": 10.0}, prices=changed_prices,
        ),
        strategy_cache=cache, mode="backtest",
    )
    assert second["SQQQ"] == -1
    assert second["_nexus_position_sizes"]["SQQQ"]["sell_fraction"] == 1.0


@pytest.mark.parametrize(
    "bear_mode,runtime_mode", [("shadow", None), ("active", "FULL")],
)
def test_no_history_ownership_conflict_precedes_generic_refusal(
    bear_mode, runtime_mode,
):
    _, prices = bear_ready_downtrend_fixture()
    cache = {}
    out = StrategyX().run_once(
        list(prices), prices, NOW, base_cfg(bear_system_mode=bear_mode), {},
        data={},
        portfolio_emulator=FakeEmulator(
            cash=1000.0, positions={"DBMF": 10.0}, prices=prices,
        ),
        strategy_cache=cache, mode=runtime_mode,
    )
    conflict = "ownership conflict: unprovenanced bear holding(s): DBMF"
    assert out == {}
    assert cache["_strategy_x_last"]["bear_overlay_reason"] == conflict
    assert cache["_sx_bear_shadow"]["refusal_reason"] == conflict
    assert cache["_sx_bear_shadow"]["baseline_targets"] == {}
    assert cache["_sx_bear_shadow"]["proposed_targets"] == {}
    assert cache["_sx_bear_shadow"]["target_delta"] == {}
    assert not cache.get("_sx_bear_owned")


def test_no_history_exact_active_still_raises_ownership_conflict():
    _, prices = bear_ready_downtrend_fixture()
    with pytest.raises(
        BearSystemStateError,
        match="ownership conflict: unprovenanced bear holding.*DBMF",
    ):
        StrategyX().run_once(
            list(prices), prices, NOW,
            base_cfg(bear_system_mode="active"), {}, data={},
            portfolio_emulator=FakeEmulator(
                cash=1000.0, positions={"DBMF": 10.0}, prices=prices,
            ),
            strategy_cache={}, mode="backtest",
        )


def test_no_history_refused_active_keeps_provenance_owned_emergency_exit():
    cache, _, prices = _prime_active_manager_evidence()
    out = StrategyX().run_once(
        list(prices), prices, NOW + timedelta(days=1),
        base_cfg(
            bear_system_mode="active", crisis_alpha_symbols=["KMLM", "CTA"],
        ),
        {}, data={},
        portfolio_emulator=FakeEmulator(
            cash=1000.0, positions={"DBMF": 10.0}, prices=prices,
        ),
        strategy_cache=cache, mode="FULL",
    )
    assert out["DBMF"] == -1
    assert out["_nexus_position_sizes"]["DBMF"]["sell_fraction"] == 1.0
    assert cache["_strategy_x_last"]["bear_overlay_reason"] == (
        "research-only runtime"
    )
    assert "DBMF" in cache["_sx_bear_ownership_evidence"]["records"]


def test_shadow_telemetry_cannot_authenticate_kicker_state_or_mint_evidence():
    data, prices = bear_session_data(follow_through=True)
    forged_cache = {
        "_sx_bear_state_version": 1,
        "_sx_bear_system_state": "armed",
        "_sx_bear_kicker_bars": 0,
        "_sx_bear_kicker_cooldown": 0,
        "_sx_bear_kicker_entry_day": "",
        "_sx_bear_kicker_targeted": False,
        "_sx_bear_owned": [],
        "_sx_bear_shadow": {
            "kicker": {"symbol": "SQQQ", "state": "idle"},
        },
    }
    clean_cache = {
        key: value for key, value in forged_cache.items()
        if key != "_sx_bear_shadow"
    }
    forged = StrategyX().run_once(
        list(prices), prices, NOW, base_cfg(bear_system_mode="active"), {},
        data=data, portfolio_emulator=FakeEmulator(prices=prices),
        strategy_cache=forged_cache, mode="backtest",
    )
    clean = StrategyX().run_once(
        list(prices), prices, NOW, base_cfg(bear_system_mode="active"), {},
        data=data, portfolio_emulator=FakeEmulator(prices=prices),
        strategy_cache=clean_cache, mode="backtest",
    )
    assert "SQQQ" not in forged
    assert "SQQQ" not in forged.get("_nexus_executable_buys", [])
    assert "SQQQ" not in forged_cache.get("_sx_bear_owned", [])
    assert "SQQQ" not in forged_cache.get(
        "_sx_bear_ownership_evidence", {}
    ).get("records", {})
    assert {
        key: value for key, value in forged.items() if not key.startswith("_")
    } == {
        key: value for key, value in clean.items() if not key.startswith("_")
    }


@pytest.mark.parametrize("bear_mode", ["off", "shadow"])
def test_shadow_telemetry_cannot_retain_flat_raw_kicker_ownership(bear_mode):
    data, prices = bear_ready_downtrend_fixture()
    cache = {
        "_sx_bear_kicker_targeted": True,
        "_sx_bear_owned": ["SQQQ"],
        "_sx_bear_shadow": {"kicker": {"symbol": "SQQQ"}},
    }
    StrategyX().run_once(
        list(prices), prices, NOW, base_cfg(bear_system_mode=bear_mode), {},
        data=data, portfolio_emulator=FakeEmulator(prices=prices),
        strategy_cache=cache,
    )
    assert "SQQQ" not in cache.get("_sx_bear_owned", [])
    assert cache["_sx_bear_kicker_targeted"] is False


def test_multiple_kicker_evidence_records_block_replacement_without_inference():
    data, prices = bear_session_data(follow_through=True)
    prices["PSQ"] = 10.0
    data["PSQ"] = {"bars": bars(80, start=10.0, step=0.01)}
    cache = {
        "_sx_bear_state_version": 1,
        "_sx_bear_system_state": "holding",
        "_sx_bear_kicker_bars": 1,
        "_sx_bear_kicker_cooldown": 0,
        "_sx_bear_kicker_entry_day": NOW.date().isoformat(),
        "_sx_bear_kicker_state_symbol": "PSQ",
        "_sx_bear_kicker_targeted": True,
        "_sx_bear_pending_kicker_symbol": "PSQ",
        "_sx_bear_owned": ["PSQ", "SQQQ"],
        "_sx_bear_ownership_evidence": {
            "version": 1,
            "records": {
                "PSQ": {
                    "symbol": "PSQ",
                    "role": "kicker",
                    "target_weight": 0.05,
                },
                "SQQQ": {
                    "symbol": "SQQQ",
                    "role": "kicker",
                    "target_weight": 0.05,
                },
            },
        },
    }
    out = StrategyX().run_once(
        list(prices), prices, NOW,
        base_cfg(bear_system_mode="active", bear_kicker_symbol="PSQ"), {},
        data=data, portfolio_emulator=FakeEmulator(prices=prices),
        strategy_cache=cache, mode="backtest",
    )
    assert "PSQ" not in out
    assert "PSQ" not in out.get("_nexus_executable_buys", [])
    assert cache["_sx_bear_kicker_targeted"] is True
    assert cache["_sx_bear_pending_kicker_symbol"] == "PSQ"
    assert {
        symbol for symbol, record
        in cache["_sx_bear_ownership_evidence"]["records"].items()
        if record["role"] == "kicker"
    } == {"PSQ", "SQQQ"}


@pytest.mark.parametrize(
    "pending_result",
    [
        "missing-reader", None, "SQQQ", {"SQQQ": True}, ["sqqq"], [1],
        RuntimeError("boom"),
    ],
    ids=[
        "missing-reader", "none", "scalar", "mapping", "unnormalized",
        "non-string", "error",
    ],
)
def test_invalid_or_throwing_pending_read_blocks_kicker_buy_and_mint(
    pending_result,
):
    data, prices = bear_session_data(follow_through=True)
    cache = {
        "_sx_bear_state_version": 1,
        "_sx_bear_system_state": "armed",
        "_sx_bear_kicker_bars": 0,
        "_sx_bear_kicker_cooldown": 0,
        "_sx_bear_kicker_entry_day": "",
        "_sx_bear_kicker_state_symbol": "SQQQ",
        "_sx_bear_kicker_targeted": False,
        "_sx_bear_pending_kicker_symbol": "",
        "_sx_bear_owned": [],
    }
    emulator = FakeEmulator(prices=prices, pending_symbols=pending_result)
    if pending_result == "missing-reader":
        emulator.pending_execution_symbols = None
    out = StrategyX().run_once(
        list(prices), prices, NOW, base_cfg(bear_system_mode="active"), {},
        data=data,
        portfolio_emulator=emulator,
        strategy_cache=cache, mode="backtest",
    )
    assert "SQQQ" not in out
    assert "SQQQ" not in out.get("_nexus_executable_buys", [])
    assert "SQQQ" not in cache.get("_sx_bear_owned", [])
    assert "SQQQ" not in cache.get(
        "_sx_bear_ownership_evidence", {}
    ).get("records", {})


def test_real_pending_same_symbol_buy_suppresses_retries_and_topups():
    data, prices = bear_session_data(follow_through=True)
    cache = {
        "_sx_bear_state_version": 1,
        "_sx_bear_system_state": "armed",
        "_sx_bear_kicker_bars": 0,
        "_sx_bear_kicker_cooldown": 0,
        "_sx_bear_kicker_entry_day": "",
        "_sx_bear_kicker_state_symbol": "SQQQ",
        "_sx_bear_kicker_targeted": False,
        "_sx_bear_pending_kicker_symbol": "",
        "_sx_bear_owned": [],
    }
    simulator = NextEventExecutionSimulator(
        DEFAULT_EQUITY_EXECUTION_COST_MODEL
    )
    emulator = PortfolioEmulator(1000.0, execution_simulator=simulator)
    first = StrategyX().run_once(
        list(prices), prices, NOW, base_cfg(
            bear_system_mode="active", min_order_usd=1.0,
        ), {}, data=data, portfolio_emulator=emulator,
        strategy_cache=cache, mode="backtest",
    )
    assert first["SQQQ"] == 1
    receipt = emulator.execute_signal(
        "SQQQ", 1, prices["SQQQ"], timestamp=NOW,
        cash_per_trade=40.0, order_source="strategy_x",
    )
    assert receipt and simulator.pending_order_count == 1

    retry = StrategyX().run_once(
        list(prices), prices, NOW + timedelta(days=1), base_cfg(
            bear_system_mode="active", min_order_usd=1.0,
        ), {}, data=data, portfolio_emulator=emulator,
        strategy_cache=cache, mode="backtest",
    )
    assert "SQQQ" not in retry
    assert "SQQQ" not in retry.get("_nexus_executable_buys", [])
    assert simulator.pending_order_count == 1
    assert cache["_sx_bear_pending_kicker_symbol"] == "SQQQ"
    assert "SQQQ" in cache["_sx_bear_ownership_evidence"]["records"]


def test_pending_bear_sleeve_fill_keeps_provenance_across_regime_flip():
    """A prior risk-off order may fill after the next risk-on decision.

    Cash and manager entries use the same next-event execution book as the
    kicker.  Their evidence must survive while those entries are pending so a
    delayed fill is recognized as Strategy X-owned and can be exited safely.
    """
    bear_data, prices = bear_ready_downtrend_fixture()
    cfg = base_cfg(bear_system_mode="active", min_order_usd=1.0)
    cache = {}

    risk_off = StrategyX().run_once(
        list(prices), prices, NOW, cfg, {}, data=bear_data,
        portfolio_emulator=FakeEmulator(prices=prices),
        strategy_cache=cache, mode="backtest",
    )
    pending_bear = {"BIL", "DBMF", "KMLM", "CTA"}
    assert pending_bear <= set(risk_off.get("_nexus_executable_buys", []))
    assert pending_bear <= set(
        cache["_sx_bear_ownership_evidence"]["records"]
    )

    risk_on_data = dict(bear_data)
    risk_on_data["QQQ"] = {"bars": bars(260)}
    StrategyX().run_once(
        list(prices), prices, NOW + timedelta(days=1), cfg, {},
        data=risk_on_data,
        portfolio_emulator=FakeEmulator(
            prices=prices, pending_symbols=tuple(sorted(pending_bear)),
        ),
        strategy_cache=cache, mode="backtest",
    )
    assert pending_bear <= set(
        cache["_sx_bear_ownership_evidence"]["records"]
    )

    delayed_positions = {
        "BIL": 5.0, "DBMF": 5.0, "KMLM": 5.0, "CTA": 5.0,
    }
    after_fill = StrategyX().run_once(
        list(prices), prices, NOW + timedelta(days=2), cfg, {},
        data=risk_on_data,
        portfolio_emulator=FakeEmulator(
            cash=500.0, positions=delayed_positions, prices=prices,
        ),
        strategy_cache=cache, mode="backtest",
    )
    assert pending_bear <= set(after_fill.get("_nexus_sell_enforcement", []))
    assert pending_bear <= set(
        cache["_sx_bear_ownership_evidence"]["records"]
    )

    StrategyX().run_once(
        list(prices), prices, NOW + timedelta(days=3), cfg, {},
        data=risk_on_data,
        portfolio_emulator=FakeEmulator(prices=prices),
        strategy_cache=cache, mode="backtest",
    )
    assert not cache["_sx_bear_ownership_evidence"]["records"]


def test_failed_pending_reader_retains_only_strict_manager_evidence():
    data, prices = bear_ready_downtrend_fixture()
    data["QQQ"] = {"bars": bars(260)}
    cache = {
        "_sx_bear_owned": ["CTA", "DBMF"],
        "_sx_bear_ownership_evidence": ownership_evidence(
            "DBMF", "manager", 0.2,
        ),
    }

    StrategyX().run_once(
        list(prices), prices, NOW,
        base_cfg(bear_system_mode="active"), {}, data=data,
        portfolio_emulator=FakeEmulator(
            prices=prices, pending_symbols=RuntimeError("book unavailable"),
        ),
        strategy_cache=cache, mode="backtest",
    )

    assert cache["_sx_bear_owned"] == ["DBMF"]
    assert set(cache["_sx_bear_ownership_evidence"]["records"]) == {"DBMF"}


def test_retired_kicker_blocks_new_kicker_until_exit_then_delayed_fill_is_owned():
    cache, _, _ = _prime_active_kicker_evidence()

    def kicker_records():
        return {
            symbol for symbol, record
            in cache["_sx_bear_ownership_evidence"]["records"].items()
            if record["role"] == "kicker"
        }

    assert kicker_records() == {"SQQQ"}

    cfg = base_cfg(
        bear_system_mode="active", bear_kicker_symbol="PSQ",
        min_order_usd=1.0,
    )

    def changed_market(*, fresh=False, sqqq_priceable=False):
        data, prices = bear_session_data(follow_through=not fresh)
        data["PSQ"] = {"bars": bars(80, start=10.0, step=0.01)}
        prices["PSQ"] = 10.0
        if not sqqq_priceable:
            data.pop("SQQQ", None)
            prices.pop("SQQQ", None)
        return data, prices

    held_old = {"SQQQ": 10.0}
    day = 2

    # The ticker change invalidates SQQQ-bound state and starts PSQ cooldown.
    data, prices = changed_market()
    first = StrategyX().run_once(
        list(prices), prices, NOW + timedelta(days=day), cfg, {},
        data=at_day(data, day),
        portfolio_emulator=FakeEmulator(
            cash=1000.0, positions=held_old, prices=prices,
        ),
        strategy_cache=cache, mode="backtest",
    )
    assert "PSQ" not in first and "SQQQ" not in first
    assert cache["_sx_bear_kicker_cooldown"] == 10
    assert kicker_records() == {"SQQQ"}

    # Spend all ten PSQ cooldown decisions while the retired SQQQ exit has no
    # quote. No session may target the replacement ticker.
    for _ in range(10):
        day += 1
        data, prices = changed_market()
        out = StrategyX().run_once(
            list(prices), prices, NOW + timedelta(days=day), cfg, {},
            data=at_day(data, day),
            portfolio_emulator=FakeEmulator(
                cash=1000.0, positions=held_old, prices=prices,
            ),
            strategy_cache=cache, mode="backtest",
        )
        assert "PSQ" not in out and "SQQQ" not in out
        assert kicker_records() == {"SQQQ"}
    assert cache["_sx_bear_system_state"] == "idle"

    # A new fresh event and its confirmation are ignored while SQQQ remains.
    day += 1
    data, prices = changed_market(fresh=True)
    armed_attempt = StrategyX().run_once(
        list(prices), prices, NOW + timedelta(days=day), cfg, {}, data=data,
        portfolio_emulator=FakeEmulator(
            cash=1000.0, positions=held_old, prices=prices,
        ),
        strategy_cache=cache, mode="backtest",
    )
    assert "PSQ" not in armed_attempt
    assert cache["_sx_bear_system_state"] == "idle"

    day += 1
    data, prices = changed_market()
    confirmation_attempt = StrategyX().run_once(
        list(prices), prices, NOW + timedelta(days=day), cfg, {}, data=data,
        portfolio_emulator=FakeEmulator(
            cash=1000.0, positions=held_old, prices=prices,
        ),
        strategy_cache=cache, mode="backtest",
    )
    assert "PSQ" not in confirmation_attempt
    assert kicker_records() == {"SQQQ"}

    # When SQQQ becomes priceable, keep its evidence while issuing a full exit.
    day += 1
    data, prices = changed_market(sqqq_priceable=True)
    retired_exit = StrategyX().run_once(
        list(prices), prices, NOW + timedelta(days=day), cfg, {}, data=data,
        portfolio_emulator=FakeEmulator(
            cash=1000.0, positions=held_old, prices=prices,
        ),
        strategy_cache=cache, mode="backtest",
    )
    assert retired_exit["SQQQ"] == -1
    assert retired_exit["_nexus_position_sizes"]["SQQQ"]["sell_fraction"] == 1.0
    assert "PSQ" not in retired_exit
    assert kicker_records() == {"SQQQ"}

    # Observe the exit fill before allowing a later fresh PSQ event.
    day += 1
    data, prices = changed_market(sqqq_priceable=True)
    StrategyX().run_once(
        list(prices), prices, NOW + timedelta(days=day), cfg, {}, data=data,
        portfolio_emulator=FakeEmulator(cash=1000.0, prices=prices),
        strategy_cache=cache, mode="backtest",
    )
    assert kicker_records() == set()

    day += 1
    data, prices = changed_market(fresh=True, sqqq_priceable=True)
    StrategyX().run_once(
        list(prices), prices, NOW + timedelta(days=day), cfg, {}, data=data,
        portfolio_emulator=FakeEmulator(cash=1000.0, prices=prices),
        strategy_cache=cache, mode="backtest",
    )
    assert cache["_sx_bear_system_state"] == "armed"

    # Confirm PSQ, leave the buy unfilled for one decision, then deliver the
    # fill one decision later. Explicit pending identity must keep provenance.
    day += 1
    data, prices = changed_market(sqqq_priceable=True)
    target = StrategyX().run_once(
        list(prices), prices, NOW + timedelta(days=day), cfg, {}, data=data,
        portfolio_emulator=FakeEmulator(cash=1000.0, prices=prices),
        strategy_cache=cache, mode="backtest",
    )
    assert target["PSQ"] == 1
    assert cache["_sx_bear_pending_kicker_symbol"] == "PSQ"
    assert kicker_records() == {"PSQ"}

    day += 1
    unfilled = StrategyX().run_once(
        list(prices), prices, NOW + timedelta(days=day), cfg, {}, data=data,
        portfolio_emulator=FakeEmulator(cash=1000.0, prices=prices),
        strategy_cache=cache, mode="backtest",
    )
    assert unfilled["PSQ"] == 1
    assert cache["_sx_bear_pending_kicker_symbol"] == "PSQ"
    assert kicker_records() == {"PSQ"}

    day += 1
    delayed_fill = StrategyX().run_once(
        list(prices), prices, NOW + timedelta(days=day), cfg, {}, data=data,
        portfolio_emulator=FakeEmulator(
            cash=900.0, positions={"PSQ": 10.0}, prices=prices,
        ),
        strategy_cache=cache, mode="backtest",
    )
    assert "PSQ" not in delayed_fill.get("_nexus_sell_enforcement", [])
    assert kicker_records() == {"PSQ"}


def test_real_pending_retired_buy_blocks_replacement_through_delayed_fill():
    cache, _, _ = _prime_active_kicker_evidence()
    simulator = NextEventExecutionSimulator(
        DEFAULT_EQUITY_EXECUTION_COST_MODEL
    )
    emulator = PortfolioEmulator(1000.0, execution_simulator=simulator)
    receipt = emulator.execute_signal(
        "SQQQ", 1, 8.0, timestamp=NOW + timedelta(days=1),
        cash_per_trade=40.0, order_source="strategy_x",
    )
    assert receipt and simulator.pending_symbols == ("SQQQ",)
    assert emulator.get_positions() == {}

    cfg = base_cfg(
        bear_system_mode="active", bear_kicker_symbol="PSQ",
        min_order_usd=1.0,
    )

    def changed_market(*, fresh=False, sqqq_priceable=False):
        data, prices = bear_session_data(follow_through=not fresh)
        data["PSQ"] = {"bars": bars(80, start=10.0, step=0.01)}
        prices["PSQ"] = 10.0
        if not sqqq_priceable:
            data.pop("SQQQ", None)
            prices.pop("SQQQ", None)
        return data, prices

    def decide(day, *, fresh=False, sqqq_priceable=False):
        data, prices = changed_market(
            fresh=fresh, sqqq_priceable=sqqq_priceable,
        )
        return StrategyX().run_once(
            list(prices), prices, NOW + timedelta(days=day), cfg, {},
            data=at_day(data, day),
            portfolio_emulator=emulator, strategy_cache=cache,
            mode="backtest",
        )

    # The old buy remains in the real next-event book without an SQQQ quote.
    # It must block PSQ throughout its config-reset cooldown and later signals.
    for day in range(2, 13):
        out = decide(day)
        assert "PSQ" not in out
        assert simulator.pending_symbols == ("SQQQ",)
        assert cache["_sx_bear_pending_kicker_symbol"] == "SQQQ"
        assert "SQQQ" in cache["_sx_bear_ownership_evidence"]["records"]

    assert cache["_sx_bear_system_state"] == "idle"
    assert "PSQ" not in decide(13, fresh=True)
    assert cache["_sx_bear_system_state"] == "idle"
    assert "PSQ" not in decide(14)

    # A much later quote fills the original buy. Provenance and the blocker
    # must still exist when Strategy X next observes the position.
    fills = emulator.process_quote(SimulationQuote.from_mid(
        symbol="SQQQ", timestamp=NOW + timedelta(days=15), mid=8.0,
        spread_bps=5.0,
    ))
    assert fills and simulator.pending_symbols == ()
    assert emulator.get_positions().get("SQQQ", 0.0) > 0
    after_fill = decide(15)
    assert "PSQ" not in after_fill
    assert "SQQQ" in cache["_sx_bear_ownership_evidence"]["records"]

    # Once priceable, the retired fill exits completely. Only a later fresh
    # event may start PSQ's state machine.
    retired_exit = decide(16, sqqq_priceable=True)
    assert retired_exit["SQQQ"] == -1
    emulator.execute_signal(
        "SQQQ", -1, 8.0, timestamp=NOW + timedelta(days=16),
        sell_fraction=1.0, order_source="strategy_x",
    )
    emulator.process_quote(SimulationQuote.from_mid(
        symbol="SQQQ", timestamp=NOW + timedelta(days=17), mid=8.0,
        spread_bps=5.0,
    ))
    assert "SQQQ" not in emulator.get_positions()
    decide(17, sqqq_priceable=True)
    assert "SQQQ" not in cache["_sx_bear_ownership_evidence"]["records"]

    assert "PSQ" not in decide(18, fresh=True, sqqq_priceable=True)
    assert cache["_sx_bear_system_state"] == "armed"
    replacement = decide(19, sqqq_priceable=True)
    assert replacement["PSQ"] == 1
    assert "SQQQ" not in emulator.get_positions()
