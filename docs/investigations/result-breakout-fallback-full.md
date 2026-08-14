# Full-length stage 1: the mechanism fires, and it does not widen the funnel

bt 718107 run to completion (48,580 log lines, 43 fallback ticks).

## Correction to the previous document first

`result-breakout-fallback-stage1b.md` concluded **zero promotions, criterion 3 FAILS**, from a probe
killed at roughly ten minutes. That was wrong. The full run shows **16 buy-side promotion lines**:

```
Buy: URAA (No graph signal | breakout(5w_high(+0.30)=+0.30) | Base=+0.0),
     SLVP (No graph signal | breakout(5w_high(+0.30)=+0.30) | Base=+0.0), ...
```

A killed probe can produce a false negative exactly as a stopped run produces a meaningless return.
The earlier verdict is withdrawn.

## Stage 1 on the full run: PASS

| criterion | required | observed | |
|---|---|---|---|
| skip rate at `bars=0` | well under 100% | **10.5%** (baseline 100%) | pass |
| evaluations with bars | >= 1 | 203 of 227 per tick, 89% | pass |
| promotions to a buy | >= 1 | **16 lines** | pass |
| `FALLBACK ERROR` | 0 | 0 | pass |

The defect is repaired and the promotion path is alive for the first time in this project's history.

## And the endpoint that matters is unmoved

The preregistration named the funnel as the real endpoint: *"If the funnel does not widen, the lever
did not do the thing it was built to do, whatever the return says."*

| run | moved >=30% | buy intent | bought |
|---|---:|---:|---:|
| bt 523085 control | 57 | 17% | 5% |
| bt 102463 displacement | 53 | 20% | 1% |
| **bt 718107 fallback** | 53 | **18%** | 3% |

18% against 17-20% in the arms without it. **No widening.**

## Why, and it is not subtle

The names promoted are `URAA` and `SLVP` - both at `Base=+0.0`. The boost is `+0.30`, the threshold
is `0.3`, so promotion happens *only* from a base of exactly zero. The large movers this objective
cares about do not sit at zero: they carry negative bases from macro or graph signals (`EXAS -0.70`,
`AJG -0.50`), and `-0.70 + 0.30` is not a buy.

So the rescue promotes precisely the names nobody was asking about - neutral tickers printing a
5-week high - while the +87% mover with a bearish macro tag stays unbuyable.

## Verdict

**No P&L pair.** Stage 1 passes on mechanism and fails on the endpoint it was built to move. Running
a pair would spend two full backtests to measure a lever that demonstrably does not change which
large movers get bought, and any return difference would be the ~10pp shared-state dispersion.

`breakout_history_fallback_enabled` stays default-OFF and is not on doc 193. The code is worth
keeping: the history defect it fixes is real, and any future promotion work needs bars to exist
before it can matter.

## What this says about the objective

The objective's instruction is to ask why a name ranked #1 did not get bought. Three answers are now
measured, in order of size:

1. **86% of large movers are never scored** - no LLM sentiment, no graph path. Fixing their history
   makes them *evaluable* but not *promotable*, because the boost cannot overcome a negative base.
2. **A minority are scored and refused** - `SNDK` is proposed and filled 94.9% through its move;
   the entry-extension gate blocks 4 of 36.
3. **Sizing and holding are not the problem** - 14.00% median entry, 40-100% capture on names
   bought.

The next question is not "how do we promote more names" but "why does a +87% mover carry a negative
base at all". That is a scoring question, not a plumbing one, and it has not been investigated.
