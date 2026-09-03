# Outlier sleeve — engine test (default, spec windows)

Instance strategy-eb-lab · granularity 86400 · etf-liquid tiers · run 2026-09-03 02:38Z

| regime | window | bt | span | EB+sleeve | SPY-TR | Δ vs SPY | bil25 base | Δ vs base | DD | SPY DD | base DD |
|---|---|---|---|---|---|---|---|---|---|---|---|
| bear | rb1 | 126814 | 2022-01-01..2022-06-30 | +2.19% | -19.40% | +21.60 | +2.06% | +0.13 | -12.1% | -23.0% | -12.3% |
| bear | rb3 | 396366 | 2025-02-15..2025-04-15 | +2.75% | -11.40% | +14.16 | +2.59% | +0.16 | -7.0% | -18.8% | -6.9% |
| multi | cyc | 876989 | 2021-11-01..2026-08-27 | +183.97% | +77.11% | +106.86 | +197.78% | -13.81 | -22.2% | -24.7% | -21.1% |
|  | nb4 | 743251 | 2026-01-15..2026-04-30 | +2.19% | +3.41% | -1.21 | +1.94% | +0.25 | -12.7% | -8.9% | -12.7% |
|  | nc3 | 525162 | 2022-03-01..2022-08-31 | -11.36% | -8.21% | -3.15 | -11.20% | -0.16 | -19.5% | -20.5% | -18.6% |
|  | ny1 | 548317 | 2022-01-01..2023-12-29 | +34.91% | +2.89% | +32.02 | +36.00% | -1.09 | -21.1% | -24.7% | -21.1% |
|  | ny3 | 386429 | 2024-01-01..2026-08-27 | +144.75% | +66.53% | +78.22 | +139.49% | +5.26 | -14.3% | -18.8% | -13.5% |

## Verdict: **FAIL**

```
{
 "c1_return": false,
 "c2_drawdown": true,
 "c4_population": false,
 "attribution_top": {
  "KGC": 123.0,
  "ANET": 21.0,
  "T": 6.0,
  "OXY": 6.0,
  "MMM": 3.0,
  "MPC": 2.0,
  "INTC": -1.0,
  "SYK": -4.0,
  "LPLA": -4.0,
  "ENPH": -7.0,
  "EXE": -8.0,
  "DRI": -15.0
 },
 "c3_bears_nonnegative": true
}
```

# Outlier sleeve — engine test (default, spec windows)

Instance strategy-eb-lab · granularity 86400 · etf-liquid tiers · run 2026-09-03 03:51Z

| regime | window | bt | span | EB+sleeve | SPY-TR | Δ vs SPY | bil25 base | Δ vs base | DD | SPY DD | base DD |
|---|---|---|---|---|---|---|---|---|---|---|---|
| bear | rb1 | 649764 | 2022-01-01..2022-06-30 | +0.05% | -19.40% | +19.45 | +2.06% | -2.01 | -12.7% | -23.0% | -12.3% |
| bear | rb3 | 797509 | 2025-02-15..2025-04-15 | +1.73% | -11.40% | +13.13 | +2.59% | -0.86 | -6.0% | -18.8% | -6.9% |
| multi | cyc | 191226 | 2021-11-01..2026-08-27 | +208.63% | +77.11% | +131.52 | +197.78% | +10.85 | -18.5% | -24.7% | -21.1% |
|  | nb4 | 495576 | 2026-01-15..2026-04-30 | +5.59% | +3.41% | +2.18 | +1.94% | +3.65 | -11.7% | -8.9% | -12.7% |
|  | nc3 | 867858 | 2022-03-01..2022-08-31 | -13.86% | -8.21% | -5.65 | -11.20% | -2.66 | -21.5% | -20.5% | -18.6% |
|  | ny1 | 653445 | 2022-01-01..2023-12-29 | +29.16% | +2.89% | +26.27 | +36.00% | -6.84 | -20.0% | -24.7% | -21.1% |
|  | ny3 | 324710 | 2024-01-01..2026-08-27 | +146.91% | +66.53% | +80.39 | +139.49% | +7.42 | -17.0% | -18.8% | -13.5% |

## Verdict: **FAIL**

```
{
 "c1_return": false,
 "c2_drawdown": true,
 "c4_population": false,
 "attribution_top": {
  "NVDA": 566.0,
  "HOOD": 478.0,
  "CLS": 256.0,
  "SPOT": 192.0,
  "SMCI": 170.0,
  "KGC": 117.0,
  "EQT": 69.0,
  "ANET": 47.0,
  "AU": 38.0,
  "PODD": 35.0,
  "CI": 33.0,
  "PANW": 25.0
 },
 "c3_bears_nonnegative": true
}
```

