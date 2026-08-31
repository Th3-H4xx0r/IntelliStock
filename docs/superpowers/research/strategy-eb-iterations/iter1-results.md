# iter1 — trend-conditioned remainder (risk-on SPY / risk-off BIL·GLD)

Replay: `eb2.py`, an extension of `../tune/eb.py`. **Validated**: with the trend state forced
permanently ON, `eb2.py` reproduces `tune/eb.py` to machine precision (max |Δ| = 0.000 pp across
all nine windows), and SPY-TR reproduces the engine's stated figures to 0.08–0.29 pp under the
same window convention (base = last close strictly before `start`; final = (last close ≤ `end`) − 1
session). Decide close *t*, execute close *t+1*; floor-quantised w (step 0.05, cap 0.85); band 0.10;
Wednesday cadence; cash sweep; 4.4 bps per one-way |Δ| on every leg (TQQQ, SPY, BIL, GLD).

Grid = **5,184 configs**: tv {0.12, 0.15, 0.20} × (fast,slow) {(10,40),(20,60)} × N {100,150,200} ×
x {0,1,2,3}% × y {0,1,2,3}% × {single, dual-SMA} × occupant {BIL, GLD, 50/50} × off-damp {1.0, 0.5, 0.0}.
The dial is gone. Data: yfinance `auto_adjust` daily from 2004; TQQQ before 2010-02-11 is synthetic
(3·rQQQ − 2·rBIL − 0.95%/yr expense, then de-biased by the +0.73 bps/day optimism measured on the
2010-2026 overlap; daily corr 0.9989). Everything before 2021 is design context, not verifiable.

---

## 1. Does anything reach the operator's three goals?

**No — two of the three are unreachable in this family, and the third is comfortably reached.**

| goal | target | best achieved | verdict |
|---|---|---|---|
| (a) win-rate vs SPY-TR | ≥ 95% of 19 windows (≥ 18) | **15/19 (78.9%)**, 26 configs | **fail** |
| (b) bear windows absolute > 0 | all three > 0 | best *worst-bear* = **−8.96%**; best single bear = −1.10% | **fail (0 of 5,184)** |
| (c) full-cycle CAGR − SPY-TR | ≥ +3 pp | **+7.36 pp** best; 1,480 configs (28.6%) clear +3 pp | **pass** |

Win distribution over the 19 windows: 15/19 (26), 14 (52), 13 (227), 12 (454), 11 (689), 10 (961),
9 (1,090), 8 (931), ≤7 (754). In-sample-9 distribution: **7/9 is again the ceiling** — 60 configs,
up from 3 in the previous 423k-config search, and all 60 lose *exactly* chop2 + chop3.

