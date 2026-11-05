"""TR stage schemas, prompts and the quota-aware LLM wrapper (Task 6).

No network: the provider function ``llm_utils.call_structured_llm_by_provider``
is replaced by a fake in every test that calls the model.
"""
from __future__ import annotations

import os
import re
import sys
from datetime import datetime, timedelta, timezone

import pytest

_backend = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

import llm_utils  # noqa: E402
from chatbot.claude_cli_provider import ClaudeCliRateLimitError  # noqa: E402
from trend_research import llm, prompts, schemas  # noqa: E402

# Monday 2025-08-04, 09:25 ET (EDT) = 13:25 UTC.
AS_OF = datetime(2025, 8, 4, 13, 25, tzinfo=timezone.utc)
HEADER = ("Today is Monday 2025-08-04 before the US market open. Use ONLY the material "
          "below; do not use knowledge of events after this date.")
PORTFOLIO_WORDS = re.compile(r"\b(nav|cash|positions?|holdings?|portfolios?)\b", re.I)


# -- fixtures --------------------------------------------------------------------

def _digest():
    return {
        "window": {"start": "2025-07-28T13:00:00Z", "end": "2025-08-04T13:00:00Z",
                   "decision_time": "2025-08-04T13:25:00Z"},
        "headlines": [
            {"id": "2025-08-01T12:00:00Z|alpaca|101", "t": "2025-08-01T12:00:00Z",
             "headline": "Chipmaker lifts AI server outlook", "symbols": ["NVDA", "AMD"]},
            {"id": "2025-08-02T09:00:00Z|alpaca|102", "t": "2025-08-02T09:00:00Z",
             "headline": "Tariff talks stall on steel", "symbols": []},
        ],
        "themes": [
            {"theme": "TARIFFS", "days": 5, "volume": 1200, "share": 0.012, "tone": -2.1,
             "baseline_share": 0.008, "share_change": 0.5,
             "titles": [{"id": "2025-08-01T10:00:00Z|gdelt|ab12", "t": "2025-08-01T10:00:00Z",
                         "headline": "New steel duties announced", "domain": "example.com"}]},
        ],
        "macro": [
            {"series": "CPIAUCSL", "obs_date": "2025-06-01", "value": 321.5,
             "prev_obs_date": "2025-05-01", "prev_value": 320.6, "change": 0.9,
             "available_at": "2025-07-15T12:30:00Z", "new_this_week": False},
        ],
        "calendar": [
            {"date": "2025-08-12", "kind": "cpi", "label": "CPI (July)",
             "available_at": "2025-01-01T00:00:00Z"},
        ],
        "tape": {"as_of_session": "2025-08-04",
                 "symbols": {"SPY": {"last_session": "2025-08-01", "last_close": 621.7,
                                     "sessions": 252, "ret_1w": -0.023, "ret_1m": 0.004,
                                     "ret_3m": 0.08, "sma50_dist": 0.01, "sma200_dist": 0.06},
                             "UUP": None},
                 "vix": 20.4},
    }


def _scan():
    return schemas.TrendScan(
        trends=[schemas.Trend(
            name="AI data-center build-out", category="tech", state="strengthening",
            confidence=0.8, evidence_ids=["2025-08-01T12:00:00Z|alpaca|101"],
            beneficiaries=["NVDA", "AVGO"], losers=[], summary="Capex keeps rising.")],
        regime="neutral", regime_reasons="Mixed tape, VIX near 20.")


def _note(sym="NVDA", conviction=8.0, status="new"):
    return schemas.ResearchNote(
        symbol=sym, conviction=conviction, thesis="AI capex.", breaks_if=["capex cut"],
        priced_in="partly", thesis_status=status, risks="Export rules.")


def _dossier():
    return {
        "factors": {"symbol": "NVDA", "as_of": "2025-08-03", "period_end": "2025-04-27",
                    "filed_at": "2025-05-28", "gp_a": 0.9, "asset_growth": -0.4,
                    "accruals": 0.02, "net_issuance": 0.01},
        "factors_status": "ok",
        "filings": [{"form": "10-Q", "accession": "0001045810-25-000116",
                     "filing_date": "2025-05-28", "report_date": "2025-04-27",
                     "available_at": "2025-05-28T20:30:00Z", "items": "",
                     "excerpt": "Revenue grew on data center demand."}],
        "trend_stats": {"last_session": "2025-08-01", "last_close": 173.7, "sessions": 252,
                        "ret_1w": -0.01, "ret_1m": 0.1, "ret_3m": 0.4, "sma50_dist": 0.08,
                        "sma200_dist": 0.3, "high_52w_dist": -0.02, "max_drawdown_3m": -0.06},
        "news": [{"id": "2025-08-01T12:00:00Z|alpaca|101", "t": "2025-08-01T12:00:00Z",
                  "source": "alpaca", "published_at": "2025-08-01T12:00:00Z",
                  "headline": "Chipmaker lifts AI server outlook", "summary": "Guide raised.",
                  "content": "x" * 5000, "url": "https://example.com/a",
                  "symbols": ["NVDA", "AMD"]}],
        "prior_note": _note(status="intact").model_dump(),
        "trend_names": ["AI data-center build-out"],
        "why": "Primary beneficiary.",
    }


