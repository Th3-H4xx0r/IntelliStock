# Strategy EB — explicit curve-fit against the 9 engine windows

Replay: `eb.py` (vectorised, 423,360 configs). Daily closes, yfinance `auto_adjust=True`,
QQQ/TQQQ/SPY/BIL. Decide at close *t* from closes ≤ *t*, execute at close *t+1*.
Cost 4.4 bps per one-way |Δ| on every ETF leg. Cash sweep (>2% NAV → remainder legs)
reproduced. No lookahead.

Grid: tv 0.10–0.30 ×0.01 (21) · fast {5,10,15,20} · slow {20,40,60,90} · band {0.05,0.10,0.15}
· wmax {0.45,0.65,0.85} · step 0.05 · weekday {0..4} · remainder_bil_fraction {0,0.25,0.5,1.0}
· variant {none, damp50, damp100, damp150, damp200, chop2, damp100+chop2} = **423,360 configs**.

`damp*` = w ×0.5 when QQQ < its N-day SMA. `chop2` = w ×0.5 when |QQQ/SMA100 − 1| < 2%.
**Neither exists in `backend/strategy_eb.py` today — both would need new code to ship.**

## Window calibration

Engine SPY-TR reproduced to 0.12 pp mean absolute error using: base = last close strictly
before `start`; final mark = (last close ≤ `end`) − 1 session. Every other end convention
was worse (e.g. plain last-close-≤-end misses chop3 by 2.2 pp). Win test uses the
**double hurdle**: EB must beat both the replay SPY-TR and the engine's stated figure.

| window | dates | engine SPY-TR | replay SPY-TR | err |
|---|---|---|---|---|
| bear1 | 2021-12-31→2022-06-29 | −19.40 | −19.33 | −0.07 |
| bear2 | 2026-01-30→2026-03-31 | −5.84 | −5.76 | −0.08 |
| bear3 | 2025-02-14→2025-04-14 | −11.40 | −11.31 | +0.09 |
| bull1 | 2022-12-30→2023-07-28 | +20.32 | +20.38 | +0.06 |
| bull2 | 2026-03-31→2026-05-29 | +16.58 | +16.32 | −0.26 |
| bull3 | 2023-12-29→2024-06-27 | +15.64 | +15.68 | +0.04 |
| chop1 | 2025-11-07→2026-02-23 | +2.08 | +2.00 | −0.08 |
| chop2 | 2022-06-30→2022-12-29 | +2.24 | +2.53 | +0.29 |
| chop3 | 2024-06-28→2024-10-30 | +7.03 | +6.90 | −0.13 |

## 1. All-9 winners: ZERO. Ceiling is 7/9.

| wins | configs (double hurdle) |
|---|---|
| 9/9 | **0** |
| 8/9 | **0** |
| 7/9 | 3 |
| 6/9 | 15,515 |
| 5/9 | 65,790 |
| ≤4/9 | 342,052 |

Per-window winner counts (of 423,360):

| window | winners | % | best EB | worst EB |
|---|---|---|---|---|
| bull1 | 399,731 | 94.4% | +90.58 | +9.31 |
| bull3 | 395,073 | 93.3% | +49.00 | +5.27 |
| bull2 | 367,452 | 87.5% | +51.73 | +4.92 |
| bear1 | 159,439 | 37.7% | +3.26 | −42.32 |
| bear2 | 119,896 | 28.3% | +1.06 | −18.02 |
| bear3 | 71,467 | 16.9% | +0.58 | −31.26 |
| **chop2** | **147** | 0.035% | +4.98 | −17.72 |
| **chop3** | **67** | 0.016% | +8.40 | −16.68 |
| **chop1** | **18** | 0.004% | +3.51 | −15.38 |

**8/9 is impossible by construction.** The three chop winner sets are pairwise **disjoint**
(chop1∩chop2 = 0, chop1∩chop3 = 0, chop2∩chop3 = 0). No config can win more than one chop
window, so the ceiling is 3 bulls + 3 bears + 1 chop = **7**.

### The binding constraint (arithmetic)

The family is structurally always-long: `w·TQQQ + (1−w)·[dial·BIL + (1−dial)·SPY]`, w ≥ 0.
A static blend can beat SPY only if TQQQ > SPY (or BIL > SPY). Leg returns:

