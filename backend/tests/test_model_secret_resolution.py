"""Encrypted Models.api_key values are decrypted at every runtime reader."""
from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock

import pytest

_backend = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _backend not in sys.path:
    sys.path.insert(0, _backend)


@pytest.fixture
def encrypted_key(monkeypatch):
    pytest.importorskip("cryptography")
    from cryptography.fernet import Fernet

    monkeypatch.setenv("INTELLISTOCK_CRED_KEY", Fernet.generate_key().decode())
    from secret_store import encrypt
    return encrypt("model-secret")


def test_model_resolver_injects_plaintext_from_encrypted_model_key(monkeypatch, encrypted_key):
    """Changing a stored cipher to opaque text must not reach strategy config."""
    import model_resolver

    monkeypatch.setattr(model_resolver, "_get_model_from_cache_or_db", lambda *_args: {
        "provider": "openai", "model": "gpt-4o", "api_key": encrypted_key,
    })

    resolved = model_resolver.resolve_model_refs_in_config(None, {"llm_model_id": "model-1"})

    assert resolved["llm_api_key"] == "model-secret"


def test_action_get_model_raw_decrypts_api_key(monkeypatch, encrypted_key):
    """The internal LLM-test reader receives the usable key, not its cipher."""
    import interactive_utils as iu

    fake_r = MagicMock()
    fake_r.db.return_value.table.return_value.get.return_value.run.return_value = {
        "id": "model-1", "api_key": encrypted_key,
    }
    monkeypatch.setattr(iu, "r", fake_r)
    monkeypatch.setattr(iu, "_ensure_models_table", lambda conn: None)

    assert iu.action_get_model_raw(None, "model-1")["api_key"] == "model-secret"


def test_chatbot_model_resolution_decrypts_api_key(monkeypatch, encrypted_key):
    """Chatbot provider calls must receive the decrypted configured key."""
    from chatbot import orchestration

    monkeypatch.setattr(orchestration, "_fetch_model_doc", lambda *_args: {
        "provider": "openai", "model": "gpt-4o", "api_key": encrypted_key,
    })

    assert orchestration._resolve_model(None, "model-1")["api_key"] == "model-secret"


def test_kalshi_analyst_decrypts_model_api_key(monkeypatch, encrypted_key):
    """The analyst dispatch boundary must see the usable key, never Fernet text."""
    from kalshi.intelligence import analyst_panel

    captured = {}
    fake_llm_utils = type(sys)("llm_utils")
    fake_llm_utils.resolve_api_key_for_provider = lambda *_args: ""

    def call(provider, api_key, *_args, **_kwargs):
        captured["provider"] = provider
        captured["api_key"] = api_key
        return {"adjustments": {}}

    fake_llm_utils.call_structured_llm_by_provider = call
    monkeypatch.setitem(sys.modules, "llm_utils", fake_llm_utils)

    llm_call = analyst_panel.make_llm_call({
        "provider": "openai", "model": "gpt-4o", "api_key": encrypted_key,
    })
    assert llm_call is not None
    llm_call("test prompt")
    assert captured == {"provider": "openai", "api_key": "model-secret"}
