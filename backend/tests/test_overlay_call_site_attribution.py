"""Task 9 — overlay LLM calls must attribute call_site from worker threads.

Root cause (forensics): `_apply_trade_overlay` / `_apply_etf_trade_overlay`
run inside a ThreadPoolExecutor and passed attribution only via
`attribution_keys=` — which enriches the LLMCriticalFailure exception but
NEVER the thread-local telemetry stack that `record_llm_call` reads. With no
ambient context in the worker thread, `_merge_active_ctx()` returned empty and
usage rows recorded call_site="(unset)".

Fix pattern mirrors the sentiment site (:14437): enter `llm_call_context(...)`
INSIDE the worker body so the merged context carries call_site + ids.

These tests invoke the worker bodies SYNCHRONOUSLY (no executor) with a fake
`_scl_guarded` that snapshots the live telemetry context at call time.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import llm_telemetry
from strategies import graph_nexus_analysis as gna


def _install_fakes(monkeypatch, captured):
    monkeypatch.setattr(
        gna, "_resolve_role_llm_config",
        lambda config, role: ("openai", "TESTKEY", "gpt-x", "v1"),
    )
    monkeypatch.setattr(
        gna, "_resolve_role_llm_provider_config",
        lambda config, role: {},
    )
    monkeypatch.setattr(gna, "_build_llm_trace", lambda *a, **k: {})

    def _fake_scl(*a, **k):
        # Snapshot the thread-local telemetry context AT CALL TIME — this is
        # exactly what record_llm_call() would merge into the usage row.
        captured.append({
            "ctx": dict(llm_telemetry._merge_active_ctx()),
            "attribution_keys": dict(k.get("attribution_keys") or {}),
        })
        return None  # -> function returns ({}, trace) cleanly

    monkeypatch.setattr(gna, "_scl_guarded", _fake_scl)


def _cfg():
    return {
        "_telemetry_backtest_id": "586767",
        "_telemetry_instance_id": "alpaca-main|e3fdb8e1",
        "llm_overlay_enabled": True,
        "use_toon_format": False,
    }


def test_stock_overlay_records_call_site(monkeypatch):
    captured = []
    _install_fakes(monkeypatch, captured)
    gna._apply_trade_overlay(
        "AAPL",
        {"graph_raw_score": 0.1},
        feature_row={"date_key": "2026-06-02"},
        active_events=[],
        historical_analogs=[],
        config=_cfg(),
    )
    assert captured, "_scl_guarded was not invoked"
    ctx = captured[0]["ctx"]
    assert ctx.get("call_site") == "overlay"
    assert ctx.get("instance_id") == "alpaca-main|e3fdb8e1"
    assert ctx.get("backtest_id") == "586767"
    # attribution_keys (exception enrichment) must remain untouched
    assert captured[0]["attribution_keys"].get("call_site") == "overlay"


def test_etf_overlay_records_call_site(monkeypatch):
    captured = []
    _install_fakes(monkeypatch, captured)
    gna._apply_etf_trade_overlay(
        "XLE",
        {"base_score": 1},
        feature_row={"date_key": "2026-06-02"},
        active_events=[],
        active_trends=[],
        config=_cfg(),
    )
    assert captured, "_scl_guarded was not invoked"
    ctx = captured[0]["ctx"]
    assert ctx.get("call_site") == "overlay_etf"
    assert ctx.get("instance_id") == "alpaca-main|e3fdb8e1"
    assert ctx.get("backtest_id") == "586767"
    assert captured[0]["attribution_keys"].get("call_site") == "overlay_etf"


def test_worker_context_pops_after_return(monkeypatch):
    # The context manager must not leak frames into the caller thread.
    captured = []
    _install_fakes(monkeypatch, captured)
    gna._apply_trade_overlay(
        "MSFT",
        {"graph_raw_score": 0.0},
        feature_row={"date_key": "2026-06-02"},
        active_events=[],
        historical_analogs=[],
        config=_cfg(),
    )
    assert llm_telemetry._merge_active_ctx() == {}
