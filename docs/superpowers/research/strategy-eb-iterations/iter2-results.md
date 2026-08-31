# Iteration 2 — The Reachable Ceiling

Data: yfinance daily, `auto_adjust=True`, 38 tickers, full coverage 2020-01-02 → 2026-08-28.
Window return = last close ≤ end ÷ last close **strictly before** start (so day 1 of the window counts).
Rotations/switches: signal at close *t*, execute at close *t+1*, **10 bps per one-way trade**. No lookahead.
Sanity: SPY evaluated against itself scores 0/18 wins, CAGR +12.83%, maxDD −24.50%.

---

## QUESTION 1 — The reachable ceiling

### 1.1 Per-window top-3 and SPY-TR

| Window | SPY-TR | #1 | #2 | #3 | # of 37 beating SPY |
|---|---|---|---|---|---|
| bear 2022-01→06 | −19.98% | UNG +52.20 | USO +47.81 | XLE +31.54 | 25 |
| bear 2026-02→04 | −5.05% | USO +56.05 | PDBC +17.77 | DBC +17.40 | 27 |
| bear 2025-02→04 | −11.56% | GDX +23.00 | GLD +11.83 | IEF +1.93 | 24 |
| bull 2023-01→07 | +20.61% | TQQQ +164.27 | QLD +97.00 | SMH +58.29 | **8** |
| bull 2026-04→06 | +16.64% | TQQQ +106.43 | QLD +63.33 | SMH +58.53 | **7** |
| bull 2024-01→06 | +15.22% | SMH +49.08 | TQQQ +46.69 | QLD +31.55 | **9** |
| chop 2025-11→2026-02 | +2.74% | SLV +80.05 | GDX +53.69 | GLD +28.86 | 23 |
| chop 2022-07→12 | +2.26% | XLE +24.93 | SLV +18.13 | XLI +13.52 | 16 |
| chop 2024-07→10 | +4.81% | FXI +21.43 | GDX +18.89 | XLU +18.07 | 16 |
| hold 2021-11→12 | +3.78% | SMH +13.45 | XLRE +9.27 | XLP +8.97 | 11 |
| hold 2023-08→10 | −8.33% | UUP +6.41 | UNG +5.49 | USO +2.53 | 19 |
| hold 2025-05→10 | +23.70% | TQQQ +113.48 | SMH +71.82 | QLD +68.99 | 10 |
| hold 2024-11→2025-02 | +7.58% | UNG +54.60 | TQQQ +29.77 | QLD +20.68 | 10 |
| hold 2026-06→08 | +2.20% | XBI +23.19 | GDX +15.87 | XLV +15.30 | 16 |
| cal 2022 | −18.18% | XLE +64.32 | USO +28.97 | DBC +19.34 | 23 |
| cal 2023 | +26.18% | TQQQ +198.05 | QLD +117.72 | SMH +73.38 | **8** |
| cal 2024 | +24.89% | TQQQ +58.27 | QLD +42.81 | SMH +39.10 | 10 |
| cal 2025 | +17.72% | GDX +154.77 | SLV +144.66 | GLD +63.68 | 16 |
| FULL CYCLE | +79.07% | SMH +331.63 | GDX +250.46 | SLV +184.16 | 12 |

The structural fact is in the last column. In bear and chop windows two-thirds of the menu beats SPY — beating a falling index is easy. In the three bull windows only **7–9 of 37** assets beat SPY, and they are always the same three: TQQQ, QLD, SMH. **The bull windows are the binding constraint, and the only instruments that clear them are levered Nasdaq and semis.**

### 1.2 Perfect foresight

Trivially 19/19, minimum edge +9.67pp (hold 2021-11→12). The informative number is churn: **10 distinct winners across the 14 non-calendar windows.** TQQQ wins 5, SMH 3, then UNG/GDX/XLE 2 each and USO/SLV/FXI/UUP/XBI once. The winner set is nearly disjoint between regimes — leveraged equity in bulls, energy/gas/metals in bears. No selector can track a target that moves this way; foresight is doing all the work.

### 1.3 Realistic GEM-style rotation (no foresight)

Monthly signal, top-N by trailing return, equal-weight, 10 bps/one-way.

| Menu | Lookback | N | Wins (of 18) + cycle | Bears | Cycle | CAGR | maxDD |
|---|---|---|---|---|---|---|---|
| **core+def** | 6m | 2 | **12/18 + W** | −9.84 / −1.68 / −10.57 | +118.59% | +17.59% | −30.2% |
| sectors+def | 12-1 | 2 | 11/18 + W | +10.50 / −1.93 / −8.52 | +207.22% | +26.18% | −23.2% |
| FULL 38 | 6m | 2 | 11/18 + W | −15.78 / −4.09 / −11.09 | +107.26% | +16.30% | −45.3% |
| risk-on core | 6m | 2 | 10/18 + W | −18.78 / −2.03 / −19.06 | +184.31% | +24.17% | −35.7% |
| FULL 38 | 12-1 | 1 | 5/18 + L | −52.18 / −6.13 / −16.85 | −44.59% | −11.51% | −74.9% |

