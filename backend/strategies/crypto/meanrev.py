# INTELLISTOCK_SCHEMA: {"strategy": "MeanRev", "weight": 1.0, "execution_position": 0, "decision_phase": "pre", "execution_scope": "run_once", "conditions": {}, "config": {"band": "medium", "rsi_period": 14, "rsi_buy": 35, "rsi_exit": 55, "regime_ma": 200, "top_k": 2, "sizing": "vol", "atr_period": 14, "bear_gate_ma": 0}}
# INTELLISTOCK_DESCRIPTION: Regime-filtered RSI mean-reversion for crypto. Buys oversold majors (RSI below rsi_buy) ONLY while they hold above a long regime MA ("buy healthy dips, not falling knives"), holds up to top_k, and banks the bounce when RSI recovers past rsi_exit. Sits in cash most of the time — in backtests it stayed positive across bull, chop, and a -42% BTC drawdown while trend/momentum strategies were whipsawed. Sizes each dip by volatility (ATR%) so higher-bounce coins get more capital ("sizing":"vol", bounded 0.6-1.6x); "sizing":"equal" restores flat 1/top_k slots. Optional crash-bear gate ("bear_gate_ma", e.g. 1200 = 50 days of hourly bars; 0=off): while the equal-weight basket of the universe sits below its bear_gate_ma-bar MA, NEW entries are blocked (exits and holds unaffected) — faithfully validated to turn a 2022-class crash-bear from -19% into +11% while every milder bear stays positive, at the cost of part of the mild-bear scalp profit.
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
- Optional bear gate (``bear_gate_ma`` > 0, e.g. 1200 hourly bars = 50 days):
  while the equal-weight basket of the visible universe is below its
  ``bear_gate_ma``-bar MA, NEW entries are blocked; exits/holds unaffected.
  Rationale: the per-coin SMA(regime_ma) filter still admits dip-buys during
  bear-market rallies, which accumulate losses in a 2022-class crash. The
  basket gate suppresses exactly those. Faithfully verified (PortfolioEmulator,
  Binance.US fees, 2021-2026): 2022 bear -19% -> +11% and every mild bear stays
  positive (robust across 1200-1680-bar windows), at the cost of part of the
  mild-bear scalp profit. Fail-OPEN: with < max(600, bear_gate_ma//2) shared
  bars of history the gate stays off (live safety on short data windows).

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
        self.atr_period = 14
        self.sizing = "vol"
        self.bear_gate_ma = 0  # 0 = off; e.g. 1200 (50d of hourly bars) to enable

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
        atr_p = max(2, int(settings.get("atr_period", self.atr_period)))
        sizing = str(settings.get("sizing", self.sizing)).lower()
        try:
            bear_gate_ma = max(0, int(settings.get("bear_gate_ma", self.bear_gate_ma) or 0))
        except (TypeError, ValueError):
            bear_gate_ma = 0
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

        # Per-symbol RSI + regime (+ ATR% for volatility-scaled sizing).
        rsi_by = {}
        regime_ok = {}
        atr_pct_by = {}
        # Only the LATEST indicator value is used each tick. Cap the window fed to
        # talib so every step is O(cap) instead of O(history) (SMA(regime_ma) is
        # exact from the last regime_ma bars; RSI has long converged after a big
        # warmup) — avoids an O(n²) blow-up on long backtests. Generous tail = parity.
        _cap = min_bars + 300
        for sym in universe:
            closes = _series(data.get(sym) or [], "c")
            atr_pct_by[sym] = None
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
            # ATR% (latest) from the same capped window — sizing weight input.
            highs = _series(data.get(sym) or [], "h")
            lows = _series(data.get(sym) or [], "l")
            if len(highs) > _cap:
                highs = highs[-_cap:]
            if len(lows) > _cap:
                lows = lows[-_cap:]
            try:
                if len(highs) == len(closes) and len(lows) == len(closes):
                    atr = talib.ATR(highs, lows, closes, timeperiod=atr_p)
                    if not np.isnan(atr[-1]) and closes[-1] > 0:
                        atr_pct_by[sym] = float(atr[-1]) / float(closes[-1])
            except Exception:
                atr_pct_by[sym] = None

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
        # Bear gate: while the universe basket is below its long MA, block NEW
        # entries entirely (free=0). Exits above are already emitted; holds keep
        # holding. See module docstring for the validation numbers.
        free = max(0, top_k - len(keep_held))
        if free and bear_gate_ma > 0 and _bear_gate_blocked(data, universe, bear_gate_ma):
            free = 0
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

        _apply_equal_weight_sizing(result, prices, portfolio_emulator, top_k,
                                   atr_pct_by=atr_pct_by, sizing=sizing)
        return core.apply_crypto_config(result, config, prices, portfolio_emulator)


def _bear_gate_blocked(data, universe, gate_ma):
    """True when the equal-weight basket of ``universe`` closes below its
    ``gate_ma``-bar moving average (expanding-capped, no lookahead).

    Basket = mean over coins of close normalized at a shared trailing anchor
    (last ``gate_ma + 100`` bars). Coins shorter than max(600, gate_ma//2) bars
    are dropped; if none (or the shared window is still that short) the gate
    FAILS OPEN (returns False) so short live data windows never block trading.
    Never raises."""
    try:
        need = gate_ma + 100
        min_hist = max(600, gate_ma // 2)
        series = []
        for sym in universe:
            closes = _series(data.get(sym) or [], "c")
            if len(closes) >= min_hist and closes[-1] > 0:
                tail = closes[-min(len(closes), need):]
                if tail[0] > 0:
                    series.append(tail)
        if not series:
            return False
        shared = min(len(c) for c in series)
        if shared < min_hist:
            return False
        rows = [c[-shared:] / c[-shared] for c in series if c[-shared] > 0]
        if not rows:
            return False
        basket = np.mean(rows, axis=0)
        ma = float(np.mean(basket[-min(shared, gate_ma):]))
        last = float(basket[-1])
        if not (np.isfinite(last) and np.isfinite(ma)):
            return False
        return bool(last < ma)
    except Exception:
        return False


def _apply_equal_weight_sizing(result, prices, portfolio_emulator, top_k,
                               atr_pct_by=None, sizing="equal"):
    """Emit per-buy ``buy_cash`` and a full ``sell_fraction`` for each exit into
    ``result["_nexus_position_sizes"]``, so the broker deploys 100% into the
    top_k dip slots. Never emits bare floats. No-op if there are no buys.

    Default (``sizing="equal"`` or no ``atr_pct_by``): each buy targets an equal
    ``pv/top_k`` slot. This is the behavior connors.py relies on — do not change it.

    ``sizing="vol"`` with ``atr_pct_by`` {sym: ATR%}: each slot is scaled by the
    coin's volatility — ``buy_cash = pv/top_k * clamp(atr_pct/ref, 0.6, 1.6)`` where
    ``ref`` is the median ATR% over the coins being bought — so more capital flows to
    higher-bounce names (validated to win/tie equal-weight in every regime). Buys with
    a missing/invalid ATR% fall back to the flat ``pv/top_k`` slot."""
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

    # Volatility reference = median ATR% over the buys that carry a valid value.
    ref = None
    if sizing == "vol" and isinstance(atr_pct_by, dict):
        vals = sorted(v for s in buys
                      for v in [atr_pct_by.get(s)]
                      if isinstance(v, (int, float)) and v is not None and v > 0)
        if vals:
            m = len(vals)
            ref = vals[m // 2] if m % 2 else (vals[m // 2 - 1] + vals[m // 2]) / 2.0

    for s in buys:
        if per <= 0:
            continue
        cash = per
        if ref and ref > 0:
            av = atr_pct_by.get(s) if isinstance(atr_pct_by, dict) else None
            if isinstance(av, (int, float)) and av is not None and av > 0:
                w = av / ref
                w = 0.6 if w < 0.6 else (1.6 if w > 1.6 else w)
                cash = round(per * w, 2)
        sizes[s] = {"buy_cash": cash, "asset_class": "crypto"}
    for s, v in result.items():
        if isinstance(s, str) and not s.startswith("_") and v == -1:
            sizes[s] = {"sell_fraction": 1.0, "asset_class": "crypto"}
    sizes["_cash_reserve_floor_pct"] = 0.0
    sizes["_buy_price_floor"] = 0.0
    result["_nexus_position_sizes"] = sizes


# Readable alias; the broker loads `Meanrev` (single-word capitalize of "meanrev").
MeanRev = Meanrev
