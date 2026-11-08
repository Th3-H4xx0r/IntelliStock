# W6-tariff-shock-2025H1: backtest 578822 (swing-paper, doc 204, daily bars)

Window 2025-01-06 → 2025-06-30, $100k start. Read-only. Sources: the uncapped log (`logs/578822.log`, 2,364 lines), `BacktestSteps`
(137 trade rows → 69 final, one null, so **68 fills**), `BacktestResults`, the lane's own `SwingDailyBars` (IEX, split-adjusted),
membership, sector and VIX rows, and yfinance (auto-adjusted). Scripts and intermediate data are in `swing_bt/w6/`. The wheel lane
logged itself inert in backtests.

**Alignment.** A pv step dated D carries the close of D−1, so the engine's −0.64% runs **01-03c → 06-27c**; SPY-TR +4.50%, RSP-TR
+3.51% on that basis. Six holiday pv steps (01-09, 01-20, 02-17, 04-18, 05-26, 06-19) repeat the prior close and are dropped: 120 closes.

**Replica.** `w6/psim.py` (the W4/W5 simulator re-pointed) matches the logged PASSED count on 56/56 ACTIVE sessions, the phase on
120/120 and **all 38 entries**. It ends −0.94% (engine −0.64%), max DD −8.67% (−8.16%), charging a flat ~23 bps a side. "Replica"
numbers are deltas vs −0.94% on one 8-name path: direction, not size.

## 1. Headline

| Metric | Strategy | SPY-TR | RSP-TR (equal-weight) |
|---|---|---|---|
| Return, 01-03c→06-27c | **−0.64%** | +4.50% | +3.51% |
| Excess | **−5.14 pp** vs SPY | — | −4.15 pp vs RSP |
| Max drawdown | **−8.16%**: peak 02-19, trough **06-18**, not regained by the end | **−18.76%** (02-19 → 04-08; regained 06-26) | −15.96% |

| Book and trades | Value |
|---|---|
| Average invested / positions | 54.7% / 4.43 over 120 closes (`metrics.json` gives 55.7% / 4.52 because it counts the holiday repeats). 8 names at 31 closes; **0 names at 31 closes (04-03 → 05-15)** |
| Fills / round trips | 68 fills (38 buys, 30 sells). 30 closed trips; 8 open at the end (GLD, SHY, AWK, K, GDDY, YUM, CME, PODD: −$8) |
| Win rate; average win / loss | 40.0% (12 of 30); +8.54% / −5.91%. The payoff is 1.45, so the breakeven win rate is 40.9% |
| Expectancy | **−0.13% a trade (−$21)** |
| Holding period | 12.2 trading days on average (17.9 calendar), median 9.5 |
| Turnover | $469.7k of buys: 4.7× equity in 5.8 months |

| Exit type | n | Average net | Hold (trading days) | P&L |
|---|---|---|---|---|
| Bracket target (+9% on the prior close) | 10 (3 gap fills: AAPL, GRMN, BKNG) | +8.64% | 13.4 | +$10,901 |
| Bracket stop (−6% on the prior close) | 16 (2 gap fills: LULU, DAY) | −5.80% | 11.5 | −$11,602 |
| RSI crosses 70 (COST) | 1 | +7.28% | 15 | +$748 |
| Close-based target, sold at the next open (AVGO) | 1 | +8.83% | 8 | +$1,105 |
| Close-based stop, sold at the next open (IT, SHW) | 2 | −6.77% | 12.5 | −$1,779 |
| Open at the end | 8 | +0.01% | 20.8 | −$8 |

**Costs.** The engine charged **$1,632.58**: $1,600.37 spread, $25.19 fees and $7.02 slippage. That is 1.63% of equity, or 19.4 bps a
fill on $839.7k of fills. A flat 23 bps on the same notional is $1,931 (1.93%), and 68 × 23 bps × $12.5k is $1,955. Annualised, costs
run about 3.4% a year. Fills averaged −0.07% against the signal close. The prior-close stop sat −5.92% from the fill on average,
ranging from **−0.12% (DAY, which opened −5.9% below its signal close)** to −7.50% (APH).

