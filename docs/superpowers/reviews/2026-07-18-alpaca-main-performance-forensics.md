# Alpaca Main Performance Forensics

**Date:** 2026-07-18

**Instance:** `alpaca-main`

**Strategy:** 179, Graph Nexus

**Scope:** Read-only Alpaca, IntelliStock API, RethinkDB, and source-code audit

**Decision:** **NO-GO for continued legacy live order generation.**

## 1. Executive Finding

The account is not merely drawing a visually noisy chart. After the account reached its
final $6,000 funding level, it lost 0.3437% through July 17 while adjusted SPY gained
0.8056%. Over the latest complete month, the portfolio lost 3.0104% while SPY lost only
0.4647%, an active shortfall of 2.5457 percentage points.

The account's first-deposit time-weighted return appears to beat SPY, but that comparison
is dominated by the June 4-8 period when most capital had not yet been deposited or
invested. It is not evidence that the strategy beat the benchmark. The ten-session period
after the July 6 portfolio reset did beat SPY, but it is too short and path-dependent to
establish an edge.

The observed behavior is best explained by a combination of:

1. Negative payoff asymmetry despite a 50% exit hit rate.
2. Very high broad-rotation turnover rather than repeated same-symbol round trips.
3. Backfill and override paths that can execute stale or explicitly contradictory evidence.
4. Forecast holding periods that outlive the apparent short-lived signal.
5. Stale position marks and a scheduler/live-mode collision in the live code path.
6. No SPY residual sleeve, allowing exited capital to create benchmark drag.
7. Incomplete decision provenance, incorrect inception P&L, and fragile order recovery.

No available result supports a claim that Graph Nexus currently has deployable alpha.
The appropriate response is to stop tuning the legacy order path, repair portfolio truth
and execution invariants, and test fresh direct forecasts as a challenger to SPY.

## 2. Method and Conventions

The audit used authenticated read-only calls without recording credentials or raw
authenticated payloads in git:

- Alpaca account state, positions, orders, portfolio history, and account activities.
- IntelliStock API state and whitelisted strategy configuration.
- RethinkDB decision, WAL, outcome, queue, momentum, and discovery records.
- Adjusted Alpaca IEX daily bars for SPY and traded symbols.
- Current source at commit `b2753374`, with GitNexus used for architecture orientation.

Alpaca daily portfolio timestamps were mapped to the preceding U.S. trading session in
`America/New_York`, then matched to the same-session adjusted SPY close. Daily account
marks were treated as close-to-close values. The June 8 deposit was removed from the
first-funding time-weighted return. Fills were aggregated by broker order ID before FIFO
reconstruction because most orders produced two activity fragments.

IEX is a single-exchange feed, so the symbol-level horizon study and implementation-cost
proxy are diagnostic, not promotion evidence. SIP-quality data remains required before
80% active exposure.

## 3. Portfolio Performance

| Lens | Sessions | Portfolio | SPY | Excess | Portfolio DD magnitude | SPY DD magnitude |
|---|---:|---:|---:|---:|---:|---:|
| First funding, Jun 4-Jul 17 | 30 | -0.3437% TWR | -1.5562% | +1.2126pp | 5.8259% | 4.1469% |
| Final funding, Jun 8-Jul 17 | 28 | -0.3437% | +0.8056% | -1.1493pp | 5.8259% | 3.1163% |
| Latest month, Jun 18-Jul 17 | 20 | -3.0104% | -0.4647% | -2.5457pp | 4.8126% | 2.3301% |
| Post-reset, Jul 6-Jul 17 | 10 | +0.7542% | -1.0635% | +1.8178pp | 3.8292% | 1.5445% |

Details:

- Final-funding equity was $6,000 on June 8 and $5,979.38 on July 17.
- The proper pre-first-fill anchor is June 9. SPY gained 1.1018% from that session,
  increasing active underperformance to 1.4454 percentage points.
- The account peak was $6,243.15 on June 15. The trough was $5,879.43 on July 13, a
  5.8259% maximum drawdown.
- Annualized daily volatility over the funded period was approximately 23.17%, versus
  15.89% for SPY. The account accepted more volatility and a deeper drawdown without
  earning a positive funded-period return.