def _all_prompts():
    return {
        "scan": prompts.trend_scan_prompt(_digest(), [t.model_dump() for t in _scan().trends],
                                          AS_OF),
        "scan_cold": prompts.trend_scan_prompt(_digest(), None, AS_OF),
        "candidates": prompts.candidate_prompt(
            _scan(), [{"symbol": "NVDA", "conviction": 8.0, "thesis": "AI capex."}], AS_OF),
        "research": prompts.research_prompt("NVDA", _dossier(), AS_OF),
        "book": prompts.book_prompt(_scan(), [_note(), _note("GLD", 6.0)], AS_OF),
    }


# -- prompts ---------------------------------------------------------------------

def test_prompt_version():
    assert prompts.PROMPT_VERSION == "tr-v1"


def test_prompt_has_date_header():
    for name, text in _all_prompts().items():
        assert text.startswith(HEADER), name


def test_prompt_header_uses_new_york_date():
    # 03:00 UTC Tuesday is still Monday evening in New York.
    late = datetime(2025, 8, 5, 3, 0, tzinfo=timezone.utc)
    assert prompts.research_prompt("NVDA", {}, late).startswith("Today is Monday 2025-08-04 ")


def test_prompt_rejects_naive_time():
    with pytest.raises(ValueError):
        prompts.research_prompt("NVDA", {}, datetime(2025, 8, 4, 13, 25))


def test_prompt_has_no_portfolio_words():
    texts = dict(_all_prompts(), system=prompts.SYSTEM_PROMPT)
    for name, text in texts.items():
        found = PORTFOLIO_WORDS.findall(text)
        assert not found, (name, found)


def test_prompts_carry_their_inputs():
    p = _all_prompts()
    assert "2025-08-01T12:00:00Z|alpaca|101" in p["scan"]          # evidence ids are citable
    assert "TARIFFS" in p["scan"] and "CPIAUCSL" in p["scan"] and "CPI (July)" in p["scan"]
    assert "AI data-center build-out" in p["scan"]                  # prior trends
    assert "AI data-center build-out" not in p["scan_cold"]         # cold start: none
    assert "NVDA" in p["candidates"] and "AI capex." in p["candidates"]
    assert "Revenue grew on data center demand." in p["research"]
    assert "x" * 5000 not in p["research"]                          # article bodies capped
    assert "intact" in p["research"]                                # prior note shown
    assert "GLD" in p["book"] and "neutral" in p["book"]


def test_prompts_list_universe_and_rules():
    p = _all_prompts()
    for name in ("candidates", "book"):
        for sym in ("GLD", "SLV", "USO", "XLE", "CPER", "URA", "BIL", "TLT", "XLU", "XLP",
                    "SH", "SQQQ", "TQQQ", "SOXL"):
            assert re.search(r"\b%s\b" % sym, p[name]), (name, sym)
        assert "SMH" in p[name]                                     # a rejected theme ETF
    book = p["book"]
    assert "8-12" in book and "0.25" in book and "0.20" in book
    for cat in ("tech", "geopolitics", "economy_rates", "consumer_health"):
        assert cat in p["scan"]


def test_prompts_are_deterministic():
    assert _all_prompts() == _all_prompts()


# -- schemas ---------------------------------------------------------------------

def test_schemas_enforce_ranges():
    with pytest.raises(Exception):
        schemas.ResearchNote(symbol="X", conviction=11, thesis="", breaks_if=[],
                             priced_in="partly", thesis_status="new", risks="")
    with pytest.raises(Exception):
        schemas.BookProposal(picks=[{"symbol": "X", "conviction": 5, "target_weight": 0.3,
                                     "role": "core"}], defensive_share=0.1, notes="")
    with pytest.raises(Exception):
        schemas.Trend(name="x", category="sports", state="emerging", confidence=0.5,
                      evidence_ids=[], beneficiaries=[], losers=[], summary="")


