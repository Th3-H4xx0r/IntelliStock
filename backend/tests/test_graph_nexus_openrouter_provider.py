"""Regression: graph_nexus must recognize the openrouter provider.

Before the fix, `_normalize_llm_provider("openrouter")` returned "gemini"
(openrouter missing from _NEXUS_VALID_PROVIDERS), so an OpenRouter model
(e.g. nvidia/nemotron-3-ultra) was silently routed to gemini -> 401/404.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from strategies import graph_nexus_analysis as gna


def test_openrouter_is_a_valid_provider():
    assert "openrouter" in gna._NEXUS_VALID_PROVIDERS


def test_normalize_keeps_openrouter():
    assert gna._normalize_llm_provider("openrouter") == "openrouter"
    assert gna._normalize_llm_provider("OpenRouter") == "openrouter"
    # unknown providers still fall back to gemini (unchanged behavior)
    assert gna._normalize_llm_provider("totally-unknown") == "gemini"


def test_role_config_forwards_openrouter_fields():
    cfg = {
        "llm_provider": "openrouter",
        "llm_model": "nvidia/nemotron-3-ultra-550b-a55b",
        "openrouter_base_url": "https://openrouter.ai/api/v1",
        "openrouter_referer": "https://intellistock.app",
        "openrouter_title": "IntelliStock",
    }
    out = gna._resolve_role_llm_provider_config_fields(cfg, "")
    assert out["openrouter_base_url"] == "https://openrouter.ai/api/v1"
    assert out["openrouter_referer"] == "https://intellistock.app"
    assert out["openrouter_title"] == "IntelliStock"


def test_role_config_openrouter_defaults_empty_when_unset():
    # base_url has a default downstream in llm_utils; we forward nothing extra.
    cfg = {"llm_provider": "openrouter", "llm_model": "nvidia/nemotron-3-ultra-550b-a55b"}
    out = gna._resolve_role_llm_provider_config_fields(cfg, "")
    assert "openrouter_referer" not in out
    assert "openrouter_title" not in out
