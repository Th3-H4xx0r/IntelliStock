# Controlled Benchmark-Relative Alpha Plan: Adversarial Review

**Date:** 2026-07-14

**Revised:** 2026-07-18 - validated against the complete Alpaca main and RethinkDB baseline

**Stack constraint:** RethinkDB is the sole application persistence database.

**Artifacts reviewed:**

- `docs/superpowers/specs/2026-07-11-controlled-benchmark-alpha-design.md`
- `docs/superpowers/plans/2026-07-11-controlled-benchmark-alpha.md`
- `docs/superpowers/reviews/2026-07-18-alpaca-main-performance-forensics.md`
- Current repository at `b2753374`

**Verdict:** **NO-GO for implementation as written and NO-GO for live restart.**

The plan has the right high-level separation of forecasts, allocation, risk, and execution.
It also correctly keeps live trading paused, treats SPY as the residual benchmark sleeve,
caps active exposure at 80%, and refuses to promise outperformance. However, it is not yet
an executable safety contract. Ten blocking gaps can either prevent the promotion evidence
from being produced, create duplicate or unfunded orders, make a kill state ineffective, or
allow a result to pass gates that do not correspond to the deployed system.

This review is deliberately hostile. It assumes stale and contradictory market data,
broker acknowledgement ambiguity, partial fills, process death, database failure, manual
trades, corporate actions, contaminated historical data, and an optimizer that exploits
every ambiguous metric.

## 1. Executive Decision

Do not execute the original `0 -> 18` sequence unchanged. Preserve the containment work,
repair the immediate stale-price and credential defects, then prove that trustworthy alpha
evidence can be generated before building the full live allocator and execution runtime.

Required decision by area:

| Area | Decision | Reason |
|---|---|---|
| Live equity trading | Keep paused | The known stale-mark defect remains a real-money safety issue until replay-tested |
| Credential response | Execute immediately | Historical plaintext credentials must be treated as compromised |
| Market-mark repair | Execute immediately | It is required even if the alpha program is later rejected |
| Research/evaluation foundation | Rewrite and move earlier | The current plan defines outcomes but no job that produces them |
| Allocator/execution runtime | Hold | Cost, order-state, migration, and recovery contracts are incomplete |
| LIVE_40 promotion | Block | The current gates can be populated with dependent or mismatched evidence |
| LIVE_60/LIVE_80 | Block | They inherit all LIVE_40 defects and add market-data/capacity risk |

## 2. What Should Be Retained

The following decisions survive adversarial review:

- Graph Nexus must emit forecasts, never orders.
- One portfolio allocator and one order authority must own all target changes.
- Unused active capital should normally remain in SPY rather than idle cash.
- The 40% active level is a target only when enough qualified forecasts exist; it cannot
  weaken eligibility.
- Active exposure is capped at 80%, ten names, 8% per name, and 20% per active sector.
- Margin, shorting, leveraged ETFs, and options remain out of scope.
- Propagation-only Graph evidence remains shadow-only until a direct ablation proves
  incremental unseen alpha after costs.
- Promotion must be bound to immutable artifacts and require an operator action.
- Tax rules never prevent emergency risk reduction.
- Live, paper, shadow, and backtest records must not overwrite one another.
- RethinkDB remains the sole application persistence database; outage handling uses
  deterministic broker reconciliation rather than a second database.
- The July 10 stale-price incident must become a deterministic regression fixture.

## 3. Severity Model

- **BLOCKER:** Can cause unintended real orders or exposure, make a safety promise false,
  make promotion evidence impossible to produce, or let invalid evidence authorize live
  capital.
- **HIGH:** Can materially bias alpha, drawdown, tax, or execution results, or create a
  likely operational failure under normal adverse conditions.
- **MEDIUM:** Does not alone authorize unsafe exposure but creates ambiguity, avoidable
  fragility, or an implementation trap.

## 4. Blocking Findings

### B01 - No component produces trustworthy horizon outcomes

**Attack:** The plan creates `HorizonOutcome` and `AlphaOutcomes`, then assumes calibration,
ablation, and promotion can consume them. No task owns an evaluator that waits for the
correct future session, obtains point-in-time stock and adjusted-SPY prices, resolves
delistings/corporate actions, and writes outcomes for both traded and untraded forecasts.

**Failure:** Task 10 cannot train honestly, Task 16 cannot compare forecast families, and
Task 17 can receive an empty, selected, or legacy-contaminated result set. Evaluating only
executed trades introduces policy selection bias.

**Required correction:** Add an `OutcomeEvaluator` task and module before calibration:

- Resolve every registered eligible and rejected forecast, not only filled positions.
- Use the exchange calendar already available in `backend/live_calendar.py` or a new strict
  wrapper that refuses its weekday fallback for research and live promotion.
- Define the exact entry observation and exit observation for 1, 3, and 5 trading sessions.
- Store raw prices, adjusted prices, timestamps, data source, corporate-action state,
  missing-data reason, and benchmark return.
- Make evaluation idempotent and revision-aware; a corrected market-data record produces a
  new version and never silently mutates prior evidence.

**Acceptance test:** A holiday, early close, split, symbol change, delisting, missing bar,
and nonexecuted forecast all resolve deterministically without using a future-known field.

### B02 - `data_snapshot_id` does not create point-in-time data

**Attack:** Task 16 rejects mutable data but provides no ingestion, snapshot, or universe
construction pipeline. Current Graph/news state and a current stock universe cannot be
retroactively labeled with a snapshot ID and become point-in-time.

**Failure:** Revised news, updated Neo4j edges, survivorship, ticker changes, or current
sector membership can leak into old dates and manufacture alpha. The stated 24-month
minimum is also insufficient for a 12-month train, 3-month calibration, multiple 3-month
tests, and a genuinely sealed multi-regime holdout.

**Required correction:** Add a data-manifest task before model work:

