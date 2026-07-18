# Controlled Benchmark-Relative Alpha Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Revised:** 2026-07-18 - Alpaca main forensics, fresh-direct challenger, and crash-safe execution

**Goal:** Replace direct Graph Nexus order authority with a fresh-price, benchmark-relative portfolio system that targets SPY +10 percentage points annualized while enforcing a 15% drawdown ceiling and producing statistically defensible evidence before live exposure increases.

**Architecture:** Add a focused `backend/benchmark_alpha/` package for typed forecasts, durable ledgers, allocation, tax controls, risk, execution, research, and promotion. Keep broker-specific market-mark truth in `backend/market_marks.py`; integrate it into the existing adapters and `broker.py` behind an off-by-default mode gate. Graph Nexus and a deterministic challenger become forecast producers, while one allocator and one risk/execution engine own all target weights and orders.

**Tech Stack:** Python 3, pytest, standard-library dataclasses/enums/statistics, NumPy, pandas, scikit-learn calibration, Alpaca `TradingClient`/`StockDataStream`, RethinkDB, FastAPI.

## Global Constraints

- Live equity order generation on `alpaca-main` stays paused through Task 18 and an
  explicit LIVE_40 promotion. Passing Tasks 0-6 repairs safety prerequisites but grants
  no legacy or alpha order authority.
- The July 18 read-only audit observed `alpaca-main.runCommand=true`; Task 0 must turn it
  off and verify the halt before any implementation work is treated as a live safeguard.
- `benchmark_alpha_mode` defaults to `"off"`; no migration may silently enable shadow, paper, or live operation.
- Normal allocation is 2% cash, 18-58% SPY, and 40-80% active stocks; safety and eligibility may reduce active exposure below 40%.
- `LIVE_40` starts at up to 40% fresh-direct active exposure, 58-98% SPY residual, and
  2% cash.
  Forty percent is a promoted target, never an eligibility floor. LIVE_60 and LIVE_80
  require separate promotion evidence.
- Maximums: 10 active stocks, 8% per stock, 20% per active sector, 100% gross exposure, no margin.
- Account type, settlement, day-trading, and buying-power rules come from current broker
  capability fields, not a hard-coded PDT assumption. An unknown capability blocks
  exposure increases.
- Normal same-session traded notional is capped at the promoted active tier: 40% of NAV
  in LIVE_40, 60% in LIVE_60, and 80% in LIVE_80. Emergency reductions are exempt but
  separately attributed. Research may select a lower cap, never a higher unpromoted one.
- Initially only a fresh, current-cycle direct forecast may emit an alpha order. Legacy
  backfill, scheduled votes, breakout overrides, propagation, reserved slots, and
  high-conviction bypasses remain research-only until separately promoted.
- A deterministic challenger is a research control and shadow candidate. Its success
  alone cannot pass the Stage B GO gate or authorize LIVE_40 without a separately reviewed
  design amendment.
- New exposure requires a mark observed within 60 seconds; a broker-position fallback may be at most 120 seconds old.
- A typed mark is required at decision time and again immediately before submission;
  historical bars and fill prices are not valid current-quote substitutes.
- Promotion to 80% active requires SIP-quality consolidated data or a documented equivalent.
- `drawdown_magnitude = max(0, 1 - adjusted_equity / peak_adjusted_equity)` is always a
  positive fraction. States are soft at 0.08, hard at 0.12, and kill at 0.15; emergency
  reduction overrides tax and turnover guards.
- SPY wash-sale controls use FIFO-compatible lots, a 30-day lookback, and a 31-day post-loss-sale repurchase block.
- FIFO and wash-sale-window tracking cover every repeatedly traded symbol. SPY also uses
  the residual-sleeve batching policy.
- Backtests and research use point-in-time snapshots, deterministic model-output replay, costs, and a sealed holdout.
- The current 26-entry horizon sample is hypothesis-generating and is never reused as a
  sealed holdout or promotion sample.
- No secret value may be persisted in BacktestResults, alpha ledgers, logs, tests, fixtures, API responses, or migration output.
- RethinkDB is the sole application persistence database for alpha events, state, orders,
  fills, outcomes, experiments, and promotions.
- Alpaca is authoritative for broker positions, balances, orders, fills, and account
  activities; RethinkDB is the sole IntelliStock application database and stores their
  reconciled provenance. No secondary application database is introduced.
- Every performance report must show fixed first-funding, final-funding, strategy-start,
  rolling-window, and promotion-inception lenses. It may not select a favorable start date.
- Every broker order is classified as strategy, dashboard/manual, or unresolved. Any
  unresolved ownership blocks promotion.
- The execution scheduler tick (`FULL`, `MONITOR`, `IDLE`) and brokerage execution mode
  (`OFF`, `OBSERVE`, `SHADOW_PORTFOLIO`, `PAPER`, `LIVE`) are different typed fields.
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
- `backend/benchmark_alpha/rethink_store.py` - authoritative RethinkDB events, state, indexed reads, and outage reconciliation.
- `backend/benchmark_alpha/risk.py` - mark-health and portfolio drawdown state machines.
- `backend/benchmark_alpha/watchdog.py` - independent broker/strategy mark and equity watchdog.
- `backend/benchmark_alpha/emergency.py` - narrowly scoped, broker-reconciled reduce-only executor.
- `backend/benchmark_alpha/benchmark.py` - adjusted SPY series and inception/high-water accounting.
- `backend/benchmark_alpha/metrics.py` - active-return and risk statistics.
- `backend/benchmark_alpha/outcomes.py` - strict trading-calendar horizon outcome evaluator.
- `backend/benchmark_alpha/costs.py` - versioned execution-cost and implementation-shortfall model.
- `backend/benchmark_alpha/shadow.py` - quote-driven virtual cash, positions, fills, and costs.
- `backend/benchmark_alpha/research_policy.py` - non-trading portfolio/tax policy used by the Stage B gate.
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
- `backend/nexus_runtime_state.py` - extend the existing RethinkDB WAL/state boundary with alpha identifiers and hard-durability writes.
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
- `backend/tests/test_alpha_emergency.py`
- `backend/tests/test_alpha_risk.py`
- `backend/tests/test_alpha_rethink_store.py`
- `backend/tests/test_alpha_api.py`
- `backend/tests/test_alpha_benchmark.py`
- `backend/tests/test_alpha_metrics.py`
- `backend/tests/test_alpha_outcomes.py`
- `backend/tests/test_alpha_costs.py`
- `backend/tests/test_alpha_shadow.py`
- `backend/tests/test_alpha_research_policy.py`
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

- [ ] Record the July 18 read-only baseline: `runCommand=true`, equity $5,979.38, cash
  $1,624.15, seven positions, no SPY, no open orders, 46 filled orders, and 127 account
  activities. Store values only in the redacted incident record.
- [ ] Set `alpaca-main.runCommand=False` and confirm no process is generating new equity
  orders. Re-read the instance and broker open-order state; a successful API response alone
  is not proof of containment.
- [ ] Persist and verify `legacy_order_authority_disabled=true` for `alpaca-main` in the
  RethinkDB containment state. Only an authenticated Task 18 LIVE_40 promotion may replace
  HALT with alpha authority; it never restores legacy order authority.
- [ ] Cancel open entry orders. Do not liquidate held positions as part of this administrative step.
- [ ] Capture redacted values for equity, cash, positions, open orders, `LiveOrderWAL`
  counts, current config hash, latest live log timestamp, account capability fields, and
  active non-paper status.
- [ ] Record the broker baseline and ownership reconciliation: 38 WAL-linked strategy
  orders, eight July 6 dashboard sells, and zero unclassified orders. Preserve broker order
  IDs only in access-controlled operational evidence, never this repository.
- [ ] Create a migration disposition for each current position: adopted, exit-only, or
  external/quarantined. Explicitly address the observed 8% cap violations in CNC, KNX, BX,
  S, OKTA, QLYS, and EWTX.
- [ ] Rotate every credential found in historical BacktestResults: Alpaca, OpenRouter, Azure, Bedrock/AWS, Benzinga, and Neo4j. Update runtime secret sources without printing values.
- [ ] Verify each retired credential fails a minimal authentication probe and each replacement succeeds. Record provider name and boolean result only.
- [ ] Preserve the July 10 log excerpt and sanitized database counts needed by `test_alpha_replay_july10.py`; never preserve authenticated response bodies containing secrets.
- [ ] Preserve the July 18 sanitized reconciliation fixture: two deposits, dividends,
  fees, multi-fragment fills, manual-order ownership, and the $0.0129 ledger residual.
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

- [x] **Step 1: Write failing recursive-sanitizer tests.** *(11 tests incl. provider aliases, deep-copy isolation, secret_ref scheme validation.)*

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

- [x] **Step 2:** Run `cd backend && pytest tests/test_persistence_safety.py -v`; expect both tests to fail because the module does not exist. *(2026-07-18: RED confirmed — ModuleNotFoundError.)*
- [x] **Step 3:** Implement recursive dict/list/tuple traversal. Replace secret-key values with `{"redacted": True, "source": "runtime_secret"}`; apply logger-compatible value-pattern scanning to all strings; deep-copy non-secret values.
- [x] **Step 4:** In `load_strategies_from_db`, build `_backtest_strategy_schema` from `sanitize_snapshot`. Immediately before both BacktestResults writes, call `assert_secret_free` on the complete payload and fail the backtest write closed on `SecretMaterialError`. *(Also sanitizes the crypto synthetic-schema return path; guards the final finished-row write as well as both stub writes.)*
- [x] **Step 5:** Run `pytest tests/test_persistence_safety.py tests/test_backtest_pnl_consistency.py -v`; expect PASS. *(19 passed.)*
- [x] **Step 6:** Run impact analysis on `load_strategies_from_db`; run staged change detection; commit only the named files with `security: prevent secrets in backtest persistence`. *(GitNexus does not index broker.py — its size exceeds the indexer cap — so impact fell back to manual caller analysis: 3 call sites, all internal to broker.py; return shape unchanged. detect_changes staged: risk low, 0 affected flows. Commit 21ad009.)*

### Task 2: Purge Historical Backtest Secrets Without Re-Exposing Them

**Files:**

- Create: `backend/scripts/purge_backtest_secrets.py`
- Test: `backend/tests/test_purge_backtest_secrets.py`

**Interfaces:**

- `sanitize_backtest_row(row: dict) -> tuple[dict, int]` returns a replacement patch and number of redacted fields.
- CLI defaults to dry-run. Mutation requires both `--apply` and `--confirm-table BacktestResults`.
- Output contains row IDs, counts, and status only; it never renders old or new secret-bearing payloads.

- [x] **Step 1: Write the failing pure-row test.** *(Plus no-schema noop, CLI, and output-hygiene tests.)*

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

