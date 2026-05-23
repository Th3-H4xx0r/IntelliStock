"""Bedrock support in backend/strategies/graph_nexus_analysis.py.

The critical one: without "bedrock" in _NEXUS_VALID_PROVIDERS,
_normalize_llm_provider silently rewrites it to "gemini".
"""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.abspath(os.path.join(_HERE, ".."))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from strategies import graph_nexus_analysis as gna  # noqa: E402


def test_bedrock_in_valid_providers():
    assert "bedrock" in gna._NEXUS_VALID_PROVIDERS


def test_normalize_keeps_bedrock():
    assert gna._normalize_llm_provider("bedrock") == "bedrock"
    assert gna._normalize_llm_provider("BEDROCK") == "bedrock"


def test_default_model_for_bedrock(monkeypatch):
    monkeypatch.delenv("GRAPH_NEXUS_BEDROCK_MODEL", raising=False)
    assert "claude" in gna._default_model_for_provider("bedrock").lower()


def test_default_model_for_bedrock_env_override(monkeypatch):
    monkeypatch.setenv("GRAPH_NEXUS_BEDROCK_MODEL", "amazon.nova-pro-v1:0")
    assert gna._default_model_for_provider("bedrock") == "amazon.nova-pro-v1:0"


def test_default_api_key_for_bedrock(monkeypatch):
    monkeypatch.setenv("BEDROCK_API_KEY", "bk-env")
    assert gna._default_api_key_for_provider("bedrock") == "bk-env"


def test_role_provider_config_bedrock():
    cfg = {
        "sentiment_llm_provider": "bedrock",
        "sentiment_bedrock_region": "us-west-2",
        "sentiment_bedrock_reasoning": "high",
    }
    out = gna._resolve_role_llm_provider_config(cfg, "sentiment")
    assert out["bedrock_region"] == "us-west-2"
    assert out["bedrock_reasoning"] == "high"
