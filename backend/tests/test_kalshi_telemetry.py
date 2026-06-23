from kalshi.models import EdgeFlag, KalshiContractPosition
from kalshi.telemetry import edge_radar_payload, portfolio_series, settlement_items


def _flag(ticker, edge):
    return EdgeFlag("f1", ticker, "home", 0.55, 0.50, 0.01, edge)


def test_edge_radar_sorted_and_limited():
    flags = [_flag("A", 0.02), _flag("B", 0.05), _flag("C", 0.03)]
    out = edge_radar_payload(flags, limit=2)
    assert [r["market_ticker"] for r in out] == ["B", "C"]
    assert out[0]["edge"] == 0.05


def test_portfolio_series_converts_cents_to_dollars():
    snaps = [{"ts": "t1", "value_cents": 482014}, {"ts": "t2", "value_cents": 488314}]
    out = portfolio_series(snaps)
    assert out == [{"ts": "t1", "value": 4820.14}, {"ts": "t2", "value": 4883.14}]


def test_portfolio_series_downsamples_but_keeps_endpoints():
    snaps = [{"ts": str(i), "value_cents": i * 100} for i in range(1000)]
    out = portfolio_series(snaps, max_points=50)
    assert len(out) <= 52  # max_points plus guaranteed first/last
    assert out[0]["ts"] == "0"
    assert out[-1]["ts"] == "999"


def test_settlement_items_unrealized_pnl():
    pos = [
        KalshiContractPosition("KX-A", "YES", 40, 52.0, current_price_cents=64.0),
        KalshiContractPosition("KX-B", "NO", 25, 58.0, current_price_cents=None),
    ]
    out = settlement_items(pos)
    assert out[0]["unrealized_cents"] == (64.0 - 52.0) * 40
    assert out[1]["unrealized_cents"] is None
