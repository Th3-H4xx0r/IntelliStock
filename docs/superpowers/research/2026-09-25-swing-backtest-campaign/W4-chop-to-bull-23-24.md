# W4-chop-to-bull-23-24: backtest 821492 (swing-paper, doc 204, daily bars)

Window 2023-08-07 → 2024-03-28, $100k start. Read-only. Sources: the uncapped log (`logs/821492.log`, 3,324 lines), `BacktestSteps`
(final rows only: 96 trade rows, one null, so **95 fills**), `BacktestResults`, the engine's own `SwingDailyBars` (IEX) and yfinance
(auto-adjusted). Scripts and intermediate data are in `swing_bt/w4/`.

**Alignment.** A pv step dated D carries the close of D−1, so the engine's +5.71% runs from the **08-04 close to the 03-27 close**. SPY-TR
over the same span is +18.35% (`metrics.json` is already on this basis). Six pv steps fall on holidays (09-04, 11-23, 12-25, 01-01, 01-15,
02-19) and duplicate the prior close. They are dropped below, leaving 163 closes.

**Replica.** `w4/psim.py` rebuilds the funnel and regime from engine bars. It matches the logged PASSED count on 136/136 ACTIVE sessions and
the phase on 163/163. It reproduces **all 51 entries exactly** (same symbols, same sessions). It ends at +4.56% against the engine's
+5.71%: it gives full size to the three clamped buys and charges a flat ~23 bps a side. All "replica" numbers below are deltas against
that +4.56%. They measure one window and one path, so treat them as direction, not size.

## 1. Headline

| Metric | Strategy | SPY-TR | RSP-TR (equal-weight) |
|---|---|---|---|
| Return, 08-04c→03-27c | **+5.71%** | +18.35% | +12.91% |
| Excess | **−12.64 pp** vs SPY | — | −7.20 pp vs RSP |
| Max drawdown | **−9.60%**: peak 08-07, trough 10-31, back to the peak only on **03-11** | −8.66% in-window (−9.97% from its 07-31 peak) | −10.6% over the same dates |

| Book and trades | Value |
|---|---|
| Average invested / positions | 80.5% / 6.79. The book held 8 names at 79 of 163 closes |
| Fills / round trips | 95 fills (51 buys, 44 sells). 44 closed trips; 7 open at the end (+$2,292) |
| Win rate; average win / loss | 47.7% (21 of 44); +8.75% / −6.84%. Payoff 1.28, so the breakeven win rate is 43.9% |
| Expectancy | +0.60% a trade (+$78) |
| Holding period | 21.6 trading days on average (31.1 calendar), median 16 |
| Turnover | $590.6k of buys, 5.9× equity in 7.7 months, or about 9× a year one-way |

| Exit type | n | Average net | Hold (trading days) | P&L |
|---|---|---|---|---|
| Bracket target (+9% on the prior close) | 18 (5 gap fills) | +8.69% | 22.5 | +$18,519 |
| Bracket stop (−6% on the prior close) | 21 (3 gap fills) | −6.92% | 18.0 | −$16,712 |
| RSI crosses 70 (SYK, COP, FDX) | 3 | +9.13% | 47.0 | +$3,097 |
| Close-based stop, sold at the next open (AOS, REG) | 2 | −5.99% | 13.0 | −$1,489 |
| Close-based target | 0 | — | — | — |
| Open at the end | 7 | +2.54% | 21.9 | +$2,292 |

**Costs.** The engine charged **$2,036.50**: $1,994.53 spread, $33.22 fees and $8.75 slippage. That is 2.04% of equity, or 18.4 bps a
fill on $1.107M of fills. The flat 95 × 23 bps estimate is $2,547 (2.55%). Annualised, costs run about 3.2% a year.
Fills averaged +0.48% above the signal close, so the prior-close stop sat **−6.44% from the fill** on average. Across entries it ranged
from −4.4% to −10.3% (WRK entered on a +4.7% gap).

