# Bull/Rally Correctness-First Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development`. Execute each task with
> red-green-refactor TDD and a task-scoped review before continuing.

**Goal:** Build deterministic Graph Nexus evidence replays, repair the
recovery/bull execution mismatches behind default-off flags, and run a
preregistered bull/rally development matrix without reducing bear, flip, or
full-window performance.

**Architecture:** Reuse strict PIT datasets and `ExperimentRegistry`. Add
content-addressed branch-union model-evidence fixtures with arm-specific
request-set manifests and candidate-specific replay receipts. Preregister the
complete matrix before its first run. Expose backtest-only, allowlisted
overrides through the API so experiments never mutate the live strategy
document. Add four independently reversible strategy candidates and promote
only an exact combined artifact that passes the complete evidence gates.

**Tech stack:** Python 3.14, pytest, SHA-256 canonical JSON, RethinkDB, FastAPI,
Pydantic, existing next-event equity simulator.

## Global Constraints

- Work only in `.worktrees/bull-rally-correctness` on
  `codex/bull-rally-correctness`; preserve the dirty primary checkout.
- Equities/Alpaca only. Do not touch Kalshi, crypto, or removed Robinhood paths.
- Never start `alpaca-main`; keep its `runCommand=false`.
- Do not push while any backtest is running because a push auto-redeploys.
- Strict PIT remains fail-closed. Never fabricate/backdate a manifest or add a
  current-state historical fallback.
- Every production function/class/method edit requires upstream GitNexus impact
  analysis first. Treat unindexed `broker.py` and
  `graph_nexus_analysis.py` execution paths as HIGH risk and report that before
  editing.
- Every change starts with a failing test. Existing behavior is byte-compatible
  when evidence mode is off and candidate keys are absent/default-off.
- Before every commit: stage only task files, run `git diff --cached --check`,
  then `gitnexus_detect_changes({scope:"staged"})`.
- Never log, persist, or return credentials. Reuse existing secret scrubbing and
  add explicit model-evidence secret rejection.
- A replay miss, extra/missing semantic request, PIT-chain mismatch, cost-model
  mismatch, or source/config mismatch makes the run ineligible; no provider or
  mutable-cache fallback.
- The five named windows are development/regression evidence only. Production
  eligibility still requires the existing `evaluate_promotion` contract.

---

### Task 1: Thread-Safe Immutable Model-Evidence Primitives

**Files**

- Create: `backend/model_evidence.py`
- Create: `backend/tests/test_model_evidence.py`

**Interfaces**

- `ModelEvidenceError`
- `canonical_request_envelope(...) -> dict`
- `ModelEvidenceContext(decision_at, call_site, role, subject, local_sequence)`
- `semantic_request_id(envelope, *, context) -> str`
- `ModelEvidenceRecord`
- `ModelEvidenceLedger`
- `ModelEvidenceReservation`
- `ModelEvidenceSession`
- `ModelEvidenceSession.reserve(semantic_id) -> ModelEvidenceReservation`
- `activate_model_evidence_session(session)`, `get_model_evidence_session()`,
  and `clear_model_evidence_session()`

**Requirements**

- Canonical request identity includes requested provider/model, safe adapter
  identity, complete prompt/system prompt, schema bytes, tools/tool choice,
  generation settings, fallback policy, and canonicalization version.
- API keys and secret-bearing keys are rejected recursively.
- Effective/attempted models, raw/validated response hashes, fallback state, and
  successful/`None` outcomes are response metadata.
- The process-global session is guarded by a lock because one backtest worker
  owns one run while Graph Nexus worker threads must see the same session.
- The caller constructs a complete evidence context before dispatching worker
  threads. Calls without a decision timestamp, non-empty call site and role,
  subject/batch identity, or deterministic local sequence fail closed.
- Semantic IDs cannot depend on thread ID or completion order. Each logical
  occurrence has a deterministic occurrence key. Re-publishing the same
  immutable row is idempotent; a second logical occurrence is separately
  consumed, and divergent rows for one key reject fixture finalization.
- `record` and `record_extend` require a non-empty arm identity and dynamically
  observe semantic occurrences while a fixture is being built; they neither
  require nor accept the final request-set declaration before construction.
