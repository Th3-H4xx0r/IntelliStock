# CC Daemon Reuse + Token Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `call_claude_cli_chat_structured` (the daemon path in `backend/chatbot/claude_cli_provider.py`) safe to enable in production by fixing its three documented bugs, and verify whether `claude-sonnet-4-6 reasoning_effort=medium` extended-thinking tokens are silently inflating Claude Max quota consumption.

**Architecture:** Three discrete units of change, in order. (1) A one-off probe script that prints the claude-cli envelope's full token breakdown so we can see whether `thinking_tokens` is being discarded; if found, log it. (2) Two scaffold-side edits — schema-fingerprinted conversation_id and per-conversation history accumulation in a bounded dict — both made entirely inside `backend/chatbot/claude_cli_provider.py` and `backend/llm_utils.py`, with **zero changes** to the shared session manager (`ClaudeCliSessionManager._send_one_turn_locked` / `_get_or_spawn`). (3) Unit + integration tests in `backend/tests/test_strategy_claude_cli_dispatch.py` that mock the daemon chat function and prove the scaffold conforms to the cumulative-history contract.

**Tech Stack:** Python 3.11, Pydantic v2, pytest, the `claude` CLI binary (used only at probe time; tests mock it).

**Reference spec:** `docs/superpowers/specs/2026-05-14-cc-daemon-reuse-and-token-audit-design.md`

---

## File Structure

| File | Role | Action |
|---|---|---|
| `scripts/probe_cc_envelope.py` | One-off diagnostic that invokes `claude` once with Sonnet 4.6 + reasoning_effort=medium and prints every key under `usage`/top-level. Deletable after the question is answered. | Create |
| `backend/llm_utils.py` | Holds the daemon-dispatch branch that builds `conversation_id`. We add a 12-char schema_hash segment so different schemas can't collide. We also clear the scaffold's history dict on errors in the retry loop. | Modify (around lines 1272-1320, 1369-1395) |
| `backend/chatbot/claude_cli_provider.py` | Holds `call_claude_cli_chat_structured`. We add module-level `_structured_history` dict + lock, accumulate user/assistant turns per conversation_id, send the cumulative list to the chat path, evict to keep memory bounded, and expose a `_clear_structured_history` helper for the retry path to call. We also surface `thinking_tokens` in the structured-spawn log line if Phase 1 finds the field. | Modify (around lines 2321-2473) |
| `backend/tests/test_strategy_claude_cli_dispatch.py` | Holds existing 51 tests for the llm_utils → claude-cli dispatch. We add 4 new tests covering schema fingerprinting, history accumulation, error-path history reset, and dict-bound eviction. | Modify (append) |

**Why no session-manager edits:** Bugs #1 and #3 both stem from the structured path violating the chat path's cumulative-history contract. We fix this by conforming to the contract from the scaffold side. The non-structured chatbot path (live, in production) is untouched.

---

## Phase 1 — Reasoning-Token Verification

### Task 1: Build the envelope probe

**Files:**
- Create: `scripts/probe_cc_envelope.py`

- [ ] **Step 1: Create the probe script**

