from kalshi.quant.elo import win_prob, elo_to_expected_goals


def test_win_prob_favors_higher_elo_and_home():
    assert win_prob(1700, 1500) > 0.5
    assert win_prob(1500, 1500) > 0.5  # home-field advantage tips an even matchup
    assert win_prob(1400, 1700) < 0.5


def test_expected_goals_supremacy_and_bounds():
    hg, ag = elo_to_expected_goals(1800, 1500)
    assert hg > ag                       # stronger home scores more
    assert hg >= 0.2 and ag >= 0.2
    eg_equal = elo_to_expected_goals(1500, 1500)
    assert eg_equal[0] >= eg_equal[1]    # home edge from HFA
