"""The migration script, against a real Postgres and a synthetic dump.

The fixture is SYNTHETIC by construction: a real 3.1 MB production backtest
document is never committed. Its shapes are taken from the live database
(read-only, 2026-08-22) -- string Instances ids, a numeric ``instance_id`` on
BacktestResults, a stopped run with no ``backtest_refusals`` key at all, raw
TIME pseudotypes nested inside payloads. ``MIGRATION_FIXTURE=<path>`` points
the same assertions at a real export.

Every test needs a real database: COPY, temp tables, partition routing and
COLLATE "C" cannot be proven against a dict.
"""
import gzip
import importlib.util
import json
import os
import pathlib
import sys

import pytest

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "rethink_dump_small.json.gz"
_SCRIPT = (pathlib.Path(__file__).resolve().parents[2]
           / "scripts" / "migrate_rethinkdb_to_postgres.py")


def _load(name, path):
    """Load a top-level ``scripts/`` module by path.

    ``from scripts.x import y`` does NOT reach the repo-root ``scripts/``
    directory: ``backend/`` sits at sys.path[0] (conftest puts it there to
    mirror production) and ``backend/scripts/`` is a real package, so the name
    ``scripts`` always resolves there. The operational scripts this plan
    creates live at the repo root, where the runbook and compose reference
    them, so the tests load them by path.
    """
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


mig = _load("migrate_rethinkdb_to_postgres", _SCRIPT)

requires_pg = pytest.mark.skipif(
    not os.environ.get("PG_TEST_DSN"),
    reason="PG_TEST_DSN not set (run: ./scripts/dev_pg.sh up)")

pytestmark = requires_pg


@pytest.fixture
def dump():
    path = pathlib.Path(os.environ.get("MIGRATION_FIXTURE") or FIXTURE)
    with gzip.open(str(path), "rt") as fh:
        return json.load(fh)


@pytest.fixture
def exported(dump):
    """The dump as ``export_table`` would hand it over: TIME pseudotypes
    already converted to ISO-8601. Nothing else is touched."""
    iso = mig.iso
    return {table: [iso(row) for row in rows] for table, rows in dump.items()}


def _feed(rows, chunk=1000):
    """A stand-in for export_table that ignores since_id, the way a re-run
    against an unchanged source does."""
    def _export(table, *, since_id=None, batch=chunk):
        for start in range(0, len(rows), chunk):
            yield rows[start:start + chunk]
    return _export


# --------------------------------------------------------------------------
# conversion
# --------------------------------------------------------------------------

def test_iso_converts_raw_time_pseudotype():
    iso = mig.iso

    raw = {"$reql_type$": "TIME", "epoch_time": 1755835020.123456,
           "timezone": "+00:00"}
    assert iso(raw) == "2025-08-22T03:57:00.123456+00:00"
    assert iso({"a": [raw]}) == {"a": ["2025-08-22T03:57:00.123456+00:00"]}
    assert iso("plain") == "plain"


def test_iso_honours_a_negative_offset():
    iso = mig.iso

    raw = {"$reql_type$": "TIME", "epoch_time": 1755835020.123456,
           "timezone": "-05:00"}
    assert iso(raw) == "2025-08-21T22:57:00.123456-05:00"


def test_iso_leaves_other_pseudotypes_verbatim():
    iso = mig.iso

    binary = {"$reql_type$": "BINARY", "data": "aGk="}
    assert iso(binary) == binary


def test_iso_reaches_time_nested_in_a_payload(exported):
    row = exported["GraphNexusTradeContexts"][0]
    nested = row["payload"]["nested"]
    assert nested[0] == "2025-08-22T03:57:00+00:00"
    assert nested[1]["deep"].startswith("2025-08-23T")


# --------------------------------------------------------------------------
# the BacktestResults split
# --------------------------------------------------------------------------

