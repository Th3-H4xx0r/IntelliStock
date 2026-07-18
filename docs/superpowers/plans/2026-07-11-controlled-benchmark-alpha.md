# Controlled Benchmark-Relative Alpha Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace direct Graph Nexus order authority with a fresh-price, benchmark-relative portfolio system that targets SPY +10 percentage points annualized while enforcing a 15% drawdown ceiling and producing statistically defensible evidence before live exposure increases.

**Architecture:** Add a focused `backend/benchmark_alpha/` package for typed forecasts, durable ledgers, allocation, tax controls, risk, execution, research, and promotion. Keep broker-specific market-mark truth in `backend/market_marks.py`; integrate it into the existing adapters and `broker.py` behind an off-by-default mode gate. Graph Nexus and a deterministic challenger become forecast producers, while one allocator and one risk/execution engine own all target weights and orders.

**Tech Stack:** Python 3, pytest, standard-library dataclasses/enums/sqlite3/statistics, NumPy, pandas, scikit-learn calibration, Alpaca `TradingClient`/`StockDataStream`, RethinkDB, FastAPI.

## Global Constraints

- Live equity order generation stays paused until Tasks 0-6 pass their verification gates.
- `benchmark_alpha_mode` defaults to `"off"`; no migration may silently enable shadow, paper, or live operation.
- Normal allocation is 2% cash, 18-58% SPY, and 40-80% active stocks; safety and eligibility may reduce active exposure below 40%.
- Maximums: 10 active stocks, 8% per stock, 20% per active sector, 100% gross exposure, no margin.
- Graph propagation-only forecasts remain shadow-only until an unseen-data ablation proves positive incremental alpha.
- New exposure requires a mark observed within 60 seconds; a broker-position fallback may be at most 120 seconds old.
- Promotion to 80% active requires SIP-quality consolidated data or a documented equivalent.
- Drawdown states are soft at 8%, hard at 12%, and kill at 15%; emergency reduction overrides tax and turnover guards.
- SPY wash-sale controls use FIFO-compatible lots, a 30-day lookback, and a 31-day post-loss-sale repurchase block.
- Backtests and research use point-in-time snapshots, deterministic model-output replay, costs, and a sealed holdout.
- No secret value may be persisted in BacktestResults, alpha ledgers, logs, tests, fixtures, API responses, or migration output.
- Before editing any existing function, class, or method, run `gitnexus_impact({target, direction:"upstream"})`; warn before HIGH/CRITICAL changes.
- Before every commit, run `gitnexus_detect_changes({scope:"staged"})` and review all affected flows.
- Preserve unrelated changes in the dirty worktree. Every commit stages only files named by its task.

---

## File Structure

**New production modules:**

- `backend/market_marks.py` - timestamped market-mark types, precedence, and freshness.
- `backend/alpaca_mark_stream.py` - `StockDataStream` lifecycle and quote/trade callbacks.
- `backend/persistence_safety.py` - recursive secret-safe snapshot sanitizer and assertions.
- `backend/benchmark_alpha/__init__.py` - stable public exports.
- `backend/benchmark_alpha/types.py` - typed run origins, forecasts, targets, intents, and outcomes.
- `backend/benchmark_alpha/local_store.py` - SQLite event spool and durable state.
- `backend/benchmark_alpha/rethink_store.py` - indexed RethinkDB replication and reads.
- `backend/benchmark_alpha/risk.py` - mark-health and portfolio drawdown state machines.
- `backend/benchmark_alpha/watchdog.py` - independent broker/strategy mark and equity watchdog.
- `backend/benchmark_alpha/benchmark.py` - adjusted SPY series and inception/high-water accounting.
- `backend/benchmark_alpha/metrics.py` - active-return and risk statistics.
- `backend/benchmark_alpha/calibration.py` - score-to-excess-return calibration.
- `backend/benchmark_alpha/forecast_adapters.py` - Graph Nexus metadata to typed forecasts.
- `backend/benchmark_alpha/challenger.py` - deterministic momentum/event forecasts.
- `backend/benchmark_alpha/tax.py` - FIFO lots, SPY batching, and wash-sale guards.
- `backend/benchmark_alpha/allocator.py` - constrained benchmark-replacement targets.
- `backend/benchmark_alpha/execution.py` - target deltas, gates, and order intents.
- `backend/benchmark_alpha/runtime.py` - shadow/paper/live orchestration.
- `backend/benchmark_alpha/research.py` - experiment registry and purged walk-forward splits.
- `backend/benchmark_alpha/promotion.py` - immutable tier-promotion reports and enforcement.
- `backend/scripts/purge_backtest_secrets.py` - dry-run-first historical sanitization.
- `backend/scripts/migrate_alpha_tables.py` - table/index creation and legacy-context retention.
- `backend/scripts/run_alpha_research.py` - deterministic registered experiment runner.
- `backend/scripts/verify_alpha_readiness.py` - operational and promotion-gate report.

**Existing integration points:**

- `backend/broker_adapters/base.py` - expose current market marks without removing `_last_prices` compatibility.
- `backend/broker_adapters/alpaca.py` - continuously refresh market marks and attach alpha IDs to fills.
- `backend/broker_adapters/_wal.py` - retain run, allocation, and intent IDs with order state.
- `backend/broker.py` - mark consumption, legacy buy freshness gate, alpha runtime seam, live state, and fill audit.
- `backend/instance.py` - start and stop the independent mark watchdog with the broker.
- `backend/live_state.py` - mark/risk/alpha health fields.
- `backend/nexus_runtime_state.py` - stop using the empty ad-hoc audit path once alpha ledger replication is active.
- `backend/nexus_broker_utils.py` - compatibility buy gate while legacy mode remains available.
- `backend/interactive_utils.py` - indexed legacy Nexus reads and new alpha read actions.
- `backend/api/main.py` - authenticated alpha audit/performance/readiness endpoints.
- `backend/backtest_summary.py` - benchmark-relative result fields.
- `backend/engines/ai_backtest_engine.py` - remove LLM KEEP/TOSS from alpha promotion authority.
- `backend/strategies/graph_nexus_analysis.py` - only typed evidence metadata needed by the adapter; no allocator code.

**New tests:**

- `backend/tests/test_persistence_safety.py`
- `backend/tests/test_purge_backtest_secrets.py`
- `backend/tests/test_market_marks.py`
- `backend/tests/test_alpaca_market_marks.py`
- `backend/tests/test_alpha_watchdog.py`
- `backend/tests/test_alpha_local_store.py`
- `backend/tests/test_alpha_risk.py`
- `backend/tests/test_alpha_rethink_store.py`
- `backend/tests/test_alpha_api.py`
- `backend/tests/test_alpha_benchmark.py`
- `backend/tests/test_alpha_metrics.py`
- `backend/tests/test_alpha_calibration.py`
- `backend/tests/test_alpha_forecasts.py`
- `backend/tests/test_alpha_challenger.py`
- `backend/tests/test_alpha_tax.py`
- `backend/tests/test_alpha_allocator.py`
- `backend/tests/test_alpha_execution.py`
- `backend/tests/test_alpha_runtime.py`
- `backend/tests/test_alpha_research.py`
- `backend/tests/test_alpha_promotion.py`
- `backend/tests/test_alpha_replay_july10.py`

---

## Phase 0 - Containment and Credential Safety

### Task 0: Freeze Live Exposure and Record the Incident Baseline

**Files:** No source changes. Store only redacted operator evidence outside git.

**Produces:** A recorded halt timestamp, canceled-entry-order count, broker positions/equity snapshot, provider rotation checklist, and explicit operator decision for each currently held position.