- Inventory which raw bars, news articles, Graph nodes/edges, event scores, sector data,
  fundamentals, and universes actually have immutable `known_at` timestamps.
- Content-hash every immutable input partition and model output.
- Maintain point-in-time membership including delisted and renamed securities.
- Refuse a feature when its historical availability cannot be proven.
- Use a future-only shadow holdout if earlier periods have already been repeatedly examined.

**Acceptance test:** Rebuilding an old experiment from its manifest after current Graph and
news data change produces byte-identical features, forecasts, and outcomes.

### B03 - There is no implementable equity execution-cost model

**Attack:** The allocator requires expected excess return to beat spread, slippage, tax,
and turnover cost; research claims next-tradable fills and partial fills; promotion targets
net active return. No task defines or implements those costs. Current equity backtests are
explicitly treated as commission-free in `backend/backtest_summary.py:76-85` and
`backend/portfolio_emulator.py:30-38`.

**Failure:** A high-turnover 1-3 day strategy can pass using frictionless fills. The plan
also omits fixed operating costs. At an account near $6,000, data, model, and
infrastructure subscriptions can consume a material percentage of capital before any
trading loss; each currently applicable cost must be read from its invoice or provider
contract rather than hard-coded in research.

**Required correction:** Add a versioned `EquityCostModel` before allocator or research:

- Directional bid/ask spread at decision time.
- Latency and market-impact slippage by order size versus displayed size and ADV.
- Partial-fill, cancel/replace, rejection, and missed-trade opportunity cost.
- Regulatory fees and any broker fees that actually apply.
- Fixed data/infrastructure cost reported both separately and amortized at current capital.
- Calibrate estimates against actual fills and reject promotion when realized slippage
  materially exceeds the registered model.

**Acceptance test:** A zero-alpha high-turnover strategy loses money after modeled costs,
and replayed actual fills reconcile predicted versus realized implementation shortfall.

### B04 - RethinkDB-only persistence needs explicit outage semantics

**Attack:** RethinkDB is the sole application database and the current `WALStore` writes
`LiveOrderWAL` directly to it with `conflict="error"` in
`backend/nexus_runtime_state.py:79-110`. If it is unreachable during an emergency, the
system cannot simultaneously guarantee a durable pre-submit event and guarantee that a
risk-reducing order is sent. Adding a second application database would not match the
IntelliStock stack.

**Failure:** A naive implementation either blocks an emergency sell because the WAL write
failed, or submits an unrecorded sell and later claims the audit was complete. A restart
during the same outage can also lose non-broker predictions and gate events.

**Required correction:** Keep RethinkDB authoritative and make the tradeoff explicit:

- Use hard-durability RethinkDB writes and verify table/index/cluster health before normal
  PAPER/LIVE order authority starts.
- During an outage, block every exposure increase and cancel open entry orders.
- Continue risk monitoring from direct broker state and the current process's last known
  risk state; a restart while RethinkDB remains down starts in `HALT`, never NORMAL.
- Permit only a deterministic, quantity-capped risk reduction. Re-read the broker position,
  cap the order at held quantity, and use a client-order ID derived from instance, risk
  episode, symbol, side, and target quantity.
- Treat Alpaca's order/fill history as the temporary authoritative record for that emergency
  action. On recovery or restart, scan recent orders by instance prefix and backfill missing
  RethinkDB WAL/fill rows.
- Mark non-broker analytics from a simultaneous database/process failure as an unrecoverable
  degraded interval. Promotion remains blocked until reconciliation is complete.

**Acceptance test:** With RethinkDB firewalled, a buy is blocked, entries are canceled, a
strictly reducing order uses the deterministic broker path, and reconnect/restart backfills
the exact order and fills without duplication. No report claims complete analytics for the
degraded interval.

### B05 - The order plan is a list, but execution is a state machine

**Attack:** Task 14 says it emits stock sells, one SPY order, then stock buys, while also
saying buys can use only confirmed sell fills. Those conditions cannot be satisfied by one
pure list built before any sell fills. It also omits outstanding-order reservations,
cancel/replace rules, cumulative partial-fill deltas, and a new cycle arriving while the
prior allocation is still executing.

**Failure:** The runtime can overspend, duplicate a target, buy before funding exists,
ignore an open order when calculating current exposure, or apply cumulative fill quantity
twice.

**Required correction:** Replace `list[OrderIntent]` execution with an explicit persisted
batch state machine:

1. Reconcile broker positions, balances, and all open/recent orders.
2. Persist target generation and reserve current open orders.
3. Submit reductions and funding sells.
4. Wait for terminal state or a bounded deadline; process cumulative fills monotonically.
5. Recompute positions, cash, taxes, marks, and constraints.
6. Submit only the newly affordable increase.
7. Reconcile final residuals and record why any target remained unmet.

**Acceptance test:** Kill the process after broker acceptance but before RethinkDB
`mark_submitted`, after a 37% partial fill, and during cancel/replace. Every restart adopts
the existing broker order by client ID and never exceeds the target or available cash.

### B06 - The independent watchdog cannot perform the promised kill action

**Attack:** The design promises KILL liquidates active stocks and SPY to cash. Task 6 gives
the watchdog a read-only/cancel-only client and explicitly denies it order submission.

**Failure:** If the main broker process is dead or wedged during a gap, the watchdog can
cancel entries and halt the instance but cannot reduce the exposure that triggered KILL.
The documentation would claim stronger protection than exists.

**Required correction:** Choose and document one honest model:

- Preferred: a tightly scoped emergency executor with separate credentials, a hardcoded
  reduce-only position reconciliation algorithm, strict symbol/quantity checks, normal
  RethinkDB audit writes, and deterministic broker-history recovery during a store outage; or
- Minimum: state clearly that watchdog KILL only halts/cancels, page an operator through
  two independent channels, and provide a tested manual liquidation runbook.

Do not give a general-purpose sidecar unrestricted order authority without independent
position reconciliation and quantity caps.

