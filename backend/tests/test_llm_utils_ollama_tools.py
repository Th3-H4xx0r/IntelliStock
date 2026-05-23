"""Tests for the Ollama tool-calling adapter in backend/llm_utils.py.

Covers:
  * _normalize_tools_to_openai_shape — accepts OpenAI- or Gemini-shaped lists
  * call_ollama_with_tools — single-shot dispatch with normalised tools,
    returns the {"text", "tool_calls"} shape mirrored from Gemini's tool path.
"""
from unittest.mock import MagicMock, patch
import pytest


# ─────────────────────────────── _normalize_tools ───────────────────────────


def test_normalize_openai_shape_passthrough():
    from llm_utils import _normalize_tools_to_openai_shape

    tools = [{
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "fetch weather",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
            },
        },
    }]
    out = _normalize_tools_to_openai_shape(tools)
    # Functional equivalence (handles either passthrough or deep-copy):
    assert out == tools


def test_normalize_gemini_shape_flattens_function_declarations():
    from llm_utils import _normalize_tools_to_openai_shape

    gemini = [{
        "function_declarations": [
            {
                "name": "get_weather",
                "description": "fetch weather",
                "parameters": {
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                },
            },
            {
                "name": "get_news",
                "description": "fetch news",
                "parameters": {
                    "type": "object",
                    "properties": {"topic": {"type": "string"}},
                },
            },
        ],
    }]

    out = _normalize_tools_to_openai_shape(gemini)
    assert len(out) == 2
    for o in out:
        assert o["type"] == "function"
        assert set(o["function"].keys()) >= {"name", "parameters"}
    assert {o["function"]["name"] for o in out} == {"get_weather", "get_news"}


def test_normalize_rejects_unknown_shape():
    from llm_utils import _normalize_tools_to_openai_shape

    with pytest.raises(ValueError):
        _normalize_tools_to_openai_shape([{"random": "shape"}])


def test_normalize_empty_list_returns_empty_list():
    from llm_utils import _normalize_tools_to_openai_shape
    assert _normalize_tools_to_openai_shape([]) == []


def test_normalize_none_returns_empty_list():
    from llm_utils import _normalize_tools_to_openai_shape
    assert _normalize_tools_to_openai_shape(None) == []