# Outlier sleeve — engine test (confirm50, spec windows)

Instance strategy-eb-lab · granularity 86400 · etf-liquid tiers · run 2026-09-03 04:45Z

| regime | window | bt | span | EB+sleeve | SPY-TR | Δ vs SPY | bil25 base | Δ vs base | DD | SPY DD | base DD |
|---|---|---|---|---|---|---|---|---|---|---|---|
| bear | rb1 | 422531 | 2022-01-01..2022-06-30 | +0.25% | -19.40% | +19.65 | +2.06% | -1.81 | -12.8% | -23.0% | -12.3% |
| bear | rb3 | 685460 | 2025-02-15..2025-04-15 | +1.73% | -11.40% | +13.13 | +2.59% | -0.86 | -6.0% | -18.8% | -6.9% |
| multi | cyc | 700068 | 2021-11-01..2026-08-27 | +179.19% | +77.11% | +102.08 | +197.78% | -18.59 | -20.7% | -24.7% | -21.1% |
|  | nb4 | 510385 | 2026-01-15..2026-04-30 | +5.59% | +3.41% | +2.18 | +1.94% | +3.65 | -11.7% | -8.9% | -12.7% |
|  | nc3 | 502979 | 2022-03-01..2022-08-31 | -13.78% | -8.21% | -5.57 | -11.20% | -2.58 | -21.5% | -20.5% | -18.6% |
|  | ny1 | 402609 | 2022-01-01..2023-12-29 | +30.35% | +2.89% | +27.45 | +36.00% | -5.65 | -19.5% | -24.7% | -21.1% |
|  | ny3 | 862610 | 2024-01-01..2026-08-27 | +138.72% | +66.53% | +72.19 | +139.49% | -0.77 | -14.0% | -18.8% | -13.5% |

## Verdict: **FAIL**

```
{
 "c1_return": false,
 "c2_drawdown": true,
 "c4_population": false,
 "attribution_top": {
  "WBD": 296.0,
  "NEM": 199.0,
  "SMCI": 171.0,
  "CLS": 89.0,
  "KGC": 79.0,
  "EQT": 70.0,
  "AEM": 63.0,
  "NVDA": 61.0,
  "VRT": 53.0,
  "MPC": 50.0,
  "APH": 33.0,
  "ANET": 33.0
 },
 "sleeve_realised_gain": -2183.0,
 "c3_bears_nonnegative": true
}
```

# Outlier sleeve — engine test (default, regime windows)

Instance strategy-eb-lab · granularity 86400 · etf-liquid tiers · run 2026-09-03 06:12Z

