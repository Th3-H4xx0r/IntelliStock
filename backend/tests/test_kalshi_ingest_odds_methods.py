"""Tests for OddsPapi fixtures + historical-odds methods & pure parsers."""
import os
import sys

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from kalshi.ingest_odds import OddsPapiClient, parse_fixtures, parse_hist_odds


# --- parse_fixtures ---

def test_parse_fixtures_settled_home_win_derives_result():
    raw = {"fixtures": [
        {"id": 1, "home": "England", "away": "DR Congo", "kickoff_ts": 1780000000,
         "status": "finished", "home_score": 2, "away_score": 0},
    ]}
    out = parse_fixtures(raw)
    assert len(out) == 1
    f = out[0]
    assert f == {
        "fixture_id": "1", "home": "England", "away": "DR Congo",
        "kickoff_ts": 1780000000, "home_score": 2, "away_score": 0,
        "result": "home", "settled": True,
    }


def test_parse_fixtures_settled_away_win_and_draw():
    raw = {"fixtures": [
        {"id": 2, "home": "A", "away": "B", "kickoff_ts": 1, "status": "finished",
         "home_score": 0, "away_score": 1},
        {"id": 3, "home": "A", "away": "B", "kickoff_ts": 2, "status": "finished",
         "home_score": 1, "away_score": 1},
    ]}
    out = parse_fixtures(raw)
    assert out[0]["result"] == "away"
    assert out[1]["result"] == "draw"


def test_parse_fixtures_unsettled_has_no_result():
    raw = {"fixtures": [
        {"id": 4, "home": "A", "away": "B", "kickoff_ts": 3, "status": "scheduled"},
    ]}
    out = parse_fixtures(raw)
    assert out[0]["settled"] is False
    assert out[0]["result"] is None
    assert out[0]["home_score"] is None and out[0]["away_score"] is None


def test_parse_fixtures_accepts_iso_kickoff():
    raw = {"fixtures": [
        {"id": 5, "home": "A", "away": "B", "kickoff": "2026-07-01T18:00:00Z", "status": "scheduled"},
    ]}
    out = parse_fixtures(raw)
    assert out[0]["kickoff_ts"] == 1782928800


def test_parse_fixtures_empty_safe():
    assert parse_fixtures(None) == []
    assert parse_fixtures({}) == []
    assert parse_fixtures({"fixtures": []}) == []


# --- parse_hist_odds ---

def test_parse_hist_odds_sorted_by_ts():
    raw = {"odds": [
        {"ts": 200, "books": {"pinnacle": {"home": 1.9, "draw": 3.4, "away": 4.1}}},
        {"ts": 100, "books": {"pinnacle": {"home": 2.0, "draw": 3.5, "away": 4.0}}},
    ]}
    out = parse_hist_odds(raw)
    assert [s["ts"] for s in out] == [100, 200]
    assert out[0] == {"ts": 100, "home": 2.0, "draw": 3.5, "away": 4.0}


def test_parse_hist_odds_falls_back_through_book_priority():
    raw = {"odds": [
        {"ts": 100, "books": {"betfair": {"home": 2.1, "draw": 3.6, "away": 3.9}}},
    ]}
    out = parse_hist_odds(raw, books=("pinnacle", "betfair"))
    assert out == [{"ts": 100, "home": 2.1, "draw": 3.6, "away": 3.9}]


def test_parse_hist_odds_skips_snapshot_missing_all_requested_books():
    raw = {"odds": [
        {"ts": 100, "books": {"onexbet": {"home": 2.1, "draw": 3.6, "away": 3.9}}},
    ]}
    assert parse_hist_odds(raw, books=("pinnacle",)) == []


def test_parse_hist_odds_empty_safe():
    assert parse_hist_odds(None) == []
    assert parse_hist_odds({}) == []
    assert parse_hist_odds({"odds": []}) == []


# --- thin client methods over fetch_raw ---

class FakeResp:
    def __init__(self, payload):
        self._payload = payload
        self.content = b"{}"

    def json(self):
        return self._payload

    def raise_for_status(self):
        pass


class FakeSession:
    def __init__(self, payload):
        self._payload = payload
        self.last_call = None

    def request(self, method, url, params=None, timeout=None, headers=None):
        self.last_call = (method, url, params)
        self.last_headers = headers
        return FakeResp(self._payload)


def test_list_fixtures_calls_fetch_raw_with_expected_params():
    payload = {"fixtures": [{"id": 9, "home": "A", "away": "B", "kickoff_ts": 5,
                              "status": "scheduled"}]}
    sess = FakeSession(payload)
    client = OddsPapiClient(api_key="KEY", session=sess)
    out = client.list_fixtures(sport_id=1, date_from="2026-07-01", date_to="2026-07-02")
    method, url, params = sess.last_call
    assert method == "GET"
    assert url.endswith("/v4/fixtures")
    assert params["sportId"] == 1
    assert params["from"] == "2026-07-01"
    assert params["to"] == "2026-07-02"
    assert params["apiKey"] == "KEY"
    assert out[0]["fixture_id"] == "9"


def test_historical_odds_calls_fetch_raw_with_expected_params():
    payload = {"odds": [{"ts": 100, "books": {"pinnacle": {"home": 2.0, "draw": 3.5, "away": 4.0}}}]}
    sess = FakeSession(payload)
    client = OddsPapiClient(api_key="KEY", session=sess)
    out = client.historical_odds(fixture_id=9, books=("pinnacle",))
    method, url, params = sess.last_call
    assert method == "GET"
    assert url.endswith("/v4/historical-odds")
    assert params["fixtureId"] == 9
    assert params["apiKey"] == "KEY"
    assert out == [{"ts": 100, "home": 2.0, "draw": 3.5, "away": 4.0}]
