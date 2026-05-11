"""
PortfolioEmulator: used only for backtesting. Tracks cash, positions, trades,
and portfolio value snapshots over time.
"""

import copy


class PortfolioEmulator:
    """
    Emulates a portfolio for backtesting. Records all buys/sells with timestamps
    and portfolio value snapshots so you can retrieve trade history and portfolio
    value history at the end of a backtest.
    """

    def __init__(self, initial_cash=100000.0):
        self._cash = float(initial_cash)
        self._initial_value = float(initial_cash)  # Track original portfolio value
        self._positions = {}  # ticker -> shares (float)
        self._trades = []    # list of { timestamp, action, ticker, shares, price, total, cash_after }
        self._portfolio_snapshots = []  # list of { timestamp, value, cash, positions_snapshot, prices }
        self._last_prices = {}  # V7.3: track last known prices for portfolio valuation fallback

    def buy(self, ticker, shares, price, timestamp=None):
        """
        Execute a buy: spend cash and add to positions.
        ticker: str
        shares: float (can be fractional)
        price: float (price per share)
        timestamp: optional datetime for the trade record (caller can pass current_time)
        Returns: True if trade was executed, False if insufficient cash.
        """
        if shares <= 0 or price <= 0:
            return False
        total = shares * price
        if total > self._cash:
            return False
        self._cash -= total
        self._positions[ticker] = self._positions.get(ticker, 0.0) + shares
        self._trades.append({
            "timestamp": timestamp,
            "action": "buy",
            "ticker": ticker,
            "shares": shares,
            "price": price,
            "total": total,
            "cash_after": self._cash,
        })
        print(f"TRADE: Buy {shares} shares of {ticker} at {price} for a total of {total} cash_after: {self._cash}")
        return True

    def sell(self, ticker, shares, price, timestamp=None):
        """
        Execute a sell: reduce positions and add cash.
        Returns: True if trade was executed, False if insufficient shares.
        """
        if shares <= 0 or price <= 0:
            return False
        current = self._positions.get(ticker, 0.0)
        if current < shares:
            shares = current  # sell all we have
        if shares <= 0:
            return False
        total = shares * price
        self._cash += total
        self._positions[ticker] = current - shares
        if self._positions[ticker] <= 0:
            del self._positions[ticker]
        self._trades.append({
            "timestamp": timestamp,
            "action": "sell",
            "ticker": ticker,
            "shares": shares,
            "price": price,
            "total": total,
            "cash_after": self._cash,
        })
        print(f"TRADE: Sell {shares} shares of {ticker} at {price} for a total of {total} cash_after: {self._cash}")
        return True

    def get_portfolio_value(self, prices):
        """
        Current portfolio value: cash + sum(positions[t] * prices[t]) for all positions.
        prices: dict ticker -> price (use live/current prices for each ticker).
        Missing tickers in prices are valued at 0.
        """
        value = self._cash
        for ticker, shares in self._positions.items():
            p = (prices or {}).get(ticker)
            if p is not None:
                value += shares * float(p)
        return value

    def save_portfolio_snapshot(self, prices, timestamp=None):
        """
        Record current portfolio value at this moment with the given prices.
        prices: dict ticker -> price (live/current prices for valuation).
        timestamp: optional datetime (e.g. current_time in backtest loop).
        """
        value = self.get_portfolio_value(prices)
        # V3: Track last known prices for portfolio_total fallback
        if prices:
            if not hasattr(self, '_last_prices'):
                self._last_prices = {}
            self._last_prices.update(prices)
        self._portfolio_snapshots.append({
            "timestamp": timestamp,
            "value": value,
            "cash": self._cash,
            "positions_snapshot": copy.copy(self._positions),
            "prices": copy.copy(prices) if prices else {},
        })

    def get_trade_history(self):
        """Return list of all trades, each with timestamp, action, ticker, shares, price, total, cash_after."""
        return list(self._trades)

    def get_portfolio_history(self):
        """Return list of all snapshots: timestamp and value (and optionally cash/positions)."""
        return list(self._portfolio_snapshots)

    def get_cash(self):
        """Current cash balance."""
        return self._cash

    def get_available_cash(self, reserved: float = 0.0):
        """Cash available for non-reserving strategies (e.g. total_cash - reserved_capital)."""
        return max(0.0, self._cash - float(reserved))

    def get_positions(self):
        """Current positions: dict ticker -> shares (copy)."""
        return copy.copy(self._positions)

    def get_positions_value(self, prices):
        """Value of positions only (excluding cash) using given prices."""
        v = 0.0
        for ticker, shares in self._positions.items():
            p = (prices or {}).get(ticker)
            if p is not None:
                v += shares * float(p)
        return v

    def execute_signal(self, ticker, signal, price, timestamp=None, cash_per_trade=1000.0, sell_fraction=1.0):
        """
        Convenience: execute a strategy signal (1=buy, -1=sell, 0=hold) with a simple rule.
        Buy: invest up to cash_per_trade; if cash is less than cash_per_trade, use all available cash so the trade still goes through.
        Sell: sell sell_fraction (0-1) of shares of ticker; default 1.0 = sell all.
        Returns True if a trade was executed.
        """
        if price is None or price <= 0:
            return False
        if signal == 1:
            amount_to_use = min(cash_per_trade, self._cash)
            if amount_to_use <= 0:
                return False
            shares = amount_to_use / price
            return self.buy(ticker, shares, price, timestamp=timestamp)
        if signal == -1:
            total_shares = self._positions.get(ticker, 0.0)
            if total_shares <= 0:
                return False
            frac = max(0.0, min(1.0, float(sell_fraction)))
            shares = total_shares * frac if frac < 1.0 else total_shares
            if shares <= 0:
                return False
            return self.sell(ticker, shares, price, timestamp=timestamp)
        return False

    def get_initial_value(self):
        """Return the original/initial portfolio value."""
        return self._initial_value

    def print_portfolio(self, prices, logger=None):
        """
        Print a pretty formatted summary of the portfolio.
        prices: dict ticker -> price (current prices for valuation)
        logger: optional logger function(msg, color) (if None, uses print)
        """
        final_value = self.get_portfolio_value(prices)
        cash = self.get_cash()
        positions = self.get_positions()
        positions_value = self.get_positions_value(prices)
        pnl = final_value - self._initial_value
        pnl_percent = (pnl / self._initial_value * 100) if self._initial_value > 0 else 0.0
        trades = self.get_trade_history()
        
        # Determine color/sign for P&L
        pnl_sign = "+" if pnl >= 0 else ""
        pnl_color = "green" if pnl >= 0 else "red"
        
        # Helper to log with or without logger
        def log(msg, color="white"):
            if logger:
                logger(msg, color)
            else:
                print(msg)
        
        # Print header
        log("=" * 70, "cyan")
        log("PORTFOLIO SUMMARY", "cyan")
        log("=" * 70, "cyan")
        
        # Initial value
        log(f"Initial Value:     ${self._initial_value:,.2f}", "white")
        
        # Final value
        log(f"Final Value:       ${final_value:,.2f}", "white")
        
        # P&L
        pnl_str = f"{pnl_sign}${abs(pnl):,.2f} ({pnl_sign}{pnl_percent:.2f}%)"
        log(f"Profit & Loss:     {pnl_str}", pnl_color)
        
        log("-" * 70, "white")
        
        # Cash
        log(f"Cash:              ${cash:,.2f}", "white")
        
        # Positions value
        log(f"Positions Value:   ${positions_value:,.2f}", "white")
        
        # Positions breakdown
        if positions:
            log("-" * 70, "white")
            log("Positions:", "white")
            for ticker, shares in sorted(positions.items()):
                price = (prices or {}).get(ticker, 0.0)
                position_value = shares * price
                log(f"  {ticker:8s}  {shares:10.4f} shares  @ ${price:8.2f}  = ${position_value:10,.2f}", "white")
        else:
            log("Positions:         None", "white")
        
        # Trade summary
        log("-" * 70, "white")
        log(f"Total Trades:       {len(trades)}", "white")
        if trades:
            buy_count = sum(1 for t in trades if t.get("action") == "buy")
            sell_count = sum(1 for t in trades if t.get("action") == "sell")
            log(f"  Buys:            {buy_count}", "white")
            log(f"  Sells:           {sell_count}", "white")
        
        log("=" * 70, "cyan")
