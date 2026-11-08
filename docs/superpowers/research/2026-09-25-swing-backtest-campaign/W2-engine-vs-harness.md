# W2 bear 2022: IntelliStock engine vs research harness (10.8 pp apart)

**Engine:** backtest 588533 (swing-paper, doc 204), **−15.31%**, 31 round trips.
**Harness:** `decompose.py` row `D_live_rules_PIT_23bps`, **−4.52%**, 19 round trips.
Window: 2022-01-03 → 2022-10-14. Gap: **−10.79 pp**.

**Method.** `swing_bt/w2h/run2.py` is a copy of the harness `engine.run(**LIVE)` with nine switches. Each switch moves one assumption to the engine's.
- **Harness end.** With every switch off, on yfinance bars, it reproduces **−4.52% exactly**.
- **Engine end.** With every switch on, on the lane's own IEX bars (`w2/engine_bars.pkl`) plus the engine's membership, sector map and CBOE VIX, it gives **−15.39%**. All **31 trades match**: same symbol, entry session, exit session and reason.
- **Residual.** The 0.08 pp left over comes from three share counts: HOLX, ARE, and ESS on 02-10.
- **Attribution.** I ran all 2⁹ = 512 switch combinations and computed Shapley values. The table also shows two one-at-a-time bounds: switching the factor on from the harness end, and switching it off from the engine end.

## Answer: causes, ranked

| # | Cause (switch) | Shapley pp | One-at-a-time range | Ruling |
|---|---|---|---|---|
| 1 | **Volume source (Dv).** IEX-only volume in `volume > vol_avg20` | **−4.27** | −3.3 … −5.1 | (b) data. The engine is the side that departs from live (§3). |
| 2 | **ETFs blocked in the harness (X).** No QQQ/SPY, and no defensive book in bear mode | **−2.89** | −2.8 … −3.5 | (c) harness simplification |
| 3 | **Price source for indicators (Dp).** IEX last-trade closes, split-only adjustment | **−1.82** | −1.4 … −1.8 | (b) data, engine-side |
| 4 | **Harness universe is survivor-only (U).** 70 of 505 PIT members are missing | **−0.90** | −0.7 … −0.9 | (c) harness simplification |
| 5 | **Engine reads removed members' sector as "unknown" (K).** They escape the 1-per-sector cap | **−0.73** | 0 … −1.76 | **(a) port bug** |
| 6 | **Execution prices (De).** IEX open/high/low for fills and bracket touches | −0.45 | +0.2 … −0.45 | (b) data |
| 7 | **"Fix 4" (S).** The sector set grows with each same-session buy | −0.41 | **+1.6 … −3.2** | **(a) port deviation** |
| 8 | **Engine settled-cash clamp (C).** No pending-sell credit, plus a 5% T+1 haircut | **+0.50** | +0.6 … +0.8 | **(a) port/config bug**; luck in the engine's favour |
| 9 | **Fill and cost mechanics (F).** Half-spread model vs flat 23 bps; bracket rounding | +0.09 | +0.0 … +0.25 | none. Both come to about 46 bps per round trip. |
| 10 | **Regime inputs.** SPY SMA200, VIX source, bear counter | **0.00** | 0 | (b), no P&L effect (§2.10) |
| 11 | **Universe order / tie-break** | **0.00** | 0 | (d), never binding (§2.11) |
| | **Total** | **−10.87** | | engine end −15.39 vs actual −15.31 |

**By class:**
- Data (IEX feed, split-only adjustment): **−6.5 pp**.
- Harness simplifications: **−3.8 pp**.
- Port-logic deviations: **−0.6 pp net** (K −0.7, S −0.4, C +0.5).
- Costs: **≈ 0**.

**A faithful replay of paper_trader.py** means SIP-like bars, ETFs allowed, the full PIT universe with PIT sectors, no fix 4, and pending-sell credit. It returns **−7.7%** with engine fills and −8.0% with flat 23 bps. So both published numbers are off:
- The harness is about **3.3 pp too good**: no losing ETF book, and a survivor-only universe.
- The engine is about **7.6 pp too bad**: the IEX data and the port deviations.

The 512 cells span −17.8% to −3.5%. This entry is a noisy draw: a loss is about $700 per trade, and swapping about 30 trades moves the result by several pp. Don't read the sign of Dv as structural.

## 1. Side-by-side trade diff (P&L in $, after costs)

**Shared: 8 trades, same entry session.** Engine total −3,988; harness total −4,196; net **+$208 to the engine**.

