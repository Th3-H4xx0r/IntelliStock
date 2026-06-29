# OpenRouter Model-Provider Integration — Design

**Date:** 2026-06-29
**Status:** Approved design, pending implementation plan
**Scope decisions (confirmed with operator):** full parity (backend + web + mobile); live searchable model dropdown; configurable app-attribution headers; reasoning-effort low/medium/high.

## 1. Goal

Add `openrouter` as a first-class LLM provider, selectable anywhere the existing providers (`gemini`, `openai`, `azure`, `nvidia`, `deepseek`, `ollama`, `bedrock`, `claude-cli`, `codex-cli`) are. An operator should be able to create a Models-table row with `provider = openrouter`, pick a model from a live dropdown, optionally set attribution headers and reasoning effort, save it, and have every strategy/instance that references that model route LLM calls through OpenRouter — on web and mobile, plain-text and structured-output paths alike.

## 2. Verified facts from OpenRouter official docs

| Property | Value |
|---|---|
| Base URL | `https://openrouter.ai/api/v1` |
| Chat endpoint | `POST /chat/completions` (OpenAI Chat Completions compatible — drop-in) |
| Auth | `Authorization: Bearer <OPENROUTER_API_KEY>` |
| Model id format | `vendor/model`, e.g. `anthropic/claude-3.5-sonnet` |
| List models | `GET /api/v1/models` (public, no auth) → `{ data: [{ id, name, context_length, pricing, ... }] }` |
| Reasoning | request body `reasoning: { effort: "low" \| "medium" \| "high" }`; the OpenAI-style top-level `reasoning_effort` is accepted as an alias |
| Attribution headers (optional) | `HTTP-Referer: <site url>` and `X-Title: <app name>`. Only affect leaderboard ranking; calls work without them. |

**Architectural consequence:** OpenRouter is structurally identical to the existing `nvidia` provider — an OpenAI-compatible endpoint reached through pydantic_ai's `OpenAIChatModel` + `OpenAIProvider(base_url=…, api_key=…)` (structured path) and a raw-HTTP `requests.post(.../chat/completions)` clone of `_call_nvidia` (plain path). The only two things beyond `nvidia` are (a) optional attribution headers and (b) the model-id is a slash-namespaced string. This keeps the integration almost entirely mechanical pattern-replication of the recent `nvidia`/`bedrock`/`ollama` additions.

## 3. Configuration model

A new Models-table row with `provider = "openrouter"` carries these fields (mirroring `nvidia_base_url` / `bedrock_region` conventions):

| Models-row field | Strategy-config key (after resolution) | Default / env fallback | Notes |
|---|---|---|---|
| `model` | `llm_model` / `model_name` | — | `vendor/model` id |
| `api_key` | `llm_api_key` | env `OPENROUTER_API_KEY` | Bearer token |
| `openrouter_base_url` | `openrouter_base_url` | `https://openrouter.ai/api/v1` (env `OPENROUTER_BASE_URL`) | rarely changed |
| `openrouter_referer` | `openrouter_referer` | env `OPENROUTER_HTTP_REFERER` (default empty) | sent as `HTTP-Referer` |
| `openrouter_title` | `openrouter_title` | env `OPENROUTER_X_TITLE` (default empty) | sent as `X-Title` |
| `reasoning_effort` | `llm_reasoning_effort` | empty | reuses the existing low/medium/high knob |

`reasoning_effort` deliberately reuses the **existing** field (not an OpenRouter-specific one) because OpenRouter accepts the standard `reasoning_effort` alias. This means it flows automatically through `_unified_reasoning_effort`, `_cache_effort_key`, and `canonical_model_cache_key` with **zero** changes to the cache-identity layer.

## 4. Components & touch points

Grouped by layer. Each item names the existing analog being mirrored.

