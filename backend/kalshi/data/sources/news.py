"""Match news for the LLM analyst panel. Pulls recent articles (injuries, lineups,
team news, who's playing) for a fixture via the repo's free Google News helper,
and summarizes them into a compact block for the prompt. summarize_news_items is
pure/tested; fetch_match_news is integration."""
from __future__ import annotations


def summarize_news_items(items: list, limit: int = 12) -> str:
    """Compact article dicts into a bulleted text block for the LLM prompt."""
    lines = []
    for a in (items or [])[:limit]:
        title = (a.get("title") or a.get("headline") or "").strip()
        if not title:
            continue
        desc = (a.get("description") or "").strip()
        lines.append(f"- {title}" + (f" — {desc[:180]}" if desc else ""))
    return "\n".join(lines)


def fetch_match_news(home: str, away: str, *, max_total: int = 18) -> list:  # pragma: no cover - integration
    """Recent news for the match — the raw material the analyst reasons over:
    match preview, predicted lineups / starting XI, injuries & suspensions, recent
    form, and head-to-head. Empty on failure (analyst then reasons from stats alone)."""
    try:
        from strategies.google_news import fetch_google_news
        keywords = [
            f"{home} vs {away} preview prediction",
            f"{home} predicted lineup starting XI injury suspension team news",
            f"{away} predicted lineup starting XI injury suspension team news",
            f"{home} {away} head to head form",
        ]
        return fetch_google_news(keywords=keywords, max_total=max_total, max_results_per_keyword=6) or []
    except Exception:
        return []
