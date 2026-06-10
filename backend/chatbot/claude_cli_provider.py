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
import re
import secrets
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


# Telemetry — defensive import so a missing/broken module never blocks LLM calls.
try:
    from llm_telemetry import record_llm_call as _telemetry_record
except Exception:
    def _telemetry_record(**_kwargs):
        return None


def _safe_record(**kwargs) -> None:
    try:
        _telemetry_record(**kwargs)
    except Exception:
        pass


# ── Configuration (env-overridable) ────────────────────────────────────────

CLAUDE_CLI_MAX_CONCURRENT = int(os.environ.get("CLAUDE_CLI_MAX_CONCURRENT", "10"))
CLAUDE_CLI_IDLE_TTL_SEC = int(os.environ.get("CLAUDE_CLI_IDLE_TTL_SEC", "3600"))
CLAUDE_CLI_SPAWN_TIMEOUT_SEC = int(os.environ.get("CLAUDE_CLI_SPAWN_TIMEOUT_SEC", "30"))
CLAUDE_CLI_TURN_TIMEOUT_SEC = int(os.environ.get("CLAUDE_CLI_TURN_TIMEOUT_SEC", "120"))
CLAUDE_CLI_SWEEPER_INTERVAL_SEC = int(os.environ.get("CLAUDE_CLI_SWEEPER_INTERVAL_SEC", "300"))

# Global concurrency cap (chatbot + strategies combined).
_GLOBAL_SPAWN_SEM = threading.BoundedSemaphore(value=max(1, CLAUDE_CLI_MAX_CONCURRENT))


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


def _rollback_optimistic_user(conversation_id: str) -> None:
    """Drop the optimistic user message appended at the start of this turn.

    Called from both the pre-chat exception path (``call_claude_cli_chat``
    raised) and the post-chat exception path (response failed JSON or
    Pydantic validation). Preserves the invariant that
    ``_structured_history`` only contains successfully-exchanged turns.
    """
    with _structured_history_lock:
        hist = _structured_history.get(conversation_id, [])
        if hist and hist[-1].get("role") == "user":
            hist.pop()
        if not hist:
            _structured_history.pop(conversation_id, None)


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


# Env vars that make ``claude`` authenticate as something OTHER than the
# operator's Claude subscription (OAuth). Claude Code's auth precedence is
# ``ANTHROPIC_API_KEY`` / ``ANTHROPIC_AUTH_TOKEN`` BEFORE the subscription
# OAuth, so a deployment that sets either (e.g. for a different provider)
# silently hijacks every claude-cli call: the request goes out with the
# API key and the server returns ``401 Invalid authentication credentials``
# even though ``claude auth status`` reports the subscription as logged in.
# We run claude-cli strictly in subscription mode, so we scrub these from
# every spawned child env. (We inherit the parent env with ``os.environ
# .copy()`` to keep PATH / HOME / locale, then strip just the auth keys.)
_SUBSCRIPTION_CONFLICTING_ENV = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
)


def _strip_api_key_env(env: Dict[str, str]) -> Dict[str, str]:
    """Remove API-key/token env vars so ``claude`` uses the subscription
    OAuth, not an inherited key. Mutates and returns *env* for chaining:
    ``child_env = _strip_api_key_env(os.environ.copy())``."""
    for key in _SUBSCRIPTION_CONFLICTING_ENV:
        env.pop(key, None)
    return env


# Stripping the *process* env (above) is not enough on its own: Claude Code
# also loads an ``env`` block out of ``settings.json`` (user/project/managed)
# and injects it into the session. A deployment with
# ``{"env": {"ANTHROPIC_API_KEY": "…"}}`` in its claude settings therefore
# still hijacks every call — the request goes out with that key and the
# server returns 401, even though ``claude auth status`` reports the
# subscription as logged in and even right after a fresh re-auth.
#
# A ``--settings`` CLI flag deep-merges with the HIGHEST precedence over
# user/project settings, so blanking the keys here makes claude ignore the
# settings-file values and fall back to the subscription OAuth. This works
# regardless of where the key is configured or whether we run in a copied
# runtime HOME. Verified empirically: the healthy subscription path is
# unaffected (still succeeds), while a stale settings ``env`` key that
# otherwise 401s is neutralized. We inject this on every *model* invocation
# (chat / structured / test-cli); the ``claude auth …`` management commands
# don't need it. ``--settings`` is in ``_HARD_REJECTED_FLAGS`` so a user's
# extra_args can never add a second, conflicting one.
_FORCE_SUBSCRIPTION_SETTINGS = json.dumps(
    {"env": {"ANTHROPIC_API_KEY": "", "ANTHROPIC_AUTH_TOKEN": ""}}
)
_FORCE_SUBSCRIPTION_ARGS = ["--settings", _FORCE_SUBSCRIPTION_SETTINGS]


# Argv that opens up the IntelliStock MCP server (and ONLY the
# IntelliStock MCP server — CC's built-in Bash / Read / Edit / etc. stay
# disabled). The session populates ``--mcp-config`` with the per-session
# path at spawn time.
#
# Permission-mode lab notes (CC 2.1.x running as root inside Docker):
#
#   * ``--dangerously-skip-permissions`` — refused at startup, root.
#   * ``--permission-mode bypassPermissions`` — same root refusal.
#   * ``--permission-mode acceptEdits`` — starts cleanly, lists MCP
#     tools, but silently BLOCKS the actual invocation (verified in
#     production: tools/list HTTP'd through, tools/call never did).
#     CC doesn't classify MCP tools as "edit" for permission purposes.
#   * **No ``--permission-mode`` at all** — relies on CC's default
#     behaviour in ``-p`` mode (which skips the workspace-trust
#     dialog automatically). With ``--allowedTools mcp__*`` granting
#     the namespace explicitly, MCP calls go through. This is the
#     option we ship.
#
# Allow-list note:
#   ``--allowedTools mcp__*`` (broad MCP glob) rather than
#   ``mcp__intellistock__*``. The broader pattern is supported across
#   more CC versions. With ``--strict-mcp-config`` pointing only at
#   our config, this is functionally equivalent to restricting to
#   IntelliStock tools.
_MCP_FLAGS_TEMPLATE = [
    "--strict-mcp-config",
    "--no-session-persistence",
    "--disable-slash-commands",
    "--dangerously-skip-permissions",
    "--allowedTools", "mcp__*",
]


# Drop privileges to this OS user when spawning the ``claude`` CLI
# (CC refuses --dangerously-skip-permissions when running as root, and
# every other permission-mode either trips the same root check or
# silently blocks MCP tool invocation). The Dockerfile creates
# ``claudeuser`` (UID 1000) for this purpose and sets the env var via
# ``ENV CLAUDE_CLI_RUNTIME_USER=claudeuser`` so the privilege drop is
# active by default for all in-image installs.
#
# Fallback: if neither env var is set but we're running as root AND
# ``claudeuser`` exists in /etc/passwd, auto-pick it. This catches
# operators whose orchestrator strips ENV from the image (some
# Dockploy / Coolify configurations do this).


def _detect_runtime_user_default() -> Optional[str]:
    if sys.platform == "win32":
        return None
    try:
        if os.geteuid() != 0:
            return None     # we're already non-root; no drop needed
    except Exception:
        return None
    try:
        import pwd
        pwd.getpwnam("claudeuser")
        return "claudeuser"
    except Exception:
        return None


_CC_RUNTIME_USER = (
    (os.environ.get("CLAUDE_CLI_RUNTIME_USER") or "").strip()
    or _detect_runtime_user_default()
    or None
)
_CC_RUNTIME_UID_ENV = (os.environ.get("CLAUDE_CLI_RUNTIME_UID") or "").strip()


def _runtime_user_kwargs() -> Dict[str, Any]:
    """Popen kwargs that drop privileges to ``CLAUDE_CLI_RUNTIME_USER``
    (or its numeric UID via ``CLAUDE_CLI_RUNTIME_UID``). Returns ``{}``
    when no drop is configured — the bare process UID is used.

    NOTE: in practice we prefer wrapping argv with ``runuser`` (see
    ``_wrap_argv_for_runtime_user``) so the privilege drop is visible
    in process listings / logs. This function is retained for
    completeness; callers that wrap argv should NOT use both."""
    if sys.platform == "win32":
        # No POSIX user/group switching on Windows.
        return {}
    # Python 3.9+ accepts ``user=`` for Popen; both string usernames
    # and integer UIDs are valid.
    target: Any = None
    if _CC_RUNTIME_UID_ENV:
        try:
            target = int(_CC_RUNTIME_UID_ENV)
        except ValueError:
            target = _CC_RUNTIME_USER
    elif _CC_RUNTIME_USER:
        target = _CC_RUNTIME_USER
    if target is None:
        return {}
    return {"user": target}


def _wrap_argv_for_runtime_user(argv: List[str]) -> List[str]:
    """Wrap ``argv`` with ``runuser`` (or ``su``) so the spawned process
    actually runs as ``CLAUDE_CLI_RUNTIME_USER``. We use this in place
    of Popen's ``user=`` kwarg because the latter has been observed to
    silently no-op inside containers where libc capabilities are
    restricted — the symptom is CC continuing to see ``uid=0`` and
    refusing ``--dangerously-skip-permissions``.

    Picks the first available wrapper:
      1. ``runuser -p -u <user> --``  (util-linux, always available on
         python:3.11-slim Debian-based images)
      2. ``su -p -s /bin/sh -c "<argv>" <user>``  (fallback; requires
         quoting the argv into a single shell command)

    ``-p`` / ``--preserve-environment`` is critical: WITHOUT it,
    runuser/su reset HOME to the target user's home (``/home/claudeuser``
    on our image). Our ``HOME=/tmp/cc-home-<sess>`` env on Popen is
    overridden, so CC looks for credentials in ``/home/claudeuser/.claude``
    — which is empty — and reports ``Not logged in``. Preserving the
    env keeps the per-session runtime home visible to the subprocess.

    Returns the argv unchanged if no runtime user is configured or if
    we're on Windows.
    """
    if sys.platform == "win32":
        return argv
    if not _CC_RUNTIME_USER:
        return argv
    import shutil
    runuser_path = shutil.which("runuser")
    if runuser_path:
        # ``--`` terminates runuser's own option parsing so flags in
        # argv (e.g. ``--strict-mcp-config``) are passed through.
        # ``-p`` preserves HOME / USER / SHELL / LOGNAME from our
        # Popen ``env={...}`` so the per-session runtime HOME lands.
        return [runuser_path, "-p", "-u", _CC_RUNTIME_USER, "--", *argv]
    # Fallback to su; quote argv into a single -c argument.
    su_path = shutil.which("su")
    if su_path:
        quoted = " ".join(shlex.quote(a) for a in argv)
        return [su_path, "-p", "-s", "/bin/sh", "-c", quoted, _CC_RUNTIME_USER]
    # Neither available — return as-is and let Popen's user= take over.
    _log(
        "no runuser/su available; relying on Popen(user=) which may "
        "no-op silently in some container configurations",
        "yellow",
    )
    return argv


