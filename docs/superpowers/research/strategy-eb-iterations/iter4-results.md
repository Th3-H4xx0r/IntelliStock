# iter4 — book/signal search for 18/19 + three non-negative bears

**Verdict up front: the target is unreachable.** Across 15,540 configurations of
the mechanism that is actually implemented, the ceiling with all three bear
windows ≥ 0 is **15/19**, and the ceiling ignoring the bear constraint is
**16/19** — exactly the class the engine-confirmed R1w baseline already occupies.
Four windows are lost by **100%** of bear-safe configurations, which caps wins at
15 arithmetically. Nothing is close to 18/19.

A second, larger finding: the whole cycle margin is an **asset bet on gold and
gold miners**, not a timing edge. Swapping GLD/GDX for SPY in both books collapses
the top pick's cycle margin from **+107.4pp to +3.5pp**.

---

## 1. Replay validation — FAILED the ±0.5pp bar. Max error 7.45pp.

I rebuilt the replay directly from `backend/strategy_eb.py` +
`backend/strategy_x.py:targets_to_orders` rather than reusing iter2b's
approximation, because the pure-book path has mechanics iter2b did not model:
the `core_band_pct = 0.03` per-leg no-churn band, `min_order_usd`, the 0.5%
`cost_haircut_pct`, settled-cash buy sizing, and the next-session cash sweep.
Two engine-side facts I confirmed from source and that change the numbers:

* **Strategy legs are priced off Alpaca `adjustment="split"`** (`broker.py:1013`)
  — **price-only, no dividends**. Only the SPY benchmark uses `adjustment="all"`
  (`broker.py:1371`). So **BIL earns ~0% in the engine** (+0.2% over the cycle vs
  +19.3% total-return). Any BIL slice is dead money, not a T-bill yield.
* Cost tiers are exactly as assumed: `ETF_LIQUID_SYMBOLS` = {SPY,QQQ,TQQQ,QLD,
  SQQQ,BIL,GLD,IWM} at 4.4 bps one-way, everything else 23.2 bps
  (`simulated_execution.py:132-243`).

Semantics: decision session = last *visible* daily close, so a tick decides when
`IDX[i-1]` is a Wednesday; fill at that tick's bar close (`lag=1`); buying power
credits pending sell proceeds. I calibrated `lag ∈ {1,2}` and settled-vs-credited
cash against the engine; `lag=1` + credited proceeds won on both arms.

**Result of replaying the exact R1w config against the engine's 19 numbers:**

| statistic | R1w (state machine) | STATIC control (no state machine) |
|---|---|---|
| max abs error, 18 non-cycle windows | **7.45pp** | 6.42pp |
| mean abs error | **2.41pp** | 2.35pp |
| cycle error | −13.4pp on +220.3% | −1.8pp on +246.8% |

The `STATIC` arm from the same battery — reference QQQ, `trend_filter_bars: 0`,
a *fixed* SMH .4 / GLD .4 / GDX .2 book with no state machine at all — shows the
**same error scale**. That is the diagnostic: the residual is not my state
machine, it is the price series. Corroborating it, the engine's own SPY-TR
benchmark disagrees with yfinance `auto_adjust` by up to 1.8pp (engine cycle +77
vs mine +78.8; ru2 +16.6 vs +15.8; h4 +7.6 vs +7.1).

**So the ±0.5pp gate cannot be met with public data, and I did not pretend it
was.** Everything below carries a ±2–3pp per-window error bar, which is larger
than most of the margins being ranked. My replay of R1w scores 14/19 where the
engine scored 16/19 — the win count itself is unstable at ±2 windows.

---

## 2. Search

Axes exactly as specified. Staged to stay inside budget and disclosed as staged:

* **Stage 1** (14,820): signal fixed at the engine-confirmed (SPY, SMA 80,
  −1%/+2%); all 780 ON books (step 0.1, 3–4 names over {SMH, GLD, QLD, GDX, XBI})
  × all 19 OFF books (GDX/XLE in quarters + an optional 0.25/0.50 GLD or BIL slice).
* **Stage 2** (720): the top 40 book pairs × all 18 signal variants
  (ref ∈ {SPY,QQQ} × N ∈ {60,80,100} × (enter,exit) ∈ {(1,2),(0,1),(2,3)}%).
