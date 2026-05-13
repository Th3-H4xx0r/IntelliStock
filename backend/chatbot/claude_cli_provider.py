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


def _init_claude_state_once() -> None:
    """Resolve the canonical ``.claude.json`` source path ONCE per process.

    If the operator's HOME has a live ``$HOME/.claude.json`` (bind-mounted
    or whatever), use that. Otherwise, restore the most recent backup
    from ``.claude/backups/.claude.json.backup.*`` into a stable local
    cache file and use the cache from then on. This avoids the noisy
    per-call "restoring .claude.json from backup" log line and shaves
    a glob + copy off the hot path of every CC spawn.

    Idempotent. Safe to call multiple times — the first call wins.
    """
    global _CLAUDE_JSON_SOURCE
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
        *safety,
        *effective_extra,
    ]
    return argv


def _build_structured_argv(
    *,
    cli_path: str,
    model: str,
    system_prompt: str,
    json_schema: str,
    extra_args: List[str],
    effort: Optional[str] = None,
) -> List[str]:
    """Argv for a spawn-per-call structured-output run."""
    effective_extra = _merge_extra_args_with_effort(extra_args, _normalize_effort(effort))
    argv = [
        cli_path, "-p",
        "--output-format", "json",
        "--model", model,
        "--system-prompt", system_prompt,
        "--json-schema", json_schema,
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
        child_env = os.environ.copy()
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
    _init_claude_state_once()
    runtime_home = _prepare_runtime_home_for_id("structured")
    argv = _wrap_argv_for_runtime_user(argv)
    child_env = os.environ.copy()
    if runtime_home and runtime_home != (os.environ.get("HOME") or "/root"):
        child_env["HOME"] = runtime_home

    if not _GLOBAL_SPAWN_SEM.acquire(timeout=CLAUDE_CLI_SPAWN_TIMEOUT_SEC):
        _cleanup_runtime_home(runtime_home)
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
                env=child_env,
                cwd=runtime_home or None,
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
        # Per-call temp HOME — drop it whether success/timeout/error.
        try:
            _cleanup_runtime_home(runtime_home)
        except Exception:
            pass

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
        *_SAFETY_FLAGS,
    ]
    argv = _wrap_argv_for_runtime_user(argv)
    child_env = os.environ.copy()
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
