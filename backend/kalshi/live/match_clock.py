"""Best-effort match clock from a raw Kalshi market dict (Kalshi-price-only mode).

We have no external score feed, so kickoff/minute are derived from the market's
own timestamps when present. When nothing is parseable the phase is UNKNOWN and
the live monitor falls back to price-move detection (it never fakes a minute).
Pure + unit-tested."""
from __future__ import annotations

import datetime
import re

PREGAME = "pregame"
LIVE = "live"
ENDED = "ended"
UNKNOWN = "unknown"

_TICKER_DATE = re.compile(r"(\d{2})(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)(\d{2})")
_MONTHS = {"JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
           "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12}


def match_date_from_ticker(ticker):
    """Parse the YYMONDD date token Kalshi embeds in game tickers, e.g.
    'KXWCGAME-26JUN23ARGAUT' -> date(2026, 6, 23). None when absent/invalid."""
    m = _TICKER_DATE.search((ticker or "").upper())
    if not m:
        return None
    yy, mon, dd = m.groups()
    try:
        return datetime.date(2000 + int(yy), _MONTHS[mon], int(dd))
    except (ValueError, KeyError):
        return None


def kickoff_date_ts(ticker):
    """Epoch seconds for 00:00 UTC of the ticker's match DATE — an approximate kickoff
    for the UI countdown when the market exposes no precise start time. None if the
    ticker has no date token. (Display only — NOT used for phase/live timing, which
    needs a precise kickoff.)"""
    d = match_date_from_ticker(ticker)
    if d is None:
        return None
    return datetime.datetime(d.year, d.month, d.day, tzinfo=datetime.timezone.utc).timestamp()


def match_phase(kickoff_ts, now_ts, *, regulation_min: float = 115.0):
    """Return (phase, elapsed_min). kickoff_ts/now_ts are epoch seconds.
    kickoff_ts falsy -> (UNKNOWN, None). Live spans [kickoff, kickoff+regulation_min]
    (regulation + stoppage + halftime); after that ENDED; before kickoff PREGAME."""
    if not kickoff_ts or not now_ts:
        return (UNKNOWN, None)
    elapsed = (now_ts - kickoff_ts) / 60.0
    if elapsed < 0:
        return (PREGAME, elapsed)
    if elapsed <= regulation_min:
        return (LIVE, elapsed)
    return (ENDED, elapsed)


def _iso_to_epoch(value) -> float | None:
    if not value or not isinstance(value, str):
        return None
    s = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt.timestamp()
    except (ValueError, TypeError):
        return None


def kickoff_from_market(market: dict):
    """Best-effort kickoff epoch seconds from a raw Kalshi market dict.

    Kalshi exposes an explicit game start on some markets; we try the most
    specific time fields first. We deliberately do NOT guess a kickoff from a
    close/expiration time (those trail the match by an unknown margin) or from a
    date-only ticker token — a wrong minute is worse than UNKNOWN. Returns None
    when no reliable start time is present."""
    if not isinstance(market, dict):
        return None
    # Only true game-start fields — NOT open_time/close_time (those are the
    # trading window, which lead/trail the match by an unknown margin).
    for key in ("game_start_time", "kickoff_time", "start_time"):
        ts = _iso_to_epoch(market.get(key))
        if ts:
            return ts
    return None


def phase_for_market(*, kickoff_ts, ticker, now_ts, regulation_min: float = 115.0):
    """Combined phase: a precise kickoff timestamp wins; otherwise fall back to the
    ticker date (future -> PREGAME, past -> ENDED). A match dated TODAY but with no
    precise kickoff is PREGAME, NOT live: the date alone can't tell a 3pm kickoff
    from one that's still 10 hours away. Promotion to LIVE requires either a real
    kickoff_ts that puts `now` inside the match window (the branch above) or ESPN's
    scoreboard confirming state == in (the engine overlays that). No date and no
    kickoff -> UNKNOWN. Returns (phase, elapsed_min)."""
    if kickoff_ts:
        return match_phase(kickoff_ts, now_ts, regulation_min=regulation_min)
    d = match_date_from_ticker(ticker)
    if d is None:
        return (UNKNOWN, None)
    today = datetime.datetime.fromtimestamp(now_ts, datetime.timezone.utc).date() \
        if now_ts else datetime.date.today()
    if d < today:
        return (ENDED, None)
    # d >= today: future OR today-with-unknown-kickoff -> not confidently live.
    return (PREGAME, None)