**Plain GEM rotation tops out at 12/18 — materially worse than iteration 1's 15/19.** Top-1 concentration is a disaster (the 12-1 top-1 book loses money over the cycle with a −74.9% drawdown). Momentum buys the *previous* regime's winner and hands it back at every turn.

### 1.4 Defensive-only menu {GLD, BIL, UUP, IEF, DBC}

| Lookback | N | Wins | Bears | Cycle | CAGR | maxDD |
|---|---|---|---|---|---|---|
| 6m | 1 | 10/18 + W | +28.25 / −1.80 / +11.83 | +129.23% | +18.75% | −31.0% |
| 12-1 | 1 | 9/18 + W | +28.20 / −1.60 / +11.83 | +153.53% | +21.26% | −32.3% |
| **6m** | **2** | 8/18 + L | **+12.49 / +10.21 / +1.07** | +56.94% | +9.79% | **−16.0%** |
| 12-1 | 2 | 8/18 + W | +13.81 / +4.27 / +2.96 | +81.50% | +13.14% | −16.0% |

As a standalone book this is weak (8–10/18). **As an OFF-state occupant it is excellent**: the top-2 variants are absolutely positive in all three bears with a −16% drawdown. That is the correct role for it — occupant, not strategy.

---

## QUESTION 2 — Bear-positive books

**Single assets positive in all three bears: exactly one — BIL** (+0.12 / +0.58 / +0.67). Cycle +19.28%, CAGR +3.72%. Not investable as a strategy (5/18 wins).

Note what this kills: **GLD is not bear-positive** (2022H1 −0.6%), nor is IEF, TLT, UUP, DBC, or any sector. The 2022 bear was a bond-and-gold bear as well as an equity bear.

**50/50 pairs: 7 of 703** are positive in all three bears. All are metals/energy:

| Pair | Bears | Bulls (SPY: +20.6/+16.6/+15.2) | Wins | Cycle | CAGR | maxDD |
|---|---|---|---|---|---|---|
| GDX/USO | +13.88 / +30.65 / +4.74 | +7.96 / **+0.00** / +15.47 | 11/18 | +227.39% | +27.85% | −34.7% |
| GDX/XLE | +7.66 / +9.59 / +5.32 | +6.42 / **−6.42** / +10.36 | 11/18 | +227.03% | +27.82% | −30.9% |
| GDX/DBC | +5.63 / +10.70 / +7.57 | +5.59 / −1.17 / +7.78 | 10/18 | +161.65% | +22.05% | −33.0% |
| GLD/DBC | +12.76 / +8.13 / +2.22 | +4.02 / −0.53 / +8.92 | 9/18 | +110.75% | +16.70% | −18.6% |
| GLD/UUP | +3.83 / +1.08 / +2.96 | +4.81 / −2.32 / +10.04 | 8/18 | +85.62% | +13.67% | −11.7% |

**The trade, quantified.** Every bear-positive pair beats SPY-TR handily over the cycle (GDX/USO +227% vs +79%, CAGR 27.85% vs 12.83%) — but **loses two of the three bull windows outright**, because none of them holds equity. Bear-positivity in this battery is purchased entirely with commodity beta, and commodity beta does not participate in a Nasdaq bull.

Best **bear-positive fixed 3-asset blend: 12/18** (SLV 0.2 / GDX 0.5 / USO 0.3, bearmin +2.27%, cycle +243%). Momentum selectors: only the defensive top-2 books qualify, at 8/18.

---

## QUESTION 3 — The verdict math

### (a) Any FIXED blend
Exhaustive: 13,357 two-asset blends (5% grid), 303,696 three-asset blends (10% grid).

- 2-asset ceiling: **15/18** (XLE .45/SMH .55; GLD .50/SMH .50).
- 3-asset ceiling: **17/18 + cycle = 18/19** (GLD .40 / GDX .20 / SMH .40 — verified with monthly rebalance and costs: 17/18+W, cycle +270%, CAGR +31.14%, maxDD −29.6%). Bears −17.59 / −0.94 / +0.37.
- **Constrained to bear-positive, the 3-asset ceiling collapses to 12/18.**

This 18/19 is not a rule — it is a hindsight asset pick. Choosing SMH and GDX in Nov 2021 *is* the alpha; nothing generated that choice.

### (b) Monthly momentum rotation (realistic)
**Ceiling 12/18.** Not close. Do not pursue.

### (c) Trend-switch family (iteration 1's shape) — 4,896 configs
17 signals × {monthly, daily} × 18 ON legs × 8 OFF legs.

| Result | Count | Share |
|---|---|---|
| 18/19 | **8** | 0.16% |
| 17/19 | 94 | 1.9% |
| ≥16/19 | 520 | 10.6% |
| **bear-positive in all 3** | **3** | **0.06%** |
| 18/19 **and** bear-positive | **0** | **0%** |

