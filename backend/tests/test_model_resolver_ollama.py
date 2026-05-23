"""Tests for the Ollama-specific bits of backend/model_resolver.py.

Covers:
  * Ollama-specific fields (base_url, keep_alive, think) reach the
    strategy config when the Models row is resolved.
  * Switching a role from Azure → Ollama doesn't leave the old api_key
    behind (the bug that surfaced as
    "provider=gemini model=gpt-oss:20b API_KEY_INVALID" in production).
"""
from unittest.mock import patch


_OLLAMA_ROW = {
    "id": "row-ollama-1",
    "provider": "ollama",
    "model": "gpt-oss:20b",
    "api_key": "",
    "ollama_base_url": "http://REDACTED-IP:11434",
    "ollama_keep_alive": "5m",
    "ollama_think": "medium",
    "openai_base_url": "",
    "nvidia_base_url": "",
    "azure_openai_endpoint": "",
    "azure_openai_api_version": "",
    "reasoning_effort": "",
    "cli_path": "",
    "extra_args": "",
}


def _patch_model_lookup(row):
    """Patch the resolver's DB lookup so we don't need RethinkDB."""
    return patch("model_resolver._get_model_from_cache_or_db", return_value=row)


def test_resolver_propagates_ollama_fields_into_role_config():
    """A *_llm_model_id pointing at an Ollama row must populate
    ollama_base_url / ollama_keep_alive / ollama_think on the role."""
    from model_resolver import resolve_model_refs_in_config
    cfg = {
        "lookback_sentiment_llm_model_id": "row-ollama-1",
    }
    with _patch_model_lookup(_OLLAMA_ROW):
        out = resolve_model_refs_in_config(conn=None, config=cfg)
    assert out["lookback_sentiment_llm_provider"] == "ollama"
    assert out["lookback_sentiment_llm_model"] == "gpt-oss:20b"
    assert out["lookback_sentiment_ollama_base_url"] == "http://REDACTED-IP:11434"
    assert out["lookback_sentiment_ollama_keep_alive"] == "5m"
    assert out["lookback_sentiment_ollama_think"] == "medium"


def test_resolver_clears_stale_api_key_when_switching_to_ollama():
    """Switching a role from Azure → Ollama at the picker MUST clear the
    old Azure api_key off the role. Without this, the dispatcher routed
    the call back through Gemini's API with the stale key and got
    API_KEY_INVALID (see production log 2026-05-23T09:06)."""
    from model_resolver import resolve_model_refs_in_config
    cfg = {
        # Stale role-specific keys from a previous Azure config:
        "lookback_event_maintenance_llm_provider": "azure",
        "lookback_event_maintenance_llm_api_key": "DD7Y...-azure-key-...VDyP",
        "lookback_event_maintenance_llm_model": "gpt-5.4-mini",
        # New picker selection — Ollama row id:
        "lookback_event_maintenance_llm_model_id": "row-ollama-1",
        # Generic-default fallback (left in place by the picker):
        "lookback_llm_provider": "gemini",
        "lookback_llm_api_key": "gemini-default-key-xyz",
    }
    with _patch_model_lookup(_OLLAMA_ROW):
        out = resolve_model_refs_in_config(conn=None, config=cfg)
    # Role-specific provider now points at ollama:
    assert out["lookback_event_maintenance_llm_provider"] == "ollama"
    # And the stale api_key is gone — set to "" explicitly so the
    # downstream strategy doesn't keep using it via fallback:
    assert out["lookback_event_maintenance_llm_api_key"] == ""


def test_resolver_clears_stale_provider_when_model_id_resolves():
    """If a previous gemini value sat on the role-specific provider key,
    the resolver must overwrite it with the model row's provider."""
    from model_resolver import resolve_model_refs_in_config
    cfg = {
        "lookback_sentiment_llm_provider": "gemini",
        "lookback_sentiment_llm_model_id": "row-ollama-1",
    }
    with _patch_model_lookup(_OLLAMA_ROW):
        out = resolve_model_refs_in_config(conn=None, config=cfg)
    assert out["lookback_sentiment_llm_provider"] == "ollama"


def test_resolver_preserves_non_applicable_fields():
    """Fields in field_map that the Ollama row doesn't populate
    (azure_endpoint, openai_base_url, etc.) must not clobber legit
    inline values at other prefixes. Resolver only touches keys with
    the matching prefix."""
    from model_resolver import resolve_model_refs_in_config
    cfg = {
        "lookback_sentiment_llm_model_id": "row-ollama-1",
        # A DIFFERENT prefix's azure config — must be left untouched.
        "lookback_default_azure_openai_endpoint": "https://other.azure.com",
    }
    with _patch_model_lookup(_OLLAMA_ROW):
        out = resolve_model_refs_in_config(conn=None, config=cfg)
    # Sentiment role gets ollama config (provider/model/base_url):
    assert out["lookback_sentiment_llm_provider"] == "ollama"
    # Default role's azure_endpoint is untouched:
    assert out["lookback_default_azure_openai_endpoint"] == "https://other.azure.com"


def test_resolver_no_change_when_no_model_id_keys():
    """Fast path — strategies without any *_llm_model_id keys must
    return the original config unchanged."""
    from model_resolver import resolve_model_refs_in_config
    cfg = {"lookback_llm_provider": "azure", "lookback_llm_model": "gpt-5-mini"}
    out = resolve_model_refs_in_config(conn=None, config=cfg)
    assert out == cfg
