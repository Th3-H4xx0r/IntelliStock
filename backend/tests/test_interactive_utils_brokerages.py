"""Credential writers fail closed when encryption is unavailable."""
from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock

import pytest

_backend = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _backend not in sys.path:
    sys.path.insert(0, _backend)


class _Response:
    ok = True
    status_code = 200

    @staticmethod
    def json():
        return {"account_number": "acct"}


def _fake_rethink(monkeypatch, iu):
    """Cage the WRITE. These tests assert the write is REFUSED, so the store's
    insert must fail loudly if the refusal ever regresses."""
    writes = []

    def _refuse(*a, **k):
        writes.append((a, k))
        return {"inserted": 1, "generated_keys": ["new-id"]}

    monkeypatch.setattr(iu.store, "insert", _refuse)
    monkeypatch.setattr(iu, "_ensure_brokerage_accounts_table", lambda conn: None)
    return writes


def test_link_alpaca_rejects_write_when_credential_key_is_missing(monkeypatch):
    """Removing encryption must turn successful broker validation into a failed save."""
    import interactive_utils as iu

    monkeypatch.delenv("INTELLISTOCK_CRED_KEY", raising=False)
    _fake_rethink(monkeypatch, iu)
    monkeypatch.setattr("requests.get", lambda *_args, **_kwargs: _Response())
    monkeypatch.setattr(iu, "_alpaca_validate_data_access", lambda *_args: {"ok": True})

    with pytest.raises(RuntimeError, match="INTELLISTOCK_CRED_KEY"):
        iu.action_link_alpaca(None, "paper", "alpaca-key", "alpaca-secret")


def test_model_writers_reject_plaintext_api_key_when_credential_key_is_missing(monkeypatch):
    """Model create and edit cannot store an API key without Fernet."""
    import interactive_utils as iu

    monkeypatch.delenv("INTELLISTOCK_CRED_KEY", raising=False)
    monkeypatch.setattr(iu.store, "insert",
                        lambda *a, **k: {"inserted": 1,
                                         "generated_keys": ["model-1"]})
    monkeypatch.setattr(iu.store, "get", lambda *a, **k: {
        "id": "model-1", "provider": "openai", "model": "gpt-4o",
        "api_key": "old-key"})
    monkeypatch.setattr(iu, "_ensure_models_table", lambda conn: None)

    with pytest.raises(RuntimeError, match="INTELLISTOCK_CRED_KEY"):
        iu.action_create_model(None, "model", "openai", "gpt-4o", api_key="new-key")
    with pytest.raises(RuntimeError, match="INTELLISTOCK_CRED_KEY"):
        iu.action_edit_model(None, "model-1", api_key="new-key")
