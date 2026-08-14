# Stage 1, second probe: the history defect is fixed, the promotion still does not happen

bt 718107, doc 195, fallback on, diagnostics on, killed early. The observability added after the
first probe answered the question immediately.

## The fallback works

```
BREAKOUT FALLBACK: cache=524 symbols, requested=180, supplied=137
BREAKOUT FALLBACK: cache=848 symbols, requested=205, supplied=181
```

The `_overlay_bars_raw` cache **is** reachable from `_finalize_scores` - my stated hypothesis that
it might be a different object was wrong - and it supplies bars for most requested symbols.

| | baseline bt 278531 | with fallback bt 718107 |
|---|---:|---:|
| evaluations skipping at `bars=0` | **100%** (7,156 of 7,156) | **12.2%** (25 of ~205 per tick) |

The disjointness defect is repaired. Names that could never be evaluated now are:
`EXAS (No graph signal | breakout(5w_high(+0.30)=+0.30) | Base=-0.7)`.

## And it still does not promote anything

Preregistered criterion 3 was **at least one breakout promotion**. Result:

| criterion | required | observed | |
|---|---|---|---|
| skip rate at `bars=0` | well under 100% | 12.2% | **pass** |
| evaluations with `bars>0` | >= 1 | ~180 per tick | **pass** |
| **promotions to a buy** | **>= 1** | **0** | **FAIL** |
| `BREAKOUT FALLBACK ERROR` | 0 | 0 | pass |

Zero lines put a `breakout(...)` reason inside a `Buy:` group. Every one lands in `Sell:`.

**Why, exactly.** The boost is added to the existing raw score:

```
_new_raw = _existing_raw + _bk_boost
if fresh_score <= 0 and _new_raw >= buy_thresh:  fresh_score = 1
```

A single 5-week-high scores `+0.30` and `buy_threshold` is `0.3`, so the boost clears the bar only
from a base of exactly 0.0. The observed candidates carry negative bases - `EXAS` at `Base=-0.7`,
`AJG` at `Base=-0.5` - because a macro or graph signal already marked them down. `-0.7 + 0.30` is
`-0.4`, nowhere near `+0.3`.

So the mechanism the objective needs - promote a mover on price action when there is no news - is
calibrated so that it can only ever promote a name whose other signals are *precisely* neutral.

## Verdict, by the rule written before the run

**Stage 1 does not pass, so no P&L pair is authorised.** Three of four criteria pass and the one
that matters does not. Running a pair now would measure a lever that has never once changed a
decision, and any return difference would be shared-state noise misread as a result.

## What is now known that was not this morning

* The history defect was real, is understood, and is fixed by a default-OFF change.
* Fixing it is **necessary but not sufficient**. The next constraint is the boost calibration
  against `buy_threshold`, on candidates that are not neutral.
* This also lands the granularity issue recorded in `breakout-window-granularity.md`: the overlay
  cache holds **1Day** bars, so `closes[-25:]` is a genuine 5-week window for the first time,
  rather than 25 hourly bars (~3.5 trading days).

## Not proposed

Raising the boost or lowering `buy_threshold` is exactly the kind of knob-turn that would be fitting
to this one window, and the objective forbids it. Any change here needs its own preregistration and
must be judged on the funnel - the share of >=30% movers receiving a buy intent, currently 18-25%
across four runs - not on a single window's return.

`breakout_history_fallback_enabled` remains default-OFF and is not enabled on doc 193.
