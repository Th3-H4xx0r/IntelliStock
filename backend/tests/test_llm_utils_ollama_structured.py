"""Tests for the Ollama structured-output path in backend/llm_utils.py.

We hit Ollama's OpenAI-compatible endpoint (``<base_url>/v1``) via
PydanticAI's ``OpenAIChatModel`` — the most reliable structured-output
path for Ollama as of v0.5+.
"""
from unittest.mock import MagicMock, patch
from pydantic import BaseModel


class _Schema(BaseModel):
    answer: str
    confidence: float


# ──────────────────────── _build_pydantic_ai_model branch ───────────────────


def test_build_pydantic_ai_model_ollama_points_at_v1_endpoint():
    """PydanticAI structured-output path must hit <base_url>/v1 ."""
    from llm_utils import _build_pydantic_ai_model

    captured = {}

    def _fake_chat_model_ctor(name, provider=None, profile=None, **_kwargs):
        captured["model_name"] = name
        captured["provider_obj"] = provider
        return MagicMock()

    with patch("llm_utils.OpenAIChatModel", side_effect=_fake_chat_model_ctor), \
         patch("llm_utils.OpenAIProvider") as fake_prov:
        out = _build_pydantic_ai_model(
            provider="ollama",
            api_key="",
            model="llama3.2",
            provider_config={"ollama_base_url": "http://localhost:11434"},
        )
    assert out is not None
    assert captured["model_name"] == "llama3.2"
    prov_kwargs = fake_prov.call_args.kwargs
    # base_url + api_key now live on the injected timeout-bounded openai_client.
    _client = prov_kwargs["openai_client"]
    _base = str(_client.base_url).rstrip("/")
    assert _base.endswith("/v1")
    assert _base.startswith("http://localhost:11434")
    # Empty api_key gets sentinel string for the OpenAI SDK (which insists
    # on a non-empty key) — local Ollama ignores it.
    assert _client.api_key


def test_build_pydantic_ai_model_ollama_does_not_double_append_v1():
    """If the operator's base_url already ends in /v1, don't add a second /v1."""
    from llm_utils import _build_pydantic_ai_model

    with patch("llm_utils.OpenAIChatModel", return_value=MagicMock()), \
         patch("llm_utils.OpenAIProvider") as fake_prov:
        _build_pydantic_ai_model(
            provider="ollama",
            api_key="cloud-key",
            model="gpt-oss:120b-cloud",
            provider_config={"ollama_base_url": "https://ollama.com/v1"},
        )
    prov_kwargs = fake_prov.call_args.kwargs
    _client = prov_kwargs["openai_client"]
    assert str(_client.base_url).rstrip("/") == "https://ollama.com/v1"
    assert _client.api_key == "cloud-key"


def test_build_pydantic_ai_model_ollama_empty_api_key_allowed():
    """Critical: local Ollama legitimately has no key; we must not return None."""
    from llm_utils import _build_pydantic_ai_model

    with patch("llm_utils.OpenAIChatModel", return_value=MagicMock()), \
         patch("llm_utils.OpenAIProvider"):
        out = _build_pydantic_ai_model(
            provider="ollama",
            api_key="",
            model="llama3.2",
            provider_config={"ollama_base_url": "http://localhost:11434"},
        )
    assert out is not None


def test_build_pydantic_ai_model_other_provider_empty_api_key_returns_none():
    """Regression: openai/etc with empty api_key still returns None."""
    from llm_utils import _build_pydantic_ai_model

    with patch("llm_utils.OpenAIChatModel", return_value=MagicMock()), \
         patch("llm_utils.OpenAIProvider"):
        out = _build_pydantic_ai_model(
            provider="openai",
            api_key="",
            model="gpt-4o",
        )
    assert out is None


# ──────────────────────── call_structured_llm_by_provider ──────────────────


def test_call_structured_llm_by_provider_routes_ollama():
    """Pyramid: the public structured dispatcher must accept provider=ollama
    with empty api_key and dispatch to the PydanticAI pipeline."""
    from llm_utils import call_structured_llm_by_provider

    fake_model = MagicMock()
    fake_run_result = MagicMock()
    fake_run_result.output = _Schema(answer="ok", confidence=1.0)
    fake_agent = MagicMock()
    fake_agent.run_sync.return_value = fake_run_result

    with patch("llm_utils._build_pydantic_ai_model", return_value=fake_model), \
         patch("llm_utils.Agent", return_value=fake_agent):
        out = call_structured_llm_by_provider(
            provider="ollama",
            api_key="",
            model="llama3.2",
            prompt="x",
            output_type=_Schema,
            provider_config={"ollama_base_url": "http://localhost:11434"},
        )
    assert isinstance(out, _Schema)
    assert out.answer == "ok"


def test_call_structured_llm_other_providers_still_short_circuit_on_empty_api_key():
    """Regression: openai/etc with empty api_key continues to return None."""
    from llm_utils import call_structured_llm_by_provider

    with patch("llm_utils._build_pydantic_ai_model") as fake_build, \
         patch("llm_utils.Agent"):
        out = call_structured_llm_by_provider(
            provider="openai", api_key="", model="gpt-4o",
            prompt="x", output_type=_Schema,
        )
    assert out is None
    fake_build.assert_not_called()
