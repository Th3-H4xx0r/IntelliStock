"""G11 long-tail store semantics.

``rethink_changefeed`` already landed on the new contract in Plan A
(commit f757b89), so the first test is a regression guard rather than a
new requirement.
"""
import os
import sys

import psycopg
import psycopg_pool
import pytest

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)


def test_is_transient_db_error_reclassified():
    from rethink_changefeed import is_transient_db_error, is_transient_rethinkdb_error

    assert is_transient_rethinkdb_error is is_transient_db_error   # alias kept
    assert is_transient_db_error(psycopg.OperationalError("server closed"))
    assert is_transient_db_error(psycopg_pool.PoolTimeout("timed out"))
    assert is_transient_db_error(ConnectionResetError("reset"))
    assert is_transient_db_error(OSError("broken pipe"))
    assert not is_transient_db_error(ValueError("bad json"))


def test_earnings_is_in_filter(store):
    store.insert("EarningsLLMCache", [
        {"id": "1", "ticker": "AAPL"}, {"id": "2", "ticker": "MSFT"},
        {"id": "3", "ticker": "NVDA"},
    ])
    rows = store.run(store.filter("EarningsLLMCache",
                                  store.P.field("ticker").is_in(["AAPL", "NVDA"])))
    assert sorted(row["id"] for row in rows) == ["1", "3"]
    # An empty key set is `false`, not "match everything".
    assert store.run(store.filter("EarningsLLMCache",
                                  store.P.field("ticker").is_in([]))) == []


def test_llm_telemetry_write_survives_a_store_error(store, monkeypatch):
    """Telemetry must never take down a caller."""
    import llm_telemetry
    from db.errors import UnavailableError

    class _Boom:
        def insert(self, *a, **k):
            raise UnavailableError("down")

    llm_telemetry._reset_for_tests()
    try:
        llm_telemetry.configure(db_conn_factory=lambda: None, r_module=_Boom(),
                                enabled=True, auto_start_flusher=False,
                                pricing_yaml_path=None)
        llm_telemetry.record_llm_call(provider="x", model="y",
                                      usage={"input_tokens": 1, "output_tokens": 0})
        llm_telemetry.flush()          # must not raise
        # ...and the failed batch is re-queued rather than dropped.
        assert llm_telemetry._state["write_errors_24h"] >= 1
        assert llm_telemetry.get_buffer_depth() == 1     # re-queued, not dropped
    finally:
        llm_telemetry._reset_for_tests()


def test_llm_telemetry_flush_writes_through_the_store(store, monkeypatch):
    import llm_telemetry

    llm_telemetry._reset_for_tests()
    try:
        llm_telemetry.configure(db_conn_factory=lambda: None, r_module=store,
                                enabled=True, auto_start_flusher=False,
                                pricing_yaml_path=None)
        llm_telemetry.record_llm_call(provider="azure", model="gpt-4o",
                                      usage={"input_tokens": 3, "output_tokens": 1})
        llm_telemetry.flush()
        rows = store.run("LLMUsage")
        assert len(rows) == 1 and rows[0]["provider"] == "azure"
    finally:
        llm_telemetry._reset_for_tests()


def test_prefix_scan_finds_scope_suffixed_ids(store):
    """clear_instance_state.py:100-104: exact-only matching once found ZERO
    scoped rows and turned a full clear into a silent no-op (R13)."""
    store.insert("NexusRuntimeState", [
        {"id": "alpaca-main"},
        {"id": "alpaca-main|deadbeef"},
        {"id": "alpaca-mainland"},
        {"id": "other"},
    ])
    rows = store.run(store.filter("NexusRuntimeState",
                                  store.P.field("id").starts_with("alpaca-main")))
    assert len(rows) == 3
    rows = store.run(store.filter("NexusRuntimeState",
                                  store.P.field("id").starts_with("alpaca-main|")))
    assert [r["id"] for r in rows] == ["alpaca-main|deadbeef"]


def test_strategy_cache_round_trip(store, monkeypatch):
    import strategy_cache_persistence as scp

    monkeypatch.setattr(scp, "store", store)
    # ensure_table() does real DDL; the FakeStore has no server to run it on.
    monkeypatch.setattr(scp, "_ensure_table", lambda conn=None, r=None: True)
    scp.save_strategy_cache_to_db(None, None, "inst-1", "graph_nexus_analysis",
                                  {"a": 1})
    got = scp.load_strategy_cache_from_db(None, None, "inst-1",
                                          "graph_nexus_analysis")
    assert got == {"a": 1}
    assert scp.load_strategy_cache_from_db(None, None, "nope",
                                           "graph_nexus_analysis") is None


def test_model_resolver_reads_the_store(store, monkeypatch):
    import model_resolver

    monkeypatch.setattr(model_resolver, "store", store)
    model_resolver.invalidate_model_cache()
    store.insert("Models", {"id": "m1", "provider": "azure", "api_key": "sk-x"})
    got = model_resolver._get_model_from_cache_or_db(None, "m1")
    assert got["api_key"] == "sk-x"
    assert model_resolver._get_model_from_cache_or_db(None, "missing") is None
    model_resolver.invalidate_model_cache()


def test_live_boot_audit_persists_and_is_idempotent(store):
    import live_boot_audit

    row = {"id": "inst-1|2026-08-22T00:00:00Z", "instance_id": "inst-1",
           "boot_at_utc": "2026-08-22T00:00:00Z"}
    live_boot_audit.persist_audit_row(r=store, conn=None, row=row)
    live_boot_audit.persist_audit_row(r=store, conn=None, row=row)
    assert store.count("LiveBootAudit") == 1
