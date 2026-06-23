from kalshi.risk import RiskCaps
from kalshi.capital.planner import allocate

CAPS = RiskCaps(max_contracts_per_market=50, bankroll_cents=100000)


def test_holds_reserve_for_better_future_opp():
    cands = [{"id": "now", "score": 0.3, "edge": 0.04, "price_cents": 50}]
    allocs = allocate(cands, bankroll_cents=100000, caps=CAPS, reserve_frac=0.4, expected_better_soon=True)
    spent = sum(a["stake_cents"] for a in allocs)
    assert spent <= 60000  # 40% reserve held when a better opp is expected


def test_deploys_when_no_better_soon():
    cands = [{"id": "now", "score": 0.9, "edge": 0.08, "price_cents": 50}]
    allocs = allocate(cands, bankroll_cents=100000, caps=CAPS, reserve_frac=0.4, expected_better_soon=False)
    assert sum(a["stake_cents"] for a in allocs) > 0


def test_respects_per_bet_kelly_and_market_cap():
    cands = [{"id": "x", "score": 0.9, "edge": 0.5, "price_cents": 50}]  # huge edge
    allocs = allocate(cands, bankroll_cents=100000, caps=CAPS, reserve_frac=0.0, expected_better_soon=False)
    a = allocs[0]
    assert a["contracts"] <= 50                 # per-market cap
    # cost never exceeds the quarter-Kelly stake of the deployable bankroll
    from kalshi.risk import quarter_kelly_fraction
    max_stake = int(quarter_kelly_fraction(edge=0.5, price_cents=50) * 100000)
    assert a["stake_cents"] <= max_stake


def test_higher_score_funded_first():
    cands = [
        {"id": "lo", "score": 0.2, "edge": 0.04, "price_cents": 50},
        {"id": "hi", "score": 0.9, "edge": 0.04, "price_cents": 50},
    ]
    allocs = allocate(cands, bankroll_cents=100000, caps=CAPS, reserve_frac=0.0, expected_better_soon=False)
    assert allocs[0]["id"] == "hi"