- [x] **Step 2:** Run the test; expect import failure. *(RED confirmed.)*
- [x] **Step 3:** Implement streaming iteration by primary key, batches of 100, sanitizer reuse from Task 1, `conflict="replace"` only for the `strategy_schema` field, and a final `assert_secret_free` verification pass. *(Implemented as a field-scoped `.update()` patch with hard durability — same replace-only-strategy_schema semantics.)*
- [x] **Step 4:** Add CLI tests proving dry-run performs zero updates and `--apply` without the confirmation string exits code 2.
- [x] **Step 5:** Run `pytest tests/test_purge_backtest_secrets.py tests/test_persistence_safety.py -v`; expect PASS. *(18 passed.)*
- [x] **Step 6:** Run the script against an injected fake RethinkDB backend in dry-run mode.
  Production execution requires a fresh encrypted database backup governed by the same
  secret-access controls; do not put that backup in git or `/tmp`. *(Fake-backend dry-run exercised in tests; PRODUCTION RUN PENDING operator backup + explicit authorization.)*
- [x] **Step 7:** Run change detection; commit with `security: add dry-run-first backtest secret purge`. *(risk low, 0 affected flows; commit follows Task 1's 21ad009.)*

---

## Phase 1 - Fresh Market Truth and Durable Safety State

### Task 3: Timestamped Market-Mark Contract

**Files:**

- Create: `backend/market_marks.py`
- Test: `backend/tests/test_market_marks.py`

**Interfaces:**

- `MarkSource`: `STREAM_QUOTE`, `STREAM_TRADE`, `REST_QUOTE`, `BROKER_POSITION`, `FILL`.
- `MarkQuality`: `CONSOLIDATED`, `SINGLE_EXCHANGE`, `BROKER_DERIVED`, `EXECUTION_ONLY`.
- Immutable `MarketMark(symbol, price, bid, ask, bid_size, ask_size, observed_at,
  received_at, source, feed, quality, session, conditions, halted)`.
- `MarkPurpose`: `DECISION`, `SUBMISSION`, `RISK_REDUCTION`, each with an explicit allowed
  source, age, spread, clock-skew, and halt policy.
- `MarketMark.age_seconds(now) -> float`.
- Thread-safe `MarketMarkBook.update(mark) -> bool`, `get(symbol)`, `snapshot()`, and `fresh_price(symbol, now, max_age_seconds, allowed_qualities=None)`.
- A mark with an older `observed_at` cannot replace a newer mark. A broker-position mark can replace a fill mark at the same timestamp; fill marks never outrank quotes or trades.

- [x] **Step 1: Write failing precedence and freshness tests.**

```python
from datetime import datetime, timedelta, timezone
from market_marks import MarkQuality, MarkSource, MarketMark, MarketMarkBook


def test_market_mark_book_rejects_older_and_expires_at_sla():
    now = datetime(2026, 7, 10, 14, 0, tzinfo=timezone.utc)
    book = MarketMarkBook()
    current = MarketMark(
        symbol="MRNA", price=76.51, bid=None, ask=None, bid_size=None, ask_size=None,
        observed_at=now, received_at=now, source=MarkSource.BROKER_POSITION,
        feed="broker", quality=MarkQuality.BROKER_DERIVED,
        session="regular", conditions=(), halted=False,
    )
    assert book.update(current) is True
    older = MarketMark(
        symbol="MRNA", price=81.36, bid=None, ask=None, bid_size=None, ask_size=None,
        observed_at=now - timedelta(minutes=5), received_at=now,
        source=MarkSource.FILL, feed="execution",
        quality=MarkQuality.EXECUTION_ONLY, session="regular",
        conditions=(), halted=False,
    )
    assert book.update(older) is False
    assert book.fresh_price("MRNA", now + timedelta(seconds=59), 60) == 76.51
    assert book.fresh_price("MRNA", now + timedelta(seconds=61), 60) is None
```

- [x] **Step 2:** Run `pytest tests/test_market_marks.py -v`; expect import failure. *(RED confirmed.)*
- [x] **Step 3:** Implement enum ordering explicitly in a `_SOURCE_PRIORITY` mapping, validate positive finite prices and timezone-aware timestamps, and guard the internal dict with `threading.RLock`.
- [x] **Step 4:** Add tests for case-normalized symbols, defensive snapshot copies, NaN/zero rejection, and quote-over-fill precedence.
- [x] **Step 4a:** Add decision-versus-submission purpose tests, crossed/locked quote
  handling, zero size, halt/LULD conditions, session classification, and exchange-clock
  skew. A fill mark must fail every exposure-increase purpose. *(evaluate_mark + PURPOSE_POLICIES; RISK_REDUCTION stays available degraded with recorded reasons.)*
- [x] **Step 5:** Run the test file; expect PASS. *(17 passed.)*
- [x] **Step 6:** Run change detection; commit with `feat: add timestamped market mark book`. *(risk low; commit 92809cf.)*

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
- The order path obtains a second fresh mark immediately before submission, persists it
  with the intent, measures drift from the decision mark, and fails a buy outside its
  registered collar.

- [x] **Step 1:** Run GitNexus impact on `refresh_positions`, `save_portfolio_snapshot`, `_on_trade_update`, and `_ensure_prices_include_positions`. Record direct callers and warn before continuing if any risk is HIGH/CRITICAL. *(refresh_positions LOW/2 direct; _on_trade_update LOW/0; save_portfolio_snapshot LOW/0. _ensure_prices_include_positions is in broker.py which GitNexus does not index (file-size cap) — manual analysis: 5 call sites, all internal to broker.py, signature unchanged.)*
- [x] **Step 2: Write a regression test reproducing the July 10 failure.**

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

- [x] **Step 3:** Run the regression plus clean-room tests; expect the regression to fail at 81.36. *(RED confirmed: `assert 81.36 == 76.51`.)*
- [x] **Step 4:** Initialize `MarketMarkBook`, update it from position REST responses and trade events, and mirror accepted newest prices into `_last_prices`. Do not overwrite `avg_entry_price` or reconstructed entry-trade state. *(Fill events write EXECUTION_ONLY marks; save_portfolio_snapshot records REST_QUOTE marks.)*
- [x] **Step 5:** Implement `AlpacaMarkStream` with injected stream factory for tests. Add callback tests proving a newer IEX quote replaces a broker mark, an older callback is ignored, subscription changes are idempotent, disconnect sets degraded health, and reconnect never blocks the broker strategy thread. *(Plus 30-symbol budget with explicit overflow — H02.)*
- [x] **Step 6:** Make `_ensure_prices_include_positions` resolution origin-specific.
  BACKTEST may use the registered bar.
  PAPER/LIVE decisions use a fresh stream/REST quote or broker-position fallback within
  policy; an unavailable current mark fails closed. No delayed or scalar fallback may
  authorize an exposure increase. *(Live resolution: typed mark ≤120s → fresh fetch → stale scalar VALUATION-ONLY → yfinance. Increase authority is `BrokerAdapter.decision_price`, which fails closed; broker.py itself is not unit-importable (module-level argparse), so the gate lives in base.py where it is tested.)*
- [x] **Step 7:** Run `pytest tests/test_alpaca_market_marks.py tests/test_clean_room_adapter_init.py tests/test_broker_adapter_base.py -v`; expect PASS. *(29 passed; adapter/WAL/broker sweep: 130 passed.)*
- [x] **Step 8:** Run the July 10 regression twice to prove a second refresh changes the mark. Run change detection and commit with `fix: stream and refresh current marks instead of preserving fills`. *(76.51 → 68.26 second-leg test; risk low, 0 affected flows; commit ab215a6.)*
- [x] **Step 9:** Add a regression proving that a historical one-minute bar fetched near
  submission cannot retroactively satisfy the decision-time mark gate and cannot silently
  fall back to a fill price. *(test_stale_bar_near_submission_cannot_satisfy_decision_gate.)*

### Task 5: Authoritative RethinkDB Event and State Store

**Files:**

- Create: `backend/benchmark_alpha/__init__.py`
- Create: `backend/benchmark_alpha/types.py`
- Create: `backend/benchmark_alpha/rethink_store.py`
- Create: `backend/scripts/migrate_alpha_tables.py`
- Modify: `backend/nexus_runtime_state.py`
- Test: `backend/tests/test_alpha_rethink_store.py`

**Interfaces:**

- `RunOrigin`: `BACKTEST`, `OBSERVE`, `SHADOW_PORTFOLIO`, `PAPER`, `LIVE`.
- `ExecutionMode`: `OFF`, `OBSERVE`, `SHADOW_PORTFOLIO`, `PAPER`, `LIVE`.
- `SchedulerTickMode`: `FULL`, `MONITOR`, `IDLE`. It is never accepted where
  `ExecutionMode` is required.
- `PromotionTier`: `OBSERVE`, `SHADOW_PORTFOLIO`, `PAPER`, `LIVE_40`, `LIVE_60`,
  `LIVE_80`.
- Immutable `AuthorizationContext(tier, active_cap, evidence_allowlist, expires_at,
  artifact_hashes)`. Missing, expired, or malformed authorization permits no exposure
  increase.
- `RunPhase`: `FORECASTED`, `ALLOCATED`, `INTENTS_WRITTEN`,
  `REDUCTIONS_SUBMITTED`, `REDUCTIONS_SETTLED`, `INCREASES_SUBMITTED`,
  `SETTLED_OR_EXPIRED`.
- `EventKind`: `PREDICTION`, `DECISION`, `GATE`, `ALLOCATION`, `ORDER_INTENT`,
  `BROKER_ORDER`, `FILL`, `CASH_ACTIVITY`, `PORTFOLIO_SNAPSHOT`, `OUTCOME`, `RISK`,
  `TAX`, `INCIDENT`, `DEMOTION`.
- `AlphaRethinkStore(r_module, conn_factory, db_name)` is the sole alpha persistence boundary.
- `AlphaRethinkStore.for_backend(backend)` injects a deterministic test backend without
  changing production RethinkDB semantics.
- Tables `AlphaEvents` and `AlphaState` hold append-only events and versioned mutable state;
  the existing `LiveOrderWAL` remains the authoritative order-intent table.
- `append_event(event_id, kind, payload, created_at) -> bool` writes with hard durability,
  returns `False` only for a byte-identical existing ID, and raises `AlphaIntegrityError`
  for divergent content.
- `put_state(key, payload, expected_version) -> StateRecord` uses compare-and-swap semantics;
  `get_state(key)` and `health()` never translate an unavailable database into empty state.
- A durable `run:<instance_id>:<run_id>` state stores the immutable target and current
  phase. Restart reconciles Alpaca and resumes the first incomplete phase rather than
  relying on a date-only `full_cycle_completed` marker.
- PAPER/LIVE normal order authority starts only when RethinkDB health, table readiness, and
  WAL writes pass. An outage blocks exposure increases and enters `HALT`; Task 14 defines
  the deterministic broker-reconciled exception for quantity-capped risk reductions.

- [x] **Step 1: Write failing hard-write, idempotency, and state-version tests.**

```python
from datetime import datetime, timezone
import pytest
from benchmark_alpha.rethink_store import AlphaIntegrityError, AlphaRethinkStore
from benchmark_alpha.types import EventKind


class FakeBackend:
    def __init__(self):
        self.events = {}
        self.states = {}
        self.durabilities = []

    def insert_event(self, doc, *, durability):
        self.durabilities.append(durability)
        prior = self.events.get(doc["id"])
        if prior is not None:
            return prior
        self.events[doc["id"]] = doc
        return None

    def compare_and_swap_state(self, key, expected_version, doc, *, durability):
        self.durabilities.append(durability)
        prior = self.states.get(key)
        version = 0 if prior is None else prior["version"]
        if version != expected_version:
            return None
        self.states[key] = doc
        return doc


def test_rethink_store_uses_hard_writes_and_rejects_divergent_duplicate():
    backend = FakeBackend()
    store = AlphaRethinkStore.for_backend(backend)
    ts = datetime(2026, 7, 11, tzinfo=timezone.utc)
    assert store.append_event("e1", EventKind.PREDICTION, {"symbol": "AAPL"}, ts)
    assert store.append_event("e1", EventKind.PREDICTION, {"symbol": "AAPL"}, ts) is False
    with pytest.raises(AlphaIntegrityError):
        store.append_event("e1", EventKind.PREDICTION, {"symbol": "MSFT"}, ts)
    assert backend.durabilities == ["hard", "hard", "hard"]
```

- [x] **Step 2:** Run `pytest tests/test_alpha_rethink_store.py -v`; expect import failure. *(RED confirmed.)*
- [x] **Step 3:** Implement canonical JSON serialization (`sort_keys=True`, compact separators),
  UTC timestamps, hard-durability inserts, byte-identical duplicate checks, and
  compare-and-swap state versions. Add a dry-run-first migration for `AlphaEvents`,
  `AlphaState`, and required `LiveOrderWAL` indexes. Reuse the repository's bounded
  RethinkDB connection factory and never log connection credentials.
- [x] **Step 4:** Extend `nexus_runtime_state.py` so `LiveOrderWAL` rows include
  `instance_id`, `run_id`, `allocation_id`, and `intent_id`, with indexes for instance and
  client-order prefix. Preserve existing callers through optional fields. *(ensure_alpha_wal_indexes(); WALStore.insert already passes optional row fields through — record_intent threads them in Task 14 as planned there.)*
- [x] **Step 5:** Add tests for divergent event IDs, stale state versions, connection
  timeout, unavailable-table health, and the invariant that storage errors are never
  returned as empty successful reads.
- [x] **Step 5a:** Add crash/restart tests for every run phase, plus a type test proving
  `FULL`, `MONITOR`, and `IDLE` cannot be interpreted as brokerage execution mode. Assert
  live safeguards are enabled in every LIVE scheduler tick.
- [x] **Step 6:** Run tests; expect PASS. Run change detection; commit with
  `feat(alpha): add authoritative RethinkDB event and state store`. *(12 passed + 23 WAL/runtime-state regressions; risk low, 0 affected flows; commit b2634a1. PRODUCTION migration run PENDING operator authorization.)*

### Task 6: Freshness Watchdog and Persistent Drawdown State

> **STATUS 2026-07-18 (commit 7f88a14):** Steps 1-8 DONE (risk.py flow-adjusted
> state machine w/ exact 8/12/15 boundaries + July 18 5.8259% fixture; mark
> health; legacy_live_order_block fail-closed; watchdog alert→3-strike
> cancel/halt/reduce w/ degraded-audit; reduce-only executor w/ deterministic
> episode client IDs; broker.py containment gate at the execute_signal choke
> point + GATE events + snapshot mark_health/risk/held-count). Step 8a:
> scoped-credential support via ALPACA_WATCHDOG_KEY/SECRET; shared-credential
> residual risk documented in watchdog_main + LIVE_40 sign-off note. Step 9
> DONE (instance.py lifecycle behind ALPHA_MARK_WATCHDOG_ENABLED=1, default
> OFF; watchdog_main refuses to start without prerequisites). Step 10 suites:
> 60 passed (incl. test_live_boot_setup, test_nexus_monitor_cycle); full
> alpha/adapter sweep 279 passed. Step 11: broker.py not GitNexus-indexed
> (size cap) — manual impact analysis; detect_changes risk low, 0 flows.
> DEFERRED to runtime integration (Tasks 14/15): live-boot risk-state
> load-or-HALT wiring inside broker.py's boot path (helpers exist and are
> tested; the boot call site lands with the alpha runtime seam).
> nexus_broker_utils.py compat gate superseded by the stronger adapter-level
> containment gate (implementation-equivalent divergence).
> **Phase 1 verification gate: PASSED in test form** —
> test_alpha_replay_july10.py proves changing marks on all 8 positions,
> contained legacy orders blocked, typed reduce-only sells available, and
> restart preserving the high-water mark.

