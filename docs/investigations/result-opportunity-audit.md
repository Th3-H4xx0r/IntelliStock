# Cadence vs latency: the qualifying moments exist and are not being looked at

bt 413617 (corrected audit, killed early — 12 ticks). Read with the caveat that the fallback flag
was OFF for this run, so `nomap` reflects the unrepaired history map.

## Result

| quantity | value |
|---|---|
| qualifying symbols per tick | 34 → 390 (rising as the cache fills) |
| distinct qualifying symbols | 145 |
| **entries flagged `nomap`** | **465 of 465 — 100%** |
| **movers ≥30% that qualify at some point** | **9 of 45** |

The nine: `AEHR`, `AEIS`, `AGMI`, `AMAT`, `ATI`, `AXTI`, `CCJ`, `CIEN`, `ONDS`.

56% sit at exactly +0.00% — at their high — with a median window range of **16%**, so these are
genuine 25-day highs and not the flat-series artifact the first version of this instrument produced.

## What it answers

The two surviving explanations were **latency** (the qualifying moment never arrives because
discovery fires after the run) and **cadence** (the moment arrives on a day the scorer does not look
at that name).

**The moments arrive.** Nine of the large movers print a 25-day high with real range during the
window. Latency alone does not explain the near-zero promotions.

**And nothing that qualifies is visible to the scorer.** Every one of the 465 qualifying entries is
`nomap` — absent from the history map `_finalize_scores` passes to the promotion test. Without the
fallback the scorer cannot evaluate a single qualifying symbol.

## How this fits with the fallback result, which looked contradictory

With `breakout_history_fallback_enabled` on (bt 718107, 778288) the movers *were* evaluated — `AAOI`
18 times — and still never came within 1%. That looked like the moments not existing. This audit
shows they do exist. The reconciliation is coverage: the scorer evaluates a mover on a median **43%**
of days, so a qualifying day can simply be a day it did not run for that name.

So the requirement is **both**: the history must be reachable (the fallback) *and* the name must be
evaluated on the day it qualifies. Neither alone is sufficient, which is exactly why the fallback
fixed evaluability without widening the funnel.

## What is still not established

* This probe is 12 ticks. The per-tick counts were still climbing when it was killed.
* It ran with the fallback OFF, so `nomap` overstates what the scorer would miss with it on.
* Whether a mover's qualifying day coincides with a day the scorer skipped it is **implied** here,
  not directly measured. The direct test is to run the audit and the fallback together and check,
  per mover, whether a `BREAKOUT OPPORTUNITY` day lacks a matching `BREAKOUT NOPATTERN` or promotion
  line. That is one run and has not been done.

## What must not be concluded

That the promotion cadence should simply be increased. Evaluating every name every tick has a cost
in candidates and therefore turnover — the objective's known leak at ~290%/mo against ~50%
break-even — and the first version of this very instrument produced a confident, entirely false
answer. The next step is the direct test above, not a change.
