"""TR research ledger and resumable builder (plan Task 7).

No network: the LLM is a fake StageCaller, and bars, filings and dossiers come
from injected fakes. Everything else reads the FakeStore (or PG_TEST_DSN).
Weeks under test: Mondays 2025-08-04, 08-11, 08-18; ``as_of`` is 09:25 ET
(13:25Z, EDT) on each.
"""
from __future__ import annotations

import os
import re
import sys
from datetime import date, datetime, timedelta, timezone

import pytest

_backend = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from db.fake import FakeStore  # noqa: E402
from trend_research import build_ledger as bl  # noqa: E402
from trend_research import ledger, pit_store  # noqa: E402
from trend_research.llm import BudgetExceeded, QuotaExhausted, StageFailed  # noqa: E402
from trend_research.schemas import (  # noqa: E402
    BookProposal, CandidateList, ResearchNote, TrendScan)

NOW = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)
W1, W2, W3 = date(2025, 8, 4), date(2025, 8, 11), date(2025, 8, 18)
MODEL, PV = "claude-test", "tr-v1"
SECRET = "sk-ant-SECRET-123456"
_SYM_RE = re.compile(r'symbol is "([A-Z.]+)"')


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# -- fakes ---------------------------------------------------------------------------

class FakeCaller:
    """The StageCaller contract: ``call`` and ``calls_used``; every attempt
    counts toward ``max_calls``. ``hook(n, stage, prompt)`` may raise."""

    def __init__(self, *, max_calls=10_000, hook=None, trend="AI buildout",
                 candidates=("NVDA", "msft", "ITA", "GLD", "NOFIL", "NOBAR", "SAP.DE"),
                 picks=(("NVDA", 0.12), ("GLD", 0.10), ("BIL", 0.05))):
        self.max_calls = max_calls
        self.calls_used = 0
        self.hook = hook
        self.trend = trend
        self.candidates = candidates
        self.picks = picks
        self.prompts = []                   # (stage, prompt)

    def call(self, prompt, output_type, *, max_output_tokens):
        if self.calls_used >= self.max_calls:
            raise BudgetExceeded("budget")
        self.calls_used += 1
        stage = {TrendScan: "scan", CandidateList: "candidates", ResearchNote: "research",
                 BookProposal: "book"}[output_type]
        self.prompts.append((stage, prompt))
        if self.hook:
            self.hook(self.calls_used, stage, prompt)
        if stage == "scan":
            return TrendScan(trends=[{
                "name": self.trend, "category": "tech", "state": "strengthening",
                "confidence": 0.7, "evidence_ids": [], "beneficiaries": ["NVDA"],
                "losers": [], "summary": "Capex keeps rising."}],
                regime="risk_on", regime_reasons="tape up")
        if stage == "candidates":
            return CandidateList(candidates=[
                {"symbol": s, "trend_names": [self.trend], "why": "exposed to %s" % s}
                for s in self.candidates])
        if stage == "research":
            sym = _SYM_RE.search(prompt).group(1)
            return ResearchNote(symbol=sym, conviction=7.0, thesis="thesis for %s" % sym,
                                breaks_if=["capex cut"], priced_in="partly",
                                thesis_status="new", risks="valuation")
        return BookProposal(picks=[{"symbol": s, "conviction": 7.0, "target_weight": w,
                                    "role": "defensive" if s == "BIL" else "core"}
                                   for s, w in self.picks],
                            defensive_share=0.1, notes="n")

    def stages(self):
        return [s for s, _ in self.prompts]


def _bars(session_count=700, end=date(2026, 11, 1)):
    out, d = [], end
    while len(out) < session_count:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return [{"t": "%sT04:00:00Z" % x.isoformat(), "o": 1, "h": 1, "l": 1,
             "c": 100.0 + i * 0.1, "v": 1000} for i, x in enumerate(sorted(out))]


BARS = _bars()


