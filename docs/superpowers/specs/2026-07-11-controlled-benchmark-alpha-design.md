# Controlled Benchmark-Relative Alpha Portfolio

**Date:** 2026-07-11

**Status:** Approved (auto-approval granted after interactive design review)

**Target instance:** `alpaca-main`

**Strategy:** Strategy 179, Graph Nexus

**Risk profile:** Controlled

## 1. Executive Decision

IntelliStock will become a benchmark-replacement portfolio rather than a cash-plus-stock
picker. Capital not assigned to qualified active positions remains in SPY, subject to
the drawdown circuit. Graph Nexus will produce horizon-specific forecasts and will no
longer directly authorize orders. A separate allocator, risk engine, and execution layer
will convert forecasts into target weights.

Normal target allocation:

```text
2% operational cash
18-58% SPY
40-80% active stocks
up to 10 active positions, 8% maximum each
```

The 40% active target is not permission to manufacture trades. Stale data, insufficient
qualified forecasts, risk-off state, tax constraints, or a circuit breaker may leave
more capital in SPY or cash.

This design cannot guarantee outperformance. Its purpose is to make excess return
measurable, prevent operational defects from masquerading as strategy behavior, and
require out-of-sample evidence before increasing real-money exposure.

## 2. Production Research Findings

The findings below were derived from the production API, Alpaca account history,
RethinkDB records, current source code, and live logs on 2026-07-11. Secrets and raw
authenticated responses are intentionally excluded.

### 2.1 Performance and attribution

- The one-month portfolio series fell from $6,143.62 to $5,949.05: -$194.57 or -3.17%.
- The user's comparison showed SPY at approximately +2.33%, producing about -5.50
  percentage points of active underperformance. Exact close-aligned comparisons vary
  slightly with feed and timestamp alignment.
- Since the approximately $6,000 funding baseline, the account was about -0.85% while
  SPY gained about +2.69%, an active gap of roughly -3.54 percentage points.
- Current capital was approximately 72% in positions and 28% in cash. In a rising market,
  idle cash creates benchmark drag even when no stock selection decision is made.

### 2.2 Trade evidence

- Alpaca contained 43 filled orders since June 1: 25 buys and 18 sells across 25 symbols.
- FIFO episode reconstruction produced 9 winning and 9 losing exits, -$18.07 realized
  P&L, approximately -$40.34 current unrealized P&L, and a 0.94 profit factor.
- CRWV and DKNG were the largest completed losses. MRNA was the largest current loss at
  the research cutoff.
- The decision table contained only 25 rows and began after trading had already started.
  Eight July 6 exits had non-bot client-order identifiers. `LiveDecisionAudit` was empty,
  so the system cannot fully attribute every trade, hold, rejection, and manual action.

### 2.3 Forecast-horizon hypothesis

For the 25 observed entries, raw IEX-bar counterfactuals showed approximate mean excess
return versus SPY of:

| Horizon | Mean excess return | Sample |
|---|---:|---:|
| Same day | -0.32pp | 25 |
| 1 trading day | +1.32pp | 25 |
| 3 trading days | +1.18pp | 25 |
| 5 trading days | -1.40pp | 17 |
| 10 trading days | -1.16pp | 14 |

A simplified three-day exit counterfactual produced approximately +$422 versus about
-$58 actual realized plus current unrealized P&L. This is a small, overlapping,
capital-unconstrained sample and is not a deployable rule. It is evidence that the
current 15-30 day hold and rotation constraints may be mismatched to a 1-3 day signal.

### 2.4 Candidate and signal quality

- The live backfill queue contained 59 candidates; 50 still required price backfill.
- In a short July 7-10 survivor sample, direct-signal candidates had approximately
  +1.14pp excess return while propagation-expansion candidates had about -1.47pp.
- Queue score rank had no useful positive relationship with subsequent return in that
  sample. Propagation-only candidates therefore remain research-only until they prove
  incremental value on unseen data.
- Several executed reasons contradicted entry eligibility: two purchases stated "No
  graph signal," another reported a quality-filter block, and MRNA included negative
  trend evidence. Execution gates are not currently invariant.
- ML output displayed neutral 0.50/0.50 probabilities while `nexus_ml_enabled=false`.
  Disabled model legs must contribute neither score nor apparent confidence.

### 2.5 Critical live-price defect

