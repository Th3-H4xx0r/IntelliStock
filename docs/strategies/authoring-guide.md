# Strategy authoring guide

This page is for someone writing their first IntelliStock strategy
module. Read the
[How strategies work](../../README.md#how-strategies-work) section of
the main README first — it covers the lifecycle, the two scopes
(`per_symbol` / `run_once`), and the two phases (pre-decision /
post-decision). This page picks up from there.

## What a strategy module looks like

A strategy is a Python module under `backend/strategies/` exporting a
single class. The class name derives from the filename in PascalCase:
`my_strategy.py` → `MyStrategy`.

The simplest reference implementation in the codebase is
[`backend/strategies/rsi.py`](../../backend/strategies/rsi.py). Open
it in another tab while you read this.

## Anatomy

Every strategy module has four parts:

### 1. JSON schema header

A single docstring-style comment at the top of the file declares the
strategy's metadata and default config. The CLI and UI parse this
header to render the config form, validate user input, and seed
defaults.

```python
# INTELLISTOCK_SCHEMA: {
#   "strategy": "my_strategy",
#   "weight": 0.5,
#   "execution_position": 0,
#   "decision_phase": "pre",
#   "execution_scope": "per_symbol",
#   "conditions": {},
#   "config": {
#     "lookback_period": 14,
#     "threshold": 0.5
#   }
# }
```

| Field                | Meaning                                                                          |
| -------------------- | -------------------------------------------------------------------------------- |
| `strategy`           | Module identifier. Must match the filename without the `.py` extension.          |
| `weight`             | Default weight in vote aggregation. The user can override.                       |
| `execution_position` | Order within the phase; lower runs first. Use 0 unless ordering matters.         |
| `decision_phase`     | `"pre"` (votes) or `"post"` (sizes / allocates after the vote).                  |
| `execution_scope`    | `"per_symbol"` (one call per ticker per tick) or `"run_once"` (one call total).  |
| `conditions`         | Reserved. Leave empty for new strategies.                                        |
| `config`             | The default config dict. Whatever keys you put here become user-editable.        |

### 2. Class definition

```python
class MyStrategy:
    def __init__(self, config: dict):
        self.config = config
        self.lookback = int(config.get("lookback_period", 14))
        self.threshold = float(config.get("threshold", 0.5))
```

The `config` dict is the same shape as the schema header's `config`
key, with user overrides applied. Coerce types defensively in
`__init__` because the dict round-trips through JSON.

### 3. The `run()` method

The broker loop calls `run()` once per tick. The signature differs by
scope:

```python
# per_symbol scope
def run(self, symbol, prices, current_time, data):
    """
    symbol       — the ticker, e.g. "NVDA"
    prices       — list of recent OHLCV bars; prices[-1] is the current bar
    current_time — datetime of the current bar
    data         — shared dict with portfolio state, indicators, etc.
    """
    ...
    return vote, weight_override, size_hint, reason
```

```python
# run_once scope
def run(self, symbols, prices_by_symbol, current_time, data):
    """
    symbols           — list of all tickers in the watchlist
    prices_by_symbol  — dict[symbol -> list of OHLCV bars]
    current_time      — datetime of the current bar
    data              — shared dict
    """
    ...
    return {symbol: (vote, weight_override, size_hint, reason)
            for symbol in symbols}
```

### 4. Return shape

A pre-decision strategy returns a 4-tuple per (symbol, tick):

| Position | Type                  | Meaning                                                                  |
| -------- | --------------------- | ------------------------------------------------------------------------ |
| 0        | `int ∈ {-1, 0, +1}`   | The vote. `+1` = buy, `0` = hold, `-1` = sell.                          |
| 1        | `float \| None`       | Weight override. `None` = use the configured weight. `1.0` = force-only. |
| 2        | `dict \| None`        | Size hint, e.g. `{"buy_cash": 5000}` or `{"sell_fraction": 0.5}`.        |
| 3        | `str`                 | Human-readable reason, surfaced in trade logs and the UI.                |

A post-decision strategy returns just the size hint (positions 0 and 1
are ignored).

## Data you can read at decision time

Inside `run()`, the most useful inputs are:

- **`prices`** — list of OHLCV dicts in chronological order.
  `prices[-1]` is the current bar. Each bar is
  `{"timestamp": ..., "open": ..., "high": ..., "low": ..., "close": ..., "volume": ...}`.
- **`data["portfolio"]`** — current positions:
  `{symbol: {"shares": int, "entry_price": float, "entry_time": datetime, "high_water": float}}`.
- **`data["account"]`** — cash, equity, day-trade count.
- **`data["indicators"]`** — pre-computed by the broker loop: SMA, EMA,
  RSI, MACD, ATR. Cheaper than recomputing per strategy.
- **`data["strategy_cache"][self.__class__.__name__]`** — a per-strategy
  dict that persists across ticks. Use it for state that's expensive
  to recompute (trained models, baseline values).

Don't read from RethinkDB or Neo4j inside `run()` — the loop runs
hundreds of times per backtest tick and the network round-trip
dominates. Stage data in `__init__` or in `data`.

## Registering and testing

1. Drop the file into `backend/strategies/`. Filename → class name.
2. Restart the backend (or the instance container) to pick up the new
   module. The strategy registry uses dynamic `importlib` discovery,
   so no manual registration is needed.
3. Create a strategy via the UI (Strategies tab) or CLI
   (`docker compose exec backend python cli.py strategy create`). Pick
   your `strategy_type` and the config form will render from your
   schema header.
4. Link the strategy to an instance and run a backtest. Watch the
   per-tick log for your `reason` strings — that's the fastest
   feedback loop.

## Common patterns

### Force-exit on stop loss

```python
def run(self, symbol, prices, current_time, data):
    pos = data["portfolio"].get(symbol)
    if pos and prices[-1]["close"] < pos["entry_price"] * (1 - self.stop_pct):
        return -1, 1.0, None, f"stop-loss tripped at {prices[-1]['close']}"
    return 0, None, None, "no signal"
```

`weight_override = 1.0` makes this strategy's vote dominate the
aggregation — a stop-loss should override every other voter.

### Burn-in guard

```python
def run(self, symbol, prices, current_time, data):
    if len(prices) < self.lookback:
        return 0, None, None, f"warming up ({len(prices)}/{self.lookback} bars)"
    ...
```

Strategies with indicator dependencies should bail with a 0 vote until
they have enough history. Don't divide-by-zero in production.

### Caching trained models

```python
def run(self, symbol, prices, current_time, data):
    cache = data["strategy_cache"].setdefault(self.__class__.__name__, {})
    if symbol not in cache:
        cache[symbol] = self._train_model(prices)
    forecast = cache[symbol].predict(prices[-1])
    ...
```

Training cost is amortised across the whole backtest. `Volatility`
uses this pattern.

## Don't

- Don't make HTTP calls inside `run()`. If you need external data,
  pre-fetch in a `run_once` strategy and stash in `data`.
- Don't mutate `prices` or `data["portfolio"]`. The broker loop owns
  those; mutations here will surprise other strategies.
- Don't `print()`. Use `logging.getLogger(__name__)` so your output
  flows into the per-instance log file with a timestamp.
- Don't catch all exceptions silently. Let them bubble; the broker
  loop will catch and log them with the strategy name attached.

## See also

- [`backend/strategies/rsi.py`](../../backend/strategies/rsi.py) —
  simplest indicator strategy.
- [`backend/strategies/ml_news.py`](../../backend/strategies/ml_news.py) —
  `run_once` LLM-driven strategy.
- [`backend/strategies/risk_manager.py`](../../backend/strategies/risk_manager.py) —
  weight-override + size-hint pattern.
- [`backend/broker.py`](../../backend/broker.py) — the broker loop
  that calls your `run()`. Search for `_load_strategy_class` to see
  how dynamic discovery works.
