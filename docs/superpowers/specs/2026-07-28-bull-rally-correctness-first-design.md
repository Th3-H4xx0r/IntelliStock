# Bull/Rally Correctness-First Alpha Repair

**Date:** 2026-07-28
**Scope:** Strategy 179 equities / Alpaca only
**Approved approach:** staged correctness-first repair
**Safety boundary:** no Kalshi, crypto, Robinhood, or live-instance start

## Goal

Produce promotion-grade evidence that Strategy 179 can beat SPY in both the
2026-03-30 through 2026-04-27 bull window and the 2026-04-27 through 2026-05-25
rally window without materially reducing the existing bear, flip-flop, or full
window results.

This design does not promise that the strategy will always outperform SPY.
It establishes deterministic experiments, repairs two execution mismatches,
and evaluates narrowly scoped bull/rally levers under explicit no-regression
gates.

## Forensic Findings

### Non-reproducible historical evidence

`bt#582264` and `bt#331350` have the same saved configuration across 523 keys,
but returned +1.7984% and -3.1703%, respectively. Their bought-ticker overlap
was only 9 of 53 names. Other identical-config groups varied by 1.39 to 6.41
percentage points across the bear, flip, full, and rally windows.

Strict point-in-time data capture now freezes graph, fundamentals, universe,
and news inputs, but it does not freeze all model outputs. The ordinary prompt
cache is mutable and its key intentionally omits system prompts, tools, and
output schemas. It is therefore useful for latency but is not an immutable
experimental replay ledger.

No strategy change can graduate from single-run comparisons until identical
replays are deterministic.

### Recovery capacity is split between strategy and broker

The strategy's `_apply_recovery_cap` raises a confirmed recovery book from the
chop cap of 8 to `max_positions_recovery=14`. The broker's final
`_regime_position_cap_hard` gate reads only bull/chop/bear/crash and still
enforces 8. It blocked otherwise executable AAOI and FTH recovery entries.

The broker gate is the final choke point for every new Graph Nexus buy, so this
repair has a HIGH effective blast radius and must remain default-off until
cross-window validation passes.

### Bull breakout sizing uses the previous cycle's regime

Regime-profile merging occurs before the strategy computes the current cycle's
regime. Consequently, the deployed confirmed-bull 10% momentum-breakout cap
still used the base 6% cap on the April 8 CAR entry. The isolated notional
estimate would lift that run only to roughly 13.74%, about 0.58 percentage
points above SPY before cash-path effects, so it does not meet the primary
alpha gate by itself. The same one-cycle lag can retain the larger cap for one
cycle after a downgrade.

The repair must resolve only this lane's cap from the current stamped regime;
it must not broadly change profile timing or the regime detector.

### Circuit-breaker direction is reversed

The comments for `_get_conviction_aware_floor` say bull should widen the loss
floor and bear should tighten it. For a -15% floor, current arithmetic produces
-10% in bull and -20% in bear, the opposite behavior. Several rally exits
clustered near the unintended bull threshold, which is consistent with but not
direct proof of circuit-breaker firing because the early risk logs were capped.
A naive hold-to-end counterfactual improves the target by about 2.55 percentage
points but still leaves it far below SPY and is not executable evidence that
the correction helps.

The correction is a separate candidate, never bundled with allocation fixes.
It remains default-off and is promoted only on its own evidence.

### Historical +28.60% bull reference is not a safe target

`bt#353454` obtained most of its advantage from concentrated AAOI and CAR
exposure. It entered AAOI while the present system was in bear, exceeded the
current bear capacity contract, and used a different discovery/input path.
Reproducing it would require weakening controls that created the profitable
bear result. That configuration is rejected as a production target.

## Architecture

The work is divided into an evidence foundation and four independently
switchable candidates. No candidate is enabled merely because its unit tests
pass.

### Phase 0: immutable deterministic experiment replay

Add a content-addressed model-call ledger around the two provider choke points:
plain and structured guarded LLM calls. A deterministic semantic call identity
uses decision time, call site, role, symbol or batch identity, canonical request
hash, and a deterministic local sequence. Thread identity and completion order
never participate. A recorded request envelope includes:

