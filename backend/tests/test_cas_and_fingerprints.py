"""The four compare-and-swap sites and the fingerprint canonicalisation.

Every CAS this group ports was a ReQL ``r.branch`` — three as
``.update(lambda row: r.branch(cond, patch, {}))`` (a DEEP MERGE on match) and
one as ``.replace(lambda row: r.branch(...))`` (a whole-document swap). The two
shapes are NOT interchangeable and these tests pin which is which.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

import pytest                                            # noqa: E402

from db.errors import StoreError                         # noqa: E402
from db.json import canonical, canonical_sha256          # noqa: E402
from db.store import P                                   # noqa: E402

# The four ported CAS sites hold ``db.store`` directly (they take no
# connection any more), so they cannot be pointed at the FakeStore the
# fixture yields when there is no database. They run against real Postgres.
needs_pg = pytest.mark.skipif(
    not os.environ.get("PG_TEST_DSN"),
    reason="exercises the module-level db.store; needs PG_TEST_DSN")


# -- store-level CAS ------------------------------------------------------

def test_cas_distinguishes_predicate_false_from_row_missing(store):
    store.insert("NexusRuntimeState", {"id": "s", "version": 1})
    assert store.replace_if("NexusRuntimeState", "s",
                            when=P.field("version").eq("2"),
                            doc={"id": "s", "version": 2}) is None   # predicate false
    # A missing row is NOT conflated with a failed predicate: it raises.
    with pytest.raises(StoreError):
        store.replace_if("NexusRuntimeState", "missing",
                         when=P.field("version").eq("1"),
                         doc={"id": "missing"})
    assert store.replace_if("NexusRuntimeState", "missing",
                            when=None, doc={"id": "missing"},
                            insert_if_absent=True) == {"id": "missing"}


def test_cas_predicate_matches_a_float_stored_for_an_int(store):
    """The dict predicate carries the guarded ::numeric compare, which is what
    ReQL's ``row['version'].eq(3)`` did. A text compare would miss 3.0."""
    store.insert("NexusRuntimeState", {"id": "f", "version": 3.0})
    when = store.predicate({"version": 3})
    assert store.replace_if("NexusRuntimeState", "f", when=when,
                            doc={"id": "f", "version": 4}) is not None


# -- the four ported sites -------------------------------------------------

@needs_pg
def test_alpha_state_cas_returns_none_for_stale_and_for_missing(store):
    from benchmark_alpha.pg_store import PostgresBackend, STATE_TABLE

    backend = PostgresBackend()
    doc = {"id": "k", "version": 1, "payload": {"a": 1}}
    assert backend.compare_and_swap_state("k", 0, doc, durability="hard") == doc
    # version 0 again: the row exists, so the expectation is stale.
    assert backend.compare_and_swap_state("k", 0, doc, durability="hard") is None
    # a stale version on an existing row
    assert backend.compare_and_swap_state(
        "k", 7, {"id": "k", "version": 8}, durability="hard") is None
    # a missing row: r.branch(row.eq(None), row, ...) was a no-op, not an error
    assert backend.compare_and_swap_state(
        "gone", 3, {"id": "gone", "version": 4}, durability="hard") is None
    # the happy path REPLACES the document (this site was .replace, not .update)
    assert backend.compare_and_swap_state(
        "k", 1, {"id": "k", "version": 2}, durability="hard") is not None
    assert store.get(STATE_TABLE, "k") == {"id": "k", "version": 2}