def _drop_user_uid() -> Optional[int]:
    """Return the numeric UID we drop to, or ``None`` if no drop is set."""
    if sys.platform == "win32":
        return None
    if _CC_RUNTIME_UID_ENV:
        try:
            return int(_CC_RUNTIME_UID_ENV)
        except ValueError:
            pass
    if _CC_RUNTIME_USER:
        try:
            import pwd
            return pwd.getpwnam(_CC_RUNTIME_USER).pw_uid
        except Exception:
            return None
    return None


# One-time, process-wide path to the ``.claude.json`` source we'll copy
# into each session's runtime HOME. Resolved once on manager start by
# ``_init_claude_state_once``. Empty when no privilege drop is set up.
_CLAUDE_JSON_SOURCE: str = ""
_CLAUDE_STATE_INIT_LOCK = threading.Lock()
# Process-wide shared runtime HOME for spawn-per-call paths
# (call_claude_cli_structured). The chatbot's persistent session has its
# OWN per-conversation runtime_home — these are independent.
_STRUCTURED_RUNTIME_HOME: str = ""
_STRUCTURED_RUNTIME_HOME_LOCK = threading.Lock()
# Per-call spawn-id counter so concurrent worker logs are distinguishable.
# Sonnet+reasoning_effort=high routinely takes 30-90s per structured call;
# without per-spawn entry/exit logs the broker appears frozen while 4
# workers run in parallel. The counter pairs entry/exit lines.
_STRUCTURED_SPAWN_COUNTER: int = 0
_STRUCTURED_SPAWN_LOCK = threading.Lock()

# Thread-local capture of the most recent structured-call envelope's usage
# block. Telemetry reads this in llm_utils._call_claude_cli_structured_from_strategy
# so the LLMUsage row gets real input_tokens/output_tokens/cache_*_tokens instead
# of an empty dict. Without this, the dashboard would show zero tokens for the
# project's primary provider (claude-cli) because the envelope's usage block is
# parsed and discarded inside this module.
_LAST_STRUCT_ENVELOPE_USAGE = threading.local()


def get_last_struct_envelope_usage() -> Dict[str, Any]:
    """Return the most recent structured-call envelope usage dict for the
    current thread. Returns ``{}`` if no call has happened yet, or if the
    envelope didn't carry a usage block. The thread-local is overwritten at
    the end of every ``call_claude_cli_structured`` invocation (success path
    only — failure paths leave the previous value, but callers gate reads on
    their own success flag so stale data isn't observable as success).
    """
    return getattr(_LAST_STRUCT_ENVELOPE_USAGE, "data", {}) or {}
# Toggle full-stdout dumps to the log for diagnosing model-output issues.
# DEFAULT-OFF in prod — the structured_output reader fix (40ff345) is
# stable, so the per-spawn ~1500-char repr() dumps add log volume + I/O
# without diagnostic value during a normal backtest. Set
# CLAUDE_CLI_DUMP_STRUCTURED_STDOUT=1 to re-enable when debugging.
_DUMP_STRUCTURED_STDOUT = str(
    os.environ.get("CLAUDE_CLI_DUMP_STRUCTURED_STDOUT", "0")
).strip().lower() in ("1", "true", "yes", "y", "on")


def _init_claude_state_once() -> None:
    """Resolve the canonical ``.claude.json`` source path ONCE per process.

    If the operator's HOME has a live ``$HOME/.claude.json`` (bind-mounted
    or whatever), use that. Otherwise, restore the most recent backup
    from ``.claude/backups/.claude.json.backup.*`` into a stable local
    cache file and use the cache from then on. This avoids the noisy
    per-call "restoring .claude.json from backup" log line and shaves
    a glob + copy off the hot path of every CC spawn.

    Thread-safe and idempotent — first caller wins, subsequent callers
    return immediately. Without the lock, multiple workers race past the
    fast-path guard and the "restored from backup" log fires once per
    worker on cold start.
    """
    global _CLAUDE_JSON_SOURCE
    if _CLAUDE_JSON_SOURCE:
        return
    with _CLAUDE_STATE_INIT_LOCK:
        # Re-check inside the lock — another thread may have populated
        # while we were waiting.
        if _CLAUDE_JSON_SOURCE:
            return
        src = os.environ.get("HOME") or "/root"
        live = os.path.join(src, ".claude.json")
        if os.path.isfile(live):
            _CLAUDE_JSON_SOURCE = live
            _log(f"claude.json source = live host file at {live!r}", "cyan")
            return
        # No live file — try to restore the most recent backup.
        import glob
        src_claude = os.path.join(src, ".claude")
        backups = sorted(
            glob.glob(os.path.join(src_claude, "backups", ".claude.json.backup.*")),
            reverse=True,
        )
        if not backups:
            _log(
                f"no .claude.json at {live!r} and no backups in "
                f"{os.path.join(src_claude, 'backups')!r} — CC will likely "
                "report 'Not logged in'. Run `claude` on the host to "
                "regenerate auth state.",
                "yellow",
            )
            return
        # Materialise a stable cache file owned by the process so per-session
        # prep can copy it without re-running the glob. /tmp is fine — the
        # file lives only as long as this container process.
        cache_dir = "/tmp/cc-claude-state"
        try:
            os.makedirs(cache_dir, mode=0o755, exist_ok=True)
        except Exception as e:
            _log(f"failed to create cache dir {cache_dir!r}: {e}", "yellow")
            return
        cache_path = os.path.join(cache_dir, ".claude.json")
        try:
            import shutil
            shutil.copy2(backups[0], cache_path)
            os.chmod(cache_path, 0o644)
            _CLAUDE_JSON_SOURCE = cache_path
            _log(
                f"restored .claude.json from {backups[0]!r} to cache "
                f"{cache_path!r} (one-time, at startup)",
                "cyan",
            )
        except Exception as e:
            _log(f"failed to restore .claude.json from backup: {e}", "yellow")


def _scrub_json_file(path: str, mutate) -> bool:
    """Load the JSON object at *path*, apply ``mutate(dict)`` in place, and
    rewrite the file only if it actually changed. Returns True on a write.
    Silent no-op for missing / unreadable / non-object files."""
    if not os.path.isfile(path):
        return False
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return False
    if not isinstance(data, dict):
        return False
    before = json.dumps(data, sort_keys=True)
    try:
        mutate(data)
    except Exception:
        return False
    if json.dumps(data, sort_keys=True) == before:
        return False
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        return True
    except Exception:
        return False


def _scrub_settings_dict(d: Dict[str, Any]) -> None:
    # apiKeyHelper runs a command whose stdout becomes the API key; an env
    # block can pin ANTHROPIC_API_KEY/ANTHROPIC_AUTH_TOKEN. Both override the
    # subscription OAuth and 401 if stale.
    d.pop("apiKeyHelper", None)
    env = d.get("env")
    if isinstance(env, dict):
        for k in _SUBSCRIPTION_CONFLICTING_ENV:
            env.pop(k, None)
        if not env:
            d.pop("env", None)


def _neutralize_api_key_config(runtime_home: str) -> None:
    """Strip every non-subscription auth source from a COPIED runtime HOME so
    claude-cli authenticates with the operator's subscription OAuth, never a
    stale API key. Claude Code's auth precedence puts an API key (env var,
    settings ``env`` block, ``apiKeyHelper``, or a stored ``primaryApiKey``
    in ``.claude.json``) BEFORE the subscription, so any one of these — left
    over from an earlier API-key setup — silently 401s every claude-cli call
    even though ``claude auth status`` reports the subscription as logged in.

    We scrub the per-call COPY only; the operator's real ``~/.claude`` files
    are never touched. Combined with ``_strip_api_key_env`` (process env) and
    ``_FORCE_SUBSCRIPTION_ARGS`` (the ``--settings`` env override), this
    covers all four sources.
    """
    _scrub_json_file(
        os.path.join(runtime_home, ".claude.json"),
        lambda d: d.pop("primaryApiKey", None),
    )
    sdir = os.path.join(runtime_home, ".claude")
    for name in ("settings.json", "settings.local.json"):
        _scrub_json_file(os.path.join(sdir, name), _scrub_settings_dict)


def _get_structured_runtime_home() -> str:
    """Process-wide shared runtime HOME for the spawn-per-call structured
    path. Created once on first use, reused by every subsequent call.

    Without this, every ``call_claude_cli_structured`` did a full
    ``shutil.copytree`` of ``~/.claude/`` (~50MB) per call, which on a
    backtest with thousands of LLM calls dominates wall time and burns
    disk IO. The persistent-session chatbot path doesn't have this
    problem because it copies ONCE per session and reuses.

    Thread-safe. Returns process HOME unchanged when no privilege drop
    is configured (i.e. ``_drop_user_uid()`` returns None).
    """
    global _STRUCTURED_RUNTIME_HOME
    if _STRUCTURED_RUNTIME_HOME:
        return _STRUCTURED_RUNTIME_HOME
    target_uid = _drop_user_uid()
    if target_uid is None:
        # No privilege drop — caller will pass process HOME through.
        return os.environ.get("HOME") or "/root"
    with _STRUCTURED_RUNTIME_HOME_LOCK:
        if _STRUCTURED_RUNTIME_HOME:
            return _STRUCTURED_RUNTIME_HOME
        # _prepare_runtime_home_for_id creates a fresh tmp HOME with the
        # full copytree. We do that ONCE and stash it.
        path = _prepare_runtime_home_for_id("shared-structured")
        if path and path.startswith("/tmp/cc-home-"):
            _STRUCTURED_RUNTIME_HOME = path
            _log(
                f"structured runtime HOME materialised at {path!r} "
                "(shared across all spawn-per-call structured-output calls)",
                "cyan",
            )
        else:
            # _prepare_runtime_home returned process HOME (no drop) — use it.
            _STRUCTURED_RUNTIME_HOME = path
        return _STRUCTURED_RUNTIME_HOME


def _prepare_runtime_home_for_id(id_hint: str) -> str:
    """Same as ``_prepare_runtime_home`` but takes a free-form id_hint
    used only for the temp-dir prefix. Lets non-session callers (test
    endpoint, structured-output one-shot) reuse the same auth-prep path
    so they share the operator's login the same way the chatbot does.
    """
    class _Stub:
        conversation_id = (id_hint or "adhoc")[:24] or "adhoc"
    return _prepare_runtime_home(_Stub())  # type: ignore[arg-type]