class Env:
    """Injected bars, filings and dossier fakes over one store."""

    def __init__(self, st, *, dossier_status=None):
        self.st = st
        self.bars_calls = []
        self.filings_calls = []
        self.dossier_calls = []
        self.dossier_status = dossier_status or {}    # sym -> list of statuses

    def bars_fetcher(self, symbols, start, end):
        self.bars_calls.append((tuple(symbols), start, end))
        return {s: [b for b in BARS if start.isoformat() <= b["t"][:10] <= end.isoformat()]
                for s in symbols if s != "NOBAR"}

    def filings_fetcher(self, sym, start, end):
        self.filings_calls.append((sym, start, end))
        if sym == "NOFIL":
            return 0
        if sym == "BADFX":
            raise RuntimeError("EDGAR 503")
        pit_store.put_rows("TrendFilingsPIT", [{
            "id": "%s|2025-05-01T20:00:00Z|0000000001-25-000001" % sym, "symbol": sym,
            "cik": 1, "form": "10-K", "accession": "0000000001-25-000001",
            "available_at": "2025-05-01T20:00:00Z"}], store=self.st)
        return 1

    def dossier_fn(self, sym, as_of):
        self.dossier_calls.append((sym, as_of))
        seq = self.dossier_status.get(sym)
        status = seq.pop(0) if seq else "ok"
        return {"factors": {"symbol": sym}, "factors_status": status, "filings": []}

    def kwargs(self):
        return {"store": self.st, "bars_fetcher": self.bars_fetcher,
                "filings_fetcher": self.filings_fetcher, "dossier_fn": self.dossier_fn}


@pytest.fixture
def st():
    return FakeStore()


def run(st, caller, *, chain="honest", start=W1, end=W3, env=None, sleep=None, now=NOW,
        api_key=SECRET):
    env = env or Env(st)
    return bl.build(chain, start, end, provider="anthropic", model=MODEL, api_key=api_key,
                    max_calls=caller.max_calls, prompt_version=PV, caller=caller,
                    sleep=sleep or (lambda s: None), now=lambda: now, **env.kwargs())


def row(st, monday, chain="honest"):
    return ledger.get_week(chain, monday, MODEL, PV, store=st)


# Researched each cold week: NVDA, MSFT, GLD (ITA = theme ETF, NOFIL = no
# operating filings, NOBAR = no bars, SAP.DE = foreign listing).
COLD_RESEARCH = ["NVDA", "MSFT", "GLD"]
CALLS_PER_COLD_WEEK = 2 + len(COLD_RESEARCH) + 1


# -- ledger --------------------------------------------------------------------------

def test_week_id_and_as_of():
    assert ledger.week_id("honest", W1, MODEL, PV) == "honest|2025-08-04|claude-test|tr-v1"
    with pytest.raises(ValueError):
        ledger.week_id("a|b", W1, MODEL, PV)
    with pytest.raises(ValueError):
        ledger.week_id("honest", date(2025, 8, 5), MODEL, PV)
    assert ledger.week_as_of(W1) == datetime(2025, 8, 4, 13, 25, tzinfo=timezone.utc)
    # Labor Day 2025-09-01: the week's first session is Tuesday.
    assert ledger.week_as_of(date(2025, 9, 1)) == datetime(2025, 9, 2, 13, 25,
                                                           tzinfo=timezone.utc)
    # Winter (EST): 09:25 ET = 14:25Z.
    assert ledger.week_as_of(date(2026, 1, 12)) == datetime(2026, 1, 12, 14, 25,
                                                            tzinfo=timezone.utc)


def _ledger_row(chain, monday, status="ok", model=MODEL):
    at = iso(ledger.week_as_of(monday))
    return {"id": ledger.week_id(chain, monday, model, PV), "chain_id": chain,
            "week_start": monday.isoformat(), "as_of": at, "available_at": at,
            "model": model, "provider": "anthropic", "prompt_version": PV, "status": status}


def test_latest_row_is_strictly_before_and_ok_only(st):
    ledger.put_week(_ledger_row("honest", W1), store=st)
    ledger.put_week(_ledger_row("honest", W2, status="failed"), store=st)
    ledger.put_week(_ledger_row("other", W2), store=st)
    ledger.put_week(_ledger_row("honest", W2, model="other-model"), store=st)
    w2_as_of = ledger.week_as_of(W2)
    assert ledger.latest_row("honest", MODEL, PV, w2_as_of, store=st)["id"].startswith(
        "honest|2025-08-04")
    # as_of exactly at the decision is not visible (strict <).
    assert ledger.latest_row("honest", MODEL, PV, ledger.week_as_of(W1), store=st) is None
    assert ledger.latest_row("honest", MODEL, PV, w2_as_of + timedelta(seconds=1),
                             store=st)["week_start"] == "2025-08-04"
    assert ledger.latest_row("other", MODEL, PV, w2_as_of, store=st) is None


