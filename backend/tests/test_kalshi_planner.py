from kalshi.risk import RiskCaps
from kalshi.capital.planner import allocate

CAPS = RiskCaps(max_contracts_per_market=50, bankroll_cents=100000)


def test_kelly_fraction_and_min_stake_scale_bet_size():
    # A thin 2% edge: hardcoded quarter-Kelly would be dust (~$1 on $95). The tier's
    # kelly_fraction + min_stake_frac floor make it a meaningful bet.
    caps = RiskCaps(kelly_fraction=0.4, min_stake_frac=0.12, max_contracts_per_market=50, bankroll_cents=9500)
    cands = [{"id": "x", "score": 1.0, "edge": 0.022, "price_cents": 52}]
    a = allocate(cands, bankroll_cents=9500, caps=caps, reserve_frac=0.3, expected_better_soon=False)[0]
    assert a["stake_cents"] >= 1000          # >= ~$10 (12% floor of $95), not dust
    # Max tier stakes more than high tier on the same candidate.
    caps_max = RiskCaps(kelly_fraction=0.6, min_stake_frac=0.20, max_contracts_per_market=200, bankroll_cents=9500)
    a_max = allocate(cands, bankroll_cents=9500, caps=caps_max, reserve_frac=0.3, expected_better_soon=False)[0]
    assert a_max["stake_cents"] > a["stake_cents"]


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


def test_model_only_size_haircut():
    # A model-only (no-sharp) candidate is sized SMALLER than an identical sharp-anchored
    # one, so a wrong model-only favorite call costs less.
    caps = RiskCaps(kelly_fraction=0.25, max_contracts_per_market=100000,
                    bankroll_cents=100000, model_only_size_mult=0.5)
    sharp_c = [{"id": "s", "score": 1.0, "edge": 0.10, "price_cents": 50, "has_sharp": True}]
    model_c = [{"id": "m", "score": 1.0, "edge": 0.10, "price_cents": 50, "has_sharp": False}]
    a_sharp = allocate(sharp_c, bankroll_cents=100000, caps=caps, reserve_frac=0.0, expected_better_soon=False)[0]
    a_model = allocate(model_c, bankroll_cents=100000, caps=caps, reserve_frac=0.0, expected_better_soon=False)[0]
    assert a_model["contracts"] == a_sharp["contracts"] // 2     # haircut to ~half


def test_model_only_haircut_off_by_default():
    caps = RiskCaps(kelly_fraction=0.25, max_contracts_per_market=100000, bankroll_cents=100000)  # mult defaults 1.0
    sharp_c = [{"id": "s", "score": 1.0, "edge": 0.10, "price_cents": 50, "has_sharp": True}]
    model_c = [{"id": "m", "score": 1.0, "edge": 0.10, "price_cents": 50, "has_sharp": False}]
    a_sharp = allocate(sharp_c, bankroll_cents=100000, caps=caps, reserve_frac=0.0, expected_better_soon=False)[0]
    a_model = allocate(model_c, bankroll_cents=100000, caps=caps, reserve_frac=0.0, expected_better_soon=False)[0]
    assert a_model["contracts"] == a_sharp["contracts"]          # no haircut when mult=1.0
