# INTELLISTOCK_SCHEMA: {"strategy": "RiskManager", "weight": 0.20, "execution_position": 20, "decision_phase": "pre", "execution_scope": "per_symbol", "conditions": {}, "config": {"stop_atr_mult": 2.0, "take_profit_atr_mult": 3.0, "max_hold_days": 7, "fallback_stop_pct": 0.08, "trail_atr_mult": 1.5, "trail_activation_atr": 0.5}}
# INTELLISTOCK_DESCRIPTION: Risk manager for news pipeline. Stop-loss (ATR or %), take-profit, and max-hold-days exit. Overrides weight to 1.0 on exit signals. Tracks position state in strategy_cache.
# DIFFICULTY: 3
"""
Risk Manager sub-strategy for the news pipeline.

Protects downside and enforces time-based exits. Evaluated per-symbol before
other guards so it can force sells immediately.

Exit conditions (checked in order):
1. Gap stop: today's price <= stop_price  ->  force sell
2. Take profit: today's price >= take_profit_price  ->  force sell
3. Max hold: days held >= max_hold_days  ->  force sell

Position state is auto-initialized from portfolio_emulator trade history when
a held position has no cached state yet (e.g. after broker restart or first
run). The init_position_state() function can also be called externally.

Returns (score, weight_override, size_hint, reason).
On exit signals: (-1, 1.0, {"sell_fraction": 1.0}, reason)  -- weight 1.0 forces the sell.
Otherwise: (0, None, None, reason)  -- neutral hold.
"""

from __future__ import annotations

import datetime
from typing import Any

try:
    import sys
    import os
    broker_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if broker_dir not in sys.path:
        sys.path.insert(0, broker_dir)
    from intellistock_logger import intellistock_logger
    def _log(msg, color="white"):
        intellistock_logger.log(msg, color, service="RiskManager")
except Exception:
    def _log(msg, color="white"):
        print(f"[RiskManager] {msg}")


_CACHE_KEY = "risk_manager"


def _ensure_cache(strategy_cache: dict) -> dict:
    """Return (and lazily create) the risk_manager sub-dict in strategy_cache."""
    if _CACHE_KEY not in strategy_cache:
        strategy_cache[_CACHE_KEY] = {"position_state": {}}
    if "position_state" not in strategy_cache[_CACHE_KEY]:
        strategy_cache[_CACHE_KEY]["position_state"] = {}
    return strategy_cache[_CACHE_KEY]


def _to_date(dt) -> datetime.date | None:
    """Convert datetime / date / ISO string to date."""
    if dt is None:
        return None
    if isinstance(dt, datetime.datetime):
        return dt.date()
    if isinstance(dt, datetime.date):
        return dt
    try:
        return datetime.datetime.fromisoformat(str(dt).replace("Z", "+00:00")).date()
    except Exception:
        return None


def _compute_atr(bars: list[dict], period: int = 14) -> float | None:
    """Compute ATR via TA-Lib from bar dicts with keys h, l, c."""
    if not bars or len(bars) < period + 1:
        return None
    import numpy as np
    import talib
    high = np.array([float(b.get("h") or b.get("c") or 0) for b in bars], dtype=np.float64)
    low = np.array([float(b.get("l") or b.get("c") or 0) for b in bars], dtype=np.float64)
    close = np.array([float(b.get("c") or 0) for b in bars], dtype=np.float64)
    result = talib.ATR(high, low, close, timeperiod=period)
    val = result[-1]
    return float(val) if not np.isnan(val) else None


def init_position_state(strategy_cache: dict, symbol: str, entry_price: float,
                        entry_date, config: dict, bars: list[dict] | None = None):
    """
    Called externally (e.g. by TradeAllocator) when a buy is executed.
    Sets stop_price, take_profit_price, and max_hold exit date.
    """
    cache = _ensure_cache(strategy_cache)
    cfg = config or {}
    stop_atr_mult = float(cfg.get("stop_atr_mult", 2.0))
    take_profit_atr_mult = float(cfg.get("take_profit_atr_mult", 3.0))
    max_hold_days = int(cfg.get("max_hold_days", 7))
    fallback_stop_pct = float(cfg.get("fallback_stop_pct", 0.08))

    # Compute ATR-based stop if bars available
    atr = _compute_atr(bars) if bars else None
    if atr and atr > 0:
        stop_price = entry_price - stop_atr_mult * atr
        take_profit_price = entry_price + take_profit_atr_mult * atr
    else:
        # Fallback: percentage-based
        stop_price = entry_price * (1.0 - fallback_stop_pct)
        take_profit_price = entry_price * (1.0 + fallback_stop_pct * (take_profit_atr_mult / stop_atr_mult))

    edate = _to_date(entry_date) or datetime.date.today()

    cache["position_state"][symbol] = {
        "entry_price": entry_price,
        "entry_date": edate.isoformat(),
        "stop_price": stop_price,
        "take_profit_price": take_profit_price,
        "max_hold_days": max_hold_days,
        "planned_exit_date": (edate + datetime.timedelta(days=max_hold_days)).isoformat(),
        "highest_close_since_entry": entry_price,
    }
    _log(f"{symbol}: position state initialized — entry={entry_price:.2f}, "
         f"stop={stop_price:.2f}, tp={take_profit_price:.2f}, "
         f"max_hold={max_hold_days}d", "cyan")


def clear_position_state(strategy_cache: dict, symbol: str):
    """Remove position state after a sell is executed."""
    cache = _ensure_cache(strategy_cache)
    cache["position_state"].pop(symbol, None)