- Before provider dispatch, callers atomically reserve the semantic ID.
  `record` reserves a provider slot. `record_extend` returns an explicit replay
  hit for an existing immutable union row (including a recorded `None`) or
  reserves an absent row for provider completion. Only a reserved provider slot
  may be recorded; duplicate/unreserved/divergent completion and pending slots
  fail finalization.
- Successful `record`/`record_extend` finalization returns the immutable
  observed occurrence sequence for `FixtureBuild`. The build freezes each
  observed arm set only when the fixture seals. `replay` alone requires that
  sealed immutable `frozenset` and fails on undeclared requests, misses,
  over-consumption, or unused arm records. `off` is a no-op.
- Import/export is canonical, content-addressed, and immutable.

- [ ] Write tests for canonical identity, secret rejection, explicit occurrence
  keys, concurrent completion-order independence, idempotent publication,
  dynamic recording, atomic single reservation, recorded-`None` replay hits,
  record-extend hit/miss behavior, unreserved/duplicate completion, pending
  finalization, strict replay miss/over-consumption/unused checks, and hash
  tampering.
- [ ] Run `python3 -m pytest -q backend/tests/test_model_evidence.py` and verify
  RED.
- [ ] Implement the minimum pure module and verify GREEN.
- [ ] Run staged GitNexus change detection and commit:
  `feat(backtest): add immutable model evidence ledger`.

---

### Task 2: Durable Branch-Union Fixtures, Matrix Manifests, and Receipts

**Files**

- Create: `backend/backtest_replay.py`
- Create: `backend/tests/test_backtest_replay.py`
- Modify: `backend/experiment_registry.py`
- Modify: `backend/tests/test_experiment_registry.py`

**Interfaces**

- `ReplayFixture` binds an ordered PIT manifest/source-hash chain, model ledger
  hash, per-arm request-set hashes, RNG-seed manifest, benchmark manifest, and
  one cost scenario.
- `FixtureBuild` has a preregistered stable ID derived from matrix/window/
  fixture-ordinal/cost-scenario identity. Build IDs address mutable construction
  state; successful sealing creates the distinct content-addressed fixture ID.
- `ExperimentMatrixManifest` binds all arms, combinations, windows, cost
  scenarios, fixture count, arm recording order, trial count, bootstrap seed,
  failure rules, selection rule, and implementation hashes before any run.
- `ReplayReceipt` binds matrix/arm IDs, fixture ID, immutable experiment
  fingerprint, executed source-tree/content hash, dependency/runtime digest,
  execution-cost-model hash, ordered trade-ledger hash, and arm-local
  replay-completeness audit.
- `InMemoryReplayStore` and `RethinkReplayStore` publish call rows first,
  fixture/receipt manifests last, and reject divergent immutable rows.
- `trade_ledger_hash(decisions, fills) -> str`
- `validate_replay_source(...)` rejects a dirty/untracked executed tree unless
  its full content manifest is supplied and hashed.

**Requirements**

- Reuse `ExperimentRegistry`; do not duplicate its candidate-specific
  configuration, source-tree, model, trial, or execution-model contracts.
- A baseline and candidate share one sealed union fixture but have different
  declared request sets, experiment fingerprints, and receipts. Request sets
  are taken from successful construction-session finalization while building
  and become immutable declarations only at seal time.
  Common semantic IDs must map to one identical immutable row. Branch-only rows
  do not count as unused for another arm.
- Build a separate union fixture for every cost scenario because full-path cost
  changes can alter holdings and downstream model requests.
- A matrix manifest and its arm IDs are immutable and must exist before the
  first fixture-building or formal backtest request.
- PIT entries preserve decision order and verify `manifest.as_of <= decision`.
- Receipts are promotion-ineligible until replay, PIT, execution, benchmark,
  and accounting audits all pass.

- [ ] Run GitNexus impact on `ExperimentSpec` and its constructors/readers.
- [ ] Write failing immutable-store, matrix-preregistration, build-ID/final-ID,
  source-manifest, PIT-chain, common-row conflict, cost-keyed arm request-set,
  fixture/receipt, and trade-hash tests.
- [ ] Implement and run the focused replay/experiment-registry suites.
- [ ] Detect staged changes and commit:
  `feat(backtest): persist deterministic replay fixtures`.

