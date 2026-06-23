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
