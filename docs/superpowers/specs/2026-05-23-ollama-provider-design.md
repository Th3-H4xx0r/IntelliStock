# Ollama as an IntelliStock LLM Provider — Design

**Status:** Draft v1 (planning only, not yet approved for implementation)
**Date:** 2026-05-23
**Sibling specs:** `2026-05-12-claude-code-cli-provider-design.md`, `2026-05-20-codex-cli-provider-design.md` (the CLI providers established the per-provider-module pattern; this design instead follows the inline pattern of `_call_openai`, `_call_gemini`, etc.).

---

## 1. Goal

Add a new `provider = "ollama"` so operators can target a local, self-hosted-remote, or Ollama Cloud instance from any place in IntelliStock that picks an LLM. The provider must reach feature parity with the existing first-class providers (gemini / openai / azure / nvidia / deepseek):

| Capability | In scope |
|---|---|
| Plain-text chat | yes |
| Structured output via PydanticAI | yes |
| Tool / function calling | yes |
| Critical-guard + rate-limit integration | yes |
| Embeddings | no (no existing provider exposes this path) |
| In-app `ollama pull` | no (out-of-band; covered in §13 future work) |

Operator flow at the end:

1. Operator installs Ollama on their machine (or has a remote host, or uses Ollama Cloud at `https://ollama.com/v1`).
2. Operator opens `/models`, picks `Ollama (local / cloud)`, fills in `ollama_base_url` (defaults to `http://localhost:11434`), API key (only required when targeting `ollama.com`), and picks a model from the auto-fetched dropdown — or types one in by hand.
3. Operator clicks **Test**; a 1-token smoke prompt confirms the model responds.
4. Operator targets the new `model_id` from any strategy role / digest / chatbot path exactly like any other model.

---

## 2. Deployment scope

Three deployment targets, all behind one `provider="ollama"` entry:

| Target | base_url example | api_key | Notes |
|---|---|---|---|
| Local | `http://localhost:11434` | empty | Default. No auth. |
| Remote self-hosted | `http://REDACTED-IP:11434` | empty | Operator-managed CORS/host. Same code path as local. |
| Ollama Cloud | `https://ollama.com/v1` | required Bearer | Detected by hostname check: parsed `urlparse(base_url).hostname` equals `ollama.com` or ends with `.ollama.com`. UI marks the api_key field required when so. |

No separate provider enum for cloud vs local — `base_url` is the discriminator.

---

## 3. Architecture choice

We follow **Approach A** (inline `_call_ollama` next to existing per-provider functions in `llm_utils.py`) plus a small **`backend/ollama_client.py`** module for non-chat HTTP operations (`/api/tags`, `/api/show`, `/api/ps`).

Rationale: the six non-CLI providers all live inline in `llm_utils.py`. Introducing a second factoring pattern just for Ollama would create an inconsistency with no offsetting benefit, since Ollama's chat surface looks structurally identical to OpenAI's. Model discovery / health-check is a different concern (no LLM call), so it earns its own module.

---

## 4. Components

### 4.1 New module: `backend/ollama_client.py`

Thin async wrappers over the official `ollama` SDK. Single responsibility: keep non-chat HTTP out of `llm_utils.py`.

```python
# Public functions
async def list_models(base_url: str, api_key: str | None = None,
                      *, timeout_sec: float = 8.0) -> list[dict]: ...
async def show_model(base_url: str, api_key: str | None,
                     model: str, *, timeout_sec: float = 8.0) -> dict: ...
async def health_check(base_url: str, api_key: str | None = None,
                       *, timeout_sec: float = 4.0) -> tuple[bool, str]: ...

class OllamaConnectionError(Exception): ...   # network/timeout
class OllamaAuthError(Exception): ...         # 401 from cloud
class OllamaProviderError(Exception): ...     # other non-2xx
```

Each function instantiates `ollama.AsyncClient(host=base_url, headers=_auth_headers(api_key))`. `_auth_headers` returns `{"Authorization": f"Bearer {api_key}"}` when api_key is non-empty, else `{}`.

`list_models()` returns the SDK's `models` list verbatim minus internal-only fields, projected to:
```python
{"name": str, "model": str, "parameter_size": str | None,
 "quantization_level": str | None, "size_bytes": int,
 "context_length": int | None}  # context_length only populated when ≤20 models
```

When there are 20 or fewer models, the function also fans out `show_model()` calls concurrently (`asyncio.gather` with a `Semaphore(4)`) to enrich each entry with `context_length`. Above 20, the field is `None` — caller can lazy-fetch on demand.

