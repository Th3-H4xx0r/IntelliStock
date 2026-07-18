# INTELLISTOCK_SCHEMA: {"strategy": "Connors", "weight": 1.0, "execution_position": 0, "decision_phase": "pre", "execution_scope": "run_once", "conditions": {}, "config": {"band": "low", "rsi_period": 3, "rsi_buy": 5, "exit_ma": 5, "regime_ma": 200, "top_k": 2}}
# INTELLISTOCK_DESCRIPTION: Connors-style fast RSI mean-reversion for crypto. Buys deeply oversold majors (short RSI below rsi_buy) while above a long regime MA, and exits fast when price mean-reverts back above a short MA. Higher turnover, quicker in/out than MeanRev — in backtests it was modestly but robustly positive (+6% over 400d vs BTC -42%), the faster cousin of the top strategy.
# DIFFICULTY: 3
"""
Connors fast mean-reversion crypto strategy (run_once). DB name: "connors".

The classic Connors RSI-2/3 dip-buy, long-only and regime-gated:
- Regime (entry only): only ENTER while close > SMA(regime_ma).
- Entry: buy (1) the most-oversold coins where a SHORT RSI (rsi_period, e.g. 3)
  is below ``rsi_buy`` (e.g. 5), filling up to ``top_k`` equal-weight slots.
- Exit: sell (-1) once price mean-reverts back above SMA(exit_ma) (e.g. 5) — a
  fast "mean touched" exit. NOT sold on a regime break (that sells into weakness
  and misses the bounce; same lesson as MeanRev).

Faster and higher-turnover than MeanRev; lower but still-positive backtest
returns. Emits equal-weight ``buy_cash`` sizing. Returns 1/0/-1.
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
from strategies.crypto.meanrev import _apply_equal_weight_sizing

DEFAULT_MAJORS = [
    "BTC/USD", "ETH/USD", "SOL/USD", "AVAX/USD",
    "LINK/USD", "LTC/USD", "DOT/USD", "BCH/USD",
]

_DISCOVERY_TIMEFRAME = "1Hour"
_series = core.series
_held_symbols = core.held_symbols


class Connors:
    """Connors RSI-2/3 fast dip-buyer with a short-MA mean-touch exit."""

    def __init__(self):
        self.rsi_period = 3
        self.rsi_buy = 5.0
        self.exit_ma = 5
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
        exit_ma = max(2, int(settings.get("exit_ma", self.exit_ma)))
        regime_ma = max(2, int(settings.get("regime_ma", self.regime_ma)))
        top_k = max(1, int(settings.get("top_k", self.top_k)))
        min_bars = regime_ma + rsi_p + 2

        data = data or {}
        seed = [str(s).strip().upper() for s in (symbols or []) if str(s).strip()]
        if seed:
            candidates = seed
        else:
            band = str(settings.get("band", "low"))
            disc_k = max(top_k + 3, int(settings.get("discovery_k", 10)))
            timeframe = str(settings.get("discovery_timeframe", _DISCOVERY_TIMEFRAME))
            candidates = core.discover_universe_cached(band, disc_k, settings, timeframe, strategy_cache) or DEFAULT_MAJORS

        seed_set = set(seed)
        discovered = [s for s in candidates if s not in seed_set]
        universe = [s for s in candidates if data.get(s)]

        result: dict = {}
        if discovered:
            result["_nexus_discovered"] = discovered
        core.exit_blind_held(result, portfolio_emulator, data, universe)
        if not universe:
            return core.apply_crypto_config(result, config, prices, portfolio_emulator)

        held = _held_symbols(portfolio_emulator, universe)

        rsi_by = {}
        regime_ok = {}
        above_exit_ma = {}
        # Cap the talib window: only the latest value is used, so O(cap)/step not
        # O(history) (SMA exact from its last N bars; RSI converged after warmup).
        _cap = min_bars + 300
        for sym in universe:
            closes = _series(data.get(sym) or [], "c")
            if len(closes) < min_bars:
                rsi_by[sym] = None
                regime_ok[sym] = False
                above_exit_ma[sym] = False
                continue
            if len(closes) > _cap:
                closes = closes[-_cap:]
            try:
                rsi = talib.RSI(closes, timeperiod=rsi_p)
                rsi_last = rsi[-1]
            except Exception:
                rsi_last = np.nan
            sma_reg = talib.SMA(closes, timeperiod=regime_ma)[-1]
            sma_exit = talib.SMA(closes, timeperiod=exit_ma)[-1]
            rsi_by[sym] = None if np.isnan(rsi_last) else float(rsi_last)
            regime_ok[sym] = (not np.isnan(sma_reg)) and (closes[-1] > sma_reg)
            above_exit_ma[sym] = (not np.isnan(sma_exit)) and (closes[-1] > sma_exit)

        # Exits: held coin whose price mean-reverted back above SMA(exit_ma).
        keep_held = set()
        for sym in universe:
            if sym not in held:
                continue
            if above_exit_ma.get(sym):
                result[sym] = -1
            else:
                result[sym] = 0
                keep_held.add(sym)

        # Entries: most-oversold (lowest short-RSI) regime-OK coins, up to free slots.
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
