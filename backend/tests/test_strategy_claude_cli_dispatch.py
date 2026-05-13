"""End-to-end claude-cli dispatch tests without the real binary.

Covers the gap the bug-sweep agents flagged: no test exercised the
strategy → llm_utils → claude-cli path before this. Production hit a
silent regression (backtest #336915 burning ~25s/cycle on Gemini 404s)
because graph_nexus_analysis._normalize_llm_provider had a hardcoded
whitelist that coerced 'claude-cli' to 'gemini'. These tests pin the
expected behavior so a future whitelist edit can't reintroduce it.

We mock the leaf function that spawns the ``claude`` subprocess so the
tests run anywhere without needing the binary, a login, or an internet
connection.
"""
from __future__ import annotations

import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest
from pydantic import BaseModel

_BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)


class _DummyOutput(BaseModel):
    text: str
    score: float


@pytest.fixture
def mock_claude_cli_structured():
    """Patch ``call_claude_cli_structured`` in claude_cli_provider so
    the test never spawns a subprocess. Returns the mock so individual
    tests can assert the call arguments."""
    with patch("chatbot.claude_cli_provider.call_claude_cli_structured") as m:
        m.return_value = _DummyOutput(text="ok", score=0.5)
        yield m


# ── 1) llm_utils dispatch ────────────────────────────────────────────────


def test_call_structured_llm_routes_claude_cli_branch(mock_claude_cli_structured):
    """call_structured_llm_by_provider must hand claude-cli off to the
    bespoke branch — NOT through PydanticAI / Gemini / OpenAI."""
    from llm_utils import call_structured_llm_by_provider

    out = call_structured_llm_by_provider(
        "claude-cli",
        "ignored-api-key",
        "claude-sonnet-4-6",
        prompt="classify this article",
        output_type=_DummyOutput,
        provider_config={"cli_path": "claude", "extra_args": ""},
    )

    assert out is not None
    assert out.text == "ok"
    # Subprocess-spawning function was called exactly once.
    assert mock_claude_cli_structured.call_count == 1
    kwargs = mock_claude_cli_structured.call_args.kwargs
    assert kwargs["model"] == "claude-sonnet-4-6"
    assert kwargs.get("cli_path") in ("claude", None)


def test_claude_cli_branch_records_terminal_on_not_logged_in():
    """When the bespoke branch surfaces ClaudeCliNotLoggedInError, the
    metadata must flag is_terminal=True so callers can skip retry/split
    loops."""
    from chatbot.claude_cli_provider import ClaudeCliNotLoggedInError
    from llm_utils import (
        call_structured_llm_by_provider,
        get_last_structured_llm_call_metadata,
    )

    with patch("chatbot.claude_cli_provider.call_claude_cli_structured") as m:
        m.side_effect = ClaudeCliNotLoggedInError("not logged in")
        out = call_structured_llm_by_provider(
            "claude-cli",
            "ignored",
            "claude-sonnet-4-6",
            prompt="…",
            output_type=_DummyOutput,
            provider_config={"cli_path": "claude"},
        )

    assert out is None
    meta = get_last_structured_llm_call_metadata()
    assert meta.get("is_terminal") is True
    assert meta.get("ok") is False


# ── 2) graph_nexus_analysis normalizer ───────────────────────────────────


@pytest.mark.parametrize(
    "provider,expected",
    [
        ("claude-cli", "claude-cli"),
        ("anthropic", "anthropic"),
        ("CLAUDE-CLI", "claude-cli"),
        ("gemini", "gemini"),
        ("azure", "azure"),
        ("openai", "openai"),
        ("nvidia", "nvidia"),
        ("deepseek", "deepseek"),
        # Unknown providers still coerce to gemini — that's the
        # intentional safe-default.
        ("unknown-provider", "gemini"),
        ("", "gemini"),
    ],
)
def test_graph_nexus_normalize_provider_preserves_claude_cli(provider, expected):
    """REGRESSION: silent coercion of 'claude-cli' → 'gemini' caused the
    backtest #336915 thrash. Pin the normalizer's contract."""
    from strategies.graph_nexus_analysis import _normalize_llm_provider

    assert _normalize_llm_provider(provider) == expected


def test_graph_nexus_default_api_key_returns_sentinel_for_claude_cli():
    """``if not api_key: return`` short-circuits in role callers would
    skip the pipeline if we returned empty for claude-cli. The sentinel
    string keeps the truthiness check passing."""
    from strategies.graph_nexus_analysis import _default_api_key_for_provider

    out = _default_api_key_for_provider("claude-cli")
    assert out  # non-empty
    assert out == "claude-cli-no-api-key"


def test_graph_nexus_default_model_for_claude_cli():
    from strategies.graph_nexus_analysis import _default_model_for_provider

    assert _default_model_for_provider("claude-cli") == "claude-sonnet-4-6"
    assert _default_model_for_provider("anthropic") == "claude-sonnet-4-6"


# ── 3) Same checks for the four other strategies/engines that had the bug


