# Nexus Model Switch — Preserve History Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a save-time "Preserve history" choice so changing a `graph_nexus_analysis` instance's LLM model re-stamps existing Nexus state to the new identities instead of triggering a destructive lookback + cleanup on next boot.

**Architecture:** Centralize the two boot identities (`live_config_hash`, `history_scope_id`) in one broker-free module that boot also calls (parity guarantee). A re-stamp module updates the snapshot row (base instance id) and cleanup marker (scoped instance id) to the new identities. A read-only preview endpoint drives a three-button popup on web + mobile; the existing `PUT /strategies/{id}` gains a `preserve_history` flag.

**Tech Stack:** Python (FastAPI, RethinkDB/`rethinkdb` driver, pytest), Vue 3 (web), Flutter/Dart (mobile).

## Global Constraints

- Real-money instance (`alpaca-main`, doc 179) — broker positions/P&L must never be touched; only Nexus analytical-layer DB rows.
- **Hash parity is mandatory:** re-stamped identities MUST equal boot-computed identities for the same resolved config. Boot and the feature call the SAME functions.
- Snapshot table `NexusStrategyCache` is keyed by **base** instance id; cleanup marker `GraphNexusLearningCache` `cleanup_done|<id>` is keyed by **scoped** instance id. Never mix them.
- Resolve order mirrors boot exactly: `resolve_model_refs_in_config(force_refresh=True)` → `_apply_live_overrides` → compute identities.
- Local env: use `python3` (3.14); `python-socketio` not installed (broker-import tests error at collection — avoid importing `broker` in new tests).
- No test may hit prod RethinkDB.

---

### Task 1: Extract shared identity module + refactor boot to use it

**Files:**
- Create: `backend/nexus_config_identity.py`
- Modify: `backend/broker.py` (history-scope helpers `_nexus_history_scope_doc`/`_nexus_history_scope_id` + their `_role_*` deps; boot config-hash site ~5659; history-scope site)
- Test: `backend/tests/test_nexus_config_identity.py`

**Interfaces:**
- Produces:
  - `live_config_hash(resolved_cfg: dict, *, strategy_name: str = "graph_nexus_analysis", lookback_days_default: int = 120) -> str` (16-char)
  - `history_scope_id(resolved_cfg: dict) -> str` (24-char)

- [ ] **Step 1: Write failing tests** (`backend/tests/test_nexus_config_identity.py`) — import `nexus_config_identity` only (NOT broker):

```python
import nexus_config_identity as nci

BASE = {
    "discovery_llm_provider": "bedrock", "discovery_llm_model": "kimi-k2.5",
    "sentiment_llm_provider": "bedrock", "sentiment_llm_model": "kimi-k2.5",
    "company_llm_provider": "bedrock", "company_llm_model": "kimi-k2.5",
    "macro_llm_provider": "bedrock", "macro_llm_model": "kimi-k2.5",
    "analyst_llm_provider": "bedrock", "analyst_llm_model": "kimi-k2.5",
    "lookback_learning_days": 120,
}

def test_config_hash_stable_and_16char():
    h = nci.live_config_hash(dict(BASE))
    assert h == nci.live_config_hash(dict(BASE)) and len(h) == 16

def test_config_hash_changes_on_model():
    a = nci.live_config_hash(dict(BASE))
    b = nci.live_config_hash({**BASE, "analyst_llm_model": "nemotron-3-ultra"})
    assert a != b

def test_history_scope_changes_on_model_stable_on_neutral():
    a = nci.history_scope_id(dict(BASE))
    assert a == nci.history_scope_id(dict(BASE)) and len(a) == 24
    assert a == nci.history_scope_id({**BASE, "some_neutral_key": "x"})
    assert a != nci.history_scope_id({**BASE, "analyst_llm_model": "nemotron-3-ultra"})

def test_explicit_history_scope_id_passthrough():
    assert nci.history_scope_id({"history_scope_id": "abc123"}) == "abc123"
```

