"""The round-trip gate.

A document -> split into metadata + steps + progress -> assemble() ->
canonical() byte-identical to the input, at every lifecycle stage.

BACKTEST_SPLIT_FIXTURE=<path> runs the same assertions against an
operator-supplied real export instead of the checked-in fixtures.
"""
import gzip
import json
import os

import pytest

import backtest_result_store as brs
from db import schema
from db.json import canonical

from .conftest import requires_pg
from .test_fixtures_shape import STAGES, load_fixture


def _fixtures():
    override = os.environ.get("BACKTEST_SPLIT_FIXTURE")
    if override:
        with gzip.open(override, "rt", encoding="utf-8") as fh:
            return {"operator": json.load(fh)}
    return {stage: load_fixture(stage) for stage in STAGES}


_STAGE_NAMES = sorted(_fixtures())


@pytest.fixture
def split_schema(pg_schema):
    schema.ensure_schema(tables=["BacktestResults", "BacktestSteps",
                                 "BacktestProgress"])
    return pg_schema


# ---- pure split/assemble mechanics --------------------------------------

def test_step_keys_cover_the_six_arrays():
    keys = {key for _, key, _ in brs._STEP_KEYS}
    assert keys == {"backtest_decisions", "backtest_refusals", "backtest_trades",
                    "portfolio_value_history", "logs", "backtest_prices"}


def test_always_present_is_the_four_arrays_the_stub_creates_empty():
    assert brs._ALWAYS_PRESENT == {"portfolio_value_history", "backtest_trades",
                                   "backtest_prices", "logs"}


def test_split_doc_removes_every_step_array_from_the_metadata():
    doc = load_fixture("running")
    meta, steps, progress = brs.split_doc(doc)
    for _, key, _ in brs._STEP_KEYS:
        assert key not in meta
    assert steps["decision"] == doc["backtest_decisions"]
    assert progress["status"] == doc["status"]
    assert progress["progress"] == doc["progress"]


def test_split_doc_keeps_every_other_key_verbatim():
    doc = load_fixture("finished")
    meta, _, _ = brs.split_doc(doc)
    step_keys = {key for _, key, _ in brs._STEP_KEYS}
    hot_keys = {"status", "progress", "time_elapsed_seconds", "_last_active"}
    for key, value in doc.items():
        if key in step_keys:
            continue
        assert meta[key] == value, key
    assert hot_keys <= set(doc)          # they stay in doc too, unchanged


def test_split_doc_without_an_id_is_an_error():
    from db.errors import StoreError
    with pytest.raises(StoreError):
        brs.split_doc({"status": "running"})


# ---- the gate -----------------------------------------------------------

@requires_pg
@pytest.mark.parametrize("stage", _STAGE_NAMES)
def test_round_trip_is_byte_identical(split_schema, stage):
    doc = _fixtures()[stage]
    brs.write_split(doc, final=True)
    got = brs.assemble(doc["id"])
    assert canonical(got) == canonical(doc)


@requires_pg
def test_a_live_run_round_trips_every_array_it_has_written(split_schema):
    """The mid-run path: incremental, non-final rows, caps applied on read.

    An array that is PRESENT but EMPTY and not one of the four always-present
    keys (the running sample's backtest_refusals) is the one thing a non-final
    write cannot reproduce -- there is no row to prove it was written. That is
    why finality has a seq=0 marker and why the gate above writes final=True.
    """
    doc = load_fixture("running")
    brs.write_split(doc, final=False)
    got = brs.assemble(doc["id"])
    expected = {k: v for k, v in doc.items() if not (
        k in brs.KIND_FOR_KEY and v == [] and k not in brs._ALWAYS_PRESENT)}
    assert canonical(got) == canonical(expected)


@requires_pg
@pytest.mark.parametrize("stage", _STAGE_NAMES)
def test_assembled_keys_are_lexicographic(split_schema, stage):
    doc = _fixtures()[stage]
    brs.write_split(doc, final=True)
    got = brs.assemble(doc["id"])
    assert list(got) == sorted(got)


@requires_pg
def test_progress_scalar_types_survive_the_overlay(split_schema):
    """Live data: stopped runs carry progress as an int, running as a float;
    finished runs carry time_elapsed_seconds as a float, running as an int.
    A double precision column would turn 0 into 0.0 and change the bytes."""
    stopped, finished = load_fixture("stopped"), load_fixture("finished")
    for doc in (stopped, finished):
        brs.write_split(doc, final=True)
        got = brs.assemble(doc["id"])
        assert type(got["progress"]) is type(doc["progress"])
        assert type(got["time_elapsed_seconds"]) is type(
            doc["time_elapsed_seconds"])


@requires_pg
def test_the_four_always_present_arrays_exist_from_the_first_read(split_schema):
    stub = load_fixture("stub")
    brs.write_split(stub, final=False)
    got = brs.assemble(stub["id"])
    for key in brs._ALWAYS_PRESENT:
        assert got[key] == []


@requires_pg
def test_decisions_and_refusals_are_absent_until_written(split_schema):
    stub = load_fixture("stub")
    brs.write_split(stub, final=False)
    got = brs.assemble(stub["id"])
    assert "backtest_decisions" not in got
    assert "backtest_refusals" not in got


