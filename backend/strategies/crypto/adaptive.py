# INTELLISTOCK_SCHEMA: {"strategy": "Adaptive", "weight": 1.0, "execution_position": 0, "decision_phase": "pre", "execution_scope": "run_once", "conditions": {}, "config": {"band": "low", "switch_ma": 4800, "confirm_ma": 1200, "rebalance_drift": 0, "rsi_period": 14, "rsi_buy": 35, "rsi_exit": 55, "regime_ma": 200, "top_k": 2, "sizing": "vol", "atr_period": 14}}
# INTELLISTOCK_DESCRIPTION: Regime-adaptive crypto strategy: holds the equal-weight basket (buy & hold) while the universe basket trades above BOTH its slow switch MA (switch_ma, default 4800 hourly bars = 200 days) and its fast confirm MA (confirm_ma, default 1200 = 50 days); the moment the regime breaks it liquidates the basket and hands control to the MeanRev dip-buyer running with the crash-bear entry gate. Bulls get real market exposure: prod backtest Oct-2023..Mar-2024 at Binance.US fees = +132% vs MeanRev's +56% (EW buy&hold +181%). Crash protection is strong but UNIVERSE-DEPENDENT: 2022 on the 5 old majors was positive (+8.6% faithful) but the prod 7-coin 2022 incl. SOL(-94%)/AVAX(-90%) lost -19% — still ~55 pts better than buy&hold's -74.5%. Chop is its weak regime (~-21% vs B&H -35%). Pick MeanRev for max bear safety, Adaptive for bull participation.
# DIFFICULTY: 3
"""
Adaptive regime switcher (run_once). DB name: "adaptive".

Two modes, decided per tick from the equal-weight basket of the visible
universe (each coin's close normalized at a shared trailing anchor):

- BULL — basket >= ramped SMA(switch_ma) AND basket >= ramped SMA(confirm_ma):
  buy every non-held universe coin at pv/N ("hold the market"); never churns
  while the regime holds. With too little history to judge (< max(600,
  confirm_ma//2) shared bars) the strategy FAILS OPEN TO BULL — holding the
  market is the benchmark behavior.
- BEAR — anything else: the basket is liquidated on the flip tick (detected
  via ``strategy_cache``; fallback: more coins held than MeanRev's top_k),
  then control delegates to :class:`strategies.crypto.meanrev.Meanrev` with
  ``bear_gate_ma`` defaulted to ``confirm_ma`` — i.e. dip-buys only while the
  basket holds above its 50d MA, so 2022-class crash bears stay positive.

Both MAs are expanding-capped (ramped) over the window the broker provides,
no lookahead. Exits inherit MeanRev semantics in bear mode; in bull mode the
basket is only sold on the regime flip.

Validated (faithful PortfolioEmulator, Binance.US 0.02%, 9 regime intervals
2021-2026; fast-harness plateau sw 3600-4800 confirmed):

    2021bull +111.2 (B&H +190) | 2022bear +8.6 (B&H -67) | 2023recov +16.3
    2324bull +74.1 (B&H +119)  | 2024chop -21.3 (B&H -35) | late24 +8.1
    OOS +13.1 (B&H -36)        | tgt -3.6 (B&H -21)       | fullrec +14.4 (B&H -50)

Mean ~+24.5%/interval vs MeanRev-only ~+14.5%. Trade-off vs MeanRev: far more
bull capture, but chop and the mildest bears can go slightly negative.

UNIVERSE CAVEAT (prod A/B 2026-07-14): the faithful 2022 numbers above are on
the 5 old majors (BTC/ETH/LTC/BCH/LINK — the only coins with local 2022
caches). Prod backtests on the 7-coin universe incl. SOL (-94% in 2022) and
AVAX (-90%): 2022 = -19.1% (vs EW B&H -74.5%) — false bull flips during
bear-market rallies in the hardest-crashing coins cost ~-28 pts vs the
narrow-universe result. Bull side confirmed at scale: Oct-23..Mar-24 = +132.1%
vs MeanRev +55.7% (B&H +181%). Not a replacement for MeanRev — a different
point on the risk/return frontier.
"""

