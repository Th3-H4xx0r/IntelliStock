"""Deterministic weekly digest for TR stage 1 (spec §7.2, plan Task 5).

No AI. One decision's digest compresses the week's news into
representative headlines and adds GDELT theme context, macro releases, the
event calendar and the market tape.

Window. For a decision on NY date ``S`` in the week starting Monday ``M``,
the week is ``(week_decision_time(M - 7), week_decision_time(M)]`` -- 09:00
ET on each week's first NYSE session, the same windows the GDELT titles
were fetched over -- and, as for every prompt input, only rows with
``available_at < decision_time``. Consecutive weeks tile with no gap and no
overlap. A row stamped after 09:00 ET but before a 09:30 decision falls in
the NEXT week's digest, where it is still unseen news.

Articles are deduplicated by ``(source, source_id)``, keeping the version
with the earliest ``available_at`` visible at the decision (an edit
produces a second row). Edits are looked for ``DEDUP_LOOKBACK`` before the
window, so a story first seen last week and edited this week is not news
this week.

Headlines (alpaca rows): cluster by normalized headline (lowercase,
punctuation stripped, first 12 tokens); score = cluster size x (1 +
distinct symbols across the cluster); ties by the representative's
``available_at`` then ``id`` (bytewise). The top ``max_headlines`` clusters
are represented by their earliest article.

Themes: TrendThemesPIT theme-days available in the window (volume, share,
tone) against the ``BASELINE_WEEKS`` before it, plus the window's gdelt
titles grouped by theme: at most ``TITLES_PER_THEME`` distinct headlines,
the most prominent first where rows carry ``prominence`` (GKG titles,
ruling R37), otherwise oldest first.

``digest_hash`` is sha256 over canonical JSON (sorted keys, no whitespace),
so one store gives one hash whatever order its rows were written in.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timedelta, timezone

from swing_trader import clock
from trend_research import calendar_data, ingest_gdelt, ingest_macro, market_tape
from trend_research.pit_store import parse_available_at, read_available

__all__ = ["weekly_digest", "digest_hash", "digest_window", "symbol_news", "visible_news",
           "normalize_headline"]

NEWS_TABLE = "TrendNewsPIT"
HEADLINE_TOKENS = 12
#: How far before a window an earlier version of an edited article is sought.
DEDUP_LOOKBACK = timedelta(days=7)
BASELINE_WEEKS = 4
TITLES_PER_THEME = 15
#: Calendar days after the decision date covered by the event calendar.
CALENDAR_AHEAD_DAYS = 13
_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _check_time(decision_time) -> None:
    if not isinstance(decision_time, datetime) or decision_time.utcoffset() is None:
        raise ValueError("decision_time must be a tz-aware datetime, got %r"
                         % (decision_time,))


def _id_key(row) -> bytes:
    return str(row.get("id", "")).encode("utf-8")


def _num(value):
    return market_tape._num(value)


def digest_window(decision_time: datetime) -> tuple:
    """``(start, end)``: the digest covers ``start < available_at <= end``
    (and ``< decision_time``)."""
    _check_time(decision_time)
    monday = clock.week_monday(date.fromisoformat(clock.ny_date(decision_time)))
    return (ingest_gdelt.week_decision_time(monday - timedelta(days=7)),
            ingest_gdelt.week_decision_time(monday))


def visible_news(start: datetime, decision_time: datetime, *, store=None) -> list:
    """TrendNewsPIT rows with ``start <= available_at < decision_time``,
    deduplicated by ``(source, source_id)`` keeping the earliest version;
    oldest first (ties by id, bytewise)."""
    _check_time(decision_time)
    rows = read_available(NEWS_TABLE, lo_id=_iso(start),
                          hi_id=_iso(decision_time + timedelta(seconds=1)),
                          decision_time=decision_time, store=store)
    seen, out = set(), []
    for row in rows:                       # oldest available_at first
        if parse_available_at(row.get("available_at")) < start:
            continue
        key = (row.get("source"), row.get("source_id") or row.get("id"))
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def _symbols(row) -> list:
    return sorted({str(s).strip().upper() for s in row.get("symbols") or []
                   if isinstance(s, str) and s.strip()})


def symbol_news(symbol: str, decision_time: datetime, *, weeks: int = 8, limit: int = 40,
                store=None, articles=None) -> list:
    """Alpaca articles tagged ``symbol`` with ``available_at`` in
    ``[decision_time - weeks, decision_time)``, newest first, at most
    ``limit``. GDELT rows carry no symbols and are never returned.
    ``articles``: rows already read with ``visible_news`` over at least that
    span, to avoid one read per symbol."""
    _check_time(decision_time)
    sym = str(symbol or "").strip().upper()
    start = decision_time - timedelta(weeks=weeks)
    if articles is None:
        articles = visible_news(start, decision_time, store=store)
    hits = []
    for row in articles:
        if row.get("source") != "alpaca" or sym not in _symbols(row):
            continue
        at = parse_available_at(row.get("available_at"))
        if not start <= at < decision_time:
            continue
        hits.append((at, _id_key(row), row))
    hits.sort(key=lambda h: (h[0], h[1]), reverse=True)
    return [{"id": r["id"], "t": r.get("available_at"), "source": "alpaca",
             "published_at": r.get("published_at"), "headline": r.get("headline") or "",
             "summary": r.get("summary") or "", "content": r.get("content") or "",
             "url": r.get("url") or "", "symbols": _symbols(r)}
            for _, _, r in hits[:max(0, int(limit))]]


def normalize_headline(text) -> str:
    """Lowercase, punctuation stripped, the first ``HEADLINE_TOKENS`` tokens."""
    words = _PUNCT.sub("", str(text or "").lower()).split()
    return " ".join(words[:HEADLINE_TOKENS])


def _headlines(rows: list, max_headlines: int) -> list:
    clusters: dict = {}
    for row in rows:                        # oldest first: the first member is the earliest
        key = normalize_headline(row.get("headline"))
        if key:
            clusters.setdefault(key, []).append(row)
    ranked = []
    for members in clusters.values():
        rep = members[0]
        symbols = set()
        for m in members:
            symbols.update(_symbols(m))
        score = len(members) * (1 + len(symbols))
        ranked.append((-score, parse_available_at(rep["available_at"]), _id_key(rep), rep))
    ranked.sort(key=lambda r: r[:3])
    return [{"id": rep["id"], "t": rep["available_at"], "headline": rep.get("headline") or "",
             "symbols": _symbols(rep)} for *_k, rep in ranked[:max(0, int(max_headlines))]]


def _theme_totals(rows: list) -> dict:
    vol = sum(int(r.get("volume") or 0) for r in rows)
    totals = [r.get("total_articles") for r in rows]
    total = sum(totals) if totals and all(isinstance(t, (int, float)) for t in totals) else None
    toned = [(int(r.get("volume") or 0), _num(r.get("tone"))) for r in rows]
    toned = [(v, t) for v, t in toned if t is not None]
    tone_w = sum(v for v, _ in toned)
    tone = sum(v * t for v, t in toned) / tone_w if tone_w else None
    return {"volume": vol, "share": (vol / total) if total else None, "tone": tone}


def _prominence(row) -> int:
    value = row.get("prominence")
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def _themes(decision_time, start, end, gdelt_rows, store) -> list:
    base_start = start - timedelta(weeks=BASELINE_WEEKS)
    week, base = {}, {}
    for row in ingest_gdelt.themes_between((base_start - timedelta(days=2)).date(), end.date(),
                                           decision_time, store=store):
        at = parse_available_at(row.get("available_at"))
        theme = row.get("theme")
        if start < at <= end:
            week.setdefault(theme, []).append(row)
        elif base_start < at <= start:
            base.setdefault(theme, []).append(row)
    titles: dict = {}
    # oldest first, ties by id; GKG rows (ruling R37) rank by prominence
    # first -- a stable sort, so rows without it (the DOC-API path) keep
    # that order
    for row in sorted(gdelt_rows, key=lambda r: -_prominence(r)):
        th = titles.setdefault(row.get("theme"), {})
        key = normalize_headline(row.get("headline"))
        if key and key not in th and len(th) < TITLES_PER_THEME:
            th[key] = {"id": row["id"], "t": row["available_at"],
                       "headline": row.get("headline") or "", "domain": row.get("domain") or ""}
    out = []
    for theme in sorted(t for t in set(week) | set(titles) if isinstance(t, str)):
        cur = _theme_totals(week[theme]) if theme in week else None
        prev = _theme_totals(base[theme]) if theme in base else None
        share, prev_share = (cur or {}).get("share"), (prev or {}).get("share")
        out.append({
            "theme": theme,
            "days": len(week.get(theme, [])),
            "volume": cur["volume"] if cur else None,
            "share": _num(share),
            "tone": _num(cur["tone"]) if cur else None,
            "baseline_share": _num(prev_share),
            "share_change": _num(share / prev_share - 1) if share is not None and prev_share
            else None,
            "titles": list(titles.get(theme, {}).values()),
        })
    return out


def _macro(decision_time, start, store) -> list:
    out = []
    for series in ingest_macro.MACRO_SERIES:
        obs = ingest_macro.macro_as_of(series, decision_time, store=store)
        if not obs:
            continue
        last, prev = obs[-1], (obs[-2] if len(obs) > 1 else None)
        value, prev_value = _num(last.get("value")), _num(prev.get("value")) if prev else None
        out.append({
            "series": series, "obs_date": last.get("obs_date"), "value": value,
            "prev_obs_date": prev.get("obs_date") if prev else None, "prev_value": prev_value,
            "change": _num(value - prev_value) if value is not None and prev_value is not None
            else None,
            "available_at": last.get("available_at"),
            "new_this_week": parse_available_at(last.get("available_at")) > start,
        })
    return out


def weekly_digest(decision_time: datetime, *, max_headlines: int = 250, store=None,
                  bars_loader=None) -> dict:
    """``{"window", "headlines", "themes", "macro", "calendar", "tape"}`` for
    the week before ``decision_time`` (see the module docstring)."""
    start, end = digest_window(decision_time)
    rows = [r for r in visible_news(start - DEDUP_LOOKBACK, decision_time, store=store)
            if start < parse_available_at(r["available_at"]) <= end]
    alpaca = [r for r in rows if r.get("source") == "alpaca"]
    gdelt = [r for r in rows if r.get("source") == "gdelt"]
    session = date.fromisoformat(clock.ny_date(decision_time))
    return {
        "window": {"start": _iso(start), "end": _iso(end), "decision_time": _iso(decision_time)},
        "headlines": _headlines(alpaca, max_headlines),
        "themes": _themes(decision_time, start, end, gdelt, store),
        "macro": _macro(decision_time, start, store),
        "calendar": calendar_data.event_calendar(
            start.date(), session + timedelta(days=CALENDAR_AHEAD_DAYS),
            decision_time=decision_time),
        "tape": market_tape.market_tape(decision_time, bars_loader=bars_loader, store=store),
    }


def digest_hash(digest: dict) -> str:
    """sha256 hex of ``digest`` as canonical JSON (sorted keys, compact)."""
    blob = json.dumps(digest, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
