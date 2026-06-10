"""Article doc-id cache keys on canonical model identity (provider-agnostic)."""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.abspath(os.path.join(_HERE, ".."))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from strategies import graph_nexus_analysis as gna  # noqa: E402


def test_doc_id_provider_agnostic_and_effort_aware():
    azure = gna._llm_cached_doc_id("company", "HASH", "gpt-oss-120b", "v3", {"reasoning_effort": "medium"})
    bedrock = gna._llm_cached_doc_id("company", "HASH", "openai.gpt-oss-120b-1:0", "v3",
                                     {"bedrock_region": "us-east-1", "bedrock_reasoning": "medium"})
    assert azure == bedrock == "company|HASH|gpt-oss-120b@medium|v3"


def test_doc_id_reasoning_level_invalidates():
    med = gna._llm_cached_doc_id("company", "HASH", "openai.gpt-oss-120b-1:0", "v3", {"bedrock_reasoning": "medium"})
    high = gna._llm_cached_doc_id("company", "HASH", "openai.gpt-oss-120b-1:0", "v3", {"bedrock_reasoning": "high"})
    assert med != high


def test_role_provider_config_surfaces_cache_family():
    cfg = {
        "company_article_llm_provider": "bedrock",
        "company_article_bedrock_region": "us-east-1",
        "company_article_model_cache_family": "gpt-oss-120b",
    }
    out = gna._resolve_role_llm_provider_config(cfg, "company_article")
    assert out["model_cache_family"] == "gpt-oss-120b"
