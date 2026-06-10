# Live Mode Safe Startup — Design Spec

**Date:** 2026-05-21
**Status:** Design approved, ready for implementation planning
**Phases:** Phase 1 (ship now, for tomorrow's launch); Phase 2 (multi-week, separate implementation cycle)

## 1. Problem Statement

Live mode on `instance_id="main"` currently loads strategy state that is contaminated by the previous (badly-performing) nexus version's broker history, decision artifacts, and persisted in-memory caches. A new nexus version booted on this instance inherits cooldowns, blacklists, peak HWM markers, discovered-stock "sold" flags, rotation timers, and learning-cache scopes from the old version. Backtests do not have this problem because they start with `_strategy_cache = {}` and never read live-mode state.

The operator wants live boot to behave like a backtest boot: hydrate `_strategy_cache` from a clean, version-validated source (a recent backtest's end-state snapshot), then run trading from there — without paying the cost of a 30-60 minute full 120-day lookback on every boot.

## 2. Goals

- **Primary:** A live boot on `instance_id="main"` uses a recent backtest's final `_strategy_cache` as its starting state, runs a short gap-day lookback (typically 1 day) to catch up to the present, and starts trading with no influence from the prior (deprecated) nexus version's state.
- **Secondary:** Future live restarts (e.g. container reboot weeks into a run) also benefit by reusing the live runtime's rolling state snapshot — not just the cold-start case.
- **Tertiary:** Set up the schema and code patterns so Phase 2 (versioned per-instance tables, side-by-side strategy version operation) can ship cleanly without paint-into-corner decisions.

## 3. Non-Goals

- Multi-strategy-per-instance UI work (Phase 3, future).
- Changes to backtest result reporting (separate effort).
- Changes to the `BrokerAdapter` / Robinhood integration layer.
- Strategy-logic changes inside `graph_nexus_analysis.py` (cache-loading scaffolding only).
- A "rewind" / "time-travel" debugger.

## 4. Decisions Locked During Brainstorming

| Decision | Choice | Rationale |
|---|---|---|
| Cache source for `_strategy_cache` at live boot | Snapshot-from-backtest | Operator's intuition; matches the goal of "live should act like backtest." |
| Existing Robinhood positions on launch day | Operator liquidates manually before launch | Simplest; F1b warm-boot detection doesn't fire; deployment ramp runs normally from bar_index=0. |
| Legacy per-instance auxiliary table cleanup | Phase 1: extended `clear_main_instance_lookback_state.py`; Phase 2: versioned per-instance schema removes the need entirely | Phase 1 ships today; Phase 2 is the long-term right answer. |
| Snapshot table architecture | Extend existing `NexusStrategyCache` with `origin` and `config_hash` columns | Unified persistence model; rolling live persistence; cleanest Phase 2 evolution. |
| Timeline split | Both phases spec'd in this one document; Phase 1 implemented first, Phase 2 gets its own implementation plan later | Operator asked for consolidated design. |

## 5. Phase 1 — Architecture

```
BACKTEST (run with base_instance_id="main", end_date=today)
   └─> _run_backtest_historic_lookback (broker.py:2924)
   └─> main backtest loop (writes GraphNexusTradeContexts, etc.)
   └─> at backtest end (NEW): _persist_strategy_cache_snapshot()
         └─> serialize _strategy_cache + config_hash + nexus_module_hash
         └─> write row to NexusStrategyCache with origin="backtest"

[OPERATOR pre-launch checklist]
   1. Liquidate all Robinhood positions
   2. Run extended clear_main_instance_lookback_state.py --apply --instance main
   3. Run validate_live_launch_readiness.py; expect GREEN

LIVE BOOT (instance_id="main")
   └─> broker.py:5057-5309 strategy cache boot section
         └─> (NEW) lookup most recent NexusStrategyCache where
             (instance_id="main", config_hash=current_hash)
             ordered by end_date desc, created_at desc
         └─> if found and not stale and module hashes match:
                hydrate _strategy_cache from blob
                compute gap_days = trading days between end_date and today
                run short lookback for gap_days only (typically 1)
         └─> if not found OR stale OR hash mismatch:
                log warning + Discord notify
                run current full-lookback behavior (NEXUS_LIVE_LOOKBACK_MAX_DAYS, default 120)
   └─> _run_live_historic_lookback (may be the short-gap variant)
   └─> main loop starts with seeded _strategy_cache

LIVE RUNTIME (every N bars, e.g. EOD)
   └─> (EXISTING save path) save_strategy_cache_to_db
         └─> write row to NexusStrategyCache with origin="live" and current config_hash
         └─> a mid-day or next-day restart now reuses this same load path
```

### 5.1 BLOCKERS from session #4 — Phase 1 resolution

| BLOCKER | Resolution |
|---|---|
| Settlement T+1/T+2 wait | Pre-launch checklist + boot-time soft check: if `cash_available < cash_total * 0.95`, log warning + Discord ping, don't block. |
| F1b ramp bypass on 0 warm positions | Operator liquidates → 0 positions → F1b doesn't trigger. Add explicit assert + log at boot: `warm positions=0, F1b bypass=disabled, ramp starting from bar_index=0`. |
| `_nexus_full_cycle_completed_date` clear | Field is included in the snapshot. Loaded snapshot's value used directly. Add explicit boot log: `loaded full_cycle_completed_date=<X>; next FULL cycle expected at <Y>`. |

## 6. Phase 1 — Data Model

### 6.1 `NexusStrategyCache` extended schema

| Column | Type | Notes |
|---|---|---|
| `id` | string (PK) | `"{instance_id}\|{strategy_name}\|{config_hash}\|{origin}\|{end_date}"` |
| `instance_id` | string | secondary index |
| `strategy_name` | string | e.g. `"graph_nexus_analysis"` |
| `origin` | string | `"backtest"` or `"live"`; secondary index |
| `config_hash` | string | SHA256[:16] of canonical config (see 6.2); secondary index |
| `start_date` | ISO date string | backtest: lookback start; live: typically `null` |
| `end_date` | ISO date string | backtest: last processed date; live: most recent bar processed |
| `cache_blob` | object | JSON-serialized `_strategy_cache` (see 6.3) |
| `nexus_module_hash` | string | SHA256[:16] of `graph_nexus_analysis.py` contents; sanity check on load |
| `created_at` | ISO timestamp | tie-breaker when multiple matches |
| `record_version` | int | schema version; start at 1 |

**Compound secondary index:** `instance_id_config_hash` → `[instance_id, config_hash]` for the load query.

**Migration:** New columns are nullable; existing rows backfilled by a one-time RethinkDB update setting `origin="live"`, `config_hash="legacy"`, `nexus_module_hash="legacy"`, `record_version=0`. Load logic treats `config_hash="legacy"` as a non-match (falls back to full lookback).

### 6.2 `config_hash` composition

Canonical JSON of these fields, then SHA256[:16]:

- All 5 `_NEXUS_*_PROMPT_VERSION` constants (sentiment, macro, company, etc.)
- For each LLM stage: `provider`, `model`, `effort`
- `history_scope_id` ingredients: `neo4j_uri`, `neo4j_user`, `sentiment_cache_scope_salt`, `use_toon_format`, `num_articles_for_llm`
- `lookback_learning_days`
- `strategy_name`

Anything not in this list is "behaviorally neutral" — drift does not invalidate the snapshot. Document the full list in `backend/strategy_cache_persistence.py` so the rule is discoverable.

### 6.3 `_strategy_cache` serialization

Most contents serialize as plain JSON. Special-case handling:

- `set` → `list` (sorted deterministically on serialize, restored as `set` on deserialize)
- `OrderedDict` → list of `[k, v]` pairs, preserved order on restore
- `_neo4j_snapshot` (LRU cache) → **skipped**; recreated cold on first day-1 reads from Neo4j. Document in `__skipped_fields__` blob marker.
- Per-thread/lock/connection objects → **skipped**; recreated on load.
- Cached numpy/pandas objects → **skipped** if any exist; recreated on demand.

`__skipped_fields__` is a list of strings (field names) included in the cache_blob so future readers know what was intentionally omitted.

### 6.4 Snapshot load query (pseudocode)

```python
# in backend/strategy_cache_persistence.py
def load_with_fallback(instance_id, strategy_name, current_config_hash,
                      current_module_hash, staleness_days=7, conn=None):
    row = (
        r.table("NexusStrategyCache")
         .get_all([instance_id, current_config_hash],
                  index="instance_id_config_hash")
         .filter(r.row["strategy_name"] == strategy_name)
         .order_by(r.desc("end_date"), r.desc("created_at"))
         .limit(1)
         .nth(0)
         .default(None)
         .run(conn)
    )
    if row is None:
        return None, "no_match"
    if row.get("nexus_module_hash") != current_module_hash:
        return None, "module_drift"
    end_dt = parse_iso(row["end_date"])
    if (today() - end_dt).days > staleness_days:
        return None, "stale"
    try:
        cache = deserialize(row["cache_blob"])
    except Exception as e:
        log.error(f"snapshot deserialize failed: row_id={row['id']} err={e}")
        return None, "deserialize_error"
    return cache, "ok"
```

### 6.5 Gap-day lookback

After successful snapshot load:

```python
gap_dates = trading_days_between(snapshot.end_date + 1_day, today())
if gap_dates:
    run_live_historic_lookback(lookback_start_dates=gap_dates, ...)
# else: skip lookback entirely, go straight to main loop
```

The existing `_run_live_historic_lookback` already has resume-marker logic on `GraphNexusTradeContexts`; passing a narrow date range works with that logic.

## 7. Phase 1 — Code Surfaces

| File | Change | Approx LOC |
|---|---|---|
| `backend/strategy_cache_persistence.py` | Schema extension; `_compute_config_hash`, `_compute_module_hash`, `serialize_cache`, `deserialize_cache`, `persist_backtest_snapshot`, `load_with_fallback`. | ~200 new |
| `backend/broker.py` (backtest path, post-loop) | Call `persist_backtest_snapshot` at end of backtest main loop (right after `BacktestResults` write). Wrap in try/except so backtest doesn't fail if snapshot write fails. | ~30 |
| `backend/broker.py` (live boot, ~5057-5309) | Call `load_with_fallback`; branch on result; compute `gap_dates`; pass narrow range to `_run_live_historic_lookback`. Add boot-sequence logs (Section 8.2). | ~80 |
| `backend/broker.py` (live runtime save) | Add `origin="live"` and `config_hash` fields to the existing save call. | ~10 |
| `scripts/clear_main_instance_lookback_state.py` | Extend to clear 10 additional tables (NexusStrategyCache filtered to `origin="live"`, LiveOrderWAL, GraphNexusDiscoveredStocks, GraphNexusMarketTrends, GraphNexusRotationCooldown, GraphNexusTradeOutcomes, GraphNexusLearningCache, GraphNexusDiscoverySnapshots, GraphNexusOutcomeSeries, GraphNexusAnalystPanel). Combined with the 4 already cleared (NexusRuntimeState, GraphNexusTradeContexts, GraphNexusOutcomes, LiveState), the script covers all 14 tables that Phase 2 will version-isolate. Print before/after row-count summary. | ~150 new |
| `scripts/validate_live_launch_readiness.py` (NEW) | Pre-launch validation: snapshot presence/age/origin, RH position count, WAL open count, stale per-instance row count. Exit codes 0/1/2. | ~150 |
| `backend/tests/test_strategy_cache_persistence.py` (NEW) | Unit tests; see Section 9.1. | ~300 |
| `backend/tests/test_broker_live_boot_with_snapshot.py` (NEW) | Integration tests; see Section 9.2. | ~200 |
| `backend/tests/test_clear_main_instance_lookback_state.py` (NEW or extend) | Cleanup script tests; see Section 9.3. | ~100 |
| `docs/runbooks/live-launch-checklist.md` (NEW) | Operator runbook. | ~100 |

**Touch list outside these:** None. Strategy code (`graph_nexus_analysis.py`) is unchanged.

## 8. Phase 1 — Error Handling & Observability

### 8.1 Failure modes

| Failure | Detection | Behavior | Operator signal |
|---|---|---|---|
| No matching snapshot | Empty query result | Full lookback fallback | Boot log: `[snapshot] no match for (instance=main, config_hash=abc123); running full lookback` |
| `config_hash` differs | Query returns nothing for current hash | Same as no match | Same log line |
| `nexus_module_hash` differs | Compare on load | Reject; full lookback | Boot log + Discord: `[snapshot] rejected: module drift; full lookback running` |
| Stale snapshot | `end_date` > `staleness_days` (default 7) | Reject; full lookback | Boot log + Discord: `[snapshot] rejected: stale by N days` |
| `cache_blob` deserialize fails | try/except | Reject; log row id; full lookback | Discord alert with row id |
| Blob has dropped fields | `__skipped_fields__` marker | Hydrate present; log dropped; recreate cold | Boot log: `[snapshot] hydrated 47 keys; 3 cold (neo4j_snapshot, ...)` |
| Gap-day lookback errors | Existing handler | Existing behavior | Existing channel |
| Backtest fails to write snapshot | try/except in `persist_backtest_snapshot` | Don't fail backtest; log error | Backtest log + Discord |
| Concurrent backtest snapshot writes | PK includes `end_date` and `config_hash`; collisions rare | Take latest by `created_at` | None |
| Live rolling save fails | Existing handler | Don't crash; log; retry next save | Existing channel |

### 8.2 Boot-time log lines (always emitted, snapshot or no)

```
[live_boot] strategy_name=graph_nexus_analysis
[live_boot] instance_id=main config_hash=abc123 module_hash=def456
[snapshot] query: found row id=main|graph_nexus_analysis|abc123|backtest|2026-05-20
[snapshot] decision: USE (end_date=2026-05-20, gap_days=1, dropped_fields=[_neo4j_snapshot])
[snapshot] hydrated 47 keys into _strategy_cache
[lookback] running for 1 gap day(s): [2026-05-21]
[lookback] gap day 2026-05-21 done: 23 articles cached (hits=22, misses=1)
[live_boot] warm positions=0, F1b bypass=disabled, ramp from bar_index=0
[live_boot] _nexus_full_cycle_completed_date=2026-05-20; next FULL cycle expected ~6:30 AM PT
[live_boot] ready for live trading at 2026-05-21T13:30:00Z (06:30 PT)
```

Mirrored to Discord at INFO level.

### 8.3 Telemetry counters

Added via existing telemetry surface (analogous to LLMUsage tagging in session #8):

- `live_boot_snapshot_outcome`: enum `loaded | no_match | stale | module_drift | hash_mismatch | deserialize_error`
- `live_boot_gap_lookback_days`: int
- `live_boot_snapshot_age_seconds`: int

Visible on `/token-usage` page or similar; tiny line for tracking snapshot health.

### 8.4 Rollback

- Env flag / Instances-row config: `NEXUS_LIVE_SNAPSHOT_LOAD=off` (default `on`). When `off`, `load_with_fallback` returns `(None, "disabled")` immediately; full lookback runs.
- Env flag: `NEXUS_BACKTEST_SNAPSHOT_WRITE=off` (default `on`). When `off`, backtests skip snapshot write.
- Both flags surface via Instances row config (preferred) or process env (fallback). No code change needed to toggle.

## 9. Phase 1 — Test Plan

### 9.1 Unit tests (`backend/tests/test_strategy_cache_persistence.py`, ~25 tests)

- `test_config_hash_stable_across_dict_order`
- `test_config_hash_changes_on_prompt_version_bump`
- `test_config_hash_ignores_neutral_fields`
- `test_module_hash_stable_for_same_file`
- `test_module_hash_changes_on_file_edit`
- `test_serialize_handles_sets_and_ordereddicts`
- `test_serialize_drops_non_json_safe_fields_with_marker`
- `test_serialize_roundtrip_preserves_semantic_content`
- `test_deserialize_corrupt_blob_raises_clean_error`
- `test_deserialize_missing_marker_works`
- `test_persist_backtest_snapshot_writes_row`
- `test_persist_backtest_snapshot_handles_db_error`
- `test_persist_backtest_snapshot_idempotent_on_pk_collision`
- `test_load_with_fallback_returns_match`
- `test_load_with_fallback_returns_none_on_no_match`
- `test_load_with_fallback_rejects_stale_snapshot`
- `test_load_with_fallback_rejects_module_hash_drift`
- `test_load_with_fallback_rejects_deserialize_error`
- `test_load_with_fallback_picks_latest_by_end_date`
- `test_load_with_fallback_picks_latest_by_created_at_for_tie`
- `test_load_with_fallback_db_unavailable_returns_none`
- `test_load_with_fallback_env_flag_off_skips_query`
- `test_load_with_fallback_filters_by_strategy_name`
- `test_load_with_fallback_filters_by_config_hash`
- `test_load_with_fallback_includes_origin_in_result`

### 9.2 Integration tests (`backend/tests/test_broker_live_boot_with_snapshot.py`, ~10 tests)

- `test_backtest_writes_snapshot_at_end`
- `test_live_boot_loads_snapshot_and_skips_lookback`
- `test_live_boot_runs_gap_lookback_when_snapshot_partial`
- `test_live_boot_full_lookback_when_no_snapshot`
- `test_live_boot_full_lookback_when_module_hash_drift`
- `test_live_boot_full_lookback_when_stale`
- `test_env_flag_off_skips_snapshot_load`
- `test_rolling_live_save_uses_origin_live`
- `test_load_prefers_newer_origin_regardless_of_type`
- `test_boot_log_lines_emitted_in_expected_order`

### 9.3 Cleanup script tests

- `test_dry_run_lists_target_rows_without_deleting`
- `test_apply_deletes_only_target_instance`
- `test_apply_preserves_backtest_origin_snapshots`
- `test_apply_handles_missing_tables_gracefully`

### 9.4 Manual / operator acceptance (in the runbook)

1. Run backtest with `base_instance_id="main"` ending today; observe `[snapshot] persisted ...` in Discord.
2. Run cleanup script `--apply`; observe row-count summary.
3. Run `validate_live_launch_readiness.py`; expect GREEN.
4. Start live instance; tail logs; observe expected boot sequence (8.2).
5. Sanity: open `/backtests/<id>` AI Credits card → still renders (session #8 feature unbroken).
6. At 6:30 AM PT, first FULL cycle fires and a normal decision logs.

**Target:** ~50 new tests. Total project count after Phase 1: ~205-210 (from 158).

## 10. Phase 1 — Operator Runbook (summary; full text in `docs/runbooks/live-launch-checklist.md`)

**T-24h evening before:**
1. Lock strategy config (model, prompt versions, history_scope_id).
2. Kick off backtest with `base_instance_id="main"`, `end_date=today`.
3. Verify backtest log: `[snapshot] persisted: id=main|graph_nexus_analysis|<hash>|backtest|<end_date> bytes=<N>`.

**T-1h to T-30min morning of launch:**
4. Liquidate all Robinhood positions; wait for fills.
5. Verify cash equity == total equity (no T+1 pending). Acceptable to launch with small drift, but log a warning.
6. Run: `python scripts/clear_main_instance_lookback_state.py --apply --instance main`. Expect summary listing cleared row counts across all 14 per-instance tables (the original 4 + 10 added by Phase 1), with backtest-origin `NexusStrategyCache` row preserved.
7. Run: `python scripts/validate_live_launch_readiness.py`. Expect GREEN. YELLOW → read warning. RED → do not proceed.

**T-15min:**
8. Start the live instance (UI button or API).
9. Tail logs; observe the boot sequence from 8.2.
10. Discord posts a `🟢 Live launch ready` embed.

**T+0:**
11. First FULL cycle fires at ~6:30 AM PT; observe first decisions.
12. Sanity-check the AI Credits card on a recent backtest detail page.

**Rollback if anything looks wrong:**
- Set `NEXUS_LIVE_SNAPSHOT_LOAD=off` in Instances row → restart broker → fresh full 120-day lookback runs from scratch.
- Or stop the instance entirely; investigate; don't trade until issues resolved.

## 11. Phase 2 — Architecture (multi-week, separate implementation cycle)

Phase 2 takes Phase 1's `config_hash` concept and promotes it to `strategy_version`, then adds version-aware filtering to every per-instance table. The cleanup script becomes unnecessary because old-version data sits alongside new-version data, naturally invisible to the new strategy's reads.

### 11.1 Schema changes (~14 tables get a new column)

| Table | New column | Default for existing rows |
|---|---|---|
| `NexusStrategyCache` | `strategy_version` | derive from `config_hash` (already present from Phase 1) |
| `LiveOrderWAL` | `strategy_version` | `"legacy"` |
| `NexusRuntimeState` | `strategy_version` | derive from `scope_id` if present, else `"legacy"` |
| `GraphNexusDiscoveredStocks` | `strategy_version` | `"legacy"` |
| `GraphNexusMarketTrends` | `strategy_version` | `"legacy"` |
| `GraphNexusDiscoverySnapshots` | `strategy_version` | `"legacy"` |
| `GraphNexusOutcomes` | `strategy_version` | `"legacy"` |
| `GraphNexusTradeContexts` | `strategy_version` | `"legacy"` |
| `GraphNexusTradeOutcomes` | `strategy_version` | `"legacy"` |
| `GraphNexusOutcomeSeries` | `strategy_version` | `"legacy"` |
| `GraphNexusRotationCooldown` | `strategy_version` | `"legacy"` |
| `GraphNexusAnalystPanel` | `strategy_version` | `"legacy"` |
| `GraphNexusLearningCache` | `strategy_version` | `"legacy"` |
| `LiveState` | `strategy_version` | current value or `"legacy"` |

### 11.2 Read-path changes

Every query of the form:
```python
r.table(X).get_all(instance_id, index="instance_id")
```
becomes:
```python
r.table(X).get_all([instance_id, strategy_version], index="instance_id_strategy_version")
```

Estimated query sites: ~40-60 across `backend/strategies/graph_nexus_analysis.py`, `backend/broker.py`, `backend/nexus_*.py`, `backend/api/main.py`.

### 11.3 Strategy version source

- Defined as the `config_hash` from Phase 1 (already canonical, already used for snapshot matching).
- Exposed as a top-level `version_hash` field on the `Strategies` table, computed at strategy-config-save time, displayed in the UI.
- The UI shows the current `version_hash` for each strategy; if operator changes config, hash updates; persisted across saves.

### 11.4 What Phase 2 removes

- `scripts/clear_main_instance_lookback_state.py` becomes obsolete.
- The 3 BLOCKERS from session #4 get cleaner solutions (per-version state isolation).
- No more "did I forget to clear table X" worry.

### 11.5 What Phase 2 unlocks (Phase 3 territory, not designed here)

- A/B running: two strategy versions live on the same instance simultaneously, dividing capital.
- Historical comparison reports: "version A made these decisions vs version B on the same date."
- Hot strategy-version swap during a trading day (with appropriate capital handoff).

### 11.6 Why Phase 2 isn't in this implementation plan

- Multi-table schema migration is operationally risky; needs its own deployment plan, rollback plan, downtime estimate.
- Read-path changes touch a lot of code; needs careful test coverage.
- Best done after Phase 1 has been running stably for at least one launch cycle (so we learn what corners Phase 1 doesn't cover).

Phase 2 gets its own `brainstorm → spec → plan → implement` cycle.

## 12. Open Questions / Future Work

- Should `staleness_days` be configurable per-instance (in Instances row) rather than a constant? (Phase 2.)
- Should `_neo4j_snapshot` be serializable? Currently skipped; cold-recreate on day 1 reads from Neo4j is acceptable but slows first FULL cycle. (Investigate in Phase 2.)
- Should the snapshot blob be compressed (e.g. zstd) if it grows large? Current estimate: a few hundred KB. (Defer until measured.)
- How does Phase 2's `strategy_version` interact with the Models UI (session #8) when operator swaps providers/models? (Phase 2 design.)

## 13. References

- Brainstorming session: this conversation, 2026-05-21.
- Parallel deep-dive agents output: see conversation transcript.
- Session #4 deferred-live-mode notes: `.sessions/2026-05-17-220000-tier3-missed-rally-fixes-and-deferred-live-mode-spec.md` §9.
- Existing live-mode boot code: `backend/broker.py:4920-5373`.
- Existing strategy cache persistence: `backend/strategy_cache_persistence.py`.
- Existing cleanup script: `scripts/clear_main_instance_lookback_state.py`.
- Session #8 handoff (preceding context): `.sessions/2026-05-20-173400-codex-cli-provider-shipped-and-per-backtest-cost-feature.md`.
