# W7-recent-25-26: backtest 554589 (swing-paper, doc 204, daily bars)

Window 2025-07-07 → 2026-09-18, $100k start. Read-only. Sources: the uncapped log (`logs/554589.log`, 7,150 lines), `BacktestSteps`
(529 trade rows → 265 final, one null, so **264 fills**), `BacktestResults`, the lane's own `SwingDailyBars` (IEX, split-adjusted),
membership, sector and VIX rows, and yfinance (auto-adjusted). Scripts and intermediate data are in `swing_bt/w7/`.

**Alignment.** A pv step dated D carries the close of D−1, so the engine's −6.15% runs **2025-07-03c → 2026-09-17c**. Ten pv steps repeat
the prior close (holidays) and are dropped, leaving 304 closes. Over the same span: SPY-TR **+23.30%**, RSP-TR **+17.10%**, QQQ +29.51%.

**Replica.** `w7/psim7.py` (the W6 simulator, re-pointed and extended) matches the logged PASSED count on 278/278 ACTIVE sessions, the
ACTIVE/blocked call on 304/304 and **136 of 137 logged entries** (the 137th, AKAM 09-10, exists only because the engine dropped AMAT on
09-09; see the known-issues list at the end of §6). It ends −5.94% (engine −6.15%), max DD −12.86% (−12.33%). Every variant was also run under **30 random tie-break
orders, paired**. Those runs show how much of a result comes from the alphabetical path. They vary the order only, not the market path.

## 1. Headline

| Metric | Strategy | SPY-TR | RSP-TR (equal-weight) |
|---|---|---|---|
| Return, 07-03c→09-17c | **−6.15%** | +23.30% | +17.10% |
| Excess | **−29.45 pp** vs SPY | — | **−23.25 pp** vs RSP |
| Max drawdown | **−12.33%**: peak 2025-09-11, trough **2026-06-18**, regained 08-24, then −6.6% again by the end | −8.88% (01-27 → 03-30-2026) | −7.85% (02-27 → 03-30) |

| Book and trades | Value |
|---|---|
| Average invested / positions | **87.8% / 7.19** over 304 closes; 8 names at 149 closes. NAV closed above $100k on only **5 of 304 closes** |
| Fills / round trips | 264 fills (136 buys, 128 sells); 128 closed trips; 8 open at the end (XLP, QQQ, ACGL, ED, LIN, AME, ABBV, BALL: −$225) |
| Win rate; average win / loss | 42.2% (54 of 128); +8.22% / −6.61%. The payoff is 1.24, so the breakeven win rate is 44.6% |
| Expectancy | **−0.36% a trade (−$46)**. Best trip +12.2% (C), worst −12.3% (CDNS): the bracket caps both tails |
| Holding period | 15.2 trading days on average (22.1 calendar), median 9 |
| Turnover | $1.55M of buys: **16.2× equity in 14.5 months** (~9.4 entries a month) |

| Exit type | n | Average net | Hold (trading days) | P&L |
|---|---|---|---|---|
| Bracket target (+9% on the prior close) | 50 (8 gap fills) | +8.38% | 18.3 | +$47,742 |
| Bracket stop (−6% on the prior close) | 70 (14 gap fills) | −6.62% | 11.8 | **−$53,226** |
| RSI crosses 70 (FRT, AEP, EA) | 3 | +5.19% | 38.3 | +$1,723 |
| Close-based target, sold at the next open (T) | 1 | +9.31% | 17 | +$979 |
| Close-based stop, sold at the next open (ADSK, JKHY, HLT, ESS) | 4 | −6.46% | 18.3 | −$3,158 |
| Open at the end | 8 | +0.10% | 29.9 | −$225 |

**Costs.** The engine charged **$5,557.49** ($5,443.46 spread, $90.14 fees, $23.89 slippage): **5.6% of starting equity**, 18.5 bps a
fill on $3.00M of fills, about **4.6% a year**. At a flat 23 bps on the same notional that would be $6,910; 264 × 23 bps × $12.5k gives $7,590.
Fills averaged +0.41% against the signal close. So the prior-close stop sat −6.37% from the fill on average, ranging from −10.4% (CASY,
which opened +5.0% above its signal close) to −1.75% (T, which opened −4.3% below its signal close).

## 2. Path

