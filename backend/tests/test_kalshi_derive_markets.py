from kalshi.quant.dixon_coles import scoreline_matrix
from kalshi.quant.derive_markets import one_x_two, over_under, btts, exact_score, double_chance


def test_one_x_two_sums_to_one():
    m = scoreline_matrix(1.6, 1.1, 10)
    p = one_x_two(m)
    assert abs(p["home"] + p["draw"] + p["away"] - 1.0) < 1e-6
    assert p["home"] > p["away"]  # higher home xg


def test_over_under_complement():
    m = scoreline_matrix(1.6, 1.1, 10)
    o = over_under(m, line=2.5)
    assert abs(o["over"] + o["under"] - 1.0) < 1e-9
    assert 0 < o["over"] < 1


def test_btts_and_exact_and_double_chance():
    m = scoreline_matrix(1.6, 1.1, 10)
    assert 0 < btts(m)["yes"] < 1
    assert 0 < exact_score(m, 1, 1) < 1
    dc = double_chance(m)
    p = one_x_two(m)
    assert abs(dc["home_draw"] - (p["home"] + p["draw"])) < 1e-9