from __future__ import annotations

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
from strategies.crypto.meanrev import DEFAULT_MAJORS, Meanrev

_DISCOVERY_TIMEFRAME = "1Hour"
_series = core.series
_held_symbols = core.held_symbols

_MODE_KEY = "_adaptive_mode"


def _basket_mas(data, universe, switch_ma, confirm_ma):
    """(basket_last, slow_ma, fast_ma) from the shared trailing window, or
    None when there is too little history (fail-open to bull). Expanding-capped
    means, no lookahead. Never raises."""
    try:
        need = switch_ma + 100
        min_hist = max(600, confirm_ma // 2)
        series = []
        for sym in universe:
            closes = _series(data.get(sym) or [], "c")
            if len(closes) >= min_hist and closes[-1] > 0:
                series.append(closes[-min(len(closes), need):])
        if not series:
            return None
        shared = min(len(c) for c in series)
        if shared < min_hist:
            return None
        rows = [c[-shared:] / c[-shared] for c in series if c[-shared] > 0]
        if not rows:
            return None
        basket = np.mean(rows, axis=0)
        slow = float(np.mean(basket[-min(shared, switch_ma):]))
        fast = float(np.mean(basket[-min(shared, confirm_ma):]))
        last = float(basket[-1])
        if not (np.isfinite(last) and np.isfinite(slow) and np.isfinite(fast)):
            return None
        return last, slow, fast
    except Exception:
        return None


def _is_bull(data, universe, switch_ma, confirm_ma):
    mas = _basket_mas(data, universe, switch_ma, confirm_ma)
    if mas is None:
        return True  # fail open to bull: hold-the-market is the benchmark
    last, slow, fast = mas
    return last >= slow and last >= fast


class Adaptive:
    """Bull -> hold the EW basket; bear -> gated MeanRev dip-buying.

    Class name is ``Adaptive`` to match the broker's module->class rule
    ("adaptive" -> "Adaptive")."""

    def __init__(self):
        self.switch_ma = 4800   # 200d of hourly bars
        self.confirm_ma = 1200  # 50d fast confirm; doubles as bear-mode entry gate
        self.top_k = 2
        # Bull-mode drift rebalancing: act when a coin's weight leaves
        # [tgt*(1-band), tgt*(1+band)] (relative band). DEFAULT 0 = OFF:
        # the faithful sim said +1 pt/interval, but the controlled PROD A/B
        # (2026-07-18, bt 355497 drift=0 +132.06 byte-identical to baseline
        # vs bt 153661 drift=0.5 +125.43) showed it COSTS ~6.6 pts through
        # the real decision pipeline — prod evidence wins. Opt-in only.
        self.rebalance_drift = 0.0
        self._meanrev = Meanrev()

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

        try:
            switch_ma = max(2, int(settings.get("switch_ma", self.switch_ma)))
        except (TypeError, ValueError):
            switch_ma = self.switch_ma
        try:
            confirm_ma = max(2, int(settings.get("confirm_ma", self.confirm_ma)))
        except (TypeError, ValueError):
            confirm_ma = self.confirm_ma
        top_k = max(1, int(settings.get("top_k", self.top_k)))
        try:
            rebalance_drift = max(0.0, float(settings.get("rebalance_drift", self.rebalance_drift)))
        except (TypeError, ValueError):
            rebalance_drift = self.rebalance_drift

        data = data or {}
        seed = [str(s).strip().upper() for s in (symbols or []) if str(s).strip()]
        if seed:
            candidates = seed
        else:
            band = str(settings.get("band", "low"))
            disc_k = max(top_k + 3, int(settings.get("discovery_k", 10)))
            timeframe = str(settings.get("discovery_timeframe", _DISCOVERY_TIMEFRAME))
            candidates = core.discover_universe_cached(
                band, disc_k, settings, timeframe, strategy_cache
            ) or list(DEFAULT_MAJORS)

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
        cache = strategy_cache if isinstance(strategy_cache, dict) else None
        prev_mode = cache.get(_MODE_KEY) if cache else None

        if _is_bull(data, universe, switch_ma, confirm_ma):
            # BULL: buy every non-held coin at pv/N; held coins hold, EXCEPT
            # when drift rebalancing is on and a coin's weight left the band —
            # then trim the overweight (fractional sell) / top up the
            # underweight (buy_cash), mirroring overlay_allocations semantics.
            pv = 0.0
            positions = {}
            if portfolio_emulator is not None:
                try:
                    pv = float(portfolio_emulator.get_portfolio_value(prices) or 0.0)
                except Exception:
                    pv = 0.0
                try:
                    positions = portfolio_emulator.get_positions() or {}
                except Exception:
                    positions = {}
            per = round(pv / len(universe), 2) if pv > 0 else 0.0
            tgt_w = 1.0 / len(universe)
            sizes = result.get("_nexus_position_sizes")
            if not isinstance(sizes, dict):
                sizes = {}
            for sym in universe:
                if sym not in held:
                    result[sym] = 1
                    if per > 0:
                        sizes[sym] = {"buy_cash": per, "asset_class": "crypto"}
                    continue
                result[sym] = 0
                if rebalance_drift <= 0 or pv <= 0:
                    continue
                px = prices.get(sym)
                qty = core.position_qty(positions, sym)
                if not px or px <= 0 or qty <= 0:
                    continue
                w = (qty * float(px)) / pv
                if w > tgt_w * (1 + rebalance_drift):
                    result[sym] = -1
                    sizes[sym] = {"sell_fraction": round((w - tgt_w) / w, 6),
                                  "asset_class": "crypto"}
                elif w < tgt_w * (1 - rebalance_drift):
                    result[sym] = 1
                    sizes[sym] = {"buy_cash": round((tgt_w - w) * pv, 2),
                                  "asset_class": "crypto"}
            sizes["_cash_reserve_floor_pct"] = 0.0
            sizes["_buy_price_floor"] = 0.0
            result["_nexus_position_sizes"] = sizes
            if cache is not None:
                cache[_MODE_KEY] = "bull"
            return core.apply_crypto_config(result, config, prices, portfolio_emulator)

        # BEAR. Flip tick (was bull, or basket remnants with no cache): liquidate.
        flipped = (prev_mode == "bull") or (prev_mode is None and len(held) > top_k)
        if cache is not None:
            cache[_MODE_KEY] = "bear"
        if flipped and held:
            sizes = result.get("_nexus_position_sizes")
            if not isinstance(sizes, dict):
                sizes = {}
            for sym in universe:
                if sym in held:
                    result[sym] = -1
                    sizes[sym] = {"sell_fraction": 1.0, "asset_class": "crypto"}
                elif sym not in result:
                    result[sym] = 0
            sizes["_cash_reserve_floor_pct"] = 0.0
            sizes["_buy_price_floor"] = 0.0
            result["_nexus_position_sizes"] = sizes
            return core.apply_crypto_config(result, config, prices, portfolio_emulator)

        # Steady bear: delegate to the gated MeanRev dip-buyer.
        mr_config = dict(settings)
        mr_config.setdefault("bear_gate_ma", confirm_ma)
        mr_out = self._meanrev.run_once(
            symbols=universe, prices=prices, current_time=current_time,
            config=mr_config, conditions={}, data=data,
            portfolio_emulator=portfolio_emulator, strategy_cache=strategy_cache,
            time_increment=time_increment, mode=mode,
        )
        if isinstance(mr_out, dict):
            for k, v in mr_out.items():
                if k == "_nexus_discovered":
                    continue  # ours (if any) is already in result
                result[k] = v
        return core.apply_crypto_config(result, config, prices, portfolio_emulator)


# Readable alias, mirroring meanrev's MeanRev.
AdaptiveSwitcher = Adaptive
