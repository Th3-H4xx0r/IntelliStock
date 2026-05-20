"""Unit tests for the codex-cli provider module.

Subprocess interactions are mocked — no real ``codex`` binary required.
The companion file ``test_strategy_codex_cli_dispatch.py`` covers the
llm_utils integration paths.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from typing import Any, Dict, List, Optional
from unittest import mock

import pytest

_BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from chatbot import codex_cli_provider as provider  # noqa: E402
from chatbot.codex_cli_provider import (  # noqa: E402
    CodexCliCrashError,
    CodexCliError,
    CodexCliNotAuthenticatedError,
    CodexCliNotInstalledError,
    CodexCliProtocolError,
    CodexCliTimeoutError,
    CodexCliValidationError,
    CodexDeviceCodeLogin,
    CodexInstaller,
    _classify_error_text,
    _classify_oauth_failure,
    _extract_json_from_text,
    detect_install_method,
    invalidate_auth_cache,
    is_authenticated,
    is_installed,
    validate_extra_args,
)


# ── validate_extra_args ────────────────────────────────────────────────────


class TestValidateExtraArgs:
    def test_empty_returns_empty(self):
        assert validate_extra_args("") == []
        assert validate_extra_args(None) == []
        assert validate_extra_args("   ") == []

    def test_allows_sandbox(self):
        assert validate_extra_args("--sandbox read-only") == ["--sandbox", "read-only"]

    def test_allows_model_override(self):
        assert validate_extra_args("--model gpt-5-codex") == ["--model", "gpt-5-codex"]

    def test_allows_quiet(self):
        assert validate_extra_args("--quiet") == ["--quiet"]

    def test_allows_no_browser(self):
        assert validate_extra_args("--no-browser") == ["--no-browser"]

    def test_rejects_exec_flag(self):
        with pytest.raises(CodexCliValidationError, match="denied flag|not in allowlist"):
            validate_extra_args("--exec /usr/bin/curl evil.com")

    def test_rejects_run_flag(self):
        with pytest.raises(CodexCliValidationError, match="denied flag|not in allowlist"):
            validate_extra_args("--run rm -rf /")

    def test_rejects_shell_flag(self):
        with pytest.raises(CodexCliValidationError, match="denied flag|not in allowlist"):
            validate_extra_args("--shell bash")

    def test_rejects_include_file(self):
        with pytest.raises(CodexCliValidationError, match="denied flag|not in allowlist"):
            validate_extra_args("--include-file /etc/passwd")

    def test_rejects_env_flag(self):
        with pytest.raises(CodexCliValidationError, match="denied flag|not in allowlist"):
            validate_extra_args("--env API_KEY=stolen")

    def test_rejects_unknown_long_flag(self):
        with pytest.raises(CodexCliValidationError, match="not in allowlist"):
            validate_extra_args("--unsafe-thing yes")

    def test_rejects_short_flag(self):
        with pytest.raises(CodexCliValidationError, match="short flag .* not permitted"):
            validate_extra_args("-q")

    def test_rejects_short_c_flag(self):
        # -c is in DENIED_FLAG_PREFIXES — short form of config gets blocked
        # before short-flag check (denied list runs first).
        with pytest.raises(CodexCliValidationError, match="denied flag|short flag"):
            validate_extra_args("-c foo=bar")

    def test_config_blocks_nested_dotted_key(self):
        # Nested config keys (e.g. sandbox.exec_path) are unsound to allow —
        # they can disable the codex sandbox or redirect the API endpoint.
        with pytest.raises(CodexCliValidationError, match="not on the allowlist|nested key"):
            validate_extra_args("--config sandbox.exec_path=/bin/bash")

    def test_config_blocks_provider_base_url_override(self):
        # The model_providers.* namespace can redirect requests to an attacker host.
        with pytest.raises(CodexCliValidationError, match="not on the allowlist|nested key"):
            validate_extra_args("--config model_providers.openai.base_url=http://evil.com")

    def test_config_blocks_dangerous_top_level_key(self):
        # sandbox_mode=danger-full-access disables the sandbox entirely.
        with pytest.raises(CodexCliValidationError, match="not on the allowlist"):
            validate_extra_args("--config sandbox_mode=danger-full-access")

    def test_config_blocks_unknown_top_level_key(self):
        with pytest.raises(CodexCliValidationError, match="not on the allowlist"):
            validate_extra_args("--config env=KEY=evil")

    def test_config_requires_key_value_form(self):
        # `--config foo` without `=` is rejected.
        with pytest.raises(CodexCliValidationError, match="key=value"):
            validate_extra_args("--config approval")

    def test_config_safe_value_allowed(self):
        # A clean config k=v on an allowed top-level key passes and
        # propagates BOTH the flag and the value to argv.
        out = validate_extra_args("--config approval=never")
        assert "--config" in out
        assert "approval=never" in out

    def test_equals_style_flag(self):
        assert validate_extra_args("--model=gpt-5-codex") == ["--model=gpt-5-codex"]

    def test_list_input(self):
        out = validate_extra_args(["--quiet", "--sandbox", "read-only"])
        assert out == ["--quiet", "--sandbox", "read-only"]

    def test_unsupported_type_raises(self):
        with pytest.raises(CodexCliValidationError, match="unsupported type"):
            validate_extra_args(123)  # type: ignore[arg-type]

    def test_stray_positional_rejected(self):
        with pytest.raises(CodexCliValidationError, match="stray positional"):
            validate_extra_args("hello world")


# ── _resolve_cli_path / is_installed ────────────────────────────────────────


class TestResolveCliPath:
    def test_bare_name_uses_path(self):
        # Mock both shutil.which and _is_system_binary_path so the test
        # passes on any host platform (the path-safety gate is exercised
        # separately by TestIsSystemBinaryPath).
        with mock.patch("chatbot.codex_cli_provider.shutil.which", return_value="/usr/local/bin/codex"), \
             mock.patch("chatbot.codex_cli_provider._is_system_binary_path", return_value=True):
            assert provider._resolve_cli_path("codex") == "/usr/local/bin/codex"

    def test_missing_returns_none(self):
        with mock.patch("chatbot.codex_cli_provider.shutil.which", return_value=None):
            assert provider._resolve_cli_path("codex") is None

    def test_default_empty_resolves_codex(self):
        with mock.patch("chatbot.codex_cli_provider.shutil.which", return_value="/x/codex"), \
             mock.patch("chatbot.codex_cli_provider._is_system_binary_path", return_value=True):
            assert provider._resolve_cli_path(None) == "/x/codex"
            assert provider._resolve_cli_path("") == "/x/codex"

    def test_rejects_non_system_path_for_bare_name(self):
        # Even when shutil.which finds the binary, refuse it if the
        # resolved location isn't under a recognised system root.
        with mock.patch("chatbot.codex_cli_provider.shutil.which", return_value="/tmp/codex"), \
             mock.patch("chatbot.codex_cli_provider._is_system_binary_path", return_value=False):
            assert provider._resolve_cli_path("codex") is None


class TestIsInstalled:
    def test_true_when_resolved(self):
        with mock.patch("chatbot.codex_cli_provider.shutil.which", return_value="/x/codex"), \
             mock.patch("chatbot.codex_cli_provider._is_system_binary_path", return_value=True):
            assert is_installed() is True

    def test_false_when_missing(self):
        with mock.patch("chatbot.codex_cli_provider.shutil.which", return_value=None):
            assert is_installed() is False


class TestIsSystemBinaryPath:
    def test_rejects_unix_tmp(self):
        with mock.patch("chatbot.codex_cli_provider.sys.platform", "linux"):
            assert provider._is_system_binary_path("/tmp/codex") is False
            assert provider._is_system_binary_path("/var/tmp/codex") is False

    def test_accepts_unix_usr_local_bin(self):
        with mock.patch("chatbot.codex_cli_provider.sys.platform", "linux"):
            # realpath collapses to /usr/local/bin/codex on most hosts; on
            # Windows test runners os.path.realpath rewrites it differently —
            # so this is best-effort. We mock realpath to make it stable.
            with mock.patch("chatbot.codex_cli_provider.os.path.realpath", return_value="/usr/local/bin/codex"):
                assert provider._is_system_binary_path("/usr/local/bin/codex") is True

    def test_rejects_windows_temp(self):
        with mock.patch("chatbot.codex_cli_provider.sys.platform", "win32"), \
             mock.patch.dict(os.environ, {"ProgramFiles": "C:\\Program Files"}):
            with mock.patch("chatbot.codex_cli_provider.os.path.realpath", return_value="C:\\Users\\User\\AppData\\Local\\Temp\\codex.exe"):
                assert provider._is_system_binary_path("C:\\Users\\User\\AppData\\Local\\Temp\\codex.exe") is False

    def test_rejects_windows_unc_path(self):
        with mock.patch("chatbot.codex_cli_provider.sys.platform", "win32"):
            with mock.patch("chatbot.codex_cli_provider.os.path.realpath", return_value="\\\\attacker\\share\\codex.exe"):
                assert provider._is_system_binary_path("\\\\attacker\\share\\codex.exe") is False


# ── is_authenticated ────────────────────────────────────────────────────────


class TestIsAuthenticated:
    def setup_method(self):
        # Each test starts with a clean auth cache so previous tests don't leak.
        invalidate_auth_cache()

    def test_auth_json_fast_path_wins_over_cli(self, tmp_path, monkeypatch):
        # Fast path: auth.json present with tokens → authenticated, no
        # subprocess spawn at all.
        auth_file = tmp_path / "auth.json"
        auth_file.write_text(json.dumps({
            "tokens": {"access_token": "abc.def.ghi", "refresh_token": "rt"},
        }), encoding="utf-8")
        monkeypatch.setenv("CODEX_HOME", str(tmp_path))
        with mock.patch("chatbot.codex_cli_provider.subprocess.run") as m_run:
            ok, msg = is_authenticated()
            assert ok is True
            assert "auth.json" in msg
            assert m_run.call_count == 0

    def test_not_installed(self, tmp_path, monkeypatch):
        # Auth file absent + codex not on PATH → unauthenticated.
        monkeypatch.setenv("CODEX_HOME", str(tmp_path))
        with mock.patch("chatbot.codex_cli_provider.shutil.which", return_value=None):
            ok, msg = is_authenticated()
            assert ok is False
            assert "not installed" in msg

    def test_logged_in_exit_zero(self, tmp_path, monkeypatch):
        # No auth.json → fall through to CLI probe → CLI says logged in.
        monkeypatch.setenv("CODEX_HOME", str(tmp_path))
        fake_result = mock.Mock(returncode=0, stdout="Logged in as user@example.com\n", stderr="")
        with mock.patch("chatbot.codex_cli_provider.shutil.which", return_value="/x/codex"), \
             mock.patch("chatbot.codex_cli_provider._is_system_binary_path", return_value=True), \
             mock.patch("chatbot.codex_cli_provider.subprocess.run", return_value=fake_result):
            ok, msg = is_authenticated()
            assert ok is True
            assert "authenticated" in msg.lower() or "@" in msg or msg

    def test_not_logged_in(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CODEX_HOME", str(tmp_path))
        fake_result = mock.Mock(returncode=1, stdout="", stderr="not logged in. Please run codex login.")
        with mock.patch("chatbot.codex_cli_provider.shutil.which", return_value="/x/codex"), \
             mock.patch("chatbot.codex_cli_provider._is_system_binary_path", return_value=True), \
             mock.patch("chatbot.codex_cli_provider.subprocess.run", return_value=fake_result):
            ok, msg = is_authenticated()
            assert ok is False
            assert "not authenticated" in msg.lower() or "codex login" in msg

    def test_cache_hit(self, tmp_path, monkeypatch):
        # No auth.json → CLI probe runs and is cached; second call hits cache.
        monkeypatch.setenv("CODEX_HOME", str(tmp_path))
        fake_result = mock.Mock(returncode=0, stdout="Logged in", stderr="")
        with mock.patch("chatbot.codex_cli_provider.shutil.which", return_value="/x/codex"), \
             mock.patch("chatbot.codex_cli_provider._is_system_binary_path", return_value=True), \
             mock.patch("chatbot.codex_cli_provider.subprocess.run", return_value=fake_result) as m_run:
            is_authenticated()
            is_authenticated()
            # First probe may try up to 3 subcommands; second is cached.
            assert m_run.call_count <= 3

    def test_auth_json_with_only_refresh_token(self, tmp_path, monkeypatch):
        # access_token missing but refresh_token present → still treat as
        # authenticated; the refresh will run on the first API call.
        auth_file = tmp_path / "auth.json"
        auth_file.write_text(json.dumps({
            "tokens": {"refresh_token": "rt"},
        }), encoding="utf-8")
        monkeypatch.setenv("CODEX_HOME", str(tmp_path))
        ok, msg = is_authenticated()
        assert ok is True

    def test_auth_json_malformed_falls_through(self, tmp_path, monkeypatch):
        # Truncated/invalid auth.json → fast path declines, fall through
        # to CLI probe (which then says unauthenticated because we mock no CLI).
        auth_file = tmp_path / "auth.json"
        auth_file.write_text("not json {{{", encoding="utf-8")
        monkeypatch.setenv("CODEX_HOME", str(tmp_path))
        with mock.patch("chatbot.codex_cli_provider.shutil.which", return_value=None):
            ok, _ = is_authenticated()
            assert ok is False


# ── _classify_oauth_failure / _classify_error_text ──────────────────────────


class TestClassifiers:
    def test_oauth_failure_invalid_grant(self):
        hint = _classify_oauth_failure("invalid_grant: refresh token expired")
        assert hint is not None
        assert "log in" in hint.lower() or "authenticate" in hint.lower() or "codex login" in hint

    def test_oauth_failure_unauthorized(self):
        hint = _classify_oauth_failure("HTTP 401 Unauthorized")
        assert hint is not None

    def test_oauth_failure_none_for_clean_text(self):
        assert _classify_oauth_failure("ok success") is None

    def test_error_text_timeout(self):
        err = _classify_error_text("operation timed out after 30s")
        assert isinstance(err, CodexCliTimeoutError)

    def test_error_text_default_to_protocol(self):
        err = _classify_error_text("unparseable garbage")
        assert isinstance(err, CodexCliError)


# ── OAuth token management ──────────────────────────────────────────────────


def _make_jwt(exp_epoch: int) -> str:
    """Build a fake JWT with the given exp claim (header.payload.sig)."""
    import base64
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode()
    payload = base64.urlsafe_b64encode(
        json.dumps({"exp": exp_epoch}).encode()
    ).rstrip(b"=").decode()
    sig = "sig"
    return f"{header}.{payload}.{sig}"


class TestOAuthTokenManagement:
    def test_access_token_exp_parses_jwt(self):
        from chatbot.codex_cli_provider import _access_token_exp
        tok = _make_jwt(1_700_000_000)
        assert _access_token_exp(tok) == 1_700_000_000

    def test_access_token_exp_handles_malformed(self):
        from chatbot.codex_cli_provider import _access_token_exp
        assert _access_token_exp("") is None
        assert _access_token_exp("not.a.jwt.at.all") is None
        assert _access_token_exp("only_one_part") is None

    def test_is_access_token_expiring_true_when_close(self):
        from chatbot.codex_cli_provider import _is_access_token_expiring
        soon = int(time.time()) + 30  # 30s from now
        assert _is_access_token_expiring(_make_jwt(soon), skew_sec=60) is True

    def test_is_access_token_expiring_false_when_far(self):
        from chatbot.codex_cli_provider import _is_access_token_expiring
        far = int(time.time()) + 3600  # 1h from now
        assert _is_access_token_expiring(_make_jwt(far), skew_sec=60) is False

    def test_is_access_token_expiring_false_without_exp(self):
        # No exp claim → assume valid (the API call will surface a 401 if not).
        from chatbot.codex_cli_provider import _is_access_token_expiring
        assert _is_access_token_expiring("a.b.c", skew_sec=60) is False

    def test_get_access_token_reads_auth_file(self, tmp_path, monkeypatch):
        from chatbot.codex_cli_provider import _get_codex_access_token
        far = int(time.time()) + 3600
        tok = _make_jwt(far)
        (tmp_path / "auth.json").write_text(json.dumps({
            "tokens": {"access_token": tok, "refresh_token": "rt"},
        }), encoding="utf-8")
        monkeypatch.setenv("CODEX_HOME", str(tmp_path))
        assert _get_codex_access_token() == tok

    def test_get_access_token_refreshes_when_expiring(self, tmp_path, monkeypatch):
        from chatbot.codex_cli_provider import _get_codex_access_token
        soon = int(time.time()) + 5
        old_tok = _make_jwt(soon)
        new_tok = _make_jwt(int(time.time()) + 3600)
        (tmp_path / "auth.json").write_text(json.dumps({
            "tokens": {"access_token": old_tok, "refresh_token": "rt"},
        }), encoding="utf-8")
        monkeypatch.setenv("CODEX_HOME", str(tmp_path))
        with mock.patch(
            "chatbot.codex_cli_provider._refresh_codex_tokens",
            return_value={"access_token": new_tok, "refresh_token": "rt2", "id_token": "", "account_id": ""},
        ) as m_refresh:
            out = _get_codex_access_token()
            assert out == new_tok
            assert m_refresh.call_count == 1
        # auth.json should have been rewritten with the new token
        data = json.loads((tmp_path / "auth.json").read_text(encoding="utf-8"))
        assert data["tokens"]["access_token"] == new_tok
        assert data["tokens"]["refresh_token"] == "rt2"

    def test_get_access_token_no_auth_file_raises(self, tmp_path, monkeypatch):
        from chatbot.codex_cli_provider import _get_codex_access_token
        monkeypatch.setenv("CODEX_HOME", str(tmp_path))
        with pytest.raises(CodexCliNotAuthenticatedError):
            _get_codex_access_token()


# ── _build_responses_request / _extract_responses_text ─────────────────────


class TestResponsesRequestBuilder:
    def test_minimal_request(self):
        from chatbot.codex_cli_provider import _build_responses_request
        body = _build_responses_request(
            model="gpt-5-codex", prompt="hello",
            system_prompt=None, max_output_tokens=None, reasoning_effort=None,
        )
        assert body["model"] == "gpt-5-codex"
        assert body["store"] is False
        assert isinstance(body["input"], list) and len(body["input"]) == 1
        assert body["input"][0]["role"] == "user"
        content = body["input"][0]["content"]
        assert content == [{"type": "input_text", "text": "hello"}]
        assert "instructions" not in body
        assert "reasoning" not in body

    def test_system_prompt_routes_to_instructions(self):
        from chatbot.codex_cli_provider import _build_responses_request
        body = _build_responses_request(
            model="gpt-5", prompt="u", system_prompt="be terse",
            max_output_tokens=None, reasoning_effort=None,
        )
        assert body.get("instructions") == "be terse"

    def test_reasoning_effort_normalised(self):
        from chatbot.codex_cli_provider import _build_responses_request
        for effort in ("low", "medium", "high"):
            body = _build_responses_request(
                model="o4-mini", prompt="x", system_prompt=None,
                max_output_tokens=None, reasoning_effort=effort,
            )
            assert body["reasoning"] == {"effort": effort}
        # garbage effort → omitted
        body = _build_responses_request(
            model="o4-mini", prompt="x", system_prompt=None,
            max_output_tokens=None, reasoning_effort="bogus",
        )
        assert "reasoning" not in body

    def test_max_output_tokens_passthrough(self):
        from chatbot.codex_cli_provider import _build_responses_request
        body = _build_responses_request(
            model="gpt-5", prompt="x", system_prompt=None,
            max_output_tokens=256, reasoning_effort=None,
        )
        assert body["max_output_tokens"] == 256


class TestResponsesTextExtractor:
    def test_direct_output_text_field(self):
        from chatbot.codex_cli_provider import _extract_responses_text
        assert _extract_responses_text({"output_text": "hello"}) == "hello"

    def test_output_array_message_text(self):
        from chatbot.codex_cli_provider import _extract_responses_text
        payload = {
            "output": [
                {"type": "reasoning", "content": []},  # ignored
                {"type": "message", "content": [
                    {"type": "output_text", "text": "answer"},
                ]},
            ]
        }
        assert _extract_responses_text(payload) == "answer"

    def test_handles_multiple_text_parts(self):
        from chatbot.codex_cli_provider import _extract_responses_text
        payload = {
            "output": [
                {"type": "message", "content": [
                    {"type": "output_text", "text": "part1 "},
                    {"type": "text", "text": "part2"},
                ]},
            ]
        }
        assert _extract_responses_text(payload) == "part1 part2"

    def test_empty_payload(self):
        from chatbot.codex_cli_provider import _extract_responses_text
        assert _extract_responses_text({}) == ""
        assert _extract_responses_text({"output": []}) == ""


# ── call_codex_cli_plain via Responses API ─────────────────────────────────


class TestCallCodexCliPlain:
    def _setup_auth(self, tmp_path, monkeypatch):
        far = int(time.time()) + 3600
        tok = _make_jwt(far)
        (tmp_path / "auth.json").write_text(json.dumps({
            "tokens": {"access_token": tok, "refresh_token": "rt"},
        }), encoding="utf-8")
        monkeypatch.setenv("CODEX_HOME", str(tmp_path))
        return tok

    def test_happy_path_returns_text(self, tmp_path, monkeypatch):
        from chatbot.codex_cli_provider import call_codex_cli_plain
        self._setup_auth(tmp_path, monkeypatch)
        fake_payload = {"output": [
            {"type": "message", "content": [
                {"type": "output_text", "text": "hello world"},
            ]},
        ]}
        with mock.patch(
            "chatbot.codex_cli_provider._call_responses_api",
            return_value=fake_payload,
        ) as m_call:
            out = call_codex_cli_plain(model="gpt-5-codex", prompt="hi")
            assert out == "hello world"
            assert m_call.call_count == 1
            body = m_call.call_args.kwargs["body"]
            assert body["model"] == "gpt-5-codex"

    def test_missing_model_returns_empty(self, tmp_path, monkeypatch):
        from chatbot.codex_cli_provider import call_codex_cli_plain
        out = call_codex_cli_plain(model="", prompt="hi")
        assert out == ""

    def test_not_authenticated_raises(self, tmp_path, monkeypatch):
        # No auth.json on disk.
        from chatbot.codex_cli_provider import call_codex_cli_plain
        monkeypatch.setenv("CODEX_HOME", str(tmp_path))
        with pytest.raises(CodexCliNotAuthenticatedError):
            call_codex_cli_plain(model="gpt-5-codex", prompt="hi")

    def test_401_triggers_refresh_and_retry(self, tmp_path, monkeypatch):
        from chatbot.codex_cli_provider import call_codex_cli_plain
        self._setup_auth(tmp_path, monkeypatch)
        fake_payload = {"output": [
            {"type": "message", "content": [
                {"type": "output_text", "text": "after refresh"},
            ]},
        ]}
        # First call raises 401, second succeeds.
        side_effects = [
            CodexCliNotAuthenticatedError("401"),
            fake_payload,
        ]
        with mock.patch(
            "chatbot.codex_cli_provider._call_responses_api",
            side_effect=side_effects,
        ) as m_call, mock.patch(
            "chatbot.codex_cli_provider._refresh_codex_tokens",
            return_value={"access_token": _make_jwt(int(time.time()) + 3600), "refresh_token": "rt", "id_token": "", "account_id": ""},
        ):
            out = call_codex_cli_plain(model="gpt-5-codex", prompt="hi")
            assert out == "after refresh"
            assert m_call.call_count == 2

    def test_persistent_401_raises_not_authenticated(self, tmp_path, monkeypatch):
        from chatbot.codex_cli_provider import call_codex_cli_plain
        self._setup_auth(tmp_path, monkeypatch)
        with mock.patch(
            "chatbot.codex_cli_provider._call_responses_api",
            side_effect=CodexCliNotAuthenticatedError("401"),
        ), mock.patch(
            "chatbot.codex_cli_provider._refresh_codex_tokens",
            return_value={"access_token": _make_jwt(int(time.time()) + 3600), "refresh_token": "rt", "id_token": "", "account_id": ""},
        ):
            with pytest.raises(CodexCliNotAuthenticatedError):
                call_codex_cli_plain(model="gpt-5-codex", prompt="hi")

    def test_empty_response_returns_empty(self, tmp_path, monkeypatch):
        from chatbot.codex_cli_provider import call_codex_cli_plain
        self._setup_auth(tmp_path, monkeypatch)
        with mock.patch(
            "chatbot.codex_cli_provider._call_responses_api",
            return_value={"output": []},
        ):
            out = call_codex_cli_plain(model="gpt-5-codex", prompt="hi")
            assert out == ""

    def test_reasoning_effort_forwarded(self, tmp_path, monkeypatch):
        from chatbot.codex_cli_provider import call_codex_cli_plain
        self._setup_auth(tmp_path, monkeypatch)
        fake_payload = {"output_text": "ok"}
        with mock.patch(
            "chatbot.codex_cli_provider._call_responses_api",
            return_value=fake_payload,
        ) as m_call:
            call_codex_cli_plain(
                model="o4-mini", prompt="hi",
                provider_config={"reasoning_effort": "high"},
            )
            body = m_call.call_args.kwargs["body"]
            assert body.get("reasoning") == {"effort": "high"}


# ── call_codex_cli_structured ───────────────────────────────────────────────


class _StructuredOutput:
    """Minimal pydantic-like target for structured-output tests."""
    def __init__(self, name: str, value: int):
        self.name = name
        self.value = value

    @classmethod
    def model_validate(cls, data):
        return cls(name=str(data["name"]), value=int(data["value"]))

    @classmethod
    def model_json_schema(cls):
        return {
            "type": "object",
            "properties": {"name": {"type": "string"}, "value": {"type": "integer"}},
            "required": ["name", "value"],
        }


class TestCallCodexCliStructured:
    def _setup_auth(self, tmp_path, monkeypatch):
        far = int(time.time()) + 3600
        (tmp_path / "auth.json").write_text(json.dumps({
            "tokens": {"access_token": _make_jwt(far), "refresh_token": "rt"},
        }), encoding="utf-8")
        monkeypatch.setenv("CODEX_HOME", str(tmp_path))

    def test_happy_path_validates_json(self, tmp_path, monkeypatch):
        from chatbot.codex_cli_provider import call_codex_cli_structured
        self._setup_auth(tmp_path, monkeypatch)
        fake_payload = {"output_text": '{"name": "abc", "value": 42}'}
        with mock.patch(
            "chatbot.codex_cli_provider._call_responses_api",
            return_value=fake_payload,
        ):
            out = call_codex_cli_structured(
                model="gpt-5-codex", prompt="x",
                output_type=_StructuredOutput,
            )
            assert out is not None
            assert out.name == "abc"
            assert out.value == 42

    def test_invalid_json_retries_then_gives_up(self, tmp_path, monkeypatch):
        from chatbot.codex_cli_provider import call_codex_cli_structured
        self._setup_auth(tmp_path, monkeypatch)
        with mock.patch(
            "chatbot.codex_cli_provider._call_responses_api",
            return_value={"output_text": "not json at all"},
        ) as m_call:
            out = call_codex_cli_structured(
                model="gpt-5-codex", prompt="x",
                output_type=_StructuredOutput,
                output_retries=1,
            )
            assert out is None
            assert m_call.call_count == 2  # 1 initial + 1 retry


# ── _extract_json_from_text ────────────────────────────────────────────────


class TestExtractJson:
    def test_pure_json_returned_as_is(self):
        assert _extract_json_from_text('{"a": 1}') == '{"a": 1}'

    def test_fenced_block_stripped(self):
        out = _extract_json_from_text('```json\n{"x": 2}\n```')
        assert out and json.loads(out) == {"x": 2}

    def test_unfenced_block_with_prose(self):
        text = 'Sure, here you go:\n\n{"k": "v"}\n\nLet me know if you need more.'
        out = _extract_json_from_text(text)
        assert out and json.loads(out) == {"k": "v"}

    def test_array_extraction(self):
        out = _extract_json_from_text('Result: [1, 2, 3]')
        assert out and json.loads(out) == [1, 2, 3]

    def test_empty_returns_none(self):
        assert _extract_json_from_text("") is None
        assert _extract_json_from_text("   ") is None


# ── CodexInstaller ─────────────────────────────────────────────────────────


class TestCodexInstaller:
    def setup_method(self):
        # Reset the class-level jobs dict so prior tests don't leak.
        with CodexInstaller._jobs_lock:
            CodexInstaller._jobs.clear()
        # Release the install semaphore in case a prior test held it
        try:
            while True:
                provider._INSTALL_SEM.release()
        except ValueError:
            pass

    def test_rejects_when_no_npm_or_brew(self):
        with mock.patch("chatbot.codex_cli_provider.shutil.which", return_value=None):
            with pytest.raises(CodexCliError, match="No supported install method"):
                CodexInstaller.start()

    def test_rejects_unsupported_method_explicitly(self):
        # Even if we ask for something weird, only npm/brew are allowed.
        with mock.patch("chatbot.codex_cli_provider.shutil.which", return_value=None):
            with pytest.raises(CodexCliError, match="No supported install method"):
                CodexInstaller.start(method="apt")

    def test_status_unknown_job_returns_none(self):
        assert CodexInstaller.status("nope-not-a-job") is None

    def test_only_one_install_at_a_time(self):
        # Acquire the semaphore so the second start() will fail.
        provider._INSTALL_SEM.acquire(blocking=False)
        try:
            with mock.patch("chatbot.codex_cli_provider.shutil.which", return_value="/usr/local/bin/npm"), \
                 mock.patch("chatbot.codex_cli_provider._is_system_binary_path", return_value=True):
                with pytest.raises(CodexCliError, match="already running"):
                    CodexInstaller.start(method="npm")
        finally:
            provider._INSTALL_SEM.release()

    def test_install_success_path(self):
        # Simulate a Popen that exits 0 with some log output. We patch
        # both ``shutil.which`` (so npm is "available") and ``subprocess.Popen``.
        fake_stdout = iter(["installed @openai/codex@0.10.0\n", ""])
        fake_proc = mock.Mock()
        fake_proc.stdout = mock.Mock()
        fake_proc.stdout.readline = lambda: next(fake_stdout)
        fake_proc.wait = mock.Mock(return_value=0)
        fake_proc.returncode = 0

        with mock.patch("chatbot.codex_cli_provider.shutil.which", return_value="/usr/local/bin/npm"), \
             mock.patch("chatbot.codex_cli_provider._is_system_binary_path", return_value=True), \
             mock.patch("chatbot.codex_cli_provider.subprocess.Popen", return_value=fake_proc):
            job = CodexInstaller.start(method="npm")
            # Wait for the background thread to finish.
            for _ in range(100):
                snap = CodexInstaller.status(job.job_id)
                if snap and snap["state"] in ("success", "failed"):
                    break
                time.sleep(0.02)
            snap = CodexInstaller.status(job.job_id)
            assert snap is not None
            assert snap["state"] == "success"
            assert snap["exit_code"] == 0


# ── CodexDeviceCodeLogin ───────────────────────────────────────────────────


class TestCodexDeviceCodeLogin:
    def setup_method(self):
        with CodexDeviceCodeLogin._jobs_lock:
            CodexDeviceCodeLogin._jobs.clear()

    def test_not_installed_raises(self):
        with mock.patch("chatbot.codex_cli_provider.shutil.which", return_value=None):
            with pytest.raises(CodexCliNotInstalledError):
                CodexDeviceCodeLogin.start(cli_path="codex")

    def test_cancel_unknown_returns_false(self):
        assert CodexDeviceCodeLogin.cancel("nope") is False

    def test_status_unknown_returns_none(self):
        assert CodexDeviceCodeLogin.status("nope") is None

    def test_start_parses_pairing_url_and_code(self):
        # Simulate a subprocess that prints a pairing URL and code on stdout
        # then exits 0 to indicate successful login.
        stdout_lines = iter([
            "Opening browser is disabled.\n",
            "Visit https://chatgpt.com/connect to authorize.\n",
            "Enter code: A1B2-C3D4\n",
            "",
        ])
        stderr_lines = iter([""])
        fake_proc = mock.Mock()
        fake_proc.stdout = mock.Mock()
        fake_proc.stdout.readline = lambda: next(stdout_lines)
        fake_proc.stderr = mock.Mock()
        fake_proc.stderr.readline = lambda: next(stderr_lines)
        fake_proc.poll = mock.Mock(return_value=None)
        fake_proc.wait = mock.Mock(return_value=0)

        with mock.patch("chatbot.codex_cli_provider.shutil.which", return_value="/usr/local/bin/codex"), \
             mock.patch("chatbot.codex_cli_provider._is_system_binary_path", return_value=True), \
             mock.patch("chatbot.codex_cli_provider.subprocess.Popen", return_value=fake_proc):
            job = CodexDeviceCodeLogin.start(cli_path="codex")
            # Give the parser a moment to scan stdout.
            for _ in range(50):
                snap = CodexDeviceCodeLogin.status(job.job_id)
                if snap and snap["pairing_url"] and snap["pairing_code"]:
                    break
                time.sleep(0.02)
            snap = CodexDeviceCodeLogin.status(job.job_id)
            assert snap is not None
            assert snap["pairing_url"] is not None and "chatgpt.com" in snap["pairing_url"]
            assert snap["pairing_code"] == "A1B2-C3D4"

    def test_parses_codex_0_132_device_auth_output_with_ansi(self):
        # Regression: codex 0.132 ``codex login --device-auth`` wraps
        # both the URL and the pairing code in ANSI SGR escapes
        # (``\x1b[94m…\x1b[0m``) and uses a 4-5 char pairing code
        # format like ``BLM4-B2C6J`` (vs the older 4-4 ``A1B2-C3D4``).
        # Without ANSI strip the URL regex eats the trailing reset
        # sequence and produces a corrupted link; without the wider
        # code regex the parser misses the code entirely. This test
        # pins both fixes so the panel never gets stuck on
        # "Waiting for sign-in…" with no URL/code surfaced.
        stdout_lines = iter([
            "Welcome to Codex [v\x1b[90m0.132.0\x1b[0m]\n",
            "Follow these steps to sign in with ChatGPT using device code authorization:\n",
            "\n",
            "1. Open this link in your browser and sign in to your account\n",
            "   \x1b[94mhttps://auth.openai.com/codex/device\x1b[0m\n",
            "\n",
            "2. Enter this one-time code (expires in 15 minutes)\n",
            "   \x1b[94mBLM4-B2C6J\x1b[0m\n",
            "\n",
            "",  # EOF
        ])
        stderr_lines = iter([""])
        fake_proc = mock.Mock()
        fake_proc.stdout = mock.Mock()
        fake_proc.stdout.readline = lambda: next(stdout_lines)
        fake_proc.stderr = mock.Mock()
        fake_proc.stderr.readline = lambda: next(stderr_lines)
        fake_proc.poll = mock.Mock(return_value=None)
        fake_proc.wait = mock.Mock(return_value=0)

        with mock.patch("chatbot.codex_cli_provider.shutil.which", return_value="/usr/local/bin/codex"), \
             mock.patch("chatbot.codex_cli_provider._is_system_binary_path", return_value=True), \
             mock.patch("chatbot.codex_cli_provider.subprocess.Popen", return_value=fake_proc):
            job = CodexDeviceCodeLogin.start(cli_path="codex")
            for _ in range(50):
                snap = CodexDeviceCodeLogin.status(job.job_id)
                if snap and snap["pairing_url"] and snap["pairing_code"]:
                    break
                time.sleep(0.02)
            snap = CodexDeviceCodeLogin.status(job.job_id)
            assert snap is not None
            # The URL must NOT contain residual ANSI escapes.
            assert snap["pairing_url"] == "https://auth.openai.com/codex/device"
            assert snap["pairing_code"] == "BLM4-B2C6J"

    def test_rejects_non_openai_pairing_url(self):
        # An attacker-controlled codex build that prints a non-OpenAI URL
        # must NOT be surfaced to the operator (regex + urlparse gates).
        stdout_lines = iter([
            "Visit https://evil.com/oauth?go=1 to authorize.\n",
            "Enter code: A1B2-C3D4\n",
            "",
        ])
        stderr_lines = iter([""])
        fake_proc = mock.Mock()
        fake_proc.stdout = mock.Mock()
        fake_proc.stdout.readline = lambda: next(stdout_lines)
        fake_proc.stderr = mock.Mock()
        fake_proc.stderr.readline = lambda: next(stderr_lines)
        fake_proc.poll = mock.Mock(return_value=None)
        fake_proc.wait = mock.Mock(return_value=0)

        with mock.patch("chatbot.codex_cli_provider.shutil.which", return_value="/usr/local/bin/codex"), \
             mock.patch("chatbot.codex_cli_provider._is_system_binary_path", return_value=True), \
             mock.patch("chatbot.codex_cli_provider.subprocess.Popen", return_value=fake_proc):
            job = CodexDeviceCodeLogin.start(cli_path="codex")
            # Even with the code parsed, pairing_url should remain None
            # because the regex rejects non-OpenAI hosts.
            for _ in range(30):
                snap = CodexDeviceCodeLogin.status(job.job_id)
                if snap and snap["pairing_code"]:
                    break
                time.sleep(0.02)
            snap = CodexDeviceCodeLogin.status(job.job_id)
            assert snap is not None
            assert snap["pairing_url"] is None or snap["pairing_url"] == ""


# ── detect_install_method ──────────────────────────────────────────────────


class TestDetectInstallMethod:
    def test_brew_preferred_on_macos(self):
        # darwin + brew available → brew
        with mock.patch("chatbot.codex_cli_provider.sys.platform", "darwin"), \
             mock.patch("chatbot.codex_cli_provider.shutil.which", side_effect=lambda x: "/opt/homebrew/bin/brew" if x == "brew" else "/usr/local/bin/npm"):
            method, path = detect_install_method()
            assert method == "brew"

    def test_npm_chosen_on_linux(self):
        with mock.patch("chatbot.codex_cli_provider.sys.platform", "linux"), \
             mock.patch("chatbot.codex_cli_provider.shutil.which", side_effect=lambda x: "/usr/local/bin/npm" if x == "npm" else None):
            method, path = detect_install_method()
            assert method == "npm"

    def test_unknown_when_neither_available(self):
        with mock.patch("chatbot.codex_cli_provider.shutil.which", return_value=None):
            method, path = detect_install_method()
            assert method == "unknown"
            assert path is None
