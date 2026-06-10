# Claude Code CLI as an IntelliStock Model Provider — Design

**Status:** Draft for review
**Date:** 2026-05-12
**Author:** Pranav Krishna (designed collaboratively with Claude)

## Summary

Add Claude Code (CC) as a selectable model provider in IntelliStock. The provider invokes the locally-installed `claude` CLI to run prompts against the operator's logged-in CC subscription, avoiding the need for an Anthropic API key. CC is used in **text-only mode** (no tools, no MCP, no filesystem/shell access). The provider works in both the **chatbot** (multi-turn, persistent subprocess per conversation) and **strategies** (single-shot structured-output calls).

## Goals

1. Let a personal/self-hosted IntelliStock instance use the operator's Claude Code subscription as if it were any other LLM provider.
2. Make CC selectable on the existing model dropdowns (`StepAddModel`, `ModelsView`, `ChatModelPicker`) with zero changes to the existing provider abstraction's external shape.
3. Keep chatbot latency competitive — eliminate the ~2.7-3.7 s subprocess startup tax that would otherwise hit every turn.
4. Support both chatbot turns (multi-turn, stateful) and strategy structured-output calls (single-shot, schema-validated).
5. Safe-by-default: no tool execution, no MCP, no filesystem/shell exposure to the model.

## Non-Goals

- MCP bridge exposing IntelliStock chatbot tools (`fetch_quote`, `query_graph`, etc.) to CC. Deferred to a separate "Nexus Analyst" project.
- Streaming token deltas to the UI. The current chatbot is non-streaming for all providers; matching that.
- Multi-user isolated CC accounts (each IntelliStock user with their own `~/.claude/` login). Self-hosted single-operator scope.
- CC's native tools (Read, Bash, WebFetch, Edit). Explicit security non-goal — would require sandboxing.
- Per-call cost-tracking dashboard. CC reports `total_cost_usd` per call but surfacing it is its own feature.
- Auto-updating the `claude` binary. Operator responsibility.

## Background

IntelliStock's model abstraction (`backend/chatbot/llm.py:314-361`, `backend/llm_utils.py`) currently normalizes OpenAI, Azure, DeepSeek, NVIDIA, and Gemini behind a single `{content, tool_calls, finish_reason, raw}` shape. Models are stored in RethinkDB (`interactive_utils.py:7815-7900`) and selected via three Vue components.

Anthropic's Claude API requires an API key. For users who pay for Claude Code Pro/Max but don't want a separate API account, CC can be driven via subprocess to get the same model access. JarvisClaw (`Th3-H4xx0r/JarvisClaw`, MIT) demonstrates a working pattern — long-lived `claude` subprocess per conversation with NDJSON over stdin/stdout. We adapt that pattern, but disable all tools and MCP for safety.

**Measured latency reality:** spawn-per-call costs ~2.7-3.7 s of pure subprocess overhead on a Windows dev machine, even with all reduction flags (`--no-session-persistence --strict-mcp-config --disable-slash-commands`). This is unacceptable as a per-turn tax on chatbot interactions. A persistent subprocess per conversation pays this cost once and amortizes it over the conversation lifetime.

## Design Decisions (confirmed during brainstorming)

| Decision | Choice | Rationale |
|---|---|---|
| Deployment scope | Personal/self-hosted | Single operator; no per-user CC auth isolation needed |
| Tool model | Pure text-only, no MCP, no CC tools | Simplicity + security; MCP can be added later |
| Surfaces | Both chatbot and all strategies | Uniform UX; CC selectable anywhere a model is |
| Auth setup | Docs + "Test connection" button | Operator runs `claude` on the server once; UI verifies |
| Process model | Hybrid: persistent per chatbot conversation, spawn-per-call for strategies | Latency where it matters; simplicity where it doesn't |
| Output validation | CC's native `--json-schema` flag + Pydantic re-validate | Defense in depth; CC enforces server-side, we validate on receipt |

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│ Frontend (Vue 3)                                              │
│   StepAddModel.vue | ModelsView.vue | LlmConfigForm.vue       │
│   ChatModelPicker.vue                                         │
│   - new provider option "claude-cli"                          │
│   - conditional fields: cli_path, extra_args                  │
│   - Test Connection button                                    │
└──────────────────────────────────────────────────────────────┘
                          │ HTTPS
