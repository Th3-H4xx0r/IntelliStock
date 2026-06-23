from kalshi.models import KalshiMarket
from kalshi.risk import RiskCaps
from kalshi.replay import replay_fixtures, LIMITATIONS


def test_replay_computes_hypothetical_orders_and_clv():
    caps = RiskCaps(edge_threshold=0.03, max_contracts_per_market=50, bankroll_cents=100000)
    records = [
        {
            "fixture_id": "f1",
            "league": "EPL",
            "fair": {"home": 0.55, "draw": 0.27, "away": 0.20},
            "markets": [KalshiMarket("KX-HOME", "f1", "home", 48)],  # edge -> order @48c
            "close_cents": {"KX-HOME": 52},                          # closed higher -> +CLV
        }
    ]
    out = replay_fixtures(records, caps=caps, fee_rate=0.07)
    assert out["n_orders"] == 1
    order = out["orders"][0]
    assert order["market_ticker"] == "KX-HOME"
    assert order["entry_cents"] == 48 and order["close_cents"] == 52
    assert abs(order["clv"] - 0.04) < 1e-9
    assert out["clv"]["overall"]["n"] == 1
    assert out["limitations"] == LIMITATIONS


def test_replay_honors_missing_close():
    caps = RiskCaps(edge_threshold=0.03, max_contracts_per_market=50, bankroll_cents=100000)
    records = [{
        "fixture_id": "f2", "league": "Serie B",
        "fair": {"home": 0.55, "draw": 0.27, "away": 0.20},
        "markets": [KalshiMarket("KX-HOME", "f2", "home", 48)],
        "close_cents": {},
    }]
    out = replay_fixtures(records, caps=caps, fee_rate=0.07)
    assert out["orders"][0]["clv"] is None
    assert out["clv"]["overall"]["n"] == 0  # no CLV row without a close
