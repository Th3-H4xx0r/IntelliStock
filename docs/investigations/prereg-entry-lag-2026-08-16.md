# Preregistration: entry lag is the endpoint, not return

Written BEFORE the config was changed or the run launched. Session 2026-08-16.

## The defect, measured without lookahead

In **bt 333727** the median lag from a name's FIRST appearance in discovery to its actual BUY
is **14 calendar days**:

| name | first seen | bought | lag | captured |
|---|---|---|---:|---:|
| AEHR | 2026-04-01 | 2026-04-24 | **23 d** | −10.93% |
| AXTI | 2026-04-07 | 2026-05-13 | **36 d** | −11.94% |
| AAOI | 2026-04-06 | 2026-04-20 | 14 d | −12.33% |
| MXL | 2026-04-15 | 2026-04-28 | 13 d | +73.66% |
| AIOS | 2026-05-06 | 2026-05-19 | 13 d | +1.86% |

This is the objective's *"another 19 days after the gap that was its whole +54.7%"*, reproduced
on current code. **Lag is measured from the run's own log and needs no future prices**, unlike
the "% through the move" statistic, which uses the window's END price and is therefore
lookahead-contaminated and cannot be used as a signal.

The code already names the cause (`graph_nexus_analysis.py:14379`):

> "Ranking on accumulated trailing return is structurally a late-entry machine: a name
> qualifies only AFTER it has moved... r = −0.895 (p < 0.0001) with perfect separation — every
> position filled at <=55% elapsed made money, every one at >100% lost."

## The change

| key | from | to |
|---|---|---|
| `momentum_rank_on_60d` | `False` | `True` |
| `momentum_breakout_freshness_pct` | `0` (inert) | `5.0` |
| `momentum_breakout_lookback_bars` | 20 | 20 (unchanged) |

Two keys move together and that is **unavoidable, not sloppy**: `_fresh` only executes inside
the `momentum_rank_on_60d` branch, so the tie-break cannot be armed without the ranking key.
Said here rather than discovered later.

**What the evidence for each actually is, stated honestly:**

* `momentum_rank_on_60d` is recorded as **UNPROVEN** in `fix-generalize.md` §4.2 — IC(60d)=+0.201
  is positive in exactly ONE run and negative in three. It is armed anyway because the CURRENT
  default is worse: `max(20d,60d)` measured **IC = −0.003**, and `fix-generalize.md:247` says in
  terms *"do not revert to max(20d, 60d) on this"*. This is the better of two poor keys, not a
  good one.
* The freshness tie-break's "4/5 windows, 5/5 on big movers" result is **OFFLINE**.
  `next-conversion-experiment-priority.md:57` records it as having "no real-run causal exposure"
  and firing on 62% of the universe. The code comment reads stronger than the evidence; the docs
  win.
* `momentum_breakout_freshness_pct = 5.0` is **my choice and is unvalidated**. It means "price is
  within 5% above its prior 20-bar high", i.e. a name that just cleared its base. Tighter than
  whatever produced the 62% hit rate, because a tie-break that fires on most of the universe is
  not a tie-break.

## The endpoint — decided in advance

**Return is NOT the endpoint and will not be quoted as one.** Two runs of this document one flag
apart share ~20% of their traded names, and the instance carries 4,213 rows of state between
runs (`attest_arm_start.py`). A return delta here measures the draw.

**The endpoint is the mechanism, in this order:**

1. **Did it execute?** The log must show `Breakout freshness: fresh=… stale=… unmeasurable=…`
   with **order CHANGED**. If `unmeasurable` dominates, the bars map is empty at ranking time and
   the lever is inert — the exact failure of `breakout-is-structurally-dead.md`, where a breakout
   mechanism reached its arithmetic 2,922 times and exited at `bars=0` every time. Report inert
   and stop.
2. **Did lag fall?** Median discovery→buy lag against the **14-day** baseline above. This is the
   preregistered primary endpoint. It is deterministic per run and far less draw-sensitive than
   return, because it asks *how long a name waited*, not *which names appeared*.
3. **Did turnover rise?** A turnover increase is disqualifying, per the objective's standing
   constraint.

## What I will not claim

- Not that a lag reduction is a return improvement. It is a necessary condition the objective
  names, not a sufficient one.
- Not a causal return result from this run, at any delta.
- Not that a single window generalises.
