from __future__ import annotations


def test_symbol_search_returns_supported_instruments_with_display_names():
    """Dropping a valid asset class or its name makes mobile discovery incomplete."""
    from api.main import search_symbol_instruments

    def provider(_query: str):
        return {
            "quotes": [
                {
                    "symbol": "AAPL",
                    "quoteType": "EQUITY",
                    "longname": "Apple Inc.",
                },
                {
                    "symbol": "SPY",
                    "quoteType": "ETF",
                    "shortname": "SPDR S&P 500 ETF Trust",
                },
                {
                    "symbol": "BTC-USD",
                    "quoteType": "CRYPTOCURRENCY",
                    "shortname": "Bitcoin USD",
                },
                {
                    "symbol": "ES=F",
                    "quoteType": "FUTURE",
                    "shortname": "E-mini S&P 500",
                },
            ]
        }

    assert search_symbol_instruments("bit", provider=provider) == [
        {"symbol": "AAPL", "name": "Apple Inc.", "type": "Stock"},
        {
            "symbol": "SPY",
            "name": "SPDR S&P 500 ETF Trust",
            "type": "ETF",
        },
        {"symbol": "BTC-USD", "name": "Bitcoin USD", "type": "Crypto"},
    ]


def test_brokerage_positions_normalizes_direct_alpaca_response():
    """Removing the direct-account fallback would hide holdings without an instance."""
    from api.main import normalize_alpaca_positions

    assert normalize_alpaca_positions([
        {
            "symbol": "AAPL",
            "qty": "2",
            "avg_entry_price": "180",
            "current_price": "200",
            "market_value": "400",
            "unrealized_pl": "40",
            "unrealized_plpc": "0.1111",
        },
    ]) == [{
        "symbol": "AAPL",
        "qty": 2.0,
        "avgEntryPrice": 180.0,
        "lastPrice": 200.0,
        "marketValue": 400.0,
        "unrealizedPnl": 40.0,
        "unrealizedPnlPct": 11.11,
    }]
