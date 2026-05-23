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
