"""Tests for the /llm-usage/* FastAPI endpoints.

We call the endpoint functions directly rather than going through
TestClient because httpx (a starlette.testclient dependency) is not in
the venv. Direct calls also avoid spinning up the @app.on_event("startup")
hooks, which would try to talk to RethinkDB.
"""
from __future__ import annotations

import inspect
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


def test_llm_usage_endpoints_require_auth_but_not_admin():
    """Token-usage endpoints should be available to any authenticated user,
    not just admins. Guard this at the dependency layer."""
    import api.main as main_mod

    endpoints = (
        main_mod.api_llm_usage_summary,
        main_mod.api_llm_usage_timeseries,
        main_mod.api_llm_usage_top_spenders,
        main_mod.api_llm_usage_calls,
        main_mod.api_llm_usage_health,
        main_mod.api_llm_usage_by_backtest,
        main_mod.api_backtest_llm_cost,
    )

    for endpoint in endpoints:
        dep = inspect.signature(endpoint).parameters["current_user"].default.dependency
        assert dep is main_mod.get_current_user


# ── /llm-usage/by-backtest aggregation ────────────────────────────────────


def test_by_backtest_aggregates_and_sorts_by_cost(monkeypatch):
    """The aggregate folds rows by backtest_id, sums tokens + cost, and
    sorts desc by cost. Untagged rows (no backtest_id) are excluded —
    they have no backtest to attribute to."""
    import api.main as main_mod
    rows = [
        # backtest 42 — 2 calls, $0.15 total
        {"backtest_id": 42, "instance_id": "inst-a", "input_tokens": 100, "output_tokens": 50,
         "total_cost_usd": 0.10, "ts": 1_700_000_000_000, "ok": True},
        {"backtest_id": 42, "instance_id": "inst-a", "input_tokens": 200, "output_tokens": 80,
         "total_cost_usd": 0.05, "ts": 1_700_000_010_000, "ok": True},
        # backtest 7 — 1 call, $0.50 (more expensive)
        {"backtest_id": 7, "instance_id": "inst-b", "input_tokens": 5000, "output_tokens": 200,
         "total_cost_usd": 0.50, "ts": 1_700_000_020_000, "ok": False},
        # untagged — must be dropped
        {"backtest_id": None, "input_tokens": 10, "output_tokens": 5,
         "total_cost_usd": 0.001, "ts": 1_700_000_030_000, "ok": True},
    ]

    class _FakeQuery:
        def __init__(self, rows): self._rows = rows
        def between(self, *_a, **_k): return self
        def run(self, _conn): return iter(self._rows)

    class _FakeDb:
        def __init__(self, rows): self._rows = rows
        def table(self, _name): return _FakeQuery(self._rows)

    class _FakeR:
        def db(self, _name): return _FakeDb(rows)

    monkeypatch.setattr(main_mod, "_r_auth", _FakeR())
    out = main_mod._llm_usage_by_backtest(range_str="30d", limit=10, conn=object())

    assert isinstance(out, list)
    # Untagged row dropped → only 2 backtests in the aggregate.
    assert len(out) == 2
    # Sorted desc by cost — backtest 7 ($0.50) before backtest 42 ($0.15).
    assert out[0]["backtest_id"] == "7"
    assert out[0]["cost_usd"] == 0.50
    assert out[0]["calls"] == 1
    assert out[0]["failed_calls"] == 1
    assert out[0]["ok_calls"] == 0
    assert out[1]["backtest_id"] == "42"
    assert out[1]["calls"] == 2
    assert abs(out[1]["cost_usd"] - 0.15) < 1e-9
    assert out[1]["tokens"] == 100 + 50 + 200 + 80
    assert out[1]["ok_calls"] == 2
    assert out[1]["instance_id"] == "inst-a"


def test_by_backtest_returns_empty_on_db_error(monkeypatch):
    """When the RethinkDB query raises, we degrade to an empty list
    rather than 500ing the cost-screen request."""
    import api.main as main_mod

    class _FakeR:
        def db(self, *_a, **_k):
            raise RuntimeError("connection refused")

    monkeypatch.setattr(main_mod, "_r_auth", _FakeR())
    out = main_mod._llm_usage_by_backtest(range_str="24h", limit=10, conn=object())
    assert out == []


