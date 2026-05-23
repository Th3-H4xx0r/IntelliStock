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