| regime | window | bt | span | EB+sleeve | SPY-TR | Δ vs SPY | bil25 base | Δ vs base | DD | SPY DD | base DD |
|---|---|---|---|---|---|---|---|---|---|---|---|
| bear | rb1 | 698762 | 2022-01-01..2022-06-30 | +0.05% | -19.40% | +19.45 | +2.06% | -2.01 | -12.7% | -23.0% | -12.3% |
| bear | rb2 | 897636 | 2026-02-02..2026-04-01 | -0.20% | -5.84% | +5.64 | +0.29% | -0.49 | -10.8% | -8.9% | -11.3% |
| bear | rb3 | 334527 | 2025-02-15..2025-04-15 | +1.73% | -11.40% | +13.13 | +2.59% | -0.86 | -6.0% | -18.8% | -6.9% |
| bull | nu1 | 492515 | 2023-10-25..2024-03-28 | +22.51% | +24.15% | -1.64 | +22.71% | -0.20 | -6.3% | -3.0% | -6.3% |
| bull | nu2 | 140722 | 2024-08-06..2024-12-31 | +12.67% | +14.26% | -1.59 | +9.85% | +2.82 | -7.9% | -4.1% | -8.2% |
| bull | p21bull | 266068 | 2021-01-01..2021-10-29 | +19.73% | +23.82% | -4.08 | +18.82% | +0.91 | -7.6% | -5.3% | -9.1% |
| bull | ru1 | 849248 | 2023-01-02..2023-07-31 | +25.06% | +20.32% | +4.74 | +25.97% | -0.91 | -9.5% | -7.4% | -10.2% |
| bull | ru2 | 471576 | 2026-04-01..2026-06-01 | +14.20% | +16.58% | -2.38 | +11.19% | +3.01 | -4.8% | -1.9% | -4.8% |
| bull | ru3 | 383362 | 2024-01-01..2024-06-28 | +17.18% | +15.64% | +1.54 | +18.23% | -1.05 | -9.3% | -5.3% | -8.9% |
| chop | nc1 | 782942 | 2023-02-01..2023-06-15 | +10.51% | +8.03% | +2.48 | +13.20% | -2.69 | -8.4% | -7.4% | -9.1% |
| chop | nc2 | 527090 | 2024-03-15..2024-08-30 | +1.69% | +9.06% | -7.37 | +1.82% | -0.13 | -12.8% | -8.3% | -12.4% |
| chop | rc1 | 764044 | 2025-11-10..2026-02-24 | +21.85% | +2.08% | +19.78 | +19.94% | +1.91 | -7.8% | -4.5% | -8.5% |
| chop | rc2 | 153035 | 2022-07-01..2022-12-30 | +2.36% | +2.24% | +0.12 | +3.38% | -1.02 | -15.8% | -16.9% | -17.3% |
| chop | rc3 | 305724 | 2024-07-01..2024-10-31 | +1.24% | +7.03% | -5.79 | +2.55% | -1.31 | -13.0% | -8.3% | -14.4% |
| handoff | h1 | 239369 | 2021-11-01..2021-12-31 | -2.43% | +3.92% | -6.36 | -3.21% | +0.78 | -9.4% | -4.1% | -9.7% |
| handoff | h2 | 390646 | 2023-08-01..2023-10-31 | -0.08% | -8.93% | +8.84 | -0.03% | -0.05 | -5.0% | -10.0% | -5.6% |
| handoff | h3 | 746642 | 2025-05-01..2025-10-31 | +39.52% | +23.52% | +16.00 | +39.53% | -0.01 | -6.9% | -2.9% | -7.1% |
| handoff | h4 | 714529 | 2024-11-01..2025-02-14 | +18.53% | +7.64% | +10.89 | +13.18% | +5.35 | -7.3% | -4.4% | -8.2% |
| handoff | h5 | 539308 | 2026-06-01..2026-08-27 | +6.89% | +1.57% | +5.33 | +5.74% | +1.15 | -8.2% | -4.4% | -9.2% |
| year | y22 | 453089 | 2022-01-01..2022-12-30 | +1.56% | -18.27% | +19.84 | +2.61% | -1.05 | -20.0% | -24.7% | -21.1% |
| year | y23 | 248386 | 2023-01-02..2023-12-29 | +28.80% | +26.18% | +2.62 | +30.41% | -1.61 | -11.2% | -10.0% | -11.5% |
| year | y24 | 178603 | 2024-01-01..2024-12-31 | +21.07% | +25.29% | -4.23 | +21.62% | -0.55 | -14.9% | -8.3% | -13.5% |
| year | y25 | 630824 | 2025-01-01..2025-12-31 | +52.80% | +18.67% | +34.13 | +55.45% | -2.65 | -9.5% | -18.8% | -8.0% |
| multi | cyc | 407161 | 2021-11-01..2026-08-27 | +208.63% | +77.11% | +131.52 | +197.78% | +10.85 | -18.5% | -24.7% | -21.1% |
| multi | ny2 | 332851 | 2023-01-02..2024-12-31 | +64.69% | +57.66% | +7.02 | +54.31% | +10.38 | -15.1% | -10.0% | -14.1% |

## Verdict: **17/25 windows beat SPY-TR**

```
{
 "c1_return": false,
 "c2_drawdown": true,
 "c4_population": false,
 "attribution_top": {
  "NVDA": 566.0,
  "HOOD": 478.0,
  "CLS": 256.0,
  "SPOT": 192.0,
  "SMCI": 170.0,
  "KGC": 117.0,
  "EQT": 69.0,
  "ANET": 47.0,
  "AU": 38.0,
  "PODD": 35.0,
  "CI": 33.0,
  "PANW": 25.0
 },
 "sleeve_realised_gain": -859.0,
 "c3_bears_nonnegative": false
}
```


---

# Analysis (2026-09-03)

## 1. Pre-registered verdict: FAIL on the return bar, by 4.2pp; everything else improved

Sequential runs on the lab instance (`strategy-eb-lab`, doc 201 = bil25 EB lane with
`reserve_for_other_lanes_pct 0.15` + outlier sleeve at defaults), thresholds frozen in
spec §9 before any run.

