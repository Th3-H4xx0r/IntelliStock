"""Unit tests for the claude-cli provider module.

All subprocess interactions are mocked — these tests don't require the
real ``claude`` binary or any network access. The integration-test file
``test_claude_cli_integration.py`` covers end-to-end flows against a
real CC install (env-gated).
"""
from __future__ import annotations

import ast
import inspect
import io
import json
import os
import subprocess
import sys
import threading
import time
from collections import deque
from typing import Any, Dict, List, Optional
from unittest import mock

import pytest

# Ensure the backend dir is importable when running pytest from repo root.
_BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from chatbot import claude_cli_provider as provider  # noqa: E402
from chatbot.claude_cli_provider import (  # noqa: E402
    ClaudeCliCrashError,
    ClaudeCliError,
    ClaudeCliNotInstalledError,
    ClaudeCliNotLoggedInError,
    ClaudeCliRateLimitError,
    ClaudeCliSessionManager,
    ClaudeCliTimeoutError,
    ClaudeCliToolPolicyError,
    ClaudeCliValidationError,
    SessionState,
    _build_chat_argv,
    _build_structured_argv,
    _classify_error_text,
    _coerce_text,
    call_claude_cli_structured,
    validate_extra_args,
)
from chatbot.claude_cli_provider import test_claude_cli as _test_claude_cli  # noqa: E402  # avoid pytest collection


@pytest.fixture(autouse=True)
def _isolate_cli_path_resolution(monkeypatch):
    """Keep mocked subprocess tests independent of the host's CLI installer."""
    real_resolver = provider._resolve_cli_path

    def _resolve(requested):
        if os.path.isabs(requested):
            return real_resolver(requested)
        return requested

    monkeypatch.setattr(provider, "_resolve_cli_path", _resolve)


# ── validate_extra_args ────────────────────────────────────────────────────


class TestValidateExtraArgs:
    def test_empty_returns_empty(self):
        assert validate_extra_args("") == []
        assert validate_extra_args(None) == []   # type: ignore[arg-type]
        assert validate_extra_args("   ") == []

    def test_allows_fallback_model(self):
        assert validate_extra_args("--fallback-model claude-haiku-4-5") == [
            "--fallback-model", "claude-haiku-4-5"
        ]

    def test_allows_effort(self):
        assert validate_extra_args("--effort high") == ["--effort", "high"]

    def test_rejects_append_system_prompt(self):
        # --append-system-prompt was removed from the allowlist (security
        # audit: it lets any caller bend the system prompt outside policy).
        with pytest.raises(ValueError, match="not allowed|not on the allowlist"):
            validate_extra_args('--append-system-prompt "Be terse"')

    def test_allows_max_budget(self):
        # The validator may canonicalise (e.g. 0.50 → 0.500000); just
        # check that both tokens come back and the second is numeric.
        out = validate_extra_args("--max-budget-usd 0.50")
        assert out[0] == "--max-budget-usd"
        assert float(out[1]) == 0.5

    def test_multiple_allowed_flags(self):
        out = validate_extra_args(
            "--fallback-model claude-haiku-4-5 --effort low"
        )
        assert out == [
            "--fallback-model", "claude-haiku-4-5",
            "--effort", "low",
        ]

    def test_equals_style_flag_syntax(self):
        # Both spellings should work: --flag value AND --flag=value.
        assert validate_extra_args("--effort=high") == ["--effort", "high"]
        assert validate_extra_args(
            "--fallback-model=claude-haiku-4-5 --effort=low"
        ) == [
            "--fallback-model", "claude-haiku-4-5",
            "--effort", "low",
        ]

    def test_rejects_value_poisoning(self):
        # --effort with a value that looks like a flag must NOT slip
        # through and re-introduce a banned token in argv.
        with pytest.raises(ValueError, match="suspicious value"):
            validate_extra_args("--effort --tools")

    def test_rejects_effort_outside_enum(self):
        with pytest.raises(ValueError, match="--effort value must be one of"):
            validate_extra_args("--effort superhigh")

    def test_rejects_non_numeric_max_budget(self):
        with pytest.raises(ValueError, match="must be numeric"):
            validate_extra_args("--max-budget-usd notanumber")

    def test_rejects_negative_max_budget(self):
        # Negative values are caught by the leading-dash guard before
        # the numeric range check ever runs; either rejection is fine
        # for our threat model. Match both flavours of the message.
        with pytest.raises(ValueError, match="suspicious value|range|must be in"):
            validate_extra_args("--max-budget-usd -5")

    def test_rejects_huge_max_budget(self):
        # 5000 USD per call is unambiguous abuse — out of range.
        with pytest.raises(ValueError, match="range|must be in"):
            validate_extra_args("--max-budget-usd 5000")

    def test_rejects_oversized_token_count(self):
        # 32-pair limit (allowlist enforces _MAX_FLAG_TOKENS = 32). 64
        # tokens of valid flag-pairs are still over the cap.
        many = " ".join(["--effort low"] * 40)
        with pytest.raises(ValueError, match="too many tokens"):
            validate_extra_args(many)

    @pytest.mark.parametrize("flag", [
        "--tools", "--allowed-tools", "--allowedTools",
        "--disallowed-tools", "--disallowedTools",
        "--dangerously-skip-permissions",
        "--allow-dangerously-skip-permissions",
        "--permission-mode",
        "--mcp-config", "--add-dir",
        "--system-prompt",
        "--print", "-p",
        "--input-format", "--output-format",
        "--include-partial-messages",
        "--model",
        "--continue", "-c", "--resume", "-r",
        "--session-id",
        "--fork-session",
        # Newly added to the reject list:
        "--plugin-dir", "--plugin-url",
        "--settings", "--setting-sources",
        "--agent", "--agents",
        "--betas", "--bare",
        "--system-prompt-file", "--append-system-prompt-file",
        "--exclude-dynamic-system-prompt-sections",
    ])
    def test_rejects_hard_rejected_flags(self, flag):
        with pytest.raises(ValueError, match="not allowed|not on the allowlist"):
            validate_extra_args(f"{flag} value")

    def test_rejects_unknown_flag(self):
        with pytest.raises(ValueError, match="not on the allowlist"):
            validate_extra_args("--frobnicate widget")

    def test_rejects_missing_value(self):
        with pytest.raises(ValueError, match="requires a value"):
            validate_extra_args("--fallback-model")

    def test_rejects_bad_shell_quoting(self):
        with pytest.raises(ValueError, match="not valid shell-quoted text"):
            validate_extra_args('--effort "unterminated')


