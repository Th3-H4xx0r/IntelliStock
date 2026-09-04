# Short-window pre-registration — Strategy EB family (frozen 2026-09-03, before any run)

## Why this document exists

The operator's goal moved from "beat SPY over 3–5-year windows" to "beat SPY in every
3–6-month window in every regime and be positive in every bear window". Measured on the
engine's own 2021-11→2026-08 price path, that literal goal is satisfied only by perfect
5-day foresight (96% / 100% / 90%); every real construction moves along a frontier where
bear-window positivity is bought with bull-window underperformance, and the champion
family already sits on it. A four-voice council (Architect + Skeptic, Pragmatist, Critic)
unanimously rejected an options overlay at this account size (one SPY spread is 7–13× NAV,
21–38%/yr against a 2%/yr budget; option bars exist only from 2024) and recommended
converting the goal into pre-registered rates with a stop rule. This is that
pre-registration. Nothing below may be edited after the first engine run.

## Metric definitions (fixed)

- Price path: engine `pv` steps of each run; SPY-TR = engine SPY price × 1.0125^(years)
  (the same 1.25%/yr accrual the engine credits the portfolio).
- Rolling window W ∈ {63, 126, 252} sessions over the cycle run (2021-11-01 → 2026-08-27).
  `beat(W)` = share of windows where strategy return > SPY-TR return.
- Bear windows = the three full bear runs already used throughout: rb1 2022-01-01→06-30,
  rb2 2026-02-01→04-01, rb3 2025-02-15→04-15. `bear_positive` = all three returns ≥ 0.
- Cycle return and max drawdown from the cycle run.

## Baselines on existing engine paths (no new runs)

| run | beat 3m | beat 6m | beat 12m | 3m bear-positive | 3m bull-beat | cycle | maxDD |
|---|---|---|---|---|---|---|---|
| champion (bt 781256) | 63.5% | 74.5% | 92.9% | 46% | 57% | +233.8% | −27.5% |
| bil25 (bt 785201) — live paper | 61.6% | 75.3% | 86.9% | 42% | 57% | +197.8% | −21.1% |
| bil25 + sleeve (bt 191226) | 62.8% | 76.3% | 90.1% | 46% | 60% | +208.6% | −18.5% |

## Statistical power (the council's correction)

The cycle holds ~19 independent 3-month windows; rolling windows are near-fully
autocorrelated. Standard error on a beat rate ≈ ±11pp; the bear-window rate rests on 2–3
episodes. Therefore the **minimum detectable effect is +10pp on beat(3m)**: a candidate that
improves beat(3m) by less than 10pp over bil25 is indistinguishable from noise and is not
adopted even if it clears a threshold by a hair.

## Thresholds (all must hold; frozen)

| criterion | threshold | bil25 today |
|---|---|---|
| T1 beat(3m) | ≥ 70% **and** ≥ bil25 + 10pp | 61.6% |
| T2 beat(6m) | ≥ 80% | 75.3% |
| T3 beat(12m) | ≥ 92% | 86.9% |
| T4 bear windows | rb1, rb2, rb3 all ≥ 0 | +2.06 / +0.29 / +2.59 |
| T5 cycle return | ≥ +190% | +197.8% |
| T6 cycle maxDD | ≥ −24% | −21.1% |

## Candidates (fixed list; mechanism-chosen, not fitted)

The short-window losses concentrate in bull windows (bull-beat 57%): the ON-state book is
gold/energy (no equity beta) and the vol target caps the core. Two mechanisms can raise
bull-window beta without touching the OFF-state defence (which is what keeps bears ≥ 0):

| id | change vs bil25 (everything else identical) | mechanism |
|---|---|---|
| K1 | `trend_on_book` = QQQ 0.50 / GLD 0.25 / GDX 0.125 / XLE 0.125 | equity beta in the ON remainder |
| K2 | `trend_on_book` = QQQ 0.25 / GLD 0.375 / GDX 0.1875 / XLE 0.1875 | half-measure of K1 |
| K3 | `target_vol` 0.20 → 0.25 | more core on calm tape |
| K4 | K2 + `target_vol` 0.25 | both |

Known-failed configurations are excluded by prior measurement: faster re-entry, partial
damp, higher core cap (non-binding), two-tranche cadence (cycle +187.8% < T5).

## Procedure (fixed)

- Lab doc 201 / instance `strategy-eb-lab` only; the outlier sleeve lane is disabled and
  `reserve_for_other_lanes_pct` set to 0 for every arm; doc 201 is restored afterwards.
  Doc 200 (paper) is never touched.
- Per candidate: 4 sequential engine runs — cyc, rb1, rb2, rb3 — one at a time.
  16 runs total; no other backtests in flight.
- Verdict per candidate = T1–T6. Adoption requires ALL. If none pass, bil25 stays and
  **this line of tuning stops** — no second round, no loosened thresholds.
- Results are appended below by `scripts/eb_short_window_test.py` verbatim.

## Results

_(appended by the runner)_
