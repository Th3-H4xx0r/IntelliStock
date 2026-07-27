# Production Live-Readiness Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Correct the research, execution, security, risk, and promotion defects that currently prevent IntelliStock from being truthfully eligible for automated real-money trading, while keeping the real-money instance stopped.

**Architecture:** Add focused event-time, simulation, live-order, risk-state, and readiness boundaries around the existing broker and `benchmark_alpha` modules. Preserve broker adapters as transport clients, but route every order source through one durable lifecycle; make research consume immutable point-in-time inputs and normalized broker events. Complete the work as independently reviewable TDD tasks and deploy only inactive artifacts.

**Tech Stack:** Python 3.14, pytest, dataclasses/enums, pandas/NumPy, RethinkDB, FastAPI, Alpaca SDK, Docker Compose, GitNexus.

## Global Constraints

- Baseline source is commit `739d8f3` or a descendant containing the approved design.
- `alpaca-main` and every real-money instance remain stopped for the entire plan.
- Do not enable `runCommand`, invoke a broker write API, cancel an order, or alter a live position.
- Deployment validation is read-only and must prove the account and order set are unchanged.
- Do not print, log, fixture, commit, or return any secret or account identifier.
- New exposure fails closed on unknown data, cash, positions, calendar, kill-switch, persistence, risk state, configuration, or watchdog health.
- Portfolio, cash, capacity, and protective state change only from confirmed cumulative fill events.
- Existing legacy behavior may remain only behind an explicitly non-promotable compatibility mode.
- Every production-symbol edit requires upstream GitNexus impact analysis before editing. HIGH or CRITICAL findings must be reported before proceeding.
- Every commit stages only files owned by its task and requires `gitnexus_detect_changes({scope:"staged"})`.
- Each task follows red-green-refactor TDD and receives specification-compliance and code-quality review.
- Existing user changes and untracked backtest artifacts in the original worktree are never copied, staged, overwritten, or deleted.
- No task may claim the 60-trading-day paper/shadow observation has completed.

---

## File Structure

**New focused modules:**

- `backend/event_time.py` — availability timestamps and simulation-clock contracts.
- `backend/point_in_time_data.py` — immutable manifest and point-in-time filtering.
- `backend/simulated_execution.py` — next-event fills and versioned cost model.
- `backend/experiment_registry.py` — immutable attempt registration and provenance.
- `backend/live_orders/types.py` — immutable intent/event/lifecycle types.
- `backend/live_orders/gate.py` — unified exposure and dependency gate.
- `backend/live_orders/store.py` — durable lifecycle persistence.
- `backend/live_orders/service.py` — submit/retry/event application orchestration.
- `backend/live_orders/reconcile.py` — broker-first startup reconciliation.
- `backend/live_risk_state.py` — versioned account and residual-sleeve state.
- `backend/live_readiness.py` — composable readiness checks and state machine.
- `backend/credential_audit.py` — schema-aware plaintext-secret inventory.
- `backend/scripts/migrate_encrypted_credentials.py` — dry-run-first copy/verify/switch migration.
- `backend/scripts/verify_inactive_deployment.py` — read-only deployment and account invariants.

**Primary existing integration points:**

- `backend/backtest_prices_cursor.py`
- `backend/backtest_price_history.py`
- `backend/portfolio_emulator.py`
- `backend/backtest_summary.py`
- `backend/broker.py`
- `backend/broker_adapters/base.py`
- `backend/broker_adapters/alpaca.py`
- `backend/broker_adapters/robinhood.py`
- `backend/nexus_runtime_state.py`
- `backend/live_state.py`
- `backend/instance.py`
- `backend/secret_store.py`
- `backend/interactive_utils.py`
- `backend/live_kill_switch.py`
- `backend/strategy_cache_persistence.py`
- `backend/strategies/graph_nexus_analysis.py`
- `backend/benchmark_alpha/metrics.py`
- `backend/benchmark_alpha/research.py`
- `backend/benchmark_alpha/rethink_store.py`
- `backend/api/main.py`
- `backend/server.py`
- `docker-compose.yml`

---

### Task 1: Strict Secret Boundary and Read-Only Credential Inventory

**Files:**
- Create: `backend/credential_audit.py`
- Create: `backend/scripts/migrate_encrypted_credentials.py`
- Modify: `backend/secret_store.py`
- Modify: `backend/interactive_utils.py`
- Modify: `backend/live_kill_switch.py`
- Test: `backend/tests/test_credential_audit.py`
- Test: `backend/tests/test_secret_store.py`
- Test: `backend/tests/test_interactive_utils_brokerages.py`
- Test: `backend/tests/test_live_kill_switch_robinhood.py`

**Interfaces:**
- Produces `decrypt_required(stored: str | None, *, field: str) -> str`.
- Produces `SecretFinding(table: str, row_id_hash: str, field: str, encrypted: bool)`.
- Produces `scan_secret_fields(rows_by_table: Mapping[str, Iterable[dict]]) -> tuple[SecretFinding, ...]`.
- Produces a migration CLI whose default is dry-run and whose output contains counts and hashes only.

- [ ] **Step 1: Run upstream impact analysis before editing existing symbols**

Run GitNexus impact for `decrypt`, `action_link_alpaca`,
`action_create_model`, `action_update_model`, and
`_persist_rh_refreshed_token`. Record direct callers, processes, and risk in
the task report.

