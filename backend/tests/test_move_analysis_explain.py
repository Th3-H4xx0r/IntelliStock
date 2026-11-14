"""Citation rules for move explanations (move_analysis.explain) and the news
window (move_analysis.news)."""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

import pytest

_BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from move_analysis import explain as ex  # noqa: E402
from move_analysis import news as nw  # noqa: E402

EVENT = {"symbol": "NVDA", "date": "2026-10-02", "ret_pct": -7.9, "market_pct": -0.8,
         "sector_pct": -1.1, "specific_pct": -6.0, "sector_etf": "XLK"}
ARTICLES = [
    {"title": "Broker cuts NVDA to hold", "summary": "Cites export limits.",
     "url": "https://example.com/a", "source": "Benzinga",
     "published_at": "2026-10-02T12:31:00Z"},
    {"title": "Chip stocks slide", "summary": "", "url": "https://example.com/b",
     "source": "Benzinga", "published_at": "2026-10-02T14:00:00Z"},
]


def _answer(**over):
    base = {"category": "analyst", "headline": "Downgrade on export limits",
            "explanation": "A broker cut its rating.", "confidence": "medium",
            "cited": [1]}
    base.update(over)
    return base


def test_a_cited_answer_is_kept_with_its_sources():
    out = ex.explain_event(EVENT, ARTICLES, lambda p, s: _answer())
    assert out["category"] == "analyst"
    assert out["headline"] == "Downgrade on export limits"
    assert [s["url"] for s in out["sources"]] == ["https://example.com/a"]


def test_an_uncited_cause_is_stored_as_no_catalyst():
    out = ex.explain_event(EVENT, ARTICLES, lambda p, s: _answer(cited=[]))
    assert out["category"] == "no_catalyst"
    assert out["headline"] == ex.NO_CATALYST_HEADLINE
    assert out["sources"] == [] and out["confidence"] == "low"
    assert "cited no article" in out["explanation"]


def test_invented_article_numbers_are_dropped():
    out = ex.explain_event(EVENT, ARTICLES, lambda p, s: _answer(cited=[7, "x", 0, 2, 2]))
    assert [s["url"] for s in out["sources"]] == ["https://example.com/b"]
    only_invented = ex.explain_event(EVENT, ARTICLES, lambda p, s: _answer(cited=[9]))
    assert only_invented["category"] == "no_catalyst"


def test_unknown_category_and_confidence_are_coerced():
    out = ex.finalize(_answer(category="aliens", confidence="certain"), ARTICLES)
    assert out["category"] == "no_catalyst"
    out = ex.finalize(_answer(confidence="certain"), ARTICLES)
    assert out["confidence"] == "low"


def test_the_model_cannot_answer_market():
    assert ex.finalize(_answer(category="market"), ARTICLES)["category"] == "no_catalyst"


def test_no_articles_means_no_model_call():
    def _boom(prompt, system):
        raise AssertionError("the model must not be called")

    out = ex.explain_event(EVENT, [], _boom)
    assert out["category"] == "no_catalyst" and out["sources"] == []


def test_an_empty_answer_is_an_error_not_an_explanation():
    with pytest.raises(ex.ExplainError):
        ex.explain_event(EVENT, ARTICLES, lambda p, s: {})
    with pytest.raises(ex.ExplainError):
        ex.explain_event(EVENT, ARTICLES, lambda p, s: None)


def test_long_text_is_trimmed():
    out = ex.finalize(_answer(headline="h" * 500, explanation="e" * 5000), ARTICLES)
    assert len(out["headline"]) == ex.HEADLINE_CHARS
    assert len(out["explanation"]) == ex.EXPLANATION_CHARS


def test_prompt_numbers_the_articles_and_states_the_split():
    prompt = ex.build_prompt(EVENT, ARTICLES)
    assert "[1] 2026-10-02T12:31:00Z | Benzinga | Broker cuts NVDA to hold" in prompt
    assert "[2] " in prompt and "-6.00%" in prompt and "(XLK)" in prompt