# ── argv builders ──────────────────────────────────────────────────────────


class TestArgvBuilders:
    def test_chat_argv_has_safety_flags(self):
        argv = _build_chat_argv(
            cli_path="claude",
            model="claude-sonnet-4-6",
            system_prompt="hi",
            extra_args=[],
        )
        # Safety flags must always be present.
        assert "--tools" in argv
        # `--tools ""` must be in sequence (empty string disables tools).
        ti = argv.index("--tools")
        assert argv[ti + 1] == ""
        assert "--strict-mcp-config" in argv
        assert "--no-session-persistence" in argv
        assert "--disable-slash-commands" in argv
        # Streaming flags for chatbot mode.
        assert "--input-format" in argv
        assert argv[argv.index("--input-format") + 1] == "stream-json"
        assert "--output-format" in argv
        assert argv[argv.index("--output-format") + 1] == "stream-json"
        assert "-p" in argv
        assert "--verbose" in argv
        # Caller's model and system prompt make it through.
        assert "claude-sonnet-4-6" in argv
        assert "hi" in argv

    def test_chat_argv_includes_extra_args(self):
        argv = _build_chat_argv(
            cli_path="claude",
            model="claude-sonnet-4-6",
            system_prompt="hi",
            extra_args=["--fallback-model", "claude-haiku-4-5"],
        )
        assert "--fallback-model" in argv
        assert "claude-haiku-4-5" in argv

    def test_chat_argv_injects_effort_when_set(self):
        argv = _build_chat_argv(
            cli_path="claude", model="x", system_prompt="hi",
            extra_args=[], effort="high",
        )
        i = argv.index("--effort")
        assert argv[i + 1] == "high"

    def test_chat_argv_skips_effort_when_blank(self):
        argv = _build_chat_argv(
            cli_path="claude", model="x", system_prompt="hi",
            extra_args=[], effort="",
        )
        assert "--effort" not in argv

    def test_chat_argv_skips_effort_when_invalid(self):
        argv = _build_chat_argv(
            cli_path="claude", model="x", system_prompt="hi",
            extra_args=[], effort="nuclear",  # not in enum
        )
        assert "--effort" not in argv

    def test_chat_argv_user_extra_args_effort_wins(self):
        argv = _build_chat_argv(
            cli_path="claude", model="x", system_prompt="hi",
            extra_args=["--effort", "low"], effort="high",
        )
        # User-typed value takes precedence; no duplicate flag.
        i = argv.index("--effort")
        assert argv[i + 1] == "low"
        assert argv.count("--effort") == 1

    def test_chat_argv_no_mcp_config_uses_pure_text_safety(self):
        argv = _build_chat_argv(
            cli_path="claude", model="x", system_prompt="hi",
            extra_args=[],
        )
        # Pure-text mode: --tools "" disables every built-in.
        assert "--tools" in argv
        assert argv[argv.index("--tools") + 1] == ""
        # No MCP wiring.
        assert "--mcp-config" not in argv
        assert "--allowedTools" not in argv

    def test_chat_argv_with_mcp_config_disables_builtins_and_allows_mcp(self):
        argv = _build_chat_argv(cli_path="claude", model="x", system_prompt="hi",
                                extra_args=[], mcp_config_path="/tmp/test-mcp.json")
        assert argv[argv.index("--mcp-config") + 1] == "/tmp/test-mcp.json"
        assert argv.count("--tools") == 1
        i = argv.index("--tools")
        assert argv[i + 1] == ""
        assert argv[i + 2].startswith("--")          # variadic flag: nothing else may be eaten
        assert argv[argv.index("--allowedTools") + 1] == "mcp__*"
        for flag in ("--strict-mcp-config", "--no-session-persistence",
                     "--disable-slash-commands", "--dangerously-skip-permissions"):
            assert flag in argv
        assert "--permission-mode" not in argv

    @pytest.mark.parametrize("mcp_path", [None, "/tmp/test-mcp.json"])
    def test_every_chat_mode_disables_builtin_tools(self, mcp_path):
        argv = _build_chat_argv(cli_path="claude", model="x", system_prompt="hi",
                                extra_args=["--effort", "high"], mcp_config_path=mcp_path)
        assert argv[argv.index("--tools") + 1] == ""

    def test_structured_argv_has_json_schema(self):
        argv = _build_structured_argv(
            cli_path="claude",
            model="claude-haiku-4-5",
            system_prompt="be terse",
            json_schema='{"type":"object"}',
            extra_args=[],
        )
        assert "--json-schema" in argv
        assert argv[argv.index("--json-schema") + 1] == '{"type":"object"}'
        assert "--output-format" in argv
        assert argv[argv.index("--output-format") + 1] == "json"
        # Structured mode is single-shot — no --input-format.
        assert "--input-format" not in argv
        assert "--tools" in argv