- [ ] **Step 2: Run, verify import-fail.** `cd backend && python3 -m pytest tests/test_nexus_config_identity.py -q` → FAIL (module missing).

- [ ] **Step 3: Read the source to move.** Read `backend/broker.py` lines ~2840-2935 for `_nexus_history_scope_doc`, `_nexus_history_scope_id`, and the `_role_provider`/`_role_model`/`_role_prompt` helpers they call. Read `backend/broker.py:5655-5675` for the exact `_compute_config_hash({...})` boot call.

- [ ] **Step 4: Write `backend/nexus_config_identity.py`.** Move the history-scope functions verbatim, parameterized to take a `settings: dict` (the resolved config) explicitly with no broker globals. Wrap the snapshot hash:

```python
import strategy_cache_persistence as scp
from broker_snapshot_helpers import (
    _collect_prompt_versions, _collect_llm_stages, _collect_history_scope_inputs,
)

def live_config_hash(resolved_cfg, *, strategy_name="graph_nexus_analysis", lookback_days_default=120):
    cfg = resolved_cfg or {}
    return scp._compute_config_hash({
        "strategy_name": strategy_name,
        "prompt_versions": _collect_prompt_versions(cfg),
        "llm_stages": _collect_llm_stages(cfg),
        "history_scope_id_inputs": _collect_history_scope_inputs(cfg),
        "lookback_learning_days": int(cfg.get("lookback_learning_days", lookback_days_default) or lookback_days_default),
    })

# _nexus_history_scope_doc(settings) and history_scope_id(settings) moved from broker.py
```

  Confirm `broker_snapshot_helpers` and `strategy_cache_persistence` import without pulling in `broker` (they do per investigation).

- [ ] **Step 5: Refactor `broker.py`** to import from the new module and delete the moved bodies: at the boot config-hash site replace the inline `_compute_config_hash({...})` with `nexus_config_identity.live_config_hash(_bt_cfg_load)`; replace `_nexus_history_scope_id(...)` calls with `nexus_config_identity.history_scope_id(...)`. Keep names available via `from nexus_config_identity import history_scope_id as _nexus_history_scope_id` if other call sites use the old name.

- [ ] **Step 6: Run tests + boot import smoke.** `python3 -m pytest tests/test_nexus_config_identity.py -q` → PASS. `python3 -c "import ast,sys; ast.parse(open('broker.py').read())"` → no syntax error.

- [ ] **Step 7: Commit.** `git add backend/nexus_config_identity.py backend/tests/test_nexus_config_identity.py backend/broker.py && git commit -m "feat(nexus): extract shared config-identity module; boot uses it"`

---

### Task 2: Re-stamp module (preview + restamp)

**Files:**
- Create: `backend/nexus_restamp.py`
- Test: `backend/tests/test_nexus_restamp.py`

**Interfaces:**
- Consumes: `nexus_config_identity.live_config_hash/history_scope_id`; `model_resolver.resolve_model_refs_in_config`; `live_mode_overrides._apply_live_overrides`; `strategy_cache_persistence` (table name `NexusStrategyCache`).
- Produces:
  - `resolve_for_identity(conn, raw_cfg: dict) -> dict`
  - `linked_base_instance_ids(conn, strategy_id) -> list[str]`
  - `preview_change(conn, strategy_id, proposed_strategies: list) -> dict` → `{"needs_prompt": bool, "instances": [{"base_instance_id", "would_rebuild", "snapshot_exists"}]}`
  - `restamp_instance(conn, base_instance_id: str, resolved_cfg: dict) -> dict` → `{"snapshots_restamped": int, "markers_restamped": int, "config_hash", "history_scope_id"}`

