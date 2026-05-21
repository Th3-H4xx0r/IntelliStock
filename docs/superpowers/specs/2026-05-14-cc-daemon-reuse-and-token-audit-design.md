# CC Daemon Path — True Reuse + Reasoning-Token Verification

- **Date:** 2026-05-14
- **Status:** Draft for review
- **Author session:** continuation of `.sessions/2026-05-14-194023-lookback-day-optimizations-and-daemon-scaffold.md`
- **Branch:** `claude-code-integration`
- **Predecessor commit:** `340e920` (daemon scaffold landed default-OFF with 3 documented bugs)

## Background

Commit `340e920` introduced a scaffold function `call_claude_cli_chat_structured` in `backend/chatbot/claude_cli_provider.py` that routes structured-output calls through the existing long-lived chatbot daemon (used by `call_claude_cli_chat`). The scaffold is gated by env flag `CLAUDE_CLI_DAEMON_FOR_STRUCTURED` (default OFF) and ships with three documented bugs:

1. `_send_one_turn_locked` (line `claude_cli_provider.py:1221`) slices `messages[messages_sent:]` and raises `"send_turn called with no new messages to deliver."` on every call after the first, because the structured path sends only the new turn while the chat path expects a cumulative history.
2. `_get_or_spawn` deliberately ignores `system_prompt` on session reuse, so different schemas reaching the same `conversation_id` silently use the first schema's instruction.
3. `ThreadPoolExecutor` worker reuse means `threading.get_ident()` is stable across consecutive batch tasks → same `conversation_id` → bugs #1 and #2 trigger on the second dispatch.

Separately, the operator reports Claude Max quota burning unusually fast even on `claude-sonnet-4-6 reasoning_effort=medium` — not on the slow gpt-oss path. The leading hypothesis is that Sonnet 4.6's medium-tier extended-thinking output tokens count toward billing but are invisible in the current `Tokens: input=X output=Y` log line.

## Goals

1. **Verify the reasoning-token hypothesis.** Establish, with concrete envelope data, whether claude-cli's structured-output envelope exposes a `thinking_tokens` (or equivalent) field that the current logging code discards. If so, wire it into the visible `Tokens:` log line.
2. **Fix the 3 daemon bugs so the scaffold actually delivers reuse.** Capture the ~21 s/day subprocess-startup savings (~700 ms × ~30 calls/day) the scaffold was designed for, by making per-thread daemon subprocesses reusable across calls in a batch.
3. **Keep all changes scaffold-side.** Do not edit the shared session-manager code path that backs the working non-structured `call_claude_cli_chat` — regressing the chatbot path is unacceptable.

## Non-Goals

- Wiring Anthropic prompt caching (`cache_control: {type: "ephemeral"}`) to the claude-cli path. Confirmed unsupported: no `cache_control` references in `backend/`, and the claude-cli binary does not expose the marker through any flag.
- Anthropic Batch API integration. Separate, larger change.
- Reducing `reasoning_effort` from medium → low/none for classification phases. Pure strategy-config change; out of scope of this spec.
- Refactoring `_send_one_turn_locked` to support single-turn semantics. Would touch shared chat-path code; we instead conform to its existing cumulative-history contract from the scaffold side.
- Cross-thread subprocess sharing. Current per-`tid` keying means N TPE workers × K schemas = N·K subprocesses; sharing across threads would require a connection pool with locking. Out of scope.

## Phase 1 — Reasoning-Token Verification

### Probe

Add `scripts/probe_cc_envelope.py` (one-off diagnostic; can be deleted after the question is answered):

1. Construct a minimal structured call: `claude-sonnet-4-6`, `reasoning_effort=medium`, a 200-token system prompt, a 50-token user prompt, and a trivial 2-field schema.
2. Invoke `claude` via subprocess (the same shape as `call_claude_cli_chat_structured_subprocess` already does).
3. Set `CLAUDE_CLI_DUMP_STRUCTURED_STDOUT=1` for this call only so the full envelope JSON is preserved.
4. Print: top-level keys, every key under `usage`/`token_usage`/`metrics` (whichever exists), and the byte size of the envelope.
5. Compare against the fields the production code currently reads in `_call_claude_cli_structured_from_strategy`.

### Decision tree