def test_put_week_replaces_failed_never_ok(st):
    ledger.put_week(_ledger_row("honest", W1, status="failed"), store=st)
    ledger.put_week(_ledger_row("honest", W1), store=st)
    assert row(st, W1)["status"] == "ok"
    with pytest.raises(ValueError, match="ok row"):
        ledger.put_week(_ledger_row("honest", W1, status="failed"), store=st)
    bad = _ledger_row("honest", W2)
    bad["as_of"] = bad["available_at"] = "2025-08-11T00:00:00Z"
    with pytest.raises(ValueError, match="as_of"):
        ledger.put_week(bad, store=st)


# -- builder -------------------------------------------------------------------------

def test_writes_one_row_per_week(st):
    env = Env(st)
    caller = FakeCaller()
    summary = run(st, caller, env=env)
    assert summary["built_ok"] == 3 and summary["built_failed"] == 0
    assert summary["stopped"] is None
    rows = [row(st, m) for m in (W1, W2, W3)]
    assert all(r and r["status"] == "ok" for r in rows)
    r1 = rows[0]
    assert r1["as_of"] == r1["available_at"] == "2025-08-04T13:25:00Z"
    assert r1["provider"] == "anthropic" and r1["model"] == MODEL and r1["prompt_version"] == PV
    assert re.fullmatch(r"[0-9a-f]{64}", r1["digest_hash"])
    assert [c["symbol"] for c in r1["candidates"]] == COLD_RESEARCH
    assert r1["calls_used"] == CALLS_PER_COLD_WEEK
    assert TrendScan.model_validate(r1["scan"]) and BookProposal.model_validate(r1["book"])
    assert [n["symbol"] for n in r1["notes"]] == COLD_RESEARCH
    # BIL is the cash remainder, not a researched recommendation.
    assert [r["symbol"] for r in r1["recommended"]] == ["NVDA", "GLD"]
    assert r1["recommended"][0]["thesis"] == "thesis for NVDA"
    reasons = {d["raw"]: d["reason"] for d in r1["dropped"]}
    assert "ETF outside" in reasons["ITA"]
    assert "operating company" in reasons["NOFIL"]
    assert "sessions of daily bars" in reasons["NOBAR"]
    assert "not a US-listed" in reasons["SAP.DE"]
    for key in ("headlines", "themes", "macro_series", "news_rows_8w", "news_items",
                "filings", "candidates_named"):
        assert key in r1["provenance"]
    assert r1["built_at"]["started"] and r1["built_at"]["finished"]
    # R7: stocks fetched over the 400 days before as_of; the allowed ETF is not.
    fetched = {c[0]: c for c in env.filings_calls if c[2] == W1}
    assert fetched["NVDA"][1:] == (W1 - timedelta(days=400), W1)
    assert "GLD" not in fetched and "ITA" not in fetched
    # Dossiers read at the exact as_of datetime, never midnight.
    assert all(t == ledger.week_as_of(W1) for s, t in env.dossier_calls[:3])
    # The second week researches the prior recommended names too.
    assert rows[1]["prior_row_id"] == r1["id"]
    assert summary["calls_used"] == caller.calls_used


def test_resume_skips_ok_weeks(st):
    run(st, FakeCaller())
    again = FakeCaller()
    summary = run(st, again)
    assert again.calls_used == 0
    assert summary["skipped_ok"] == 3 and summary["built_ok"] == 0


def test_quota_mid_week_no_partial_row(st):
    reset_at = NOW + timedelta(minutes=10)
    state = {"hit": False}
    slept = []

    def hook(n, stage, prompt):
        research_calls = sum(1 for s in caller.stages() if s == "research")
        if stage == "research" and research_calls == 3 and not state["hit"]:
            state["hit"] = True
            raise QuotaExhausted(reset_at, "subscription quota hit")

    def sleep(seconds):
        # Nothing for the interrupted week is written while we wait.
        assert row(st, W1) is None
        slept.append(seconds)

    caller = FakeCaller(hook=hook)
    summary = run(st, caller, end=W1, sleep=sleep)
    assert slept == [600 + 60]
    assert summary["quota_waits"] == 1
    r = row(st, W1)
    assert r["status"] == "ok" and [n["symbol"] for n in r["notes"]] == COLD_RESEARCH
    # The retried week re-ran from the digest: 5 calls before the hit (the 3rd research call counts) + a full week.
    assert caller.calls_used == 5 + CALLS_PER_COLD_WEEK
    assert r["calls_used"] == CALLS_PER_COLD_WEEK


