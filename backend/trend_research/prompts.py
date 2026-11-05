"""Prompts for the four TR research stages (spec §7.2, plan Task 6).

Every prompt opens with the simulated date and the instruction to use only
the supplied material (``header``); tools and web search stay off in the
caller. No prompt carries anything about the lane's simulated book (spec C2):
the inputs are world state at the date (digest, dossier) and the ledger's own
prior research (trend list, recommended names, last note).

Rendering is deterministic -- the same inputs give the same text -- so a
resumed week re-sends identical prompts and hits the prompt cache.

Inputs, as the builder (Task 7) passes them:

* ``digest``: ``digest.weekly_digest`` output (window, headlines, themes,
  macro, calendar, tape).
* ``prior_trends``: the previous ledger row's ``scan["trends"]`` (Trend
  dicts or models) in the same chain, or None on a cold start.
* ``scan``: a ``TrendScan`` (model or dict).
* ``prior_recommended``: the previous row's recommended names -- dicts with
  ``symbol`` and optionally ``conviction`` / ``thesis``, or bare symbols.
* ``dossier``: ``filings.filing_dossier`` output (``factors``,
  ``factors_status``, ``filings``) plus, all optional, ``trend_stats``
  (``market_tape.symbol_trend_stats``), ``news`` (``digest.symbol_news``),
  ``prior_note`` (the symbol's ResearchNote from the previous row),
  ``trend_names`` and ``why`` (from the candidate list).
* ``notes``: this week's ResearchNotes (models or dicts).
"""
from __future__ import annotations

import dataclasses
import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from trend_research import book

__all__ = ["PROMPT_VERSION", "SYSTEM_PROMPT", "header", "trend_scan_prompt",
           "candidate_prompt", "research_prompt", "book_prompt"]

PROMPT_VERSION = "tr-v1"

_NY = ZoneInfo("America/New_York")

#: Per-item caps, in characters, so one long article or filing cannot crowd
#: out the rest of a dossier.
FILING_EXCERPT_CHARS = 4000
NEWS_SUMMARY_CHARS = 300
NEWS_CONTENT_CHARS = 600

SYSTEM_PROMPT = (
    "You are an equity research analyst running a weekly study of market trends. "
    "Each request is dated and carries everything that was public at that moment. "
    "Treat the stated date as today: do not use knowledge of anything that happened "
    "after it, do not guess later prices, and do not browse. When the material does "
    "not support a claim, say so rather than filling the gap from memory. Reply only "
    "with JSON that matches the requested schema."
)

_CATEGORIES = (
    "  - tech: technology waves\n"
    "  - geopolitics: wars, tariffs, sanctions, elections\n"
    "  - economy_rates: interest rates, inflation, oil, the dollar\n"
    "  - consumer_health: spending shifts, drugs, demographics"
)

_STATES = (
    "emerging (first signs), strengthening (evidence building), mature (widely known, "
    "still in force), fading (evidence weakening), broken (no longer holds)"
)


def _sorted(symbols) -> str:
    return ", ".join(sorted(symbols))


UNIVERSE = (
    "Allowed instruments:\n"
    "- Stocks: any US-listed common stock, including US listings of foreign companies "
    "(ADRs). Write plain upper-case tickers with no exchange prefix or suffix "
    "(class shares as BRK.B).\n"
    "- Commodity and materials ETFs: %s\n"
    "- Defensive ETFs: %s (BIL is T-bills)\n"
    "- Inverse ETFs, for bearish bets: %s\n"
    "- Leveraged ETFs, for high-conviction trends only: %s\n"
    "Any other ETF or fund (for example ITA, SMH, XBI, SPY, QQQ) is rejected."
    % (_sorted(book.COMMODITY_ETFS), _sorted(book.DEFENSIVE_ETFS),
       _sorted(book.INVERSE_ETFS), _sorted(book.LEVERAGED_ETFS))
)


# -- rendering helpers -------------------------------------------------------------

def header(as_of: datetime) -> str:
    """The opening line of every prompt, dated in New York."""
    if not isinstance(as_of, datetime) or as_of.utcoffset() is None:
        raise ValueError("as_of must be a tz-aware datetime, got %r" % (as_of,))
    return ("Today is {d:%A %Y-%m-%d} before the US market open. Use ONLY the material "
            "below; do not use knowledge of events after this date.").format(
                d=as_of.astimezone(_NY))