**Files:**

- Create: `backend/benchmark_alpha/risk.py`
- Create: `backend/benchmark_alpha/watchdog.py`
- Create: `backend/benchmark_alpha/emergency.py`
- Modify: `backend/instance.py:311-414`
- Modify: `backend/broker.py:4063-4385`, `backend/broker.py:9254-9296`
- Modify: `backend/live_state.py`
- Modify: `backend/nexus_broker_utils.py`
- Test: `backend/tests/test_alpha_risk.py`
- Test: `backend/tests/test_alpha_replay_july10.py`
- Test: `backend/tests/test_alpha_watchdog.py`
- Test: `backend/tests/test_alpha_emergency.py`

**Interfaces:**

- `RiskLevel`: `NORMAL`, `SOFT`, `HARD`, `KILL`.
- `RiskState(peak_adjusted_equity, last_raw_equity, cumulative_external_flow,
  drawdown_magnitude, level, updated_at)`.
- `update_risk_state(previous, raw_equity, external_capital_flows, observed_at) -> RiskState`
  uses a flow-adjusted equity series and never lowers the peak except through an explicit
  operator reset record.
- Deposits, withdrawals, and external transfers adjust capital. Dividends, interest, and
  fees remain economic return/cost but are reconciled at their broker effective time.
  Splits and reorganizations adjust quantities/marks rather than being treated as cash.
- `evaluate_mark_health(held_symbols, marks, now, entry_max_age=60, fallback_max_age=120) -> MarkHealth`.
- `legacy_live_order_block(instance_id, side, containment_state) -> str | None` blocks
  every legacy-generated order on a contained live instance. Risk reduction is available
  only through the typed alpha/emergency reduce-only path or an explicitly external
  operator action.
- State key is `risk:<instance_id>` in `AlphaRethinkStore` and is reconciled against broker equity at every snapshot.
- `AlphaWatchdog(probe, rethink_store, thresholds).poll_once(now) -> WatchdogResult`
  compares direct broker positions/equity with the broker process's RethinkDB-persisted
  marks/equity. It runs in a separate process and has no general order-submit method.
- `ReduceOnlyEmergencyExecutor.reduce_to_targets(risk_episode_id, targets)` re-reads each
  broker position, rejects buys/shorts, caps sell quantity to held quantity, and uses a
  deterministic risk-episode client ID. It has no strategy, allocator, candidate, or
  arbitrary-order interface.
- After three consecutive critical mismatches or a KILL state, the watchdog cancels entry
  orders, halts the instance, and invokes only the reduce-only executor. If RethinkDB is
  unavailable, the broker order history is temporarily authoritative and recovery
  backfills the exact action.

- [ ] **Step 1: Write failing drawdown and external-flow transition tests.**

```python
from datetime import datetime, timezone
from benchmark_alpha.risk import RiskLevel, RiskState, update_risk_state


def test_drawdown_state_preserves_peak_and_crosses_all_thresholds():
    ts = datetime(2026, 7, 11, tzinfo=timezone.utc)
    s = RiskState.initial(10000.0, ts)
    assert update_risk_state(s, 9200.0, [], ts).level is RiskLevel.SOFT
    assert update_risk_state(s, 8800.0, [], ts).level is RiskLevel.HARD
    killed = update_risk_state(s, 8500.0, [], ts)
    assert killed.level is RiskLevel.KILL
    assert killed.peak_adjusted_equity == 10000.0
```

- [ ] **Step 2:** Add a July 10 fixture with MRNA fill 81.36, broker marks 76.51 then 68.26, and account equity 6,113.98 then 5,949.05. Assert the evaluated mark ends at 68.26 and drawdown is nonzero.
- [ ] **Step 3:** Run both files; expect failures.
- [ ] **Step 4:** Implement the pure risk state machine and mark-health result. Use exact
  boundaries: `>=8` SOFT, `>=12` HARD, `>=15` KILL. Add classification tests proving
  deposits/withdrawals adjust capital, dividends/fees remain economic P&L, splits preserve
  value, and unknown flows quarantine peak changes.
- [ ] **Step 5:** Add `mark_health`, mark source/age per position, current peak,
  `drawdown_magnitude`, and risk level to `_compute_live_state_snapshot`. Correct
  held-count telemetry from the snapshot positions rather than per-cycle action counters.
- [ ] **Step 6:** Call `legacy_live_order_block` immediately before every legacy live
  submission. Persist `legacy_order_authority_disabled=true` for `alpaca-main` during
  Task 0 containment; no OFF-mode setting or successful mark-health result may clear it.
  A blocked action writes a RethinkDB GATE event when storage is healthy and logs one
  bounded alert. Risk reductions use the deterministic reduce-only path defined in Tasks
  6 and 14.
- [ ] **Step 7:** On startup, load the RethinkDB risk state and reconcile it with current
  broker equity without resetting the peak. If RethinkDB is unavailable, start in `HALT`,
  cancel entries, and do not permit increases. An explicit reset requires a separate
  operator event containing old peak, new peak, reason, and actor.
- [ ] **Step 7a:** Reproduce the July 18 account high of $6,243.15 and trough of
  $5,879.43 after removing the June 8 deposit. Assert a deposit creates no new performance
  high and `drawdown_magnitude` remains 0.058259.
- [ ] **Step 8:** Implement the independent watchdog with injected read/cancel probe and
  separate reduce-only executor. Add tests for one transient mismatch (alert only), three
  consecutive mismatches (cancel entries, halt, and reduce), healthy reset of the counter,
  and the inability of either object to buy, short, exceed held quantity, or select a
  symbol not returned by broker reconciliation.
- [ ] **Step 8a:** Use a separately scoped broker credential if Alpaca supports it for the
  account. Otherwise document that the process-level capability restriction is the
  enforceable boundary, protect the shared runtime secret independently, and require this
  residual credential risk in the LIVE_40 operator sign-off.
- [ ] **Step 9:** In `instance.py`, start and stop the watchdog subprocess alongside live
  equity brokers when `ALPHA_MARK_WATCHDOG_ENABLED=1`; keep the default disabled until the
  RethinkDB table/index migration, bounded connection timeouts, and watchdog credentials
  are deployed. Test the pure command builder and lifecycle cleanup.
