# iter5 — vol-targeted TQQQ core + static remainder book

**Pass count against the pre-registered bar: 0 of 13,536.** Not one configuration
reaches 18/19 wins, and not one gets all three bears to zero — the bear
constraint fails by *arithmetic*, not by a hair: across the whole grid the best
2022H1 return is **−1.91%**, and 0.0% of configurations are non-negative there.

**But the mechanism hypothesis was half right, and the half that was right is
the interesting half.** Continuous sizing fixed two of the four windows that
100% of iter4's bear-safe family lost, and it did so decisively.

---

## 1. Setup

`iter5/core5.py` replays the shipped `strategy_eb` with a **non-zero** core:
`rv = max(stdev(ret[-fast:]), stdev(ret[-slow:]))·√252` on QQQ,
`w = floor(clamp(tv/(3·rv)·damp, 0, wmax)/0.05)·0.05`, weekly Wednesday decision
(`WKD[i-1]==2`), `core_rebalance_band 0.10` on the core weight, remainder split
across a book whose shares sum to 1 so the SPY/BIL shortfall leg is empty and
**the ON and OFF books are identical** — a flip damps leverage and rotates
nothing. Execution is iter4's calibrated path: `core_band_pct 0.03`,
`min_order_usd 25`, `cost_haircut 0.005`, next-session sweep above 2% cash,
lag=1 fills, buying power crediting pending proceeds, per-symbol engine costs
4.4/23.2 bps, legs on split-adjusted prices, SPY-TR benchmark.

Two validations. At `target_vol: 0` the simulator reproduces iter4's pure-book
replay to **≤0.09pp** on all 19 windows for three test books — same code path,
same numbers. At the shipped default (`tv 0.20`, remainder 100% SPY) it returns
**CAGR +23.3%, maxDD −45.5%** over 2010-06→2026-08 against the module
docstring's "~24% / ~−40%". The machinery is the shipped one.

Grid: tv {0.10,0.15,0.20,0.25} × (fast,slow) {(10,40),(20,60)} × wmax
{0.45,0.65} × 3 damp variants {trend off; 100/0.5; 100/0.0} × 282 books
(2–4 of GLD/GDX/SMH/XBI, 0.1 steps, sum 1) = **13,536 configurations, all run,
no staging.**

**The error bar has not moved since iter4:** the replay is ±2 windows against
the engine and per-window error is 2.4pp mean / 7.4pp max. Any margin
under ±1.5pp is a coin flip and is labelled as one.

---

## 2. The pre-registered bar

| filter | n |
|---|---|
| **wins ≥ 18 AND 3 bears ≥ 0 AND every win margin ≥ +1.5pp** | **0** |
| wins ≥ 18 alone | 0 |
| 3 bears ≥ 0 alone | **0** |
| every win margin ≥ +1.5pp alone | 7,687 |
| wins ≥ 17 and ≥ 2 bears | 16 |

### Pareto frontier

| bears ≥ 0 | n | max wins | best worst-window margin at max wins |
|---|---|---|---|
| 3/3 | **0** | — | — |
| 2/3 | 982 | **17/19** | −7.42pp |
| 1/3 | 7,856 | 16/19 | −4.79pp |
| 0/3 | 4,698 | 16/19 | −4.27pp |

Max wins overall is **17/19** — one better than iter4's 16 — and the ceiling
with a bear constraint is 2/3, where iter4 could reach 3/3. The two families sit
on opposite ends of one dial.

---

## 3. Was the mechanism hypothesis right? Half.

The four windows that 100% of iter4's bear-safe set lost, and what continuous
sizing did to each:

| window | SPY-TR | iter4 (binary switch) | iter5 win rate | iter5 best | verdict |
|---|---|---|---|---|---|
| **ru2** bull 2026Apr–Jun | +15.77% | **lost by 100%** | **71.3%** (83.9% with the filter off) | +66.6% (+50.8pp) | **FIXED** |
| **y23** cal 2023 | +26.71% | **lost by 100%** | **83.4%** (97.3% filter off) | +108.7% (+81.9pp) | **FIXED** |
| **h4** hold 2024Nov–25Feb | +7.13% | lost by 100% | 1.8% | +8.74% (+1.61pp) | not fixed |
| **h1** hold 2021Nov–Dec | +3.61% | lost by 100% | **0.0%** | +3.09% (−0.51pp) | **not fixable here** |

