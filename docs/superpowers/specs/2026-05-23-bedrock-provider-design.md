# Amazon Bedrock as an IntelliStock LLM Provider — Design

- Status: approved (brainstorming complete, 2026-05-23)
- Author: pair session (operator + Claude)
- Related: `2026-05-23-ollama-provider-design.md` (the immediately-preceding provider integration; Bedrock follows its conventions)

## 1. Goal

Add **Amazon Bedrock** as a first-class LLM provider, on equal footing with the existing
providers (`gemini`, `openai`, `azure`, `nvidia`, `deepseek`, `ollama`, `anthropic`,
`claude-cli`, `codex-cli`). "First-class" means full parity with `ollama`:

- Usable for **plain text**, **structured/JSON**, and **tool-calling** LLM calls.
- Selectable in the Models CRUD UI, in per-instance LLM pickers, and in strategy config
  (including all Graph Nexus roles: sentiment, event_maintenance, etc.).
- Participates in telemetry (`_safe_record`) and the critical-guard pause-on-failure flow.
- Backed by a written design (this doc) → implementation plan → TDD implementation →
  parallel bug sweep, matching how `ollama` shipped.

## 2. Authentication & deployment scope

**Auth model: Bedrock API key (bearer token), v1.** AWS Bedrock long-term API keys are a
single bearer token that authenticates data-plane (and, policy-permitting, control-plane)
requests without SigV4 credential pairs. This maps cleanly onto the existing single
`api_key` field — no second secret, no credential-pair schema change.

- **Region is required** and is a new per-model field (`bedrock_region`).
- The bearer token is injected **per boto3 client** (see §4.1), never via a process-wide
  `AWS_BEARER_TOKEN_BEDROCK` env var, so concurrent calls resolving different Bedrock
  models with different keys cannot collide.
- Static IAM access-key/secret pairs, AWS profiles, and the ambient credential chain
  (IAM role / instance profile) are **out of scope for v1** (see §10). The client builder
  is structured so they can be added later without reshaping callers.

Deployment is unchanged: self-hosted (Dockploy + nginx), global plaintext `Models` table
in RethinkDB. Backtest runs spawn a fresh broker process (caches reset per run); live mode
is a long-running broker.

## 3. Architecture choice

**Hybrid (Approach C), mirroring the Ollama integration:**

| Call path | Driver |
|-----------|--------|
| Plain text (`_call_bedrock`) | boto3 `bedrock-runtime.converse()` |
| Tool-calling (`call_bedrock_with_tools`) | boto3 `bedrock-runtime.converse()` with `toolConfig` |
| Structured / JSON (`_call_bedrock_structured_from_strategy`) | PydanticAI `BedrockConverseModel` via the existing `_build_pydantic_ai_model()` factory |
| Model discovery (`/bedrock/list-models`) | boto3 control-plane `bedrock.list_foundation_models()` + `list_inference_profiles()` |

All chat paths use the **Converse API**, which normalizes request/response across every
Bedrock model family (Anthropic Claude, Meta Llama, Amazon Nova, Mistral, Cohere, AI21)
and natively supports tool use — so there is a single code path regardless of model family.
The structured path reuses the shared PydanticAI validation/retry/`raw_json_fallback`
machinery that `gemini`/`deepseek`/ollama-structured already go through. No provider base
class exists in this codebase; dispatch is flat `if/elif`, so each touchpoint gets an
explicit `bedrock` branch (§4.2, §4.4).

**Dependency change:** `backend/requirements.txt` line 54 becomes
`pydantic-ai-slim[google,openai,bedrock]==1.0.18` (the `bedrock` extra pulls boto3). An
explicit floor `boto3>=1.40.0` is added with a comment, because Bedrock **bearer-token**
auth requires a recent botocore. PydanticAI 1.0.18 ships `models/bedrock.py`
(`BedrockConverseModel`) and `providers/bedrock.py` (`BedrockProvider`) — verified present.

## 4. Components

### 4.1 New module: `backend/bedrock_client.py`

Mirrors `backend/ollama_client.py`.