| Probe result | Action in same PR |
|---|---|
| Envelope contains `thinking_tokens` or `output_tokens.thinking` field | Extend the `Tokens:` log line in `claude_cli_provider.py` to surface it. Add to `_LAST_STRUCTURED_LLM_CALL` data. ~10 lines. |
| Envelope contains `cache_creation_input_tokens` / `cache_read_input_tokens` (would contradict prior analysis) | Stop. File a follow-up — there is a hidden cache mechanism worth investigating before further work. |
| Envelope contains no separate thinking-token field | Document the negative result in the probe script comments and in commit message. Hypothesis falsified — quota burn cause is elsewhere (likely the 6-worker maintenance cap × Sonnet calls — covered in Phase 2 follow-up). |

### Why this is Phase 1, not a separate PR

If the probe reveals a billable token category we're not tracking, the daemon-path benchmarking in Phase 3 will report misleading numbers unless we capture that field too. We need accurate token accounting before we can validate "did the daemon path actually help."

## Phase 2 — Daemon-Path Fixes (Approach 2: True Reuse)

### Fix for Bug #2 — schema-fingerprinted conversation_id

**File:** `backend/llm_utils.py` (the daemon-dispatch branch added in `340e920` around lines 1272-1320).

Change the conversation_id construction from:

```python
_conversation_id = f"nexus-structured-{model}-{_sys_hash[:12]}-tid{_tid}"
```

to:

```python
_schema_hash = hashlib.sha256(schema_json.encode("utf-8")).hexdigest()[:12]
_conversation_id = f"nexus-structured-{model}-{_sys_hash[:12]}-schema{_schema_hash}-tid{_tid}"
```

`schema_json` is the same `json.dumps(schema, sort_keys=True)` value already produced upstream (per the bug-sweep fix in `340e920`). Stable, deterministic, no collisions across schemas.

**Effort:** 2-3 lines. No tests need new infrastructure; one unit test that verifies two different schemas produce two different conversation_ids is sufficient.

### Fix for Bug #1 — scaffold-side history accumulation

**File:** `backend/chatbot/claude_cli_provider.py`, inside `call_claude_cli_chat_structured`.

Add module-level state:

```python
_structured_history: Dict[str, List[Dict[str, str]]] = {}
_structured_history_lock = threading.Lock()
_STRUCTURED_HISTORY_MAX_CONVERSATIONS = 512
_STRUCTURED_HISTORY_KEEP_PAIRS = 1  # keep only last user/assistant pair per conv-id
```

In `call_claude_cli_chat_structured`, replace the single-message dispatch with:

```python
with _structured_history_lock:
    history = _structured_history.setdefault(conversation_id, [])
    history.append({"role": "user", "content": user_prompt})
    messages_to_send = list(history)  # snapshot under lock

# ... existing call_claude_cli_chat(messages=messages_to_send, ...) ...

# After successful response:
with _structured_history_lock:
    history = _structured_history.get(conversation_id, [])
    history.append({"role": "assistant", "content": assistant_text})
    # Eviction: keep only the most recent pair, so messages_sent slicing remains [latest_user]
    if len(history) > _STRUCTURED_HISTORY_KEEP_PAIRS * 2:
        _structured_history[conversation_id] = history[-_STRUCTURED_HISTORY_KEEP_PAIRS * 2:]
    # Bound dict size to prevent unbounded growth across long backtests
    if len(_structured_history) > _STRUCTURED_HISTORY_MAX_CONVERSATIONS:
        # Drop oldest 10% (insertion order; Python dicts preserve it)
        drop_n = _STRUCTURED_HISTORY_MAX_CONVERSATIONS // 10
        for k in list(_structured_history.keys())[:drop_n]:
            del _structured_history[k]
```

### Bug #3 — resolved automatically by #1 + #2

Once each `(model, sys_hash, schema_hash, tid)` triple maintains its own cumulative history dict entry, TPE worker reuse stops being a bug: the second call from the same worker correctly appends a new user turn, the session manager slices `messages[messages_sent:]` to `[latest_user]`, and the dispatch succeeds. No call-level UUID needed — and importantly, no UUID would defeat the warm-subprocess reuse that's the whole point.

### Failure handling