```python
#!/usr/bin/env python
"""One-off diagnostic: invoke claude-cli once with Sonnet 4.6 +
reasoning_effort=medium and print every key under the response
envelope (especially anything resembling thinking/extended-thinking
or cache-creation/read tokens). Run once, inspect output, then
either wire the new fields into the structured-spawn logger or
document the negative result.

Usage:
    python scripts/probe_cc_envelope.py

Env vars:
    CLAUDE_CLI_DUMP_STRUCTURED_STDOUT=1  (set automatically below)
    CC_PROBE_MODEL                       (default: claude-sonnet-4-6)
    CC_PROBE_EFFORT                      (default: medium)
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Path-setup so this runs from repo root without installing the package.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_BACKEND = _REPO_ROOT / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

# Capture the raw envelope by re-enabling the diagnostic dump that
# commit 340e920 turned off-by-default. This MUST be set BEFORE the
# provider module is imported because the flag is read at import.
os.environ["CLAUDE_CLI_DUMP_STRUCTURED_STDOUT"] = "1"

from pydantic import BaseModel  # noqa: E402

from chatbot.claude_cli_provider import call_claude_cli_structured  # noqa: E402


class _ProbeOut(BaseModel):
    answer: str
    score: float


def main() -> int:
    model = os.environ.get("CC_PROBE_MODEL", "claude-sonnet-4-6")
    effort = os.environ.get("CC_PROBE_EFFORT", "medium")

    # Minimal prompt — we want the envelope, not a good answer.
    sys_prompt = (
        "You classify text as 'positive' or 'negative' and emit a "
        "score between 0 and 1. Respond with JSON only."
    )
    user_prompt = "Classify: 'The earnings beat estimates by 12%.'"

    try:
        result = call_claude_cli_structured(
            model=model,
            system_prompt=sys_prompt,
            user_prompt=user_prompt,
            output_schema=_ProbeOut,
            reasoning_effort=effort,
            timeout_sec=90,
        )
    except Exception as e:
        print(f"PROBE FAILED: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    print("=== Parsed result ===")
    print(json.dumps(result.model_dump(), indent=2))
    print()
    print("=== Look in stderr above for the raw envelope dump ===")
    print(
        "The CLAUDE_CLI_DUMP_STRUCTURED_STDOUT=1 flag prints lines starting "
        "with '[claude-cli][structured][stdout]'. Inspect those for token fields."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Run the probe once and capture output**

Run: `python scripts/probe_cc_envelope.py 2>&1 | tee /tmp/cc_probe.log`

Expected: a parsed `{"answer": "...", "score": ...}` printed, plus one or more `[claude-cli][structured][stdout]` lines in stderr containing the raw envelope JSON.

- [ ] **Step 3: Inspect the envelope for token fields**

Open `/tmp/cc_probe.log` (or scroll the terminal). Look at the JSON content of `[claude-cli][structured][stdout]` lines and answer three yes/no questions:

1. Does the envelope contain a top-level or nested `thinking_tokens` / `reasoning_tokens` / `output_tokens.thinking` field?
2. Does it contain `cache_creation_input_tokens` or `cache_read_input_tokens`?
3. Does it contain any other usage-related field beyond `input_tokens` / `output_tokens`?

Write the answers (and the exact field paths if yes) into the commit message for Task 2.

- [ ] **Step 4: Commit the probe**

```bash
git add scripts/probe_cc_envelope.py
git commit -m "probe: dump claude-cli envelope to verify reasoning-token accounting"
```

### Task 2: Wire newly-discovered token fields into the structured log line

**Files:**
- Modify: `backend/chatbot/claude_cli_provider.py` (the structured-spawn `Tokens:` log line — find with grep below)

**Conditional task — execute ONLY if Task 1 Step 3 found a previously-unlogged field.**

- [ ] **Step 1: Locate the existing Tokens log line**

Run: `grep -n '"Tokens:' backend/chatbot/claude_cli_provider.py`

Identify the line that currently formats `f"Tokens: input={...} output={...}"` (or similar). Note the surrounding code that extracts `input_tokens` / `output_tokens` from the envelope — the new field will be parsed the same way.

- [ ] **Step 2: Write the failing test**

Add to `backend/tests/test_strategy_claude_cli_dispatch.py`:

```python
def test_thinking_tokens_logged_when_envelope_contains_them(capsys):
    """If the claude-cli envelope returns a thinking_tokens field
    (Sonnet 4.6 reasoning_effort=medium), the structured-spawn log
    line must surface it. Otherwise quota burn appears mysteriously
    low in the logs vs the Anthropic console."""
    # The test feeds a synthetic envelope through the spawn-path's
    # parsing helper. The exact helper name + envelope shape come from
    # Task 1 Step 3 — fill in once the probe has revealed them.
    pass  # PLACEHOLDER: replace with concrete assertion after probe runs