def test_backtest_row_splits_into_metadata_steps_progress(exported):
    split_backtest_row = mig.split_backtest_row
    from backtest_result_store import _STEP_KEYS

    row = exported["BacktestResults"][0]
    meta, steps, progress = split_backtest_row(row)

    step_keys = {key for _kind, key, _cap in _STEP_KEYS}
    assert step_keys.isdisjoint(meta)                  # no step array left in doc
    assert meta["strategy_schema"] == row["strategy_schema"]   # metadata verbatim
    assert progress["payload"]["status"] == row["status"]
    assert progress["payload"]["progress"] == row["progress"]

    # backtest_result_store numbers step rows from seq 1, preceded by a seq=0
    # marker carrying a JSON null -- that is how "finalised with zero entries"
    # stays distinguishable from "never finalised". This mirrors it exactly.
    decisions = [s for s in steps if s[0] == "decision"]
    assert [s[1] for s in decisions] == list(range(len(row["backtest_decisions"]) + 1))
    assert decisions[0][2] is None                                  # the marker
    assert decisions[1][2] == row["backtest_decisions"][0]


def test_a_key_the_source_never_had_stays_absent(exported):
    split_backtest_row = mig.split_backtest_row

    stopped = exported["BacktestResults"][1]
    assert "backtest_refusals" not in stopped
    _meta, steps, _progress = split_backtest_row(stopped)
    assert not [s for s in steps if s[0] == "refusal"]
    # ...while a key that WAS there but empty still gets its marker row.
    assert [s for s in steps if s[0] == "decision"] == [("decision", 0, None)]


def test_split_matches_the_production_writer_row_for_row(store, exported):
    """The migration's COPY path and backtest_result_store.write_split must
    produce identical tables. Anything else is a second, divergent writer."""
    _write_backtest_rows = mig._write_backtest_rows
    import backtest_result_store as brs

    row = exported["BacktestResults"][0]
    _write_backtest_rows([row])
    by_copy = store.sql('SELECT backtest_id, kind, seq, final, doc '
                        'FROM "BacktestSteps" ORDER BY kind, final, seq')
    progress_by_copy = store.sql('SELECT id, payload, last_active '
                                 'FROM "BacktestProgress" ORDER BY id')
    meta_by_copy = store.sql('SELECT id, doc FROM "BacktestResults" ORDER BY id')

    brs.delete_backtest(row["id"])
    brs.write_split(row, final=True)
    by_writer = store.sql('SELECT backtest_id, kind, seq, final, doc '
                          'FROM "BacktestSteps" ORDER BY kind, final, seq')
    progress_by_writer = store.sql('SELECT id, payload, last_active '
                                   'FROM "BacktestProgress" ORDER BY id')
    meta_by_writer = store.sql('SELECT id, doc FROM "BacktestResults" ORDER BY id')

    assert by_copy == by_writer
    assert progress_by_copy == progress_by_writer
    assert meta_by_copy == meta_by_writer


def test_migrated_backtest_assembles_back_byte_identical(store, exported):
    _write_backtest_rows = mig._write_backtest_rows
    from backtest_result_store import assemble
    from db.json import canonical

    for row in exported["BacktestResults"]:
        _write_backtest_rows([row])
        assert canonical(assemble(row["id"])) == canonical(dict(sorted(row.items())))


# --------------------------------------------------------------------------
# PriceHistory
# --------------------------------------------------------------------------

def test_price_history_promotes_ticker_and_ts_into_columns(store, exported):
    copy_batch = mig.copy_batch
    from db.json import canonical

    rows = exported["PriceHistory"]
    assert copy_batch("PriceHistory", rows) == len(rows)
    stored = store.sql('SELECT ticker, ts, id, doc FROM "PriceHistory" '
                       'ORDER BY ticker COLLATE "C", ts, id COLLATE "C"')
    assert len(stored) == len(rows)
    assert {r["ticker"] for r in stored} == {"AAPL", "MSFT"}
    by_id = {r["id"]: r["doc"] for r in stored}
    for source in rows:
        assert canonical(by_id[source["id"]]) == canonical(source)
    # the rows landed in real monthly partitions, not a default catch-all
    parts = store.sql("SELECT count(*) AS n FROM pg_inherits i JOIN pg_class c "
                      "ON c.oid = i.inhrelid WHERE i.inhparent = "
                      "'\"PriceHistory\"'::regclass")
    assert parts[0]["n"] >= 2


def test_price_history_row_without_a_timestamp_is_rejected(store):
    copy_batch = mig.copy_batch

    with pytest.raises(ValueError):
        copy_batch("PriceHistory", [{"id": "x", "ticker": "AAPL", "price": 1.0}])
    with pytest.raises(ValueError):
        copy_batch("PriceHistory", [{"id": "x", "ticker": "AAPL",
                                     "timestamp": "not-a-date"}])


