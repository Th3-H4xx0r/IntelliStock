"""Tests for Kalshi candlesticks parsing (public data, no auth required)."""
import os
import sys

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from kalshi.client import parse_candlesticks, yes_ask_close_at, KalshiClient

RAW = {"candlesticks": [
    {"end_period_ts": 1000, "yes_bid": {"close_dollars": "0.40"}, "yes_ask": {"close_dollars": "0.44"}, "price": {}, "volume_fp": "5.00", "open_interest_fp": "10.00"},
    {"end_period_ts": 2000, "yes_bid": {"close_dollars": "0.46"}, "yes_ask": {"close_dollars": "0.50"}, "price": {"close_dollars": "0.48"}, "volume_fp": "3.00", "open_interest_fp": "12.00"},
]}


def test_parse_candlesticks_extracts_rows():
    rows = parse_candlesticks(RAW)
    assert len(rows) == 2 and rows[0]["end_period_ts"] == 1000


def test_yes_ask_close_at_returns_cents_of_last_on_or_before():
    rows = parse_candlesticks(RAW)
    assert yes_ask_close_at(rows, 1500) == 44   # 0.44 dollars -> 44c, last <= 1500
    assert yes_ask_close_at(rows, 2500) == 50
    assert yes_ask_close_at(rows, 500) is None   # nothing before


def test_yes_ask_close_at_handles_missing_book():
    assert yes_ask_close_at([{"end_period_ts": 1, "yes_ask": {}}], 5) is None


def test_parse_candlesticks_empty_is_safe():
    assert parse_candlesticks({}) == []


def test_get_candlesticks_fetches_and_parses():
    """Fake session test: verify URL, params, parsing."""
    class FakeResp:
        content = b'{"candlesticks": [...]}'
        def json(self):
            return RAW
        def raise_for_status(self):
            pass

    class FakeSession:
        def __init__(self):
            self.last_call = None
        def request(self, method, url, headers=None, params=None, timeout=None):
            self.last_call = (method, url, headers, params)
            return FakeResp()

    session = FakeSession()
    client = KalshiClient(
        key_id="test",
        private_key_pem="test",
        session=session,
    )

    result = client.get_candlesticks("KXWCGAME-26JUN30MEXECU", 1000, 2000, period_interval=60)

    # Verify URL and params
    method, url, headers, params = session.last_call
    assert method == "GET"
    assert "external-api.kalshi.com" in url
    assert "/series/KXWCGAME/" in url  # series derived from ticker
    assert "KXWCGAME-26JUN30MEXECU" in url  # ticker in URL
    assert params == {"start_ts": 1000, "end_ts": 2000, "period_interval": 60}
    assert headers["User-Agent"] == "Mozilla/5.0 AppleWebKit/537.36 Chrome/126"

    # Verify parsing
    assert len(result) == 2
    assert result[0]["end_period_ts"] == 1000