# ── _coerce_text ───────────────────────────────────────────────────────────


class TestCoerceText:
    def test_string_passes_through(self):
        assert _coerce_text("hello") == "hello"

    def test_none_returns_empty(self):
        assert _coerce_text(None) == ""

    def test_list_of_text_blocks(self):
        # CC's NDJSON sometimes ships content as a list of structured blocks.
        blocks = [{"type": "text", "text": "hello "}, {"type": "text", "text": "world"}]
        assert _coerce_text(blocks) == "hello world"

    def test_list_with_non_text_blocks_ignored(self):
        blocks = [{"type": "tool_use", "id": "abc"}, {"type": "text", "text": "ok"}]
        assert _coerce_text(blocks) == "ok"

    def test_dict_with_text_field(self):
        assert _coerce_text({"text": "hi"}) == "hi"

    def test_fallback_to_json_for_unknown_shape(self):
        out = _coerce_text({"foo": "bar"})
        assert "foo" in out and "bar" in out


# ── _classify_error_text ───────────────────────────────────────────────────


class TestClassifyError:
    def test_not_logged_in_canonical(self):
        err = _classify_error_text("Not logged in · Please run /login")
        assert isinstance(err, ClaudeCliNotLoggedInError)

    def test_not_logged_in_variant(self):
        err = _classify_error_text("Please run `claude login` to continue")
        assert isinstance(err, ClaudeCliNotLoggedInError)

    def test_rate_limit_keyword(self):
        err = _classify_error_text("You have hit your usage limit for this billing cycle")
        assert isinstance(err, ClaudeCliRateLimitError)

    def test_rate_limit_quota(self):
        err = _classify_error_text("subscription quota exceeded; retry later")
        assert isinstance(err, ClaudeCliRateLimitError)

    def test_generic_passthrough(self):
        err = _classify_error_text("something bizarre")
        assert isinstance(err, ClaudeCliError)
        assert not isinstance(err, (ClaudeCliNotLoggedInError, ClaudeCliRateLimitError))


# ── Mock subprocess plumbing ───────────────────────────────────────────────


class FakeStdin(io.StringIO):
    """Captures writes from the session manager; tracks .closed."""
    def __init__(self):
        super().__init__()
        self._closed = False

    @property
    def closed(self):  # type: ignore[override]
        return self._closed

    def close(self):  # type: ignore[override]
        self._closed = True
        super().close()


class _LineQueue:
    """A blocking iterator that yields lines pushed via ``feed_line`` and
    raises StopIteration once ``close_stream`` is called and the queue is
    drained. Mimics ``proc.stdout`` for the session manager's reader."""

    def __init__(self):
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._buf: deque = deque()
        self._closed = False

    def feed_line(self, line: str) -> None:
        if not line.endswith("\n"):
            line += "\n"
        with self._cond:
            self._buf.append(line)
            self._cond.notify_all()

    def close_stream(self) -> None:
        with self._cond:
            self._closed = True
            self._cond.notify_all()

    def __iter__(self):
        return self

    def __next__(self):
        with self._cond:
            while not self._buf and not self._closed:
                self._cond.wait(timeout=10)
            if self._buf:
                return self._buf.popleft()
            raise StopIteration


class FakeProcess:
    """A subprocess.Popen-shaped fake usable by the session manager.

    Tests drive it by calling ``feed_event`` to push NDJSON lines that
    simulate CC's stdout output, and ``crash`` to simulate EOF/exit.
    """

    def __init__(self, argv: List[str]):
        self.argv = argv
        self.stdin = FakeStdin()
        self.stdout = _LineQueue()
        self.stderr = _LineQueue()
        self.returncode: Optional[int] = None
        self._killed = False

    # API the manager calls:
    def kill(self):
        self._killed = True
        self.returncode = -9
        self.stdout.close_stream()
        self.stderr.close_stream()

    def wait(self, timeout=None):
        if self.returncode is None:
            self.returncode = 0
        return self.returncode

    def poll(self):
        return self.returncode

    # Test driver helpers:
    def feed_event(self, event: Dict[str, Any]):
        self.stdout.feed_line(json.dumps(event))

    def crash(self):
        # Simulate the process exiting (stdout EOF).
        self.returncode = 1
        self.stdout.close_stream()
        self.stderr.close_stream()


