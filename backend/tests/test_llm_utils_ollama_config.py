"""Tests for Ollama provider config + API key resolution in backend/llm_utils.py."""
import pytest


# ─────────────────── resolve_api_key_for_provider ───────────────────────────


def test_resolve_api_key_for_ollama_returns_explicit_when_provided():
    from llm_utils import resolve_api_key_for_provider
    assert resolve_api_key_for_provider("ollama", "secret-xyz") == "secret-xyz"


def test_resolve_api_key_for_ollama_returns_env_var_when_no_explicit(monkeypatch):
    from llm_utils import resolve_api_key_for_provider
    monkeypatch.setenv("OLLAMA_API_KEY", "from-env-abc")
    assert resolve_api_key_for_provider("ollama", None) == "from-env-abc"


def test_resolve_api_key_for_ollama_returns_empty_when_nothing_set(monkeypatch):
    from llm_utils import resolve_api_key_for_provider
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    # Empty string is valid for local Ollama (no auth).
    assert resolve_api_key_for_provider("ollama", None) == ""


# ─────────────────── _resolve_provider_config ───────────────────────────────


def test_resolve_provider_config_ollama_default_base_url(monkeypatch):
    from llm_utils import _resolve_provider_config
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    out = _resolve_provider_config("ollama", {})
    assert out["ollama_base_url"] == "http://localhost:11434"


def test_resolve_provider_config_ollama_uses_env_var(monkeypatch):
    from llm_utils import _resolve_provider_config
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://REDACTED-IP:11434/")
    out = _resolve_provider_config("ollama", {})
    # Trailing slash stripped:
    assert out["ollama_base_url"] == "http://REDACTED-IP:11434"


def test_resolve_provider_config_ollama_explicit_beats_env(monkeypatch):
    from llm_utils import _resolve_provider_config
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://env-host:11434")
    out = _resolve_provider_config(
        "ollama", {"ollama_base_url": "https://ollama.com/v1/"}
    )
    assert out["ollama_base_url"] == "https://ollama.com/v1"


def test_resolve_provider_config_ollama_propagates_keep_alive():
    from llm_utils import _resolve_provider_config
    out = _resolve_provider_config(
        "ollama", {"ollama_base_url": "http://localhost:11434",
                   "ollama_keep_alive": "  60m  "}
    )
    assert out["ollama_keep_alive"] == "60m"


def test_resolve_provider_config_ollama_omits_empty_keep_alive():
    from llm_utils import _resolve_provider_config
    out = _resolve_provider_config(
        "ollama", {"ollama_base_url": "http://localhost:11434",
                   "ollama_keep_alive": ""}
    )
    # Empty string should not pollute the resolved config.
    assert "ollama_keep_alive" not in out or not out["ollama_keep_alive"]