```
# Exceptions
class BedrockConnectionError(Exception): ...   # network / endpoint unreachable
class BedrockAuthError(Exception): ...         # 401/403, AccessDenied, expired/invalid token
class BedrockProviderError(Exception): ...     # other 4xx/5xx from Bedrock

# Client builders — bearer token injected per-client, no global env var.
def build_runtime_client(api_key: str, region: str): ...   # bedrock-runtime
def build_control_client(api_key: str, region: str): ...   # bedrock (control plane)

# Discovery
def list_models(api_key: str, region: str) -> list[dict]: ...
    # returns [{id, name, provider_name, kind: "foundation"|"inference_profile",
    #           supports_tools: bool, modalities: [...]}], merging
    # list_foundation_models() + list_inference_profiles(); raises typed exceptions.
```

**Bearer-token injection mechanism.** Build the client with
`botocore.config.Config(signature_version=botocore.UNSIGNED)` and register a
`before-send.bedrock*` (or `before-sign`) event handler that sets
`request.headers["Authorization"] = f"Bearer {api_key}"`. This keeps auth per-client with
no process-global state. The plan pins the exact event name and verifies it against a live
endpoint; if botocore in the pinned version exposes a first-class per-client token API,
use that instead.

### 4.2 `backend/llm_utils.py` extensions

Follow the `_call_ollama` conventions throughout: **return `""` on any failure (never
raise)**, call `_stash_last_http(status, body, exc)` on error, call
`_safe_record(provider="bedrock", model=..., usage={...}, ok=..., duration_ms=...,
retry_count=..., error=..., model_id=...)` for telemetry, and emit a
`[llm_utils] BEDROCK TOKENS:` line (input/output tokens from Converse `usage`).

| Symbol (approx. line) | Change |
|---|---|
| `resolve_api_key_for_provider()` (~196) | `bedrock` → `os.environ.get("BEDROCK_API_KEY")` fallback |
| `_cache_effort_key()` (~576) | `bedrock` → normalized `bedrock_reasoning` so the prompt cache does not share entries across reasoning levels |
| `_resolve_provider_config()` (~608) | extract `bedrock_region`, `bedrock_reasoning` into the resolved dict |
| `_build_pydantic_ai_model()` (~715) | `bedrock` branch → `BedrockConverseModel(model, provider=BedrockProvider(bedrock_client=build_runtime_client(api_key, region)))`; pass reasoning via model settings / `additionalModelRequestFields` |
| `call_structured_llm_by_provider()` dispatch (~2342) | `bedrock` branch; `api_key` is **required** (no empty-key exemption like ollama) |
| `_normalize_bedrock_reasoning(value, model)` (new) | maps `off/low/medium/high` → model-specific `additionalModelRequestFields` (§ below) |
| `_call_bedrock(...)` (new, plain) | boto3 `.converse(modelId, messages, inferenceConfig={maxTokens,temperature}, additionalModelRequestFields=<reasoning>)`; extract assistant text, fall back to any reasoning/text block if content empty |
| `call_bedrock_with_tools(...)` (new) | boto3 `.converse(..., toolConfig={"tools":[...], "toolChoice":...})`; loop while `stopReason == "tool_use"`, dispatch tool calls, feed `toolResult` blocks back |
| `call_llm_by_provider()` dispatch (~5038) | `elif p == "bedrock": return _call_bedrock(...)` |

**Reasoning normalization** (`_normalize_bedrock_reasoning`):
- Anthropic Claude (3.7+ / models advertising reasoning): `off` → omit; `low/medium/high`
  → `{"reasoning_config": {"type": "enabled", "budget_tokens": N}}` with
  N ≈ 1024 / 4096 / 16384.
- Amazon Nova: equivalent reasoning field when applicable.
- Any model that does not support reasoning, or `off`: **omit the field entirely** (sending
  it raises `ValidationException`). Model-family detection is by `model` ID prefix /
  discovery metadata.

### 4.3 `backend/strategies/graph_nexus_analysis.py`

