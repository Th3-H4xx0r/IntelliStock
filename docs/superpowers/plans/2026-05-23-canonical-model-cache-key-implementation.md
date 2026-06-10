# Canonical Model Cache Key — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (inline) to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Nexus article-classification cache and the prompt-hash cache key on a provider-agnostic *canonical model identity* + *unified reasoning effort*, so the same underlying model under different provider/model-name strings shares cache, reasoning level always invalidates correctly, and existing Azure rows are migrated to the new scheme.

**Architecture:** One shared helper `canonical_model_cache_key(model, provider_config)` in `backend/llm_utils.py` (auto-normalize the model + `model_cache_family` override + unified effort), consumed by both cache-key sites. The article doc-id key drops `provider`; the prompt-hash key hashes the canonical string. A one-time migration re-keys existing article rows.

**Tech Stack:** Python, RethinkDB, pytest, Vue 3.

**Spec:** `docs/superpowers/specs/2026-05-23-canonical-model-cache-key-design.md`

**Conventions:** backend tests in `backend/tests/`, run from repo root with `--ignore=backend/tests/test_intellistock_logger.py --ignore=backend/tests/test_redact_logger.py`. `python3`. Commit footer `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>`; no backticks in commit bodies. Don't stage `AGENTS.md`/`CLAUDE.md`.

---

## Phase 1 — Canonical identity helpers (`backend/llm_utils.py`)

### Task 1: `_auto_normalize_model`

**Files:** Modify `backend/llm_utils.py` (add near `llm_model_reference`, ~line 600); Test `backend/tests/test_canonical_model_key.py`

- [ ] **Step 1: Write failing tests**
```python
# backend/tests/test_canonical_model_key.py
import llm_utils

def test_auto_normalize_strips_vendor_and_version():
    n = llm_utils._auto_normalize_model
    assert n("openai.gpt-oss-120b-1:0") == "gpt-oss-120b"
    assert n("gpt-oss-120b") == "gpt-oss-120b"               # azure deployment name
    assert n("openai.gpt-oss-120b-1:0") == n("gpt-oss-120b")  # the equivalence we want
    assert n("us.anthropic.claude-3-5-sonnet-20241022-v2:0") == "claude-3-5-sonnet-20241022"
    assert n("amazon.nova-pro-v1:0") == "nova-pro"

def test_auto_normalize_keeps_distinct_models_distinct():
    n = llm_utils._auto_normalize_model
    assert n("openai.gpt-oss-120b-1:0") != n("openai.gpt-oss-20b-1:0")
    assert n("anthropic.claude-3-5-sonnet-20241022-v2:0") != n("gemini-3-flash-preview")
```

- [ ] **Step 2: Run, verify fail** — `python3 -m pytest backend/tests/test_canonical_model_key.py -q --ignore=backend/tests/test_intellistock_logger.py --ignore=backend/tests/test_redact_logger.py` → AttributeError.

- [ ] **Step 3: Implement** (insert after `llm_model_reference`):
```python
_CANON_VENDOR_PREFIXES = ("openai.", "anthropic.", "meta.", "amazon.", "mistral.", "cohere.", "ai21.", "deepseek.", "qwen.")
_CANON_REGION_PREFIXES = ("us.", "eu.", "apac.")
_CANON_VERSION_SUFFIX_RE = re.compile(r"(?:-v?\d+)?:\d+$")


def _auto_normalize_model(model: str) -> str:
    """Normalize a provider/model string to a provider-agnostic token.

    Strips a leading cross-region inference-profile prefix (us./eu./apac.),
    a vendor prefix (openai./anthropic./…), and a trailing version/profile
    suffix (:0, -1:0, -v2:0); lowercases. So openai.gpt-oss-120b-1:0 and a bare
    azure 'gpt-oss-120b' both become 'gpt-oss-120b'.
    """
    s = str(model or "").strip().lower()
    for p in _CANON_REGION_PREFIXES:
        if s.startswith(p):
            s = s[len(p):]
            break
    for p in _CANON_VENDOR_PREFIXES:
        if s.startswith(p):
            s = s[len(p):]
            break
    s = _CANON_VERSION_SUFFIX_RE.sub("", s)
    return s.strip()
```
(`re` is already imported at the top of llm_utils.py.)