def test_quota_unknown_reset_waits_30_minutes(st):
    state = {"hit": False}
    slept = []

    def hook(n, stage, prompt):
        if stage == "book" and not state["hit"]:
            state["hit"] = True
            raise QuotaExhausted(None)

    run(st, FakeCaller(hook=hook), end=W1, sleep=slept.append)
    assert slept == [1800.0]
    assert row(st, W1)["status"] == "ok"


def test_cold_start_chain_has_no_prior_trends(st):
    run(st, FakeCaller(trend="OTHER-CHAIN-TREND", candidates=("AMD",),
                       picks=(("AMD", 0.1),)), chain="other")
    caller = FakeCaller(trend="HONEST-TREND")
    run(st, caller, end=W2)
    scans = [p for s, p in caller.prompts if s == "scan"]
    assert "None: this is the first week of the study" in scans[0]
    assert "OTHER-CHAIN-TREND" not in scans[0]
    cands = [p for s, p in caller.prompts if s == "candidates"]
    assert "AMD" not in cands[0] and "none (first week)" in cands[0]
    # Week 2 inherits from its own chain only.
    assert "HONEST-TREND" in scans[1] and "OTHER-CHAIN-TREND" not in scans[1]
    assert row(st, W1)["prior_row_id"] is None
    research_w2 = [p for s, p in caller.prompts if s == "research"][len(COLD_RESEARCH):]
    nvda = [p for p in research_w2 if 'symbol is "NVDA"' in p][0]
    assert "thesis for NVDA" in nvda          # last week's note on it


def test_failed_stage_writes_failed_row(st):
    def hook(n, stage, prompt):
        if stage == "scan" and caller.calls_used <= 2:
            raise StageFailed("api_key=%s rejected: no structured result" % SECRET)

    caller = FakeCaller(hook=hook)
    summary = run(st, caller, end=W2)
    r = row(st, W1)
    assert r["status"] == "failed" and r["failed_stage"] == "scan"
    assert "no structured result" in r["error"] and SECRET not in r["error"]
    assert r["calls_used"] == 2 and r["digest_hash"]
    assert r["recommended"] == [] and r["book"] is None
    # The next week starts cold: a failed row carries no prior state.
    r2 = row(st, W2)
    assert r2["status"] == "ok" and r2["prior_row_id"] is None
    assert summary["built_failed"] == 1 and summary["built_ok"] == 1
    # A later run retries the failed week; the lane-visible ok row replaces it.
    again = FakeCaller()
    summary2 = run(st, again, end=W2)
    assert summary2["built_ok"] == 1 and summary2["skipped_ok"] == 1
    assert row(st, W1)["status"] == "ok"


def test_stage_retried_once_before_failing(st):
    def hook(n, stage, prompt):
        if stage == "book" and sum(1 for s in caller.stages() if s == "book") == 1:
            raise StageFailed("bad json")

    caller = FakeCaller(hook=hook)
    run(st, caller, end=W1)
    assert row(st, W1)["status"] == "ok"
    assert caller.stages().count("book") == 2


def test_budget_stop(st):
    caller = FakeCaller(max_calls=CALLS_PER_COLD_WEEK + 3)
    summary = run(st, caller)
    assert summary["stopped"] == "budget"
    assert summary["built_ok"] == 1
    assert row(st, W1)["status"] == "ok"
    assert row(st, W2) is None and row(st, W3) is None
    assert summary["calls_used"] == CALLS_PER_COLD_WEEK + 3


