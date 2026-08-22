"""Tests for the GraphNexusTradeContexts index bootstrap and the
indexed resume-date query.

Backtest #953929 stalled ~4 minutes on the resume-date query at lookback
prep time because the table had no secondary index — every call was a
full table scan over every backtest's rows across every operator. The
``instance_id`` secondary index bounds the scan to rows for this instance
only. These tests pin the new behavior so a future refactor can't regress
to the full scan.

Postgres port (G11): the index now lives in db/schema.py's registry, so the
bootstrap is ``store.index_list`` + ``schema.ensure_table`` instead of
index_create/index_wait, and the query is one Selection instead of a fast
path plus a fallback. The assertions are on behaviour, not on the chain.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

_BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

TABLE = "GraphNexusTradeContexts"


@pytest.fixture(autouse=True)
def _reset_index_ready_flag():
    """Each test starts with a fresh module-level index-ready flag so
    the lazy bootstrap path is actually exercised."""
    from nexus_lookback_db import reset_index_ready_flag_for_tests

    reset_index_ready_flag_for_tests()
    yield
    reset_index_ready_flag_for_tests()


def _seed(store, rows):
    """Insert resume rows, giving each a primary key."""
    store.insert(TABLE, [dict(row, id=f"row-{i}") for i, row in enumerate(rows)])


def test_ensure_index_skips_when_already_present(store, monkeypatch):
    """When the registry already declares the index, the helper must NOT
    run DDL and must short-circuit."""
    import nexus_lookback_db as nlb

    ddl = MagicMock()
    monkeypatch.setattr(nlb, "store", store)
    monkeypatch.setattr(nlb.db_schema, "ensure_table", ddl)

    ok = nlb.ensure_nexus_trade_contexts_instance_id_index(None, None)

    assert ok is True
    assert nlb._NEXUS_TRADE_CONTEXTS_INDEX_READY is True
    ddl.assert_not_called()


def test_ensure_index_creates_when_missing(store, monkeypatch):
    """When the index is absent, the helper runs the schema DDL."""
    import nexus_lookback_db as nlb

    ddl = MagicMock()
    monkeypatch.setattr(nlb, "store", store)
    monkeypatch.setattr(nlb.store, "index_list", lambda _t: [])
    monkeypatch.setattr(nlb.db_schema, "ensure_table", ddl)

    ok = nlb.ensure_nexus_trade_contexts_instance_id_index(None, None)

    assert ok is True
    ddl.assert_called_with(TABLE)
    assert nlb._NEXUS_TRADE_CONTEXTS_INDEX_READY is True


def test_ensure_index_returns_false_on_exception(store, monkeypatch):
    """Any failure must not raise — the helper returns False so the caller
    falls back to the unindexed scan."""
    import nexus_lookback_db as nlb

    def _boom(_t):
        raise RuntimeError("db down")

    monkeypatch.setattr(nlb, "store", store)
    monkeypatch.setattr(nlb.store, "index_list", _boom)

    ok = nlb.ensure_nexus_trade_contexts_instance_id_index(None, None)

    assert ok is False
    assert nlb._NEXUS_TRADE_CONTEXTS_INDEX_READY is False


def test_ensure_index_short_circuits_after_first_success(store, monkeypatch):
    """The module-level flag must cache the success so subsequent calls
    don't re-check."""
    import nexus_lookback_db as nlb

    calls = []
    monkeypatch.setattr(nlb, "store", store)
    monkeypatch.setattr(nlb.store, "index_list",
                        lambda t: calls.append(t) or ["instance_id"])

    nlb.ensure_nexus_trade_contexts_instance_id_index(None, None)
    ok2 = nlb.ensure_nexus_trade_contexts_instance_id_index(None, None)

    assert ok2 is True
    assert len(calls) == 1


def test_load_processed_dates_returns_empty_on_empty_input():
    import nexus_lookback_db as nlb

    assert nlb.load_nexus_processed_trade_context_dates("", []) == set()
    assert nlb.load_nexus_processed_trade_context_dates("inst", []) == set()
    assert nlb.load_nexus_processed_trade_context_dates("", ["2025-12-31"]) == set()


