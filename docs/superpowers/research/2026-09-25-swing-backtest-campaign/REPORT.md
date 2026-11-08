# Swing-trader port: backtest campaign synthesis (W1–W7)

**Date:** 2026-09-25 · **Instance:** `swing-paper` (doc 204 "Swing trader paper"), daily bars (86400), $100k per window, AI gate OFF.
**Sources:** `analysis/W1…W7-*.md` (the per-window deep dives; every number below traces to one of them), `metrics.json`,
and `docs/superpowers/research/2026-09-24-swing-trader-port-research.md`. This report is read-only: no code, config or DB row was changed.
**Basis:** a pv step dated D carries the close of D−1, so SPY-TR and RSP-TR are measured from the previous session's close, as in
`analyze.py`. Fills are deduped. RSP was recomputed on the same basis (it matches every per-window figure).

## 1. Verdict

The port does not beat the S&P 500. In seven windows from July 2021 to September 2026 it beat SPY total return once (the 2022 bear,
by sitting in cash) and trailed it six times, by 3.8 to 38.4 pp. Against RSP equal-weight the record is the same: it beat RSP once
(W2) and trailed it six times, by 1.6 to 31.2 pp. Only two windows made money. Chained, the seven windows compound to about −23%,
against SPY +92% and RSP +54%. The entry signal has no reliable edge over a random S&P name, so what remains is an equal-weight,
tech-underweight book that pays 3–5% a year in costs. Its regime gate saved it in the 2022 bear and the 2025 tariff crash, but it
re-opened late and whipsawed everywhere else. Measured against the standing bar (beat SPY in every 3–6-month window and stay
positive in bears), it fails outright: W7 beat SPY in 26% of 3-month windows and 0% of 6-month windows, and W2 lost 15.3% in 2022.

**Against the 2026-09-24 research:**
- "Entry ≈ random": **confirmed** in 7 of 7 windows.
- "Trails SPY after costs": **confirmed** in 6 of 7.
- "The drawdown benefit comes from the regime filter": **seen in only 2 of 7** (W2 and W6), and in W6 the stops did most of the protecting.

## 2. Results

| Window (backtest id) | Tape | Return | SPY-TR | RSP-TR | vs SPY | vs RSP | Max DD (SPY DD) | Invested | Closed trips (+open) | Win rate | Expectancy / trade | Engine costs |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| W1 2021-07-12..12-31 (389081) | bull | **+6.23%** | +10.07% | +7.78% | −3.84 | −1.55 | −5.98% (−5.11%) | 86.9% | 39 (+7) | 51.3% | +0.95% | $1,855 (1.86%) |
| W2 2022-01-03..10-14 (588533) | bear | **−15.31%** | −22.06%¹ | −18.30%¹ | **+6.75**¹ | **+2.99**¹ | −15.77% (−24.50%) | 21.0% | 31 (+0) | 12.9% | −4.32% | $1,566 (1.57%) |
| W3 2022-10-17..2023-07-31 (647829) | V-recovery | **−9.09%** | +29.32% | +22.07% | −38.41 | −31.16 | −15.58% (−7.54%) | 46.6% | 33 (+5) | 27.3% | −2.35% | $1,632 (1.63%) |
| W4 2023-08-07..2024-03-28 (821492) | chop→bull | **+5.71%** | +18.35% | +12.91% | −12.64 | −7.20 | −9.60% (−8.66%) | 80.5% | 44 (+7) | 47.7% | +0.60% | $2,037 (2.04%) |
| W5 2024-04-01..12-31 (535352) | mega-cap bull | **−4.98%** | +13.55% | +4.46% | −18.53 | −9.44 | −9.43% (−8.41%) | 89.0% | 60 (+8) | 41.7% | −0.32% | $2,947 (2.95%) |
| W6 2025-01-06..06-30 (578822) | tariff crash + V | **−0.64%** | +4.50% | +3.51% | −5.14 | −4.15 | −8.16% (−18.76%) | 55.7% | 30 (+8) | 40.0% | −0.13% | $1,633 (1.63%) |
| W7 2025-07-07..2026-09-18 (554589) | recent bull | **−6.15%** | +23.30% | +17.10% | −29.45 | −23.25 | −12.33% (−8.88%) | 87.9% | 128 (+8) | 42.2% | −0.36% | $5,557 (5.56%) |
| **FULL 2021-07-12..2026-09-18 (757712)** | all | **−8.75%** | +87.35% | +52.51% | **−96.10** | **−61.26** | **−32.09% (−24.50%)** | 68.8% | 341 | — | — | — |

¹ W2's book was flat at both ends, so the benchmark endpoints are a choice. Measured 01-03c→10-14c, SPY-TR is −24.27% and
RSP −20.18%, which gives +8.96 pp vs SPY and +4.87 pp vs RSP. Invested % is from `metrics.json`, which counts holiday
repeats; the holiday-free figures in the analyses differ by 1 pp or less.

