"""Macro-article skeleton detection must NOT treat a model `route="ignore"`
verdict as a no-data failure.

Bug (live main, lookback): _classify_macro_article_chunk's skeleton check and the
defense-in-depth cache filter both exempted route in ("", "macro", "ignore"), so a
legitimate "this article is irrelevant → ignore" classification was logged as
"skeleton output — treating as failure", never cached, and re-fetched + re-failed
every cycle. An `ignore` verdict IS a real classification and must count as signal
(so it gets cached). Bare-default rows (route defaults to "macro", everything else
default) are still genuine skeletons and must NOT count.

Both detectors now delegate to one helper: _macro_fields_have_signal.
"""
from __future__ import annotations


def test_route_ignore_counts_as_signal():
    import strategies.graph_nexus_analysis as g
    assert g._macro_fields_have_signal(
        route="ignore", reason="", impact_strength=0.0,
        macro_signal_type="general", impact_direction="neutral",
    ) is True


def test_bare_default_is_skeleton():
    import strategies.graph_nexus_analysis as g
    # route defaults to "macro"; with all other fields default this is a true skeleton.
    assert g._macro_fields_have_signal(
        route="macro", reason="", impact_strength=0.0,
        macro_signal_type="general", impact_direction="neutral",
    ) is False
    assert g._macro_fields_have_signal(
        route="", reason="", impact_strength=0.0,
        macro_signal_type="", impact_direction="",
    ) is False


def test_real_signal_counts():
    import strategies.graph_nexus_analysis as g
    assert g._macro_fields_have_signal(route="macro", impact_direction="bearish") is True
    assert g._macro_fields_have_signal(route="macro", reason="rate hike") is True
    assert g._macro_fields_have_signal(route="macro", impact_strength=0.6) is True
    assert g._macro_fields_have_signal(route="macro", macro_signal_type="trade_policy") is True
    # company_focused / mixed routes are also real classifications, not skeletons.
    assert g._macro_fields_have_signal(route="company_focused") is True
    assert g._macro_fields_have_signal(route="mixed") is True
