# Nexus Model Switch — "Preserve History" Design

**Date:** 2026-06-29
**Strategy affected:** `graph_nexus_analysis` (equities). Driving case: the live real-money `alpaca-main` instance (Strategies doc 179).
**Status:** Approved design, ready for implementation plan.

## Problem

Changing the LLM model(s) for a `graph_nexus_analysis` instance — i.e. editing any
`*_llm_model_id` key in the Strategies config — silently triggers a destructive,
expensive transition on the next instance boot. The operator's intent ("just use the
new model going forward, leave my history alone") is **not** what the code does today.

A model change alters **two independent identities**, each with its own consequence:

1. **`_compute_config_hash` (16-char)** — includes `llm_stages` (provider/model/effort).
   Changing it makes the live-boot snapshot lookup return `no_match`
   (`strategy_cache_persistence.load_with_fallback`), which signals a historic lookback.
2. **`history_scope_id` (24-char)** — `broker._nexus_history_scope_doc` hashes the
   per-role provider/model/prompt. Changing it makes the cleanup marker
   (`GraphNexusLearningCache` row `cleanup_done|<instance_id>`) mismatch, which fires the
   **destructive config-change cleanup**.

### Why today's behavior is actively bad (not just a re-run)

- The cleanup deletes, **by scoped instance id**, all rows for the instance in:
  `GraphNexusDiscovered`, `GraphNexusTrends`, `GraphNexusActiveEvents`,
  `GraphNexusActiveEventHistory`, `GraphNexusActiveEventMaintenance`,
  `GraphNexusOutcomes`, and `GraphNexusLearningCache` (except the `cleanup_done|…` marker).
- The lookback's resume logic (`nexus_lookback_db.historic_lookback_resume_dates`) gates
  **only** on `instance_id + date_key`. Dates that already have `GraphNexusTradeContexts`
  rows are **skipped**, so the lookback does **not** rebuild the wiped discoveries/trends/
  events/learning for those dates.
- Net result of a naive model swap: discoveries/trends/events/learning are **wiped and
  not rebuilt**, while raw `TradeContexts`/`TradeOutcomes`/`OutcomeSeries` linger under the
  **old** model's stamp — a degraded, half-reset analytical state.

Real Alpaca positions and realized P&L are **never** touched by any of this (they live
server-side at the broker). The damage is confined to the Nexus analytical layer that
feeds decisions and the dashboard nexus cards.

## Goal

Give the operator an explicit, safe choice at save time. When an edit would change either
identity for an instance that has existing live state, prompt:

> **This changes the analysis model for `<instance>`. Preserve existing history and apply
> the new model only going forward?**
> **[ Preserve & apply forward ]  [ Full rebuild ]  [ Cancel ]**

- **Preserve & apply forward** — re-stamp the existing saved state to the new identities so
  the next boot reuses everything as-is and the new model applies only to new dates. No
  lookback, no cleanup.
- **Full rebuild** — today's behavior (full lookback + cleanup on next boot).
- **Cancel** — abort the save.

Non-goals: changing backtest behavior; changing what `_compute_config_hash` /
`history_scope_id` mean globally; touching the broker adapter or live trading loop.

## Approach

**Centralize the two identities, then re-stamp on save.**

The single biggest correctness risk is **hash parity**: the re-stamped hashes must exactly
equal what the broker computes at boot, or "preserve" silently fails and the instance does
the destructive rebuild anyway. To guarantee parity we extract the identity computations
into one shared, broker-free module and make boot call the same functions the feature calls.

Alternatives considered and rejected:
- *One-off manual migration script* — does not give the reusable popup the operator wants.
- *Make the model hash-agnostic globally* — breaks backtest comparability and snapshot
  correctness everywhere.

## Components

### Backend