The July 10 MONITOR loop repeatedly evaluated positions at morning prices even after
successful account and position refreshes. MRNA remained near a displayed -6% while its
broker close was near -16%. Similar intraday reversals occurred in OKTA, S, QLYS, and KNX.

The root cause is an overloaded price cache:

- `AlpacaAdapter.refresh_positions` derives current broker marks but only writes them
  when `_last_prices` has no existing value. Fill-time prices therefore survive future
  REST refreshes (`backend/broker_adapters/alpaca.py:1191-1197`).
- `_ensure_prices_include_positions` consumes that cache before requesting a fresh
  outbound price (`backend/broker.py:2434-2445`).

Portfolio peak and drawdown state were consequently stale. This is a real-money safety
defect: live equity trading should remain paused until current marks, price timestamps,
and drawdown state are fixed and replay-tested.

### 2.6 Reliability and telemetry

- Live logs contained 77 stale-tier warnings, 64 zero-bar responses from Alpaca IEX,
  one hard price timeout, and 31 RethinkDB/changefeed connection errors.
- MONITOR notifications repeatedly reported zero held positions while eight were held.
- Several Nexus API endpoints returned HTTP 500. Their handlers scan entire history
  tables with a string split/filter instead of using the existing base-instance index
  (`backend/interactive_utils.py:6925-6959`).
- `GraphNexusTradeContexts` contained more than 100,000 rows and the current history
  scope alone contained tens of thousands. Analytics queries need compound indexes,
  bounded retention, and explicit run scoping.
- Of 655 current-scope trade-outcome rows, 583 used `unknown` intent. The intent allowlist
  omits values emitted by the strategy, and the writer persists every non-`hold` value,
  including `unknown` (`backend/strategies/graph_nexus_analysis.py:621-650` and
  `:9613-9668`). The resulting hit-rate metric is not trustworthy.

### 2.7 Backtest validity

- The AI backtest agent uses one fixed 2025-11-10 through 2026-02-24 window
  (`backend/engines/ai_backtest_engine.py:45-50`).
- It asks an LLM to keep strategies based primarily on absolute profit rather than
  benchmark-relative, risk-adjusted evidence (`backend/engines/ai_backtest_engine.py:1106-1132`).
- Strategy 179 had 483 stored results, of which about 80% stopped before completion.
  Finished results are therefore a selected subset.
- June replays ranged from -2.32% to +10.37%, including materially different outcomes
  from superficially similar configurations. Model/data nondeterminism is large.
- Backtest result rows lack first-class benchmark return, active return, information
  ratio, beta, turnover, and modeled execution-cost fields.

### 2.8 Credential incident

Historical `BacktestResults.strategy_schema` documents contain complete strategy
configuration snapshots, including plaintext provider, brokerage, and database
credentials. The code copies the full strategy list into result rows
(`backend/broker.py:2721-2727`, `:6514-6536`, and `:7216-7239`).

All credentials found in those historical snapshots must be treated as compromised:
rotate them, purge existing plaintext, persist only secret references, and add redaction
at storage and API/log boundaries. No credential values may enter tests, fixtures,
design documents, or migration logs.

## 3. Success Contract

Primary objective:

```text
Annualized net portfolio return >= SPY total return + 10 percentage points
```

Promotion thresholds:

- Median annualized active return across unseen windows at least +8 percentage points.
- Target annualized active return +10 percentage points after modeled costs.
- 90% bootstrap lower confidence bound for active return above zero.
- Information ratio at least 0.75.
- Maximum portfolio drawdown no greater than 15%.
- Portfolio beta normally between 0.8 and 1.1.
- Positive active return in at least 60% of unseen quarters.
- No margin or gross exposure above 100%.
- Operational and audit gates in Section 10 pass with no critical exceptions.

Returns are compared at matching timestamps against dividend-adjusted SPY total return.
Both pre-tax and estimated after-tax results are reported; the primary research metric
remains pre-tax net-of-execution-cost active return so tax assumptions cannot hide a
weak signal.

## 4. Portfolio Construction

### 4.1 Benchmark-replacement model

The allocator starts from the benchmark allocation and replaces SPY weight with qualified
stocks. An 8% stock target is funded by approximately an 8% reduction in SPY. Exiting the
stock normally restores SPY weight.

Constraints:

| Constraint | Value |
|---|---:|
| Operational cash | 2% |
| Normal active target | 40-80% |
| Maximum active positions | 10 |
| Maximum single-stock weight | 8% |
| Maximum active sector weight | 20% |
| Gross exposure | 100% |
| Margin | Disabled |
| Normal beta target | 0.8-1.1 |

The allocator uses forecast excess return, calibrated confidence, volatility, correlation,
sector exposure, tax state, and turnover cost. It does not use a position-count target as
a reason to buy an otherwise ineligible candidate.

### 4.2 Tax-aware SPY sleeve

- The lowest 18% SPY weight is the stable core. The additional 0-40% is the flexible
  benchmark sleeve.
- Active buys and sells are netted before SPY orders are generated.
- The flexible sleeve is normally rebalanced weekly or after at least five percentage
  points of allocation drift, rather than on every stock order.
- A local FIFO-compatible lot ledger is built from actual fills and reconciled with
  broker records. Alpaca's reporting remains authoritative for tax forms.
- A discretionary SPY sale that would realize a loss is blocked if SPY was acquired in
  the preceding 30 calendar days.
- After an allowed SPY loss sale, discretionary SPY repurchases are blocked for 31 days.
- The system does not automatically substitute VOO or IVV as a purported wash-sale
  workaround. "Substantially identical" treatment is a tax determination, not a trading
  heuristic.
- Transactions in other accounts, IRAs, or a spouse's account are not observable. The
  UI and audit record state this limitation and allow operator-supplied blackout dates.
- Tax guards never block emergency risk reduction.

Wash sales are not illegal; the loss is generally disallowed at that time and reflected
in replacement basis. This design reduces avoidable occurrences but is not tax advice.

## 5. Component Boundaries

### 5.1 MarketMarkService

Owns current price truth. Entry cost, fill price, and market mark are separate concepts.
Every mark has:

```text
symbol, price, observed_at, received_at, source, feed, age_seconds, quality
```

Resolution order:

1. Alpaca streaming quote/trade with a valid exchange timestamp.
2. Alpaca REST latest quote/trade.
3. Broker-position current price or market-value/quantity mark.
4. No valid mark: fail closed for exposure increases and alert.

During the regular session, exposure increases require a quote or trade observed within
60 seconds. A broker-position fallback may be at most 120 seconds old. A risk reduction
may proceed from independently refreshed broker state even when the primary mark is
degraded, but the degraded source and age must be recorded.

Historical bars are not a substitute for a current quote. Risk reduction remains
available through independently refreshed broker state. An out-of-process watchdog
compares strategy marks, broker marks, positions, and account equity.

### 5.2 Forecast producers

Graph Nexus and a deterministic momentum/event challenger implement one interface and
emit forecasts, not orders:

```text
symbol
as_of
horizon_trading_days
expected_excess_return
probability_outperform
confidence
evidence_class
feature_version
model_version
data_snapshot_id
eligibility
gate_reasons
```

Graph direct evidence and graph propagation are distinct evidence classes. Propagation-
only forecasts are shadow-only until incremental out-of-sample value is demonstrated.
LLM output may contribute features but never bypasses typed eligibility gates.

### 5.3 PortfolioAllocator

Consumes fresh marks, forecasts, current positions, SPY lots, exposure, and risk state.
It produces a complete target portfolio plus the reason for each delta. It has no broker
credentials and cannot submit orders.

### 5.4 RiskAndExecutionEngine

Validates target deltas against freshness, position, sector, beta, turnover, drawdown,
tax, buying-power, and idempotency constraints. It generates broker orders with stable
client-order IDs and records every rejection. No strategy-specific path can bypass it.

### 5.5 Ledgers and evaluator

Separate append-only records store predictions, gates, target allocations, order intents,
broker orders, fills, position episodes, tax lots, and horizon outcomes. Each record has
`run_id`, `origin`, instance, strategy/config version, model version, data snapshot, and
timestamps. Live, shadow, paper, lookback, and backtest origins never overwrite one another.

## 6. Signal and Position Lifecycle

### 6.1 Prediction horizons

Both challengers forecast 1-, 3-, and 5-trading-day SPY-relative return. Model selection
chooses a horizon only from training/validation data. The sealed test set is used once.

### 6.2 Eligibility

An entry is ineligible if any required data is stale, price quality is insufficient,
quality checks fail, the evidence is absent, a negative-evidence limit is breached, the
reason code is unknown, or the predicted edge does not exceed estimated cost and risk.
"No graph signal" can never become a Graph Nexus order. Backfill, reserved slots, and
break-glass paths use the same invariant gates.

