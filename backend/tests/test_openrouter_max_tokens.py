"""Regression tests for the OpenRouter reasoning-model max_tokens gap.

Root cause: OpenRouter reasoning models (e.g. nvidia/nemotron-3-ultra-550b-a55b)
burn the provider-default completion cap on *reasoning* tokens before any JSON
is emitted. Our call sites pass ``max_output_tokens=0`` to mean "uncapped",
which sent NO ``max_tokens`` on the wire and let OpenRouter apply its own finite
default cap — so the structured JSON never fit and PydanticAI raised
"Model token limit (provider default) exceeded before any response was
generated. Increase the `max_tokens` model setting...".

Fix: for OpenRouter specifically, an uncapped call gets an explicit, generous
``max_tokens`` (``_OPENROUTER_UNCAPPED_MAX_OUTPUT_TOKENS``) so reasoning + JSON
both fit. Every other provider path is untouched (uncapped stays uncapped).

These tests are fully caged: no network (requests.post mocked / not reached for
the pure settings builder) and no DB.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import patch

import pytest

_BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

_NEMOTRON = "nvidia/nemotron-3-ultra-550b-a55b"


class _FakeResp:
    def __init__(self, *, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text
        self.headers = {}

    def json(self):
        return self._payload


def _ok_payload():
    return {
        "choices": [{"message": {"content": '{"text": "hi"}'}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
    }


# ── structured model_settings builder ───────────────────────────────────────
def test_structured_settings_openrouter_uncapped_gets_generous_cap():
    import llm_utils
    settings = llm_utils._build_structured_model_settings(
        "openrouter", 0, 60.0, 0.2, model=_NEMOTRON
    )
    assert settings.get("max_tokens") == llm_utils._OPENROUTER_UNCAPPED_MAX_OUTPUT_TOKENS
    assert settings["max_tokens"] > 0


def test_structured_settings_openrouter_respects_explicit_cap():
    import llm_utils
    settings = llm_utils._build_structured_model_settings(
        "openrouter", 512, 60.0, 0.2, model=_NEMOTRON
    )
    assert settings.get("max_tokens") == 512


@pytest.mark.parametrize("provider", ["openai", "nvidia", "deepseek", "azure"])
def test_structured_settings_other_providers_uncapped_untouched(provider):
    """Uncapped (0) for any non-OpenRouter, non-gemini provider must stay
    uncapped (max_tokens=None) exactly as before this fix."""
    import llm_utils
    settings = llm_utils._build_structured_model_settings(
        provider, 0, 60.0, 0.2, model="some-model"
    )
    assert settings.get("max_tokens") is None


def test_structured_settings_gemini_uncapped_unchanged():
    """Gemini keeps its pre-existing 256 floor — this fix does not touch it."""
    import llm_utils
    if llm_utils.GoogleModelSettings is None:
        pytest.skip("GoogleModelSettings unavailable")
    settings = llm_utils._build_structured_model_settings(
        "gemini", 0, 60.0, 0.2, model="gemini-2.0-flash"
    )
    assert settings.get("max_tokens") == 256


# ── plain OpenRouter path (also covers the raw-JSON structured fallback) ─────
def _capture_body_call(**call_kwargs):
    import llm_utils
    captured = {}

    def _fake_post(url, headers=None, json=None, timeout=None):
        captured["body"] = json
        return _FakeResp(payload=_ok_payload())

    with patch("requests.post", side_effect=_fake_post):
        out = llm_utils._call_openrouter(
            "sk-or-key", _NEMOTRON, "decide", **call_kwargs
        )
    return out, captured.get("body", {})


def test_plain_openrouter_uncapped_sends_generous_cap():
    import llm_utils
    _out, body = _capture_body_call(max_output_tokens=0)
    assert body.get("max_tokens") == llm_utils._OPENROUTER_UNCAPPED_MAX_OUTPUT_TOKENS


def test_plain_openrouter_respects_explicit_cap():
    _out, body = _capture_body_call(max_output_tokens=64)
    assert body.get("max_tokens") == 64


def test_plain_openai_uncapped_still_untouched():
    """The OpenAI plain path must NOT gain an injected cap — only OpenRouter."""
    import llm_utils
    captured = {}

    def _fake_post(url, headers=None, json=None, timeout=None):
        captured["body"] = json
        return _FakeResp(payload={
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 2},
        })

    with patch("requests.post", side_effect=_fake_post):
        llm_utils._call_openai("oa-key", "gpt-4o-mini", "decide", max_output_tokens=0)
    body = captured.get("body", {})
    assert "max_tokens" not in body
    assert "max_completion_tokens" not in body
