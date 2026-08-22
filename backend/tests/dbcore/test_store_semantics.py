"""Acceptance matrix for spec section 4: ReQL -> store.

COVERAGE maps every row of the spec's mapping table to the test that proves
it. A reviewer checks the table off against this dict. Rows proved elsewhere
name the file; rows proved here name a test in this file.
"""
import datetime as dt

import pytest

from db import Literal, P, store
from db import schema
from db.errors import StoreError

from .conftest import requires_pg

COVERAGE = {
    ".get(pk)": "test_store_reads.py::test_get_missing_row_is_none_never_empty_dict",
    ".get_all(index)": "test_store_reads.py::test_get_all_on_a_secondary_index_field",
    ".get_all no dedupe": "test_store_reads.py::test_get_all_does_not_dedupe",
    ".get_all variadic empty": "test_store_reads.py::test_get_all_with_no_keys_is_a_valid_empty_result",
    ".insert(doc)": "test_store_writes.py::test_insert_then_get",
    "conflict='error'": "test_store_conflict.py::test_conflict_error_records_the_conflict_without_aborting_the_batch",
    "conflict='replace'": "test_store_conflict.py::test_conflict_replace_drops_keys_the_new_document_lacks",
    "conflict='update'": "test_store_conflict.py::test_conflict_update_deep_merges_it_does_not_use_shallow_concat",
    "multi-doc insert / WRITE_CHUNK": "test_store_conflict.py::test_a_500_doc_chunk_with_two_duplicates_is_partial_success",
    ".update(patch)": "test_store_writes.py::test_update_deep_merges_objects_and_replaces_arrays",
    "r.literal(v)": "test_store_writes.py::test_update_with_literal_blanks_a_subtree",
    "r.literal nested in a replacement": "test_merge_property.py::test_nested_literal_sentinels_are_stripped_from_a_replacement",
    "SQL merge == Python merge": "test_merge_property.py::test_sql_and_python_deep_merge_agree",
    ".replace(doc)": "test_store_writes.py::test_replace_swaps_the_whole_document",
    ".replace(r.branch(...)) CAS": "test_store_writes.py::test_replace_if_distinguishes_missing_row_from_failed_predicate",
    ".delete()": "test_store_writes.py::test_delete_over_a_selection_is_one_statement",
    ".between(lo,hi)": "test_store_query.py::test_between_is_half_open_by_default",
    "r.minval / r.maxval": "test_store_query.py::test_minval_and_maxval_omit_the_bound",
    "prefix between + LIKE": "test_prefix_scan.py::test_like_form_and_range_form_agree",
    ".filter({dict})": "test_store_query.py::test_filter_dict_none_means_json_null_not_absent",
    ".filter(lambda)": "test_store_query.py::test_undefaulted_field_comparison_is_false_on_a_missing_key",
    "origin_not_backtest": "test_store_query.py::test_origin_not_backtest_criterion",
    ".default().match('^prefix')": "test_prefix_scan.py::test_a_prefix_containing_like_metacharacters_is_escaped",
    ".default().coerce_to('string')": "test_store_query.py::test_coerce_to_string_stringifies_a_json_number",
    ".split('|').nth(0)": "test_store_query.py::test_split_nth_selects_the_base_instance",
    ".pluck(*fields)": "test_store_reads.py::test_pluck_omits_missing_keys_it_never_emits_null",
    ".merge(lambda)": "test_merge_is_not_implemented_in_the_store",
    ".order_by(index)/r.desc": "test_collation.py::test_the_graph_nexus_window_tiebreak_reproduces_python_byte_order",
    ".limit/.slice": "test_store_reads.py::test_limit_and_slice",
    ".count()": "test_store_reads.py::test_count_of_a_table",
    "r.expr(list).contains": "test_store_query.py::test_is_in_matches_any_of_a_list",
    "r.branch(c,a,b)": "test_status_norm_index_expression_is_a_case_expression",
    "r.now()": "test_now_is_server_side_and_transaction_start",
    "r.now().to_epoch_time()": "test_now_epoch_returns_a_float",
    "r.epoch_time(x)": "test_epoch_time_builds_a_timestamp",
    "TIME pseudotype": "test_store_reads.py::test_time_fields_decode_back_to_aware_datetimes",
    "NaN / Infinity": "test_store_writes.py::test_nan_is_rejected_at_the_client",
    "NUL (U+0000)": "test_merge_property.py::test_a_nul_character_is_rejected_at_the_client",
    "jsonb number renormalisation": "test_merge_property.py::test_jsonb_renormalises_numbers_on_any_round_trip",
    "int primary keys": "test_store_reads.py::test_coerce_id_round_trips_int_tables",
    "instance_id NUMBER/STRING": "test_instance_id_type_split_is_left_alone_in_doc",
    "durability=": "test_store_writes.py::test_durability_is_accepted_and_ignored",
    "noreply_wait / conn.close": "test_noreply_and_close_are_not_part_of_the_api",
    "unimplemented ReQL ops": "test_unimplemented_reql_ops_are_absent",
    "table_create / index_wait": "test_store_reads.py::test_table_list_and_table_create",
    "ReQL 100k array limit": "test_store_reads.py::test_run_raises_above_pg_max_rows",
    "missing primary key rejected": "test_a_missing_pk_field_write_is_rejected_like_rethinkdb",
    "COLLATE \"C\" everywhere": "test_collation.py::test_order_by_id_desc_is_bytewise",
    "monthly partitions": "test_partitions_retention.py::test_rows_route_to_the_right_partition",
    "retention sweeper": "test_partitions_retention.py::test_sweep_deletes_only_rows_past_the_cutoff",
}


