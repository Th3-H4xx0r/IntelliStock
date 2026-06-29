"""Multi-provider LLM call with tool use, for the chatbot.

This module wraps the OpenAI Chat Completions JSON shape (which OpenAI,
Azure OpenAI, DeepSeek, and NVIDIA NIM all speak) and Gemini's REST
``generateContent`` shape so the chatbot can run a single tool-calling
loop regardless of which provider the user picked.

The function ``call_chat_with_tools`` returns a normalised result:

    {
      "content": str | None,         # assistant text (may be empty when tools called)
      "tool_calls": [
        {"id": str, "name": str, "arguments": dict}
      ],
      "finish_reason": str,
      "raw": <provider-specific payload>,
    }

It is *not* streaming — the orchestration layer uses non-streaming
round-trips so each tool call can be safely paused for user confirmation.
"""
from __future__ import annotations

import json
import os
import re
import sys
import uuid
from typing import Any, Dict, List, Optional, Tuple

import requests


# Trim noisy provider-error bodies and strip URLs / api-key-shaped tokens
# before they bubble up into a RuntimeError that the orchestrator persists
# as user-visible chatbot content.
_URL_RE = re.compile(r"https?://[^\s'\"<>]+", re.IGNORECASE)
_API_KEY_LIKE = re.compile(r"\b(?:sk|nvapi|pk|gsk|gho)[_-][A-Za-z0-9]{12,}\b")


def _safe_provider_error(provider: str, status_code: int, body_text: str) -> str:
    snippet = (body_text or "")[:300]
    snippet = _URL_RE.sub("[redacted-url]", snippet)
    snippet = _API_KEY_LIKE.sub("[redacted-key]", snippet)
    return f"{provider} API {status_code}: {snippet}"


# ── Public message normalization ────────────────────────────────────────────