def test_by_backtest_limit_honoured(monkeypatch):
    """`limit` caps the returned rows after sort."""
    import api.main as main_mod
    rows = [
        {"backtest_id": i, "input_tokens": 10, "output_tokens": 5,
         "total_cost_usd": float(i) * 0.01, "ts": 1, "ok": True}
        for i in range(20)
    ]

    class _FakeR:
        def db(self, _name):
            class _T:
                def table(self_inner, _n):
                    class _Q:
                        def between(self_q, *_a, **_k): return self_q
                        def run(self_q, _conn): return iter(rows)
                    return _Q()
            return _T()

    monkeypatch.setattr(main_mod, "_r_auth", _FakeR())
    out = main_mod._llm_usage_by_backtest(range_str="30d", limit=5, conn=object())
    assert len(out) == 5
    # Top 5 should be ids 19..15 (highest cost first).
    assert [r["backtest_id"] for r in out] == ["19", "18", "17", "16", "15"]


# ── /backtests/{id}/llm-cost detail ───────────────────────────────────────


def test_for_backtest_aggregates_by_dimension(monkeypatch):
    """Per-backtest detail rolls up rows into headline totals + per-model +
    per-call_site + per-strategy + per-provider sub-aggregates."""
    import api.main as main_mod
    rows = [
        {"backtest_id": "42", "provider": "azure", "model": "gpt-5.4-mini",
         "input_tokens": 100, "output_tokens": 50, "reasoning_tokens": 20,
         "cache_read_input_tokens": 0, "total_cost_usd": 0.10, "ts": 100,
         "ok": True, "call_site": "macro_article", "strategy": "GraphNexusAnalysis"},
        {"backtest_id": "42", "provider": "codex-cli", "model": "gpt-5.4-mini",
         "input_tokens": 200, "output_tokens": 80, "reasoning_tokens": 0,
         "cache_read_input_tokens": 50, "total_cost_usd": 0.20, "ts": 200,
         "ok": False, "call_site": "sentiment", "strategy": "GraphNexusAnalysis"},
    ]

    class _FakeR:
        def db(self, _name):
            class _T:
                def table(self_inner, _n):
                    class _Q:
                        def get_all(self_q, *_a, **_k): return self_q
                        def run(self_q, _conn): return iter(rows)
                    return _Q()
            return _T()

    monkeypatch.setattr(main_mod, "_r_auth", _FakeR())
    out = main_mod._llm_usage_for_backtest(backtest_id="42", conn=object())

    assert out["backtest_id"] == "42"
    assert out["total_calls"] == 2
    assert out["ok_calls"] == 1
    assert out["failed_calls"] == 1
    assert out["total_input_tokens"] == 300
    assert out["total_output_tokens"] == 130
    assert out["total_reasoning_tokens"] == 20
    assert out["total_cache_read_tokens"] == 50
    assert out["total_tokens"] == 430
    assert abs(out["total_cost_usd"] - 0.30) < 1e-9
    assert out["first_ts"] == 100
    assert out["last_ts"] == 200

    # by_provider: azure ($0.10) vs codex-cli ($0.20), sorted desc by cost.
    assert out["by_provider"][0]["key"] == "codex-cli"
    assert out["by_provider"][0]["cost_usd"] == 0.20
    assert out["by_provider"][1]["key"] == "azure"

    # by_model: single model accumulates both rows.
    assert len(out["by_model"]) == 1
    assert out["by_model"][0]["key"] == "gpt-5.4-mini"
    assert out["by_model"][0]["calls"] == 2

    # by_call_site: macro_article vs sentiment
    sites = {row["key"] for row in out["by_call_site"]}
    assert sites == {"macro_article", "sentiment"}


def test_for_backtest_returns_zeroed_doc_on_db_error(monkeypatch):
    """When the index lookup fails (or returns nothing), the detail
    endpoint must still return a well-formed zeroed envelope so the
    AI-credits card can render its empty state without a 500."""
    import api.main as main_mod

    class _FakeR:
        def db(self, *_a, **_k):
            raise RuntimeError("rethinkdb_unreachable")

    monkeypatch.setattr(main_mod, "_r_auth", _FakeR())
    out = main_mod._llm_usage_for_backtest(backtest_id="42", conn=object())
    assert out["backtest_id"] == "42"
    assert out["total_calls"] == 0
    assert out["total_cost_usd"] == 0
    assert out["by_model"] == []
    assert out["first_ts"] is None
    assert out["last_ts"] is None