### 4.1 Backend — dispatch & call paths (`backend/llm_utils.py`)
1. **`resolve_api_key_for_provider`** — add `if p == "openrouter": return OPENROUTER_API_KEY`. (mirror `nvidia`)
2. **`_resolve_provider_config`** — add `openrouter` branch resolving `openrouter_base_url` (default + env), `openrouter_referer`, `openrouter_title`, and normalized `reasoning_effort`. (mirror `nvidia` + `ollama` extra fields)
3. **`_safe_provider_meta`** — add `openrouter` branch returning `{ base_url, reasoning_effort }` (no secrets/headers). (mirror `nvidia`)
4. **`_build_pydantic_ai_model`** — add `openrouter` branch building `OpenAIChatModel(model, provider=OpenAIProvider(base_url, api_key, openai_client=AsyncOpenAI(base_url, api_key, default_headers={HTTP-Referer, X-Title})))`. Attribution headers are injected via the client's `default_headers`; when both are empty, fall back to the plain `OpenAIProvider(base_url, api_key)` form. (mirror `nvidia`, extended for headers)
5. **`_call_openrouter`** (new) — clone of `_call_nvidia`: OpenAI-compatible `POST /chat/completions`, Bearer auth, retry/backoff, token-usage logging tagged `"openrouter"`, attribution headers added to the header dict, reasoning passed as top-level `reasoning_effort` (with `extra_body={"reasoning": {"effort": …}}` as the native equivalent). No special model rate-limiter (the kimi RPM caps are NVIDIA-only).
6. **`call_llm_by_provider`** — add `elif p == "openrouter": _call_openrouter(...)` branch. (mirror `nvidia` branch at ~line 5524)
7. **`_STRUCTURED_LLM_PROVIDER_LOCKS`** — add `"openrouter": threading.Lock()`.
8. **`_is_terminal_provider_not_found` / `_terminal_provider_not_found_hint`** — add `openrouter` to the OpenAI-compatible 404 group and add a cross-provider hint (a bare model id with no `/` is probably the wrong provider).
9. **Structured path** (`call_structured_llm_by_provider`, `_structured_model_name`, `_structured_model_candidates`) — provider-agnostic via `_build_pydantic_ai_model`; add `openrouter` only where the code explicitly enumerates the OpenAI-compatible set (e.g. the `{azure, openai, nvidia}` membership checks) so prompted-JSON handling and base-url logic apply.

### 4.2 Backend — model discovery (new, mirrors `bedrock_client` / `ollama_client`)
10. **`backend/openrouter_client.py`** (new) — `list_models(base_url) -> list[dict]` doing `GET {base_url}/models`, normalizing to `{ id, name, context_length, modalities }`. Public endpoint, so no key required; never raises (returns `[]` + error on failure).
11. **`backend/api/main.py`** — `POST /openrouter/list-models` endpoint (mirror `POST /ollama/list-models` at ~line 2213) returning `{ models, error }`.

### 4.3 Backend — Models CRUD & resolution
12. **`model_resolver.py` `field_map`** — add `openrouter_base_url`, `openrouter_referer`, `openrouter_title` mappings (mirror `nvidia_base_url`).
13. **`interactive_utils.py` `action_create_model` / `action_edit_model`** — add the three `openrouter_*` params to the signature + persisted-field list.
14. **`interactive_utils.py` `_validate_provider_model_compat`** — accept `openrouter` with a `vendor/model` slash id; warn if no slash.
15. **`backend/api/main.py` Pydantic bodies** — `CreateModelBody`, `UpdateModelBody`, `LlmConfigTestBody` gain `openrouterBaseUrl`, `openrouterReferer`, `openrouterTitle`; `_build_llm_test_provider_config` gains an `openrouter` branch.

### 4.4 Frontend (web)
16. **`frontend/src/utils/strategyConfig.js`** — add `{ value: 'openrouter', label: 'OpenRouter' }` to `LLM_PROVIDER_OPTIONS`; reuse the generic low/medium/high reasoning options for it; include the `openrouter_*` fields in `buildStrategyLlmTestPayload`.
17. **`frontend/src/composables/useOpenRouterModels.js`** (new) — cached loader hitting `POST /openrouter/list-models` (mirror `useBedrockModels.js`), with manual-entry fallback on error.
18. **`frontend/src/components/LlmConfigForm.vue`** — add an `openrouter` config block: base-url input (prefilled default), HTTP-Referer + X-Title inputs, and a searchable model dropdown backed by `useOpenRouterModels` with a Refresh button and free-text fallback; wire the reasoning-effort dropdown for `openrouter`.
19. **`frontend/src/views/ModelsView.vue`** — add `openrouterBaseUrl` (default `https://openrouter.ai/api/v1`), `openrouterReferer`, `openrouterTitle` to the form-draft initial state and save/load mapping.

