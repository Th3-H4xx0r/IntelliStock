"""Thin async wrappers over the official ``ollama`` SDK.

Responsibility: keep non-chat HTTP operations out of llm_utils.py.
The chat path lives in ``llm_utils._call_ollama``; this module owns
``/api/tags``, ``/api/show``, ``/api/ps``, plus the shared exception
classes that both modules raise.

Used by:
  * backend/api/main.py — the ``POST /ollama/list-models`` endpoint
  * backend/llm_utils.py — ``_call_ollama`` imports the exception classes
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

import httpx
from ollama import AsyncClient, ResponseError


# ─────────────────────────────── exceptions ────────────────────────────────


class OllamaConnectionError(Exception):
    """Network failure reaching the Ollama host (DNS, refused, timeout)."""


class OllamaAuthError(Exception):
    """Authentication failure (401 from Ollama Cloud)."""


class OllamaProviderError(Exception):
    """Any other non-2xx response from Ollama (404 model-not-found, 5xx, …)."""


# ────────────────────────────── helpers ────────────────────────────────────


def _auth_headers(api_key: Optional[str]) -> dict[str, str]:
    if api_key:
        return {"Authorization": f"Bearer {api_key}"}
    return {}


_CONTEXT_LENGTH_SUFFIX = ".context_length"


def _extract_context_length(show_response: dict[str, Any]) -> Optional[int]:
    """Pull the per-family context_length out of /api/show.

    Ollama keys this as ``<family>.context_length`` inside model_info, e.g.
    ``llama.context_length`` or ``qwen2.context_length``. The family varies
    per model so we scan the keys instead of hardcoding.
    """
    info = (show_response or {}).get("model_info") or {}
    for k, v in info.items():
        if k.endswith(_CONTEXT_LENGTH_SUFFIX) and isinstance(v, int):
            return v
    return None


def _project_model(raw: dict[str, Any]) -> dict[str, Any]:
    details = raw.get("details") or {}
    return {
        "name": raw.get("name") or raw.get("model"),
        "model": raw.get("model") or raw.get("name"),
        "size_bytes": raw.get("size"),
        "parameter_size": details.get("parameter_size"),
        "quantization_level": details.get("quantization_level"),
        "context_length": None,
    }


# Network exceptions we want to map to OllamaConnectionError consistently
# across every function in this module. Includes both httpx-specific
# variants and the built-in ConnectionError / TimeoutError that some code
# paths surface.
_CONNECTION_EXCS = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.RemoteProtocolError,
    httpx.NetworkError,
    ConnectionError,
    TimeoutError,
)


# ────────────────────────────── list_models ────────────────────────────────


async def list_models(
    base_url: str,
    api_key: Optional[str] = None,
    *,
    timeout_sec: float = 8.0,
) -> list[dict[str, Any]]:
    """Return installed models. If <=20, also enrich each with ``context_length``."""
    client = AsyncClient(
        host=base_url,
        headers=_auth_headers(api_key),
        timeout=timeout_sec,
    )
    try:
        resp = await client.list()
    except ResponseError as e:
        if (e.status_code or 0) == 401:
            raise OllamaAuthError(f"Unauthorized at {base_url}") from e
        raise OllamaProviderError(
            f"Ollama returned {e.status_code} listing models: {e}"
        ) from e
    except _CONNECTION_EXCS as e:
        raise OllamaConnectionError(
            f"Could not reach Ollama at {base_url}"
        ) from e

    # The SDK returns a model object that exposes ``models`` as a list.
    # When the underlying SDK changes between dict-shape and object-shape
    # responses, this lookup keeps working for both.
    if hasattr(resp, "models"):
        raw_models = list(resp.models or [])
    else:
        raw_models = (resp or {}).get("models") or []

    tags = []
    for m in raw_models:
        if hasattr(m, "model_dump"):
            m = m.model_dump()
        elif not isinstance(m, dict):
            m = dict(m)
        tags.append(_project_model(m))

    if 0 < len(tags) <= 20:
        sem = asyncio.Semaphore(4)

        async def _enrich(tag: dict[str, Any]) -> None:
            async with sem:
                try:
                    detail = await client.show(tag["name"])
                    if hasattr(detail, "model_dump"):
                        detail = detail.model_dump()
                    tag["context_length"] = _extract_context_length(detail)
                except Exception:
                    tag["context_length"] = None

        await asyncio.gather(*(_enrich(t) for t in tags))

    return tags
