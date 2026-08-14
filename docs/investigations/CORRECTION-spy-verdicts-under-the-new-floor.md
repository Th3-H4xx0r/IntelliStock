# Correction: "beats SPY in 2 of 4 windows" does not survive the corrected floor

I repeated "2 of 4" all session. It was computed against the **4.94pp** floor. Re-reading the same
numbers against the **~10pp** same-config dispersion measured today changes the answer, and one
further check changes it again.

## Step 1 — the same table, corrected floor

| window | strategy | SPY | vs SPY | old verdict | corrected |
|---|---:|---:|---:|---|---|
| W0 ref (bt 873929) | +16.41% | +0.41% | +16.00pp | beat | beat |
| W1 OOS bull | +11.98% | +8.54% | +3.44pp | noise | noise |
| W2 bear | +24.36% | −1.77% | +26.13pp | beat | beat, **unreliable** (25 SPY samples) |
| W3 non-semi | −12.86% | −2.72% | −10.14pp | loses | loses, marginally |

## Step 2 — the W0 "beat" is the AGQ run

bt 873929 is the run whose P&L was **113% `AGQ`**, a name absent from its own matched control. The
reproducible W0 result on the same instance and config is bt 523085 at **+6.00%**. Against the same
window's SPY of +0.41%, that is **+5.59pp — inside the ~10pp floor, i.e. noise.**

So the only window that cleared the corrected bar did so on a lottery draw that does not reproduce.

## Honest scoreboard

| | count |
|---|---|
| windows with a **clear, reproducible** beat vs SPY | **0** |
| noise | 2 (W0 reproducible, W1) |
| beat but unreliable benchmark | 1 (W2, 25 SPY samples) |
| loses | 1 (W3, −10.14pp) |

**"Beats SPY in 2 of 4" is withdrawn.** The defensible statement is: on four windows, one loss, two
results inside the noise band, and one apparent bear-regime win whose benchmark rests on 25 price
samples and cannot be trusted.

## A measurement gap this exposes

SPY is benchmarked from each run's own monitor price stream, and the sample counts are far too low
to rely on — `spy_series` returns **4 samples** for bt 523085 and bt 102463, and 25 for the bear
window. A benchmark computed from 4 points is not a benchmark. Any future claim about beating SPY
needs a benchmark series built independently of the run's monitor cadence, or it will keep producing
confident numbers with nothing behind them.

## Why this matters more than the individual verdicts

Every favourable result this project has reported was scored against a floor roughly half the true
dispersion, using a benchmark with single-digit sample counts. That combination manufactures
confident conclusions in both directions. The corrected position is not that the strategy is bad —
it is that **the evidence collected so far cannot tell**, and the fix is repetition and a real
benchmark, not another lever.


## A better SPY benchmark, and what it changes

`spy_series` returns **4 samples** for bt 523085 — unusable. But SPY is *traded* in these runs, so
its fills carry dated prices. Extracting those gives 16, 6 and 17 points respectively, and the
17-point series from bt 718107 spans 2026-01-02 to 02-26, most of the window:

**Window SPY = +0.45%.**

| run | strategy | SPY | vs SPY | verdict @ ~10pp |
|---|---:|---:|---:|---|
| ctl 523085 | +6.00% | +0.45% | +5.55pp | **noise** |
| trt 102463 (displacement) | +11.12% | +0.45% | **+10.67pp** | beat, **marginal** |

Three caveats that must travel with those numbers:

1. SPY coverage differs per run (16, 6, 17 points), because it depends on when the core lane traded.
   Comparing a full-window strategy return against a partial SPY window is the same error as reading
   a stopped run's P&L, so only the 17-point series is used above.
2. The ~10pp floor is itself estimated from **one** same-config comparison. A floor with n=1 cannot
   sharply adjudicate a result 0.67pp beyond it.
3. One window, one pair. The objective requires >=3 windows including one OOS and one
   non-semiconductor.

So the honest statement is narrower than either of my earlier ones: **the displacement arm is the
only result in this session that clears the corrected floor at all, and it clears it by 0.67pp on a
single window with a benchmark built from 17 points.** That is a reason to run it again, not a
result.

## Method note for next time

Benchmark SPY from **fills**, not from the monitor stream — 17 dated points against 4. Better still,
fetch an independent SPY series for the window so the benchmark does not depend on whether the
strategy happened to trade the core lane. Every SPY comparison this project has published rests on
the weaker source.


## Retroactive re-benchmark of every run with an available log

Using SPY fills and reporting the span, because a truncated SPY series cannot be differenced against
a full-window strategy return:

| window | bt | SPY pts | SPY span | window ends | SPY | vs SPY | usable? |
|---|---|---:|---|---|---:|---:|---|
| W0 ref | 873929 | 11 | 01-02..**02-10** | 03-01 | +1.59% | +14.82pp | **no** — span short, and this is the AGQ run |
| W1 OOS bull | 647755 | 6 | 04-06..**04-13** | ~06-01 | +3.03% | +8.95pp | **no** — 7 days of SPY |
| W2 bear | 624674 | 4 | 03-10..**03-19** | 03-30 | −3.40% | +27.76pp | **no** — 4 points, 9 days |
| W3 non-semi | 553341 | 9 | 06-01..**06-29** | 07-01 | −2.72% | **−10.14pp** | **yes** — span covers the window |
| W0 ctl | 523085 | 16 | 01-02..**02-09** | 03-01 | +1.42% | +4.58pp | partial |
| W0 fallback | 718107 | 17 | 01-02..**02-26** | 03-01 | +0.45% | n/a | best coverage |

**Exactly one comparison in the project has a SPY series that covers its window: W3, and it is a
loss of 10.14pp.**

Every other SPY figure this project has published — including the +26.13pp bear-regime "win" that
made blocker (5) look resolved — differences a full-window strategy return against a SPY series
spanning 7 to 39 days of it. The bear window's benchmark rests on **four price points over nine
days**.

That does not make those results wrong. It makes them unmeasured. The correct statement about
"beat SPY in every regime" is:

> One window is properly benchmarked and the strategy loses it. The rest are not benchmarked at all.

## What would fix this permanently

`scripts/spy_benchmark.py` refuses series under three points and prints the span, so the error
cannot silently recur. But the real fix is an **independent SPY series** for each window, fetched
rather than inferred from whether the core lane happened to trade. Until that exists, no regime
claim should be made in either direction.