| Criterion | Threshold | Measured | Result |
|---|---|---|---|
| c1 cycle return | ≥ +212.8% (bil25 +197.8 + 15pp) | **+208.6%** (bt 191226) | FAIL by 4.2pp |
| c2 cycle max DD | ≥ −24.1% | **−18.5%** (bil25 −21.1%) | PASS, 2.6pp shallower |
| c3 bear windows stay ≥ 0 | rb1, rb3, nb4 | +0.05%, +1.73%, +5.59% | PASS |
| c4 population | ≥ 3 names ≥ 5% of sleeve gain | 7 names (SNDK, LITE, MU, NVDA, HOOD, CIEN, CLS) | PASS |

Per the pre-registration the sleeve is **not adopted on this evidence**. The bar is not
moved after seeing the number.

## 2. What the sleeve actually did (true attribution, `sleeve_attrib.py`)

Round-trip P&L plus open positions marked at the last close (the runner's cash-flow
attribution under-counts any run that ends with open slots):

| Arm | Sleeve P&L | Share of NAV gain | Top contributors | Open at end |
|---|---|---|---|---|
| default (confirm ≥25%) | **+$5,036** | 40% of $12,518 | SNDK $1,676 · LITE $647 · MU $617 · NVDA $566 · HOOD $478 · CIEN $438 · CLS $256 | CIEN, DELL, HWM, LITE, MU, MXL, SNDK |
| confirm ≥50% | +$1,423 | 13% of $10,751 | WDC $1,003 · WBD $296 · NEM $199 · SMCI $171 | DELL, GLW, MXL, PANW, SNDK, TSEM, WDC |

The screen caught the population it was built for — SNDK, MU, LITE, NVDA, HOOD, CLS,
SMCI all entered on 52-week-high + top-decile RS breakouts — and the SMA-200 exit held
them (NVDA +314%, HOOD +202%, CLS +90%, SMCI +83%, SPOT +74% realised).

## 3. Why the first engine run failed by −13.8pp, and the fix

bt 876989 (first run): 105 screens, 4.2 candidates each, **0.25 entries per screen; 67
screens found candidates and bought nothing**. The EB lane targets 100% of NAV and its
idle-cash sweep took every settled dollar, so the sleeve was starved while EB still paid
for a book it could not fully fund. Fix: `strategy_eb.reserve_for_other_lanes_pct`
(default 0.0 = byte-identical champion; 304 EB tests unchanged) — the book is sized off
NAV minus max(reserve, value of positions outside the EB universe) and the sibling's
undeployed share is held back from every buy and from the sweep. After the fix: 121
screens, 0.70 entries per screen, 156 entries, and the cycle moved from +184.0% to +208.6%.

Lesson for any future multi-lane document: two run-once lanes on one account contend for
cash; the lane that sizes to 100% of NAV wins by default.

## 4. Where the 4.2pp went

The sleeve added ~+40pp of NAV over the cycle; the EB book, running at 85% instead of
100%, gave back ~−30pp. Net +10.9pp against a +15pp bar. In windows with no breakouts to
catch (2022–23: −6.8pp; the 2022 chop: −2.7pp; 2022-H1 bear: −2.0pp) the reserve is pure
cost; in windows with breakouts it more than pays (2024→now +7.4pp; 2026 bear +3.7pp).

## 5. confirm ≥50% arm: worse (+179.2%, DD −20.7%)

The stricter peer filter rejected NVDA, HOOD and SPOT — the names that carried the
default arm — and kept a different, smaller-payoff set (WBD, NEM). Higher win rate (54%
vs 42%), far lower payoff. The 25% setting stays the reference; the confirmation filter
is a modest, noisy improvement at best, as the offline spike said.

## 6. Regime battery (25 windows, sequential)

**Wins vs SPY-TR:** 17/25 — bear 3/3 · bull 2/6 · chop 3/5 · handoff 4/5 · year 3/4 · multi 2/2  (bil25 alone: 16/25 — bear 3/3 · bull 2/6 · chop 3/5 · handoff 4/5 · year 3/4 · multi 1/2)

**Wins vs bil25 base:** 9/25 — bear 0/3 · bull 3/6 · chop 1/5 · handoff 3/5 · year 0/4 · multi 2/2

**Drawdown shallower-or-equal than bil25:** 19/25 — bear 2/3 · bull 5/6 · chop 4/5 · handoff 5/5 · year 2/4 · multi 1/2