- [ ] **Step 4: Run, verify pass** → 2 passed.
- [ ] **Step 5: Commit** (bundle with Tasks 2-3).

### Task 2: `_unified_reasoning_effort`

**Files:** Modify `backend/llm_utils.py`; Test append to `test_canonical_model_key.py`

- [ ] **Step 1: Write failing tests**
```python
def test_unified_effort_across_providers():
    u = llm_utils._unified_reasoning_effort
    assert u({"reasoning_effort": "medium"}) == "medium"          # azure/openai/nvidia/cli
    assert u({"bedrock_reasoning": "medium"}) == "medium"          # bedrock
    assert u({"ollama_think": "medium"}) == "medium"               # ollama
    assert u({"bedrock_reasoning": "off"}) == ""
    assert u({"ollama_think": "true"}) == "on"
    assert u({}) == ""
```

- [ ] **Step 2: Run, verify fail.**

- [ ] **Step 3: Implement** (after `_auto_normalize_model`):
```python
def _unified_reasoning_effort(provider_config: dict | None) -> str:
    """Collapse the per-provider effort field to a common token.

    Reads reasoning_effort (azure/openai/nvidia/cli) OR bedrock_reasoning
    (bedrock) OR ollama_think (ollama). low/medium/high pass through;
    true/on -> 'on'; off/false/empty/unknown -> ''.
    """
    pc = provider_config or {}
    raw = (
        str(pc.get("reasoning_effort") or "").strip().lower()
        or str(pc.get("bedrock_reasoning") or "").strip().lower()
        or str(pc.get("ollama_think") or "").strip().lower()
    )
    if raw in ("low", "medium", "high"):
        return raw
    if raw in ("true", "on", "yes", "1"):
        return "on"
    return ""
```

- [ ] **Step 4: Run, verify pass.**

### Task 3: `canonical_model_cache_key`

**Files:** Modify `backend/llm_utils.py`; Test append.

- [ ] **Step 1: Write failing tests**
```python
def test_canonical_key_azure_bedrock_equal():
    k = llm_utils.canonical_model_cache_key
    azure = k("gpt-oss-120b", {"reasoning_effort": "medium"})
    bedrock = k("openai.gpt-oss-120b-1:0", {"bedrock_region": "us-east-1", "bedrock_reasoning": "medium"})
    assert azure == bedrock == "gpt-oss-120b@medium"

def test_canonical_key_effort_changes_key():
    k = llm_utils.canonical_model_cache_key
    assert k("openai.gpt-oss-120b-1:0", {"bedrock_reasoning": "medium"}) != \
           k("openai.gpt-oss-120b-1:0", {"bedrock_reasoning": "high"})
    assert k("openai.gpt-oss-120b-1:0", {"bedrock_reasoning": "off"}) == "gpt-oss-120b"

def test_canonical_key_family_override_wins():
    k = llm_utils.canonical_model_cache_key
    assert k("my-weird-azure-deployment", {"reasoning_effort": "medium", "model_cache_family": "gpt-oss-120b"}) \
        == "gpt-oss-120b@medium"
```

- [ ] **Step 2: Run, verify fail.**

- [ ] **Step 3: Implement** (after `_unified_reasoning_effort`):
```python
def canonical_model_cache_key(model: str, provider_config: dict | None = None) -> str:
    """Provider-agnostic cache identity '<base>@<effort>' (or '<base>').

    base = provider_config['model_cache_family'] (operator override) if set,
    else the auto-normalized model. effort = the unified reasoning effort.
    Two configs that mean the same model + effort get the same key regardless
    of provider or naming convention.
    """
    pc = provider_config or {}
    family = str(pc.get("model_cache_family") or "").strip().lower()
    base = family or _auto_normalize_model(model)
    effort = _unified_reasoning_effort(pc)
    return f"{base}@{effort}" if effort else base
```