- [ ] **Step 10:** Run `pytest tests/test_alpha_risk.py tests/test_alpha_watchdog.py tests/test_alpha_emergency.py tests/test_alpha_replay_july10.py tests/test_live_boot_setup.py tests/test_nexus_monitor_cycle.py -v`; expect PASS.
- [ ] **Step 11:** Run impact analysis on `_compute_live_state_snapshot`, `start_broker`, and the enclosing broker execution function, then change detection; commit with `fix(alpha): enforce fresh marks and independent drawdown watchdog`.

**Phase 1 verification gate:** with Alpaca calls stubbed, replay July 10 and prove all
eight positions receive changing marks, every contained legacy order is blocked, typed
reduce-only risk sells remain available, and restart does not reset the high-water mark.
Do not proceed to portfolio construction if this gate fails.

---

## Phase 2 - Immutable Audit, Indexed Reads, and Truthful Benchmarking

### Task 7: Typed Alpha Records and Indexed RethinkDB Tables

**Files:**

- Extend: `backend/benchmark_alpha/types.py`
- Extend: `backend/benchmark_alpha/rethink_store.py`
- Extend: `backend/scripts/migrate_alpha_tables.py`
- Test: `backend/tests/test_alpha_rethink_store.py`

**Interfaces:**

- Strict enums: `EvidenceClass(DIRECT, PROPAGATION, DETERMINISTIC)`, `GateEffect(ELIGIBLE, REJECTED, HELD)`, and `OrderEffect(SUBMITTED, ACCEPTED, PARTIAL, FILLED, CANCELED, REJECTED, EXPIRED)`.
- Immutable dataclasses `Forecast`, `GateRecord`, `TargetPosition`, `AllocationPlan`,
  `OrderIntent`, `BrokerOrderRecord`, `FillRecord`, `PortfolioSnapshot`, `CashActivity`,
  `HorizonOutcome`, and `IncidentRecord`; each implements `to_doc()` and rejects unknown
  enum values.
- Every order chain carries `run_id`, `allocation_id`, `decision_id`, `forecast_id`,
  `intent_id`, `client_order_id`, and `broker_order_id`. It also records owner source,
  effective config hash, mark ID/age/source, requested/submitted/filled/residual quantity,
  reserved cash, exact bypass or rejection code, and compact LLM call IDs or a trace
  digest when model evidence contributed. Live, lookback, and backtest attribution remain
  separate.
- A decision record is written for every buy, sell, hold, skip, eligibility rejection,
  risk override, tax override, and manual/dashboard reconciliation. An order is not
  required for a decision to be auditable.
- Tables: `AlphaPredictions`, `AlphaGates`, `AlphaAllocations`, `AlphaOrderIntents`,
  `AlphaBrokerOrders`, `AlphaFills`, `AlphaPortfolioSnapshots`, `AlphaCashActivities`,
  `AlphaOutcomes`, `AlphaIncidents`, `AlphaExperiments`, and `AlphaPromotions`.
- Indexes on each event table: `run_asof=[run_id, as_of]` and `instance_origin_asof=[instance_id, origin, as_of]`.
- IDs hash `schema_version`, record type, and that record's complete natural identity.
  Forecast identity includes producer/model version, evidence class, symbol, as-of,
  horizon, and revision. Decisions include run/cycle, symbol, and decision revision.
  Broker orders include intent/revision and broker order ID. Fills prefer Alpaca activity
  ID and otherwise include broker order, cumulative quantity, event time, and sequence.
  Outcomes include forecast ID, horizon, and data revision. A generic
  run/type/symbol/time formula is prohibited.
- `AlphaRethinkStore.write_record(record) -> bool` writes directly with
  `conflict="error"` and hard durability; an already-existing byte-identical ID is
  idempotent, while divergent content raises `AlphaIntegrityError`.

- [ ] **Step 1:** Write tests constructing each dataclass, round-tripping `to_doc`, and
  rejecting `evidence_class="unknown"`, unresolved owner source, missing decision lineage,
  and cumulative partial-fill regression.
- [ ] **Step 2:** Write a fake Rethink writer test proving hard-durability direct writes are
  idempotent only for byte-identical records and surface connection failure as unavailable.
- [ ] **Step 3:** Run tests; expect missing types/store failures.
- [ ] **Step 4:** Implement dataclasses with timezone-aware timestamp validation,
  finite-number validation, canonical uppercase symbols, and per-record natural identities
  above. Add collision tests for direct versus propagation forecasts, two producers on the
  same symbol/horizon, multiple partial fills, corrected outcomes, and same-cycle decision
  revisions.
- [ ] **Step 5:** Implement table/index creation in the migration script. The script prints table/index names and status only and supports `--dry-run`.
- [ ] **Step 6:** Implement bounded RethinkDB connection and query deadlines. Database
  failure updates explicit audit-store health, blocks increases, and never becomes an empty
  successful result. Broker-originated risk reductions are recovered by Task 14 from
  deterministic client-order IDs after connectivity returns.
- [ ] **Step 7:** Run `pytest tests/test_alpha_rethink_store.py -v`; expect PASS. Run change
  detection; commit with `feat(alpha): add typed indexed RethinkDB alpha records`.

### Task 7A: Trading Calendar and Horizon Outcome Evaluator

**Files:**

- Create: `backend/benchmark_alpha/outcomes.py`
- Extend: `backend/benchmark_alpha/rethink_store.py`
- Reuse or strictly wrap: `backend/live_calendar.py`
- Test: `backend/tests/test_alpha_outcomes.py`

**Interfaces:**

- `OutcomeEvaluator.resolve(forecast, calendar, stock_series, spy_series) -> HorizonOutcome`
  evaluates every registered eligible and rejected forecast, not only filled trades.
- Entry is the registered tradable observation after `as_of`; exit is the same observation
  after exactly 1, 3, or 5 exchange sessions. The weekday fallback in
  `live_calendar.py` is prohibited for promotion-eligible evaluation.
- Each outcome stores raw and adjusted stock/SPY prices, source timestamps, session dates,
  corporate-action state, missing-data reason, revision, and benchmark-relative return.
- Corrected source data creates a new revision. It never mutates evidence previously used
  by a promotion report.

- [ ] **Step 1:** Add failing fixtures for a holiday, early close, split, symbol change,
  delisting, missing bar, rejected forecast, and untraded eligible forecast.
- [ ] **Step 2:** Add legacy-corruption fixtures proving `unknown` intent is rejected,
  sell-direction return semantics are not inverted, and KLAC/FEMY split artifacts cannot
  pass as extreme alpha.
- [ ] **Step 3:** Implement strict calendar/session alignment and adjustment metadata.
  Store a conservative missing outcome when evidence cannot be resolved; never drop it
  from the denominator.
- [ ] **Step 4:** Reproduce the July 18 1/3/5/10-day diagnostic only as
  `hypothesis_generating=True`. Assert the 26 observed entries cannot be registered as a
  sealed holdout.
- [ ] **Step 5:** Run `pytest tests/test_alpha_outcomes.py -v`; expect PASS. Run change
  detection; commit with `feat(alpha): add unbiased horizon outcome evaluator`.

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
  - `GET /instances/{instance_id}/alpha/predictions?origin=&run_id=&limit=&cursor=`
  - `GET /instances/{instance_id}/alpha/allocations?origin=&run_id=&limit=&cursor=`
  - `GET /instances/{instance_id}/alpha/performance?origin=&run_id=`
  - `GET /instances/{instance_id}/alpha/readiness`
- Limits are clamped to 1-500, cursors are opaque compound-index positions, and queries
  require exact instance plus origin or run scope.
- The legacy scorecard is explicitly marked untrusted while the known baseline remains:
  104,352 trade contexts, 17,355 unknown context intents, 2,054 current-scope forward
  outcomes, and 1,752 unknown outcome intents. Recomputed lookback contexts never claim to
  be the causal record for a live fill.

- [ ] **Step 1:** Run impact analysis on `action_nexus_trade_contexts` and `action_nexus_outcome_stats`; record LOW/HIGH assessment.
- [ ] **Step 2:** Add tests whose fake table raises if a lambda full-table filter or an unindexed `.run()` is used; assert `.get_all(instance_id, index="base_instance_id")` is selected.
- [ ] **Step 3:** Add API authentication/shape tests for the four alpha endpoints, including 401 without auth and limit clamping.
- [ ] **Step 4:** Run tests; expect failures against the full-table implementation.
- [ ] **Step 5:** Run impact analysis on `_normalize_action_intent` and `_save_trade_contexts_and_outcomes`. Change invalid-intent handling so queue/deferred states cannot create directional outcomes, and add tests for every intent currently emitted by Graph Nexus.
- [ ] **Step 5a:** Run impact analysis on `_log_live_trade_decision`. Replace its
  best-effort daemon-thread write with the Task 7 hard-durability decision event before
  normal submission. A logging failure blocks increases; it is never swallowed.
- [ ] **Step 6:** Implement indexed legacy queries and alpha store read methods. Mark the
  old outcome scorecard `data_status="legacy_untrusted"` while any invalid historical rows
  remain. Return explicit `audit_store_health`, `last_successful_write_at`, and
  `last_successful_read_at`; never convert storage failure into an empty successful scorecard.
- [ ] **Step 7:** Add retention to `migrate_alpha_tables.py`: raw candidate predictions 400 days, high-frequency mark-health events 30 days, allocations/orders/fills/promotions retained indefinitely unless policy changes.
- [ ] **Step 8:** Run API and telemetry tests; expect PASS. Run change detection; commit with `perf(alpha): scope telemetry reads and reject invalid outcomes`.
- [ ] **Step 9:** Add a reconciliation API that reports broker fills without decision/WAL
  lineage, decisions without intents, and fills without terminal state. Use the July 18
  baseline of 38 WAL-linked and eight dashboard-owned orders; any unresolved owner is a
  readiness failure.

### Task 9: Inception Equity, SPY Total Return, and Active Metrics

**Files:**

- Create: `backend/benchmark_alpha/benchmark.py`
- Create: `backend/benchmark_alpha/metrics.py`
- Modify: `backend/live_broker_fetch.py`
- Modify: `backend/backtest_summary.py:40-75`
- Modify: `backend/broker.py:4146-4154`, `:4298-4300`
- Test: `backend/tests/test_alpha_benchmark.py`
- Test: `backend/tests/test_alpha_metrics.py`

**Interfaces:**

- `align_return_series(portfolio_values, spy_adjusted_values) -> pandas.DataFrame` performs timestamp-normalized inner alignment and rejects fewer than two observations.
- `compute_active_metrics(aligned, annualization=252, bootstrap_seed=179) -> ActiveMetrics`
  returns portfolio/benchmark/net active return, beta, tracking error, information ratio,
  Sharpe, Sortino, positive `max_drawdown_magnitude`, Calmar, Deflated Sharpe probability,
  and 90% bootstrap active-return interval.