@pytest.fixture
def fake_popen(monkeypatch):
    """Patches subprocess.Popen so spawning a session never invokes the
    real ``claude``. Returns a list that accumulates every FakeProcess
    instance created; tests use ``fake_popen[-1]`` to grab the latest."""
    created: List[FakeProcess] = []

    def _factory(argv, **_kwargs):
        fp = FakeProcess(argv)
        created.append(fp)
        return fp

    monkeypatch.setattr("chatbot.claude_cli_provider.subprocess.Popen", _factory)
    yield created


@pytest.fixture
def manager():
    """A fresh manager per test, with a tiny idle TTL and sweeper interval
    so eviction tests don't take real minutes."""
    mgr = ClaudeCliSessionManager(
        idle_ttl_sec=1,
        sweeper_interval_sec=60,  # we don't rely on the sweeper for most tests
        spawn_timeout_sec=5,
        turn_timeout_sec=5,
    )
    yield mgr
    mgr.shutdown_all()


# ── Session manager: spawn / reuse / model switch ──────────────────────────


def _make_result(text: str = "ok", **extra) -> Dict[str, Any]:
    out = {
        "type": "result",
        "result": text,
        "is_error": False,
        "stop_reason": "end_turn",
        "duration_ms": 1234,
        "duration_api_ms": 800,
    }
    out.update(extra)
    return out


class TestSessionManagerCore:
    def test_spawns_on_first_call(self, fake_popen, manager):
        # Feed the result before we call send_turn so the reader thread
        # can deliver it as soon as it starts. (The fake stdout queue is
        # thread-safe and the reader spawns in _spawn.)
        def _arm_response():
            # Wait briefly for FakeProcess to be created, then push.
            for _ in range(50):
                if fake_popen:
                    break
                time.sleep(0.01)
            assert fake_popen, "Popen was never called"
            fake_popen[-1].feed_event(_make_result("hello"))

        threading.Thread(target=_arm_response, daemon=True).start()
        out = manager.send_turn(
            conversation_id="conv-1",
            messages=[{"role": "user", "content": "say hello"}],
            system_prompt="sys",
            model="claude-haiku-4-5",
            cli_path="claude",
            extra_args=[],
        )
        assert out["content"] == "hello"
        assert out["tool_calls"] == []
        assert out["finish_reason"] == "end_turn"
        assert len(fake_popen) == 1

    def test_reuses_warm_session_across_turns(self, fake_popen, manager):
        # First turn
        threading.Thread(
            target=lambda: (_wait_for_proc(fake_popen), fake_popen[-1].feed_event(_make_result("one"))),
            daemon=True,
        ).start()
        manager.send_turn(
            conversation_id="conv-A",
            messages=[{"role": "user", "content": "first"}],
            system_prompt="sys",
            model="claude-haiku-4-5",
            cli_path="claude",
            extra_args=[],
        )
        assert len(fake_popen) == 1

        # Second turn — should hit the SAME process (no second Popen call).
        threading.Thread(
            target=lambda: fake_popen[-1].feed_event(_make_result("two")),
            daemon=True,
        ).start()
        out = manager.send_turn(
            conversation_id="conv-A",
            messages=[
                {"role": "user", "content": "first"},
                {"role": "assistant", "content": "one"},
                {"role": "user", "content": "second"},
            ],
            system_prompt="sys",
            model="claude-haiku-4-5",
            cli_path="claude",
            extra_args=[],
        )
        assert out["content"] == "two"
        assert len(fake_popen) == 1, "spawned a second subprocess on a warm session"

    def test_respawns_on_model_change(self, fake_popen, manager):
        threading.Thread(
            target=lambda: (_wait_for_proc(fake_popen), fake_popen[-1].feed_event(_make_result("ha"))),
            daemon=True,
        ).start()
        manager.send_turn(
            conversation_id="conv-B",
            messages=[{"role": "user", "content": "hi"}],
            system_prompt="sys",
            model="claude-haiku-4-5",
            cli_path="claude",
            extra_args=[],
        )
        assert len(fake_popen) == 1

        # Same conversation, different model: should respawn.
        threading.Thread(
            target=lambda: (_wait_for_proc(fake_popen, expect_count=2),
                            fake_popen[-1].feed_event(_make_result("so"))),
            daemon=True,
        ).start()
        manager.send_turn(
            conversation_id="conv-B",
            messages=[{"role": "user", "content": "hi"}],
            system_prompt="sys",
            model="claude-sonnet-4-6",   # ← changed
            cli_path="claude",
            extra_args=[],
        )
        assert len(fake_popen) == 2

    def test_messages_sent_counter_diffs(self, fake_popen, manager):
        # First turn pushes 1 message.
        threading.Thread(
            target=lambda: (_wait_for_proc(fake_popen), fake_popen[-1].feed_event(_make_result("ack1"))),
            daemon=True,
        ).start()
        manager.send_turn(
            conversation_id="conv-C",
            messages=[{"role": "user", "content": "msg1"}],
            system_prompt="sys",
            model="claude-haiku-4-5",
            cli_path="claude",
            extra_args=[],
        )

        # Reset the captured stdin so we can verify only the diff is pushed.
        # FakeStdin is a StringIO; we record the second-turn write.
        before = fake_popen[-1].stdin.getvalue()

        threading.Thread(
            target=lambda: fake_popen[-1].feed_event(_make_result("ack2")),
            daemon=True,
        ).start()
        manager.send_turn(
            conversation_id="conv-C",
            messages=[
                {"role": "user", "content": "msg1"},
                {"role": "assistant", "content": "ack1"},
                {"role": "user", "content": "msg2"},
            ],
            system_prompt="sys",
            model="claude-haiku-4-5",
            cli_path="claude",
            extra_args=[],
        )

        after = fake_popen[-1].stdin.getvalue()
        diff = after[len(before):]
        # The diff should only contain msg2 (and possibly the prior assistant
        # turn if we replay it — but our manager only sends what's after
        # messages_sent), not msg1.
        assert "msg2" in diff
        assert "msg1" not in diff


