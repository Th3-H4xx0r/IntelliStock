"""Credential inventory and migration primitives never expose secret values."""
from __future__ import annotations

import hashlib
import os
import sys

import pytest

_backend = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _backend not in sys.path:
    sys.path.insert(0, _backend)


def test_inventory_never_returns_secret_values():
    """Replacing the stored credential with a canary must never expose it."""
    from credential_audit import scan_secret_fields

    findings = scan_secret_fields({
        "BrokerageAccounts": [{"id": "acct-1", "alpaca_secret": "CANARY"}],
    })

    assert "CANARY" not in repr(findings)
    assert findings[0].field == "alpaca_secret"
    assert findings[0].encrypted is False
    assert findings[0].row_id_hash == hashlib.sha256(b"acct-1").hexdigest()


def test_inventory_only_scans_schema_allowed_secret_fields():
    """An arbitrary string field must not become inventory output."""
    from credential_audit import scan_secret_fields

    findings = scan_secret_fields({
        "BrokerageAccounts": [{"id": "acct-2", "notes": "CANARY", "alpaca_key": "fernet:token"}],
        "Unknown": [{"id": "row-1", "api_key": "CANARY"}],
    })

    assert [(finding.table, finding.field, finding.encrypted) for finding in findings] == [
        ("BrokerageAccounts", "alpaca_key", True),
    ]


def test_encrypted_patch_copy_verify_switch_round_trips(with_key):
    """Migration patches encrypt plaintext but preserve existing ciphertext."""
    from scripts.migrate_encrypted_credentials import build_encrypted_patch, verify_patch
    from secret_store import encrypt

    existing = encrypt("already-encrypted")
    patch = build_encrypted_patch(
        {"alpaca_key": "plain-key", "alpaca_secret": existing, "notes": "CANARY"},
        fields=("alpaca_key", "alpaca_secret"),
    )

    assert patch["alpaca_key"].startswith("fernet:")
    assert patch["alpaca_secret"] == existing
    assert "notes" not in patch
    verify_patch(patch, fields=("alpaca_key", "alpaca_secret"))


@pytest.fixture
def with_key(monkeypatch):
    pytest.importorskip("cryptography")
    from cryptography.fernet import Fernet

    monkeypatch.setenv("INTELLISTOCK_CRED_KEY", Fernet.generate_key().decode())
