import datetime

from kalshi.live.match_clock import (
    ENDED, LIVE, PREGAME, UNKNOWN,
    kickoff_from_market, match_date_from_ticker, match_phase, phase_for_market,
)


def _ts(y, mo, d, h=0):
    return datetime.datetime(y, mo, d, h, tzinfo=datetime.timezone.utc).timestamp()


def test_match_phase_boundaries():
    ko = _ts(2026, 6, 23, 12)
    assert match_phase(ko, ko - 600)[0] == PREGAME
    phase, elapsed = match_phase(ko, ko + 45 * 60)
    assert phase == LIVE and 44 < elapsed < 46
    assert match_phase(ko, ko + 200 * 60)[0] == ENDED
    assert match_phase(None, ko) == (UNKNOWN, None)


def test_kickoff_from_market_iso_field_and_missing():
    assert kickoff_from_market({"game_start_time": "2026-06-23T12:00:00Z"}) == _ts(2026, 6, 23, 12)
    assert kickoff_from_market({}) is None
    assert kickoff_from_market({"close_time": "2026-06-23T20:00:00Z"}) is None  # close != kickoff
    assert kickoff_from_market({"open_time": "2026-06-23T08:00:00Z"}) is None   # open != kickoff
    assert kickoff_from_market("not a dict") is None


def test_match_date_from_ticker():
    assert match_date_from_ticker("KXWCGAME-26JUN23ARGAUT") == datetime.date(2026, 6, 23)
    assert match_date_from_ticker("KXWCGAME-26JUN14GERCUW-GER") == datetime.date(2026, 6, 14)
    assert match_date_from_ticker("no-date-here") is None


def test_phase_for_market_falls_back_to_ticker_date():
    today = _ts(2026, 6, 23)
    assert phase_for_market(kickoff_ts=None, ticker="X-26JUN24ABCDEF", now_ts=today)[0] == PREGAME
    # today's date but NO precise kickoff -> NOT assumed live (could be hours away);
    # only a real kickoff_ts or ESPN state==in promotes to LIVE.
    assert phase_for_market(kickoff_ts=None, ticker="X-26JUN23ABCDEF", now_ts=today)[0] == PREGAME
    assert phase_for_market(kickoff_ts=None, ticker="X-26JUN22ABCDEF", now_ts=today)[0] == ENDED
    assert phase_for_market(kickoff_ts=None, ticker="no-date", now_ts=today)[0] == UNKNOWN
    # a precise kickoff wins over the date heuristic
    ko = _ts(2026, 6, 23, 12)
    assert phase_for_market(kickoff_ts=ko, ticker="X-26JUN23ABCDEF", now_ts=ko + 60)[0] == LIVE
