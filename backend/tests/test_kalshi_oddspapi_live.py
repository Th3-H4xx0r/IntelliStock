"""OddsPapi fallback live-sharp adapter: normalizes to odds_api's event shape
and degrades safely. Uses a fake client (no network)."""
import sys, types
sys.modules.setdefault("socketio", types.ModuleType("socketio"))

from kalshi.data.sources import oddspapi_live, odds_api


class FakeOddsPapiClient:
    def __init__(self, fixtures, snaps_by_id):
        self._fixtures = fixtures
        self._snaps = snaps_by_id
        self.calls = []

    def list_fixtures(self, sport_id, date_from, date_to):
        self.calls.append(("list", sport_id, date_from, date_to))
        return self._fixtures

    def historical_odds(self, fixture_id):
        return self._snaps.get(fixture_id, [])


def test_fetch_events_normalizes_priced_fixtures():
    client = FakeOddsPapiClient(
        fixtures=[
            {"fixture_id": "f1", "home": "Spain", "away": "Austria", "has_odds": True},
            {"fixture_id": "f2", "home": "Brazil", "away": "Norway", "has_odds": False},  # no odds -> skip
        ],
        snaps_by_id={"f1": [
            {"ts": 1, "home": 1.5, "draw": 4.0, "away": 7.0},
            {"ts": 2, "home": 1.6, "draw": 3.9, "away": 6.5},  # latest snapshot is used
        ]},
    )
    evs = oddspapi_live.fetch_events("k", "2026-06-01", "2026-06-05", client=client)
    assert len(evs) == 1
    ev = evs[0]
    assert ev["home"] == "Spain" and ev["away"] == "Austria"
    assert ev["books"]["oddspapi"] == {"home": 1.6, "draw": 3.9, "away": 6.5}
    # The normalized event must be consumable by the existing sharp path.
    q = odds_api.quote_for_event(ev)
    assert q is not None and q.home == 1.6


def test_empty_key_returns_empty():
    assert oddspapi_live.fetch_events("", "2026-06-01", "2026-06-05") == []


def test_no_odds_or_no_snaps_degrades_to_empty():
    client = FakeOddsPapiClient(
        fixtures=[{"fixture_id": "f1", "home": "A", "away": "B", "has_odds": True}],
        snaps_by_id={},  # priced flag but no snapshots
    )
    assert oddspapi_live.fetch_events("k", "2026-06-01", "2026-06-05", client=client) == []


def test_invalid_decimal_odds_skipped():
    client = FakeOddsPapiClient(
        fixtures=[{"fixture_id": "f1", "home": "A", "away": "B", "has_odds": True}],
        snaps_by_id={"f1": [{"ts": 1, "home": 1.0, "draw": 3.0, "away": 5.0}]},  # 1.0 <= 1.0 invalid
    )
    assert oddspapi_live.fetch_events("k", "2026-06-01", "2026-06-05", client=client) == []
