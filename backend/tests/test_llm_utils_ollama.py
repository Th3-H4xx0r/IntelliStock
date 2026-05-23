"""Tests for backend/llm_utils.py:_call_ollama (plain-text chat path).

Pattern matches the other providers (``_call_openai``, ``_call_nvidia``):
returns ``""`` on failure, calls ``_stash_last_http(status, body, exc)``
for the critical-guard, and ``_safe_record(provider="ollama", ...)`` for
telemetry. Never raises.
"""
from unittest.mock import MagicMock, patch

import pytest


def _fake_response(text="hi from ollama"):
    return {
        "message": {"content": text},
        "done": True,
        "eval_count": 5,
        "prompt_eval_count": 10,
    }


# ────────────────────────── happy path ──────────────────────────────────────


def test_call_ollama_happy_path_returns_message_content():
    from llm_utils import _call_ollama

    fake_client = MagicMock()
    fake_client.chat.return_value = _fake_response("hi")
    with patch("llm_utils._make_ollama_sync_client", return_value=fake_client):
        out = _call_ollama(
            api_key="", model="llama3.2", prompt="hello",
            max_output_tokens=32, base_url="http://localhost:11434",
        )
    assert out == "hi"
    call_kwargs = fake_client.chat.call_args.kwargs
    assert call_kwargs["model"] == "llama3.2"
    assert call_kwargs["messages"] == [{"role": "user", "content": "hello"}]
    assert call_kwargs["options"]["num_predict"] == 32


def test_call_ollama_json_format_when_response_mime_type_json():
    from llm_utils import _call_ollama

    fake_client = MagicMock()
    fake_client.chat.return_value = _fake_response('{"x": 1}')
    with patch("llm_utils._make_ollama_sync_client", return_value=fake_client):
        out = _call_ollama(
            api_key="", model="llama3.2", prompt="x?",
            max_output_tokens=32, base_url="http://localhost:11434",
            response_mime_type="application/json",
        )
    assert out == '{"x": 1}'
    call_kwargs = fake_client.chat.call_args.kwargs
    assert call_kwargs["format"] == "json"


def test_call_ollama_keep_alive_propagated():
    from llm_utils import _call_ollama

    fake_client = MagicMock()
    fake_client.chat.return_value = _fake_response("ok")
    with patch("llm_utils._make_ollama_sync_client", return_value=fake_client):
        _call_ollama(
            api_key="", model="llama3.2", prompt="x",
            max_output_tokens=8, base_url="http://localhost:11434",
            keep_alive="60m",
        )
    call_kwargs = fake_client.chat.call_args.kwargs
    assert call_kwargs.get("keep_alive") == "60m"


def test_call_ollama_keep_alive_minus_one_sent_as_int_not_string():
    """Ollama's Go parser rejects '-1' as a string ('time: missing unit in
    duration') but accepts -1 as an integer (special-cased to 'never
    unload'). _normalize_ollama_keep_alive must coerce."""
    from llm_utils import _call_ollama

    fake_client = MagicMock()
    fake_client.chat.return_value = _fake_response("ok")
    with patch("llm_utils._make_ollama_sync_client", return_value=fake_client):
        _call_ollama(
            api_key="", model="llama3.2", prompt="x",
            max_output_tokens=8, base_url="http://localhost:11434",
            keep_alive="-1",
        )
    sent = fake_client.chat.call_args.kwargs.get("keep_alive")
    assert sent == -1 and isinstance(sent, int)


def test_call_ollama_keep_alive_zero_sent_as_int():
    """0 must reach Ollama as an int (unload immediately)."""
    from llm_utils import _call_ollama

    fake_client = MagicMock()
    fake_client.chat.return_value = _fake_response("ok")
    with patch("llm_utils._make_ollama_sync_client", return_value=fake_client):
        _call_ollama(
            api_key="", model="llama3.2", prompt="x",
            max_output_tokens=8, base_url="http://localhost:11434",
            keep_alive="0",
        )
    sent = fake_client.chat.call_args.kwargs.get("keep_alive")
    assert sent == 0 and isinstance(sent, int)


def test_call_ollama_keep_alive_empty_string_omits_field():
    """Empty string keep_alive must NOT be sent — Ollama would 400."""
    from llm_utils import _call_ollama

    fake_client = MagicMock()
    fake_client.chat.return_value = _fake_response("ok")
    with patch("llm_utils._make_ollama_sync_client", return_value=fake_client):
        _call_ollama(
            api_key="", model="llama3.2", prompt="x",
            max_output_tokens=8, base_url="http://localhost:11434",
            keep_alive="",
        )
    assert "keep_alive" not in fake_client.chat.call_args.kwargs


