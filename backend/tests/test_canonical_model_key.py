"""Tests for the provider-agnostic canonical model cache identity in llm_utils."""
import llm_utils


# ───────────────────────────── _auto_normalize_model ────────────────────────


def test_auto_normalize_strips_vendor_and_version():
    n = llm_utils._auto_normalize_model
    assert n("openai.gpt-oss-120b-1:0") == "gpt-oss-120b"
    assert n("gpt-oss-120b") == "gpt-oss-120b"                       # azure deployment name
    assert n("openai.gpt-oss-120b-1:0") == n("gpt-oss-120b")        # the equivalence we want
    assert n("us.anthropic.claude-3-5-sonnet-20241022-v2:0") == "claude-3-5-sonnet-20241022"
    assert n("amazon.nova-pro-v1:0") == "nova-pro"


def test_auto_normalize_keeps_distinct_models_distinct():
    n = llm_utils._auto_normalize_model
    assert n("openai.gpt-oss-120b-1:0") != n("openai.gpt-oss-20b-1:0")
    assert n("anthropic.claude-3-5-sonnet-20241022-v2:0") != n("gemini-3-flash-preview")


# ──────────────────────────── _unified_reasoning_effort ─────────────────────


def test_unified_effort_across_providers():
    u = llm_utils._unified_reasoning_effort
    assert u({"reasoning_effort": "medium"}) == "medium"
    assert u({"bedrock_reasoning": "medium"}) == "medium"
    assert u({"ollama_think": "medium"}) == "medium"
    assert u({"bedrock_reasoning": "off"}) == ""
    assert u({"ollama_think": "true"}) == "on"
    assert u({}) == ""


# ──────────────────────────── canonical_model_cache_key ─────────────────────


def test_canonical_key_azure_bedrock_equal():
    k = llm_utils.canonical_model_cache_key
    azure = k("gpt-oss-120b", {"reasoning_effort": "medium"})
    bedrock = k("openai.gpt-oss-120b-1:0", {"bedrock_region": "us-east-1", "bedrock_reasoning": "medium"})
    assert azure == bedrock == "gpt-oss-120b@medium"


def test_canonical_key_effort_changes_key():
    k = llm_utils.canonical_model_cache_key
    assert k("openai.gpt-oss-120b-1:0", {"bedrock_reasoning": "medium"}) != \
           k("openai.gpt-oss-120b-1:0", {"bedrock_reasoning": "high"})
    assert k("openai.gpt-oss-120b-1:0", {"bedrock_reasoning": "off"}) == "gpt-oss-120b"


def test_canonical_key_family_override_wins():
    k = llm_utils.canonical_model_cache_key
    assert k("my-weird-azure-deployment", {"reasoning_effort": "medium", "model_cache_family": "gpt-oss-120b"}) \
        == "gpt-oss-120b@medium"


def test_prompt_cache_key_provider_agnostic():
    azure = llm_utils.canonical_model_cache_key("gpt-oss-120b", {"reasoning_effort": "medium"})
    bedrock = llm_utils.canonical_model_cache_key("openai.gpt-oss-120b-1:0", {"bedrock_reasoning": "medium"})
    assert llm_utils._prompt_cache_key("PROMPT", azure, "") == llm_utils._prompt_cache_key("PROMPT", bedrock, "")
