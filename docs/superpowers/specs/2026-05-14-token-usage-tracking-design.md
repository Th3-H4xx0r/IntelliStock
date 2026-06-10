# Token Usage Tracking — Design

- **Date:** 2026-05-14
- **Status:** Draft for review
- **Branch:** `claude-code-integration`
- **Author session:** continuation of CC daemon-reuse work (see `.sessions/2026-05-14-194023-lookback-day-optimizations-and-daemon-scaffold.md` and `2026-05-14-cc-daemon-reuse-and-token-audit-design.md`)

## Background

The operator runs IntelliStock on a Claude Max $100/mo plan plus multiple paid API providers (Azure OpenAI, Gemini, DeepSeek, OpenAI direct, NVIDIA). Today, token counts ARE extracted at every LLM-call dispatch point but are written only to stdout as `"Tokens: input=X output=Y"` log lines. The thread-local `_LAST_STRUCTURED_LLM_CALL.data["usage"]` dict surfaces tokens to the immediate caller but is overwritten on every subsequent call and never persists.

There is no queryable record of LLM usage. The operator cannot answer:

- "Why did my Claude Max quota burn 5× faster yesterday?"
- "Which strategy / call-site is the most expensive?"
- "How many gpt-oss-120b tokens did backtest #387303 consume?"
- "What's my projected monthly spend across all providers?"

This spec specifies a token-usage tracking subsystem (backend persistence + REST API + Vue 3 UI page) that captures every LLM call across every provider, attributes it to context (strategy / backtest / call-site), computes USD cost from a pricing registry, and surfaces both aggregate diagnostics and the 50 most recent calls in a dashboard page.

## Goals

1. **Capture every LLM call.** All structured-output paths in `backend/llm_utils.py` (`_call_openai`, `_call_azure_openai`, `_call_gemini`, `_call_deepseek`, `_call_claude_cli_structured_from_strategy`) AND the chatbot path in `backend/chatbot/claude_cli_provider.py` (`call_claude_cli_chat` per-event token deltas + `total_cost_usd` extraction).
2. **Persist per-call rows + daily aggregates** in RethinkDB so historical analysis remains fast as row count grows.
3. **Attribute calls to context** (backtest_id, instance_id, strategy, call_site, conversation_id) so the operator can drill down from "expensive day" to "which call".
4. **Compute USD cost** from a checked-in YAML pricing registry, overridable per model via the existing `Models` table. For claude-cli specifically, use the `total_cost_usd` from the envelope when available (most accurate).
5. **Surface a single Token Usage page** in the existing Vue frontend with: top-line widgets, time-series chart, top-10 spenders, and a 50-most-recent-calls table with filters and drill-down.
6. **Probe the local claude CLI state file** (e.g., `~/.claude/usage.json` or similar) as an optional secondary signal for Max-plan quota estimation.
7. **Don't slow down LLM calls.** Telemetry writes happen asynchronously via an in-memory queue + background flusher.

## Non-Goals

- Anthropic Console scraping. Out of scope; fragile and ToS-questionable.
- Real-time per-call WebSocket push to the UI. The 30-second auto-refresh on the UI is adequate.
- Cost projections / forecasting beyond a simple "Max plan est" widget computed as (this-month-claude-cli-cost / days-elapsed) × days-in-month.
- Multi-tenant per-user cost reporting. (The existing `Models` table is user-editable per `backend/api/main.py`, but the operator is the sole consumer of this UI for the foreseeable future.)
- Hard rate-limit enforcement (budget caps that block calls). The UI shows usage; it does not gate dispatch.
- Migration of existing log-only token data. Telemetry starts from the first call after deploy.

## Architecture

Three layers, plus a central telemetry sink module.

