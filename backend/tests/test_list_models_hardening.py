"""The list-models routes send explicit keys only and return fixed error text."""
import logging
from unittest.mock import AsyncMock, Mock

import pytest

from bedrock_client import BedrockAuthError, BedrockConnectionError, BedrockProviderError
from ollama_client import OllamaAuthError, OllamaConnectionError, OllamaProviderError

_LLM_ENV_VARS = (
    "OPENAI_API_KEY", "OPENAI_BASE_URL", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT",
    "NVIDIA_BASE_URL", "OLLAMA_API_KEY", "OLLAMA_BASE_URL", "OPENROUTER_API_KEY",
    "OPENROUTER_BASE_URL", "LLM_BASE_URL_ALLOWLIST",
)


@pytest.fixture(autouse=True)
def _clear_llm_env(monkeypatch):
    # api/main.py runs load_dotenv at import, so a developer's .env can leak in.
    for name in _LLM_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def app_client():
    from fastapi.testclient import TestClient
    from api import main as api_main

    app = api_main.app
    app.dependency_overrides[api_main.get_current_user] = lambda: {
        "id": "test-user", "username": "test", "role": "user"}
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(api_main.get_current_user, None)


def _capture_ollama(monkeypatch):
    seen = {}
    async def fake(base_url, api_key=None, **_):
        seen.update(base_url=base_url, api_key=api_key); return []
    monkeypatch.setattr("api.main.ollama_client.list_models", fake)
    return seen

def test_ollama_env_key_goes_only_to_allowlisted_hosts(app_client, monkeypatch):
    monkeypatch.setenv("OLLAMA_API_KEY", "env-secret")
    seen = _capture_ollama(monkeypatch)
    assert app_client.post("/ollama/list-models", json={"base_url": "https://ollama.com"}).status_code == 200
    assert seen["api_key"] == "env-secret"
    assert app_client.post("/ollama/list-models", json={"base_url": "http://10.9.9.9:11434"}).status_code == 200
    assert not seen["api_key"]

def test_ollama_caller_key_goes_anywhere(app_client, monkeypatch):
    seen = _capture_ollama(monkeypatch)
    app_client.post("/ollama/list-models", json={"base_url": "http://10.9.9.9:11434", "api_key": "mine"})
    assert seen["api_key"] == "mine"

@pytest.mark.parametrize("exc,status,body", [
    (OllamaConnectionError("Could not reach Ollama at http://10.9.9.9:22 [Errno 111] Connection refused"),
     502, {"error": "Could not reach the Ollama host"}),
    (OllamaProviderError("Ollama returned 404 listing models: <html>internal page</html>"),
     502, {"error": "The Ollama host returned an error"}),
    (OllamaAuthError("Unauthorized at http://10.9.9.9:11434"), 401, {"error": "Authentication failed"}),
])
def test_ollama_errors_are_fixed_strings(app_client, monkeypatch, exc, status, body):
    monkeypatch.setattr("api.main.ollama_client.list_models", AsyncMock(side_effect=exc))
    r = app_client.post("/ollama/list-models", json={"base_url": "http://10.9.9.9:11434"})
    assert (r.status_code, r.json()) == (status, body)

@pytest.mark.parametrize("url", ["file:///etc/passwd", "http://user:pw@h:11434", "http://169.254.169.254/"])
def test_ollama_rejects_bad_urls_before_any_call(app_client, monkeypatch, url):
    seen = _capture_ollama(monkeypatch)
    assert app_client.post("/ollama/list-models", json={"base_url": url}).status_code == 400
    assert seen == {}

@pytest.mark.parametrize("exc,status,body", [
    (BedrockConnectionError("Could not reach Bedrock in us-east-1: Connect timeout on endpoint URL"),
     502, {"error": "Could not reach Bedrock in us-east-1"}),
    (BedrockAuthError("AccessDeniedException: User arn:aws:iam::123:user/x is not authorized"),
     401, {"error": "Authentication failed (the key may lack bedrock:ListFoundationModels)"}),
    (BedrockProviderError("ValidationException: bad"), 502, {"error": "Bedrock returned an error"}),
])
def test_bedrock_errors_are_fixed_strings(app_client, monkeypatch, exc, status, body):
    monkeypatch.setattr("api.main.bedrock_client.list_models", Mock(side_effect=exc))
    r = app_client.post("/bedrock/list-models", json={"api_key": "k", "region": "us-east-1"})
    assert (r.status_code, r.json()) == (status, body)