- [ ] Confirm `alpaca-main` has `runCommand=False` and no process is generating new equity orders.
- [ ] Cancel open entry orders. Do not liquidate held positions as part of this administrative step.
- [ ] Capture redacted values for equity, cash, positions, open orders, `LiveOrderWAL` counts, current config hash, and latest live log timestamp.
- [ ] Rotate every credential found in historical BacktestResults: Alpaca, OpenRouter, Azure, Bedrock/AWS, Benzinga, and Neo4j. Update runtime secret sources without printing values.
- [ ] Verify each retired credential fails a minimal authentication probe and each replacement succeeds. Record provider name and boolean result only.
- [ ] Preserve the July 10 log excerpt and sanitized database counts needed by `test_alpha_replay_july10.py`; never preserve authenticated response bodies containing secrets.
- [ ] Do not resume live trading after this task. The next live eligibility decision is Task 18.

### Task 1: Secret-Safe Persistence Boundary

**Files:**

- Create: `backend/persistence_safety.py`
- Modify: `backend/broker.py:2696-2727`, `backend/broker.py:6514-6536`, `backend/broker.py:7216-7239`
- Test: `backend/tests/test_persistence_safety.py`

**Interfaces:**

- Produces `sanitize_snapshot(value: object) -> object`.
- Produces `assert_secret_free(value: object) -> None`.
- Produces `SecretMaterialError(ValueError)`.
- Secret-key matching is case-insensitive and covers `key`, `secret`, `token`, `password`, credentials, connection-string userinfo, and provider-specific aliases. Safe names such as `strategy_config_hash` and `secret_ref` are explicitly allowlisted.

- [ ] **Step 1: Write failing recursive-sanitizer tests.**

```python
import json
import pytest
from persistence_safety import SecretMaterialError, assert_secret_free, sanitize_snapshot


def test_sanitize_snapshot_removes_nested_secret_values():
    raw = {
        "name": "Nexus Only",
        "strategies": [{"config": {
            "alpaca_key": "CANARY_ALPACA_VALUE",
            "openrouter_api_key": "CANARY_OPENROUTER_VALUE",
            "strategy_config_hash": "abc123",
            "secret_ref": "env:OPENROUTER_API_KEY",
        }}],
    }
    clean = sanitize_snapshot(raw)
    encoded = json.dumps(clean, sort_keys=True)
    assert "CANARY_ALPACA_VALUE" not in encoded
    assert "CANARY_OPENROUTER_VALUE" not in encoded
    assert clean["strategies"][0]["config"]["strategy_config_hash"] == "abc123"
    assert clean["strategies"][0]["config"]["secret_ref"] == "env:OPENROUTER_API_KEY"


def test_assert_secret_free_rejects_secret_hidden_in_safe_field():
    with pytest.raises(SecretMaterialError):
        assert_secret_free({"notes": "Authorization: Bearer CANARY_TOKEN_MATERIAL_123456789"})
```

- [ ] **Step 2:** Run `cd backend && pytest tests/test_persistence_safety.py -v`; expect both tests to fail because the module does not exist.
- [ ] **Step 3:** Implement recursive dict/list/tuple traversal. Replace secret-key values with `{"redacted": True, "source": "runtime_secret"}`; apply logger-compatible value-pattern scanning to all strings; deep-copy non-secret values.
- [ ] **Step 4:** In `load_strategies_from_db`, build `_backtest_strategy_schema` from `sanitize_snapshot`. Immediately before both BacktestResults writes, call `assert_secret_free` on the complete payload and fail the backtest write closed on `SecretMaterialError`.
- [ ] **Step 5:** Run `pytest tests/test_persistence_safety.py tests/test_backtest_pnl_consistency.py -v`; expect PASS.
- [ ] **Step 6:** Run impact analysis on `load_strategies_from_db`; run staged change detection; commit only the named files with `security: prevent secrets in backtest persistence`.

### Task 2: Purge Historical Backtest Secrets Without Re-Exposing Them

**Files:**

- Create: `backend/scripts/purge_backtest_secrets.py`
- Test: `backend/tests/test_purge_backtest_secrets.py`

**Interfaces:**

- `sanitize_backtest_row(row: dict) -> tuple[dict, int]` returns a replacement patch and number of redacted fields.
- CLI defaults to dry-run. Mutation requires both `--apply` and `--confirm-table BacktestResults`.
- Output contains row IDs, counts, and status only; it never renders old or new secret-bearing payloads.

- [ ] **Step 1: Write the failing pure-row test.**

```python
from scripts.purge_backtest_secrets import sanitize_backtest_row


def test_sanitize_backtest_row_returns_secret_free_patch():
    patch, count = sanitize_backtest_row({
        "id": "r1",
        "strategy_schema": {"strategies": [{"config": {
            "benzinga_api_key": "CANARY_BENZINGA_VALUE",
            "max_positions": 8,
        }}]},
    })
    assert count == 1
    assert patch["strategy_schema"]["strategies"][0]["config"]["max_positions"] == 8
    assert "CANARY_BENZINGA_VALUE" not in repr(patch)
```

- [ ] **Step 2:** Run the test; expect import failure.
- [ ] **Step 3:** Implement streaming iteration by primary key, batches of 100, sanitizer reuse from Task 1, `conflict="replace"` only for the `strategy_schema` field, and a final `assert_secret_free` verification pass.
- [ ] **Step 4:** Add CLI tests proving dry-run performs zero updates and `--apply` without the confirmation string exits code 2.
- [ ] **Step 5:** Run `pytest tests/test_purge_backtest_secrets.py tests/test_persistence_safety.py -v`; expect PASS.
- [ ] **Step 6:** Run the script against a local fake/in-memory store in dry-run mode. Production execution requires a fresh encrypted database backup governed by the same secret-access controls; do not put that backup in git or `/tmp`.
- [ ] **Step 7:** Run change detection; commit with `security: add dry-run-first backtest secret purge`.

---

## Phase 1 - Fresh Market Truth and Durable Safety State

### Task 3: Timestamped Market-Mark Contract

**Files:**

- Create: `backend/market_marks.py`
- Test: `backend/tests/test_market_marks.py`

**Interfaces:**

- `MarkSource`: `STREAM_QUOTE`, `STREAM_TRADE`, `REST_QUOTE`, `BROKER_POSITION`, `FILL`.
- `MarkQuality`: `CONSOLIDATED`, `SINGLE_EXCHANGE`, `BROKER_DERIVED`, `EXECUTION_ONLY`.
- Immutable `MarketMark(symbol, price, observed_at, received_at, source, feed, quality)`.
- `MarketMark.age_seconds(now) -> float`.
- Thread-safe `MarketMarkBook.update(mark) -> bool`, `get(symbol)`, `snapshot()`, and `fresh_price(symbol, now, max_age_seconds, allowed_qualities=None)`.
- A mark with an older `observed_at` cannot replace a newer mark. A broker-position mark can replace a fill mark at the same timestamp; fill marks never outrank quotes or trades.

- [ ] **Step 1: Write failing precedence and freshness tests.**

```python
from datetime import datetime, timedelta, timezone
from market_marks import MarkQuality, MarkSource, MarketMark, MarketMarkBook


def test_market_mark_book_rejects_older_and_expires_at_sla():
    now = datetime(2026, 7, 10, 14, 0, tzinfo=timezone.utc)
    book = MarketMarkBook()
    current = MarketMark("MRNA", 76.51, now, now, MarkSource.BROKER_POSITION,
                         "iex", MarkQuality.BROKER_DERIVED)
    assert book.update(current) is True
    older = MarketMark("MRNA", 81.36, now - timedelta(minutes=5), now,
                       MarkSource.FILL, "execution", MarkQuality.EXECUTION_ONLY)
    assert book.update(older) is False
    assert book.fresh_price("MRNA", now + timedelta(seconds=59), 60) == 76.51
    assert book.fresh_price("MRNA", now + timedelta(seconds=61), 60) is None
```

- [ ] **Step 2:** Run `pytest tests/test_market_marks.py -v`; expect import failure.
- [ ] **Step 3:** Implement enum ordering explicitly in a `_SOURCE_PRIORITY` mapping, validate positive finite prices and timezone-aware timestamps, and guard the internal dict with `threading.RLock`.
- [ ] **Step 4:** Add tests for case-normalized symbols, defensive snapshot copies, NaN/zero rejection, and quote-over-fill precedence.
- [ ] **Step 5:** Run the test file; expect PASS.
- [ ] **Step 6:** Run change detection; commit with `feat: add timestamped market mark book`.

