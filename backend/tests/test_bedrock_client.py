"""Tests for backend/bedrock_client.py — bearer-token clients + model discovery."""
import types
import pytest

import bedrock_client as bc


# ────────────────────────────── exceptions ─────────────────────────────────


def test_exception_hierarchy():
    for cls in (bc.BedrockConnectionError, bc.BedrockAuthError, bc.BedrockProviderError):
        assert issubclass(cls, Exception)
    assert bc.BedrockAuthError is not bc.BedrockConnectionError
    assert bc.BedrockProviderError is not bc.BedrockAuthError


# ────────────────────────────── bearer auth ────────────────────────────────


def test_inject_bearer_sets_authorization_header():
    req = types.SimpleNamespace(headers={})
    handler = bc._make_bearer_injector("secret-key")
    handler(request=req)
    assert req.headers["Authorization"] == "Bearer secret-key"


def test_inject_bearer_noop_without_token():
    req = types.SimpleNamespace(headers={})
    bc._make_bearer_injector("")(request=req)
    assert "Authorization" not in req.headers


def test_build_runtime_client_is_unsigned_with_region(monkeypatch):
    captured = {}

    class _FakeClient:
        def __init__(self):
            self.meta = types.SimpleNamespace(
                events=types.SimpleNamespace(
                    register=lambda *a, **k: captured.setdefault("registered", a)
                )
            )

    def _fake_boto3_client(service, region_name=None, config=None, **kw):
        captured["service"] = service
        captured["region"] = region_name
        captured["config"] = config
        return _FakeClient()

    monkeypatch.setattr(bc.boto3, "client", _fake_boto3_client)
    bc.build_runtime_client("key-123", "us-east-1")
    assert captured["service"] == "bedrock-runtime"
    assert captured["region"] == "us-east-1"
    # UNSIGNED so no AWS creds are needed; bearer header injected via event.
    assert captured["config"].signature_version == bc.botocore.UNSIGNED
    assert captured["registered"][0].startswith("before-send.bedrock-runtime")


def test_build_client_requires_region(monkeypatch):
    monkeypatch.setattr(bc.boto3, "client", lambda *a, **k: None)
    with pytest.raises(bc.BedrockProviderError):
        bc.build_runtime_client("key", "")


# ────────────────────────────── list_models ────────────────────────────────


def test_list_models_maps_access_denied_to_auth_error(monkeypatch):
    from botocore.exceptions import ClientError

    class _Denied:
        def list_foundation_models(self, **kw):
            raise ClientError(
                {"Error": {"Code": "AccessDeniedException", "Message": "no"}},
                "ListFoundationModels",
            )

        def list_inference_profiles(self, **kw):
            return {"inferenceProfileSummaries": []}

    monkeypatch.setattr(bc, "build_control_client", lambda api_key, region, **kw: _Denied())
    with pytest.raises(bc.BedrockAuthError):
        bc.list_models("key", "us-east-1")


def test_list_models_normalizes_foundation_and_profiles(monkeypatch):
    class _Ok:
        def list_foundation_models(self, **kw):
            return {"modelSummaries": [
                {"modelId": "anthropic.claude-3-5-sonnet-20241022-v2:0",
                 "modelName": "Claude 3.5 Sonnet v2", "providerName": "Anthropic",
                 "inputModalities": ["TEXT"], "outputModalities": ["TEXT"],
                 "responseStreamingSupported": True},
            ]}

        def list_inference_profiles(self, **kw):
            return {"inferenceProfileSummaries": [
                {"inferenceProfileId": "us.anthropic.claude-3-5-sonnet-20241022-v2:0",
                 "inferenceProfileName": "US Claude 3.5 Sonnet v2"},
            ]}

    monkeypatch.setattr(bc, "build_control_client", lambda api_key, region, **kw: _Ok())
    out = bc.list_models("key", "us-east-1")
    by_id = {m["id"]: m for m in out}
    assert "anthropic.claude-3-5-sonnet-20241022-v2:0" in by_id
    assert by_id["anthropic.claude-3-5-sonnet-20241022-v2:0"]["kind"] == "foundation"
    assert by_id["us.anthropic.claude-3-5-sonnet-20241022-v2:0"]["kind"] == "inference_profile"


def test_list_models_inference_profile_failure_is_nonfatal(monkeypatch):
    from botocore.exceptions import ClientError

    class _PartialOk:
        def list_foundation_models(self, **kw):
            return {"modelSummaries": [
                {"modelId": "amazon.nova-pro-v1:0", "modelName": "Nova Pro",
                 "providerName": "Amazon", "inputModalities": ["TEXT"], "outputModalities": ["TEXT"]},
            ]}

        def list_inference_profiles(self, **kw):
            raise ClientError({"Error": {"Code": "ValidationException", "Message": "unsupported"}},
                              "ListInferenceProfiles")

    monkeypatch.setattr(bc, "build_control_client", lambda api_key, region, **kw: _PartialOk())
    out = bc.list_models("key", "us-east-1")
    assert [m["id"] for m in out] == ["amazon.nova-pro-v1:0"]


def test_health_check_never_raises(monkeypatch):
    monkeypatch.setattr(bc, "list_models", lambda *a, **k: (_ for _ in ()).throw(bc.BedrockAuthError("nope")))
    ok, err = bc.health_check("key", "us-east-1")
    assert ok is False and "nope" in err
