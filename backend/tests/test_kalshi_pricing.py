from kalshi.intelligence.pricing import model_market_probs, build_market_probs
from kalshi.strategy.candidates import generate_candidates


def test_model_market_probs_from_expected_goals():
    mp = model_market_probs((1.7, 1.1))
    assert abs(sum(mp["winner"].values()) - 1.0) < 1e-6
    assert mp["winner"]["home"] > mp["winner"]["away"]
    assert abs(mp["over_under"]["over"] + mp["over_under"]["under"] - 1.0) < 1e-9


def test_model_only_when_no_sharp():
    mp = model_market_probs(None)
    assert mp == {}


def test_build_market_probs_fuses_sharp_model_llm():
    eg = (1.7, 1.1)
    sharp = {"winner": {"home": 0.50, "draw": 0.27, "away": 0.23}}
    adj = {"winner": 0.50}  # over the cap -> clamped to +0.05
    fused = build_market_probs(eg, sharp, adj, w_sharp=0.7, llm_cap=0.05)
    # home fused = 0.7*sharp + 0.3*model + clamp(0.5, 0.05)
    assert fused["winner"]["home"] > sharp["winner"]["home"]  # model + llm push it up
    # over_under exists from the model even though sharp didn't price it
    assert "over_under" in fused


def test_pricing_feeds_candidate_generation():
    eg = (1.9, 0.9)
    fused = build_market_probs(eg, {}, {})
    markets = [{"market_ticker": "KX-HOME", "market_type": "winner", "side": "home", "yes_ask_cents": 45}]
    cands = generate_candidates("f1", "low", fused, markets, fee_rate=0.07, edge_threshold=0.03)
    # strong home side, cheap price -> a candidate
    assert len(cands) == 1 and cands[0].side == "home"
