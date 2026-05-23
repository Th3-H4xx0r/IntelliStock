# Amazon Bedrock LLM Provider — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Amazon Bedrock as a first-class IntelliStock LLM provider (plain, structured, tool-calling) with discovery, reasoning control, telemetry, and critical-guard participation — full parity with the `ollama` provider.

**Architecture:** Hybrid, mirroring `ollama`. boto3 `bedrock-runtime.converse()` drives plain + tool-calling; PydanticAI `BedrockConverseModel` (fed a boto3 client) drives structured output through the existing `_build_pydantic_ai_model()` factory. Control-plane `bedrock` client drives discovery. Auth is a Bedrock **API key (bearer token)** injected per-client (no global `AWS_BEARER_TOKEN_BEDROCK`); region is a required per-model field. Dispatch is flat `if/elif` (no base class), so each touchpoint gets an explicit `bedrock` branch.

**Tech Stack:** Python, FastAPI, RethinkDB, boto3 (new), PydanticAI 1.0.18 (`bedrock` extra), Vue 3 + Tailwind, pytest.

**Spec:** `docs/superpowers/specs/2026-05-23-bedrock-provider-design.md`

**Conventions (read once):**
- Backend tests live in `backend/tests/`. Run from repo root. ALWAYS append `--ignore=backend/tests/test_intellistock_logger.py --ignore=backend/tests/test_redact_logger.py` (those two fail to collect, unrelated).
- Python is `python3` / `pip3` on this machine.
- Provider call paths **return `""`/empty (never raise)**, call `_stash_last_http(status, body, exc)` on failure, and `_safe_record(provider=..., model=..., usage=..., ok=..., duration_ms=..., retry_count=..., error=..., model_id=...)` for telemetry.
- Commit footer: `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>`. **No backticks in commit message bodies** (shell command-substitution drops the words).
- snake_case backend ↔ camelCase frontend: `bedrock_region`↔`bedrockRegion`, `bedrock_reasoning`↔`bedrockReasoning`.
- Do NOT stage `AGENTS.md` / `CLAUDE.md` (gitnexus owns those working-tree edits).

---

## Phase 1 — Dependencies & client module

### Task 1: Add the Bedrock dependency

**Files:**
- Modify: `backend/requirements.txt:54` (the `pydantic-ai-slim` line) + add a boto3 floor.

- [ ] **Step 1: Edit requirements**

Change line 54 from:
```
pydantic-ai-slim[google,openai]==1.0.18
```
to:
```
pydantic-ai-slim[google,openai,bedrock]==1.0.18
```
Then append after the ollama block (after line 65):
```
# Amazon Bedrock provider — boto3 drives the Converse API (plain + tools) and
# the control-plane bedrock client (discovery). The pydantic-ai bedrock extra
# above also pulls boto3, but we pin an explicit floor because Bedrock API-key
# (bearer-token) auth requires a recent botocore.
boto3>=1.40.0
```

- [ ] **Step 2: Install locally**

Run: `pip3 install 'pydantic-ai-slim[google,openai,bedrock]==1.0.18' 'boto3>=1.40.0'`
Expected: boto3 + botocore install; no PydanticAI version change.

- [ ] **Step 3: Verify imports resolve**

Run: `python3 -c "import boto3, botocore; from pydantic_ai.models.bedrock import BedrockConverseModel; from pydantic_ai.providers.bedrock import BedrockProvider; from pydantic_ai.models.bedrock import BedrockModelSettings; print('ok', boto3.__version__)"`
Expected: `ok 1.4x.x`

- [ ] **Step 4: Commit**

```bash
git add backend/requirements.txt
git commit -m "$(cat <<'EOF'
build(backend): add boto3 + pydantic-ai bedrock extra for Bedrock provider

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: New module `backend/bedrock_client.py`

Mirrors `backend/ollama_client.py`: typed exceptions, per-client bearer-token boto3 builders, and `list_models()` discovery.

**Files:**
- Create: `backend/bedrock_client.py`
- Test: `backend/tests/test_bedrock_client.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_bedrock_client.py
import types
import pytest
import bedrock_client as bc


def test_inject_bearer_sets_authorization_header():
    req = types.SimpleNamespace(headers={})
    handler = bc._make_bearer_injector("secret-key")
    handler(request=req)
    assert req.headers["Authorization"] == "Bearer secret-key"


def test_build_runtime_client_is_unsigned_with_region(monkeypatch):
    captured = {}

    class _FakeClient:
        def __init__(self):
            self.meta = types.SimpleNamespace(events=types.SimpleNamespace(register=lambda *a, **k: captured.setdefault("registered", a)))

    def _fake_boto3_client(service, region_name=None, config=None, **kw):
        captured["service"] = service
        captured["region"] = region_name
        captured["config"] = config
        return _FakeClient()

    monkeypatch.setattr(bc.boto3, "client", _fake_boto3_client)
    client = bc.build_runtime_client("key-123", "us-east-1")
    assert captured["service"] == "bedrock-runtime"
    assert captured["region"] == "us-east-1"
    # UNSIGNED so no AWS creds are needed; bearer header injected via event.
    assert captured["config"].signature_version == bc.botocore.UNSIGNED
    assert captured["registered"][0].startswith("before-send.bedrock-runtime")


def test_list_models_maps_access_denied_to_auth_error(monkeypatch):
    from botocore.exceptions import ClientError

    class _Denied:
        def list_foundation_models(self, **kw):
            raise ClientError({"Error": {"Code": "AccessDeniedException", "Message": "no"}}, "ListFoundationModels")
        def list_inference_profiles(self, **kw):
            return {"inferenceProfileSummaries": []}

    monkeypatch.setattr(bc, "build_control_client", lambda api_key, region: _Denied())
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

    monkeypatch.setattr(bc, "build_control_client", lambda api_key, region: _Ok())
    out = bc.list_models("key", "us-east-1")
    ids = {m["id"] for m in out}
    assert "anthropic.claude-3-5-sonnet-20241022-v2:0" in ids
    assert "us.anthropic.claude-3-5-sonnet-20241022-v2:0" in ids
    kinds = {m["id"]: m["kind"] for m in out}
    assert kinds["us.anthropic.claude-3-5-sonnet-20241022-v2:0"] == "inference_profile"
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `python3 -m pytest backend/tests/test_bedrock_client.py -v --ignore=backend/tests/test_intellistock_logger.py --ignore=backend/tests/test_redact_logger.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'bedrock_client'` (tests import it).

> Note: `backend/tests/` add `backend/` to `sys.path` via conftest — confirm `import bedrock_client` resolves like `import ollama_client` does in existing tests. If not, use the same import style the existing ollama tests use.

- [ ] **Step 3: Implement `backend/bedrock_client.py`**

