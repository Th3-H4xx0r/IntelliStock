# Iteration 2b — the three headline rules under the engine's real cost curve

**Cost model.** One-way, per unit notional traded, charged per leg on `|target − held|`:
**4.4 bps** for `{SPY, QQQ, TQQQ, QLD, SQQQ, BIL, GLD, IWM}`; **23.2 bps** for everything
else (SMH, GDX, XLE, USO, DBC, IEF, UUP …). Decide close *t*, execute close *t+1*.
Data: yfinance `auto_adjust=True`, 2008-01-02 → 2026-08-28, all 15 tickers full coverage
except TQQQ/SQQQ (2010-02-11; synthetic `3·rQQQ − 2·rBIL − 95bps/252` before that, as in iter1).
Window convention is iter2's: entry basis = last close **strictly before** start.

**Reconstruction is verified.** At a flat 10 bps my R1 returns 16/19, bears +1.10/+4.60/+1.30,
cycle +247.37%, CAGR +29.43%, maxDD −31.8%, and R2 returns 17/19, bears −18.81/−0.02/−1.24,
cycle +229.66%, CAGR +28.04%, maxDD −36.1% — iter2's published figures to two decimals.
R3 reproduces iter1's A′ at maxDD −26.6% and 14 flips exactly, CAGR +19.08% vs their +18.22%,
2010-21 CAGR +16.85% vs their +16.71% (residual is the one-day window-end offset in iter1's `bnd()`).

SPY-TR reference: cycle **+79.07%**, CAGR **+12.83%**, maxDD −24.5%; 2010-2021 CAGR **+15.04%**, maxDD −33.7%.
Bears −19.98 / −5.05 / −11.56. Bulls +20.6 / +16.6 / +15.2.

---

## 1. Engine-cost scorecard

| Rule / variant | Wins | Bears (abs) | Cycle | Margin | CAGR | maxDD | Turn/yr | Cost drag | 2010-21 CAGR |
|---|---|---|---|---|---|---|---|---|---|
| **R1 base (daily)** | **16/19** | **+0.57 / +4.42 / +1.13** | +238.70% | +159.6pp | +28.76% | −32.0% | 781% | 1.69pp/y | +12.27% |
| R1 weekly | **16/19** | **+0.16 / +4.06 / +1.55** | +277.37% | +198.3pp | +31.67% | −31.3% | 662% | 1.46pp/y | +12.84% |
| R1 hyst 1% | 15/19 | −5.70 / −1.03 / +0.46 | +223.84% | +144.8pp | +27.56% | −30.2% | 614% | 1.32pp/y | +14.86% |
| R1 weekly+hyst | 15/19 | −5.39 / −3.24 / −1.57 | +187.26% | +108.2pp | +24.44% | −30.0% | 500% | 1.05pp/y | +14.81% |
| **R2 base (daily)** | **17/19** | −19.44 / −0.12 / −1.49 | +222.04% | +143.0pp | +27.42% | −36.3% | 1564% | 2.64pp/y | +20.51% |
| R2 weekly | 15/19 | −23.46 / +0.17 / −8.57 | +167.36% | +88.3pp | +22.60% | −37.3% | 845% | 1.38pp/y | +21.09% |
| R2 hyst 1% | 15/19 | −24.17 / +0.17 / −3.68 | +176.46% | +97.4pp | +23.45% | −42.0% | 987% | 1.56pp/y | +21.04% |
| R2 weekly+hyst | 14/19 | −28.82 / +0.17 / −8.57 | +133.31% | +54.2pp | +19.19% | −42.0% | 765% | 1.21pp/y | +22.18% |
| **R3 = iter-1 A′** | 15/19 | −14.71 / −7.08 / −4.57 | +132.26% | +53.2pp | +19.08% | −26.6% | 635% | 0.33pp/y | +16.85% |

**Engine costs are not what kills these rules.** Going from flat 10 bps to the real tier
structure costs R1 8.7pp of cycle return (+247.4 → +238.7) and R2 7.6pp (+229.7 → +222.0);
neither loses a window. R3 gets *cheaper* — every leg it trades (TQQQ/SPY/GLD/BIL) is
tier-A, so 4.4 bps beats the 10 bps iter1 already charged. iter2's warning that R1 is
"fragile to costs" was calibrated on a 50 bps flat shock, which is roughly double the true
tier-B rate. The real breakeven is higher than 23.2 bps but not by much: sweeping the
tier-B rate, R1's 2022H1 bear stays positive at 30 bps (+0.23%) and flips at 40 bps (−0.26%),
where the rule also drops to 14/19. **R1's bear-positivity has ~11 bps of cost headroom.**

Turnover is the honest worry, not the drag. R1 base trades **781%/yr one-way (≈65%/month)**
and R2 base **1564%/yr (≈130%/month)** — both above the ~50%/month threshold at which this
class of signal has historically stopped paying. The turnover is mostly *drift*, not flips:
R1 flips regime 14 times in 4.8 years but rebalances to target every day. Rebalancing only on
state change cuts R1 to 580%/yr and costs it one window (16/19 → 15/19); the weekly-evaluation
variant is strictly better on every axis — higher return, lower drawdown, less turnover, and
it keeps 16/19 with all three bears positive.

---

## 2. The GLD/GDX exposure, measured

Replacing the gold complex with BIL (R1 ON: SMH .3 / **BIL** .7, OFF: **BIL** .5 / XLE .5;
R2 ON: QLD .5 / **BIL** .5, OFF: **BIL** .5 / XLE .5; R3 occupant GLD → BIL):

| Rule | Live margin | Neutralised | Wins after | **Share of margin from GLD+GDX** |
|---|---|---|---|---|
| R1 | +159.6pp | **−0.2pp** | 16 → **9/19** | **100.1%** |
| R2 | +143.0pp | +26.9pp | 17 → **12/19** | **81.2%** |
| R3 | +53.2pp | **−0.2pp** | 15 → **9/19** | **100.4%** |

Single-leg decomposition (drop one, keep the other):

| | drop GLD only | drop GDX only |
|---|---|---|
| R1 | 12/19, +53.2pp (−67% of margin) | 13/19, +80.2pp (−50%) |
| R2 | 11/19, +53.8pp (−62%) | 16/19, +101.9pp (−29%) |

**R1 and R3 have no measurable margin outside gold.** Both land within 0.2pp of SPY-TR once
the gold complex is gone — after 4.8 years, 780%/yr of turnover and 14 regime flips, the
non-gold residual is zero. R2 keeps +26.9pp, but that residual is levered Nasdaq, not signal.

---

## 3. The control that matters: no rule at all

Static buy-and-hold blends, monthly rebalance, same engine costs, same battery:

| Static book | Wins | Bears | Cycle | Margin | CAGR | maxDD | 2010-21 |
|---|---|---|---|---|---|---|---|
| SMH .3 / GLD .7 *(= R1's ON leg, held forever)* | 15/19 | −12.05 / −1.74 / +1.56 | +217.30% | +138.2pp | +27.03% | **−22.6%** | +10.32% |
| GDX .5 / XLE .5 *(= the OFF leg, held forever)* | 12/19 | **+7.61 / +9.57 / +5.32** | +225.92% | +146.8pp | +27.73% | −31.0% | +2.17% |
| QLD .5 / GLD .5 *(= R2's ON leg)* | 13/19 | −30.64 / −6.99 / −10.99 | +162.76% | +83.7pp | +22.16% | −40.7% | +22.14% |
| **SMH .4 / GLD .4 / GDX .2** | **18/19** | −17.60 / −0.95 / +0.36 | +269.30% | **+190.2pp** | +31.08% | −29.6% | +11.49% |
| GLD | 10/19 | −1.46 / −1.60 / +11.83 | +153.59% | +74.5pp | +21.26% | −26.4% | +3.96% |
| GDX | 11/19 | −14.52 / +1.92 / +23.00 | +250.46% | +171.4pp | +29.67% | −46.5% | −2.28% |

This is the finding. **A static SMH/GLD/GDX blend scores 18/19 with no signal at all** — better
than every switching rule tested here. R1's entire contribution over holding its own ON leg
forever is +21.4pp of margin and one window; its OFF leg, held alone, is *already* bear-positive
in all three bears with a bigger cycle margin than the rule that switches into it. R1 is a
weighted average of two hindsight-excellent static books, and the switch is nearly ornamental.
R2 does have real switch content — 17/19 and +143pp against its ON leg's 13/19 and +83.7pp —
which is the one place a signal earns its keep in this set.

**Design context (2010-2021), where none of these assets were chosen:** SPY +15.04%/yr.
R1 **+12.27%** (loses by 2.8pp/yr), R3 +16.85% (+1.8pp), R2 +20.51% (+5.5pp). R2's win is a
leverage artifact — static QLD/GLD already returns +22.14%/yr there — and R1, the only
bear-positive rule, is the one that *fails* the out-of-sample era outright.

---

## Verdict

**Against the stated bar (≥16/19, bear-positive, cycle margin ≥ +5pp), exactly one rule
survives engine costs: R1, in both its base-daily and weekly forms.** Weekly is the better
of the two — 16/19, bears +0.16/+4.06/+1.55, cycle +277.4% vs SPY +79.1% (+198.3pp),
CAGR +31.67%, maxDD −31.3%, 662%/yr turnover, 1.46pp/yr drag. R2 clears win-count (17/19)
and margin (+143pp) but is −19.44% through 2022H1 and fails the bear mandate outright.
R3 clears nothing: 15/19, all three bears negative.

**With GLD and GDX neutralised, nothing survives — and it is not close.** R1 falls to 9/19
with a **−0.2pp** cycle margin; R3 falls to 9/19 with **−0.2pp**; R2 falls to 12/19 and
+26.9pp, its remainder attributable to QLD leverage rather than the 150-day signal. There is
no rule-alpha here. **There is gold-alpha, and a trend switch is the wrapper it arrived in.**

The strongest single number against all three is section 3: a static 40/40/20 SMH/GLD/GDX
blend scores 18/19 with +190.2pp of margin and no signal whatsoever, and R1's own OFF leg —
GDX/XLE, held permanently — is bear-positive in all three bears with a larger cycle margin
than R1 itself. Every result in this family is a statement about what SMH, GLD and GDX did
between November 2021 and August 2026, and the 2010-2021 column confirms it: the one rule
that satisfies the bear mandate underperforms SPY by 2.8pp/yr in the era it was not fitted to.

If R1 ships, it should ship as the weekly variant, labelled a gold-and-miners overlay with a
semiconductor sleeve, sized on that basis — not as a trend-following strategy. Its bear
positivity has ~11 bps of cost headroom above the engine's 23.2 bps tier-B rate and 65%/month
of turnover, both of which want a live-fee check before any capital moves.
