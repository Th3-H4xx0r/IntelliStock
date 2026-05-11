# INTELLISTOCK_SCHEMA: {"strategy": "Example", "weight": 0.5, "execution_position": 0, "conditions": {}, "config": {}}
# INTELLISTOCK_DESCRIPTION: Example strategy. Random buy/hold/sell for demos. No config required.
# DIFFICULTY: 1
"""
Example strategy. Strategy name from DB: "Example" (file example.py, class Example).
Returns: 1 = buy, 0 = hold, -1 = sell.
"""
import random

class Example:
    """Example strategy. Uses config and optional conditions to produce a score."""

    def run(self, symbol, price, current_time, config, conditions, data=None, portfolio_emulator=None, strategy_cache=None, time_increment=None):
        """
        Run the strategy.
        symbol: str
        price: float or None (current price for symbol)
        current_time: datetime
        config: dict (strategy-specific config from DB)
        conditions: list[dict] (optional conditions from DB)
        data: optional backtest bar data (symbol -> list of bars)
        portfolio_emulator: optional PortfolioEmulator (backtest only); current positions/cash
        strategy_cache: optional dict for this strategy to persist data across runs (broker-global).
        Returns: 1 (buy), 0 (hold), -1 (sell)
        """
        return random.choice([1,0,0,0,0,0,-1])