```python
"""Thin wrappers over boto3 for Amazon Bedrock.

Responsibility: keep non-chat operations (discovery, client construction,
bearer-token auth) out of llm_utils.py. The chat paths live in
llm_utils._call_bedrock / call_bedrock_with_tools and the structured path in
_build_pydantic_ai_model; this module owns list_foundation_models /
list_inference_profiles plus the shared exception classes and the per-client
bearer-token injection.

Auth: Bedrock API key (bearer token), injected per boto3 client via a
before-send event handler with an UNSIGNED signer — so concurrent clients
built with different keys never collide and no AWS credentials are required.

Used by:
  * backend/api/main.py — the POST /bedrock/list-models endpoint
  * backend/llm_utils.py — builds runtime clients for Converse calls
"""
from __future__ import annotations

from typing import Any, Optional

import boto3
import botocore
from botocore.config import Config
from botocore.exceptions import ClientError, EndpointConnectionError, ConnectionError as BotoConnError


class BedrockConnectionError(Exception):
    """Network failure reaching the Bedrock endpoint (DNS, refused, timeout)."""


class BedrockAuthError(Exception):
    """Authentication / authorization failure (401/403, AccessDenied, expired/invalid token)."""


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


def _make_bearer_injector(api_key: str):
    """Return a before-send handler that sets the bearer Authorization header."""
    token = str(api_key or "").strip()

    def _inject(request=None, **_kwargs):
        if request is not None and token:
            request.headers["Authorization"] = f"Bearer {token}"
        return None

    return _inject


def _client_config(timeout_sec: float = 30.0, max_attempts: int = 1) -> Config:
    return Config(
        signature_version=botocore.UNSIGNED,  # bearer token via header, not SigV4
        read_timeout=float(timeout_sec),
        connect_timeout=min(15.0, float(timeout_sec)),
        retries={"max_attempts": max_attempts, "mode": "standard"},
    )


def _build_client(service: str, api_key: str, region: str, *, timeout_sec: float = 30.0):
    region = str(region or "").strip()
    if not region:
        raise BedrockProviderError("Bedrock requires a region (e.g. us-east-1)")
    client = boto3.client(service, region_name=region, config=_client_config(timeout_sec))
    client.meta.events.register(f"before-send.{service}", _make_bearer_injector(api_key))
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


def list_models(api_key: str, region: str, *, timeout_sec: float = 15.0) -> list[dict[str, Any]]:
    """Return Bedrock foundation models + cross-region inference profiles.

    Raises BedrockAuthError / BedrockConnectionError / BedrockProviderError.
    Discovery may be unavailable for narrowly-scoped API keys (the key may
    authorize Converse but not bedrock:ListFoundationModels); callers degrade
    to manual model-id entry on BedrockAuthError.
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
    """Cheap probe used by UI. Never raises — flattens to (ok, error)."""
    try:
        list_models(api_key, region, timeout_sec=timeout_sec)
        return True, ""
    except (BedrockAuthError, BedrockConnectionError, BedrockProviderError) as e:
        return False, str(e)
```

- [ ] **Step 4: Run tests, verify they pass**

Run: `python3 -m pytest backend/tests/test_bedrock_client.py -v --ignore=backend/tests/test_intellistock_logger.py --ignore=backend/tests/test_redact_logger.py`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/bedrock_client.py backend/tests/test_bedrock_client.py
git commit -m "$(cat <<'EOF'
feat(bedrock_client): boto3 wrappers, bearer-token clients, model discovery

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase 2 — Core call paths in `backend/llm_utils.py`

### Task 3: Add PydanticAI Bedrock imports

**Files:**
- Modify: `backend/llm_utils.py:39-60` (the PydanticAI try/except import block)

- [ ] **Step 1: Add imports**

In the `try:` block (after line 48 `from pydantic_ai.settings import ModelSettings`), add:
```python
    from pydantic_ai.models.bedrock import BedrockConverseModel, BedrockModelSettings
    from pydantic_ai.providers.bedrock import BedrockProvider
```
In the `except Exception:` block (after line 60 `ModelSettings = None`), add:
```python
    BedrockConverseModel = None
    BedrockModelSettings = None
    BedrockProvider = None
```

- [ ] **Step 2: Verify import**

Run: `python3 -c "import sys; sys.path.insert(0,'backend'); import llm_utils; print(llm_utils.BedrockConverseModel is not None)"`
Expected: `True`

- [ ] **Step 3: Commit** (bundle with Task 4).

---

### Task 4: `_normalize_bedrock_reasoning` helper

Maps `off/low/medium/high` → Converse `additionalModelRequestFields`. Model-aware: only Anthropic Claude reasoning models get a reasoning_config; everything else (and `off`/unknown) returns `None` (omit the field — sending it 400s).

**Files:**
- Modify: `backend/llm_utils.py` — add helper near the ollama normalizers (after `_normalize_ollama_keep_alive`, ~line 3728)
- Test: `backend/tests/test_bedrock_calls.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_bedrock_calls.py
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ))  # backend on path if not already
import llm_utils


def test_reasoning_off_returns_none():
    assert llm_utils._normalize_bedrock_reasoning("off", "us.anthropic.claude-3-7-sonnet-20250219-v1:0") is None
    assert llm_utils._normalize_bedrock_reasoning("", "anthropic.claude-3-5-sonnet-20241022-v2:0") is None


def test_reasoning_claude_maps_to_budget():
    out = llm_utils._normalize_bedrock_reasoning("medium", "us.anthropic.claude-3-7-sonnet-20250219-v1:0")
    assert out == {"reasoning_config": {"type": "enabled", "budget_tokens": 4096}}
    hi = llm_utils._normalize_bedrock_reasoning("high", "anthropic.claude-opus-4-20250514-v1:0")
    assert hi["reasoning_config"]["budget_tokens"] == 16384


def test_reasoning_omitted_for_non_claude():
    # Llama / Nova / Mistral don't take Claude's reasoning_config — omit to avoid 400.
    assert llm_utils._normalize_bedrock_reasoning("high", "meta.llama3-1-70b-instruct-v1:0") is None
    assert llm_utils._normalize_bedrock_reasoning("high", "amazon.nova-pro-v1:0") is None
```

- [ ] **Step 2: Run, verify fail**

Run: `python3 -m pytest backend/tests/test_bedrock_calls.py -v --ignore=backend/tests/test_intellistock_logger.py --ignore=backend/tests/test_redact_logger.py`
Expected: FAIL — `AttributeError: module 'llm_utils' has no attribute '_normalize_bedrock_reasoning'`.

- [ ] **Step 3: Implement** (insert after `_normalize_ollama_keep_alive`, before `_resolve_ollama_timeout`):

```python
_BEDROCK_REASONING_BUDGETS = {"low": 1024, "medium": 4096, "high": 16384}


def _normalize_bedrock_reasoning(value, model) -> dict | None:
    """Map a bedrock_reasoning effort to Converse additionalModelRequestFields.

    Only Anthropic Claude reasoning-capable models (3.7+, Sonnet/Opus 4) accept
    the reasoning_config block; sending it to other families (Llama, Nova,
    Mistral) — or to older Claude — yields a ValidationException. So we emit the
    field only for anthropic/claude model ids and omit (return None) otherwise.
    Older Claude models that reject reasoning surface a classified config error;
    the operator sets reasoning=off. (Requires Claude 3.7+.)
    """
    effort = str(value or "").strip().lower()
    if effort not in _BEDROCK_REASONING_BUDGETS:
        return None
    m = str(model or "").strip().lower()
    if "anthropic" in m or "claude" in m:
        return {"reasoning_config": {"type": "enabled", "budget_tokens": _BEDROCK_REASONING_BUDGETS[effort]}}
    return None
```

- [ ] **Step 4: Run, verify pass**

Run: `python3 -m pytest backend/tests/test_bedrock_calls.py -v --ignore=backend/tests/test_intellistock_logger.py --ignore=backend/tests/test_redact_logger.py`
Expected: 3 passed.

- [ ] **Step 5: Commit** (bundle Tasks 3+4):

```bash
git add backend/llm_utils.py backend/tests/test_bedrock_calls.py
git commit -m "$(cat <<'EOF'
feat(llm_utils): bedrock PydanticAI imports + reasoning normalizer

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Config helpers — api-key, cache-effort, provider-config, provider-meta

**Files:**
- Modify: `backend/llm_utils.py` — `resolve_api_key_for_provider` (~220), `_cache_effort_key` (~587), `_resolve_provider_config` (~657 ollama branch end), `_safe_provider_meta` (~712)
- Test: append to `backend/tests/test_bedrock_calls.py`

- [ ] **Step 1: Write failing tests** (append):

```python
def test_resolve_api_key_bedrock_env(monkeypatch):
    monkeypatch.setenv("BEDROCK_API_KEY", "envkey")
    assert llm_utils.resolve_api_key_for_provider("bedrock", None) == "envkey"
    assert llm_utils.resolve_api_key_for_provider("bedrock", "explicit") == "explicit"


def test_cache_effort_key_bedrock_isolates_reasoning():
    k_off = llm_utils._cache_effort_key("bedrock", {"bedrock_reasoning": "off"})
    k_hi = llm_utils._cache_effort_key("bedrock", {"bedrock_reasoning": "high"})
    assert k_off != k_hi
    assert k_hi.startswith("reason:")


