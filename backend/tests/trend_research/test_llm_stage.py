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
from pydantic import ValidationError

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
    with pytest.raises(ValidationError):
        schemas.ResearchNote(symbol="X", conviction=11, thesis="", breaks_if=[],
                             priced_in="partly", thesis_status="new", risks="")
    with pytest.raises(ValidationError):
        schemas.BookProposal(picks=[{"symbol": "X", "conviction": 5, "target_weight": 0.3,
                                     "role": "core"}], defensive_share=0.1, notes="")
    with pytest.raises(ValidationError):
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


@pytest.fixture(autouse=True)
def _cli_with_safe_mode(monkeypatch):
    """No subprocess: the claude CLI's --help lists --safe-mode."""
    monkeypatch.setattr(llm, "_default_cli_help", lambda path: "  --safe-mode  Start with ...")
    llm._CLI_HELP_CACHE.clear()
    yield
    llm._CLI_HELP_CACHE.clear()


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
    tag = llm.prompt_tag(schemas.ResearchNote, prompts.PROMPT_VERSION)
    assert kw["prompt"] == tag + "\n\nprompt text" and kw["output_type"] is schemas.ResearchNote
    assert kw["tools"] is None and kw["use_prompt_cache"] is True
    assert kw["max_output_tokens"] == 1500 and kw["temperature"] == 0.2
    assert kw["timeout_sec"] == 300
    assert kw["system_prompt"] == prompts.SYSTEM_PROMPT
    # llm_utils' own retries are off: _stage owns them, and every spawn counts.
    assert kw["retries"] == 0 and kw["output_retries"] == 0
    assert kw["provider_config"]["extra_args"] == ["--safe-mode"]


def test_anthropic_requires_api_key_and_unknown_providers_are_refused():
    with pytest.raises(ValueError):
        _caller(provider="anthropic", api_key=None)
    with pytest.raises(ValueError):
        _caller(provider="openai", api_key="k")


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


def test_rate_limit_text_with_retry_after(fake):
    def limited():
        llm_utils._LAST_STRUCTURED_LLM_CALL.data = {
            "provider": "claude-cli", "ok": False,
            "error": "status_code: 429, rate_limit_error; retry-after: 120"}
        return None
    fake(limited)
    with pytest.raises(llm.QuotaExhausted) as ei:
        _caller().call("p", schemas.ResearchNote, max_output_tokens=10)
    assert ei.value.reset_at == datetime(2025, 8, 4, 10, 2, tzinfo=timezone.utc)


def test_stale_metadata_from_an_earlier_call_is_ignored(fake):
    llm_utils._LAST_STRUCTURED_LLM_CALL.data = {"ok": False, "error": "quota hit earlier"}
    fake(lambda: None)
    # No error text at all: nothing says the model answered, so nothing may
    # be written for the week.
    with pytest.raises(llm.TransientError):
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


def test_validation_error_quoting_429_and_quotas_is_StageFailed(fake):
    def invalid():
        llm_utils._LAST_STRUCTURED_LLM_CALL.data = {
            "ok": False,
            "error": "Pydantic validation failed: 1 validation error for ResearchNote; "
                     "payload_preview={'thesis': 'a $429 million order as export quotas "
                     "tighten; the rate limit on shipments eases'}"}
        return None
    fake(invalid)
    with pytest.raises(llm.StageFailed):
        _caller().call("p", schemas.ResearchNote, max_output_tokens=10)
    assert not llm.is_quota_error("not valid JSON: '$429 million'")
    # claude-cli's own phrases are trusted even next to a validation hint.
    assert llm.is_quota_error("schema retry aborted: You've hit your limit · resets 3pm")


def _cache_hit_result():
    llm_utils._LAST_STRUCTURED_LLM_CALL.data = {"ok": True, "prompt_cache_hit": True}
    return schemas.ResearchNote(symbol="NVDA", conviction=7, thesis="t", breaks_if=[],
                                priced_in="partly", thesis_status="new", risks="")


