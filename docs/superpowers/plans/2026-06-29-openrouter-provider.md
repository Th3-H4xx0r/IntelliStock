# OpenRouter Provider Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `openrouter` as a first-class LLM provider (backend + web + mobile) with a live model dropdown, configurable attribution headers, reasoning-effort support, and pricing auto-fill into the existing per-row cost-override fields.

**Architecture:** OpenRouter is OpenAI Chat-Completions compatible, so it mirrors the existing `nvidia` provider end-to-end — `OpenAIChatModel`+`OpenAIProvider(base_url, api_key)` on the structured path and a `_call_nvidia` clone on the plain path. Two additions beyond nvidia: optional `HTTP-Referer`/`X-Title` headers, and a public `GET /api/v1/models` discovery endpoint that also feeds pricing auto-fill.

**Tech Stack:** Python (backend, pydantic_ai, requests, FastAPI, RethinkDB), Vue 3 (web), Flutter/Dart (mobile), pytest, flutter test.

## Global Constraints

- Provider key string is exactly `openrouter` (lowercase) everywhere.
- Default base URL: `https://openrouter.ai/api/v1` (env override `OPENROUTER_BASE_URL`).
- API key env var: `OPENROUTER_API_KEY`. Attribution env fallbacks: `OPENROUTER_HTTP_REFERER`, `OPENROUTER_X_TITLE`.
- Model ids are `vendor/model` slash form.
- Reasoning reuses the **standard** `reasoning_effort` field (low/medium/high) — do NOT add an openrouter-specific reasoning field; OpenRouter accepts `reasoning_effort` as an alias.
- Pricing values from `/models` are USD **per token** (strings) → multiply by `1_000_000` for IntelliStock's USD-per-1M fields.
- Per-row cost-override fields already exist: `input_cost_per_1m`, `output_cost_per_1m`, `cache_creation_cost_per_1m`, `cache_read_cost_per_1m`. Do NOT add new pricing schema.
- Before editing each backend symbol, run `gitnexus_impact({target, direction:"upstream"})`; run `gitnexus_detect_changes()` before each commit (per CLAUDE.md).
- Backend tests run with `python3 -m pytest` from `backend/`; mobile with `flutter test` from `mobile/`.

---

### Task 1: Backend — API-key + config resolution + provider meta

**Files:**
- Modify: `backend/llm_utils.py` — `resolve_api_key_for_provider` (~217-237), `_resolve_provider_config` (~689-779), `_safe_provider_meta` (~782-816)
- Test: `backend/tests/test_openrouter_provider.py` (new)

**Interfaces:**
- Produces: `resolve_api_key_for_provider("openrouter")` → `OPENROUTER_API_KEY`; `_resolve_provider_config("openrouter", cfg)` returns dict with `openrouter_base_url`, `openrouter_referer`, `openrouter_title`, normalized `reasoning_effort`; `_safe_provider_meta("openrouter", cfg)` → `{base_url, reasoning_effort?}`.

- [ ] **Step 1: Write failing tests** in `backend/tests/test_openrouter_provider.py`:

```python
import os
import importlib
import llm_utils


def test_api_key_from_env(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-xyz")
    assert llm_utils.resolve_api_key_for_provider("openrouter") == "sk-or-xyz"


def test_api_key_explicit_wins(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "envkey")
    assert llm_utils.resolve_api_key_for_provider("openrouter", "explicit") == "explicit"


def test_resolve_config_defaults(monkeypatch):
    monkeypatch.delenv("OPENROUTER_BASE_URL", raising=False)
    cfg = llm_utils._resolve_provider_config("openrouter", {})
    assert cfg["openrouter_base_url"] == "https://openrouter.ai/api/v1"


def test_resolve_config_headers_and_reasoning():
    cfg = llm_utils._resolve_provider_config("openrouter", {
        "openrouter_referer": "https://intellistock.app",
        "openrouter_title": "IntelliStock",
        "reasoning_effort": "HIGH",
    })
    assert cfg["openrouter_referer"] == "https://intellistock.app"
    assert cfg["openrouter_title"] == "IntelliStock"
    assert cfg["reasoning_effort"] == "high"


def test_safe_meta_no_secrets():
    meta = llm_utils._safe_provider_meta("openrouter", {
        "openrouter_base_url": "https://openrouter.ai/api/v1",
        "openrouter_referer": "https://x", "reasoning_effort": "low",
    })
    assert meta["base_url"] == "https://openrouter.ai/api/v1"
    assert "openrouter_referer" not in meta
    assert meta["reasoning_effort"] == "low"
```

