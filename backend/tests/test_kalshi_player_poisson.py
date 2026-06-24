from kalshi.quant.player_poisson import p_player_scores


def test_bounded_and_monotonic():
    p = p_player_scores(0.5, 90, True)
    assert 0 < p < 1
    assert p_player_scores(0.8, 90, True) > p_player_scores(0.3, 90, True)
    assert p_player_scores(0.5, 90, True) > p_player_scores(0.5, 45, True)


def test_unconfirmed_starter_reduces():
    assert p_player_scores(0.6, 90, confirmed_starter=False) < p_player_scores(0.6, 90, confirmed_starter=True)


def test_zero_xg_is_zero():
    assert p_player_scores(0.0, 90, True) == 0.0