def test_book_proposal_dumps_the_fields_book_py_reads():
    bp = schemas.BookProposal(picks=[{"symbol": "NVDA", "conviction": 8, "target_weight": 0.1,
                                      "role": "core"}], defensive_share=0.2, notes="n")
    assert bp.model_dump() == {"picks": [{"symbol": "NVDA", "conviction": 8.0,
                                          "target_weight": 0.1, "role": "core"}],
                               "defensive_share": 0.2, "notes": "n"}
    assert set(_note().model_dump()) == {"symbol", "conviction", "thesis", "breaks_if",
                                         "priced_in", "thesis_status", "risks"}
    cl = schemas.CandidateList(candidates=[{"symbol": "NVDA", "trend_names": ["a"],
                                            "why": "w"}])
    assert cl.model_dump() == {"candidates": [{"symbol": "NVDA", "trend_names": ["a"],
                                               "why": "w"}]}


# -- StageCaller -----------------------------------------------------------------

class FakeProvider:
    """Stands in for ``call_structured_llm_by_provider``; ``script`` is a list
    of results (an exception instance is raised, a callable is called)."""

    def __init__(self, *script):
        self.script = list(script)
        self.calls = []

    def __call__(self, provider, api_key, model, prompt, output_type, **kw):
        self.calls.append(dict(kw, provider=provider, api_key=api_key, model=model,
                               prompt=prompt, output_type=output_type))
        item = self.script.pop(0) if self.script else output_type.model_validate(
            {"symbol": "NVDA", "conviction": 7, "thesis": "t", "breaks_if": [],
             "priced_in": "partly", "thesis_status": "new", "risks": ""})
        if isinstance(item, BaseException):
            raise item
        if callable(item):
            return item()
        return item


@pytest.fixture
def fake(monkeypatch):
    def install(*script):
        f = FakeProvider(*script)
        monkeypatch.setattr(llm_utils, "call_structured_llm_by_provider", f)
        return f
    return install


def _caller(**kw):
    now = kw.pop("now", lambda: datetime(2025, 8, 4, 10, 0, tzinfo=timezone.utc))
    return llm.StageCaller(kw.pop("provider", "claude-cli"), "claude-opus-4-1",
                           kw.pop("api_key", None), max_calls=kw.pop("max_calls", 10),
                           now=now, **kw)


def test_call_passes_contract_to_llm_utils(fake):
    f = fake()
    note = _caller().call("prompt text", schemas.ResearchNote, max_output_tokens=1500)
    assert isinstance(note, schemas.ResearchNote)
    kw = f.calls[0]
    assert kw["provider"] == "claude-cli" and kw["model"] == "claude-opus-4-1"
    assert kw["prompt"] == "prompt text" and kw["output_type"] is schemas.ResearchNote
    assert kw["tools"] is None and kw["use_prompt_cache"] is True
    assert kw["max_output_tokens"] == 1500 and kw["temperature"] == 0.2
    assert kw["timeout_sec"] == 300
    assert kw["system_prompt"] == prompts.SYSTEM_PROMPT


def test_anthropic_requires_api_key_and_passes_it(fake):
    with pytest.raises(ValueError):
        _caller(provider="anthropic", api_key=None)
    with pytest.raises(ValueError):
        _caller(provider="openai", api_key="k")
    f = fake()
    _caller(provider="anthropic", api_key="sk-test").call("p", schemas.ResearchNote,
                                                         max_output_tokens=10)
    assert f.calls[0]["provider"] == "anthropic" and f.calls[0]["api_key"] == "sk-test"


def test_quota_maps_to_QuotaExhausted(fake):
    fake(ClaudeCliRateLimitError(
        "Claude Code subscription quota hit — retry later "
        "(You've hit your limit · resets 11:30am)"))
    with pytest.raises(llm.QuotaExhausted) as ei:
        _caller().call("p", schemas.ResearchNote, max_output_tokens=10)
    assert ei.value.reset_at == datetime(2025, 8, 4, 11, 30, tzinfo=timezone.utc)


def test_quota_reset_rolls_to_next_day_and_reads_timezone(fake):
    fake(ClaudeCliRateLimitError("You've hit your limit · resets 9pm (America/New_York)"))
    # 02:00 UTC on 08-05 is 22:00 EDT on 08-04: the next 9pm New York is 08-05 21:00 EDT.
    c = _caller(now=lambda: datetime(2025, 8, 5, 2, 0, tzinfo=timezone.utc))
    with pytest.raises(llm.QuotaExhausted) as ei:
        c.call("p", schemas.ResearchNote, max_output_tokens=10)
    assert ei.value.reset_at == datetime(2025, 8, 6, 1, 0, tzinfo=timezone.utc)