def test_prompt_cache_hits_do_not_consume_the_budget(fake):
    f = fake(*([_cache_hit_result] * 5))
    c = _caller(max_calls=2)
    for _ in range(5):
        c.call("p", schemas.ResearchNote, max_output_tokens=10)
    assert c.calls_used == 0 and len(f.calls) == 5
    c.call("real 1", schemas.ResearchNote, max_output_tokens=10)
    c.call("real 2", schemas.ResearchNote, max_output_tokens=10)
    assert c.calls_used == 2
    with pytest.raises(llm.BudgetExceeded):            # stops before a real call
        c.call("real 3", schemas.ResearchNote, max_output_tokens=10)
    assert len(f.calls) == 7


def test_budget_is_thread_safe(fake):
    import threading
    f = fake()
    c = _caller(max_calls=40)
    errors = []

    def worker():
        for _ in range(10):
            try:
                c.call("p", schemas.ResearchNote, max_output_tokens=10)
            except llm.BudgetExceeded:
                errors.append(1)
    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert c.calls_used == 40 and len(f.calls) == 40 and len(errors) == 40


def test_programming_errors_propagate(fake):
    fake(TypeError("bad argument"))
    with pytest.raises(TypeError):
        _caller().call("p", schemas.ResearchNote, max_output_tokens=10)


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


def test_a_crash_is_transient_not_a_stage_failure(fake):
    fake(RuntimeError("subprocess crashed"))
    with pytest.raises(llm.TransientError):
        _caller().call("p", schemas.ResearchNote, max_output_tokens=10)


def test_stage_token_budgets():
    # Anthropic caps (ruling A7): scan >= 6000, candidates >= 4000, book >= 3000,
    # research >= 2500.
    assert llm.MAX_OUTPUT_TOKENS["scan"] >= 6000
    assert llm.MAX_OUTPUT_TOKENS["candidates"] >= 4000
    assert llm.MAX_OUTPUT_TOKENS["book"] >= 3000
    assert llm.MAX_OUTPUT_TOKENS["research"] >= 2500


# -- fix wave A: failure classes, attempt counting, cache tag (A3, A7) ---------------

@pytest.mark.parametrize("text", [
    "ClaudeCliTimeoutError: claude timed out after 300s",
    "ClaudeCliCrashError: claude exited 1 with no stdout",
    "anthropic HTTP 529 overloaded_error: Overloaded",
    "ClaudeCliNotLoggedInError: not logged in",
    "ConnectError: [Errno 111] Connection refused",
])
def test_infra_failures_are_transient(fake, text):
    def failed():
        llm_utils._LAST_STRUCTURED_LLM_CALL.data = {"ok": False, "error": text}
        return None
    fake(failed)
    with pytest.raises(llm.TransientError):
        _caller().call("p", schemas.ResearchNote, max_output_tokens=10)
    assert llm.classify_failure(text) == "transient"


def test_overloaded_is_never_a_quota_hit():
    assert not llm.is_quota_error("anthropic HTTP 529 overloaded_error: Overloaded")


@pytest.mark.parametrize("text,reset", [
    ("5-hour limit reached ∙ resets 3pm", datetime(2025, 8, 4, 15, 0, tzinfo=timezone.utc)),
    ("Weekly limit reached ∙ resets in 2 days",
     datetime(2025, 8, 6, 10, 0, tzinfo=timezone.utc)),
    ("Claude AI usage limit reached|1754400000", None),
    ("rate_limit_error: Number of request tokens has exceeded your per-minute rate limit",
     None),
])
def test_quota_wordings_are_detected(fake, text, reset):
    fake(RuntimeError(text))
    with pytest.raises(llm.QuotaExhausted) as ei:
        _caller().call("p", schemas.ResearchNote, max_output_tokens=10)
    assert ei.value.reset_at == reset


def test_every_cli_spawn_counts_toward_the_budget(fake):
    def two_spawns():
        llm_utils._LAST_STRUCTURED_LLM_CALL.data = {
            "ok": True, "attempted_models": ["claude-opus-4-1", "claude-opus-4-1"]}
        return _note()
    def two_failed_spawns():
        llm_utils._LAST_STRUCTURED_LLM_CALL.data = {
            "ok": False, "attempted_models": ["m", "m"],
            "error": "claude output failed Pydantic validation: bad"}
        return None
    fake(two_spawns, two_failed_spawns)
    c = _caller(max_calls=10)
    c.call("p", schemas.ResearchNote, max_output_tokens=10)
    assert c.calls_used == 2
    with pytest.raises(llm.StageFailed):
        c.call("p", schemas.ResearchNote, max_output_tokens=10)
    assert c.calls_used == 4