- requested provider/model and provider adapter/endpoint identity
- reasoning effort and safe generation settings
- complete prompt/message history and complete system prompt
- canonical structured-output schema bytes, when applicable
- canonical tools, tool choice, fallback policy, and canonicalization version
- every response-affecting token, temperature, retry, and raw-JSON setting

The ledger records the returned text or canonical structured JSON and safe call
metadata. Effective/attempted models, fallback mode, raw-response hash,
validated-payload hash, and failure/`None` outcomes are response metadata, not
lookup identity. API keys, credentials, provider secrets, and secret-bearing
configuration fields are rejected. Concurrent results are aggregated by
semantic call identity, never completion order.

Recording mode bypasses every ordinary mutable prompt cache. Any upstream
LLM-derived sentiment/article/cache artifact must either be bypassed or be
snapshotted, hashed, and replayed through the evidence fixture; one unbound
cache read makes a run ineligible. The ledger is separate from temporal market
datasets and from candidate-specific code. A
content-addressed replay fixture binds:

- the ordered strict-PIT manifest chain and source hashes used by the window
- immutable model-call ledger hash
- RNG-seed manifest and benchmark-session manifest

This permits several sealed model-output fixtures for uncertainty testing while
ensuring baseline and candidate consume the same fixture. Exact request
identity matching fails closed if a candidate changes a prompt or introduces a
new model call.

The existing immutable `ExperimentRegistry` remains the source of
candidate-specific code revision, source-tree hash, effective configuration,
model settings, execution-cost model, and preregistration. A completed replay
receipt binds that experiment fingerprint to the replay-fixture ID and
trade-ledger hash. Baseline and candidate therefore have different experiment
fingerprints but can consume the same sealed external/model inputs.
Uncommitted or untracked executed source is ineligible unless its exact content
manifest is hashed into the run; a commit SHA alone is insufficient.

Replay behavior is fail-closed:

- no provider call or mutable prompt-cache fallback on a replay miss
- no unused recorded call at replay completion
- no response-schema mismatch
- no PIT chain, seed, benchmark-session, or replay-ledger drift
- no mismatch between the completed receipt and its preregistered experiment

Paired A/B evidence additionally requires identical semantic request-ID sets.
A candidate that adds, omits, or changes a model request is not a matched-output
experiment and cannot resolve the difference with a provider call. It requires
a separately approved experiment design rather than silently weakening replay.

Existing PIT manifests remain valid market-data evidence. A complete ordered
chain is required because each historical decision can resolve a different
eligible manifest. Existing manifests are not promotion-grade deterministic
evidence unless bound into a complete replay fixture and receipt. This avoids
silently rewriting historical manifests or fabricating model outputs that did
not exist.

Each completed backtest persists:

- replay-fixture ID, experiment fingerprint, and every component hash
- trade-ledger hash over ordered decisions and fills
- gross return, explicit fees, slippage, spread impact, and net return
- benchmark source, aligned SPY sessions, and active return
- decision/fill ordering audit and replay-completeness result

Three exact baseline replays must yield the same trade-ledger hash and no more
than the stored numeric precision of final equity before alpha experiments
begin. Repeating one fixture verifies determinism; it does not create
independent statistical observations.

### Candidate A1: recovery-cap coherence

Add `regime_position_cap_recovery_hard_enforce`, default `false`.

When enabled, the broker hard-cap resolver returns the recovery cap only if all
of the following are true:

- current confirmed regime is `chop`
- `_market_regime_recovery` is true
- `max_positions_recovery` is configured
- existing bear-capacity latch is not active

The value must be a finite positive integer. The effective cap is
`min(max_positions, max(chop_cap, recovery_cap))`, matching the strategy's
"only raises" behavior without exceeding the global book limit. Invalid
recovery state or configuration fails locally to the ordinary chop hard cap;
it must never fall through the broker helper's broad exception path to no cap.
Otherwise behavior is byte-compatible with the existing bull/chop/bear/crash
table. The broker logs the effective label and cap for every hard block.

This does not weaken bear confirmation, bear capacity, the recovery release
conditions, or SQQQ controls.

### Candidate A2: same-cycle momentum-breakout cap

Add `momentum_breakout_max_nav_pct_by_regime`, absent by default.