def test_quota_swallowed_by_llm_utils_is_still_detected(fake):
    """llm_utils catches ClaudeCliRateLimitError and returns None; the error
    text survives only in the last-call metadata."""
    def swallowed():
        llm_utils._LAST_STRUCTURED_LLM_CALL.data = {
            "provider": "claude-cli", "ok": False, "is_terminal": True,
            "error": "Claude Code subscription quota hit — retry later "
                     "(You've hit your limit · resets 11:50pm (UTC))"}
        return None
    fake(swallowed)
    with pytest.raises(llm.QuotaExhausted) as ei:
        _caller().call("p", schemas.ResearchNote, max_output_tokens=10)
    assert ei.value.reset_at == datetime(2025, 8, 4, 23, 50, tzinfo=timezone.utc)


def test_session_limit_text_without_reset_time(fake):
    fake(RuntimeError("Session limit reached"))
    with pytest.raises(llm.QuotaExhausted) as ei:
        _caller().call("p", schemas.ResearchNote, max_output_tokens=10)
    assert ei.value.reset_at is None


def test_http_429_with_retry_after(fake):
    def limited():
        llm_utils._LAST_STRUCTURED_LLM_CALL.data = {
            "provider": "anthropic", "ok": False,
            "error": "status_code: 429, rate_limit_error; retry-after: 120"}
        return None
    fake(limited)
    with pytest.raises(llm.QuotaExhausted) as ei:
        _caller(provider="anthropic", api_key="k").call("p", schemas.ResearchNote,
                                                       max_output_tokens=10)
    assert ei.value.reset_at == datetime(2025, 8, 4, 10, 2, tzinfo=timezone.utc)


def test_stale_metadata_from_an_earlier_call_is_ignored(fake):
    llm_utils._LAST_STRUCTURED_LLM_CALL.data = {"ok": False, "error": "quota hit earlier"}
    fake(lambda: None)
    with pytest.raises(llm.StageFailed):
        _caller().call("p", schemas.ResearchNote, max_output_tokens=10)


def test_validation_error_quoting_quota_is_not_a_quota_hit(fake):
    def invalid():
        llm_utils._LAST_STRUCTURED_LLM_CALL.data = {
            "ok": False, "error": "ClaudeCliValidationError: schema mismatch in reply "
                                  "'steel import quotas tighten'"}
        return None
    fake(invalid)
    with pytest.raises(llm.StageFailed):
        _caller().call("p", schemas.ResearchNote, max_output_tokens=10)
    assert llm.is_quota_error("Anthropic API: quota exceeded for this key")
    assert not llm.is_quota_error("request id 14290 failed")


def test_budget_exceeded_after_max_calls(fake):
    f = fake()
    c = _caller(max_calls=2)
    c.call("p1", schemas.ResearchNote, max_output_tokens=10)
    c.call("p2", schemas.ResearchNote, max_output_tokens=10)
    assert c.calls_used == 2
    with pytest.raises(llm.BudgetExceeded):
        c.call("p3", schemas.ResearchNote, max_output_tokens=10)
    assert len(f.calls) == 2 and c.calls_used == 2


def test_none_result_raises_StageFailed(fake):
    def failed():
        llm_utils._LAST_STRUCTURED_LLM_CALL.data = {
            "ok": False, "error": "Output JSON did not match the requested schema"}
        return None
    fake(failed)
    with pytest.raises(llm.StageFailed) as ei:
        _caller().call("p", schemas.ResearchNote, max_output_tokens=10)
    assert "did not match" in str(ei.value)


def test_wrong_shape_result_raises_StageFailed(fake):
    fake({"symbol": "NVDA", "conviction": 42})
    with pytest.raises(llm.StageFailed):
        _caller().call("p", schemas.ResearchNote, max_output_tokens=10)


def test_dict_result_is_validated(fake):
    fake({"symbol": "NVDA", "conviction": 7, "thesis": "t", "breaks_if": [],
          "priced_in": "partly", "thesis_status": "new", "risks": ""})
    out = _caller().call("p", schemas.ResearchNote, max_output_tokens=10)
    assert isinstance(out, schemas.ResearchNote) and out.conviction == 7


def test_other_provider_exception_raises_StageFailed(fake):
    fake(RuntimeError("subprocess crashed"))
    with pytest.raises(llm.StageFailed):
        _caller().call("p", schemas.ResearchNote, max_output_tokens=10)


def test_stage_token_budgets():
    assert llm.MAX_OUTPUT_TOKENS == {"scan": 4000, "candidates": 2000, "research": 1500,
                                     "book": 2000}
