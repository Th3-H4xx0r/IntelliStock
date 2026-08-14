# SNDK forensic — bt 523085 (`/tmp/bt523085.log`), window 2026-01-01 .. 2026-02-28

**Scope.** SNDK only, exhaustively. Every claim cites a line in `/tmp/bt523085.log`
(`Lnnnnn`) or a line in `backend/` (`file:line`). **No backtest was run for this
document.** All counterfactual arithmetic states its assumptions inline; where the
log cannot support a claim that is said explicitly.

## 0. Run outcome

| | | source |
|---|---|---|
| Initial value | $6,000.00 | L40295 |
| Final value | $6,366.10 | L40296 |
| Run P&L | **+$366.10 (+6.10%)** | L40297 |
| SNDK price move, full window | $237.33 -> $631.54 = **+166.10%** | L40338 |
| SNDK P&L booked by the run | **+$18.84 (+2.28%)** | L40325 |
| SNDK final position | 1.3364 sh @ $635.94 = $849.85 | L40308 |
| Only SNDK fill in the run | 2026-02-04, 1.336363 sh @ $617.420677 = $825.10 | L24977 |
| Whole-run buy funnel | **16 `FILL BUY` vs 67 `SKIP BUY`** | grep |

### 0.1 Honesty note on the +166.10% headline

The run could not have captured +166.10%; comparing it to what the run earned would be
comparing a full-window number to a partial-window opportunity. SNDK is *discovered*
2026-01-01 (L2091, `20d=+15.6%, 60d=+95.9%`) but scores 0.585 and is not a buy on
2026-01-05 (L4048). The earliest **actionable** signal is 2026-01-07 via the backfill
queue at $328.19 (L6012); the earliest signal reaching the **broker buy path** is
2026-01-12 at $388.455 (L8989). Against the final mark $635.94 (L40308) the capturable
moves are **+93.8%** (from 01-07) and **+63.7%** (from 01-12). Everything below is
measured against those.

---

## 1. Timestamped ledger

### 1.1 Price / intent track (`[BROKER] SNDK @ <ts> ($px)`)

| Date | Price | Broker action | action_intent | Line |
|---|---|---|---|---|
| 2026-01-01 | $237.33 | hold | `hold` | L2722 |
| 2026-01-02 | $262.08 | hold | `hold` | L3589 |
| 2026-01-05 | $270.54 | hold | `hold` | L4504 |
| 2026-01-06 | $328.19 | hold | `hold` | L5432 |
| 2026-01-07 | $335.9 | hold | `hold` | L6357 |
| 2026-01-08 | $333.19 | hold | `hold` | L7324 |
| 2026-01-09 | $363.01 | hold | `hold` | L8252 |
| 2026-01-12 | $388.455 | buy | `momentum_watchlist_buy` | L8989 |
| 2026-01-13 | $390.49 | buy | `momentum_watchlist_buy` | L9915 |
| 2026-01-14 | $393.06 | buy | `momentum_watchlist_buy` | L10833 |
| 2026-01-15 | $418.72 | buy | `backfill_queue_buy` | L11765 |
| 2026-01-16 | $405.47 | buy | `momentum_watchlist_rotation` | L12716 |
| 2026-01-19 | $413.55 | buy | `momentum_watchlist_buy` | L13645 |
| 2026-01-20 | $446.96 | hold | `deferred_unfunded_buy` | L14753 |
| 2026-01-21 | $468.84 | buy | `momentum_watchlist_buy` | L15473 |
| 2026-01-29 | $533.41 | buy | `momentum_watchlist_buy` | L21043 |
| 2026-02-03 | $655.38 | buy | `backfill_queue_buy` | L23825 |
| 2026-02-04 | $644.9 | buy | `backfill_queue_buy` | L24718 |
| 2026-02-05 | $600.77 | hold | `hold` | L25764 |
| 2026-02-06 | $601.83 | hold | `hold` | L26608 |
| 2026-02-09 | $584.5 | hold | `hold` | L27504 |
| 2026-02-10 | $558.0 | hold | `hold` | L28395 |
| 2026-02-11 | $596.85 | hold | `hold` | L29298 |
| 2026-02-12 | $651.97 | hold | `hold` | L30195 |
| 2026-02-13 | $590.73 | hold | `hold` | L31072 |
| 2026-02-16 | $626.79 | hold | `hold` | L31993 |
| 2026-02-17 | $601.46 | hold | `hold` | L32877 |
| 2026-02-18 | $595.505 | hold | `hold` | L33779 |
| 2026-02-19 | $606.65 | hold | `hold` | L34671 |
| 2026-02-20 | $638.4 | hold | `hold` | L35555 |
| 2026-02-23 | $683.19 | hold | `hold` | L36418 |
| 2026-02-24 | $664.485 | hold | `hold` | L37313 |
| 2026-02-25 | $645.43 | hold | `hold` | L38185 |
| 2026-02-26 | $636.61 | hold | `hold` | L39085 |
| 2026-02-27 | $641.26 | hold | `hold` | L39996 |

**Seven trading sessions have NO `[BROKER] SNDK @ ...` line at all:**
2026-01-22, 2026-01-23, 2026-01-26, 2026-01-27, 2026-01-28, 2026-01-30, 2026-02-02. On those bars SNDK never entered the broker's execution symbol
set (it was held at `Backfill queue BLOCKED (full_priority_blocked)` / `demoted to
queue-only hold` — L16252, L17179, L19106, L20025, L21852, L22815, L23605). **SNDK's
price on those seven sessions is therefore not recoverable from this log.** The
neighbours are $468.84 (01-21, L15473), $533.41 (01-29, L21043), $655.38 (02-03,
L23825). `backend/backtest_prices.csv` was deliberately NOT used: it is dated
2026-08-03 while this log is 2026-08-13, so it belongs to a different run.

### 1.2 Full event ledger (every SNDK line except the 187 repeated `Monitor decision`
and 82 `conviction_tier` / `Trade overlay LLM` / bar-cache lines)