| Month | Strategy | SPY-TR | RSP-TR | QQQ | vs SPY | vs RSP | Avg invested |
|---|---|---|---|---|---|---|---|
| 2025-07 (from 07-03c) | −2.81% | +1.08% | −0.84% | +1.58% | −3.89 | −1.97 | 79% |
| 2025-08 | +2.45% | +2.05% | +2.72% | +0.95% | +0.40 | −0.27 | 92% |
| 2025-09 | +0.40% | +3.56% | +1.01% | +5.38% | −3.17 | −0.61 | 83% |
| **2025-10** | **−6.35%** | +2.38% | −0.93% | +4.78% | **−8.74** | **−5.42** | 92% |
| 2025-11 | +1.63% | +0.19% | +1.92% | −1.56% | +1.43 | −0.29 | 93% |
| 2025-12 | +0.49% | +0.08% | +0.41% | −0.67% | +0.41 | +0.07 | 91% |
| 2026-01 | −0.68% | +1.47% | +3.40% | +1.23% | −2.15 | −4.08 | 91% |
| 2026-02 | +2.98% | −0.86% | +3.48% | −2.34% | +3.85 | −0.50 | 81% |
| 2026-03 | −6.39% | −4.94% | −5.97% | −4.84% | −1.45 | −0.42 | 76% |
| **2026-04** | +2.53% | **+10.51%** | +6.00% | +15.69% | **−7.98** | −3.48 | 88% |
| **2026-05** | **−3.99%** | +5.26% | +2.65% | +10.57% | **−9.25** | **−6.64** | 96% |
| 2026-06 | −0.41% | −1.03% | +2.28% | −0.15% | +0.61 | −2.70 | 91% |
| **2026-07** | **+6.79%** | +0.03% | +1.05% | −6.57% | **+6.76** | **+5.74** | 88% |
| 2026-08 | +1.90% | +2.68% | +2.04% | +4.18% | −0.78 | −0.14 | 90% |
| 2026-09 (to 09-17c) | −3.89% | −0.58% | −2.77% | +0.02% | −3.31 | −1.12 | 91% |

It beat SPY in 6 of 15 months and RSP in 2 of 15. Rolling windows: it beat SPY in 26% of 63-session windows and RSP in 10%, and in
**0% of 126-session (6-month) windows, against either benchmark**. Only 24% of those 6-month windows were even positive.