- [ ] **Step 2: Run, verify fail**: `cd backend && python3 -m pytest tests/test_openrouter_provider.py -v` → FAIL (provider not handled / returns gemini key).

- [ ] **Step 3: Implement.** In `resolve_api_key_for_provider`, add before the final `return`:

```python
    if p == "openrouter":
        return str(os.environ.get("OPENROUTER_API_KEY") or "").strip()
```

In `_resolve_provider_config`, add a branch after the `nvidia` branch:

```python
    elif p == "openrouter":
        base_url = str(
            resolved.get("openrouter_base_url")
            or os.environ.get("OPENROUTER_BASE_URL")
            or "https://openrouter.ai/api/v1"
        ).strip().rstrip("/")
        resolved["openrouter_base_url"] = base_url
        referer = str(
            resolved.get("openrouter_referer")
            or os.environ.get("OPENROUTER_HTTP_REFERER")
            or ""
        ).strip()
        if referer:
            resolved["openrouter_referer"] = referer
        else:
            resolved.pop("openrouter_referer", None)
        title = str(
            resolved.get("openrouter_title")
            or os.environ.get("OPENROUTER_X_TITLE")
            or ""
        ).strip()
        if title:
            resolved["openrouter_title"] = title
        else:
            resolved.pop("openrouter_title", None)
        reasoning_effort = normalize_reasoning_effort(resolved.get("reasoning_effort"))
        if reasoning_effort:
            resolved["reasoning_effort"] = reasoning_effort
        else:
            resolved.pop("reasoning_effort", None)
```

In `_safe_provider_meta`, add after the `nvidia` block:

```python
    if p == "openrouter":
        meta = {"base_url": str(config.get("openrouter_base_url") or "https://openrouter.ai/api/v1")}
        reasoning_effort = normalize_reasoning_effort(config.get("reasoning_effort"))
        if reasoning_effort:
            meta["reasoning_effort"] = reasoning_effort
        return meta
```

- [ ] **Step 4: Run, verify pass**: `python3 -m pytest tests/test_openrouter_provider.py -v` → PASS.
- [ ] **Step 5: Commit**: `git add backend/llm_utils.py backend/tests/test_openrouter_provider.py && git commit -m "feat(llm): openrouter api-key + config + meta resolution"`

---

### Task 2: Backend — structured model build, locks, terminal-404

**Files:**
- Modify: `backend/llm_utils.py` — `_build_pydantic_ai_model` (~819-966), `_STRUCTURED_LLM_PROVIDER_LOCKS` (~170-172), `_is_terminal_provider_not_found` (~997-1056), `_terminal_provider_not_found_hint` (~1059-1101), and the `{azure, openai, nvidia}` membership sets (~838, ~2635).
- Test: `backend/tests/test_openrouter_provider.py`

**Interfaces:**
- Consumes: `_resolve_provider_config` (Task 1).
- Produces: `_build_pydantic_ai_model("openrouter", key, model, cfg)` returns an `OpenAIChatModel` whose provider base_url is the openrouter URL; injects `HTTP-Referer`/`X-Title` via the OpenAI client `default_headers` when set.

- [ ] **Step 1: Write failing tests** (append):

```python
import pytest


@pytest.mark.skipif(not llm_utils._PYDANTIC_AI_AVAILABLE, reason="pydantic_ai not installed")
def test_build_model_openrouter_base_url():
    model = llm_utils._build_pydantic_ai_model(
        "openrouter", "sk-or-x", "anthropic/claude-3.5-sonnet",
        {"openrouter_base_url": "https://openrouter.ai/api/v1"},
    )
    assert model is not None
    # OpenAIChatModel exposes the provider's base_url on its client.
    assert "openrouter.ai" in str(getattr(model, "base_url", "") or
                                  getattr(getattr(model, "client", None), "base_url", ""))


def test_lock_registered():
    assert "openrouter" in llm_utils._STRUCTURED_LLM_PROVIDER_LOCKS


def test_terminal_not_found_openrouter():
    exc = RuntimeError("HTTP 404: model_not_found")
    assert llm_utils._is_terminal_provider_not_found("openrouter", exc) is True
```

- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement.** Add lock: in `_STRUCTURED_LLM_PROVIDER_LOCKS` dict add `"openrouter": threading.Lock(),`.

In `_build_pydantic_ai_model`, add a branch before the final `return GoogleModel(...)`:

```python
    if p == "openrouter":
        base_url = str(resolved.get("openrouter_base_url") or "https://openrouter.ai/api/v1").strip().rstrip("/")
        referer = str(resolved.get("openrouter_referer") or "").strip()
        title = str(resolved.get("openrouter_title") or "").strip()
        default_headers = {}
        if referer:
            default_headers["HTTP-Referer"] = referer
        if title:
            default_headers["X-Title"] = title
        profile = _prompted_json_profile() if _prefers_prompted_structured_output(p, model) else None
        if default_headers:
            try:
                from openai import AsyncOpenAI as _AsyncOpenAI
                _client = _AsyncOpenAI(base_url=base_url, api_key=api_key, default_headers=default_headers)
                return OpenAIChatModel(model, provider=OpenAIProvider(openai_client=_client), profile=profile)
            except Exception:
                pass  # fall through to header-less provider
        return OpenAIChatModel(
            model,
            provider=OpenAIProvider(base_url=base_url, api_key=api_key),
            profile=profile,
        )
```

In `_is_terminal_provider_not_found`, extend the OpenAI-compatible group:

```python
    if p in ("openai", "nvidia", "openrouter"):
```

In `_terminal_provider_not_found_hint`, extend the same `("openai", "nvidia")` cross-provider checks to include `"openrouter"`, and add a slash hint:

```python
    elif p == "openrouter" and "/" not in (model or ""):
        cross_provider_hint = (
            f" OpenRouter model ids are 'vendor/model' (e.g. 'anthropic/claude-3.5-sonnet'); "
            f"{name!r} has no '/' and is probably wrong."
        )
```

Add `"openrouter"` to the `_prefers_prompted_structured_output` provider set (`{"azure", "openai", "nvidia"}` → add `"openrouter"`) and to the membership set near line ~2635 (`{"azure", "openai", "nvidia"}`).

- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Commit**: `git commit -am "feat(llm): openrouter structured model build + lock + 404 handling"`

---

### Task 3: Backend — plain-text `_call_openrouter` + dispatch

**Files:**
- Modify: `backend/llm_utils.py` — add `_call_openrouter` near `_call_nvidia` (~4277-4444); add dispatch branch in `call_llm_by_provider` (~5524 nvidia branch).
- Test: `backend/tests/test_openrouter_provider.py`

**Interfaces:**
- Produces: `_call_openrouter(api_key, model, prompt, max_output_tokens=256, timeout_sec=None, retries=0, base_url="", response_mime_type=None, reasoning_effort="", referer="", title="") -> str`. `call_llm_by_provider(provider="openrouter", ...)` routes to it.

- [ ] **Step 1: Write failing test** (uses `requests_mock`-style monkeypatch on `requests.post`):

```python
def test_call_openrouter_posts_chat_completions(monkeypatch):
    captured = {}

    class _Resp:
        status_code = 200
        headers = {}
        text = ""
        def json(self):
            return {"choices": [{"message": {"content": "hello"}}]}

    def _fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["body"] = json
        return _Resp()

    import requests
    monkeypatch.setattr(requests, "post", _fake_post)
    out = llm_utils._call_openrouter(
        "sk-or-x", "anthropic/claude-3.5-sonnet", "hi",
        referer="https://intellistock.app", title="IntelliStock",
        reasoning_effort="high",
    )
    assert out == "hello"
    assert captured["url"].endswith("/chat/completions")
    assert captured["headers"]["Authorization"] == "Bearer sk-or-x"
    assert captured["headers"]["HTTP-Referer"] == "https://intellistock.app"
    assert captured["headers"]["X-Title"] == "IntelliStock"
    assert captured["body"]["reasoning_effort"] == "high"
```

- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement** `_call_openrouter` as a clone of `_call_nvidia` with these differences: default base_url `https://openrouter.ai/api/v1`; build headers dict including `HTTP-Referer`/`X-Title` when passed; add `reasoning_effort` as a top-level body field when non-empty (also set `body["reasoning"] = {"effort": reasoning_effort}` for native support); NO `_get_model_request_rate_limiter` block (openrouter has no NVIDIA-style cap); `_log_token_usage("openrouter", model, data)`:

