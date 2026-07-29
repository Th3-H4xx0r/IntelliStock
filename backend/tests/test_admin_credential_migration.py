"""Encrypting legacy plaintext credentials in place.

Rows written before the encrypted-at-rest boundary still hold plaintext, and
strict decryption correctly refuses them. That is what 500'd the dashboard and
what stops a backtest resolving its LLM key.

Editing a row through the UI only re-encrypts the fields actually re-entered,
so a key nobody retyped stays legacy forever -- which is why "just re-save it"
did not clear Models.api_key. This migration closes that gap directly.
"""
import os
import sys

import pytest

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from credential_audit import SECRET_FIELDS_BY_TABLE, scan_secret_fields  # noqa: E402
from scripts.migrate_encrypted_credentials import (  # noqa: E402
    build_encrypted_patch,
    verify_patch,
)
from secret_store import decrypt_required, is_encrypted  # noqa: E402


@pytest.fixture(autouse=True)
def _cred_key(monkeypatch):
    """Encryption needs a key; the deployment supplies its own via env."""
    import base64
    monkeypatch.setenv("INTELLISTOCK_CRED_KEY",
                       base64.urlsafe_b64encode(b"0" * 32).decode())
    import secret_store
    secret_store._fernet.cache_clear() if hasattr(
        secret_store._fernet, "cache_clear") else None
    yield


def test_models_api_key_is_an_audited_secret():
    assert SECRET_FIELDS_BY_TABLE["Models"] == ("api_key",)


def test_plaintext_is_encrypted_and_round_trips():
    row = {"id": "m1", "api_key": "sk-legacy-plaintext"}
    patch = build_encrypted_patch(row, fields=("api_key",))
    assert is_encrypted(patch["api_key"]), "must be stored encrypted"
    assert patch["api_key"] != row["api_key"]
    verify_patch(patch, fields=("api_key",))
    assert decrypt_required(patch["api_key"], field="Models.api_key") == "sk-legacy-plaintext"


def test_already_encrypted_values_are_left_untouched():
    """Re-running the migration must be safe — no double encryption."""
    once = build_encrypted_patch({"id": "m1", "api_key": "sk-x"}, fields=("api_key",))
    twice = build_encrypted_patch({"id": "m1", **once}, fields=("api_key",))
    assert twice["api_key"] == once["api_key"]
    assert decrypt_required(twice["api_key"], field="Models.api_key") == "sk-x"


def test_empty_credentials_are_skipped():
    for value in ("", None):
        assert build_encrypted_patch({"id": "m1", "api_key": value},
                                     fields=("api_key",)) == {}


def test_verify_rejects_a_patch_that_cannot_decrypt():
    """The migration verifies before it writes, so a bad patch never lands."""
    with pytest.raises(Exception):
        verify_patch({"api_key": "not-actually-encrypted"}, fields=("api_key",))


def test_scan_reports_plaintext_without_exposing_values():
    rows = {"Models": [{"id": "m1", "api_key": "sk-legacy-plaintext"}]}
    findings = scan_secret_fields(rows)
    assert findings and any(not f.encrypted for f in findings)
    blob = repr(findings)
    assert "sk-legacy-plaintext" not in blob, "a scan must never carry the secret"


def test_endpoint_is_dry_run_by_default():
    src = open(os.path.join(_backend, "api", "main.py")).read()
    assert "/admin/credentials/migrate" in src
    assert "apply: bool = False" in src, "must not mutate unless explicitly asked"
    assert "verify_patch(patch, fields=fields)" in src, "verify before writing"