- `deflated_sharpe_probability(sharpe, sample_count, skew, kurtosis, trials) -> float` uses `statistics.NormalDist`; `trials` is the complete registered experiment count, including failures.
- `InceptionState(inception_equity, inception_at, high_water_equity, source)` is durable and never replaced by ordinary restart equity.
- Benchmark fetches request adjusted SPY bars and record feed, adjustment, request time, and data snapshot ID.
- `BrokerHistoryIngestor` paginates Alpaca account activities and stores fills, cash
  deposits/withdrawals, dividends, fees, and corporate actions idempotently by activity ID.
- The canonical account history uses 1D marks from account start for performance and a
  separate intraday request with `intraday_reporting=continuous`,
  `pnl_reset=no_reset`, and explicit cash-flow treatment for monitoring. Deprecated
  `extended_hours=true` is not the accounting contract.
- Daily portfolio UTC timestamps map to the preceding valid `America/New_York` session
  before matching adjusted SPY. Multi-fragment fills aggregate by broker order ID before
  order- and FIFO-level attribution.
- Fixed report lenses are `first_funding`, `final_funding`, `strategy_start`,
  `rolling_1m`, `promotion_inception`, and `current_tier`. Each reports full-account,
  strategy-owned, dashboard/manual, and active-sleeve attribution where defined.

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
- [ ] **Step 2a:** Add the July 18 golden account fixture. Assert:
  - final-funded return -0.3437%, SPY +0.8056%, active -1.1493pp;
  - rolling-month return -3.0104%, SPY -0.4647%, active -2.5457pp;
  - funded-period `max_drawdown_magnitude` 5.8259%;
  - the first-funding TWR removes the June 8 $4,000 deposit;
  - the account ledger reconciles within $0.05.
- [ ] **Step 3:** Run tests; expect import failures.
- [ ] **Step 4:** Implement metrics with NumPy/pandas and `statistics.NormalDist`; use daily percentage returns for beta/information ratio and value series for cumulative return/drawdown. Bootstrap contiguous daily blocks of length five with a fixed seed. Add Bailey/Lopez de Prado Deflated Sharpe using the registered trial count, sample skew, and excess kurtosis.
- [ ] **Step 5:** Extend `compute_backtest_summary` by merging, not replacing, its existing
  truthful P&L fields. Add `benchmark_return`, `active_return`, `beta`, `tracking_error`,
  `information_ratio`, positive `max_drawdown_magnitude`, and bootstrap bounds.
- [ ] **Step 6:** Persist inception/high-water state via `AlphaRethinkStore`; expose it in
  LiveState. Account cash flows are recorded separately so deposits/withdrawals do not
  become trading return. An unavailable store starts or keeps the runtime in `HALT` and
  cannot reset the peak.
- [ ] **Step 6a:** Run impact analysis on the portfolio-history function in
  `live_broker_fetch.py`. Remove the fallback that sets `initial_value=current_equity`
  after an empty 1M/15Min response. Remove its duplicate history semantics, reuse the
  canonical history ingestor, and expose explicit `history_unavailable` rather than
  reporting zero total P&L.
- [ ] **Step 6b:** Ingest account activities with pagination and preserve source IDs.
  Reconcile the 46-order/127-activity baseline, 90 fill activity fragments, two deposits,
  two dividends, and 33 fee activities without double counting.
- [ ] **Step 7:** Run `pytest tests/test_alpha_benchmark.py tests/test_alpha_metrics.py tests/test_backtest_pnl_consistency.py -v`; expect PASS. Run impact analysis on `compute_backtest_summary`, then change detection; commit with `feat(alpha): add truthful SPY-relative performance accounting`.

### Task 9A: Equity Cost Model and Shadow Portfolio

**Files:**

- Create: `backend/benchmark_alpha/costs.py`
- Create: `backend/benchmark_alpha/shadow.py`
- Create: `backend/benchmark_alpha/research_policy.py`
- Extend: `backend/benchmark_alpha/types.py`
- Test: `backend/tests/test_alpha_costs.py`
- Test: `backend/tests/test_alpha_shadow.py`
- Test: `backend/tests/test_alpha_research_policy.py`

**Interfaces:**

- `EquityCostModel.estimate(intent, decision_quote, submit_quote, liquidity) -> CostEstimate`
  separately reports half-spread, decision latency, price drift, market impact, regulatory
  fees, missed trade, and fixed operating cost.
- `ImplementationShortfall.observe(intent, fills) -> ShortfallRecord` uses contemporaneous
  tradable bid/ask and never labels fill-versus-stale-bar drift as broker slippage.
- `ShadowPortfolio.apply(target, quotes, cost_model, now) -> ShadowCycle` maintains virtual
  cash, positions, fills, reservations, rejected orders, and portfolio equity without a
  broker submission method.
- `ResearchPortfolioPolicy.build(forecasts, active_cap, constraints, cost_model,
  research_tax_state, as_of) -> ResearchTarget` supplies the non-trading 40/60/80
  portfolio path required by Task 16 before production Tasks 12-13 exist. It enforces 2%
  cash, SPY residual, ten names, 8% name, 20% sector, horizon expiry, turnover, and a
  conservative wash-sale/tax opportunity-cost proxy.
- The research policy has no broker, WAL, runtime, or order-intent dependency. Production
  Tasks 12-13 must later match its golden target fixtures while adding live
  reconciliation and enforcement.
- Cost model versions are immutable and calibrated only from prior fills. Realized
  shortfall drift beyond its registered bound automatically demotes live authority.

- [ ] **Step 1:** Write a zero-alpha, high-turnover fixture that loses money after spread,
  slippage, fees, and fixed cost.
- [ ] **Step 2:** Add quote-to-fill tests for partial fills, rejected orders,
  cancel/replace, market-order drift, marketable-limit non-fill, and missing NBBO.
- [ ] **Step 3:** Encode the July 18 diagnostic as a non-promotional fixture: 28 matched
  decisions, 6.5-minute median decision-to-fill delay, and an approximately 32.1 bps
  unfavorable fill-versus-reference proxy. Assert it is labeled `reference_proxy`, not
  measured NBBO slippage.
- [ ] **Step 4:** Implement the virtual portfolio against the typed target, reservation,
  tax-state, and risk interfaces that PAPER/LIVE will later consume. It uses Task 9A's
  research policy/proxy now and has no production Task 12-15 or trading-client dependency.
- [ ] **Step 4a:** Implement `ResearchPortfolioPolicy` and a research tax proxy. Add
  40/60/80 golden targets, insufficient-signal SPY residual, all-symbol wash-sale cost,
  and tier turnover-cap tests. Prove it cannot create a broker intent.
- [ ] **Step 5:** Report LLM/data/infrastructure costs separately and amortized at current
  capital. The observed approximately $80.73 of attributed LLM usage is not silently
  omitted, even though most calls appear related to lookback/backtest activity.
- [ ] **Step 6:** Run `pytest tests/test_alpha_costs.py tests/test_alpha_shadow.py tests/test_alpha_research_policy.py -v`;
  expect PASS. Run change detection; commit with
  `feat(alpha): add costed quote-driven shadow portfolio`.

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

- `ForecastCalibrator.fit(rows, evidence_class, horizon_days) -> CalibratedModel` uses
  date-grouped cross-fitting, conservative shrinkage, and at least 100 resolved
  observations with both positive and negative classes.
- Probability calibration and conditional excess-return estimation are separate artifacts;
  one isotonic curve does not produce both quantities.
- `CalibratedModel.predict(raw_score) -> (expected_excess_return,
  probability_outperform, uncertainty)`.
- `graph_forecasts(scores, metadata, calibrators, run_context) -> list[Forecast]` emits horizons 1, 3, 5.
- Missing calibration produces `eligible=False` with gate reason `uncalibrated`; it never maps a raw score directly to tradable expected return.
- Only `EvidenceClass.DIRECT` with `forecast.as_of == current_run.as_of` is eligible for
  the initial PAPER/LIVE challenger. Propagation, backfill, scheduled votes, breakout,
  reserved slots, and other legacy override lanes are rejected before allocation.

- [ ] **Step 1:** Add calibration tests with 120 deterministic samples and assertions that predicted probability is monotonic and bounded 0-1.
- [ ] **Step 2:** Add adapter tests for direct, propagation, no-graph-signal,
  failed-quality, negative-trend, missing-price, disabled-ML, unknown-reason, queued-score
  grace, scheduled-vote override, breakout override, and high-conviction bypass inputs.
- [ ] **Step 2a:** Add sanitized regression fixtures for BDC, COHR, BV, and MRNA. Assert
  every contradictory record produces an auditable rejection and no `OrderIntent`.
- [ ] **Step 3:** Run tests; expect module failures.
- [ ] **Step 4:** Implement calibrator serialization with training-window endpoints, sample count, feature version, and model hash. Never pickle untrusted payloads; store JSON thresholds and isotonic breakpoints.
- [ ] **Step 5:** Implement the adapter against existing `scores`,
  `_nexus_action_intents`, position-size metadata, and typed evidence fields. Reason text
  is explanatory only and cannot create eligibility. Every eligible forecast references
  the exact current-cycle evidence snapshot; an old queue score may preserve research
  priority but never trade eligibility. If one required field is absent, emit an audited
  ineligible forecast rather than guessing.
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
- [ ] **Step 4a:** Register the July 18 findings as hypotheses, not thresholds:
  fresh initial buys may decay after three sessions, while backfill candidates may have
  negative incremental value. Do not tune the challenger on the 26-entry live sample.
- [ ] **Step 5:** Run tests; expect PASS. Run change detection; commit with `feat(alpha): add deterministic benchmark-relative challenger`.

**Stage B STOP/GO:** Execute Task 16 immediately after Tasks 10-11, despite its retained
number below. Do not begin Tasks 12-15 unless fresh-direct LIVE_40 passes the predeclared
unseen net-of-cost Stage B gate. Deterministic success alone requires a new reviewed
strategy decision.

> **STAGE B STATUS 2026-07-18 (commits 058764e..04cb279, pushed):** Tasks 7,
> 7A, 8, 9, 9A, 10, 11, and 16 IMPLEMENTED with TDD (~110 Stage B tests; full
> alpha sweep 317 passed). The STOP/GO gate (`evaluate_stage_b_go`) and the
> 27-experiment registered matrix EXIST and are enforced in code, but the
> gate has NOT been evaluated — running it requires the frozen point-in-time
> data manifest/pipeline (operator-supplied historical bars/graph snapshots)
> plus the registered research execution, which is the next unit of work.
> Tasks 12-15 remain unbuilt per the gate contract. Deferred within Stage B
> (recorded per task): Task 8 Step 5a `_log_live_trade_decision` hard-write
> (legacy live submissions are already fully blocked by the Task 6
> containment gate; the alpha path's decisions are hard-durability
> GateRecords wired in Task 15), Task 8's scheduled retention job (policy
> declared in migrate_alpha_tables.RETENTION_DAYS; M03), Task 9's
> BrokerHistoryIngestor live pagination wiring (pure idempotent ingestion +
> ledger reconcile implemented; broker paging lands with Task 18 ops), and
> intraday_reporting=continuous monitoring request wiring. detect_changes on
> Task 8 flagged HIGH via hunk-adjacency on api_widget_accounts — verified
> zero deletions in api/main.py (purely additive endpoints); all widget
> flows' tests pass.