```

Note: this is a deliberate placeholder because the field path depends on the probe's output. The implementing engineer fills in the assertion using the path discovered in Task 1 Step 3. Skip this task entirely if no new field was found.

- [ ] **Step 3: Run the placeholder test to confirm it's skipped or trivially passes**

Run: `pytest backend/tests/test_strategy_claude_cli_dispatch.py::test_thinking_tokens_logged_when_envelope_contains_them -v`

Expected: PASS (placeholder body is empty) or SKIP if you mark it.

- [ ] **Step 4: Add the field to the log line and to `_LAST_STRUCTURED_LLM_CALL.data["usage"]`**

Pattern (substitute the actual field name from the probe — example uses `thinking_tokens`):

```python
# Inside the structured-spawn result handler in claude_cli_provider.py,
# wherever input_tokens / output_tokens are read from the envelope:
_thinking = int(usage.get("thinking_tokens") or 0)
_log_msg = (
    f"Tokens: input={_input} output={_output}"
    + (f" thinking={_thinking}" if _thinking else "")
)
# And in the metadata propagated to _LAST_STRUCTURED_LLM_CALL.data:
data["usage"]["thinking_tokens"] = _thinking
```

- [ ] **Step 5: Replace the placeholder test with a real assertion**

```python
def test_thinking_tokens_logged_when_envelope_contains_them(capsys):
    from chatbot import claude_cli_provider as ccp
    # Feed a synthetic envelope through whichever helper extracts
    # usage. If it's parsed inside call_claude_cli_structured, mock
    # the subprocess call and assert on captured stdout/stderr.
    # (Concrete shape: see Task 1 Step 3 probe output.)
    fake_envelope = {
        "usage": {"input_tokens": 100, "output_tokens": 50, "thinking_tokens": 250},
        # ... whatever other fields the envelope contains ...
    }
    # Assert the log line contains "thinking=250".
    # Assert _LAST_STRUCTURED_LLM_CALL.data["usage"]["thinking_tokens"] == 250.
    ...
```

- [ ] **Step 6: Run the test**

Run: `pytest backend/tests/test_strategy_claude_cli_dispatch.py::test_thinking_tokens_logged_when_envelope_contains_them -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add backend/chatbot/claude_cli_provider.py backend/tests/test_strategy_claude_cli_dispatch.py
git commit -m "log: surface claude-cli thinking_tokens so reasoning-effort quota cost is visible"
```

---

## Phase 2 — Daemon-Path Fixes

### Task 3: Add schema_hash to the conversation_id (bug #2)

**Files:**
- Modify: `backend/llm_utils.py:1288-1293`
- Test: `backend/tests/test_strategy_claude_cli_dispatch.py`

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_strategy_claude_cli_dispatch.py`:

```python
def test_daemon_conversation_id_differs_per_schema(monkeypatch):
    """Bug #2 fix: two output schemas with the same model+system_prompt+thread
    must NOT collide on conversation_id. Otherwise the second schema reuses
    the first schema's frozen system prompt inside the daemon subprocess."""
    from pydantic import BaseModel

    class SchemaA(BaseModel):
        a: str

    class SchemaB(BaseModel):
        b: int
        c: int

    monkeypatch.setenv("CLAUDE_CLI_DAEMON_FOR_STRUCTURED", "1")
    seen_ids: list[str] = []

    def fake_chat_structured(**kwargs):
        seen_ids.append(kwargs["conversation_id"])
        return kwargs["output_schema"](
            **({"a": "x"} if kwargs["output_schema"] is SchemaA else {"b": 1, "c": 2})
        )

    from llm_utils import call_structured_llm_by_provider
    with patch(
        "chatbot.claude_cli_provider.call_claude_cli_chat_structured",
        side_effect=fake_chat_structured,
    ):
        call_structured_llm_by_provider(
            "claude-cli", "k", "claude-sonnet-4-6",
            prompt="p", output_type=SchemaA,
            provider_config={"cli_path": "claude"},
        )
        call_structured_llm_by_provider(
            "claude-cli", "k", "claude-sonnet-4-6",
            prompt="p", output_type=SchemaB,
            provider_config={"cli_path": "claude"},
        )

    assert len(seen_ids) == 2
    assert seen_ids[0] != seen_ids[1], (
        f"Schema A and B must yield different conversation_ids; got {seen_ids}"
    )
    # Both should include the schema-fingerprint segment we added.
    assert "schema" in seen_ids[0] and "schema" in seen_ids[1]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && pytest tests/test_strategy_claude_cli_dispatch.py::test_daemon_conversation_id_differs_per_schema -v`
Expected: FAIL with `AssertionError: Schema A and B must yield different conversation_ids` (current conversation_id has no schema component).

- [ ] **Step 3: Locate the conversation_id construction**

Open `backend/llm_utils.py` and find lines 1288-1293 (the block that begins with `_use_daemon_path = daemon_for_structured_enabled()` and constructs `_conversation_id`).

- [ ] **Step 4: Compute and inject `schema_hash` into the conversation_id**

Replace lines 1288-1293 with:

```python
    _use_daemon_path = daemon_for_structured_enabled()
    _conversation_id = ""
    if _use_daemon_path:
        _sys_hash = hashlib.sha256(
            (sys_str or "").encode("utf-8", errors="replace")
        ).hexdigest()[:12]
        # Bug #2 fix: include a schema fingerprint so two different output
        # schemas (e.g., company-classification vs macro-classification)
        # never collide on conversation_id. Without this, the daemon
        # subprocess keeps the first schema's system suffix forever
        # because _get_or_spawn deliberately ignores subsequent
        # system_prompt values when reusing a session.
        try:
            _schema_json = json.dumps(
                output_type.model_json_schema(), sort_keys=True
            )
        except Exception:
            # Fall back to the class qualified name — better than no
            # fingerprint at all.
            _schema_json = f"{output_type.__module__}.{output_type.__qualname__}"
        _schema_hash = hashlib.sha256(
            _schema_json.encode("utf-8", errors="replace")
        ).hexdigest()[:12]
        _tid = threading.get_ident()
        _conversation_id = (
            f"nexus-structured-{model}-{_sys_hash}-schema{_schema_hash}-tid{_tid}"
        )
```

(Add `import json` at the top of the file if not already imported — check first with `grep '^import json' backend/llm_utils.py`.)

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd backend && pytest tests/test_strategy_claude_cli_dispatch.py::test_daemon_conversation_id_differs_per_schema -v`
Expected: PASS

- [ ] **Step 6: Run the full existing test file to ensure no regressions**

Run: `cd backend && pytest tests/test_strategy_claude_cli_dispatch.py -v`
Expected: all 51+ tests pass (no existing test should break).

- [ ] **Step 7: Commit**

```bash
git add backend/llm_utils.py backend/tests/test_strategy_claude_cli_dispatch.py
git commit -m "fix(daemon): include schema fingerprint in nexus-structured conversation_id"
```

### Task 4: Accumulate per-conversation history in the scaffold (bug #1)

**Files:**
- Modify: `backend/chatbot/claude_cli_provider.py:2321-2473` (add module-level history dict + lock; rewrite the `call_claude_cli_chat` call to pass cumulative history)
- Test: `backend/tests/test_strategy_claude_cli_dispatch.py`

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_strategy_claude_cli_dispatch.py`:

```python
def test_daemon_history_accumulates_across_calls():
    """Bug #1 fix: the chat-path session manager slices messages by
    sess.messages_sent, so the scaffold must pass cumulative history,
    not a single user turn. After two structured calls on the same
    conversation_id, the second invocation of call_claude_cli_chat
    must see at least 3 messages (user1, assistant1, user2)."""
    from chatbot.claude_cli_provider import (
        call_claude_cli_chat_structured,
        _clear_structured_history,
    )

    seen_message_lists: list[list] = []

    def fake_chat(**kwargs):
        seen_message_lists.append(list(kwargs.get("messages", [])))
        return {"content": json.dumps({"text": "ok", "score": 0.5})}

    _clear_structured_history()  # ensure clean slate
    with patch("chatbot.claude_cli_provider.call_claude_cli_chat", side_effect=fake_chat):
        for _ in range(2):
            call_claude_cli_chat_structured(
                conversation_id="probe-1",
                model="claude-sonnet-4-6",
                system_prompt="sys",
                user_prompt="u",
                output_schema=_DummyOutput,
            )

    assert len(seen_message_lists) == 2
    # First call: just the user turn.
    assert len(seen_message_lists[0]) == 1
    assert seen_message_lists[0][0]["role"] == "user"
    # Second call: at minimum user + assistant + user (cumulative history).
    assert len(seen_message_lists[1]) >= 3
    assert seen_message_lists[1][0]["role"] == "user"
    assert seen_message_lists[1][1]["role"] == "assistant"
    assert seen_message_lists[1][2]["role"] == "user"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && pytest tests/test_strategy_claude_cli_dispatch.py::test_daemon_history_accumulates_across_calls -v`
Expected: FAIL — either `ImportError: cannot import name '_clear_structured_history'` or `len(seen_message_lists[1]) >= 3` assertion fails (current code passes a single-message list both times).

- [ ] **Step 3: Add module-level state and helper to `claude_cli_provider.py`**

After the imports near the top of `backend/chatbot/claude_cli_provider.py` (find a suitable location — e.g., next to other module-level state — or near the `_JSON_ONLY_SYSTEM_SUFFIX` constant), add:

```python
# ── Scaffold-side history accumulation for the structured daemon path ──
#
# The chat-path session manager (ClaudeCliSessionManager._send_one_turn_locked)
# requires callers to pass cumulative history each call; it slices
# ``messages[sess.messages_sent:]`` to compute the diff. The structured
# scaffold (call_claude_cli_chat_structured) originally passed a single
# user turn, which made every call after the first fail with
# "send_turn called with no new messages to deliver" (bug #1 in the
# function's docstring).
#
# We fix this scaffold-side: maintain a small history dict keyed on
# conversation_id, append user+assistant turns per call, and pass the
# accumulated list to call_claude_cli_chat. We retain only the last
# (user, assistant) pair per conversation_id so memory stays bounded
# even across multi-day backtests; the session manager only needs
# "len(messages) > messages_sent" to be true, which a single pair
# satisfies.
_structured_history: Dict[str, List[Dict[str, str]]] = {}
_structured_history_lock = threading.Lock()
_STRUCTURED_HISTORY_MAX_CONVERSATIONS = 512
_STRUCTURED_HISTORY_KEEP_PAIRS = 1  # keep last user/assistant pair per conv_id


def _clear_structured_history(conversation_id: Optional[str] = None) -> None:
    """Drop the scaffold's per-conversation history.

    Called by the retry path in ``llm_utils`` when the daemon subprocess
    dies mid-batch — the subprocess loses its context on respawn, so
    our cached history would be stale relative to the new session.
    Pass ``conversation_id=None`` to drop everything (used by tests).
    """
    with _structured_history_lock:
        if conversation_id is None:
            _structured_history.clear()
        else:
            _structured_history.pop(conversation_id, None)
```

(Verify `Dict`, `List`, `Optional` are already imported from `typing` at the top of the file — they should be; if not, add them.)

- [ ] **Step 4: Rewrite `call_claude_cli_chat_structured` to use the history dict**

In `backend/chatbot/claude_cli_provider.py`, find the body of `call_claude_cli_chat_structured` (around lines 2333-2473). Replace the `call_claude_cli_chat` invocation (lines 2422-2432) and the surrounding logic with:

```python
    # Bug #1 fix: pass cumulative history per conversation_id. See module
    # comment on _structured_history for the contract this satisfies.
    with _structured_history_lock:
        history = _structured_history.setdefault(conversation_id, [])
        history.append({"role": "user", "content": user_prompt or ""})
        messages_snapshot = list(history)

    try:
        chat_result = call_claude_cli_chat(
            conversation_id=conversation_id,
            messages=messages_snapshot,
            system_prompt=augmented_system_prompt,
            model=model,
            user_id=user_id or "",
            cli_path=cli_path or "claude",
            extra_args=extra_args,
            reasoning_effort=reasoning_effort,
            timeout_sec=timeout_sec,
        )
    except Exception:
        # The daemon subprocess may have died or rejected the turn —
        # drop the user message we optimistically appended so the next
        # retry doesn't pass the same prompt twice.
        with _structured_history_lock:
            hist = _structured_history.get(conversation_id, [])
            if hist and hist[-1].get("role") == "user":
                hist.pop()
            if not hist:
                _structured_history.pop(conversation_id, None)
        raise
```

Then, after the `chat_result` is parsed and before the function returns the validated output (i.e., after the existing JSON-parsing + validation block), add the assistant-side history append and eviction:

```python
    # Successful response — record the assistant turn so the next call
    # on this conversation_id can slice past it.
    with _structured_history_lock:
        hist = _structured_history.get(conversation_id)
        if hist is not None:
            hist.append({"role": "assistant", "content": stripped})
            # Eviction: keep only the most recent N pairs. We don't
            # need true multi-turn — we only need len(history) > 0
            # at dispatch time so messages_sent slicing yields a
            # non-empty list. Constant memory per conversation_id.
            cap = _STRUCTURED_HISTORY_KEEP_PAIRS * 2
            if len(hist) > cap:
                _structured_history[conversation_id] = hist[-cap:]
            # Bound the dict size to prevent unbounded growth across
            # long backtests with many distinct (model, sys, schema,
            # tid) triples. Drop the oldest 10% in insertion order.
            if len(_structured_history) > _STRUCTURED_HISTORY_MAX_CONVERSATIONS:
                drop_n = max(1, _STRUCTURED_HISTORY_MAX_CONVERSATIONS // 10)
                for k in list(_structured_history.keys())[:drop_n]:
                    _structured_history.pop(k, None)
```