---

### Task 3: Guarded LLM Record/Replay and Mutable-Cache Exclusion

**Files**

- Modify: `backend/llm_utils.py`
- Modify: `backend/_phase_alpha_helpers.py`
- Modify: `backend/strategies/graph_nexus_analysis.py`
- Modify: `backend/tests/test_llm_critical_guard_retry.py`
- Create: `backend/tests/test_llm_model_evidence.py`
- Modify: `backend/tests/test_phase_alpha_variance.py`
- Create: `backend/tests/test_nexus_evidence_cache_policy.py`

**Requirements**

- Integrate at `_call_llm_with_critical_guard` and
  `_call_structured_llm_with_critical_guard`, before mutable prompt-cache
  lookup/provider dispatch. Pass the complete caller-created
  `ModelEvidenceContext` into worker threads.
- Replay reconstructs/validates structured payloads using the requested schema.
- Record final strategy-visible text/structured result and safe provider
  metadata; non-critical `None` results are replayable.
- Evidence recording bypasses ordinary prompt caches. Replay never calls a
  provider.
- Add one centralized evidence-cache policy consulted before every read of
  sentiment, overlay-result, active-event-maintenance, analyst-panel,
  learning-derived, and ordinary prompt caches. Record/record-extend bypass
  them; replay accepts only artifacts explicitly bound into the sealed fixture.
- Evidence mode requires sentiment-cache force disabled, sentiment cache off,
  fast/overlay result caches off, and deterministic clean-start state. Invalid
  combinations fail during broker/strategy preflight, before strategy
  execution rather than merely before the first provider call.
- The off path must preserve existing critical retry/backoff behavior.

- [ ] Run GitNexus impact on both guarded-call functions,
  `resolve_use_sentiment_cache`, and every Graph Nexus LLM-derived cache reader;
  report indexed results and treat the unindexed strategy path as HIGH.
- [ ] Write failing plain, structured, all-upstream-cache-bypass,
  preflight-before-strategy, provider-not-called, schema-mismatch,
  failure-result, context propagation, concurrency, and off-path tests.
- [ ] Implement and run the focused LLM/cache suites.
- [ ] Detect staged changes and commit:
  `feat(backtest): replay guarded model evidence`.

---

### Task 4: Backtest Lifecycle, Allowlisted Overrides, Costs, and Evidence API

**Files**

- Modify: `backend/api/main.py`
- Modify: `backend/interactive_utils.py`
- Modify: `backend/backtest_experiments.py`
- Modify: `backend/broker.py`
- Modify: `backend/portfolio_emulator.py`
- Modify: `backend/simulated_execution.py`
- Modify: `backend/tests/test_backtest_execution_costs.py`
- Modify: `backend/tests/test_broker_graph_nexus_pit.py`
- Create: `backend/tests/test_backtest_evidence_api.py`
- Create: `backend/tests/test_backtest_candidate_overrides.py`

**API additions**

- `evidence_mode`: `off | record | record_extend | replay`, default `off`
- `fixture_build_id`: required for `record` and `record_extend`
- `replay_fixture_id`: required only for formal `replay`
- `matrix_manifest_id` and `matrix_arm_id`: required for every non-off mode
- `cost_scenario_id`: required for every non-off mode
- `equity_total_cost_bps`: absent/nominal, `25`, or `50`
- `nexus_candidate_overrides`: mapping restricted to the four approved
  candidate keys and their nested schemas
- `POST /backtest-evidence/matrices`: publish the complete immutable matrix
  manifest without queueing or starting an instance

**Requirements**

- Validate and store options on `BacktestInstances`; never accept stock
  credentials or arbitrary strategy overrides. Reject a missing, unknown, or
  mismatched matrix/arm reference before queueing.
- Add one explicit `backtest_row_id` option loader at broker startup. It must
  load and validate the queue-row evidence/cost/override contract before model
  resolution, evidence activation, candidate override application, experiment
  preregistration, PIT lookback, or emulator construction.
- Apply allowlisted overrides to the in-memory backtest Graph Nexus spec before
  immutable experiment preregistration. Never mutate `Strategies` or the live
  instance document.