class RiskManager:
    """Per-symbol risk management. Forces exits via weight_override=1.0."""

    def run(self, symbol, price, current_time, config, conditions,
            data=None, portfolio_emulator=None, strategy_cache=None,
            time_increment=None):
        if strategy_cache is None:
            return (0, None, None, "No cache")

        cache = _ensure_cache(strategy_cache)
        state = cache["position_state"].get(symbol)

        # Check if we actually hold the position
        has_position = False
        if portfolio_emulator is not None:
            positions = portfolio_emulator.get_positions()
            has_position = positions.get(symbol, 0) > 0
            if state is not None and not has_position:
                # Position was already closed; clean up state
                clear_position_state(strategy_cache, symbol)
                return (0, None, None, None)

        # If no cached state but we hold a position, auto-init from trade history
        if state is None and has_position and portfolio_emulator is not None:
            state = self._auto_init_from_trades(
                symbol, portfolio_emulator, strategy_cache, config, data)

        # If still no state, stay neutral
        if state is None:
            return (0, None, None, None)

        cfg = config or {}
        current_price = float(price) if price else 0
        if current_price <= 0:
            return (0, None, None, "No price")

        stop_price = float(state.get("stop_price", 0))
        take_profit_price = float(state.get("take_profit_price", 0))
        entry_price = float(state.get("entry_price", 0))
        max_hold_days = int(state.get("max_hold_days", 5))

        today = _to_date(current_time) or datetime.date.today()
        entry_date = _to_date(state.get("entry_date"))
        days_held = (today - entry_date).days if entry_date else 0

        # Update highest close
        highest = float(state.get("highest_close_since_entry", entry_price))
        if current_price > highest:
            highest = current_price
            state["highest_close_since_entry"] = current_price

        # Trailing stop: ratchet stop up after price gains trail_activation_atr × ATR
        trail_atr_mult = float(cfg.get("trail_atr_mult", 1.5))
        trail_activation_atr = float(cfg.get("trail_activation_atr", 1.0))
        if trail_atr_mult > 0 and data and symbol in data:
            atr_val = _compute_atr(data[symbol])
            if atr_val and atr_val > 0:
                activation_price = entry_price + trail_activation_atr * atr_val
                if highest >= activation_price:
                    trailing_stop = highest - trail_atr_mult * atr_val
                    if trailing_stop > stop_price:
                        state["stop_price"] = trailing_stop
                        stop_price = trailing_stop

        # 1. Stop loss
        if stop_price > 0 and current_price <= stop_price:
            pct_loss = ((current_price - entry_price) / entry_price * 100) if entry_price else 0
            reason = f"Stop hit: price {current_price:.2f} <= stop {stop_price:.2f} ({pct_loss:+.1f}%)"
            _log(f"{symbol}: {reason}", "red")
            return (-1, 1.0, {"sell_fraction": 1.0}, reason)

        # 2. Take profit
        if take_profit_price > 0 and current_price >= take_profit_price:
            pct_gain = ((current_price - entry_price) / entry_price * 100) if entry_price else 0
            reason = f"Take profit: price {current_price:.2f} >= target {take_profit_price:.2f} ({pct_gain:+.1f}%)"
            _log(f"{symbol}: {reason}", "green")
            return (-1, 1.0, {"sell_fraction": 1.0}, reason)

        # 3. Max hold days (smart: only force-exit losing positions; winners extend to 2×)
        if days_held >= max_hold_days:
            pct = ((current_price - entry_price) / entry_price * 100) if entry_price else 0
            if pct <= 0:
                # Losing at max hold — cut losses
                reason = f"Max hold reached: {days_held}d >= {max_hold_days}d ({pct:+.1f}%)"
                _log(f"{symbol}: {reason}", "yellow")
                return (-1, 1.0, {"sell_fraction": 1.0}, reason)
            elif days_held >= max_hold_days * 2:
                # Profitable but hit absolute cap (2× max hold)
                reason = f"Extended max hold reached: {days_held}d >= {max_hold_days * 2}d ({pct:+.1f}%)"
                _log(f"{symbol}: {reason}", "yellow")
                return (-1, 1.0, {"sell_fraction": 1.0}, reason)
            # else: profitable and within extension window — keep holding, let trailing stop manage

        # Hold
        pct = ((current_price - entry_price) / entry_price * 100) if entry_price else 0
        return (0, None, None, f"Holding {days_held}d, P/L {pct:+.1f}%, stop={stop_price:.2f}")

    def _auto_init_from_trades(self, symbol, portfolio_emulator, strategy_cache, config, data):
        """Auto-initialize position state from trade history when cache is empty."""
        trades = portfolio_emulator.get_trade_history()
        # Find the most recent buy for this symbol that hasn't been fully sold
        buy_trades = [t for t in trades if t.get("ticker") == symbol and t.get("action") == "buy"]
        if not buy_trades:
            return None
        last_buy = buy_trades[-1]
        entry_price = float(last_buy.get("price", 0))
        entry_date = last_buy.get("timestamp")
        if entry_price <= 0:
            return None
        bars = (data or {}).get(symbol) if data else None
        init_position_state(strategy_cache, symbol, entry_price, entry_date, config or {}, bars)
        _log(f"{symbol}: auto-initialized position state from trade history "
             f"(entry={entry_price:.2f})", "cyan")
        cache = _ensure_cache(strategy_cache)
        return cache["position_state"].get(symbol)