@requires_pg
def test_a_finalized_empty_array_is_not_the_same_as_never_written(split_schema):
    stopped = load_fixture("stopped")
    brs.write_split(stopped, final=True)
    got = brs.assemble(stopped["id"])
    assert got["backtest_decisions"] == []      # finalized empty, so present
    assert "backtest_refusals" not in got       # never written, so absent


@requires_pg
def test_live_reads_apply_the_legacy_caps(split_schema):
    doc = dict(load_fixture("running"))
    doc["id"] = 999001
    doc["backtest_trades"] = [{"n": i} for i in range(1500)]
    doc["logs"] = ["line %d" % i for i in range(900)]
    doc["portfolio_value_history"] = [{"t": i, "v": float(i)}
                                      for i in range(5000)]
    brs.write_split(doc, final=False)
    got = brs.assemble(999001)
    assert got["backtest_trades"] == doc["backtest_trades"][-1000:]
    assert got["logs"] == doc["logs"][-500:]
    assert len(got["portfolio_value_history"]) <= 3000
    assert got["portfolio_value_history"][0] == doc["portfolio_value_history"][0]
    assert got["portfolio_value_history"][-1] == doc["portfolio_value_history"][-1]


@requires_pg
def test_final_reads_are_uncapped(split_schema):
    doc = dict(load_fixture("finished"))
    doc["id"] = 999002
    doc["backtest_trades"] = [{"n": i} for i in range(1500)]
    brs.write_split(doc, final=True)
    assert brs.assemble(999002)["backtest_trades"] == doc["backtest_trades"]


@requires_pg
def test_seq_is_the_only_ordering(split_schema):
    doc = dict(load_fixture("running"))
    doc["id"] = 999003
    doc["backtest_decisions"] = [{"n": i} for i in range(50)]
    brs.write_split(doc, final=False)
    assert brs.assemble(999003)["backtest_decisions"] == doc["backtest_decisions"]


@requires_pg
def test_the_progress_overlay_beats_a_stale_doc(split_schema):
    doc = dict(load_fixture("running"))
    doc["id"] = 999004
    brs.write_split(doc, final=False)
    brs.write_progress(999004, {"status": "stopped", "progress": 100,
                                "time_elapsed_seconds": 5,
                                "_last_active": "2026-08-22T04:00:00+00:00"})
    got = brs.assemble(999004)
    assert got["status"] == "stopped" and got["progress"] == 100
    assert got["time_elapsed_seconds"] == 5
    assert got["_last_active"] == "2026-08-22T04:00:00+00:00"


@requires_pg
def test_watermarks_report_max_seq_per_kind(split_schema):
    brs.append_steps(999005, "decision", [{"n": 1}, {"n": 2}], start_seq=0)
    brs.append_steps(999005, "log", [{"m": "x"}], start_seq=0)
    assert brs.watermarks(999005) == {"decision": 2, "log": 1}


@requires_pg
def test_append_steps_from_a_watermark_never_duplicates(split_schema):
    entries = [{"n": i} for i in range(10)]
    brs.append_steps(999006, "decision", entries[:4], start_seq=0)
    marks = brs.watermarks(999006)
    brs.append_steps(999006, "decision", entries[marks["decision"]:],
                     start_seq=marks["decision"])
    rows = brs.assemble_field(999006, "backtest_decisions")
    assert rows == entries


@requires_pg
def test_replaying_an_already_written_range_is_a_no_op(split_schema):
    entries = [{"n": i} for i in range(6)]
    brs.append_steps(999007, "decision", entries, start_seq=0)
    brs.append_steps(999007, "decision", entries, start_seq=0)   # crash replay
    assert brs.assemble_field(999007, "backtest_decisions") == entries


@requires_pg
def test_unknown_step_kind_is_an_error(split_schema):
    from db.errors import StoreError
    with pytest.raises(StoreError):
        brs.append_steps(999008, "nope", [{"n": 1}], start_seq=0)


@requires_pg
def test_assemble_of_a_missing_backtest_is_none(split_schema):
    assert brs.assemble(424242) is None


@requires_pg
def test_assemble_field_reads_one_array_without_the_whole_document(split_schema):
    doc = load_fixture("running")
    brs.write_split(doc, final=False)
    got = brs.assemble_field(doc["id"], "portfolio_value_history")
    assert got == doc["portfolio_value_history"]


@requires_pg
def test_assemble_field_of_a_non_step_key_falls_back_to_the_document(split_schema):
    doc = load_fixture("running")
    brs.write_split(doc, final=False)
    assert brs.assemble_field(doc["id"], "strategy_id") == doc["strategy_id"]
    assert brs.assemble_field(424243, "strategy_id") is None


@requires_pg
def test_assemble_field_of_an_unwritten_array(split_schema):
    stub = load_fixture("stub")
    brs.write_split(stub, final=False)
    assert brs.assemble_field(stub["id"], "logs") == []
    assert brs.assemble_field(stub["id"], "backtest_refusals") is None


@requires_pg
def test_delete_backtest_removes_all_three_tables(split_schema):
    doc = load_fixture("running")
    brs.write_split(doc, final=True)
    assert brs.delete_backtest(doc["id"]) is True
    assert brs.assemble(doc["id"]) is None
    assert brs.read_progress(doc["id"]) is None
    from db import store
    assert store.sql('SELECT count(*) AS n FROM "BacktestSteps" '
                     "WHERE backtest_id = %s",
                     (str(doc["id"]),))[0]["n"] == 0
    assert brs.delete_backtest(doc["id"]) is False