# --------------------------------------------------------------------------
# COPY and resume
# --------------------------------------------------------------------------

def test_nan_is_rejected_at_the_client_not_the_server(store):
    copy_batch = mig.copy_batch

    with pytest.raises(ValueError):
        copy_batch("Instances", [{"id": "x", "v": float("nan")}])
    assert store.count("Instances") == 0


def test_a_bad_row_leaves_the_whole_batch_unwritten(store, exported):
    """Encoding happens before the first write, so row 4's NaN cannot leave
    rows 1-3 copied."""
    copy_batch = mig.copy_batch

    rows = list(exported["Instances"][:3]) + [{"id": "bad", "v": float("inf")}]
    with pytest.raises(ValueError):
        copy_batch("Instances", rows)
    assert store.count("Instances") == 0


def test_copy_batch_round_trips_documents_unchanged(store, exported):
    copy_batch = mig.copy_batch
    from db.json import canonical

    rows = exported["Instances"]
    assert copy_batch("Instances", rows) == len(rows)
    stored = store.sql('SELECT id, doc FROM "Instances" ORDER BY id COLLATE "C"')
    assert [r["id"] for r in stored] == sorted(r["id"] for r in rows)
    by_id = {r["id"]: r["doc"] for r in stored}
    for source in rows:
        assert canonical(by_id[source["id"]]) == canonical(source)


def test_resume_from_last_id_does_not_duplicate(store, exported, monkeypatch):
    """Kill mid-table, rerun, end with the same row count."""
    rows = exported["Instances"]
    monkeypatch.setattr(mig, "export_table", _feed(rows[:2]))
    mig.migrate_table("Instances", batch=2, dry_run=False)
    assert store.count("Instances") == 2
    assert mig.read_state("Instances")["last_id"] == rows[1]["id"]

    monkeypatch.setattr(mig, "export_table", _feed(rows))
    mig.migrate_table("Instances", batch=10, dry_run=False)
    assert store.count("Instances") == len(rows)     # not 2 + len(rows)
    assert mig.read_state("Instances")["finished_at"]


def test_dry_run_writes_nothing(store, exported, monkeypatch):
    rows = exported["Instances"]
    monkeypatch.setattr(mig, "export_table", _feed(rows))
    report = mig.migrate_table("Instances", batch=10, dry_run=True)
    assert report["rows"] == len(rows)
    assert store.count("Instances") == 0
    assert mig.read_state("Instances") == {}


# --------------------------------------------------------------------------
# verify
# --------------------------------------------------------------------------

def test_verify_writes_whole_documents_on_mismatch(store, exported, tmp_path,
                                                   monkeypatch):
    monkeypatch.setattr(mig, "MISMATCH_DIR", str(tmp_path))
    source = dict(exported["Instances"][0])
    mig.copy_batch("Instances", [{**source, "name": "TAMPERED"}])
    monkeypatch.setattr(mig, "export_table", _feed([source]))
    report = mig.verify_table("Instances", sample=1.0)
    assert report["mismatches"] == 1
    written = list(tmp_path.rglob("*.json"))
    assert len(written) == 1
    body = json.loads(written[0].read_text())
    assert body["rethink"]["name"] == source["name"]     # WHOLE doc, both sides
    assert body["postgres"]["name"] == "TAMPERED"


def test_verify_hash_is_invariant_to_key_order(store, exported, monkeypatch,
                                               tmp_path):
    monkeypatch.setattr(mig, "MISMATCH_DIR", str(tmp_path))
    source = exported["Instances"][1]
    mig.copy_batch("Instances", [dict(reversed(list(source.items())))])
    monkeypatch.setattr(mig, "export_table", _feed([source]))
    assert mig.verify_table("Instances", sample=1.0)["mismatches"] == 0


def test_verify_counts_a_missing_row(store, exported, monkeypatch, tmp_path):
    monkeypatch.setattr(mig, "MISMATCH_DIR", str(tmp_path))
    rows = exported["Instances"]
    mig.copy_batch("Instances", rows[:3])
    monkeypatch.setattr(mig, "export_table", _feed(rows))
    report = mig.verify_table("Instances", sample=1.0)
    assert report["rethink_rows"] == 5
    assert report["pg_rows"] == 3
    assert report["mismatches"] == 2


