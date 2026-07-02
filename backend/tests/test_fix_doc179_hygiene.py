"""Unit tests for the doc-179 hygiene script's pure logic.

All fixtures use FAKE keys (``AKFAKE...``/``SKFAKE...``) — no real secrets appear
here, and nothing in this file touches a real database. The pure
``build_updates`` / ``extract_broker_creds`` functions are exercised, plus
``apply_updates`` against a FAKE in-memory rdb object (no network, no driver).
"""

import copy
import os
import sys

import pytest

_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

from scripts.fix_doc179_hygiene import (  # noqa: E402
    apply_updates,
    build_updates,
    extract_broker_creds,
    HALT_CLEAR,
)

WORKING_KEY = "AKFAKELIVEKEY1111111"
WORKING_SECRET = "SKFAKELIVESECRET11111111111111111111"


def _doc179():
    """A doc-179-shaped Strategies row with a DEAD inner alpaca key."""
    return {
        "id": 179,
        "name": "alpaca-main strategy",
        "strategies": [
            {
                "name": "sub-strategy-0",
                "config": {
                    "alpaca_key": "AKFAKEDEADKEY0000000",
                    "alpaca_secret": "SKFAKEDEADSECRET00000000000000000000",
                    "risk_pct": 2.5,
                    "keep_me": "untouched-inner",
                },
            },
            {
                "name": "sub-strategy-1",
                "config": {"alpaca_key": "AKFAKEOTHER_DONOTTOUCH"},
            },
        ],
        "outer_field": "untouched-outer",
    }


def _brokerage_row():
    """The 'Alpaca Live' BrokerageAccounts row holding the WORKING key.

    Field names mirror the REAL prod row (verified live 2026-07-02):
    ``alpaca_key``/``alpaca_secret`` — NOT ``key``/``secret``.
    """
    return {
        "id": "08f683af-76f6-404d-872c-37baa45711ee",
        "account_name": "Alpaca Live",
        "brokerage_type": "alpaca",
        "alpaca_key": WORKING_KEY,
        "alpaca_secret": WORKING_SECRET,
        "alpaca_paper": False,
        "status": "active",
    }


# --- extract_broker_creds ---------------------------------------------------


def test_extract_creds_real_prod_field_names():
    """Primary case: the prod row's actual fields, alpaca_key/alpaca_secret."""
    k, s, kf, sf = extract_broker_creds(_brokerage_row())
    assert k == WORKING_KEY
    assert s == WORKING_SECRET
    assert kf == "alpaca_key"
    assert sf == "alpaca_secret"


def test_extract_creds_key_secret_fallback_pair():
    """Fallback case: a row using plain key/secret is still handled."""
    row = {"id": "x", "key": "AKFAKEALT1", "secret": "SKFAKEALT2"}
    k, s, kf, sf = extract_broker_creds(row)
    assert k == "AKFAKEALT1"
    assert s == "SKFAKEALT2"
    assert kf == "key"
    assert sf == "secret"


def test_extract_creds_missing_raises():
    with pytest.raises(ValueError):
        extract_broker_creds({"id": "x", "account_name": "no creds here"})

    with pytest.raises(ValueError):
        extract_broker_creds({"id": "x", "key": "", "secret": ""})


# --- build_updates ----------------------------------------------------------


def test_build_updates_replaces_only_inner_creds():
    doc = _doc179()
    updates = build_updates(doc, _brokerage_row())

    strat_update = updates["strategies_179_update"]
    # Rethink-ready partial update touches ONLY the top-level "strategies" field.
    assert list(strat_update.keys()) == ["strategies"]

    new_arr = strat_update["strategies"]
    cfg0 = new_arr[0]["config"]
    # inner creds swapped to the working brokerage key/secret
    assert cfg0["alpaca_key"] == WORKING_KEY
    assert cfg0["alpaca_secret"] == WORKING_SECRET
    # everything else in config[0] untouched
    assert cfg0["risk_pct"] == 2.5
    assert cfg0["keep_me"] == "untouched-inner"
    assert set(cfg0.keys()) == {"alpaca_key", "alpaca_secret", "risk_pct", "keep_me"}
    # sub-strategy-1 untouched entirely
    assert new_arr[1] == {"name": "sub-strategy-1", "config": {"alpaca_key": "AKFAKEOTHER_DONOTTOUCH"}}


def test_build_updates_outer_config_untouched():
    updates = build_updates(_doc179(), _brokerage_row())
    strat_update = updates["strategies_179_update"]
    # Outer-level fields (name, outer_field, id) are NOT part of the update.
    assert "outer_field" not in strat_update
    assert "name" not in strat_update
    assert "id" not in strat_update


def test_build_updates_does_not_mutate_input():
    doc = _doc179()
    before = copy.deepcopy(doc)
    build_updates(doc, _brokerage_row())
    assert doc == before  # pure: source doc must be untouched