iter4's diagnosis of ru2 and y23 was correct and the fix works: both were
**equity-beta shortfall**, and holding a continuously-sized levered core instead
of an all-or-nothing rotation removes them as guaranteed losses. Note that ru2
and y23 improve *most* with the trend filter switched off entirely — pure
continuous sizing — which is exactly the predicted direction.

h1 and h4 were misdiagnosed as re-entry latency. They are not. h1 is a 42-session
window in which a levered Nasdaq core cannot beat +3.61% of SPY from a standing
start no matter how it is sized; the ceiling across all 13,536 is −0.51pp, and
the ceiling is the *same number* in all three damp variants, so the state machine
is not involved at all. h4 has a +1.61pp ceiling reached by 1.8% of the grid.

**And the fix bought its two windows with a third.** iter4's 587 bear-safe
configurations all had core weight 0 by construction. Any TQQQ core at all makes
2022H1 negative, and the damage scales cleanly with the core:

| target_vol | best 2022H1 | ru2 won by | y23 won by | max wins |
|---|---|---|---|---|
| 0.10 | −1.91% | 40.0% | 55.2% | 16 |
| 0.15 | −3.74% | 62.8% | 78.3% | 16 |
| 0.20 | −4.66% | 85.9% | 99.9% | 16 |
| 0.25 | −6.77% | 96.6% | 100.0% | **17** |

That is the whole finding in one table. The core weight is a single dial trading
**"non-negative in every bear"** against **"beat SPY in every bull"**, and no
setting of it satisfies both. This is not a search failure; it is the shape of
the mechanism.

---

## 4. The frontier picks

**P1** — max wins, most bears available: `tv 0.25, (10,40), wmax 0.45,
trend_filter 100, core_off_damp 0.0, book GLD 0.5 / GDX 0.5 both states`.
**17/19 wins, 2/3 bears, worst window −7.42pp, cycle +250.7% vs SPY +78.8%.**

| window | SPY-TR | P1 | margin | | window | SPY-TR | P1 | margin |
|---|---|---|---|---|---|---|---|---|
| bear 2022H1 | −20.44 | −10.33 | +10.11 | | hold 2023Aug–Oct | −8.06 | −4.97 | +3.09 |
| bear 2026Feb–Apr | −5.52 | **+2.19** | +7.71 | | hold 2025May–Oct | +22.83 | +48.80 | +25.97 |
| bear 2025Feb–Apr | −11.82 | **+1.76** | +13.58 | | hold 2024Nov–25Feb | +7.13 | +8.07 | +0.94 ○ |
| bull 2023H1 | +21.12 | +40.38 | +19.26 | | hold 2026Jun–Aug | +1.92 | +2.30 | +0.38 ○ |
| bull 2026Apr–Jun | +15.77 | +18.68 | +2.91 | | cal 2022 | −18.65 | −11.07 | +7.58 |
| bull 2024H1 | +15.87 | +34.06 | +18.19 | | cal 2023 | +26.71 | +40.80 | +14.09 |
| chop 2025Nov–26Feb | +1.17 | +20.99 | +19.82 | | cal 2024 | +25.59 | +41.45 | +15.86 |
| chop 2022H2 | +1.19 | +1.28 | +0.09 ○ | | cal 2025 | +18.01 | +71.40 | +53.39 |
| chop 2024Jul–Oct | +4.59 | −2.82 | **−7.41** | | **FULL CYCLE** | **+78.77** | **+250.66** | **+171.89** |
| hold 2021Nov–Dec | +3.61 | −0.10 | **−3.71** | | | | | |

○ = inside the ±1.5pp noise band. **Noise-aware: 13 real wins, 2 real losses,
3 coin flips.** The 17/19 headline is really 13–16.