Per-window winner counts (double hurdle on the nine: must beat both replay SPY-TR and the engine's figure):

| window | hurdle | winners / 5,184 | best EB | best margin |
|---|---|---|---|---|
| bull3 | +15.68 | 5,184 (100.0%) | +34.68 | +19.00 |
| h4 2024-11→2025-02 | +7.59 | 5,184 (100%) | +8.98 | +1.40 |
| y2024 | +25.34 | 4,970 (95.9%) | +46.70 | +21.35 |
| bull1 | +20.38 | 4,736 (91.4%) | +54.41 | +34.03 |
| y2023 | +26.54 | 4,552 (87.8%) | +66.43 | +39.89 |
| h3 2025-05→10 | +23.30 | 4,320 (83.3%) | +34.66 | +11.36 |
| bull2 | +16.58 | 4,132 (79.7%) | +35.33 | +18.75 |
| bear1 | −19.33 | 4,032 (77.8%) | −8.33 | +11.00 |
| cycle | +77.91 | 3,637 (70.2%) | +141.41 | +63.51 |
| bear2 | −5.76 | 1,516 (29.2%) | −1.93 | +3.83 |
| bear3 | −11.31 | 1,392 (26.9%) | −1.10 | +10.21 |
| h2 2023-08→10 | −8.90 | 320 (6.2%) | −1.66 | +7.24 |
| **chop1** | **+2.08** | **256 (4.9%)** | **+6.33** | **+4.25** |
| **chop2** | +2.53 | **0** | +1.25 | −1.27 |
| **chop3** | +7.03 | **0** | +3.04 | −3.99 |
| **h1** 2021-11→12 | +4.05 | **0** | +3.14 | −0.91 |
| **h5** 2026-06→08 | +1.53 | **0** | −2.03 | −3.56 |

Four windows are unwinnable by every config in the grid. 95% of 19 requires losing at most one —
so the goal is dead on arrival, and the trade-off surface is: **chop2, chop3, h1, h5 cost four
windows no matter what; the remaining 15 are simultaneously winnable.**

---

## 2. Does the trend-conditioned remainder break the chop wall? **Partly — one window of three.**

The wall in the previous iteration was arithmetic: in every chop window TQQQ < SPY *and* BIL < SPY
simultaneously, so `EB − SPY = w(TQQQ−SPY) + (1−w)·dial·(BIL−SPY) − cost ≤ 0` for all admissible
(w, dial). GLD is the term that can be positive.

| window | SPY | TQQQ | BIL | GLD | GLD−SPY | BIL−SPY | winnable? |
|---|---|---|---|---|---|---|---|
| chop1 2025-11→2026-02 | +2.00 | −8.65 | +1.04 | **+30.67** | **+28.67** | −0.96 | **yes, 256 configs** |
| chop2 2022-07→12 | +2.53 | −27.54 | +1.29 | +0.23 | −2.30 | −1.24 | no |
| chop3 2024-07→10 | +6.90 | +2.16 | +1.72 | **+19.76** | **+12.86** | −5.18 | no (state stayed ON) |

- **chop1 breaks.** Winner count goes from 18-in-423,360 (0.004%, pure rebalance-phase luck) to
  256-in-5,184 (4.9%), best margin +3.51 → +4.25 pp. Every one of the 256 winners is **occupant = GLD,
  N = 100, x ≤ 1%** — no BIL winner, no 50/50 winner, no N=150/200 winner. The trend went OFF over
  2025-11→2026-02 and gold ran +30.7% against SPY's +2.0%. That is the mechanism working exactly
  as designed, not timing luck.
- **chop2 does not break, and cannot.** GLD was −2.30 pp *under* SPY there. The best any occupant
  can do is BIL at +1.29 vs SPY +2.53. Best-in-grid is +1.25 — i.e. the ceiling is SPY − 1.27 pp,
  and this window is structurally lost.
- **chop3 does not break for a different reason.** GLD beat SPY by +12.86 pp, but QQQ held above
  SMA(100/150/200) through almost the whole window (only the 2024-08-05 spike dipped it), so the
  state never turned OFF long enough to own gold. The remainder sat in SPY while the TQQQ core
  (+2.16 vs SPY +6.90) bled. The old family "won" chop3 with 67 configs that were **100% Friday**
  rebalancers — phase luck this Wednesday-only grid correctly does not reproduce.

**Net: the occupant switch converts chop from 3 structural losses to 1 mechanical win, 1 structural
loss, and 1 detection failure.** It also flips h2 (the 2023 correction) from unwinnable-in-practice
to 320 winners.

The mechanism test over the whole 2007-06 → 2026-08 sample (4,840 days, config A's state path):

| state | days | GLD ann. | SPY ann. | GLD−SPY ann. | t-stat |
|---|---|---|---|---|---|
| OFF | 1,143 (23.6%) | +21.45% | +5.22% | **+16.23%** | **+0.91** |
| ON | 3,697 (76.4%) | +8.22% | +14.31% | −6.09% | −1.12 |

Sign is right, magnitude is large (+22.3%/yr spread of the spread), **significance is absent**.
Episode level is worse: across **46 OFF episodes GLD beat SPY in 23 — exactly 50%** — median
−0.12 pp, mean +1.68 pp, and the mean is carried by a single episode (2008-09-03→2009-03-17,
+51.75 pp). Take that one episode out and the edge is a coin flip.

---

## 3. Top-5 (selected on in-sample-9 + calendar years ONLY; holdout reported after the fact)

All five: `(fast,slow)` on QQQ log-vol, `N` = SMA length, `x/y` = OFF/ON hysteresis, occupant, off-damp.
The dual-SMA variant is **completely inert** — QQQ < SMA(50) whenever QQQ < SMA(N)·(1−x) on a
Wednesday, for every N in the grid — so it never appears as a distinguishing axis.

| # | config | in-9 | 19 | cyc CAGR | Δ vs SPY | maxDD | turn/yr | worst bear |
|---|---|---|---|---|---|---|---|---|
| **A** | 10/40, tv 0.15, N100, x 1%, y 2%, GLD, damp **0.0** | **7/9** | **15** | **+19.20%** | **+6.51** | **−22.4%** | 695% | −10.19 |
| B | 10/40, tv 0.20, N100, x 1%, y 2%, GLD, damp 0.0 | 7/9 | 15 | +19.87% | +7.18 | −25.4% | 788% | −11.39 |
| C | 20/60, tv 0.15, N100, x 1%, y 2%, GLD, damp 0.0 | 7/9 | 15 | +17.35% | +4.67 | −22.3% | 665% | −10.19 |
| A′ | 10/40, tv 0.15, N100, x 1%, y 2%, GLD, damp **0.5** | 6/9 | 14 | +18.22% | +5.53 | −26.6% | 669% | −14.25 |
| D | 10/40, tv 0.15, N100, x 1%, y 2%, **50/50**, damp 0.0 | 5/9 | 13 | +15.90% | +3.21 | −19.1% | 695% | −9.56 |

SPY-TR over the cycle: **+77.91%, +12.68% CAGR, −24.50% maxDD.**

Window detail (A / B / C / A′ / D vs the double hurdle):

| window | hurdle | A | B | C | A′ | D |
|---|---|---|---|---|---|---|
| bear1 | −19.33 | −10.19 W | −11.39 W | −10.19 W | −14.25 W | −9.56 W |
| bear2 | −5.76 | −5.24 W | −5.24 W | −5.24 W | −6.66 · | −3.71 W |
| bear3 | −11.31 | −2.20 W | −4.40 W | −2.20 W | −5.69 W | −6.62 W |
| bull1 | +20.38 | +34.29 W | +42.08 W | +32.92 W | +36.07 W | +31.30 W |
| bull2 | +16.58 | +16.67 W | +20.89 W | +16.67 W | +16.67 W | +16.44 · |
| bull3 | +15.68 | +30.09 W | +34.68 W | +26.82 W | +30.09 W | +30.09 W |
| chop1 | +2.08 | +5.19 W | +4.12 W | +5.79 W | +4.10 W | +0.97 · |
| chop2 | +2.53 | −1.52 · | −2.52 · | −0.53 · | −2.60 · | −3.88 · |
| chop3 | +7.03 | −2.17 · | −5.20 · | −3.26 · | −1.42 · | −3.22 · |

**Holdout (never used for selection).** Whole-grid distribution: 0/5 → 0 configs, 1/5 → 800 (15.4%),
2/5 → 4,128 (79.6%), **3/5 → 256 (4.9%), 4/5 and 5/5 → 0**. Grid mean 1.895/5. All five picks score
**3/5 — the grid's ceiling.** And unlike the previous search, `corr(in-sample-9 wins, holdout wins) =
**+0.180**` (7/9 tier averages 2.53/5 vs the grid's 1.90) — the inverse fit/holdout relation from the
last iteration has **reversed**. That is the single most encouraging statistic here.

| holdout | SPY | A | B | C | A′ | D |
|---|---|---|---|---|---|---|
| 2021-11→12 | +4.05 | +2.73 · | +2.11 · | +2.59 · | +2.73 · | +2.73 · |
| 2023-08→10 | −8.90 | −2.20 W | −2.46 W | −2.20 W | −3.65 W | −5.13 W |
| 2025-05→10 | +23.30 | +28.40 W | +31.46 W | +25.36 W | +28.40 W | +28.41 W |
| 2024-11→2025-02 | +7.59 | +8.73 W | +8.98 W | +8.42 W | +8.73 W | +8.73 W |
| 2026-06→08 | +1.53 | −4.66 · | −6.42 · | −4.55 · | −4.17 · | −6.15 · |

Calendar years: A −12.13 / +41.37 / +34.30 / +33.08 (2022–25) vs SPY −17.96 / +26.54 / +25.34 / +18.60 —
**A wins all four years and the full cycle, 5/5.** So do B, C, A′, D.

**Design context (pre-engine, yfinance only; TQQQ synthetic in 2008).** This is where it turns:

| | SPY | A | B | C | A′ | D |
|---|---|---|---|---|---|---|
| 2008 | −37.68 | −30.89 | −34.11 | −32.21 | −32.15 | −26.29 |
| **2011** | **+2.40** | **−27.76** | **−33.87** | **−28.42** | **−24.46** | **−26.90** |
| 2015 | +2.26 | −9.09 | −10.52 | −8.67 | −5.57 | −9.28 |
| 2018 | −5.40 | −2.29 | −6.94 | −2.65 | −4.74 | −4.91 |
| 2020 | +17.73 | +11.66 | +13.01 | +6.61 | +11.66 | +7.80 |
| 2010-21 CAGR | **+15.07%** | **+14.92%** | +16.31% | +13.44% | +16.71% | +13.29% |
| 2010-21 maxDD | −33.7% | −38.5% | −42.6% | −38.7% | −38.5% | −36.3% |

**A loses the 2010-2021 era to a flat SPY, at a worse drawdown.**

---

## 4. The config I would take to the engine

```
strategy      : EB + trend-conditioned remainder
vol target tv : 0.15          fast/slow : 10 / 40 (QQQ daily log-ret stdev, annualised, max of the two)
w             : floor_0.05( clip( tv / (3 · rv), 0, 0.85 ) )
band          : 0.10          cadence   : Wednesday        min history: 70 sessions
trend         : QQQ vs SMA(100), evaluated on decision days only
                ON  -> OFF  when QQQ < SMA100 · 0.99   (x = 1%)
                OFF -> ON   when QQQ > SMA100 · 1.02   (y = 2%)
                dual-SMA    : OFF (inert)
occupant      : trend ON  -> remainder (1−w) into SPY
                trend OFF -> remainder (1−w) into GLD
off-damp      : 0.50   (w is multiplied by 0.5 before quantisation while the state is OFF)
```

That is **A′, not A.** A is the in-sample winner (7/9, 15/19, −22.4% maxDD) but its whole advantage
over A′ comes from `off-damp = 0.0`, and off-damp is the one axis that **flips sign between eras**:

| off-damp | cycle CAGR | cycle maxDD | 2010-21 CAGR | 2010-21 maxDD | 2007-26 CAGR |
|---|---|---|---|---|---|
| 0.0 (A) | +19.20% | −22.4% | **+14.92%** | −38.5% | +14.31% |
| 0.5 (A′) | +18.22% | −26.6% | **+16.71%** | −38.5% | +15.23% |
| 1.0 | +18.64% | −29.3% | **+19.53%** | −38.5% | +16.66% |
| SPY-TR | +12.68% | −24.5% | +15.07% | −33.7% | +10.74% |

Over 2010-2021 the full core cut bought **zero** drawdown protection (all three variants hit −38.5%,
set by the 2020 COVID crash, which was too fast for a weekly SMA switch) and cost **4.6 pp/yr of CAGR**.
A′ is the plateau interior: it beats SPY-TR in *every* era measured (+1.6 pp/yr 2010-21, +5.5 pp/yr
2021-26, +4.5 pp/yr 2007-26) rather than only in the one it was fitted on. If the operator wants the
in-sample maximum instead, take A — it is one grid step away and the code is identical.

**New code required in `backend/strategy_eb.py`** (three additions, all in the weight/allocation path):

1. **Persistent trend state** — a two-state machine on `QQQ close vs SMA(100)`, updated only on
   decision days, with asymmetric thresholds `off_threshold_pct = 1.0`, `on_threshold_pct = 2.0`.
   It must persist across ticks (a new `risk_state` field on the instance row), because
   re-deriving it from a cold start changes the path. Initialise on first run to `QQQ > SMA100`.
2. **Occupant switch** — replace the fixed `remainder_bil_fraction` dial with
   `remainder_symbol = SPY if state_on else risk_off_symbol`, `risk_off_symbol = "GLD"`. This adds
   GLD to the tradeable universe and to the price/bar feed.
3. **Off-damp** — `w *= off_damp` (0.5) *before* the 0.05 floor-quantisation, while the state is OFF.
4. Rebalance trigger must fire on **`|w_target − w_held| ≥ band` OR `state ≠ last_executed_state`** —
   without the second clause the occupant rotation is silently skipped inside the band.

**Cost warning.** Turnover is **669%/yr one-way (≈56%/month)**, against 158%/yr for the same vol
target with no trend switch. The switch itself is ~460%/yr — 92 state flips over 2007-2026, each
rotating the entire ~70% remainder from SPY to GLD and back. At 4.4 bps that is 2.9%/yr already
charged in every number above, and it sits right on the Novy-Marx 50%/month line this repo has
flagged before. Raising `x` to 2% cuts flips 92 → 80 and turnover to 589%/yr, at the price of one
in-sample window.

**Sensitivity (±1 step around A′):** 6/9 holds under fast/slow 20/60, x = 0%, y = 3%, dual-SMA,
damp 0.0 (which improves to 7/9), damp 1.0, tv 0.20, N = 150, x = 2%, x = 3% — **12 of 16 neighbours
hold or improve the win count, and none falls below 4/9.** For A the same test holds 7/9 under six
neighbours (tv 0.20, 20/60, x = 0%, y = 1%, y = 3%, dual-SMA). Compare the previous iteration's 7/9 point, where *every*
non-inert perturbation lost a window. **This is a plateau, not a knife edge.** The two axes that do
bite are `occupant` (BIL costs 3 in-sample windows and 7 pp/yr of cycle CAGR) and `N` (150/200 lose
chop1 outright).

---

## 5. Honest overfit assessment

**The mechanism is real and the magnitude is not trustworthy.** Four things say "real": the chop1
winners are 100% GLD-occupant with a coherent N/x signature rather than a weekday phase; the
in-sample/holdout correlation is **+0.18, reversed from last iteration's inverse**; the top picks
sit at the grid's holdout ceiling (3/5) rather than below it; and ±1-step sensitivity is a plateau.

Five things say "do not trust the number":

1. **The entire cycle margin is gold.** Same config, occupant = BIL instead of GLD: +76.67% over the
   cycle against SPY's +77.91% — a **loss**. GLD contributes **+56.65 pp** of the +55.40 pp margin.
   There is no result here without gold, and 2024-2026 was gold's best run in forty years
   (+25.9% in 2024, +64.7% in 2025).
2. **The conditional edge is one episode.** GLD beat SPY in 23 of 46 OFF episodes (50%), median
   −0.12 pp. The +16.2%/yr OFF-state edge carries t = +0.91. The four episodes above +10 pp are
   2008-01, 2008-09 (+51.8), 2016-01, 2018-10 — three of them in the synthetic-TQQQ or
   pre-verifiable era.
3. **The trend switch is net-negative outside the fitted window.** Same config with the state forced
   permanently ON: **+1,213.5% over 2010-2021 vs +430.5% with the switch** (−783 pp), and 2011 goes
   −0.51% → **−27.76%** — a year SPY made +2.40% and QQQ +3.77%. The switch pays +32 pp over
   2021-2026 and destroys two-thirds of the return over 2010-2021. **n = 1 favourable regime.**
4. **2011 is the whipsaw wall reasserting itself**, exactly as the prior brief warned. 10 flips,
   1,859%/yr turnover, −36.8% drawdown, and 1%/2% hysteresis was not enough. The whole grid loses
   2011 (best −4.88 vs SPY +2.40, median −19.93).
5. **Bears still lose money.** Zero of 5,184 configs are positive in all three bear windows; the
   best *worst-bear* is −8.96%. Goal (b) is not close, and it will not be reached by a long-only
   blend whose only risk-off asset is a coin-flip hedge.

Discipline check: 5,184 configs, 15 windows tested for selection, top pick on a 10-of-16 plateau,
holdout used only for reporting. The fit count is small by design and the selection did not degrade
holdout. But the honest read is that **this iteration bought one chop window (chop1) and a +5.5 pp/yr
cycle margin with a gold hedge whose conditional edge is statistically indistinguishable from zero
and whose historical support is a single 2008 episode.** An engine run on A′ will confirm the replay;
it will not confirm the edge. If the operator wants the edge tested rather than the code, the run
that matters is A′ *versus* the same config with `risk_off_symbol = BIL` — that isolates the entire
claim into one A/B, and this repo has already shown cold-start A/Bs are byte-reproducible.