- [ ] **Step 2: Write failing strict-decryption and inventory tests**

```python
def test_decrypt_required_rejects_plaintext():
    with pytest.raises(RuntimeError, match="plaintext secret"):
        decrypt_required("live-secret", field="alpaca_secret")


def test_inventory_never_returns_secret_values():
    findings = scan_secret_fields({
        "BrokerageAccounts": [{"id": "acct-1", "alpaca_secret": "CANARY"}],
    })
    encoded = repr(findings)
    assert "CANARY" not in encoded
    assert findings[0].field == "alpaca_secret"
    assert findings[0].encrypted is False
```

- [ ] **Step 3: Run the tests and verify RED**

Run:

```bash
python3 -m pytest -q \
  backend/tests/test_credential_audit.py \
  backend/tests/test_secret_store.py
```

Expected: import or assertion failures because the strict APIs do not exist.

- [ ] **Step 4: Implement strict secret APIs and schema-aware inventory**

```python
def decrypt_required(stored, *, field):
    if not is_encrypted(stored):
        raise RuntimeError(f"{field}: plaintext secret is forbidden")
    value = decrypt(stored)
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"{field}: decrypted secret is empty")
    return value
```

`scan_secret_fields` must use an explicit per-table field allowlist. It hashes
row IDs with SHA-256 and never includes stored values in findings, logs, or
exceptions.

- [ ] **Step 5: Remove every plaintext write fallback**

`action_link_alpaca`, Robinhood linking/update paths, model create/update, and
kill-switch token refresh must call `encrypt()` and propagate failure. A
missing `INTELLISTOCK_CRED_KEY` is a failed operation, not a warning.

- [ ] **Step 6: Implement copy-verify-switch migration primitives**

The script must expose pure functions:

```python
def build_encrypted_patch(row: dict, *, fields: tuple[str, ...]) -> dict:
    patch = {}
    for field in fields:
        value = row.get(field)
        if value in (None, ""):
            continue
        patch[field] = value if is_encrypted(value) else encrypt(str(value))
    return patch


def verify_patch(patch: dict, *, fields: tuple[str, ...]) -> None:
    for field in fields:
        value = patch.get(field)
        if value in (None, ""):
            continue
        decrypt_required(value, field=field)
```

CLI mutation requires both `--apply` and `--backup-file <0600 path>`. The plan
does not run `--apply`; tests use in-memory rows.

- [ ] **Step 7: Verify GREEN and regression coverage**

Run:

```bash
python3 -m pytest -q \
  backend/tests/test_credential_audit.py \
  backend/tests/test_secret_store.py \
  backend/tests/test_interactive_utils_brokerages.py \
  backend/tests/test_live_kill_switch_robinhood.py
```

- [ ] **Step 8: Detect changes and commit**

Run staged GitNexus change detection and commit only this task:

```bash
git commit -m "fix(security): enforce encrypted credential storage"
```

---

### Task 2: Inactive Deployment and Control-Plane Containment

**Files:**
- Create: `backend/live_readiness.py`
- Create: `backend/scripts/verify_inactive_deployment.py`
- Modify: `backend/api/main.py`
- Modify: `backend/instance.py`
- Modify: `backend/server.py`
- Modify: `docker-compose.yml`
- Test: `backend/tests/test_live_readiness.py`
- Test: `backend/tests/test_alpha_api.py`
- Test: `backend/tests/test_instance_live_readiness.py`
- Test: `backend/tests/test_server_socket_ownership.py`
- Test: `backend/tests/test_docker_compose_security.py`

**Interfaces:**
- Produces `ReadinessState` with `RESEARCH`, `PAPER_ELIGIBLE`,
  `CANARY_ELIGIBLE`, `LIVE_ELIGIBLE`, and `LIVE_RUNNING`.
- Produces immutable `ReadinessCheck(name, passed, reason, evidence_hash)`.
- Produces `ReadinessReport(instance_id, state, checks, artifact_hash)`.
- Produces `assert_live_start_allowed(report: ReadinessReport) -> None`.

- [ ] **Step 1: Run impact analysis**

Analyze `api_alpha_readiness`, the instance broker-start function, and the
Socket.IO connection/duplicate-client handler before edits. Report any
HIGH/CRITICAL risk.

- [ ] **Step 2: Write failing readiness and containment tests**

```python
def test_live_start_refuses_an_unmet_check():
    report = ReadinessReport(
        instance_id="alpaca-main",
        state=ReadinessState.RESEARCH,
        checks=(ReadinessCheck("secrets", False, "plaintext", "abc"),),
        artifact_hash="artifact-hash",
    )
    with pytest.raises(LiveReadinessError, match="secrets"):
        assert_live_start_allowed(report)


def test_inactive_verifier_rejects_any_order_delta():
    before = AccountInvariant.from_docs(positions=[], orders=[])
    after = AccountInvariant.from_docs(positions=[], orders=[{"id": "new"}])
    assert compare_account_invariants(before, after).passed is False
```

Add a server test proving an unauthenticated duplicate UUID cannot terminate
another worker.

- [ ] **Step 3: Run tests and verify RED**

```bash
python3 -m pytest -q \
  backend/tests/test_live_readiness.py \
  backend/tests/test_alpha_api.py \
  backend/tests/test_instance_live_readiness.py \
  backend/tests/test_server_socket_ownership.py \
  backend/tests/test_docker_compose_security.py
```

