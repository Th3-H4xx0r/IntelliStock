from kalshi.risk import RiskCaps
from kalshi.orchestrator import plan_and_allocate

CAPS = RiskCaps(max_contracts_per_market=50, bankroll_cents=100000,
                edge_threshold=0.03, max_open_exposure_frac=0.6, per_league_cap_frac=0.25)


def _fixture(fid, home_xg, away_xg, price):
    return {
        "fixture_id": fid,
        "expected_goals": (home_xg, away_xg),
        "sharp_probs": {},
        "analyst": {"adjustments": {}, "rationales": {"winner": f"{fid}: strong home side."}},
        "kalshi_markets": [{"market_ticker": f"{fid}-HOME", "market_type": "winner", "side": "home", "yes_ask_cents": price}],
        "liquidity": 800,
        "hours_to_kickoff": 6,
        "model_confidence": 0.7,
    }


def test_pipeline_places_and_logs_decisions():
    fixtures = [_fixture("f1", 2.1, 0.8, 40)]  # strong home, cheap -> placed
    out = plan_and_allocate(
        fixtures, instance_id="i1", brokerage_id="b1", ts="t", tier="low", caps=CAPS,
        fee_rate=0.07, edge_threshold=0.03, reserve_frac=0.0, expected_better_soon=False,
    )
    assert len(out["allocations"]) == 1
    placed = [d for d in out["decisions"] if d["decision"] == "placed"]
    assert len(placed) == 1
    d = placed[0]
    assert d["market_ticker"] == "f1-HOME" and d["size"] > 0
    assert d["model_prob"] is not None
    assert d["llm_rationale"].startswith("f1")


def test_reserve_holds_back_capital():
    fixtures = [_fixture("f1", 2.1, 0.8, 40)]
    out = plan_and_allocate(
        fixtures, instance_id="i1", brokerage_id="b1", ts="t", tier="low", caps=CAPS,
        fee_rate=0.07, edge_threshold=0.03, reserve_frac=0.5, expected_better_soon=True,
    )
    spent = sum(a["stake_cents"] for a in out["allocations"])
    assert spent <= 50000  # half held in reserve


def test_no_edge_yields_no_placed_decisions():
    fixtures = [_fixture("f1", 1.0, 1.0, 80)]  # even sides, expensive -> no edge
    out = plan_and_allocate(
        fixtures, instance_id="i1", brokerage_id="b1", ts="t", tier="low", caps=CAPS,
        fee_rate=0.07, edge_threshold=0.05, reserve_frac=0.0, expected_better_soon=False,
    )
    assert all(d["decision"] != "placed" for d in out["decisions"])


def _fixture_3way(fid, home_xg, away_xg, asks, sharp=None):
    """A fixture with a full 3-way `winner` market (home/draw/away asks in
    cents) — used for the market-anchor tests."""
    return {
        "fixture_id": fid,
        "expected_goals": (home_xg, away_xg),
        "sharp_probs": {"winner": sharp} if sharp else {},
        "analyst": {"adjustments": {}, "rationales": {}},
        "kalshi_markets": [
            {"market_ticker": f"{fid}-{side.upper()}", "market_type": "winner",
             "side": side, "yes_ask_cents": price}
            for side, price in asks.items()
        ],
        "liquidity": 800,
        "hours_to_kickoff": 6,
        "model_confidence": 0.7,
    }


def _decision_for(out, market_ticker):
    for d in out["decisions"]:
        if d["market_ticker"] == market_ticker:
            return d
    raise AssertionError(f"no decision for {market_ticker}")


