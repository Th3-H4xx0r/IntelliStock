# W3-recovery-22-23: backtest 647829 (swing-paper, doc 204), 2022-10-17 to 2023-07-31

**Sources.** The uncapped log (`logs/647829.log`, 3,196 lines), every `BacktestSteps` row, `BacktestResults`, and the lane's own `SwingDailyBars`, `SwingIndexMembership`, `SwingSectorMap` and VIX rows (all read-only). SPY, RSP, QQQ and the defensive ETFs come from yfinance with auto_adjust. Scratch work is in `swing_bt/w3/`.

**Replay fidelity.**
- **Exits.** Replaying the exit rules on the lane's IEX bars reproduces all 33 closed round trips: the same exit day, the same reason, and the fill price to within 0.01%. The one exception is BDX's gapped target, where the engine filled at the open with no half-spread (+11.95% against +11.69% in the replay).
- **Funnel.** The rebuilt funnel matches the logged count at every stage on all 93 ACTIVE sessions.
- **Whole book.** A portfolio replay (`w3/psim.py`) reproduces all 38 entries, with the same names on the same sessions, and ends at −9.02% against the engine's −9.09%. Every counterfactual below runs through that replay.
- **Noise.** A single trade is worth ±$0.6–1.3k, and a small rule change reshuffles which names fill the slots. **Treat any difference between variants smaller than about 5 pp as noise.**

## Bottom line: why it lost while SPY rallied

SPY-TR returned +29.32% (10-14 close to 07-28 close). The book returned −9.09%, which is −38.4 pp. The rally came in two halves, and the book was on the wrong side of both:

| Stretch | SPY-TR | RSP (equal weight) | QQQ | Book | Invested |
|---|---|---|---|---|---|
| 10-14 → 11-30 (SPY still *below* its plain SMA200) | **+13.99%** | +16.08% | +12.51% | 0.00% | 0% |
| 11-30 → 01-31 (above SMA200, below 1.03×) | +0.16% | +2.37% | +0.67% | −0.15% | 3% (XLP) |
| 01-31 → 05-31 (gate open, then flickering) | +3.19% | **−7.52%** | +18.30% | **−15.26%** | 73% |
| 05-31 → 07-28 (open for good) | +9.76% | +11.07% | +10.35% | **+7.44%** | 88% |

The book sat in cash while breadth rallied, and was fully invested in equal-weight-like names while the market narrowed to mega-cap tech. Here is how the gap splits across the four suspects:

1. **The gate kept it out. This cost −13.4 pp against SPY, but about 0 in absolute terms.**
   - All of SPY's +14% came while SPY was below even its *plain* SMA200, so no 200-day filter could have caught it.
   - With no gate at all, the replay stood at +4.5% on 11-30 but was back to −0.3% by 01-31, and ended the window at −2.4%.
   - On blocked sessions, the signal averaged +0.78% per trade (n=514), against +0.57% on ACTIVE sessions. The gate did not pick worse days for this signal; it only cut exposure.
2. **The late re-entry cost about 0.**
   - The 3% buffer held the book out from 12-01 to 01-31, but SPY went from 407.45 to 406.50 over that span (+0.16% total return).
   - A plain-SMA200 gate replay re-opens on 12-01 and ends at −8.95%. Its 12 Dec–Jan entries lost $3.6k.
3. **The stop whipsaw at the gate's openings was the absolute loss.**
   - After 02-01, the gate closed 3 times, each within 1.5% of the line, and re-opened 4 times.
   - The **16 entries made on the first 3 sessions after an opening lost −$8,423, with 14 of 16 losing.** That is 93% of the run's −$9,091.
   - Counting all 24 losing exits: −$17.6k.
   - A −10% stop instead of −6% ends the portfolio replay at −2.6%.
4. **Stock picking cost −14.1 pp against SPY over the same holding windows.**
   - −5.1 pp of that is breadth: RSP trailed SPY over those windows.
   - −8.9 pp is picks against RSP, including 1.6 pp of costs.
   - On the sessions where the book actually bought, the signal's passes averaged **−1.62% per trade, against −0.30% for a random S&P member** with the same exits.

## 0. Accounting and fidelity checks: the books are clean

