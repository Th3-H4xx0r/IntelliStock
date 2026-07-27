"""Tests for Ollama-specific Models CRUD changes in backend/api/main.py and
backend/interactive_utils.py."""
import pytest


# ─────────────── _validate_provider_model_compat permissiveness ─────────────


def test_validate_provider_model_compat_accepts_any_non_empty_ollama_model():
    """Ollama models are user-installed Modelfile tags; we accept anything
    non-empty by leaving 'ollama' out of the incompatibility prefix map."""
    from interactive_utils import _validate_provider_model_compat
    # All these should not raise:
    _validate_provider_model_compat("ollama", "llama3.2")
    _validate_provider_model_compat("ollama", "qwen2.5:14b")
    _validate_provider_model_compat("ollama", "gpt-oss:120b-cloud")
    _validate_provider_model_compat("ollama", "my-custom-modelfile")
    # Even names that LOOK like other providers' models pass for Ollama,
    # because Modelfile tags can legitimately reference any name. Worst
    # case the runtime call hits 404 model-not-found which is surfaced
    # cleanly by _call_ollama.
    _validate_provider_model_compat("ollama", "claude-sonnet-4-6")
    _validate_provider_model_compat("ollama", "gpt-4o")


def test_validate_provider_model_compat_empty_model_passes_through():
    """The validator already returns early for empty model; emptiness is
    enforced upstream by Pydantic's ``min_length=1`` on CreateModelBody.model."""
    from interactive_utils import _validate_provider_model_compat
    _validate_provider_model_compat("ollama", "")
    _validate_provider_model_compat("ollama", None)


# ─────────────────── CreateModelBody / EditModelBody ───────────────────────

# Import the body classes by hand to avoid app startup side effects (the
# FastAPI app at module import time also tries to talk to RethinkDB).


def _import_body(name: str):
    """Import a Pydantic body class from api.main without triggering app boot."""
    # api/main.py declares these at module top-level; importing the module
    # will run any decorators on FastAPI routes but only side-effect-free
    # Pydantic class definitions are needed here. If app boot has heavy
    # side effects we'd need a smaller helper module; for now this works
    # since import-time work in main.py is bounded.
    import importlib
    mod = importlib.import_module("api.main")
    return getattr(mod, name)


def test_create_model_body_accepts_ollama_fields():
    CreateModelBody = _import_body("CreateModelBody")
    body = CreateModelBody(
        name="Local Llama",
        provider="ollama",
        model="llama3.2",
        ollama_base_url="http://localhost:11434",
        ollama_keep_alive="5m",
    )
    assert body.ollama_base_url == "http://localhost:11434"
    assert body.ollama_keep_alive == "5m"


def test_edit_model_body_accepts_ollama_fields():
    EditModelBody = _import_body("EditModelBody")
    body = EditModelBody(
        ollama_base_url="https://ollama.com/v1",
        ollama_keep_alive="60m",
    )
    assert body.ollama_base_url == "https://ollama.com/v1"
    assert body.ollama_keep_alive == "60m"


def test_create_model_body_ollama_base_url_length_capped():
    CreateModelBody = _import_body("CreateModelBody")
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        CreateModelBody(
            name="x", provider="ollama", model="llama3.2",
            ollama_base_url="x" * 600,
        )


def test_create_model_body_ollama_keep_alive_length_capped():
    CreateModelBody = _import_body("CreateModelBody")
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        CreateModelBody(
            name="x", provider="ollama", model="llama3.2",
            ollama_base_url="http://localhost:11434",
            ollama_keep_alive="x" * 20,  # > 16 chars
        )


# ─────────────────── LlmConfigTestBody ─────────────────────────────────────


def test_llm_config_test_body_accepts_ollama_fields():
    LlmConfigTestBody = _import_body("LlmConfigTestBody")
    body = LlmConfigTestBody(
        provider="ollama",
        model="llama3.2",
        api_key=None,
        ollama_base_url="http://localhost:11434",
        ollama_keep_alive="5m",
    )
    assert body.ollama_base_url == "http://localhost:11434"
    assert body.ollama_keep_alive == "5m"


# ─────────────── _build_llm_test_provider_config branch ──────────────────


def test_build_llm_test_provider_config_ollama_propagates_fields():
    from api.main import _build_llm_test_provider_config
    LlmConfigTestBody = _import_body("LlmConfigTestBody")
    body = LlmConfigTestBody(
        provider="ollama",
        model="llama3.2",
        ollama_base_url="http://REDACTED-IP:11434",
        ollama_keep_alive="60m",
    )
    config = _build_llm_test_provider_config(body)
    assert config["ollama_base_url"] == "http://REDACTED-IP:11434"
    assert config["ollama_keep_alive"] == "60m"


def test_build_llm_test_provider_config_ollama_omits_empty_keep_alive():
    from api.main import _build_llm_test_provider_config
    LlmConfigTestBody = _import_body("LlmConfigTestBody")
    body = LlmConfigTestBody(
        provider="ollama",
        model="llama3.2",
        ollama_base_url="http://localhost:11434",
    )
    config = _build_llm_test_provider_config(body)
    assert config["ollama_base_url"] == "http://localhost:11434"
    assert "ollama_keep_alive" not in config


# ─────────────── /llm/test path — local Ollama (no api_key) ────────────────


def test_llm_test_endpoint_accepts_local_ollama_without_api_key(monkeypatch):
    """Critical UX path: clicking "Test & Save" on a local Ollama row
    must NOT fail with "No API key provided" — local Ollama has no key."""
    from fastapi.testclient import TestClient
    from unittest.mock import patch
    from api import main as api_main

    # Clear env so the test isolates the explicit api_key=None case.
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)

    app = api_main.app
    app.dependency_overrides[api_main.get_current_user] = lambda: {
        "id": "test-user", "username": "test", "role": "user"
    }
    app.dependency_overrides[api_main.conn_dependency] = lambda: object()
    client = TestClient(app)

    # Mock the two LLM call sites the test endpoint uses.
    class _Stub:
        ok = True
    with patch("api.main.call_structured_llm_by_provider",
               return_value=_Stub()), \
         patch("api.main.call_llm_by_provider",
               return_value="PONG"), \
         patch("api.main.get_last_structured_llm_call_metadata",
               return_value={"effective_model": "llama3.2", "provider_meta": {}}):
        resp = client.post(
            "/llm/test",
            json={
                "provider": "ollama",
                "model": "llama3.2",
                "api_key": None,
                "ollama_base_url": "http://localhost:11434",
            },
        )
    app.dependency_overrides.clear()
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["provider"] == "ollama"
    assert payload["model"] == "llama3.2"