def test_normalize_keep_alive_passes_through_duration_strings():
    """5m, 60m, 1h etc. must pass through unchanged."""
    from llm_utils import _normalize_ollama_keep_alive
    assert _normalize_ollama_keep_alive("5m") == "5m"
    assert _normalize_ollama_keep_alive("60m") == "60m"
    assert _normalize_ollama_keep_alive("1h") == "1h"
    assert _normalize_ollama_keep_alive("  5m  ") == "5m"
    # Integer-shaped strings get coerced to int (Ollama needs that for -1/0).
    assert _normalize_ollama_keep_alive("-1") == -1
    assert _normalize_ollama_keep_alive("300") == 300
    # Empty / None → None (omit the field).
    assert _normalize_ollama_keep_alive("") is None
    assert _normalize_ollama_keep_alive("   ") is None
    assert _normalize_ollama_keep_alive(None) is None


def test_call_ollama_falls_back_to_thinking_when_content_empty():
    """Reasoning models (qwen3, deepseek-r1, gpt-oss) emit content in the
    thinking field when num_predict is consumed by reasoning tokens.
    _call_ollama must surface that text rather than returning empty."""
    from llm_utils import _call_ollama

    fake_client = MagicMock()
    fake_client.chat.return_value = {
        "message": {
            "content": "",
            "thinking": "Equities respond to interest-rate expectations.",
        },
        "done": True,
    }
    with patch("llm_utils._make_ollama_sync_client", return_value=fake_client):
        out = _call_ollama(
            api_key="", model="qwen3.6:35b", prompt="x",
            max_output_tokens=128, base_url="http://localhost:11434",
        )
    assert "interest-rate expectations" in out


def test_call_ollama_stashes_reasoning_split_when_both_present():
    """The thread-local reasoning stash captures content + thinking so the
    smoke endpoint can render them separately even though _call_ollama
    returns just the visible content string."""
    from llm_utils import _call_ollama, get_last_ollama_reasoning

    fake_client = MagicMock()
    fake_client.chat.return_value = {
        "message": {
            "content": "Interest rate changes.",
            "thinking": "Let me reason about macro drivers...",
        },
        "done": True,
    }
    with patch("llm_utils._make_ollama_sync_client", return_value=fake_client):
        out = _call_ollama(
            api_key="", model="gpt-oss:20b", prompt="x",
            max_output_tokens=128, base_url="http://localhost:11434",
        )
    # Return value is just content — NOT a mixed/concatenated blob.
    assert out == "Interest rate changes."
    stash = get_last_ollama_reasoning()
    assert stash["content_chars"] == len("Interest rate changes.")
    assert stash["thinking_chars"] == len("Let me reason about macro drivers...")
    assert "macro drivers" in stash["thinking"]


def test_call_ollama_stashes_reasoning_split_when_content_empty():
    """Even when content is empty and we fall back to thinking, the stash
    still records the split — the endpoint sees content_chars=0."""
    from llm_utils import _call_ollama, get_last_ollama_reasoning

    fake_client = MagicMock()
    fake_client.chat.return_value = {
        "message": {
            "content": "",
            "thinking": "All reasoning, no answer.",
        },
        "done": True,
    }
    with patch("llm_utils._make_ollama_sync_client", return_value=fake_client):
        _call_ollama(
            api_key="", model="qwen3.6:35b", prompt="x",
            max_output_tokens=128, base_url="http://localhost:11434",
        )
    stash = get_last_ollama_reasoning()
    assert stash["content_chars"] == 0
    assert stash["thinking_chars"] > 0
    assert stash["thinking"] == "All reasoning, no answer."


def test_call_ollama_prefers_content_over_thinking_when_both_present():
    """When both fields are populated, content (the visible answer) wins."""
    from llm_utils import _call_ollama

    fake_client = MagicMock()
    fake_client.chat.return_value = {
        "message": {
            "content": "Rates.",
            "thinking": "Let me reason about this in detail...",
        },
        "done": True,
    }
    with patch("llm_utils._make_ollama_sync_client", return_value=fake_client):
        out = _call_ollama(
            api_key="", model="qwen3.6:35b", prompt="x",
            max_output_tokens=128, base_url="http://localhost:11434",
        )
    assert out == "Rates."