- **Mark-to-market.** `value = cash + Σ qty × price` holds to $0.00 on all 206 pv days.
- **Cash ledger.** Replaying the 71 fills reproduces every `cash_after` exactly. Σ round-trip P&L equals the NAV change of −$9,091.06.
- **Duplicate rows.** Every step row is stored twice, plus one null seq-0 row: pv 412+1, trade 142+1, decision 82+1. The run made **71 fills: 38 buys and 33 sells**, which is 33 closed round trips plus 5 still open at the end (PEG, CLX, GRMN, HOLX, CSX: +$396 marked).
- **pv alignment.** The last pv row (07-31) carries the 07-28 close, and each regime line uses the prior session's close. SPY on 10-17 reads 357.68, which is the 10-14 close.
- **The same-session credit bug did not bite here.**
  - Every one of the 38 logged ENTERs filled, and the fill never differed from the log by more than 2 shares of rounding.
  - The only session with both a signal exit and entries was 05-18 (HES sold; CAG and MGM bought), and both entries filled at full size.
  - Some positions were undersized, but by the strategy's own buying power, not by the engine:
    - XOM: 9.2% of equity, with $7,959 of cash.
    - PKG: 11.1%. HES: 11.2%.
    - NVR: 6.6%, because one share cost $5,627.
- **Non-point-in-time sectors let two names past the cap.**
  - **HES** (acquired in 2025, so it maps to `unknown`) entered on 05-08 beside OKE, both energy names, and lost −$627.
  - **HOLX** entered on 07-12 beside BDX (healthcare) and made +$143.
  - ATVI is also `unknown`, but it had no conflict when it entered.
  - Capping `unknown` in the replay gives −11.1%, which is inside the noise.
- **Dividends.** The engine credits no dividends on stock holdings (`return_basis: price_return`), while the benchmark is total return. That is about −0.6 pp of bias against the book.

## 1. Headline

| Metric | Value |
|---|---|
| Return / SPY-TR | **−9.09% / +29.32%**, excess **−38.41 pp** (RSP +22.07%, QQQ +47.86%) |
| Max drawdown / SPY max drawdown | **−15.58%** (02-13 → 05-31, never recovered) / −7.54% (02-02 → 03-13) |
| Average invested / average positions | 46.6% (stocks 41.9%, defensive 4.7%) / 3.80. That is 3% before 02-01 and 73% after. 0 positions on 59 of 206 pv days (10-17..01-05); 8 on 37. |
| Round trips / win rate | 33 closed + 5 open / **27.3% (9 of 33)**; stocks alone 8 of 32 |
| Average win / average loss / expectancy | +8.41% / −6.38% / **−2.35% per trade (−$287)**. Wins +$8,161, losses −$17,648. |
| Average hold | 19.2 trading days (median 11); 28.2 calendar days |
| Exit mix | bracket stop 20, stop gapped at the open 2 (OKE, CAG), bracket target 7, target gapped at the open 1 (BDX), close-based stop 2 (HES, XOM), close-based target 0, RSI-70 1 (XLP), open at the end 5 |
| Cost drag | spread $1,601 + fees $24 + slippage $7 = **$1,632 (1.63 pp)**. The estimate of 71 × 23 bps × $11.2k gives $1,832. Bought notional was 4.3× NAV. |

## 2. Path

Each row runs from one month-end close to the next.

| Month | Strategy | SPY-TR | Excess | Average invested | Average positions | NAV at end |
|---|---|---|---|---|---|---|
| Oct (from 10-14) | 0.00% | +7.99% | −7.99 | 0% | 0.0 | 100,000 |
| Nov | 0.00% | +5.56% | −5.56 | 0% | 0.0 | 100,000 |
| Dec | 0.00% | −5.76% | +5.76 | 0% | 0.0 | 100,000 |
| Jan | −0.15% | +6.29% | −6.43 | 11% | 0.9 | 99,854 |
| Feb | −2.00% | −2.51% | +0.52 | 86% | 7.0 | 97,858 |
| Mar | −1.17% | +3.71% | −4.88 | 40% | 3.2 | 96,711 |
| Apr | −1.75% | +1.60% | −3.35 | 58% | 4.6 | 95,020 |
| **May** | **−10.95%** | +0.46% | **−11.41** | 83% | 6.6 | 84,616 |
| Jun | +6.65% | +6.48% | +0.17 | 90% | 7.6 | 90,246 |
| Jul (to 07-28) | +0.73% | +3.08% | −2.34 | 83% | 6.8 | 90,909 |