def test_dossier_fetch_failed_retried_then_dropped(st):
    env = Env(st, dossier_status={"NVDA": ["fetch_failed", "ok"],
                                  "MSFT": ["fetch_failed", "fetch_failed"]})
    run(st, FakeCaller(), end=W1, env=env)
    r = row(st, W1)
    assert r["status"] == "ok"
    assert [c["symbol"] for c in r["candidates"]] == ["NVDA", "GLD"]
    assert {d["symbol"]: d["stage"] for d in r["dropped"]}["MSFT"] == "dossier"
    assert r["provenance"]["dossier_retries"] == ["NVDA", "MSFT"]


def test_research_failure_drops_candidate_not_week(st):
    def hook(n, stage, prompt):
        if stage == "research" and 'symbol is "MSFT"' in prompt:
            raise StageFailed("bad json")

    run(st, FakeCaller(hook=hook), end=W1)
    r = row(st, W1)
    assert r["status"] == "ok"
    assert [c["symbol"] for c in r["candidates"]] == ["NVDA", "GLD"]
    assert {d["symbol"]: d["stage"] for d in r["dropped"]}["MSFT"] == "research"


def test_filings_fetch_error_recorded_on_drop(st):
    run(st, FakeCaller(candidates=("NVDA", "BADFX")), end=W1)
    reasons = {d["raw"]: d["reason"] for d in row(st, W1)["dropped"]}
    assert "filings fetch failed: RuntimeError: EDGAR 503" in reasons["BADFX"]


def test_candidate_cap(st):
    names = ["S%s" % chr(65 + i) + chr(65 + j) for i in range(3) for j in range(12)]
    caller = FakeCaller(candidates=names)
    run(st, caller, end=W1)
    r = row(st, W1)
    assert len(r["candidates"]) == bl.MAX_CANDIDATES
    assert caller.stages().count("research") == bl.MAX_CANDIDATES
    assert sum(1 for d in r["dropped"] if d["stage"] == "cap") == len(names) - bl.MAX_CANDIDATES


def test_symbol_news_capped_and_strictly_before(st):
    as_of = ledger.week_as_of(W1)
    rows = []
    for i in range(45):
        at = iso(as_of - timedelta(hours=i + 1))
        rows.append({"id": "%s|alpaca|n%d" % (at, i), "source": "alpaca",
                     "source_id": "n%d" % i, "available_at": at, "published_at": at,
                     "headline": "NVDA story %d" % i, "symbols": ["NVDA"]})
    at = iso(as_of)
    rows.append({"id": "%s|alpaca|late" % at, "source": "alpaca", "source_id": "late",
                 "available_at": at, "published_at": at, "headline": "LATE NVDA STORY",
                 "symbols": ["NVDA"]})
    pit_store.put_rows("TrendNewsPIT", rows, store=st)
    caller = FakeCaller(candidates=("NVDA",))
    run(st, caller, end=W1)
    nvda = [p for s, p in caller.prompts if s == "research"][0]
    assert "LATE NVDA STORY" not in nvda
    assert nvda.count("NVDA story") == 40
    assert row(st, W1)["candidates"][0]["news_items"] == 40


def test_weeks_not_yet_due_are_not_built(st):
    caller = FakeCaller()
    summary = run(st, caller, now=ledger.week_as_of(W2))
    assert summary["built_ok"] == 1 and summary["not_yet_due"] == 2
    assert row(st, W2) is None


def test_quota_delay():
    assert bl.quota_delay(NOW + timedelta(seconds=30), NOW) == 90
    assert bl.quota_delay(NOW - timedelta(seconds=30), NOW) == 60
    assert bl.quota_delay(None, NOW) == 1800


def test_cli_resolves_models_row_and_prints_counts_only(monkeypatch, capsys):
    import model_resolver

    def fake_resolve(conn, config, *, force_refresh=False):
        assert config == {"tr_llm_model_id": "row-1"} and force_refresh
        return dict(config, tr_llm_provider="claude-cli", tr_llm_model="claude-opus",
                    tr_llm_api_key=SECRET, tr_cli_path="claude")

    seen = {}

    def fake_build(chain, start, end, **kw):
        seen.update(kw, chain=chain, start=start, end=end)
        return {"chain_id": chain, "model": kw["model"], "prompt_version": PV, "weeks": 3,
                "built_ok": 2, "built_failed": 1, "skipped_ok": 0, "not_yet_due": 0,
                "quota_waits": 0, "calls_used": 12, "stopped": None}

    monkeypatch.setattr(model_resolver, "resolve_model_refs_in_config", fake_resolve)
    monkeypatch.setattr(bl, "build", fake_build)
    assert bl.main(["--chain", "honest-opus", "--start", "2025-08-04", "--end", "2025-08-18",
                    "--model-id", "row-1"]) == 0
    assert seen["provider"] == "claude-cli" and seen["model"] == "claude-opus"
    assert seen["api_key"] == SECRET and seen["provider_config"] == {"cli_path": "claude"}
    assert seen["max_calls"] == 2000 and seen["start"] == W1
    out = capsys.readouterr().out
    assert SECRET not in out and "2 built ok, 1 failed" in out and "12 LLM calls" in out