```python
def _call_openrouter(
    api_key: str,
    model: str,
    prompt: str,
    max_output_tokens: int = 256,
    timeout_sec: int | None = None,
    retries: int = 0,
    base_url: str = "",
    response_mime_type: str | None = None,
    reasoning_effort: str = "",
    referer: str = "",
    title: str = "",
) -> str:
    """Call OpenRouter (OpenAI-compatible /chat/completions). Clone of _call_nvidia
    without NVIDIA's RPM limiter, plus optional attribution headers + reasoning."""
    if not api_key:
        return ""
    try:
        import requests as _requests
        url = (base_url or "https://openrouter.ai/api/v1").rstrip("/") + "/chat/completions"
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
        if referer:
            headers["HTTP-Referer"] = referer
        if title:
            headers["X-Title"] = title
        body: dict = {"model": model, "messages": [{"role": "user", "content": prompt}], "top_p": 0.95}
        if not _omit_temperature(model):
            body["temperature"] = 0.2
        if max_output_tokens and max_output_tokens > 0:
            body["max_tokens"] = max_output_tokens
        effort = normalize_reasoning_effort(reasoning_effort)
        if effort:
            body["reasoning_effort"] = effort
            body["reasoning"] = {"effort": effort}
        if (
            str(response_mime_type or "").strip().lower() == "application/json"
            and not _model_skips_json_object_format(model)
        ):
            body["response_format"] = {"type": "json_object"}
        timeout = _coerce_timeout_sec(timeout_sec)
        max_retries = max(0, int(retries or 0))
        retriable_status = {429, 500, 502, 503, 504}
        for attempt in range(max_retries + 1):
            attempt_timeout = timeout if attempt == 0 else timeout * 2
            connect_timeout = min(15, attempt_timeout)
            try:
                r = _requests.post(url, headers=headers, json=body, timeout=(connect_timeout, attempt_timeout))
            except _requests.exceptions.Timeout as _to_e:
                if attempt < max_retries:
                    time.sleep(_backoff_sleep_seconds(attempt)); continue
                try: _stash_last_http(status=None, body=f"timeout after {attempt_timeout}s", exc=_to_e)
                except Exception: pass
                return ""
            except _requests.exceptions.RequestException as _req_e:
                if attempt < max_retries:
                    time.sleep(_backoff_sleep_seconds(attempt)); continue
                try:
                    _resp = getattr(_req_e, "response", None)
                    _stash_last_http(status=getattr(_resp, "status_code", None),
                                     body=(getattr(_resp, "text", "") if _resp is not None else str(_req_e))[:1000], exc=_req_e)
                except Exception: pass
                return ""
            if r.status_code in retriable_status and attempt < max_retries:
                _nr_filter, _nr_tag = _is_non_retryable_filter_response(status=r.status_code, body=getattr(r, "text", "") or "")
                if not _nr_filter:
                    wait = _retry_after_seconds(getattr(r, "headers", None)) or _http_retry_backoff_seconds(f"status_code: {r.status_code}", attempt)
                    time.sleep(wait); continue
            if r.status_code >= 400:
                try: _err_body = r.json()
                except Exception: _err_body = r.text[:500]
                try: _stash_last_http(status=r.status_code, body=(r.text or "")[:1000] if hasattr(r, "text") else str(_err_body)[:1000], exc=None)
                except Exception: pass
                raise RuntimeError(f"HTTP {r.status_code}: {_err_body}")
            data = r.json()
            _log_token_usage("openrouter", model, data)
            choices = data.get("choices") or []
            if not choices:
                if attempt < max_retries:
                    time.sleep(_backoff_sleep_seconds(attempt)); continue
                return ""
            message = choices[0].get("message") or {}
            text = _extract_chat_message_text(message)
            if text:
                try: _stash_last_http(status=200, body=None, exc=None)
                except Exception: pass
                return text
            if attempt < max_retries:
                time.sleep(_backoff_sleep_seconds(attempt)); continue
            return ""
        return ""
    except Exception as _exc:
        _LAST_PLAIN_LLM_CALL_ERROR.error = str(_exc)
        try:
            tid = threading.get_ident()
            with _LAST_HTTP_LOCK:
                _already = tid in _LAST_HTTP_PER_THREAD
            if not _already:
                _resp = getattr(_exc, "response", None)
                _stash_last_http(status=getattr(_resp, "status_code", None),
                                 body=(getattr(_resp, "text", "") if _resp is not None else str(_exc))[:1000], exc=_exc)
        except Exception: pass
        return ""
```