**Acceptance test:** Terminate the main broker process while positions remain and cross a
KILL fixture. The documented mechanism must either reduce positions or explicitly prove
and alert that human action is required.

### B07 - Drawdown state is not adjusted for external cash flows

**Attack:** Task 6 updates drawdown from raw account equity. Task 9 separately mentions
cash flows for returns but never changes the `RiskState` transition. A withdrawal looks
like a loss; a deposit can create a false high-water mark. Splits, dividends, fees, and
broker corrections can also distort the series.

**Failure:** A legitimate withdrawal can trigger HARD/KILL and liquidate the portfolio.
A deposit can later make a smaller market loss appear as a larger drawdown.

**Required correction:** Define a flow-adjusted risk equity series. Persist every broker
cash activity with effective time and type, reconcile it before changing the peak, and
quarantine unknown flows. Keep raw net liquidation value visible beside the adjusted
series. Corporate actions must not be inferred from price jumps.

**Acceptance test:** Deposits, withdrawals, dividends, fees, and a split do not create
false drawdown transitions, while an actual market loss still crosses 8/12/15% exactly.

### B08 - SHADOW mode contradicts containment and lacks a virtual portfolio

**Attack:** Task 15 says SHADOW evaluates alpha and then takes the labeled legacy path.
Task 18 says shadow runs with broker submission disabled at both runtime and preflight.
Those are different behaviors. The plan also records shadow intents but defines no virtual
fill engine or shadow equity curve.

**Failure:** One interpretation continues legacy real-money orders during an alpha safety
trial. The other produces no completed shadow portfolio, making active return, turnover,
drawdown, and implementation shortfall impossible to measure.

**Required correction:** Split the concepts:

- `OBSERVE`: alpha forecasts only; absolutely all equity submission disabled.
- `SHADOW_PORTFOLIO`: target allocation plus deterministic simulated fills from recorded
  quotes, with no broker submits.
- `LEGACY_COMPARE`: optional separately identified legacy counterfactual, never mixed with
  alpha evidence and never enabled on the contained live instance.

**Acceptance test:** Broker submit is replaced by an unconditional trap in shadow and is
never called. A complete virtual position, cash, fill, cost, and outcome ledger still
reconciles for the same period.

### B09 - Existing positions have no alpha migration contract

**Attack:** The account already holds positions. The current adapter has clean-room
classification and external-position quarantine, and boot reconciliation exists in
`backend/broker_adapters/alpaca.py:1781-1825`. The plan does not specify whether existing
positions are adopted, liquidated, quarantined, or assigned forecasts and tax lots when
PAPER/LIVE becomes sole order authority.

**Failure:** The first alpha cycle can treat legacy holdings as unexplained manual
contamination, liquidate them without a cost/tax decision, omit them from caps, or buy a
duplicate position.

**Required correction:** Add a one-time migration manifest per account:

- Reconcile broker positions, open orders, fills, basis, acquisition dates, and WAL origin.
- Classify each position as adopted, exit-only, or external/quarantined.
- Require an operator decision and reason for every unmatched position.
- Seed tax lots and risk state without inventing unavailable basis.
- Block alpha order authority until quantity reconciliation is exact.

**Acceptance test:** Start PAPER/LIVE with legacy positions, an external manual position,
and an open partial order. No symbol is omitted, double-counted, or silently attributed to
a forecast.

### B10 - Promotion identity is both incomplete and self-invalidating

**Attack:** Task 17 binds promotion to a `data_snapshot` hash and says any data hash change
expires approval. Live input data changes every cycle, so a literal implementation
invalidates promotion on the first new quote. If implementers instead ignore live data,
the hash can fail to cover the deployed training set, dependencies, or artifact.

**Failure:** LIVE either refuses to run after one cycle or runs code/data different from
what was approved. The plan also checks promotion at startup but does not define continuous
automatic demotion when operational gates fail later.

**Required correction:** Separate identities:

- Immutable training-data manifest hash.
- Feature and model artifact hash.
- Source commit plus dirty-tree hash, dependency lock hash, and deployed image digest.
- Runtime risk/allocation config hash.
- Cost-model version and broker/data-provider capability manifest.
- Per-cycle input snapshot IDs for audit only, not promotion invalidation.

Re-evaluate operational promotion predicates continuously. A stale feed, reconciliation
error, invalid model artifact, or durability failure must block increases immediately and
demote according to a persisted state machine.

**Acceptance test:** New ordinary quotes do not expire a valid model approval, while a
one-byte model, config, dependency, image, or training-manifest change does.

## 5. High-Severity Findings

### H01 - A scalar market mark is insufficient for execution

The proposed `MarketMark` stores one price. An execution decision needs at least bid, ask,
sizes, exchange/tape, conditions, session, and purpose. Valuation can use a validated
midpoint; a buy cost uses ask; a sell liquidation estimate uses bid. Trades, quotes, and
broker-derived marks are not interchangeable merely because one is newer.

**Correction:** Introduce a bid/ask-aware quote type, a separate trade type, condition and
LULD/halts validation, and purpose-specific price resolution. Preserve source sequence and
both exchange and receive timestamps. Reject crossed/locked or invalid quotes. Alpaca's
stream exposes bid, ask, sizes, conditions, tape, and LULD channels; consume them instead
of discarding them into one scalar.

### H02 - The freshness SLA and IEX policy are too weak for live capital

Sixty seconds for a new buy and 120 seconds for a broker fallback are long during a fast
move. Fresh IEX data is still one-exchange data. Alpaca documents IEX as a single exchange
and SIP as consolidated coverage; the Basic plan also limits stock websocket subscriptions
to 30 symbols. Held positions plus a changing candidate set can exceed that cap.

**Correction:** Derive SLAs by order purpose and volatility, usually seconds rather than a
minute for increases. Require consolidated or broker-equivalent bid/ask data for every live
tier unless a measured fallback passes slippage and safety gates. Add subscription-budget,
rate-limit, reconnect, and candidate-churn tests.

