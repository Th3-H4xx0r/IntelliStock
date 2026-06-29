# OpenRouter Provider Setup

IntelliStock supports [OpenRouter](https://openrouter.ai) as an LLM provider.
OpenRouter is an OpenAI-compatible gateway to hundreds of models (Anthropic,
OpenAI, Google, Meta, Mistral, DeepSeek, and more) behind a single API key.

## 1. Get an API key

Create a key at <https://openrouter.ai/keys>. It looks like `sk-or-v1-…`.

## 2. Add a Model in IntelliStock

In the web app go to **Models → New Model** (or the **Models** tab on mobile):

| Field | Value |
|-------|-------|
| Provider | **OpenRouter** |
| Model | A `vendor/model` id, e.g. `anthropic/claude-3.5-sonnet`, `openai/gpt-4o-mini`, `google/gemini-2.5-pro`. Pick from the live catalog dropdown, or type it. |
| API Key | your `sk-or-…` key |
| Base URL | leave as `https://openrouter.ai/api/v1` (only change for a self-hosted proxy) |
| Reasoning Effort | optional — `Low` / `Medium` / `High` for thinking models |
| HTTP-Referer / X-Title | optional — see *Attribution* below |

The web **Model** dropdown is populated from OpenRouter's public catalog
(`GET /api/v1/models`). Selecting a model also **auto-fills its pricing** into
the per-model cost-override fields.

## 3. Pricing auto-fill

When you pick a model from the catalog dropdown, IntelliStock reads that model's
catalog pricing (USD per token) and fills the per-row cost overrides, converting
to USD per **1M tokens**:

| OpenRouter catalog field | IntelliStock cost field |
|--------------------------|-------------------------|
| `prompt`                 | Input $/1M |
| `completion`             | Output $/1M |
| `input_cache_read`       | Cache-read $/1M |
| `input_cache_write`      | Cache-create $/1M |

These are a **prefill, not a lock** — edit or clear them before saving. They
take precedence over `backend/llm_pricing.yaml` in telemetry cost accounting.

**Limitation:** per-request, per-image, and web-search surcharges aren't
representable in the per-1M-token cost model, so they aren't tracked (same as
every other provider). Token costs are accurate.

## 4. Attribution (optional)

OpenRouter can attribute usage to your app on its public leaderboard. Set:

- **HTTP-Referer** — your site URL (required for an app page to be created)
- **X-Title** — your app's display name

Both are optional; calls work fine without them. They're sent as request
headers only when set.

## Environment-variable fallback (optional)

Instead of (or in addition to) per-row values, the backend reads:

- `OPENROUTER_API_KEY` — used when a Model row leaves the key blank
- `OPENROUTER_BASE_URL` — overrides the default base URL
- `OPENROUTER_HTTP_REFERER`, `OPENROUTER_X_TITLE` — default attribution headers

## Notes

- Model ids are namespaced `vendor/model`. A bare name (no `/`) is almost
  always wrong and will 404 — the form warns about this.
- Reasoning uses the standard `reasoning_effort` knob (OpenRouter accepts it as
  an alias for its native `reasoning.effort`), so cache identity and history
  labels behave the same as the OpenAI/NVIDIA providers.
