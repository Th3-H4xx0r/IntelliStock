import pytest
from kalshi.models import OddsQuote
from kalshi.fair_value import fair_from_odds, blend_fair


def test_fair_from_pinnacle_quote_sums_to_one():
    q = OddsQuote(book="pinnacle", home=2.0, draw=3.5, away=4.0)
    f = fair_from_odds(q, method="power")
    assert abs(f["home"] + f["draw"] + f["away"] - 1.0) < 1e-9
    assert f["home"] > f["away"]


def test_fair_from_odds_rejects_unknown_method():
    q = OddsQuote(book="pinnacle", home=2.0, draw=3.5, away=4.0)
    with pytest.raises(ValueError):
        fair_from_odds(q, method="bogus")


def test_blend_returns_sharp_when_no_fallback():
    sharp = {"home": 0.5, "draw": 0.3, "away": 0.2}
    assert blend_fair(sharp, None) == sharp


def test_blend_renormalizes():
    sharp = {"home": 0.5, "draw": 0.3, "away": 0.2}
    fallback = {"home": 0.4, "draw": 0.3, "away": 0.3}
    out = blend_fair(sharp, fallback, sharp_weight=0.5)
    assert abs(sum(out.values()) - 1.0) < 1e-9