### H03 - Stop-distance math is not a stop mechanism

Task 14 computes a 5-8% distance but does not define entry basis, ATR timestamp, persisted
stop price, split adjustment, gap behavior, order type, replacement, or what happens while
the monitor is down. A polling rule is not equivalent to a broker-held stop, and a stop-limit
can remain unfilled through a gap.

**Correction:** Define persistent per-lot/position `StopState`, election source, update
policy, broker-versus-synthetic stop choice, price collar, retry/escalation, and overnight
semantics. Report that stop price is not a guaranteed fill.

### H04 - Model/data outage is treated like negative conviction

At horizon expiry, absence of a fresh eligible forecast sets target to zero. A temporary
LLM, Graph, or data outage can therefore liquidate every affected holding even though no
new negative evidence exists.

**Correction:** Distinguish `NEGATIVE`, `EXPIRED_VALID`, `UNAVAILABLE`, and `STALE_SOURCE`.
Block increases on unavailable data, retain bounded risk-managed exposure for a documented
grace interval, and use a staged fallback rather than an instantaneous infrastructure-driven
portfolio rotation.

### H05 - The allocator contract omits constraints promised by the design

The design names correlations, turnover, tax, and costs. `build_allocation` receives no
correlation matrix or cost model and its deterministic ratio uses only return/volatility.
`active_floor=.40` is present even though implementation is told not to enforce it. A
future maintainer can reasonably implement the wrong semantics.

**Correction:** Remove or rename the floor as `normal_active_target_min`; include covariance
or an explicit conservative correlation stress, cost model, open-order exposure, and data
quality. Missing sector/beta/correlation data must go to a capped `UNKNOWN` bucket or make
the candidate ineligible.

### H06 - SPY replacement and the wash-sale block can deadlock each other

With 1-3 day stock horizons, exits repeatedly buy SPY and later entries repeatedly sell it.
Blocking every loss sale after a prior-30-day SPY acquisition can strand the flexible
sleeve. Weekly/5pp batching can instead leave exited stock proceeds in cash, creating the
same benchmark drag the design is meant to eliminate.

The tax model also covers only SPY even though active names can be sold at a loss and
reacquired within 30 days. IRS matching can be partial by share quantity and carries basis
and holding-period adjustments; it is not only a yes/no block.

**Correction:** Simulate the tax policy inside research, report opportunity cost, support
per-symbol replacement-share matching and adjusted basis, and define whether the system
accepts a wash sale when avoiding it would violate the investment/risk mandate. Keep the
cross-account limitation explicit. Obtain tax-professional review before calling the
ledger tax-correct.

### H07 - Forecast and trade counts are not independent samples

One hundred same-day cross-sectional forecasts can be one market event, not 100 independent
observations. Overlapping 1/3/5-day outcomes are serially dependent. A fixed five-day block
bootstrap and 50 paper positions can substantially understate uncertainty.

**Correction:** Cluster outcomes by forecast date, use block lengths justified by measured
autocorrelation and maximum horizon, report effective sample size, and base primary gates
on unseen daily portfolio returns. Shadow and paper counts are operational gates, not proof
of annualized alpha. Do not annualize a few weeks into a promotion statistic.

### H08 - One isotonic calibrator cannot safely produce both probability and return

Task 10 asks `CalibratedModel.predict` for probability of outperforming and expected excess
return from one raw score. These are different targets. One hundred pooled observations is
too weak when split across evidence class, horizon, regime, and time.

**Correction:** Use separate cross-fitted probability and conditional-return estimators,
with shrinkage toward zero and uncertainty intervals. Calibrate only on past folds, test
reliability and return error out of sample, and make allocation use a conservative lower
bound after costs rather than a point estimate.

### H09 - The sealed holdout is probably already contaminated

Strategy 179 has hundreds of historical trials, and July trade/counterfactual results have
already influenced the 1/3/5-day design. Counting only newly registered experiments in the
Deflated Sharpe calculation understates researcher degrees of freedom. A previously viewed
period is not sealed because it is assigned a new experiment ID.

**Correction:** Include prior strategy/configuration searches in the trial ledger where
possible, treat all viewed historical periods as development data, and reserve future data
for the true holdout. A material feature-family change consumes a new future holdout; it
cannot reuse the old approval.

### H10 - Universe, liquidity, factor, and corporate-action risks are absent

The plan caps sector and beta but does not define the tradable universe, price/ADV/spread
minimums, IPO seasoning, halts, earnings risk, delistings, splits, dividends, or unknown
sector handling. Ten correlated high-beta names can satisfy a nominal sector cap while
sharing the same momentum/liquidity factor.

**Correction:** Register point-in-time universe rules and minimum liquidity, add corporate
action handling, and report factor and stress exposure beyond beta. Add portfolio shocks
for correlated gaps, volatility spikes, and spread widening.

### H11 - The account-regulation model is dated and incomplete

The repository still imports `PDTRestricted`, but Alpaca deprecated PDT and day-trading
buying-power fields on 2026-07-06 under the new intraday margin framework. The plan says
"no margin" without defining the current Alpaca account mode, intraday margin fields,
settled funds, open-order reserves, or how T+1 settlement affects same-cycle reuse.

**Correction:** Query and persist the current account capability schema at startup. Enforce
no debit and no leverage from actual cash/non-leverage fields, not legacy PDT assumptions.
Add broker-version contract tests and fail closed when required account fields disappear.

### H12 - Promotion metrics are ambiguous and can be optimized around

`median annual active >=8pp`, `target active >=10pp`, and full-portfolio active return are
not precisely defined. A 40% sleeve needs much more underlying stock-selection alpha than
an 80% sleeve to add the same 10 percentage points to the whole portfolio. Beta near one
can also be achieved by the SPY residual while the stock sleeve has extreme risk.

