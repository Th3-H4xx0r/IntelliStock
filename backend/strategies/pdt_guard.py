# INTELLISTOCK_SCHEMA: {"strategy": "PdtGuard", "weight": 0.10, "execution_position": 30, "decision_phase": "pre", "execution_scope": "per_symbol", "conditions": {}, "config": {"block_same_day_exit": true}}
# INTELLISTOCK_DESCRIPTION: PDT guard for accounts under $25k. Blocks same-day exits to avoid pattern day-trader violations. Overrides weight to 1.0 when blocking.
# DIFFICULTY: 1
"""
PDT (Pattern Day Trade) Guard sub-strategy for the news pipeline.

For accounts under $25k, day trades (opening and closing a position in the
same calendar day) trigger PDT restrictions after 3 occurrences in 5 business
days. This strategy takes a conservative approach: block ANY same-day exit.

Since the trading system runs once per day at open, the main risk is:
- Buy at today's open, then another strategy votes sell on the same day.

This guard checks if the position was entered today and blocks the sell.

Returns:
  (0, 1.0, None, reason)  -- forces HOLD when blocking a same-day exit
  (0, None, None, None)   -- neutral otherwise
"""

from __future__ import annotations

import datetime

try:
    import sys
    import os
    broker_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if broker_dir not in sys.path:
        sys.path.insert(0, broker_dir)
    from intellistock_logger import intellistock_logger
    def _log(msg, color="white"):
        intellistock_logger.log(msg, color, service="PdtGuard")
except Exception:
    def _log(msg, color="white"):
        print(f"[PdtGuard] {msg}")


_CACHE_KEY = "pdt_guard"


def _to_date(dt) -> datetime.date | None:
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


def record_entry(strategy_cache: dict, symbol: str, entry_date):
    """Record that a position was opened on entry_date. Called by TradeAllocator on buy."""
    if _CACHE_KEY not in strategy_cache:
        strategy_cache[_CACHE_KEY] = {"entry_dates": {}}
    if "entry_dates" not in strategy_cache[_CACHE_KEY]:
        strategy_cache[_CACHE_KEY]["entry_dates"] = {}
    d = _to_date(entry_date)
    if d:
        strategy_cache[_CACHE_KEY]["entry_dates"][symbol] = d.isoformat()


def clear_entry(strategy_cache: dict, symbol: str):
    """Remove entry record after position is fully closed."""
    if _CACHE_KEY in strategy_cache and "entry_dates" in strategy_cache[_CACHE_KEY]:
        strategy_cache[_CACHE_KEY]["entry_dates"].pop(symbol, None)


class PdtGuard:
    """Blocks same-day exits to prevent PDT violations."""

    def run(self, symbol, price, current_time, config, conditions,
            data=None, portfolio_emulator=None, strategy_cache=None,
            time_increment=None):
        if strategy_cache is None:
            return (0, None, None, None)

        cfg = config or {}
        if not cfg.get("block_same_day_exit", True):
            return (0, None, None, None)

        cache = strategy_cache.get(_CACHE_KEY, {})
        entry_dates = cache.get("entry_dates", {})
        entry_iso = entry_dates.get(symbol)

        # Auto-detect entry date from trade history if not cached
        if not entry_iso and portfolio_emulator is not None:
            positions = portfolio_emulator.get_positions()
            if positions.get(symbol, 0) > 0:
                trades = portfolio_emulator.get_trade_history()
                buys = [t for t in trades if t.get("ticker") == symbol and t.get("action") == "buy"]
                if buys:
                    last_buy_ts = buys[-1].get("timestamp")
                    d = _to_date(last_buy_ts)
                    if d:
                        entry_iso = d.isoformat()
                        record_entry(strategy_cache, symbol, d)

        if not entry_iso:
            return (0, None, None, None)

        today = _to_date(current_time) or datetime.date.today()
        entry_date = _to_date(entry_iso)

        if entry_date == today:
            # We have a position opened today. Block any sell by voting HOLD
            # with weight_override=1.0, which forces the final decision to HOLD.
            _log(f"{symbol}: blocking same-day exit (entered today {today})", "yellow")
            return (0, 1.0, None, f"PDT guard: block same-day exit (entered {today})")

        return (0, None, None, None)