**The three worst drawdown legs.** All three sit inside one peak-to-trough episode, 02-13 → 05-31. The attribution is daily, per symbol, including marks.

1. **02-13 → 03-23: −4.42%** (SPY −4.40%, RSP −9.57%, QQQ +1.88%). This is the 02-01 opening cohort stopping out through the February pullback and the SVB week, mostly while the gate was shut.
   - GILD −1,293 (mark and stop), CAG −645, APD −636, NDAQ −576, FE −534, HON −513, XLP −344.
2. **04-11 → 04-26: −3.18%** (SPY −1.31%). The April re-opening names.
   - WBD −823, CINF −709, DAL −635, STLD −527, APTV −421.
3. **05-01 → 05-31: −11.37%** (SPY +0.56%, RSP −3.72%, QQQ +8.01%). The 04-28, 05-01 and 05-08 opening bursts in energy, materials and cyclicals, during the narrowest month of 2023.
   - OKE −1,081, REGN −892, PKG −889, FOXA −887, DOW −805, MPC −781, AMP −747, MGM −693, CAG −641, HES −627, STLD −552.
   - GWW −848 was only a mark: it hit its target in June.

**Where it lagged most, and why:**
- **Oct, Nov and Jan (−8.0, −5.6 and −6.4 pp): cash.** The gate was closed and bear mode found nothing to buy. The worst 20-session excess of this stretch was −11.4 pp, on 11-11.
- **Mar (−4.9 pp): mostly cash.** The book was 40% invested, the gate was shut, and the February cohort was still riding its stops.
- **Apr (−3.4 pp) and May (−11.4 pp): bad picks.** The book was 58–83% invested, yet lost 11% in a month when SPY was flat. The worst 20-session excess, −12.2 pp, came on 05-30.
- **Jun and Jul: the book worked.** From 05-31 to 07-28, at 88% invested, it made +7.44% against SPY's +9.76% and RSP's +11.07%.

## 3. Regime

**Session counts.** Of 197 sessions:
- **93 were ACTIVE:** 02-01..02-21, 04-03..04-25, 04-28..05-04, and 05-08..07-31.
- **21 were blocked but not in bear mode:** 10-17..10-27, 02-22..03-06, 04-26..04-27, and 05-05.
- **83 were bear mode:** 10-28..01-31 (64 sessions) and 03-07..03-31 (19 sessions).

**What did the blocking.**
- The SPY leg blocked all 104 blocked sessions.
- VIX was above 25 on 20 of them (10-17..11-04, 11-09, 11-10, 03-14, 03-16, 03-20), but never blocked on its own.
- SPY was below its plain SMA200 on 69 of them. SPY compounded **+19.7% across those 69 sessions**.
- The 3% buffer alone blocked 35 sessions, 11 before 02-01 and 24 after, and SPY made only +1.6% across them.
- In the replay, removing the VIX leg changes nothing, and so does lowering the buffer to 1.00.

**SPY against 1.03 × SMA200.** The values are the lane's IEX closes. Each session reads the prior close.

