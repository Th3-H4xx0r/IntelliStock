"""Tests for the Bedrock-specific bits of backend/model_resolver.py.

Mirrors test_model_resolver_ollama.py: bedrock_region / bedrock_reasoning
reach the role config, and switching a role to Bedrock overwrites the stale
provider/api_key (the always_overwrite contract).
"""
from unittest.mock import patch


_BEDROCK_ROW = {
    "id": "row-bedrock-1",
    "provider": "bedrock",
    "model": "us.anthropic.claude-3-5-sonnet-20241022-v2:0",
    "api_key": "bedrock-key-abc",
    "bedrock_region": "us-east-1",
    "bedrock_reasoning": "medium",
    "ollama_base_url": "",
    "ollama_keep_alive": "",
    "ollama_think": "",
    "openai_base_url": "",
    "nvidia_base_url": "",
    "azure_openai_endpoint": "",
    "azure_openai_api_version": "",
    "reasoning_effort": "",
    "cli_path": "",
    "extra_args": "",
}


def _patch_model_lookup(row):
    return patch("model_resolver._get_model_from_cache_or_db", return_value=row)


def test_resolver_propagates_bedrock_fields():
    from model_resolver import resolve_model_refs_in_config
    cfg = {"lookback_sentiment_llm_model_id": "row-bedrock-1"}
    with _patch_model_lookup(_BEDROCK_ROW):
        out = resolve_model_refs_in_config(conn=None, config=cfg)
    assert out["lookback_sentiment_llm_provider"] == "bedrock"
    assert out["lookback_sentiment_llm_model"] == "us.anthropic.claude-3-5-sonnet-20241022-v2:0"
    assert out["lookback_sentiment_llm_api_key"] == "bedrock-key-abc"
    assert out["lookback_sentiment_bedrock_region"] == "us-east-1"
    assert out["lookback_sentiment_bedrock_reasoning"] == "medium"


def test_resolver_overwrites_stale_provider_when_switching_to_bedrock():
    from model_resolver import resolve_model_refs_in_config
    cfg = {
        "lookback_event_maintenance_llm_provider": "ollama",
        "lookback_event_maintenance_llm_api_key": "stale-ollama-key",
        "lookback_event_maintenance_llm_model_id": "row-bedrock-1",
    }
    with _patch_model_lookup(_BEDROCK_ROW):
        out = resolve_model_refs_in_config(conn=None, config=cfg)
    assert out["lookback_event_maintenance_llm_provider"] == "bedrock"
    assert out["lookback_event_maintenance_llm_api_key"] == "bedrock-key-abc"
    assert out["lookback_event_maintenance_bedrock_region"] == "us-east-1"
