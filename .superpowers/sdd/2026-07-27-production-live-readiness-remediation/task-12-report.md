# Task 12 report — statistical promotion and readiness

## Scope and impact

GitNexus reported LOW impact for the readiness API and Stage-B evaluator,
MEDIUM for active-metric computation (five direct callers), and LOW for the
append-only store boundary. The stale index could not resolve the newly added
readiness functions; their direct consumers were covered by focused tests.

No instance state, broker account, order, position, or promotion record was
mutated while implementing or testing this task.

## Implemented

- Added immutable `PromotionEvidence` bound to exact source/deployment,
  paper-build, config, model, data-manifest, cost-model, risk-policy, and broker
  adapter hashes.
- Added a pure evaluator returning every failed statistical, research,
  engineering, security, ownership, watchdog, and paper-observation gate.
- Enforced 24 point-in-time months, 12 unseen months, three regimes, purged
  folds, once-only preregistered holdout, adjusted-SPY alignment, costed fills,
  complete trial count, median repeat reporting, stability/concentration
  checks, and the specified numeric performance thresholds.
- Enforced CI/chaos/restart/credential/rollback/ownership/watchdog gates and an
  exact-build **60 trading day** paper minimum.
- Added immutable append-only promotion records. Recording requires a passing
  decision plus a distinct explicit authenticated operator approval; operator
  identity is stored only as a hash.
- Added a read-only JSON + human-table CLI. It performs no state mutation and
  exits non-zero when the requested state is not eligible.
- Added adjacent state-transition enforcement. Pure evaluation can produce at
  most `LIVE_ELIGIBLE`; `LIVE_RUNNING` requires a separate explicit action.
- Extended the authenticated readiness API to surface the latest immutable
  promotion record without treating it as automatic activation.

## Verification

- Promotion/readiness/research/metrics/API/store suite: **129 passed**.
- Modified modules compile successfully.
- Table-driven tests independently fail every statistical and operational
  threshold, including paper day 59, watchdog age 61s, and all artifact drift.

Current historical backtests do not satisfy this contract: their observation
window is too short and there are zero completed exact-build paper trading
days. The correct state remains non-live until those calendar gates accrue.
