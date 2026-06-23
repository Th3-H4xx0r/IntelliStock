from kalshi.feature_models import TeamForm, PlayerRate, MatchFeatures


def test_match_features_complete_predicate():
    incomplete = MatchFeatures(fixture_id="f1", home="A", away="B")
    assert incomplete.is_complete() is False

    complete = MatchFeatures(
        fixture_id="f1", home="A", away="B",
        home_form=TeamForm(elo=1600, xg_for=1.6),
        away_form=TeamForm(elo=1500, xg_for=1.2),
    )
    assert complete.is_complete() is True


def test_player_rate_fields():
    p = PlayerRate(name="Striker", minutes=2000, shots=60, xg_per90=0.5, scored_last_n=3)
    assert p.name == "Striker" and p.xg_per90 == 0.5
