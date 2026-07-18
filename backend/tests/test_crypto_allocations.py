"""Tests for the fixed+dynamic allocation overlay (crypto_config.allocations).

The overlay MUST emit dict-valued _nexus_position_sizes (never bare floats — a
prior bug crashed the broker with `'buy_cash' in <float>`), honor fixed % / $
targets by rebalancing, split the remainder across the strategy's dynamic buys,
and set the control keys that let the broker deploy 100%.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from strategies.crypto import core  # noqa: E402


def _all_sizes_are_dicts(out):
    sizes = out.get("_nexus_position_sizes", {})
    return all(isinstance(v, dict) for k, v in sizes.items() if not k.startswith("_"))


def test_no_allocations_is_noop():
    out = {"BTC/USD": 1}
    res = core.overlay_allocations(dict(out), [], 10000.0, {"BTC/USD": 50000}, {})
    assert res == out
    assert "_nexus_position_sizes" not in res


def test_fixed_pct_buys_from_flat():
    out = {}
    res = core.overlay_allocations(
        out, [{"symbol": "BTC/USD", "pct": 0.10}], 10000.0,
        {"BTC/USD": 50000.0}, {},  # no position held
    )
    assert res["BTC/USD"] == 1
    s = res["_nexus_position_sizes"]["BTC/USD"]
    assert isinstance(s, dict)
    assert abs(s["buy_cash"] - 1000.0) < 1e-6      # 10% of 10k
    assert s["asset_class"] == "crypto" and s["high_conviction"] is True
    assert res["_nexus_position_sizes"]["_cash_reserve_floor_pct"] == 0.0
    assert res["_nexus_position_sizes"]["_buy_price_floor"] == 0.0
    assert "BTC/USD" in res["_nexus_discovered"]
    assert _all_sizes_are_dicts(res)


def test_fixed_at_target_holds():
    # 10% of 10k = $1000 target; hold 0.02 BTC @ 50k = $1000 (on target)
    res = core.overlay_allocations(
        {}, [{"symbol": "BTC/USD", "pct": 0.10}], 10000.0,
        {"BTC/USD": 50000.0}, {"BTC/USD": 0.02},
    )
    assert res["BTC/USD"] == 0  # within tolerance band -> hold


def test_fixed_over_target_sells_fraction():
    # target $1000; hold 0.04 BTC @ 50k = $2000 -> sell half
    res = core.overlay_allocations(
        {}, [{"symbol": "BTC/USD", "pct": 0.10}], 10000.0,
        {"BTC/USD": 50000.0}, {"BTC/USD": 0.04},
    )
    assert res["BTC/USD"] == -1
    s = res["_nexus_position_sizes"]["BTC/USD"]
    assert "sell_fraction" in s and abs(s["sell_fraction"] - 0.5) < 1e-3


def test_usd_allocation_target():
    res = core.overlay_allocations(
        {}, [{"symbol": "SOL/USD", "usd": 2500}], 10000.0, {"SOL/USD": 100.0}, {},
    )
    assert abs(res["_nexus_position_sizes"]["SOL/USD"]["buy_cash"] - 2500.0) < 1e-6


def test_dynamic_remainder_split_across_strategy_buys():
    # 20% BTC fixed -> dynamic budget = 80% of 10k = 8000, split over 2 dyn buys
    out = {"AVAX/USD": 1, "LINK/USD": 1}
    res = core.overlay_allocations(
        out, [{"symbol": "BTC/USD", "pct": 0.20}], 10000.0,
        {"BTC/USD": 50000.0, "AVAX/USD": 30.0, "LINK/USD": 15.0}, {},
    )
    assert abs(res["_nexus_position_sizes"]["AVAX/USD"]["buy_cash"] - 4000.0) < 1e-6
    assert abs(res["_nexus_position_sizes"]["LINK/USD"]["buy_cash"] - 4000.0) < 1e-6
    assert _all_sizes_are_dicts(res)


def test_apply_crypto_config_reads_injected_blob():
    class PE:
        def get_portfolio_value(self, prices):
            return 10000.0
        def get_positions(self):
            return {}
    cfg = {"crypto_config": {"allocations": [{"symbol": "ETH/USD", "pct": 0.30}]}}
    res = core.apply_crypto_config({}, cfg, {"ETH/USD": 3000.0}, PE())
    assert res["ETH/USD"] == 1
    assert abs(res["_nexus_position_sizes"]["ETH/USD"]["buy_cash"] - 3000.0) < 1e-6


def test_apply_crypto_config_noop_without_allocations():
    res = core.apply_crypto_config({"BTC/USD": 1}, {"crypto_config": {}}, {}, None)
    assert res == {"BTC/USD": 1}
    assert "_nexus_position_sizes" not in res


def test_zero_pv_is_safe():
    res = core.overlay_allocations({"X/USD": 1}, [{"symbol": "BTC/USD", "pct": 0.1}], 0.0, {}, {})
    assert res == {"X/USD": 1}
