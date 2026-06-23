from kalshi.models import KalshiMarket, EdgeFlag
from kalshi.edge import implied_from_cents, compute_edge, flag_edges


def test_implied_from_cents():
    assert abs(implied_from_cents(48) - 0.48) < 1e-9


def test_compute_edge_subtracts_fee():
    # fair 0.53, ask 48c -> raw edge 0.05; fee 0.01 -> net 0.04
    e = compute_edge(fair_prob=0.53, yes_ask_cents=48, fee=0.01)
    assert abs(e - 0.04) < 1e-9


def test_flag_edges_filters_below_threshold_and_sorts():
    fair = {"home": 0.53, "draw": 0.25, "away": 0.22}
    markets = [
        KalshiMarket("KX-HOME", "f1", "home", 48),  # net edge +0.04
        KalshiMarket("KX-AWAY", "f1", "away", 21),  # net edge ~0.00 -> filtered
        KalshiMarket("KX-DRAW", "f1", "draw", 19),  # net edge +0.05
    ]
    flags = flag_edges("f1", fair, markets, fee=0.01, threshold=0.03)
    assert [f.market_ticker for f in flags] == ["KX-DRAW", "KX-HOME"]
    assert isinstance(flags[0], EdgeFlag)
    assert flags[0].edge > flags[1].edge


def test_flag_edges_skips_unknown_side():
    fair = {"home": 0.5, "draw": 0.3, "away": 0.2}
    markets = [KalshiMarket("KX-OU", "f1", "over", 40)]
    assert flag_edges("f1", fair, markets, fee=0.0, threshold=0.0) == []
