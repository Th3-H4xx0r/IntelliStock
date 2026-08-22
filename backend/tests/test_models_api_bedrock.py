"""Tests for Bedrock-specific Models CRUD + discovery in backend/api/main.py
and backend/interactive_utils.py. Mirrors test_models_api_ollama.py."""
import importlib

import pytest


def _import_body(name: str):
    """Import a Pydantic body class from api.main without app-boot side effects."""
    mod = importlib.import_module("api.main")
    return getattr(mod, name)


# ─────────────────── CreateModelBody / EditModelBody / TestBody ─────────────


def test_create_model_body_accepts_bedrock_fields():
    CreateModelBody = _import_body("CreateModelBody")
    b = CreateModelBody(
        name="Bedrock Claude", provider="bedrock",
        model="us.anthropic.claude-3-5-sonnet-20241022-v2:0",
        api_key="bk-key", bedrock_region="us-east-1", bedrock_reasoning="medium")
    assert b.bedrock_region == "us-east-1"
    assert b.bedrock_reasoning == "medium"


def test_edit_model_body_accepts_bedrock_fields():
    EditModelBody = _import_body("EditModelBody")
    b = EditModelBody(bedrock_region="us-west-2", bedrock_reasoning="high")
    assert b.bedrock_region == "us-west-2"
    assert b.bedrock_reasoning == "high"


def test_llm_config_test_body_accepts_bedrock_fields():
    LlmConfigTestBody = _import_body("LlmConfigTestBody")
    b = LlmConfigTestBody(provider="bedrock", model="m", api_key="k",
                          bedrock_region="us-east-1", bedrock_reasoning="low")
    assert b.bedrock_region == "us-east-1"


def test_create_edit_model_body_accept_cache_family():
    CreateModelBody = _import_body("CreateModelBody")
    EditModelBody = _import_body("EditModelBody")
    c = CreateModelBody(name="x", provider="bedrock", model="openai.gpt-oss-120b-1:0",
                        api_key="k", model_cache_family="gpt-oss-120b")
    assert c.model_cache_family == "gpt-oss-120b"
    e = EditModelBody(model_cache_family="gpt-oss-120b")
    assert e.model_cache_family == "gpt-oss-120b"


def test_action_create_model_persists_cache_family(monkeypatch):
    import interactive_utils as iu
    from unittest.mock import MagicMock
    from cryptography.fernet import Fernet
    monkeypatch.setenv("INTELLISTOCK_CRED_KEY", Fernet.generate_key().decode())
    fake_r = MagicMock()
    monkeypatch.setattr(iu.store, "insert", fake_r.insert)
    fake_r.insert.return_value = {"inserted": 1, "generated_keys": ["new-id"]}
    monkeypatch.setattr(iu, "_ensure_models_table", lambda conn: None)
    iu.action_create_model(None, "x", "bedrock", "openai.gpt-oss-120b-1:0",
                           api_key="k", model_cache_family="GPT-OSS-120b")
    doc = fake_r.insert.call_args.args[1]
    assert doc["model_cache_family"] == "gpt-oss-120b"  # normalized lowercase


def test_llm_config_test_output_tolerates_terse_models():
    """Terse models (e.g. Bedrock GPT-OSS) return only {"ok": true}; the probe
    must not require provider/model to be echoed back."""
    LlmConfigTestOutput = _import_body("LlmConfigTestOutput")
    out = LlmConfigTestOutput(ok=True)
    assert out.ok is True and out.provider is None and out.model is None
    # Extra fields a model might add are ignored (Pydantic default).
    out2 = LlmConfigTestOutput(**{"ok": True, "reason": "valid"})
    assert out2.ok is True


def test_bedrock_region_length_capped():
    CreateModelBody = _import_body("CreateModelBody")
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        CreateModelBody(name="x", provider="bedrock", model="m", bedrock_region="x" * 40)


# ─────────────── _build_llm_test_provider_config branch ────────────────────


def test_build_llm_test_provider_config_bedrock():
    from api.main import _build_llm_test_provider_config
    LlmConfigTestBody = _import_body("LlmConfigTestBody")
    body = LlmConfigTestBody(provider="bedrock", model="m", api_key="k",
                             bedrock_region="eu-central-1", bedrock_reasoning="High")
    cfg = _build_llm_test_provider_config(body)
    assert cfg["bedrock_region"] == "eu-central-1"
    assert cfg["bedrock_reasoning"] == "high"


def test_build_llm_test_provider_config_bedrock_omits_empty():
    from api.main import _build_llm_test_provider_config
    LlmConfigTestBody = _import_body("LlmConfigTestBody")
    body = LlmConfigTestBody(provider="bedrock", model="m", api_key="k", bedrock_region="us-east-1")
    cfg = _build_llm_test_provider_config(body)
    assert cfg["bedrock_region"] == "us-east-1"
    assert "bedrock_reasoning" not in cfg