def test_prompt_tag_changes_with_version_schema_and_system_prompt(monkeypatch):
    a = llm.prompt_tag(schemas.ResearchNote, "tr-v1")
    assert a.startswith("TR prompt_version=tr-v1 schema=") and " system=" in a
    assert llm.prompt_tag(schemas.ResearchNote, "tr-v2") != a
    assert llm.prompt_tag(schemas.BookProposal, "tr-v1") != a
    monkeypatch.setattr(prompts, "SYSTEM_PROMPT", prompts.SYSTEM_PROMPT + " Be terse.")
    assert llm.prompt_tag(schemas.ResearchNote, "tr-v1") != a


def test_prompt_version_reaches_the_prompt(fake):
    f = fake()
    _caller(prompt_version="tr-v9").call("p", schemas.ResearchNote, max_output_tokens=10)
    assert f.calls[0]["prompt"].startswith("TR prompt_version=tr-v9 ")


# -- fix wave A11: claude-cli isolation and no model override ---------------------------

def test_fallback_model_and_other_flags_are_refused():
    with pytest.raises(ValueError, match="fallback"):
        _caller(provider_config={"extra_args": "--fallback-model claude-opus-4-5"})
    with pytest.raises(ValueError):
        _caller(provider_config={"extra_args": "--model claude-opus-4-5"})
    with pytest.raises(ValueError):
        _caller(provider_config={"extra_args": ["--fallback-model", "x"]})


def test_model_aliases_are_refused():
    with pytest.raises(ValueError, match="alias"):
        llm.StageCaller("claude-cli", "sonnet", None, max_calls=1)


def test_effort_is_kept_and_safe_mode_added(fake):
    f = fake()
    c = _caller(provider_config={"extra_args": "--effort high", "cli_path": "claude"})
    c.call("p", schemas.ResearchNote, max_output_tokens=10)
    assert f.calls[0]["provider_config"]["extra_args"] == ["--effort", "high", "--safe-mode"]
    assert f.calls[0]["provider_config"]["cli_path"] == "claude"


def test_a_cli_without_safe_mode_is_refused_before_any_call(fake, monkeypatch):
    monkeypatch.setattr(llm, "_default_cli_help", lambda path: "  --bare  Minimal mode")
    llm._CLI_HELP_CACHE.clear()
    f = fake()
    c = _caller()
    with pytest.raises(llm.TransientError, match="safe-mode"):
        c.call("p", schemas.ResearchNote, max_output_tokens=10)
    assert f.calls == [] and c.calls_used == 0


# -- fix wave A6: the direct Anthropic path ---------------------------------------------

class FakeResp:
    def __init__(self, status, payload=None, headers=None):
        self.status_code = status
        self._payload = payload if payload is not None else {}
        self.headers = headers or {}

    def json(self):
        return self._payload


def _tool_reply(payload, stop="tool_use"):
    return FakeResp(200, {"stop_reason": stop, "content": [
        {"type": "text", "text": "Here you go."},
        {"type": "tool_use", "id": "t1", "name": llm.ANTHROPIC_TOOL, "input": payload}]})


class FakeHttp:
    def __init__(self, *script):
        self.script = list(script)
        self.calls = []

    def __call__(self, url, *, headers, json, timeout):
        self.calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


KEY = "sk-ant-test-0123456789abcdef"


@pytest.fixture
def no_cache(monkeypatch):
    stored = {}
    monkeypatch.setattr(llm, "_cache_get", lambda prompt, key: stored.get((prompt, key)))
    monkeypatch.setattr(llm, "_cache_put",
                        lambda prompt, key, raw: stored.__setitem__((prompt, key), raw))
    monkeypatch.setattr(llm_utils, "call_structured_llm_by_provider",
                        lambda *a, **k: pytest.fail("anthropic must not go through llm_utils"))
    return stored


def _anthropic(http, sleeps=None, **kw):
    return llm.StageCaller("anthropic", "claude-sonnet-4-5", KEY, max_calls=kw.pop("max_calls", 20),
                           http_post=http, sleep=(sleeps.append if sleeps is not None
                                                  else lambda s: None),
                           now=lambda: datetime(2025, 8, 4, 10, 0, tzinfo=timezone.utc), **kw)