### 4.2 `backend/llm_utils.py` extensions

Seven surgical additions, each mirroring the closest existing analogue. Exception classes (`OllamaConnectionError`, `OllamaAuthError`, `OllamaProviderError`) live in `ollama_client.py` (§4.1) and are imported here; `_call_ollama` and `call_ollama_with_tools` translate raw `ollama` SDK exceptions and `httpx` errors into these shared classes so callers and the critical-guard see a consistent shape regardless of which entry point raised.

1. **`_call_ollama(api_key, model, prompt, max_output_tokens, timeout_sec=None, retries=0, base_url="http://localhost:11434", response_mime_type=None, reasoning_effort="")`** — sibling of `_call_openai`. Uses `ollama.Client(host=base_url, headers=_auth_headers(api_key)).chat(...)`. Maps `max_output_tokens` → `options.num_predict`. Maps `response_mime_type="application/json"` → `format="json"` (per Ollama docs). Ignores `reasoning_effort` (model-specific, not a standard option). Retries via existing `_compute_backoff_seconds()`. Telemetry via `_safe_record()`.

2. **`_call_ollama_structured_from_strategy(...)`** — feeds PydanticAI. Uses **`OpenAIChatModel(model, provider=OpenAIProvider(base_url=f"{base_url.rstrip('/')}/v1", api_key=api_key or "ollama"))`** because PydanticAI's `OllamaModel` is less mature than its OpenAI-compatible path. (Ollama exposes JSON-schema responses through both surfaces.)

3. **`call_ollama_with_tools(api_key, model, prompt, tools, ...)`** — mirrors `call_gemini_with_tools`'s **return** shape (`{"text": str, "tool_calls": [{"name": str, "arguments": dict}, ...]}`) but accepts OpenAI-style tool dicts at the **input** (`[{"type": "function", "function": {"name": ..., "parameters": ...}}]`) since Ollama is OpenAI-compatible. A small `_normalize_tools_to_openai_shape()` helper converts Gemini-style tool dicts when callers pass them (detected by presence of top-level `function_declarations` key).

4. **`_resolve_provider_config(provider, provider_config)`** — new branch:
   ```python
   elif p == "ollama":
       base = str(
           resolved.get("ollama_base_url") or
           os.environ.get("OLLAMA_BASE_URL") or
           "http://localhost:11434"
       ).rstrip("/")
       resolved["ollama_base_url"] = base
       keep_alive = resolved.get("ollama_keep_alive")
       if keep_alive:
           resolved["ollama_keep_alive"] = str(keep_alive).strip()
   ```

5. **`resolve_api_key_for_provider(provider, explicit_api_key)`** — new branch:
   ```python
   if p == "ollama":
       return (explicit_api_key
               or os.environ.get("OLLAMA_API_KEY", "")
               or "")  # empty string is valid for local
   ```

6. **Dispatcher** (`call_llm_by_provider`, llm_utils.py:~4527) — new branch before the gemini fallback:
   ```python
   elif p == "ollama":
       _result = _call_ollama(
           api_key, model, prompt,
           max_output_tokens=max_output_tokens,
           timeout_sec=timeout_sec, retries=retries,
           base_url=str(resolved.get("ollama_base_url") or "http://localhost:11434"),
           response_mime_type=response_mime_type,
       )
   ```

7. **Structured dispatcher** (`call_structured_llm_by_provider`) — analogous new branch routing to `_call_ollama_structured_from_strategy`.

### 4.3 `backend/llm_critical_guard.py` extension

Inside `classify()`, add explicit recognition for Ollama failure shapes:

- **`auth_failure`** when provider is `"ollama"` and the wrapped exception is `OllamaAuthError`, OR the raw HTTP status was 401, OR the error body contains `unauthorized`/`api key`. Only meaningfully fires for Ollama Cloud — local Ollama returns no 401s. Treated as **critical**: do not retry.
- **`provider_5xx_persistent`** — reuses the existing per-`(provider, model)` 5xx counter. No code change needed; Ollama participates automatically once it appears in dispatch.
- **No content-filter / abuse-monitor case.** The Azure regexes in `_is_non_retryable_filter_response` already gate on the provider being Azure-shaped; Ollama responses won't match.

### 4.4 Rate limiting

Ollama participates in the per-`(provider, model)` request rate limiter at zero extra code cost (the limiter is keyed by tuple). **We do not seed any limits for Ollama in this pass** — local has no rate limit, and cloud throttling thresholds are not yet documented enough to encode meaningfully. If we observe throttling in practice, we add entries with the same pattern used for NVIDIA NIM.

