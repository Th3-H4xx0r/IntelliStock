# Strategy EB — the champion, and the ceiling above it

Closes the EB program. The search found a configuration that beats SPY on the
engine by a wide margin and is non-negative in all three bear windows. It did
not find one that beats SPY in 95% of rolling twelve-month windows, and three
independent walls say that target is unreachable in this instrument family.

Everything below is the ENGINE (`instance strategy-eb`, doc 200, daily,
$6,000, `equity_cost_tiers="etf-liquid"`), not a local harness.

## The champion

```json
{
  "strategy_eb_enabled": true,
  "core_symbol": "TQQQ", "core_leverage": 3.0, "reference_symbol": "QQQ",
  "off_symbol": "SPY", "cash_symbol": "BIL",
  "target_vol": 0.20, "core_max_weight": 0.65, "weight_step": 0.05,
  "vol_fast_bars": 10, "vol_slow_bars": 40, "min_history_bars": 70,
  "core_rebalance_band": 0.10, "rebalance_weekdays": [2],
  "remainder_bil_fraction": 0.0,
  "trend_filter_bars": 25,
  "trend_off_enter_pct": 0.01, "trend_on_exit_pct": 0.02,
  "risk_off_symbol": "", "core_off_damp": 0.0,
  "trend_on_book":  {"GLD": 0.5, "GDX": 0.25, "XLE": 0.25},
  "trend_off_book": {"GLD": 0.5, "GDX": 0.25, "XLE": 0.25},
  "cash_sweep_min_pct": 0.02
}
```
Universe `[QQQ, TQQQ, SPY, BIL, GLD, GDX, XLE]`. Both books are identical, so
the filter flips the CORE only — `core_off_damp 0.0` leaves the levered fund
entirely while QQQ is below its 25-day average, and the same three-name book
carries the remainder in both states.

`core_max_weight` is INERT here: 0.65, 0.85 and 1.00 all return +233.8418%
(bt 421091 / 309775 vs 781256, byte-identical). At `target_vol 0.20` the clamp
never binds, so the 0.65 in the config is a guard, not a tuned parameter.

## What the engine measured

| | champion | SPY-TR |
|---|---|---|
| cycle 2021-11-01 → 2026-08-27 | **+233.84%** (bt 781256) | +77.1% |
| CAGR over that cycle | +28.42% | +12.59% |
| max drawdown | **−27.54%** | −24.66% |
| rolling 12m win rate | **91.3%** month-end (42/46) · 92.6% on 252-session windows | — |
| rolling 6m / 3m | 75.0% / 63.6% | — |
| bear 2022-01→06 | **+0.55%** (bt 806182) | −19.4% |
| bear 2026-02→04 | **+0.35%** (bt 333118) | −5.8% |
| bear 2025-02→04 | **+6.18%** (bt 878859) | −11.4% |
| calendar 2022 (in-cycle) | −1.07% | −18.49% |
| turnover | 474%/yr, 320 trades | — |

Measured by `scripts/strategy_eb_gate.py 781256 --source pg`, which accrues the
engine's price-only SPY to total return at 1.25%/yr before comparing.

**It fails the frozen §11 gate on G5.** G1, G2, G3, G4 and G6 pass; turnover is
474%/yr against a 400% bound that was pre-registered before any engine run. The
gate is not re-tuned to pass, so the strategy ships DISABLED, per the XS
precedent. Two caveats on the table above, both against the champion's favour:

- The **+2.78% calendar-2022** figure quoted in earlier notes belongs to the
  SIBLING arm T1 (`target_vol 0.15`, `trend_filter_bars 40`, bt 717258) run as
  a standalone calendar year — not to the champion. The champion has no
  standalone 2022 run; its in-cycle 2022 is −1.07%.
- Replay-vs-engine error on this family runs +2.8 to +8.3pp optimistic on bear
  windows and ±7pp on rolling-12m (iter6 parent calibration). Bear margins of
  +0.35 and +0.55 are inside that band and carry no information beyond sign.

## Why 95% of rolling windows is unreachable — three walls

1. **The 2023 V-recovery needs bear-time equity.** The rolling windows that end
   in the 2023 recovery are measured from inside the 2022 selloff. Winning them
   requires holding equity through the bear that precedes them, which is
   exactly what bear-safety forbids. iter6 measured the conjunction directly:
   of 64,800 configurations, 1,432 clear 95% rolling and 28 clear 3/3 bears —
   and **zero** clear both. At min-bear ≥ 0 the best rolling was 79.2%.
2. **A slow gate cannot see the 2026 bear.** QQQ held above its 200-day average
   through ~79% of that window, so any slow trend filter stays ON through it.
   iter7 answered this by going fast: N ∈ {25…60} is a plateau, not a spike,
   and it moved the frontier's shortfall from 15.8pp to **1.5pp** — close, and
   still short.