def _scan_payload():
    return _scan().model_dump()


def test_anthropic_forces_one_tool_with_the_output_schema(no_cache):
    http = FakeHttp(_tool_reply(_scan_payload()))
    out = _anthropic(http).call("scan prompt", schemas.TrendScan, max_output_tokens=8000)
    assert isinstance(out, schemas.TrendScan) and out.trends[0].name.startswith("AI")
    req = http.calls[0]
    assert req["url"] == "https://api.anthropic.com/v1/messages"
    assert req["headers"]["x-api-key"] == KEY
    assert req["headers"]["anthropic-version"] == llm.ANTHROPIC_VERSION
    body = req["json"]
    assert body["model"] == "claude-sonnet-4-5" and body["max_tokens"] == 8000
    assert body["system"] == prompts.SYSTEM_PROMPT
    assert body["tool_choice"] == {"type": "tool", "name": llm.ANTHROPIC_TOOL}
    assert len(body["tools"]) == 1
    schema_text = __import__("json").dumps(body["tools"][0]["input_schema"])
    assert "$ref" not in schema_text and "$defs" not in schema_text
    assert body["tools"][0]["input_schema"]["properties"]["trends"]["items"]["properties"][
        "confidence"]["maximum"] == 1.0
    assert body["messages"][0]["content"].startswith("TR prompt_version=")
    assert "tools" in body and "web_search" not in schema_text


def test_anthropic_overload_is_retried_with_backoff(no_cache):
    sleeps = []
    http = FakeHttp(FakeResp(529, {"error": {"type": "overloaded_error", "message": "Overloaded"}}),
                    ConnectionError("reset by peer"),
                    _tool_reply(_note().model_dump()))
    c = _anthropic(http, sleeps)
    assert c.call("p", schemas.ResearchNote, max_output_tokens=2500).symbol == "NVDA"
    assert len(sleeps) == 2 and c.calls_used == 3          # every HTTP attempt counts


def test_anthropic_persistent_5xx_is_transient(no_cache):
    http = FakeHttp(*[FakeResp(500, {"error": {"type": "api_error", "message": "boom"}})]
                    * llm.ANTHROPIC_ATTEMPTS)
    with pytest.raises(llm.TransientError):
        _anthropic(http).call("p", schemas.ResearchNote, max_output_tokens=10)
    assert len(http.calls) == llm.ANTHROPIC_ATTEMPTS


def test_anthropic_429_waits_retry_after_then_is_a_quota_hit(no_cache):
    sleeps = []
    r429 = FakeResp(429, {"error": {"type": "rate_limit_error", "message": "slow down"}},
                    {"retry-after": "30"})
    http = FakeHttp(*[r429] * llm.ANTHROPIC_ATTEMPTS)
    with pytest.raises(llm.QuotaExhausted) as ei:
        _anthropic(http, sleeps).call("p", schemas.ResearchNote, max_output_tokens=10)
    assert sleeps == [30.0] * (llm.ANTHROPIC_ATTEMPTS - 1)
    assert ei.value.reset_at == datetime(2025, 8, 4, 10, 0, 30, tzinfo=timezone.utc)


def test_anthropic_credit_exhaustion_is_a_quota_hit(no_cache):
    http = FakeHttp(FakeResp(400, {"error": {
        "type": "invalid_request_error",
        "message": "Your credit balance is too low to access the Anthropic API."}}))
    with pytest.raises(llm.QuotaExhausted) as ei:
        _anthropic(http).call("p", schemas.ResearchNote, max_output_tokens=10)
    assert ei.value.reset_at is None and len(http.calls) == 1


def test_anthropic_auth_failure_is_transient_and_never_shows_the_key(no_cache, caplog):
    http = FakeHttp(FakeResp(401, {"error": {"type": "authentication_error",
                                             "message": "invalid x-api-key %s" % KEY}}))
    with pytest.raises(llm.TransientError) as ei:
        _anthropic(http).call("p", schemas.ResearchNote, max_output_tokens=10)
    assert KEY not in str(ei.value)
    http = FakeHttp(*[ConnectionError("proxy said %s" % KEY)] * llm.ANTHROPIC_ATTEMPTS)
    with caplog.at_level("DEBUG"):
        with pytest.raises(llm.TransientError) as ei:
            _anthropic(http).call("p", schemas.ResearchNote, max_output_tokens=10)
    assert KEY not in str(ei.value) and KEY not in caplog.text