# -- fix round 1 ---------------------------------------------------------------------

def test_stage_failure_text_is_scrubbed_in_logs(st, caplog):
    def hook(n, stage, prompt):
        if stage in ("scan", "research"):
            raise StageFailed("api_key=%s rejected" % SECRET)

    with caplog.at_level("DEBUG", logger="trend_research"):
        run(st, FakeCaller(hook=hook), end=W1)
    assert "stage failed" in caplog.text
    assert SECRET not in caplog.text
    r = row(st, W1)
    assert r["status"] == "failed" and SECRET not in repr(r)


def test_recommended_only_names_with_a_note(st):
    caller = FakeCaller(picks=(("NVDA", 0.12), ("TLT", 0.10), ("MSFT", 0.0), ("BIL", 0.05),
                               ("nvda", 0.05)))
    run(st, caller, end=W1)
    r = row(st, W1)
    assert [x["symbol"] for x in r["recommended"]] == ["NVDA"]
    rec = {d["symbol"]: d["reason"] for d in r["dropped"] if d["stage"] == "recommended"}
    assert rec == {"TLT": "book pick has no research note this week",
                   "MSFT": "book pick at target_weight 0"}
    # The book itself is stored as the model proposed it.
    assert [p["symbol"] for p in r["book"]["picks"]] == ["NVDA", "TLT", "MSFT", "BIL", "nvda"]


def test_quota_wait_cap_stops_cleanly(st, caplog):
    slept = []

    def hook(n, stage, prompt):
        raise QuotaExhausted(None, "session limit")

    with caplog.at_level("WARNING", logger="trend_research"):
        summary = run(st, FakeCaller(hook=hook), sleep=slept.append)
    assert slept == [1800.0] * bl.MAX_QUOTA_WAITS_PER_WEEK
    assert summary["stopped"] == "quota_wait_cap" and summary["stopped_week"] == "2025-08-04"
    assert summary["quota_waits"] == bl.MAX_QUOTA_WAITS_PER_WEEK
    assert row(st, W1) is None and row(st, W2) is None
    assert "wait 12 of 12" in caplog.text


def test_quota_wait_count_resets_per_week(st):
    hits = {"n": 0}

    def hook(n, stage, prompt):
        # Each week's scan hits the quota 12 times before succeeding.
        if stage == "scan" and hits["n"] < 12:
            hits["n"] += 1
            raise QuotaExhausted(None)
        if stage == "book":
            hits["n"] = 0

    summary = run(st, FakeCaller(hook=hook), end=W2, sleep=lambda s: None)
    assert summary["stopped"] is None and summary["built_ok"] == 2
    assert summary["quota_waits"] == 24 and summary["stopped_week"] is None


def test_prior_recommended_researched_first_even_if_not_named(st):
    run(st, FakeCaller(), end=W1)
    assert [x["symbol"] for x in row(st, W1)["recommended"]] == ["NVDA", "GLD"]
    caller = FakeCaller(candidates=("AMD", "MSFT"), picks=(("AMD", 0.1),))
    run(st, caller, start=W2, end=W2)
    r2 = row(st, W2)
    assert [c["symbol"] for c in r2["candidates"]] == ["NVDA", "GLD", "AMD", "MSFT"]
    assert r2["candidates"][0]["sources"] == ["prior_recommended"]
    researched = [_SYM_RE.search(p).group(1) for s, p in caller.prompts if s == "research"]
    assert researched == ["NVDA", "GLD", "AMD", "MSFT"]
    assert r2["prior_row_id"] == row(st, W1)["id"]