# ─────────────── POST /bedrock/list-models endpoint ────────────────────────


@pytest.fixture
def app_client():
    from fastapi.testclient import TestClient
    from api import main as api_main
    app = api_main.app
    app.dependency_overrides[api_main.get_current_user] = lambda: {
        "id": "test-user", "username": "test", "role": "user"}
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_bedrock_list_models_happy_path(app_client):
    from unittest.mock import patch, Mock
    fake = [{"id": "anthropic.claude-3-5-sonnet-20241022-v2:0", "name": "Claude 3.5 Sonnet v2",
             "provider_name": "Anthropic", "kind": "foundation", "supports_tools": True, "modalities": ["TEXT"]}]
    with patch("api.main.bedrock_client.list_models", new=Mock(return_value=fake)):
        resp = app_client.post("/bedrock/list-models", json={"api_key": "k", "region": "us-east-1"})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"models": fake}


def test_bedrock_list_models_auth_error_401(app_client):
    from unittest.mock import patch, Mock
    from bedrock_client import BedrockAuthError
    with patch("api.main.bedrock_client.list_models", new=Mock(side_effect=BedrockAuthError("denied"))):
        resp = app_client.post("/bedrock/list-models", json={"api_key": "k", "region": "us-east-1"})
    assert resp.status_code == 401


def test_bedrock_list_models_connection_error_502(app_client):
    from unittest.mock import patch, Mock
    from bedrock_client import BedrockConnectionError
    with patch("api.main.bedrock_client.list_models", new=Mock(side_effect=BedrockConnectionError("no net"))):
        resp = app_client.post("/bedrock/list-models", json={"api_key": "k", "region": "us-east-1"})
    assert resp.status_code == 502


def test_bedrock_list_models_requires_region(app_client):
    resp = app_client.post("/bedrock/list-models", json={"api_key": "k"})
    assert resp.status_code in (400, 422)


def test_bedrock_list_models_passes_args(app_client):
    from unittest.mock import patch
    captured = {}

    def _fake(api_key, region, **kw):
        captured["api_key"] = api_key
        captured["region"] = region
        return []

    with patch("api.main.bedrock_client.list_models", new=_fake):
        resp = app_client.post("/bedrock/list-models", json={"api_key": "secret", "region": "eu-west-1"})
    assert resp.status_code == 200
    assert captured == {"api_key": "secret", "region": "eu-west-1"}


# ─────────────── interactive_utils action round-trips ──────────────────────


def test_action_create_model_persists_bedrock_fields(monkeypatch):
    import interactive_utils as iu
    from unittest.mock import MagicMock
    from cryptography.fernet import Fernet
    monkeypatch.setenv("INTELLISTOCK_CRED_KEY", Fernet.generate_key().decode())
    fake_r = MagicMock()
    monkeypatch.setattr(iu.store, "insert", fake_r.insert)
    fake_r.insert.return_value = {"inserted": 1, "generated_keys": ["new-id"]}
    monkeypatch.setattr(iu, "_ensure_models_table", lambda conn: None)
    iu.action_create_model(
        None, "bk", "bedrock", "us.anthropic.claude-3-5-sonnet-20241022-v2:0",
        api_key="key", bedrock_region="us-east-1", bedrock_reasoning="Medium")
    doc = fake_r.insert.call_args.args[1]
    assert doc["provider"] == "bedrock"
    assert doc["bedrock_region"] == "us-east-1"
    assert doc["bedrock_reasoning"] == "medium"  # normalized to lowercase


def test_action_edit_model_updates_bedrock_fields(monkeypatch):
    import interactive_utils as iu
    from unittest.mock import MagicMock
    existing = {"id": "m1", "provider": "bedrock",
                "model": "us.anthropic.claude-3-5-sonnet-20241022-v2:0",
                "bedrock_region": "us-east-1", "bedrock_reasoning": "off"}
    fake_r = MagicMock()
    fake_r.get.return_value = existing
    monkeypatch.setattr(iu.store, "insert", fake_r.insert)
    monkeypatch.setattr(iu.store, "update", fake_r.update)
    monkeypatch.setattr(iu.store, "get", fake_r.get)
    monkeypatch.setattr(iu, "_ensure_models_table", lambda conn: None)
    iu.action_edit_model(None, "m1", bedrock_region="eu-west-1", bedrock_reasoning="high")
    update = fake_r.update.call_args.args[2]
    assert update["bedrock_region"] == "eu-west-1"
    assert update["bedrock_reasoning"] == "high"