- [ ] **Step 4: Run, verify pass** → all Phase-1 tests green.
- [ ] **Step 5: Commit**
```bash
git add backend/llm_utils.py backend/tests/test_canonical_model_key.py
git commit -m "$(cat <<'EOF'
feat(llm_utils): canonical_model_cache_key — provider-agnostic model identity + unified effort

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase 2 — Article doc cache

### Task 4: `_llm_cached_doc_id` uses the canonical key (drop provider)

**Files:** Modify `backend/strategies/graph_nexus_analysis.py` — the llm_utils import blocks (~80-104), `_llm_cached_doc_id` (3187) and its 2 callers (3719, 3844); Test `backend/tests/test_nexus_canonical_cache.py`

- [ ] **Step 1: Write failing test**
```python
# backend/tests/test_nexus_canonical_cache.py
import os, sys
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(_HERE, "..")))
from strategies import graph_nexus_analysis as gna  # noqa: E402

def test_doc_id_provider_agnostic_and_effort_aware():
    azure = gna._llm_cached_doc_id("company", "HASH", "gpt-oss-120b", "v3", {"reasoning_effort": "medium"})
    bedrock = gna._llm_cached_doc_id("company", "HASH", "openai.gpt-oss-120b-1:0", "v3",
                                     {"bedrock_region": "us-east-1", "bedrock_reasoning": "medium"})
    assert azure == bedrock == "company|HASH|gpt-oss-120b@medium|v3"
    high = gna._llm_cached_doc_id("company", "HASH", "openai.gpt-oss-120b-1:0", "v3", {"bedrock_reasoning": "high"})
    assert high != bedrock  # reasoning level now invalidates
```

- [ ] **Step 2: Run, verify fail** (old signature has `provider`; old key has provider+`-MEDIUM`-or-nothing).

- [ ] **Step 3: Implement.**
- 3a. Add `canonical_model_cache_key,` to BOTH `from llm_utils import (...)` blocks (after `llm_model_reference,` at ~88 and ~104).
- 3b. Replace `_llm_cached_doc_id`:
```python
def _llm_cached_doc_id(
    schema_type: str,
    article_hash: str,
    model: str,
    prompt_version: str,
    provider_config: dict[str, Any] | None = None,
) -> str:
    return f"{schema_type}|{article_hash}|{canonical_model_cache_key(model, provider_config)}|{prompt_version}"
```
- 3c. Update the 2 callers — drop the `provider` argument:
  - line ~3719: `_llm_cached_doc_id("company", row["article_hash"], model, prompt_version, provider_config)`
  - line ~3844: `_llm_cached_doc_id("macro", row["article_hash"], model, prompt_version, provider_config)`
  (Read each line first; remove the `provider,` positional that currently sits between `article_hash` and `model`.)

- [ ] **Step 4: Run, verify pass** → 1 passed.
- [ ] **Step 5: Commit**
```bash
git add backend/strategies/graph_nexus_analysis.py backend/tests/test_nexus_canonical_cache.py
git commit -m "$(cat <<'EOF'
feat(nexus): article doc cache keys on canonical model identity (drop provider)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase 3 — Prompt-hash cache

### Task 5: prompt-cache sites hash the canonical key

**Files:** Modify `backend/llm_utils.py` — every `_check_prompt_cache(` / `_store_prompt_cache(` call site; Test append to `test_canonical_model_key.py`

- [ ] **Step 1: Write failing test** (proves provider-agnostic prompt key via the real key fn):
```python
def test_prompt_cache_key_provider_agnostic():
    azure = llm_utils.canonical_model_cache_key("gpt-oss-120b", {"reasoning_effort": "medium"})
    bedrock = llm_utils.canonical_model_cache_key("openai.gpt-oss-120b-1:0", {"bedrock_reasoning": "medium"})
    assert llm_utils._prompt_cache_key("PROMPT", azure, "") == llm_utils._prompt_cache_key("PROMPT", bedrock, "")
```
(This passes once Phase 1 lands — it documents the invariant the call-site change relies on. Keep it.)