## 2. Path

| Month (close to close) | Strategy | SPY-TR | RSP-TR | vs SPY | Average invested | Average positions |
|---|---|---|---|---|---|---|
| Aug (from 08-04c) | −0.95% | +0.79% | −0.94% | −1.74 | 91% | 7.7 |
| Sep | −4.74% | −4.74% | −5.08% | 0.00 | 80% | 7.3 |
| Oct | −3.94% | −2.17% | −4.14% | −1.77 | 63% | 5.7 |
| **Nov** | +4.51% | **+9.13%** | +9.18% | **−4.62** | **56%** | 4.5 |
| **Dec** | **−0.74%** | +4.57% | +6.82% | **−5.31** | 83% | 6.7 |
| Jan | +2.58% | +1.59% | −0.85% | +0.99 | 96% | 7.8 |
| Feb | +3.41% | +5.22% | +4.05% | −1.80 | 89% | 7.4 |
| Mar (to 03-27c) | +5.99% | +3.29% | +4.11% | +2.70 | 90% | 7.7 |

**Three phases.** In the pullback (08-04c → 10-27c, SPY's closing low) the strategy made −9.28% against SPY's −7.76% and RSP's −11.30%:
**−1.5 pp** vs SPY. In the rally (10-27c → 12-29c) it made **+3.64% against SPY's +16.21%** and RSP's +18.52%: **−12.6 pp**. In Q1
(12-29c → 03-27c) it made +12.44% against +10.41%: **+2.0 pp**. The whole deficit is the rally. The cumulative gap was −5.4 pp by 11-07,
−13.2 pp by 12-29 and widest at −17.2 pp on 02-14; a strong March narrowed it to −12.6 pp.

**Worst drawdown windows** (non-overlapping, NAV at the close, P&L by symbol including trades):
1. **08-07 → 10-31, −9.60%** (SPY −6.89%, RSP −10.61%, 78% invested), in two legs:
   - 08-07 → 10-03, −6.48% (SPY −6.13%): the day-1 cohort, bought a week after SPY's 07-31 peak, stopped out one by one (ICE −993,
     LYV −980, ECL −906, AOS −864, PNW −836). SPY lost as much: this leg was the market.
   - 10-10 → 10-31, −4.93% (SPY −3.76%): the 10-11 gate-flicker cohort was stopped into the low (CMCSA −1,323 on an earnings gap,
     CCL −837, JPM −832, TAP −820), and IT gave back −851 of open gain. This leg was bad timing.
2. **12-19 → 01-17, −3.77%** (SPY −0.54%, RSP −2.12%, **96% invested**): COP −929, CEG −902, BG −757, PXD −733, AON −692, CMCSA −663.
   The book was fully invested and heavy in energy (COP and PXD, the second held under the "unknown" sector). Bad picks.
3. **02-02 → 02-13, −3.22%** (SPY −0.05%, 87% invested): EPAM −742 (it later hit its target), ZTS −579, CCL −496 (stopped 02-05),
   MS −453, AMT −338 (stopped 02-05), CCI −311. Rate-sensitive REITs fell into the 02-13 CPI drop. Bad picks.

**Where it lagged most** (rolling 20 sessions; no defensive ETF was ever held):
- **10-26 → 11-24, −6.8 pp** (NAV +3.6% against SPY +10.4%, 51% invested). Cash; see §3.
- 11-24 → 12-22, −5.4 pp (NAV −1.0% against +4.5%, 77% invested). Picks: AON −1,193 (gap), MRO −680 and FANG −599 (energy, both
  stopped 12-06), BG −515, HUM −399. Then 01-17 → 02-14, −2.5 pp (90% invested), picks again: ZTS, AMT, CCL, CCI.

## 3. Regime: how the pullback was handled, and was re-entry in time?

