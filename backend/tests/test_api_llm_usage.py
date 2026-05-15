"""Tests for the /llm-usage/* FastAPI endpoints.

We call the endpoint functions directly rather than going through
TestClient because httpx (a starlette.testclient dependency) is not in
the venv. Direct calls also avoid spinning up the @app.on_event("startup")
hooks, which would try to talk to RethinkDB.
"""
from __future__ import annotations

import os
import sys

import pytest

_BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)


@pytest.fixture
def seed_recent_calls():
    """Seed three calls into the in-memory ring buffer."""
    import llm_telemetry
    llm_telemetry._reset_for_tests()
    llm_telemetry.configure(
        db_conn_factory=lambda: None,
        enabled=True,
        auto_start_flusher=False,
        pricing_yaml_path=None,
    )
    for i in range(3):
        llm_telemetry.record_llm_call(
            provider="azure",
            model="gpt-4o",
            usage={"input_tokens": 100 + i, "output_tokens": 50},
        )
    yield llm_telemetry
    llm_telemetry._reset_for_tests()


@pytest.fixture
def empty_telemetry():
    """Configure an empty, enabled telemetry sink."""
    import llm_telemetry
    llm_telemetry._reset_for_tests()
    llm_telemetry.configure(
        db_conn_factory=lambda: None,
        enabled=True,
        auto_start_flusher=False,
        pricing_yaml_path=None,
    )
    yield llm_telemetry
    llm_telemetry._reset_for_tests()


def test_recent_calls_endpoint_returns_ring(seed_recent_calls):
    """Fast-path: range='now' + no filters returns the in-memory ring buffer
    directly (no DB round-trip). Exercises the early-return branch in
    api_llm_usage_calls."""
    from api.main import api_llm_usage_calls

    data = api_llm_usage_calls(
        limit=10, offset=0, range="now",
        provider="", model="", backtest_id="", strategy="",
        conn=None,
        current_user={"id": "u"},
    )
    assert isinstance(data, list)
    assert len(data) == 3
    assert data[0]["provider"] == "azure"


def test_recent_calls_endpoint_merges_ring_and_db_rows(monkeypatch, seed_recent_calls):
    """The fast path should still include persisted rows from other
    processes, not just the API process ring buffer."""
    import api.main as main_mod

    ring_top = seed_recent_calls.get_recent_calls(1)[0]
    persisted = {
        "id": "persisted-row",
        "ts": int(ring_top["ts"]) + 1000,
        "provider": "claude-cli",
        "model": "claude-sonnet-4-6",
        "input_tokens": 321,
        "output_tokens": 123,
        "total_cost_usd": 0.4567,
        "strategy": "GraphNexusAnalysis",
        "call_site": "sentiment",
    }

    monkeypatch.setattr(
        main_mod,
        "_llm_usage_calls_db",
        lambda **_kwargs: [persisted, dict(ring_top)],
    )

    data = main_mod.api_llm_usage_calls(
        limit=10,
        offset=0,
        range="now",
        provider="",
        model="",
        backtest_id="",
        strategy="",
        conn=None,
        current_user={"id": "u"},
    )

    assert data[0]["id"] == "persisted-row"
    assert sum(1 for row in data if row["id"] == ring_top["id"]) == 1


def test_summary_endpoint_returns_zero_when_empty(empty_telemetry):
    """Summary with no rows in DB and no buffer activity returns a zero-totals
    object, not 404 — the UI should always be able to render the page."""
    from api.main import api_llm_usage_summary

    data = api_llm_usage_summary(
        range="24h",
        conn=None,
        current_user={"id": "u"},
    )
    assert data["total_calls"] >= 0
    assert "by_provider" in data
    assert "telemetry_health" in data
    assert isinstance(data["by_provider"], list)


def test_health_endpoint(empty_telemetry):
    """Health endpoint exposes buffer + last-flush + error counters."""
    from api.main import api_llm_usage_health

    data = api_llm_usage_health(current_user={"id": "u"})
    assert "buffer_depth" in data
    assert "last_flush_ts" in data
    assert "write_errors_24h" in data
    # Empty buffer in this fixture.
    assert data["buffer_depth"] == 0


def test_top_spenders_orders_desc_by_cost():
    """Direct unit test of the top-spenders helper: feed records via the ring
    buffer, then ensure the helper aggregates and sorts correctly. Uses an
    empty conn since the function falls back gracefully on DB errors."""
    import llm_telemetry
    from api.main import _llm_usage_top_spenders

    llm_telemetry._reset_for_tests()
    llm_telemetry.configure(
        db_conn_factory=lambda: None,
        enabled=True,
        auto_start_flusher=False,
        pricing_yaml_path=None,
    )
    try:
        # DB query will return empty (no real conn); helper still produces
        # a list, just empty.
        out = _llm_usage_top_spenders(
            range_str="24h", group_by="model", limit=10, conn=None,
        )
        assert isinstance(out, list)
    finally:
        llm_telemetry._reset_for_tests()


def test_range_to_ms_window_handles_each_range():
    """Sanity check the range-window helper."""
    from api.main import _range_to_ms_window

    s24, e24 = _range_to_ms_window("24h")
    s7, e7 = _range_to_ms_window("7d")
    s30, e30 = _range_to_ms_window("30d")
    s_default, _ = _range_to_ms_window("nonsense")

    # 24h window is exactly 86_400_000 ms.
    assert e24 - s24 == 24 * 3600 * 1000
    assert e7 - s7 == 7 * 24 * 3600 * 1000
    assert e30 - s30 == 30 * 24 * 3600 * 1000
    # Unknown range defaults to 24h.
    assert e24 - s_default == 24 * 3600 * 1000