class TestSessionManagerErrors:
    def test_timeout_raises_and_marks_crashed(self, fake_popen, manager):
        # No event fed — the reader will time out waiting.
        manager._turn_timeout_sec = 1
        with pytest.raises(ClaudeCliTimeoutError):
            manager.send_turn(
                conversation_id="conv-T",
                messages=[{"role": "user", "content": "hang"}],
                system_prompt="sys",
                model="claude-haiku-4-5",
                cli_path="claude",
                extra_args=[],
                timeout_sec=1,
            )

    def test_not_logged_in_error_event(self, fake_popen, manager):
        threading.Thread(
            target=lambda: (_wait_for_proc(fake_popen), fake_popen[-1].feed_event({
                "type": "result",
                "is_error": True,
                "result": "Not logged in · Please run /login",
                "stop_reason": "stop_sequence",
            })),
            daemon=True,
        ).start()
        with pytest.raises(ClaudeCliNotLoggedInError):
            manager.send_turn(
                conversation_id="conv-NL",
                messages=[{"role": "user", "content": "test"}],
                system_prompt="sys",
                model="claude-haiku-4-5",
                cli_path="claude",
                extra_args=[],
            )

    def test_subprocess_crash_marks_session(self, fake_popen, manager):
        # Crashing the first process triggers transparent retry; the
        # second attempt's process never gets a response either (test
        # doesn't arm one), so the call ultimately fails. Important
        # invariant: TWO subprocesses were spawned (proving the retry).
        threading.Thread(
            target=lambda: (_wait_for_proc(fake_popen), fake_popen[-1].crash()),
            daemon=True,
        ).start()
        manager._turn_timeout_sec = 1
        with pytest.raises((ClaudeCliCrashError, ClaudeCliTimeoutError)):
            manager.send_turn(
                conversation_id="conv-X",
                messages=[{"role": "user", "content": "test"}],
                system_prompt="sys",
                model="claude-haiku-4-5",
                cli_path="claude",
                extra_args=[],
                timeout_sec=1,
            )
        # Retry happened — fresh process spawned after the first crashed.
        assert len(fake_popen) >= 2, "expected respawn after crash, only saw one Popen"

    def test_file_not_found_raises_not_installed(self, monkeypatch, manager):
        def _boom(*a, **kw):
            raise FileNotFoundError("nope")
        monkeypatch.setattr("chatbot.claude_cli_provider.subprocess.Popen", _boom)
        with pytest.raises(ClaudeCliNotInstalledError):
            manager.send_turn(
                conversation_id="conv-F",
                messages=[{"role": "user", "content": "test"}],
                system_prompt="sys",
                model="claude-haiku-4-5",
                cli_path="claude",
                extra_args=[],
            )


# ── Session manager: built-in tool guard ───────────────────────────────────


def _feed_after_write(fake_popen, events: List[Dict[str, Any]]) -> None:
    """Feed ``events`` once the turn's user message is on stdin.

    The turn slot is registered before the stdin write, so waiting for a
    non-empty stdin guarantees a caller is waiting when the events land.
    """
    def _run():
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if fake_popen and fake_popen[-1].stdin.getvalue():
                break
            time.sleep(0.01)
        for event in events:
            fake_popen[-1].feed_event(event)

    threading.Thread(target=_run, daemon=True).start()


def _send(manager) -> Dict[str, Any]:
    return manager.send_turn(
        conversation_id="conv-guard",
        messages=[{"role": "user", "content": "hi"}],
        system_prompt="sys",
        model="claude-haiku-4-5",
        cli_path="claude",
        extra_args=[],
    )


def _init(tools):
    return {"type": "system", "subtype": "init", "tools": tools}


