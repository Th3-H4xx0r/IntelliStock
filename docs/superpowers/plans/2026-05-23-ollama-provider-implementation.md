# Ollama LLM Provider Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire Ollama (local, remote self-hosted, and Ollama Cloud) into IntelliStock as a first-class LLM provider — plain chat, structured output via PydanticAI, tool calling, and critical-guard / rate-limit participation — surfaced through the existing `/models` UI.

**Architecture:** Approach A from the spec — inline `_call_ollama` next to `_call_openai` in `backend/llm_utils.py`, plus a small `backend/ollama_client.py` for non-chat HTTP (`/api/tags`, `/api/show`, `/api/ps`). One new endpoint `POST /ollama/list-models`. New `provider="ollama"` entry in the UI provider dropdown with a conditional form block.

**Tech Stack:** Python (FastAPI, RethinkDB, official `ollama` SDK ≥0.4, PydanticAI), Vue 3 + Tailwind on the frontend. Backend tests via `pytest` in `backend/tests/`. The frontend has no test runner today — frontend changes are verified via manual smoke testing as listed in the spec.

**Spec:** `docs/superpowers/specs/2026-05-23-ollama-provider-design.md`.

**Important paths discovered during planning:**
- Backend tests live in `backend/tests/` (62 files), not `tests/`. The spec used `tests/...` paths — the plan corrects this to `backend/tests/...`.
- The dispatcher lives in `backend/llm_utils.py` around line 4434 (`call_llm_by_provider`).
- Models CRUD lives in `backend/api/main.py` (`action_*_model`) and `backend/interactive_utils.py` (`_validate_provider_model_compat` at line ~7916, model schema at line ~7959).
- Frontend provider dropdown in `frontend/src/utils/strategyConfig.js` (`LLM_PROVIDER_OPTIONS`).
- Form lives in `frontend/src/components/LlmConfigForm.vue`; page in `frontend/src/views/ModelsView.vue`.

---

## File Structure (created or modified)

**Backend — created:**
- `backend/ollama_client.py` — async wrappers around `ollama.AsyncClient` for `/api/tags`, `/api/show`, `/api/ps`. Exception classes shared with `llm_utils.py`.
- `backend/tests/test_ollama_client.py` — unit tests for the above.
- `backend/tests/test_llm_utils_ollama.py` — `_call_ollama` plain text tests.
- `backend/tests/test_llm_utils_ollama_structured.py` — structured-output tests.
- `backend/tests/test_llm_utils_ollama_tools.py` — tool-calling tests + Gemini-shape adapter.
- `backend/tests/test_llm_critical_guard_ollama.py` — classifier tests for Ollama failures.
- `backend/tests/test_models_api_ollama.py` — Pydantic model + CRUD tests for new fields.
- `backend/tests/test_ollama_list_models_endpoint.py` — endpoint tests.
- `backend/tests/integration/test_ollama_live.py` — opt-in live test.

**Backend — modified:**
- `backend/requirements.txt` — add `ollama>=0.4,<1`.
- `backend/llm_utils.py` — new branches in `call_llm_by_provider`, `call_structured_llm_by_provider`, `_resolve_provider_config`, `resolve_api_key_for_provider`; new functions `_call_ollama`, `_call_ollama_structured_from_strategy`, `call_ollama_with_tools`; new helper `_normalize_tools_to_openai_shape`; new in-memory set `_ollama_warm_pairs`.
- `backend/llm_critical_guard.py` — extend `classify()` for Ollama 401.
- `backend/interactive_utils.py` — extend `_validate_provider_model_compat` for `"ollama"`; persist new fields on Models docs.
- `backend/api/main.py` — extend `CreateModelBody`, `EditModelBody`, `LlmConfigTestBody`; add `POST /ollama/list-models`.
- `.env.example` — add `OLLAMA_BASE_URL`, `OLLAMA_API_KEY`.

**Frontend — created:**
- `frontend/src/composables/useOllamaModels.js` — cached model-list loader.

**Frontend — modified:**
- `frontend/src/utils/strategyConfig.js` — add `'ollama'` to `LLM_PROVIDER_OPTIONS`, extend `buildStrategyLlmTestPayload`.
- `frontend/src/components/LlmConfigForm.vue` — conditional Ollama branch.
- `frontend/src/views/ModelsView.vue` — extend `formDraft` with `ollamaBaseUrl`, `ollamaKeepAlive`.

---

## Phase 1 — Backend foundation

### Task 1: Add `ollama` dependency

**Files:**
- Modify: `backend/requirements.txt`
- Test: `backend/tests/test_ollama_dep_smoke.py`

- [ ] **Step 1: Write the smoke test**

Create `backend/tests/test_ollama_dep_smoke.py`:
```python
"""Smoke test: the ollama SDK is importable and exposes the expected surface."""

def test_ollama_imports():
    import ollama
    assert hasattr(ollama, "Client")
    assert hasattr(ollama, "AsyncClient")

def test_ollama_response_error_class():
    from ollama import ResponseError
    assert issubclass(ResponseError, Exception)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest backend/tests/test_ollama_dep_smoke.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ollama'`.

- [ ] **Step 3: Add the dependency**

Append to `backend/requirements.txt`:
```
ollama>=0.4,<1
```

Then install it locally:
```bash
pip install 'ollama>=0.4,<1'
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest backend/tests/test_ollama_dep_smoke.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/requirements.txt backend/tests/test_ollama_dep_smoke.py
git commit -m "deps(ollama): add official ollama SDK + smoke test"
```

---

### Task 2: Create `ollama_client.py` exception classes + `list_models`

**Files:**
- Create: `backend/ollama_client.py`
- Create: `backend/tests/test_ollama_client.py`

- [ ] **Step 1: Write failing tests for exception classes + list_models**

Create `backend/tests/test_ollama_client.py`:
```python
import asyncio
from unittest.mock import AsyncMock, patch

import pytest


def test_exception_hierarchy():
    from ollama_client import (
        OllamaConnectionError, OllamaAuthError, OllamaProviderError,
    )
    for cls in (OllamaConnectionError, OllamaAuthError, OllamaProviderError):
        assert issubclass(cls, Exception)
    # Distinct classes — no accidental shared inheritance:
    assert OllamaAuthError is not OllamaConnectionError
    assert OllamaProviderError is not OllamaConnectionError


def test_list_models_happy_path_under_20_enriches_context():
    """When ≤20 models, list_models enriches each with context_length via show."""
    from ollama_client import list_models

    fake_list = {"models": [
        {"name": "llama3.2", "model": "llama3.2",
         "size": 2_000_000_000,
         "details": {"parameter_size": "3B", "quantization_level": "Q4_K_M"}},
        {"name": "qwen2.5:14b", "model": "qwen2.5:14b",
         "size": 9_000_000_000,
         "details": {"parameter_size": "14B", "quantization_level": "Q4_K_M"}},
    ]}
    fake_show = {
        "llama3.2": {"model_info": {"llama.context_length": 131072}},
        "qwen2.5:14b": {"model_info": {"qwen2.context_length": 32768}},
    }

    fake_client = AsyncMock()
    fake_client.list = AsyncMock(return_value=fake_list)
    fake_client.show = AsyncMock(side_effect=lambda model: fake_show[model])

    with patch("ollama_client.AsyncClient", return_value=fake_client):
        result = asyncio.run(list_models("http://localhost:11434"))

    assert len(result) == 2
    by_name = {m["name"]: m for m in result}
    assert by_name["llama3.2"]["context_length"] == 131072
    assert by_name["llama3.2"]["parameter_size"] == "3B"
    assert by_name["llama3.2"]["quantization_level"] == "Q4_K_M"
    assert by_name["qwen2.5:14b"]["context_length"] == 32768


def test_list_models_over_20_skips_context_enrichment():
    """When >20 models, context_length is None (no /api/show fanout)."""
    from ollama_client import list_models

    fake_list = {"models": [
        {"name": f"m{i}", "model": f"m{i}", "size": 1_000_000_000,
         "details": {"parameter_size": "1B", "quantization_level": "Q4_K_M"}}
        for i in range(21)
    ]}
    fake_client = AsyncMock()
    fake_client.list = AsyncMock(return_value=fake_list)
    fake_client.show = AsyncMock(side_effect=AssertionError("show must not be called"))

    with patch("ollama_client.AsyncClient", return_value=fake_client):
        result = asyncio.run(list_models("http://localhost:11434"))

    assert len(result) == 21
    assert all(m["context_length"] is None for m in result)
    fake_client.show.assert_not_called()


def test_list_models_auth_error_on_401():
    from ollama import ResponseError
    from ollama_client import list_models, OllamaAuthError

    fake_client = AsyncMock()
    fake_client.list = AsyncMock(side_effect=ResponseError("unauthorized", 401))

    with patch("ollama_client.AsyncClient", return_value=fake_client):
        with pytest.raises(OllamaAuthError):
            asyncio.run(list_models("https://ollama.com/v1", api_key="bad"))


def test_list_models_connection_error_on_network_failure():
    import httpx
    from ollama_client import list_models, OllamaConnectionError

    fake_client = AsyncMock()
    fake_client.list = AsyncMock(side_effect=httpx.ConnectError("refused"))

    with patch("ollama_client.AsyncClient", return_value=fake_client):
        with pytest.raises(OllamaConnectionError):
            asyncio.run(list_models("http://localhost:11434"))


def test_list_models_sends_bearer_when_api_key_provided():
    """When api_key is non-empty, Authorization header is set."""
    from ollama_client import list_models

    fake_list = {"models": []}
    fake_client = AsyncMock()
    fake_client.list = AsyncMock(return_value=fake_list)

    captured = {}
    def _factory(*args, **kwargs):
        captured.update(kwargs)
        return fake_client

    with patch("ollama_client.AsyncClient", side_effect=_factory):
        asyncio.run(list_models("https://ollama.com/v1", api_key="secret"))

    headers = captured.get("headers", {})
    assert headers.get("Authorization") == "Bearer secret"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest backend/tests/test_ollama_client.py -v`
Expected: collection failure or 6 fails — `ollama_client` module not found.

- [ ] **Step 3: Implement `ollama_client.py`**