| Session | SPY | SMA200 | 1.03 × SMA200 | SPY vs line | VIX | Phase |
|---|---|---|---|---|---|---|
| 10-17 | 357.68 | 415.13 | 427.58 | −16.35% | 32.0 | blocked |
| 11-01 | 386.22 | 409.97 | 422.27 | −8.54% | 25.9 | bear mode |
| 11-07 | 376.14 | 408.40 | 420.65 | −10.58% | 24.6 | bear mode (VIX ≤ 25 except 11-09/11-10 and 3 March days) |
| 12-01 | 407.45 | 404.14 | 416.27 | −2.12% | 20.6 | bear mode; **first close above the plain SMA200** |
| 12-06 | 399.61 | 403.56 | 415.67 | −3.86% | 20.8 | bear mode; back below the SMA200 |
| 12-29 | 376.69 | 400.41 | 412.42 | −8.66% | 22.1 | bear mode |
| 01-24 | 400.59 | 395.73 | 407.60 | −1.72% | 19.8 | bear mode; above the SMA200 for good |
| **02-01** | 406.50 | 394.50 | 406.33 | **+0.04%** | 19.4 | **ACTIVE; opened by 0.04%** |
| 02-03 | 416.73 | 394.25 | 406.08 | +2.62% | 18.7 | ACTIVE (peak for 2 months) |
| 02-22 | 399.06 | 393.27 | 405.06 | −1.48% | 22.9 | blocked |
| 03-14 | 385.33 | 393.12 | 404.91 | −4.84% | 26.5 | bear mode (the SVB low) |
| 04-03 | 409.37 | 392.62 | 404.40 | +1.23% | 18.7 | ACTIVE |
| 04-26 | 406.12 | 395.09 | 406.94 | −0.20% | 18.8 | blocked |
| 04-28 | 412.38 | 395.35 | 407.21 | +1.27% | 17.0 | ACTIVE |
| 05-05 | 404.96 | 396.04 | 407.92 | −0.73% | 20.1 | blocked |
| **05-08** | 412.68 | 396.13 | 408.02 | **+1.14%** | 17.2 | **ACTIVE: re-opened for good** |
| 06-01 | 417.86 | 396.64 | 408.54 | +2.28% | 17.9 | ACTIVE |
| 07-03 | 443.22 | 399.26 | 411.23 | +7.78% | 13.6 | ACTIVE |
| 07-31 | 456.91 | 406.42 | 418.61 | +9.15% | 13.3 | ACTIVE (peak margin +9.52% on 07-20) |

**The gate re-opened for good on session 2023-05-08**, on the 05-05 close. That is session 140 of 197; it first opened on 02-01, session 74.

**SPY during each blocked stretch:**

| Blocked stretch | SPY-TR | RSP | NAV |
|---|---|---|---|
| 10-17..01-31 (73 sessions) | **+14.18%** | **+18.84%** | −0.15% |
| 02-22..03-31 (28 sessions) | +2.97% (troughed at −4.4% on 03-13) | −1.63% | −1.84% |
| 04-26..04-27 | +1.56% | +0.58% | +0.47% |
| 05-05 | +1.85% | +1.68% | +0.90% |

**Did the gate help or hurt? Against SPY, it hurt: about −13.4 pp from the October–January block.** Against the strategy's own signal it was neutral, and the gate variants show it (this is the same no-gate result as the Bottom line, with the other variants added):

| Replay | Return | Max drawdown |
|---|---|---|
| Actual gate | −9.0% | −15.6% |
| Plain SMA200 | −8.95% | −14.8% |
| No VIX leg | −9.0% | −15.6% |
| **No gate at all** | **−2.4%** | −9.6% |

The no-gate advantage is mostly path: that replay held different names through Feb–May. It was also negative in a +29% tape.