def test_resolve_provider_config_bedrock_keeps_region_reasoning():
    cfg = llm_utils._resolve_provider_config("bedrock", {"bedrock_region": "us-west-2", "bedrock_reasoning": "Low"})
    assert cfg["bedrock_region"] == "us-west-2"
    assert cfg["bedrock_reasoning"] == "low"
    assert "reasoning_effort" not in cfg
```

- [ ] **Step 2: Run, verify fail.**
Run: `python3 -m pytest backend/tests/test_bedrock_calls.py -k "bedrock_env or effort_key_bedrock or provider_config_bedrock" -v --ignore=backend/tests/test_intellistock_logger.py --ignore=backend/tests/test_redact_logger.py`
Expected: FAIL (env returns "", effort/region not handled).

- [ ] **Step 3a:** In `resolve_api_key_for_provider`, after the `if p == "ollama":` block (line ~224) add:
```python
    if p == "bedrock":
        return str(os.environ.get("BEDROCK_API_KEY") or "").strip()
```

- [ ] **Step 3b:** In `_cache_effort_key`, before the final `return` (line ~592), add a branch:
```python
    if (provider or "").strip().lower() == "bedrock":
        v = str(pc.get("bedrock_reasoning", "") or "").strip().lower()
        return f"reason:{v}" if v and v != "off" else ""
```

- [ ] **Step 3c:** In `_resolve_provider_config`, add an `elif p == "bedrock":` branch (after the ollama branch ends at line ~681, before `return resolved`):
```python
    elif p == "bedrock":
        region = str(resolved.get("bedrock_region") or os.environ.get("BEDROCK_REGION") or os.environ.get("AWS_REGION") or "").strip()
        if region:
            resolved["bedrock_region"] = region
        reasoning = str(resolved.get("bedrock_reasoning") or "").strip().lower()
        if reasoning and reasoning != "off":
            resolved["bedrock_reasoning"] = reasoning
        else:
            resolved.pop("bedrock_reasoning", None)
        resolved.pop("reasoning_effort", None)
```

- [ ] **Step 3d:** In `_safe_provider_meta`, before its final `return {}` (line ~712), add:
```python
    if p == "bedrock":
        meta = {"bedrock_region": str(config.get("bedrock_region") or "")}
        reasoning = str(config.get("bedrock_reasoning") or "").strip().lower()
        if reasoning and reasoning != "off":
            meta["bedrock_reasoning"] = reasoning
        return meta
```

- [ ] **Step 4: Run, verify pass.** Same `-k` command as Step 2 → 3 passed.

- [ ] **Step 5: Commit**
```bash
git add backend/llm_utils.py backend/tests/test_bedrock_calls.py
git commit -m "$(cat <<'EOF'
feat(llm_utils): bedrock api-key/cache-effort/provider-config/meta wiring

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: `_call_bedrock` (plain text via Converse)

**Files:**
- Modify: `backend/llm_utils.py` — add `_call_bedrock` near the other `_call_*` (e.g. after `_call_nvidia`)
- Test: append to `backend/tests/test_bedrock_calls.py`

- [ ] **Step 1: Write failing tests** (append). Tests mock `bedrock_client.build_runtime_client` to return a fake client whose `.converse` returns a Converse-shaped dict, and assert text extraction + telemetry + empty-on-error.

```python
import types as _types

class _FakeConverseClient:
    def __init__(self, response=None, error=None):
        self._response = response
        self._error = error
        self.calls = []
    def converse(self, **kwargs):
        self.calls.append(kwargs)
        if self._error:
            raise self._error
        return self._response

def _ok_converse(text="hello", in_tok=10, out_tok=5):
    return {"output": {"message": {"role": "assistant", "content": [{"text": text}]}},
            "usage": {"inputTokens": in_tok, "outputTokens": out_tok},
            "stopReason": "end_turn"}

def test_call_bedrock_happy_path(monkeypatch):
    fake = _FakeConverseClient(response=_ok_converse("hi there"))
    monkeypatch.setattr(llm_utils.bedrock_client, "build_runtime_client", lambda api_key, region, **kw: fake)
    out = llm_utils._call_bedrock("key", "anthropic.claude-3-5-sonnet-20241022-v2:0", "ping", region="us-east-1")
    assert out == "hi there"
    assert fake.calls[0]["modelId"] == "anthropic.claude-3-5-sonnet-20241022-v2:0"

def test_call_bedrock_includes_reasoning_for_claude(monkeypatch):
    fake = _FakeConverseClient(response=_ok_converse())
    monkeypatch.setattr(llm_utils.bedrock_client, "build_runtime_client", lambda api_key, region, **kw: fake)
    llm_utils._call_bedrock("key", "us.anthropic.claude-3-7-sonnet-20250219-v1:0", "ping",
                            region="us-east-1", reasoning="high")
    assert fake.calls[0]["additionalModelRequestFields"]["reasoning_config"]["budget_tokens"] == 16384

def test_call_bedrock_returns_empty_on_client_error(monkeypatch):
    from botocore.exceptions import ClientError
    err = ClientError({"Error": {"Code": "AccessDeniedException", "Message": "no"},
                       "ResponseMetadata": {"HTTPStatusCode": 403}}, "Converse")
    fake = _FakeConverseClient(error=err)
    monkeypatch.setattr(llm_utils.bedrock_client, "build_runtime_client", lambda api_key, region, **kw: fake)
    out = llm_utils._call_bedrock("key", "anthropic.claude-3-5-sonnet-20241022-v2:0", "ping", region="us-east-1")
    assert out == ""
    captured = llm_utils._pop_last_http() or {}
    assert captured.get("status") == 403
```

- [ ] **Step 2: Run, verify fail** (`_call_bedrock`/`bedrock_client` attr missing).
Run: `python3 -m pytest backend/tests/test_bedrock_calls.py -k call_bedrock -v --ignore=backend/tests/test_intellistock_logger.py --ignore=backend/tests/test_redact_logger.py`

- [ ] **Step 3a:** Near the top imports of llm_utils.py (after the rethinkdb try-block, ~line 38), add a defensive import so `llm_utils.bedrock_client` exists and is monkeypatchable:
```python
try:
    import bedrock_client
except Exception:
    bedrock_client = None
```

