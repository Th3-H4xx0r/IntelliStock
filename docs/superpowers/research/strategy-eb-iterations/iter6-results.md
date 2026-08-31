# iter6 — vol-targeted TQQQ core × trend filter driving BOTH damp and a state-dependent book

## Primary objective, stated before results

On 2021-11-01→2026-08-27, rolling 252-session windows: EB > SPY-TR in **≥95%**
of windows **AND** all three bear windows absolute **≥0** **AND** cycle margin
**≥+100pp**. SPY-TR = yfinance SPY `auto_adjust` (no 1.25% accrual added).

## Pass count: **0 of 64,800.**

The grid is the full product of the specified axes — tv{.15,.20,.25} ×
(fast,slow){(10,40),(20,60)} × wmax{.45,.65} × ref{SPY,QQQ} × N{80,100,150} ×
hyst{(1,2),(0,1)} × damp{0,.5} = 288 core settings × 15 ON books × 15 OFF books.
That is 64,800, not the ~5–10k the brief estimated; it ran in 258 s, so it ran
whole with no staging. `reference_symbol` is a single shipped field driving both
the rv sizing and the trend SMA, so `ref` moves both — that is what config can
express.

**Validation.** At tv=0 with ON==OFF the simulator reproduces iter5/core5 to
**0.0000pp** on all 19 windows for three books; P1 replayed here reproduces
iter5's P1 to **0.0000pp**. The wider column set and the two-book code path are
a strict superset, not a new model.

**Parent controls vs the engine** (replay − engine):

| parent | wins | bears (rb1/rb2/rb3) | roll-12m | cycle |
|---|---|---|---|---|
| P1 replay | 17/19 | −10.33 / +2.19 / +1.76 | 94.9% | +250.7 |
| P1 engine | 15/19 | −13.10 / −6.10 / +7.70 | 91.5% | +238.5 |
| **delta** | **+2** | **+2.8 / +8.3 / −5.9pp** | **+3.4pp** | **+12.2pp** |
| R1w replay | 14/19 | −2.92 / +4.47 / +0.91 | 79.9% | +206.9 |
| R1w engine | 16/19 | −2.20 / +0.40 / +0.20 | 87.1% | +220.0 |
| **delta** | **−2** | **−0.7 / +4.1 / +0.7pp** | **−7.2pp** | **−13.1pp** |

Two things follow. The ±2-window / ~1.5pp error bar holds on wins, but **rb2
(2026 Feb–Apr, a 40-session window) carries +4 to +8pp of replay error**, and
**rolling-12m carries ±7pp**. Any bear margin under ~5pp and any rolling-12m
figure inside ±7pp of a threshold is not a measurement.

## Which sub-goal binds

| filter | n |
|---|---|
| cycle margin ≥ +100pp | 24,787 |
| rolling-252 win ≥ 95% | 1,432 |
| all three bears ≥ 0 | **28** |
| roll ≥95% **and** cycle ≥100pp | 1,207 |
| 3 bears **and** cycle ≥100pp | 10 |
| **roll ≥95% and 3 bears** | **0** |

Cycle margin is free. The binding constraint is the **conjunction of rolling-win
and bear-safety**, and it is a clean trade-off, not a near miss:

| constraint | best of the other |
|---|---|
| min-bear ≥ 0 (n=28) | max roll-252 = **79.2%** |
| min-bear ≥ −1% (n=104) | max roll = 85.9% |
| min-bear ≥ −2% (n=278) | max roll = 95.4% |
| roll ≥ 90% (n=7,455) | best min-bear = −1.07% |
| roll ≥ 95% (n=1,432) | best min-bear = **−1.98%** |
| roll ≥ 99% (n=50) | best min-bear = −2.24% |

**The frontier is 15.8pp of rolling-win short of the target at min-bear = 0.**
The dial is core exposure, exactly as in iter5. The core=0 control on the same
books and filter gives bears **+5.74/+3.88/+6.69** and roll-252 of only **54.3%**;
the filter-off control gives roll **86.3%**, cycle +145.7pp and bears
**−18.25/−1.06/+1.65**. One dial, two ends.

## What iter6 did unlock

**28 configurations reach 3/3 non-negative bears — the first in this program.**
iter5 had 0 of 13,536; iter4's bear-safe family all had core = 0. Attribution is
unambiguous:

- **All 28 have ON ≠ OFF.** Of the 1,440 frozen-book (iter5-style) rows, max
  bears = 2/3 and best min-bear = −3.03%. The new degree of freedom is real.
- **All 28 carry XLE in the OFF book.** With XLE the 2022H1 ceiling is
  **+15.45%**; without it, +1.17% and **zero** 3/3 configs. Energy in the
  risk-off leg is what makes 2022H1 non-negative — not the core, not the damp.