Create `backend/ollama_client.py`:
```python
"""Thin async wrappers over the official `ollama` SDK.

Responsibility: keep non-chat HTTP operations out of llm_utils.py.
The chat path lives in llm_utils._call_ollama; this module owns
/api/tags, /api/show, /api/ps, and the shared exception classes.
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

import httpx
from ollama import AsyncClient, ResponseError


class OllamaConnectionError(Exception):
    """Network failure reaching the Ollama host."""


class OllamaAuthError(Exception):
    """Authentication failure (only meaningful for Ollama Cloud)."""


class OllamaProviderError(Exception):
    """Any other non-2xx response from Ollama."""


def _auth_headers(api_key: Optional[str]) -> dict[str, str]:
    if api_key:
        return {"Authorization": f"Bearer {api_key}"}
    return {}


_CONTEXT_LENGTH_SUFFIX = ".context_length"


def _extract_context_length(show_response: dict[str, Any]) -> Optional[int]:
    """Pull the per-family context_length out of /api/show.

    Ollama keys this as `<family>.context_length` inside model_info, e.g.
    `llama.context_length` or `qwen2.context_length`. The family varies
    per model, so we scan the keys instead of hardcoding.
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


async def list_models(
    base_url: str,
    api_key: Optional[str] = None,
    *,
    timeout_sec: float = 8.0,
) -> list[dict[str, Any]]:
    """Return installed models. If <=20, also enrich each with context_length."""
    client = AsyncClient(host=base_url, headers=_auth_headers(api_key),
                        timeout=timeout_sec)
    try:
        resp = await client.list()
    except ResponseError as e:
        if (e.status_code or 0) == 401:
            raise OllamaAuthError(f"Unauthorized at {base_url}") from e
        raise OllamaProviderError(
            f"Ollama returned {e.status_code} listing models: {e}"
        ) from e
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout,
            httpx.RemoteProtocolError, ConnectionError) as e:
        raise OllamaConnectionError(f"Could not reach Ollama at {base_url}") from e

    raw_models = resp.get("models") or []
    tags = [_project_model(m) for m in raw_models]

    if 0 < len(tags) <= 20:
        sem = asyncio.Semaphore(4)

        async def _enrich(tag: dict[str, Any]) -> None:
            async with sem:
                try:
                    detail = await client.show(tag["name"])
                    tag["context_length"] = _extract_context_length(detail)
                except Exception:
                    tag["context_length"] = None

        await asyncio.gather(*(_enrich(t) for t in tags))

    return tags
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest backend/tests/test_ollama_client.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/ollama_client.py backend/tests/test_ollama_client.py
git commit -m "feat(ollama_client): add exception classes + list_models with context enrichment"
```

---

### Task 3: Add `show_model` to `ollama_client.py`

**Files:**
- Modify: `backend/ollama_client.py`
- Modify: `backend/tests/test_ollama_client.py`

- [ ] **Step 1: Write failing tests**

Append to `backend/tests/test_ollama_client.py`:
```python
def test_show_model_returns_raw_response():
    from ollama_client import show_model

    expected = {
        "model_info": {"llama.context_length": 131072},
        "capabilities": ["completion", "tools"],
    }
    fake_client = AsyncMock()
    fake_client.show = AsyncMock(return_value=expected)
    with patch("ollama_client.AsyncClient", return_value=fake_client):
        result = asyncio.run(show_model("http://localhost:11434", None, "llama3.2"))
    assert result == expected
    fake_client.show.assert_awaited_once_with("llama3.2")


def test_show_model_auth_error_on_401():
    from ollama import ResponseError
    from ollama_client import show_model, OllamaAuthError

    fake_client = AsyncMock()
    fake_client.show = AsyncMock(side_effect=ResponseError("unauthorized", 401))
    with patch("ollama_client.AsyncClient", return_value=fake_client):
        with pytest.raises(OllamaAuthError):
            asyncio.run(show_model("https://ollama.com/v1", "bad", "any"))


def test_show_model_connection_error_on_network_failure():
    import httpx
    from ollama_client import show_model, OllamaConnectionError

    fake_client = AsyncMock()
    fake_client.show = AsyncMock(side_effect=httpx.ConnectError("refused"))
    with patch("ollama_client.AsyncClient", return_value=fake_client):
        with pytest.raises(OllamaConnectionError):
            asyncio.run(show_model("http://localhost:11434", None, "llama3.2"))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest backend/tests/test_ollama_client.py -v -k show_model`
Expected: 3 fails — `show_model` not importable.

- [ ] **Step 3: Add `show_model`**

Append to `backend/ollama_client.py`:
```python
async def show_model(
    base_url: str,
    api_key: Optional[str],
    model: str,
    *,
    timeout_sec: float = 8.0,
) -> dict[str, Any]:
    """Return /api/show for a specific model (raw response, no projection)."""
    client = AsyncClient(host=base_url, headers=_auth_headers(api_key),
                        timeout=timeout_sec)
    try:
        return await client.show(model)
    except ResponseError as e:
        if (e.status_code or 0) == 401:
            raise OllamaAuthError(f"Unauthorized at {base_url}") from e
        raise OllamaProviderError(
            f"Ollama returned {e.status_code} for show({model}): {e}"
        ) from e
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout,
            httpx.RemoteProtocolError, ConnectionError) as e:
        raise OllamaConnectionError(f"Could not reach Ollama at {base_url}") from e
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest backend/tests/test_ollama_client.py -v -k show_model`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/ollama_client.py backend/tests/test_ollama_client.py
git commit -m "feat(ollama_client): add show_model"
```

---

### Task 4: Add `health_check` to `ollama_client.py`

**Files:**
- Modify: `backend/ollama_client.py`
- Modify: `backend/tests/test_ollama_client.py`

- [ ] **Step 1: Write failing tests**

Append to `backend/tests/test_ollama_client.py`:
```python
def test_health_check_ok_when_list_succeeds():
    from ollama_client import health_check

    fake_client = AsyncMock()
    fake_client.list = AsyncMock(return_value={"models": []})
    with patch("ollama_client.AsyncClient", return_value=fake_client):
        ok, msg = asyncio.run(health_check("http://localhost:11434"))
    assert ok is True
    assert msg == ""


def test_health_check_false_on_connection_error_with_message():
    import httpx
    from ollama_client import health_check

    fake_client = AsyncMock()
    fake_client.list = AsyncMock(side_effect=httpx.ConnectError("refused"))
    with patch("ollama_client.AsyncClient", return_value=fake_client):
        ok, msg = asyncio.run(health_check("http://localhost:11434"))
    assert ok is False
    assert "localhost:11434" in msg


def test_health_check_false_on_auth_failure():
    from ollama import ResponseError
    from ollama_client import health_check

    fake_client = AsyncMock()
    fake_client.list = AsyncMock(side_effect=ResponseError("unauthorized", 401))
    with patch("ollama_client.AsyncClient", return_value=fake_client):
        ok, msg = asyncio.run(health_check("https://ollama.com/v1", "bad"))
    assert ok is False
    assert "auth" in msg.lower() or "unauthorized" in msg.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest backend/tests/test_ollama_client.py -v -k health_check`
Expected: 3 fails.

- [ ] **Step 3: Add `health_check`**

Append to `backend/ollama_client.py`:
```python
async def health_check(
    base_url: str,
    api_key: Optional[str] = None,
    *,
    timeout_sec: float = 4.0,
) -> tuple[bool, str]:
    """Cheap probe: hit /api/tags. Returns (ok, error_message)."""
    try:
        await list_models(base_url, api_key, timeout_sec=timeout_sec)
        return True, ""
    except OllamaAuthError as e:
        return False, f"Unauthorized ({e})"
    except OllamaConnectionError as e:
        return False, str(e)
    except OllamaProviderError as e:
        return False, str(e)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest backend/tests/test_ollama_client.py -v`
Expected: 12 passed (all tests in this file).

- [ ] **Step 5: Commit**

```bash
git add backend/ollama_client.py backend/tests/test_ollama_client.py
git commit -m "feat(ollama_client): add health_check"
```

---

## Phase 2 — Backend LLM integration

### Task 5: Add `_normalize_tools_to_openai_shape` helper to `llm_utils.py`

**Files:**
- Modify: `backend/llm_utils.py` (add helper near other private helpers, e.g. just below `_omit_temperature` around line ~488)
- Create: `backend/tests/test_llm_utils_ollama_tools.py`

- [ ] **Step 1: Write failing tests**

Create `backend/tests/test_llm_utils_ollama_tools.py`:
```python
import pytest


def test_normalize_openai_shape_passthrough():
    from llm_utils import _normalize_tools_to_openai_shape

    tools = [{
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "fetch weather",
            "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
        },
    }]
    assert _normalize_tools_to_openai_shape(tools) == tools


def test_normalize_gemini_shape_flattens_function_declarations():
    from llm_utils import _normalize_tools_to_openai_shape

    gemini = [{
        "function_declarations": [
            {"name": "get_weather", "description": "fetch weather",
             "parameters": {"type": "object",
                            "properties": {"city": {"type": "string"}}}},
            {"name": "get_news", "description": "fetch news",
             "parameters": {"type": "object",
                            "properties": {"topic": {"type": "string"}}}},
        ],
    }]

    out = _normalize_tools_to_openai_shape(gemini)
    assert len(out) == 2
    for o in out:
        assert o["type"] == "function"
        assert set(o["function"].keys()) >= {"name", "parameters"}
    assert {o["function"]["name"] for o in out} == {"get_weather", "get_news"}


def test_normalize_rejects_unknown_shape():
    from llm_utils import _normalize_tools_to_openai_shape

    with pytest.raises(ValueError):
        _normalize_tools_to_openai_shape([{"random": "shape"}])