**Correction:** Report and gate both total portfolio and active-sleeve return/risk, define
every aggregation formula, and use tier-specific capacity/scaling tests. Include downside
capture, expected shortfall, turnover, implementation shortfall, and regime attribution.

### H13 - Deterministic event IDs can collide

Task 7 derives IDs from run/origin/type/symbol/as-of/horizon. Direct and propagation
forecasts for the same symbol/horizon can share those fields. Multiple fill events do not
naturally have a horizon and can share a timestamp. The event enum also lacks explicit
manual action, cash flow, reconciliation, corporate action, broker acknowledgement, and
promotion/demotion events used elsewhere in the plan.

**Correction:** Define per-record identity from the natural immutable keys: producer,
model, evidence class, forecast version, allocation sequence, intent attempt, broker order
ID, broker event ID, and cumulative fill version. Add schema versions and collision tests.

### H14 - Secret sanitization remains denylist-based

Key and value pattern scanning cannot reliably recognize arbitrary random credentials, and
allowlisting `secret_ref` without validating its syntax can place a real secret under a
safe name. Task 2 scans only `BacktestResults.strategy_schema`; derived exports, other
tables, logs, snapshots, replicas, and backups are not inventoried.

**Correction:** Persist an explicit allowlisted public configuration projection instead of
sanitizing the full strategy object. Require `secret_ref` to match an approved reference
scheme and identifier. Run a metadata-only exposure inventory across the full persistence
estate, rotate all affected credentials, and treat immutable backups as sensitive until
retention expires.

### H15 - Risk recovery and demotion have no state machine

The plan defines entry into SOFT/HARD/KILL but not hysteresis, staged liquidation priority,
cooldown, recovery evidence, or who can restore exposure. It also checks many promotion
conditions only when a tier is requested.

**Correction:** Add persisted transition rules for NORMAL, SOFT, HARD, KILL, RECOVERY, and
operator HALT. Define automatic demotion, minimum cooldown, required healthy observations,
and explicit operator authorization. A restart must resume the prior state, not NORMAL.

## 6. Medium-Severity Findings

### M01 - The plan is already stale against the current branch

Since the plan commit, shared files including `backend/broker.py`,
`backend/interactive_utils.py`, `backend/api/main.py`, and
`backend/backtest_summary.py` have changed. Current GitNexus locations already differ from
several line references in Tasks 1, 8, 9, 15, and 16.

**Correction:** Rebase every task on symbols and current GitNexus context immediately before
execution. Treat line ranges as hints, not edit instructions.

### M02 - RethinkDB topology and durability are unspecified

The plan names RethinkDB but does not establish replica count, write acknowledgements,
hard-durability settings, backup freshness, connection deadlines, or restore behavior. A
single reachable endpoint is not proof that the state survives a node or host failure.

**Correction:** Document and test the deployed RethinkDB topology, hard-write behavior,
replica/write-ack policy, bounded connection timeouts, backup verification, and restore
drills. Promotion health must read these facts rather than infer durability from one query.

### M03 - API/index/retention design is incomplete

Limit-only endpoints are not stable pagination. `get_all(instance_id)` can still return a
large instance scope before Python filtering. A migration script does not continuously
enforce a 30/400-day retention policy.

**Correction:** Use compound indexes matching exact query shapes, deterministic cursor
pagination, index readiness checks, and a scheduled retention/aggregation job with dry-run
counts and audit records.

### M04 - Daily performance alignment is underspecified

Timestamp normalization can accidentally pair an account snapshot from one time with an
SPY close from another. Adjusted SPY bars, cash dividends received in the account, and
external cash flows need one explicit total-return convention.

**Correction:** Register the valuation timestamp and market calendar, preserve unmatched
dates as errors, and add golden tests around dividends, holidays, early closes, and account
cash activity.

### M05 - Decision cutoff and order schedule are absent

"Forecast once per trading day" does not say whether data is cut off pre-open, intraday,
or at close, nor when an order is eligible to execute. This can introduce same-bar
lookahead and inconsistent horizon length.

**Correction:** Define `known_at`, decision cutoff, earliest tradable time, session, TIF,
and expiry for every model family. Backtest fills must use the next eligible market event.

### M06 - Fractional, tick-size, and residual handling are incomplete

The existing adapter may floor non-fractionable quantities after rejection. That changes
portfolio weights and cash but the plan does not reallocate or record the target shortfall.
Limit-price tick rules and notional-order replacement restrictions also matter.

**Correction:** Normalize asset capabilities before allocation, round prices/quantities
before intent identity is created, and feed every residual back into reconciliation.

### M07 - `incident-free` is undefined

Without a severity taxonomy, operators can reclassify or omit an event and preserve a
promotion clock.

**Correction:** Define incident levels, automatic incident creation predicates, availability
and reconciliation SLOs, clock-reset rules, and who may close an incident. Promotion uses
machine-derived counts plus an immutable human disposition.

### M08 - Paper execution is operational evidence, not alpha evidence

Paper fills do not reliably represent live queue position, spread, rejects, or impact.
Combining paper returns with historical or live returns can falsely narrow confidence.

**Correction:** Keep evidence sources separate. Use paper for state-machine and fault
testing; use point-in-time unseen research and later real fills for alpha/cost validation.

### M09 - Operator signatures and append-only claims are vague

An `actor` string in RethinkDB is not authenticated approval, and a database row
is not immutable to an administrator.

**Correction:** Bind promotions and resets to an authenticated principal and signed artifact
digest, record revocation, and add a tamper-evident content hash chain or write-once export
for high-value audit events.

### M10 - The plan builds most production plumbing before the alpha stop/go decision

Tasks 0-15 can consume substantial engineering effort before Task 16 determines whether
either forecast family has usable unseen alpha. Finding A14 acknowledges this but the
execution order does not correct it.

**Correction:** After safety, audit, outcomes, benchmark, and cost foundations, run the
registered research program. Continue to tax/allocator/live integration only if a model
passes a predeclared research gate.