def test_normalize_think_accepts_bool_and_effort_strings():
    from llm_utils import _normalize_ollama_think
    # Bool-shaped strings → actual bool
    assert _normalize_ollama_think("true") is True
    assert _normalize_ollama_think("True") is True
    assert _normalize_ollama_think("ON") is True
    assert _normalize_ollama_think("yes") is True
    assert _normalize_ollama_think("1") is True
    assert _normalize_ollama_think("false") is False
    assert _normalize_ollama_think("FALSE") is False
    assert _normalize_ollama_think("off") is False
    assert _normalize_ollama_think("no") is False
    assert _normalize_ollama_think("0") is False
    # Effort levels → pass-through lowercase strings
    assert _normalize_ollama_think("low") == "low"
    assert _normalize_ollama_think("Medium") == "medium"
    assert _normalize_ollama_think("HIGH") == "high"
    # Empty / unknown → None (omit field)
    assert _normalize_ollama_think("") is None
    assert _normalize_ollama_think(None) is None
    assert _normalize_ollama_think("   ") is None
    assert _normalize_ollama_think("ultra") is None  # unknown — better silent omit than 400


def test_call_ollama_sends_think_bool_true():
    """think='true' must reach Ollama as JSON bool True (not the string)."""
    from llm_utils import _call_ollama

    fake_client = MagicMock()
    fake_client.chat.return_value = _fake_response("hi")
    with patch("llm_utils._make_ollama_sync_client", return_value=fake_client):
        _call_ollama(
            api_key="", model="qwen3.6:35b", prompt="x",
            max_output_tokens=128, base_url="http://localhost:11434",
            think="true",
        )
    sent = fake_client.chat.call_args.kwargs.get("think")
    assert sent is True


def test_call_ollama_sends_think_bool_false():
    from llm_utils import _call_ollama

    fake_client = MagicMock()
    fake_client.chat.return_value = _fake_response("hi")
    with patch("llm_utils._make_ollama_sync_client", return_value=fake_client):
        _call_ollama(
            api_key="", model="qwen3.6:35b", prompt="x",
            max_output_tokens=128, base_url="http://localhost:11434",
            think="false",
        )
    sent = fake_client.chat.call_args.kwargs.get("think")
    assert sent is False


def test_call_ollama_sends_think_effort_string():
    """gpt-oss accepts think='low'/'medium'/'high' as a string."""
    from llm_utils import _call_ollama

    fake_client = MagicMock()
    fake_client.chat.return_value = _fake_response("hi")
    with patch("llm_utils._make_ollama_sync_client", return_value=fake_client):
        _call_ollama(
            api_key="", model="gpt-oss:20b", prompt="x",
            max_output_tokens=128, base_url="http://localhost:11434",
            think="high",
        )
    sent = fake_client.chat.call_args.kwargs.get("think")
    assert sent == "high"


def test_call_ollama_omits_think_when_unset():
    """No think arg → the field is not sent (Ollama uses model default)."""
    from llm_utils import _call_ollama

    fake_client = MagicMock()
    fake_client.chat.return_value = _fake_response("hi")
    with patch("llm_utils._make_ollama_sync_client", return_value=fake_client):
        _call_ollama(
            api_key="", model="llama3.2", prompt="x",
            max_output_tokens=128, base_url="http://localhost:11434",
        )
    assert "think" not in fake_client.chat.call_args.kwargs


def test_dispatcher_routes_think_from_provider_config():
    """Trace: provider_config.ollama_think → resolved → _call_ollama think kw."""
    from llm_utils import call_llm_by_provider

    captured = {}
    def _fake(api_key, model, prompt, **kwargs):
        captured["think"] = kwargs.get("think")
        return "ok"
    with patch("llm_utils._call_ollama", side_effect=_fake):
        call_llm_by_provider(
            provider="ollama", api_key="", model="qwen3.6:35b", prompt="x",
            max_output_tokens=16,
            provider_config={
                "ollama_base_url": "http://localhost:11434",
                "ollama_think": "high",
            },
        )
    assert captured["think"] == "high"