| window | SPY | TQQQ | BIL | TQQQ−SPY | SPY−BIL | best static blend | bound margin |
|---|---|---|---|---|---|---|---|
| bear1 | −19.33 | −70.08 | +0.11 | −50.76 | −19.44 | +0.11 (w=0, dial=1) | **+19.44** |
| bear2 | −5.76 | −22.69 | +0.56 | −16.93 | −6.32 | +0.56 (w=0, dial=1) | **+6.32** |
| bear3 | −11.31 | −44.62 | +0.65 | −33.31 | −11.96 | +0.65 (w=0, dial=1) | **+11.96** |
| bull1 | +20.38 | +163.74 | +2.63 | +143.36 | +17.75 | +142.24 (w=.85, dial=0) | +121.86 |
| bull2 | +16.32 | +102.88 | +0.61 | +86.56 | +15.71 | +89.90 | +73.57 |
| bull3 | +15.68 | +49.03 | +2.56 | +33.35 | +13.12 | +44.03 | +28.35 |
| **chop1** | +2.00 | −8.65 | +1.04 | **−10.65** | **+0.96** | +2.00 (w=0, dial=0) | **+0.00** |
| **chop2** | +2.53 | −27.54 | +1.29 | **−30.07** | **+1.24** | +2.53 (w=0, dial=0) | **+0.00** |
| **chop3** | +6.90 | +2.16 | +1.72 | **−4.75** | **+5.18** | +6.90 (w=0, dial=0) | **+0.00** |

In each chop window TQQQ < SPY **and** BIL < SPY simultaneously. Both terms of
`EB − SPY = w·(TQQQ−SPY) + (1−w)·dial·(BIL−SPY) − cost` are ≤ 0 for every admissible
(w, dial). The static-blend optimum over the whole grid is exactly SPY, attained only at
w = 0, dial = 0 — a book the family cannot hold, and costs then make it strictly negative.
Bear windows have a +6 to +19 pp escape hatch (BIL > SPY); chop windows have **zero**.

Every chop "win" is therefore pure intra-window weight *timing*, not a blend that works.
Evidence: chop3's 67 winners are **100% weekday=4**, while chop3's mean margin by weekday
is Mon −7.84, Tue −8.92, Wed −9.70, Thu −8.26, Fri −5.96 pp — the whole family is 6–10 pp
under SPY and one rebalance phase catches a favourable sequence. chop1's 18 winners are all
fast=5, band=0.15, and require the `chop2` brake.

**chop1 is the least winnable** (18/423,360 = 0.004%, median margin −5.82 pp).

### The 7/9 configs (all three; wmax is inert — the cap never binds at tv=0.12 with damping)

`tv=0.12, fast=5, slow=90, band=0.05, wmax∈{0.45,0.65,0.85}, weekday=0 (Mon), dial=0.50, variant=damp100+chop2`

bear1 −14.83 (+4.49) · bear2 −4.40 (+1.36) · bear3 −8.03 (+3.28) · bull1 +24.69 (+4.31) ·
bull2 +18.32 (+1.74) · bull3 +15.75 (**+0.07**) · chop1 +0.61 (−1.47) · chop2 +2.69 (+0.16) ·
chop3 −1.42 (−8.45)

Three more reach 7/9 against the replay hurdle only (`slow=20` instead of 90); they miss
bull2 by 0.06 pp against the engine's +16.58.

### Best 5 by count, then min margin (6/9 tier)

| tv | fast | slow | band | wmax | wd | dial | variant | min margin | lost | full CAGR | maxDD |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 0.11 | 5 | 40 | 0.05 | any | 4 | 0.50 | damp50 | −3.41 | all 3 chop | +13.62% | −16.3% |
| 0.11 | 5 | 20 | 0.05 | any | 0 | 0.50 | damp50 | −3.63 | all 3 chop | +13.27% | −16.6% |
| 0.11 | 5 | 40 | 0.10 | any | 4 | 0.50 | damp150 | −3.70 | all 3 chop | +12.73% | −17.1% |
| 0.11 | 5 | 20 | 0.15 | any | 0 | 0.25 | damp100+chop2 | −3.80 | all 3 chop | — | — |
| 0.12 | 5 | 90 | 0.05 | any | 0 | 0.50 | damp100+chop2 | −8.45 (7/9) | chop1, chop3 | +12.01% | −18.0% |

## 2. Full cycle + holdout for the 7/9 winners

Full cycle 2021-10-29 → 2026-08-26 (4.82 y). **SPY-TR: +77.91% total, +12.68% CAGR, −24.50% maxDD.**

| config | CAGR | maxDD | turnover | holdout |
|---|---|---|---|---|
| **7/9 winner** (tv .12 / 5 / 90 / band .05 / Mon / dial .50 / damp100+chop2) | **+12.01%** | −18.0% | **418%/yr** | **1/5** |
| shipped default (tv .20 / 20 / 60 / band .10 / Wed / dial 0 / none) | +16.45% | −41.5% | 105%/yr | 2/5 |
| best 6/9 (tv .11 / 5 / 40 / band .05 / Fri / dial .50 / damp50) | +13.62% | −16.3% | 332%/yr | 1/5 |