def test_verify_assembles_backtest_results_before_hashing(store, exported,
                                                          monkeypatch, tmp_path):
    monkeypatch.setattr(mig, "MISMATCH_DIR", str(tmp_path))
    rows = exported["BacktestResults"]
    for row in rows:
        mig._write_backtest_rows([row])
    monkeypatch.setattr(mig, "export_table", _feed(rows))
    report = mig.verify_table("BacktestResults", sample=1.0)
    assert report["mismatches"] == 0
    assert report["rethink_rows"] == report["pg_rows"] == len(rows)


def test_verify_ordering_is_bytewise_on_scope_suffixed_ids(store):
    ids = ["alpaca-main|a", "alpaca-mainZ", "alpaca-main9", "alpaca-mainb"]
    for rid in ids:
        store.insert("GraphNexusTradeContexts", {"id": rid})
    assert mig.verify_ordering("GraphNexusTradeContexts", first_n=4) == sorted(ids)


def test_verify_ordering_parity_names_the_position_that_diverged(
        store, monkeypatch):
    ids = ["alpaca-main9", "alpaca-mainZ", "alpaca-mainb"]
    for rid in ids:
        store.insert("GraphNexusTradeContexts", {"id": rid})
    monkeypatch.setattr(mig, "export_table",
                        _feed([{"id": rid} for rid in ids]))
    assert mig.verify_ordering_parity(
        "GraphNexusTradeContexts", first_n=10)["diverged_at"] is None

    monkeypatch.setattr(mig, "export_table",
                        _feed([{"id": rid} for rid in
                               ["alpaca-mainZ", "alpaca-main9", "alpaca-mainb"]]))
    assert mig.verify_ordering_parity(
        "GraphNexusTradeContexts", first_n=10)["diverged_at"] == 0


def test_verify_id_types_reports_a_registry_disagreement(store, exported,
                                                         monkeypatch):
    """Instances is declared id_type="int" but every live key is a string.
    store.coerce_id rejects those, so the rows would be unreadable after the
    cutover -- the copy is faithful and this is what says so out loud."""
    monkeypatch.setattr(mig, "export_table", _feed(exported["Instances"]))
    offenders = mig.verify_id_types("Instances")
    assert "alpaca-main" in offenders
    monkeypatch.setattr(mig, "export_table", _feed(exported["BacktestResults"]))
    assert mig.verify_id_types("BacktestResults") == []


def test_verify_exits_nonzero_on_any_mismatch(store, exported, monkeypatch,
                                              tmp_path):
    monkeypatch.setattr(mig, "MISMATCH_DIR", str(tmp_path))
    source = exported["Instances"][2]
    mig.copy_batch("Instances", [{**source, "name": "WRONG"}])
    monkeypatch.setattr(mig, "export_table", _feed([source]))
    assert mig.main(["--verify", "--tables", "Instances",
                     "--verify-sample", "1.0"]) != 0


def test_verify_exits_zero_when_the_copy_is_faithful(store, exported,
                                                     monkeypatch, tmp_path):
    """The counterpart that proves the exit code tracks the data and not the
    unreachable RethinkDB in a test environment."""
    monkeypatch.setattr(mig, "MISMATCH_DIR", str(tmp_path))
    rows = exported["Instances"]
    mig.copy_batch("Instances", rows)
    monkeypatch.setattr(mig, "export_table", _feed(rows))
    assert mig.main(["--verify", "--tables", "Instances", "--verify-sample",
                     "1.0", "--no-parity-checks"]) == 0
    assert not list(tmp_path.rglob("*.json"))


def test_ordering_parity_is_skipped_for_an_int_keyed_table(store, exported,
                                                           monkeypatch):
    """Strategies ids are NUMBERS in RethinkDB, so its id order legitimately
    differs from the text order Postgres gives them. Reporting that every run
    would train the operator to ignore the check that matters."""
    monkeypatch.setattr(mig, "export_table", _feed(exported["Instances"]))
    parity = mig.verify_ordering_parity("Strategies", first_n=10)
    assert parity["diverged_at"] is None
    assert "int-keyed" in parity["skipped"]
