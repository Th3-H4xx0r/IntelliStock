from kalshi.data.discovery import (
    DEFAULT_SOCCER_SERIES,
    extract_teams,
    parse_kalshi_market,
    group_by_event,
)


def test_world_cup_series_is_discovered():
    # World Cup series ticker (KXWCGAME) must be in the default scan set.
    assert "KXWCGAME" in DEFAULT_SOCCER_SERIES


def test_parse_world_cup_winner_sides_home_away_draw():
    # Real Kalshi World Cup shape: Argentina vs Austria with Argentina/Austria/Tie.
    base = {"event_ticker": "KXWCGAME-26JUN22ARGAUT", "title": "Argentina vs Austria", "yes_ask": 60}
    home = parse_kalshi_market({**base, "ticker": "KXWCGAME-...-ARG", "yes_sub_title": "Argentina"})
    away = parse_kalshi_market({**base, "ticker": "KXWCGAME-...-AUT", "yes_sub_title": "Austria"})
    tie = parse_kalshi_market({**base, "ticker": "KXWCGAME-...-TIE", "yes_sub_title": "Tie"})
    assert (home["market_type"], home["side"]) == ("winner", "home")
    assert (away["market_type"], away["side"]) == ("winner", "away")
    assert (tie["market_type"], tie["side"]) == ("winner", "draw")


def test_extract_teams_common_formats():
    assert extract_teams("Arsenal vs Chelsea") == ("Arsenal", "Chelsea")
    assert extract_teams("Arsenal v Chelsea?") == ("Arsenal", "Chelsea")
    assert extract_teams("Leeds at Everton") == ("Leeds", "Everton")
    assert extract_teams("no teams here") == (None, None)


def test_extract_teams_strips_market_suffix_from_away():
    # Kalshi titles append the market label after the away team — it must not
    # contaminate the team name (otherwise the Elo/national lookup misses).
    assert extract_teams("Argentina vs Austria Winner") == ("Argentina", "Austria")
    assert extract_teams("France vs Iraq Total Goals") == ("France", "Iraq")
    assert extract_teams("Japan vs Sweden Both Teams To Score") == ("Japan", "Sweden")
    # Multi-word names with no trailing market word are preserved.
    assert extract_teams("Bosnia and Herzegovina vs Qatar") == ("Bosnia and Herzegovina", "Qatar")


def test_parse_world_cup_away_side_maps_after_suffix_strip():
    # With the suffix stripped, the away team's yes_sub_title now matches -> "away".
    p = parse_kalshi_market({
        "ticker": "KXWCGAME-...-AUT", "event_ticker": "KXWCGAME-26JUN22ARGAUT",
        "title": "Argentina vs Austria Winner", "yes_sub_title": "Austria", "yes_ask": 40,
    })
    assert p["home"] == "Argentina" and p["away"] == "Austria"
    assert (p["market_type"], p["side"]) == ("winner", "away")


def test_parse_kalshi_market():
    p = parse_kalshi_market({
        "ticker": "KXEPLGAME-ARSCHE-ARS", "event_ticker": "KXEPLGAME-ARSCHE",
        "title": "Arsenal vs Chelsea", "yes_sub_title": "Arsenal", "yes_ask": 52,
    })
    assert p["market_type"] == "winner"
    assert p["home"] == "Arsenal" and p["away"] == "Chelsea"
    assert p["yes_ask_cents"] == 52 and p["event_ticker"] == "KXEPLGAME-ARSCHE"


def test_group_by_event():
    g = group_by_event([
        {"event_ticker": "E1", "market_ticker": "M1"},
        {"event_ticker": "E1", "market_ticker": "M2"},
        {"event_ticker": "E2", "market_ticker": "M3"},
    ])
    assert len(g["E1"]) == 2 and len(g["E2"]) == 1