- [ ] **Step 4: Implement composable readiness and honest API output**

The readiness API returns each check, evidence hash, current state, and every
failure reason. It never returns `readiness_ok=True` unless state is
`LIVE_ELIGIBLE` or `LIVE_RUNNING`.

- [ ] **Step 5: Enforce readiness at the real-money start boundary**

`instance.py` must evaluate the signed/fingerprinted report immediately before
spawning a real-money broker. Paper and backtest modes cannot satisfy a live
check by type coercion.

- [ ] **Step 6: Harden control-plane ownership and Docker bindings**

Duplicate client identifiers require authenticated ownership and cannot call
`os._exit` on another worker. Bind RethinkDB driver/admin ports to
`127.0.0.1` or remove host publication in the production profile.

- [ ] **Step 7: Implement read-only inactive verification**

`verify_inactive_deployment.py` reads instance state, positions, and orders
twice around artifact validation and exits nonzero if `runCommand` becomes
true or any broker object changes. It has no broker-write imports.

- [ ] **Step 8: Verify GREEN, detect changes, and commit**

Run the five test files above, GitNexus staged change detection, and commit:

```bash
git commit -m "feat(readiness): enforce inactive live deployment gates"
```

---

### Task 3: Event-Time Availability and Intraday Cursor Causality

**Files:**
- Create: `backend/event_time.py`
- Modify: `backend/backtest_prices_cursor.py`
- Modify: `backend/backtest_price_history.py`
- Modify: `backend/broker.py`
- Test: `backend/tests/test_event_time.py`
- Test: `backend/tests/test_prices_cursor.py`
- Test: `backend/tests/test_price_history_cursor.py`
- Test: `backend/tests/test_backtest_no_same_bar_fill.py`

**Interfaces:**
- Produces `SimulationClock(decision_at, available_through, execute_not_before)`.
- Produces `BarInterval(start, end)` and
  `bar_available_at(bar, *, interval, session_close_resolver) -> datetime`.
- Cursor APIs receive `bar_available_at: Callable[[dict], datetime]`.

- [ ] **Step 1: Run impact analysis**

Analyze `latest_price_at`, `get_price_history_up_to_current`, and the enclosing
broker price-resolution function. Treat dynamic broker imports as manual
HIGH-risk integration even if GitNexus reports fewer callers.

- [ ] **Step 2: Replace leakage-locking tests with failing causal tests**

```python
def test_hour_bar_close_is_not_visible_at_its_open():
    bar = {"t": "2026-03-02T14:00:00Z", "c": 123.0}
    assert bar_available_at(
        bar, interval=timedelta(hours=1), session_close_resolver=None
    ) == datetime(2026, 3, 2, 15, 0, tzinfo=timezone.utc)


def test_latest_price_waits_until_bar_close():
    at_open = latest_price_at(
        {"SPY": [BAR]}, ["SPY"], OPEN,
        bar_time_to_datetime=parse,
        bar_available_at=availability,
    )
    assert "SPY" not in at_open
```

Daily tests must prove a same-session daily close is unavailable before the
exchange session closes.

- [ ] **Step 3: Run tests and verify RED**

```bash
python3 -m pytest -q \
  backend/tests/test_event_time.py \
  backend/tests/test_prices_cursor.py \
  backend/tests/test_price_history_cursor.py \
  backend/tests/test_backtest_no_same_bar_fill.py
```

- [ ] **Step 4: Implement the event-time contract**

Use aware UTC datetimes internally. Reject naive timestamps at the new module
boundary. Bar eligibility compares `available_at <= available_through`, never
bar label alone.

- [ ] **Step 5: Integrate cursor callers**

Broker backtests construct one availability resolver from the actual
granularity and exchange calendar. Crypto daily/session behavior remains
explicit rather than inheriting equity session assumptions.

- [ ] **Step 6: Add a full-loop regression**

At decision time `14:00`, a signal derived from the `14:00–15:00` bar must not
exist. At `15:00`, the bar may inform a signal but its order has
`execute_not_before > 15:00` or the next quote/bar event.

- [ ] **Step 7: Verify GREEN, detect changes, and commit**

```bash
git commit -m "fix(backtest): enforce bar-close availability"
```

---

### Task 4: Point-in-Time News, Daily Overlay, Graph, Fundamentals, and Universe

**Files:**
- Create: `backend/point_in_time_data.py`
- Modify: `backend/strategies/graph_nexus_analysis.py`
- Modify: `backend/ticker_universe.py`
- Test: `backend/tests/test_point_in_time_data.py`
- Test: `backend/tests/test_nexus_daily_overlay_pit.py`
- Test: `backend/tests/test_nexus_news_pit.py`
- Test: `backend/tests/test_nexus_graph_snapshot_pit.py`
- Test: `backend/tests/test_ticker_universe_pit.py`

**Interfaces:**
- Produces immutable `DatasetManifest(manifest_id, source_hashes, created_at)`.
- Produces `PointInTimeContext(as_of, manifest, strict=True)`.
- Produces `filter_available(records, *, context, available_at) -> tuple`.
- Historical Graph Nexus entry points require `PointInTimeContext`.

- [ ] **Step 1: Run impact analysis**

Analyze each located daily-overlay, article-cache, graph-query, fundamental,
and universe-selection symbol before editing. If GitNexus cannot resolve a
nested function, record UNKNOWN risk and manually enumerate call sites before
the edit.