- [ ] **Step 1: Write failing tests** using a fake RethinkDB conn/`r` (in-memory dicts) — do NOT import broker. Test that:
  - `restamp_instance` writes a new `NexusStrategyCache` row keyed by **base** id with `id` ending `|{newhash}|live|{end_date}` and `config_hash == newhash`, leaving the old row.
  - It sets every `GraphNexusLearningCache` row `cleanup_done|<scoped>` whose `instance_id` startswith base to `config_hash == history_scope_id(resolved_cfg)`.
  - Idempotent: second call makes no further changes.
  - `preview_change` returns `needs_prompt=False` when proposed identities equal current; `True` when changed and a snapshot exists; `needs_prompt=False` when changed but no snapshot exists.

  (Inject `r` and `conn` so the test substitutes fakes; the module takes `r`/`conn` as params or imports the project's `r` — match `strategy_cache_persistence` style which uses module-level `r`.)

- [ ] **Step 2: Run, verify fail.** `python3 -m pytest tests/test_nexus_restamp.py -q` → FAIL.

- [ ] **Step 3: Implement `backend/nexus_restamp.py`.** Use the same `r`/DB_NAME import pattern as `strategy_cache_persistence.py`. `resolve_for_identity` = `resolve_model_refs_in_config(conn, raw_cfg, force_refresh=True)` then `_apply_live_overrides`. `linked_base_instance_ids` queries `Instances.filter(strategy_id == sid).pluck("id")`. Snapshot re-stamp: read latest live-origin row(s) for `instance_id == base`, copy with new `id`/`config_hash`, refresh `updated_at*`, `insert(conflict="replace")`. Marker re-stamp: `table("GraphNexusLearningCache").filter(id matches "^cleanup_done\\|" AND instance_id startswith base).update({"config_hash": new_scope})`. Pull the proposed sub-strategy config out of `proposed_strategies` by matching `strategy == "graph_nexus_analysis"`.

- [ ] **Step 4: Run tests** → PASS.

- [ ] **Step 5: Commit.** `git add backend/nexus_restamp.py backend/tests/test_nexus_restamp.py && git commit -m "feat(nexus): preview + restamp module for model-switch preserve"`

---

### Task 3: Parity test (keystone)

**Files:**
- Test: `backend/tests/test_nexus_restamp_parity.py`

- [ ] **Step 1: Write the parity test.** For a representative resolved config, assert `nexus_restamp` and `nexus_config_identity` agree, and that the identity strings are exactly what boot derives — by importing `nexus_config_identity` (the single source boot now uses) and asserting `restamp_instance`'s computed `config_hash`/`history_scope_id` equal `live_config_hash`/`history_scope_id` of the same resolved cfg. (This guards that re-stamp can never write an identity boot won't match.)

```python
import nexus_config_identity as nci
import nexus_restamp as nr

def test_restamp_identities_match_boot(monkeypatch):
    cfg = {"analyst_llm_provider":"bedrock","analyst_llm_model":"nemotron-3-ultra","lookback_learning_days":120}
    # stub resolve_for_identity to return cfg unchanged
    monkeypatch.setattr(nr, "resolve_for_identity", lambda conn, c: c)
    out = nr._identities_for(cfg)  # small helper returning (config_hash, history_scope_id)
    assert out == (nci.live_config_hash(cfg), nci.history_scope_id(cfg))
```

- [ ] **Step 2: Add `_identities_for(cfg)` helper** to `nexus_restamp.py` if not present (returns the tuple used by `restamp_instance`).
- [ ] **Step 3: Run** → PASS.
- [ ] **Step 4: Commit.** `git commit -am "test(nexus): parity guard restamp identities == boot identities"`

---

### Task 4: Backend action handlers + API endpoints

**Files:**
- Modify: `backend/interactive_utils.py` (`action_edit_strategy`; new `action_preview_strategy_config_change`)
- Modify: `backend/api/main.py` (`EditStrategyBody`; new preview route)
- Test: `backend/tests/test_action_preview_and_preserve.py`

**Interfaces:**
- Consumes: `nexus_restamp.preview_change/restamp_instance/resolve_for_identity/linked_base_instance_ids`.
- Produces: `action_preview_strategy_config_change(conn, strategy_id, strategies) -> dict`; `action_edit_strategy(conn, strategy_id, name=None, strategies=None, preserve_history=False) -> dict`.

