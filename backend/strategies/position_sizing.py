# INTELLISTOCK_SCHEMA: {"strategy": "PositionSizing", "weight": 0.5, "execution_position": 0, "decision_phase": "post", "conditions": {}, "config": {"risk_pct": 2, "stop_loss_pct": 5}}
# INTELLISTOCK_DESCRIPTION: Fixed-percentage risk position sizing. Not a voter; called after buy/sell decision. Config: risk_pct (default 2), stop_loss_pct (default 5).
# DIFFICULTY: 2
"""
Position Sizing strategy. Not a voting strategy—used by the broker only after a trade
decision (buy/sell) is reached. Returns the dollar amount (for buy) or sell fraction (for sell)
to use for the trade.

Implements Fixed Percentage Risk Method:
  Shares to Buy = (Account Size × Risk %) / Risk per Share
  Risk per Share = |Current Price - Stop Loss|
  Stop loss can be set as a percentage below entry (e.g. 5%) or as a fixed price.

Config: risk_pct (default 2), stop_loss_pct (default 5, meaning 5% below price).
"""

from __future__ import annotations

try:
    import os
    import sys
    broker_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if broker_dir not in sys.path:
        sys.path.insert(0, broker_dir)
    from intellistock_logger import intellistock_logger
    def _log(msg, color="white"):
        intellistock_logger.log(msg, color, service="PositionSizing")
except Exception:
    def _log(msg, color="white"):
        print(f"[PositionSizing] {msg}")


class PositionSizing:
    """
    Fixed Percentage Risk position sizing. Called by the broker after a buy/sell decision
    to get the trade size. Does not vote (buy/hold/sell).
    """

    def get_trade_size(self, symbol, side, price, account_size, config, portfolio_emulator=None):
        """
        Return the size to use for this trade. Called only when decision is buy or sell.

        Args:
            symbol: Ticker symbol
            side: 'buy' or 'sell'
            price: Current price per share
            account_size: Total portfolio value (cash + positions)
            config: Dict with risk_pct (e.g. 2), stop_loss_pct (e.g. 5) or stop_loss_price
            portfolio_emulator: Optional; used to get available cash for buy cap

        Returns:
            For buy: dict with 'buy_cash' (dollar amount to use), or None to use broker default.
            For sell: dict with 'sell_fraction' (0-1), or None to use broker default (sell all).
        """
        if price is None or price <= 0:
            return None
        price = float(price)
        cfg = config or {}
        risk_pct = float(cfg.get("risk_pct", 2.0)) / 100.0  # e.g. 2 -> 0.02
        stop_loss_pct = float(cfg.get("stop_loss_pct", 5.0)) / 100.0  # e.g. 5 -> 0.05
        stop_loss_price = cfg.get("stop_loss_price")  # optional explicit price

        if side == "buy":
            if stop_loss_price is not None:
                try:
                    stop_loss_price = float(stop_loss_price)
                except (TypeError, ValueError):
                    stop_loss_price = None
            if stop_loss_price is None:
                stop_loss_price = price * (1.0 - stop_loss_pct)
            risk_per_share = price - stop_loss_price
            if risk_per_share <= 0:
                _log(f"[POSITION_SIZING] {symbol}: risk_per_share <= 0 (price={price}, stop={stop_loss_price}); using default size", "yellow")
                return None
            risk_amount = account_size * risk_pct
            shares = risk_amount / risk_per_share
            buy_cash = shares * price
            # Cap by available cash if we have portfolio_emulator
            if portfolio_emulator is not None:
                available = portfolio_emulator.get_cash()
                if buy_cash > available and available > 0:
                    buy_cash = available
                    _log(f"[POSITION_SIZING] {symbol}: capped buy_cash to available cash {available:.2f}", "cyan")
            if buy_cash <= 0:
                return None
            _log(f"[POSITION_SIZING] {symbol}: buy size ${buy_cash:.2f} (account={account_size:.2f}, risk%={risk_pct*100}, risk/share={risk_per_share:.2f}, shares={shares:.2f})", "green")
            return {"buy_cash": buy_cash}

        if side == "sell":
            # Default: sell all; position_sizing doesn't change sell size unless we add logic later
            return None

        return None