- [ ] **Step 2: Write failing future-record rejection tests**

```python
def test_strict_context_rejects_future_article():
    ctx = PointInTimeContext(as_of=ts("2026-03-02T14:00Z"), manifest=MANIFEST)
    rows = filter_available(
        [article("13:59"), article("14:01")],
        context=ctx,
        available_at=lambda row: row.published_at,
    )
    assert [r.id for r in rows] == ["13:59"]


def test_missing_historical_graph_snapshot_fails():
    with pytest.raises(PointInTimeDataError, match="graph snapshot"):
        load_graph_snapshot(context=STRICT_CONTEXT, store=EMPTY_STORE)
```

- [ ] **Step 3: Run tests and verify RED**

Run the five task test files.

- [ ] **Step 4: Implement immutable manifests and strict filtering**

Every derived cache key includes manifest ID and `as_of`. Cached sentiment is
computed from the already-filtered article set. A full-day cached aggregate is
never reused for an earlier timestamp.

- [ ] **Step 5: Remove current-state historical fallbacks**

Historical backtests fail with a typed error when dated graph, fundamental, or
universe data is missing. Live mode continues to use current state through an
explicit live context.

- [ ] **Step 6: Correct daily overlay availability**

Daily final closes enter the context only after the corresponding exchange
session close. Prefetch may download a wider range but may not expose records
through the context.

- [ ] **Step 7: Verify GREEN, detect changes, and commit**

```bash
git commit -m "fix(research): require point-in-time strategy inputs"
```

---

### Task 5: Next-Event Execution, Costs, and Confirmed-Fill Accounting

**Files:**
- Create: `backend/simulated_execution.py`
- Modify: `backend/portfolio_emulator.py`
- Modify: `backend/broker.py`
- Modify: `backend/backtest_summary.py`
- Test: `backend/tests/test_simulated_execution.py`
- Test: `backend/tests/test_portfolio_emulator_fills.py`
- Test: `backend/tests/test_backtest_execution_costs.py`
- Test: `backend/tests/test_backtest_pnl_consistency.py`

**Interfaces:**
- Produces `SimulationOrder`, `SimulationQuote`, `SimulationFill`.
- Produces `ExecutionCostModel(version, spread_bps, slippage_bps, fee_bps, latency)`.
- Produces `NextEventExecutionSimulator.submit(order)` and
  `on_quote(quote) -> tuple[SimulationFill, ...]`.
- Produces `PortfolioEmulator.apply_fill(fill) -> None`.

- [ ] **Step 1: Run impact analysis**

Analyze `PortfolioEmulator.buy`, `sell`, `execute_signal`,
`compute_backtest_summary`, and the enclosing broker backtest order-emission
function.

- [ ] **Step 2: Write failing next-event and cost tests**

```python
def test_order_cannot_fill_on_decision_event():
    sim = NextEventExecutionSimulator(COSTS)
    sim.submit(order(decision_at=T0, execute_not_before=T1))
    assert sim.on_quote(quote(T0, bid=99, ask=101)) == ()
    assert sim.on_quote(quote(T1, bid=100, ask=102))[0].price > 102


def test_accounting_changes_only_on_fill():
    emu = PortfolioEmulator(10_000)
    before = emu.get_cash()
    emu.record_order(order(decision_at=T0, execute_not_before=T1))
    assert emu.get_cash() == before
    emu.apply_fill(fill(side="buy", quantity=10, price=101, fees=1))
    assert emu.get_cash() == before - 1_011
```

- [ ] **Step 3: Run tests and verify RED**

Run the four task test files.

- [ ] **Step 4: Implement normalized simulated broker events**

Fills contain cumulative and incremental quantities, price, fees, quote
timestamp, execution timestamp, order ID, and cost-model version. Reject
non-finite or nonpositive required fields.

- [ ] **Step 5: Route equity backtests through the simulator**

Signals create pending orders. The next eligible quote/bar drives fills. Remove
direct same-cycle `buy`/`sell` execution from the promoted equity backtest path.
Legacy immediate helpers remain only for non-promotable compatibility tests.

- [ ] **Step 6: Persist cost and fill provenance in results**

Backtest results include cost-model version, total fees, spread/slippage cost,
unfilled/rejected counts, and fill provenance. Promotion refuses rows lacking
these fields.

- [ ] **Step 7: Verify GREEN, detect changes, and commit**

```bash
git commit -m "feat(backtest): model next-event execution costs"
```

---

### Task 6: Immutable Experiment Registry and Correct SPY Benchmark Wiring

**Files:**
- Create: `backend/experiment_registry.py`
- Modify: `backend/benchmark_alpha/research.py`
- Modify: `backend/benchmark_alpha/rethink_store.py`
- Modify: `backend/benchmark_alpha/metrics.py`
- Modify: `backend/backtest_summary.py`
- Modify: `backend/broker.py`
- Test: `backend/tests/test_experiment_registry.py`
- Test: `backend/tests/test_alpha_research.py`
- Test: `backend/tests/test_alpha_metrics.py`
- Test: `backend/tests/test_alpha_benchmark.py`

**Interfaces:**
- Produces immutable `ExperimentSpec` and `RegisteredExperiment`.
- Produces `register_before_run(spec) -> RegisteredExperiment`.
- Produces `complete_experiment(experiment_id, result) -> None`.
- `compute_active_metrics(aligned, *, trials: int)` requires an explicit
  positive trial count.