### 4.5 Mobile (Flutter)
20. **`mobile/lib/features/strategies/strategy_config.dart`** — add the `openrouter` provider option (+ reuse generic reasoning-effort options).
21. **`mobile/lib/features/models/data/model_repository.dart`** — add `openrouterBaseUrl`, `openrouterReferer`, `openrouterTitle` to `LlmModel` + JSON (de)serialization.
22. **`mobile/lib/features/models/presentation/llm_config_form.dart`** — add the three fields to `LlmConfigDraft` + the form UI (base-url, referer, title, model field; dropdown if the mobile app exposes a discovery call, else free-text to match current mobile patterns).
23. **`mobile/lib/features/models/presentation/models_screen.dart`** — add the `openrouter` provider label + form-draft initialization.

### 4.6 Tests
24. **Backend** (mirror `test_model_resolver_*`, `test_models_api_*`, `test_llm_utils_*`): api-key resolution, config resolution (defaults + headers + reasoning), `_build_pydantic_ai_model` returns an OpenAI-compatible model with base_url + default_headers, `model_resolver` field-map injection, `openrouter_client.list_models` parsing/error handling, dispatch routes to `_call_openrouter`.
25. **Mobile** (mirror `llm_config_draft_test.dart`): draft round-trips the three new fields.

### 4.7 Docs
26. **`docs/claude-code-provider-setup.md`** (or a sibling) — OpenRouter setup: key signup, base URL, optional attribution headers, model-id format, reasoning note.
27. **`backend/llm_pricing.yaml`** — optional: operators may add per-model entries with `provider: openrouter` for cost tracking; not required for function.

## 5. Data flow (runtime)

```
Strategy config { llm_model_id: <row id> }
  → model_resolver.resolve_model_refs_in_config
      injects provider=openrouter, llm_model, llm_api_key,
              openrouter_base_url, openrouter_referer, openrouter_title,
              llm_reasoning_effort
  → call_llm_by_provider(provider="openrouter", ...)            [plain path]
      → _call_openrouter → POST {base_url}/chat/completions
         headers: Authorization, HTTP-Referer?, X-Title?
         body: model, messages, reasoning_effort?
  OR call_structured_llm_by_provider(...)                       [structured path]
      → _build_pydantic_ai_model → OpenAIChatModel(OpenAIProvider(base_url, key, default_headers))
```

## 6. Error handling

- **Missing API key:** `resolve_api_key_for_provider` returns empty → existing "no api key" short-circuit logs and skips (same as openai/nvidia today).
- **Wrong-provider model id** (no `/`): surfaced via `_validate_provider_model_compat` warning at save time and `_terminal_provider_not_found_hint` at call time.
- **`GET /models` failure:** `openrouter_client.list_models` returns `[]` + error; the form shows the error and falls back to free-text model entry (exactly the Bedrock behavior).
- **429 / 5xx:** reuse `_call_nvidia`'s retry/backoff and critical-guard HTTP stashing. No OpenRouter-specific rate limiter is added (OpenRouter does its own upstream balancing; the kimi RPM caps stay NVIDIA-scoped).
- **Content-filter / abuse patterns:** the existing `_is_non_retryable_filter_response` guard applies unchanged in the cloned call path.

## 7. Cache identity (no changes needed)

Because OpenRouter reuses the standard `reasoning_effort` field, `canonical_model_cache_key` and `_cache_effort_key` already handle it. One nuance: `_auto_normalize_model` strips **dot**-namespaced vendor prefixes (`anthropic.`), not the **slash** form (`anthropic/`), so `anthropic/claude-3.5-sonnet` keeps its full id as the cache base. This is internally consistent (OpenRouter rows cache against each other). Operators who want an OpenRouter model to *share* cache with the same model on another provider can set `model_cache_family` — the existing override. No code change; documented as a note.

## 7a. Pricing auto-fill (operator-requested)

When the operator picks a model from the live dropdown, the form auto-fills the Models-row per-model cost overrides from OpenRouter's catalog pricing. **Feasibility verified — reliable for all models, no new backend schema.**

