"""Claude Code CLI as an IntelliStock LLM provider.

Wraps the locally-installed ``claude`` binary so the operator's Claude
Code subscription can drive both the chatbot and the structured-output
strategy paths without an Anthropic API key. **Pure text-only mode** —
no CC tools, no MCP servers, no filesystem/shell access from the model.

Two integration paths
=====================

``call_claude_cli_chat``
    Long-lived ``claude`` subprocess per chatbot conversation. Spawned on
    the first turn, reused across subsequent turns, evicted after an idle
    TTL. The CLI itself holds the conversation history — we send only the
    *new* user turn on each call via ``--input-format stream-json``. This
    amortises the ~3 s subprocess startup over the conversation lifetime.

``call_claude_cli_structured``
    Spawn-per-call for strategies. Uses CC's native ``--json-schema`` flag
    to enforce structured output server-side; we additionally re-validate
    with Pydantic on receipt for defense in depth.

Design pattern inspired by JarvisClaw (MIT-licensed, see
https://github.com/Th3-H4xx0r/JarvisClaw — specifically the
``services/forge/src/claude_bridge/{session,manager}.rs`` files). All
process management here is sync / threading-based to match IntelliStock's
existing chatbot orchestration (sync FastAPI handlers) and strategies'
``ThreadPoolExecutor`` callers.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from queue import Empty, Queue
from typing import Any, Dict, List, Optional


# ── Configuration (env-overridable) ────────────────────────────────────────

CLAUDE_CLI_MAX_CONCURRENT = int(os.environ.get("CLAUDE_CLI_MAX_CONCURRENT", "10"))
CLAUDE_CLI_IDLE_TTL_SEC = int(os.environ.get("CLAUDE_CLI_IDLE_TTL_SEC", "3600"))
CLAUDE_CLI_SPAWN_TIMEOUT_SEC = int(os.environ.get("CLAUDE_CLI_SPAWN_TIMEOUT_SEC", "30"))
CLAUDE_CLI_TURN_TIMEOUT_SEC = int(os.environ.get("CLAUDE_CLI_TURN_TIMEOUT_SEC", "120"))
CLAUDE_CLI_SWEEPER_INTERVAL_SEC = int(os.environ.get("CLAUDE_CLI_SWEEPER_INTERVAL_SEC", "300"))

# Global concurrency cap (chatbot + strategies combined).
_GLOBAL_SPAWN_SEM = threading.BoundedSemaphore(value=max(1, CLAUDE_CLI_MAX_CONCURRENT))


# ── Exceptions ─────────────────────────────────────────────────────────────


class ClaudeCliError(RuntimeError):
    """Generic claude-cli provider error."""


class ClaudeCliNotInstalledError(ClaudeCliError):
    """`claude` binary not on PATH (or `cli_path` is wrong)."""


class ClaudeCliNotLoggedInError(ClaudeCliError):
    """The CC CLI is installed but no one has run `claude` to log in."""


class ClaudeCliRateLimitError(ClaudeCliError):
    """Pro/Max subscription token quota hit. Retry later with backoff."""


class ClaudeCliTimeoutError(ClaudeCliError):
    """Per-call timeout exceeded."""


class ClaudeCliCrashError(ClaudeCliError):
    """Subprocess died mid-turn (EOF on stdout or non-zero exit)."""


class ClaudeCliValidationError(ClaudeCliError):
    """Output JSON did not match the requested Pydantic schema."""


# ── extra_args whitelist ───────────────────────────────────────────────────

# Flags users may set via the Models table's ``extra_args`` field. Each
# entry carries a ``"value"`` shape plus an optional validator that runs
# against the value. ``--append-system-prompt`` is *intentionally absent*:
# allowing it lets an authenticated user override the system prompt that
# IntelliStock sets, which would defeat the operator-policy boundary.
_EFFORT_LEVELS = {"low", "medium", "high", "xhigh", "max"}
_MAX_FLAG_TOKENS = 32        # cap on parsed argv tokens from extra_args
_MAX_FALLBACK_MODEL_LEN = 80  # cap on per-value length


def _validate_fallback_model(value: str) -> str:
    if not value or len(value) > _MAX_FALLBACK_MODEL_LEN:
        raise ValueError(f"--fallback-model value must be 1..{_MAX_FALLBACK_MODEL_LEN} chars")
    # Conservative charset: letters, digits, dot, dash, underscore.
    if not all(c.isalnum() or c in ".-_" for c in value):
        raise ValueError(f"--fallback-model value contains disallowed characters: {value!r}")
    return value


def _validate_effort_level(value: str) -> str:
    if value not in _EFFORT_LEVELS:
        raise ValueError(
            f"--effort value must be one of {sorted(_EFFORT_LEVELS)}; got {value!r}"
        )
    return value


def _validate_max_budget_usd(value: str) -> str:
    try:
        amount = float(value)
    except (TypeError, ValueError) as e:
        raise ValueError(f"--max-budget-usd value must be numeric; got {value!r}") from e
    import math as _math
    if _math.isnan(amount) or _math.isinf(amount):
        raise ValueError(f"--max-budget-usd must be a finite number; got {value!r}")
    if amount <= 0 or amount > 1000.0:
        raise ValueError("--max-budget-usd must be in the range (0, 1000]")
    # Re-emit a canonical form so a malicious payload like '1e308' or
    # '0x1p+10' can't slip through downstream.
    return f"{amount:.6f}"


_ALLOWED_EXTRA_ARG_FLAGS: Dict[str, Any] = {
    "--fallback-model": _validate_fallback_model,
    "--effort": _validate_effort_level,
    "--max-budget-usd": _validate_max_budget_usd,
}


# Flags that must never appear in extra_args. They're either set by us
# (so a user override would silently break the integration) or would
# undo the safety constraints (re-enabling tools / MCP / filesystem /
# alternative system prompts). The list errs broad on purpose; every
# entry here is also enforced by the allowlist (unknown == rejected).
_HARD_REJECTED_FLAGS = {
    # Tool / MCP / sandboxing escape hatches
    "--tools", "--allowed-tools", "--allowedTools",
    "--disallowed-tools", "--disallowedTools",
    "--dangerously-skip-permissions", "--allow-dangerously-skip-permissions",
    "--permission-mode",
    "--mcp-config", "--mcp-debug", "--add-dir",
    "--strict-mcp-config",       # we set this; user override is meaningless
    # System prompt / context overrides — would let users redirect the model
    "--system-prompt", "--system-prompt-file",
    "--append-system-prompt", "--append-system-prompt-file",
    "--exclude-dynamic-system-prompt-sections",
    # IntelliStock-managed flags
    "--print", "-p",
    "--input-format", "--output-format",
    "--include-partial-messages", "--include-hook-events",
    "--no-session-persistence",
    "--disable-slash-commands",
    "--model",
    "--verbose",
    # Session / persistence / interactive surfaces
    "--continue", "-c", "--resume", "-r",
    "--session-id", "--fork-session",
    "--from-pr",
    # IDE / remote-control / development affordances
    "--ide", "--chrome", "--no-chrome",
    "--remote-control", "--remote-control-session-name-prefix",
    "--debug", "-d", "--debug-file",
    "--worktree", "-w", "--tmux",
    # Plugin / settings / agent overrides — these can re-enable tools,
    # hooks, MCP, or change the agent identity entirely.
    "--plugin-dir", "--plugin-url",
    "--settings", "--setting-sources",
    "--agent", "--agents",
    "--betas", "--bare", "--brief",
    "--file", "-n", "--name",
    "--replay-user-messages",
    # Misc
    "--json-schema",                # we set this on the structured path
    "--effort",                     # allowed-list entry; reject if anyone
                                    # also lists it here will fail the
                                    # disjointness assert below.
}

# Defense-in-depth: the allowlist and rejectlist must NEVER overlap. A
# loosening that adds a flag to both lists would otherwise silently
# admit it via the allowlist path. Strip _HARD_REJECTED_FLAGS of any
# allowlist entries at module import (--effort is intentionally on the
# allowlist; remove the safety duplicate).
_HARD_REJECTED_FLAGS = _HARD_REJECTED_FLAGS - set(_ALLOWED_EXTRA_ARG_FLAGS)
assert not (set(_ALLOWED_EXTRA_ARG_FLAGS) & _HARD_REJECTED_FLAGS), (
    "claude-cli flag allowlist and reject-list overlap"
)


def _split_flag_token(token: str) -> tuple[str, Optional[str]]:
    """Split ``--flag=value`` into (``--flag``, ``value``); for a plain
    flag returns (token, None). Mirrors GNU/Unix long-option conventions
    so users can write either ``--effort high`` or ``--effort=high``."""
    if token.startswith("--") and "=" in token:
        head, _, tail = token.partition("=")
        return head, tail
    return token, None


def validate_extra_args(extra_args_str: str) -> List[str]:
    """Parse a user-supplied extra_args string into a list of subprocess
    arguments, raising ``ValueError`` if anything outside the allowlist
    appears.

    The free-text field on the Models form is convenient for advanced
    users but is a security boundary — re-enabling ``--tools`` would defeat
    the entire point of pure-text mode. So we hard-reject every flag that
    isn't on the small, vetted allowlist above AND we validate the value
    of each accepted flag (rejecting `--effort --tools`-style poisoning
    where the second token would otherwise be silently passed through).
    """
    if not extra_args_str or not extra_args_str.strip():
        return []
    try:
        tokens = shlex.split(extra_args_str)
    except ValueError as e:
        raise ValueError(f"extra_args is not valid shell-quoted text: {e}") from e
    if len(tokens) > _MAX_FLAG_TOKENS:
        raise ValueError(
            f"extra_args has too many tokens ({len(tokens)} > {_MAX_FLAG_TOKENS}); "
            "did you paste an entire shell command by mistake?"
        )

    out: List[str] = []
    i = 0
    while i < len(tokens):
        raw = tokens[i]
        flag, inline_value = _split_flag_token(raw)
        if flag in _HARD_REJECTED_FLAGS:
            raise ValueError(
                f"extra_args flag {flag!r} is not allowed (managed internally or "
                "would re-enable tools / MCP / filesystem access / system-prompt override)."
            )
        if flag not in _ALLOWED_EXTRA_ARG_FLAGS:
            raise ValueError(
                f"extra_args flag {flag!r} is not on the allowlist. "
                f"Permitted flags: {', '.join(sorted(_ALLOWED_EXTRA_ARG_FLAGS))}"
            )
        validator = _ALLOWED_EXTRA_ARG_FLAGS[flag]
        if inline_value is not None:
            value = inline_value
            i += 1
        else:
            if i + 1 >= len(tokens):
                raise ValueError(f"extra_args flag {flag!r} requires a value.")
            value = tokens[i + 1]
            i += 2
        # Reject value-poisoning: an attacker may try ``--effort --tools``
        # so the next token is "--tools" and the safety-relevant flag
        # then re-enters argv. Treat anything that LOOKS like a flag as
        # an invalid value.
        if value.startswith("-"):
            raise ValueError(
                f"extra_args flag {flag!r} has a suspicious value {value!r} "
                "(values may not start with '-')."
            )
        # Per-flag validator may further constrain (enum / numeric / etc.).
        validator(value)
        out.append(flag)
        out.append(value)
    return out


# ── Spawn args ─────────────────────────────────────────────────────────────


_SAFETY_FLAGS = [
    "--tools", "",                   # disable every built-in tool
    "--strict-mcp-config",            # ignore user/system MCP servers
    "--no-session-persistence",       # don't write session state to disk
    "--disable-slash-commands",       # don't load skills/slash commands
]


def _build_chat_argv(
    *,
    cli_path: str,
    model: str,
    system_prompt: str,
    extra_args: List[str],
) -> List[str]:
    """Argv for a long-lived chatbot subprocess (stream-json in & out)."""
    argv = [
        cli_path, "-p", "--verbose",
        "--input-format", "stream-json",
        "--output-format", "stream-json",
        "--model", model,
        "--system-prompt", system_prompt,
        *_SAFETY_FLAGS,
        *extra_args,
    ]
    return argv


def _build_structured_argv(
    *,
    cli_path: str,
    model: str,
    system_prompt: str,
    json_schema: str,
    extra_args: List[str],
) -> List[str]:
    """Argv for a spawn-per-call structured-output run."""
    argv = [
        cli_path, "-p",
        "--output-format", "json",
        "--model", model,
        "--system-prompt", system_prompt,
        "--json-schema", json_schema,
        *_SAFETY_FLAGS,
        *extra_args,
    ]
    return argv


def _platform_popen_kwargs() -> Dict[str, Any]:
    """Spawn kwargs that keep the subprocess in a process group we can
    cleanly kill on Linux and Windows."""
    if sys.platform == "win32":
        # CREATE_NEW_PROCESS_GROUP lets us send Ctrl-Break; without it the
        # parent's signal handlers can leak SIGINT into the child during
        # FastAPI graceful shutdown.
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
    return {"start_new_session": False}


def _allowed_cli_binaries() -> set[str]:
    """Operator-overridable allowlist of binaries we'll exec as the CLI.
    Default: just ``claude`` (resolved via PATH or an absolute path whose
    basename matches). Set ``CLAUDE_CLI_ALLOWED_BINARIES`` to a comma-
    separated list to extend (e.g. for vendored builds)."""
    raw = (os.environ.get("CLAUDE_CLI_ALLOWED_BINARIES") or "").strip()
    extras = {p.strip() for p in raw.split(",") if p.strip()}
    return {"claude", "claude.exe"} | extras


def _resolve_cli_path(cli_path: str) -> str:
    """Resolve ``cli_path`` to a vetted absolute executable, or raise
    ``ClaudeCliNotInstalledError``. This is the *only* place that decides
    which binary we'll exec; an authenticated user can write ``cli_path``
    into the Models table but cannot pivot the field into RCE because:

      * relative paths must basename-match an entry on the allowlist
        (default ``claude`` / ``claude.exe``);
      * absolute paths are resolved via ``os.path.realpath`` and then
        rejected if their basename isn't on the allowlist;
      * symlinks are followed before the basename check, so
        ``ln -s /bin/sh /tmp/claude && cli_path=/tmp/claude`` resolves
        back to ``/bin/sh`` and gets rejected.
    """
    import shutil
    requested = (cli_path or "claude").strip() or "claude"
    allow = _allowed_cli_binaries()

    if os.path.isabs(requested) or os.sep in requested or (os.altsep and os.altsep in requested):
        # Absolute or contains a path separator — verify it exists and
        # the realpath's basename is on the allowlist.
        if not os.path.isfile(requested):
            raise ClaudeCliNotInstalledError(
                f"cli_path {requested!r} does not exist on this server."
            )
        real = os.path.realpath(requested)
        if os.path.basename(real).lower() not in {a.lower() for a in allow}:
            raise ClaudeCliError(
                f"cli_path {requested!r} resolves to {real!r}, whose basename "
                f"is not in the allowlist {sorted(allow)}. Refusing to exec "
                "an arbitrary binary."
            )
        return real

    # PATH lookup — only basename-allowlisted entries are permitted.
    if requested.lower() not in {a.lower() for a in allow}:
        raise ClaudeCliError(
            f"cli_path {requested!r} is not in the allowlist {sorted(allow)}. "
            "Use an absolute path to the claude binary, or set "
            "CLAUDE_CLI_ALLOWED_BINARIES."
        )
    found = shutil.which(requested)
    if not found:
        raise ClaudeCliNotInstalledError(
            f"claude binary {requested!r} not found on PATH. "
            "Install with: npm i -g @anthropic-ai/claude-code"
        )
    real = os.path.realpath(found)
    if os.path.basename(real).lower() not in {a.lower() for a in allow}:
        raise ClaudeCliError(
            f"claude on PATH resolves to {real!r}, which isn't allowlisted."
        )
    return real


def _release_spawn_sem_safely() -> None:
    """Release one slot of ``_GLOBAL_SPAWN_SEM``; log on over-release rather
    than swallowing silently so bookkeeping bugs surface during testing."""
    try:
        _GLOBAL_SPAWN_SEM.release()
    except ValueError:
        _log(
            "_GLOBAL_SPAWN_SEM over-release (bug in close path); ignoring",
            "yellow",
        )


# ── Session state ──────────────────────────────────────────────────────────


class SessionState(Enum):
    SPAWNING = "spawning"
    IDLE = "idle"
    AWAITING = "awaiting"
    CRASHED = "crashed"
    CLOSED = "closed"


@dataclass
class _TurnResult:
    content: str = ""
    finish_reason: str = "stop"
    raw: Any = None
    error: Optional[Exception] = None


@dataclass
class _Session:
    """One persistent ``claude`` subprocess for one chatbot conversation.

    The CLI is the system of record for conversation history. We track
    ``messages_sent`` so subsequent turns push only the new user message;
    on a crash-and-respawn we reset to 0 and replay everything.

    Lock order (acquire in this order, release in reverse):
      ``manager._sessions_lock`` > ``send_lock`` > ``_pending_lock``
    """
    conversation_id: str
    model: str
    cli_path: str
    system_prompt: str
    extra_args_signature: str = ""    # tuple-as-string for change detection
    process: Optional[subprocess.Popen] = None
    state: SessionState = SessionState.SPAWNING
    last_activity: float = field(default_factory=time.monotonic)
    send_lock: threading.Lock = field(default_factory=threading.Lock)
    messages_sent: int = 0
    _stdout_thread: Optional[threading.Thread] = None
    _stderr_thread: Optional[threading.Thread] = None
    _accumulated: List[str] = field(default_factory=list)
    _last_result_meta: Dict[str, Any] = field(default_factory=dict)
    _pending: Optional["_TurnSlot"] = None
    _pending_lock: threading.Lock = field(default_factory=threading.Lock)
    # True iff this session owns one slot of ``_GLOBAL_SPAWN_SEM``. Set to
    # True after a successful Popen; flipped back to False at close. Without
    # this, a session created in the dict but whose ``_spawn`` failed before
    # the semaphore was acquired would over-release on close.
    _sem_held: bool = False
    # Holds the most recent ``result`` event that arrived before a caller
    # registered a pending slot. The reader stashes it here; the next
    # ``send_turn`` consumes it before writing new messages. Without this
    # buffer the reader silently drops the event, and the caller times out.
    _buffered_result: Optional[Dict[str, Any]] = None
    _buffered_text: str = ""


@dataclass
class _TurnSlot:
    """A bounded handoff between the per-session reader thread and the
    caller waiting for the next ``result`` NDJSON event."""
    event: threading.Event = field(default_factory=threading.Event)
    result: _TurnResult = field(default_factory=_TurnResult)


# ── Session manager (the persistent-process core) ──────────────────────────


class ClaudeCliSessionManager:
    """Singleton owning all live chatbot subprocesses."""

    def __init__(
        self,
        *,
        idle_ttl_sec: int = CLAUDE_CLI_IDLE_TTL_SEC,
        sweeper_interval_sec: int = CLAUDE_CLI_SWEEPER_INTERVAL_SEC,
        spawn_timeout_sec: int = CLAUDE_CLI_SPAWN_TIMEOUT_SEC,
        turn_timeout_sec: int = CLAUDE_CLI_TURN_TIMEOUT_SEC,
    ):
        self._sessions: Dict[str, _Session] = {}
        self._sessions_lock = threading.RLock()
        self._idle_ttl_sec = idle_ttl_sec
        self._sweeper_interval_sec = sweeper_interval_sec
        self._spawn_timeout_sec = spawn_timeout_sec
        self._turn_timeout_sec = turn_timeout_sec
        self._shutdown = threading.Event()
        self._sweeper_thread: Optional[threading.Thread] = None

    # ── Public API ──

    def start(self) -> None:
        """Start the idle sweeper thread. Idempotent."""
        if self._sweeper_thread and self._sweeper_thread.is_alive():
            return
        self._shutdown.clear()
        t = threading.Thread(
            target=self._sweeper_loop, name="claude-cli-sweeper", daemon=True,
        )
        t.start()
        self._sweeper_thread = t

    def shutdown_all(self, *, grace_sec: float = 5.0) -> None:
        """Close every live subprocess. Called from FastAPI lifespan exit."""
        self._shutdown.set()
        with self._sessions_lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for sess in sessions:
            self._close_session(sess, grace_sec=grace_sec)

    def send_turn(
        self,
        *,
        conversation_id: str,
        messages: List[Dict[str, Any]],
        system_prompt: str,
        model: str,
        cli_path: str,
        extra_args: List[str],
        timeout_sec: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Send one chatbot turn and return the normalised response shape.

        Retries once if the warm session was evicted/crashed between
        ``_get_or_spawn`` and acquiring ``send_lock``.
        """
        timeout = timeout_sec or self._turn_timeout_sec
        extra_sig = "|".join(extra_args)

        # Two-attempt loop: first attempt may find a session that the
        # sweeper / a concurrent close has just swapped out from under us;
        # the second attempt re-spawns. We never loop more than twice — if
        # the second attempt also races, the caller sees the error.
        last_err: Optional[Exception] = None
        for attempt in range(2):
            sess = self._get_or_spawn(
                conversation_id=conversation_id,
                model=model,
                cli_path=cli_path,
                system_prompt=system_prompt,
                extra_args=extra_args,
                extra_sig=extra_sig,
            )
            with sess.send_lock:
                # Re-verify the session we locked is still the live one in
                # the registry. The sweeper or ``close()`` may have popped
                # it between return from ``_get_or_spawn`` and our lock
                # acquisition. If so, retry with a fresh session.
                with self._sessions_lock:
                    current = self._sessions.get(conversation_id)
                if current is not sess or sess.state == SessionState.CRASHED:
                    # Drop the now-stale session and loop to re-spawn.
                    self._discard_session(sess)
                    continue
                # Also check that the OS process is still alive.
                if sess.process is None or sess.process.poll() is not None:
                    self._discard_session(sess)
                    continue
                try:
                    return self._send_one_turn_locked(
                        sess=sess, messages=messages, timeout=timeout,
                    )
                except ClaudeCliCrashError as e:
                    # The single in-flight crash already torn down by
                    # _send_one_turn_locked. Try once more with a respawn.
                    last_err = e
                    continue
        if last_err is not None:
            raise last_err
        raise ClaudeCliError("send_turn exhausted retries without explicit error")

    def _send_one_turn_locked(
        self, *, sess: _Session, messages: List[Dict[str, Any]], timeout: int,
    ) -> Dict[str, Any]:
        """Inner turn body; assumes caller holds ``sess.send_lock`` AND
        has confirmed the session is live + registered."""
        # Compute the messages we still need to push (full history on a
        # fresh spawn, only the diff on a warm session).
        to_send = messages[sess.messages_sent:]
        if not to_send:
            # No new messages — surface clearly. (Shouldn't normally
            # happen; the orchestration always appends a user turn.)
            raise ClaudeCliError(
                "send_turn called with no new messages to deliver."
            )

        slot = _TurnSlot()
        # Consume any result event that arrived before this slot existed
        # (e.g. test-driven races, or a CLI that ships its prompt response
        # faster than we can register the pending future).
        with sess._pending_lock:
            buffered = sess._buffered_result
            buffered_text = sess._buffered_text
            sess._buffered_result = None
            sess._buffered_text = ""
            sess._pending = slot
            sess._accumulated = []
        sess.state = SessionState.AWAITING

        try:
            self._write_user_messages(sess, to_send)
        except Exception as e:
            with sess._pending_lock:
                sess._pending = None
            sess.state = SessionState.CRASHED
            raise ClaudeCliCrashError(
                f"writing to claude stdin failed: {e}"
            ) from e

        sess.messages_sent = len(messages)
        sess.last_activity = time.monotonic()

        # If the reader already buffered a result before we registered,
        # deliver it directly without waiting for stdout.
        if buffered is not None:
            self._deliver_result(sess, buffered, accumulated_override=buffered_text)

        # Wait for the reader thread to fulfil the slot.
        if not slot.event.wait(timeout):
            with sess._pending_lock:
                sess._pending = None
            sess.state = SessionState.CRASHED
            self._kill_process(sess)
            raise ClaudeCliTimeoutError(
                f"claude turn timed out after {timeout}s"
            )

        # Snapshot the result under the lock; mutations elsewhere are
        # also guarded so this read is consistent.
        with sess._pending_lock:
            sess._pending = None
            err = slot.result.error
            content = slot.result.content
            finish_reason = slot.result.finish_reason or "stop"
            raw = slot.result.raw

        sess.last_activity = time.monotonic()
        if err is not None:
            # The reader saw an error event or detected a crash.
            sess.state = SessionState.CRASHED
            raise err
        sess.state = SessionState.IDLE
        return {
            "content": content,
            "tool_calls": [],
            "finish_reason": finish_reason,
            "raw": raw,
        }

    def close(self, conversation_id: str) -> None:
        """Explicitly close a session (e.g., user deletes the conversation)."""
        with self._sessions_lock:
            sess = self._sessions.pop(conversation_id, None)
        if sess is not None:
            self._close_session(sess)

    # ── Internal: spawn / lifecycle ──

    def _get_or_spawn(
        self,
        *,
        conversation_id: str,
        model: str,
        cli_path: str,
        system_prompt: str,
        extra_args: List[str],
        extra_sig: str,
    ) -> _Session:
        with self._sessions_lock:
            existing = self._sessions.get(conversation_id)
            if existing is not None:
                if (
                    existing.state in (SessionState.IDLE, SessionState.AWAITING, SessionState.SPAWNING)
                    and existing.model == model
                    and existing.system_prompt == system_prompt
                    and existing.extra_args_signature == extra_sig
                ):
                    return existing
                # Stale or mismatched (model / system_prompt / extra_args
                # changed since this conversation last ran). Drop & respawn.
                self._sessions.pop(conversation_id, None)
            sess = _Session(
                conversation_id=conversation_id,
                model=model,
                cli_path=cli_path,
                system_prompt=system_prompt,
                extra_args_signature=extra_sig,
            )
            self._sessions[conversation_id] = sess

        # Spawn outside the manager lock — startup takes seconds and other
        # conversations shouldn't block.
        if existing is not None and existing is not sess:
            self._close_session(existing)

        try:
            self._spawn(sess, extra_args=extra_args)
        except Exception:
            with self._sessions_lock:
                # Only remove if it's still the same session object (a
                # concurrent caller may have replaced it on respawn).
                if self._sessions.get(conversation_id) is sess:
                    self._sessions.pop(conversation_id, None)
            raise
        return sess

    def _spawn(self, sess: _Session, *, extra_args: List[str]) -> None:
        # Resolve+validate cli_path to a trusted absolute executable BEFORE
        # touching the global semaphore. We accept either ``"claude"`` (PATH
        # lookup), an absolute path whose basename is ``claude``/``claude.exe``,
        # or anything explicitly allowed via ``CLAUDE_CLI_ALLOWED_BINARIES``.
        resolved_cli = _resolve_cli_path(sess.cli_path)
        if not _GLOBAL_SPAWN_SEM.acquire(timeout=self._spawn_timeout_sec):
            raise ClaudeCliError(
                f"global subprocess cap ({CLAUDE_CLI_MAX_CONCURRENT}) reached; "
                "another call is hogging the budget."
            )
        argv = _build_chat_argv(
            cli_path=resolved_cli,
            model=sess.model,
            system_prompt=sess.system_prompt,
            extra_args=extra_args,
        )
        try:
            proc = subprocess.Popen(
                argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=1,
                text=True,
                encoding="utf-8",
                errors="replace",
                **_platform_popen_kwargs(),
            )
        except FileNotFoundError as e:
            # Semaphore was acquired above — release it before raising so
            # a missing binary doesn't permanently shrink the pool.
            _release_spawn_sem_safely()
            raise ClaudeCliNotInstalledError(
                f"claude binary not found at {sess.cli_path!r}. "
                "Install with: npm i -g @anthropic-ai/claude-code"
            ) from e
        except Exception as e:
            _release_spawn_sem_safely()
            raise ClaudeCliError(f"failed to spawn claude: {e}") from e

        # Spawn succeeded — record ownership so _close_session releases
        # exactly once (and only when we actually hold a slot).
        sess.process = proc
        sess._sem_held = True
        sess.state = SessionState.IDLE
        sess._stdout_thread = threading.Thread(
            target=self._stdout_reader_loop, args=(sess,),
            name=f"cc-stdout-{sess.conversation_id[:8]}", daemon=True,
        )
        sess._stderr_thread = threading.Thread(
            target=self._stderr_reader_loop, args=(sess,),
            name=f"cc-stderr-{sess.conversation_id[:8]}", daemon=True,
        )
        sess._stdout_thread.start()
        sess._stderr_thread.start()

    def _discard_session(self, sess: _Session) -> None:
        """Remove the session from the registry and tear down its process.
        Used when a crash is observed inside send_turn and we want a fresh
        one on the next call to _get_or_spawn."""
        with self._sessions_lock:
            if self._sessions.get(sess.conversation_id) is sess:
                self._sessions.pop(sess.conversation_id, None)
        self._close_session(sess, grace_sec=1.0)

    def _close_session(self, sess: _Session, *, grace_sec: float = 5.0) -> None:
        # Make close() idempotent: an already-CLOSED session still passes
        # through here harmlessly. State is set first so the reader thread's
        # ``finally`` block recognises this is a graceful close rather than
        # a crash (which would otherwise flip CLOSED → CRASHED).
        sess.state = SessionState.CLOSED
        proc = sess.process
        if proc is None:
            # Never spawned; nothing to release.
            return
        try:
            try:
                if proc.stdin and not proc.stdin.closed:
                    proc.stdin.close()
            except Exception:
                pass
            try:
                proc.wait(timeout=grace_sec)
            except subprocess.TimeoutExpired:
                self._kill_process(sess)
        finally:
            # Release the global slot — but only if this session actually
            # owned one. Without this guard, closing a placeholder session
            # whose ``_spawn`` failed before acquiring would silently
            # over-release and inflate the pool's bounded counter.
            if sess._sem_held:
                sess._sem_held = False
                _release_spawn_sem_safely()
        # Wake any waiter that's still pending. Mutate ``slot.result``
        # under the pending lock so we don't race with ``_deliver_result``
        # in the reader thread.
        with sess._pending_lock:
            if sess._pending is not None:
                if sess._pending.result.error is None:
                    sess._pending.result.error = ClaudeCliCrashError(
                        "session closed before result arrived"
                    )
                sess._pending.event.set()
                sess._pending = None

    def _kill_process(self, sess: _Session) -> None:
        proc = sess.process
        if proc is None:
            return
        try:
            proc.kill()
        except Exception:
            pass
        try:
            proc.wait(timeout=2.0)
        except Exception:
            pass

    # ── Internal: I/O ──

    def _write_user_messages(self, sess: _Session, messages: List[Dict[str, Any]]) -> None:
        """Push one NDJSON user event per message to stdin."""
        proc = sess.process
        if proc is None or proc.stdin is None or proc.stdin.closed:
            raise ClaudeCliCrashError("subprocess stdin is closed")
        # Concatenate so we make one syscall instead of one per message.
        out = []
        for m in messages:
            role = (m.get("role") or "").lower()
            content = _coerce_text(m.get("content"))
            if role == "user":
                line = json.dumps({"type": "user", "message": {"role": "user", "content": content}}, ensure_ascii=False)
                out.append(line)
            elif role == "assistant":
                # When replaying history after a crash we re-introduce
                # prior assistant turns so the CLI rebuilds context. CC's
                # stream-json input accepts assistant events.
                line = json.dumps({"type": "assistant", "message": {"role": "assistant", "content": content}}, ensure_ascii=False)
                out.append(line)
            elif role == "tool":
                # Should not occur in pure-text mode (we never enable tools)
                # but if a legacy conversation has tool messages, skip them.
                continue
            elif role == "system":
                # System prompt is set via --system-prompt at spawn time;
                # skip system messages from the history to avoid duplication.
                continue
            else:
                continue
        if not out:
            return
        try:
            proc.stdin.write("\n".join(out) + "\n")
            proc.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            raise ClaudeCliCrashError(f"stdin write failed: {e}") from e

    def _stdout_reader_loop(self, sess: _Session) -> None:
        """Long-running thread: parse NDJSON events from CC's stdout and
        deliver each ``result`` to whoever is currently waiting."""
        proc = sess.process
        if proc is None or proc.stdout is None:
            return
        try:
            for raw_line in proc.stdout:
                if self._shutdown.is_set():
                    break
                line = (raw_line or "").strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except Exception:
                    # CC sometimes prints non-JSON warnings; skip silently.
                    continue
                self._handle_event(sess, event)
        except Exception as e:
            self._fail_pending(sess, ClaudeCliCrashError(f"stdout reader crashed: {e}"))
        finally:
            # stdout EOF means the subprocess exited. Only flip state to
            # CRASHED if this wasn't a deliberate close — otherwise a
            # graceful eviction flips CLOSED → CRASHED at the end and a
            # subsequent ``_get_or_spawn`` thinks the session died unexpectedly.
            if sess.state not in (SessionState.CLOSED,) and not self._shutdown.is_set():
                sess.state = SessionState.CRASHED
                self._fail_pending(sess, ClaudeCliCrashError(
                    "claude subprocess exited (stdout EOF)"
                ))

    def _stderr_reader_loop(self, sess: _Session) -> None:
        proc = sess.process
        if proc is None or proc.stderr is None:
            return
        try:
            for raw_line in proc.stderr:
                if self._shutdown.is_set():
                    break
                line = (raw_line or "").strip()
                if line:
                    # Log via the existing intellistock logger if available;
                    # otherwise fall back to stderr.
                    _log(f"[{sess.conversation_id[:8]}] stderr: {line}", "yellow")
        except Exception:
            pass

    def _handle_event(self, sess: _Session, event: Dict[str, Any]) -> None:
        """Dispatch one NDJSON event from CC's stdout.

        Relevant event types observed in CC 2.1.139:
          - ``system`` / ``system:init``: subprocess ready
          - ``assistant``: partial / full assistant content
          - ``result``: terminal event for the current turn
          - ``error``: terminal error for the current turn
        """
        etype = (event.get("type") or "").lower()
        if etype == "assistant":
            msg = event.get("message") or {}
            content = msg.get("content")
            text = _extract_assistant_text(content)
            if text:
                sess._accumulated.append(text)
            return
        if etype == "result":
            self._deliver_result(sess, event)
            return
        if etype == "error":
            err_msg = str(event.get("message") or event.get("error") or "claude error")
            self._fail_pending(sess, ClaudeCliError(err_msg))
            return
        # system / system:init / other framing events: nothing to do.

    def _deliver_result(
        self,
        sess: _Session,
        event: Dict[str, Any],
        *,
        accumulated_override: Optional[str] = None,
    ) -> None:
        """Deliver a ``result`` NDJSON event to the currently-waiting slot,
        or buffer it on the session if no caller has registered yet (this
        happens in tests and in fast-completion CLI builds where the
        ``result`` line arrives before the spawning thread sets
        ``_pending``)."""
        is_error = bool(event.get("is_error"))
        result_text = event.get("result")
        finish_reason = event.get("stop_reason") or "stop"
        # Pre-resolve text outside the lock so we don't hold _pending_lock
        # while doing string work.
        text = _coerce_text(result_text) if result_text else ""
        # Mutate the slot's result entirely under _pending_lock so we don't
        # race ``_close_session`` and ``_fail_pending`` writing the same
        # fields concurrently.
        with sess._pending_lock:
            slot = sess._pending
            if slot is None:
                # No caller is waiting; stash the event so the next
                # ``send_turn`` can consume it. Only one result is
                # buffered — newer events overwrite older ones since
                # there's at most one in-flight turn per conversation.
                sess._buffered_result = event
                sess._buffered_text = text or "".join(sess._accumulated)
                return
            if is_error:
                slot.result.error = _classify_error_text(str(result_text or "claude error"))
                slot.event.set()
                return
            # Prefer the event's own result; fall back to streamed
            # assistant deltas or an explicit override (used by the
            # buffered-replay path in ``_send_one_turn_locked``).
            if not text:
                text = accumulated_override or "".join(sess._accumulated)
            slot.result.content = text
            slot.result.finish_reason = finish_reason
            slot.result.raw = event
            sess._last_result_meta = {
                "duration_ms": event.get("duration_ms"),
                "duration_api_ms": event.get("duration_api_ms"),
                "session_id": event.get("session_id"),
                "total_cost_usd": event.get("total_cost_usd"),
                "usage": event.get("usage"),
            }
        slot.event.set()

    def _fail_pending(self, sess: _Session, err: Exception) -> None:
        with sess._pending_lock:
            slot = sess._pending
            if slot is None:
                return
            if slot.result.error is None:
                slot.result.error = err
            slot.event.set()

    # ── Internal: idle sweeper ──

    def _sweeper_loop(self) -> None:
        while not self._shutdown.wait(self._sweeper_interval_sec):
            self._sweep_once()

    def _sweep_once(self) -> None:
        """Evict sessions idle past ``idle_ttl_sec``.

        Skip any session that's currently servicing a turn or has a pending
        slot — racing the sweeper against an in-flight turn would close
        the subprocess from under it. We use a try-acquire on ``send_lock``
        so we never block; if we can't get it instantly the session is
        active and we'll try again next tick.
        """
        now = time.monotonic()
        with self._sessions_lock:
            candidates = [
                (cid, sess) for cid, sess in self._sessions.items()
                if now - sess.last_activity > self._idle_ttl_sec
                and sess.state in (SessionState.IDLE, SessionState.CRASHED)
            ]
        evicted: List[_Session] = []
        for cid, sess in candidates:
            if not sess.send_lock.acquire(blocking=False):
                # Active turn — leave it for the next sweep.
                continue
            try:
                with sess._pending_lock:
                    has_pending = sess._pending is not None
                if has_pending:
                    continue
                with self._sessions_lock:
                    # Only evict if this same session object is still in
                    # the dict; a concurrent ``close()`` or ``_get_or_spawn``
                    # replace may have already swapped it out.
                    if self._sessions.get(cid) is sess:
                        self._sessions.pop(cid, None)
                        evicted.append(sess)
            finally:
                sess.send_lock.release()
        for sess in evicted:
            _log(f"idle eviction of conversation {sess.conversation_id[:8]}", "cyan")
            self._close_session(sess)