Cycle CAGR **+29.74%**, maxDD **−35.9%**, turnover **317%/yr**, cost drag
0.30pp/yr. Its two positive bears clear zero by +2.19 and +1.76pp — unlike
iter4's T1, which cleared by +0.10 and +0.12, these are outside the noise.

Gold-neutralised (GLD,GDX→SPY): cycle margin collapses **+171.9 → +27.5pp**, so
~84% of the edge is the gold position — but the residual +27.5pp (≈+4.4pp/yr) is
**eight times** iter4 T1's +3.5pp. The levered-core transform carries real
asset-neutral margin; the static book did not.

**2010-06→2021-12 design-context decade: +12.63%/yr vs SPY-TR +15.94%/yr.** It
still loses the decade — but P1's *exact core with the shipped SPY remainder*
compounds at **+21.3%/yr** there (the shipped default: +26.5%). The gold book is
what costs the decade, not the core.

**±1 grid step on P1 (11 neighbours): 2 hold or improve, wins range 12–17.** Not
a plateau. The damp axis is the fragile one: `100/0.0 → 0/1.0` costs 5 wins and
both bears.

Other frontier members: best pure-continuous (trend off) = 15/19, 2/3 bears,
turnover only **89%/yr**; best by real-win count = `tv 0.10, (10,40), wmax 0.45,
100/0.0, GLD .3/GDX .4/SMH .3` at **15 real wins, 3 real losses**, CAGR +29.7%,
maxDD −33.9%, turnover 135%/yr, gold-neutral margin +47.4pp.

---

## 5. The control that should stop this line of work

**A static 100% GLD book with no strategy at all scores 13/19 and 2/3 bears**
(GDX alone: 12/19, 2/3). P1's entire contribution over buying gold and holding
it is four wins and one non-negative bear, inside a replay whose win count is
±2. Best gold-free book in the whole grid: **11/19, 1/3 bears.**

## 6. Recommendation

The mechanism is a genuine improvement on iter4 — more wins, a much larger
asset-neutral margin, bears that clear zero by margins rather than by rounding,
lower turnover at the low-`tv` end — and it is still **not the brief**. The
18/19 + three-non-negative-bears target is now shown to be unreachable from
*both* ends of the one dial the family has: core = 0 forfeits ru2 and y23, core
> 0 forfeits 2022H1, and h1 is unwinnable at any setting. I do not recommend
shipping P1. If the target stands, it needs an instrument this strategy cannot
express — something that is long equity beta and defensively positioned at the
same time — not another point on this grid.

### doc-200 config JSON — P1

```json
{
  "strategy_eb_enabled": true,
  "core_symbol": "TQQQ",
  "core_leverage": 3.0,
  "reference_symbol": "QQQ",
  "off_symbol": "SPY",
  "cash_symbol": "BIL",
  "target_vol": 0.25,
  "core_max_weight": 0.45,
  "weight_step": 0.05,
  "vol_fast_bars": 10,
  "vol_slow_bars": 40,
  "min_history_bars": 70,
  "core_rebalance_band": 0.10,
  "rebalance_weekdays": [2],
  "remainder_bil_fraction": 0.0,
  "trend_filter_bars": 100,
  "trend_off_enter_pct": 0.01,
  "trend_on_exit_pct": 0.02,
  "risk_off_symbol": "",
  "core_off_damp": 0.0,
  "trend_on_book": {"GLD": 0.5, "GDX": 0.5},
  "trend_off_book": {"GLD": 0.5, "GDX": 0.5},
  "cash_sweep_min_pct": 0.02,
  "core_band_pct": 0.03,
  "min_order_usd": 25.0,
  "cost_haircut_pct": 0.005,
  "broker_max_single_position_pct": 0.95,
  "honour_single_position_cap": true
}
```

Battery `stocks`: `["TQQQ","QQQ","SPY","BIL","GLD","GDX"]`,
`granularity: "86400"`, `equity_cost_tiers: "etf-liquid"`.

*Scripts: `iter5/{core5,validate,sanity,search5,analyse5,extra5}.py`; logs in
`iter5/{search,analyse,extra}_log.txt`; raw grid in `rows.pkl`.*
