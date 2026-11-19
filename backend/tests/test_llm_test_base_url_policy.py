"""POST /llm/test never pairs a saved or server key with an unlisted base URL."""
import types

import pytest

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


class _Ok:
    ok = True


@pytest.fixture
def llm_test(monkeypatch):
    from fastapi.testclient import TestClient
    from api import main as api_main

    state = types.SimpleNamespace(
        saved_row={"id": "m1", "provider": "openai", "api_key": "saved-secret", "openai_base_url": ""},
        calls=[],
        smoke_configs=[],
        structured_result=_Ok(),
        smoke_exc=None,
        meta={"effective_model": "m", "provider_meta": {}},
    )

    def fake_get_model_raw(conn, model_id):
        return state.saved_row

    def fake_structured(*args, **kwargs):
        state.calls.append((args[1], kwargs["provider_config"]))
        return state.structured_result

    def fake_plain(*args, **kwargs):
        state.smoke_configs.append(kwargs["provider_config"])
        if state.smoke_exc is not None:
            raise state.smoke_exc
        return "PONG"

    monkeypatch.setattr(api_main, "action_get_model_raw", fake_get_model_raw)
    monkeypatch.setattr(api_main, "call_structured_llm_by_provider", fake_structured)
    monkeypatch.setattr(api_main, "call_llm_by_provider", fake_plain)
    monkeypatch.setattr(api_main, "get_last_structured_llm_call_metadata", lambda: state.meta)

    app = api_main.app
    app.dependency_overrides[api_main.get_current_user] = lambda: {"id": "u1", "username": "test"}
    app.dependency_overrides[api_main.conn_dependency] = lambda: object()
    client = TestClient(app)
    state.post = lambda json: client.post("/llm/test", json=json)
    try:
        yield state
    finally:
        app.dependency_overrides.pop(api_main.get_current_user, None)
        app.dependency_overrides.pop(api_main.conn_dependency, None)


HIDDEN = ("LLM connectivity test failed. Details are hidden because the base URL is not on the "
          "allowlist; the API log has them.")
HIDDEN_SMOKE = "Smoke generation failed. Details are hidden because the base URL is not on the allowlist."
EDIT = {"provider": "openai", "model": "gpt-x", "api_key": "sk-****abcd", "model_id": "m1"}

def test_saved_key_is_not_sent_to_an_unlisted_base_url(llm_test):
    r = llm_test.post({**EDIT, "openai_base_url": "https://evil.example/v1"})
    assert r.status_code == 400 and "LLM_BASE_URL_ALLOWLIST" in r.json()["detail"]
    assert llm_test.calls == []

def test_server_key_is_not_sent_to_an_unlisted_base_url(llm_test, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "env-secret")
    r = llm_test.post({"provider": "openai", "model": "gpt-x", "openai_base_url": "https://evil.example/v1"})
    assert r.status_code == 400 and llm_test.calls == []

def test_server_key_still_used_without_a_caller_url(llm_test, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "env-secret")
    assert llm_test.post({"provider": "openai", "model": "gpt-x"}).status_code == 200
    assert llm_test.calls[0][0] == "env-secret"

def test_caller_key_may_go_to_any_url(llm_test):
    r = llm_test.post({"provider": "openai", "model": "gpt-x", "api_key": "sk-mine",
                       "openai_base_url": "https://evil.example/v1"})
    assert r.status_code == 200 and llm_test.calls[0][0] == "sk-mine"

def test_saved_key_is_used_for_its_own_row_host(llm_test):
    llm_test.saved_row = {**llm_test.saved_row, "openai_base_url": "https://llm.lan/v1"}
    assert llm_test.post({**EDIT, "openai_base_url": "https://llm.lan/v1"}).status_code == 200
    assert llm_test.calls[0][0] == "saved-secret"

@pytest.mark.parametrize("provider,row_url,form_url", [
    ("openrouter", {"openrouter_base_url": ""}, {"openrouter_base_url": "https://openrouter.ai/api/v1"}),
    ("ollama", {"ollama_base_url": ""}, {"ollama_base_url": "http://localhost:11434"}),
])
def test_edit_flow_with_client_default_base_url_still_uses_saved_key(llm_test, provider, row_url, form_url):
    llm_test.saved_row = {"id": "m1", "provider": provider, "api_key": "saved-secret", **row_url}
    r = llm_test.post({"provider": provider, "model": "m", "api_key": "sk-****abcd", "model_id": "m1", **form_url})
    assert r.status_code == 200 and llm_test.calls[0][0] == "saved-secret"

