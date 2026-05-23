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