def _plain(obj):
    """Models, dataclasses and dates as plain JSON-able values."""
    if isinstance(obj, BaseModel):
        return _plain(obj.model_dump())
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return _plain(dataclasses.asdict(obj))
    if isinstance(obj, dict):
        return {str(k): _plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_plain(v) for v in obj]
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    return obj


def _clip(text, limit: int) -> str:
    s = " ".join(str(text or "").split())
    return s if len(s) <= limit else s[:limit].rstrip() + " ..."


def _pct(value) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "n/a"
    return "%+.1f%%" % (value * 100)


def _num(value, fmt: str = "%.4g") -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "n/a"
    return fmt % value


def _list(items) -> str:
    items = [str(i) for i in (items or ()) if str(i).strip()]
    return ", ".join(items) if items else "none"


def _section(title: str, body: str) -> str:
    return "## %s\n%s" % (title, body.strip() or "none")


def _stats_line(stats: dict | None) -> str:
    if not stats:
        return "no price history"
    parts = ["last close %s (%s)" % (_num(stats.get("last_close")), stats.get("last_session")),
             "1w %s" % _pct(stats.get("ret_1w")), "1m %s" % _pct(stats.get("ret_1m")),
             "3m %s" % _pct(stats.get("ret_3m")),
             "vs SMA50 %s" % _pct(stats.get("sma50_dist")),
             "vs SMA200 %s" % _pct(stats.get("sma200_dist"))]
    if "high_52w_dist" in stats:
        parts.append("vs 52w high %s" % _pct(stats.get("high_52w_dist")))
    if "max_drawdown_3m" in stats:
        parts.append("3m max drawdown %s" % _pct(stats.get("max_drawdown_3m")))
    return ", ".join(parts)


def _trend_lines(trends, *, with_ids: bool = False) -> str:
    lines = []
    for t in _plain(trends or []):
        line = "- %s [%s, %s, confidence %s] beneficiaries: %s; losers: %s. %s" % (
            t.get("name"), t.get("category"), t.get("state"),
            _num(t.get("confidence"), "%.2f"), _list(t.get("beneficiaries")),
            _list(t.get("losers")), _clip(t.get("summary"), 400))
        if with_ids:
            line += " Evidence: %s" % _list(t.get("evidence_ids"))
        lines.append(line)
    return "\n".join(lines)


def _scan_block(scan) -> str:
    s = _plain(scan) or {}
    return "\n\n".join([
        _section("Market regime", "%s: %s" % (s.get("regime"), _clip(s.get("regime_reasons"), 800))),
        _section("Trends", _trend_lines(s.get("trends"))),
    ])


def _note_line(n: dict) -> str:
    return ("- %s: conviction %s, status %s, priced in %s. Thesis: %s Breaks if: %s. "
            "Risks: %s" % (n.get("symbol"), _num(n.get("conviction"), "%.1f"),
                           n.get("thesis_status"), n.get("priced_in"),
                           _clip(n.get("thesis"), 600), _list(n.get("breaks_if")),
                           _clip(n.get("risks"), 300)))


# -- digest sections ---------------------------------------------------------------

def _headline_lines(headlines) -> str:
    return "\n".join("[%s] %s%s" % (h.get("id"), _clip(h.get("headline"), 240),
                                    " (%s)" % _list(h.get("symbols")) if h.get("symbols") else "")
                     for h in headlines or ())


def _theme_lines(themes) -> str:
    lines = []
    for th in themes or ():
        lines.append("- %s: %s days, volume %s, share %s, change vs baseline %s, tone %s" % (
            th.get("theme"), th.get("days"), _num(th.get("volume"), "%d"),
            _num(th.get("share"), "%.4f"), _pct(th.get("share_change")),
            _num(th.get("tone"), "%.2f")))
        for t in th.get("titles") or ():
            lines.append("  [%s] %s (%s)" % (t.get("id"), _clip(t.get("headline"), 200),
                                             t.get("domain") or "?"))
    return "\n".join(lines)


def _macro_lines(macro) -> str:
    return "\n".join(
        "- %s: %s for %s (previous %s for %s, change %s)%s" % (
            m.get("series"), _num(m.get("value")), m.get("obs_date"), _num(m.get("prev_value")),
            m.get("prev_obs_date"), _num(m.get("change")),
            ", released this week" if m.get("new_this_week") else "")
        for m in macro or ())


def _calendar_lines(events) -> str:
    return "\n".join("- %s %s: %s%s" % (e.get("date"), e.get("kind"), e.get("label"),
                                         " (cancelled)" if e.get("cancelled") else "")
                     for e in events or ())


