"""Tests for backend/ollama_client.py — list_models, show_model, health_check."""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest


# ────────────────────────────── Exception hierarchy ─────────────────────────


def test_exception_hierarchy():
    from ollama_client import (
        OllamaConnectionError, OllamaAuthError, OllamaProviderError,
    )
    for cls in (OllamaConnectionError, OllamaAuthError, OllamaProviderError):
        assert issubclass(cls, Exception)
    assert OllamaAuthError is not OllamaConnectionError
    assert OllamaProviderError is not OllamaConnectionError
    assert OllamaProviderError is not OllamaAuthError


# ─────────────────────────────────── list_models ────────────────────────────


def test_list_models_happy_path_under_20_enriches_context():
    """When ≤20 models, list_models enriches each with context_length via show."""
    from ollama_client import list_models

    fake_list = {"models": [
        {"name": "llama3.2", "model": "llama3.2",
         "size": 2_000_000_000,
         "details": {"parameter_size": "3B", "quantization_level": "Q4_K_M"}},
        {"name": "qwen2.5:14b", "model": "qwen2.5:14b",
         "size": 9_000_000_000,
         "details": {"parameter_size": "14B", "quantization_level": "Q4_K_M"}},
    ]}
    fake_show = {
        "llama3.2": {"model_info": {"llama.context_length": 131072}},
        "qwen2.5:14b": {"model_info": {"qwen2.context_length": 32768}},
    }

    fake_client = AsyncMock()
    fake_client.list = AsyncMock(return_value=fake_list)
    fake_client.show = AsyncMock(side_effect=lambda model: fake_show[model])

    with patch("ollama_client.AsyncClient", return_value=fake_client):
        result = asyncio.run(list_models("http://localhost:11434"))

    assert len(result) == 2
    by_name = {m["name"]: m for m in result}
    assert by_name["llama3.2"]["context_length"] == 131072
    assert by_name["llama3.2"]["parameter_size"] == "3B"
    assert by_name["llama3.2"]["quantization_level"] == "Q4_K_M"
    assert by_name["qwen2.5:14b"]["context_length"] == 32768


def test_list_models_over_20_skips_context_enrichment():
    """When >20 models, context_length is None (no /api/show fanout)."""
    from ollama_client import list_models

    fake_list = {"models": [
        {"name": f"m{i}", "model": f"m{i}", "size": 1_000_000_000,
         "details": {"parameter_size": "1B", "quantization_level": "Q4_K_M"}}
        for i in range(21)
    ]}
    fake_client = AsyncMock()
    fake_client.list = AsyncMock(return_value=fake_list)
    fake_client.show = AsyncMock(side_effect=AssertionError("show must not be called"))

    with patch("ollama_client.AsyncClient", return_value=fake_client):
        result = asyncio.run(list_models("http://localhost:11434"))

    assert len(result) == 21
    assert all(m["context_length"] is None for m in result)
    fake_client.show.assert_not_called()


def test_list_models_auth_error_on_401():
    from ollama import ResponseError
    from ollama_client import list_models, OllamaAuthError

    fake_client = AsyncMock()
    fake_client.list = AsyncMock(side_effect=ResponseError("unauthorized", 401))

    with patch("ollama_client.AsyncClient", return_value=fake_client):
        with pytest.raises(OllamaAuthError):
            asyncio.run(list_models("https://ollama.com/v1", api_key="bad"))


def test_list_models_connection_error_on_network_failure():
    import httpx
    from ollama_client import list_models, OllamaConnectionError

    fake_client = AsyncMock()
    fake_client.list = AsyncMock(side_effect=httpx.ConnectError("refused"))

    with patch("ollama_client.AsyncClient", return_value=fake_client):
        with pytest.raises(OllamaConnectionError):
            asyncio.run(list_models("http://localhost:11434"))


def test_list_models_sends_bearer_when_api_key_provided():
    """When api_key is non-empty, Authorization header is set."""
    from ollama_client import list_models

    fake_list = {"models": []}
    fake_client = AsyncMock()
    fake_client.list = AsyncMock(return_value=fake_list)

    captured = {}
    def _factory(*args, **kwargs):
        captured.update(kwargs)
        return fake_client

    with patch("ollama_client.AsyncClient", side_effect=_factory):
        asyncio.run(list_models("https://ollama.com/v1", api_key="secret"))

    headers = captured.get("headers", {}) or {}
    assert headers.get("Authorization") == "Bearer secret"


# ─────────────────────────────────── show_model ─────────────────────────────


def test_show_model_returns_raw_response():
    from ollama_client import show_model

    expected = {
        "model_info": {"llama.context_length": 131072},
        "capabilities": ["completion", "tools"],
    }
    fake_client = AsyncMock()
    fake_client.show = AsyncMock(return_value=expected)
    with patch("ollama_client.AsyncClient", return_value=fake_client):
        result = asyncio.run(show_model("http://localhost:11434", None, "llama3.2"))
    assert result == expected
    fake_client.show.assert_awaited_once_with("llama3.2")


def test_show_model_auth_error_on_401():
    from ollama import ResponseError
    from ollama_client import show_model, OllamaAuthError

    fake_client = AsyncMock()
    fake_client.show = AsyncMock(side_effect=ResponseError("unauthorized", 401))
    with patch("ollama_client.AsyncClient", return_value=fake_client):
        with pytest.raises(OllamaAuthError):
            asyncio.run(show_model("https://ollama.com/v1", "bad", "any"))


def test_show_model_connection_error_on_network_failure():
    import httpx
    from ollama_client import show_model, OllamaConnectionError

    fake_client = AsyncMock()
    fake_client.show = AsyncMock(side_effect=httpx.ConnectError("refused"))
    with patch("ollama_client.AsyncClient", return_value=fake_client):
        with pytest.raises(OllamaConnectionError):
            asyncio.run(show_model("http://localhost:11434", None, "llama3.2"))


# ─────────────────────────────────── health_check ───────────────────────────


def test_health_check_ok_when_list_succeeds():
    from ollama_client import health_check

    fake_client = AsyncMock()
    fake_client.list = AsyncMock(return_value={"models": []})
    with patch("ollama_client.AsyncClient", return_value=fake_client):
        ok, msg = asyncio.run(health_check("http://localhost:11434"))
    assert ok is True
    assert msg == ""


def test_health_check_false_on_connection_error_with_message():
    import httpx
    from ollama_client import health_check

    fake_client = AsyncMock()
    fake_client.list = AsyncMock(side_effect=httpx.ConnectError("refused"))
    with patch("ollama_client.AsyncClient", return_value=fake_client):
        ok, msg = asyncio.run(health_check("http://localhost:11434"))
    assert ok is False
    assert "localhost:11434" in msg


def test_health_check_false_on_auth_failure():
    from ollama import ResponseError
    from ollama_client import health_check

    fake_client = AsyncMock()
    fake_client.list = AsyncMock(side_effect=ResponseError("unauthorized", 401))
    with patch("ollama_client.AsyncClient", return_value=fake_client):
        ok, msg = asyncio.run(health_check("https://ollama.com/v1", "bad"))
    assert ok is False
    assert "auth" in msg.lower() or "unauthorized" in msg.lower()