class TestBuiltinToolGuard:
    def test_init_with_a_builtin_tool_refuses_the_session(self, fake_popen, manager):
        _feed_after_write(fake_popen, [_init(["Bash", "mcp__intellistock__list_instances"])])
        with pytest.raises(ClaudeCliToolPolicyError, match="Bash"):
            _send(manager)
        assert fake_popen[-1]._killed is True

    def test_init_with_only_toolsearch_extra_is_refused(self, fake_popen, manager):
        _feed_after_write(fake_popen, [_init(["ToolSearch", "mcp__intellistock__list_instances"])])
        with pytest.raises(ClaudeCliToolPolicyError, match="ToolSearch"):
            _send(manager)

    @pytest.mark.parametrize("tools", [["mcp__intellistock__list_instances"], [], None])
    def test_init_with_mcp_only_empty_or_missing_tools_is_accepted(self, fake_popen, manager, tools):
        init = _init(tools) if tools is not None else {"type": "system", "subtype": "init"}
        _feed_after_write(fake_popen, [init, _make_result("ok")])
        assert _send(manager)["content"] == "ok"
        assert fake_popen[-1]._killed is False

    def test_builtin_tools_in_init_helper(self):
        assert provider._builtin_tools_in_init(_init(["Read", "mcp__a__b", "Bash"])) == ["Bash", "Read"]
        assert provider._builtin_tools_in_init({"type": "system", "subtype": "init", "tools": "Bash"}) == []


class TestSessionManagerIdleSweep:
    def test_sweeper_evicts_idle(self, fake_popen, manager):
        # First turn lands and goes idle.
        threading.Thread(
            target=lambda: (_wait_for_proc(fake_popen), fake_popen[-1].feed_event(_make_result("ok"))),
            daemon=True,
        ).start()
        manager.send_turn(
            conversation_id="conv-Sweep",
            messages=[{"role": "user", "content": "test"}],
            system_prompt="sys",
            model="claude-haiku-4-5",
            cli_path="claude",
            extra_args=[],
        )
        assert "conv-Sweep" in manager._sessions

        # Backdate last_activity past the TTL.
        manager._sessions["conv-Sweep"].last_activity = time.monotonic() - 100

        manager._sweep_once()
        assert "conv-Sweep" not in manager._sessions


# ── Structured (spawn-per-call) ────────────────────────────────────────────


class _FakeBaseModel:
    """Minimal Pydantic-v2-like stub to avoid a real Pydantic import here."""
    @classmethod
    def model_json_schema(cls):
        return {"type": "object", "properties": {"value": {"type": "string"}}}

    @classmethod
    def model_validate(cls, data):
        if not isinstance(data, dict) or "value" not in data:
            raise ValueError(f"missing 'value' in {data!r}")
        inst = cls()
        inst.value = data["value"]
        return inst


class TestCallClaudeCliStructured:
    def _completed(self, stdout: str, returncode: int = 0, stderr: str = "") -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(args=["claude"], returncode=returncode, stdout=stdout, stderr=stderr)

    def test_happy_path(self, monkeypatch):
        envelope = json.dumps({
            "type": "result",
            "is_error": False,
            "result": json.dumps({"value": "hi there"}),
        })
        monkeypatch.setattr(
            "chatbot.claude_cli_provider.subprocess.run",
            lambda *a, **k: self._completed(envelope),
        )
        out = call_claude_cli_structured(
            model="claude-haiku-4-5",
            system_prompt="be terse",
            user_prompt="say hi",
            output_schema=_FakeBaseModel,
            cli_path="claude",
        )
        assert isinstance(out, _FakeBaseModel)
        assert out.value == "hi there"

    def test_is_error_classifies(self, monkeypatch):
        envelope = json.dumps({
            "type": "result",
            "is_error": True,
            "result": "Not logged in · Please run /login",
        })
        monkeypatch.setattr(
            "chatbot.claude_cli_provider.subprocess.run",
            lambda *a, **k: self._completed(envelope),
        )
        with pytest.raises(ClaudeCliNotLoggedInError):
            call_claude_cli_structured(
                model="claude-haiku-4-5",
                system_prompt="be terse",
                user_prompt="x",
                output_schema=_FakeBaseModel,
                cli_path="claude",
            )

    def test_non_json_result_raises_validation(self, monkeypatch):
        envelope = json.dumps({
            "type": "result",
            "is_error": False,
            "result": "this is not json",
        })
        monkeypatch.setattr(
            "chatbot.claude_cli_provider.subprocess.run",
            lambda *a, **k: self._completed(envelope),
        )
        with pytest.raises(ClaudeCliValidationError):
            call_claude_cli_structured(
                model="claude-haiku-4-5",
                system_prompt="be terse",
                user_prompt="x",
                output_schema=_FakeBaseModel,
                cli_path="claude",
            )

    def test_timeout_raises(self, monkeypatch):
        def _timeout(*a, **k):
            raise subprocess.TimeoutExpired(cmd=["claude"], timeout=1)
        monkeypatch.setattr("chatbot.claude_cli_provider.subprocess.run", _timeout)
        with pytest.raises(ClaudeCliTimeoutError):
            call_claude_cli_structured(
                model="claude-haiku-4-5",
                system_prompt="sys",
                user_prompt="x",
                output_schema=_FakeBaseModel,
                cli_path="claude",
                timeout_sec=1,
            )

    def test_file_not_found_raises_not_installed(self, monkeypatch):
        def _nope(*a, **k):
            raise FileNotFoundError("no claude here")
        monkeypatch.setattr("chatbot.claude_cli_provider.subprocess.run", _nope)
        with pytest.raises(ClaudeCliNotInstalledError):
            call_claude_cli_structured(
                model="claude-haiku-4-5",
                system_prompt="sys",
                user_prompt="x",
                output_schema=_FakeBaseModel,
                cli_path="/nonexistent/claude",
            )

    def test_schema_must_have_model_json_schema(self):
        class NotPydantic: pass
        with pytest.raises(ClaudeCliError):
            call_claude_cli_structured(
                model="claude-haiku-4-5",
                system_prompt="sys",
                user_prompt="x",
                output_schema=NotPydantic,
                cli_path="claude",
            )


