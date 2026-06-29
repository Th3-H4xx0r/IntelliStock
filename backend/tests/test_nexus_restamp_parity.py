"""Keystone parity guard: re-stamp identities == the shared identities boot uses.

The broker boot path computes the snapshot/cleanup identities via
``nexus_config_identity.live_config_hash`` / ``history_scope_id`` (imported as
``_nexus_live_config_hash`` / ``_nexus_history_scope_id`` in broker.py). The
re-stamp path MUST compute byte-identical values, or "preserve" silently
degrades to a destructive rebuild. This test fails loudly if the two ever drift.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import nexus_config_identity as nci
import nexus_restamp as nr

REPRESENTATIVE = {
    "llm_provider": "bedrock", "llm_model": "nemotron-3-ultra",
    "discovery_llm_provider": "bedrock", "discovery_llm_model": "nemotron-3-ultra",
    "sentiment_llm_provider": "bedrock", "sentiment_llm_model": "nemotron-3-ultra",
    "company_llm_provider": "bedrock", "company_llm_model": "nemotron-3-ultra",
    "macro_llm_provider": "bedrock", "macro_llm_model": "nemotron-3-ultra",
    "analyst_llm_provider": "bedrock", "analyst_llm_model": "nemotron-3-ultra",
    "company_article_llm_model": "nemotron-3-ultra",
    "macro_article_llm_model": "nemotron-3-ultra",
    "event_maintenance_llm_model": "nemotron-3-ultra",
    "overlay_llm_model": "nemotron-3-ultra",
    "lookback_learning_days": 120,
}


def test_restamp_identities_match_shared_module():
    cfg_hash, scope = nr._identities_for(REPRESENTATIVE)
    assert cfg_hash == nci.live_config_hash(REPRESENTATIVE)
    assert scope == nci.history_scope_id(REPRESENTATIVE)


def test_restamp_uses_the_same_strategy_name_as_boot():
    # Boot hashes under "graph_nexus_analysis"; re-stamp must too (default arg).
    assert nr.NEXUS_STRATEGY_NAME == "graph_nexus_analysis"
    assert nci.NEXUS_STRATEGY_NAME == "graph_nexus_analysis"