@pytest.mark.parametrize(
    "module_path",
    [
        "strategies.earnings",
        "strategies.ml_news",
        "engines.ai_backtest_engine",
    ],
)
def test_other_modules_normalize_claude_cli(module_path):
    """earnings, ml_news, and ai_backtest_engine each had their own
    copy of the silent-coerce bug. Make sure each module's normalizer
    preserves claude-cli."""
    import importlib

    mod = importlib.import_module(module_path)
    assert hasattr(mod, "_normalize_llm_provider"), f"{module_path} missing _normalize_llm_provider"
    assert mod._normalize_llm_provider("claude-cli") == "claude-cli"
    assert mod._normalize_llm_provider("anthropic") == "anthropic"
    # API-key sentinel for claude-cli — keeps the ``if not api_key`` short-circuits from firing.
    assert mod._default_api_key_for_provider("claude-cli") == "claude-cli-no-api-key"


# ── 4) Provider+model save-time compatibility check ──────────────────────


@pytest.mark.parametrize(
    "provider,model",
    [
        ("gemini", "claude-sonnet-4-6"),
        ("gemini", "gpt-4o"),
        ("gemini", "o3-mini"),
        ("openai", "claude-3-5-sonnet"),
        ("openai", "gemini-1.5-pro"),
        ("nvidia", "claude-opus-4"),
        ("anthropic", "gpt-4o"),
        ("anthropic", "gemini-pro"),
        ("claude-cli", "o1-preview"),
        # case-insensitive
        ("gemini", "Claude-Sonnet-4-6"),
        ("CLAUDE-CLI", "GPT-4o"),
    ],
)
def test_validate_provider_model_compat_rejects_mismatches(provider, model):
    from interactive_utils import _validate_provider_model_compat

    with pytest.raises(ValueError, match="Incompatible provider"):
        _validate_provider_model_compat(provider, model)


@pytest.mark.parametrize(
    "provider,model",
    [
        # Correct combos
        ("gemini", "gemini-2.0-flash"),
        ("openai", "gpt-4o"),
        ("openai", "o4-mini"),
        ("anthropic", "claude-sonnet-4-6"),
        ("claude-cli", "claude-opus-4-1"),
        ("nvidia", "meta/llama-3.3-70b-instruct"),
        # Azure exempt — deployment names are operator-defined
        ("azure", "claude-sonnet-4-6"),
        ("azure", "anything-goes"),
        # Unknown providers pass through (forward-compat)
        ("future-provider", "anything"),
        # Empty values pass — separate emptiness handling
        ("", "claude-sonnet-4-6"),
        ("gemini", ""),
    ],
)
def test_validate_provider_model_compat_passes_valid(provider, model):
    from interactive_utils import _validate_provider_model_compat

    # Should not raise
    _validate_provider_model_compat(provider, model)


# ── 5) Test-endpoint reasoning_effort plumbing ───────────────────────────


def test_build_llm_test_provider_config_includes_reasoning_effort_for_claude_cli():
    """The ``/test-llm-config`` endpoint's provider-config builder was
    silently dropping reasoning_effort for claude-cli before this fix.
    Make sure 'high' flows through."""
    pytest.importorskip("fastapi")  # api.main needs FastAPI; skip locally if not installed
    from api.main import _build_llm_test_provider_config

    body = types.SimpleNamespace(
        provider="claude-cli",
        api_key="",
        openai_base_url=None,
        nvidia_base_url=None,
        azure_openai_endpoint=None,
        azure_openai_api_version=None,
        reasoning_effort="high",
        cli_path="claude",
        extra_args="",
    )
    cfg = _build_llm_test_provider_config(body)
    assert cfg.get("reasoning_effort") == "high"


# ── 6) Output-repair pipeline ────────────────────────────────────────────


class _Wrapper(BaseModel):
    items: list[_DummyOutput]


def test_repair_unwraps_single_key_inner_json():
    """CC sometimes wraps the schema-conforming JSON inside a single-key
    envelope like ``{"final": "{\"text\":...}"}``. The repair pipeline
    should unwrap it before raising Pydantic validation error."""
    from chatbot.claude_cli_provider import _try_repair_payload_for_schema

    payload = {"final": '{"text": "hello", "score": 0.42}'}
    out = _try_repair_payload_for_schema(_DummyOutput, payload)
    assert out is not None
    assert out.text == "hello"
    assert out.score == 0.42


def test_repair_coerces_bare_list_into_wrapper_schema():
    """``_coerce_structured_output_shape`` wraps a bare list into the
    expected single-field wrapper schema."""
    from chatbot.claude_cli_provider import _try_repair_payload_for_schema

    raw_list = [{"text": "a", "score": 1.0}, {"text": "b", "score": 2.0}]
    out = _try_repair_payload_for_schema(_Wrapper, raw_list)
    assert out is not None
    assert len(out.items) == 2
    assert out.items[0].text == "a"


def test_repair_returns_none_when_no_strategy_works():
    """Genuinely broken payloads should return None so the caller raises
    the original ClaudeCliValidationError with the payload preview."""
    from chatbot.claude_cli_provider import _try_repair_payload_for_schema

    out = _try_repair_payload_for_schema(_DummyOutput, {"completely": "wrong"})
    assert out is None
