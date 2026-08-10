# scorecard — 21 finished runs against the benchmark, and where the alpha actually comes from

`python3 scripts/scorecard.py` — joins every finished `v2-let-run-core` backtest to the SPY return
of its own window (cached bars, no runs) and marks it against both bars in the objective:
**beat SPY**, and **1x pace** (+12%/2mo, pro-rated).

```
  19/21 beat SPY     9/21 at or above 1x pace     mean alpha +8.66pp
```

## THE PATTERN THAT MATTERS

| window | SPY | runs | strategy range | verdict |
|---|---|---|---|---|
| reference bull/chop 01-01..03-01 | **+0.24%** | 14 | +1.72% .. +17.36% | 14/14 beat SPY, 4/14 at 1x |
| bear 03-02..03-30 | **-7.86%** | 3 | +10.44% .. +18.71% | **3/3 beat SPY by 18-27pp, 3/3 at 1x** |
| OOS bull 03-30..04-27 | **+13.10%** | 3 | +4.75% .. +13.35% | **0/3 beat SPY** |
| non-semi 06-01..07-01 | **-1.71%** | 1 | +3.09% | beat SPY by 4.8pp, below 1x |

**Every window where the strategy wins big is a window where SPY is flat or falling.** The one
window where SPY runs hard (+13.10%), the strategy has now been run three times — with the hedge
misfiring (+4.75%), with it costing -$514 (+13.35%), and with it suppressed entirely (+12.34%) —
and has **never beaten the index**.

That is not a hedge problem; the third run had no hedge at all. It is a **participation problem**:
in a strong bull the book does not keep up with a fully-invested index.

## WHY THIS REFRAMES THE REMAINING WORK

The objective wants both "1x-3x a year" and "beat SPY in every regime". The evidence says those
two clauses are currently failing in *different* windows:

* the reference and non-semi windows produce **alpha but not pace** (SPY is flat, so beating it is
  easy; +3.09% and +9.70% median are well under the +11.6% / +5.9% bars);
* the OOS window produces **pace but not alpha** (+12.34% clears its +5.5% bar and still loses to
  a +13.10% index).

A lever that raises returns across the board fixes both. A lever that only avoids losses fixes
neither — which is exactly what bt 584712 measured for the fresh-low gate: drawdown halved, return
unchanged inside noise.

`sndk-100-dollars.md` is the one candidate on the table that raises the top end rather than
trimming the bottom: the same names, held bigger.

## CAVEAT THAT LIMITS ALL OF IT

The 14 reference-window runs do **not** share a config — the whole of 2026-08-09 was spent tuning
on that window, so the +1.72%..+17.36% spread is config *and* noise. Two runs that DO share a
config, 571147 and 676939, still differ by **2.71pp** with 0/18 held-name overlap. Nothing below
~5pp on a single window is evidence.