- [ ] **Step 1: Run impact analysis**

Analyze `compute_active_metrics`, `compute_backtest_summary`, the broker
backtest-result writer, and research-store insert/update functions.

- [ ] **Step 2: Write failing registry and benchmark tests**

```python
def test_failed_attempt_still_counts_as_a_trial():
    registry.register_before_run(SPEC)
    registry.fail(SPEC.experiment_id, "data_missing")
    assert registry.trial_count(scope=SPEC.search_scope) == 1


def test_metrics_refuse_implicit_trial_count():
    with pytest.raises(TypeError):
        compute_active_metrics(ALIGNED)
```

Add a broker integration test proving the benchmark series is passed to
`compute_backtest_summary` and timestamps are aligned rather than positionally
zipped.

- [ ] **Step 3: Run tests and verify RED**

Run the four task test files.

- [ ] **Step 4: Implement canonical experiment fingerprints**

Fingerprint commit/source, complete effective config, model/provider/prompts,
seed/repeats, dataset/graph/universe/benchmark manifests, execution-cost model,
dates, and fold. Registration is append-only and occurs before any model call.

- [ ] **Step 5: Make benchmark alignment timestamp-keyed**

Use adjusted SPY total-return values keyed by valuation timestamp. Missing
portfolio or benchmark observations produce an explicit incomplete result;
they are not silently zipped or dropped for promotion.

- [ ] **Step 6: Use actual historical trial counts**

Remove `trials=1`. Every Deflated Sharpe calculation receives the registry
count for the declared search scope.

- [ ] **Step 7: Verify GREEN, detect changes, and commit**

```bash
git commit -m "feat(research): register trials and align SPY alpha"
```

---

### Task 7: Unified Immutable Order Gate

**Files:**
- Create: `backend/live_orders/__init__.py`
- Create: `backend/live_orders/types.py`
- Create: `backend/live_orders/gate.py`
- Modify: `backend/broker.py`
- Modify: `backend/live_state.py`
- Test: `backend/tests/test_live_order_types.py`
- Test: `backend/tests/test_live_order_gate.py`
- Test: `backend/tests/test_manual_order_gate.py`
- Test: `backend/tests/test_residual_sleeve_live_gate.py`

**Interfaces:**
- Produces immutable `OrderIntent`, `DependencySnapshot`, and `GateDecision`.
- Produces `UnifiedOrderGate.evaluate(intent, snapshot) -> GateDecision`.
- Every strategy, manual, risk-exit, and residual-sleeve source creates an
  `OrderIntent`; no source calls `adapter.submit_order` directly.

- [ ] **Step 1: Run impact analysis**

Analyze every direct `submit_order`, `buy`, and `sell` caller in `broker.py`,
manual command handlers, and residual-sleeve deploy/release helpers. Report the
manual blast radius as CRITICAL because it controls real orders.

- [ ] **Step 2: Write failing fail-closed and reduce-only tests**

```python
@pytest.mark.parametrize("field", [
    "kill_switch", "quote", "cash", "positions", "calendar",
    "persistence", "risk_state", "watchdog",
])
def test_unknown_dependency_blocks_new_exposure(field):
    snap = dataclasses.replace(healthy_snapshot(), **{field: Health.UNKNOWN})
    assert gate.evaluate(BUY_INTENT, snap).allowed is False


def test_reduce_only_quantity_is_capped_to_fresh_position():
    decision = gate.evaluate(
        sell_intent(quantity=12, reduce_only=True),
        healthy_snapshot(position_quantity=5),
    )
    assert decision.approved_quantity == Decimal("5")
```

- [ ] **Step 3: Run tests and verify RED**

Run the four task test files.

- [ ] **Step 4: Implement immutable typed contracts**

The intent includes account/instance/source/reason, symbol/side/quantity,
reduce-only, decision/quote timestamps, risk snapshot ID, and deterministic
idempotency key with retry ordinal.

- [ ] **Step 5: Implement one gate for all sources**

Gate ordering is identity/arming, dependency health, reduce-only containment,
market/quote, cash/positions, exposure limits, open-order/idempotency, and
authorization. It returns all reason codes and performs no I/O.

- [ ] **Step 6: Replace direct caller paths**

Manual commands, normal strategy signals, risk exits, and SPY/SQQQ sleeve
actions enqueue approved intents through one service seam. Add a static/AST
test that fails if broker order transport is called outside the service and
adapter compatibility methods.

- [ ] **Step 7: Verify GREEN, detect changes, and commit**

```bash
git commit -m "feat(live): centralize all orders behind one gate"
```

---

### Task 8: Durable Order Lifecycle and Confirmed-Fill State

**Files:**
- Create: `backend/live_orders/store.py`
- Create: `backend/live_orders/service.py`
- Modify: `backend/nexus_runtime_state.py`
- Modify: `backend/broker_adapters/_wal.py`
- Modify: `backend/broker_adapters/alpaca.py`
- Modify: `backend/broker_adapters/robinhood.py`
- Test: `backend/tests/test_live_order_store.py`
- Test: `backend/tests/test_live_order_service.py`
- Test: `backend/tests/test_live_order_crash_matrix.py`
- Test: `backend/tests/test_live_order_partial_fills.py`
- Test: `backend/tests/test_live_order_retry.py`