def test_load_processed_dates_returns_empty_when_table_missing(store, monkeypatch):
    """If GraphNexusTradeContexts holds nothing yet, return empty
    (the lookback will run all dates fresh)."""
    import nexus_lookback_db as nlb

    monkeypatch.setattr(nlb, "store", store)
    monkeypatch.setattr(nlb.store, "table_list", lambda: [])
    out = nlb.load_nexus_processed_trade_context_dates(
        "inst|scope", ["2025-12-31", "2026-01-01"]
    )
    assert out == set()


def test_load_processed_dates_is_scoped_to_the_instance(store, monkeypatch):
    """The query must return only THIS instance's date_keys, and only the
    ones actually asked for."""
    import nexus_lookback_db as nlb

    monkeypatch.setattr(nlb, "store", store)
    _seed(store, [
        {"instance_id": "inst|scope", "date_key": "2025-12-31"},
        {"instance_id": "inst|scope", "date_key": "2026-01-01"},
        {"instance_id": "inst|scope", "date_key": "2026-01-05"},   # not asked for
        {"instance_id": "other|scope", "date_key": "2026-01-02"},  # other instance
    ])

    out = nlb.load_nexus_processed_trade_context_dates(
        "inst|scope", ["2025-12-31", "2026-01-01", "2026-01-02"]
    )

    assert out == {"2025-12-31", "2026-01-01"}


def test_load_processed_dates_still_correct_when_the_index_is_unavailable(
        store, monkeypatch):
    """If the index bootstrap fails, the function must still return correct
    results rather than raising or returning empty."""
    import nexus_lookback_db as nlb

    def _boom(_t):
        raise RuntimeError("perm denied")

    monkeypatch.setattr(nlb, "store", store)
    _seed(store, [{"instance_id": "inst|scope", "date_key": "2025-12-31"}])
    monkeypatch.setattr(nlb.store, "index_list", _boom)

    out = nlb.load_nexus_processed_trade_context_dates("inst|scope", ["2025-12-31"])

    assert out == {"2025-12-31"}


def test_strict_load_ignores_legacy_unverified_rows(store, monkeypatch):
    import nexus_lookback_db as nlb

    monkeypatch.setattr(nlb, "store", store)
    _seed(store, [
        {"instance_id": "inst|scope", "date_key": "2025-12-31"},
        {"instance_id": "inst|scope", "date_key": "2026-01-01",
         "pit_provenance": "legacy_unverified"},
    ])

    out = nlb.load_nexus_processed_trade_context_dates(
        "inst|scope", ["2025-12-31", "2026-01-01"], require_strict=True,
    )

    assert out == set()


def test_strict_load_accepts_only_complete_verified_provenance(store, monkeypatch):
    import nexus_lookback_db as nlb

    monkeypatch.setattr(nlb, "store", store)
    _seed(store, [
        {"instance_id": "inst|scope", "date_key": "2025-12-31",
         "pit_provenance": "strict_verified", "pit_manifest_id": "manifest-a",
         "pit_as_of": "2025-12-31T21:00:00+00:00"},
        {"instance_id": "inst|scope", "date_key": "2026-01-01",
         "pit_provenance": "strict_verified", "pit_manifest_id": "",
         "pit_as_of": "2026-01-01T21:00:00+00:00"},
    ])

    out = nlb.load_nexus_processed_trade_context_dates(
        "inst|scope", ["2025-12-31", "2026-01-01"], require_strict=True,
    )

    assert out == {"2025-12-31"}


def test_load_processed_dates_swallows_connection_failure():
    """If the database is unreachable, return empty rather than propagating
    (matches the legacy behavior — the caller will just process every
    date afresh)."""
    import nexus_lookback_db as nlb

    with patch.object(nlb, "_connect", side_effect=RuntimeError("net down")):
        out = nlb.load_nexus_processed_trade_context_dates(
            "inst|scope", ["2025-12-31"]
        )
    assert out == set()


def test_historic_lookback_resume_dates_returns_suffix_from_first_gap():
    """Skip the contiguous prefix already in the table; resume from the
    first unprocessed date."""
    import datetime as _dt
    import nexus_lookback_db as nlb

    opens = [
        _dt.datetime(2025, 12, 31),
        _dt.datetime(2026, 1, 1),
        _dt.datetime(2026, 1, 2),
        _dt.datetime(2026, 1, 3),
    ]
    with patch.object(
        nlb,
        "load_nexus_processed_trade_context_dates",
        return_value={"2025-12-31", "2026-01-01"},
    ):
        out = nlb.historic_lookback_resume_dates("inst|scope", opens)

    assert out == [opens[2], opens[3]]


