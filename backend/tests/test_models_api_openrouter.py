"""Tests for OpenRouter-specific Models CRUD + discovery in backend/api/main.py
and backend/interactive_utils.py. Mirrors test_models_api_bedrock.py."""
import importlib

import pytest


def _import_body(name: str):
    mod = importlib.import_module("api.main")
    return getattr(mod, name)


# ─────────────────── body classes accept openrouter fields ──────────────────
def test_create_model_body_accepts_openrouter_fields():
    CreateModelBody = _import_body("CreateModelBody")
    b = CreateModelBody(
        name="OR Claude", provider="openrouter",
        model="anthropic/claude-3.5-sonnet", api_key="sk-or-k",
        openrouter_base_url="https://openrouter.ai/api/v1",
        openrouter_referer="https://intellistock.app",
        openrouter_title="IntelliStock")
    assert b.openrouter_base_url == "https://openrouter.ai/api/v1"
    assert b.openrouter_referer == "https://intellistock.app"
    assert b.openrouter_title == "IntelliStock"


def test_edit_model_body_accepts_openrouter_fields():
    EditModelBody = _import_body("EditModelBody")
    b = EditModelBody(openrouter_base_url="https://proxy/api/v1", openrouter_title="X")
    assert b.openrouter_base_url == "https://proxy/api/v1"
    assert b.openrouter_title == "X"


def test_llm_config_test_body_accepts_openrouter_fields():
    LlmConfigTestBody = _import_body("LlmConfigTestBody")
    b = LlmConfigTestBody(provider="openrouter", model="anthropic/claude-3.5-sonnet",
                          api_key="k", openrouter_referer="https://x")
    assert b.openrouter_referer == "https://x"


# ─────────────── _build_llm_test_provider_config branch ────────────────────
def test_build_llm_test_provider_config_openrouter():
    from api.main import _build_llm_test_provider_config
    LlmConfigTestBody = _import_body("LlmConfigTestBody")
    body = LlmConfigTestBody(provider="openrouter", model="anthropic/claude-3.5-sonnet",
                             api_key="k", openrouter_referer="https://intellistock.app",
                             openrouter_title="IntelliStock", reasoning_effort="High")
    cfg = _build_llm_test_provider_config(body)
    assert cfg["openrouter_base_url"] == "https://openrouter.ai/api/v1"
    assert cfg["openrouter_referer"] == "https://intellistock.app"
    assert cfg["openrouter_title"] == "IntelliStock"
    assert cfg["reasoning_effort"] == "high"


def test_build_llm_test_provider_config_openrouter_defaults_base():
    from api.main import _build_llm_test_provider_config
    LlmConfigTestBody = _import_body("LlmConfigTestBody")
    body = LlmConfigTestBody(provider="openrouter", model="openai/gpt-4o-mini", api_key="k")
    cfg = _build_llm_test_provider_config(body)
    assert cfg["openrouter_base_url"] == "https://openrouter.ai/api/v1"
    assert "openrouter_referer" not in cfg
    assert "openrouter_title" not in cfg


# ─────────────── POST /openrouter/list-models endpoint ─────────────────────
@pytest.fixture
def app_client():
    from fastapi.testclient import TestClient
    from api import main as api_main
    app = api_main.app
    app.dependency_overrides[api_main.get_current_user] = lambda: {
        "id": "test-user", "username": "test", "role": "user"}
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_openrouter_list_models_happy_path(app_client):
    from unittest.mock import patch, Mock
    fake = [{"id": "anthropic/claude-3.5-sonnet", "name": "Claude 3.5 Sonnet",
             "context_length": 200000, "pricing": {"prompt": "0.000003", "completion": "0.000015"}}]
    with patch("api.main.openrouter_client.list_models", new=Mock(return_value=fake)):
        resp = app_client.post("/openrouter/list-models", json={})
    assert resp.status_code == 200, resp.text
    assert resp.json()["models"] == fake
    assert resp.json()["error"] is None


def test_openrouter_list_models_empty_sets_error(app_client):
    from unittest.mock import patch, Mock
    with patch("api.main.openrouter_client.list_models", new=Mock(return_value=[])):
        resp = app_client.post("/openrouter/list-models", json={})
    assert resp.status_code == 200
    assert resp.json()["models"] == []
    assert resp.json()["error"]


def test_openrouter_list_models_passes_base_url(app_client):
    from unittest.mock import patch
    captured = {}

    def _fake(base_url, **kw):
        captured["base_url"] = base_url
        return [{"id": "x/y", "name": "Y", "context_length": None, "pricing": {}}]

    with patch("api.main.openrouter_client.list_models", new=_fake):
        resp = app_client.post("/openrouter/list-models", json={"base_url": "https://proxy/api/v1"})
    assert resp.status_code == 200
    assert captured["base_url"] == "https://proxy/api/v1"


# ─────────────── interactive_utils action round-trips ──────────────────────
def test_action_create_model_persists_openrouter_fields(monkeypatch):
    import interactive_utils as iu
    from unittest.mock import MagicMock
    from cryptography.fernet import Fernet
    monkeypatch.setenv("INTELLISTOCK_CRED_KEY", Fernet.generate_key().decode())
    fake_r = MagicMock()
    fake_r.db.return_value.table.return_value.insert.return_value.run.return_value = {"generated_keys": ["new-id"]}
    monkeypatch.setattr(iu, "r", fake_r)
    monkeypatch.setattr(iu, "_ensure_models_table", lambda conn: None)
    iu.action_create_model(
        None, "or", "openrouter", "anthropic/claude-3.5-sonnet",
        api_key="key", openrouter_base_url="https://openrouter.ai/api/v1",
        openrouter_referer="https://intellistock.app", openrouter_title="IntelliStock")
    doc = fake_r.db.return_value.table.return_value.insert.call_args.args[0]
    assert doc["provider"] == "openrouter"
    assert doc["openrouter_base_url"] == "https://openrouter.ai/api/v1"
    assert doc["openrouter_referer"] == "https://intellistock.app"
    assert doc["openrouter_title"] == "IntelliStock"


def test_action_edit_model_updates_openrouter_fields(monkeypatch):
    import interactive_utils as iu
    from unittest.mock import MagicMock
    existing = {"id": "m1", "provider": "openrouter",
                "model": "anthropic/claude-3.5-sonnet",
                "openrouter_base_url": "https://openrouter.ai/api/v1"}
    fake_r = MagicMock()
    fake_r.db.return_value.table.return_value.get.return_value.run.return_value = existing
    monkeypatch.setattr(iu, "r", fake_r)
    monkeypatch.setattr(iu, "_ensure_models_table", lambda conn: None)
    iu.action_edit_model(None, "m1", openrouter_referer="https://new.app",
                         openrouter_title="New")
    update = fake_r.db.return_value.table.return_value.get.return_value.update.call_args.args[0]
    assert update["openrouter_referer"] == "https://new.app"
    assert update["openrouter_title"] == "New"
