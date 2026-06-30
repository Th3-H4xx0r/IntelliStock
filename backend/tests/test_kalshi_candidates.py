from kalshi.strategy.candidates import generate_candidates, Candidate


def _markets():
    return [
        {"market_ticker": "KX-HOME", "market_type": "winner", "side": "home", "yes_ask_cents": 50},
        {"market_ticker": "KX-AWAY", "market_type": "winner", "side": "away", "yes_ask_cents": 18},
        {"market_ticker": "KX-OVER", "market_type": "over_under", "side": "over", "yes_ask_cents": 50},
    ]


def _probs():
    return {"winner": {"home": 0.60, "away": 0.30}, "over_under": {"over": 0.58}}


def test_low_tier_excludes_over_under_and_caps_to_one():
    cands = generate_candidates("f1", "low", _probs(), _markets(), fee_rate=0.07, edge_threshold=0.03)
    assert all(c.market_type == "winner" for c in cands)  # over_under not allowed at low
    assert len(cands) == 1                                 # max 1 bet/game


def test_correlation_keeps_one_winner_side():
    cands = generate_candidates("f1", "medium", _probs(), _markets(), fee_rate=0.07, edge_threshold=0.03)
    winners = [c for c in cands if c.market_type == "winner"]
    assert len(winners) == 1                               # ME: only top-edge side
    assert winners[0].side == "away"                       # away has the bigger edge here
    assert isinstance(cands[0], Candidate)


def test_edge_threshold_filters():
    # away fair barely above price -> filtered out at a high threshold
    probs = {"winner": {"home": 0.60, "away": 0.19}}
    cands = generate_candidates("f1", "low", probs,
                                [{"market_ticker": "KX-AWAY", "market_type": "winner", "side": "away", "yes_ask_cents": 18}],
                                fee_rate=0.07, edge_threshold=0.03)
    assert cands == []


def test_multi_bet_cap_high_tier():
    markets = [{"market_ticker": f"M{i}", "market_type": "over_under", "side": f"o{i}", "yes_ask_cents": 40} for i in range(10)]
    probs = {"over_under": {f"o{i}": 0.70 for i in range(10)}}
    cands = generate_candidates("f1", "high", probs, markets, fee_rate=0.07, edge_threshold=0.03)
    assert len(cands) <= 4


# --- P1: no-sharp markets need a higher edge bar (keep volume only on big edges) ---

def _winner_markets():
    return [
        {"market_ticker": "KX-HOME", "market_type": "winner", "side": "home", "yes_ask_cents": 40},
        {"market_ticker": "KX-AWAY", "market_type": "winner", "side": "away", "yes_ask_cents": 30},
    ]


def test_no_sharp_market_needs_higher_edge_bar():
    # home fair 0.47 @ 40c -> ~+5% net edge: clears the 3% base bar but NOT the 8% no-sharp bar
    probs = {"winner": {"home": 0.47, "away": 0.30}}
    cands = generate_candidates(
        "f1", "medium", probs, _winner_markets(),
        fee_rate=0.07, edge_threshold=0.03,
        sharp_probs={},                 # no sharp line for this fixture
        no_sharp_edge_threshold=0.08,
    )
    assert [c.market_ticker for c in cands] == []   # gated out: model-only, edge < 8%


def test_sharp_market_uses_base_bar():
    # same ~+5% edge as the no-sharp test above, but with a sharp line present -> the
    # base 3% bar applies (not the 8% no-sharp bar), so it IS placed.
    probs = {"winner": {"home": 0.47, "away": 0.30}}
    cands = generate_candidates(
        "f1", "medium", probs, _winner_markets(),
        fee_rate=0.07, edge_threshold=0.03,
        sharp_probs={"winner": {"home": 0.47}},   # sharp present for home
        no_sharp_edge_threshold=0.08,
    )
    assert "KX-HOME" in [c.market_ticker for c in cands]   # base bar applies, placed


def test_no_sharp_big_edge_still_traded():
    # home fair 0.80 @ 40c -> ~+38% edge: clears even the 8% no-sharp bar (keeps volume)
    probs = {"winner": {"home": 0.80, "away": 0.10}}
    cands = generate_candidates(
        "f1", "medium", probs, _winner_markets(),
        fee_rate=0.07, edge_threshold=0.03,
        sharp_probs={}, no_sharp_edge_threshold=0.08,
    )
    assert "KX-HOME" in [c.market_ticker for c in cands]


def test_no_sharp_bar_disabled_by_default():
    probs = {"winner": {"home": 0.50, "away": 0.30}}
    cands = generate_candidates(
        "f1", "medium", probs, _winner_markets(),
        fee_rate=0.07, edge_threshold=0.03, sharp_probs={},
    )  # no_sharp_edge_threshold defaults 0.0 -> unchanged legacy behavior
    assert "KX-HOME" in [c.market_ticker for c in cands]