In `call_llm_by_provider`, add after the `nvidia` branch (read the nvidia branch first to match how it pulls config + reasoning_effort; mirror exactly):

```python
    elif p == "openrouter":
        _cfg = _resolve_provider_config(provider, provider_config)
        _result = _call_openrouter(
            api_key, model, prompt,
            max_output_tokens=max_output_tokens, timeout_sec=timeout_sec, retries=retries,
            base_url=str(_cfg.get("openrouter_base_url") or ""),
            response_mime_type=response_mime_type,
            reasoning_effort=str(_cfg.get("reasoning_effort") or ""),
            referer=str(_cfg.get("openrouter_referer") or ""),
            title=str(_cfg.get("openrouter_title") or ""),
        )
```

(Match the exact surrounding variable names — `_result`, return/telemetry handling — by reading the nvidia branch first.)

- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Commit**: `git commit -am "feat(llm): _call_openrouter plain path + dispatch"`

---

### Task 4: Backend — model_resolver field map

**Files:**
- Modify: `backend/model_resolver.py` — `field_map` (~143-168)
- Test: `backend/tests/test_openrouter_resolver.py` (new)

- [ ] **Step 1: Failing test:**

```python
import model_resolver


class _FakeConn: pass


def test_openrouter_fields_injected(monkeypatch):
    doc = {
        "id": "m1", "provider": "openrouter", "model": "anthropic/claude-3.5-sonnet",
        "api_key": "sk-or-x", "openrouter_base_url": "https://openrouter.ai/api/v1",
        "openrouter_referer": "https://intellistock.app", "openrouter_title": "IntelliStock",
    }
    monkeypatch.setattr(model_resolver, "_get_model_from_cache_or_db", lambda c, mid: doc)
    out = model_resolver.resolve_model_refs_in_config(_FakeConn(), {"llm_model_id": "m1"})
    assert out["llm_provider"] == "openrouter"
    assert out["openrouter_base_url"] == "https://openrouter.ai/api/v1"
    assert out["openrouter_referer"] == "https://intellistock.app"
    assert out["openrouter_title"] == "IntelliStock"
```

- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement** — add to `field_map`:

```python
            "openrouter_base_url": f"{prefix}openrouter_base_url",
            "openrouter_referer": f"{prefix}openrouter_referer",
            "openrouter_title": f"{prefix}openrouter_title",
```

- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Commit**: `git commit -am "feat(resolver): inject openrouter_* config fields"`

---

### Task 5: Backend — `openrouter_client.list_models` + `POST /openrouter/list-models`

**Files:**
- Create: `backend/openrouter_client.py`
- Modify: `backend/api/main.py` — add endpoint (mirror `POST /ollama/list-models` ~2213) + a `ListModelsBody`-style request model if needed.
- Test: `backend/tests/test_openrouter_client.py` (new)

**Interfaces:**
- Produces: `openrouter_client.list_models(base_url="https://openrouter.ai/api/v1", timeout_sec=15.0) -> list[dict]` where each row is `{id, name, context_length, pricing}` and `pricing` is the raw OpenRouter dict (USD/token strings). Never raises; returns `[]` on error.

- [ ] **Step 1: Failing test:**

```python
import openrouter_client


def test_list_models_normalizes(monkeypatch):
    payload = {"data": [
        {"id": "anthropic/claude-3.5-sonnet", "name": "Claude 3.5 Sonnet",
         "context_length": 200000,
         "pricing": {"prompt": "0.000003", "completion": "0.000015",
                     "input_cache_read": "0.0000003", "input_cache_write": "0.00000375"}},
        {"id": "openai/gpt-4o-mini", "name": "GPT-4o mini", "context_length": 128000,
         "pricing": {"prompt": "0.00000015", "completion": "0.0000006"}},
    ]}

    class _Resp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return payload

    import requests
    monkeypatch.setattr(requests, "get", lambda url, timeout=None: _Resp())
    rows = openrouter_client.list_models()
    assert rows[0]["id"] == "anthropic/claude-3.5-sonnet"
    assert rows[0]["pricing"]["prompt"] == "0.000003"
    assert rows[0]["context_length"] == 200000


def test_list_models_error_returns_empty(monkeypatch):
    import requests
    def _boom(url, timeout=None): raise requests.RequestException("down")
    monkeypatch.setattr(requests, "get", _boom)
    assert openrouter_client.list_models() == []
```

- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement** `backend/openrouter_client.py`:

```python
"""OpenRouter model-discovery client. Backs POST /openrouter/list-models and
the web LLM-config dropdown. Public GET {base_url}/models — no auth. Never
raises; returns [] on any failure (caller falls back to manual entry)."""
from __future__ import annotations

from typing import Any

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"


def list_models(base_url: str = DEFAULT_BASE_URL, *, timeout_sec: float = 15.0) -> list[dict[str, Any]]:
    base = str(base_url or DEFAULT_BASE_URL).strip().rstrip("/")
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
        rows.append({
            "id": mid,
            "name": str(m.get("name") or mid),
            "context_length": m.get("context_length"),
            "pricing": m.get("pricing") if isinstance(m.get("pricing"), dict) else {},
        })
    rows.sort(key=lambda x: x["id"])
    return rows
```

In `backend/api/main.py`, mirror the ollama endpoint. Read `POST /ollama/list-models` first; add:

```python
@app.post("/openrouter/list-models", response_class=JSONResponse)
def api_openrouter_list_models(body: dict = Body(default={}), current_user: dict = Depends(get_current_user)):
    import openrouter_client
    base_url = str((body or {}).get("base_url") or openrouter_client.DEFAULT_BASE_URL).strip()
    models = openrouter_client.list_models(base_url)
    return {"models": models, "error": None if models else "No models returned"}
```