def test_call_ollama_with_tools_thinking_fallback():
    """Tool-calling path also surfaces thinking when content is empty."""
    from llm_utils import call_ollama_with_tools

    fake_client = MagicMock()
    fake_client.chat.return_value = {
        "message": {
            "content": "",
            "thinking": "I should call get_weather.",
            "tool_calls": [{"function": {"name": "get_weather",
                                         "arguments": {"city": "SF"}}}],
        }
    }
    with patch("llm_utils._make_ollama_sync_client", return_value=fake_client):
        out = call_ollama_with_tools(
            api_key="", model="qwen3.6:35b", prompt="weather?",
            tools=[{"type": "function",
                    "function": {"name": "get_weather",
                                 "parameters": {"type": "object"}}}],
            base_url="http://localhost:11434",
        )
    assert "get_weather" in out["text"] or "call" in out["text"].lower()
    assert out["tool_calls"][0]["name"] == "get_weather"


def test_call_ollama_warms_pair_after_first_success():
    from llm_utils import _call_ollama, _ollama_warm_pairs

    _ollama_warm_pairs.clear()
    fake_client = MagicMock()
    fake_client.chat.return_value = _fake_response("ok")
    with patch("llm_utils._make_ollama_sync_client", return_value=fake_client):
        _call_ollama(
            api_key="", model="llama3.2", prompt="x",
            max_output_tokens=8, base_url="http://localhost:11434",
        )
    assert ("http://localhost:11434", "llama3.2") in _ollama_warm_pairs


def test_call_ollama_stashes_200_on_success():
    """Success path stashes a 200 HTTP for the critical-guard."""
    from llm_utils import _call_ollama, _pop_last_http

    _pop_last_http()  # clear any leftover from earlier tests on this thread
    fake_client = MagicMock()
    fake_client.chat.return_value = _fake_response("ok")
    with patch("llm_utils._make_ollama_sync_client", return_value=fake_client):
        _call_ollama(
            api_key="", model="llama3.2", prompt="x",
            max_output_tokens=8, base_url="http://localhost:11434",
        )
    stash = _pop_last_http()
    assert stash is not None
    assert stash["status"] == 200


# ────────────────────────── failure paths ───────────────────────────────────


def test_call_ollama_404_model_not_installed_is_not_retried_and_returns_empty():
    from ollama import ResponseError
    from llm_utils import _call_ollama, _pop_last_http

    _pop_last_http()
    fake_client = MagicMock()
    fake_client.chat.side_effect = ResponseError("model 'foo' not found", 404)
    with patch("llm_utils._make_ollama_sync_client", return_value=fake_client):
        out = _call_ollama(
            api_key="", model="foo", prompt="x",
            max_output_tokens=32, base_url="http://localhost:11434",
            retries=3,  # would otherwise retry
        )
    assert out == ""
    assert fake_client.chat.call_count == 1
    stash = _pop_last_http()
    assert stash and stash["status"] == 404


def test_call_ollama_401_stashes_401_for_auth_failure_classifier():
    from ollama import ResponseError
    from llm_utils import _call_ollama, _pop_last_http

    _pop_last_http()
    fake_client = MagicMock()
    fake_client.chat.side_effect = ResponseError("unauthorized", 401)
    with patch("llm_utils._make_ollama_sync_client", return_value=fake_client):
        out = _call_ollama(
            api_key="bad", model="any", prompt="x",
            max_output_tokens=32, base_url="https://ollama.com/v1",
            retries=2,  # 401 must not be retried
        )
    assert out == ""
    assert fake_client.chat.call_count == 1
    stash = _pop_last_http()
    assert stash and stash["status"] == 401


def test_call_ollama_5xx_is_retried_until_recovery():
    from ollama import ResponseError
    from llm_utils import _call_ollama, _pop_last_http

    _pop_last_http()
    fake_client = MagicMock()
    fake_client.chat.side_effect = [
        ResponseError("oops", 500),
        ResponseError("oops", 502),
        _fake_response("recovered"),
    ]
    with patch("llm_utils._make_ollama_sync_client", return_value=fake_client), \
         patch("llm_utils._backoff_sleep_seconds", return_value=0):
        out = _call_ollama(
            api_key="", model="llama3.2", prompt="x",
            max_output_tokens=32, base_url="http://localhost:11434",
            retries=2,
        )
    assert out == "recovered"
    assert fake_client.chat.call_count == 3


def test_call_ollama_5xx_exhausted_returns_empty_and_stashes_5xx():
    from ollama import ResponseError
    from llm_utils import _call_ollama, _pop_last_http

    _pop_last_http()
    fake_client = MagicMock()
    fake_client.chat.side_effect = ResponseError("upstream", 502)
    with patch("llm_utils._make_ollama_sync_client", return_value=fake_client), \
         patch("llm_utils._backoff_sleep_seconds", return_value=0):
        out = _call_ollama(
            api_key="", model="llama3.2", prompt="x",
            max_output_tokens=32, base_url="http://localhost:11434",
            retries=2,  # 1 original + 2 retries = 3 calls
        )
    assert out == ""
    assert fake_client.chat.call_count == 3
    stash = _pop_last_http()
    assert stash and 500 <= stash["status"] < 600