**Why no schema change:** the Models row already has `input_cost_per_1m`, `output_cost_per_1m`, `cache_creation_cost_per_1m`, `cache_read_cost_per_1m` fields, fully wired through `ModelsView.vue` (`inputCostPer1m` etc.), the `CreateModelBody`/`UpdateModelBody` API bodies, CRUD, and — critically — `llm_telemetry.compute_cost`, where a Models-row override takes precedence over `llm_pricing.yaml`. Auto-fill simply prefills these existing fields.

**Data source:** `GET /api/v1/models` already returns a `pricing` object per model (the same call that backs the dropdown — no extra request). Pricing values are **strings in USD per token**. Conversion to IntelliStock's USD-per-1M units is `value × 1_000_000`:

| OpenRouter `pricing.*` (USD/token, string) | Models-row field (USD/1M) |
|---|---|
| `prompt` | `input_cost_per_1m = prompt × 1e6` |
| `completion` | `output_cost_per_1m = completion × 1e6` |
| `input_cache_read` | `cache_read_cost_per_1m = input_cache_read × 1e6` |
| `input_cache_write` | `cache_creation_cost_per_1m = input_cache_write × 1e6` |

Example — Claude Opus 4.8 via OpenRouter (`prompt: "0.000005"`, `completion: "0.000025"`) → `input_cost_per_1m = 5.00`, `output_cost_per_1m = 25.00`.

**Reliability across all models:**
- `prompt` and `completion` are present for **every** model in the catalog (they are the billing basis) → input/output per-1M auto-fill is reliable for all models. Free models report `"0"` → 0.00.
- `input_cache_read` / `input_cache_write` are present only for caching-capable models → mapped when present, left null otherwise (telemetry then falls through to YAML/zero for those components — correct).
- **Not representable** in IntelliStock's per-1M-token cost model and therefore NOT auto-filled (documented limitation): `request` (per-request flat fee), `image`, `audio`, `web_search`, `internal_reasoning`. Token costs remain correct; these surcharges go untracked, same as every other provider today.

**Implementation:**
- `useOpenRouterModels` (§4.4 #17) must preserve the full `pricing` object (and `context_length`) per model in its normalized rows, not just `id`/`name`.
- `LlmConfigForm.vue` (§4.4 #18): on model-select, parse the four pricing strings (guarding non-numeric/empty), multiply by 1e6, and write into the existing `inputCostPer1m` / `outputCostPer1m` / `cacheCreationCostPer1m` / `cacheReadCostPer1m` draft fields. Values are **prefilled, not locked** — the operator can edit or clear any of them before saving. Re-selecting a model re-fills.
- A small "auto-filled from OpenRouter" hint + a button to clear the cost fields, so an operator who wants to fall through to YAML can opt out.
- Mobile parity (§4.5): same prefill if the mobile form gains the dropdown; if mobile keeps free-text model entry, pricing auto-fill is deferred there (mobile already exposes the cost-override fields for manual entry).
- Tests: a unit test for the string→per-1M conversion (incl. missing-cache-field and non-numeric guards).

## 8. Out of scope (YAGNI)

- No per-model OpenRouter rate limiter (add later only if 429s appear in practice).
- No OpenRouter "provider routing" / fallback-model preferences in the request body — single model id only for v1.
- No automatic price import into the global `llm_pricing.yaml` — pricing auto-fill targets the **per-row** cost-override fields only (see §7a). The YAML stays hand-maintained.
- No per-request/image/web-search surcharge tracking (not representable in the per-1M-token cost model; see §7a).
- No OAuth PKCE flow — API-key auth only.

## 9. Testing & verification strategy

- Unit tests per §4.6 run in `backend/tests` (python3) and `mobile/test` (flutter test).
- Manual: create an `openrouter` Models row with a real key, use the `/llm/test` endpoint (web form "Test" button) to confirm a live round-trip on both a non-reasoning and a reasoning model; confirm the live dropdown populates; confirm a referenced strategy resolves and calls through.
- `gitnexus_impact` on `call_llm_by_provider`, `_build_pydantic_ai_model`, `resolve_api_key_for_provider`, and `resolve_model_refs_in_config` before editing each (per CLAUDE.md), and `gitnexus_detect_changes` before committing.
