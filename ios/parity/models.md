# Parity checklist — LLM models and token usage (Wave 2, agent "app", area 3)

Ports `mobile/lib/features/models/{application,presentation}/**` and
`mobile/lib/features/token_usage/{application,presentation}/**`. Tests:
`test/features/models/llm_config_draft_test.dart` (all of it — the data layer had no part of it) and
the UI logic of `test/features/token_usage/token_usage_repository_test.dart` (range → bucket,
telemetry state; the models were ported by the data agent).

Legend: `[x]` ported as-is · `[x] → native form: …` deliberately changed in form · `[ ]` open.

## models_controller.dart → `Features/LLMModels/Model/ModelsModel.swift`

- [x] `ModelsController` (autoDispose): `GET /models` on build; `refresh()` → loading then reload.

## models_screen.dart → `Features/LLMModels/Views/ModelsView.swift`

- [x] Back + refresh app-bar buttons → native form: navigation back + `refresh` toolbar button; pull to refresh.
- [x] Header: eyebrow `Models`, `LLM Models`, `Centralized LLM model configurations. Strategies can reference these instead of storing credentials inline.`, `Add Model` (`add`).
- [x] Skeleton → native form: redacted placeholder rows. Error → error banner + Retry.
- [x] Empty: `psychology`, `No models saved yet`, `Add a model to start using centralized LLM configurations.`, `Add Model`.
- [x] Card: name, provider label (`Google Gemini`, `DeepSeek`, `OpenAI Compatible`, `Azure OpenAI`, `NVIDIA NIM`, `Ollama (local/cloud)`, `AWS Bedrock`, `OpenRouter`, `Claude Code CLI`, `OpenAI Codex CLI`, else raw).
- [x] Chips `Model: …` (mono), `Effort: …` (`_reasoningCell`: CLI `—`; ollama think Default/On/Off/capitalised; bedrock Off/capitalised; else Default/capitalised), `Key/CLI: …` (claude-cli path or `claude`, codex-cli path or `codex`, else key or `—`), `Created: …` (`fmtDate`).
- [x] claude-cli rows: test button (`cable`, spinner) → `POST /models/:id/test-cli`; `Testing…`; ok → `✓ v<version|?>, <logged in|not logged in>[, response: …]`; else `error ?? 'Unknown error'`; failure → error text; green/red line.
- [x] Edit (`edit`) and Delete (`delete_outline`, red) with labels `Edit`, `Delete`, `Test CLI connection`.
- [x] Delete confirm `Delete "<name>"?` / `Strategies referencing this model will revert to inline credentials.` / `Delete` → `DELETE /models/:id` → refresh; failure → `Delete failed: $e`.
- [x] Add/Edit sheet: `Add Model` / `Edit Model`, `Save a reusable LLM configuration.`; close (after a save → closes and refreshes; disabled while submitting).
- [x] Edit prefill: name; draft from the model (api key blank; azure version default `2024-10-21`; ollama URL default `http://localhost:11434`; bedrock region `us-east-1`, reasoning `off`; openrouter URL default); pricing fields from the costs (`toString`).
- [x] `Name` (`e.g. Gemini Flash — Main`).
- [x] `Pricing override (optional)` (collapsible): `Leave blank to use backend llm_pricing.yaml defaults. Values are $/1M tokens.`; `Input cost ($/1M tokens)`, `Output cost ($/1M tokens)`, `Cache creation cost ($/1M tokens)`, `Cache read cost ($/1M tokens)` (`e.g. 3.00`, decimal pad); parsed with `double.tryParse` into `input_cost_per_1m`, `output_cost_per_1m`, `cache_creation_cost_per_1m`, `cache_read_cost_per_1m` when valid.
- [x] Test only: `Model is required`; claude-cli → `Use the cable icon on the saved row to test a claude-cli model.`; `Testing LLM configuration…` → `POST /llm/test` (draft payload) → `LLM test passed (not saved).` / `LLM test failed: $e`.
- [x] Test & Save: `Name is required`, `Model is required`; test when not claude-cli and (codex-cli or ollama or a key); failure → `LLM test failed: $e` and stop; `Updating model…` / `Saving model…`; `PUT /models/:id` or `POST /models` with the payload + name + pricing.
- [x] claude-cli after save: `Testing Claude CLI connection…` → `POST /models/:id/test-cli` → `Model saved. Claude CLI connection OK (logged in).` / `Model saved, but Claude CLI test failed: <error|unknown error>` / exception text; others → `LLM test passed. Model updated.` / `LLM test passed. Model saved.`; failure → error text.
- [x] Status line (green/red); test-result panel; edit note `Leave API Key empty to keep the existing key unchanged.`
- [x] Buttons: `Cancel` / `Close` (after save), `Test only` / `Testing…` (not claude-cli, not saved), `Test & Save` / `Test & Update` / `Testing…` / `Saving…` / `Updating…`.
- [x] Test-result panel: `LLM connectivity test response`; provider / model / effective (when different) / latency `Nms`; `structured connectivity probe:` pretty JSON; `real-generation smoke` + `(Nms)`, `· content N chars`, `· reasoning N chars`; `prompt: …`; `content:` block (unless 0 chars); collapsible `reasoning (N chars)`; `smoke generation failed: …`; empty → `smoke generation returned empty — structured check passed but the model did not produce free-form text.`; `provider meta:` pretty JSON.
- [x] Bottom sheet → native form: a `.sheet` with a `NavigationStack` and an inset-grouped `Form`; actions in the bottom bar.