### Task 4: Make Alpaca Refreshes Update Current Marks

**Files:**

- Create: `backend/alpaca_mark_stream.py`
- Modify: `backend/broker_adapters/base.py`
- Modify: `backend/broker_adapters/alpaca.py:238-245`, `:1052-1198`, `:1391-1431`, `:1754-1768`
- Modify: `backend/broker.py:2400-2462`
- Test: `backend/tests/test_alpaca_market_marks.py`
- Test: `backend/tests/test_clean_room_adapter_init.py`

**Interfaces:**

- `BrokerAdapter.get_market_marks() -> dict[str, MarketMark]` returns a copy.
- `AlpacaMarkStream(book, api_key, api_secret, feed)` wraps `StockDataStream`, subscribes
  to current held/candidate symbols, reconnects with bounded backoff, and maps both quote
  and trade callbacks into `MarketMarkBook` without ever submitting orders.
- Alpaca owns `_market_marks: MarketMarkBook`; `_last_prices` remains a temporary compatibility mirror of the newest mark price.
- Successful `refresh_positions` writes every positive `market_value / qty` broker mark unconditionally when it is newer; it never preserves a fill price merely because a cache key exists.
- `_ensure_prices_include_positions` accepts a fresh mark before any scalar cache value and never treats an untimestamped `_last_prices` entry as fresh live data.

- [ ] **Step 1:** Run GitNexus impact on `refresh_positions`, `save_portfolio_snapshot`, `_on_trade_update`, and `_ensure_prices_include_positions`. Record direct callers and warn before continuing if any risk is HIGH/CRITICAL.
- [ ] **Step 2: Write a regression test reproducing the July 10 failure.**

```python
def test_second_position_refresh_replaces_fill_time_price(alpaca_adapter, position_factory):
    alpaca_adapter._last_prices["MRNA"] = 81.36
    alpaca_adapter._client.get_all_positions.return_value = [
        position_factory("MRNA", qty="4", market_value="306.04", avg_entry="81.36")
    ]
    alpaca_adapter.refresh_positions()
    assert alpaca_adapter._last_prices["MRNA"] == 76.51
    mark = alpaca_adapter.get_market_marks()["MRNA"]
    assert mark.price == 76.51
    assert mark.source.value == "broker_position"
```

- [ ] **Step 3:** Run the regression plus clean-room tests; expect the regression to fail at 81.36.
- [ ] **Step 4:** Initialize `MarketMarkBook`, update it from position REST responses and trade events, and mirror accepted newest prices into `_last_prices`. Do not overwrite `avg_entry_price` or reconstructed entry-trade state.
- [ ] **Step 5:** Implement `AlpacaMarkStream` with injected stream factory for tests. Add callback tests proving a newer IEX quote replaces a broker mark, an older callback is ignored, subscription changes are idempotent, disconnect sets degraded health, and reconnect never blocks the broker strategy thread.
- [ ] **Step 6:** Change `_ensure_prices_include_positions` resolution to: explicit cycle prices, backtest bar, fresh MarketMark, outbound quote, delayed fallback. The legacy scalar cache is allowed only in backtests or with an explicit timestamp supplied by the mark book.
- [ ] **Step 7:** Run `pytest tests/test_alpaca_market_marks.py tests/test_clean_room_adapter_init.py tests/test_broker_adapter_base.py -v`; expect PASS.
- [ ] **Step 8:** Run the July 10 regression twice to prove a second refresh changes the mark. Run change detection and commit with `fix: stream and refresh current marks instead of preserving fills`.

### Task 5: Durable Local Event Spool and State Store

**Files:**

- Create: `backend/benchmark_alpha/__init__.py`
- Create: `backend/benchmark_alpha/types.py`
- Create: `backend/benchmark_alpha/local_store.py`
- Test: `backend/tests/test_alpha_local_store.py`

**Interfaces:**

- `RunOrigin`: `BACKTEST`, `SHADOW`, `PAPER`, `LIVE`.
- `ExecutionMode`: `OFF`, `SHADOW`, `PAPER`, `LIVE`.
- `EventKind`: `PREDICTION`, `GATE`, `ALLOCATION`, `ORDER_INTENT`, `FILL`, `OUTCOME`, `RISK`, `TAX`.
- `LocalAlphaStore(path)` creates SQLite tables `events` and `state` in WAL journal mode with `synchronous=FULL`.
- `append_event(event_id, kind, payload, created_at) -> bool` is idempotent by `event_id`.
- `pending_events(limit)`, `mark_synced(event_ids, synced_at)`, `put_state(key, payload)`, and `get_state(key)`.
- Default path is `$LIVE_TRADING_STATE_DIR/alpha-ledger.sqlite3`; startup fails for PAPER/LIVE if the directory is not writable or persistent.

- [ ] **Step 1: Write failing durability and idempotency tests.**

```python
from datetime import datetime, timezone
from benchmark_alpha.local_store import LocalAlphaStore
from benchmark_alpha.types import EventKind


def test_local_store_survives_reopen_and_deduplicates(tmp_path):
    path = tmp_path / "alpha.sqlite3"
    ts = datetime(2026, 7, 11, tzinfo=timezone.utc)
    first = LocalAlphaStore(path)
    assert first.append_event("e1", EventKind.PREDICTION, {"symbol": "AAPL"}, ts) is True
    assert first.append_event("e1", EventKind.PREDICTION, {"symbol": "AAPL"}, ts) is False
    first.close()
    reopened = LocalAlphaStore(path)
    assert reopened.pending_events(10)[0]["payload"]["symbol"] == "AAPL"
```

- [ ] **Step 2:** Run `pytest tests/test_alpha_local_store.py -v`; expect import failure.
- [ ] **Step 3:** Implement explicit transactions, canonical JSON serialization (`sort_keys=True`, compact separators), UTC ISO timestamps, and schema version 1 in `PRAGMA user_version`.
- [ ] **Step 4:** Add tests for atomic state replacement, corrupt JSON detection, concurrent append serialization, and `mark_synced` not deleting forensic rows.
- [ ] **Step 5:** Run tests; expect PASS. Run change detection; commit with `feat(alpha): add durable local event and state store`.

### Task 6: Freshness Watchdog and Persistent Drawdown State

**Files:**

- Create: `backend/benchmark_alpha/risk.py`
- Create: `backend/benchmark_alpha/watchdog.py`
- Modify: `backend/instance.py:311-414`
- Modify: `backend/broker.py:4063-4385`, `backend/broker.py:9254-9296`
- Modify: `backend/live_state.py`
- Modify: `backend/nexus_broker_utils.py`
- Test: `backend/tests/test_alpha_risk.py`
- Test: `backend/tests/test_alpha_replay_july10.py`
- Test: `backend/tests/test_alpha_watchdog.py`

**Interfaces:**

- `RiskLevel`: `NORMAL`, `SOFT`, `HARD`, `KILL`.
- `RiskState(peak_equity, last_equity, drawdown_pct, level, updated_at)`.
- `update_risk_state(previous, equity, observed_at) -> RiskState` never lowers the peak except through an explicit operator reset record.
- `evaluate_mark_health(held_symbols, marks, now, entry_max_age=60, fallback_max_age=120) -> MarkHealth`.
- `legacy_buy_block(mark_health, symbol) -> str | None` blocks current legacy buys while the new runtime is still off; sells are never blocked by this function.
- State key is `risk:<instance_id>` in `LocalAlphaStore` and is reconciled against broker equity at every snapshot.
- `AlphaWatchdog(probe, local_store, thresholds).poll_once(now) -> WatchdogResult` compares direct broker positions/equity with the broker process's locally persisted marks/equity. It runs in a separate process and has no order-submit method; after three consecutive critical mismatches it cancels open entry orders through a cancel-only client and halts the instance.

- [ ] **Step 1: Write failing drawdown transition tests.**

