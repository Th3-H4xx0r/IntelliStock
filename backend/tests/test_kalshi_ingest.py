from kalshi.ingest_odds import (
    budget_remaining, should_scan, fixtures_per_day_budget, parse_three_way,
)
from kalshi.ingest_fixtures import normalize_fixture


def test_budget_remaining_and_should_scan():
    assert budget_remaining(used=168, limit=250) == 82
    assert should_scan(used=168, cost=80) is True
    assert should_scan(used=168, cost=90) is False   # would overspend
    assert should_scan(used=250, cost=1) is False


def test_fixtures_per_day_throttle():
    # 82 left over 10 days -> 8/day
    assert fixtures_per_day_budget(used=168, days_left_in_month=10) == 8
    assert fixtures_per_day_budget(used=250, days_left_in_month=10) == 0
    assert fixtures_per_day_budget(used=0, days_left_in_month=0) == 0


def test_parse_three_way_pinnacle():
    raw = {"books": {"pinnacle": {"home": 2.0, "draw": 3.5, "away": 4.0}}}
    q = parse_three_way(raw, book="pinnacle")
    assert q is not None and q.home == 2.0 and q.away == 4.0


def test_parse_three_way_missing_book():
    assert parse_three_way({"books": {}}, book="pinnacle") is None
    assert parse_three_way({"books": {"pinnacle": {"home": 2.0}}}) is None  # missing draw/away


def test_normalize_fixture_crosswalks_team_names():
    raw = {
        "id": 12345,
        "competition": {"name": "Premier League"},
        "homeTeam": {"name": "Man Utd"},
        "awayTeam": {"name": "Spurs"},
        "utcDate": "2026-06-22T14:00:00Z",
    }
    f = normalize_fixture(raw)
    assert f.fixture_id == "12345"
    assert f.home == "Manchester United"
    assert f.away == "Tottenham Hotspur"
    assert f.league == "Premier League"
