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

### Run 2026-09-04 01:10Z — candidates K1, K2, K3, K4

| cand | cyc bt | beat 3m | beat 6m | beat 12m | rb1 | rb2 | rb3 | cycle | maxDD | T1 | T2 | T3 | T4 | T5 | T6 | verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| K1 | 837889 | 62.6% | 70.1% | 74.9% | -6.82% | -1.54% | +1.94% | +181.0% | -25.0% | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | **FAIL** |
| K2 | 953663 | 62.1% | 70.6% | 77.2% | -2.95% | -0.06% | +2.77% | +186.5% | -23.5% | ✗ | ✗ | ✗ | ✗ | ✗ | ✓ | **FAIL** |
| K3 | 877293 | 64.7% | 78.8% | 89.1% | +1.39% | +0.29% | +3.20% | +219.7% | -21.6% | ✗ | ✗ | ✗ | ✓ | ✓ | ✓ | **FAIL** |
| K4 | 413708 | 63.8% | 71.8% | 76.6% | -3.82% | -0.06% | +2.59% | +198.7% | -24.4% | ✗ | ✗ | ✗ | ✗ | ✓ | ✗ | **FAIL** |

### Run 2026-09-04 02:46Z — candidates B0

| cand | cyc bt | beat 3m | beat 6m | beat 12m | rb1 | rb2 | rb3 | cycle | maxDD | T1 | T2 | T3 | T4 | T5 | T6 | verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| B0 | 906467 | 59.7% | 64.1% | 71.8% | -13.17% | -7.15% | -8.88% | +136.8% | -33.0% | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | **FAIL** |

### Run 2026-09-04 02:58Z — candidates B0

| cand | cyc bt | beat 3m | beat 6m | beat 12m | rb1 | rb2 | rb3 | cycle | maxDD | T1 | T2 | T3 | T4 | T5 | T6 | verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| B0 | 630425 | 60.9% | 73.8% | 85.0% | +2.19% | +0.29% | +2.73% | +196.8% | -22.2% | ✗ | ✗ | ✗ | ✓ | ✓ | ✓ | **FAIL** |

### Regime battery 2026-09-04 03:04Z — K3

| cand | regime | window | bt | return | SPY-TR | Δ vs SPY | bil25 | Δ vs bil25 | DD |
|---|---|---|---|---|---|---|---|---|---|
| K3 | bear | rb1 | 747188 | +1.39% | -19.40% | +20.79 | +2.06% | -0.67 | -12.8% |
| K3 | bear | rb2 | 780374 | +0.29% | -5.84% | +6.13 | +0.29% | +0.00 | -11.3% |
| K3 | bear | rb3 | 152278 | +3.20% | -11.40% | +14.60 | +2.59% | +0.61 | -7.0% |
| K3 | bull | ru1 | 400499 | +32.93% | +20.32% | +12.61 | +25.97% | +6.96 | -10.2% |
| K3 | bull | ru2 | 784840 | +17.98% | +16.58% | +1.40 | +11.19% | +6.79 | -4.9% |
| K3 | bull | ru3 | 214773 | +18.43% | +15.64% | +2.79 | +18.23% | +0.20 | -11.1% |
| K3 | bull | p21bull | 172742 | +25.13% | +23.82% | +1.32 | +18.82% | +6.31 | -9.7% |
| K3 | bull | nu1 | 872285 | +26.29% | +24.15% | +2.14 | +22.71% | +3.58 | -7.1% |
| K3 | bull | nu2 | 273161 | +13.52% | +14.26% | -0.74 | +9.85% | +3.67 | -8.9% |
| K3 | chop | rc1 | 690618 | +19.64% | +2.08% | +17.56 | +19.94% | -0.30 | -8.7% |
| K3 | chop | rc2 | 630009 | +1.83% | +2.24% | -0.41 | +3.38% | -1.55 | -17.9% |
| K3 | chop | rc3 | 186935 | +0.34% | +7.03% | -6.70 | +2.55% | -2.21 | -16.7% |
| K3 | chop | nc1 | 726407 | +15.74% | +8.03% | +7.71 | +13.20% | +2.54 | -9.3% |
| K3 | chop | nc2 | 617952 | +2.48% | +9.06% | -6.58 | +1.82% | +0.66 | -13.4% |
| K3 | handoff | h1 | 793027 | -3.95% | +3.92% | -7.87 | -3.21% | -0.74 | -10.6% |
| K3 | handoff | h2 | 365770 | +0.12% | -8.93% | +9.05 | -0.03% | +0.15 | -5.8% |
| K3 | handoff | h3 | 965260 | +42.06% | +23.52% | +18.54 | +39.53% | +2.53 | -7.9% |
| K3 | handoff | h4 | 275639 | +15.89% | +7.64% | +8.26 | +13.18% | +2.71 | -8.7% |
| K3 | handoff | h5 | 158714 | +6.06% | +1.57% | +4.49 | +5.74% | +0.32 | -9.4% |
| K3 | year | y22 | 942617 | +1.49% | -18.27% | +19.76 | +2.61% | -1.12 | -21.5% |
| K3 | year | y23 | 867833 | +36.36% | +26.18% | +10.18 | +30.41% | +5.95 | -12.7% |
| K3 | year | y24 | 416413 | +25.57% | +25.29% | +0.28 | +21.62% | +3.95 | -15.9% |
| K3 | year | y25 | 983848 | +56.34% | +18.67% | +37.67 | +55.45% | +0.89 | -9.8% |
| K3 | multi | ny2 | 408278 | +69.45% | +57.66% | +11.79 | +54.31% | +15.14 | -15.9% |
| K3 | multi | cyc | 435675 | +219.65% | +77.11% | +142.54 | +197.78% | +21.87 | -21.6% |

