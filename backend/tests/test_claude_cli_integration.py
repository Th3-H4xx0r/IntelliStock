"""End-to-end integration tests for the claude-cli provider.

These spawn a real ``claude`` subprocess and require:

  * the ``claude`` binary on PATH (or set ``CLAUDE_CLI_BIN`` to a path)
  * an active Claude Code login on this host (i.e. ``claude`` interactive
    setup has been completed, ``~/.claude/`` contains a session token)
  * the ``INTELLISTOCK_TEST_CLAUDE_CLI`` env var set to a truthy value
    (any of: 1, true, yes), otherwise the entire module is skipped

The default test model is the cheapest fast model — override with
``CLAUDE_CLI_TEST_MODEL`` if you want to exercise sonnet/opus.

These tests cost real subscription tokens. They are skipped by default
so CI doesn't burn credits.
"""
from __future__ import annotations

import os
import sys

import pytest

_BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)


def _env_enabled(name: str) -> bool:
    return str(os.environ.get(name, "")).strip().lower() in ("1", "true", "yes", "y", "on")


pytestmark = pytest.mark.skipif(
    not _env_enabled("INTELLISTOCK_TEST_CLAUDE_CLI"),
    reason="Set INTELLISTOCK_TEST_CLAUDE_CLI=1 to run claude-cli integration tests",
)


CLI_PATH = os.environ.get("CLAUDE_CLI_BIN", "claude")
TEST_MODEL = os.environ.get("CLAUDE_CLI_TEST_MODEL", "claude-haiku-4-5")


# Import after the skip-mark so unrelated import errors don't take down
# the whole file when integration tests are disabled.
from chatbot.claude_cli_provider import (  # noqa: E402
    call_claude_cli_chat,
    call_claude_cli_structured,
    get_session_manager,
    shutdown_session_manager,
    test_claude_cli,
)


@pytest.fixture(autouse=True)
def _close_sessions_after_each_test():
    yield
    shutdown_session_manager()


def test_version_probe_succeeds():
    out = test_claude_cli(cli_path=CLI_PATH, model=TEST_MODEL)
    assert out["ok"] is True, f"test_claude_cli failed: {out}"
    assert out["version"], "version string is empty"
    assert out["logged_in"] is True


def test_oneshot_chat_returns_text():
    out = call_claude_cli_chat(
        conversation_id="integration-oneshot",
        messages=[{"role": "user", "content": "Reply with only the word 'OK'."}],
        system_prompt="You are a connectivity test. Reply with exactly one word.",
        model=TEST_MODEL,
        cli_path=CLI_PATH,
        extra_args=[],
        timeout_sec=60,
    )
    assert isinstance(out, dict)
    text = (out.get("content") or "").strip().lower()
    assert text, f"no content returned: {out}"
    # The model may say "OK" or "OK." or "Ok"; just check the substring.
    assert "ok" in text, f"expected 'ok' in response, got {text!r}"


def test_multi_turn_preserves_context():
    out1 = call_claude_cli_chat(
        conversation_id="integration-multi",
        messages=[{"role": "user", "content": "My favourite colour is blue. Say acknowledged."}],
        system_prompt="You answer concisely.",
        model=TEST_MODEL,
        cli_path=CLI_PATH,
        extra_args=[],
        timeout_sec=60,
    )
    assert "acknowledged" in (out1.get("content") or "").lower(), out1

    out2 = call_claude_cli_chat(
        conversation_id="integration-multi",
        messages=[
            {"role": "user", "content": "My favourite colour is blue. Say acknowledged."},
            {"role": "assistant", "content": out1["content"]},
            {"role": "user", "content": "What is my favourite colour? Reply with the colour name only."},
        ],
        system_prompt="You answer concisely.",
        model=TEST_MODEL,
        cli_path=CLI_PATH,
        extra_args=[],
        timeout_sec=60,
    )
    text = (out2.get("content") or "").lower()
    assert "blue" in text, f"context not preserved across turns; got {text!r}"


def test_structured_output_validates_against_pydantic():
    from pydantic import BaseModel, Field

    class Numbers(BaseModel):
        first: int = Field(..., description="The first number")
        second: int = Field(..., description="The second number")
        sum: int = Field(..., description="first + second")

    out = call_claude_cli_structured(
        model=TEST_MODEL,
        system_prompt=(
            "You are a calculator. Reply ONLY with JSON matching the given schema."
        ),
        user_prompt="Set first=7, second=5, and compute the sum.",
        output_schema=Numbers,
        cli_path=CLI_PATH,
        timeout_sec=90,
    )
    assert isinstance(out, Numbers)
    assert out.first == 7
    assert out.second == 5
    assert out.sum == 12


def test_warm_session_avoids_second_spawn():
    """Performance smoke test: second turn should be noticeably faster
    than the first because the persistent subprocess is reused. We allow
    a generous margin (3 s) since real API latency dwarfs spawn time on
    slow models. The intent is to catch a regression where every turn
    re-spawns."""
    import time

    mgr = get_session_manager()
    cid = "integration-warm"

    t0 = time.monotonic()
    call_claude_cli_chat(
        conversation_id=cid,
        messages=[{"role": "user", "content": "Say one."}],
        system_prompt="Answer with exactly one word.",
        model=TEST_MODEL,
        cli_path=CLI_PATH,
        extra_args=[],
        timeout_sec=60,
    )
    cold = time.monotonic() - t0

    t1 = time.monotonic()
    call_claude_cli_chat(
        conversation_id=cid,
        messages=[
            {"role": "user", "content": "Say one."},
            {"role": "assistant", "content": "one"},
            {"role": "user", "content": "Say two."},
        ],
        system_prompt="Answer with exactly one word.",
        model=TEST_MODEL,
        cli_path=CLI_PATH,
        extra_args=[],
        timeout_sec=60,
    )
    warm = time.monotonic() - t1

    # On a real machine cold is ~6-15 s and warm is ~2-6 s. We assert
    # the warm turn is at least somewhat faster — a regression that
    # respawns every turn would have warm ≈ cold.
    assert warm < cold + 0.5, (
        f"warm turn ({warm:.2f}s) not faster than cold ({cold:.2f}s); "
        "session may not be reused"
    )
    assert cid in mgr._sessions
