"""Unit tests for the Claude Code CLI subscription re-auth flow.

Covers the URL/code parsing helpers, the read-only ``claude auth status``
probe, ``claude auth logout``, and the ``ClaudeCliLogin`` job lifecycle
edge cases. All subprocess interactions are mocked — no real ``claude``
binary, PTY, or network is required. The live PTY drive (start →
submit_code against a real CLI) is intentionally out of scope here; it's
exercised manually via the web/mobile UI against a deployment.
"""
from __future__ import annotations

import os
import subprocess
import sys
from types import SimpleNamespace
from unittest import mock

import pytest

_BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from chatbot import claude_cli_provider as provider  # noqa: E402
from chatbot.claude_cli_provider import ClaudeCliLogin  # noqa: E402


# ── URL allowlist ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("url", [
    "https://claude.ai/oauth/authorize?code=true&client_id=x",
    "https://www.claude.ai/oauth/authorize?x=1",
    "https://console.anthropic.com/oauth/authorize?x=1",
    "http://claude.ai/oauth",
])
def test_safe_oauth_url_accepts_anthropic_hosts(url):
    assert provider._is_safe_claude_oauth_url(url) is True


@pytest.mark.parametrize("url", [
    "",
    "https://evil.com/oauth/authorize",
    "https://claude.ai.evil.com/oauth",
    "https://user@claude.ai/oauth",  # userinfo
    "ftp://claude.ai/oauth",
    "javascript:alert(1)",
])
def test_safe_oauth_url_rejects_everything_else(url):
    assert provider._is_safe_claude_oauth_url(url) is False


# ── Authorization-code format ───────────────────────────────────────────────


@pytest.mark.parametrize("code", [
    "ac_abc-123",
    "abcdef",
    "tok.en_value~123/456=#state-99",
])
def test_auth_code_regex_accepts_plausible_codes(code):
    assert provider._CC_AUTH_CODE_RE.match(code)


@pytest.mark.parametrize("code", [
    "short",            # < 6 chars
    "has space",        # whitespace
    "bad;rm -rf /",     # shell metachars
    "line\nbreak1",     # newline
    "x" * 2000,         # too long
])
def test_auth_code_regex_rejects_bad_codes(code):
    assert not provider._CC_AUTH_CODE_RE.match(code)


# ── ANSI stripping (PTY output is colorized) ────────────────────────────────


def test_ansi_stripper_recovers_clean_url():
    raw = "\x1b[94mVisit: https://claude.ai/oauth/authorize?x=1\x1b[0m"
    assert provider._ANSI_ESCAPE_RE_CC.sub("", raw) == (
        "Visit: https://claude.ai/oauth/authorize?x=1"
    )


# ── claude auth status (read-only probe) ────────────────────────────────────


def _fake_run_factory(status_stdout, status_rc=0):
    def _fake_run(argv, **kwargs):
        if argv[-1] == "--version":
            return SimpleNamespace(stdout="1.2.3 (Claude Code)\n", stderr="", returncode=0)
        if argv[-2:] == ["auth", "status"]:
            return SimpleNamespace(stdout=status_stdout, stderr="", returncode=status_rc)
        return SimpleNamespace(stdout="", stderr="", returncode=0)
    return _fake_run


def test_auth_status_reports_authenticated(monkeypatch):
    monkeypatch.setattr(provider, "_resolve_cli_path", lambda p: "/usr/bin/claude")
    monkeypatch.setattr(
        provider.subprocess, "run",
        _fake_run_factory("Logged in as user@example.com\nLogin method: Claude subscription\n"),
    )
    out = provider.claude_auth_status("claude")
    assert out["installed"] is True
    assert out["authenticated"] is True
    assert out["account"] == "user@example.com"
    assert out["version"] == "1.2.3 (Claude Code)"


def test_auth_status_reports_not_logged_in(monkeypatch):
    monkeypatch.setattr(provider, "_resolve_cli_path", lambda p: "/usr/bin/claude")
    monkeypatch.setattr(
        provider.subprocess, "run",
        _fake_run_factory("Not logged in", status_rc=1),
    )
    out = provider.claude_auth_status("claude")
    assert out["installed"] is True
    assert out["authenticated"] is False
    assert out["account"] is None


def test_auth_status_handles_missing_binary(monkeypatch):
    def _raise(p):
        raise provider.ClaudeCliNotInstalledError("claude not found")
    monkeypatch.setattr(provider, "_resolve_cli_path", _raise)
    out = provider.claude_auth_status("claude")
    assert out["installed"] is False
    assert out["authenticated"] is False


