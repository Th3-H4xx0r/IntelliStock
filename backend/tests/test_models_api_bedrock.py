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
