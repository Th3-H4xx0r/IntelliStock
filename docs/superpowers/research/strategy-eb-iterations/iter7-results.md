# iter7 — fast core-only damp × XLE-admitted static book

## Objective, stated before results (unchanged from iter6)

Rolling 252-session windows on 2021-11-01→2026-08-27: EB > SPY-TR in **≥95%**
of them, **AND** rb1 (2022H1), rb2 (2026 Feb–Apr), rb3 (2025 Feb–Apr) each
**≥0** absolute, **AND** cycle margin **≥+100pp**. SPY-TR = yfinance `auto_adjust`.

## Pass count: **0 of 19,840.**

Grid = tv{.10,.15,.20,.25} × (fast,slow){(10,40),(20,60)} × wmax{.45,.65} ×
ref{SPY,QQQ} × N{20,30,40,60,**100**} × hyst{(0,1),(1,2)}% × damp{0,.25}
= 640 cores × 31 books (all .25-step 2–4-name compositions over
{GLD,GDX,XLE,SMH}) = 19,840, every one expressible in shipped `strategy_eb`.
N=100 is carried inside the grid as the direct control for the old cell.
Ran whole in 96 s.

**Simulator.** iter6/core6 verbatim — no model change. Reloaded under iter7 it
reproduces P1, iter6 #1 and iter6 #8 to **0.000pp** on cycle margin and ≤0.003pp
on every bear (that residual is my transcription of iter6's 2-dp published
figures, not simulator drift). Costs 4.4/23.2 bps per symbol, decide t / execute
t+1, weekly Wed, band 0.10.

## The mechanism question, answered: the exclusion **moved**, and it moved a lot

| constraint | iter6 best of the other | **iter7** |
|---|---|---|
| all 3 bears ≥ 0 → max rolling-12m | 79.2% | **93.5%** |
| roll ≥ 90% → best min-bear | −1.07% | **+1.49%** |
| roll ≥ 95% → best min-bear | −1.98% | **−0.28%** |
| shortfall of the frontier from 95% at min-bear ≥ 0 | **15.8pp** | **1.5pp** |

The one-dial exclusion iter5/iter6 measured was an artefact of wiring the trend
filter to the *whole book*. Wired to the core only, it stops being one dial.
The clean control — identical core, identical static book, **only N changes**:

| N | flips | turn %/yr | drag | rb1 | rb2 | rb3 | bears | roll-12m | cycle mg |
|---|---|---|---|---|---|---|---|---|---|
| OFF | 0 | 140% | 0.12pp | −11.30 | −0.28 | −6.56 | 0/3 | 91.4% | +119.4 |
| 20 | 40 | 398% | 0.36 | −0.39 | +3.88 | +1.51 | 2/3 | 72.1% | +124.4 |
| **30** | 24 | 270% | 0.24 | **+1.49** | **+3.88** | **+1.51** | **3/3** | 90.5% | +145.2 |
| **40** | 23 | 293% | 0.26 | **+0.10** | **+3.88** | **+1.51** | **3/3** | **93.5%** | **+155.8** |
| 60 | 23 | 281% | 0.25 | +0.10 | +3.88 | +1.51 | 3/3 | 76.7% | +125.8 |
| 100 | 14 | 195% | 0.16 | −0.58 | +3.88 | −0.57 | 1/3 | 84.1% | +117.2 |
| 150 | 8 | 158% | 0.14 | −0.58 | +2.99 | −3.62 | 1/3 | 93.4% | +119.0 |

Three states, one book: the **book alone** (no core, no filter) is 3/3 bear-safe
at roll 61.8%; **book + core, filter off** is 0/3 at roll 91.4%; **book + core +
fast filter** is 3/3 *and* 93.5%. The fast filter recovers essentially all of the
core's rolling-win contribution while deleting the core precisely in the bears.
N=100 cannot: it is too slow to be out of TQQQ for 2022H1 and 2025 Feb–Apr.

**Why fast is affordable here, measured not asserted.** At tv .15 the mean
ON-state core weight is **21.6% of NAV**, so a flip round-trips ~43% of NAV, not
100%. Same core, same N, same flip dates, remainder rotating instead of static:

| N | remainder | turn %/yr | drag %/yr | roll-12m | min-bear |
|---|---|---|---|---|---|
| 40 | **static (core-only flip)** | 293% | 0.26 | 93.5% | **+0.10** |
| 40 | rotating SMH.5/GLD.5 → book | 671% | **1.16** | 92.0% | **−5.59** |
| 30 | static | 270% | 0.24 | 90.5% | +1.49 |
| 30 | rotating | 683% | 1.20 | 90.2% | −4.98 |

Whole-book switching at fast N costs **4.5× the drag and 5pp of bear margin** for
no rolling-win gain. That is the exclusion iter5 measured — it is a property of
the wiring, not of the fast filter.

**And it is a plateau, not a spike.** N swept one bar at a time, 15→120: 3/3
bears holds over **N ∈ {25,30,35,40,45,50,55,60}**, with roll-12m 90.5–93.5% for
N=25…55 (only N=60 dips to 76.7%). Immediate neighbours of N=40 are 91.8/93.5/91.6%.

## Pareto frontier (0 configs pass, so this is the deliverable)

- **(i) max rolling-12m s.t. bears ≥ 0 → T1**: tv .15, (10,40), wmax .45, QQQ,
  **N40**, 1/2%, damp 0, book **GLD .5 / GDX .25 / XLE .25** (same in both
  states). roll **93.5%**, bears +0.10/+3.88/+1.51, cycle **+155.8pp**, 13/19.
- **(ii) max min-bear s.t. roll ≥ 90% → T2**: same but **N30**. min-bear
  **+1.49%**, roll 90.5%, cycle +145.2pp, 13/19.
- **(iii) best compromise → T3**: **tv .20, N30**. roll **97.0%** (clears the
  rolling bar), 15/19 wins, cycle +171.6pp — but rb3 = **−0.28%**, so 2/3 bears.

All three land on the *same* book — iter6's rotation-only OFF book, here held
statically in both states. Of the 188 3/3-bear configs, 100% use ref QQQ and only
three books ever appear, all containing XLE; 164/188 use tv .10.

## Top-3 battery

| | T1 (N40) | T2 (N30) | T3 (tv.20,N30) |
|---|---|---|---|
| rolling 3m / 6m / 12m | 65.2 / 74.5 / **93.5%** | 63.2 / 71.0 / 90.5% | 69.5 / 79.8 / **97.0%** |
| 19-window wins | 13/19 | 13/19 | **15/19** |
| bears | +0.10/+3.88/+1.51 (3/3) | +1.49/+3.88/+1.51 (3/3) | +0.44/+3.88/−0.28 (2/3) |
| worst window | −9.10pp | −9.10pp | −6.11pp |
| cycle margin | +155.8pp | +145.2pp | +171.6pp |
| CAGR / SPY-TR | +28.48% / +12.81% | +27.63% | +29.72% |
| maxDD | −27.6% | −26.4% | −28.0% |
| turnover | 293%/yr | 270%/yr | 348%/yr |
| cost drag | 0.26pp/yr | 0.24pp/yr | 0.32pp/yr |
| **gold+energy-neutral margin** | **+14.9pp** | +16.4pp | +22.7pp |
| neutralised bears | −21.85/−3.33/−15.54 | −20.92/−3.33/−15.54 | −21.68/−3.33/−16.98 |
| 2010-06→2021-12 CAGR | +7.53%/yr (SPY +15.94) | +7.73%/yr | +8.21%/yr |
| ±25% cost | 3/3 held (+0.05/+3.84/+1.46) | 3/3 held | 2/3 both ways |
| ±1 core step keeping 3/3 | 6/10 | 5/10 | 2/10 |
| ±1 book step keeping 3/3 | **0/9** | **0/9** | 0/9 |

