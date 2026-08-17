# The measurement that works understates the strategy — and only live trading resolves it

Written 2026-08-17. This is the most consequential finding of the session and it changes the
production argument, so it gets its own document.

## The setup

Cold-start A/B was established as the only interpretable protocol: clear per-instance state
before each arm, attest `cold=True`, and the engine becomes deterministic (byte-identical A/A,
100% overlap, noise floor 10pp → 0.5pp). That is settled and it is what made five levers
measurable tonight.

## The problem with it

A cold start does not just remove contamination. It removes the **discovery pool** — and the pool
is what finds the big movers.

| run | state | names traded | which names |
|---|---|---:|---|
| window d warm (bt 333727) | warm | **12** | AAOI, **AEHR**, AIFD, AIOS, AIQ, **AXTI**, BC, BOTZ, D, **MXL**, RIVN, SPY |
| window d cold (bt 479057) | cold | **7** | MSFT, NVDA, NVTS, OIH, RIVN, SPY, VDE |
| window f warm (bt 325136) | warm | **13** | AAPL, ABBV, AMZN, BLTE, CCL, EQT, NVDA, RCL, SHEL, SPY, SQQQ, TSLA, VVX |
| window f cold (bt 790588) | cold | **9** | BLTE, COPJ, COPX, CSCO, CVLT, GCMG, RCL, SPY, SQQQ |

The warm window-d run found **AEHR (+152%), AAOI (+118%), AXTI (+90%), MXL (+396%)** — the exact
names the OBJECTIVE is written about. The cold run found large caps and returned **+10.15%
against SPY's +16.66%**. Same code, same config, same window; the difference is whether discovery
had a pool to draw from.

## The honest scoreboard, both ways

| window | SPY | COLD | vs SPY | WARM | vs SPY |
|---|---:|---:|---:|---:|---:|
| c | −5.29% | **+6.11%** | **+11.40pp** ✓ | +0.46% (pre-fix) | +5.75pp ✓ |
| d | +16.66% | +10.15% | **−6.51pp** ✗ | +20.53% | +3.87pp ✓ |
| f | +0.69% | −2.70% | **−3.39pp** ✗ | −5.09% | −5.78pp ✗ |

**Cold: 1 of 3 beat SPY, mean +0.50pp. Warm: 2 of 3, mean −0.05pp** (and 3 of 4 including the
stopped window a).

Neither is the truth:

* **Cold understates.** A 6-week backtest starting with an empty pool cannot rebuild what a
  long-running instance already knows. The system is being judged with its main input disabled.
* **Warm is contaminated.** The warm pool came from OTHER backtest runs — including runs over
  later calendar periods — so it can carry information the run should not have. That is exactly
  the 4,213 rows of carried state that made every prior A/B uninterpretable.

## Why this is an argument FOR paper trading, not a caveat about it

There is exactly one configuration that is both **warm** and **clean**: a live instance
accumulating its discovery pool forward in time. It has the pool depth that finds AEHR and AAOI,
and every row in it was written by data that existed when it was written — no cross-run
contamination, no future-dated state, no lookahead.

**Backtesting cannot produce that condition.** Cold backtests are measurable but starved; warm
backtests are representative but contaminated. Forward paper trading is the only setting that is
neither, and it is free.

That upgrades "paper first" from a compliance step to **the only experiment that can answer the
actual question.** It is also what `assess_live_readiness.py` has been saying all along, with
`paper_observation` blocking and `paper_days` at zero.

## What this does NOT license

It does not license assuming the warm numbers are the real ones. The warm runs are where the
+20.53% headline lives, and that headline is 78% unrealized on six round trips. If the warm pool's
advantage is partly lookahead, live will underperform it — and the only way to find out is to run
it forward.

## Practical rules that follow

1. **Compare levers cold.** That protocol is correct and stays.
2. **Never quote a cold absolute return as the strategy's expected performance.** It is a floor
   with discovery handicapped, not a forecast.
3. **Never quote a warm absolute return either.** It is contaminated by construction.
4. **Only forward paper/live returns are both representative and clean.** Everything else is a
   comparison instrument.
