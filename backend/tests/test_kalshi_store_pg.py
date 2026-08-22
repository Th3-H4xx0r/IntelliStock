"""`kalshi.*` over `db.store` — the semantics the ReQL port must keep.

The kalshi tables are the only ones in the repo with primary keys that are not
called `id`, so most of this file is about that: `KALSHI_TABLES` stays the
source of truth, `db.schema` agrees with it, and a document still carries its
key field.
"""
import os
import sys
import types

sys.modules.setdefault("socketio", types.ModuleType("socketio"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from db import schema as dbschema
from db.errors import StoreError
from kalshi import db as kdb

_NEEDS_PG = pytest.mark.skipif(not os.environ.get("PG_TEST_DSN"),
                               reason="real DDL needs PG_TEST_DSN")


@pytest.fixture
def kstore(store, monkeypatch):
    monkeypatch.setattr(kdb, "store", store)
    return store


# ── the registry ─────────────────────────────────────────────────────────────

def test_kalshi_tables_keeps_its_shape_and_size():
    assert isinstance(kdb.KALSHI_TABLES, (list, tuple))
    assert len(kdb.KALSHI_TABLES) == 27
    assert ("sports_fixtures", "fixture_id") in kdb.KALSHI_TABLES
    assert ("KalshiHistFixtures", "fixture_key") in kdb.KALSHI_TABLES


def test_every_registry_primary_key_is_declared_in_the_schema():
    """A pk the schema does not know about would be silently written to an
    `id` column that no document carries."""
    for table, pk in kdb.KALSHI_TABLES:
        assert dbschema.spec(table).pk_field == pk, table
        assert table in dbschema.ALL_TABLES, table


# ── non-`id` primary keys ────────────────────────────────────────────────────

def test_non_id_primary_key_round_trips(kstore):
    kstore.insert("KalshiHistFixtures", {"fixture_key": "F1", "result": "home"})
    got = kstore.get("KalshiHistFixtures", "F1")
    assert got == {"fixture_key": "F1", "result": "home"}    # pk stays in the doc


def test_missing_pk_field_raises(kstore):
    with pytest.raises(StoreError):
        kstore.insert("KalshiHistFixtures", {"result": "home"})


# ── db.py helpers ────────────────────────────────────────────────────────────

def test_bump_scan_budget_accumulates_on_the_window_key(kstore):
    assert kdb.bump_scan_budget(None, "2026-06-22T12:00:00Z") == 1
    assert kdb.bump_scan_budget(None, "2026-06-30T00:00:00Z", 3) == 4
    assert kdb.bump_scan_budget(None, "2026-07-01T00:00:00Z") == 1
    assert kstore.get("kalshi_scan_budget", "2026-06")["used"] == 4


def test_read_portfolio_snapshots_orders_by_ts_in_the_database(kstore):
    for ts in ("2026-01-03", "2026-01-01", "2026-01-02"):
        kdb.save_portfolio_snapshot(None, brokerage_id="b1", ts=ts,
                                    value_cents=1, cash_cents=1)
    kdb.save_portfolio_snapshot(None, brokerage_id="other", ts="2026-01-00",
                                value_cents=1, cash_cents=1)
    rows = kdb.read_portfolio_snapshots(None, "b1")
    assert [row["ts"] for row in rows] == ["2026-01-01", "2026-01-02", "2026-01-03"]


def test_write_order_is_keyed_on_client_order_id(kstore):
    kdb.write_order(None, client_order_id="c1", decision_id="d1",
                    market_ticker="KX-A", status="resting", requested=5)
    kdb.write_order(None, client_order_id="c1", decision_id="d1",
                    market_ticker="KX-A", status="filled", requested=5, filled=5)
    row = kstore.get("kalshi_orders", "c1")
    assert row["status"] == "filled" and row["filled"] == 5
    assert kstore.count("kalshi_orders") == 1


def test_upsert_fills_dedupes_on_the_stable_id(kstore):
    fill = {"market_ticker": "KX-A", "ts": "t", "price_cents": 40, "contracts": 2}
    assert kdb.upsert_fills(None, [fill, dict(fill)]) == 2
    assert kstore.count("kalshi_fills") == 1


def test_append_edge_history_caps_the_series(kstore):
    for i in range(5):
        kdb.append_edge_history(None, "inst", [{"market_ticker": "KX-A", "edge": i}],
                                "t%d" % i, cap=3)
    row = kstore.get("kalshi_edge_history", "inst|KX-A")
    assert [h["edge"] for h in row["history"]] == [2, 3, 4]


def test_mark_paper_positions_marks_only_open_paper_rows(kstore):
    kstore.insert("kalshi_decisions", [
        {"id": "1", "instance_id": "i", "decision": "placed", "paper": True,
         "market_ticker": "KX-A", "entry_avg_cents": 40, "size": 10},
        {"id": "2", "instance_id": "i", "decision": "placed", "paper": True,
         "market_ticker": "KX-B", "entry_avg_cents": 40, "size": 10,
         "outcome": "yes"},
        {"id": "3", "instance_id": "i", "decision": "skipped", "paper": True,
         "market_ticker": "KX-A", "entry_avg_cents": 40, "size": 10},
    ])
    out = kdb.mark_paper_positions(None, "i", {"KX-A": 50, "KX-B": 90})
    assert out == {"marked": 1, "unrealized_pnl_cents": 100}
    assert kstore.get("kalshi_decisions", "1")["unrealized_pnl_cents"] == 100
    assert "unrealized_pnl_cents" not in kstore.get("kalshi_decisions", "2")


def test_paper_pnl_totals_reads_only_this_instance(kstore):
    kstore.insert("kalshi_decisions", [
        {"id": "1", "instance_id": "i", "decision": "placed", "paper": True,
         "outcome": "yes", "realized_pnl_cents": 100},
        {"id": "2", "instance_id": "other", "decision": "placed", "paper": True,
         "outcome": "yes", "realized_pnl_cents": 999},
    ])
    assert kdb.paper_pnl_totals(None, "i")["realized_cents"] == 100


def test_get_champion_falls_back_to_the_default_scope(kstore):
    kstore.insert("KalshiModelRegistry", [
        {"id": "v1", "instance_id": "__default__", "kind": "calibrator",
         "is_champion": True, "created_at": "2026-01-01"},
        {"id": "v2", "instance_id": "i", "kind": "calibrator",
         "is_champion": False, "created_at": "2026-02-01"},
    ])
    assert kdb.get_champion(None, "i")["id"] == "v1"
    kdb.set_champion(None, "v2", "i")
    assert kdb.get_champion(None, "i")["id"] == "v2"


def test_set_champion_demotes_the_prior_one_in_the_same_scope(kstore):
    kstore.insert("KalshiModelRegistry", [
        {"id": "old", "instance_id": "i", "kind": "calibrator",
         "is_champion": True, "created_at": "2026-01-01"},
        {"id": "elsewhere", "instance_id": "j", "kind": "calibrator",
         "is_champion": True, "created_at": "2026-01-01"},
    ])
    kdb.set_champion(None, "new", "i")
    assert kstore.get("KalshiModelRegistry", "old")["is_champion"] is False
    assert kstore.get("KalshiModelRegistry", "elsewhere")["is_champion"] is True


def test_pending_or_running_backtests_matches_both_states(kstore):
    kstore.insert("KalshiBacktests", [
        {"id": "a", "status": "pending"}, {"id": "b", "status": "running"},
        {"id": "c", "status": "finished"},
    ])
    got = {row["id"] for row in kdb.pending_or_running_backtests(None)}
    assert got == {"a", "b"}


def test_list_backtests_filters_by_brokerage(kstore):
    kstore.insert("KalshiBacktests", [
        {"id": "a", "brokerage_id": "b1", "created_at": "2026-01-01"},
        {"id": "b", "brokerage_id": "b1", "created_at": "2026-02-01"},
        {"id": "c", "brokerage_id": "b2", "created_at": "2026-03-01"},
    ])
    assert [row["id"] for row in kdb.list_backtests(None, "b1")] == ["b", "a"]


def test_delete_backtest_removes_the_result_too(kstore):
    kstore.insert("KalshiBacktests", {"id": "a"})
    kstore.insert("KalshiBacktestResults", {"id": "a"})
    kdb.delete_backtest(None, "a")
    assert kstore.get("KalshiBacktests", "a") is None
    assert kstore.get("KalshiBacktestResults", "a") is None


def test_an_empty_ticker_set_matches_nothing(kstore):
    """`r.expr([]).contains(x)` is false for every row; `= ANY('{}')` must be
    too, or a settlement pass with no tickers would grade the whole table."""
    from db import P
    kstore.insert("kalshi_fills", {"id": "f", "market_ticker": "KX-A"})
    sel = kstore.filter("kalshi_fills", P.field("market_ticker").is_in([]))
    assert kstore.run(sel) == []
    sel = kstore.filter("kalshi_fills", P.field("market_ticker").is_in(["KX-A"]))
    assert len(kstore.run(sel)) == 1


# ── the pending-backtest watcher ─────────────────────────────────────────────

def test_watch_pending_skips_the_initial_rows():
    import kalshi.backtest_worker as kbw
    w = kbw._watch_pending(lambda change: None)
    assert w.table == "KalshiBacktests"
    assert w.predicate == {"status": "pending"}
    assert w.include_initial is False        # the drain owns existing rows
    assert not w.is_alive()                  # returned unstarted


@_NEEDS_PG
def test_watch_pending_delivers_a_new_row(kstore):
    import kalshi.backtest_worker as kbw
    kstore.insert("KalshiBacktests", {"id": "kb0", "status": "pending"})
    seen = []
    w = kbw._watch_pending(seen.append, poll_interval=0.1)
    w.start()
    try:
        import time
        time.sleep(1.0)                      # let it seed the existing row
        kstore.insert("KalshiBacktests", {"id": "kb1", "status": "pending"})
        deadline = time.time() + 5
        while time.time() < deadline and not seen:
            time.sleep(0.05)
    finally:
        w.stop()
    assert [c["new_val"]["id"] for c in seen] == ["kb1"]   # kb0 never delivered


# ── DDL ──────────────────────────────────────────────────────────────────────

@_NEEDS_PG
def test_ensure_tables_creates_every_registered_table(kstore):
    created = kdb.ensure_tables(None)
    assert created == []                    # the fixture already ensured them
    names = set(kstore.table_list())
    for table, _pk in kdb.KALSHI_TABLES:
        assert table in names, table
