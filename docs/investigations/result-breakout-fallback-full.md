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

## Why the funnel did not widen: the fallback helps a different set of symbols

| | count |
|---|---:|
| symbols ever receiving a breakout boost | 18 |
| of those, names that moved >=30% | **3** |
| movers >=30% evaluated with bars | **0 of 53** |
| movers >=30% still skipping at `bars=0` | **53 of 53** |

The 18 symbols the fallback rescued are `AGMI, AJG, COPA, COPP, DFEN, EXAS, GBUG, ILIT, LIMI,
MSTZ, MU, PILL, SELX, SLJY, SLVP, URAA, URNJ, YXI` - overwhelmingly leveraged and thematic ETFs.
**Every single one of the 53 large movers is still `bars=0`.**

So `_overlay_bars_raw` is populated for the momentum-scan universe, not for the names discovery
appends during the tick. The newly discovered mover has bars in **neither** source at the moment it
is scored:

* not in the broker `price_history` map - built from `symbols_for_data` before `run_once`;
* not in `strategy_cache["_overlay_bars_raw"]` - populated for a different universe.

That is why the skip rate fell from 100% to 10.5% while the funnel stayed at 18%. The rate improved
for symbols nobody was asking about.

### The claim this corrects

Earlier in this document: *"the same bars are already in scope - momentum discovery reads
`_overlay_bars_raw` to compute its 20d/60d returns"*. True for the symbols discovery scanned;
**false for the symbols it discovers**. `AAOI` is announced as
`Discovered stock (momentum): AAOI (20d=+33.9%, 60d=+3.1%)` using bars held transiently during the
scan, and those bars are not what `_finalize_scores` can see afterwards.

## The actual requirement, stated precisely

For a discovered mover to be promotable on price action in the same tick it is discovered, its bars
must be reachable at scoring time. Neither existing source provides that. The options are
architectural, not parametric:

1. fetch history for discovered symbols **before** the scoring call rather than after it;
2. persist the scan's bars into `_overlay_bars_raw` at discovery time, so the name it just
   discovered is covered;
3. accept a one-tick delay and score discovered names on the following bar, once
   `symbols_for_data` has expanded to include them.

Option 3 is the smallest change and costs one hourly bar of entry timing - which matters, since
blocker (1) is that winners are already entered late. Option 2 is the most direct. Neither has been
tried, and given that four hypotheses in this document have been falsified by measurement, neither
should be assumed to work.

**No further change is being shipped on this today.** The evidence supports the diagnosis; it does
not yet support a repair.

## CORRECTION: the previous section is wrong — the movers ARE evaluated

The section above claims *"every single one of the 53 large movers is still `bars=0`"*. That is
false, and the error was mine: `BREAKOUT SKIP` lines carry an **empty reason string** when the
scorer runs to completion and simply finds no breakout pattern, and I treated every skip line as a
`bars=0` skip.

Splitting the 7,340 skip lines by whether a reason is present:

| | lines | symbols |
|---|---:|---:|
| `skip:bars=0<25` — no history | 1,026 | 423 |
| **empty reason — evaluated WITH bars, no pattern found** | **6,314** | **536** |

And for the 53 large movers specifically:

| | count |
|---|---:|
| ever skipped for `bars=0` | **10** |
| **ever evaluated with bars** | **53 of 53** |

| mover | move | `bars=0` skips | evaluated with bars |
|---|---:|---:|---:|
| SNDK | +187.9% | 0 | 20 |
| LITE | +105.1% | 0 | 20 |
| DTSS | +95.7% | 0 | 27 |
| AAOI | +87.7% | 0 | 26 |
| MRNA | +81.8% | 0 | 25 |
| VICR | +81.3% | 0 | 20 |

**The fallback works, and it works on the names that matter.** Every large mover is now scored
against its price history, dozens of times each, where before not one ever was.

## So the blocker has moved, and this is the honest new question

`AAOI` is up 87.7% and is evaluated 26 times with bars, and the breakout scorer finds **no 52-week
high, no 5-week high, no volume surge and no gap-up** on any of them. For a name that nearly
doubled, that is not credible on its face. Two candidates, neither tested:

1. **The bars are wrong for the purpose.** `_overlay_bars_raw` holds `1Day` bars and
   `_visible_overlay_bars` truncates to what is causally visible; if the visible tail is short or
   stale, `closes[-25:]` and `closes[-252:]` describe a window that does not include the move.
2. **The thresholds do not match the data.** The gap-up test wants 5.0% against the previous close
   and the volume test wants 2.0x a 20-bar average - plausible on daily bars, but they have never
   been checked against what these series actually contain.

Distinguishing them costs one instrumented run that logs, for a named mover, the number of visible
bars and the actual 5w/52w/volume/gap values computed. That is the next step, and it is a
measurement, not a change.

## Standing correction count

This document now contains five falsified hypotheses of mine and two corrected factual claims. The
measurements have survived every check; my explanations of them have not. The practical consequence
is unchanged: nothing has been enabled on doc 193, and no return claim rests on any of this.
