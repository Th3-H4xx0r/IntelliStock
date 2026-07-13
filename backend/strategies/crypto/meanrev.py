# INTELLISTOCK_SCHEMA: {"strategy": "MeanRev", "weight": 1.0, "execution_position": 0, "decision_phase": "pre", "execution_scope": "run_once", "conditions": {}, "config": {"band": "medium", "rsi_period": 14, "rsi_buy": 35, "rsi_exit": 55, "regime_ma": 200, "top_k": 2}}
# INTELLISTOCK_DESCRIPTION: Regime-filtered RSI mean-reversion for crypto. Buys oversold majors (RSI below rsi_buy) ONLY while they hold above a long regime MA ("buy healthy dips, not falling knives"), holds up to top_k, and banks the bounce when RSI recovers past rsi_exit. Sits in cash most of the time — in backtests it stayed positive across bull, chop, and a -42% BTC drawdown while trend/momentum strategies were whipsawed.
# DIFFICULTY: 3
"""
Mean-reversion crypto strategy (run_once). DB name: "meanrev".

Long-only dip-buying with a regime gate on ENTRY only:
- Regime: a coin is only ENTERED while close > SMA(regime_ma) (a long uptrend).
- Entry: among regime-OK coins, buy (1) the most-oversold (RSI < rsi_buy),
  filling up to ``top_k`` equal-weight slots.
- Exit: a held coin is sold (-1) ONLY once RSI recovers past ``rsi_exit`` (take
  the bounce). It is NOT sold on a regime break — exiting a held dip because
  price slipped under the MA sells into weakness and misses the bounce
  (validated: a regime-break exit costs ~20 points of return).
- Everything else holds (0).

Emits equal-weight ``buy_cash`` sizing so each slot targets ~1/top_k of the
portfolio, deploying 100% (no cash-reserve / price floor for crypto). Returns
1 = buy, 0 = hold, -1 = sell, plus ``_nexus_discovered`` for auto-picked pairs.
"""

from __future__ import annotations

import numpy as np
import talib

try:
    import sys
    import os
    _crypto_dir = os.path.dirname(os.path.abspath(__file__))
    _backend_dir = os.path.dirname(os.path.dirname(_crypto_dir))
    if _backend_dir not in sys.path:
        sys.path.insert(0, _backend_dir)
except Exception:
    pass

from strategies.crypto import core

DEFAULT_MAJORS = [
    "BTC/USD", "ETH/USD", "SOL/USD", "AVAX/USD",
    "LINK/USD", "LTC/USD", "DOT/USD", "BCH/USD",
]

_DISCOVERY_TIMEFRAME = "1Hour"
_series = core.series
_held_symbols = core.held_symbols