| Symbol (approx. line) | Change |
|---|---|
| `_NEXUS_VALID_PROVIDERS` (624) | **add `"bedrock"`** — the critical allowlist; without it `_normalize_llm_provider` silently rewrites `bedrock`→`gemini` (the exact class of bug that cost the most time in the Ollama work) |
| `_default_model_for_provider()` (~741) | `bedrock` → `os.environ.get("GRAPH_NEXUS_BEDROCK_MODEL")` or the documented default `us.anthropic.claude-3-5-sonnet-20241022-v2:0` (a broadly-available inference profile in `us-*` regions; overridable per-model and via env) |
| `_default_api_key_for_provider()` (~766) | `bedrock` → `os.environ.get("BEDROCK_API_KEY", "")` |
| `_resolve_role_llm_provider_config()` (~793) | `bedrock` branch returns `{"bedrock_region": ..., "bedrock_reasoning": ...}` resolved through the config hierarchy |
| provider/model mismatch warning (~1064) | optional hint when a `bedrock` model ID looks wrong (no `anthropic.`/`meta.`/`amazon.`/`us.`/`eu.` prefix) |
| `_hierarchy_llm_config()` (~13119) | include `bedrock` in the allowed-provider check + hierarchy fallback |

### 4.4 `backend/llm_critical_guard.py`

Add Bedrock error-shape classification (regexes over the stashed error code/message):

- **auth_failure** (pauses backtest): `AccessDeniedException`, `UnrecognizedClientException`,
  `InvalidSignatureException`, `ExpiredTokenException`, `UnauthorizedException`, HTTP 401/403.
- **provider_5xx_persistent** (pause after N consecutive): `InternalServerException`,
  `ServiceUnavailableException`, `ModelNotReadyException`, HTTP 5xx.
- **throttling**: `ThrottlingException` / `TooManyRequestsException` → retry/backoff;
  escalate to persistent only if it never clears.
- **config error** (do not hammer; surfaced but not a transient retry): `ValidationException`,
  `ResourceNotFoundException` (bad model ID / missing inference profile).

The existing per-`(provider, model)` consecutive-failure counter is provider-agnostic and
needs no change beyond the new patterns.

### 4.5 `backend/model_resolver.py`

Add `bedrock_region` and `bedrock_reasoning` to the `*_llm_*` field map (~131–176) so a
`*_llm_model_id` reference expands into inline `*_llm_bedrock_region` /
`*_llm_bedrock_reasoning`. Both go in the **`always_overwrite`** set so a stale prior
provider's fields cannot leak into a Bedrock resolution (the fix pattern established for
ollama). Verbose `[ModelResolver]` logging includes the new fields.

### 4.6 `backend/api/main.py`

| Symbol (approx. line) | Change |
|---|---|
| `LlmConfigTestBody` (~461) | add `bedrock_region: Optional[str]` (max_length 32), `bedrock_reasoning: Optional[str]` (max_length 16) |
| `CreateModelBody` (~487) | same two fields |
| `EditModelBody` (~519) | same two fields |
| `_build_llm_test_provider_config()` (~368) | `bedrock` block: copy region + reasoning into the test config |
| `api_test_llm_config()` `/llm/test` (~1374) | `bedrock` requires `api_key` (no ollama-style exemption); surface reasoning/thinking text in the smoke result like ollama's `smoke_thinking` |
| **new** `POST /bedrock/list-models` (~near 1987) | body `{api_key, region}` → `bedrock_client.list_models`; returns `{models:[...]}` or a typed error payload (auth/permission/connection). Mirrors `api_ollama_list_models`. Backend-only (no browser-direct fallback). |

### 4.7 `backend/interactive_utils.py`

- `action_create_model()` (~8025): add `bedrock_region=None, bedrock_reasoning=None`
  params; persist `(value or "").strip()` into the model doc.
- `action_edit_model()` (~8075): add `bedrock_region`, `bedrock_reasoning` to the
  updateable-fields loop.

### 4.8 Frontend