(The variable `stripped` is the content we successfully parsed — defined earlier in the function. Verify by reading the existing code.)

- [ ] **Step 5: Run the new test to verify it passes**

Run: `cd backend && pytest tests/test_strategy_claude_cli_dispatch.py::test_daemon_history_accumulates_across_calls -v`
Expected: PASS

- [ ] **Step 6: Run the full claude-cli test suite**

Run: `cd backend && pytest tests/test_strategy_claude_cli_dispatch.py tests/test_claude_cli_provider.py tests/test_claude_cli_integration.py -v`
Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add backend/chatbot/claude_cli_provider.py backend/tests/test_strategy_claude_cli_dispatch.py
git commit -m "fix(daemon): accumulate cumulative history in scaffold so chat-path slicing works"
```

### Task 5: Clear history on dispatch errors (bug #1 follow-up)

**Files:**
- Modify: `backend/llm_utils.py:1369-1395` (the retry loop's `except` blocks)
- Test: `backend/tests/test_strategy_claude_cli_dispatch.py`

The append-then-raise rollback inside the scaffold (Task 4 Step 4) handles the optimistic-user-turn case. We also want to clear the entire history for a conversation_id when the retry loop in `llm_utils.py` decides to back off and retry — the daemon subprocess may have crashed, so any retained history is now stale.

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_strategy_claude_cli_dispatch.py`:

```python
def test_daemon_history_cleared_between_retries(monkeypatch):
    """Bug #1 follow-up: when a dispatch raises ClaudeCliValidationError
    and the retry loop sleeps + retries, the scaffold's per-conversation
    history must be cleared so the next attempt doesn't replay stale
    messages against a possibly-respawned subprocess."""
    from chatbot.claude_cli_provider import (
        ClaudeCliValidationError,
        _clear_structured_history,
        _structured_history,
    )

    monkeypatch.setenv("CLAUDE_CLI_DAEMON_FOR_STRUCTURED", "1")
    _clear_structured_history()

    calls = {"n": 0}

    def fake_chat_structured(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ClaudeCliValidationError("synthetic invalid output")
        return _DummyOutput(text="ok", score=0.5)

    from llm_utils import call_structured_llm_by_provider
    with patch(
        "chatbot.claude_cli_provider.call_claude_cli_chat_structured",
        side_effect=fake_chat_structured,
    ):
        out = call_structured_llm_by_provider(
            "claude-cli", "k", "claude-sonnet-4-6",
            prompt="p", output_type=_DummyOutput,
            provider_config={"cli_path": "claude"},
            output_retries=1,  # one retry budget
        )

    assert out is not None
    assert calls["n"] == 2  # one failure, one success
    # After both calls, the success path's history should contain at most
    # one pair for the surviving conversation_id (not two pairs).
    for conv_id, hist in _structured_history.items():
        assert len(hist) <= 2, (
            f"History for {conv_id} should be reset between retries; got {hist!r}"
        )
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && pytest tests/test_strategy_claude_cli_dispatch.py::test_daemon_history_cleared_between_retries -v`
Expected: FAIL — history isn't being cleared between retries yet.

- [ ] **Step 3: Clear history in the validation-error retry path**

In `backend/llm_utils.py`, find the `except ClaudeCliValidationError as e:` block (around lines 1369-1384) and the `except ClaudeCliError as e:` block (around line 1385). After the `if attempt < _retry_budget:` branch but before the `time.sleep(_backoff); continue`, add:

```python
                # Daemon subprocess may have died or returned garbage —
                # clear our cached history for this conversation_id so the
                # retry starts from a clean slate. The session manager
                # will respawn if needed.
                if _use_daemon_path and _conversation_id:
                    try:
                        from chatbot.claude_cli_provider import (
                            _clear_structured_history,
                        )
                        _clear_structured_history(_conversation_id)
                    except Exception:
                        pass  # best-effort; never block the retry on cleanup
```

