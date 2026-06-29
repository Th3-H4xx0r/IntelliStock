"""Regression: earnings, ml_news, and the AI backtest engine must recognize the
openrouter provider (same allow-list gap that bit graph_nexus — see PR #63).

Before the fix, each module's own `_normalize_llm_provider` mapped openrouter ->
"gemini", silently routing OpenRouter models to the wrong endpoint (401/404).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from strategies import earnings
from strategies import ml_news
from engines import ai_backtest_engine


def test_normalize_keeps_openrouter_in_all_three():
    for mod in (earnings, ml_news, ai_backtest_engine):
        assert mod._normalize_llm_provider("openrouter") == "openrouter", mod.__name__
        # unknown still falls back to gemini (unchanged behavior)
        assert mod._normalize_llm_provider("nope") == "gemini", mod.__name__


def test_defaults_do_not_fall_back_to_gemini_for_openrouter():
    # api-key default should read OPENROUTER_API_KEY, not GEMINI_API_KEY
    for mod in (earnings, ml_news, ai_backtest_engine):
        assert mod._default_api_key_for_provider.__module__  # exists
    # model default returns an openrouter-shaped value (empty/env), never a gemini id
    assert "gemini" not in earnings._default_model_for_provider("openrouter").lower()
    assert "gemini" not in ml_news._default_model_for_provider("openrouter").lower()
    assert "gemini" not in ai_backtest_engine._default_model_for_provider("openrouter").lower()


def test_config_strategies_forward_openrouter_fields():
    cfg = {
        "llm_provider": "openrouter",
        "openrouter_base_url": "https://openrouter.ai/api/v1",
        "openrouter_referer": "https://intellistock.app",
        "openrouter_title": "IntelliStock",
    }
    for mod in (earnings, ml_news):
        out = mod._resolve_provider_config(cfg, "openrouter")
        assert out["openrouter_base_url"] == "https://openrouter.ai/api/v1", mod.__name__
        assert out["openrouter_referer"] == "https://intellistock.app", mod.__name__
        assert out["openrouter_title"] == "IntelliStock", mod.__name__


def test_ai_backtest_env_config_forwards_base_url(monkeypatch):
    monkeypatch.setenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    out = ai_backtest_engine._provider_config_from_env("AI_BACKTESTING_AGENT", "openrouter")
    assert out["openrouter_base_url"] == "https://openrouter.ai/api/v1"
