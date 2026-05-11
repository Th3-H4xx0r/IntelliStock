# INTELLISTOCK_SCHEMA: {"strategy": "ExposureLimits", "weight": 0.10, "execution_position": 40, "decision_phase": "pre", "execution_scope": "per_symbol", "conditions": {}, "config": {"MAX_POSITION_PCT": 0.20, "MAX_CONCURRENT_POSITIONS": 3, "CASH_BUFFER_PCT": 0.20}}
# INTELLISTOCK_DESCRIPTION: Portfolio exposure guard. Blocks buys when max positions reached or cash buffer violated. Overrides weight to 1.0 when blocking.
# DIFFICULTY: 2
"""
Exposure Limits sub-strategy for the news pipeline.

Portfolio-level constraints to prevent over-allocation:
1. Max concurrent positions — block buys if already holding MAX_CONCURRENT_POSITIONS
2. Cash buffer — block buys if remaining cash would fall below CASH_BUFFER_PCT of equity
3. Max single position — block if a single position would exceed MAX_POSITION_PCT of equity

Only blocks BUY decisions; never interferes with sells or holds.
When blocking, returns (0, 1.0, None, reason) to force HOLD.

Config:
  MAX_POSITION_PCT: max fraction of equity for a single position (default 0.20)
  MAX_CONCURRENT_POSITIONS: max number of simultaneous positions (default 3)
  CASH_BUFFER_PCT: minimum cash as fraction of equity to keep (default 0.20)
"""

from __future__ import annotations

try:
    import sys
    import os
    broker_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if broker_dir not in sys.path:
        sys.path.insert(0, broker_dir)
    from intellistock_logger import intellistock_logger
    def _log(msg, color="white"):
        intellistock_logger.log(msg, color, service="ExposureLimits")
except Exception:
    def _log(msg, color="white"):
        print(f"[ExposureLimits] {msg}")


class ExposureLimits:
    """Blocks buys that would violate portfolio exposure constraints."""

    def run(self, symbol, price, current_time, config, conditions,
            data=None, portfolio_emulator=None, strategy_cache=None,
            time_increment=None):
        # This strategy only blocks buys. It votes HOLD (0) by default.
        # The blocking is done by returning weight_override=1.0 which forces
        # the final aggregated decision toward HOLD.
        #
        # We can only evaluate constraints if we have portfolio_emulator.
        if portfolio_emulator is None:
            return (0, None, None, None)

        cfg = config or {}
        max_positions = int(cfg.get("MAX_CONCURRENT_POSITIONS", 3))
        cash_buffer_pct = float(cfg.get("CASH_BUFFER_PCT", 0.20))
        max_position_pct = float(cfg.get("MAX_POSITION_PCT", 0.20))

        positions = portfolio_emulator.get_positions()
        current_price = float(price) if price else 0

        # Count current positions (shares > 0)
        num_positions = sum(1 for s in positions.values() if s > 0)

        # 1. Max concurrent positions
        if symbol not in positions or positions.get(symbol, 0) <= 0:
            # This would be a new position
            if num_positions >= max_positions:
                reason = f"Exposure limit: {num_positions}/{max_positions} positions full"
                _log(f"{symbol}: {reason}", "yellow")
                return (0, 1.0, None, reason)

        # 2. Cash buffer check
        cash = portfolio_emulator.get_cash()
        # Estimate equity (need prices dict — use current_price for this symbol at least)
        prices_dict = {symbol: current_price} if current_price > 0 else {}
        equity = portfolio_emulator.get_portfolio_value(prices_dict)
        if equity <= 0:
            equity = cash  # fallback

        min_cash = equity * cash_buffer_pct
        available_for_trade = cash - min_cash

        if available_for_trade <= 0:
            reason = f"Exposure limit: cash {cash:.0f} below buffer ({cash_buffer_pct*100:.0f}% of {equity:.0f})"
            _log(f"{symbol}: {reason}", "yellow")
            return (0, 1.0, None, reason)

        # 3. Max single position size
        if current_price > 0:
            max_position_value = equity * max_position_pct
            current_position_value = positions.get(symbol, 0) * current_price
            if current_position_value >= max_position_value:
                reason = f"Exposure limit: {symbol} position ${current_position_value:.0f} >= max ${max_position_value:.0f}"
                _log(f"{symbol}: {reason}", "yellow")
                return (0, 1.0, None, reason)

        return (0, None, None, None)