- [ ] **Step 1: Write failing tests** for `action_edit_strategy(..., preserve_history=True)` calling `restamp_instance` for each linked instance (monkeypatch `nexus_restamp`), and `preserve_history=False` NOT calling it; `action_preview_strategy_config_change` delegating to `preview_change`.

- [ ] **Step 2: Run, verify fail.**

- [ ] **Step 3: Implement.** Add `preserve_history=False` param to `action_edit_strategy`; after the existing `Strategies.update`, if true: for each `base_id` in `linked_base_instance_ids`, `resolved = resolve_for_identity(conn, <new graph_nexus config>)`, `restamp_instance(conn, base_id, resolved)`; include a `restamp` summary in the return. Add `action_preview_strategy_config_change`. In `api/main.py`: add `preserve_history: bool = False` to `EditStrategyBody`, pass through; add `POST /strategies/{strategy_id}/config-change-preview` mirroring the `clear-state` dry-run wiring (`_run`, `conn_dependency`, `get_current_user`).

- [ ] **Step 4: Run tests** → PASS.
- [ ] **Step 5: Commit.** `git commit -am "feat(api): config-change preview endpoint + preserve_history on edit"`

---

### Task 5: Web popup (InstanceDetailView.vue)

**Files:**
- Modify: `frontend/src/views/InstanceDetailView.vue` (`submitEditStrategy` + new modal state)

- [ ] **Step 1: Add preview call + modal.** In `submitEditStrategy`, before the `PUT`, `POST ${API_BASE}/strategies/${d.id}/config-change-preview` with `{strategies: d.strategies}`. If `body.needs_prompt`, set modal state listing affected instances and STOP (await user choice) instead of PUTting. Add a `<div>` modal with three buttons → handlers that call a shared `doSaveStrategy(preserveHistory)` which performs the existing `PUT` with `preserve_history` added to the body. Non-prompt path calls `doSaveStrategy(false)` directly.

- [ ] **Step 2: Modal copy:** "Changing the analysis model for <names> would otherwise rebuild Nexus history. Preserve existing history and apply the new model going forward? Positions & P&L are unaffected; takes effect on next Stop→Start." Buttons: **Preserve & apply forward** / **Full rebuild** / **Cancel**.

- [ ] **Step 3: Build.** `cd frontend && npm run build` → green.
- [ ] **Step 4: Commit.** `git commit -am "feat(web): model-switch preserve-history popup"`

---

### Task 6: Mobile confirm dialog

**Files:**
- Modify: `mobile/lib/features/strategies/data/strategy_repository.dart` (add `preview` + `preserveHistory` param on `update`)
- Modify: the strategy config edit screen that calls `strategy_repository.update`

- [ ] **Step 1: Repository.** Add `Future<Map<String,dynamic>> previewConfigChange(String id, List strategies)` → `POST /strategies/$id/config-change-preview`; add optional `bool preserveHistory = false` to `update`, injected into the body.

- [ ] **Step 2: Screen.** Before saving, call `previewConfigChange`; if `needs_prompt`, `showDialog` AlertDialog with the three actions; pass `preserveHistory` to `update`.

- [ ] **Step 3: Analyze.** `cd mobile && flutter analyze lib/features/strategies` → clean (pre-existing info lints OK).
- [ ] **Step 4: Commit.** `git commit -am "feat(mobile): model-switch preserve-history confirm dialog"`

---

## Self-Review

- **Spec coverage:** shared identity module (T1) ✓; restamp base/scoped keying (T2) ✓; preview `needs_prompt` semantics (T2) ✓; parity (T3) ✓; preview endpoint + `preserve_history` (T4) ✓; web popup (T5) ✓; mobile dialog (T6) ✓.
- **Placeholders:** none — concrete files/commands; broker history-scope helper bodies are moved verbatim during T1 Step 3 (read-then-move, not a placeholder).
- **Type consistency:** `live_config_hash`/`history_scope_id`/`restamp_instance`/`preview_change` signatures used identically across tasks.