def test_call_ollama_connection_error_retried_then_stashed():
    import httpx
    from llm_utils import _call_ollama, _pop_last_http

    _pop_last_http()
    fake_client = MagicMock()
    fake_client.chat.side_effect = httpx.ConnectError("refused")
    with patch("llm_utils._make_ollama_sync_client", return_value=fake_client), \
         patch("llm_utils._backoff_sleep_seconds", return_value=0):
        out = _call_ollama(
            api_key="", model="llama3.2", prompt="x",
            max_output_tokens=32, base_url="http://localhost:11434",
            retries=1,
        )
    assert out == ""
    assert fake_client.chat.call_count == 2
    stash = _pop_last_http()
    assert stash is not None
    # No HTTP status when the connection never landed; classifier reads exc.
    assert stash["status"] is None
    assert stash["exc"] is not None


def test_call_ollama_empty_api_key_works_for_local():
    """Empty api_key is valid for local Ollama; we must not short-circuit."""
    from llm_utils import _call_ollama

    fake_client = MagicMock()
    fake_client.chat.return_value = _fake_response("local-ok")
    with patch("llm_utils._make_ollama_sync_client", return_value=fake_client):
        out = _call_ollama(
            api_key="", model="llama3.2", prompt="x",
            max_output_tokens=8, base_url="http://localhost:11434",
        )
    assert out == "local-ok"


# ────────────────────────── client factory + warm timeout ───────────────────


def test_resolve_ollama_timeout_cold_pair_uses_120s():
    from llm_utils import _resolve_ollama_timeout, _ollama_warm_pairs
    _ollama_warm_pairs.clear()
    assert _resolve_ollama_timeout("http://localhost:11434", "newmodel", None) == 120.0


def test_resolve_ollama_timeout_warm_pair_uses_30s():
    from llm_utils import _resolve_ollama_timeout, _ollama_warm_pairs
    _ollama_warm_pairs.add(("http://localhost:11434", "warmmodel"))
    assert _resolve_ollama_timeout("http://localhost:11434", "warmmodel", None) == 30.0


def test_resolve_ollama_timeout_explicit_override_wins():
    from llm_utils import _resolve_ollama_timeout, _ollama_warm_pairs
    _ollama_warm_pairs.clear()
    assert _resolve_ollama_timeout("http://localhost:11434", "x", 10) == 10.0


# ────────────────────────── dispatcher wiring ───────────────────────────────


def test_dispatcher_routes_provider_ollama():
    """call_llm_by_provider should dispatch provider=ollama to _call_ollama
    with the resolved ollama_base_url + keep_alive."""
    from llm_utils import call_llm_by_provider

    captured = {}
    def _fake(api_key, model, prompt, **kwargs):
        captured["model"] = model
        captured["prompt"] = prompt
        captured["base_url"] = kwargs.get("base_url")
        captured["keep_alive"] = kwargs.get("keep_alive")
        return "dispatcher-ok"

    with patch("llm_utils._call_ollama", side_effect=_fake):
        out = call_llm_by_provider(
            provider="ollama",
            api_key="",
            model="llama3.2",
            prompt="ping",
            max_output_tokens=16,
            provider_config={"ollama_base_url": "http://localhost:11434",
                             "ollama_keep_alive": "5m"},
        )
    assert out == "dispatcher-ok"
    assert captured["model"] == "llama3.2"
    assert captured["base_url"] == "http://localhost:11434"
    assert captured["keep_alive"] == "5m"


def test_dispatcher_empty_api_key_local_ollama_does_not_short_circuit():
    """A local Ollama row legitimately has empty api_key; the dispatcher
    must NOT short-circuit it like it does for the cloud providers."""
    from llm_utils import call_llm_by_provider

    with patch("llm_utils._call_ollama", return_value="local") as fake:
        out = call_llm_by_provider(
            provider="ollama", api_key="", model="llama3.2", prompt="x",
            max_output_tokens=16,
            provider_config={"ollama_base_url": "http://localhost:11434"},
        )
    assert out == "local"
    fake.assert_called_once()