**Aggregates**
- **Beat SPY:** 1 of 7 (W2). **Beat RSP:** 1 of 7 (W2). **Positive:** 2 of 7 (W1, W4). **Drawdown shallower than SPY's:** 2 of 7 (W2, W6).
- **Chained:** −23.4%, against SPY +92.1% and RSP +53.7%. There are seven cold starts, each with a day-1 buying burst, so this
  is indicative only and does not replace FULL.
- **Trades:** 365 closed round trips, 39.7% won. The trade-weighted expectancy is −0.60% a trade net.
- **Costs:** the engine charged $17.2k across the seven $100k windows.
- **Gross of costs and missing dividends** it still trails SPY in 6 of 7 windows: W1 ≈ −1.3, W3 ≈ −36, W4 ≈ −9.5, W5 ≈ −14.5,
  W6 ≈ −3.1 and W7 ≈ −22 pp. Costs are not the reason it loses.
- **Tie-order distribution** (30 random tie-break orders, same rules):
  - W5: −3.5% ± 3.4. No order beat RSP, and every order trailed SPY by 9.9 pp or more.
  - W7: +4.8% ± 8.2. 0 of 30 beat SPY and 1 of 30 beat RSP. The engine's path sits near the 13th percentile.
  - So the endpoints above are typical draws, not unusually bad luck.
- **What to expect from FULL:** the prior local replication (23 bps, point-in-time membership, 2021-06→2026-06) gave +2.9% to +7.5%,
  and local harnesses have over-stated before. FULL is one alphabetical path: read it against those bands, not on its own.

## 3. What consistently happens across windows

**3.1 The entry signal is about as good as random (7 of 7 windows).** Each window compares the signal's passers with random S&P names
under the same exits:

| Window | Signal passers | Random comparison | Reading |
|---|---|---|---|
| W1 | Per closed trade vs RSP +0.08% ± 0.93; 20-day excess vs RSP −0.61 ± 0.43 | No logged feature ranks passers (\|ρ\| ≤ 0.07) | random |
| W2 | −5.38% a trade | Random member above its SMA200 −3.80% | **worse** than random |
| W3 | +0.57% | All members +0.61%. On the sessions it bought: −1.62% vs −0.30% | random, worse where used |
| W4 | +0.37% | Random above-SMA200 +0.91%, all members +0.58% | random or worse |
| W5 | +0.47% (bracket) | +0.05%. On a fixed 20-day hold, passers trail RSP by −0.50% against −0.10% for random names | edge only through the bracket |
| W6 | +0.64% | −0.69%, but the edge is January only (June −0.42% vs +0.74%) | random |
| W7 | +0.25% | −0.23%. 20-day excess −0.38% vs −0.05% | random |

"RSI < 50 and rising" removes 88–93% of trend-qualified names in every window and selects laggards. ADX > 15 is nearly inert, and
`rr_ok` never binds: 9/6 is always ≥ 1.5. The small positive gaps in W5–W7 pool overlapping trades, so their errors are
understated, and they vanish on a fixed hold.

**3.2 Equal weight, mega caps and the sector cap (W1, W3, W4, W5, W7; reversed in W6).**
- **RSP trailed SPY in 6 of 7 windows** (W2 is the exception), and by 34.8 pp over the full span.
  - An 8-slot, equal-dollar book behaves like RSP. Equal-weight drag: W1 −2.1, W3 −5.1, W4 −2.8, W5 −6.7 and W7 −1.8 pp.
    W6 gained +1.7 pp in the crash.
  - In W7 the monthly excess vs SPY correlates 0.80 with RSP−SPY.
- **Mega caps are almost never bought.**
  - In W5, NVDA passed the full filter on 2 of 187 sessions, AAPL on 2 and MSFT on 8. The top 11 names supplied +10.6 of SPY's
    +13.55 pp, and SPY without them ≈ RSP.
  - In W7, NVDA, MSFT, AAPL, AVGO and ORCL had zero entries.
- **The 1-per-sector cap turns the book sector-equal-weight.** Technology can hold at most 12.5% (W5 held 11.8%) against about 30%
  of SPY. One stale holder then blocks its whole sector:
  - ROP held tech for 101 sessions in W3 (39 tech signals blocked) and for 181 sessions in W5 (112 signals from 42 names,
    including MSFT, AAPL and AVGO; those replay at +1.75%).
  - DTE held utilities for 154 sessions in W7 (115 skips), and CHD held staples for 155 in W5.
- **Loosening the cap does not help** (in pp): 2 per sector gives W3 −1.1, W4 −6.0, W5 +1.2 paired (noise), W6 +7.8 single-path
  and W7 −8.0 ± 1.4 paired.

**3.3 The regime gate closes late, flickers, re-opens late, and the entry burst after it re-opens loses.**
- **It protects only in a slow bear, or when it closes before the crash.**
  - W2: +11.4 pp from cash, which is all of W2's excess (April–October).
  - W6: +13.0 pp of cash in the crash leg, but the gate itself was worth about +4.3 pp; the stops emptied the book.
  - In the other five windows, the strategy's drawdown was deeper than SPY's.
- **Late close:**
  - W2 closed on 01-20 with SPY already −4.9%, and 5 stops fired while the gate still read ACTIVE.
  - W4 closed with 57% of the fall done, and W7 with 37%. W6 is the exception, closing at 24%.
  - Closing sells nothing, so the book shrinks only through stops (W2, W4, W5, W6).