┌──────────────────────────────────────────────────────────────┐
│ Backend (FastAPI)                                             │
│                                                               │
│   api/main.py                                                 │
│     POST /api/models/{id}/test-cli  ← new endpoint            │
│                                                               │
│   chatbot/llm.py::call_chat_with_tools                        │
│     if provider == "claude-cli":                              │
│       → claude_cli_provider.call_claude_cli_chat(...)         │
│                                                               │
│   llm_utils.py::call_structured_llm_by_provider               │
│     if provider == "claude-cli":                              │
│       → claude_cli_provider.call_claude_cli_structured(...)   │
│                                                               │
│   chatbot/claude_cli_provider.py  ← NEW MODULE                │
│     ClaudeCliSessionManager  (async, persistent per conv)     │
│     call_claude_cli_chat()   (async, chatbot)                 │
│     call_claude_cli_structured()  (sync, strategies)          │
│     test_claude_cli()         (sync, connection test)         │
│                                                               │
└──────────────────────────────────────────────────────────────┘
                          │ subprocess (NDJSON via stdin/stdout)
                ┌─────────┴─────────┐
                │ claude CLI         │
                │ (Node.js process)  │
                │ ~/.claude/ auth    │
                └────────────────────┘
```

## Components

### `backend/chatbot/claude_cli_provider.py` (NEW, ~350 lines)

```python
class ClaudeCliSession:
    conversation_id: str
    model: str                              # e.g. "claude-sonnet-4-6"
    cli_path: str
    process: asyncio.subprocess.Process
    state: SessionState                     # Idle | Awaiting | Crashed | Closed
    last_activity: float
    send_lock: asyncio.Lock                 # serializes turns within conv
    messages_sent: int                      # how many history msgs pushed
    reader_task: asyncio.Task               # stdout NDJSON parser
    pending: asyncio.Future | None          # current turn collector
    accumulated_text: list[str]             # assistant content buffer

class ClaudeCliSessionManager:
    sessions: dict[str, ClaudeCliSession]
    sessions_lock: asyncio.Lock
    idle_ttl_sec: int = 3600
    sweeper_task: asyncio.Task

    async def send_turn(
        self, *, conv_id, messages, sys_prompt, model, cli_path, extra_args
    ) -> dict: ...
    async def get_or_spawn(self, ...) -> ClaudeCliSession: ...
    async def close(self, conv_id) -> None: ...
    async def shutdown_all(self) -> None: ...

async def call_claude_cli_chat(
    *, conversation_id, messages, system_prompt, model, cli_path, extra_args,
    timeout: int = 120,
) -> dict:
    """Returns {content, tool_calls=[], finish_reason, raw}."""

def call_claude_cli_structured(
    *, model, system_prompt, user_prompt, output_schema, cli_path,
    extra_args, timeout: int = 120,
) -> BaseModel:
    """Spawn-per-call. CC's --json-schema enforces server-side."""

def test_claude_cli(*, cli_path, model) -> dict:
    """Returns {ok, version, logged_in, model_response, error, elapsed_ms}."""

def validate_extra_args(extra_args_str: str) -> list[str]:
    """Parses + whitelist-validates. Raises ValueError on disallowed args."""
```

### Spawn commands

**Chatbot (persistent, stream-json):**
```
claude -p --verbose \
  --input-format stream-json --output-format stream-json \
  --model <model> --system-prompt <sys> \
  --tools "" --strict-mcp-config \
  --no-session-persistence --disable-slash-commands \
  [+ whitelisted extra_args]
```

**Strategies (one-shot, json):**
```
claude -p \
  --output-format json --model <model> \
  --system-prompt <sys> \
  --json-schema <pydantic-derived-schema> \
  --tools "" --strict-mcp-config \
  --no-session-persistence --disable-slash-commands \
  [+ whitelisted extra_args]