* **Total 15,540.** Ranked only by (bears ≥ 0 count, wins, worst-window margin,
  cycle margin).

### The frontier

| bears ≥ 0 | n | max wins | best worst-window margin at max wins |
|---|---|---|---|
| **3/3** | 587 | **15/19** | −15.17pp |
| 2/3 | 7,291 | **16/19** | −8.15pp |
| 1/3 | 6,599 | 16/19 | −6.92pp |
| 0/3 | 1,063 | 15/19 | −10.44pp |

### Why 18/19 is impossible here

Loss frequency across the 587 bear-safe configurations:

| window | lost by | window | lost by |
|---|---|---|---|
| **ru2** 2026-04→06 | **100%** | ru1 2023H1 | 96.8% |
| **h1** 2021-11→12 | **100%** | y24 | 79.0% |
| **h4** 2024-11→2025-02 | **100%** | ru3 2024H1 | 29.5% |
| **y23** | **100%** | rc3, h2, rc2, h5 | 19.3 / 16.0 / 15.3 / 3.4% |

Four guaranteed losses ⇒ **15 is the hard ceiling** for any bear-safe member of
this family. Three of the four are the same failure: a weekly SMA state that is
OFF into a sharp rebound (ru2 re-entry latency, h4, h1's two-month window that
starts flat in cash), plus y23, where a gold-heavy ON book cannot keep up with
a +26.7% SPY year. The mechanism has one state variable and no way to be long
equity beta and gold at once, so these do not trade off — they compound.

---

## 3. Top 10 (ranked as specified). All are bear-safe; none exceeds 15/19.

| # | ref/N/hyst | ON book | OFF book | bears≥0 | wins | worst mgn | cycle mgn | CAGR | maxDD | turn/yr | cost drag | 2010-21 CAGR | GLD/GDX→BIL mgn |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **T1** | SPY 100 1/2 | GLD .6 QLD .2 GDX .2 | GDX .25 XLE .25 GLD .5 | 3 | **15** | −15.17 | +107.4 | +24.4% | −27.2% | 240% | 0.39pp | +7.12% | **−53.9pp** |
| T2 | SPY 100 1/2 | GLD .8 QLD .1 GDX .1 | GDX .5 XLE .5 | 3 | 15 | −20.01 | +131.7 | +26.5% | −32.7% | 637% | 1.16pp | +6.55% | −50.4pp |
| T3 | SPY 80 1/2 | GLD .8 QLD .1 GDX .1 | GDX .5 XLE .5 | 3 | 15 | −20.01 | +109.7 | +24.6% | −38.7% | 639% | 1.15pp | +6.08% | −58.6pp |
| T4 | SPY 100 1/2 | SMH .1 GLD .6 QLD .1 GDX .2 | GDX .25 XLE .25 GLD .5 | 3 | 14 | −14.65 | +118.2 | +25.3% | −27.0% | 237% | 0.48pp | +6.28% | −48.9pp |
| T5 | QQQ 100 2/3 | SMH .1 GLD .6 QLD .1 GDX .2 | GDX .25 XLE .25 GLD .5 | 3 | 14 | −14.65 | +111.7 | +24.8% | −27.0% | 187% | 0.35pp | +7.98% | −51.0pp |
| T6 | QQQ 100 1/2 | SMH .1 GLD .6 QLD .1 GDX .2 | GDX .25 XLE .25 GLD .5 | 3 | 14 | −14.65 | +106.7 | +24.3% | −28.9% | 210% | 0.40pp | +6.86% | −52.8pp |
| T7 | SPY 100 1/2 | SMH .2 GLD .6 GDX .2 | GDX .25 XLE .25 GLD .5 | 3 | 14 | −15.13 | +123.9 | +25.8% | −26.8% | 237% | 0.56pp | +5.42% | −45.9pp |
| T8 | QQQ 100 2/3 | SMH .2 GLD .6 GDX .2 | GDX .25 XLE .25 GLD .5 | 3 | 14 | −15.13 | +116.8 | +25.2% | −26.8% | 186% | 0.43pp | +6.95% | −49.1pp |
| T9 | SPY 80 1/2 | SMH .2 GLD .6 GDX .2 | GDX .25 XLE .25 GLD .5 | 3 | 14 | −15.13 | +109.0 | +24.5% | −29.8% | 236% | 0.55pp | +5.14% | −51.3pp |
| T10 | QQQ 100 1/2 | SMH .2 GLD .6 GDX .2 | GDX .25 XLE .25 GLD .5 | 3 | 14 | −15.13 | +108.4 | +24.5% | −28.8% | 209% | 0.49pp | +6.03% | −51.6pp |
| — | *R1w baseline* | SMH .3 GLD .7 | GDX .5 XLE .5 | 2 | 14* | −12.36 | +128.1 | +26.2% | −39.7% | 718% | 1.38pp | +8.77% | −31.3pp |
| — | *P16 (best 16/19)* | SMH .3 GLD .5 GDX .1 XBI .1 | GDX 1.0 | 2 | **16** | −8.35 | +147.6 | +27.8% | −46.8% | 629% | 1.47pp | +10.75% | −41.9pp |