```
┌──────────────────────────────────────────────────────────┐
│ UI (Vue 3)                                                │
│   frontend/src/views/TokenUsageView.vue                   │
│     widgets · time-series chart · top spenders · table    │
└───────────────────────┬──────────────────────────────────┘
                        │ REST (existing Bearer-auth pattern)
┌───────────────────────▼──────────────────────────────────┐
│ Backend API (FastAPI)                                     │
│   backend/api/llm_usage.py                                │
│     /api/llm-usage/summary · timeseries · top-spenders ·  │
│     calls · health                                        │
└───────────────────────┬──────────────────────────────────┘
                        │
┌───────────────────────▼──────────────────────────────────┐
│ Persistence (RethinkDB)                                   │
│   LLMUsage (per-call rows, ~30-100K/month)                │
│   LLMUsageDaily (rollup buckets, ~10/day)                 │
│   backend/llm_pricing.yaml (pricing defaults)             │
│   Models table (per-model pricing overrides — existing)   │
└──────────────────────────────────────────────────────────┘
                        ▲
                        │ record_llm_call()
┌───────────────────────┴──────────────────────────────────┐
│ Central telemetry sink                                    │
│   backend/llm_telemetry.py                                │
│     · in-memory deque (thread-safe)                       │
│     · daemon flusher (5 s OR 50 rows, whichever first)    │
│     · in-memory ring buffer of last 200 calls (UI cache)  │
│     · pricing lookup (yaml → models-table → unknown)      │
│     · llm_call_context() thread-local context manager     │
└──────────────────────────────────────────────────────────┘
                        ▲
                        │ called at 6 instrumentation sites
                       (see Section "Instrumentation")
```

### Boundary rules

- `llm_telemetry.py` is the only module that knows about the `LLMUsage` / `LLMUsageDaily` tables. Other modules call its public API and don't touch RethinkDB directly.
- Telemetry failures NEVER propagate to LLM-call sites. The 6 instrumentation sites wrap `record_llm_call()` in `try / except Exception: pass` defensively; the sink itself also swallows + stderr-logs internally.
- LLM-call latency is unchanged: the instrumentation site appends to the queue (O(1)) and returns. Pricing math + DB I/O happen in the daemon flusher thread.
- A 5-second window of buffered calls is lost on hard crash. Acceptable.

## Data Model

### `LLMUsage` — per-call rows

```python
{
    "id": str,                           # UUID4 hex
    "ts": int,                           # Unix epoch ms — INDEXED
    "provider": str,                     # see "Provider values" below
    "model": str,                        # canonical model name
    "model_id": Optional[str],           # FK to Models table when call sourced from a configured Model row

    # Tokens — any field may be 0 if provider doesn't report it
    "input_tokens": int,
    "output_tokens": int,
    "cache_creation_input_tokens": int,
    "cache_read_input_tokens": int,
    "reasoning_tokens": int,             # Sonnet extended thinking, OpenAI o-series

    # Cost — computed at write time
    "input_cost_usd": float,
    "output_cost_usd": float,
    "cache_creation_cost_usd": float,
    "cache_read_cost_usd": float,
    "total_cost_usd": float,             # convenience sum (also the source-of-truth when cost_source == "envelope")
    "cost_source": str,                  # "yaml" | "models_override" | "envelope" | "unknown"

    # Attribution
    "backtest_id": Optional[str],
    "instance_id": Optional[str],
    "strategy": Optional[str],
    "call_site": Optional[str],
    "conversation_id": Optional[str],

    # Outcome
    "ok": bool,
    "duration_ms": int,
    "retry_count": int,
    "error": Optional[str],              # truncated to 200 chars
}
```

**Provider values:** `"openai" | "openai-compatible" | "azure" | "gemini" | "deepseek" | "anthropic" | "claude-cli" | "claude-cli-chat" | "nvidia"`.

**Indexes:**
- Primary: `id`.
- Secondary: `ts`, `provider`, `model`, `backtest_id`, `instance_id`.
- Compound: `[ts, provider]` (for the most common dashboard query).

### `LLMUsageDaily` — daily rollup buckets

```python
{
    "id": str,                           # "{date}_{provider}_{model}" e.g. "2026-05-14_claude-cli_claude-sonnet-4-6"
    "date": str,                         # ISO date "YYYY-MM-DD" — INDEXED
    "provider": str,
    "model": str,
    "call_count": int,
    "input_tokens": int,
    "output_tokens": int,
    "cache_creation_input_tokens": int,
    "cache_read_input_tokens": int,
    "reasoning_tokens": int,
    "total_cost_usd": float,
    "last_updated_ts": int,
}
```