- Initialize evidence before historic lookback, record/verify every resolved
  PIT manifest, and finalize fixture/receipt only after a successful complete
  run. Wrap the entire broker lifecycle in one cleanup/finalization boundary
  covering success, ordinary exception, critical LLM abort, user stop, pause
  termination, and forced process exit.
- Only success may seal a fixture. Every non-success terminal path persists an
  ineligible receipt/outcome, clears the process-global evidence session, and
  never publishes a finalized fixture.
- Route any broker `os._exit` path through an evidence-aware terminal
  persistence/cleanup hook before the forced exit; `try/finally` alone cannot
  intercept `os._exit`.
- Resolve one immutable `ExecutionCostModel` object per run. For stress
  scenarios, use
  `scale = target / (spread_bps / 2 + slippage_bps + fee_bps)`, preserve
  component proportions, and version the scenario. Write that exact resolved
  object into preregistration and pass the same object to
  `create_backtest_emulator`; never post-hoc subtract embedded fill costs.
- Persist replay hashes, complete execution provenance, cost reconciliation,
  benchmark manifest, and decision/fill audit in `BacktestResults`.
- `/backtests/{id}/summary` exposes those read-only fields and never secrets or
  raw prompts/model responses.
- Existing API calls without new fields remain byte-compatible.

- [ ] Run GitNexus impact on `action_create_backtest`,
  `action_summarize_backtest`, `preregister_backtest_experiment`,
  `_run_graph_nexus_with_point_in_time`, and `create_backtest_emulator`. Treat
  the unindexed broker path as HIGH.
- [ ] Write failing matrix-publication, API validation, queue-to-broker loading,
  allowlist, no-live-mutation, cost-object identity/scaling, PIT-chain,
  all-terminal-path cleanup, receipt-finalization, error-state, and
  summary-projection tests.
- [ ] Implement in the smallest helpers possible and run focused suites.
- [ ] Detect staged changes and commit:
  `feat(backtest): bind replay evidence to API runs`.

---

### Task 5: A1 Recovery Hard-Cap Coherence

**Files**

- Modify: `backend/broker.py`
- Modify: `backend/tests/test_residual_sleeve.py`
- Modify: `backend/tests/test_recovery_capture.py`

**Behavior**

- `regime_position_cap_recovery_hard_enforce=false` is byte-compatible.
- When true, current regime must be chop, recovery flag true, bear latch false,
  and recovery cap a finite positive integer.
- Effective cap is
  `min(max_positions, max(max_positions_chop, max_positions_recovery))`.
- Invalid recovery state/config returns the ordinary chop hard cap, never
  `None`.
- Log `regime=recovery` and the effective cap.

- [ ] Re-run/report upstream impact on `_regime_position_cap_hard`; GitNexus
  cannot index the giant broker file, so direct tracing makes this HIGH risk.
- [ ] Write failing default-off, valid recovery, bear-latch, invalid, clamp, and
  sleeve-exclusion tests.
- [ ] Implement, run residual-sleeve/recovery/backfill-cap suites, detect
  changes, and commit:
  `fix(nexus): align broker recovery position cap`.

---

### Task 6: A2 Current-Regime Momentum Breakout Ceiling

**Files**

- Modify: `backend/strategies/graph_nexus_analysis.py`
- Modify: `backend/broker.py`
- Modify: `backend/tests/test_momentum_breakout_add_gates.py`
- Modify: `backend/tests/test_regime_profile.py`

**Behavior**

- Add pure `_resolve_momentum_breakout_nav_cap(config, strategy_cache)`.
- Entire mapping absent: preserve scalar legacy behavior.
- Mapping present: strict validation requires finite `0 < default <= 1`,
  `0 < bull <= 1`, and `0 < recovery <= 1` entries.
- Bull uses `bull` only when current confirmed regime is bull and
  `momentum_breakout_add_respect_gates=true`. Recovery means chop plus recovery
  flag and uses `recovery`; chop, bear, crash, unknown, and downgraded labels
  use `default`, never the previous profile scalar.
- `momentum_breakout_max_nav_pct_by_regime` is protected as base-only during
  `_apply_regime_profile`.

