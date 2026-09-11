"""GET /brokerages must never hand back a credential — including Kalshi's.

_mask_brokerage_doc masked an allowlist of four field names. kalshi_private_key
was not one of them, so every listing returned the Fernet ciphertext of an RSA
private key that signs live Kalshi orders, to any authenticated caller. The
allowlist is the bug: a field added later is exposed by default. Masking is now
decided by the field's NAME, so a new *_key / *_secret / *_token / *_private_key
is masked the day it is written.
"""
from __future__ import annotations

import os
import sys

import pytest

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)


PEM = (
    "-----BEGIN RSA PRIVATE KEY-----\n"
    "MIIEowIBAAKCAQEACANARY_KALSHI_SIGNING_KEY_MATERIAL\n"
    "-----END RSA PRIVATE KEY-----\n"
)


def test_the_kalshi_private_key_is_masked():
    from interactive_utils import _mask_brokerage_doc

    out = _mask_brokerage_doc({
        "id": "b1", "brokerage_type": "kalshi",
        "kalshi_key_id": "abc-123",
        "kalshi_private_key": PEM,
        "kalshi_environment": "demo",
    })
    assert out["kalshi_private_key"] == "****"
    assert "CANARY" not in repr(out)


def test_the_kalshi_private_key_is_masked_when_it_is_fernet_ciphertext(monkeypatch):
    """The stored form is ciphertext; returning THAT is the shipped bug."""
    import interactive_utils as iu

    out = iu._mask_brokerage_doc({
        "id": "b1", "kalshi_private_key": "fernet:CANARY_CIPHERTEXT",
    })
    assert "CANARY" not in repr(out)
    assert "fernet:" not in repr(out)


def test_an_unknown_secret_shaped_field_is_masked_by_name():
    from interactive_utils import _mask_brokerage_doc

    out = _mask_brokerage_doc({
        "id": "b1",
        "some_future_secret": "CANARY_A",
        "some_future_token": "CANARY_B",
        "some_future_private_key": "CANARY_C",
        "some_future_key": "CANARY_D_LONG_ENOUGH",
    })
    assert "CANARY_A" not in repr(out)
    assert "CANARY_B" not in repr(out)
    assert "CANARY_C" not in repr(out)
    # *_key keeps the first/last four so an operator can identify the account.
    assert out["some_future_key"] == "CANA****OUGH"


def test_non_secret_fields_are_untouched():
    from interactive_utils import _mask_brokerage_doc

    doc = {
        "id": "b1", "account_name": "live", "brokerage_type": "alpaca",
        "alpaca_paper": False, "kalshi_key_id": "abc-123",
        "alpaca_data_feed": "iex", "created_at": "2026-09-11T00:00:00Z",
    }
    assert _mask_brokerage_doc(doc) == doc


def test_the_existing_alpaca_and_binance_masks_are_unchanged():
    from interactive_utils import _mask_brokerage_doc, _looks_masked

    out = _mask_brokerage_doc({
        "id": "b1",
        "alpaca_key": "PKABCDEFGHIJKLMNOP",
        "alpaca_secret": "CANARY_SECRET",
        "binanceus_key": "BUABCDEFGHIJKLMNOP",
        "binanceus_secret": "CANARY_SECRET_2",
    })
    assert out["alpaca_key"] == "PKAB****MNOP"
    assert out["alpaca_secret"] == "****"
    assert out["binanceus_key"] == "BUAB****MNOP"
    assert out["binanceus_secret"] == "****"
    # The edit form echoes these back; they must still read as "keep existing".
    assert _looks_masked(out["alpaca_key"])
    assert _looks_masked(out["binanceus_secret"])


def test_a_short_key_masks_completely():
    from interactive_utils import _mask_brokerage_doc

    assert _mask_brokerage_doc({"alpaca_key": "SHORT"})["alpaca_key"] == "****"


def test_list_brokerages_never_returns_the_ciphertext(monkeypatch):
    """End to end through the action GET /brokerages calls."""
    import interactive_utils as iu

    monkeypatch.setattr(iu, "_ensure_brokerage_accounts_table", lambda conn: None)
    monkeypatch.setattr(iu.store, "run", lambda *_a, **_k: [
        {"id": "b1", "brokerage_type": "kalshi",
         "kalshi_private_key": "fernet:CANARY_CIPHERTEXT",
         "kalshi_key_id": "abc-123"},
        {"id": "b2", "brokerage_type": "alpaca",
         "alpaca_key": "PKABCDEFGHIJKLMNOP", "alpaca_secret": "CANARY_SECRET"},
    ])
    out = iu.action_list_brokerages(None)
    assert "CANARY" not in repr(out)
    assert "fernet:" not in repr(out)
    assert out["accounts"][0]["kalshi_private_key"] == "****"


# --- the audit inventory --------------------------------------------------


@pytest.mark.parametrize("field", [
    "alpaca_key", "alpaca_secret",
    "binanceus_key", "binanceus_secret",
    "kalshi_private_key",
])
def test_the_audit_covers_every_brokerage_credential(field):
    from credential_audit import scan_secret_fields

    findings = scan_secret_fields({
        "BrokerageAccounts": [{"id": "acct-1", field: "CANARY"}],
    })
    assert [f.field for f in findings] == [field]
    assert "CANARY" not in repr(findings)


def test_the_audit_still_ignores_non_credential_fields():
    from credential_audit import scan_secret_fields

    findings = scan_secret_fields({
        "BrokerageAccounts": [{"id": "acct-1", "account_name": "CANARY",
                               "kalshi_key_id": "CANARY"}],
    })
    assert findings == ()