### 6.3 Position sizing

Qualified positions generally begin near 4% and may scale to 8%. Sizing considers
expected excess return, probability calibration, volatility, correlations, sector
capacity, active allocation, and the per-position loss budget. The allocator may hold
fewer than ten positions.

### 6.4 Re-underwriting and exits

- Forecasts refresh once per trading day from point-in-time data.
- Market marks and safety state update continuously.
- At forecast-horizon expiry, a position returns to SPY unless a newly generated
  forecast independently requalifies it.
- There is no 15-30 day minimum hold for forecast expiry, risk exit, or rotation.
- Static partial-profit tiers are disabled initially. Target-weight changes or complete
  exits are driven by the new forecast and risk state.
- Rotation requires the incoming expected excess return to exceed the incumbent after
  estimated cost, tax effect, and an anti-churn hurdle.

### 6.5 Loss and drawdown budgets

- Initial stop distance is approximately 1.5 ATR, constrained to a 5-8% price range.
- Planned loss at the stop is at most approximately 0.6% of portfolio equity per position.
- At 8% portfolio drawdown, the active ceiling falls to 40% and beta/exposure are reduced.
- At 12% drawdown, new active entries stop and exposure is reduced under a deterministic
  liquidation schedule.
- At 15% drawdown, the kill state cancels entries, liquidates active positions and
  benchmark exposure to cash, and returns both forecast models to shadow mode.
- Emergency risk actions override wash-sale and normal turnover guards.
- Material rolling underperformance versus SPY freezes promotion even when absolute
  drawdown is below the thresholds.

## 7. Failure Semantics

| Failure | Required behavior |
|---|---|
| Price exceeds freshness SLA | Cancel/block exposure increases; alert; retain broker-backed risk reduction |
| Stream disconnects | Reconnect with bounded backoff; switch to REST; mark quality degradation |
| RethinkDB unavailable | Continue risk monitoring from local durable state; queue analytics writes |
| LLM unavailable | Graph Nexus emits no tradable forecast; deterministic challenger remains independent |
| Broker state unavailable | Cancel outstanding entries where possible; prohibit new exposure; page operator |
| Unknown reason or intent | Reject persistence and trading action; raise a schema alert |
| Reconciliation mismatch | Freeze affected symbol; do not infer ownership or quantity silently |
| Secret detected in persistence payload | Reject write, redact log, and raise security alert |

Discord, analytics endpoints, and the LLM are never dependencies for a risk exit.

## 8. Data and API Remediation

- Add compound indexes for base instance, run/origin, date, and symbol.
- Replace full-table split/filter handlers with indexed, bounded queries.
- Introduce retention and aggregation policies for candidate contexts and horizon series.
- Replace `unknown` coercion with typed prediction/effect enums and reject invalid values.
- Persist outcomes only for registered predictions or executed position episodes, not
  arbitrary non-hold candidates.
- Populate a complete decision ledger for eligible, rejected, held, rotated, and manual
  actions.
- Correct MONITOR held/sell/hold counters and expose mark age/source in live state.
- Preserve live inception equity and high-water marks across restarts; reconcile them
  daily with Alpaca portfolio history and cash activities.
- Keep the operational risk path independent from analytics-table availability.

## 9. Security Remediation

1. Pause live trading and rotate every credential present in historical strategy schemas.
2. Replace secret values in strategy configuration with secret references.
3. Add a recursive persistence sanitizer with an explicit allowlist and forbidden-key
   detector before BacktestResults, logs, APIs, or audit records are written.
4. Purge or irreversibly remove secret fields from existing BacktestResults and any
   derived exports/backups under the applicable retention policy.
5. Remove key prefix/suffix logging; report only provider name and credential presence.
6. Add repository, database, and API tests that insert canary secrets and prove they never
   appear in persisted or returned payloads.

## 10. Validation Program

### 10.1 Registered experiment matrix

- SPY total-return control.
- Deterministic momentum/event challenger.
- Graph Nexus direct evidence only.
- Graph Nexus direct plus propagation.
- Forecast horizons of 1, 3, and 5 trading days.
- Active ceilings of 40%, 60%, and 80%.