When present, the momentum-breakout lane resolves its NAV ceiling from the
strategy cache's current confirmed regime after detection:

- `bull`: candidate value 0.10
- `recovery`: candidate value 0.06
- required `default`: candidate value 0.06

Recovery is recognized only when current regime is chop and the recovery flag
is true. When the mapping exists, missing, invalid, chop, bear, crash, and
unknown labels resolve to its conservative `default`; they never inherit the
previous profile's scalar. Legacy scalar behavior is retained only when the
entire mapping is absent. The mapping is base-only and cannot be overridden by
a previous-cycle regime profile. A bull uplift additionally requires
`momentum_breakout_add_respect_gates=true`. The resolved cap and regime are
logged.

This change is local to `momentum_breakout_add`; other regime-profile levers
retain their existing timing.

### Candidate A3: current-bull initial deployment ramp

Add `deployment_ramp_caps_by_regime`, absent by default.

During the existing initial deployment-bar sequence, choose caps from the
current confirmed regime when a mapping exists. The first rally candidate is:

- bull: `[0.50, 0.70, 1.00]`
- every other regime: use the existing global caps

This matches the only historical slow-ramp comparator; `[0.50, 0.70, 0.90]`
may be evaluated only as a separately registered conservative extrapolation.
That comparator was dominated by AMD and used a different first slate, so it is
a risk-control hypothesis, not causal alpha evidence.

The global deployment bar index is retained. A bull regime that begins later
does not start a new ramp episode, so the direct rule is scoped to a cold start
already classified bull rather than suppressing later recoveries. It does not
create or re-rank Graph Nexus candidates, but it changes execution, queue/slot
state, and future portfolio-dependent behavior. With the residual sleeve
enabled it can materially shift exposure from active stocks into SPY and create
sleeve deploy/release orders. Experiments separately attribute active-stock
pacing, SPY beta, sleeve turnover, and costs. Warm live boots that fast-forward
persisted ramp state are reported separately from empty-book backtests.

### Candidate A4: corrected circuit-breaker semantics

Add `circuit_breaker_regime_adjustment_semantics_v2`, default `false`.

When disabled, arithmetic remains byte-compatible. When enabled:

- bull uses `base - abs(bull_adjustment)` on the absolute floor,
  making the floor more negative and therefore wider
- bear uses `base + abs(bear_adjustment)` on the absolute floor, making it less
  negative and therefore tighter
- chop remains unchanged
- crash retains the immediate-exit sentinel

Adjustment parsing accepts explicit zero and fails invalid values to zero. The
existing explicit `max_open_loss_pct` override retains precedence. For MID/LOW
tiers the volatility-scaled floor can still dominate; the candidate corrects
the absolute-floor direction, not a guaranteed final stop width. Tests and
telemetry cover and report the absolute, volatility, override, and final
effective floors.

This candidate is evaluated alone after A1-A3. It is not required to graduate
the allocation fixes and is rejected if bear, dead-cat, or full-window tail
risk, turnover, blacklist churn, or capital lock worsens beyond the gates.

## Experiment Matrix

Every row uses matched PIT/model-output bundles, seed, execution model, and
benchmark sessions.

1. Baseline exact replay, three repetitions per bundle.
2. A1 alone.
3. A2 alone.
4. A3 alone.
5. Predeclared allocation combinations, each with interaction attribution.
6. A4 alone against the unchanged baseline.
7. Preregistered A4-plus-allocation combinations only if A4 passes alone.

Every candidate, combination, threshold, failed/stopped attempt, repeat count,
and selection rule is preregistered before execution. A combination is a new
candidate and must pass the complete matrix itself. At least ten independently
recorded, preregistered model-output fixtures per primary development window are
required for model-output uncertainty; repeated replay of one fixture counts
once.

The following are development/regression windows because their trades informed
diagnosis or candidate construction. Passing them advances a frozen candidate
to sealed evaluation but is not out-of-sample promotion evidence.

Primary development windows:

- rally: 2026-04-27 through 2026-05-25
- bull: 2026-03-30 through 2026-04-27

Safety windows:

- bear: 2026-03-02 through 2026-03-30
- flip-flop: 2026-01-05 through 2026-03-02
- full: 2026-03-02 through 2026-04-27