\* the engine scored R1w 16/19; my replay 14/19 — the ±2-window instability above.
SPY-TR: cycle +78.8%, 2010-2021 CAGR +14.89%. **Every one of these underperforms
SPY by 4–10pp/yr in the 2010-2021 design-context period.**

### Per-window returns (%), T1 / T2 / T3 / R1w / P16 vs SPY-TR

| window | SPY-TR | T1 | T2 | T3 | R1w | P16 |
|---|---|---|---|---|---|---|
| bear 2022H1 | −20.44 | **+0.10** | **+0.03** | **+0.03** | −2.92 | −17.31 |
| bear 2026Feb–Apr | −5.52 | **+0.12** | +2.90 | +5.28 | +4.47 | +0.37 |
| bear 2025Feb–Apr | −11.82 | +3.88 | +3.46 | +3.46 | +0.91 | +14.63 |
| bull 2023H1 | +21.12 | +21.42 | +21.14 | +21.99 | +32.04 | +35.90 |
| bull 2026Apr–Jun | +15.77 | +0.60 | −4.24 | −4.24 | +3.41 | +7.42 |
| bull 2024H1 | +15.87 | +19.14 | +16.22 | +16.22 | +26.13 | +24.99 |
| chop 2025Nov–26Feb | +1.17 | +22.53 | +23.22 | +23.22 | +23.09 | +24.18 |
| chop 2022H2 | +1.19 | +5.17 | +18.12 | +7.03 | +3.63 | +2.25 |
| chop 2024Jul–Oct | +4.59 | +7.44 | +12.17 | +12.11 | +6.72 | +9.26 |
| hold 2021Nov–Dec | +3.61 | +1.09 | +1.51 | +1.51 | +2.75 | +0.83 |
| hold 2023Aug–Oct | −8.06 | −3.65 | −6.75 | −6.75 | −6.72 | −2.45 |
| hold 2025May–Oct | +22.83 | +34.77 | +31.07 | +31.07 | +32.64 | +33.10 |
| hold 2024Nov–25Feb | +7.13 | +6.14 | +6.38 | +6.38 | +3.60 | +1.49 |
| hold 2026Jun–Aug | +1.92 | +4.00 | +3.36 | +3.36 | −0.76 | +3.22 |
| 2022 | −18.65 | +3.12 | +17.46 | +6.46 | +0.03 | −15.94 |
| 2023 | +26.71 | +20.17 | +15.80 | +16.62 | +25.97 | +41.62 |
| 2024 | +25.59 | +28.21 | +28.76 | +28.76 | +34.40 | +34.09 |
| 2025 | +18.01 | +64.21 | +57.10 | +57.10 | +48.79 | +64.23 |
| **cycle** | **+78.77** | +186.12 | +210.51 | +188.45 | +206.88 | +226.37 |

---

## 4. The bear-safety is a knife edge, and it is inside the noise

T1's three bears are **+0.10%, +0.12%, +3.88%**. Two of the three clear zero by
about a tenth of a percent — against a replay whose per-window error is 2.4pp
mean and 7.4pp max. **Those two "positive" bears carry no information.**

±1 grid step on T1 (26 neighbours): **22 of 26 lose bear-safety.**