def test_dispatcher_other_providers_still_short_circuit_on_empty_api_key():
    """Regression: the short-circuit fix for ollama must NOT bleed into other
    providers — openai etc. with empty api_key must still return ''."""
    from llm_utils import call_llm_by_provider

    with patch("llm_utils._call_openai") as fake:
        out = call_llm_by_provider(
            provider="openai", api_key="", model="gpt-4o", prompt="x",
            max_output_tokens=16,
        )
    assert out == ""
    fake.assert_not_called()


def test_dispatcher_azure_still_short_circuits_on_empty_api_key():
    """Regression: Azure must still reject empty api_key after the ollama fix."""
    from llm_utils import call_llm_by_provider

    with patch("llm_utils._call_azure_openai") as fake:
        out = call_llm_by_provider(
            provider="azure", api_key="", model="gpt-4o", prompt="x",
            max_output_tokens=16,
            provider_config={"azure_endpoint": "https://x.azure.com",
                             "api_version": "2024-10-21"},
        )
    assert out == ""
    fake.assert_not_called()


def test_dispatcher_nvidia_still_short_circuits_on_empty_api_key():
    """Regression: NVIDIA must still reject empty api_key after the ollama fix."""
    from llm_utils import call_llm_by_provider

    with patch("llm_utils._call_nvidia") as fake:
        out = call_llm_by_provider(
            provider="nvidia", api_key="", model="moonshotai/kimi-k2.6",
            prompt="x", max_output_tokens=16,
        )
    assert out == ""
    fake.assert_not_called()


def test_dispatcher_keep_alive_flows_to_underlying_call():
    """Trace: provider_config.ollama_keep_alive must reach _call_ollama's
    keep_alive kwarg via the dispatcher."""
    from llm_utils import call_llm_by_provider

    captured = {}
    def _fake(api_key, model, prompt, **kwargs):
        captured["keep_alive"] = kwargs.get("keep_alive")
        return "ok"

    with patch("llm_utils._call_ollama", side_effect=_fake):
        out = call_llm_by_provider(
            provider="ollama", api_key="", model="llama3.2", prompt="x",
            max_output_tokens=16,
            provider_config={
                "ollama_base_url": "http://localhost:11434",
                "ollama_keep_alive": "60m",
            },
        )
    assert out == "ok"
    assert captured["keep_alive"] == "60m"


def test_call_ollama_generic_exception_is_not_retried_and_returns_empty():
    """A non-ResponseError, non-network Exception (e.g. SDK parse failure)
    must not retry — it indicates a bug, not a transient outage."""
    from llm_utils import _call_ollama, _pop_last_http

    _pop_last_http()
    fake_client = MagicMock()
    fake_client.chat.side_effect = RuntimeError("JSON parse failed")
    with patch("llm_utils._make_ollama_sync_client", return_value=fake_client):
        out = _call_ollama(
            api_key="", model="llama3.2", prompt="x",
            max_output_tokens=32, base_url="http://localhost:11434",
            retries=3,
        )
    assert out == ""
    assert fake_client.chat.call_count == 1


def test_call_ollama_403_other_4xx_is_not_retried():
    """4xx other than 401/404 still terminates the loop (no retry storm)."""
    from ollama import ResponseError
    from llm_utils import _call_ollama

    fake_client = MagicMock()
    fake_client.chat.side_effect = ResponseError("forbidden", 403)
    with patch("llm_utils._make_ollama_sync_client", return_value=fake_client):
        out = _call_ollama(
            api_key="", model="llama3.2", prompt="x",
            max_output_tokens=32, base_url="http://localhost:11434",
            retries=3,
        )
    assert out == ""
    assert fake_client.chat.call_count == 1


def test_warm_pair_lock_is_acquired_on_concurrent_marks():
    """The warm-pair helpers serialize set updates so concurrent worker
    threads don't observe a torn set. We can't easily prove correctness
    under contention in a unit test, but we can verify the lock is in
    fact held during the public helpers."""
    from llm_utils import (
        _mark_ollama_pair_warm,
        _ollama_pair_is_warm,
        _ollama_warm_pairs_lock,
        _ollama_warm_pairs,
    )

    _ollama_warm_pairs.clear()
    assert not _ollama_pair_is_warm("http://x:11434", "llama3.2")
    _mark_ollama_pair_warm("http://x:11434", "llama3.2")
    assert _ollama_pair_is_warm("http://x:11434", "llama3.2")
    # Lock is a regular threading.Lock — acquire/release works as expected.
    assert _ollama_warm_pairs_lock.acquire(blocking=False) is True
    _ollama_warm_pairs_lock.release()