| File | Change |
|------|--------|
| `frontend/src/utils/strategyConfig.js` | `LLM_PROVIDER_OPTIONS` (~303) add `{ value: 'bedrock', label: 'AWS Bedrock' }`; add `BEDROCK_REASONING_OPTIONS` (`Off/Low/Medium/High`); `buildStrategyLlmTestPayload` (~687) `bedrock` branch → `api_key, model, bedrock_region, bedrock_reasoning` |
| `frontend/src/composables/useBedrockModels.js` (**new**) | `loadBedrockModels({ apiKey, region, force })` → `POST /api/bedrock/list-models`; 30s TTL cache; **no** browser-direct fallback (SigV4/CORS infeasible); exposes `models`, `loading`, `error`; on error the form permits manual model-ID entry |
| `frontend/src/components/LlmConfigForm.vue` | `bedrock` template branch: **region** (text input + datalist of common regions, default `us-east-1`), **api_key** (Bedrock API key, required), **model** (searchable picker via `useBedrockModels`, graceful manual fallback), **reasoning** dropdown; `onProviderChange` clears bedrock-only fields on switch-away and seeds region default on switch-to; `effortOptions`/effort label includes bedrock reasoning |
| `frontend/src/views/ModelsView.vue` | `formDraft` bedrock fields (`bedrockRegion:'us-east-1'`, `bedrockReasoning:''`); hydrate from stored model (`bedrock_region`, `bedrock_reasoning`); `_reasoningCell` bedrock branch (shows reasoning effort); `submitModel` payload bedrock fields |
| `frontend/src/views/InstanceDetailView.vue` | LLM-picker `effortLabel` bedrock branch + field hydration (`bedrockRegion`, `bedrockReasoning`) |
| `frontend/src/views/InstancesView.vue` | same picker `effortLabel` + hydration |

snake_case ↔ camelCase: `bedrock_region`↔`bedrockRegion`, `bedrock_reasoning`↔`bedrockReasoning`.

## 5. Storage — RethinkDB `Models` table

No schema migration (schemaless). A Bedrock model row carries:

```
provider:          "bedrock"
api_key:           "<Bedrock API key / bearer token>"
model:             "us.anthropic.claude-3-5-sonnet-20241022-v2:0"   # foundation OR inference-profile id
bedrock_region:    "us-east-1"
bedrock_reasoning: "off" | "low" | "medium" | "high"
```

Existing rows of other providers are unaffected (extra fields ignored by their paths).

## 6. Environment variables

Optional fallbacks when a `Models` row leaves fields blank (mirrors other providers):

```
BEDROCK_API_KEY              # bearer token fallback
GRAPH_NEXUS_BEDROCK_MODEL    # default model id for nexus roles when unspecified
AWS_REGION / BEDROCK_REGION  # region fallback (per-model field preferred)
```

The per-client bearer-token injection means we deliberately do **not** rely on
`AWS_BEARER_TOKEN_BEDROCK` being set process-wide.

## 7. Error handling & resiliency

- **Never raise out of the call paths** — return `""` and stash. boto3 `ClientError`
  exposes `response["Error"]["Code"]`/`["Message"]` and
  `response["ResponseMetadata"]["HTTPStatusCode"]`; `_stash_last_http` records status from
  the metadata and a body built from code+message so the critical-guard can classify it.
- **Retries**: transient (`ThrottlingException`, 5xx) retried with backoff up to the
  existing retry budget; **no retry** on auth or `ValidationException`/`ResourceNotFound`.
- **Discovery is best-effort**: `/bedrock/list-models` failures (including a narrowly-scoped
  key lacking `bedrock:ListFoundationModels`, or control-plane bearer-token auth not being
  honored) return a typed error; the UI shows it and **falls back to manual model-ID entry**.
  Discovery never blocks saving a model.
- **Inference-profile guidance**: when Converse returns a validation error indicating the
  model needs an inference profile, the error surfaces with a hint to use the `us.`/`eu.`
  prefixed profile ID.

## 8. Tool calling — adapter details

Converse tool format: `toolConfig={"tools": [{"toolSpec": {"name","description",
"inputSchema": {"json": <schema>}}}], "toolChoice": {...}}`. The loop:

1. Send messages + `toolConfig`.
2. If `stopReason == "tool_use"`, collect `toolUse` blocks (`toolUseId`, `name`, `input`).
3. Execute each tool; append a user message with `toolResult` blocks
   (`toolUseId`, `content`, optional `status:"error"`).
4. Repeat until `stopReason == "end_turn"` (or a max-iteration guard).

This adapts the codebase's existing tool-call contract to Converse shapes; the public
`call_bedrock_with_tools` signature matches the other providers' tool entrypoints.

## 9. Testing

