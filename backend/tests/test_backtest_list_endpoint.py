"""The list fast path must return what the legacy pluck+merge returned."""
import os

import pytest

import backtest_result_store as brs
from db import schema, store

pytestmark = pytest.mark.skipif(
    not os.environ.get("PG_TEST_DSN"),
    reason="PG_TEST_DSN not set (run: ./scripts/dev_pg.sh up)")

_LIST_TICKER_PREVIEW = 4


@pytest.fixture
def seeded(pg_schema):
    schema.ensure_schema(tables=["BacktestResults", "BacktestSteps",
                                 "BacktestProgress"])
    for i in range(1, 8):
        doc = {
            "id": 800000 + i, "backtest_id": 800000 + i,
            "instance_id": "main" if i % 2 else 5,
            "status": "running" if i == 7 else "finished",
            "progress": 42.5 if i == 7 else 100,
            "timestamp": "2026-08-%02dT00:00:00" % (10 + i),
            "start_date": "2026-03-01 00:00:00", "end_date": "2026-04-01 00:00:00",
            "tickers": ["T%d" % n for n in range(i)],
            "time_elapsed_seconds": 10 * i,
            "pnl": 1.0 * i, "pnl_percent": 0.1 * i,
            "portfolio_value_history": [], "backtest_trades": [],
            "backtest_prices": [], "logs": [],
            "strategy_schema": {"blob": "x" * 40000},   # must never be read
        }
        brs.write_split(doc, final=(i != 7))
    return pg_schema


def test_list_returns_only_the_summary_fields(seeded):
    active, page, total = brs.list_rows(instance_filter=None, page=1,
                                        per_page=50, sort_order="desc",
                                        ticker_preview=_LIST_TICKER_PREVIEW)
    assert total == 7
    for row in page:
        assert "strategy_schema" not in row
        assert "backtest_decisions" not in row
        assert set(row) <= set(brs.LIST_FIELDS) | {"tickers_total"}


def test_tickers_are_previewed_and_counted(seeded):
    _active, page, _ = brs.list_rows(instance_filter=None, page=1, per_page=50,
                                     sort_order="desc",
                                     ticker_preview=_LIST_TICKER_PREVIEW)
    row = [r for r in page if r["id"] == 800007][0]
    assert row["tickers"] == ["T0", "T1", "T2", "T3"]       # limit(4)
    assert row["tickers_total"] == 7                        # count()


def test_a_row_without_tickers_previews_as_an_empty_list(seeded):
    brs.write_split({"id": 800100, "status": "finished", "progress": 100,
                     "timestamp": "2026-08-01T00:00:00"}, final=True)
    _a, page, _t = brs.list_rows(instance_filter=None, page=1, per_page=50,
                                 sort_order="desc", ticker_preview=4)
    row = [r for r in page if r["id"] == 800100][0]
    assert row["tickers"] == [] and row["tickers_total"] == 0


def test_status_and_progress_come_from_the_hot_row(seeded):
    brs.write_progress(800001, {"status": "paused", "progress": 12.5})
    _a, page, _t = brs.list_rows(instance_filter=None, page=1, per_page=50,
                                 sort_order="desc", ticker_preview=4)
    row = [r for r in page if r["id"] == 800001][0]
    assert row["status"] == "paused" and row["progress"] == 12.5


def test_active_rows_use_the_status_norm_index(seeded):
    brs.write_progress(800002, {"status": "paused_llm_critical"})
    active, _page, _t = brs.list_rows(instance_filter=None, page=1, per_page=50,
                                      sort_order="desc", ticker_preview=4)
    ids = {r["id"] for r in active}
    assert 800007 in ids                     # running
    assert 800002 in ids                     # paused_* normalises to paused


def test_ordering_is_timestamp_desc_bytewise(seeded):
    _a, page, _t = brs.list_rows(instance_filter=None, page=1, per_page=50,
                                 sort_order="desc", ticker_preview=4)
    stamps = [r["timestamp"] for r in page]
    assert stamps == sorted(stamps, key=lambda s: s.encode("utf-8"), reverse=True)


def test_instance_filter_matches_both_number_and_string_instance_ids(seeded):
    """592 live rows carry instance_id as a NUMBER, 833 as a STRING."""
    _a, page, total = brs.list_rows(instance_filter="5", page=1, per_page=50,
                                    sort_order="desc", ticker_preview=4)
    assert total == 3                        # the even-numbered seeds
    assert all(str(r["instance_id"]) == "5" for r in page)


def test_paging(seeded):
    _a, p1, total = brs.list_rows(instance_filter=None, page=1, per_page=3,
                                  sort_order="desc", ticker_preview=4)
    _a2, p2, _t = brs.list_rows(instance_filter=None, page=2, per_page=3,
                                sort_order="desc", ticker_preview=4)
    assert total == 7 and len(p1) == 3 and len(p2) == 3
    assert not ({r["id"] for r in p1} & {r["id"] for r in p2})


