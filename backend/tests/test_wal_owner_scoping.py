"""WAL ownership is keyed on (instance_id, account_id), not an 8-char prefix.

The clean-room classifier used to decide "is this broker position mine?" from
the first 8 alphanumeric characters of the instance id. Two instances whose
ids share those 8 characters -- ``strategy-eb`` and ``strategy-eb-lab`` both
collapse to ``strategy-`` -- therefore read each other's fills, and a
human-held position at the other instance's broker account could be adopted
as strategy-owned and sold.

These tests pin the replacement: every WAL row carries the instance_id and
account_id that wrote it, the reader filters on both exactly, and rows that
carry neither (written before this change) are never owned.
"""
from __future__ import annotations

import inspect
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from broker_adapters._classifier import classify_broker_positions, derive_cid_prefix
from broker_adapters._wal import InMemoryStore, LiveOrderWAL


EB = "strategy-eb"
EB_LAB = "strategy-eb-lab"
ACCT_LIVE = "acct-live-1"
ACCT_LAB = "acct-paper-2"


def _now() -> datetime:
    return datetime(2026, 9, 11, 15, 0, tzinfo=timezone.utc)


def test_the_two_instance_ids_really_do_collide_on_the_prefix():
    """The premise: the old ownership signal cannot tell these two apart."""
    assert derive_cid_prefix(EB) == derive_cid_prefix(EB_LAB) == "strategy-"


# --- record_intent persists the owner -------------------------------------


def test_record_intent_persists_instance_and_account_id():
    wal = LiveOrderWAL(InMemoryStore(), instance_id=EB, account_id=ACCT_LIVE)
    wal.record_intent("strategy-abc-0", "SNDK", "buy", qty=3.0, notional=None)
    row = wal._store.get("strategy-abc-0")
    assert row["instance_id"] == EB
    assert row["account_id"] == ACCT_LIVE


def test_record_intent_on_an_unbound_wal_writes_no_owner_fields():
    """Back-compat: an unbound WAL still writes, and the row is simply
    un-owned -- which the reader treats as not-mine."""
    wal = LiveOrderWAL(InMemoryStore())
    wal.record_intent("strategy-abc-0", "SNDK", "buy", qty=3.0, notional=None)
    row = wal._store.get("strategy-abc-0")
    assert not row.get("instance_id")
    assert not row.get("account_id")


def test_bind_owner_sets_identity_after_construction():
    wal = LiveOrderWAL(InMemoryStore())
    wal.bind_owner(instance_id=EB, account_id=ACCT_LIVE)
    wal.record_intent("strategy-abc-0", "SNDK", "buy", qty=1.0, notional=None)
    assert wal._store.get("strategy-abc-0")["instance_id"] == EB


# --- the owner-scoped reader ----------------------------------------------


def _fill(store, cid, symbol, instance_id, account_id, qty=10.0, side="buy"):
    row = {
        "client_order_id": cid,
        "symbol": symbol,
        "side": side,
        "state": "filled",
        "filled_qty": qty,
        "filled_avg_price": 100.0,
        "broker_order_id": "brk-" + cid,
        "updated_at_utc": _now().isoformat(),
        "created_at_utc": _now().isoformat(),
    }
    if instance_id is not None:
        row["instance_id"] = instance_id
    if account_id is not None:
        row["account_id"] = account_id
    store.insert(row)


def test_list_filled_for_owner_keeps_only_exact_owner_rows():
    store = InMemoryStore()
    _fill(store, "strategy-aaa-0", "SNDK", EB, ACCT_LIVE)
    _fill(store, "strategy-bbb-0", "AMD", EB_LAB, ACCT_LAB)        # sibling instance
    _fill(store, "strategy-ccc-0", "NVDA", EB, ACCT_LAB)           # same id, other account
    _fill(store, "strategy-ddd-0", "TSLA", None, None)             # legacy row
    wal = LiveOrderWAL(store, instance_id=EB, account_id=ACCT_LIVE)

    rows = wal.list_filled_for_owner(EB, ACCT_LIVE)
    assert [r["client_order_id"] for r in rows] == ["strategy-aaa-0"]


def test_list_filled_for_owner_refuses_a_blank_owner():
    """A missing owner identity must not degrade into 'everything'."""
    store = InMemoryStore()
    _fill(store, "strategy-aaa-0", "SNDK", EB, ACCT_LIVE)
    wal = LiveOrderWAL(store)
    assert wal.list_filled_for_owner("", "") == []
    assert wal.list_filled_for_owner(EB, "") == []
    assert wal.list_filled_for_owner(None, None) == []