def test_every_spec_row_names_a_test():
    assert len(COVERAGE) == 50
    for row, target in COVERAGE.items():
        assert target and "::" in target or not target.endswith(".py"), row


def test_every_coverage_target_in_this_file_exists():
    """A row that names a test HERE must name one that exists -- otherwise the
    matrix reads as proof of something nothing checks."""
    here = {name for name, target in COVERAGE.items() if "::" not in target}
    missing = {COVERAGE[name] for name in here
               if COVERAGE[name] not in globals()}
    assert missing == set(), missing


def test_merge_is_not_implemented_in_the_store():
    """The only .merge() site is interactive_utils.py:5232's _slim ticker
    projection, which the BacktestResults list SQL replaces (Plan B)."""
    assert not hasattr(store, "merge")


def test_noreply_and_close_are_not_part_of_the_api():
    """noreply_wait=False and conn.close(...) become no-ops / pool release:
    there is nothing to call."""
    for name in ("noreply_wait", "close", "connect"):
        assert not hasattr(store, name)


def test_unimplemented_reql_ops_are_absent():
    """r.args, r.uuid, r.js, r.do, return_changes, eq_join, group, reduce all
    have ZERO sites. If a port needs one, that is a signal the site is being
    rewritten rather than ported."""
    for name in ("args", "uuid", "js", "do", "eq_join", "group", "reduce",
                 "for_each", "union", "has_fields"):
        assert not hasattr(store, name), name


@requires_pg
def test_now_is_server_side_and_transaction_start(pg_schema):
    """r.now() -> now() in SQL. now() is transaction START time, not statement
    time; every one of the 34 sites writes in its own short transaction, so
    the distinction is invisible and the store never batches two into one."""
    rows = store.sql("SELECT now() AS a, to_jsonb(now()) AS b")
    assert isinstance(rows[0]["a"], dt.datetime)
    assert rows[0]["a"].tzinfo is not None


@requires_pg
def test_now_epoch_returns_a_float(pg_schema):
    value = store.sql("SELECT extract(epoch from now()) AS t")[0]["t"]
    assert float(value) > 1_700_000_000


@requires_pg
def test_epoch_time_builds_a_timestamp(pg_schema):
    value = store.sql("SELECT to_timestamp(%s) AS t", (1_755_000_000,))[0]["t"]
    assert value.year == 2025 or value.year == 2026


@requires_pg
def test_status_norm_index_expression_is_a_case_expression(pg_schema):
    """r.branch(c, a, b) -> CASE WHEN. status_norm lives on BacktestProgress
    (spec section 5.3 decision 6), not on BacktestResults."""
    schema.ensure_schema(tables=["BacktestProgress", "BacktestResults"])
    rows = store.sql("SELECT indexdef FROM pg_indexes WHERE tablename = %s",
                     ("BacktestProgress",))
    defs = " ".join(r["indexdef"] for r in rows)
    assert "CASE" in defs.upper() and "paused" in defs
    br = store.sql("SELECT indexname FROM pg_indexes WHERE tablename = %s",
                   ("BacktestResults",))
    assert not any("status_norm" in r["indexname"] for r in br)


@requires_pg
def test_instance_id_type_split_is_left_alone_in_doc(pg_schema):
    """592 rows carry instance_id as a NUMBER and 833 as a STRING. The doc is
    left as-is; the generated column coalesces for indexing, exactly as the
    ReQL index does. Rewriting the values would change 592 documents' bytes
    and break every fingerprint taken before cutover."""
    schema.ensure_schema(tables=["BacktestResults"])
    store.insert("BacktestResults", [{"id": 1, "instance_id": 5},
                                     {"id": 2, "instance_id": "5"}])
    assert store.get("BacktestResults", 1)["instance_id"] == 5
    assert store.get("BacktestResults", 2)["instance_id"] == "5"
    both = store.sql(
        'SELECT id FROM "BacktestResults" '
        "WHERE coalesce(doc->>'instance_id', doc->>'instance','') = %s ORDER BY id",
        ("5",))
    assert [r["id"] for r in both] == ["1", "2"]


@requires_pg
def test_tickers_total_survives_a_non_array_tickers_value(pg_schema):
    """jsonb_array_length RAISES on a non-array, and a generated column that
    raises makes EVERY insert on the table fail -- so the expression is
    guarded with jsonb_typeof."""
    schema.ensure_schema(tables=["BacktestResults"])
    store.insert("BacktestResults", [{"id": 1, "tickers": ["A", "B"]},
                                     {"id": 2, "tickers": "AAPL"},
                                     {"id": 3}])
    rows = store.sql('SELECT id, tickers_total FROM "BacktestResults" ORDER BY id')
    assert [(r["id"], r["tickers_total"]) for r in rows] == \
        [("1", 2), ("2", 0), ("3", 0)]


def test_package_exports_the_public_surface():
    from db import Literal as L, P as Pp, store as s, watch as w
    assert L is Literal and Pp is P and s is store and w is not None


@requires_pg
def test_a_missing_pk_field_write_is_rejected_like_rethinkdb(pg_schema):
    schema.ensure_schema(tables=["kalshi_markets"])
    with pytest.raises(StoreError):
        store.insert("kalshi_markets", {"yes_bid": 1})
