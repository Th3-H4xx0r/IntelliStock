"""Codex CLI (OpenAI ``codex``) as an IntelliStock LLM provider.

Architectural template: ``backend/chatbot/claude_cli_provider.py`` (claude-cli).
Protocol details: cribbed from ``NousResearch/hermes-agent``
(``agent/transports/codex_app_server.py``) — Codex CLI's ``app-server``
subcommand speaks JSON-RPC 2.0 over newline-delimited stdin/stdout.

What this module exposes
========================

Strategy-facing (called by ``llm_utils.py``)
--------------------------------------------
``call_codex_cli_plain``
    Plain-text completion. Spawns ``codex app-server``, runs a single
    turn, returns the assistant text. Used for non-structured strategy
    calls and the ``/llm/test`` real-generation smoke prompt.

``call_codex_cli_structured``
    Prompted-JSON structured output. Wraps the prompt with a JSON-schema
    instruction, parses the model's text response, validates against the
    target Pydantic schema. Retries on invalid JSON up to
    ``output_retries`` times (default 2).

``call_codex_cli_chat_structured``
    Spawn-per-call structured wrapper that reuses ``call_codex_cli_structured``
    semantically but threads a conversation_id for telemetry parity with
    the claude-cli chat-structured path. (Codex's app-server is itself a
    session, so we use a fresh thread per call for determinism.)

Web-UI setup (called by ``backend/api/main.py``)
------------------------------------------------
``is_installed`` / ``get_version`` / ``is_authenticated``
    Cheap detection probes used by ``/codex/status``.

``CodexInstaller``
    Wraps ``npm install -g @openai/codex`` or ``brew install codex`` as
    a streaming background job with a per-job log buffer.

``CodexDeviceCodeLogin``
    Drives ``codex login --no-browser`` (or whichever pairing flag the
    installed version supports), parses the pairing URL + code from
    stdout, polls the subprocess for completion. The frontend polls
    ``status()`` every 2s.

Security stance
===============

Pure-text, prompt-only. We do not enable Codex's tool-call APIs, file
read/write, MCP servers, or any shell execution from the model. The
allowlist gate on ``extra_args`` keeps an authenticated operator from
turning the strategy config into RCE (e.g. ``--exec /usr/bin/curl ...``
or ``-e/etc/passwd``). Codex's own tokens land in ``~/.codex/auth.json``
on the API host — operator responsible for filesystem permissions.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from queue import Empty, Queue
from typing import Any, Callable, Dict, List, Optional, Tuple


# Telemetry — defensive import so a missing module never blocks LLM calls.
try:
    from llm_telemetry import record_llm_call as _telemetry_record  # type: ignore
except Exception:
    def _telemetry_record(**_kwargs):  # type: ignore
        return None


def _safe_record(**kwargs) -> None:
    try:
        _telemetry_record(**kwargs)
    except Exception:
        pass


# ── Configuration (env-overridable) ────────────────────────────────────────

CODEX_CLI_MAX_CONCURRENT = int(os.environ.get("CODEX_CLI_MAX_CONCURRENT", "10"))
CODEX_CLI_IDLE_TTL_SEC = int(os.environ.get("CODEX_CLI_IDLE_TTL_SEC", "3600"))
CODEX_CLI_SPAWN_TIMEOUT_SEC = int(os.environ.get("CODEX_CLI_SPAWN_TIMEOUT_SEC", "30"))
CODEX_CLI_TURN_TIMEOUT_SEC = int(os.environ.get("CODEX_CLI_TURN_TIMEOUT_SEC", "120"))
CODEX_CLI_AUTH_PROBE_TTL_SEC = int(os.environ.get("CODEX_CLI_AUTH_PROBE_TTL_SEC", "60"))
CODEX_CLI_DEVICE_CODE_PARSE_TIMEOUT_SEC = int(
    os.environ.get("CODEX_CLI_DEVICE_CODE_PARSE_TIMEOUT_SEC", "15")
)
CODEX_CLI_DEVICE_CODE_TOTAL_TIMEOUT_SEC = int(
    os.environ.get("CODEX_CLI_DEVICE_CODE_TOTAL_TIMEOUT_SEC", "600")
)
CODEX_CLI_INSTALL_TIMEOUT_SEC = int(
    os.environ.get("CODEX_CLI_INSTALL_TIMEOUT_SEC", "300")
)

# Global concurrency cap (chatbot + strategies combined).
_GLOBAL_SPAWN_SEM = threading.BoundedSemaphore(value=max(1, CODEX_CLI_MAX_CONCURRENT))

# Per-host cap for /codex/install (no parallel installs).
_INSTALL_SEM = threading.BoundedSemaphore(value=1)


# ── Exceptions ──────────────────────────────────────────────────────────────


class CodexCliError(RuntimeError):
    """Base for all codex-cli provider errors."""


class CodexCliNotInstalledError(CodexCliError):
    """codex binary not found on PATH or non-executable."""


class CodexCliNotAuthenticatedError(CodexCliError):
    """codex CLI not authenticated; operator must complete `codex login`."""


class CodexCliTimeoutError(CodexCliError):
    """Subprocess exceeded the per-call wall-clock budget."""


class CodexCliProtocolError(CodexCliError):
    """JSON-RPC handshake or response framing was malformed."""


class CodexCliValidationError(CodexCliError):
    """An extra-args entry failed the allowlist gate."""


# ── extra_args allowlist ────────────────────────────────────────────────────
#
# Conservative initial allowlist — only flags we've verified are safe. Loosen
# via PR as needs emerge. Reject anything that could enable shell execution,
# file read/write outside ~/.codex/, network egress to arbitrary endpoints,
# or environment-variable injection.

_ALLOWED_LONG_FLAGS = {
    "--sandbox",        # codex sandbox mode (read-only, workspace-write, etc.)
    "--model",          # model override (already settable via the model field)
    "--config",         # ad-hoc config override; we accept k=v but validate below
    "--no-browser",     # used by device-code login; explicit OK
    "--headless",       # alias used by some versions
    "--quiet",          # less noise in logs
    "--profile",        # codex profile selector
    "--ask-for-approval",  # we always pass "never" — harmless but explicit
}

_ALLOWED_SHORT_FLAGS = set()  # no short flags accepted; ambiguity risk

_DENIED_FLAG_PREFIXES = (
    "--exec",
    "--run",
    "--shell",
    "--cmd",
    "--include-file",
    "--allow-file",
    "--env",
    "-c",  # short form of --config can be ambiguous; force long form
    "-e",
)


# Key-allowlist for ``--config key=value`` overrides. Codex's config is a
# dotted/TOML namespace; a substring denylist (e.g. block "exec") is
# trivially bypassed by harmless-looking keys that turn off the sandbox
# (`sandbox_mode=danger-full-access`), redirect API requests to an
# attacker host (`model_providers.openai.base_url=…`), enable web tools
# (`tools.web_search=true`), or load arbitrary files
# (`experimental_instructions_file=…`). We only permit the small set of
# top-level keys that are intentionally tweakable for benign tuning.
_ALLOWED_CONFIG_KEY_PREFIXES = (
    "approval",          # approval-policy related; we always pass "never"
    "approval_policy",
    "model",             # model name override (already settable via --model)
    "preferred_auth_method",
    "reasoning_effort",
    "show_raw_agent_reasoning",
    "verbose",
)


def validate_extra_args(raw: Any) -> List[str]:
    """Allowlist-gate extra_args. Accepts a string (shell-quoted) or a list.

    Returns the validated list (may be empty). Raises
    ``CodexCliValidationError`` on a denied flag.
    """
    if raw is None or raw == "":
        return []
    if isinstance(raw, str):
        try:
            tokens = shlex.split(raw)
        except ValueError as e:
            raise CodexCliValidationError(f"extra_args: shell-parse failed: {e}") from e
    elif isinstance(raw, (list, tuple)):
        tokens = [str(t) for t in raw]
    else:
        raise CodexCliValidationError(f"extra_args: unsupported type {type(raw).__name__}")
    out: List[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i].strip()
        if not tok:
            i += 1
            continue
        for denied in _DENIED_FLAG_PREFIXES:
            if tok == denied or tok.startswith(denied + "="):
                raise CodexCliValidationError(
                    f"extra_args: denied flag {tok!r}; blocked to prevent shell/file/env escape"
                )
        if tok.startswith("--"):
            flag = tok.split("=", 1)[0]
            if flag not in _ALLOWED_LONG_FLAGS:
                raise CodexCliValidationError(
                    f"extra_args: flag {flag!r} not in allowlist; permitted flags: "
                    f"{sorted(_ALLOWED_LONG_FLAGS)}"
                )
            # Special handling for --config key=value: the value is a
            # dotted TOML config path that can disable Codex's sandbox,
            # redirect API endpoints to attacker hosts, enable shell-y
            # tools, or load arbitrary files. We require a key-allowlist
            # — substring denylists on the value are unsound across
            # Codex versions. Capture value (inline or next token) and
            # append both to argv so the subprocess actually sees it.
            consumed_value: Optional[str] = None
            if flag == "--config":
                val: Optional[str] = None
                if "=" in tok:
                    val = tok.split("=", 1)[1]
                elif i + 1 < len(tokens):
                    val = tokens[i + 1]
                    consumed_value = val
                    i += 1  # skip the value token
                if val is None or "=" not in val:
                    raise CodexCliValidationError(
                        f"extra_args: --config value {val!r} must be key=value form"
                    )
                key_part = val.split("=", 1)[0].strip().lower()
                # Reject any dotted descent into nested namespaces that
                # could control providers / sandboxes / files.
                if "." in key_part:
                    raise CodexCliValidationError(
                        f"extra_args: --config nested key {key_part!r} is not on the allowlist; "
                        f"only top-level keys {sorted(_ALLOWED_CONFIG_KEY_PREFIXES)} are permitted"
                    )
                if key_part not in _ALLOWED_CONFIG_KEY_PREFIXES:
                    raise CodexCliValidationError(
                        f"extra_args: --config key {key_part!r} is not on the allowlist; "
                        f"permitted top-level keys: {sorted(_ALLOWED_CONFIG_KEY_PREFIXES)}"
                    )
            out.append(tok)
            if consumed_value is not None:
                out.append(consumed_value)
            i += 1
        elif tok.startswith("-") and not tok.startswith("--"):
            # Short flag — currently nothing allowed
            raise CodexCliValidationError(
                f"extra_args: short flag {tok!r} not permitted; use long form"
            )
        else:
            # Bare argument (e.g. value for --model). Accept if we just
            # consumed an allowed flag without =; otherwise reject.
            if out and out[-1] in _ALLOWED_LONG_FLAGS and "=" not in out[-1]:
                out.append(tok)
                i += 1
            else:
                raise CodexCliValidationError(
                    f"extra_args: stray positional argument {tok!r}"
                )
    return out


# ── Resolver / detection ────────────────────────────────────────────────────


def _resolve_cli_path(cli_path: Optional[str]) -> Optional[str]:
    """Resolve a codex binary path. Returns absolute path or None.

    Accepts:
      - None/empty -> looks up "codex" on PATH
      - bare name -> looks up on PATH
      - absolute/relative path -> only allowed when it resolves under a
        recognised system-binary root (``_is_system_path``). User-writable
        paths (``/tmp``, Windows ``%TEMP%``, UNC shares) are rejected.

    Refusing user-writable paths blocks the simplest path-hijack route:
    an operator who can write to /tmp/codex would otherwise be able to
    spawn anything from this provider.
    """
    candidate = (cli_path or "codex").strip()
    if not candidate:
        return None
    if os.path.sep in candidate or (candidate.startswith(".") and len(candidate) > 1):
        # Absolute or relative explicit path
        full = os.path.abspath(os.path.expanduser(candidate))
        if not (os.path.isfile(full) and os.access(full, os.X_OK)):
            return None
        if not _is_system_binary_path(full):
            return None
        return full
    resolved = shutil.which(candidate)
    if resolved is not None and not _is_system_binary_path(resolved):
        return None
    return resolved


def _is_system_binary_path(p: str) -> bool:
    """Return True iff *p* is under a recognised system-binary directory.

    Rejects user-writable / temp paths so a non-admin who can write a
    binary somewhere wouldn't get it run by virtue of typing the path
    into the cli_path field. Compares the realpath, which collapses
    symlinks pointing into a writable target.
    """
    try:
        normalized = os.path.realpath(p)
    except OSError:
        return False
    low = normalized.lower()
    # UNC paths are user-controlled in practice — refuse.
    if sys.platform == "win32" and (normalized.startswith("\\\\") or low.startswith("//")):
        return False
    if sys.platform == "win32":
        # Allow standard Windows install prefixes + npm's per-user prefix.
        win_roots = []
        for env_key in (
            "ProgramFiles", "ProgramFiles(x86)", "ProgramW6432",
            "SystemRoot", "ProgramData",
        ):
            v = os.environ.get(env_key)
            if v:
                win_roots.append(os.path.realpath(v).lower())
        # npm's default global prefix lives under %APPDATA%\npm — accept
        # only that subdir (not all of APPDATA).
        appdata = os.environ.get("APPDATA")
        if appdata:
            win_roots.append(os.path.realpath(os.path.join(appdata, "npm")).lower())
        # Chocolatey
        choco = os.environ.get("ChocolateyInstall")
        if choco:
            win_roots.append(os.path.realpath(choco).lower())
        # Reject explicit temp paths even if they sit under an allowed root.
        for needle in ("\\temp\\", "/temp/", "\\tmp\\", "/tmp/"):
            if needle in low:
                return False
        return any(low.startswith(root + "\\") or low == root for root in win_roots)
    # POSIX: reject /tmp, /var/tmp; accept canonical system + Homebrew + npm
    # prefixes. Anything under the operator's home that isn't an explicit
    # package-manager subtree falls through to reject so an attacker who
    # gets shell-as-non-root can't drop a binary in ~/codex and have it run.
    if normalized.startswith("/tmp") or normalized.startswith("/var/tmp"):
        return False
    posix_roots = (
        "/usr/bin", "/usr/sbin", "/usr/local/bin", "/usr/local/sbin",
        "/opt", "/bin", "/sbin",
        "/Library", "/Applications",
        "/opt/homebrew",
        # Common npm-global locations
        "/usr/lib/node_modules", "/usr/local/lib/node_modules",
    )
    # Use forward-slash literals (these are POSIX paths even when this
    # function runs on a Windows test runner via mocked sys.platform).
    return any(normalized == root or normalized.startswith(root + "/") for root in posix_roots)


def is_installed(cli_path: Optional[str] = None) -> bool:
    return _resolve_cli_path(cli_path) is not None


def get_version(cli_path: Optional[str] = None) -> Optional[str]:
    """Run ``codex --version`` with a 5s timeout. Returns the version string
    on success, None on failure. Caches per (cli_path) for 60s."""
    path = _resolve_cli_path(cli_path)
    if not path:
        return None
    key = path
    now = time.monotonic()
    with _VERSION_CACHE_LOCK:
        cached = _VERSION_CACHE.get(key)
        if cached and (now - cached[1]) < 60.0:
            return cached[0]
    try:
        result = subprocess.run(
            [path, "--version"],
            capture_output=True, text=True, timeout=5,
            **_platform_popen_kwargs(),
        )
        if result.returncode == 0:
            ver = (result.stdout or result.stderr or "").strip().splitlines()[0] if (result.stdout or result.stderr).strip() else ""
            with _VERSION_CACHE_LOCK:
                _VERSION_CACHE[key] = (ver, now)
            return ver
    except (subprocess.TimeoutExpired, OSError):
        pass
    with _VERSION_CACHE_LOCK:
        _VERSION_CACHE[key] = (None, now)
    return None


_VERSION_CACHE: Dict[str, Tuple[Optional[str], float]] = {}
_VERSION_CACHE_LOCK = threading.Lock()


_AUTH_CACHE: Dict[str, Tuple[bool, str, float]] = {}
_AUTH_CACHE_LOCK = threading.Lock()


def _platform_popen_kwargs() -> Dict[str, Any]:
    """Per-platform Popen kwargs. On Windows we use creationflags to hide
    consoles for spawned codex processes."""
    if sys.platform == "win32":
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    return {}


def is_authenticated(cli_path: Optional[str] = None) -> Tuple[bool, str]:
    """Probe codex CLI auth state. Returns (authenticated, message).

    Strategy: try `codex login status` (preferred), then fall back to
    `codex auth status`, then to inspecting ~/.codex/auth.json existence.
    Cached per cli_path for CODEX_CLI_AUTH_PROBE_TTL_SEC.
    """
    path = _resolve_cli_path(cli_path)
    if not path:
        return False, "codex CLI not installed"
    key = path
    now = time.monotonic()
    with _AUTH_CACHE_LOCK:
        cached = _AUTH_CACHE.get(key)
        if cached and (now - cached[2]) < CODEX_CLI_AUTH_PROBE_TTL_SEC:
            return cached[0], cached[1]
    ok, msg = _probe_auth_uncached(path)
    with _AUTH_CACHE_LOCK:
        _AUTH_CACHE[key] = (ok, msg, now)
    return ok, msg


def _probe_auth_uncached(cli_path: str) -> Tuple[bool, str]:
    # Try `codex login status` then `codex auth status` (different
    # versions use different subcommand names).
    for sub in (["login", "status"], ["auth", "status"], ["whoami"]):
        try:
            result = subprocess.run(
                [cli_path, *sub],
                capture_output=True, text=True, timeout=8,
                **_platform_popen_kwargs(),
            )
        except (subprocess.TimeoutExpired, OSError):
            continue
        # Treat any of these as "this subcommand exists":
        # - exit 0 with stdout containing "logged in" / "authenticated" / email
        # - exit non-zero with "not logged in" / "unauthorized" in stderr
        combined = (result.stdout or "") + "\n" + (result.stderr or "")
        low = combined.lower()
        if "command not found" in low or "unknown command" in low or "no such" in low:
            continue
        if result.returncode == 0:
            if any(t in low for t in ("logged in", "authenticated", "@")):
                return True, "authenticated"
            # exit-0 but no positive marker — accept conservatively
            return True, "codex CLI returned exit-0 from auth probe"
        if any(t in low for t in (
            "not logged in", "not authenticated", "unauthorized",
            "invalid_grant", "refresh token", "please run", "login required",
        )):
            return False, "codex CLI not authenticated; run `codex login`"
        # Unknown failure shape — fall through to next subcommand
    # Last resort: file probe.
    home = os.path.expanduser("~/.codex")
    auth_json = os.path.join(home, "auth.json")
    if os.path.isfile(auth_json) and os.path.getsize(auth_json) > 16:
        return True, "auth.json present (no subcommand probe matched)"
    return False, "codex CLI not authenticated; run `codex login`"


def invalidate_auth_cache(cli_path: Optional[str] = None) -> None:
    """Clear the auth probe cache. Called after a successful login or logout."""
    with _AUTH_CACHE_LOCK:
        if cli_path is None:
            _AUTH_CACHE.clear()
        else:
            path = _resolve_cli_path(cli_path)
            if path:
                _AUTH_CACHE.pop(path, None)


# ── OAuth failure classification ────────────────────────────────────────────


_OAUTH_FAILURE_HINTS = (
    "invalid_grant",
    "refresh token",
    "refresh_token",
    "unauthorized",
    "401",
    "not authenticated",
    "not logged in",
    "login required",
    "token expired",
    "expired_token",
    "access_denied",
)


def _classify_oauth_failure(*texts: str) -> Optional[str]:
    """Inspect stderr / error strings for OAuth-style auth failure hints.
    Returns a user-facing hint message, or None if no hint matched.
    """
    blob = " ".join((t or "").lower() for t in texts)
    for hint in _OAUTH_FAILURE_HINTS:
        if hint in blob:
            return (
                "codex CLI is not authenticated (or its tokens expired). "
                "Re-run `codex login` (or use the Sign in flow in the Models UI)."
            )
    return None


def _classify_error_text(text: str) -> CodexCliError:
    """Promote a raw error string to a typed exception."""
    if not text:
        return CodexCliError("codex CLI failed with empty error")
    low = text.lower()
    if any(t in low for t in ("not found", "no such file", "command not found")):
        return CodexCliNotInstalledError(text)
    if _classify_oauth_failure(text):
        return CodexCliNotAuthenticatedError(text)
    if "timeout" in low or "timed out" in low:
        return CodexCliTimeoutError(text)
    return CodexCliError(text)


# ── JSON-RPC over newline-delimited stdin/stdout ────────────────────────────


@dataclass
class _PendingRequest:
    request_id: int
    response_event: threading.Event = field(default_factory=threading.Event)
    response: Optional[Dict[str, Any]] = None


class _CodexAppServerClient:
    """Spawns ``codex app-server`` and speaks newline-delimited JSON-RPC 2.0
    over its stdin/stdout. Thread-safe writes via a single send lock; reads
    handled by a dedicated reader thread that demuxes responses to pending
    request futures and queues notifications/server-requests for the caller.
    """

    def __init__(
        self,
        codex_bin: str,
        codex_home: Optional[str] = None,
        extra_args: Optional[List[str]] = None,
        cwd: Optional[str] = None,
    ):
        self._codex_bin = codex_bin
        self._codex_home = codex_home
        self._extra_args = list(extra_args or [])
        self._cwd = cwd or os.getcwd()
        self._proc: Optional[subprocess.Popen] = None
        self._send_lock = threading.Lock()
        self._reader_thread: Optional[threading.Thread] = None
        self._stderr_thread: Optional[threading.Thread] = None
        self._stderr_buf: List[str] = []
        self._stderr_buf_lock = threading.Lock()
        self._stderr_buf_max = 200
        self._next_id = 1
        self._pending: Dict[int, _PendingRequest] = {}
        self._pending_lock = threading.Lock()
        self._notifications: "Queue[Dict[str, Any]]" = Queue()
        self._closed = False

    def spawn(self) -> None:
        if self._proc is not None:
            return
        cmd = [self._codex_bin, "app-server", *self._extra_args]
        env = os.environ.copy()
        if self._codex_home:
            env["CODEX_HOME"] = self._codex_home
        # Force non-interactive output so the CLI doesn't try to open a TUI
        # on stdin/stdout when launched from a non-tty parent.
        env["CODEX_NO_TUI"] = "1"
        env["TERM"] = env.get("TERM", "dumb")
        try:
            self._proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
                env=env,
                cwd=self._cwd,
                **_platform_popen_kwargs(),
            )
        except FileNotFoundError as e:
            raise CodexCliNotInstalledError(
                f"codex binary not found at {self._codex_bin!r}: {e}"
            ) from e
        # Start both reader threads, but if either start() raises (e.g.
        # OS thread-limit exhaustion) reap the subprocess so we don't
        # orphan a running codex with no consumers on its stdout pipe.
        try:
            self._reader_thread = threading.Thread(
                target=self._read_loop,
                name=f"codex-app-server-reader-{id(self):x}",
                daemon=True,
            )
            self._reader_thread.start()
            self._stderr_thread = threading.Thread(
                target=self._stderr_loop,
                name=f"codex-app-server-stderr-{id(self):x}",
                daemon=True,
            )
            self._stderr_thread.start()
        except Exception:
            try:
                self._proc.kill()
            except Exception:
                pass
            self._proc = None
            raise

    def is_alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def stderr_tail(self, n: int = 30) -> str:
        with self._stderr_buf_lock:
            return "\n".join(self._stderr_buf[-n:])

    def _read_loop(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        try:
            for line in iter(proc.stdout.readline, b""):
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    # Non-JSON-RPC line (e.g. startup banner) — discard.
                    continue
                if not isinstance(msg, dict):
                    continue
                if "id" in msg and ("result" in msg or "error" in msg):
                    rid = msg.get("id")
                    if isinstance(rid, int):
                        with self._pending_lock:
                            pend = self._pending.pop(rid, None)
                        if pend is not None:
                            pend.response = msg
                            pend.response_event.set()
                            continue
                    # Unrouted response — drop.
                elif "method" in msg:
                    # Notification or server request.
                    self._notifications.put(msg)
        except Exception:
            pass

    def _stderr_loop(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        try:
            for line in iter(proc.stderr.readline, b""):
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").rstrip()
                with self._stderr_buf_lock:
                    self._stderr_buf.append(text)
                    if len(self._stderr_buf) > self._stderr_buf_max:
                        self._stderr_buf = self._stderr_buf[-self._stderr_buf_max:]
        except Exception:
            pass

    def request(self, method: str, params: Optional[Dict[str, Any]] = None, *, timeout: float = 30.0) -> Dict[str, Any]:
        """Send a JSON-RPC request and block for the response."""
        if self._proc is None or self._proc.stdin is None:
            raise CodexCliProtocolError("codex app-server: process not started")
        if not self.is_alive():
            raise CodexCliCrashError("codex app-server: process exited", self.stderr_tail())
        with self._pending_lock:
            rid = self._next_id
            self._next_id += 1
            pend = _PendingRequest(request_id=rid)
            self._pending[rid] = pend
        payload = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            payload["params"] = params
        line = json.dumps(payload).encode("utf-8") + b"\n"
        try:
            with self._send_lock:
                self._proc.stdin.write(line)
                self._proc.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            with self._pending_lock:
                self._pending.pop(rid, None)
            raise CodexCliCrashError(f"codex app-server write failed: {e}", self.stderr_tail()) from e
        ok = pend.response_event.wait(timeout=timeout)
        if not ok:
            with self._pending_lock:
                self._pending.pop(rid, None)
            raise CodexCliTimeoutError(
                f"codex app-server: request {method!r} timed out after {timeout}s"
            )
        resp = pend.response or {}
        if "error" in resp:
            err = resp["error"] or {}
            msg = err.get("message") or str(err)
            stderr_tail = self.stderr_tail()
            hint = _classify_oauth_failure(msg, stderr_tail)
            if hint:
                raise CodexCliNotAuthenticatedError(hint)
            raise CodexCliProtocolError(f"codex app-server error: {msg}")
        return resp.get("result") or {}

    def take_notification(self, timeout: float = 0.0) -> Optional[Dict[str, Any]]:
        try:
            return self._notifications.get(timeout=timeout) if timeout > 0 else self._notifications.get_nowait()
        except Empty:
            return None

    def initialize(self) -> Dict[str, Any]:
        try:
            from version import __version__ as _intelli_version  # type: ignore
        except Exception:
            _intelli_version = "0.0.0"
        params = {
            "client_name": "intellistock",
            "client_title": "IntelliStock Agent",
            "client_version": _intelli_version,
        }
        return self.request("initialize", params, timeout=15.0)

    def thread_start(self, *, model: Optional[str] = None, system: Optional[str] = None) -> str:
        params: Dict[str, Any] = {}
        if model:
            params["model"] = model
        if system:
            params["system"] = system
        result = self.request("thread/start", params, timeout=15.0)
        tid = result.get("thread_id") or result.get("id")
        if not isinstance(tid, str) or not tid:
            raise CodexCliProtocolError(
                f"codex app-server: thread/start returned no thread_id, got {result!r}"
            )
        return tid

    def run_turn(self, *, thread_id: str, user_input: str, timeout: float = CODEX_CLI_TURN_TIMEOUT_SEC) -> Dict[str, Any]:
        params = {"thread_id": thread_id, "user_input": user_input}
        return self.request("thread/run_turn", params, timeout=timeout)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        proc = self._proc
        if proc is None:
            return
        # Only attempt a JSON-RPC shutdown if the reader thread is alive —
        # otherwise the request would block the full timeout waiting for
        # a response that nobody is reading.
        reader_alive = bool(self._reader_thread and self._reader_thread.is_alive())
        try:
            with self._send_lock:
                if proc.stdin and not proc.stdin.closed:
                    if reader_alive:
                        try:
                            self.request("shutdown", None, timeout=2.0)
                        except Exception:
                            pass
                    try:
                        proc.stdin.close()
                    except Exception:
                        pass
        except Exception:
            pass
        try:
            proc.terminate()
            proc.wait(timeout=5.0)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


class CodexCliCrashError(CodexCliError):
    """app-server subprocess exited before/during our request."""

    def __init__(self, msg: str, stderr_tail: str = ""):
        super().__init__(msg)
        self.stderr_tail = stderr_tail


# ── Turn → text extraction ─────────────────────────────────────────────────


def _extract_text_from_run_turn_result(result: Dict[str, Any]) -> str:
    """Pull the assistant's final text out of a thread/run_turn result.

    Handles both the structured ``projected_messages`` shape (Hermes-style)
    and the raw OpenAI Responses output (``output``/``message`` items).
    Returns empty string if no text was produced.
    """
    if not isinstance(result, dict):
        return ""
    # 1. Direct final_text field
    final_text = result.get("final_text") or result.get("text")
    if isinstance(final_text, str) and final_text.strip():
        return final_text.strip()
    # 2. projected_messages (Hermes-style) — last assistant message
    msgs = result.get("projected_messages") or result.get("messages") or []
    if isinstance(msgs, list):
        for m in reversed(msgs):
            if not isinstance(m, dict):
                continue
            if (m.get("role") or "").lower() != "assistant":
                continue
            content = m.get("content")
            text = _coerce_message_content_to_text(content)
            if text:
                return text
    # 3. Raw OpenAI Responses output
    output = result.get("output") or []
    if isinstance(output, list):
        chunks: List[str] = []
        for item in output:
            if not isinstance(item, dict):
                continue
            if item.get("type") != "message":
                continue
            content = item.get("content")
            text = _coerce_message_content_to_text(content)
            if text:
                chunks.append(text)
        if chunks:
            return "\n".join(chunks).strip()
    return ""


def _coerce_message_content_to_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: List[str] = []
        for c in content:
            if isinstance(c, dict):
                # Responses-API style: {"type":"output_text","text":"..."}
                t = c.get("text") or c.get("content")
                if isinstance(t, str) and t.strip():
                    parts.append(t.strip())
                elif isinstance(t, list):
                    parts.append(_coerce_message_content_to_text(t))
            elif isinstance(c, str):
                parts.append(c.strip())
        return "\n".join(p for p in parts if p).strip()
    if isinstance(content, dict):
        t = content.get("text")
        return t.strip() if isinstance(t, str) else ""
    return ""


# ── Last-call error tracking (mirrors claude_cli pattern) ──────────────────


_LAST_PLAIN_LLM_CALL_ERROR = threading.local()


def _set_last_error(msg: str) -> None:
    _LAST_PLAIN_LLM_CALL_ERROR.error = msg


def _get_last_error() -> str:
    return getattr(_LAST_PLAIN_LLM_CALL_ERROR, "error", "") or ""


# ── Public API: plain ──────────────────────────────────────────────────────


def call_codex_cli_plain(
    *,
    model: str,
    prompt: str,
    provider_config: Optional[Dict[str, Any]] = None,
    timeout_sec: Optional[int] = None,
    retries: int = 0,
    system_prompt: Optional[str] = None,
) -> str:
    """One-shot plain text completion via ``codex app-server``.

    Spawns a fresh subprocess (no session reuse for plain calls — keeps
    the call site simple; the cost is ~3s startup, paid only for
    non-structured strategy calls).
    """
    _set_last_error("")
    if not model:
        _set_last_error("codex-cli: model is required")
        return ""
    cfg = provider_config or {}
    cli_path = (cfg.get("cli_path") or "codex") or "codex"
    extra_args_raw = cfg.get("extra_args") or []
    try:
        extra_args = validate_extra_args(extra_args_raw)
    except CodexCliValidationError as e:
        _set_last_error(str(e))
        return ""
    path = _resolve_cli_path(cli_path)
    if not path:
        _set_last_error(f"codex CLI not installed (cli_path={cli_path!r})")
        return ""
    auth_ok, auth_msg = is_authenticated(cli_path)
    if not auth_ok:
        _set_last_error(auth_msg)
        return ""

    timeout = float(timeout_sec or CODEX_CLI_TURN_TIMEOUT_SEC)
    max_retries = max(0, int(retries or 0))
    last_err = ""
    for attempt in range(max_retries + 1):
        acquired = _GLOBAL_SPAWN_SEM.acquire(blocking=True, timeout=30)
        if not acquired:
            last_err = "codex-cli: spawn semaphore timeout"
            continue
        client = _CodexAppServerClient(
            codex_bin=path, codex_home=os.environ.get("CODEX_HOME"),
            extra_args=extra_args,
        )
        try:
            client.spawn()
            client.initialize()
            tid = client.thread_start(model=model, system=system_prompt)
            result = client.run_turn(thread_id=tid, user_input=prompt, timeout=timeout)
            text = _extract_text_from_run_turn_result(result)
            if text:
                # Telemetry is recorded by the outer llm_utils adapter
                # (which has model_id from the strategy's resolved Models
                # row). Recording here too would double-count rows and
                # break per-model attribution on the usage dashboard.
                return text
            last_err = "codex-cli: empty response"
        except CodexCliNotAuthenticatedError as e:
            invalidate_auth_cache(cli_path)
            _set_last_error(str(e))
            # Re-raise so the outer adapter can mark this as terminal and
            # short-circuit retry/split loops — swallowing the exception
            # here used to let strategies keep retrying an unauthenticated
            # CLI for the full output_retries budget.
            raise
        except CodexCliError as e:
            last_err = str(e)
        except Exception as e:
            last_err = f"codex-cli: unexpected error: {e}"
        finally:
            try:
                client.close()
            except Exception:
                pass
            try:
                _GLOBAL_SPAWN_SEM.release()
            except Exception:
                pass
    _set_last_error(last_err or "codex-cli: all retries exhausted")
    return ""


# ── Public API: structured (prompted JSON) ─────────────────────────────────


def _build_structured_prompt(prompt: str, output_type: Any) -> str:
    """Wrap the user prompt with a JSON-schema instruction. Uses the
    Pydantic model's JSON schema if available, else a generic instruction.
    """
    schema_json = ""
    try:
        if hasattr(output_type, "model_json_schema"):
            schema_json = json.dumps(output_type.model_json_schema(), indent=2)
        elif hasattr(output_type, "schema"):
            schema_json = json.dumps(output_type.schema(), indent=2)
    except Exception:
        schema_json = ""
    instructions = (
        "Respond with ONLY a single JSON object matching the following schema. "
        "Do not include markdown code fences, prose, or any text before or after the JSON.\n"
    )
    if schema_json:
        instructions += f"\nSchema:\n{schema_json}\n"
    return f"{instructions}\nUser request:\n{prompt}"


def _extract_json_from_text(text: str) -> Optional[str]:
    """Pull the first balanced JSON object/array out of free-form text."""
    if not text:
        return None
    # Strip markdown fences if present.
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, re.IGNORECASE)
    if fenced:
        candidate = fenced.group(1).strip()
        if candidate.startswith("{") or candidate.startswith("["):
            return candidate
    # Find first balanced { ... } or [ ... ]
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        depth = 0
        start = -1
        for i, ch in enumerate(text):
            if ch == open_ch:
                if depth == 0:
                    start = i
                depth += 1
            elif ch == close_ch and depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    return text[start : i + 1]
    # Fall through: return whole text and let parser fail explicitly.
    stripped = text.strip()
    if stripped:
        return stripped
    return None


def _validate_structured_payload(output_type: Any, raw_text: str) -> Any:
    js = _extract_json_from_text(raw_text)
    if not js:
        raise CodexCliProtocolError("codex-cli structured: no JSON found in response")
    try:
        data = json.loads(js)
    except json.JSONDecodeError as e:
        raise CodexCliProtocolError(f"codex-cli structured: JSON parse failed: {e}") from e
    # Try Pydantic v2 then v1, then return raw.
    try:
        if hasattr(output_type, "model_validate"):
            return output_type.model_validate(data)
        if hasattr(output_type, "parse_obj"):
            return output_type.parse_obj(data)
    except Exception as e:
        raise CodexCliProtocolError(f"codex-cli structured: pydantic validation failed: {e}") from e
    return data


def call_codex_cli_structured(
    *,
    model: str,
    prompt: str,
    output_type: Any,
    system_prompt: Optional[str] = None,
    provider_config: Optional[Dict[str, Any]] = None,
    timeout_sec: Optional[int] = None,
    retries: int = 0,
    output_retries: Optional[int] = None,
    use_prompt_cache: bool = False,
) -> Any:
    """Structured-output call. Uses prompted-JSON mode because Codex's
    app-server protocol has no native JSON-schema enforcement.

    ``retries`` controls HTTP-level retries (transient crashes / timeouts).
    ``output_retries`` controls JSON-validation retries (model produced
    text but it failed schema validation). Default = 2.
    """
    _set_last_error("")
    if output_type is None:
        _set_last_error("codex-cli structured: output_type is required")
        return None
    if not model or not prompt:
        _set_last_error("codex-cli structured: model and prompt are required")
        return None
    out_retries = int(output_retries if output_retries is not None else 2)
    wrapped_prompt = _build_structured_prompt(prompt, output_type)
    last_err = ""
    for out_attempt in range(out_retries + 1):
        text = call_codex_cli_plain(
            model=model,
            prompt=wrapped_prompt if out_attempt == 0 else (
                "Your previous response failed JSON validation. "
                "Reply with ONLY a single valid JSON object matching the schema. "
                "No prose, no markdown.\n\n" + wrapped_prompt
            ),
            provider_config=provider_config,
            timeout_sec=timeout_sec,
            retries=retries,
            system_prompt=system_prompt,
        )
        if not text:
            last_err = _get_last_error() or "codex-cli structured: empty response"
            continue
        try:
            result = _validate_structured_payload(output_type, text)
            if result is not None:
                return result
            last_err = "codex-cli structured: payload validation returned None"
        except CodexCliError as e:
            last_err = str(e)
    _set_last_error(last_err or "codex-cli structured: all output retries exhausted")
    return None


def call_codex_cli_chat_structured(
    *,
    conversation_id: str,
    model: str,
    prompt: str,
    output_type: Any,
    system_prompt: Optional[str] = None,
    provider_config: Optional[Dict[str, Any]] = None,
    timeout_sec: Optional[int] = None,
    retries: int = 0,
    output_retries: Optional[int] = None,
) -> Any:
    """Conversation-id variant. Currently delegates to the spawn-per-call
    structured path (Codex's app-server is itself a session, but for
    determinism we use a fresh thread per call). The conversation_id is
    threaded through telemetry only.
    """
    # NOTE: a future enhancement could persist a real Codex thread per
    # conversation_id and call ``thread/run_turn`` instead of restarting.
    # For now, parity with the structured-only strategy path is enough.
    return call_codex_cli_structured(
        model=model, prompt=prompt, output_type=output_type,
        system_prompt=system_prompt, provider_config=provider_config,
        timeout_sec=timeout_sec, retries=retries, output_retries=output_retries,
    )


# ── Installer ──────────────────────────────────────────────────────────────


def detect_install_method() -> Tuple[str, Optional[str]]:
    """Pick a likely install method for the current host. Returns
    (method, command_path) where method ∈ {"npm","brew","unknown"}.
    """
    # Prefer brew on macOS if available (system-managed updates).
    if sys.platform == "darwin":
        brew = shutil.which("brew")
        if brew:
            return "brew", brew
    npm = shutil.which("npm")
    if npm:
        return "npm", npm
    brew = shutil.which("brew")
    if brew:
        return "brew", brew
    return "unknown", None


@dataclass
class _InstallJob:
    job_id: str
    state: str = "running"  # running | success | failed
    exit_code: Optional[int] = None
    log: List[str] = field(default_factory=list)
    started_at: float = field(default_factory=time.monotonic)
    error: Optional[str] = None


class CodexInstaller:
    """Streams an install command (npm/brew) to an in-memory log buffer.

    Job-tracked so the frontend can poll for completion. One install at a
    time per host (via ``_INSTALL_SEM``).
    """

    _jobs: Dict[str, _InstallJob] = {}
    _jobs_lock = threading.Lock()
    _LOG_BUFFER_MAX = 200
    _JOB_TTL_SEC = 1800  # 30 min

    @classmethod
    def start(cls, method: Optional[str] = None) -> _InstallJob:
        chosen_method, cmd_path = (method or "", None)
        if not chosen_method:
            chosen_method, cmd_path = detect_install_method()
        else:
            cmd_path = shutil.which("brew" if chosen_method == "brew" else "npm")
        if chosen_method not in ("npm", "brew") or not cmd_path:
            raise CodexCliError(
                "No supported install method available (need npm or brew on PATH)"
            )
        # Reject non-system-path install binaries to deter PATH hijacking.
        if not cls._is_system_path(cmd_path):
            raise CodexCliError(
                f"Refusing to use install binary in non-system path: {cmd_path!r}"
            )
        if not _INSTALL_SEM.acquire(blocking=False):
            raise CodexCliError("Another codex install is already running")
        # If any setup between here and the thread-start raises, we must
        # release the semaphore — otherwise the host can never run
        # another install until the process restarts.
        worker_started = False
        try:
            cls._reap_old_jobs()
            job_id = str(uuid.uuid4())
            job = _InstallJob(job_id=job_id)
            if chosen_method == "npm":
                argv = [cmd_path, "install", "-g", "@openai/codex"]
            else:
                argv = [cmd_path, "install", "codex"]
            env = cls._sanitized_env()
            with cls._jobs_lock:
                cls._jobs[job_id] = job
            thread = threading.Thread(
                target=cls._run_subprocess, args=(job, argv, env), daemon=True,
                name=f"codex-installer-{job_id[:8]}",
            )
            thread.start()
            worker_started = True
            return job
        finally:
            if not worker_started:
                try:
                    _INSTALL_SEM.release()
                except Exception:
                    pass

    @classmethod
    def status(cls, job_id: str) -> Optional[Dict[str, Any]]:
        with cls._jobs_lock:
            job = cls._jobs.get(job_id)
        if job is None:
            return None
        return {
            "job_id": job.job_id,
            "state": job.state,
            "exit_code": job.exit_code,
            "log_tail": list(job.log[-50:]),
            "error": job.error,
        }

    @classmethod
    def _run_subprocess(cls, job: _InstallJob, argv: List[str], env: Dict[str, str]) -> None:
        proc: Optional[subprocess.Popen] = None
        try:
            proc = subprocess.Popen(
                argv,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                env=env, bufsize=1, text=True,
                **_platform_popen_kwargs(),
            )
            assert proc.stdout is not None
            deadline = time.monotonic() + CODEX_CLI_INSTALL_TIMEOUT_SEC
            for line in iter(proc.stdout.readline, ""):
                if time.monotonic() > deadline:
                    job.error = f"install timed out after {CODEX_CLI_INSTALL_TIMEOUT_SEC}s"
                    try:
                        proc.kill()
                    except Exception:
                        pass
                    break
                if not line:
                    break
                with cls._jobs_lock:
                    job.log.append(line.rstrip())
                    if len(job.log) > cls._LOG_BUFFER_MAX:
                        job.log = job.log[-cls._LOG_BUFFER_MAX:]
            proc.wait(timeout=10)
            job.exit_code = proc.returncode
            job.state = "success" if proc.returncode == 0 else "failed"
            if proc.returncode != 0 and not job.error:
                job.error = f"install exited {proc.returncode}"
        except Exception as e:
            job.state = "failed"
            job.error = f"installer crashed: {e}"
        finally:
            try:
                _INSTALL_SEM.release()
            except Exception:
                pass
            # Bust the installed/version cache so the next /codex/status
            # re-checks immediately.
            with _VERSION_CACHE_LOCK:
                _VERSION_CACHE.clear()

    @staticmethod
    def _sanitized_env() -> Dict[str, str]:
        # Inherit PATH/HOME/USER + npm_config_* / HOMEBREW_* — drop everything else.
        keep_prefixes = ("npm_config_", "HOMEBREW_", "PATH", "HOME", "USER", "TMPDIR", "TEMP", "TMP", "LANG", "LC_", "SHELL", "APPDATA", "PROGRAMFILES", "PROGRAMW6432", "SYSTEMROOT", "USERPROFILE")
        env = {k: v for k, v in os.environ.items() if any(k == p or k.startswith(p) for p in keep_prefixes)}
        return env

    @staticmethod
    def _is_system_path(p: str) -> bool:
        # Delegate to the shared system-binary path gate. Rejecting non-
        # system paths blocks the "PATH-hijack-as-installer" route where
        # an attacker drops a fake ``npm`` in a writable directory that
        # appears earlier in PATH.
        return _is_system_binary_path(p)

    @classmethod
    def _reap_old_jobs(cls) -> None:
        now = time.monotonic()
        with cls._jobs_lock:
            stale = [jid for jid, j in cls._jobs.items() if (now - j.started_at) > cls._JOB_TTL_SEC]
            for jid in stale:
                cls._jobs.pop(jid, None)


# ── Device-code login ──────────────────────────────────────────────────────


_PAIRING_URL_RE = re.compile(
    # Require the host to be EXACTLY one of the allowlisted OpenAI hosts —
    # next char must be a path/query/fragment delimiter or end-of-URL so
    # ``chatgpt.com.evil.com/...`` and userinfo tricks like
    # ``chatgpt.com@evil.com/...`` can't bypass the host check.
    r"(https?://(?:chatgpt\.com|platform\.openai\.com|auth\.openai\.com)(?=[/?#\s]|$)[^\s@]*)",
    re.IGNORECASE,
)
# Device codes are 3-6 alphanumeric chars on each side of a single
# dash. Real-world examples from codex 0.132 include "BLM4-B2C6J" (4-5),
# older flows used 4-4 ("A1B2-C3D4"). Require the dash so we don't pick
# up random 8-char tokens in unrelated stderr text.
_PAIRING_CODE_RE = re.compile(r"\b([A-Z0-9]{3,6}-[A-Z0-9]{3,6})\b")
# ANSI / VT100 SGR escape sequences (e.g. "\x1b[94m" for blue, "\x1b[0m"
# to reset). codex 0.132 wraps both the URL and the pairing code with
# these when stderr is captured to a pipe, even though stdin is closed —
# the URL regex would otherwise consume the trailing reset sequence
# (``…/device\x1b[0m``) and surface a corrupted link to the operator.
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _is_safe_pairing_url(url: str) -> bool:
    """Belt-and-braces check that *url* really is on the OpenAI host
    allowlist and contains no userinfo. The regex above narrows the
    space, but a future refactor could loosen it; this stays strict.
    """
    if not url:
        return False
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
    except Exception:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    host = (parsed.hostname or "").lower()
    if "@" in (parsed.netloc or ""):
        return False
    return host in {"chatgpt.com", "platform.openai.com", "auth.openai.com"}


@dataclass
class _LoginJob:
    job_id: str
    state: str = "pending"  # pending | parsed | success | failed | expired | cancelled
    pairing_url: Optional[str] = None
    pairing_code: Optional[str] = None
    error: Optional[str] = None
    started_at: float = field(default_factory=time.monotonic)
    proc: Optional[subprocess.Popen] = None
    stdout_buf: List[str] = field(default_factory=list)
    stderr_buf: List[str] = field(default_factory=list)


class CodexDeviceCodeLogin:
    """Drives ``codex login`` from the web UI.

    Tries the non-interactive flag in order of preference: ``--no-browser``,
    ``--headless``, ``CODEX_NO_BROWSER=1``. Captures the pairing URL +
    code from stdout via regex. Polls the subprocess for exit code.
    """

    _jobs: Dict[str, _LoginJob] = {}
    _jobs_lock = threading.Lock()
    _JOB_TTL_SEC = 1800
    _BUF_MAX = 200
    # Hard cap on concurrent live login jobs across the whole host.
    # Without this an admin could spam ``/codex/login/start`` and stack
    # hundreds of live subprocesses + buffers.
    _MAX_LIVE_JOBS = int(os.environ.get("CODEX_CLI_MAX_LIVE_LOGIN_JOBS", "5"))

    @classmethod
    def start(cls, *, cli_path: str = "codex") -> _LoginJob:
        path = _resolve_cli_path(cli_path)
        if not path:
            raise CodexCliNotInstalledError(f"codex not found at {cli_path!r}")
        cls._reap_old_jobs()
        # Hard cap on live login jobs to prevent an admin from stacking
        # hundreds of subprocesses + reader threads + buffers.
        with cls._jobs_lock:
            live = sum(
                1 for j in cls._jobs.values()
                if j.state in ("pending", "parsed")
            )
        if live >= cls._MAX_LIVE_JOBS:
            raise CodexCliError(
                f"too many concurrent codex login jobs ({live}); cancel an "
                f"existing job or wait for one to finish"
            )
        job_id = str(uuid.uuid4())
        job = _LoginJob(job_id=job_id)
        with cls._jobs_lock:
            cls._jobs[job_id] = job
        thread = threading.Thread(
            target=cls._run, args=(path, job), daemon=True,
            name=f"codex-login-{job_id[:8]}",
        )
        thread.start()
        # Block for pairing URL+code parse, up to CODEX_CLI_DEVICE_CODE_PARSE_TIMEOUT_SEC.
        deadline = time.monotonic() + CODEX_CLI_DEVICE_CODE_PARSE_TIMEOUT_SEC
        while time.monotonic() < deadline:
            with cls._jobs_lock:
                snapshot = cls._jobs.get(job_id)
                if snapshot is None:
                    break
                if snapshot.pairing_url and snapshot.pairing_code:
                    snapshot.state = "parsed" if snapshot.state == "pending" else snapshot.state
                    return snapshot
                if snapshot.state in ("failed", "expired", "cancelled"):
                    return snapshot
            time.sleep(0.2)
        # Fall through: didn't parse in time. Return current state.
        with cls._jobs_lock:
            return cls._jobs.get(job_id) or job

    @classmethod
    def status(cls, job_id: str) -> Optional[Dict[str, Any]]:
        with cls._jobs_lock:
            job = cls._jobs.get(job_id)
        if job is None:
            return None
        # Auto-probe authentication on a successful subprocess exit so the
        # caller can flip "Sign in" → "Authenticated" in one shot.
        if job.state == "success":
            try:
                invalidate_auth_cache()
            except Exception:
                pass
        return {
            "job_id": job.job_id,
            "state": job.state,
            "pairing_url": job.pairing_url,
            "pairing_code": job.pairing_code,
            "error": job.error,
            "stdout_tail": list(job.stdout_buf[-30:]),
            "stderr_tail": list(job.stderr_buf[-30:]),
        }

    @classmethod
    def cancel(cls, job_id: str) -> bool:
        with cls._jobs_lock:
            job = cls._jobs.get(job_id)
        if job is None:
            return False
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
        with cls._jobs_lock:
            if job.state in ("pending", "parsed"):
                job.state = "cancelled"
        return True

    @classmethod
    def _run(cls, cli_path: str, job: _LoginJob) -> None:
        # codex CLI v0.132+ has a dedicated ``--device-auth`` flag for
        # remote / headless machines. Without it, ``codex login`` binds
        # an OAuth loopback server inside the container and the
        # operator's browser can never reach it (the printed URL has
        # ``redirect_uri=http://localhost:1455/auth/callback`` which is
        # the *container's* localhost). With ``--device-auth``, codex
        # uses a real device-code flow: prints a chatgpt.com pairing
        # URL + 8-char code on stderr, polls the OpenAI device-code
        # endpoint until the operator approves, then writes
        # ``~/.codex/auth.json`` and exits.
        #
        # Falls back to ``codex login`` (no flag) if --device-auth is
        # not supported by the installed version — surfaces the
        # loopback URL anyway so the operator can at least see what
        # codex is doing.
        attempts = [
            (["login", "--device-auth"], {}),
            (["login"], {}),
        ]
        last_err = ""
        for args, env_overrides in attempts:
            # Reset state at the top of each iteration. Without this,
            # the second attempt's state-update block (guarded by
            # ``if job.state in ("pending", "parsed")``) would skip
            # because the first attempt had already flipped state to
            # "failed", and the loop would return the first attempt's
            # stale error even if a later attempt succeeded.
            with cls._jobs_lock:
                job.state = "pending"
                job.error = None
                job.stdout_buf = []
                job.stderr_buf = []
                job.pairing_url = None
                job.pairing_code = None

            env = os.environ.copy()
            env.update(env_overrides)
            env["TERM"] = env.get("TERM", "dumb")
            try:
                proc = subprocess.Popen(
                    [cli_path, *args],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True, bufsize=1, env=env,
                    **_platform_popen_kwargs(),
                )
            except FileNotFoundError as e:
                last_err = f"codex not found: {e}"
                continue
            with cls._jobs_lock:
                job.proc = proc
            # Reader threads.
            stop = threading.Event()
            readers: List[threading.Thread] = []
            for stream, buf_name in ((proc.stdout, "stdout_buf"), (proc.stderr, "stderr_buf")):
                t = threading.Thread(
                    target=cls._reader, args=(stream, job, buf_name, stop), daemon=True,
                )
                t.start()
                readers.append(t)
            # Wait for the subprocess to exit, parsing happens in readers.
            try:
                exit_code = proc.wait(timeout=CODEX_CLI_DEVICE_CODE_TOTAL_TIMEOUT_SEC)
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                except Exception:
                    pass
                exit_code = -1
                with cls._jobs_lock:
                    job.state = "expired"
                    job.error = "login timed out"
            stop.set()
            for t in readers:
                try:
                    t.join(timeout=2)
                except Exception:
                    pass
            with cls._jobs_lock:
                if job.state in ("pending", "parsed"):
                    if exit_code == 0:
                        job.state = "success"
                    else:
                        # Pull stderr first, then stdout — older codex
                        # versions printed the clap parse error on
                        # stderr; newer versions print runtime errors
                        # interleaved in stdout. Surface both so the
                        # operator can see what's wrong.
                        stderr_blob = "\n".join(job.stderr_buf).strip()
                        stdout_tail = "\n".join(job.stdout_buf[-10:]).strip()
                        hint = (
                            _classify_oauth_failure(stderr_blob, stdout_tail)
                            or stderr_blob[-300:]
                            or stdout_tail[-300:]
                        )
                        job.error = hint or f"codex login exited {exit_code}"
                        job.state = "failed"
            if job.state in ("success", "parsed", "pending"):
                # If we got at least a parse, this attempt is the one we
                # commit to. Don't retry with a different flag.
                return
            last_err = job.error or f"codex login exited {exit_code}"
            # Otherwise loop and try the next flag form.
        # All attempts failed.
        with cls._jobs_lock:
            if job.state != "expired":
                job.state = "failed"
                job.error = last_err or "codex login: no supported flag worked"

    @classmethod
    def _reader(cls, stream, job: _LoginJob, buf_name: str, stop: threading.Event) -> None:
        try:
            for line in iter(stream.readline, ""):
                if stop.is_set():
                    break
                if not line:
                    break
                text = line.rstrip()
                # Strip ANSI/VT100 SGR escapes BEFORE matching. codex
                # 0.132 wraps the URL and pairing code in color codes
                # (``\x1b[94m…\x1b[0m``) even when stdout/stderr are
                # captured to a pipe. Without stripping, the URL regex
                # would consume the trailing ``\x1b[0m`` (matches
                # ``[^\s@]*``) and surface a corrupted link, and the
                # pairing-code regex's word-boundary anchor would
                # fail to find a clean match next to an ESC byte.
                clean = _ANSI_ESCAPE_RE.sub("", text)
                with cls._jobs_lock:
                    buf = getattr(job, buf_name)
                    buf.append(text)
                    if len(buf) > cls._BUF_MAX:
                        setattr(job, buf_name, buf[-cls._BUF_MAX:])
                    # Parse pairing URL + code from BOTH stdout and
                    # stderr. codex 0.132's ``codex login`` writes to
                    # stderr ("Starting local login server …", "If your
                    # browser did not open, navigate to …"), while
                    # ``codex login --device-auth`` emits the
                    # ``https://auth.openai.com/codex/device`` URL +
                    # ``XXXX-XXXXX`` pairing code on stdout. The
                    # _is_safe_pairing_url gate still constrains the
                    # host to the OpenAI allowlist so a future flag
                    # change can't slip a non-OpenAI URL through.
                    if not job.pairing_url:
                        m = _PAIRING_URL_RE.search(clean)
                        if m and _is_safe_pairing_url(m.group(1)):
                            job.pairing_url = m.group(1)
                    if not job.pairing_code:
                        m = _PAIRING_CODE_RE.search(clean)
                        if m:
                            job.pairing_code = m.group(1)
        except Exception:
            pass
        try:
            stream.close()
        except Exception:
            pass

    @classmethod
    def _reap_old_jobs(cls) -> None:
        now = time.monotonic()
        with cls._jobs_lock:
            stale = [jid for jid, j in cls._jobs.items() if (now - j.started_at) > cls._JOB_TTL_SEC]
            for jid in stale:
                cls._jobs.pop(jid, None)


def logout(cli_path: str = "codex") -> Tuple[bool, str]:
    """Spawn ``codex logout``. Returns (ok, message)."""
    path = _resolve_cli_path(cli_path)
    if not path:
        return False, "codex not installed"
    try:
        result = subprocess.run(
            [path, "logout"], capture_output=True, text=True, timeout=10,
            **_platform_popen_kwargs(),
        )
        invalidate_auth_cache(cli_path)
        if result.returncode == 0:
            return True, "ok"
        return False, ((result.stderr or result.stdout or "").strip() or f"codex logout exit {result.returncode}")
    except (subprocess.TimeoutExpired, OSError) as e:
        return False, str(e)
