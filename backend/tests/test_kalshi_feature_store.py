from kalshi.feature_models import TeamForm
from kalshi.data.feature_store import assemble_features, expected_goals
from kalshi.data.discovery import market_type_from_ticker


def test_incomplete_features_have_no_expected_goals():
    f = assemble_features(fixture_id="f1", home="A", away="B")
    assert f.is_complete() is False
    assert expected_goals(f) is None


def test_expected_goals_favors_stronger_attacker_with_home_adv():
    f = assemble_features(
        fixture_id="f1", home="A", away="B",
        home_form=TeamForm(xg_for=2.0, xg_against=0.8),
        away_form=TeamForm(xg_for=1.0, xg_against=1.4),
    )
    hg, ag = expected_goals(f)
    assert hg > ag
    assert hg > 0.1 and ag > 0.1


def test_market_type_classification():
    assert market_type_from_ticker("KXEPL-ARS-WIN", "Arsenal to win") == "winner"
    assert market_type_from_ticker("KX-OVER25", "Over 2.5 total goals") == "over_under"
    assert market_type_from_ticker("KX-BTTS", "Both teams to score") == "btts"
    assert market_type_from_ticker("KX-SCORER", "Saka anytime scorer") == "player_score"
    assert market_type_from_ticker("KX-CS-1-1", "Correct score 1-1") == "exact_score"
    assert market_type_from_ticker("KX-MYSTERY", "") == "other"