def _coerce_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Strip chatbot-specific fields and produce OpenAI-compatible messages."""
    out: List[Dict[str, Any]] = []
    for m in messages:
        role = m.get("role")
        if role not in ("system", "user", "assistant", "tool"):
            continue
        if role == "tool":
            out.append({
                "role": "tool",
                "tool_call_id": m.get("tool_call_id") or "",
                "name": m.get("name") or "",
                "content": _as_text(m.get("content")),
            })
            continue
        if role == "assistant" and m.get("tool_calls"):
            out.append({
                "role": "assistant",
                "content": _as_text(m.get("content")) or None,
                "tool_calls": [
                    {
                        "id": tc.get("id"),
                        "type": "function",
                        "function": {
                            "name": tc.get("name"),
                            "arguments": json.dumps(tc.get("arguments") or {}),
                        },
                    }
                    for tc in m["tool_calls"]
                ],
            })
            continue
        out.append({"role": role, "content": _as_text(m.get("content"))})
    return out


def _as_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    try:
        return json.dumps(content, ensure_ascii=False)
    except Exception:
        return str(content)


# ── OpenAI-compatible providers ─────────────────────────────────────────────


def _openai_compat_url(provider: str, base_url: Optional[str], azure_endpoint: Optional[str], azure_api_version: Optional[str], model: str) -> Tuple[str, Dict[str, str]]:
    """Resolve the chat completions endpoint URL + headers for the given provider."""
    p = (provider or "").strip().lower()
    if p == "azure":
        endpoint = (azure_endpoint or "").strip().rstrip("/")
        if not endpoint:
            raise ValueError("Azure endpoint is required for the azure provider")
        # Azure's v1 API doesn't accept a query-param api-version (returns
        # "API version not supported"). Mirror the pattern that already works
        # for the rest of the project — `_call_azure_openai` in llm_utils.py.
        url = f"{endpoint}/openai/v1/chat/completions"
        return url, {"Content-Type": "application/json"}
    if p == "openai":
        base = (base_url or "https://api.openai.com").strip().rstrip("/")
        if not base.endswith("/v1"):
            base = base + "/v1"
        return f"{base}/chat/completions", {"Content-Type": "application/json"}
    if p == "deepseek":
        return "https://api.deepseek.com/v1/chat/completions", {"Content-Type": "application/json"}
    if p == "nvidia":
        base = (base_url or "https://integrate.api.nvidia.com/v1").strip().rstrip("/")
        if not base.endswith("/v1"):
            base = base + "/v1"
        return f"{base}/chat/completions", {"Content-Type": "application/json"}
    if p == "openrouter":
        base = (base_url or "https://openrouter.ai/api/v1").strip().rstrip("/")
        return f"{base}/chat/completions", {"Content-Type": "application/json"}
    raise ValueError(f"unsupported provider for openai-compat: {provider!r}")


def _call_openai_compat(
    *,
    provider: str,
    api_key: str,
    model: str,
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]],
    base_url: Optional[str],
    azure_endpoint: Optional[str],
    azure_api_version: Optional[str],
    reasoning_effort: Optional[str],
    timeout_sec: int,
    max_output_tokens: int,
) -> Dict[str, Any]:
    url, headers = _openai_compat_url(provider, base_url, azure_endpoint, azure_api_version, model)
    auth_headers = dict(headers)
    # Azure's v1 API uses Bearer auth, same as the rest of the project's
    # working _call_azure_openai. The legacy api-key header path is only
    # for the older /openai/deployments/{name}/... URL shape.
    auth_headers["Authorization"] = f"Bearer {api_key}"
    p = (provider or "").lower()

    # reasoning_effort is only documented for OpenAI's o-series and Azure
    # OpenAI deployments. DeepSeek (auto-reasons) and NVIDIA NIM use other
    # control surfaces, so passing it through there can 400.
    eff = ""
    if p in ("openai", "azure", "openrouter") and reasoning_effort:
        eff_candidate = str(reasoning_effort).strip().lower()
        if eff_candidate in ("low", "medium", "high"):
            eff = eff_candidate

    payload: Dict[str, Any] = {
        "model": model,
        "messages": _coerce_messages(messages),
    }
    # Azure's v1 API always wants max_completion_tokens (max_tokens is
    # deprecated and the gpt-5 / o-series families reject it). OpenAI's
    # reasoning models also require max_completion_tokens. Other OpenAI-
    # compat backends (DeepSeek / NVIDIA) still expect max_tokens.
    if p == "azure" or (p == "openai" and eff):
        payload["max_completion_tokens"] = int(max_output_tokens)
    else:
        payload["max_tokens"] = int(max_output_tokens)
    if eff:
        payload["reasoning_effort"] = eff
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    resp = requests.post(url, headers=auth_headers, json=payload, timeout=timeout_sec)
    if resp.status_code >= 400:
        raise RuntimeError(_safe_provider_error(provider, resp.status_code, resp.text))
    data = resp.json()
    choice = (data.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    raw_calls = msg.get("tool_calls") or []
    tool_calls: List[Dict[str, Any]] = []
    for tc in raw_calls:
        fn = tc.get("function") or {}
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except Exception:
            args = {}
        tool_calls.append({
            "id": tc.get("id") or str(uuid.uuid4()),
            "name": fn.get("name") or "",
            "arguments": args if isinstance(args, dict) else {},
        })
    return {
        "content": msg.get("content") or "",
        "tool_calls": tool_calls,
        "finish_reason": choice.get("finish_reason") or "",
        "raw": data,
    }


# ── Gemini ──────────────────────────────────────────────────────────────────


GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"


def _gemini_messages(messages: List[Dict[str, Any]]) -> Tuple[Optional[str], List[Dict[str, Any]]]:
    """Convert chat-style messages to Gemini's `contents` array.
    Returns (system_instruction, contents).
    """
    sys_parts: List[str] = []
    contents: List[Dict[str, Any]] = []
    for m in messages:
        role = m.get("role")
        if role == "system":
            sys_parts.append(_as_text(m.get("content")))
            continue
        if role == "tool":
            contents.append({
                "role": "user",
                "parts": [{
                    "functionResponse": {
                        "name": m.get("name") or "",
                        "response": {"result": _as_text(m.get("content"))},
                    },
                }],
            })
            continue
        if role == "assistant":
            parts: List[Dict[str, Any]] = []
            content = _as_text(m.get("content"))
            if content:
                parts.append({"text": content})
            for tc in (m.get("tool_calls") or []):
                parts.append({"functionCall": {"name": tc.get("name") or "", "args": tc.get("arguments") or {}}})
            # Skip entirely when there's nothing to send — Gemini rejects
            # contents with empty parts arrays.
            if parts:
                contents.append({"role": "model", "parts": parts})
            continue
        if role == "user":
            contents.append({"role": "user", "parts": [{"text": _as_text(m.get("content"))}]})
    sys = "\n\n".join([p for p in sys_parts if p]) or None
    return sys, contents


def _call_gemini(
    *,
    api_key: str,
    model: str,
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]],
    timeout_sec: int,
    max_output_tokens: int,
) -> Dict[str, Any]:
    sys_instr, contents = _gemini_messages(messages)
    body: Dict[str, Any] = {
        "contents": contents,
        "generationConfig": {"maxOutputTokens": int(max_output_tokens)},
    }
    if sys_instr:
        body["systemInstruction"] = {"parts": [{"text": sys_instr}]}
    if tools:
        # tools came in as OpenAI shape; convert to Gemini function declarations
        decls: List[Dict[str, Any]] = []
        for t in tools:
            fn = t.get("function") or {}
            decls.append({
                "name": fn.get("name"),
                "description": fn.get("description"),
                "parameters": fn.get("parameters") or {"type": "object"},
            })
        body["tools"] = [{"functionDeclarations": decls}]

    url = f"{GEMINI_BASE}/models/{model}:generateContent"
    headers = {
        "Content-Type": "application/json",
        # Use header auth instead of ?key=… so the API key never lands in
        # access logs or proxy traces.
        "x-goog-api-key": api_key,
    }
    resp = requests.post(url, headers=headers, json=body, timeout=timeout_sec)
    if resp.status_code >= 400:
        raise RuntimeError(_safe_provider_error("gemini", resp.status_code, resp.text))
    data = resp.json()
    cand = (data.get("candidates") or [{}])[0]
    parts = (cand.get("content") or {}).get("parts") or []
    text_out: List[str] = []
    tool_calls: List[Dict[str, Any]] = []
    for part in parts:
        if "text" in part and part["text"]:
            text_out.append(part["text"])
        if "functionCall" in part:
            fc = part["functionCall"] or {}
            tool_calls.append({
                "id": str(uuid.uuid4()),
                "name": fc.get("name") or "",
                "arguments": fc.get("args") or {},
            })
    return {
        "content": "".join(text_out),
        "tool_calls": tool_calls,
        "finish_reason": cand.get("finishReason") or "",
        "raw": data,
    }


# ── Public entry point ──────────────────────────────────────────────────────


def call_chat_with_tools(
    *,
    provider: str,
    api_key: str,
    model: str,
    messages: List[Dict[str, Any]],
    tools_openai: Optional[List[Dict[str, Any]]] = None,
    base_url: Optional[str] = None,
    azure_endpoint: Optional[str] = None,
    azure_api_version: Optional[str] = None,
    reasoning_effort: Optional[str] = None,
    timeout_sec: int = 90,
    max_output_tokens: int = 1024,
) -> Dict[str, Any]:
    """Provider-agnostic chat completion with optional tool calling."""
    p = (provider or "gemini").strip().lower()
    if not api_key:
        raise ValueError("api_key required")
    if not model:
        raise ValueError("model required")
    try:
        if p in ("openai", "azure", "deepseek", "nvidia", "openrouter"):
            return _call_openai_compat(
                provider=p,
                api_key=api_key,
                model=model,
                messages=messages,
                tools=tools_openai,
                base_url=base_url,
                azure_endpoint=azure_endpoint,
                azure_api_version=azure_api_version,
                reasoning_effort=reasoning_effort,
                timeout_sec=timeout_sec,
                max_output_tokens=max_output_tokens,
            )
        return _call_gemini(
            api_key=api_key,
            model=model,
            messages=messages,
            tools=tools_openai,
            timeout_sec=timeout_sec,
            max_output_tokens=max_output_tokens,
        )
    except requests.exceptions.Timeout as e:
        raise RuntimeError(f"{p} request timed out after {timeout_sec}s") from e
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"{p} request failed: {e}") from e


__all__ = ["call_chat_with_tools"]