### Task 12: FIFO Lots, All-Symbol Wash-Sale Windows, and SPY Rebalancing

**Files:**

- Create: `backend/benchmark_alpha/tax.py`
- Test: `backend/tests/test_alpha_tax.py`

**Interfaces:**

- `TaxLot(lot_id, symbol, acquired_at, quantity, unit_basis, remaining_quantity)`.
- `consume_fifo(lots, quantity, sale_price, sold_at) -> LotConsumption`.
- `WashSaleGuard(lots=(), acquisitions=(), loss_sales=(), external_blackouts=())` owns
  one reconciled immutable tax-state snapshot.
- `WashSaleGuard.evaluate_sale(symbol, quantity, sale_price, sold_at,
  emergency=False) -> TaxDecision`.
- `WashSaleGuard.evaluate_buy(symbol, quantity, buy_price, bought_at,
  emergency=False) -> TaxDecision`.
- `should_rebalance_spy(last_rebalance_at, current_weight, target_weight, now) -> bool` is true after seven calendar days or absolute drift of at least 0.05.
- Emergency intents carry `risk_override=True` and bypass tax blocks while preserving the warning/audit record.

- [ ] **Step 1: Write failing boundary tests for days 30 and 31.**

```python
from datetime import datetime, timedelta, timezone
from benchmark_alpha.tax import WashSaleGuard


def test_spy_repurchase_block_ends_after_day_30():
    sold = datetime(2026, 7, 1, tzinfo=timezone.utc)
    guard = WashSaleGuard(loss_sales=[sold])
    assert guard.evaluate_buy(
        "SPY", quantity=1, buy_price=700,
        bought_at=sold + timedelta(days=30)
    ).allowed is False
    assert guard.evaluate_buy(
        "SPY", quantity=1, buy_price=700,
        bought_at=sold + timedelta(days=31)
    ).allowed is True
```

- [ ] **Step 2:** Add FIFO partial-lot tests for SPY and an active symbol,
  prior-30-day-buy loss-sale blocking, gain-sale allowance, partial replacement-share
  matching, adjusted basis/holding period, 5pp SPY drift, weekly SPY batching, external
  blackout dates, and emergency override.
- [ ] **Step 3:** Run tests; expect module failure.
- [ ] **Step 4:** Implement decimal-safe quantities/prices using `Decimal(str(value))`; return realized gain/loss and affected lot IDs without claiming broker tax-lot selection.
- [ ] **Step 5:** Persist all-symbol lot events through `AlphaRethinkStore`; reconcile
  quantities to broker positions daily and freeze affected-symbol discretionary orders on
  mismatch or RethinkDB unavailability. Emergency risk reductions remain allowed and are
  backfilled from broker history when storage recovers.
- [ ] **Step 5a:** Record estimated tax opportunity cost for each blocked or overridden
  action. Never claim visibility into other accounts; accept operator blackout dates.
- [ ] **Step 6:** Run tests; expect PASS. Run change detection; commit with `feat(alpha): add FIFO SPY wash-sale controls`.

### Task 13: Constrained Benchmark-Replacement Allocator

**Files:**

- Create: `backend/benchmark_alpha/allocator.py`
- Test: `backend/tests/test_alpha_allocator.py`

**Interfaces:**

- Immutable `AllocatorConfig(cash_weight=.02, absolute_active_ceiling=.80,
  max_positions=10, max_name_weight=.08, max_sector_weight=.20, beta_min=.8,
  beta_max=1.1)`. There is no enforceable `active_floor` or unscoped promoted target.
- `build_allocation(forecasts, current_weights, pending_orders, sectors, betas,
  volatilities, covariance, liquidity, mark_quality, risk_limits, tax_state,
  cost_model, authorization, as_of) -> AllocationPlan`.
- Select only eligible forecasts whose expected excess return exceeds modeled spread, slippage, tax cost, and an anti-churn hurdle.
- Weight priority is conservative expected-excess-return divided by risk, with deterministic
  symbol tie-breaking. Clip stock weights to 8%, sector totals to 20%, and active total to
  `min(config.absolute_active_ceiling, authorization.active_cap,
  risk_limits.active_cap)`. Filter evidence through `authorization.evidence_allowlist`.
  Positions may be below 4% when risk, cost, liquidity, or legacy migration requires it.
- Set `SPY = 1 - cash_weight - sum(active_weights)`; if too few candidates exist, keep the residual in SPY rather than forcing the 40% floor.
- A held stock whose selected forecast has reached `as_of + horizon_trading_days` receives target weight zero unless a newly created, independently eligible forecast renews it. No minimum-hold key can override expiry or a risk exit.

- [ ] **Step 1:** Write tests for no signals (98% SPY/2% cash), five strong signals at
  LIVE_40 (40% active/58% SPY/2% cash), ten equal strong signals only after LIVE_80
  promotion (80% active/18% SPY/2% cash), one sector overflow, one 20% proposed name
  clipped to 8%, risk SOFT ceiling 40%, expired three-day forecast exiting to SPY, and a
  newly timestamped eligible forecast renewing the position.
- [ ] **Step 1a:** Add missing/expired authorization failures. With the same ten strong
  forecasts, LIVE_40 must cap active weight at 0.40, LIVE_60 at 0.60, and LIVE_80 at 0.80;
  no runtime mode or NORMAL risk state can raise that cap.
- [ ] **Step 2:** Add property tests over deterministic random inputs asserting weights sum to 1 within `1e-9`, no negative weights, no more than ten stocks, name/sector/gross caps, and repeat-call equality.
- [ ] **Step 3:** Run tests; expect module failure.
- [ ] **Step 4:** Implement selection and iterative cap redistribution. If beta remains outside 0.8-1.1 after clipping, move active weight back to SPY; never use leverage to repair beta.
- [ ] **Step 5:** Include `reason_codes`, rejected candidate IDs, forecast IDs, expected turnover, and constraint utilization in `AllocationPlan`.
- [ ] **Step 5a:** Require a cross-sectional replacement hurdle: incoming conservative
  excess return must beat the incumbent after spread, latency, tax, uncertainty, and
  anti-churn cost. Enforce the tier-specific same-session gross-turnover cap and record
  any emergency bypass.
- [ ] **Step 5b:** Add a migration fixture with the July 18 seven-position portfolio.
  Assert all legacy weights count toward exposure, no duplicate symbol is bought, and
  cap correction follows the operator migration manifest rather than blind liquidation.
- [ ] **Step 6:** Run tests; expect PASS. Run change detection; commit with `feat(alpha): add constrained SPY replacement allocator`.

### Task 14: Position Stops, Drawdown Limits, and Persisted Execution State Machine

**Files:**

- Extend: `backend/benchmark_alpha/risk.py`
- Create: `backend/benchmark_alpha/execution.py`
- Modify: `backend/broker_adapters/_wal.py`
- Test: `backend/tests/test_alpha_execution.py`

**Interfaces:**

- `stop_distance_pct(atr_pct) -> float` clamps `1.5 * atr_pct` to 0.05-0.08.
- `max_position_weight_for_loss_budget(stop_pct, loss_budget=.006) -> float` cannot exceed 0.08.
- `risk_limits(state, authorization) -> RiskLimits`: NORMAL active cap is the authorized
  tier cap, SOFT is `min(authorization.active_cap, .40)`, HARD is 0 with staged
  reductions, and KILL targets cash 1.0.
- `ExecutionBatchMachine.reconcile_and_advance(batch_id, plan, broker_snapshot, marks,
  tax_guard, risk_state, authorization, now) -> BatchResult` advances one persisted phase
  at a time and revalidates the current tier before every increase.
- Phases are reconcile, reserve, reductions, wait/expire, recompute, affordable increases,
  and residual reconciliation. A new allocation cannot overwrite an unsettled target.
- Every intent has `run_id`, `allocation_id`, `decision_id`, `forecast_id`, `intent_id`,
  `intent_revision`, `reason_codes`, decision/submit mark IDs, `risk_override`, and stable
  client-order material.
- Exposure-increasing intents require fresh marks and complete constraints. Risk-reducing intents may use a broker fallback mark and always record degraded quality.
- Buy availability uses cached/settled cash plus only sell quantity confirmed filled through WAL. A merely submitted or partially unfilled sell contributes no projected proceeds.
- Open buys reserve cash. Pending buys and sells count toward name, sector, active, gross,
  and position-count limits until terminal reconciliation.
- Client IDs derive from stable intent ID plus revision. Same-intent retries reuse an ID;
  terminal-negative successors, revised targets, and residual quantities use explicit
  successor revisions.
- Fill handling is sequence-aware and delta-idempotent. Cumulative partial-fill events
  update only the positive quantity delta; partial-then-canceled orders remain in position,
  cash, and restart state.
- Normal submission requires a successful hard-durability `LiveOrderWAL` write in RethinkDB.
  If RethinkDB is unavailable, all increases remain blocked. A risk reduction may use
  `submit_degraded_reduction(instance_id, risk_episode_id, symbol, held_qty, target_qty)`,
  which re-reads the broker position, caps the sell to current held quantity, and uses a
  deterministic client-order ID. On recovery or restart, recent broker orders/fills with
  the instance prefix are compared with RethinkDB and missing WAL/events are backfilled.

- [ ] **Step 1:** Add tests for ATR clamps, 0.6% loss budget, phased sells-before-buys, one
  net SPY order, stale-buy rejection, stale-risk-sell allowance, reserved open-buy cash,
  partial-fill-safe available cash, KILL liquidation to cash, RethinkDB-down buy
  rejection, quantity-capped degraded risk reduction, and broker-history WAL backfill.
- [ ] **Step 1a:** Add crash injection after intent persistence, broker acceptance, 37%
  partial fill, cancel request, cancel acknowledgement, and final fill. On every restart,
  adopt the existing broker order and never exceed the persisted target or available cash.
- [ ] **Step 1b:** Add same-day emergency exit, add, partial sell, split, manual order,
  terminal rejection, and residual successor tests. Returning an old order with a
  different requested quantity is a reconciliation error, not success.
- [ ] **Step 1c:** Add cash-versus-margin capability, unsettled funds, day-trading
  restriction, fractional quantity, tick-size, and residual-notional tests. Unknown or
  stale broker capability data must fail an increase closed.
