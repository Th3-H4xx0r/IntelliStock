# Provider-Agnostic Canonical Model Identity for LLM Caches — Design

- Status: approved (brainstorming complete, 2026-05-23)
- Related: `2026-05-23-bedrock-provider-design.md` (the switch azure→bedrock that exposed this)

## 1. Problem

Two Nexus LLM caches key on the **raw provider + model string + provider-specific
effort format**, so switching a role between providers for the *same underlying model*
needlessly invalidates the cache, and (for Bedrock) the reasoning level is missing from the
key entirely.

Concrete trigger: a role moved from Azure `gpt-oss-120b` (Medium) to Bedrock
`openai.gpt-oss-120b-1:0` (Medium) — the same model + effort — produced different cache
keys, forcing full re-classification.

Two existing keys:
- **Article doc cache** (`_llm_cached_doc_id`, tables `GraphNexusNewsLLMCompany` /
  `GraphNexusNewsLLMMacro`): `{schema}|{article_hash}|{provider}|{model_ref}|{prompt_version}`
  where `model_ref = llm_model_reference(model, provider_config["reasoning_effort"])`.
  - `provider` differs (azure vs bedrock).
  - `model_ref` differs (gpt-oss-120b vs openai.gpt-oss-120b-1:0).
  - effort suffix is present for Azure (`-MEDIUM` via `reasoning_effort`) but **absent** for
    Bedrock (effort is in `bedrock_reasoning`, which `_llm_model_ref` never reads) — so the
    Bedrock article key is the same regardless of reasoning level (a correctness bug).
- **Prompt-hash cache** (`_check_prompt_cache` / `_store_prompt_cache`, persistent RethinkDB):
  `sha256(prompt, model, effort_key)` where `effort_key = _cache_effort_key(provider, provider_config)`
  (`"medium"` for azure, `"reason:medium"` for bedrock, `"think:medium"` for ollama). Keys on
  raw model + provider-specific effort string → also misses on a provider switch.

## 2. Goals

1. **Reasoning level is always part of the key**, unified across providers (azure
   `reasoning_effort` ≡ bedrock `bedrock_reasoning` ≡ ollama `ollama_think`). Changing the
   effort correctly invalidates.
2. **The same underlying model under different provider/model-name strings shares cache**
   (azure `gpt-oss-120b` ≡ bedrock `openai.gpt-oss-120b-1:0`).
3. **Existing Azure article-cache rows are migrated** to the new scheme so the Bedrock
   config reuses them immediately (no re-classification).
4. Genuinely different models stay distinct (`gpt-oss-120b` ≠ `gpt-oss-20b`, claude ≠ gemini).

## 3. Approach

A single shared helper computes a **canonical model cache key** from `(model,
provider_config)`; both cache-key sites consume it. (Rejected: resolver-layer injection —
more provider_config-builder sites to touch and miss; inline-only — logic duplicated across
the two caches with drift risk.)

Equivalence is decided by **auto-normalization with an optional per-model override**
(`model_cache_family`): auto-normalize handles the common case with zero config; the
override force-groups edge cases (e.g. arbitrarily-named Azure deployments). `provider` is
**dropped** from the article key — the canonical identity is provider-independent.

## 4. Components

### 4.1 Canonical identity helper — new in `backend/llm_utils.py`

```python
def canonical_model_cache_key(model: str, provider_config: dict | None = None) -> str:
    """Provider-agnostic cache identity: '<base>@<effort>' (or '<base>' if no effort).

    base   = provider_config['model_cache_family'] (override) if set, else
             _auto_normalize_model(model).
    effort = _unified_reasoning_effort(provider_config).
    """
```

- `_auto_normalize_model(model)`: lowercase; strip a leading vendor prefix
  (`openai.` / `anthropic.` / `meta.` / `amazon.` / `mistral.` / `cohere.` / `ai21.`) and a
  cross-region inference-profile prefix (`us.` / `eu.` / `apac.`); strip a trailing
  version/profile suffix (`:0`, `-1:0`, `-v\d+:\d+`); trim whitespace. Examples:
  `openai.gpt-oss-120b-1:0` → `gpt-oss-120b`; `us.anthropic.claude-3-5-sonnet-20241022-v2:0`
  → `claude-3-5-sonnet-20241022`; azure `gpt-oss-120b` → `gpt-oss-120b`.
- `_unified_reasoning_effort(provider_config)`: read the first set of
  `reasoning_effort` (azure/openai/nvidia/cli), `bedrock_reasoning` (bedrock),
  `ollama_think` (ollama); normalize to `low` / `medium` / `high`; map `off` / `false` /
  empty → `""`; map `true` / `on` → `on`. → azure `medium` and bedrock `medium` both → `medium`.

### 4.2 Article doc cache (`_llm_cached_doc_id`)

