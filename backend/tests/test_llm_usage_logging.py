"""Per-call token usage belongs in the engine log.

The LLM trace has carried `usage` all along, but the log line printed provider,
model, ok, fallback and prompt hash and dropped it. So a run showed which calls
succeeded but not what they cost, and answering "how many tokens did this
backtest burn" meant querying LLMUsage instead of reading the log in front of
you.

Providers disagree on key names, so both families are accepted; a provider that
returns nothing reports n/a rather than a misleading zero.
"""
import os
import sys

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

import strategies.graph_nexus_analysis as g  # noqa: E402

fmt = g._format_llm_usage


def test_openai_style_usage():
    assert fmt({"prompt_tokens": 1200, "completion_tokens": 340,
                "total_tokens": 1540}) == "1200in/340out/1540tot"


def test_bedrock_and_anthropic_key_names():
    assert fmt({"inputTokens": 900, "outputTokens": 210}) == "900in/210out/1110tot"
    assert fmt({"input_tokens": 50, "output_tokens": 10}) == "50in/10out/60tot"


def test_total_is_derived_when_absent():
    assert fmt({"prompt_tokens": 7, "completion_tokens": 3}).endswith("10tot")


def test_missing_usage_is_not_reported_as_zero():
    """A silent 0 would read as a free call; n/a says the provider told us
    nothing."""
    for empty in ({}, None, "nonsense", []):
        assert fmt(empty) == "n/a"


def test_partial_usage_is_marked_not_invented():
    assert fmt({"total_tokens": 77}) == "?in/?out/77tot"


def test_negative_or_bogus_values_are_ignored():
    assert fmt({"prompt_tokens": -5, "completion_tokens": None}) == "n/a"


def test_the_log_line_actually_includes_it():
    src = open(g.__file__).read()
    assert "tokens={_format_llm_usage(trace.get('usage'))}" in src