Every experiment is registered before execution. Stopped, failed, and rejected results
remain visible. Hyperparameter selection occurs inside training/validation folds, not on
the final holdout.

### 10.2 Point-in-time walk-forward protocol

- Use at least 24 months of point-in-time history covering bull, bear/high-volatility,
  and sideways periods. If this history is unavailable, production promotion is blocked.
- Freeze data snapshots, prompts, model outputs, code version, and execution assumptions.
- Use rolling train/calibration/test windows with a purge and embargo at least as long as
  the maximum forecast horizon.
- Preserve one sealed final holdout and evaluate it once per registered model family.
- Model next-tradable-price fills, spread, slippage, latency, dividends, partial fills,
  and rejected orders.
- Report active return, alpha/beta, information ratio, Sharpe/Sortino, Deflated Sharpe,
  max drawdown, Calmar, exposure, turnover, capacity, tax estimate, and performance by
  evidence class, regime, and horizon.
- Use bootstrap confidence intervals and multiple-testing controls. A high headline P&L
  cannot compensate for a failed statistical or drawdown gate.

### 10.3 Operational gates

- All predictions, targets, orders, and fills reconcile.
- No position is evaluated on a stale or timestamp-free mark.
- Drawdown state survives restart and matches broker equity within tolerance.
- All failure-mode integration tests pass.
- Promotion to 80% active requires consolidated SIP-quality market data or a documented
  equivalent; the single-exchange IEX feed is not sufficient for that exposure tier.
- No persisted or API-returned secret canaries.
- No unknown prediction, decision, order, or outcome enum values.
- Shadow and paper behavior use the same allocator and risk code as live mode.

## 11. Rollout

1. **Containment:** pause new live equity orders; rotate credentials; preserve forensic
   records; repair the market-mark and drawdown defects.
2. **Measurement foundation:** implement immutable ledgers, indexed queries, benchmark
   accounting, and a deterministic research harness.
3. **Research:** run the registered ablations and sealed walk-forward program.
4. **Shadow:** at least four weeks and 100 qualified forecasts with no safety breach.
5. **Paper:** at least six weeks and 50 completed positions with positive active return
   and every promotion gate satisfied.
6. **Live 40% active:** begin at the user-approved floor only after all prior gates pass.
7. **Live 60% active:** require at least eight incident-free live weeks plus continuing
   positive active performance and drawdown compliance.
8. **Live 80% active:** require at least six live months, 150 completed positions, and
   continued compliance with the full success contract.

Any critical data, reconciliation, security, or risk defect returns the affected model to
shadow mode. Exposure increases are never automatic merely because time has elapsed.

## 12. Testing Strategy

- Unit tests for price-source separation, mark freshness, fallback precedence, intent
  schemas, horizon expiry, allocation constraints, loss budgets, tax windows, and
  benchmark math.
- Property tests for gross exposure, name/sector caps, cash accounting, idempotent order
  generation, and "blocked means no order" invariants.
- Replay tests built from July 10 broker/log events proving stale fill prices cannot drive
  MRNA or portfolio drawdown decisions.
- Integration tests for stream loss, REST degradation, RethinkDB outage, LLM outage,
  broker timeout, restart continuity, partial fills, manual orders, and kill-state recovery.
- Security tests with canary keys through strategy snapshot, backtest persistence, logs,
  and API responses.
- Golden walk-forward fixtures whose data, model outputs, trades, and metrics are
  deterministic across reruns.

## 13. Non-Goals

- Guaranteeing a specific return.
- Enabling margin, options, short selling, or leveraged ETFs.
- Treating the current 25 entries as sufficient statistical evidence.
- Blindly enabling the disabled ML leg.
- Assuming ETF substitution eliminates wash-sale risk.
- Tuning the current 446-key configuration before measurement and safety defects are fixed.

## 14. External References

- [IRS Publication 550: wash sales](https://www.irs.gov/publications/p550)
- [Alpaca wash-sale guidance](https://alpaca.markets/support/wash-sale)
- [Alpaca market-data feeds](https://alpaca.markets/sdks/python/api_reference/data/enums.html)
- [Alpaca stock-data streaming](https://alpaca.markets/sdks/python/api_reference/data/stock/live.html)
- [Bailey and Lopez de Prado: Deflated Sharpe Ratio](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551)
- [S&P DJI SPIVA U.S. scorecard](https://www.spglobal.com/spdji/en/spiva/article/spiva-us/)