- [ ] **Step 2:** Run tests; expect failures.
- [ ] **Step 3:** Implement the persisted batch machine. Calculate current weights from
  broker quantities and fresh marks; use a configurable minimum notional of $1 and
  suppress deltas below both $1 and 0.25 percentage points. Build a cash ledger from
  current cash plus confirmed fill deltas, never expected sell proceeds.
- [ ] **Step 4:** Extend `WALRecord` and `record_intent` with optional alpha IDs/reasons while
  retaining backward compatibility for existing callers. Enforce hard durability and a
  bounded write deadline through the RethinkDB store.
- [ ] **Step 5:** Enforce one order authority per instance/mode. A byte-identical
  same-revision intent or client-order ID adopts the existing WAL/broker record and never
  submits twice; divergent content fails reconciliation. Implement the
  degraded risk-reduction path with deterministic IDs and startup/recovery scans of recent
  broker orders by instance prefix; broker evidence may backfill only risk-reducing rows.
- [ ] **Step 5a:** At decision and pre-submit, capture bid/ask, sizes, source, observed
  time, received time, age, spread, and halt state. Enforce a registered maximum
  decision-to-submit age and price-drift collar. Market versus marketable-limit policy is
  selected by the registered cost model; neither order type is assumed universally best.
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
- `AuthorizationProvider.current(instance_id, now) -> AuthorizationContext` is required
  for PAPER/LIVE allocation and immediately before every exposure-increasing submission.
  Missing or downgraded authorization cancels entries and recomputes against the lower cap.
- `CycleResult` contains forecasts, gates, allocation, intents, submissions, and health.
- Modes:
  - OFF: exact legacy behavior for backtests and non-contained instances. On contained
    `alpaca-main`, OFF is HALT/read-only and the broker submission trap remains active.
  - OBSERVE: write forecasts and gates only; all equity submission is disabled.
  - SHADOW_PORTFOLIO: build targets and costed virtual fills; all broker submission is
    disabled.
  - PAPER: alpha is sole order authority on a paper brokerage; legacy Graph orders are suppressed.
  - LIVE: alpha is sole order authority and requires a valid promotion record for the exact config/model/data hashes.
- MONITOR cycles update marks, stops, and drawdown only. FULL cycles refresh forecasts and allocate. Risk exits can occur on either cadence.
- `execution_mode` and `scheduler_tick_mode` are independent typed inputs. Each run event
  asserts that LIVE invariants remain active for FULL, MONITOR, and IDLE ticks.
- In PAPER/LIVE alpha modes, legacy `rotation_min_hold_days`, `sell_enforcement_min_hold_days`, and static partial-profit tiers have no order authority; horizon renewal, target weights, and alpha risk rules are authoritative.

- [ ] **Step 1:** Run impact analysis on `AlpacaAdapter.execute_signal`, the enclosing broker per-symbol execution function, and `_on_trade_update`. Warn before proceeding on HIGH/CRITICAL.
- [ ] **Step 2:** Add tests proving OFF is byte-for-byte compatible at the decision seam
  for backtests while contained live OFF submits nothing,
  OBSERVE and SHADOW_PORTFOLIO submit zero orders, the shadow virtual ledger still
  reconciles, PAPER suppresses legacy Graph orders, LIVE without promotion refuses
  startup, a risk exit works during MONITOR, and legacy minimum-hold/profit-tier settings
  cannot change an alpha-mode intent.
- [ ] **Step 2a:** Reproduce the scheduler collision: pass LIVE execution with each
  `FULL`, `MONITOR`, and `IDLE` tick and assert live safeguards, stale-news rejection,
  propagation pruning, phantom-sell suppression, and persistent forced-exit state remain
  enabled.
- [ ] **Step 3:** Add a RethinkDB-outage test: exposure increases stop, open entries are
  canceled, broker-backed risk evaluation continues in memory, a quantity-capped emergency
  reduction uses a deterministic client-order ID, and recovery backfills that broker event.
  Assert that non-broker analytics from a simultaneous database/process failure are marked
  as an unrecoverable degraded audit interval rather than reported complete.
- [ ] **Step 4:** Run tests; expect runtime missing.
- [ ] **Step 5:** Implement `AlphaRuntime` using dependency injection for the RethinkDB
  store, authorization provider, calibrators, allocator, execution batch machine, and
  broker adapter. For normal operation, write
  each event with hard durability before the next side effect. The only exception is the
  Task 14 deterministic, quantity-capped risk-reduction path during a declared store outage.
- [ ] **Step 6:** Add one broker seam after run-once results are available and before
  legacy per-symbol submission. Do not duplicate strategy evaluation. OFF takes the old
  path only outside contained live operation; contained live OFF, OBSERVE, and
  SHADOW_PORTFOLIO hit an unconditional broker-submit trap. PAPER/LIVE take the alpha
  path. A separately named `LEGACY_COMPARE` research origin may record the legacy
  counterfactual but may not trade on the contained instance.
- [ ] **Step 7:** Attach allocation/intent IDs to the RethinkDB WAL. On Alpaca fill/reject
  callbacks, write sequence-aware delta Fill/OrderEffect events directly. If the store is unavailable,
  retain a bounded in-memory copy and recover the authoritative order/fill from Alpaca by
  client-order prefix after reconnect or restart; never claim the in-memory copy is durable.
- [ ] **Step 8:** Extend LiveState with alpha mode, run ID, last full cycle, last monitor
  cycle, audit-store health, last successful store write/read, active/SPY/cash weights,
  constraint utilization, mark health, risk level, degraded-audit interval, and promotion tier.
- [ ] **Step 8a:** Add current target, pending buy/sell reservations, filled/residual
  quantity, current execution phase, run resume point, live-mode invariant status, and
  strategy/dashboard/external attribution counts.
- [ ] **Step 9:** Run `pytest tests/test_alpha_runtime.py tests/test_alpha_execution.py tests/test_nexus_monitor_cycle.py tests/test_broker_pause_resume_cycle.py -v`; expect PASS.
- [ ] **Step 10:** Run change detection and inspect every affected execution flow. Commit with `feat(alpha): integrate shadow and gated portfolio runtime`.

**Phase 3 verification gate:** run OFF, OBSERVE, and SHADOW_PORTFOLIO against the same
deterministic backtest fixture. OFF must reproduce legacy orders there; the other modes
must produce no broker order while SHADOW_PORTFOLIO emits a fully reconciled virtual
forecast-to-fill chain. Repeat OFF with contained `alpaca-main` identity and require zero
submissions. PAPER may start only after all results pass.

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
- Runner refuses mutable/current news data, unfrozen LLM output, insufficient
  power/regime coverage, or a reused sealed holdout.
- Alpha promotion ignores `_validate_result_llm`; the LLM may summarize results but cannot KEEP/TOSS them.
- A `DataManifest` content-hashes point-in-time bars, universes including delisted/renamed
  symbols, news, Graph edges, event scores, fundamentals, sector data, model outputs, and
  their `known_at` provenance. An unprovable historical feature is excluded.
- `StageBGoReport` contains fresh-direct LIVE_40 research-policy results only.
  `evaluate_stage_b_go(report) -> StageBGoDecision` requires median annual active >=8pp,
  target active >=10pp, 90% bootstrap lower bound >0, information ratio >=0.75,
  Deflated Sharpe probability >=0.95, `0 <= max_drawdown_magnitude <=0.15`, profit
  factor >1 after all costs, positive active return in >=60% unseen quarters, and adequate
  multi-regime power. It grants permission only to build Tasks 12-15, never to trade.

- [ ] **Step 1:** Write split tests proving every test date follows training/calibration, the five-day embargo has no overlap, and the same inputs produce the same experiment ID.
- [ ] **Step 2:** Add registry tests proving failed experiments remain stored and duplicate specs return the original ID rather than creating another trial.
- [ ] **Step 3:** Add rejection tests for insufficient regime/power coverage, an unfrozen
  model-output provider, unprovable point-in-time feature availability, current-universe
  survivorship, and a second final-holdout evaluation.
- [ ] **Step 4:** Run tests; expect module failure.
- [ ] **Step 5:** Implement split generation with calendar-month boundaries, explicit UTC dates, and frozen manifest hashes. Record all parameter combinations before running any result.
- [ ] **Step 6:** Build the exact matrix: SPY control; deterministic challenger; Graph
  fresh direct; fresh direct plus backfill; fresh direct plus propagation; and each legacy
  override lane separately. Test horizons 1/3/5 and active ceilings 40/60/80. Nested folds
  select horizon/ceiling; the final holdout compares registered model families once.
- [ ] **Step 6a:** Generate every portfolio result with Task 9A
  `ResearchPortfolioPolicy`, its conservative all-symbol tax proxy, and the registered
  cost model. No production allocator, tax guard, runtime, or broker order is required.
- [ ] **Step 6b:** Report payoff ratio, profit factor, win/loss size, turnover, holding
  period, implementation shortfall, tax blocks, fixed cost, and active return by evidence
  class. Include the legacy 0.7993 total and 0.6686 strategy-owned profit factors only as
  historical baselines.
- [ ] **Step 6c:** Evaluate the predeclared Stage B gate on fresh-direct LIVE_40 only.
  Add a test where the deterministic challenger passes but fresh direct fails; the result
  must be NO-GO.
- [ ] **Step 7:** Route alpha-mode AI backtests through the registry and objective metrics. Preserve legacy AI behavior for non-alpha experiments, but label its result `promotion_eligible=False`.
- [ ] **Step 8:** Run `pytest tests/test_alpha_research.py tests/test_nexus_dual_cadence_backtest_harness.py -v`; expect PASS.
- [ ] **Step 9:** Run impact analysis on `_validate_result_llm` and the experiment launch function; run change detection; commit with `feat(alpha): add registered purged walk-forward research`.

### Task 17: Statistical and Operational Promotion Gate

**Files:**

- Create: `backend/benchmark_alpha/promotion.py`
- Create: `backend/scripts/verify_alpha_readiness.py`
- Extend: `backend/benchmark_alpha/metrics.py`
- Modify: `backend/benchmark_alpha/runtime.py`
- Test: `backend/tests/test_alpha_promotion.py`
- Test: `backend/tests/test_alpha_runtime.py`

**Interfaces:**

- Uses `PromotionTier` and `AuthorizationContext` defined in Task 5.
- `PromotionReport` contains every metric, sample count, incident count, market-data quality,
  RethinkDB health, reconciliation status, degraded-audit intervals,
  config/model/data hashes, evidence classes, and pass/fail reasons.
- `evaluate_promotion(report, requested_tier) -> PromotionDecision` enforces cumulative
  gates and, on an operator-approved pass, creates an `AuthorizationContext` with the exact
  0.40/0.60/0.80 active cap and evidence allowlist. LIVE_40 requires fresh-direct
  evidence; deterministic-only success is insufficient.
