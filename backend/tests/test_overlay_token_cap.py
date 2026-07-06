"""Trade-overlay LLM output-token cap.

Regression for the 2026-07-06 live stall: the overlay call passed
``max_output_tokens=0``, which the OpenRouter path turns into a 32,768-token
reasoning-safe default. Nemotron then spiralled to ~28k reasoning tokens on a
single symbol (238s), and a `requests` between-bytes read timeout can't stop a
steadily-streaming generation — so one symbol stalled the whole bar. The
overlay response is a tiny bounded JSON, so it must send a sane, tunable cap.
"""
import sys
import os

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from strategies.graph_nexus_analysis import _overlay_max_output_tokens  # noqa: E402


def test_default_when_absent():
    assert _overlay_max_output_tokens({}) == 2500


def test_none_config_uses_default():
    assert _overlay_max_output_tokens(None) == 2500


def test_honours_explicit_positive():
    assert _overlay_max_output_tokens({"overlay_llm_max_output_tokens": 1200}) == 1200


def test_zero_coerces_to_default_never_uncapped():
    # 0 was the bug (→ 32768 on the wire); it must never mean "uncapped" again.
    assert _overlay_max_output_tokens({"overlay_llm_max_output_tokens": 0}) == 2500


def test_negative_coerces_to_default():
    assert _overlay_max_output_tokens({"overlay_llm_max_output_tokens": -5}) == 2500


def test_invalid_coerces_to_default():
    assert _overlay_max_output_tokens({"overlay_llm_max_output_tokens": "abc"}) == 2500


def test_string_number_is_accepted():
    assert _overlay_max_output_tokens({"overlay_llm_max_output_tokens": "1800"}) == 1800