T1 survives +25% cost as 3/3 (iter6's #8 did not). Its 62 losing 12m windows all
end in 2023 (26) or 2024 (36) — one whiplash episode, median −1.5pp.

## Mandatory noise flags

Replay-vs-engine error, from iter6's parent calibration: bear windows **+2.8 to
+8.3pp optimistic**, rolling-12m **±7pp**.

- **Every bear margin in the top-3 sits inside the +2.8…+8.3pp band.** T1's
  +0.10 / +3.88 / +1.51 and T2's +1.49 / +3.88 / +1.51 are *all three* smaller
  than the low end of the error bar. **The 3/3 bear result is not a measurement**
  and would very likely not survive an engine run — the same verdict as iter6.
- **The 1.5pp rolling shortfall (93.5% vs 95%) is inside ±7pp.** iter7 therefore
  cannot distinguish "passes the rolling bar" from "fails it". Symmetrically,
  T3's 97.0% is not proof of clearing it either.
- rb2 = +3.88% is identical across N ∈ {20,…,60} and equals the book's own
  return: the filter is simply flat TQQQ through that window, so this column
  carries no timing information at all.
- What is **not** inside noise: the frontier *shift*, 79.2 → 93.5% at min-bear ≥ 0.
  A 14.3pp move measured on the same simulator against the same iter6 baseline is
  a comparison, not an absolute, and 14.3pp > the ±7pp bar.

## Honest negatives

Neutralising GLD/GDX/XLE → SPY collapses T1's margin from +155.8pp to **+14.9pp**
and its bears to −21.85/−3.33/−15.54. **~90% of the edge and 100% of the
bear-safety is the commodity book, not the timing.** Out of design sample
(2010–2021) T1 compounds at **+7.53%/yr against SPY-TR's +15.94%** with a −36%
drawdown. The book is a knife-edge: **0 of 9** single-step book neighbours keep
3/3 bears. And the static book alone — never timed, 41%/yr turnover — is already
3/3 with margins **+4.24/+3.88/+6.01**, thicker than anything the strategy
produces; the strategy buys +32pp of rolling-12m and +59pp of cycle margin over
it, for 7× the turnover, while making the bears *thinner*.

## Verdict

Cell A is a real mechanical result: **the fast core-only damp moves the
bears-vs-rolling frontier by 14.3pp and the one-dial exclusion does not bind in
this wiring.** It needs cell B's XLE-bearing book as the carrier — neither half
works alone. The objective is still **not met (0/19,840)**, and the residual
1.5pp gap plus every bear margin lie inside replay error, so iter7 cannot claim
the objective is nearly reached, only that the *frontier* is no longer 15.8pp
away. **Do not ship on this evidence.** The one action worth the money: run T1 on
the engine. It is the first configuration in the program whose failure mode is
measurement error rather than mechanism, and an engine run would settle both the
bear signs and the rolling-12m number at once.

*Off-grid note, flagged as not pre-registered:* **N=25** (one step below the
declared {20,30,40,60,100}) gives 3/3 bears +1.57/+3.88/+1.51, roll 92.1%, cycle
+142.9pp and **15/19 wins** — dominating T1 on wins and min-bear for 1.4pp of
rolling. Reported for completeness; T1 remains the pre-registered pick.

### doc-200 config JSON — T1

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
  "vol_fast_bars": 10,
  "vol_slow_bars": 40,
  "min_history_bars": 70,
  "core_rebalance_band": 0.10,
  "rebalance_weekdays": [2],
  "remainder_bil_fraction": 0.0,
  "trend_filter_bars": 40,
  "trend_off_enter_pct": 0.01,
  "trend_on_exit_pct": 0.02,
  "risk_off_symbol": "",
  "core_off_damp": 0.0,
  "trend_on_book":  {"GLD": 0.5, "GDX": 0.25, "XLE": 0.25},
  "trend_off_book": {"GLD": 0.5, "GDX": 0.25, "XLE": 0.25},
  "cash_sweep_min_pct": 0.02,
  "core_band_pct": 0.03,
  "min_order_usd": 25.0,
  "cost_haircut_pct": 0.005,
  "broker_max_single_position_pct": 0.95,
  "honour_single_position_cap": true
}
```

For **T2** substitute `"trend_filter_bars": 30`; for **T3**, `"target_vol": 0.20`
and `"trend_filter_bars": 30`.

Battery `stocks`: `["TQQQ","QQQ","SPY","BIL","GLD","GDX","XLE","SMH"]`,
`granularity: "86400"`, `equity_cost_tiers: "etf-liquid"`.

*Scripts: `iter7/{validate7,search7,extra7,probe7,probe8}.py`; logs
`iter7/{validate,search,extra,probe7,probe8}_log.txt`; raw grid `rows7.pkl`;
`top7.json`, `detail7.json`. Simulator is `iter6/core6.py` unmodified.*