```python
from datetime import datetime, timezone
from benchmark_alpha.risk import RiskLevel, RiskState, update_risk_state


def test_drawdown_state_preserves_peak_and_crosses_all_thresholds():
    ts = datetime(2026, 7, 11, tzinfo=timezone.utc)
    s = RiskState.initial(10000.0, ts)
    assert update_risk_state(s, 9200.0, ts).level is RiskLevel.SOFT
    assert update_risk_state(s, 8800.0, ts).level is RiskLevel.HARD
    killed = update_risk_state(s, 8500.0, ts)
    assert killed.level is RiskLevel.KILL
    assert killed.peak_equity == 10000.0
```

- [ ] **Step 2:** Add a July 10 fixture with MRNA fill 81.36, broker marks 76.51 then 68.26, and account equity 6,113.98 then 5,949.05. Assert the evaluated mark ends at 68.26 and drawdown is nonzero.
- [ ] **Step 3:** Run both files; expect failures.
- [ ] **Step 4:** Implement the pure risk state machine and mark-health result. Use exact boundaries: `>=8` SOFT, `>=12` HARD, `>=15` KILL.
- [ ] **Step 5:** Add `mark_health`, mark source/age per position, current peak, drawdown, and risk level to `_compute_live_state_snapshot`. Correct held-count telemetry from the snapshot positions rather than per-cycle action counters.
- [ ] **Step 6:** Call `legacy_buy_block` immediately before any live legacy BUY submission. A blocked buy writes a local GATE event and logs one bounded alert; a SELL continues.
- [ ] **Step 7:** On startup, load the durable risk state and reconcile it with current broker equity without resetting the peak. An explicit reset requires a separate operator event containing old peak, new peak, reason, and actor.
- [ ] **Step 8:** Implement the independent watchdog with injected read-only/cancel-only probe. Add tests for one transient mismatch (alert only), three consecutive mismatches (cancel entries and halt), healthy reset of the counter, and inability to submit an order.
- [ ] **Step 9:** In `instance.py`, start and stop the watchdog subprocess alongside live equity brokers when `ALPHA_MARK_WATCHDOG_ENABLED=1`; keep the default disabled until the deployment manifest mounts the shared durable state directory. Test the pure command builder and lifecycle cleanup.
- [ ] **Step 10:** Run `pytest tests/test_alpha_risk.py tests/test_alpha_watchdog.py tests/test_alpha_replay_july10.py tests/test_live_boot_setup.py tests/test_nexus_monitor_cycle.py -v`; expect PASS.
- [ ] **Step 11:** Run impact analysis on `_compute_live_state_snapshot`, `start_broker`, and the enclosing broker execution function, then change detection; commit with `fix(alpha): enforce fresh marks and independent drawdown watchdog`.

**Phase 1 verification gate:** with Alpaca calls stubbed, replay July 10 and prove all eight positions receive changing marks, new buys fail closed after 60 seconds, sells remain available, and restart does not reset the high-water mark. Do not proceed to portfolio construction if this gate fails.

---

## Phase 2 - Immutable Audit, Indexed Reads, and Truthful Benchmarking

### Task 7: Typed Alpha Records and RethinkDB Replication

**Files:**

- Extend: `backend/benchmark_alpha/types.py`
- Create: `backend/benchmark_alpha/rethink_store.py`
- Create: `backend/scripts/migrate_alpha_tables.py`
- Test: `backend/tests/test_alpha_rethink_store.py`

**Interfaces:**

- Strict enums: `EvidenceClass(DIRECT, PROPAGATION, DETERMINISTIC)`, `GateEffect(ELIGIBLE, REJECTED, HELD)`, and `OrderEffect(SUBMITTED, ACCEPTED, PARTIAL, FILLED, CANCELED, REJECTED, EXPIRED)`.
- Immutable dataclasses `Forecast`, `GateRecord`, `TargetPosition`, `AllocationPlan`, `OrderIntent`, `FillRecord`, and `HorizonOutcome`; each implements `to_doc()` and rejects unknown enum values.
- Tables: `AlphaPredictions`, `AlphaGates`, `AlphaAllocations`, `AlphaOrderIntents`, `AlphaFills`, `AlphaOutcomes`, `AlphaExperiments`, and `AlphaPromotions`.
- Indexes on each event table: `run_asof=[run_id, as_of]` and `instance_origin_asof=[instance_id, origin, as_of]`.
- `AlphaRethinkStore.replicate_pending(local_store, limit=500) -> ReplicationResult` writes with `conflict="error"`; an already-existing identical ID is marked synced, while divergent content raises an integrity error.

- [ ] **Step 1:** Write tests constructing each dataclass, round-tripping `to_doc`, and rejecting `evidence_class="unknown"`.
- [ ] **Step 2:** Write a fake Rethink writer test proving replication marks only successfully inserted or byte-identical events synced.
- [ ] **Step 3:** Run tests; expect missing types/store failures.
- [ ] **Step 4:** Implement dataclasses with timezone-aware timestamp validation, finite-number validation, canonical uppercase symbols, and deterministic IDs derived from run/origin/type/symbol/as-of/horizon.
- [ ] **Step 5:** Implement table/index creation in the migration script. The script prints table/index names and status only and supports `--dry-run`.
- [ ] **Step 6:** Implement replication with bounded retries outside the risk thread. RethinkDB failure leaves SQLite events pending and cannot raise into risk evaluation.
- [ ] **Step 7:** Run `pytest tests/test_alpha_local_store.py tests/test_alpha_rethink_store.py -v`; expect PASS. Run change detection; commit with `feat(alpha): add typed immutable records and Rethink replication`.

### Task 8: Replace Full-Table Nexus Reads With Indexed, Scoped APIs

**Files:**

- Modify: `backend/interactive_utils.py:6913-6959`
- Modify: `backend/api/main.py:5068-5094`
- Extend: `backend/benchmark_alpha/rethink_store.py`
- Test: `backend/tests/test_alpha_api.py`
- Test: `backend/tests/test_nexus_telemetry.py`

**Interfaces:**

- Legacy context/outcome actions query `base_instance_id` with an index and apply bounded date/order limits; no string-split table filter.
- Legacy invalid intents produce a schema-error context and no outcome row. Missing emitted
  intent names are either added to the explicit allowlist when they represent a real
  directional prediction, or mapped to a typed rejection/hold when they represent queue
  state; none are coerced to `unknown`.
- New authenticated reads:
  - `GET /instances/{instance_id}/alpha/predictions?origin=&run_id=&limit=`
  - `GET /instances/{instance_id}/alpha/allocations?origin=&run_id=&limit=`
  - `GET /instances/{instance_id}/alpha/performance?origin=&run_id=`
  - `GET /instances/{instance_id}/alpha/readiness`
- Limits are clamped to 1-500 and queries require exact instance plus origin or run scope.

- [ ] **Step 1:** Run impact analysis on `action_nexus_trade_contexts` and `action_nexus_outcome_stats`; record LOW/HIGH assessment.
- [ ] **Step 2:** Add tests whose fake table raises if a lambda full-table filter or an unindexed `.run()` is used; assert `.get_all(instance_id, index="base_instance_id")` is selected.
- [ ] **Step 3:** Add API authentication/shape tests for the four alpha endpoints, including 401 without auth and limit clamping.
- [ ] **Step 4:** Run tests; expect failures against the full-table implementation.
- [ ] **Step 5:** Run impact analysis on `_normalize_action_intent` and `_save_trade_contexts_and_outcomes`. Change invalid-intent handling so queue/deferred states cannot create directional outcomes, and add tests for every intent currently emitted by Graph Nexus.
- [ ] **Step 6:** Implement indexed legacy queries and alpha store read methods. Mark the old outcome scorecard `data_status="legacy_untrusted"` while any invalid historical rows remain. Return explicit `replication_lag_seconds`; never convert storage failure into an empty successful scorecard.
- [ ] **Step 7:** Add retention to `migrate_alpha_tables.py`: raw candidate predictions 400 days, high-frequency mark-health events 30 days, allocations/orders/fills/promotions retained indefinitely unless policy changes.
- [ ] **Step 8:** Run API and telemetry tests; expect PASS. Run change detection; commit with `perf(alpha): scope telemetry reads and reject invalid outcomes`.