def _prepare_runtime_home(sess: "_Session") -> str:
    """Copy the operator's ``~/.claude`` from the parent process's HOME
    into a fresh temp directory owned by the runtime user. Returns the
    absolute path to use as ``HOME`` for the spawned CC subprocess.

    Why copy: the bind-mounted host ``~/.claude`` is typically mode
    0700 owned by host root. When we drop to UID 1000, that user
    cannot read the credentials. Copying with chowned ownership gives
    the subprocess a readable view without altering the host files.

    When no runtime user drop is configured (e.g. local dev as a
    non-root user), we return the parent process's HOME unchanged.
    """
    import shutil
    import tempfile
    target_uid = _drop_user_uid()
    src = os.environ.get("HOME") or "/root"
    src_claude = os.path.join(src, ".claude")
    if target_uid is None or not os.path.isdir(src_claude):
        return src
    try:
        runtime_dir = tempfile.mkdtemp(
            prefix=f"cc-home-{sess.conversation_id[:8]}-",
            dir="/tmp",
        )
    except Exception as e:
        _log(f"failed to allocate runtime home: {e}", "yellow")
        return src
    dst_claude = os.path.join(runtime_dir, ".claude")
    try:
        shutil.copytree(src_claude, dst_claude, symlinks=True)
        # CC also reads ``$HOME/.claude.json`` (sibling of .claude/).
        # The source path was resolved once at manager startup by
        # ``_init_claude_state_once`` — either the live host file or
        # a cached restoration from the latest backup. Empty string
        # means neither was available, in which case we let CC error
        # naturally rather than restoring a stale backup per call.
        if _CLAUDE_JSON_SOURCE:
            shutil.copy2(_CLAUDE_JSON_SOURCE, os.path.join(runtime_dir, ".claude.json"))
        # Scrub any non-subscription auth source from the COPY (a stored
        # primaryApiKey / apiKeyHelper / env API key would otherwise override
        # the subscription OAuth and 401 every call). Done before the chown
        # walk so the rewritten files get the runtime-user ownership too.
        _neutralize_api_key_config(runtime_dir)
        # Recursively chown to the runtime user so the dropped
        # subprocess can read everything. mode is left as-is from the
        # source.
        for root_dir, dirs, files in os.walk(runtime_dir):
            try:
                os.chown(root_dir, target_uid, target_uid)
            except Exception:
                pass
            for f in files:
                try:
                    os.chown(os.path.join(root_dir, f), target_uid, target_uid)
                except Exception:
                    pass
        # Top-level files (.claude.json) — chown explicitly in case
        # they weren't caught by the walk above.
        cj = os.path.join(runtime_dir, ".claude.json")
        if os.path.isfile(cj):
            try:
                os.chown(cj, target_uid, target_uid)
            except Exception:
                pass
        # Ensure the top-level home is +rx so the user can resolve it.
        os.chmod(runtime_dir, 0o755)
        # Diagnostic: log what's actually in the .claude dir post-copy so
        # we can tell whether credentials are present and readable by the
        # runtime user. Auth lives in either ``.credentials.json`` (older
        # builds) or ``credentials.json`` (newer) — both surface here.
        try:
            entries = sorted(os.listdir(dst_claude))
            cred_path = None
            for name in (".credentials.json", "credentials.json"):
                p = os.path.join(dst_claude, name)
                if os.path.isfile(p):
                    cred_path = p
                    break
            cred_info = "no credentials file found"
            if cred_path:
                st = os.stat(cred_path)
                cred_info = (
                    f"{os.path.basename(cred_path)}: "
                    f"size={st.st_size} mode={oct(st.st_mode & 0o777)} "
                    f"uid={st.st_uid}"
                )
            _log(
                f"runtime_home contents: dirs/files={entries[:10]}"
                f"{'…' if len(entries) > 10 else ''}; {cred_info}",
                "cyan",
            )
        except Exception as e:
            _log(f"runtime_home introspection failed: {e}", "yellow")
    except Exception as e:
        _log(f"failed to populate runtime home: {e}", "yellow")
        try:
            shutil.rmtree(runtime_dir, ignore_errors=True)
        except Exception:
            pass
        return src
    return runtime_dir


def _cleanup_runtime_home(path: str) -> None:
    if not path or path == (os.environ.get("HOME") or "/root"):
        return
    if not path.startswith("/tmp/cc-home-"):
        return
    try:
        import shutil
        shutil.rmtree(path, ignore_errors=True)
    except Exception:
        pass


def _mcp_runtime_dir() -> str:
    """Directory where per-session ``.mcp.json`` files live.
    Configurable via ``CLAUDE_CLI_MCP_DIR``; defaults to the OS temp dir
    plus an ``intellistock-mcp`` subdirectory we create on demand."""
    base = (os.environ.get("CLAUDE_CLI_MCP_DIR") or "").strip()
    if not base:
        import tempfile
        base = os.path.join(tempfile.gettempdir(), "intellistock-mcp")
    try:
        os.makedirs(base, exist_ok=True)
    except Exception:
        pass
    return base


def _mcp_server_command_args() -> List[str]:
    """How to invoke the IntelliStock MCP server. Defaults to running
    ``python <path-to-intellistock_mcp_server.py>``; the path resolves
    relative to this file so it works inside the container regardless
    of cwd."""
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "intellistock_mcp_server.py")
    # Allow operator override (e.g. a wrapper that activates a venv first).
    override = (os.environ.get("CLAUDE_CLI_MCP_COMMAND") or "").strip()
    if override:
        return shlex.split(override) + [script]
    return [sys.executable or "python", script]


def _mcp_callback_url() -> str:
    """Base URL the MCP server uses to call back into IntelliStock.

    Defaults to ``http://127.0.0.1:<port>``: the MCP server is spawned
    by CC which is in turn spawned by the chatbot handler running in
    the api container, so the server-side endpoint is co-resident. The
    loopback path bypasses Docker's internal DNS (which is flaky inside
    a container calling its own service name on some setups) and works
    regardless of the ``api`` service name. Operators can override with
    ``CLAUDE_CLI_MCP_CALLBACK_URL`` for split deployments.
    """
    explicit = (os.environ.get("CLAUDE_CLI_MCP_CALLBACK_URL") or "").strip()
    if explicit:
        return explicit.rstrip("/")
    port = (os.environ.get("API_PORT") or "8011").strip() or "8011"
    return f"http://127.0.0.1:{port}"


def _write_session_mcp_config(sess: "_Session") -> str:
    """Materialise a per-session ``.mcp.json`` registering the
    IntelliStock MCP server with the session's token/conv-id env. The
    file is written 0600 and removed when the session is closed.

    When a runtime-user drop is configured, the file is also chowned to
    that UID so the dropped subprocess can read it. The auth token
    stays restricted (0600) — only the runtime user can read it.
    """
    cmd = _mcp_server_command_args()
    config = {
        "mcpServers": {
            "intellistock": {
                "command": cmd[0],
                "args": cmd[1:],
                "env": {
                    "INTELLISTOCK_MCP_URL": _mcp_callback_url(),
                    "INTELLISTOCK_MCP_TOKEN": sess.mcp_token,
                    "INTELLISTOCK_CONVERSATION_ID": sess.conversation_id,
                    "INTELLISTOCK_USER_ID": sess.user_id or "",
                },
            }
        }
    }
    runtime_dir = _mcp_runtime_dir()
    path = os.path.join(
        runtime_dir,
        f"mcp-{sess.conversation_id[:8]}-{secrets.token_hex(4)}.json",
    )
    try:
        # Restrictive perms — file contains an auth token.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, json.dumps(config, ensure_ascii=False).encode("utf-8"))
        finally:
            os.close(fd)
    except Exception as e:
        raise ClaudeCliError(f"failed to write per-session MCP config: {e}") from e
    # Chown to the runtime user so the dropped subprocess can read it.
    runtime_uid = _drop_user_uid()
    if runtime_uid is not None:
        try:
            os.chown(path, runtime_uid, runtime_uid)
        except Exception as e:
            _log(f"chown of mcp-config to UID {runtime_uid} failed: {e}", "yellow")
    return path


def _cleanup_session_mcp_config(sess: "_Session") -> None:
    path = sess.mcp_config_path
    if not path:
        return
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    except Exception as e:
        _log(f"failed to remove stale MCP config {path}: {e}", "yellow")
    finally:
        sess.mcp_config_path = ""


_VALID_EFFORT_LEVELS = {"low", "medium", "high", "xhigh", "max"}


def _normalize_effort(value: Optional[str]) -> Optional[str]:
    """Coerce a reasoning_effort value to a valid CC ``--effort`` level
    or ``None`` if blank/invalid. Lets the UI's empty-string "Default"
    cleanly become "no flag passed"."""
    if not value:
        return None
    v = str(value).strip().lower()
    if v in _VALID_EFFORT_LEVELS:
        return v
    return None


def _merge_extra_args_with_effort(extra_args: List[str], effort: Optional[str]) -> List[str]:
    """If the caller passed a separate ``effort`` value (typically from
    the Models table's ``reasoning_effort`` field), inject ``--effort
    <value>`` into argv. If the user *also* manually wrote ``--effort``
    into ``extra_args``, the user-typed value wins (more specific
    intent); we just don't double-emit the flag."""
    if not effort:
        return list(extra_args)
    # If --effort is already present, leave the user's value alone.
    for i, tok in enumerate(extra_args):
        if tok == "--effort":
            return list(extra_args)
    return ["--effort", effort] + list(extra_args)


def _build_chat_argv(
    *,
    cli_path: str,
    model: str,
    system_prompt: str,
    extra_args: List[str],
    effort: Optional[str] = None,
    mcp_config_path: Optional[str] = None,
) -> List[str]:
    """Argv for a long-lived chatbot subprocess (stream-json in & out).

    When ``mcp_config_path`` is provided, CC is launched with the
    IntelliStock MCP server attached and tools restricted to
    ``mcp__intellistock__*`` — CC's built-in Bash/Read/Edit/etc.
    remain disabled. Without a config path, CC runs in pure-text mode
    (no tools at all).
    """
    effective_extra = _merge_extra_args_with_effort(extra_args, _normalize_effort(effort))
    if mcp_config_path:
        safety = [
            "--mcp-config", mcp_config_path,
            *_MCP_FLAGS_TEMPLATE,
        ]
    else:
        safety = list(_SAFETY_FLAGS)
    argv = [
        cli_path, "-p", "--verbose",
        "--input-format", "stream-json",
        "--output-format", "stream-json",
        "--model", model,
        "--system-prompt", system_prompt,
        *_FORCE_SUBSCRIPTION_ARGS,
        *safety,
        *effective_extra,
    ]
    return argv


_JSON_ONLY_SYSTEM_SUFFIX = (
    "\n\n--- OUTPUT FORMAT (HARD CONSTRAINT) ---\n"
    "Your reply MUST be a single JSON object matching the schema above. "
    "Do NOT wrap it in markdown code fences. Do NOT add narration, headers, "
    "summaries, tables, or any prose before or after the JSON. "
    "Do NOT include the schema itself. Output ONLY the JSON value."
)


