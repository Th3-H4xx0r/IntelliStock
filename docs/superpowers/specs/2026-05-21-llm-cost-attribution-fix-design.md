# LLM Cost Attribution Fix — Design Spec

**Date:** 2026-05-21
**Status:** Design approved, ready for implementation planning.

## 1. Problem Statement

LLM cost rows produced during recent backtests show `backtest_id="main|<24-char-hex>"` instead of the numeric `BacktestResults.id`. This is the **scoped runtime instance ID** (`f"{base}|{scope_id}"`) that the Nexus strategy uses internally for cache scoping. It is incorrectly being passed as the `backtest_id` field in nested `llm_call_context(...)` calls within `backend/strategies/graph_nexus_analysis.py`, overriding the correct numeric ID set by the broker's outer telemetry context.

**Two user-visible symptoms:**

1. Cost screen ("LLM cost by backtest") shows rows labeled `#main|38727a97a6bd04cda7fc45b6` instead of `#877964` for backtest 877964.
2. Backtest detail screen (`/backtests/<id>`) AI Credits card shows "No LLM calls were attributed to this backtest" because the lookup query keys on numeric `backtest_id` but rows have the scoped-string value.

There is also no current separation between live-mode LLM costs and backtest LLM costs.

## 2. Goals

- After this fix, every `LLMUsage` row's `backtest_id` field is **either** a numeric string matching `BacktestResults.id` (backtest mode) **or** `None` (live mode).
- The cost screen distinguishes "Backtest #N" rows from "Live: <instance_id>" rows in a single ranked table.
- The backtest detail screen's AI Credits card correctly finds the LLM calls attributed to that backtest.

## 3. Non-Goals

- No backfill of pre-existing buggy rows — they will be wiped entirely.
- No schema migration (only field semantics tighten).
- No change to how the scoped runtime instance ID is constructed in broker.py:2842 — it remains valid for cache scoping inside `graph_nexus_analysis`.
- No change to `llm_call_context`'s context-stack-merge semantics.
- No new "mode" enum column on `LLMUsage` (presence of `backtest_id` already encodes it).

## 4. Decisions

| Decision | Choice |
|---|---|
| Cost screen layout | Single "LLM cost by run" table with a `kind` column tagging `backtest` vs `live` |
| BT877964 (currently running) | Stop it pre-deploy, deploy fix, user re-runs |
| Historical `LLMUsage` data | Wipe entire table before deploying |
| Mode encoding | Implicit: `backtest_id NULL` ⇒ live, `backtest_id NOT NULL` ⇒ backtest |
| New columns | None |

## 5. Root Cause

`backend/strategies/graph_nexus_analysis.py` has six nested `llm_call_context(...)` calls that pass `backtest_id=instance_id`. The `instance_id` variable at those sites is the **scoped runtime instance ID** (`f"{base}|{scope_id}"`), not a backtest ID.

The telemetry stack (`backend/llm_telemetry.py` lines 230-235) merges frames such that later (inner) frames override earlier (outer) frames for non-`None` values. The broker's outer frame correctly sets `backtest_id=<numeric>` (backtest) or `None` (live) — but the strategy's inner frame overrides it.

Sites:
| Line | Call site label | Value passed |
|---|---|---|
| 3227 | `company_classification` (main) | `backtest_id=instance_id` |
| 3278 | `company_classification` (retry split) | `backtest_id=instance_id` |
| 3433 | `macro_classification` (main) | `backtest_id=instance_id` |
| 3493 | `macro_classification` (retry split) | `backtest_id=instance_id` |
| 4323 | `active_event_maintenance` | `backtest_id=instance_id` |
| 13901 | `sentiment` | `backtest_id=_sent_instance_id` |

Other strategies (`earnings`, `ml_news`, `nexus_analyst_panel`) already do this correctly — they pass only `strategy=` and `call_site=` in their inner contexts and let the outer broker frame's `backtest_id` stand.

## 6. Architecture & Data Flow (post-fix)

```
BACKTEST RUN (instance_id="<numeric BacktestInstances id>", base_instance_id="main")
   broker.py main loop:
     broker_backtest_id = "<numeric>"
     broker_instance_id = "<numeric>"
     with telemetry_llm_call_context(backtest_id="<numeric>", instance_id="<numeric>"):
         graph_nexus_analysis.run_once(..., config={..., "runtime_instance_id": "main|<hash>"})
             with llm_call_context(strategy="GraphNexusAnalysis", call_site="..."):  ← FIXED: no backtest_id
                 LLM call → record: backtest_id="<numeric>", instance_id="<numeric>"

LIVE RUN (instance_id="main")
   broker.py main loop:
     broker_backtest_id = None
     broker_instance_id = "main"
     with telemetry_llm_call_context(backtest_id=None, instance_id="main"):
         graph_nexus_analysis.run_once(..., config={..., "runtime_instance_id": "main|<hash>"})
             with llm_call_context(strategy="GraphNexusAnalysis", call_site="..."):  ← FIXED
                 LLM call → record: backtest_id=None, instance_id="main"
```

**Cost aggregation logic** (in the backend endpoint):

```
For LLMUsage rows in [time window]:
    if row.backtest_id is not None and is not empty:
        bucket by backtest_id → kind="backtest", display_label="Backtest #<id>"
    else:
        bucket by instance_id → kind="live",     display_label="Live: <instance_id>"

Sort buckets by total cost desc, apply limit.
```

## 7. Code Changes

### 7.1 `backend/strategies/graph_nexus_analysis.py`

Remove the `backtest_id=` argument from each of the six `llm_call_context(...)` calls. Keep `strategy=` and `call_site=`. The outer broker context's `backtest_id` value then propagates correctly through the merge.