### Task 9: Inception Equity, SPY Total Return, and Active Metrics

**Files:**

- Create: `backend/benchmark_alpha/benchmark.py`
- Create: `backend/benchmark_alpha/metrics.py`
- Modify: `backend/backtest_summary.py:40-75`
- Modify: `backend/broker.py:4146-4154`, `:4298-4300`
- Test: `backend/tests/test_alpha_benchmark.py`
- Test: `backend/tests/test_alpha_metrics.py`

**Interfaces:**

- `align_return_series(portfolio_values, spy_adjusted_values) -> pandas.DataFrame` performs timestamp-normalized inner alignment and rejects fewer than two observations.
- `compute_active_metrics(aligned, annualization=252, bootstrap_seed=179) -> ActiveMetrics` returns portfolio/benchmark/net active return, beta, tracking error, information ratio, Sharpe, Sortino, max drawdown, Calmar, Deflated Sharpe probability, and 90% bootstrap active-return interval.
- `deflated_sharpe_probability(sharpe, sample_count, skew, kurtosis, trials) -> float` uses `statistics.NormalDist`; `trials` is the complete registered experiment count, including failures.
- `InceptionState(inception_equity, inception_at, high_water_equity, source)` is durable and never replaced by ordinary restart equity.
- Benchmark fetches request adjusted SPY bars and record feed, adjustment, request time, and data snapshot ID.

- [ ] **Step 1: Write failing exact-series tests.**

```python
import pandas as pd
from benchmark_alpha.metrics import compute_active_metrics


def test_active_metrics_use_matching_timestamps_and_known_active_return():
    idx = pd.to_datetime(["2026-07-08", "2026-07-09", "2026-07-10"], utc=True)
    aligned = pd.DataFrame({"portfolio": [100.0, 102.0, 101.0],
                            "benchmark": [100.0, 101.0, 101.0]}, index=idx)
    m = compute_active_metrics(aligned, bootstrap_seed=179)
    assert round(m.portfolio_return, 6) == 0.01
    assert round(m.benchmark_return, 6) == 0.01
    assert round(m.active_return, 6) == 0.0
```

- [ ] **Step 2:** Add tests proving a restart equity of 5,949.05 does not overwrite an inception equity of 6,000 or a high water of 6,243.15.
- [ ] **Step 3:** Run tests; expect import failures.
- [ ] **Step 4:** Implement metrics with NumPy/pandas and `statistics.NormalDist`; use daily percentage returns for beta/information ratio and value series for cumulative return/drawdown. Bootstrap contiguous daily blocks of length five with a fixed seed. Add Bailey/Lopez de Prado Deflated Sharpe using the registered trial count, sample skew, and excess kurtosis.
- [ ] **Step 5:** Extend `compute_backtest_summary` by merging, not replacing, its existing truthful P&L fields. Add `benchmark_return`, `active_return`, `beta`, `tracking_error`, `information_ratio`, `max_drawdown`, and bootstrap bounds.
- [ ] **Step 6:** Persist inception/high-water state via `LocalAlphaStore`; expose it in LiveState. Account cash flows are recorded separately so deposits/withdrawals do not become trading return.
- [ ] **Step 7:** Run `pytest tests/test_alpha_benchmark.py tests/test_alpha_metrics.py tests/test_backtest_pnl_consistency.py -v`; expect PASS. Run impact analysis on `compute_backtest_summary`, then change detection; commit with `feat(alpha): add truthful SPY-relative performance accounting`.

---

## Phase 3 - Forecasts, Tax Controls, Allocation, and Execution

### Task 10: Graph Nexus Forecast Adapter and Calibration

**Files:**

- Create: `backend/benchmark_alpha/calibration.py`
- Create: `backend/benchmark_alpha/forecast_adapters.py`
- Modify only if required: `backend/strategies/graph_nexus_analysis.py` evidence metadata export
- Test: `backend/tests/test_alpha_calibration.py`
- Test: `backend/tests/test_alpha_forecasts.py`

**Interfaces:**

- `ForecastCalibrator.fit(rows, evidence_class, horizon_days) -> CalibratedModel` uses isotonic regression only with at least 100 resolved observations and both positive/negative classes.
- `CalibratedModel.predict(raw_score) -> (expected_excess_return, probability_outperform)`.
- `graph_forecasts(scores, metadata, calibrators, run_context) -> list[Forecast]` emits horizons 1, 3, 5.
- Missing calibration produces `eligible=False` with gate reason `uncalibrated`; it never maps a raw score directly to tradable expected return.
- `EvidenceClass.PROPAGATION` is ineligible for PAPER/LIVE until the promotion record explicitly enables it.

- [ ] **Step 1:** Add calibration tests with 120 deterministic samples and assertions that predicted probability is monotonic and bounded 0-1.
- [ ] **Step 2:** Add adapter tests for direct, propagation, no-graph-signal, failed-quality, negative-trend, missing-price, disabled-ML, and unknown-reason inputs.
- [ ] **Step 3:** Run tests; expect module failures.
- [ ] **Step 4:** Implement calibrator serialization with training-window endpoints, sample count, feature version, and model hash. Never pickle untrusted payloads; store JSON thresholds and isotonic breakpoints.
- [ ] **Step 5:** Implement the adapter against existing `scores`, `_nexus_action_intents`, position-size metadata, and reason text. If one required field is absent, emit an audited ineligible forecast rather than guessing.
- [ ] **Step 6:** If Graph Nexus needs one metadata key exposed, run impact analysis on the exact enclosing symbol and add only that key; do not move allocator or execution logic into the strategy file.
- [ ] **Step 7:** Run tests; expect PASS. Run change detection; commit with `feat(alpha): adapt Nexus evidence into calibrated forecasts`.

### Task 11: Deterministic Momentum/Event Challenger

**Files:**

- Create: `backend/benchmark_alpha/challenger.py`
- Test: `backend/tests/test_alpha_challenger.py`

**Interfaces:**

- `ChallengerFeatures(excess_5d, excess_20d, realized_vol_20d, trend_quality, event_score)`.
- `build_features(stock_bars, spy_bars, event_score, as_of) -> ChallengerFeatures` consumes bars at or before `as_of` only.
- `challenger_forecasts(universe, bars, spy_bars, event_scores, calibrators, run_context) -> list[Forecast]` emits the same 1/3/5-day contract.
- Raw score is `0.50*z(excess_5d) + 0.30*z(excess_20d) + 0.20*z(event_score) - 0.20*z(realized_vol_20d)` computed cross-sectionally with deterministic tie-breaking by symbol.

- [ ] **Step 1:** Create synthetic bars where A trends above SPY, B matches SPY, and C falls. Assert A > B > C and prove bars after `as_of` do not change features.
- [ ] **Step 2:** Add a shuffled-input test proving output order and IDs are deterministic.
- [ ] **Step 3:** Run tests; expect import failure.
- [ ] **Step 4:** Implement finite-value handling, cross-sectional z-scores with zero-variance output 0, point-in-time slicing, and calibration reuse from Task 10.
- [ ] **Step 5:** Run tests; expect PASS. Run change detection; commit with `feat(alpha): add deterministic benchmark-relative challenger`.

### Task 12: FIFO SPY Lots and Wash-Sale-Aware Rebalancing

**Files:**

- Create: `backend/benchmark_alpha/tax.py`
- Test: `backend/tests/test_alpha_tax.py`

**Interfaces:**

- `TaxLot(lot_id, symbol, acquired_at, quantity, unit_basis, remaining_quantity)`.
- `consume_fifo(lots, quantity, sale_price, sold_at) -> LotConsumption`.
- `WashSaleGuard.evaluate_spy_sale(lots, quantity, sale_price, sold_at, acquisitions, emergency=False) -> TaxDecision`.
- `WashSaleGuard.evaluate_spy_buy(quantity, buy_price, bought_at, prior_loss_sales, external_blackouts) -> TaxDecision`.
- `should_rebalance_spy(last_rebalance_at, current_weight, target_weight, now) -> bool` is true after seven calendar days or absolute drift of at least 0.05.
- Emergency intents carry `risk_override=True` and bypass tax blocks while preserving the warning/audit record.

