from kalshi.api_payloads import (
    portfolio_payload, edges_payload, positions_payload, clv_payload, scan_budget_payload,
)


def test_portfolio_payload_series_and_day_change():
    snaps = [{"ts": "t1", "value_cents": 480000}, {"ts": "t2", "value_cents": 482014}]
    p = portfolio_payload(snaps, value_cents=482014, cash_cents=318000)
    assert p["value"] == 4820.14
    assert p["cash"] == 3180.0
    assert p["day_change"] == round(4820.14 - 4800.0, 2)
    assert len(p["series"]) == 2


def test_portfolio_payload_empty():
    p = portfolio_payload([], value_cents=0, cash_cents=0)
    assert p["value"] == 0.0 and p["day_change"] == 0.0 and p["series"] == []


def test_portfolio_payload_day_change_from_24h_baseline():
    # day_change must be value - prev_value (24h baseline), NOT the whole-window
    # delta. Here the window spans weeks but the 24h baseline is 481000.
    snaps = [{"ts": "t0", "value_cents": 400000}, {"ts": "t1", "value_cents": 481000}]
    p = portfolio_payload(snaps, value_cents=482014, cash_cents=0, prev_value_cents=481000)
    assert p["day_change"] == round((482014 - 481000) / 100.0, 2)  # 10.14, not 820.14


def test_edges_payload_sorted_limited():
    rows = [
        {"market_ticker": "A", "edge": 0.02},
        {"market_ticker": "B", "edge": 0.05},
        {"market_ticker": "C", "edge": 0.03},
    ]
    out = edges_payload(rows, limit=2)
    assert [e["market_ticker"] for e in out["edges"]] == ["B", "C"]
    assert out["count"] == 2


def test_positions_payload_unrealized():
    rows = [{"market_ticker": "A", "side": "YES", "contracts": 40,
             "avg_price_cents": 52.0, "current_price_cents": 64.0}]
    out = positions_payload(rows)
    assert out["positions"][0]["unrealized_cents"] == (64.0 - 52.0) * 40


def test_clv_payload():
    out = clv_payload([{"league": "EPL", "clv": 0.02}, {"league": "EPL", "clv": 0.04}])
    assert out["by_league"]["EPL"]["n"] == 2


def test_scan_budget_payload():
    out = scan_budget_payload(168, days_left_in_month=10, limit=250)
    assert out["remaining"] == 82 and out["fixtures_per_day"] == 8
