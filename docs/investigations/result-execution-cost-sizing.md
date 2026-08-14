# Blocker (4), execution cost, right-sized

`simulated_execution.py:139-144` states the case: every fill crosses the spread by construction, the
measured half-spread is **22.8 bps**, slippage against mid is ~0.1 bps, and only a resting order
avoids the crossing. The scaffolding for passive execution already exists - `limit_price` and
`expire_after_quotes` on `SimulationOrder`.

## What it actually costs in the runs measured today

| run | traded notional | fills | half-spread at 22.8 bps | share of P&L |
|---|---:|---:|---:|---:|
| ctl 523085 | $24,478 | 32 | $55.81 | **15%** |
| trt 102463 (displacement) | $13,898 | 16 | $31.69 | **5%** |
| 718107 (fallback) | $27,625 | 36 | $62.99 | n/a |

## Reading

**It is real but secondary.** Recovering the entire crossing cost adds roughly $56 to a $360 result
or $32 to a $667 one. That is 5-15% of P&L - worth having, and nowhere near the gap between +6% and
the objective's +12% two-month bar, let alone +25%.

**Turnover reduction already captures half of it.** The displacement arm trades $13,898 against the
control's $24,478, so its crossing cost is 43% lower without touching execution at all. The two
levers are not independent: fewer, larger, longer-held positions is both the objective's stated
preference and a direct discount on this cost.

**The stated risk is asymmetric.** The source comment is explicit that an unfilled limit "is a
position you wanted and did not get". Given the finding in
`result-breakout-nopattern.md` - that names are already entered late, with the median mover 19 days
from +10% to peak and discovery consuming +24.2% of it - resting orders would push entries later
still. Saving 22.8 bps while missing entries on a book whose returns are dominated by a handful of
large movers is a poor trade, and it would be invisible in a return comparison that sits inside the
~10pp dispersion.

## Recommendation

Not the next lever. Ranked by measured size, the remaining gaps are:

1. **Discovery latency** - names become candidates +24.2% into a move that runs a median 19 more
   days. This gates whether the objective's arithmetic is reachable at all.
2. **Turnover** - the known leak, and the one thing measured to improve today (32 -> 16 trades),
   which also discounts execution cost as a side effect.
3. **Execution cost** - 5-15% of P&L, with a real risk of worsening entry timing, which is already
   the binding problem.

Passive execution should be revisited only once entry timing is not the constraint. Nothing has been
changed in `simulated_execution.py`.
