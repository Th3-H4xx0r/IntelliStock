# INTELLISTOCK_SCHEMA: {"strategy": "RiskTolerance", "weight": 0.5, "execution_position": 0, "conditions": {}, "config": {"max_loss_dollars": 500, "take_profit_pct": 10}}
# INTELLISTOCK_DESCRIPTION: Sell-only strategy. Triggers sell on max loss (dollars) or take-profit (%). Does not vote buy.
# DIFFICULTY: 2
"""
Risk Tolerance strategy. Strategy name from DB: "risk_tolerance" or "RiskTolerance".
Monitors positions and automatically triggers sell signals based on:
- Max loss: Sell if position loss exceeds a dollar amount threshold
- Take profit: Sell if position profit exceeds a percentage threshold

Returns: 1 = buy (never, this strategy only sells), 0 = hold, -1 = sell.
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
        intellistock_logger.log(msg, color, service="RiskToleranceStrategy")
except Exception:
    def _log(msg, color="white"):
        print(f"[RiskToleranceStrategy] {msg}")


def _calculate_average_entry_price(trades, symbol):
    """
    Calculate average entry price for current position by tracking cost basis.
    Uses FIFO-like approach: tracks cost basis and remaining shares.
    Returns: (average_entry_price, current_shares) or (None, 0) if no position
    """
    if not trades:
        return None, 0.0
    
    # Filter trades for this symbol
    symbol_trades = [t for t in trades if t.get("ticker") == symbol]
    if not symbol_trades:
        return None, 0.0
    
    # Track cost basis and shares using FIFO
    # We'll track buys as (shares, price) and match sells against them
    buy_queue = []  # List of (shares, price) tuples
    current_shares = 0.0
    total_cost_basis = 0.0
    
    for trade in symbol_trades:
        action = trade.get("action")
        shares = float(trade.get("shares", 0))
        price = float(trade.get("price", 0))
        
        if action == "buy":
            buy_queue.append((shares, price))
            current_shares += shares
            total_cost_basis += shares * price
        elif action == "sell":
            # Remove shares from buy queue (FIFO)
            remaining_to_sell = shares
            while remaining_to_sell > 0 and buy_queue:
                buy_shares, buy_price = buy_queue[0]
                if buy_shares <= remaining_to_sell:
                    # Remove entire buy
                    total_cost_basis -= buy_shares * buy_price
                    current_shares -= buy_shares
                    remaining_to_sell -= buy_shares
                    buy_queue.pop(0)
                else:
                    # Partial removal
                    total_cost_basis -= remaining_to_sell * buy_price
                    current_shares -= remaining_to_sell
                    buy_queue[0] = (buy_shares - remaining_to_sell, buy_price)
                    remaining_to_sell = 0
    
    if current_shares <= 0:
        return None, 0.0
    
    average_entry_price = total_cost_basis / current_shares
    return average_entry_price, current_shares


class RiskTolerance:
    """
    Risk tolerance strategy that monitors positions and triggers sells based on:
    - max_loss_percent: Maximum loss percentage before selling (e.g., 5.0 means sell if loss >= 5%)
    - take_profit_percent: Take profit percentage (e.g., 10.0 means sell if profit >= 10%)
    
    This strategy only returns sell signals (-1) or hold (0). It never returns buy (1).
    It requires portfolio_emulator to track positions and entry prices.
    
    Returns: tuple (score, weight_override)
    - score: -1 (sell), 0 (hold)
    - weight_override: 1.0 only for sell (take profit / stop loss); None for hold
      Override applies only to buy/sell signals (take profit, stop loss), not to hold.
    """

    def __init__(self):
        self.max_loss_percent = None  # Percentage (e.g., 5.0 for 5%)
        self.take_profit_percent = None  # Percentage (e.g., 10.0 for 10%)

    def run(self, symbol, price, current_time, config, conditions, data=None, portfolio_emulator=None, strategy_cache=None, time_increment=None):
        """
        Check if position should be sold based on max loss or take profit.
        
        Args:
            symbol: Stock ticker symbol
            price: Current price per share
            config: Dict with 'max_loss_percent' (float, percentage) and 'take_profit_percent' (float, percentage)
            conditions: Not used (for compatibility)
            data: Not used (for compatibility)
            portfolio_emulator: PortfolioEmulator instance to get positions and trade history
        
        Returns:
            -1 if should sell (max loss exceeded or take profit reached)
            0 if should hold (no position, or thresholds not met)
        """
        # Get parameters from config (preferred) or conditions (fallback)
        # Support both 'max_loss_percent' (new) and 'max_loss' (old, for backward compatibility)
        max_loss_percent = config.get("max_loss_percent") if config else None
        if max_loss_percent is None and config:
            # Backward compatibility: check for old 'max_loss' key (treat as percentage)
            max_loss_percent = config.get("max_loss")
        if max_loss_percent is None and conditions:
            max_loss_percent = conditions.get("max_loss_percent")
            if max_loss_percent is None:
                max_loss_percent = conditions.get("max_loss")  # Backward compatibility
        if max_loss_percent is not None:
            try:
                max_loss_percent = float(max_loss_percent)
            except (TypeError, ValueError):
                max_loss_percent = None
        
        take_profit_percent = config.get("take_profit_percent") if config else None
        if take_profit_percent is None and conditions:
            take_profit_percent = conditions.get("take_profit_percent")
        if take_profit_percent is not None:
            try:
                take_profit_percent = float(take_profit_percent)
            except (TypeError, ValueError):
                take_profit_percent = None
        
        # Validate parameters
        if max_loss_percent is None and take_profit_percent is None:
            _log(f"[RISK] {symbol}: No max_loss_percent or take_profit_percent configured. Skipping.", "yellow")
            return (0, None)
        
        if max_loss_percent is not None and max_loss_percent < 0:
            _log(f"[RISK] {symbol}: Invalid max_loss_percent ({max_loss_percent}). Must be >= 0.", "yellow")
            return (0, None)
        
        if take_profit_percent is not None and take_profit_percent < 0:
            _log(f"[RISK] {symbol}: Invalid take_profit_percent ({take_profit_percent}). Must be >= 0.", "yellow")
            return (0, None)
        
        # Need portfolio_emulator to check positions
        if portfolio_emulator is None:
            _log(f"[RISK] {symbol}: No portfolio_emulator available. Skipping.", "yellow")
            return (0, None)
        
        # Get current position
        positions = portfolio_emulator.get_positions() or {}
        shares = positions.get(symbol, 0.0) or 0.0
        
        if shares <= 0:
            # No position, nothing to monitor
            return (0, None)
        
        # Need current price
        if price is None:
            _log(f"[RISK] {symbol}: No current price available. Skipping.", "yellow")
            return (0, None)  # Hold: don't override so other strategies decide
        
        try:
            current_price = float(price)
        except (TypeError, ValueError):
            _log(f"[RISK] {symbol}: Invalid price: {price}. Skipping.", "yellow")
            return (0, None)  # Hold: don't override
        
        if current_price <= 0:
            _log(f"[RISK] {symbol}: Invalid price: {current_price}. Skipping.", "yellow")
            return (0, None)  # Hold: don't override
        
        # Calculate average entry price from trade history
        trades = portfolio_emulator.get_trade_history() or []
        avg_entry_price, current_shares_check = _calculate_average_entry_price(trades, symbol)
        
        if avg_entry_price is None or current_shares_check <= 0:
            _log(f"[RISK] {symbol}: Could not calculate entry price. Skipping.", "yellow")
            return (0, None)  # Hold: don't override
        
        # Verify shares match
        if abs(current_shares_check - shares) > 0.001:
            _log(f"[RISK] {symbol}: Position mismatch (shares={shares}, calculated={current_shares_check}). Using calculated.", "yellow")
            shares = current_shares_check
        
        # Calculate P&L percentage
        pnl_percent = ((current_price - avg_entry_price) / avg_entry_price * 100.0) if avg_entry_price > 0 else 0.0
        
        # Check max loss threshold (percentage)
        if max_loss_percent is not None and pnl_percent <= -max_loss_percent:
            position_value = shares * current_price
            cost_basis = shares * avg_entry_price
            pnl = position_value - cost_basis
            _log(
                f"[RISK] {symbol}: Max loss exceeded! Loss={abs(pnl_percent):.2f}% >= {max_loss_percent:.2f}% "
                f"(entry=${avg_entry_price:.2f}, current=${current_price:.2f}, shares={shares:.4f}, P&L=${pnl:.2f}) -> SELL",
                "red"
            )
            return (-1, 1.0)  # Sell signal with 100% weight override
        
        # Check take profit threshold
        if take_profit_percent is not None and pnl_percent >= take_profit_percent:
            position_value = shares * current_price
            cost_basis = shares * avg_entry_price
            pnl = position_value - cost_basis
            _log(
                f"[RISK] {symbol}: Take profit reached! Profit={pnl_percent:.2f}% >= {take_profit_percent:.2f}% "
                f"(entry=${avg_entry_price:.2f}, current=${current_price:.2f}, shares={shares:.4f}, P&L=${pnl:.2f}) -> SELL",
                "green"
            )
            return (-1, 1.0)  # Sell signal with 100% weight override
        
        # Hold - thresholds not met
        # Don't override on hold: only take profit and stop loss (sell) override other strategies
        return (0, None)  # (score=hold, weight_override=None)