# ── claude auth logout ──────────────────────────────────────────────────────


def test_logout_success_invalidates_cache(monkeypatch):
    monkeypatch.setattr(provider, "_resolve_cli_path", lambda p: "/usr/bin/claude")
    monkeypatch.setattr(
        provider.subprocess, "run",
        lambda argv, **kw: SimpleNamespace(stdout="ok", stderr="", returncode=0),
    )
    called = {"invalidated": False}
    monkeypatch.setattr(
        provider, "invalidate_runtime_home_cache",
        lambda: called.__setitem__("invalidated", True),
    )
    ok, msg = provider.claude_logout("claude")
    assert ok is True
    assert called["invalidated"] is True


def test_logout_failure_surfaces_stderr(monkeypatch):
    monkeypatch.setattr(provider, "_resolve_cli_path", lambda p: "/usr/bin/claude")
    monkeypatch.setattr(
        provider.subprocess, "run",
        lambda argv, **kw: SimpleNamespace(stdout="", stderr="boom", returncode=1),
    )
    monkeypatch.setattr(provider, "invalidate_runtime_home_cache", lambda: None)
    ok, msg = provider.claude_logout("claude")
    assert ok is False
    assert "boom" in msg


# ── classify login failure ──────────────────────────────────────────────────


def test_classify_login_failure():
    assert "rejected" in (provider._classify_login_failure("Error: invalid code") or "")
    assert "expired" in (provider._classify_login_failure("the code has expired") or "").lower()
    assert provider._classify_login_failure("") is None
    assert provider._classify_login_failure("totally unrelated noise") is None


# ── ClaudeCliLogin job lifecycle edge cases ─────────────────────────────────


def test_submit_code_unknown_job_returns_none():
    assert ClaudeCliLogin.submit_code("does-not-exist", "abcdef123") is None


def test_status_unknown_job_returns_none():
    assert ClaudeCliLogin.status("nope") is None


def test_cancel_unknown_job_returns_false():
    assert ClaudeCliLogin.cancel("nope") is False


def test_submit_code_rejects_bad_format(monkeypatch):
    # Seed a fake job whose proc looks alive so we reach the format gate.
    job = provider._CcLoginJob(job_id="j1", state="parsed")
    job.proc = SimpleNamespace(poll=lambda: None)
    job.master_fd = 999
    with ClaudeCliLogin._jobs_lock:
        ClaudeCliLogin._jobs["j1"] = job
    try:
        snap = ClaudeCliLogin.submit_code("j1", "no good")
        assert snap is not None
        assert "unexpected format" in (snap["error"] or "")
        # State must not advance to awaiting_code on a malformed code.
        assert snap["state"] == "parsed"
    finally:
        with ClaudeCliLogin._jobs_lock:
            ClaudeCliLogin._jobs.pop("j1", None)


def test_submit_code_dead_proc_fails_cleanly():
    job = provider._CcLoginJob(job_id="j2", state="parsed")
    job.proc = SimpleNamespace(poll=lambda: 1)  # already exited
    job.master_fd = 999
    with ClaudeCliLogin._jobs_lock:
        ClaudeCliLogin._jobs["j2"] = job
    try:
        snap = ClaudeCliLogin.submit_code("j2", "abcdef123")
        assert snap is not None
        assert snap["state"] == "failed"
    finally:
        with ClaudeCliLogin._jobs_lock:
            ClaudeCliLogin._jobs.pop("j2", None)


def test_reader_parses_url_from_pty_output(monkeypatch):
    # Drive _reader with a fake fd that yields colorized output then EOF.
    chunks = [
        b"\x1b[2mOpening browser...\x1b[0m\r\n",
        b"Paste the following URL: \x1b[94mhttps://claude.ai/oauth/authorize?code=true&state=abc\x1b[0m\r\n",
        b"",  # EOF
    ]
    seq = iter(chunks)

    def fake_read(fd, n):
        try:
            return next(seq)
        except StopIteration:
            return b""

    monkeypatch.setattr(provider.os, "read", fake_read)
    job = provider._CcLoginJob(job_id="j3", state="pending", master_fd=42)
    ClaudeCliLogin._reader(job)
    assert job.login_url == "https://claude.ai/oauth/authorize?code=true&state=abc"
    assert job.state == "parsed"