def test_normalize_empty_list_returns_empty_list():
    from llm_utils import _normalize_tools_to_openai_shape
    assert _normalize_tools_to_openai_shape([]) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest backend/tests/test_llm_utils_ollama_tools.py -v -k normalize`
Expected: 4 fails — helper not defined.

- [ ] **Step 3: Add the helper**

In `backend/llm_utils.py`, add this function near other private helpers (search for `_omit_temperature` and add below it):
```python
def _normalize_tools_to_openai_shape(tools: list[dict]) -> list[dict]:
    """Convert either Gemini- or OpenAI-shaped tool dicts into OpenAI shape.

    Gemini shape (used by `call_gemini_with_tools`):
        [{"function_declarations": [{"name": ..., "parameters": ...}, ...]}]
    OpenAI shape (used by Ollama, OpenAI, NVIDIA):
        [{"type": "function", "function": {"name": ..., "parameters": ...}}]
    """
    if not tools:
        return []
    first = tools[0]
    if isinstance(first, dict) and first.get("type") == "function":
        # Already OpenAI shape — passthrough.
        return list(tools)
    if isinstance(first, dict) and "function_declarations" in first:
        flattened: list[dict] = []
        for entry in tools:
            for fn in entry.get("function_declarations", []) or []:
                flattened.append({"type": "function", "function": dict(fn)})
        return flattened
    raise ValueError(
        "Unsupported tools shape: expected OpenAI-style "
        "[{type:function,...}] or Gemini-style [{function_declarations:[...]}]"
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest backend/tests/test_llm_utils_ollama_tools.py -v -k normalize`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/llm_utils.py backend/tests/test_llm_utils_ollama_tools.py
git commit -m "feat(llm_utils): add _normalize_tools_to_openai_shape helper"
```

---

### Task 6: Extend `resolve_api_key_for_provider` for `"ollama"`

**Files:**
- Modify: `backend/llm_utils.py` (around line 196)
- Create: `backend/tests/test_llm_utils_ollama_config.py`

- [ ] **Step 1: Write failing tests**

Create `backend/tests/test_llm_utils_ollama_config.py`:
```python
import os
import pytest


def test_resolve_api_key_for_ollama_returns_explicit_when_provided():
    from llm_utils import resolve_api_key_for_provider
    assert resolve_api_key_for_provider("ollama", "secret-xyz") == "secret-xyz"


def test_resolve_api_key_for_ollama_returns_env_var_when_no_explicit(monkeypatch):
    from llm_utils import resolve_api_key_for_provider
    monkeypatch.setenv("OLLAMA_API_KEY", "from-env-abc")
    assert resolve_api_key_for_provider("ollama", None) == "from-env-abc"


def test_resolve_api_key_for_ollama_returns_empty_when_nothing_set(monkeypatch):
    from llm_utils import resolve_api_key_for_provider
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    # Empty string is valid for local Ollama (no auth).
    assert resolve_api_key_for_provider("ollama", None) == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest backend/tests/test_llm_utils_ollama_config.py -v -k resolve_api_key`
Expected: 3 fails — `ollama` branch routes to default Gemini handler.

- [ ] **Step 3: Add the branch**

In `backend/llm_utils.py`, find `resolve_api_key_for_provider`. Locate the existing if/elif chain (it currently handles `azure`, `openai`, `deepseek`, `nvidia`, etc.) and add an `ollama` branch before the trailing fallback:
```python
if p == "ollama":
    return (
        explicit_api_key
        or os.environ.get("OLLAMA_API_KEY", "")
        or ""
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest backend/tests/test_llm_utils_ollama_config.py -v -k resolve_api_key`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/llm_utils.py backend/tests/test_llm_utils_ollama_config.py
git commit -m "feat(llm_utils): resolve_api_key_for_provider handles ollama"
```

---

### Task 7: Extend `_resolve_provider_config` for `"ollama"`

**Files:**
- Modify: `backend/llm_utils.py` (around line 553)
- Modify: `backend/tests/test_llm_utils_ollama_config.py`

- [ ] **Step 1: Write failing tests**

Append to `backend/tests/test_llm_utils_ollama_config.py`:
```python
def test_resolve_provider_config_ollama_default_base_url(monkeypatch):
    from llm_utils import _resolve_provider_config
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    out = _resolve_provider_config("ollama", {})
    assert out["ollama_base_url"] == "http://localhost:11434"


def test_resolve_provider_config_ollama_uses_env_var(monkeypatch):
    from llm_utils import _resolve_provider_config
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://REDACTED-IP:11434/")
    out = _resolve_provider_config("ollama", {})
    # Trailing slash stripped:
    assert out["ollama_base_url"] == "http://REDACTED-IP:11434"


def test_resolve_provider_config_ollama_explicit_beats_env(monkeypatch):
    from llm_utils import _resolve_provider_config
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://env-host:11434")
    out = _resolve_provider_config(
        "ollama", {"ollama_base_url": "https://ollama.com/v1/"}
    )
    assert out["ollama_base_url"] == "https://ollama.com/v1"


def test_resolve_provider_config_ollama_propagates_keep_alive():
    from llm_utils import _resolve_provider_config
    out = _resolve_provider_config(
        "ollama", {"ollama_base_url": "http://localhost:11434",
                   "ollama_keep_alive": "  60m  "}
    )
    assert out["ollama_keep_alive"] == "60m"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest backend/tests/test_llm_utils_ollama_config.py -v -k _resolve_provider_config`
Expected: 4 fails.

- [ ] **Step 3: Add the branch**

In `backend/llm_utils.py`, find `_resolve_provider_config` and add an `ollama` branch (mirroring how `nvidia` adds `base_url`):
```python
elif p == "ollama":
    base = str(
        resolved.get("ollama_base_url")
        or os.environ.get("OLLAMA_BASE_URL")
        or "http://localhost:11434"
    ).rstrip("/")
    resolved["ollama_base_url"] = base
    keep_alive = resolved.get("ollama_keep_alive")
    if keep_alive:
        resolved["ollama_keep_alive"] = str(keep_alive).strip()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest backend/tests/test_llm_utils_ollama_config.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/llm_utils.py backend/tests/test_llm_utils_ollama_config.py
git commit -m "feat(llm_utils): _resolve_provider_config handles ollama (base_url + keep_alive)"
```

---

### Task 8: Implement `_call_ollama` (plain-text chat)

**Files:**
- Modify: `backend/llm_utils.py` (add `_call_ollama` near `_call_openai`; add module-level set `_ollama_warm_pairs: set[tuple[str, str]] = set()` near other module-level state)
- Create: `backend/tests/test_llm_utils_ollama.py`

- [ ] **Step 1: Write failing tests**

Create `backend/tests/test_llm_utils_ollama.py`:
```python
from unittest.mock import MagicMock, patch
import pytest


def _fake_response(text="hi from ollama"):
    return {"message": {"content": text}, "done": True,
            "eval_count": 5, "prompt_eval_count": 10}


def test_call_ollama_happy_path_returns_message_content():
    from llm_utils import _call_ollama

    fake_client = MagicMock()
    fake_client.chat.return_value = _fake_response("hi")
    with patch("llm_utils._make_ollama_sync_client", return_value=fake_client):
        out = _call_ollama(
            api_key="", model="llama3.2", prompt="hello",
            max_output_tokens=32, base_url="http://localhost:11434",
        )
    assert out == "hi"
    call_kwargs = fake_client.chat.call_args.kwargs
    assert call_kwargs["model"] == "llama3.2"
    assert call_kwargs["messages"] == [{"role": "user", "content": "hello"}]
    assert call_kwargs["options"]["num_predict"] == 32


def test_call_ollama_json_format_when_response_mime_type_json():
    from llm_utils import _call_ollama

    fake_client = MagicMock()
    fake_client.chat.return_value = _fake_response('{"x": 1}')
    with patch("llm_utils._make_ollama_sync_client", return_value=fake_client):
        out = _call_ollama(
            api_key="", model="llama3.2", prompt="x?",
            max_output_tokens=32, base_url="http://localhost:11434",
            response_mime_type="application/json",
        )
    assert out == '{"x": 1}'
    call_kwargs = fake_client.chat.call_args.kwargs
    assert call_kwargs["format"] == "json"


def test_call_ollama_404_model_not_installed_is_not_retried():
    from ollama import ResponseError
    from llm_utils import _call_ollama
    from ollama_client import OllamaProviderError

    fake_client = MagicMock()
    fake_client.chat.side_effect = ResponseError("model 'foo' not found", 404)
    with patch("llm_utils._make_ollama_sync_client", return_value=fake_client):
        with pytest.raises(OllamaProviderError):
            _call_ollama(
                api_key="", model="foo", prompt="x",
                max_output_tokens=32, base_url="http://localhost:11434",
                retries=3,  # would otherwise retry 3 times
            )
    assert fake_client.chat.call_count == 1


def test_call_ollama_401_raises_auth_error():
    from ollama import ResponseError
    from llm_utils import _call_ollama
    from ollama_client import OllamaAuthError

    fake_client = MagicMock()
    fake_client.chat.side_effect = ResponseError("unauthorized", 401)
    with patch("llm_utils._make_ollama_sync_client", return_value=fake_client):
        with pytest.raises(OllamaAuthError):
            _call_ollama(
                api_key="bad", model="any", prompt="x",
                max_output_tokens=32, base_url="https://ollama.com/v1",
            )


def test_call_ollama_5xx_is_retried_until_retries_exhausted():
    from ollama import ResponseError
    from llm_utils import _call_ollama

    fake_client = MagicMock()
    fake_client.chat.side_effect = [
        ResponseError("oops", 500),
        ResponseError("oops", 502),
        _fake_response("recovered"),
    ]
    with patch("llm_utils._make_ollama_sync_client", return_value=fake_client), \
         patch("llm_utils._compute_backoff_seconds", return_value=0):
        out = _call_ollama(
            api_key="", model="llama3.2", prompt="x",
            max_output_tokens=32, base_url="http://localhost:11434",
            retries=2,
        )
    assert out == "recovered"
    assert fake_client.chat.call_count == 3


def test_call_ollama_keep_alive_propagated():
    from llm_utils import _call_ollama

    fake_client = MagicMock()
    fake_client.chat.return_value = _fake_response("ok")
    with patch("llm_utils._make_ollama_sync_client", return_value=fake_client):
        _call_ollama(
            api_key="", model="llama3.2", prompt="x",
            max_output_tokens=8, base_url="http://localhost:11434",
            keep_alive="60m",
        )
    call_kwargs = fake_client.chat.call_args.kwargs
    assert call_kwargs.get("keep_alive") == "60m"


def test_call_ollama_warms_pair_after_first_success():
    from llm_utils import _call_ollama, _ollama_warm_pairs

    _ollama_warm_pairs.clear()
    fake_client = MagicMock()
    fake_client.chat.return_value = _fake_response("ok")
    with patch("llm_utils._make_ollama_sync_client", return_value=fake_client):
        _call_ollama(
            api_key="", model="llama3.2", prompt="x",
            max_output_tokens=8, base_url="http://localhost:11434",
        )
    assert ("http://localhost:11434", "llama3.2") in _ollama_warm_pairs
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest backend/tests/test_llm_utils_ollama.py -v`
Expected: 7 fails — `_call_ollama` not defined, `_make_ollama_sync_client` not defined.

- [ ] **Step 3: Implement `_call_ollama`**

In `backend/llm_utils.py`:

(a) Near the top of the file (after the other module-level state — search for an existing module-level dict/set near the rate-limiter code), add:
```python
_ollama_warm_pairs: set[tuple[str, str]] = set()
```

(b) Add a factory helper near other helpers:
```python
def _make_ollama_sync_client(base_url: str, api_key: str | None, timeout: float):
    """Wrapper so tests can patch client creation."""
    from ollama import Client
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    return Client(host=base_url, headers=headers, timeout=timeout)


def _resolve_ollama_timeout(base_url: str, model: str, explicit_timeout) -> float:
    """120s on cold pair (first call since process boot), 30s once warm."""
    if explicit_timeout is not None:
        try:
            return float(explicit_timeout)
        except (TypeError, ValueError):
            pass
    if (base_url, model) in _ollama_warm_pairs:
        return 30.0
    return 120.0
```

(c) Add `_call_ollama` after `_call_openai`:
```python
def _call_ollama(
    api_key: str | None,
    model: str,
    prompt: str,
    max_output_tokens: int = 256,
    timeout_sec=None,
    retries: int = 0,
    base_url: str = "http://localhost:11434",
    response_mime_type=None,
    reasoning_effort: str = "",   # accepted but ignored — model-specific
    keep_alive: str | None = None,
) -> str:
    import time
    from ollama import ResponseError
    import httpx
    from ollama_client import (
        OllamaAuthError, OllamaConnectionError, OllamaProviderError,
    )

    options: dict[str, object] = {}
    if max_output_tokens:
        options["num_predict"] = int(max_output_tokens)

    chat_kwargs: dict[str, object] = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "options": options,
    }
    if response_mime_type and "json" in str(response_mime_type).lower():
        chat_kwargs["format"] = "json"
    if keep_alive:
        chat_kwargs["keep_alive"] = keep_alive

    attempt = 0
    last_exc: Exception | None = None
    while attempt <= retries:
        try:
            timeout = _resolve_ollama_timeout(base_url, model, timeout_sec)
            client = _make_ollama_sync_client(base_url, api_key, timeout)
            t0 = time.monotonic()
            resp = client.chat(**chat_kwargs)
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            text = ((resp or {}).get("message") or {}).get("content") or ""
            try:
                _safe_record(
                    provider="ollama", model=model, ms=elapsed_ms, ok=True,
                    prompt_tokens=(resp or {}).get("prompt_eval_count"),
                    completion_tokens=(resp or {}).get("eval_count"),
                )
            except Exception:
                pass
            _ollama_warm_pairs.add((base_url, model))
            return text
        except ResponseError as e:
            status = e.status_code or 0
            last_exc = e
            if status == 401:
                raise OllamaAuthError(f"Unauthorized at {base_url}") from e
            if status == 404:
                # Model not installed — don't retry; user-config issue.
                raise OllamaProviderError(
                    f"Ollama model not found: {model} (at {base_url})"
                ) from e
            if status == 429 or 500 <= status < 600:
                if attempt < retries:
                    time.sleep(_compute_backoff_seconds(attempt, status))
                    attempt += 1
                    continue
            raise OllamaProviderError(
                f"Ollama {status}: {e}"
            ) from e
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout,
                httpx.RemoteProtocolError, ConnectionError, TimeoutError) as e:
            last_exc = e
            if attempt < retries:
                time.sleep(_compute_backoff_seconds(attempt, None))
                attempt += 1
                continue
            raise OllamaConnectionError(
                f"Could not reach Ollama at {base_url}: {e}"
            ) from e

    # Should not reach here; raise a generic provider error if it does.
    raise OllamaProviderError(f"Ollama call failed: {last_exc}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest backend/tests/test_llm_utils_ollama.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/llm_utils.py backend/tests/test_llm_utils_ollama.py
git commit -m "feat(llm_utils): add _call_ollama plain-text chat with retry + warm-pair tracking"
```

---

### Task 9: Wire dispatcher in `call_llm_by_provider`

**Files:**
- Modify: `backend/llm_utils.py` (around line 4527)
- Modify: `backend/tests/test_llm_utils_ollama.py`

- [ ] **Step 1: Write failing tests**

Append to `backend/tests/test_llm_utils_ollama.py`:
```python
def test_dispatcher_routes_provider_ollama():
    from llm_utils import call_llm_by_provider

    captured = {}
    def _fake(api_key, model, prompt, **kwargs):
        captured["model"] = model
        captured["prompt"] = prompt
        captured["base_url"] = kwargs.get("base_url")
        return "dispatcher-ok"

    with patch("llm_utils._call_ollama", side_effect=_fake):
        out = call_llm_by_provider(
            provider="ollama",
            api_key="",
            model="llama3.2",
            prompt="ping",
            max_output_tokens=16,
            provider_config={"ollama_base_url": "http://localhost:11434"},
        )
    assert out == "dispatcher-ok"
    assert captured["model"] == "llama3.2"
    assert captured["base_url"] == "http://localhost:11434"
```

- [ ] **Step 2: Run tests to verify it fails**

Run: `pytest backend/tests/test_llm_utils_ollama.py::test_dispatcher_routes_provider_ollama -v`
Expected: FAIL — currently falls through to `_call_gemini`.

- [ ] **Step 3: Add dispatcher branch**

In `backend/llm_utils.py`, find `call_llm_by_provider` near line 4434. Add this branch in the if/elif chain just before the final `else: _result = _call_gemini(...)`:
```python
elif p == "ollama":
    _result = _call_ollama(
        api_key, model, prompt,
        max_output_tokens=max_output_tokens,
        timeout_sec=timeout_sec, retries=retries,
        base_url=str(resolved.get("ollama_base_url") or "http://localhost:11434"),
        response_mime_type=response_mime_type,
        keep_alive=resolved.get("ollama_keep_alive"),
    )
```

- [ ] **Step 4: Run tests to verify it passes**

Run: `pytest backend/tests/test_llm_utils_ollama.py -v`
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/llm_utils.py backend/tests/test_llm_utils_ollama.py
git commit -m "feat(llm_utils): wire ollama into call_llm_by_provider dispatcher"
```

---

### Task 10: Implement `_call_ollama_structured_from_strategy`

**Files:**
- Modify: `backend/llm_utils.py`
- Create: `backend/tests/test_llm_utils_ollama_structured.py`

- [ ] **Step 1: Write failing tests**

Create `backend/tests/test_llm_utils_ollama_structured.py`:
```python
from unittest.mock import MagicMock, patch
from pydantic import BaseModel


class _Schema(BaseModel):
    answer: str
    confidence: float


def test_structured_uses_openai_compat_endpoint():
    """PydanticAI structured-output path points at <base_url>/v1."""
    from llm_utils import _call_ollama_structured_from_strategy

    fake_agent_result = MagicMock()
    fake_agent_result.output = _Schema(answer="42", confidence=0.9)

    fake_agent = MagicMock()
    fake_agent.run_sync.return_value = fake_agent_result

    captured = {}
    def _fake_model_ctor(model_name, provider=None, **_kwargs):
        captured["model_name"] = model_name
        captured["provider"] = provider
        return MagicMock()

    with patch("llm_utils.OpenAIChatModel", side_effect=_fake_model_ctor), \
         patch("llm_utils.OpenAIProvider") as fake_prov, \
         patch("llm_utils.Agent", return_value=fake_agent):
        out = _call_ollama_structured_from_strategy(
            api_key="",
            model="llama3.2",
            prompt="What?",
            output_type=_Schema,
            base_url="http://localhost:11434",
        )

    assert isinstance(out, _Schema)
    assert out.answer == "42"
    # OpenAIProvider was created with the /v1 base url:
    prov_kwargs = fake_prov.call_args.kwargs
    assert prov_kwargs["base_url"].endswith("/v1")
    assert prov_kwargs["base_url"].startswith("http://localhost:11434")
    assert prov_kwargs["api_key"]  # non-empty (real key or sentinel "ollama")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest backend/tests/test_llm_utils_ollama_structured.py -v`
Expected: FAIL — function not defined.

- [ ] **Step 3: Implement `_call_ollama_structured_from_strategy`**

Read `_call_openai_structured_from_strategy` in `backend/llm_utils.py` first to mirror its exact shape — it's the closest analogue (also OpenAI-compatible). Then add this near it:
```python
def _call_ollama_structured_from_strategy(
    *,
    api_key: str | None,
    model: str,
    prompt: str,
    output_type,
    system_prompt: str | None = None,
    base_url: str = "http://localhost:11434",
    timeout_sec=None,
    retries: int = 0,
    output_retries: int | None = None,
    response_mime_type=None,
):
    """PydanticAI structured output via Ollama's OpenAI-compatible /v1 endpoint.

    Uses OpenAIChatModel pointed at <base_url>/v1 — Ollama exposes JSON-schema
    structured output through this path as of v0.5+.
    """
    base = base_url.rstrip("/")
    if not base.endswith("/v1"):
        base = base + "/v1"
    provider = OpenAIProvider(base_url=base, api_key=(api_key or "ollama"))
    model_obj = OpenAIChatModel(model, provider=provider)

    agent_kwargs: dict[str, object] = {"output_type": output_type}
    if system_prompt:
        agent_kwargs["system_prompt"] = system_prompt
    if output_retries is not None:
        agent_kwargs["output_retries"] = output_retries

    agent = Agent(model_obj, **agent_kwargs)
    result = agent.run_sync(prompt)
    return result.output
```

**Note:** `OpenAIChatModel`, `OpenAIProvider`, and `Agent` must already be imported at the top of `llm_utils.py` (they are — used by the existing OpenAI structured path). If not, add `from pydantic_ai import Agent` and `from pydantic_ai.models.openai import OpenAIChatModel` and `from pydantic_ai.providers.openai import OpenAIProvider` (verify exact import paths against existing imports).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest backend/tests/test_llm_utils_ollama_structured.py -v`
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/llm_utils.py backend/tests/test_llm_utils_ollama_structured.py
git commit -m "feat(llm_utils): add _call_ollama_structured_from_strategy via OpenAI-compat /v1"
```

---

### Task 11: Wire structured dispatcher

**Files:**
- Modify: `backend/llm_utils.py` (`call_structured_llm_by_provider`)
- Modify: `backend/tests/test_llm_utils_ollama_structured.py`

- [ ] **Step 1: Write failing test**

Append to `backend/tests/test_llm_utils_ollama_structured.py`:
```python
def test_call_structured_llm_by_provider_routes_ollama():
    from llm_utils import call_structured_llm_by_provider

    captured = {}
    def _fake(**kwargs):
        captured.update(kwargs)
        return _Schema(answer="ok", confidence=1.0)

    with patch("llm_utils._call_ollama_structured_from_strategy", side_effect=_fake):
        out = call_structured_llm_by_provider(
            provider="ollama",
            api_key="",
            model="llama3.2",
            prompt="x",
            output_type=_Schema,
            provider_config={"ollama_base_url": "http://localhost:11434"},
        )
    assert out.answer == "ok"
    assert captured["base_url"] == "http://localhost:11434"
    assert captured["model"] == "llama3.2"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest backend/tests/test_llm_utils_ollama_structured.py -v -k routes_ollama`
Expected: FAIL — falls through to default branch.

- [ ] **Step 3: Add branch**

In `call_structured_llm_by_provider` in `backend/llm_utils.py`, find the existing if/elif chain (mirrors `call_llm_by_provider`) and add before the trailing fallback:
```python
elif p == "ollama":
    _result = _call_ollama_structured_from_strategy(
        api_key=api_key,
        model=model,
        prompt=prompt,
        output_type=output_type,
        system_prompt=system_prompt,
        base_url=str(resolved.get("ollama_base_url") or "http://localhost:11434"),
        timeout_sec=timeout_sec,
        retries=retries,
        output_retries=output_retries,
    )
```

(Use whatever local variable names exist in the surrounding code — `output_retries` may be named slightly differently; mirror the OpenAI branch immediately above.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest backend/tests/test_llm_utils_ollama_structured.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/llm_utils.py backend/tests/test_llm_utils_ollama_structured.py
git commit -m "feat(llm_utils): route call_structured_llm_by_provider to ollama"
```

---

### Task 12: Implement `call_ollama_with_tools`

**Files:**
- Modify: `backend/llm_utils.py`
- Modify: `backend/tests/test_llm_utils_ollama_tools.py`

- [ ] **Step 1: Write failing tests**

Append to `backend/tests/test_llm_utils_ollama_tools.py`:
```python
from unittest.mock import MagicMock, patch


def test_call_ollama_with_tools_passes_normalised_tools_and_returns_dict():
    from llm_utils import call_ollama_with_tools

    fake_resp = {
        "message": {
            "content": "calling tool",
            "tool_calls": [
                {"function": {"name": "get_weather",
                              "arguments": {"city": "SF"}}}
            ],
        }
    }
    fake_client = MagicMock()
    fake_client.chat.return_value = fake_resp

    tools = [{
        "type": "function",
        "function": {"name": "get_weather", "description": "fetch",
                     "parameters": {"type": "object",
                                    "properties": {"city": {"type": "string"}}}},
    }]

    with patch("llm_utils._make_ollama_sync_client", return_value=fake_client):
        out = call_ollama_with_tools(
            api_key="", model="qwen2.5", prompt="weather in SF?",
            tools=tools, base_url="http://localhost:11434",
        )

    assert out["text"] == "calling tool"
    assert len(out["tool_calls"]) == 1
    assert out["tool_calls"][0]["name"] == "get_weather"
    assert out["tool_calls"][0]["arguments"] == {"city": "SF"}

    call_kwargs = fake_client.chat.call_args.kwargs
    assert call_kwargs["tools"] == tools


def test_call_ollama_with_tools_accepts_gemini_shape():
    from llm_utils import call_ollama_with_tools

    fake_client = MagicMock()
    fake_client.chat.return_value = {
        "message": {"content": "ok", "tool_calls": []}
    }
    gemini_tools = [{
        "function_declarations": [
            {"name": "get_weather", "description": "fetch",
             "parameters": {"type": "object",
                            "properties": {"city": {"type": "string"}}}},
        ],
    }]

    with patch("llm_utils._make_ollama_sync_client", return_value=fake_client):
        out = call_ollama_with_tools(
            api_key="", model="qwen2.5", prompt="x",
            tools=gemini_tools, base_url="http://localhost:11434",
        )

    sent_tools = fake_client.chat.call_args.kwargs["tools"]
    assert sent_tools[0]["type"] == "function"
    assert sent_tools[0]["function"]["name"] == "get_weather"
    assert out["text"] == "ok"
    assert out["tool_calls"] == []


def test_call_ollama_with_tools_handles_response_without_tool_calls():
    """Non-tool model returns prose only — tool_calls is empty list."""
    from llm_utils import call_ollama_with_tools

    fake_client = MagicMock()
    fake_client.chat.return_value = {
        "message": {"content": "I will not call any tool today."}
    }
    with patch("llm_utils._make_ollama_sync_client", return_value=fake_client):
        out = call_ollama_with_tools(
            api_key="", model="llama3.2", prompt="?",
            tools=[{"type": "function",
                    "function": {"name": "noop",
                                 "parameters": {"type": "object"}}}],
            base_url="http://localhost:11434",
        )
    assert out["text"].startswith("I will not")
    assert out["tool_calls"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest backend/tests/test_llm_utils_ollama_tools.py -v -k call_ollama_with_tools`
Expected: 3 fails — function not defined.

- [ ] **Step 3: Add `call_ollama_with_tools`**

In `backend/llm_utils.py`, add near `call_gemini_with_tools`:
```python
def call_ollama_with_tools(
    api_key: str | None,
    model: str,
    prompt: str,
    tools: list[dict],
    *,
    base_url: str = "http://localhost:11434",
    timeout_sec=None,
    max_output_tokens: int = 1024,
    keep_alive: str | None = None,
) -> dict:
    """Tool-using chat against Ollama.

    Accepts OpenAI-shape OR Gemini-shape tool dicts (normalised internally).
    Returns the same shape as call_gemini_with_tools:
        {"text": str, "tool_calls": [{"name": str, "arguments": dict}, ...]}
    """
    import time
    from ollama import ResponseError
    import httpx
    from ollama_client import (
        OllamaAuthError, OllamaConnectionError, OllamaProviderError,
    )

    normalised = _normalize_tools_to_openai_shape(tools or [])
    options: dict[str, object] = {}
    if max_output_tokens:
        options["num_predict"] = int(max_output_tokens)

    chat_kwargs: dict[str, object] = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "tools": normalised,
        "options": options,
    }
    if keep_alive:
        chat_kwargs["keep_alive"] = keep_alive

    timeout = _resolve_ollama_timeout(base_url, model, timeout_sec)
    client = _make_ollama_sync_client(base_url, api_key, timeout)

    try:
        t0 = time.monotonic()
        resp = client.chat(**chat_kwargs)
        elapsed_ms = int((time.monotonic() - t0) * 1000)
    except ResponseError as e:
        status = e.status_code or 0
        if status == 401:
            raise OllamaAuthError(f"Unauthorized at {base_url}") from e
        if status == 404:
            raise OllamaProviderError(
                f"Ollama model not found: {model} (at {base_url})"
            ) from e
        raise OllamaProviderError(f"Ollama {status}: {e}") from e
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout,
            httpx.RemoteProtocolError, ConnectionError, TimeoutError) as e:
        raise OllamaConnectionError(
            f"Could not reach Ollama at {base_url}: {e}"
        ) from e

    msg = (resp or {}).get("message") or {}
    text = msg.get("content") or ""
    raw_calls = msg.get("tool_calls") or []
    tool_calls = []
    for tc in raw_calls:
        fn = (tc or {}).get("function") or {}
        args = fn.get("arguments")
        if isinstance(args, str):
            # Ollama may stringify args — normalise to dict.
            try:
                import json
                args = json.loads(args)
            except Exception:
                args = {"_raw": args}
        tool_calls.append({"name": fn.get("name", ""), "arguments": args or {}})

    try:
        _safe_record(
            provider="ollama", model=model, ms=elapsed_ms, ok=True,
            prompt_tokens=(resp or {}).get("prompt_eval_count"),
            completion_tokens=(resp or {}).get("eval_count"),
        )
    except Exception:
        pass
    _ollama_warm_pairs.add((base_url, model))

    return {"text": text, "tool_calls": tool_calls}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest backend/tests/test_llm_utils_ollama_tools.py -v`
Expected: 7 passed (4 normalize + 3 with_tools).

- [ ] **Step 5: Commit**

```bash
git add backend/llm_utils.py backend/tests/test_llm_utils_ollama_tools.py
git commit -m "feat(llm_utils): add call_ollama_with_tools (OpenAI + Gemini shape input)"
```

---

### Task 13: Extend `llm_critical_guard.classify` for Ollama

**Files:**
- Modify: `backend/llm_critical_guard.py`
- Create: `backend/tests/test_llm_critical_guard_ollama.py`

- [ ] **Step 1: Write failing tests**

Read `backend/llm_critical_guard.py` first to learn the `classify()` signature. Then create `backend/tests/test_llm_critical_guard_ollama.py`:
```python
import pytest


def test_classify_ollama_401_is_auth_failure_critical():
    """An Ollama Cloud 401 must classify as auth_failure (critical)."""
    from llm_critical_guard import classify

    tag, is_critical = classify(
        provider="ollama",
        model="llama3.2",
        status_code=401,
        body="unauthorized",
        exception=None,
    )
    assert tag == "auth_failure"
    assert is_critical is True


def test_classify_ollama_OllamaAuthError_is_auth_failure_critical():
    from llm_critical_guard import classify
    from ollama_client import OllamaAuthError

    tag, is_critical = classify(
        provider="ollama",
        model="llama3.2",
        status_code=None,
        body=None,
        exception=OllamaAuthError("bad key"),
    )
    assert tag == "auth_failure"
    assert is_critical is True


def test_classify_ollama_404_not_critical():
    """Model-not-installed (404) is user config, not a provider outage."""
    from llm_critical_guard import classify

    tag, is_critical = classify(
        provider="ollama",
        model="ghost",
        status_code=404,
        body="model not found",
        exception=None,
    )
    assert tag == "none"
    assert is_critical is False


def test_classify_ollama_persistent_5xx_after_three(monkeypatch):
    """Three consecutive 5xx for the same (provider, model) → critical."""
    from llm_critical_guard import classify, _reset_for_tests
    _reset_for_tests()
    for _ in range(2):
        tag, is_critical = classify(
            provider="ollama", model="llama3.2", status_code=500,
            body="oops", exception=None,
        )
        assert is_critical is False
    tag, is_critical = classify(
        provider="ollama", model="llama3.2", status_code=502,
        body="oops", exception=None,
    )
    assert tag == "provider_5xx_persistent"
    assert is_critical is True


def test_classify_ollama_200_resets_5xx_counter():
    from llm_critical_guard import classify, _reset_for_tests
    _reset_for_tests()
    for _ in range(2):
        classify(provider="ollama", model="llama3.2",
                 status_code=500, body="oops", exception=None)
    # Success resets:
    classify(provider="ollama", model="llama3.2",
             status_code=200, body=None, exception=None)
    # Now a single 500 should not yet be critical:
    tag, is_critical = classify(
        provider="ollama", model="llama3.2",
        status_code=500, body="oops", exception=None,
    )
    assert is_critical is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest backend/tests/test_llm_critical_guard_ollama.py -v`
Expected: collection errors or fails — needs adapter to existing classify signature.

If the existing `classify()` signature differs (e.g., takes a single dict / exception only), adjust the tests to match the existing call shape BEFORE implementing the branch. Read `backend/llm_critical_guard.py` to confirm — the spec says it already tracks per-`(provider, model)` 5xx counter and Ollama "participates automatically" once it appears in dispatch. The tests above are an architectural target; tweak signatures as needed.

- [ ] **Step 3: Implement the Ollama branch**

In `backend/llm_critical_guard.py`, inside `classify()`, add (mirroring existing branches like Azure abuse-monitor):
```python
# Ollama auth failure (cloud only — local never 401s).
if (provider or "").strip().lower() == "ollama":
    try:
        from ollama_client import OllamaAuthError
    except ImportError:  # ollama_client module missing during early tests
        OllamaAuthError = None  # type: ignore
    if status_code == 401:
        return "auth_failure", True
    if exception is not None and OllamaAuthError is not None \
            and isinstance(exception, OllamaAuthError):
        return "auth_failure", True
    body_str = (body or "")
    if isinstance(body_str, str) and (
        "unauthorized" in body_str.lower() or "api key" in body_str.lower()
    ):
        return "auth_failure", True
# The existing per-(provider, model) 5xx counter handles persistent_5xx
# transparently — no Ollama-specific code needed there.
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest backend/tests/test_llm_critical_guard_ollama.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/llm_critical_guard.py backend/tests/test_llm_critical_guard_ollama.py
git commit -m "feat(llm_critical_guard): classify ollama 401 as auth_failure"
```

---

## Phase 3 — API + storage

### Task 14: Extend `_validate_provider_model_compat` for `"ollama"`

**Files:**
- Modify: `backend/interactive_utils.py` (around line 7916, function `_validate_provider_model_compat`)
- Create: `backend/tests/test_models_api_ollama.py`

- [ ] **Step 1: Write failing test**

Create `backend/tests/test_models_api_ollama.py`:
```python
import pytest


def test_validate_provider_model_compat_accepts_any_non_empty_ollama_model():
    from interactive_utils import _validate_provider_model_compat
    # Should not raise:
    _validate_provider_model_compat("ollama", "llama3.2")
    _validate_provider_model_compat("ollama", "qwen2.5:14b")
    _validate_provider_model_compat("ollama", "gpt-oss:120b-cloud")
    _validate_provider_model_compat("ollama", "my-custom-modelfile")


def test_validate_provider_model_compat_rejects_empty_ollama_model():
    from interactive_utils import _validate_provider_model_compat
    with pytest.raises((ValueError, Exception)):
        _validate_provider_model_compat("ollama", "")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest backend/tests/test_models_api_ollama.py -v -k validate_provider_model_compat`
Expected: errors — the current validator may not know `"ollama"`.

- [ ] **Step 3: Add the branch**

Read `_validate_provider_model_compat` in `backend/interactive_utils.py` to understand the existing pattern. Then add an `"ollama"` arm — permissive, accepting any non-empty model. Conceptually:
```python
if provider == "ollama":
    if not (model and model.strip()):
        raise ValueError("ollama provider requires a non-empty model name")
    return  # any non-empty string is valid
```

Match the existing function's exception type and early-return convention; do not introduce a new exception class just for this.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest backend/tests/test_models_api_ollama.py -v -k validate_provider_model_compat`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/interactive_utils.py backend/tests/test_models_api_ollama.py
git commit -m "feat(interactive_utils): _validate_provider_model_compat accepts ollama (permissive)"
```

---

### Task 15: Extend `CreateModelBody` + `EditModelBody` with Ollama fields

**Files:**
- Modify: `backend/api/main.py` (search for `class CreateModelBody`)
- Modify: `backend/tests/test_models_api_ollama.py`

- [ ] **Step 1: Write failing tests**

Append to `backend/tests/test_models_api_ollama.py`:
```python
def test_create_model_body_accepts_ollama_fields():
    # Avoid app startup side effects — import only the body schemas.
    from api.main import CreateModelBody
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
    from api.main import EditModelBody
    body = EditModelBody(
        ollama_base_url="https://ollama.com/v1",
        ollama_keep_alive="60m",
    )
    assert body.ollama_base_url == "https://ollama.com/v1"
    assert body.ollama_keep_alive == "60m"


def test_create_model_body_ollama_base_url_length_capped():
    from api.main import CreateModelBody
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        CreateModelBody(
            name="x", provider="ollama", model="llama3.2",
            ollama_base_url="x" * 600,
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest backend/tests/test_models_api_ollama.py -v -k model_body`
Expected: 3 fails — fields don't exist.

- [ ] **Step 3: Add fields**

In `backend/api/main.py`, find `class CreateModelBody(BaseModel):` and add:
```python
ollama_base_url:   Optional[str] = Field(None, max_length=512)
ollama_keep_alive: Optional[str] = Field(None, max_length=16)
```

Do the same for `class EditModelBody(BaseModel):`. Also ensure that the action functions (`action_create_model`, `action_edit_model`) in `backend/interactive_utils.py` accept these kwargs and persist them on the Model doc. Find the existing pattern (e.g., where `nvidia_base_url` is handled) and mirror it — add `ollama_base_url` and `ollama_keep_alive` to the persisted dict.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest backend/tests/test_models_api_ollama.py -v -k model_body`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/api/main.py backend/interactive_utils.py backend/tests/test_models_api_ollama.py
git commit -m "feat(models): add ollama_base_url + ollama_keep_alive fields to CRUD"
```

---

### Task 16: Add `POST /ollama/list-models` endpoint

**Files:**
- Modify: `backend/api/main.py`
- Create: `backend/tests/test_ollama_list_models_endpoint.py`

- [ ] **Step 1: Write failing tests**

Create `backend/tests/test_ollama_list_models_endpoint.py`:
```python
from unittest.mock import AsyncMock, patch
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from api.main import app
    return TestClient(app)


def _auth_headers():
    # Mirror whatever the existing /models tests use. If the app uses a
    # cookie or header-based auth, copy that pattern from an existing
    # test file like test_api_llm_usage.py.
    return {}


def test_list_models_happy_path(client):
    fake = [
        {"name": "llama3.2", "model": "llama3.2", "parameter_size": "3B",
         "quantization_level": "Q4_K_M", "size_bytes": 1, "context_length": 131072},
    ]
    with patch("api.main.ollama_client.list_models",
               new=AsyncMock(return_value=fake)):
        resp = client.post(
            "/ollama/list-models",
            json={"base_url": "http://localhost:11434"},
            headers=_auth_headers(),
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"models": fake}


def test_list_models_502_on_connection_error(client):
    from ollama_client import OllamaConnectionError
    with patch("api.main.ollama_client.list_models",
               new=AsyncMock(side_effect=OllamaConnectionError("nope"))):
        resp = client.post(
            "/ollama/list-models",
            json={"base_url": "http://localhost:11434"},
            headers=_auth_headers(),
        )
    assert resp.status_code == 502
    assert "Could not reach" in resp.json().get("error", "")


def test_list_models_401_on_auth_error(client):
    from ollama_client import OllamaAuthError
    with patch("api.main.ollama_client.list_models",
               new=AsyncMock(side_effect=OllamaAuthError("nope"))):
        resp = client.post(
            "/ollama/list-models",
            json={"base_url": "https://ollama.com/v1", "api_key": "bad"},
            headers=_auth_headers(),
        )
    assert resp.status_code == 401


def test_list_models_400_on_missing_base_url(client):
    resp = client.post(
        "/ollama/list-models",
        json={},
        headers=_auth_headers(),
    )
    assert resp.status_code in (400, 422)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest backend/tests/test_ollama_list_models_endpoint.py -v`
Expected: collection error or 4 fails — route not registered.

If the existing test pattern requires auth setup, look at `backend/tests/test_api_llm_usage.py` (or any existing endpoint test) and mirror the auth fixture exactly.

- [ ] **Step 3: Add the endpoint**

In `backend/api/main.py`, near the other `/models` endpoints, add:
```python
import ollama_client  # at the top with other imports if not already present


class OllamaListModelsBody(BaseModel):
    base_url: str = Field(..., max_length=512)
    api_key:  Optional[str] = Field(None, max_length=512)


@app.post("/ollama/list-models")
async def ollama_list_models_endpoint(
    body: OllamaListModelsBody,
    # mirror existing auth dependency used by /models endpoints:
    _user=Depends(require_authenticated_user),  # adjust name to match existing
):
    try:
        models = await ollama_client.list_models(body.base_url, body.api_key)
        return {"models": models}
    except ollama_client.OllamaAuthError as e:
        return JSONResponse(
            status_code=401, content={"error": "Authentication failed"}
        )
    except ollama_client.OllamaConnectionError as e:
        return JSONResponse(
            status_code=502,
            content={"error": f"Could not reach Ollama at {body.base_url}"},
        )
    except ollama_client.OllamaProviderError as e:
        return JSONResponse(
            status_code=502, content={"error": str(e)}
        )
```

Find the exact `Depends(...)` form used by the existing `/models` GET endpoint and copy it verbatim. Find the exact `JSONResponse` import (it may already be imported).

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest backend/tests/test_ollama_list_models_endpoint.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/api/main.py backend/tests/test_ollama_list_models_endpoint.py
git commit -m "feat(api): POST /ollama/list-models for model discovery"
```

---

### Task 17: Extend `LlmConfigTestBody` to accept Ollama fields

**Files:**
- Modify: `backend/api/main.py` (search for `class LlmConfigTestBody`)
- Modify: `backend/api/main.py` (the action that runs the test — search for `/llm/test`)
- Modify: `backend/tests/test_models_api_ollama.py`

- [ ] **Step 1: Write failing test**

Append to `backend/tests/test_models_api_ollama.py`:
```python
def test_llm_config_test_body_accepts_ollama_fields():
    from api.main import LlmConfigTestBody
    body = LlmConfigTestBody(
        provider="ollama",
        model="llama3.2",
        api_key=None,
        ollama_base_url="http://localhost:11434",
        ollama_keep_alive="5m",
    )
    assert body.ollama_base_url == "http://localhost:11434"
    assert body.ollama_keep_alive == "5m"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest backend/tests/test_models_api_ollama.py -v -k llm_config_test_body`
Expected: FAIL — fields missing.

- [ ] **Step 3: Add fields + wire into the test action**

In `backend/api/main.py`, find `class LlmConfigTestBody(BaseModel):` and add:
```python
ollama_base_url:   Optional[str] = Field(None, max_length=512)
ollama_keep_alive: Optional[str] = Field(None, max_length=16)
```

Then locate the function that POST `/llm/test` calls (likely `action_test_llm_config` or similar in `backend/interactive_utils.py`). Where it currently constructs `provider_config={...}` to pass into `call_structured_llm_by_provider`, add the ollama fields when present, mirroring how `nvidia_base_url` flows through:
```python
if (body.provider or "").lower() == "ollama":
    provider_config["ollama_base_url"] = body.ollama_base_url
    if body.ollama_keep_alive:
        provider_config["ollama_keep_alive"] = body.ollama_keep_alive
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest backend/tests/test_models_api_ollama.py -v -k llm_config_test_body`
Expected: passed.

- [ ] **Step 5: Commit**

```bash
git add backend/api/main.py backend/interactive_utils.py backend/tests/test_models_api_ollama.py
git commit -m "feat(api): /llm/test accepts ollama_base_url + ollama_keep_alive"
```

---

### Task 18: Update `.env.example`

**Files:**
- Modify: `.env.example`

- [ ] **Step 1: Find a logical section in `.env.example`**

Open `.env.example` and find the section near other LLM env vars (e.g., near `GEMINI_API_KEY`, `DEEPSEEK_API_KEY`). Identify the line number.

- [ ] **Step 2: Append the Ollama section**

Add this block adjacent to the existing legacy fallback provider keys section:
```bash

# Ollama provider — optional env-var fallbacks. Models table rows override.
# OLLAMA_BASE_URL: default endpoint when a row leaves ollama_base_url blank.
#   Local:   http://localhost:11434
#   Cloud:   https://ollama.com/v1
# OLLAMA_API_KEY: only needed for Ollama Cloud (Bearer token).
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_API_KEY=
```

- [ ] **Step 3: Commit**

```bash
git add .env.example
git commit -m "docs(.env.example): document OLLAMA_BASE_URL + OLLAMA_API_KEY fallbacks"
```

---

## Phase 4 — Frontend

> **Note:** the frontend repo has no test runner configured (no vitest/jest in `frontend/package.json`). Frontend changes are verified by the manual checklist in Phase 5 / Task 23, not by JS unit tests.

### Task 19: Add Ollama to `LLM_PROVIDER_OPTIONS` and test payload

**Files:**
- Modify: `frontend/src/utils/strategyConfig.js`

- [ ] **Step 1: Read existing `LLM_PROVIDER_OPTIONS`**

Open `frontend/src/utils/strategyConfig.js` and locate `export const LLM_PROVIDER_OPTIONS`.

- [ ] **Step 2: Add the Ollama entry**

Append to the array:
```js
{ value: 'ollama', label: 'Ollama (local / cloud)' },
```

- [ ] **Step 3: Locate `buildStrategyLlmTestPayload`**

Search for `buildStrategyLlmTestPayload` in the same file. Identify where provider-specific branches build the payload (the existing pattern handles `azure`, `nvidia`, etc.).

- [ ] **Step 4: Add the Ollama branch**

Within the same provider switch, add:
```js
if (provider === 'ollama') {
  const baseUrl = String(draft?.ollamaBaseUrl || 'http://localhost:11434').trim()
  if (baseUrl) payload.ollama_base_url = baseUrl
  const keepAlive = String(draft?.ollamaKeepAlive || '').trim()
  if (keepAlive) payload.ollama_keep_alive = keepAlive
  if (draft?.apiKey) payload.api_key = draft.apiKey
}
```

(Match the local-variable name conventions of the existing code — `draft` vs `formDraft`, etc.)

- [ ] **Step 5: Smoke-build + commit**

Run:
```bash
cd frontend && npm run build
```
Expected: clean build, no syntax errors.

```bash
git add frontend/src/utils/strategyConfig.js
git commit -m "feat(frontend): add ollama to LLM_PROVIDER_OPTIONS + test payload builder"
```

---

### Task 20: Create `useOllamaModels.js` composable

**Files:**
- Create: `frontend/src/composables/useOllamaModels.js`

- [ ] **Step 1: Inspect existing composables for style**

Look at `frontend/src/composables/useChatbot.js` and `useFullscreen.js` to match style (ES module, `ref()`/`reactive()`, default export vs named).

- [ ] **Step 2: Implement the composable**

Create `frontend/src/composables/useOllamaModels.js`:
```js
// Cached loader for /ollama/list-models. 30-second in-memory cache.
// Pass force=true to bypass the cache (used by the Refresh button).

const CACHE = new Map()          // key: `${baseUrl}|${apiKey || ''}`  → {ts, models}
const TTL_MS = 30_000

function _cacheKey(baseUrl, apiKey) {
  return `${baseUrl}|${apiKey || ''}`
}

export async function loadOllamaModels({ baseUrl, apiKey, force = false } = {}) {
  const key = _cacheKey(baseUrl, apiKey)
  if (!force) {
    const hit = CACHE.get(key)
    if (hit && (Date.now() - hit.ts) < TTL_MS) {
      return { models: hit.models, error: null }
    }
  }
  try {
    const resp = await fetch('/ollama/list-models', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({ base_url: baseUrl, api_key: apiKey || undefined }),
    })
    if (!resp.ok) {
      const body = await resp.json().catch(() => ({}))
      return { models: [], error: body.error || `HTTP ${resp.status}` }
    }
    const data = await resp.json()
    const models = data.models || []
    CACHE.set(key, { ts: Date.now(), models })
    return { models, error: null }
  } catch (err) {
    return { models: [], error: String(err) }
  }
}

export function clearOllamaModelsCache() {
  CACHE.clear()
}
```

- [ ] **Step 3: Smoke build + commit**

Run:
```bash
cd frontend && npm run build
```
Expected: clean build.

```bash
git add frontend/src/composables/useOllamaModels.js
git commit -m "feat(frontend): add useOllamaModels composable with 30s cache"
```

---

### Task 21: Add Ollama branch to `LlmConfigForm.vue`

**Files:**
- Modify: `frontend/src/components/LlmConfigForm.vue`

- [ ] **Step 1: Read current form structure**

Open `frontend/src/components/LlmConfigForm.vue` and find:
- The conditional blocks for other providers (`v-if="draft.provider === 'nvidia'"`, etc.) — mimic their styling.
- The `draft` (or `modelValue`) prop shape — see how parent passes fields like `nvidiaBaseUrl`, `apiKey`, etc.
- The reasoning-effort field — note that it's shown for some providers only.

- [ ] **Step 2: Add the Ollama conditional block**

In the template, after the existing nvidia block, add:
```html
<!-- Ollama -->
<div v-if="draft.provider === 'ollama'" class="space-y-3">
  <label class="block text-sm font-medium text-gray-200">Ollama Base URL</label>
  <input
    type="text"
    :value="draft.ollamaBaseUrl"
    @input="update('ollamaBaseUrl', $event.target.value)"
    placeholder="http://localhost:11434 or https://ollama.com/v1"
    class="w-full bg-gray-800 text-gray-100 rounded px-3 py-2"
  />

  <label class="block text-sm font-medium text-gray-200">
    API key
    <span v-if="ollamaCloudHost" class="text-red-400">(required for ollama.com)</span>
    <span v-else class="text-gray-500">(optional)</span>
  </label>
  <input
    type="password"
    :value="draft.apiKey"
    @input="update('apiKey', $event.target.value)"
    class="w-full bg-gray-800 text-gray-100 rounded px-3 py-2"
  />

  <label class="block text-sm font-medium text-gray-200">Model</label>
  <div v-if="ollamaListError" class="text-xs text-yellow-400 mb-1">
    Couldn't reach Ollama — enter model name manually.
  </div>
  <div v-else-if="ollamaModels.length" class="flex items-center gap-2">
    <select
      :value="draft.model"
      @change="update('model', $event.target.value)"
      class="flex-1 bg-gray-800 text-gray-100 rounded px-3 py-2"
    >
      <option value="" disabled>Select a model</option>
      <option
        v-for="m in ollamaModels"
        :key="m.name"
        :value="m.name"
      >
        {{ m.name }}
        <template v-if="m.parameter_size">
          — {{ m.parameter_size }}{{ m.quantization_level ? ' / ' + m.quantization_level : '' }}
        </template>
      </option>
    </select>
    <button
      type="button"
      @click="refreshOllamaModels"
      class="px-2 py-2 rounded bg-gray-700 text-gray-200"
      :disabled="ollamaListLoading"
      :title="'Refresh model list'"
    >🗘</button>
    <button
      type="button"
      @click="ollamaManual = !ollamaManual"
      class="text-xs text-gray-400 underline"
    >{{ ollamaManual ? 'pick from list' : 'type a name' }}</button>
  </div>
  <input
    v-if="ollamaListError || ollamaManual || !ollamaModels.length"
    type="text"
    :value="draft.model"
    @input="update('model', $event.target.value)"
    placeholder="e.g. llama3.2"
    class="w-full bg-gray-800 text-gray-100 rounded px-3 py-2"
  />

  <!-- Advanced -->
  <details class="text-sm">
    <summary class="cursor-pointer text-gray-400">Advanced</summary>
    <label class="block mt-2 text-sm font-medium text-gray-200">Keep alive</label>
    <input
      type="text"
      :value="draft.ollamaKeepAlive"
      @input="update('ollamaKeepAlive', $event.target.value)"
      placeholder="5m"
      class="w-full bg-gray-800 text-gray-100 rounded px-3 py-2"
    />
  </details>
</div>
```

In the `<script setup>` section, add:
```js
import { ref, computed, watch } from 'vue'
import { loadOllamaModels } from '@/composables/useOllamaModels'

const ollamaModels = ref([])
const ollamaListLoading = ref(false)
const ollamaListError = ref('')
const ollamaManual = ref(false)

const ollamaCloudHost = computed(() => {
  const url = String(props.draft?.ollamaBaseUrl || '').toLowerCase()
  try {
    const host = new URL(url).hostname
    return host === 'ollama.com' || host.endsWith('.ollama.com')
  } catch {
    return false
  }
})

async function fetchOllamaModels({ force = false } = {}) {
  const baseUrl = String(props.draft?.ollamaBaseUrl || '').trim()
  if (!baseUrl) return
  ollamaListLoading.value = true
  ollamaListError.value = ''
  const { models, error } = await loadOllamaModels({
    baseUrl,
    apiKey: props.draft?.apiKey || '',
    force,
  })
  ollamaModels.value = models
  ollamaListError.value = error || ''
  ollamaListLoading.value = false
}

function refreshOllamaModels() { fetchOllamaModels({ force: true }) }

watch(
  () => [props.draft?.provider, props.draft?.ollamaBaseUrl, props.draft?.apiKey],
  ([provider]) => {
    if (provider === 'ollama') fetchOllamaModels()
  },
  { immediate: true },
)
```

(If the form uses a different reactive prop name — e.g., `modelValue` rather than `draft` — adjust accordingly. Match the convention used by other providers in the same file.)

Also: **hide the reasoning-effort field for `provider === 'ollama'`** — add `&& draft.provider !== 'ollama'` to whichever `v-if` currently shows it.

- [ ] **Step 3: Smoke build**

Run:
```bash
cd frontend && npm run build
```
Expected: clean build.

- [ ] **Step 4: Quick manual smoke**

Run the dev server (`npm run dev`), open the Models page, click "Add Model", pick "Ollama (local / cloud)" from the provider dropdown. Verify:
- The Ollama-specific fields render.
- With a local Ollama running, the model dropdown populates.
- With Ollama stopped, the form degrades to free-text with the warning banner.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/LlmConfigForm.vue
git commit -m "feat(frontend): add Ollama branch to LlmConfigForm (picker + cloud auth + advanced)"
```

---

### Task 22: Extend `ModelsView.vue` `formDraft`

**Files:**
- Modify: `frontend/src/views/ModelsView.vue`

- [ ] **Step 1: Locate `formDraft`**

In `frontend/src/views/ModelsView.vue`, search for `formDraft`. It's a `ref()` or `reactive()` holding the form's local state.

- [ ] **Step 2: Add the new fields**

Add to the initial-value object:
```js
ollamaBaseUrl: 'http://localhost:11434',
ollamaKeepAlive: '',
```

- [ ] **Step 3: Ensure round-trip on save**

Find where the component sends the create/edit payload to the backend. Ensure `ollama_base_url` and `ollama_keep_alive` are derived from `formDraft.ollamaBaseUrl` and `formDraft.ollamaKeepAlive` when `provider === 'ollama'`. Mirror the existing pattern for `nvidia_base_url`.

Find where it loads an existing row into `formDraft` for editing. Ensure it maps `model.ollama_base_url → formDraft.ollamaBaseUrl` and same for keep_alive.

- [ ] **Step 4: Smoke build + manual check**

```bash
cd frontend && npm run build
```
Then `npm run dev`; open Models page → Add → fill the form → save → re-edit. Both fields round-trip correctly.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/views/ModelsView.vue
git commit -m "feat(frontend): ModelsView formDraft round-trips ollama_base_url + ollama_keep_alive"
```

---

## Phase 5 — Live integration + verification

### Task 23: Opt-in live integration test

**Files:**
- Create: `backend/tests/integration/test_ollama_live.py`

- [ ] **Step 1: Write the gated test**

Create `backend/tests/integration/test_ollama_live.py`:
```python
"""Opt-in live integration test for the Ollama provider.

Requires a local Ollama with `llama3.2` installed. Skipped unless
RUN_OLLAMA_LIVE=1 in the environment.

    ollama pull llama3.2
    RUN_OLLAMA_LIVE=1 pytest backend/tests/integration/test_ollama_live.py -v
"""

import os
import pytest

from pydantic import BaseModel


pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_OLLAMA_LIVE") != "1",
    reason="Set RUN_OLLAMA_LIVE=1 to enable; requires local Ollama + llama3.2",
)


def test_plain_text():
    from llm_utils import call_llm_by_provider
    out = call_llm_by_provider(
        provider="ollama",
        api_key="",
        model="llama3.2",
        prompt="Reply with exactly the word PONG.",
        max_output_tokens=16,
        provider_config={"ollama_base_url": "http://localhost:11434"},
    )
    assert isinstance(out, str) and out.strip()


def test_structured_output():
    from llm_utils import call_structured_llm_by_provider

    class Answer(BaseModel):
        answer: str

    out = call_structured_llm_by_provider(
        provider="ollama",
        api_key="",
        model="llama3.2",
        prompt='Reply as JSON: {"answer": "PONG"}',
        output_type=Answer,
        provider_config={"ollama_base_url": "http://localhost:11434"},
    )
    assert isinstance(out, Answer)
    assert out.answer


def test_tool_calling():
    """qwen2.5 supports tools; skip if not installed."""
    import subprocess
    tags = subprocess.run(
        ["curl", "-sf", "http://localhost:11434/api/tags"],
        capture_output=True, text=True, timeout=5,
    )
    if "qwen2.5" not in tags.stdout:
        pytest.skip("qwen2.5 not installed; `ollama pull qwen2.5` to enable")

    from llm_utils import call_ollama_with_tools
    tools = [{
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get the weather for a city",
            "parameters": {"type": "object",
                           "properties": {"city": {"type": "string"}},
                           "required": ["city"]},
        },
    }]
    result = call_ollama_with_tools(
        api_key="",
        model="qwen2.5",
        prompt="What's the weather in San Francisco? Call the tool.",
        tools=tools,
        base_url="http://localhost:11434",
    )
    assert "text" in result
    assert "tool_calls" in result
```

- [ ] **Step 2: Run with the gate off — must skip cleanly**

Run: `pytest backend/tests/integration/test_ollama_live.py -v`
Expected: 3 skipped.

- [ ] **Step 3: Run with the gate on (optional, requires Ollama)**

Only do this if you have local Ollama with `llama3.2`:
```bash
RUN_OLLAMA_LIVE=1 pytest backend/tests/integration/test_ollama_live.py -v
```
Expected: 2 passed (3 if `qwen2.5` is also installed).

- [ ] **Step 4: Commit**

```bash
git add backend/tests/integration/test_ollama_live.py
git commit -m "test(ollama): opt-in live integration test (plain + structured + tools)"
```

---

### Task 24: Parallel bug sweep

> **Per user request:** This task launches multiple parallel agents to audit the entire implementation before the final manual verification. Findings get addressed inline; only then proceed to Task 25.

**Files:**
- No file changes here; findings drive fixup commits.

- [ ] **Step 1: Collect the implementation diff**

Run:
```bash
git log --oneline main..HEAD | head -40
git diff main...HEAD --stat
```
Note the list of changed files. They'll be passed to the audit agents.

- [ ] **Step 2: Dispatch parallel audit agents**

Use the Agent tool to dispatch **four** agents in parallel, each in a single message. Each gets a focused brief:

**Agent 1 — Backend correctness:**
> Audit the Ollama backend implementation on this branch for correctness bugs and regressions. Files of interest: `backend/ollama_client.py`, `backend/llm_utils.py` (the Ollama additions), `backend/llm_critical_guard.py`, `backend/interactive_utils.py`, `backend/api/main.py`. Specifically check:
> - Does `_call_ollama` correctly distinguish 404 (no retry) from 5xx/429 (retry)?
> - Do the new branches in `call_llm_by_provider` and `call_structured_llm_by_provider` actually receive `ollama_base_url` from the resolved provider_config? Trace the variable from request body → action → llm_utils.
> - Are `OllamaAuthError` / `OllamaConnectionError` correctly mapped from raw `ollama.ResponseError` and `httpx.*` exceptions?
> - Does `_normalize_tools_to_openai_shape` handle edge cases like nested Gemini schemas?
> - Does the critical-guard branch run BEFORE existing branches that might swallow 401 (re-check ordering)?
> Report findings under 600 words with file:line citations. Flag any issue as P0 (must fix before merge), P1 (should fix), P2 (nice-to-have).

**Agent 2 — Frontend correctness:**
> Audit the Ollama frontend implementation on this branch. Files of interest: `frontend/src/utils/strategyConfig.js`, `frontend/src/composables/useOllamaModels.js`, `frontend/src/components/LlmConfigForm.vue`, `frontend/src/views/ModelsView.vue`. Check:
> - Does the model picker correctly populate after the user types a base_url? (watch dependency / debouncing)
> - Does the Vue `props.draft` reactivity actually trigger the watcher when `ollamaBaseUrl` changes?
> - Is the OOoma Cloud hostname detection (`ollama.com` / `.ollama.com`) actually wired to make the API-key field required?
> - Is `formDraft.ollamaBaseUrl` correctly hydrated when editing an existing Ollama row?
> - Are there any patterns missed when comparing to the existing NVIDIA branch (which is structurally closest)?
> Report under 500 words, P0/P1/P2.

**Agent 3 — Test coverage holes:**
> Review the new test files under `backend/tests/` (any file containing "ollama") for coverage gaps. Specifically look for:
> - Untested error paths in `_call_ollama` and `call_ollama_with_tools`.
> - Missing tests for `_resolve_ollama_timeout` (cold vs warm).
> - Tests that mock too aggressively and would still pass even if the real code is broken.
> - Tests that depend on import-order or module-level state and could flake.
> Report under 500 words. For each gap, propose the minimum test that would close it.

**Agent 4 — Spec/plan alignment:**
> Compare the implementation on this branch against the spec at `docs/superpowers/specs/2026-05-23-ollama-provider-design.md` and the plan at `docs/superpowers/plans/2026-05-23-ollama-provider-implementation.md`. Identify:
> - Spec acceptance criteria (§12) that are not demonstrably met by the diff.
> - Out-of-scope items (§10) that snuck in.
> - Field name drift between spec / plan / code (e.g., `ollama_keep_alive` vs `ollamaKeepAlive`).
> Report under 400 words.

Run all four with `run_in_background=true`.

- [ ] **Step 3: Triage findings**

Wait for all four reports. Consolidate findings into a short triage list grouped by P0/P1/P2. For every P0, address it inline:
- For a backend bug: write a regression test that reproduces it (TDD), then fix the code, run the test, commit with message like `fix(ollama): <specific bug>`.
- For a frontend bug: fix it directly, smoke-build, commit.
- For a coverage gap rated P0 by the agent: add the test, run it, commit.

P1 findings: address if cheap; otherwise file as a follow-up note in the spec's §13.
P2 findings: note but don't act.

- [ ] **Step 4: Re-run the full suite**

Run: `pytest backend/tests/ -v -k ollama`
Expected: all passing.

Run: `cd frontend && npm run build`
Expected: clean build.

- [ ] **Step 5: Commit any fixup changes**

If fixups were needed, they're already individually committed in Step 3. Otherwise skip.

---

### Task 25: Manual verification + branch wrap-up

**Files:** none (verification only).

- [ ] **Step 1: Local Ollama smoke**

Pre-req: `ollama serve` running locally with `ollama pull llama3.2` done.

1. Start backend + frontend dev servers.
2. Navigate to `/models`. Click "Add Model".
3. Pick "Ollama (local / cloud)". Verify:
   - Base URL defaults to `http://localhost:11434`.
   - Model dropdown populates with installed models (at minimum `llama3.2`).
4. Pick `llama3.2`, give the row a name, click "Test & Save". The smoke response renders. The row appears in the list.
5. Set this `model_id` on any small backtest. Run it. It completes against Ollama.

- [ ] **Step 2: Ollama-stopped degradation smoke**

1. Stop `ollama serve`.
2. In `/models`, click Edit on the Ollama row. The model dropdown should hide and the free-text input appear with the warning banner.
3. Click Test. Verify: a clean error message, no retry storm, no unhandled exception in backend logs.

- [ ] **Step 3: Ollama Cloud smoke (if you have credentials)**

1. Add another Ollama row, base URL `https://ollama.com/v1`, paste a valid API key.
2. API key label should read "(required for ollama.com)".
3. Model dropdown should populate (cloud-available models, e.g., `gpt-oss:120b-cloud`).
4. Click Test. Smoke succeeds.
5. Edit the row, replace the API key with garbage. Save. Run a backtest. Verify backtest pauses cleanly via the existing critical-guard "auth_failure" path — does NOT abort, does NOT retry storm.

- [ ] **Step 4: Tool-calling smoke (optional)**

If you have a tool-using strategy:
1. Add an Ollama row with model `qwen2.5` (or any tool-capable model — pre-pulled).
2. Wire the model_id into a tool-using strategy role.
3. Run a small backtest. Verify the tool calls dispatch correctly.

- [ ] **Step 5: Final commit (only if uncommitted edits remain)**

Run `git status` — if clean, the branch is ready. Otherwise stage and commit any leftover docs/comments fixes:
```bash
git status
git add <anything outstanding>
git commit -m "chore(ollama): wrap up branch"
```

---

## Self-Review (do this after completing all tasks)

1. **Spec coverage:** every section in `2026-05-23-ollama-provider-design.md` should map to at least one task:
   - §3 architecture → reflected in Tasks 2 + 8.
   - §4.1 ollama_client.py → Tasks 2, 3, 4.
   - §4.2 llm_utils extensions → Tasks 5, 6, 7, 8, 9, 10, 11, 12.
   - §4.3 critical guard → Task 13.
   - §4.5 endpoint + /llm/test → Tasks 16, 17.
   - §4.6 frontend → Tasks 19, 20, 21, 22.
   - §5 storage → Tasks 14, 15.
   - §6 env → Task 18.
   - §7 error handling → covered by Task 8 retry logic + Task 13 classifier.
   - §8 tool adapter → Tasks 5, 12.
   - §9 testing → all tasks above + Task 23.
   - §12 acceptance criteria → Task 25 manual checklist.

2. **Placeholder scan:** every code step has actual code, every command has expected output, every test path is concrete.

3. **Type / name consistency:**
   - Backend: `ollama_base_url`, `ollama_keep_alive` (snake_case) — used consistently in `_resolve_provider_config`, Pydantic bodies, action persistence.
   - Frontend: `ollamaBaseUrl`, `ollamaKeepAlive` (camelCase) — used consistently in `formDraft`, composable arguments, conditional template.
   - Exception classes: `OllamaConnectionError`, `OllamaAuthError`, `OllamaProviderError` — defined in `ollama_client.py`, imported in `llm_utils.py` and `llm_critical_guard.py`.
   - Helper names: `_call_ollama` (chat), `_call_ollama_structured_from_strategy` (PydanticAI), `call_ollama_with_tools` (tool), `_normalize_tools_to_openai_shape` (adapter).