If the daemon subprocess dies mid-batch (rate-limit kill, crash), the session manager re-spawns. The scaffold's history dict will be stale (it thinks the subprocess has prior context that's now lost). To handle:

- On any `ClaudeCliError` / `ClaudeCliValidationError` from a dispatch, clear that conversation_id's history entry under the lock before the retry runs.
- Pattern: `with _structured_history_lock: _structured_history.pop(conversation_id, None)` in the except block of the existing retry loop in `llm_utils.py:~1352-1379`.

### Subprocess proliferation note

Per-`tid` keying means N TPE workers × K schemas = up to N·K subprocesses live at once. Typical lookback shape: K=2 (company, macro), N=6 (the new maintenance cap) → up to 12 subprocesses, each ~30 MB resident. Acceptable. Document the upper bound in the function docstring.

## Phase 3 — Validation

### Unit tests (add to `tests/test_strategy_claude_cli_dispatch.py`)

1. `test_conversation_id_includes_schema_hash`: two different schemas → two different conversation_ids; identical schemas → identical conversation_ids.
2. `test_history_accumulates_across_calls`: monkeypatch `call_claude_cli_chat`, dispatch 3 sequential `call_claude_cli_chat_structured` calls with the same conversation_id, assert the third call sees `[user, assistant, user, assistant, user]` (5 messages) at dispatch time, or — under eviction — `[user, assistant, user]` (3 messages, last pair retained).
3. `test_history_evicted_on_error`: dispatch one call successfully, then one that raises `ClaudeCliError`, then a third successful call; assert history is reset between #2 and #3.
4. `test_history_dict_bounded`: insert 600 distinct conversation_ids; assert size ≤ 512 after.

### Integration test

`test_tpe_worker_reuse_does_not_break`: spin up a `ThreadPoolExecutor(max_workers=2)`, submit 6 `call_claude_cli_chat_structured` calls against a stub `call_claude_cli_chat`, assert all 6 succeed with non-empty responses and the stub was called 6 times.

### Behavioral parity check

Add a `pytest -m manual` test (or document in the PR description) that:
1. Enables `CLAUDE_CLI_DAEMON_FOR_STRUCTURED=1` locally.
2. Runs a 1-day lookback against a small symbol universe.
3. Compares wall time and total `Tokens:` log values against the same run with the flag off.
4. Expected delta: −15s to −25s wall time (matches ~700 ms × ~30 calls projection); token totals should be within ±2% (no structural change in payloads).

## Risks

- **History-dict growth in pathological cases.** Mitigated by the 512-conv-id cap + per-id pair-keeping cap. Worst case at 512 entries × ~2 KB each ≈ 1 MB resident — negligible.
- **Subprocess proliferation in long-running prod.** Bounded by N·K. If operator raises maintenance cap further or adds more schemas, the count grows linearly but slowly. Documented; revisit if it exceeds 32.
- **Session-manager edge cases we don't yet know about.** The chat path has been in production; we believe its contract is "always pass cumulative history" and we're conforming. If we discover the contract is more restrictive (e.g., session times out after N seconds idle and our cached history goes stale), Phase 3's behavioral parity check will surface it.
- **Reasoning-token probe may discover nothing surprising.** That's a valid outcome — we document the negative result and stop. The daemon fix work proceeds regardless because it has independent justification.

## Open Questions

- Should we also clear the scaffold's history dict when the env flag flips OFF (e.g., for graceful operator-side disable)? Not strictly necessary because dict is module-level and process-local, but a small `clear_structured_history()` helper would be tidy.
- Once Approach 2 is in, is there value in adding a small CLI tool (`scripts/cc_daemon_status.py`) to print live subprocess counts and history-dict size? Useful for prod diagnostics; out of scope for this PR but worth tracking.

## Out of Scope (cross-references)

- **Reasoning-effort downgrade for classification** (Sonnet 4.6 medium → low/none) — strategy-config edit, separate change.
- **Maintenance worker-cap tuning** (commit 340e920 raised from 2 → 6) — operator can revert via config if Phase 1 confirms reasoning tokens are inflating per-worker cost.
- **Win A config edit** (gpt-oss-120b → gpt-5-mini for daily sentiment + maintenance) — already documented in handoff; strategy-config edit, separate.
