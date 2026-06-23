from kalshi.live.cards import build_live_card, market_probs_from_markets


def test_market_probs_devig_normalizes_to_one():
    markets = [
        {"market_type": "winner", "side": "home", "mid_cents": 60},
        {"market_type": "winner", "side": "draw", "mid_cents": 30},
        {"market_type": "winner", "side": "away", "mid_cents": 30},
        {"market_type": "over_under", "side": "over", "mid_cents": 55},  # ignored
    ]
    p = market_probs_from_markets(markets)
    assert set(p) == {"home", "draw", "away"}
    assert abs(sum(p.values()) - 1.0) < 1e-6
    assert p["home"] > p["draw"]


def test_market_probs_empty_when_no_mids():
    assert market_probs_from_markets([]) == {}
    assert market_probs_from_markets([{"market_type": "winner", "side": "home", "mid_cents": 0}]) == {}


def test_build_live_card_shape():
    card = build_live_card(
        instance_id="i1", fixture_id="E1", home="Argentina", away="Austria",
        market_probs={"home": 0.6, "draw": 0.2, "away": 0.2},
        score={"home": 2, "away": 1, "clock": "67'"}, elapsed_min=66.7,
        event="up", news="- Messi scores", decisions=[{"action": "add"}], ts="t",
    )
    assert card["id"] == "i1|E1"
    assert card["home"] == "Argentina" and card["score"]["home"] == 2
    assert card["elapsed_min"] == 66.7 and card["event"] == "up"
    assert card["decisions"][0]["action"] == "add"
