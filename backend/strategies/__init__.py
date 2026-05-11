# Strategy modules: each file is named after the strategy (lowercase, underscores).
# The class inside must match the strategy name (PascalCase) and implement run(symbol, price, current_time, config, conditions, data) -> 1|0|-1.
#
# Future Trades Queue:
# All strategies can schedule trades for future dates using the strategy_cache parameter.
# Usage in any strategy's run() method:
#   from future_trades_util import schedule_trade, pop_pending_trades, expire_old_trades
#   pending = pop_pending_trades(strategy_cache, symbol, current_time)
#   schedule_trade(strategy_cache, symbol="AAPL", target_date="2026-03-15", signal=1, reason="Earnings")
# Currently implemented: News, GraphNexusAnalysis. Others have support via strategy_cache.
