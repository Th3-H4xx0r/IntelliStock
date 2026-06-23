from kalshi.live.scoreboard import clock_minutes, live_status, match_score, parse_scoreboard


def test_clock_minutes():
    assert clock_minutes("67'") == 67.0
    assert clock_minutes("45'+7'") == 52.0
    assert clock_minutes("") is None
    assert clock_minutes("HT") is None


def test_live_status_maps_state_and_clock():
    assert live_status({"state": "in", "clock": "67'"}) == ("live", 67.0)
    assert live_status({"state": "post"}) == ("ended", None)
    assert live_status({"state": "pre"}) == ("pregame", None)
    assert live_status(None) == (None, None)

_ESPN = {
    "events": [{
        "competitions": [{
            "status": {"displayClock": "67'", "type": {"state": "in", "shortDetail": "67'"}},
            "competitors": [
                {"homeAway": "home", "score": "2", "team": {"displayName": "Argentina"}},
                {"homeAway": "away", "score": "1", "team": {"displayName": "Austria"}},
            ],
        }],
    }],
}


def test_parse_scoreboard_extracts_score_and_clock():
    g = parse_scoreboard(_ESPN)
    assert len(g) == 1
    e = g[0]
    assert e["home"] == "Argentina" and e["away"] == "Austria"
    assert e["home_score"] == 2 and e["away_score"] == 1
    assert e["clock"] == "67'" and e["state"] == "in"


def test_parse_scoreboard_handles_empty():
    assert parse_scoreboard(None) == []
    assert parse_scoreboard({"events": []}) == []


def test_match_score_by_name_and_flipped_orientation():
    board = parse_scoreboard(_ESPN)
    m = match_score(board, "Argentina", "Austria")
    assert m["home_score"] == 2 and m["away_score"] == 1
    # If we ask in the opposite orientation, scores swap to our home/away.
    f = match_score(board, "Austria", "Argentina")
    assert f["home"] == "Austria" and f["home_score"] == 1 and f["away_score"] == 2
    assert match_score(board, "Brazil", "France") is None