- [ ] **Step 1: Write failing boundary tests for days 30 and 31.**

```python
from datetime import datetime, timedelta, timezone
from benchmark_alpha.tax import WashSaleGuard


def test_spy_repurchase_block_ends_after_day_30():
    sold = datetime(2026, 7, 1, tzinfo=timezone.utc)
    guard = WashSaleGuard(loss_sales=[sold])
    assert guard.evaluate_spy_buy(sold + timedelta(days=30)).allowed is False
    assert guard.evaluate_spy_buy(sold + timedelta(days=31)).allowed is True
```

- [ ] **Step 2:** Add FIFO partial-lot tests, prior-30-day-buy loss-sale blocking, gain-sale allowance, 5pp drift, weekly batching, external blackout dates, and emergency override.
- [ ] **Step 3:** Run tests; expect module failure.
- [ ] **Step 4:** Implement decimal-safe quantities/prices using `Decimal(str(value))`; return realized gain/loss and affected lot IDs without claiming broker tax-lot selection.
- [ ] **Step 5:** Persist lot events through `LocalAlphaStore`; reconcile quantities to broker positions daily and freeze SPY discretionary orders on mismatch.
- [ ] **Step 6:** Run tests; expect PASS. Run change detection; commit with `feat(alpha): add FIFO SPY wash-sale controls`.

### Task 13: Constrained Benchmark-Replacement Allocator

**Files:**

- Create: `backend/benchmark_alpha/allocator.py`
- Test: `backend/tests/test_alpha_allocator.py`

**Interfaces:**

- Immutable `AllocatorConfig(cash_weight=.02, active_floor=.40, active_ceiling=.80, max_positions=10, max_name_weight=.08, max_sector_weight=.20, beta_min=.8, beta_max=1.1, starter_weight=.04)`.
- `build_allocation(forecasts, current_weights, sectors, betas, volatilities, risk_limits, tax_state, as_of) -> AllocationPlan`.
- Select only eligible forecasts whose expected excess return exceeds modeled spread, slippage, tax cost, and an anti-churn hurdle.
- Weight priority is positive expected-excess-return divided by volatility, with deterministic symbol tie-breaking. Clip stock weights to 4-8%, sector totals to 20%, and active total to the risk-adjusted ceiling.
- Set `SPY = 1 - cash_weight - sum(active_weights)`; if too few candidates exist, keep the residual in SPY rather than forcing the 40% floor.
- A held stock whose selected forecast has reached `as_of + horizon_trading_days` receives target weight zero unless a newly created, independently eligible forecast renews it. No minimum-hold key can override expiry or a risk exit.

- [ ] **Step 1:** Write tests for no signals (98% SPY/2% cash), ten equal strong signals (80% active/18% SPY/2% cash), one sector overflow, one 20% proposed name clipped to 8%, risk SOFT ceiling 40%, expired three-day forecast exiting to SPY, and a newly timestamped eligible forecast renewing the position.
- [ ] **Step 2:** Add property tests over deterministic random inputs asserting weights sum to 1 within `1e-9`, no negative weights, no more than ten stocks, name/sector/gross caps, and repeat-call equality.
- [ ] **Step 3:** Run tests; expect module failure.
- [ ] **Step 4:** Implement selection and iterative cap redistribution. If beta remains outside 0.8-1.1 after clipping, move active weight back to SPY; never use leverage to repair beta.
- [ ] **Step 5:** Include `reason_codes`, rejected candidate IDs, forecast IDs, expected turnover, and constraint utilization in `AllocationPlan`.
- [ ] **Step 6:** Run tests; expect PASS. Run change detection; commit with `feat(alpha): add constrained SPY replacement allocator`.

### Task 14: Position Stops, Drawdown Limits, and Execution Intent Planning

**Files:**

- Extend: `backend/benchmark_alpha/risk.py`
- Create: `backend/benchmark_alpha/execution.py`
- Modify: `backend/broker_adapters/_wal.py`
- Test: `backend/tests/test_alpha_execution.py`

**Interfaces:**

- `stop_distance_pct(atr_pct) -> float` clamps `1.5 * atr_pct` to 0.05-0.08.
- `max_position_weight_for_loss_budget(stop_pct, loss_budget=.006) -> float` cannot exceed 0.08.
- `risk_limits(state) -> RiskLimits`: NORMAL active cap .80, SOFT .40, HARD 0 with staged reductions, KILL cash target 1.0.
- `build_order_intents(plan, portfolio, marks, tax_guard, risk_state, now) -> list[OrderIntent]` emits stock sells, SPY net sell/buy, then stock buys.
- Every intent has `run_id`, `allocation_id`, `intent_id`, `reason_codes`, `mark_id`, `risk_override`, and stable client-order material.
- Exposure-increasing intents require fresh marks and complete constraints. Risk-reducing intents may use a broker fallback mark and always record degraded quality.
- Buy availability uses cached/settled cash plus only sell quantity confirmed filled through WAL. A merely submitted or partially unfilled sell contributes no projected proceeds.

- [ ] **Step 1:** Add tests for ATR clamps, 0.6% loss budget, sells-before-buys, one net SPY order, stale-buy rejection, stale-risk-sell allowance, partial-fill-safe available cash, and KILL liquidation to cash.
- [ ] **Step 2:** Run tests; expect failures.
- [ ] **Step 3:** Implement pure intent planning. Calculate current weights from broker quantities and fresh marks; use a configurable minimum notional of $1 and suppress deltas below both $1 and 0.25 percentage points. Build a cash ledger from current cash plus confirmed fill deltas, never expected sell proceeds.
- [ ] **Step 4:** Extend `WALRecord` and `record_intent` with optional alpha IDs/reasons while retaining backward compatibility for existing callers.
- [ ] **Step 5:** Enforce one order authority per instance/mode. Duplicate intent ID or client-order ID returns the existing WAL record and never submits twice.
- [ ] **Step 6:** Run `pytest tests/test_alpha_execution.py tests/test_broker_adapter_base.py tests/test_clean_room_scenarios.py -v`; expect PASS.
- [ ] **Step 7:** Run impact analysis on `LiveOrderWAL.record_intent`; run change detection; commit with `feat(alpha): plan risk-gated idempotent target orders`.

### Task 15: Runtime Orchestration and Broker Integration

**Files:**

- Create: `backend/benchmark_alpha/runtime.py`
- Modify: `backend/broker.py:9150-9710`
- Modify: `backend/broker_adapters/alpaca.py:1391-1505`, `:1593-1716`
- Modify: `backend/live_state.py`
- Test: `backend/tests/test_alpha_runtime.py`

**Interfaces:**

- `AlphaRuntime.evaluate_cycle(run_context, graph_payload, bars, events, portfolio, marks) -> CycleResult`.
- `CycleResult` contains forecasts, gates, allocation, intents, submissions, and health.
- Modes:
  - OFF: exact legacy behavior.
  - SHADOW: write forecasts/allocation/intents; submit none; legacy behavior remains separately labeled.
  - PAPER: alpha is sole order authority on a paper brokerage; legacy Graph orders are suppressed.
  - LIVE: alpha is sole order authority and requires a valid promotion record for the exact config/model/data hashes.
- MONITOR cycles update marks, stops, and drawdown only. FULL cycles refresh forecasts and allocate. Risk exits can occur on either cadence.
- In PAPER/LIVE alpha modes, legacy `rotation_min_hold_days`, `sell_enforcement_min_hold_days`, and static partial-profit tiers have no order authority; horizon renewal, target weights, and alpha risk rules are authoritative.