### 7.2 `backend/api/main.py` — extend cost aggregation

`_llm_usage_by_backtest(range_str, limit, conn)`:

1. Query all rows in the time window (existing query path).
2. Partition rows: `backtest_id IS NOT NULL` → backtest buckets keyed by `backtest_id`; otherwise → live buckets keyed by `instance_id`.
3. For each bucket emit:
   - `kind`: `"backtest"` or `"live"`
   - `key`: the bucket key (numeric backtest id, or instance_id string)
   - `display_label`: `"Backtest #<key>"` or `"Live: <key>"`
   - `instance_id`, `started_at` (min ts), `last_at` (max ts), `calls`, `tokens_in`, `tokens_out`, `tokens_reasoning`, `cost_usd`, `success_count`, `failure_count`
4. Sort by `cost_usd` desc, cap at `limit`.

`/backtests/{backtest_id}/llm-cost` (per-backtest detail endpoint) — no logic change, just confirm it does `r.table('LLMUsage').get_all(backtest_id, index='backtest_id')` (it does).

### 7.3 `frontend/src/views/TokenUsageView.vue`

- Section heading: `"LLM cost by backtest"` → `"LLM cost by run"`.
- Replace the `BACKTEST` column header with a row label that uses the API's new `display_label`.
- Add a `KIND` column (or pill chip) showing `BACKTEST` or `LIVE`.
- Row click handler: only navigate to backtest detail when `kind === "backtest"`. For live rows, no navigation (or link to instance detail if such a route exists).

### 7.4 `frontend/src/views/BacktestDetailView.vue`

No code changes. The AI Credits card will work automatically because LLM rows now have the correct numeric `backtest_id`.

### 7.5 Tests

`backend/tests/test_api_llm_usage.py` — add three tests on top of existing five:

- `test_by_backtest_groups_backtest_rows_by_backtest_id`: seed two LLMUsage rows with `backtest_id="100"` and one with `backtest_id="200"`; assert two backtest buckets with correct sums.
- `test_by_backtest_includes_live_rows_with_null_backtest_id`: seed LLMUsage rows with `backtest_id=None` and `instance_id="main"` (×3) and `instance_id="paper"` (×1); assert two live buckets emerge with `kind="live"`.
- `test_by_backtest_emits_kind_and_display_label`: assert both `kind` and `display_label` fields present in every row of the response.

`backend/tests/test_llm_telemetry.py` — no change needed (context-stack semantics unchanged).

## 8. Operational Rollout

1. **Stop BT877964.** Via API: `POST /backtests/877964/stop`. Confirm `BacktestResults.status` flips to `stopped`. This halts new buggy-ID writes.
2. **Audit other running backtests** on `instance="main"`. Stop any others (operator's call).
3. **Wipe `LLMUsage` and `LLMUsageDaily`** in production RethinkDB. Single `r.table('LLMUsage').delete().run(conn)` plus the daily rollup.
4. **Commit + push** all code changes (strategy + endpoint + frontend + tests) in a coherent set.
5. **Wait for Dockploy redeploy** (~4 min).
6. **Post-deploy validation:**
   - Kick a small smoke backtest (short date range, ~1-2 days, instance=main).
   - Watch first LLM row land in `LLMUsage` — confirm `backtest_id` is numeric, `instance_id` is numeric.
   - Open `/cost`: row appears as `Backtest #<id>` with `KIND` pill `BACKTEST`.
   - Open `/backtests/<id>`: AI Credits card populates.
7. **Then** operator re-runs full backtest (2025-11-10 → 2026-05-21) or starts live instance.

**Rollback:** If post-deploy smoke shows wrong attribution, `git revert` the strategy + endpoint commits, redeploy. Cost screen returns to empty (we wiped), no new bad data accumulates.

## 9. Testing Plan

| Layer | Test | What it verifies |
|---|---|---|
| Unit | New `test_api_llm_usage.py` tests (×3) | Aggregation correctly partitions backtest vs live and emits `kind`/`display_label`. |
| Unit | Existing `test_llm_telemetry.py` | No regression — context-stack semantics unchanged. |
| Unit | Existing Phase 1 tests (strategy_cache + broker_live_boot) | No regression in adjacent code. |
| Integration | Smoke backtest post-deploy | End-to-end attribution + UI display. |
| Build | `npx vite build` | Frontend renders without errors. |

## 10. What This Spec Does NOT Change

- `_resolve_nexus_runtime_identity` and the scoped runtime instance ID — still used internally for cache scoping, sentiment-cache scope keys, and the `History scope` log line in `graph_nexus_analysis`.
- Any RethinkDB schema (no new columns).
- `LLMUsage` indexes (already on `backtest_id` and `instance_id`).
- The Phase 1 snapshot mechanism (independent surface).
- LLM call cost computation (pricing YAML, token accounting, USD conversion).

## 11. Open Questions / Future Work

- Could add a `mode` enum column ("backtest"|"live") later if implicit derivation gets confusing. Not needed today.
- Live row click could link to an instance detail page if/when such a page exists.
- The `daily_rollup` view also derives from `LLMUsage` — once we wipe the daily aggregation, it'll naturally rebuild as new rows land.

## 12. References

- Bug surface: `backend/strategies/graph_nexus_analysis.py` (6 sites listed in §5)
- Aggregation endpoint: `backend/api/main.py` `_llm_usage_by_backtest`
- Telemetry stack semantics: `backend/llm_telemetry.py:230-235`
- Phase 1 snapshot spec (adjacent context): `docs/superpowers/specs/2026-05-21-live-mode-safe-startup-design.md`