def _tape_lines(tape) -> str:
    tape = tape or {}
    lines = ["- %s: %s" % (sym, _stats_line(stats))
             for sym, stats in sorted((tape.get("symbols") or {}).items())]
    lines.append("- VIX: %s" % _num(tape.get("vix"), "%.2f"))
    return "\n".join(lines)


# -- stage prompts -----------------------------------------------------------------

def trend_scan_prompt(digest: dict, prior_trends, as_of: datetime) -> str:
    """Stage 2: update the running trend list and call the regime."""
    digest = _plain(digest) or {}
    window = digest.get("window") or {}
    if prior_trends:
        prior = _section("Trend list from last week's study", _trend_lines(prior_trends))
    else:
        prior = _section("Trend list from last week's study",
                         "None: this is the first week of the study. Build the list from "
                         "this week's material only.")
    return "\n\n".join([
        header(as_of),
        "Task: update the running list of market trends with this week's evidence, then "
        "call the market regime.\n"
        "- Each trend has a category:\n%s\n"
        "- State is one of: %s.\n"
        "- Keep last week's trends that still hold and update their state; mark a trend "
        "fading or broken when this week's evidence goes against it; add new trends the "
        "evidence supports. About 5 to 15 trends.\n"
        "- confidence is 0 to 1. evidence_ids must be ids in square brackets from the "
        "material below. beneficiaries and losers are tickers of companies or funds "
        "exposed to the trend.\n"
        "- regime is risk_on, neutral, risk_off or crisis; regime_reasons cites the tape, "
        "macro releases and news." % (_CATEGORIES, _STATES),
        prior,
        "This week's material covers %s to %s." % (window.get("start"), window.get("end")),
        _section("Headlines ([id] headline (tickers)), most covered first",
                 _headline_lines(digest.get("headlines"))),
        _section("Global news themes (GDELT), with sample titles",
                 _theme_lines(digest.get("themes"))),
        _section("Macro data (latest first-release values)", _macro_lines(digest.get("macro"))),
        _section("Scheduled events", _calendar_lines(digest.get("calendar"))),
        _section("Market tape (daily closes before today's session)",
                 _tape_lines(digest.get("tape"))),
    ])


def candidate_prompt(scan, prior_recommended, as_of: datetime) -> str:
    """Stage 3: name the instruments most exposed to the active trends."""
    prior_lines = []
    for r in _plain(prior_recommended or []):
        if isinstance(r, dict):
            extra = []
            if r.get("conviction") is not None:
                extra.append("conviction %s" % _num(r.get("conviction"), "%.1f"))
            if r.get("thesis"):
                extra.append(_clip(r.get("thesis"), 300))
            prior_lines.append("- %s%s" % (r.get("symbol"),
                                           ": " + "; ".join(extra) if extra else ""))
        else:
            prior_lines.append("- %s" % r)
    return "\n\n".join([
        header(as_of),
        "Task: name the instruments most exposed to the trends below, about 30 to 40 of "
        "them, ranked strongest exposure first.\n"
        "- Each candidate gives its ticker, the trend names it rides (exactly as written "
        "below) and one sentence on why.\n"
        "- Prefer liquid names with a direct, material exposure. Losers of a trend are "
        "not candidates unless an inverse ETF expresses the bet.\n"
        "- When the regime is risk_off or crisis, include defensive or inverse ETFs.\n"
        "- The names recommended last week are researched again regardless; list one "
        "only if it still fits a trend.\n"
        "- Only instruments in the allowed list below.",
        _scan_block(scan),
        _section("Recommended last week", "\n".join(prior_lines) or "none (first week)"),
        UNIVERSE,
    ])