class Meanrev:
    """Regime-filtered RSI dip-buyer on crypto majors.

    Class name is ``Meanrev`` (not ``MeanRev``) to match the broker's
    module->class rule: ``"meanrev"`` -> ``"".join(w.capitalize() ...)`` ->
    ``"Meanrev"``. ``MeanRev`` is kept as an alias below for readability."""

    def __init__(self):
        self.rsi_period = 14
        self.rsi_buy = 35.0
        self.rsi_exit = 55.0
        self.regime_ma = 200
        self.top_k = 2

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

        rsi_p = max(2, int(settings.get("rsi_period", self.rsi_period)))
        rsi_buy = float(settings.get("rsi_buy", self.rsi_buy))
        rsi_exit = float(settings.get("rsi_exit", self.rsi_exit))
        regime_ma = max(2, int(settings.get("regime_ma", self.regime_ma)))
        top_k = max(1, int(settings.get("top_k", self.top_k)))
        min_bars = regime_ma + rsi_p + 2

        data = data or {}
        seed = [str(s).strip().upper() for s in (symbols or []) if str(s).strip()]
        if seed:
            candidates = seed
        else:
            band = str(settings.get("band", "medium"))
            disc_k = max(top_k + 3, int(settings.get("discovery_k", 10)))
            timeframe = str(settings.get("discovery_timeframe", _DISCOVERY_TIMEFRAME))
            candidates = core.discover_universe_cached(band, disc_k, settings, timeframe, strategy_cache) or DEFAULT_MAJORS

        seed_set = set(seed)
        discovered = [s for s in candidates if s not in seed_set]
        universe = [s for s in candidates if data.get(s)]

        result: dict = {}
        if discovered:
            result["_nexus_discovered"] = discovered
        # Exit held coins we can't see this tick (dropped out / empty window).
        core.exit_blind_held(result, portfolio_emulator, data, universe)
        if not universe:
            return core.apply_crypto_config(result, config, prices, portfolio_emulator)

        held = _held_symbols(portfolio_emulator, universe)

        # Per-symbol RSI + regime.
        rsi_by = {}
        regime_ok = {}
        # Only the LATEST indicator value is used each tick. Cap the window fed to
        # talib so every step is O(cap) instead of O(history) (SMA(regime_ma) is
        # exact from the last regime_ma bars; RSI has long converged after a big
        # warmup) — avoids an O(n²) blow-up on long backtests. Generous tail = parity.
        _cap = min_bars + 300
        for sym in universe:
            closes = _series(data.get(sym) or [], "c")
            if len(closes) < min_bars:
                rsi_by[sym] = None
                regime_ok[sym] = False
                continue
            if len(closes) > _cap:
                closes = closes[-_cap:]
            try:
                rsi = talib.RSI(closes, timeperiod=rsi_p)
                rsi_last = rsi[-1]
            except Exception:
                rsi_last = np.nan
            sma = talib.SMA(closes, timeperiod=regime_ma)
            sma_last = sma[-1]
            rsi_by[sym] = None if np.isnan(rsi_last) else float(rsi_last)
            regime_ok[sym] = (not np.isnan(sma_last)) and (closes[-1] > sma_last)

        # Exits: sell a held coin ONLY once RSI recovers past rsi_exit (bank the
        # bounce). The regime filter gates ENTRIES only — exiting a held dip just
        # because price slipped under the MA sells into weakness and misses the
        # mean-reversion bounce (validated: the regime-break exit costs ~20 pts).
        keep_held = set()
        for sym in universe:
            if sym not in held:
                continue
            rv = rsi_by.get(sym)
            if rv is not None and rv >= rsi_exit:
                result[sym] = -1
            else:
                result[sym] = 0
                keep_held.add(sym)

        # Entries: most-oversold regime-OK coins, up to the free slots.
        free = max(0, top_k - len(keep_held))
        buy_candidates = sorted(
            [s for s in universe
             if s not in held and regime_ok.get(s)
             and rsi_by.get(s) is not None and rsi_by[s] < rsi_buy],
            key=lambda s: rsi_by[s],
        )
        winners = buy_candidates[:free]
        for sym in universe:
            if sym in winners:
                result[sym] = 1
            elif sym not in result:
                result[sym] = 0

        _apply_equal_weight_sizing(result, prices, portfolio_emulator, top_k)
        return core.apply_crypto_config(result, config, prices, portfolio_emulator)


def _apply_equal_weight_sizing(result, prices, portfolio_emulator, top_k):
    """Emit equal-weight ``buy_cash`` (~1/top_k of portfolio) for each buy and a
    full ``sell_fraction`` for each exit, so the broker deploys 100% into the
    top_k dip slots. Never emits bare floats. No-op if there are no buys."""
    buys = [s for s, v in result.items()
            if isinstance(s, str) and not s.startswith("_") and v == 1]
    if not buys:
        return
    pv = 0.0
    if portfolio_emulator is not None:
        try:
            pv = float(portfolio_emulator.get_portfolio_value(prices) or 0.0)
        except Exception:
            pv = 0.0
    sizes = result.get("_nexus_position_sizes")
    if not isinstance(sizes, dict):
        sizes = {}
    per = round(pv / top_k, 2) if pv > 0 else 0.0
    for s in buys:
        if per > 0:
            sizes[s] = {"buy_cash": per, "asset_class": "crypto"}
    for s, v in result.items():
        if isinstance(s, str) and not s.startswith("_") and v == -1:
            sizes[s] = {"sell_fraction": 1.0, "asset_class": "crypto"}
    sizes["_cash_reserve_floor_pct"] = 0.0
    sizes["_buy_price_floor"] = 0.0
    result["_nexus_position_sizes"] = sizes


# Readable alias; the broker loads `Meanrev` (single-word capitalize of "meanrev").
MeanRev = Meanrev