def test_historic_lookback_resume_dates_returns_empty_when_all_processed():
    import datetime as _dt
    import nexus_lookback_db as nlb

    opens = [_dt.datetime(2025, 12, 31), _dt.datetime(2026, 1, 1)]
    with patch.object(
        nlb,
        "load_nexus_processed_trade_context_dates",
        return_value={"2025-12-31", "2026-01-01"},
    ):
        out = nlb.historic_lookback_resume_dates("inst|scope", opens)

    assert out == []


def test_historic_lookback_resume_dates_returns_full_when_none_processed():
    """Fresh-from-scratch (the case after we cleared main's lookback
    state) — every day must be in the work list."""
    import datetime as _dt
    import nexus_lookback_db as nlb

    opens = [_dt.datetime(2025, 12, 31), _dt.datetime(2026, 1, 1)]
    with patch.object(
        nlb, "load_nexus_processed_trade_context_dates", return_value=set()
    ):
        out = nlb.historic_lookback_resume_dates("inst|scope", opens)

    assert out == opens


def test_historic_resume_requires_strict_rows_by_default():
    import datetime as _dt
    import nexus_lookback_db as nlb

    opens = [_dt.datetime(2025, 12, 31)]
    with patch.object(
        nlb, "load_nexus_processed_trade_context_dates", return_value=set()
    ) as loader:
        nlb.historic_lookback_resume_dates("inst|scope", opens)

    # Strict by default remains the invariant. allow_research is opt-in and
    # only a research run passes it — a strict run resuming from unverified
    # rows would launder current-state data into a strict result.
    loader.assert_called_once_with(
        "inst|scope",
        ["2025-12-31"],
        require_strict=True,
        allow_research=False,
    )


# ── Strategy-side index plumbing tests ────────────────────────────────────
#
# graph_nexus_analysis.py is group G4's file and is still on ReQL, so these
# three keep the ReQL-chain double.


def _build_reql_chain(rows=None):
    chain = MagicMock()
    for name in ("db", "table", "filter", "get_all", "pluck",
                 "index_create", "index_wait", "expr"):
        getattr(chain, name).return_value = chain
    chain.run.return_value = rows or []
    return chain



def test_ensure_nexus_history_table_passes_indexes_through():
    """The strategy-side ``_ensure_nexus_history_table`` must accept the
    ``indexes`` kwarg, create the table through db.schema, and record the
    marker for every index the registry declares.

    Under Postgres the index set lives in ``db/schema.py`` and
    ``CREATE INDEX`` is synchronous, so there is no index_create/index_wait
    pair to assert on -- the contract is "ensure_table was called and the
    named index is declared".
    """
    from strategies import graph_nexus_analysis as gna

    ensured = []
    gna._nexus_history_tables_ensured.clear()

    fake_conn = MagicMock()
    with patch.object(gna._dbschema, "ensure_table", side_effect=ensured.append):
        gna._ensure_nexus_history_table(
            fake_conn, "GraphNexusTradeContexts", indexes=("instance_id",)
        )

    assert ensured == ["GraphNexusTradeContexts"]
    assert "GraphNexusTradeContexts::instance_id" in gna._nexus_history_tables_ensured


def test_ensure_nexus_history_table_undeclared_index_is_not_marked():
    """An index the registry does not declare must NOT be recorded as ready --
    silently marking it would hide a missing index behind a memo."""
    from strategies import graph_nexus_analysis as gna

    gna._nexus_history_tables_ensured.clear()

    fake_conn = MagicMock()
    with patch.object(gna._dbschema, "ensure_table"):
        gna._ensure_nexus_history_table(
            fake_conn, "GraphNexusTradeContexts", indexes=("not_a_real_field",)
        )

    assert ("GraphNexusTradeContexts::not_a_real_field"
            not in gna._nexus_history_tables_ensured)


def test_ensure_nexus_history_table_memoises_per_table():
    """A second call for an already-ensured table does no further DDL work."""
    from strategies import graph_nexus_analysis as gna

    ensured = []
    gna._nexus_history_tables_ensured.clear()

    fake_conn = MagicMock()
    with patch.object(gna._dbschema, "ensure_table", side_effect=ensured.append):
        gna._ensure_nexus_history_table(fake_conn, "SomeOtherTable")
        gna._ensure_nexus_history_table(fake_conn, "SomeOtherTable")

    assert ensured == ["SomeOtherTable"]