def test_list_filled_for_prefix_on_a_bound_wal_is_owner_scoped():
    """The clean-room boot path calls list_filled_for_prefix. Once the WAL
    knows its owner, that call must not return the sibling's fills."""
    store = InMemoryStore()
    _fill(store, "strategy-aaa-0", "SNDK", EB, ACCT_LIVE)
    _fill(store, "strategy-bbb-0", "AMD", EB_LAB, ACCT_LAB)
    _fill(store, "strategy-ccc-0", "TSLA", None, None)
    wal = LiveOrderWAL(store, instance_id=EB, account_id=ACCT_LIVE)

    rows = wal.list_filled_for_prefix("strategy-")
    assert [r["client_order_id"] for r in rows] == ["strategy-aaa-0"]


def test_list_filled_for_prefix_unbound_keeps_legacy_behaviour():
    store = InMemoryStore()
    _fill(store, "strategy-aaa-0", "SNDK", EB, ACCT_LIVE)
    _fill(store, "strategy-ccc-0", "TSLA", None, None)
    wal = LiveOrderWAL(store)
    assert len(wal.list_filled_for_prefix("strategy-")) == 2


def test_inmemory_and_walstore_agree_on_the_owner_reader_signature():
    from nexus_runtime_state import WALStore

    assert hasattr(WALStore, "list_filled_for_owner")
    params = list(inspect.signature(WALStore.list_filled_for_owner).parameters)
    assert params[:3] == ["self", "instance_id", "account_id"]
    assert "since_utc" in params
    assert params == list(inspect.signature(InMemoryStore.list_filled_for_owner).parameters)


# --- the classifier -------------------------------------------------------


def _position(symbol, qty):
    return {"symbol": symbol, "qty": qty, "market_value": qty * 100.0}


def test_a_human_held_position_is_never_adopted_from_the_siblings_fills():
    """The headline case: the lab instance bought 10 AMD; a human bought 10
    AMD in the live account. The live instance must quarantine it."""
    sibling_rows = [
        {
            "client_order_id": "strategy-bbb-0",   # same 8-char prefix
            "symbol": "AMD",
            "side": "buy",
            "state": "filled",
            "filled_qty": 10.0,
            "filled_avg_price": 100.0,
            "broker_order_id": "brk-1",
            "updated_at_utc": _now().isoformat(),
            "instance_id": EB_LAB,
            "account_id": ACCT_LAB,
        }
    ]
    owned, external, trades = classify_broker_positions(
        positions=[_position("AMD", 10.0)],
        wal_rows=sibling_rows,
        instance_id=EB,
        account_id=ACCT_LIVE,
        now_utc=_now(),
    )
    assert owned == {}
    assert "AMD" in external
    assert trades == []


def test_same_instance_other_account_is_not_owned():
    rows = [
        {
            "client_order_id": "strategy-bbb-0",
            "symbol": "AMD",
            "side": "buy",
            "state": "filled",
            "filled_qty": 10.0,
            "filled_avg_price": 100.0,
            "broker_order_id": "brk-1",
            "updated_at_utc": _now().isoformat(),
            "instance_id": EB,
            "account_id": ACCT_LAB,
        }
    ]
    owned, external, _ = classify_broker_positions(
        positions=[_position("AMD", 10.0)],
        wal_rows=rows,
        instance_id=EB,
        account_id=ACCT_LIVE,
        now_utc=_now(),
    )
    assert owned == {}
    assert "AMD" in external


def test_legacy_rows_without_owner_fields_are_not_owned_when_an_owner_is_known():
    rows = [
        {
            "client_order_id": "strategy-bbb-0",
            "symbol": "AMD",
            "side": "buy",
            "state": "filled",
            "filled_qty": 10.0,
            "filled_avg_price": 100.0,
            "broker_order_id": "brk-1",
            "updated_at_utc": _now().isoformat(),
        }
    ]
    owned, external, _ = classify_broker_positions(
        positions=[_position("AMD", 10.0)],
        wal_rows=rows,
        instance_id=EB,
        account_id=ACCT_LIVE,
        now_utc=_now(),
    )
    assert owned == {}
    assert external["AMD"]["qty"] == 10.0


