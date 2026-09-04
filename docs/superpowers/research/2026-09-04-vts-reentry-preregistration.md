# VIX term-structure re-entry ("VTS") — pre-registration (frozen 2026-09-04 before any run)

## Mechanism (why this is not another tuning of the same family)

Every prior candidate changed *what* Strategy EB holds. This changes *when it is allowed
to be out*. The SMA-25 damp exits on every dip and re-enters only after price recovers
above SMA×(1+2%), which is late by construction (measured: the Aug-2024 lag cost three
windows; the 2022 bear-market rallies were missed). Implied volatility mean-reverts faster
than price recovers. Rule:

    OFF  ⇔  price state is OFF (existing SMA-25 hysteresis)  AND  vol curve inverted
    ON   otherwise

where "vol curve inverted" = (VIXY / VIXM) ÷ its trailing 250-session median > threshold.
VIXY (short-term VIX futures ETF) and VIXM (mid-term) are tradable proxies the engine and
the live bar path can already fetch; they are DATA symbols only — never traded. With
`vts_enabled` the state is evaluated every session and a state flip may trade off-cadence
(the exit already does; the re-entry must, or the mechanism is blunted to weekly).

## Offline screen (engine price path 2021-11-01 → 2026-08-27, 5 bps per switch, decision at close, fill next session; controls included)

| book | rule | ON % | 3m beat | 3m bear+ | 3m bull-beat | 6m beat | 12m beat | total |
|---|---|---|---|---|---|---|---|---|
| 45 TQQQ / 55 BIL | always ON | 100% | 55% | 0% | 86% | 59% | 74% | +74% |
| 45 TQQQ / 55 BIL | SMA-25 damp | 61% | 43% | 11% | 52% | 35% | 31% | +14% |
| 45 TQQQ / 55 BIL | **SMA-25 OR VIX<1.00** | 93% | **69%** | **40%** | 86% | **84%** | **99%** | **+236%** |
| 45 TQQQ / 55 SPY | always ON | 100% | 59% | 0% | 93% | 68% | 82% | +105% |
| 45 TQQQ / 55 SPY | SMA-25 damp | 61% | 47% | 10% | 64% | 42% | 55% | +28% |
| 45 TQQQ / 55 SPY | **SMA-25 OR VIX<1.00** | 93% | **75%** | **42%** | 95% | **90%** | **100%** | **+401%** |
| 45 TQQQ / 55 GLD | always ON | 100% | 70% | 0% | 95% | 79% | 88% | +189% |
| 45 TQQQ / 55 GLD | SMA-25 damp | 61% | 62% | 22% | 74% | 65% | 75% | +111% |
| 45 TQQQ / 55 GLD | **SMA-25 OR VIX<1.00** | 93% | **83%** | **45%** | 94% | **92%** | **100%** | **+468%** |

Threshold sensitivity is real: 0.90 is much worse (OFF too often); a vol-only rule (no
price condition) loses the bear clause. The gains on the price-only books (+20–26pp on 3m
beat) exceed the ±11pp minimum detectable effect. The toy books are not Strategy EB (no vol
targeting, no cadence, no books) — the offline screen over-states, as every local harness
here does. **The engine is the verdict.**

## Thresholds (identical to the 2026-09-03 short-window pre-registration; frozen)

| criterion | threshold | bil25 today |
|---|---|---|
| T1 beat(3m) | ≥ 70% **and** ≥ bil25 + 10pp (≥ 71.6%) | 61.6% |
| T2 beat(6m) | ≥ 80% | 75.3% |
| T3 beat(12m) | ≥ 92% | 86.9% |
| T4 bear windows | rb1, rb2, rb3 all ≥ 0 | +2.06 / +0.29 / +2.59 |
| T5 cycle return | ≥ +190% | +197.8% |
| T6 cycle maxDD | ≥ −24% | −21.1% |

Metric definitions are those of `2026-09-03-short-window-preregistration.md`.

## Candidates (fixed; ≤ 2)

| id | config on top of bil25 (everything else identical) |
|---|---|
| V1 | `vts_enabled true`, `vts_threshold 1.00`, `vts_median_bars 250` |
| V2 | same with `vts_threshold 1.05` (the one robustness point) |

## Procedure (fixed)

- Default `vts_enabled false` ⇒ byte-identical champion/bil25 (existing EB tests must pass unchanged).
- Lab doc 201 / instance `strategy-eb-lab` only (sleeve disabled, reserve 0); restored after. Doc 200 never touched.
- 4 sequential runs per candidate (cyc, rb1, rb2, rb3) — 8 runs, one at a time.
- Adoption requires ALL of T1–T6. Otherwise bil25 stays and this line stops. No second round.

## Results

_(appended by the runner verbatim)_