```

User prompt goes on stdin in both cases (avoids 8 KB Windows command-line limit).

### `backend/chatbot/llm.py` (EDIT, ~30 lines added)

Add a `claude-cli` branch in `call_chat_with_tools`. Must thread `conversation_id` through from the caller (chatbot orchestration already has it). Other providers ignore the new arg.

### `backend/llm_utils.py` (EDIT, ~40 lines added)

Add a `claude-cli` branch in `call_structured_llm_by_provider`. Bypasses PydanticAI (which has no CC backend) and calls `call_claude_cli_structured` directly.

### `backend/chatbot/orchestration.py` (EDIT, ~5 lines)

Pass `conversation_id` into `call_chat_with_tools`. The conversation_id is already available in this scope.

### `backend/api/main.py` (EDIT, ~50 lines added)

```python
@app.post("/api/models/{model_id}/test-cli")
async def test_claude_cli_endpoint(model_id: str, user = Depends(...)):
    doc = await get_model(model_id)
    assert doc["provider"] == "claude-cli"
    return await asyncio.to_thread(
        test_claude_cli, cli_path=doc["cli_path"], model=doc["model"]
    )
```

### `backend/interactive_utils.py` (EDIT, ~30 lines added)

`action_create_model` and `action_update_model` validate `extra_args` against the whitelist (see "extra_args whitelist" below). Validation lives in `claude_cli_provider.validate_extra_args` and is called from both action handlers.

### `backend/model_resolver.py` (EDIT, ~5 lines)

Short-circuit for `claude-cli`: return doc unchanged (no api_key resolution).

### Frontend (EDIT, ~120 lines across 3 files)

- `frontend/src/components/onboarding/StepAddModel.vue` — add `{value: "claude-cli", label: "Claude Code CLI (server subscription)"}` to provider options
- `frontend/src/views/ModelsView.vue` — same
- `frontend/src/components/LlmConfigForm.vue` — conditional rendering:
  - When `provider === "claude-cli"`:
    - Hide: `api_key`, `openai_base_url`, `nvidia_base_url`, `azure_openai_*`
    - Show: `cli_path` (placeholder `claude`), `extra_args` (placeholder `--fallback-model claude-haiku-4-5`)
    - Show help callout: *"This provider uses Claude Code on the server. SSH to the server and run `claude` to log in first. Tools are disabled — CC is used as a text-only LLM."*
    - Model dropdown: `claude-sonnet-4-6`, `claude-opus-4-7`, `claude-haiku-4-5`, plus a free-text "custom" option
    - "Test connection" button — calls `POST /api/models/{id}/test-cli`, shows spinner → ✓/✗ with response details

## Data Model

**RethinkDB `Models` table — two new optional columns** (no migration needed; RethinkDB is schemaless):

| Column | Type | Default | Notes |
|---|---|---|---|
| `cli_path` | string | `"claude"` | Override only if not on PATH |
| `extra_args` | string | `""` | Free-text; `shlex.split` + whitelist validated |

For `provider == "claude-cli"`: `api_key`, `openai_base_url`, `nvidia_base_url`, `azure_*` are all unused / null. `model` carries the variant.

## Data Flow

### Chatbot turn (persistent process)

1. User sends message in conversation `conv_123` via the frontend
2. `backend/chatbot/orchestration.py` builds the full messages list and calls `call_chat_with_tools(messages, tools, model_id, conversation_id=conv_123, ...)`
3. `call_chat_with_tools` resolves the model doc, sees `provider == "claude-cli"`, and calls `call_claude_cli_chat(conversation_id=conv_123, messages, ...)`
4. `ClaudeCliSessionManager.send_turn`:
   1. `get_or_spawn(conv_123)`: if a warm session exists with matching model+sys_prompt, reuse it. Otherwise close any mismatched session, spawn a new subprocess, start the reader task, and wait for the `system:init` event.
   2. `async with session.send_lock:`
   3. Write `messages[session.messages_sent:]` to stdin as NDJSON `user` events (typically just the latest user turn). Update `messages_sent`.
   4. `session.pending = Future()`; `session.state = Awaiting`
   5. `await asyncio.wait_for(session.pending, timeout=120)` — reader task fulfills it on `result` event
   6. `session.state = Idle`; `session.last_activity = now()`
5. Return `{content: <assistant text>, tool_calls: [], finish_reason: "stop", raw: <full result event>}`
6. Chatbot orchestration treats this like any other provider response (tools never present)

### Strategy call (spawn-per-call)

1. Strategy (e.g. `graph_nexus_analysis`) calls `call_structured_llm_by_provider(provider="claude-cli", model, prompt, schema, ...)`
2. Branch in `llm_utils.py` calls `call_claude_cli_structured(...)`
3. Convert Pydantic schema: `output_schema.model_json_schema()` → `json.dumps`
4. `subprocess.run([claude, -p, --output-format, json, --model, M, --system-prompt, S, --json-schema, SCHEMA, --tools, "", --strict-mcp-config, --no-session-persistence, --disable-slash-commands, ...extra], input=user_prompt, capture_output=True, text=True, timeout=120)`
5. Parse `proc.stdout` as JSON. If `is_error`, classify (NotLoggedIn / RateLimit / Generic) and raise.
6. `output_schema.model_validate(json.loads(result.result))` for defense-in-depth
7. Return validated Pydantic instance
8. Existing retry/backoff logic in `call_structured_llm_by_provider` handles transient failures

## Concurrency Model

- **Per-conversation:** one CC subprocess; `send_lock` serializes turns. Two near-simultaneous sends on the same conversation queue.
- **Across conversations:** independent subprocesses, fully parallel.
- **Strategies:** each `call_claude_cli_structured` spawns its own subprocess. `graph_nexus_analysis` runs 4-6 concurrent workers via `ThreadPoolExecutor`.
- **Global cap:** `CLAUDE_CLI_MAX_CONCURRENT` (default 10) — asyncio.Semaphore for chatbot + threading.Semaphore for strategies, combined via a shared counter. Prevents runaway subprocess spawning.

## Error Handling

| Exception | Trigger | UI / caller behavior |
|---|---|---|
| `ClaudeCliNotInstalledError` | `FileNotFoundError` on spawn | "Install: `npm i -g @anthropic-ai/claude-code`" |
| `ClaudeCliNotLoggedInError` | `result.result == "Not logged in · Please run /login"` | "SSH to server and run `claude` to log in." **No retry.** |
| `ClaudeCliRateLimitError` | parsed from `is_error` result | "Subscription quota exceeded — retry shortly." Retry with longer backoff (60 s, 180 s). |
| `ClaudeCliTimeoutError` | per-call timeout | Retry once, then fail |
| `ClaudeCliCrashError` | subprocess EOF or non-zero exit | Internal — session manager respawns + replays history transparently |
| `ClaudeCliValidationError` | Pydantic validation fails after CC `--json-schema` check | Retry once with hint, then fail |
| `ClaudeCliError` | generic fallback | Surface raw error |

**Crash recovery details:** On `Crashed` state, `get_or_spawn` kills the dead process, spawns a fresh one, resets `messages_sent = 0`, and lets the next `send_turn` re-push the full conversation history. User sees one slow turn (~3 s spawn) but no error.

**Idle eviction:** sweeper task ticks every 5 min. Sessions with `now() - last_activity > idle_ttl_sec` (default 3600 s) are closed: `stdin.close()`, 5 s wait, then `process.kill()`. Snapshot the session dict before iterating to avoid holding a lock across awaits (JarvisClaw bug reference).

**Server shutdown:** FastAPI lifespan event calls `manager.shutdown_all()` to gracefully close all subprocesses. atexit handler as belt-and-suspenders.

**Process orphan prevention:** spawn with `start_new_session=False` on Unix and `CREATE_NEW_PROCESS_GROUP` on Windows so kill signals propagate. Branch in one helper on `sys.platform`.

## Security Model

CC is a Node.js process running as the IntelliStock server user. It has the server user's filesystem and network access. Pure-text mode disables CC's user-facing tools but does not sandbox the process. Defenses:

1. **`--tools ""` + `--strict-mcp-config`** hard-coded in spawn args (NOT user-configurable). Disables Read, Edit, Bash, WebFetch, and all MCP servers.
2. **`--system-prompt` is set by IntelliStock**, not the user — prompt injection from chat input goes through `--input-format stream-json` user turns, the same threat surface every LLM has.
3. **`extra_args` whitelist** (below) — prevents users re-enabling tools/MCP through the config field.
4. **No `--add-dir`** — CC has no extra filesystem context beyond `cwd`.
5. **No `--dangerously-skip-permissions`** — moot since no tools enabled, but explicit.

**Threat model accepted:** worst case is the model returns adversarial *text* in a chatbot response. We're not exposing CC's host-controlling capabilities. Blast radius is identical to any other LLM provider.

### `extra_args` whitelist

**Allowed** (in `claude_cli_provider.validate_extra_args`):
- `--fallback-model <model>`
- `--effort <low|medium|high|xhigh|max>`
- `--append-system-prompt <text>`
- `--max-budget-usd <amount>`

**Hard-rejected** (raises `ValueError` → 400 from API):
- `--tools`, `--allowed-tools`, `--allowedTools`, `--disallowed-tools`, `--disallowedTools`
- `--dangerously-skip-permissions`, `--allow-dangerously-skip-permissions`
- `--permission-mode`
- `--mcp-config`
- `--add-dir`
- `--system-prompt` (already set by us)
- `--print`, `-p` (already set)
- `--input-format`, `--output-format` (already set)
- Unknown flags

Parsing: `shlex.split`, iterate as key/value pairs, reject if key not in allowlist.

## Settings

New keys in the existing settings module (env-overridable):

| Key | Default | Purpose |
|---|---|---|
| `CLAUDE_CLI_MAX_CONCURRENT` | 10 | Global subprocess cap |
| `CLAUDE_CLI_IDLE_TTL_SEC` | 3600 | Chatbot session eviction TTL |
| `CLAUDE_CLI_SPAWN_TIMEOUT_SEC` | 30 | Abort spawn if it takes longer |
| `CLAUDE_CLI_TURN_TIMEOUT_SEC` | 120 | Per-call timeout |
| `CLAUDE_CLI_SWEEPER_INTERVAL_SEC` | 300 | Idle sweeper tick rate |

## Observability

Use the existing `intellistock_logger.intellistock_logger.log(msg, color, service="ClaudeCli")` pattern. Log levels:

- **DEBUG:** spawn args (with sensitive bits redacted), turn start/end, NDJSON event types received
- **INFO:** idle eviction, model switch, graceful shutdown, version detected on spawn
- **WARNING:** stderr output from CC, retries, transient errors
- **ERROR:** spawn failures, timeouts that exhaust retries, validation failures

Future (not in this design): a metrics surface for active session count, spawn count, crash count, avg turn latency.

## Testing

### Unit tests — `backend/tests/test_claude_cli_provider.py`
Mock `asyncio.subprocess` and `subprocess.run`. Cases:
- Session lifecycle (spawn → send → result → state transitions)
- `send_lock` serializes concurrent sends within one conversation
- Crash detection (stdout EOF → Crashed) + recovery (next call respawns + replays)
- Idle sweeper closes sessions past TTL
- Model switch closes old session, spawns new
- `messages_sent` counter sends only diffs on subsequent turns
- `extra_args` whitelist rejects every banned flag
- "Not logged in" detection raises immediately (no retry)
- Sync structured call validates Pydantic, handles `is_error`, retries on validation failure

### Integration tests — `backend/tests/test_claude_cli_integration.py`
Marked skip unless `INTELLISTOCK_TEST_CLAUDE_CLI=1`. Real subprocess. Cases:
- `claude --version` succeeds, returns parseable version
- One-shot text call returns expected content
- Multi-turn conversation preserves context across turns
- Structured call with `--json-schema` returns Pydantic-validatable instance
- Crash recovery: kill subprocess externally, verify next call respawns transparently

### Regression
Existing chatbot, scheduler, strategies, and nexus tests must continue to pass.

### Manual smoke test plan (pre-merge)
1. `claude --version` works; `claude` login current on dev machine
2. Start backend + frontend
3. Add model with `provider=claude-cli`, `model=claude-sonnet-4-6` → "Test connection" returns ✓
4. Chatbot send "Hello" → ~6 s response (3 s spawn + 3 s API)
5. Chatbot send "What was my last message?" → ~3 s response (session reused); answers "Hello"
6. Five messages back-to-back → each <5 s
7. Wait > 1 h, send again → session re-spawned, slower first turn, fast after
8. Run `graph_nexus_analysis` with claude-cli as `llm_provider` → completes, scores produced
9. Save model with `extra_args = "--tools Bash"` → 400 error
10. Save model with `extra_args = "--fallback-model claude-haiku-4-5"` → saves, takes effect on overload

## Known Risks (Not Fully Mitigated)

- **CC version drift.** Anthropic may change `-p` flag behavior or NDJSON event names in future releases. Mitigation: pin a tested CC version range in docs; integration tests catch regressions when bumping.
- **`--json-schema` behavior on deeply nested / recursive Pydantic types.** Verified to exist in `--help`, not stress-tested. If problems surface, fall back to manual prompt-and-validate (same as providers without schema support).
- **Windows vs. Linux spawn quirks.** Dev machine is Windows; prod may be Linux. Code branches on `sys.platform` in one helper.
- **Shared subscription quota.** All CC calls (chatbot + strategies, all conversations) share one subscription. Heavy strategy runs may delay chatbot responses. Mitigation: existing rate-limit handling; can add per-surface throttles later if needed.

## Effort Estimate

Approximately **2 weeks** of focused work:
- Backend new module + integration (~600 lines + tests): 1 week
- Frontend (3 Vue components, conditional rendering, Test Connection): 2-3 days
- Integration tests, manual smoke testing, polish: 2-3 days
- Documentation (operator setup, troubleshooting): 1 day

## File Inventory

| File | Change | Approx. lines |
|---|---|---|
| `backend/chatbot/claude_cli_provider.py` | NEW | ~350 |
| `backend/chatbot/llm.py` | EDIT | +30 |
| `backend/chatbot/orchestration.py` | EDIT | +5 |
| `backend/llm_utils.py` | EDIT | +40 |
| `backend/api/main.py` | EDIT | +50 |
| `backend/interactive_utils.py` | EDIT | +30 |
| `backend/model_resolver.py` | EDIT | +5 |
| `backend/tests/test_claude_cli_provider.py` | NEW | ~400 |
| `backend/tests/test_claude_cli_integration.py` | NEW | ~150 |
| `frontend/src/components/onboarding/StepAddModel.vue` | EDIT | +20 |
| `frontend/src/views/ModelsView.vue` | EDIT | +20 |
| `frontend/src/components/LlmConfigForm.vue` | EDIT | +80 |
| `docs/claude-code-provider-setup.md` | NEW | ~100 (operator docs) |

## References

- JarvisClaw (MIT) — Claude Code CLI bridge pattern: https://github.com/Th3-H4xx0r/JarvisClaw — specifically `services/forge/src/claude_bridge/session.rs` and `manager.rs`
- Claude Code CLI `--help` output: confirms `-p`, `--input-format stream-json`, `--output-format stream-json`, `--json-schema`, `--tools ""`, `--strict-mcp-config`, `--no-session-persistence`, `--disable-slash-commands`, `--system-prompt`, `--model`, `--fallback-model`, `--effort` flags exist as of CC 2.1.139
- IntelliStock model abstraction: `backend/chatbot/llm.py:314-361` (call_chat_with_tools), `backend/llm_utils.py` (call_structured_llm_by_provider), `backend/interactive_utils.py:7815-7900` (model CRUD), `backend/model_resolver.py:1-117`
