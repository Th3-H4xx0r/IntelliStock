"""End-to-end codex-cli dispatch tests without the real binary.

Mirrors test_strategy_claude_cli_dispatch.py — these tests pin the
strategy → llm_utils → codex-cli dispatch path so the same silent-
coerce regression that broke claude-cli (#336915) can't sneak in for
codex.

Subprocess-spawning leaf functions are mocked; tests run anywhere
without needing codex installed.
"""
from __future__ import annotations

import os
import sys
import types
from unittest.mock import patch

import pytest
from pydantic import BaseModel

_BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)


class _DummyOutput(BaseModel):
    text: str
    score: float


@pytest.fixture
def mock_codex_cli_structured():
    with patch("chatbot.codex_cli_provider.call_codex_cli_structured") as m:
        m.return_value = _DummyOutput(text="ok", score=0.5)
        yield m


# ── 1) llm_utils dispatch ────────────────────────────────────────────────


def test_call_structured_llm_routes_codex_cli_branch(mock_codex_cli_structured):
    """call_structured_llm_by_provider must hand codex-cli off to the
    bespoke branch — NOT through PydanticAI / Gemini / OpenAI."""
    from llm_utils import call_structured_llm_by_provider

    out = call_structured_llm_by_provider(
        "codex-cli",
        "ignored-api-key",
        "gpt-5-codex",
        prompt="classify this article",
        output_type=_DummyOutput,
        provider_config={"cli_path": "codex", "extra_args": ""},
    )

    assert out is not None
    assert out.text == "ok"
    assert mock_codex_cli_structured.call_count == 1
    kwargs = mock_codex_cli_structured.call_args.kwargs
    assert kwargs["model"] == "gpt-5-codex"


def test_codex_cli_branch_records_terminal_on_not_authenticated():
    """ClaudeCli's NotLoggedIn → terminal contract: codex must match.
    When CodexCliNotAuthenticatedError fires the metadata must flag
    is_terminal=True so callers can skip retry/split loops."""
    from chatbot.codex_cli_provider import CodexCliNotAuthenticatedError
    from llm_utils import (
        call_structured_llm_by_provider,
        get_last_structured_llm_call_metadata,
    )

    with patch("chatbot.codex_cli_provider.call_codex_cli_structured") as m:
        m.side_effect = CodexCliNotAuthenticatedError("not authenticated")
        out = call_structured_llm_by_provider(
            "codex-cli",
            "ignored",
            "gpt-5-codex",
            prompt="…",
            output_type=_DummyOutput,
            provider_config={"cli_path": "codex"},
        )

    assert out is None
    meta = get_last_structured_llm_call_metadata()
    assert meta.get("ok") is False


def test_plain_llm_routes_codex_cli_branch():
    """call_llm_by_provider must hand codex-cli off to the bespoke branch
    for plain (non-structured) completions too."""
    with patch("chatbot.codex_cli_provider.call_codex_cli_plain") as m:
        m.return_value = "stocks correlate with rates"
        from llm_utils import call_llm_by_provider

        out = call_llm_by_provider(
            "codex-cli",
            "ignored",
            "gpt-5-codex",
            prompt="one sentence about equities",
            provider_config={"cli_path": "codex"},
        )

    assert out == "stocks correlate with rates"
    assert m.call_count == 1
    kwargs = m.call_args.kwargs
    assert kwargs["model"] == "gpt-5-codex"


# ── 2) Per-strategy / per-engine normalizers ─────────────────────────────


@pytest.mark.parametrize(
    "module_path",
    [
        "strategies.graph_nexus_analysis",
        "strategies.earnings",
        "strategies.ml_news",
    ],
)
def test_strategy_modules_normalize_codex_cli(module_path):
    """Every strategy/engine with its own provider normalizer must
    preserve codex-cli (not coerce to gemini)."""
    import importlib

    mod = importlib.import_module(module_path)
    assert hasattr(mod, "_normalize_llm_provider"), f"{module_path} missing _normalize_llm_provider"
    assert mod._normalize_llm_provider("codex-cli") == "codex-cli"
    assert mod._normalize_llm_provider("CODEX-CLI") == "codex-cli"
    # API-key sentinel: keeps the ``if not api_key`` short-circuits from firing.
    assert mod._default_api_key_for_provider("codex-cli") == "codex-cli-no-api-key"
    # Default model
    assert mod._default_model_for_provider("codex-cli") == "gpt-5-codex"


# ── 3) Provider+model save-time compatibility check ──────────────────────


@pytest.mark.parametrize(
    "provider,model",
    [
        # codex-cli should reject models that obviously belong to other
        # providers — same incompat-prefix gate as claude-cli.
        ("codex-cli", "claude-sonnet-4-6"),
        ("codex-cli", "gemini-1.5-pro"),
        ("CODEX-CLI", "Claude-Opus-4-1"),
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
        ("codex-cli", "gpt-5-codex"),
        ("codex-cli", "gpt-4o"),
        ("codex-cli", "o4-mini"),
    ],
)
def test_validate_provider_model_compat_passes_valid(provider, model):
    from interactive_utils import _validate_provider_model_compat

    _validate_provider_model_compat(provider, model)


# ── 4) Test-endpoint reasoning_effort plumbing ───────────────────────────


def test_build_llm_test_provider_config_includes_reasoning_effort_for_codex_cli():
    """The ``/llm/test`` endpoint's provider-config builder must include
    reasoning_effort when codex-cli is selected (same contract as claude-cli)."""
    pytest.importorskip("fastapi")
    from api.main import _build_llm_test_provider_config

    body = types.SimpleNamespace(
        provider="codex-cli",
        api_key="",
        openai_base_url=None,
        nvidia_base_url=None,
        azure_openai_endpoint=None,
        azure_openai_api_version=None,
        reasoning_effort="high",
    )
    cfg = _build_llm_test_provider_config(body)
    assert cfg.get("reasoning_effort") == "high"


# ── 5) model_resolver path: codex-cli refs inject cli_path + extra_args ──


def test_model_resolver_injects_codex_cli_fields():
    """When a strategy config has ``codex_llm_model_id``, the resolver
    should look up the Models row and inject cli_path/extra_args inline."""
    from model_resolver import resolve_model_refs_in_config

    fake_conn = object()
    fake_doc = {
        "id": "model-codex-123",
        "provider": "codex-cli",
        "model": "gpt-5-codex",
        "cli_path": "codex",
        "extra_args": "--quiet",
    }
    with patch("model_resolver._get_model_from_cache_or_db", return_value=fake_doc):
        out = resolve_model_refs_in_config(
            fake_conn,
            {"llm_model_id": "model-codex-123"},
        )

    assert out.get("llm_provider") == "codex-cli"
    assert out.get("model_name") == "gpt-5-codex" or out.get("llm_model") == "gpt-5-codex"
    assert out.get("cli_path") == "codex"
    assert out.get("extra_args") == "--quiet"
