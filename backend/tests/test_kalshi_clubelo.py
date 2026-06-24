from kalshi.data.sources.clubelo import parse_clubelo_csv, elo_for

CSV = """Rank,Club,Country,Level,Elo,From,To
1,Man City,ENG,1,2050.5,2026-06-20,2026-06-27
2,Arsenal,ENG,1,1980.2,2026-06-20,2026-06-27
3,Chelsea,ENG,1,1875.0,2026-06-20,2026-06-27
"""


def test_parse_and_lookup_with_crosswalk():
    table = parse_clubelo_csv(CSV)
    # "Man City" normalizes to "Manchester City"
    assert elo_for(table, "Man City") == 2050.5
    assert elo_for(table, "Manchester City") == 2050.5
    assert elo_for(table, "Arsenal") == 1980.2


def test_unknown_team_default():
    table = parse_clubelo_csv(CSV)
    assert elo_for(table, "Nonexistent FC") == 1500.0


def test_empty_csv():
    assert parse_clubelo_csv("") == {}
