"""Transport-only tests for the HX engine controller.

No network, no engine, no simulated performance. Every test injects a fake
`call` and asserts on what the controller would have SENT.
"""
import importlib.util
import json
import os
import sys

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ROOT = os.path.dirname(_BACKEND)
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)


def _module():
    path = os.path.join(_ROOT, "scripts", "hx_run_native_api.py")
    spec = importlib.util.spec_from_file_location("_hx_ctl", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _listing(rows):
    return {"backtests": rows, "total": len(rows), "total_pages": 1}


def test_the_frozen_variants_and_windows_are_what_the_spec_registered():
    """Both tables are preregistered. A silent edit to either turns a
    pass/fail verdict into an unfalsifiable one."""
    ctl = _module()
    assert ctl.VARIANTS == {
        "V1": {"PSQ": 0.60, "BIL": 0.40},
        "V2": {"SQQQ": 0.25, "BIL": 0.75},
        "V3": {"SH": 0.60, "BIL": 0.40},
        "V4": {"GLD": 0.30, "BIL": 0.70},
        "V5": {"PSQ": 0.40, "GLD": 0.20, "BIL": 0.40},
    }
    assert ctl.WINDOWS["rb1"] == ("bear", "2022-01-01", "2022-06-30")
    assert ctl.WINDOWS["rb2"] == ("bear", "2026-02-01", "2026-04-01")
    assert ctl.WINDOWS["rb3"] == ("bear", "2025-02-15", "2025-04-15")
    assert ctl.WINDOWS["cyc"] == ("multi", "2021-11-01", "2026-08-27")
    assert len(ctl.WINDOWS) == 25
    assert ctl.BEAR_ORDER == ("rb3", "rb2", "rb1")


def test_a_running_job_refuses_the_whole_run():
    """One job at a time is a user veto from 2026-09-03, not a preference."""
    ctl = _module()

    def fake_call(method, path, body=None, **kwargs):
        return 200, _listing([{"id": 9, "status": "running"}])

    assert ctl.engine_busy(fake_call) == [9]


def test_the_queue_listing_is_paginated_until_total():
    """A clipped listing reads as an idle engine, which is exactly how two
    containers end up on it at once."""
    ctl = _module()
    pages = {1: [{"id": i, "status": "finished"} for i in range(100)],
             2: [{"id": 100, "status": "queued"}]}
    seen = []

    def fake_call(method, path, body=None, **kwargs):
        seen.append(path)
        page = 2 if "page=2" in path else 1
        return 200, {"backtests": pages[page], "total": 101,
                     "total_pages": 2}

    assert ctl.engine_busy(fake_call) == [100]
    assert any("per_page=100" in p for p in seen)
    assert any("page=2" in p for p in seen)


def test_applying_a_variant_puts_the_bear_book_and_refuses_protected_docs():
    ctl = _module()
    sent = {}

    def fake_call(method, path, body=None, **kwargs):
        if path == f"/instances/{ctl.INSTANCE_ID}":
            return 200, {"strategy_id": 444}
        if path == "/strategies/444" and method == "GET":
            return 200, {"name": ctl.DOC_NAME, "strategies": [
                {"strategy": "strategy_hx",
                 "config": {"bear_book": {"PSQ": 0.6, "BIL": 0.4}}}]}
        if method == "PUT":
            sent["body"] = body
            return 200, {}
        return 200, {}

    assert ctl.apply_variant(fake_call, "V2") == "444"
    assert sent["body"]["strategies"][0]["config"]["bear_book"] == {
        "SQQQ": 0.25, "BIL": 0.75}

    def protected_call(method, path, body=None, **kwargs):
        if path == f"/instances/{ctl.INSTANCE_ID}":
            return 200, {"strategy_id": 201}
        raise AssertionError("must refuse before reading doc 201")

    try:
        ctl.apply_variant(protected_call, "V2")
    except SystemExit as error:
        assert "201" in str(error)
    else:
        raise AssertionError("doc 201 was not refused")


def test_the_post_body_is_the_frozen_engine_contract():
    ctl = _module()
    body = ctl.post_body("V5", "2025-02-15", "2025-04-15")
    assert body["instance_id"] == "strategy-hx-lab"
    assert body["granularity"] == "86400"
    assert body["initial_cash"] == 6000
    assert body["equity_cost_tiers"] == "etf-liquid"
    assert body["evidence_mode"] == "off"
    # V5's GLD leg must be in the stocks list or it has no bars.
    assert body["stocks"] == ["BIL", "GLD", "PSQ", "QQQ", "SPY", "TQQQ"]


def test_a_twenty_five_percent_drawdown_stops_the_job():
    ctl = _module()
    calls = []

    def fake_call(method, path, body=None, **kwargs):
        calls.append((method, path))
        if path.endswith("/status"):
            return 200, {"status": "running"}
        if path.endswith("/summary"):
            return 200, {"risk_metrics": {"max_drawdown_pct": 0.2512,
                                          "observation_count": 40}}
        return 200, {}

    assert ctl.should_stop(fake_call, "bt1") is True
    assert ctl.drawdown_of({"risk_metrics": {"max_drawdown_pct": 0.19}}) == 0.19
    assert ctl.drawdown_of({"risk_metrics": {}}) is None
    assert ctl.drawdown_of({"risk_metrics": {"max_drawdown_pct": True}}) is None
    assert ctl.drawdown_of({"risk_metrics": {"max_drawdown_pct": 4.0}}) is None


def test_the_archive_drops_the_equity_curve_and_writes_three_files(tmp_path):
    """A cycle summary carries thousands of NAV points. Keeping them turns a
    verdict file into a megabyte nobody reads."""
    ctl = _module()

    def fake_call(method, path, body=None, **kwargs):
        if path.endswith("/summary"):
            return 200, {"equity_curve": [1, 2, 3], "pnl_percent": 4.2,
                         "risk_metrics": {"max_drawdown_pct": 0.11}}
        if path.endswith("/logs"):
            return 200, {"logs": ["line"]}
        return 200, {"nodes": []}

    out = tmp_path / "V1-rb3"
    summary = ctl.archive(fake_call, "bt9", str(out))
    assert "equity_curve" not in summary
    assert summary["pnl_percent"] == 4.2
    for name in ("summary.json", "logs.json", "graph-data.json"):
        assert (out / name).exists()
    saved = json.loads((out / "summary.json").read_text())
    assert "equity_curve" not in saved


def test_the_verdict_line_names_the_spy_benchmark_or_says_it_is_unknown():
    ctl = _module()
    line = ctl.verdict("V1", "rb1", {"pnl_percent": 3.1,
                                     "risk_metrics": {"max_drawdown_pct": .09}})
    assert "rb1" in line and "-19.40" in line and "+3.10" in line
    assert "SPY n/a" in ctl.verdict("V1", "h3", {"pnl_percent": 1.0,
                                                 "risk_metrics": {}})
