from kalshi.live.scoreboard import match_score, parse_scoreboard

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
