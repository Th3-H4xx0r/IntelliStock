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


# ──────────────────────────── call_ollama_with_tools ───────────────────────


from unittest.mock import MagicMock, patch


def test_call_ollama_with_tools_passes_normalised_tools_and_returns_dict():
    from llm_utils import call_ollama_with_tools

    fake_resp = {
        "message": {
            "content": "calling tool",
            "tool_calls": [
                {"function": {"name": "get_weather",
                              "arguments": {"city": "SF"}}}
            ],
        }
    }
    fake_client = MagicMock()
    fake_client.chat.return_value = fake_resp

    tools = [{
        "type": "function",
        "function": {"name": "get_weather", "description": "fetch",
                     "parameters": {"type": "object",
                                    "properties": {"city": {"type": "string"}}}},
    }]

    with patch("llm_utils._make_ollama_sync_client", return_value=fake_client):
        out = call_ollama_with_tools(
            api_key="", model="qwen2.5", prompt="weather in SF?",
            tools=tools, base_url="http://localhost:11434",
        )

    assert out["text"] == "calling tool"
    assert len(out["tool_calls"]) == 1
    assert out["tool_calls"][0]["name"] == "get_weather"
    assert out["tool_calls"][0]["arguments"] == {"city": "SF"}

    call_kwargs = fake_client.chat.call_args.kwargs
    # The fake captures exactly what was forwarded after normalization.
    assert call_kwargs["tools"] == tools


def test_call_ollama_with_tools_accepts_gemini_shape():
    from llm_utils import call_ollama_with_tools

    fake_client = MagicMock()
    fake_client.chat.return_value = {
        "message": {"content": "ok", "tool_calls": []}
    }
    gemini_tools = [{
        "function_declarations": [
            {"name": "get_weather", "description": "fetch",
             "parameters": {"type": "object",
                            "properties": {"city": {"type": "string"}}}},
        ],
    }]

    with patch("llm_utils._make_ollama_sync_client", return_value=fake_client):
        out = call_ollama_with_tools(
            api_key="", model="qwen2.5", prompt="x",
            tools=gemini_tools, base_url="http://localhost:11434",
        )

    sent_tools = fake_client.chat.call_args.kwargs["tools"]
    assert sent_tools[0]["type"] == "function"
    assert sent_tools[0]["function"]["name"] == "get_weather"
    assert out["text"] == "ok"
    assert out["tool_calls"] == []


def test_call_ollama_with_tools_handles_response_without_tool_calls():
    """Non-tool model returns prose only — tool_calls is empty list."""
    from llm_utils import call_ollama_with_tools

    fake_client = MagicMock()
    fake_client.chat.return_value = {
        "message": {"content": "I will not call any tool today."}
    }
    with patch("llm_utils._make_ollama_sync_client", return_value=fake_client):
        out = call_ollama_with_tools(
            api_key="", model="llama3.2", prompt="?",
            tools=[{"type": "function",
                    "function": {"name": "noop",
                                 "parameters": {"type": "object"}}}],
            base_url="http://localhost:11434",
        )
    assert out["text"].startswith("I will not")
    assert out["tool_calls"] == []


def test_call_ollama_with_tools_parses_stringified_arguments():
    """Some Ollama versions emit arguments as a JSON-encoded string instead
    of a dict. We must normalize to dict for callers."""
    from llm_utils import call_ollama_with_tools

    fake_client = MagicMock()
    fake_client.chat.return_value = {
        "message": {
            "content": "",
            "tool_calls": [{
                "function": {"name": "get_news",
                             "arguments": '{"topic": "markets"}'},
            }],
        }
    }
    with patch("llm_utils._make_ollama_sync_client", return_value=fake_client):
        out = call_ollama_with_tools(
            api_key="", model="qwen2.5", prompt="news?",
            tools=[{"type": "function",
                    "function": {"name": "get_news",
                                 "parameters": {"type": "object"}}}],
            base_url="http://localhost:11434",
        )
    assert out["tool_calls"][0]["arguments"] == {"topic": "markets"}


def test_call_ollama_with_tools_returns_safe_shape_on_provider_error():
    """ResponseError must not propagate — return empty text + tool_calls."""
    from ollama import ResponseError
    from llm_utils import call_ollama_with_tools

    fake_client = MagicMock()
    fake_client.chat.side_effect = ResponseError("model not found", 404)
    with patch("llm_utils._make_ollama_sync_client", return_value=fake_client):
        out = call_ollama_with_tools(
            api_key="", model="ghost", prompt="?",
            tools=[{"type": "function",
                    "function": {"name": "x", "parameters": {"type": "object"}}}],
            base_url="http://localhost:11434",
        )
    assert out == {"text": "", "tool_calls": []}