Sealed evaluation must satisfy the repository's existing
`evaluate_promotion` contract rather than replacing it. That contract includes
at least 24 verified PIT months, 12 unseen months, three regimes, purged folds,
one preregistered sealed-holdout evaluation, complete trial accounting,
positive block-bootstrap active-return bounds, deflated-Sharpe and
concentration tests, exact artifact/config/model/data matching, and 60 paper
trading days on the exact build/config. No named development window can satisfy
those production gates.

## Promotion Gates

### Determinism and provenance

- Exact ordered decision/fill ledger hash and final equity match across three
  repetitions to stored numeric precision.
- Zero provider/cache fallback and zero unconsumed replay entries.
- Strict PIT source hashes match and every input availability time is at or
  before its decision.
- Every simulated fill timestamp is strictly after its originating decision.

### Primary alpha

- Bull and rally both have positive after-cost active return versus aligned SPY.
- Median active return is at least +1.0 percentage point in each primary
  window.
- The existing 5th-percentile bound of the two-sided 90% five-session block
  bootstrap over daily active returns is above zero across sealed evidence.
- A separately registered full rerun that excludes the ex-post largest winning
  active stock from inception retains positive compounded, capital-weighted
  pooled primary active return. Subtracting realized ticker P&L is not accepted.

### Cross-window safety

- In each bear, flip, and full development window, paired
  `candidate - baseline` median return delta is at least -0.5 percentage points
  and its 10th percentile is at least -1.0 point.
- Bear and flip remain absolutely profitable in every matched replay.
- In each safety window, median maximum-drawdown degradation is no worse than
  0.5 percentage points and its 90th percentile is no worse than 1.0 point.
- A candidate claimed to be inert in a window must produce the identical
  trade-ledger hash there.
- The full window overlaps bear and bull and is a consistency check, not an
  independent observation.

### Costs and concentration

- Candidate passes primary, safety, and drawdown gates under nominal, 25bps,
  and 50bps one-way total-cost scenarios. Total one-way cost means modeled
  half-spread plus slippage plus explicit fee.
- Cost stress is a full path re-simulation under a hashed cost model, not a
  post-hoc haircut. It preserves the replay fixture and recomputes
  affordability, quantities, fills, NAV gates, and exits without
  double-counting costs already embedded in fill prices.
- Active-stock new-order/post-trade target is at most 15% of NAV.
- Active-stock appreciation above 25% of NAV rejects automatic promotion and
  requires a separately tested trim/manual-risk decision.
- SPY and SQQQ remain governed by separate sleeve contracts; confirmed-bear
  SQQQ post-trade target remains capped at 70%, with appreciation drift
  reported separately.
- Accounting reconciles starting equity, cash flows, gross counterfactual P&L,
  spread, slippage, fees, and actual net ending equity exactly.
- The read-only evidence API exposes and validates all replay hashes, cost
  components, rejected/unfilled counts, benchmark manifest, and audits.

## Invariants

- Do not weaken bear hysteresis, cap-two behavior, bear-capacity latch,
  long-book trim, rotation blocks, or SQQQ scaling.
- Do not change the full/bull 70%/120%/170% profit tiers or 15% trailing exit.
- Do not add ticker-specific rules or use future winners to construct a slate.
- A3 must explicitly report missed early leaders, SPY sleeve turnover, queue
  expiry, regime flips during the first three bars, and warm-boot behavior.
- A4 must explicitly report bull capital lock/drawdown, bear V-bottom exits,
  re-entry/blacklist churn, volatility-dominant floors, and override precedence.
- Do not enable live trading, start an Alpaca instance, or change linked account
  credentials.
- Do not touch Kalshi, crypto, or removed Robinhood paths.
- Do not push during an active backtest because deployment interrupts it.
- A failed gate rejects the candidate; it is not averaged away by another
  window.

## Rollout and Rollback

All candidate keys are absent/default-off in production code. Promotion uses
the exact combined code/config artifact that passed the complete matrix;
independent component passes do not establish combination safety. The patch is
drift-checked against the current strategy document and backed up before
mutation.

Deployment is code/config only with `runCommand=false`. The Alpaca instance
remains stopped. Rollback restores the pre-promotion strategy document and
leaves the instance stopped.