| perturbation | bears ≥ 0 | wins | bears (%) |
|---|---|---|---|
| **T1 base** | **3** | 15 | +0.10 / +0.12 / +3.88 |
| N: 80 | 3 | 14 | +0.10 / +1.27 / +3.88 |
| N: 60 / 120 / 140 | **2** | 12 / 14 / 14 | −4.14 … −3.23 on rb1 |
| hyst 0.00/0.02, 0.01/0.03 | 3 | 15 / 14 | unchanged |
| hyst 0.00/0.01, 0.02/0.03, 0.01/0.01, 0.02/0.02 | **2** | 14–15 | rb1 −1.67 … −3.23 |
| ref QQQ | 3 | 13 | +0.10 / +3.88 / +3.88 |
| **every one of the 8 ON-book ±0.1 moves** | **2** | 13–15 | rb1 or rb2 goes negative |
| **every one of the 8 OFF-book ±0.25 moves** | **2 or 1** | 14–16 | rb1 as low as −7.51 |

Not a plateau. A single point that happens to land two bears a hair above zero.

---

## 5. What the margin actually is

| config | raw cycle margin | GLD,GDX → **BIL** | GLD,GDX → **SPY** |
|---|---|---|---|
| T1 | +107.4pp | −53.9pp | **+3.5pp** |
| T2 | +131.7pp | −50.4pp | +3.2pp |
| T3 | +109.7pp | −58.6pp | −7.5pp |
| R1w | +128.1pp | −31.3pp | +20.1pp |
| P16 | +147.6pp | −41.9pp | +16.9pp |

Context: over the cycle, GLD alone returned **+152.3%** and GDX **+225.5%** vs
SPY-TR +78.8%. The BIL swap is harsh (engine BIL ≈ 0% on split-adjusted bars, so
it is a cash swap, not a T-bill swap); the SPY swap is the fair control, and it
says **~97% of T1's edge is the gold/miners position**. In the 2010-2021
design-context period — where gold did nothing (GLD +3.76%/yr, GDX −3.27%/yr) —
T1 compounds at **+7.1%/yr against SPY's +14.9%**. Optimising over a window in
which gold tripled selects for holding gold. Note that the search *made this
worse*: it pushed the ON book from R1w's SMH .3 / GLD .7 to GLD .6-.8, i.e. it
paid for bear-safety with more gold, and the SPY-neutral margin fell from +20.1pp
to +3.5pp.

---

## 6. Recommendation

**Do not ship any of these.** T1 is a worse strategy than the confirmed R1w
baseline on every axis that matters: fewer engine-scored wins (15 vs 16), a worse
worst window (−15.2 vs −12.4pp), less cycle margin (+107 vs +128pp), a far
weaker asset-neutral margin (+3.5 vs +20.1pp), and a worse design-context decade
(+7.1 vs +8.8%/yr). Its only advantage is two bears at +0.1%, which is noise.
The one thing it genuinely buys is drawdown: −27.2% vs R1w's −39.7%, at 240%/yr
turnover instead of 718% and 0.39pp/yr cost drag instead of 1.38pp — if the
brief were "keep R1w's shape, cut the drawdown and the churn", T1 is the answer.
It is not the brief.

If the 18/19 + three-non-negative-bears target is to stay, the **mechanism** has
to change — the four 100%-loss windows (ru2, h1, h4, y23) are re-entry latency
and equity-beta shortfall, and no re-weighting of two static books fixes either.

### doc-200 config JSON for the top pick (T1)

```json
{
  "strategy_eb_enabled": true,
  "target_vol": 0.0,
  "reference_symbol": "SPY",
  "trend_filter_bars": 100,
  "trend_off_enter_pct": 0.01,
  "trend_on_exit_pct": 0.02,
  "core_off_damp": 1.0,
  "trend_on_book": {"GLD": 0.6, "QLD": 0.2, "GDX": 0.2},
  "trend_off_book": {"GDX": 0.25, "XLE": 0.25, "GLD": 0.5},
  "risk_off_symbol": "",
  "remainder_bil_fraction": 0.0,
  "rebalance_weekdays": [2]
}
```
Battery `stocks` list: `["TQQQ","SPY","BIL","QQQ","GLD","GDX","XLE","QLD"]`,
`granularity: "86400"`, `equity_cost_tiers: "etf-liquid"`.

*Scripts: `iter4/{fetch2,core4,fast,search,analyse,detail,sens,extra}.py`.
Data: `px_split.csv` (legs, `adjustment="split"` equivalent), `px_tr.csv` (SPY-TR
benchmark).*
