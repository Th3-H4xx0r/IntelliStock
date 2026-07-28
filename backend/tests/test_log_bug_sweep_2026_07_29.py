"""Three defects found by reading a full backtest log end to end (bt#382293).

None of them raised. Each one degraded the run quietly and kept going, which
is the failure mode that produces confident-looking but meaningless research.
"""
import os
import sys

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

import strategies.graph_nexus_analysis as g  # noqa: E402


# ---------------------------------------------------------------- overlay key
def test_overlay_detects_a_missing_key(monkeypatch):
    for env in ("OPENROUTER_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY",
                "ANTHROPIC_API_KEY", "AZURE_OPENAI_API_KEY", "LLM_API_KEY"):
        monkeypatch.delenv(env, raising=False)
    assert g._overlay_llm_key_available({}) is False
    assert g._overlay_llm_key_available({"llm_api_key": ""}) is False
    assert g._overlay_llm_key_available({"llm_api_key": "   "}) is False
    assert g._overlay_llm_key_available(None) is False


def test_overlay_accepts_a_config_or_env_key(monkeypatch):
    for env in ("OPENROUTER_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY",
                "ANTHROPIC_API_KEY", "AZURE_OPENAI_API_KEY", "LLM_API_KEY"):
        monkeypatch.delenv(env, raising=False)
    assert g._overlay_llm_key_available({"llm_api_key": "sk-x"}) is True
    assert g._overlay_llm_key_available({"overlay_llm_api_key": "sk-y"}) is True
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-env")
    assert g._overlay_llm_key_available({}) is True


def test_overlay_is_short_circuited_before_dispatch():
    """756 doomed retry chains on one run, all ending in 'keeping base score'
    — the score they would have kept anyway."""
    src = open(g.__file__).read()
    assert "_overlay_llm_key_available(config)" in src
    assert "Trade overlay LLM: skipped (no API key)" in src


# ------------------------------------------------------------- mcap false alarm
def test_silent_fail_alarm_requires_an_actually_empty_cache():
    """Pre-seed is an incremental top-up called with 1-2 new tickers per cycle.
    Alarming on populated==0 alone cried wolf whenever a discovered small cap
    simply was not a Neo4j Company, while the cache held 5,488 entries."""
    src = open(g.__file__).read()
    assert "if not _neo4j_market_cap_cache:" in src, (
        "the BT109429 alarm must be gated on the cache actually being empty")
    # The red alarm must be the empty-cache variant, and it must sit inside the
    # guarded branch rather than firing on any zero-increment cycle.
    alarm = "0 tickers populated AND the Neo4j market-cap"
    assert alarm in src
    guard_at = src.index("if not _neo4j_market_cap_cache:")
    assert guard_at < src.index(alarm), "the alarm must be inside the guard"


def test_the_benign_case_still_reports_something():
    """Silence would be its own bug — the operator should still see that a
    ticker went unresolved, just not be sent to check Neo4j ingestion."""
    src = open(g.__file__).read()
    assert "no new market caps resolved this cycle" in src