**The damage was in how the gate flickered after 02-01:**
- Its three closes came 1.48%, 0.20% and 0.73% below the line.
- Each re-opening sent a burst of entries into the same short-term top (see section 5).
- Six stops fired while the gate was shut (HON, FE, CAG, GILD, APD, APTV: −$4,723).
- Selling the stock book when the gate closes (W2's H2) makes it **worse** here, at −14.4%, because every close came right after a dip that then recovered.

**The defensive ETF book was nearly inert.**
- It made one trade in 83 bear-mode sessions: XLP from 01-05 to 04-24, **+$312 (+2.5%)**, exited on RSI-70.
- The ETFs mostly failed on RSI: a median of 2 of 5 stood above their SMA200, and RSI < 50 and rising almost never fires on a trending ETF.
- Over the 10-27..01-31 bear stretch, every defensive ETF rose: XLP +3.7%, XLU +6.2%, XLV +2.5%, **GLD +15.9%**, SHY +1.3%. SPY rose +7.5%.
- GLD never qualified.
- Without bear mode, the replay gives −10.1%, so the sleeve added about +1 pp.

## 4. Funnel

The median ACTIVE session ran: universe 505 (503 point-in-time members plus SPY and QQQ) → not held 498 → has indicators 495 → **above SMA200 281 → RSI < 50 and rising 17** → MACD improving 11 → volume 3 → ADX 2 → **passed 2**.

**Which filter kills the most.** Summed over sessions, RSI < 50-and-rising removes **24,676 of the 26,524 name-sessions above SMA200 (93%)**. Above SMA200 removes 19,585, volume 838, MACD 606 and ADX 99, and `rr_ok` removes 0.

**Pass counts.** 72 of the 93 ACTIVE sessions passed at least one name. The biggest days were 06-02 (21), 06-01 (17) and 06-05 (16), all while the book was full.

**Full book.** **33 of the 93 ACTIVE sessions had 0 open slots.** 8 names were held on 37 pv days (02-06..02-13, and most of 05-31..07-17).

**Sector cap.** It blocked **237 entries**, and the slot limit blocked 31. This time the blocked names did *better* than the ones taken:

| Group | Mean per trade | Wins |
|---|---|---|
| Sector-capped names, replayed with the same exits | **+1.30%** | 58%; +0.98% on each name's first skip, n=129 |
| Slot-blocked names | −1.10% | 48% |
| Stock trades actually taken | **−2.10%** | |

- **This is mostly timing, not the cap's logic.** Most of the capped and slot-blocked names came in June, when all signals averaged +4.13%.
- **ROP held the only technology slot for 101 sessions (02-02 → 06-29)** and blocked 39 tech signals. They were mixed: AMAT +10.2, CDNS +8.7, MCHP +7.7, but AMD −6.0, ADI −8.3, MPWR −11.5. Mean +0.44%.
- **Loosening the caps does not help.** Allowing 2 per sector gives −10.1% in the replay, and time stops at 30 or 40 sessions give −16.6% and −14.0%.

**Is the entry better than random?** The replay uses the same exits and costs:

| Entry set | n | Mean per trade | Wins |
|---|---|---|---|
| Every signal pass, all 93 ACTIVE sessions | 335 | +0.57% | 53% |
| Every member, the same sessions | 45,995 | +0.61% | 53% |
| Signal passes on the sessions where the book bought | 117 | **−1.62%** | |
| A random member on those same sessions | | **−0.30%** | |
| **Signal on the first 3 sessions after a gate opening** | 57 | **−4.19%** | 18% |
| Signal on later ACTIVE sessions | 278 | +1.54% | 60% |

Overall, the signal is about random, as the 2026-09-24 research found. It was worse than random exactly where the book used it.

## 5. Decisions

### Top 5 winners
| Trade | Entry reason | Exit |
|---|---|---|
| BDX 05-12 → 07-24, **+$1,323 (+11.9%)** | RSI 44.5 (prev 44.1), MACD −1.94 (prev −2.02), ADX 30.1, volume 1.25× | Opened +6.6% at 281.85, above its 273.91 target, so the gapped target filled at the open. It was then −2.2% by 07-31. |
| ORLY 02-01 → 02-14, **+$1,052 (+8.4%)** | Opening-day entry (margin +0.04%): RSI 41.2 (prev 39.9), volume 1.02× | Target in 9 sessions. ORLY was −5.2% 20 sessions later, so the exit was good. |
| ATVI 05-16 → 07-11, **+$1,051 (+9.6%)** | RSI 45.9 (prev 41.1), MACD −0.01 (prev −0.23), ADX 38.5; sector `unknown` | Target hit. ATVI was +7.3% by 07-31 as the MSFT deal cleared, so about $0.8k was left behind. |
| GOOGL 07-03 → 07-26, **+$1,024 (+9.1%)** | RSI 47.8 (prev 46.0), ADX 25.9, volume 1.19× | Target hit. +1.4% after 20 sessions. |
| ROP 02-02 → 06-29, **+$1,014 (+8.3%)** | RSI 47.9 (prev 41.7), volume 2.14× | Target after 101 sessions, while it blocked 39 tech entries. +4.1% after 20 sessions. |

### Top 5 losers
| Trade | Entry reason | Exit |
|---|---|---|
| OKE 05-08 → 05-15, **−$1,081 (−9.35%)** | Re-opening session: RSI 48.6 (prev 37.5), MACD improving, volume 1.32×. Entry gapped +1.25%. | Gapped through its 60.64 stop to 59.35 on the Magellan deal news. It was +13.5% by 07-28. |
| APTV 04-12 → 04-27, **−$932 (−7.7%)** | RSI 40.8, ADX 15.8, volume 1.01×: marginal on every filter. Entry gapped +1.57%, so the stop sat −7.45% below the fill. | Stopped one session after the gate closed. |
| GILD 02-01 → 03-09, **−$829 (−6.6%)** | Opening day (+0.04%): RSI 46.4 (prev 42.7), volume 1.48× | Stopped in bear mode, then +5.9% over the next 20 sessions. |
| WBD 04-18 → 04-21, **−$823 (−6.8%)** | RSI 49.4 (prev 41.0), ADX 15.6 | Stopped in 3 sessions. −10.4% 20 sessions later, so the stop was right. |
| STLD 04-18 → 05-04, **−$819 (−6.8%)** | RSI 43.3, MACD 0.18 (prev 0.06), ADX 25.2 | Stopped the day before the gate's third close. |

**Patterns in the 24 losers:**
- **Timing: 14 of the 24 were bought on the first 3 sessions after a gate opening.**

  | Opening | Names bought | P&L |
  |---|---|---|
  | 02-01..02-03 | ORLY, CAG, GILD, FE, ROP, NDAQ, HON | −$1,761 |
  | 04-03 | DAL | −$640 |
  | 04-28 / 05-01 | AMP, FOXA, REGN, MPC, PKG | −$3,507 |
  | 05-08 | OKE, DOW, HES | −$2,514 |

  Even a random S&P member lost −1.41% on those sessions (against +0.91% later), because every opening marked a short-term high: SPY then fell 2.6% (Feb) or RSP fell 2.3% (late April).
- **Sector.** Value and cyclicals lost as breadth collapsed:
  - Energy (OKE, MPC, XOM, plus HES): −$2.9k.
  - Materials (STLD, DOW, APD): −$2.3k.
  - Consumer defensive (CAG twice, CLX): −$1.6k.
  - Technology (+$1.1k) and the delisted `unknown` names ATVI and HOLX (+$1.2k) were the only positive buckets.
- **Gaps through the stop are not the problem.** There were two (OKE −9.35%, CAG −5.9%).
- **The prior-close anchor is a problem.**
  - The stop sat between −5.0% and −8.6% from the fill, and 18 of 37 stock trades lost more than 6% from their fill.
  - Every entry that gapped up more than 0.5% and then lost (OKE, APTV, WBD, STLD, DOW, HES, XOM) lost 6.1–9.4%.
- **Whipsaw.**
  - After the 23 stops that have price data, the names rose a median +1.8% over 20 sessions, and 7 traded back above their entry.
  - None reached its original target within 20 sessions, so the stops cut noise rather than turning points. By 07-28 the stopped names stood a mean +7.7% above their stop price.

## 6. Why it trailed SPY (−38.41 pp)

The attribution is trade by trade: each trade's notional is compared with SPY, and with RSP, over the identical holding window. It is additive on the $100k.

| Component | pp |
|---|---|
| **Cash while the gate was closed (10-17..01-31).** SPY +14.18%; the book held 0% stocks, plus XLP from 01-05. | **−13.4** |
| **Underexposure after re-entry (02-01..07-28).** 73% invested, with 31 more blocked sessions. | **−10.3** |
| **Stock selection vs SPY, same windows** | **−14.1** |
|   of which breadth: RSP vs SPY over the same windows | −5.1 |
|   of which picks vs RSP, including costs | −8.9 |
| Defensive ETF (XLP) vs SPY | −0.7 |
| Costs (inside selection) | −1.6 |
| Exits cutting winners | ≈ −0.5 per trade |
| Dividends not credited (outside this sum) | ≈ −0.6 |

**On exits cutting winners.** The 8 target exits rose a median +1.4% over the next 20 sessions: GWW +9.4, ATVI +7.3 and ROP +4.1 against ORLY −5.2 and BDX −2.2. A +15% target on the same entries gives −1.63% per trade instead of −2.10%, about $2k.

**Net.** Two things drive the loss:
- **Structural lag:** a 200-day gate is out for a V-bottom that happens below the SMA200.
- **A bad book once it was in:** the gate re-opened four times into short-term tops, each re-opening filled the empty slots in one burst of value and cyclical names, and the −6% bracket stopped nearly all of them out while breadth fell 7.5%.

The research claims hold, with one exception:
- The entry is about random: +0.57% against +0.61% overall.
- It trailed SPY after costs.
- **Here the regime filter gave no drawdown benefit:** −15.6% against SPY's −7.5%.

## 7. Change hypotheses (implement nothing)

1. **Require confirmation before stock entries after a gate opening, or a clearance band above 1.03×.**
   - *Observation:* 16 entries on the first 3 sessions after 4 openings lost −$8.4k (14 of 16 lost). On those sessions the signal averaged −4.19% (n=57) against +1.54% later. W2 showed the same: three one-session flips, and 10 of 11 fills lost.
   - *Expected effect:* +2 pp here.
     - 3 consecutive ACTIVE sessions: −7.0%.
     - Plus at most 2 entries per session: −6.8%.
   - *What does not work:* a per-session cap alone (−12.5%) and close-only-below-plain-SMA200 hysteresis (−10.2%) are both worse.
   - *Risk:* only 7 openings across two windows, all in 2022–23, and the gain is inside the ±5 pp path noise. It could delay entry after a real V-bottom.
2. **Replace the flat −6% bracket with a volatility-scaled stop (k × ATR), or anchor it on the fill.**
   - *Observation:* the 24 losing exits cost −$17.6k, and the stopped names rebounded a median +1.8%.
   - *Expected effect:* with a −10% stop:
     - The taken trades go from −2.10% to −0.17% per trade.
     - All signal passes go from +0.57% to +0.95%.
     - The portfolio goes from −9.0% to −2.6%, and max drawdown from −15.6% to −9.2%.
   - *Risk:* regime-specific. On W2's bear entries the same flat −10% stop costs 1.4 pp per stock trade (−4.63% to −6.06%). That is why the stop should scale with volatility rather than simply widen. Test on W1–W4 before adopting it.
3. **Don't loosen the gate to fix recoveries.**
   - *Observation:*
     - SPY's +14% came entirely below its plain SMA200 (10-14 → 11-30).
     - The 3% buffer cost +0.16% of SPY-TR (11-30 → 01-31), and +1.6% across all 35 buffer-only sessions.
     - The replay results are flat: plain SMA200 −8.95%, no VIX leg −9.0%, no gate −2.4% (a path effect, and still −31.7 pp behind SPY).
   - *Implication:* the recovery lag is structural to a 200-day filter paired with a mean-reversion entry. If SPY-relative return in V-bottoms matters, it needs a separate trend or index sleeve, not a gate tweak.
   - *Risk:* this is one V-bottom. The buffer did help in W2.
4. **In bear mode, stop swing-trading the defensive ETFs: hold cash or T-bills, or a static basket.**
   - *Observation:* the sleeve fired once in 83 sessions here (+$312) and 7 times in 152 sessions in W2 (−$2,994). Its RSI < 50-and-rising entry almost never fires on a trending ETF: GLD rose +15.9% without once qualifying.
   - *Expected effect:* the evidence conflicts across windows.
     - An equal-weight basket would have made about +5.9% here but −8.8% in W2.
     - Cash is roughly neutral in both.
   - *Risk:* regime-specific in both directions (in 2008 and 2020, GLD and SHY rallied).
5. **Fix fidelity before comparing windows.**
   - *Proposal:*
     - Point-in-time sectors, or cap `unknown` as its own sector. HES entered beside OKE, and HOLX beside BDX.
     - Credit dividends to held stocks, since the engine is price-return while SPY is total return (about 0.6 pp).
     - Keep logging engine resizes. None happened in this run.
   - *Expected effect:* about neutral. The capped replay gives −11.1%, inside the noise.
   - *Risk:* none for the strategy, but it breaks byte-identity with earlier runs.
6. **Leave the sector cap, the slot count and the RSI/MACD thresholds alone.**
   - *Observation:* 2 per sector (−10.1%) and time stops (−14% to −17%) are worse. The capped names' +1.3% was June timing, not a signal. RSI kills 93% of the candidates, yet the survivors perform like random names.
   - *Implication:* the lever is *when* the book fills (hypothesis 1) and *how far* the stop sits (hypothesis 2), not which names pass.
   - *Risk:* the ACTIVE-session sample is correlated. The 93 sessions are really about 4 market states.
