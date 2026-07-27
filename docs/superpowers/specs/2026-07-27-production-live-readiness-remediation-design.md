# Production Live-Readiness Remediation Design

**Date:** 2026-07-27
**Repository:** IntelliStock
**Baseline commit:** `6e98bc6c35a9a7d4472db436d88a95d3c7456cc2`
**Status:** Approved architecture, pending written-spec review

## Objective

Make IntelliStock capable of becoming eligible for fully automated real-money
trading without using invalid backtest evidence or unsafe live-order behavior.
The work produces an auditable `LIVE_ELIGIBLE` candidate and deploys its
artifacts inactive. It does not start the existing live instance.

The system must never claim readiness merely because code compiles, focused
tests pass, or a selected backtest beats SPY. Readiness is the conjunction of
research integrity, execution safety, operational security, and statistically
valid promotion evidence.

## Non-Negotiable Safety Boundary

During remediation and inactive deployment:

- Do not start `alpaca-main` or any other real-money instance.
- Do not enable `runCommand`.
- Do not submit, replace, or cancel broker orders.
- Do not close, resize, import, or otherwise alter existing live positions.
- Do not migrate live credentials destructively without a verified rollback.
- Do not promote the strategy by bypassing an unmet readiness gate.

The target state for this project is a deployed but inactive candidate. Its
readiness state may advance no further than `LIVE_ELIGIBLE`; transitioning to
`LIVE_RUNNING` requires a separate explicit activation action after every gate
has passed.

## Why a Staged Rebuild Is Required

The existing system has failures that cross subsystem boundaries:

- Historical decisions can observe the close of an intraday bar at the bar's
  opening timestamp and then fill at that close.
- Same-date daily overlays, full-day cached sentiment, and present-day graph or
  universe state can leak future information into historical simulations.
- Backtests model complete, immediate, fee-free fills at decision prices.
- The residual SPY/SQQQ sleeve that dominates reported bear performance is
  deployed only in the backtest branch.
- Order acceptance is treated as sufficient to release capacity and expected
  proceeds before confirmed fills.
- Restart reconciliation occurs after position-ownership classification,
  allowing a crash-window fill to become unmanaged.
- Drawdown and SQQQ protective state can reset on restart or module drift.
- Critical control and data failures can fail open for exposure increases.
- Manual orders bypass the central strategy order safeguards.
- Credentials are present in plaintext records, and the default database
  configuration exposes unauthenticated services.
- Historical configuration search is not represented in the promotion
  statistics, and the selected windows are not sealed holdouts.

Narrow patches would preserve unsafe interactions. A single large rewrite
would be too difficult to review and validate. The design therefore divides
the work into four independently testable programs connected through explicit
contracts.

## Architecture

### Program 1: Research Integrity

#### `SimulationClock`

Owns three distinct timestamps:

- `decision_at`: when strategy code is allowed to observe data.
- `available_through`: latest event timestamp visible to the strategy.
- `execute_at`: earliest timestamp at which an order may fill.

For an intraday bar labeled by interval start, its close is unavailable until
the interval ends. An order created from that close may not fill before the
next tradable event.

#### `PointInTimeDataProvider`

Provides bars, news, fundamentals, graph facts, corporate actions, and universe
membership through one event-time interface. Every response carries:

- event timestamp;
- source publication timestamp when applicable;
- retrieval/source identifier;
- immutable dataset-manifest hash;
- adjustment policy.

The provider rejects any record whose availability timestamp exceeds
`available_through`. Daily data may not use a same-session closing value before
that session closes. News-derived features must be recomputed from the filtered
article set rather than reused from a full-day cache.

Historical universe membership and fundamentals must come from dated snapshots.
If a required point-in-time snapshot is missing, the run fails; it may not
silently substitute current state.

#### `ExecutionSimulator`

Consumes immutable order intents and simulates:

- next-tradable-event execution;
- bid/ask spread;
- configurable market impact and slippage;
- commissions and regulatory fees;
- latency;
- partial fills;
- rejections, cancellations, and expirations;
- market gaps and extended-hours constraints.