- [ ] **Step 1:** Run impact analysis on `AlpacaAdapter.execute_signal`, the enclosing broker per-symbol execution function, and `_on_trade_update`. Warn before proceeding on HIGH/CRITICAL.
- [ ] **Step 2:** Add tests proving OFF is byte-for-byte compatible at the decision seam, SHADOW submits zero orders, PAPER suppresses legacy Graph orders, LIVE without promotion refuses startup, a risk exit works during MONITOR, and legacy minimum-hold/profit-tier settings cannot change an alpha-mode intent.
- [ ] **Step 3:** Add a RethinkDB-outage test: SQLite receives all cycle events, risk evaluation continues, and the replication error appears in health without enabling orders.
- [ ] **Step 4:** Run tests; expect runtime missing.
- [ ] **Step 5:** Implement `AlphaRuntime` using dependency injection for store, calibrators, allocator, execution, and broker adapter. Append each event locally before the next side effect.
- [ ] **Step 6:** Add one broker seam after run-once results are available and before legacy per-symbol submission. Do not duplicate strategy evaluation. OFF takes the old path; PAPER/LIVE take the alpha path; SHADOW evaluates alpha then takes the labeled legacy path.
- [ ] **Step 7:** Attach allocation/intent IDs to the WAL. On Alpaca fill/reject callbacks, resolve the WAL row and append a typed Fill/OrderEffect event locally before asynchronous replication.
- [ ] **Step 8:** Extend LiveState with alpha mode, run ID, last full cycle, last monitor cycle, replication lag, active/SPY/cash weights, constraint utilization, mark health, risk level, and promotion tier.
- [ ] **Step 9:** Run `pytest tests/test_alpha_runtime.py tests/test_alpha_execution.py tests/test_nexus_monitor_cycle.py tests/test_broker_pause_resume_cycle.py -v`; expect PASS.
- [ ] **Step 10:** Run change detection and inspect every affected execution flow. Commit with `feat(alpha): integrate shadow and gated portfolio runtime`.

**Phase 3 verification gate:** run OFF and SHADOW against the same deterministic fixture. OFF must reproduce legacy orders; SHADOW must produce no broker order while emitting a fully reconciled forecast-to-intent chain. PAPER may start only after both results pass.

---

## Phase 4 - Deterministic Research and Promotion

### Task 16: Registered Purged Walk-Forward Research Harness

**Files:**

- Create: `backend/benchmark_alpha/research.py`
- Create: `backend/scripts/run_alpha_research.py`
- Modify: `backend/engines/ai_backtest_engine.py:1106-1132`, `:1408-1418`
- Test: `backend/tests/test_alpha_research.py`

**Interfaces:**

- Immutable `ExperimentSpec(model_family, horizons, active_ceiling, train_months, calibration_months, test_months, embargo_days, data_snapshot_id, code_hash, model_hash, cost_model_version)` with deterministic `experiment_id`.
- `walk_forward_splits(dates, train_months=12, calibration_months=3, test_months=3, embargo_days=5)` yields non-overlapping ordered splits.
- `ExperimentRegistry.register`, `mark_running`, `mark_finished`, `mark_failed`; failed/stopped runs remain queryable.
- Runner refuses mutable/current news data, unfrozen LLM output, fewer than 24 months, missing required regimes, or a reused sealed holdout.
- Alpha promotion ignores `_validate_result_llm`; the LLM may summarize results but cannot KEEP/TOSS them.

- [ ] **Step 1:** Write split tests proving every test date follows training/calibration, the five-day embargo has no overlap, and the same inputs produce the same experiment ID.
- [ ] **Step 2:** Add registry tests proving failed experiments remain stored and duplicate specs return the original ID rather than creating another trial.
- [ ] **Step 3:** Add rejection tests for 23 months of data, an unfrozen model-output provider, and a second final-holdout evaluation.
- [ ] **Step 4:** Run tests; expect module failure.
- [ ] **Step 5:** Implement split generation with calendar-month boundaries, explicit UTC dates, and frozen manifest hashes. Record all parameter combinations before running any result.
- [ ] **Step 6:** Build the exact matrix: SPY control; deterministic challenger; Graph direct; Graph direct+propagation; horizons 1/3/5; active ceilings 40/60/80. Nested folds select horizon/ceiling; the final holdout compares registered model families once.
- [ ] **Step 7:** Route alpha-mode AI backtests through the registry and objective metrics. Preserve legacy AI behavior for non-alpha experiments, but label its result `promotion_eligible=False`.
- [ ] **Step 8:** Run `pytest tests/test_alpha_research.py tests/test_nexus_dual_cadence_backtest_harness.py -v`; expect PASS.
- [ ] **Step 9:** Run impact analysis on `_validate_result_llm` and the experiment launch function; run change detection; commit with `feat(alpha): add registered purged walk-forward research`.

### Task 17: Statistical and Operational Promotion Gate

**Files:**

- Create: `backend/benchmark_alpha/promotion.py`
- Create: `backend/scripts/verify_alpha_readiness.py`
- Extend: `backend/benchmark_alpha/metrics.py`
- Test: `backend/tests/test_alpha_promotion.py`

**Interfaces:**

- `PromotionTier`: `SHADOW`, `PAPER`, `LIVE_40`, `LIVE_60`, `LIVE_80`.
- `PromotionReport` contains every metric, sample count, incident count, market-data quality, replication health, reconciliation status, config/model/data hashes, evidence classes, and pass/fail reasons.
- `evaluate_promotion(report, requested_tier) -> PromotionDecision` enforces cumulative gates.
- Statistical gates: median annual active >=8pp, target active >=10pp, 90% bootstrap lower bound >0, information ratio >=0.75, Deflated Sharpe probability >=0.95, max drawdown <=15%, beta 0.8-1.1, and positive active return in >=60% unseen quarters.
- Sample/time gates: SHADOW >=4 weeks and 100 qualified forecasts; PAPER >=6 weeks and 50 completed positions; LIVE_60 >=8 incident-free live weeks; LIVE_80 >=6 live months and 150 completed positions.
- LIVE_80 additionally requires SIP-quality consolidated data.

- [ ] **Step 1:** Write one passing LIVE_40 report and table-driven single-field failures for every statistical and operational threshold, including Deflated Sharpe at 0.9499 versus 0.95.
- [ ] **Step 2:** Add tests proving propagation stays disabled when the direct+propagation ablation does not beat direct-only after costs, and that no elapsed-time gate can override a failed metric.
- [ ] **Step 3:** Add tests for config/model/data hash mismatch, SQLite replication backlog, stale marks, decision/order mismatch, secrets-canary failure, and IEX-only LIVE_80 rejection.
- [ ] **Step 4:** Run tests; expect module failure.
- [ ] **Step 5:** Implement pure promotion evaluation with complete reason lists. Promotion records are append-only, tier-specific, signed by authenticated operator identity, and expire on any config/model/data hash change.
- [ ] **Step 6:** Implement readiness CLI output as JSON plus a human table; it performs read-only checks and exits 0 only when the requested tier passes.
- [ ] **Step 7:** Run `pytest tests/test_alpha_promotion.py tests/test_alpha_metrics.py tests/test_alpha_rethink_store.py -v`; expect PASS.
- [ ] **Step 8:** Run change detection; commit with `feat(alpha): enforce statistical and operational promotion gates`.

### Task 18: End-to-End Shadow, Paper, and Controlled Live Verification

**Files:** No new source file unless a failing verification exposes a missing test seam. Update runbooks only with commands that have been executed successfully.

- [ ] Run the full focused suite:

```bash
cd backend
pytest \
  tests/test_persistence_safety.py \
  tests/test_purge_backtest_secrets.py \
  tests/test_market_marks.py \
  tests/test_alpaca_market_marks.py \
  tests/test_alpha_local_store.py \
  tests/test_alpha_risk.py \
  tests/test_alpha_rethink_store.py \
  tests/test_alpha_api.py \
  tests/test_alpha_benchmark.py \
  tests/test_alpha_metrics.py \
  tests/test_alpha_calibration.py \
  tests/test_alpha_forecasts.py \
  tests/test_alpha_challenger.py \
  tests/test_alpha_tax.py \
  tests/test_alpha_allocator.py \
  tests/test_alpha_execution.py \
  tests/test_alpha_runtime.py \
  tests/test_alpha_research.py \
  tests/test_alpha_promotion.py \
  tests/test_alpha_replay_july10.py -v
```