@pytest.mark.parametrize("resp", [
    FakeResp(400, {"error": {"type": "invalid_request_error", "message": "prompt is too long"}}),
    _tool_reply({"symbol": "NVDA"}, stop="max_tokens"),
    FakeResp(200, {"stop_reason": "refusal", "content": [{"type": "text", "text": "no"}]}),
    _tool_reply({"symbol": "NVDA", "conviction": 42}),
])
def test_anthropic_answers_without_a_valid_result_are_stage_failures(no_cache, resp):
    with pytest.raises(llm.StageFailed):
        _anthropic(FakeHttp(resp)).call("p", schemas.ResearchNote, max_output_tokens=10)


def test_anthropic_replies_are_cached_and_replayed_free(no_cache):
    http = FakeHttp(_tool_reply(_note().model_dump()))
    c = _anthropic(http)
    c.call("p", schemas.ResearchNote, max_output_tokens=10)
    assert c.calls_used == 1
    assert all(key.startswith("anthropic:") for _prompt, key in no_cache)
    again = c.call("p", schemas.ResearchNote, max_output_tokens=10)   # no HTTP left
    assert again.symbol == "NVDA" and c.calls_used == 1 and len(http.calls) == 1


def test_anthropic_budget_stops_before_a_request(no_cache):
    http = FakeHttp(FakeResp(529, {"error": {"type": "overloaded_error"}}),
                    FakeResp(529, {"error": {"type": "overloaded_error"}}))
    with pytest.raises(llm.BudgetExceeded):
        _anthropic(http, max_calls=2).call("p", schemas.ResearchNote, max_output_tokens=10)
    assert len(http.calls) == 2


# -- fix wave A8 / A10: what reaches a prompt --------------------------------------------

def test_news_html_is_stripped_and_capped():
    d = _dossier()
    d["news"][0]["content"] = ('<p>Shares <a href="https://x.example/q?a=1&amp;b=2">rose</a>'
                               ' &amp; held.</p><script>var x = 1;</script>' + "y" * 5000)
    d["news"][0]["summary"] = "<b>Guide</b> raised &mdash; again"
    text = prompts.research_prompt("NVDA", d, AS_OF)
    assert "<p>" not in text and "href" not in text and "<script" not in text
    assert "var x" not in text and "Shares rose & held." in text
    assert "Guide raised — again" in text
    line = [ln for ln in text.splitlines() if ln.startswith("[2025-08-01T12:00:00Z|alpaca|101]")][0]
    assert len(line) < 240 + prompts.NEWS_SUMMARY_CHARS + prompts.NEWS_CONTENT_CHARS + 80


def test_no_price_level_reaches_a_prompt():
    """Ruling R28: split-adjusted levels would reveal a later split."""
    from datetime import date as _date
    from trend_research import market_tape
    days, d = [], _date(2024, 6, 3)
    while d < _date(2025, 8, 2):
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    bars = [{"t": "%sT04:00:00Z" % x.isoformat(), "c": 1234.5678 + i * 0.37}
            for i, x in enumerate(days)]
    loader = lambda syms, a, b: {s: bars for s in syms}          # noqa: E731
    stats = market_tape.symbol_trend_stats("NVDA", AS_OF, bars_loader=loader)
    from db.fake import FakeStore
    tape = market_tape.market_tape(AS_OF, bars_loader=loader, store=FakeStore())
    assert stats["vol_3m"] is not None and "last_close" not in stats
    d = dict(_dossier(), trend_stats=stats)
    dig = dict(_digest(), tape=tape)
    texts = [prompts.research_prompt("NVDA", d, AS_OF),
             prompts.trend_scan_prompt(dig, None, AS_OF)]
    levels = {"%.4g" % c["c"] for c in bars} | {"%.1f" % c["c"] for c in bars} \
        | {str(int(c["c"])) for c in bars}
    for text in texts:
        found = [lv for lv in levels if lv in text]
        assert found == [], found[:5]
    assert "3m volatility" in texts[0]