(Adjust `Body`/`Depends` imports + auth dependency to match the file's existing conventions — read the ollama endpoint to copy them exactly.)

- [ ] **Step 4: Run, verify pass**: `python3 -m pytest tests/test_openrouter_client.py -v`.
- [ ] **Step 5: Commit**: `git commit -am "feat(api): openrouter model-discovery client + endpoint"`

---

### Task 6: Backend — Models CRUD, compat validation, API bodies, /llm/test

**Files:**
- Modify: `backend/interactive_utils.py` — `action_create_model` (~8460), `action_edit_model` field list (~8519-8580), `_validate_provider_model_compat` (~8407-8451)
- Modify: `backend/api/main.py` — `CreateModelBody` (~575), `UpdateModelBody` (~600), `LlmConfigTestBody` (~508), `_build_llm_test_provider_config` (~408), and the create/edit call sites passing fields through (~2151, ~2175).
- Test: `backend/tests/test_openrouter_models_api.py` (new) — or extend existing models-api test if pattern matches.

**Interfaces:**
- Consumes: resolver field names from Task 4.
- Produces: create/edit a model with `openrouter_base_url`, `openrouter_referer`, `openrouter_title`; `/llm/test` builds an openrouter provider_config.

- [ ] **Step 1: Failing test** — assert `action_create_model` stores the three fields and `_validate_provider_model_compat("openrouter", "anthropic/claude-3.5-sonnet")` is OK while `"claude-3.5-sonnet"` (no slash) warns. (Read the existing bedrock models-api test to match harness/fixtures; mirror it.)

- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement** — add `openrouter_base_url=None, openrouter_referer=None, openrouter_title=None` params to `action_create_model` and persist them in the doc; add the three keys to the `action_edit_model` updatable-field list; add an `openrouter` clause to `_validate_provider_model_compat` (accept slash ids; warn otherwise). In `main.py` add the three `Optional[str]` fields to `CreateModelBody`/`UpdateModelBody`/`LlmConfigTestBody` (camelCase `openrouterBaseUrl`, `openrouterReferer`, `openrouterTitle` for the test body to match its peers; snake for create/update to match theirs — verify each model's existing convention), pass them through at the call sites, and add an `openrouter` branch to `_build_llm_test_provider_config`:

```python
    if provider == "openrouter":
        return {
            "openrouter_base_url": (body.openrouterBaseUrl or "https://openrouter.ai/api/v1"),
            "openrouter_referer": (body.openrouterReferer or ""),
            "openrouter_title": (body.openrouterTitle or ""),
            "reasoning_effort": (body.reasoningEffort or ""),
        }
```

- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Commit**: `git commit -am "feat(models): openrouter CRUD + compat + /llm/test config"`

---

### Task 7: Web — provider option, reasoning, test payload

**Files:**
- Modify: `frontend/src/utils/strategyConfig.js` — `LLM_PROVIDER_OPTIONS` (~303-313), reasoning options (~315-361), `buildStrategyLlmTestPayload` (~408+)

- [ ] **Step 1:** Add `{ value: 'openrouter', label: 'OpenRouter' }` to `LLM_PROVIDER_OPTIONS`.
- [ ] **Step 2:** Reuse the generic low/medium/high reasoning options for `openrouter` (map provider→options so `openrouter` returns the standard effort list, same as `openai`).
- [ ] **Step 3:** In `buildStrategyLlmTestPayload`, include `openrouterBaseUrl`, `openrouterReferer`, `openrouterTitle` when provider is `openrouter`.
- [ ] **Step 4:** If a frontend unit test harness exists for `strategyConfig.js`, add an assertion that `openrouter` is in the options; otherwise verify by `npm run build` later in Task 10.
- [ ] **Step 5: Commit**: `git commit -am "feat(web): openrouter provider option + test payload"`

---

### Task 8: Web — `useOpenRouterModels` composable

**Files:**
- Create: `frontend/src/composables/useOpenRouterModels.js` (mirror `useBedrockModels.js`)

- [ ] **Step 1:** Create the composable: `loadOpenRouterModels({ baseUrl, force })` → POSTs `{ base_url }` to `${API_BASE}/openrouter/list-models`, returns `{ models, error }`. **Preserve the full `pricing` object and `context_length`** on each row (needed for auto-fill). 30s cache keyed by baseUrl. `clearOpenRouterModelsCache()`.
- [ ] **Step 2: Commit**: `git commit -am "feat(web): useOpenRouterModels discovery composable"`

---

### Task 9: Web — `LlmConfigForm.vue` openrouter block + pricing auto-fill

**Files:**
- Modify: `frontend/src/components/LlmConfigForm.vue`

**Interfaces:**
- Consumes: `useOpenRouterModels` (Task 8); existing draft cost fields `inputCostPer1m`/`outputCostPer1m`/`cacheCreationCostPer1m`/`cacheReadCostPer1m`.

- [ ] **Step 1:** Add an `openrouter` provider-specific template block (shown when `provider === 'openrouter'`): base-url input (default prefilled), HTTP-Referer input, X-Title input, and a searchable model dropdown backed by `useOpenRouterModels` with a Refresh button and free-text fallback. Wire the reasoning-effort dropdown for `openrouter`.
- [ ] **Step 2:** Add a `onOpenRouterModelSelected(model)` handler. Helper:

```js
function _perMillion(v) {
  const n = Number(v)
  return Number.isFinite(n) ? +(n * 1_000_000).toFixed(6) : null
}
function applyOpenRouterPricing(row) {
  const p = (row && row.pricing) || {}
  const inp = _perMillion(p.prompt)
  const out = _perMillion(p.completion)
  const cr = _perMillion(p.input_cache_read)
  const cw = _perMillion(p.input_cache_write)
  if (inp != null) formDraft.inputCostPer1m = inp
  if (out != null) formDraft.outputCostPer1m = out
  if (cr != null) formDraft.cacheReadCostPer1m = cr
  if (cw != null) formDraft.cacheCreationCostPer1m = cw
}
```

On dropdown select: set `formDraft.model` and call `applyOpenRouterPricing(row)`. Values are editable (not locked). Show an "auto-filled from OpenRouter — editable" hint and a "Clear cost fields" button that nulls the four fields.
- [ ] **Step 3:** Verify `npm run build` (or `npm run lint`) from `frontend/` succeeds.
- [ ] **Step 4: Commit**: `git commit -am "feat(web): openrouter config block + pricing auto-fill"`

---

### Task 10: Web — `ModelsView.vue` draft fields

**Files:**
- Modify: `frontend/src/views/ModelsView.vue` — form-draft initial state (~69-96), reset (~155), load mapping (~193-196 area), save payload (~354 area)

- [ ] **Step 1:** Add `openrouterBaseUrl: 'https://openrouter.ai/api/v1'`, `openrouterReferer: ''`, `openrouterTitle: ''` to the initial draft + reset.
- [ ] **Step 2:** Map them on load (`m.openrouter_base_url ?? 'https://openrouter.ai/api/v1'`, etc.) and into the save payload (snake_case) alongside the nvidia fields.
- [ ] **Step 3:** `npm run build` from `frontend/` → succeeds.
- [ ] **Step 4: Commit**: `git commit -am "feat(web): ModelsView openrouter draft fields"`

---

### Task 11: Mobile — provider option, model fields, form, labels

**Files:**
- Modify: `mobile/lib/features/strategies/strategy_config.dart` (provider option ~420-429; reasoning options ~438-468)
- Modify: `mobile/lib/features/models/data/model_repository.dart` (`LlmModel` fields + JSON ~17-95)
- Modify: `mobile/lib/features/models/presentation/llm_config_form.dart` (`LlmConfigDraft` + form UI)
- Modify: `mobile/lib/features/models/presentation/models_screen.dart` (provider label + draft init)
- Test: `mobile/test/features/models/llm_config_draft_test.dart`

- [ ] **Step 1: Failing test** — assert an `LlmConfigDraft`/`LlmModel` round-trips `openrouterBaseUrl`/`openrouterReferer`/`openrouterTitle` through JSON. (Mirror the existing bedrock assertions in the file.)
- [ ] **Step 2: Run, verify fail**: `cd mobile && flutter test test/features/models/llm_config_draft_test.dart`.
- [ ] **Step 3: Implement** — add `SelectOption(value: 'openrouter', label: 'OpenRouter')`; reuse generic reasoning-effort options for openrouter; add the three string fields to `LlmModel` (+ `fromJson`/`toJson` snake_case keys) and `LlmConfigDraft` (+ constructor/copy); add form inputs (base-url default, referer, title, model free-text — dropdown deferred per spec §4.5); add the provider label + draft init in `models_screen.dart`.
- [ ] **Step 4: Run, verify pass**; also `flutter analyze` clean for touched files.
- [ ] **Step 5: Commit**: `git commit -am "feat(mobile): openrouter provider + config fields"`

---

### Task 12: Docs

**Files:**
- Modify/create: `docs/claude-code-provider-setup.md` (or a new `docs/openrouter-setup.md`)
- Modify: `backend/llm_pricing.yaml` (comment only — note openrouter rows use per-row overrides via auto-fill)

- [ ] **Step 1:** Document: get an `OPENROUTER_API_KEY`, set provider=openrouter, base URL default, optional HTTP-Referer/X-Title for leaderboard attribution, `vendor/model` ids, reasoning low/medium/high, pricing auto-fills from the dropdown into per-row cost fields (editable), per-request/image surcharges not tracked.
- [ ] **Step 2:** Add a comment block to `llm_pricing.yaml` noting openrouter relies on per-row overrides (auto-filled), not global YAML entries.
- [ ] **Step 3: Commit**: `git commit -am "docs: openrouter provider setup"`

---

## Self-Review

- **Spec coverage:** §3 config → T1/T4/T6; §4.1 dispatch → T1/T2/T3; §4.2 discovery → T5; §4.3 CRUD → T6; §4.4 web → T7/T8/T9/T10; §4.5 mobile → T11; §4.6 tests → folded into each task; §4.7 docs → T12; §7a pricing auto-fill → T8 (preserve pricing) + T9 (apply). All covered.
- **Type consistency:** `_call_openrouter` signature in T3 matches its dispatch call in T3; `applyOpenRouterPricing` reads `pricing.{prompt,completion,input_cache_read,input_cache_write}` matching OpenRouter's keys and writes the four existing draft cost fields; `list_models` row shape (`{id,name,context_length,pricing}`) in T5 matches what T8 preserves and T9 reads.
- **Placeholder scan:** new/non-obvious code (resolution, `_call_openrouter`, `openrouter_client`, pricing conversion) shown in full; mechanical mirrors point to the exact analog file+lines and list the exact fields to add.
- **Note:** several tasks instruct "read the nvidia/ollama/bedrock analog first to copy exact conventions" — this is deliberate for the mechanical-mirror steps where matching surrounding variable names matters.