- **Flicker:**
  - W2 had three one-session re-openings that cleared the line by 0.05–0.27%, and each reset the bear counter.
  - W3 closed three times within 1.5% of the line and re-opened four times.
  - W4 re-opened for 1, 3 and 2 sessions. W6 flipped 10 times. W7 had a one-session re-open on 03-11.
- **Late re-open:**
  - W3: SPY +14.2% and RSP +18.8% while blocked, all of it below the plain SMA200 (−13.4 pp).
  - W4 re-opened with SPY already +6.5% off its low (bear sessions −3.1 pp).
  - W6 re-opened after 78% of the rebound (−18.8 pp), and W7 after 50%.
- **Entry bursts after an opening.**
  - Losers: W2 10 of 11 fills lost (−$5.1k); W3 14 of 16 lost (−$8.4k, 93% of the run's loss); W4 5 of 5 (−$4.5k);
    W6 4 of 4 (−$2.4k).
  - Counter-cases: W5 (08-09: 3 of 5 won, +$1.8k) and W7 (03-11 cohort +$0.8k; 04-14 cohort −$0.2k).
  - Every window's cold start is the same event: 8 empty slots filled in one session. W1 had 5 of 8 day-1 names stopped; W4 lost
    6 of 8; W5 put 87% of equity into 7 names, and all that session's passers replay at −4.8%; W7 stopped 5 of 6. W6 day 1 won (+$2.8k).
- **The 3% buffer blocked the worst signals** in W2 (−3.4% a trade), W4 (−1.0%) and W6 (−3.2%), and it cost little in W3.
- **No gate vs gate** (replica, pp, positive = better without the gate): W1 +0.7, W3 +6.6 (path), W4 +4.6, W5 −0.8,
  W6 +1.0 on return but a worse drawdown, and W7 +4.1 ± 1.4 paired. Removing the gate wins more windows but loses W2, the window it exists for.

**3.4 The −6% stop produces every loss (7 of 7).**
- **All 220 closed losers across the seven windows were stop exits**, bracket or close-based.
  - The payoff ratio of 1.24–1.45 needs a 41–45% win rate. The book cleared that only in W1 and W4, its two positive windows.
- **Gaps:** 30 of the stops gapped through.
  - The cost beyond the stop was roughly offset by gap-ups over the target: W4 −$928 vs +$903, W5 −$712 vs +$595, W7 −$2,430 vs +$1,574.
  - In W7, 18 trips with a ≥4% gap on ≥1.8× volume lost −$6.4k, while the other 118 trips made +$0.2k.
- **Anchor:** the bracket is anchored on the prior close, not the fill.
  - The effective stop ranged from −0.1% (DAY, W6) to −10.4% (CASY, W7), and ADBE (W1) entered below its own stop.
  - Gap-up entries that failed lost 7–9% in every window.
  - The close-based stop keys off the fill and sells a session later, so it is a second, different stop (W1 CAT −10.4%).
- **Whipsaw depends on the tape.** Stopped names over the next 20 sessions: W1 +4.9%, W3 +1.8% (median), W4 +2.3%, W5 +1.1%,
  W6 −4.1% and W7 −0.1%.

**3.5 The +9% target: mixed.**
- **After the target** (next 20 sessions): W1 +4.2%, W3 +1.4% (median), W4 +6.0% (BX +22.9%, IT +18%), W5 −2.9%, W6 −6.6% and W7 −0.5%.
  W7 also sold DELL (+335% by 09-2026) and MPC (+120%).
- **It cuts leaders in trending tapes and banks gains in mean-reverting ones.**

**3.6 Defensive ETFs and bear mode never helped, and they are never sold on re-open.**
- **Activity by window:**
  - W1 and W5 never entered bear mode.
  - W4 bought nothing, and W6 made 1 buy in 44 sessions.
  - W3 made 1 trade (+$312) in 83 sessions, while GLD rose +15.9% without ever qualifying.
  - W2 made 8 trades for −$3.0k; all five ETFs fell 4.8–10.5%, and SHY never closed above its SMA200.
  - W7 bought XLV, GLD and XLP at the 03-30 low and held them through the rebound: −5.5 pp, its largest loss outside selection.
- **Why:** the swing entry rarely fires on a trending ETF, and no rule sells a defensive ETF when the gate re-opens.
  - SHY's bracket is unreachable.
  - XLP sat for 119 sessions in W7, and GLD with SHY held 25% of equity and 2 slots through June in W6.
- **Relative to SPY:** +1.4 pp in W2, −0.7 in W3, −0.6 in W6 and −5.5 in W7.

**3.7 Costs and turnover (7 of 7).**
- When invested, the book turns over 9–13× equity a year one-way (W7: 16.2× in 14.5 months).
- The engine charged 18–21 bps a fill (target legs fill as limits and pay no spread), about 3.2–4.6% a year: $17.2k over the campaign.
- If real single-name costs are lower than the engine's flat model, the cost line shrinks. Even at zero cost, the book trails SPY in 6 of 7 windows.

**3.8 No dividends in the engine (7 of 7).**
- The result is `price_return`, the engine credited only QQQ's dividends, and the benchmark is total return.
- The bias is W1 −0.66, W3 −0.6, W4 −1.1, W5 −1.1, W6 −0.35 and W7 −1.7 pp (all lower bounds), about −5.5 pp in total.
- Adding it back flips no verdict.

**3.9 Alphabetical tie-break noise (±8 pp).**
- **How the order is set:** candidates are visited in today's S&P list order, which is alphabetical by company name. On busy days the
  alphabet picks the book (W1 07-12: 50 passers; W6 day 1: 36; W7 10-15: 36).
- **Size of the noise:**
  - Over 30 random orders, one standard deviation is 3.4 pp in W5 (9 months) and 8.2 pp in W7 (14 months; range −13.9% to +20.6%).
  - W3, W6 and W7 each concluded that single-path variant gaps under about 5–8 pp are noise.

## 4. Bugs and fidelity issues to fix before trusting any number

| # | Issue | Evidence | Impact | Fix |
|---|---|---|---|---|
| 1 | **The same-session exit→entry credit flags are missing on doc 204** | `scripts/swing_lab_setup.py` sets `backtest_credit_pending_sell_proceeds` (the emulator's buying power) and `backtest_credit_sell_proceeds_enabled` (the broker's buy gate) only in `swing_lane(lab=True)`. The campaign ran on `swing-paper`, whose doc 204 is built with `lab=False`. A read-only check today confirms neither flag is set and no swing-lab doc or instance exists. Clamped buys: W1 ELV 4/32 sh, DRI 14/87; W2 ESS 24/35; W4 EXPD 2/104, CMCSA 248/273, JBHT 19/65; W5 ALLE 4/102, VLO 8/83, MDT 3/137; W6 CLX 24/80; W7 WFC 8/153, ABBV 5/46. Entries **silently dropped**: W2 MLM, W5 GOOGL, W7 AMAT | The log says "ENTER N sh", but the engine fills fewer shares or none, with no refusal row, while `unfilled_order_count` stays 0. P&L per window runs from −$1.3k to +$0.8k, all luck. The stubs hold a slot and a sector for weeks (ELV blocked 8 healthcare names for 5–7 weeks; EXPD blocked 47 industrials skips over 46 sessions) | Run research on a swing-only lab doc created by `swing_lab_setup.py`, which sets both flags, or add both flags to doc 204's swing lane (operator decision). Log a refusal for every engine resize or drop. Check whether live has the same gap (W1 pointed at `_consider`/`swing_live_budget` against pending-sale proceeds) |
| 2 | **Sector map built from today's yfinance** | `SwingSectorMap` is as of 2026-09-25. 41 delisted or acquired names read `unknown`, and `sector_conflict` exempts `unknown`. Bypasses: W2 HOLX, FRC, DRE, INFO (−$3.0k); W3 HES beside OKE; W4 MRO beside FANG (−$1.6k); W6 K beside KR, DAY beside AVGO; W7 BK, CTRA. XLP, XLU, XLV and SHY are `unknown` too, so they are uncapped among themselves | Replica P&L: W3 −2.1, W4 +0.45, W5 0, W6 +1.6 and W7 −2.2 ± 0.9 pp. That is noise, but the rule is not applied as written, and it is a survivorship leak | Backfill point-in-time sectors for the delisted names. Until then treat `unknown` as its own sector and give the ETFs a real label |
| 3 | **BacktestSteps stores every row twice** | Each row appears twice under one seq, plus a null seq-0 row: W2 has 410 pv rows = 205 × 2 and 124 trade rows = 62 × 2 | Naive counts double: the first W1 metrics said 171 fills, and there were 85. Averages are unaffected because the duplicates are identical | The engine should write one row per step, or tag the final row and have every reader filter on it. `analyze.py` dedupes on (time, symbol, side, qty) meanwhile |
| 4 | **A pv step dated D carries the previous session's close** | Every window. Holiday pv steps also repeat the prior close (6–10 per window) | A one-session benchmark misalignment (W1: −3.17 vs −3.84 pp). Holiday repeats inflate the averages slightly (W6 invested 55.7% vs 54.7%). W2's excess spans +6.75 to +8.96 depending on the endpoints | Stamp pv with the session whose close it carries (or add an explicit close date), and skip non-sessions. Until then align to SPY's prior-session close, as `analyze.py` does |
| 5 | **Log-pull credentials under the deprecated `DEFAULT_ADMIN_*` keys in `.env`** | `scripts/_api.py` still accepts them with a warning, but `scripts/pull_backtest_logs.py` reads only `INTELLISTOCK_API_USERNAME/PASSWORD` (or `INTELLISTOCK_API_TOKEN`). The W6 and W7 analysts passed the values through the process environment | No data impact. Log pulls fail, which invites ad-hoc credential handling | Rename the two keys in `.env` (operator action; not done here) |
| 6 | **The earnings block is skipped in backtests** | Live applies `EARNINGS_HARD_BLOCK` = 5 days at entry (`backend/swing_trader/constants.py:41`). Backtests skip it, as the original backtester did, and have no point-in-time earnings calendar | Backtests include entries live would refuse. The sign is unknown: event gaps were a wash in W4–W6, but cost W7 −$6.4k (18 trips). Earnings-day stops: W1 CI and CSCO, W4 CMCSA, W5 MRK (all from public record, unverified in-data) | Load a point-in-time earnings calendar and apply the same block in backtests, so that the backtest and paper measure one strategy |

**Also:**
- **No interest on idle cash.** This biases the cash-heavy windows (W2, W3 and W6) by roughly 1–2 pp each. That is my estimate from
  average cash × the T-bill yield, not a measurement.
- **The campaign ran on the paper doc, which also carries the wheel lane.** The lab setup warns that a second lane can reorder
  same-session fills. The wheel logged itself inert, so the impact is ≈ 0, but research belongs on a swing-only doc.
- **Deal-pinned takeover targets are held as zombies** (K in W6, EA in W7). This is universe hygiene, not a return lever.

## 5. Change hypotheses, ranked by cross-window evidence

"Paired" means the replica run under the same 30 random tie orders, as mean ± s.e. (orders better). "Single" means one alphabetical
path, whose noise is ±5–8 pp. Nothing below is implemented.

**H1. Stop swing-trading defensive ETFs in bear mode.** At minimum, sell them at the next open when the gate re-opens. Better, hold
cash or T-bills (a static BIL/SHY position) and sell it on re-open.
- *Supports:* W2 (sleeve −$3.0k), W3 (1 trade in 83 sessions; missed GLD's +15.9%), W4 (bought nothing), W6 (zombies, −0.6 pp)
  and W7 (−5.5 pp).
- *Contradicts:* nothing in absolute terms. In W6, selling on re-open scores −0.18 ± 0.08, because the ETFs were bought on the last bear session.
- *Paired:* W7 sell-on-re-open +1.83 ± 1.16 (20/30); W7 no bear mode +1.59 ± 1.15; W6 −0.18 ± 0.08.
- *Risk:* low, since it adds a missing exit. A static GLD or defensive basket is a regime bet (the basket fell 8.8% in 2022, while
  GLD was worth +6.5 pp in W6). T-bills are the robust floor, but the engine needs cash interest to credit them.

**H2. Confirm before stock entries after a gate opening.** Require 3 consecutive ACTIVE sessions, and do not reset the bear counter
on a single ACTIVE session.
- *Supports:* W2 (≈ +5 pp est.; 10 of 11 flicker fills lost), W3 (+2.0 single; 14 of 16 lost), W4 (+0.5 single, drawdown −6.1%
  vs −10.3%) and W6 (re-open cohort 4 of 4 lost; hysteresis +1.35 single).
- *Contradicts:* W5 (the 08-09 burst won) and W7 (the 03-11 flicker cohort +$0.8k). W1 had no openings.
- *Paired:* **none yet**. Every supporting number is single-path and inside the noise.
- *Risk:* it delays re-entry after a real V (W4 rally leg +2.0% vs +3.6%), which worsens the late-re-open problem H3 targets.
  The evidence is about a dozen openings, all 2022–2026.

**H3. Re-open faster.** Re-admit entries when SPY > SMA50 and VIX ≤ 25, and keep the 1.03 × SMA200 close rule.
- *Supports:* W6 (re-opened after 78% of the rebound), W7 (after 50%), W3 (SPY +14% below its plain SMA200 while blocked) and W4
  (the below-SMA200 sessions held the best signals, +5.3% a trade).
- *Contradicts:* **W2 is untested and is the likely failure.** 2022 had six failed rallies, and a rough SPY-sleeve version of this rule
  lost −1.8% there. The buffer also blocked the worst signals in W2, W4 and W6.
- *Paired:* **W6 +2.38 ± 0.21 (30/30)**; W7 +0.37 ± 1.34 (noise).
- *Risk:* it buys bear-market rallies, and it pulls against H2. Test each alone and then together.

**H4 (structural, not a fix). Run the book as a satellite beside a cap-weighted core, or judge it against RSP.**
- *Supports:* W1, W3, W4, W5 and W7. A 50/50 SPY core would have made +4.3% in W5 (vs −4.98%) and +8.6% in W7 (vs −6.15%).
- *Contradicts:* W2 and W6, where the core gives back the gate's protection.
- An idle-cash sweep while ACTIVE is a smaller version of the same thing: +1–3 pp gross in W1 and W4, near 0 in W5–W7 at 88–89%
  invested, and its extra fills pay 23 bps each.
- *Risk:* this is beta. It narrows the gap by diluting the strategy, not by improving it.

**Changes whose evidence contradicts across windows. Do not adopt any of them.**

| Change | Helped | Hurt | Paired | Reading |
|---|---|---|---|---|
| **Drop the +9% target** | W4 +1.9 (single); W5 +3.64 (25/30) | W6 −5.8 (single); W7 −5.8 ± 1.7; W2 per trade −4.7% → −8.1% with wider exits and no target | W5 +3.6, W7 −5.8 | A bet on trending tapes. It flips sign by regime |
| **Sell the stock book when the gate closes** | W2 +$5.8k; W4 +4.8; W6 +2.3 (+6.0 with 3-session confirmation) | W3 −5.4 (every close came right after a dip that recovered); W7 −6.3 ± 1.3 | W7 −6.3 | It helps only when more downside follows the close |
| **Change the flat −6% stop** (−10%, ATR, fill anchor) | W3 −10%: +6.4, drawdown −9.2% vs −15.6%; W4 −10%: +0.6 (+4.8 in the rally); W6 −10%: +2.2 | W2 −10%: −1.4 pp a trade; W5 over 30 orders −10% −0.4, fill anchor −1.0, 2×ATR/3×ATR −0.5 (all noise); W7 −10% without the target −12.6 ± 1.9 | W5 null; W7 −12.6 | No width wins across tapes. Fill-anchoring has one non-return merit: uniform risk per trade |
| **Cap new entries at 2 a session** | W4 +3.5 (single); W5 +2.28 (22/30) | W3 −3.5 (single); W6 −9.0 (single) | W5 +2.3; W7 +0.8 ± 1.0 | Noise |

**Leave alone:**
- Loosening the sector cap (§3.2).
- A blanket time stop: W3 −5 to −7.6, W5 +0.47 paired, W7 −6.6 ± 1.7.
- Tuning the RSI or MACD thresholds, since the entry is random.

The one untested idea around the cap is a *stale-holder replacement* rule (W5 H4): let a new same-sector signal replace a holding that
has sat within ±3% of its fill for N sessions.

**Pre-registered test protocol (required before adopting anything):**
1. **Fix fidelity first:** bugs 1, 2 and 6, plus dividends. Move research to a swing-only lab doc and re-run the baseline on all seven
   windows and FULL. Every number in this report will shift.
2. **Write each hypothesis down before running it:** one rule, fixed parameters, no sweep. The primary metric is the paired Δ
   return vs baseline per window, over the same 30 tie orders. The secondary metrics are max drawdown and the W2 result.
3. **Run every window, W2's bear included.** The pass bar:
   - Δ > 0 in at least 6 of 7 windows.
   - Pooled mean Δ > 2 s.e.
   - W2's Δ ≥ −1 pp, with a drawdown no deeper than the baseline's.
   - No single window supplies more than half the gain.
4. **One change at a time.** Combine only changes that passed alone, then re-test the combination the same way.
5. **Confirm on the engine**, one backtest at a time and never in parallel, at granularity 86400: FULL, W2 and W7. The engine result
   must land inside the replica's 30-order band, because local replicas have over-stated before. Engine bars start in 2020-07, so a
   2012–2020 out-of-sample check is possible only in the local harness.
6. **Count the variants.** This campaign has already tried about 20. At a 2 s.e. bar, expect about one false pass per 20.
7. **The final comparison is against SPY and against "SPY under the same regime gate, else cash".** The earlier research put that at
   +50.4% vs SPY +83.4%, with a −10.1% drawdown. If a variant cannot beat the gated SPY, the stock-picking layer adds cost, not value.

## 6. What the AI conviction gate might add

- **What it is:** live scores every passer 0–100 (Claude conviction from RSI/MACD, sector-ETF RSI, the earnings date and web-search news).
  A score of 75 or more trades, and 50–74 waits for dashboard approval.
  - All seven backtests ran with the gate off, as the original repo's backtester did. **Every number above measures the technical rules alone.**
- **What it can and cannot do:**
  - It can only remove passers. It cannot reach mega caps the funnel never passes, fix the equal-weight drag, the gate's timing or the
    defensive sleeve.
  - Its whole possible value is ranking the 4–10 passers a session, which is exactly where no logged feature worked (|ρ| ≤ 0.07).
  - The plausible edge is event awareness: earnings, deal-pinned names (K, EA) and news gaps.
- **The planned test** is short and isolated: AI-on vs AI-off on a recent window after the model's training cutoff. For it to mean anything:
  - Web and news search must be off, or hard-limited to before each decision date. Otherwise future news leaks in.
  - Use the same doc and tie order in both arms, and run the 50–74 band both ways (approve and reject), since a human approver cannot be replayed.
  - Log a score for every passer, including those the book had no slot for.
- **What would be meaningful:**
  - **Not the NAV difference.** A 3–6-month window gives 25–40 round trips, and tie order alone moves NAV by ±3–8 pp. A gap below
    about 8 pp says nothing.
  - **Meaningful: replay every scored passer with the same bracket.** Approved passers must beat rejected ones by about 1.5 pp a trade
    or more, with errors clustered by date, and the sign must hold in both halves of the window.
    - Compare approved with rejected rather than AI-on NAV with AI-off NAV. A gate that just cuts exposure looks good in a falling tape.
  - **The bar to matter:** to close W7's gap to SPY, the gate would have to lift expectancy by about 1.2–1.8 pp a trade (an 18–29 pp gap
    over 128 trades at 12.5% each). That is more than any edge the technical signal showed in any window.
  - A real but smaller gain improves the book without changing the verdict.
  - A null result means the gate is cost (API spend plus approval latency). The exception is if it demonstrably rejects event-risk
    losers more often than winners, in which case keep it as a veto only.

## 7. Recommendation for `swing-paper`

**Run it, as a measurement instrument on a paper account, not as a step toward real money.**
- **The expectation:** nothing in this campaign supports live capital. The expected result is roughly RSP minus 3–5% a year, with a
  gate that trails every V-shaped rebound.
- **Confirm the brokerage first:** it must be a paper Alpaca account that is not alpaca-main's. The setup script checks this.
- **The wheel lane:** doc 204 also carries it, and nothing here measures it; it is inert in backtests.

**What to watch, weekly:**
1. **Every logged ENTER against the order actually sent and filled.** Look for clamps and silent drops after same-session exits, the
   live analogue of bug 1. I have not verified the live budget path.
2. **Stop and target fills against their levels,** especially opens that gap through. The Alpaca path has no native brackets.
3. **Gate flips, and every entry within 3 sessions of a re-open.** This is the recurring losing cohort.
4. **Zombies:**
   - defensive ETFs still held after a re-open;
   - any holding that has sat within ±3% of its fill for 40 sessions or more (the ROP, DTE and XLP pattern);
   - deal-pinned names.
5. **`unknown`-sector names held beside a same-sector name.**
6. **Earnings-block refusals, and the AI score for every candidate**, including rejects and no-slot names, so the gate can be
   calibrated against outcomes.
7. **Realised cost per fill against the engine's ~20 bps,** and turnover (the backtests ran at 9–13× equity a year).
8. **A monthly scorecard against SPY-TR, RSP and the backtest band.**

**Decision points (my suggestion):**
- Do not read anything before about 6 months or 50 closed trades.
- At 12 months, if it trails RSP (not only SPY), stop it.
- Re-read this report once FULL (757712) finishes, and fill in its row.

**Nothing in this report changes the strategy. Doc 204 and every other row are untouched.**
## Addendum — "is it the LLM, or did the port change the logic?" (harness decomposition, 2026-09-25)

The research harness (`scratchpad/replication/engine.py`) reproduces the repo's own backtester trade-for-trade (291/291). I ran it on the same windows with yfinance bars through 2026-06-10, so W7 is truncated. Columns:
- **A** = the repo's `backtester.py` as published: 0 cost, today's S&P list, its 16-name sector map, close-based exits, sizing off starting capital, SPY buffer 1.02, no bear mode.
- **D0** = the repo's LIVE `paper_trader.py` rules (the ones the friend trades and the port implements), still at 0 cost on today's list.
- **D** = live rules + point-in-time membership + 23 bps.

| Window | A repo backtester | +PIT | +23 bps | D0 live rules, 0 cost | D live rules, PIT, 23 bps | Our engine | SPY (price) |
|---|---|---|---|---|---|---|---|
| W1 | +5.99 | +3.19 | +0.68 | +0.09 | −1.78 | +6.23 | +9.40 |
| W2 | −10.53 | −9.69 | −10.63 | −8.64 | −4.52 | −15.31 | −24.27 |
| W3 | +8.04 | +5.28 | +2.51 | −0.31 | −6.56 | −9.09 | +26.32 |
| W4 | +10.14 | +5.72 | +3.05 | +17.68 | +11.47 | +5.71 | +17.30 |
| W5 | +12.26 | +9.58 | +7.15 | +4.16 | −4.32 | −4.98 | +13.33 |
| W6 | +0.18 | −2.68 | −4.34 | +3.10 | +1.74 | −0.64 | +4.40 |
| W7 (to 06-10) | +13.47 | +13.47 | +10.27 | +0.80 | −10.69 | −6.15 (to 09-18) | +17.87 |
| **FULL 2021-07..2026-06** | **+41.42** | +48.45 | +25.82 | **+14.29** | **−2.26** | pending | **+77.12** |

**Ablation (FULL, live rules at 0 bps = +14.29%; each row switches ONE rule back to the backtester's setting):**

| Rule switched back | Result |
|---|---|
| Sector map → the backtester's 16-name map (the cap almost never binds) | **+36.33%** |
| Exits → close-based, instead of the live bracket | +25.63% |
| Sizing → off starting capital | +25.10% |
| Bear mode → off | +21.25% |
| Max positions → unlimited | +14.29% (no effect) |
| SPY buffer → 1.02 | +12.25% |

**Reading:**
- **(1) Not the LLM.** The repo's +41% five-year backtest never used it.
- **(2) The repo's own backtester does not test the rules its live trader runs.** On identical data and costs the live rules make +14% vs +41%. Most of that gap is the sector cap: live looks up every name's sector, while the backtester's map covers 16 names. The bracket exits, equity sizing and bear mode also cost return.
  - The port implements the LIVE rules, as asked ("use the exact code"), so our numbers should be compared with D0/D, not with A.
- **(3) Realistic assumptions cost the rest.** 23 bps costs and point-in-time membership take the live rules from +14% to about −2% over five years. Our engine windows sit within the tie-order noise of D, except W2, where the engine is 10.8 pp worse; worth a look.
- **(4) Being positive in a rising market is not beating it.** The live rules at 0 cost made +14% while SPY made +77%.
- **(5) The friend's track record doesn't fit the repo's history.** The repo is 5 months old (first commit 2026-04-26) and its CLAUDE.md says all trading runs `paper=True`, so a year of live results cannot come from this code. Ask for the Alpaca equity history to compare against SPY on the same dates.

## Addendum — the friend's repo backtester run exactly as published (2026-09-25)

A fresh clone at c2afa71, unmodified. It ran in a throwaway venv with matplotlib and tabulate added, and MPLBACKEND=Agg.

| Run | Strategy | SPY-TR | RSP | Max DD (SPY) |
|---|---|---|---|---|
| `backtester.py` (2021-09-27 → 2026-09-25) | **+58.60%** (338 trades, 50.6% won) | **+86.32%** | +48.70% | −12.21% (−24.50%) |
| `--walk-forward` train 2021-01 → 2023-12 | **+2.06%** (107 trades) | **+34.74%** | +31.95% | −14.55% |
| `--walk-forward` test 2024-01 → 2026-09-24 | **+17.72%** (152 trades) | **+67.63%** | +39.29% | −12.46% |

- **The repo's own best-case test trails SPY**: −27.7 pp over 5 years, −32.7 pp in the train period and −49.9 pp in the test period.
- **The main run only starts trading on 2023-01-24.** It needs about 200 bars to form the SMA200, and the regime gate kept it out of 2022. All of its +58.6% comes after that date, while SPY made +102% over the same stretch.
- **The walk-forward verdict ("✅ STRATEGY GENERALIZES") only compares the strategy's train Sharpe with its test Sharpe.** It never compares either half with the market, and both halves lose to SPY and to RSP.
- **This is still the repo's best case.** It charges zero costs on 338 trades, uses today's S&P list for all five years, and does not test the live paper_trader rules the friend trades: the sector cap rarely binds, and exits happen at the close with no bracket.

## Addendum: why W2's engine result (−15.3%) and the harness result (−4.5%) differ (see analysis/W2-engine-vs-harness.md)

One simulator reproduces both runs. With every setting matched to the harness it lands exactly on −4.52%; with every setting matched to the engine it lands on −15.39%, and all 31 engine trades match. A Shapley split across 512 on/off combinations attributes the gap as follows:
- **Market data: −6.5 pp. This is the engine being unfaithful.**
  - **The backtest lane fetches IEX bars with split-only adjustment** (`strategy_swing.py:698`, `backtest_bars.py:51`). paper_trader.py and our own live path use SIP bars, fully adjusted.
  - **IEX volume is a sliver of consolidated volume**, so the "volume > 20-day average" filter picks different names. It agrees with consolidated volume on only 77.6% of name-days, which costs −4.3 pp. IEX closes cost a further −1.8 pp and IEX open/high/low fills −0.45 pp.
  - **Fix before trusting any engine number:** fetch the lane's own backtest bars from the SIP feed with full adjustment, and check that fill prices stay consistent.
- **Harness simplifications: −3.8 pp.** The harness never trades ETFs in bear mode (a defect in my decompose.py: the point-in-time mask also blocks the defensive book), and its universe holds only survivors. The harness's D-column figures are therefore somewhat optimistic in bear windows.
- **Port logic, net −0.6 pp:**
  - "Unknown"-sector names for removed members escape the sector cap (−0.73).
  - Same-session buys are added to the sector set, which paper_trader does not do (−0.41; a deliberate port fix; keep or revert as a ruling).
  - The missing pending-sell credit clamps or drops entries (+0.50 here, by luck).
- **A replay faithful to paper_trader gives about −7.7%** (−8.0% at a flat 23 bps). That still beats SPY's −22% in this bear window.

## Addendum: AI conviction gate test (2026-08-03 → 2026-09-18; see analysis/AI-test.md)

| Run | Return | SPY-TR (+2.08%) | Model calls |
|---|---|---|---|
| AI off (243642) | −1.58% | −3.7 pp | 0 |
| AI on, auto-enter ≥70 (175059, gpt-5.4-mini, point-in-time inputs) | −0.69% | −2.8 pp | 60 (59 scored) |

**Scores**
- They ranged from 56 to 82 and never fell below 50, so the model rejected nothing outright.
- 19 candidates were auto-entered and 40 fell in the 50–69 approval band.

**Replay of every scored candidate**
- Approved: −0.44% (CI −3.28 to +2.63).
- Band: −1.70% (CI −3.20 to −0.11).
- The difference between them is +1.26 pp with p = 0.43, which is not significant.
- The correlation between score and outcome is ρ = +0.04 (p = 0.74).

**Scores were unstable for the same name:** ED scored 58, 62 and 80 within six sessions and lost about 5% each time.

**Why AI-on finished higher:** its +0.9 pp advantage comes from path luck. Skipping band names on Aug 3–5 left slots free that two gap-up winners later filled.

**Verdict:** on these weeks the score adds no detectable information. A real test would need:
- scores for every technical passer, not only those that reach a free slot;
- 600–1,000 candidates spanning more than one market regime;
- collection going forward in paper trading, after the model's training cutoff;
- a re-scoring noise check;
- a non-AI ranking as the baseline.