### 9.1 Unit tests (new, `backend/tests/`)
- `_call_bedrock` happy path (mock boto3 client `.converse` response) → returns text +
  records telemetry; failure (`ClientError`) → returns `""` + stashes status/body.
- `_normalize_bedrock_reasoning`: Claude tiers → correct `budget_tokens`; `off` and
  unsupported models → field omitted.
- Dispatch: `call_llm_by_provider("bedrock", ...)` routes to `_call_bedrock`;
  `call_structured_llm_by_provider` builds the PydanticAI Bedrock model.
- `_build_pydantic_ai_model` bedrock branch returns a `BedrockConverseModel`.
- Critical-guard: each Bedrock error code maps to the right classification.
- `model_resolver`: bedrock fields expand and `always_overwrite` clears stale fields.
- `bedrock_client.list_models`: normalizes foundation + inference-profile rows; maps
  AccessDenied→`BedrockAuthError`, endpoint error→`BedrockConnectionError`.
- API body validation accepts/limits the new fields; `action_create_model` /
  `action_edit_model` round-trip them.

Run: `python3 -m pytest backend/tests/ -k bedrock --ignore=backend/tests/test_intellistock_logger.py --ignore=backend/tests/test_redact_logger.py`

### 9.2 Live integration (opt-in)
Gated by `RUN_BEDROCK_LIVE=1` (+ `BEDROCK_API_KEY`, `BEDROCK_REGION`, a model ID): real
plain + structured + tools round-trips. Mirrors `RUN_OLLAMA_LIVE`.

### 9.3 Frontend
`cd frontend && npm run build` clean (no test runner). Manual smoke of the form: provider
select shows AWS Bedrock; region/key entry; model picker loads (or degrades to manual);
reasoning dropdown; Test vs Test&Save. **Note:** full UI behavior can only be verified with
a live key + region; this will be called out rather than claimed.

## 10. Out of scope (v1)

- Streaming (`converse_stream`).
- Static IAM access-key/secret pairs, AWS profiles, ambient/IAM-role credential chain,
  cross-account assume-role. (Client builder is shaped to add these later.)
- Per-model `InvokeModel` (we use Converse exclusively).
- Embeddings.
- In-app model-access enablement / provisioned-throughput / model management.

## 11. Risks

- **Bearer-token control-plane support**: a Bedrock API key may authenticate the data plane
  (Converse) but not `bedrock:ListFoundationModels` on the control plane (policy- or
  service-dependent). Mitigation: discovery is best-effort with manual-entry fallback (§7).
- **botocore version for bearer auth**: too-old botocore lacks bearer-token handling.
  Mitigation: explicit `boto3>=1.40.0` floor + a unit test asserting the bearer header is
  attached.
- **Inference profiles**: newer models reject on-demand foundation-model IDs and require a
  region-prefixed inference-profile ID. Mitigation: discovery lists profiles; validation
  errors surface a hint (§7).
- **`_NEXUS_VALID_PROVIDERS` omission**: forgetting to add `bedrock` silently degrades to
  `gemini`. Mitigation: explicit unit test that `_normalize_llm_provider("bedrock") == "bedrock"`.
- **Reasoning field sent to unsupported model**: raises `ValidationException`. Mitigation:
  model-aware omission + a unit test.

## 12. Acceptance criteria

1. A `bedrock` model can be created/edited in the Models UI (region + key + model + reasoning),
   `/llm/test` smoke passes against a live key, and the model is selectable in instance LLM
   pickers and strategy config.
2. Plain, structured, and tool-calling calls all succeed via Converse; failures return `""`,
   stash HTTP shape, and the critical-guard classifies Bedrock errors correctly.
3. `_normalize_llm_provider("bedrock")` returns `"bedrock"` (allowlist wired).
4. Model discovery populates the picker when permitted and degrades to manual entry otherwise.
5. `python3 -m pytest backend/tests/ -k bedrock ...` is green; `npm run build` is clean.
6. No regression in existing providers (their dispatch/test suites still pass).

## 13. Future work

- Static IAM credentials + ambient credential chain (second auth mode).
- Streaming responses.
- In-app surfacing of which models the key can access (filter discovery by entitlement).
- Shared OpenAI-compat / Converse helper refactor if a 2nd Converse-style provider lands.
