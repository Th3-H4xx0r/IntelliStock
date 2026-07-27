"""Credential writers fail closed when encryption is unavailable."""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
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
    fake_r = MagicMock()
    fake_r.db.return_value.table.return_value.insert.return_value.run.return_value = {"generated_keys": ["new-id"]}
    fake_r.db.return_value.table.return_value.get.return_value.run.return_value = {
        "id": "existing",
        "brokerage_type": "robinhood",
        "robinhood_access_token": "fernet:old-access",
        "robinhood_refresh_token": "fernet:old-refresh",
        "robinhood_device_token": "fernet:old-device",
    }
    monkeypatch.setattr(iu, "r", fake_r)
    monkeypatch.setattr(iu, "_ensure_brokerage_accounts_table", lambda conn: None)
    return fake_r


def _install_robinhood(monkeypatch):
    module = type(sys)("robinhood_engine")

    class State:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)
            self.account_number = "acct"
            self.account_url = "https://example.invalid/accounts/acct"

    class Client:
        def __init__(self, *, state, **_kwargs):
            self.state = state

        def select_default_account(self, **_kwargs):
            return {"account_number": "acct", "url": "https://example.invalid/accounts/acct"}

        def refresh(self):
            return SimpleNamespace(
                access_token="refreshed-access",
                refresh_token="refreshed-refresh",
                obtained_at_epoch=1,
                expires_in=3600,
            )

    module.RobinhoodSessionState = State
    module.RobinhoodClient = Client
    module.RobinhoodAPIError = RuntimeError
    monkeypatch.setitem(sys.modules, "robinhood_engine", module)


def test_link_alpaca_rejects_write_when_credential_key_is_missing(monkeypatch):
    """Removing encryption must turn successful broker validation into a failed save."""
    import interactive_utils as iu

    monkeypatch.delenv("INTELLISTOCK_CRED_KEY", raising=False)
    _fake_rethink(monkeypatch, iu)
    monkeypatch.setattr("requests.get", lambda *_args, **_kwargs: _Response())
    monkeypatch.setattr(iu, "_alpaca_validate_data_access", lambda *_args: {"ok": True})

    with pytest.raises(RuntimeError, match="INTELLISTOCK_CRED_KEY"):
        iu.action_link_alpaca(None, "paper", "alpaca-key", "alpaca-secret")


def test_link_robinhood_rejects_write_when_credential_key_is_missing(monkeypatch):
    """Robinhood account linking must never substitute plaintext tokens."""
    import interactive_utils as iu

    monkeypatch.delenv("INTELLISTOCK_CRED_KEY", raising=False)
    _fake_rethink(monkeypatch, iu)
    _install_robinhood(monkeypatch)

    with pytest.raises(RuntimeError, match="INTELLISTOCK_CRED_KEY"):
        iu.action_link_robinhood_tokens(None, "rh", "access", "refresh", device_token="device")


def test_update_robinhood_rejects_write_when_credential_key_is_missing(monkeypatch):
    """Updating an existing RH token must not silently persist plaintext."""
    import interactive_utils as iu

    monkeypatch.delenv("INTELLISTOCK_CRED_KEY", raising=False)
    _fake_rethink(monkeypatch, iu)
    _install_robinhood(monkeypatch)

    with pytest.raises(RuntimeError, match="INTELLISTOCK_CRED_KEY"):
        iu.action_update_brokerage(None, "existing", access_token="new-access", refresh_token="new-refresh")


def test_refresh_robinhood_rejects_write_when_credential_key_is_missing(monkeypatch):
    """A refresh result is not written unless it can be encrypted."""
    import interactive_utils as iu

    monkeypatch.delenv("INTELLISTOCK_CRED_KEY", raising=False)
    _fake_rethink(monkeypatch, iu)
    _install_robinhood(monkeypatch)

    with pytest.raises(RuntimeError, match="INTELLISTOCK_CRED_KEY"):
        iu.action_refresh_robinhood(None, "existing")


def test_model_writers_reject_plaintext_api_key_when_credential_key_is_missing(monkeypatch):
    """Model create and edit cannot store an API key without Fernet."""
    import interactive_utils as iu

    monkeypatch.delenv("INTELLISTOCK_CRED_KEY", raising=False)
    fake_r = MagicMock()
    fake_r.db.return_value.table.return_value.insert.return_value.run.return_value = {"generated_keys": ["model-1"]}
    fake_r.db.return_value.table.return_value.get.return_value.run.return_value = {
        "id": "model-1", "provider": "openai", "model": "gpt-4o", "api_key": "old-key"}
    monkeypatch.setattr(iu, "r", fake_r)
    monkeypatch.setattr(iu, "_ensure_models_table", lambda conn: None)

    with pytest.raises(RuntimeError, match="INTELLISTOCK_CRED_KEY"):
        iu.action_create_model(None, "model", "openai", "gpt-4o", api_key="new-key")
    with pytest.raises(RuntimeError, match="INTELLISTOCK_CRED_KEY"):
        iu.action_edit_model(None, "model-1", api_key="new-key")
