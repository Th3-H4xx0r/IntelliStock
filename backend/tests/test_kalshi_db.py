from kalshi.db import (
    KALSHI_TABLES,
    portfolio_snapshot_doc,
    edge_doc,
    scan_budget_window,
)


def test_tables_cover_the_design():
    names = {t[0] for t in KALSHI_TABLES}
    for required in [
        "sports_fixtures", "kalshi_markets", "kalshi_odds_snapshots",
        "kalshi_edges", "kalshi_orders", "kalshi_positions",
        "kalshi_portfolio_snapshots", "kalshi_clv_log", "team_stats",
        "kalshi_scan_budget",
    ]:
        assert required in names


def test_portfolio_snapshot_doc_id_is_scoped():
    d = portfolio_snapshot_doc(brokerage_id="kal-live", ts="2026-06-22T12:00:00Z",
                               value_cents=482014, cash_cents=318000)
    assert d["id"] == "kal-live|2026-06-22T12:00:00Z"
    assert d["value_cents"] == 482014


def test_edge_doc_merges_flag():
    d = edge_doc(brokerage_id="b1", ts="t", flag={"market_ticker": "KX-A", "edge": 0.05})
    assert d["id"] == "b1|KX-A|t"
    assert d["edge"] == 0.05


def test_scan_budget_window_is_month():
    assert scan_budget_window("2026-06-22T12:00:00Z") == "2026-06"
