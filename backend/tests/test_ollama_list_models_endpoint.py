"""Tests for POST /ollama/list-models in backend/api/main.py."""
from unittest.mock import AsyncMock, patch
import pytest


@pytest.fixture
def app_client():
    """Build a TestClient with the FastAPI app and override the auth +
    DB-connection dependencies so unit tests don't need a real DB or auth."""
    from fastapi.testclient import TestClient
    from api import main as api_main

    app = api_main.app
    # Auth bypass — return a stub user dict.
    app.dependency_overrides[api_main.get_current_user] = lambda: {
        "id": "test-user", "username": "test", "role": "user"
    }
    # The endpoint doesn't need a DB connection but the route schema may
    # require it; we leave conn_dependency alone unless we hit failures.
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_list_models_happy_path(app_client):
    fake = [
        {"name": "llama3.2", "model": "llama3.2", "parameter_size": "3B",
         "quantization_level": "Q4_K_M", "size_bytes": 1, "context_length": 131072},
    ]
    with patch("api.main.ollama_client.list_models",
               new=AsyncMock(return_value=fake)):
        resp = app_client.post(
            "/ollama/list-models",
            json={"base_url": "http://localhost:11434"},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {"models": fake}


def test_list_models_502_on_connection_error(app_client):
    from ollama_client import OllamaConnectionError
    with patch("api.main.ollama_client.list_models",
               new=AsyncMock(side_effect=OllamaConnectionError("nope"))):
        resp = app_client.post(
            "/ollama/list-models",
            json={"base_url": "http://localhost:11434"},
        )
    assert resp.status_code == 502
    payload = resp.json()
    assert "Could not reach" in payload.get("error", "") or \
           "Could not reach" in payload.get("detail", "")


def test_list_models_401_on_auth_error(app_client):
    from ollama_client import OllamaAuthError
    with patch("api.main.ollama_client.list_models",
               new=AsyncMock(side_effect=OllamaAuthError("bad"))):
        resp = app_client.post(
            "/ollama/list-models",
            json={"base_url": "https://ollama.com/v1", "api_key": "bad"},
        )
    assert resp.status_code == 401


def test_list_models_502_on_provider_error(app_client):
    from ollama_client import OllamaProviderError
    with patch("api.main.ollama_client.list_models",
               new=AsyncMock(side_effect=OllamaProviderError("500 oops"))):
        resp = app_client.post(
            "/ollama/list-models",
            json={"base_url": "http://localhost:11434"},
        )
    assert resp.status_code == 502


def test_list_models_validation_error_on_missing_base_url(app_client):
    resp = app_client.post("/ollama/list-models", json={})
    assert resp.status_code in (400, 422)


def test_list_models_passes_api_key_to_underlying_call(app_client):
    """Bearer key from request body must flow to ollama_client.list_models."""
    captured = {}
    async def _fake(base_url, api_key=None, **_):
        captured["base_url"] = base_url
        captured["api_key"] = api_key
        return []
    with patch("api.main.ollama_client.list_models", new=_fake):
        resp = app_client.post(
            "/ollama/list-models",
            json={"base_url": "https://ollama.com/v1", "api_key": "secret"},
        )
    assert resp.status_code == 200
    assert captured["base_url"] == "https://ollama.com/v1"
    assert captured["api_key"] == "secret"