Holdout for the 7/9 winner (never seen during the search):

| holdout | SPY-TR | EB | result |
|---|---|---|---|
| 2021-11-01→2021-12-31 | +4.05 | +2.42 | **LOSS** |
| 2023-08-01→2023-10-31 (correction) | −8.90 | −8.02 | WIN |
| 2025-05-01→2025-10-31 | +23.30 | +20.05 | **LOSS** |
| 2024-11-01→2025-02-14 | +7.59 | +6.96 | **LOSS** |
| 2026-06-01→2026-08-27 | +1.53 | −4.78 | **LOSS** |

**1 of 5.** It also loses the full cycle on CAGR (+12.01 vs +12.68) — the fitted config beats
SPY in 7 of 9 hand-picked windows and still trails SPY over the period containing them.
Mean holdout wins: whole grid 1.88/5; bear+bull sweepers 1.44/5; the 7/9 set **1.00/5** — the
better a config fits the 9 windows, the *worse* it does out of sample. **No config in 423,360
wins 5/5 holdout**; only 1,756 (0.4%) win 4/5.

## 3. Sensitivity of the single best config — knife-edge

Base 7/9, sum of margins +5.5 pp. Each axis moved ±1 grid step:

| axis | change | wins | Δ |
|---|---|---|---|
| wmax | 0.45 or 0.85 | 7/9 | inert (cap never binds) |
| tv | 0.13 | 6/9 | −1 |
| tv | 0.11 | **5/9** | −2 |
| fast | 10 | 6/9 | −1 |
| slow | 60 | 6/9 | −1 |
| band | 0.10 | **4/9** | −3 |
| weekday | 1 / 2 / 3 / 4 | 6/9 | −1 (every one) |
| dial | 0.25 | **5/9** | −2 |
| dial | 1.00 | **4/9** | −3 |
| variant | damp100 (drop the chop brake) | 6/9 | −1 |
| variant | chop2 (drop the trend damp) | **5/9** | −2 |
| variant | none | **4/9** | −3 |

**Every non-inert perturbation loses at least one window.** 1 of 12 real neighbours holds
7/9, and that one (wmax) changes nothing in the replay. This is a single point, not a plateau.
The tightest binding margins confirm it: bull3 +0.07 pp and chop2 +0.16 pp — two windows are
won by less than one rebalance's worth of cost.

## 4. Verdict

**No.** There is no configuration that wins all 9, and none that wins 8. The ceiling is 7/9,
reached by exactly one parameter point (×3 inert wmax duplicates), it sits on a knife edge,
it goes 1/5 on holdout, and it trails SPY over the full cycle.

The frontier, in plain terms:

- **Bears and bulls together are easy.** 15,485 configs (3.7%) win all three bears *and* all
  three bulls. That is the real, mechanically-sound result: a vol-targeted core with a
  half-BIL remainder cuts a −19% bear to −13% and still clears a +20% bull because TQQQ's
  excess over SPY is +143 pp there.
- **Chop is the wall.** The best any bear+bull sweeper does on its worst chop window is
  **−3.41 pp**. To close that gap the strategy would have to hold less than 0% TQQQ — a short
  or an inverse leg — which the family does not have and which the design doc already KILLed
  (bottom detector n=77, t = −0.95).
- **The trade-off is dial vs bulls.** dial=0.50 is forced: dial→0.25 gives up bear2, dial→1.0
  gives up all three bulls. Raising tv buys bulls and loses bears one-for-one.

Recommendation: do **not** spend an engine confirmation run on the 7/9 point. If one is spent,
spend it on the *mechanism* rather than the fit — `tv=0.11, fast=5, slow=40, band=0.05,
wmax=0.65, weekday=4, dial=0.50, variant=damp50` (6/9, worst margin −3.41 pp, CAGR +13.62%
vs SPY +12.68%, maxDD −16.3% vs SPY −24.50%). It beats SPY on the full cycle at two-thirds
the drawdown, and its three losses are the three windows that are provably unwinnable.
Its cost is 332%/yr turnover, above the 50%/mo Novy-Marx line already flagged in this repo — and
it is still only **1/5 on holdout** (2021-11→12 +2.32 vs +4.05 L; 2023-08→10 −6.48 vs −8.90 W;
2025-05→10 +21.78 vs +23.30 L; 2024-11→2025-02 +6.74 vs +7.59 L; 2026-06→08 −1.88 vs +1.53 L).
Four of those five losses are the same shape: EB gives up 1.5–2.5 pp of a rising tape to buy the
drawdown protection it delivers in bears. That is the honest description of this whole family —
a risk transform, priced in CAGR, not an alpha — which is exactly what its own design doc says.