- **163 sessions: 136 ACTIVE, 21 BLOCKED, 6 bear mode** (10-10 and 11-01..11-07). VIX never blocked (it peaked at 21.7). All 27 blocks
  came from the SPY leg: **19 buffer-only** (above the plain SMA200, below 1.03×) and **8 below the plain SMA200** (10-23, 10-24, 10-26..11-02).
- **Closing was late.** The first block came on 09-25, on the 09-22 close of 430.28 against a 430.64 threshold. By then SPY-TR was
  already −5.65% off its 07-31 peak, 57% of its −9.97% fall.
  - Closing the gate sells nothing. The book shrank through stops instead: EXPE, ICE, REG (close-based), PNW and CMCSA all went
    between 09-22 and 10-06, taking the book from 8 names to 4.
- **It flickered.** The gate was ACTIVE for 09-26 (1 session), 10-11..10-13 (3) and 10-17..10-18 (2). 09-26 bought CTRA (+9.4%).
  **10-11 bought CCL, CMCSA, JPM and TAP, and 10-17 bought BKR. All 5 lost, −$4,543 (−4.5% of NAV)**, the window's biggest loss
  cluster. Four were stopped 10-18..10-27; JPM and TAP went on 10-27, **the day of SPY's closing low**.
- **Bear mode bought nothing.** On 10-10, 0 of the 5 defensive ETFs were above their SMA200; on 11-01..11-07 only GLD was, and its RSI
  was not "below 50 and rising". The 6 bear sessions were plain cash (13–40% invested) while **SPY rose +4.38%**, a −3.10 pp cost.
- **SPY during the blocked stretches:**

  | Blocked sessions | SPY | Entry signals the gate blocked (replayed with the real bracket) |
  |---|---|---|
  | 09-25, 09-27..10-10 | +2.0% (09-27 → 10-11 opens) | 194 passers, **−1.02%** a trade, 37% won. The buffer helped, as it did in W2 |
  | 10-16, 10-19..10-31 | −3.43% (10-19 → 10-31 opens) | 64 passers, +3.47% a trade, 67% won |
  | 11-01..11-07 (bear mode), 11-10 | **+4.38%** (11-01 → 11-08 opens) | 67 passers, **+4.61%**, 78% won |
  | The 8 sessions **below the plain SMA200** | — | 89 passers, **+5.34%** a trade, **81%** won: the best signals of the window |

- **Re-entry was not in time.** The gate re-opened on 11-08 (the 11-07 close of 436.89 cleared 436.85 by 0.01%), blocked again on
  11-10, and stayed ACTIVE from 11-13. At the 11-08 open SPY was already **+6.5% off its 10-27 closing low**, and the book held one name
  (BKR), about 12% invested.
  - After that, the entry signal was the bottleneck: in a straight-up tape almost nothing has RSI below 50 and rising. The filter kept
    **4% of above-SMA200 names in Nov and 3% in Dec, against 13–19% in Aug–Oct**. Passers averaged **1.4 a session in Nov and 1.8 in
    Dec** (11 zero-pass sessions), against 7–9 in Aug–Oct.
  - The book did not reach 8 names until the 12-12 close. Idle slot-sessions: **24 in Nov and 11 in Dec**, against 5 in the other six months combined.
- **Did the gate help or hurt? It hurt here.** The no-gate replica makes +9.15% against +4.56% (about −4.6 pp), with no better drawdown
  (−10.18% against −10.33%). The strategy's drawdown (−9.60%) was deeper than SPY's (−8.66%), so the earlier research's "drawdown benefit
  from the regime filter" did not show up. This matches W3's V-bottom: a 200-day gate is shut exactly when a pullback entry works best. W2 is the counter-case.

**Did the +9% target and −6% stop cap the rally? Yes, and more than the gate did.**
- **The stop sold the book at the low.** CCL (10-18), EXPD (10-25), CMCSA (10-26), JPM and TAP (10-27) were stopped out within 7
  sessions of the low. Over the next 20 sessions they rose CCL +20.4%, JPM +12.1%, EXPD +8.7%, TAP +6.8% and CMCSA +6.6%.