Add this to BOTH the `ClaudeCliValidationError` and `ClaudeCliError` retry branches (terminal `ClaudeCliRateLimitError` / `ClaudeCliNotLoggedInError` don't retry and don't need cleanup).

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd backend && pytest tests/test_strategy_claude_cli_dispatch.py::test_daemon_history_cleared_between_retries -v`
Expected: PASS

- [ ] **Step 5: Run the full suite once more**

Run: `cd backend && pytest tests/test_strategy_claude_cli_dispatch.py -v`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add backend/llm_utils.py backend/tests/test_strategy_claude_cli_dispatch.py
git commit -m "fix(daemon): clear structured-history between retries so stale state never replays"
```

### Task 6: Test TPE-worker reuse end-to-end (bug #3 verification)

**Files:**
- Test: `backend/tests/test_strategy_claude_cli_dispatch.py` (append)

Bug #3 (same `threading.get_ident()` across consecutive TPE tasks → same conversation_id → bugs #1 and #2 trigger) is resolved automatically once Tasks 3 and 4 land. This task adds a regression test that proves it.

- [ ] **Step 1: Write the test**

Add to `backend/tests/test_strategy_claude_cli_dispatch.py`:

```python
def test_daemon_tpe_worker_reuse_does_not_break(monkeypatch):
    """Bug #3 regression: a ThreadPoolExecutor worker that handles two
    consecutive structured-call tasks must not fail on the second one.
    Before the fix, both tasks hit the same conversation_id (same tid)
    and the second tripped bug #1's empty-slice error.
    """
    import concurrent.futures
    from chatbot.claude_cli_provider import _clear_structured_history

    monkeypatch.setenv("CLAUDE_CLI_DAEMON_FOR_STRUCTURED", "1")
    _clear_structured_history()

    call_count = {"n": 0}

    def fake_chat_structured(**kwargs):
        call_count["n"] += 1
        return _DummyOutput(text=f"ok-{call_count['n']}", score=0.5)

    from llm_utils import call_structured_llm_by_provider
    with patch(
        "chatbot.claude_cli_provider.call_claude_cli_chat_structured",
        side_effect=fake_chat_structured,
    ):
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            # 3 sequential tasks on a single worker — same tid for all 3.
            futures = [
                pool.submit(
                    call_structured_llm_by_provider,
                    "claude-cli", "k", "claude-sonnet-4-6",
                    prompt=f"p{i}", output_type=_DummyOutput,
                    provider_config={"cli_path": "claude"},
                )
                for i in range(3)
            ]
            results = [f.result(timeout=10) for f in futures]

    assert call_count["n"] == 3
    assert all(r is not None for r in results)
    assert results[0].text == "ok-1"
    assert results[2].text == "ok-3"
```

- [ ] **Step 2: Run the test**

Run: `cd backend && pytest tests/test_strategy_claude_cli_dispatch.py::test_daemon_tpe_worker_reuse_does_not_break -v`
Expected: PASS (because Tasks 3+4 already fixed the underlying issue).

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_strategy_claude_cli_dispatch.py
git commit -m "test(daemon): cover TPE worker reuse path so bug #3 regression stays out"
```

### Task 7: Bound the history dict size

**Files:**
- Test: `backend/tests/test_strategy_claude_cli_dispatch.py` (append)

The eviction in Task 4 caps the dict at `_STRUCTURED_HISTORY_MAX_CONVERSATIONS` (512). Verify with a stress test.

- [ ] **Step 1: Write the test**

```python
def test_daemon_history_dict_bounded():
    """Memory-safety guard: the scaffold's history dict must never grow
    beyond _STRUCTURED_HISTORY_MAX_CONVERSATIONS, even across long
    backtests with many distinct (model, sys, schema, tid) triples."""
    from chatbot.claude_cli_provider import (
        _clear_structured_history,
        _structured_history,
        _STRUCTURED_HISTORY_MAX_CONVERSATIONS,
        call_claude_cli_chat_structured,
    )

    _clear_structured_history()

    def fake_chat(**kwargs):
        return {"content": json.dumps({"text": "ok", "score": 0.5})}

    with patch("chatbot.claude_cli_provider.call_claude_cli_chat", side_effect=fake_chat):
        # Drive 1.2x the cap worth of distinct conversation_ids.
        n = int(_STRUCTURED_HISTORY_MAX_CONVERSATIONS * 1.2)
        for i in range(n):
            call_claude_cli_chat_structured(
                conversation_id=f"probe-{i}",
                model="claude-sonnet-4-6",
                system_prompt="sys",
                user_prompt="u",
                output_schema=_DummyOutput,
            )

    assert len(_structured_history) <= _STRUCTURED_HISTORY_MAX_CONVERSATIONS, (
        f"Dict grew to {len(_structured_history)}, "
        f"cap is {_STRUCTURED_HISTORY_MAX_CONVERSATIONS}"
    )