**Interfaces:**
- Produces `LifecycleState` and immutable `BrokerOrderEvent`.
- Produces `OrderLifecycleStore.append(event)` with compare-and-set sequencing.
- Produces `LiveOrderService.submit(intent)` and `apply_broker_event(event)`.
- Produces `new_retry_intent(terminal_intent, reason)`.

- [ ] **Step 1: Run impact analysis**

Analyze existing WAL record/transition functions and both adapter
`submit_order` and trade-update handlers. Warn before editing because the
adapter methods are CRITICAL transport paths.

- [ ] **Step 2: Write failing lifecycle and crash-matrix tests**

```python
def test_acknowledgement_does_not_release_capacity():
    service.submit(INTENT)
    service.apply_broker_event(ack_event())
    assert portfolio.cash == START_CASH
    assert portfolio.positions == {}
    assert reservations.active(INTENT.id)


def test_duplicate_cumulative_fill_is_exactly_once():
    service.apply_broker_event(fill_event(cumulative="3"))
    service.apply_broker_event(fill_event(cumulative="3"))
    assert portfolio.quantity("SPY") == Decimal("3")
```

Table-drive crashes before/after intent persist, broker submit, ack persist,
partial fill, final fill, rejection, cancellation, and accounting.

- [ ] **Step 3: Run tests and verify RED**

Run the five task test files.

- [ ] **Step 4: Implement append-only transitions**

Legal transitions are recorded, not overwritten. Each event includes account,
client/broker order IDs, event ID, cumulative and incremental fill quantity,
price, fees, timestamp, and sequence.

- [ ] **Step 5: Update adapters to emit normalized events**

Adapters remain transport clients. They do not mutate portfolio accounting
from submission success. Polling and streaming feed the same idempotent event
application path.

- [ ] **Step 6: Implement bounded terminal retries**

Rejected/canceled/expired orders may produce a new retry intent with
`retry_ordinal + 1`, a new client ID, and a configured maximum. The prior WAL
record remains terminal.

- [ ] **Step 7: Verify GREEN, detect changes, and commit**

```bash
git commit -m "feat(live): add durable confirmed-fill lifecycle"
```

---

### Task 9: Broker-First Startup Reconciliation and Ownership

**Files:**
- Create: `backend/live_orders/reconcile.py`
- Modify: `backend/broker_adapters/_classifier.py`
- Modify: `backend/broker_adapters/alpaca.py`
- Modify: `backend/broker_adapters/robinhood.py`
- Modify: `backend/broker.py`
- Test: `backend/tests/test_startup_reconciliation.py`
- Test: `backend/tests/test_clean_room_scenarios.py`
- Test: `backend/tests/test_clean_room_adapter_init.py`

**Interfaces:**
- Produces `ReconciliationResult(events, owned, external, unresolved, healthy)`.
- Produces `StartupReconciler.reconcile(account_snapshot, wal)`.
- Adapter ownership classification consumes a completed reconciliation result.

- [ ] **Step 1: Run impact analysis**

Analyze adapter constructors, classifier functions, broker adapter creation,
and restart reconciliation. Treat ordering changes as CRITICAL.

- [ ] **Step 2: Write the failing crash-after-fill regression**

```python
def test_fill_before_wal_update_is_owned_after_one_restart():
    broker = FakeBroker(positions={"CAR": 4}, orders=[filled_order("cid-1", 4)])
    wal = wal_with_acknowledged_intent("cid-1")
    result = StartupReconciler(broker, wal).run()
    assert result.owned == {"CAR": Decimal("4")}
    assert result.unresolved == {}
```

Add tests proving a new manual position created while stopped remains external,
partial fills are split correctly, and unresolved state blocks exposure.

- [ ] **Step 3: Run tests and verify RED**

Run the three task test files.

- [ ] **Step 4: Implement mandatory startup ordering**

Broker snapshot and WAL reconciliation execute before owned/external
classification and before strategy reconstruction. Reconciled fills generate
the same normalized events as live streaming.

- [ ] **Step 5: Remove implicit adoption**

Unknown symbols and quantities default to external/quarantined unless a
confirmed strategy intent/fill proves ownership.

- [ ] **Step 6: Verify GREEN, detect changes, and commit**

```bash
git commit -m "fix(live): reconcile fills before ownership classification"
```

---

### Task 10: Persistent Risk State, Continuous Drawdown, and SQQQ Safety

**Files:**
- Create: `backend/live_risk_state.py`
- Modify: `backend/strategy_cache_persistence.py`
- Modify: `backend/strategies/graph_nexus_analysis.py`
- Modify: `backend/broker.py`
- Modify: `backend/live_state.py`
- Test: `backend/tests/test_live_risk_state.py`
- Test: `backend/tests/test_drawdown_circuit.py`
- Test: `backend/tests/test_residual_sleeve.py`
- Test: `backend/tests/test_residual_sleeve_restart.py`
- Test: `backend/tests/test_monitor_drawdown_execution.py`

**Interfaces:**
- Produces versioned `AccountRiskState` and `SleeveRiskState`.
- Produces `RiskStateStore.load_required(instance_id, account_id)`.
- Produces `apply_confirmed_fill(state, event)`.
- Produces `evaluate_drawdown(state, fresh_equity, fresh_marks)`.

- [ ] **Step 1: Run impact analysis**

Analyze `load_with_fallback`, drawdown-halt logic, monitor-cycle risk exits,
residual-sleeve deploy/release/stop helpers, and strategy-cache save/load.
Report HIGH/CRITICAL risk before edits.