def test_matching_owner_rows_are_still_adopted():
    rows = [
        {
            "client_order_id": "strategy-aaa-0",
            "symbol": "AMD",
            "side": "buy",
            "state": "filled",
            "filled_qty": 10.0,
            "filled_avg_price": 100.0,
            "broker_order_id": "brk-1",
            "updated_at_utc": _now().isoformat(),
            "instance_id": EB,
            "account_id": ACCT_LIVE,
        }
    ]
    owned, external, trades = classify_broker_positions(
        positions=[_position("AMD", 10.0)],
        wal_rows=rows,
        instance_id=EB,
        account_id=ACCT_LIVE,
        now_utc=_now(),
    )
    assert owned == {"AMD": 10.0}
    assert external == {}
    assert [t["ticker"] for t in trades] == ["AMD"]


def test_without_an_account_id_a_foreign_instance_row_is_still_rejected():
    """The legacy call shape (no account_id) keeps working for rows that
    predate owner stamping, but a row that names a DIFFERENT instance is
    rejected regardless -- that is the prefix collision itself."""
    rows = [
        {
            "client_order_id": "strategy-bbb-0",
            "symbol": "AMD",
            "side": "buy",
            "state": "filled",
            "filled_qty": 10.0,
            "filled_avg_price": 100.0,
            "broker_order_id": "brk-1",
            "updated_at_utc": _now().isoformat(),
            "instance_id": EB_LAB,
            "account_id": ACCT_LAB,
        },
        {
            "client_order_id": "strategy-ccc-0",
            "symbol": "SNDK",
            "side": "buy",
            "state": "filled",
            "filled_qty": 4.0,
            "filled_avg_price": 50.0,
            "broker_order_id": "brk-2",
            "updated_at_utc": _now().isoformat(),
        },
    ]
    owned, external, _ = classify_broker_positions(
        positions=[_position("AMD", 10.0), _position("SNDK", 4.0)],
        wal_rows=rows,
        instance_id=EB,
        now_utc=_now(),
    )
    assert "AMD" in external and "AMD" not in owned
    assert owned.get("SNDK") == 4.0


# --- the factory binds the owner ------------------------------------------


def test_build_adapter_accepts_an_account_id():
    from broker_adapters import factory

    assert "account_id" in inspect.signature(factory.build_adapter).parameters


def test_factory_derives_a_stable_account_fingerprint_without_leaking_the_key():
    from broker_adapters.factory import derive_account_identity

    a = derive_account_identity(broker_type="alpaca", paper=False, api_key="AKSECRET123")
    b = derive_account_identity(broker_type="alpaca", paper=False, api_key="AKSECRET123")
    c = derive_account_identity(broker_type="alpaca", paper=True, api_key="AKSECRET123")
    d = derive_account_identity(broker_type="alpaca", paper=False, api_key="OTHERKEY")
    assert a == b
    assert a != c and a != d
    assert "AKSECRET123" not in a


def test_explicit_account_id_wins_over_the_fingerprint():
    from broker_adapters.factory import derive_account_identity

    got = derive_account_identity(
        broker_type="alpaca", paper=False, api_key="AKSECRET123",
        account_id="brokerage-doc-7",
    )
    assert got == "brokerage-doc-7"


def test_retention_and_dry_run_filters_still_apply_under_owner_scoping():
    old = (_now() - timedelta(days=400)).isoformat()
    rows = [
        {
            "client_order_id": "strategy-aaa-0", "symbol": "AMD", "side": "buy",
            "state": "filled", "filled_qty": 10.0, "filled_avg_price": 100.0,
            "broker_order_id": "brk-1", "updated_at_utc": old,
            "instance_id": EB, "account_id": ACCT_LIVE,
        },
        {
            "client_order_id": "strategy-bbb-0", "symbol": "SNDK", "side": "buy",
            "state": "filled", "filled_qty": 4.0, "filled_avg_price": 50.0,
            "broker_order_id": "dry-xyz", "updated_at_utc": _now().isoformat(),
            "instance_id": EB, "account_id": ACCT_LIVE,
        },
    ]
    owned, external, _ = classify_broker_positions(
        positions=[_position("AMD", 10.0), _position("SNDK", 4.0)],
        wal_rows=rows,
        instance_id=EB,
        account_id=ACCT_LIVE,
        now_utc=_now(),
    )
    assert owned == {}
    assert "aged-out" in external["AMD"]["note"]
    assert "SNDK" in external
