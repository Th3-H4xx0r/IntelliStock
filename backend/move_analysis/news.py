"""News published between the previous close and the close of the day a
stock moved. Alpaca's news feed (Benzinga) first; Google News when Alpaca has
nothing or no Alpaca account is linked.

Only articles stamped inside the window are returned, so a write-up published
after the move cannot be handed to the model as its cause.
"""
from __future__ import annotations

from datetime import date, datetime, time as dtime, timedelta, timezone
from typing import Callable, Optional
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
ALPACA_NEWS_URL = "https://data.alpaca.markets/v1beta1/news"
MAX_ARTICLES = 25
SUMMARY_CHARS = 500


def news_window(prev_date: Optional[str], day: str) -> tuple:
    """``(start, end)`` in UTC: previous session's 16:00 close to this
    session's 16:15, New York time."""
    this_day = date.fromisoformat(day)
    prev_day = date.fromisoformat(prev_date) if prev_date else this_day - timedelta(days=3)
    start = datetime.combine(prev_day, dtime(16, 0), tzinfo=NY)
    end = datetime.combine(this_day, dtime(16, 15), tzinfo=NY)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def _parse_stamp(value) -> Optional[datetime]:
    if isinstance(value, datetime):
        stamp = value
    elif isinstance(value, str) and value.strip():
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            stamp = datetime.fromisoformat(text)
        except ValueError:
            return None
    else:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc)


def _iso(stamp: datetime) -> str:
    return stamp.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _clean(text, limit: int) -> str:
    return " ".join(str(text or "").split())[:limit]


def _http_url(value) -> str:
    url = str(value or "").strip()
    return url if url.startswith(("https://", "http://")) else ""


def alpaca_credentials(store, brokerage_id: Optional[str] = None) -> Optional[tuple]:
    """Market-data keys from a linked Alpaca account: the named one, else a
    paper account, else any. None when there is none."""
    from stock_credential_boundary import resolve_alpaca_brokerage_credentials

    try:
        rows = [row for row in store.run("BrokerageAccounts")
                if str((row or {}).get("brokerage_type") or "").strip().lower() == "alpaca"]
    except Exception:
        return None
    rows.sort(key=lambda row: (str(row.get("id")) != str(brokerage_id or ""),
                               row.get("alpaca_paper") is not True))
    for row in rows:
        try:
            creds = resolve_alpaca_brokerage_credentials(
                row, expected_brokerage_id=str(row.get("id")))
        except Exception:
            continue
        if creds.key and creds.secret:
            return creds.key, creds.secret
    return None


def fetch_alpaca(symbol: str, start: datetime, end: datetime, key: str,
                 secret: str, *, http_get: Optional[Callable] = None) -> list:
    if http_get is None:
        import requests

        http_get = requests.get
    response = http_get(
        ALPACA_NEWS_URL,
        headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret,
                 "accept": "application/json"},
        params={"symbols": symbol, "start": _iso(start), "end": _iso(end),
                "limit": 50, "sort": "desc", "include_content": "false"},
        timeout=20,
    )
    response.raise_for_status()
    raw = (response.json() or {}).get("news") or []
    out = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        stamp = _parse_stamp(item.get("created_at"))
        title = _clean(item.get("headline"), 300)
        if stamp is None or not title or not (start <= stamp <= end):
            continue
        symbols = item.get("symbols") if isinstance(item.get("symbols"), list) else []
        out.append({
            "title": title,
            "summary": _clean(item.get("summary"), SUMMARY_CHARS),
            "url": _http_url(item.get("url")),
            "source": _clean(item.get("source") or "Benzinga", 60),
            "published_at": _iso(stamp),
            "_breadth": len(symbols) or 99,
        })
    return out


def fetch_google(symbol: str, start: datetime, end: datetime) -> list:
    from strategies.google_news import fetch_google_news

    raw = fetch_google_news(
        keywords=[f"{symbol} stock"],
        start_date=start.replace(tzinfo=None),
        end_date=end.replace(tzinfo=None),
        max_results_per_keyword=20,
        max_total=20,
    )
    out = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        stamp = _parse_stamp(item.get("published_date"))
        title = _clean(item.get("title") or item.get("headline"), 300)
        if stamp is None or not title or not (start <= stamp <= end):
            continue
        out.append({
            "title": title,
            "summary": _clean(item.get("description"), SUMMARY_CHARS),
            "url": _http_url(item.get("url")),
            "source": _clean(item.get("source") or "Google News", 60),
            "published_at": _iso(stamp),
            "_breadth": 50,
        })
    return out


def articles_for(symbol: str, prev_date: Optional[str], day: str, *,
                 credentials: Optional[tuple] = None,
                 alpaca: Callable = fetch_alpaca,
                 google: Callable = fetch_google) -> list:
    """Up to ``MAX_ARTICLES`` articles, the ones about fewest other symbols
    first, then oldest first. Never raises: no news is an answer."""
    start, end = news_window(prev_date, day)
    found: list = []
    if credentials:
        try:
            found = alpaca(symbol, start, end, credentials[0], credentials[1])
        except Exception:
            found = []
    if not found:
        try:
            found = google(symbol, start, end)
        except Exception:
            found = []
    seen = set()
    unique = []
    for article in found:
        key = article["title"].lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(article)
    unique.sort(key=lambda a: (a.get("_breadth", 99), a["published_at"]))
    return [{k: v for k, v in a.items() if not k.startswith("_")}
            for a in unique[:MAX_ARTICLES]]