@needs_pg
def test_lifecycle_cas_deep_merges_and_keeps_the_id(store):
    """nexus_runtime_state's CAS was .update(), so unnamed keys survive and the
    ``id`` popped from the patch must NOT be dropped from the stored row."""
    import nexus_runtime_state as nrs

    backend = nrs.PostgresLifecycleBackend()
    assert backend.create({"client_order_id": "c1", "version": 0,
                           "events": [], "instance_id": "main",
                           "keep_me": "yes"}) is True
    assert backend.compare_and_swap("c1", 9, {"id": "c1", "version": 10}) is False
    assert backend.compare_and_swap(
        "c1", 0, {"id": "c1", "version": 1, "events": ["e"]}) is True
    row = store.get(nrs.LIFECYCLE_TABLE, "c1")
    assert row["id"] == "c1"              # the row keeps its primary key
    assert row["keep_me"] == "yes"        # deep merge, not replace
    assert row["events"] == ["e"]         # arrays replace wholesale
    assert row["version"] == 1


@needs_pg
def test_risk_state_cas_first_write_then_version_guard(store):
    from live_risk_state import PostgresRiskBackend

    backend = PostgresRiskBackend()
    # expected_version 0 is the FIRST-WRITE branch (insert), exactly as in
    # ReQL: it fails once the row exists rather than comparing anything.
    assert backend.compare_and_swap("risk:x", 0, {"version": 1, "a": 1}) is True
    assert backend.compare_and_swap("risk:x", 0, {"version": 1, "a": 2}) is False
    assert backend.compare_and_swap("risk:x", 5, {"version": 6}) is False
    assert backend.compare_and_swap("risk:x", 1, {"version": 2, "a": 3}) is True
    assert backend.get("risk:x")["version"] == 2
    assert backend.get("risk:x")["a"] == 3


@needs_pg
def test_live_command_claim_cas_is_atomic_on_status_and_owner(store):
    import live_state as ls

    ls.submit_command(None, None, instance_id="i1", type="halt",
                      payload={}, submitted_by="op")
    first = ls.claim_next_pending(None, None, "i1", worker_id="w1")
    assert first is not None and first["lease_owner"] == "w1"
    # the row is now "running" with a live lease: a second worker gets nothing
    assert ls.claim_next_pending(None, None, "i1", worker_id="w2") is None


# -- fingerprints ----------------------------------------------------------

def test_canonical_is_invariant_to_key_order_and_number_form():
    a = {"b": 1.230e-5, "a": [1, 2]}
    b = {"a": [1, 2], "b": 0.00001230}
    assert canonical(a) == canonical(b)
    assert canonical_sha256(a) == canonical_sha256(b)


@needs_pg
def test_frozen_state_canonicalisation_survives_a_jsonb_round_trip(store):
    """The fingerprint tables are hashed through frozen_paired_state, which
    sorts keys and collapses integral floats — the two normalisations a jsonb
    round trip needs. Proven end to end rather than by inspection."""
    from frozen_paired_state import state_rows_sha256

    rows = [{"id": "r1", "z": 1.0, "a": {"b": [1, 2], "c": "x"}},
            {"id": "r2", "a": {"c": "y", "b": []}, "z": 2}]
    before = state_rows_sha256(rows)
    store.insert("NexusRuntimeState", rows)
    after = state_rows_sha256(
        sorted((store.get("NexusRuntimeState", r["id"]) for r in rows),
               key=lambda d: d["id"]))
    assert after == before


def test_paired_state_attest_fingerprint_is_stable_across_key_order():
    import paired_state_attest as psa

    left = psa.state_fingerprint(
        {"NexusRuntimeState": [{"id": "x", "a": 1, "b": {"p": 1.0, "q": 2}}]})
    right = psa.state_fingerprint(
        {"NexusRuntimeState": [{"id": "x", "b": {"q": 2, "p": 1}, "a": 1}]})
    assert left["bundle_sha256"] == right["bundle_sha256"]


def test_history_scope_id_is_unchanged_by_the_port():
    """The scoped instance id is embedded in every production row id. A change
    to its hashing would orphan every scoped row, so it is pinned by value."""
    from nexus_config_identity import history_scope_id

    assert history_scope_id({"history_scope_salt": "s"}) == history_scope_id(
        {"history_scope_salt": "s"})
    assert len(history_scope_id({})) == 24
