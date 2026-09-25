"""Transport-only tests for the swing lab and paper setup. No network."""
import importlib.util
import os
import re
import sys

import pytest

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ROOT = os.path.dirname(_BACKEND)
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from swing_trader.constants import SWING_DEFAULTS, WHEEL_DEFAULTS  # noqa: E402


def _setup():
    """Load the script without importing scripts/_api.py (it reads .env and logs in)."""
    path = os.path.join(_ROOT, "scripts", "swing_lab_setup.py")
    spec = importlib.util.spec_from_file_location("_swing_lab_setup", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


LIVE = {"alpaca-main": {"id": "alpaca-main", "brokerage_id": "brk-live",
                        "granularity_time_increment": 60}}
BROKERAGES = [{"id": "brk-live", "alpaca_paper": False},
              {"id": "brk-paper", "alpaca_paper": True},
              {"id": "brk-lab", "alpaca_paper": True},
              {"id": "brk-eb-live", "alpaca_paper": False},
              {"id": "brk-unknown"}]


def members(store, rows):
    store.insert("SwingIndexMembership", [
        {"id": f"SPX|{d}", "index": "SPX", "date": d, "members": m} for d, m in rows],
        conflict="replace")


class Api:
    """A fake of scripts/_api.py:call. 4xx raise SystemExit, as _api does."""

    def __init__(self, docs=(), instances=None, brokerages=()):
        self.docs = list(docs)
        self.instances = dict(instances or {})
        self.brokerages = list(brokerages)
        self.seen = []

    def __call__(self, method, path, body=None, **kwargs):
        self.seen.append((method, path, body))
        if (method, path) == ("GET", "/strategies"):
            return 200, {"strategies": self.docs}
        if (method, path) == ("POST", "/strategies"):
            return 200, {"id": 900}
        if method == "PUT" and path.startswith("/strategies/"):
            return 200, {"id": path.rsplit("/", 1)[-1]}
        if (method, path) == ("GET", "/brokerages"):
            return 200, {"accounts": self.brokerages}
        if (method, path) == ("POST", "/instances"):
            self.instances[body["id"]] = dict(body)
            return 200, body
        if method == "GET" and path.startswith("/instances/"):
            iid = path.split("/")[2]
            if iid not in self.instances:
                raise SystemExit(f"HTTP 404 on GET {path}")
            return 200, dict(self.instances[iid])
        return 200, {}

    def calls(self, method, prefix=""):
        return [(p, b) for m, p, b in self.seen if m == method and p.startswith(prefix)]


def test_the_lab_lane_is_enabled_defaults_plus_the_funding_flag():
    s = _setup()
    lane = s.swing_lane(lab=True)
    assert (lane["strategy"], lane["execution_scope"], lane["decision_phase"],
            lane["execution_position"], lane["weight"], lane["conditions"]) == (
        "strategy_swing", "run_once", "pre", 10, 1.0, {})
    assert lane["config"] == {**SWING_DEFAULTS, "strategy_swing_enabled": True,
                              "backtest_credit_pending_sell_proceeds": True,
                              "backtest_credit_sell_proceeds_enabled": True}
    # Only the swing lane: another lane's close-filled sell of a bracketed
    # symbol would run before a same-session stop in the simulator.
    assert s.lab_payload() == {"name": s.LAB_DOC_NAME, "strategies": [lane]}
    paper = s.paper_payload()["strategies"]
    assert [l["strategy"] for l in paper] == ["strategy_swing", "strategy_wheel"]
    assert "backtest_credit_pending_sell_proceeds" not in paper[0]["config"]
    assert "backtest_credit_sell_proceeds_enabled" not in paper[0]["config"]
    assert paper[1]["config"] == {**WHEEL_DEFAULTS, "strategy_wheel_enabled": True}
    assert paper[1]["execution_position"] == 20


def test_docs_200_to_203_are_refused_in_every_spelling():
    s = _setup()
    for doc_id in (200, "200", 201, " 201 ", 202, "203"):
        with pytest.raises(SystemExit):
            s.assert_writable(doc_id)
    assert s.assert_writable(204) == 204


def test_the_lab_watchlist_is_every_member_visible_in_the_window(store):
    s = _setup()
    members(store, [("2021-01-04", ["AAPL", "BRK.B", "OLD"]),
                    ("2021-09-20", ["AAPL", "BRK.B", "NEW"]),
                    ("2026-09-18", ["AAPL", "BRK.B", "LATE"])])
    watch = s.lab_watchlist(store, "2021-07-01", "2026-09-18")
    assert watch == sorted({"AAPL", "BRK.B", "OLD", "NEW", "SPY", "QQQ",
                            "XLP", "XLU", "XLV", "GLD", "SHY"})
    with pytest.raises(SystemExit, match="build_swing_reference_data"):
        s.lab_watchlist(store, "2020-01-01", "2020-06-01")


def test_a_fresh_lab_is_created_with_daily_bars_and_the_watchlist(store):
    s = _setup()
    members(store, [("2021-01-04", ["AAPL", "MSFT"])])
    api = Api(instances={**LIVE,
                         "strategy-eb": {"id": "strategy-eb", "brokerage_id": "brk-lab"}},
              brokerages=BROKERAGES)
    assert s.main(["--start", "2021-07-01", "--end", "2021-12-31"], call=api,
                  store=store) == 0
    ((_, doc),) = api.calls("POST", "/strategies")
    assert doc == s.lab_payload()
    body = api.instances[s.LAB_INSTANCE_ID]
    assert (body["strategy_id"], body["granularity"], body["brokerage_id"]) == (
        900, "86400", "brk-lab")
    assert "granularity_time_increment" not in body
    assert body["stocks"] == s.lab_watchlist(store, "2021-07-01", "2021-12-31")
    assert "runCommand" not in body and "run_command" not in body


def test_an_existing_lab_is_updated_in_place_and_relinked(store):
    s = _setup()
    members(store, [("2021-01-04", ["AAPL"])])
    api = Api(docs=[{"id": 444, "name": s.LAB_DOC_NAME}],
              instances={s.LAB_INSTANCE_ID: {"id": s.LAB_INSTANCE_ID, "strategy_id": 1}})
    assert s.main(["--start", "2021-07-01", "--end", "2021-12-31"], call=api,
                  store=store) == 0
    assert [p for p, _ in api.calls("PUT", "/strategies/")] == ["/strategies/444"]
    assert api.calls("POST", "/strategies") == []
    assert api.calls("POST", f"/instances/{s.LAB_INSTANCE_ID}/link-strategy") == [
        (f"/instances/{s.LAB_INSTANCE_ID}/link-strategy", {"strategy_id": 444})]
    assert not any(m == "PATCH" for m, _p, _b in api.seen)


def test_a_protected_doc_found_by_name_is_refused(store):
    s = _setup()
    members(store, [("2021-01-04", ["AAPL"])])
    api = Api(docs=[{"id": 201, "name": s.LAB_DOC_NAME}])
    with pytest.raises(SystemExit, match="201"):
        s.main(["--start", "2021-07-01", "--end", "2021-12-31"], call=api, store=store)
    assert api.calls("PUT") == []


def test_a_duplicate_stock_is_tolerated_and_a_real_failure_is_not(store):
    s = _setup()
    members(store, [("2021-01-04", ["AAPL"])])

    class Dup(Api):
        def __call__(self, method, path, body=None, **kwargs):
            if method == "POST" and path.endswith("/stocks"):
                raise SystemExit(f"HTTP 409 on POST {path}\n{{\"detail\":\"already added\"}}")
            return super().__call__(method, path, body, **kwargs)

    existing = {s.LAB_INSTANCE_ID: {"id": s.LAB_INSTANCE_ID}}
    assert s.main(["--start", "2021-07-01", "--end", "2021-12-31"],
                  call=Dup(docs=[{"id": 444, "name": s.LAB_DOC_NAME}], instances=existing),
                  store=store) == 0

    class Down(Api):
        def __call__(self, method, path, body=None, **kwargs):
            if method == "POST" and path.endswith("/stocks"):
                raise SystemExit(f"HTTP 500 on POST {path}")
            return super().__call__(method, path, body, **kwargs)

    with pytest.raises(SystemExit, match="500"):
        s.main(["--start", "2021-07-01", "--end", "2021-12-31"],
               call=Down(docs=[{"id": 444, "name": s.LAB_DOC_NAME}], instances=existing),
               store=store)


# -- --paper -------------------------------------------------------------------------


def test_paper_creates_both_lanes_on_a_proven_paper_brokerage(store):
    s = _setup()
    api = Api(instances=dict(LIVE), brokerages=BROKERAGES)
    assert s.main(["--paper", "--brokerage-id", "brk-paper"], call=api, store=store) == 0
    ((_, doc),) = api.calls("POST", "/strategies")
    assert doc == s.paper_payload()
    body = api.instances[s.PAPER_INSTANCE_ID]
    assert (body["brokerage_id"], body["strategy_id"], body["granularity"]) == (
        "brk-paper", 900, "60")
    assert body["stocks"] == s.paper_watchlist()
    assert "run_command" not in body


@pytest.mark.parametrize("argv,needle", [
    (["--paper"], "--brokerage-id"),
    (["--paper", "--brokerage-id", "brk-live"], "alpaca-main"),
    (["--paper", "--brokerage-id", "brk-unknown"], "paper"),
    (["--paper", "--brokerage-id", "brk-missing"], "not found"),
])
def test_paper_refuses_anything_it_cannot_prove_is_a_separate_paper_account(
        store, argv, needle):
    s = _setup()
    api = Api(instances=dict(LIVE), brokerages=BROKERAGES)
    with pytest.raises(SystemExit, match=needle):
        s.main(argv, call=api, store=store)
    assert api.calls("POST", "/instances") == [] and api.calls("POST", "/strategies") == []


def test_paper_refuses_when_alpaca_main_cannot_be_read(store):
    s = _setup()
    api = Api(instances={}, brokerages=BROKERAGES)
    with pytest.raises(SystemExit, match="alpaca-main"):
        s.main(["--paper", "--brokerage-id", "brk-paper"], call=api, store=store)


# -- G8a ruling 6: never alpaca-main, never docs 200-203; the help documents the
# -- daily granularity and Monday starts -----------------------------------------

def _writes(api):
    return [(m, p, b) for m, p, b in api.seen if m != "GET"]


def test_neither_flow_ever_writes_alpaca_main_or_a_protected_doc(store):
    s = _setup()
    members(store, [("2021-01-04", ["AAPL", "MSFT"])])
    lab = Api(instances={"strategy-eb": {"id": "strategy-eb", "brokerage_id": "brk-lab"}})
    assert s.main(["--start", "2021-07-12", "--end", "2021-12-31"], call=lab, store=store) == 0
    paper = Api(instances=dict(LIVE), brokerages=BROKERAGES)
    assert s.main(["--paper", "--brokerage-id", "brk-paper"], call=paper, store=store) == 0
    for api in (lab, paper):
        for method, path, body in _writes(api):
            assert "alpaca-main" not in path and not re.match(r"/strategies/20[0-3]\b", path)
            assert (body or {}).get("strategy_id") not in (200, 201, 202, 203)
            assert (body or {}).get("id") not in ("alpaca-main", "strategy-eb")
    # alpaca-main is only ever READ, to prove the brokerage differs.
    assert ("GET", "/instances/alpaca-main", None) in paper.seen


def test_help_documents_daily_granularity_and_monday_starts(capsys):
    s = _setup()
    with pytest.raises(SystemExit) as done:
        s.main(["--help"])
    assert done.value.code == 0
    text = capsys.readouterr().out
    assert "86400" in text and "Monday" in text and "200-203" in text


@pytest.mark.parametrize("start,noted", [("2021-07-01", True), ("2021-07-12", False)])
def test_a_window_that_does_not_start_on_a_monday_is_noted(store, capsys, start, noted):
    s = _setup()
    members(store, [("2021-01-04", ["AAPL"])])
    api = Api(instances={"strategy-eb": {"id": "strategy-eb", "brokerage_id": "brk-lab"}})
    assert s.main(["--start", start, "--end", "2021-12-31"], call=api, store=store) == 0
    out = capsys.readouterr().out
    assert ("not a Monday" in out) is noted



# -- G8a fix round 1 ---------------------------------------------------------------

def test_important_1_an_existing_paper_instance_on_another_brokerage_is_refused(store):
    s = _setup()
    instances = dict(LIVE)
    instances[s.PAPER_INSTANCE_ID] = {"id": s.PAPER_INSTANCE_ID, "brokerage_id": "brk-live",
                                      "strategy_id": 5}
    api = Api(instances=instances, brokerages=BROKERAGES)
    with pytest.raises(SystemExit, match="brk-live") as refused:
        s.main(["--paper", "--brokerage-id", "brk-paper"], call=api, store=store)
    assert refused.value.code not in (0, None)
    assert _writes(api) == []                      # no doc, no link, no stocks
    assert api.instances[s.PAPER_INSTANCE_ID]["brokerage_id"] == "brk-live"


def test_important_1_a_rerun_on_the_same_paper_brokerage_relinks(store):
    s = _setup()
    instances = dict(LIVE)
    instances[s.PAPER_INSTANCE_ID] = {"id": s.PAPER_INSTANCE_ID, "brokerage_id": "brk-paper",
                                      "strategy_id": 5}
    api = Api(docs=[{"id": 444, "name": s.PAPER_DOC_NAME}], instances=instances,
              brokerages=BROKERAGES)
    assert s.main(["--paper", "--brokerage-id", "brk-paper"], call=api, store=store) == 0
    assert api.calls("POST", f"/instances/{s.PAPER_INSTANCE_ID}/link-strategy") == [
        (f"/instances/{s.PAPER_INSTANCE_ID}/link-strategy", {"strategy_id": 444})]


@pytest.mark.parametrize("doc_id", [200.0, "0200", "200\n", "201.0", " 0203 ", "2e2"])
def test_m7_every_spelling_of_a_protected_doc_id_is_refused(doc_id):
    with pytest.raises(SystemExit):
        _setup().assert_writable(doc_id)


@pytest.mark.parametrize("argv", [["--start", "2021-13-01"], ["--end", "yesterday"],
                                  ["--start", "07/01/2021"]])
def test_m7_dates_are_validated(store, argv):
    s = _setup()
    with pytest.raises(SystemExit) as bad:
        s.main(argv, call=Api(), store=store)
    assert bad.value.code == 2


# -- FW-str minor (c): the lab never links a live brokerage -------------------------

@pytest.mark.parametrize("eb_brokerage, needle", [
    ("brk-eb-live", "not marked as an Alpaca paper account"),   # a live account
    ("brk-live", "alpaca-main"),                                 # the real-money one
    ("brk-unknown", "not marked as an Alpaca paper account"),   # paper not declared
    ("brk-missing", "not found"),
])
def test_a_fresh_lab_never_copies_a_brokerage_it_cannot_prove_is_paper(
        store, capsys, eb_brokerage, needle):
    """strategies-review M-8: the lab cloned strategy-eb's brokerage. If that
    is a live account, the lab instance of a swing document is linked to real
    money. Created with none instead, and the operator is told why."""
    s = _setup()
    members(store, [("2021-01-04", ["AAPL"])])
    api = Api(instances={**LIVE, "strategy-eb": {"id": "strategy-eb",
                                                 "brokerage_id": eb_brokerage}},
              brokerages=BROKERAGES)
    assert s.main(["--start", "2021-07-12", "--end", "2021-12-31"], call=api,
                  store=store) == 0
    body = api.instances[s.LAB_INSTANCE_ID]
    assert body["brokerage_id"] is None
    for _m, _p, written in _writes(api):
        assert eb_brokerage not in repr(written) and "brk-live" not in repr(written)
    out = capsys.readouterr().out
    assert "NO brokerage" in out and needle in out and "paper" in out


def test_a_fresh_lab_without_a_readable_alpaca_main_links_no_brokerage(store, capsys):
    s = _setup()
    members(store, [("2021-01-04", ["AAPL"])])
    api = Api(instances={"strategy-eb": {"id": "strategy-eb", "brokerage_id": "brk-lab"}},
              brokerages=BROKERAGES)
    assert s.main(["--start", "2021-07-12", "--end", "2021-12-31"], call=api,
                  store=store) == 0
    assert api.instances[s.LAB_INSTANCE_ID]["brokerage_id"] is None
    assert "NO brokerage" in capsys.readouterr().out


def test_an_existing_lab_on_a_live_brokerage_is_left_alone_and_flagged(store, capsys):
    s = _setup()
    members(store, [("2021-01-04", ["AAPL"])])
    lab = {"id": s.LAB_INSTANCE_ID, "strategy_id": 1, "brokerage_id": "brk-eb-live"}
    api = Api(docs=[{"id": 444, "name": s.LAB_DOC_NAME}],
              instances={**LIVE, s.LAB_INSTANCE_ID: lab}, brokerages=BROKERAGES)
    assert s.main(["--start", "2021-07-12", "--end", "2021-12-31"], call=api,
                  store=store) == 0
    assert api.instances[s.LAB_INSTANCE_ID]["brokerage_id"] == "brk-eb-live"
    assert not any("brokerage" in repr(b) for _m, _p, b in _writes(api))
    out = capsys.readouterr().out
    assert "WARNING" in out and "brk-eb-live" in out and "paper" in out