# ── test_claude_cli (connection test endpoint helper) ──────────────────────


class TestTestClaudeCliHelper:
    def test_not_installed(self, monkeypatch):
        # Use a fully-bogus path so _resolve_cli_path's absolute-path
        # branch surfaces "does not exist" before we ever subprocess.run.
        def _runner(argv, **kw):
            raise FileNotFoundError("nope")
        monkeypatch.setattr("chatbot.claude_cli_provider.subprocess.run", _runner)
        out = _test_claude_cli(cli_path="/nonexistent/claude")
        assert out["ok"] is False
        assert out["version"] is None
        # Either the resolver caught the missing path or the subprocess
        # FileNotFoundError did — both are acceptable error wordings.
        assert ("not found" in out["error"] or "does not exist" in out["error"]), out["error"]

    def test_happy_path(self, monkeypatch):
        calls = {"n": 0}

        def _runner(argv, **kw):
            calls["n"] += 1
            if "--version" in argv:
                return subprocess.CompletedProcess(args=argv, returncode=0,
                                                   stdout="2.1.139 (Claude Code)\n", stderr="")
            envelope = json.dumps({
                "type": "result",
                "is_error": False,
                "result": "ok",
            })
            return subprocess.CompletedProcess(args=argv, returncode=0, stdout=envelope, stderr="")

        monkeypatch.setattr("chatbot.claude_cli_provider.subprocess.run", _runner)
        out = _test_claude_cli(cli_path="claude")
        assert out["ok"] is True
        assert out["version"] == "2.1.139 (Claude Code)"
        assert out["logged_in"] is True
        assert out["model_response"] == "ok"

    def test_not_logged_in(self, monkeypatch):
        def _runner(argv, **kw):
            if "--version" in argv:
                return subprocess.CompletedProcess(args=argv, returncode=0,
                                                   stdout="2.1.139\n", stderr="")
            envelope = json.dumps({
                "type": "result",
                "is_error": True,
                "result": "Not logged in · Please run /login",
            })
            return subprocess.CompletedProcess(args=argv, returncode=1, stdout=envelope, stderr="")

        monkeypatch.setattr("chatbot.claude_cli_provider.subprocess.run", _runner)
        out = _test_claude_cli(cli_path="claude")
        assert out["ok"] is False
        assert out["logged_in"] is False
        assert "not logged in" in out["error"].lower()


# ── Child environment allowlist ─────────────────────────────────────────────


_SECRETS = ("JWT_SECRET", "POSTGRES_PASSWORD", "INTELLISTOCK_CRED_KEY", "SECRET_AUTH_KEY",
            "ALPACA_API_KEY", "ALPACA_SECRET_KEY", "OPENROUTER_API_KEY", "PG_DSN",
            "MCP_SERVICE_TOKEN", "ANTHROPIC_API_KEY", "DISCORD_WEBHOOK_URL")