def _build_structured_argv(
    *,
    cli_path: str,
    model: str,
    system_prompt: str,
    json_schema: str,
    extra_args: List[str],
    effort: Optional[str] = None,
) -> List[str]:
    """Argv for a spawn-per-call structured-output run.

    CC's ``--json-schema`` flag does NOT reliably force the model to
    output strict JSON in practice — Sonnet/Haiku frequently render a
    markdown table or narration around (or instead of) the JSON object.
    Append a hard ``OUTPUT FORMAT`` constraint to the system prompt so
    the model has an unambiguous instruction, in addition to the schema
    flag.
    """
    effective_extra = _merge_extra_args_with_effort(extra_args, _normalize_effort(effort))
    augmented_system_prompt = (system_prompt or "").rstrip() + _JSON_ONLY_SYSTEM_SUFFIX
    argv = [
        cli_path, "-p",
        "--output-format", "json",
        "--model", model,
        "--system-prompt", augmented_system_prompt,
        "--json-schema", json_schema,
        *_FORCE_SUBSCRIPTION_ARGS,
        *_SAFETY_FLAGS,
        *effective_extra,
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
    # Identifies the IntelliStock user that owns this conversation, so the
    # MCP server can dispatch tools with the right principal. Threaded in
    # by ``call_claude_cli_chat``.
    user_id: str = ""
    # Ephemeral token the spawned MCP server presents back to IntelliStock's
    # /chatbot/internal/mcp-* endpoints. Generated per session; never logged.
    mcp_token: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    # Path to the per-session ``.mcp.json`` we write at spawn time; cleaned
    # up at close. Empty when MCP is disabled.
    mcp_config_path: str = ""
    # Temp HOME dir we materialise for the dropped-privilege subprocess
    # (copy of the operator's ~/.claude, chowned to the runtime UID).
    # Empty when no privilege drop is configured.
    runtime_home: str = ""
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
    # UI render blocks (charts, tables, navigate, ...) produced by MCP
    # tools during the in-flight turn. The MCP bridge appends here when
    # CC invokes a safe render-only tool; ``send_turn`` drains and clears
    # the list on turn completion so blocks land in the conversation as
    # part of the final assistant message. Lock-free append is safe — only
    # one in-flight turn per session and no concurrent reader.
    pending_blocks: List[Dict[str, Any]] = field(default_factory=list)


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
        # Resolve the .claude.json source ONCE so per-session spawn
        # doesn't have to scan for a backup every call. No-op on
        # subsequent ``start()`` invocations.
        _init_claude_state_once()
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
        user_id: str = "",
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
                user_id=user_id,
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
        # Clear any blocks left over from a prior failed/aborted turn so
        # they don't bleed into this turn's response.
        sess.pending_blocks.clear()
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
        # Drain UI render blocks accumulated during this turn (charts,
        # tables, navigate, ...) so the orchestration can attach them
        # to the assistant message. Reset the list afterwards so the
        # NEXT turn starts clean — blocks are per-turn, not per-session.
        blocks = sess.pending_blocks[:]
        sess.pending_blocks.clear()
        return {
            "content": content,
            "tool_calls": [],
            "blocks": blocks,
            "finish_reason": finish_reason,
            "raw": raw,
        }

    def close(self, conversation_id: str) -> None:
        """Explicitly close a session (e.g., user deletes the conversation)."""
        with self._sessions_lock:
            sess = self._sessions.pop(conversation_id, None)
        if sess is not None:
            self._close_session(sess)

    def record_block(self, token: str, block: Dict[str, Any]) -> bool:
        """Stash a UI render block (chart, table, navigate, ...) on the
        session matching ``token``. Called by the MCP bridge when a safe
        tool returns a render-only payload — without this, the block
        lives only in CC's transient view of the tool result and never
        reaches the IntelliStock UI. Returns True if the session was
        found and the block was recorded.
        """
        sess = self.lookup_by_token(token)
        if sess is None:
            return False
        sess.pending_blocks.append(block)
        return True

    def lookup_by_token(self, token: str) -> Optional["_Session"]:
        """Return the session whose ``mcp_token`` matches the supplied
        value, or ``None``. Used by the IntelliStock backend to
        authenticate inbound MCP tool-call requests from a spawned CC
        subprocess. Constant-time-ish compare to defeat trivial token
        guessing (the search itself is linear; tokens are 256 bits)."""
        if not token:
            return None
        with self._sessions_lock:
            for sess in self._sessions.values():
                if secrets.compare_digest(sess.mcp_token, token):
                    return sess
        return None

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
        user_id: str = "",
    ) -> _Session:
        with self._sessions_lock:
            existing = self._sessions.get(conversation_id)
            if existing is not None:
                # Reuse only depends on ``(model, extra_args)``. We
                # deliberately ignore ``system_prompt`` here because
                # IntelliStock's chatbot rebuilds it every turn and bakes
                # in dynamic context (workspace counts, settings) that
                # drift between adjacent messages. Matching exactly on
                # system_prompt would force a respawn on every turn,
                # killing the whole point of the persistent-process
                # design (and adding ~3 s of startup per message). The CC
                # subprocess keeps whatever system prompt it spawned
                # with for the conversation's lifetime; refreshing
                # workspace context that frequently isn't worth the cost.
                if (
                    existing.state in (SessionState.IDLE, SessionState.AWAITING, SessionState.SPAWNING)
                    and existing.model == model
                    and existing.extra_args_signature == extra_sig
                ):
                    return existing
                # Stale or mismatched (model / extra_args changed since
                # this conversation last ran). Drop & respawn.
                self._sessions.pop(conversation_id, None)
            sess = _Session(
                conversation_id=conversation_id,
                model=model,
                cli_path=cli_path,
                system_prompt=system_prompt,
                extra_args_signature=extra_sig,
                user_id=user_id,
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
        # Prepare a per-session HOME dir (copy of the operator's ~/.claude
        # chowned to the runtime user) so the dropped subprocess can read
        # its credentials. No-op when no privilege drop is configured.
        sess.runtime_home = _prepare_runtime_home(sess)
        # Materialise the per-session MCP config BEFORE the semaphore so a
        # disk failure doesn't burn a slot.
        sess.mcp_config_path = _write_session_mcp_config(sess)
        if not _GLOBAL_SPAWN_SEM.acquire(timeout=self._spawn_timeout_sec):
            # Drop the config we just wrote; we won't be exec'ing.
            _cleanup_session_mcp_config(sess)
            _cleanup_runtime_home(sess.runtime_home)
            sess.runtime_home = ""
            raise ClaudeCliError(
                f"global subprocess cap ({CLAUDE_CLI_MAX_CONCURRENT}) reached; "
                "another call is hogging the budget."
            )
        argv = _build_chat_argv(
            cli_path=resolved_cli,
            model=sess.model,
            system_prompt=sess.system_prompt,
            extra_args=extra_args,
            mcp_config_path=sess.mcp_config_path,
        )
        # Wrap argv with ``runuser -u claudeuser --`` so the privilege
        # drop is explicit and visible — Popen(user=) was observed to
        # silently no-op in some container capability configurations.
        argv = _wrap_argv_for_runtime_user(argv)
        # Build the env for the child. We override HOME so CC reads the
        # chowned copy of ~/.claude that the runtime user can access, and
        # we keep PATH so claude / node / python still resolve.
        child_env = _strip_api_key_env(os.environ.copy())
        if sess.runtime_home:
            child_env["HOME"] = sess.runtime_home
        # Diagnostic: log the actual argv head + runtime config so it's
        # obvious in the api logs whether the wrap landed.
        _log(
            f"spawn argv[0:3]={argv[:3]} runtime_user={_CC_RUNTIME_USER!r} "
            f"runtime_home={sess.runtime_home!r}",
            "cyan",
        )
        spawn_kwargs: Dict[str, Any] = dict(_platform_popen_kwargs())
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
                env=child_env,
                cwd=sess.runtime_home or None,
                **spawn_kwargs,
            )
        except FileNotFoundError as e:
            # Semaphore was acquired above — release it before raising so
            # a missing binary doesn't permanently shrink the pool. Also
            # clean up the per-session MCP config + runtime HOME we wrote.
            _release_spawn_sem_safely()
            _cleanup_session_mcp_config(sess)
            _cleanup_runtime_home(sess.runtime_home)
            sess.runtime_home = ""
            raise ClaudeCliNotInstalledError(
                f"claude binary not found at {sess.cli_path!r}. "
                "Install with: npm i -g @anthropic-ai/claude-code"
            ) from e
        except Exception as e:
            _release_spawn_sem_safely()
            _cleanup_session_mcp_config(sess)
            _cleanup_runtime_home(sess.runtime_home)
            sess.runtime_home = ""
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
        # Always tear down the per-session MCP config + runtime home —
        # both contain copies of operator credentials and we don't want
        # them lingering in the runtime/temp dirs.
        _cleanup_session_mcp_config(sess)
        if sess.runtime_home:
            _cleanup_runtime_home(sess.runtime_home)
            sess.runtime_home = ""
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
        """Push one NDJSON user event per message to stdin.

        Content is serialised as a list of content blocks — the canonical
        Anthropic format ``[{type: "text", text: "..."}]`` — rather than
        a bare string. When tools are disabled the CLI's input validator
        unconditionally calls ``.some()`` on ``message.content`` looking
        for ``tool_use`` blocks; passing a string makes that throw a
        TypeError inside the JS bundle ("content.some is not a function").
        Wrapping in blocks satisfies the validator and works in every
        CC version we've tested.
        """
        proc = sess.process
        if proc is None or proc.stdin is None or proc.stdin.closed:
            raise ClaudeCliCrashError("subprocess stdin is closed")
        # Concatenate so we make one syscall instead of one per message.
        out = []
        for m in messages:
            role = (m.get("role") or "").lower()
            text = _coerce_text(m.get("content"))
            if not text:
                # The CLI's validators iterate over content blocks; an
                # empty array still trips checks like ``"tool_use_id" in W``
                # on each block, so we just skip blank messages entirely.
                continue
            blocks = [{"type": "text", "text": text}]
            if role == "user":
                line = json.dumps({"type": "user", "message": {"role": "user", "content": blocks}}, ensure_ascii=False)
                out.append(line)
            elif role == "assistant":
                # When replaying history after a crash we re-introduce
                # prior assistant turns so the CLI rebuilds context. CC's
                # stream-json input accepts assistant events.
                line = json.dumps({"type": "assistant", "message": {"role": "assistant", "content": blocks}}, ensure_ascii=False)
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
                # Log the raw CC error envelope so operators can see the
                # real reason behind a generic ``Not logged in`` mapping
                # (auth-state corruption, MCP config mismatch, expired
                # tokens — CC's wording usually points right at it).
                raw_err = str(result_text or "")[:500]
                _log(
                    f"[{sess.conversation_id[:8]}] CC is_error result: "
                    f"{raw_err!r}",
                    "yellow",
                )
                classified = _classify_error_envelope(event)
                if isinstance(classified, ClaudeCliRateLimitError):
                    _emit_rate_limit_discord_alert(str(result_text or ""))
                slot.result.error = classified
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
            # Telemetry: record this chat turn (token counts + envelope cost).
            try:
                event_usage = event.get("usage") or {}
                if not isinstance(event_usage, dict):
                    event_usage = {}
                event_total_cost_usd = event.get("total_cost_usd")
                _safe_record(
                    provider="claude-cli-chat",
                    model=sess.model,
                    usage={
                        "input_tokens": int(event_usage.get("input_tokens", 0) or 0),
                        "output_tokens": int(event_usage.get("output_tokens", 0) or 0),
                        "cache_creation_input_tokens": int(
                            event_usage.get("cache_creation_input_tokens", 0) or 0
                        ),
                        "cache_read_input_tokens": int(
                            event_usage.get("cache_read_input_tokens", 0) or 0
                        ),
                    },
                    ok=True,
                    duration_ms=int(event.get("duration_ms") or 0),
                    retry_count=0,
                    error=None,
                    cost_usd_override=(
                        float(event_total_cost_usd)
                        if event_total_cost_usd is not None
                        else None
                    ),
                    model_id=None,
                )
            except Exception:
                pass
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
    # CC Pro/Max subscription wording observed in backtest #336915 logs:
    # "You've hit your limit · resets 11:50pm (UTC)". None of the earlier
    # needles match it, so the 429 was being mis-classified as a generic
    # ClaudeCliError and retried indefinitely.
    "hit your limit",
    "hit my limit",
    "you've hit",
    "you have hit",
)