- [ ] **Step 2: Enumerate + edit call sites.** Run `grep -n "_check_prompt_cache(\|_store_prompt_cache(" backend/llm_utils.py`. At EACH call site (they all have `provider_config` and a local `cache_effort`/`_effort_key` in scope), change the `(model, <effort_var>)` arguments to `(canonical_model_cache_key(model, provider_config), "")`. Example (`call_llm_by_provider`):
```python
# before:
_effort_key = _cache_effort_key(provider, provider_config)
_cached = _check_prompt_cache(prompt, model, _effort_key)
# after:
_cached = _check_prompt_cache(prompt, canonical_model_cache_key(model, provider_config), "")
```
and the matching store:
```python
_store_prompt_cache(prompt, canonical_model_cache_key(model, provider_config), "", _result)
```
Apply the same transform to the structured-path sites (the `cache_effort = _cache_effort_key(...)` ones) — both the check and the store at each site, so they stay consistent.

- [ ] **Step 3: Verify no site missed** — re-grep; every remaining `_check/_store_prompt_cache(` call's 2nd arg is `canonical_model_cache_key(...)` and 3rd arg is `""`.

- [ ] **Step 4: Run** the bedrock + ollama + structured suites to confirm no regression:
`python3 -m pytest backend/tests/ -k "prompt_cache or canonical or ollama or bedrock or structured" --ignore=backend/tests/test_intellistock_logger.py --ignore=backend/tests/test_redact_logger.py -q`

- [ ] **Step 5: Commit**
```bash
git add backend/llm_utils.py backend/tests/test_canonical_model_key.py
git commit -m "$(cat <<'EOF'
feat(llm_utils): prompt-hash cache keys on canonical model identity

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase 4 — `model_cache_family` override field

> Mirror the just-shipped `bedrock_region` field wiring exactly. Read each ollama/bedrock analogue, add the parallel `model_cache_family` line.

### Task 6: Pydantic bodies + actions + API routes

**Files:** `backend/api/main.py`, `backend/interactive_utils.py`; Test append to `backend/tests/test_models_api_bedrock.py`

- [ ] **Step 1: Failing test**
```python
def test_create_model_body_accepts_cache_family():
    CreateModelBody = _import_body("CreateModelBody")
    b = CreateModelBody(name="x", provider="bedrock", model="openai.gpt-oss-120b-1:0",
                        api_key="k", model_cache_family="gpt-oss-120b")
    assert b.model_cache_family == "gpt-oss-120b"
```
- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement** — add `model_cache_family: Optional[str] = Field(default=None, max_length=64)` to `CreateModelBody` and `EditModelBody` (next to the bedrock fields). In `action_create_model` add param `model_cache_family=None` + doc field `"model_cache_family": (model_cache_family or "").strip().lower(),`. Add `"model_cache_family"` to the `action_edit_model` updateable-fields tuple. Forward `model_cache_family=body.model_cache_family` in the `api_create_model` `_run(action_create_model, ...)` call.
- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Commit** (bundle Tasks 6-8).

### Task 7: `model_resolver` field_map

**Files:** `backend/model_resolver.py:143-160`; Test `backend/tests/test_model_resolver_bedrock.py`

- [ ] **Step 1: Failing test** — extend `_BEDROCK_ROW` with `"model_cache_family": "gpt-oss-120b"`, assert `out["lookback_sentiment_model_cache_family"] == "gpt-oss-120b"` after resolve.
- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement** — add to `field_map`: `"model_cache_family": f"{prefix}model_cache_family",`.
- [ ] **Step 4: Run, verify pass.**

### Task 8: `_resolve_role_llm_provider_config` surfaces the field

**Files:** `backend/strategies/graph_nexus_analysis.py:793` (`_resolve_role_llm_provider_config`); Test `backend/tests/test_nexus_canonical_cache.py`

- [ ] **Step 1: Failing test**
```python
def test_role_provider_config_surfaces_cache_family():
    cfg = {"company_article_llm_provider": "bedrock",
           "company_article_bedrock_region": "us-east-1",
           "company_article_model_cache_family": "gpt-oss-120b"}
    out = gna._resolve_role_llm_provider_config(cfg, "company_article")
    assert out["model_cache_family"] == "gpt-oss-120b"