## 7. Required Attack Scenarios

The revised plan must include end-to-end tests for all of these sequences:

| ID | Sequence | Required invariant |
|---|---|---|
| S01 | RethinkDB down, mark stale, held stock breaches risk limit | No buy; deterministic quantity-capped risk sell remains available and is later backfilled from broker history |
| S02 | Broker accepts order, HTTP times out, process dies | Restart adopts by client ID; no duplicate order |
| S03 | SPY funding sell fills 37%, then stalls | Stock buys use only the confirmed net proceeds and reserved cash |
| S04 | New daily allocation arrives while old orders are open | Open quantities count toward exposure; stale orders are reconciled/canceled |
| S05 | Cash withdrawal occurs during a flat market | No false drawdown; raw and flow-adjusted equity both remain visible |
| S06 | Main process dies during KILL | Watchdog behavior matches the documented liquidation or manual-response contract |
| S07 | Graph/LLM unavailable at horizon expiry | No infrastructure-driven uncontrolled mass exit |
| S08 | Manual fill changes a held quantity | Symbol freezes; external event is recorded; no model receives credit |
| S09 | Split, dividend, symbol change, or delisting | Marks, lots, stops, returns, and outcomes remain reconcilable |
| S10 | Quote is fresh but crossed, IEX-only, or outside LULD | Increase is rejected or uses the approved degraded-data policy |
| S11 | Current inputs change but model artifact does not | Promotion remains valid; per-cycle snapshot changes are audited |
| S12 | Model/config/image/dependency hash changes | Live authority demotes before any new exposure |
| S13 | RethinkDB becomes unavailable between decision and WAL write | Increases block; reductions use the deterministic broker-reconciled outage policy |
| S14 | Duplicate/out-of-order partial-fill events arrive | Filled quantity and cash change monotonically exactly once |
| S15 | More than 30 held/candidate symbols need streaming data | Subscription budget is enforced; no silent unmarked symbol |
| S16 | SPY loss lot conflicts with a new active allocation | Tax opportunity cost and chosen override/block are explicit and auditable |
| S17 | Same symbol has direct and propagation forecasts | IDs do not collide; outcomes remain attributable by evidence class |
| S18 | Container and host restart after promotion | Risk state, WAL, positions, approval, and demotion state recover exactly |

## 8. Mandatory Amendments by Original Task

### Task 0

- Retain the live halt and credential rotation.
- Add a complete credential exposure inventory, not only known provider names.
- Record account capability fields relevant to the current intraday margin regime.
- Record every held and open-order migration decision.

### Tasks 1-2

- Replace denylist sanitization with an allowlisted public-config projection.
- Validate secret-reference syntax and provider allowlists.
- Inventory all tables, exports, logs, replicas, and backups; purge mutable stores and
  protect immutable backups after rotation.

### Tasks 3-4

- Replace scalar-only price truth with quote, trade, broker valuation, and execution-price
  types.
- Include bid/ask sizes, conditions, LULD/halts, session, and clock health.
- Reuse strict `live_calendar.py` behavior and prohibit its fallback in promotion-eligible
  paths.
- Define purpose-specific freshness and data-quality policies for every live tier.

### Task 5

- Keep RethinkDB authoritative for events, state, and the order WAL.
- Add instance/run/schema columns, natural event identities, hard writes, bounded
  connection deadlines, cluster health, migrations, and backup/restore verification.
- Add the explicit deterministic, quantity-capped broker-reconciliation policy for risk
  reductions during a RethinkDB outage; no second application database is introduced.

### Task 6

- Use flow-adjusted drawdown state.
- Define watchdog authority honestly.
- Add recovery/hysteresis/demotion states and exact staged liquidation priority.

### New Task 7A - Trading Calendar and Outcome Evaluator

- Implement exact forecast expiry and next-tradable observation rules.
- Resolve all forecasts, including untraded and rejected candidates.
- Handle corporate actions, missing data, revisions, and benchmark adjustment.

### Tasks 7-8

- Expand enums and IDs to cover manual actions, cash flows, acknowledgements, partial-fill
  versions, reconciliation, incidents, and demotions.
- Add cursor pagination, exact compound indexes, and a scheduled retention process.

### Task 9

- Define one end-of-day valuation convention and flow-adjusted series.
- Add clustered/HAC-aware uncertainty and golden Deflated-Sharpe tests.
- Report portfolio and active-sleeve metrics separately.

### New Task 9A - Equity Cost and Shadow Fill Model

- Implement directional spread, slippage, latency, rejection, partial-fill, and fixed-cost
  accounting.
- Create the shadow virtual portfolio and implementation-shortfall ledger.

### Tasks 10-11

- Separate probability calibration from conditional-return estimation.
- Cross-fit by date and shrink estimates toward zero.
- Define point-in-time event-score provenance.
- Add conservative uncertainty and effective-sample-size gates.

### Task 12

- Support all repeatedly traded symbols, not only SPY.
- Model partial replacement-share matching, adjusted basis, and holding period.
- Measure the opportunity cost of tax blocks in research.

### Task 13

- Remove the enforceable `active_floor` field.
- Add covariance/stress, cost, open-order, liquidity, unknown-sector, and data-quality inputs.
- Allow weights below 4% or no trade when the risk/cost budget requires it.

### Task 14

- Implement a persisted multi-phase execution batch, not a one-shot intent list.
- Reserve cash and exposure for open orders.
- Define order type, price collars, deadline, cancel/replace, tick/fractional normalization,
  and residual reconciliation.

### Task 15

- Make shadow broker submission impossible and add a virtual portfolio.
- Add the legacy-position migration manifest.
- Continuously enforce demotion and artifact identity, not only at startup.

### Task 16

- Move it before Tasks 12-15, after outcomes/costs/calibration exist.
- Replace the nominal 24-month rule with a power/regime/data-availability requirement.
- Count earlier research degrees of freedom and reserve genuinely future data.