## 2. Path

| Month (close to close) | Strategy | SPY-TR | RSP-TR | vs SPY | Average invested | Average positions |
|---|---|---|---|---|---|---|
| Jan (from 01-03c) | +3.58% | +1.67% | +2.79% | +1.91 | 93% | 7.6 |
| Feb | +3.00% | −1.27% | −0.58% | **+4.27** | 87% | 7.1 |
| Mar | −4.54% | −5.57% | −3.40% | +1.04 | 39% | 3.1 |
| Apr | −1.00% | −0.87% | −2.36% | −0.13 | 1% | 0.1 |
| **May** | +0.39% | **+6.28%** | +4.31% | **−5.89** | 31% | 2.5 |
| **Jun (to 06-27c)** | **−1.84%** | +4.64% | +2.95% | **−6.48** | 91% | 7.3 |

| Phase | Strategy | SPY-TR | RSP-TR | vs SPY | Average invested |
|---|---|---|---|---|---|
| Pre-crash, 01-03c → 02-19c (SPY's peak) | +6.99% | +3.54% | +3.70% | +3.45 | 91% |
| Crash, 02-19c → 04-08c (SPY's low) | **−5.75%** | **−18.76%** | −15.96% | **+13.01** | 43% |
| Rebound, 04-08c → 05-16c | **−0.01%** | **+19.68%** | +17.69% | **−19.69** | 1% |
| Re-entry, 05-16c → 06-27c | −1.45% | +3.79% | +0.91% | −5.24 | 84% |

The cumulative excess peaked at **+16.7 pp on 04-08** and ended at −5.1 pp. The rebound alone decided the result.

**Worst drawdown windows** (non-overlapping, NAV at the close, P&L by symbol including trades):
1. **02-19 → 06-18, −8.16%** (SPY −2.23%, RSP −2.53%), in three legs. (a) 02-19 → 04-03, −5.75% (SPY −12.17%, 47% invested):
   the February cohort stopped out one by one (AMP −1,075, LULU −951, IT −915, CHRW −903, CLX −894, SHW −864, DAL −859, VMC −582,
   KMI −455; only BMY +1,108 won). Bad picks, but half SPY's fall. (b) 04-03 → 05-16: flat at $100,833, 0 positions. (c) 05-19 →
   06-18, −2.75% (SPY +0.44%, 82% invested): BRO −780, KR −681, K −528, YUM −486, GDDY −373, AWK −231, PODD −228 vs GLD +512. Bad picks.
2. **01-08 → 01-10, −2.13% in one session** (SPY −1.53%): INCY −477, LVS −471, CHTR −447, AMP −415; LVS and CHTR stopped. Market.
3. **01-28 → 02-03, −1.49%** (SPY −1.12%): CHTR −1,404 (second stop, 01-30), AMP −787, KIM −398 vs COST +522, AAPL +507. Picks.

**Where it lagged most** (rolling 20 sessions): **04-21 → 05-19, −15.6 pp** (NAV +0.2% vs SPY +15.8%, 4% invested): cash in bear
mode (§3). **05-23 → 06-24, −7.3 pp** (NAV −2.2% vs +5.1%, 87% invested): picks and the two defensive slots (re-entry leg:
selection −3.2 pp, equal-weight −1.6, GLD/SHY −0.6; §6). No other window lagged by more than 0.9 pp; it led through Jan–Feb.

## 3. Regime: April protection, bear mode, and the re-entry lag

- **120 sessions: 56 ACTIVE, 20 BLOCKED, 44 bear mode** (03-17 → 05-16). Of 64 blocked, 42 were below the plain SMA200 and 22
  buffer-only. VIX > 25 on 18, **all also SPY-blocked**: the VIX leg never bound alone (no-VIX and VIX-30 replicas are identical).
- Timeline (10 flips): ACTIVE 01-06..02-27 → blocked 02-28 → **one-session re-open 03-03** → blocked 03-04..03-14 → bear
  03-17..05-16 → ACTIVE 05-19..05-21 → blocked 05-22..06-03 → ACTIVE 06-04/05 → blocked 06-06 → ACTIVE → blocked 06-23 → ACTIVE.

**Did the gate protect the portfolio in April? Partly. The stops did most of the work.**
- **It closed early this time**: first block 02-28 (02-27 close 585.03 vs 587.23), SPY-TR −4.55% off its peak, 24% of the fall (W4: 57%).
- **Closing sells nothing; the bracket emptied the book**: 8 names on 03-03 → 6 on 03-04 (LULU gap-stop, AMP) → 5 on 03-07 (BMY
  target) → 3 on 03-11 (IT close-stop, KIM) → 2 on 03-19 (SHW) → 1 on 03-24 (CLX) → **0 on 04-03** (CHRW, stopped on the tariff gap
  day). So the 04-03 → 04-08 collapse (SPY-TR −12.2%) hit a 13% book for one day and an empty one after.
- **The 03-03 re-open was luck**: 30 passers, but a full book. They replay at **−7.01% a trade, 0 of 29 won**; the gate-exit
  replica shows the whipsaw avoided (8 names bought 03-03, all out by 03-04 at −0.7% to −6.6%).
- **Replica:** no gate gives a −10.56% crash leg vs −6.28%, so **the gate was worth +4.3 pp in the crash**; the other ~8 pp of the
  lead came from the bracket. The buffer blocked the worst signals, as in W2 and unlike W4: buffer-only sessions replay at −3.16% a
  trade (19% won, n=97), below-SMA200 sessions at −0.96% (36%, n=269).

**Did bear mode's defensive ETFs help? No. The sleeve bought nothing for 43 of its 44 sessions.**
- The defensive funnel passed 0.05 names a session. Failures over the 44 sessions: GLD had RSI ≥ 50 on 38 (above 70 on 13); XLV
  was below its SMA200 on all 44; XLU and SHY had RSI ≥ 50 on 31 each; XLP's volume was below average on 34.
- **GLD rose +8.1% from bear start to re-open** (03-17o → 05-19o), +13.4% from the gate close, +23.7% over the window. It qualified
  once, **on 05-16, the last bear session**, after its 04-21 peak, together with SHY (12.5% each).
- **Both then became zombies**: the regime re-opened next session and no rule sells a defensive ETF on re-open. SHY's bracket
  (77.47 / 89.84) is unreachable (largest 60-day move in the prior year 2.35%; June range 82.21–82.84); GLD's (279.98 / 324.66) was
  not hit (291.9–317.6). They held 25% of equity and 2 of 8 slots through June: GLD +2.7%, SHY +0.2% vs SPY +3.8%. The 10 June
  no-slot skips replay at +1.55%; the no-bear-mode replica is +0.27 pp.
- **Overlay on the bear-mode cash** (average $94k): GLD +$6.5k, equal-weight 5-ETF basket +$1.6k, SHY +$0.5k (the engine pays no
  interest on cash), SPY +$7.2k. Bear mode began after SPY had fallen 8.2% and ended after the rebound: SPY +5.4% across it.

**SPY during the blocked stretches** (open to open; signals the gate blocked, replayed with the real bracket):

| Blocked stretch | SPY | GLD | Blocked signals |
|---|---|---|---|
| 02-28 | +1.81% | +0.96% | 6 passers, −7.02% a trade, 0 won |
| 03-04..03-14 (blocked) | −2.92% | +2.43% | 88, −2.20%, 26% won |
| 03-17..04-08 (bear, before the low) | **−12.06%** | +2.42% | 90, **−5.27%**, 9% won |
| 04-09..05-16 (bear, after the low) | **+19.18%** | +5.54% | 168, +0.83%, 47% won (random names above their SMA200: +1.35%, 59%) |
| 05-22..06-03 | +2.45% | +1.42% | 10, +0.65%, 40% won |
| 06-06 and 06-23 | +0.18%, +1.56% | −1.2%, −1.7% | 4 passers |

**How late was the re-entry? 28 sessions, after 78% of the rebound.**
- Re-opened **05-19** (05-16 close 594.28 vs 591.58), with SPY-TR already **+18.5% off its 04-08 low**; SPY-TR regained its
  02-19 peak only on 06-26. It flickered shut 05-22..06-03, 06-06 and 06-23; the book reached 8/8 only on 06-05 (2 of them GLD/SHY).

  | Re-open trigger (first session after the 04-08 low) | Session | Sessions after the low | SPY-TR, low → open | Open → 06-27c | Share of the rebound missed |
  |---|---|---|---|---|---|
  | VIX close ≤ 30 | 04-21 | 8 | +5.0% | +18.3% | 22% |
  | SPY > SMA20 | 04-25 | 12 | +10.1% | +12.8% | 44% |
  | VIX close ≤ 25 | 04-28 | 13 | +11.1% | +11.8% | 48% |
  | SPY > SMA50 | 05-02 | 17 | +13.7% | +9.2% | 59% |
  | SPY > SMA200 | 05-13 | 24 | +17.5% | +5.7% | 74% |
  | **SPY > 1.03 × SMA200 (actual)** | **05-19** | **28** | **+18.5%** | **+4.9%** | **78%** |

- **A faster gate alone would not have caught the V.** The no-gate replica held ~7 names over 04-09..05-16 and made +5.4% vs SPY
  +19.7% (27% of the move); post-low signals replay below random names (+0.83% vs +1.35%). The +9% target caps each winner and "RSI
  < 50 and rising" picks laggards: the no-gate book bought AFL, MO, ABT, ADP, AEP, AEE and AMT.
- **Did the gate help or hurt? Slightly hurt return (−1.0 pp), helped drawdown**: no gate +0.06% vs −0.94%, max DD −11.0% vs −8.7%
  (+4.3 pp in the crash, −5.4 pp in the rebound). The drawdown benefit is clear here (−8.2% vs −18.8%), but the stops earned most of it.

## 4. Funnel

Typical ACTIVE session, mean (median): universe 505 → not held 498.7 → has indicators 497.4 → close > SMA200 **271.9 (280.5)** →
RSI < 50 and rising **33.0 (23.5)** → MACD up 25.3 (16) → volume > 20-day avg **11.8 (6.5)** → ADX > 15 10.6 (4) → **PASSED 10.6 (4)**.

- **The RSI filter kills the most**: it removed 13,379 and kept 12.1%; SMA200 removed 12,629 (kept 55%); volume kept 46%, MACD 77%,
  ADX 90%; rr_ok never binds. Passers by month: **Jan 22.1** (median 14), Feb 5.1, 03-03 30, **May 2.0**, Jun 4.2 (2 zero-pass sessions).
- **Full book**: 26 of 56 ACTIVE sessions (46%) began at 8/8. Only 10 slot-sessions sat idle for lack of passers, 7 in May.
- **Sector cap.** 356 skips (242 itemised + 114 in "+N more" summaries) vs 72 no-slot skips, mostly via AMP (46), CMI (38) and
  APH (22). It left slots empty on 9 sessions (02-10, 02-13, 02-14, 02-20, 05-19, 06-04, 06-10, 06-11, 06-13).
- **Did capped names later outperform? Not beyond noise.**

  | Group | Unique names | Bracket replay | 20-day excess vs RSP |
  |---|---|---|---|
  | Entered | 36 | +0.19% (44% won) | −1.43% (s.e. 1.31) |
  | Sector-capped | 164 | +0.43% (44% won) | −0.35% (s.e. 0.62) |
  | No-slot | 43 | +1.22% (49% won) | +1.45% (s.e. 1.08) |

  Allowing 2 names per sector scores **+7.8 pp** in the replica here, but −6.0 pp in W4 and worse in W3. Capping new entries at
  2 a session scores −9.0 pp. The spread between those two is path noise from an 8-name book, not a signal.
- **The entry is about random, and anti-momentum in rising tapes.** ACTIVE passers replay at +0.64% a trade (s.e. 0.30, n=602,
  overlapping) vs −0.69% for random above-SMA200 names and −0.55% for all members, but the edge is January only: Jan +2.71% vs
  +0.65%, Feb −4.88% vs −2.75%, **Jun −0.42% vs +0.74%**. 20-day excess vs RSP: −0.09% vs −0.35%; per closed trade −1.43% ± 1.31.
  **"Entry ≈ random" is confirmed.**

## 5. Decisions

**Top 5 winners.** All ended at the +9% cap. The next five (BKNG, APD, GRMN, INCY, CMI) made $1,007–1,075 each, and the best trip made +10.8%.

| Trip | Entry (RSI prev→now, MACD hist, ADX, volume vs 20-day average) | Exit | Result |
|---|---|---|---|
| CCL 01-13→01-21 | RSI 42.4→43.3, MACD −0.275→−0.242, ADX 27.9, vol 1.62×. It opened −1.7% below the signal close, so the target sat +10.8% above the fill | Target in 5 sessions | **+10.8%, +$1,300** |
| AAPL 01-23→01-31 | RSI 29.7→31.6, ADX 33.1, vol 1.22× | Target gap at 247.12 against a 243.95 target, the morning after Q1 earnings (public record). By 06-27 it was −18.6% below the exit | +9.7%, +$1,246 |
| ATO 01-06→02-24 | Day 1; RSI 39.6→41.5, ADX 32.4, vol 1.01× | Target after 32 sessions | +9.0%, +$1,108 |
| BMY 02-24→03-07 | RSI 45.0→45.2, ADX 19.6, vol 1.04× | Target on 03-07 while the market fell; it then fell −9.1% in 20 sessions. The only February-cohort winner | +8.4%, +$1,108 |
| AVGO 02-03→02-13 | RSI 46.6→50.0, MACD −4.51→−3.72, ADX 28.0 | Close-based target (236.34), sold at the 02-14 open for 234.97. It then fell −16.8% in 20 sessions | +8.8%, +$1,105 |

**Top 5 losers.** All were capped near the stop.

| Trip | Entry | Exit | Result |
|---|---|---|---|
| LULU 02-25→03-04 | RSI 32.6→39.5, MACD −6.16→−5.39, ADX 26.8, vol 1.08× | The stop gapped through: filled 340.86 against a 345.85 stop at the 03-04 open. By 06-27 it was −31% below the exit | **−7.4%, −$951** |
| LVS 01-06→01-10 | Day 1; RSI 39.8→42.7, ADX 17.9. It opened +1.3% above the signal close, so the stop sat −7.2% from the fill | Stopped in 3 sessions; −11.8% more in 20 | −7.4%, −$932 |
| IT 02-25→03-11 | RSI 30.0→38.3, ADX 22.9, vol 1.36× | Close-based stop (471.78), sold at the next open for 467.18; −19.2% more in 20 sessions | −7.0%, −$915 |
| CHRW 02-24→04-03 | RSI 47.6→49.1, MACD +0.20→+0.32, ADX 26.7 | Rode the whole slide and was stopped on the 04-03 gap day | −6.8%, −$903 |
| VMC 02-14→02-21 | RSI 41.2→49.1, ADX 19.8, vol 1.42× | Bracket stop in 4 sessions | −6.9%, −$899 |

CHTR was bought twice and stopped twice (01-10 −5.5%, then re-bought 01-16 and stopped 01-30 −6.3%; −$1,462 together). It then
rose +11.3% in 20 sessions and +22.8% by 06-27, **the one stop that sold a future leader**.

**Patterns in the 18 losers.**
- **Timing cohorts.** The February cohort (02-14..02-26, around SPY's 02-19 peak, before the 02-28 flip) lost 7 of 9 (VMC, DAL,
  CLX, CHRW, IT, LULU, SHW: −$6,285; GRMN and BMY +$2,152). **The re-open cohort (05-19..05-21) lost 4 of 4** (AWK, K, BRO, KR:
  −$2,439 incl. open marks), the fourth window with this pattern.
- **Low-beta picks after the re-open**: average 120-day beta **0.28** vs 0.85 in January (KR, AWK, K, CME ≤ 0.04). Consumer
  defensive went 1 for 5 (−$2,261), with "unknown" K beside KR. K was a pinned takeover target (Mars's $83.50 cash bid, public
  record): bought at 82.74, its target was unreachable; it drifted to 79.03.
- **Gaps were a wash**: 2 gap-downs through the stop (LULU −$175 beyond it, DAY), 3 gap-ups over the target (AAPL, GRMN, BKNG).
  CLX (02-04) and DAY (02-05) opened below the signal close after earnings, so the prior-close stop sat 3.0% and 0.1% from the fill
  (−$177 together; DAY's entry and stop filled at the same open).

## 6. Why it trailed SPY

Daily arithmetic attribution against SPY-TR (the sum is −6.40 pp; compounded, −5.13 pp):

| Driver | pp | By phase (pre-crash / crash / rebound / re-entry) and evidence |
|---|---|---|
| **Cash and underexposure** | **−6.66** | −1.01 / **+12.98** / **−18.81** / +0.18. By regime: bear mode −7.14, ACTIVE +0.50 |
| Defensive ETFs | −0.64 | All in the re-entry leg: the GLD/SHY slots against SPY (§3) |
| Equal-weight vs cap-weight | +1.69 | +0.73 / +2.54 / 0 / −1.58. RSP fell less in the crash and lagged in June |
| Stock selection vs RSP, net | −0.79 | **+3.56** / −1.14 / 0 / **−3.21** (BRO, KR, K, YUM, GDDY) |
| ↳ of which costs | −1.63 | $1,632.58 on 68 fills |
| ↳ of which dividends not credited | ≈ −0.35 | At least $328 (AMP, CHRW, KIM, SHW, SHY), plus K's June dividend, which yfinance lacks (≈ $87, public record) |
| ↳ selection gross | ≈ +1.2 | Noise-level: −1.43% ± 1.31 per closed trade vs RSP |
| Exits cutting winners | none | The 10 target exits fell −6.6% over the next 20 sessions (1 of 10 higher, −2.4 pp vs SPY). The 16 stops fell −4.1% more (5 of 16 higher) |

**Verdict.** The regime machinery worked on the way down and failed on the way up. The bracket and an early gate close emptied the
book before the 04-03 → 04-08 collapse (+16.7 pp ahead at the low). The 1.03× gate then held it in cash for the first +18.5% of the
rebound, and bear mode could not buy the one asset that was rising (GLD). Once re-opened, the pullback signal filled the book with
low-beta laggards beside two zombie ETFs (−1.45% vs SPY +3.79%). Picks net of costs were about zero; the whole −5.1 pp is the
rebound's cash (−18.8) minus the crash's cash (+13.0).

**Against the earlier research:** entry ≈ random **confirmed** (January-only edge); trailing SPY after costs **confirmed** (−5.1 pp,
≈ −3.1 pp gross of costs and dividends); the regime filter's drawdown benefit **seen for the first time** (−8.2% vs −18.8%), but
the replica credits the gate with only ~4 pp of it; the stops did the rest.

**Known issues, checked:** rows stored twice (137 → 68 fills) and previous-close pv: apply. Same-session clamp: applies once, CLX
sized 80 / filled 24 on 02-04 (COST's RSI-70 exit session); CLX was stopped that day, so it saved ~$280; no entry dropped.
"Unknown" sectors: K beside KR, DAY beside AVGO (SHY, XLP, XLU, XLV are also "unknown"). Gate: flickers (10 flips) but did **not
close late** this time. Stops near lows / targets selling leaders: **mostly not here** (only CHTR). Alphabetical ties: day 1 had
36 passers and bought APD, AMP, APH, ATO, CHTR, CMI, INCY, LVS (+$2,759, lucky). Equal-weight lag: **reversed** (+1.7 pp, from
the crash). No dividends: applies.

## 7. Change hypotheses (evidence only; nothing implemented)

1. **Bear mode: stop swing-trading the defensive ETFs; park idle cash in a static sleeve and sell it on re-open.** *Observation:*
   1 buy in 44 bear sessions; GLD (+8.1% across bear mode, +13.4% from the gate close) qualified only on the last one, and both buys
   became zombies (SHY can never hit its bracket; 25% of equity, 2 slots, −0.6 pp in June). W3 the same (GLD +15.9%, no entry); W2's
   7 buys lost −$2,994. *Expected:* on this bear cash, T-bills +0.5 pp, equal-weight basket +1.6 pp, GLD +6.5 pp; the zombie fix
   alone +0.3 pp. *Risk:* W2's defensives all fell 4.8–10.5%, so a basket loses to cash there, and GLD alone is a bet on 2025's
   gold rally. T-bills are the regime-robust floor (crediting cash interest would also make the engine comparison fair).
2. **Gate hysteresis: open above 1.03 × SMA200, close only below the plain SMA200.** *Observation:* 10 flips; the 05-19..05-21
   re-open cohort lost 4 of 4 and the 03-03 re-open's passers replay at −7.0%; flicker cohorts also lost in W2 (10/11), W3 (14/16),
   W4 (5/5). *Replica:* +1.35 pp, max DD −8.05% vs −8.67%, 2 flips (close 03-11, re-open 05-19). *Risk:* it closes later in a crash
   (crash leg −6.86% vs −6.28%) and re-admits buffer-only sessions, whose signals were the worst here (−3.16% a trade).
3. **Sell the stock book at the next open when the gate closes; re-open only after 3 consecutive ACTIVE sessions.** *Replica:*
   **+6.0 pp (+5.08%), max DD −1.93%** vs −8.67% (gate-exit alone +2.3 pp, but it whipsaws on the 03-03 flicker; the 3-session rule
   also blocks the window's first two sessions, a start artifact costing ~1.7 pp, so this is if anything understated).
   *Cross-window:* W4 +4.8 pp, W2 +$5.8k, but **W3 −5.4 pp**. *Risk:* variants here span −9.9% to +6.8% (8-name path noise).
4. **A faster re-entry for an index sleeve, not the swing book: hold SPY in empty slots while the 1.03 gate is shut but VIX ≤ 25
   and SPY > SMA50.** *Observation:* 78% of the rebound came before the re-open, and the swing book can't catch a V even ungated
   (+5.4% vs +19.7%; post-re-open beta 0.28). An idle-cash sleeve after 05-16 would have *lost* $183: the lever is the bear stretch.
   *Cross-window (SPY, rough):* **+8.1% here**, **−1.8% in W2's 2022 bear** (6 failed rallies), +0.3% in W3. *Risk:* it buys
   bear-market rallies and adds beta when the gate says not to; judge only on a whole-cycle scorecard.
5. **Keep the −6%/+9% bracket; don't widen or drop it on W4's evidence alone.** *Observation:* here targeted names fell −6.6% in
   the next 20 sessions, and the no-gate crash leg (−10.6% vs −18.8%) shows the bracket did most of the protecting. *Replica:* no
   target **−5.8 pp**; stop −10% +2.2 pp; stop −10% no target −4.2 pp; RSI-70 only +5.1 pp but a −14.2% max DD. *Implication:* the
   bracket saved W6 and cost W4; test any volatility-scaled version (W4 H2) on all windows at once.
6. **Data hygiene: point-in-time sector map; exclude announced cash-takeover targets.** *Observation:* K and DAY read "unknown"
   (dropped by yfinance) and bypassed the cap; K was pinned under its $83.50 deal price. *Replica:* capping "unknown" +1.6 pp (W4
   +0.45). *Expected:* small (+0.5–1.5 pp), low risk: a correctness fix, not a parameter.

## Data caveats
- Replays use the engine's IEX bars and fill at the open ± 23 bps (a few bps off engine fills); signal-vs-random means pool
  overlapping trades, so their standard errors are understated. Earnings/takeover context (AAPL, CLX, DAY, Mars/K) is public record,
  not verified in-data; yfinance lacks K and DAY, so the dividend figure is a lower bound.
- `pull_backtest_logs.py` no longer reads the `DEFAULT_ADMIN_*` names `.env` still uses; I passed them as
  `INTELLISTOCK_API_USERNAME/PASSWORD` in the process environment for this one call. No file was changed.
