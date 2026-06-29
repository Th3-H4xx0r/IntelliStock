"""OpenRouter model-discovery client.

Backs the ``POST /openrouter/list-models`` endpoint and the web LLM-config
dropdown. OpenRouter's catalog is a public ``GET {base_url}/models`` — no auth
required. Never raises: returns ``[]`` on any failure so the caller falls back
to manual model-id entry (same contract as the Bedrock/Ollama discovery paths).

Each returned row preserves the raw ``pricing`` object (USD **per token**,
string values) so the frontend can auto-fill the per-row cost-override fields
(``× 1e6`` → USD per 1M tokens).
"""
from __future__ import annotations

from typing import Any

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"


def list_models(base_url: str = DEFAULT_BASE_URL, *, timeout_sec: float = 15.0) -> list[dict[str, Any]]:
    """Return the OpenRouter model catalog as a list of
    ``{id, name, context_length, pricing}`` rows, sorted by id.

    Never raises — returns ``[]`` on network/parse error.
    """
    base = str(base_url or DEFAULT_BASE_URL).strip().rstrip("/")
    if not base:
        base = DEFAULT_BASE_URL
    try:
        import requests
        r = requests.get(f"{base}/models", timeout=timeout_sec)
        r.raise_for_status()
        data = r.json() or {}
    except Exception:
        return []
    rows: list[dict[str, Any]] = []
    for m in (data.get("data") or []):
        if not isinstance(m, dict):
            continue
        mid = str(m.get("id") or "").strip()
        if not mid:
            continue
        pricing = m.get("pricing")
        rows.append({
            "id": mid,
            "name": str(m.get("name") or mid),
            "context_length": m.get("context_length"),
            "pricing": pricing if isinstance(pricing, dict) else {},
        })
    rows.sort(key=lambda x: x["id"])
    return rows
