# Codex CLI as an IntelliStock LLM Provider — Design

**Status:** Draft v2 (planning only, not yet approved for implementation)
**Date:** 2026-05-20
**Inspired by:** [`NousResearch/hermes-agent`](https://github.com/NousResearch/hermes-agent) — specifically `agent/transports/codex_app_server.py` and `codex_app_server_session.py`. Architectural template is IntelliStock's existing `claude-cli` provider at `backend/chatbot/claude_cli_provider.py`.

**v2 amendments (operator request):**
- The entire onboarding flow runs in the IntelliStock web UI, not the shell.
- When the operator picks `codex-cli` in `/models`, the UI: (a) detects whether `codex` is installed and surfaces a one-click "Install" button if not; (b) walks the operator through Codex's device-code OAuth flow by displaying the pairing code + URL and polling for completion.
- See §10 for the install flow and §11 for the device-code login flow.

---

## 1. Goal

Add a new `provider = "codex-cli"` that lets the operator drive both the chatbot and the structured-output strategy paths through the locally-installed `codex` CLI (OpenAI's reference Codex client). The auth is the user's ChatGPT subscription managed by Codex itself — no OpenAI API key needed inside IntelliStock.

Operator flow at the end:

1. Operator runs `codex login` once in their shell (Codex stores tokens in `~/.codex/`).
2. Operator adds a Codex CLI model in `/models` (`provider="codex-cli"`, `cli_path="codex"`, optional reasoning effort, no API key field).
3. Strategy roles (sentiment, event_maintenance, macro_article, etc.) and the chatbot can target this model_id like any other.
4. If Codex's tokens expire, the broker surfaces a clear "Codex CLI not authenticated — run `codex login`" error in the log and `/models` UI test result, mirroring the existing claude-cli behavior.

---

## 2. Why this is feasible cheaply

The hard work is already done for `claude-cli`. Codex differs in only three meaningful ways:

| Aspect | claude-cli | codex-cli |
|---|---|---|
| Long-lived chat protocol | `--input-format stream-json` (line-delimited JSON, one message per line) | `codex app-server` (JSON-RPC 2.0, line-delimited) |
| Structured output | `--json-schema` flag, schema enforced server-side | No schema flag — must use prompted-JSON mode + Pydantic validate on receipt |
| Auth probe | `claude doctor` / `claude --version` works without login; auth-required errors surface in stderr on the first turn | `codex login --status` (or parse exit code from a no-op request) |

Everything else — process supervision, idle-TTL eviction, concurrency cap, telemetry — copies straight from `claude_cli_provider.py` with the protocol layer swapped.

---

## 3. Components

### 3.1 New module: `backend/chatbot/codex_cli_provider.py`

Mirrors `claude_cli_provider.py`. Public surface (called by `llm_utils.py`):

```python
def call_codex_cli_plain(
    *, model: str, prompt: str,
    provider_config: dict[str, Any] | None = None,
    timeout_sec: int | None = None, retries: int = 0,
) -> str: ...

def call_codex_cli_structured(
    *, model: str, prompt: str, output_type: Any,
    system_prompt: str | None = None,
    provider_config: dict[str, Any] | None = None,
    timeout_sec: int | None = None, retries: int = 0,
    output_retries: int | None = None,
    use_prompt_cache: bool = False,
) -> Any | None: ...

def call_codex_cli_chat_structured(
    *, conversation_id: str, model: str, prompt: str, output_type: Any,
    system_prompt: str | None = None,
    provider_config: dict[str, Any] | None = None,
    timeout_sec: int | None = None, retries: int = 0,
    output_retries: int | None = None,
) -> Any | None: ...

def call_codex_cli_chat(
    conversation_id: str, messages: list[dict[str, str]],
    *, system_prompt: str | None = None,
    provider_config: dict[str, Any] | None = None,
    timeout_sec: int | None = None,
) -> str: ...

def validate_extra_args(raw: str | list[str]) -> str: ...  # allowlist-gated, mirrors claude-cli
def is_authenticated() -> tuple[bool, str]: ...  # cheap probe used by /llm/test
class CodexCliNotInstalledError(Exception): ...
class CodexCliNotAuthenticatedError(Exception): ...
class CodexCliError(Exception): ...
```

### 3.2 Session manager: `CodexAppServerSession`

Owns the long-lived `codex app-server` subprocess for chat. Follows the Hermes pattern with simplifications:

- Spawn once per `(conversation_id, model, cli_path, extra_args)` tuple.
- `subprocess.Popen([cli_path, "app-server", *extra_args], stdin=PIPE, stdout=PIPE, stderr=PIPE, bufsize=0, env=...)`.
- Two reader threads: stdout → JSON-RPC message queue, stderr → ring buffer for error classification.
- Initialization sequence (cribbed verbatim from `hermes-agent/agent/transports/codex_app_server_session.py`):

  1. `initialize` request — `{client_name: "intellistock", client_title: "IntelliStock Agent", client_version: <version>}`. Times out at 15s.
  2. `thread/start` request — returns `thread_id`. Times out at 15s.
  3. Per-turn: `thread/run_turn` with `{thread_id, user_input}`. Notifications stream back via `take_notification(timeout=...)`. Terminal event types: `response.completed`, `response.failed`, `response.incomplete`.
  4. On shutdown / idle TTL: send `shutdown`, wait 5s, then `proc.terminate()`.

- Concurrency cap: shared global `_CODEX_CLI_GLOBAL_SPAWN_SEM` (env `CODEX_CLI_MAX_CONCURRENT`, default 10).
- Idle TTL: env `CODEX_CLI_IDLE_TTL_SEC`, default 3600. Sweeper thread closes idle sessions like claude-cli.

### 3.3 Structured output

Codex does NOT support `--json-schema` like Claude Code. Two-path strategy:

1. **Preferred:** prompted-JSON mode. Wrap the user prompt with the existing `_raw_structured_fallback_prompt(prompt, output_type)` from `llm_utils.py:1131` (which constructs a "respond with JSON matching this schema" prompt). Send via `thread/run_turn`, accumulate `output_text.delta` events, parse with `_validate_structured_output_from_raw_text(output_type, raw_text)` on completion.
2. **Fallback for stubborn turns:** if the first attempt yields invalid JSON, retry with an explicit "Reply with ONLY a JSON object matching this schema. No markdown, no backticks." prefix. Up to `output_retries` (default 2).

This mirrors how the existing code already handles `gpt-oss` and `moonshotai/kimi` quirks (`_model_skips_json_object_format` path) — we already have the prompted-JSON validator. Codex just reuses it.

### 3.4 Auth flow

**On first use:**

1. `_resolve_cli_path("codex")` finds the binary on `PATH`. If missing → `CodexCliNotInstalledError("codex not found on PATH; install via `npm install -g @openai/codex` or `brew install codex`")`.
2. Spawn `codex login --status` (read-only probe) with a 5s timeout. Parse exit code:
   - 0 → authenticated, proceed.
   - non-zero → `CodexCliNotAuthenticatedError("codex CLI not authenticated; run `codex login` in your shell")`.
3. Cache the probe result for 60s per `cli_path` so we don't spawn a probe on every strategy call.

**On expired tokens mid-session:**

- Codex's `thread/run_turn` will fail with stderr like `invalid_grant` / `refresh token` / `unauthorized`. The `_classify_oauth_failure(stderr_blob)` helper (port from Hermes) detects this and surfaces `CodexCliNotAuthenticatedError` to the caller.
- The strategy already handles a missing-LLM response by falling back to company-article classifications, so the impact is graceful degradation, not a crash.

**On UI test (`/llm/test`):**

- Already-existing route at `backend/api/main.py:1338`. Extend to handle `provider="codex-cli"` by calling `is_authenticated()` first, then a 1-turn structured probe with the standard "Return ok=true if this provider configuration is valid" prompt + smoke prompt (same pattern as the recent NVIDIA test).
- If `is_authenticated()` returns False, return a 400 with the message `"codex CLI not authenticated — run `codex login` in your shell, then re-test"`. The modal already surfaces this via `submitMsg`.

### 3.5 Wiring into existing dispatch

- `backend/llm_utils.py`:
  - `_normalize_llm_provider`: add `"codex-cli"` to the valid set at L622 (`_NEXUS_VALID_PROVIDERS`).
  - `_default_api_key_for_provider`: return `"codex-cli-no-api-key"` sentinel (same trick as claude-cli — gates the `if not api_key` short-circuit).
  - `_default_model_for_provider`: return the configured default (suggest `"gpt-5-codex"` or whatever Codex CLI defaults to).
  - `call_llm_by_provider`: add `if p == "codex-cli": return _call_codex_cli_plain(...)` branch (mirrors L3143).
  - `call_structured_llm_by_provider`: add a `provider == "codex-cli"` early-return that dispatches to `call_codex_cli_structured_from_strategy` (mirrors L1594).
- `backend/strategies/graph_nexus_analysis.py`:
  - `_resolve_role_llm_provider_config`: add a `provider == "codex-cli"` branch (mirrors the claude-cli branch at L832-853) that pulls `cli_path` / `extra_args` / `reasoning_effort` from the role-prefixed config keys.
- `backend/model_resolver.py`:
  - `resolve_model_refs_in_config`: extend the `provider == "claude-cli"` block at L114-122 to also handle `"codex-cli"` (same `cli_path`/`extra_args` injection).
- `backend/api/main.py`:
  - `/llm/test` endpoint: route to codex-cli auth probe + smoke prompt.
  - Pydantic `CreateModelBody` already allows `cli_path` / `extra_args` — no schema change needed.
- `backend/interactive_utils.py`:
  - `_validate_provider_model_compat`: accept `"codex-cli"` (currently checks claude-cli at L7937).
  - `action_create_model` / `action_edit_model`: codex-cli stores `cli_path` and `extra_args` like claude-cli (no api_key); no schema change.
  - `_restore_inline_from_model` (L8079): add codex-cli branch alongside claude-cli at L8100-8104.
  - `_validate_claude_cli_extra_args`: rename to `_validate_cli_extra_args` and parameterize on provider, OR add a sibling `_validate_codex_cli_extra_args`. The allowlist for codex needs investigation — likely a different set of safe flags than claude.

### 3.6 Frontend (`frontend/src/views/ModelsView.vue`)

- Provider dropdown: add `"codex-cli"` option.
- Form: when provider is `codex-cli`, show only `cli_path` (default `"codex"`) and `extra_args` (free-text, allowlist-validated server-side) — no API key, no base URL, no Azure fields.
- Existing `isCli` flag at L174 already gates Test & Save behavior. Generalize: rename `isCli` → `isLocalCli` and treat both `claude-cli` and `codex-cli` as local-CLI providers.
- "Current provider:" label in the modal: add a `codex-cli` → "Codex CLI" mapping.
- Pricing override section: codex-cli usage is metered by ChatGPT subscription (no per-token cost). Hide pricing fields when provider is codex-cli (mirrors how claude-cli currently doesn't surface pricing, since LLM telemetry's pricing computation for `claude-cli` is already a no-op via the `_unknown_` fallback in `llm_pricing.yaml`).

---

## 4. Files touched (summary)

| File | Change |
|------|--------|
| `backend/chatbot/codex_cli_provider.py` | **NEW** — ~700 lines, sibling of `claude_cli_provider.py` |
| `backend/llm_utils.py` | dispatch branches in `call_llm_by_provider`, `call_structured_llm_by_provider`, `_default_api_key_for_provider`, `_default_model_for_provider`, `_normalize_llm_provider` |
| `backend/strategies/graph_nexus_analysis.py` | `_resolve_role_llm_provider_config` branch (~30 lines) |
| `backend/model_resolver.py` | extend the `claude-cli` block to also handle `codex-cli` (~10 lines) |
| `backend/api/main.py` | `/llm/test` codex-cli branch (~30 lines) |
| `backend/interactive_utils.py` | `_validate_provider_model_compat`, `_restore_inline_from_model`, new `_validate_codex_cli_extra_args` (~50 lines) |
| `frontend/src/views/ModelsView.vue` | provider dropdown + form gating (~40 lines) |
| `backend/llm_pricing.yaml` | new `codex-cli` row mapping to `_unknown_` (no per-token cost) |
| `backend/tests/test_codex_cli_provider.py` | **NEW** — unit tests for spawn, init, structured parse, auth probe, idle TTL |
| `backend/tests/test_strategy_codex_cli_dispatch.py` | **NEW** — integration tests for the strategy dispatch path (mock subprocess) |

Estimated total: ~1000 LOC new + ~150 LOC modifications.

---

## 5. Test plan

### Unit (`test_codex_cli_provider.py`)

- `test_codex_cli_not_installed_raises` — patch `_resolve_cli_path` to return None.
- `test_codex_cli_not_authenticated_raises` — patch `subprocess.run` to return exit code 1 with `not authenticated` stderr.
- `test_codex_cli_session_initialize_handshake` — fake subprocess that responds to `initialize` and `thread/start`. Assert correct JSON-RPC envelopes sent over stdin.
- `test_codex_cli_structured_prompted_json_path` — fake subprocess returns `{ok: true, ...}` over JSON-RPC notifications; assert Pydantic model parsed correctly.
- `test_codex_cli_structured_retry_on_invalid_json` — first attempt returns malformed JSON, second attempt returns valid; assert success after retry.
- `test_codex_cli_chat_idle_ttl_eviction` — set TTL to 0.1s, sleep, assert session closed.
- `test_codex_cli_concurrent_spawn_cap` — set `CODEX_CLI_MAX_CONCURRENT=2`, attempt 3 spawns, assert third blocks.
- `test_codex_cli_oauth_failure_classification` — patch stderr to contain `invalid_grant`; assert `_classify_oauth_failure` returns the right hint.

### Integration (`test_strategy_codex_cli_dispatch.py`)

- `test_strategy_dispatches_to_codex_cli_when_provider_codex_cli` — config with `sentiment_llm_provider="codex-cli"`, mock `call_codex_cli_structured`, assert called with right args.
- `test_default_api_key_for_codex_cli_returns_sentinel` — mirrors `test_graph_nexus_default_api_key_returns_sentinel_for_claude_cli`.
- `test_default_model_for_codex_cli` — mirrors the claude-cli default-model test.
- `test_resolve_role_llm_provider_config_codex_cli` — assert returned dict has `cli_path` / `extra_args` / `reasoning_effort` and NOT `azure_endpoint` / `base_url`.

### Manual

- Install codex CLI on host, run `codex login`, add a Codex CLI model in `/models`, hit "Test & Save" — modal should show the smoke response text.
- Switch a strategy's sentiment role to the new model_id, run a 1-day backtest, verify `LLM/enhanced_sentiment: provider=codex-cli ok=True` in the log.
- Manually invalidate Codex auth (`codex logout`) mid-run, verify the strategy falls back to company-article classifications without crashing AND the next `/llm/test` from the UI surfaces "codex CLI not authenticated".

---

## 6. Risks and mitigations

1. **Codex's JSON-RPC notification stream is verbose** — `output_text.delta` events for token streaming, `reasoning.delta` for chain-of-thought, etc. Our structured path needs to consume them in order and assemble the final text without buffering all events forever. **Mitigation:** stream-and-discard non-text events, accumulate `output_text.delta` payloads only until `response.completed`.

2. **`codex` binary version drift** — the app-server protocol may change between Codex CLI releases. **Mitigation:** record the negotiated protocol version from the `initialize` response and refuse to proceed if it's outside our tested range (e.g. `2025.x`). Log the version on each spawn.

3. **Token expiration mid-bar** — strategy makes 4 parallel event_maintenance calls; if Codex tokens expire after call 1, calls 2-4 may variously hang or 401. **Mitigation:** the auth probe cache (60s TTL) catches the expiration on the next probe; in-flight calls fail with `CodexCliNotAuthenticatedError`, the structured caller treats this as a transient error and falls through to the company-article fallback.

4. **No native rate limit** — unlike NVIDIA, Codex relies on OpenAI's account quotas. We probably don't need an RPM throttle. **Mitigation:** add the `_PROVIDER_MODEL_REQUEST_RATE_LIMITERS` slot but leave it unset by default. Operator can add a `(codex-cli, gpt-5-codex)` entry later if they observe limits.

5. **Process leakage** — if the API server crashes with codex sessions open, the orphaned subprocesses can stick around. **Mitigation:** copy the claude-cli sweeper thread + `atexit.register(_shutdown_all_sessions)` pattern.

6. **Extra-args allowlist drift** — claude-cli's allowlist (`_validate_claude_cli_extra_args`) protects against shell-escape into RCE. Codex has a different set of safe flags (e.g. `--sandbox`, `--model`, `--config`). **Mitigation:** new `_validate_codex_cli_extra_args` with a conservative initial allowlist; reject everything else; loosen via PR as needs emerge.

---

## 7. Out of scope

- No support for Codex's tool-call / function-call APIs (we send plain prompts; the structured path is prompted-JSON only).
- No support for Codex's MCP server integration (intentionally pure-text mode, same security stance as claude-cli).
- No support for Codex's image/file inputs (text-only).
- No automatic `codex login` flow inside IntelliStock — operator runs it once in their shell. (Codex's login is an OAuth browser dance; embedding it in our web UI is out of scope for v1.)

---

## 8. Open questions for operator

1. Should the codex-cli provider be available for the **chatbot** (`backend/chatbot/orchestration.py`) on day 1, or only the strategy structured-output path? Chatbot adds a bit more code (conversation_id management) but maps 1:1 to the claude-cli implementation.
2. Default `cli_path`: just `"codex"` (PATH lookup), or a stricter absolute path requirement to prevent typo-mounting a malicious binary?
3. Should the `/llm/test` smoke prompt for codex be the same generic "name one macro driver" prompt, or something codex-specific (e.g. "Write a one-line Python comment explaining what a P/E ratio is.") so the operator can sanity-check the model actually responds in character?
4. Do we want a `codex_cli_provider_default_reasoning_effort` config knob, mirroring the claude-cli default-effort pattern, or rely on the per-model `reasoning_effort` field already on `Models` rows?

---

## 10. Web-UI driven install flow

**Goal:** Operator never opens a terminal for Codex.

### Backend endpoints (new, in `backend/api/main.py`)

```
GET  /codex/status           → { installed: bool, version: str | null, authenticated: bool, install_method: "npm"|"brew"|"unknown", cli_path: str | null }
POST /codex/install          → { job_id: str }       ; spawns install subprocess, returns immediately
GET  /codex/install/{job_id} → { state: "running"|"success"|"failed", log_tail: [str], exit_code: int|null }
```

### Detection logic (`/codex/status`)

1. `_resolve_cli_path("codex")` — same helper claude-cli already uses; returns absolute path or None.
2. If installed, exec `codex --version` (5s timeout) to confirm it runs (catches PATH-shadow + bitrot).
3. `is_authenticated()` from §3.4 (cheap, 60s cached).
4. Determine `install_method`:
   - If macOS + `brew --prefix codex` succeeds → "brew".
   - Else if `npm` is on PATH → "npm".
   - Else "unknown" (UI shows a docs link instead of an Install button).

### Install subprocess (`/codex/install`)

Spawns the matching command and streams stdout/stderr to a ring buffer indexed by `job_id`:

| Platform | Command |
|---|---|
| npm (default) | `npm install -g @openai/codex` |
| brew | `brew install codex` |

Security:
- The install endpoint is admin-only (same gate as `/models` PUT).
- `job_id` is a UUID; tail buffer is in-memory only, capped at 200 lines / 30 min TTL.
- The install subprocess inherits ONLY a sanitized env (`PATH`, `HOME`, `npm_config_*` if applicable) — no leakage of strategy API keys.
- Reject if the resolved npm/brew binary is in a non-system path (covers a user with a tampered shell PATH).
- Cap at one in-flight install per host (semaphore).

### Frontend (`ModelsView.vue`)

When operator selects provider `codex-cli`:

```
┌─────────────────────────────────────────┐
│ Codex CLI Setup                         │
│                                         │
│  ✗ codex not installed                  │
│   Detected install method: npm          │
│   [ Install codex ]                     │
│                                         │
│  (live log tail, last 5 lines)          │
└─────────────────────────────────────────┘
```

After install succeeds, the same panel updates to:

```
┌─────────────────────────────────────────┐
│ Codex CLI Setup                         │
│                                         │
│  ✓ codex 2026.x installed                │
│  ✗ Not authenticated                    │
│   [ Sign in to OpenAI ]                 │
└─────────────────────────────────────────┘
```

Clicking "Sign in" opens the device-code panel (§11).

When both checks are green, the existing model form (cli_path / extra_args / reasoning_effort) appears under the setup panel and Save becomes available.

---

## 11. Web-UI driven device-code login

### How Codex authenticates

Codex CLI uses OAuth against ChatGPT. The CLI command `codex login` does one of two things depending on the environment:

1. **TTY mode (`codex login` in a terminal):** opens a browser and runs a localhost callback server.
2. **Headless mode (`codex login --headless` or `--no-browser`, depending on the version):** prints a pairing URL + device code to stdout, waits for the operator to complete it in their own browser, then exits 0.

We use mode 2. The exact flag name needs verification at implementation time — the IntelliStock-side wrapper should:
- Spawn `codex login` with stdin closed and `CODEX_NO_BROWSER=1` env (or the equivalent flag for the installed Codex version).
- Read stdout line-by-line until we see a pairing URL (`https://chatgpt.com/codex/...` or `https://platform.openai.com/...`) and a code.
- Surface both to the frontend immediately.
- Wait for the subprocess to exit (or timeout at e.g. 10 min) — exit code 0 means success, non-zero with stderr hint means failure.

### Backend endpoints

```
POST /codex/login/start              → { job_id: str, pairing_url: str | null, pairing_code: str | null }
GET  /codex/login/{job_id}/status    → { state: "pending"|"success"|"failed"|"expired",
                                          pairing_url: str | null, pairing_code: str | null,
                                          authenticated: bool, error: str | null }
POST /codex/login/{job_id}/cancel    → { cancelled: bool }
POST /codex/logout                   → { ok: bool }    ; spawns `codex logout`, clears auth
```

### Implementation sketch (new helper in `backend/chatbot/codex_cli_provider.py`)

```python
class CodexDeviceCodeLogin:
    """Drives `codex login --no-browser` from the web UI.

    Lifecycle:
      start() -> spawns subprocess, returns once pairing_url + code parsed
                 from stdout (10s timeout for the parse).
      status() -> returns current state; transitions on subprocess exit.
      cancel() -> terminates subprocess.
    """
    def __init__(self, cli_path: str = "codex"):
        self._cli_path = cli_path
        self._proc: subprocess.Popen | None = None
        self._pairing_url: str | None = None
        self._pairing_code: str | None = None
        self._state: str = "pending"
        self._error: str | None = None
        self._stdout_thread: threading.Thread | None = None
        self._stderr_buf: list[str] = []
        self._lock = threading.Lock()

    def start(self, *, timeout_sec: int = 600) -> None: ...
    def status(self) -> dict: ...
    def cancel(self) -> None: ...
```

stdout-parse heuristic (regex against known Codex pairing-line shapes; brittle, but the CLI's output is stable enough across releases that we can keep up):

```
PAIRING_URL_RE  = re.compile(r"https?://[^\s]+(?:codex|openai)[^\s]*", re.I)
PAIRING_CODE_RE = re.compile(r"\b([A-Z0-9]{4}-[A-Z0-9]{4})\b")
```

If we don't see both within 10s of spawn, surface an error to the frontend with the stdout/stderr tail so the operator can see what Codex actually printed.

### Frontend device-code panel

```
┌─────────────────────────────────────────┐
│ Sign in to OpenAI for Codex             │
│                                         │
│   Open this URL in your browser:        │
│   https://chatgpt.com/codex/device      │  [ Copy URL ]
│                                         │
│   Enter this code:                      │
│         X8K2-9PNQ                       │  [ Copy code ]
│                                         │
│   ⠋ Waiting for you to complete sign-in │
│                                         │
│   [ Cancel ]                            │
└─────────────────────────────────────────┘
```

- Polls `/codex/login/{job_id}/status` every 2s.
- On success: replace panel with green "✓ Authenticated" check and reveal the rest of the model form.
- On failure: show the stderr hint (e.g. "Expired code, click Cancel and try again").
- On user cancel: send `POST /codex/login/{job_id}/cancel`, close the panel.

### Security considerations

- **Endpoints are admin-only.** Same role gate as `/models`.
- **The pairing URL + code are NOT secrets** (they're a one-time challenge); safe to render directly.
- **The login subprocess writes tokens to `~/.codex/auth.json`** on the server host (Codex's default). This means the auth lives on the API host — multi-host deployments need a shared filesystem mount for `~/.codex/`, OR each host onboards independently.
- **No tokens transit our API.** The browser does its OAuth dance directly with ChatGPT; Codex's subprocess receives the tokens via the OAuth callback that ChatGPT's site triggers.
- **`/codex/logout` invalidates tokens** so a stale session can't be reused after the operator intentionally signs out.

### Failure modes

1. **Codex version doesn't support `--no-browser` / headless mode.** Detect via `codex login --help`; if the flag is absent, fall back to a "Open a terminal on the API host and run `codex login`" inline note in the UI, with a refresh button that re-checks `/codex/status`.
2. **No `~/.codex/` write permission.** Surface the stderr hint and instruct operator to chmod / re-deploy.
3. **Firewall blocks `chatgpt.com`.** Operator sees a "code expired" failure; we relay Codex's error verbatim.
4. **API host is remote (Tailscale).** OAuth flow happens in the operator's browser, not the API host — works fine. The token ends up on the API host because that's where `codex login` runs.

---

## 12. Files touched (v2 amendment)

In addition to §4:

| File | Change |
|------|--------|
| `backend/api/main.py` | `/codex/status`, `/codex/install`, `/codex/install/{job_id}`, `/codex/login/start`, `/codex/login/{job_id}/status`, `/codex/login/{job_id}/cancel`, `/codex/logout` (~250 lines) |
| `backend/chatbot/codex_cli_provider.py` | `CodexDeviceCodeLogin`, `CodexInstaller` classes (~300 lines on top of §3.1's ~700) |
| `frontend/src/views/ModelsView.vue` | Setup panel + device-code panel + polling logic (~150 lines on top of §3.6's ~40) |
| `frontend/src/components/codex/CodexSetupPanel.vue` | **NEW** — extracts the install/login UI for reuse (could later move into an onboarding flow) |
| `frontend/src/components/codex/CodexDeviceCodePanel.vue` | **NEW** — pairing-code display + polling |
| `backend/tests/test_codex_install.py` | **NEW** — install endpoint smoke (mocked subprocess) |
| `backend/tests/test_codex_device_code_login.py` | **NEW** — stdout parsing for pairing URL/code, timeout, cancel |

Updated total: ~2000 LOC new + ~150 LOC modifications. Roughly **2x the v1 effort** — most of the new code is the install + login UX (which is the only part the operator actually sees).

---

## 13. Rollout sequencing (revised)

1. **Phase A** — backend protocol layer + claude-cli-style strategy dispatch (§3, no UI work). Test with manual `codex login` from a shell. **~1 day.**
2. **Phase B** — `/codex/status` + `/codex/install` + install panel in `ModelsView.vue`. Operator can install codex from the UI. **~0.5 day.**
3. **Phase C** — device-code login (`/codex/login/*` + device-code panel). Operator can sign in from the UI. **~1 day.**
4. **Phase D** — adversarial review + adoption polish (logout button, "re-sign-in" affordance when tokens expire mid-strategy, telemetry). **~0.5 day.**

Total: **~3 days of focused work** for the full end-to-end web-UI experience.

---

## 9. (Original v1 rollout — superseded by §13)

1. Land the new files + dispatch branches behind a feature flag (`codex_cli_provider_enabled`, default False) — strategy and `/llm/test` skip codex-cli entirely when False, returning a 400 "provider not enabled".
2. Enable the flag in dev, add a model row via `/models`, run the full test suite + a 1-day backtest end-to-end.
3. Adversarial review (parallel subagents) — the Phase η bug-sweep pattern caught the Azure-key shadow bug; same pattern for codex would catch eg. token-leak in stderr logs, allowlist gaps, race conditions in the session manager.
4. Flip the flag to True; document in `CLAUDE.md` as a supported provider alongside `claude-cli`.

Implementation effort estimate: **6-10 hours of focused work** for the core port (one tight working day) plus 1-2 hours for adversarial review + tests. Most of the savings come from claude-cli having already paved the operational concerns (sweeper, semaphore, telemetry, sentinel api_key, frontend gating). The new code is primarily the JSON-RPC protocol layer.