| Phase | Strategy | SPY-TR | RSP-TR | QQQ | vs SPY | vs RSP | Invested |
|---|---|---|---|---|---|---|---|
| Bull grind, 07-03c → 01-27c (SPY's peak) | −3.04% | +11.86% | +8.32% | +13.75% | −14.89 | −11.36 | 89% |
| Correction, 01-27c → 03-30c (SPY's low) | −7.37% | −8.88% | −5.05% | −11.43% | +1.52 | −2.31 | 79% |
| V-rebound, 03-30c → 06-18c | −2.21% | **+18.47%** | +11.64% | **+32.66%** | **−20.67** | −13.85 | 92% (36% defensive ETFs) |
| Recent, 06-18c → 09-17c | +6.84% | +2.12% | +1.99% | −3.09% | +4.72 | +4.85 | 89% |

The strategy tracks breadth. Monthly excess vs SPY correlates **0.80** with RSP−SPY (slope 1.42, intercept −1.3 pp a month). Daily, it
has a beta of 0.59 to SPY and a 0.70 loading on RSP−SPY, and alpha is −12%/yr (R² vs RSP 0.33, tracking error 10%/yr). Its only strong stretch, 06-18 →
08-24 (**+14.3%** vs SPY +2.2%, RSP +6.1%), is the one where QQQ fell (−4.5%).

**Worst drawdown windows** (NAV at the close, P&L by symbol including trades):
1. **2025-09-11 → 2026-06-18, −12.33%, while SPY rose +14.8% and RSP +11.7%.** Three legs, all picks:
   (a) 09-11 → 11-20-2025, **−9.84%** (SPY −0.5%, RSP −3.9%, 90% invested): CTVA −1,589 (stopped twice), DRI −1,425, APA −854,
   BA −836, DLTR −791, DIS −766, BR −761, CSGP −758, ADSK −758, against EW, DELL and DG at about +1k each. Of the October entries, 1 of 9 won (−$4,694).
   (b) 02-20 → 03-30-2026, **−9.78%** (SPY −8.1%, RSP −7.5%, 77% invested): NUE −1,570 (two gap stops), TSLA −1,287 (two stops),
   AMD −884, CFG −882, EL −847, QQQ −817, BAC −701, BK −627. It fell more than SPY despite the gate (§3).
   (c) 04-23 → 06-18-2026, **−7.27%** (SPY +5.7%, QQQ +13.7%, 95% invested): GLD −1,140 (a bear-mode leftover), HII −1,092, EQT −824,
   CME −720, CAH −701, DG −688, BALL −643, DVN and HAL −642 each. Only DE (+1,008) did much.
2. **2026-08-24 → 09-16, −6.86%** (SPY −1.2%, RSP −4.4%, 93% invested): HLT −1,080, ESS −870, HWM −851, LIN −705 (still held), EME −681,
   HRL −647, AKAM −596. Every name lost except BALL and ABBV. This is the most recent behaviour, and it undid the whole summer gain.
3. **2025-07-03 → 08-06, −2.86%** (SPY +1.2%, RSP −1.3%): the first-week cohort (07-07..07-10) stopped out 5 of 6 (EQT −924, AJG −803,
   PAYC −798 after one session, PM −798 on a −6.5% gap). DTE, the sixth, won but took 154 sessions.

**Where it lagged most** (rolling 20 sessions; none of these windows was cash-driven, since invested was 84–96%): **04-08 → 05-06-2026, −11.0 pp**
(NAV −2.4% vs SPY +8.6%): picks (HII, GD, DG, CAH, BALL) plus the defensive ETFs. **10-03 → 10-31-2025, −7.6 pp** (NAV −5.7% vs SPY +1.9%, RSP −1.7%):
picks (DHI −1,406, CSGP, BA, HSY, ORLY, MA). **08-19 → 09-17-2026, −5.4 pp**, then 08-21 → 09-19-2025 and 05-19 → 06-17-2026, about −5 pp each. The
cumulative gap to SPY bottomed at **−33.3 pp on 2026-06-01**.

## 3. Regime: the gate barely acted, and where it did, it cost

- **304 sessions: 278 ACTIVE (91%), 13 blocked, 13 bear mode (03-25 → 04-13-2026).** 8 flips: VIX-only one-session blocks on
  2025-10-17 and 11-21; then blocked 03-09/10, a **one-session re-open on 03-11**, blocked 03-12..03-24, bear mode 03-25..04-13, ACTIVE from 04-14.
  Of the 26 non-ACTIVE sessions, 13 were below the plain SMA200, 6 were buffer-only and 16 had VIX > 25.
- **Late close.** SPY-TR peaked on 01-27. The first block came on 03-09, with SPY-TR 3.3% off its peak at the 03-06 close: 37% of the −8.88% fall
  was already done. By then the pre-flip cohort was in the book. Of 10 entries between 02-17 and 03-06, **1 won (−$5,645)**, 4 of the 9 losers through gap stops. The
  03-11 flicker cohort (C +12.2%, GM +8.1%, NUE −8.8%, EL −7.2%) netted +$773.
- **Late re-open.** The gate re-opened on 04-14, 10 sessions after the 03-30 low, with SPY already +8.8% off it: **50% of the low → 06-18
  rebound missed** (W6: 78%). Alternative triggers: VIX ≤ 25 on 04-02 (+3 sessions, +2.3%); SPY > SMA50 and SMA200 on 04-09 (+7, +6.8%).

| Non-ACTIVE stretch | SPY (open → open) | GLD | XLP | XLV | Signals the gate blocked, replayed with the bracket |
|---|---|---|---|---|---|
| 2025-10-17, 11-21 (VIX-only) | +1.19%, +1.17% | +0.1%, +0.2% | +1.1%, +0.6% | +1.5%, +2.2% | 8 passers, +3.0% |
| 03-09..03-10 | +1.68% | +1.72% | +0.33% | +0.70% | 22, +1.20%, 50% won |
| 03-12..03-24 (blocked) | −1.59% | **−11.64%** | −2.42% | −3.73% | 283 over 03-12..04-13: **+2.63%, 60% won** |
| 03-25..04-13 (bear mode) | **+4.41%** | +4.67% | −0.45% | +1.16% | (same) |

Across all 26 sessions SPY made about **+7.0%**. Blocked-session signals replay at +2.47% a trade (58% won, n=161) and bear-session
signals at +2.61% (61%, n=152), against **+0.25% for ACTIVE** signals. The below-SMA200 sessions were the best (+3.45%, 65% won).
**Did the gate help or hurt? It hurt return and gave no drawdown benefit.** No gate: **+4.11 ± 1.43 pp paired** (better in 19 of 30 orders), max DD
−11.2% vs −10.5%. The drawdown was deeper than SPY's (−12.3% vs −8.9%) because of picks, not market exposure. (In W6 the gate saved about 4 pp in the crash.)

**Bear mode's defensive ETFs: −5.5 pp, the largest non-selection loss.** Bear mode bought XLV on 03-26, then GLD and XLP at the **03-30
open, the day of SPY's low**. No rule sells them on re-open, so they held 36% of the book through the V-rebound:
- **GLD**: −11.9% from the 04-14 open to 06-18 (SPY +8.9%). It was stopped on 06-09 at −7.5% (−$853).
- **XLV**: took 63 sessions to reach its +9% target (06-26, +$1,027). SPY rose +13.3% over the same hold.
- **XLP**: a zombie. It was still held at the end after **119 sessions**, at +1.7% against SPY +21.1% over the hold, and its bracket (76.87 / 89.14) was never touched.

Attribution: **−6.26 pp in the rebound phase** (§6). Selling defensives at the next open on re-open: **+1.83 ± 1.16 pp paired** (20 of 30),
but −0.18 ± 0.08 in W6, where they were bought on the last bear session. No bear mode: +1.59 ± 1.15.

## 4. Funnel

Typical ACTIVE session, mean (median): universe 505 → not held 498.5 → indicators 496.4 → close > SMA200 **300.2 (301)** → RSI < 50
and rising **30.6 (28)** → MACD up 21.9 (20) → volume > 20-day average **7.8 (6)** → ADX > 15 6.5 (6) → **PASSED 6.5 (6)**.

- **The RSI filter kills the most**: it removed 74,933 and kept 10.2%. SMA200 removed 54,546 (kept 60.5%). The volume filter kept 35.6%, MACD 71.5%,
  ADX 83.2%, and rr_ok never binds. 16 sessions passed nobody. The busiest: 10-15-2025 (36), 11-24 (30), 07-31-2026 (30). September 2026 averaged 13.1.
- **Full book**: 145 of 278 ACTIVE sessions (52%) began at 8/8. Only 29 slot-sessions sat idle for lack of passers, 19 of them in the first month.
- **Sector cap**: **1,300 skips** (1,150 itemised plus 150 in "+N more" lines) against 342 no-slot skips. The cap left slots empty on 37 sessions.
  The biggest blockers were **DTE (115 utility skips while it sat 154 sessions)**, MA (61), CDNS (44), ADSK (43) and PNR (40).
- **Did capped names later outperform?** A little, but within noise, and loosening the cap hurts:

  | Group | Unique names | Bracket replay | 20-day excess vs RSP |
  |---|---|---|---|
  | Entered | 118 | −0.08% (44% won) | −0.92% (s.e. 0.86) |
  | Sector-capped | 373 | +0.74% (47% won) | +0.70% (s.e. 0.67) |
  | No-slot | 193 | −0.07% (42% won) | −0.43% (s.e. 0.81) |

  Allowing 2 names per sector: **−8.0 ± 1.4 pp paired** (better in 6 of 30 orders). A more concentrated book takes correlated stops.
- **Mega-caps**: the 20 largest names made up 4.9% of all passes (94 passes, proportional to their count), but were **entered only 8 times** (AMZN, GOOG,
  TSLA ×3, LLY, WMT, MA). NVDA, MSFT, AAPL, AVGO and ORCL: 0 entries, mostly sector-capped behind a tech name already held (ADSK, CDNS, DELL and others).
  Their passes replay at −1.70% a trade. The pullback signal does not catch mega-cap trends.
- **Entry ≈ random, confirmed.** ACTIVE passers replay at +0.25% a trade (s.e. 0.17, n=1,878, overlapping), against −0.23% for random
  above-SMA200 names and −0.21% for all members. Their 20-day excess vs RSP is −0.38% (s.e. 0.23), against −0.05% for random names. There is no persistent
  monthly edge: Dec +3.48% vs +1.58% random, Feb −2.49% vs −1.76%, Aug −2.33% vs −2.40%.

## 5. Decisions

**Top 5 winners.**

| Trip | Entry (RSI prev→now, MACD hist, ADX, volume) | Exit | Result |
|---|---|---|---|
| C 03-11→04-08-26 | The one-session re-open; RSI 38.6→43.6, MACD −0.76→−0.59, ADX 22.4, vol 1.37× | Held through bear mode; target gap-fill at 122.01 vs 118.78 | **+12.2%, +$1,417** |
| TRGP 08-07→08-18-26 | RSI 39.9→48.4, ADX 19.4, vol 1.21×; opened −2.0% below the signal close | Target gap: +5.7% open on 3.1× volume | +11.6%, +$1,376 |
| AMZN 06-30→07-31-26 | RSI 37.4→46.3, MACD −1.53→−0.66, ADX 32.8 | Target gap: +12.3% open on 3.1× volume | +11.2%, +$1,230 |
| CAT 07-31→08-04-26 | RSI 28.9→34.8, ADX 23.4; opened +4.4% above the signal close | Target gap after 2 sessions; −16.0% over the next 20 | +9.9%, +$1,167 |
| MPC 01-06→02-04-26 | RSI 30.1→48.3, MACD −0.88→+0.14, ADX 39.3, vol 1.98× | Target gap, then +13.3% over the next 20 sessions and **+120% by 09-17** | +9.4%, +$1,118 |

**Top 5 losers.** All five were gap stops on event-sized volume.

| Trip | Entry | Exit | Result |
|---|---|---|---|
| CDNS 06-29→07-17-26 | RSI 45.1→49.8, vol 2.01×; filled +1.8% above the signal close | −7.6% gap on 2.8× volume; re-bought 07-22 and stopped again 08-11 (−$594) | **−12.3%, −$1,412** |
| DRI 07-21→09-18-25 | RSI 36.5→42.1, ADX 20.9 | −9.2% gap on **8.1× volume** after 42 sessions | −9.9%, −$1,225 |
| HII 04-30→05-05-26 | RSI 33.3→33.9, ADX 32.1 | −8.3% gap on 4.7× volume; −13.5% more over the next 20 sessions (the stop helped) | −9.3%, −$1,092 |
| COF 01-16→01-23-26 | RSI 44.3→47.0, ADX 37.7; filled +1.9% above the signal close | −5.6% gap on 3.1× volume; −14.0% more over the next 20 | −8.3%, −$1,023 |
| LVS 01-22→01-29-26 | RSI 29.0→35.9, ADX 33.9, vol 1.86× | −9.5% gap on 2.7× volume | −8.4%, −$987 |

**Patterns in the 74 losers** (70 bracket stops and 4 close-based stops):
- **Event gaps decide the result.** 18 trips had at least one open gap of 4% or more on at least 1.8× volume while held. Together they made **−$6,413**,
  13 of the 18 lost, and **the other 118 trips made +$249**. The 14 gap stops lost $2,430 beyond their stop levels; the 8 gap targets gained $1,574 beyond theirs.
  This is a volume/gap proxy: there was no earnings calendar in-data (yfinance needs `lxml`), so the earnings link is unverified.
- **Sectors**: consumer cyclical 22 trips −$4,886 (32% won); basic materials 8 trips −$4,208 (1 won: CTVA ×2, NUE ×2, DOW and FCX stopped);
  technology −$2,772. Healthcare +$6,060 (71% won).
- **Timing cohorts**: pre-flip (02-17..03-06) 1 of 10 won, −$5,645; October 2025 entries 1 of 9, −$4,694; the three entries after the 10-17 VIX
  block, 0 of 3 (−$2,137). The re-open cohort 04-14..04-23 went 2 of 5 (−$244), milder than W1–W6's re-open bursts.
- **Zombie slots**: DTE 154 sessions (+8.6% vs SPY +10.5%), XLP 119, **EA 96**, SPG 60. EA was bought on 02-09-2026 under the announced $210 cash
  take-private (public record). It traded 197.09–205.66 for 96 sessions, below its 218.01 target, and left on an RSI-70 exit at +2.1%. It is W6's K again.

## 6. Why it trailed SPY

Daily arithmetic attribution against SPY-TR (sum −27.30 pp; compounding adds −2.15, giving −29.45):

| Driver | pp | By phase (bull / correction / rebound / recent) and evidence |
|---|---|---|
| Cash and underexposure | −2.03 | −1.52 / +2.16 / −1.59 / −1.07. The book was 88% invested; the engine pays no interest on cash |
| **Defensive ETFs** | **−5.46** | 0 / −0.16 / **−6.26** / +0.96. GLD, XLV and XLP were bought at the low and never sold on re-open (§3) |
| Equal-weight vs cap-weight | −1.84 | −1.94 / +3.23 / −3.22 / +0.09. Structurally, RSP trailed SPY by 6.2 pp compounded |
| **Stock selection vs RSP, net** | **−17.97** | **−10.76** / −3.58 / −8.20 / **+4.57** |
| ↳ of which costs | ≈ −5.8 | $5,557 on 264 fills |
| ↳ of which dividends not credited | ≈ −1.7 | At least $1,648 across 24 ex-dates (DTE ×2, SPG, WMB, CVS, ADM, ED …). The engine credited $13.59, on QQQ only; BK, CTRA and EA lack yfinance data |
| ↳ selection gross | ≈ −10.4 | The whole gross gap is about the tie-order draw: the replica's alphabetical path ends **10.7 pp below the mean of 30 random orders** |
| Exits cutting winners | none within 20 sessions | The 49 target exits moved −0.47% over the next 20 sessions (26 higher, −1.4 pp vs SPY). The 70 stops moved −0.14% (31 higher), so the stop neither saved nor cost on average. Long run, the target sold DELL at +8.7% on 09-22-2025 (+335% by 09-17-2026) and MPC (+120%) |

**The endpoint is a bad draw from a losing distribution.** The same rules under 30 random tie orders returned **+4.79% ± 8.2** (median
+5.9%, range −13.9% to +20.6%, max DD −10.5% on average). **None beat SPY and 1 of 30 beat RSP.** The replica's alphabetical −5.94% sits
at the ~13th percentile. So the typical result in this regime is roughly +5% over 14 months: about 18 pp behind SPY and 12 behind RSP.

**Verdict.** This is the regime the live account will trade, and the gate hardly matters in it. The book is 88% invested, turns over 16×
and equal-weights 7–8 pullback names. Their selection is random against RSP, and it pays 5.6% in costs. The strategy is a leveraged bet on breadth
(a 0.70 loading on RSP−SPY): it made +14% in the summer 2026 rotation, when QQQ fell, and lost 20.7 pp to SPY in the mega-cap-led April–June V.
The two avoidable losses were the defensive ETFs held through the rebound (−5.5 pp) and costs (−5.8 pp). The rest is breadth and noise.

**Against the earlier research:** entry ≈ random **confirmed**. Trailing SPY after costs **confirmed** (−29.5 pp; about −22 pp gross of costs and
dividends). The regime filter's drawdown benefit is **not seen**: −12.3% vs SPY's −8.9%, and removing the gate improves return.

**Known issues, checked:**
- Rows stored twice and previous-close pv: both apply. Stop generates every loss: yes. Equal-weight lag: −6.2 pp. Dividends: the engine credited only QQQ's.
- Same-session clamps, 3 material: WFC filled 8 of 153 on 08-15-25 after T's close-target exit (the stub held financials for 26 sessions with $637);
  ABBV 5 of 46 on 09-11-26 after ESS; AMAT **dropped** on 09-09-26 after HLT (neither rejected nor unfilled). At full size WFC +$1,051, AMAT −$1,132, ABBV +$303: net ≈ −$220, a wash.
- "Unknown" sectors: BK and CTRA; CTRA sat beside EQT and MPC in energy (+$1,099). Capping "unknown" scores −2.2 ± 0.9 pp paired here.
- Gate: closed late and flickered (03-11). Alphabetical ties: a ~13th-percentile path. Dropping the +9% target: **−5.8 ± 1.7 pp paired** (as in W6; W5 +3.6).
  Defensive ETFs never sold: **confirmed, and larger than in W6**.

## 7. Change hypotheses (evidence only; nothing implemented)

1. **Sell bear-mode defensive ETFs at the next open when the regime re-opens, or park bear cash in T-bills instead of swing-trading ETFs.**
   *Observation:* −5.46 pp here (−6.26 in the rebound). GLD fell −11.9% after the re-open; XLP sat for 119 sessions. W6 had the same zombies (−0.6 pp),
   and W3 and W6 missed GLD rallies. *Replica:* +1.83 ± 1.16 pp paired (20 of 30), DD −10.2% vs −10.5%; W6 −0.18 ± 0.08. *Risk:* low, since this
   fixes a missing exit rule; a false re-open swaps defensives into stocks. W2's defensives lost regardless.
2. **Re-open faster: re-admit entries when SPY > SMA50 and VIX ≤ 25, and keep the 1.03 × SMA200 close rule.** *Observation:* the re-open
   came after 50% of the rebound here and 78% in W6. Signals from the blocked sessions replayed at +2.5% (about 60% won). *Replica:* **W7 +0.37 ±
   1.34 pp, max DD −9.35% vs −10.46%; W6 +2.38 ± 0.21 pp (30 of 30), DD −7.9% vs −10.0%.** Adding a close-below-SMA200 hysteresis cuts
   W6 to +0.68. *Risk:* it buys bear-market rallies. W2's six failed 2022 rallies are the untested case, so run W2 before adopting. After a
   fast re-open the 1.03 rule can shut again next session (04-09 → 04-10 in the replica).
3. **Size it as a satellite beside a cap-weighted core, or judge it against RSP.** *Observation:* 0 of 30 orders beat SPY and 1 of 30 beat RSP;
   0% of 6-month windows beat either; 70% of the strategy's daily excess loads on breadth; tech mega-caps are never held. *Expected:*
   a 50/50 SPY core would have made **+8.6%** here instead of −6.15% (a gap of −14.7 pp instead of −29.5). *Risk:* it is beta, not alpha, and it gives back part
   of the bear-market protection (W2, W6).
4. **Measure what live will trade: add the live 5-day earnings hard block (`EARNINGS_HARD_BLOCK`) to backtests with a point-in-time
   earnings calendar, and test an exit-before-earnings rule.** *Observation:* the 18 event-gap trips cost −$6,413 while the other 118 made +$249,
   and the backtest skips the block that live applies at entry. *Expected:* unknown sign. Gaps should be symmetric in principle, and W6 found them a wash.
   *Risk:* overfitting to 13 of 18 losers. First verify which gaps were earnings (DRI, GOOG and CDNS look like it but are unverified).
5. **Negative results: do not loosen the sector cap, add a blanket time stop, drop the target or sell the book on gate close on this evidence.**
   *Replica (paired, 30 orders):* per-sector 2 **−8.0 ± 1.4**; 40-session time stop **−6.6 ± 1.7** (it frees DTE/XLP/EA slots but churns); no
   target −5.8 ± 1.7; stop −10% without a target −12.6 ± 1.9; sell on gate close −6.3 ± 1.3 (W6 +6.0); max 2 new a session +0.8 ± 1.0 (W5 +2.3).
   *Implication:* single-path variants here span −16.9% to +7.5%, inside the ±8 pp tie-order noise. Only paired, multi-window tests count.
   Excluding EA scores −3.9 ± 1.9: excluding deal-pinned names is a data-hygiene fix, not a return lever.
6. **Engine and data hygiene before the live comparison.** Fix the same-session exit→entry credit (3 clamps and one silent drop, which
   `unfilled_order_count` misses). Credit stock dividends (≥ $1.6k, about 1.7 pp). Use a point-in-time sector map (BK, CTRA). *Expected:* +1–2 pp, mostly dividends. Low risk.

## Data caveats
- Replays use the engine's IEX bars and fill at the open ± 23 bps. Signal-vs-random means pool overlapping trades, so their standard errors are
  understated. Replays and 20-day aftermaths are truncated at 09-17 (6 exits have fewer than 20 sessions after them).
- Event gaps are a volume/gap proxy, not an earnings calendar. The EA takeover is public record, and its $197–206 pin is in-data. yfinance lacks BK, CTRA and EA
  (take-private closed; engine bars end 08-04-2026), so the dividend figure is a lower bound.
- yfinance returns holiday rows (05-25, 09-07-2026) with NaN SPY. They are dropped before alignment.
- `pull_backtest_logs.py` no longer reads the `DEFAULT_ADMIN_*` names `.env` still uses, so I passed them as `INTELLISTOCK_API_USERNAME/PASSWORD`
  in the process environment for this one call. No file was changed.