**Rollup strategy:** background sweep every 60 minutes, run by `backend/llm_telemetry.py` daemon. Reads yesterday + today's rows from `LLMUsage`, recomputes aggregates per `(date, provider, model)`, upserts. Idempotent.

### `backend/llm_pricing.yaml` — pricing registry

```yaml
# USD per 1M tokens. Update when providers change pricing.
# Per-model entries in the Models table (input_cost_per_1m, output_cost_per_1m)
# override these values.

claude-sonnet-4-6:
  provider: anthropic
  input_per_1m: 3.00
  output_per_1m: 15.00
  cache_creation_per_1m: 3.75
  cache_read_per_1m: 0.30

claude-opus-4-7:
  provider: anthropic
  input_per_1m: 15.00
  output_per_1m: 75.00
  cache_creation_per_1m: 18.75
  cache_read_per_1m: 1.50

claude-haiku-4-5:
  provider: anthropic
  input_per_1m: 1.00
  output_per_1m: 5.00
  cache_creation_per_1m: 1.25
  cache_read_per_1m: 0.10

# Azure / OpenAI models
gpt-5-mini:
  provider: azure
  input_per_1m: 0.25
  output_per_1m: 2.00

gpt-oss-120b:
  provider: azure
  input_per_1m: 0.0     # included in plan; tracked at zero
  output_per_1m: 0.0

# Gemini
gemini-2-5-pro:
  provider: gemini
  input_per_1m: 1.25
  output_per_1m: 10.00

# DeepSeek
deepseek-chat:
  provider: deepseek
  input_per_1m: 0.27
  output_per_1m: 1.10

# Fallback for unknown models — yields cost_source = "unknown"
_unknown_:
  input_per_1m: null
  output_per_1m: null
```

### Models-table override (existing table, additive fields)

The existing `Models` RethinkDB table (managed by `ModelsView.vue` + `/api/models/*`) gains four optional fields:

```python
{
    # ... existing fields ...
    "input_cost_per_1m": Optional[float],
    "output_cost_per_1m": Optional[float],
    "cache_creation_cost_per_1m": Optional[float],
    "cache_read_cost_per_1m": Optional[float],
}
```

When present, these override the YAML registry. When absent, YAML applies. Editable from `ModelsView.vue` (add four numeric fields to the existing form). Migration: existing rows simply lack the fields — accessor uses `.get(field) or yaml_fallback`.

## Pricing Lookup Order

For each recorded call:

1. **Envelope override** — if `cost_usd_override` is passed (claude-cli chat envelope path), use it directly. `cost_source = "envelope"`. The component costs (input/output/cache) are computed proportionally if the envelope only gives a total.
2. **Models-table override** — look up `model_id` in the Models table; if all four cost-per-1m fields are present, compute cost. `cost_source = "models_override"`.
3. **YAML registry** — look up `model` in `llm_pricing.yaml`. `cost_source = "yaml"`.
4. **Unknown** — record tokens with all cost fields = 0 and `cost_source = "unknown"`. UI distinguishes these so the operator can populate pricing.

## Instrumentation

### `backend/llm_telemetry.py` public surface

```python
def configure(
    *,
    db_conn_factory: Callable[[], Any],
    flush_interval_s: float = 5.0,
    max_buffer: int = 50,
    pricing_yaml_path: str = "backend/llm_pricing.yaml",
    enabled: bool = True,
) -> None: ...

def record_llm_call(
    *,
    provider: str,
    model: str,
    usage: dict,                            # {"input_tokens": int, ...}
    ok: bool = True,
    duration_ms: int = 0,
    retry_count: int = 0,
    error: Optional[str] = None,
    cost_usd_override: Optional[float] = None,
    model_id: Optional[str] = None,
) -> None: ...

@contextmanager
def llm_call_context(
    *,
    backtest_id: Optional[str] = None,
    instance_id: Optional[str] = None,
    strategy: Optional[str] = None,
    call_site: Optional[str] = None,
    conversation_id: Optional[str] = None,
) -> Iterator[None]:
    """Thread-local context manager. Telemetry reads the active context when
    record_llm_call() runs. Nested contexts merge (inner wins for set fields).
    """

def get_recent_calls(n: int = 50) -> List[dict]:
    """Read from the in-memory ring buffer (last ~200 calls). Sub-ms; bypasses DB.
    Used by /api/llm-usage/calls?range=now for instant first paint.
    """

def flush() -> None:
    """Forced sync flush. Called on graceful shutdown + by tests."""

def get_buffer_depth() -> int: ...        # for /api/llm-usage/health
def get_pricing(model: str) -> Optional[dict]: ...  # exposed for the Models page
```