- The post-reset result covers only ten sessions and excludes reset-day P&L. It is a
  monitoring fact, not statistically persuasive evidence.

## 4. Broker Ledger Reconciliation

At the read-only cutoff near `2026-07-18T09:49Z`:

- Equity: $5,979.38.
- Cash: $1,624.15, or 27.16%.
- Long market value: $4,355.23, or 72.84%.
- Open positions: 7.
- Open orders: 0.
- SPY position: none.
- Account activities: 127.
- Orders: 46, all market/day and filled.
- Orders tied to `alpaca-main` WAL records: 38.
- Alpaca dashboard orders: 8 sells on July 6.
- Unclassified orders: 0.

The ledger contained two deposits totaling $6,000, $7.91 of dividends, and $0.52 of
fees. FIFO realized P&L, current unrealized P&L, dividends, and fees reconcile to account
P&L within $0.0129:

```text
FIFO realized P&L         -$71.3420
Current unrealized P&L    +$43.3449
Dividends                  +$7.9100
Fees                       -$0.5200
Economic total            -$20.6071
Account P&L               -$20.6200
Rounding residual          -$0.0129
```

The eight dashboard exits lost $9.97 FIFO. Strategy-owned exits lost $61.37 FIFO, so
manual intervention is not the main explanation for the account's flat result.

| Exit owner | Sells | Wins/losses | FIFO P&L | Profit factor |
|---|---:|---:|---:|---:|
| Strategy, WAL-linked | 12 | 5 / 7 | -$61.37 | 0.6686 |
| Alpaca dashboard | 8 | 5 / 3 | -$9.97 | 0.9415 |
| Total | 20 | 10 / 10 | -$71.34 | 0.7993 |

## 5. Current Exposure Violates the Proposed Contract

The intended controlled portfolio permits up to ten names at 8% each. Every current
position exceeded that cap at the cutoff:

| Symbol | Market value | Account weight | Unrealized P&L |
|---|---:|---:|---:|
| CNC | $855.56 | 14.31% | -$31.27 |
| KNX | $777.78 | 13.01% | +$10.98 |
| BX | $573.12 | 9.59% | +$17.43 |
| S | $557.08 | 9.32% | +$29.10 |
| OKTA | $541.65 | 9.06% | +$12.44 |
| QLYS | $534.80 | 8.94% | +$4.93 |
| EWTX | $515.23 | 8.62% | -$0.27 |

The top three names represented 36.90% of equity. A live migration must classify these
holdings as adopted, exit-only, or external and bring them under the cap without a blind,
tax-insensitive liquidation.

## 6. Trading Quality and Turnover

Across 46 filled orders:

- 26 buys and 20 sells.
- Buy notional: $15,769.61.
- Sell notional: $11,386.38.
- Gross traded notional: $27,155.99.
- Gross turnover: approximately 470.94% of average equity.
- One-way turnover: approximately 197.47%.
- Exit hit rate: 50%.
- Gross profits: $284.12.
- Gross losses: $355.46.
- Profit factor: 0.7993.
- Quantity-weighted holding time: approximately 10.33 days.

The account did not repeatedly sell and rebuy the same symbol. The churn came from broad
portfolio replacement. Wash-sale controls are still required for every repeatedly traded
symbol, not only SPY, but wash sales are not the current primary cause.

The largest completed losses were CRWV at about -$121.64, MRNA at about -$70.09, DKNG
at about -$49.22, and BDC at about -$39.39. A 50% hit rate cannot overcome a payoff
distribution where average losses exceed average gains. Fixed percentage loss thresholds
also allowed individual losses near 19-21%, which is inconsistent with a controlled
8%-maximum position design.

## 7. Decision and Signal Forensics

The current `BotTradeDecisions` sample contained 28 rows and covered all 28 strategy
orders from June 18 onward. The first ten WAL-linked strategy orders predated the logger
and have no equivalent decision row. The logger is best-effort, runs in a daemon thread,
and suppresses persistence failures, so missing audit data is expected under failure.

Several actual entry records contradict executable eligibility:

- BDC `backfill_rotation_buy` said `No graph signal` and later lost $39.39.
- COHR `direct_reserved_buy` said `No graph signal` and later lost $9.24.
- BV `backfill_rotation_buy` said the Nexus quality filter blocked the candidate.
- MRNA's initial buy carried `trend_momentum_negative_evidence` and later lost $70.09.

The backfill grace path can retain a queued score after the current score falls below the
normal threshold. The execution reason is generated from current symbol context rather
than the exact queued evidence that supplied eligibility, allowing a stale candidate and
contradictory current rationale to coexist.

The legacy outcome endpoint is not suitable for promotion:

- `GraphNexusTradeContexts` contained 104,352 rows across seven `alpaca-main` scopes:
  2,917 buys, 93,185 holds, 8,250 sells, and 17,355 unknown intents.
- Only 11.1% of context rows contained an entry price. Recent rows intentionally omit
  the heavy LLM, overlay, and ML trace payloads needed for exact attribution.
- The current forward-outcome scope contained 2,054 rows; 1,752, or 85.3%, used
  `action_intent=unknown`.
- Six hundred forty-eight outcomes were zero, 637 never advanced beyond entry date, and
  KLAC/FEMY extremes were consistent with unadjusted corporate actions.
- Sell returns are stored with favorable directional sign, while the API hit-rate helper
  treats a sell as correct when that return is negative. Sell accuracy is semantically
  inverted.
- The endpoint reads at most 2,000 rows after broad table filtering.
- Queue/deferred states can be persisted as if they were directional outcomes.
- The reported hit rate therefore does not describe a stable, attributable strategy.

Recomputed Graph contexts are also not causal live records. Only 16 of 46 fills had any
scope whose final action agreed with the fill, only six had all available scopes agree,
and 12 had conflicting actions across scopes. Those rows were recomputed under later
configurations; they cannot prove why a historical live order was placed.

RethinkDB contained 166 `alpaca-main` boot records, all in legacy mode, with frequent
same-day restarts and recent `snapshot_loaded=false`. `LiveState.portfolio_history`
contained only one point, `LiveDecisionAudit` was empty, and no dedicated allocation
history existed. The broker history, not those tables, supplied the usable equity curve.

The currently observed strategy configuration also differs from the approved controlled
contract: `max_positions=8`, `nexus_portfolio_pct=.95`, and no SPY residual sleeve.
Backfill and rotation are enabled, while the ML leg is disabled but still appears in
reasons as neutral 0.50/0.50 evidence.

`LLMUsage` attributed approximately 10,328 calls and $80.73 of recorded cost to
`alpaca-main`; 9,229 calls carried a backtest ID. Compact live call IDs or trace digests
are not retained with decisions, so exact live LLM contribution and cost cannot be
established.

## 8. Horizon Study

Adjusted IEX daily bars for the 26 observed buys produced the following small,
overlapping, capital-unconstrained sample:

| Horizon | Sample | Mean excess | Median excess | Positive |
|---|---:|---:|---:|---:|
| 1 day | 26 | +0.086% | -0.182% | 12 / 26 |
| 3 days | 26 | -0.010% | -0.489% | 12 / 26 |
| 5 days | 25 | -3.055% | -1.432% | 10 / 25 |
| 10 days | 17 | -5.069% | -6.038% | 6 / 17 |

At three days, the 12 `initial_buy` observations averaged +1.489% excess return, while
the four `backfill_rotation` observations averaged -5.989%. The single
`direct_reserved` observation was -9.909%.

This does not justify deploying a three-day rule. It does justify a preregistered
fresh-direct versus backfill ablation at 1, 3, and 5 sessions. The existing observations
must be treated as hypothesis-generating and cannot be reused as a sealed holdout.

## 9. Execution Timing

Twenty-eight persisted decisions were matched to fills:

- Median decision-to-fill time: 6.5 minutes.
- Mean decision-to-fill time: 8.7 minutes.
- July 6 initial-buy mean: approximately 19.7 minutes.
- Matched notional: $16,773.76.
- Fill-versus-decision-reference proxy: $55.24 unfavorable, or approximately 32.1 bps
  notional-weighted.
- Adverse direction: 19 of 28 matches.