class TestChildEnvAllowlist:
    def test_child_env_drops_every_secret(self, monkeypatch):
        for k in _SECRETS:
            monkeypatch.setenv(k, "canary")
        env = provider._claude_child_env("/tmp/cc-home-x")
        assert not set(_SECRETS) & set(env)
        assert env["HOME"] == "/tmp/cc-home-x"

    def test_child_env_keeps_what_claude_needs(self, monkeypatch):
        keep = {"PATH": "/usr/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "LC_CTYPE": "C.UTF-8",
                "TZ": "UTC", "TERM": "xterm", "TMPDIR": "/tmp", "CLAUDE_CONFIG_DIR": "/cfg",
                "HTTPS_PROXY": "http://proxy:3128", "NO_PROXY": "127.0.0.1",
                "NODE_EXTRA_CA_CERTS": "/ca.pem", "CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat01-x"}
        for k, v in keep.items():
            monkeypatch.setenv(k, v)
        env = provider._claude_child_env()
        assert {k: env.get(k) for k in keep} == keep
        assert env["HOME"] == os.environ["HOME"]

    def test_child_env_supplies_the_token_file(self, tmp_path, monkeypatch):
        (tmp_path / ".claude").mkdir()
        (tmp_path / ".claude" / "oauth_token").write_text("sk-ant-oat01-file\n")
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
        env = provider._claude_child_env("/tmp/cc-home-y")
        assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "sk-ant-oat01-file"
        assert env["HOME"] == "/tmp/cc-home-y"

    def test_allowlist_holds_no_secret_shaped_name(self):
        keys = provider._CHILD_ENV_KEYS
        assert not [k for k in keys if any(s in k for s in ("SECRET", "PASSWORD", "DSN", "API_KEY", "CRED"))]
        assert [k for k in keys if "TOKEN" in k] == ["CLAUDE_CODE_OAUTH_TOKEN"]

    def test_every_subprocess_call_passes_an_explicit_env(self):
        tree = ast.parse(inspect.getsource(provider))
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute) and n.func.attr in ("run", "Popen")
                 and isinstance(n.func.value, ast.Name) and n.func.value.id == "subprocess"]
        assert len(calls) >= 8
        assert [n.lineno for n in calls if not any(k.arg == "env" for k in n.keywords)] == []
        assert "os.environ.copy()" not in inspect.getsource(provider)

    def test_spawn_hands_popen_the_allowlisted_env(self, tmp_path, monkeypatch, manager):
        monkeypatch.setenv("JWT_SECRET", "canary")
        monkeypatch.setenv("CLAUDE_CLI_MCP_DIR", str(tmp_path))
        runtime_home = str(tmp_path / "cc-home")
        monkeypatch.setattr(provider, "_prepare_runtime_home", lambda sess: runtime_home)
        monkeypatch.setattr(provider, "_cleanup_runtime_home", lambda path: None)
        created: List[FakeProcess] = []
        seen: List[Dict[str, Any]] = []

        def _factory(argv, **kwargs):
            seen.append(kwargs)
            fp = FakeProcess(argv)
            created.append(fp)
            return fp

        monkeypatch.setattr("chatbot.claude_cli_provider.subprocess.Popen", _factory)
        _feed_after_write(created, [_make_result("ok")])
        assert _send(manager)["content"] == "ok"
        env = seen[-1]["env"]
        assert "JWT_SECRET" not in env
        assert env["HOME"] == runtime_home

    def test_structured_call_hands_run_the_allowlisted_env(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET", "canary")
        envelope = json.dumps({
            "type": "result",
            "is_error": False,
            "result": json.dumps({"value": "hi there"}),
        })
        seen: List[Dict[str, str]] = []

        def _run(argv, **kwargs):
            seen.append(kwargs["env"])
            return subprocess.CompletedProcess(args=argv, returncode=0, stdout=envelope, stderr="")

        monkeypatch.setattr("chatbot.claude_cli_provider.subprocess.run", _run)
        out = call_claude_cli_structured(
            model="claude-haiku-4-5",
            system_prompt="be terse",
            user_prompt="say hi",
            output_schema=_FakeBaseModel,
            cli_path="claude",
        )
        assert out.value == "hi there"
        assert seen and "JWT_SECRET" not in seen[-1]

    @pytest.mark.parametrize("wait", ["300", None, "abc"])
    def test_mcp_config_env_block(self, tmp_path, monkeypatch, wait):
        monkeypatch.setenv("CLAUDE_CLI_MCP_DIR", str(tmp_path))
        if wait is None:
            monkeypatch.delenv("INTELLISTOCK_MCP_CONFIRM_WAIT_SEC", raising=False)
        else:
            monkeypatch.setenv("INTELLISTOCK_MCP_CONFIRM_WAIT_SEC", wait)
        sess = provider._Session(conversation_id="conv-cfg-1", model="m", cli_path="claude",
                                 system_prompt="s", user_id="u1")
        with open(provider._write_session_mcp_config(sess), encoding="utf-8") as fh:
            cfg = json.load(fh)
        env = cfg["mcpServers"]["intellistock"]["env"]
        assert env["CLAUDE_CODE_OAUTH_TOKEN"] == ""
        assert {"INTELLISTOCK_MCP_URL", "INTELLISTOCK_MCP_TOKEN",
                "INTELLISTOCK_CONVERSATION_ID", "INTELLISTOCK_USER_ID"} <= set(env)
        assert env["INTELLISTOCK_MCP_TOKEN"] == sess.mcp_token
        assert env.get("INTELLISTOCK_MCP_CONFIRM_WAIT_SEC") == ("300" if wait == "300" else None)


# ── Helpers ────────────────────────────────────────────────────────────────


def _wait_for_proc(fake_popen_list, expect_count: int = 1, timeout: float = 2.0):
    """Block briefly until a FakeProcess has been spawned. Used to avoid
    a race between the spawning thread and the test thread that wants to
    feed events into the latest process."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if len(fake_popen_list) >= expect_count:
            return
        time.sleep(0.01)
    raise AssertionError(
        f"FakeProcess was never created (expected at least {expect_count}, "
        f"got {len(fake_popen_list)})"
    )