## llm_config_form.dart → `LlmConfigDraft` + `LlmConfigFormSection`

- [x] `LlmConfigDraft` fields and defaults (gemini, azure version `2024-10-21`, ollama `http://localhost:11434`, bedrock `us-east-1`/`off`, openrouter `https://openrouter.ai/api/v1`); `copyWith`.
- [x] `toPayload`: provider + trimmed model; `reasoning_effort` when set; `model_cache_family` trimmed + lower-cased; CLI → `cli_path`, `extra_args`; else `api_key`, `openai_base_url`, `nvidia_base_url`, `azure_openai_endpoint`, `azure_openai_api_version` (non-empty, trimmed); ollama → `ollama_base_url` (default when empty), `ollama_keep_alive`, `ollama_think`; bedrock → `bedrock_region`, `bedrock_reasoning` (lower-cased); openrouter → `openrouter_base_url` (default), `openrouter_referer`, `openrouter_title`. `toTestPayload` = `toPayload`.
- [x] Provider change resets: CLI clears key/urls/azure/effort; non-CLI clears CLI fields; ollama default URL + no effort, else clears ollama; bedrock defaults + no effort, else clears bedrock; openrouter default URL, else clears openrouter.
- [x] Provider picker (10 providers, labels as above).
- [x] `Model` / `Deployment / Model Name` (azure); placeholders `e.g. gpt-5.2 deployment name`, `claude-sonnet-4-6`, `gpt-5-codex`, `e.g. gemini-3-flash-preview`.
- [x] `Cache Family (optional)` (`auto (e.g. gpt-oss-120b)`) + `Share LLM cache across same-underlying-model rows. Leave blank to auto-detect.`
- [x] CLI: `CLI Path` (`codex` / `claude`), `Extra Args` (`--sandbox read-only` / `--fallback-model claude-haiku-4-5`); codex → Codex setup panel; claude → info box `Uses the locally-installed claude binary on the server (subscription auth). Tools are disabled — CC is used as a text-only LLM. Use the panel below to re-authenticate when the token expires; no SSH required.` + Claude setup panel.
- [x] Claude model picker: `GET /claude/models?cli_path=…` on show / CLI path change / Refresh; options `label — needs credits`; `Custom…` (`__custom__`) reveals the free-text field (also when the saved model isn't listed); no list → `Couldn't list Claude models: …\nEnter the model name manually above (e.g. claude-sonnet-4-6).` + free text; `No models returned`; hint `1M-context models need usage credits (claude.ai/settings/usage); standard models don't.`; `Available models` + `Refresh` / `Loading...`.
- [x] Ollama: `Ollama Base URL` (`http://localhost:11434 or https://ollama.com/v1`), `API Key (optional — local Ollama has no auth)` (`Ollama Cloud Bearer token, or leave blank`); `Pick from installed models` (fetch on show / URL change / Refresh with `force`) → `POST /ollama/list-models {base_url, api_key?, force?}`; error `Couldn't reach Ollama at this base URL: …`; picker `name · size · quant` (`Select a model`); `Thinking / Effort` (Default, Off, On, Low, Medium, High); `Keep Alive (Advanced)` → `Keep Alive` (`5m`) + `Go duration like 5m (default), 60m, 1h. -1 = never unload.`
- [x] Bedrock: `AWS Region` (`us-east-1`) with the nine suggestion chips; `API Key (required — Bedrock bearer token)` (`Bedrock API key (bearer token)`); `Pick from available models` (fetch needs region + key; on region/key change / Refresh) → `POST /bedrock/list-models {region, api_key, force?}`; error `Couldn't list Bedrock models: …\nEnter the model id manually above (e.g. us.anthropic.claude-3-5-sonnet-20241022-v2:0).`; picker `id · profile · provider`; `Reasoning` (Off, Low, Medium, High).
- [x] `Reasoning Effort` for azure/openai/nvidia/openrouter/claude-cli/codex-cli (nvidia adds `None (off)`).
- [x] API key for the rest: `Azure API Key` / `API Key`, placeholder `NVIDIA API Key (nvapi-...)` / `Optional if provided by environment`.
- [x] OpenAI `OpenAI Base URL` (`Optional custom base URL`); NVIDIA `NVIDIA NIM Base URL` (`https://integrate.api.nvidia.com/v1`).
- [x] OpenRouter: `OpenRouter Base URL`, info `Model ids are vendor/model, e.g. anthropic/claude-3.5-sonnet.`, `HTTP-Referer (optional)` (`https://your-site.example`), `X-Title (optional)` (`IntelliStock`).
- [x] Azure: `Azure Endpoint` (`https://your-resource.services.ai.azure.com`), `API Version` (`2024-10-21`), info `Use the Azure resource root plus your deployment/model name. Do not use a full /models/chat/completions or /openai/v1/ URL here.`
- [x] Disabled while submitting.
- [x] OpenRouter live catalog / pricing auto-fill → not in `mobile/lib` (web only); nothing to port. See Ruling.

## claude_setup_panel.dart → `ClaudeCliSetupPanel`

- [x] Title `Claude Code CLI (subscription) setup` + refresh; `GET` auth status on show.
- [x] Status: `Probing claude CLI status…`; `Status probe failed: …`; `installed: ✓ yes / ✗ no`, `version`, `authenticated`, `account`; auth message.
- [x] Not installed: `The claude binary is not installed on the server. Install it on the server before re-authenticating.`
- [x] Installed: `Re-authenticate if the saved subscription token has expired (e.g. "401 Invalid authentication credentials").` / `Claude is installed but not authenticated. Start the sign-in flow.`; `Re-authenticate Claude` / `Starting…`; Cancel while a login is live.
- [x] Start: `POST` login start `{cli_path}`; login URL host allow-list (claude.ai, www.claude.ai, claude.com, www.claude.com, console.anthropic.com, anthropic.com; http(s), no user info) else `claude returned a non-Anthropic login URL; ignoring for safety`.
- [x] `Login state: …` (success green, failed/cancelled red, else orange) when not `parsed`; error box when no live login.
- [x] Live login: `1. Open the link and sign in.\n2. Copy the authorization code.\n3. Paste it below.`; `Open URL: …` + copy (`Copied`); code field `Paste authorization code`; `Submit code` / `Submitting…`.
- [x] Submit: `Paste the authorization code first.`; `Exchanging code…`; success → `✓ Claude re-authenticated`, clears, refreshes status; else `error|'Login failed'` + `\n<output_tail>`; exception text.
- [x] Cancel → cancel job (errors ignored) → `cancelled`.
- [x] `Sign out of Claude` → confirm `Sign out of Claude?` / `All strategies using claude-cli will need to re-authenticate.` / `Sign out` → logout (errors ignored) → refresh.
- [x] `url_launcher` not used here (copy only) → native form: the URL is also a tappable link (`openURL`), as the brief asks.

## codex_setup_panel.dart → `CodexCliSetupPanel`

- [x] Title `OpenAI Codex CLI setup` + refresh; status on show; `Probing codex CLI status...`; `Status probe failed: …`; installed / version / authenticated chips.
- [x] Not installed: method `unknown` → `The backend has neither npm nor brew available. Rebuild the backend image with INSTALL_CODEX_CLI=1.`; else `Codex CLI is not installed. Click to install via brew install codex|npm install -g @openai/codex.` + `Install Codex CLI` / `Installing...`.
- [x] Install: `POST` install → job; poll every 1.5 s while `running` (`state`, `exit_code`, `log_tail`, `error`); then refresh status; `Install state: … (exit N)`, log tail (last lines, 100 pt), error.
- [x] Not authenticated: `Codex is installed but not authenticated. Start the OpenAI device-code login.`; `Sign in with OpenAI` / `Waiting for sign-in...`; Cancel → cancel job → `cancelled`.
- [x] Login: `POST` start `{cli_path}`; pairing URL allow-list (chatgpt.com, platform.openai.com, auth.openai.com) else `codex returned a non-OpenAI pairing URL; ignoring for safety`; poll every 2 s while `pending`/`parsed`; then refresh status.
- [x] `Login state: …` (not while pending); `Open URL: …` + copy; `Code:` large spaced mono + copy (`Copied to clipboard`); error.
- [x] Authenticated: `✓ Codex CLI is installed and authenticated. Strategies can now select codex-cli.`, auth message, `Sign out of OpenAI` → confirm `Sign out of OpenAI?` / `All strategies using codex-cli will need to re-authenticate.` / `Sign out`.
- [x] Timers stop when the panel goes away.

## token_usage_controller.dart / token_usage_screen.dart → `Features/TokenUsage`

- [x] `TokenUsageController`: range `24h`; fetch all every 10 s, paused in the background; `setRange` refetches now; `refreshNow`; a failed refresh replaces the data with the error (Dart's AsyncError).
- [x] Header: `payments` tile, `Token Usage`, telemetry pill (`Awaiting data` / `Healthy` / `Degraded` (write errors > 0) / `Lagging` (last flush > 30 s)), `Live telemetry across providers, models, and strategy call sites.`
- [x] Range buttons `24h` `7d` `30d` → native form: segmented picker.
- [x] Partial error banner.
- [x] KPI: `PERIOD COST` (`fmtUsdCost`, `N tokens · N calls`, top-3 providers by cost `provider · $` or `No provider spend`); `PERIOD CALLS` (`Avg cost`, `Recent rows`); `MAX PLAN ESTIMATE` (bar vs $100, `N% of $100 Claude Max budget`); `TELEMETRY HEALTH` (`Buffer`, `Last flush` `Ns`, `Errors 24h`).
- [x] `SPEND TREND` / `Cost over time` / `Stacked by provider`: cost summed per provider per bucket, stacked columns, legend, provider colours primary/info/success/warning/teal/danger, y labels `$0.0000` below 1 else `$0.00`; empty `No usage in this window.` / `Calls will appear here after telemetry flushes.` → native form: Swift Charts stacked `BarMark`s, flat colours.
- [x] `Top spenders by model` / `Top spenders by call site`: key (mono), calls, tokens, cost; empty `No model spend recorded yet.` / `No call-site spend recorded yet.`
- [x] `LLM cost by run`: label `displayLabel ?? #<id|?>`, kind badge (`LIVE` green / else accent), instance, first time; cost, `tokens · N calls`, `ok/calls ok` (orange when failures); backtest rows with an id push `/backtests/:id`; empty `No LLM cost data in this range.`
- [x] `Recent calls`: provider, model (`—`), `strategy / call site`, cost, `↑in ↓out`, relative time; tap → detail; empty `No calls recorded yet.`
- [x] Call detail overlay `RECENT CALL` + `model ?? provider ?? 'Call detail'` + pretty JSON (selectable) → native form: a sheet.
- [x] Skeleton → native form: redacted placeholder layout; error → banner + Retry.

## Copy changes (title-style capitalisation)

- `Test only` → `Test Only`; `Submit code` → `Submit Code`; `Sign in with OpenAI` → `Sign In with OpenAI`;
  `Sign out of Claude` → `Sign Out of Claude`; `Sign out of OpenAI` → `Sign Out of OpenAI`; `Sign out` (confirm) → `Sign Out`.

## Rulings

- Ruling: the brief's "OpenRouter live catalog and pricing auto-fill" is not in `mobile/lib` (the Dart form has only the OpenRouter URL / Referer / Title fields; the catalog lives in the web frontend), so nothing was added — porting byte for byte means not inventing it — cost if wrong: the catalog is a separate feature request.
- Ruling: the pickers refetch on the same triggers as Dart's `didUpdateWidget` (Ollama: shown or URL changes; Bedrock: shown, region or key changes; Claude: shown or CLI path changes) through a keyed `.task` — cost if wrong: none.
- Ruling: the Claude and Codex login URLs are also tappable (`openURL`) besides the copy button — the brief asks for URLs opened with `openURL`; the host allow-lists still gate what is shown — cost if wrong: one extra affordance.
- Ruling: saved pricing values prefill with Dart's `double.toString()` (`3` shows `3.0`) — `JSON.dartDoubleString` — cost if wrong: none.
- Ruling: the token-usage refresh uses `fetchAllUnlessCancelled` (orchestrator, core round 2): endpoint failures still fold into `partialError` as in Dart, and a cancelled refresh (the screen went away) leaves the state unchanged instead of reporting "6 of 6 requests failed" — cost if wrong: none.
- Ruling: the top-3 provider sort is stable (ties keep server order); Dart's `List.sort` gave no tie order — cost if wrong: two providers with the exact same cost may swap.
- Ruling: the spend-trend chart uses Swift Charts stacked `BarMark`s with a top legend and the provider palette; Syncfusion's tooltip becomes the default chart (no scrubbing) — cost if wrong: no per-bar tooltip.
- Ruling: the Add / Edit sheet and the call detail are native sheets; the setup panels sit inside the form's CLI section as tinted (not glass) containers — cost if wrong: none.