def test_build_updates_includes_halt_clear():
    updates = build_updates(_doc179(), _brokerage_row())
    assert updates["instance_halt_clear"] == {"halt_reason": None, "halted_at": None}
    assert updates["instance_halt_clear"] == HALT_CLEAR


def test_build_updates_meta_fingerprints_show_mismatch():
    updates = build_updates(_doc179(), _brokerage_row())
    meta = updates["meta"]
    # dead vs working key must differ → mismatch proven by differing fingerprints
    assert meta["old_key_fp"] != meta["new_key_fp"]
    assert meta["old_secret_fp"] != meta["new_secret_fp"]
    assert meta["key_changed"] is True
    assert meta["broker_key_field"] == "alpaca_key"
    assert meta["broker_secret_field"] == "alpaca_secret"
    # no raw secret values leak into meta
    blob = repr(meta)
    assert "AKFAKE" not in blob
    assert "SKFAKE" not in blob


def test_build_updates_no_change_when_already_matching():
    doc = _doc179()
    row = _brokerage_row()
    # make doc already carry the working key
    doc["strategies"][0]["config"]["alpaca_key"] = row["alpaca_key"]
    doc["strategies"][0]["config"]["alpaca_secret"] = row["alpaca_secret"]
    updates = build_updates(doc, row)
    assert updates["meta"]["key_changed"] is False
    assert updates["meta"]["old_key_fp"] == updates["meta"]["new_key_fp"]


# --- apply_updates: scrubbed exceptions (no real DB — fake rdb object) -------


class FakeReqlError(Exception):
    """Stands in for ReqlRuntimeError: its str() renders the query term tree,
    which INCLUDES the literal update payload (i.e. the raw key/secret)."""


class _FakeRdb:
    """Mimics r.db(...).table(...).get(...).update(payload).run(conn).

    Tables listed in ``fail_tables`` raise a driver-style error whose message
    embeds the full payload — exactly the leak vector apply_updates must scrub.
    """

    def __init__(self, fail_tables=()):
        self.fail_tables = set(fail_tables)
        self.writes = []  # (table, payload) of successful runs
        self._table = None
        self._payload = None

    def db(self, name):
        return self

    def table(self, name):
        self._table = name
        return self

    def get(self, _id):
        return self

    def update(self, payload):
        self._payload = payload
        return self

    def run(self, conn):
        if self._table in self.fail_tables:
            raise FakeReqlError(
                "ReqlRuntimeError: Cannot perform write: term tree = "
                "r.table(%r).update(%r)" % (self._table, self._payload)
            )
        self.writes.append((self._table, self._payload))
        return {"replaced": 1}


def _updates():
    return build_updates(_doc179(), _brokerage_row())


def test_apply_updates_happy_path_writes_both():
    rdb = _FakeRdb()
    apply_updates(rdb, conn=object(), updates=_updates(), instance_row={"id": "alpaca-main"})
    tables = [t for t, _ in rdb.writes]
    assert tables == ["Strategies", "Instances"]
    assert rdb.writes[1][1] == HALT_CLEAR


def test_apply_updates_skips_instance_write_when_row_missing():
    rdb = _FakeRdb()
    apply_updates(rdb, conn=object(), updates=_updates(), instance_row=None)
    assert [t for t, _ in rdb.writes] == ["Strategies"]


def test_apply_updates_scrubs_secret_from_strategies_failure():
    rdb = _FakeRdb(fail_tables={"Strategies"})
    with pytest.raises(RuntimeError) as excinfo:
        apply_updates(rdb, conn=object(), updates=_updates(), instance_row={"id": "alpaca-main"})
    exc = excinfo.value
    msg = str(exc)
    # The scrubbed error must NEVER contain the raw key/secret values...
    assert WORKING_KEY not in msg
    assert WORKING_SECRET not in msg
    assert "AKFAKE" not in msg
    assert "SKFAKE" not in msg
    # ...but should name the original class and give fingerprint-only context.
    assert "FakeReqlError" in msg
    assert "fp=" in msg
    # Exception chain severed (`from None`) so the term-tree message can't
    # render in the traceback either.
    assert exc.__cause__ is None
    assert exc.__suppress_context__ is True


def test_apply_updates_scrubs_instances_failure():
    rdb = _FakeRdb(fail_tables={"Instances"})
    with pytest.raises(RuntimeError) as excinfo:
        apply_updates(rdb, conn=object(), updates=_updates(), instance_row={"id": "alpaca-main"})
    exc = excinfo.value
    msg = str(exc)
    assert "AKFAKE" not in msg
    assert "SKFAKE" not in msg
    assert "FakeReqlError" in msg
    assert "already succeeded" in msg  # tells operator the key write landed
    assert exc.__cause__ is None
    assert exc.__suppress_context__ is True
    # The Strategies write did land before the failure.
    assert [t for t, _ in rdb.writes] == ["Strategies"]
