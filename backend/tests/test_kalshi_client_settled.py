"""Tests for Kalshi settled markets (public data, no auth required)."""
import os
import sys

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from kalshi.client import result_side_from_markets, KalshiClient


def test_result_side_from_markets_maps_yes_side_to_home():
    """Fixture MEX (home) vs ECU (away). -MEX suffix resolves yes -> 'home'."""
    markets = [
        {"ticker": "KXWCGAME-26JUN30MEXECU-MEX", "result": "yes"},   # home wins
        {"ticker": "KXWCGAME-26JUN30MEXECU-ECU", "result": "no"},    # away loses
        {"ticker": "KXWCGAME-26JUN30MEXECU-TIE", "result": "no"},    # draw loses
    ]
    assert result_side_from_markets(markets, "KXWCGAME-26JUN30MEXECU") == "home"


def test_result_side_from_markets_maps_away_side():
    """Fixture MEX (home) vs ECU (away). -ECU suffix resolves yes -> 'away'."""
    markets = [
        {"ticker": "KXWCGAME-26JUN30MEXECU-MEX", "result": "no"},
        {"ticker": "KXWCGAME-26JUN30MEXECU-ECU", "result": "yes"},   # away wins
        {"ticker": "KXWCGAME-26JUN30MEXECU-TIE", "result": "no"},
    ]
    assert result_side_from_markets(markets, "KXWCGAME-26JUN30MEXECU") == "away"


def test_result_side_from_markets_maps_tie_side():
    """Fixture MEX vs ECU. -TIE suffix resolves yes -> 'draw'."""
    markets = [
        {"ticker": "KXWCGAME-26JUN30MEXECU-MEX", "result": "no"},
        {"ticker": "KXWCGAME-26JUN30MEXECU-ECU", "result": "no"},
        {"ticker": "KXWCGAME-26JUN30MEXECU-TIE", "result": "yes"},   # draw happens
    ]
    assert result_side_from_markets(markets, "KXWCGAME-26JUN30MEXECU") == "draw"


def test_result_side_from_markets_returns_none_if_no_yes():
    """No yes result found."""
    markets = [
        {"ticker": "KXWCGAME-26JUN30MEXECU-MEX", "result": "no"},
        {"ticker": "KXWCGAME-26JUN30MEXECU-ECU", "result": "no"},
        {"ticker": "KXWCGAME-26JUN30MEXECU-TIE", "result": "no"},
    ]
    assert result_side_from_markets(markets, "KXWCGAME-26JUN30MEXECU") is None


def test_result_side_from_markets_returns_none_if_empty():
    """Empty markets list."""
    assert result_side_from_markets([], "KXWCGAME-26JUN30MEXECU") is None


def test_list_settled_markets_fetches_and_paginates():
    """Fake session test: verify URL, params, pagination via cursor."""
    class FakeResp:
        def __init__(self, markets, cursor=None):
            self._markets = markets
            self._cursor = cursor
            self.content = b"..."

        def json(self):
            return {"markets": self._markets, "cursor": self._cursor}

        def raise_for_status(self):
            pass

    class FakeSession:
        def __init__(self):
            self.calls = []

        def request(self, method, url, headers=None, params=None, timeout=None):
            self.calls.append((method, url, params))
            # First page
            if not params or not params.get("cursor"):
                return FakeResp(
                    [{"ticker": "KXWCGAME-26JUN30MEXECU-MEX", "result": "yes"}],
                    cursor="page2"
                )
            # Second page (final)
            return FakeResp(
                [{"ticker": "KXWCGAME-26JUN30MEXECU-ECU", "result": "no"}],
                cursor=None
            )

    session = FakeSession()
    client = KalshiClient(
        key_id="test",
        private_key_pem="test",
        session=session,
    )

    result = client.list_settled_markets("KXWCGAME", limit=50)

    # Verify pagination
    assert len(session.calls) == 2
    assert len(result) == 2  # both pages combined

    # Verify first call URL and params
    method, url, params = session.calls[0]
    assert method == "GET"
    assert "external-api.kalshi.com" in url
    assert params["series_ticker"] == "KXWCGAME"
    assert params["status"] == "settled"
    assert params["limit"] == 50

    # Verify second call includes cursor
    method, url, params = session.calls[1]
    assert params["cursor"] == "page2"

    # Verify results are combined
    assert result[0]["ticker"] == "KXWCGAME-26JUN30MEXECU-MEX"
    assert result[1]["ticker"] == "KXWCGAME-26JUN30MEXECU-ECU"
