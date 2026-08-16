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

## RESULT — bt 826225 vs bt 333727

**First, a correction to this document's own baseline.** The "14 calendar days" above was
computed from a metric that only saw names passing two momentum-specific log lines. Run against
the treatment it produced **negative lags** (a name bought before it was "first seen"), which is
impossible and exposed the bug. Replaced with a lane-agnostic definition — the first bar on
which the broker evaluated the symbol at all (`SYM @ DATE ($price)`), which news, graph and
momentum lanes all print — and applied identically to both arms. **On the corrected metric the
control median is 3.0 days, not 14.** The preregistered baseline was wrong, and it was wrong in
the direction that would have flattered the treatment.

### Endpoint 1 — did it execute? **PASS.**

```
Breakout freshness: fresh=14 stale=19 unmeasurable=0 (band<=5.0%, lookback=20 bars) — order CHANGED
...  44 evaluations, 44 of 44 "order CHANGED", unmeasurable=0 throughout
```

`unmeasurable=0` is the important number: the bars map is populated at ranking time, so this is
**not** the `breakout-is-structurally-dead.md` failure where a breakout mechanism reached its
arithmetic 2,922 times and exited at `bars=0` every time. The tie-break is live and it reorders.
Hit rate is 10-40% of candidates, tighter than the 62% the offline test reported, as the 5% band
intended.

### Endpoint 2 — did median lag fall? **NOT MET.**

| | control 333727 | treatment 826225 |
|---|---:|---:|
| n | 11 | 8 |
| **median lag** | **3.0 d** | **3.0 d** |
| mean lag | 9.2 d | 2.6 d |
| max lag | **35 d** | **6 d** |
| names with lag >= 12 d | **5** | **0** |

**The preregistered primary endpoint did not move.** Median is identical. Reporting this as a
win by switching to the mean would be choosing the statistic after seeing the answer.

What did change is the TAIL: the control's long-lag entries were AIOS 12d, MXL 12d, AAOI 14d,
AEHR 23d, AXTI 35d — and **AAOI, AEHR and AXTI are exactly the three names it lost money on**.
The treatment has no entry beyond 6 days. That is the predicted direction, and it is recorded as
a **post-hoc secondary observation, not a preregistered result.**

**And it is contaminated.** The arms share **11% of their traded names** — lag is a per-name
property, so a treatment trading almost entirely different names has a different lag
distribution for that reason alone. Lag is less draw-sensitive than return; it is not
draw-independent. I cannot attribute the tail collapse to the lever.

### Endpoint 3 — turnover. **Favourable, also contaminated.**

Total trades **20 -> 15**. A decrease, so not disqualifying, and it points the way the
objective's turnover constraint wants. Same 11%-overlap caveat.

### Return — NOT quoted as a result

+26.81% against the control's +20.53%. `check_pair_validity` scores the pair **VOID at 11%
overlap**, so that +6.28pp measures the draw. It is written here only so nobody later finds the
number and mistakes it for a finding.

### The one clean claim

Stripping everything the overlap contaminates, exactly one statement survives:
**the tie-break executes and changes the candidate ordering on every evaluation, and it is not
inert.** Everything else this run touched is directional evidence at 11% overlap.

Worth noting separately: `SNDK` — the name the OBJECTIVE singles out as emitting 13 buy signals
that were all refused — was bought in the treatment on **day 2** (considered 04-01, bought
04-02, lag 1 day).

### Disposition

`momentum_rank_on_60d=true` and `momentum_breakout_freshness_pct=5.0` stay armed on doc 195: the
lever is proven live, turnover moved the right way, no endpoint moved against it, and the
alternative it replaced (`max(20d,60d)`) measures IC = −0.003. It is **not** claimed to improve
returns, and the primary endpoint did not move.

## What I will not claim

- Not that a lag reduction is a return improvement. It is a necessary condition the objective
  names, not a sufficient one.
- Not a causal return result from this run, at any delta.
- Not that a single window generalises.
