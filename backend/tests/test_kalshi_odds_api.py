from kalshi.data.sources.odds_api import (
    match_event, parse_events, quote_for_event, sport_key_for_series, sport_key_for_league,
)
from kalshi.fair_value import sharp_probs_from_quote

# Documented The-Odds-API v4 shape.
_PAYLOAD = [{
    "home_team": "Argentina", "away_team": "Austria", "commence_time": "2026-06-23T18:00:00Z",
    "bookmakers": [
        {"key": "williamhill", "markets": [{"key": "h2h", "outcomes": [
            {"name": "Argentina", "price": 1.50}, {"name": "Austria", "price": 7.0}, {"name": "Draw", "price": 4.2}]}]},
        {"key": "pinnacle", "markets": [{"key": "h2h", "outcomes": [
            {"name": "Argentina", "price": 1.55}, {"name": "Austria", "price": 6.5}, {"name": "Draw", "price": 4.0}]}]},
    ],
}]


def test_sport_key_lookups():
    assert sport_key_for_series("KXWCGAME") == "soccer_fifa_world_cup"
    assert sport_key_for_series("UNKNOWN") is None
    assert sport_key_for_league("EPL") == "soccer_epl"
    assert sport_key_for_league("nope") is None


def test_parse_events_extracts_three_way_per_book():
    evs = parse_events(_PAYLOAD)
    assert len(evs) == 1
    e = evs[0]
    assert e["home"] == "Argentina" and e["away"] == "Austria"
    assert set(e["books"]) == {"williamhill", "pinnacle"}
    assert e["books"]["pinnacle"] == {"home": 1.55, "draw": 4.0, "away": 6.5}


def test_parse_events_degrades_safely():
    assert parse_events(None) == []
    assert parse_events([{"home_team": "A"}]) == []        # no away / no books
    assert parse_events([{"home_team": "A", "away_team": "B", "bookmakers": []}]) == []


def test_quote_for_event_prefers_sharpest_book():
    e = parse_events(_PAYLOAD)[0]
    q = quote_for_event(e)
    assert q.book == "pinnacle" and q.home == 1.55   # pinnacle ranked above williamhill


def test_match_event_by_name_and_flipped():
    evs = parse_events(_PAYLOAD)
    assert match_event(evs, "Argentina", "Austria")["home"] == "Argentina"
    flipped = match_event(evs, "Austria", "Argentina")    # opposite orientation
    assert flipped["home"] == "Austria"
    assert flipped["books"]["pinnacle"]["home"] == 6.5    # swapped to our home (Austria)
    assert match_event(evs, "Brazil", "France") is None


def test_match_event_does_not_substring_match_wrong_team():
    # Guinea must NOT match Guinea-Bissau (substring) — that would bet the wrong
    # match's odds. Exact-canonical matching only.
    evs = [{"home": "Guinea", "away": "Spain", "books": {"pinnacle": {"home": 5.0, "draw": 3.8, "away": 1.7}}}]
    assert match_event(evs, "Guinea-Bissau", "Spain") is None
    assert match_event(evs, "Guinea", "Spain") is not None


def test_match_event_folds_national_aliases():
    evs = [{"home": "United States", "away": "Iran", "books": {"pinnacle": {"home": 2.0, "draw": 3.3, "away": 3.6}}}]
    assert match_event(evs, "USA", "IR Iran") is not None   # aliases fold to canonical


def test_sharp_probs_from_quote_shape_and_normalization():
    e = parse_events(_PAYLOAD)[0]
    sp = sharp_probs_from_quote(quote_for_event(e))
    assert set(sp) == {"winner"}
    w = sp["winner"]
    assert abs(sum(w.values()) - 1.0) < 1e-9
    assert w["home"] > w["away"]           # Argentina favored
    assert sharp_probs_from_quote(None) == {}