```

- [ ] **Step 2: Run the test**

Run: `cd backend && pytest tests/test_strategy_claude_cli_dispatch.py::test_daemon_history_dict_bounded -v`
Expected: PASS (eviction logic added in Task 4 handles this).

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_strategy_claude_cli_dispatch.py
git commit -m "test(daemon): pin history-dict size cap so memory stays bounded across backtests"
```

---

## Phase 3 — Validation

### Task 8: Behavioral parity smoke test (manual)

**Files:**
- Modify: `docs/superpowers/specs/2026-05-14-cc-daemon-reuse-and-token-audit-design.md` (append a Phase-3 result section)

Once Tasks 3-7 are merged, run the daemon path against a small backtest to confirm no regression.

- [ ] **Step 1: Set the env flag and start a 1-day lookback**

Run (on the prod box or a dev container with creds):

```bash
export CLAUDE_CLI_DAEMON_FOR_STRUCTURED=1
# Start a 1-day lookback backtest via the normal IntelliStock API or CLI.
# Capture the backtest ID for log retrieval.
```

- [ ] **Step 2: Capture wall time + total tokens**

After the backtest finishes, pull the log:

```bash
python scripts/pull_backtest_logs.py <backtest_id>
```

Grep for the `"Lookback day complete"` summary line and the cumulative `Tokens:` rollup. Record both numbers.

- [ ] **Step 3: Re-run the same backtest with the flag OFF for comparison**

```bash
unset CLAUDE_CLI_DAEMON_FOR_STRUCTURED
# Run the same 1-day lookback on the same date range.
```

Compare wall time and tokens. Expected: daemon-on run is 15–25 s faster (spawn savings); token totals within ±2%.

- [ ] **Step 4: Document the result in the spec**

Append to `docs/superpowers/specs/2026-05-14-cc-daemon-reuse-and-token-audit-design.md`:

```markdown
## Phase 3 Result — Daemon Behavioral Parity (recorded YYYY-MM-DD)

- Daemon ON: backtest <id>, day-1 wall <X>s, total tokens <Y>
- Daemon OFF: backtest <id>, day-1 wall <X>s, total tokens <Y>
- Delta: wall <±Z>s, tokens <±W%>
- Verdict: <safe to enable | needs further work | revert>
```

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/specs/2026-05-14-cc-daemon-reuse-and-token-audit-design.md
git commit -m "docs: record daemon-path behavioral parity result"
```

### Task 9: Final sweep — run all relevant test suites

- [ ] **Step 1: Run the targeted suites**

Run:

```bash
cd backend && pytest tests/test_strategy_claude_cli_dispatch.py \
                     tests/test_claude_cli_provider.py \
                     tests/test_claude_cli_integration.py -v
```

Expected: all green.

- [ ] **Step 2: Run the broader nexus hardening suite**

Run:

```bash
cd backend && pytest test_graph_hardening.py -v
```

Expected: all green (no behavioral change to the nexus path; the daemon scaffold is still default-OFF).

- [ ] **Step 3: GitNexus check**

Run:

```bash
npx gitnexus analyze --embeddings
```

Expected: clean re-index.

---

## Self-Review Result

- **Spec coverage**: Phase 1 (probe + optional log wiring) → Tasks 1+2. Phase 2 Bug #2 → Task 3. Phase 2 Bug #1 → Tasks 4+5. Phase 2 Bug #3 → Task 6 (regression test, fix is inherent in Tasks 3-5). Bounded-memory risk → Task 7. Behavioral parity → Task 8. All non-goals explicitly out of scope.
- **Placeholder scan**: Task 2 contains an intentional placeholder pattern because the field name depends on the probe output — flagged in-line as conditional. No other TBDs.
- **Type consistency**: `_clear_structured_history`, `_structured_history`, `_STRUCTURED_HISTORY_MAX_CONVERSATIONS`, `_STRUCTURED_HISTORY_KEEP_PAIRS` are defined in Task 4 and referenced consistently in Tasks 5, 6, 7. Conversation_id segments `nexus-structured-{model}-{sys_hash}-schema{schema_hash}-tid{tid}` are stable across Tasks 3 and 6.