# Pulls "11:50pm (UTC)" out of CC's "resets 11:50pm (UTC)." phrasing so
# the Discord alert can quote the reset time directly.
_RESET_TIME_RE = re.compile(r"resets?\s+([^.\n]+?)(?:\s*[\.\n]|$)", re.I)


def _looks_like_rate_limit(text: str) -> bool:
    """Pure-text rate-limit heuristic. Used as a fallback when the
    envelope lacks ``api_error_status``."""
    if not text:
        return False
    lower = text.lower()
    return any(needle in lower for needle in _RATE_LIMIT_NEEDLES)


def _classify_error_text(text: str) -> ClaudeCliError:
    """Map a CC error-result string to the right exception class."""
    t = (text or "").strip()
    if any(needle in t for needle in _NOT_LOGGED_IN_NEEDLES):
        return ClaudeCliNotLoggedInError(
            "Claude Code is not logged in on this server. SSH in and run "
            "`claude` to log in, then retry."
        )
    if _looks_like_rate_limit(t):
        return ClaudeCliRateLimitError(
            f"Claude Code subscription quota hit — retry later "
            f"({t[:200]})"
        )
    return ClaudeCliError(t[:300])


def _classify_error_envelope(envelope: Dict[str, Any]) -> ClaudeCliError:
    """Map a CC error envelope to the right exception class.

    Prefers ``api_error_status`` over text matching. CC sometimes returns
    a 429 with a body whose phrasing doesn't match any of our text needles
    (e.g. ``"You've hit your limit · resets 11:50pm (UTC)"``); classifying
    on status code first ensures we never miss a real rate-limit again
    (root cause of the unbounded retry storm in backtest #336915)."""
    text = str(envelope.get("result") or "").strip()
    status = envelope.get("api_error_status")
    if isinstance(status, str):
        try:
            status = int(status)
        except (TypeError, ValueError):
            status = None
    if status == 429 or _looks_like_rate_limit(text):
        return ClaudeCliRateLimitError(
            f"Claude Code subscription quota hit — retry later "
            f"({text[:200]})"
        )
    return _classify_error_text(text)


# Process-wide dedupe for Discord rate-limit alerts. CC subscription
# quota resets at a fixed wall-clock time, so spamming a notification per
# spawn (a backtest can hit the wall 20+ times in seconds while chunk-
# fallback fans out) would be useless noise. Key by the reset substring
# so the operator sees one alert per quota window.
_RATE_LIMIT_ALERT_LOCK = threading.Lock()
_RATE_LIMIT_ALERTS_SENT: set[str] = set()


def _emit_rate_limit_discord_alert(raw_text: str) -> None:
    """Enqueue a Discord notification when CC reports the subscription
    rate limit. Dedupes per (process, reset-time) so chunk-fallback
    fan-out can't spam the channel. Any failure to enqueue is logged but
    must never propagate — Discord outages should not break a backtest."""
    if not raw_text:
        return
    reset_match = _RESET_TIME_RE.search(raw_text)
    reset_time = reset_match.group(1).strip() if reset_match else ""
    dedupe_key = reset_time or raw_text[:80]
    with _RATE_LIMIT_ALERT_LOCK:
        if dedupe_key in _RATE_LIMIT_ALERTS_SENT:
            return
        _RATE_LIMIT_ALERTS_SENT.add(dedupe_key)
    try:
        from interactive_utils import get_conn  # lazy import to avoid a cycle
        from discord_sender import enqueue_discord_message

        conn = get_conn()
        try:
            embed = {
                "title": "Claude Code rate limit hit",
                "description": (
                    f"Claude Code subscription quota exhausted.\n\n"
                    f"**Reset:** {reset_time or 'see message below'}\n\n"
                    f"Strategies using `claude-cli` will skip the affected "
                    f"bars until the quota refreshes."
                ),
                "color": 0xE67E22,  # orange — warning, not fatal
                "fields": [
                    {
                        "name": "Raw message",
                        "value": raw_text[:1024] or "(empty)",
                        "inline": False,
                    },
                ],
            }
            enqueue_discord_message(conn, "notifications", "", embed=embed)
            _log(
                f"Discord rate-limit alert enqueued (reset={reset_time or '?'})",
                "yellow",
            )
        finally:
            try:
                conn.close(noreply_wait=False)
            except Exception:
                pass
    except Exception as exc:
        _log(f"failed to enqueue Discord rate-limit alert: {exc}", "yellow")


def _reset_rate_limit_alert_dedupe_for_tests() -> None:
    """Hook for tests — clears the per-process dedupe set so each test
    starts from a clean state."""
    with _RATE_LIMIT_ALERT_LOCK:
        _RATE_LIMIT_ALERTS_SENT.clear()


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
    user_id: str = "",
    cli_path: str = "claude",
    extra_args: Optional[List[str]] = None,
    reasoning_effort: Optional[str] = None,
    timeout_sec: Optional[int] = None,
) -> Dict[str, Any]:
    """Send one chatbot turn to a persistent ``claude`` subprocess.

    The first call for a given ``conversation_id`` spawns; subsequent calls
    reuse the warm process. Returns the normalised
    ``{content, tool_calls, finish_reason, raw}`` shape that the chatbot's
    provider abstraction expects.

    ``user_id`` identifies the IntelliStock user for MCP tool dispatch —
    when a tool call comes back from CC, the backend executes it as this
    user. Required for the MCP bridge to be useful; chats without a
    user_id will still work but the model's tool calls will fail.

    ``reasoning_effort`` (if set to one of ``low|medium|high|xhigh|max``)
    is injected as ``--effort <level>`` on the spawned CLI. Folded into
    ``extra_args`` via the spawn-argv builder so the session-reuse
    signature picks up changes — bumping effort mid-conversation will
    transparently respawn the subprocess with the new flag.
    """
    if not conversation_id:
        raise ValueError("conversation_id is required for claude-cli chatbot calls")
    if not model:
        raise ValueError("model is required")
    extra_args = list(extra_args or [])
    effort = _normalize_effort(reasoning_effort)
    if effort:
        # Inline the --effort pair into extra_args so the session-reuse
        # ``extra_args_signature`` reflects the value and a UI change
        # forces a clean respawn.
        extra_args = _merge_extra_args_with_effort(extra_args, effort)
    mgr = get_session_manager()
    return mgr.send_turn(
        conversation_id=conversation_id,
        messages=messages,
        system_prompt=system_prompt or "",
        model=model,
        cli_path=cli_path or "claude",
        extra_args=extra_args,
        user_id=user_id or "",
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
    reasoning_effort: Optional[str] = None,
    timeout_sec: Optional[int] = None,
) -> Any:
    """Spawn-per-call structured-output run. ``output_schema`` must be a
    Pydantic v2 ``BaseModel`` subclass.

    Returns a validated instance of ``output_schema`` on success. Raises one
    of the ``ClaudeCli*Error`` types on failure.

    Optional ``reasoning_effort`` (``low|medium|high|xhigh|max``) is
    mapped to CC's ``--effort`` flag. Blank/invalid values are silently
    ignored so the CLI runs with its built-in default.
    """
    if not model:
        raise ValueError("model is required")
    if output_schema is None:
        raise ValueError("output_schema is required")
    extra_args = list(extra_args or [])
    effort = _normalize_effort(reasoning_effort)
    if effort:
        extra_args = _merge_extra_args_with_effort(extra_args, effort)
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
    # Apply the same auth-state prep + privilege drop the chatbot's
    # persistent session uses. Without this, the spawn-per-call subprocess
    # runs as container root and CC's API call fails with
    # ``401 Invalid authentication credentials`` because the local Pro/Max
    # session is keyed to the runtime user we set up at install time.
    # We use a PROCESS-WIDE shared runtime HOME (created once, reused for
    # every structured call) instead of one per call — without sharing,
    # every call would do a ~50MB shutil.copytree, which dominates the
    # wall time of a backtest with thousands of LLM dispatches.
    _init_claude_state_once()
    runtime_home = _get_structured_runtime_home()
    argv = _wrap_argv_for_runtime_user(argv)
    child_env = _strip_api_key_env(os.environ.copy())
    if runtime_home and runtime_home != (os.environ.get("HOME") or "/root"):
        child_env["HOME"] = runtime_home

    # Per-call counter so concurrent spawns are distinguishable in logs.
    # Sonnet+reasoning_effort=high routinely takes 30-90s per structured
    # call; without per-spawn entry/exit logs the broker appears frozen
    # while 4 workers run in parallel. The counter lets the operator
    # match a "spawn N" line to its matching "completed N in Xs" line.
    global _STRUCTURED_SPAWN_COUNTER
    with _STRUCTURED_SPAWN_LOCK:
        _STRUCTURED_SPAWN_COUNTER += 1
        spawn_id = _STRUCTURED_SPAWN_COUNTER

    if not _GLOBAL_SPAWN_SEM.acquire(timeout=CLAUDE_CLI_SPAWN_TIMEOUT_SEC):
        raise ClaudeCliError(
            f"global subprocess cap ({CLAUDE_CLI_MAX_CONCURRENT}) reached"
        )
    _spawn_t0 = time.monotonic()
    _log(
        f"structured spawn #{spawn_id}: model={model} effort={effort or 'default'} "
        f"prompt_chars={len(user_prompt or '')} schema_fields={len(schema_dict.get('properties') or {}) if isinstance(schema_dict, dict) else '?'} "
        f"timeout={timeout}s",
        "cyan",
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
                env=child_env,
                cwd=runtime_home or None,
                **_platform_popen_kwargs(),
            )
        except FileNotFoundError as e:
            _log(
                f"structured spawn #{spawn_id}: FileNotFoundError after "
                f"{time.monotonic() - _spawn_t0:.1f}s — claude binary missing",
                "red",
            )
            raise ClaudeCliNotInstalledError(
                f"claude binary not found at {resolved_cli!r}"
            ) from e
        except subprocess.TimeoutExpired as e:
            _log(
                f"structured spawn #{spawn_id}: TIMEOUT after "
                f"{time.monotonic() - _spawn_t0:.1f}s (timeout_sec={timeout})",
                "red",
            )
            raise ClaudeCliTimeoutError(
                f"claude structured call timed out after {timeout}s"
            ) from e
    finally:
        _release_spawn_sem_safely()
        # NOTE: do NOT cleanup runtime_home — it's shared across all
        # structured calls and lives for the lifetime of the process.

    _elapsed = time.monotonic() - _spawn_t0
    _log(
        f"structured spawn #{spawn_id}: completed in {_elapsed:.1f}s "
        f"(exit={proc.returncode}, stdout_chars={len(proc.stdout or '')}, "
        f"stderr_chars={len(proc.stderr or '')})",
        "cyan" if proc.returncode == 0 else "yellow",
    )
    # Diagnostic dump of CC's full envelope so the operator can see
    # exactly what the model produced (markdown vs JSON, partial vs
    # complete, etc.). Off by default; enable with
    # CLAUDE_CLI_DUMP_STRUCTURED_STDOUT=1 in the container env.
    if _DUMP_STRUCTURED_STDOUT:
        _stdout_preview = (proc.stdout or "")[:1500]
        _stderr_preview = (proc.stderr or "")[:600]
        _log(
            f"structured spawn #{spawn_id} STDOUT[:1500]={_stdout_preview!r}",
            "yellow",
        )
        if _stderr_preview:
            _log(
                f"structured spawn #{spawn_id} STDERR[:600]={_stderr_preview!r}",
                "yellow",
            )

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
        err = _classify_error_envelope(result_envelope)
        if isinstance(err, ClaudeCliRateLimitError):
            _emit_rate_limit_discord_alert(
                str(result_envelope.get("result") or "")
            )
        raise err

    # Capture the envelope's usage block on a thread-local so the upstream
    # telemetry layer (llm_utils -> record_llm_call) can read real token
    # counts. Without this, claude-cli rows in LLMUsage would always show
    # input_tokens=0/output_tokens=0 because the envelope is parsed locally
    # and never propagated. The block is set BEFORE the success path returns
    # so even validation-repair returns capture the right usage.
    _env_usage = result_envelope.get("usage")
    if isinstance(_env_usage, dict):
        _LAST_STRUCT_ENVELOPE_USAGE.data = dict(_env_usage)
    else:
        _LAST_STRUCT_ENVELOPE_USAGE.data = {}

    # CC's --json-schema flag puts the validated structured output in
    # a SEPARATE ``structured_output`` field on the envelope when the
    # model honored it. The ``result`` field then either contains the
    # model's natural-language reply (often empty) or — when CC didn't
    # enforce the schema — the model's freeform response (markdown,
    # narration, etc.). Discovered via diagnostic dump on backtest
    # #513520: spawn #3 had ``result=""`` and the actual schema-matching
    # JSON only in ``structured_output``. Prefer that field when present.
    structured_output = result_envelope.get("structured_output")
    payload: Any
    if isinstance(structured_output, (dict, list)):
        payload = structured_output
    else:
        raw_result = result_envelope.get("result")
        if raw_result is None:
            raise ClaudeCliError("claude result envelope missing `result` field")
        if isinstance(raw_result, str):
            stripped = raw_result.strip()
            if not stripped:
                raise ClaudeCliValidationError(
                    "claude returned empty result and no structured_output field"
                )
            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError:
                # CC didn't enforce --json-schema — the model returned
                # prose / markdown. Use the same JSON-extraction logic
                # the OpenAI/Azure raw-JSON fallback uses to pull the
                # JSON out of the surrounding text.
                extracted = _try_extract_payload_from_text(output_schema, stripped)
                if extracted is not None:
                    return extracted
                raise ClaudeCliValidationError(
                    f"claude result is not valid JSON and no structured_output field: "
                    f"{stripped[:200]!r}"
                )
        else:
            payload = raw_result

    try:
        return output_schema.model_validate(payload)
    except Exception as e:
        # Strict validation failed. CC's ``--json-schema`` enforces only
        # SHAPE — Pydantic enforces stricter constraints (regex, min/max,
        # discriminated unions, custom validators) on top. Before giving
        # up, try the same repair pipeline the OpenAI/Azure raw-JSON
        # fallback uses: shape coercion, nested-list unwrap, single-key
        # inner-JSON unwrap. None of these mutate the underlying model
        # output; they just present alternative wrappings to Pydantic.
        repaired = _try_repair_payload_for_schema(output_schema, payload)
        if repaired is not None:
            return repaired
        # Include a preview of what CC returned so operators can see the
        # specific shape mismatch — without this the error is just
        # "missing field X" / "type Y vs Z" with no concrete context.
        try:
            payload_preview = json.dumps(payload, default=str)[:400]
        except Exception:
            payload_preview = str(payload)[:400]
        raise ClaudeCliValidationError(
            f"claude output failed Pydantic validation: {e} | payload_preview={payload_preview!r}"
        ) from e


