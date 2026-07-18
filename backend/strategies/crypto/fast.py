# INTELLISTOCK_SCHEMA: {"strategy": "Fast", "weight": 1.0, "execution_position": 0, "decision_phase": "pre", "execution_scope": "run_once", "conditions": {}, "config": {"band": "high", "entry_window": 20, "exit_window": 10, "trend_ma": 50}}
# INTELLISTOCK_DESCRIPTION: Band 1 Donchian-channel breakout trend-follower. Buys an N-bar-high breakout only while price is above a long trend MA (regime filter that dodges counter-trend chop), rides the trend, and exits on a faster M-bar-low breakdown (M<N). Turtle-style: let winners run, cut losers.
# DIFFICULTY: 3
"""
Fast tactical crypto strategy (run_once). DB name: "fast" (file fast.py, class Fast).

Donchian-channel breakout trend-follower (Turtle-style). Research is consistent
that raw MA-cross / breakout systems bleed fees to whipsaw in chop; the fix that
matters most is a REGIME FILTER, not tuning periods. So:
- ENTRY (flat): close breaks above the prior ``entry_window``-bar high AND price
  is above the ``trend_ma`` long-term average (up-regime) -> buy (1).
- EXIT (held): close breaks below the prior ``exit_window``-bar low (a faster
  trailing channel, ``exit_window`` < ``entry_window``) -> sell (-1); otherwise
  hold (0) and let the winner run.
- Everything else -> hold (0).
Returns 1 = buy, 0 = hold, -1 = sell, plus ``_nexus_discovered`` for auto-picked
pairs. Per-symbol sizing is left to the broker's default sizing.
"""

from __future__ import annotations

from typing import Mapping, Optional

import numpy as np

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

# Shared helpers (single source of truth in core).
_series = core.series
_held_symbols = core.held_symbols


class Fast:
    """Donchian-channel breakout trend-follower on crypto majors."""

    def __init__(self):
        self.entry_window = 20   # breakout entry lookback (prior N-bar high)
        self.exit_window = 10    # faster breakdown exit (prior M-bar low, M<N)
        self.trend_ma = 50       # regime filter: only go long above this SMA

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

        entry_w = max(2, int(settings.get("entry_window", self.entry_window)))
        exit_w = max(2, int(settings.get("exit_window", self.exit_window)))
        trend_ma = max(entry_w, int(settings.get("trend_ma", self.trend_ma)))
        min_bars = trend_ma + 2

        data = data or {}
        seed = [str(s).strip().upper() for s in (symbols or []) if str(s).strip()]
        if seed:
            candidates = seed
        else:
            band = str(settings.get("band", "high"))
            disc_k = max(1, int(settings.get("discovery_k", 10)))
            timeframe = str(settings.get("discovery_timeframe", _DISCOVERY_TIMEFRAME))
            candidates = core.discover_universe_cached(band, disc_k, settings, timeframe, strategy_cache) or DEFAULT_MAJORS

        seed_set = set(seed)
        discovered = [s for s in candidates if s not in seed_set]
        universe = [s for s in candidates if data.get(s)]

        result: dict = {}
        if discovered:
            result["_nexus_discovered"] = discovered
        # Exit held coins we can't see this tick (crypto no-sells bug fix).
        core.exit_blind_held(result, portfolio_emulator, data, universe)
        if not universe:
            return core.apply_crypto_config(result, config, prices, portfolio_emulator)

        held = _held_symbols(portfolio_emulator, universe)
        for sym in universe:
            bars = data.get(sym) or []
            closes = _series(bars, "c")
            highs = _series(bars, "h")
            lows = _series(bars, "l")
            if closes.size < min_bars:
                result[sym] = -1 if sym in held else 0
                continue
            close_now = float(closes[-1])
            trend = float(np.mean(closes[-trend_ma:]))
            up_regime = close_now > trend
            # Prior-bar Donchian channels (exclude the current, still-forming bar).
            donchian_hi = float(np.max(highs[-(entry_w + 1):-1]))
            donchian_lo = float(np.min(lows[-(exit_w + 1):-1]))
            if sym in held:
                # Trailing channel exit — leave on a break of the exit low; else
                # hold and let the winner run.
                result[sym] = -1 if close_now < donchian_lo else 0
            else:
                # Enter only on a breakout WHILE the long-term regime is up.
                result[sym] = 1 if (up_regime and close_now > donchian_hi) else 0

        return core.apply_crypto_config(result, config, prices, portfolio_emulator)