- **The target sold the leaders.** IT and COR (11-03), PANW (11-15), BX (11-28), PNR (12-06) and ADSK (12-11) hit the target and then rose
  **another +10.9% on average in 20 sessions (+6.6 pp vs SPY)**: BX +22.9%, IT +18.0%, PANW +13.8%. The funnel was dry, so the freed slots sat idle.
- **Across the window**, the 18 target exits rose +5.97% in the next 20 sessions (+2.5 pp vs SPY; 14 of 18 higher). The 21 stops rose +2.33% (−0.3 pp vs SPY).
- **Replica, rally segment (10-27c → 12-29c):**

  | Exit rule | Rally segment | Whole window | Max drawdown |
  |---|---|---|---|
  | Actual | +3.59% | +4.56% | −10.33% |
  | No target | +6.20% | +6.41% | −10.87% |
  | Stop −10% | +8.41% | +5.14% | −12.04% |
  | Stop −10%, no target | +11.44% | +10.58% | −11.95% |
  | **RSI-70 exit only** | **+18.97%** (above SPY's +16.2%) | +15.36% | −10.71% |

  Against that, the gate variants add +3 to +5 pp on the rally segment.

## 4. Funnel

The typical ACTIVE session, as mean (median):

universe 505 → not held 497.9 → has indicators 494.2 → close > SMA200 **310.7 (331)** → RSI < 50 and rising **25.7 (20)** →
MACD histogram up 19.1 (14) → volume > 20-day average **5.9 (5)** → ADX > 15 5.1 (4) → rr_ok 5.1 → **PASSED 5.1 (4)**.

- **The RSI filter kills the most.** It removed 38,764 candidates and kept 8.3%. SMA200 removed 24,954 (kept 63%); volume kept 31%,
  ADX 87%; rr_ok never binds. 16 sessions had zero passers and 65 had three or fewer. Passers by month: Aug–Oct 7.9, 7.0, 8.8;
  **Nov–Dec 1.4, 1.8**; Jan–Mar 6.5, 5.8, 4.2.
- **Full book.** 75 of 136 ACTIVE sessions (55%) began with 8/8 held.
- **Sector cap.** It fired 529 times (464 itemised plus 65 in the "+N more" summaries), against 120 no-slot skips. It left slots empty
  on 24 sessions, 14 of them between 11-14 and 12-20.
- **Did capped names later outperform? Not overall.**

  | Group | Unique names | Bracket replay | 20-day excess vs RSP |
  |---|---|---|---|
  | Entered | 47 | +0.90% | −0.33% (s.e. 0.93) |
  | Sector-capped | 225 | −0.38% | +0.04% (s.e. 0.42) |
  | No-slot | 74 | +0.40% | +0.27% (s.e. 0.76) |

  The 15 names capped **while slots sat idle in Nov–Dec** would have averaged +2.8% (9 of 15 won, e.g. REGN, VRTX and AMGN behind HUM).
  But 2 per sector across the window costs **−6.0 pp** in the replica (−1.47%), because it also doubles up the losing Aug–Oct cohorts.
  W3 found the same. Leave the cap alone.
- **The entry is still about random.** Replayed with the real bracket, ACTIVE-session passers made +0.37% a trade (s.e. 0.26, n=727);
  random above-SMA200 names on the same sessions made +0.91% (n=14,027), all members +0.58%. Within each phase the signal edges random by
  0.2–0.9 pp, inside 1–2 s.e. (Aug–Oct −2.72% vs −2.93%, Nov–Dec +2.77% vs +1.85%, Jan–Mar +2.66% vs +2.13%). Per closed trade the
  excess vs RSP is −0.28% ± 1.02. **This confirms "entry ≈ random".**

## 5. Decisions

**Top 5 winners.** Entry readings are RSI prev→now, the MACD histogram, ADX, and volume relative to its 20-day average.

| Trip | Entry | Exit | Result |
|---|---|---|---|
| FDX 01-08→03-22 | RSI 36.0→38.3, MACD −2.54→−2.31, ADX 26.1, vol 1.01× | RSI crossed 70 (69.5→74.3) on the 03-21 close and was sold at the 03-22 open for 286.99, +8.3% above that close on its earnings gap | **+15.3%, +$1,673**, 52 sessions |
| BRO 12-22→01-23 | RSI 37.8→41.3, ADX 22.7, vol 1.88× | Target gap-fill at 79.65 against the 76.74 target, the morning after Q4 earnings (+$486 over the target) | +12.1%, +$1,432 |
| COR 09-18→11-03 | RSI 41.7→45.8, MACD +0.29→+0.41, ADX 25.0, vol 1.21× | Target gap at 197.87. It rode through the whole pullback | +10.5%, +$1,274 |
| EPAM 02-02→02-16 | RSI 39.8→46.6, ADX 19.3, vol 1.27× | Target at 310.82. It then fell −5.8% in 20 sessions, so a good exit | +9.9%, +$1,179 |
| CTRA 09-26→10-13 | Bought on the one-session re-open; RSI 31.4→39.3, ADX 16.0 | Target gap. Sector "unknown" | +9.4%, +$1,129 |

**Top 5 losers:**

| Trip | Entry | Exit | Result |
|---|---|---|---|
| CMCSA 10-11→10-26 | The 10-11 flicker cohort; RSI 45.2→48.6, MACD −0.148→−0.071, ADX 24.7, vol 1.01× | The stop gapped through: filled 39.96 against a 41.78 stop, on its Q3 earnings day (public record; not verified in-data). It then rose +6.6% in 20 sessions | **−11.0%, −$1,323** |
| AON 12-11→12-20 | RSI 44.6→48.3, ADX 17.2, vol 1.05× | The stop gapped through at 293.83 against 304.62, on the day it announced the NFP deal (public record) | −10.1%, −$1,193 |
| LYV 08-07→09-05 | Day 1; RSI 35.4→40.7, ADX 45.4, vol 1.25× | Bracket stop | −7.4%, −$937 |
| ZTS 02-06→03-13 | RSI 46.7→49.0, ADX 15.2 (barely above 15); the entry gapped +1.3%, so the stop sat −7.3% from the fill | Bracket stop. It fell −8.8% more in 20 sessions | −7.4%, −$902 |
| ICE 08-07→09-26 | Day 1; RSI 39.2→49.5, ADX 18.3 | Bracket stop on the day after the gate first closed | −7.1%, −$885 |

**Patterns in the 23 losers.**
- **Timing cohorts carry most of the losses.** The **day-1 cohort** (08-07..08-10), bought a week after SPY's peak, lost 6 of 8 (AOS, ECL,
  ICE, LYV, EXPE, PNW: −$5,112, or −$3,465 net of SYK and IT). The **gate-flicker cohort** (10-11..10-17) lost 5 of 5 (−$4,543).