- [ ] **Step 3b:** Implement `_call_bedrock` (place after `_call_nvidia`'s definition):
```python
def _call_bedrock(
    api_key: str,
    model: str,
    prompt: str,
    max_output_tokens: int = 256,
    timeout_sec: int | None = None,
    retries: int = 0,
    region: str = "",
    response_mime_type: str | None = None,
    reasoning: str = "",
) -> str:
    """Plain-text chat against Amazon Bedrock via the Converse API.

    Mirrors _call_ollama semantics: returns "" on every failure (no raise),
    stashes the HTTP shape via _stash_last_http, records telemetry. boto3 is
    synchronous; the client carries the bearer token + region.
    """
    _t0 = time.monotonic()
    if bedrock_client is None or not model or not api_key or not str(region or "").strip():
        try:
            _stash_last_http(status=None, body="bedrock not configured (region/key/model)", exc=None)
        except Exception:
            pass
        return ""
    from botocore.exceptions import ClientError

    messages = [{"role": "user", "content": [{"text": prompt}]}]
    inference_config: dict[str, object] = {}
    if max_output_tokens and int(max_output_tokens) > 0:
        inference_config["maxTokens"] = int(max_output_tokens)
    converse_kwargs: dict[str, object] = {"modelId": model, "messages": messages}
    if inference_config:
        converse_kwargs["inferenceConfig"] = inference_config
    amrf = _normalize_bedrock_reasoning(reasoning, model)
    if amrf:
        converse_kwargs["additionalModelRequestFields"] = amrf
    if response_mime_type and "json" in str(response_mime_type).lower():
        converse_kwargs["system"] = [{"text": "Respond with ONLY a single valid JSON value. No prose, no markdown fences."}]

    timeout = float(_coerce_timeout_sec(timeout_sec))
    max_retries = max(0, int(retries or 0))
    last_status: int | None = None
    last_body: str | None = None
    last_exc: BaseException | None = None

    for attempt in range(max_retries + 1):
        try:
            client = bedrock_client.build_runtime_client(api_key, region, timeout_sec=timeout)
            resp = client.converse(**converse_kwargs)
        except ClientError as e:
            code = (e.response or {}).get("Error", {}).get("Code", "")
            last_status = (e.response or {}).get("ResponseMetadata", {}).get("HTTPStatusCode")
            last_body = f"{code}: {(e.response or {}).get('Error', {}).get('Message', str(e))}"[:1000]
            last_exc = e
            transient = code in ("ThrottlingException", "TooManyRequestsException",
                                 "ServiceUnavailableException", "InternalServerException",
                                 "ModelNotReadyException") or (isinstance(last_status, int) and 500 <= last_status < 600)
            if transient and attempt < max_retries:
                time.sleep(_backoff_sleep_seconds(attempt))
                continue
            break
        except Exception as e:
            last_status = None
            last_body = str(e)[:1000]
            last_exc = e
            break

        content = (((resp or {}).get("output") or {}).get("message") or {}).get("content") or []
        text = "".join(b.get("text", "") for b in content if isinstance(b, dict) and "text" in b)
        if not text:  # reasoning-only output → fall back to reasoning text blocks
            text = "".join(
                (b.get("reasoningContent", {}).get("reasoningText", {}) or {}).get("text", "")
                for b in content if isinstance(b, dict) and "reasoningContent" in b
            )
        usage = (resp or {}).get("usage") or {}
        _in_tok = int(usage.get("inputTokens") or 0)
        _out_tok = int(usage.get("outputTokens") or 0)
        try:
            import sys as _sys
            print(f"[llm_utils] BEDROCK TOKENS: model={model!r} in_tokens={_in_tok} "
                  f"out_tokens={_out_tok} content_chars={len(text)}", file=_sys.stderr, flush=True)
        except Exception:
            pass
        try:
            _stash_last_http(status=200, body=None, exc=None)
        except Exception:
            pass
        try:
            _safe_record(provider="bedrock", model=model,
                         usage={"input_tokens": _in_tok, "output_tokens": _out_tok},
                         ok=True, duration_ms=int((time.monotonic() - _t0) * 1000),
                         retry_count=attempt, error=None, model_id=None)
        except Exception:
            pass
        return text

    try:
        _stash_last_http(status=last_status, body=last_body, exc=last_exc)
    except Exception:
        pass
    try:
        _LAST_PLAIN_LLM_CALL_ERROR.error = str(last_exc) if last_exc else (last_body or "")
    except Exception:
        pass
    try:
        _safe_record(provider="bedrock", model=model, usage={}, ok=False,
                     duration_ms=int((time.monotonic() - _t0) * 1000), retry_count=max_retries,
                     error=(str(last_exc)[:200] if last_exc else (last_body[:200] if last_body else "unknown")),
                     model_id=None)
    except Exception:
        pass
    return ""
```

- [ ] **Step 4: Run, verify pass.** Same `-k call_bedrock` command → 3 passed.

- [ ] **Step 5: Commit**
```bash
git add backend/llm_utils.py backend/tests/test_bedrock_calls.py
git commit -m "$(cat <<'EOF'
feat(llm_utils): _call_bedrock plain Converse path (empty-on-failure + telemetry)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: `call_bedrock_with_tools` (single-shot, matches `call_ollama_with_tools`)

Single-shot like the ollama tools entrypoint: returns `{"text": str, "tool_calls": [{"name", "arguments"}]}`, does NOT execute tools. Converts tool dicts to Converse `toolSpec`.

**Files:**
- Modify: `backend/llm_utils.py` — add after `_call_bedrock`
- Test: append to `backend/tests/test_bedrock_calls.py`

- [ ] **Step 1: Write failing test**:
```python
def test_call_bedrock_with_tools_parses_tooluse(monkeypatch):
    resp = {"output": {"message": {"role": "assistant", "content": [
                {"text": "calling"},
                {"toolUse": {"toolUseId": "t1", "name": "lookup", "input": {"q": "AAPL"}}}]}},
            "usage": {"inputTokens": 3, "outputTokens": 4}, "stopReason": "tool_use"}
    fake = _FakeConverseClient(response=resp)
    monkeypatch.setattr(llm_utils.bedrock_client, "build_runtime_client", lambda api_key, region, **kw: fake)
    tools = [{"type": "function", "function": {"name": "lookup", "description": "d",
              "parameters": {"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]}}}]
    out = llm_utils.call_bedrock_with_tools("key", "anthropic.claude-3-5-sonnet-20241022-v2:0",
                                            "find AAPL", tools, region="us-east-1")
    assert out["tool_calls"] == [{"name": "lookup", "arguments": {"q": "AAPL"}}]
    assert "calling" in out["text"]
    assert "toolConfig" in fake.calls[0]

def test_call_bedrock_with_tools_empty_on_error(monkeypatch):
    from botocore.exceptions import ClientError
    err = ClientError({"Error": {"Code": "ValidationException", "Message": "bad"},
                       "ResponseMetadata": {"HTTPStatusCode": 400}}, "Converse")
    fake = _FakeConverseClient(error=err)
    monkeypatch.setattr(llm_utils.bedrock_client, "build_runtime_client", lambda api_key, region, **kw: fake)
    out = llm_utils.call_bedrock_with_tools("key", "m", "p", [], region="us-east-1")
    assert out == {"text": "", "tool_calls": []}
```

- [ ] **Step 2: Run, verify fail.**
Run: `python3 -m pytest backend/tests/test_bedrock_calls.py -k bedrock_with_tools -v --ignore=backend/tests/test_intellistock_logger.py --ignore=backend/tests/test_redact_logger.py`

- [ ] **Step 3: Implement** (after `_call_bedrock`):
```python
def _tools_to_bedrock_toolspec(tools: list[dict]) -> list[dict]:
    """Convert OpenAI/Gemini-shape tool dicts to Converse toolSpec entries."""
    normalised = _normalize_tools_to_openai_shape(tools or [])
    specs = []
    for t in normalised:
        fn = (t or {}).get("function") or {}
        name = fn.get("name")
        if not name:
            continue
        specs.append({"toolSpec": {
            "name": name,
            "description": fn.get("description", "") or name,
            "inputSchema": {"json": fn.get("parameters") or {"type": "object", "properties": {}}},
        }})
    return specs


def call_bedrock_with_tools(
    api_key: str | None,
    model: str,
    prompt: str,
    tools: list[dict],
    *,
    region: str = "",
    timeout_sec=None,
    max_output_tokens: int = 1024,
    reasoning: str = "",
) -> dict:
    """Single-shot tool-using Converse call. Returns {"text", "tool_calls"};
    does NOT execute tools or loop (mirrors call_ollama_with_tools). Returns
    {"text": "", "tool_calls": []} on any failure."""
    _t0 = time.monotonic()
    if bedrock_client is None or not model or not api_key or not str(region or "").strip():
        return {"text": "", "tool_calls": []}
    from botocore.exceptions import ClientError

    converse_kwargs: dict[str, object] = {
        "modelId": model,
        "messages": [{"role": "user", "content": [{"text": prompt}]}],
    }
    if max_output_tokens and int(max_output_tokens) > 0:
        converse_kwargs["inferenceConfig"] = {"maxTokens": int(max_output_tokens)}
    specs = _tools_to_bedrock_toolspec(tools or [])
    if specs:
        converse_kwargs["toolConfig"] = {"tools": specs}
    amrf = _normalize_bedrock_reasoning(reasoning, model)
    if amrf:
        converse_kwargs["additionalModelRequestFields"] = amrf

    timeout = float(_coerce_timeout_sec(timeout_sec))
    try:
        client = bedrock_client.build_runtime_client(api_key, region, timeout_sec=timeout)
        resp = client.converse(**converse_kwargs)
    except ClientError as e:
        try:
            _stash_last_http(status=(e.response or {}).get("ResponseMetadata", {}).get("HTTPStatusCode"),
                             body=str(e)[:1000], exc=e)
            _safe_record(provider="bedrock", model=model, usage={}, ok=False,
                         duration_ms=int((time.monotonic() - _t0) * 1000), retry_count=0,
                         error=str(e)[:200], model_id=None)
        except Exception:
            pass
        return {"text": "", "tool_calls": []}
    except Exception as e:
        try:
            _stash_last_http(status=None, body=str(e)[:1000], exc=e)
        except Exception:
            pass
        return {"text": "", "tool_calls": []}

    content = (((resp or {}).get("output") or {}).get("message") or {}).get("content") or []
    text = "".join(b.get("text", "") for b in content if isinstance(b, dict) and "text" in b)
    tool_calls = [{"name": b["toolUse"].get("name", ""), "arguments": b["toolUse"].get("input") or {}}
                  for b in content if isinstance(b, dict) and "toolUse" in b]
    usage = (resp or {}).get("usage") or {}
    try:
        _stash_last_http(status=200, body=None, exc=None)
        _safe_record(provider="bedrock", model=model,
                     usage={"input_tokens": int(usage.get("inputTokens") or 0),
                            "output_tokens": int(usage.get("outputTokens") or 0)},
                     ok=True, duration_ms=int((time.monotonic() - _t0) * 1000),
                     retry_count=0, error=None, model_id=None)
    except Exception:
        pass
    return {"text": text, "tool_calls": tool_calls}
```

- [ ] **Step 4: Run, verify pass.** `-k bedrock_with_tools` → 2 passed.

- [ ] **Step 5: Commit**
```bash
git add backend/llm_utils.py backend/tests/test_bedrock_calls.py
git commit -m "$(cat <<'EOF'
feat(llm_utils): call_bedrock_with_tools single-shot Converse tool calls

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: `_build_pydantic_ai_model` bedrock branch (structured path)

Builds `BedrockConverseModel(model, provider=BedrockProvider(bedrock_client=...), settings=BedrockModelSettings(...))`. Reasoning threads in via `bedrock_additional_model_requests_fields` (confirmed: the model copies it to `additionalModelRequestFields`).

**Files:**
- Modify: `backend/llm_utils.py:817-836` (the `if p == "ollama":` branch in `_build_pydantic_ai_model`) — add a `bedrock` branch before it.
- Test: append to `backend/tests/test_bedrock_calls.py`

- [ ] **Step 1: Write failing test**:
```python
def test_build_pydantic_ai_model_bedrock(monkeypatch):
    fake_client = object()
    monkeypatch.setattr(llm_utils.bedrock_client, "build_runtime_client", lambda api_key, region, **kw: fake_client)
    m = llm_utils._build_pydantic_ai_model(
        "bedrock", "key", "us.anthropic.claude-3-7-sonnet-20250219-v1:0",
        {"bedrock_region": "us-east-1", "bedrock_reasoning": "medium"})
    assert m is not None
    assert m.__class__.__name__ == "BedrockConverseModel"

def test_build_pydantic_ai_model_bedrock_requires_key(monkeypatch):
    assert llm_utils._build_pydantic_ai_model("bedrock", "", "m", {"bedrock_region": "us-east-1"}) is None
```

- [ ] **Step 2: Run, verify fail** (falls through to GoogleModel / returns wrong class).
Run: `python3 -m pytest backend/tests/test_bedrock_calls.py -k build_pydantic_ai_model_bedrock -v --ignore=backend/tests/test_intellistock_logger.py --ignore=backend/tests/test_redact_logger.py`

- [ ] **Step 3: Implement.** Insert before `if p == "ollama":` (line 817):
```python
    if p == "bedrock":
        if BedrockConverseModel is None or BedrockProvider is None:
            return None
        region = str(resolved.get("bedrock_region")
                     or os.environ.get("BEDROCK_REGION")
                     or os.environ.get("AWS_REGION") or "").strip()
        if not region or bedrock_client is None:
            return None
        client = bedrock_client.build_runtime_client(api_key, region)
        settings = None
        amrf = _normalize_bedrock_reasoning(resolved.get("bedrock_reasoning"), model)
        if amrf and BedrockModelSettings is not None:
            settings = BedrockModelSettings(bedrock_additional_model_requests_fields=amrf)
        return BedrockConverseModel(
            model,
            provider=BedrockProvider(bedrock_client=client),
            settings=settings,
        )
```
> Note: `_build_pydantic_ai_model` already returns `None` for `not api_key and p != "ollama"` (line 722) — so the empty-key test passes without extra code. Verify the line 722 guard still reads `p != "ollama"` (bedrock is NOT exempt). Leave it.

- [ ] **Step 4: Run, verify pass.** `-k build_pydantic_ai_model_bedrock` → 2 passed.

- [ ] **Step 5: Commit**
```bash
git add backend/llm_utils.py backend/tests/test_bedrock_calls.py
git commit -m "$(cat <<'EOF'
feat(llm_utils): bedrock branch in _build_pydantic_ai_model (structured path)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 9: Wire dispatch — `call_llm_by_provider` (plain) + structured guard

**Files:**
- Modify: `backend/llm_utils.py:5139-5151` (add `elif p == "bedrock":` in `call_llm_by_provider`, before the `else: _call_gemini`)
- Test: append to `backend/tests/test_bedrock_calls.py`

The structured dispatcher (`call_structured_llm_by_provider`) is already provider-agnostic — it builds the model via `_build_pydantic_ai_model` (Task 8) and its `if not api_key and provider != "ollama"` guard (line 2404) correctly requires a key for bedrock. **No change needed there.**

- [ ] **Step 1: Write failing test**:
```python
def test_dispatch_routes_bedrock_to_call_bedrock(monkeypatch):
    called = {}
    def _fake(api_key, model, prompt, **kw):
        called["args"] = (api_key, model, prompt, kw)
        return "routed"
    monkeypatch.setattr(llm_utils, "_call_bedrock", _fake)
    # bypass prompt cache + rate limiter for a clean unit check
    monkeypatch.setattr(llm_utils, "_check_prompt_cache", lambda *a, **k: None)
    monkeypatch.setattr(llm_utils, "_store_prompt_cache", lambda *a, **k: None)
    monkeypatch.setattr(llm_utils, "_get_model_rate_limiter", lambda *a, **k: None)
    out = llm_utils.call_llm_by_provider("bedrock", "key", "anthropic.claude-x", "hi",
                                         provider_config={"bedrock_region": "us-east-1", "bedrock_reasoning": "low"})
    assert out == "routed"
    assert called["args"][3]["region"] == "us-east-1"
    assert called["args"][3]["reasoning"] == "low"
```

- [ ] **Step 2: Run, verify fail** (routes to gemini else-branch).
Run: `python3 -m pytest backend/tests/test_bedrock_calls.py -k dispatch_routes_bedrock -v --ignore=backend/tests/test_intellistock_logger.py --ignore=backend/tests/test_redact_logger.py`

- [ ] **Step 3: Implement.** In `call_llm_by_provider`, add before the final `else:` (line 5152):
```python
    elif p == "bedrock":
        _result = _call_bedrock(
            api_key,
            model,
            prompt,
            max_output_tokens=max_output_tokens,
            timeout_sec=timeout_sec,
            retries=retries,
            region=str(resolved.get("bedrock_region") or ""),
            response_mime_type=response_mime_type,
            reasoning=str(resolved.get("bedrock_reasoning") or ""),
        )
```

- [ ] **Step 4: Run, verify pass.** `-k dispatch_routes_bedrock` → 1 passed. Then run the whole bedrock file:
Run: `python3 -m pytest backend/tests/test_bedrock_calls.py backend/tests/test_bedrock_client.py -v --ignore=backend/tests/test_intellistock_logger.py --ignore=backend/tests/test_redact_logger.py`
Expected: all green.

- [ ] **Step 5: Commit**
```bash
git add backend/llm_utils.py backend/tests/test_bedrock_calls.py
git commit -m "$(cat <<'EOF'
feat(llm_utils): route provider=bedrock through _call_bedrock in dispatcher

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase 3 — Failure classification & model-ref resolution

### Task 10: `llm_critical_guard.py` — Bedrock error classification

**Files:**
- Modify: `backend/llm_critical_guard.py:40-51` (extend `_RX_AUTH`, add a config-error matcher) and `classify()` (~54-101)
- Test: `backend/tests/test_critical_guard_bedrock.py`

- [ ] **Step 1: Write failing tests**:
```python
# backend/tests/test_critical_guard_bedrock.py
import llm_critical_guard as g

def setup_function(_):
    g.reset_state()

def test_bedrock_access_denied_is_auth_failure():
    tag, crit = g.classify(status=403, body="AccessDeniedException: not authorized", provider="bedrock", model="m")
    assert (tag, crit) == ("auth_failure", True)

def test_bedrock_expired_token_is_auth_failure():
    tag, crit = g.classify(status=400, body="ExpiredTokenException: token expired", provider="bedrock", model="m")
    assert crit is True and tag == "auth_failure"

def test_bedrock_validation_error_not_critical():
    # Bad model id / missing inference profile is a config error, not a retry storm.
    tag, crit = g.classify(status=400, body="ValidationException: model not found", provider="bedrock", model="m")
    assert crit is False

def test_bedrock_5xx_persists_after_three():
    for _ in range(3):
        g.update_consecutive_state(tag="x", status=500, provider="bedrock", model="m")
    tag, crit = g.classify(status=500, body="InternalServerException", provider="bedrock", model="m")
    assert (tag, crit) == ("provider_5xx_persistent", True)
```

- [ ] **Step 2: Run, verify fail** (`AccessDeniedException`/`ExpiredTokenException` not matched by `_RX_AUTH`).
Run: `python3 -m pytest backend/tests/test_critical_guard_bedrock.py -v --ignore=backend/tests/test_intellistock_logger.py --ignore=backend/tests/test_redact_logger.py`

- [ ] **Step 3: Implement.** Extend `_RX_AUTH` (line 40-50) to add the Bedrock auth codes — append these alternations before the closing `)`:
```python
    r"|AccessDeniedException"
    r"|UnrecognizedClientException"
    r"|InvalidSignatureException"
    r"|ExpiredTokenException"
    r"|ForbiddenException"
```
(The existing `unauthorized` + `status == 401` branches already cover 401; the `403` Bedrock case is caught by the new `AccessDeniedException` body match. ValidationException matches none of the critical patterns → returns `("none", False)` automatically, satisfying the not-critical test. The 5xx-persistent path is provider-agnostic and already works.)

- [ ] **Step 4: Run, verify pass.** → 4 passed. Then run the existing guard tests to confirm no regression:
Run: `python3 -m pytest backend/tests/ -k "critical_guard or guard" -v --ignore=backend/tests/test_intellistock_logger.py --ignore=backend/tests/test_redact_logger.py`

- [ ] **Step 5: Commit**
```bash
git add backend/llm_critical_guard.py backend/tests/test_critical_guard_bedrock.py
git commit -m "$(cat <<'EOF'
feat(llm_critical_guard): classify Bedrock auth error codes as auth_failure

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 11: `model_resolver.py` — expand bedrock fields

**Files:**
- Modify: `backend/model_resolver.py:142-160` (`always_overwrite` + `field_map`)
- Test: `backend/tests/test_model_resolver_bedrock.py` (mirror an existing resolver test; if none exists, test the field_map mapping directly)

- [ ] **Step 1: Read the current resolver block.** Read `backend/model_resolver.py:130-180` to see exact `field_map` keys + the prefix logic + how an existing resolver test constructs `conn`/config. Mirror that test harness.

- [ ] **Step 2: Write failing test** modeled on the existing resolver test (mock `conn` returning a Models row with provider=bedrock + bedrock_region + bedrock_reasoning; assert the inline `*_llm_bedrock_region` / `*_llm_bedrock_reasoning` appear and a stale `*_llm_ollama_think` is cleared by always_overwrite of provider/api_key). If the existing tests already provide a fixture, reuse it.

- [ ] **Step 3: Implement.** Add to `field_map` (after the ollama_think line ~159):
```python
            "bedrock_region": f"{prefix}bedrock_region",
            "bedrock_reasoning": f"{prefix}bedrock_reasoning",
```
`always_overwrite` already contains `provider` + `api_key`; that is sufficient — region/reasoning are populated from the doc only when present, and the existing always-overwrite of provider/api_key prevents a stale provider from being kept. (Do NOT add region/reasoning to always_overwrite — they should not be force-cleared when a non-bedrock model is resolved; the field_map only sets them when the doc has them.)

- [ ] **Step 4: Run, verify pass.**
Run: `python3 -m pytest backend/tests/test_model_resolver_bedrock.py -v --ignore=backend/tests/test_intellistock_logger.py --ignore=backend/tests/test_redact_logger.py`

- [ ] **Step 5: Commit**
```bash
git add backend/model_resolver.py backend/tests/test_model_resolver_bedrock.py
git commit -m "$(cat <<'EOF'
feat(model_resolver): expand bedrock_region/bedrock_reasoning model refs

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase 4 — Nexus strategy allowlist

### Task 12: `graph_nexus_analysis.py` — the critical allowlist + role resolver

**THE highest-risk task.** Omitting `bedrock` from `_NEXUS_VALID_PROVIDERS` silently rewrites it to `gemini` (the exact bug that cost the most time in the ollama work).

**Files:**
- Modify: `backend/strategies/graph_nexus_analysis.py:624` (`_NEXUS_VALID_PROVIDERS`), `_default_model_for_provider` (~741), `_default_api_key_for_provider` (~766), `_resolve_role_llm_provider_config` (~793)
- Test: `backend/tests/test_nexus_bedrock_provider.py`

- [ ] **Step 1: Read** `backend/strategies/graph_nexus_analysis.py:620-960` to see the exact set literal, the default-functions' bodies, and the ollama branch in `_resolve_role_llm_provider_config` (mirror it for bedrock fields).

- [ ] **Step 2: Write failing test**:
```python
# backend/tests/test_nexus_bedrock_provider.py
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "strategies"))
import graph_nexus_analysis as gna

def test_bedrock_in_valid_providers():
    assert "bedrock" in gna._NEXUS_VALID_PROVIDERS

def test_normalize_keeps_bedrock():
    assert gna._normalize_llm_provider("bedrock") == "bedrock"
    assert gna._normalize_llm_provider("BEDROCK") == "bedrock"

def test_default_model_for_bedrock(monkeypatch):
    monkeypatch.delenv("GRAPH_NEXUS_BEDROCK_MODEL", raising=False)
    assert "claude" in gna._default_model_for_provider("bedrock").lower()
```

- [ ] **Step 3: Run, verify fail** (`bedrock` rewritten to `gemini`).
Run: `python3 -m pytest backend/tests/test_nexus_bedrock_provider.py -v --ignore=backend/tests/test_intellistock_logger.py --ignore=backend/tests/test_redact_logger.py`

- [ ] **Step 4: Implement.**
- 4a. Add `"bedrock",` to the `_NEXUS_VALID_PROVIDERS` set literal at line 624.
- 4b. In `_default_model_for_provider`, add (matching the existing per-provider style observed in Step 1):
```python
    if provider == "bedrock":
        return (os.environ.get("GRAPH_NEXUS_BEDROCK_MODEL")
                or "us.anthropic.claude-3-5-sonnet-20241022-v2:0").strip() or "us.anthropic.claude-3-5-sonnet-20241022-v2:0"
```
- 4c. In `_default_api_key_for_provider`, add:
```python
    if provider == "bedrock":
        return os.environ.get("BEDROCK_API_KEY", "").strip()
```
- 4d. In `_resolve_role_llm_provider_config`, mirror the ollama branch to add a bedrock branch returning the bedrock fields resolved through the config hierarchy (use the SAME hierarchy-lookup helpers the ollama branch uses, observed in Step 1):
```python
    if provider == "bedrock":
        return {
            "bedrock_region": _role_or_global(config, role, "bedrock_region"),
            "bedrock_reasoning": _role_or_global(config, role, "bedrock_reasoning"),
        }
```
> Replace `_role_or_global(...)` with the exact hierarchy accessor the ollama branch uses (e.g. the same `config.get(f"{role}_llm_bedrock_region") or config.get("bedrock_region")` shape). Match the observed ollama branch precisely.

- [ ] **Step 5: Run, verify pass.** → 3 passed.

- [ ] **Step 6: Commit**
```bash
git add backend/strategies/graph_nexus_analysis.py backend/tests/test_nexus_bedrock_provider.py
git commit -m "$(cat <<'EOF'
feat(nexus): add bedrock to valid providers + role resolver + defaults

Without bedrock in _NEXUS_VALID_PROVIDERS, _normalize_llm_provider silently
rewrites it to gemini. Adds defaults and the per-role config branch.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase 5 — API layer

### Task 13: `api/main.py` — Pydantic bodies + test endpoint config + /llm/test

**Files:**
- Modify: `backend/api/main.py` — `LlmConfigTestBody` (~461), `CreateModelBody` (~487), `EditModelBody` (~519), `_build_llm_test_provider_config` (~368), `api_test_llm_config` (~1374)
- Test: `backend/tests/test_api_bedrock_bodies.py` (validate the Pydantic models accept + bound the new fields)

- [ ] **Step 1: Read** `backend/api/main.py:360-540` and `1374-1523` to see exact field declarations + the `_build_llm_test_provider_config` shape + the ollama exemption/`smoke_thinking` in `/llm/test`.

- [ ] **Step 2: Write failing test**:
```python
# backend/tests/test_api_bedrock_bodies.py
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
import main as api_main

def test_create_model_body_accepts_bedrock_fields():
    b = api_main.CreateModelBody(name="bk", provider="bedrock", model="us.anthropic.claude-3-5-sonnet-20241022-v2:0",
                                 api_key="key", bedrock_region="us-east-1", bedrock_reasoning="medium")
    assert b.bedrock_region == "us-east-1"
    assert b.bedrock_reasoning == "medium"
```
(Adjust the constructor kwargs to the actual required fields of `CreateModelBody` observed in Step 1.)

- [ ] **Step 3: Implement.**
- 3a. Add to each of `LlmConfigTestBody`, `CreateModelBody`, `EditModelBody`:
```python
    bedrock_region: Optional[str] = Field(default=None, max_length=32)
    bedrock_reasoning: Optional[str] = Field(default=None, max_length=16)
```
- 3b. In `_build_llm_test_provider_config`, add (after the ollama block):
```python
    if provider == "bedrock":
        if body.bedrock_region:
            cfg["bedrock_region"] = body.bedrock_region.strip()
        if body.bedrock_reasoning:
            cfg["bedrock_reasoning"] = body.bedrock_reasoning.strip().lower()
```
(use the exact `cfg`/`body` variable names from Step 1.)
- 3c. In `api_test_llm_config`, the existing `if not api_key and provider not in ("ollama", ...)` guard — confirm bedrock is NOT exempt (bedrock requires a key). If the smoke path renders ollama reasoning via `smoke_thinking`, the bedrock plain path already returns reasoning text fallback so no special rendering is required; leave the generic smoke handling.

- [ ] **Step 4: Run, verify pass.**
Run: `python3 -m pytest backend/tests/test_api_bedrock_bodies.py -v --ignore=backend/tests/test_intellistock_logger.py --ignore=backend/tests/test_redact_logger.py`

- [ ] **Step 5: Commit**
```bash
git add backend/api/main.py backend/tests/test_api_bedrock_bodies.py
git commit -m "$(cat <<'EOF'
feat(api): bedrock fields in model/test bodies + test-config builder

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 14: `POST /bedrock/list-models` endpoint

**Files:**
- Modify: `backend/api/main.py` — add the route mirroring `api_ollama_list_models` (~1987); add a request body model.
- Test: extend `backend/tests/test_api_bedrock_bodies.py` or add an endpoint test (mock `bedrock_client.list_models`).

- [ ] **Step 1: Read** `backend/api/main.py:1987-2025` (the ollama list-models endpoint) for the exact decorator, auth dependency, and error-shape conventions.

- [ ] **Step 2: Write failing test** that calls the handler with a mocked `bedrock_client.list_models` returning two models and asserts the JSON shape `{"models": [...]}`; and a second asserting an auth error returns a typed error payload (not a 500), allowing the UI to fall back to manual entry.

- [ ] **Step 3: Implement** the route, mirroring `api_ollama_list_models` exactly (same auth dependency + try/except mapping `BedrockAuthError`/`BedrockConnectionError`/`BedrockProviderError` to a JSON `{"models": [], "error": "..."}` with the matching status the ollama endpoint uses). Body: `{api_key: str, region: str}`. Call `bedrock_client.list_models(api_key, region)`.

- [ ] **Step 4: Run, verify pass.**
Run: `python3 -m pytest backend/tests/test_api_bedrock_bodies.py -v --ignore=backend/tests/test_intellistock_logger.py --ignore=backend/tests/test_redact_logger.py`

- [ ] **Step 5: Commit**
```bash
git add backend/api/main.py backend/tests/test_api_bedrock_bodies.py
git commit -m "$(cat <<'EOF'
feat(api): POST /bedrock/list-models discovery endpoint

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 15: `interactive_utils.py` — persist bedrock fields

**Files:**
- Modify: `backend/interactive_utils.py` — `action_create_model` (~8025), `action_edit_model` (~8075)
- Test: mirror an existing model-action test if present; else a direct doc-shape assertion.

- [ ] **Step 1: Read** `backend/interactive_utils.py:8020-8175` to see the exact signature + doc-dict construction + the updateable-fields loop (the ollama fields are the template).

- [ ] **Step 2: Write failing test** mirroring the existing create/edit model test (mock `conn`); assert a created bedrock model doc contains `bedrock_region`/`bedrock_reasoning` and that edit updates them.

- [ ] **Step 3: Implement.**
- 3a. Add `bedrock_region=None, bedrock_reasoning=None` to `action_create_model`'s signature and into the doc dict: `"bedrock_region": (bedrock_region or "").strip(), "bedrock_reasoning": (bedrock_reasoning or "").strip().lower(),`
- 3b. Add `"bedrock_region"`, `"bedrock_reasoning"` to `action_edit_model`'s updateable-fields list (mirror how `ollama_think` is handled).
- 3c. **Trace the caller**: the API handler that calls `action_create_model`/`action_edit_model` (the POST/PUT model routes in api/main.py) must forward `body.bedrock_region`/`body.bedrock_reasoning`. Grep for `action_create_model(` and `action_edit_model(` in `backend/api/main.py` and add the two kwargs at each call site.

- [ ] **Step 4: Run, verify pass.**
Run: `python3 -m pytest backend/tests/ -k "model and (create or edit or bedrock)" -v --ignore=backend/tests/test_intellistock_logger.py --ignore=backend/tests/test_redact_logger.py`

- [ ] **Step 5: Commit**
```bash
git add backend/interactive_utils.py backend/api/main.py backend/tests/
git commit -m "$(cat <<'EOF'
feat(interactive_utils): persist bedrock_region/bedrock_reasoning on models

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase 6 — Frontend (no test runner; verify via `npm run build` + manual smoke)

> For every frontend task: first READ the ollama analogue in the same file, then add the parallel bedrock branch with the field names below. Keep camelCase: `bedrockRegion`, `bedrockReasoning`. The model field reuses the shared `model` binding; the key reuses the shared `apiKey` binding.

### Task 16: `strategyConfig.js` — provider option, reasoning options, payload

**Files:** Modify `frontend/src/utils/strategyConfig.js`

- [ ] **Step 1:** Add to `LLM_PROVIDER_OPTIONS` (line 309, after the ollama entry):
```js
  { value: 'bedrock', label: 'AWS Bedrock' },
```
- [ ] **Step 2:** Add a reasoning options constant near `OLLAMA_THINK_OPTIONS` (~line 332):
```js
export const BEDROCK_REASONING_OPTIONS = [
  { value: 'off', label: 'Off' },
  { value: 'low', label: 'Low' },
  { value: 'medium', label: 'Medium' },
  { value: 'high', label: 'High' },
]
```
- [ ] **Step 3:** In `buildStrategyLlmTestPayload` (read the ollama branch ~687 first), add a bedrock branch:
```js
  if (provider === 'bedrock') {
    payload.bedrock_region = (cfg.bedrockRegion || '').trim()
    payload.bedrock_reasoning = (cfg.bedrockReasoning || '').trim().toLowerCase()
  }
```
(match the exact `payload`/`cfg` variable names + the `.trim()` style of the ollama branch.)
- [ ] **Step 4:** Build check at end of Phase 6.

### Task 17: `useBedrockModels.js` composable

**Files:** Create `frontend/src/composables/useBedrockModels.js`

- [ ] **Step 1:** Read `frontend/src/composables/useOllamaModels.js` fully.
- [ ] **Step 2:** Create `useBedrockModels.js` mirroring it, with these differences: POST to `/bedrock/list-models` with `{ api_key, region }`; **NO** browser-direct fallback (Bedrock requires SigV4/CORS — backend only); on error, set `error` and leave `models` empty so the form falls back to a manual model-id text input. 30s TTL cache keyed by `region` (+ key presence). Expose `{ models, loading, error, loadBedrockModels }`.

### Task 18: `LlmConfigForm.vue` — bedrock form branch

**Files:** Modify `frontend/src/components/LlmConfigForm.vue`

- [ ] **Step 1:** Read the entire component, focusing on the ollama `<template v-if="draft.provider === 'ollama'">` branch + its script state (`onProviderChange`, effort/think state, model-picker wiring).
- [ ] **Step 2:** Add a `<template v-if="draft.provider === 'bedrock'">` branch with: **Region** (text input + `<datalist>` of common regions: us-east-1, us-west-2, eu-central-1, eu-west-1, ap-southeast-2, ap-northeast-1; bind `draft.bedrockRegion`, default us-east-1), **API key** (bind shared `draft.apiKey`; helper text "Bedrock API key (bearer token)"), **Model** (searchable picker fed by `useBedrockModels`; on load error or empty, show a plain text input bound to `draft.model` so a narrowly-scoped key still works), **Reasoning** (`<select>` from `BEDROCK_REASONING_OPTIONS` bound to `draft.bedrockReasoning`; helper text "Requires Claude 3.7+; ignored by other models").
- [ ] **Step 3:** In `onProviderChange`, mirror the ollama handling: when switching to bedrock seed `bedrockRegion='us-east-1'`/`bedrockReasoning='off'`; when switching away clear bedrock-only fields. Include bedrock in the effort-label computed if the form shows one.

### Task 19: `ModelsView.vue` — CRUD form draft + reasoning cell

**Files:** Modify `frontend/src/views/ModelsView.vue`

- [ ] **Step 1:** Read the ollama handling (formDraft init, hydration ~169, `_reasoningCell` ~50-72, `submitModel` payload ~265-317).
- [ ] **Step 2:** Add to `formDraft`: `bedrockRegion: 'us-east-1', bedrockReasoning: 'off'`. Hydrate from stored model: `bedrockRegion: m.bedrock_region || 'us-east-1', bedrockReasoning: m.bedrock_reasoning || 'off'`. In `_reasoningCell`, add: `if (m.provider === 'bedrock') return String(m.bedrock_reasoning || '').trim().toUpperCase() || 'OFF'`. In `submitModel`, add to the payload when `provider==='bedrock'`: `payload.bedrock_region = ...; payload.bedrock_reasoning = ...` (match the snake_case keys the API body expects).

### Task 20: `InstanceDetailView.vue` + `InstancesView.vue` — picker hydration

**Files:** Modify `frontend/src/views/InstanceDetailView.vue`, `frontend/src/views/InstancesView.vue`

- [ ] **Step 1:** Read each view's LLM-picker `effortLabel` + the ollama field hydration block.
- [ ] **Step 2:** In each, add bedrock field hydration (`bedrockRegion: m.bedrock_region || ''`, `bedrockReasoning: m.bedrock_reasoning || ''`) and an `effortLabel` bedrock branch returning the reasoning effort upper-cased (or 'OFF').

### Task 21: Frontend build verification

- [ ] **Step 1:** Run: `cd frontend && npm run build`
Expected: clean build, no errors.
- [ ] **Step 2: Commit the whole frontend phase**
```bash
git add frontend/src
git commit -m "$(cat <<'EOF'
feat(frontend): AWS Bedrock provider — form, discovery composable, model CRUD, pickers

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase 7 — Integration & verification

### Task 22: Opt-in live integration test

**Files:** Create `backend/tests/test_bedrock_live.py`

- [ ] **Step 1:** Mirror the ollama live test: gate the whole module on `os.environ.get("RUN_BEDROCK_LIVE") == "1"` via `pytestmark = pytest.mark.skipif(...)`. Read `BEDROCK_API_KEY`, `BEDROCK_REGION`, and a `BEDROCK_TEST_MODEL` (default `us.anthropic.claude-3-5-sonnet-20241022-v2:0`).
- [ ] **Step 2:** Three tests: plain `call_llm_by_provider("bedrock", ...)` returns non-empty; structured `call_structured_llm_by_provider` returns a validated object; `call_bedrock_with_tools` returns a tool_call for a forced-tool prompt. Each skipped unless the env gate is set.
- [ ] **Step 3: Commit**
```bash
git add backend/tests/test_bedrock_live.py
git commit -m "$(cat <<'EOF'
test(bedrock): opt-in live integration tests (RUN_BEDROCK_LIVE=1)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Task 23: Full verification

- [ ] **Step 1:** Backend:
Run: `python3 -m pytest backend/tests/ -k bedrock --ignore=backend/tests/test_intellistock_logger.py --ignore=backend/tests/test_redact_logger.py -v`
Expected: all bedrock tests pass; live tests skipped.
- [ ] **Step 2:** Regression — run the existing provider/guard/resolver suites:
Run: `python3 -m pytest backend/tests/ -k "ollama or critical or resolver or llm" --ignore=backend/tests/test_intellistock_logger.py --ignore=backend/tests/test_redact_logger.py -q`
Expected: no new failures vs baseline.
- [ ] **Step 3:** Frontend: `cd frontend && npm run build` clean.

---

## Self-Review (run after writing — completed inline)

- **Spec coverage:** §2 auth → Tasks 1,2,5,8; §3 architecture → Tasks 6,7,8; §4.1 client → Task 2; §4.2 llm_utils → Tasks 3-9; §4.3 nexus → Task 12; §4.4 critical-guard → Task 10; §4.5 resolver → Task 11; §4.6 api bodies/test/endpoint → Tasks 13,14; §4.7 interactive_utils → Task 15; §4.8 frontend → Tasks 16-21; §9 testing → all + Tasks 22,23; §7 error handling → Tasks 6,10,14. All covered.
- **Placeholder scan:** Frontend tasks (16-21) and a few backend tasks (11,12,13,14,15) carry a "read the ollama analogue first" instruction rather than full reproduced code, because the exact surrounding code wasn't read at planning time. Each names the precise file, the symbol, the exact new field names/keys, and the snippet to add — concrete, not "TBD". The novel backend code (client, _call_bedrock, tools, reasoning, structured branch, dispatch, critical-guard) is fully reproduced.
- **Type consistency:** field names `bedrock_region`/`bedrock_reasoning` (snake) ↔ `bedrockRegion`/`bedrockReasoning` (camel) used consistently; `_normalize_bedrock_reasoning(value, model)` signature consistent across Tasks 4/6/7/8; `bedrock_client.build_runtime_client(api_key, region)` / `build_control_client` / `list_models(api_key, region)` consistent across Tasks 2/6/7/8/14.
```