### 4.5 New API endpoint: `POST /ollama/list-models`

Lives in `backend/api/main.py` next to the existing `/models` CRUD.

**Request body** (`OllamaListModelsBody`):
```python
class OllamaListModelsBody(BaseModel):
    base_url: str = Field(..., max_length=512)
    api_key:  Optional[str] = Field(None, max_length=512)
```

**Response (200):**
```json
{
  "models": [
    {"name": "llama3.2", "parameter_size": "3B",
     "quantization_level": "Q4_K_M", "context_length": 131072},
    {"name": "qwen2.5:14b", "parameter_size": "14B",
     "quantization_level": "Q4_K_M", "context_length": 32768}
  ]
}
```

**Error responses:**
- `502 {"error": "Could not reach Ollama at <url>"}` on `OllamaConnectionError`.
- `401 {"error": "Authentication failed"}` on `OllamaAuthError`.
- `400 {"error": "Invalid base_url"}` on validation failures.

**Auth:** same dependency as `/models` (any authenticated user). The body parameters are short-lived — never persisted by this endpoint — so it's safe to accept api_key here.

**`POST /llm/test` extension:** `LlmConfigTestBody` gains `ollama_base_url: Optional[str]` and `ollama_keep_alive: Optional[str]`. Routing happens through the standard dispatcher.

### 4.6 Frontend additions

**`frontend/src/utils/strategyConfig.js`:**
```js
export const LLM_PROVIDER_OPTIONS = [
  // ... existing entries ...
  { value: 'ollama', label: 'Ollama (local / cloud)' },
]
```
Extend `buildStrategyLlmTestPayload`:
```js
if (provider === 'ollama') {
  payload.ollama_base_url = String(draft.ollamaBaseUrl || 'http://localhost:11434').trim()
  if (draft.ollamaKeepAlive) payload.ollama_keep_alive = String(draft.ollamaKeepAlive).trim()
  if (draft.apiKey) payload.api_key = draft.apiKey
}
```

**`frontend/src/composables/useOllamaModels.js`** (new, ~50 lines):
- `async function loadModels({ baseUrl, apiKey, force = false }) → { models, error }`.
- 30-second in-memory cache keyed by `(baseUrl, apiKey ?? '')`.
- `force=true` bypasses the cache (used by the Refresh button).

**`frontend/src/components/LlmConfigForm.vue`** — new conditional block (`v-if="draft.provider === 'ollama'"`):
- `ollamaBaseUrl` input. Placeholder: `http://localhost:11434 or https://ollama.com/v1`.
- API key input: label dynamically reads "API key (required for ollama.com)" when host contains `ollama.com`, otherwise "API key (optional)".
- Model picker:
  - When model list loads successfully → `<select>` of model names + adjacent "🗘 Refresh" button + small "Type a custom name" toggle that reveals a free-text input.
  - When model list fetch fails → automatic fallback to free-text input with a one-line warning banner above ("Couldn't reach Ollama — enter model name manually").
- "Advanced" toggle (collapsed) exposing `ollamaKeepAlive` (text, placeholder `5m`).
- Reasoning effort field: **hidden** for Ollama.

**`frontend/src/views/ModelsView.vue`** — extend `formDraft`:
```js
ollamaBaseUrl: 'http://localhost:11434',
ollamaKeepAlive: '',
```
No list-row changes (the row template already shows provider + model + name).

**No new image asset.** Material Symbols continue to be the visual style. A future Ollama logo can be added without a schema change.

---

## 5. Storage — RethinkDB Models table

RethinkDB is schemaless; no migration required.

New fields on the per-document shape when `provider == "ollama"`:

```python
{
  "provider":         "ollama",
  "model":            "llama3.2",                   # user-picked tag string
  "api_key":          "<bearer for cloud, empty for local>",  # reuses existing field
  "ollama_base_url":  "http://localhost:11434",     # new
  "ollama_keep_alive": "5m",                        # new, optional
  # existing pricing-override fields remain available (set to 0 for local)
}
```

**Backend Pydantic** — extend `CreateModelBody` and `EditModelBody` in `backend/api/main.py`:
```python
ollama_base_url:  Optional[str] = Field(None, max_length=512)
ollama_keep_alive: Optional[str] = Field(None, max_length=16)
```

**`_validate_provider_model_compat`** (interactive_utils.py) — register `"ollama"` with a permissive rule: any non-empty string is a valid model. No blacklist; Ollama users name models by Modelfile tag.

**API key masking** — the existing masking in `action_list_models` (`key[:4] + "****" + key[-4:]`) applies to Ollama api_key transparently, no extra code.