### Task 17

- Define tier-specific metrics, evidence provenance, and formulas.
- Do not treat forecast/trade counts as independent statistical samples.
- Separate training-manifest identity from ordinary live input snapshots.
- Include fixed operating cost, realized slippage drift, and continuous automatic demotion.

### Task 18

- Run all S01-S18 attacks, including real process termination and network partitions.
- Reconcile every shadow day, not one selected day.
- Treat paper performance as operational evidence only.
- Require a deployment restore drill and broker/account capability check before LIVE_40.

## 9. Revised Execution Order

The safest and least wasteful sequence is:

```text
Stage A - Contain and repair
0 -> revised 1 -> revised 2 -> revised 3 -> revised 4
  -> revised RethinkDB event/WAL 5 -> revised flow-aware risk/watchdog 6

Stage B - Establish whether alpha evidence is possible
revised 7 -> new 7A outcome evaluator -> revised 8 -> revised 9
  -> new 9A cost/shadow-fill model -> revised 10 -> revised 11
  -> revised 16 registered research

STOP/GO GATE
If no forecast family passes unseen net-of-cost gates, stop. Keep the safety fixes,
run the portfolio as SPY/cash according to the operator decision, and do not build or
market the system as alpha.

Stage C - Build portfolio and execution only after GO
revised 12 -> revised 13 -> revised 14 -> revised 15

Stage D - Prove operations and scale deliberately
revised 17 -> revised 18 -> LIVE_40 -> LIVE_60 -> LIVE_80
```

## 10. Revised Promotion Evidence Contract

Promotion should consume separate evidence buckets and never pool them as if homogeneous:

| Evidence | Valid use | Invalid use |
|---|---|---|
| Historical point-in-time walk-forward | Alpha, drawdown, regime, and cost hypothesis | Operational reliability |
| Future shadow virtual portfolio | Leakage check, target behavior, modeled implementation shortfall | Real fill quality |
| Alpaca paper | State machine, restart, rejection, and fault behavior | Proof of live execution alpha |
| LIVE_40 actual fills | Real slippage, reconciliation, operational health, early degradation | Annualized certainty after a few weeks |
| LIVE_60 actual fills | Capacity and scaling evidence | Automatic authorization of LIVE_80 |

Minimum properties for a promotion-eligible statistical report:

- Primary observations are unseen daily portfolio returns, not raw forecast rows.
- Forecast-date clustering and serial dependence are reflected in uncertainty.
- Both portfolio and active-sleeve results are shown.
- All prior registered and relevant historical search attempts contribute to
  multiple-testing disclosure.
- Cost model, fixed operating cost, and realized implementation shortfall are visible.
- Every result is attributable by model, evidence class, horizon, regime, and tier.
- Missing/delisted outcomes remain in the denominator with an explicit conservative rule.
- No short sample is converted into persuasive annualized alpha solely through scaling.
- The exact deployed artifact and runtime constraints match the approved report.
- A promotion can be demoted automatically without granting the system permission to
  self-promote again.

## 11. Final Adversarial Verdict

The plan is a strong architecture proposal but an incomplete execution contract. Its
existing 14 findings correctly identify several broad risks, yet most mitigations are
assertions rather than fully owned tasks. The largest gap is not another stock-selection
parameter. It is the missing chain from point-in-time forecast to unbiased outcome to
costed virtual portfolio to crash-safe real order.

The controlled objective remains coherent only under these conditions:

1. Immediate security and mark-safety work is completed regardless of alpha prospects.
2. Research is moved ahead of expensive live-runtime construction.
3. LIVE_40 cannot start until all blockers B01-B10 are closed with tests.
4. Every HIGH finding has an explicit task, owner, and acceptance criterion.
5. Promotion uses independent, correctly aligned, net-of-cost evidence and a continuously
   enforced demotion path.
6. The system reports failure honestly if no model demonstrates alpha. SPY plus the chosen
   cash reserve is the fallback, not forced stock selection.

Until then, the plan must not be described as ready to execute, statistically defensible,
or capable of enforcing a 15% realized-loss guarantee.

## 12. July 18 Empirical Validation

The updated Alpaca and RethinkDB evidence does not relax the hostile verdict. It validates
the original concerns and exposes additional release blockers.

| Finding | Empirical evidence | Review consequence |
|---|---|---|
| B03, missing cost model | 470.94% gross turnover; 6.5-minute median decision-to-fill delay; approximately 32.1 bps unfavorable reference proxy | Costed quote-driven shadow evidence is mandatory before alpha claims |
| B05, order state machine | 46 orders produced 90 fill activity fragments; current code applies partial fills incompletely | Delta-idempotent fills and crash-resume tests remain BLOCKER |
| B07, cash-flow drawdown | $2,000 and $4,000 deposits materially change the apparent benchmark comparison | All risk and return series must be flow-adjusted |
| B09, legacy migration | Every current position exceeds the proposed 8% cap; two exceed 13% | A position-by-position migration manifest is mandatory |
| H03, stops | CRWV and MRNA lost approximately 20.7% and 19.0% | A stop formula without executable state and gap handling is not a control |
| M04, alignment | Alpaca daily UTC marks map to the preceding New York session | Session mapping requires a golden test |
| M05, order schedule | Median decision-to-fill was 6.5 minutes; July 6 buys averaged about 19.7 minutes | Decision/submission marks and a drift collar are mandatory |
| M10, sequencing | The post-reset result is positive but covers only ten sessions | It cannot bypass the research STOP/GO gate |

### E01 - Live safeguards are disabled by a mode collision

The daemon passes `FULL`, `MONITOR`, or `IDLE` through the same parameter used to infer
whether execution is live. The inferred live flag is therefore false during live
scheduler runs, changing cache reuse, propagation pruning, stale-news, phantom-sell, and
forced-exit behavior.

**Severity:** BLOCKER.

