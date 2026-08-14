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