def daemon_for_structured_enabled() -> bool:
    """True iff the experimental daemon path is opt-in via env. Read at every
    call so flipping the flag in tests / runtime takes effect without a
    process restart. Cheap (one ``os.environ.get`` per call). Default OFF.

    See ``call_claude_cli_chat_structured`` for KNOWN ISSUES before flipping.
    """
    return str(
        os.environ.get("CLAUDE_CLI_DAEMON_FOR_STRUCTURED", "0")
    ).strip().lower() in ("1", "true", "yes", "y", "on")


def call_claude_cli_chat_structured(
    *,
    conversation_id: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    output_schema: Any,                # Pydantic v2 BaseModel — avoid the import here
    cli_path: str = "claude",
    extra_args: Optional[List[str]] = None,
    reasoning_effort: Optional[str] = None,
    timeout_sec: Optional[int] = None,
    user_id: str = "",
) -> Any:
    """EXPERIMENTAL: structured-output via the long-lived chatbot daemon.

    Sends a single user-prompt turn to a persistent ``claude`` subprocess
    (keyed by ``conversation_id``) and parses the assistant text as JSON
    against ``output_schema``.

    KNOWN ISSUES — DO NOT enable in prod without addressing these first:

    1. Second-call failure (HARD BUG): ``ClaudeCliSessionManager`` tracks
       ``messages_sent`` and on turn N it sends ``messages[messages_sent:]``.
       This wrapper passes ``messages=[user_prompt]`` (length 1) every call,
       so on call #2 the slice is empty and ``_send_one_turn_locked`` raises
       ``"send_turn called with no new messages to deliver."`` The fix is
       either (a) accumulate full conversation history per conversation_id
       (grows context, bad for cost), (b) rotate conversation_id per call
       (defeats the warm-process goal), or (c) add a "structured-mode" path
       to the session manager that bypasses ``messages_sent`` slicing.

    2. System prompt is frozen at first spawn (``_get_or_spawn``). Different
       schemas reaching the same conversation_id will see the FIRST schema's
       instruction, not their own. The conversation_id should include a
       schema fingerprint to avoid silent collisions.

    3. ``ThreadPoolExecutor`` worker reuse: ``threading.get_ident()`` is
       stable for the OS thread, so two consecutive tasks on the same
       worker share a conversation. Combined with #1, every batch worker
       hits the "no new messages" error on its second dispatch.

    The default flag (``CLAUDE_CLI_DAEMON_FOR_STRUCTURED``) is OFF so the
    spawn path remains the production default. The scaffold lands so the
    follow-up work (fixing the session manager + benchmarking) can build
    on it.

    The trade-off vs ``call_claude_cli_structured``:
      * No ``--json-schema`` enforcement — relies on the JSON-only system
        suffix (same one ``_build_structured_argv`` uses) plus the same
        repair pipeline (``_try_extract_payload_from_text`` /
        ``_try_repair_payload_for_schema``) the spawn path uses.
      * Concurrent callers MUST use distinct ``conversation_id`` values
        — one persistent subprocess per conversation, single-threaded
        within that conversation.

    Returns a validated instance of ``output_schema`` or raises one of
    the typed ``ClaudeCli*Error`` exceptions.
    """
    if not conversation_id:
        raise ValueError("conversation_id is required for the daemon-structured path")
    if not model:
        raise ValueError("model is required")
    if output_schema is None:
        raise ValueError("output_schema is required")

    try:
        schema_dict = output_schema.model_json_schema()
    except Exception as e:
        raise ClaudeCliError(
            f"output_schema must be a Pydantic v2 BaseModel "
            f"with model_json_schema(): {e}"
        ) from e
    # ``sort_keys=True`` so the schema serialization is byte-identical for
    # the same ``output_schema``. Anthropic prompt caching needs an exact
    # prefix match; without sort_keys, dict ordering changes (across Python
    # versions or after schema-class edits) silently invalidate the cache.
    schema_json = json.dumps(schema_dict, ensure_ascii=False, sort_keys=True)

    # Same JSON-only suffix the spawn path appends so the model gets a
    # consistent instruction across both paths. Including the schema
    # inline gives the model something concrete to honor — without
    # ``--json-schema`` enforcement, this is the only contract.
    augmented_system_prompt = (
        (system_prompt or "").rstrip()
        + "\n\n--- TARGET JSON SCHEMA (HARD CONSTRAINT) ---\n"
        + schema_json
        + _JSON_ONLY_SYSTEM_SUFFIX
    )

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
        _rollback_optimistic_user(conversation_id)
        raise

    # Wrap the post-call success path (coerce → parse → extract/repair →
    # validate) in a rollback try/except. If ANY of these steps raise
    # (including the empty-content guard, JSON parse failure, or Pydantic
    # validation failure), we must drop the optimistic user message we
    # appended above — symmetric with the call_claude_cli_chat failure
    # rollback — so retries don't replay stale state.
    try:
        # Use ``_coerce_text`` for symmetry with the rest of the provider —
        # ``call_claude_cli_chat`` returns ``content`` as a plain string today,
        # but a future CLI version could ship a content-blocks list and the
        # spawn path's coercer handles both.
        raw_content = chat_result.get("content") if isinstance(chat_result, dict) else None
        content = _coerce_text(raw_content) if raw_content is not None else ""
        if not isinstance(content, str) or not content.strip():
            raise ClaudeCliValidationError(
                "claude daemon returned empty content"
            )

        stripped = content.strip()
        validated: Any = None
        payload: Any = None
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            # Model returned prose / fenced JSON / narration around the JSON.
            # Reuse the same extraction pipeline the spawn path uses so behavior
            # stays consistent across the two routes.
            extracted = _try_extract_payload_from_text(output_schema, stripped)
            if extracted is not None:
                validated = extracted
            else:
                raise ClaudeCliValidationError(
                    f"claude daemon content is not valid JSON: {stripped[:200]!r}"
                )
        else:
            try:
                validated = output_schema.model_validate(payload)
            except Exception as e:
                repaired = _try_repair_payload_for_schema(output_schema, payload)
                if repaired is not None:
                    validated = repaired
                else:
                    try:
                        payload_preview = json.dumps(payload, default=str)[:400]
                    except Exception:
                        payload_preview = str(payload)[:400]
                    raise ClaudeCliValidationError(
                        f"claude daemon output failed Pydantic validation: {e} | payload_preview={payload_preview!r}"
                    ) from e
    except Exception:
        _rollback_optimistic_user(conversation_id)
        raise

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

    return validated


def _try_extract_payload_from_text(output_schema: Any, raw_text: str) -> Any:
    """When CC returns prose with an embedded JSON object (e.g. markdown
    code fences, narration around the JSON), pull the JSON out and try
    the full validate-then-repair pipeline against each candidate.

    Returns the validated Pydantic instance on success, or ``None`` if
    nothing usable was found. Lazy-imports the candidate-extraction
    helper from ``llm_utils`` to avoid a module-load circular import.
    """
    if not isinstance(raw_text, str) or not raw_text.strip():
        return None
    try:
        from llm_utils import _raw_json_candidates
    except Exception:
        return None
    candidates: list[Any] = []
    try:
        for cand_text in _raw_json_candidates(raw_text):
            if not cand_text:
                continue
            try:
                candidates.append(json.loads(cand_text))
            except Exception:
                continue
    except Exception:
        return None
    for payload in candidates:
        try:
            return output_schema.model_validate(payload)
        except Exception:
            repaired = _try_repair_payload_for_schema(output_schema, payload)
            if repaired is not None:
                return repaired
    return None


