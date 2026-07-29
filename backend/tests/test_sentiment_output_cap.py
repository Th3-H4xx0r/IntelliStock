"""One unbounded LLM call stalled an entire 85-day lookback.

bt#396880 hung on a single OpenRouter sentiment call to a 550B reasoning
model. The request never returned and never recorded a telemetry row, so the
run sat at bar 1/85 with a live heartbeat for 11+ minutes. The per-attempt
timeout DOUBLES (180s -> 360s -> 720s), so one day can burn ~21 minutes before
it even fails -- roughly 14 hours for the lookback if every day behaved that
way.

`max_output_tokens` was hardcoded to 0 (unlimited) for the sentiment call
regardless of model, while the prompt itself asks for "under 1500 total output
tokens". That made the limit a polite request. It is now a bound.
"""
import os
import sys

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

import strategies.graph_nexus_analysis as g  # noqa: E402

limits = g._get_sentiment_prompt_limits


def test_default_is_bounded_not_unlimited():
    cap = limits({})["max_output_tokens"]
    assert cap > 0, "an unbounded default is what hung the run"
    assert cap >= 1500, "must leave headroom over the prompt's own target"


def test_explicit_value_is_honoured():
    assert limits({"sentiment_llm_max_output_tokens": 3000})["max_output_tokens"] == 3000


def test_unlimited_is_still_reachable_but_opt_in():
    """Deliberately still possible — just never the default."""
    assert limits({"sentiment_llm_max_output_tokens": 0})["max_output_tokens"] == 0


def test_bad_values_fall_back_to_the_bounded_default():
    for bad in ("x", None, -100, [], {}):
        cap = limits({"sentiment_llm_max_output_tokens": bad})["max_output_tokens"]
        assert cap > 0, bad


def test_cap_applies_to_lookback_and_live_alike():
    """The stall happened during lookback, but a live tick uses the same path."""
    for lookback in (True, False):
        cfg = {"_nexus_lookback_mode": lookback}
        assert limits(cfg)["max_output_tokens"] > 0