It emits the same normalized broker-event contract used by live adapters.
Portfolio cash, quantity, capacity, and realized P&L change only from emitted
fill events.

#### `ExperimentRegistry`

Registers every attempt before execution, including stopped and failed runs.
Each immutable experiment record contains:

- experiment identifier;
- parent experiment when derived;
- commit SHA and source-tree hash;
- complete effective configuration and its hash;
- effective model/provider, prompt hash, and model settings;
- seed and predeclared repeat count;
- point-in-time dataset and graph manifests;
- universe and benchmark manifests;
- execution-cost model;
- start/end dates and walk-forward fold;
- creation timestamp and actor;
- final status and failure reason.

The registry prevents mutation of inputs after a run begins. Results reference
the registered experiment rather than embedding an incomplete configuration
snapshot.

#### `AlphaPromotionGate`

Computes SPY-relative metrics from aligned daily portfolio returns and adjusted
SPY total returns. It records observation counts, bootstrap intervals,
information ratio, Deflated Sharpe probability, profit factor after costs, and
per-regime/per-quarter active returns.

The Deflated Sharpe calculation uses the complete historical trial count from
the experiment registry. It never defaults to one trial when multiple attempts
exist.

### Program 2: Execution Safety

#### `OrderIntent`

Every strategy, residual-sleeve, risk-exit, and manual request becomes an
immutable intent containing:

- instance and account identifiers;
- strategy/run identifier;
- symbol, side, quantity/notional, and reduce-only flag;
- source and reason;
- decision timestamp and quote snapshot;
- risk snapshot identifier;
- deterministic idempotency key and bounded retry ordinal.

No caller may invoke a broker adapter directly.

#### `UnifiedOrderGate`

The sole order-submission boundary enforces:

- instance/account ownership;
- explicit live arming state;
- kill-switch state;
- market/calendar eligibility;
- quote source, freshness, and sanity;
- fresh broker cash and positions;
- gross, net, symbol, leveraged-product, and order exposure limits;
- position-count and reserve constraints;
- strict reduce-only quantity caps;
- open-order and idempotency checks;
- watchdog and persistence health.

When a required dependency is unavailable, exposure increases fail closed.
Verified reduce-only orders may proceed only from a fresh broker position and
quote.

#### `OrderLifecycleService`

Persists an append-only lifecycle:

`INTENT_RECORDED → SUBMITTING → ACKNOWLEDGED → PARTIAL → FILLED`

Terminal alternatives are:

`REJECTED`, `CANCELED`, `EXPIRED`, and `UNKNOWN_REQUIRES_RECONCILIATION`.

Submission acknowledgement never changes cash, capacity, portfolio positions,
or protective state. Only cumulative confirmed fills produce accounting
deltas. Broker events are deduplicated by account, broker order ID, event ID,
and cumulative filled quantity.

A terminal retry creates a new intent with an incremented retry ordinal. It
does not reuse a terminal client-order identifier.

#### `StartupReconciler`

Startup order is mandatory:

1. Read the broker account, positions, and open/recent orders.
2. Reconcile every nonterminal and unknown WAL record.
3. Reconstruct confirmed strategy fills.
4. Classify strategy-owned and external quantities.
5. Reconstruct risk and protective state.
6. Evaluate readiness.
7. Permit the scheduler to start only when readiness allows it.

Unknown ownership remains quarantined. A manual position created while the
instance is down is never silently adopted.

#### `PersistentRiskState`

Stores account high-water mark, drawdown episode, exposure, and all residual
sleeve state from confirmed fills. SQQQ state includes confirmed entry basis,
peak, stop episode, cooldown, allocation state, and outstanding intents.

State is versioned independently of the strategy module hash. Code or
configuration deployments cannot lower the account high-water mark. Missing or
incompatible state blocks exposure increases until reconciliation succeeds.

Drawdown evaluation runs on every monitoring cycle with fresh broker equity and
marks. A breach cancels exposure-increasing orders and generates verified
reduce-only exits for strategy-owned positions. SPY and SQQQ have no blanket
drawdown exemption.

