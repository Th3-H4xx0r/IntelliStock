# INTELLISTOCK_SCHEMA: {"strategy": "TradeAllocator", "weight": 1.0, "execution_position": 100, "decision_phase": "post", "execution_scope": "per_symbol", "conditions": {}, "config": {"MAX_POSITION_PCT": 0.20, "CASH_BUFFER_PCT": 0.20}}
# INTELLISTOCK_DESCRIPTION: Post-decision trade sizing for news pipeline. Computes buy_cash based on equity/buffer limits, or sell_fraction=1.0 for full exits.
# DIFFICULTY: 2
"""
Trade Allocator — post-decision sizing strategy for the news pipeline.

Runs AFTER the buy/sell/hold decision is made. Converts the decision into
concrete trade parameters:
  - BUY: compute buy_cash based on equity, position limits, and cash buffer
  - SELL: return sell_fraction=1.0 (full exit)

Matches the broker's post-decision get_trade_size interface:
  get_trade_size(symbol, side, price, account_size, config, portfolio_emulator=...)

Note: Sibling strategy state (RiskManager stop/tp, PdtGuard entry dates,
CooldownManager exit dates) is self-managed by those strategies using
portfolio_emulator.get_trade_history() — TradeAllocator does not need to
coordinate with them.

Config:
  MAX_POSITION_PCT: max fraction of equity for a single position (default 0.20)
  CASH_BUFFER_PCT: minimum cash fraction to keep as buffer (default 0.20)
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
        intellistock_logger.log(msg, color, service="TradeAllocator")
except Exception:
    def _log(msg, color="white"):
        print(f"[TradeAllocator] {msg}")


class TradeAllocator:
    """Post-decision sizing. Returns buy_cash or sell_fraction."""

    def get_trade_size(self, symbol, side, price, account_size, config,
                       portfolio_emulator=None):
        """
        Called by broker after buy/sell decision.

        Args:
            symbol: ticker
            side: 'buy' or 'sell'
            price: current price per share
            account_size: total portfolio equity
            config: strategy config dict
            portfolio_emulator: PortfolioEmulator instance (backtest) or None (live)

        Returns:
            dict with 'buy_cash' or 'sell_fraction', or None for broker default.
        """
        if price is None or price <= 0:
            return None

        cfg = config or {}
        price = float(price)

        if side == "buy":
            return self._size_buy(symbol, price, account_size, cfg, portfolio_emulator)

        if side == "sell":
            _log(f"{symbol}: SELL sizing fraction=1.0 (full exit)", "yellow")
            return {"sell_fraction": 1.0}

        return None

    def _size_buy(self, symbol, price, account_size, cfg, portfolio_emulator):
        """Compute buy_cash respecting position size and cash buffer limits."""
        max_position_pct = float(cfg.get("MAX_POSITION_PCT", 0.20))
        cash_buffer_pct = float(cfg.get("CASH_BUFFER_PCT", 0.20))

        equity = float(account_size) if account_size else 0
        if equity <= 0:
            return None

        # Base allocation: fraction of equity
        base_cash = equity * max_position_pct

        # Enforce cash buffer
        if portfolio_emulator is not None:
            available_cash = portfolio_emulator.get_cash()
            min_cash = equity * cash_buffer_pct
            max_spendable = available_cash - min_cash
            if max_spendable <= 0:
                _log(f"{symbol}: no cash available after buffer "
                     f"(cash={available_cash:.0f}, buffer={min_cash:.0f})", "yellow")
                return None
            buy_cash = min(base_cash, max_spendable)
        else:
            buy_cash = base_cash

        if buy_cash <= 0:
            return None

        _log(f"{symbol}: BUY sizing ${buy_cash:.2f} "
             f"(equity={equity:.0f}, max_pos={max_position_pct*100:.0f}%)", "green")
        return {"buy_cash": buy_cash}