| Date | Line | Event |
|---|---|---|
| 2026-01-01 | L2091 | Discovered stock (momentum): SNDK (20d=+15.6%, 60d=+95.9%) |
| 2026-01-01 | L2476 | Backtest symbol expansion: loaded 733 1Hour bars for SNDK |
| 2026-01-01 | L2722 | SNDK @ 2026-01-01 15:00:00 ($237.33): hold action_intent=hold (weighted scores from 1 strategies) |
| 2026-01-02 | L3589 | SNDK @ 2026-01-02 15:00:00 ($262.08): hold action_intent=hold (weighted scores from 1 strategies) |
| 2026-01-05 | L4048 | Momentum watchlist: watchlist=193, scored=105, top3=[('LITE', 0.681), ('SOC', 0.646), ('SNDK', 0.585)], held_momentum=1, new_buys=['LITE'] |
| 2026-01-05 | L4504 | SNDK @ 2026-01-05 15:00:00 ($270.54): hold action_intent=hold (weighted scores from 1 strategies) |
| 2026-01-06 | L5432 | SNDK @ 2026-01-06 15:00:00 ($328.19): hold action_intent=hold (weighted scores from 1 strategies) |
| 2026-01-07 | L5995 | V32 mw_buy extension-block: SNDK range +73.2% > 25% — no conviction bypass [bars=97] |
| 2026-01-07 | L5996 | Momentum watchlist: watchlist=228, scored=134, top3=[('SNDK', 0.96), ('VICR', 0.81), ('LITE', 0.742)], held_momentum=1, new_buys=['SNDK'] |
| 2026-01-07 | L6012 | Backfill queue ADD: SNDK (score=1.000, price=$328.19, source=direct) |
| 2026-01-07 | L6035 | V28 BFQ ALLOC=0: SNDK (queued 1d, score=1.000, signal_score=1.000) / priority_budget=$324 standard_budget=$324 min_pos=$100 headroom=0 budget_key=none |
| 2026-01-07 | L6357 | SNDK @ 2026-01-07 15:00:00 ($335.9): hold action_intent=hold (weighted scores from 1 strategies) |
| 2026-01-08 | L6950 | V32 mw_buy extension-block: SNDK range +75.3% > 25% — no conviction bypass [bars=98] |
| 2026-01-08 | L6951 | Momentum watchlist: watchlist=243, scored=144, top3=[('SNDK', 0.915), ('VICR', 0.79), ('FEIM', 0.667)], held_momentum=1, new_buys=['SNDK'] |
| 2026-01-08 | L6970 | Backfill queue REFRESH: SNDK (score=1.100, recurrence=1, source=direct) |
| 2026-01-08 | L6989 | V28 BFQ ALLOC=0: SNDK (queued 1d, score=1.000, signal_score=1.100) / priority_budget=$14 standard_budget=$14 min_pos=$100 headroom=3 budget_key=standard |
| 2026-01-08 | L7324 | SNDK @ 2026-01-08 15:00:00 ($333.19): hold action_intent=hold (weighted scores from 1 strategies) |
| 2026-01-09 | L7914 | Momentum watchlist: watchlist=258, scored=156, top3=[('GLUE', 1.036), ('SNDK', 0.78), ('ONDS', 0.748)], held_momentum=1, new_buys=['GLUE'] |
| 2026-01-09 | L7950 | V28 BFQ ALLOC=0: SNDK (queued 2d, score=1.000, signal_score=1.100) / priority_budget=$59 standard_budget=$59 min_pos=$100 headroom=5 budget_key=standard |
| 2026-01-09 | L8252 | SNDK @ 2026-01-09 15:00:00 ($363.01): hold action_intent=hold (weighted scores from 1 strategies) |
| 2026-01-12 | L8854 | Momentum watchlist: watchlist=270, scored=169, top3=[('SNDK', 0.951), ('GLUE', 0.919), ('VICR', 0.784)], held_momentum=1, new_buys=['SNDK'] |
| 2026-01-12 | L8859 | Executable stock slate: SNDK |
| 2026-01-12 | L8897 | V31.2 total-spend cap [CONCENTRATE]: funded 3 of 3 by conviction (SNDK@$860, KLAC@$860, UUUU@$860) out of $3,868; dropped 0 to the queue |
| 2026-01-12 | L8898 | Executable buys: KLAC, SNDK, UUUU |
| 2026-01-12 | L8989 | SNDK @ 2026-01-12 15:00:00 ($388.455): buy action_intent=momentum_watchlist_buy (weighted scores from 1 strategies) |
| 2026-01-12 | L8990 | SATELLITE OVERFLOW: SNDK raw=+1.700 >= 1.50 — funding $221 of room out of the core (floor-bounded) |
| 2026-01-12 | L8991 | SATELLITE CAP: SNDK trimmed $860 -> $221 to keep the core at target |
| 2026-01-12 | L8992 | TURNOVER BUDGET BYPASS: SNDK raw=+1.700 >= 1.50 — admitting a conviction buy through a 83% budget; the brake is for churn, not for the trade that matters |
| 2026-01-12 | L8993 | Buy gate inputs for SNDK: cash=$212.59 reserved=$0.00 floor=$120.00 effective_floor=$0.00 high_conv=True open_pos=6 cash_per_trade=$221.24 available=$212.59 cash_to_use=$212.59 → PASS |
| 2026-01-12 | L8994 | SKIP BUY SNDK — cash_to_use $212.59 < min $368 (allocated $221.24) |
| 2026-01-12 | L8995 | Gate skips reported back: SNDK (insufficient_cash) |
| 2026-01-13 | L9777 | Momentum watchlist: watchlist=284, scored=180, top3=[('SNDK', 1.013), ('VICR', 0.771), ('FEIM', 0.714)], held_momentum=1, new_buys=['SNDK'] |
| 2026-01-13 | L9782 | Executable stock slate: SNDK |
| 2026-01-13 | L9799 | Backfill queue REPLACE (broker-skipped): SNDK displaced U (score=1.700, priority=1) |
| 2026-01-13 | L9817 | V31.2 total-spend cap [CONCENTRATE]: funded 3 of 3 by conviction (LLY@$871, SNDK@$871, BRKR@$871) out of $3,921; dropped 0 to the queue |
| 2026-01-13 | L9819 | Executable buys: BRKR, LLY, SNDK |
| 2026-01-13 | L9915 | SNDK @ 2026-01-13 15:00:00 ($390.49): buy action_intent=momentum_watchlist_buy (weighted scores from 1 strategies) |
| 2026-01-13 | L9916 | SATELLITE OVERFLOW: SNDK raw=+1.700 >= 1.50 — funding $212 of room out of the core (floor-bounded) |
| 2026-01-13 | L9917 | SATELLITE CAP: SNDK trimmed $871 -> $212 to keep the core at target |
| 2026-01-13 | L9918 | TURNOVER BUDGET BYPASS: SNDK raw=+1.700 >= 1.50 — admitting a conviction buy through a 82% budget; the brake is for churn, not for the trade that matters |
| 2026-01-13 | L9919 | Buy gate inputs for SNDK: cash=$221.25 reserved=$0.00 floor=$120.00 effective_floor=$0.00 high_conv=True open_pos=6 cash_per_trade=$212.42 available=$221.25 cash_to_use=$212.42 → PASS |
| 2026-01-13 | L9920 | SKIP BUY SNDK — cash_to_use $212.42 < min $373 (allocated $212.42) |
| 2026-01-13 | L9921 | Gate skips reported back: SNDK (insufficient_cash) |
| 2026-01-14 | L10697 | Momentum watchlist: watchlist=296, scored=184, top3=[('SNDK', 0.946), ('VICR', 0.724), ('GLUE', 0.723)], held_momentum=1, new_buys=['SNDK'] |
| 2026-01-14 | L10702 | Executable stock slate: SNDK |
| 2026-01-14 | L10717 | Backfill queue REPLACE (broker-skipped): SNDK displaced MINV (score=1.700, priority=1) |
| 2026-01-14 | L10733 | V31.2 total-spend cap [CONCENTRATE]: funded 2 of 3 by conviction (GLUE@$871, SNDK@$871) out of $3,921; dropped 1 to the queue; skipped as not executable: NUVB(Nexus execution price floor: NUVB at $6.29 is below |
| 2026-01-14 | L10734 | Executable buys: GLUE, SNDK |
| 2026-01-14 | L10833 | SNDK @ 2026-01-14 15:00:00 ($393.06): buy action_intent=momentum_watchlist_buy (weighted scores from 1 strategies) |
| 2026-01-14 | L10834 | SATELLITE OVERFLOW: SNDK raw=+1.700 >= 1.50 — funding $211 of room out of the core (floor-bounded) |
| 2026-01-14 | L10835 | SATELLITE CAP: SNDK trimmed $871 -> $211 to keep the core at target |
| 2026-01-14 | L10836 | TURNOVER BUDGET BYPASS: SNDK raw=+1.700 >= 1.50 — admitting a conviction buy through a 82% budget; the brake is for churn, not for the trade that matters |
| 2026-01-14 | L10837 | Buy gate inputs for SNDK: cash=$221.27 reserved=$0.00 floor=$120.00 effective_floor=$0.00 high_conv=True open_pos=6 cash_per_trade=$210.94 available=$221.27 cash_to_use=$210.94 → PASS |
| 2026-01-14 | L10838 | SKIP BUY SNDK — cash_to_use $210.94 < min $373 (allocated $210.94) |
| 2026-01-14 | L10839 | Gate skips reported back: SNDK (insufficient_cash) |
| 2026-01-15 | L11610 | Momentum watchlist: watchlist=307, scored=193, top3=[('SNDK', 1.04), ('OSS', 0.816), ('VICR', 0.803)], held_momentum=1, new_buys=['SNDK'] |
| 2026-01-15 | L11612 | V28 ROTATION RESULT: fired=0/4 pairs=[] unfunded=7 (top: ATI(raw=1.800,age=0d), STX(raw=1.800,age=0d), WDC(raw=1.800,age=0d), NXPI(raw=1.700,age=0d), SNDK(raw=1.700,age=0d), TXN(raw=1.700,age=0d), SYNA(raw=1.65 |
| 2026-01-15 | L11628 | Backfill queue REPLACE: SNDK displaced AIFD (score=1.700, source=direct) |
| 2026-01-15 | L11633 | Backfill queue REFRESH (broker-skipped): SNDK (score=1.700, priority=1) |
| 2026-01-15 | L11635 | V28 BFQ DRAIN ENTRY: queue_size=60 headroom=7 cash=$324 priority_budget=$162 standard_budget=$162 min_pos=$100 top10=[VVX(score=2.200,age=3d), SON(score=2.000,age=7d), SNDK(score=1.800,age=0d), WDC(score=1.800, |
| 2026-01-15 | L11636 | Backfill queue BUY: SNDK (queued 1 bars, alloc=$100, score=1.700 HIGH-CONV) |
| 2026-01-15 | L11648 | V31.2 total-spend cap [CONCENTRATE]: funded 3 of 3 by conviction (ON@$890, WDC@$890, SNDK@$890) out of $4,006; dropped 0 to the queue |
| 2026-01-15 | L11654 | Executable buys: ON, SNDK, WDC |
| 2026-01-15 | L11765 | SNDK @ 2026-01-15 15:00:00 ($418.72): buy action_intent=backfill_queue_buy (weighted scores from 1 strategies) |
| 2026-01-15 | L11766 | SATELLITE OVERFLOW: SNDK raw=+1.700 >= 1.50 — funding $191 of room out of the core (floor-bounded) |
| 2026-01-15 | L11767 | SATELLITE CAP: SNDK trimmed $890 -> $191 to keep the core at target |
| 2026-01-15 | L11768 | TURNOVER BUDGET BYPASS: SNDK raw=+1.700 >= 1.50 — admitting a conviction buy through a 80% budget; the brake is for churn, not for the trade that matters |
| 2026-01-15 | L11769 | Buy gate inputs for SNDK: cash=$221.30 reserved=$0.00 floor=$120.00 effective_floor=$0.00 high_conv=True open_pos=6 cash_per_trade=$191.40 available=$221.30 cash_to_use=$191.40 → PASS |
| 2026-01-15 | L11770 | SKIP BUY SNDK — cash_to_use $191.40 < min $382 (allocated $191.40) |
| 2026-01-15 | L11771 | Gate skips reported back: SNDK (insufficient_cash) |
| 2026-01-16 | L12548 | Buy: SNDK (Direct momentum_breakout sentiment=+1 (raw=+1.000, 1 paths) ), USO (Direct trend_momentum sentiment=+1 (raw=+1.000, 1 paths) / B), ALHC (Direct general sentiment=+1 (raw=+1.000, 1 paths) / Base=+1.), |
| 2026-01-16 | L12554 | Momentum watchlist: watchlist=319, scored=201, top3=[('SNDK', 1.113), ('SKYT', 0.859), ('VICR', 0.805)], held_momentum=1, new_buys=['SNDK'] |
| 2026-01-16 | L12556 | V28 ROTATION RESULT: fired=0/4 pairs=[] unfunded=7 (top: DOW(raw=1.800,age=0d), FHN(raw=1.800,age=0d), LYB(raw=1.800,age=0d), SNDK(raw=1.700,age=0d), VVX(raw=1.300,age=4d), WELL(raw=1.300,age=4d), USO(raw=1.300 |
| 2026-01-16 | L12561 | Momentum rotation: sell VICR (score=0.805) → buy SNDK (score=1.113, $1081) |
| 2026-01-16 | L12578 | Backfill queue BLOCKED: SNDK (full_priority_blocked, score=1.113, source=direct) |
| 2026-01-16 | L12579 | Backfill queue BLOCKED (broker-skipped): SNDK (full_priority_blocked, priority=1) |
| 2026-01-16 | L12592 | V31.2 rotation cap: SNDK alloc $1081 → $952 (15% of portfolio $6350) [src=momentum_watchlist_rotation] |
| 2026-01-16 | L12594 | V31.2 total-spend cap [CONCENTRATE]: funded 2 of 2 by conviction (ASML@$889, SNDK@$952) out of $4,000; dropped 0 to the queue |
| 2026-01-16 | L12626 | Executable buys: ASML, SNDK |
| 2026-01-16 | L12716 | SNDK @ 2026-01-16 15:00:00 ($405.47): buy action_intent=momentum_watchlist_rotation (weighted scores from 1 strategies) |
| 2026-01-16 | L12717 | SATELLITE CAP: SNDK skipped — satellite at its design share ($-1,393 room); core would be squeezed below target |
| 2026-01-19 | L13496 | Buy: AMZN (Direct earnings sentiment=+1 (raw=+1.000, 2 paths) / Base=+1), SLGN (Direct trend_momentum sentiment=+1 (raw=+1.000, 1 paths) / B), SNDK (Direct momentum_breakout sentiment=+1 (raw=+1.000, 1 paths) ) |
| 2026-01-19 | L13503 | Momentum watchlist: watchlist=336, scored=218, top3=[('SNDK', 1.093), ('SKYT', 1.006), ('LUNR', 1.005)], held_momentum=0, new_buys=['SNDK', 'SKYT'] |
| 2026-01-19 | L13507 | Executable stock slate: SKYT, SNDK, AMZN, RRX, RUN |
| 2026-01-19 | L13535 | V31.2 total-spend cap [CONCENTRATE]: funded 4 of 9 by conviction (SKYT@$885, SNDK@$885, AMZN@$885, RRX@$885) out of $3,983; dropped 5 to the queue |
| 2026-01-19 | L13556 | Executable buys: AMZN, RRX, SKYT, SNDK |
| 2026-01-19 | L13645 | SNDK @ 2026-01-19 15:00:00 ($413.55): buy action_intent=momentum_watchlist_buy (weighted scores from 1 strategies) |
| 2026-01-19 | L13646 | SATELLITE OVERFLOW: SNDK raw=+1.700 >= 1.50 — funding $1,275 of room out of the core (floor-bounded) |
| 2026-01-19 | L13647 | TURNOVER BUDGET BYPASS: SNDK raw=+1.700 >= 1.50 — admitting a conviction buy through a 98% budget; the brake is for churn, not for the trade that matters |
| 2026-01-19 | L13648 | Buy gate inputs for SNDK: cash=$1299.21 reserved=$0.00 floor=$120.00 effective_floor=$0.00 high_conv=True open_pos=5 cash_per_trade=$885.03 available=$1299.21 cash_to_use=$885.03 → PASS |
| 2026-01-19 | L13649 | SKIP BUY SNDK — fundable $133.49 of cash_to_use $885.03 (orders already in flight this tick reserve the rest) < min $379 (allocated $885.03) |
| 2026-01-19 | L13650 | Gate skips reported back: SNDK (insufficient_cash) |
| 2026-01-20 | L14392 | Momentum watchlist: watchlist=346, scored=225, top3=[('SNDK', 1.093), ('SKYT', 1.006), ('LUNR', 1.005)], held_momentum=0, new_buys=['SNDK', 'SKYT'] |
| 2026-01-20 | L14397 | Queued due to cash / slot cap: SNDK, TPG, UBER |
| 2026-01-20 | L14408 | Backfill queue BLOCKED: SNDK (full_priority_blocked, score=1.700, source=direct) |
| 2026-01-20 | L14414 | Backfill queue BLOCKED (broker-skipped): SNDK (full_priority_blocked, priority=1) |
| 2026-01-20 | L14428 | Deferred unfunded buys demoted to hold: AMAT, ITA, SKYT, SNDK, TPG, USO, UUUU, V, VMC, XOM |
| 2026-01-20 | L14753 | SNDK @ 2026-01-20 15:00:00 ($446.96): hold action_intent=deferred_unfunded_buy (weighted scores from 1 strategies) |
| 2026-01-21 | L15331 | Momentum watchlist: watchlist=360, scored=236, top3=[('SNDK', 1.279), ('SKYT', 1.187), ('LUNR', 0.998)], held_momentum=0, new_buys=['SNDK', 'SKYT'] |
| 2026-01-21 | L15336 | Executable stock slate: SKYT, SNDK |
| 2026-01-21 | L15357 | V31.2 total-spend cap [CONCENTRATE]: funded 3 of 3 by conviction (SKYT@$879, SNDK@$879, RKLB@$879) out of $3,957; dropped 0 to the queue |
| 2026-01-21 | L15377 | Executable buys: RKLB, SKYT, SNDK |
| 2026-01-21 | L15473 | SNDK @ 2026-01-21 15:00:00 ($468.84): buy action_intent=momentum_watchlist_buy (weighted scores from 1 strategies) |
| 2026-01-21 | L15474 | SATELLITE OVERFLOW: SNDK raw=+1.700 >= 1.50 — funding $1,252 of room out of the core (floor-bounded) |
| 2026-01-21 | L15475 | TURNOVER BUDGET BYPASS: SNDK raw=+1.700 >= 1.50 — admitting a conviction buy through a 98% budget; the brake is for churn, not for the trade that matters |
| 2026-01-21 | L15476 | Buy gate inputs for SNDK: cash=$146.68 reserved=$0.00 floor=$120.00 effective_floor=$0.00 high_conv=True open_pos=5 cash_per_trade=$879.44 available=$146.68 cash_to_use=$146.68 → PASS |
| 2026-01-21 | L15477 | SKIP BUY SNDK — cash_to_use $146.68 < min $377 (allocated $879.44) |
| 2026-01-21 | L15478 | Gate skips reported back: SNDK (insufficient_cash) |
| 2026-01-22 | L16132 | Broker-skipped scoring net: 1 ticker(s) added: SNDK |
| 2026-01-22 | L16223 | Entry extension gate: SNDK recent runup +111.2% > 25% — buy blocked |
| 2026-01-22 | L16234 | V32 mw_buy extension-block: SNDK range +111.2% > 25% — no conviction bypass [bars=107] |
| 2026-01-22 | L16236 | Momentum watchlist: watchlist=371, scored=245, top3=[('SNDK', 1.422), ('VTYX', 1.146), ('SKYT', 1.086)], held_momentum=0, new_buys=['SNDK', 'VTYX'] |
| 2026-01-22 | L16252 | Backfill queue BLOCKED: SNDK (full_priority_blocked, score=1.300, source=direct) |
| 2026-01-22 | L16258 | Backfill queue BLOCKED (broker-skipped): SNDK (full_priority_blocked, priority=1) |
| 2026-01-23 | L17179 | V32 mw_buy extension-block: SNDK range +112.0% > 25% — no conviction bypass [bars=108] |
| 2026-01-23 | L17181 | Momentum watchlist: watchlist=383, scored=253, top3=[('SNDK', 1.371), ('VTYX', 1.04), ('SKYT', 1.008)], held_momentum=0, new_buys=['SNDK', 'VTYX'] |
| 2026-01-26 | L18156 | V32 mw_buy extension-block: SNDK range +112.0% > 25% — no conviction bypass [bars=109] |
| 2026-01-26 | L18158 | Momentum watchlist: watchlist=403, scored=274, top3=[('SNDK', 1.36), ('VTYX', 1.01), ('HL', 0.991)], held_momentum=0, new_buys=['SNDK', 'VTYX'] |
| 2026-01-27 | L19084 | Momentum watchlist: watchlist=417, scored=276, top3=[('SNDK', 1.21), ('AXTI', 0.937), ('SKYT', 0.933)], held_momentum=0, new_buys=['SNDK', 'AXTI'] |
| 2026-01-27 | L19087 | V28 ROTATION RESULT: fired=0/4 pairs=[] unfunded=10 (top: UBER(raw=1.663,age=12d), BMY(raw=1.800,age=0d), FDX(raw=1.800,age=0d), GILD(raw=1.800,age=0d), SQM(raw=1.800,age=0d), SNDK(raw=1.700,age=0d), VVX(raw=1. |
| 2026-01-27 | L19106 | Backfill queue BLOCKED: SNDK (full_priority_blocked, score=1.700, source=direct) |
| 2026-01-27 | L19126 | Deferred unfunded buys demoted to hold: SNDK, UHS, USO, UUUU, VMC |
| 2026-01-28 | L20000 | Momentum watchlist: watchlist=423, scored=280, top3=[('SNDK', 1.105), ('TE', 1.038), ('SKYT', 0.946)], held_momentum=0, new_buys=['SNDK', 'TE'] |
| 2026-01-28 | L20002 | V28 ROTATION RESULT: fired=0/4 pairs=[] unfunded=9 (top: AMZN(raw=1.800,age=0d), DHR(raw=1.800,age=0d), EBAY(raw=1.800,age=0d), ENVA(raw=1.800,age=0d), TPG(raw=1.800,age=0d), V(raw=1.800,age=0d), SNDK(raw=1.700 |
| 2026-01-28 | L20025 | Backfill queue BLOCKED: SNDK (full_priority_blocked, score=1.700, source=direct) |
| 2026-01-28 | L20053 | Deferred unfunded buys demoted to hold: SNDK, TPG, UHS, USO, UUUU, V, VMC, XOM |
| 2026-01-29 | L20918 | Momentum watchlist: watchlist=434, scored=283, top3=[('SNDK', 1.374), ('TE', 1.043), ('SKYT', 0.95)], held_momentum=0, new_buys=['SNDK', 'TE'] |
| 2026-01-29 | L20924 | Executable stock slate: SNDK |
| 2026-01-29 | L20957 | V31.2 total-spend cap [CONCENTRATE]: funded 3 of 3 by conviction (BA@$891, SNDK@$891, SQM@$891) out of $4,008; dropped 0 to the queue |
| 2026-01-29 | L20973 | Executable buys: BA, SNDK, SQM |
| 2026-01-29 | L20983 | Nexus executable buys: adding 1 ticker(s) to execution: SNDK |
| 2026-01-29 | L21043 | SNDK @ 2026-01-29 15:00:00 ($533.41): buy action_intent=momentum_watchlist_buy (weighted scores from 1 strategies) |
| 2026-01-29 | L21044 | SATELLITE OVERFLOW: SNDK raw=+1.700 >= 1.50 — funding $256 of room out of the core (floor-bounded) |
| 2026-01-29 | L21045 | SATELLITE CAP: SNDK trimmed $891 -> $256 to keep the core at target |
| 2026-01-29 | L21046 | TURNOVER BUDGET BYPASS: SNDK raw=+1.700 >= 1.50 — admitting a conviction buy through a 88% budget; the brake is for churn, not for the trade that matters |
| 2026-01-29 | L21047 | Buy gate inputs for SNDK: cash=$253.10 reserved=$0.00 floor=$120.00 effective_floor=$0.00 high_conv=True open_pos=6 cash_per_trade=$255.56 available=$253.10 cash_to_use=$253.10 → PASS |
| 2026-01-29 | L21048 | SKIP BUY SNDK — fundable $252.84 of cash_to_use $253.10 (orders already in flight this tick reserve the rest) < min $382 (allocated $255.56) |
| 2026-01-29 | L21049 | Gate skips reported back: SNDK (insufficient_cash) |
| 2026-01-30 | L21718 | Momentum ceiling block: SNDK 20d=+125.5% 60d=+171.7% (caps 20d=80%, 60d=200%) |
| 2026-01-30 | L21733 | Propagation scoring expansion: 40 ticker(s) added: AAPL, INCY, KLAC, PRTX, RBLX, RCL, SNDK, USARE, VSEC, WHR |
| 2026-01-30 | L21829 | Sector concentration detail: technology: kept SNDK, INCY, KLAC, VSEC, WDC, AAPL, TE, SMCI / demoted TNDM |
| 2026-01-30 | L21831 | Propagation expansion buys (promoted): AAPL, INCY, KLAC, RCL, SNDK, VSEC, WM, ARM, ADBE, AMZN, SMCI |
| 2026-01-30 | L21839 | Priority sizing order: watchlist=none / prop_exp=INCY, KLAC, SNDK, VSEC |
| 2026-01-30 | L21840 | Momentum watchlist: watchlist=444, scored=292, top3=[('SNDK', 1.476), ('SKYT', 0.917), ('TE', 0.857)], held_momentum=0, new_buys=['SNDK', 'SKYT'] |
| 2026-01-30 | L21842 | V28 ROTATION RESULT: fired=0/4 pairs=[] unfunded=8 (top: INCY(raw=1.800,age=0d), KLAC(raw=1.800,age=0d), VSEC(raw=1.800,age=0d), WDC(raw=1.750,age=0d), SNDK(raw=1.700,age=0d), TE(raw=1.300,age=0d), USO(raw=1.30 |
| 2026-01-30 | L21852 | Backfill queue BLOCKED: SNDK (full_priority_blocked, score=1.700, source=propagation_expansion) |
| 2026-01-30 | L21867 | Backfill queue BLOCKED (broker-skipped): SNDK (full_priority_blocked, priority=1) |
| 2026-01-30 | L21879 | Backfill queue FORCE-ADD: SNDK (source=propagation_expansion, score=1.700) |
| 2026-01-30 | L21903 | Promoted buys demoted to queue-only hold: INCY, KLAC, SNDK, UBER, VSEC, WELL |
| 2026-02-02 | L22663 | Momentum ceiling block: SNDK 20d=+142.8% 60d=+178.3% (caps 20d=80%, 60d=200%) |
| 2026-02-02 | L22685 | Backfill queue scoring net: 58 ticker(s) added: VVX, UBER, CAT, GILD, CMI, BMY, DHR, DOW, FFIV, FITB, VRTX, AKAM, ALL, BOKF, CHRW, CNTA, EBAY, FBP, HON, INCY, KLAC, LAES, RGA, UNH, UNM, VSEC, XPEV, EC, MSTR, EB |
| 2026-02-02 | L22773 | Sector concentration detail: technology: kept SONO, WDC, SNDK, RUN, SKYT, TE / demoted TNDM, RRX |
| 2026-02-02 | L22784 | Momentum watchlist: watchlist=450, scored=287, top3=[('SNDK', 1.505), ('SKYT', 0.804), ('TE', 0.709)], held_momentum=0, new_buys=['SNDK', 'SKYT'] |
| 2026-02-02 | L22786 | V28 ROTATION RESULT: fired=0/4 pairs=[] unfunded=7 (top: SONO(raw=1.750,age=0d), SNDK(raw=1.705,age=0d), SKYT(raw=1.700,age=0d), RDNT(raw=1.300,age=0d), RKLB(raw=1.300,age=0d), RUN(raw=1.300,age=0d), SON(raw=1. |
| 2026-02-02 | L22815 | V28 BFQ ALLOC=0: SNDK (queued 1d, score=1.705, signal_score=1.700) / priority_budget=$95 standard_budget=$95 min_pos=$100 headroom=5 budget_key=priority |
| 2026-02-02 | L22828 | Promoted buys demoted to queue-only hold: RUN, SNDK, SONO, UBER |
| 2026-02-03 | L23589 | Momentum ceiling block: SNDK 20d=+141.7% 60d=+241.9% (caps 20d=80%, 60d=200%) |
| 2026-02-03 | L23605 | Backfill queue scoring net: 46 ticker(s) added: UBER, CAT, GILD, CMI, BMY, DHR, DOW, FFIV, FITB, VRTX, ALL, BOKF, CHRW, CNTA, EBAY, FBP, HON, INCY, LAES, RGA, UNH, UNM, VSEC, XPEV, EC, MSTR, ARW, BAH, EME, FIVE |
| 2026-02-03 | L23702 | Momentum watchlist: watchlist=456, scored=296, top3=[('SNDK', 1.907), ('MOVE', 0.861), ('AXTI', 0.82)], held_momentum=0, new_buys=['SNDK', 'MOVE'] |
| 2026-02-03 | L23704 | V28 ROTATION RESULT: fired=0/4 pairs=[] unfunded=7 (top: WMT(raw=1.800,age=14d), SNDK(raw=2.107,age=1d), DVN(raw=1.800,age=0d), FN(raw=1.800,age=0d), TYRA(raw=1.750,age=0d), SIND(raw=1.300,age=0d), SON(raw=1.30 |
| 2026-02-03 | L23717 | Backfill queue REFRESH: SNDK (score=2.307, recurrence=2, source=direct) |
| 2026-02-03 | L23724 | V28 BFQ DRAIN ENTRY: queue_size=60 headroom=7 cash=$391 priority_budget=$195 standard_budget=$195 min_pos=$100 top10=[SNDK(score=2.307,age=0d), UBER(score=2.163,age=5d), WMT(score=1.900,age=0d), CAT(score=1.850 |
| 2026-02-03 | L23725 | Backfill queue BUY: SNDK (queued 1 bars, alloc=$100, score=2.107 HIGH-CONV) |
| 2026-02-03 | L23737 | V31.2 total-spend cap [CONCENTRATE]: funded 4 of 4 by conviction (SNDK@$874, CTRA@$874, MOVE@$874, ENVA@$874) out of $3,934; dropped 0 to the queue |
| 2026-02-03 | L23738 | Executable buys: CTRA, ENVA, MOVE, SNDK |
| 2026-02-03 | L23761 | Nexus executable buys: adding 1 ticker(s) to execution: SNDK |
| 2026-02-03 | L23825 | SNDK @ 2026-02-03 15:00:00 ($655.38): buy action_intent=backfill_queue_buy (weighted scores from 1 strategies) |
| 2026-02-03 | L23826 | SATELLITE OVERFLOW: SNDK raw=+2.107 >= 1.50 — funding $1,107 of room out of the core (floor-bounded) |
| 2026-02-03 | L23827 | TURNOVER BUDGET BYPASS: SNDK raw=+2.107 >= 1.50 — admitting a conviction buy through a 76% budget; the brake is for churn, not for the trade that matters |
| 2026-02-03 | L23828 | Buy gate inputs for SNDK: cash=$1097.41 reserved=$0.00 floor=$120.00 effective_floor=$0.00 high_conv=True open_pos=5 cash_per_trade=$874.30 available=$1097.41 cash_to_use=$874.30 → PASS |
| 2026-02-03 | L23829 | SKIP BUY SNDK — fundable $99.20 of cash_to_use $874.30 (orders already in flight this tick reserve the rest) < min $375 (allocated $874.30) |
| 2026-02-03 | L23830 | Gate skips reported back: SNDK (insufficient_cash) |
| 2026-02-04 | L24468 | Momentum ceiling block: SNDK 20d=+153.7% 60d=+221.3% (caps 20d=80%, 60d=200%) |
| 2026-02-04 | L24485 | Broker-skipped scoring net: 2 ticker(s) added: SNDK, CTRA |
| 2026-02-04 | L24576 | Momentum watchlist: watchlist=466, scored=298, top3=[('SNDK', 1.936), ('WDC', 0.698), ('VTYX', 0.67)], held_momentum=0, new_buys=['SNDK', 'WDC'] |
| 2026-02-04 | L24578 | V28 ROTATION RESULT: fired=0/4 pairs=[] unfunded=5 (top: SNDK(raw=2.136,age=0d), SAN(raw=1.800,age=0d), SON(raw=1.300,age=1d), RIO(raw=1.300,age=0d), RKLB(raw=1.300,age=0d)) |
| 2026-02-04 | L24591 | Backfill queue ADD: SNDK (score=2.136, price=$655.38, source=direct) |
| 2026-02-04 | L24596 | Backfill queue REFRESH (broker-skipped): SNDK (score=2.136, priority=1) |
| 2026-02-04 | L24598 | V28 BFQ DRAIN ENTRY: queue_size=60 headroom=7 cash=$607 priority_budget=$303 standard_budget=$303 min_pos=$100 top10=[SNDK(score=2.236,age=0d), UBER(score=2.163,age=6d), WMT(score=1.900,age=1d), CAT(score=1.850 |
| 2026-02-04 | L24599 | Backfill queue BUY: SNDK (queued 1 bars, alloc=$152, score=2.136 HIGH-CONV) |
| 2026-02-04 | L24610 | V31.2 total-spend cap [CONCENTRATE]: funded 4 of 8 by conviction (SNDK@$864, AMZN@$864, ETN@$864, MSGE@$864) out of $3,887; dropped 4 to the queue |
| 2026-02-04 | L24612 | Executable buys: AMZN, ETN, MSGE, SNDK |
| 2026-02-04 | L24639 | Nexus executable buys: adding 1 ticker(s) to execution: SNDK |
| 2026-02-04 | L24718 | SNDK @ 2026-02-04 15:00:00 ($644.9): buy action_intent=backfill_queue_buy (weighted scores from 1 strategies) |
| 2026-02-04 | L24719 | SATELLITE OVERFLOW: SNDK raw=+2.136 >= 1.50 — funding $2,594 of room out of the core (floor-bounded) |
| 2026-02-04 | L24720 | TURNOVER BUDGET BYPASS: SNDK raw=+2.136 >= 1.50 — admitting a conviction buy through a 101% budget; the brake is for churn, not for the trade that matters |
| 2026-02-04 | L24721 | Buy gate inputs for SNDK: cash=$1636.94 reserved=$0.00 floor=$120.00 effective_floor=$120.00 high_conv=True open_pos=3 cash_per_trade=$863.82 available=$1516.94 cash_to_use=$863.82 → PASS |
| 2026-02-04 | L24977 | [execution] FILL BUY SNDK qty=1.33636300 cumulative=1.33636300 price=617.420677 fees=0.024753 quote=2026-02-04 16:00:00+00:00 model=equity-measured-v3-nbbo23 source=main_signal |
| 2026-02-05 | L25355 | Discovered stock (momentum): SNDK (20d=+67.2%, 60d=+181.6%) |
| 2026-02-05 | L25467 | Momentum watchlist: watchlist=477, scored=292, top3=[('SNDK', 1.382), ('MOVE', 0.75), ('VTYX', 0.7)], held_momentum=0, new_buys=['MOVE', 'VTYX'] |
| 2026-02-05 | L25764 | SNDK @ 2026-02-05 15:00:00 ($600.77): hold action_intent=hold (weighted scores from 1 strategies) |
| 2026-02-06 | L26348 | Momentum watchlist: watchlist=488, scored=277, top3=[('SNDK', 1.208), ('MOVE', 0.899), ('LITE', 0.701)], held_momentum=0, new_buys=['MOVE', 'LITE'] |
| 2026-02-06 | L26608 | SNDK @ 2026-02-06 15:00:00 ($601.83): hold action_intent=hold (weighted scores from 1 strategies) |
| 2026-02-09 | L27206 | Momentum watchlist: watchlist=498, scored=312, top3=[('SNDK', 1.187), ('MOVE', 0.84), ('RFIL', 0.774)], held_momentum=0, new_buys=['MOVE', 'RFIL'] |
| 2026-02-09 | L27504 | SNDK @ 2026-02-09 15:00:00 ($584.5): hold action_intent=hold (weighted scores from 1 strategies) |
| 2026-02-10 | L28098 | Momentum watchlist: watchlist=511, scored=329, top3=[('SNDK', 1.156), ('MOVE', 0.926), ('AXTI', 0.917)], held_momentum=0, new_buys=['MOVE', 'AXTI'] |
| 2026-02-10 | L28395 | SNDK @ 2026-02-10 15:00:00 ($558.0): hold action_intent=hold (weighted scores from 1 strategies) |
| 2026-02-11 | L28991 | Momentum watchlist: watchlist=516, scored=328, top3=[('MOVE', 0.928), ('SNDK', 0.876), ('UCTT', 0.783)], held_momentum=0, new_buys=['MOVE', 'UCTT'] |
| 2026-02-11 | L29298 | SNDK @ 2026-02-11 15:00:00 ($596.85): hold action_intent=hold (weighted scores from 1 strategies) |
| 2026-02-12 | L29888 | Momentum watchlist: watchlist=530, scored=326, top3=[('SNDK', 0.951), ('UCTT', 0.77), ('LITE', 0.758)], held_momentum=0, new_buys=['UCTT', 'LITE'] |
| 2026-02-12 | L30195 | SNDK @ 2026-02-12 15:00:00 ($651.97): hold action_intent=hold (weighted scores from 1 strategies) |
| 2026-02-13 | L30790 | Momentum watchlist: watchlist=540, scored=314, top3=[('SNDK', 1.021), ('LITE', 0.8), ('UCTT', 0.636)], held_momentum=0, new_buys=['LITE', 'UCTT'] |
| 2026-02-13 | L31072 | SNDK @ 2026-02-13 15:00:00 ($590.73): hold action_intent=hold (weighted scores from 1 strategies) |
| 2026-02-16 | L31666 | Momentum watchlist: watchlist=548, scored=324, top3=[('SNDK', 1.078), ('FSLY', 0.831), ('LITE', 0.812)], held_momentum=0, new_buys=['FSLY', 'LITE'] |
| 2026-02-16 | L31993 | SNDK @ 2026-02-16 15:00:00 ($626.79): hold action_intent=hold (weighted scores from 1 strategies) |
| 2026-02-17 | L32591 | Momentum watchlist: watchlist=550, scored=324, top3=[('SNDK', 1.078), ('FSLY', 0.831), ('LITE', 0.812)], held_momentum=0, new_buys=['FSLY', 'LITE'] |
| 2026-02-17 | L32877 | SNDK @ 2026-02-17 15:00:00 ($601.46): hold action_intent=hold (weighted scores from 1 strategies) |
| 2026-02-18 | L33477 | Momentum watchlist: watchlist=558, scored=331, top3=[('SNDK', 1.03), ('LITE', 0.988), ('PMN', 0.789)], held_momentum=0, new_buys=['LITE', 'PMN'] |
| 2026-02-18 | L33779 | SNDK @ 2026-02-18 15:00:00 ($595.505): hold action_intent=hold (weighted scores from 1 strategies) |
| 2026-02-19 | L34369 | Momentum watchlist: watchlist=567, scored=357, top3=[('IBRX', 1.734), ('SNDK', 0.979), ('LITE', 0.97)], held_momentum=0, new_buys=['IBRX', 'LITE'] |
| 2026-02-19 | L34671 | SNDK @ 2026-02-19 15:00:00 ($606.65): hold action_intent=hold (weighted scores from 1 strategies) |
| 2026-02-20 | L35276 | Momentum watchlist: watchlist=577, scored=367, top3=[('IBRX', 1.734), ('LITE', 1.018), ('SNDK', 0.978)], held_momentum=0, new_buys=['IBRX', 'LITE'] |
| 2026-02-20 | L35555 | SNDK @ 2026-02-20 15:00:00 ($638.4): hold action_intent=hold (weighted scores from 1 strategies) |
| 2026-02-23 | L36418 | SNDK @ 2026-02-23 15:00:00 ($683.19): hold action_intent=hold (weighted scores from 1 strategies) |
| 2026-02-24 | L36999 | [sell-gate] SNDK / gate=winner_protect / pnl=+7.6% / drop_from_peak=3.3% < 8.0% / result=blocked (hold) |
| 2026-02-24 | L37031 | Momentum watchlist: watchlist=590, scored=354, top3=[('IBRX', 2.043), ('SNDK', 1.048), ('LITE', 1.017)], held_momentum=0, new_buys=['IBRX', 'LITE'] |
| 2026-02-24 | L37313 | SNDK @ 2026-02-24 15:00:00 ($664.485): hold action_intent=hold (weighted scores from 1 strategies) |
| 2026-02-25 | L38185 | SNDK @ 2026-02-25 15:00:00 ($645.43): hold action_intent=hold (weighted scores from 1 strategies) |
| 2026-02-26 | L39085 | SNDK @ 2026-02-26 15:00:00 ($636.61): hold action_intent=hold (weighted scores from 1 strategies) |
| 2026-02-27 | L39996 | SNDK @ 2026-02-27 15:00:00 ($641.26): hold action_intent=hold (weighted scores from 1 strategies) |
| 2026-02-28 | L40308 | SNDK          1.3364 shares  @ $  635.94  = $    849.85 |
| 2026-02-28 | L40325 | SNDK: P&L = $18.84 (+2.28%) |
| 2026-02-28 | L40338 | SNDK: $237.33 -> $631.54  (+166.10%) |

### 1.3 Post-entry monitor track (condensed; 187 `Monitor decision` lines, all `HOLD`)

Entry $617.42. The monitor never once produced a sell: `day 1 pnl=-5.3%` (L25202)
through `day 7 pnl=-1.5%` (L29457) and beyond, every line `→ HOLD (monitor: hold)`.
The one sell-gate evaluation in the window is L36999, 2026-02-24:
`[sell-gate] SNDK | gate=winner_protect | pnl=+7.6% | drop_from_peak=3.3% < 8.0% |
result=blocked (hold)` — a non-empty reason, i.e. evaluated and blocked. SNDK was
still held at the close (L40308), so no exit defect is claimed here.

---

## 2. The nine refusals — cause, evidence, and cost

Exactly nine SNDK buy intents reached the broker and were refused. Three earlier
refusals happened one level up, inside the backfill queue, and are listed in §3.1.

| # | Date | Price | Allocator ask | Post-cap alloc | Gate had | Floor | Verdict line |
|---|---|---|---|---|---|---|---|
| 1 | 2026-01-12 | $388.455 | $860 | $221.24 | $212.59 | $368 | L8994 |
| 2 | 2026-01-13 | $390.49 | $871 | $212.42 | $212.42 | $373 | L9920 |
| 3 | 2026-01-14 | $393.06 | $871 | $210.94 | $210.94 | $373 | L10838 |
| 4 | 2026-01-15 | $418.72 | $890 | $191.40 | $191.40 | $382 | L11770 |
| 5 | 2026-01-16 | $405.47 | $952 | — (hard skip) | — | — | L12717 |
| 6 | 2026-01-19 | $413.55 | $885.03 | $885.03 | $133.49 | $379 | L13649 |
| 7 | 2026-01-21 | $468.84 | $879.44 | $879.44 | $146.68 | $377 | L15477 |
| 8 | 2026-01-29 | $533.41 | $891 | $255.56 | $252.84 | $382 | L21048 |
| 9 | 2026-02-03 | $655.38 | $874.30 | $874.30 | $99.20 | $375 | L23829 |
| ✓ | 2026-02-04 | $644.90 | $863.82 | $863.82 | $863.82 | $370 | **FILL L24977** |

They are **not one defect**. They fall into three mechanically distinct groups:

* **A — SATELLITE CAP trims below the execution floor** (#1,2,3,4,8).
* **B — in-flight SPY core reservation starves the alpha buy** (#6,9); and the same
  core selling SPY to fund a buy whose proceeds have not settled (#7).
* **C — the rotation lane loses its conviction score** (#5).

### 2.1 Cost of each individual refusal

Two counterfactuals, both stated explicitly. Terminal mark is $635.94 (L40308); the
run never sold SNDK.

**CF-1 — cost of the DELAY, holding size fixed at the $825.10 that was actually
deployed (L24977).** This is additive across refusals because each refusal only costs
the price move to the *next* opportunity. Sum = the whole delay cost.

| # | Date | Price | Next opportunity | **Marginal cost of THIS refusal** |
|---|---|---|---|---|
| 1 | 2026-01-12 | $388.455 | $390.49 | **+$7.04** |
| 2 | 2026-01-13 | $390.49 | $393.06 | **+$8.79** |
| 3 | 2026-01-14 | $393.06 | $418.72 | **+$81.81** |
| 4 | 2026-01-15 | $418.72 | $405.47 | **−$40.95** (price fell; waiting helped) |
| 5 | 2026-01-16 | $405.47 | $413.55 | **+$25.28** |
| 6 | 2026-01-19 | $413.55 | $468.84 | **+$149.63** |
| 7 | 2026-01-21 | $468.84 | $533.41 | **+$135.48** |
| 8 | 2026-01-29 | $533.41 | $655.38 | **+$183.07** |
| 9 | 2026-02-03 | $655.38 | $617.42 (fill) | **−$49.22** (fill was below signal) |
| | | | **TOTAL** | **+$500.92** |

Check: $825.10 bought at $388.455 is 2.1241 sh, worth $1,350.77 at $635.94, against the
$849.85 actually held (L40308) — difference **$500.92**, exactly the column sum.

Context: the run made **+$366.10** (L40297). Recovering $500.92 would have taken it to
**+$867.02 (+14.45%)** — i.e. **this one name's entry delay is larger than the entire
run's profit.** CF-1 assumes $825.10 was fundable on 2026-01-12, which it was not
(cash was $212.59, L8993); it is therefore the cost of the *whole* funding + floor
complex, not of the floor alone.

**CF-2 — cost of refusing the money the gate ACTUALLY had in hand.** Lower bound, and
these are NOT additive (buying on 01-12 makes 01-13 moot).

| # | Date | Dollars on hand | Terminal @ $635.94 | Forgone gain |
|---|---|---|---|---|
| 1 | 2026-01-12 | $212.59 | $348.03 | **+$135.44** |
| 2 | 2026-01-13 | $212.42 | $345.94 | +$133.52 |
| 3 | 2026-01-14 | $210.94 | $341.28 | +$130.34 |
| 4 | 2026-01-15 | $191.40 | $290.69 | +$99.29 |
| 5 | 2026-01-16 | $0 (hard skip) | — | see §2.4 |
| 6 | 2026-01-19 | $133.49 | $205.28 | +$71.79 |
| 7 | 2026-01-21 | $146.68 | $198.96 | +$52.28 |
| 8 | 2026-01-29 | $252.84 | $301.44 | +$48.60 |
| 9 | 2026-02-03 | $99.20 | $96.26 | −$2.94 |

The single most valuable refusal to have reversed is **#1, worth +$135.44 on the cash
actually sitting in the account**, and it is also the one whose reversal makes SNDK a
`held` name — after which broker.py:3819 (`if decision != 1 or held: return False`)
exempts every later add from the floor entirely, so #2,3,4,8 would have converted too.
I cannot put a number on that compounding without running a backtest, and I have not.

### 2.2 Group A — SATELLITE CAP trims to a size the execution floor then refuses

Two gates in the same function, 700 lines apart, with **opposite policies on the same
dollars**:

* `broker.py:15954-15958` — the satellite cap **TRIMS** rather than refusing, by
  explicit design (`broker.py:15862`: *"TRIM to the remaining share, do not refuse."*):
  ```
  if cash_per_trade > _sat_room:
      _log(f"SATELLITE CAP: {symbol} trimmed ${cash_per_trade:,.0f} -> ${_sat_room:,.0f} ...")
      cash_per_trade = _sat_room
  ```
* `broker.py:3819-3822` — the execution floor then **REFUSES** anything under
  `max($50, NAV × min_position_nav_pct)`:
  ```
  hard = floor > _EXEC_MIN_POSITION_USD
  return fundable < floor and (hard or cash_to_use < cash_per_trade)
  ```
  `hard` short-circuits the historical *"only refuse what was TRUNCATED"* test. The
  authors removed it deliberately — `broker.py:16495-16501`: *"A position below the
  NAV floor must not open regardless of how it got small."*

Measured floor in this run is ~6% of NAV: $368-$382 across L8994..L23829, against
`15% of portfolio $6350` at L12592 → NAV ≈ $6,133-$6,367, and 368/6133 = 0.060.

**The two are arithmetically incompatible.** On every Group-A bar the satellite
headroom was $191-$256 while the floor was $368-$382, so the trim guaranteed the
refusal:

```
2026-01-12  L8991  SATELLITE CAP: SNDK trimmed $860 -> $221
            L8993  Buy gate inputs ... cash_to_use=$212.59 → PASS
            L8994  SKIP BUY SNDK — cash_to_use $212.59 < min $368 (allocated $221.24)
2026-01-15  L11767 SATELLITE CAP: SNDK trimmed $890 -> $191
            L11770 SKIP BUY SNDK — cash_to_use $191.40 < min $382
2026-01-29  L21045 SATELLITE CAP: SNDK trimmed $891 -> $256
            L21048 SKIP BUY SNDK — fundable $252.84 ... < min $382
```

And the core sleeve is dragged into it. On the SAME tick the core's funding request is
trimmed by the SAME headroom and SPY is actually SOLD to raise the money:

```
2026-01-12  L8975  [core] funding request trimmed $2,579 -> $221 — satellite headroom
                   will refuse the remainder
            L8976  [core] released 0.0125 SPY @ 694.00 (residual_bull_refill)
            L8994  SKIP BUY SNDK ...
```

`broker.py:3445-3450` names this exact failure — *"extending the release without
extending the buy sells core to fund an order that is then refused and buys it straight
back"* — but the code only made the *release* and the *headroom* agree. The
**execution floor is a third gate neither of them knows about**, so the loop it was
written to close is still open, one hop further down.

### 2.3 Group B — the SPY core leg reserves the alpha book's cash

`PortfolioEmulator.execute_signal` clamps every buy to
`min(cash_per_trade, get_buying_power(reserved_cash))` (`portfolio_emulator.py:1489-1492`),
where `reserved_cash` is the still-in-flight BUY reservations booked earlier. The buy
gate logs `get_cash()` instead (`broker.py:16461`), which nets neither. Result: the
gate prints `→ PASS` and the floor then refuses against a number an order of magnitude
smaller.

**2026-01-19 (#6) — reconstructed to the cent.** The core bought $1,165.72 of SPY on
2026-01-17 (L13194, `band_deploy: 11.6% -> 30.2% of NAV`); that order did not fill until
2026-01-20 (L14816). Across the intervening 01-19 tick it held a live cash reservation:

```
L13648  Buy gate inputs for SNDK: cash=$1299.21 reserved=$0.00 ... cash_to_use=$885.03 → PASS
L13649  SKIP BUY SNDK — fundable $133.49 of cash_to_use $885.03 ... < min $379
```

$1,299.21 − $1,165.72 = **$133.49 exactly.** AMZN (L13632) and SKYT (L13642) were
refused on the identical number the same tick — so this is *not* an ordering/tie-break
problem: **every** alpha buy on 01-19 was starved by the index leg.

**2026-02-03 (#9) — same mechanism, same tick.** L23405: `[core] bought $965.34 SPY
(band_deploy: 12.0% -> 27.4% of NAV)`, emitted at L23405 — *before* the alpha buy loop
at L23828. $1,097.41 − $965.34 = $132.07; the gate saw $99.20 after the unsettled-sell
haircut (`portfolio_emulator.py:476-479`). SNDK was **first** in execution order that
tick (L23828, ahead of CTRA/MOVE) and was refused anyway, together with CTRA (L23836)
and MOVE (L23843). I explicitly rule out the alphabetical tie-break
(`broker.py:14601`, `_buy_order_conviction_ranked_enabled` default OFF at
`broker.py:3370`, and all 33 `Execution order:` lines in this log read
`(intent_priority, allocation, ticker)`) as the cause of these two refusals.

**2026-01-21 (#7) — the core sells $1,114 to fund a buy it then cannot fund.**

```
L15455  [core] funding request trimmed $2,638 -> $1,252
L15456  [core] released 1.6309 SPY @ 677.66 (residual_bull_refill)   <- submitted
L15476  Buy gate inputs for SNDK: cash=$146.68 ... → PASS
L15477  SKIP BUY SNDK — cash_to_use $146.68 < min $377 (allocated $879.44)
L15710  FILL SELL SPY qty=1.63091190 price=683.201892  ($1,114.24)   <- fills at bar close
```

The sale settles *after* the buy is sized, so the money raised specifically to buy SNDK
was invisible to the gate that refused SNDK.

**The resulting index churn is measurable.** SPY round-trips between 2026-01-17 and
2026-02-04, from the `FILL` lines:

| Date | Side | Notional | Line |
|---|---|---|---|
| 2026-01-20 | BUY | $1,152.59 | L14816 |
| 2026-01-21 | SELL | $1,114.24 | L15710 |
| 2026-01-22 | BUY | $1,127.65 | L16662 |
| 2026-01-23 | SELL | $1,127.93 | L17625 |
| 2026-01-28 | SELL | $5.18 | L20383 |
| 2026-01-30 | SELL | $7.04 | L22230 |
| 2026-02-03 | BUY | $962.14 | L24080 |
| 2026-02-03 | SELL | $9.41 | L24081 |
| 2026-02-04 | SELL | $954.06 | L24976 |
| | | **$6,460.25 gross = 107.7% of the $6,000 book** | |

Net SPY position change over that stretch is ~0 (final holding 1.0457 sh, L40309).
Every one of those releases was raised to fund a satellite buy the execution floor
then refused, and the redeploys are what starved SNDK on 01-19 and 02-03.

### 2.4 Group C — refusal #5, 2026-01-16: the rotation lane loses its conviction score

This is the single most expensive refusal in the ledger, and it is a **sell-only
half-rotation**.

```
L12561  Momentum rotation: sell VICR (score=0.805) → buy SNDK (score=1.113, $1081)
L12592  V31.2 rotation cap: SNDK alloc $1081 → $952 (15% of portfolio $6350)
L12626  Executable buys: ASML, SNDK
L12716  SNDK @ 2026-01-16 15:00:00 ($405.47): buy action_intent=momentum_watchlist_rotation
L12717  SATELLITE CAP: SNDK skipped — satellite at its DESIGN SHARE ($-1,393 room)
L12724  SKIP BUY ASML — cash_to_use $194.16 < min $381
L12962  [execution] FILL SELL VICR qty=7.22960347 price=149.087809   <- the SELL EXECUTED
```

**The sell leg filled; both buy legs were refused. Zero buys on the tick.** $1,077.85
of VICR was liquidated to fund a rotation that never happened.

The wording `design share` (not `overflow ceiling`) is diagnostic. `broker.py:15908-15912`
prints `design share` only when `_sat_is_conv` is False, and `broker.py:15892-15897`
sets `_sat_is_conv = raw_net_score >= _sat_conv_min` (1.50, per every
`SATELLITE OVERFLOW ... >= 1.50` line). On **every other** SNDK bar the hint carried
`raw=+1.700` / `+2.107` / `+2.136` and took the overflow path (L8990, L9916, L13646,
L15474, L21044, L23826, L24719). On 01-16 alone it did not.

Source cause, `strategies/graph_nexus_analysis.py:31125-31135`:

```python
nexus_position_sizes[_mw_buy] = {
    "buy_cash": round(_mw_buy_alloc, 2),
    "high_conviction": True,
    "raw_net_score": round(max(_mw_buy_score, 0.50), 4),   # <-- line 31129
    "signal_source": "momentum_watchlist_rotation",
    ...
}
```

The rotation lane writes the **momentum-watchlist score** into `raw_net_score`. On
01-16 that was **1.113** (L12554, L12561) — on a different scale from the graph raw net
score the broker's 1.50 conviction threshold is calibrated to, and which the same tick
recorded as **1.700** for SNDK at L12556. So the one lane that *brings its own funding*
is the one lane that cannot clear the conviction gate, and it is scored
`high_conviction: True` while carrying a sub-threshold `raw_net_score` — the two keys
in the same dict contradict each other.

**Cost of #5**, all three branches priced to the same $635.94 mark:

| Branch | Terminal | Gain |
|---|---|---|
| Intended rotation: $952 into SNDK @ $405.47 | $1,493.12 | **+$541.12** |
| Do nothing: keep VICR ($1,077.85 @ $149.09 → $201.80, L40340) | $1,458.93 | +$381.09 |
| **What actually happened**: sell VICR, buy nothing; cash swept into SPY on 01-17 (L13194); SPY +0.64% (L40339) | ~$1,084 | **~+$7** |

The half-rotation was the **worst of the three branches**, costing **$541.12** against
its own intent and **$374** against simply doing nothing.

### 2.5 Observability defect (contributing, not causal)

`broker.py:16456-16457` computes the PASS/SKIP banner from a **hardcoded $50**:

```python
_exec_min_pos_preview = 50.0
_will_skip = cash_to_use < _exec_min_pos_preview and cash_to_use < cash_per_trade
```

while the real decision runs 60 lines later against a ~$375 NAV floor
(`broker.py:16515-16518`). Consequence across the whole run: **67 of 78
`Buy gate inputs ... → PASS` lines are immediately followed by `SKIP BUY` of the same
symbol (86%), and all 67 `SKIP BUY` lines are preceded by a `PASS`.** All nine SNDK
gate lines say `→ PASS`. The diagnostic that exists to explain refused buys reports the
opposite of the decision.

### 2.6 What is NOT the cause (ruled out with evidence)

* **Turnover brake** — bypassed on every SNDK bar: `TURNOVER BUDGET BYPASS: SNDK
  raw=+1.700 >= 1.50` (L8992, L9918, L10836, L11768, L13647, L15475, L21046, L23827,
  L24720). Not once did it block SNDK.
* **max_positions** — `max_positions gate armed: held=6, cap=8` (L8974). Slots were
  free on the Group-A bars; there is no `blocked SNDK (held=..., cap=...)` line anywhere.
* **Alphabetical tie-break** — ruled out in §2.3.
* **Displacement** — `satellite_displacement_enabled` is default False
  (`broker.py:3298-3304`) and there are **zero `DISPLACEMENT` lines in the entire log**.
  The one lever designed to free cash for a high-conviction buy never ran. This is a
  *missing* mitigation, not the cause.
* **Exit logic** — SNDK was held to the close (L40308); the one sell-gate evaluation
  blocked with a non-empty reason (L36999).

---

## 3. The upstream refusals (before the broker ever saw a buy)

### 3.1 Backfill-queue `ALLOC=0`, 2026-01-07 / 01-08 / 01-09 / 02-02

```
L6012  2026-01-07  Backfill queue ADD: SNDK (score=1.000, price=$328.19, source=direct)
L6035  2026-01-07  V28 BFQ ALLOC=0: SNDK ... priority_budget=$324 standard_budget=$324
                   min_pos=$100 headroom=0 budget_key=none
L6989  2026-01-08  V28 BFQ ALLOC=0: SNDK ... priority_budget=$14  min_pos=$100 headroom=3
L7950  2026-01-09  V28 BFQ ALLOC=0: SNDK ... priority_budget=$59  min_pos=$100 headroom=5
L22815 2026-02-02  V28 BFQ ALLOC=0: SNDK ... priority_budget=$95  min_pos=$100 headroom=5
```

On 01-08/01-09/02-02 the budget ($14 / $59 / $95) is below the queue's own `min_pos=$100`,
so the queue could not have funded anything at all. Note 01-07 reports
`headroom=0 budget_key=none` — a *different* refusal reason (no slot) from the other
three (no budget). **$212.59 of cash existed on 01-12 (L8993) but only $324 of
priority budget on 01-07 (L6035); I cannot determine from this log why the $324 budget
produced a $0 allocation with `budget_key=none`, and I do not speculate.**

### 3.2 The extension gate

```
L5995  2026-01-07  V32 mw_buy extension-block: SNDK range +73.2% > 25% [bars=97]
L6950  2026-01-08  V32 mw_buy extension-block: SNDK range +75.3% > 25% [bars=98]
L16223 2026-01-22  Entry extension gate: SNDK recent runup +111.2% > 25% — buy blocked
L16234 2026-01-22  V32 mw_buy extension-block: SNDK range +111.2% > 25% [bars=107]
L17179 2026-01-23  V32 mw_buy extension-block: SNDK range +112.0% > 25% [bars=108]
L18156 2026-01-26  V32 mw_buy extension-block: SNDK range +112.0% > 25% [bars=109]
```

`no conviction bypass` on all six. This gate blocked the `mw_buy` lane on 01-07/01-08
(routing SNDK to the queue instead, L6012) and blocked it outright on 01-22/01-23/01-26.
It is a genuine design choice, not a bug, so I do not rank it as a defect — but it is
worth recording that a name scoring 1.42/1.37/1.36 at the **top of the watchlist**
(L16236, L17181, L18158) was blocked for three consecutive sessions on the strength of
the move that made it the top of the watchlist.

### 3.3 The momentum ceiling (discovery lane)

```
L21718 2026-01-30  Momentum ceiling block: SNDK 20d=+125.5% 60d=+171.7% (caps 20d=80%, 60d=200%)
L22663 2026-02-02  Momentum ceiling block: SNDK 20d=+142.8% 60d=+178.3%
L23589 2026-02-03  Momentum ceiling block: SNDK 20d=+141.7% 60d=+241.9%
L24468 2026-02-04  Momentum ceiling block: SNDK 20d=+153.7% 60d=+221.3%
```

The 20d cap (80%) is what binds; the 60d cap (200%) only binds on 02-03.

### 3.4 The 01-22 .. 02-02 dead zone

Seven sessions on which SNDK was top-of-watchlist and the broker never saw it:

| Date | Watchlist rank | Blocking line |
|---|---|---|
| 2026-01-22 | SNDK 1.422 (#1, L16236) | extension-block L16234 + `full_priority_blocked` L16252 |
| 2026-01-23 | SNDK 1.371 (#1, L17181) | extension-block L17179 |
| 2026-01-26 | SNDK 1.360 (#1, L18158) | extension-block L18156 |
| 2026-01-27 | SNDK 1.210 (#1, L19084) | `full_priority_blocked` L19106; `Deferred unfunded buys demoted to hold` L19126 |
| 2026-01-28 | SNDK 1.105 (#1, L20000) | `full_priority_blocked` L20025; demoted L20053 |
| 2026-01-30 | SNDK 1.476 (#1, L21840) | `full_priority_blocked` L21852, then `FORCE-ADD` L21879, then `Promoted buys demoted to queue-only hold` L21903 |
| 2026-02-02 | SNDK 1.505 (#1, L22784) | `BFQ ALLOC=0` L22815; demoted L22828 |

Note L21879 (`Backfill queue FORCE-ADD: SNDK`) followed 24 lines later by L21903
(`Promoted buys demoted to queue-only hold: ... SNDK ...`) on the same tick: the queue
force-admitted it and the sizing pass immediately demoted it.

---

## 4. The single smallest code change that would have bought SNDK on the first signal

**Target: refusal #1, 2026-01-12 at $388.455 (L8989/L8994).**

On that bar the book had `cash=$212.59` (L8993) and the satellite cap had already
trimmed the allocation to `$221.24` (L8991). Any floor above $212.59 refuses. The
binding line is `broker.py:3819-3822`.

### The change — one condition in `_exec_min_position_gate` (`broker.py`, ~line 3865)

```python
    fundable = cash_to_use
```
becomes
```python
    # A buy that a SIZING GUARD already cut below the NAV floor was not
    # truncated by accident — `cash_per_trade` IS the size the book's own
    # guards chose (SATELLITE CAP, broker.py:15954-15958, deliberately TRIMS
    # rather than refusing: "TRIM to the remaining share, do not refuse",
    # broker.py:15862). Refusing it turns a deliberate small position into no
    # position, after the core was already SOLD to fund it (L8975/L8976).
    # Fall back to the historical $50 in exactly that case; the truncation
    # test below still applies, so genuine runts are still refused.
    if floor > cash_per_trade:
        floor = _EXEC_MIN_POSITION_USD
    fundable = cash_to_use
```

### Verified against the shipped function, not a mirror

Driving `broker.py`'s real `_exec_min_position_skips` (AST-extracted exactly as
`backend/tests/test_exec_min_position_floor.py` does) with the nine gate tuples read
off the log:

```
                       as shipped        with the fix
2026-01-12  skip=True           ->  floor 368 -> 50   skip=False   <-- FIRST SIGNAL, BUYS
2026-01-13  skip=True           ->  floor 373 -> 50   skip=False
2026-01-14  skip=True           ->  floor 373 -> 50   skip=False
2026-01-15  skip=True           ->  floor 382 -> 50   skip=False
2026-01-19  skip=True           ->  floor 379 unchanged   skip=True    (Group B, untouched)
2026-01-21  skip=True           ->  floor 377 unchanged   skip=True    (Group B, untouched)
2026-01-29  skip=True           ->  floor 382 -> 50   skip=False
2026-02-03  skip=True           ->  floor 375 unchanged   skip=True    (Group B, untouched)
2026-02-04  skip=False          ->  floor 370 unchanged   skip=False   (the real fill: IDENTICAL)
```

**Properties.**

* It buys on the **first** broker signal, 2026-01-12 at $388.455, for the $212.59 that
  was actually in the account — **+$135.44** on that tranche alone (§2.1 CF-2), and
  thereafter SNDK is `held`, which `broker.py:3819` already exempts from the floor
  entirely, so refusals #2/#3/#4/#8 convert to adds as well.
* It is **inert by default**: with `min_position_nav_pct` absent, `floor` is already
  `_EXEC_MIN_POSITION_USD`, so `floor > cash_per_trade` can only fire when
  `cash_per_trade < $50`, where the existing truncation test already governs.
* It **does not reopen the runt leak** the floor was written for. The regressions in
  `broker.py:3769-3772` (AVY: ask $860.36 → fill $47.36; AMZN: ask $613.78 → fill
  $102.17) both have `cash_per_trade` far ABOVE the floor, so the branch never fires
  and they still skip. Same for refusals #6/#7/#9 above.
* It changes **nothing** about the 2026-02-04 fill.

### Explicitly NOT proposed as the smallest change

* Lowering `min_position_nav_pct` — a **config** change, not code, and it would weaken
  the floor for every name rather than only where a sizing guard set the size.
* Enabling `satellite_displacement_enabled` / `buy_order_conviction_ranked_enabled` —
  also config; and §2.3 shows conviction ranking would not have saved #6/#9 anyway.
* Making the SATELLITE CAP refuse instead of trim — that produces **no** buy, which is
  the wrong direction.

---

## 5. Ranked defects and the fixes they need

**Defect 1 — `momentum_watchlist_rotation` writes the watchlist score into
`raw_net_score`, so the only self-funding lane cannot pass the conviction gate; and its
sell leg executes even when its buy leg is refused.** Cost on 2026-01-16 alone:
**$541.12** (§2.4). Evidence: L12561, L12592, L12717, L12962;
`graph_nexus_analysis.py:31129`; `broker.py:15892-15897`, `broker.py:15908-15912`.
*Fix:* at `graph_nexus_analysis.py:31129` carry the graph raw score
(`max(_mw_buy_score, graph_raw, 0.50)`, or a dedicated `mw_score` key) so
`raw_net_score` stays on the scale the 1.50 threshold is calibrated to; and make the
rotation's sell leg conditional on the paired buy clearing the satellite/floor gates,
so a refused buy cannot leave a sell-only tick.

**Defect 2 — the SPY core leg's in-flight cash reservation is invisible to the buy
gate.** Cost: the marginal chain legs owned by refusals #6, #7 and #9 =
$149.63 + $135.48 − $49.22 = **$235.89**, plus $6,460.25 of index churn (107.7% of the
book, §2.3) for ~zero net allocation change. Evidence: L13194/L14816/L13649 (the $133.49 identity),
L23405/L23829, L15456/L15477/L15710; `broker.py:16461` vs
`portfolio_emulator.py:1489-1492`. *Fix:* log and gate on
`get_buying_power(reserved)` rather than `get_cash()` at `broker.py:16461`, and make
the core's `band_deploy` (`residual_bull_deploy`) run **after** the alpha buy loop —
or reserve the alpha book's sized demand before the core deploys — so the index cannot
pre-empt cash that the same tick already allocated to a conviction name.

**Defect 3 — SATELLITE CAP trims below the execution floor, which then refuses,
turning a deliberate small position into no position after the core was sold to fund
it.** Cost: refusals #1,2,3,4,8; **+$135.44** recoverable on the first bar alone, and
it is the gate that blocks the whole entry chain. Evidence: L8991/L8993/L8994 and the
three siblings; `broker.py:15954-15958` vs `broker.py:3819-3822` and `broker.py:16495-16501`.
*Fix:* the four-line change in §4.

**Defect 4 (observability, contributing) — the `Buy gate inputs ... → PASS` banner uses
a hardcoded $50 preview and contradicts the real decision on 67 of 78 lines (86%).**
Evidence: `broker.py:16456-16457` vs `broker.py:16515-16518`. *Fix:* compute the banner
from `_exec_min_position_gate`'s return, or move the banner below the gate call.

---

## 6. Claims I could not support

* Why `V28 BFQ ALLOC=0` on 2026-01-07 reported `budget_key=none, headroom=0` while
  `priority_budget=$324` (L6035). The log does not show the branch taken; I did not
  trace it and I do not speculate.
* SNDK's price on 2026-01-22, 01-23, 01-26, 01-27, 01-28, 01-30, 02-02 — not in this log
  (§1.1). The CF-1 chain therefore steps 01-21 → 01-29 directly, which is correct for
  the delay arithmetic (those bars produced no buy intent) but means I cannot say
  whether an intra-window entry on those bars would have been cheaper or dearer.
* The compounding effect of the §4 fix beyond the first tranche. Reversing #1 makes SNDK
  `held` and changes cash, positions, `max_positions`, and the satellite share on every
  later bar. Quantifying it requires a backtest, which was out of scope here.
* Whether the whole-run +$500.92 of CF-1 is *recoverable*: it assumes $825.10 was
  fundable on 2026-01-12, which it was not. It is the joint cost of Defects 1-3, not of
  any one of them.