def test_market_explanation_names_the_larger_driver():
    market = ex.market_explanation(dict(EVENT, ret_pct=-5.0, market_pct=-4.0,
                                        sector_pct=-0.5, specific_pct=-0.5))
    assert market["category"] == "market" and market["headline"] == "Moved with the market"
    sector = ex.market_explanation(dict(EVENT, ret_pct=-5.0, market_pct=-1.0,
                                        sector_pct=-3.5, specific_pct=-0.5))
    assert sector["headline"] == "Moved with its sector (XLK)"


def test_category_list_covers_every_category_with_a_label():
    listed = ex.category_list()
    assert [c["key"] for c in listed] == list(ex.CATEGORIES)
    assert all(c["label"] for c in listed)


# ── news window ──────────────────────────────────────────────────────────────


def test_window_runs_from_the_previous_close_to_this_close():
    start, end = nw.news_window("2026-10-01", "2026-10-02")
    assert start == datetime(2026, 10, 1, 20, 0, tzinfo=timezone.utc)   # 16:00 EDT
    assert end == datetime(2026, 10, 2, 20, 15, tzinfo=timezone.utc)    # 16:15 EDT


def test_articles_outside_the_window_are_dropped():
    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"news": [
                {"headline": "Before the window", "created_at": "2026-10-01T15:00:00Z",
                 "url": "https://e.com/1", "symbols": ["NVDA"]},
                {"headline": "Inside", "created_at": "2026-10-02T12:00:00Z",
                 "url": "https://e.com/2", "symbols": ["NVDA"], "summary": "s"},
                {"headline": "After the close", "created_at": "2026-10-02T23:00:00Z",
                 "url": "https://e.com/3", "symbols": ["NVDA"]},
                {"headline": "Undated", "url": "https://e.com/4"},
            ]}

    start, end = nw.news_window("2026-10-01", "2026-10-02")
    got = nw.fetch_alpaca("NVDA", start, end, "k", "s", http_get=lambda *a, **k: _Resp())
    assert [a["title"] for a in got] == ["Inside"]


def test_articles_for_prefers_focused_articles_and_falls_back_to_google():
    def _alpaca(symbol, start, end, key, secret):
        return [
            {"title": "Roundup", "summary": "", "url": "", "source": "B",
             "published_at": "2026-10-02T10:00:00Z", "_breadth": 12},
            {"title": "NVDA downgrade", "summary": "", "url": "", "source": "B",
             "published_at": "2026-10-02T12:00:00Z", "_breadth": 1},
            {"title": "nvda DOWNGRADE", "summary": "", "url": "", "source": "B",
             "published_at": "2026-10-02T12:05:00Z", "_breadth": 1},
        ]

    got = nw.articles_for("NVDA", "2026-10-01", "2026-10-02", credentials=("k", "s"),
                          alpaca=_alpaca, google=lambda *a: [])
    assert [a["title"] for a in got] == ["NVDA downgrade", "Roundup"]
    assert all("_breadth" not in a for a in got)

    fallback = nw.articles_for(
        "NVDA", "2026-10-01", "2026-10-02", credentials=None, alpaca=_alpaca,
        google=lambda *a: [{"title": "G", "summary": "", "url": "", "source": "G",
                            "published_at": "2026-10-02T11:00:00Z", "_breadth": 50}])
    assert [a["title"] for a in fallback] == ["G"]


def test_articles_for_never_raises():
    def _boom(*a, **k):
        raise RuntimeError("down")

    assert nw.articles_for("NVDA", None, "2026-10-02", credentials=("k", "s"),
                           alpaca=_boom, google=_boom) == []


def test_non_http_urls_are_blanked():
    assert nw._http_url("javascript:alert(1)") == ""
    assert nw._http_url("https://ok.example/x") == "https://ok.example/x"