### Program 3: Security and Operations

#### Secret Storage

All broker, model, data-provider, database, and refresh credentials use
authenticated encryption. Startup refuses:

- plaintext sensitive fields;
- an absent or invalid encryption key;
- decryption failures;
- ambiguous duplicate credential sources.

Migration is copy-verify-switch:

1. Create encrypted replacements without deleting the source.
2. Decrypt and validate the replacements in memory.
3. Switch references atomically.
4. Verify read-only connectivity.
5. Remove plaintext only after a recoverable backup and audit record exist.

All currently stored credentials must be rotated outside the application before
live activation.

#### Network and Control Plane

RethinkDB and administrative interfaces bind only to an internal network or
loopback and are not published unauthenticated. API and Socket.IO control paths
require authenticated authorization, instance ownership, and server-side
step-up confirmation for material actions.

Duplicate client identifiers cannot terminate workers. Manual trading commands
use the unified order gate and durable lifecycle.

#### Independent Watchdog

Real-money readiness requires a healthy watchdog independent of the trading
loop. It checks:

- process and scheduler heartbeat;
- broker/account reconciliation age;
- quote age;
- WAL/event-processing lag;
- drawdown-state age;
- kill-switch reachability;
- database health;
- effective configuration and deployment identity.

Watchdog startup failure is fatal for real-money eligibility. Alerts and state
transitions are written to an append-only audit log.

#### Deployment and Rollback

Deployment artifacts include:

- immutable image/source identifier;
- schema and secret-migration version;
- effective configuration fingerprint;
- readiness report;
- test and migration evidence;
- rollback instructions.

Inactive deployment validation asserts:

- the live instance remains stopped;
- `runCommand` remains disabled;
- no order was created, changed, or canceled;
- live positions and account quantities are unchanged;
- migrations are reversible;
- readiness truthfully lists every unmet gate.

### Program 4: Promotion and Release

#### Readiness States

The enforced state machine is:

`RESEARCH → PAPER_ELIGIBLE → CANARY_ELIGIBLE → LIVE_ELIGIBLE → LIVE_RUNNING`

Transitions are monotonic only while their evidence remains valid. A code,
configuration, model, prompt, data-manifest, risk-policy, or broker-adapter
change invalidates downstream evidence according to its declared scope.

This remediation may produce and deploy a stopped `LIVE_ELIGIBLE` candidate. It
does not transition to `LIVE_RUNNING`.

#### Statistical Promotion Requirements

Promotion requires:

- at least 24 months of point-in-time data;
- at least 12 genuinely unseen months;
- at least three market regimes;
- purged walk-forward folds;
- one preregistered sealed holdout evaluated once;
- adjusted SPY total-return benchmark aligned to daily portfolio returns;
- bootstrap lower active-return bound above zero;
- information ratio at least `0.75`;
- Deflated Sharpe probability at least `0.95`;
- profit factor greater than `1.0` after modeled costs;
- positive alpha in at least `60%` of unseen quarters;
- predeclared repeats summarized by median, never the best run;
- parameter-perturbation stability;
- leave-one-winner-out and sector/factor concentration analysis.

Adding alpha from independently reset windows is prohibited.

#### Operational Promotion Requirements

Promotion also requires:

- complete CI success with isolated tests and no collection pollution;
- crash injection at every order-lifecycle transition;
- database, broker, quote, calendar, kill-switch, and watchdog chaos tests;
- restart survival for ownership, high-water mark, and sleeve state;
- security migration and rollback rehearsal;
- no unresolved critical/high findings;
- at least 60 trading days of paper/shadow evidence from the exact production
  build and configuration.

Calendar observation cannot be simulated or waived. Until it completes,
readiness reports the unmet requirement rather than claiming live readiness.

## Error-Handling Policy

Errors are categorized by whether exposure may increase:

| Condition | New exposure | Reduce-only | Scheduler |
|---|---|---|---|
| Healthy | Allowed by gate | Allowed | Continue |
| Stale/unknown data | Blocked | Fresh broker quote required | Degraded |
| Cash/position refresh failure | Blocked | Fresh broker position required | Degraded |
| Kill-switch unreachable | Blocked | Allowed only by emergency policy | Degraded |
| WAL/reconciliation uncertainty | Blocked | Reconciled quantity only | Halt new work |
| Risk-state uncertainty | Blocked | Reconciled quantity only | Halt new work |
| Watchdog unhealthy | Blocked | Emergency policy only | Stop |
| Secret/config integrity failure | Blocked | Blocked | Refuse startup |

No exception handler may convert a critical control failure into an identity
function or a misleading “overrides applied” message.

## Testing Strategy

### Research Tests

- Boundary tests around bar-open, bar-close, session-close, publication, and
  corporate-action timestamps.
- Property tests asserting every consumed event satisfies
  `available_at <= decision_at`.
- Negative tests proving absent historical snapshots fail the run.
- Golden tests for adjusted SPY alignment and active-return calculations.
- Differential tests between deterministic repeated experiments.

### Execution Tests

- State-transition unit tests for every legal and illegal WAL transition.
- Partial-fill delta and duplicate-event property tests.
- Crash injection before/after persistence, submission, acknowledgement,
  partial fill, final fill, rejection, cancellation, and accounting.
- Restart tests with broker/WAL disagreements.
- Manual, strategy, risk-exit, and SQQQ end-to-end contract tests proving every
  intent passes through the unified gate.
- Gap, stale quote, buying-power, extended-hours, and retry tests.

### Security and Operations Tests

- Plaintext-secret startup rejection and encrypted migration rollback.
- Authorization and ownership tests for API and Socket.IO commands.
- Network configuration assertions for database/admin services.
- Watchdog-loss and audit-log integrity tests.
- Inactive deployment assertions against a read-only account snapshot.

### Release Verification

- Full backend and frontend suites in clean isolation.
- Static checks, type checks, linting, dependency/security scans, and migration
  validation.
- GitNexus impact analysis before every production-symbol edit and
  `gitnexus_detect_changes()` before every commit.
- Independent code review for each workstream and a final cross-workstream
  adversarial review.

Passing helper-level tests alone is insufficient; every critical behavior needs
an integration or chaos test at the boundary where it can fail in production.

## Workstream Order and Interfaces

1. **Containment and secrets**
   - Establish encrypted storage, internal networking, truthful readiness, and
     inactive-deployment assertions.
2. **Research integrity**
   - Produce normalized event-time data and broker-event simulation contracts.
3. **Execution lifecycle**
   - Consume the normalized broker-event contract and centralize order routing.
4. **Persistent risk and controls**
   - Consume confirmed lifecycle fills and fresh broker snapshots.
5. **Promotion and release**
   - Consume immutable experiment records, operational evidence, and deployment
     identities.

Each workstream receives its own implementation plan and review gate. Later
workstreams may depend only on documented interfaces from earlier workstreams,
not their internal implementation.

## Acceptance Criteria

The remediation is complete only when:

1. Existing future-data and same-bar-fill regression tests fail against the
   baseline and pass against the corrected engine.
2. All order sources demonstrably use the unified gate.
3. Cash, capacity, position, and protective state change only from confirmed
   fill events.
4. Crash/restart tests cannot produce an unmanaged strategy fill or reset risk
   state.
5. Critical dependency failures block new exposure.
6. No plaintext secrets or unauthenticated public database/control services
   remain.
7. Promotion metrics are computed from registered trials and aligned SPY total
   returns.
8. The complete clean test suite and all chaos tests pass.
9. Deployment artifacts are reproducible and reversible.
10. Read-only post-deployment verification proves the real-money instance is
    still stopped and the broker account was not mutated.
11. Readiness reports `LIVE_ELIGIBLE` only when every required research,
    statistical, security, execution, and calendar gate is satisfied.

## Explicit Non-Guarantee

Passing these gates can establish engineering and evidentiary readiness. It
cannot guarantee profit or continuous outperformance of SPY. The production
system must report uncertainty and observed evidence rather than promise an
unachievable outcome.
