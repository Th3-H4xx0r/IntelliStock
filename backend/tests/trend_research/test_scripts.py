"""TR operator scripts: lab setup, backtest runner and research journal
(plan Task 12).

The API is a fake that records every call (no network); the journal reads a
FakeStore. What is pinned: the lab setup refuses docs 200/201 and a
brokerage that is not provably paper before writing anything, and writes no
instance but strategy-tr-lab; the backtest runner refuses to post while a run
is in flight and posts the exact body; the journal renders a fixture week.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys

import pytest

_backend = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_scripts = os.path.join(os.path.dirname(_backend), "scripts")
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from db.fake import FakeStore  # noqa: E402
from trend_research import book, ledger  # noqa: E402


def _load(name):
    spec = importlib.util.spec_from_file_location(f"_tr_script_{name}",
                                                  os.path.join(_scripts, f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


lab = _load("tr_lab_setup")
runner = _load("tr_backtest")
journal = _load("tr_journal")

PAPER, LIVE, NOT_PAPER = "brk-paper", "brk-live", "brk-other"
LAB_ARGS = ["--chain", "honest-1", "--model", "claude-test", "--cutoff", "2025-03-01"]


class FakeAPI:
    """Routes keyed (METHOD, path); a missing route is the API's 404, which
    `_api.call` raises as SystemExit. Every call is recorded."""

    def __init__(self, routes=None):
        self.routes = dict(routes or {})
        self.calls = []

    def __call__(self, method, path, body=None):
        self.calls.append((method, path, body))
        key = (method, path)
        if key not in self.routes:
            raise SystemExit(f"HTTP 404 on {method} {path}\nnot found")
        value = self.routes[key]
        if callable(value):
            value = value(body)
        if isinstance(value, BaseException):
            raise value
        return 200, value

    def writes(self):
        return [c for c in self.calls if c[0] != "GET"]


def lab_api(*, docs=(), created_id=77, lab_instance=None, clone_brokerage=PAPER,
            paper_flag=True):
    routes = {
        ("GET", "/strategies"): {"strategies": list(docs)},
        ("POST", "/strategies"): {"id": created_id},
        ("GET", "/instances/alpaca-main"): {"id": "alpaca-main", "brokerage_id": LIVE},
        ("GET", "/instances/strategy-eb"): {"id": "strategy-eb",
                                            "brokerage_id": clone_brokerage},
        ("GET", "/brokerages"): {"accounts": [
            {"id": PAPER, "account_name": "Alpaca Paper", "alpaca_paper": paper_flag},
            {"id": LIVE, "account_name": "Alpaca Live", "alpaca_paper": False},
            {"id": NOT_PAPER, "account_name": "Other", "alpaca_paper": False},
        ]},
        ("POST", "/instances"): {"id": "strategy-tr-lab"},
        ("POST", "/instances/strategy-tr-lab/link-strategy"): {},
    }
    for doc in docs:
        routes[("PUT", f"/strategies/{doc['id']}")] = {}
    for sym in lab.STOCKS:
        routes[("POST", "/instances/strategy-tr-lab/stocks")] = {}
    after = {"id": "strategy-tr-lab", "strategy_id": created_id,
             "granularity_time_increment": 900,
             "brokerage_id": (lab_instance or {}).get("brokerage_id", clone_brokerage)}
    state = {"inst": lab_instance}

    def get_lab(_body):
        if state["inst"] is None:
            return SystemExit("HTTP 404 on GET /instances/strategy-tr-lab\nnot found")
        return state["inst"]

    def create(body):
        state["inst"] = after
        return {"id": "strategy-tr-lab"}

    routes[("GET", "/instances/strategy-tr-lab")] = get_lab
    routes[("POST", "/instances")] = create
    return FakeAPI(routes)


# -- tr_lab_setup -----------------------------------------------------------------------


@pytest.mark.parametrize("doc_id", [200, "201", " 200 ", "0201", 200.0])
def test_lab_setup_refuses_existing_protected_doc(doc_id, capsys):
    api = lab_api(docs=[{"id": doc_id, "name": "TR lab"}])
    with pytest.raises(SystemExit, match="REFUSING to write doc"):
        lab.main(LAB_ARGS, call=api)
    assert api.writes() == []


@pytest.mark.parametrize("doc_id", [200, "201"])
def test_lab_setup_refuses_created_protected_doc(doc_id):
    api = lab_api(created_id=doc_id)
    with pytest.raises(SystemExit, match="REFUSING to write doc"):
        lab.main(LAB_ARGS, call=api)
    # The POST that answered 200/201 is the only write; nothing is linked to it.
    assert [c[:2] for c in api.writes()] == [("POST", "/strategies")]


@pytest.mark.parametrize("clone, flag, why", [
    (NOT_PAPER, True, "not marked as an Alpaca paper account"),
    (PAPER, False, "not marked as an Alpaca paper account"),
    (LIVE, True, "real-money account"),
    (None, True, "no brokerage to link"),
    ("brk-missing", True, "not found"),
])
def test_lab_setup_refuses_a_brokerage_that_is_not_paper(clone, flag, why):
    api = lab_api(clone_brokerage=clone, paper_flag=flag)
    with pytest.raises(SystemExit, match=why):
        lab.main(LAB_ARGS, call=api)
    assert api.writes() == []


def test_lab_setup_refuses_existing_lab_instance_on_a_non_paper_brokerage():
    api = lab_api(lab_instance={"id": "strategy-tr-lab", "brokerage_id": NOT_PAPER})
    with pytest.raises(SystemExit, match="not marked as an Alpaca paper"):
        lab.main(LAB_ARGS, call=api)
    assert api.writes() == []


def test_lab_setup_creates_doc_and_instance(capsys):
    api = lab_api()
    assert lab.main(LAB_ARGS + ["--prompt-version", "tr-v2"], call=api) == 0
    writes = api.writes()
    method, path, doc = writes[0]
    assert (method, path) == ("POST", "/strategies")
    assert doc["name"] == "TR lab" and len(doc["strategies"]) == 1
    spec = doc["strategies"][0]
    header = lab.lane_schema()
    assert spec["strategy"] == "strategy_trend_research" == header["strategy"]
    cfg = spec["config"]
    assert cfg["strategy_trend_research_enabled"] is True
    assert (cfg["tr_chain_id"], cfg["tr_model"], cfg["tr_prompt_version"],
            cfg["research_model_cutoff"]) == ("honest-1", "claude-test", "tr-v2", "2025-03-01")
    # Every other knob is the header's default, untouched.
    for key, value in header["config"].items():
        if key not in ("strategy_trend_research_enabled", "tr_chain_id", "tr_model",
                       "tr_prompt_version", "research_model_cutoff"):
            assert cfg[key] == value
    assert header["config"]["strategy_trend_research_enabled"] is False   # not mutated

    created = [b for m, p, b in writes if (m, p) == ("POST", "/instances")]
    assert created == [{"id": "strategy-tr-lab", "name": "TR lab (backtest only)",
                        "strategy_id": 77, "granularity": "900", "brokerage_id": PAPER,
                        "stocks": sorted(book.ALL_ALLOWED_ETFS)}]
    # The only instance ever written is the lab.
    for m, p, _b in writes:
        if p.startswith("/instances/"):
            assert p.startswith("/instances/strategy-tr-lab/")
        assert "alpaca-main" not in p and "strategy-eb" not in p
        assert not p.startswith("/strategies/200") and not p.startswith("/strategies/201")


def test_lab_setup_is_idempotent_on_an_existing_lab():
    api = lab_api(docs=[{"id": 88, "name": "TR lab"}],
                  lab_instance={"id": "strategy-tr-lab", "brokerage_id": PAPER})
    assert lab.main(LAB_ARGS, call=api) == 0
    writes = [c[:2] for c in api.writes()]
    assert ("PUT", "/strategies/88") in writes
    assert ("POST", "/instances/strategy-tr-lab/link-strategy") in writes
    assert ("POST", "/instances") not in writes
    assert not any(m == "PATCH" for m, _p in writes)
    link = [b for m, p, b in api.writes() if p.endswith("/link-strategy")]
    assert link == [{"strategy_id": 88}]


@pytest.mark.parametrize("drop", ["--chain", "--model", "--cutoff"])
def test_lab_setup_requires_the_selector_flags(drop):
    i = LAB_ARGS.index(drop)
    with pytest.raises(SystemExit) as exc:
        lab.parse_args(LAB_ARGS[:i] + LAB_ARGS[i + 2:])
    assert exc.value.code == 2


def test_lab_setup_rejects_a_bad_cutoff():
    with pytest.raises(SystemExit) as exc:
        lab.parse_args(["--chain", "c", "--model", "m", "--cutoff", "2025-13-01"])
    assert exc.value.code == 2


# -- tr_backtest ------------------------------------------------------------------------


def backtest_api(listing, statuses=("running", "finished"), card=None, lab_listing=None):
    seq = list(statuses)

    def status(_body):
        return {"id": 9, "status": seq.pop(0) if len(seq) > 1 else seq[0], "progress": 50}

    lab_rows = [r for r in listing if r.get("instance") == "strategy-tr-lab"]
    return FakeAPI({
        ("GET", "/backtests?per_page=100"): {"backtests": listing},
        ("GET", "/backtests?instance_id=strategy-tr-lab&per_page=100"):
            {"backtests": lab_rows if lab_listing is None else lab_listing},
        ("GET", "/instances/strategy-tr-lab"): {"id": "strategy-tr-lab", "strategy_id": 77},
        ("GET", "/strategies/77"): {"id": 77, "name": "TR lab", "strategies": [
            {"strategy": "strategy_trend_research",
             "config": {"strategy_trend_research_enabled": True, "tr_chain_id": "c1",
                        "tr_model": "claude-test", "tr_prompt_version": "tr-v1"}}]},
        ("POST", "/backtests"): {"id": 9, "instance": "strategy-tr-lab"},
        ("GET", "/backtests/9/status"): status,
        ("GET", "/backtests/9/summary"): {"id": 9, "tr_scorecard": card},
    })


CARD = {seg: {"untested": False, "start": "2025-01-06", "end": "2025-06-30", "ret": 4.0,
              "spy_ret": 3.0, "cagr": 8.0, "spy_cagr": 6.0, "excess_pp": 2.0, "max_dd": -5.0,
              "random_pctile_candidates": 60.0, "random_pctile_universe": 55.0,
              "pass_all_regimes": False, "pass_plus5": False, "pass": False,
              "windows": [{"tag": "w1", "kind": "bull", "ret": 1.0, "spy_ret": 2.0,
                           "beat": False}]}
        for seg in ("overall", "optimistic", "honest")}
CARD["meta"] = {"notes": ["a note"]}
RUN_ARGS = ["--start", "2025-01-06", "--end", "2025-06-30", "--poll-seconds", "0"]


def ledger_store(mondays=("2025-01-13", "2025-03-03", "2025-06-23"), *, chain="c1"):
    """A FakeStore holding the lab chain's ok weeks (plus noise the window
    must ignore: a failed week, another model)."""
    st = FakeStore()
    rows = [{"id": f"{chain}|{m}|claude-test|tr-v1", "chain_id": chain, "week_start": m,
             "model": "claude-test", "prompt_version": "tr-v1", "status": "ok"}
            for m in mondays]
    rows.append({"id": f"{chain}|2024-06-03|claude-test|tr-v1", "chain_id": chain,
                 "week_start": "2024-06-03", "model": "claude-test",
                 "prompt_version": "tr-v1", "status": "failed"})
    rows.append({"id": f"{chain}|2024-01-08|other|tr-v1", "chain_id": chain,
                 "week_start": "2024-01-08", "model": "other", "prompt_version": "tr-v1",
                 "status": "ok"})
    st.insert(ledger.TABLE, rows)
    return st


@pytest.mark.parametrize("status", ["pending", "queued", "running", "paused",
                                    "paused_llm_critical"])
def test_backtest_refuses_while_the_lab_has_a_run_in_flight(status):
    api = backtest_api([{"id": 5, "instance": "strategy-tr-lab", "status": status},
                        {"id": 4, "instance": "strategy-tr-lab", "status": "finished"}])
    with pytest.raises(SystemExit, match="strategy-tr-lab already has a backtest in flight"):
        runner.main(RUN_ARGS, call=api, sleep=lambda s: None, store=ledger_store())
    assert api.writes() == []


def test_backtest_refuses_while_another_instance_runs():
    api = backtest_api([{"id": 6, "instance": "strategy-eb-lab", "status": "running"}])
    with pytest.raises(SystemExit, match="the engine already has a backtest in flight"):
        runner.main(RUN_ARGS, call=api, sleep=lambda s: None, store=ledger_store())
    assert api.writes() == []


def test_backtest_posts_one_run_polls_and_prints_the_scorecard(capsys):
    api = backtest_api([{"id": 4, "instance": "strategy-tr-lab", "status": "finished"}],
                       statuses=("pending", "running", "finished"), card=CARD)
    assert runner.main(RUN_ARGS, call=api, sleep=lambda s: None, store=ledger_store()) == 0
    assert api.writes() == [("POST", "/backtests", {
        "instance_id": "strategy-tr-lab", "stocks": sorted(book.ALL_ALLOWED_ETFS),
        "start_date": "2025-01-06", "end_date": "2025-06-30", "granularity": "900",
        "initial_cash": 5500})]
    out = capsys.readouterr().out
    assert "backtest 9: finished" in out
    assert "honest" in out and "8.00" in out and "a note" in out
    assert json.loads(out[out.index("{"):])["honest"]["cagr"] == 8.0


def test_backtest_sends_cost_flags_only_when_asked():
    api = backtest_api([], card=CARD)
    runner.main(RUN_ARGS + ["--cost-tiers", "etf-liquid"], call=api, sleep=lambda s: None,
                store=ledger_store())
    body = api.writes()[0][2]
    assert body["equity_cost_tiers"] == "etf-liquid" and "equity_total_cost_bps" not in body


def test_backtest_times_out_without_stopping_the_run(capsys):
    api = backtest_api([], statuses=("running",))
    ticks = iter(range(0, 10 ** 6, 1000))
    code = runner.main(RUN_ARGS + ["--max-wait-hours", "1"], call=api,
                       sleep=lambda s: None, clock=lambda: next(ticks), store=ledger_store())
    assert code == 2
    assert "TIMEOUT" in capsys.readouterr().out
    assert [c[:2] for c in api.writes()] == [("POST", "/backtests")]   # never stopped


def test_backtest_reports_a_missing_scorecard(capsys):
    api = backtest_api([], card=None)
    assert runner.main(RUN_ARGS, call=api, sleep=lambda s: None, store=ledger_store()) == 0
    assert "no tr_scorecard" in capsys.readouterr().out


def test_backtest_failed_run_exits_nonzero():
    api = backtest_api([], statuses=("error",), card={"error": "ValueError: boom"})
    assert runner.main(RUN_ARGS, call=api, sleep=lambda s: None, store=ledger_store()) == 1



# -- fix wave B: the ledger window, paused_llm_critical, granularity ---------------------------


def _posted(api):
    return [b for m, p, b in api.writes() if (m, p) == ("POST", "/backtests")]


def test_backtest_window_defaults_to_the_ledger():
    """final I6: no flags -> a week of warm-up before the first ok week
    through the end of the last ok week (failed rows and other models are
    not the chain's window)."""
    api = backtest_api([], card=CARD)
    assert runner.main(["--poll-seconds", "0"], call=api, sleep=lambda s: None,
                       store=ledger_store()) == 0
    body = _posted(api)[0]
    assert (body["start_date"], body["end_date"]) == ("2025-01-06", "2025-06-29")
    assert body["granularity"] == "900"
    # The selector came from the lab's linked document.
    assert ("GET", "/strategies/77", None) in api.calls


def test_backtest_refuses_a_long_cash_lead_in():
    api = backtest_api([], card=CARD)
    with pytest.raises(SystemExit, match="REFUSING: --start 2021-07-05 is"):
        runner.main(["--start", "2021-07-05", "--end", "2025-06-30", "--poll-seconds", "0"],
                    call=api, sleep=lambda s: None, store=ledger_store())
    assert _posted(api) == []
    # Two weeks of lead-in is fine; more needs the flag.
    api = backtest_api([], card=CARD)
    runner.main(["--start", "2024-12-30", "--poll-seconds", "0"], call=api,
                sleep=lambda s: None, store=ledger_store())
    assert _posted(api)[0]["start_date"] == "2024-12-30"
    api = backtest_api([], card=CARD)
    runner.main(["--start", "2021-07-05", "--end", "2025-06-30", "--allow-cash-lead",
                 "--poll-seconds", "0"], call=api, sleep=lambda s: None, store=ledger_store())
    assert _posted(api)[0]["start_date"] == "2021-07-05"


def test_backtest_refuses_before_the_ledger_is_built():
    api = backtest_api([], card=CARD)
    with pytest.raises(SystemExit, match="no ok ledger weeks yet"):
        runner.main(["--poll-seconds", "0"], call=api, sleep=lambda s: None,
                    store=ledger_store(mondays=()))
    assert _posted(api) == []


def test_backtest_sees_a_paused_llm_critical_run_off_page_one():
    """The global listing pins only running/queued/pending/paused rows to page
    1; the lab's own rows are read with the instance filter."""
    api = backtest_api([{"id": 4, "instance": "strategy-tr-lab", "status": "finished"}],
                       lab_listing=[{"id": 3, "instance": "strategy-tr-lab",
                                     "status": "paused_llm_critical"}])
    with pytest.raises(SystemExit, match="strategy-tr-lab already has a backtest in flight"):
        runner.main(RUN_ARGS, call=api, sleep=lambda s: None, store=ledger_store())
    assert api.writes() == []


def test_lab_setup_corrects_an_existing_instance_granularity():
    api = lab_api(docs=[{"id": 88, "name": "TR lab"}],
                  lab_instance={"id": "strategy-tr-lab", "brokerage_id": PAPER,
                                "granularity_time_increment": 60})
    inst = api.routes[("GET", "/instances/strategy-tr-lab")]

    def patch(body):
        state = inst(None)
        state["granularity_time_increment"] = int(body["granularity"])
        return state

    api.routes[("PATCH", "/instances/strategy-tr-lab")] = patch
    assert lab.main(LAB_ARGS, call=api) == 0
    patches = [b for m, p, b in api.writes() if m == "PATCH"]
    assert patches == [{"granularity": "900"}]


def test_lab_setup_refuses_when_the_granularity_will_not_change(capsys):
    api = lab_api(docs=[{"id": 88, "name": "TR lab"}],
                  lab_instance={"id": "strategy-tr-lab", "brokerage_id": PAPER,
                                "granularity_time_increment": "60"})
    api.routes[("PATCH", "/instances/strategy-tr-lab")] = {}
    with pytest.raises(SystemExit, match="REFUSING to leave strategy-tr-lab at granularity"):
        lab.main(LAB_ARGS, call=api)

# -- tr_journal -------------------------------------------------------------------------


def _week(monday, *, status="ok", picks=(("NVDA", 8.5, 0.2), ("BIL", 5.0, 0.1)), chain="c1",
          **extra):
    row = {"id": f"{chain}|{monday}|claude-test|tr-v1", "chain_id": chain,
           "week_start": monday, "as_of": f"{monday}T13:25:00Z",
           "available_at": f"{monday}T13:25:00Z", "model": "claude-test",
           "provider": "claude-cli", "prompt_version": "tr-v1", "status": status,
           "calls_used": 14}
    if status == "ok":
        row.update({
            "scan": {"regime": "risk_on", "regime_reasons": "breadth | strong, rates easing",
                     "trends": [{"name": "AI capex", "category": "tech", "state": "strengthening",
                                 "confidence": 0.8, "summary": "hyperscaler spend up"}]},
            "notes": [{"symbol": s, "conviction": c, "thesis": f"{s} thesis text",
                       "breaks_if": [f"{s} guidance cut", "capex pause"], "priced_in": "partly",
                       "thesis_status": "new", "risks": "valuation"} for s, c, _w in picks],
            "book": {"picks": [{"symbol": s, "conviction": c, "target_weight": w,
                                "role": "core"} for s, c, w in picks],
                     "defensive_share": 0.1, "notes": "tilted to AI"},
            "dropped": [{"symbol": "ARKK", "raw": "ARKK", "sources": ["scan"],
                         "stage": "universe", "reason": "ETF outside the allowed universe"}],
        })
    else:
        row.update({"failed_stage": "scan", "error": "no structured result"})
    row.update(extra)
    return row


def test_journal_renders_a_fixture_week():
    text = journal.render_journal([_week("2025-08-11")], chain="c1")
    for needle in ("# TR research journal - chain `c1`", "## Week of 2025-08-11",
                   "Status: **ok**", "LLM calls used: 14", "Regime: **risk_on**",
                   "breadth \\| strong", "| AI capex | tech | strengthening | 0.80 |",
                   "**NVDA** - conviction 8.5, weight 20.0%, role core",
                   "Thesis: NVDA thesis text", "Breaks if: NVDA guidance cut; capex pause",
                   "| ARKK | ARKK | universe | ETF outside the allowed universe |"):
        assert needle in text, needle


def test_journal_lists_sells_and_failed_weeks():
    rows = [_week("2025-08-11"), _week("2025-08-18", status="failed"),
            _week("2025-08-25", picks=(("BIL", 5.0, 0.2),))]
    text = journal.render_journal(rows, chain="c1")
    failed = text[text.index("## Week of 2025-08-18"):text.index("## Week of 2025-08-25")]
    assert "failed at `scan`" in failed and "Left the book" not in failed
    last = text[text.index("## Week of 2025-08-25"):]
    assert "### Left the book" in last
    assert "**NVDA** (held after week 2025-08-11) - not picked this week" in last
    assert "2 ok, 1 failed" in text


def test_journal_reads_only_its_chain_and_window():
    st = FakeStore()
    st.insert(ledger.TABLE, [_week("2025-08-11"), _week("2025-08-18"), _week("2025-08-25"),
                             _week("2025-08-18", chain="c10"),
                             _week("2025-08-18", model="other",
                                   id="c1|2025-08-18|other|tr-v1")])
    from datetime import date
    rows = journal.chain_rows("c1", start=date(2025, 8, 18), end=date(2025, 8, 18), store=st)
    assert sorted((r["chain_id"], r["week_start"], r["model"]) for r in rows) == [
        ("c1", "2025-08-18", "claude-test"), ("c1", "2025-08-18", "other")]
    rows = journal.chain_rows("c1", model="claude-test", store=st)
    assert [r["week_start"] for r in rows] == ["2025-08-11", "2025-08-18", "2025-08-25"]


def test_journal_main_writes_markdown(tmp_path):
    st = FakeStore()
    st.insert(ledger.TABLE, [_week("2025-08-11")])
    out = tmp_path / "j.md"
    assert journal.main(["--chain", "c1", "--out", str(out)], store=st) == 0
    assert "## Week of 2025-08-11" in out.read_text()
    assert journal.main(["--chain", "nope", "--out", str(out)], store=st) == 1


def test_journal_read_only_store_adds_the_read_only_option(monkeypatch):
    from db import pool
    monkeypatch.setattr(pool, "_pool", None)
    monkeypatch.setattr(pool, "_options", lambda: "-c timezone=UTC")
    monkeypatch.setenv("POSTGRES_HOST", "example.invalid")
    journal.read_only_store(load_env=False)
    assert pool._options().endswith("-c default_transaction_read_only=on")
    monkeypatch.setattr(pool, "_pool", object())
    with pytest.raises(SystemExit, match="already open"):
        journal.read_only_store(load_env=False)
