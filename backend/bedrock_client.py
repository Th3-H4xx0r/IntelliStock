"""Thin wrappers over boto3 for Amazon Bedrock.

Responsibility: keep non-chat operations (discovery, client construction,
bearer-token auth) out of llm_utils.py. The chat paths live in
``llm_utils._call_bedrock`` / ``call_bedrock_with_tools`` and the structured
path in ``_build_pydantic_ai_model``; this module owns
``list_foundation_models`` / ``list_inference_profiles`` plus the shared
exception classes and the per-client bearer-token injection.

Auth: Bedrock API key (bearer token), injected per boto3 client via a
``before-sign`` event handler with an UNSIGNED signer — so concurrent clients
built with different keys never collide and no AWS credentials are required.
(``before-sign`` is the idiomatic header-injection hook; with UNSIGNED there is
no SigV4 step to overwrite the header. Verified at the wire in tests.)

Used by:
  * backend/api/main.py — the ``POST /bedrock/list-models`` endpoint
  * backend/llm_utils.py — builds runtime clients for Converse calls
"""
from __future__ import annotations

from typing import Any

import boto3
import botocore
from botocore.config import Config
from botocore.exceptions import (
    ClientError,
    EndpointConnectionError,
    ConnectionError as BotoConnError,
)


# ─────────────────────────────── exceptions ────────────────────────────────


class BedrockConnectionError(Exception):
    """Network failure reaching the Bedrock endpoint (DNS, refused, timeout)."""


class BedrockAuthError(Exception):
    """Authentication / authorization failure (401/403, AccessDenied, expired token)."""


class BedrockProviderError(Exception):
    """Any other non-2xx response from Bedrock (ValidationException, 5xx, …)."""


# Bedrock error codes that mean "the caller is not authorized / the token is bad".
_AUTH_CODES = {
    "AccessDeniedException",
    "UnauthorizedException",
    "UnrecognizedClientException",
    "InvalidSignatureException",
    "ExpiredTokenException",
    "ForbiddenException",
}
_CONNECTION_EXCS = (EndpointConnectionError, BotoConnError, ConnectionError, TimeoutError)


# ─────────────────────────────── helpers ───────────────────────────────────


def _make_bearer_injector(api_key: str):
    """Return a before-sign handler that sets the bearer Authorization header.

    Works for the ``before-sign.<service>`` event (mutable ``AWSRequest``,
    fires before transmission); with an UNSIGNED signer there is no SigV4 step
    to clobber the header. The handler tolerates the extra kwargs before-sign
    passes (signing_name, region_name, request_signer, …) via ``**_kwargs``.
    """
    token = str(api_key or "").strip()

    def _inject(request=None, **_kwargs):
        if request is not None and token:
            request.headers["Authorization"] = f"Bearer {token}"
        return None

    return _inject


def _client_config(timeout_sec: float = 30.0, max_attempts: int = 1) -> Config:
    # Clamp to a positive float: botocore treats read_timeout=None/0 as an
    # UNBOUNDED read, which would let a stalled Bedrock call hang forever.
    try:
        _t = float(timeout_sec)
    except (TypeError, ValueError):
        _t = 30.0
    if not (_t > 0):
        _t = 30.0
    return Config(
        signature_version=botocore.UNSIGNED,  # bearer token via header, not SigV4
        read_timeout=_t,
        connect_timeout=min(15.0, _t),
        retries={"max_attempts": max_attempts, "mode": "standard"},
    )


def _build_client(service: str, api_key: str, region: str, *, timeout_sec: float = 30.0):
    region = str(region or "").strip()
    if not region:
        raise BedrockProviderError("Bedrock requires a region (e.g. us-east-1)")
    client = boto3.client(service, region_name=region, config=_client_config(timeout_sec))
    client.meta.events.register(f"before-sign.{service}", _make_bearer_injector(api_key))
    return client


def build_runtime_client(api_key: str, region: str, *, timeout_sec: float = 30.0):
    """boto3 bedrock-runtime client authenticating with the API key as a bearer token."""
    return _build_client("bedrock-runtime", api_key, region, timeout_sec=timeout_sec)


def build_control_client(api_key: str, region: str, *, timeout_sec: float = 15.0):
    """boto3 bedrock (control-plane) client for discovery."""
    return _build_client("bedrock", api_key, region, timeout_sec=timeout_sec)


def _classify_client_error(e: "ClientError") -> Exception:
    code = (e.response or {}).get("Error", {}).get("Code", "")
    msg = (e.response or {}).get("Error", {}).get("Message", str(e))
    status = (e.response or {}).get("ResponseMetadata", {}).get("HTTPStatusCode")
    if code in _AUTH_CODES or status in (401, 403):
        return BedrockAuthError(f"{code or status}: {msg}")
    return BedrockProviderError(f"{code or status}: {msg}")


def _project_foundation(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": raw.get("modelId"),
        "name": raw.get("modelName") or raw.get("modelId"),
        "provider_name": raw.get("providerName") or "",
        "kind": "foundation",
        "supports_tools": "TEXT" in (raw.get("outputModalities") or []),
        "modalities": list(raw.get("inputModalities") or []),
    }


def _project_profile(raw: dict[str, Any]) -> dict[str, Any]:
    pid = raw.get("inferenceProfileId")
    return {
        "id": pid,
        "name": raw.get("inferenceProfileName") or pid,
        "provider_name": "",
        "kind": "inference_profile",
        "supports_tools": True,
        "modalities": [],
    }


# ────────────────────────────── list_models ────────────────────────────────


def list_models(api_key: str, region: str, *, timeout_sec: float = 15.0) -> list[dict[str, Any]]:
    """Return Bedrock foundation models + cross-region inference profiles.

    Raises BedrockAuthError / BedrockConnectionError / BedrockProviderError.
    Discovery may be unavailable for narrowly-scoped API keys (the key may
    authorize Converse but not ``bedrock:ListFoundationModels``); callers
    degrade to manual model-id entry on BedrockAuthError.
    """
    client = build_control_client(api_key, region, timeout_sec=timeout_sec)
    out: list[dict[str, Any]] = []
    try:
        fm = client.list_foundation_models()
    except ClientError as e:
        raise _classify_client_error(e) from e
    except _CONNECTION_EXCS as e:
        raise BedrockConnectionError(f"Could not reach Bedrock in {region}: {e}") from e
    for m in (fm or {}).get("modelSummaries", []) or []:
        if m.get("modelId"):
            out.append(_project_foundation(m))
    # Inference profiles are best-effort: some regions / keys lack the API.
    try:
        ip = client.list_inference_profiles()
        for p in (ip or {}).get("inferenceProfileSummaries", []) or []:
            if p.get("inferenceProfileId"):
                out.append(_project_profile(p))
    except (ClientError, *_CONNECTION_EXCS):
        pass
    return out


def health_check(api_key: str, region: str, *, timeout_sec: float = 8.0) -> tuple[bool, str]:
    """Cheap probe used by UI. Never raises — flattens to ``(ok, error)``."""
    try:
        list_models(api_key, region, timeout_sec=timeout_sec)
        return True, ""
    except (BedrockAuthError, BedrockConnectionError, BedrockProviderError) as e:
        return False, str(e)