- [ ] **Step 2: Write failing restart and continuous-kill tests**

```python
def test_restart_preserves_sqqq_basis_peak_and_cooldown():
    saved = store.save(SLEEVE_STATE)
    assert store.load_required(INSTANCE, ACCOUNT) == saved


def test_monitor_tick_evaluates_account_drawdown():
    result = run_monitor(equity=88, persisted_peak=100)
    assert result.drawdown == Decimal("0.12")
    assert result.new_exposure_allowed is False
```

Add tests proving module/config drift cannot lower the high-water mark, unknown
state fails closed, and losing SQQQ is not exempt from an account kill.

- [ ] **Step 3: Run tests and verify RED**

Run the five task test files.

- [ ] **Step 4: Implement state independent of module hash**

Risk records key by account/instance and schema version, not strategy module
hash. Migrations preserve the maximum observed high-water mark. State changes
come only from fresh broker snapshots and confirmed fills.

- [ ] **Step 5: Evaluate drawdown on every monitor tick**

Fresh broker equity and marks are mandatory. Breach output becomes reduce-only
intents through the unified gate; it never directly calls an adapter.

- [ ] **Step 6: Replace process-local sleeve protection**

Entry basis, peak, stop episode, cooldown, allocation, and outstanding intents
are reconstructed from persisted state and confirmed fills. Unknown state
blocks sleeve exposure. Enforce a continuously checked leveraged-product cap
through the gate rather than acquisition-only sizing.

- [ ] **Step 7: Verify GREEN, detect changes, and commit**

```bash
git commit -m "fix(risk): persist drawdown and leveraged sleeve state"
```

---

### Task 11: Fail-Closed Kill Switch, Watchdog, Manual Commands, and Command Recovery

**Files:**
- Modify: `backend/live_kill_switch.py`
- Modify: `backend/benchmark_alpha/watchdog.py`
- Modify: `backend/benchmark_alpha/watchdog_main.py`
- Modify: `backend/live_state.py`
- Modify: `backend/instance.py`
- Modify: `backend/broker.py`
- Test: `backend/tests/test_live_kill_switch_smoke.py`
- Test: `backend/tests/test_alpha_watchdog.py`
- Test: `backend/tests/test_live_command_recovery.py`
- Test: `backend/tests/test_manual_order_gate.py`
- Test: `backend/tests/test_fail_closed_dependencies.py`

**Interfaces:**
- Produces durable `ControlHealth` evidence consumed by `UnifiedOrderGate`.
- Produces command lease fields `lease_owner`, `lease_expires_at`, and
  `attempt_count`.
- Watchdog health is mandatory for live eligibility.

- [ ] **Step 1: Run impact analysis**

Analyze `halt_live_trading`, watchdog start/monitor functions,
`claim_next_pending`, manual command execution, and broker kill-switch polling.

- [ ] **Step 2: Write failing fail-closed and recovery tests**

```python
def test_kill_switch_read_failure_blocks_buy():
    snapshot = dataclasses.replace(
        healthy_snapshot(), kill_switch=Health.UNKNOWN
    )
    assert gate.evaluate(BUY_INTENT, snapshot).allowed is False


def test_expired_running_command_is_reclaimed_once():
    cmd = store.insert_running(lease_expires_at=PAST, attempt_count=1)
    claimed = store.claim(INSTANCE, worker_id="new")
    assert claimed.id == cmd.id
    assert claimed.attempt_count == 2
```

- [ ] **Step 3: Run tests and verify RED**

Run the five task test files.

- [ ] **Step 4: Make critical reads fail closed for new exposure**

Kill-switch, cash, position, market-calendar, configuration, and watchdog
failures enter the dependency snapshot as unhealthy/unknown. No exception
handler replaces live safety overrides with an identity function.

- [ ] **Step 5: Require and supervise the watchdog**

Real-money readiness fails if the watchdog is absent, stale, or cannot start.
The watchdog reads broker/persistence truth independently and writes durable
health evidence.

- [ ] **Step 6: Lease and recover commands**

Pending commands are atomically leased. Expired `running` commands return to a
reconciliation state; a previously submitted order is reconciled before any
retry. Manual orders recheck arming and containment immediately before submit.

- [ ] **Step 7: Verify GREEN, detect changes, and commit**

```bash
git commit -m "fix(ops): fail closed and recover live commands"
```

---

### Task 12: Statistical Promotion and Enforced Readiness State Machine

**Files:**
- Create: `backend/benchmark_alpha/promotion.py`
- Modify: `backend/benchmark_alpha/research.py`
- Modify: `backend/benchmark_alpha/metrics.py`
- Modify: `backend/api/main.py`
- Modify: `backend/live_readiness.py`
- Create: `backend/scripts/verify_alpha_readiness.py`
- Test: `backend/tests/test_alpha_promotion.py`
- Test: `backend/tests/test_alpha_research.py`
- Test: `backend/tests/test_alpha_metrics.py`
- Test: `backend/tests/test_alpha_api.py`
- Test: `backend/tests/test_live_readiness.py`

**Interfaces:**
- Produces immutable `PromotionEvidence` and `PromotionDecision`.
- Produces `evaluate_promotion(evidence) -> PromotionDecision`.
- Produces append-only promotion records tied to exact artifact hashes.