**Is 18/19 reachable by a non-clairvoyant rule? Yes — but it is a needle, and it is not bear-positive.**

All eight 18/19 configs share one ON leg, **SMH 0.4 / GLD 0.4 / GDX 0.2**. No other ON leg out of 18 produces a single 18/19 config. The rule survives 50 bps costs (5 regime flips over the cycle), so it is not a cost artifact — but a result concentrated on one point in asset space and zero others is asset selection wearing a rule's clothing.

**Headline 18/19:** SPY 50d>200d SMA, daily signal → ON = SMH .4/GLD .4/GDX .2, OFF = GDX/XLE 50/50.
Bears −15.08 / −0.91 / +0.50. Cycle **+267.4% vs SPY +79.1%**, CAGR +30.94%, maxDD −31.3%. Only loss: hold 2024-11→2025-02, −2.55pp.

**The one that satisfies the bear mandate — 16/19:** SPY 20d>80d SMA, daily → ON = SMH .3/GLD .7, OFF = GDX/XLE.
Bears **+1.10 / +4.60 / +1.30**. Cycle +247.4%, CAGR +29.43%, maxDD −31.8%. Loses bull 2026-04→06 (−0.8% vs +16.6%) and one other. **Fragile to costs: at 50 bps the 2022H1 bear flips to −1.77% and bear-positivity is lost** (17 flips, 4,892% cumulative turnover).

### Which windows are fundamentally lost, and why

Loss frequency among the 100 best configs:

| Window | Lost by | Cause |
|---|---|---|
| **hold 2024-11→2025-02** | **84/100** | Not a timing failure. The trend signal is risk-ON 100% of this window — the rule holds its ON leg throughout and still loses, because SMH/GLD/GDX simply underperformed SPY. Pure asset-selection loss; no signal can fix it. |
| **hold 2026-06→08** | 55/100 | SPY +2.20% grind; the OFF leg's commodity beta is uncorrelated noise. Coin-flip window. |
| **bull 2026-04→06** | 39/100 | **Re-entry latency.** The V-bottom off the Feb–Apr bear: the 20>100 rule re-enters on day 17 of 43, after **SPY has already moved +9.78% of its +16.64%**. |
| hold 2023-08→10 | 9/100 | Mild. |
| all three bears | **0/100** | Beating a falling SPY is the easy half. |

**Why bear-positive and 18/19 are mutually exclusive.** Signal latency, measured directly: SPY spends **79.1% of the 2026-02→04 bear above its 200d SMA**, 40.5% of 2025-02→04, and 34.4% of 2022H1. At month-end granularity the 2026 bear signals `R,R,D` — the only defensive reading lands at the window's end. Getting absolutely positive through these short, fast bears requires a fast signal (20>80). A fast signal whipsaws, and the whipsaw is charged in the bull windows — which, per §1.1, only 7–9 of 37 assets can win at all. **The bears demand fast; the bulls demand slow and levered. One switch cannot serve both.**

---

## Verdict

**Do not chase 95%.** 18/19 exists but occupies 0.16% of a 4,896-config space, sits on exactly one hindsight-chosen ON leg, and violates the bear mandate. Iteration 1's 15/19 with bears ≥ −9% is closer to the honest frontier than it looks.

**Best defensible profile, and what I would ship:**

> **SPY > 150d SMA (daily) → ON = QLD 0.5 / GLD 0.5; else OFF = GDX 0.5 / XLE 0.5.**
> **17/19**, bears −18.81 / −0.02 / −1.24, cycle **+229.7% vs SPY +79.1%**, CAGR +28.04%, maxDD −36.1%.

QLD is a defensible ex-ante choice in a way SMH/GDX is not. But note it degrades to 16/19 at 20 bps and 13/19 at 50 bps (33 flips) — **verify against the engine before believing it.**

**The realistic frontier is a choice between two points, not a single optimum:**

| | Win-rate | Bear absolute | Cycle margin | Honesty |
|---|---|---|---|---|
| 18/19 headline | 18/19 | −15.1% worst | +188pp | needle; hindsight ON leg |
| 17/19 defensible | 17/19 | −18.8% worst | +151pp | acceptable; cost-fragile |
| **16/19 bear-positive** | 16/19 | **+1.10% worst** | +168pp | **satisfies the bear mandate** |

If the bear mandate is genuinely binding, **take the 16/19 and stop** — 95% win-rate and absolute bear positivity are not jointly reachable in this battery by any rule I can name. If the mandate is soft, iteration 1's family is already within ~2 windows of a ceiling that is mostly overfit.

**One caveat on my own numbers.** My reconstruction of iteration 1 (vol-target TQQQ + trend-conditioned GLD/BIL, vol targets 0.25–0.40) scored only 10–11/19 with bears −24% to −37% — well short of the reported 15/19 / −9%. My reconstruction is cruder than the real thing; treat the iteration-1 row above as unverified and read the ceiling analysis, not that comparison.
