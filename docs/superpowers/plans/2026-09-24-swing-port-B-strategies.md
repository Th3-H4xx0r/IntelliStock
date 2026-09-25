# swing-trader port — Plan B: ported strategies, AI layer, approvals, API, reference data

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port ST's swing strategy, options wheel and AI conviction layer into `backend/swing_trader/` plus two run_once strategy wrappers (`strategy_swing`, `strategy_wheel`), with the six reference/record tables, the approval state machine (plan A-live Task 15 owns the broker hook that calls it), the API routes, the notification types, and the scripts that build reference data and the lab/paper documents.

**Architecture:** ST's pure functions are copied verbatim into `backend/swing_trader/` and pinned to the originals by parity tests against a vendored copy of the ST source under `backend/tests/fixtures/swing_trader_st/`. Only I/O changes: files become `db.store` tables, Pushover becomes `notifications.notify`, the Anthropic SDK becomes `llm_utils` driven by the model the operator links (`conviction_llm_model_id`). The two wrappers speak the run_once contract of plan A (decisions + `_nexus_position_sizes` bracket hints + `_nexus_option_orders`); the live scans are resumable state machines in `strategy_cache` that stay inside the broker's per-tick watchdog.

**Tech Stack:** Python 3 (pandas, numpy, pydantic v2, yfinance, alpaca-py 0.43.5, exchange_calendars), FastAPI, PostgreSQL 17 through `db.store` (FakeStore in tests), pytest.

**Spec:** `docs/superpowers/specs/2026-09-24-swing-trader-port-design.md` (this plan covers §4, §5, §7, §8, §9, §10 API + notifications, §11). Shared names: `docs/superpowers/plans/2026-09-24-swing-port-interfaces.md` (the contract). Source: github.com/tmasters2876/swing-trader at commit c2afa71 ("ST").

## Global Constraints

- EB on doc 200 (alpaca-main, real money) takes byte-identical code paths. **Plan B does not edit `backend/broker.py`.** The approval command handler the brief listed as B12 (`submit_order` with `{"source": "swing_approval", "signal_id"}`) is implemented by plan A-live Task 15 (`_execute_swing_approval`, routed only on that source); plan B supplies the two modules it calls (`swing_trader.approvals`, `swing_trader.signals_store`) and, in Task 20, the end-to-end test of that handshake.
- `broker.py` cannot be imported under pytest. Test broker code by AST-extracting the function (pattern: `backend/tests/test_manual_order_gate.py::_extract_execute_live_command`, `backend/tests/test_strategy_x_broker_coexistence.py::_DISPATCHER_NAMES`).
- A strategy wrapper is loaded in tests by file path with ONLY `backend/` on `sys.path`. Never add `backend/strategies/` to `sys.path` (it shadows `strategy_x`, `strategy_eb`, and friends).
- DB access only through `from db import store` (modules keep it as a module attribute so tests can monkeypatch it). DDL only in `backend/db/schema.py`.
- Run tests from the repo root: `python3 -m pytest backend/tests/<file> -q -p no:cacheprovider`.
- Suite baseline: 7,565 passed with EXACTLY 19 pre-existing failures. The target is no new failures.
- Before editing any existing symbol run `gitnexus_impact({target: "<symbol>", direction: "upstream"})` and report the blast radius; warn on HIGH/CRITICAL. If GitNexus says the index is stale, run `npx gitnexus analyze` first. Run `gitnexus_detect_changes()` before every commit.
- Commit bodies contain no backticks. Every commit message ends with exactly:
  ```
  Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_016ztXjLWXu61Dpp4XMU2jsS
  ```
- Stage files by explicit path. Never stage `AGENTS.md` or `CLAUDE.md`.
- `backend/strategies/swing.py` (strategy "Swing") is not touched and nothing is named `swing` at `backend/` or `backend/strategies/` level. The ported package is `backend/swing_trader/`.
- Strategy ids and classes: `strategy_swing` → `StrategySwing` in `backend/strategies/strategy_swing.py`; `strategy_wheel` → `StrategyWheel` in `backend/strategies/strategy_wheel.py`. Enable flags `strategy_swing_enabled`, `strategy_wheel_enabled`. Model key `conviction_llm_model_id` (resolver injects `conviction_llm_provider`, `conviction_llm_model`, `conviction_llm_api_key`).
- Swing header keys and values exactly as spec §5.1: `rsi_period=14`, `rsi_entry_max=50`, `rsi_overbought=70`, `sma_long=200`, `macd_fast/slow/signal=12/26/9`, `vol_avg_period=20`, `adx_period=14`, `adx_min=15`, `spy_buffer=1.03`, `vix_max=25.0`, `position_size_pct=0.125`, `max_positions=8`, `max_per_sector=1`, `profit_target=0.09`, `stop_loss=0.06`, `bear_regime_days=10`, `defensive_universe=[XLP, XLU, XLV, GLD, SHY]`, `earnings_hard_block_days=5`, `ai_gate_enabled=true`, `ai_approve_threshold=75`, `ai_review_threshold=50`, `conviction_llm_model_id=""`, `scan_time_et="09:15"`, `strategy_swing_enabled=false`, `live_max_order_fraction=0.2`, `live_max_symbol_fraction=0.2`, drawdown rungs, `honour_single_position_cap=true`, `broker_max_single_position_pct=0.2`.
- Wheel header keys and values exactly as spec §5.2: `rsi_min=30`, `rsi_max=60`, `sma_trend=50`, `atr_period=14`, `strike_atr_mult=0.5`, `min_premium_pct=0.005`, `target_delta=0.25`, `days_to_expiry=7`, `max_collateral_pct=0.25`, `max_per_sector=2`, `auto_covered_call=false`, `approve_threshold=75`, `review_threshold=50`, `earnings_block_days=7`, `limit_bid_mult=0.95`, `scan_weekday=0`, `scan_time_et="10:30"`, `monitor_time_et="15:45"`, `conviction_llm_model_id=""`, `strategy_wheel_enabled=false`.
- Notification types exactly: `swing_entry`, `swing_pending_review`, `swing_exit`, `swing_run_summary`, `wheel_put_placed`, `wheel_pending_review`, `wheel_position_alert`, `wheel_assignment`. Push on by default for pending reviews, entries, exits and position alerts.
- API routes exactly: `GET /instances/{instance_id}/swing/signals?status=`, `POST /instances/{instance_id}/swing/signals/{signal_id}/decision` with `{"decision", "reason"}`, `GET /instances/{instance_id}/wheel`, `GET /instances/{instance_id}/swing/calibration`. Auth required on all.
- Every module in `backend/swing_trader/` opens with a header naming the ST file and line range it came from. Pure functions are copied verbatim; the only edits allowed are I/O (files → `db.store`, Pushover → `notifications`, Anthropic SDK → `llm_utils`), constants lifted to keyword arguments with ST's value as the default, and the operator-approved fixes of spec §9 (each marked `fix N` at its line).
- Plan B owns every `backend/api/main.py` and `backend/interactive_utils.py` edit of the port (plan C touches no backend file). Plan A-backtest's Task 7 and plan A-live's Task 16 edit only the fingerprint tuple of `api/main.py`, in their own hunks.
- Live trading of either lane is a non-goal; everything targets the separate Alpaca paper account. No merge to `main` in this plan.

## Review Focus

1. **A symbol with a NaN close on the scan day** — its indicators fall back to the last completed bar (ST `get_latest_indicators` NaN guard); a symbol with no usable close is skipped; a NaN SPY close never reaches the regime line. Tests: Task 3 (`test_trailing_nan_close_falls_back_to_last_completed_bar`), Task 16 (`test_a_nan_close_on_the_last_bar_uses_the_previous_session`).
2. **VIX row missing for the day (holiday or data gap)** — the backtest regime uses the latest VIX row strictly before the NY trading date if it is at most 5 calendar days old; older than that the regime is blocked with a logged reason (never a silently stale VIX). Tests: Task 6 (`test_vix_before_skips_a_holiday_and_refuses_a_stale_gap`), Task 16 (`test_a_vix_gap_blocks_entries_and_logs_once`).
3. **LLM returns malformed JSON or a score outside 0–100** — the swing candidate is skipped (no `SwingSignals` row, no order, fix 5) and the scan moves to the next candidate; the wheel candidate is recorded as an `ai_rejected` row with score 0, as ST did. Tests: Task 13 (`test_out_of_range_score_raises`, `test_wheel_scoring_failure_is_a_reject_not_a_crash`), Task 17 (`test_an_ai_error_skips_only_that_candidate`).
4. **Approval clicked twice, or after the signal was already submitted** — the second decision gets HTTP 400 (the signal is no longer pending: `approvals.decide` raises `SignalConflict`, a `ValueError`, which `api/main.py:_run` maps to 400 — interfaces §9 item 2) and enqueues nothing; the executor refuses a signal that is not `approved`/`approved_half` and never builds a second intent. Two clicks at once: the one that loses the compare-and-swap gets 409 and enqueues nothing. Tests: Task 14 (`test_a_second_decision_conflicts`), Task 20 (`test_a_submitted_signal_is_never_submitted_twice`), Task 21 (`test_a_second_decision_is_a_400_and_enqueues_nothing`, `test_a_click_that_loses_the_race_is_a_409_and_queues_nothing`).
5. **Wheel scan re-run the same week after a crash mid-scan** — a same-session restart resumes from the persisted cursor without re-scoring (deterministic signal ids), the Tuesday fallback runs a fresh scan only when no completion marker exists for the week, and an underlying with an open short put or a working sell-to-open order is skipped (fix 1). Tests: Task 18 (`test_a_crashed_monday_scan_resumes_without_rescoring`, `test_tuesday_fallback_runs_only_without_a_completion_marker`, `test_an_underlying_with_an_open_put_is_skipped`).

## Contract additions

These names are new relative to `docs/superpowers/plans/2026-09-24-swing-port-interfaces.md`. Task 1 adds them to that file as section 11, "Additions from plan B" (section 9 is the UI-consumed shapes and section 10 is plan A-live's additions; if A-live has not landed yet, still number it 11), in the same commit as the tables, so plans A and C see them.

1. `backend/swing_trader/constants.py` exports `SWING_DEFAULTS` and `WHEEL_DEFAULTS` (dicts, header order). Plan A-live's `defaults_by_lane` keeps its own copy of the six `live_*` keys (`_SW_DEFAULTS`: 0.2 / 0.2 / 0.2 / 0.25 / 0.35 / 0.45; the wheel's row is `{}`), and `SWING_DEFAULTS` carries the same six values. The lane's document config, which `swing_lab_setup.py` writes from `SWING_DEFAULTS`, wins over that row at runtime.
2. Intent labels `swing_stop_exit` and `swing_target_exit`: ST's `exit_signal` also exits on a close ≤ −6% or ≥ +9% from the average entry, which a live bracket anchored on the PRIOR close can miss after a gapped fill. Plain strings like the others.
3. The live swing entry hint carries no share count: `buy_cash = max(min(equity × 0.125, buying power) × size_adjustment, prior close)`, and plan A-live's engine buys `floor(buy_cash / live price)` whole shares. That can be one share fewer than ST's close-based count; accepted. An approval's order does carry `qty` (`approvals.build_approved_order`, consumed by A-live Task 15). The swing lane keeps every exit it emitted in its strategy cache under `_swing_pending_exits` and re-emits it on each later tick while the stock is held with no working non-bracket sell (A-live contract addition 15: the engine never retries a deferred exit).
4. Option order dict key `"session": "YYYY-MM-DD"` (the NY session the lane decided in), informational: A-live keys every `us_option` SELL on the contract and the session itself (its contract addition 2) and ignores the key. The wheel lane reads A-live's `_engine_wheel_assignments` (its contract addition 13) from its own strategy cache to know which shares are wheel shares; only those are covered-call candidates.
5. `SwingSignals` documents may carry `score: None` (AI gate disabled — calibration excludes them), `context: dict` (earnings days, sector ETF, sector RSI, news), and `error: str` on a `failed` row the lane wrote. Plan A-live's approval handler and its option-order write-back set only `status` (`submitted` | `failed`) and `order_client_id`.
6. `backend/swing_trader/signals_store.py` also exports `signal_id_for(instance_id, lane, session, symbol) -> str` (deterministic uuid5 hex), `new_signal(**fields) -> dict`, `cas_signal(signal_id, *, expect_status, doc) -> bool`, `swing_owned_symbols(instance_id, held) -> set[str]`, `insert_wheel_scan(row) -> str`, `list_wheel_scans(instance_id, limit=50) -> list[dict]`, `all_signals(instance_id) -> list[dict]`, `ensure_tables() -> None`, `OPEN_STATUSES`.
7. `backend/swing_trader/approvals.py` exports `SignalConflict(ValueError)`, raised for a decision on a signal that is not pending; `api/main.py:_run` maps it to 400, as interfaces §9 item 2 pins. `backend/interactive_utils.py` exports `SwingDecisionRaceError(RuntimeError)` for a click that lost the compare-and-swap to a concurrent one (the route answers 409) and `SwingBrokerUnavailableError(RuntimeError)` for an approval that cannot reach the broker (instance not running, or the command not queued) and for an unreadable wheel book (503; the signal stays pending). An unknown signal id, or one belonging to another instance, raises `LookupError` → 404. A malformed body is FastAPI's 422.
8. `backend/swing_trader/account.py` exports the read-only book views both lanes share: `spendable`, `pending_symbols`, `entry_price_from_trades`, `equity_positions`, `option_symbols`, `live_equity`, `live_buying_power`, `option_positions`, `open_orders`, `account_options`.
9. `backend/swing_trader/notify.py` exports `send(category, instance_id, title, message, *, priority=0) -> None` and `notify_wheel_assignment(instance_id, *, symbol, qty, price=None, date=None) -> None`; plan A-live's activities poller calls the latter.
10. `backend/llm_utils.py` exports `call_llm_with_web_search(provider, api_key, model, prompt, *, max_output_tokens=300, max_uses=2, timeout_sec=None, provider_config=None) -> str`.
11. Consumed from plan A-live and not otherwise named in the contract: (a) the positions payload of `live_broker_fetch.fetch_broker_live_state` carries `option_type` for option positions (GET `/wheel` needs it; Task 21 falls back to the OCC type character when absent); (b) the broker REST-marks every symbol in `_nexus_executable_buys` and `_nexus_sell_enforcement` before the gate (swing names are not on the instance watchlist, so without a mark every entry dies on `dependency.quote.unknown`).
12. The API response shapes are the ones interfaces §9 ("UI-consumed shapes", pinned by plan C) defines; Task 21 pins them with route tests.

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `backend/db/schema.py` (modify) | Register the six tables | 1 |
| `backend/tests/dbcore/test_schema_ensure.py` (modify) | Table count 129 → 135 | 1 |
| `backend/tests/fixtures/swing_trader_st/paper_trader_pure.py` | Vendored ST paper_trader pure code (parity oracle) | 2 |
| `backend/tests/fixtures/swing_trader_st/wheel_trader_pure.py` | Vendored ST wheel_trader pure code | 2 |
| `backend/tests/fixtures/swing_trader_st/ai_analyst_pure.py` | Vendored ST ai_analyst pure code | 13 |
| `backend/swing_trader/__init__.py` | Package docstring | 2 |
| `backend/swing_trader/constants.py` | ST constants, `SWING_DEFAULTS`, `WHEEL_DEFAULTS` | 2 |
| `backend/swing_trader/indicators.py` | RSI, MACD, SMA, ADX, ATR, `get_latest_indicators` | 3 |
| `backend/swing_trader/signals.py` | `entry_signal`, `exit_signal`, `sector_conflict`, per-session bear counter | 4 |
| `backend/swing_trader/regime.py` | SPY × 1.03 / VIX ≤ 25 decision, live VIX fetch | 4 |
| `backend/swing_trader/clock.py` | NY session clock, NYSE calendar, per-tick budget | 5 |
| `backend/swing_trader/universe.py` | ST's S&P 500 list, wheel universe, live ordering | 6 |
| `backend/swing_trader/sectors.py` | `get_symbol_sector` over `SwingSectorMap` | 6 |
| `backend/swing_trader/refdata.py` | PIT readers: VIX, S&P membership, sector map | 6 |
| `backend/swing_trader/market_data.py` | Batched SIP bars, IEX latest trade, live price, engine-bar frames | 7 |
| `backend/swing_trader/wheel_rules.py` | Screening, expiry, delta pick, ladder, caps, dedupe, 2×, monitor, covered call | 8, 9, 10 |
| `backend/llm_utils.py` (modify) | `call_llm_with_web_search`; claude-cli `tools` argument | 11 |
| `backend/notification_types.py` (modify) | Eight types + push defaults | 12 |
| `backend/swing_trader/notify.py` | Category senders | 12 |
| `backend/swing_trader/ai_analyst.py` | ST prompts, structured scoring, checks | 13 |
| `backend/swing_trader/signals_store.py` | `SwingSignals` / `SwingWheelScans` access | 14 |
| `backend/swing_trader/approvals.py` | `decide`, `build_approved_order` | 14 |
| `backend/swing_trader/iv.py` | IV snapshots, IV rank | 15 |
| `backend/swing_trader/calibration.py` | Score buckets, outcome resolution | 15 |
| `backend/strategies/strategy_swing.py` | `StrategySwing` (backtest + live) | 16, 17 |
| `backend/strategies/strategy_wheel.py` | `StrategyWheel` (live only) | 18, 19 |
| `backend/swing_trader/account.py` | Read-only views of the book (positions, account, orders) | 16, 17, 18 |
| `backend/tests/test_swing_approval_roundtrip.py` | Plan A-live's approval handler against plan B's real modules | 20 |
| `backend/api/main.py` (modify) | Four routes | 21 |
| `backend/interactive_utils.py` (modify) | Four actions | 21 |
| `scripts/build_swing_reference_data.py` | VIX, membership, sectors | 22 |
| `scripts/swing_lab_setup.py` | Lab doc/instance; `--paper` doc/instance | 23 |
| `scripts/strategy_swing_sync_schema.py` | Headers ← DEFAULTS | 24 |
| (none) | Full-suite verification and the cross-plan hand-offs | 25 |

## Order and cross-plan dependencies

Tasks 1–15 and 22–24 depend on nothing outside this plan. Task 16 emits the plan A-backtest hints (`bracket`, `whole_shares`, `fill_at_next_open`) but its tests do not need A-backtest. Tasks 17–19 emit plan A-live payloads and call A-live adapter methods through fakes, so their tests pass without A-live, but live behaviour needs A-live merged. **Task 20 runs after plan A-live Task 15** (it exercises A-live's `_execute_swing_approval` through A-live's `backend/tests/swing_broker_harness.py`). **Task 21 runs after plan A-backtest Task 7**, which edits the `_CODE_FINGERPRINT_FILES` tuple in `backend/api/main.py`; Task 21 edits the same file in separate hunks (the import block and new routes). The deploy fingerprint lists are plan A-live Task 16's, which runs last and reconciles every file this plan adds. **Task 25** (full-suite verification) runs after plans A-live Tasks 1–15 and A-backtest have landed and before A-live Task 16, and hands that task the file list. Each task's Interfaces block says which A task it consumes.

---

### Task 1: Register the six swing tables (B1) and publish the contract additions

**Files:**
- Modify: `backend/db/schema.py` (the `ALL_TABLES` tuple ~line 107-124; the `_SPECS` list next to the Outlier entries ~line 277-280)
- Modify: `backend/tests/dbcore/test_schema_ensure.py:24-27`
- Modify: `docs/superpowers/plans/2026-09-24-swing-port-interfaces.md` (append a section)
- Test: `backend/tests/test_swing_tables.py`

**Interfaces:**
- Consumes: nothing.
- Produces: tables `SwingSignals` and `SwingWheelScans` (text `id`, generated column `instance_id`), `SwingIvSnapshots`, `SwingMacroDaily`, `SwingIndexMembership` (text `id`, prefix index on `id`), `SwingSectorMap` (text `id`, default template). Every later task reads and writes these through `db.store`.

- [ ] **Step 1: Impact analysis**

Run `gitnexus_impact({target: "backend/db/schema.py", direction: "upstream"})`. Adding registry entries changes no function; the blast radius to report is the two tests that pin the table list (`backend/tests/dbcore/test_schema_ensure.py`, `backend/tests/test_kalshi_store_pg.py` only asserts membership). Risk: LOW.

- [ ] **Step 2: Write the failing test**

Create `backend/tests/test_swing_tables.py`:

```python
"""The six swing-trader tables: registered, keyed the way the contract says,
and readable the way the lanes read them (spec §7)."""
import os
import sys

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from db import schema  # noqa: E402
from db.store import P  # noqa: E402

SWING_TABLES = ("SwingSignals", "SwingWheelScans", "SwingIvSnapshots",
                "SwingMacroDaily", "SwingIndexMembership", "SwingSectorMap")


def test_the_six_tables_are_registered_with_text_ids():
    for name in SWING_TABLES:
        assert name in schema.ALL_TABLES, name
        assert schema.spec(name).id_type == "text", name


def test_the_record_tables_index_instance_id():
    for name in ("SwingSignals", "SwingWheelScans"):
        assert schema.TABLES[name].indexed_fields == ("instance_id",)


def test_the_dated_series_are_prefix_scanned_on_id():
    for name in ("SwingIvSnapshots", "SwingMacroDaily", "SwingIndexMembership"):
        assert schema.TABLES[name].prefix_fields == ("id",)


def test_between_on_the_id_is_strictly_before_the_upper_date(store):
    """`VIX|<ny date>` as the OPEN upper bound is the whole point-in-time rule:
    the row dated the session itself carries a close from the future."""
    store.insert("SwingMacroDaily", [
        {"id": f"VIX|{d}", "series": "VIX", "date": d, "close": c, "source": "cboe"}
        for d, c in (("2026-06-01", 15.0), ("2026-06-02", 16.0),
                     ("2026-06-03", 40.0))], conflict="replace")
    rows = store.run(store.limit(store.order_by(
        store.between("SwingMacroDaily", "VIX|", "VIX|2026-06-03"),
        index="id", desc=True), 1))
    assert [r["date"] for r in rows] == ["2026-06-02"]


def test_signals_filter_by_instance(store):
    store.insert("SwingSignals", [
        {"id": "a1", "instance_id": "swing-paper", "status": "pending"},
        {"id": "b1", "instance_id": "other", "status": "pending"}])
    rows = store.run(store.filter(
        "SwingSignals", P.field("instance_id").eq("swing-paper")))
    assert [r["id"] for r in rows] == ["a1"]
```

- [ ] **Step 3: Run it to verify it fails**

Run: `python3 -m pytest backend/tests/test_swing_tables.py -q -p no:cacheprovider`
Expected: FAIL — `AssertionError: SwingSignals` (not in `ALL_TABLES`).

- [ ] **Step 4: Register the tables**

In `backend/db/schema.py`, `ALL_TABLES`, replace:

```python
    "PointInTimeManifests", "PriceHistory", "PushDevices", "Stocks", "Strategies",
    "TickerDayFeatures", "Users", "backtest_replay_calls",
```

with:

```python
    "PointInTimeManifests", "PriceHistory", "PushDevices", "Stocks", "Strategies",
    "SwingIndexMembership", "SwingIvSnapshots", "SwingMacroDaily",
    "SwingSectorMap", "SwingSignals", "SwingWheelScans",
    "TickerDayFeatures", "Users", "backtest_replay_calls",
```

In `_SPECS`, directly after `TableSpec("OutlierGraphPeers"),` insert:

```python
    # Swing-trader port (docs/superpowers/specs/2026-09-24-swing-trader-port-design.md §7).
    # The two record tables are read per instance. The three dated series are
    # read by id prefix ("VIX|", "SPX|", "SYMBOL|") and always strictly before
    # the NY trading date, so a bytewise prefix scan is the whole access path.
    TableSpec("SwingSignals", indexed_fields=("instance_id",)),
    TableSpec("SwingWheelScans", indexed_fields=("instance_id",)),
    TableSpec("SwingIvSnapshots", prefix_fields=("id",)),
    TableSpec("SwingMacroDaily", prefix_fields=("id",)),
    TableSpec("SwingIndexMembership", prefix_fields=("id",)),
    TableSpec("SwingSectorMap"),
```

In `backend/tests/dbcore/test_schema_ensure.py`, replace:

```python
    # 2026-09-02: +2 for the outlier sleeve's feature and peer tables
    # (OutlierUniverseFeatures, OutlierGraphPeers).
    assert len(schema.ALL_TABLES) == 129 + len(_LAZILY_CREATED)
```

with:

```python
    # 2026-09-02: +2 for the outlier sleeve's feature and peer tables
    # (OutlierUniverseFeatures, OutlierGraphPeers).
    # 2026-09-24: +6 for the swing-trader port (SwingSignals, SwingWheelScans,
    # SwingIvSnapshots, SwingMacroDaily, SwingIndexMembership, SwingSectorMap).
    assert len(schema.ALL_TABLES) == 135 + len(_LAZILY_CREATED)
```

- [ ] **Step 5: Publish the contract additions**

Append to `docs/superpowers/plans/2026-09-24-swing-port-interfaces.md`:

```markdown

## 11. Additions from plan B

- `swing_trader.constants.SWING_DEFAULTS` and `WHEEL_DEFAULTS`: the header defaults. The six swing `live_*` values equal A-live's `_SW_DEFAULTS` row (0.2 / 0.2 / 0.2 / 0.25 / 0.35 / 0.45).
- Intent labels `swing_stop_exit` and `swing_target_exit` (ST `exit_signal` close-based stop and target).
- The live swing entry hint has no share count: `buy_cash = max(min(equity × 0.125, buying power) × size_adjustment, prior close)`; the engine buys `floor(buy_cash / live price)`. Approval orders carry `qty` (`approvals.build_approved_order`).
- Swing strategy-cache key `_swing_pending_exits` (`{symbol: {"reason", "intent", "since"}}`): an emitted exit is re-emitted on every later tick while the stock is held with no working non-bracket sell (answers A-live addition 15).
- Option order dict key `"session": "YYYY-MM-DD"`, informational (A-live keys option sells on the session itself).
- The wheel lane reads `_engine_wheel_assignments` (A-live addition 13) from its strategy cache: only assigned shares are covered-call candidates.
- `SwingSignals` may carry `score: None` (AI gate off), `context: dict`, and `error` on a `failed` row the lane wrote (A-live's approval handler writes only `status` and `order_client_id`).
- `swing_trader.signals_store`: `signal_id_for(instance_id, lane, session, symbol) -> str`, `new_signal(**fields) -> dict`, `cas_signal(signal_id, *, expect_status, doc) -> bool`, `swing_owned_symbols(instance_id, held) -> set[str]`, `insert_wheel_scan(row) -> str`, `list_wheel_scans(instance_id, limit=50) -> list[dict]`, `all_signals(instance_id) -> list[dict]`, `ensure_tables() -> None`, `OPEN_STATUSES`.
- `swing_trader.approvals.SignalConflict(ValueError)`: a decision on a signal that is not pending; `_run` maps it to 400 per §9 item 2. A click that lost the compare-and-swap: `interactive_utils.SwingDecisionRaceError` → 409. An approval that cannot reach the broker, or an unreadable wheel book: `interactive_utils.SwingBrokerUnavailableError` → 503, and the signal stays pending. Unknown or foreign signal id: `LookupError` → 404.
- `swing_trader.account`: read-only book views shared by both lanes (`equity_positions`, `option_symbols`, `live_equity`, `live_buying_power`, `option_positions`, `open_orders`, `account_options`, `spendable`, `pending_symbols`, `entry_price_from_trades`).
- The approval command handler (§7) is plan A-live Task 15's `_execute_swing_approval`; plan B does not edit `broker.py`.
- `swing_trader.notify.send(category, instance_id, title, message, *, priority=0)` and `notify_wheel_assignment(instance_id, *, symbol, qty, price=None, date=None)` (A-live's activities poller calls the latter).
- `llm_utils.call_llm_with_web_search(provider, api_key, model, prompt, *, max_output_tokens=300, max_uses=2, timeout_sec=None, provider_config=None) -> str`.
- Plan B consumes from A-live: `option_type` on option positions in `live_broker_fetch.fetch_broker_live_state`; a REST mark for every symbol in `_nexus_executable_buys` / `_nexus_sell_enforcement` before the gate.
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python3 -m pytest backend/tests/test_swing_tables.py backend/tests/dbcore/test_schema_ensure.py backend/tests/test_outlier_features.py -q -p no:cacheprovider`
Expected: PASS (dbcore tests that need Postgres skip without `PG_TEST_DSN`).

- [ ] **Step 7: Commit**

Run `gitnexus_detect_changes()`; expect only `backend/db/schema.py` registry literals and the test/doc files.

```bash
git add backend/db/schema.py backend/tests/dbcore/test_schema_ensure.py backend/tests/test_swing_tables.py docs/superpowers/plans/2026-09-24-swing-port-interfaces.md
git commit -m "feat(swing): register the six swing-trader tables

SwingSignals and SwingWheelScans are per-instance record tables; the three
dated series are read by id prefix strictly before the NY date; the sector
map is a plain keyed table. The interface contract gains plan B's additions.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016ztXjLWXu61Dpp4XMU2jsS"
```

---

### Task 2: Vendored ST oracle, constants and the two header defaults (B2)

**Files:**
- Create: `backend/tests/fixtures/swing_trader_st/paper_trader_pure.py`
- Create: `backend/tests/fixtures/swing_trader_st/wheel_trader_pure.py`
- Create: `backend/swing_trader/__init__.py`
- Create: `backend/swing_trader/constants.py`
- Test: `backend/tests/test_swing_constants.py`

**Interfaces:**
- Consumes: nothing.
- Produces: the vendored ST modules every parity test loads by path; `swing_trader.constants` names used everywhere after this task: the swing names exactly as ST (`RSI_PERIOD`, `RSI_OVERSOLD`, `PROFIT_TARGET`, `STOP_LOSS`, `ADX_TREND_MIN`, `SMA200_PERIOD`, `VOL_AVG_PERIOD`, `MACD_*`, `ADX_PERIOD`, `VIX_FEAR_THRESHOLD`, `SPY_BUFFER`, `WARMUP_DAYS`, `MAX_POSITIONS`, `BEAR_REGIME_DAYS`, `DEFENSIVE_UNIVERSE`, `EARNINGS_HARD_BLOCK`, `MAX_PER_SECTOR`, `POSITION_SIZE_PCT`, `UNIVERSE`), the wheel names (`WHEEL_UNIVERSE`, `WHEEL_RSI_PERIOD`, `RSI_MAX`, `RSI_MIN`, `MIN_OPTION_PREMIUM_PCT`, `TARGET_DELTA`, `DAYS_TO_EXPIRY`, `WHEEL_WARMUP_DAYS`, `SMA50_PERIOD`, `ATR_PERIOD`, `STRIKE_ATR_MULT`, `MAX_COLLATERAL_PCT`, `AUTO_COVERED_CALL`, `WHEEL_APPROVE_THRESHOLD`, `WHEEL_REVIEW_THRESHOLD`, `WHEEL_SECTOR_MAP`, `WHEEL_MAX_PER_SECTOR`, `ETF_NO_EARNINGS`, `YFINANCE_FLAKY`, `MIN_DTE`, `LIMIT_BID_MIN`, `LIMIT_BID_MULT`, `LIMIT_CLOSE_MULT`, `AUTO_CLOSE_ITM_PCT`, `AUTO_CLOSE_DTE2_PCT`), `AI_RSI_PERIOD`, `AI_APPROVE_THRESHOLD`, `AI_REVIEW_THRESHOLD`, `TARGET_DTE`, `RANK_WINDOW`, `MIN_RANK_ROWS`, `GATE_TRADES`, `MIN_BUCKET_N`, `BUCKETS`, `IV_SNAPSHOT_TIME_ET`, `SWING_DEFAULTS: dict`, `WHEEL_DEFAULTS: dict`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_swing_constants.py`:

```python
"""Every ported constant equals ST's, and the header defaults are ST's values
under the spec's key names (spec §5)."""
import importlib.util
import os
import sys

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from swing_trader import constants as C  # noqa: E402

_ST_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "fixtures", "swing_trader_st")


def _st(name):
    """Load a vendored ST module by path, never as a package import."""
    spec = importlib.util.spec_from_file_location(
        f"_swing_st_{name}", os.path.join(_ST_DIR, f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_swing_constants_equal_st():
    st = _st("paper_trader_pure")
    for name in ("UNIVERSE", "POSITION_SIZE_PCT", "PROFIT_TARGET", "STOP_LOSS",
                 "RSI_PERIOD", "RSI_OVERSOLD", "RSI_OVERBOUGHT", "SMA200_PERIOD",
                 "MACD_FAST", "MACD_SLOW", "MACD_SIGNAL", "VOL_AVG_PERIOD",
                 "ADX_PERIOD", "ADX_TREND_MIN", "VIX_FEAR_THRESHOLD",
                 "SPY_BUFFER", "WARMUP_DAYS", "MAX_POSITIONS",
                 "BEAR_REGIME_DAYS", "DEFENSIVE_UNIVERSE",
                 "EARNINGS_HARD_BLOCK", "MAX_PER_SECTOR"):
        assert getattr(C, name) == getattr(st, name), name


def test_wheel_constants_equal_st():
    st = _st("wheel_trader_pure")
    pairs = {
        "WHEEL_UNIVERSE": "WHEEL_UNIVERSE", "WHEEL_RSI_PERIOD": "RSI_PERIOD",
        "RSI_MAX": "RSI_MAX", "RSI_MIN": "RSI_MIN",
        "IV_PERCENTILE_MIN": "IV_PERCENTILE_MIN",
        "MIN_OPTION_PREMIUM_PCT": "MIN_OPTION_PREMIUM_PCT",
        "MAX_DELTA": "MAX_DELTA", "TARGET_DELTA": "TARGET_DELTA",
        "DAYS_TO_EXPIRY": "DAYS_TO_EXPIRY", "WHEEL_WARMUP_DAYS": "WARMUP_DAYS",
        "SMA50_PERIOD": "SMA50_PERIOD", "ATR_PERIOD": "ATR_PERIOD",
        "STRIKE_ATR_MULT": "STRIKE_ATR_MULT",
        "MAX_COLLATERAL_PCT": "MAX_COLLATERAL_PCT",
        "AUTO_COVERED_CALL": "AUTO_COVERED_CALL",
        "WHEEL_APPROVE_THRESHOLD": "APPROVE_THRESHOLD",
        "WHEEL_REVIEW_THRESHOLD": "REVIEW_THRESHOLD",
        "WHEEL_SECTOR_MAP": "WHEEL_SECTOR_MAP",
        "WHEEL_MAX_PER_SECTOR": "WHEEL_MAX_PER_SECTOR",
    }
    for ours, theirs in pairs.items():
        assert getattr(C, ours) == getattr(st, theirs), ours


def test_the_remaining_st_constants_carry_st_values():
    # ai_analyst.py:41-43, iv_collector.py:50-52, calibration.py:22-25,
    # app.py:1341-1342, wheel_trader.py:785 and :590-602.
    assert (C.AI_RSI_PERIOD, C.AI_APPROVE_THRESHOLD, C.AI_REVIEW_THRESHOLD) == (14, 75, 50)
    assert (C.TARGET_DTE, C.RANK_WINDOW, C.MIN_RANK_ROWS) == (30, 252, 60)
    assert (C.GATE_TRADES, C.MIN_BUCKET_N) == (20, 5)
    assert C.BUCKETS == [(50, 64), (65, 74), (75, 84), (85, 100)]
    assert (C.AUTO_CLOSE_ITM_PCT, C.AUTO_CLOSE_DTE2_PCT) == (10.0, 5.0)
    assert C.MIN_DTE == 7
    assert (C.LIMIT_BID_MIN, C.LIMIT_BID_MULT, C.LIMIT_CLOSE_MULT) == (0.05, 0.95, 0.90)
    assert C.ETF_NO_EARNINGS == {"SPY", "QQQ", "GLD", "XLE", "IWM", "DIA", "VXX"}
    assert C.YFINANCE_FLAKY == {"NVDA", "TSLA", "AMD"}


SWING_KEYS = {
    "strategy_swing_enabled", "rsi_period", "rsi_entry_max", "rsi_overbought",
    "sma_long", "macd_fast", "macd_slow", "macd_signal", "vol_avg_period",
    "adx_period", "adx_min", "spy_buffer", "vix_max", "position_size_pct",
    "max_positions", "max_per_sector", "profit_target", "stop_loss",
    "bear_regime_days", "defensive_universe", "earnings_hard_block_days",
    "ai_gate_enabled", "ai_approve_threshold", "ai_review_threshold",
    "conviction_llm_model_id", "scan_time_et", "live_max_order_fraction",
    "live_max_symbol_fraction", "live_max_leveraged_fraction",
    "live_soft_drawdown", "live_hard_drawdown", "live_kill_drawdown",
    "honour_single_position_cap", "broker_max_single_position_pct",
}
WHEEL_KEYS = {
    "strategy_wheel_enabled", "rsi_min", "rsi_max", "sma_trend", "atr_period",
    "strike_atr_mult", "min_premium_pct", "target_delta", "days_to_expiry",
    "max_collateral_pct", "max_per_sector", "auto_covered_call",
    "approve_threshold", "review_threshold", "earnings_block_days",
    "limit_bid_mult", "scan_weekday", "scan_time_et", "monitor_time_et",
    "conviction_llm_model_id",
}


def test_swing_defaults_are_st_values_under_the_spec_keys():
    d = C.SWING_DEFAULTS
    assert set(d) == SWING_KEYS
    assert d["strategy_swing_enabled"] is False
    assert (d["rsi_period"], d["rsi_entry_max"], d["rsi_overbought"]) == (
        C.RSI_PERIOD, C.RSI_OVERSOLD, C.RSI_OVERBOUGHT)
    assert d["sma_long"] == C.SMA200_PERIOD
    assert (d["macd_fast"], d["macd_slow"], d["macd_signal"]) == (
        C.MACD_FAST, C.MACD_SLOW, C.MACD_SIGNAL)
    assert (d["vol_avg_period"], d["adx_period"], d["adx_min"]) == (
        C.VOL_AVG_PERIOD, C.ADX_PERIOD, C.ADX_TREND_MIN)
    assert (d["spy_buffer"], d["vix_max"]) == (C.SPY_BUFFER, C.VIX_FEAR_THRESHOLD)
    assert (d["position_size_pct"], d["max_positions"], d["max_per_sector"]) == (
        C.POSITION_SIZE_PCT, C.MAX_POSITIONS, C.MAX_PER_SECTOR)
    assert (d["profit_target"], d["stop_loss"]) == (C.PROFIT_TARGET, C.STOP_LOSS)
    assert d["bear_regime_days"] == C.BEAR_REGIME_DAYS
    assert d["defensive_universe"] == C.DEFENSIVE_UNIVERSE
    assert d["earnings_hard_block_days"] == C.EARNINGS_HARD_BLOCK
    assert d["ai_gate_enabled"] is True
    assert (d["ai_approve_threshold"], d["ai_review_threshold"]) == (75, 50)
    assert d["conviction_llm_model_id"] == "" and d["scan_time_et"] == "09:15"
    assert (d["live_max_order_fraction"], d["live_max_symbol_fraction"]) == (0.2, 0.2)
    assert d["honour_single_position_cap"] is True
    assert d["broker_max_single_position_pct"] == 0.2
    assert 0 < d["live_soft_drawdown"] < d["live_hard_drawdown"] < d["live_kill_drawdown"] < 1


def test_wheel_defaults_are_st_values_under_the_spec_keys():
    d = C.WHEEL_DEFAULTS
    assert set(d) == WHEEL_KEYS
    assert d["strategy_wheel_enabled"] is False
    assert (d["rsi_min"], d["rsi_max"], d["sma_trend"], d["atr_period"]) == (
        C.RSI_MIN, C.RSI_MAX, C.SMA50_PERIOD, C.ATR_PERIOD)
    assert (d["strike_atr_mult"], d["min_premium_pct"], d["target_delta"]) == (
        C.STRIKE_ATR_MULT, C.MIN_OPTION_PREMIUM_PCT, C.TARGET_DELTA)
    assert (d["days_to_expiry"], d["max_collateral_pct"], d["max_per_sector"]) == (
        C.DAYS_TO_EXPIRY, C.MAX_COLLATERAL_PCT, C.WHEEL_MAX_PER_SECTOR)
    assert d["auto_covered_call"] is C.AUTO_COVERED_CALL is False
    assert (d["approve_threshold"], d["review_threshold"]) == (
        C.WHEEL_APPROVE_THRESHOLD, C.WHEEL_REVIEW_THRESHOLD)
    assert (d["earnings_block_days"], d["limit_bid_mult"]) == (7, C.LIMIT_BID_MULT)
    assert (d["scan_weekday"], d["scan_time_et"], d["monitor_time_et"]) == (0, "10:30", "15:45")
    assert d["conviction_llm_model_id"] == ""
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest backend/tests/test_swing_constants.py -q -p no:cacheprovider`
Expected: FAIL — `ModuleNotFoundError: No module named 'swing_trader'`.

- [ ] **Step 3: Vendor the ST paper_trader oracle**

Create `backend/tests/fixtures/swing_trader_st/paper_trader_pure.py` (no `__init__.py`: tests load it by path; the name has no `test_` prefix so pytest never collects it):

```python
"""VENDORED ST CODE — parity oracle for tests only. Never import from production.

Source: github.com/tmasters2876/swing-trader @ c2afa71, paper_trader.py.
Copied verbatim: lines 63-81, 85-89, 92-149, 156-201, 237-272, 293-340.
Not reproduced: the Alpaca client, dotenv, requests, tabulate and the print
rebinding of the original module. Two module attributes stand in for its I/O
so a test can drive it: `yf` (ST's yfinance module; None until a test injects
a stub) and REGIME_TRACKER_FILE (ST's /var/data path; a test points it at
tmp_path).
"""
import json
import os
import tempfile
from datetime import datetime, timezone

import numpy as np
import pandas as pd

yf = None
REGIME_TRACKER_FILE = "regime_tracker.json"

UNIVERSE = [
    "AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "META", "GOOGL", "SPY",
    "QQQ",  "AMD",  "NFLX", "JPM",  "DIS",  "BA",   "XLE",  "GLD",
]
POSITION_SIZE_PCT = 0.125
PROFIT_TARGET = 0.09
STOP_LOSS = 0.06
RSI_PERIOD = 14
RSI_OVERSOLD = 50   # raised from 40 (variant L sweep, 2026-06-10) — pullback entries, not knife-catches
RSI_OVERBOUGHT = 70
SMA200_PERIOD = 200
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
VOL_AVG_PERIOD = 20
ADX_PERIOD    = 14
ADX_TREND_MIN = 15   # ADX must exceed this to confirm a real trend
VIX_FEAR_THRESHOLD = 25.0      # block new entries when VIX closes above this
SPY_BUFFER = 1.03              # SPY must be this multiple above SMA200 for regime to be active
WARMUP_DAYS   = 300  # ~200 trading days needed for SMA200 warmup
MAX_POSITIONS = 8    # max concurrent open positions
BEAR_REGIME_DAYS      = 10   # consecutive blocked days before switching to defensive mode
DEFENSIVE_UNIVERSE    = ["XLP", "XLU", "XLV", "GLD", "SHY"]
EARNINGS_HARD_BLOCK   = 5    # days — hard skip in entry loop before AI is called

# Sector map for correlation check — covers S&P 500 major sectors
# Symbol → sector string. Unknown symbols default to "unknown" (allowed through)
_SECTOR_OVERRIDES: dict[str, str] = {
    # ETFs and commodities not in S&P 500
    "SPY": "broad_market", "QQQ": "broad_market",
    "GLD": "commodity",    "XLE": "energy",
    "IWM": "broad_market", "DIA": "broad_market",
}

_SP500_SECTOR_CACHE: dict[str, str] = {}

def get_symbol_sector(symbol: str) -> str:
    """Return the sector for a symbol. Fetches from yfinance on first call, then caches."""
    if symbol in _SECTOR_OVERRIDES:
        return _SECTOR_OVERRIDES[symbol]
    if symbol in _SP500_SECTOR_CACHE:
        return _SP500_SECTOR_CACHE[symbol]
    try:
        info   = yf.Ticker(symbol).info
        sector = info.get("sector", "unknown").lower().replace(" ", "_")
        _SP500_SECTOR_CACHE[symbol] = sector
        return sector
    except Exception:
        return "unknown"

MAX_PER_SECTOR = 1   # hard cap — only 1 position per sector at a time


def _atomic_write_json(path: str, data) -> None:
    dir_ = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=dir_)
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh, indent=2, default=str)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def get_blocked_days() -> int:
    try:
        with open(REGIME_TRACKER_FILE) as f:
            return json.load(f).get("blocked_days", 0)
    except Exception:
        return 0


def update_regime_tracker(regime_ok: bool) -> int:
    days = 0 if regime_ok else get_blocked_days() + 1
    try:
        _atomic_write_json(REGIME_TRACKER_FILE, {"blocked_days": days, "updated": datetime.now(timezone.utc).isoformat()})
    except Exception:
        pass
    return days


def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100.0 - (100.0 / (1.0 + rs))


def _macd(series: pd.Series, fast=12, slow=26, signal=9):
    ema_f = series.ewm(span=fast, adjust=False).mean()
    ema_s = series.ewm(span=slow, adjust=False).mean()
    line = ema_f - ema_s
    sig = line.ewm(span=signal, adjust=False).mean()
    return line, sig


def _sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period).mean()


def _adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Average Directional Index — measures trend strength (not direction)."""
    prev_close = close.shift(1)
    prev_low   = low.shift(1)
    prev_high  = high.shift(1)

    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs(),
    ], axis=1).max(axis=1)

    dm_plus  = np.where((high - prev_high) > (prev_low - low), (high - prev_high).clip(lower=0), 0.0)
    dm_minus = np.where((prev_low - low) > (high - prev_high), (prev_low - low).clip(lower=0), 0.0)

    dm_plus  = pd.Series(dm_plus,  index=close.index)
    dm_minus = pd.Series(dm_minus, index=close.index)

    atr      = tr.ewm(com=period - 1, min_periods=period).mean()
    di_plus  = 100 * dm_plus.ewm(com=period - 1,  min_periods=period).mean() / atr
    di_minus = 100 * dm_minus.ewm(com=period - 1, min_periods=period).mean() / atr

    dx = (100 * (di_plus - di_minus).abs() / (di_plus + di_minus).replace(0, np.nan))
    return dx.ewm(com=period - 1, min_periods=period).mean()


def get_latest_indicators(df: pd.DataFrame) -> dict:
    """Return dict of symbol → latest indicator snapshot."""
    result = {}
    for symbol in df["symbol"].unique():
        sdf = df[df["symbol"] == symbol].copy().sort_values("date").reset_index(drop=True)
        # yfinance can return a NaN close for today's forming bar near the open.
        # float(nan) does not raise, so an unguarded close flows through as
        # now=$nan and (for SPY) SPY $nan in the regime line. Drop NaN-close rows
        # to fall back to the last completed bar; skip the symbol entirely if
        # nothing usable remains (handled downstream as "no indicator data").
        sdf = sdf[sdf["close"].notna()]
        if sdf.empty:
            continue
        sdf["rsi"] = _rsi(sdf["close"], RSI_PERIOD)
        macd_line, macd_sig = _macd(sdf["close"], MACD_FAST, MACD_SLOW, MACD_SIGNAL)
        sdf["macd_hist"]       = macd_line - macd_sig
        sdf["macd_hist_prev"]  = sdf["macd_hist"].shift(1)
        sdf["macd_hist_prev2"] = sdf["macd_hist"].shift(2)
        sdf["sma200"]    = _sma(sdf["close"], SMA200_PERIOD)
        sdf["vol_avg20"] = sdf["volume"].rolling(VOL_AVG_PERIOD).mean()
        sdf["rsi_prev"]  = sdf["rsi"].shift(1)
        sdf["adx"]       = _adx(sdf["high"], sdf["low"], sdf["close"], ADX_PERIOD)
        last = sdf.iloc[-1]
        result[symbol] = {
            "close":           float(last["close"]),
            "volume":          float(last["volume"]),
            "rsi":             float(last["rsi"])             if not pd.isna(last["rsi"])             else None,
            "rsi_prev":        float(last["rsi_prev"])        if not pd.isna(last["rsi_prev"])        else None,
            "macd_hist":       float(last["macd_hist"])       if not pd.isna(last["macd_hist"])       else None,
            "macd_hist_prev":  float(last["macd_hist_prev"])  if not pd.isna(last["macd_hist_prev"])  else None,
            "macd_hist_prev2": float(last["macd_hist_prev2"]) if not pd.isna(last["macd_hist_prev2"]) else None,
            "sma200":          float(last["sma200"])          if not pd.isna(last["sma200"])          else None,
            "vol_avg20":       float(last["vol_avg20"])       if not pd.isna(last["vol_avg20"])       else None,
            "adx":             float(last["adx"])             if not pd.isna(last["adx"])             else None,
        }
    return result


def entry_signal(ind: dict) -> bool:
    if any(v is None for v in ind.values()):
        return False
    # RSI<50 + 1-bar MACD ("variant L") — only config positive in both halves of
    # the 2021-2026 S&P 500 sweep (experiments/combo_results.json, 2026-06-10).
    rsi_signal      = ind["rsi"] < RSI_OVERSOLD and ind["rsi"] > ind["rsi_prev"]
    macd_improving  = ind["macd_hist"] > ind["macd_hist_prev"]
    vol_above_avg   = ind["volume"] > ind["vol_avg20"]
    above_sma       = ind["close"] > ind["sma200"]
    trend_confirmed = ind["adx"] > ADX_TREND_MIN
    rr_ok           = (PROFIT_TARGET / STOP_LOSS) >= 1.5
    return rsi_signal and macd_improving and vol_above_avg and above_sma and trend_confirmed and rr_ok


def exit_signal(ind: dict, entry_price: float):
    """Returns (should_exit: bool, reason: str | None)."""
    price = ind["close"]
    pct = (price - entry_price) / entry_price

    if pct >= PROFIT_TARGET:
        return True, "profit_target"
    if pct <= -STOP_LOSS:
        return True, "stop_loss"

    rsi_cross_ob = (
        ind["rsi_prev"] is not None
        and ind["rsi"] is not None
        and ind["rsi_prev"] < RSI_OVERBOUGHT
        and ind["rsi"] >= RSI_OVERBOUGHT
    )
    if rsi_cross_ob:
        return True, "rsi_overbought"

    return False, None


def sector_conflict(symbol: str, active_positions: set) -> str | None:
    """
    Returns the conflicting symbol if adding `symbol` would exceed MAX_PER_SECTOR
    for its sector, otherwise returns None.
    """
    candidate_sector = get_symbol_sector(symbol)
    if candidate_sector == "unknown":
        return None  # unknown sector — allow entry
    for held in active_positions:
        if get_symbol_sector(held) == candidate_sector:
            return held
    return None
```

- [ ] **Step 4: Vendor the ST wheel_trader oracle**

Create `backend/tests/fixtures/swing_trader_st/wheel_trader_pure.py`:

```python
"""VENDORED ST CODE — parity oracle for tests only. Never import from production.

Source: github.com/tmasters2876/swing-trader @ c2afa71, wheel_trader.py.
Copied verbatim: lines 76-80, 83-120, 704, 720-741, 765-834, 839-953,
958-1072, 1220-1252. Not reproduced: the Alpaca and Anthropic clients,
dotenv, tabulate, notify and the print rebinding. Module attributes a test
drives: yf, datetime (next_friday reads datetime.now(ET)),
_get_trading_client (the calendar), _get_client and _fetch_news (the scorer),
WHEEL_COMPLETE_MARKER.
"""
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from alpaca.trading.requests import GetCalendarRequest

yf = None
MODEL = "claude-sonnet-4-6"
WHEEL_COMPLETE_MARKER = "last_wheel_scan_complete.txt"


def _get_trading_client():
    raise RuntimeError("a test must inject _get_trading_client")


def _get_client():
    raise RuntimeError("a test must inject _get_client")


def _fetch_news(symbol):
    raise RuntimeError("a test must inject _fetch_news")


WHEEL_UNIVERSE = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "META",
    "NVDA", "JPM",  "V",     "JNJ",  "WMT",
    "SPY",  "QQQ",  "GLD",   "XLE",  "AMD",
]

RSI_PERIOD             = 14
RSI_MAX                = 60        # only sell puts when stock is NOT overbought
RSI_MIN                = 30        # avoid if deeply oversold (could keep falling)
IV_PERCENTILE_MIN      = 20        # want elevated IV for premium (approximated via ATR%)
MIN_OPTION_PREMIUM_PCT = 0.005     # minimum 0.5% of stock price as weekly premium (approx)
MAX_DELTA              = 0.35      # target 0.20–0.35 delta puts (approximated by strike selection)
TARGET_DELTA           = 0.25      # target put delta — 0.25 = ~75% probability of expiring worthless
DAYS_TO_EXPIRY         = 7         # target weekly options (next Friday)
WARMUP_DAYS            = 60        # days of history needed for indicators
SMA50_PERIOD           = 50        # trend filter — only sell puts above 50-day SMA
ATR_PERIOD             = 14        # for IV approximation and strike selection
STRIKE_ATR_MULT        = 0.5       # put strike = current price − (ATR × this multiple)
MAX_COLLATERAL_PCT     = 0.25      # max collateral per name as fraction of account equity
                                   # (strike × 100 × contracts must fit — caps assignment risk)
AUTO_COVERED_CALL      = False     # R4-02: dry-run mode. When False, assignment detection
                                   # still runs and notifies what WOULD be placed, but no
                                   # order is submitted. Flip to True after the first real
                                   # assignment's dry-run notification has been reviewed.

# AI thresholds — same pattern as ai_analyst.py
APPROVE_THRESHOLD   = 75           # score ≥ 75 → log as recommended
REVIEW_THRESHOLD    = 50           # score 50–74 → log as pending review
                                   # score < 50 → reject

WHEEL_SECTOR_MAP: dict[str, str] = {
    "AAPL": "tech",  "MSFT": "tech",  "NVDA": "tech",  "AMD": "tech",
    "GOOGL": "tech", "META": "tech",  "AMZN": "tech",  "TSLA": "tech",
    "NFLX": "tech",  "ADBE": "tech",  "CRM": "tech",   "ORCL": "tech",
    "INTC": "tech",  "QCOM": "tech",  "TXN": "tech",   "AVGO": "tech",
    "JPM": "finance","BAC": "finance","GS": "finance",  "MS": "finance",
    "WFC": "finance","C": "finance",  "BLK": "finance", "BX": "finance",
    "XOM": "energy", "CVX": "energy", "COP": "energy",  "SLB": "energy",
    "XLE": "energy", "OXY": "energy",
    "JNJ": "health", "UNH": "health", "PFE": "health",  "MRK": "health",
    "ABT": "health", "TMO": "health", "MDT": "health",  "LLY": "health",
    "SPY": "broad",  "QQQ": "broad",  "GLD": "commodity",
}
WHEEL_MAX_PER_SECTOR = 2   # allow up to 2 per sector for wheel (more diversified than swing)

ET = ZoneInfo("America/New_York")


def _rsi(series: pd.Series, period: int = RSI_PERIOD) -> pd.Series:
    delta    = series.diff()
    gain     = delta.clip(lower=0)
    loss     = (-delta).clip(lower=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs       = avg_gain / avg_loss.replace(0, np.nan)
    return 100.0 - (100.0 / (1.0 + rs))


def _sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period).mean()


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = ATR_PERIOD) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(com=period - 1, min_periods=period).mean()


def next_friday() -> str:
    """
    Return the date string (YYYY-MM-DD) of the next valid options expiry
    Friday that is at least MIN_DTE days away.

    Uses Alpaca's market calendar API to determine valid trading days —
    this handles all US market holidays, observed holidays, and unexpected
    closures automatically without a static holiday list.

    Logic:
    1. Find the next Friday that is MIN_DTE+ days away
    2. Ask Alpaca if that Friday is a trading day
    3. If yes — use it
    4. If no — check the Thursday of that week (exchange sometimes moves
       expiry to Thursday before a holiday Friday)
    5. If Thursday is also closed — skip to the following Friday and repeat
    Falls back to the pure date calculation if Alpaca calendar is unavailable.
    """
    from datetime import date as _date

    MIN_DTE = 7

    today = datetime.now(ET).date()
    days_ahead = (4 - today.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    candidate = today + timedelta(days=days_ahead)

    # Enforce MIN_DTE
    if (candidate - today).days < MIN_DTE:
        candidate += timedelta(days=7)

    # Ask Alpaca's calendar — handles all holidays automatically
    try:
        client     = _get_trading_client()
        # Fetch calendar for a 3-week window around candidate
        cal_req    = GetCalendarRequest(
            start=str(candidate - timedelta(days=1)),
            end=str(candidate + timedelta(days=14)),
        )
        calendar   = client.get_calendar(cal_req)
        # Build a set of valid trading dates in the window
        trading_days = {_date.fromisoformat(str(c.date)) for c in calendar}

        max_attempts = 8   # safety limit — never loop forever
        attempts     = 0
        while attempts < max_attempts:
            if candidate in trading_days:
                print(f"  [next_friday] Expiry: {candidate} (confirmed trading day via Alpaca calendar)")
                return str(candidate)

            # Friday is a holiday — check Thursday of same week
            thursday = candidate - timedelta(days=1)
            if thursday in trading_days:
                print(f"  [next_friday] {candidate} is a non-trading day — using Thursday {thursday} (holiday expiry)")
                return str(thursday)

            # Both closed — skip to next Friday
            print(f"  [next_friday] {candidate} is a non-trading day, Thursday also closed — skipping to following Friday")
            candidate += timedelta(days=7)
            attempts  += 1

        # Exhausted attempts — fall through to date-only result
        print(f"  [next_friday] WARNING: could not confirm trading day after {max_attempts} attempts — using {candidate}")
        return str(candidate)

    except Exception as exc:
        # Alpaca calendar unavailable — fall back to date calculation only
        print(f"  [next_friday] Calendar API unavailable ({exc}) — using date calculation fallback")
        return str(candidate)


def get_candidates(raw: dict, symbols: list) -> list[dict]:
    """
    Screen each symbol and return a list of candidate dicts for put selling.

    Screening criteria (ALL must pass):
    1. RSI(14) between RSI_MIN and RSI_MAX — not overbought, not collapsing
    2. Close > SMA(50) — stock in uptrend (we want puts on rising stocks)
    3. ATR% > 1.5% — enough volatility to generate meaningful premium
    4. Estimated weekly premium > MIN_OPTION_PREMIUM_PCT of stock price
    5. No earnings within 7 days — avoid binary event risk
    """
    candidates = []

    for symbol in symbols:
        try:
            # raw is {symbol: DataFrame} from market_data (R8-05)
            df = raw.get(symbol)
            if df is None:
                continue
            df = df.copy().dropna(subset=["Close"]).reset_index()

            if len(df) < SMA50_PERIOD + 5:
                print(f"  [wheel] {symbol}: insufficient data ({len(df)} bars), skipping")
                continue

            close  = df["Close"]
            high   = df["High"]
            low    = df["Low"]

            rsi_series = _rsi(close)
            sma50      = _sma(close, SMA50_PERIOD)
            atr_series = _atr(high, low, close)

            last_close = float(close.iloc[-1])
            last_rsi   = float(rsi_series.iloc[-1]) if not pd.isna(rsi_series.iloc[-1]) else None
            last_sma50 = float(sma50.iloc[-1])       if not pd.isna(sma50.iloc[-1])     else None
            last_atr   = float(atr_series.iloc[-1])  if not pd.isna(atr_series.iloc[-1]) else None

            if any(v is None for v in [last_rsi, last_sma50, last_atr]):
                print(f"  [wheel] {symbol}: indicator NaN, skipping")
                continue

            atr_pct = last_atr / last_close * 100

            # Strike selection: ATR-based OTM put
            strike_price = round(last_close - (last_atr * STRIKE_ATR_MULT), 2)
            otm_pct      = (last_close - strike_price) / last_close * 100

            # Approximate weekly premium as 25% of ATR (rough Black-Scholes proxy)
            est_premium     = round(last_atr * 0.25, 2)
            est_premium_pct = est_premium / last_close * 100

            # Earnings check — ETFs (SPY, QQQ, GLD, XLE etc.) have no earnings calendar
            ETF_NO_EARNINGS = {"SPY", "QQQ", "GLD", "XLE", "IWM", "DIA", "VXX"}
            YFINANCE_FLAKY  = {"NVDA", "TSLA", "AMD"}  # intermittent after-hours data issues
            earnings_days = None
            try:
                import datetime as dt_module
                if symbol.upper() in ETF_NO_EARNINGS or symbol.upper() in YFINANCE_FLAKY:
                    earnings_days = None
                else:
                    cal = yf.Ticker(symbol).calendar
                    if cal:
                        raw_dates = cal.get("Earnings Date")
                        if raw_dates is not None:
                            today = dt_module.date.today()
                            items = raw_dates if hasattr(raw_dates, "__iter__") and not isinstance(raw_dates, str) else [raw_dates]
                            future = sorted(pd.Timestamp(d).date() for d in items if pd.Timestamp(d).date() >= today)
                            earnings_days = (future[0] - today).days if future else None
            except Exception:
                earnings_days = None

            # Apply all filters
            reasons_failed = []
            if not (RSI_MIN <= last_rsi <= RSI_MAX):
                reasons_failed.append(f"RSI {last_rsi:.1f} out of [{RSI_MIN},{RSI_MAX}]")
            if last_close < last_sma50:
                reasons_failed.append(f"below SMA50 (${last_close:.2f} < ${last_sma50:.2f})")
            if atr_pct < 1.5:
                reasons_failed.append(f"ATR% {atr_pct:.2f}% < 1.5%")
            if est_premium_pct < MIN_OPTION_PREMIUM_PCT * 100:
                reasons_failed.append(f"est. premium {est_premium_pct:.3f}% < {MIN_OPTION_PREMIUM_PCT*100:.2f}%")
            if earnings_days is not None and earnings_days <= 7:
                reasons_failed.append(f"earnings in {earnings_days} days")
            if otm_pct < 1.5:
                reasons_failed.append(f"strike only {otm_pct:.1f}% OTM (min 1.5%) — too close to ATM")

            if reasons_failed:
                print(f"  [wheel] {symbol}: FILTERED — {'; '.join(reasons_failed)}")
                continue

            candidates.append({
                "symbol":          symbol,
                "stock_price":     last_close,
                "strike_price":    strike_price,
                "otm_pct":         round(otm_pct, 2),
                "expiry":          next_friday(),
                "est_premium":     est_premium,
                "est_premium_pct": round(est_premium_pct, 3),
                "rsi":             round(last_rsi, 1),
                "sma50":           round(last_sma50, 2),
                "atr":             round(last_atr, 2),
                "atr_pct":         round(atr_pct, 2),
                "earnings_days":   earnings_days,
            })
            print(
                f"  [wheel] {symbol}: CANDIDATE  RSI {last_rsi:.1f}  "
                f"strike ${strike_price}  premium ~${est_premium}/share  exp {next_friday()}"
            )

        except Exception as exc:
            print(f"  [wheel] {symbol}: ERROR — {exc}")
            continue

    return candidates


def score_candidate(candidate: dict) -> dict:
    """
    Ask Claude to score a put-selling candidate 0–100.
    Returns the candidate dict enriched with AI fields.
    """
    import json, re

    symbol      = candidate["symbol"]
    stock_price = candidate["stock_price"]
    strike      = candidate["strike_price"]
    otm_pct     = candidate["otm_pct"]
    expiry      = candidate["expiry"]
    est_premium = candidate["est_premium"]
    rsi         = candidate["rsi"]
    atr_pct     = candidate["atr_pct"]
    earnings    = candidate["earnings_days"]

    print(f"  [AI-wheel] Fetching news for {symbol}…")
    news = _fetch_news(symbol)

    prompt = f"""You are an options income analyst evaluating a cash-secured put opportunity.
Score this trade 0–100 and return ONLY valid JSON — no text outside it.

PROPOSED TRADE
  Strategy:        Sell cash-secured put (Wheel income)
  Symbol:          {symbol}
  Stock price:     ${stock_price:.2f}
  Put strike:      ${strike:.2f}  ({otm_pct:.1f}% OTM)
  Expiry:          {expiry}  (weekly, ~{DAYS_TO_EXPIRY} days)
  Est. premium:    ${est_premium:.2f}/share  (~${est_premium*100:.0f}/contract)
  Est. premium%:   {candidate['est_premium_pct']:.3f}% of stock price

TECHNICALS
  RSI(14):         {rsi:.1f}  (screening range: {RSI_MIN}–{RSI_MAX})
  ATR%:            {atr_pct:.2f}%  (weekly volatility proxy)
  Above SMA(50):   Yes ✓

CONTEXT
  Days to earnings:{f' {earnings}' if earnings is not None else ' unknown'}
  Recent news:     {news}

MARKET CONTEXT — apply penalties as directed above:
  If recent news or your knowledge indicates the broad market (SPY) has declined
  > 2% over the past 3 trading days, apply the SPY weakness penalty.
  If recent news or your knowledge indicates the stock's sector ETF has declined
  > 3% over the past 5 trading days, apply the sector weakness penalty.
  If the stock price is within 5% of the put strike, apply the low margin of
  safety penalty. Current margin: {otm_pct:.1f}% OTM.

SCORING GUIDE
  75–100 → strong put-sell candidate
  50–74  → marginal — flag for human review
  0–49   → reject (too risky or poor premium)

RISK FACTORS THAT SHOULD LOWER SCORE
  • Earnings within 7 days (gap-down risk) — reduce score by 20 points
  • Stock in clear downtrend or recent sharp drop — reduce score by 15 points
  • Very low premium (< 0.5% weekly) — reduce score by 10 points
  • Negative news catalyst (downgrade, miss, scandal) — reduce score by 15 points
  • Sector ETF down > 3% over past 5 days (sector weakness) — reduce score by 15 points
  • SPY down > 2% over past 3 days (broad market weakness) — reduce score by 10 points
  • Stock price within 5% of strike (low margin of safety) — reduce score by 10 points

POSITIVE FACTORS THAT SHOULD RAISE SCORE
  • Stock near support level (put strike near 52-week support)
  • Bullish news or analyst upgrades
  • Premium > 1% weekly (excellent income yield)
  • RSI recovering from oversold (stock likely to stay above strike)

Return exactly this JSON:
{{
  "conviction_score": <integer 0–100>,
  "recommendation": <"approve" | "review" | "reject">,
  "reasoning": "<2–4 sentences explaining the score>",
  "position_size_contracts": <1 | 2 | 3>,
  "key_risks": ["<risk 1>", "<risk 2>"]
}}"""

    print(f"  [AI-wheel] Calling Claude for {symbol} conviction score…")
    try:
        client = _get_client()
        resp = client.messages.create(
            model=MODEL,
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}],
        )
        raw_text = resp.content[0].text.strip()
        fence    = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw_text, re.DOTALL)
        json_str = fence.group(1) if fence else raw_text
        result   = json.loads(json_str)

        score = int(result.get("conviction_score", 0))
        if score >= APPROVE_THRESHOLD:
            result["recommendation"] = "approve"
        elif score >= REVIEW_THRESHOLD:
            result["recommendation"] = "review"
        else:
            result["recommendation"] = "reject"

        contracts = int(result.get("position_size_contracts", 1))
        result["position_size_contracts"] = max(1, min(contracts, 3))

    except Exception as exc:
        print(f"  [AI-wheel] ERROR scoring {symbol}: {exc}")
        result = {
            "conviction_score":      0,
            "recommendation":        "reject",
            "reasoning":             f"AI scoring failed: {exc}",
            "position_size_contracts": 1,
            "key_risks":             ["AI scoring error"],
        }

    candidate.update(result)
    candidate["timestamp"] = datetime.now(timezone.utc).isoformat()
    return candidate


def _scan_already_ran_this_week() -> bool:
    """
    Return True only if a scan COMPLETED this week, per the completion marker
    (last_wheel_scan_complete.txt), not merely if wheel_trades.csv has rows.

    Why the marker and not CSV rows: a Monday scan that started but was killed
    mid-run (e.g. a gunicorn worker restart) still writes partial CSV rows. The
    old row-presence check then reported the week as "already covered" and the
    Tuesday fallback — whose entire purpose is to catch a failed Monday — skipped
    itself. This happened in production on 2026-06-30. The marker is written ONLY
    after main() finishes successfully, so its presence proves completion.

    Fail-safe: any error reading/parsing the marker logs and returns False
    (proceed with the scan). A false negative (an extra Tuesday scan when Monday
    actually succeeded) is far cheaper than a false positive (skipping the only
    successful scan opportunity of the week).
    """
    if not os.path.exists(WHEEL_COMPLETE_MARKER):
        return False
    try:
        today  = datetime.now(ET).date()
        monday = today - timedelta(days=today.weekday())
        with open(WHEEL_COMPLETE_MARKER) as f:
            raw = f.read().strip()
        # Marker holds run_time, formatted "%Y-%m-%d %H:%M ET" — take the date.
        marker_date = datetime.strptime(raw.split()[0], "%Y-%m-%d").date()
        ran = marker_date >= monday
        if ran:
            print(f"  [wheel] Scan already completed this week ({marker_date}) — skipping Tuesday fallback")
        return ran
    except Exception as exc:
        print(f"  [wheel] Could not read completion marker ({exc}) — proceeding")
        return False
```

- [ ] **Step 5: Create the package and the constants**

Create `backend/swing_trader/__init__.py`:

```python
"""Ported logic from github.com/tmasters2876/swing-trader @ c2afa71 ("ST").

Every module names the ST file and line range it came from. Pure functions
are copied verbatim; only I/O changed: files became db.store tables, Pushover
became notifications, the Anthropic SDK became llm_utils. The operator-
approved deviations are spec §9 items 1-17, each marked at its line.

Spec: docs/superpowers/specs/2026-09-24-swing-trader-port-design.md
"""
```

Create `backend/swing_trader/constants.py`:

```python
"""Every ST constant, with ST's value, and the two strategy header defaults.

Ported from github.com/tmasters2876/swing-trader @ c2afa71 ("ST"):
    paper_trader.py:63-81, 85-89, 117   swing constants (verbatim)
    wheel_trader.py:76-80, 83-120       wheel constants
    ai_analyst.py:41-43                 AI thresholds
    iv_collector.py:50-52               IV snapshot constants (verbatim)
    calibration.py:22-25                calibration gate (verbatim)
    wheel_trader.py:892-893             get_candidates' earnings exemptions (hoisted)
    wheel_trader.py:785, 590-602        next_friday's MIN_DTE, the limit ladder (hoisted)
    app.py:1341-1342                    wheel monitor auto-close thresholds (hoisted)
Names that collide across ST files carry a WHEEL_ or AI_ prefix here; the
values do not change.
"""

# -- paper_trader.py:63-81 -------------------------------------------------
UNIVERSE = [
    "AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "META", "GOOGL", "SPY",
    "QQQ",  "AMD",  "NFLX", "JPM",  "DIS",  "BA",   "XLE",  "GLD",
]
POSITION_SIZE_PCT = 0.125
PROFIT_TARGET = 0.09
STOP_LOSS = 0.06
RSI_PERIOD = 14
RSI_OVERSOLD = 50   # raised from 40 (variant L sweep, 2026-06-10) — pullback entries, not knife-catches
RSI_OVERBOUGHT = 70
SMA200_PERIOD = 200
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
VOL_AVG_PERIOD = 20
ADX_PERIOD    = 14
ADX_TREND_MIN = 15   # ADX must exceed this to confirm a real trend
VIX_FEAR_THRESHOLD = 25.0      # block new entries when VIX closes above this
SPY_BUFFER = 1.03              # SPY must be this multiple above SMA200 for regime to be active
# -- paper_trader.py:85-89 -------------------------------------------------
WARMUP_DAYS   = 300  # ~200 trading days needed for SMA200 warmup
MAX_POSITIONS = 8    # max concurrent open positions
BEAR_REGIME_DAYS      = 10   # consecutive blocked days before switching to defensive mode
DEFENSIVE_UNIVERSE    = ["XLP", "XLU", "XLV", "GLD", "SHY"]
EARNINGS_HARD_BLOCK   = 5    # days — hard skip in entry loop before AI is called
# -- paper_trader.py:117 ---------------------------------------------------
MAX_PER_SECTOR = 1   # hard cap — only 1 position per sector at a time

# -- wheel_trader.py:76-80 -------------------------------------------------
WHEEL_UNIVERSE = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "META",
    "NVDA", "JPM",  "V",     "JNJ",  "WMT",
    "SPY",  "QQQ",  "GLD",   "XLE",  "AMD",
]

# -- wheel_trader.py:83-100 (RSI_PERIOD and WARMUP_DAYS renamed) ------------
WHEEL_RSI_PERIOD       = 14
RSI_MAX                = 60        # only sell puts when stock is NOT overbought
RSI_MIN                = 30        # avoid if deeply oversold (could keep falling)
IV_PERCENTILE_MIN      = 20        # want elevated IV for premium (approximated via ATR%)
MIN_OPTION_PREMIUM_PCT = 0.005     # minimum 0.5% of stock price as weekly premium (approx)
MAX_DELTA              = 0.35      # target 0.20–0.35 delta puts (approximated by strike selection)
TARGET_DELTA           = 0.25      # target put delta — 0.25 = ~75% probability of expiring worthless
DAYS_TO_EXPIRY         = 7         # target weekly options (next Friday)
WHEEL_WARMUP_DAYS      = 60        # days of history needed for indicators
SMA50_PERIOD           = 50        # trend filter — only sell puts above 50-day SMA
ATR_PERIOD             = 14        # for IV approximation and strike selection
STRIKE_ATR_MULT        = 0.5       # put strike = current price − (ATR × this multiple)
MAX_COLLATERAL_PCT     = 0.25      # max collateral per name as fraction of account equity
                                   # (strike × 100 × contracts must fit — caps assignment risk)
AUTO_COVERED_CALL      = False     # R4-02: dry-run mode. When False, assignment detection
                                   # still runs and notifies what WOULD be placed, but no
                                   # order is submitted. Flip to True after the first real
                                   # assignment's dry-run notification has been reviewed.

# -- wheel_trader.py:102-105 (renamed: they collide with ai_analyst's) ------
# AI thresholds — same pattern as ai_analyst.py
WHEEL_APPROVE_THRESHOLD = 75       # score ≥ 75 → log as recommended
WHEEL_REVIEW_THRESHOLD  = 50       # score 50–74 → log as pending review
                                   # score < 50 → reject

# -- wheel_trader.py:107-120 -----------------------------------------------
WHEEL_SECTOR_MAP: dict[str, str] = {
    "AAPL": "tech",  "MSFT": "tech",  "NVDA": "tech",  "AMD": "tech",
    "GOOGL": "tech", "META": "tech",  "AMZN": "tech",  "TSLA": "tech",
    "NFLX": "tech",  "ADBE": "tech",  "CRM": "tech",   "ORCL": "tech",
    "INTC": "tech",  "QCOM": "tech",  "TXN": "tech",   "AVGO": "tech",
    "JPM": "finance","BAC": "finance","GS": "finance",  "MS": "finance",
    "WFC": "finance","C": "finance",  "BLK": "finance", "BX": "finance",
    "XOM": "energy", "CVX": "energy", "COP": "energy",  "SLB": "energy",
    "XLE": "energy", "OXY": "energy",
    "JNJ": "health", "UNH": "health", "PFE": "health",  "MRK": "health",
    "ABT": "health", "TMO": "health", "MDT": "health",  "LLY": "health",
    "SPY": "broad",  "QQQ": "broad",  "GLD": "commodity",
}
WHEEL_MAX_PER_SECTOR = 2   # allow up to 2 per sector for wheel (more diversified than swing)

# -- ai_analyst.py:41-43 (renamed) -----------------------------------------
AI_RSI_PERIOD       = 14
AI_APPROVE_THRESHOLD = 75
AI_REVIEW_THRESHOLD  = 50

# -- iv_collector.py:50-52 -------------------------------------------------
TARGET_DTE       = 30    # sample the expiry nearest 30 days out
RANK_WINDOW      = 252   # trailing rows (~52 weeks) for IV Rank
MIN_RANK_ROWS    = 60    # below this, _load_iv_rank returns None (insufficient history)

# -- calibration.py:22-25 --------------------------------------------------
GATE_TRADES      = 20    # closed scored trades needed for the go-live calibration call
MIN_BUCKET_N     = 5     # below this a bucket is statistically meaningless

BUCKETS = [(50, 64), (65, 74), (75, 84), (85, 100)]

# -- hoisted locals ----------------------------------------------------------
# wheel_trader.py:892-893 (get_candidates)
ETF_NO_EARNINGS = {"SPY", "QQQ", "GLD", "XLE", "IWM", "DIA", "VXX"}
YFINANCE_FLAKY  = {"NVDA", "TSLA", "AMD"}  # intermittent after-hours data issues
# wheel_trader.py:785 (next_friday)
MIN_DTE = 7
# wheel_trader.py:590-602 (place_put_order's limit-price ladder)
LIMIT_BID_MIN = 0.05       # a live bid above this prices the order
LIMIT_BID_MULT = 0.95      # live bid × 0.95
LIMIT_CLOSE_MULT = 0.90    # else stale close × 0.90, else est_premium
# app.py:1341-1342 (api_check_wheel_positions)
AUTO_CLOSE_ITM_PCT  = 10.0   # ≥10% ITM at any DTE → close immediately
AUTO_CLOSE_DTE2_PCT = 5.0    # ≥5% ITM with ≤2 DTE → not recovering in time
# spec §5.2: the IV snapshot runs once per session at or after 09:15 ET
IV_SNAPSHOT_TIME_ET = "09:15"

# -- strategy headers (spec §5.1 / §5.2) ------------------------------------
#: StrategySwing's DEFAULTS. The INTELLISTOCK_SCHEMA header of
#: backend/strategies/strategy_swing.py is generated from this dict by
#: scripts/strategy_swing_sync_schema.py and a test pins them equal.
SWING_DEFAULTS = {
    "strategy_swing_enabled": False,
    "rsi_period": RSI_PERIOD,
    "rsi_entry_max": RSI_OVERSOLD,
    "rsi_overbought": RSI_OVERBOUGHT,
    "sma_long": SMA200_PERIOD,
    "macd_fast": MACD_FAST,
    "macd_slow": MACD_SLOW,
    "macd_signal": MACD_SIGNAL,
    "vol_avg_period": VOL_AVG_PERIOD,
    "adx_period": ADX_PERIOD,
    "adx_min": ADX_TREND_MIN,
    "spy_buffer": SPY_BUFFER,
    "vix_max": VIX_FEAR_THRESHOLD,
    "position_size_pct": POSITION_SIZE_PCT,
    "max_positions": MAX_POSITIONS,
    "max_per_sector": MAX_PER_SECTOR,
    "profit_target": PROFIT_TARGET,
    "stop_loss": STOP_LOSS,
    "bear_regime_days": BEAR_REGIME_DAYS,
    "defensive_universe": list(DEFENSIVE_UNIVERSE),
    "earnings_hard_block_days": EARNINGS_HARD_BLOCK,
    "ai_gate_enabled": True,
    "ai_approve_threshold": AI_APPROVE_THRESHOLD,
    "ai_review_threshold": AI_REVIEW_THRESHOLD,
    "conviction_llm_model_id": "",
    "scan_time_et": "09:15",
    # The live envelope, in EB's form (spec §5.1). The lane is sized at 12.5%
    # per name, so 20% per order and per symbol only ever refuses a mistake;
    # the drawdown rungs are EB's. The swing universe holds no leveraged ETF;
    # 0.2 matches plan A-live's `_SW_DEFAULTS` row in defaults_by_lane.
    "live_max_order_fraction": 0.2,
    "live_max_symbol_fraction": 0.2,
    "live_max_leveraged_fraction": 0.2,
    "live_soft_drawdown": 0.25,
    "live_hard_drawdown": 0.35,
    "live_kill_drawdown": 0.45,
    # BROKER-side keys (backtest_engine / broker read them off the lane).
    "honour_single_position_cap": True,
    "broker_max_single_position_pct": 0.2,
}

#: StrategyWheel's DEFAULTS; same generation and pin as SWING_DEFAULTS.
WHEEL_DEFAULTS = {
    "strategy_wheel_enabled": False,
    "rsi_min": RSI_MIN,
    "rsi_max": RSI_MAX,
    "sma_trend": SMA50_PERIOD,
    "atr_period": ATR_PERIOD,
    "strike_atr_mult": STRIKE_ATR_MULT,
    "min_premium_pct": MIN_OPTION_PREMIUM_PCT,
    "target_delta": TARGET_DELTA,
    "days_to_expiry": DAYS_TO_EXPIRY,
    "max_collateral_pct": MAX_COLLATERAL_PCT,
    "max_per_sector": WHEEL_MAX_PER_SECTOR,
    "auto_covered_call": AUTO_COVERED_CALL,
    "approve_threshold": WHEEL_APPROVE_THRESHOLD,
    "review_threshold": WHEEL_REVIEW_THRESHOLD,
    "earnings_block_days": 7,
    "limit_bid_mult": LIMIT_BID_MULT,
    "scan_weekday": 0,
    "scan_time_et": "10:30",
    "monitor_time_et": "15:45",
    "conviction_llm_model_id": "",
}
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `python3 -m pytest backend/tests/test_swing_constants.py -q -p no:cacheprovider`
Expected: PASS (5 tests).

- [ ] **Step 7: Commit**

Run `gitnexus_detect_changes()`; expect new files only.

```bash
git add backend/swing_trader/__init__.py backend/swing_trader/constants.py backend/tests/fixtures/swing_trader_st/paper_trader_pure.py backend/tests/fixtures/swing_trader_st/wheel_trader_pure.py backend/tests/test_swing_constants.py
git commit -m "feat(swing): ST constants, header defaults and the vendored parity oracle

Every swing and wheel constant is ST's value; the header defaults map them
onto the spec's key names. The vendored ST source under tests/fixtures is
the oracle every parity test compares against.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016ztXjLWXu61Dpp4XMU2jsS"
```

---

### Task 3: Indicators with parity to ST (B2)

**Files:**
- Create: `backend/swing_trader/indicators.py`
- Test: `backend/tests/test_swing_indicators.py`

**Interfaces:**
- Consumes: `swing_trader.constants` (Task 2); vendored `paper_trader_pure`, `wheel_trader_pure` (Task 2).
- Produces:
  - `_rsi(series, period=14) -> pd.Series`, `_macd(series, fast=12, slow=26, signal=9) -> (line, sig)`, `_sma(series, period) -> pd.Series`, `_adx(high, low, close, period=14) -> pd.Series`, `_atr(high, low, close, period=14) -> pd.Series` (verbatim ST).
  - `rsi_last(series, period=14) -> float | None` (ST `ai_analyst._rsi`).
  - `bars_to_long_frame(bars: dict[str, pd.DataFrame]) -> pd.DataFrame` (columns lower-cased + `symbol`, `date`).
  - `get_latest_indicators(df, *, rsi_period, macd_fast, macd_slow, macd_signal, sma_period, vol_avg_period, adx_period) -> dict[str, dict]` with keys `close, volume, rsi, rsi_prev, macd_hist, macd_hist_prev, macd_hist_prev2, sma200, vol_avg20, adx` (floats or None).
  - `indicators_for_frames(frames: dict[str, pd.DataFrame], cfg: dict | None = None) -> dict[str, dict]` — the same snapshot, reading the periods from a swing config.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_swing_indicators.py`:

```python
"""Indicator parity with ST, and ST's own NaN-close tests
(ST tests/test_indicators_nan.py, ported)."""
import importlib.util
import math
import os
import sys

import numpy as np
import pandas as pd

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from swing_trader import indicators as ind  # noqa: E402

_ST_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "fixtures", "swing_trader_st")


def _st(name):
    spec = importlib.util.spec_from_file_location(
        f"_swing_st_{name}", os.path.join(_ST_DIR, f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _ohlcv(seed, n=260, nan_tail=False):
    """A seeded daily OHLCV frame shaped like market_data.get_daily_bars."""
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0.0005, 0.02, n)))
    high = close * (1 + rng.uniform(0.0, 0.02, n))
    low = close * (1 - rng.uniform(0.0, 0.02, n))
    vol = rng.integers(500_000, 2_000_000, n).astype(float)
    if nan_tail:
        close = close.copy()
        close[-1] = np.nan
    idx = pd.date_range("2024-01-01", periods=n, freq="B")
    return pd.DataFrame({"Open": close, "High": high, "Low": low,
                         "Close": close, "Volume": vol}, index=idx)


def _same(a, b):
    if a is None or b is None:
        return a is None and b is None
    return math.isclose(a, b, rel_tol=0.0, abs_tol=1e-12)


def test_get_latest_indicators_matches_st_on_seeded_frames():
    st = _st("paper_trader_pure")
    frames = {f"S{i}": _ohlcv(i, nan_tail=(i == 3)) for i in range(6)}
    long = ind.bars_to_long_frame(frames)
    ours = ind.get_latest_indicators(long)
    theirs = st.get_latest_indicators(long)
    assert set(ours) == set(theirs) == set(frames)
    for sym in frames:
        for key, value in theirs[sym].items():
            assert _same(ours[sym][key], value), (sym, key)


def test_indicators_for_frames_equals_the_long_frame_path():
    frames = {f"S{i}": _ohlcv(10 + i) for i in range(4)}
    one_by_one = ind.indicators_for_frames(frames, {})
    all_at_once = ind.get_latest_indicators(ind.bars_to_long_frame(frames))
    assert one_by_one == all_at_once


def test_indicators_for_frames_reads_the_periods_from_config():
    frames = {"S1": _ohlcv(7)}
    base = ind.indicators_for_frames(frames, {})
    fast = ind.indicators_for_frames(frames, {"rsi_period": 7})
    assert base["S1"]["rsi"] != fast["S1"]["rsi"]
    assert base["S1"]["sma200"] == fast["S1"]["sma200"]


def test_primitive_indicators_are_verbatim():
    st = _st("paper_trader_pure")
    wst = _st("wheel_trader_pure")
    f = _ohlcv(42)
    c, h, lo = f["Close"], f["High"], f["Low"]
    pd.testing.assert_series_equal(ind._rsi(c, 14), st._rsi(c, 14))
    pd.testing.assert_series_equal(ind._macd(c)[0], st._macd(c)[0])
    pd.testing.assert_series_equal(ind._macd(c)[1], st._macd(c)[1])
    pd.testing.assert_series_equal(ind._sma(c, 200), st._sma(c, 200))
    pd.testing.assert_series_equal(ind._adx(h, lo, c, 14), st._adx(h, lo, c, 14))
    pd.testing.assert_series_equal(ind._atr(h, lo, c), wst._atr(h, lo, c))
    pd.testing.assert_series_equal(ind._rsi(c), wst._rsi(c))


def test_rsi_last_is_ai_analysts_scalar_rsi():
    c = _ohlcv(3)["Close"]
    assert math.isclose(ind.rsi_last(c), float(ind._rsi(c, 14).iloc[-1]))
    assert ind.rsi_last(c.iloc[:10]) is None


# -- ST tests/test_indicators_nan.py, ported ---------------------------------

def _bars(symbol, closes):
    n = len(closes)
    return pd.DataFrame({
        "symbol": [symbol] * n,
        "date": pd.date_range("2024-01-01", periods=n),
        "close": [float(c) for c in closes],
        "high": [(float(c) + 1.0) if not pd.isna(c) else c for c in closes],
        "low": [(float(c) - 1.0) if not pd.isna(c) else c for c in closes],
        "volume": [1_000_000] * n,
    })


def test_trailing_nan_close_falls_back_to_last_completed_bar():
    closes = [100.0 + i for i in range(250)] + [np.nan]
    out = ind.get_latest_indicators(_bars("AMGN", closes))
    assert out["AMGN"]["close"] == 349.0
    assert not pd.isna(out["AMGN"]["close"])


def test_symbol_with_no_usable_close_is_skipped():
    out = ind.get_latest_indicators(_bars("ZZZ", [np.nan, np.nan, np.nan]))
    assert "ZZZ" not in out


def test_mixed_symbols_only_drops_the_unusable_one():
    df = pd.concat([_bars("GOOD", [100.0 + i for i in range(250)]),
                    _bars("BAD", [np.nan, np.nan])], ignore_index=True)
    out = ind.get_latest_indicators(df)
    assert "GOOD" in out and "BAD" not in out
    assert out["GOOD"]["close"] == 349.0


def test_bars_to_long_frame_shape():
    long = ind.bars_to_long_frame({"AAA": _ohlcv(1, n=5)})
    assert {"open", "high", "low", "close", "volume", "symbol", "date"} <= set(long.columns)
    assert list(long["symbol"].unique()) == ["AAA"]
    assert ind.bars_to_long_frame({}).empty
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest backend/tests/test_swing_indicators.py -q -p no:cacheprovider`
Expected: FAIL — `ImportError: cannot import name 'indicators' from 'swing_trader'`.

- [ ] **Step 3: Write the implementation**

Create `backend/swing_trader/indicators.py`:

```python
"""Indicators, ported from ST.

    paper_trader.py:156-201  _rsi, _macd, _sma, _adx            (verbatim)
    paper_trader.py:219-232  the long-frame reshape in fetch_bars -> bars_to_long_frame
    paper_trader.py:237-272  get_latest_indicators (periods lifted to keyword
                             arguments whose defaults are ST's constants)
    wheel_trader.py:734-741  _atr                                (verbatim)
    ai_analyst.py:111-119    _rsi returning the last value       -> rsi_last

wheel_trader.py:720-731 re-declares _rsi and _sma with identical bodies, so
the wheel reuses these.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from swing_trader.constants import (
    ADX_PERIOD,
    ATR_PERIOD,
    MACD_FAST,
    MACD_SIGNAL,
    MACD_SLOW,
    RSI_PERIOD,
    SMA200_PERIOD,
    VOL_AVG_PERIOD,
)


def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100.0 - (100.0 / (1.0 + rs))


def _macd(series: pd.Series, fast=12, slow=26, signal=9):
    ema_f = series.ewm(span=fast, adjust=False).mean()
    ema_s = series.ewm(span=slow, adjust=False).mean()
    line = ema_f - ema_s
    sig = line.ewm(span=signal, adjust=False).mean()
    return line, sig


def _sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period).mean()


def _adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Average Directional Index — measures trend strength (not direction)."""
    prev_close = close.shift(1)
    prev_low   = low.shift(1)
    prev_high  = high.shift(1)

    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs(),
    ], axis=1).max(axis=1)

    dm_plus  = np.where((high - prev_high) > (prev_low - low), (high - prev_high).clip(lower=0), 0.0)
    dm_minus = np.where((prev_low - low) > (high - prev_high), (prev_low - low).clip(lower=0), 0.0)

    dm_plus  = pd.Series(dm_plus,  index=close.index)
    dm_minus = pd.Series(dm_minus, index=close.index)

    atr      = tr.ewm(com=period - 1, min_periods=period).mean()
    di_plus  = 100 * dm_plus.ewm(com=period - 1,  min_periods=period).mean() / atr
    di_minus = 100 * dm_minus.ewm(com=period - 1, min_periods=period).mean() / atr

    dx = (100 * (di_plus - di_minus).abs() / (di_plus + di_minus).replace(0, np.nan))
    return dx.ewm(com=period - 1, min_periods=period).mean()


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = ATR_PERIOD) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(com=period - 1, min_periods=period).mean()


def rsi_last(series: pd.Series, period: int = 14) -> float | None:
    """ai_analyst.py:111-119, where ST named it _rsi."""
    if len(series) < period + 2:
        return None
    d = series.diff()
    g = d.clip(lower=0).ewm(com=period - 1, min_periods=period).mean()
    l = (-d).clip(lower=0).ewm(com=period - 1, min_periods=period).mean()
    rs = g / l.replace(0, np.nan)
    val = (100.0 - 100.0 / (1.0 + rs)).iloc[-1]
    return float(val) if not np.isnan(val) else None


def bars_to_long_frame(bars: dict) -> pd.DataFrame:
    """paper_trader.py:219-232: {symbol: OHLCV frame} -> ST's long format."""
    all_parts = []
    for sym, sdf in bars.items():
        part = sdf.copy().dropna(how="all")
        part.columns = [c.lower() for c in part.columns]
        part["symbol"] = sym
        part["date"] = pd.to_datetime(part.index).normalize()
        part = part.reset_index(drop=True)
        all_parts.append(part)
    if not all_parts:
        return pd.DataFrame()
    return pd.concat(all_parts, ignore_index=True)


def get_latest_indicators(df: pd.DataFrame, *, rsi_period: int = RSI_PERIOD,
                          macd_fast: int = MACD_FAST, macd_slow: int = MACD_SLOW,
                          macd_signal: int = MACD_SIGNAL,
                          sma_period: int = SMA200_PERIOD,
                          vol_avg_period: int = VOL_AVG_PERIOD,
                          adx_period: int = ADX_PERIOD) -> dict:
    """Return dict of symbol → latest indicator snapshot."""
    result = {}
    for symbol in df["symbol"].unique():
        sdf = df[df["symbol"] == symbol].copy().sort_values("date").reset_index(drop=True)
        # yfinance can return a NaN close for today's forming bar near the open.
        # float(nan) does not raise, so an unguarded close flows through as
        # now=$nan and (for SPY) SPY $nan in the regime line. Drop NaN-close rows
        # to fall back to the last completed bar; skip the symbol entirely if
        # nothing usable remains (handled downstream as "no indicator data").
        sdf = sdf[sdf["close"].notna()]
        if sdf.empty:
            continue
        sdf["rsi"] = _rsi(sdf["close"], rsi_period)
        macd_line, macd_sig = _macd(sdf["close"], macd_fast, macd_slow, macd_signal)
        sdf["macd_hist"]       = macd_line - macd_sig
        sdf["macd_hist_prev"]  = sdf["macd_hist"].shift(1)
        sdf["macd_hist_prev2"] = sdf["macd_hist"].shift(2)
        sdf["sma200"]    = _sma(sdf["close"], sma_period)
        sdf["vol_avg20"] = sdf["volume"].rolling(vol_avg_period).mean()
        sdf["rsi_prev"]  = sdf["rsi"].shift(1)
        sdf["adx"]       = _adx(sdf["high"], sdf["low"], sdf["close"], adx_period)
        last = sdf.iloc[-1]
        result[symbol] = {
            "close":           float(last["close"]),
            "volume":          float(last["volume"]),
            "rsi":             float(last["rsi"])             if not pd.isna(last["rsi"])             else None,
            "rsi_prev":        float(last["rsi_prev"])        if not pd.isna(last["rsi_prev"])        else None,
            "macd_hist":       float(last["macd_hist"])       if not pd.isna(last["macd_hist"])       else None,
            "macd_hist_prev":  float(last["macd_hist_prev"])  if not pd.isna(last["macd_hist_prev"])  else None,
            "macd_hist_prev2": float(last["macd_hist_prev2"]) if not pd.isna(last["macd_hist_prev2"]) else None,
            "sma200":          float(last["sma200"])          if not pd.isna(last["sma200"])          else None,
            "vol_avg20":       float(last["vol_avg20"])       if not pd.isna(last["vol_avg20"])       else None,
            "adx":             float(last["adx"])             if not pd.isna(last["adx"])             else None,
        }
    return result


def indicators_for_frames(frames: dict, cfg: dict | None = None) -> dict:
    """ST's fetch_bars reshape + get_latest_indicators, one symbol at a time.

    ST filters one long frame per symbol, which is O(symbols × rows) over a
    500-name universe; the snapshot per symbol is identical either way (pinned
    by test_indicators_for_frames_equals_the_long_frame_path).
    """
    cfg = cfg or {}
    kw = {
        "rsi_period": int(cfg.get("rsi_period", RSI_PERIOD)),
        "macd_fast": int(cfg.get("macd_fast", MACD_FAST)),
        "macd_slow": int(cfg.get("macd_slow", MACD_SLOW)),
        "macd_signal": int(cfg.get("macd_signal", MACD_SIGNAL)),
        "sma_period": int(cfg.get("sma_long", SMA200_PERIOD)),
        "vol_avg_period": int(cfg.get("vol_avg_period", VOL_AVG_PERIOD)),
        "adx_period": int(cfg.get("adx_period", ADX_PERIOD)),
    }
    out = {}
    for symbol, frame in (frames or {}).items():
        if frame is None or len(frame) == 0:
            continue
        long = bars_to_long_frame({symbol: frame})
        if long.empty or "close" not in long.columns:
            continue
        out.update(get_latest_indicators(long, **kw))
    return out
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 -m pytest backend/tests/test_swing_indicators.py -q -p no:cacheprovider`
Expected: PASS (9 tests).

- [ ] **Step 5: Commit**

Run `gitnexus_detect_changes()`; expect new files only.

```bash
git add backend/swing_trader/indicators.py backend/tests/test_swing_indicators.py
git commit -m "feat(swing): port ST indicators with parity tests

RSI, MACD, SMA, ADX and ATR are ST's code; get_latest_indicators keeps the
NaN-close guard and lifts its periods to keyword arguments. Parity against
the vendored ST source on seeded frames, plus ST's own NaN tests.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016ztXjLWXu61Dpp4XMU2jsS"
```

---

### Task 4: Signals, regime and the per-session bear counter (B3)

**Files:**
- Create: `backend/swing_trader/signals.py`
- Create: `backend/swing_trader/regime.py`
- Test: `backend/tests/test_swing_signals.py`
- Test: `backend/tests/test_swing_regime_logic.py`

**Interfaces:**
- Consumes: `swing_trader.constants` (Task 2); vendored `paper_trader_pure` (Task 2).
- Produces:
  - `signals.entry_signal(ind, *, rsi_oversold=50, adx_trend_min=15, profit_target=0.09, stop_loss=0.06) -> bool`
  - `signals.exit_signal(ind, entry_price, *, profit_target=0.09, stop_loss=0.06, rsi_overbought=70) -> tuple[bool, str | None]`; reasons `"profit_target" | "stop_loss" | "rsi_overbought"`.
  - `signals.sector_conflict(symbol, active_positions, *, sector_of, max_per_sector=1) -> str | None`
  - `signals.update_regime_tracker(state: dict | None, regime_ok: bool, session: str) -> dict` returning `{"session": str, "blocked_days": int}`.
  - `signals.select_entry_universe(regime_ok, blocked_days, *, bear_regime_days, live_universe, defensive_universe) -> tuple[list | None, str]`; phase `"regime_ok" | "bear_mode" | "blocked"`.
  - `signals.entry_kwargs(cfg) -> dict`, `signals.exit_kwargs(cfg) -> dict` (config → the keyword arguments above).
  - `regime.regime_decision(spy_close, spy_sma200, vix_close, *, spy_buffer=1.03, vix_max=25.0) -> dict` with keys `spy_close, spy_sma200, vix, spy_ok, vix_ok, regime_ok, entries_allowed, blocked_reason`.
  - `regime.fetch_vix_close() -> float | None` (live; yfinance, as ST).

- [ ] **Step 1: Write the failing signal tests**

Create `backend/tests/test_swing_signals.py`:

```python
"""Swing signal parity with ST paper_trader.py, fix 4's sector semantics, and
fix 11 (the bear counter advances once per session, not per run)."""
import importlib.util
import os
import sys
import types

import numpy as np

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from swing_trader import signals  # noqa: E402

_ST_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "fixtures", "swing_trader_st")
KEYS = ("close", "volume", "rsi", "rsi_prev", "macd_hist", "macd_hist_prev",
        "macd_hist_prev2", "sma200", "vol_avg20", "adx")


def _st():
    spec = importlib.util.spec_from_file_location(
        "_swing_st_paper", os.path.join(_ST_DIR, "paper_trader_pure.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _random_ind(rng):
    ind = {
        "close": float(rng.uniform(50, 150)), "volume": float(rng.uniform(1e5, 3e6)),
        "rsi": float(rng.uniform(20, 80)), "rsi_prev": float(rng.uniform(20, 80)),
        "macd_hist": float(rng.normal()), "macd_hist_prev": float(rng.normal()),
        "macd_hist_prev2": float(rng.normal()), "sma200": float(rng.uniform(50, 150)),
        "vol_avg20": float(rng.uniform(1e5, 3e6)), "adx": float(rng.uniform(5, 40)),
    }
    if rng.random() < 0.1:
        ind[KEYS[int(rng.integers(0, len(KEYS)))]] = None
    return ind


def test_entry_signal_matches_st():
    st, rng = _st(), np.random.default_rng(0)
    hits = 0
    for _ in range(3000):
        ind = _random_ind(rng)
        ours = signals.entry_signal(ind)
        assert ours == st.entry_signal(ind)
        hits += ours
    assert hits > 0, "fixture never produced an entry; widen the ranges"


def test_exit_signal_matches_st():
    st, rng = _st(), np.random.default_rng(1)
    for _ in range(3000):
        ind = _random_ind(rng)
        if ind["close"] is None:
            continue
        ind["rsi_prev"] = float(rng.uniform(60, 75)) if rng.random() < 0.5 else ind["rsi_prev"]
        entry = float(ind["close"] * rng.uniform(0.85, 1.15))
        assert signals.exit_signal(ind, entry) == st.exit_signal(ind, entry)


def test_config_thresholds_change_the_verdict():
    ind = {"close": 100.0, "volume": 2e6, "rsi": 55.0, "rsi_prev": 50.0,
           "macd_hist": 0.2, "macd_hist_prev": 0.1, "macd_hist_prev2": 0.0,
           "sma200": 90.0, "vol_avg20": 1e6, "adx": 20.0}
    assert signals.entry_signal(ind) is False
    assert signals.entry_signal(ind, rsi_oversold=60) is True
    kw = signals.entry_kwargs({"rsi_entry_max": 60, "adx_min": 15,
                               "profit_target": 0.09, "stop_loss": 0.06})
    assert signals.entry_signal(ind, **kw) is True
    assert signals.exit_kwargs({"profit_target": 0.2, "stop_loss": 0.1,
                                "rsi_overbought": 80}) == {
        "profit_target": 0.2, "stop_loss": 0.1, "rsi_overbought": 80}


def test_sector_conflict_matches_st():
    st = _st()
    info = {"AAPL": "Technology", "MSFT": "Technology", "JPM": "Financial Services",
            "XOM": "Energy", "ZZZ": None}

    class _Ticker:
        def __init__(self, symbol):
            self.info = ({"sector": info[symbol]} if info.get(symbol) else {})

    st.yf = types.SimpleNamespace(Ticker=_Ticker)
    for sym in info:
        for active in ({"MSFT"}, {"JPM"}, {"XOM", "MSFT"}, set(), {"SPY"}):
            assert signals.sector_conflict(
                sym, active, sector_of=st.get_symbol_sector) == st.sector_conflict(sym, active)


def test_a_higher_sector_cap_admits_a_second_name():
    sector = {"AAPL": "technology", "MSFT": "technology", "NVDA": "technology"}.get
    assert signals.sector_conflict("NVDA", {"AAPL"}, sector_of=sector,
                                   max_per_sector=2) is None
    assert signals.sector_conflict("NVDA", {"AAPL", "MSFT"}, sector_of=sector,
                                   max_per_sector=2) in {"AAPL", "MSFT"}


def test_unknown_sector_is_never_a_conflict():
    assert signals.sector_conflict("X", {"Y"}, sector_of=lambda s: "unknown") is None


def test_bear_counter_matches_st_with_one_run_per_session(tmp_path):
    st = _st()
    st.REGIME_TRACKER_FILE = str(tmp_path / "regime_tracker.json")
    state = None
    for i, ok in enumerate([False, False, True, False, False, False]):
        theirs = st.update_regime_tracker(ok)
        state = signals.update_regime_tracker(state, ok, f"2026-06-0{i + 1}")
        assert state["blocked_days"] == theirs


def test_fix_11_a_second_tick_in_the_same_session_does_not_count(tmp_path):
    st = _st()
    st.REGIME_TRACKER_FILE = str(tmp_path / "regime_tracker.json")
    st.update_regime_tracker(False)
    assert st.update_regime_tracker(False) == 2      # ST counted runs
    state = signals.update_regime_tracker(None, False, "2026-06-01")
    state = signals.update_regime_tracker(state, False, "2026-06-01")
    assert state == {"session": "2026-06-01", "blocked_days": 1}
    state = signals.update_regime_tracker(state, False, "2026-06-02")
    assert state["blocked_days"] == 2
    assert signals.update_regime_tracker(state, True, "2026-06-03")["blocked_days"] == 0


def test_select_entry_universe_follows_paper_trader_600_626():
    live, defensive = ["AAA", "BBB"], ["XLP", "GLD"]
    kw = {"bear_regime_days": 10, "live_universe": live, "defensive_universe": defensive}
    assert signals.select_entry_universe(True, 0, **kw) == (live, "regime_ok")
    assert signals.select_entry_universe(False, 9, **kw) == (None, "blocked")
    assert signals.select_entry_universe(False, 10, **kw) == (defensive, "bear_mode")
```

- [ ] **Step 2: Write the failing regime tests**

Create `backend/tests/test_swing_regime_logic.py`:

```python
"""fetch_regime's decision logic (ST tests/test_regime_logic.py, ported to the
pure decision) and the live VIX read with ST's NaN guard."""
import math
import os
import sys
import types

import pandas as pd

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from swing_trader import regime  # noqa: E402

# ST's reference datasets: 299 bars at 400 then 430 -> SMA200 ~= 400.15,
# 430 > 412.15 (allowed); 300 bars at 400 -> 400 > 412 is False (blocked).
ALLOWED = (430.0, (199 * 400.0 + 430.0) / 200)
BLOCKED = (400.0, 400.0)


def test_entries_allowed_when_spy_above_buffer_and_vix_low():
    r = regime.regime_decision(*ALLOWED, 15.0)
    assert r["entries_allowed"] is True and r["blocked_reason"] is None


def test_blocked_when_spy_below_buffer():
    r = regime.regime_decision(*BLOCKED, 15.0)
    assert r["entries_allowed"] is False
    assert r["blocked_reason"] == "SPY below SMA200×1.03"


def test_blocked_when_vix_too_high():
    r = regime.regime_decision(*ALLOWED, 30.0)
    assert r["entries_allowed"] is False and r["blocked_reason"] == "VIX > 25.0"


def test_blocked_when_both_conditions_fail():
    r = regime.regime_decision(*BLOCKED, 30.0)
    assert r["blocked_reason"] == "SPY below SMA200×1.03 and VIX > 25"


def test_vix_exactly_at_threshold_allows_entries():
    assert regime.regime_decision(*ALLOWED, 25.0)["entries_allowed"] is True


def test_missing_or_nan_inputs_block_and_never_propagate_nan():
    for spy, sma, vix in ((None, 400.0, 15.0), (430.0, None, 15.0),
                          (430.0, 400.0, None), (float("nan"), 400.0, 15.0),
                          (430.0, 400.0, float("nan"))):
        r = regime.regime_decision(spy, sma, vix)
        assert r["regime_ok"] is False
        for key in ("spy_close", "spy_sma200", "vix"):
            assert r[key] is None or math.isfinite(r[key])


def test_config_buffer_and_vix_max_are_honoured():
    assert regime.regime_decision(410.0, 400.0, 15.0)["regime_ok"] is False
    assert regime.regime_decision(410.0, 400.0, 15.0, spy_buffer=1.02)["regime_ok"] is True
    assert regime.regime_decision(*ALLOWED, 28.0, vix_max=30.0)["regime_ok"] is True


def _yf(closes=None, error=None):
    class _T:
        def __init__(self, symbol):
            assert symbol == "^VIX"

        def history(self, period, interval):
            if error:
                raise error
            return pd.DataFrame({"Close": closes or []})
    return types.SimpleNamespace(Ticker=_T)


def test_fetch_vix_close_drops_a_trailing_nan(monkeypatch):
    monkeypatch.setattr(regime, "yf", _yf([14.0, 15.5, float("nan")]))
    assert regime.fetch_vix_close() == 15.5


def test_fetch_vix_close_is_none_on_empty_all_nan_or_error(monkeypatch):
    monkeypatch.setattr(regime, "yf", _yf([]))
    assert regime.fetch_vix_close() is None
    monkeypatch.setattr(regime, "yf", _yf([float("nan")]))
    assert regime.fetch_vix_close() is None
    monkeypatch.setattr(regime, "yf", _yf(error=RuntimeError("down")))
    assert regime.fetch_vix_close() is None
```

- [ ] **Step 3: Run them to verify they fail**

Run: `python3 -m pytest backend/tests/test_swing_signals.py backend/tests/test_swing_regime_logic.py -q -p no:cacheprovider`
Expected: FAIL — `ImportError: cannot import name 'signals' from 'swing_trader'`.

- [ ] **Step 4: Write signals.py**

Create `backend/swing_trader/signals.py`:

```python
"""Swing signals, ported from ST paper_trader.py.

    paper_trader.py:293-304  entry_signal     verbatim; thresholds lifted to kwargs
    paper_trader.py:307-326  exit_signal      verbatim; thresholds lifted to kwargs
    paper_trader.py:329-340  sector_conflict  the sector lookup is injected (a
                             stored map, spec §9 item 15) and a per-sector cap
                             other than ST's 1 is honoured
    paper_trader.py:135-149  update_regime_tracker, counted per NY SESSION
                             instead of per run (spec §9 fix 11)
    paper_trader.py:600-626  which universe the entry pass scans
"""
from __future__ import annotations

from swing_trader.constants import (
    ADX_TREND_MIN,
    MAX_PER_SECTOR,
    PROFIT_TARGET,
    RSI_OVERBOUGHT,
    RSI_OVERSOLD,
    STOP_LOSS,
)


def entry_signal(ind: dict, *, rsi_oversold=RSI_OVERSOLD, adx_trend_min=ADX_TREND_MIN,
                 profit_target=PROFIT_TARGET, stop_loss=STOP_LOSS) -> bool:
    if any(v is None for v in ind.values()):
        return False
    # RSI<50 + 1-bar MACD ("variant L") — only config positive in both halves of
    # the 2021-2026 S&P 500 sweep (experiments/combo_results.json, 2026-06-10).
    rsi_signal      = ind["rsi"] < rsi_oversold and ind["rsi"] > ind["rsi_prev"]
    macd_improving  = ind["macd_hist"] > ind["macd_hist_prev"]
    vol_above_avg   = ind["volume"] > ind["vol_avg20"]
    above_sma       = ind["close"] > ind["sma200"]
    trend_confirmed = ind["adx"] > adx_trend_min
    rr_ok           = (profit_target / stop_loss) >= 1.5
    return rsi_signal and macd_improving and vol_above_avg and above_sma and trend_confirmed and rr_ok


def exit_signal(ind: dict, entry_price: float, *, profit_target=PROFIT_TARGET,
                stop_loss=STOP_LOSS, rsi_overbought=RSI_OVERBOUGHT):
    """Returns (should_exit: bool, reason: str | None)."""
    price = ind["close"]
    pct = (price - entry_price) / entry_price

    if pct >= profit_target:
        return True, "profit_target"
    if pct <= -stop_loss:
        return True, "stop_loss"

    rsi_cross_ob = (
        ind["rsi_prev"] is not None
        and ind["rsi"] is not None
        and ind["rsi_prev"] < rsi_overbought
        and ind["rsi"] >= rsi_overbought
    )
    if rsi_cross_ob:
        return True, "rsi_overbought"

    return False, None


def sector_conflict(symbol: str, active_positions, *, sector_of,
                    max_per_sector: int = MAX_PER_SECTOR) -> str | None:
    """
    Returns the conflicting symbol if adding `symbol` would exceed MAX_PER_SECTOR
    for its sector, otherwise returns None.
    """
    candidate_sector = sector_of(symbol)
    if candidate_sector == "unknown":
        return None  # unknown sector — allow entry
    same = []
    for held in active_positions:
        if sector_of(held) == candidate_sector:
            same.append(held)
            if len(same) >= int(max_per_sector):
                return same[0]
    return None


def update_regime_tracker(state, regime_ok: bool, session: str) -> dict:
    """paper_trader.py:143-149 — `days = 0 if regime_ok else blocked + 1` — but
    keyed on the NY session: ST's cron re-ran on retries and restarts and each
    run advanced the counter (fix 11). The first evaluation of a session wins."""
    state = dict(state or {})
    if state.get("session") == session:
        return {"session": session, "blocked_days": int(state.get("blocked_days") or 0)}
    days = 0 if regime_ok else int(state.get("blocked_days") or 0) + 1
    return {"session": session, "blocked_days": days}


def select_entry_universe(regime_ok, blocked_days, *, bear_regime_days,
                          live_universe, defensive_universe):
    """paper_trader.py:602-626: the S&P list when the regime is on, the
    defensive ETFs once blocked for `bear_regime_days` sessions, else none."""
    if regime_ok:
        return list(live_universe), "regime_ok"
    if int(blocked_days) >= int(bear_regime_days):
        return list(defensive_universe), "bear_mode"
    return None, "blocked"


def entry_kwargs(cfg: dict) -> dict:
    return {"rsi_oversold": float(cfg.get("rsi_entry_max", RSI_OVERSOLD)),
            "adx_trend_min": float(cfg.get("adx_min", ADX_TREND_MIN)),
            "profit_target": float(cfg.get("profit_target", PROFIT_TARGET)),
            "stop_loss": float(cfg.get("stop_loss", STOP_LOSS))}


def exit_kwargs(cfg: dict) -> dict:
    return {"profit_target": float(cfg.get("profit_target", PROFIT_TARGET)),
            "stop_loss": float(cfg.get("stop_loss", STOP_LOSS)),
            "rsi_overbought": float(cfg.get("rsi_overbought", RSI_OVERBOUGHT))}
```

Note on `exit_kwargs`: the test compares with `{"profit_target": 0.2, "stop_loss": 0.1, "rsi_overbought": 80}`; `80 == 80.0` holds, so float coercion is fine.

- [ ] **Step 5: Write regime.py**

Create `backend/swing_trader/regime.py`:

```python
"""Market regime, ported from ST.

    paper_trader.py:478-480  spy_ok / vix_ok / regime_ok     (verbatim, config-driven)
    app.py:487-502           the blocked_reason strings of fetch_regime
    paper_trader.py:279-286  fetch_vix_close, with fetch_regime's NaN guard
                             (app.py:447-456) — ST's own later fix for the
                             same read.
Backtests do not call fetch_vix_close: they read SwingMacroDaily strictly
before the NY date (refdata.vix_before).
"""
from __future__ import annotations

import math

import yfinance as yf

from swing_trader.constants import SPY_BUFFER, VIX_FEAR_THRESHOLD


def _finite(value):
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def regime_decision(spy_close, spy_sma200, vix_close, *, spy_buffer=SPY_BUFFER,
                    vix_max=VIX_FEAR_THRESHOLD) -> dict:
    spy_close, spy_sma200, vix_close = (_finite(spy_close), _finite(spy_sma200),
                                        _finite(vix_close))
    spy_ok     = spy_close is not None and spy_sma200 is not None and spy_close > spy_sma200 * spy_buffer
    vix_ok     = vix_close is not None and vix_close <= vix_max
    regime_ok  = spy_ok and vix_ok
    if spy_ok and vix_ok:
        blocked_reason = None
    elif not spy_ok and not vix_ok:
        blocked_reason = f"SPY below SMA200×{spy_buffer} and VIX > {vix_max:g}"
    elif not spy_ok:
        blocked_reason = f"SPY below SMA200×{spy_buffer}"
    else:
        blocked_reason = f"VIX > {vix_max}"
    return {"spy_close": spy_close, "spy_sma200": spy_sma200, "vix": vix_close,
            "spy_ok": bool(spy_ok), "vix_ok": bool(vix_ok),
            "regime_ok": bool(regime_ok), "entries_allowed": bool(regime_ok),
            "blocked_reason": blocked_reason}


def fetch_vix_close() -> float | None:
    try:
        raw = yf.Ticker("^VIX").history(period="5d", interval="1d")
        if raw.empty:
            return None
        vix_close = raw["Close"].dropna()
        if vix_close.empty:
            return None
        val = float(vix_close.iloc[-1])
        return val if math.isfinite(val) else None
    except Exception:
        return None
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python3 -m pytest backend/tests/test_swing_signals.py backend/tests/test_swing_regime_logic.py -q -p no:cacheprovider`
Expected: PASS (18 tests).

- [ ] **Step 7: Commit**

Run `gitnexus_detect_changes()`; expect new files only.

```bash
git add backend/swing_trader/signals.py backend/swing_trader/regime.py backend/tests/test_swing_signals.py backend/tests/test_swing_regime_logic.py
git commit -m "feat(swing): port entry, exit, sector and regime rules

entry_signal, exit_signal and sector_conflict match ST on seeded fixtures.
The bear-mode counter advances once per NY session (fix 11), and the regime
decision keeps ST's thresholds and blocked reasons with a NaN guard.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016ztXjLWXu61Dpp4XMU2jsS"
```

---

### Task 5: Session clock, NYSE calendar and the per-tick budget

**Files:**
- Create: `backend/swing_trader/clock.py`
- Test: `backend/tests/test_swing_clock.py`

**Interfaces:**
- Consumes: `backend/live_calendar.is_nyse_open` (existing).
- Produces:
  - `NY: ZoneInfo`, `TICK_GRID_MIN = 20`, `MONITOR_BUDGET_S = 100.0`, `DEFAULT_BUDGET_S = 1500.0`, `CANDIDATE_RESERVE_S = 55.0`, `PREPARE_RESERVE_S = 45.0`.
  - `as_utc(value) -> datetime | None`, `ny_now(current_time) -> datetime`, `ny_date(current_time) -> str`, `parse_hhmm(value, default="00:00") -> time`, `at_or_after(current_time, hhmm, *, lead_min=0) -> bool`, `week_monday(d) -> date`.
  - `trading_days(start: date, end: date) -> set[date]`, `is_trading_day(d: date) -> bool`, `is_rth(current_time) -> bool`.
  - `tick_deadline(current_time, mode, *, clock=time.monotonic) -> float` (shared by every lane in one broker tick), `time_left(deadline, *, clock=time.monotonic) -> float`.

**Why this exists.** The live broker wakes every 20 minutes from 05:00 PT (`backend/scheduler.py` `DEFAULT_CONFIG`), and on a MONITOR tick it discards the whole run_once output after 120 s (`broker.py` `_WATCHDOG_MONITOR_SEC`) while the lane's cache writes still land. An AI-scored scan can take minutes, so both lanes check one shared deadline before each network step and resume on the next tick. `CANDIDATE_RESERVE_S` covers one candidate's two 25 s model calls plus data reads.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_swing_clock.py`:

```python
"""NY clock, the NYSE calendar, and the budget both lanes share in one tick."""
import os
import sys
from datetime import date, datetime, timezone

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from swing_trader import clock  # noqa: E402

MON_0920_ET = datetime(2026, 6, 1, 13, 20, tzinfo=timezone.utc)   # EDT
MON_0940_ET = datetime(2026, 6, 1, 13, 40, tzinfo=timezone.utc)


def test_ny_date_uses_the_new_york_calendar():
    late = datetime(2026, 6, 2, 2, 30, tzinfo=timezone.utc)   # 22:30 ET Jun 1
    assert clock.ny_date(late) == "2026-06-01"
    assert clock.ny_date(datetime(2026, 6, 1, 13, 20)) == "2026-06-01"   # naive = UTC


def test_at_or_after_with_and_without_a_lead():
    assert clock.at_or_after(MON_0920_ET, "09:15") is True
    assert clock.at_or_after(MON_0920_ET, "09:30") is False
    # 15:40 ET is the last RTH tick; a 15:45 monitor with a 20-minute lead fires there.
    t = datetime(2026, 6, 1, 19, 40, tzinfo=timezone.utc)
    assert clock.at_or_after(t, "15:45") is False
    assert clock.at_or_after(t, "15:45", lead_min=clock.TICK_GRID_MIN) is True


def test_parse_hhmm_falls_back_on_garbage():
    assert clock.parse_hhmm("10:30").hour == 10
    assert clock.parse_hhmm("nonsense", "09:15").minute == 15


def test_trading_days_skip_weekends_and_good_friday():
    days = clock.trading_days(date(2026, 3, 30), date(2026, 4, 6))
    assert date(2026, 4, 2) in days          # Thursday
    assert date(2026, 4, 3) not in days      # Good Friday
    assert date(2026, 4, 4) not in days      # Saturday
    assert clock.is_trading_day(date(2026, 6, 1)) is True
    assert clock.is_trading_day(date(2026, 7, 3)) is False   # Independence Day observed


def test_is_rth():
    assert clock.is_rth(MON_0920_ET) is False
    assert clock.is_rth(MON_0940_ET) is True


def test_week_monday():
    assert clock.week_monday(date(2026, 6, 4)) == date(2026, 6, 1)


def test_both_lanes_share_one_deadline_per_tick():
    clock._DEADLINES.clear()
    now = [1000.0]
    fake = lambda: now[0]  # noqa: E731
    a = clock.tick_deadline(MON_0920_ET, "MONITOR", clock=fake)
    now[0] = 1030.0
    b = clock.tick_deadline(MON_0920_ET, "MONITOR", clock=fake)
    assert a == b == 1000.0 + clock.MONITOR_BUDGET_S
    assert clock.time_left(a, clock=fake) == clock.MONITOR_BUDGET_S - 30.0
    c = clock.tick_deadline(MON_0940_ET, "FULL", clock=fake)
    assert c == 1030.0 + clock.DEFAULT_BUDGET_S


def test_the_monitor_budget_fits_inside_the_broker_watchdog():
    # A candidate starts only with CANDIDATE_RESERVE_S left and takes at most
    # about that long, so a MONITOR tick ends by MONITOR_BUDGET_S (< 120 s).
    assert clock.MONITOR_BUDGET_S < 120.0
    assert clock.CANDIDATE_RESERVE_S < clock.MONITOR_BUDGET_S
    assert clock.PREPARE_RESERVE_S < clock.MONITOR_BUDGET_S
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest backend/tests/test_swing_clock.py -q -p no:cacheprovider`
Expected: FAIL — `ImportError: cannot import name 'clock' from 'swing_trader'`.

- [ ] **Step 3: Write the implementation**

Create `backend/swing_trader/clock.py`:

```python
"""NY session clock, the NYSE calendar, and the per-tick time budget.

Platform glue with no ST source. ST ran from UTC crons that drifted an hour
every winter (spec §9 item 12); here every schedule is an ET wall-clock time
evaluated on the live broker's own ticks, which land every 20 minutes from
05:00 PT (backend/scheduler.py DEFAULT_CONFIG): 09:15 fires at the 09:20 tick,
10:30 at 10:40, and 15:45 — which has no tick before the 16:00 close — fires
at 15:40 through the `lead_min` of at_or_after.

The budget: on a MONITOR tick broker.py waits 120 s for run_run_once_strategies
(_WATCHDOG_MONITOR_SEC) and then DISCARDS its output while the strategy's cache
writes still land; other ticks wait 1800 s. Both lanes of one document run in
that one call, so they share one deadline, stop starting network work before
it, and resume on the next tick from their cached state.
"""
from __future__ import annotations

import time as _time
from datetime import date, datetime, time as dtime, timedelta, timezone
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")

TICK_GRID_MIN = 20
MONITOR_BUDGET_S = 100.0
DEFAULT_BUDGET_S = 1500.0
#: One candidate = earnings + sector bars + two model calls at 25 s each.
CANDIDATE_RESERVE_S = 55.0
#: The bars fetch (~6 batched requests) plus the VIX read.
PREPARE_RESERVE_S = 45.0

_DEADLINES: dict = {}
_CALENDAR = None


def as_utc(value):
    """tz-aware UTC, or None. Naive input is treated as UTC (the backtest clock
    is naive, the live clock aware; strategy_x._as_utc makes the same call)."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def ny_now(current_time) -> datetime:
    t = as_utc(current_time) or datetime.now(timezone.utc)
    return t.astimezone(NY)


def ny_date(current_time) -> str:
    return ny_now(current_time).date().isoformat()


def parse_hhmm(value, default: str = "00:00") -> dtime:
    for raw in (value, default):
        try:
            hh, mm = str(raw).strip().split(":", 1)
            return dtime(int(hh), int(mm))
        except (TypeError, ValueError):
            continue
    return dtime(0, 0)


def at_or_after(current_time, hhmm, *, lead_min: int = 0) -> bool:
    now = ny_now(current_time)
    t = parse_hhmm(hhmm)
    target = now.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
    return now >= target - timedelta(minutes=int(lead_min))


def week_monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _calendar():
    global _CALENDAR
    if _CALENDAR is None:
        import exchange_calendars as _ec
        _CALENDAR = _ec.get_calendar("XNYS")
    return _CALENDAR


def trading_days(start: date, end: date) -> set:
    """NYSE sessions in [start, end]. Weekdays when exchange_calendars is
    missing — the fallback live_calendar also uses."""
    try:
        sessions = _calendar().sessions_in_range(start.isoformat(), end.isoformat())
        return {ts.date() for ts in sessions}
    except Exception:
        out, d = set(), start
        while d <= end:
            if d.weekday() < 5:
                out.add(d)
            d += timedelta(days=1)
        return out


def is_trading_day(d: date) -> bool:
    return d in trading_days(d, d)


def is_rth(current_time) -> bool:
    from live_calendar import is_nyse_open
    t = as_utc(current_time)
    return bool(t is not None and is_nyse_open(t))


def tick_deadline(current_time, mode, *, clock=_time.monotonic) -> float:
    key = (as_utc(current_time) or datetime.now(timezone.utc)).isoformat()
    if key not in _DEADLINES:
        _DEADLINES.clear()
        budget = (MONITOR_BUDGET_S if str(mode or "").upper() == "MONITOR"
                  else DEFAULT_BUDGET_S)
        _DEADLINES[key] = clock() + budget
    return _DEADLINES[key]


def time_left(deadline, *, clock=_time.monotonic) -> float:
    return float(deadline) - clock()
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 -m pytest backend/tests/test_swing_clock.py -q -p no:cacheprovider`
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/swing_trader/clock.py backend/tests/test_swing_clock.py
git commit -m "feat(swing): NY session clock, NYSE calendar and a shared tick budget

Scan times are ET wall-clock times evaluated on the broker's 20-minute live
ticks. Both lanes of a document share one deadline per tick so an AI scan
never overruns the 120 s MONITOR watchdog, which discards run_once output.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016ztXjLWXu61Dpp4XMU2jsS"
```

---

### Task 6: Universe, sectors and the point-in-time reference readers (B3)

**Files:**
- Create: `backend/swing_trader/universe.py`
- Create: `backend/swing_trader/sectors.py`
- Create: `backend/swing_trader/refdata.py`
- Test: `backend/tests/test_swing_universe_refdata.py`

**Interfaces:**
- Consumes: `swing_trader.constants` (Task 2); the tables of Task 1.
- Produces:
  - `universe.SP500_SYMBOLS: list[str]` (ST's 503 names, dash class shares), `universe.get_sp500_symbols() -> list[str]` (+ SPY, QQQ), `universe.get_wheel_universe() -> list[str]` (minus BRK-B, BF-B), `universe.norm_symbol(s) -> str` (upper case, dash → dot), `universe.order_like_live(symbols) -> list[str]` (dot form, live list order).
  - `sectors._SECTOR_OVERRIDES`, `sectors.normalize_sector(raw) -> str`, `sectors.get_symbol_sector(symbol, sector_map=None, *, allow_network=False, cache=None) -> str`.
  - `refdata.MACRO_TABLE, MEMBERSHIP_TABLE, SECTOR_TABLE, IV_TABLE, SIGNALS_TABLE, SCANS_TABLE, SWING_TABLES`, `refdata.VIX_MAX_STALE_DAYS = 5`, `refdata.macro_id(series, d)`, `refdata.membership_id(index, d)`, `refdata.vix_before(store, ny_date, *, max_stale_days=5) -> tuple[float | None, str | None]`, `refdata.members_before(store, ny_date, index="SPX") -> list[str] | None`, `refdata.sector_map(store, symbols) -> dict[str, str]`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_swing_universe_refdata.py`:

```python
"""Universes (ST sp500_symbols.py), sectors (ST paper_trader.py:92-115 over a
stored map), and the point-in-time readers the backtest uses (spec §7)."""
import os
import sys
import types

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from swing_trader import refdata, sectors, universe  # noqa: E402


def test_the_vendored_list_is_sts_may_2026_list():
    assert len(universe.SP500_SYMBOLS) == 503
    assert len(set(universe.SP500_SYMBOLS)) == 503
    assert universe.SP500_SYMBOLS[:3] == ["MMM", "AOS", "ABT"]
    assert universe.SP500_SYMBOLS[-3:] == ["ZBRA", "ZBH", "ZTS"]
    assert {"BRK-B", "BF-B"} <= set(universe.SP500_SYMBOLS)


def test_live_universe_appends_the_regime_etfs_and_wheel_drops_class_shares():
    live = universe.get_sp500_symbols()
    assert live[-2:] == ["SPY", "QQQ"] and len(live) == 505
    wheel = universe.get_wheel_universe()
    assert "BRK-B" not in wheel and "BF-B" not in wheel and len(wheel) == 501


def test_order_like_live_uses_todays_order_then_former_members():
    ordered = universe.order_like_live(["ZTS", "FB", "MMM", "brk-b", "SPY", "AOS"])
    assert ordered == ["MMM", "AOS", "BRK.B", "ZTS", "SPY", "FB"]


def test_norm_symbol():
    assert universe.norm_symbol(" brk-b ") == "BRK.B"


def test_overrides_win_then_the_map_then_unknown():
    smap = {"AAPL": "technology", "BRK.B": "financial_services"}
    assert sectors.get_symbol_sector("SPY", smap) == "broad_market"
    assert sectors.get_symbol_sector("AAPL", smap) == "technology"
    assert sectors.get_symbol_sector("BRK-B", smap) == "financial_services"
    assert sectors.get_symbol_sector("ZZZZ", smap) == "unknown"


def test_yfinance_is_only_a_live_fallback(monkeypatch):
    calls = []

    class _T:
        def __init__(self, symbol):
            calls.append(symbol)
            self.info = {"sector": "Consumer Defensive"}

    monkeypatch.setattr(sectors, "yf", types.SimpleNamespace(Ticker=_T))
    cache = {}
    assert sectors.get_symbol_sector("KO", {}, allow_network=False) == "unknown"
    assert calls == []
    assert sectors.get_symbol_sector("KO", {}, allow_network=True, cache=cache) == "consumer_defensive"
    assert sectors.get_symbol_sector("KO", {}, allow_network=True, cache=cache) == "consumer_defensive"
    assert calls == ["KO"]


def test_normalize_sector_is_sts_normalisation():
    assert sectors.normalize_sector("Financial Services") == "financial_services"
    assert sectors.normalize_sector(None) == "unknown"


def _vix(store, rows):
    store.insert(refdata.MACRO_TABLE, [
        {"id": refdata.macro_id("VIX", d), "series": "VIX", "date": d,
         "close": c, "source": "cboe"} for d, c in rows], conflict="replace")


def test_vix_before_skips_a_holiday_and_refuses_a_stale_gap(store):
    _vix(store, [("2026-05-21", 14.0), ("2026-05-22", 15.0), ("2026-05-26", 99.0)])
    # Tuesday after Memorial Day: Friday's close, 4 days old, is the reading.
    assert refdata.vix_before(store, "2026-05-26") == (15.0, None)
    # The row dated the session itself is never visible.
    assert refdata.vix_before(store, "2026-05-27")[0] == 99.0
    # A two-week hole is a data gap, not a weekend: no reading, with a reason.
    value, reason = refdata.vix_before(store, "2026-06-10")
    assert value is None and "2026-05-26" in reason
    assert refdata.vix_before(store, "2026-01-02")[0] is None


def test_vix_before_refuses_a_non_numeric_close(store):
    _vix(store, [("2026-06-01", "nan")])
    value, reason = refdata.vix_before(store, "2026-06-02")
    assert value is None and reason


def test_members_before_is_the_latest_change_strictly_before(store):
    store.insert(refdata.MEMBERSHIP_TABLE, [
        {"id": refdata.membership_id("SPX", "2021-01-04"), "index": "SPX",
         "date": "2021-01-04", "members": ["AAPL", "FB", "BRK.B"]},
        {"id": refdata.membership_id("SPX", "2022-06-09"), "index": "SPX",
         "date": "2022-06-09", "members": ["AAPL", "META", "BRK.B"]},
    ], conflict="replace")
    assert refdata.members_before(store, "2022-06-09") == ["AAPL", "FB", "BRK.B"]
    assert refdata.members_before(store, "2022-06-10") == ["AAPL", "META", "BRK.B"]
    assert refdata.members_before(store, "2020-12-31") is None


def test_sector_map_reads_by_alpaca_symbol(store):
    store.insert(refdata.SECTOR_TABLE, [
        {"id": "AAPL", "symbol": "AAPL", "sector": "technology",
         "as_of": "2026-09-24", "source": "yfinance"},
        {"id": "BRK.B", "symbol": "BRK.B", "sector": "financial_services",
         "as_of": "2026-09-24", "source": "yfinance"}], conflict="replace")
    assert refdata.sector_map(store, ["AAPL", "BRK-B", "NOPE"]) == {
        "AAPL": "technology", "BRK.B": "financial_services"}
    assert refdata.sector_map(store, []) == {}


def test_the_table_names_are_the_contracts():
    assert refdata.SWING_TABLES == ("SwingSignals", "SwingWheelScans",
                                    "SwingIvSnapshots", "SwingMacroDaily",
                                    "SwingIndexMembership", "SwingSectorMap")
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest backend/tests/test_swing_universe_refdata.py -q -p no:cacheprovider`
Expected: FAIL — `ImportError: cannot import name 'refdata' from 'swing_trader'`.

- [ ] **Step 3: Write universe.py**

Create `backend/swing_trader/universe.py` (the list is ST's `sp500_symbols.py`, one name per line there, reflowed here):

```python
"""Universes, ported from ST.

    sp500_symbols.py        SP500_SYMBOLS ("Last updated: May 2026"; verbatim
                            list, reflowed)
    paper_trader.py:46-58   get_sp500_symbols (SPY and QQQ appended)
    wheel_trader.py:46-55   get_wheel_universe (BRK-B and BF-B excluded)

The LIVE universe is today's list, as in ST. Backtests read
SwingIndexMembership point-in-time (refdata.members_before) and visit it in
the order ST's live scan would (order_like_live), because the order decides
which candidates get the last open slots.
"""
from __future__ import annotations

SP500_SYMBOLS = [
    "MMM", "AOS", "ABT", "ABBV", "ACN", "ADBE", "AMD", "AES", "AFL", "A", "APD",
    "ABNB", "AKAM", "ALB", "ARE", "ALGN", "ALLE", "LNT", "ALL", "GOOGL", "GOOG", "MO",
    "AMZN", "AMCR", "AEE", "AEP", "AXP", "AIG", "AMT", "AWK", "AMP", "AME", "AMGN",
    "APH", "ADI", "AON", "APA", "APO", "AAPL", "AMAT", "APP", "APTV", "ACGL", "ADM",
    "ARES", "ANET", "AJG", "AIZ", "T", "ATO", "ADSK", "ADP", "AZO", "AVB", "AVY",
    "AXON", "BKR", "BALL", "BAC", "BAX", "BDX", "BRK-B", "BBY", "TECH", "BIIB", "BLK",
    "BX", "XYZ", "BK", "BA", "BKNG", "BSX", "BMY", "AVGO", "BR", "BRO", "BF-B", "BLDR",
    "BG", "BXP", "CHRW", "CDNS", "CPT", "CPB", "COF", "CAH", "CCL", "CARR", "CVNA",
    "CASY", "CAT", "CBOE", "CBRE", "CDW", "COR", "CNC", "CNP", "CF", "CRL", "SCHW",
    "CHTR", "CVX", "CMG", "CB", "CHD", "CIEN", "CI", "CINF", "CTAS", "CSCO", "C",
    "CFG", "CLX", "CME", "CMS", "KO", "CTSH", "COHR", "COIN", "CL", "CMCSA", "FIX",
    "CAG", "COP", "ED", "STZ", "CEG", "COO", "CPRT", "GLW", "CPAY", "CTVA", "CSGP",
    "COST", "CTRA", "CRH", "CRWD", "CCI", "CSX", "CMI", "CVS", "DHR", "DRI", "DDOG",
    "DVA", "DECK", "DE", "DELL", "DAL", "DVN", "DXCM", "FANG", "DLR", "DG", "DLTR",
    "D", "DPZ", "DASH", "DOV", "DOW", "DHI", "DTE", "DUK", "DD", "ETN", "EBAY", "SATS",
    "ECL", "EIX", "EW", "EA", "ELV", "EME", "EMR", "ETR", "EOG", "EPAM", "EQT", "EFX",
    "EQIX", "EQR", "ERIE", "ESS", "EL", "EG", "EVRG", "ES", "EXC", "EXE", "EXPE",
    "EXPD", "EXR", "XOM", "FFIV", "FDS", "FICO", "FAST", "FRT", "FDX", "FIS", "FITB",
    "FSLR", "FE", "FISV", "F", "FTNT", "FTV", "FOXA", "FOX", "BEN", "FCX", "GRMN",
    "IT", "GE", "GEHC", "GEV", "GEN", "GNRC", "GD", "GIS", "GM", "GPC", "GILD", "GPN",
    "GL", "GDDY", "GS", "HAL", "HIG", "HAS", "HCA", "DOC", "HSIC", "HSY", "HPE", "HLT",
    "HD", "HON", "HRL", "HST", "HWM", "HPQ", "HUBB", "HUM", "HBAN", "HII", "IBM",
    "IEX", "IDXX", "ITW", "INCY", "IR", "PODD", "INTC", "IBKR", "ICE", "IFF", "IP",
    "INTU", "ISRG", "IVZ", "INVH", "IQV", "IRM", "JBHT", "JBL", "JKHY", "J", "JNJ",
    "JCI", "JPM", "KVUE", "KDP", "KEY", "KEYS", "KMB", "KIM", "KMI", "KKR", "KLAC",
    "KHC", "KR", "LHX", "LH", "LRCX", "LVS", "LDOS", "LEN", "LII", "LLY", "LIN", "LYV",
    "LMT", "L", "LOW", "LULU", "LITE", "LYB", "MTB", "MPC", "MAR", "MRSH", "MLM",
    "MAS", "MA", "MKC", "MCD", "MCK", "MDT", "MRK", "META", "MET", "MTD", "MGM",
    "MCHP", "MU", "MSFT", "MAA", "MRNA", "TAP", "MDLZ", "MPWR", "MNST", "MCO", "MS",
    "MOS", "MSI", "MSCI", "NDAQ", "NTAP", "NFLX", "NEM", "NWSA", "NWS", "NEE", "NKE",
    "NI", "NDSN", "NSC", "NTRS", "NOC", "NCLH", "NRG", "NUE", "NVDA", "NVR", "NXPI",
    "ORLY", "OXY", "ODFL", "OMC", "ON", "OKE", "ORCL", "OTIS", "PCAR", "PKG", "PLTR",
    "PANW", "PSKY", "PH", "PAYX", "PYPL", "PNR", "PEP", "PFE", "PCG", "PM", "PSX",
    "PNW", "PNC", "POOL", "PPG", "PPL", "PFG", "PG", "PGR", "PLD", "PRU", "PEG", "PTC",
    "PSA", "PHM", "PWR", "QCOM", "DGX", "Q", "RL", "RJF", "RTX", "O", "REG", "REGN",
    "RF", "RSG", "RMD", "RVTY", "HOOD", "ROK", "ROL", "ROP", "ROST", "RCL", "SPGI",
    "CRM", "SNDK", "SBAC", "SLB", "STX", "SRE", "NOW", "SHW", "SPG", "SWKS", "SJM",
    "SW", "SNA", "SOLV", "SO", "LUV", "SWK", "SBUX", "STT", "STLD", "STE", "SYK",
    "SMCI", "SYF", "SNPS", "SYY", "TMUS", "TROW", "TTWO", "TPR", "TRGP", "TGT", "TEL",
    "TDY", "TER", "TSLA", "TXN", "TPL", "TXT", "TMO", "TJX", "TKO", "TTD", "TSCO",
    "TT", "TDG", "TRV", "TRMB", "TFC", "TYL", "TSN", "USB", "UBER", "UDR", "ULTA",
    "UNP", "UAL", "UPS", "URI", "UNH", "UHS", "VLO", "VTR", "VLTO", "VRSN", "VRSK",
    "VZ", "VRTX", "VRT", "VTRS", "VICI", "V", "VST", "VMC", "WRB", "GWW", "WAB", "WMT",
    "DIS", "WBD", "WM", "WAT", "WEC", "WFC", "WELL", "WST", "WDC", "WY", "WSM", "WMB",
    "WTW", "WDAY", "WYNN", "XEL", "XYL", "YUM", "ZBRA", "ZBH", "ZTS",
]


def get_sp500_symbols() -> list[str]:
    # Always include regime symbols regardless of S&P 500 membership
    symbols = list(SP500_SYMBOLS)
    for sym in ["SPY", "QQQ"]:
        if sym not in symbols:
            symbols.append(sym)
    return symbols


def get_wheel_universe() -> list[str]:
    EXCLUDE = {"BRK-B", "BF-B"}
    return [s for s in SP500_SYMBOLS if s not in EXCLUDE]


def norm_symbol(symbol) -> str:
    """Upper case, and Alpaca's dot for class shares (market_data.py:84-89)."""
    return str(symbol or "").strip().upper().replace("-", ".")


def order_like_live(symbols) -> list[str]:
    """Point-in-time members in the order ST's live scan visits them: today's
    list order first, then former members alphabetically."""
    rank = {norm_symbol(s): i for i, s in enumerate(get_sp500_symbols())}
    seen, uniq = set(), []
    for s in symbols or []:
        k = norm_symbol(s)
        if k and k not in seen:
            seen.add(k)
            uniq.append(k)
    return sorted(uniq, key=lambda s: (rank.get(s, len(rank)), s))
```

- [ ] **Step 4: Write sectors.py**

Create `backend/swing_trader/sectors.py`:

```python
"""Sectors, ported from ST paper_trader.py:92-115.

ST resolved every symbol through yfinance on first use and cached it for the
process. Here the stored SwingSectorMap (scripts/build_swing_reference_data.py:
yfinance info.sector + ST's overrides, normalised the way ST normalised) is
read first, and yfinance is only a LIVE fallback for a symbol the map lacks
(spec §9 item 15). The map is static and labelled not point-in-time.
"""
from __future__ import annotations

import yfinance as yf

from swing_trader.universe import norm_symbol

# Sector map for correlation check — covers S&P 500 major sectors
# Symbol → sector string. Unknown symbols default to "unknown" (allowed through)
_SECTOR_OVERRIDES: dict[str, str] = {
    # ETFs and commodities not in S&P 500
    "SPY": "broad_market", "QQQ": "broad_market",
    "GLD": "commodity",    "XLE": "energy",
    "IWM": "broad_market", "DIA": "broad_market",
}


def normalize_sector(raw) -> str:
    """ST's normalisation (paper_trader.py:111): lower case, spaces to _."""
    return str(raw if raw else "unknown").lower().replace(" ", "_")


def get_symbol_sector(symbol, sector_map=None, *, allow_network=False,
                      cache=None) -> str:
    """Return the sector for a symbol: override, stored map, then (live only)
    yfinance, cached in `cache`."""
    if symbol in _SECTOR_OVERRIDES:
        return _SECTOR_OVERRIDES[symbol]
    key = norm_symbol(symbol)
    if sector_map and key in sector_map:
        return sector_map[key]
    if cache is not None and key in cache:
        return cache[key]
    if not allow_network:
        return "unknown"
    try:
        info   = yf.Ticker(symbol).info
        sector = info.get("sector", "unknown").lower().replace(" ", "_")
        if cache is not None:
            cache[key] = sector
        return sector
    except Exception:
        return "unknown"
```

- [ ] **Step 5: Write refdata.py**

Create `backend/swing_trader/refdata.py`:

```python
"""Point-in-time reference data for backtests (spec §7).

Rows are written only by scripts/build_swing_reference_data.py. Every reader
takes the store as an argument (db.store in production, the FakeStore fixture
in tests) and reads STRICTLY BEFORE the NY trading date: a daily row dated the
session carries a close from the future. `between` is [lo, hi), so the open
upper bound "VIX|<ny date>" is that rule.
"""
from __future__ import annotations

import math
from datetime import date

from swing_trader.universe import norm_symbol

SIGNALS_TABLE = "SwingSignals"
SCANS_TABLE = "SwingWheelScans"
IV_TABLE = "SwingIvSnapshots"
MACRO_TABLE = "SwingMacroDaily"
MEMBERSHIP_TABLE = "SwingIndexMembership"
SECTOR_TABLE = "SwingSectorMap"
SWING_TABLES = (SIGNALS_TABLE, SCANS_TABLE, IV_TABLE, MACRO_TABLE,
                MEMBERSHIP_TABLE, SECTOR_TABLE)

#: A VIX row older than this, counted back from the session, is a data gap and
#: not a weekend or holiday: the longest regular gap is Friday to the Tuesday
#: after a Monday holiday, 4 days. A gap blocks the regime, as a missing VIX
#: did in ST (paper_trader.py:479).
VIX_MAX_STALE_DAYS = 5


def macro_id(series, d) -> str:
    return f"{series}|{str(d)[:10]}"


def membership_id(index, d) -> str:
    return f"{index}|{str(d)[:10]}"


def _latest_before(store, table, prefix, ny_date):
    rows = store.run(store.limit(store.order_by(
        store.between(table, f"{prefix}|", f"{prefix}|{str(ny_date)[:10]}"),
        index="id", desc=True), 1))
    return rows[0] if rows else None


def vix_before(store, ny_date, *, max_stale_days: int = VIX_MAX_STALE_DAYS):
    """(close, None) for the latest VIX row strictly before `ny_date`, or
    (None, reason) when there is none, it is too old, or its close is bad."""
    row = _latest_before(store, MACRO_TABLE, "VIX", ny_date)
    if row is None:
        return None, f"no VIX row before {ny_date}"
    d = str(row.get("date") or str(row.get("id", "")).split("|", 1)[-1])[:10]
    age = (date.fromisoformat(str(ny_date)[:10]) - date.fromisoformat(d)).days
    if age > int(max_stale_days):
        return None, f"latest VIX row is {d}, {age} days before {ny_date}"
    try:
        close = float(row.get("close"))
    except (TypeError, ValueError):
        return None, f"VIX row {d} has no numeric close"
    if not math.isfinite(close) or close <= 0:
        return None, f"VIX row {d} close is {row.get('close')!r}"
    return close, None


def members_before(store, ny_date, index: str = "SPX"):
    """The full member list as of the latest change dated strictly before
    `ny_date` (dot-form symbols), or None when no row precedes it."""
    row = _latest_before(store, MEMBERSHIP_TABLE, index, ny_date)
    if row is None:
        return None
    return [norm_symbol(s) for s in (row.get("members") or []) if str(s).strip()]


def sector_map(store, symbols) -> dict:
    keys = sorted({norm_symbol(s) for s in (symbols or []) if str(s).strip()})
    if not keys:
        return {}
    rows = store.get_all(SECTOR_TABLE, *keys)
    return {str(r.get("symbol") or r.get("id")).upper(): str(r.get("sector") or "unknown")
            for r in rows}
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `python3 -m pytest backend/tests/test_swing_universe_refdata.py -q -p no:cacheprovider`
Expected: PASS (12 tests).

- [ ] **Step 7: Commit**

```bash
git add backend/swing_trader/universe.py backend/swing_trader/sectors.py backend/swing_trader/refdata.py backend/tests/test_swing_universe_refdata.py
git commit -m "feat(swing): universes, stored sectors and point-in-time readers

ST's May 2026 S&P list drives the live scan; backtests read membership and
VIX strictly before the NY date, and a VIX gap older than five days blocks
the regime instead of reading a stale value.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016ztXjLWXu61Dpp4XMU2jsS"
```

---

### Task 7: Market data — batched bars, latest trade, engine-bar frames (B4)

**Files:**
- Create: `backend/swing_trader/market_data.py`
- Test: `backend/tests/test_swing_market_data.py`

**Interfaces:**
- Consumes: `swing_trader.clock` (Task 5), `swing_trader.constants.WARMUP_DAYS`.
- Consumes (live, plan A-live, optional): `adapter.get_latest_trades(symbols) -> dict[str, tuple[float, str]]` (contract §3). Any exception falls back to the client path.
- Produces:
  - `BATCH_SIZE = 100`, `RETRY_ATTEMPTS = 2`, `RETRY_WAIT = 2`, `STALE_TRADE_SECONDS = 600`, `LIVE_WINDOW_DAYS = 450`.
  - `data_client(api_key, secret) -> StockHistoricalDataClient` (cached per key pair; raises `RuntimeError` when either is empty).
  - `get_daily_bars(symbols, days, *, client) -> dict[str, pd.DataFrame]` (Open/High/Low/Close/Volume, naive date index, caller's spelling).
  - `get_latest_trade(symbol, *, client) -> tuple[float | None, datetime | None]`, `get_latest_price(symbol, *, client) -> float | None`.
  - `fetch_live_price(symbol, *, client, now=None) -> float | None` (IEX, stale → yfinance).
  - `yf_last_price(symbol) -> float | None`, `fresh_trade_price(price, ts, *, now=None) -> float | None`, `live_prices(symbols, *, adapter=None, client=None, now=None) -> dict[str, float]`.
  - `frames_from_engine_bars(data, symbols, as_of, *, lookback_days=450) -> dict[str, pd.DataFrame]` (backtest).

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_swing_market_data.py` (the first block is ST's `tests/test_market_data.py` with the client passed in instead of monkeypatching `_get_data_client`):

```python
"""market_data.py (ST tests/test_market_data.py, ported) plus the live price
fallback (app.py:_fetch_live_price) and the backtest frame builder."""
import os
import sys
import types
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from swing_trader import market_data  # noqa: E402


class FakeBar:
    def __init__(self, ts, o=10.0, h=11.0, lo=9.0, c=10.5, v=1000):
        self.timestamp = ts
        self.open, self.high, self.low, self.close, self.volume = o, h, lo, c, v


class FakeBarSet:
    def __init__(self, data):
        self.data = data


class FakeTrade:
    def __init__(self, price, ts):
        self.price = price
        self.timestamp = ts


def _bars(n, start_close=100.0):
    base = datetime(2026, 7, 1, 4, 0, tzinfo=timezone.utc)
    return [FakeBar(base + timedelta(days=i), c=start_close + i) for i in range(n)]


class FakeClient:
    def __init__(self, responses=None, error=None):
        self.calls = []
        self.responses = responses or []
        self.error = error

    def get_stock_bars(self, req):
        self.calls.append(req)
        if self.error is not None:
            raise self.error
        idx = len(self.calls) - 1
        if idx < len(self.responses):
            return self.responses[idx]
        return FakeBarSet({})

    def get_stock_latest_trade(self, req):
        if self.error is not None:
            raise self.error
        return self.responses[0]


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(market_data.time, "sleep", lambda s: None)


def test_batch_splitting():
    symbols = [f"SYM{i}" for i in range(250)]
    client = FakeClient(responses=[
        FakeBarSet({s: _bars(2) for s in symbols[0:100]}),
        FakeBarSet({s: _bars(2) for s in symbols[100:200]}),
        FakeBarSet({s: _bars(2) for s in symbols[200:250]}),
    ])
    result = market_data.get_daily_bars(symbols, days=10, client=client)
    assert [len(c.symbol_or_symbols) for c in client.calls] == [100, 100, 50]
    assert len(result) == 250


def test_request_params():
    client = FakeClient(responses=[FakeBarSet({"SPY": _bars(3)})])
    market_data.get_daily_bars(["SPY"], days=300, client=client)
    req = client.calls[0]
    assert req.feed == market_data.DataFeed.SIP
    assert req.adjustment == market_data.Adjustment.ALL
    assert (req.timeframe.amount_value, req.timeframe.unit_value) == \
           (market_data.TimeFrame.Day.amount_value, market_data.TimeFrame.Day.unit_value)
    expected_start = datetime.now(timezone.utc) - timedelta(days=300)
    req_start = req.start if req.start.tzinfo else req.start.replace(tzinfo=timezone.utc)
    assert abs((req_start - expected_start).total_seconds()) < 60


def test_column_and_index_shape():
    client = FakeClient(responses=[FakeBarSet({"AAPL": _bars(5)})])
    df = market_data.get_daily_bars(["AAPL"], days=10, client=client)["AAPL"]
    assert list(df.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert isinstance(df.index, pd.DatetimeIndex) and df.index.tz is None
    assert df.index[0] == pd.Timestamp("2026-07-01")
    assert (df.index == df.index.normalize()).all()
    assert df.index.is_monotonic_increasing
    assert df["Close"].iloc[-1] == 104.0


def test_empty_response_and_empty_symbol_list():
    assert market_data.get_daily_bars(
        ["ZZZZ"], days=10, client=FakeClient(responses=[FakeBarSet({})])) == {}
    client = FakeClient()
    assert market_data.get_daily_bars([], days=10, client=client) == {}
    assert client.calls == []


def test_partial_and_empty_symbol_responses_are_absent():
    client = FakeClient(responses=[FakeBarSet({"AAPL": _bars(3), "EMPTY": []})])
    result = market_data.get_daily_bars(["AAPL", "MISSING", "EMPTY"], days=10, client=client)
    assert set(result) == {"AAPL"}


def test_batch_failure_continues():
    calls = {"n": 0}

    class Flaky(FakeClient):
        def get_stock_bars(self, req):
            self.calls.append(req)
            calls["n"] += 1
            if req.symbol_or_symbols[0] == "BAD0":
                raise RuntimeError("boom")
            return FakeBarSet({s: _bars(2) for s in req.symbol_or_symbols})

    bad = [f"BAD{i}" for i in range(100)]
    good = [f"GOOD{i}" for i in range(50)]
    result = market_data.get_daily_bars(bad + good, days=10, client=Flaky())
    assert calls["n"] == market_data.RETRY_ATTEMPTS + 1
    assert len(result) == 50 and all(s.startswith("GOOD") for s in result)


def test_retry_then_success():
    class OnceFlaky(FakeClient):
        def get_stock_bars(self, req):
            self.calls.append(req)
            if len(self.calls) == 1:
                raise RuntimeError("transient")
            return FakeBarSet({"SPY": _bars(2)})

    client = OnceFlaky()
    assert "SPY" in market_data.get_daily_bars(["SPY"], days=10, client=client)
    assert len(client.calls) == 2


def test_large_single_symbol_history_passthrough():
    client = FakeClient(responses=[FakeBarSet({"SPY": _bars(1300)})])
    assert len(market_data.get_daily_bars(["SPY"], days=2000, client=client)["SPY"]) == 1300


def test_latest_price_success_failure_zero_and_missing():
    ts = datetime(2026, 7, 7, 15, 0, tzinfo=timezone.utc)
    ok = FakeClient(responses=[{"AAPL": FakeTrade(212.34, ts)}])
    assert market_data.get_latest_price("AAPL", client=ok) == 212.34
    assert market_data.get_latest_trade("AAPL", client=ok) == (212.34, ts)
    down = FakeClient(error=RuntimeError("api down"))
    assert market_data.get_latest_price("AAPL", client=down) is None
    assert market_data.get_latest_trade("AAPL", client=down) == (None, None)
    zero = FakeClient(responses=[{"AAPL": FakeTrade(0.0, ts)}])
    assert market_data.get_latest_price("AAPL", client=zero) is None
    assert market_data.get_latest_price("AAPL", client=FakeClient(responses=[{}])) is None


def test_dash_symbols_translated_and_mapped_back():
    client = FakeClient(responses=[FakeBarSet({"BRK.B": _bars(3), "AAPL": _bars(3)})])
    result = market_data.get_daily_bars(["BRK-B", "AAPL"], days=10, client=client)
    assert client.calls[0].symbol_or_symbols == ["BRK.B", "AAPL"]
    assert set(result) == {"BRK-B", "AAPL"}
    ts = datetime(2026, 7, 7, 15, 0, tzinfo=timezone.utc)
    trade = FakeClient(responses=[{"BRK.B": FakeTrade(480.0, ts)}])
    assert market_data.get_latest_price("BRK-B", client=trade) == 480.0


def test_data_client_refuses_missing_credentials():
    with pytest.raises(RuntimeError):
        market_data.data_client("", "secret")


# -- app.py:_fetch_live_price -------------------------------------------------

def _yf_price(price):
    return types.SimpleNamespace(Ticker=lambda s: types.SimpleNamespace(
        fast_info={"last_price": price}))


def test_a_stale_iex_print_in_market_hours_falls_back_to_yfinance(monkeypatch):
    monkeypatch.setattr(market_data, "yf", _yf_price(99.0))
    now = datetime(2026, 7, 7, 15, 0, tzinfo=timezone.utc)      # 11:00 ET
    stale = FakeClient(responses=[{"AAPL": FakeTrade(210.0, now - timedelta(minutes=20))}])
    assert market_data.fetch_live_price("AAPL", client=stale, now=now) == 99.0
    fresh = FakeClient(responses=[{"AAPL": FakeTrade(210.0, now - timedelta(minutes=2))}])
    assert market_data.fetch_live_price("AAPL", client=fresh, now=now) == 210.0
    night = datetime(2026, 7, 7, 23, 0, tzinfo=timezone.utc)    # 19:00 ET
    old = FakeClient(responses=[{"AAPL": FakeTrade(210.0, night - timedelta(hours=3))}])
    assert market_data.fetch_live_price("AAPL", client=old, now=night) == 210.0


def test_live_prices_prefers_the_adapter_and_falls_back(monkeypatch):
    monkeypatch.setattr(market_data, "yf", _yf_price(50.0))
    now = datetime(2026, 7, 7, 15, 0, tzinfo=timezone.utc)

    class Adapter:
        def get_latest_trades(self, symbols):
            return {"AAA": (101.0, (now - timedelta(minutes=1)).isoformat()),
                    "BBB": (7.0, (now - timedelta(hours=1)).isoformat())}

    out = market_data.live_prices(["AAA", "BBB", "CCC"], adapter=Adapter(), now=now)
    assert out == {"AAA": 101.0, "BBB": 50.0, "CCC": 50.0}

    class Broken:
        def get_latest_trades(self, symbols):
            raise NotImplementedError

    assert market_data.live_prices(["AAA"], adapter=Broken(), now=now) == {"AAA": 50.0}


# -- engine bars -> ST frames (backtest) --------------------------------------

def _daily(day, c, o=None, h=None, lo=None, v=1000.0):
    return {"t": f"{day}T04:00:00Z", "o": o or c, "h": h or c + 1, "l": lo or c - 1,
            "c": c, "v": v}


def test_daily_engine_bars_stop_strictly_before_the_ny_date():
    data = {"AAA": {"bars": [_daily("2026-05-28", 10.0), _daily("2026-05-29", 11.0),
                             _daily("2026-06-01", 12.0)]}}
    as_of = datetime(2026, 6, 1, 13, 20, tzinfo=timezone.utc)
    df = market_data.frames_from_engine_bars(data, ["AAA"], as_of)["AAA"]
    assert list(df.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert df.index[-1] == pd.Timestamp("2026-05-29") and df["Close"].iloc[-1] == 11.0
    assert df.index.tz is None


def test_intraday_engine_bars_roll_up_by_ny_session():
    bars = [
        {"t": "2026-05-29T13:30:00Z", "o": 10.0, "h": 10.5, "l": 9.8, "c": 10.2, "v": 100},
        {"t": "2026-05-29T19:45:00Z", "o": 10.2, "h": 11.0, "l": 10.1, "c": 10.9, "v": 300},
        {"t": "2026-06-01T13:30:00Z", "o": 11.0, "h": 11.5, "l": 10.9, "c": 11.2, "v": 50},
    ]
    as_of = datetime(2026, 6, 1, 14, 0, tzinfo=timezone.utc)
    df = market_data.frames_from_engine_bars({"AAA": bars}, ["AAA"], as_of)["AAA"]
    assert len(df) == 1
    row = df.iloc[0]
    assert (row["Open"], row["High"], row["Low"], row["Close"], row["Volume"]) == (
        10.0, 11.0, 9.8, 10.9, 400.0)


def test_engine_frames_keep_a_nan_close_and_trim_to_the_live_window():
    old = (datetime(2026, 6, 1) - timedelta(days=500)).date().isoformat()
    data = {"AAA": [_daily(old, 5.0), _daily("2026-05-28", 10.0),
                    {"t": "2026-05-29T04:00:00Z", "o": 11, "h": 12, "l": 10,
                     "c": None, "v": 1}]}
    as_of = datetime(2026, 6, 1, 13, 20, tzinfo=timezone.utc)
    df = market_data.frames_from_engine_bars(data, ["AAA", "NOBARS"], as_of)
    assert set(df) == {"AAA"}
    assert len(df["AAA"]) == 2 and pd.isna(df["AAA"]["Close"].iloc[-1])
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest backend/tests/test_swing_market_data.py -q -p no:cacheprovider`
Expected: FAIL — `ImportError: cannot import name 'market_data' from 'swing_trader'`.

- [ ] **Step 3: Write the implementation**

Create `backend/swing_trader/market_data.py`:

```python
"""Alpaca market data, ported from ST.

    market_data.py (whole file)  get_daily_bars, get_latest_trade,
                                 get_latest_price, _bars_to_frame,
                                 _to_alpaca_symbol (verbatim bodies)
    app.py:673-706               _fetch_live_price -> fetch_live_price

Only I/O changed: ST built a module singleton from ALPACA_API_KEY/SECRET in
the environment; here each call takes a `client` built by data_client() from
the credentials the engine injects into every run_once strategy's config
(broker.py run_run_once_strategies: config["alpaca_key"], config["alpaca_secret"]).
ST's prints go to the strategy log.

frames_from_engine_bars turns the BACKTEST engine's bars into the same
per-symbol frames, keeping only sessions strictly before the NY date inside
ST's live window, so EWM warm-up matches what live computes.
"""
from __future__ import annotations

import hashlib
import math
import time
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import yfinance as yf
from alpaca.data.enums import Adjustment, DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, StockLatestTradeRequest
from alpaca.data.timeframe import TimeFrame

from swing_trader import clock
from swing_trader.constants import WARMUP_DAYS

try:
    from intellistock_logger import intellistock_logger as _ilog  # type: ignore

    def _log(msg, color="white"):
        _ilog.log(str(msg), color, service="SwingMarketData")
except Exception:  # pragma: no cover - standalone/test import
    def _log(msg, color="white"):
        print(f"[SwingMarketData] {msg}")

BATCH_SIZE    = 100   # symbols per bars request (rate-limit friendly)
RETRY_ATTEMPTS = 2    # attempts per batch
RETRY_WAIT     = 2    # seconds between attempts
#: app.py:692-697 — an IEX print older than this during market hours is stale.
STALE_TRADE_SECONDS = 600
#: paper_trader.py:216 — WARMUP_DAYS trading days scaled to calendar days.
LIVE_WINDOW_DAYS = int(WARMUP_DAYS * 1.5)

_clients: dict = {}


def data_client(api_key, secret) -> StockHistoricalDataClient:
    """One client per credential pair (ST: the _get_data_client singleton)."""
    if not api_key or not secret:
        raise RuntimeError("Alpaca data credentials missing: the broker injects "
                           "alpaca_key/alpaca_secret into every run_once config")
    fp = hashlib.sha256(f"{api_key}:{secret}".encode()).hexdigest()
    client = _clients.get(fp)
    if client is None:
        client = StockHistoricalDataClient(api_key, secret)
        _clients[fp] = client
    return client


def _bars_to_frame(bars: list) -> pd.DataFrame:
    """Convert a list of alpaca Bar objects to an Open/High/Low/Close/Volume
    DataFrame indexed by naive trading date.

    Alpaca stamps daily bars at 04:00/05:00 UTC (midnight ET); converting to
    ET before dropping tz yields the actual trading date, so downstream
    indicator code sees the same DatetimeIndex shape yfinance produced.
    """
    rows = {
        "Open":   [b.open   for b in bars],
        "High":   [b.high   for b in bars],
        "Low":    [b.low    for b in bars],
        "Close":  [b.close  for b in bars],
        "Volume": [b.volume for b in bars],
    }
    idx = pd.DatetimeIndex([b.timestamp for b in bars])
    if idx.tz is not None:
        idx = idx.tz_convert("America/New_York").tz_localize(None)
    df = pd.DataFrame(rows, index=idx.normalize())
    df.index.name = "Date"
    return df.sort_index()


def _to_alpaca_symbol(symbol: str) -> str:
    """yfinance-style class shares use a dash (BRK-B); Alpaca uses a dot
    (BRK.B). A dash symbol in a bars request 400s the ENTIRE batch — found
    2026-07-07 when batch 1 of the S&P universe (containing BRK-B and BF-B)
    returned 100 nulls on the live RSI watchlist."""
    return symbol.replace("-", ".")


def get_daily_bars(symbols: list, days: int, *, client) -> dict:
    """Fetch `days` calendar days of daily bars for `symbols`.

    Returns {symbol: DataFrame} with columns Open/High/Low/Close/Volume and a
    naive date DatetimeIndex, keyed by the CALLER'S symbol spelling (dash
    class-share symbols are translated to Alpaca's dot form for the request
    and mapped back here). Symbols with no data are simply absent from the
    dict (callers already tolerate missing symbols). A batch that fails after
    all retries logs a warning and is skipped — remaining batches still run.
    """
    if not symbols:
        return {}

    to_caller = {_to_alpaca_symbol(s): s for s in symbols}
    start = datetime.now(timezone.utc) - timedelta(days=days)
    alpaca_symbols = [_to_alpaca_symbol(s) for s in symbols]
    batches = [alpaca_symbols[i:i + BATCH_SIZE] for i in range(0, len(alpaca_symbols), BATCH_SIZE)]
    result: dict = {}

    for batch_num, batch in enumerate(batches, 1):
        req = StockBarsRequest(
            symbol_or_symbols=batch,
            timeframe=TimeFrame.Day,
            start=start,
            adjustment=Adjustment.ALL,
            feed=DataFeed.SIP,
        )
        for attempt in range(1, RETRY_ATTEMPTS + 1):
            try:
                barset = client.get_stock_bars(req)
                data = getattr(barset, "data", None) or {}
                for sym, bars in data.items():
                    if bars:
                        result[to_caller.get(sym, sym)] = _bars_to_frame(bars)
                _log(f"  [market_data] Batch {batch_num}/{len(batches)} OK "
                     f"({len(data)}/{len(batch)} symbols)")
                break
            except Exception as exc:
                if attempt < RETRY_ATTEMPTS:
                    _log(f"  [market_data] Batch {batch_num}/{len(batches)} attempt "
                         f"{attempt} failed — {exc}; retrying in {RETRY_WAIT}s")
                    time.sleep(RETRY_WAIT)
                else:
                    _log(f"  [market_data] WARNING: batch {batch_num}/{len(batches)} "
                         f"failed after {RETRY_ATTEMPTS} attempts — {exc}")

    return result


def get_latest_trade(symbol: str, *, client):
    """Latest trade via the IEX feed. Returns (price, timestamp) or (None, None).

    timestamp is the trade's tz-aware datetime — callers that care about
    staleness (approve-time pricing) can inspect it.
    """
    try:
        alpaca_sym = _to_alpaca_symbol(symbol)
        req = StockLatestTradeRequest(symbol_or_symbols=alpaca_sym, feed=DataFeed.IEX)
        trades = client.get_stock_latest_trade(req)
        trade = trades[alpaca_sym]
        price = float(trade.price)
        if price <= 0:
            return None, None
        return price, trade.timestamp
    except Exception as exc:
        _log(f"  [market_data] latest trade failed for {symbol} — {exc}")
        return None, None


def get_latest_price(symbol: str, *, client):
    """Latest trade price via IEX; None on any failure."""
    price, _ = get_latest_trade(symbol, client=client)
    return price


def _is_stale(ts, now) -> bool:
    """app.py:688-697: stale only during market hours and older than 10 min."""
    try:
        now_utc = now or datetime.now(timezone.utc)
        now_et = now_utc.astimezone(clock.NY)
        market_open = (
            now_et.weekday() < 5
            and (now_et.hour, now_et.minute) >= (9, 30)
            and now_et.hour < 16
        )
        age = (now_utc - clock.as_utc(ts)).total_seconds()
        return market_open and age > STALE_TRADE_SECONDS
    except Exception:
        return False


def yf_last_price(symbol: str):
    try:
        return float(yf.Ticker(symbol).fast_info["last_price"])
    except Exception as exc:
        _log(f"  [live-price] {symbol}: yfinance fallback failed — {exc}")
        return None


def fetch_live_price(symbol: str, *, client, now=None):
    """Live price: Alpaca IEX latest trade → yfinance fast_info → None (R8-06).

    IEX prints can be thin on less-liquid names, and approve-time price feeds
    bracket math — so a stale print is as bad as no print. If the IEX trade
    timestamp is older than 10 minutes during market hours, treat it as a
    failure and fall through to yfinance.
    """
    price, ts = get_latest_trade(symbol, client=client)
    if price is not None:
        if ts is None or not _is_stale(ts, now):
            return price
        _log(f"  [live-price] {symbol}: IEX trade is stale — falling back to yfinance")
    return yf_last_price(symbol)


def fresh_trade_price(price, ts, *, now=None):
    """A (price, timestamp) pair from the broker adapter, or None when it is
    missing, non-positive or stale by the rule above."""
    try:
        p = float(price)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(p) or p <= 0:
        return None
    if ts is not None and _is_stale(ts, now):
        return None
    return p


def live_prices(symbols, *, adapter=None, client=None, now=None) -> dict:
    """Latest prices for `symbols`: the adapter's latest trades when it has
    them (plan A-live get_latest_trades), else ST's IEX path, else yfinance."""
    wanted = [str(s) for s in (symbols or []) if str(s).strip()]
    trades = {}
    if adapter is not None and wanted:
        try:
            trades = adapter.get_latest_trades(wanted) or {}
        except Exception:
            trades = {}
    out = {}
    for sym in wanted:
        price, ts = (trades.get(sym) or (None, None))
        p = fresh_trade_price(price, ts, now=now) if price is not None else None
        if p is None:
            p = (fetch_live_price(sym, client=client, now=now) if client is not None
                 else yf_last_price(sym))
        if p is not None:
            out[sym] = p
    return out


def _bars_for(data, symbol):
    """The engine hands bars as {sym: {"bars": [...]}} or {sym: [...]}."""
    if not isinstance(data, dict):
        return []
    entry = data.get(symbol)
    if isinstance(entry, dict):
        return entry.get("bars") or []
    return entry or []


def _num(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("nan")


def frames_from_engine_bars(data, symbols, as_of, *,
                            lookback_days: int = LIVE_WINDOW_DAYS) -> dict:
    """BACKTEST bars -> ST's per-symbol daily frames, point-in-time.

    Keeps only sessions STRICTLY BEFORE the NY date of `as_of` (a daily bar
    stamped with the session carries its 16:00 close) and within the same
    calendar window ST's live fetch uses. Daily bars keep their date label
    (Alpaca stamps 1Day bars 04:00/05:00Z); intraday bars roll up by NY
    session (first open, max high, min low, last close, summed volume). A
    NaN close stays NaN: get_latest_indicators' guard owns that case.
    """
    cutoff = clock.ny_date(as_of)
    floor = (date.fromisoformat(cutoff) - timedelta(days=int(lookback_days))).isoformat()
    out = {}
    for symbol in symbols or []:
        stamped = []
        for bar in _bars_for(data, symbol):
            if not isinstance(bar, dict):
                continue
            ts = clock.as_utc(bar.get("t") or bar.get("timestamp") or bar.get("date"))
            if ts is not None:
                stamped.append((ts, bar))
        if not stamped:
            continue
        stamped.sort(key=lambda item: item[0])
        gaps = [(b[0] - a[0]).total_seconds() for a, b in zip(stamped, stamped[1:])
                if (b[0] - a[0]).total_seconds() > 0]
        daily = not gaps or min(gaps) >= 23 * 3600
        rows = []
        for ts, bar in stamped:
            day = (ts.date() if daily else ts.astimezone(clock.NY).date()).isoformat()
            if not (floor <= day < cutoff):
                continue
            rows.append((day, _num(bar.get("o", bar.get("open"))),
                         _num(bar.get("h", bar.get("high"))),
                         _num(bar.get("l", bar.get("low"))),
                         _num(bar.get("c", bar.get("close"))),
                         _num(bar.get("v", bar.get("volume")))))
        if not rows:
            continue
        frame = pd.DataFrame(rows, columns=["Date", "Open", "High", "Low", "Close", "Volume"])
        if daily:
            frame = frame.drop_duplicates("Date", keep="last")
        else:
            frame = frame.groupby("Date", sort=True).agg(
                {"Open": "first", "High": "max", "Low": "min",
                 "Close": "last", "Volume": "sum"}).reset_index()
        frame.index = pd.DatetimeIndex(pd.to_datetime(frame.pop("Date")))
        frame.index.name = "Date"
        out[symbol] = frame.sort_index()
    return out
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 -m pytest backend/tests/test_swing_market_data.py -q -p no:cacheprovider`
Expected: PASS (16 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/swing_trader/market_data.py backend/tests/test_swing_market_data.py
git commit -m "feat(swing): port ST market data with injected credentials

Batched SIP daily bars and the IEX latest trade are ST's code with the
client passed in from the engine's injected credentials. Stale IEX prints
fall back to yfinance, and backtest bars become ST's frames strictly before
the NY date.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016ztXjLWXu61Dpp4XMU2jsS"
```

---

### Task 8: Wheel screening, expiry and the scan guard (B5, part 1)

**Files:**
- Create: `backend/swing_trader/wheel_rules.py`
- Test: `backend/tests/test_swing_wheel_screen.py`

**Interfaces:**
- Consumes: `swing_trader.constants`, `swing_trader.indicators` (`_rsi`, `_sma`, `_atr`); vendored `wheel_trader_pure` (Task 2).
- Produces (`swing_trader.wheel_rules`):
  - `next_friday(today: date, calendar, *, min_dte=7, log=None) -> str` — `calendar(start, end) -> set[date]`; an exception from it is ST's fallback.
  - `screen_technicals(raw, symbols, *, cfg=None, log=None) -> list[dict]` — pre-candidates with keys `symbol, stock_price, strike_price, otm_pct, est_premium, est_premium_pct, rsi, sma50, atr, atr_pct`.
  - `wheel_earnings_days(symbol) -> int | None` (yfinance, ST's exemptions).
  - `apply_earnings(pre, earnings_days, *, expiry, earnings_block_days=7) -> dict | None` — adds `expiry` and `earnings_days` (ST's candidate dict).
  - `get_candidates(raw, symbols, *, expiry, earnings_days_for=None, cfg=None, log=None) -> list[dict]`.
  - `sector_filter(candidates, *, max_per_sector=2, sector_map=WHEEL_SECTOR_MAP, log=None) -> list[dict]`.
  - `scan_already_ran_this_week(marker, today) -> bool`.
  - `occ_parts(symbol) -> tuple[str, str, str, float] | None` — `(root, "YYYY-MM-DD", "put"|"call", strike)` from the fixed-width OCC suffix; used ONLY for working orders, whose `OrderRef` carries nothing but the symbol.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_swing_wheel_screen.py`:

```python
"""Wheel screening and expiry, pinned to ST wheel_trader.py, plus ST's
tests/test_wheel_scan_guard.py ported to the cache marker."""
import importlib.util
import os
import sys
import types
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from swing_trader import wheel_rules  # noqa: E402

_ST_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "fixtures", "swing_trader_st")


def _st():
    spec = importlib.util.spec_from_file_location(
        "_swing_st_wheel", os.path.join(_ST_DIR, "wheel_trader_pure.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _frame(seed, n=90, drift=0.001, vol=0.025):
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(drift, vol, n)))
    high = close * (1 + rng.uniform(0.005, 0.03, n))
    low = close * (1 - rng.uniform(0.005, 0.03, n))
    idx = pd.date_range("2026-02-02", periods=n, freq="B")
    return pd.DataFrame({"Open": close, "High": high, "Low": low, "Close": close,
                         "Volume": rng.integers(1_000_000, 3_000_000, n).astype(float)},
                        index=idx)


def _fake_yf(earnings, calls):
    today = date.today()

    class _T:
        def __init__(self, symbol):
            calls.append(symbol)
            self.symbol = symbol

        @property
        def calendar(self):
            days = earnings.get(self.symbol)
            if days is None:
                return {}
            return {"Earnings Date": [pd.Timestamp(today + timedelta(days=days))]}

    return types.SimpleNamespace(Ticker=_T)


def test_get_candidates_matches_st(monkeypatch):
    st = _st()
    raw = {f"S{i:02d}": _frame(i) for i in range(40)}
    earnings = {"S02": 3, "S07": 10}
    st.yf = _fake_yf(earnings, [])
    st.next_friday = lambda: "2026-06-12"
    ours_calls = []
    monkeypatch.setattr(wheel_rules, "yf", _fake_yf(earnings, ours_calls))
    theirs = st.get_candidates(raw, list(raw))
    ours = wheel_rules.get_candidates(raw, list(raw), expiry="2026-06-12")
    assert ours == theirs
    assert [c["symbol"] for c in ours][:1] == ["S07"]    # S02 dropped by earnings
    assert "S02" not in {c["symbol"] for c in ours}
    # Earnings are looked up only for names that passed every other filter.
    assert len(ours_calls) < len(raw)


def test_config_thresholds_are_honoured(monkeypatch):
    monkeypatch.setattr(wheel_rules, "yf", _fake_yf({}, []))
    raw = {f"S{i:02d}": _frame(i) for i in range(40)}
    base = wheel_rules.get_candidates(raw, list(raw), expiry="2026-06-12")
    tight = wheel_rules.get_candidates(raw, list(raw), expiry="2026-06-12",
                                       cfg={"rsi_max": 50})
    assert len(tight) < len(base)
    assert all(c["rsi"] <= 50 for c in tight)


def test_short_or_missing_history_is_skipped():
    raw = {"SHORT": _frame(1, n=40), "OK": _frame(2)}
    pre = wheel_rules.screen_technicals(raw, ["SHORT", "MISSING", "OK"])
    assert {p["symbol"] for p in pre} <= {"OK"}


def test_apply_earnings_blocks_within_seven_days_inclusive():
    pre = {"symbol": "X", "stock_price": 10.0, "strike_price": 9.5, "otm_pct": 5.0,
           "est_premium": 0.1, "est_premium_pct": 1.0, "rsi": 45.0, "sma50": 9.0,
           "atr": 0.4, "atr_pct": 4.0}
    assert wheel_rules.apply_earnings(pre, 7, expiry="2026-06-12") is None
    c = wheel_rules.apply_earnings(pre, 8, expiry="2026-06-12")
    assert c["expiry"] == "2026-06-12" and c["earnings_days"] == 8
    assert wheel_rules.apply_earnings(pre, None, expiry="2026-06-12")["earnings_days"] is None


def test_etf_and_flaky_names_skip_the_earnings_call(monkeypatch):
    calls = []
    monkeypatch.setattr(wheel_rules, "yf", _fake_yf({"SPY": 1, "NVDA": 1}, calls))
    assert wheel_rules.wheel_earnings_days("SPY") is None
    assert wheel_rules.wheel_earnings_days("NVDA") is None
    assert calls == []


def _calendar_client(sessions):
    class _Client:
        def get_calendar(self, req):
            return [types.SimpleNamespace(date=d) for d in sorted(sessions)
                    if req.start <= d <= req.end]
    return _Client()


def _weekdays(start, end, holidays=()):
    out, d = set(), start
    while d <= end:
        if d.weekday() < 5 and d not in holidays:
            out.add(d)
        d += timedelta(days=1)
    return out


def test_next_friday_matches_st_across_a_good_friday(monkeypatch):
    st = _st()
    good_friday = date(2026, 4, 3)
    for holidays in ((good_friday,), (good_friday, date(2026, 4, 2))):
        sessions = _weekdays(date(2026, 3, 1), date(2026, 6, 30), holidays)
        st._get_trading_client = lambda: _calendar_client(sessions)
        day = date(2026, 3, 16)
        while day <= date(2026, 4, 17):
            frozen = day

            class _Frozen(datetime):
                @classmethod
                def now(cls, tz=None):
                    return cls(frozen.year, frozen.month, frozen.day, 10, 30, tzinfo=tz)

            monkeypatch.setattr(st, "datetime", _Frozen)
            ours = wheel_rules.next_friday(
                day, lambda a, b: {d for d in sessions if a <= d <= b})
            assert ours == st.next_friday(), day
            day += timedelta(days=1)


def test_next_friday_known_answers_and_fallback():
    sessions = _weekdays(date(2026, 3, 1), date(2026, 6, 30), (date(2026, 4, 3),))
    cal = lambda a, b: {d for d in sessions if a <= d <= b}  # noqa: E731
    assert wheel_rules.next_friday(date(2026, 3, 23), cal) == "2026-04-02"   # Monday
    assert wheel_rules.next_friday(date(2026, 6, 1), cal) == "2026-06-12"    # Monday, 11 DTE

    def broken(a, b):
        raise RuntimeError("calendar down")

    assert wheel_rules.next_friday(date(2026, 3, 23), broken) == "2026-04-03"


def test_sector_filter_caps_mapped_sectors_and_passes_unknown():
    cands = [{"symbol": s} for s in ("AAPL", "MSFT", "NVDA", "JPM", "ZZ1", "ZZ2", "ZZ3")]
    kept = [c["symbol"] for c in wheel_rules.sector_filter(cands, max_per_sector=2)]
    assert kept == ["AAPL", "MSFT", "JPM", "ZZ1", "ZZ2", "ZZ3"]


# -- ST tests/test_wheel_scan_guard.py, on the strategy_cache marker ---------

def test_no_marker_returns_false():
    assert wheel_rules.scan_already_ran_this_week(None, date(2026, 6, 2)) is False


def test_marker_from_this_week_returns_true():
    assert wheel_rules.scan_already_ran_this_week("2026-06-01", date(2026, 6, 2)) is True


def test_marker_from_last_week_returns_false():
    assert wheel_rules.scan_already_ran_this_week("2026-05-26", date(2026, 6, 2)) is False


def test_corrupt_marker_fails_open():
    assert wheel_rules.scan_already_ran_this_week("not-a-timestamp", date(2026, 6, 2)) is False


def test_occ_parts_reads_the_fixed_width_suffix():
    assert wheel_rules.occ_parts("APH261002P00130000") == ("APH", "2026-10-02", "put", 130.0)
    assert wheel_rules.occ_parts("BRKB261002C00480500") == ("BRKB", "2026-10-02", "call", 480.5)
    assert wheel_rules.occ_parts("AAPL") is None
    assert wheel_rules.occ_parts("APH261002X00130000") is None
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest backend/tests/test_swing_wheel_screen.py -q -p no:cacheprovider`
Expected: FAIL — `ImportError: cannot import name 'wheel_rules' from 'swing_trader'`.

- [ ] **Step 3: Write the implementation**

Create `backend/swing_trader/wheel_rules.py`:

```python
"""Wheel rules, ported from ST wheel_trader.py and app.py.

    wheel_trader.py:765-834    next_friday: Alpaca's calendar API became an
                               injected `calendar(start, end) -> set[date]`
    wheel_trader.py:839-953    get_candidates, split so the one network call
                               (earnings) runs only for names that passed every
                               technical filter; the candidate set is identical
                               (pinned by test_get_candidates_matches_st)
    wheel_trader.py:1307-1318  main()'s per-sector cap -> sector_filter
    wheel_trader.py:1220-1252  _scan_already_ran_this_week; the completion
                               marker lives in strategy_cache
Task 9 adds the order build (get_put_delta, place_put_order) and Task 10 the
position rules (check_open_wheel_positions, check_assigned_positions and
app.py's api_check_wheel_positions).

Operator-approved fixes (spec §9): 1 duplicate put, 3 the cap counts existing
puts, 6 the contract type is checked, 8 cash not margin, 9 the full chain,
10 contract fields instead of an OCC regex.
"""
from __future__ import annotations

import datetime as dt_module
from datetime import date, datetime, timedelta

import pandas as pd
import yfinance as yf

from swing_trader.constants import (
    ATR_PERIOD,
    ETF_NO_EARNINGS,
    MIN_DTE,
    MIN_OPTION_PREMIUM_PCT,
    RSI_MAX,
    RSI_MIN,
    SMA50_PERIOD,
    STRIKE_ATR_MULT,
    WHEEL_MAX_PER_SECTOR,
    WHEEL_SECTOR_MAP,
    YFINANCE_FLAKY,
)
from swing_trader.indicators import _atr, _rsi, _sma

try:
    from intellistock_logger import intellistock_logger as _ilog  # type: ignore

    def _log(msg, color="white"):
        _ilog.log(str(msg), color, service="StrategyWheel")
except Exception:  # pragma: no cover - standalone/test import
    def _log(msg, color="white"):
        print(f"[StrategyWheel] {msg}")


def next_friday(today: date, calendar, *, min_dte: int = MIN_DTE, log=None) -> str:
    """
    Return the date string (YYYY-MM-DD) of the next valid options expiry
    Friday that is at least MIN_DTE days away.

    1. Find the next Friday that is MIN_DTE+ days away
    2. Ask the calendar if that Friday is a trading day
    3. If yes — use it
    4. If no — check the Thursday of that week (exchange sometimes moves
       expiry to Thursday before a holiday Friday)
    5. If Thursday is also closed — skip to the following Friday and repeat
    Falls back to the pure date calculation if the calendar is unavailable.
    """
    log = log or _log
    days_ahead = (4 - today.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    candidate = today + timedelta(days=days_ahead)

    # Enforce MIN_DTE
    if (candidate - today).days < int(min_dte):
        candidate += timedelta(days=7)

    try:
        # Fetch calendar for a 3-week window around candidate
        trading_days = calendar(candidate - timedelta(days=1),
                                candidate + timedelta(days=14))

        max_attempts = 8   # safety limit — never loop forever
        attempts     = 0
        while attempts < max_attempts:
            if candidate in trading_days:
                log(f"  [next_friday] Expiry: {candidate} (confirmed trading day via Alpaca calendar)")
                return str(candidate)

            # Friday is a holiday — check Thursday of same week
            thursday = candidate - timedelta(days=1)
            if thursday in trading_days:
                log(f"  [next_friday] {candidate} is a non-trading day — using Thursday {thursday} (holiday expiry)")
                return str(thursday)

            # Both closed — skip to next Friday
            log(f"  [next_friday] {candidate} is a non-trading day, Thursday also closed — skipping to following Friday")
            candidate += timedelta(days=7)
            attempts  += 1

        # Exhausted attempts — fall through to date-only result
        log(f"  [next_friday] WARNING: could not confirm trading day after {max_attempts} attempts — using {candidate}")
        return str(candidate)

    except Exception as exc:
        # Calendar unavailable — fall back to date calculation only
        log(f"  [next_friday] Calendar API unavailable ({exc}) — using date calculation fallback")
        return str(candidate)


def screen_technicals(raw: dict, symbols: list, *, cfg: dict | None = None,
                      log=None) -> list[dict]:
    """wheel_trader.py:839-953 without the earnings lookup and the expiry.

    Screening criteria (ALL must pass):
    1. RSI(14) between RSI_MIN and RSI_MAX — not overbought, not collapsing
    2. Close > SMA(50) — stock in uptrend (we want puts on rising stocks)
    3. ATR% > 1.5% — enough volatility to generate meaningful premium
    4. Estimated weekly premium > MIN_OPTION_PREMIUM_PCT of stock price
    6. Strike at least 1.5% OTM
    (5, no earnings within 7 days, is apply_earnings.)
    """
    cfg = cfg or {}
    log = log or _log
    rsi_min = cfg.get("rsi_min", RSI_MIN)
    rsi_max = cfg.get("rsi_max", RSI_MAX)
    sma_n = int(cfg.get("sma_trend", SMA50_PERIOD))
    atr_n = int(cfg.get("atr_period", ATR_PERIOD))
    strike_atr_mult = float(cfg.get("strike_atr_mult", STRIKE_ATR_MULT))
    min_premium_pct = float(cfg.get("min_premium_pct", MIN_OPTION_PREMIUM_PCT))
    out = []
    for symbol in symbols:
        try:
            # raw is {symbol: DataFrame} from market_data (R8-05)
            df = raw.get(symbol)
            if df is None:
                continue
            df = df.copy().dropna(subset=["Close"]).reset_index()

            if len(df) < sma_n + 5:
                log(f"  [wheel] {symbol}: insufficient data ({len(df)} bars), skipping")
                continue

            close  = df["Close"]
            high   = df["High"]
            low    = df["Low"]

            rsi_series = _rsi(close)
            sma50      = _sma(close, sma_n)
            atr_series = _atr(high, low, close, atr_n)

            last_close = float(close.iloc[-1])
            last_rsi   = float(rsi_series.iloc[-1]) if not pd.isna(rsi_series.iloc[-1]) else None
            last_sma50 = float(sma50.iloc[-1])       if not pd.isna(sma50.iloc[-1])     else None
            last_atr   = float(atr_series.iloc[-1])  if not pd.isna(atr_series.iloc[-1]) else None

            if any(v is None for v in [last_rsi, last_sma50, last_atr]):
                log(f"  [wheel] {symbol}: indicator NaN, skipping")
                continue

            atr_pct = last_atr / last_close * 100

            # Strike selection: ATR-based OTM put
            strike_price = round(last_close - (last_atr * strike_atr_mult), 2)
            otm_pct      = (last_close - strike_price) / last_close * 100

            # Approximate weekly premium as 25% of ATR (rough Black-Scholes proxy)
            est_premium     = round(last_atr * 0.25, 2)
            est_premium_pct = est_premium / last_close * 100

            reasons_failed = []
            if not (rsi_min <= last_rsi <= rsi_max):
                reasons_failed.append(f"RSI {last_rsi:.1f} out of [{rsi_min},{rsi_max}]")
            if last_close < last_sma50:
                reasons_failed.append(f"below SMA50 (${last_close:.2f} < ${last_sma50:.2f})")
            if atr_pct < 1.5:
                reasons_failed.append(f"ATR% {atr_pct:.2f}% < 1.5%")
            if est_premium_pct < min_premium_pct * 100:
                reasons_failed.append(f"est. premium {est_premium_pct:.3f}% < {min_premium_pct*100:.2f}%")
            if otm_pct < 1.5:
                reasons_failed.append(f"strike only {otm_pct:.1f}% OTM (min 1.5%) — too close to ATM")

            if reasons_failed:
                log(f"  [wheel] {symbol}: FILTERED — {'; '.join(reasons_failed)}")
                continue

            out.append({
                "symbol":          symbol,
                "stock_price":     last_close,
                "strike_price":    strike_price,
                "otm_pct":         round(otm_pct, 2),
                "est_premium":     est_premium,
                "est_premium_pct": round(est_premium_pct, 3),
                "rsi":             round(last_rsi, 1),
                "sma50":           round(last_sma50, 2),
                "atr":             round(last_atr, 2),
                "atr_pct":         round(atr_pct, 2),
            })

        except Exception as exc:
            log(f"  [wheel] {symbol}: ERROR — {exc}")
            continue

    return out


def wheel_earnings_days(symbol: str):
    """wheel_trader.py:891-909. ETFs have no earnings calendar and three names
    were yfinance-flaky after hours in ST, so those are None without a call."""
    earnings_days = None
    try:
        if symbol.upper() in ETF_NO_EARNINGS or symbol.upper() in YFINANCE_FLAKY:
            earnings_days = None
        else:
            cal = yf.Ticker(symbol).calendar
            if cal:
                raw_dates = cal.get("Earnings Date")
                if raw_dates is not None:
                    today = dt_module.date.today()
                    items = raw_dates if hasattr(raw_dates, "__iter__") and not isinstance(raw_dates, str) else [raw_dates]
                    future = sorted(pd.Timestamp(d).date() for d in items if pd.Timestamp(d).date() >= today)
                    earnings_days = (future[0] - today).days if future else None
    except Exception:
        earnings_days = None
    return earnings_days


def apply_earnings(pre: dict, earnings_days, *, expiry: str,
                   earnings_block_days: int = 7):
    """wheel_trader.py:921-943: the earnings filter, then ST's candidate dict."""
    if earnings_days is not None and earnings_days <= int(earnings_block_days):
        return None
    return {
        "symbol":          pre["symbol"],
        "stock_price":     pre["stock_price"],
        "strike_price":    pre["strike_price"],
        "otm_pct":         pre["otm_pct"],
        "expiry":          expiry,
        "est_premium":     pre["est_premium"],
        "est_premium_pct": pre["est_premium_pct"],
        "rsi":             pre["rsi"],
        "sma50":           pre["sma50"],
        "atr":             pre["atr"],
        "atr_pct":         pre["atr_pct"],
        "earnings_days":   earnings_days,
    }


def get_candidates(raw: dict, symbols: list, *, expiry: str, earnings_days_for=None,
                   cfg: dict | None = None, log=None) -> list[dict]:
    log = log or _log
    earnings_days_for = earnings_days_for or wheel_earnings_days
    block = int((cfg or {}).get("earnings_block_days", 7))
    candidates = []
    for pre in screen_technicals(raw, symbols, cfg=cfg, log=log):
        earnings_days = earnings_days_for(pre["symbol"])
        c = apply_earnings(pre, earnings_days, expiry=expiry, earnings_block_days=block)
        if c is None:
            log(f"  [wheel] {pre['symbol']}: FILTERED — earnings in {earnings_days} days")
            continue
        candidates.append(c)
        log(f"  [wheel] {c['symbol']}: CANDIDATE  RSI {c['rsi']:.1f}  "
            f"strike ${c['strike_price']}  premium ~${c['est_premium']}/share  exp {expiry}")
    return candidates


def sector_filter(candidates: list, *, max_per_sector: int = WHEEL_MAX_PER_SECTOR,
                  sector_map: dict = WHEEL_SECTOR_MAP, log=None) -> list:
    """wheel_trader.py:1308-1318 — limit candidates per sector before AI scoring."""
    log = log or _log
    sector_counts: dict[str, int] = {}
    filtered_candidates = []
    for c in candidates:
        sector = sector_map.get(c["symbol"], "unknown")
        count  = sector_counts.get(sector, 0)
        if sector == "unknown" or count < int(max_per_sector):
            filtered_candidates.append(c)
            sector_counts[sector] = count + 1
        else:
            log(f"  [wheel] {c['symbol']}: sector cap reached ({sector}, max {max_per_sector})")
    return filtered_candidates


def scan_already_ran_this_week(marker, today: date) -> bool:
    """wheel_trader.py:1220-1252: True only if a scan COMPLETED this week, per
    the completion marker (a NY session date written after the whole scan
    finished), never because a partial scan left rows behind. Any error reading
    the marker returns False: an extra Tuesday scan is far cheaper than
    skipping the week's only successful scan."""
    if not marker:
        return False
    try:
        monday = today - timedelta(days=today.weekday())
        marker_date = datetime.strptime(str(marker).split()[0], "%Y-%m-%d").date()
        return marker_date >= monday
    except Exception:
        return False


def occ_parts(symbol):
    """(root, expiry, 'put'|'call', strike) from an OCC symbol's fixed-width
    suffix (YYMMDD + C/P + 8-digit strike ×1000), or None. Positions are read
    from Alpaca's contract fields (fix 10); this is only for WORKING ORDERS,
    whose OrderRef carries the symbol and nothing else."""
    s = str(symbol or "").strip().upper()
    if len(s) < 16:
        return None
    root, ymd, cp, strike8 = s[:-15], s[-15:-9], s[-9], s[-8:]
    if not root or not ymd.isdigit() or cp not in ("P", "C") or not strike8.isdigit():
        return None
    return (root, f"20{ymd[:2]}-{ymd[2:4]}-{ymd[4:6]}",
            "put" if cp == "P" else "call", int(strike8) / 1000.0)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 -m pytest backend/tests/test_swing_wheel_screen.py -q -p no:cacheprovider`
Expected: PASS (13 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/swing_trader/wheel_rules.py backend/tests/test_swing_wheel_screen.py
git commit -m "feat(swing): port the wheel screen, expiry and weekly scan guard

get_candidates, next_friday and the per-sector cap match ST on fixtures;
the earnings call runs only for names that passed the technical filters,
which yields the same candidate set with far fewer network calls.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016ztXjLWXu61Dpp4XMU2jsS"
```

---

### Task 9: Wheel order build — delta pick, limit ladder, caps, dedupe (B5, part 2)

**Files:**
- Modify: `backend/swing_trader/wheel_rules.py` (append)
- Test: `backend/tests/test_swing_wheel_orders.py`

**Interfaces:**
- Consumes: Task 8's `occ_parts`; contract §3 DTO shapes, read by attribute only (`OptionContractDTO.symbol/underlying/option_type/strike/expiration/close_price`, `OptionSnapshotDTO.bid/delta`, `OptionPositionDTO.symbol/underlying/option_type/strike/expiry/qty/avg_entry_price/market_value`, `OrderRef.symbol/side/qty/filled_qty/position_intent`). Tests use `SimpleNamespace`, so this task does not need A-live.
- Consumes (live, plan A-live): `adapter.get_option_contracts(underlying, *, option_type, expiration_gte, expiration_lte)` (all pages, fix 9), `adapter.get_option_snapshots(contracts)`.
- Produces:
  - `nearby_contracts(contracts, target_strike) -> list`
  - `pick_put_by_delta(nearby, snapshots, *, target_delta=0.25) -> tuple[float|None, str|None, float|None, float|None]` (strike, contract, bid, |Δ−target|)
  - `limit_price_ladder(*, delta_bid, contract_close, est_premium, bid_mult=0.95) -> tuple[float, str]`
  - `short_put_collateral(option_positions) -> tuple[float, dict[str, float]]`, `pending_sto_collateral(open_orders) -> tuple[float, dict[str, float]]`
  - `duplicate_put_reason(symbol, option_positions, open_orders, committed=()) -> str | None` (fix 1)
  - `size_contracts(*, requested, strike, equity, cash, max_collateral_pct=0.25, existing_underlying_collateral=0.0, reserved_collateral=0.0) -> tuple[int, str | None, list[str]]` (fixes 3, 8)
  - `build_put_order(candidate, *, chain, snapshots, cfg=None, equity, cash, existing_underlying_collateral=0.0, reserved_collateral=0.0, signal_id=None, session=None) -> tuple[dict | None, str | None, dict]` — the order is contract §1's option order dict plus `session`.
  - `build_put_order_live(candidate, *, adapter, cfg, equity, cash, option_positions, open_orders, committed_collateral=None, signal_id=None, session=None) -> tuple[dict | None, str | None, dict]`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_swing_wheel_orders.py`:

```python
"""The put order: ST's collateral tests (tests/test_collateral_cap.py) ported,
the delta pick and price ladder of place_put_order, and fixes 1, 3, 8, 9."""
import os
import sys
from types import SimpleNamespace as NS

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from swing_trader import wheel_rules as wr  # noqa: E402

EXP = "2026-06-12"


def contract(strike, symbol=None, close=2.0, expiry=EXP, kind="put", underlying="Q"):
    return NS(symbol=symbol or f"{underlying}260612P{int(strike * 1000):08d}",
              underlying=underlying, option_type=kind, strike=strike,
              expiration=expiry, open_interest=100, close_price=close)


def snap(delta, bid):
    return NS(bid=bid, ask=None, last=None, iv=None, delta=delta, gamma=None,
              theta=None, vega=None, quote_ts=None)


def candidate(symbol, strike, contracts=1, est_premium=2.0):
    return {"symbol": symbol, "strike_price": strike, "expiry": EXP,
            "position_size_contracts": contracts, "est_premium": est_premium}


def order_for(strike, contracts=1, *, equity=100_000, cash=150_000, bid=1.80,
              existing=0.0, reserved=0.0):
    c = contract(strike)
    return wr.build_put_order(candidate("Q", strike, contracts), chain=[c],
                              snapshots={c.symbol: snap(-0.25, bid)}, equity=equity,
                              cash=cash, existing_underlying_collateral=existing,
                              reserved_collateral=reserved, signal_id="sig-1",
                              session="2026-06-01")


# -- ST tests/test_collateral_cap.py -----------------------------------------

def test_put_blocked_when_strike_exceeds_collateral_cap():
    order, error, _ = order_for(790.0, bid=30.0)
    assert order is None and "Collateral cap" in error


def test_contracts_reduced_to_fit_collateral_cap():
    order, error, meta = order_for(100.0, contracts=3, bid=1.50)
    assert error is None and order["qty"] == 2
    assert any("reducing 3 → 2" in n for n in meta["notes"])


def test_put_within_cap_placed_unchanged():
    order, error, _ = order_for(130.0, bid=1.80)
    assert error is None and order["qty"] == 1


# -- fixes -------------------------------------------------------------------

def test_fix_3_the_cap_counts_puts_already_on_the_underlying():
    order, error, _ = order_for(130.0, existing=13_000.0)
    assert order is None and "Collateral cap" in error
    order, error, _ = order_for(100.0, contracts=2, existing=5_000.0)
    assert order["qty"] == 2       # 25k cap - 5k = 20k fits two $10k puts


def test_fix_8_collateral_is_checked_against_cash_not_margin():
    order, error, _ = order_for(130.0, cash=20_000.0, reserved=13_000.0)
    assert order is None and error.startswith("Insufficient cash")
    order, _, meta = order_for(100.0, contracts=2, cash=30_000.0, reserved=15_000.0)
    assert order["qty"] == 1 and any("reducing to 1" in n for n in meta["notes"])


def test_fix_1_duplicates_are_refused():
    short_put = NS(symbol="Q260605P00100000", underlying="Q", option_type="put",
                   strike=100.0, expiry="2026-06-05", qty=-1, avg_entry_price=1.0,
                   market_value=-100.0)
    short_call = NS(symbol="Q260605C00150000", underlying="Q", option_type="call",
                    strike=150.0, expiry="2026-06-05", qty=-1, avg_entry_price=1.0,
                    market_value=-100.0)
    working = NS(symbol="Q260612P00095000", side="sell", qty=1, filled_qty=0,
                 position_intent="sell_to_open")
    assert "open short put" in wr.duplicate_put_reason("Q", [short_put], [])
    assert wr.duplicate_put_reason("Q", [short_call], []) is None
    assert "working sell order" in wr.duplicate_put_reason("Q", [], [working])
    assert "already ordered" in wr.duplicate_put_reason("q", [], [], {"Q"})
    assert wr.duplicate_put_reason("R", [short_put], [working]) is None


def test_collateral_totals_read_contract_fields_and_working_orders():
    positions = [NS(symbol="A1", underlying="A", option_type="put", strike=50.0, qty=-2),
                 NS(symbol="A2", underlying="A", option_type="call", strike=60.0, qty=-1),
                 NS(symbol="B1", underlying="B", option_type="put", strike=20.0, qty=1)]
    assert wr.short_put_collateral(positions) == (10_000.0, {"A": 10_000.0})
    orders = [NS(symbol="C260612P00040000", side="sell", qty=3, filled_qty=1,
                 position_intent="sell_to_open"),
              NS(symbol="C260612P00040000", side="buy", qty=1, filled_qty=0,
                 position_intent="buy_to_close"),
              NS(symbol="AAPL", side="sell", qty=10, filled_qty=0, position_intent=None)]
    assert wr.pending_sto_collateral(orders) == (8_000.0, {"C": 8_000.0})


# -- place_put_order's contract choice and price ------------------------------

def test_the_contract_closest_to_the_target_delta_wins():
    chain = [contract(95.0), contract(100.0), contract(105.0)]
    snaps = {chain[0].symbol: snap(-0.18, 0.9), chain[1].symbol: snap(-0.24, 1.4),
             chain[2].symbol: snap(-0.33, 2.2)}
    order, _, meta = wr.build_put_order(candidate("Q", 100.0), chain=chain, snapshots=snaps,
                                        equity=100_000, cash=100_000)
    assert order["contract"] == chain[1].symbol and order["strike"] == 100.0
    assert order["limit_price"] == round(1.4 * 0.95, 2) and meta["price_source"] == "bid"


def test_no_greeks_falls_back_to_the_highest_strike_at_or_below_target():
    chain = [contract(95.0, close=1.5), contract(99.0, close=1.2), contract(101.0)]
    order, _, meta = wr.build_put_order(candidate("Q", 100.0), chain=chain,
                                        snapshots={}, equity=100_000, cash=100_000)
    assert order["strike"] == 99.0
    assert order["limit_price"] == round(1.2 * 0.90, 2) and meta["price_source"] == "close"


def test_the_price_ladder():
    assert wr.limit_price_ladder(delta_bid=1.00, contract_close=2.0, est_premium=0.5) == (0.95, "bid")
    assert wr.limit_price_ladder(delta_bid=0.04, contract_close=2.0, est_premium=0.5) == (1.8, "close")
    assert wr.limit_price_ladder(delta_bid=0.0, contract_close=None, est_premium=0.5) == (0.5, "est_premium")


def test_a_sub_penny_limit_is_refused():
    c = contract(100.0, close=None)
    order, error, _ = wr.build_put_order(candidate("Q", 100.0, est_premium=0.004), chain=[c],
                                         snapshots={}, equity=100_000, cash=100_000)
    assert order is None and error.startswith("Limit price too low")


def test_the_order_is_the_contract_option_order_shape():
    order, _, _ = order_for(130.0)
    assert order == {
        "signal_id": "sig-1", "session": "2026-06-01", "underlying": "Q",
        "contract": order["contract"], "option_type": "put", "strike": 130.0,
        "expiry": EXP, "position_intent": "sell_to_open", "qty": 1,
        "order_type": "limit", "limit_price": round(1.80 * 0.95, 2), "tif": "day",
        "reason": "wheel_sto_put"}


def test_no_chain_for_the_expiry_is_a_skip_not_a_crash():
    other = contract(100.0, expiry="2026-06-19")
    order, error, _ = wr.build_put_order(candidate("Q", 100.0), chain=[other],
                                         snapshots={}, equity=100_000, cash=100_000)
    assert order is None and "No put contracts" in error


def test_fix_9_the_live_build_reads_the_whole_chain_through_the_adapter():
    chain = [contract(90.0 + i) for i in range(30)]

    class Adapter:
        def __init__(self):
            self.calls = []

        def get_option_contracts(self, underlying, **kw):
            self.calls.append((underlying, kw))
            return chain

        def get_option_snapshots(self, symbols):
            self.snap_request = list(symbols)
            return {s: snap(-0.25 if s == chain[20].symbol else -0.05, 1.0) for s in symbols}

    a = Adapter()
    order, error, _ = wr.build_put_order_live(
        candidate("Q", 110.0), adapter=a, cfg={}, equity=100_000, cash=100_000,
        option_positions=[], open_orders=[], signal_id="s", session="2026-06-01")
    assert a.calls == [("Q", {"option_type": "put", "expiration_gte": EXP,
                              "expiration_lte": EXP})]
    assert error is None and order["contract"] == chain[20].symbol
    assert all(abs(float(s[-8:]) / 1000 - 110.0) / 110.0 <= 0.15 for s in a.snap_request)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest backend/tests/test_swing_wheel_orders.py -q -p no:cacheprovider`
Expected: FAIL — `AttributeError: module 'swing_trader.wheel_rules' has no attribute 'build_put_order'`.

- [ ] **Step 3: Append the implementation**

Append to `backend/swing_trader/wheel_rules.py`, and extend its constants import to add `LIMIT_BID_MIN, LIMIT_BID_MULT, LIMIT_CLOSE_MULT, MAX_COLLATERAL_PCT, TARGET_DELTA`:

```python
# -- wheel_trader.py:137-227 get_put_delta, over the FULL chain (fix 9) ------

def nearby_contracts(contracts, target_strike: float) -> list:
    """wheel_trader.py:172-180: strikes within 15% of the target keep the
    snapshot request small; none nearby -> the whole chain."""
    nearby = [
        c for c in contracts
        if abs(float(c.strike) - target_strike) / target_strike <= 0.15
    ]
    if not nearby:
        nearby = list(contracts)
    return [c for c in nearby if getattr(c, "symbol", None)]


def pick_put_by_delta(nearby, snapshots, *, target_delta: float = TARGET_DELTA):
    """wheel_trader.py:193-223: the contract whose |delta| is closest to the
    target. Returns (strike, contract, bid, diff) or (None, None, None, None)
    for the ATR fallback. `snapshots` are OptionSnapshotDTOs (contract §3)."""
    best_symbol = None
    best_strike = None
    best_diff   = float("inf")
    best_bid    = 0.0

    for contract_sym, snap in (snapshots or {}).items():
        try:
            delta_raw = getattr(snap, "delta", None)
            if delta_raw is None:
                continue
            delta = abs(float(delta_raw))
            diff  = abs(delta - target_delta)
            if diff < best_diff:
                best_diff   = diff
                best_symbol = contract_sym
                matching = [c for c in nearby if c.symbol == contract_sym]
                if matching:
                    best_strike = float(matching[0].strike)
                try:
                    bid = getattr(snap, "bid", None)
                    best_bid = float(bid) if bid else 0.0
                except Exception:
                    best_bid = 0.0
        except Exception:
            continue

    if best_symbol and best_strike:
        return best_strike, best_symbol, best_bid, best_diff
    return None, None, None, None


# -- wheel_trader.py:531-702 place_put_order --------------------------------

def limit_price_ladder(*, delta_bid, contract_close, est_premium,
                       bid_mult: float = LIMIT_BID_MULT):
    """wheel_trader.py:587-606. Priority: live bid × 0.95 → stale close × 0.90
    → est_premium. Bid-side pricing gets fills; the haircut keeps premium."""
    if delta_bid and delta_bid > LIMIT_BID_MIN:
        return round(delta_bid * bid_mult, 2), "bid"
    close_price = float(contract_close) if contract_close else None
    if close_price and close_price > 0:
        return round(close_price * LIMIT_CLOSE_MULT, 2), "close"
    return round(float(est_premium), 2), "est_premium"


def short_put_collateral(option_positions):
    """Collateral of every open short PUT, total and by underlying, from the
    contract fields (fix 10)."""
    total, by = 0.0, {}
    for p in option_positions or []:
        try:
            qty = float(p.qty)
            if qty >= 0 or str(p.option_type).lower() != "put":
                continue
            c = float(p.strike) * 100 * abs(qty)
        except Exception:
            continue
        u = str(p.underlying).upper()
        total += c
        by[u] = by.get(u, 0.0) + c
    return total, by


def pending_sto_collateral(open_orders):
    """Collateral reserved by working sell-to-open PUT orders (the unfilled
    remainder). An OrderRef names its contract only by symbol."""
    total, by = 0.0, {}
    for o in open_orders or []:
        parts = occ_parts(getattr(o, "symbol", ""))
        if parts is None:
            continue
        root, _expiry, kind, strike = parts
        if kind != "put" or str(getattr(o, "side", "")).lower() != "sell":
            continue
        if getattr(o, "position_intent", None) not in (None, "sell_to_open"):
            continue
        remaining = float(getattr(o, "qty", 0) or 0) - float(getattr(o, "filled_qty", 0) or 0)
        if remaining <= 0:
            continue
        c = strike * 100 * remaining
        total += c
        by[root] = by.get(root, 0.0) + c
    return total, by


def duplicate_put_reason(symbol, option_positions, open_orders, committed=()):
    """Fix 1: a rerun cannot sell a second put on an underlying that already
    has an open short put, a working sell order, or an order from this scan."""
    u = str(symbol).upper()
    for p in option_positions or []:
        try:
            if (float(p.qty) < 0 and str(p.option_type).lower() == "put"
                    and str(p.underlying).upper() == u):
                return f"duplicate: open short put {p.symbol} on {u}"
        except Exception:
            continue
    for o in open_orders or []:
        parts = occ_parts(getattr(o, "symbol", ""))
        if (parts and parts[0] == u and parts[2] == "put"
                and str(getattr(o, "side", "")).lower() == "sell"):
            return f"duplicate: working sell order {o.symbol} on {u}"
    if u in {str(x).upper() for x in (committed or ())}:
        return f"duplicate: a put on {u} was already ordered in this scan"
    return None


def size_contracts(*, requested, strike, equity, cash,
                   max_collateral_pct: float = MAX_COLLATERAL_PCT,
                   existing_underlying_collateral: float = 0.0,
                   reserved_collateral: float = 0.0):
    """wheel_trader.py:611-670. Returns (contracts, error | None, notes).

    Step 3b, the per-name collateral cap (R3-08), now counts the collateral
    already open or working on this underlying (fix 3). Step 3c checks CASH
    net of every open and pending short put instead of margin buying power,
    which let a $79k put through on a $100k account (fix 8)."""
    notes = []
    n_contracts = int(requested)
    max_collateral = equity * max_collateral_pct
    headroom = max_collateral - float(existing_underlying_collateral or 0.0)
    allowed_contracts = int(headroom // (strike * 100)) if headroom > 0 else 0

    if allowed_contracts < 1:
        already = (f" (${existing_underlying_collateral:,.0f} already committed on this name)"
                   if existing_underlying_collateral else "")
        return 0, (f"Collateral cap: strike ${strike*100:,.0f} > {max_collateral_pct:.0%} "
                   f"of equity ${max_collateral:,.0f}{already}"), notes

    if n_contracts > allowed_contracts:
        notes.append(
            f"Collateral cap: reducing {n_contracts} → {allowed_contracts} contract(s) "
            f"(${strike * 100 * n_contracts:,.0f} would exceed {max_collateral_pct:.0%} "
            f"of equity ${max_collateral:,.0f})")
        n_contracts = allowed_contracts

    available = float(cash) - float(reserved_collateral or 0.0)
    required_capital = strike * 100 * n_contracts
    if available < required_capital:
        if available >= strike * 100:
            notes.append(f"insufficient cash for {n_contracts} contracts (need "
                         f"${required_capital:,.0f}, have ${available:,.0f}) — reducing to 1")
            n_contracts = 1
        else:
            return 0, (f"Insufficient cash: need ${strike*100:,.0f}, "
                       f"have ${available:,.0f}"), notes
    return n_contracts, None, notes


def build_put_order(candidate, *, chain, snapshots, cfg=None, equity, cash,
                    existing_underlying_collateral=0.0, reserved_collateral=0.0,
                    signal_id=None, session=None):
    """place_put_order (wheel_trader.py:531-702) as a pure function: pick the
    contract, price it, size it. Returns (order | None, error | None, meta)."""
    cfg = cfg or {}
    symbol        = candidate["symbol"]
    target_strike = float(candidate["strike_price"])
    expiry        = str(candidate["expiry"])[:10]
    n_contracts   = int(candidate.get("position_size_contracts", 1))

    puts = [c for c in (chain or [])
            if str(getattr(c, "option_type", "")).lower() == "put"
            and str(getattr(c, "expiration", ""))[:10] == expiry]
    if not puts:
        return None, f"No put contracts found for {symbol} exp {expiry}", {}

    nearby = nearby_contracts(puts, target_strike)
    delta_strike, delta_contract, delta_bid, delta_diff = pick_put_by_delta(
        nearby, snapshots, target_delta=float(cfg.get("target_delta", TARGET_DELTA)))

    if delta_strike and delta_contract:
        strike_used     = delta_strike
        contract_symbol = delta_contract
        matching        = [c for c in puts if c.symbol == delta_contract]
        best_for_price  = matching[0] if matching else None
    else:
        # Fallback: closest strike at or below ATR-based target
        eligible = [c for c in puts if float(c.strike) <= target_strike]
        if not eligible:
            eligible = puts
        best = max(eligible, key=lambda c: float(c.strike))
        strike_used     = float(best.strike)
        contract_symbol = best.symbol
        delta_bid       = 0.0
        best_for_price  = best

    limit_price, source = limit_price_ladder(
        delta_bid=delta_bid,
        contract_close=getattr(best_for_price, "close_price", None) if best_for_price else None,
        est_premium=candidate["est_premium"],
        bid_mult=float(cfg.get("limit_bid_mult", LIMIT_BID_MULT)))
    if limit_price < 0.01:
        return None, f"Limit price too low (${limit_price}) — skipping order", {}

    n, error, notes = size_contracts(
        requested=n_contracts, strike=strike_used, equity=float(equity), cash=float(cash),
        max_collateral_pct=float(cfg.get("max_collateral_pct", MAX_COLLATERAL_PCT)),
        existing_underlying_collateral=existing_underlying_collateral,
        reserved_collateral=reserved_collateral)
    if error:
        return None, error, {"notes": notes}

    order = {
        "signal_id": signal_id, "session": session, "underlying": symbol,
        "contract": contract_symbol, "option_type": "put", "strike": strike_used,
        "expiry": expiry, "position_intent": "sell_to_open", "qty": int(n),
        "order_type": "limit", "limit_price": float(limit_price), "tif": "day",
        "reason": "wheel_sto_put",
    }
    chosen = (snapshots or {}).get(contract_symbol)
    return order, None, {"notes": notes, "price_source": source,
                         "delta": getattr(chosen, "delta", None), "bid": delta_bid,
                         "delta_diff": delta_diff}


def build_put_order_live(candidate, *, adapter, cfg, equity, cash, option_positions,
                         open_orders, committed_collateral=None, signal_id=None,
                         session=None):
    """build_put_order over the broker adapter: the FULL chain for the expiry
    (plan A-live's get_option_contracts follows every next_page_token — ST
    read one page, fix 9) and snapshots for the nearby strikes."""
    symbol = str(candidate["symbol"]).upper()
    expiry = str(candidate["expiry"])[:10]
    chain = list(adapter.get_option_contracts(
        symbol, option_type="put", expiration_gte=expiry, expiration_lte=expiry) or [])
    puts = [c for c in chain if str(getattr(c, "option_type", "")).lower() == "put"
            and str(getattr(c, "expiration", ""))[:10] == expiry]
    nearby = nearby_contracts(puts, float(candidate["strike_price"])) if puts else []
    try:
        snapshots = adapter.get_option_snapshots([c.symbol for c in nearby]) if nearby else {}
    except Exception as exc:
        _log(f"  [delta] Snapshot error for {symbol}: {exc} — falling back to ATR strike", "yellow")
        snapshots = {}
    held_total, held_by = short_put_collateral(option_positions)
    pend_total, pend_by = pending_sto_collateral(open_orders)
    committed = {str(k).upper(): float(v) for k, v in (committed_collateral or {}).items()}
    existing = held_by.get(symbol, 0.0) + pend_by.get(symbol, 0.0) + committed.get(symbol, 0.0)
    reserved = held_total + pend_total + sum(committed.values())
    return build_put_order(candidate, chain=chain, snapshots=snapshots, cfg=cfg,
                           equity=equity, cash=cash,
                           existing_underlying_collateral=existing,
                           reserved_collateral=reserved, signal_id=signal_id,
                           session=session)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest backend/tests/test_swing_wheel_orders.py backend/tests/test_swing_wheel_screen.py -q -p no:cacheprovider`
Expected: PASS (27 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/swing_trader/wheel_rules.py backend/tests/test_swing_wheel_orders.py
git commit -m "feat(swing): build wheel put orders with ST's delta pick and price ladder

The contract nearest 0.25 delta comes from the full chain (fix 9), priced
at bid x 0.95 then ST's fallbacks. The collateral cap counts puts already
on the name (fix 3), cash replaces margin buying power (fix 8), and a put on
an underlying that already has one is refused (fix 1).

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016ztXjLWXu61Dpp4XMU2jsS"
```

---

### Task 10: Wheel position rules — 2× buy-back, the daily monitor, covered calls (B5, part 3)

**Files:**
- Modify: `backend/swing_trader/wheel_rules.py` (append)
- Test: `backend/tests/test_swing_wheel_positions.py`

**Interfaces:**
- Consumes: Task 8's `occ_parts`; constants `AUTO_CLOSE_ITM_PCT`, `AUTO_CLOSE_DTE2_PCT`.
- Produces:
  - `btc_order(pos, qty, reason, *, signal_id=None, session=None) -> dict` (contract §1, `buy_to_close`, market).
  - `two_x_exits(option_positions, *, open_orders=()) -> list[tuple[dict, dict]]` — (order, `{"cost_basis", "current_value", "loss_ratio"}`).
  - `put_monitor_decision(*, contract, underlying, strike, expiry, stock_price, today) -> dict` with keys `contract, underlying, strike, expiry, stock_price, dte, itm, itm_pct, deep_itm, action ("auto_close"|"alert_itm"|"info_expiring"|None), intent ("wheel_btc_itm"|"wheel_btc_expiry"|None), reason, title, message, priority`.
  - `covered_call_strike(cost_basis) -> float`, `covered_call_candidates(equity_positions, option_positions, open_orders, *, swing_owned=()) -> list[dict]` (`symbol, qty, cost_basis, call_strike, n_contracts`), `dry_run_message(candidate, expiry) -> tuple[str, str]`, `pick_covered_call(calls, call_strike, cost_basis) -> tuple | None`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_swing_wheel_positions.py`:

```python
"""Position rules: ST check_open_wheel_positions (2x premium), app.py
api_check_wheel_positions (the 15:45 monitor), and check_assigned_positions
(ST tests/test_assignment_detection.py, ported to the pure detector)."""
import os
import sys
from datetime import date
from types import SimpleNamespace as NS

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from swing_trader import wheel_rules as wr  # noqa: E402

TODAY = date(2026, 6, 1)


def put(symbol="Q260612P00100000", qty=-1, avg=1.0, mv=-150.0, kind="put",
        underlying="Q", strike=100.0, expiry="2026-06-12"):
    return NS(symbol=symbol, underlying=underlying, option_type=kind, strike=strike,
              expiry=expiry, qty=qty, avg_entry_price=avg, current_price=None,
              market_value=mv, unrealized_pl=None, multiplier=100)


def test_two_x_premium_buys_back_a_short_put():
    exits = wr.two_x_exits([put(mv=-210.0)])
    assert len(exits) == 1
    order, info = exits[0]
    assert order["position_intent"] == "buy_to_close" and order["order_type"] == "market"
    assert order["qty"] == 1 and order["reason"] == "wheel_btc_2x"
    assert info["loss_ratio"] == 2.1


def test_two_x_ignores_calls_longs_small_losses_and_working_closes():
    assert wr.two_x_exits([put(kind="call", mv=-500.0)]) == []       # fix 6
    assert wr.two_x_exits([put(qty=2, mv=500.0)]) == []
    assert wr.two_x_exits([put(mv=-150.0)]) == []
    working = NS(symbol="Q260612P00100000", side="buy")
    assert wr.two_x_exits([put(mv=-300.0)], open_orders=[working]) == []


def decide(stock, *, expiry="2026-06-12", strike=100.0):
    return wr.put_monitor_decision(contract="Q260612P00100000", underlying="Q",
                                   strike=strike, expiry=expiry, stock_price=stock,
                                   today=TODAY)


def test_monitor_auto_closes_ten_percent_itm_at_any_dte():
    d = decide(88.0)
    assert d["action"] == "auto_close" and d["intent"] == "wheel_btc_itm"
    assert d["priority"] == 2 and round(d["itm_pct"], 1) == 12.0


def test_monitor_auto_closes_five_percent_itm_inside_two_days():
    d = decide(94.0, expiry="2026-06-03")
    assert d["action"] == "auto_close" and d["dte"] == 2


def test_monitor_auto_closes_any_itm_on_expiry_day():
    d = decide(99.5, expiry="2026-06-01")
    assert d["action"] == "auto_close" and d["intent"] == "wheel_btc_expiry"


def test_monitor_alerts_below_the_thresholds_and_informs_on_otm_expiry():
    assert decide(98.0)["action"] == "alert_itm"
    assert decide(98.0)["priority"] == 1
    tomorrow = decide(105.0, expiry="2026-06-02")
    assert tomorrow["action"] == "info_expiring" and "TOMORROW" in tomorrow["title"]
    assert "TODAY" in decide(105.0, expiry="2026-06-01")["title"]
    assert decide(105.0)["action"] is None


def test_monitor_without_a_price_does_nothing_and_says_why():
    d = decide(None, expiry="2026-06-01")
    assert d["action"] is None and d["reason"] == "no underlying price"


# -- ST tests/test_assignment_detection.py, on the detector ------------------

def equity(qty=100, cost=790.0):
    return {"qty": qty, "avg_entry_price": cost}


def test_detection_fires_on_100_shares_with_nothing_covering():
    cands = wr.covered_call_candidates({"LITE": equity()}, [], [])
    assert cands == [{"symbol": "LITE", "qty": 100, "cost_basis": 790.0,
                      "call_strike": 829.5, "n_contracts": 1}]
    title, msg = wr.dry_run_message(cands[0], "2026-06-12")
    assert "dry-run" in title.lower() and "NO order placed" in msg


def test_existing_short_call_order_suppresses_detection():
    open_call = NS(symbol="LITE260626C00830000", side="sell")
    assert wr.covered_call_candidates({"LITE": equity()}, [], [open_call]) == []


def test_under_100_shares_skipped():
    assert wr.covered_call_candidates({"LITE": equity(qty=99)}, [], []) == []


def test_open_swing_position_never_gets_covered_call():
    assert wr.covered_call_candidates({"KR": equity(250, 60.0)}, [], [],
                                      swing_owned={"KR"}) == []


def test_held_short_call_position_suppresses_detection():
    held = put(symbol="LITE260626C00830000", kind="call", underlying="LITE", strike=830.0)
    assert wr.covered_call_candidates({"LITE": equity()}, [held], []) == []


def test_two_hundred_shares_is_two_contracts_and_the_strike_pick():
    cands = wr.covered_call_candidates({"LITE": equity(qty=200)}, [], [])
    assert cands[0]["n_contracts"] == 2
    calls = [NS(symbol="LITE260626C00820000", strike=820.0, close_price=15.0),
             NS(symbol="LITE260626C00830000", strike=830.0, close_price=12.0),
             NS(symbol="LITE260626C00840000", strike=840.0, close_price=9.0)]
    best, strike, limit = wr.pick_covered_call(calls, 829.5, 790.0)
    assert (best.symbol, strike, limit) == ("LITE260626C00830000", 830.0, 12.0)
    best, strike, limit = wr.pick_covered_call(calls[:1], 829.5, 790.0)
    assert strike == 820.0          # no strike at/above: ST uses the lowest available
    no_close = [NS(symbol="X", strike=900.0, close_price=None)]
    assert wr.pick_covered_call(no_close, 829.5, 790.0)[2] == 7.9
    assert wr.pick_covered_call([], 829.5, 790.0) is None
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest backend/tests/test_swing_wheel_positions.py -q -p no:cacheprovider`
Expected: FAIL — `AttributeError: module 'swing_trader.wheel_rules' has no attribute 'two_x_exits'`.

- [ ] **Step 3: Append the implementation**

Append to `backend/swing_trader/wheel_rules.py`, and extend its constants import to add `AUTO_CLOSE_DTE2_PCT, AUTO_CLOSE_ITM_PCT`:

```python
# -- position rules ----------------------------------------------------------

def btc_order(pos, qty, reason, *, signal_id=None, session=None) -> dict:
    """A buy-to-close market order in the contract §1 shape."""
    return {"signal_id": signal_id, "session": session,
            "underlying": str(pos.underlying).upper(), "contract": pos.symbol,
            "option_type": str(pos.option_type).lower(), "strike": float(pos.strike),
            "expiry": str(pos.expiry)[:10], "position_intent": "buy_to_close",
            "qty": int(qty), "order_type": "market", "limit_price": None,
            "tif": "day", "reason": reason}


def two_x_exits(option_positions, *, open_orders=()):
    """wheel_trader.py:230-321 check_open_wheel_positions: a short PUT (fix 6:
    ST read any negative quantity) worth 2× the premium collected is bought
    back at market — a 100% loss on the premium caps the trade. A contract
    that already has a working buy is skipped."""
    working = {str(getattr(o, "symbol", "")).upper() for o in (open_orders or ())
               if str(getattr(o, "side", "")).lower() == "buy"}
    out = []
    for pos in option_positions or []:
        try:
            symbol = pos.symbol
            qty    = abs(int(float(pos.qty)))

            # Short puts have negative qty in Alpaca
            if float(pos.qty) >= 0:
                continue
            if str(pos.option_type).lower() != "put":
                continue

            # cost_basis is what we collected when selling
            # current market_value (negative for short) is what it costs to close
            cost_basis    = abs(float(pos.avg_entry_price)) * qty * 100
            current_value = abs(float(pos.market_value))

            if cost_basis <= 0:
                continue

            loss_ratio = current_value / cost_basis
            if loss_ratio >= 2.0 and str(symbol).upper() not in working:
                out.append((btc_order(pos, qty, "wheel_btc_2x"),
                            {"cost_basis": cost_basis, "current_value": current_value,
                             "loss_ratio": round(loss_ratio, 4)}))
        except Exception:
            continue
    return out


def put_monitor_decision(*, contract, underlying, strike, expiry, stock_price, today):
    """app.py:1309-1411, the per-position rules of api_check_wheel_positions,
    over Alpaca's contract fields (fix 10). Callers pass short PUTS only (fix 6).

    Tier 2 auto-close rules (no AI — deterministic):
      - ITM ≥ 10% at any DTE         → buy-to-close market order, priority 2
      - ITM ≥ 5% AND DTE ≤ 2         → buy-to-close market order, priority 2
      - DTE = 0 AND any ITM amount   → buy-to-close market order, priority 2
    Alert-only (human decides):
      - ITM < thresholds above       → priority 1, no auto action
      - DTE ≤ 1 AND OTM              → priority 0 informational
    """
    expiry_date = date.fromisoformat(str(expiry)[:10])
    dte         = (expiry_date - today).days

    itm     = stock_price is not None and stock_price < strike
    itm_pct = ((strike - stock_price) / strike * 100) if itm and stock_price else 0.0
    deep_itm = itm_pct >= 3.0

    out = {"contract": contract, "underlying": underlying, "strike": strike,
           "expiry": str(expiry_date), "stock_price": stock_price, "dte": dte,
           "itm": itm, "itm_pct": itm_pct, "deep_itm": deep_itm, "action": None,
           "intent": None, "reason": "", "title": None, "message": None,
           "priority": None}

    should_auto_close = False
    auto_close_reason = ""
    intent = None

    if stock_price is not None and itm:
        if itm_pct >= AUTO_CLOSE_ITM_PCT:
            should_auto_close = True
            auto_close_reason = f"{itm_pct:.1f}% ITM (≥{AUTO_CLOSE_ITM_PCT:.0f}% threshold) — bust regardless of DTE"
            intent = "wheel_btc_itm"
        elif dte <= 2 and itm_pct >= AUTO_CLOSE_DTE2_PCT:
            should_auto_close = True
            auto_close_reason = f"{itm_pct:.1f}% ITM with only {dte} day(s) to expiry — not recovering"
            intent = "wheel_btc_itm"
        elif dte == 0:
            should_auto_close = True
            auto_close_reason = f"Expiry TODAY and ITM ${stock_price:.2f} vs strike ${strike:.2f} — closing before assignment"
            intent = "wheel_btc_expiry"

    if should_auto_close:
        out.update(action="auto_close", intent=intent, reason=auto_close_reason,
                   priority=2, title=f"🚨 Auto-Closed: {underlying}",
                   message=(f"🚨 AUTO-CLOSE ORDERED\n"
                            f"{contract}\n"
                            f"Reason: {auto_close_reason}\n"
                            f"A buy-to-close market order was sent."))
        return out

    if stock_price is None:
        # ST formatted a None price into its alert and crashed out of the
        # position; say so instead of guessing.
        out["reason"] = "no underlying price"
        return out

    if itm:
        # ITM but below auto-close thresholds — monitor, may recover
        out.update(action="alert_itm", priority=1, title=f"⚠️ ITM PUT: {underlying}",
                   message=(f"{contract}\n"
                            f"Strike: ${strike:.2f} | Stock: ${stock_price:.2f}\n"
                            f"{itm_pct:.1f}% ITM | {dte} days to expiry\n"
                            f"Below auto-close threshold — monitor closely."))
    elif dte <= 1 and not itm:
        # Expiring soon but OTM — will expire worthless, informational only
        out.update(action="info_expiring", priority=0,
                   title=f"⏰ EXPIRING {'TODAY' if dte == 0 else 'TOMORROW'} OTM: {underlying}",
                   message=(f"{contract}\n"
                            f"Strike: ${strike:.2f} | Stock: ${stock_price:.2f}\n"
                            f"OTM — on track to expire worthless ✅\n"
                            f"Expiry: {expiry_date}"))
    return out


def covered_call_strike(cost_basis) -> float:
    """wheel_trader.py:432: cost basis × 1.05."""
    return round(float(cost_basis) * 1.05, 2)


def covered_call_candidates(equity_positions, option_positions, open_orders, *,
                            swing_owned=()):
    """wheel_trader.py:348-449 check_assigned_positions, the detection half.

    `equity_positions` is {symbol: {"qty", "avg_entry_price"}} (long stock).
    A name is covered when a held short option sits on it (contract fields,
    fix 10) or a working SELL option order does (its OCC root); ST counted a
    short put there too, and so does this. Open swing inventory is never an
    assignment (ST: _is_swing_position)."""
    covered_symbols = set()
    for o in open_orders or []:
        parts = occ_parts(getattr(o, "symbol", ""))
        if parts and str(getattr(o, "side", "")).lower() == "sell":
            covered_symbols.add(parts[0])
    for p in option_positions or []:
        try:
            if float(p.qty) < 0:
                covered_symbols.add(str(p.underlying).upper())
        except Exception:
            continue
    owned = {str(s).upper() for s in (swing_owned or ())}
    out = []
    for symbol, pos in sorted((equity_positions or {}).items()):
        qty = int(float(pos.get("qty") or 0))
        if qty <= 0:
            continue
        if qty < 100:
            _log(f"  [covered-call] {symbol}: only {qty} shares — need 100 for covered call, skipping")
            continue
        if str(symbol).upper() in owned:
            _log(f"  [covered-call] {symbol}: open swing position — skipping")
            continue
        if str(symbol).upper() in covered_symbols:
            _log(f"  [covered-call] {symbol}: already has open short call — skipping")
            continue
        cost_basis = float(pos.get("avg_entry_price") or 0.0)
        out.append({"symbol": symbol, "qty": qty, "cost_basis": cost_basis,
                    "call_strike": covered_call_strike(cost_basis),
                    "n_contracts": qty // 100})
    return out


def dry_run_message(c, expiry):
    """wheel_trader.py:436-448, the AUTO_COVERED_CALL=False notification."""
    title = f"🔍 Assignment detected: {c['symbol']} (dry-run)"
    msg = (
        f"ASSIGNMENT DETECTED — dry-run, NO order placed\n"
        f"{c['symbol']}: {c['qty']} shares @ cost ${c['cost_basis']:.2f}\n"
        f"Would sell {c['n_contracts']} covered call(s):\n"
        f"Strike ≥ ${c['call_strike']:.2f} (cost basis × 1.05)  exp {expiry}\n"
        f"To enable: set auto_covered_call=true on the wheel lane"
    )
    return title, msg


def pick_covered_call(calls, call_strike, cost_basis):
    """wheel_trader.py:477-495: the closest strike AT OR ABOVE cost × 1.05
    (else the lowest available), priced at its close (else 1% of cost).
    Returns (contract, strike, limit_price) or None."""
    if not calls:
        return None
    eligible = [c for c in calls if float(c.strike) >= call_strike]
    if not eligible:
        eligible = list(calls)
    best = min(eligible, key=lambda c: float(c.strike))
    close_price = float(best.close_price) if getattr(best, "close_price", None) else None
    if close_price and close_price > 0:
        limit_price = round(close_price, 2)
    else:
        limit_price = round(cost_basis * 0.01, 2)
    if limit_price < 0.01:
        return None
    return best, float(best.strike), limit_price
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest backend/tests/test_swing_wheel_positions.py backend/tests/test_swing_wheel_orders.py backend/tests/test_swing_wheel_screen.py -q -p no:cacheprovider`
Expected: PASS (40 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/swing_trader/wheel_rules.py backend/tests/test_swing_wheel_positions.py
git commit -m "feat(swing): port the wheel's 2x buy-back, daily monitor and covered-call rules

The monitor keeps ST's 10%, 5%-within-two-days and expiry-day auto-close
rules and reads contract fields (fix 10) for puts only (fix 6). Assignment
detection ports ST's dry-run with swing inventory excluded.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016ztXjLWXu61Dpp4XMU2jsS"
```

---

### Task 11: `llm_utils.call_llm_with_web_search` (B6)

**Files:**
- Modify: `backend/llm_utils.py` — `_call_claude_cli_plain` (~line 1895) gains `tools`/`max_turns`; new function after `call_gemini_with_grounding` (~line 7072)
- Test: `backend/tests/test_llm_web_search.py`

**Interfaces:**
- Consumes: `call_gemini_with_grounding(api_key, model, prompt, max_output_tokens=1024, timeout_sec=None) -> str` (existing), `_call_claude_cli_plain(...)` (existing).
- Produces: `call_llm_with_web_search(provider, api_key, model, prompt, *, max_output_tokens=300, max_uses=2, timeout_sec=None, provider_config=None) -> str` ("" when the provider cannot search or the call failed; never raises).

**How llm_utils reaches Anthropic today (read before editing).** There is no direct Anthropic-API provider in the models framework: `call_llm_by_provider` (~6760) and `call_structured_llm_by_provider` (~2822) route Claude models through `claude-cli` (the Claude Code CLI, `_call_claude_cli_plain` / `_call_claude_cli_structured_from_strategy`), `bedrock` (Converse) and `openrouter`; an unknown provider string falls through to Gemini. The web search tool therefore extends the **claude-cli** transport, which today passes `--tools ""` (every tool off). Claude Code's `WebSearch` tool runs Anthropic's server-side web search. Per the claude-api skill's platform table, server web search is **not available on Amazon Bedrock**, so `bedrock` skips news like every other provider. For a future direct-API provider the tool shape is `{"type": "web_search_20260209", "name": "web_search", "max_uses": 2}` on Opus 4.6+/Sonnet 4.6+ and `web_search_20250305` on older models and Vertex; nothing in this task sends it. **Flag for verification on the server:** the installed Claude Code CLI accepts `--tools WebSearch --allowedTools WebSearch --max-turns 3` in `-p` mode (the flags this task adds).

- [ ] **Step 1: Impact analysis**

Run `gitnexus_impact({target: "_call_claude_cli_plain", direction: "upstream"})`. Expected direct caller: `call_llm_by_provider` (the claude-cli branch), which every strategy's plain claude-cli call goes through. The new keyword arguments default to today's behaviour, so the argv of every existing call is byte-identical (pinned by `test_a_plain_claude_cli_call_keeps_every_tool_off`). Risk: LOW; report it.

- [ ] **Step 2: Write the failing test**

Create `backend/tests/test_llm_web_search.py`:

```python
"""Web-searching model calls for the swing-trader news line (spec §8)."""
import json
import os
import subprocess
import sys
import types

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

import llm_utils  # noqa: E402


def _capture_cli(monkeypatch, result="Upgraded by two brokers this week.", is_error=False):
    seen = {}
    import chatbot.claude_cli_provider as ccp
    monkeypatch.setattr(ccp, "_resolve_cli_path", lambda path: "/usr/local/bin/claude")

    def fake_run(argv, **kwargs):
        seen["argv"] = list(argv)
        seen["input"] = kwargs.get("input")
        return types.SimpleNamespace(
            stdout=json.dumps({"result": result, "is_error": is_error}),
            stderr="", returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    return seen


def test_gemini_goes_through_search_grounding(monkeypatch):
    seen = {}

    def fake(api_key, model, prompt, max_output_tokens=1024, timeout_sec=None):
        seen.update(api_key=api_key, model=model, prompt=prompt,
                    tokens=max_output_tokens, timeout=timeout_sec)
        return "grounded summary"

    monkeypatch.setattr(llm_utils, "call_gemini_with_grounding", fake)
    out = llm_utils.call_llm_with_web_search(
        "gemini", "k", "gemini-2.5-flash", "news?", max_output_tokens=300, timeout_sec=25)
    assert out == "grounded summary"
    assert seen == {"api_key": "k", "model": "gemini-2.5-flash", "prompt": "news?",
                    "tokens": 300, "timeout": 25}


def test_claude_cli_enables_only_web_search_and_caps_the_turns(monkeypatch):
    seen = _capture_cli(monkeypatch)
    out = llm_utils.call_llm_with_web_search(
        "claude-cli", "", "claude-sonnet-4-6", "news?", max_uses=2, timeout_sec=25)
    assert out == "Upgraded by two brokers this week."
    argv = seen["argv"]
    assert argv[argv.index("--tools") + 1] == "WebSearch"
    assert argv[argv.index("--allowedTools") + 1] == "WebSearch"
    assert argv[argv.index("--max-turns") + 1] == "3"
    assert seen["input"] == "news?"


def test_a_plain_claude_cli_call_keeps_every_tool_off(monkeypatch):
    seen = _capture_cli(monkeypatch, result="ok")
    assert llm_utils.call_llm_by_provider("claude-cli", "", "claude-sonnet-4-6", "hi") == "ok"
    argv = seen["argv"]
    assert argv[argv.index("--tools") + 1] == ""
    assert "--allowedTools" not in argv and "--max-turns" not in argv


def test_a_cli_error_is_an_empty_string(monkeypatch):
    _capture_cli(monkeypatch, result="boom", is_error=True)
    assert llm_utils.call_llm_with_web_search("claude-cli", "", "m", "q") == ""


def test_other_providers_skip_news_with_one_log_line(monkeypatch, capsys):
    monkeypatch.setattr(llm_utils, "_WEB_SEARCH_SKIP_LOGGED", set())
    for provider in ("openai", "openai", "bedrock"):
        assert llm_utils.call_llm_with_web_search(provider, "k", "m", "q") == ""
    err = capsys.readouterr().err
    assert err.count("'openai'") == 1 and err.count("'bedrock'") == 1
```

- [ ] **Step 3: Run it to verify it fails**

Run: `python3 -m pytest backend/tests/test_llm_web_search.py -q -p no:cacheprovider`
Expected: FAIL — `AttributeError: module 'llm_utils' has no attribute 'call_llm_with_web_search'`.

- [ ] **Step 4: Extend the claude-cli transport**

In `backend/llm_utils.py`, change the signature of `_call_claude_cli_plain`:

```python
def _call_claude_cli_plain(
    *,
    model: str,
    prompt: str,
    provider_config: dict[str, Any] | None = None,
    timeout_sec: int | None = None,
    retries: int = 0,
    tools: str = "",
    max_turns: int | None = None,
) -> str:
    """Plain-text claude-cli call. Returns the assistant's reply text, or
    empty string on failure. Mirrors the existing call_llm_by_provider
    contract (best-effort, never raises into the strategy).

    `tools` is the CLI's --tools value: "" (every tool off) for every model
    call in the system except call_llm_with_web_search, which passes
    "WebSearch" and caps the agent loop with `max_turns`. With the default
    arguments the argv is byte-identical to what it always was."""
```

In its argv, replace:

```python
            "--tools", "",
```

with:

```python
            "--tools", tools,
            *(["--allowedTools", tools] if tools else []),
            *(["--max-turns", str(int(max_turns))] if tools and max_turns else []),
```

and guard the prompt-cache store so a web search (whose answer changes daily) is never cached — replace:

```python
        try:
            _effort_key = _cache_effort_key("claude-cli", provider_config)
            _store_prompt_cache(prompt, canonical_model_cache_key(model, provider_config), "", str(result_text))
        except Exception:
            pass
```

with:

```python
        if not tools:
            try:
                _effort_key = _cache_effort_key("claude-cli", provider_config)
                _store_prompt_cache(prompt, canonical_model_cache_key(model, provider_config), "", str(result_text))
            except Exception:
                pass
```

- [ ] **Step 5: Add `call_llm_with_web_search`**

Insert directly after `call_gemini_with_grounding` in `backend/llm_utils.py`:

```python
# ── Web-searching call (swing-trader news line, spec §8) ───────────────────
#
# Providers whose model can search the web server-side. Every other provider
# returns "" and logs ONE line per process: a missing news line never fails a
# scan, and a log line per candidate per day would bury the one that matters.
# Direct Anthropic API (not a provider in the models framework today) would use
# {"type": "web_search_20260209", "name": "web_search", "max_uses": 2} on
# Opus 4.6+/Sonnet 4.6+ (web_search_20250305 on older models and Vertex); the
# tool does not exist on Amazon Bedrock.
_WEB_SEARCH_SKIP_LOGGED: set = set()


def call_llm_with_web_search(
    provider: str,
    api_key: str,
    model: str,
    prompt: str,
    *,
    max_output_tokens: int = 300,
    max_uses: int = 2,
    timeout_sec: int | None = None,
    provider_config: dict[str, Any] | None = None,
) -> str:
    """One model call that may search the web first. Returns the reply text,
    or "" when the provider cannot search or the call failed. Never raises.

    - gemini: Google Search grounding (call_gemini_with_grounding).
    - claude-cli: Anthropic's server-side web search through Claude Code's
      WebSearch tool, the only tool enabled, with the agent loop capped at
      `max_uses` searches plus the answer (ST's max_uses=2).
    """
    p = (provider or "").strip().lower()
    try:
        if p == "gemini":
            return call_gemini_with_grounding(
                api_key, model, prompt, max_output_tokens=max_output_tokens,
                timeout_sec=timeout_sec) or ""
        if p == "claude-cli":
            return _call_claude_cli_plain(
                model=model, prompt=prompt, provider_config=provider_config,
                timeout_sec=timeout_sec, retries=0, tools="WebSearch",
                max_turns=max(1, int(max_uses)) + 1) or ""
    except Exception:
        return ""
    if p not in _WEB_SEARCH_SKIP_LOGGED:
        _WEB_SEARCH_SKIP_LOGGED.add(p)
        import sys
        print(f"[llm_utils] web search is not available for provider {p!r}; "
              "news skipped", file=sys.stderr, flush=True)
    return ""
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python3 -m pytest backend/tests/test_llm_web_search.py -q -p no:cacheprovider`
Expected: PASS (5 tests).

Run the existing claude-cli and provider tests too: `python3 -m pytest backend/tests -q -p no:cacheprovider -k "claude_cli or llm_utils or openrouter"`
Expected: no new failures.

- [ ] **Step 7: Commit**

Run `gitnexus_detect_changes()`; expect `_call_claude_cli_plain` (signature, default-preserving) and the new `call_llm_with_web_search` only.

```bash
git add backend/llm_utils.py backend/tests/test_llm_web_search.py
git commit -m "feat(llm): web-searching model call for the swing news line

Gemini goes through search grounding; claude-cli, the models framework's
Anthropic transport, enables only Claude Code's WebSearch tool with the
loop capped at ST's two searches. Every other provider returns empty with
one log line. Plain claude-cli calls keep every tool off.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016ztXjLWXu61Dpp4XMU2jsS"
```

---

### Task 12: Notification types and the swing senders (B14)

**Files:**
- Modify: `backend/notification_types.py` (`NOTIFICATION_TYPES` ~line 17, `_PUSH_ON_BY_DEFAULT` ~line 158-166)
- Modify: `backend/tests/test_notification_types.py` (the push-default assertion)
- Create: `backend/swing_trader/notify.py`
- Test: `backend/tests/test_swing_notify.py`

**Interfaces:**
- Consumes: `notifications.notify(*, category, instance_id, title, body, discord_channel, discord_embed=None, push_title=None, push_body=None, user_id=None, data=None)` (existing, ~line 189); `notification_types.type_for_key`.
- Produces: the eight keys of spec §10; `swing_trader.notify.PREFIXES`, `send(category, instance_id, title, message, *, priority=0) -> None` (never raises), `notify_wheel_assignment(instance_id, *, symbol, qty, price=None, date=None) -> None`.

- [ ] **Step 1: Impact analysis**

Run `gitnexus_impact({target: "NOTIFICATION_TYPES", direction: "upstream", file_path: "backend/notification_types.py"})` and the same for `default_routing`. Expected consumers: `notifications.resolve_routing`, `interactive_utils` notification-preference actions, the settings UIs via `public_types()`. Adding types appends keys; routing of existing keys is unchanged. Risk: LOW.

- [ ] **Step 2: Write the failing tests**

Create `backend/tests/test_swing_notify.py`:

```python
"""The eight swing/wheel notification types and their sender."""
import os
import sys

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from notification_types import (  # noqa: E402
    NOTIFICATION_TYPE_KEYS, _PUSH_ON_BY_DEFAULT, classify, default_routing,
    groups_in_order, type_for_key,
)
from swing_trader import notify  # noqa: E402

SWING_KEYS = ("swing_entry", "swing_pending_review", "swing_exit",
              "swing_run_summary", "wheel_put_placed", "wheel_pending_review",
              "wheel_position_alert", "wheel_assignment")
PUSH_ON = {"swing_entry", "swing_pending_review", "swing_exit",
           "wheel_put_placed", "wheel_pending_review", "wheel_position_alert"}


def test_the_eight_types_exist_in_their_own_group():
    for key in SWING_KEYS:
        assert key in NOTIFICATION_TYPE_KEYS
        assert type_for_key(key)["group"] == "Swing & Wheel"
    assert "Swing & Wheel" in groups_in_order()


def test_reviews_entries_exits_and_alerts_push_by_default():
    routing = default_routing()
    for key in SWING_KEYS:
        assert routing[key]["discord"] is True
        assert routing[key]["push"] is (key in PUSH_ON)
    assert PUSH_ON <= _PUSH_ON_BY_DEFAULT


def test_each_prefix_classifies_to_its_own_key():
    for key in SWING_KEYS:
        text = f"{notify.PREFIXES[key]} [swing-paper] something"
        assert classify(content=text) == key


def test_send_routes_by_category_with_the_prefix(monkeypatch):
    sent = []
    monkeypatch.setattr(notify, "_sink", lambda **kw: sent.append(kw))
    notify.send("swing_pending_review", "swing-paper", "⚠️ REVIEW: AAPL (score 62/100)",
                "AAPL @ $190.00\nScore: 62/100 — needs your approval", priority=1)
    assert len(sent) == 1
    msg = sent[0]
    assert msg["category"] == "swing_pending_review"
    assert msg["instance_id"] == "swing-paper"
    assert msg["discord_channel"] == "trades"
    assert msg["body"].startswith("SWING REVIEW [swing-paper] ⚠️ REVIEW: AAPL")
    assert msg["push_title"] == "⚠️ REVIEW: AAPL (score 62/100)"


def test_priority_two_is_marked_urgent(monkeypatch):
    sent = []
    monkeypatch.setattr(notify, "_sink", lambda **kw: sent.append(kw))
    notify.send("wheel_position_alert", "swing-paper", "🚨 Auto-Closed: APH", "x", priority=2)
    assert "(URGENT)" in sent[0]["body"] and "(URGENT)" in sent[0]["push_title"]


def test_send_never_raises(monkeypatch):
    def boom(**kw):
        raise RuntimeError("outbox down")

    monkeypatch.setattr(notify, "_sink", boom)
    notify.send("swing_entry", "swing-paper", "BUY AAPL", "x")


def test_wheel_assignment_helper(monkeypatch):
    sent = []
    monkeypatch.setattr(notify, "_sink", lambda **kw: sent.append(kw))
    notify.notify_wheel_assignment("swing-paper", symbol="APH", qty=100, price=130.0,
                                   date="2026-10-02")
    assert sent[0]["category"] == "wheel_assignment"
    assert "APH: assigned 100 shares @ $130.00 on 2026-10-02" in sent[0]["body"]
```

- [ ] **Step 3: Run it to verify it fails**

Run: `python3 -m pytest backend/tests/test_swing_notify.py -q -p no:cacheprovider`
Expected: FAIL — `ImportError: cannot import name 'notify' from 'swing_trader'` (and the keys are missing).

- [ ] **Step 4: Add the types**

In `backend/notification_types.py`, insert directly before `# --- Fallback (hidden default) — never dropped ---`:

```python
    # --- Swing & Wheel (swing-trader port, spec §10) ---
    # Reviews, entries, exits and position alerts push by default: a review
    # that waits is a trade the operator meant to decide on, and a short put
    # going in the money is money at risk. ST sent these as Pushover messages.
    {"key": "swing_entry", "group": "Swing & Wheel", "label": "Swing entry",
     "desc": "The swing lane sent a bracket buy", "channel": "trades",
     "discord": True, "push": True, "prefixes": ["SWING ENTRY ["]},
    {"key": "swing_pending_review", "group": "Swing & Wheel", "label": "Swing review needed",
     "desc": "A swing candidate scored 50-74 and waits for your approval",
     "channel": "trades", "discord": True, "push": True, "prefixes": ["SWING REVIEW ["]},
    {"key": "swing_exit", "group": "Swing & Wheel", "label": "Swing exit",
     "desc": "The swing lane sold a position", "channel": "trades",
     "discord": True, "push": True, "prefixes": ["SWING EXIT ["]},
    {"key": "swing_run_summary", "group": "Swing & Wheel", "label": "Swing & wheel run summary",
     "desc": "A swing or wheel scan finished; AI rejects and bear-mode notes",
     "channel": "notifications", "discord": True, "push": False,
     "prefixes": ["SWING RUN ["]},
    {"key": "wheel_put_placed", "group": "Swing & Wheel", "label": "Wheel put sent",
     "desc": "The wheel lane sent a cash-secured put", "channel": "trades",
     "discord": True, "push": True, "prefixes": ["WHEEL PUT ["]},
    {"key": "wheel_pending_review", "group": "Swing & Wheel", "label": "Wheel review needed",
     "desc": "A wheel candidate scored 50-74 and waits for your approval",
     "channel": "trades", "discord": True, "push": True, "prefixes": ["WHEEL REVIEW ["]},
    {"key": "wheel_position_alert", "group": "Swing & Wheel", "label": "Wheel position alert",
     "desc": "A short put is in the money, near expiry, or is being bought back",
     "channel": "trades", "discord": True, "push": True, "prefixes": ["WHEEL ALERT ["]},
    {"key": "wheel_assignment", "group": "Swing & Wheel", "label": "Wheel assignment",
     "desc": "A put was assigned; includes the covered-call dry run",
     "channel": "trades", "discord": True, "push": False,
     "prefixes": ["WHEEL ASSIGNMENT ["]},

```

Replace:

```python
_PUSH_ON_BY_DEFAULT = {"instance_crash"}
```

with:

```python
_PUSH_ON_BY_DEFAULT = {
    "instance_crash",
    # swing-trader port (spec §10): pending reviews, entries, exits, position alerts
    "swing_entry", "swing_pending_review", "swing_exit",
    "wheel_put_placed", "wheel_pending_review", "wheel_position_alert",
}
```

In `backend/tests/test_notification_types.py`, replace:

```python
    # the only push-on-by-default key today
    assert _PUSH_ON_BY_DEFAULT == {"instance_crash"}
    assert r["instance_crash"]["push"] is True
```

with:

```python
    # instance_crash, plus the swing-trader port's reviews, entries, exits and
    # position alerts (spec §10)
    assert _PUSH_ON_BY_DEFAULT == {
        "instance_crash", "swing_entry", "swing_pending_review", "swing_exit",
        "wheel_put_placed", "wheel_pending_review", "wheel_position_alert"}
    assert r["instance_crash"]["push"] is True
```

- [ ] **Step 5: Write the sender**

Create `backend/swing_trader/notify.py`:

```python
"""Swing & wheel notifications (replaces ST notify.py's Pushover sender).

ST sent Pushover messages at priority 0, 1 or 2. IntelliStock routes by
category (backend/notification_types.py) to Discord and iOS push (spec §9
item 13), so the category decides the routing and a priority-2 message is
marked URGENT in its text.
"""
from __future__ import annotations

PREFIXES = {
    "swing_entry": "SWING ENTRY",
    "swing_pending_review": "SWING REVIEW",
    "swing_exit": "SWING EXIT",
    "swing_run_summary": "SWING RUN",
    "wheel_put_placed": "WHEEL PUT",
    "wheel_pending_review": "WHEEL REVIEW",
    "wheel_position_alert": "WHEEL ALERT",
    "wheel_assignment": "WHEEL ASSIGNMENT",
    "strategy_error": "STRATEGY ERROR",
}

try:
    from intellistock_logger import intellistock_logger as _ilog  # type: ignore

    def _log(msg, color="white"):
        _ilog.log(str(msg), color, service="SwingNotify")
except Exception:  # pragma: no cover - standalone/test import
    def _log(msg, color="white"):
        print(f"[SwingNotify] {msg}")


def _sink(**kwargs):
    from notifications import notify as _notify
    _notify(**kwargs)


def send(category, instance_id, title, message, *, priority=0) -> None:
    """Enqueue one notification. Never raises: a notification failure must
    never cost a scan its orders."""
    try:
        from notification_types import type_for_key
        meta = type_for_key(category) or {}
        prefix = PREFIXES.get(category, str(category).upper())
        urgent = " (URGENT)" if int(priority or 0) >= 2 else ""
        body = f"{prefix} [{instance_id}] {title}{urgent}\n{message}"
        _sink(category=category, instance_id=str(instance_id), title=str(title),
              body=body, discord_channel=meta.get("channel") or "notifications",
              push_title=f"{title}{urgent}"[:120], push_body=str(message)[:220])
    except Exception as exc:
        _log(f"notify failed [{category}]: {type(exc).__name__}: {exc}", "yellow")


def notify_wheel_assignment(instance_id, *, symbol, qty, price=None, date=None) -> None:
    """Plan A-live's activities poller calls this on an OPASN activity."""
    detail = f"{symbol}: assigned {float(qty):g} shares"
    if price:
        detail += f" @ ${float(price):.2f}"
    if date:
        detail += f" on {date}"
    send("wheel_assignment", instance_id, f"Wheel assignment: {symbol}", detail, priority=1)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python3 -m pytest backend/tests/test_swing_notify.py backend/tests/test_notification_types.py backend/tests/test_notification_prefs_store.py backend/tests/test_notification_types_kalshi_runtime.py -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 7: Commit**

Run `gitnexus_detect_changes()`; expect the two literals in `notification_types.py` and new files.

```bash
git add backend/notification_types.py backend/tests/test_notification_types.py backend/swing_trader/notify.py backend/tests/test_swing_notify.py
git commit -m "feat(notify): swing and wheel notification types

Eight categories in a Swing & Wheel group. Pending reviews, entries, exits
and position alerts push by default; run summaries and assignments go to
Discord. The sender replaces ST's Pushover calls and never raises.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016ztXjLWXu61Dpp4XMU2jsS"
```

---

### Task 13: AI analyst over the linked model (B7)

**Files:**
- Create: `backend/tests/fixtures/swing_trader_st/ai_analyst_pure.py`
- Create: `backend/swing_trader/ai_analyst.py`
- Test: `backend/tests/test_swing_ai_analyst.py`

**Interfaces:**
- Consumes: `swing_trader.market_data.get_daily_bars` (Task 7), `swing_trader.indicators.rsi_last` (Task 3), `llm_utils.call_structured_llm_by_provider(provider, api_key, model, prompt, output_type, *, max_output_tokens, timeout_sec, retries, provider_config) -> pydantic object | None` (existing, ~2822), `llm_utils.call_llm_with_web_search` (Task 11).
- Produces:
  - `SwingConviction`, `WheelConviction` (pydantic models: `conviction_score`, `recommendation`, `reasoning`, `position_size_adjustment` / `position_size_contracts`, `key_risks`).
  - `llm_role_from_config(cfg, prefix="conviction_") -> dict | None` (`{"provider", "model", "api_key", "provider_config"}`; None when nothing usable is linked).
  - `resolve_sector_etf(symbol, sector_of) -> str | None`, `days_until_earnings(symbol) -> int | None`, `sector_etf_rsi(symbol, *, sector_of, client) -> float | None`.
  - `SWING_NEWS_PROMPT`, `WHEEL_NEWS_PROMPT`, `fetch_news_summary(symbol, role, *, prompt_template=SWING_NEWS_PROMPT, web_search=None) -> str` (never raises).
  - `build_swing_prompt(**fields) -> str`, `build_wheel_prompt(candidate, news, *, rsi_min=30, rsi_max=60, days_to_expiry=7) -> str` (ST's prompts, byte for byte).
  - `apply_thresholds(result, approve_threshold, review_threshold) -> dict` (raises `ValueError` outside 0–100), `handle_result(result) -> None` (raises `ValueError` for a REVIEW with bad prices).
  - `analyse(signal, *, role, sector_of, bars_client=None, llm=None, earnings_fn=None, news_fn=None, approve_threshold=75, review_threshold=50) -> dict` — raises on any failure (fix 5 is the caller's skip).
  - `score_candidate(candidate, *, role, llm=None, news_fn=None, approve_threshold=75, review_threshold=50, rsi_min=30, rsi_max=60, days_to_expiry=7) -> dict` — a failure is ST's REJECT with score 0.
  - `NEWS_TIMEOUT_S = 25`, `SCORE_TIMEOUT_S = 25`.

- [ ] **Step 1: Vendor the ST ai_analyst oracle**

Create `backend/tests/fixtures/swing_trader_st/ai_analyst_pure.py`:

```python
"""VENDORED ST CODE — parity oracle for tests only. Never import from production.

Source: github.com/tmasters2876/swing-trader @ c2afa71, ai_analyst.py.
Copied verbatim: lines 48-77, 80-92 (line 89, `from paper_trader import
get_symbol_sector`, removed: the module attribute below stands in), 111-119,
203-338, 343-391 (line 390, `from app import append_to_pending`, removed:
the module attribute below stands in). Module attributes a test drives:
_get_client, days_until_earnings, sector_etf_rsi, fetch_news_summary,
get_symbol_sector, and `pending` (what append_to_pending received).
"""
import json
import re
from datetime import date, datetime, timezone

import numpy as np
import pandas as pd

MODEL            = "claude-sonnet-4-6"
PENDING_FILE     = "pending_trades.json"
RSI_PERIOD       = 14
APPROVE_THRESHOLD = 75
REVIEW_THRESHOLD  = 50

pending = []


def append_to_pending(record):
    pending.append(record)


def get_symbol_sector(symbol):
    return "unknown"


def _get_client():
    raise RuntimeError("a test must inject _get_client")


def days_until_earnings(symbol):
    raise RuntimeError("a test must inject days_until_earnings")


def sector_etf_rsi(symbol):
    raise RuntimeError("a test must inject sector_etf_rsi")


def fetch_news_summary(symbol):
    raise RuntimeError("a test must inject fetch_news_summary")


SECTOR_ETF: dict[str, str] = {
    "XLE":   "XLE",   # Energy (maps to itself)
    "GLD":   "GLD",   # Gold (maps to itself)
    "SPY":   "SPY",   # Broad market (maps to itself)
    "QQQ":   "XLK",   # Nasdaq proxy → tech
    # Defensive universe — used in bear mode (BEAR_REGIME_DAYS reached)
    "XLP":   "XLP",   # Consumer Staples
    "XLU":   "XLU",   # Utilities
    "XLV":   "XLV",   # Health Care
    "SHY":   "SHY",   # Short-term Treasuries
}

# GICS sector (normalized as paper_trader.get_symbol_sector returns:
# lowercase, spaces→underscores) → Select Sector SPDR ETF. This gives sector
# context for ANY symbol in the universe, not just hand-listed ones (R5-01).
_SECTOR_TO_ETF: dict[str, str] = {
    "technology":             "XLK",
    "financial_services":     "XLF",
    "healthcare":             "XLV",
    "consumer_cyclical":      "XLY",
    "consumer_defensive":     "XLP",
    "energy":                 "XLE",
    "industrials":            "XLI",
    "basic_materials":        "XLB",
    "utilities":              "XLU",
    "real_estate":            "XLRE",
    "communication_services": "XLC",
    "broad_market":           "SPY",
    "commodity":              "GLD",
}


def _resolve_sector_etf(symbol: str) -> str | None:
    """Sector ETF for a symbol: explicit override first, else by GICS sector.
    Late import of get_symbol_sector avoids the circular dependency
    (paper_trader imports ai_analyst at module level). Returns None only when
    the sector is unknown/unmapped — caller treats that as 'unavailable'."""
    explicit = SECTOR_ETF.get(symbol.upper())
    if explicit:
        return explicit
    try:
        return _SECTOR_TO_ETF.get(get_symbol_sector(symbol))
    except Exception:
        return None


def _rsi(series: pd.Series, period: int = RSI_PERIOD) -> float | None:
    if len(series) < period + 2:
        return None
    d = series.diff()
    g = d.clip(lower=0).ewm(com=period - 1, min_periods=period).mean()
    l = (-d).clip(lower=0).ewm(com=period - 1, min_periods=period).mean()
    rs = g / l.replace(0, np.nan)
    val = (100.0 - 100.0 / (1.0 + rs)).iloc[-1]
    return float(val) if not np.isnan(val) else None


def analyse(signal: dict) -> dict:
    """
    Run the full AI analysis pipeline for a trade signal.

    Required signal keys:
      symbol, rsi, rsi_prev, macd_hist, macd_hist_prev,
      entry_price, shares, stop_price, target_price

    Returns a result dict and handles file I/O (pending_trades.json).
    """
    symbol         = str(signal["symbol"]).upper()
    rsi            = float(signal["rsi"])
    rsi_prev       = float(signal["rsi_prev"])
    macd_hist      = float(signal["macd_hist"])
    macd_hist_prev = float(signal["macd_hist_prev"])
    entry_price    = float(signal["entry_price"])
    shares         = int(signal["shares"])
    stop_price     = float(signal["stop_price"])
    target_price   = float(signal["target_price"])

    risk_pct   = (entry_price - stop_price) / entry_price * 100
    reward_pct = (target_price - entry_price) / entry_price * 100
    rr_ratio   = reward_pct / risk_pct if risk_pct else 0.0

    # ── Enrichment (runs in serial to avoid rate-limiting) ────────────────
    print(f"  [AI] Fetching earnings proximity for {symbol}…")
    earnings_days = days_until_earnings(symbol)

    print(f"  [AI] Fetching sector ETF RSI for {symbol}…")
    etf_sym  = _resolve_sector_etf(symbol) or "unknown"
    etf_rsi  = sector_etf_rsi(symbol)

    print(f"  [AI] Fetching news for {symbol}…")
    news = fetch_news_summary(symbol)

    # ── Build Claude prompt ───────────────────────────────────────────────
    prompt = f"""You are an AI risk analyst for a swing trading system.
Evaluate this proposed trade and return ONLY a valid JSON object — no text outside it.

TRADE SIGNAL
  Symbol:          {symbol}
  Entry price:     ${entry_price:.2f}
  Shares:          {shares}
  Stop loss:       ${stop_price:.2f}  (risk: −{risk_pct:.1f}%)
  Profit target:   ${target_price:.2f}  (reward: +{reward_pct:.1f}%)
  Risk/reward:     1:{rr_ratio:.2f}

TECHNICAL INDICATORS
  RSI(14):         {rsi:.1f}  (prev: {rsi_prev:.1f}) — {'rising ✓' if rsi > rsi_prev else 'falling ✗'}
  MACD histogram:  {macd_hist:.4f}  (prev: {macd_hist_prev:.4f}) — {'improving ✓' if macd_hist > macd_hist_prev else 'weakening ✗'}

CONTEXT
  Sector ETF:      {etf_sym}  |  Sector RSI(14): {f'{etf_rsi:.1f}' if etf_rsi is not None else 'unavailable'}
  Days to earnings:{f' {earnings_days}' if earnings_days is not None else ' unknown'}
  Recent news:     {news}

UPSTREAM FILTERS ALREADY PASSED
  RSI < 50 and rising (pullback in uptrend), MACD histogram improving, price > SMA(200),
  volume > 20-day avg, SPY regime (>SMA200×1.03), VIX ≤ 25.

SCORING GUIDE
  75–100 → approve   (strong setup, proceed)
  50–74  → review    (flag for human review)
  0–49   → reject    (too risky)

RISK FACTORS THAT SHOULD LOWER SCORE
  • Earnings within 5 days (gap risk)
  • Sector ETF RSI > 65 (overbought sector) or < 35 (sector breakdown) — ONLY when a value is shown above; if sector RSI is unavailable, do NOT treat its absence as a risk
  • Negative news catalyst (downgrade, miss, scandal)

key_risks RULES (critical):
  • Every entry MUST be specific to THIS trade and MUST inform the approve/reject decision.
  • Do NOT list structural constants that are identical on every trade — the
    risk/reward is always 1:1.50 and the stop/target are always −6%/+9% by
    system design, so NEVER cite the R/R ratio or the fixed bracket as a risk.
  • Do NOT cite "sector data unavailable" as a risk — it is a data-coverage note, not a trade weakness.
  • If there are no genuine trade-specific risks, return an empty list rather than filler.

Return exactly this JSON structure:
{{
  "conviction_score": <integer 0–100>,
  "recommendation": <"approve" | "review" | "reject">,
  "reasoning": "<2–4 sentences>",
  "position_size_adjustment": <1.0 | 0.5 | 0.25>,
  "key_risks": ["<trade-specific risk>", ...]
}}"""

    # ── Call Claude ───────────────────────────────────────────────────────
    print(f"  [AI] Calling Claude for conviction score…")
    client = _get_client()
    resp = client.messages.create(
        model=MODEL,
        max_tokens=600,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = resp.content[0].text.strip()

    # Strip markdown fences if present
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    json_str = fence_match.group(1) if fence_match else raw

    result = json.loads(json_str)

    # Enforce thresholds regardless of what Claude said in the text
    score = int(result.get("conviction_score", 0))
    if score >= APPROVE_THRESHOLD:
        result["recommendation"] = "approve"
    elif score >= REVIEW_THRESHOLD:
        result["recommendation"] = "review"
    else:
        result["recommendation"] = "reject"

    # Validate position_size_adjustment
    valid_adj = {1.0, 0.5, 0.25}
    adj = float(result.get("position_size_adjustment", 1.0))
    result["position_size_adjustment"] = adj if adj in valid_adj else 1.0

    # Attach enrichment and identity fields
    result.update({
        "symbol":         symbol,
        "entry_price":    entry_price,
        "shares":         shares,
        "stop_price":     stop_price,
        "target_price":   target_price,
        "risk_pct":       round(risk_pct, 2),
        "reward_pct":     round(reward_pct, 2),
        "rr_ratio":       round(rr_ratio, 2),
        "earnings_days":  earnings_days,
        "sector_etf":     etf_sym,
        "sector_rsi":     etf_rsi,
        "news_summary":   news,
        "timestamp":      datetime.now(timezone.utc).isoformat(),
    })

    _handle_result(result)
    return result


def _handle_result(result: dict) -> None:
    sym   = result["symbol"]
    score = result["conviction_score"]
    rec   = result["recommendation"]
    adj   = result["position_size_adjustment"]

    if rec == "approve":
        print(
            f"  [AI] AUTO-APPROVED  {sym}  "
            f"score={score}  size_adj={adj}x\n"
            f"       {result['reasoning'][:100]}"
        )
        return

    if rec == "reject":
        print(
            f"  [AI] REJECTED       {sym}  "
            f"score={score}\n"
            f"       {result['reasoning'][:100]}"
        )
        return

    # review — validate prices before writing to pending_trades.json
    entry  = float(result.get("entry_price", 0))
    stop   = float(result.get("stop_price",  0))
    target = float(result.get("target_price", 0))
    if stop >= entry * 0.99:
        raise ValueError(
            f"Price validation failed for {sym}: "
            f"stop_price ${stop:.4f} must be < entry_price ${entry:.4f} × 0.99 "
            f"(got stop/entry = {stop/entry:.4f})"
        )
    if target <= entry * 1.01:
        raise ValueError(
            f"Price validation failed for {sym}: "
            f"target_price ${target:.4f} must be > entry_price ${entry:.4f} × 1.01 "
            f"(got target/entry = {target/entry:.4f})"
        )

    print(
        f"  [AI] REVIEW NEEDED  {sym}  "
        f"score={score}  size_adj={adj}x  → {PENDING_FILE}"
    )
    # Late import avoids circular: app → paper_trader → ai_analyst.
    # By call time app is fully initialised. append_to_pending holds
    # _pending_lock for the full read-append-write so concurrent
    # approve/reject routes cannot race with this write.
    append_to_pending(result)
```

- [ ] **Step 2: Write the failing test**

Create `backend/tests/test_swing_ai_analyst.py`:

```python
"""AI analyst parity with ST (prompts byte for byte, result post-processing),
ST's own prompt and sector tests, and the failure modes of fix 5."""
import importlib.util
import inspect
import json
import os
import sys
import types

import pandas as pd
import pytest

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from swing_trader import ai_analyst as aa  # noqa: E402

_ST_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "fixtures", "swing_trader_st")
ROLE = {"provider": "claude-cli", "model": "claude-sonnet-4-6", "api_key": "",
        "provider_config": {"cli_path": "claude"}}


def _st(name):
    spec = importlib.util.spec_from_file_location(
        f"_swing_st_{name}", os.path.join(_ST_DIR, f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Anthropic:
    """ST's client.messages.create, answering with fixed text."""

    def __init__(self, text):
        self.text = text
        self.prompts = []

    @property
    def messages(self):
        return self

    def create(self, **kw):
        self.prompts.append(kw["messages"][0]["content"])
        return types.SimpleNamespace(content=[types.SimpleNamespace(text=self.text)])


def _llm(payload, seen):
    def call(provider, api_key, model, prompt, output_type, **kw):
        seen.append({"provider": provider, "model": model, "prompt": prompt,
                     "output_type": output_type, **kw})
        return None if payload is None else output_type(**payload)
    return call


SIGNAL = {"symbol": "nflx", "rsi": 38.2, "rsi_prev": 35.7, "macd_hist": 0.142,
          "macd_hist_prev": 0.089, "entry_price": 950.0, "shares": 13,
          "stop_price": 893.0, "target_price": 1035.5}
PAYLOAD = {"conviction_score": 80, "recommendation": "review",
           "reasoning": "Clean pullback in an uptrend.",
           "position_size_adjustment": 0.5, "key_risks": ["Earnings in 4 days"]}


def _st_analyse(payload):
    st = _st("ai_analyst_pure")
    client = _Anthropic(json.dumps(payload))
    st._get_client = lambda: client
    st.days_until_earnings = lambda s: 4
    st.get_symbol_sector = lambda s: "communication_services"
    st.sector_etf_rsi = lambda s: 55.5
    st.fetch_news_summary = lambda s: "NEWS"
    return st, client, st.analyse(dict(SIGNAL))


def _our_analyse(payload, monkeypatch, **kw):
    seen = []
    monkeypatch.setattr(aa, "sector_etf_rsi", lambda s, **k: 55.5)
    result = aa.analyse(dict(SIGNAL), role=ROLE,
                        sector_of=lambda s: "communication_services",
                        earnings_fn=lambda s: 4, news_fn=lambda s, r: "NEWS",
                        llm=_llm(payload, seen), **kw)
    return seen, result


COMPARED = ("conviction_score", "recommendation", "reasoning",
            "position_size_adjustment", "key_risks", "symbol", "entry_price",
            "shares", "stop_price", "target_price", "risk_pct", "reward_pct",
            "rr_ratio", "earnings_days", "sector_etf", "sector_rsi", "news_summary")


def test_the_swing_prompt_and_result_match_st(monkeypatch):
    _, client, theirs = _st_analyse(PAYLOAD)
    seen, ours = _our_analyse(PAYLOAD, monkeypatch)
    assert seen[0]["prompt"] == client.prompts[0]
    assert seen[0]["output_type"] is aa.SwingConviction
    assert seen[0]["max_output_tokens"] == 600 and seen[0]["retries"] == 0
    assert seen[0]["timeout_sec"] == aa.SCORE_TIMEOUT_S
    for key in COMPARED:
        assert ours[key] == theirs[key], key
    assert ours["recommendation"] == "approve"     # thresholds re-applied in code


def test_a_review_with_valid_prices_matches_st(monkeypatch):
    payload = dict(PAYLOAD, conviction_score=62, position_size_adjustment=0.3)
    st, _, theirs = _st_analyse(payload)
    _, ours = _our_analyse(payload, monkeypatch)
    assert theirs["recommendation"] == ours["recommendation"] == "review"
    assert ours["position_size_adjustment"] == theirs["position_size_adjustment"] == 1.0
    assert st.pending and st.pending[0]["symbol"] == "NFLX"


def test_a_review_with_bad_prices_raises_like_st(monkeypatch):
    payload = dict(PAYLOAD, conviction_score=60)
    bad = dict(SIGNAL, stop_price=949.0)
    st = _st("ai_analyst_pure")
    st._get_client = lambda: _Anthropic(json.dumps(payload))
    st.days_until_earnings = lambda s: None
    st.sector_etf_rsi = lambda s: None
    st.fetch_news_summary = lambda s: "NEWS"
    with pytest.raises(ValueError, match="Price validation failed"):
        st.analyse(dict(bad))
    monkeypatch.setattr(aa, "sector_etf_rsi", lambda s, **k: None)
    with pytest.raises(ValueError, match="Price validation failed"):
        aa.analyse(dict(bad), role=ROLE, sector_of=lambda s: "unknown",
                   earnings_fn=lambda s: None, news_fn=lambda s, r: "NEWS",
                   llm=_llm(payload, []))


def test_malformed_json_raises(monkeypatch):
    monkeypatch.setattr(aa, "sector_etf_rsi", lambda s, **k: None)
    with pytest.raises(ValueError, match="no valid JSON"):
        aa.analyse(dict(SIGNAL), role=ROLE, sector_of=lambda s: "unknown",
                   earnings_fn=lambda s: None, news_fn=lambda s, r: "NEWS",
                   llm=_llm(None, []))


def test_out_of_range_score_raises(monkeypatch):
    monkeypatch.setattr(aa, "sector_etf_rsi", lambda s, **k: None)
    for score in (150, -5):
        with pytest.raises(ValueError, match="outside 0-100"):
            aa.analyse(dict(SIGNAL), role=ROLE, sector_of=lambda s: "unknown",
                       earnings_fn=lambda s: None, news_fn=lambda s, r: "NEWS",
                       llm=_llm(dict(PAYLOAD, conviction_score=score), []))


def test_no_role_raises():
    with pytest.raises(RuntimeError, match="no conviction model"):
        aa._score("prompt", aa.SwingConviction, None, None)


def test_config_thresholds_are_reapplied(monkeypatch):
    _, ours = _our_analyse(dict(PAYLOAD, conviction_score=78), monkeypatch,
                           approve_threshold=80, review_threshold=50)
    assert ours["recommendation"] == "review"


# -- the wheel scorer (wheel_trader.py:958-1072) ------------------------------

CAND = {"symbol": "APH", "stock_price": 131.2, "strike_price": 127.8, "otm_pct": 2.59,
        "expiry": "2026-06-12", "est_premium": 1.7, "est_premium_pct": 1.296,
        "rsi": 48.3, "sma50": 125.0, "atr": 6.8, "atr_pct": 5.18, "earnings_days": None}
WHEEL_PAYLOAD = {"conviction_score": 81, "recommendation": "reject",
                 "reasoning": "Good premium.", "position_size_contracts": 5,
                 "key_risks": ["Sector weak"]}


def test_the_wheel_prompt_and_result_match_st():
    st = _st("wheel_trader_pure")
    client = _Anthropic(json.dumps(WHEEL_PAYLOAD))
    st._get_client = lambda: client
    st._fetch_news = lambda s: "NEWS"
    theirs = st.score_candidate(dict(CAND))
    seen = []
    ours = aa.score_candidate(dict(CAND), role=ROLE, news_fn=lambda s, r: "NEWS",
                              llm=_llm(WHEEL_PAYLOAD, seen))
    assert seen[0]["prompt"] == client.prompts[0]
    assert seen[0]["output_type"] is aa.WheelConviction
    for key in ("conviction_score", "recommendation", "reasoning",
                "position_size_contracts", "key_risks", "strike_price", "expiry"):
        assert ours[key] == theirs[key], key
    assert ours["recommendation"] == "approve" and ours["position_size_contracts"] == 3


def test_wheel_scoring_failure_is_a_reject_not_a_crash():
    for payload in (None, dict(WHEEL_PAYLOAD, conviction_score=250)):
        out = aa.score_candidate(dict(CAND), role=ROLE, news_fn=lambda s, r: "NEWS",
                                 llm=_llm(payload, []))
        assert out["recommendation"] == "reject" and out["conviction_score"] == 0
        assert out["key_risks"] == ["AI scoring error"]
        assert out["reasoning"].startswith("AI scoring failed:")
    out = aa.score_candidate(dict(CAND), role=None, news_fn=lambda s, r: "NEWS")
    assert out["recommendation"] == "reject"


# -- ST tests/test_ai_analyst_prompt.py, on the prompt builder ---------------

SRC = inspect.getsource(aa.build_swing_prompt)


def test_rr_ratio_not_listed_as_risk_factor():
    assert "Risk/reward ratio < 1.5:1" not in SRC


def test_prompt_forbids_structural_constants_in_key_risks():
    assert "NEVER cite the R/R ratio" in SRC
    assert "do NOT treat its absence as a risk" in SRC


def test_prompt_requires_trade_specific_risks():
    assert "specific to THIS trade" in SRC


# -- ST tests/test_ai_analyst_sector.py, with the sector lookup injected -------

def test_sector_etf_resolution():
    assert aa.resolve_sector_etf("APD", lambda s: "basic_materials") == "XLB"
    assert aa.resolve_sector_etf("AVGO", lambda s: "technology") == "XLK"
    assert aa.resolve_sector_etf("PLD", lambda s: "real_estate") == "XLRE"
    assert aa.resolve_sector_etf("ZZZZ", lambda s: "unknown") is None

    def must_not_call(symbol):
        raise AssertionError("should not be called")

    assert aa.resolve_sector_etf("SPY", must_not_call) == "SPY"

    def boom(symbol):
        raise Exception("down")

    assert aa.resolve_sector_etf("APD", boom) is None


def _oscillating_df(n=40):
    closes = [100.0]
    for i in range(1, n):
        closes.append(closes[-1] + (1.5 if i % 2 else -1.0))
    return pd.DataFrame({"Close": closes})


def test_sector_etf_rsi(monkeypatch):
    calls = []
    monkeypatch.setattr(aa.market_data, "get_daily_bars",
                        lambda syms, days, client: calls.append((syms, days)) or {"XLB": _oscillating_df()})
    val = aa.sector_etf_rsi("CF", sector_of=lambda s: "basic_materials", client=object())
    assert isinstance(val, float) and calls == [(["XLB"], 90)]
    calls.clear()
    assert aa.sector_etf_rsi("ZZZZ", sector_of=lambda s: "unknown", client=object()) is None
    assert calls == []
    monkeypatch.setattr(aa.market_data, "get_daily_bars", lambda syms, days, client: {})
    assert aa.sector_etf_rsi("AVGO", sector_of=lambda s: "technology", client=object()) is None


# -- the models framework ----------------------------------------------------

def test_llm_role_from_config():
    cfg = {"conviction_llm_provider": "claude-cli", "conviction_llm_model": "claude-sonnet-4-6",
           "conviction_llm_api_key": "", "conviction_cli_path": "claude",
           "conviction_llm_reasoning_effort": "high", "conviction_llm_model_id": "7"}
    role = aa.llm_role_from_config(cfg)
    assert role["provider"] == "claude-cli" and role["model"] == "claude-sonnet-4-6"
    assert role["provider_config"]["cli_path"] == "claude"
    assert role["provider_config"]["reasoning_effort"] == "high"
    assert "llm_model_id" not in role["provider_config"]
    assert aa.llm_role_from_config({"conviction_llm_provider": "openai",
                                    "conviction_llm_model": "gpt-5"}) is None
    assert aa.llm_role_from_config({"conviction_llm_model_id": ""}) is None


def test_news_never_raises_and_uses_sts_prompt():
    seen = []

    def search(provider, api_key, model, prompt, **kw):
        seen.append((provider, prompt, kw["max_uses"], kw["max_output_tokens"]))
        return "  Upgraded twice.  "

    assert aa.fetch_news_summary("AAPL", ROLE, web_search=search) == "Upgraded twice."
    assert seen[0][1].startswith("Search for 'AAPL stock news this week'")
    assert seen[0][2:] == (2, 300)
    assert aa.fetch_news_summary("AAPL", ROLE, web_search=lambda *a, **k: "") == \
        "No news summary available."

    def boom(*a, **k):
        raise RuntimeError("timeout")

    assert aa.fetch_news_summary("AAPL", ROLE, web_search=boom) == "News fetch failed: timeout"
    assert aa.fetch_news_summary("AAPL", None).startswith("News unavailable")
```

- [ ] **Step 3: Run it to verify it fails**

Run: `python3 -m pytest backend/tests/test_swing_ai_analyst.py -q -p no:cacheprovider`
Expected: FAIL — `ImportError: cannot import name 'ai_analyst' from 'swing_trader'`.

- [ ] **Step 4: Write the implementation**

Create `backend/swing_trader/ai_analyst.py`:

```python
"""AI conviction layer, ported from ST ai_analyst.py and wheel_trader.py.

    ai_analyst.py:48-92     SECTOR_ETF, _SECTOR_TO_ETF, _resolve_sector_etf
                            (the sector lookup is injected)
    ai_analyst.py:122-158   days_until_earnings (verbatim), sector_etf_rsi
    ai_analyst.py:163-198   fetch_news_summary -> llm_utils.call_llm_with_web_search
    ai_analyst.py:203-338   analyse: the prompt is ST's byte for byte; the call
                            goes through llm_utils.call_structured_llm_by_provider
                            with a JSON schema instead of the Anthropic SDK
    ai_analyst.py:343-384   _handle_result's checks; the pending_trades.json
                            write became a SwingSignals row (the caller's job)
    wheel_trader.py:958-1096 score_candidate and _fetch_news

The model is the one the operator links (`conviction_llm_model_id`, resolved
by model_resolver into `conviction_llm_provider/model/api_key`; spec §8).
Thresholds are re-applied in code whatever the model says. One deviation: a
score outside 0-100 is treated as a failed call, never as a verdict.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import pandas as pd
import yfinance as yf
from pydantic import BaseModel, Field

from swing_trader import market_data
from swing_trader.constants import (
    AI_APPROVE_THRESHOLD,
    AI_REVIEW_THRESHOLD,
    DAYS_TO_EXPIRY,
    RSI_MAX,
    RSI_MIN,
    WHEEL_APPROVE_THRESHOLD,
    WHEEL_REVIEW_THRESHOLD,
)
from swing_trader.indicators import rsi_last

try:
    from intellistock_logger import intellistock_logger as _ilog  # type: ignore

    def _log(msg, color="white"):
        _ilog.log(str(msg), color, service="SwingAI")
except Exception:  # pragma: no cover - standalone/test import
    def _log(msg, color="white"):
        print(f"[SwingAI] {msg}")

#: Hard bounds on each model call, so one slow provider costs a candidate and
#: never the broker's per-tick watchdog (clock.CANDIDATE_RESERVE_S).
NEWS_TIMEOUT_S = 25
SCORE_TIMEOUT_S = 25

#: Providers the models framework runs without an API key.
_KEYLESS_PROVIDERS = {"claude-cli", "codex-cli", "ollama"}

# Explicit per-symbol overrides — ETFs/commodities that map to themselves and a
# few majors. Everything NOT here resolves dynamically by GICS sector (R5-01),
# which covers the full S&P 500 instead of this hand-maintained list (was 2.4%).
SECTOR_ETF: dict[str, str] = {
    "XLE":   "XLE",   # Energy (maps to itself)
    "GLD":   "GLD",   # Gold (maps to itself)
    "SPY":   "SPY",   # Broad market (maps to itself)
    "QQQ":   "XLK",   # Nasdaq proxy → tech
    # Defensive universe — used in bear mode (BEAR_REGIME_DAYS reached)
    "XLP":   "XLP",   # Consumer Staples
    "XLU":   "XLU",   # Utilities
    "XLV":   "XLV",   # Health Care
    "SHY":   "SHY",   # Short-term Treasuries
}

# GICS sector (normalized as paper_trader.get_symbol_sector returns:
# lowercase, spaces→underscores) → Select Sector SPDR ETF. This gives sector
# context for ANY symbol in the universe, not just hand-listed ones (R5-01).
_SECTOR_TO_ETF: dict[str, str] = {
    "technology":             "XLK",
    "financial_services":     "XLF",
    "healthcare":             "XLV",
    "consumer_cyclical":      "XLY",
    "consumer_defensive":     "XLP",
    "energy":                 "XLE",
    "industrials":            "XLI",
    "basic_materials":        "XLB",
    "utilities":              "XLU",
    "real_estate":            "XLRE",
    "communication_services": "XLC",
    "broad_market":           "SPY",
    "commodity":              "GLD",
}

SWING_NEWS_PROMPT = (
    "Search for '{symbol} stock news this week' and write "
    "1–2 sentences summarising recent news sentiment. "
    "Prioritise: analyst upgrades/downgrades, earnings beats/misses, "
    "guidance changes, or major corporate events."
)
WHEEL_NEWS_PROMPT = (
    "Search for '{symbol} stock news this week' and write "
    "1–2 sentences summarising recent news sentiment. "
    "Prioritise: analyst upgrades/downgrades, earnings, guidance changes."
)


class SwingConviction(BaseModel):
    """ST's swing JSON (ai_analyst.py:281-288)."""
    conviction_score: float
    recommendation: str = ""
    reasoning: str = ""
    position_size_adjustment: float = 1.0
    key_risks: list[str] = Field(default_factory=list)


class WheelConviction(BaseModel):
    """ST's wheel JSON (wheel_trader.py:1027-1034)."""
    conviction_score: float
    recommendation: str = ""
    reasoning: str = ""
    position_size_contracts: int = 1
    key_risks: list[str] = Field(default_factory=list)


def llm_role_from_config(cfg: dict, prefix: str = "conviction_"):
    """The linked model, read off an already-resolved config the way
    self_learning/roles.py:role_config does. None when nothing usable is
    linked: a missing model must never fall back to an ambient default."""
    cfg = cfg or {}
    provider = str(cfg.get(f"{prefix}llm_provider") or "").strip().lower()
    model = str(cfg.get(f"{prefix}llm_model") or "").strip()
    api_key = str(cfg.get(f"{prefix}llm_api_key") or "")
    if not provider or not model:
        return None
    if not api_key and provider not in _KEYLESS_PROVIDERS:
        return None
    provider_config = {
        key[len(prefix):]: value for key, value in cfg.items()
        if isinstance(key, str) and key.startswith(prefix)
        and not key.endswith(("llm_api_key", "llm_model_id", "llm_provider", "llm_model"))
    }
    effort = provider_config.pop("llm_reasoning_effort", None)
    if effort:
        provider_config["reasoning_effort"] = effort
    return {"provider": provider, "model": model, "api_key": api_key,
            "provider_config": provider_config}


def resolve_sector_etf(symbol: str, sector_of):
    """Sector ETF for a symbol: explicit override first, else by GICS sector.
    Returns None only when the sector is unknown/unmapped — caller treats that
    as 'unavailable'."""
    explicit = SECTOR_ETF.get(symbol.upper())
    if explicit:
        return explicit
    try:
        return _SECTOR_TO_ETF.get(sector_of(symbol))
    except Exception:
        return None


def days_until_earnings(symbol: str) -> int | None:
    """Calendar days until the next confirmed earnings date, or None."""
    try:
        cal = yf.Ticker(symbol).calendar
        if not cal:
            return None
        raw = cal.get("Earnings Date")
        if raw is None:
            return None
        # raw may be a list of Timestamps or a single Timestamp
        items = raw if hasattr(raw, "__iter__") and not isinstance(raw, str) else [raw]
        today = date.today()
        future = sorted(
            pd.Timestamp(d).date() for d in items
            if pd.Timestamp(d).date() >= today
        )
        return (future[0] - today).days if future else None
    except Exception:
        return None


def sector_etf_rsi(symbol: str, *, sector_of, client):
    """RSI(14) of the sector ETF for this symbol (resolved by GICS sector)."""
    etf = resolve_sector_etf(symbol, sector_of)
    if not etf:
        return None
    try:
        # Alpaca bars via market_data (R8-05); 90 calendar days ≈ the old
        # yfinance period="60d" (60 trading days) — EWM RSI is warmup-sensitive.
        df = market_data.get_daily_bars([etf], days=90, client=client).get(etf)
        if df is None or df.empty:
            return None
        close = df["Close"].squeeze()
        val = rsi_last(close)
        return round(val, 1) if val is not None else None
    except Exception:
        return None


def fetch_news_summary(symbol: str, role, *, prompt_template: str = SWING_NEWS_PROMPT,
                       web_search=None) -> str:
    """A 1–2 sentence news sentiment summary from a web-searching model call.
    Never raises; a provider without web search yields ST's fallback text."""
    if role is None:
        return "News unavailable: no conviction model linked"
    try:
        if web_search is None:
            from llm_utils import call_llm_with_web_search as web_search
        text = web_search(role["provider"], role["api_key"], role["model"],
                          prompt_template.format(symbol=symbol),
                          max_output_tokens=300, max_uses=2,
                          timeout_sec=NEWS_TIMEOUT_S,
                          provider_config=role.get("provider_config") or None)
        return (text or "").strip() or "No news summary available."
    except Exception as exc:
        return f"News fetch failed: {exc}"


def build_swing_prompt(*, symbol, entry_price, shares, stop_price, target_price,
                       risk_pct, reward_pct, rr_ratio, rsi, rsi_prev, macd_hist,
                       macd_hist_prev, etf_sym, etf_rsi, earnings_days, news) -> str:
    """ai_analyst.py:239-288, byte for byte."""
    prompt = f"""You are an AI risk analyst for a swing trading system.
Evaluate this proposed trade and return ONLY a valid JSON object — no text outside it.

TRADE SIGNAL
  Symbol:          {symbol}
  Entry price:     ${entry_price:.2f}
  Shares:          {shares}
  Stop loss:       ${stop_price:.2f}  (risk: −{risk_pct:.1f}%)
  Profit target:   ${target_price:.2f}  (reward: +{reward_pct:.1f}%)
  Risk/reward:     1:{rr_ratio:.2f}

TECHNICAL INDICATORS
  RSI(14):         {rsi:.1f}  (prev: {rsi_prev:.1f}) — {'rising ✓' if rsi > rsi_prev else 'falling ✗'}
  MACD histogram:  {macd_hist:.4f}  (prev: {macd_hist_prev:.4f}) — {'improving ✓' if macd_hist > macd_hist_prev else 'weakening ✗'}

CONTEXT
  Sector ETF:      {etf_sym}  |  Sector RSI(14): {f'{etf_rsi:.1f}' if etf_rsi is not None else 'unavailable'}
  Days to earnings:{f' {earnings_days}' if earnings_days is not None else ' unknown'}
  Recent news:     {news}

UPSTREAM FILTERS ALREADY PASSED
  RSI < 50 and rising (pullback in uptrend), MACD histogram improving, price > SMA(200),
  volume > 20-day avg, SPY regime (>SMA200×1.03), VIX ≤ 25.

SCORING GUIDE
  75–100 → approve   (strong setup, proceed)
  50–74  → review    (flag for human review)
  0–49   → reject    (too risky)

RISK FACTORS THAT SHOULD LOWER SCORE
  • Earnings within 5 days (gap risk)
  • Sector ETF RSI > 65 (overbought sector) or < 35 (sector breakdown) — ONLY when a value is shown above; if sector RSI is unavailable, do NOT treat its absence as a risk
  • Negative news catalyst (downgrade, miss, scandal)

key_risks RULES (critical):
  • Every entry MUST be specific to THIS trade and MUST inform the approve/reject decision.
  • Do NOT list structural constants that are identical on every trade — the
    risk/reward is always 1:1.50 and the stop/target are always −6%/+9% by
    system design, so NEVER cite the R/R ratio or the fixed bracket as a risk.
  • Do NOT cite "sector data unavailable" as a risk — it is a data-coverage note, not a trade weakness.
  • If there are no genuine trade-specific risks, return an empty list rather than filler.

Return exactly this JSON structure:
{{
  "conviction_score": <integer 0–100>,
  "recommendation": <"approve" | "review" | "reject">,
  "reasoning": "<2–4 sentences>",
  "position_size_adjustment": <1.0 | 0.5 | 0.25>,
  "key_risks": ["<trade-specific risk>", ...]
}}"""
    return prompt


def build_wheel_prompt(candidate: dict, news: str, *, rsi_min=RSI_MIN, rsi_max=RSI_MAX,
                       days_to_expiry=DAYS_TO_EXPIRY) -> str:
    """wheel_trader.py:965-1034, byte for byte (the screening range and the
    expiry horizon follow the lane's config; ST's constants are the defaults)."""
    RSI_MIN, RSI_MAX, DAYS_TO_EXPIRY = rsi_min, rsi_max, days_to_expiry  # noqa: N806
    symbol      = candidate["symbol"]
    stock_price = candidate["stock_price"]
    strike      = candidate["strike_price"]
    otm_pct     = candidate["otm_pct"]
    expiry      = candidate["expiry"]
    est_premium = candidate["est_premium"]
    rsi         = candidate["rsi"]
    atr_pct     = candidate["atr_pct"]
    earnings    = candidate["earnings_days"]

    prompt = f"""You are an options income analyst evaluating a cash-secured put opportunity.
Score this trade 0–100 and return ONLY valid JSON — no text outside it.

PROPOSED TRADE
  Strategy:        Sell cash-secured put (Wheel income)
  Symbol:          {symbol}
  Stock price:     ${stock_price:.2f}
  Put strike:      ${strike:.2f}  ({otm_pct:.1f}% OTM)
  Expiry:          {expiry}  (weekly, ~{DAYS_TO_EXPIRY} days)
  Est. premium:    ${est_premium:.2f}/share  (~${est_premium*100:.0f}/contract)
  Est. premium%:   {candidate['est_premium_pct']:.3f}% of stock price

TECHNICALS
  RSI(14):         {rsi:.1f}  (screening range: {RSI_MIN}–{RSI_MAX})
  ATR%:            {atr_pct:.2f}%  (weekly volatility proxy)
  Above SMA(50):   Yes ✓

CONTEXT
  Days to earnings:{f' {earnings}' if earnings is not None else ' unknown'}
  Recent news:     {news}

MARKET CONTEXT — apply penalties as directed above:
  If recent news or your knowledge indicates the broad market (SPY) has declined
  > 2% over the past 3 trading days, apply the SPY weakness penalty.
  If recent news or your knowledge indicates the stock's sector ETF has declined
  > 3% over the past 5 trading days, apply the sector weakness penalty.
  If the stock price is within 5% of the put strike, apply the low margin of
  safety penalty. Current margin: {otm_pct:.1f}% OTM.

SCORING GUIDE
  75–100 → strong put-sell candidate
  50–74  → marginal — flag for human review
  0–49   → reject (too risky or poor premium)

RISK FACTORS THAT SHOULD LOWER SCORE
  • Earnings within 7 days (gap-down risk) — reduce score by 20 points
  • Stock in clear downtrend or recent sharp drop — reduce score by 15 points
  • Very low premium (< 0.5% weekly) — reduce score by 10 points
  • Negative news catalyst (downgrade, miss, scandal) — reduce score by 15 points
  • Sector ETF down > 3% over past 5 days (sector weakness) — reduce score by 15 points
  • SPY down > 2% over past 3 days (broad market weakness) — reduce score by 10 points
  • Stock price within 5% of strike (low margin of safety) — reduce score by 10 points

POSITIVE FACTORS THAT SHOULD RAISE SCORE
  • Stock near support level (put strike near 52-week support)
  • Bullish news or analyst upgrades
  • Premium > 1% weekly (excellent income yield)
  • RSI recovering from oversold (stock likely to stay above strike)

Return exactly this JSON:
{{
  "conviction_score": <integer 0–100>,
  "recommendation": <"approve" | "review" | "reject">,
  "reasoning": "<2–4 sentences explaining the score>",
  "position_size_contracts": <1 | 2 | 3>,
  "key_risks": ["<risk 1>", "<risk 2>"]
}}"""
    return prompt


def _score(prompt: str, output_type, role, llm=None) -> dict:
    """One structured scoring call. Raises when nothing usable came back."""
    if role is None:
        raise RuntimeError("no conviction model linked")
    if llm is None:
        from llm_utils import call_structured_llm_by_provider as llm
    out = llm(role["provider"], role["api_key"], role["model"], prompt, output_type,
              max_output_tokens=600, timeout_sec=SCORE_TIMEOUT_S, retries=0,
              provider_config=role.get("provider_config") or None)
    if out is None:
        raise ValueError("the model returned no valid JSON object")
    if hasattr(out, "model_dump"):
        return dict(out.model_dump())
    if isinstance(out, dict):
        return dict(out)
    raise ValueError(f"unexpected scoring result {type(out).__name__}")


def apply_thresholds(result: dict, approve_threshold, review_threshold) -> dict:
    """ai_analyst.py:306-313 — enforce thresholds regardless of what the model
    said in the text. A score outside 0-100 is a failed call (review focus 3)."""
    score = int(result.get("conviction_score", 0))
    if not 0 <= score <= 100:
        raise ValueError(f"conviction_score {score} is outside 0-100")
    result["conviction_score"] = score
    if score >= approve_threshold:
        result["recommendation"] = "approve"
    elif score >= review_threshold:
        result["recommendation"] = "review"
    else:
        result["recommendation"] = "reject"
    return result


def handle_result(result: dict) -> None:
    """ai_analyst.py:343-384 without the pending_trades.json write (the caller
    records a SwingSignals row). Raises ValueError for a REVIEW whose prices
    fail validation."""
    sym   = result["symbol"]
    score = result["conviction_score"]
    rec   = result["recommendation"]
    adj   = result["position_size_adjustment"]

    if rec == "approve":
        _log(f"  [AI] AUTO-APPROVED  {sym}  score={score}  size_adj={adj}x\n"
             f"       {result['reasoning'][:100]}")
        return

    if rec == "reject":
        _log(f"  [AI] REJECTED       {sym}  score={score}\n"
             f"       {result['reasoning'][:100]}")
        return

    # review — validate prices before recording the pending signal
    entry  = float(result.get("entry_price", 0))
    stop   = float(result.get("stop_price",  0))
    target = float(result.get("target_price", 0))
    if stop >= entry * 0.99:
        raise ValueError(
            f"Price validation failed for {sym}: "
            f"stop_price ${stop:.4f} must be < entry_price ${entry:.4f} × 0.99 "
            f"(got stop/entry = {stop/entry:.4f})"
        )
    if target <= entry * 1.01:
        raise ValueError(
            f"Price validation failed for {sym}: "
            f"target_price ${target:.4f} must be > entry_price ${entry:.4f} × 1.01 "
            f"(got target/entry = {target/entry:.4f})"
        )
    _log(f"  [AI] REVIEW NEEDED  {sym}  score={score}  size_adj={adj}x")


def analyse(signal: dict, *, role, sector_of, bars_client=None, llm=None,
            earnings_fn=None, news_fn=None,
            approve_threshold=AI_APPROVE_THRESHOLD,
            review_threshold=AI_REVIEW_THRESHOLD) -> dict:
    """ai_analyst.py:203-338 over the linked model. Raises on a failed call,
    an out-of-range score, or a REVIEW whose prices fail the checks; the
    caller skips that candidate and moves on (spec §9 fix 5)."""
    symbol         = str(signal["symbol"]).upper()
    rsi            = float(signal["rsi"])
    rsi_prev       = float(signal["rsi_prev"])
    macd_hist      = float(signal["macd_hist"])
    macd_hist_prev = float(signal["macd_hist_prev"])
    entry_price    = float(signal["entry_price"])
    shares         = int(signal["shares"])
    stop_price     = float(signal["stop_price"])
    target_price   = float(signal["target_price"])

    risk_pct   = (entry_price - stop_price) / entry_price * 100
    reward_pct = (target_price - entry_price) / entry_price * 100
    rr_ratio   = reward_pct / risk_pct if risk_pct else 0.0

    # ── Enrichment (runs in serial to avoid rate-limiting) ────────────────
    earnings_days = (earnings_fn or days_until_earnings)(symbol)
    etf_sym  = resolve_sector_etf(symbol, sector_of) or "unknown"
    etf_rsi  = sector_etf_rsi(symbol, sector_of=sector_of, client=bars_client)
    news = (news_fn or fetch_news_summary)(symbol, role)

    prompt = build_swing_prompt(
        symbol=symbol, entry_price=entry_price, shares=shares, stop_price=stop_price,
        target_price=target_price, risk_pct=risk_pct, reward_pct=reward_pct,
        rr_ratio=rr_ratio, rsi=rsi, rsi_prev=rsi_prev, macd_hist=macd_hist,
        macd_hist_prev=macd_hist_prev, etf_sym=etf_sym, etf_rsi=etf_rsi,
        earnings_days=earnings_days, news=news)

    result = apply_thresholds(_score(prompt, SwingConviction, role, llm),
                              approve_threshold, review_threshold)

    # Validate position_size_adjustment
    valid_adj = {1.0, 0.5, 0.25}
    adj = float(result.get("position_size_adjustment", 1.0))
    result["position_size_adjustment"] = adj if adj in valid_adj else 1.0

    # Attach enrichment and identity fields
    result.update({
        "symbol":         symbol,
        "entry_price":    entry_price,
        "shares":         shares,
        "stop_price":     stop_price,
        "target_price":   target_price,
        "risk_pct":       round(risk_pct, 2),
        "reward_pct":     round(reward_pct, 2),
        "rr_ratio":       round(rr_ratio, 2),
        "earnings_days":  earnings_days,
        "sector_etf":     etf_sym,
        "sector_rsi":     etf_rsi,
        "news_summary":   news,
        "timestamp":      datetime.now(timezone.utc).isoformat(),
    })

    handle_result(result)
    return result


def score_candidate(candidate: dict, *, role, llm=None, news_fn=None,
                    approve_threshold=WHEEL_APPROVE_THRESHOLD,
                    review_threshold=WHEEL_REVIEW_THRESHOLD,
                    rsi_min=RSI_MIN, rsi_max=RSI_MAX,
                    days_to_expiry=DAYS_TO_EXPIRY) -> dict:
    """wheel_trader.py:958-1072: score a put-selling candidate 0–100 and return
    a copy enriched with the AI fields. Any failure is ST's REJECT, score 0."""
    symbol = candidate["symbol"]
    if news_fn is None:
        def news_fn(s, r):
            return fetch_news_summary(s, r, prompt_template=WHEEL_NEWS_PROMPT)
    news = news_fn(symbol, role)
    prompt = build_wheel_prompt(candidate, news, rsi_min=rsi_min, rsi_max=rsi_max,
                                days_to_expiry=days_to_expiry)
    try:
        result = _score(prompt, WheelConviction, role, llm)
        score = int(result.get("conviction_score", 0))
        if not 0 <= score <= 100:
            raise ValueError(f"conviction_score {score} is outside 0-100")
        result["conviction_score"] = score
        if score >= approve_threshold:
            result["recommendation"] = "approve"
        elif score >= review_threshold:
            result["recommendation"] = "review"
        else:
            result["recommendation"] = "reject"

        contracts = int(result.get("position_size_contracts", 1))
        result["position_size_contracts"] = max(1, min(contracts, 3))

    except Exception as exc:
        _log(f"  [AI-wheel] ERROR scoring {symbol}: {exc}", "yellow")
        result = {
            "conviction_score":      0,
            "recommendation":        "reject",
            "reasoning":             f"AI scoring failed: {exc}",
            "position_size_contracts": 1,
            "key_risks":             ["AI scoring error"],
        }

    scored = dict(candidate)
    scored.update(result)
    scored["timestamp"] = datetime.now(timezone.utc).isoformat()
    return scored
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `python3 -m pytest backend/tests/test_swing_ai_analyst.py -q -p no:cacheprovider`
Expected: PASS (16 tests).

- [ ] **Step 6: Commit**

```bash
git add backend/swing_trader/ai_analyst.py backend/tests/fixtures/swing_trader_st/ai_analyst_pure.py backend/tests/test_swing_ai_analyst.py
git commit -m "feat(swing): port ST's AI analyst onto the linked model

Both prompts are ST's byte for byte, pinned against the vendored source.
Scoring asks the linked conviction model for a validated JSON object, the
thresholds and size clamp are re-applied in code, and a malformed reply or
a score outside 0-100 fails that one candidate.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016ztXjLWXu61Dpp4XMU2jsS"
```

---

### Task 14: Signal store and the approval state machine (B8)

**Files:**
- Create: `backend/swing_trader/signals_store.py`
- Create: `backend/swing_trader/approvals.py`
- Test: `backend/tests/test_swing_approvals.py`

**Interfaces:**
- Consumes: tables (Task 1), `refdata` table names (Task 6), `clock` (Task 5), `wheel_rules.next_friday`, `duplicate_put_reason`, `build_put_order_live` (Tasks 8–9).
- Consumes (live, plan A-live): `adapter.get_account_options() -> dict` (`cash`, `equity`), `adapter.list_option_positions()`, `adapter.list_open_orders()`, `adapter.get_option_contracts(...)`, `adapter.get_option_snapshots(...)` (contract §3). Tests use a fake.
- Produces (`signals_store`): `ensure_tables()`, `signal_id_for(instance_id, lane, session, symbol) -> str`, `new_signal(*, instance_id, lane, symbol, session, score, recommendation, reasoning, key_risks, size_adjustment, proposal, status, context=None, created_at=None) -> dict`, `insert_signal(doc) -> str`, `get_signal(signal_id) -> dict | None`, `update_signal(signal_id, patch) -> None`, `cas_signal(signal_id, *, expect_status, doc) -> bool`, `list_signals(instance_id, status=None, limit=100) -> list[dict]` (newest first), `all_signals(instance_id) -> list[dict]`, `swing_owned_symbols(instance_id, held) -> set[str]`, `insert_wheel_scan(row) -> str`, `list_wheel_scans(instance_id, limit=50) -> list[dict]` (newest first), `OPEN_STATUSES = ("auto_approved", "submitted")`.
- Produces (`approvals`): `DECISION_STATUS`, `APPROVED_STATUSES = ("approved", "approved_half")`, `SignalConflict(ValueError)`, `decide(signal, decision, user, reason, now_iso) -> dict`, `build_approved_order(signal, *, live_price, equity, cfg, adapter=None, today=None) -> dict` — swing `{"kind": "equity_bracket", "symbol", "qty", "take_profit_price", "stop_loss_price"}`, wheel `{"kind": "option", **option order dict}`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_swing_approvals.py`:

```python
"""SwingSignals access, the decision state machine, and the order rebuild at
approval time (ST app.py api_approve / api_approve_wheel; fix 2)."""
import os
import sys
from datetime import date
from types import SimpleNamespace as NS

import pytest

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from swing_trader import approvals, signals_store  # noqa: E402

CFG = {"stop_loss": 0.06, "profit_target": 0.09, "position_size_pct": 0.125,
       "max_collateral_pct": 0.25, "target_delta": 0.25, "limit_bid_mult": 0.95}


@pytest.fixture
def ss(store, monkeypatch):
    monkeypatch.setattr(signals_store, "store", store)
    return store


def sig(symbol="AAPL", lane="swing", status="pending", created="2026-06-01T13:25:00+00:00",
        instance="swing-paper", session="2026-06-01", adj=0.5, proposal=None, score=62):
    return signals_store.new_signal(
        instance_id=instance, lane=lane, symbol=symbol, session=session, score=score,
        recommendation="review", reasoning="Clean pullback.", key_risks=["Earnings soon"],
        size_adjustment=adj, status=status, created_at=created,
        proposal=proposal or {"entry": 190.0, "stop": 178.6, "target": 207.1, "shares": 65})


def test_signal_ids_are_deterministic_uuid_hex():
    a = signals_store.signal_id_for("swing-paper", "swing", "2026-06-01", "aapl")
    assert a == signals_store.signal_id_for("swing-paper", "swing", "2026-06-01", "AAPL")
    assert len(a) == 32 and int(a, 16) >= 0
    assert a != signals_store.signal_id_for("swing-paper", "wheel", "2026-06-01", "AAPL")
    assert a != signals_store.signal_id_for("swing-paper", "swing", "2026-06-02", "AAPL")


def test_new_signal_is_the_contract_document():
    doc = sig()
    assert set(doc) >= {"id", "instance_id", "lane", "symbol", "session", "created_at",
                        "score", "recommendation", "reasoning", "key_risks",
                        "size_adjustment", "proposal", "status", "decided_by",
                        "decided_at", "decision_reason", "order_client_id", "outcome"}
    assert doc["recommendation"] == "REVIEW" and doc["outcome"] is None


def test_insert_keeps_the_first_row(ss):
    signals_store.insert_signal(sig())
    signals_store.insert_signal(sig(status="auto_approved"))
    assert signals_store.get_signal(sig()["id"])["status"] == "pending"
    assert signals_store.get_signal("nope") is None


def test_list_signals_filters_by_instance_and_status_newest_first(ss):
    signals_store.insert_signal(sig("AAA", created="2026-06-01T13:21:00+00:00"))
    signals_store.insert_signal(sig("BBB", created="2026-06-01T13:22:00+00:00"))
    signals_store.insert_signal(sig("CCC", status="ai_rejected",
                                    created="2026-06-01T13:23:00+00:00"))
    signals_store.insert_signal(sig("DDD", instance="other"))
    assert [r["symbol"] for r in signals_store.list_signals("swing-paper", "pending")] == ["BBB", "AAA"]
    assert [r["symbol"] for r in signals_store.list_signals("swing-paper")] == ["CCC", "BBB", "AAA"]
    assert len(signals_store.list_signals("swing-paper", limit=1)) == 1
    assert {r["symbol"] for r in signals_store.all_signals("swing-paper")} == {"AAA", "BBB", "CCC"}


def test_cas_signal_is_a_compare_and_swap(ss):
    doc = sig()
    signals_store.insert_signal(doc)
    assert signals_store.cas_signal(doc["id"], expect_status="pending",
                                    doc=dict(doc, status="approved")) is True
    assert signals_store.cas_signal(doc["id"], expect_status="pending",
                                    doc=dict(doc, status="rejected")) is False
    assert signals_store.get_signal(doc["id"])["status"] == "approved"


def test_update_signal_deep_merges(ss):
    doc = sig()
    signals_store.insert_signal(doc)
    signals_store.update_signal(doc["id"], {"outcome": {"pnl": 12.5}})
    signals_store.update_signal(doc["id"], {"outcome": {"pnl_pct": 1.2}})
    assert signals_store.get_signal(doc["id"])["outcome"] == {"pnl": 12.5, "pnl_pct": 1.2}


def test_swing_owned_symbols(ss):
    signals_store.insert_signal(sig("KR", status="auto_approved"))
    signals_store.insert_signal(sig("LITE", status="submitted"))
    signals_store.insert_signal(sig("GIS", status="pending"))
    closed = sig("PEP", status="auto_approved")
    closed["outcome"] = {"pnl": 1.0}
    signals_store.insert_signal(closed)
    signals_store.insert_signal(sig("APH", lane="wheel", status="auto_approved"))
    held = {"KR", "LITE", "GIS", "PEP", "APH", "XOM"}
    assert signals_store.swing_owned_symbols("swing-paper", held) == {"KR", "LITE"}
    assert signals_store.swing_owned_symbols("swing-paper", set()) == set()


def test_wheel_scans_round_trip(ss):
    base = {"instance_id": "swing-paper", "session": "2026-06-01", "symbol": "APH",
            "stock_price": 131.2, "strike": 127.8, "expiry": "2026-06-12",
            "premium_est": 1.7, "score": 81, "recommendation": "APPROVE",
            "reasoning": "r", "status": "placed", "skip_reason": None}
    first = signals_store.insert_wheel_scan(dict(base, created_at="2026-06-01T14:41:00+00:00"))
    again = signals_store.insert_wheel_scan(dict(base, created_at="2026-06-01T14:42:00+00:00",
                                                 status="skipped"))
    assert first == again                                  # a resumed scan replaces
    signals_store.insert_wheel_scan(dict(base, symbol="GIS", created_at="2026-06-01T14:43:00+00:00"))
    rows = signals_store.list_wheel_scans("swing-paper")
    assert [r["symbol"] for r in rows] == ["GIS", "APH"] and rows[1]["status"] == "skipped"
    assert len(signals_store.list_wheel_scans("swing-paper", limit=1)) == 1


# -- decide ------------------------------------------------------------------

def test_decide_statuses_and_audit_fields():
    now = "2026-06-01T14:00:00+00:00"
    for decision, status in (("approve", "approved"), ("approve_half", "approved_half"),
                             ("reject", "rejected")):
        out = approvals.decide(sig(), decision, "pranav", "  looks fine  ", now)
        assert out["status"] == status and out["decided_by"] == "pranav"
        assert out["decided_at"] == now and out["decision_reason"] == "looks fine"
    assert approvals.decide(sig(), "reject", "pranav", None, now)["decision_reason"] is None


def test_a_second_decision_conflicts():
    first = approvals.decide(sig(), "approve", "pranav", None, "t1")
    for status in ("approved", "approved_half", "rejected", "submitted", "failed",
                   "auto_approved", "ai_rejected"):
        with pytest.raises(approvals.SignalConflict):
            approvals.decide(dict(first, status=status), "reject", "pranav", None, "t2")
    assert issubclass(approvals.SignalConflict, ValueError)


def test_an_unknown_decision_is_a_value_error():
    with pytest.raises(ValueError, match="decision must be one of"):
        approvals.decide(sig(), "maybe", "pranav", None, "t")


# -- build_approved_order: swing (app.py:1004-1045) ---------------------------

def test_the_swing_order_is_rebuilt_at_the_live_price():
    s = dict(sig(), status="approved")
    out = approvals.build_approved_order(s, live_price=100.0, equity=100_000.0, cfg=CFG)
    # base = int(12_500 / 100) = 125; x0.5 adjustment = 62 shares
    assert out == {"kind": "equity_bracket", "symbol": "AAPL", "qty": 62,
                   "take_profit_price": 109.0, "stop_loss_price": 94.0}


def test_approve_half_halves_the_adjusted_shares():
    s = dict(sig(), status="approved_half")
    assert approvals.build_approved_order(s, live_price=100.0, equity=100_000.0,
                                          cfg=CFG)["qty"] == 31


def test_a_price_too_small_for_the_brackets_fails_the_sanity_check():
    s = dict(sig(), status="approved")
    with pytest.raises(ValueError, match="after recalc"):
        approvals.build_approved_order(s, live_price=0.1, equity=100_000.0, cfg=CFG)
    with pytest.raises(ValueError):
        approvals.build_approved_order(s, live_price=0.0, equity=100_000.0, cfg=CFG)


# -- build_approved_order: wheel (app.py:888-940, fix 2) ----------------------

def contract(strike, expiry):
    ymd = expiry[2:4] + expiry[5:7] + expiry[8:10]
    return NS(symbol=f"APH{ymd}P{int(strike * 1000):08d}", underlying="APH",
              option_type="put", strike=strike, expiration=expiry,
              open_interest=10, close_price=1.9)


class WheelAdapter:
    def __init__(self, positions=()):
        self.positions = list(positions)
        self.chain = [contract(125.0, "2026-05-29"), contract(125.0, "2026-06-12"),
                      contract(128.0, "2026-06-12")]

    def get_option_contracts(self, underlying, **kw):
        return [c for c in self.chain if kw["expiration_gte"] <= c.expiration <= kw["expiration_lte"]]

    def get_option_snapshots(self, symbols):
        return {s: NS(bid=1.8, delta=(-0.25 if "00128000" in s else -0.15)) for s in symbols}

    def get_account_options(self):
        return {"cash": 200_000.0, "equity": 200_000.0}

    def list_option_positions(self):
        return list(self.positions)

    def list_open_orders(self, limit=200):
        return []


def wheel_sig(status="approved", qty=2):
    return signals_store.new_signal(
        instance_id="swing-paper", lane="wheel", symbol="APH", session="2026-05-18",
        score=66, recommendation="review", reasoning="r", key_risks=[], size_adjustment=None,
        status=status, created_at="2026-05-18T14:41:00+00:00",
        proposal={"contract": None, "strike": 127.8, "expiry": "2026-05-29", "qty": qty,
                  "limit_price": None, "premium_est": 1.7, "delta": None})


def test_fix_2_the_wheel_expiry_is_recomputed_at_approval():
    out = approvals.build_approved_order(wheel_sig(), live_price=131.2, equity=200_000.0,
                                         cfg=CFG, adapter=WheelAdapter(),
                                         today=date(2026, 6, 1))
    assert out["kind"] == "option" and out["expiry"] == "2026-06-12"
    assert out["contract"] == "APH260612P00128000" and out["strike"] == 128.0
    assert out["position_intent"] == "sell_to_open" and out["qty"] == 2
    assert out["limit_price"] == round(1.8 * 0.95, 2)
    half = approvals.build_approved_order(wheel_sig("approved_half"), live_price=131.2,
                                          equity=200_000.0, cfg=CFG, adapter=WheelAdapter(),
                                          today=date(2026, 6, 1))
    assert half["qty"] == 1


def test_a_wheel_approval_refuses_a_duplicate_put_and_needs_the_adapter():
    held = NS(symbol="APH260605P00120000", underlying="APH", option_type="put",
              strike=120.0, expiry="2026-06-05", qty=-1, avg_entry_price=1.0,
              market_value=-100.0)
    with pytest.raises(ValueError, match="duplicate"):
        approvals.build_approved_order(wheel_sig(), live_price=131.2, equity=200_000.0,
                                       cfg=CFG, adapter=WheelAdapter([held]),
                                       today=date(2026, 6, 1))
    with pytest.raises(ValueError, match="adapter"):
        approvals.build_approved_order(wheel_sig(), live_price=131.2, equity=200_000.0,
                                       cfg=CFG)


def test_an_unknown_lane_raises():
    with pytest.raises(ValueError, match="lane"):
        approvals.build_approved_order(dict(sig(), lane="crypto"), live_price=1.0,
                                       equity=1.0, cfg=CFG)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest backend/tests/test_swing_approvals.py -q -p no:cacheprovider`
Expected: FAIL — `ImportError: cannot import name 'approvals' from 'swing_trader'`.

- [ ] **Step 3: Write signals_store.py**

Create `backend/swing_trader/signals_store.py`:

```python
"""SwingSignals and SwingWheelScans (spec §7, contract §5-6).

ST kept reviews in pending_trades.json / pending_wheel.json and scans in
wheel_trades.csv; here they are rows. `store` is a module attribute so tests
swap in the FakeStore fixture. Signal ids are DETERMINISTIC per
(instance, lane, session, symbol): a crashed and resumed scan finds its own
row instead of re-scoring the candidate and notifying twice.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from db import store  # noqa: F401  (tests monkeypatch this name)
from db.store import P

from swing_trader.refdata import SCANS_TABLE, SIGNALS_TABLE, SWING_TABLES

_NAMESPACE = uuid.UUID("5f0c3d1e-9a4b-4c1e-8f7a-2b6d9e0a1c35")
OPEN_STATUSES = ("auto_approved", "submitted")
_ENSURED = False


def ensure_tables() -> None:
    """Create the six tables once per process. DDL stays in db/schema.py; a
    test's FakeStore needs none."""
    global _ENSURED
    if _ENSURED:
        return
    from db import schema
    from db import store as real_store
    if store is real_store:
        schema.ensure_schema(tables=list(SWING_TABLES))
    _ENSURED = True


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def signal_id_for(instance_id, lane, session, symbol) -> str:
    key = f"{instance_id}|{lane}|{str(session)[:10]}|{str(symbol).strip().upper()}"
    return uuid.uuid5(_NAMESPACE, key).hex


def new_signal(*, instance_id, lane, symbol, session, score, recommendation, reasoning,
               key_risks, size_adjustment, proposal, status, context=None,
               created_at=None) -> dict:
    return {
        "id": signal_id_for(instance_id, lane, session, symbol),
        "instance_id": str(instance_id), "lane": lane,
        "symbol": str(symbol).strip().upper(), "session": str(session)[:10],
        "created_at": created_at or _now_iso(), "score": score,
        "recommendation": str(recommendation or "").upper(),
        "reasoning": str(reasoning or ""),
        "key_risks": [str(r) for r in (key_risks or [])],
        "size_adjustment": size_adjustment, "proposal": dict(proposal or {}),
        "status": status, "decided_by": None, "decided_at": None,
        "decision_reason": None, "order_client_id": None, "outcome": None,
        "context": dict(context or {}),
    }


def insert_signal(doc: dict) -> str:
    """Insert once; a row that already exists (a resumed scan) is kept."""
    ensure_tables()
    store.insert(SIGNALS_TABLE, doc, conflict="error")
    return doc["id"]


def get_signal(signal_id):
    if not signal_id:
        return None
    return store.get(SIGNALS_TABLE, str(signal_id))


def update_signal(signal_id, patch: dict) -> None:
    store.update(SIGNALS_TABLE, str(signal_id), dict(patch))


def cas_signal(signal_id, *, expect_status, doc: dict) -> bool:
    """Replace the row only while its status is still `expect_status`."""
    return store.replace_if(SIGNALS_TABLE, str(signal_id),
                            when=P.field("status").eq(str(expect_status)),
                            doc=dict(doc)) is not None


def list_signals(instance_id, status=None, limit: int = 100) -> list:
    pred = P.field("instance_id").eq(str(instance_id))
    if status:
        pred = pred & P.field("status").eq(str(status))
    sel = store.order_by(store.filter(SIGNALS_TABLE, pred),
                         fields=(store.desc("created_at"),))
    return list(store.run(store.limit(sel, max(1, min(int(limit), 500)))))


def all_signals(instance_id) -> list:
    return list(store.iter(store.filter(
        SIGNALS_TABLE, P.field("instance_id").eq(str(instance_id)))))


def swing_owned_symbols(instance_id, held) -> set:
    """Held symbols the swing lane entered and has not closed: a swing row
    that was auto-approved or submitted and carries no outcome yet. ST read
    the same fact off paper_trades.csv (wheel_trader.py:324-345)."""
    held = {str(s).strip().upper() for s in (held or ())}
    if not held:
        return set()
    rows = store.run(store.filter(
        SIGNALS_TABLE,
        P.field("instance_id").eq(str(instance_id)) & P.field("lane").eq("swing")))
    return {str(r.get("symbol") or "").upper() for r in rows
            if str(r.get("symbol") or "").upper() in held
            and r.get("status") in OPEN_STATUSES and not r.get("outcome")}


def insert_wheel_scan(row: dict) -> str:
    """One row per candidate per session; a resumed scan replaces its row."""
    ensure_tables()
    doc = dict(row)
    doc["id"] = doc.get("id") or signal_id_for(doc.get("instance_id"), "wheelscan",
                                               doc.get("session"), doc.get("symbol"))
    doc.setdefault("created_at", _now_iso())
    store.insert(SCANS_TABLE, doc, conflict="replace")
    return doc["id"]


def list_wheel_scans(instance_id, limit: int = 50) -> list:
    sel = store.order_by(store.filter(SCANS_TABLE, P.field("instance_id").eq(str(instance_id))),
                         fields=(store.desc("created_at"),))
    return list(store.run(store.limit(sel, max(1, min(int(limit), 500)))))
```

- [ ] **Step 4: Write approvals.py**

Create `backend/swing_trader/approvals.py`:

```python
"""Approval state machine and order rebuild, ported from ST app.py.

    app.py:982-1073   api_approve: the stored prices are stale at approval, so
                      the bracket legs and the share count are recomputed at
                      the live price ("reduce" became approve_half)
    app.py:888-940    api_approve_wheel: place_put_order on the stored
                      candidate, whose expiry is now recomputed (fix 2)
    app.py:1076-1108  api_reject
A decision is final: pending -> approved | approved_half | rejected. The
compare-and-swap that makes it final against a concurrent click is
signals_store.cas_signal; the order goes out through the broker's
submit_order LiveCommand (plan A-live's _execute_swing_approval in broker.py).
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from swing_trader import clock, wheel_rules
from swing_trader.constants import POSITION_SIZE_PCT, PROFIT_TARGET, STOP_LOSS

DECISION_STATUS = {"approve": "approved", "approve_half": "approved_half",
                   "reject": "rejected"}
APPROVED_STATUSES = ("approved", "approved_half")


class SignalConflict(ValueError):
    """A decision on a signal that is not pending (already decided, submitted
    or failed). api/main.py:_run maps it to 400. A click that loses the
    compare-and-swap is the route's 409 (interactive_utils.SwingDecisionRaceError)."""


def decide(signal: dict, decision: str, user, reason, now_iso: str) -> dict:
    status = DECISION_STATUS.get(str(decision or "").strip().lower())
    if status is None:
        raise ValueError(f"decision must be one of {sorted(DECISION_STATUS)}, "
                         f"got {decision!r}")
    current = str((signal or {}).get("status") or "")
    if current != "pending":
        raise SignalConflict(f"signal {(signal or {}).get('id')} is "
                             f"{current or 'missing'}, not pending — decisions are final")
    out = dict(signal)
    out.update({"status": status, "decided_by": (str(user) if user else None),
                "decided_at": now_iso,
                "decision_reason": (str(reason).strip()[:500] or None) if reason else None})
    return out


def build_approved_order(signal: dict, *, live_price, equity, cfg, adapter=None,
                         today=None) -> dict:
    lane = (signal or {}).get("lane")
    half = signal.get("status") == "approved_half"
    cfg = cfg or {}

    if lane == "swing":
        live_price = float(live_price or 0.0)
        if not live_price > 0:
            raise ValueError(f"no live price for {signal.get('symbol')}")
        stop_loss = float(cfg.get("stop_loss", STOP_LOSS))
        profit_target = float(cfg.get("profit_target", PROFIT_TARGET))
        size_pct = float(cfg.get("position_size_pct", POSITION_SIZE_PCT))

        # Recalculate bracket legs from live price using strategy percentages
        stop_price   = round(live_price * (1 - stop_loss),    2)
        target_price = round(live_price * (1 + profit_target), 2)

        stored_adj = float(signal.get("size_adjustment") or 1.0)
        base_shares = max(1, int((float(equity) * size_pct) / live_price))
        # Apply AI position_size_adjustment, then halve again if the operator
        # chose approve_half (ST: the REDUCE button)
        adj_shares  = max(1, int(base_shares * stored_adj))
        shares      = max(1, adj_shares // 2) if half else adj_shares

        # Sanity-check recalculated prices
        if stop_price >= live_price - 0.01:
            raise ValueError(f"stop_price ${stop_price:.4f} >= live_price "
                             f"${live_price:.4f} − 0.01 after recalc")
        if target_price <= live_price + 0.01:
            raise ValueError(f"target_price ${target_price:.4f} <= live_price "
                             f"${live_price:.4f} + 0.01 after recalc")
        return {"kind": "equity_bracket", "symbol": signal["symbol"], "qty": shares,
                "take_profit_price": target_price, "stop_loss_price": stop_price}

    if lane == "wheel":
        if adapter is None:
            raise ValueError("a wheel approval needs the broker adapter")
        today = today or date.fromisoformat(clock.ny_date(datetime.now(timezone.utc)))
        # fix 2: ST placed the order on the stored candidate's expiry, which is
        # in the past once a review waits into the next week.
        expiry = wheel_rules.next_friday(today, clock.trading_days)
        proposal = dict(signal.get("proposal") or {})
        qty = int(proposal.get("qty") or 1)
        if half:
            qty = max(1, qty // 2)
        candidate = {"symbol": signal["symbol"], "stock_price": float(live_price or 0.0),
                     "strike_price": float(proposal["strike"]), "expiry": expiry,
                     "est_premium": float(proposal.get("premium_est") or 0.0),
                     "position_size_contracts": qty}
        option_positions = list(adapter.list_option_positions() or [])
        open_orders = list(adapter.list_open_orders() or [])
        duplicate = wheel_rules.duplicate_put_reason(signal["symbol"], option_positions,
                                                     open_orders)
        if duplicate:
            raise ValueError(duplicate)
        account = adapter.get_account_options() or {}
        order, error, _meta = wheel_rules.build_put_order_live(
            candidate, adapter=adapter, cfg=cfg, equity=float(equity),
            cash=float(account.get("cash") or 0.0), option_positions=option_positions,
            open_orders=open_orders, signal_id=signal.get("id"),
            session=today.isoformat())
        if order is None:
            raise ValueError(error or "no order could be built")
        return {"kind": "option", **order}

    raise ValueError(f"unknown lane {lane!r}")
```

Note the ordering for `decision_reason`: `"  looks fine  "` strips to `"looks fine"`; an all-space reason becomes None.

- [ ] **Step 5: Run the test to verify it passes**

Run: `python3 -m pytest backend/tests/test_swing_approvals.py -q -p no:cacheprovider`
Expected: PASS (17 tests).

- [ ] **Step 6: Commit**

```bash
git add backend/swing_trader/signals_store.py backend/swing_trader/approvals.py backend/tests/test_swing_approvals.py
git commit -m "feat(swing): signal store and the approval state machine

Decisions are final (pending to approved, approved_half or rejected) and a
second decision raises. An approved swing order is rebuilt at the live
price as ST's approve did; an approved wheel order gets a fresh expiry
(fix 2) and refuses a duplicate put.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016ztXjLWXu61Dpp4XMU2jsS"
```

---

### Task 15: IV snapshots and calibration (B11)

**Files:**
- Create: `backend/swing_trader/iv.py`
- Create: `backend/swing_trader/calibration.py`
- Test: `backend/tests/test_swing_iv.py`
- Test: `backend/tests/test_swing_calibration.py`

**Interfaces:**
- Consumes: `refdata.IV_TABLE` (Task 6), `signals_store` (Task 14), `clock` (Task 5), constants.
- Consumes (live, plan A-live): `adapter.get_option_contracts(symbol, *, expiration_gte, expiration_lte, strike_gte, strike_lte)`, `adapter.get_option_snapshots(contracts)` (`OptionSnapshotDTO.iv`), `adapter.list_closed_orders(symbols, after) -> list[OrderRef]` (`OrderRef.legs`).
- Produces (`iv`): `snapshot_iv(symbol, *, today=None) -> float | None`, `snapshot_iv_alpaca(symbol, *, adapter, spot, today) -> float | None`, `iv_row_id(symbol, day) -> str`, `run_iv_snapshot(store, *, adapter, spot_for, today, symbols=None, deadline=None, now_fn=time.monotonic) -> dict` (`date, recorded, skipped, failed, complete`), `load_iv_rank(store, symbol) -> float | None`.
- Produces (`calibration`): `_bucket_label`, `_score_of`, `swing_calibration(rows) -> dict`, `wheel_calibration(rows) -> dict`, `calibration_report(instance_id=None, *, rows=None) -> dict`, `resolve_swing_outcome(signal, orders) -> dict | None`, `resolve_wheel_outcome(signal, orders) -> dict | None`, `record_outcomes(instance_id, adapter, lane, *, held=None) -> int`.

- [ ] **Step 1: Write the failing IV test**

Create `backend/tests/test_swing_iv.py` (ST's `tests/test_iv_collector.py`, ported to the table):

```python
"""IV snapshots and IV Rank (ST iv_collector.py), stored in SwingIvSnapshots."""
import os
import sys
from datetime import date
from types import SimpleNamespace as NS
from unittest.mock import MagicMock

import pandas as pd
import pytest

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from swing_trader import iv as ivc  # noqa: E402

TODAY = date(2026, 6, 1)


def _mock_ticker(spot=100.0, call_iv=0.30, put_iv=0.34, expiries=("2026-07-02",)):
    t = MagicMock()
    t.options = expiries
    t.history.return_value = pd.DataFrame({"Close": [spot]})
    calls = pd.DataFrame({"strike": [95.0, 100.0, 105.0],
                          "impliedVolatility": [0.5, call_iv, 0.5]})
    puts = pd.DataFrame({"strike": [95.0, 100.0, 105.0],
                         "impliedVolatility": [0.5, put_iv, 0.5]})
    t.option_chain.return_value = NS(calls=calls, puts=puts)
    return t


def test_snapshot_iv_averages_atm_call_and_put(monkeypatch):
    monkeypatch.setattr(ivc, "yf", NS(Ticker=lambda s: _mock_ticker(call_iv=0.30, put_iv=0.34)))
    assert ivc.snapshot_iv("AAPL", today=TODAY) == 0.32


def test_snapshot_iv_returns_none_without_expiries_or_iv(monkeypatch):
    t = MagicMock()
    t.options = ()
    monkeypatch.setattr(ivc, "yf", NS(Ticker=lambda s: t))
    assert ivc.snapshot_iv("AAPL", today=TODAY) is None
    t2 = _mock_ticker()
    nan_df = pd.DataFrame({"strike": [100.0], "impliedVolatility": [float("nan")]})
    t2.option_chain.return_value = NS(calls=nan_df, puts=nan_df)
    monkeypatch.setattr(ivc, "yf", NS(Ticker=lambda s: t2))
    assert ivc.snapshot_iv("AAPL", today=TODAY) is None


class OptAdapter:
    def get_option_contracts(self, symbol, **kw):
        self.kw = kw
        out = []
        for exp in ("2026-06-26", "2026-07-02", "2026-07-10"):
            for strike in (95.0, 100.0, 105.0):
                for kind in ("call", "put"):
                    out.append(NS(symbol=f"{kind}-{exp}-{strike}", underlying=symbol,
                                  option_type=kind, strike=strike, expiration=exp))
        return out

    def get_option_snapshots(self, symbols):
        self.requested = list(symbols)
        return {s: NS(iv=0.28 if s.startswith("call") else 0.32) for s in symbols}


def test_snapshot_iv_alpaca_takes_the_nearest_30_dte_atm_pair():
    a = OptAdapter()
    assert ivc.snapshot_iv_alpaca("AAPL", adapter=a, spot=100.4, today=TODAY) == 0.3
    assert sorted(a.requested) == ["call-2026-07-02-100.0", "put-2026-07-02-100.0"]
    assert a.kw["strike_gte"] == round(100.4 * 0.93, 2)
    assert ivc.snapshot_iv_alpaca("AAPL", adapter=a, spot=None, today=TODAY) is None


def test_run_iv_snapshot_writes_one_row_per_symbol(store, monkeypatch):
    monkeypatch.setattr(ivc, "snapshot_iv", lambda s, today=None: 0.25)
    monkeypatch.setattr(ivc, "snapshot_iv_alpaca", lambda s, **kw: 0.27)
    out = ivc.run_iv_snapshot(store, adapter=object(), spot_for=lambda s: 100.0,
                              today=TODAY, symbols=["AAPL", "MSFT"])
    assert out["recorded"] == ["AAPL", "MSFT"] and out["complete"] is True
    row = store.get("SwingIvSnapshots", "AAPL|2026-06-01")
    assert (row["iv30"], row["iv30_alpaca"], row["date"]) == (0.25, 0.27, "2026-06-01")


def test_run_iv_snapshot_idempotent_same_day(store, monkeypatch):
    monkeypatch.setattr(ivc, "snapshot_iv", lambda s, today=None: 0.25)
    monkeypatch.setattr(ivc, "snapshot_iv_alpaca", lambda s, **kw: 0.27)
    ivc.run_iv_snapshot(store, adapter=object(), spot_for=lambda s: 1.0, today=TODAY,
                        symbols=["AAPL"])
    again = ivc.run_iv_snapshot(store, adapter=object(), spot_for=lambda s: 1.0,
                                today=TODAY, symbols=["AAPL"])
    assert again["recorded"] == [] and again["skipped"] == ["AAPL"]


def test_one_leg_is_enough_and_none_is_a_failure(store, monkeypatch):
    monkeypatch.setattr(ivc, "snapshot_iv", lambda s, today=None: None)
    monkeypatch.setattr(ivc, "snapshot_iv_alpaca", lambda s, **kw: 0.31 if s == "AAPL" else None)
    out = ivc.run_iv_snapshot(store, adapter=object(), spot_for=lambda s: 1.0, today=TODAY,
                              symbols=["AAPL", "MSFT"])
    assert out["recorded"] == ["AAPL"] and out["failed"] == ["MSFT"]
    assert store.get("SwingIvSnapshots", "AAPL|2026-06-01")["iv30"] is None
    assert store.get("SwingIvSnapshots", "MSFT|2026-06-01") is None


def test_the_budget_stops_the_run_early(store, monkeypatch):
    monkeypatch.setattr(ivc, "snapshot_iv", lambda s, today=None: 0.2)
    monkeypatch.setattr(ivc, "snapshot_iv_alpaca", lambda s, **kw: 0.2)
    ticks = iter([100.0, 100.0, 100.0])
    out = ivc.run_iv_snapshot(store, adapter=object(), spot_for=lambda s: 1.0, today=TODAY,
                              symbols=["AAPL", "MSFT"], deadline=105.0,
                              now_fn=lambda: next(ticks))
    assert out["complete"] is False and out["recorded"] == []


def _history(store, symbol, ivs):
    store.insert("SwingIvSnapshots", [
        {"id": f"{symbol}|2026-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}", "symbol": symbol,
         "date": f"2026-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}", "iv30": iv,
         "iv30_alpaca": None} for i, iv in enumerate(ivs)], conflict="replace")


def test_iv_rank_computed_from_trailing_window(store):
    _history(store, "AAPL", [0.20 + (0.20 * i / 98) for i in range(99)] + [0.30])
    assert ivc.load_iv_rank(store, "AAPL") == 50.0


def test_iv_rank_none_below_min_rows_degenerate_or_missing(store):
    _history(store, "AAPL", [0.25] * (ivc.MIN_RANK_ROWS - 1))
    assert ivc.load_iv_rank(store, "AAPL") is None
    _history(store, "MSFT", [0.25] * 100)
    assert ivc.load_iv_rank(store, "MSFT") is None
    assert ivc.load_iv_rank(store, "NOPE") is None
```

- [ ] **Step 2: Write the failing calibration test**

Create `backend/tests/test_swing_calibration.py` (ST's `tests/test_calibration.py`, ported to `SwingSignals` rows, plus outcome resolution):

```python
"""Score-vs-outcome calibration (ST calibration.py) over SwingSignals, and the
outcome records the lanes write from the broker's closed orders."""
import os
import sys
from types import SimpleNamespace as NS

import pytest

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from swing_trader import calibration as cal, signals_store  # noqa: E402


def swing_row(symbol, score, buy, sell, shares):
    pnl = round((sell - buy) * shares, 2)
    return {"lane": "swing", "symbol": symbol, "score": score, "status": "auto_approved",
            "outcome": {"entry_price": buy, "exit_price": sell, "shares": shares,
                        "pnl": pnl, "pnl_pct": round((sell - buy) / buy * 100, 4)}}


def test_swing_buckets_round_trips_by_score():
    result = cal.swing_calibration([swing_row("AAPL", 80, 100.0, 110.0, 10),
                                    swing_row("MSFT", 55, 200.0, 190.0, 5)])
    assert result["closed_total"] == 2 and result["closed_scored"] == 2
    b75 = result["buckets"]["75-84"]
    assert (b75["count"], b75["win_rate"], b75["total_pnl"]) == (1, 100.0, 100.0)
    assert result["buckets"]["50-64"]["win_rate"] == 0.0


def test_swing_excludes_blank_scores_not_zero():
    result = cal.swing_calibration([swing_row("AAPL", None, 100.0, 110.0, 10)])
    assert (result["closed_total"], result["closed_scored"], result["excluded_unscored"]) == (1, 0, 1)
    assert all(b["count"] == 0 for b in result["buckets"].values())


def test_swing_insufficient_n_labeling():
    rows = [swing_row(f"S{i}", 90, 100.0, 105.0, 1) for i in range(6)]
    result = cal.swing_calibration(rows)
    assert result["buckets"]["85-100"]["insufficient_n"] is False
    assert result["buckets"]["50-64"]["insufficient_n"] is True


def test_open_rows_are_not_round_trips():
    open_row = dict(swing_row("AAPL", 80, 100.0, 110.0, 10), outcome=None)
    assert cal.swing_calibration([open_row])["closed_total"] == 0


def wheel_row(symbol, score, premium, contracts):
    return {"lane": "wheel", "symbol": symbol, "score": score, "status": "auto_approved",
            "outcome": None if premium is None else {"premium_received": premium,
                                                     "contracts": contracts}}


def test_wheel_buckets_placed_orders():
    result = cal.wheel_calibration([wheel_row("AAPL", 72, 2.50, 1),
                                    wheel_row("MSFT", 55, 1.00, 2),
                                    wheel_row("NVDA", 62, None, 1)])
    assert result["placed_total"] == 2
    assert result["buckets"]["65-74"]["total_premium"] == 250.0
    assert result["buckets"]["50-64"]["total_premium"] == 200.0


def test_wheel_with_no_rows_is_empty():
    assert cal.wheel_calibration([]) == {"placed_total": 0, "buckets": {}}


def test_report_gate():
    report = cal.calibration_report(rows=[swing_row("AAPL", 80, 100.0, 110.0, 10)])
    assert report["gate"] == {"closed_scored_trades": 1, "required": 20, "met": False}
    assert set(report) == {"swing", "wheel", "gate"}


def test_score_of_parses_like_st():
    assert [cal._score_of(v) for v in (None, "", "  ", "72", 72.9, "x", float("nan"))] == [
        None, None, None, 72, 72, None, None]


# -- outcomes -----------------------------------------------------------------

def order(symbol, side, price, qty=10, status="filled", at="2026-06-02T13:30:00+00:00",
          legs=(), oid="o"):
    return NS(broker_order_id=oid, client_order_id=oid, symbol=symbol, side=side,
              qty=qty, status=status, filled_qty=qty if status == "filled" else 0,
              filled_avg_price=price if status == "filled" else None,
              submitted_at_utc=at, legs=legs)


def test_a_bracket_round_trip_resolves_to_the_fill_prices():
    tp = order("AAPL", "sell", 109.2, at="2026-06-02T13:30:00+00:00", oid="tp")
    sl = order("AAPL", "sell", None, status="canceled", oid="sl")
    parent = order("AAPL", "buy", 100.4, legs=(tp, sl), oid="parent")
    signal = {"symbol": "AAPL", "created_at": "2026-06-01T13:25:00+00:00"}
    out = cal.resolve_swing_outcome(signal, [parent])
    assert (out["entry_price"], out["exit_price"], out["shares"]) == (100.4, 109.2, 10)
    assert out["pnl"] == 88.0 and out["exit_order_id"] == "tp"
    assert cal.resolve_swing_outcome(signal, [order("AAPL", "buy", 100.4)]) is None


def test_a_wheel_fill_records_the_premium():
    signal = {"lane": "wheel", "proposal": {"contract": "APH260612P00128000"}}
    fill = order("APH260612P00128000", "sell", 1.71, qty=2)
    assert cal.resolve_wheel_outcome(signal, [fill]) == {
        "premium_received": 1.71, "contracts": 2, "order_id": "o"}
    assert cal.resolve_wheel_outcome({"proposal": {}}, [fill]) is None


def test_record_outcomes_updates_closed_rows_only(store, monkeypatch):
    monkeypatch.setattr(signals_store, "store", store)
    for symbol, status in (("AAPL", "auto_approved"), ("MSFT", "auto_approved"),
                           ("GIS", "pending")):
        signals_store.insert_signal(signals_store.new_signal(
            instance_id="swing-paper", lane="swing", symbol=symbol, session="2026-06-01",
            score=80, recommendation="approve", reasoning="", key_risks=[],
            size_adjustment=1.0, proposal={}, status=status,
            created_at="2026-06-01T13:25:00+00:00"))

    class Adapter:
        def list_closed_orders(self, symbols, after):
            self.args = (sorted(symbols), after)
            return [order("AAPL", "buy", 100.0, legs=(order("AAPL", "sell", 110.0),))]

    a = Adapter()
    n = cal.record_outcomes("swing-paper", a, "swing", held={"MSFT"})
    assert n == 1 and a.args == (["AAPL"], "2026-06-01")
    rows = {r["symbol"]: r for r in signals_store.all_signals("swing-paper")}
    assert rows["AAPL"]["outcome"]["pnl"] == 100.0
    assert rows["MSFT"]["outcome"] is None and rows["GIS"]["outcome"] is None


def test_record_outcomes_survives_an_adapter_without_the_method(store, monkeypatch):
    monkeypatch.setattr(signals_store, "store", store)
    signals_store.insert_signal(signals_store.new_signal(
        instance_id="swing-paper", lane="swing", symbol="AAPL", session="2026-06-01",
        score=80, recommendation="approve", reasoning="", key_risks=[], size_adjustment=1.0,
        proposal={}, status="auto_approved"))
    assert cal.record_outcomes("swing-paper", object(), "swing", held=set()) == 0
```

- [ ] **Step 3: Run them to verify they fail**

Run: `python3 -m pytest backend/tests/test_swing_iv.py backend/tests/test_swing_calibration.py -q -p no:cacheprovider`
Expected: FAIL — `ImportError: cannot import name 'iv' from 'swing_trader'`.

- [ ] **Step 4: Write iv.py**

Create `backend/swing_trader/iv.py`:

```python
"""Daily implied-volatility snapshots, ported from ST iv_collector.py.

    iv_collector.py:67-103   snapshot_iv (Yahoo chain)            verbatim
    iv_collector.py:118-181  snapshot_iv_alpaca: the same 30-day ATM measure
                             from the broker adapter's option contracts and
                             indicative snapshots (plan A-live) instead of
                             ST's own clients
    iv_collector.py:232-257  run_iv_snapshot -> SwingIvSnapshots rows,
                             idempotent per ET day, bounded by the tick budget
    iv_collector.py:260-278  _load_iv_rank -> load_iv_rank; kept, and gating
                             nothing, as in ST
"""
from __future__ import annotations

import math
import time
from datetime import date, datetime, timedelta

import pandas as pd
import yfinance as yf

from swing_trader import clock
from swing_trader.constants import MIN_RANK_ROWS, RANK_WINDOW, TARGET_DTE, WHEEL_UNIVERSE
from swing_trader.refdata import IV_TABLE

try:
    from intellistock_logger import intellistock_logger as _ilog  # type: ignore

    def _log(msg, color="white"):
        _ilog.log(str(msg), color, service="StrategyWheel")
except Exception:  # pragma: no cover - standalone/test import
    def _log(msg, color="white"):
        print(f"[StrategyWheel] {msg}")


def snapshot_iv(symbol: str, *, today: date | None = None) -> float | None:
    """30-day ATM implied volatility for one symbol, or None on any failure.

    Picks the listed expiry nearest TARGET_DTE days out, takes the strike
    closest to spot on both the call and put side, and averages their IVs.
    """
    try:
        t = yf.Ticker(symbol)
        expiries = t.options
        if not expiries:
            return None

        today = today or clock.ny_now(None).date()
        def dte(exp: str) -> int:
            return abs((datetime.strptime(exp, "%Y-%m-%d").date() - today).days - TARGET_DTE)
        expiry = min(expiries, key=dte)

        hist = t.history(period="1d")
        if hist.empty:
            return None
        spot = float(hist["Close"].iloc[-1])

        chain = t.option_chain(expiry)
        ivs = []
        for side in (chain.calls, chain.puts):
            if side is None or side.empty or "impliedVolatility" not in side.columns:
                continue
            atm = side.loc[(side["strike"] - spot).abs().idxmin()]
            iv = atm["impliedVolatility"]
            if iv is not None and not pd.isna(iv) and float(iv) > 0:
                ivs.append(float(iv))
        if not ivs:
            return None
        return round(sum(ivs) / len(ivs), 4)
    except Exception as exc:
        _log(f"  [iv] {symbol}: snapshot failed — {exc}")
        return None


def snapshot_iv_alpaca(symbol: str, *, adapter, spot, today: date) -> float | None:
    """The same measure from Alpaca option snapshots (feed=indicative)."""
    try:
        if spot is None:
            return None
        spot = float(spot)
        contracts = list(adapter.get_option_contracts(
            symbol,
            expiration_gte=(today + timedelta(days=TARGET_DTE - 12)).isoformat(),
            expiration_lte=(today + timedelta(days=TARGET_DTE + 15)).isoformat(),
            strike_gte=round(spot * 0.93, 2),
            strike_lte=round(spot * 1.07, 2)) or [])
        if not contracts:
            return None

        expiry = min(
            sorted({str(c.expiration)[:10] for c in contracts}),
            key=lambda d: abs((date.fromisoformat(d) - today).days - TARGET_DTE),
        )
        # ATM contract per side (call/put) at the chosen expiry
        atm: dict[str, tuple[float, str]] = {}
        for c in contracts:
            if str(c.expiration)[:10] != expiry:
                continue
            side = str(c.option_type).lower()
            dist = abs(float(c.strike) - spot)
            if side not in atm or dist < atm[side][0]:
                atm[side] = (dist, c.symbol)
        contract_symbols = [sym for _, sym in atm.values()]
        if not contract_symbols:
            return None

        snapshots = adapter.get_option_snapshots(contract_symbols) or {}
        ivs = []
        for cs in contract_symbols:
            snap = snapshots.get(cs)
            iv = getattr(snap, "iv", None) if snap else None
            if iv is not None and math.isfinite(float(iv)) and float(iv) > 0:
                ivs.append(float(iv))
        if not ivs:
            return None
        return round(sum(ivs) / len(ivs), 4)
    except Exception as exc:
        _log(f"  [iv] {symbol}: alpaca snapshot failed — {exc}")
        return None


def iv_row_id(symbol: str, day) -> str:
    return f"{str(symbol).upper()}|{str(day)[:10]}"


def run_iv_snapshot(store, *, adapter, spot_for, today: date, symbols=None,
                    deadline=None, now_fn=time.monotonic) -> dict:
    """Snapshot IV for every wheel-universe symbol. Idempotent per ET day —
    symbols already recorded today are skipped, so re-runs are safe. Dual-write
    (R8-07): either leg may fail; a row is written if at least one succeeded.
    With a `deadline` it stops starting new symbols 10 s before it."""
    day = today.isoformat()
    recorded, skipped, failed = [], [], []
    complete = True
    for symbol in list(symbols or WHEEL_UNIVERSE):
        if store.get(IV_TABLE, iv_row_id(symbol, day)) is not None:
            skipped.append(symbol)
            continue
        if deadline is not None and clock.time_left(deadline, clock=now_fn) < 10.0:
            complete = False
            break
        iv_yahoo = snapshot_iv(symbol, today=today)
        iv_alpaca = (snapshot_iv_alpaca(symbol, adapter=adapter, spot=spot_for(symbol),
                                        today=today) if adapter is not None else None)
        if iv_yahoo is None and iv_alpaca is None:
            failed.append(symbol)
            continue
        store.insert(IV_TABLE, {"id": iv_row_id(symbol, day), "symbol": symbol.upper(),
                                "date": day, "iv30": iv_yahoo, "iv30_alpaca": iv_alpaca},
                     conflict="replace")
        recorded.append(symbol)
    _log(f"  [iv] {day}: {len(recorded)} recorded, {len(skipped)} already done, "
         f"{len(failed)} failed{' (' + ', '.join(failed) + ')' if failed else ''}")
    return {"date": day, "recorded": recorded, "skipped": skipped, "failed": failed,
            "complete": complete}


def load_iv_rank(store, symbol: str) -> float | None:
    """IV Rank 0–100 from the trailing RANK_WINDOW snapshots:
    (current − low) / (high − low) × 100.
    Returns None when history is too short (< MIN_RANK_ROWS) or degenerate."""
    sym = str(symbol).upper()
    rows = store.run(store.order_by(store.between(IV_TABLE, f"{sym}|", f"{sym}|~"),
                                    index="id"))
    ivs = [float(r["iv30"]) for r in rows if r.get("iv30") not in (None, "")]
    ivs = ivs[-RANK_WINDOW:]
    if len(ivs) < MIN_RANK_ROWS:
        return None
    lo, hi, current = min(ivs), max(ivs), ivs[-1]
    if hi <= lo:
        return None
    return round((current - lo) / (hi - lo) * 100, 1)
```

- [ ] **Step 5: Write calibration.py**

Create `backend/swing_trader/calibration.py`:

```python
"""AI conviction score vs outcome, ported from ST calibration.py (R4-04).

    calibration.py:28-47   _bucket_label, _score_of           verbatim
    calibration.py:50-82   swing_calibration over SwingSignals swing rows with
                           a closed outcome (ST: paper_trades.csv round trips)
    calibration.py:85-124  wheel_calibration over wheel rows with a premium
                           outcome (ST: wheel_trades.csv)
    calibration.py:127-137 calibration_report
Honesty rule (ST): a bucket with count < MIN_BUCKET_N is insufficient_n=True.
Outcomes are written by the lanes (record_outcomes) from the broker's closed
orders, so the record carries the FILL price (spec §9 fix 7).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

from swing_trader import clock, signals_store
from swing_trader.constants import BUCKETS, GATE_TRADES, MIN_BUCKET_N

try:
    from intellistock_logger import intellistock_logger as _ilog  # type: ignore

    def _log(msg, color="white"):
        _ilog.log(str(msg), color, service="SwingCalibration")
except Exception:  # pragma: no cover - standalone/test import
    def _log(msg, color="white"):
        print(f"[SwingCalibration] {msg}")

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _bucket_label(lo: int, hi: int) -> str:
    return f"{lo}-{hi}"


def _score_of(value) -> int | None:
    """Parse an ai_score/conviction_score cell. Blank/NaN/garbage → None."""
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    s = str(value).strip()
    if not s:
        return None
    try:
        return int(float(s))
    except ValueError:
        return None


def swing_calibration(rows) -> dict:
    """Bucket closed swing round trips by the signal's score."""
    matched = [r for r in (rows or []) if r.get("lane") == "swing"
               and isinstance(r.get("outcome"), dict) and r["outcome"].get("pnl") is not None]

    scored = []
    for t in matched:
        score = _score_of(t.get("score"))
        if score is not None:
            scored.append({**t["outcome"], "score": score})

    buckets = {}
    for lo, hi in BUCKETS:
        in_bucket = [t for t in scored if lo <= t["score"] <= hi]
        wins = [t for t in in_bucket if t["pnl"] > 0]
        buckets[_bucket_label(lo, hi)] = {
            "count":          len(in_bucket),
            "insufficient_n": len(in_bucket) < MIN_BUCKET_N,
            "win_rate":       round(len(wins) / len(in_bucket) * 100, 1) if in_bucket else None,
            "avg_pnl_pct":    round(sum(t["pnl_pct"] for t in in_bucket) / len(in_bucket), 2) if in_bucket else None,
            "total_pnl":      round(sum(t["pnl"] for t in in_bucket), 2),
        }

    return {
        "closed_total":  len(matched),
        "closed_scored": len(scored),
        "excluded_unscored": len(matched) - len(scored),
        "buckets":       buckets,
    }


def wheel_calibration(rows) -> dict:
    """Bucket wheel candidates by score; outcome = realized premium on placed
    orders. Assignment losses are out of scope until assignment history exists."""
    wheel_rows = [r for r in (rows or []) if r.get("lane") == "wheel"]
    if not wheel_rows:
        return {"placed_total": 0, "buckets": {}}

    placed = []
    for r in wheel_rows:
        score = _score_of(r.get("score"))
        out = r.get("outcome") or {}
        prem = out.get("premium_received")
        if score is None or prem in (None, ""):
            continue
        try:
            prem_per_share = float(prem)
        except (TypeError, ValueError):
            continue
        try:
            contracts = int(float(out.get("contracts") or 1))
        except (TypeError, ValueError):
            contracts = 1
        placed.append({"score": score,
                       "premium_dollars": prem_per_share * 100 * max(contracts, 1)})

    buckets = {}
    for lo, hi in BUCKETS:
        in_bucket = [t for t in placed if lo <= t["score"] <= hi]
        buckets[_bucket_label(lo, hi)] = {
            "count":              len(in_bucket),
            "insufficient_n":     len(in_bucket) < MIN_BUCKET_N,
            "total_premium":      round(sum(t["premium_dollars"] for t in in_bucket), 2),
            "avg_premium":        round(sum(t["premium_dollars"] for t in in_bucket) / len(in_bucket), 2) if in_bucket else None,
        }

    return {"placed_total": len(placed), "buckets": buckets}


def calibration_report(instance_id=None, *, rows=None) -> dict:
    if rows is None:
        rows = signals_store.all_signals(instance_id)
    swing = swing_calibration(rows)
    return {
        "swing": swing,
        "wheel": wheel_calibration(rows),
        "gate": {
            "closed_scored_trades": swing["closed_scored"],
            "required": GATE_TRADES,
            "met": swing["closed_scored"] >= GATE_TRADES,
        },
    }


# -- outcomes (spec §8 "Calibration reads SwingSignals joined to outcomes") ---

def _flatten(orders):
    for o in orders or []:
        yield o
        for leg in (getattr(o, "legs", ()) or ()):
            yield leg


def _at(o):
    return clock.as_utc(getattr(o, "submitted_at_utc", None)) or _EPOCH


def _filled(o) -> bool:
    return (str(getattr(o, "status", "")).lower() == "filled"
            and getattr(o, "filled_avg_price", None) not in (None, 0, ""))


def resolve_swing_outcome(signal: dict, orders):
    """The round trip behind a swing signal: the first filled BUY of the symbol
    from a day before the signal on, then the first filled SELL after it
    (bracket legs included)."""
    sym = str(signal.get("symbol") or "").upper()
    since = (clock.as_utc(signal.get("created_at")) or _EPOCH) - timedelta(days=1)
    mine = [o for o in _flatten(orders)
            if str(getattr(o, "symbol", "")).upper() == sym and _filled(o)]
    buys = sorted((o for o in mine if str(o.side).lower() == "buy" and _at(o) >= since),
                  key=_at)
    if not buys:
        return None
    entry = buys[0]
    sells = sorted((o for o in mine if str(o.side).lower() == "sell" and _at(o) >= _at(entry)),
                   key=_at)
    if not sells:
        return None
    exit_ = sells[0]
    buy_px, sell_px = float(entry.filled_avg_price), float(exit_.filled_avg_price)
    shares = float(getattr(entry, "filled_qty", 0) or getattr(entry, "qty", 0) or 0)
    if buy_px <= 0 or shares <= 0:
        return None
    return {"entry_price": round(buy_px, 4), "exit_price": round(sell_px, 4),
            "shares": shares, "pnl": round((sell_px - buy_px) * shares, 2),
            "pnl_pct": round((sell_px - buy_px) / buy_px * 100, 4),
            "exit_order_id": getattr(exit_, "broker_order_id", None),
            "exit_date": _at(exit_).date().isoformat()}


def _contract_of(signal: dict):
    return ((signal.get("submitted_order") or {}).get("contract")
            or (signal.get("proposal") or {}).get("contract"))


def resolve_wheel_outcome(signal: dict, orders):
    """The premium a wheel signal's put actually sold for."""
    contract = _contract_of(signal)
    if not contract:
        return None
    for o in _flatten(orders):
        if (str(getattr(o, "symbol", "")).upper() == str(contract).upper()
                and str(getattr(o, "side", "")).lower() == "sell" and _filled(o)):
            return {"premium_received": float(o.filled_avg_price),
                    "contracts": int(float(getattr(o, "filled_qty", 0) or getattr(o, "qty", 1) or 1)),
                    "order_id": getattr(o, "broker_order_id", None)}
    return None


def record_outcomes(instance_id, adapter, lane: str, *, held=None) -> int:
    """Write the outcome of every open swing or wheel signal the broker's
    closed orders can resolve. A swing symbol still held is still open.
    Never raises; returns the number of rows updated."""
    try:
        rows = [r for r in signals_store.all_signals(instance_id)
                if r.get("lane") == lane and r.get("status") in signals_store.OPEN_STATUSES
                and not r.get("outcome")]
        if lane == "swing" and held is not None:
            still_held = {str(s).upper() for s in held}
            rows = [r for r in rows if str(r.get("symbol")).upper() not in still_held]
        if not rows:
            return 0
        symbols = sorted({(r["symbol"] if lane == "swing" else _contract_of(r))
                          for r in rows} - {None})
        if not symbols:
            return 0
        after = min(str(r.get("created_at") or "") for r in rows)[:10]
        orders = list(adapter.list_closed_orders(symbols, after) or [])
        n = 0
        for r in rows:
            out = (resolve_swing_outcome(r, orders) if lane == "swing"
                   else resolve_wheel_outcome(r, orders))
            if out:
                signals_store.update_signal(r["id"], {"outcome": out})
                n += 1
        return n
    except Exception as exc:
        _log(f"outcome resolution skipped ({type(exc).__name__}: {exc})", "yellow")
        return 0
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python3 -m pytest backend/tests/test_swing_iv.py backend/tests/test_swing_calibration.py -q -p no:cacheprovider`
Expected: PASS (21 tests).

- [ ] **Step 7: Commit**

```bash
git add backend/swing_trader/iv.py backend/swing_trader/calibration.py backend/tests/test_swing_iv.py backend/tests/test_swing_calibration.py
git commit -m "feat(swing): port IV snapshots and score calibration

IV snapshots dual-write Yahoo and Alpaca into SwingIvSnapshots, once per
ET day and inside the tick budget; IV Rank is kept and gates nothing.
Calibration buckets SwingSignals by score against outcomes resolved from
the broker's closed orders at their fill prices (fix 7).

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016ztXjLWXu61Dpp4XMU2jsS"
```

---

### Task 16: `StrategySwing` — header, broker contract and the backtest path (B9, part 1)

**Files:**
- Create: `backend/swing_trader/account.py`
- Create: `backend/strategies/strategy_swing.py`
- Test: `backend/tests/test_strategy_swing_backtest.py`

**Interfaces:**
- Consumes: Tasks 2–7 (`constants.SWING_DEFAULTS`, `indicators.indicators_for_frames`, `signals`, `regime`, `clock`, `universe`, `sectors`, `refdata`, `market_data.frames_from_engine_bars`).
- Consumes (plan A-backtest, not needed by the tests): the simulator honours `bracket`, `whole_shares` and `fill_at_next_open` on `_nexus_position_sizes[sym]` (contract §4); the emulator exposes `get_trade_history()`, `pending_execution_symbols()`, `get_buying_power(prices=...)`.
- Produces:
  - `StrategySwing.run_once(symbols, prices, current_time, config, conditions, data=None, portfolio_emulator=None, strategy_cache=None, time_increment=None, mode=None, **kwargs) -> dict` returning contract §1's payload.
  - `swing_trader.account`: `spendable(emulator, prices) -> float`, `pending_symbols(emulator) -> set[str]`, `entry_price_from_trades(emulator, symbol) -> float | None` (Tasks 17–20 append the live readers to this module).
  - Module names later tasks and tests use: `DEFAULTS`, `INTENT_ENTRY = "swing_entry"`, `INTENT_DEFENSIVE = "swing_defensive_entry"`, `INTENT_EXIT = {"rsi_overbought": "swing_rsi_exit", "stop_loss": "swing_stop_exit", "profit_target": "swing_target_exit"}`, `swing_indicators(frames, cfg) -> dict` (the seam tests patch), `_emit(decisions, sizes, intents) -> dict`, cache keys `_BT_SESSION_KEY`, `_IND_MEMO_KEY`, `_BEAR_KEY`, `_SECTOR_MAP_KEY`, `_LOGGED_KEY`.

**The backtest rules (spec §5.1).** One decision per NY session, from daily bars strictly before it; VIX from `SwingMacroDaily` strictly before it; entry candidates restricted to point-in-time S&P members (visited in the live list's order) plus SPY/QQQ as ST's list does; sectors from `SwingSectorMap` only (no network in a backtest); the AI gate and the earnings block off, as in ST's backtester. Entries are `decision=1` with `{"buy_cash": min(equity × 0.125, buying power), "bracket": {"take_profit_price": round(close×1.09, 2), "stop_loss_price": round(close×0.94, 2)}, "whole_shares": True, "fill_at_next_open": True}` — the same absolute, prior-close-anchored legs live uses. ST's `exit_signal` runs on every held name (RSI cross, and the close-based −6%/+9% a gapped fill can leave a bracket short of) as `decision=-1` with `{"sell_fraction": 1.0, "fill_at_next_open": True}`. A missing membership row refuses entries (a backtest over today's list is survivorship-biased); a missing SPY series refuses the tick.

**Two engine facts this path relies on (plan A-backtest).** (a) Bracket-leg exits are never reported to the strategy: it learns a position closed only because the emulator no longer holds it. So exits, `max_positions` slots, the sector cap and the defensive-universe choice read `get_positions()` (plus `pending_execution_symbols()`) fresh every session and cache nothing about holdings. (b) The engine sizes a whole-share buy against the all-in fill price, so the filled share count can be one below `int(buy_cash / open)`; that is accepted, and the strategy does not compensate (the tests assert the emitted `buy_cash`, never a share count).

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_strategy_swing_backtest.py`:

```python
"""StrategySwing: header contract and the backtest path (spec §5.1)."""
import ast
import importlib.util
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

import pytest

# ONLY backend/ on the path; the wrapper is loaded by file path (adding
# backend/strategies/ would shadow strategy_x and friends).
_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from swing_trader.constants import SWING_DEFAULTS  # noqa: E402

PATH = os.path.join(_backend, "strategies", "strategy_swing.py")
TICK = datetime(2026, 6, 2, 12, 0, tzinfo=timezone.utc)      # 08:00 ET Tuesday
SESSION = "2026-06-02"


def _load():
    spec = importlib.util.spec_from_file_location("strategies.strategy_swing", PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def ind(close, rsi=45.0, rsi_prev=40.0, **kw):
    out = {"close": close, "volume": 2e6, "rsi": rsi, "rsi_prev": rsi_prev,
           "macd_hist": 0.2, "macd_hist_prev": 0.1, "macd_hist_prev2": 0.05,
           "sma200": close * 0.9, "vol_avg20": 1e6, "adx": 20.0}
    out.update(kw)
    return out


SPY = ind(450.0, rsi=60.0, sma200=400.0)


class BtEmulator:
    def __init__(self, cash=100_000.0, positions=None, trades=None, pending=()):
        self._cash = cash
        self._positions = dict(positions or {})
        self._trades = list(trades or [])
        self._pending = tuple(pending)

    def get_cash(self):
        return self._cash

    def get_buying_power(self, reserved=0.0, *, prices=None):
        return self._cash

    def get_positions(self):
        return dict(self._positions)

    def get_portfolio_value(self, prices):
        return self._cash + sum(q * float((prices or {}).get(s, 0.0))
                                for s, q in self._positions.items())

    def get_trade_history(self):
        return list(self._trades)

    def pending_execution_symbols(self):
        return self._pending


@pytest.fixture
def mod(store, monkeypatch):
    m = _load()
    monkeypatch.setattr(m, "store", store)
    store.insert("SwingMacroDaily", [
        {"id": "VIX|2026-05-29", "series": "VIX", "date": "2026-05-29", "close": 14.0, "source": "cboe"},
        {"id": "VIX|2026-06-01", "series": "VIX", "date": "2026-06-01", "close": 15.0, "source": "cboe"},
        {"id": "VIX|2026-06-02", "series": "VIX", "date": "2026-06-02", "close": 40.0, "source": "cboe"},
    ], conflict="replace")
    store.insert("SwingIndexMembership", [
        {"id": "SPX|2026-01-02", "index": "SPX", "date": "2026-01-02",
         "members": ["AAA", "BBB", "CCC", "XOM"]}], conflict="replace")
    store.insert("SwingSectorMap", [
        {"id": s, "symbol": s, "sector": sec, "as_of": "2026-09-24", "source": "yfinance"}
        for s, sec in (("AAA", "technology"), ("BBB", "technology"),
                       ("CCC", "energy"), ("ZZZ", "utilities"))], conflict="replace")
    return m


def cfg(**over):
    c = dict(SWING_DEFAULTS, strategy_swing_enabled=True)
    c.update(over)
    return c


def run(mod, monkeypatch, indicators, *, emu=None, cache=None, at=TICK, config=None,
        data=None):
    monkeypatch.setattr(mod, "swing_indicators", lambda frames, c: indicators)
    return mod.StrategySwing().run_once(
        sorted(indicators), {s: v["close"] for s, v in indicators.items()}, at,
        config or cfg(), {}, data=data if data is not None else {s: [] for s in indicators},
        portfolio_emulator=emu or BtEmulator(), strategy_cache=cache if cache is not None else {})


# -- header and broker contract ----------------------------------------------

def test_the_schema_header_is_exactly_the_defaults():
    header = re.search(r"# INTELLISTOCK_SCHEMA: (.*)", open(PATH).read())
    schema = json.loads(header.group(1))
    assert schema["strategy"] == "strategy_swing"
    assert schema["execution_scope"] == "run_once"
    assert schema["decision_phase"] == "pre"
    assert schema["execution_position"] == 10
    assert schema["config"] == SWING_DEFAULTS
    assert list(schema["config"]) == list(SWING_DEFAULTS)


def test_the_header_is_line_one_and_carries_a_description():
    from strategies_meta import _parse_header_meta
    text = open(PATH).read()
    assert text.startswith("# INTELLISTOCK_SCHEMA: ")
    schema, description = _parse_header_meta(text)
    assert schema["strategy"] == "strategy_swing" and "ST" in description


def test_the_class_name_matches_what_the_broker_derives():
    broker = os.path.join(_backend, "broker.py")
    tree = ast.parse(open(broker).read())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
              and n.name == "_strategy_name_to_module_and_class")
    ns = {"re": __import__("re")}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), broker, "exec"), ns)
    assert ns["_strategy_name_to_module_and_class"]("strategy_swing") == (
        "strategy_swing", "StrategySwing")
    assert hasattr(_load().StrategySwing, "run_once")


def test_disabled_or_blind_is_inert(mod, monkeypatch):
    assert run(mod, monkeypatch, {"SPY": SPY, "AAA": ind(100.0)},
               config=cfg(strategy_swing_enabled=False)) == {}
    out = mod.StrategySwing().run_once(["AAA"], {}, TICK, cfg(), {}, data={},
                                       portfolio_emulator=None, strategy_cache={})
    assert out == {}


# -- entries -----------------------------------------------------------------

def test_a_backtest_entry_is_a_prior_close_bracket_filled_at_the_next_open(mod, monkeypatch):
    out = run(mod, monkeypatch, {"SPY": SPY, "AAA": ind(100.0)})
    assert out["AAA"] == 1
    sizes = out["_nexus_position_sizes"]
    assert sizes["_cash_reserve_floor_pct"] == 0.0
    assert sizes["AAA"] == {"buy_cash": 12_500.0,
                            "bracket": {"take_profit_price": 109.0, "stop_loss_price": 94.0},
                            "whole_shares": True, "fill_at_next_open": True}
    assert out["_nexus_discovered"] == ["AAA"]
    assert out["_nexus_executable_buys"] == ["AAA"]
    assert out["_nexus_sell_enforcement"] == []
    assert out["_nexus_action_intents"] == {"AAA": "swing_entry"}


def test_the_sector_set_is_updated_after_each_buy(mod, monkeypatch):
    # fix 4: AAA and BBB are both technology; ST entered both in one run.
    out = run(mod, monkeypatch, {"SPY": SPY, "AAA": ind(100.0), "BBB": ind(50.0),
                                 "CCC": ind(80.0)})
    assert out["_nexus_executable_buys"] == ["AAA", "CCC"]


def test_only_point_in_time_members_are_entered(mod, monkeypatch):
    out = run(mod, monkeypatch, {"SPY": SPY, "ZZZ": ind(10.0), "CCC": ind(80.0)})
    assert out["_nexus_executable_buys"] == ["CCC"]


def test_without_a_membership_row_entries_are_refused(mod, monkeypatch, store):
    store.delete("SwingIndexMembership", "SPX|2026-01-02")
    lines = []
    monkeypatch.setattr(mod, "_log", lambda msg, color="white": lines.append(msg))
    assert run(mod, monkeypatch, {"SPY": SPY, "AAA": ind(100.0)}) == {}
    assert any("survivorship" in line for line in lines)


def test_the_vix_row_dated_the_session_is_invisible(mod, monkeypatch):
    # VIX|2026-06-02 = 40 would block; the rule reads 2026-06-01 (15).
    assert run(mod, monkeypatch, {"SPY": SPY, "AAA": ind(100.0)})["AAA"] == 1


def test_a_vix_gap_blocks_entries_and_logs_once(mod, monkeypatch):
    lines = []
    monkeypatch.setattr(mod, "_log", lambda msg, color="white": lines.append(msg))
    late = datetime(2026, 6, 12, 12, 0, tzinfo=timezone.utc)
    cache = {}
    assert run(mod, monkeypatch, {"SPY": SPY, "AAA": ind(100.0)}, at=late, cache=cache) == {}
    cache.pop(mod._BT_SESSION_KEY)          # force a second evaluation of the session
    run(mod, monkeypatch, {"SPY": SPY, "AAA": ind(100.0)}, at=late, cache=cache)
    assert sum("VIX unavailable" in line for line in lines) == 1


def test_slots_and_buying_power_are_sts(mod, monkeypatch):
    held = {f"H{i}": 10.0 for i in range(7)}
    out = run(mod, monkeypatch, {"SPY": SPY, "AAA": ind(100.0), "CCC": ind(80.0)},
              emu=BtEmulator(positions=held))
    assert out["_nexus_executable_buys"] == ["AAA"]            # 8 - 7 = one slot
    # Equity $100,000 (cash + a $95,000 position priced through `prices`) but
    # $5,000 buying power: below half of the $12,500 allocation, so ST skips.
    rich = BtEmulator(cash=5_000.0, positions={"H0": 950.0})
    indicators = {"SPY": SPY, "AAA": ind(100.0), "H0": ind(100.0, rsi=60.0)}
    assert run(mod, monkeypatch, indicators, emu=rich) == {}


def test_bear_mode_scans_the_defensive_universe_after_n_blocked_sessions(mod, monkeypatch):
    blocked_spy = ind(390.0, rsi=60.0, sma200=400.0)
    cache = {}
    days = [datetime(2026, 6, d, 12, 0, tzinfo=timezone.utc) for d in (2, 3)]
    indicators = {"SPY": blocked_spy, "XLP": ind(80.0), "AAA": ind(100.0)}
    assert run(mod, monkeypatch, indicators, at=days[0], cache=cache,
               config=cfg(bear_regime_days=2)) == {}
    out = run(mod, monkeypatch, indicators, at=days[1], cache=cache,
              config=cfg(bear_regime_days=2))
    assert cache[mod._BEAR_KEY]["blocked_days"] == 2
    assert out["_nexus_executable_buys"] == ["XLP"]
    assert out["_nexus_action_intents"] == {"XLP": "swing_defensive_entry"}


def test_one_decision_per_session(mod, monkeypatch):
    cache = {}
    assert run(mod, monkeypatch, {"SPY": SPY, "AAA": ind(100.0)}, cache=cache)["AAA"] == 1
    later = TICK + timedelta(hours=2)
    assert run(mod, monkeypatch, {"SPY": SPY, "AAA": ind(100.0)}, cache=cache, at=later) == {}


def test_the_ai_gate_and_earnings_are_off_in_backtests(mod, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("no AI or earnings call in a backtest")

    monkeypatch.setattr(mod.ai_analyst, "analyse", boom)
    monkeypatch.setattr(mod.ai_analyst, "days_until_earnings", boom)
    assert run(mod, monkeypatch, {"SPY": SPY, "AAA": ind(100.0)})["AAA"] == 1


# -- exits -------------------------------------------------------------------

def test_the_rsi_cross_and_close_stop_exits_sell_at_the_next_open(mod, monkeypatch):
    emu = BtEmulator(positions={"AAA": 10.0, "CCC": 5.0},
                     trades=[{"action": "buy", "ticker": "AAA", "price": 100.0},
                             {"action": "buy", "ticker": "CCC", "price": 100.0}])
    out = run(mod, monkeypatch, {"SPY": SPY,
                                 "AAA": ind(104.0, rsi=72.0, rsi_prev=68.0),
                                 "CCC": ind(93.0, rsi=40.0, rsi_prev=45.0)}, emu=emu)
    assert out["AAA"] == -1 and out["CCC"] == -1
    assert out["_nexus_position_sizes"]["AAA"] == {"sell_fraction": 1.0, "fill_at_next_open": True}
    assert out["_nexus_action_intents"] == {"AAA": "swing_rsi_exit", "CCC": "swing_stop_exit"}
    assert out["_nexus_sell_enforcement"] == ["AAA", "CCC"]


# -- review focus 1: NaN close on the scan day ---------------------------------

def test_a_nan_close_on_the_last_bar_uses_the_previous_session(mod, store):
    start = datetime(2025, 5, 1, 4, 0, tzinfo=timezone.utc)
    bars = []
    for i in range(260):
        c = 100.0 + (i % 7) - 3 + i * 0.1
        bars.append({"t": (start + timedelta(days=i)).isoformat(), "o": c, "h": c + 1,
                     "l": c - 1, "c": c, "v": 1_000_000})
    last_good = bars[-2]["c"]
    bars[-1]["c"] = None
    day_after = datetime.fromisoformat(bars[-1]["t"]) + timedelta(days=1, hours=8)
    cache = {}
    mod.StrategySwing().run_once(["AAA"], {}, day_after, cfg(), {},
                                 data={"AAA": bars, "SPY": bars},
                                 portfolio_emulator=BtEmulator(), strategy_cache=cache)
    memo = cache[mod._IND_MEMO_KEY]["ind"]
    assert memo["AAA"]["close"] == last_good
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest backend/tests/test_strategy_swing_backtest.py -q -p no:cacheprovider`
Expected: FAIL — `FileNotFoundError` for `backend/strategies/strategy_swing.py`.

- [ ] **Step 3: Write the wrapper**

Create `backend/swing_trader/account.py` (the broker-facing readers both wrappers share; Tasks 17 and 18 append to it):

```python
"""Read-only views of the book, shared by both lanes.

Platform glue with no ST source. Every reader tolerates an emulator or adapter
that lacks the method: it degrades to "no data" — which the callers treat as
"do not add exposure" — and never raises into run_once.
"""
from __future__ import annotations


def spendable(emulator, prices) -> float:
    """Buying power when the emulator offers it, else settled cash."""
    try:
        bp = emulator.get_buying_power(prices=prices)
    except (AttributeError, TypeError):
        bp = None
    if bp is None:
        return float(emulator.get_cash() or 0.0)
    return float(bp or 0.0)


def pending_symbols(emulator) -> set:
    try:
        return {str(s).upper() for s in (emulator.pending_execution_symbols() or ())}
    except Exception:
        return set()


def entry_price_from_trades(emulator, symbol):
    """The fill price of the latest buy of `symbol` (ST read Alpaca's
    avg_entry_price; one swing position is one buy)."""
    try:
        trades = emulator.get_trade_history() or []
    except Exception:
        return None
    for t in reversed(trades):
        if not isinstance(t, dict):
            continue
        if (str(t.get("ticker") or t.get("symbol") or "").upper() == str(symbol).upper()
                and str(t.get("action") or t.get("side") or "").lower() == "buy"):
            try:
                return float(t.get("price"))
            except (TypeError, ValueError):
                return None
    return None
```

Create `backend/strategies/strategy_swing.py`. Line 1 is the header exactly as `scripts/strategy_swing_sync_schema.py` (Task 24) writes it; line 2 the description:

```python
# INTELLISTOCK_SCHEMA: {"strategy": "strategy_swing", "weight": 1.0, "execution_position": 10, "decision_phase": "pre", "execution_scope": "run_once", "conditions": {}, "config": {"strategy_swing_enabled": false, "rsi_period": 14, "rsi_entry_max": 50, "rsi_overbought": 70, "sma_long": 200, "macd_fast": 12, "macd_slow": 26, "macd_signal": 9, "vol_avg_period": 20, "adx_period": 14, "adx_min": 15, "spy_buffer": 1.03, "vix_max": 25.0, "position_size_pct": 0.125, "max_positions": 8, "max_per_sector": 1, "profit_target": 0.09, "stop_loss": 0.06, "bear_regime_days": 10, "defensive_universe": ["XLP", "XLU", "XLV", "GLD", "SHY"], "earnings_hard_block_days": 5, "ai_gate_enabled": true, "ai_approve_threshold": 75, "ai_review_threshold": 50, "conviction_llm_model_id": "", "scan_time_et": "09:15", "live_max_order_fraction": 0.2, "live_max_symbol_fraction": 0.2, "live_max_leveraged_fraction": 0.2, "live_soft_drawdown": 0.25, "live_hard_drawdown": 0.35, "live_kill_drawdown": 0.45, "honour_single_position_cap": true, "broker_max_single_position_pct": 0.2}}
# INTELLISTOCK_DESCRIPTION: ST's swing strategy (github.com/tmasters2876/swing-trader), ported verbatim: buy an S&P 500 name when RSI(14) is under 50 and rising, the MACD histogram improves, volume beats its 20-day average, price is above its 200-day SMA and ADX is over 15 — while SPY is above its SMA200 × 1.03 and VIX is at most 25, else the defensive ETFs after 10 blocked sessions. 12.5% of equity per name, 8 names, 1 per sector, a −6%/+9% bracket anchored on the prior close, and an RSI-70 cross exit. Live, the linked conviction model scores each candidate: 75+ enters, 50–74 waits for your approval.
"""Strategy Swing wrapper: the swing lane of the swing-trader port.

ST's pure logic lives in backend/swing_trader/. This file owns what needs the
broker: the emulator (or live adapter), the strategy cache, the resumable
live scan, and the decision payload (interface contract §1).

Spec: docs/superpowers/specs/2026-09-24-swing-trader-port-design.md §5.1
"""
import os
import sys
from datetime import date, datetime, timezone

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from db import store  # noqa: E402  (tests monkeypatch this name)
from swing_trader import (  # noqa: E402
    account,
    ai_analyst,
    calibration,
    clock,
    indicators,
    market_data,
    notify,
    refdata,
    regime,
    sectors,
    signals,
    signals_store,
    universe,
)
from swing_trader.constants import SWING_DEFAULTS as DEFAULTS  # noqa: E402

# Route through intellistock_logger: the backtest engine discards container
# stdout on success, and BacktestResults.logs is the log an operator reads.
try:
    from intellistock_logger import intellistock_logger as _ilog  # type: ignore

    def _log(msg, color="white"):
        _ilog.log(str(msg), color, service="StrategySwing")
except Exception:  # pragma: no cover - standalone/test import
    def _log(msg, color="white"):
        print(f"[StrategySwing] {msg}")


INTENT_ENTRY = "swing_entry"
INTENT_DEFENSIVE = "swing_defensive_entry"
INTENT_EXIT = {"rsi_overbought": "swing_rsi_exit", "stop_loss": "swing_stop_exit",
               "profit_target": "swing_target_exit"}

#: Every cache key this wrapper owns carries the `_swing_` prefix.
_BT_SESSION_KEY = "_swing_bt_session"          # backtest: the session decided
_IND_MEMO_KEY = "_swing_ind_memo"              # backtest: indicators of that session
_BEAR_KEY = "_swing_bear"                      # {"session", "blocked_days"} (fix 11)
_SECTOR_MAP_KEY = "_swing_sector_map"          # backtest: SwingSectorMap, read once
_LOGGED_KEY = "_swing_logged"                  # {reason: scope} for _log_once


def _log_once(cache, reason, scope, msg, color="white"):
    """Log a standing condition at most once per `scope` (a session)."""
    seen = cache.get(_LOGGED_KEY)
    if not isinstance(seen, dict):
        seen = {}
        cache[_LOGGED_KEY] = seen
    if seen.get(reason) == scope:
        return
    seen[reason] = scope
    _log(msg, color)


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _list(value) -> list:
    if isinstance(value, str):
        return [s.strip().upper() for s in value.split(",") if s.strip()]
    return [str(s).strip().upper() for s in (value or []) if str(s).strip()]


def swing_indicators(frames, cfg) -> dict:
    """ST's get_latest_indicators over {symbol: daily frame}. A module-level
    seam so the wrapper tests can hand in exact indicator snapshots."""
    return indicators.indicators_for_frames(frames, cfg)


def _emit(decisions, sizes, intents) -> dict:
    """The broker payload (contract §1). Nothing to do is {}, as EB returns."""
    if not decisions:
        return {}
    out = dict(decisions)
    sizes = dict(sizes)
    # The broker's buy gate otherwise reserves `_cash_reserve_floor_pct`
    # (default 0.10) of starting value as untouchable cash, sized for a
    # many-name discovery book. The swing lane's own sizing is ST's: 12.5% of
    # equity per name and a half-allocation buying-power check.
    sizes["_cash_reserve_floor_pct"] = 0.0
    out["_nexus_position_sizes"] = sizes
    out["_nexus_discovered"] = sorted(decisions)
    out["_nexus_executable_buys"] = sorted(s for s, d in decisions.items() if d == 1)
    out["_nexus_sell_enforcement"] = sorted(s for s, d in decisions.items() if d == -1)
    out["_nexus_action_intents"] = {s: intents[s] for s in sorted(decisions) if s in intents}
    return out


def _exits(ind, positions, entry_of, cfg, decisions, sizes, intents) -> dict:
    """paper_trader.py:536-587: ST's exit_signal on every held name with data.
    Returns {symbol: reason} for the exits emitted."""
    out = {}
    kw = signals.exit_kwargs(cfg)
    for symbol in sorted(positions):
        i = ind.get(symbol)
        if not i:
            continue
        entry = entry_of(symbol)
        if not entry or entry <= 0:
            continue
        should_exit, reason = signals.exit_signal(i, float(entry), **kw)
        if should_exit:
            decisions[symbol] = -1
            sizes[symbol] = {"sell_fraction": 1.0, "fill_at_next_open": True}
            intents[symbol] = INTENT_EXIT[reason]
            out[symbol] = reason
    return out


class StrategySwing:
    # The class name is NOT free: broker.py resolves a run-once strategy by
    # CamelCasing its id — strategy_swing -> StrategySwing — and runs the whole
    # backtest inert when it misses.

    def run_once(self, symbols, prices, current_time, config, conditions,
                 data=None, portfolio_emulator=None, strategy_cache=None,
                 time_increment=None, mode=None, **kwargs):
        cfg = {**DEFAULTS, **(config or {})}
        if not _truthy(cfg.get("strategy_swing_enabled", False)):
            return {}
        cache = strategy_cache if isinstance(strategy_cache, dict) else {}
        if portfolio_emulator is None:
            _log_once(cache, "no-emulator", str(current_time)[:10],
                      "StrategySwing: REFUSING to trade — no portfolio emulator, so "
                      "the book cannot be read at all.", "red")
            return {}
        if data is not None:
            return self._backtest(prices, current_time, cfg, data, portfolio_emulator, cache)
        return self._live(prices, current_time, cfg, portfolio_emulator, cache, mode)

    # -- backtest ------------------------------------------------------------

    def _sector_map(self, cache, names) -> dict:
        smap = cache.get(_SECTOR_MAP_KEY)
        if not isinstance(smap, dict):
            try:
                smap = refdata.sector_map(store, names)
            except Exception as exc:
                _log(f"StrategySwing: SwingSectorMap unreadable ({type(exc).__name__}: "
                     f"{exc}) — every sector reads 'unknown'", "yellow")
                smap = {}
            cache[_SECTOR_MAP_KEY] = smap
        return smap

    def _backtest(self, prices, current_time, cfg, data, emu, cache):
        session = clock.ny_date(current_time)
        if cache.get(_BT_SESSION_KEY) == session:
            return {}
        names = sorted(str(s).upper() for s in data) if isinstance(data, dict) else []

        memo = cache.get(_IND_MEMO_KEY)
        if isinstance(memo, dict) and memo.get("session") == session:
            ind = memo["ind"]
        else:
            frames = market_data.frames_from_engine_bars(data, names, current_time)
            ind = swing_indicators(frames, cfg)
            cache[_IND_MEMO_KEY] = {"session": session, "ind": ind}

        spy = ind.get("SPY")
        if not spy:
            _log_once(cache, "no-spy", session,
                      f"StrategySwing {session} | REFUSING to trade — no SPY daily bars "
                      "before this session, so the regime cannot be read.", "red")
            return {}

        try:
            vix, vix_reason = refdata.vix_before(store, session)
        except Exception as exc:
            vix, vix_reason = None, f"SwingMacroDaily unreadable: {type(exc).__name__}: {exc}"
        if vix_reason:
            _log_once(cache, "vix", session,
                      f"StrategySwing {session} | VIX unavailable ({vix_reason}) — "
                      "regime blocked, as a missing VIX blocked ST.", "yellow")
        reg = regime.regime_decision(spy.get("close"), spy.get("sma200"), vix,
                                     spy_buffer=float(cfg["spy_buffer"]),
                                     vix_max=float(cfg["vix_max"]))
        bear = signals.update_regime_tracker(cache.get(_BEAR_KEY), reg["regime_ok"], session)
        cache[_BEAR_KEY] = bear

        decisions, sizes, intents = {}, {}, {}
        positions = {str(s).upper(): float(q or 0.0)
                     for s, q in (emu.get_positions() or {}).items() if float(q or 0.0) > 0}
        exited = _exits(ind, positions, lambda s: account.entry_price_from_trades(emu, s), cfg,
                        decisions, sizes, intents)

        try:
            members = refdata.members_before(store, session)
        except Exception as exc:
            _log(f"StrategySwing {session} | SwingIndexMembership unreadable "
                 f"({type(exc).__name__}: {exc})", "red")
            members = None
        univ, phase = signals.select_entry_universe(
            reg["regime_ok"], bear["blocked_days"],
            bear_regime_days=int(cfg["bear_regime_days"]),
            live_universe=universe.order_like_live(list(members or []) + ["SPY", "QQQ"]),
            defensive_universe=_list(cfg["defensive_universe"]))
        if phase == "regime_ok" and members is None:
            _log_once(cache, "no-membership", session,
                      f"StrategySwing {session} | REFUSING entries — no SwingIndexMembership "
                      "row before this session; entering off today's list would be "
                      "survivorship-biased. Run scripts/build_swing_reference_data.py.", "red")
            univ = None

        if univ:
            smap = self._sector_map(cache, names)
            eff = {str(s).upper(): v for s, v in (prices or {}).items()}
            for s, i in ind.items():
                if float(eff.get(s) or 0.0) <= 0:
                    eff[s] = i["close"]
            equity = float(emu.get_portfolio_value(eff) or 0.0)
            bp = account.spendable(emu, eff)
            active = (set(positions) | account.pending_symbols(emu)) - set(exited)
            self._entries(univ, ind, active, cfg, equity, bp,
                          lambda s: sectors.get_symbol_sector(s, smap), phase,
                          decisions, sizes, intents)

        cache[_BT_SESSION_KEY] = session
        if decisions:
            _log(f"StrategySwing {session} | regime "
                 f"{'ACTIVE' if reg['regime_ok'] else 'BLOCKED'} ({phase}) | "
                 f"entries={sorted(s for s, d in decisions.items() if d == 1)} "
                 f"exits={sorted(s for s, d in decisions.items() if d == -1)}", "cyan")
        return _emit(decisions, sizes, intents)

    def _entries(self, univ, ind, active, cfg, equity, bp, sector_of, phase,
                 decisions, sizes, intents):
        """paper_trader.py:626-756 without the AI gate or the earnings block
        (ST's backtester ran neither)."""
        entries_placed = 0
        available_slots = int(cfg["max_positions"]) - len(active)
        ekw = signals.entry_kwargs(cfg)
        size_pct = float(cfg["position_size_pct"])
        stop_loss, profit_target = float(cfg["stop_loss"]), float(cfg["profit_target"])
        for symbol in univ:
            if symbol in active or symbol not in ind:
                continue
            i = ind[symbol]
            if not signals.entry_signal(i, **ekw):
                continue
            if signals.sector_conflict(symbol, active, sector_of=sector_of,
                                       max_per_sector=int(cfg["max_per_sector"])):
                continue
            if entries_placed >= available_slots:
                continue
            alloc = equity * size_pct
            if bp < alloc * 0.5:
                continue
            use = min(alloc, bp)
            shares = int(use / i["close"])
            if shares < 1:
                continue
            decisions[symbol] = 1
            sizes[symbol] = {
                "buy_cash": round(use, 2),
                "bracket": {"take_profit_price": round(i["close"] * (1 + profit_target), 2),
                            "stop_loss_price": round(i["close"] * (1 - stop_loss), 2)},
                "whole_shares": True,
                "fill_at_next_open": True,
            }
            intents[symbol] = INTENT_DEFENSIVE if phase == "bear_mode" else INTENT_ENTRY
            bp -= shares * i["close"]
            entries_placed += 1
            active.add(symbol)               # fix 4: the sector set follows each buy

    # -- live (Task 17) --------------------------------------------------------

    def _live(self, prices, current_time, cfg, emu, cache, mode):
        _log_once(cache, "live-not-built", clock.ny_date(current_time),
                  "StrategySwing: the live path is not built yet (plan B Task 17).", "red")
        return {}
```

Generate the header line with the sync logic instead of typing it: run `python3 - <<'EOF'` with

```python
import json, sys
sys.path.insert(0, "backend")
from swing_trader.constants import SWING_DEFAULTS
print("# INTELLISTOCK_SCHEMA: " + json.dumps({
    "strategy": "strategy_swing", "weight": 1.0, "execution_position": 10,
    "decision_phase": "pre", "execution_scope": "run_once", "conditions": {},
    "config": SWING_DEFAULTS}))
```

and paste its single output line as line 1. It must equal:

```text
# INTELLISTOCK_SCHEMA: {"strategy": "strategy_swing", "weight": 1.0, "execution_position": 10, "decision_phase": "pre", "execution_scope": "run_once", "conditions": {}, "config": {"strategy_swing_enabled": false, "rsi_period": 14, "rsi_entry_max": 50, "rsi_overbought": 70, "sma_long": 200, "macd_fast": 12, "macd_slow": 26, "macd_signal": 9, "vol_avg_period": 20, "adx_period": 14, "adx_min": 15, "spy_buffer": 1.03, "vix_max": 25.0, "position_size_pct": 0.125, "max_positions": 8, "max_per_sector": 1, "profit_target": 0.09, "stop_loss": 0.06, "bear_regime_days": 10, "defensive_universe": ["XLP", "XLU", "XLV", "GLD", "SHY"], "earnings_hard_block_days": 5, "ai_gate_enabled": true, "ai_approve_threshold": 75, "ai_review_threshold": 50, "conviction_llm_model_id": "", "scan_time_et": "09:15", "live_max_order_fraction": 0.2, "live_max_symbol_fraction": 0.2, "live_max_leveraged_fraction": 0.2, "live_soft_drawdown": 0.25, "live_hard_drawdown": 0.35, "live_kill_drawdown": 0.45, "honour_single_position_cap": true, "broker_max_single_position_pct": 0.2}}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 -m pytest backend/tests/test_strategy_swing_backtest.py -q -p no:cacheprovider`
Expected: PASS (16 tests).

Also run the neighbouring header tests to prove nothing else broke: `python3 -m pytest backend/tests/test_strategy_eb_run_once.py backend/tests/test_self_learning_levers.py -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Commit**

Run `gitnexus_detect_changes()`; expect new files only.

```bash
git add backend/swing_trader/account.py backend/strategies/strategy_swing.py backend/tests/test_strategy_swing_backtest.py
git commit -m "feat(swing): StrategySwing header and backtest path

One decision per NY session from bars, VIX and S&P membership strictly
before it. Entries are prior-close-anchored brackets filled at the next
open; ST's exit_signal sells at the next open. The AI gate and earnings
block stay off, as in ST's backtester.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016ztXjLWXu61Dpp4XMU2jsS"
```

---

### Task 17: `StrategySwing` live path — the resumable 09:15 scan (B9, part 2)

**Files:**
- Modify: `backend/swing_trader/account.py` (append the live readers)
- Modify: `backend/strategies/strategy_swing.py` (add cache keys and `_yf_symbol`; replace the `_live` stub)
- Test: `backend/tests/test_strategy_swing_live.py`

**Interfaces:**
- Consumes: Task 16's module; `market_data.data_client/get_daily_bars/LIVE_WINDOW_DAYS` (Task 7); `regime.fetch_vix_close` (Task 4); `ai_analyst.llm_role_from_config/analyse/days_until_earnings` (Task 13); `signals_store` (Task 14); `calibration.record_outcomes` (Task 15); `notify.send` (Task 12); `clock.tick_deadline/time_left/is_rth/is_trading_day/at_or_after` (Task 5).
- Consumes (live adapter; plan A-live where marked): `get_positions()`, `refresh_positions() -> list[PositionDTO]`, `refresh_account().equity`, `refresh_cash().buying_power`, `list_open_orders()`, `list_closed_orders(symbols, after)` (A-live), `list_option_positions()` (A-live). Every read is wrapped: an adapter without the method degrades to "no data", never to a duplicate order.
- Consumes (plan A-live, for the orders to execute): bracket intents from `_nexus_position_sizes[sym]["bracket"]` with `whole_shares`, sized by the engine as `floor(buy_cash / live price)` (it reads no `qty` from the hint) and submitted as a GTC market bracket that Alpaca queues for the open; a REST mark for every emitted symbol; `strategy_swing` in `_LANE_ENABLE_FLAGS` with `defaults_by_lane` → `SWING_DEFAULTS`. A-live contract addition 15: the engine never retries an exit it deferred (a bracket-leg cancel that did not confirm in 10 s, or a sell that floored to zero), so the lane re-emits it.
- Produces: `account.equity_positions(emu) -> dict[str, dict]`, `account.option_symbols(emu) -> set[str]`, `account.live_equity(emu, prices=None) -> float`, `account.live_buying_power(emu) -> float`; cache keys `_SCAN_KEY = "_swing_scan"`, `_SCAN_DONE_KEY = "_swing_scan_done_session"`, `_EMITTED_KEY = "_swing_emitted"`, `_NO_MODEL_KEY = "_swing_no_model_session"`, `_SECTOR_CACHE_KEY = "_swing_sector_cache"`, `_PENDING_EXIT_KEY = "_swing_pending_exits"` (`{symbol: {"reason", "intent", "since"}}`); module helper `_working_exit(order) -> bool`; methods `StrategySwing._reemit_exits(...)`; `SwingSignals` rows with statuses `auto_approved`, `pending`, `ai_rejected`; notifications `swing_entry`, `swing_pending_review`, `swing_exit`, `swing_run_summary`, `strategy_error`.

**The live rules (spec §5.1, §8, §9).** The scan starts at the first tick at or after `scan_time_et` on an NYSE trading day (09:20 on the broker's grid), fetches its own bars for today's S&P list plus the defensive ETFs, reads VIX and earnings from yfinance as ST did, and runs ST's paper_trader order: exits first, then the bear counter (per session, fix 11), then the entry pass. Each candidate passes ST's gates (sector — updated after each buy, fix 4 —, slots, half-allocation buying power, whole shares, the <5-day earnings hard block), then the AI gate: 75+ enters with `{"buy_cash": max(min(alloc, bp) × size_adjustment, close), "bracket": prior-close legs, "whole_shares": True, "fill_at_next_open": True}` — `alloc = equity × 0.125`, and the floor of one share at the prior close is ST's `max(1, …)`. The engine buys `floor(buy_cash / live price)` shares, so a live price above the prior close can buy one share fewer than ST's `max(1, int(int(min(alloc, bp) / close) × size_adjustment))`; that difference is accepted, not compensated. The ST count is still what the `SwingSignals` proposal and the push report; 50–74 becomes a `pending` row and a push; below 50 an `ai_rejected` row. One AI error skips its candidate (fix 5). With `ai_gate_enabled` and no model linked, no entry is placed and one `strategy_error` alert goes out per session. Candidates are scored inside the shared tick budget and the scan resumes next tick; the session latch is stamped only when the queue is exhausted. An entry emitted before the open that left no trace at the broker (no working order, no order since the session began, no position) is re-emitted once at the first regular-hours tick — the gate's `quote.stale` refusal the spec names. Bracket-leg fills are never reported to the lane; a closed position is simply absent from `refresh_positions()` on the next scan, which is the only place slots, sectors and exits are counted from.

**Deferred exits (A-live contract addition 15).** ST placed its RSI/close exit as a market sell that always executed. Here the engine may defer it — it must first cancel the bracket's legs and waits at most 10 s for Alpaca to confirm — and it never retries. So every exit the scan emits is also written to `_PENDING_EXIT_KEY`, and every later tick (any session) re-emits `-1` for each pending symbol that is still held and has no working non-bracket sell order. A bracket leg is not a working exit: it is the thing being cancelled. The entry is dropped the first tick the position is gone (the exit filled, or a leg did). An unreadable book re-emits nothing that tick; the gate's reduce-only rule is the backstop against a duplicate sell, never this lane's reason to fire blind.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_strategy_swing_live.py`:

```python
"""StrategySwing live path: the resumable 09:15 scan (spec §5.1, §8, §9)."""
import importlib.util
import os
import sys
from datetime import datetime, timezone
from types import SimpleNamespace as NS

import pytest

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from swing_trader import clock, signals_store  # noqa: E402
from swing_trader.constants import SWING_DEFAULTS  # noqa: E402

PATH = os.path.join(_backend, "strategies", "strategy_swing.py")
MON_0920 = datetime(2026, 6, 1, 13, 20, tzinfo=timezone.utc)
MON_0940 = datetime(2026, 6, 1, 13, 40, tzinfo=timezone.utc)
MON_1000 = datetime(2026, 6, 1, 14, 0, tzinfo=timezone.utc)
TUE_0920 = datetime(2026, 6, 2, 13, 20, tzinfo=timezone.utc)


def _load():
    spec = importlib.util.spec_from_file_location("strategies.strategy_swing", PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def ind(close, rsi=45.0, rsi_prev=40.0, **kw):
    out = {"close": close, "volume": 2e6, "rsi": rsi, "rsi_prev": rsi_prev,
           "macd_hist": 0.2, "macd_hist_prev": 0.1, "macd_hist_prev2": 0.05,
           "sma200": close * 0.9, "vol_avg20": 1e6, "adx": 20.0}
    out.update(kw)
    return out


IND = {"SPY": ind(450.0, rsi=60.0, sma200=400.0), "AAA": ind(100.0), "BBB": ind(50.0),
       "CCC": ind(80.0), "DDD": ind(20.0)}
APPROVE = {"conviction_score": 80, "recommendation": "approve", "reasoning": "ok",
           "position_size_adjustment": 1.0, "key_risks": []}
REVIEW = {"conviction_score": 60, "recommendation": "review", "reasoning": "meh",
          "position_size_adjustment": 0.5, "key_risks": ["x"]}
REJECT = {"conviction_score": 30, "recommendation": "reject", "reasoning": "no",
          "position_size_adjustment": 1.0, "key_risks": []}


class LiveAdapter:
    def __init__(self, positions=None, equity=100_000.0, bp=200_000.0,
                 open_orders=(), closed=(), options=()):
        self.pos = dict(positions or {})       # symbol -> (qty, avg_entry, market_value)
        self.equity, self.bp = equity, bp
        self.open_orders, self.closed, self.options = list(open_orders), list(closed), list(options)
        self.closed_calls = []

    def get_positions(self):
        return {s: v[0] for s, v in self.pos.items()}

    def refresh_positions(self):
        return [NS(symbol=s, qty=v[0], avg_entry_price=v[1], market_value=v[2])
                for s, v in self.pos.items()]

    def refresh_account(self):
        return NS(equity=self.equity)

    def refresh_cash(self):
        return NS(cash=self.bp, buying_power=self.bp)

    def get_cash(self):
        return self.bp

    def get_portfolio_value(self, prices=None):
        return self.equity

    def list_option_positions(self):
        return list(self.options)

    def list_open_orders(self, limit=200):
        return list(self.open_orders)

    def list_closed_orders(self, symbols, after):
        self.closed_calls.append((list(symbols), after))
        return list(self.closed)

    def get_trade_history(self):
        return []


def scripted(results):
    calls = []

    def analyse(signal, **kw):
        calls.append(signal["symbol"])
        r = results[signal["symbol"]]
        if isinstance(r, Exception):
            raise r
        return dict(r, symbol=signal["symbol"])

    analyse.calls = calls
    return analyse


@pytest.fixture
def live(store, monkeypatch):
    m = _load()
    monkeypatch.setattr(m, "store", store)
    monkeypatch.setattr(signals_store, "store", store)
    store.insert("SwingSectorMap", [
        {"id": s, "symbol": s, "sector": sec, "as_of": "2026-09-24", "source": "yfinance"}
        for s, sec in (("AAA", "technology"), ("BBB", "technology"),
                       ("CCC", "energy"), ("DDD", "utilities"))], conflict="replace")
    monkeypatch.setattr(m.market_data, "data_client", lambda k, s: object())
    monkeypatch.setattr(m.market_data, "get_daily_bars", lambda syms, days, client: {})
    monkeypatch.setattr(m.universe, "get_sp500_symbols",
                        lambda: ["AAA", "BBB", "CCC", "DDD", "SPY", "QQQ"])
    monkeypatch.setattr(m.regime, "fetch_vix_close", lambda: 15.0)
    monkeypatch.setattr(m.ai_analyst, "days_until_earnings", lambda s: None)
    monkeypatch.setattr(m.calibration, "record_outcomes", lambda *a, **k: 0)
    monkeypatch.setattr(m.clock, "is_trading_day", lambda d: True)
    monkeypatch.setattr(m, "swing_indicators", lambda frames, c: dict(IND))
    sent = []
    monkeypatch.setattr(m.notify, "send",
                        lambda cat, iid, title, msg, priority=0: sent.append((cat, title)))
    m.sent = sent
    clock._DEADLINES.clear()
    return m


def cfg(**over):
    c = dict(SWING_DEFAULTS, strategy_swing_enabled=True, instance_id="swing-paper",
             alpaca_key="k", alpaca_secret="s", conviction_llm_provider="claude-cli",
             conviction_llm_model="claude-sonnet-4-6", conviction_llm_api_key="")
    c.update(over)
    return c


def tick(m, at, adapter, cache, config=None, mode="MONITOR"):
    return m.StrategySwing().run_once(["SPY"], {}, at, config or cfg(), {}, data=None,
                                      portfolio_emulator=adapter, strategy_cache=cache,
                                      mode=mode)


def rows():
    return {r["symbol"]: r for r in signals_store.all_signals("swing-paper")}


def test_the_0920_scan_emits_brackets_and_records_every_score(live, monkeypatch):
    ai = scripted({"AAA": APPROVE, "CCC": REVIEW, "DDD": REJECT})
    monkeypatch.setattr(live.ai_analyst, "analyse", ai)
    cache = {}
    out = tick(live, MON_0920, LiveAdapter(), cache)
    assert {s: d for s, d in out.items() if not s.startswith("_")} == {"AAA": 1}
    # The engine buys floor(buy_cash / live price) shares; a live price above
    # the prior close may buy one share fewer than ST's 125 (accepted).
    assert out["_nexus_position_sizes"]["AAA"] == {
        "buy_cash": 12_500.0,
        "bracket": {"take_profit_price": 109.0, "stop_loss_price": 94.0},
        "whole_shares": True, "fill_at_next_open": True}
    assert out["_nexus_action_intents"] == {"AAA": "swing_entry"}
    assert ai.calls == ["AAA", "CCC", "DDD"]           # BBB: same sector as AAA (fix 4)
    r = rows()
    assert (r["AAA"]["status"], r["CCC"]["status"], r["DDD"]["status"]) == (
        "auto_approved", "pending", "ai_rejected")
    assert "BBB" not in r
    assert r["CCC"]["proposal"] == {"entry": 80.0, "stop": 75.2, "target": 87.2, "shares": 156}
    cats = [c for c, _ in live.sent]
    assert cats == ["swing_entry", "swing_pending_review", "swing_run_summary",
                    "swing_run_summary"]
    assert live.sent[-1][1] == "Swing Trader ✅ Run Complete"
    assert cache[live._SCAN_DONE_KEY] == "2026-06-01" and live._SCAN_KEY not in cache


def test_a_later_tick_does_not_rescan(live, monkeypatch):
    ai = scripted({"AAA": APPROVE, "CCC": REVIEW, "DDD": REJECT})
    monkeypatch.setattr(live.ai_analyst, "analyse", ai)
    cache = {}
    tick(live, MON_0920, LiveAdapter(), cache)
    working = LiveAdapter(open_orders=[NS(symbol="AAA", side="buy")])
    assert tick(live, MON_1000, working, cache) == {}
    assert ai.calls == ["AAA", "CCC", "DDD"]


def test_a_stale_quote_entry_is_reemitted_once_at_the_open(live, monkeypatch):
    monkeypatch.setattr(live.ai_analyst, "analyse",
                        scripted({"AAA": APPROVE, "CCC": REJECT, "DDD": REJECT}))
    cache = {}
    first = tick(live, MON_0920, LiveAdapter(), cache)
    empty = LiveAdapter()
    again = tick(live, MON_0940, empty, cache)
    assert again["AAA"] == 1
    assert again["_nexus_position_sizes"]["AAA"] == first["_nexus_position_sizes"]["AAA"]
    assert empty.closed_calls == [(["AAA"], "2026-06-01")]
    assert tick(live, MON_1000, LiveAdapter(), cache) == {}


def test_no_rearm_when_the_broker_saw_the_order(live, monkeypatch):
    monkeypatch.setattr(live.ai_analyst, "analyse",
                        scripted({"AAA": APPROVE, "CCC": REJECT, "DDD": REJECT}))
    cache = {}
    tick(live, MON_0920, LiveAdapter(), cache)
    rejected = LiveAdapter(closed=[NS(symbol="AAA", side="buy", status="rejected")])
    assert tick(live, MON_0940, rejected, cache) == {}
    assert tick(live, MON_0940, LiveAdapter(positions={"AAA": (125, 100.0, 12_500.0)}),
                {**cache}) == {}


def test_an_ai_error_skips_only_that_candidate(live, monkeypatch):
    monkeypatch.setattr(live.ai_analyst, "analyse", scripted({
        "AAA": ValueError("the model returned no valid JSON object"),
        "CCC": APPROVE, "DDD": ValueError("conviction_score 150 is outside 0-100")}))
    cache = {}
    out = tick(live, MON_0920, LiveAdapter(), cache)
    assert out["_nexus_executable_buys"] == ["CCC"]
    assert set(rows()) == {"CCC"}
    assert cache[live._SCAN_DONE_KEY] == "2026-06-01"


def test_no_model_linked_refuses_entries_with_one_alert_per_session(live, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("no scoring without a model")

    monkeypatch.setattr(live.ai_analyst, "analyse", boom)
    held = LiveAdapter(positions={"EEE": (10, 100.0, 1_040.0)})
    monkeypatch.setattr(live, "swing_indicators",
                        lambda f, c: dict(IND, EEE=ind(104.0, rsi=72.0, rsi_prev=68.0)))
    no_model = cfg(conviction_llm_provider="", conviction_llm_model="")
    cache = {}
    out = tick(live, MON_0920, held, cache, config=no_model)
    assert {s: d for s, d in out.items() if not s.startswith("_")} == {"EEE": -1}
    tick(live, MON_1000, held, cache, config=no_model)
    assert [c for c, _ in live.sent].count("strategy_error") == 1
    tick(live, TUE_0920, held, cache, config=no_model)
    assert [c for c, _ in live.sent].count("strategy_error") == 2


def test_the_tick_budget_resumes_the_scan_on_the_next_tick(live, monkeypatch):
    ai = scripted({"AAA": APPROVE, "CCC": REVIEW, "DDD": REJECT})
    monkeypatch.setattr(live.ai_analyst, "analyse", ai)
    budget = iter([100.0, 60.0, 10.0])
    monkeypatch.setattr(live.clock, "time_left", lambda deadline: next(budget, 1_000.0))
    cache = {}
    first = tick(live, MON_0920, LiveAdapter(), cache)
    assert first["_nexus_executable_buys"] == ["AAA"]
    assert cache[live._SCAN_KEY]["cursor"] == 1 and live._SCAN_DONE_KEY not in cache
    working = LiveAdapter(open_orders=[NS(symbol="AAA", side="buy")])
    second = tick(live, MON_0940, working, cache, mode="FULL")
    assert second == {}                                # CCC pending, DDD rejected
    assert ai.calls == ["AAA", "CCC", "DDD"]
    assert cache[live._SCAN_DONE_KEY] == "2026-06-01"


def test_rsi_cross_and_close_stop_exits_run_in_the_scan(live, monkeypatch):
    monkeypatch.setattr(live.ai_analyst, "analyse",
                        scripted({"AAA": REJECT, "CCC": REJECT, "DDD": REJECT}))
    monkeypatch.setattr(live, "swing_indicators", lambda f, c: dict(
        IND, EEE=ind(104.0, rsi=72.0, rsi_prev=68.0), FFF=ind(93.0, rsi=40.0, rsi_prev=45.0)))
    adapter = LiveAdapter(positions={"EEE": (10, 100.0, 1_040.0), "FFF": (5, 100.0, 465.0)})
    out = tick(live, MON_0920, adapter, {})
    assert out["EEE"] == -1 and out["FFF"] == -1
    assert out["_nexus_position_sizes"]["EEE"] == {"sell_fraction": 1.0, "fill_at_next_open": True}
    assert out["_nexus_action_intents"] == {"EEE": "swing_rsi_exit", "FFF": "swing_stop_exit"}
    assert [t for c, t in live.sent if c == "swing_exit"] == ["SELL EEE", "SELL FFF"]


def test_a_deferred_exit_is_reemitted_until_the_position_is_gone(live, monkeypatch):
    monkeypatch.setattr(live.ai_analyst, "analyse",
                        scripted({"AAA": REJECT, "CCC": REJECT, "DDD": REJECT}))
    monkeypatch.setattr(live, "swing_indicators",
                        lambda f, c: dict(IND, EEE=ind(104.0, rsi=72.0, rsi_prev=68.0)))
    held = LiveAdapter(positions={"EEE": (10, 100.0, 1_040.0)})
    cache = {}
    assert tick(live, MON_0920, held, cache)["EEE"] == -1
    assert cache[live._PENDING_EXIT_KEY]["EEE"]["intent"] == "swing_rsi_exit"
    # The engine deferred it (the legs' cancel did not confirm): nothing at the broker.
    again = tick(live, MON_0940, held, cache)
    assert {s: d for s, d in again.items() if not s.startswith("_")} == {"EEE": -1}
    assert again["_nexus_action_intents"] == {"EEE": "swing_rsi_exit"}
    assert again["_nexus_position_sizes"]["EEE"] == {"sell_fraction": 1.0}
    # A working market sell is the exit in flight: do not stack a second one.
    selling = LiveAdapter(positions={"EEE": (10, 100.0, 1_040.0)},
                          open_orders=[NS(symbol="EEE", side="sell", order_class="simple")])
    assert tick(live, MON_1000, selling, cache) == {}
    # Still held the next morning, before that session's scan: still re-emitted.
    tue_0900 = datetime(2026, 6, 2, 13, 0, tzinfo=timezone.utc)
    assert tick(live, tue_0900, held, cache)["EEE"] == -1
    # Gone: the entry is dropped and nothing more is sent.
    assert tick(live, datetime(2026, 6, 2, 13, 5, tzinfo=timezone.utc),
                LiveAdapter(), cache) == {}
    assert "EEE" not in cache[live._PENDING_EXIT_KEY]


def test_a_bracket_leg_is_not_a_working_exit(live, monkeypatch):
    cache = {live._PENDING_EXIT_KEY: {"EEE": {"reason": "stop_loss",
                                              "intent": "swing_stop_exit",
                                              "since": "2026-06-01"}},
             live._SCAN_DONE_KEY: "2026-06-01"}
    legs = [NS(symbol="EEE", side="sell", order_class="bracket", status="held"),
            NS(symbol="EEE", side="sell", order_class="bracket", status="new")]
    out = tick(live, MON_1000, LiveAdapter(positions={"EEE": (10, 100.0, 930.0)},
                                           open_orders=legs), cache)
    assert out["EEE"] == -1 and out["_nexus_action_intents"] == {"EEE": "swing_stop_exit"}

    class Unreadable(LiveAdapter):
        def list_open_orders(self, limit=200):
            raise RuntimeError("orders endpoint down")

    assert tick(live, MON_1000, Unreadable(positions={"EEE": (10, 100.0, 930.0)}),
                cache) == {}
    assert "EEE" in cache[live._PENDING_EXIT_KEY]


def test_the_bear_counter_advances_once_per_session(live, monkeypatch):
    monkeypatch.setattr(live.regime, "fetch_vix_close", lambda: 30.0)
    monkeypatch.setattr(live, "swing_indicators", lambda f, c: dict(IND, XLP=ind(80.0)))
    monkeypatch.setattr(live.ai_analyst, "analyse", scripted({"XLP": APPROVE}))
    cache = {}
    config = cfg(bear_regime_days=2)
    assert tick(live, MON_0920, LiveAdapter(), cache, config=config) == {}
    tick(live, MON_1000, LiveAdapter(), cache, config=config)
    assert cache[live._BEAR_KEY] == {"session": "2026-06-01", "blocked_days": 1}
    out = tick(live, TUE_0920, LiveAdapter(), cache, config=config)
    assert cache[live._BEAR_KEY]["blocked_days"] == 2
    assert out["_nexus_action_intents"] == {"XLP": "swing_defensive_entry"}
    assert any(t.startswith("🐻 Bear Mode Day 2") for _, t in live.sent)


def test_a_restarted_scan_reuses_the_recorded_decision(live, monkeypatch):
    ai = scripted({"CCC": REJECT, "DDD": REJECT})
    monkeypatch.setattr(live.ai_analyst, "analyse", ai)
    signals_store.insert_signal(signals_store.new_signal(
        instance_id="swing-paper", lane="swing", symbol="AAA", session="2026-06-01",
        score=80, recommendation="approve", reasoning="ok", key_risks=[],
        size_adjustment=1.0, proposal={}, status="auto_approved"))
    out = tick(live, MON_0920, LiveAdapter(), {})
    assert out["AAA"] == 1 and "AAA" not in ai.calls


def test_a_holiday_or_an_early_tick_does_nothing(live, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("must not scan")

    monkeypatch.setattr(live.ai_analyst, "analyse", boom)
    early = datetime(2026, 6, 1, 13, 0, tzinfo=timezone.utc)       # 09:00 ET
    assert tick(live, early, LiveAdapter(), {}) == {}
    monkeypatch.setattr(live.clock, "is_trading_day", lambda d: False)
    assert tick(live, MON_0920, LiveAdapter(), {}) == {}


def test_with_the_ai_gate_off_every_signal_enters_unscored(live, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("gate off: no scoring")

    monkeypatch.setattr(live.ai_analyst, "analyse", boom)
    out = tick(live, MON_0920, LiveAdapter(), {},
               config=cfg(ai_gate_enabled=False, conviction_llm_provider=""))
    assert out["_nexus_executable_buys"] == ["AAA", "CCC", "DDD"]
    assert all(r["score"] is None and r["status"] == "auto_approved" for r in rows().values())


def test_the_scan_never_places_more_than_the_open_slots(live, monkeypatch):
    monkeypatch.setattr(live.ai_analyst, "analyse",
                        scripted({"AAA": APPROVE, "CCC": APPROVE, "DDD": APPROVE}))
    out = tick(live, MON_0920, LiveAdapter(), {}, config=cfg(max_positions=1))
    assert out["_nexus_executable_buys"] == ["AAA"]


def test_missing_credentials_refuse_the_scan(live, monkeypatch):
    def refuse(k, s):
        raise RuntimeError("Alpaca data credentials missing")

    monkeypatch.setattr(live.market_data, "data_client", refuse)
    cache = {}
    assert tick(live, MON_0920, LiveAdapter(), cache) == {}
    assert live._SCAN_DONE_KEY not in cache
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest backend/tests/test_strategy_swing_live.py -q -p no:cacheprovider`
Expected: FAIL — the `_live` stub returns `{}` (first assertion fails on `{}`).

- [ ] **Step 3: Add the live cache keys and helpers**

In `backend/strategies/strategy_swing.py`, directly after `_LOGGED_KEY = ...` add:

```python
_SCAN_KEY = "_swing_scan"                      # live: the resumable scan state
_SCAN_DONE_KEY = "_swing_scan_done_session"    # live: the session latch, on COMPLETION
_EMITTED_KEY = "_swing_emitted"                # live: entries sent this session
_NO_MODEL_KEY = "_swing_no_model_session"      # live: the no-model alert, once a session
_SECTOR_CACHE_KEY = "_swing_sector_cache"      # live: yfinance fallback sectors
_PENDING_EXIT_KEY = "_swing_pending_exits"     # live: exits re-sent until the stock is gone
```

Append to `backend/swing_trader/account.py`:

```python
def equity_positions(emu) -> dict:
    """{symbol: {"qty", "avg_entry_price", "market_value"}} for long stock.
    ST read avg_entry_price off Alpaca positions (paper_trader.py:545); the
    adapter's refresh_positions returns the same PositionDTO."""
    dtos = []
    refresh = getattr(emu, "refresh_positions", None)
    if callable(refresh):
        try:
            dtos = list(refresh() or [])
        except Exception:
            dtos = []
    try:
        held = {str(s).upper(): float(q or 0.0) for s, q in (emu.get_positions() or {}).items()}
    except Exception:
        held = {}
    out = {}
    for d in dtos:
        sym = str(getattr(d, "symbol", "") or "").upper()
        qty = float(getattr(d, "qty", 0) or 0.0)
        if qty > 0 and sym in held:
            out[sym] = {"qty": qty,
                        "avg_entry_price": float(getattr(d, "avg_entry_price", 0) or 0.0),
                        "market_value": float(getattr(d, "market_value", 0) or 0.0)}
    for sym, qty in held.items():
        if qty > 0 and sym not in out:
            out[sym] = {"qty": qty, "avg_entry_price": entry_price_from_trades(emu, sym) or 0.0,
                        "market_value": 0.0}
    return out


def option_symbols(emu) -> set:
    """Open option contracts. ST's open_positions was the whole account, so
    option positions take swing slots (paper_trader.py:454, 596)."""
    try:
        return {str(p.symbol).upper() for p in (emu.list_option_positions() or [])}
    except Exception:
        return set()


def live_equity(emu, prices=None) -> float:
    try:
        return float(emu.refresh_account().equity)
    except Exception:
        return float(emu.get_portfolio_value(prices or {}) or 0.0)


def live_buying_power(emu) -> float:
    """ST sized swing entries against account.buying_power (paper_trader.py:450)."""
    try:
        return float(emu.refresh_cash().buying_power)
    except Exception:
        return float(emu.get_cash() or 0.0)
```

In `backend/strategies/strategy_swing.py`, directly after `_exits(...)` add:

```python
def _yf_symbol(symbol) -> str:
    """yfinance spells class shares with a dash (BRK-B), Alpaca with a dot."""
    return str(symbol).replace(".", "-")


def _enum_text(value) -> str:
    return str(getattr(value, "value", value) or "").strip().lower().rsplit(".", 1)[-1]


def _working_exit(order) -> bool:
    """A working SELL that is not a bracket leg. Alpaca reports a bracket's
    take-profit and stop legs as sells of order_class "bracket" (the stop
    waits in "held"); those legs are what the engine cancels before it sells,
    so they never count as the exit being in flight."""
    return (_enum_text(getattr(order, "side", None)) == "sell"
            and _enum_text(getattr(order, "order_class", None)) not in {"bracket", "oco", "oto"})
```

- [ ] **Step 4: Replace the `_live` stub**

Replace the whole `_live` method (the last method of `StrategySwing`) with:

```python
    # -- live ----------------------------------------------------------------

    def _live(self, prices, current_time, cfg, emu, cache, mode):
        """Spec §5.1 live/paper. The scan starts at the first tick at or after
        scan_time_et, resumes on later ticks until every candidate is scored,
        and stamps the session latch when it COMPLETES: a crash mid-scan
        resumes from the persisted cursor instead of losing the day."""
        session = clock.ny_date(current_time)
        if not clock.is_trading_day(date.fromisoformat(session)):
            return {}
        iid = str(cfg.get("instance_id") or "swing")
        decisions, sizes, intents = {}, {}, {}
        self._reemit_exits(session, emu, cache, decisions, sizes, intents)
        self._rearm_stale(current_time, session, emu, cache, decisions, sizes, intents)

        if (cache.get(_SCAN_DONE_KEY) != session
                and clock.at_or_after(current_time, cfg["scan_time_et"])):
            signals_store.ensure_tables()
            deadline = clock.tick_deadline(current_time, mode)
            scan = cache.get(_SCAN_KEY)
            if not isinstance(scan, dict) or scan.get("session") != session:
                scan = None
                if clock.time_left(deadline) >= clock.PREPARE_RESERVE_S:
                    try:
                        scan = self._prepare(prices, session, iid, cfg, emu, cache,
                                             decisions, sizes, intents)
                    except Exception as exc:
                        _log(f"StrategySwing {session} | scan preparation failed "
                             f"({type(exc).__name__}: {exc}); retried next tick", "red")
                        scan = None
                    if scan is not None:
                        cache[_SCAN_KEY] = scan
            if scan is not None:
                self._score_queue(scan, session, iid, cfg, cache, deadline,
                                  decisions, sizes, intents)
                if scan["cursor"] >= len(scan["queue"]):
                    cache[_SCAN_DONE_KEY] = session
                    cache.pop(_SCAN_KEY, None)
                    self._run_summary(scan, session, iid, emu)

        self._remember_emitted(current_time, session, cache, decisions, sizes, intents)
        return _emit(decisions, sizes, intents)

    def _prepare(self, prices, session, iid, cfg, emu, cache, decisions, sizes, intents):
        """paper_trader.py:446-626 up to the entry loop: data, regime, exits,
        the bear counter, and the queue of names whose signal fired. Exits are
        merged into the payload only once everything above them succeeded."""
        try:
            client = market_data.data_client(cfg.get("alpaca_key"), cfg.get("alpaca_secret"))
        except RuntimeError as exc:
            _log_once(cache, "no-creds", session,
                      f"StrategySwing {session} | REFUSING to scan — {exc}", "red")
            return None
        live_universe = [universe.norm_symbol(s) for s in universe.get_sp500_symbols()]
        defensive = _list(cfg["defensive_universe"])
        fetch_universe = list(dict.fromkeys(live_universe + defensive))
        bars = market_data.get_daily_bars(fetch_universe, days=market_data.LIVE_WINDOW_DAYS,
                                          client=client)
        ind = swing_indicators(bars, cfg)
        spy = ind.get("SPY")
        if not spy:
            _log_once(cache, "no-spy-live", session,
                      f"StrategySwing {session} | no SPY daily bars — the scan retries "
                      "next tick", "red")
            return None
        vix = regime.fetch_vix_close()
        reg = regime.regime_decision(spy.get("close"), spy.get("sma200"), vix,
                                     spy_buffer=float(cfg["spy_buffer"]),
                                     vix_max=float(cfg["vix_max"]))

        equity_pos = account.equity_positions(emu)
        option_syms = account.option_symbols(emu)
        equity = account.live_equity(emu, prices)
        bp = account.live_buying_power(emu)
        calibration.record_outcomes(iid, emu, "swing", held=set(equity_pos))

        ex_dec, ex_sizes, ex_int = {}, {}, {}
        reasons = _exits(ind, {s: p["qty"] for s, p in equity_pos.items()},
                         lambda s: equity_pos[s]["avg_entry_price"], cfg,
                         ex_dec, ex_sizes, ex_int)
        for symbol, reason in reasons.items():
            close, pos = ind[symbol]["close"], equity_pos[symbol]
            entry = pos["avg_entry_price"]
            notify.send("swing_exit", iid, f"SELL {symbol}",
                        f"{symbol} — {reason}\n{(close - entry) / entry * 100:+.1f}% | "
                        f"${(close - entry) * pos['qty']:+.0f}", priority=1)
            bp += pos["qty"] * close
        pending = cache.setdefault(_PENDING_EXIT_KEY, {})
        for symbol, reason in reasons.items():
            pending.setdefault(symbol, {"reason": reason, "intent": INTENT_EXIT[reason],
                                        "since": session})
        active = (set(equity_pos) | option_syms) - set(reasons)

        bear = signals.update_regime_tracker(cache.get(_BEAR_KEY), reg["regime_ok"], session)
        cache[_BEAR_KEY] = bear
        univ, phase = signals.select_entry_universe(
            reg["regime_ok"], bear["blocked_days"],
            bear_regime_days=int(cfg["bear_regime_days"]),
            live_universe=live_universe, defensive_universe=defensive)
        vix_str = f"{vix:.1f}" if vix is not None else "n/a"
        if phase == "bear_mode":
            notify.send("swing_run_summary", iid, f"🐻 Bear Mode Day {bear['blocked_days']}",
                        f"Regime blocked {bear['blocked_days']} consecutive days.\n"
                        f"Scanning defensive: {', '.join(defensive)}\n"
                        f"Reason: {reg['blocked_reason']}")

        queue = []
        if univ:
            ekw = signals.entry_kwargs(cfg)
            for symbol in univ:
                if symbol in active or symbol not in ind:
                    continue
                if signals.entry_signal(ind[symbol], **ekw):
                    queue.append({"symbol": symbol, "ind": ind[symbol]})
        _log(f"StrategySwing {session} | regime "
             f"{'ACTIVE' if reg['regime_ok'] else 'BLOCKED'} ({phase}) | SPY "
             f"{reg['spy_close']} vs SMA200 {reg['spy_sma200']} | VIX {vix_str} | "
             f"exits={sorted(reasons)} | signals={[c['symbol'] for c in queue]}", "cyan")

        decisions.update(ex_dec)
        sizes.update(ex_sizes)
        intents.update(ex_int)
        return {"session": session, "phase": phase, "regime_ok": reg["regime_ok"],
                "vix": vix, "queue": queue, "cursor": 0, "active": sorted(active),
                "option_symbols": sorted(option_syms), "equity": equity,
                "buying_power": bp, "entries_placed": 0,
                "available_slots": int(cfg["max_positions"]) - len(active),
                "counts": {"entered": 0, "pending": 0, "rejected": 0, "skipped": 0,
                           "ai_errors": 0}}

    def _score_queue(self, scan, session, iid, cfg, cache, deadline,
                     decisions, sizes, intents):
        """paper_trader.py:626-756, one candidate at a time inside the tick
        budget. One failure skips its candidate (fix 5)."""
        gate = _truthy(cfg.get("ai_gate_enabled", True))
        role = ai_analyst.llm_role_from_config(cfg) if gate else None
        if gate and role is None:
            if cache.get(_NO_MODEL_KEY) != session:
                cache[_NO_MODEL_KEY] = session
                left = len(scan["queue"]) - scan["cursor"]
                msg = ("ai_gate_enabled is on but no conviction model is linked "
                       f"(conviction_llm_model_id); {left} swing entr(ies) refused this "
                       "session. Link a model in the strategy editor.")
                _log(f"StrategySwing {session} | REFUSING ENTRIES — {msg}", "red")
                notify.send("strategy_error", iid, "Swing AI gate: no model linked", msg,
                            priority=1)
            scan["counts"]["skipped"] += len(scan["queue"]) - scan["cursor"]
            scan["cursor"] = len(scan["queue"])
            return

        option_syms = set(scan.get("option_symbols") or [])
        smap = refdata.sector_map(store, [c["symbol"] for c in scan["queue"]]
                                  + list(scan["active"]))
        sector_cache = cache.setdefault(_SECTOR_CACHE_KEY, {})

        def sector_of(symbol):
            if symbol in option_syms:
                return "unknown"
            return sectors.get_symbol_sector(symbol, smap, allow_network=True,
                                             cache=sector_cache)

        client = market_data.data_client(cfg.get("alpaca_key"), cfg.get("alpaca_secret"))
        while scan["cursor"] < len(scan["queue"]):
            if clock.time_left(deadline) < clock.CANDIDATE_RESERVE_S:
                _log(f"StrategySwing {session} | tick budget spent — "
                     f"{len(scan['queue']) - scan['cursor']} candidate(s) resume next tick",
                     "cyan")
                return
            item = scan["queue"][scan["cursor"]]
            scan["cursor"] += 1
            try:
                self._consider(item, scan, session, iid, cfg, role, gate, sector_of,
                               client, cache, decisions, sizes, intents)
            except Exception as exc:
                scan["counts"]["ai_errors"] += 1
                _log(f"StrategySwing {session} | {item['symbol']}: skipped after "
                     f"{type(exc).__name__}: {exc} — the scan continues (fix 5)", "yellow")

    def _consider(self, item, scan, session, iid, cfg, role, gate, sector_of, client,
                  cache, decisions, sizes, intents):
        symbol, ind = item["symbol"], item["ind"]
        active = set(scan["active"])
        if symbol in active:
            return
        conflict = signals.sector_conflict(symbol, active, sector_of=sector_of,
                                           max_per_sector=int(cfg["max_per_sector"]))
        if conflict:
            _log(f"  {symbol}: SIGNAL blocked — sector conflict ({sector_of(symbol)} "
                 f"already held via {conflict})")
            scan["counts"]["skipped"] += 1
            return
        if scan["entries_placed"] >= scan["available_slots"]:
            _log(f"  {symbol}: Skipped (no open slots)")
            scan["counts"]["skipped"] += 1
            return
        alloc = scan["equity"] * float(cfg["position_size_pct"])
        if scan["buying_power"] < alloc * 0.5:
            _log(f"  {symbol}: Skipped (insufficient buying power: "
                 f"${scan['buying_power']:,.2f})")
            scan["counts"]["skipped"] += 1
            return
        use = min(alloc, scan["buying_power"])
        base_shares = int(use / ind["close"])
        if base_shares < 1:
            _log(f"  {symbol}: Skipped (share count rounds to 0)")
            scan["counts"]["skipped"] += 1
            return

        stop_price   = round(ind["close"] * (1 - float(cfg["stop_loss"])), 2)
        target_price = round(ind["close"] * (1 + float(cfg["profit_target"])), 2)

        # Hard earnings block — no exceptions regardless of AI score
        earnings_days = ai_analyst.days_until_earnings(_yf_symbol(symbol))
        block = int(cfg["earnings_hard_block_days"])
        if earnings_days is not None and earnings_days < block:
            _log(f"  [{symbol}] SKIP — earnings in {earnings_days} day(s) "
                 f"(hard block: < {block} days)")
            scan["counts"]["skipped"] += 1
            return

        sid = signals_store.signal_id_for(iid, "swing", session, symbol)
        existing = signals_store.get_signal(sid)
        proposal = {"entry": ind["close"], "stop": stop_price, "target": target_price,
                    "shares": base_shares}
        if existing is not None:
            # A resumed scan: the decision is already recorded; never re-score.
            result = {"conviction_score": existing.get("score"),
                      "recommendation": str(existing.get("recommendation") or "").lower(),
                      "reasoning": existing.get("reasoning") or "",
                      "position_size_adjustment": float(existing.get("size_adjustment") or 1.0),
                      "key_risks": existing.get("key_risks") or []}
        elif gate:
            result = ai_analyst.analyse(
                {"symbol": symbol, "rsi": ind["rsi"], "rsi_prev": ind["rsi_prev"],
                 "macd_hist": ind["macd_hist"], "macd_hist_prev": ind["macd_hist_prev"],
                 "entry_price": ind["close"], "shares": base_shares,
                 "stop_price": stop_price, "target_price": target_price},
                role=role, sector_of=sector_of, bars_client=client,
                earnings_fn=lambda s: earnings_days,
                approve_threshold=int(cfg["ai_approve_threshold"]),
                review_threshold=int(cfg["ai_review_threshold"]))
        else:
            result = {"conviction_score": None, "recommendation": "approve",
                      "reasoning": "AI gate disabled", "position_size_adjustment": 1.0,
                      "key_risks": []}
        rec = result["recommendation"]
        score = result.get("conviction_score")
        adj = float(result.get("position_size_adjustment") or 1.0)
        context = {k: result.get(k) for k in ("earnings_days", "sector_etf", "sector_rsi",
                                              "news_summary") if k in result}

        def record(status):
            if existing is None:
                signals_store.insert_signal(signals_store.new_signal(
                    instance_id=iid, lane="swing", symbol=symbol, session=session,
                    score=score, recommendation=rec, reasoning=result.get("reasoning"),
                    key_risks=result.get("key_risks"), size_adjustment=adj,
                    proposal=proposal, status=status, context=context))

        if rec == "reject":
            record("ai_rejected")
            scan["counts"]["rejected"] += 1
            if existing is None:
                notify.send("swing_run_summary", iid, f"REJECTED {symbol}",
                            f"{symbol} — Score {score}/100\n"
                            f"{str(result.get('reasoning') or '')[:100]}")
            return

        if rec == "review":
            record("pending")
            scan["counts"]["pending"] += 1
            if existing is None:
                notify.send("swing_pending_review", iid,
                            f"⚠️ REVIEW: {symbol} (score {score}/100)",
                            f"{symbol} @ ${ind['close']:.2f}\n"
                            f"Score: {score}/100 — needs your approval\n"
                            f"{str(result.get('reasoning') or '')[:120]}\n"
                            f"Risks: {', '.join(result.get('key_risks') or [])}\n"
                            f"Stop: ${stop_price:.2f} | Target: ${target_price:.2f}\n"
                            f"Approve or reject in IntelliStock (web or iOS).", priority=1)
            return

        # approve — apply position size adjustment before placing
        if existing is not None and existing.get("status") != "auto_approved":
            return          # the operator decided it, or it already failed
        shares = max(1, int(base_shares * adj))
        record("auto_approved")
        scan["buying_power"] -= shares * ind["close"]
        scan["entries_placed"] += 1
        scan["active"].append(symbol)            # fix 4: the sector set follows each buy
        scan["counts"]["entered"] += 1
        emitted = (cache.get(_EMITTED_KEY) or {})
        if emitted.get("session") == session and symbol in (emitted.get("entries") or {}):
            return          # already sent before a restart; the broker holds it
        decisions[symbol] = 1
        # The engine sizes a live whole-share bracket as floor(buy_cash / live
        # price) and reads no share count from the hint (plan A-live). buy_cash
        # is ST's allocation times the AI size adjustment, never below one
        # share at the prior close (ST's max(1, ...)).
        sizes[symbol] = {"buy_cash": round(max(use * adj, ind["close"]), 2),
                         "bracket": {"take_profit_price": target_price,
                                     "stop_loss_price": stop_price},
                         "whole_shares": True, "fill_at_next_open": True}
        intents[symbol] = INTENT_DEFENSIVE if scan["phase"] == "bear_mode" else INTENT_ENTRY
        if existing is None:
            notify.send("swing_entry", iid, f"BUY {symbol}",
                        f"{symbol} — {shares} shares @ ${ind['close']:.2f}\n"
                        f"Stop ${stop_price:.2f} | Target ${target_price:.2f}", priority=1)

    def _reemit_exits(self, session, emu, cache, decisions, sizes, intents):
        """A-live contract addition 15: the engine never retries an exit it
        deferred. Re-send every pending exit whose stock is still held and has
        no working non-bracket sell; forget it once the stock is gone."""
        pending = cache.get(_PENDING_EXIT_KEY)
        if not isinstance(pending, dict) or not pending:
            return
        try:
            held = {str(s).upper() for s, q in (emu.get_positions() or {}).items()
                    if float(q or 0.0) > 0}
            selling = {str(o.symbol).upper() for o in (emu.list_open_orders() or [])
                       if _working_exit(o)}
        except Exception as exc:
            _log_once(cache, "exit-book", session,
                      f"StrategySwing {session} | pending exits not re-sent — the book is "
                      f"unreadable ({type(exc).__name__}: {exc})", "yellow")
            return
        for symbol in sorted(pending):
            if symbol not in held:
                pending.pop(symbol)
                _log(f"StrategySwing {session} | {symbol}: position gone — exit complete")
                continue
            if symbol in selling:
                continue
            decisions[symbol] = -1
            sizes[symbol] = {"sell_fraction": 1.0}
            intents[symbol] = pending[symbol]["intent"]
            _log_once(cache, f"reemit-{symbol}", session,
                      f"StrategySwing {session} | {symbol}: re-sending the "
                      f"{pending[symbol]['reason']} exit decided {pending[symbol]['since']} "
                      "— still held with no working sell (the engine does not retry a "
                      "deferred exit)", "yellow")

    def _rearm_stale(self, current_time, session, emu, cache, decisions, sizes, intents):
        """Spec §5.1: an entry the gate refused on a stale pre-market quote is
        re-emitted ONCE, at the first regular-hours tick. The lane cannot see
        the gate's reason, so it re-emits exactly the pre-market entries that
        left no trace at the broker: no working order, no order of any status
        since the session began, no position. An unreadable book re-emits
        nothing — a missed entry costs an opportunity, a duplicate costs money."""
        em = cache.get(_EMITTED_KEY)
        if not isinstance(em, dict) or em.get("session") != session:
            return
        todo = [s for s, e in (em.get("entries") or {}).items()
                if not e.get("at_rth") and not e.get("rearmed")]
        if not todo or not clock.is_rth(current_time):
            return
        for s in todo:
            em["entries"][s]["rearmed"] = True
        try:
            working = {str(o.symbol).upper() for o in (emu.list_open_orders() or [])}
            seen = {str(o.symbol).upper() for o in (emu.list_closed_orders(todo, session) or [])}
            held = {str(s).upper() for s, q in (emu.get_positions() or {}).items()
                    if float(q or 0.0) > 0}
        except Exception as exc:
            _log(f"StrategySwing {session} | stale-quote re-arm skipped — the order book "
                 f"is unreadable ({type(exc).__name__}: {exc})", "yellow")
            return
        for s in todo:
            if s in working or s in seen or s in held:
                continue
            entry = em["entries"][s]
            decisions[s] = 1
            sizes[s] = dict(entry["hint"])
            intents[s] = entry["intent"]
            _log(f"StrategySwing {session} | {s}: re-emitting the entry once at the open — "
                 "nothing reached the broker pre-market (quote.stale)", "cyan")

    def _remember_emitted(self, current_time, session, cache, decisions, sizes, intents):
        buys = [s for s, d in decisions.items() if d == 1]
        if not buys:
            return
        em = cache.get(_EMITTED_KEY)
        if not isinstance(em, dict) or em.get("session") != session:
            em = {"session": session, "entries": {}}
            cache[_EMITTED_KEY] = em
        at_rth = clock.is_rth(current_time)
        for s in buys:
            if s in em["entries"]:
                continue            # a re-arm keeps its record, rearmed=True
            em["entries"][s] = {"hint": dict(sizes.get(s) or {}),
                                "intent": intents.get(s, INTENT_ENTRY),
                                "at_rth": at_rth, "rearmed": False}

    def _run_summary(self, scan, session, iid, emu):
        """paper_trader.py:790-809, the run-complete notification."""
        positions = account.equity_positions(emu)
        parts = []
        for s, p in sorted(positions.items()):
            if p["avg_entry_price"] > 0 and p["market_value"] > 0:
                pct = (p["market_value"] / (p["qty"] * p["avg_entry_price"]) - 1) * 100
                parts.append(f"{s} {pct:+.1f}%")
        c = scan["counts"]
        vix = scan.get("vix")
        lines = [f"{session} ET", f"Signals: {c['entered']} | Positions: {len(positions)}"]
        if parts:
            lines.append(" | ".join(parts))
        lines.append(f"Regime: {'ACTIVE' if scan.get('regime_ok') else 'BLOCKED'} | "
                     f"VIX {f'{vix:.1f}' if vix is not None else 'n/a'}")
        lines.append(f"Review: {c['pending']} | Rejected: {c['rejected']} | "
                     f"Skipped: {c['skipped']} | AI errors: {c['ai_errors']}")
        notify.send("swing_run_summary", iid, "Swing Trader ✅ Run Complete", "\n".join(lines))
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python3 -m pytest backend/tests/test_strategy_swing_live.py backend/tests/test_strategy_swing_backtest.py -q -p no:cacheprovider`
Expected: PASS (32 tests).

- [ ] **Step 6: Commit**

Run `gitnexus_detect_changes()`; expect `backend/strategies/strategy_swing.py` and `backend/swing_trader/account.py` only.

```bash
git add backend/swing_trader/account.py backend/strategies/strategy_swing.py backend/tests/test_strategy_swing_live.py
git commit -m "feat(swing): StrategySwing live scan, AI gate and stale-quote re-arm

The 09:15 ET scan fetches its own bars, runs ST's exits and entry gates,
and scores candidates with the linked model inside the shared tick budget,
resuming on later ticks; the session latch is stamped on completion. No
linked model refuses entries with one alert per session, one AI error
skips one candidate, and a pre-market entry that never reached the broker
is re-sent once at the open. An exit the engine deferred is re-sent on
every later tick until the position is gone.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016ztXjLWXu61Dpp4XMU2jsS"
```

---

### Task 18: `StrategyWheel` — header, inert backtests and the weekly put scan (B10, part 1)

**Files:**
- Modify: `backend/swing_trader/account.py` (append the option-book readers)
- Create: `backend/strategies/strategy_wheel.py`
- Test: `backend/tests/test_strategy_wheel.py`

**Interfaces:**
- Consumes: Tasks 5, 7, 8, 9, 12, 13, 14 (`clock`, `market_data`, `wheel_rules.screen_technicals/wheel_earnings_days/apply_earnings/sector_filter/next_friday/scan_already_ran_this_week/duplicate_put_reason/build_put_order_live`, `notify.send`, `ai_analyst.llm_role_from_config/score_candidate`, `signals_store`).
- Consumes (live adapter, plan A-live): `list_option_positions()`, `list_open_orders()`, `get_account_options()`, `get_option_contracts(...)`, `get_option_snapshots(...)`; and, for the orders to execute, A-live's `_nexus_option_orders` side channel (contract §1) with the plan-B `session` key, `strategy_wheel` in `_LANE_ENABLE_FLAGS`, the options gate branch, and the per-signal status write-back for orders that carry a `signal_id`.
- Produces:
  - `account.option_positions(emu) -> list | None`, `account.open_orders(emu) -> list | None` (None = unreadable: the wheel then sells nothing), `account.account_options(emu) -> dict` (`cash`, `equity` floats).
  - `StrategyWheel.run_once(...)` returning `{}` or `{"_nexus_position_sizes": {"_cash_reserve_floor_pct": 0.0}, "_nexus_option_orders": [order, ...]}`.
  - Module names: `DEFAULTS`, `_SCAN_KEY = "_wheel_scan"`, `_COMPLETE_KEY = "_wheel_scan_complete_session"`, `_MONITOR_KEY = "_wheel_monitor_session"`, `_IV_KEY = "_wheel_iv_session"`, `_NO_MODEL_KEY = "_wheel_no_model_session"`, `_emit_options(orders) -> dict`, `EARNINGS_RESERVE_S = 5.0`.
  - Rows: `SwingSignals` lane `wheel` (`auto_approved`, `pending`, `ai_rejected`, `failed`), `SwingWheelScans` (`placed`, `pending`, `rejected`, `skipped`).

**The weekly scan (spec §5.2, ST `wheel_trader.main`).** On `scan_weekday` (Monday) at or after `scan_time_et` inside regular hours — 10:40 on the broker's grid — and, if the week has no completion marker, on the day after (ST's Tuesday fallback). Stage 1 runs ST's position checks (Task 19), fetches 90 calendar days of bars for the S&P list minus BRK-B/BF-B, and runs the technical screen. Stage 2 applies the earnings filter one name at a time and then ST's per-sector cap. Stage 3 scores each candidate: 75+ builds the put from the full chain (Tasks 8–9: delta nearest 0.25, bid × 0.95, caps counting existing puts, cash not margin) after refusing an underlying that already has an open short put or a working sell order (fix 1); 50–74 is a `pending` signal and a push; below 50 is `ai_rejected`. Every stage is resumable inside the shared tick budget, and the completion marker (ST's `last_wheel_scan_complete.txt`) is written only after the last candidate. A scan still running at the close stops; the Tuesday fallback then starts a fresh one. With no model linked the scan is refused with one alert per session (ST scored every candidate).

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_strategy_wheel.py`:

```python
"""StrategyWheel: inert in backtests, and the weekly put scan (spec §5.2)."""
import importlib.util
import json
import os
import re
import sys
from datetime import datetime, timezone
from types import SimpleNamespace as NS

import pytest

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from swing_trader import clock, signals_store  # noqa: E402
from swing_trader.constants import WHEEL_DEFAULTS  # noqa: E402

PATH = os.path.join(_backend, "strategies", "strategy_wheel.py")
MON_1040 = datetime(2026, 6, 1, 14, 40, tzinfo=timezone.utc)
MON_1100 = datetime(2026, 6, 1, 15, 0, tzinfo=timezone.utc)
MON_1020 = datetime(2026, 6, 1, 14, 20, tzinfo=timezone.utc)
TUE_1040 = datetime(2026, 6, 2, 14, 40, tzinfo=timezone.utc)
WED_1040 = datetime(2026, 6, 3, 14, 40, tzinfo=timezone.utc)
EXPIRY = "2026-06-12"


def _load():
    spec = importlib.util.spec_from_file_location("strategies.strategy_wheel", PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def pre(symbol, price, strike, prem):
    return {"symbol": symbol, "stock_price": price, "strike_price": strike,
            "otm_pct": round((price - strike) / price * 100, 2), "est_premium": prem,
            "est_premium_pct": round(prem / price * 100, 3), "rsi": 48.0,
            "sma50": price * 0.95, "atr": prem * 4, "atr_pct": 4.0}


PRE = [pre("APH", 131.2, 127.8, 1.7), pre("GIS", 60.0, 58.5, 0.8), pre("KO", 70.0, 68.2, 0.9)]
STRIKES = {"APH": (126.0, 127.0, 128.0), "GIS": (57.0, 58.0, 59.0), "KO": (67.0, 68.0, 69.0)}


def occ(underlying, strike, kind="P", expiry=EXPIRY):
    return f"{underlying}{expiry[2:4]}{expiry[5:7]}{expiry[8:10]}{kind}{int(strike * 1000):08d}"


class WheelAdapter:
    def __init__(self, option_positions=(), open_orders=(), cash=100_000.0,
                 equity=100_000.0, equities=None, broken_orders=False):
        self.option_positions = list(option_positions)
        self.orders = list(open_orders)
        self.cash, self.equity = cash, equity
        self.equities = dict(equities or {})
        self.broken_orders = broken_orders

    def get_positions(self):
        return {s: v[0] for s, v in self.equities.items()}

    def refresh_positions(self):
        return [NS(symbol=s, qty=v[0], avg_entry_price=v[1], market_value=v[0] * v[1])
                for s, v in self.equities.items()]

    def list_option_positions(self):
        return list(self.option_positions)

    def list_open_orders(self, limit=200):
        if self.broken_orders:
            raise RuntimeError("orders endpoint down")
        return list(self.orders)

    def get_account_options(self):
        return {"cash": self.cash, "equity": self.equity}

    def refresh_account(self):
        return NS(equity=self.equity)

    def get_cash(self):
        return self.cash

    def get_option_contracts(self, underlying, **kw):
        return [NS(symbol=occ(underlying, k), underlying=underlying, option_type="put",
                   strike=k, expiration=EXPIRY, open_interest=10, close_price=1.0)
                for k in STRIKES.get(underlying, ())]

    def get_option_snapshots(self, symbols):
        out = {}
        for s in symbols:
            strike = int(s[-8:]) / 1000
            middle = STRIKES[s[:-15]][1]
            out[s] = NS(bid=1.0, delta=(-0.25 if strike == middle else -0.10))
        return out

    def get_latest_trades(self, symbols):
        return {}


def scorer(results):
    calls = []

    def score(candidate, **kw):
        calls.append(candidate["symbol"])
        r = results[candidate["symbol"]]
        return dict(candidate, conviction_score=r[0], recommendation=r[1],
                    reasoning="r", position_size_contracts=r[2], key_risks=[])

    score.calls = calls
    return score


@pytest.fixture
def wheel(store, monkeypatch):
    m = _load()
    monkeypatch.setattr(m, "store", store)
    monkeypatch.setattr(signals_store, "store", store)
    monkeypatch.setattr(m.market_data, "data_client", lambda k, s: object())
    monkeypatch.setattr(m.market_data, "get_daily_bars", lambda syms, days, client: {"APH": object()})
    monkeypatch.setattr(m.universe, "get_wheel_universe", lambda: ["APH", "GIS", "KO"])
    monkeypatch.setattr(m.wheel_rules, "screen_technicals", lambda raw, syms, cfg=None: [dict(p) for p in PRE])
    monkeypatch.setattr(m.wheel_rules, "wheel_earnings_days", lambda s: None)
    monkeypatch.setattr(m.clock, "is_trading_day", lambda d: True)
    monkeypatch.setattr(m.calibration, "record_outcomes", lambda *a, **k: 0)
    monkeypatch.setattr(m.ai_analyst, "score_candidate", scorer(
        {"APH": (80, "approve", 1), "GIS": (60, "review", 1), "KO": (30, "reject", 1)}))
    # The daily monitor and the IV snapshot (Task 19) have their own tests.
    monkeypatch.setattr(m.StrategyWheel, "_monitor", lambda self, *a: [])
    monkeypatch.setattr(m.StrategyWheel, "_iv", lambda self, *a: [])
    sent = []
    monkeypatch.setattr(m.notify, "send",
                        lambda cat, iid, title, msg, priority=0: sent.append((cat, title)))
    m.sent = sent
    clock._DEADLINES.clear()
    return m


def cfg(**over):
    c = dict(WHEEL_DEFAULTS, strategy_wheel_enabled=True, instance_id="swing-paper",
             alpaca_key="k", alpaca_secret="s", conviction_llm_provider="claude-cli",
             conviction_llm_model="claude-sonnet-4-6", conviction_llm_api_key="")
    c.update(over)
    return c


def tick(m, at, adapter, cache, config=None, mode="MONITOR", data=None):
    return m.StrategyWheel().run_once(["SPY"], {}, at, config or cfg(), {}, data=data,
                                      portfolio_emulator=adapter, strategy_cache=cache,
                                      mode=mode)


def orders(out):
    return out.get("_nexus_option_orders", []) if out else []


# -- header and inert backtests ----------------------------------------------

def test_the_schema_header_is_exactly_the_defaults():
    schema = json.loads(re.search(r"# INTELLISTOCK_SCHEMA: (.*)", open(PATH).read()).group(1))
    assert schema["strategy"] == "strategy_wheel" and schema["execution_scope"] == "run_once"
    assert schema["execution_position"] == 20 and schema["decision_phase"] == "pre"
    assert schema["config"] == WHEEL_DEFAULTS and list(schema["config"]) == list(WHEEL_DEFAULTS)
    assert hasattr(_load().StrategyWheel, "run_once")


def test_backtests_are_inert_and_say_so_once(wheel, monkeypatch):
    lines = []
    monkeypatch.setattr(wheel, "_log", lambda msg, color="white": lines.append(msg))
    cache = {}
    assert tick(wheel, MON_1040, WheelAdapter(), cache, data={"APH": []}) == {}
    assert tick(wheel, MON_1100, WheelAdapter(), cache, data={"APH": []}) == {}
    assert sum("live-only" in line for line in lines) == 1
    assert tick(wheel, MON_1040, WheelAdapter(), {}, config=cfg(strategy_wheel_enabled=False)) == {}


# -- the Monday scan -----------------------------------------------------------

def test_monday_scan_sells_the_approved_put_and_records_every_score(wheel):
    cache = {}
    out = tick(wheel, MON_1040, WheelAdapter(), cache)
    sid = signals_store.signal_id_for("swing-paper", "wheel", "2026-06-01", "APH")
    assert out["_nexus_position_sizes"] == {"_cash_reserve_floor_pct": 0.0}
    assert orders(out) == [{
        "signal_id": sid, "session": "2026-06-01", "underlying": "APH",
        "contract": occ("APH", 127.0), "option_type": "put", "strike": 127.0,
        "expiry": EXPIRY, "position_intent": "sell_to_open", "qty": 1,
        "order_type": "limit", "limit_price": 0.95, "tif": "day",
        "reason": "wheel_sto_put"}]
    rows = {r["symbol"]: r for r in signals_store.all_signals("swing-paper")}
    assert {s: r["status"] for s, r in rows.items()} == {
        "APH": "auto_approved", "GIS": "pending", "KO": "ai_rejected"}
    assert rows["APH"]["proposal"]["contract"] == occ("APH", 127.0)
    assert rows["GIS"]["proposal"]["expiry"] == EXPIRY and rows["GIS"]["proposal"]["qty"] == 1
    scans = {r["symbol"]: r["status"] for r in signals_store.list_wheel_scans("swing-paper")}
    assert scans == {"APH": "placed", "GIS": "pending", "KO": "rejected"}
    assert cache[wheel._COMPLETE_KEY] == "2026-06-01" and wheel._SCAN_KEY not in cache
    cats = [c for c, _ in wheel.sent]
    assert cats == ["wheel_put_placed", "wheel_pending_review", "swing_run_summary"]
    assert tick(wheel, MON_1100, WheelAdapter(), cache) == {}          # done for the week


def test_the_scan_waits_for_its_day_its_time_and_regular_hours(wheel):
    assert tick(wheel, MON_1020, WheelAdapter(), {}) == {}
    assert tick(wheel, WED_1040, WheelAdapter(), {}) == {}
    after_close = datetime(2026, 6, 1, 20, 30, tzinfo=timezone.utc)
    assert tick(wheel, after_close, WheelAdapter(), {}) == {}
    assert wheel.ai_analyst.score_candidate.calls == []


def test_tuesday_fallback_runs_only_without_a_completion_marker(wheel):
    assert tick(wheel, TUE_1040, WheelAdapter(),
                {wheel._COMPLETE_KEY: "2026-06-01"}) == {}
    assert wheel.ai_analyst.score_candidate.calls == []
    cache = {wheel._COMPLETE_KEY: "2026-05-26"}                         # last week
    out = tick(wheel, TUE_1040, WheelAdapter(), cache)
    assert [o["underlying"] for o in orders(out)] == ["APH"]
    assert cache[wheel._COMPLETE_KEY] == "2026-06-02"


def test_a_crashed_monday_scan_resumes_without_rescoring(wheel, monkeypatch):
    budget = iter([100.0, 50.0, 50.0, 50.0, 60.0, 10.0])
    monkeypatch.setattr(wheel.clock, "time_left", lambda deadline: next(budget, 1_000.0))
    cache = {}
    first = tick(wheel, MON_1040, WheelAdapter(), cache)
    assert [o["underlying"] for o in orders(first)] == ["APH"]
    assert cache[wheel._SCAN_KEY]["phase"] == "scoring" and cache[wheel._SCAN_KEY]["cursor"] == 1
    assert wheel._COMPLETE_KEY not in cache
    # A fresh strategy object over the persisted cache: the broker restarted.
    working = WheelAdapter(open_orders=[NS(symbol=occ("APH", 127.0), side="sell", qty=1,
                                           filled_qty=0, position_intent="sell_to_open")])
    second = tick(wheel, MON_1100, working, cache, mode="FULL")
    assert orders(second) == []
    assert wheel.ai_analyst.score_candidate.calls == ["APH", "GIS", "KO"]
    assert cache[wheel._COMPLETE_KEY] == "2026-06-01"


def test_a_rerun_after_losing_the_cache_does_not_sell_a_second_put(wheel):
    # fix 1: the first run sold APH, then the process died before the cache
    # was saved. The rerun finds the recorded decision and the working order.
    signals_store.insert_signal(signals_store.new_signal(
        instance_id="swing-paper", lane="wheel", symbol="APH", session="2026-06-01",
        score=80, recommendation="approve", reasoning="r", key_risks=[],
        size_adjustment=None, proposal={"contract": occ("APH", 127.0)},
        status="auto_approved"))
    working = WheelAdapter(open_orders=[NS(symbol=occ("APH", 127.0), side="sell", qty=1,
                                           filled_qty=0, position_intent="sell_to_open")])
    out = tick(wheel, MON_1040, working, {})
    assert orders(out) == []
    assert "APH" not in wheel.ai_analyst.score_candidate.calls


def test_an_underlying_with_an_open_put_is_skipped(wheel):
    held = NS(symbol=occ("APH", 120.0, expiry="2026-06-05"), underlying="APH",
              option_type="put", strike=120.0, expiry="2026-06-05", qty=-1,
              avg_entry_price=1.0, market_value=-60.0)
    out = tick(wheel, MON_1040, WheelAdapter(option_positions=[held]), {})
    assert orders(out) == []
    scan = {r["symbol"]: r for r in signals_store.list_wheel_scans("swing-paper")}["APH"]
    assert scan["status"] == "skipped" and "duplicate" in scan["skip_reason"]
    sig = signals_store.get_signal(signals_store.signal_id_for(
        "swing-paper", "wheel", "2026-06-01", "APH"))
    assert sig["status"] == "failed" and "duplicate" in sig["error"]


def test_an_unreadable_order_book_sells_nothing_and_resumes(wheel):
    cache = {}
    assert orders(tick(wheel, MON_1040, WheelAdapter(broken_orders=True), cache)) == []
    assert cache[wheel._SCAN_KEY]["cursor"] == 0
    out = tick(wheel, MON_1100, WheelAdapter(), cache)
    assert [o["underlying"] for o in orders(out)] == ["APH"]


def test_no_model_linked_refuses_the_scan_with_one_alert(wheel):
    cache = {}
    no_model = cfg(conviction_llm_provider="", conviction_llm_model="")
    assert tick(wheel, MON_1040, WheelAdapter(), cache, config=no_model) == {}
    assert tick(wheel, MON_1100, WheelAdapter(), cache, config=no_model) == {}
    assert [c for c, _ in wheel.sent] == ["strategy_error"]
    assert wheel._COMPLETE_KEY not in cache
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest backend/tests/test_strategy_wheel.py -q -p no:cacheprovider`
Expected: FAIL — `FileNotFoundError` for `backend/strategies/strategy_wheel.py`.

- [ ] **Step 3: Append the option-book readers**

Append to `backend/swing_trader/account.py`:

```python
def option_positions(emu):
    """Open option positions (OptionPositionDTOs, contract §3), or None when
    the adapter cannot say — the wheel then sells nothing (fix 1 needs them)."""
    try:
        return list(emu.list_option_positions() or [])
    except Exception:
        return None


def open_orders(emu):
    """Working orders (OrderRefs), or None when unreadable."""
    try:
        return list(emu.list_open_orders() or [])
    except Exception:
        return None


def account_options(emu) -> dict:
    """{"cash", "equity", ...} for the wheel's caps (plan A-live
    get_account_options), falling back to settled cash and account equity."""
    try:
        acct = dict(emu.get_account_options() or {})
    except Exception:
        acct = {}
    if acct.get("cash") is None:
        try:
            acct["cash"] = float(emu.get_cash() or 0.0)
        except Exception:
            acct["cash"] = 0.0
    if acct.get("equity") is None:
        acct["equity"] = live_equity(emu)
    acct["cash"] = float(acct["cash"])
    acct["equity"] = float(acct["equity"])
    return acct
```

- [ ] **Step 4: Write the wrapper**

Create `backend/strategies/strategy_wheel.py` (line 1 generated the same way as Task 16's, from `WHEEL_DEFAULTS` with `"execution_position": 20`):

```python
# INTELLISTOCK_SCHEMA: {"strategy": "strategy_wheel", "weight": 1.0, "execution_position": 20, "decision_phase": "pre", "execution_scope": "run_once", "conditions": {}, "config": {"strategy_wheel_enabled": false, "rsi_min": 30, "rsi_max": 60, "sma_trend": 50, "atr_period": 14, "strike_atr_mult": 0.5, "min_premium_pct": 0.005, "target_delta": 0.25, "days_to_expiry": 7, "max_collateral_pct": 0.25, "max_per_sector": 2, "auto_covered_call": false, "approve_threshold": 75, "review_threshold": 50, "earnings_block_days": 7, "limit_bid_mult": 0.95, "scan_weekday": 0, "scan_time_et": "10:30", "monitor_time_et": "15:45", "conviction_llm_model_id": ""}}
# INTELLISTOCK_DESCRIPTION: ST's options wheel (github.com/tmasters2876/swing-trader), live and paper only: every Monday at 10:30 ET — Tuesday if Monday's scan did not finish — it screens the S&P 500 for RSI 30–60, price above its 50-day SMA, ATR above 1.5% and a strike at least 1.5% out of the money, skips earnings within 7 days, caps two names per sector, and has the linked conviction model score each one. Scores of 75+ sell the weekly put nearest 0.25 delta at bid × 0.95, capped at 25% of equity per name and by cash; 50–74 wait for your approval. Daily at 15:45 ET it buys back puts 10% in the money (5% within two days of expiry, any amount on expiry day), and each scan buys back a put worth twice its premium. Inert in backtests: there is no historical option data.
"""Strategy Wheel wrapper: the options lane of the swing-trader port.

ST's wheel rules live in backend/swing_trader/wheel_rules.py. This file owns
the broker contract: the resumable weekly scan, the daily monitor, the IV
snapshot, and the `_nexus_option_orders` payload (interface contract §1).

Spec: docs/superpowers/specs/2026-09-24-swing-trader-port-design.md §5.2
"""
import os
import sys
from datetime import date

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from db import store  # noqa: E402  (tests monkeypatch this name)
from swing_trader import (  # noqa: E402
    account,
    ai_analyst,
    calibration,
    clock,
    iv,
    market_data,
    notify,
    signals_store,
    universe,
    wheel_rules,
)
from swing_trader.constants import (  # noqa: E402
    IV_SNAPSHOT_TIME_ET,
    WHEEL_DEFAULTS as DEFAULTS,
    WHEEL_WARMUP_DAYS,
)

try:
    from intellistock_logger import intellistock_logger as _ilog  # type: ignore

    def _log(msg, color="white"):
        _ilog.log(str(msg), color, service="StrategyWheel")
except Exception:  # pragma: no cover - standalone/test import
    def _log(msg, color="white"):
        print(f"[StrategyWheel] {msg}")


_SCAN_KEY = "_wheel_scan"                        # the resumable weekly scan
_COMPLETE_KEY = "_wheel_scan_complete_session"   # ST: last_wheel_scan_complete.txt
_MONITOR_KEY = "_wheel_monitor_session"          # the daily monitor, once a session
_IV_KEY = "_wheel_iv_session"                    # the IV snapshot, once a session
_NO_MODEL_KEY = "_wheel_no_model_session"
_LOGGED_KEY = "_wheel_logged"

#: One yfinance earnings lookup.
EARNINGS_RESERVE_S = 5.0


def _log_once(cache, reason, scope, msg, color="white"):
    seen = cache.get(_LOGGED_KEY)
    if not isinstance(seen, dict):
        seen = {}
        cache[_LOGGED_KEY] = seen
    if seen.get(reason) == scope:
        return
    seen[reason] = scope
    _log(msg, color)


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _emit_options(orders) -> dict:
    if not orders:
        return {}
    return {"_nexus_position_sizes": {"_cash_reserve_floor_pct": 0.0},
            "_nexus_option_orders": list(orders)}


class StrategyWheel:
    # broker.py resolves strategy_wheel -> StrategyWheel; the name is not free.

    def run_once(self, symbols, prices, current_time, config, conditions,
                 data=None, portfolio_emulator=None, strategy_cache=None,
                 time_increment=None, mode=None, **kwargs):
        cfg = {**DEFAULTS, **(config or {})}
        if not _truthy(cfg.get("strategy_wheel_enabled", False)):
            return {}
        cache = strategy_cache if isinstance(strategy_cache, dict) else {}
        if data is not None:
            _log_once(cache, "backtest", "any",
                      "StrategyWheel: the wheel is live-only — there is no historical "
                      "option data (spec §1) — so this lane is inert in backtests.",
                      "yellow")
            return {}
        if portfolio_emulator is None:
            _log_once(cache, "no-emulator", str(current_time)[:10],
                      "StrategyWheel: REFUSING — no broker adapter to read the book.", "red")
            return {}
        session = clock.ny_date(current_time)
        today = date.fromisoformat(session)
        if not clock.is_trading_day(today):
            return {}
        iid = str(cfg.get("instance_id") or "wheel")
        signals_store.ensure_tables()
        deadline = clock.tick_deadline(current_time, mode)
        orders = []
        for step in (self._monitor, self._weekly, self._iv):
            try:
                orders.extend(step(current_time, session, today, iid, cfg,
                                   portfolio_emulator, cache, deadline) or [])
            except Exception as exc:
                _log(f"StrategyWheel {session} | {step.__name__.lstrip('_')} failed "
                     f"({type(exc).__name__}: {exc})", "red")
        return _emit_options(orders)

    # -- the weekly scan (wheel_trader.py:1257-1407) ---------------------------

    def _weekly(self, now, session, today, iid, cfg, emu, cache, deadline):
        scan_wd = int(cfg.get("scan_weekday", 0)) % 7
        if today.weekday() not in (scan_wd, (scan_wd + 1) % 7):
            return []
        if not clock.at_or_after(now, cfg["scan_time_et"]) or not clock.is_rth(now):
            return []
        # The Tuesday fallback runs only when Monday's scan did not COMPLETE
        # (wheel_trader.py:1274-1277); the marker, never partial rows, says so.
        if wheel_rules.scan_already_ran_this_week(cache.get(_COMPLETE_KEY), today):
            return []
        role = ai_analyst.llm_role_from_config(cfg)
        if role is None:
            if cache.get(_NO_MODEL_KEY) != session:
                cache[_NO_MODEL_KEY] = session
                msg = ("no conviction model is linked (conviction_llm_model_id), so the "
                       "weekly put scan is refused — ST scored every candidate. Link a "
                       "model in the strategy editor.")
                _log(f"StrategyWheel {session} | REFUSING the scan — {msg}", "red")
                notify.send("strategy_error", iid, "Wheel: no model linked", msg, priority=1)
            return []

        orders = []
        state = cache.get(_SCAN_KEY)
        if not isinstance(state, dict) or state.get("session") != session:
            if clock.time_left(deadline) < clock.PREPARE_RESERVE_S:
                return []
            state, orders = self._prepare(session, today, iid, cfg, emu, cache)
            if state is None:
                return orders
            cache[_SCAN_KEY] = state
        orders += self._advance(state, session, iid, cfg, role, emu, deadline)
        if state["phase"] == "done":
            cache[_COMPLETE_KEY] = session
            cache.pop(_SCAN_KEY, None)
            self._notify_results(state, session, iid)
        return orders

    def _prepare(self, session, today, iid, cfg, emu, cache):
        """wheel_trader.py:1279-1300: the position checks, then bars and the
        technical screen. Returns (state | None, orders)."""
        orders = self._position_checks(session, today, iid, cfg, emu, cache)
        try:
            client = market_data.data_client(cfg.get("alpaca_key"), cfg.get("alpaca_secret"))
        except RuntimeError as exc:
            _log_once(cache, "no-creds", session,
                      f"StrategyWheel {session} | REFUSING the scan — {exc}", "red")
            return None, orders
        live_universe = [universe.norm_symbol(s) for s in universe.get_wheel_universe()]
        raw = market_data.get_daily_bars(live_universe, days=int(WHEEL_WARMUP_DAYS * 1.5),
                                         client=client)
        if not raw:
            notify.send("swing_run_summary", iid, "Wheel Scanner ⚠️ Failed",
                        f"{session}\nNo market data returned")
            return None, orders
        pre = wheel_rules.screen_technicals(raw, live_universe, cfg=cfg)
        expiry = wheel_rules.next_friday(today, clock.trading_days)
        _log(f"StrategyWheel {session} | {len(pre)} of {len(live_universe)} passed the "
             f"technical screen; expiry {expiry}", "cyan")
        return ({"session": session, "phase": "earnings", "expiry": expiry, "pre": pre,
                 "cursor": 0, "candidates": [], "queue": [],
                 "scanned": len(live_universe), "approved": [], "review": [],
                 "rejected": 0, "committed": {}}, orders)

    def _advance(self, state, session, iid, cfg, role, emu, deadline):
        orders = []
        if state["phase"] == "earnings":
            while state["cursor"] < len(state["pre"]):
                if clock.time_left(deadline) < EARNINGS_RESERVE_S:
                    return orders
                p = state["pre"][state["cursor"]]
                state["cursor"] += 1
                days = wheel_rules.wheel_earnings_days(p["symbol"])
                c = wheel_rules.apply_earnings(p, days, expiry=state["expiry"],
                                               earnings_block_days=int(cfg["earnings_block_days"]))
                if c is None:
                    _log(f"  [wheel] {p['symbol']}: FILTERED — earnings in {days} days")
                    continue
                state["candidates"].append(c)
            state["queue"] = wheel_rules.sector_filter(
                state["candidates"], max_per_sector=int(cfg["max_per_sector"]))
            _log(f"  {len(state['queue'])} candidate(s) after sector filter → sending to AI scorer")
            state["phase"], state["cursor"] = "scoring", 0

        if state["phase"] == "scoring":
            positions, book = account.option_positions(emu), account.open_orders(emu)
            if positions is None or book is None:
                _log(f"StrategyWheel {session} | the option book is unreadable — no put "
                     "is sold this tick (fix 1 needs it); the scan resumes next tick",
                     "yellow")
                return orders
            acct = account.account_options(emu)
            while state["cursor"] < len(state["queue"]):
                if clock.time_left(deadline) < clock.CANDIDATE_RESERVE_S:
                    return orders
                c = state["queue"][state["cursor"]]
                state["cursor"] += 1
                try:
                    order = self._consider(c, state, session, iid, cfg, role, emu,
                                           positions, book, acct)
                except Exception as exc:
                    _log(f"StrategyWheel {session} | {c['symbol']}: skipped after "
                         f"{type(exc).__name__}: {exc} — the scan continues (fix 5)",
                         "yellow")
                    continue
                if order:
                    orders.append(order)
            state["phase"] = "done"
        return orders

    def _consider(self, c, state, session, iid, cfg, role, emu, positions, book, acct):
        """wheel_trader.py:1326-1356 for one candidate."""
        symbol = c["symbol"]
        sid = signals_store.signal_id_for(iid, "wheel", session, symbol)
        existing = signals_store.get_signal(sid)
        if existing is not None:
            # A resumed scan: the decision is recorded; never re-score.
            scored = dict(c, conviction_score=existing.get("score"),
                          recommendation=str(existing.get("recommendation") or "").lower(),
                          reasoning=existing.get("reasoning") or "",
                          position_size_contracts=int((existing.get("proposal") or {}).get("qty") or 1),
                          key_risks=existing.get("key_risks") or [])
        else:
            scored = ai_analyst.score_candidate(
                c, role=role, approve_threshold=int(cfg["approve_threshold"]),
                review_threshold=int(cfg["review_threshold"]), rsi_min=cfg["rsi_min"],
                rsi_max=cfg["rsi_max"], days_to_expiry=int(cfg["days_to_expiry"]))
        rec, score = scored["recommendation"], scored["conviction_score"]
        contracts = int(scored.get("position_size_contracts") or 1)
        scan_row = {"instance_id": iid, "session": session, "symbol": symbol,
                    "stock_price": c["stock_price"], "strike": c["strike_price"],
                    "expiry": c["expiry"], "premium_est": c["est_premium"], "score": score,
                    "recommendation": str(rec).upper(),
                    "reasoning": scored.get("reasoning") or ""}
        review_proposal = {"contract": None, "strike": c["strike_price"],
                           "expiry": c["expiry"], "qty": contracts, "limit_price": None,
                           "premium_est": c["est_premium"], "delta": None}

        def record(status, proposal, **extra):
            if existing is None:
                doc = signals_store.new_signal(
                    instance_id=iid, lane="wheel", symbol=symbol, session=session,
                    score=score, recommendation=rec, reasoning=scored.get("reasoning"),
                    key_risks=scored.get("key_risks"), size_adjustment=None,
                    proposal=proposal, status=status,
                    context={"stock_price": c["stock_price"], "otm_pct": c["otm_pct"],
                             "rsi": c["rsi"], "atr_pct": c["atr_pct"],
                             "earnings_days": c.get("earnings_days")})
                doc.update(extra)
                signals_store.insert_signal(doc)

        if rec == "reject":
            record("ai_rejected", review_proposal)
            signals_store.insert_wheel_scan(dict(scan_row, status="rejected", skip_reason=None))
            state["rejected"] += 1
            return None

        if rec == "review":
            record("pending", review_proposal)
            signals_store.insert_wheel_scan(dict(scan_row, status="pending", skip_reason=None))
            state["review"].append({"symbol": symbol, "strike_price": c["strike_price"],
                                    "conviction_score": score})
            if existing is None:
                notify.send("wheel_pending_review", iid,
                            f"⚠️ WHEEL REVIEW: {symbol} (score {score}/100)",
                            f"Sell ${c['strike_price']} put  exp {c['expiry']}\n"
                            f"Est. premium ~${c['est_premium']:.2f}/share\n"
                            f"{str(scored.get('reasoning') or '')[:120]}\n"
                            f"Approve or reject in IntelliStock (web or iOS).", priority=1)
            return None

        # approve
        if existing is not None and existing.get("status") != "auto_approved":
            return None
        duplicate = wheel_rules.duplicate_put_reason(symbol, positions, book, state["committed"])
        if duplicate:
            _log(f"  [wheel] {symbol}: {duplicate} — skipped (fix 1)")
            if existing is None:
                record("failed", review_proposal, error=duplicate)
                signals_store.insert_wheel_scan(dict(scan_row, status="skipped",
                                                     skip_reason=duplicate))
            return None
        order, error, meta = wheel_rules.build_put_order_live(
            dict(c, position_size_contracts=contracts), adapter=emu, cfg=cfg,
            equity=acct["equity"], cash=acct["cash"], option_positions=positions,
            open_orders=book, committed_collateral=state["committed"], signal_id=sid,
            session=session)
        if order is None:
            record("failed", review_proposal, error=error)
            signals_store.insert_wheel_scan(dict(scan_row, status="skipped", skip_reason=error))
            notify.send("swing_run_summary", iid, f"⚠️ Wheel Order Failed: {symbol}",
                        f"⚠️ ORDER NOT SENT — {symbol}\nReason: {error}\n"
                        f"Place manually: Sell ${c['strike_price']} put  exp {c['expiry']}\n"
                        f"Est. premium: ${c['est_premium']:.2f}/share\nScore: {score}",
                        priority=1)
            return None
        record("auto_approved", {"contract": order["contract"], "strike": order["strike"],
                                 "expiry": order["expiry"], "qty": order["qty"],
                                 "limit_price": order["limit_price"],
                                 "premium_est": c["est_premium"], "delta": meta.get("delta")})
        signals_store.insert_wheel_scan(dict(scan_row, strike=order["strike"],
                                             expiry=order["expiry"], status="placed",
                                             skip_reason=None))
        state["committed"][symbol] = order["strike"] * 100 * order["qty"]
        state["approved"].append({"symbol": symbol, "strike_price": order["strike"],
                                  "expiry": order["expiry"], "est_premium": c["est_premium"],
                                  "limit_price": order["limit_price"], "qty": order["qty"],
                                  "conviction_score": score})
        if existing is None:
            notify.send("wheel_put_placed", iid, f"✅ Wheel: {symbol} Put Sold",
                        f"✅ PUT ORDER SENT\n{symbol} ${order['strike']} put\n"
                        f"Expiry: {order['expiry']}\n"
                        f"Limit: ${order['limit_price']:.2f}/share "
                        f"(~${order['limit_price'] * 100:.0f}/contract)\n"
                        f"Contracts: {order['qty']}\nScore: {score}", priority=1)
        return order

    def _notify_results(self, state, session, iid):
        """wheel_trader.py:1151-1177, the run summary."""
        approved, review, rejected = state["approved"], state["review"], state["rejected"]
        scanned = state.get("scanned", 0)
        if not approved and not review:
            notify.send("swing_run_summary", iid, "Wheel Scanner ✅ Run Complete",
                        f"{session}\nNo put-selling opportunities found\n"
                        f"Scanned: {scanned} symbols | Rejected: {rejected}")
            return
        lines = [session]
        if approved:
            lines.append(f"✅ APPROVED ({len(approved)}):")
            for c in approved:
                lines.append(f"  {c['symbol']} ${c['strike_price']} put  exp {c['expiry']}  "
                             f"~${c['est_premium']:.2f}/sh  score {c['conviction_score']}")
        if review:
            lines.append(f"⚠️ REVIEW ({len(review)}):")
            for c in review:
                lines.append(f"  {c['symbol']} ${c['strike_price']} put  score {c['conviction_score']}")
        lines.append(f"Scanned: {scanned} | Rejected: {rejected}")
        notify.send("swing_run_summary", iid, "Wheel Scanner ✅ Run Complete", "\n".join(lines))

    # -- Task 19 replaces these three ------------------------------------------

    def _position_checks(self, session, today, iid, cfg, emu, cache):
        return []

    def _monitor(self, now, session, today, iid, cfg, emu, cache, deadline):
        return []

    def _iv(self, now, session, today, iid, cfg, emu, cache, deadline):
        return []
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `python3 -m pytest backend/tests/test_strategy_wheel.py -q -p no:cacheprovider`
Expected: PASS (10 tests).

- [ ] **Step 6: Commit**

Run `gitnexus_detect_changes()`; expect new files and the `account.py` append.

```bash
git add backend/swing_trader/account.py backend/strategies/strategy_wheel.py backend/tests/test_strategy_wheel.py
git commit -m "feat(wheel): StrategyWheel weekly put scan, inert in backtests

Monday at 10:30 ET, or Tuesday when Monday's scan never completed, the lane
screens, filters earnings, caps sectors, scores each candidate with the
linked model and sells the put nearest 0.25 delta through the option-order
side channel. The scan resumes across ticks, a rerun never sells a second
put (fix 1), and the completion marker is written only at the end.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016ztXjLWXu61Dpp4XMU2jsS"
```

---

### Task 19: `StrategyWheel` — position checks, the 15:45 monitor and the IV snapshot (B10, part 2)

**Files:**
- Modify: `backend/strategies/strategy_wheel.py` (replace the three stubs)
- Test: `backend/tests/test_strategy_wheel.py` (append)

**Interfaces:**
- Consumes: Tasks 10 and 15 (`wheel_rules.two_x_exits/covered_call_candidates/dry_run_message/put_monitor_decision/btc_order/next_friday`, `iv.run_iv_snapshot`, `calibration.record_outcomes`), Task 7 (`market_data.live_prices/get_latest_price/get_daily_bars`), Task 14 (`signals_store.swing_owned_symbols`).
- Consumes (plan A-live): `_engine_wheel_assignments` in this lane's strategy cache (A-live contract addition 13: `{"activity_id", "contract", "underlying", "shares", "side", "strike", "date"}`, `side` `"buy"` for a put assignment and `"sell"` for a call assignment); `OptionPositionDTO` rows from `list_option_positions()`; `get_latest_trades`, `get_option_contracts`, `get_option_snapshots` (the IV snapshot's Alpaca leg).
- Produces: `StrategyWheel._position_checks(session, today, iid, cfg, emu, cache) -> list[order]`, `StrategyWheel._monitor(now, session, today, iid, cfg, emu, cache, deadline) -> list[order]`, `StrategyWheel._iv(now, session, today, iid, cfg, emu, cache, deadline) -> list`, `StrategyWheel._assigned_shares(cache) -> dict[str, int]`; buy-to-close orders with reasons `wheel_btc_2x`, `wheel_btc_itm`, `wheel_btc_expiry`; notifications `wheel_position_alert` and `wheel_assignment`; `SwingIvSnapshots` rows.

**The rules.**
- **Position checks** (ST `check_open_wheel_positions` + `check_assigned_positions`, run at the start of each weekly scan). A short put whose cost to close has reached 2× the premium collected is bought back at market (`wheel_btc_2x`), unless a buy for that contract is already working. Covered calls are considered only for shares the engine recorded as assigned to this lane (`_engine_wheel_assignments`, puts minus calls, capped at the shares held). ST treated every uncovered 100-share holding that was not swing inventory as an assignment; on a shared instance that would write calls against shares the wheel never bought. Each assigned lot gets ST's dry-run notice (`wheel_assignment`). With `auto_covered_call` on, the notice says why no call is sent: plan A-live's option gate accepts sell-to-open puts only (`option.sell_to_open_requires_put`). A lane order the gate is certain to refuse would only move the failure somewhere quieter.
- **Daily monitor** (ST `app.py:1259-1411`). It runs once a session, in regular hours, on the first tick at or after `monitor_time_et` minus one tick grid: 15:40 on the broker's 20-minute grid, because the next tick (16:00) is after the close. It checks short **puts** only (fix 6; ST parsed any short OCC symbol) against the underlying's live price (adapter latest trade, then IEX, then yfinance, as ST). At ≥ 10% ITM, ≥ 5% ITM with ≤ 2 days left, or ITM on expiry day it buys to close at market with a priority-2 alert. Otherwise an ITM put gets a priority-1 alert and an OTM put expiring within a day gets a priority-0 note. A contract with a working buy is not bought again. An unreadable book defers the monitor to the next tick instead of latching the session.
- **IV snapshot** (ST `iv_collector.run_iv_snapshot`, cron 09:15). It runs at or after 09:15 ET, inside the tick budget, and resumes on later ticks (a symbol already recorded today is skipped). The session is latched only once every symbol has been tried. The Alpaca leg's spot is ST's: the IEX latest trade, else the last daily close in 7 days.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_strategy_wheel.py`:

```python
# -- Task 19: position checks, the daily monitor, the IV snapshot ------------

from datetime import date  # noqa: E402

from swing_trader import iv as _iv_module  # noqa: E402

_REAL_RUN_IV = _iv_module.run_iv_snapshot

MON_1540 = datetime(2026, 6, 1, 19, 40, tzinfo=timezone.utc)
MON_1520 = datetime(2026, 6, 1, 19, 20, tzinfo=timezone.utc)
MON_0900 = datetime(2026, 6, 1, 13, 0, tzinfo=timezone.utc)
MON_0920 = datetime(2026, 6, 1, 13, 20, tzinfo=timezone.utc)
MON_0940 = datetime(2026, 6, 1, 13, 40, tzinfo=timezone.utc)
TODAY = date(2026, 6, 1)


def short(underlying, strike, *, kind="P", expiry=EXPIRY, qty=-1, entry=1.0, value=-60.0):
    return NS(symbol=occ(underlying, strike, kind, expiry), underlying=underlying,
              option_type="put" if kind == "P" else "call", strike=strike, expiry=expiry,
              qty=qty, avg_entry_price=entry, market_value=value, current_price=None)


def assignment(underlying, shares=100, side="buy"):
    return {"activity_id": f"a-{underlying}-{side}", "contract": occ(underlying, 68.0),
            "underlying": underlying, "shares": shares, "side": side, "strike": 68.0,
            "date": "2026-05-29"}


@pytest.fixture
def daily(store, monkeypatch):
    m = _load()                  # a fresh module: the `wheel` fixture's patches do not apply
    monkeypatch.setattr(m, "store", store)
    monkeypatch.setattr(signals_store, "store", store)
    monkeypatch.setattr(m.clock, "is_trading_day", lambda d: True)
    monkeypatch.setattr(m.calibration, "record_outcomes", lambda *a, **k: 0)
    monkeypatch.setattr(m.market_data, "data_client", lambda k, s: object())
    monkeypatch.setattr(m.StrategyWheel, "_weekly", lambda self, *a: [])
    # The IV snapshot walks the whole wheel universe over the network; its
    # own two tests put it back.
    monkeypatch.setattr(m.iv, "run_iv_snapshot", lambda store, **kw: {"complete": True})
    sent = []
    monkeypatch.setattr(m.notify, "send",
                        lambda cat, iid, title, msg, priority=0: sent.append(
                            (cat, title, msg, priority)))
    m.sent = sent
    clock._DEADLINES.clear()
    return m


def checks(m, adapter, cache, config=None):
    return m.StrategyWheel()._position_checks("2026-06-01", TODAY, "swing-paper",
                                              config or cfg(), adapter, cache)


def test_the_monday_scan_buys_back_a_put_at_twice_its_premium(wheel):
    # ST check_open_wheel_positions: collected $100, $250 to close -> 2.5x.
    xyz = short("XYZ", 40.0, expiry="2026-06-05", entry=1.0, value=-250.0)
    out = tick(wheel, MON_1040, WheelAdapter(option_positions=[xyz]), {})
    assert [(o["contract"], o["reason"]) for o in orders(out)] == [
        (xyz.symbol, "wheel_btc_2x"), (occ("APH", 127.0), "wheel_sto_put")]
    btc = orders(out)[0]
    assert (btc["position_intent"], btc["order_type"], btc["limit_price"], btc["qty"],
            btc["session"]) == ("buy_to_close", "market", None, 1, "2026-06-01")
    assert wheel.sent[0] == ("wheel_position_alert", f"⚠️ Wheel Exit: {xyz.symbol}")


def test_a_put_with_a_working_buy_is_not_bought_back_twice(daily):
    xyz = short("XYZ", 40.0, entry=1.0, value=-250.0)
    buying = WheelAdapter(option_positions=[xyz],
                          open_orders=[NS(symbol=xyz.symbol, side="buy", qty=1, filled_qty=0)])
    assert checks(daily, buying, {}) == []


def test_assigned_shares_get_a_dry_run_covered_call_notice(daily):
    adapter = WheelAdapter(equities={"KO": (100, 68.0), "PEP": (300, 150.0)})
    cache = {"_engine_wheel_assignments": [assignment("KO")]}
    assert checks(daily, adapter, cache) == []
    (cat, title, msg, priority), = daily.sent
    assert (cat, title, priority) == ("wheel_assignment", "🔍 Assignment detected: KO (dry-run)", 1)
    assert "Would sell 1 covered call(s)" in msg and "$71.40" in msg and EXPIRY in msg
    # PEP was never assigned to this lane: ST would have written calls on it.


def test_auto_covered_call_is_refused_at_the_lane(daily):
    adapter = WheelAdapter(equities={"KO": (200, 68.0)})
    cache = {"_engine_wheel_assignments": [assignment("KO", 200)]}
    assert checks(daily, adapter, cache, config=cfg(auto_covered_call=True)) == []
    (_cat, _title, msg, _priority), = daily.sent
    assert "Would sell 2 covered call(s)" in msg
    assert "sell-to-open puts only" in msg


def test_a_call_assignment_or_a_covering_call_ends_the_notices(daily):
    adapter = WheelAdapter(equities={"KO": (100, 68.0)})
    netted = {"_engine_wheel_assignments": [assignment("KO"), assignment("KO", side="sell")]}
    assert checks(daily, adapter, netted) == [] and daily.sent == []
    covered = WheelAdapter(equities={"KO": (100, 68.0)},
                           option_positions=[short("KO", 72.0, kind="C")])
    assert checks(daily, covered, {"_engine_wheel_assignments": [assignment("KO")]}) == []
    assert daily.sent == []


# -- the daily monitor -----------------------------------------------------------

PRICES = {"AAA": 88.0, "BBB": 49.0, "CCC": 31.0}


def monitor_book():
    return [short("AAA", 100.0),                                   # 12% ITM -> close
            short("BBB", 50.0),                                    # 2% ITM, 11 DTE -> alert
            short("CCC", 30.0, expiry="2026-06-01"),               # OTM, expires today
            short("DDD", 20.0, kind="C")]                          # a call: not the wheel's (fix 6)


def test_the_monitor_buys_back_a_deep_itm_put_once_a_session(daily, monkeypatch):
    seen = []
    monkeypatch.setattr(daily.market_data, "live_prices",
                        lambda syms, adapter=None, client=None, now=None:
                        seen.append(sorted(syms)) or {s: PRICES[s] for s in syms if s in PRICES})
    cache = {}
    out = tick(daily, MON_1540, WheelAdapter(option_positions=monitor_book()), cache)
    assert [(o["contract"], o["reason"], o["position_intent"], o["order_type"], o["qty"])
            for o in orders(out)] == [
        (occ("AAA", 100.0), "wheel_btc_itm", "buy_to_close", "market", 1)]
    assert seen == [["AAA", "BBB", "CCC"]]
    assert [(c, t, p) for c, t, _m, p in daily.sent] == [
        ("wheel_position_alert", "🚨 Auto-Closed: AAA", 2),
        ("wheel_position_alert", "⚠️ ITM PUT: BBB", 1),
        ("wheel_position_alert", "⏰ EXPIRING TODAY OTM: CCC", 0)]
    assert cache[daily._MONITOR_KEY] == "2026-06-01"
    assert tick(daily, datetime(2026, 6, 1, 19, 50, tzinfo=timezone.utc),
                WheelAdapter(option_positions=monitor_book()), cache) == {}


def test_the_monitor_waits_for_its_tick_and_skips_a_working_buy(daily, monkeypatch):
    monkeypatch.setattr(daily.market_data, "live_prices",
                        lambda syms, adapter=None, client=None, now=None:
                        {s: PRICES[s] for s in syms if s in PRICES})
    assert tick(daily, MON_1520, WheelAdapter(option_positions=monitor_book()), {}) == {}
    assert daily.sent == []
    working = WheelAdapter(option_positions=monitor_book(),
                           open_orders=[NS(symbol=occ("AAA", 100.0), side="buy", qty=1,
                                           filled_qty=0)])
    assert tick(daily, MON_1540, working, {}) == {}


def test_an_unreadable_book_defers_the_monitor(daily, monkeypatch):
    monkeypatch.setattr(daily.market_data, "live_prices",
                        lambda syms, adapter=None, client=None, now=None:
                        {s: PRICES[s] for s in syms if s in PRICES})

    class Down(WheelAdapter):
        def list_option_positions(self):
            raise RuntimeError("positions endpoint down")

    cache = {}
    assert tick(daily, MON_1540, Down(), cache) == {}
    assert daily._MONITOR_KEY not in cache
    out = tick(daily, datetime(2026, 6, 1, 19, 45, tzinfo=timezone.utc),
               WheelAdapter(option_positions=monitor_book()), cache)
    assert [o["reason"] for o in orders(out)] == ["wheel_btc_itm"]


# -- the IV snapshot ---------------------------------------------------------------

def test_the_iv_snapshot_runs_after_0915_and_latches_when_complete(daily, monkeypatch):
    calls = []
    results = iter([False, True])

    def run(store, *, adapter, spot_for, today, symbols=None, deadline=None, **kw):
        calls.append(today)
        return {"date": str(today), "recorded": [], "skipped": [], "failed": [],
                "complete": next(results)}

    monkeypatch.setattr(daily.iv, "run_iv_snapshot", run)
    cache = {}
    tick(daily, MON_0900, WheelAdapter(), cache)
    assert calls == []
    tick(daily, MON_0920, WheelAdapter(), cache)
    assert calls == [TODAY] and daily._IV_KEY not in cache       # budget ran out
    tick(daily, MON_0940, WheelAdapter(), cache)
    assert cache[daily._IV_KEY] == "2026-06-01"
    tick(daily, MON_1040, WheelAdapter(), cache)
    assert calls == [TODAY, TODAY]


def test_the_iv_snapshot_writes_rows_through_the_store(daily, monkeypatch, store):
    monkeypatch.setattr(daily.iv, "run_iv_snapshot", _REAL_RUN_IV)
    monkeypatch.setattr(daily.iv, "snapshot_iv", lambda symbol, today=None: 0.31)
    monkeypatch.setattr(daily.iv, "snapshot_iv_alpaca",
                        lambda symbol, *, adapter, spot, today: None)
    monkeypatch.setattr(daily.market_data, "get_latest_price", lambda s, client: 100.0)
    monkeypatch.setattr(daily.iv, "WHEEL_UNIVERSE", ["APH", "KO"])
    cache = {}
    tick(daily, MON_0920, WheelAdapter(), cache)
    assert store.get("SwingIvSnapshots", "KO|2026-06-01") == {
        "id": "KO|2026-06-01", "symbol": "KO", "date": "2026-06-01",
        "iv30": 0.31, "iv30_alpaca": None}
    assert cache[daily._IV_KEY] == "2026-06-01"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest backend/tests/test_strategy_wheel.py -q -p no:cacheprovider`
Expected: FAIL. The stubs return `[]`, so the monitor test fails on its first assertion and the 2× test finds only the put order.

- [ ] **Step 3: Replace the stubs**

In `backend/strategies/strategy_wheel.py`, replace everything from the line `    # -- Task 19 replaces these three ------------------------------------------` to the end of the file with:

```python
    # -- position checks (wheel_trader.py:230-449) -------------------------------

    @staticmethod
    def _assigned_shares(cache) -> dict:
        """Net shares the engine recorded as assigned to this lane (A-live
        contract addition 13): put assignments add, call assignments remove."""
        net = {}
        for a in (cache or {}).get("_engine_wheel_assignments") or []:
            try:
                u = str(a.get("underlying") or "").strip().upper()
                shares = int(float(a.get("shares") or 0))
            except (AttributeError, TypeError, ValueError):
                continue
            if not u or shares <= 0:
                continue
            side = str(a.get("side") or "").strip().lower()
            net[u] = net.get(u, 0) + (shares if side == "buy" else -shares)
        return {u: n for u, n in net.items() if n > 0}

    def _position_checks(self, session, today, iid, cfg, emu, cache):
        """ST ran check_open_wheel_positions and check_assigned_positions at
        the start of every scan. Returns the buy-to-close orders."""
        positions, book = account.option_positions(emu), account.open_orders(emu)
        if positions is None or book is None:
            _log(f"StrategyWheel {session} | the option book is unreadable — the 2× "
                 "premium check and the assignment check are skipped this scan", "yellow")
            return []

        orders = []
        for p in [p for p in positions if float(getattr(p, "qty", 0) or 0) < 0]:
            try:
                qty = abs(int(float(p.qty)))
                cost_basis = abs(float(p.avg_entry_price)) * qty * 100
                current_value = abs(float(p.market_value))
                if cost_basis > 0:
                    _log(f"  [wheel-exit] {p.symbol}: collected=${cost_basis:.2f}  "
                         f"current_cost_to_close=${current_value:.2f}  "
                         f"ratio={current_value / cost_basis:.2f}x")
            except (TypeError, ValueError):
                continue
        for order, info in wheel_rules.two_x_exits(positions, open_orders=book):
            order["session"] = session
            orders.append(order)
            _log(f"  [wheel-exit] ⚠️ {order['contract']} at {info['loss_ratio']:.1f}× "
                 "premium — buying to close at market", "yellow")
            notify.send("wheel_position_alert", iid, f"⚠️ Wheel Exit: {order['contract']}",
                        f"⚠️ BUY-TO-CLOSE placed\n{order['contract']}\n"
                        f"Premium collected: ${info['cost_basis']:.2f}\n"
                        f"Cost to close: ${info['current_value']:.2f} "
                        f"({info['loss_ratio']:.1f}× premium)", priority=1)
        _log("  [wheel-exit] Position check complete")

        assigned = self._assigned_shares(cache)
        held = account.equity_positions(emu)
        mine = {s: dict(p, qty=min(float(p["qty"]), float(assigned[s])))
                for s, p in held.items() if s in assigned}
        for s in sorted(set(held) - set(mine)):
            if float(held[s]["qty"]) >= 100:
                _log(f"  [covered-call] {s}: {held[s]['qty']:g} shares, none assigned to the "
                     "wheel lane — not a covered-call candidate")
        swing_owned = signals_store.swing_owned_symbols(iid, set(held))
        candidates = wheel_rules.covered_call_candidates(mine, positions, book,
                                                         swing_owned=swing_owned)
        if candidates:
            expiry = wheel_rules.next_friday(today, clock.trading_days)
            for c in candidates:
                title, msg = wheel_rules.dry_run_message(c, expiry)
                if _truthy(cfg.get("auto_covered_call", False)):
                    msg += ("\nauto_covered_call is on, but the engine's option gate accepts "
                            "sell-to-open puts only (option.sell_to_open_requires_put), so no "
                            "call was sent. Sell it manually.")
                _log(f"  [covered-call] DRY-RUN {c['symbol']}: would sell {c['n_contracts']} "
                     f"call(s) strike ≥ ${c['call_strike']:.2f} exp {expiry} — no order placed")
                notify.send("wheel_assignment", iid, title, msg, priority=1)
        _log("  [covered-call] Assignment check complete")
        return orders

    # -- the daily monitor (app.py:1259-1411) -------------------------------------

    def _monitor(self, now, session, today, iid, cfg, emu, cache, deadline):
        if cache.get(_MONITOR_KEY) == session or not clock.is_rth(now):
            return []
        # One tick early: on the 20-minute grid the next tick (16:00) is after the close.
        if not clock.at_or_after(now, cfg["monitor_time_et"], lead_min=clock.TICK_GRID_MIN):
            return []
        positions, book = account.option_positions(emu), account.open_orders(emu)
        if positions is None or book is None:
            _log(f"StrategyWheel {session} | monitor deferred — the option book is "
                 "unreadable; it runs on the next tick", "yellow")
            return []
        shorts = [p for p in positions if float(getattr(p, "qty", 0) or 0) < 0]
        puts = [p for p in shorts if str(getattr(p, "option_type", "")).lower() == "put"]
        for p in shorts:
            if p not in puts:
                _log(f"[wheel-monitor] {p.symbol}: a short {p.option_type}, not a wheel put "
                     "— not checked (fix 6)")
        working = {str(getattr(o, "symbol", "")).upper() for o in book
                   if str(getattr(o, "side", "")).lower() == "buy"}
        try:
            client = market_data.data_client(cfg.get("alpaca_key"), cfg.get("alpaca_secret"))
        except RuntimeError:
            client = None
        prices = market_data.live_prices(sorted({str(p.underlying).upper() for p in puts}),
                                         adapter=emu, client=client, now=now) if puts else {}
        orders, alerts = [], 0
        for p in puts:
            try:
                d = wheel_rules.put_monitor_decision(
                    contract=p.symbol, underlying=str(p.underlying).upper(),
                    strike=float(p.strike), expiry=p.expiry,
                    stock_price=prices.get(str(p.underlying).upper()), today=today)
                _log(f"[wheel-monitor] {p.symbol}: underlying={d['underlying']} "
                     f"strike={d['strike']:.2f} stock={d['stock_price']} DTE={d['dte']} "
                     f"ITM={d['itm']} deep_itm={d['deep_itm']}")
                if d["action"] == "auto_close":
                    if str(p.symbol).upper() in working:
                        _log(f"[wheel-monitor] {p.symbol}: {d['reason']} — a buy-to-close "
                             "is already working; not sending another", "yellow")
                        continue
                    _log(f"[wheel-monitor] 🚨 AUTO-CLOSE triggered: {p.symbol} — {d['reason']}",
                         "red")
                    orders.append(wheel_rules.btc_order(p, abs(int(float(p.qty))), d["intent"],
                                                        session=session))
                if d["title"]:
                    notify.send("wheel_position_alert", iid, d["title"], d["message"],
                                priority=int(d["priority"] or 0))
                    alerts += 1
            except Exception as exc:
                _log(f"[wheel-monitor] Error processing {p.symbol}: {exc}", "yellow")
                continue
        cache[_MONITOR_KEY] = session
        calibration.record_outcomes(iid, emu, "wheel")
        _log(f"[wheel-monitor] Done — checked {len(puts)} short put positions, "
             f"{alerts} alerts sent")
        return orders

    # -- the IV snapshot (iv_collector.py:232-257) ---------------------------------

    def _iv(self, now, session, today, iid, cfg, emu, cache, deadline):
        if cache.get(_IV_KEY) == session or not clock.at_or_after(now, IV_SNAPSHOT_TIME_ET):
            return []
        try:
            client = market_data.data_client(cfg.get("alpaca_key"), cfg.get("alpaca_secret"))
        except RuntimeError:
            client = None

        def spot_for(symbol):
            # iv_collector.py:128-133: the IEX latest trade, else the last close.
            if client is None:
                return None
            price = market_data.get_latest_price(symbol, client=client)
            if price is None:
                frame = market_data.get_daily_bars([symbol], days=7, client=client).get(symbol)
                if frame is None or frame.empty:
                    return None
                price = float(frame["Close"].iloc[-1])
            return price

        adapter = emu if callable(getattr(emu, "get_option_contracts", None)) else None
        summary = iv.run_iv_snapshot(store, adapter=adapter, spot_for=spot_for, today=today,
                                     deadline=deadline)
        if summary.get("complete"):
            cache[_IV_KEY] = session
        return []
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest backend/tests/test_strategy_wheel.py -q -p no:cacheprovider`
Expected: PASS (20 tests).

- [ ] **Step 5: Commit**

Run `gitnexus_detect_changes()`; expect `backend/strategies/strategy_wheel.py` and the test file only.

```bash
git add backend/strategies/strategy_wheel.py backend/tests/test_strategy_wheel.py
git commit -m "feat(wheel): 2x buy-back, assignment notices, the daily monitor and IV snapshots

Each weekly scan first buys back a put worth twice its premium and sends
ST's dry-run covered-call notice for shares the engine assigned to this
lane. At the 15:40 tick the monitor buys to close deep, near-expiry or
expiring in-the-money puts and alerts on the rest, once a session. The IV
snapshot runs after 09:15 ET inside the tick budget and resumes until it
has tried every symbol.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016ztXjLWXu61Dpp4XMU2jsS"
```

---

### Task 20: The approval round trip through plan A-live's handler (B12)

**Files:**
- Test: `backend/tests/test_swing_approval_roundtrip.py` (create)

**Interfaces:**
- Consumes: Task 14 (`approvals.decide`, `approvals.build_approved_order`, `approvals.SignalConflict`, `signals_store.new_signal/insert_signal/get_signal/cas_signal`); plan A-live Task 15 (`_execute_swing_approval`, `_lane_config`, `_approval_live_price`, extracted from `backend/broker.py` through A-live's `backend/tests/swing_broker_harness.py`), A-live Tasks 9 and 10 (`_build_bracket_intent`, `_build_option_intent`, `_refresh_option_quote`), A-live Task 2 (`OptionContractDTO`, `OptionSnapshotDTO`) and A-live Task 6's `backend/tests/swing_live_fixtures.py` (`RTH`, Monday 2026-10-05 11:00 ET).
- Produces: no code. This pins the handshake between the two plans with the real plan-B modules. A-live's own Task 15 test stubs `swing_trader` out through `sys.modules`, so a signature drift on either side would pass both plans' suites and fail only in production.

**Run this task after plan A-live Task 15 has landed.** Plan B does not edit `backend/broker.py`: the handler the brief listed as B12 is A-live's.

The flow under test is the one Task 21's route drives. `decide` plus `cas_signal` makes a decision final. The route then enqueues a `submit_order` LiveCommand with `{"source": "swing_approval", "signal_id"}`, and A-live's handler rebuilds the order at the live price with `build_approved_order`, submits it once, and writes `submitted` back. A second click is a `SignalConflict` (400 at the route; a click that loses the race is a 409). A redelivered command finds the signal `submitted`, not approved, and places nothing.

- [ ] **Step 1: Write the test**

Create `backend/tests/test_swing_approval_roundtrip.py`:

```python
"""Plan B Task 20: an operator decision, then plan A-live's approval handler,
with the REAL swing_trader.approvals and signals_store over the FakeStore.
One approval places exactly one order (spec §7; interfaces §7; Review Focus 4).
Runs after plan A-live Task 15, whose handler and harness it exercises."""
import datetime as datetime_module
import threading
from decimal import Decimal
from types import SimpleNamespace as NS

import pytest

from broker_adapters.base import OptionContractDTO, OptionSnapshotDTO
from live_orders import GateDecision, OrderSide, OrderSource
from live_orders.service import OrderSubmission
from swing_broker_harness import extract
from swing_live_fixtures import RTH
from swing_trader import approvals, signals_store

IID = "instance-1"
LANES = [{"strategy": "strategy_swing", "config": {"strategy_swing_enabled": True}},
         {"strategy": "strategy_wheel", "config": {"strategy_wheel_enabled": True}}]
NOW_ISO = RTH.isoformat()
LIVE = 100.5
EXPIRY = "2026-10-16"      # next_friday(2026-10-05): the 10-09 Friday is under 7 days out


def put(strike, expiry=EXPIRY):
    return f"APH{expiry[2:4]}{expiry[5:7]}{expiry[8:10]}P{int(strike * 1000):08d}"


class Service:
    account_id = "acct-1"
    instance_id = IID

    def __init__(self):
        self.intents = []

    def enqueue(self, intent):
        self.intents.append(intent)
        decision = GateDecision(allowed=True, approved_quantity=intent.quantity,
                                reason_codes=(), idempotency_key=intent.idempotency_key)
        return OrderSubmission(decision=decision, reference=NS(broker_order_id="b-1"))


class Adapter:
    """What the handler (A-live) and build_approved_order (plan B) read."""
    _account_equity = 60_000.0

    def __init__(self, option_positions=(), open_orders=()):
        self._market_marks = NS(get=lambda symbol: NS(price=LIVE, observed_at=RTH))
        self.option_positions, self.open_orders = list(option_positions), list(open_orders)

    def fetch_rest_quote_marks(self, symbols):
        return tuple(symbols)

    def get_latest_trades(self, symbols):
        return {}

    def list_option_positions(self):
        return list(self.option_positions)

    def list_open_orders(self, limit=200):
        return list(self.open_orders)

    def get_account_options(self):
        return {"cash": 60_000.0, "equity": 60_000.0}

    def get_option_contracts(self, underlying, *, option_type=None, expiration_gte=None,
                             expiration_lte=None, strike_gte=None, strike_lte=None):
        return [OptionContractDTO(put(k), "APH", "put", k, EXPIRY, 100, 1.0)
                for k in (95.0, 96.0, 97.0)]

    def get_option_snapshots(self, contracts):
        return {c: OptionSnapshotDTO(c, 2.0, 2.2, 2.1, 0.3,
                                     -0.25 if c == put(96.0) else -0.10,
                                     None, None, None, RTH.isoformat())
                for c in contracts}


def handler():
    return extract(
        ("_execute_swing_approval", "_lane_config", "_approval_live_price",
         "_build_bracket_intent", "_build_option_intent",
         "_refresh_option_quote", "_truthy", "_merged_strategy_settings"),
        assigns=("_live_option_quotes", "_LANE_ENABLE_FLAGS"),
        namespace={"datetime": datetime_module,
                   "_live_order_dependency_lock": threading.Lock(),
                   "_live_order_dependency_state": {"risk_snapshot_id": "risk-9"}},
        check=("_execute_swing_approval", "_lane_config",
               "_approval_live_price"))["_execute_swing_approval"]


@pytest.fixture
def signals(store, monkeypatch):
    monkeypatch.setattr(signals_store, "store", store)
    return store


def pending(lane="swing", symbol="AAPL", **proposal):
    doc = signals_store.new_signal(
        instance_id=IID, lane=lane, symbol=symbol, session="2026-09-28", score=62,
        recommendation="review", reasoning="r", key_risks=[], size_adjustment=1.0,
        proposal=proposal or {"entry": 98.0, "stop": 92.12, "target": 106.82, "shares": 76},
        status="pending")
    signals_store.insert_signal(doc)
    return doc["id"]


def operator(signal_id, decision):
    """What the API route does (Task 21): decide, then compare-and-swap."""
    current = signals_store.get_signal(signal_id)
    decided = approvals.decide(current, decision, "pranav", None, NOW_ISO)
    if not signals_store.cas_signal(signal_id, expect_status="pending", doc=decided):
        raise AssertionError("lost the race")          # the route answers 409 (Task 21)
    return decided


def command(service, adapter, signal_id):
    return handler()(adapter, {"source": "swing_approval", "signal_id": signal_id},
                     service, cached_strategies=LANES, now_utc=RTH)


def test_a_submitted_signal_is_never_submitted_twice(signals):
    sid = pending()
    operator(sid, "approve")
    with pytest.raises(approvals.SignalConflict):
        operator(sid, "approve")                      # the second click
    service = Service()
    ok, error, result = command(service, Adapter(), sid)
    assert ok is True and error == ""
    (intent,) = service.intents
    # ST api_approve: shares and legs recomputed at the live price.
    assert (intent.side, intent.quantity, intent.order_class, intent.source) == (
        OrderSide.BUY, Decimal("74"), "bracket", OrderSource.MANUAL)
    assert intent.stop_loss_price == Decimal(str(round(LIVE * 0.94, 2)))
    assert intent.take_profit_price == Decimal(str(round(LIVE * 1.09, 2)))
    row = signals_store.get_signal(sid)
    assert (row["status"], row["order_client_id"]) == ("submitted", intent.idempotency_key)
    assert row["decided_by"] == "pranav"
    # The command delivered again (at-least-once): refused, nothing new placed.
    again = command(service, Adapter(), sid)
    assert again[0] is False and "not approved" in again[1]
    assert len(service.intents) == 1
    with pytest.raises(approvals.SignalConflict):
        operator(sid, "reject")


def test_approve_half_halves_the_rebuilt_share_count(signals):
    sid = pending()
    operator(sid, "approve_half")
    service = Service()
    assert command(service, Adapter(), sid)[0] is True
    assert service.intents[0].quantity == Decimal("37")


def test_a_rejected_signal_is_never_placed(signals):
    sid = pending()
    operator(sid, "reject")
    service = Service()
    ok, error, _result = command(service, Adapter(), sid)
    assert ok is False and "not approved" in error and service.intents == []
    assert signals_store.get_signal(sid)["status"] == "rejected"


def test_a_wheel_approval_sells_this_weeks_put_not_the_stale_one(signals):
    # fix 2: reviewed a week late, the stored 10-09 expiry is gone.
    sid = pending(lane="wheel", symbol="APH", contract=put(96.0, "2026-10-09"),
                  strike=96.0, expiry="2026-10-09", qty=1, limit_price=None,
                  premium_est=1.5, delta=None)
    operator(sid, "approve")
    service = Service()
    ok, error, _result = command(service, Adapter(), sid)
    assert ok is True and error == ""
    (intent,) = service.intents
    assert (intent.symbol, intent.position_intent, intent.quantity, intent.limit_price,
            intent.source, intent.contract_multiplier) == (
        put(96.0), "sell_to_open", Decimal("1"), Decimal("1.9"), OrderSource.MANUAL, 100)
    assert signals_store.get_signal(sid)["status"] == "submitted"


def test_an_approval_on_an_underlying_already_short_fails_and_places_nothing(signals):
    sid = pending(lane="wheel", symbol="APH", strike=96.0, qty=1, premium_est=1.5)
    operator(sid, "approve")
    held = NS(symbol=put(90.0, "2026-10-09"), underlying="APH", option_type="put",
              strike=90.0, expiry="2026-10-09", qty=-1, avg_entry_price=1.0,
              market_value=-80.0)
    service = Service()
    ok, error, _result = command(service, Adapter(option_positions=[held]), sid)
    assert ok is False and "duplicate" in error and service.intents == []
    row = signals_store.get_signal(sid)
    assert (row["status"], row["order_client_id"]) == ("failed", None)
```

- [ ] **Step 2: Run it**

Run: `python3 -m pytest backend/tests/test_swing_approval_roundtrip.py -q -p no:cacheprovider`
Expected before plan A-live Task 15: FAIL, `AssertionError: missing from broker.py: ['_approval_live_price', '_execute_swing_approval', '_lane_config']`.
Expected after it: PASS (5 tests). This task adds no production code. If an assertion fails, the two plans disagree about the contract (interfaces §6 and §7). Fix the side the failing assertion names. Never loosen the assertion.

- [ ] **Step 3: Commit**

Run `gitnexus_detect_changes()`; expect the new test file only.

```bash
git add backend/tests/test_swing_approval_roundtrip.py
git commit -m "test(swing): approval round trip through the broker handler

An operator decision is final after one compare-and-swap. The broker's
swing_approval handler rebuilds it at the live price through the real
approvals module, places exactly one order and marks the signal
submitted. A second click conflicts, a redelivered command places
nothing, and a wheel approval sells this week's expiry.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016ztXjLWXu61Dpp4XMU2jsS"
```

---

### Task 21: API routes and actions — signals, decisions, the wheel book, calibration (B14)

**Files:**
- Modify: `backend/interactive_utils.py` (new actions after `action_get_live_command`)
- Modify: `backend/api/main.py` (the `from interactive_utils import (...)` block; `SwingDecisionBody`; four routes after `api_get_live_command`)
- Test: `backend/tests/test_swing_api.py` (create)

**Interfaces:**
- Consumes: Task 14 (`approvals.decide/APPROVED_STATUSES/SignalConflict`, `signals_store.get_signal/cas_signal/list_signals/list_wheel_scans`), Task 15 (`calibration.calibration_report`), Tasks 7 and 9 (`market_data.data_client/live_prices`, `wheel_rules.occ_parts`), Task 5 (`clock.ny_date`); the existing `action_submit_live_command`, `_resolve_instance_doc` and `live_broker_fetch.fetch_broker_live_state` / `_load_credentials`; plan A-live Task 14's live-state option rows (signed `qty`, `underlying`, `strike`, `expiry`, nullable `last_price` / `unrealized_pnl`) and Task 15's `submit_order` handler for `{"source": "swing_approval", "signal_id"}`.
- Produces, exactly as interfaces §8 and §9 pin them (plan C consumes them):
  - `GET /instances/{instance_id}/swing/signals?status=&limit=` → `{"signals": [SwingSignals doc, ...]}`, newest first.
  - `POST /instances/{instance_id}/swing/signals/{signal_id}/decision`, body `{"decision": "approve"|"approve_half"|"reject", "reason": str | null | absent}`. Success is 200 with `{"signal": <decided doc>, "command_id": str | None}`. The error codes (coordinator ruling on interfaces §9 item 2):
    - **400** only when the signal is not pending: `approvals.SignalConflict`, a `ValueError`.
    - **404** for an unknown instance, an unknown signal, or another instance's signal: `LookupError`.
    - **409** when this click lost the compare-and-swap to a concurrent one: `SwingDecisionRaceError`.
    - **503** when the approval cannot reach the broker, because the instance is not running or the command could not be queued: `SwingBrokerUnavailableError`. The signal stays or goes back to pending, so a retry later works.
    - **422** for a malformed body.
  - `GET /instances/{instance_id}/wheel` → `{"open_puts": [...], "collateral_total", "cash", "recent_scans"}`, row keys exactly §9 item 3. `itm_pct` is signed (`(strike − underlying_price) / strike × 100`, negative out of the money, `null` with no underlying price). A broker that cannot be read is **503**, never an empty book.
  - `GET /instances/{instance_id}/swing/calibration` → `calibration.calibration_report(instance_id)`.
  - `interactive_utils`: `SwingBrokerUnavailableError(RuntimeError)` (503), `SwingDecisionRaceError(RuntimeError)` (409), `action_swing_list_signals(conn, instance_id, status=None, limit=100)`, `action_swing_decide_signal(conn, instance_id, signal_id, decision, reason=None, decided_by=None)`, `action_wheel_overview(conn, instance_id, *, today=None)`, `action_swing_calibration(conn, instance_id)`, `_swing_instance(conn, instance_id) -> (doc, real_id)`, `_wheel_underlying_prices(instance_id, symbols) -> dict`.

**Run this task after plan A-backtest Task 7.** A-backtest Task 7 edits `_CODE_FINGERPRINT_FILES` in `backend/api/main.py`. This task edits two other hunks of that file: the import block, and the routes after `api_get_live_command`. Both hunks are anchored on text that A-backtest does not touch. The deploy fingerprint lists stay plan A-live Task 16's.

- [ ] **Step 0: Impact analysis**

No existing function changes. The new action calls `action_submit_live_command`, and the new routes sit next to `api_get_live_command`. Run `npx gitnexus analyze`, then `gitnexus_impact({target: "action_submit_live_command", direction: "upstream"})` and `gitnexus_impact({target: "_run", direction: "upstream"})`, and report both. Neither is modified. Risk: LOW. `interactive_utils.py` and `api/main.py` are imported by the API process only, never by `broker.py`, so EB's order path cannot reach these lines.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_swing_api.py`:

```python
"""Plan B Task 21: the swing and wheel routes, over real HTTP (TestClient),
with the shapes interfaces §9 pins for plan C's UIs."""
import os
import sys
from datetime import date, datetime, timedelta, timezone

import pytest

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

import interactive_utils  # noqa: E402
import live_broker_fetch  # noqa: E402
from swing_trader import market_data, signals_store  # noqa: E402

IID = "swing-paper"


@pytest.fixture
def api(store, monkeypatch):
    from fastapi.testclient import TestClient

    from api import main

    monkeypatch.setattr(interactive_utils, "store", store)
    monkeypatch.setattr(signals_store, "store", store)
    store.insert("Instances", [{"id": IID, "name": IID, "runCommand": True},
                               {"id": "stopped", "name": "stopped", "runCommand": False},
                               {"id": "other", "name": "other", "runCommand": True}])
    commands = []

    def submit(conn, instance_id, command_type, payload, submitted_by=None):
        commands.append((instance_id, command_type, dict(payload), submitted_by))
        return {"command_id": f"cmd-{len(commands)}", "status": "pending"}

    monkeypatch.setattr(interactive_utils, "action_submit_live_command", submit)
    main.app.dependency_overrides[main.conn_dependency] = lambda: None
    main.app.dependency_overrides[main.get_current_user] = lambda: {"id": "u1",
                                                                    "username": "pranav"}
    try:
        client = TestClient(main.app)
        client.commands = commands
        yield client
    finally:
        main.app.dependency_overrides.pop(main.conn_dependency, None)
        main.app.dependency_overrides.pop(main.get_current_user, None)


def signal(symbol="AAPL", *, instance_id=IID, status="pending", lane="swing",
           created_at="2026-09-24T13:20:00+00:00"):
    doc = signals_store.new_signal(
        instance_id=instance_id, lane=lane, symbol=symbol, session="2026-09-24", score=62,
        recommendation="review", reasoning="r", key_risks=["x"], size_adjustment=0.5,
        proposal={"entry": 98.0, "stop": 92.12, "target": 106.82, "shares": 76},
        status=status, created_at=created_at)
    signals_store.insert_signal(doc)
    return doc["id"]


def decision_url(sid, instance_id=IID):
    return f"/instances/{instance_id}/swing/signals/{sid}/decision"


# -- GET signals ---------------------------------------------------------------

def test_signals_are_wrapped_filtered_and_newest_first(api):
    signal("AAA", created_at="2026-09-24T13:20:00+00:00")
    signal("BBB", created_at="2026-09-24T13:40:00+00:00")
    signal("CCC", status="ai_rejected")
    signal("DDD", instance_id="other")
    res = api.get(f"/instances/{IID}/swing/signals", params={"status": "pending"})
    assert res.status_code == 200
    body = res.json()
    assert list(body) == ["signals"]
    assert [s["symbol"] for s in body["signals"]] == ["BBB", "AAA"]
    assert set(body["signals"][0]) >= {"id", "instance_id", "lane", "symbol", "session",
                                       "created_at", "score", "recommendation", "reasoning",
                                       "key_risks", "size_adjustment", "proposal", "status",
                                       "decided_by", "decided_at", "decision_reason",
                                       "order_client_id", "outcome"}
    everything = api.get(f"/instances/{IID}/swing/signals").json()["signals"]
    assert {s["symbol"] for s in everything} == {"AAA", "BBB", "CCC"}
    assert api.get("/instances/nope/swing/signals").status_code == 404


# -- POST decision ---------------------------------------------------------------

def test_an_approval_is_final_and_queues_one_broker_command(api):
    sid = signal()
    res = api.post(decision_url(sid), json={"decision": "approve", "reason": "looks fine"})
    assert res.status_code == 200
    body = res.json()
    assert body["command_id"] == "cmd-1"
    assert (body["signal"]["status"], body["signal"]["decided_by"],
            body["signal"]["decision_reason"]) == ("approved", "pranav", "looks fine")
    assert api.commands == [(IID, "submit_order",
                             {"source": "swing_approval", "signal_id": sid}, "pranav")]
    assert signals_store.get_signal(sid)["status"] == "approved"


@pytest.mark.parametrize("body", [{"decision": "approve_half"},
                                  {"decision": "approve_half", "reason": None}])
def test_reason_may_be_absent_or_null(api, body):
    sid = signal()
    res = api.post(decision_url(sid), json=body)
    assert res.status_code == 200
    assert res.json()["signal"]["status"] == "approved_half"
    assert res.json()["signal"]["decision_reason"] is None


def test_a_second_decision_is_a_400_and_enqueues_nothing(api):
    sid = signal()
    assert api.post(decision_url(sid), json={"decision": "approve"}).status_code == 200
    again = api.post(decision_url(sid), json={"decision": "approve"})
    assert again.status_code == 400 and "not pending" in again.json()["detail"]
    late_reject = api.post(decision_url(sid), json={"decision": "reject"})
    assert late_reject.status_code == 400
    assert len(api.commands) == 1
    submitted = signal("SUB", status="submitted")
    assert api.post(decision_url(submitted), json={"decision": "approve"}).status_code == 400


def test_a_reject_queues_nothing_and_works_on_a_stopped_instance(api):
    sid = signal(instance_id="stopped")
    res = api.post(decision_url(sid, "stopped"), json={"decision": "reject"})
    assert res.status_code == 200 and res.json()["command_id"] is None
    assert signals_store.get_signal(sid)["status"] == "rejected"
    assert api.commands == []


def test_an_approval_on_a_stopped_instance_is_a_503_and_stays_pending(api):
    sid = signal(instance_id="stopped")
    res = api.post(decision_url(sid, "stopped"), json={"decision": "approve"})
    assert res.status_code == 503 and "not running" in res.json()["detail"]
    assert signals_store.get_signal(sid)["status"] == "pending"
    assert api.commands == []


def test_a_command_that_cannot_be_queued_is_a_503_and_puts_the_signal_back(api, monkeypatch):
    def down(*a, **k):
        raise ValueError("submit_command failed: database unavailable")

    monkeypatch.setattr(interactive_utils, "action_submit_live_command", down)
    sid = signal()
    res = api.post(decision_url(sid), json={"decision": "approve"})
    assert res.status_code == 503 and "pending again" in res.json()["detail"]
    assert signals_store.get_signal(sid)["status"] == "pending"


def test_a_click_that_loses_the_race_is_a_409_and_queues_nothing(api, monkeypatch):
    sid = signal()
    # Another click decided between this one's read and its compare-and-swap.
    monkeypatch.setattr(signals_store, "cas_signal", lambda *a, **k: False)
    res = api.post(decision_url(sid), json={"decision": "approve"})
    assert res.status_code == 409 and "another click" in res.json()["detail"]
    assert api.commands == []


def test_unknown_and_foreign_signals_are_404(api):
    assert api.post(decision_url("nope"), json={"decision": "approve"}).status_code == 404
    foreign = signal(instance_id="other")
    assert api.post(decision_url(foreign), json={"decision": "approve"}).status_code == 404
    assert signals_store.get_signal(foreign)["status"] == "pending"
    assert api.post(decision_url(foreign, "nope"), json={"decision": "approve"}).status_code == 404


@pytest.mark.parametrize("body", [{}, {"decision": "maybe"}, {"reason": "x"},
                                  {"decision": 5}, {"decision": "approve", "reason": 3}])
def test_a_malformed_body_is_422(api, body):
    sid = signal()
    assert api.post(decision_url(sid), json=body).status_code == 422
    assert signals_store.get_signal(sid)["status"] == "pending"


# -- GET wheel ---------------------------------------------------------------------

def occ(u, strike, expiry, kind="P"):
    return f"{u}{expiry[2:4]}{expiry[5:7]}{expiry[8:10]}{kind}{int(strike * 1000):08d}"


def option_row(u, strike, expiry, qty, *, entry=1.23, last=0.85, pnl=38.0, meta=True, kind="P"):
    row = {"symbol": occ(u, strike, expiry, kind), "qty": float(qty), "avg_entry_price": entry,
           "last_price": last, "market_value": None if last is None else last * 100 * qty,
           "unrealized_pnl": pnl, "unrealized_pnl_pct": None, "asset_class": "us_option",
           "side": "short" if qty < 0 else "long", "multiplier": 100,
           "underlying": None, "strike": None, "expiry": None}
    if meta:                         # A-live Task 14 fills these from Alpaca's contract
        row.update(underlying=u, strike=strike, expiry=expiry)
    return row


BOOK = {"cash": 25_000.0, "equity": 60_000.0, "broker_fetch_error": None, "positions": [
    option_row("APH", 130.0, "2026-10-02", -1),
    option_row("KO", 60.0, "2026-10-02", -2, last=None, pnl=None, meta=False),
    option_row("GIS", 55.0, "2026-10-09", -1),
    option_row("MSFT", 450.0, "2026-10-02", -1, kind="C"),
    {"symbol": "AAPL", "qty": 10.0, "avg_entry_price": 200.0, "last_price": 210.0,
     "market_value": 2100.0, "unrealized_pnl": 100.0, "unrealized_pnl_pct": 5.0}]}


def test_the_wheel_book_has_exactly_the_pinned_shape(api, monkeypatch):
    monkeypatch.setattr(live_broker_fetch, "fetch_broker_live_state", lambda conn, iid: BOOK)
    monkeypatch.setattr(interactive_utils, "_wheel_underlying_prices",
                        lambda iid, symbols: {"APH": 127.4, "KO": 63.0})
    base = datetime(2026, 9, 1, tzinfo=timezone.utc)
    for i in range(25):
        signals_store.insert_wheel_scan({
            "instance_id": IID, "session": "2026-09-21", "symbol": f"S{i:02d}",
            "created_at": (base + timedelta(minutes=i)).isoformat(), "stock_price": 50.0,
            "strike": 48.0, "expiry": "2026-10-02", "premium_est": 0.5, "score": 70,
            "recommendation": "REVIEW", "reasoning": "r", "status": "pending",
            "skip_reason": None})
    monkeypatch.setattr(interactive_utils, "_ny_today", lambda: date(2026, 9, 24))
    res = api.get(f"/instances/{IID}/wheel")
    assert res.status_code == 200
    body = res.json()
    assert list(body) == ["open_puts", "collateral_total", "cash", "recent_scans"]
    aph, ko, gis = body["open_puts"]
    assert aph == {"contract": "APH261002P00130000", "underlying": "APH", "strike": 130.0,
                   "expiry": "2026-10-02", "qty": 1, "avg_entry_price": 1.23,
                   "current_price": 0.85, "underlying_price": 127.4, "itm_pct": 2.0,
                   "dte": 8, "collateral": 13000.0, "unrealized_pl": 38.0}
    # No contract fields from Alpaca: the OCC symbol names them. Out of the money: negative.
    assert (ko["underlying"], ko["strike"], ko["expiry"], ko["qty"]) == ("KO", 60.0,
                                                                        "2026-10-02", 2)
    assert ko["itm_pct"] == -5.0 and ko["collateral"] == 12000.0
    assert ko["current_price"] is None and ko["unrealized_pl"] is None
    # No underlying quote: null, never 0.0.
    assert gis["underlying_price"] is None and gis["itm_pct"] is None and gis["dte"] == 15
    assert body["collateral_total"] == 30500.0 and body["cash"] == 25000.0
    scans = body["recent_scans"]
    assert len(scans) == 20 and scans[0]["symbol"] == "S24" and scans[-1]["symbol"] == "S05"


def test_an_unreadable_broker_is_503_not_an_empty_book(api, monkeypatch):
    monkeypatch.setattr(live_broker_fetch, "fetch_broker_live_state",
                        lambda conn, iid: {"broker_fetch_error": "broker_api_error: timeout"})
    res = api.get(f"/instances/{IID}/wheel")
    assert res.status_code == 503 and "timeout" in res.json()["detail"]
    assert api.get("/instances/nope/wheel").status_code == 404


def test_underlying_prices_use_the_instance_credentials(monkeypatch):
    seen = {}
    monkeypatch.setattr(live_broker_fetch, "_load_credentials",
                        lambda iid: {"error": None, "key": "k", "secret": "s"})
    monkeypatch.setattr(market_data, "data_client",
                        lambda k, s: seen.setdefault("creds", (k, s)) and "client")
    monkeypatch.setattr(market_data, "live_prices",
                        lambda syms, client=None, **kw: {s: 1.0 for s in syms})
    assert interactive_utils._wheel_underlying_prices(IID, ["APH"]) == {"APH": 1.0}
    assert seen["creds"] == ("k", "s")
    monkeypatch.setattr(live_broker_fetch, "_load_credentials",
                        lambda iid: {"error": "instance_not_found"})
    assert interactive_utils._wheel_underlying_prices(IID, ["APH"]) == {}


# -- GET calibration ---------------------------------------------------------------

def test_calibration_reports_this_instance(api):
    res = api.get(f"/instances/{IID}/swing/calibration")
    assert res.status_code == 200
    assert set(res.json()) == {"swing", "wheel", "gate"}
    assert res.json()["gate"]["met"] is False
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest backend/tests/test_swing_api.py -q -p no:cacheprovider`
Expected: FAIL. The routes do not exist yet (404 where 200 is expected), and `interactive_utils._wheel_underlying_prices` raises `AttributeError`.

- [ ] **Step 3: Add the actions**

In `backend/interactive_utils.py`, directly after `action_get_live_command` (the function that ends `raise LiveInstanceNotFoundError("Live command not found: %s" % command_id)` / `return doc`), insert:

```python
# --- swing-trader port: signals, decisions, the wheel book (plan B Task 21) ---
# The swing and wheel lanes write SwingSignals / SwingWheelScans (swing_trader.
# signals_store). An approval is final after one compare-and-swap and reaches
# the broker as a submit_order LiveCommand {"source": "swing_approval",
# "signal_id"} that plan A-live's handler rebuilds at the live price.

class SwingBrokerUnavailableError(RuntimeError):
    """The broker cannot be reached: the wheel book is unknown (an empty book
    would read as "no open puts"), or an approval cannot be delivered. The
    routes answer 503; the signal stays pending, so a retry later works."""


class SwingDecisionRaceError(RuntimeError):
    """This click lost the compare-and-swap to a concurrent decision (409)."""


def _swing_instance(conn, instance_id):
    inst = _resolve_instance_doc(conn, instance_id) if instance_id else None
    if inst is None:
        raise LookupError("Instance not found: %s" % instance_id)
    return inst, str(inst.get("id", instance_id))


def _ny_today():
    from swing_trader import clock
    return datetime.date.fromisoformat(
        clock.ny_date(datetime.datetime.now(datetime.timezone.utc)))


def action_swing_list_signals(conn, instance_id, status=None, limit=100):
    """{"signals": [...]} newest first (interfaces §9 item 1)."""
    from swing_trader import signals_store
    _inst, real_id = _swing_instance(conn, instance_id)
    return {"signals": signals_store.list_signals(real_id, status=(status or None),
                                                  limit=limit)}


def action_swing_decide_signal(conn, instance_id, signal_id, decision, reason=None,
                               decided_by=None):
    """Decide a pending signal (interfaces §9 item 2). Not pending:
    SignalConflict, a ValueError (400). Unknown or another instance's:
    LookupError (404). Lost the compare-and-swap: SwingDecisionRaceError
    (409). An approval that cannot reach the broker (instance not running,
    command not queued): SwingBrokerUnavailableError (503), and the signal
    stays pending. A rejection needs no broker."""
    from swing_trader import approvals, signals_store
    inst, real_id = _swing_instance(conn, instance_id)
    signal = signals_store.get_signal(signal_id)
    if not signal or str(signal.get("instance_id") or "") != real_id:
        raise LookupError("Signal not found: %s" % signal_id)
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    decided = approvals.decide(signal, decision, decided_by, reason, now_iso)
    approving = decided["status"] in approvals.APPROVED_STATUSES
    if approving and not inst.get("runCommand", False):
        raise SwingBrokerUnavailableError(
            "Instance %s is not running, so the approval could not reach the broker; "
            "the signal stays pending" % real_id)
    if not signals_store.cas_signal(signal_id, expect_status="pending", doc=decided):
        raise SwingDecisionRaceError("signal %s was decided first by another click; "
                                     "decisions are final" % signal_id)
    out = {"signal": decided, "command_id": None}
    if approving:
        try:
            cmd = action_submit_live_command(
                conn, real_id, "submit_order",
                {"source": "swing_approval", "signal_id": str(signal_id)}, decided_by)
        except Exception as exc:
            # Undo the decision so the operator can approve again once the
            # broker is reachable. Only a row still in the decided status is
            # put back.
            signals_store.cas_signal(signal_id, expect_status=decided["status"], doc=signal)
            raise SwingBrokerUnavailableError(
                "the approval could not be queued for the broker (%s); the signal is "
                "pending again" % exc)
        out["command_id"] = (cmd or {}).get("command_id")
    return out


def _wheel_underlying_prices(instance_id, symbols):
    """{underlying: price} from the instance's own Alpaca data credentials:
    the IEX latest trade, then yfinance (ST app.py _fetch_live_price). A symbol
    with no quote is absent, and the row reports null."""
    import live_broker_fetch as _lbf
    from swing_trader import market_data
    wanted = sorted({str(s).upper() for s in (symbols or []) if s})
    if not wanted:
        return {}
    creds = _lbf._load_credentials(str(instance_id))
    if creds.get("error"):
        return {}
    try:
        client = market_data.data_client(creds.get("key"), creds.get("secret"))
    except RuntimeError:
        return {}
    try:
        return {s: float(p) for s, p in (market_data.live_prices(wanted, client=client)
                                         or {}).items() if p is not None}
    except Exception:
        return {}


def action_wheel_overview(conn, instance_id, *, today=None):
    """The wheel book (interfaces §9 item 3): short puts from broker truth,
    ITM % signed (negative out of the money, null without an underlying
    quote), collateral, cash, and the 20 newest scan rows."""
    import live_broker_fetch as _lbf
    from swing_trader import signals_store, wheel_rules
    _inst, real_id = _swing_instance(conn, instance_id)
    state = _lbf.fetch_broker_live_state(conn, real_id) or {}
    if state.get("broker_fetch_error"):
        raise SwingBrokerUnavailableError("broker unavailable: %s" % state["broker_fetch_error"])
    today = today or _ny_today()
    puts = []
    for p in state.get("positions") or []:
        parsed = wheel_rules.occ_parts(p.get("symbol"))
        kind = str(p.get("option_type") or (parsed[2] if parsed else "")).lower()
        try:
            qty = float(p.get("qty") or 0.0)
        except (TypeError, ValueError):
            continue
        if parsed is None or qty >= 0 or kind != "put":
            continue
        underlying = str(p.get("underlying") or parsed[0]).upper()
        strike = float(p.get("strike") if p.get("strike") is not None else parsed[3])
        expiry = str(p.get("expiry") or parsed[1])[:10]
        puts.append((p, underlying, strike, expiry, int(round(abs(qty)))))
    prices = _wheel_underlying_prices(real_id, [u for _p, u, _s, _e, _q in puts])
    rows = []
    for p, underlying, strike, expiry, n in puts:
        spot = prices.get(underlying)
        rows.append({
            "contract": str(p.get("symbol")).upper(),
            "underlying": underlying,
            "strike": strike,
            "expiry": expiry,
            "qty": n,
            "avg_entry_price": p.get("avg_entry_price"),
            "current_price": p.get("last_price"),
            "underlying_price": spot,
            "itm_pct": (round((strike - spot) / strike * 100.0, 2)
                        if spot is not None and strike > 0 else None),
            "dte": (datetime.date.fromisoformat(expiry) - today).days,
            "collateral": round(strike * 100.0 * n, 2),
            "unrealized_pl": p.get("unrealized_pnl"),
        })
    return {"open_puts": rows,
            "collateral_total": round(sum(r["collateral"] for r in rows), 2),
            "cash": state.get("cash"),
            "recent_scans": signals_store.list_wheel_scans(real_id, limit=20)}


def action_swing_calibration(conn, instance_id):
    from swing_trader import calibration
    _inst, real_id = _swing_instance(conn, instance_id)
    return calibration.calibration_report(real_id)
```

- [ ] **Step 4: Add the routes**

In `backend/api/main.py`, in the `from interactive_utils import (...)` block, replace:

```python
    action_delete_push_device,
    action_list_push_devices,
)
```

with:

```python
    action_delete_push_device,
    action_list_push_devices,
    SwingBrokerUnavailableError,
    SwingDecisionRaceError,
    action_swing_list_signals,
    action_swing_decide_signal,
    action_wheel_overview,
    action_swing_calibration,
)
```

Then, directly after `api_get_live_command` (the route whose body is `return _run(action_get_live_command, conn, command_id)`), insert:

```python
# --- swing-trader port (plan B Task 21; interfaces §8, §9) ---
#
# GET  /instances/{id}/swing/signals?status=     — review queue (web + iOS)
# POST /instances/{id}/swing/signals/{sid}/decision — approve / approve_half / reject
# GET  /instances/{id}/wheel                     — open puts, collateral, recent scans
# GET  /instances/{id}/swing/calibration         — score buckets vs outcomes


class SwingDecisionBody(BaseModel):
    decision: str = Field(pattern="^(approve|approve_half|reject)$")
    reason: Optional[str] = None      # both UIs omit it when blank


@app.get("/instances/{instance_id}/swing/signals", response_class=JSONResponse)
def api_swing_list_signals(instance_id: str, status: Optional[str] = None, limit: int = 100,
                           conn=Depends(conn_dependency),
                           current_user: dict = Depends(get_current_user)):
    return _run(action_swing_list_signals, conn, instance_id, status, limit)


@app.post("/instances/{instance_id}/swing/signals/{signal_id}/decision",
          response_class=JSONResponse)
def api_swing_decide_signal(instance_id: str, signal_id: str, body: SwingDecisionBody,
                            conn=Depends(conn_dependency),
                            current_user: dict = Depends(get_current_user)):
    """A decision is final. 400: the signal is no longer pending. 404: unknown,
    or another instance's. 409: a concurrent click won. 503: the approval
    cannot reach the broker (the signal stays pending; retry later). 422: a
    malformed body. An approval queues a submit_order LiveCommand that the
    broker rebuilds at the live price."""
    try:
        return _run(action_swing_decide_signal, conn, instance_id, signal_id,
                    body.decision, body.reason,
                    str(current_user.get("username") or current_user.get("id")
                        or "operator"))
    except SwingDecisionRaceError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except SwingBrokerUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@app.get("/instances/{instance_id}/wheel", response_class=JSONResponse)
def api_wheel_overview(instance_id: str, conn=Depends(conn_dependency),
                       current_user: dict = Depends(get_current_user)):
    try:
        return _run(action_wheel_overview, conn, instance_id)
    except SwingBrokerUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@app.get("/instances/{instance_id}/swing/calibration", response_class=JSONResponse)
def api_swing_calibration(instance_id: str, conn=Depends(conn_dependency),
                          current_user: dict = Depends(get_current_user)):
    return _run(action_swing_calibration, conn, instance_id)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python3 -m pytest backend/tests/test_swing_api.py backend/tests/test_api_authorization.py backend/tests/test_api_notification_prefs.py -q -p no:cacheprovider`
Expected: PASS. `test_swing_api.py` has 19 tests. `test_api_authorization.py` gains a case for the new POST route in its inverse test (401 without a session) and still passes.

- [ ] **Step 6: Commit**

Run `gitnexus_detect_changes()`. Expect the new functions in `interactive_utils.py`, the new routes and body in `api/main.py`, and the new test file. The fingerprint tuple must be untouched by this commit.

```bash
git add backend/interactive_utils.py backend/api/main.py backend/tests/test_swing_api.py
git commit -m "feat(api): swing signals, decisions, the wheel book and calibration

The review queue lists a lane's signals newest first. A decision is final
after one compare-and-swap. A second click, or a click on a submitted
signal, is a 400 that queues nothing, and a click that loses the race is a
409. An approval queues one swing_approval broker command. While the
instance is stopped or the command cannot be queued it is a 503, and the
signal stays pending. The wheel
route reports short puts from broker truth with a signed ITM percentage,
null without a quote, and answers 503 rather than an empty book when the
broker cannot be read.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016ztXjLWXu61Dpp4XMU2jsS"
```

---

### Task 22: Reference-data builder — VIX, S&P membership, sectors (B15, part 1)

**Files:**
- Create: `scripts/build_swing_reference_data.py`
- Test: `backend/tests/test_build_swing_reference_data.py`

**Interfaces:**
- Consumes: Task 6 (`refdata.MACRO_TABLE/MEMBERSHIP_TABLE/SECTOR_TABLE`, `refdata.macro_id/membership_id`, the readers `vix_before/members_before/sector_map`; `sectors._SECTOR_OVERRIDES/normalize_sector`; `universe.norm_symbol`), Task 2 (`DEFENSIVE_UNIVERSE`), Task 1 (the tables).
- Produces: the rows the backtest path reads, in the interfaces §5 shapes. `SwingMacroDaily` rows are `{"id": "VIX|YYYY-MM-DD", "series": "VIX", "date", "close", "source": "cboe"|"fred"}`. `SwingIndexMembership` rows are `{"id": "SPX|YYYY-MM-DD", "index": "SPX", "date", "members": [...]}`, one per change date: every change on or after `--start`, plus the one in effect at `--start`. `SwingSectorMap` rows are `{"id": SYMBOL, "symbol", "sector", "as_of", "source": "yfinance"|"override"}`.
  - Script functions: `parse_cboe_vix(text)`, `parse_fred_vix(text)`, `vix_rows(points, source, start)`, `load_vix(fetch, start)`, `parse_membership(text, rename=None)`, `membership_rows(changes, start)`, `members_union(rows)`, `sector_rows(symbols, *, sector_of, as_of)`, `main(argv=None, *, store=None, fetch=None, sector_of=None, today=None)`.
  - Constants: `CBOE_VIX_URL`, `FRED_VIX_URL`, `DEFAULT_START = "2019-01-01"`, `BENCHMARKS`, `RENAME_MAP`.

**Sources (spec §7).**
- **VIX.** Cboe's `VIX_History.csv` (`DATE` in MM/DD/YYYY, `CLOSE`). When Cboe fails or returns nothing, FRED's `VIXCLS` graph CSV, which writes `.` for a holiday.
- **Membership.** fja05680/sp500's "S&P 500 Historical Components & Changes(MM-DD-YYYY).csv": columns `date,tickers`, one row per change date, carrying the full member list. The file name carries its own date and changes with every update, so the path or URL is a required argument, never a baked-in default. `RENAME_MAP` carries a continuing company's old ticker to the symbol Alpaca's bars use today. Acquired or delisted names stay as they are: they have no bars after they leave, and the engine skips a symbol with no bars.
- **Sectors.** yfinance `info.sector` for every member since `--start`, plus SPY, QQQ and the defensive ETFs, normalized the way ST normalized (`sectors.normalize_sector`). ST's overrides win. A symbol with no yfinance sector (ETFs, most delisted names) gets no row. It reads `"unknown"`, which ST's sector check lets through, and a rerun retries it.

Every row has a deterministic id and is written with `conflict="replace"`, so a rerun is idempotent. The builder needs no broker credentials.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_build_swing_reference_data.py`:

```python
"""The reference-data builder writes exactly what the backtest readers read
(spec §7). No network: every fetch is injected."""
import importlib.util
import os
import sys
from datetime import date

import pytest

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ROOT = os.path.dirname(_BACKEND)
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from swing_trader import refdata  # noqa: E402


def _script():
    path = os.path.join(_ROOT, "scripts", "build_swing_reference_data.py")
    spec = importlib.util.spec_from_file_location("_swing_refdata_builder", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


CBOE = ("DATE,OPEN,HIGH,LOW,CLOSE\n"
        "12/31/2018,26.0,26.5,25.1,25.42\n"
        "01/02/2019,27.54,28.53,23.1,23.22\n"
        "01/03/2019,23.22,25.73,22.8,25.45\n"
        "bad row\n")
FRED = "observation_date,VIXCLS\n2019-01-02,23.22\n2019-01-21,.\n2019-01-22,20.8\n"
MEMBERSHIP = ('date,tickers\n'
              '2018-12-20,"AAPL,BRK.B,FB,HRS"\n'
              '2019-06-03,"AAPL,BRK.B,DD,FB,HRS"\n'
              '2022-06-09,"AAPL,BRK.B,DD,LHX,META"\n')


def fetcher(pages):
    def fetch(url):
        if url not in pages:
            raise AssertionError(f"unexpected fetch {url}")
        page = pages[url]
        if isinstance(page, Exception):
            raise page
        return page
    return fetch


def test_cboe_closes_are_dated_and_bad_rows_dropped():
    b = _script()
    assert b.parse_cboe_vix(CBOE) == [("2018-12-31", 25.42), ("2019-01-02", 23.22),
                                      ("2019-01-03", 25.45)]
    rows = b.vix_rows(b.parse_cboe_vix(CBOE), "cboe", "2019-01-01")
    assert rows[0] == {"id": "VIX|2019-01-02", "series": "VIX", "date": "2019-01-02",
                       "close": 23.22, "source": "cboe"}
    assert len(rows) == 2


def test_fred_is_the_fallback_and_skips_holidays():
    b = _script()
    fetch = fetcher({b.CBOE_VIX_URL: RuntimeError("503"), b.FRED_VIX_URL: FRED})
    rows = b.load_vix(fetch, "2019-01-01")
    assert [(r["date"], r["close"], r["source"]) for r in rows] == [
        ("2019-01-02", 23.22, "fred"), ("2019-01-22", 20.8, "fred")]
    with pytest.raises(SystemExit):
        b.load_vix(fetcher({b.CBOE_VIX_URL: "DATE,CLOSE\n", b.FRED_VIX_URL: "x\n"}),
                   "2019-01-01")


def test_membership_keeps_the_row_in_effect_at_start_and_renames():
    b = _script()
    changes = b.parse_membership(MEMBERSHIP)
    assert changes[0] == ("2018-12-20", ["AAPL", "BRK.B", "LHX", "META"])
    rows = b.membership_rows(changes, "2019-01-01")
    assert [r["id"] for r in rows] == ["SPX|2018-12-20", "SPX|2019-06-03", "SPX|2022-06-09"]
    assert rows[1] == {"id": "SPX|2019-06-03", "index": "SPX", "date": "2019-06-03",
                       "members": ["AAPL", "BRK.B", "DD", "LHX", "META"]}
    assert b.membership_rows(changes, "2020-01-01")[0]["date"] == "2019-06-03"
    assert b.members_union(rows) == ["AAPL", "BRK.B", "DD", "LHX", "META"]
    # A dash spelling is Alpaca's dot, and a duplicate after renaming is dropped.
    assert b.parse_membership('date,tickers\n2020-01-02,"BRK-B,FB,META"\n') == [
        ("2020-01-02", ["BRK.B", "META"])]


def test_overrides_win_and_a_missing_sector_writes_no_row():
    b = _script()
    rows, unknown = b.sector_rows(
        ["AAPL", "SPY", "XLP", "brk-b"], as_of="2026-09-24",
        sector_of={"AAPL": "Technology", "BRK.B": "Financial Services", "XLP": None}.get)
    assert rows == [
        {"id": "AAPL", "symbol": "AAPL", "sector": "technology", "as_of": "2026-09-24",
         "source": "yfinance"},
        {"id": "BRK.B", "symbol": "BRK.B", "sector": "financial_services",
         "as_of": "2026-09-24", "source": "yfinance"},
        {"id": "SPY", "symbol": "SPY", "sector": "broad_market", "as_of": "2026-09-24",
         "source": "override"}]
    assert unknown == ["XLP"]


def test_the_build_is_idempotent_and_the_readers_see_it(store, tmp_path):
    b = _script()
    csv_path = tmp_path / "sp500.csv"
    csv_path.write_text(MEMBERSHIP, encoding="utf-8")
    fetch = fetcher({b.CBOE_VIX_URL: CBOE})
    sector_calls = []

    def sector_of(symbol):
        sector_calls.append(symbol)
        return {"AAPL": "Technology", "DD": "Basic Materials"}.get(symbol)

    argv = ["--start", "2019-01-01", "--membership-csv", str(csv_path)]
    for _ in range(2):
        assert b.main(argv, store=store, fetch=fetch, sector_of=sector_of,
                      today=date(2026, 9, 24)) == 0
    assert len(store.get_all(refdata.MACRO_TABLE, "VIX|2019-01-02", "VIX|2019-01-03")) == 2
    assert refdata.vix_before(store, "2019-01-03") == (23.22, None)
    assert refdata.members_before(store, "2019-06-03") == ["AAPL", "BRK.B", "LHX", "META"]
    assert "DD" in refdata.members_before(store, "2019-06-04")
    smap = refdata.sector_map(store, ["AAPL", "DD", "SPY", "META"])
    assert smap == {"AAPL": "technology", "DD": "basic_materials", "SPY": "broad_market"}
    assert "SPY" not in sector_calls and "XLP" in sector_calls      # defensive ETFs asked


def test_only_vix_needs_no_membership_file(store):
    b = _script()
    assert b.main(["--only", "vix"], store=store, fetch=fetcher({b.CBOE_VIX_URL: CBOE}),
                  sector_of=lambda s: None) == 0
    with pytest.raises(SystemExit) as exit_info:
        b.main(["--only", "membership"], store=store, fetch=fetcher({}),
               sector_of=lambda s: None)
    assert exit_info.value.code == 2
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest backend/tests/test_build_swing_reference_data.py -q -p no:cacheprovider`
Expected: FAIL, `FileNotFoundError` for `scripts/build_swing_reference_data.py`.

- [ ] **Step 3: Write the builder**

Create `scripts/build_swing_reference_data.py`:

```python
#!/usr/bin/env python3
"""Build the swing-trader reference tables (spec §7).

    python3 scripts/build_swing_reference_data.py \\
        --membership-csv "S&P 500 Historical Components & Changes(09-01-2026).csv"
    python3 scripts/build_swing_reference_data.py --only vix

SwingMacroDaily       VIX closes from Cboe's VIX_History.csv; FRED VIXCLS when
                      Cboe fails.
SwingIndexMembership  S&P 500 members by change date from fja05680/sp500's
                      "S&P 500 Historical Components & Changes(MM-DD-YYYY).csv"
                      (take the newest from github.com/fja05680/sp500; the name
                      carries its date, so it is an argument, never a default).
                      RENAME_MAP carries old tickers to the symbols Alpaca's
                      bars use.
SwingSectorMap        yfinance info.sector for every member since --start plus
                      SPY, QQQ and the defensive ETFs, normalised as ST did;
                      ST's overrides win. Static, NOT point-in-time.

Idempotent: deterministic ids, conflict="replace". No broker credentials.
"""
from __future__ import annotations

import argparse
import csv
import io
import os
import sys
import time
import urllib.request
from datetime import date, datetime

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "backend"))

from swing_trader import refdata, sectors  # noqa: E402
from swing_trader.constants import DEFENSIVE_UNIVERSE  # noqa: E402
from swing_trader.universe import norm_symbol  # noqa: E402

CBOE_VIX_URL = "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"
FRED_VIX_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=VIXCLS"
DEFAULT_START = "2019-01-01"
BENCHMARKS = ("SPY", "QQQ")

#: A continuing company's old S&P ticker -> the symbol Alpaca's bars carry it
#: under today. Only ticker changes; an acquired or delisted name is left as
#: it is (it has no bars after it leaves, and the engine skips it). Verify
#: against Alpaca before trusting a window that spans one of these dates.
RENAME_MAP = {
    "ABC": "COR",      # AmerisourceBergen -> Cencora, 2023-08-30
    "ANTM": "ELV",     # Anthem -> Elevance Health, 2022-06-28
    "ARNC": "HWM",     # Arconic Inc. -> Howmet Aerospace, 2020-04-01
    "BBT": "TFC",      # BB&T -> Truist, 2019-12-09
    "BLL": "BALL",     # Ball Corp, 2022-05-17
    "CBS": "PARA",     # CBS -> ViacomCBS (VIAC) 2019-12-05 -> Paramount 2022-02-16
    "CDAY": "DAY",     # Ceridian -> Dayforce, 2024-02-01
    "COG": "CTRA",     # Cabot Oil & Gas -> Coterra, 2021-10-01
    "CTL": "LUMN",     # CenturyLink -> Lumen, 2020-09-18
    "DISCA": "WBD",    # Discovery -> Warner Bros. Discovery, 2022-04-11
    "DWDP": "DD",      # DowDuPont -> DuPont de Nemours, 2019-06-03
    "FB": "META",      # Facebook -> Meta Platforms, 2022-06-09
    "FBHS": "FBIN",    # Fortune Brands Home & Security -> Innovations, 2022-12-15
    "FISV": "FI",      # Fiserv, 2023-06-07
    "FLT": "CPAY",     # FleetCor -> Corpay, 2024-03-25
    "HCP": "DOC",      # HCP -> Healthpeak (PEAK) 2019-11-05 -> DOC 2024-03-04
    "HRS": "LHX",      # Harris -> L3Harris, 2019-07-01
    "JEC": "J",        # Jacobs Engineering, 2019-12-10
    "LB": "BBWI",      # L Brands -> Bath & Body Works, 2021-08-03
    "MYL": "VTRS",     # Mylan -> Viatris, 2020-11-16
    "NLOK": "GEN",     # NortonLifeLock -> Gen Digital, 2022-11-08
    "PEAK": "DOC",     # Healthpeak, 2024-03-04
    "PKI": "RVTY",     # PerkinElmer -> Revvity, 2023-05-16
    "RE": "EG",        # Everest Re -> Everest Group, 2023-07-10
    "SYMC": "GEN",     # Symantec -> NortonLifeLock (NLOK) 2019-11-04 -> GEN
    "UTX": "RTX",      # United Technologies -> Raytheon Technologies, 2020-04-03
    "VIAC": "PARA",    # ViacomCBS -> Paramount Global, 2022-02-16
    "WLTW": "WTW",     # Willis Towers Watson, 2022-01-04
}


def fetch_text(url, *, attempts=4, timeout=60) -> str:
    last = None
    for attempt in range(attempts):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "IntelliStock swing refdata"})
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return response.read().decode("utf-8-sig")
        except Exception as exc:
            last = exc
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"fetch failed: {url} ({type(last).__name__}: {last})")


def read_source(path_or_url, fetch) -> str:
    if str(path_or_url).startswith(("http://", "https://")):
        return fetch(path_or_url)
    with open(path_or_url, encoding="utf-8-sig") as fh:
        return fh.read()


def parse_cboe_vix(text) -> list:
    out = []
    for row in csv.DictReader(io.StringIO(text)):
        try:
            d = datetime.strptime(str(row.get("DATE") or "").strip(), "%m/%d/%Y").date()
            close = float(str(row.get("CLOSE") or "").strip())
        except ValueError:
            continue
        if close > 0:
            out.append((d.isoformat(), close))
    return sorted(out)


def parse_fred_vix(text) -> list:
    out = []
    reader = csv.reader(io.StringIO(text))
    next(reader, None)                                   # observation_date,VIXCLS
    for row in reader:
        if len(row) < 2:
            continue
        try:
            d = date.fromisoformat(row[0].strip())
            close = float(row[1])
        except ValueError:
            continue                                     # FRED writes "." for a holiday
        if close > 0:
            out.append((d.isoformat(), close))
    return sorted(out)


def vix_rows(points, source, start) -> list:
    return [{"id": refdata.macro_id("VIX", d), "series": "VIX", "date": d, "close": close,
             "source": source} for d, close in points if d >= start]


def load_vix(fetch, start) -> list:
    try:
        rows = vix_rows(parse_cboe_vix(fetch(CBOE_VIX_URL)), "cboe", start)
        if rows:
            return rows
        reason = "no rows"
    except Exception as exc:
        reason = f"{type(exc).__name__}: {exc}"
    print(f"Cboe VIX unavailable ({reason}); falling back to FRED VIXCLS", flush=True)
    try:
        rows = vix_rows(parse_fred_vix(fetch(FRED_VIX_URL)), "fred", start)
    except Exception as exc:
        raise SystemExit(f"no VIX from Cboe or FRED ({type(exc).__name__}: {exc})")
    if not rows:
        raise SystemExit("no VIX rows from Cboe or FRED")
    return rows


def parse_membership(text, rename=None) -> list:
    rename = RENAME_MAP if rename is None else rename
    out = []
    for row in csv.DictReader(io.StringIO(text)):
        try:
            d = date.fromisoformat(str(row.get("date") or "").strip()[:10])
        except ValueError:
            continue
        members = set()
        for raw in str(row.get("tickers") or "").split(","):
            sym = norm_symbol(raw)
            if sym:
                members.add(rename.get(sym, sym))
        if members:
            out.append((d.isoformat(), sorted(members)))
    return sorted(out)


def membership_rows(changes, start) -> list:
    """Every change dated on or after `start`, plus the one in effect at it."""
    before = [c for c in changes if c[0] < start]
    keep = ([before[-1]] if before else []) + [c for c in changes if c[0] >= start]
    return [{"id": refdata.membership_id("SPX", d), "index": "SPX", "date": d,
             "members": list(members)} for d, members in keep]


def members_union(rows) -> list:
    return sorted({s for r in rows for s in r["members"]})


def yf_sector(symbol):
    import yfinance as yf
    return (yf.Ticker(str(symbol).replace(".", "-")).info or {}).get("sector")


def sector_rows(symbols, *, sector_of, as_of):
    rows, unknown = [], []
    for sym in sorted({norm_symbol(s) for s in symbols if str(s or "").strip()}):
        if sym in sectors._SECTOR_OVERRIDES:
            rows.append({"id": sym, "symbol": sym, "sector": sectors._SECTOR_OVERRIDES[sym],
                         "as_of": as_of, "source": "override"})
            continue
        try:
            raw = sector_of(sym)
        except Exception:
            raw = None
        if not raw:
            unknown.append(sym)
            continue
        rows.append({"id": sym, "symbol": sym, "sector": sectors.normalize_sector(raw),
                     "as_of": as_of, "source": "yfinance"})
    return rows, unknown


def main(argv=None, *, store=None, fetch=None, sector_of=None, today=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--only", action="append", choices=("vix", "membership", "sectors"),
                    help="build only this table (repeatable); default all three")
    ap.add_argument("--start", default=DEFAULT_START)
    ap.add_argument("--membership-csv",
                    help="path or URL of fja05680's historical-components CSV")
    args = ap.parse_args(argv)
    parts = set(args.only or ("vix", "membership", "sectors"))
    if parts & {"membership", "sectors"} and not args.membership_csv:
        ap.error("--membership-csv is required to build membership or sectors")
    if store is None:
        from db import schema as dbschema
        from db import store as db_store
        dbschema.ensure_schema(tables=[refdata.MACRO_TABLE, refdata.MEMBERSHIP_TABLE,
                                       refdata.SECTOR_TABLE])
        store = db_store
    fetch = fetch or fetch_text
    sector_of = sector_of or yf_sector
    as_of = (today or date.today()).isoformat()

    if "vix" in parts:
        rows = load_vix(fetch, args.start)
        store.insert(refdata.MACRO_TABLE, rows, conflict="replace")
        print(f"VIX: {len(rows)} rows {rows[0]['date']}..{rows[-1]['date']} "
              f"({rows[0]['source']})", flush=True)

    if parts & {"membership", "sectors"}:
        changes = parse_membership(read_source(args.membership_csv, fetch))
        rows = membership_rows(changes, args.start)
        if not rows:
            raise SystemExit("the membership CSV has no dated rows")
        if "membership" in parts:
            store.insert(refdata.MEMBERSHIP_TABLE, rows, conflict="replace")
            union = members_union(rows)
            applied = sorted(f"{old}->{new}" for old, new in RENAME_MAP.items()
                             if new in union)
            print(f"membership: {len(rows)} change dates {rows[0]['date']}..{rows[-1]['date']}, "
                  f"{len(union)} distinct members; renames in play: {', '.join(applied)}",
                  flush=True)
        if "sectors" in parts:
            symbols = members_union(rows) + list(BENCHMARKS) + list(DEFENSIVE_UNIVERSE)
            srows, unknown = sector_rows(symbols, sector_of=sector_of, as_of=as_of)
            store.insert(refdata.SECTOR_TABLE, srows, conflict="replace")
            print(f"sectors: {len(srows)} rows; {len(unknown)} without a yfinance sector "
                  f"(read as 'unknown'): {', '.join(unknown[:40])}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 -m pytest backend/tests/test_build_swing_reference_data.py -q -p no:cacheprovider`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

Run `gitnexus_detect_changes()`; expect the new script and test only.

```bash
git add scripts/build_swing_reference_data.py backend/tests/test_build_swing_reference_data.py
git commit -m "feat(swing): reference-data builder for VIX, S&P membership and sectors

Cboe VIX closes with FRED as the fallback, the fja05680 membership file by
change date with a ticker-rename map, and yfinance sectors under ST's
overrides, written with deterministic ids so a rerun is idempotent. The
backtest readers see exactly what it writes.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016ztXjLWXu61Dpp4XMU2jsS"
```

---

### Task 23: Lab and paper setup script (B15, part 2)

**Files:**
- Create: `scripts/swing_lab_setup.py`
- Test: `backend/tests/test_swing_lab_setup.py`

**Interfaces:**
- Consumes: Task 2 (`SWING_DEFAULTS`, `WHEEL_DEFAULTS`, `DEFENSIVE_UNIVERSE`), Task 6 (`refdata.MEMBERSHIP_TABLE`, `refdata.members_before`, `universe.norm_symbol`), Task 22's rows; the API through `scripts/_api.py:call` (`GET/POST/PUT /strategies`, `GET/POST /instances`, `POST /instances/{id}/link-strategy`, `POST /instances/{id}/stocks`, `GET /brokerages`); plan A-backtest's lab-document requirement `backtest_credit_pending_sell_proceeds: true`.
- Produces:
  - Constants: `LAB_DOC_NAME = "Swing trader lab"`, `LAB_INSTANCE_ID = "swing-lab"`, `PAPER_DOC_NAME = "Swing trader paper"`, `PAPER_INSTANCE_ID = "swing-paper"`, `LIVE_INSTANCE_ID = "alpaca-main"`, `CLONE_FROM = "strategy-eb"`, `PROTECTED_DOC_IDS` (`"200"`–`"203"`).
  - Functions: `assert_writable(doc_id)`, `swing_lane(*, lab)`, `wheel_lane()`, `lab_payload()`, `paper_payload()`, `lab_watchlist(store, start, end) -> list[str]`, `paper_watchlist() -> list[str]`, `assert_paper_brokerage(call, brokerage_id)`, `main(argv=None, *, call=None, store=None) -> int`.

**What it builds (spec §7, §12).**
- **The lab (default).** A document named "Swing trader lab" with one `strategy_swing` lane: enabled `SWING_DEFAULTS` plus `backtest_credit_pending_sell_proceeds: true`. Without that flag, an entry decided on the same tick as an exit is sized before the exit's proceeds exist (plan A-backtest). The lab document carries **only** the swing lane (coordinator ruling from A-backtest's pre-flight, F2). The simulator's bar hook runs after its pending-fill block, so a close-filled sell of a bracketed symbol from any other lane would execute before a same-session stop. The wheel is inert in backtests anyway (spec §1).
  - The instance is `swing-lab`: backtest-only, daily granularity (`"86400"`, the field `CreateInstanceBody` reads), on `strategy-eb`'s brokerage as the HX lab is.
  - Its watchlist is every S&P member visible on some session in `[--start, --end]`, plus SPY, QQQ and the defensive ETFs. That is the members in effect at `--start` together with every change dated before `--end`, read from `SwingIndexMembership`.
- **The paper instance (`--paper`).** A document named "Swing trader paper" with both lanes enabled at their defaults (swing at position 10, wheel at 20), and the instance `swing-paper` on `--brokerage-id`. The brokerage must be a paper account: `alpaca_paper` must be exactly `true` on `GET /brokerages`. It must not be `alpaca-main`'s brokerage. If `alpaca-main` cannot be read, the script refuses, because it cannot prove the brokerage differs. The granularity is cloned from `alpaca-main`, the one live configuration proven on this broker. The script never starts the instance: the operator does, after linking the model (spec §12).
  - The watchlist is SPY, QQQ and the defensive ETFs. The lanes fetch their own bars, and A-live marks every emitted symbol.
- **Refusals.** Docs 200–203 are refused outright in every spelling. Doc 200 is the live champion, doc 201 is the EB lab (a killed runner has left it contaminated before), and docs 202 and 203 are the frontier research documents. Rerunning finds rows by name or id and updates them in place.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_swing_lab_setup.py`:

```python
"""Transport-only tests for the swing lab and paper setup. No network."""
import importlib.util
import os
import sys

import pytest

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ROOT = os.path.dirname(_BACKEND)
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from swing_trader.constants import SWING_DEFAULTS, WHEEL_DEFAULTS  # noqa: E402


def _setup():
    """Load the script without importing scripts/_api.py (it reads .env and logs in)."""
    path = os.path.join(_ROOT, "scripts", "swing_lab_setup.py")
    spec = importlib.util.spec_from_file_location("_swing_lab_setup", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def members(store, rows):
    store.insert("SwingIndexMembership", [
        {"id": f"SPX|{d}", "index": "SPX", "date": d, "members": m} for d, m in rows],
        conflict="replace")


class Api:
    """A fake of scripts/_api.py:call. 4xx raise SystemExit, as _api does."""

    def __init__(self, docs=(), instances=None, brokerages=()):
        self.docs = list(docs)
        self.instances = dict(instances or {})
        self.brokerages = list(brokerages)
        self.seen = []

    def __call__(self, method, path, body=None, **kwargs):
        self.seen.append((method, path, body))
        if (method, path) == ("GET", "/strategies"):
            return 200, {"strategies": self.docs}
        if (method, path) == ("POST", "/strategies"):
            return 200, {"id": 900}
        if method == "PUT" and path.startswith("/strategies/"):
            return 200, {"id": path.rsplit("/", 1)[-1]}
        if (method, path) == ("GET", "/brokerages"):
            return 200, {"accounts": self.brokerages}
        if (method, path) == ("POST", "/instances"):
            self.instances[body["id"]] = dict(body)
            return 200, body
        if method == "GET" and path.startswith("/instances/"):
            iid = path.split("/")[2]
            if iid not in self.instances:
                raise SystemExit(f"HTTP 404 on GET {path}")
            return 200, dict(self.instances[iid])
        return 200, {}

    def calls(self, method, prefix=""):
        return [(p, b) for m, p, b in self.seen if m == method and p.startswith(prefix)]


def test_the_lab_lane_is_enabled_defaults_plus_the_funding_flag():
    s = _setup()
    lane = s.swing_lane(lab=True)
    assert (lane["strategy"], lane["execution_scope"], lane["decision_phase"],
            lane["execution_position"], lane["weight"], lane["conditions"]) == (
        "strategy_swing", "run_once", "pre", 10, 1.0, {})
    assert lane["config"] == {**SWING_DEFAULTS, "strategy_swing_enabled": True,
                              "backtest_credit_pending_sell_proceeds": True}
    # Only the swing lane: another lane's close-filled sell of a bracketed
    # symbol would run before a same-session stop in the simulator.
    assert s.lab_payload() == {"name": s.LAB_DOC_NAME, "strategies": [lane]}
    paper = s.paper_payload()["strategies"]
    assert [l["strategy"] for l in paper] == ["strategy_swing", "strategy_wheel"]
    assert "backtest_credit_pending_sell_proceeds" not in paper[0]["config"]
    assert paper[1]["config"] == {**WHEEL_DEFAULTS, "strategy_wheel_enabled": True}
    assert paper[1]["execution_position"] == 20


def test_docs_200_to_203_are_refused_in_every_spelling():
    s = _setup()
    for doc_id in (200, "200", 201, " 201 ", 202, "203"):
        with pytest.raises(SystemExit):
            s.assert_writable(doc_id)
    assert s.assert_writable(204) == 204


def test_the_lab_watchlist_is_every_member_visible_in_the_window(store):
    s = _setup()
    members(store, [("2021-01-04", ["AAPL", "BRK.B", "OLD"]),
                    ("2021-09-20", ["AAPL", "BRK.B", "NEW"]),
                    ("2026-09-18", ["AAPL", "BRK.B", "LATE"])])
    watch = s.lab_watchlist(store, "2021-07-01", "2026-09-18")
    assert watch == sorted({"AAPL", "BRK.B", "OLD", "NEW", "SPY", "QQQ",
                            "XLP", "XLU", "XLV", "GLD", "SHY"})
    with pytest.raises(SystemExit, match="build_swing_reference_data"):
        s.lab_watchlist(store, "2020-01-01", "2020-06-01")


def test_a_fresh_lab_is_created_with_daily_bars_and_the_watchlist(store):
    s = _setup()
    members(store, [("2021-01-04", ["AAPL", "MSFT"])])
    api = Api(instances={"strategy-eb": {"id": "strategy-eb", "brokerage_id": "brk-lab"}})
    assert s.main(["--start", "2021-07-01", "--end", "2021-12-31"], call=api,
                  store=store) == 0
    ((_, doc),) = api.calls("POST", "/strategies")
    assert doc == s.lab_payload()
    body = api.instances[s.LAB_INSTANCE_ID]
    assert (body["strategy_id"], body["granularity"], body["brokerage_id"]) == (
        900, "86400", "brk-lab")
    assert "granularity_time_increment" not in body
    assert body["stocks"] == s.lab_watchlist(store, "2021-07-01", "2021-12-31")
    assert "runCommand" not in body and "run_command" not in body


def test_an_existing_lab_is_updated_in_place_and_relinked(store):
    s = _setup()
    members(store, [("2021-01-04", ["AAPL"])])
    api = Api(docs=[{"id": 444, "name": s.LAB_DOC_NAME}],
              instances={s.LAB_INSTANCE_ID: {"id": s.LAB_INSTANCE_ID, "strategy_id": 1}})
    assert s.main(["--start", "2021-07-01", "--end", "2021-12-31"], call=api,
                  store=store) == 0
    assert [p for p, _ in api.calls("PUT", "/strategies/")] == ["/strategies/444"]
    assert api.calls("POST", "/strategies") == []
    assert api.calls("POST", f"/instances/{s.LAB_INSTANCE_ID}/link-strategy") == [
        (f"/instances/{s.LAB_INSTANCE_ID}/link-strategy", {"strategy_id": 444})]
    assert not any(m == "PATCH" for m, _p, _b in api.seen)


def test_a_protected_doc_found_by_name_is_refused(store):
    s = _setup()
    members(store, [("2021-01-04", ["AAPL"])])
    api = Api(docs=[{"id": 201, "name": s.LAB_DOC_NAME}])
    with pytest.raises(SystemExit, match="201"):
        s.main(["--start", "2021-07-01", "--end", "2021-12-31"], call=api, store=store)
    assert api.calls("PUT") == []


def test_a_duplicate_stock_is_tolerated_and_a_real_failure_is_not(store):
    s = _setup()
    members(store, [("2021-01-04", ["AAPL"])])

    class Dup(Api):
        def __call__(self, method, path, body=None, **kwargs):
            if method == "POST" and path.endswith("/stocks"):
                raise SystemExit(f"HTTP 409 on POST {path}\n{{\"detail\":\"already added\"}}")
            return super().__call__(method, path, body, **kwargs)

    existing = {s.LAB_INSTANCE_ID: {"id": s.LAB_INSTANCE_ID}}
    assert s.main(["--start", "2021-07-01", "--end", "2021-12-31"],
                  call=Dup(docs=[{"id": 444, "name": s.LAB_DOC_NAME}], instances=existing),
                  store=store) == 0

    class Down(Api):
        def __call__(self, method, path, body=None, **kwargs):
            if method == "POST" and path.endswith("/stocks"):
                raise SystemExit(f"HTTP 500 on POST {path}")
            return super().__call__(method, path, body, **kwargs)

    with pytest.raises(SystemExit, match="500"):
        s.main(["--start", "2021-07-01", "--end", "2021-12-31"],
               call=Down(docs=[{"id": 444, "name": s.LAB_DOC_NAME}], instances=existing),
               store=store)


# -- --paper -------------------------------------------------------------------------

LIVE = {"alpaca-main": {"id": "alpaca-main", "brokerage_id": "brk-live",
                        "granularity_time_increment": 60}}
BROKERAGES = [{"id": "brk-live", "alpaca_paper": False},
              {"id": "brk-paper", "alpaca_paper": True},
              {"id": "brk-unknown"}]


def test_paper_creates_both_lanes_on_a_proven_paper_brokerage(store):
    s = _setup()
    api = Api(instances=dict(LIVE), brokerages=BROKERAGES)
    assert s.main(["--paper", "--brokerage-id", "brk-paper"], call=api, store=store) == 0
    ((_, doc),) = api.calls("POST", "/strategies")
    assert doc == s.paper_payload()
    body = api.instances[s.PAPER_INSTANCE_ID]
    assert (body["brokerage_id"], body["strategy_id"], body["granularity"]) == (
        "brk-paper", 900, "60")
    assert body["stocks"] == s.paper_watchlist()
    assert "run_command" not in body


@pytest.mark.parametrize("argv,needle", [
    (["--paper"], "--brokerage-id"),
    (["--paper", "--brokerage-id", "brk-live"], "alpaca-main"),
    (["--paper", "--brokerage-id", "brk-unknown"], "paper"),
    (["--paper", "--brokerage-id", "brk-missing"], "not found"),
])
def test_paper_refuses_anything_it_cannot_prove_is_a_separate_paper_account(
        store, argv, needle):
    s = _setup()
    api = Api(instances=dict(LIVE), brokerages=BROKERAGES)
    with pytest.raises(SystemExit, match=needle):
        s.main(argv, call=api, store=store)
    assert api.calls("POST", "/instances") == [] and api.calls("POST", "/strategies") == []


def test_paper_refuses_when_alpaca_main_cannot_be_read(store):
    s = _setup()
    api = Api(instances={}, brokerages=BROKERAGES)
    with pytest.raises(SystemExit, match="alpaca-main"):
        s.main(["--paper", "--brokerage-id", "brk-paper"], call=api, store=store)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest backend/tests/test_swing_lab_setup.py -q -p no:cacheprovider`
Expected: FAIL, `FileNotFoundError` for `scripts/swing_lab_setup.py`.

- [ ] **Step 3: Write the script**

Create `scripts/swing_lab_setup.py`:

```python
#!/usr/bin/env python3
"""Create the swing-trader lab document and instance, or the paper instance.

    python3 scripts/swing_lab_setup.py --start 2021-07-01 --end 2026-09-18
    python3 scripts/swing_lab_setup.py --paper --brokerage-id <paper brokerage id>

Lab (default): doc "Swing trader lab" with one strategy_swing lane at enabled
SWING_DEFAULTS plus backtest_credit_pending_sell_proceeds (plan A-backtest:
an entry decided with an exit is otherwise sized before the exit's cash
exists), and the backtest-only instance "swing-lab" at daily granularity. Its
watchlist is every S&P member visible in [--start, --end] from
SwingIndexMembership (run scripts/build_swing_reference_data.py first), plus
SPY, QQQ and the defensive ETFs (spec §7). The lab carries ONLY the swing
lane: the simulator's bar hook runs after its pending-fill block, so another
lane's close-filled sell of a bracketed symbol would execute before a
same-session stop (and the wheel is inert in backtests anyway).

Paper (--paper): doc "Swing trader paper" with both lanes enabled, and the
instance "swing-paper" on --brokerage-id, which must be a PAPER Alpaca account
and must not be alpaca-main's. The instance is never started here.

Idempotent. Docs 200-203 are REFUSED outright: 200 is the live champion, 201
the EB lab, 202 and 203 the frontier research documents.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, timedelta

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))
sys.path.insert(0, os.path.join(_ROOT, "backend"))

from swing_trader import refdata  # noqa: E402
from swing_trader.constants import (  # noqa: E402
    DEFENSIVE_UNIVERSE,
    SWING_DEFAULTS,
    WHEEL_DEFAULTS,
)
from swing_trader.universe import norm_symbol  # noqa: E402

LAB_DOC_NAME = "Swing trader lab"
LAB_INSTANCE_ID = "swing-lab"
PAPER_DOC_NAME = "Swing trader paper"
PAPER_INSTANCE_ID = "swing-paper"
LIVE_INSTANCE_ID = "alpaca-main"
CLONE_FROM = "strategy-eb"
PROTECTED_DOC_IDS = frozenset({"200", "201", "202", "203"})
BENCHMARKS = ("SPY", "QQQ")
DEFAULT_START = "2021-07-01"

#: `_http` formats an HTTPError as "HTTP <code> on <method> <url>\n<body>".
_DUPLICATE_CODES = ("400", "409", "422")


def _is_duplicate(error) -> bool:
    text = str(error).lower()
    return "already" in text and any(f"http {code}" in text for code in _DUPLICATE_CODES)


def assert_writable(doc_id):
    if str(doc_id).strip() in PROTECTED_DOC_IDS:
        raise SystemExit(
            f"REFUSING to write doc {doc_id}: docs 200-203 are the live champion, the "
            "EB lab and the frontier research documents. The swing port runs in its "
            "own documents or not at all.")
    return doc_id


def swing_lane(*, lab) -> dict:
    config = {**SWING_DEFAULTS, "strategy_swing_enabled": True}
    if lab:
        config["backtest_credit_pending_sell_proceeds"] = True
    return {"strategy": "strategy_swing", "weight": 1.0, "execution_position": 10,
            "decision_phase": "pre", "execution_scope": "run_once", "conditions": {},
            "config": config}


def wheel_lane() -> dict:
    return {"strategy": "strategy_wheel", "weight": 1.0, "execution_position": 20,
            "decision_phase": "pre", "execution_scope": "run_once", "conditions": {},
            "config": {**WHEEL_DEFAULTS, "strategy_wheel_enabled": True}}


def lab_payload() -> dict:
    return {"name": LAB_DOC_NAME, "strategies": [swing_lane(lab=True)]}


def paper_payload() -> dict:
    return {"name": PAPER_DOC_NAME, "strategies": [swing_lane(lab=False), wheel_lane()]}


def _extras() -> set:
    return set(BENCHMARKS) | {norm_symbol(s) for s in DEFENSIVE_UNIVERSE}


def lab_watchlist(store, start, end) -> list:
    """Members visible on some session in [start, end]: the list in effect at
    `start` (the latest change strictly before it), plus every change dated
    before `end` (one dated `end` is visible only after it)."""
    first = refdata.members_before(store, start)
    if first is None:
        raise SystemExit(f"no SwingIndexMembership row before {start}: run "
                         "scripts/build_swing_reference_data.py first")
    later = store.run(store.between(refdata.MEMBERSHIP_TABLE, f"SPX|{start}", f"SPX|{end}"))
    union = set(first) | {norm_symbol(s) for r in later for s in (r.get("members") or [])}
    return sorted(union | _extras())


def paper_watchlist() -> list:
    return sorted(_extras())


def _rows(payload):
    if isinstance(payload, list):
        return payload
    for key in ("strategies", "accounts", "items", "rows"):
        if isinstance(payload, dict) and isinstance(payload.get(key), list):
            return payload[key]
    return []


def _safe_get(call, path):
    try:
        return call("GET", path)
    except BaseException as error:  # _api.call SystemExits on 4xx
        return (404 if "404" in str(error) else 500), None


def assert_paper_brokerage(call, brokerage_id):
    """Refuse unless `brokerage_id` is provably a paper account that is not
    alpaca-main's. Returns alpaca-main's instance row."""
    code, live = _safe_get(call, f"/instances/{LIVE_INSTANCE_ID}")
    if not live:
        raise SystemExit(f"REFUSING: {LIVE_INSTANCE_ID} could not be read (HTTP {code}), so "
                         "this cannot prove the brokerage is not the real-money account")
    if str(live.get("brokerage_id") or "") == str(brokerage_id):
        raise SystemExit(f"REFUSING: brokerage {brokerage_id} is {LIVE_INSTANCE_ID}'s — "
                         "the real-money account")
    _, listing = call("GET", "/brokerages")
    row = next((b for b in _rows(listing) if str(b.get("id")) == str(brokerage_id)), None)
    if row is None:
        raise SystemExit(f"REFUSING: brokerage {brokerage_id} not found")
    if row.get("alpaca_paper") is not True:
        raise SystemExit(f"REFUSING: brokerage {brokerage_id} is not marked as an Alpaca "
                         f"paper account (alpaca_paper={row.get('alpaca_paper')!r})")
    return live


def _upsert_doc(call, name, payload):
    _, docs = call("GET", "/strategies")
    existing = next((d for d in _rows(docs) if d.get("name") == name), None)
    if existing:
        doc_id = assert_writable(existing["id"])
        call("PUT", f"/strategies/{doc_id}", payload)
        print("doc updated:", doc_id)
        return doc_id
    _, created = call("POST", "/strategies", payload)
    doc_id = assert_writable(created.get("id") or created.get("strategy_id")
                             or created.get("new_id"))
    print("doc created:", doc_id)
    return doc_id


def _upsert_instance(call, instance_id, *, name, doc_id, granularity, brokerage_id, stocks):
    code, inst = _safe_get(call, f"/instances/{instance_id}")
    if code == 404 or not inst:
        # `granularity` is the field CreateInstanceBody reads (a string of
        # seconds); under any other name it is dropped and the instance is
        # created at the 60 s default.
        call("POST", "/instances", {"id": instance_id, "name": name,
                                    "strategy_id": int(doc_id), "granularity": granularity,
                                    "brokerage_id": brokerage_id, "stocks": list(stocks)})
        print("created instance", instance_id)
    else:
        # PATCH /instances/{id} drops strategy_id; link-strategy is the route.
        call("POST", f"/instances/{instance_id}/link-strategy", {"strategy_id": int(doc_id)})
        print("instance exists; strategy linked to", doc_id)
    for symbol in stocks:
        try:
            call("POST", f"/instances/{instance_id}/stocks", {"symbol": symbol})
        except SystemExit as error:
            if not _is_duplicate(error):
                raise
    _, check = call("GET", f"/instances/{instance_id}")
    print("instance:", json.dumps({k: (check or {}).get(k) for k in (
        "id", "strategy_id", "runCommand", "granularity_time_increment", "brokerage_id")}))


def main(argv=None, *, call=None, store=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--paper", action="store_true",
                    help="create swing-paper (both lanes) instead of the backtest lab")
    ap.add_argument("--brokerage-id", help="the PAPER brokerage for --paper")
    ap.add_argument("--start", default=DEFAULT_START)
    ap.add_argument("--end", default=(date.today() - timedelta(days=1)).isoformat())
    args = ap.parse_args(argv)
    if call is None:
        from _api import call as call  # noqa: PLW0127

    if args.paper:
        if not args.brokerage_id:
            raise SystemExit("--paper needs --brokerage-id (a paper Alpaca account)")
        live = assert_paper_brokerage(call, args.brokerage_id)
        doc_id = _upsert_doc(call, PAPER_DOC_NAME, paper_payload())
        granularity = str(live.get("granularity_time_increment") or "60")
        _upsert_instance(call, PAPER_INSTANCE_ID, name="Swing trader (paper)",
                         doc_id=doc_id, granularity=granularity,
                         brokerage_id=args.brokerage_id, stocks=paper_watchlist())
        print("NOT started: link the conviction model on both lanes, then start it.")
        return 0

    if store is None:
        from db import store as db_store
        store = db_store
    stocks = lab_watchlist(store, args.start, args.end)
    print(f"lab watchlist: {len(stocks)} symbols for {args.start}..{args.end}", flush=True)
    doc_id = _upsert_doc(call, LAB_DOC_NAME, lab_payload())
    code, inst = _safe_get(call, f"/instances/{LAB_INSTANCE_ID}")
    brokerage_id = None
    if code == 404 or not inst:
        _, clone = call("GET", f"/instances/{CLONE_FROM}")
        brokerage_id = (clone or {}).get("brokerage_id")
    _upsert_instance(call, LAB_INSTANCE_ID, name="Swing trader lab (backtest only)",
                     doc_id=doc_id, granularity="86400", brokerage_id=brokerage_id,
                     stocks=stocks)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 -m pytest backend/tests/test_swing_lab_setup.py backend/tests/test_hx_lab_setup.py -q -p no:cacheprovider`
Expected: PASS (`test_swing_lab_setup.py` has 13 tests).

- [ ] **Step 5: Commit**

Run `gitnexus_detect_changes()`; expect the new script and test only.

```bash
git add scripts/swing_lab_setup.py backend/tests/test_swing_lab_setup.py
git commit -m "feat(swing): lab and paper setup script

The lab gets one swing lane at enabled defaults with same-tick sale
proceeds credited, a daily-bar backtest instance and a watchlist of every
S&P member visible in the window plus the benchmarks and defensive ETFs.
The paper instance gets both lanes on a brokerage the script can prove is
a paper account and is not the real-money one, and is never started here.
Docs 200 to 203 are refused.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016ztXjLWXu61Dpp4XMU2jsS"
```

---

### Task 24: Header sync script for both lanes (B16)

**Files:**
- Create: `scripts/strategy_swing_sync_schema.py`
- Test: `backend/tests/test_strategy_swing_sync_schema.py`

**Interfaces:**
- Consumes: Task 2 (`SWING_DEFAULTS`, `WHEEL_DEFAULTS`), Tasks 16 and 18 (the two headers on line 1).
- Produces: `scripts/strategy_swing_sync_schema.py [--root DIR]`. It rewrites line 1 of `backend/strategies/strategy_swing.py` (config `SWING_DEFAULTS`, `execution_position` 10) and of `backend/strategies/strategy_wheel.py` (config `WHEEL_DEFAULTS`, `execution_position` 20) as `"# INTELLISTOCK_SCHEMA: " + json.dumps(schema)`. The other header keys keep their order and values, and it prints one line per file. `--root` names the tree whose files it rewrites (default: this repo). The defaults always come from this repo's code.

The header is what the UI and `/strategies/available` read (the `strategy_hx_sync_schema.py` precedent). A header that drifts from the constants lets an operator configure a key the lane does not read, or hides one it does. The committed headers must be exactly what this script writes, and Task 16's and Task 18's header tests check the same thing from the other side.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_strategy_swing_sync_schema.py`:

```python
"""The committed swing and wheel headers are exactly what the sync script
writes from SWING_DEFAULTS / WHEEL_DEFAULTS."""
import json
import os
import re
import shutil
import subprocess
import sys

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ROOT = os.path.dirname(_BACKEND)
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from swing_trader.constants import SWING_DEFAULTS, WHEEL_DEFAULTS  # noqa: E402

SCRIPT = os.path.join(_ROOT, "scripts", "strategy_swing_sync_schema.py")
FILES = {"strategy_swing.py": (SWING_DEFAULTS, 10), "strategy_wheel.py": (WHEEL_DEFAULTS, 20)}


def header(text):
    return json.loads(re.search(r"# INTELLISTOCK_SCHEMA: (.*)", text).group(1))


def run(*args):
    return subprocess.run([sys.executable, SCRIPT, *args], cwd=_ROOT,
                          capture_output=True, text=True)


def test_the_committed_headers_are_byte_identical_after_a_sync():
    paths = {n: os.path.join(_BACKEND, "strategies", n) for n in FILES}
    before = {n: open(p, encoding="utf-8").read() for n, p in paths.items()}
    result = run()
    assert result.returncode == 0, result.stderr
    for name, path in paths.items():
        after = open(path, encoding="utf-8").read()
        assert after == before[name], f"{name}: the committed header is not what the defaults say"
        defaults, position = FILES[name]
        schema = header(after)
        assert schema["config"] == defaults and list(schema["config"]) == list(defaults)
        assert schema["execution_position"] == position
        assert after.splitlines()[0].startswith("# INTELLISTOCK_SCHEMA: ")
        assert after.splitlines()[1].startswith("# INTELLISTOCK_DESCRIPTION: ")


def test_a_drifted_header_is_rewritten_and_nothing_else_moves(tmp_path):
    strategies = tmp_path / "backend" / "strategies"
    strategies.mkdir(parents=True)
    for name in FILES:
        shutil.copy(os.path.join(_BACKEND, "strategies", name), strategies / name)
    path = strategies / "strategy_swing.py"
    original = path.read_text(encoding="utf-8")
    schema = header(original)
    schema["config"].pop("vix_max")
    schema["config"]["stale_key"] = 1
    schema["execution_position"] = 99
    drifted = original.replace(original.splitlines()[0],
                               "# INTELLISTOCK_SCHEMA: " + json.dumps(schema), 1)
    path.write_text(drifted, encoding="utf-8")
    result = run("--root", str(tmp_path))
    assert result.returncode == 0, result.stderr
    assert path.read_text(encoding="utf-8") == original
    assert "strategy_swing.py" in result.stdout and "strategy_wheel.py" in result.stdout
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest backend/tests/test_strategy_swing_sync_schema.py -q -p no:cacheprovider`
Expected: FAIL. The script does not exist, so `returncode == 2` ("can't open file").

- [ ] **Step 3: Write the script**

Create `scripts/strategy_swing_sync_schema.py`:

```python
#!/usr/bin/env python3
"""Sync the INTELLISTOCK_SCHEMA headers of strategy_swing and strategy_wheel
with swing_trader.constants SWING_DEFAULTS / WHEEL_DEFAULTS.

    python3 scripts/strategy_swing_sync_schema.py [--root DIR]

The header is what the UI and /strategies/available read; letting it drift
from the constants means an operator configures a key the lane does not read,
or misses one it does. Only `config` and `execution_position` are written;
every other header key keeps its order and value. --root names the tree whose
files are rewritten (default: this repo); the defaults always come from this
repo's code.
"""
import argparse
import json
import pathlib
import re
import sys

_REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "backend"))

from swing_trader.constants import SWING_DEFAULTS, WHEEL_DEFAULTS  # noqa: E402

LANES = (("strategy_swing.py", SWING_DEFAULTS, 10),
         ("strategy_wheel.py", WHEEL_DEFAULTS, 20))


def sync(path: pathlib.Path, defaults: dict, position: int) -> int:
    source = path.read_text(encoding="utf-8")
    match = re.search(r"# INTELLISTOCK_SCHEMA: (.*)", source)
    if match is None:
        raise SystemExit(f"{path}: no INTELLISTOCK_SCHEMA header")
    schema = json.loads(match.group(1))
    schema["config"] = dict(defaults)
    schema["execution_position"] = position
    path.write_text(source.replace(match.group(0),
                                   "# INTELLISTOCK_SCHEMA: " + json.dumps(schema), 1),
                    encoding="utf-8")
    return len(schema["config"])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=str(_REPO))
    args = ap.parse_args(argv)
    for name, defaults, position in LANES:
        path = pathlib.Path(args.root) / "backend" / "strategies" / name
        n = sync(path, defaults, position)
        print(f"{name}: schema synced from defaults, {n} config keys, "
              f"execution_position {position}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 -m pytest backend/tests/test_strategy_swing_sync_schema.py backend/tests/test_strategy_swing_backtest.py backend/tests/test_strategy_wheel.py -q -p no:cacheprovider`
Expected: PASS. If the first test fails on a header diff, the committed header is wrong. Run the script, review the diff and commit the synced header. Never edit the test.

- [ ] **Step 5: Commit**

Run `gitnexus_detect_changes()`; expect the new script and test only (and no header change: Tasks 16 and 18 committed the synced lines).

```bash
git add scripts/strategy_swing_sync_schema.py backend/tests/test_strategy_swing_sync_schema.py
git commit -m "feat(swing): header sync script for the swing and wheel lanes

Rewrites each lane's INTELLISTOCK_SCHEMA config and position from the
constants and leaves everything else in place. A test pins the committed
headers as exactly what it writes.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016ztXjLWXu61Dpp4XMU2jsS"
```

---

### Task 25: Full-suite verification and the cross-plan hand-offs

**Files:** none are created. Anything this task finds is fixed in the file that owns it, with that task's test extended first.

**Interfaces:**
- Consumes: every earlier task. Plans A-live Tasks 1–15 and A-backtest have landed. A-live Task 16 (deploy fingerprint lists and the final full suite) has not: it runs after this task.
- Produces: a verified branch, plus the hand-off list A-live Task 16 needs.

- [ ] **Step 1: This plan's tests**

Run:

```bash
python3 -m pytest backend/tests/test_swing_tables.py backend/tests/test_swing_constants.py \
  backend/tests/test_swing_indicators.py backend/tests/test_swing_signals.py \
  backend/tests/test_swing_regime_logic.py backend/tests/test_swing_clock.py \
  backend/tests/test_swing_universe_refdata.py backend/tests/test_swing_market_data.py \
  backend/tests/test_swing_wheel_screen.py backend/tests/test_swing_wheel_orders.py \
  backend/tests/test_swing_wheel_positions.py backend/tests/test_llm_web_search.py \
  backend/tests/test_swing_notify.py backend/tests/test_swing_ai_analyst.py \
  backend/tests/test_swing_approvals.py backend/tests/test_swing_iv.py \
  backend/tests/test_swing_calibration.py backend/tests/test_strategy_swing_backtest.py \
  backend/tests/test_strategy_swing_live.py backend/tests/test_strategy_wheel.py \
  backend/tests/test_swing_approval_roundtrip.py backend/tests/test_swing_api.py \
  backend/tests/test_build_swing_reference_data.py backend/tests/test_swing_lab_setup.py \
  backend/tests/test_strategy_swing_sync_schema.py -q -p no:cacheprovider
```

Expected: 276 passed. That is 271 without `test_swing_approval_roundtrip.py`, which needs A-live Task 15. A dbcore test skips only for Postgres without `PG_TEST_DSN`.

- [ ] **Step 2: The full suite, and exactly the pre-existing failures**

Run:

```bash
python3 -m pytest backend/tests -q -p no:cacheprovider -rf 2>&1 | tee /tmp/swing_full.txt | tail -3
grep '^FAILED' /tmp/swing_full.txt | sed 's/ - .*//' | sort > /tmp/swing_failed.txt
cut -d: -f1 /tmp/swing_failed.txt | sort | uniq -c
```

Expected:
- The passed count equals the 7,565 baseline, plus plan B's 277 (its 276 tests, plus the inverse auth test's new case for the decision route), plus the new tests of every other plan that has landed (A-live, A-backtest).
- The failure count is **exactly 19**, all pre-existing: `backend/tests/test_adv_exit_discipline_findings.py` 11, `backend/tests/test_core_sleeve_adversarial.py` 7 and `backend/tests/test_zz_adversarial_sweep.py` 1.
- Any other failure is this branch's regression. Find the owning task, extend its test, fix, and commit (run `gitnexus_detect_changes()` first). Never add a failure to the known list.

- [ ] **Step 3: The store-touching tests against Postgres**

```bash
scripts/dev_pg.sh up
export PG_TEST_DSN=...        # the DSN dev_pg.sh prints
python3 -m pytest backend/tests/dbcore/test_schema_ensure.py backend/tests/test_swing_tables.py \
  backend/tests/test_swing_universe_refdata.py backend/tests/test_swing_approvals.py \
  backend/tests/test_swing_iv.py backend/tests/test_swing_calibration.py \
  backend/tests/test_build_swing_reference_data.py backend/tests/test_swing_api.py \
  backend/tests/test_strategy_swing_backtest.py backend/tests/test_strategy_swing_live.py \
  backend/tests/test_strategy_wheel.py -q -p no:cacheprovider
```

Expected: PASS, with nothing skipped for Postgres. This is where `COLLATE "C"` id ordering, the `[lo, hi)` bound of `between` (a VIX or membership row dated the session must stay invisible), `replace_if` (a decision is final) and the six new tables' DDL are exercised for real. The FakeStore only models them.

- [ ] **Step 4: The cross-plan hand-offs**

Each of these must hold. A miss is a bug in whichever plan's code disagrees with the interfaces doc.

```bash
grep -n "from swing_trader.notify import notify_wheel_assignment" backend/broker.py      # A-live Task 12
grep -n "def notify_wheel_assignment(instance_id, \*, symbol, qty, price=None, date=None)" backend/swing_trader/notify.py
grep -n "_SW_DEFAULTS = " -A 4 backend/broker.py            # A-live Task 8: must equal SWING_DEFAULTS' six live_* values
grep -n "_engine_wheel_assignments" backend/broker.py backend/strategies/strategy_wheel.py   # written by A-live, read by Task 19
grep -n "update_signal\|build_approved_order\|get_signal" backend/broker.py               # A-live Tasks 10, 15 -> Task 14
grep -n "^## 10\.\|^## 11\." docs/superpowers/plans/2026-09-24-swing-port-interfaces.md    # A-live's additions, then plan B's
```

Expected: every grep prints at least one line. The last one prints `## 10. Additions from plan A-live` and `## 11. Additions from plan B`.

- [ ] **Step 5: Hand A-live Task 16 the files this plan ships**

A-live Task 16 adds the deployed files to `scripts/check_deployed_code.py`'s `FILES` and to `backend/api/main.py`'s `_CODE_FINGERPRINT_FILES`. The backend-relative paths from this plan are:
- `strategies/strategy_swing.py` and `strategies/strategy_wheel.py`;
- `swing_trader/__init__.py`, `swing_trader/account.py`, `swing_trader/ai_analyst.py`, `swing_trader/approvals.py`, `swing_trader/calibration.py`, `swing_trader/clock.py`, `swing_trader/constants.py`, `swing_trader/indicators.py`, `swing_trader/iv.py`, `swing_trader/market_data.py`, `swing_trader/notify.py`, `swing_trader/refdata.py`, `swing_trader/regime.py`, `swing_trader/sectors.py`, `swing_trader/signals.py`, `swing_trader/signals_store.py`, `swing_trader/universe.py`, `swing_trader/wheel_rules.py`;
- the modified `interactive_utils.py`, `api/main.py`, `notification_types.py` and `db/schema.py`, and `llm_utils.py`, which is already listed.

Scripts are not deployed. Post this list in the hand-off note for A-live Task 16. No commit.

- [ ] **Step 6: Operator steps after the merge (spec §12; for the record, not run here)**

These steps are the operator's. They run after the merge and deploy, outside market hours, and never on a Thursday or Friday EB morning. Plan B adds these to spec §12:
1. `python3 scripts/build_swing_reference_data.py --membership-csv "<the newest fja05680 CSV>"`. Check the rename line it prints against Alpaca before trusting a window that spans one of those dates.
2. `python3 scripts/swing_lab_setup.py --start 2021-07-01 --end <yesterday>`. Then run lab backtests **one at a time**, never in parallel, and analyze each full path before the next.
3. `python3 scripts/swing_lab_setup.py --paper --brokerage-id <paper brokerage>`. Link the conviction model on both lanes in the strategy editor, then start `swing-paper`.

---

## Self-Review

### 1. Spec coverage

| Spec requirement | Task |
|---|---|
| §4 `constants.py`: every ST constant, and the two header defaults | 2 |
| §4 `indicators.py`, including the NaN-close guard | 3 (parity with vendored ST) |
| §4 `signals.py` (`entry_signal`, `exit_signal`, `sector_conflict`, bear mode) and `regime.py` | 4 |
| §4 `universe.py`, `sectors.py` (the stored map first; yfinance only as a live fallback) | 6 |
| §4 `market_data.py` (batched SIP bars, IEX latest trade) | 7 |
| §4 `wheel_rules.py` (screen, `next_friday`, the Tuesday guard, delta pick, the ladder, caps, 2×, the monitor rules, covered-call strike) | 8, 9, 10 |
| §4 `ai_analyst.py` (ST prompts, `_handle_result`, thresholds, the wheel scorer) | 13 |
| §4 `iv.py`, `calibration.py` | 15 |
| §4 `approvals.py` (a final state machine, and the rebuild at the live price) | 14 |
| §4: ST's tests ported (`test_indicators_nan`, `test_regime_logic`, `test_collateral_cap`, `test_assignment_detection`, `test_wheel_scan_guard`, `test_calibration`, `test_market_data`) | 3, 4, 9, 10, 8, 15, 7 |
| §5.1 header: ST values, the live envelope, the single-position keys | 2, 16, 24 |
| §5.1 backtest items 1–6: incremental indicators, regime from SPY and `SwingMacroDaily`, point-in-time members and the sector map, the entry hint, the RSI exit hint, no AI and no earnings block | 16 |
| §5.1 live: its own bars; VIX and earnings from yfinance; the AI gate; the same hint anchored on the prior close; the latch on completion; the `quote.stale` re-arm | 17 |
| §5.1 bear counter per session (fix 11) | 4, 16, 17 |
| §5.1 exits in the scan. The engine cancels legs before selling (A-live), and **the lane re-emits an exit the engine deferred** (A-live contract addition 15) | 16, 17 |
| §5.2 header, and inert in backtests with one log line | 2, 18, 24 |
| §5.2 weekly scan: Monday 10:30 ET, a Tuesday fallback on the completion marker, delta from the full chain, bid × 0.95 and the ladder, the `_nexus_option_orders` shape | 18 |
| §5.2 Monday 2× premium buy-back | 19 |
| §5.2 daily monitor ≥ 15:45 ET with ST's three auto-close rules and ST's priorities | 19 |
| §5.2 assignment: engine-reported shares; a dry-run covered call with `auto_covered_call=false` | 19 (reads A-live's `_engine_wheel_assignments`) |
| §5.2 IV snapshot once per session ≥ 09:15 ET | 19 |
| §7 six tables and their read rules | 1, 6 |
| §7 `build_swing_reference_data.py` (VIX with FRED fallback, fja05680 membership and a rename map, sectors; idempotent) | 22 |
| §7 `swing_lab_setup.py`: lab doc and instance, the watchlist as the union of members over the window, refuses docs 200–203; `--paper` | 23 |
| §8 `conviction_llm_model_id` in both headers; the resolver's injected keys are consumed | 2, 13 |
| §8 structured scoring and `_handle_result` checks; the size adjustment clamped | 13 |
| §8 `call_llm_with_web_search`: Gemini grounding, Anthropic web search, any other provider skips with one line | 11 (see limit 3) |
| §8 one LLM error skips one candidate; no model linked means refusal with one alert per session | 13, 17, 18 |
| §8 every scored candidate is a `SwingSignals` row; calibration (`GATE_TRADES=20`, `MIN_BUCKET_N=5`) | 14, 15, 17, 18, 21 |
| §9 fix 1 (no duplicate put) | 9, 18 (lane side); A-live keys option sells on the session |
| §9 fix 2 (approval recomputes the expiry) | 14, 20 |
| §9 fix 3 (the cap counts existing puts) | 9 |
| §9 fix 4 (the sector set updated after each buy) | 16, 17 |
| §9 fix 5 (one AI error skips one candidate) | 13, 17, 18 |
| §9 fix 6 (the monitor checks the contract type) | 10, 19 |
| §9 fix 7 (the fill price is recorded) | 15 (outcomes read `filled_avg_price`); the order log is A-live's lifecycle store |
| §9 fix 8 (cash, not margin) | 9, 18 |
| §9 fix 9 (the full chain) | 9, via A-live's paginated `get_option_contracts` |
| §9 fix 10 (contract fields, not an OCC regex) | 9, 10, 19, 21 (`occ_parts` only for working orders and as a display fallback) |
| §9 items 12–17 (ET times, tables, the models framework, the stored sector map, the gate envelopes and one stale retry, no Run Now) | 5, 1, 13, 6, 16/17, — |
| §10 API routes (auth required; plan C's §9 shapes) and the notification types with push defaults | 21, 12 |
| §11 TDD, parity tests, the inverse auth test | every task; 3, 4, 8, 13; 21 Step 5 |
| §12 no merge by this plan; operator steps; the deploy fingerprint lists handed to A-live Task 16 | 25 |

The spec's frontend items (`strategyConfig.js` role labels, the web and mobile screens) belong to plan C. The engine items (§6) belong to plans A-live and A-backtest.

### 2. Placeholder scan

The generated document was searched for `TBD`, `TODO`, `FIXME`, `similar to Task`, `…` inside code blocks, and for unexpanded generator templates (the ST line-range inserts, the S&P list, the two headers). There are none. The `{{` that remain are ST's own f-string brace escapes inside its prompt templates, copied verbatim. Every code block is complete. The ST-verbatim blocks were filled in from the pinned clone (commit c2afa71) by line range, and the parity tests compare them with the vendored originals.

### 3. Type consistency

These signatures are used identically in every task that calls them:
- `signals_store.new_signal(*, instance_id, lane, symbol, session, score, recommendation, reasoning, key_risks, size_adjustment, proposal, status, context=None, created_at=None)`, `signal_id_for(instance_id, lane, session, symbol)`, `cas_signal(signal_id, *, expect_status, doc)`, `list_signals(instance_id, status=None, limit=100)`, `list_wheel_scans(instance_id, limit=50)`
- `approvals.decide(signal, decision, user, reason, now_iso)` and `approvals.build_approved_order(signal, *, live_price, equity, cfg, adapter=None, today=None)`: A-live Task 15 calls the latter exactly so, and Task 20 runs the handshake
- `wheel_rules.build_put_order_live(candidate, *, adapter, cfg, equity, cash, option_positions, open_orders, committed_collateral=None, signal_id=None, session=None) -> (order | None, error | None, meta)`, `btc_order(pos, qty, reason, *, signal_id=None, session=None)`, `two_x_exits(option_positions, *, open_orders=()) -> [(order, info)]`, `put_monitor_decision(*, contract, underlying, strike, expiry, stock_price, today)`, `covered_call_candidates(equity_positions, option_positions, open_orders, *, swing_owned=())`
- `clock.tick_deadline(current_time, mode)`, `clock.time_left(deadline)`, `clock.at_or_after(t, hhmm, *, lead_min=0)`
- `notify.send(category, instance_id, title, message, *, priority=0)`, `notify.notify_wheel_assignment(instance_id, *, symbol, qty, price=None, date=None)`: A-live Task 12 calls the latter with those keywords
- `iv.run_iv_snapshot(store, *, adapter, spot_for, today, symbols=None, deadline=None, now_fn=...)`
- `calibration.record_outcomes(instance_id, adapter, lane, *, held=None)`
- The wrapper method signatures: `_monitor/_weekly/_iv(now, session, today, iid, cfg, emu, cache, deadline)`, `_position_checks(session, today, iid, cfg, emu, cache)`
- Cache keys: every swing key starts `_swing_` and every wheel key starts `_wheel_`. `_engine_*` keys are written only by the engine.

### 4. Review Focus coverage

| Review Focus | Tests |
|---|---|
| 1. NaN close on the scan day | Task 3 (`test_trailing_nan_close_falls_back_to_last_completed_bar`), Task 16 (`test_a_nan_close_on_the_last_bar_uses_the_previous_session`) |
| 2. VIX missing | Task 6 (`test_vix_before_skips_a_holiday_and_refuses_a_stale_gap`), Task 16 (`test_a_vix_gap_blocks_entries_and_logs_once`) |
| 3. Malformed JSON, or a score outside 0–100 | Task 13 (`test_out_of_range_score_raises`, `test_wheel_scoring_failure_is_a_reject_not_a_crash`), Task 17 (`test_an_ai_error_skips_only_that_candidate`) |
| 4. An approval clicked twice or after submission (and two clicks at once) | Task 14 (`test_a_second_decision_conflicts`), Task 20 (`test_a_submitted_signal_is_never_submitted_twice`), Task 21 (`test_a_second_decision_is_a_400_and_enqueues_nothing`, `test_a_click_that_loses_the_race_is_a_409_and_queues_nothing`) |
| 5. A wheel scan rerun the same week after a crash | Task 18 (`test_a_crashed_monday_scan_resumes_without_rescoring`, `test_tuesday_fallback_runs_only_without_a_completion_marker`, `test_an_underlying_with_an_open_put_is_skipped`, `test_a_rerun_after_losing_the_cache_does_not_sell_a_second_put`) |

Failure modes the spec implies that are covered beyond those five:
- the deferred exit: Task 17, `test_a_deferred_exit_is_reemitted_until_the_position_is_gone` and `test_a_bracket_leg_is_not_a_working_exit`;
- the stale pre-market quote: Task 17;
- an unreadable order book: Tasks 17, 18 and 19, where it sells nothing and defers;
- a broker that cannot be read is a 503, never an empty wheel book: Task 21;
- approving while the instance is stopped is a 503 and the signal stays pending: Task 21;
- the Cboe outage falls back to FRED: Task 22;
- a brokerage the setup script cannot prove is paper: Task 23.

### 5. Known limits, for the coordinator and the operator

1. **`backtest_credit_pending_sell_proceeds` is inert on the swing lab today.** `broker.py` reads it only from a `graph_nexus` lane's config (`_core_sleeve_cfg_raw`, lines ~3693 and ~16827). Task 23 puts it on the swing lane as ruled. Until plan A-backtest reads it from any lane's config, an entry decided with a same-tick exit is sized before that exit's proceeds exist. The fix is A-backtest's: a one-line change in `broker.py`, which plan B does not edit.
2. **`auto_covered_call=true` cannot execute.** A-live's gate refuses every sell-to-open call (`option.sell_to_open_requires_put`). So the lane sends ST's dry-run notice and says why no call was sent. It does not emit an order the gate is certain to refuse. The wheel sells covered calls only after A-live gains a gate rule for calls covered by assigned shares.
3. **News search.** `llm_utils` has no direct Anthropic API provider (only `claude-cli`, `bedrock` and `openrouter`), so "Anthropic web search" is the claude-cli `WebSearch` tool (Task 11). Its `--tools` / `--allowedTools` / `--max-turns` flags need a check against the server's CLI version. Bedrock, OpenRouter and the others skip news with one log line, and Gemini uses grounding. Scoring works with every provider.
4. **The 20-minute tick grid.** The swing scan runs at 09:20 and the wheel scan at 10:40, the first ticks at or after ST's times. The monitor runs at 15:40, one tick early, because 16:00 is after the close. The IV snapshot runs at 09:20.
5. **Watchdog budgets.** A MONITOR tick has about 100 s, and the broker discards a run_once that overruns 120 s. So both scans are resumable and walk their queue a candidate at a time. A wheel scan with many candidates, each costing two model calls, can span several ticks (worst case: the scan stops at the close and the Tuesday fallback starts fresh).
6. **Share counts.** The engine sizes a live bracket as `floor(buy_cash / live price)`, so it can buy one share fewer than ST's close-based count. This is accepted. Approvals carry an exact `qty` (Task 14).
7. **Assigned shares.** Covered-call notices use only engine-recorded assignments (`_engine_wheel_assignments`). A reset strategy cache forgets them, which is safe but silent. Swing exits and slots still see every held stock, assigned shares included, as ST's single account did.
8. **`RENAME_MAP`** (Task 22) must be checked against Alpaca before trusting a window that spans a rename. The chain CBS → VIAC → PARA is the doubtful one: Paramount's 2025 merger changed its ticker again.
9. **The `swing-paper` granularity** is cloned from `alpaca-main`. The live cadence comes from the scheduler, not from granularity, so the clone only keeps the bar-size setting identical to the one live configuration proven on this broker.
10. **Kept from ST:** an approval can exceed `max_positions`, option positions take swing slots, and a holiday is simply a non-trading day (no scan; ST logged a skip). The backtest indicators run on the engine's bars (its adjustment), not ST's `adjustment=all` fetch.
11. **Plan B does not edit `broker.py`,** and does not own the deploy fingerprint lists. Task 25 hands A-live Task 16 the file list.

### 6. Dry run (2026-09-24)

A script applied this document's own code blocks and anchored edits, task by task, to a clean `git archive HEAD` copy of the repository.
- **Tasks 1–19 and 21–24:** every new test passes, 271 in all.
- **Task 20** needs plan A-live's `broker.py`. It was run against a copy of plan A-live's patched tree (its Tasks 1–15 applied), with this plan's `swing_trader` package dropped in: 5 passed.
- **Full suite** on the copy with Tasks 1–24 applied (Task 20's file excluded, since A-live is absent): 7,835 passed, 433 skipped and 19 failed. The pristine copy of HEAD gives 7,563 passed, 433 skipped and 19 failed in the same environment.
  - The copy's HEAD baseline is 7,563, two below the worktree's 7,565: the copy is an archive, not a checkout.
  - Plan B adds 272 passes: 271 new tests, plus one new parametrized case of `test_api_authorization.py`'s inverse test (the decision route refuses an unauthenticated caller with 401).
  - The failing set is **identical** to the baseline's: `test_adv_exit_discipline_findings` 11, `test_core_sleeve_adversarial` 7, `test_zz_adversarial_sweep` 1.
- **A fresh apply** of the final text (Tasks 1–24) into a third copy passes these tests, with 9 skipped for Postgres: plan B's tests, `test_notification_types.py`, `dbcore/test_schema_ensure.py` and `test_api_authorization.py`, 412 in all.
- **Postgres:** Task 25 Step 3 (the store-touching tests against a live Postgres) was not run here.

## Execution handoff

Plan complete and saved to `docs/superpowers/plans/2026-09-24-swing-port-B-strategies.md`.

- **Subagent-driven:** a fresh subagent implements each task, and a fresh reviewer checks it before the next one starts. A whole-branch review follows at the end.
- **Native:** one session implements every task, then one fresh reviewer checks the whole branch.

**Recommended: subagent-driven.** The 25 tasks are tightly coupled through interfaces: two wrappers stand on fifteen module tasks' exact signatures, and Tasks 20 and 21 are handshakes with plans A-live and C. A mismatch that slips one task compounds in every later one. On `swing-paper` a shipped mistake places real paper orders.