**Secrets** — Ollama Cloud Bearer tokens follow the existing plaintext convention. Out of scope to introduce Fernet for one provider; the broader plaintext-API-key concern is tracked separately.

---

## 6. Environment variables

Add to `.env.example`:
```bash
# Optional fallback for the Ollama provider when a Models row leaves these blank.
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_API_KEY=
```

Both are optional. `OLLAMA_BASE_URL` is consulted only when the Models row has an empty `ollama_base_url`. `OLLAMA_API_KEY` is consulted only when the Models row has an empty `api_key`.

---

## 7. Error handling & resiliency

| Failure | Path | UX / system behavior |
|---|---|---|
| Local Ollama not running | `/ollama/list-models` returns 502 | UI banner; model picker degrades to free-text |
| Cloud auth failure (401) | `_call_ollama` raises `OllamaAuthError` | `critical_guard.classify` returns `("auth_failure", True)`; backtest pauses cleanly |
| Model not installed (404 on `/api/chat`) | `_call_ollama` raises | Verbatim error in Test result and backtest pause banner; not retried |
| Cold-load delay | First call after Ollama boot can take 10–60s | 120-second read timeout on first call per `(base_url, model)` since process boot; 30-second for warm calls |
| Persistent 5xx | Existing per-`(provider, model)` 5xx counter | After 3 consecutive 5xx → `provider_5xx_persistent` → backtest pauses |
| Network timeout | `_call_ollama` raises `OllamaConnectionError` | Standard retry loop applies; non-critical |

Notes:
- The "first call per `(base_url, model)` since process boot" timing is tracked by a small in-memory set `_ollama_warm_pairs: set[tuple[str,str]]` in `llm_utils.py`. A pair is added on first successful response. Survives the process lifetime; resets on restart. No persistence needed.
- We deliberately do **not** add an `_ollama_failed_pairs` short-circuit — that's the critical-guard's job.

---

## 8. Tool calling — adapter details

Existing tool-using caller (`call_gemini_with_tools`) accepts a Gemini-shape tool list:
```python
tools = [{"function_declarations": [{"name": "...", "description": "...",
                                     "parameters": {...}}]}]
```

Ollama accepts an OpenAI-shape list:
```python
tools = [{"type": "function", "function": {"name": "...", "description": "...",
                                           "parameters": {...}}}]
```

`_normalize_tools_to_openai_shape(tools)` in `llm_utils.py`:
1. If the first element has a top-level `function_declarations` key, flatten each declaration into OpenAI shape.
2. If the first element has `type == "function"`, pass through unchanged.
3. Otherwise raise `ValueError`.

`call_ollama_with_tools` always normalises before dispatch. Return shape matches `call_gemini_with_tools` so callers don't branch on provider:
```python
{"text": str, "tool_calls": [{"name": str, "arguments": dict}, ...]}
```

Model capability detection: we trust the caller to pick a tool-capable model. Out of scope to gate model selection on `/api/show` capabilities — if a non-tool model receives a tool call, Ollama returns the prose text without `tool_calls` and `_call_ollama` surfaces that verbatim. We will optionally surface a warning in the Test panel when `tools` is non-empty but `tool_calls` is empty.

---

## 9. Testing

### 9.1 Unit tests (new)

- `tests/test_ollama_client.py` — `list_models`, `show_model`, `health_check`. Mock `ollama.AsyncClient`. Cases: happy path, ConnectionError, 401 cloud auth, 500 server.
- `tests/test_llm_utils_ollama.py` — `_call_ollama` happy path; 429 retry; persistent 5xx; 404 (model not installed) NOT retried; `response_mime_type="application/json"` → `format="json"`; `reasoning_effort` ignored without error; `ollama_keep_alive` propagated as `options.keep_alive`.
- `tests/test_llm_utils_ollama_structured.py` — PydanticAI structured output via OpenAI-compat `/v1` endpoint, mocked.
- `tests/test_llm_utils_ollama_tools.py` — OpenAI-shape tool list passthrough; Gemini-shape tool list converted; non-tool model returns prose only with empty `tool_calls`.
- `tests/test_llm_critical_guard_ollama.py` — 401 from cloud → `auth_failure`; 5xx persistence → `provider_5xx_persistent`; 200 resets counter.
- `tests/test_models_api_ollama.py` — CRUD on a Models row with `provider="ollama"`; round-trips `ollama_base_url` and `ollama_keep_alive`; api_key masking works; `_validate_provider_model_compat` accepts any non-empty model string.
- `tests/test_ollama_list_models_endpoint.py` — happy path, 502 on connection failure, 401 on cloud auth, 400 on invalid base_url.