- **Gaps are a wash, so an earnings blackout is not supported here.** The three gap-downs through the stop (CMCSA, AON, MRO) cost −$928
  beyond their stop levels; the five gap-ups over the target made +$903. Two of the best exits were earnings gaps (FDX, BRO).
- **By sector** (small n): real estate 0 for 3 (REG, AMT, CCI; −$1,659) and consumer defensive 1 for 3 (TAP, BG, KHC; −$1,436), rate-
  sensitive names in a rising-rate autumn and again in February. Technology went 5 for 5 (+$4,458), healthcare 5 for 7 (+$2,945). The
  12-06 energy stops (MRO and FANG, −$1,585) came from MRO's "unknown" sector, which let it sit beside FANG.

## 6. Why it trailed SPY

Daily arithmetic attribution against SPY-TR (sum −11.34 pp; compounded −12.64 pp). A per-trip SPY/RSP-equivalent method gives the same
split: underexposure −8.07, equal-weight −2.83, selection −1.74.

| Driver | pp | Evidence |
|---|---|---|
| **Cash and underexposure** | **−8.09** | ACTIVE sessions −3.21, blocked −1.78, bear mode −3.10 (6 sessions). By period: pullback −0.05, **10-27→11-07 −3.88** (32% invested), 11-07→11-30 −1.70 (62%), Dec −1.17, Q1 −1.29 |
| Defensive ETFs | 0.00 | Never bought (§3) |
| Equal-weight vs cap-weight | −2.78 | Pullback −2.99, rally **+2.63**, Q1 −2.42. RSP +12.9% against SPY +18.35% |
| Stock selection vs RSP, net | −0.46 | Pullback +1.28; rally **−7.33** (Dec alone −5.93: AON, MRO, FANG, BG, HUM); Q1 **+5.59** |
| ↳ of which costs | −2.04 | $2,036.50 on 95 fills |
| ↳ of which dividends not credited | ≈ −1.1 | The result is `price_return` against a total-return benchmark. At least $1,082 is missing (ADI, BKR, CAH, CCI, CMCSA ×2, COP, FDX, ICE, KHC, MS, NUE, REG), and more for CTRA, MRO, PXD and WRK, which lack yfinance data |
| ↳ selection gross | ≈ +1.4 to +2.7 | Noise-level: −0.28% ± 1.02 per trade |
| Exits cutting winners | not in the NAV | Replica: no target +1.9 pp; RSI-70 only +10.8 pp (§3) |