| regime | window | EB+sleeve | SPY-TR | Δ vs SPY | bil25 | Δ vs bil25 | DD | bil25 DD |
|---|---|---|---|---|---|---|---|---|
| bear | rb1 | +0.05% | -19.40% | +19.45 | +2.06% | -2.01 | -12.7% | -12.3% |
| bear | rb2 | -0.20% | -5.84% | +5.64 | +0.29% | -0.49 | -10.8% | -11.3% |
| bear | rb3 | +1.73% | -11.40% | +13.13 | +2.59% | -0.86 | -6.0% | -6.9% |
| bull | nu1 | +22.51% | +24.15% | -1.64 | +22.71% | -0.20 | -6.3% | -6.3% |
| bull | nu2 | +12.67% | +14.26% | -1.59 | +9.85% | +2.82 | -7.9% | -8.2% |
| bull | p21bull | +19.73% | +23.82% | -4.08 | +18.82% | +0.91 | -7.6% | -9.1% |
| bull | ru1 | +25.06% | +20.32% | +4.74 | +25.97% | -0.91 | -9.5% | -10.2% |
| bull | ru2 | +14.20% | +16.58% | -2.38 | +11.19% | +3.01 | -4.8% | -4.8% |
| bull | ru3 | +17.18% | +15.64% | +1.54 | +18.23% | -1.05 | -9.3% | -8.9% |
| chop | nc1 | +10.51% | +8.03% | +2.48 | +13.20% | -2.69 | -8.4% | -9.1% |
| chop | nc2 | +1.69% | +9.06% | -7.37 | +1.82% | -0.13 | -12.8% | -12.4% |
| chop | rc1 | +21.85% | +2.08% | +19.78 | +19.94% | +1.91 | -7.8% | -8.5% |
| chop | rc2 | +2.36% | +2.24% | +0.12 | +3.38% | -1.02 | -15.8% | -17.3% |
| chop | rc3 | +1.24% | +7.03% | -5.79 | +2.55% | -1.31 | -13.0% | -14.4% |
| handoff | h1 | -2.43% | +3.92% | -6.36 | -3.21% | +0.78 | -9.4% | -9.7% |
| handoff | h2 | -0.08% | -8.93% | +8.84 | -0.03% | -0.05 | -5.0% | -5.6% |
| handoff | h3 | +39.52% | +23.52% | +16.00 | +39.53% | -0.01 | -6.9% | -7.1% |
| handoff | h4 | +18.53% | +7.64% | +10.89 | +13.18% | +5.35 | -7.3% | -8.2% |
| handoff | h5 | +6.89% | +1.57% | +5.33 | +5.74% | +1.15 | -8.2% | -9.2% |
| year | y22 | +1.56% | -18.27% | +19.84 | +2.61% | -1.05 | -20.0% | -21.1% |
| year | y23 | +28.80% | +26.18% | +2.62 | +30.41% | -1.61 | -11.2% | -11.5% |
| year | y24 | +21.07% | +25.29% | -4.23 | +21.62% | -0.55 | -14.9% | -13.5% |
| year | y25 | +52.80% | +18.67% | +34.13 | +55.45% | -2.65 | -9.5% | -8.0% |
| multi | cyc | +208.63% | +77.11% | +131.52 | +197.78% | +10.85 | -18.5% | -21.1% |
| multi | ny2 | +64.69% | +57.66% | +7.02 | +54.31% | +10.38 | -15.1% | -14.1% |

Reading:
- **Regime profile is unchanged**: bears 2/3 vs SPY (rb2 −0.20% is the one sub-zero, vs +0.29% for bil25), bulls still the weak spot (2/6), every 2-year-plus window wins by a wide margin.
- **Against the champion it is a coin flip on short windows** (9 wins / 16 losses, most within ±3pp) and a clear win on the long ones: cycle +10.9pp, 2023–24 +10.4pp, both with the sleeve's names doing the work.
- **Drawdown is shallower or equal in 19 of 25 windows** — the sleeve diversifies the gold-heavy OFF book rather than adding to its tail; the six exceptions (2022-H1 bear, 2024-H1 bull, 2024 spring chop, 2024, 2025, 2023–24) are all ≤1.5pp deeper.
- **The cost is concentrated where nothing breaks out**: 2022-H1 bear −2.0pp, 2022 chop −2.7pp, 2023 chop −2.7pp, 2025 −2.7pp — the 15% reserve idling while EB's book runs at 85%.


## 7. Recommendation

Not adopted under the pre-registered rule. The evidence does support one honest follow-up
if the operator wants it: the sleeve's marginal contribution is bounded by its 15%
budget, and the cost is EB's forgone 15%; a **sleeve_fraction / reserve of 0.10** (or 0.20)
is the one untested point on that dial, and it is a single sequential cycle run each. Any
adoption remains the operator's call on risk-adjusted grounds (+10.9pp at 2.6pp less
drawdown), not a pass of the test as written.

Operational notes: sequential-only batteries (`run_windows`), lab doc 201 never touches
doc 200, the paper instance is unchanged (bil25), and pushes restart it.
