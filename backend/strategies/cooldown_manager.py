# INTELLISTOCK_SCHEMA: {"strategy": "CooldownManager", "weight": 0.05, "execution_position": 50, "decision_phase": "pre", "execution_scope": "per_symbol", "conditions": {}, "config": {"cooldown_days": 3}}
# INTELLISTOCK_DESCRIPTION: Cooldown guard. Blocks re-entry into a symbol for N days after exit to avoid churn. Overrides weight to 1.0 when blocking.
# DIFFICULTY: 1
"""
Cooldown Manager sub-strategy for the news pipeline.

Prevents churn by blocking re-entry into a symbol for a configurable number
of days after the last exit. This avoids scenarios like:
  - Day 1: Buy on positive news
  - Day 2: Sell on risk stop
  - Day 3: Weak positive news triggers another buy into the same failing stock

Config:
  cooldown_days: number of calendar days to wait after a sell before allowing
                 a new buy on the same symbol (default 2)

When blocking, returns (0, 1.0, None, reason) to force HOLD.
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
        intellistock_logger.log(msg, color, service="CooldownManager")
except Exception:
    def _log(msg, color="white"):
        print(f"[CooldownManager] {msg}")


_CACHE_KEY = "cooldown_manager"


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


def record_exit(strategy_cache: dict, symbol: str, exit_date):
    """Record that a position was exited. Called by TradeAllocator or broker on sell."""
    if _CACHE_KEY not in strategy_cache:
        strategy_cache[_CACHE_KEY] = {"last_exit_dates": {}}
    if "last_exit_dates" not in strategy_cache[_CACHE_KEY]:
        strategy_cache[_CACHE_KEY]["last_exit_dates"] = {}
    d = _to_date(exit_date)
    if d:
        strategy_cache[_CACHE_KEY]["last_exit_dates"][symbol] = d.isoformat()


class CooldownManager:
    """Blocks re-entry for cooldown_days after an exit."""

    def run(self, symbol, price, current_time, config, conditions,
            data=None, portfolio_emulator=None, strategy_cache=None,
            time_increment=None):
        if strategy_cache is None:
            return (0, None, None, None)

        cfg = config or {}
        cooldown_days = int(cfg.get("cooldown_days", 3))

        cache = strategy_cache.get(_CACHE_KEY, {})
        exit_dates = cache.get("last_exit_dates", {})
        exit_iso = exit_dates.get(symbol)

        # Auto-detect last exit date from trade history if not cached
        if not exit_iso and portfolio_emulator is not None:
            positions = portfolio_emulator.get_positions()
            if positions.get(symbol, 0) <= 0:
                trades = portfolio_emulator.get_trade_history()
                sells = [t for t in trades if t.get("ticker") == symbol and t.get("action") == "sell"]
                if sells:
                    last_sell_ts = sells[-1].get("timestamp")
                    d = _to_date(last_sell_ts)
                    if d:
                        exit_iso = d.isoformat()
                        record_exit(strategy_cache, symbol, d)

        if not exit_iso:
            return (0, None, None, None)

        today = _to_date(current_time) or datetime.date.today()
        exit_date = _to_date(exit_iso)
        if exit_date is None:
            return (0, None, None, None)

        days_since_exit = (today - exit_date).days

        if days_since_exit < cooldown_days:
            remaining = cooldown_days - days_since_exit
            reason = f"Cooldown: {days_since_exit}d since exit, {remaining}d remaining"
            _log(f"{symbol}: {reason}", "yellow")
            return (0, 1.0, None, reason)

        return (0, None, None, None)
