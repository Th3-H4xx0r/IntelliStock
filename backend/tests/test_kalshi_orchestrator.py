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


def test_calibrator_remaps_and_renormalizes_winner_probs():
    """The champion calibrator remaps the winner fair values (here a shrink toward
    the base rate flattens a strong favorite), renormalized to a coherent book."""
    asks = {"home": 55, "draw": 26, "away": 19}
    common = dict(instance_id="i1", brokerage_id="b1", ts="t", tier="medium", caps=CAPS,
                  fee_rate=0.07, edge_threshold=0.01, reserve_frac=0.0,
                  expected_better_soon=False, market_shrink=0.0)
    base = plan_and_allocate([_fixture_3way("f1", 2.4, 0.4, asks)], **common, calibrator=None)
    shrunk = plan_and_allocate([_fixture_3way("f1", 2.4, 0.4, asks)], **common,
                               calibrator={"method": "shrink", "calibrator": None, "shrink_strength": 0.5})

    def side_fair(out, side):
        return next(d["fused_fair"] for d in out["decisions"] if d.get("side") == side)

    # favorite (home) is flattened toward uniform; the group still ~sums to 1
    assert side_fair(shrunk, "home") < side_fair(base, "home")
    total = sum(side_fair(shrunk, s) for s in ("home", "draw", "away"))
    assert abs(total - 1.0) < 1e-6


def test_calibrator_none_is_identity():
    asks = {"home": 55, "draw": 26, "away": 19}
    common = dict(instance_id="i1", brokerage_id="b1", ts="t", tier="medium", caps=CAPS,
                  fee_rate=0.07, edge_threshold=0.01, reserve_frac=0.0,
                  expected_better_soon=False, market_shrink=0.0)
    a = plan_and_allocate([_fixture_3way("f1", 2.4, 0.4, asks)], **common, calibrator=None)
    b = plan_and_allocate([_fixture_3way("f1", 2.4, 0.4, asks)], **common)  # default None
    fa = {d.get("side"): d["fused_fair"] for d in a["decisions"]}
    fb = {d.get("side"): d["fused_fair"] for d in b["decisions"]}
    assert fa == fb


def test_model_champion_overrides_winner_when_nonphysical():
    """A learned/ensemble champion re-fuses the winner group; 'physical' is a no-op."""
    asks = {"home": 55, "draw": 26, "away": 19}
    common = dict(instance_id="i1", brokerage_id="b1", ts="t", tier="medium", caps=CAPS,
                  fee_rate=0.07, edge_threshold=0.01, reserve_frac=0.0,
                  expected_better_soon=False, market_shrink=0.0)
    # a champion model that strongly favours AWAY (opposite of the physical home lean)
    away_fn = lambda fx: {"home": 0.1, "draw": 0.2, "away": 0.7}

    base = plan_and_allocate([_fixture_3way("f1", 2.4, 0.4, asks)], **common,
                             model_champion="physical", model_probs_fn=away_fn)  # gated off
    ens = plan_and_allocate([_fixture_3way("f1", 2.4, 0.4, asks)], **common,
                            model_champion="ensemble", model_probs_fn=away_fn)   # active

    def side_fair(out, side):
        return next(d["fused_fair"] for d in out["decisions"] if d.get("side") == side)

    # physical champion -> away_fn ignored; ensemble champion -> away prob rises
    assert side_fair(ens, "away") > side_fair(base, "away")
    assert abs(sum(side_fair(ens, s) for s in ("home", "draw", "away")) - 1.0) < 1e-6


def test_model_champion_physical_is_identity():
    asks = {"home": 55, "draw": 26, "away": 19}
    common = dict(instance_id="i1", brokerage_id="b1", ts="t", tier="medium", caps=CAPS,
                  fee_rate=0.07, edge_threshold=0.01, reserve_frac=0.0,
                  expected_better_soon=False, market_shrink=0.0)
    a = plan_and_allocate([_fixture_3way("f1", 2.4, 0.4, asks)], **common)
    b = plan_and_allocate([_fixture_3way("f1", 2.4, 0.4, asks)], **common,
                          model_champion="physical", model_probs_fn=lambda fx: {"home": 0.9})
    fa = {d.get("side"): d["fused_fair"] for d in a["decisions"]}
    fb = {d.get("side"): d["fused_fair"] for d in b["decisions"]}
    assert fa == fb


def test_model_champion_respects_cheap_side_cap():
    """The override routes through fuse(), so a champion model that's overconfident on
    a CHEAP side (sharp < 20c) can't run far above the sharp prob (the failure mode
    a hand-rolled blend would reintroduce)."""
    # sharp says home is a 10c longshot; the model champion loves home at 40%.
    fx = _fixture_3way("f1", 1.0, 1.0, {"home": 62, "draw": 26, "away": 12},
                       sharp={"home": 0.10, "draw": 0.25, "away": 0.65})
    common = dict(instance_id="i1", brokerage_id="b1", ts="t", tier="medium", caps=CAPS,
                  fee_rate=0.07, edge_threshold=0.01, reserve_frac=0.0,
                  expected_better_soon=False, market_shrink=0.0, w_sharp=0.7)
    out = plan_and_allocate([fx], **common, model_champion="ensemble",
                            model_probs_fn=lambda f: {"home": 0.40, "draw": 0.20, "away": 0.40})
    home = next(d["fused_fair"] for d in out["decisions"] if d.get("side") == "home")
    # cheap_side_cap(0.10)=0.0 -> home may not exceed ~sharp(0.10) by more than rounding
    assert home <= 0.14   # capped near sharp, NOT ~0.19 (the uncapped hand-rolled value)
