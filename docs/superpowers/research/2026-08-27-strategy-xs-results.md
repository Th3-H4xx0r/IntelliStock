# Strategy XS: built, deployed, and measured

Date: 2026-08-27 · Document 199 · Instance `strategy-xs` · Backtest 641300

## Verdict

Strategy XS is fully implemented, tested and deployed, and it demonstrably
trades end to end. It does NOT meet the objective. On the only window the
engine's data allows, it underperforms SPY.

| | Strategy XS | SPY |
|---|---:|---:|
| 2021-11-01 -> 2026-08-27, $6,000 | **+49.77%** ($8,986) | +66.79% |
| CAGR (4.8y) | ~8.8% | ~11.3% |

P&L by leg: QLD +$1,666, GLD +$1,000, SPY +$301, UUP +$59, BIL -$58.
Spread cost $280 — 4.7% of starting capital. Rejected orders: 0.

The local fifteen-year harness said 17.52% against SPY's 15.17%. The engine
says XS loses. That is the fourth time this session a local model has
over-stated and the engine has refused the return, and the engine is the
authority.

The window matters and does not rescue the result: it starts at the
November-2021 top and contains 2022, where XS lost about 27% against SPY's 18%.
A strategy whose edge only appears in windows that exclude its worst year is
not a strategy with an edge.

## What the end-to-end run was worth

Two integration defects, neither catchable by any unit test, both silent:

1. **The class name is part of the broker contract.** `broker.py` resolves a
   run-once strategy by CamelCasing its id — `strategy_xs` -> `StrategyXs`.
   Shipped as `StrategyXS`, BT634331 completed all 1,259 sessions with zero
   fills, zero orders and no error. The only evidence was one startup line:
   `Class 'StrategyXs' not found ... has no run_once method; skipping`.

2. **The broker caps any single position at 15% of equity and TRIMS to zero
   rather than clipping.** A 60%-of-NAV core cannot be built underneath it. On
   BT102936 every QLD buy logged `trimmed to $0.00 ... cap=15%=$929.32 -> SKIP`
   and the run measured a portfolio nobody designed. Strategy X hides this by
   setting the cap to 0.95, which disarms the failsafe process-wide for every
   sibling strategy in the same document; XS sets 0.65, the smallest value that
   lets its design express itself.

Both were found only because the run went all the way to the engine. Both would
have shipped invisibly.

## What was actually verified

- The strategy self-declares its universe: seeded with SPY alone, it traded
  **QLD, SPY, BIL, GLD and UUP** — 55/13/52/57/13 fills.
- Zero errors, zero exceptions, zero rejected orders, zero position-cap trims.
- Every sell carries `action_intent=etf_sell`; no `would_block_in_phase2`.
- 543 tests pass across the Strategy X and XS suites.

## The frozen gate

Run before the engine, on fifteen years, at the engine-calibrated 23 bps:

| condition | result |
|---|---|
| CAGR above SPY | PASS (17.52 vs 15.17) |
| maxDD better than SPY | FAIL (-33.73 vs -33.72) |
| no more losing years than SPY | FAIL (4 vs 2) |
| halves agree in sign | FAIL |

The gate was written into the plan before the implementation existed. It
failed, the plan says a failed gate means the strategy stays disabled, and it
stays disabled. `strategy_xs_enabled` defaults to false.

## Why the search over-stated

A search over 4,104 constructions, selected on 2011-2018 alone, predicted
20.20% CAGR / -33.16% maxDD / 2 losing years. The implementation delivered
17.52 / -33.73 / 4. The gap concentrates in whipsaw years — 2015 measured
-10.3% against a predicted +6.0% — because the search switched legs
instantaneously at zero cost while the real code sells after the drop and
rebuys after the bounce, one bar later. Turnover is 505%/yr, not the ~2 flips a
year the search assumed.

One arithmetic error is recorded because the gate caught it: `core_weight` is a
share of the RESIDUAL, not of NAV. Set to 1.0 it put 70% of NAV in a 3x fund —
210% beta, measured at -48.32% drawdown — where the design called for 120%.

## Open

- `live_risk_state.DEFAULT_MAX_LEVERAGED_FRACTION = 0.10` still blocks any
  leveraged ETF above 10% of equity on the live path, and blocks rather than
  clips. XS cannot trade live regardless of its economics.
- The engine did not persist `pnl_percentage`, `final_value` or `max_drawdown`
  on any run this session; `pnl_percent` and `pnl_per_stock` are populated and
  were used instead.
- The `[StrategyXs]` log lines do not reach the log sink — the wrapper's
  logger import differs from Strategy X's — so per-session targets could not be
  read back from the log.