**K3: 20/25 beat SPY-TR · 19/25 beat bil25** (bil25 alone: 16/25 vs SPY-TR)


## Control, A/A and the K3 regime battery (2026-09-03 evening)

**The first B0 row above (bt 630425 was the second attempt; the first, bt 906467, is the row that
matches V1 to the decimal) was a contaminated control.** A VTS launcher killed on the old image had
already written V1 onto lab doc 201; the relaunched runner captured that as its "original" and
restored it. The lab doc was reset by hand (vts off, target_vol 0.2, reserve 0.15, sleeve on) and
verified by re-reading before the rerun. The runner now resets `vts_enabled` per candidate,
verifies its restore, refuses to post while a run is on the instance, and aborts on a poll timeout.

**Clean B0 (bt 630425, lab doc 201, current code):** cycle +196.8% / bears +2.19, +0.29, +2.73 /
maxDD −22.2% against the bil25 card (bt 785201, doc 200) +197.8% / +2.06, +0.29, +2.59 / −21.1%.
Same signs everywhere, not byte-identical: the two paths share every price and every trade until
2021-12-06, when the doc-200 run made three ~$10 crumb buys (GLD/GDX/XLE, book proportions) that
the lab run folded into the next day's XLE order. Five cents of NAV, ~1pp after five years of
weekly-quantized path dependence.

**A/A (bt 443180, doc 200 itself on instance `strategy-eb`, current code):** final NAV 17,866.98 =
bt 785201's 17,866.98, +197.783% = +197.783%, first divergence **None** across all 1,259 sessions.
The reserve, VTS, snapshot, broker-lane and runner commits since the bil25 default did not change
the champion by a cent. The lab control's 1pp is a doc-level difference (doc 201 carries the
outlier-sleeve lane where doc 200 carries graph_nexus_analysis), not code.

**K3 (target_vol 0.25) on the 25 regime windows: 20/25 beat SPY-TR (bil25: 16/25), 19/25 beat
bil25.** All three bear windows positive (+1.39, +0.29, +3.20; bil25 +2.06, +0.29, +2.59); 2022
calendar year +1.49% (bil25 +2.61%); cycle +219.65% vs +197.78%; maxDD −21.6% vs −21.1%. Where it
loses to bil25: the deepest bear (rb1 −0.67pp, y22 −1.12pp) and chop (rc2 −1.55pp, rc3 −2.21pp).
The one negative window, h1 −3.95%, is negative for bil25 too (−3.21%). K3 failed the frozen T1–T3
short-window bars (3m 64.7% / 6m 78.8% / 12m 89.1%), so this table is for the operator's adoption
decision, not a pass under this registration.
