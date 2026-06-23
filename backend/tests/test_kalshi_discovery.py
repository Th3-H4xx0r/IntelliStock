from kalshi.data.discovery import extract_teams, parse_kalshi_market, group_by_event


def test_extract_teams_common_formats():
    assert extract_teams("Arsenal vs Chelsea") == ("Arsenal", "Chelsea")
    assert extract_teams("Arsenal v Chelsea?") == ("Arsenal", "Chelsea")
    assert extract_teams("Leeds at Everton") == ("Leeds", "Everton")
    assert extract_teams("no teams here") == (None, None)


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