- [ ] Run/report impact on `_apply_regime_profile` and the Graph Nexus
  `run_once` allocation path; treat the unindexed latter as HIGH.
- [ ] Write failing legacy, bull, recovery, downgrade, missing/default,
  required-key, zero/invalid, gate-coherence, and profile-protection tests.
- [ ] Implement, run breakout/regime-profile/bear-gate suites, detect changes,
  and commit:
  `fix(nexus): size breakouts from current regime`.

---

### Task 7: A3 Confirmed-Bull Initial Deployment Ramp

**Files**

- Modify: `backend/strategies/graph_nexus_analysis.py`
- Modify: `backend/broker.py`
- Modify: `backend/tests/test_nexus_v25.py`
- Modify: `backend/tests/test_regime_profile.py`
- Create: `backend/tests/test_bull_initial_deployment_ramp.py`

**Behavior**

- `deployment_ramp_caps_by_regime` absent: byte-compatible global ramp.
- The mapping is base-only and accepts only a `bull` entry containing exactly
  three finite `0 < cap <= 1` values. Invalid mappings fail locally to the
  existing global list.
- Only current confirmed bull may select `[0.50, 0.70, 1.00]`. Recovery
  (`chop` plus recovery flag), plain chop, bear, crash, unknown, and downgraded
  labels always retain the legacy global list and existing chop scaling.
- `_apply_regime_profile` protects the mapping from previous-cycle profile
  overwrites.
- Keep the global deployment index; do not start a new ramp on a later bull
  transition.
- Same decision/bar advances the index at most once.
- Warm boot fast-forwards the deployment index beyond the maximum configured
  global/bull ramp length rather than relying on the broker's hard-coded
  three-step assumption.
- Attribution uses existing order-source, trade, decision, cash, queue, and SPY
  sleeve records to report active-stock budget, sleeve deployment/release,
  queue effects, and effective cap. Task 7 adds no new broker telemetry schema
  and does not change candidate ranking directly.

- [ ] Re-run/report impact on `_compute_available_buy_budget`,
  `_get_deployment_ramp_caps`, `_apply_regime_profile`, and broker warm-boot
  ramp initialization; direct allocation blast radius is HIGH.
- [ ] Write failing default, bull-map, non-bull fallback, invalid map,
  same-bar-stability, late-bull, and warm-boot tests.
- [ ] Implement, run deployment/residual-sleeve/budget suites, detect changes,
  and commit:
  `feat(nexus): add confirmed-bull initial ramp`.

---

### Task 8: A4 Correct Absolute Circuit-Breaker Semantics

**Files**

- Modify: `backend/strategies/graph_nexus_analysis.py`
- Modify: `backend/tests/test_nexus_tier3_phase1.py`
- Modify: `backend/tests/test_nexus_tier3_phase2b.py`
- Create: `backend/tests/test_nexus_evaluate_position_risk.py`

**Behavior**

- `circuit_breaker_regime_adjustment_semantics_v2=false` preserves current
  arithmetic exactly.
- When true: bull `base - abs(adjustment)`, bear
  `base + abs(adjustment)`, chop unchanged, crash immediate-exit sentinel.
- Explicit zero is honored; invalid values fail to zero.
- Existing `max_open_loss_pct` override keeps precedence.
- Extract a pure `_resolve_effective_open_loss_floor(...)` used by the live
  position-risk path so tests cover absolute adjustment, volatility dominance,
  override precedence, and final firing equality in one resolver.
- Tests cover HIGH, MID/LOW below the low-vol threshold, MID/LOW with
  volatility floor dominant, equality, unknown/chop, crash, and override.
- Telemetry separates absolute, volatility, override, and final effective
  floors.

- [ ] Re-run/report impact on `_get_conviction_aware_floor`; direct tracing
  makes this HIGH risk despite the unindexed giant file.
- [ ] Write failing flag-on helper tests and integration tests through the
  actual position-risk decision, including effective-floor precedence and
  equality firing, while preserving existing flag-off assertions.
- [ ] Implement, run circuit/drawdown/fast-loser/re-entry suites, detect
  changes, and commit:
  `fix(nexus): correct gated regime stop semantics`.

---

### Task 9: API Evidence Matrix Runner and Development Validation

**Files**