**Verdict.** Over the window, picks net to about zero. The deficit is the structural −8.1 pp of cash plus −2.8 pp of equal-weight, the
cash is almost all a V-bottom effect, and costs and missing dividends take another −3 pp:
- The −6% stops emptied the book at the 10-27 low. The 1.03× gate kept it empty for the first +6.5% of the rally, and bear mode had nothing to buy.
- Once the gate opened, "RSI below 50 and rising" found almost nothing to buy, while the +9% target sold each leader after about 9%.
- Bad December picks (−5.9 pp) doubled the rally's damage, and Q1 picks (+5.6 pp) gave it back. That is the noise in an 8-name book.
- Q1 shows the same machinery beating SPY in a choppy uptrend (+2.0 pp) once the book is full.

**Against the earlier research:** entry ≈ random is **confirmed**; trailing SPY after costs is **confirmed** (−12.6 pp, and about
−9.5 pp even gross of costs and dividends); the drawdown benefit from the regime filter is **not seen here** (−9.6% against −8.7%).

**Known issues, checked against this run** (all apply except the last):
- Rows stored twice: 191 trade rows reduce to 95 fills.
- Clamped entries after same-session exits. EXPD filled **2 of 104 shares** on 08-21, the session of AOS's close-based stop exit. The stub
  held a slot and the industrials sector for 46 sessions and blocked 47 industrials skips (26 names), which would have averaged −1.6%: it
  saved money by luck. CMCSA filled 248 of 273 (09-12, after SYK's RSI exit), JBHT 19 of 65 (03-22, after FDX's). No entry was dropped.
- "Unknown" sectors: CTRA, MRO, PXD and WRK. MRO sat beside FANG (both stopped 12-06, −$1,585), PXD beside COP (both won, +$1,902).
  Capping "unknown" as a sector moves the replica +0.45 pp.
- Alphabetical tie-break: unchanged. Defensive ETFs losing money: not tested, since none was bought.

## 7. Change hypotheses (evidence only; nothing implemented)