Drop the `provider` segment; replace `_llm_model_ref(...)` with the canonical key:

```
{schema_type}|{article_hash}|{canonical_model_cache_key(model, provider_config)}|{prompt_version}
```

e.g. `company|<hash>|gpt-oss-120b@medium|<ver>` — identical for the Azure and Bedrock
configs. Update the two callers (`_llm_cached_doc_id("company", ...)`,
`_llm_cached_doc_id("macro", ...)`) to stop passing `provider`.

### 4.3 Prompt-hash cache

At each `_check_prompt_cache` / `_store_prompt_cache` call site (which has `provider_config`
in scope), pass `canonical_model_cache_key(model, provider_config)` as the model component
and an empty effort (effort is folded into the canonical key), so the sha256 hashes the
canonical identity instead of raw model + provider-specific effort string. Forward-only —
existing opaque-hash ids cannot be re-keyed.

### 4.4 Override field `model_cache_family`

New optional field, threaded exactly like the bedrock fields:
- `CreateModelBody` / `EditModelBody` / (`LlmConfigTestBody` not needed) — `Optional[str]`, max_length 64.
- `action_create_model` / `action_edit_model` — persist `(model_cache_family or "").strip().lower()`.
- API create/edit routes — forward the field.
- `model_resolver.py` field_map — `"model_cache_family": f"{prefix}model_cache_family"`.
- `graph_nexus_analysis._resolve_role_llm_provider_config` — surface it into `provider_config`;
  given the function's several early-returns, a thin wrapper that adds the field once to the
  result is cleaner than editing each branch.
- UI: a text input in `LlmConfigForm.vue` (all providers) + `ModelsView.vue` formDraft /
  hydration / submit. Help text: "Optional. Models sharing this tag share LLM cache —
  set the same value on rows that are the same underlying model across providers."

**Auto-normalize already covers the immediate azure↔bedrock case**; the override is for
edge cases. Included per the approved scope.

### 4.5 Migration — new `scripts/migrate_llm_cache_to_canonical.py`

For `GraphNexusNewsLLMCompany` + `GraphNexusNewsLLMMacro`:
1. Read all rows.
2. Parse the old id (`split('|')` → schema, article_hash, provider, model_ref, prompt_version).
3. From `model_ref`, split a trailing known-effort suffix (`-LOW`/`-MEDIUM`/`-HIGH`/`-ON`,
   case-insensitive) → `(raw_model, effort)`.
4. Compute the new id `{schema}|{article_hash}|{_auto_normalize_model(raw_model)}@{effort}|{prompt_version}`
   using the **same** normalization helper as runtime (imported from `llm_utils`).
5. Re-insert the row under the new id via `conflict="replace"`.
6. Idempotent (re-keying canonical → canonical is stable). Old ids left as harmless orphans;
   `--cleanup` deletes them. `--dry-run` prints the planned re-keys without writing.

Run against `<your-rethinkdb-host>` (like the index migration), with `--dry-run` first.

### 4.6 Reasoning-in-key bug

Solved for free: `_unified_reasoning_effort` puts the effort in the canonical key for every
provider, so changing `bedrock_reasoning` (or any provider's effort) now invalidates correctly.

## 5. Testing

Unit (`backend/tests/`):
- `_auto_normalize_model`: `openai.gpt-oss-120b-1:0` → `gpt-oss-120b`; azure `gpt-oss-120b`
  → `gpt-oss-120b` (equal); `us.anthropic.claude-3-5-sonnet-20241022-v2:0` →
  `claude-3-5-sonnet-20241022`; distinct models stay distinct (120b ≠ 20b, claude ≠ gemini).
- `_unified_reasoning_effort`: reasoning_effort/bedrock_reasoning/ollama_think all → same
  token for the same level; off/false/empty → "".
- `canonical_model_cache_key`: azure(gpt-oss-120b, medium) == bedrock(openai.gpt-oss-120b-1:0,
  medium); changing effort changes the key; `model_cache_family` override wins.
- `_llm_cached_doc_id`: provider-independent; effort reflected.
- Migration: sample old ids re-key to the expected canonical ids; idempotent on re-run.

## 6. Risks / non-goals

- **Risk:** dropping `provider` means any two configs whose models normalize to the same
  token share cache. Intended for same-model; force-separate with distinct
  `model_cache_family` values if ever needed.
- **Risk:** auto-normalization could over-merge (two distinct models → same token) or
  under-merge (odd Azure deployment name). The `model_cache_family` override is the escape
  hatch for both.
- **Non-goal:** migrating the prompt-hash cache (opaque hash ids; forward-only).
- **Non-goal:** changing what the LLM actually returns; this only governs cache identity.
  Operators accept that "same model, different host" may differ slightly in exchange for reuse.