### 9.2 Frontend unit tests (new)

- `frontend/tests/composables/useOllamaModels.spec.js` — list fetch success; cache hit; force-refresh bypass; network failure path.
- `frontend/tests/components/LlmConfigForm.ollama.spec.js` — provider switch shows/hides the Ollama branch; model dropdown populates; free-text fallback when fetch fails; api_key field becomes required when host contains `ollama.com`.

### 9.3 Live integration (opt-in)

- `tests/integration/test_ollama_live.py` — gated by `RUN_OLLAMA_LIVE=1`. Requires a local Ollama with `llama3.2` installed. Runs:
  1. One smoke prompt via `call_llm_by_provider(provider="ollama", model="llama3.2", ...)`.
  2. One structured-output call via `call_structured_llm_by_provider(...)`.
  3. One tool-calling call via `call_ollama_with_tools(...)`.

### 9.4 Manual verification checklist

1. Local Ollama with `llama3.2` — add Models row, Test passes, run a tiny backtest with the new model_id.
2. Stop local Ollama — `/ollama/list-models` returns 502; UI degrades; free-text entry still saves a row; Test fails cleanly with no retry storm.
3. Point at `https://ollama.com/v1` with valid API key — Test passes.
4. Point at `https://ollama.com/v1` with wrong API key — critical-guard fires `auth_failure`; backtest pauses cleanly (mirrors Azure abuse-monitor behavior).
5. Tool-calling: pick `qwen2.5:14b` (or similar tool-capable model), run a tool-using strategy — tool dispatch works.
6. Cold-load: stop the model, start a backtest immediately — first iteration may take 30s but does not time out.

---

## 10. Out of scope

- **In-app `ollama pull` with streamed progress.** Operators install models via the `ollama` CLI; the form offers a Refresh button. Tracked in §13.
- **Embeddings.** No existing provider in `llm_utils.py` exposes embeddings; not adding it just for Ollama.
- **Encrypting LLM API keys at rest.** Pre-existing concern across all providers; not opening that door here.
- **Refactoring `_call_openai` and Ollama to share a common OpenAI-compatible code path.** Worthwhile at some point, but mixing it with a new provider review is risky. Land Ollama as a sibling first.
- **Automatic capability detection** (forcing model picker to gate on `/api/show` `capabilities.tools` etc.). Manual selection is fine for first pass.

---

## 11. Risks

| Risk | Mitigation |
|---|---|
| `ollama` Python SDK adds a new direct dep | One well-supported package, official, MIT-licensed. Pin to a known-good version. |
| Cold-load timeout misclassified as outage by critical-guard | First-call timeout bumped to 120s; warmup short-circuits subsequent calls. |
| Ollama Cloud rate limits unknown | No preset; we wait for empirical signal before adding a rate-limit entry. |
| Tool format drift between Ollama versions | `call_ollama_with_tools` accepts OpenAI shape, which Ollama explicitly supports; we don't touch the on-the-wire format. |
| Operator points at a non-Ollama URL that returns junk JSON | `_call_ollama` raises on parse failure; surfaces in Test result; not retried. |

---

## 12. Acceptance criteria

A. `/models` UI lists Ollama as a provider option. Adding a row, picking a model from the auto-populated list, clicking Test, and seeing a successful smoke response succeeds against a local Ollama with `llama3.2` installed.

B. A backtest configured with the new model_id runs end-to-end against the local Ollama — all of plain-text, structured-output, and tool-calling roles dispatch correctly.

C. With Ollama stopped: the Models form does not crash, the UI surfaces a clear error, and any in-flight backtest pauses (not aborts) on the first failed call, mirroring existing critical-guard behavior.

D. Pointing at `https://ollama.com/v1` with a valid API key works; pointing at it with an invalid key triggers `auth_failure` critical-guard and pauses cleanly.

E. All unit tests in §9.1 and §9.2 pass. Live integration test in §9.3 passes when run with `RUN_OLLAMA_LIVE=1` against a host with `llama3.2`.

---

## 13. Future work

- **In-app `ollama pull` with streamed progress.** Dedicated spec when there's demand.
- **Model auto-recommend.** Use `/api/show` `capabilities` to suggest tool-capable models when the strategy needs tools.
- **Refactor `_call_openai` and `_call_ollama` to share a parametric OpenAI-compatible helper.** Defer until at least one more OpenAI-compatible provider lands.
- **Ollama-specific rate-limit entries** once cloud throttling is observed in production.
- **Embeddings provider path** if/when the app gains an embeddings use case.