- [ ] **Step 1: Run impact analysis**

Analyze research split/registry functions, active metrics, readiness API, and
instance readiness consumption.

- [ ] **Step 2: Write table-driven gate tests**

```python
@pytest.mark.parametrize("field,value,reason", [
    ("unseen_months", 11, "unseen_months"),
    ("regime_count", 2, "regime_count"),
    ("bootstrap_active_low", 0.0, "bootstrap"),
    ("information_ratio", 0.74, "information_ratio"),
    ("deflated_sharpe_probability", 0.949, "deflated_sharpe"),
    ("profit_factor_after_costs", 1.0, "profit_factor"),
    ("positive_unseen_quarter_fraction", 0.59, "unseen_quarters"),
])
def test_each_statistical_gate_fails_closed(field, value, reason):
    evidence = dataclasses.replace(passing_evidence(), **{field: value})
    assert reason in evaluate_promotion(evidence).reasons
```

Add failures for artifact mismatch, unresolved ownership, incomplete chaos
tests, plaintext credentials, stale watchdog, and paper days below 60.

- [ ] **Step 3: Run tests and verify RED**

Run the five task test files.

- [ ] **Step 4: Implement pure promotion evaluation**

Return all failure reasons. Require 24 months, 12 unseen months, three regimes,
purged folds, once-only holdout, explicit trial count, costed fills, aligned
SPY, concentration tests, and the numeric thresholds from the approved spec.

- [ ] **Step 5: Persist immutable decisions and enforce state transitions**

Promotion records include every artifact hash. Changed code/config/model/data
invalidates dependent evidence. `LIVE_RUNNING` is unreachable without a
separate explicit activation action; this plan never invokes it.

- [ ] **Step 6: Implement read-only CLI and API evidence**

The CLI emits JSON and a human table and exits zero only for the requested
eligible state. It cannot mutate instance state.

- [ ] **Step 7: Verify GREEN, detect changes, and commit**

```bash
git commit -m "feat(alpha): enforce statistical promotion readiness"
```

---

### Task 13: Full Regression, Chaos Matrix, Inactive Packaging, and Final Review

**Files:**
- Create: `backend/tests/test_live_readiness_end_to_end.py`
- Create: `backend/tests/test_live_order_chaos.py`
- Create: `backend/tests/test_inactive_deployment.py`
- Create: `.github/workflows/readiness-ci.yml`
- Modify: `docker-compose.yml`
- Modify: `backend/scripts/verify_inactive_deployment.py`

**Interfaces:**
- Produces a versioned readiness evidence bundle with hashes only.
- Produces an inactive deployment verification report.

- [ ] **Step 1: Write an end-to-end stopped-instance test**

```python
def test_production_artifact_cannot_start_with_unmet_calendar_gate():
    report = ReadinessReport(
        instance_id="alpaca-main",
        state=ReadinessState.CANARY_ELIGIBLE,
        checks=(
            ReadinessCheck("engineering", True, "passed", "eng-hash"),
            ReadinessCheck("paper_days", False, "0 of 60", "paper-hash"),
        ),
        artifact_hash="artifact-hash",
    )
    with pytest.raises(LiveReadinessError):
        assert_live_start_allowed(report)
```

- [ ] **Step 2: Add the chaos matrix**

Cover quote/database/calendar/kill-switch/watchdog outages, broker timeouts,
acknowledgement loss, duplicate/partial fills, process crashes at every
lifecycle transition, module/config deploys, and manual-position contamination.
Every case asserts no duplicate order, no unmanaged owned quantity, and no new
exposure while truth is unknown.

- [ ] **Step 3: Run focused suites**

Run all new task tests plus existing broker, WAL, clean-room, risk, readiness,
benchmark-alpha, residual-sleeve, and backtest suites.

- [ ] **Step 4: Run the complete clean backend suite**

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider backend/tests
```

Collection pollution and order-dependent failures are release blockers. Fix
root causes rather than excluding tests.

- [ ] **Step 5: Run frontend, static, and deployment validation**

Use repository-defined frontend test/build commands, Python compilation/type or
lint commands present in project configuration, Docker Compose validation, and
dependency/security scans. Record exact commands and outputs.

- [ ] **Step 6: Run GitNexus final scope analysis**

Run `gitnexus_detect_changes` against the plan baseline and inspect every
affected process. Re-run focused tests for all HIGH/CRITICAL flows.

- [ ] **Step 7: Obtain independent final reviews**

Request specification-compliance, code-quality, statistical-validity,
execution-safety, security, and adversarial tail-risk reviews. Resolve every
critical/high finding and rerun impacted tests.

- [ ] **Step 8: Build and verify inactive artifacts**

Create the deployable artifact without starting the instance. Run the read-only
verifier before and after packaging. Assert:

- instance stopped;
- `runCommand` false;
- zero order delta;
- zero position-quantity delta;
- no live broker write call;
- readiness lists the 60-day observation as unmet unless real evidence exists.

- [ ] **Step 9: Commit final integration evidence**

Run staged change detection and commit only source/tests/docs needed for the
inactive candidate:

```bash
git commit -m "test(readiness): verify inactive production candidate"
```

- [ ] **Step 10: Finish the branch without live activation**

Use the finishing-development-branch workflow. Do not merge/push/deploy without
the requested branch action, and never start `alpaca-main`. Report remaining
calendar/evidence gates separately from engineering completion.