- All 28 share one OFF book (`GDX .25 / XLE .25 / GLD .5`), tv 0.15, ref QQQ,
  damp 0.0, N ∈ {80,100}. That is a needle, not a region.

## Top 10 by (bears-min, rolling-12m, cycle margin)

Deduplicated on outcome; every row is 3/3 bears, ref QQQ, tv 0.15, damp 0.0,
OFF = `GDX .25 / XLE .25 / GLD .5`.

| # | min-bear | roll-12m | cycle mg | wins | (fast,slow) | N | hyst | ON book |
|---|---|---|---|---|---|---|---|---|
| 1 | +0.16 | 76.3% | +105.1 | 12 | (20,60) | 100 | 1/2 | GLD .25 / GDX .75 |
| 2 | +0.16 | 71.2% | +103.3 | 12 | (10,40) | 100 | 1/2 | GLD .25 / GDX .75 |
| 3 | +0.16 | 65.8% | +91.6 | 10 | (10,40) | 100 | 0/1 | GLD .25 / GDX .75 |
| 4 | +0.16 | 61.4% | +79.6 | 11 | (20,60) | 80 | 1/2 | GLD .25 / GDX .75 |
| 5 | +0.16 | 60.0% | +76.8 | 11 | (10,40) | 80 | 1/2 | GLD .25 / GDX .75 |
| 6 | +0.16 | 59.6% | +74.8 | 10 | (20,60) | 100 | 0/1 | GLD .25 / GDX .75 |
| 7 | +0.00 | 79.2% | +103.3 | 13 | (20,60) | 100 | 1/2 | GLD .5 / GDX .5 |
| **8** | **+0.00** | **76.5%** | **+118.0** | **15** | (10,40) | 100 | 1/2 | GLD .5 / GDX .5 |
| 9 | +0.00 | 71.8% | +96.9 | 13 | (10,40) | 80 | 0/1 | GLD .5 / GDX .5 |
| 10 | +0.00 | 70.6% | +104.5 | 14 | (10,40) | 100 | 0/1 | GLD .5 / GDX .5 |

## Top pick (#1) — full stats

`tv .15, (20,60), wmax .45, ref QQQ, N100, 1%/2%, damp 0, ON GLD.25/GDX.75, OFF GDX.25/XLE.25/GLD.5`

Rolling win-rate vs SPY-TR: **3m 54.6%, 6m 63.4%, 12m 76.3%** (731/958).
Cycle **+183.82% vs SPY-TR +78.77% = +105.06pp**; CAGR +24.17% vs +12.81%;
**maxDD −31.6%**; **turnover 393%/yr** one-way; cost drag 0.58pp/yr.

| window | SPY-TR | EB | margin | | window | SPY-TR | EB | margin |
|---|---|---|---|---|---|---|---|---|
| bear 2022H1 | −20.44 | **+1.05** | +21.49 | | hold 2023Aug–Oct | −8.06 | −6.99 | +1.07 ○ |
| bear 2026Feb–Apr | −5.52 | **+3.88** | +9.40 | | hold 2025May–Oct | +22.83 | +46.53 | +23.70 |
| bear 2025Feb–Apr | −11.82 | **+0.16** | +11.98 | | hold 2024Nov–25Feb | +7.13 | +5.44 | −1.69 |
| bull 2023H1 | +21.12 | +19.50 | −1.62 | | hold 2026Jun–Aug | +1.92 | +6.08 | +4.17 |
| bull 2026Apr–Jun | +15.77 | +3.06 | **−12.70** | | cal 2022 | −18.65 | −0.97 | +17.68 |
| bull 2024H1 | +15.87 | +29.10 | +13.23 | | cal 2023 | +26.71 | +15.02 | −11.69 |
| chop 2025Nov–26Feb | +1.17 | +22.70 | +21.54 | | cal 2024 | +25.59 | +26.09 | +0.50 ○ |
| chop 2022H2 | +1.19 | +0.11 | −1.08 ○ | | cal 2025 | +18.01 | +83.02 | +65.01 |
| chop 2024Jul–Oct | +4.59 | −2.12 | −6.71 | | **FULL CYCLE** | **+78.77** | **+183.82** | **+105.06** |

**12/19 wins**, 3/3 bears, worst −12.70pp, 2 margins inside noise.

**Gold-neutralised (GLD,GDX→SPY): +105.1 → +4.5pp, and bears go
−15.31/+0.87/−17.07.** 96% of the edge and 100% of the bear-safety is the gold
position. **2010-06→2021-12: +6.94%/yr vs SPY-TR +15.94%/yr, maxDD −44.1%,
rolling-12m win 33.0%** — far worse than iter5's P1 (+12.63%/yr).