def test_the_slow_path_is_gone():
    import inspect

    import interactive_utils
    src = inspect.getsource(interactive_utils)
    assert "_LIST_TICKER_PREVIEW" in src           # the constant survives
    assert src.count("def _slim(") == 0, "the pluck+merge slow path must be deleted"


# --- Beyond the brief: the fields the brief's LIST_FIELDS dropped ----------
#
# The legacy _pluck_fields (interactive_utils.py:5223-5228, pre-port) carried
# "pnl" and "pnl_percent", and the merge below it does
# ``current["pnl"] = row.get("pnl")`` -- the list page's P&L column and the
# pnl / pnl_percent sorts both read them. Dropping them from the projection
# would blank that column, which is a Flutter-visible change.

def test_pnl_survives_the_projection(seeded):
    _a, page, _t = brs.list_rows(instance_filter=None, page=1, per_page=50,
                                 sort_order="desc", ticker_preview=4)
    row = [r for r in page if r["id"] == 800003][0]
    assert row["pnl"] == 3.0 and row["pnl_percent"] == pytest.approx(0.3)


def test_unpaged_reads_every_row_for_the_pnl_sorts(seeded):
    """sort_by=pnl ranks the WHOLE table in python, so the page window cannot
    be applied at the DB -- interactive_utils keeps an unpaged call for it."""
    active, rows, total = brs.list_rows(instance_filter=None, page=3,
                                        per_page=2, sort_order="desc",
                                        ticker_preview=4, paged=False)
    assert active == [] and total == 7
    assert {r["id"] for r in rows} == {800000 + i for i in range(1, 8)}


def test_unpaged_still_honours_the_instance_filter(seeded):
    _a, rows, total = brs.list_rows(instance_filter="main", page=1, per_page=2,
                                    sort_order="desc", ticker_preview=4,
                                    paged=False)
    assert total == 4 and len(rows) == 4
    assert all(r["instance_id"] == "main" for r in rows)


# --- The endpoint itself ---------------------------------------------------
#
# list_rows() being right does not prove action_list_backtests() still wires
# it into the active-then-page merge, the dedupe and the page-1-only pinning.
# Both halves run against the real tables now. The queue rows are seeded
# rather than mocked: BacktestInstances is an ordinary registry table.

def _cage_queue(monkeypatch, iu, queue_rows=()):
    from db import schema as dbschema
    monkeypatch.setattr(iu, "ensure_backtest_instances_table", lambda conn: None)
    dbschema.ensure_schema(tables=["BacktestInstances"])
    for row in queue_rows:
        iu.store.insert("BacktestInstances", dict(row), conflict="replace")
    return None


def test_endpoint_page_one_pins_the_active_row_and_dedupes(seeded, monkeypatch):
    import interactive_utils as iu
    _cage_queue(monkeypatch, iu)
    out = iu.action_list_backtests(None, page=1, per_page=20)
    assert out["total"] == 7 and out["total_pages"] == 1
    ids = [b["id"] for b in out["backtests"]]
    assert ids == [800007, 800006, 800005, 800004, 800003, 800002, 800001]
    row = {b["id"]: b for b in out["backtests"]}[800007]
    assert row["status"] == "running" and row["progress"] == 42.5
    assert row["stocks"] == ["T0", "T1", "T2", "T3"] and row["stocks_total"] == 7
    assert row["pnl"] == 7.0


def test_endpoint_page_two_drops_the_pinned_active_rows(seeded, monkeypatch):
    import interactive_utils as iu
    _cage_queue(monkeypatch, iu)
    p1 = iu.action_list_backtests(None, page=1, per_page=3)
    p2 = iu.action_list_backtests(None, page=2, per_page=3)
    assert p1["total"] == 7 and p2["total"] == 7
    assert not ({b["id"] for b in p1["backtests"]}
                & {b["id"] for b in p2["backtests"]})
    assert all(b["status"] != "running" for b in p2["backtests"])


def test_endpoint_pnl_sort_still_ranks_the_whole_table(seeded, monkeypatch):
    """The pnl sort has no DB page window, so it must still see every row and
    rank them in python -- page 2 is the tail of that ranking, not of the
    timestamp order."""
    import interactive_utils as iu
    _cage_queue(monkeypatch, iu)
    out = iu.action_list_backtests(None, page=1, per_page=3, sort_by="pnl")
    assert out["total"] == 7
    ids = [b["id"] for b in out["backtests"]]
    assert ids == [800007, 800006, 800005]      # active first, then pnl desc
    tail = iu.action_list_backtests(None, page=3, per_page=3, sort_by="pnl")
    assert [b["id"] for b in tail["backtests"]] == [800001]


def test_endpoint_instance_filter_reaches_the_sql(seeded, monkeypatch):
    import interactive_utils as iu
    _cage_queue(monkeypatch, iu)
    out = iu.action_list_backtests(None, 5, page=1, per_page=20)
    assert out["total"] == 3
    assert {b["id"] for b in out["backtests"]} == {800002, 800004, 800006}