1. **`backend/nexus_config_identity.py`** (new, no broker import)
   - `live_config_hash(resolved_cfg: dict) -> str` — the 16-char snapshot identity. Wraps the
     existing `_collect_prompt_versions` / `_collect_llm_stages` / `_collect_history_scope_inputs`
     (from `broker_snapshot_helpers`) + `lookback_learning_days`, fed to
     `strategy_cache_persistence._compute_config_hash`. Exactly mirrors `broker.py:5659`.
   - `history_scope_id(resolved_cfg: dict) -> str` — the 24-char cleanup identity. Moves
     `broker._nexus_history_scope_doc` + `_nexus_history_scope_id` here verbatim.
   - **`broker.py` is refactored** to import and call these two functions at the boot
     snapshot-hash site and the history-scope site, so there is one source of truth.
     This is the parity guarantee; it is the only change to boot.

2. **`backend/nexus_restamp.py`** (new, no broker import)
   - `resolve_for_identity(conn, raw_cfg) -> dict` — `resolve_model_refs_in_config(force_refresh=True)`
     then `live_mode_overrides._apply_live_overrides` (mirroring boot's order), so the
     computed identities match a live boot.
   - `preview_change(conn, strategy_id, proposed_strategies) -> dict` — for each instance
     linked to the strategy doc (`Instances.strategy_id == strategy_id`), compute the
     proposed `live_config_hash` + `history_scope_id`, compare against the instance's current
     saved-state identities (current snapshot row `config_hash`; current `cleanup_done|…`
     marker `config_hash`). Returns per-instance `{base_instance_id, would_rebuild,
     snapshot_exists}` plus an overall `needs_prompt` boolean. Read-only.
   - `restamp_instance(conn, base_instance_id, resolved_cfg) -> dict` — idempotent. Computes
     the new identities and:
     - **Snapshot** (`NexusStrategyCache`, keyed by **base** id): for each live-origin row of
       this instance, write a re-stamped copy with the new `config_hash` and rebuilt
       `id = "{base}|{strategy}|{newhash}|live|{end_date}"`; keep `nexus_module_hash`,
       `end_date`, `cache_json` unchanged; refresh `updated_at`/`updated_at_epoch`. Leave the
       old row in place (harmless, lets a revert still match).
     - **Cleanup marker** (`GraphNexusLearningCache`, keyed by **scoped** id): for each
       `cleanup_done|<scoped>` row whose scoped id starts with `<base>`, set its `config_hash`
       field to the new `history_scope_id`. Scoped ids are **discovered from existing rows**,
       not recomputed, to avoid drift.
   - **Base-vs-scoped keying is explicit and tested** — snapshot uses base id, marker uses
     scoped id; mixing them is how a "preserve" silently fails.

3. **`backend/interactive_utils.py`**
   - `action_preview_strategy_config_change(conn, strategy_id, strategies)` → wraps
     `nexus_restamp.preview_change`.
   - Extend `action_edit_strategy(conn, strategy_id, name, strategies, preserve_history=False)`:
     after the existing config write, if `preserve_history` is true, resolve the new config and
     call `restamp_instance` for each linked instance, returning a summary of what was re-stamped.

4. **`backend/api/main.py`**
   - `POST /strategies/{strategy_id}/config-change-preview` (read-only dry-run; mirrors the
     existing `/instances/{id}/clear-state` `apply=false` pattern) → `action_preview_strategy_config_change`.
   - Extend `PUT /strategies/{strategy_id}` body (`EditStrategyBody`) with optional
     `preserve_history: bool = False`, passed through to `action_edit_strategy`.

### Web (`frontend/src/views/InstanceDetailView.vue`)

- `submitEditStrategy` first POSTs to `config-change-preview` with the proposed `strategies`.
- If `needs_prompt`, open a modal listing the affected instance(s) with the three choices.
  - **Preserve & apply forward** → `PUT /strategies/{id}` with `preserve_history: true`.
  - **Full rebuild** → `PUT` with `preserve_history: false` (current behavior).
  - **Cancel** → abort, leave the editor open.
- If `!needs_prompt`, save directly as today (no behavior change for non-hash edits).
- Modal copy states: takes effect on next Stop→Start; positions/P&L are unaffected; the
  preserved learned state was trained on the prior model and the new model inherits it.

### Mobile (Flutter)

- On the strategy config screen (the `*_llm_model_id` picker that calls
  `strategy_repository.update` → `PUT /strategies/:id`): before saving, call the preview
  endpoint; if `needs_prompt`, show an `AlertDialog` with the same three choices; pass
  `preserve_history` in the update body.
- `strategy_repository` gains a preview call and a `preserveHistory` parameter on `update`.

## Data flow

```
edit models → Save
  → POST /strategies/{id}/config-change-preview {strategies}
      → resolve proposed cfg → live_config_hash + history_scope_id per linked instance
      → compare vs current snapshot.config_hash + marker.config_hash
      → { needs_prompt, instances:[{base_instance_id, would_rebuild, snapshot_exists}] }
  → if needs_prompt: popup [Preserve | Full rebuild | Cancel]
  → PUT /strategies/{id} { name, strategies, preserve_history }
      → write Strategies config (unchanged path)
      → if preserve_history: for each linked instance, restamp_instance()
          → re-stamp NexusStrategyCache row(s)  (BASE id)  → new live_config_hash
          → set GraphNexusLearningCache cleanup_done marker(s) (SCOPED id) → new history_scope_id
  → operator Stop → Start
      → boot computes identities via shared module → snapshot MATCHES, marker MATCHES
      → no lookback, no cleanup; new model applies to new dates only
```

## Edge cases & risks

- **Hash parity (keystone).** Mitigated by the shared identity module + a parity test that
  asserts `restamp_instance`'s computed hashes equal the values boot derives for the same
  resolved config. If parity ever breaks, preserve degrades to full rebuild (safe-ish:
  data loss, not money loss) — but the test must guard it.
- **Multiple linked instances / scope-suffixed ids.** Re-stamp every linked base instance;
  discover scoped marker rows from the DB rather than recomputing the scope suffix.
- **No live snapshot exists** (instance never ran) → `would_rebuild` may be true but
  `snapshot_exists` false → `needs_prompt` stays false; nothing to preserve, save directly.
- **Running instance.** Re-stamp is a DB edit; it takes effect on the next Stop→Start (the
  model change already requires a restart today). A running process writing a fresh snapshot
  under the old hash after re-stamp is harmless — our new-hash row still exists.
- **Partial config edits that change only `history_scope_id` but not `config_hash`** (or vice
  versa). `preview_change` checks both independently and `restamp_instance` always updates
  both targets, so either-direction drift is neutralized.
- **Idempotency.** Re-running `restamp_instance` with the same config is a no-op (rows already
  carry the new identities).

## Testing

- **Unit (`backend/`):**
  - `live_config_hash` / `history_scope_id`: stable for unchanged config; both change when a
    `*_llm_model_id` changes; `history_scope_id` unaffected by behaviorally-neutral keys.
  - `restamp_instance`: writes snapshot row under **base** id with correct new `id`+`config_hash`;
    sets marker(s) under **scoped** id to new `history_scope_id`; leaves other fields intact;
    idempotent; handles multiple linked instances and multiple scoped markers.
  - `preview_change`: `needs_prompt` true only when an identity changed **and** a snapshot
    exists; correct per-instance flags.
- **Parity/integration:** assert the identities written by `restamp_instance` equal those the
  broker boot path computes for the same resolved config (import the shared module both sides).
- **Web:** component test for the preview→modal→PUT branch (preserve / full / cancel).
- **Mobile:** widget test for the confirm dialog + `preserveHistory` passthrough; `flutter analyze` clean.
- No test touches prod RethinkDB; use a local/ephemeral DB or fakes.

## Out of scope

- Backtest cleanup/lookback semantics.
- The Kalshi model path (`kalshi_config.model`) — different mechanism, unaffected.
- Any change to live trading execution.
