from kalshi.feature_models import MatchFeatures, TeamForm
from kalshi.intelligence.analyst_panel import analyze, build_prompt


def _features():
    return MatchFeatures(
        fixture_id="f1", home="Arsenal", away="Chelsea",
        home_form=TeamForm(elo=1700, xg_for=1.8, form_pts=12),
        away_form=TeamForm(elo=1650, xg_for=1.4, form_pts=7),
        lineup_confirmed=True,
    )


def test_prompt_includes_teams_and_markets():
    p = build_prompt(_features(), ["winner", "over_under"])
    assert "Arsenal" in p and "Chelsea" in p
    assert "winner" in p and "over_under" in p


def test_prompt_includes_news_section_when_provided():
    news = "- Chelsea ruled out both centre-backs for the weekend."
    p = build_prompt(_features(), ["winner"], news=news)
    assert "RECENT NEWS" in p
    assert "centre-backs" in p


def test_prompt_omits_news_section_when_empty():
    p = build_prompt(_features(), ["winner"], news="")
    assert "RECENT NEWS" not in p


def test_analyze_threads_news_into_prompt():
    captured = {}

    def fake_llm(prompt):
        captured["prompt"] = prompt
        return {"adjustments": {}, "shortlist": [], "rationales": {}}

    analyze(_features(), ["winner"], news="- Striker suspended.", llm_call=fake_llm)
    assert "Striker suspended" in captured["prompt"]


def test_analyze_clamps_adjustments_and_captures_rationale():
    captured = {}

    def fake_llm(prompt):
        captured["prompt"] = prompt
        return {
            "adjustments": {"winner": 0.5, "over_under": -0.02},  # winner over the cap
            "shortlist": ["winner"],
            "rationales": {"winner": "Arsenal at home; Chelsea missing both centre-backs."},
        }

    out = analyze(_features(), ["winner", "over_under"], llm_call=fake_llm)
    assert "Arsenal" in captured["prompt"]
    assert out["adjustments"]["winner"] == 0.05           # hard-clamped to +cap
    assert out["adjustments"]["over_under"] == -0.02       # within cap, unchanged
    assert out["shortlist"] == ["winner"]
    assert out["rationales"]["winner"].startswith("Arsenal")


def test_analyze_degrades_on_llm_failure():
    def boom(prompt):
        raise RuntimeError("llm down")

    out = analyze(_features(), ["winner"], llm_call=boom)
    assert out == {"adjustments": {}, "shortlist": [], "rationales": {}}


def test_analyze_never_raises_on_malformed_output():
    # A loosely-validated LLM may return non-dict/non-list fields — must not crash.
    def malformed(prompt):
        return {"adjustments": "not a dict", "shortlist": "winner", "rationales": ["x"]}

    out = analyze(_features(), ["winner"], llm_call=malformed)
    assert out == {"adjustments": {}, "shortlist": [], "rationales": {}}


def test_analyze_rejects_non_finite_adjustments():
    def nan_llm(prompt):
        return {"adjustments": {"winner": float("nan"), "over_under": float("inf")}}

    out = analyze(_features(), ["winner", "over_under"], llm_call=nan_llm)
    assert out["adjustments"] == {}   # NaN/inf dropped, never become a max-cap nudge