Expected: all tests pass; no warning contains a canary secret.

- [ ] Run the existing broker/Nexus regression suites selected by `gitnexus_detect_changes`; require zero new failures.
- [ ] Start SHADOW against the real data feed with live order submission disabled at both runtime and broker preflight. Confirm 100% local prediction/gate/allocation/intent persistence and zero alpha client-order IDs at Alpaca.
- [ ] Reconcile one full shadow day: every qualified forecast has gates and an allocation outcome; every target delta has either an intent or a rejection reason; mark ages stay within SLA during market hours.
- [ ] Run PAPER on an Alpaca paper account. Exercise partial fills, a rejected order, stream disconnect, RethinkDB outage, process restart, manual order contamination, HARD drawdown, and KILL drawdown using controlled fixtures.
- [ ] Run the registered walk-forward matrix. The readiness command must reject promotion unless every Task 17 gate passes.
- [ ] After at least four shadow weeks and six paper weeks, request LIVE_40 with `verify_alpha_readiness.py --tier live_40`. An authenticated operator reviews and records the promotion; the software never self-promotes.
- [ ] Start live at 40% active only. Confirm 2% cash target, SPY residual, <=10 names, <=8% each, <=20% sector, no margin, tax decision for each SPY order, and a current persistent high-water mark.
- [ ] LIVE_60 and LIVE_80 remain unavailable until their time, sample, performance, safety, and market-data gates independently pass.

---

## Execution Order

```text
0 -> 1 -> 2 -> 3 -> 4 -> 5 -> 6
  -> 7 -> 8 -> 9
  -> 10 -> 11 -> 12 -> 13 -> 14 -> 15
  -> 16 -> 17 -> 18
```

Tasks 10 and 11 may run in parallel after Tasks 7-9. Tasks 12 and 13 may be developed in parallel after the typed records exist, but Task 14 consumes both. All remaining tasks are sequential because they share promotion and order-authority state.

## Plan Self-Review

- **Spec coverage:** containment/security are Tasks 0-2; streaming/REST marks, an independent watchdog, and persistent drawdown are Tasks 3-6; immutable ledgers/indexed API are Tasks 5, 7, and 8; benchmark and Deflated-Sharpe accounting are Task 9; forecasts/challenger are Tasks 10-11; wash-sale behavior is Task 12; 40-80% benchmark replacement and horizon expiry are Task 13; stops/drawdown/order safety are Task 14; runtime modes are Task 15; registered validation is Task 16; promotion thresholds and 40/60/80 rollout are Tasks 17-18.
- **Boundary check:** broker adapters own broker state and market marks; forecast producers own evidence only; allocator owns target weights; execution owns orders; research cannot submit; promotion cannot alter metrics. No allocator logic is added to Graph Nexus or `broker.py`.
- **Type consistency:** `RunOrigin`, `ExecutionMode`, `EventKind`, typed record IDs, `RiskState`, `AllocationPlan`, and `OrderIntent` are defined before consumers and retain identical names throughout.
- **Legacy safety:** OFF remains the default and keeps the old path. SHADOW submits nothing. PAPER/LIVE establish one alpha order authority and suppress legacy Graph submissions.
- **No placeholders:** every task names concrete files, interfaces, tests, commands, expected results, and commit boundaries.

## Adversarial Review

The review below assumes a hostile market, partial infrastructure failure, contaminated
history, and an optimizer that exploits any metric or gate ambiguity.

### Finding A1 - A 15% trigger cannot guarantee a 15% realized drawdown

An overnight gap, trading halt, rejected liquidation, or disconnected broker can cross
the threshold before orders fill. The plan therefore treats 15% as a kill trigger and
promotion ceiling, not a guaranteed fill price. Tasks 6, 14, and 18 require staged
reduction beginning at 8%/12%, independent broker-state fallback, and explicit recording
of gap/rejection residual risk. No documentation may promise a hard loss guarantee.

### Finding A2 - The 40% active floor could force low-quality trades

An optimizer could satisfy the allocation headline by lowering forecast eligibility.
Task 13 explicitly makes 40% a normal target, never an eligibility override; residual
capital stays in SPY. Task 17 evaluates the full portfolio so hiding in SPY cannot be
reported as active-model success.

### Finding A3 - SPY tax logic can become a risk-control veto

A wash-sale guard could strand exposure during a selloff. Tasks 12 and 14 make emergency
risk reduction override tax blocks while retaining the tax warning and lot record. The
system does not claim cross-account tax completeness or assume VOO/IVV are safe substitutes.

### Finding A4 - SQLite durability is illusory on ephemeral container storage

WAL mode does not help if the volume disappears. Task 5 requires PAPER/LIVE startup to
verify a writable persistent `LIVE_TRADING_STATE_DIR`; Task 17 fails promotion on backlog
or durability-health failure. Deployment must mount and back up that path.

### Finding A5 - Shadow evaluation can leak future news or revised graph data

Replaying current Neo4j/news state against old prices would manufacture alpha. Task 16
rejects mutable/current sources, requires point-in-time snapshot IDs, freezes model
outputs, applies purge/embargo, and permits one sealed-holdout evaluation per model family.
If 24 months of point-in-time evidence do not exist, the plan blocks promotion rather
than filling the gap with synthetic history.

### Finding A6 - Repeated matrix testing can still overfit the holdout

The experiment matrix contains many combinations. Task 16 registers them before results,
uses nested folds for horizon/ceiling selection, and records failures. Task 17 uses
bootstrap and multiple-testing-aware metrics; any config change invalidates the promotion
hash and cannot reuse a previous approval.

### Finding A7 - Graph score is not an expected return

Directly mapping a +2 score to a return would make allocation arbitrary. Task 10 blocks
uncalibrated forecasts and requires at least 100 resolved observations with both classes.
The current 25 entries cannot satisfy this gate.

### Finding A8 - Partial fills can double-spend SPY proceeds

The existing broker credits expected same-cycle sell proceeds before fills. Task 14 makes
the target planner sells-first but caps buy availability to settled/cached cash plus only
the filled portion observed through WAL; idempotent intent IDs prevent a retry from
duplicating the remainder. Paper verification must exercise delayed partial fills.

### Finding A9 - Manual orders can break attribution and tax lots

Eight observed exits lacked bot client-order IDs. Tasks 12, 15, and 18 reconcile broker
positions/fills against WAL and freeze the affected symbol on unexplained quantity. A
manual action becomes a typed external event; it is never silently credited to a model.

### Finding A10 - Benchmark metrics can be gamed by timestamp or dividend choices

Task 9 inner-aligns timestamps, uses adjusted SPY total return, separates cash flows, and
stores feed/adjustment metadata. Both raw portfolio return and active return remain visible.
An alignment with fewer than two observations is an error, not zero active return.

### Finding A11 - IEX can look fresh while remaining incomplete

Freshness alone does not make a single-exchange mark consolidated. `MarketMark` carries
quality separately from age; Task 17 rejects LIVE_80 without SIP-quality data. Lower
tiers still report IEX quality explicitly and use broker-position marks as an independent
safety comparison.

### Finding A12 - Secret redaction can miss an unexpected field name or backup

Key-name filtering alone is insufficient. Task 1 also scans values, rejects a secret-
bearing complete payload before persistence, and uses canary tests. Task 2 covers existing
rows, while Task 0 requires provider rotation because redaction cannot unexpose a secret
already read or backed up.

### Finding A13 - A promotion report could be valid for different code or data

Task 17 binds promotions to config, model, data snapshot, cost model, and code hashes.
Any mismatch or expiry returns mode to the prior tier. The runtime, not the UI, enforces
this check before PAPER/LIVE order authority starts.

### Finding A14 - The plan could ship plumbing without proving alpha

Tasks 0-15 improve safety and measurement but do not establish outperformance. Tasks 16-
18 are mandatory promotion gates, not optional follow-up. A safe bot with failed active
metrics stays in SPY/shadow mode and is not described as successful alpha.
