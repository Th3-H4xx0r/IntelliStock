"""OddsPapi ingestion — the load-bearing odds source (Pinnacle sharp anchor +
Kalshi/prediction-market lines). The free tier is ~250 requests/month, so a
budget guard throttles the daily scan and refuses to overspend; what gets
skipped is logged, never silently dropped (feature #5).

The OddsPapi key is a QUERY PARAM, not a header. The exact response schema must
be confirmed in Phase 0; `parse_three_way` works on a normalized shape that the
client maps raw responses into.
"""
from __future__ import annotations

import datetime as _dt
from typing import Any, Optional

from kalshi.models import OddsQuote

DEFAULT_MONTHLY_LIMIT = 250


def budget_remaining(used: int, limit: int = DEFAULT_MONTHLY_LIMIT) -> int:
    return max(0, limit - used)


def should_scan(used: int, cost: int, limit: int = DEFAULT_MONTHLY_LIMIT) -> bool:
    """Allow a scan only if it fits within the remaining monthly budget."""
    return cost > 0 and budget_remaining(used, limit) >= cost


def fixtures_per_day_budget(used: int, days_left_in_month: int, limit: int = DEFAULT_MONTHLY_LIMIT) -> int:
    """Adaptive throttle: how many fixtures we can afford to scan per remaining
    day without blowing the month's budget."""
    if days_left_in_month <= 0:
        return 0
    return budget_remaining(used, limit) // days_left_in_month


def parse_three_way(raw: dict, book: str = "pinnacle") -> Optional[OddsQuote]:
    """Extract a 3-way (home/draw/away) decimal-odds quote for `book` from a
    normalized response: {"books": {"pinnacle": {"home": 2.0, "draw": 3.5,
    "away": 4.0}}}. Returns None if the book or a price is missing."""
    books = (raw or {}).get("books", {})
    b = books.get(book) or books.get(book.lower())
    if not b:
        return None
    try:
        return OddsQuote(book=book, home=float(b["home"]), draw=float(b["draw"]), away=float(b["away"]))
    except (KeyError, TypeError, ValueError):
        return None


def _to_ts(entry: dict) -> int | None:
    """Fixture kickoff as epoch seconds, accepting either a raw `kickoff_ts`
    (int) or an ISO8601 `kickoff` string. None if neither is present/parseable."""
    ts = entry.get("kickoff_ts")
    if ts is not None:
        try:
            return int(ts)
        except (TypeError, ValueError):
            return None
    iso = entry.get("kickoff")
    if not iso:
        return None
    try:
        s = str(iso).replace("Z", "+00:00")
        dt = _dt.datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_dt.timezone.utc)
        return int(dt.timestamp())
    except (TypeError, ValueError):
        return None


def parse_fixtures(raw: dict) -> list[dict]:
    """Normalize an OddsPapi fixtures-list response into
    [{fixture_id, home, away, kickoff_ts, home_score, away_score, result, settled}].
    Settled = both scores present; result derived home/draw/away from the score,
    None when either score is missing. Never raises; empty-safe."""
    out = []
    for f in (raw or {}).get("fixtures") or []:
        hs, as_ = f.get("home_score"), f.get("away_score")
        settled = hs is not None and as_ is not None
        result = None
        if settled:
            try:
                hs_n, as_n = float(hs), float(as_)
                result = "home" if hs_n > as_n else ("away" if as_n > hs_n else "draw")
            except (TypeError, ValueError):
                settled = False
        out.append({
            "fixture_id": str(f.get("id", "")),
            "home": f.get("home", ""),
            "away": f.get("away", ""),
            "kickoff_ts": _to_ts(f),
            "home_score": hs if settled else None,
            "away_score": as_ if settled else None,
            "result": result,
            "settled": settled,
        })
    return out


def parse_hist_odds(raw: dict, books: tuple[str, ...] = ("pinnacle",)) -> list[dict]:
    """Normalize an OddsPapi historical-odds response into decimal-odds
    snapshots [{ts, home, draw, away}] for the first available book (in
    priority order) per snapshot, sorted ascending by ts. Snapshots with none
    of the requested books are skipped. Never raises; empty-safe."""
    out = []
    for snap in (raw or {}).get("odds") or []:
        snap_books = (snap or {}).get("books") or {}
        chosen = None
        for b in books:
            if b in snap_books:
                chosen = snap_books[b]
                break
        if not chosen:
            continue
        try:
            out.append({
                "ts": int(snap.get("ts")),
                "home": float(chosen["home"]),
                "draw": float(chosen["draw"]),
                "away": float(chosen["away"]),
            })
        except (KeyError, TypeError, ValueError):
            continue
    out.sort(key=lambda s: s["ts"])
    return out


class OddsPapiClient:
    def __init__(self, api_key: str, session: Any = None, base_url: str = "https://api.oddspapi.io"):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        if session is None:
            import requests
            session = requests.Session()
        self._session = session

    # OddsPapi sits behind Cloudflare, which 403s (error 1010) requests without a
    # browser-like User-Agent. Send one so the API is reachable at all.
    _HEADERS = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/126 Safari/537.36",
        "Accept": "application/json",
    }

    def fetch_raw(self, path: str, params: dict | None = None) -> dict:
        p = dict(params or {})
        p["apiKey"] = self.api_key  # query param, not a header
        resp = self._session.request("GET", f"{self.base_url}{path}", params=p,
                                     headers=self._HEADERS, timeout=20.0)
        resp.raise_for_status()
        return resp.json() if resp.content else {}

    def list_fixtures(self, sport_id: int, date_from: str, date_to: str) -> list[dict]:
        """Fixtures for a sport in a date window, normalized (parsed)."""
        raw = self.fetch_raw("/v4/fixtures", {
            "sportId": sport_id, "from": date_from, "to": date_to,
        })
        return parse_fixtures(raw)

    def historical_odds(self, fixture_id, books: tuple[str, ...] = ("pinnacle",)) -> list[dict]:
        """Historical odds snapshots for a fixture, normalized (parsed) for the
        first available book in `books` priority order."""
        raw = self.fetch_raw("/v4/historical-odds",
                             {"fixtureId": fixture_id, "bookmakers": ",".join(books)})
        return parse_hist_odds(raw, books=books)
