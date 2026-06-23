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