### Six instrumentation sites

| # | Site (file:approx-line) | Provider | Token-field source |
|---|---|---|---|
| 1 | `backend/llm_utils.py:_call_openai` end | openai / openai-compatible / nvidia | response.usage |
| 2 | `backend/llm_utils.py:_call_azure_openai` end | azure | response.usage |
| 3 | `backend/llm_utils.py:_call_gemini` end | gemini | response.usageMetadata |
| 4 | `backend/llm_utils.py:_call_deepseek` end | deepseek | PydanticAI .usage() |
| 5 | `backend/llm_utils.py:_call_claude_cli_structured_from_strategy` end | claude-cli | envelope usage dict (extended in Phase-1 probe wiring) |
| 6 | `backend/chatbot/claude_cli_provider.py` chat event-stream handler | claude-cli-chat | per-event delta + `total_cost_usd` |

Each site adds ~4 lines:

```python
try:
    _record_llm_call(
        provider=...,
        model=...,
        usage={...extracted dict...},
        duration_ms=int((time.monotonic() - _t0) * 1000),
        retry_count=_attempt,
        cost_usd_override=envelope_cost if provider == "claude-cli-chat" else None,
    )
except Exception:
    pass  # never block LLM call on telemetry
```

### Context-manager wrapping at call sites

Strategies that invoke the dispatcher wrap with `llm_call_context()`. Initial wrapping:

- `backend/strategies/graph_nexus_analysis.py` — wrap `_classify_company_article_chunk`, `_classify_macro_article_chunk`, sentiment LLM, and active-event maintenance batches with `call_site="..."` and `backtest_id=instance_id` (which is the backtest id in lookback context).
- `backend/strategies/ml_news.py` — wrap the news classification dispatcher.
- `backend/strategies/earnings.py` — wrap the earnings analysis dispatcher.
- `backend/strategies/nexus_analyst_panel.py` — wrap the analyst panel dispatcher.

Chatbot path doesn't wrap; it records with `call_site=None, conversation_id=<thread's conv_id>`.

### Local claude CLI state file probe

`backend/llm_telemetry.py` includes a `probe_local_cli_usage_file() -> Optional[dict]` helper that:

1. Checks `~/.claude/usage.json`, `~/.claude/.usage`, `~/.config/claude/usage.json`.
2. If found and parseable, returns the dict.
3. If not found, returns `None`.

The probe runs once at telemetry `configure()` time and then every 5 minutes (cheap). Results are exposed via `/api/llm-usage/summary` as an optional `cli_usage_file` field. Best-effort — if the CLI doesn't maintain such a file, we simply omit the field.

## API Endpoints

All under `/api/llm-usage` in a new router `backend/api/llm_usage.py`. Bearer auth (existing pattern). JSON request/response.

```
GET /api/llm-usage/summary?range=24h|7d|30d
  → {
      "period_start": int,         # epoch ms
      "period_end": int,
      "total_calls": int,
      "total_tokens": int,
      "total_cost_usd": float,
      "by_provider": [
        {"provider": str, "model": str, "calls": int, "tokens": int, "cost_usd": float}, ...
      ],
      "max_plan_estimate_usd": Optional[float],    # only when sufficient claude-cli history exists
      "cli_usage_file": Optional[dict],            # from probe_local_cli_usage_file
      "telemetry_health": {"buffer_depth": int, "last_flush_age_s": int, "write_errors_24h": int}
    }

GET /api/llm-usage/timeseries?range=24h|7d|30d&bucket=hour|day&provider=
  → [{"bucket_start_ts": int, "provider": str, "model": str, "tokens": int, "cost_usd": float}, ...]

GET /api/llm-usage/top-spenders?range=24h|7d|30d&group_by=model|strategy|call_site&limit=10
  → [{"key": str, "calls": int, "tokens": int, "cost_usd": float}, ...]   # desc by cost