**±1 step: not a plateau.** Of 8 core-axis neighbours only 4 keep 3/3 bears
(volw, wmax, N=80, hyst); tv→.20 gives 1/3, ref→SPY gives 0/3, N→150 gives 1/3,
damp→0.5 gives 1/3. Of 10 book neighbours, **1** keeps 3/3.

**Cost ±25%:** wins 12/19 throughout, bears +1.15/+3.92/+0.23 → +0.95/+3.84/+0.08,
roll 76.5→76.1%, cycle +107.1→+103.0pp. Stable — but see below.

## Dominating alternative (#8), and the flag

#8 is better on every reported axis: **15/19 wins**, 3/3 bears, roll-12m 76.5%
(3m 57.7%, 6m 66.2%), cycle **+117.97pp**, CAGR +25.32%, maxDD −30.8%, turnover
319%/yr, drag 0.43pp/yr, gold-neutral margin **+25.7pp**, 2010–21 +9.53%/yr.
It ranks below #1 only because its min-bear is +0.0033% instead of +0.16%.

**At +25% cost #8's 2022H1 flips to −0.07% and it drops to 2/3 bears.**

## Noise flag — read this before acting

Every bear-safe config in this iteration clears zero by **+0.00 to +1.05pp** on
its binding bear. Measured replay-vs-engine error on the parents is **+2.8 to
+8.3pp** on bear windows. **The 3/3 bear result is entirely inside replay noise
and would not survive an engine run.** #8's bear-safety does not even survive a
25% cost bump.

## The control that ends the line

| static buy-and-hold, no strategy | roll-12m | cycle mg | bears | 3/3? |
|---|---|---|---|---|
| GDX .25 / XLE .25 / GLD .5 (the OFF book) | 61.2% | +82.0pp | +3.19/+6.19/+7.06 | **YES** |
| GLD 100% | 66.3% | +73.5pp | +0.08/+2.50/+10.00 | **YES** |
| GLD .5 / GDX .5 | 67.0% | +110.1pp | −6.15/+2.22/+15.82 | no |

**Holding the OFF book and never trading is 3/3 bear-safe with margins 20×
thicker than the top pick's, and beats SPY-TR by +82pp with zero turnover.**
The entire strategy buys ~+15pp of rolling-12m win rate and ~+23pp of cycle
margin over that, for 393%/yr turnover — and it makes the bears *thinner*, not
safer.

## Verdict

The hybrid is a genuine mechanical advance: state-dependent books plus XLE in
the risk-off leg produced the first 3/3 bear-safe configurations in the program.
It does not reach the objective, and it does not fail by a hair — the frontier
sits **15.8pp of rolling-win short**. The 95%-rolling requirement and
non-negative bears are opposite ends of one exposure dial, now confirmed across
three iterations and 78,000+ configurations. **Do not ship.** If the objective
stands it needs an instrument that is long equity beta and defensively
positioned simultaneously; this family cannot express one.

### doc-200 config JSON — top pick #1

```json
{
  "strategy_eb_enabled": true,
  "core_symbol": "TQQQ",
  "core_leverage": 3.0,
  "reference_symbol": "QQQ",
  "off_symbol": "SPY",
  "cash_symbol": "BIL",
  "target_vol": 0.15,
  "core_max_weight": 0.45,
  "weight_step": 0.05,
  "vol_fast_bars": 20,
  "vol_slow_bars": 60,
  "min_history_bars": 70,
  "core_rebalance_band": 0.10,
  "rebalance_weekdays": [2],
  "remainder_bil_fraction": 0.0,
  "trend_filter_bars": 100,
  "trend_off_enter_pct": 0.01,
  "trend_on_exit_pct": 0.02,
  "risk_off_symbol": "",
  "core_off_damp": 0.0,
  "trend_on_book": {"GLD": 0.25, "GDX": 0.75},
  "trend_off_book": {"GDX": 0.25, "XLE": 0.25, "GLD": 0.5},
  "cash_sweep_min_pct": 0.02,
  "core_band_pct": 0.03,
  "min_order_usd": 25.0,
  "cost_haircut_pct": 0.005,
  "broker_max_single_position_pct": 0.95,
  "honour_single_position_cap": true
}
```

For **#8** (recommended over #1 if either is run): same JSON with
`"vol_fast_bars": 10`, `"vol_slow_bars": 40`,
`"trend_on_book": {"GLD": 0.5, "GDX": 0.5}`.

Battery `stocks`: `["TQQQ","QQQ","SPY","BIL","GLD","GDX","XLE"]`,
`granularity: "86400"`, `equity_cost_tiers: "etf-liquid"`.

*Scripts: `iter6/{core6,validate6,search6,extra6,probe6,probe7,probe8}.py`;
logs `iter6/{validate,search,extra,probe,probe7,probe8}_log.txt`; raw grid
`rows6.pkl`; `top200.json`, `toproll.json`, `detail6.json`.*