def test_unlisted_ollama_url_runs_keyless_without_the_ambient_key(llm_test, monkeypatch):
    monkeypatch.setenv("OLLAMA_API_KEY", "env-secret")
    r = llm_test.post({"provider": "ollama", "model": "llama3.2", "ollama_base_url": "http://10.9.9.9:11434"})
    assert r.status_code == 200
    key, cfg = llm_test.calls[0]
    assert key == "" and cfg["ollama_allow_ambient_key"] is False
    assert llm_test.smoke_configs[0]["ollama_allow_ambient_key"] is False

def test_failure_detail_is_hidden_for_an_unlisted_url(llm_test):
    llm_test.structured_result = None
    llm_test.meta = {"error": "Connection refused: 10.9.9.9:8080"}
    r = llm_test.post({"provider": "openai", "model": "gpt-x", "api_key": "sk-mine",
                       "openai_base_url": "http://10.9.9.9:8080/v1"})
    assert r.status_code == 400 and r.json()["detail"] == HIDDEN
    assert "10.9.9.9" not in r.text and "refused" not in r.text.lower()

def test_failure_detail_passes_through_for_official_hosts(llm_test):
    llm_test.structured_result = None
    llm_test.meta = {"error": "Incorrect API key provided"}
    r = llm_test.post({"provider": "openai", "model": "gpt-x", "api_key": "sk-bad"})
    assert r.status_code == 400 and r.json()["detail"] == "Incorrect API key provided"

def test_smoke_error_is_hidden_for_an_unlisted_url(llm_test):
    llm_test.smoke_exc = RuntimeError("connect ECONNREFUSED 10.9.9.9:8080")
    r = llm_test.post({"provider": "openai", "model": "gpt-x", "api_key": "sk-mine",
                       "openai_base_url": "http://10.9.9.9:8080/v1"})
    assert r.status_code == 200 and r.json()["smoke_error"] == HIDDEN_SMOKE

def test_malformed_base_url_is_refused_before_any_call(llm_test):
    r = llm_test.post({"provider": "openai", "model": "gpt-x", "api_key": "sk-mine",
                       "openai_base_url": "https://u:p@api.openai.com/v1"})
    assert r.status_code == 400
    assert r.json()["detail"] == "Invalid base URL: use http:// or https:// with a host and no credentials."
    assert llm_test.calls == []


# Additions beyond the brief's list.

def test_hidden_smoke_error_does_not_reach_the_message_either(llm_test, monkeypatch):
    # The smoke call can also fail by returning empty, with the dispatcher's
    # recorded error; the "message" field folds the smoke error in, so the
    # replacement has to happen before it is built.
    from api import main as api_main
    monkeypatch.setattr(api_main, "call_llm_by_provider",
                        lambda *a, **k: llm_test.smoke_configs.append(k["provider_config"]) or "")
    monkeypatch.setattr(api_main, "get_last_plain_llm_call_error",
                        lambda: "HTTP 404 from http://10.9.9.9:8080/v1/chat/completions")
    r = llm_test.post({"provider": "openai", "model": "gpt-x", "api_key": "sk-mine",
                       "openai_base_url": "http://10.9.9.9:8080/v1"})
    assert r.status_code == 200 and r.json()["smoke_error"] == HIDDEN_SMOKE
    assert "10.9.9.9" not in r.text and HIDDEN_SMOKE in r.json()["message"]

def test_hidden_details_still_reach_the_api_log(llm_test, monkeypatch):
    import logging

    records = []

    class _Keep(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    handler = _Keep(level=logging.WARNING)
    log = logging.getLogger("uvicorn.error")
    monkeypatch.setattr(log, "disabled", False)
    monkeypatch.setattr(log, "level", logging.NOTSET)
    log.addHandler(handler)
    try:
        llm_test.structured_result = None
        llm_test.meta = {"error": "Connection refused: 10.9.9.9:8080"}
        r = llm_test.post({"provider": "openai", "model": "gpt-x", "api_key": "sk-mine",
                           "openai_base_url": "http://10.9.9.9:8080/v1"})
    finally:
        log.removeHandler(handler)
    assert r.json()["detail"] == HIDDEN
    assert any("openai" in m and "10.9.9.9" in m and "Connection refused" in m for m in records)

def test_saved_key_is_withheld_from_an_unlisted_ollama_url(llm_test):
    llm_test.saved_row = {"id": "m1", "provider": "ollama", "api_key": "saved-secret",
                          "ollama_base_url": "https://ollama.com"}
    r = llm_test.post({"provider": "ollama", "model": "m", "api_key": "sk-****abcd", "model_id": "m1",
                       "ollama_base_url": "http://10.9.9.9:11434"})
    assert r.status_code == 200 and llm_test.calls[0][0] == ""