# Module-level singleton — initialised lazily.
_manager: Optional[ClaudeCliSessionManager] = None
_manager_lock = threading.Lock()


def get_session_manager() -> ClaudeCliSessionManager:
    """Return the process-wide session manager, starting the sweeper on
    first access. Safe to call from any thread."""
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = ClaudeCliSessionManager()
            _manager.start()
        return _manager


def shutdown_session_manager() -> None:
    """Called from FastAPI lifespan shutdown. Idempotent."""
    global _manager
    with _manager_lock:
        mgr = _manager
        _manager = None
    if mgr is not None:
        mgr.shutdown_all()


# ── Helpers ────────────────────────────────────────────────────────────────


_NOT_LOGGED_IN_NEEDLES = (
    "Not logged in",
    "Please run /login",
    "Please run `claude login`",
    "Please run `/login`",
)

_RATE_LIMIT_NEEDLES = (
    "rate limit",
    "quota",
    "too many requests",
    "usage limit",
    "subscription limit",
)


def _classify_error_text(text: str) -> ClaudeCliError:
    """Map a CC error-result string to the right exception class."""
    t = (text or "").strip()
    lower = t.lower()
    if any(needle in t for needle in _NOT_LOGGED_IN_NEEDLES):
        return ClaudeCliNotLoggedInError(
            "Claude Code is not logged in on this server. SSH in and run "
            "`claude` to log in, then retry."
        )
    if any(needle in lower for needle in _RATE_LIMIT_NEEDLES):
        return ClaudeCliRateLimitError(
            f"Claude Code subscription quota hit — retry in a few minutes "
            f"({t[:120]})"
        )
    return ClaudeCliError(t[:300])