**Required correction:** Use independent typed `ExecutionMode` and `SchedulerTickMode`
values. Assert live invariants in every run event and test LIVE with all three scheduler
ticks.

### E02 - Current market truth can remain a fill price

Position refresh calculates a broker mark but preserves an existing nonzero scalar, often
the entry fill. Risk consumes that value before the near-submission historical-bar refresh.

**Severity:** BLOCKER.

**Required correction:** Broker and quote marks replace fill-cache values according to
timestamped precedence. Require separate decision and pre-submit marks. A historical bar
cannot satisfy either current-quote gate.

### E03 - Decision provenance is neither complete nor causal

`LiveDecisionAudit` is empty. `BotTradeDecisions` begins after ten strategy orders had
already executed. Across 46 fills, only 16 have any recomputed Graph scope that agrees
with the action, and only six have all available scopes agree. Later lookback
recomputation is not the configuration/evidence record that caused the live order.

**Severity:** BLOCKER.

**Required correction:** Persist the immutable live decision before submission and carry
one decision ID through WAL, client-order ID, broker order, fills, and position episode.
Record manual/dashboard ownership explicitly.

### E04 - The legacy outcome corpus is corrupt

The current scope contains 2,054 forward outcomes, of which 1,752 use `unknown` intent.
Six hundred forty-eight are zero-return rows, 637 never advance beyond entry date, sell
hit-rate semantics are inverted, and KLAC/FEMY extremes are consistent with unadjusted
corporate actions.

**Severity:** BLOCKER for research reuse.

**Required correction:** Treat all legacy scorecards as untrusted. Build the strict
calendar/corporate-action outcome evaluator and resolve every registered forecast from
point-in-time adjusted data.

### E05 - The live account is not contained

The July 18 read-only API response showed `runCommand=true` on the active non-paper
instance. No setting or order was changed during research.

**Severity:** BLOCKER for implementation and live restart.

**Required correction:** Task 0 must halt the instance, verify no order generator remains,
and preserve the 46-order/127-activity baseline before any other live-facing work.

### Updated hostile verdict

The revised plan now specifies the missing outcome evaluator, costed shadow portfolio,
fresh-direct-only initial challenger, all-symbol wash-sale ledger, flow-adjusted
performance, execution state machine, scheduler-mode separation, and staged STOP/GO
sequence. Those document changes address the shape of B01-B10 and E01-E05.

They do not close a single operational blocker until implementation and tests exist. The
current decision remains:

```text
Legacy live order generation: NO-GO
Implementation Stage A safety work: GO after Task 0 containment
Historical alpha claim: NO-GO
LIVE_40: NO-GO
LIVE_60: NO-GO
LIVE_80: NO-GO
```

The first promotable portfolio remains at most 40% fresh-direct active, 58-98% SPY
residual, and 2% cash. Forty percent is not a forced floor. Scaling toward the
user-approved 80% ceiling
requires new unseen evidence, not the ten-session post-reset result.

### Second-pass plan corrections

An independent second pass attacked the revised task graph itself and found four
specification-level blockers. The plan was corrected as follows:

1. Task 9A now owns a non-trading `ResearchPortfolioPolicy` and conservative tax proxy,
   allowing Task 16 to evaluate 40/60/80 costed portfolios before production tax,
   allocation, and execution Tasks 12-15. Task 16 owns the predeclared Stage B GO
   evaluator; Task 17 remains the later operational promotion evaluator.
2. `AuthorizationContext` is defined before allocation. Task 13 caps active exposure at
   the minimum of the 80% absolute ceiling, the current promotion tier, and current risk.
   Task 14 rechecks it before increases, and Task 17 wires continuous runtime demotion.
3. Task 0 persists `legacy_order_authority_disabled=true` for `alpaca-main`. OFF mode on
   that contained live instance is HALT/read-only; passing safety Tasks 0-6 cannot revive
   legacy trading.
4. Drawdown uses one positive-magnitude formula:
   `max(0, 1 - adjusted_equity / peak_adjusted_equity)`. Negative drawdown inputs are
   schema errors, preventing a -20% value from passing a <=15% comparison.

The same pass also required per-record natural event identities, routine Alpaca
reconciliation, a consistent stateful wash-sale interface, and "cap at 40%" rollout
language. These changes make the plan internally executable, but they do not change the
NO-GO status of unimplemented live operation.

## 13. Sources

Repository evidence:

- `backend/broker_adapters/_wal.py:1-11` - WAL restart contract.
- `backend/broker_adapters/alpaca.py:667-976` - current submit and ambiguity recovery.
- `backend/broker_adapters/alpaca.py:1781-1825` - current restart reconciliation.
- `backend/nexus_runtime_state.py:79-110` - RethinkDB-backed WAL store.
- `backend/backtest_summary.py:76-85` - current equity backtest fee treatment.
- `backend/portfolio_emulator.py:30-38` - commission-free equity emulator path.
- `backend/live_calendar.py:1-175` - existing exchange-calendar layer and fallback.

External primary/official references:

- [IRS Publication 550 - wash sales and FIFO](https://www.irs.gov/publications/p550)
- [Alpaca real-time stock data schemas](https://docs.alpaca.markets/us/docs/real-time-stock-pricing-data)
- [Alpaca IEX versus SIP market data](https://docs.alpaca.markets/us/docs/market-data-faq)
- [Alpaca market-data subscription coverage](https://docs.alpaca.markets/us/docs/about-market-data-api)
- [Alpaca order behavior and stop/limit risks](https://docs.alpaca.markets/us/docs/orders-at-alpaca)
- [Alpaca current intraday margin rule](https://docs.alpaca.markets/us/docs/the-intraday-margin-rule)
- [Alpaca PDT field deprecation](https://docs.alpaca.markets/us/changelog/2026-06-03-pdt-651df23)
- [Bailey and Lopez de Prado - Deflated Sharpe Ratio](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551)

This review is engineering and research-risk analysis, not personalized tax, legal, or
investment advice.
