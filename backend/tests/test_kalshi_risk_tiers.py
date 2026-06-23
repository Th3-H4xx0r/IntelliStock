from kalshi.strategy.risk_tiers import allowed_markets, max_bets_per_game


def test_low_is_winner_only():
    assert allowed_markets("low") == {"winner", "double_chance"}
    assert max_bets_per_game("low") == 1


def test_medium_adds_totals_and_btts():
    a = allowed_markets("medium")
    assert "over_under" in a and "btts" in a
    assert "exact_score" not in a
    assert max_bets_per_game("medium") == 2


def test_max_allows_everything():
    a = allowed_markets("max")
    assert {"winner", "over_under", "btts", "exact_score", "player_score"} <= a
    assert max_bets_per_game("max") >= 4


def test_unknown_tier_defaults_to_medium():
    assert allowed_markets("bogus") == allowed_markets("medium")