1. **Put idle cash in an index sleeve (SPY or RSP) while the regime is ACTIVE, and sell it to fund entries.**
   - *Observation:* underexposure is the biggest driver (−8.1 pp). −3.2 pp of it fell on ACTIVE sessions: 35 idle slot-sessions in
     Nov–Dec, when the funnel passed 1.4–1.8 names a session and target exits freed slots it could not refill. W1's H2 and W3's H3
     point the same way (a separate index sleeve, not a gate tweak).
   - *Expected effect:* +1.5 to +3 pp here, gross.
   - *Risks:* under the engine's flat 23 bps, a sweep traded around every entry and exit (about 95 extra fills, ~$2.6k) eats most of
     the gain, so sweep whole idle slots after a few idle sessions, or price it at real ETF costs. A regime-gated sweep does nothing
     for the −4.9 pp lost on blocked and bear sessions. It adds beta: judge it on W2.
2. **Replace the fixed −6%/+9% bracket with a volatility-scaled stop and a trailing or RSI-70 exit (no fixed target), anchored on the fill.**
   - *Observation:* five stops fired within 7 sessions of the 10-27 low, and those names rose 6.6–20.4% in the next 20 sessions. The
     rally's target exits rose +10.9% more (+6.6 pp vs SPY). The fill-relative stop ranged from −4.4% to −10.3%.
   - *Replica:* no target +1.9 pp; stop −10% with no target +6.0 pp; RSI-70 exit only +10.8 pp (rally segment +19.0% against +3.6%).
   - *Risk: highly regime-specific.* Per trade on each window's real entries (actual / stop −10% no target / RSI-70 only):
     W2 −4.7% / −8.1% / **−11.0%** (worst −45%); W3 −2.1% / +0.6% / +2.5%. A wider stop without volatility scaling is a bet on the
     regime. Test every variant on W1–W6.
3. **Require confirmation before stock entries after a gate opening: 3 consecutive ACTIVE sessions, or at most 2 entries a session.**
   - *Observation:* this is the third window with the pattern. The 5 entries of the 10-11 and 10-17 flickers lost 5 of 5 (−$4,543). W2 lost 10 of 11 and W3 14 of 16.
   - *Replica (actual max drawdown −10.3%):* 3-session confirmation +0.5 pp, drawdown **−6.1%**; confirmation plus at most 2 a
     session +1.5 pp, −6.3%; at most 2 a session alone +3.5 pp, −7.0% (W3 found the cap alone worse).
   - *Risk:* it delays re-entry after a real V-bottom (rally segment +2.0% against +3.6%). The evidence rests on about 10 openings, all in 2022–23.
4. **Exit the stock book at the next open when the gate closes.**
   - *Observation:* the gate sells nothing. 7 stop exits worth −$5.2k fired while it was shut (PNW, REG, CMCSA ×2, EXPD, JPM, TAP).
   - *Replica:* +4.8 pp, max drawdown −7.8% against −10.3%, pullback −6.9% against −10.0%. With 3-session confirmation, drawdown falls to −5.9% but the rally segment to +0.0%.
   - *Risk:* mixed across windows. It helped in W2 (+$5.8k) and here, but hurt in W3 (−14.4% against −9.0%), where each close came right after a dip that recovered.
5. **Do not loosen the gate or the sector cap on this window's evidence.**
   - *Observation:* the 8 sessions below the plain SMA200 held the best signals (+5.34% a trade, 81% won), and no gate scores +4.6 pp.
     But the buffer-only sessions at the top of the pullback lost −1.02% a trade, and in W2 the buffer saved 3.4% a trade. Two names
     per sector costs −6.0 pp here and was also worse in W3.
   - *Implication:* no rule with a 200-day filter tells a V-bottom from a bear-market rally ahead of time. The recovery lag is structural, and hypothesis 1 is the lever, not the gate.
   - *Risk:* this rests on two V-bottoms (W3, W4) and one bear (W2).

## Data caveats
- The entry replays use the engine's IEX bars, so replayed fills can differ from the engine's by a few bps. Every "replica" figure is one path, and the alphabetical order is part of that path.
- The earnings and news attributions (CMCSA 10-26, AON 12-20, BRO 01-22, FDX 03-21) come from public record and were not verified in-data.
- yfinance lacks CTRA, MRO, PXD and WRK, so the dividend figure is a lower bound.