```
- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement** — wrap the existing function so it adds `model_cache_family` once (avoids editing each early-return branch). Rename the current `def _resolve_role_llm_provider_config(config, role)` to `def _resolve_role_llm_provider_config_fields(config, role)`, then add:
```python
def _resolve_role_llm_provider_config(config: dict, role: str) -> dict[str, Any]:
    out = dict(_resolve_role_llm_provider_config_fields(config, role))
    role_l = str(role or "").strip().lower()
    prefix = f"{role_l}_" if role_l else ""
    lookback = bool(config.get("historical_lookback_mode", False))
    fam = ""
    if lookback:
        fam = (config.get(f"lookback_{prefix}model_cache_family") or "").strip()
        if not fam and prefix:
            fam = (config.get("lookback_model_cache_family") or "").strip()
    if not fam:
        fam = (config.get(f"{prefix}model_cache_family") or config.get("model_cache_family") or "").strip()
    if fam:
        out["model_cache_family"] = fam.lower()
    return out
```
- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Commit**
```bash
git add backend/api/main.py backend/interactive_utils.py backend/model_resolver.py backend/strategies/graph_nexus_analysis.py backend/tests/
git commit -m "$(cat <<'EOF'
feat(models): model_cache_family override field (backend + resolver)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Task 9: Frontend field

**Files:** `frontend/src/views/ModelsView.vue`, `frontend/src/components/LlmConfigForm.vue`

- [ ] **Step 1:** In `ModelsView.vue` `formDraft` add `modelCacheFamily: ''`; hydrate `modelCacheFamily: m.model_cache_family || ''`; in `submitModel` always send `payload.model_cache_family = (d.modelCacheFamily || '').trim().toLowerCase() || undefined`.
- [ ] **Step 2:** In `LlmConfigForm.vue` add a text input (shown for all providers, after the model field) bound to `draft.modelCacheFamily` via `update('modelCacheFamily', …)`, with helper text: "Optional cache group. Set the same value on rows that are the same underlying model across providers so they share LLM cache."
- [ ] **Step 3:** Build: `cd frontend && npm run build` → clean.
- [ ] **Step 4: Commit** `git add frontend/src && git commit -m "feat(frontend): model_cache_family field"` (with footer).

---

## Phase 5 — Migration

### Task 10: `scripts/migrate_llm_cache_to_canonical.py`

**Files:** Create `scripts/migrate_llm_cache_to_canonical.py`; Test `backend/tests/test_migrate_llm_cache.py`

- [ ] **Step 1: Failing test**
```python
# backend/tests/test_migrate_llm_cache.py
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
import migrate_llm_cache_to_canonical as mig

def test_rekey_azure_with_effort():
    assert mig._canonical_from_old_id("company|H1|azure|gpt-oss-120b-MEDIUM|v3") == "company|H1|gpt-oss-120b@medium|v3"

def test_rekey_bedrock_no_effort_suffix():
    assert mig._canonical_from_old_id("company|H1|bedrock|openai.gpt-oss-120b-1:0|v3") == "company|H1|gpt-oss-120b|v3"

def test_already_canonical_skipped():
    assert mig._canonical_from_old_id("company|H1|gpt-oss-120b@medium|v3") is None  # 4 parts -> not old scheme
```

- [ ] **Step 2: Run, verify fail** — module missing.