This is an implementation-shortfall proxy, not measured NBBO slippage. The decision
reference may itself be stale or non-tradable. The replacement system must capture bid,
ask, sizes, feed, and timestamps at forecast, allocation, intent, pre-submit, broker
acknowledgement, and fill before deciding whether market or marketable-limit execution is
better.

## 10. Runtime Defects That Can Distort Decisions

Source audit identified the following release blockers:

1. Position refresh computes broker marks but preserves an existing nonzero last-price
   cache, often the fill price. Strategy risk then consumes the stale scalar.
2. The daemon passes scheduler values `FULL`, `MONITOR`, or `IDLE` through a parameter
   that is also used to infer live execution mode. Live safeguards consequently evaluate
   as disabled.
3. The full-cycle-completed date is persisted before planned orders are safely settled.
   A crash can suppress the day's buys; a failed persistence can duplicate a full cycle.
4. Client order IDs are based on symbol, date, and side rather than stable intent plus
   revision. Legitimate same-day successors can collapse into an unrelated prior order.
5. Partial fills are not applied delta-idempotently throughout positions, cash, and
   restart reconciliation.
6. Submitted sells can free slots and projected cash before settlement, causing clipped,
   rejected, or mis-sized replacement buys.
7. The API live-state fallback sets inception value to current equity when one history
   request is empty, incorrectly displaying total P&L as zero.
8. Decision logging is best-effort and swallows all errors, so it cannot serve as a
   real-money audit boundary.

## 11. Required Strategy Change

The selected approach is benchmark replacement with a guarded challenger:

```text
Initial promoted target:
  2% operational cash
  58-98% SPY residual
  up to 40% fresh-direct active stocks

Long-run permitted range after new evidence:
  2% operational cash
  18-58% SPY
  40-80% active stocks
  up to 10 active positions
  8% maximum per active position
```

The 40% active level is a promoted target, not a forced floor. If fewer than five
independently eligible 8% positions exist, the unused amount remains in SPY. Moving to
60% or 80% active requires new, unseen, net-of-cost evidence and operational promotion;
current live history cannot authorize it.

Initially, only a current-cycle direct forecast may emit an alpha order. Legacy backfill,
scheduled votes, breakout overrides, propagation-only candidates, reserved slots, and
high-conviction bypasses remain research-only. Every incumbent must be re-underwritten at
its forecast horizon; an incoming replacement must beat the incumbent after cost, tax,
uncertainty, and an anti-churn hurdle.

## 12. Immediate Safety Decision

The read-only API showed `runCommand=true`. This audit did not change it and performed no
trading action. Before implementation or further legacy trading:

1. Set `runCommand=false`, cancel open entry orders, and verify no order-generating
   process remains active.
2. Record an operator disposition for every current holding.
3. Fix fresh marks, execution/scheduler mode separation, flow-adjusted inception and
   drawdown truth, and durable decision logging.
4. Reconcile all existing positions, fills, lots, manual actions, and open orders into
   the RethinkDB-only state model.
5. Prove the fresh-direct challenger in observe, shadow portfolio, and Alpaca paper
   stages before requesting `LIVE_40`.

## 13. Limitations

- This is a short live sample with only 46 orders and 20 exits.
- Symbol horizon observations overlap and are not independent.
- IEX bars do not represent the consolidated U.S. market.
- The implementation-shortfall proxy does not use contemporaneous NBBO snapshots.
- FIFO reconstruction is an economic attribution ledger, not an assertion about the tax
  lots Alpaca will report.
- Other accounts, IRAs, and spouse transactions are not visible for wash-sale analysis.
- No analysis can guarantee future outperformance.

## 14. Primary References

- [Alpaca portfolio history](https://docs.alpaca.markets/us/v1.1/reference/getaccountportfoliohistory-1)
- [Alpaca account activities](https://docs.alpaca.markets/us/docs/account-activities)
- [Alpaca order behavior](https://docs.alpaca.markets/us/v1.4.2/docs/orders-at-alpaca)
- [Alpaca historical stock data](https://docs.alpaca.markets/us/docs/historical-stock-data-1)
- [IRS Publication 550](https://www.irs.gov/publications/p550)
- [Bailey and Lopez de Prado: Deflated Sharpe Ratio](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551)