3. **Buying the missing rolling wins with equity flips the bears.** sweep4 put
   15% and 25% of the ON book into SMH or QLD, four configurations, and every
   one turned the two nearest bears negative: rb1 −2.44 / −4.50 / −3.24 / −5.30
   and rb2 −0.49 / −0.97 / −0.93 / −1.69. Cycle margin barely moved
   (+233.3 / +252.4 / +231.3 / +245.0). The equity that wins the recovery
   windows is the equity that loses the bears.

## The era bet

The margin is a bet on gold and energy, in an era that rewarded them.

- Neutralising GLD/GDX to SPY collapsed iter2b's R1 from +159.6pp to **−0.2pp**
  (16/19 windows → 9/19) and R3 from +53.2pp to −0.2pp. iter5's vol-targeted
  core recovered some of that — +171.9pp → **+27.5pp** neutralised — and
  iter7's champion family keeps **+14.9pp** with gold AND energy neutralised.
  Real, and an order of magnitude smaller than the headline.
- **In the 2010-2021 design context this family loses to SPY.** SPY-TR
  +15.04%/yr; iter5's P1 +12.63%/yr; iter8's static book +9.44%/yr; iter2b's R1
  +12.27%/yr. None of the assets in the book were chosen with that decade in
  view, and none of the configurations beat a flat index there.

Do not read the cycle figure as an expectation. Read it as: in 2021-2026, a
de-levered TQQQ core with a gold/miners/energy remainder beat the index, and
2010-2021 says the same construction would have lost.

## Poison: what NOT to add to the book

- **UNG.** Across all 36 UNG-bearing configurations, UNG's compounded return
  over exactly the sessions it was selected is negative in **36 of 36**, −25.7%
  to −77.8%, median ≈ −59%, while held 16-36% of the time. Momentum buys the
  bounce and holds the contango decay.
- **SMH.** Worse than UNG as a book leg: bears down to −34.9%, maxDD −67.2%.
  It is a bull-window instrument and the program has no way to hold it only in
  bulls.
- **BIL.** Adds nothing — the best BIL-bearing menu reached 81.5% rolling-12m,
  below the champion's own three names. The winning menu is GLD/GDX/XLE.

## Iteration ledger

| # | what was searched | configs | outcome |
|---|---|---|---|
| tune | vol target × windows × band × dial × damp/chop variants, curve-fit to 9 engine windows | 423,360 | 9/9 = **0**; ceiling 7/9; the 7/9 winners scored 1/5 on holdout — fit and holdout ANTI-correlated |
| 1 | trend-conditioned remainder, SPY/BIL/GLD occupants | 5,184 | 0 bear-safe; best 15/19; chop1 breaks only with GLD, N=100, x ≤ 1%; across 46 OFF episodes GLD beat SPY in 23 — a coin flip |
| 2 | asset menu and reachable ceiling, 37 assets | 4,896 (switch family) | bull windows bind (7-9 of 37 assets beat SPY); only BIL is positive in all 3 bears; 18/19 in 8 configs, bear-positive in 3, **both in 0** |
| 2b | the three headline rules under the engine's real cost tiers | 3 rules | only R1 survives costs; gold-neutralised it falls to 9/19 at −0.2pp; a STATIC SMH/GLD/GDX blend scores 18/19 with no signal at all |
| 3 | (implementation) configurable `trend_on_book` / `trend_off_book`, commit 90fe17e | — | 34 config keys, empty book ≡ the previous two-leg remainder |
| 4 | book × signal search for 18/19 + three non-negative bears | 15,540 | target unreachable; bear-safe ceiling 15/19; 4 windows lost by 100% of bear-safe configs; gold swap collapses +107.4pp → +3.5pp |
| 5 | vol-targeted TQQQ core + static remainder book | 13,536 | 0 pass; the core FIXES the 2026 bull and cal-2023 and COSTS bear-safety; max 17/19 at 0/3 bears |
| 6 | core × trend filter driving both damp and a state-dependent book | 64,800 | 0 pass; **first 3/3 bear-safe configs (28)**, all ON≠OFF, all carrying XLE in the OFF book; frontier 15.8pp short of 95% |
| 7 | fast core-only damp × XLE-admitted static book | 19,840 | 0 pass; N ∈ {25…60} is a plateau; shortfall 15.8pp → **1.5pp**; produced the champion's family |
| 8 | momentum-rotated book on the confirmed champion | 72 | 0 pass; 96% of the gain is gold concentration; M=126 is a 6.6pp spike between worse neighbours; doubles turnover; **worse out of design sample** |

Search artifacts (grids, per-window batteries, logs) are session-scratch and
were not committed; the engine backtest ids above are the durable evidence.

## Disposition

`strategy_eb_enabled` stays **false**. The champion config is recorded here, not
in `DEFAULTS` — shipping it as a default would put a 474%/yr-turnover era bet
behind a flag anyone could flip. The three walls close the 95% objective; do not
re-open it with equity in the book, a slower gate, or a momentum-rotated book.
