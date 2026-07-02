"""Unit tests for the doc-179 hygiene script's pure logic.

All fixtures use FAKE keys (``AKFAKE...``/``SKFAKE...``) — no real secrets appear
here, and nothing in this file touches a real database. Only the pure
``build_updates`` / ``extract_broker_creds`` functions are exercised.
"""

import copy
import os
import sys

import pytest

_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

from scripts.fix_doc179_hygiene import (  # noqa: E402
    build_updates,
    extract_broker_creds,
    HALT_CLEAR,
)


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
    """The 'Alpaca Live' BrokerageAccounts row holding the WORKING key."""
    return {
        "id": "08f683af-76f6-404d-872c-37baa45711ee",
        "account_name": "Alpaca Live",
        "key": "AKFAKELIVEKEY1111111",
        "secret": "SKFAKELIVESECRET11111111111111111111",
    }


# --- extract_broker_creds ---------------------------------------------------


def test_extract_creds_key_secret_fields():
    k, s, kf, sf = extract_broker_creds(_brokerage_row())
    assert k == "AKFAKELIVEKEY1111111"
    assert s == "SKFAKELIVESECRET11111111111111111111"
    assert kf == "key"
    assert sf == "secret"


def test_extract_creds_alternate_field_names():
    row = {"id": "x", "alpaca_key": "AKFAKEALT1", "alpaca_secret": "SKFAKEALT2"}
    k, s, kf, sf = extract_broker_creds(row)
    assert k == "AKFAKEALT1"
    assert s == "SKFAKEALT2"
    assert kf == "alpaca_key"
    assert sf == "alpaca_secret"


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
    assert cfg0["alpaca_key"] == "AKFAKELIVEKEY1111111"
    assert cfg0["alpaca_secret"] == "SKFAKELIVESECRET11111111111111111111"
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
    assert meta["broker_key_field"] == "key"
    assert meta["broker_secret_field"] == "secret"
    # no raw secret values leak into meta
    blob = repr(meta)
    assert "AKFAKE" not in blob
    assert "SKFAKE" not in blob


def test_build_updates_no_change_when_already_matching():
    doc = _doc179()
    row = _brokerage_row()
    # make doc already carry the working key
    doc["strategies"][0]["config"]["alpaca_key"] = row["key"]
    doc["strategies"][0]["config"]["alpaca_secret"] = row["secret"]
    updates = build_updates(doc, row)
    assert updates["meta"]["key_changed"] is False
    assert updates["meta"]["old_key_fp"] == updates["meta"]["new_key_fp"]
