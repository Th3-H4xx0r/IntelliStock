"""Identity parity + sensitivity tests. Imports the shared module only (NOT broker)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import nexus_config_identity as nci

BASE = {
    "discovery_llm_provider": "bedrock", "discovery_llm_model": "kimi-k2.5",
    "sentiment_llm_provider": "bedrock", "sentiment_llm_model": "kimi-k2.5",
    "company_llm_provider": "bedrock", "company_llm_model": "kimi-k2.5",
    "macro_llm_provider": "bedrock", "macro_llm_model": "kimi-k2.5",
    "analyst_llm_provider": "bedrock", "analyst_llm_model": "kimi-k2.5",
    # history-scope reads root-role keys (no prefix) + company_article_/macro_article_/...
    "llm_provider": "bedrock", "llm_model": "kimi-k2.5",
    "company_article_llm_provider": "bedrock", "company_article_llm_model": "kimi-k2.5",
    "macro_article_llm_provider": "bedrock", "macro_article_llm_model": "kimi-k2.5",
    "event_maintenance_llm_provider": "bedrock", "event_maintenance_llm_model": "kimi-k2.5",
    "overlay_llm_provider": "bedrock", "overlay_llm_model": "kimi-k2.5",
    "lookback_learning_days": 120,
}


def test_config_hash_stable_and_16char():
    h = nci.live_config_hash(dict(BASE))
    assert h == nci.live_config_hash(dict(BASE))
    assert len(h) == 16


def test_config_hash_changes_on_model():
    a = nci.live_config_hash(dict(BASE))
    b = nci.live_config_hash({**BASE, "analyst_llm_model": "nemotron-3-ultra"})
    assert a != b


def test_history_scope_24char_and_stable():
    a = nci.history_scope_id(dict(BASE))
    assert a == nci.history_scope_id(dict(BASE))
    assert len(a) == 24


def test_history_scope_changes_on_root_model():
    a = nci.history_scope_id(dict(BASE))
    b = nci.history_scope_id({**BASE, "llm_model": "nemotron-3-ultra"})
    assert a != b


def test_history_scope_stable_on_neutral_key():
    a = nci.history_scope_id(dict(BASE))
    assert a == nci.history_scope_id({**BASE, "buy_threshold": 0.9, "unrelated": "x"})


def test_explicit_history_scope_id_passthrough():
    assert nci.history_scope_id({"history_scope_id": "abc123"}) == "abc123"