def _try_repair_payload_for_schema(output_schema: Any, payload: Any) -> Any:
    """Try to coerce CC's parsed JSON into something the strict Pydantic
    schema will accept. Returns the validated instance on success, or
    ``None`` if no repair worked (caller should raise the original
    validation error). Lazy-imports the repair helpers from ``llm_utils``
    to avoid a module-load circular import.
    """
    try:
        from llm_utils import (
            _coerce_structured_output_shape,
            _unwrap_nested_lists,
        )
    except Exception:
        return None
    # Try a small ladder of alternative shapes, ordered cheapest-first.
    candidates: list[Any] = []
    try:
        candidates.append(_coerce_structured_output_shape(output_schema, payload))
    except Exception:
        pass
    try:
        unwrapped = _unwrap_nested_lists(payload)
        if unwrapped is not payload:
            candidates.append(unwrapped)
            try:
                candidates.append(_coerce_structured_output_shape(output_schema, unwrapped))
            except Exception:
                pass
    except Exception:
        pass
    # ``{"final": "{\"ok\":true,...}"}`` — CC wraps the schema-conforming
    # JSON inside a single-key envelope. Unwrap and validate inner.
    if isinstance(payload, dict) and len(payload) == 1:
        inner_val = next(iter(payload.values()))
        if isinstance(inner_val, str) and inner_val.strip().startswith("{"):
            try:
                inner_parsed = json.loads(inner_val)
                candidates.append(inner_parsed)
                try:
                    candidates.append(_coerce_structured_output_shape(output_schema, inner_parsed))
                except Exception:
                    pass
            except Exception:
                pass
    seen: set[int] = set()
    for candidate in candidates:
        if candidate is None or id(candidate) in seen:
            continue
        seen.add(id(candidate))
        try:
            return output_schema.model_validate(candidate)
        except Exception:
            continue
    return None


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
    # Reuse the same runtime-home prep + runuser wrap the chatbot spawn
    # path uses. WITHOUT this, the test runs as container root with
    # HOME=/root, where ``/root/.claude.json`` isn't present (no bind
    # mount for the sibling file) and CC reports ``Not logged in`` even
    # though the chatbot path (which copies the cached restoration into
    # a per-call HOME) authenticates fine. The mismatch is what surfaces
    # as "no output from claude" on the model card.
    _init_claude_state_once()
    runtime_home = _prepare_runtime_home_for_id("test-cli")
    argv = [
        resolved_cli, "-p",
        "--output-format", "json",
        "--model", model,
        *_FORCE_SUBSCRIPTION_ARGS,
        *_SAFETY_FLAGS,
    ]
    argv = _wrap_argv_for_runtime_user(argv)
    child_env = _strip_api_key_env(os.environ.copy())
    if runtime_home and runtime_home != (os.environ.get("HOME") or "/root"):
        child_env["HOME"] = runtime_home
    try:
        try:
            proc = subprocess.run(
                argv,
                input="ok\n",
                capture_output=True, text=True, timeout=timeout_sec,
                encoding="utf-8", errors="replace",
                env=child_env,
                cwd=runtime_home or None,
                **_platform_popen_kwargs(),
            )
        except subprocess.TimeoutExpired:
            return {
                "ok": False, "version": version, "logged_in": False, "model_response": None,
                "error": f"claude -p timed out after {timeout_sec}s",
                "elapsed_ms": int((time.monotonic() - started) * 1000),
            }
    finally:
        # Per-test temp HOME — drop it whether the call succeeded or not.
        # ``_cleanup_runtime_home`` is a no-op when runtime_home equals
        # the process HOME (i.e. the "no privilege drop" case).
        try:
            _cleanup_runtime_home(runtime_home)
        except Exception:
            pass

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
        api_status = envelope.get("api_error_status")
        low = result_text.lower()
        if any(n in result_text for n in _NOT_LOGGED_IN_NEEDLES):
            return {
                "ok": False, "version": version, "logged_in": False, "model_response": None,
                "error": (
                    "Claude Code is not logged in on this server. Use the "
                    "“Re-authenticate Claude” button to sign in."
                ),
                "elapsed_ms": int((time.monotonic() - started) * 1000),
            }
        # 401 + an "invalid API key" message means a stale API key is
        # overriding the subscription OAuth. We neutralize the known sources
        # (process env via _strip_api_key_env, settings ``env`` block via
        # _FORCE_SUBSCRIPTION_ARGS); if this still fires the key is coming
        # from some other source (e.g. ``apiKeyHelper`` or a stored
        # ``primaryApiKey`` in ``.claude.json``) — surface an actionable hint.
        if api_status == 401 or "invalid api key" in low or "invalid authentication" in low:
            return {
                "ok": False, "version": version, "logged_in": False, "model_response": None,
                "error": (
                    "Claude returned 401: an API key is overriding the "
                    "subscription. Check for an apiKeyHelper or a stored "
                    "primaryApiKey in the server's ~/.claude config (an "
                    "ANTHROPIC_API_KEY env var and a settings.json env block "
                    "are already neutralized)."
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


# ── Subscription re-auth from the web/mobile UI ─────────────────────────────
# Drives ``claude auth login --claudeai`` (the normal /login flow) so an
# operator can refresh the deployment's Claude subscription credentials
# without SSHing into the box. Unlike codex's device-code flow (the CLI
# self-polls OpenAI), Claude's OAuth needs the authorization code pasted
# BACK into the CLI, so this is a two-step flow: start (capture URL) →
# submit_code (feed the pasted code). Runs under a PTY because the CLI
# refuses the interactive login without a TTY, and as the *operator* (no
# privilege drop) so fresh creds land in ``$HOME/.claude`` where every
# per-call runtime HOME is seeded from.


class ClaudeCliLoginError(ClaudeCliError):
    """Raised when the interactive login flow can't be started."""


def _operator_home() -> str:
    return os.environ.get("HOME") or "/root"


def invalidate_runtime_home_cache() -> None:
    """Drop the process-wide cached structured runtime HOME and the
    resolved ``.claude.json`` source so the *next* CLI call re-copies the
    operator's freshly-written credentials. Called after a successful
    login / logout — without it, strategies would keep using the stale
    runtime HOME (and its expired token) until the process restarts."""
    global _STRUCTURED_RUNTIME_HOME, _CLAUDE_JSON_SOURCE
    with _STRUCTURED_RUNTIME_HOME_LOCK:
        old = _STRUCTURED_RUNTIME_HOME
        _STRUCTURED_RUNTIME_HOME = ""
    if old:
        try:
            _cleanup_runtime_home(old)
        except Exception:
            pass
    with _CLAUDE_STATE_INIT_LOCK:
        _CLAUDE_JSON_SOURCE = ""
    # Persistent chatbot sessions cache their own per-conversation runtime
    # HOME; tear them down so the next turn re-spawns with fresh creds.
    try:
        shutdown_session_manager()
    except Exception:
        pass


def claude_auth_status(cli_path: str = "claude") -> Dict[str, Any]:
    """Read-only probe of the deployment's Claude subscription auth.
    Runs ``claude auth status`` under the operator HOME. Returns
    {installed, version, authenticated, account, auth_message}."""
    try:
        resolved = _resolve_cli_path(cli_path or "claude")
    except ClaudeCliNotInstalledError as e:
        return {"installed": False, "version": None, "authenticated": False,
                "account": None, "auth_message": str(e)}
    except ClaudeCliError as e:
        return {"installed": False, "version": None, "authenticated": False,
                "account": None, "auth_message": str(e)}

    version = None
    try:
        ver = subprocess.run(
            [resolved, "--version"], capture_output=True, text=True, timeout=10,
            encoding="utf-8", errors="replace", **_platform_popen_kwargs(),
        )
        if ver.stdout:
            version = ver.stdout.strip().splitlines()[0]
    except (subprocess.TimeoutExpired, OSError):
        pass

    env = _strip_api_key_env(os.environ.copy())
    env["HOME"] = _operator_home()
    try:
        res = subprocess.run(
            [resolved, "auth", "status"], capture_output=True, text=True,
            timeout=15, encoding="utf-8", errors="replace", env=env,
            **_platform_popen_kwargs(),
        )
    except subprocess.TimeoutExpired:
        return {"installed": True, "version": version, "authenticated": False,
                "account": None, "auth_message": "claude auth status timed out"}
    except OSError as e:
        return {"installed": True, "version": version, "authenticated": False,
                "account": None, "auth_message": str(e)}

    blob = _ANSI_ESCAPE_RE_CC.sub("", f"{res.stdout or ''}\n{res.stderr or ''}").strip()
    low = blob.lower()
    not_logged = any(n.lower() in low for n in _NOT_LOGGED_IN_NEEDLES) or "not logged in" in low
    authed = (res.returncode == 0) and not not_logged
    account = None
    m = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", blob)
    if m:
        account = m.group(0)
    return {
        "installed": True,
        "version": version,
        "authenticated": bool(authed),
        "account": account,
        "auth_message": (blob[:300] or ("authenticated" if authed else "not logged in")),
    }


def claude_logout(cli_path: str = "claude") -> tuple[bool, str]:
    """Run ``claude auth logout`` under the operator HOME and invalidate
    the runtime-home cache. Returns (ok, message)."""
    try:
        resolved = _resolve_cli_path(cli_path or "claude")
    except ClaudeCliError as e:
        return False, str(e)
    env = _strip_api_key_env(os.environ.copy())
    env["HOME"] = _operator_home()
    try:
        res = subprocess.run(
            [resolved, "auth", "logout"], capture_output=True, text=True,
            timeout=15, encoding="utf-8", errors="replace", env=env,
            **_platform_popen_kwargs(),
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        return False, str(e)
    invalidate_runtime_home_cache()
    if res.returncode == 0:
        return True, "ok"
    return False, ((res.stderr or res.stdout or "").strip() or f"claude auth logout exit {res.returncode}")


# Curated base set of Claude Code subscription model aliases. The CLI has no
# "list models" command, so we ship a sensible base and merge the account-
# specific extras the CLI caches in ~/.claude.json (additionalModelOptionsCache)
# — that part IS dynamic per subscription. ``[1m]`` variants use the 1M context
# window, which requires turning on usage credits at claude.ai/settings/usage
# ON TOP of the subscription; the plain aliases use the standard 200K context.
_BASE_CLAUDE_MODELS = [
    {"value": "claude-haiku-4-5", "label": "Haiku 4.5",
     "description": "Fastest, cheapest · standard 200K context"},
    {"value": "claude-sonnet-4-6", "label": "Sonnet 4.6",
     "description": "Balanced · standard 200K context"},
    {"value": "claude-sonnet-4-6[1m]", "label": "Sonnet 4.6 (1M context)",
     "description": "1M context · requires usage credits"},
    {"value": "claude-opus-4-8", "label": "Opus 4.8",
     "description": "Most capable · standard 200K context"},
    {"value": "claude-opus-4-8[1m]", "label": "Opus 4.8 (1M context)",
     "description": "1M context · requires usage credits"},
]


def list_available_models(cli_path: str = "claude") -> Dict[str, Any]:
    """Return the Claude subscription models selectable for a claude-cli
    model, for the /models UI dropdown. Merges the curated base set with the
    account-specific options the CLI caches in ``~/.claude.json``. Each entry
    carries ``requires_credits`` (true for ``[1m]`` 1M-context variants, which
    need usage credits enabled and otherwise fail with a 1M-context error)."""
    models = [dict(m) for m in _BASE_CLAUDE_MODELS]
    seen = {m["value"] for m in models}
    try:
        cj = os.path.join(_operator_home(), ".claude.json")
        if os.path.isfile(cj):
            with open(cj, "r", encoding="utf-8") as f:
                doc = json.load(f)
            for opt in (doc.get("additionalModelOptionsCache") or []):
                if not isinstance(opt, dict):
                    continue
                val = str(opt.get("value") or "").strip()
                if val and val not in seen:
                    seen.add(val)
                    models.append({
                        "value": val,
                        "label": str(opt.get("label") or val),
                        "description": str(opt.get("description") or ""),
                    })
    except Exception:
        pass
    for m in models:
        m["requires_credits"] = "[1m]" in m["value"]
    return {"models": models}


# ANSI/VT100 escape stripper for PTY output (URL/prompt parsing).
_ANSI_ESCAPE_RE_CC = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*(?:\x07|\x1b\\)|\x1b[=>]")
# Anthropic OAuth URL — captured from the login flow's stdout. The host
# allowlist below is the real gate; this just narrows the candidate space.
_CC_OAUTH_URL_RE = re.compile(r"https?://[^\s'\"<>\x1b]+")
# Authorization code the operator pastes back. Claude's OAuth code is a
# token, sometimes ``<code>#<state>``. Bounded + charset-restricted; this
# is written to the CLI's PTY stdin (not a shell), so it's not a shell
# injection vector, but we constrain it anyway.
_CC_AUTH_CODE_RE = re.compile(r"^[A-Za-z0-9._~+/=#:%-]{6,1024}$")
_CC_OAUTH_HOSTS = {
    "claude.ai", "www.claude.ai", "claude.com", "www.claude.com",
    "console.anthropic.com", "auth.anthropic.com", "anthropic.com",
}


def _is_safe_claude_oauth_url(url: str) -> bool:
    if not url:
        return False
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
    except Exception:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    if "@" in (parsed.netloc or ""):
        return False
    return (parsed.hostname or "").lower() in _CC_OAUTH_HOSTS


@dataclass
class _CcLoginJob:
    job_id: str
    state: str = "pending"  # pending | parsed | awaiting_code | success | failed | expired | cancelled
    login_url: Optional[str] = None
    error: Optional[str] = None
    started_at: float = field(default_factory=time.monotonic)
    proc: Optional[subprocess.Popen] = None
    master_fd: Optional[int] = None
    out_buf: List[str] = field(default_factory=list)


class ClaudeCliLogin:
    """Drives ``claude auth login --claudeai`` for the web/mobile UI.

    Two-step OAuth: ``start`` spawns the CLI under a PTY and captures the
    printed claude.ai authorization URL; ``submit_code`` writes the
    operator-pasted code into the PTY and waits for the CLI to exchange it
    and persist credentials. On success the runtime-home cache is dropped
    so subsequent strategy/chatbot calls pick up the new token.
    """

    _jobs: Dict[str, _CcLoginJob] = {}
    _jobs_lock = threading.Lock()
    _JOB_TTL_SEC = 1800
    _BUF_MAX = 300
    _MAX_LIVE_JOBS = int(os.environ.get("CLAUDE_CLI_MAX_LIVE_LOGIN_JOBS", "3"))
    _URL_PARSE_TIMEOUT_SEC = float(os.environ.get("CLAUDE_CLI_LOGIN_URL_TIMEOUT_SEC", "25"))
    _CODE_EXCHANGE_TIMEOUT_SEC = float(os.environ.get("CLAUDE_CLI_LOGIN_CODE_TIMEOUT_SEC", "45"))

    @classmethod
    def start(cls, *, cli_path: str = "claude") -> _CcLoginJob:
        if sys.platform == "win32":
            raise ClaudeCliLoginError("interactive claude login is not supported on Windows servers")
        resolved = _resolve_cli_path(cli_path or "claude")
        cls._reap_old_jobs()
        with cls._jobs_lock:
            live = sum(1 for j in cls._jobs.values() if j.state in ("pending", "parsed", "awaiting_code"))
        if live >= cls._MAX_LIVE_JOBS:
            raise ClaudeCliLoginError(
                f"too many concurrent claude login jobs ({live}); cancel one or wait"
            )
        job_id = str(uuid.uuid4())
        job = _CcLoginJob(job_id=job_id)
        try:
            import pty
            master_fd, slave_fd = pty.openpty()
            # Wide terminal so the long OAuth URL prints on one line.
            try:
                import fcntl
                import struct
                import termios
                fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, struct.pack("HHHH", 50, 1000, 0, 0))
            except Exception:
                pass
            env = _strip_api_key_env(os.environ.copy())
            env["HOME"] = _operator_home()
            env["TERM"] = "xterm-256color"
            # Discourage the CLI from trying to auto-launch a browser the
            # headless server can't reach; force the paste-code path.
            env["BROWSER"] = env.get("BROWSER", "true")
            env["NO_BROWSER"] = "1"
            proc = subprocess.Popen(
                [resolved, "auth", "login", "--claudeai"],
                stdin=slave_fd, stdout=slave_fd, stderr=slave_fd,
                env=env, cwd=_operator_home(), close_fds=True,
                start_new_session=True,
            )
            os.close(slave_fd)
            job.proc = proc
            job.master_fd = master_fd
        except ClaudeCliError:
            raise
        except Exception as e:
            raise ClaudeCliLoginError(f"failed to spawn claude login: {e}") from e
        with cls._jobs_lock:
            cls._jobs[job_id] = job
        threading.Thread(
            target=cls._reader, args=(job,), daemon=True,
            name=f"cc-login-{job_id[:8]}",
        ).start()
        # Block until the URL is parsed (or the flow dies / times out).
        deadline = time.monotonic() + cls._URL_PARSE_TIMEOUT_SEC
        while time.monotonic() < deadline:
            with cls._jobs_lock:
                snap = cls._jobs.get(job_id)
                if snap is None:
                    break
                if snap.login_url:
                    if snap.state == "pending":
                        snap.state = "parsed"
                    return snap
                if snap.state in ("failed", "expired", "cancelled", "success"):
                    return snap
            time.sleep(0.2)
        with cls._jobs_lock:
            snap = cls._jobs.get(job_id) or job
            if not snap.login_url and snap.state == "pending":
                snap.state = "failed"
                snap.error = snap.error or (
                    "claude login did not print an authorization URL in time. "
                    + ("".join(snap.out_buf)[-300:] if snap.out_buf else "")
                )
            return snap

    @classmethod
    def submit_code(cls, job_id: str, code: str) -> Optional[Dict[str, Any]]:
        with cls._jobs_lock:
            job = cls._jobs.get(job_id)
        if job is None:
            return None
        code = (code or "").strip()
        if not _CC_AUTH_CODE_RE.match(code):
            job.error = "authorization code has an unexpected format"
            return cls.status(job_id)
        proc = job.proc
        fd = job.master_fd
        if proc is None or fd is None or proc.poll() is not None:
            job.state = "failed"
            job.error = job.error or "login process is no longer running; restart the flow"
            return cls.status(job_id)
        try:
            os.write(fd, (code + "\r").encode("utf-8"))
        except OSError as e:
            job.state = "failed"
            job.error = f"failed to deliver code to claude: {e}"
            return cls.status(job_id)
        with cls._jobs_lock:
            if job.state in ("pending", "parsed"):
                job.state = "awaiting_code"
        # Wait for the CLI to exchange the code + persist credentials.
        deadline = time.monotonic() + cls._CODE_EXCHANGE_TIMEOUT_SEC
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                break
            time.sleep(0.3)
        rc = proc.poll()
        # Confirm via a status probe — the most reliable success signal.
        authed = False
        try:
            authed = bool(claude_auth_status(_resolve_cli_path_safe(job)).get("authenticated"))
        except Exception:
            authed = False
        tail = "".join(job.out_buf)[-400:]
        if authed or rc == 0:
            with cls._jobs_lock:
                job.state = "success"
                job.error = None
            invalidate_runtime_home_cache()
        else:
            with cls._jobs_lock:
                if job.state not in ("cancelled", "expired"):
                    job.state = "failed"
                    job.error = _classify_login_failure(tail) or (tail or f"claude login exited {rc}")
        cls._terminate(job)
        return cls.status(job_id)

    @classmethod
    def status(cls, job_id: str) -> Optional[Dict[str, Any]]:
        with cls._jobs_lock:
            job = cls._jobs.get(job_id)
        if job is None:
            return None
        return {
            "job_id": job.job_id,
            "state": job.state,
            "login_url": job.login_url,
            "error": job.error,
            "output_tail": "".join(job.out_buf)[-400:],
        }

    @classmethod
    def cancel(cls, job_id: str) -> bool:
        with cls._jobs_lock:
            job = cls._jobs.get(job_id)
        if job is None:
            return False
        with cls._jobs_lock:
            if job.state in ("pending", "parsed", "awaiting_code"):
                job.state = "cancelled"
        cls._terminate(job)
        return True

    @classmethod
    def _terminate(cls, job: _CcLoginJob) -> None:
        proc = job.proc
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        fd = job.master_fd
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
            job.master_fd = None

    @classmethod
    def _reader(cls, job: _CcLoginJob) -> None:
        fd = job.master_fd
        if fd is None:
            return
        buf = ""
        try:
            while True:
                try:
                    data = os.read(fd, 4096)
                except OSError:
                    break  # PTY closed (process exited)
                if not data:
                    break
                text = _ANSI_ESCAPE_RE_CC.sub("", data.decode("utf-8", "replace"))
                buf += text
                with cls._jobs_lock:
                    job.out_buf.append(text)
                    if len(job.out_buf) > cls._BUF_MAX:
                        job.out_buf[:] = job.out_buf[-cls._BUF_MAX:]
                    if not job.login_url:
                        for m in _CC_OAUTH_URL_RE.finditer(buf):
                            cand = m.group(0).rstrip(".,)]}'\"")
                            if _is_safe_claude_oauth_url(cand):
                                job.login_url = cand
                                if job.state == "pending":
                                    job.state = "parsed"
                                break
        except Exception:
            pass

    @classmethod
    def _reap_old_jobs(cls) -> None:
        now = time.monotonic()
        with cls._jobs_lock:
            stale = [jid for jid, j in cls._jobs.items() if (now - j.started_at) > cls._JOB_TTL_SEC]
        for jid in stale:
            with cls._jobs_lock:
                j = cls._jobs.pop(jid, None)
            if j is not None:
                cls._terminate(j)


def _resolve_cli_path_safe(job: _CcLoginJob) -> str:
    """Best-effort cli_path for the post-submit status probe; the login
    job doesn't retain the original arg, so fall back to the PATH lookup."""
    return "claude"


def _classify_login_failure(text: str) -> Optional[str]:
    low = (text or "").lower()
    if not low.strip():
        return None
    if "invalid" in low and "code" in low:
        return "The authorization code was rejected. Copy it again and retry."
    if "expired" in low:
        return "The authorization code expired. Start the login flow again."
    if any(n.lower() in low for n in _NOT_LOGGED_IN_NEEDLES):
        return "Login did not complete — Claude still reports not logged in."
    return None


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
    "ClaudeCliLogin",
    "ClaudeCliLoginError",
    "claude_auth_status",
    "claude_logout",
    "invalidate_runtime_home_cache",
    "list_available_models",
]