- Create: `scripts/run_nexus_evidence_matrix.py`
- Create: `scripts/nexus_evidence_windows.json`
- Create: `backend/tests/test_nexus_evidence_matrix_script.py`
- Modify: `scripts/run_validation_backtest.py`

**Requirements**

- Load API URL/auth from repository `.env` using existing helpers; never print
  tokens, passwords, Alpaca keys, or fixture response contents.
- Queue one run at a time, print its ID immediately, and poll no more frequently
  than every 15 minutes until terminal status. Return promptly when terminal.
- Never call an instance start endpoint.
- Publish one immutable complete matrix manifest before the first backtest POST.
  It preregisters baseline, A1, A2, A3, A4, predeclared combinations, all
  windows/costs, fixture count, fixture-build IDs, per-build arm recording
  order, trial count, failure rules, bootstrap seed, and selection rule. Every
  build run carries matrix, arm, cost-scenario, and fixture-build IDs; formal
  replay carries the resulting sealed replay-fixture ID.
- First run a single-window branch-union feasibility bundle. Record the first
  preregistered arm, then run later arms in `record_extend`: replay common rows
  and add only new branch-specific rows. Seal only after every declared branch
  completes, then run replay-only baseline and candidate arms. Stop before the
  full matrix on any common-row conflict, replay miss, unstable request set, or
  trade-hash mismatch.
- Support at least ten independently recorded sealed union fixtures per primary
  development window and cost scenario. Cost scenarios use separate fixture
  builds because full-path cost changes can alter later requests. Repeated
  replays of one fixture remain one observation. Report common-request share
  and branch-specific request-set hashes; call a result matched-output only
  when sets are identical.
- Failed, stopped, missing, replay-incomplete, or provenance-invalid arms are
  gate failures and remain in the registered trial count; never silently drop
  or retry them under a new ID.
- Compute confidence bounds with a versioned hierarchical fixture/session
  bootstrap: for each of 2,000 seeded draws, resample complete fixture rows,
  draw one contiguous five-session block sequence shared across those rows,
  compute each row's mean daily active return, and take the median across rows.
  Use the 5th/95th percentiles as the two-sided 90% interval. For safety deltas,
  resample paired baseline/candidate rows. Never pool repeated market dates as
  independent sessions.
- Produce a machine-readable comparison containing aligned SPY alpha, net
  costs, max drawdown, per-window paired deltas, trade/request hashes,
  common/branch request attribution, ledger-based leave-largest-winner-out
  concentration sensitivity, and gate verdicts. Do not claim the sensitivity
  is a counterfactual exclusion rerun.
- Refuse to promote on any provenance mismatch or gate failure.

- [ ] Write failing matrix-preregistration, build/final-ID, cost-keyed payload,
  branch-union sequencing, replay-feasibility, failure-accounting, parser,
  redaction, 15-minute polling, hierarchical-bootstrap, pairing, and
  SPY-comparison tests using a fake HTTP client and clock.
- [ ] Implement and run script tests without making network calls.
- [ ] Run the complete relevant unit suite and a branch-wide targeted suite.
- [ ] Detect staged changes and commit:
  `test(nexus): add deterministic evidence matrix runner`.
- [ ] Only after deployed code is healthy and no other backtest is active,
  launch the development matrix through the API. Do not push/redeploy during
  active runs.
- [ ] Compare bull/rally with SPY and verify bear/flip/full paired regression
  gates. Leave all failing candidates disabled.

---

### Task 10: Whole-Branch Verification and Inactive Handoff

- [ ] Run the complete focused suite for replay, PIT, costs, API, recovery,
  breakout, deployment, circuit breaker, bear safety, and secret scrubbing.
- [ ] Run `git diff --check`, `git status --short`, and
  `gitnexus_detect_changes({scope:"compare", base_ref:"main"})`.
- [ ] Request a whole-branch code review using the SDD review package.
- [ ] Resolve every Critical/Important finding through the SDD fix loop.
- [ ] Confirm production strategy/live documents were not mutated and
  `alpaca-main.runCommand=false`.
- [ ] Promotion/deployment is allowed only for the exact passing artifact. If
  development gates pass but the global promotion contract does not, report
  research/paper status rather than live-ready.