| Trade | Engine exit | Harness exit | Engine P&L | Harness P&L | What differs |
|---|---|---|---|---|---|
| ALB 01-04 | 01-10 stop | 01-10 stop | −929 | −913 | IEX open gapped +1.10% vs SIP +0.99% |
| GOOGL 01-11 | 01-21 stop | 01-21 stop | −732 | −738 | — |
| FTNT 01-11 | **01-18 gap stop** | **01-14 intraday stop** | −523 | −541 | The IEX low on 01-14 held 0.16% above the stop, while the SIP low went 0.68% through it (De) |
| EIX 01-11 | 01-24 stop | 01-24 stop | −762 | −792 | — |
| MRSH 01-11 | 01-24 stop | 01-24 stop | −782 | −820 | — |
| AMD 02-03 | 02-10 close target | 02-10 close target | +758 | +771 | — |
| AME 02-10 | 02-17 stop | 02-17 stop | −617 | −620 | — |
| ESS 02-10 | 02-24 stop | 02-24 stop | −401 | −543 | Engine bought **24 shares, not a full slot** (cash clamp, C) |

**Engine only: 23 trades, −$11,323.**

| Trade | Exit | P&L | Why the harness doesn't have it |
|---|---|---|---|
| FRC 01-07 | 01-14 stop | −829 | Removed from the index since, so absent from the harness (U). Engine sector is "unknown"; the true sector is financials (K). |
| TT 01-07 | 01-14 stop | −773 | Passes only on IEX closes (Dp) |
| REGN 01-10 | 03-14 target | +966 | IEX volume 1.32× vs SIP 0.78× (Dv). The harness buys it one session later. |
| HOLX 01-11 | 01-18 gap stop | −821 | U + K: healthcare, bought next to REGN |
| ARE 01-18 | 01-21 stop | −708 | Passes on both. The harness's one free slot went to AMD (cascade). |
| QQQ 01-18 | 01-21 stop | −574 | ETF blocked in the harness (X) |
| VRSK 01-19 | 01-26 stop | −759 | Passes on both. The harness book was full (cascade). |
| ESS / NRG 02-03 | 02-08 / 02-22 stop | −382 / −543 | Pass only on IEX closes (Dp) |
| YUM 02-03 | 03-04 stop | −674 | Dv (1.03× vs 0.82×) |
| ITW 02-03 | 02-08 close stop | −753 | Passes on both. The harness's 6 slots filled first with AMD, AON, ANET, AJG, BEN and DOV (cascade). |
| DRE / INFO 02-03 | 02-11 / 02-08 stop | −742 / −581 | U + K: real estate and industrials read as "unknown" |
| ACN 02-10 | 02-11 stop | −465 | Dv (1.33× vs 0.83×) |
| CFG 03-30 | 03-31 stop | −689 | Needs both IEX closes and IEX volume |
| XLP 03-03, XLU 04-29, XLV 04-29, GLD 05-05, XLP 05-10, XLP 05-31, XLU 06-28, XLU 09-07 | mixed | +380 −656 −596 −815 −752 −644 +821 −732 | Defensive ETFs blocked (X). Four of them also pass only on IEX volume or closes. |

**Harness only: 11 trades, −$329.**

| Trade | Exit | P&L | Why the engine doesn't have it |
|---|---|---|---|
| CNC / DHR 01-11 | 02-08 target / 01-20 stop | +1,126 / −862 | Healthcare was already held via REGN, bought a session earlier on IEX volume (Dv cascade) |
| REGN 01-11 | 03-16 target | +1,011 | The engine took the same name on 01-10 |
| GOOG 01-11 | 01-21 stop | −761 | **Fix 4:** GOOGL was bought in the same session (S) |
| ANET 02-03 | 02-23 stop | −493 | **Fix 4:** AMD was bought in the same session (S) |
| IR 02-10 | 02-18 stop | −581 | **Fix 4:** AME was bought in the same session (S) |
| AMD 01-18, DOV 02-03 | 01-19 / 02-04 stop | −579 / −669 | Fail on IEX volume (0.79×, 0.72×) (Dv) |
| AJG / AON 02-03 | 03-29 close target / 03-15 target | +1,135 / +1,039 | Fail on IEX volume (0.94×, 0.82×) (Dv) |
| BEN 02-03 | 02-14 stop | −695 | Fails on IEX closes (Dp) |

**Direct tally, engine minus harness.** It reconciles the gap without any Shapley weighting:

| Component | pp |
|---|---|
| ETFs | −3.57 |
| Removed names | −2.97 |
| Data-driven signal swaps | −3.80 |
| Slot and sector cascades | −2.48 |
| Fix 4 (the harness's GOOG, ANET and IR, which lost) | +1.84 |
| Shared-trade prices | +0.21 |
| **Total** | **−10.78** |

## 2. Root causes

**2.1 Dv — IEX-only volume (−4.3 pp, data).**
- IEX prints are a median **4.7%** of consolidated volume (p10–p90: 2.5–7.8%). A 20-day-average test on that slice is a different, noisier filter.
- On harness names across the 15 ACTIVE signal days (6,441 name-days), `vol > avg20` agrees between IEX and SIP on only **77.6%** of name-days. The price conditions agree 95.7–99.1%.
- Of the 139 name-days that pass either version, **only 73 pass both** (Jaccard 0.53). The volume source alone flips 52 passes; the price source alone flips 14.
- In this window, IEX volume dropped four winners from the harness set: AJG, AON, and CNC via the REGN cascade.

**2.2 X — the harness never trades ETFs (−2.9 pp, harness simplification).**
- `decompose.py:32` builds `M = E.pit_mask(...)` and passes `allow=M` to the LIVE row (`decompose.py:36`).
- `engine.py:125-126` applies `ES & allow` to every scan, the bear-mode scan included. SPY, QQQ, XLP, XLU, XLV, GLD and SHY are never members, so `bear_mode=True` does nothing and QQQ can't be bought.
- `analysis.py:40-44` `allow_mask` whitelists these ETFs but is unused here.
- The engine is faithful to paper_trader.py: SPY and QQQ are in the live universe (paper_trader.py:50-53), and bear mode scans the defensive list (609-621).
- On yfinance bars, allowing the ETFs adds 5 ETF trades (−$2,788) and takes the harness to −7.31%.

**2.3 Dp — IEX last-trade closes, split-only adjustment (−1.8 pp, data).**
- The log ratio of IEX close to SIP-adjusted close moves a median 3.7 bps a day (p90 12 bps).
- The engine also skips dividend adjustment (`adjustment=split`). That is enough to flip marginal conditions: RSI rising 5 times, MACD up 4, RSI < 50 3, ADX 3, above SMA200 2.
- Result: TT, ESS, NRG, CFG in; BEN out.

**2.4 U — survivor-only harness universe (−0.9 pp, harness simplification).**
- The harness's bars cover only today's `sp500_symbols.py`. Of the 505 members at 2022-01-03, 70 are missing, including FRC, DRE, INFO, HOLX, WBA and ANSS. AVB, CTRA, EA and EQR are missing too, because their yfinance fetch failed.
- The PIT mask agrees with the engine's `SwingIndexMembership` on every name the harness has (0 mismatched cells).

**2.5 K — "unknown" sector for removed members (−0.7 pp Shapley, −1.8 pp inside the engine) — PORT BUG.**
- In the engine's map, 31 of this window's members read "unknown": 29 removed or renamed names, plus BK and CTRA. SwingSectorMap has 41 "unknown" rows in all. In live, ST's `get_symbol_sector` would have returned FRC → financials, HOLX → healthcare, DRE → real estate, INFO → industrials in 2022.
- Because "unknown" is exempt, the engine held FRC next to MRSH, HOLX next to REGN, DRE next to ESS, and INFO next to ITW.

**2.6 De — IEX open/high/low (−0.45 pp, data).**
- The median daily range is 2.34% on IEX vs 2.53% on SIP, so bracket legs trigger less often. FTNT is the example: it slid to a later gap stop.
- Entry gaps differ by 0.0–0.6 pp.

**2.7 S — fix 4 (−0.4 pp net, and its sign depends on context) — PORT DEVIATION.**
- paper_trader.py fixes `active` once (596), checks `sector_conflict(symbol, active)` (640), and never adds a new buy to it (733-746). Live could therefore buy GOOGL and GOOG, or CNC, DHR and REGN, in one run. The harness does this.
- The port adds each buy to the set (`active.add(symbol)  # fix 4`), which blocked GOOG, ANET, IR, ORCL and others.
- Here it saved the engine $1,835 of direct losses, but the freed slots went to other losers. The effect runs from +1.6 pp to −3.2 pp depending on the other switches.

**2.8 C — settled-cash clamp (+0.5 pp) — PORT/CONFIG BUG.**
- On 02-10 the strategy credited AMD's exit at the close (the F1 comment at strategy_swing.py:820-822, mirroring paper_trader.py:585).
- The emulator funds buys from settled cash only (portfolio_emulator.py:504, with `SETTLED_SELL_PROCEEDS_FRACTION = 0.95` at line 124). Doc 204 never sets `backtest_credit_pending_sell_proceeds` (resolved at broker.py:3647-3672 and 18995; not in the strategy_swing schema config at line 1).
- MLM was **silently dropped** and ESS cut from 35 to 24 shares. Live Alpaca has margin buying power, so it would have filled both.
- The P&L effect was luck: +$780 in the W2 audit, +0.50 pp by Shapley.

**2.9 F — costs and bracket mechanics (+0.1 pp).**
- Both models charge about 46 bps per round trip. The engine had $1,566 of drag against $1,583 for 62 fills at 23 bps.
- Both fill a gap-through at the open and assume stop-first when a bar spans both legs.
- The only differences:
  - The harness allows a target gap on the entry day.
  - The harness doesn't round the brackets.
  - The harness charges 23 bps on target (limit) fills, where the engine charges no spread.

**2.10 Regime inputs (0.0 pp).**
- **SPY SMA200.** SPY close/SMA200 reads **+0.54 pp higher** on yfinance-adjusted bars than on raw IEX. So the three flip sessions clear the 1.03 line by 0.6–0.9% in the harness against 0.05–0.27% in the engine.
- **The one extra flip.** The offset makes 03-31 ACTIVE in the harness only, which delays bear mode by one session (04-14 vs 04-13). Nothing passed on 03-31, and no ETF signal fell on 04-13.
- **VIX.** CBOE (SwingMacroDaily) matches yfinance within 0.05 on every trading day. The CBOE feed has four holiday rows (05-30, 06-20, 07-04, 09-05) that `vix_before` picks up; all read above 25 while SPY was already blocked.
- **Bear counter.** It counts per session in both, and both see 15 ACTIVE and 183 blocked sessions.

**2.11 Tie-order (0.0 pp).**
- Both scan `sp500_symbols.py` order, and the two lists are identical: 503 names.
- The engine appends former members alphabetically by ticker, whereas live placed them by company name. That never bound anything: the removed names took slots only on 01-07, 01-11 and 02-03, and on each of those sessions every non-conflicting pass was entered.

## 3. Port bugs and deviations (deployed code = origin/main e64d9c57; the run started after it)

1. **Backtest data contract ≠ live** (the source of the −6.5 pp data block).
   - ST's live bars: `feed=DataFeed.SIP`, `adjustment=Adjustment.ALL` (swing-trader/market_data.py:8-14, 116-117). The port's live path does the same (backend/swing_trader/market_data.py:123-124).
   - The backtest lane reads `feed = cfg.get("alpaca_data_feed") or "iex"` (backend/strategies/strategy_swing.py:698) with `ADJUSTMENT = "split"` (backend/swing_trader/backtest_bars.py:51). The run log line 79 confirms "(iex, split)".
   - ST's own docstring warns that unadjusted bars "silently shift every RSI/SMA/ATR value".
2. **Fix 4:** `active.add(symbol)` at backend/strategies/strategy_swing.py:958 (live path :1534) against paper_trader.py:596/640/733-746.
3. **Removed-member sectors → "unknown" → cap bypass.**
   - backend/swing_trader/refdata_build.py:396-397 returns no row when today's yfinance has no sector for a removed name.
   - backend/swing_trader/refdata_sync.py:213-219 then stores an "unknown" retry marker.
   - backend/swing_trader/sectors.py:41-42 falls back to "unknown".
   - The exemption itself is ST's verbatim rule (signals.py:69-70, paper_trader.py:335-336).
4. **Pending-sell credit is missing on doc 204, and the resize or drop is silent.**
   - The strategy credits exits at strategy_swing.py:820-822.
   - The engine funds buys from settled cash at portfolio_emulator.py:504.
   - The flag is read by broker.py:3647-3672/18995 but is absent from doc 204 and from the schema defaults at strategy_swing.py:1.

## 4. Files

All scratch files are in `swing_bt/w2h/`:
- `harness_w2.py` (harness D trade dump) and `run2.py` (the switchable harness copy).
- `engine_repl.py` (the 31-trade engine match) and `factorial2.py` / `factorial2.json` (the 512 cells).
- `diff.py`, `census.py`, `shared.py` and `xonly.py`.
- `vix_engine.json`, read read-only from SwingMacroDaily.

No code, config or DB rows were changed.