GET /api/llm-usage/calls?limit=50&offset=0&provider=&model=&backtest_id=&strategy=
  → [{<LLMUsage row>}, ...]
  # When limit<=50 and offset==0 and no filters, served from in-memory ring buffer (sub-ms).
  # Otherwise hits LLMUsage table.

GET /api/llm-usage/health
  → {"buffer_depth": int, "last_flush_ts": int, "write_errors_24h": int, "row_count_estimate": int}
```

**Query routing logic:**
- `range=24h` and recent data → read `LLMUsage` directly (single table scan with `ts` index).
- `range=7d` or `30d` → read `LLMUsageDaily` for the closed buckets + `LLMUsage` for today's partial bucket; merge.
- Sub-second response time target on all queries up to 100K rows.

## UI

### New file: `frontend/src/views/TokenUsageView.vue`

Vue 3 SFC with Composition API, follows the pattern of `ModelsView.vue` / `DashboardView.vue`.

**Layout** (see section header mockup in the brainstorming notes):

1. **Header row.** Title + range selector (24h / 7d / 30d toggle buttons). Manual refresh button.
2. **Widget row** — 4 cards across:
   - Today's spend (USD, with up/down vs yesterday indicator)
   - This-month spend (USD, with input/output token totals subtext)
   - Claude Max plan estimate (USD spent / $100 budget bar)
   - Calls per day (current period average)
3. **Time-series chart** — stacked-bar by provider, x-axis is hour (24h range) or day (7d/30d). Library: Chart.js (likely already in deps — check). Hover tooltip shows breakdown.
4. **Top-spenders panels** — two side-by-side tables, top 10 each:
   - By model (model, calls, tokens, cost)
   - By call_site (call_site, calls, tokens, cost). Hidden if no rows have a call_site.
5. **Recent calls table** — 50 most recent rows. Columns: Time (HH:MM:SS), Provider, Model, Input tokens, Output tokens, Cost, Strategy/call_site (collapsed). Filterable by provider/model/backtest_id via a filter row above the table. Clicking a row opens a detail modal showing all fields including cache tokens, reasoning tokens, retry count, conversation_id, error.

**Auto-refresh:** 30 s polling on all sections. Manual refresh button next to the range selector.

**Navigation:** add a `Token Usage` nav entry in the existing sidebar/topbar, alongside `Models`.

### `ModelsView.vue` (existing) — additive changes

Add four optional numeric fields to the existing model-edit form: `input_cost_per_1m`, `output_cost_per_1m`, `cache_creation_cost_per_1m`, `cache_read_cost_per_1m`. Tooltips note these override `backend/llm_pricing.yaml`.

### Color / formatting conventions

- Cost: USD with 4 decimals when < $1, 2 decimals when ≥ $1.
- Tokens: K/M abbreviations (e.g., `12.4k`, `2.1M`).
- `cost_source = "unknown"` rows show cost as `—` with a tooltip "no pricing configured for {model}".

## Error Handling

### Telemetry sink

- Append-to-queue failure (impossibly rare): `try/except Exception: pass`. Counted in a private counter exposed via health endpoint.
- DB write failure in flusher: catch, log to stderr, increment `write_errors_24h`, retry with exponential backoff. Drop oldest buffered rows if buffer exceeds 5000 (memory safety).
- Pricing YAML missing / malformed: telemetry continues with all cost fields = 0 and `cost_source = "unknown"`. Log loud stderr warning at startup.

### API

- Standard FastAPI error handling. `503` if telemetry sink is disabled (`enabled=False`).
- Empty period: return zero-totals object, not 404.

### UI

- API errors → toast notification, retain stale data.
- Empty period → "No LLM calls in this window" placeholder.

## Testing

### Backend unit tests (`backend/tests/test_llm_telemetry.py` — new)

- `test_record_llm_call_writes_to_buffer` — basic append.
- `test_pricing_yaml_to_cost` — feed a known usage dict, assert cost math matches YAML.
- `test_models_table_override` — Models-table fields shadow YAML.
- `test_envelope_cost_override` — `cost_usd_override` short-circuits the registry.
- `test_unknown_model_yields_zero_cost_unknown_source` — fallback path.
- `test_flush_drains_buffer_to_db` — patch DB, assert insert called with expected rows.
- `test_flush_failure_does_not_lose_rows_within_buffer_cap` — retry on transient DB failure.
- `test_buffer_overflow_drops_oldest_not_newest` — memory-safety cap.
- `test_llm_call_context_attribution` — context manager populates backtest_id/strategy/call_site on the recorded row.
- `test_recent_calls_ring_buffer_returns_latest_n` — UI-cache path.

### Backend API tests (`backend/tests/test_api_llm_usage.py` — new)

- `test_summary_aggregates_by_provider` — seed rows, call endpoint, assert shape.
- `test_timeseries_buckets_by_hour_and_day` — seed rows across day boundary, assert bucketing.
- `test_top_spenders_orders_desc_by_cost`
- `test_calls_pagination_offset_and_limit`
- `test_calls_filter_by_backtest_id`
- `test_health_returns_buffer_state`
- `test_query_routes_to_rollup_for_long_ranges` — 30d window prefers `LLMUsageDaily` reads.

### Backend integration test (`backend/tests/test_llm_utils_telemetry_integration.py` — new)

- Patch each of the 6 call paths to return a known usage dict; invoke through `call_structured_llm_by_provider`; assert exactly one row written with correct provider/model/tokens.
- Test the chat-event handler captures `total_cost_usd` correctly.

### Frontend tests (deferred to follow-up; see Non-Goals)

The Vue page is presentational and the API contract is covered by backend tests. If the existing frontend has a test suite (likely Vitest), one smoke test that mounts `TokenUsageView` with mocked API responses suffices.

## Performance Targets

- **Telemetry overhead:** <100 µs per call (buffer append + 1 lock acquire). Verified by microbenchmark in unit tests.
- **API summary query:** <200 ms on 100K LLMUsage rows.
- **API timeseries (30d):** <500 ms; uses LLMUsageDaily.
- **UI first paint:** <1 s on a 5 Mbps connection.

## Out of Scope (cross-references)

- Anthropic Console scraping for exact Max-plan quota.
- WebSocket push for sub-30s live updates.
- Multi-tenant per-user usage segmentation.
- Hard budget caps that block LLM dispatch.
- Migrating existing stdout-only token logs into the new table.

## Open Questions

1. **Frontend chart library.** Check `frontend/package.json` for an existing chart lib; if none, recommend Chart.js (small, no React-isms). Implementation-plan decision, not a spec decision.
2. **`Models` table migration.** Existing rows simply lack the four new cost fields; accessor defaults to YAML. No explicit migration step required, but the implementation plan should add a one-liner to the `ensure_models_table` helper if column-presence is enforced anywhere downstream.
3. **CLI state file format.** The probe is best-effort; if the operator can share what `~/.claude/usage.json` looks like (if it exists), the probe parser can be specified concretely. If not, probe gracefully returns `None`.
4. **Existing `_LAST_STRUCTURED_LLM_CALL` consumers.** The thread-local data dict is read by some callers (e.g., `get_last_structured_llm_call_metadata` at `llm_utils.py:1897`). Adding telemetry does not change its shape — `_LAST_STRUCTURED_LLM_CALL` is preserved unchanged for backward compatibility; `llm_telemetry.py` is purely additive.

## Risks

- **YAML pricing drift.** Providers change prices without warning; our YAML defaults will get stale. Mitigation: surface `cost_source` in the recent-calls modal so the operator sees when YAML is being used and can override via the Models table.
- **claude-cli envelope cost field may not exist on all subscription tiers.** If `total_cost_usd` isn't in the envelope for Max plan, we fall back to YAML (which we'd set to `0.0` for Max-included models). The Phase-1 probe in the daemon-reuse spec (sibling spec) will incidentally answer this question.
- **DB row growth.** At 30-100K rows/month, two years = ~2M rows. Acceptable for RethinkDB. Daily rollup keeps query speed bounded even further out.
- **Context-manager wrap omission.** Strategies that forget to wrap with `llm_call_context()` will record rows with empty attribution. Not a correctness bug (cost still attributes to provider/model), just a debugging-quality regression. Mitigation: the implementation plan adds the wrap to all four strategy files in a single PR.