def test_openrouter_rejects_bad_urls(app_client, monkeypatch):
    called = Mock(return_value=[])
    monkeypatch.setattr("api.main.openrouter_client.list_models", called)
    assert app_client.post("/openrouter/list-models", json={"base_url": "ftp://x/"}).status_code == 400
    called.assert_not_called()

def test_ollama_discovery_client_never_sends_the_ambient_key(monkeypatch):
    import asyncio, httpx, ollama, ollama_client
    monkeypatch.setenv("OLLAMA_API_KEY", "env-secret")
    seen: list = []
    def handler(r):
        seen.append({k.lower(): v for k, v in r.headers.items()}); return httpx.Response(200, json={"models": []})
    real = ollama.AsyncClient
    monkeypatch.setattr(ollama_client, "AsyncClient",
                        lambda *a, **kw: real(*a, transport=httpx.MockTransport(handler), **kw))
    asyncio.run(ollama_client.list_models("http://h:11434"))
    assert "authorization" not in seen[-1]
    asyncio.run(ollama_client.list_models("http://h:11434", "mine"))
    assert seen[-1]["authorization"] == "Bearer mine"


# ───────────────────────────── additions ─────────────────────────────


def test_ollama_show_model_never_sends_the_ambient_key(monkeypatch):
    import asyncio, httpx, ollama, ollama_client
    monkeypatch.setenv("OLLAMA_API_KEY", "env-secret")
    seen: list = []
    def handler(r):
        seen.append({k.lower(): v for k, v in r.headers.items()})
        return httpx.Response(200, json={"model_info": {}})
    real = ollama.AsyncClient
    monkeypatch.setattr(ollama_client, "AsyncClient",
                        lambda *a, **kw: real(*a, transport=httpx.MockTransport(handler), **kw))
    asyncio.run(ollama_client.show_model("http://h:11434", None, "llama3.2"))
    assert "authorization" not in seen[-1]
    asyncio.run(ollama_client.show_model("http://h:11434", "mine", "llama3.2"))
    assert seen[-1]["authorization"] == "Bearer mine"


def test_ollama_blank_caller_key_follows_the_server_key_rule(app_client, monkeypatch):
    # The web client sends no api_key when the field is blank; another client may send "".
    monkeypatch.setenv("OLLAMA_API_KEY", "env-secret")
    seen = _capture_ollama(monkeypatch)
    app_client.post("/ollama/list-models", json={"base_url": "https://ollama.com", "api_key": ""})
    assert seen["api_key"] == "env-secret"
    app_client.post("/ollama/list-models", json={"base_url": "http://10.9.9.9:11434", "api_key": ""})
    assert not seen["api_key"]


def test_ollama_hidden_error_still_reaches_the_api_log(app_client, monkeypatch):
    records: list = []

    class _Keep(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = _Keep(level=logging.WARNING)
    log = logging.getLogger("uvicorn.error")
    log.addHandler(handler)
    try:
        monkeypatch.setattr("api.main.ollama_client.list_models", AsyncMock(
            side_effect=OllamaProviderError("Ollama returned 500 listing models: boom-detail")))
        r = app_client.post("/ollama/list-models", json={"base_url": "http://10.9.9.9:11434"})
    finally:
        log.removeHandler(handler)
    assert r.status_code == 502 and "boom-detail" not in r.text
    logged = " ".join(rec.getMessage() for rec in records if rec.levelno == logging.WARNING)
    assert "10.9.9.9" in logged and "boom-detail" in logged


def test_openrouter_blank_base_url_is_not_refused(app_client, monkeypatch):
    # A blank base URL means "the public catalog", as before; the URL check skips it.
    called = Mock(return_value=[])
    monkeypatch.setattr("api.main.openrouter_client.list_models", called)
    r = app_client.post("/openrouter/list-models", json={"base_url": "   "})
    assert r.status_code == 200
    called.assert_called_once()