def test_market_shrink_pulls_fused_fair_toward_market_when_no_sharp():
    # Strong home favorite per the model; the market (near-even asks) disagrees.
    asks = {"home": 34, "draw": 34, "away": 34}
    fixtures_noshrink = [_fixture_3way("f1", 2.5, 0.3, asks)]
    fixtures_shrink = [_fixture_3way("f1", 2.5, 0.3, asks)]

    out_noshrink = plan_and_allocate(
        fixtures_noshrink, instance_id="i1", brokerage_id="b1", ts="t", tier="medium", caps=CAPS,
        fee_rate=0.07, edge_threshold=0.01, reserve_frac=0.0, expected_better_soon=False,
        market_shrink=0.0,
    )
    out_shrink = plan_and_allocate(
        fixtures_shrink, instance_id="i1", brokerage_id="b1", ts="t", tier="medium", caps=CAPS,
        fee_rate=0.07, edge_threshold=0.01, reserve_frac=0.0, expected_better_soon=False,
        market_shrink=0.5,
    )
    fair_noshrink = _decision_for(out_noshrink, "f1-HOME")["fused_fair"]
    fair_shrink = _decision_for(out_shrink, "f1-HOME")["fused_fair"]

    # De-vigged near-even asks put the market's "home" prob near 1/3 — well below
    # the model's strong-favorite fair. Shrinking toward the market must pull the
    # fused fair DOWN (toward the market), not leave it unchanged.
    assert fair_shrink < fair_noshrink


def test_market_shrink_is_a_noop_when_sharp_present():
    asks = {"home": 34, "draw": 34, "away": 34}
    sharp = {"home": 0.62, "draw": 0.23, "away": 0.15}
    fixtures_noshrink = [_fixture_3way("f1", 2.5, 0.3, asks, sharp=sharp)]
    fixtures_shrink = [_fixture_3way("f1", 2.5, 0.3, asks, sharp=sharp)]

    out_noshrink = plan_and_allocate(
        fixtures_noshrink, instance_id="i1", brokerage_id="b1", ts="t", tier="medium", caps=CAPS,
        fee_rate=0.07, edge_threshold=0.01, reserve_frac=0.0, expected_better_soon=False,
        market_shrink=0.0,
    )
    out_shrink = plan_and_allocate(
        fixtures_shrink, instance_id="i1", brokerage_id="b1", ts="t", tier="medium", caps=CAPS,
        fee_rate=0.07, edge_threshold=0.01, reserve_frac=0.0, expected_better_soon=False,
        market_shrink=0.5,
    )
    fair_noshrink = _decision_for(out_noshrink, "f1-HOME")["fused_fair"]
    fair_shrink = _decision_for(out_shrink, "f1-HOME")["fused_fair"]

    # The anchor only fires when there is NO sharp line — with a sharp line
    # present, market_shrink must not move the fused fair at all.
    assert fair_shrink == fair_noshrink


def test_one_bet_per_fixture_caps_placed_candidates():
    # Strong home favorite -> both `winner:home` and `double_chance:home_draw`
    # clear the edge bar at these prices, and they are DIFFERENT market types so
    # generate_candidates' own mutual-exclusion (per-type) doesn't collapse them.
    fixture = {
        "fixture_id": "f1",
        "expected_goals": (2.5, 0.3),
        "sharp_probs": {},
        "analyst": {"adjustments": {}, "rationales": {}},
        "kalshi_markets": [
            {"market_ticker": "f1-HOME", "market_type": "winner", "side": "home", "yes_ask_cents": 30},
            {"market_ticker": "f1-HOMEDRAW", "market_type": "double_chance", "side": "home_draw", "yes_ask_cents": 50},
        ],
        "liquidity": 800,
        "hours_to_kickoff": 6,
        "model_confidence": 0.7,
    }
    caps = RiskCaps(max_contracts_per_market=50, bankroll_cents=1000000,
                    edge_threshold=0.03, max_open_exposure_frac=0.9, per_league_cap_frac=0.9)

    out_capped = plan_and_allocate(
        [dict(fixture)], instance_id="i1", brokerage_id="b1", ts="t", tier="medium", caps=caps,
        fee_rate=0.07, edge_threshold=0.03, reserve_frac=0.0, expected_better_soon=False,
        one_bet_per_fixture=True,
    )
    out_uncapped = plan_and_allocate(
        [dict(fixture)], instance_id="i1", brokerage_id="b1", ts="t", tier="medium", caps=caps,
        fee_rate=0.07, edge_threshold=0.03, reserve_frac=0.0, expected_better_soon=False,
        one_bet_per_fixture=False,
    )
    placed_capped = [d for d in out_capped["decisions"] if d["decision"] == "placed"]
    placed_uncapped = [d for d in out_uncapped["decisions"] if d["decision"] == "placed"]

    assert len(placed_capped) <= 1
    assert len(placed_uncapped) == 2
