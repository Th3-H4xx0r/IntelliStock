# INTELLISTOCK_SCHEMA: {"strategy": "Reference", "weight": 1.0, "execution_position": 0, "decision_phase": "pre", "execution_scope": "run_once", "conditions": {}, "config": {"band": "medium", "drift_threshold": 0.05, "deploy_fraction": 0.98}}
# INTELLISTOCK_DESCRIPTION: Crypto reference strategy. Holds a fixed equal target weight across its pairs (or the discovered universe when the seed list is empty) and emits buy/sell votes to rebalance toward target each tick. Proves the crypto platform end-to-end.
# DIFFICULTY: 1
"""
Reference crypto strategy (run_once). Strategy name from DB: "reference"
(file reference.py, class Reference).

The simplest thing that proves the platform: an equal-weight rebalancer.
- Target weight = 1 / N across the active universe.
- If a pair is under target by more than the drift band -> buy (1).
- If a pair is over target by more than the drift band -> trim (-1).
- Otherwise hold (0).
The drift band is at least ``core.min_edge_to_trade(maker=False)`` — the adapter
places MARKET (taker) orders, so we size the band to the real taker round-trip
cost and never churn for a move that can't clear it. Returns 1 = buy, 0 = hold,
-1 = sell, plus ``_nexus_discovered`` for any auto-picked pairs. Per-symbol
sizing is left to the broker's default sizing.
"""

from __future__ import annotations

from typing import List, Mapping, Optional

try:
    import sys
    import os
    _crypto_dir = os.path.dirname(os.path.abspath(__file__))
    _strategies_dir = os.path.dirname(_crypto_dir)
    _backend_dir = os.path.dirname(_strategies_dir)
    if _backend_dir not in sys.path:
        sys.path.insert(0, _backend_dir)
except Exception:
    pass

from strategies.crypto import core


def _last_price(prices: Optional[Mapping], data: Optional[Mapping], sym: str) -> Optional[float]:
    """Best available current price: live ``prices`` first, else last bar close."""
    p = (prices or {}).get(sym)
    if p:
        try:
            return float(p)
        except (TypeError, ValueError):
            pass
    bars = (data or {}).get(sym) or []
    for b in reversed(bars):
        c = b.get("c")
        if c is not None:
            try:
                return float(c)
            except (TypeError, ValueError):
                continue
    return None


def _active_universe(symbols, prices, data):
    """Seed pairs (upper-cased) or the discovered bar universe when seed empty.

    Only pairs with a usable price/bars are kept. Returns (universe, discovered).
    """
    seed = [str(s).strip().upper() for s in (symbols or []) if str(s).strip()]
    if seed:
        candidates = seed
    else:
        candidates = sorted((data or {}).keys())
    universe = [s for s in candidates if _last_price(prices, data, s) is not None]
    seed_set = set(seed)
    discovered = [s for s in universe if s not in seed_set]
    return universe, discovered


def _current_weights(portfolio_emulator, prices, universe):
    """Map each pair to its current portfolio weight (0 when flat / no emulator)."""
    positions: dict = {}
    total = 0.0
    if portfolio_emulator is not None:
        try:
            positions = portfolio_emulator.get_positions() or {}
        except Exception:
            positions = {}
        try:
            total = float(portfolio_emulator.get_portfolio_value(prices))
        except Exception:
            total = 0.0
    weights = {}
    for sym in universe:
        shares = core.position_qty(positions, sym)
        px = float((prices or {}).get(sym) or 0)
        weights[sym] = (shares * px / total) if total > 0 else 0.0
    return weights


class Reference:
    """Equal-weight crypto rebalancer (platform proof)."""

    def __init__(self):
        self.drift_threshold = 0.05
        self.deploy_fraction = 0.98

    def run_once(
        self,
        symbols,
        prices,
        current_time,
        config,
        conditions,
        data=None,
        portfolio_emulator=None,
        strategy_cache=None,
        time_increment=None,
        mode=None,
    ) -> dict:
        settings = {}
        if isinstance(conditions, dict):
            settings.update(conditions)
        if isinstance(config, dict):
            settings.update(config)

        if mode == "IDLE":
            return {}

        universe, discovered = _active_universe(symbols, prices, data)
        result: dict = {}
        if discovered:
            result["_nexus_discovered"] = list(discovered)
        if not universe:
            return core.apply_crypto_config(result, config, prices, portfolio_emulator)

        deploy = float(settings.get("deploy_fraction", self.deploy_fraction))
        deploy = max(0.0, min(deploy, 1.0))
        target = deploy / len(universe)

        edge = core.min_edge_to_trade(maker=False)
        band = max(float(settings.get("drift_threshold", self.drift_threshold)), edge)

        weights = _current_weights(portfolio_emulator, prices, universe)
        for sym in universe:
            cur = weights.get(sym, 0.0)
            if cur < target - band:
                result[sym] = 1
            elif cur > target + band:
                result[sym] = -1
            else:
                result[sym] = 0
        return core.apply_crypto_config(result, config, prices, portfolio_emulator)
