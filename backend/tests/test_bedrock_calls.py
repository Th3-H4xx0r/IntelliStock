"""Tests for the Bedrock call paths + config helpers in backend/llm_utils.py."""
import llm_utils


# ────────────────────────── _normalize_bedrock_reasoning ────────────────────


def test_reasoning_off_returns_none():
    assert llm_utils._normalize_bedrock_reasoning("off", "us.anthropic.claude-3-7-sonnet-20250219-v1:0") is None
    assert llm_utils._normalize_bedrock_reasoning("", "anthropic.claude-3-5-sonnet-20241022-v2:0") is None


def test_reasoning_claude_maps_to_budget():
    out = llm_utils._normalize_bedrock_reasoning("medium", "us.anthropic.claude-3-7-sonnet-20250219-v1:0")
    assert out == {"reasoning_config": {"type": "enabled", "budget_tokens": 4096}}
    hi = llm_utils._normalize_bedrock_reasoning("high", "anthropic.claude-opus-4-20250514-v1:0")
    assert hi["reasoning_config"]["budget_tokens"] == 16384
    lo = llm_utils._normalize_bedrock_reasoning("low", "anthropic.claude-3-7-sonnet-20250219-v1:0")
    assert lo["reasoning_config"]["budget_tokens"] == 1024


def test_reasoning_omitted_for_non_claude():
    # Llama / Nova / Mistral don't take Claude's reasoning_config — omit to avoid 400.
    assert llm_utils._normalize_bedrock_reasoning("high", "meta.llama3-1-70b-instruct-v1:0") is None
    assert llm_utils._normalize_bedrock_reasoning("high", "amazon.nova-pro-v1:0") is None


# ────────────────────────── config helpers ──────────────────────────────────


def test_resolve_api_key_bedrock_env(monkeypatch):
    monkeypatch.setenv("BEDROCK_API_KEY", "envkey")
    assert llm_utils.resolve_api_key_for_provider("bedrock", None) == "envkey"
    assert llm_utils.resolve_api_key_for_provider("bedrock", "explicit") == "explicit"


def test_cache_effort_key_bedrock_isolates_reasoning():
    k_off = llm_utils._cache_effort_key("bedrock", {"bedrock_reasoning": "off"})
    k_hi = llm_utils._cache_effort_key("bedrock", {"bedrock_reasoning": "high"})
    assert k_off != k_hi
    assert k_hi.startswith("reason:")
    assert k_off == ""  # off behaves like "no effort set"


def test_resolve_provider_config_bedrock_keeps_region_reasoning():
    cfg = llm_utils._resolve_provider_config("bedrock", {"bedrock_region": "us-west-2", "bedrock_reasoning": "Low"})
    assert cfg["bedrock_region"] == "us-west-2"
    assert cfg["bedrock_reasoning"] == "low"
    assert "reasoning_effort" not in cfg


def test_safe_provider_meta_bedrock():
    meta = llm_utils._safe_provider_meta("bedrock", {"bedrock_region": "eu-central-1", "bedrock_reasoning": "high"})
    assert meta["bedrock_region"] == "eu-central-1"
    assert meta["bedrock_reasoning"] == "high"