def _coerce_text(content: Any) -> str:
    """CC's NDJSON content fields can be either a string or a list of
    structured content blocks. Coerce to a flat string in both cases."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for block in content:
            if isinstance(block, dict):
                if "text" in block and isinstance(block["text"], str):
                    parts.append(block["text"])
                elif block.get("type") == "text" and isinstance(block.get("content"), str):
                    parts.append(block["content"])
        return "".join(parts)
    if isinstance(content, dict):
        if isinstance(content.get("text"), str):
            return content["text"]
    try:
        return json.dumps(content, ensure_ascii=False)
    except Exception:
        return str(content)


def _extract_assistant_text(content: Any) -> str:
    """Same as _coerce_text but tailored for assistant message content."""
    return _coerce_text(content)


def _log(msg: str, color: str = "white") -> None:
    try:
        from intellistock_logger import intellistock_logger
        intellistock_logger.log(msg, color, service="ClaudeCli")
    except Exception:
        try:
            print(f"[ClaudeCli] {msg}", file=sys.stderr, flush=True)
        except Exception:
            pass


# ── Chatbot entry point ────────────────────────────────────────────────────


def call_claude_cli_chat(
    *,
    conversation_id: str,
    messages: List[Dict[str, Any]],
    system_prompt: str,
    model: str,
    cli_path: str = "claude",
    extra_args: Optional[List[str]] = None,
    timeout_sec: Optional[int] = None,
) -> Dict[str, Any]:
    """Send one chatbot turn to a persistent ``claude`` subprocess.

    The first call for a given ``conversation_id`` spawns; subsequent calls
    reuse the warm process. Returns the normalised
    ``{content, tool_calls, finish_reason, raw}`` shape that the chatbot's
    provider abstraction expects.
    """
    if not conversation_id:
        raise ValueError("conversation_id is required for claude-cli chatbot calls")
    if not model:
        raise ValueError("model is required")
    extra_args = list(extra_args or [])
    mgr = get_session_manager()
    return mgr.send_turn(
        conversation_id=conversation_id,
        messages=messages,
        system_prompt=system_prompt or "",
        model=model,
        cli_path=cli_path or "claude",
        extra_args=extra_args,
        timeout_sec=timeout_sec,
    )


# ── Structured entry point (strategies) ────────────────────────────────────


def call_claude_cli_structured(
    *,
    model: str,
    system_prompt: str,
    user_prompt: str,
    output_schema: Any,                # type[BaseModel] but we avoid the import
    cli_path: str = "claude",
    extra_args: Optional[List[str]] = None,
    timeout_sec: Optional[int] = None,
) -> Any:
    """Spawn-per-call structured-output run. ``output_schema`` must be a
    Pydantic v2 ``BaseModel`` subclass.

    Returns a validated instance of ``output_schema`` on success. Raises one
    of the ``ClaudeCli*Error`` types on failure.
    """
    if not model:
        raise ValueError("model is required")
    if output_schema is None:
        raise ValueError("output_schema is required")
    extra_args = list(extra_args or [])
    timeout = timeout_sec or CLAUDE_CLI_TURN_TIMEOUT_SEC
    # Resolve+validate cli_path BEFORE acquiring the global semaphore so
    # an invalid binary doesn't burn a slot. Raises if path doesn't
    # resolve to an allowlisted ``claude`` binary.
    resolved_cli = _resolve_cli_path(cli_path or "claude")

    try:
        schema_dict = output_schema.model_json_schema()
    except Exception as e:
        raise ClaudeCliError(
            f"output_schema must be a Pydantic v2 BaseModel "
            f"with model_json_schema(): {e}"
        ) from e
    schema_json = json.dumps(schema_dict, ensure_ascii=False)

    argv = _build_structured_argv(
        cli_path=resolved_cli,
        model=model,
        system_prompt=system_prompt or "",
        json_schema=schema_json,
        extra_args=extra_args,
    )

    if not _GLOBAL_SPAWN_SEM.acquire(timeout=CLAUDE_CLI_SPAWN_TIMEOUT_SEC):
        raise ClaudeCliError(
            f"global subprocess cap ({CLAUDE_CLI_MAX_CONCURRENT}) reached"
        )
    try:
        try:
            proc = subprocess.run(
                argv,
                input=user_prompt or "",
                capture_output=True,
                text=True,
                timeout=timeout,
                encoding="utf-8",
                errors="replace",
                **_platform_popen_kwargs(),
            )
        except FileNotFoundError as e:
            raise ClaudeCliNotInstalledError(
                f"claude binary not found at {resolved_cli!r}"
            ) from e
        except subprocess.TimeoutExpired as e:
            raise ClaudeCliTimeoutError(
                f"claude structured call timed out after {timeout}s"
            ) from e
    finally:
        _release_spawn_sem_safely()

    stdout = (proc.stdout or "").strip()
    if not stdout:
        stderr_tail = (proc.stderr or "").strip()[:300]
        raise ClaudeCliError(
            f"claude produced no stdout (exit={proc.returncode}); stderr={stderr_tail!r}"
        )

    try:
        result_envelope = json.loads(stdout)
    except json.JSONDecodeError as e:
        raise ClaudeCliError(
            f"claude returned non-JSON stdout: {stdout[:300]!r}"
        ) from e

    if result_envelope.get("is_error"):
        err = _classify_error_text(str(result_envelope.get("result") or ""))
        raise err

    # The model's structured answer is nested inside .result as a string.
    raw_result = result_envelope.get("result")
    if raw_result is None:
        raise ClaudeCliError("claude result envelope missing `result` field")

    payload: Any
    if isinstance(raw_result, str):
        try:
            payload = json.loads(raw_result)
        except json.JSONDecodeError as e:
            raise ClaudeCliValidationError(
                f"claude result is not valid JSON: {str(raw_result)[:200]!r}"
            ) from e
    else:
        payload = raw_result

    try:
        return output_schema.model_validate(payload)
    except Exception as e:
        raise ClaudeCliValidationError(
            f"claude output failed Pydantic validation: {e}"
        ) from e


# ── Connection test (/api/models/{id}/test-cli) ────────────────────────────


def test_claude_cli(
    *,
    cli_path: str = "claude",
    model: str = "claude-haiku-4-5",
    timeout_sec: int = 30,
) -> Dict[str, Any]:
    """One-shot probe used by the 'Test connection' button. Reports CC
    version, login status, and a tiny prompt round-trip."""
    started = time.monotonic()

    # Resolve+validate cli_path first. ``_resolve_cli_path`` raises on a
    # missing binary, a non-allowlisted basename, or a symlink that
    # resolves outside the allowlist. We translate those into structured
    # response objects rather than letting the exception propagate to the
    # API layer.
    try:
        resolved_cli = _resolve_cli_path(cli_path or "claude")
    except ClaudeCliNotInstalledError as e:
        return {
            "ok": False, "version": None, "logged_in": False, "model_response": None,
            "error": str(e),
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        }
    except ClaudeCliError as e:
        return {
            "ok": False, "version": None, "logged_in": False, "model_response": None,
            "error": str(e),
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        }

    # 1) `claude --version` — cheap, ~170 ms.
    try:
        ver = subprocess.run(
            [resolved_cli, "--version"],
            capture_output=True, text=True, timeout=10,
            encoding="utf-8", errors="replace",
            **_platform_popen_kwargs(),
        )
    except FileNotFoundError:
        return {
            "ok": False,
            "version": None,
            "logged_in": False,
            "model_response": None,
            "error": (
                f"claude binary not found at {cli_path!r}. Install with: "
                "npm i -g @anthropic-ai/claude-code"
            ),
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        }
    except subprocess.TimeoutExpired:
        return {
            "ok": False, "version": None, "logged_in": False, "model_response": None,
            "error": "claude --version timed out (10s)",
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        }
    version = (ver.stdout or "").strip().splitlines()[0] if ver.stdout else None

    # 2) Tiny prompt round-trip — detects login + verifies the model alias.
    argv = [
        resolved_cli, "-p",
        "--output-format", "json",
        "--model", model,
        *_SAFETY_FLAGS,
    ]
    try:
        proc = subprocess.run(
            argv,
            input="ok\n",
            capture_output=True, text=True, timeout=timeout_sec,
            encoding="utf-8", errors="replace",
            **_platform_popen_kwargs(),
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False, "version": version, "logged_in": False, "model_response": None,
            "error": f"claude -p timed out after {timeout_sec}s",
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        }

    stdout = (proc.stdout or "").strip()
    if not stdout:
        return {
            "ok": False, "version": version, "logged_in": False, "model_response": None,
            "error": (proc.stderr or "").strip()[:300] or "no output from claude",
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        }

    try:
        envelope = json.loads(stdout)
    except Exception:
        return {
            "ok": False, "version": version, "logged_in": False, "model_response": None,
            "error": f"claude returned non-JSON: {stdout[:200]!r}",
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        }

    if envelope.get("is_error"):
        result_text = str(envelope.get("result") or "")
        if any(n in result_text for n in _NOT_LOGGED_IN_NEEDLES):
            return {
                "ok": False, "version": version, "logged_in": False, "model_response": None,
                "error": (
                    "Claude Code is not logged in on this server. SSH in and "
                    "run `claude` to log in."
                ),
                "elapsed_ms": int((time.monotonic() - started) * 1000),
            }
        return {
            "ok": False, "version": version, "logged_in": False, "model_response": None,
            "error": result_text[:300] or "claude reported an error",
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        }

    reply = str(envelope.get("result") or "").strip()
    return {
        "ok": True,
        "version": version,
        "logged_in": True,
        "model_response": reply[:120],
        "error": None,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
    }


__all__ = [
    "ClaudeCliError",
    "ClaudeCliNotInstalledError",
    "ClaudeCliNotLoggedInError",
    "ClaudeCliRateLimitError",
    "ClaudeCliTimeoutError",
    "ClaudeCliCrashError",
    "ClaudeCliValidationError",
    "validate_extra_args",
    "call_claude_cli_chat",
    "call_claude_cli_structured",
    "test_claude_cli",
    "get_session_manager",
    "shutdown_session_manager",
    "ClaudeCliSessionManager",
]