- Statistical gates: median annual active >=8pp, target active >=10pp, 90% bootstrap lower
  bound >0, information ratio >=0.75, Deflated Sharpe probability >=0.95,
  `max_drawdown_magnitude <=0.15`, beta 0.8-1.1, and positive active return in >=60%
  unseen quarters.
- Sample/time gates: SHADOW_PORTFOLIO >=4 weeks and 100 qualified forecasts; PAPER >=6
  weeks and 50 completed positions; LIVE_60 >=8 incident-free live weeks; LIVE_80 >=6
  live months and 150 completed positions.
- LIVE_80 additionally requires SIP-quality consolidated data.
- Promotion observations are unseen daily portfolio returns with date-clustered/HAC-aware
  uncertainty, not raw overlapping forecasts. Paper performance is operational evidence,
  not proof of live alpha.

- [ ] **Step 1:** Write one passing LIVE_40 report and table-driven single-field failures
  for every statistical and operational threshold, including Deflated Sharpe at 0.9499
  versus 0.95 and drawdown magnitude at 0.1501 versus 0.15. Reject a negative value as a
  schema error so `-0.20` can never pass by sign.
- [ ] **Step 2:** Add tests proving propagation stays disabled when the
  direct+propagation ablation does not beat direct-only after costs, deterministic-only
  success cannot authorize LIVE_40, tier decisions emit exact active caps, and no
  elapsed-time gate can override a failed metric.
- [ ] **Step 3:** Add tests for config/model/data hash mismatch, RethinkDB unavailability,
  missing broker-event backfill, degraded-audit intervals, stale marks, decision/order
  mismatch, secrets-canary failure, and IEX-only LIVE_80 rejection.
- [ ] **Step 3a:** Add failures for profit factor <=1 after costs, excessive registered
  turnover, unresolved order ownership, missing decision lineage, execution-shortfall
  drift, live-mode invariant failure, outcome split artifact, and selected start-date
  reporting.
- [ ] **Step 4:** Run tests; expect module failure.
- [ ] **Step 5:** Implement pure promotion evaluation with complete reason lists. Promotion
  records are append-only, tier-specific, and signed by authenticated operator identity.
  They bind training-manifest, model, source/dependency/image, runtime-config, cost-model,
  and provider-capability hashes. Ordinary per-cycle input snapshots are audited without
  invalidating the artifact. Continuous operational failure automatically demotes but
  never self-promotes.
- [ ] **Step 5a:** Implement the runtime `AuthorizationProvider` over append-only
  RethinkDB promotion/demotion records. Re-evaluate it at cycle start and immediately
  before every increase. On expiry, artifact mismatch, health failure, or demotion, cancel
  entries and recompute to the lower authorized cap before any further increase.
- [ ] **Step 6:** Implement readiness CLI output as JSON plus a human table; it performs read-only checks and exits 0 only when the requested tier passes.
- [ ] **Step 7:** Run `pytest tests/test_alpha_promotion.py tests/test_alpha_runtime.py tests/test_alpha_metrics.py tests/test_alpha_rethink_store.py -v`; expect PASS.
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
  tests/test_alpha_risk.py \
  tests/test_alpha_watchdog.py \
  tests/test_alpha_emergency.py \
  tests/test_alpha_rethink_store.py \
  tests/test_alpha_api.py \
  tests/test_alpha_benchmark.py \
  tests/test_alpha_metrics.py \
  tests/test_alpha_outcomes.py \
  tests/test_alpha_costs.py \
  tests/test_alpha_shadow.py \
  tests/test_alpha_research_policy.py \
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
- [ ] Start SHADOW_PORTFOLIO against the real data feed with live order submission disabled at both
  runtime and broker preflight. Confirm 100% RethinkDB
  prediction/gate/allocation/intent/virtual-fill persistence and zero alpha client-order
  IDs at Alpaca.
- [ ] Reconcile every full shadow day: every qualified forecast has gates and an
  allocation outcome; every target delta has either an intent or a rejection reason; mark
  ages stay within SLA during market hours. No selected-day sampling is permitted.
- [ ] Run PAPER on an Alpaca paper account. Exercise partial fills, a rejected order, stream disconnect, RethinkDB outage, process restart, manual order contamination, HARD drawdown, and KILL drawdown using controlled fixtures.
- [ ] Terminate the main broker process during a PAPER KILL fixture. Verify the independent
  reduce-only executor cancels entries, re-reads positions, reduces no more than held
  quantity, and reconciles every broker order/fill after restart.
- [ ] Before PAPER/LIVE order authority, apply the account migration manifest. Reconcile
  every legacy position, open order, fill, weighted economic basis, FIFO tax lot, and
  owner source. No symbol may be omitted, duplicated, or silently adopted.
- [ ] Run the registered walk-forward matrix. The readiness command must reject promotion unless every Task 17 gate passes.
- [ ] After at least four SHADOW_PORTFOLIO weeks and six PAPER weeks, request LIVE_40
  with `verify_alpha_readiness.py --tier live_40`. An authenticated operator reviews and
  records the promotion; the software never self-promotes.
- [ ] Start LIVE_40 with active exposure capped at 40%; actual active weight may be lower.
  Confirm 2% cash target, SPY residual, <=10 names, <=8% each, <=20% sector, no margin,
  tax decision for every affected symbol, and a current persistent high-water mark.
- [ ] Confirm the fresh-direct-only allowlist in LIVE_40. Attempted backfill,
  scheduled-vote, breakout, propagation, reserved-slot, and high-conviction orders must
  produce typed rejections and zero broker submissions.
- [ ] LIVE_60 and LIVE_80 remain unavailable until their time, sample, performance, safety, and market-data gates independently pass.

---

## Execution Order

```text
Stage A - Contain and repair production truth
0 -> 1 -> 2 -> 3 -> 4 -> 5 -> 6

Stage B - Establish whether trustworthy alpha evidence is possible
7 -> 7A -> 8 -> 9 -> 9A -> 10 -> 11 -> 16

STOP/GO GATE
If fresh-direct LIVE_40 fails the predeclared unseen net-of-cost Stage B gate, stop.
Retain the safety/accounting fixes and run the operator-approved SPY/cash portfolio.
Deterministic success alone does not authorize Stage C. Do not build or market the legacy
Graph path as alpha.

Stage C - Build portfolio and execution only after GO
12 -> 13 -> 14 -> 15

Stage D - Prove operations and scale
17 -> 18 -> LIVE_40 -> LIVE_60 -> LIVE_80
```

Task 9 may proceed in parallel with Task 7A after typed records exist. Tasks 10 and 11 may
run in parallel after outcomes and the cost model exist. Tasks 12 and 13 may be developed
in parallel only after the Stage B GO decision, but Task 14 consumes both. All remaining
tasks are sequential because they share promotion and order-authority state.

## Plan Self-Review

- **Spec coverage:** containment/security are Tasks 0-2; streaming/REST marks, scheduler
  mode separation, an independent watchdog, and flow-adjusted drawdown are Tasks 3-6;
  immutable ledgers, unbiased outcomes, and indexed APIs are Tasks 5, 7, 7A, and 8;
  benchmark truth and the costed shadow portfolio are Tasks 9 and 9A;
  fresh-direct/deterministic forecasts are Tasks 10-11; all-symbol wash-sale behavior is
  Task 12; 40-80% benchmark replacement and horizon expiry are Task 13; crash-safe
  execution is Task 14; runtime modes are Task 15; registered validation is Task 16;
  promotion thresholds and 40/60/80 rollout are Tasks 17-18.
- **Boundary check:** broker adapters own broker state and market marks; forecast producers own evidence only; allocator owns target weights; execution owns orders; research cannot submit; promotion cannot alter metrics. No allocator logic is added to Graph Nexus or `broker.py`.
- **Type consistency:** `RunOrigin`, `ExecutionMode`, `EventKind`, typed record IDs, `RiskState`, `AllocationPlan`, and `OrderIntent` are defined before consumers and retain identical names throughout.
- **Legacy safety:** OFF preserves the old path for backtests and non-contained instances,
  but contained `alpaca-main` OFF is HALT/read-only. OBSERVE and SHADOW_PORTFOLIO submit
  nothing. PAPER/LIVE establish one alpha order authority and suppress every legacy Graph
  submission lane.
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

### Finding A4 - A RethinkDB outage can create an audit gap during emergency reduction

RethinkDB is the sole persistence database, so the plan cannot promise complete event
durability while it is unreachable. Tasks 5, 6, 14, and 15 block increases, cancel entries,
continue broker-backed risk monitoring in memory, and allow only deterministic,
quantity-capped risk reductions. Broker orders/fills are backfilled after recovery. A
simultaneous database/process failure may lose non-broker analytics; the interval is marked
degraded and blocks promotion until reconciliation is complete.

### Finding A5 - Shadow evaluation can leak future news or revised graph data

Replaying current Neo4j/news state against old prices would manufacture alpha. Task 16
rejects mutable/current sources, requires a content-hashed point-in-time data manifest,
freezes model outputs, applies purge/embargo, and permits one sealed-holdout evaluation
per model family. If adequate multi-regime, statistically powered point-in-time evidence
does not exist, the plan blocks promotion rather than filling the gap with synthetic
history.

### Finding A6 - Repeated matrix testing can still overfit the holdout

The experiment matrix contains many combinations. Task 16 registers them before results,
uses nested folds for horizon/ceiling selection, and records failures. Task 17 uses
bootstrap and multiple-testing-aware metrics; any config change invalidates the promotion
hash and cannot reuse a previous approval.

### Finding A7 - Graph score is not an expected return

Directly mapping a +2 score to a return would make allocation arbitrary. Task 10 blocks
uncalibrated forecasts and requires at least 100 resolved observations with both classes.
The current 26 entries cannot satisfy this gate.

### Finding A8 - Partial fills can double-spend SPY proceeds

The existing broker credits expected same-cycle sell proceeds before fills. Task 14 makes
the persisted execution batch reductions-first but caps buy availability to settled/cached
cash plus only the positive fill delta observed through WAL; open buys reserve cash and
pending orders count against exposure. Intent revisions and sequence-aware fill deltas
prevent retries from duplicating the remainder. Paper verification must exercise delayed
partial fills.

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

Task 17 binds promotions to training manifest, model, source/dependency/image, runtime
config, cost model, and provider-capability hashes. Ordinary live input snapshots remain
per-cycle audit records and do not invalidate an approved artifact. Any artifact mismatch
or expiry returns mode to the prior tier. The runtime, not the UI, enforces this check
before PAPER/LIVE order authority starts.

### Finding A14 - The plan could ship plumbing without proving alpha

Tasks 0-11 improve safety and measurement but do not establish outperformance. Task 16
is the mandatory STOP/GO gate before Tasks 12-15 are built. Tasks 17-18 are mandatory
promotion gates, not optional follow-up. A safe bot with failed active metrics stays in
SPY/SHADOW_PORTFOLIO mode and is not described as successful alpha.