def research_prompt(symbol: str, dossier: dict, as_of: datetime) -> str:
    """Stage 4: one deep research note on ``symbol``."""
    head = header(as_of)
    sym = str(symbol or "").strip().upper()
    d = _plain(dossier) or {}
    prior = d.get("prior_note")
    if prior:
        prior_text = _note_line(prior)
        status_rule = ("thesis_status compares with the earlier note below: intact, "
                       "weakened or broken.")
    else:
        prior_text = "None: this is the first note on %s." % sym
        status_rule = 'thesis_status must be "new" (there is no earlier note).'

    status = d.get("factors_status")
    factors = d.get("factors") or {}
    if status == "ok":
        fund = ("Period ending %s, filed %s: gp_a (gross profit / assets) %s, "
                "asset_growth %s, accruals %s, net_issuance %s. The last three are "
                "sign-flipped: higher is better." % (
                    factors.get("period_end"), factors.get("filed_at"),
                    _num(factors.get("gp_a")), _num(factors.get("asset_growth")),
                    _num(factors.get("accruals")), _num(factors.get("net_issuance"))))
    elif status == "fetch_failed":
        fund = "SEC fundamentals could not be fetched; treat them as unknown, not as zero."
    elif status == "no_facts":
        fund = "No SEC fundamentals (a fund, a foreign filer, or no XBRL facts on file)."
    else:
        fund = "Not supplied."

    filings = []
    for f in d.get("filings") or ():
        filings.append("- %s accepted %s (period %s)%s:\n  %s" % (
            f.get("form"), f.get("available_at"), f.get("report_date") or "n/a",
            ", items %s" % f.get("items") if f.get("items") else "",
            _clip(f.get("excerpt"), FILING_EXCERPT_CHARS)))

    news = []
    for n in d.get("news") or ():
        line = "[%s] %s" % (n.get("id"), _clip(n.get("headline"), 240))
        if n.get("summary"):
            line += " -- %s" % _clip(n.get("summary"), NEWS_SUMMARY_CHARS)
        if n.get("content"):
            line += " -- %s" % _clip(n.get("content"), NEWS_CONTENT_CHARS)
        news.append(line)

    why = "Trends: %s. %s" % (_list(d.get("trend_names")), _clip(d.get("why"), 400))
    return "\n\n".join([
        head,
        "Task: write a research note on %s as of today.\n"
        '- symbol is "%s".\n'
        "- conviction is 0 to 10: 10 = strong evidence a trend lifts it over the next 1 to "
        "6 months, 5 = no edge, 0 = expect it to fall.\n"
        "- thesis: the case in a few sentences, tied to the trend.\n"
        "- breaks_if: 2 to 4 concrete, observable conditions that would end the thesis.\n"
        "- priced_in: how much of the thesis the price already reflects (little, partly, "
        "mostly, fully), judged from the price trend below.\n"
        "- %s\n"
        "- risks: the main ways this goes wrong.\n"
        "- For an ETF there are no company filings; judge it on the trend, tape and news."
        % (sym, sym, status_rule),
        _section("Why it is a candidate", why),
        _section("Earlier note (last week's study)", prior_text),
        _section("Fundamentals (SEC XBRL, filed before today)", fund),
        _section("Recent filings, newest first (excerpts)", "\n".join(filings)),
        _section("Price trend (daily closes before today's session)",
                 _stats_line(d.get("trend_stats"))),
        _section("News tagged %s, newest first" % sym, "\n".join(news)),
    ])


def book_prompt(scan, notes, as_of: datetime) -> str:
    """Stage 5: the ranked book proposal."""
    lev = _sorted(book.LEVERAGED_ETFS)
    inv = _sorted(book.INVERSE_ETFS)
    defensive = _sorted(book.DEFENSIVE_ETFS)
    rules = (
        "Task: propose this week's book from the researched names below.\n"
        "Rules (they are enforced again after you reply; a pick that breaks one is "
        "dropped or scaled down):\n"
        "- %d-%d picks, ranked strongest first. Each target_weight is 0 to %.2f; aim for "
        "about 0.08 to 0.12 each.\n"
        "- Pick only researched names or allowed ETFs. Do not pick a name whose thesis is "
        "broken. conviction is the note's conviction.\n"
        "- role: core for trend picks; defensive for %s (and GLD when held as a hedge); "
        "leveraged for %s; inverse for %s.\n"
        "- defensive_share (0 to 1) is the part of the book the regime call puts in "
        "defensive instruments. It is split across the defensive picks by their "
        "target weights (all of it goes to BIL if there are none), and a defensive pick "
        "counts only when defensive_share is above 0; the other picks share the rest. "
        "Higher in risk_off, highest in crisis.\n"
        "- Leveraged and inverse picks together are at most %.2f of the book, only on "
        "high-conviction trends; justify them in notes.\n"
        "- Ideas are ridden for about 1 to 6 months; there is no profit-taking, and a "
        "pick is replaced only for a clearly better idea.\n"
        "- notes: why this defensive_share, and why any leveraged or inverse pick."
        % (book.MIN_POSITIONS, book.MAX_POSITIONS, book.DEFAULTS["max_single_weight"],
           defensive, lev, inv, book.DEFAULTS["max_levered_inverse_weight"])
    )
    lines = [_note_line(n) for n in _plain(notes or [])]
    return "\n\n".join([
        header(as_of),
        rules,
        _scan_block(scan),
        _section("Research notes", "\n".join(lines)),
        UNIVERSE,
    ])
