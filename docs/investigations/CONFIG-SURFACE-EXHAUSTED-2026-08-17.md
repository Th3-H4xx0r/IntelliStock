# Four measurable A/Bs, four rejections: the config surface is exhausted

Written 2026-08-17, after the first night in this project's history where levers could actually be
measured. That is the point of this document: these are not more null results of the old kind.
They are **conclusive** null results, and they say something the old ones could not.

## What changed first

A cold-start A/A (bt 479057 vs bt 193668) returned **byte-identical** per-name P&L — MSFT $143.34,
NVDA $166.26, NVTS −$36.93, OIH $49.34, RIVN −$23.05, SPY $342.75, VDE −$37.68 in both arms, at
100% traded-name overlap. The ~10pp dispersion that made every prior lever unmeasurable was
carried state, and `clear-state` removes it. **Noise floor 10pp → 0.5pp.**

Against a 10pp floor, a real 3pp lever is invisible. That is the mechanical reason ~60 configs
across nine approaches came back null: not that nothing worked, but that nothing COULD be seen to
work. So these four results carry weight the old ones did not.

## The four

| # | lever | change | endpoint 1 | verdict |
|---|---|---|---|---|
| 1 | chop stand-down | `chop.core_target_pct` 0.35→0.85 | turnover **ROSE** 303→361% | **disqualified** (return +1.17pp discarded) |
| 2 | core rebalance band | 0.05→0.12 | turnover **inert** 303→302% | **inert** (return +0.98pp discarded) |
| 3 | core funding reserve | 4→60 | core notional **inert** $8,040→$7,971 | **inert** (100% overlap, 0.01pp) |
| 4 | bear-leg cash target | `release_cash_pct` 0.15→0.02 | **SPLIT**: $ −92%, count +65% | **not adopted** (hedge capture fell) |

Two of these produced a *positive return* and were still rejected, because the preregistered
mechanism did not do what it claimed. That discipline is the reason the fifth result, when one
comes, will be believable.

## What each null actually taught

Every rejection narrowed the search, which is why this is progress rather than four wasted runs:

1. **Chop → turnover is the leak.** Turnover is **303-361% of NAV over six weeks** against a ~75%
   break-even for that span — 4-5x. Not a subtlety; a structural cost.
2. **Band → core churn is funding-driven, not drift-driven.** `core_rebalance_band_pct` gates only
   the rebalance path; the suppressed trade instantly reappeared as a `funding` release. This is
   why every band-shaped fix has failed to move the core's ~2pp drag.
3. **Funding reserve → the recycle does not occur here.** Traced the chronology: every release was
   consumed by the buy it was raised for ($630 → CVLT $431; $1,233 → CSCO $616 + GCMG $585). The
   `$11,474 / $7,104 recycled` figure in `core_sleeve.py:442` is from older runs on a different
   config and does not reproduce.
4. **Bear refill → the hedge's problem is regime DETECTION, not sleeve mechanics.** Removing 92% of
   the hedge liquidation did NOT restore capture (0.93% → 0.41%). Window f had **3 bear bars**;
   window c had **19** and captured 77% of a +16.98% move. The leg is being armed on isolated bars
   where it cannot work.

## The conclusion I have to state plainly

**The remaining gap is not reachable by configuration.** Four levers, chosen from measured
evidence rather than guesswork, tested on a now-trustworthy instrument, moved nothing that
mattered. The surface that ~60 prior configs explored is genuinely exhausted, and now it is
exhausted *with proof* instead of with ambiguity.

What is left is not tuning. It is:

* **Entry timing** — 3 of 11 names in bt 333727 were bought ABOVE the price at which the window
  ended. Not a flag; a change to when the system acts.
* **Regime detection** — 3 bear bars vs 19 decides whether the one validated edge (the SQQQ leg)
  works at all. Not a flag; a change to how the tape is classified.
* **Holding at size** — the objective's actual mechanism, blocked by a book that cannot hold more
  than ~6 names and an entry that arrives late.

Each is a code change with a real design question behind it, and each now has a measurement
apparatus that can adjudicate it — which is the thing this project has never had.

## Where that leaves real money

Unchanged, and I will not dress it up. Best available configuration:

| window | regime | strategy | SPY | vs SPY |
|---|---|---:|---:|---:|
| d | bull | +20.53% | +16.66% | **+3.87pp** ✓ |
| c | chop→bear | +0.46% | −5.29% | **+5.75pp** ✓ |
| a | mild bull | +8.19% ⚠ stopped | +3.44% | +4.75pp ⚠ |
| f | chop | **−2.70%** | +0.69% | **−3.39pp** ✗ |

Beats SPY in the regimes where it has an edge, loses in flat tape, and averages far below the
objective's +12%-per-two-month bar. `assess_live_readiness.py` still returns **0/6**, with
`paper_days` at zero and blocking.

**The honest recommendation is unchanged: paper first.** It is the only uncontaminated evidence
generator available, it costs nothing, and it is the gate the system itself demands.