- [ ] **Step 3: Implement** the script:
```python
#!/usr/bin/env python3
"""Re-key Nexus article-LLM cache rows to the canonical model identity scheme.

Old id: {schema}|{article_hash}|{provider}|{model_ref}|{prompt_version}
New id: {schema}|{article_hash}|{canonical}|{prompt_version}

Run dry first:  python3 scripts/migrate_llm_cache_to_canonical.py --host <your-rethinkdb-host>
Then apply:     python3 scripts/migrate_llm_cache_to_canonical.py --host <your-rethinkdb-host> --apply
"""
from __future__ import annotations
import argparse, os, sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
from llm_utils import _auto_normalize_model  # noqa: E402

TABLES = ("GraphNexusNewsLLMCompany", "GraphNexusNewsLLMMacro")
_EFFORT_SUFFIXES = ("-low", "-medium", "-high", "-on")


def _canonical_from_old_id(old_id: str):
    """Map an OLD 5-part id to its canonical id, or None if not the old scheme."""
    if not isinstance(old_id, str):
        return None
    parts = old_id.split("|")
    if len(parts) != 5:
        return None  # already canonical (4 parts) or unexpected — skip
    schema, article_hash, _provider, model_ref, prompt_version = parts
    m, effort = model_ref, ""
    low = model_ref.lower()
    for suf in _EFFORT_SUFFIXES:
        if low.endswith(suf):
            effort = suf[1:]
            m = model_ref[: -len(suf)]
            break
    base = _auto_normalize_model(m)
    canonical = f"{base}@{effort}" if effort else base
    return f"{schema}|{article_hash}|{canonical}|{prompt_version}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=os.environ.get("RETHINKDB_HOST", "localhost"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("RETHINKDB_PORT", "28015")))
    ap.add_argument("--db", default="IntelliStock")
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry-run)")
    ap.add_argument("--cleanup", action="store_true", help="delete old ids after re-keying")
    args = ap.parse_args()

    from rethinkdb import RethinkDB
    r = RethinkDB()
    conn = r.connect(host=args.host, port=args.port, db=args.db, timeout=10)
    existing = set(r.db(args.db).table_list().run(conn))
    for table in TABLES:
        if table not in existing:
            print(f"{table}: not present, skipping")
            continue
        rekeyed = skipped = 0
        for row in r.db(args.db).table(table).run(conn):
            old_id = row.get("id")
            new_id = _canonical_from_old_id(old_id)
            if not new_id or new_id == old_id:
                skipped += 1
                continue
            print(f"  {old_id}  ->  {new_id}")
            if args.apply:
                new_row = dict(row); new_row["id"] = new_id
                r.db(args.db).table(table).insert(new_row, conflict="replace").run(conn)
                if args.cleanup:
                    r.db(args.db).table(table).get(old_id).delete().run(conn)
            rekeyed += 1
        print(f"{table}: {rekeyed} re-keyed, {skipped} unchanged ({'APPLIED' if args.apply else 'DRY-RUN'})")
    conn.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run, verify pass** → 3 passed.
- [ ] **Step 5: Commit**
```bash
git add scripts/migrate_llm_cache_to_canonical.py backend/tests/test_migrate_llm_cache.py
git commit -m "$(cat <<'EOF'
feat(scripts): migrate Nexus article-LLM cache rows to canonical model ids

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase 6 — Verification

### Task 11: Full verification + migration dry-run

- [ ] **Step 1:** `python3 -m pytest backend/tests/ -k "canonical or nexus_canonical or migrate_llm or bedrock or ollama or resolver or models_api" --ignore=backend/tests/test_intellistock_logger.py --ignore=backend/tests/test_redact_logger.py -q` → all green.
- [ ] **Step 2:** `cd frontend && npm run build` → clean.
- [ ] **Step 3:** Migration **dry-run** against prod: `python3 scripts/migrate_llm_cache_to_canonical.py --host <your-rethinkdb-host>` → review the planned re-keys (confirm the Azure `gpt-oss-120b-MEDIUM` rows map to `gpt-oss-120b@medium`). **STOP and confirm with the operator before running with `--apply`** (prod-data write).

---

## Self-Review

- **Spec coverage:** §4.1 helper → Tasks 1-3; §4.2 article cache → Task 4; §4.3 prompt cache → Task 5; §4.4 override field → Tasks 6-9; §4.5 migration → Task 10; §4.6 reasoning-in-key → covered by unified effort in Task 3 (tested in Task 4); §5 testing → tests in each task + Task 11. Covered.
- **Placeholder scan:** none — novel code (helpers, migration) reproduced in full; wiring tasks give exact field names + the bedrock_region analogue to mirror (concrete, existing code).
- **Type consistency:** `canonical_model_cache_key(model, provider_config)` / `_auto_normalize_model(model)` / `_unified_reasoning_effort(provider_config)` signatures consistent across Tasks 1-5, 10; `_llm_cached_doc_id(schema, article_hash, model, prompt_version, provider_config)` (provider dropped) consistent between Task 4 def and callers; `model_cache_family` field name consistent across Tasks 6-9; migration `_canonical_from_old_id` reuses `_auto_normalize_model` so migrated ids match runtime keys.
