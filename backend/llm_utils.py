"""
Shared LLM utilities for IntelliStock strategies.
Provides a single entry point (call_llm_by_provider) for Gemini, DeepSeek, OpenAI, and Azure OpenAI.
Prompt-hash caching: call_llm_with_prompt_cache for reuse of raw output by (model, prompt hash).
Used by: earnings, graph_nexus_analysis, ai_trading_decision, ml_news.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import re
import sys
import time
import threading
from collections import deque
from html import unescape
from typing import Any, Sequence
from datetime import datetime
from urllib.parse import urlsplit

try:
    from pydantic import TypeAdapter
except Exception:
    TypeAdapter = None

try:
    from json_repair import repair_json as _repair_json_with_library
except Exception:
    _repair_json_with_library = None

try:
    from rethinkdb import RethinkDB
    _rethink = RethinkDB()
except Exception:
    _rethink = None

try:
    from pydantic_ai import Agent
    from pydantic_ai.models.google import GoogleModel, GoogleModelSettings
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.profiles import ModelProfile
    from pydantic_ai.providers.azure import AzureProvider
    from pydantic_ai.providers.deepseek import DeepSeekProvider
    from pydantic_ai.providers.google import GoogleProvider
    from pydantic_ai.providers.openai import OpenAIProvider
    from pydantic_ai.settings import ModelSettings
    _PYDANTIC_AI_AVAILABLE = True
except Exception:
    Agent = None
    GoogleModel = None
    GoogleModelSettings = None
    OpenAIChatModel = None
    ModelProfile = None
    AzureProvider = None
    DeepSeekProvider = None
    GoogleProvider = None
    OpenAIProvider = None
    ModelSettings = None
    _PYDANTIC_AI_AVAILABLE = False

# Telemetry — defensive import so a missing/broken module never blocks LLM calls.
try:
    from llm_telemetry import record_llm_call as _telemetry_record
except Exception:
    def _telemetry_record(**_kwargs):
        return None


def _safe_record(**kwargs) -> None:
    """Best-effort telemetry. Never raises out."""
    try:
        _telemetry_record(**kwargs)
    except Exception:
        pass


# Patterns for responses that MUST NOT be retried.
#
# Retrying these patterns triggers Azure's abuse monitor (interprets a repeat
# of a blocked prompt as a deliberate bypass attempt) and gets the entire
# resource temp-blocked for 24-48h. Two real backtests have already died
# this way (bt357345 with 1008 of these errors, bt437583 exited mid-lookback).
#
# These are matched against the response body / exception text in the
# inner HTTP retry loops AND in the outer raw-JSON retry loop so the
# abuse monitor sees ONE attempt per blocked prompt and moves on.
_RX_CONTENT_FILTER = re.compile(r"content_filter|content filter", re.I)
_RX_AZURE_TEMP_BLOCK = re.compile(r"temporarily blocked|unusual behavior", re.I)


def _is_non_retryable_filter_response(
    *,
    status: int | None = None,
    body: str | None = None,
    exc: BaseException | None = None,
) -> tuple[bool, str]:
    """Return ``(is_non_retryable, reason_tag)`` for responses that MUST NOT
    be retried at the inner HTTP / raw-JSON layer.

    Retrying these patterns is exactly what triggers Azure's abuse-monitor
    temp-block. The outer ``_call_structured_llm_with_critical_guard``
    wrapper still classifies the response (``azure_403_blocked`` /
    ``content_filter``) and handles pause/abort — this guard is only
    here to make sure inner loops don't re-fire the same blocked prompt.

    Args:
        status: HTTP status code (if known). Pass ``None`` if only an
            exception is available.
        body: Response body text / error message (if available).
        exc: Exception to inspect when ``status`` / ``body`` are missing
            (e.g. ``requests.HTTPError`` carrying a ``response`` attr).

    Returns:
        ``(True, tag)`` if the caller MUST stop retrying.
        ``(False, "")`` otherwise (safe to retry under normal backoff rules).
    """
    if status is None and exc is not None:
        # Try to pull status + body off the exception (requests pattern).
        _resp = getattr(exc, "response", None)
        if _resp is not None:
            try:
                status = getattr(_resp, "status_code", None)
            except Exception:
                status = None
            if body is None:
                try:
                    body = getattr(_resp, "text", "") or ""
                except Exception:
                    body = ""
        if body is None:
            body = str(exc)

    body_l = (body or "").lower() if body else ""

    # 1. Content filter — HTTP 400 with "content_filter" in body. Azure's
    #    content filter and OpenAI's both surface this signature. Even if
    #    the status is missing or wrong (sometimes wrapped inside a
    #    RuntimeError(f"HTTP 400: ...") string), match on body alone.
    if status == 400 and _RX_CONTENT_FILTER.search(body_l):
        return True, "content_filter"
    if _RX_CONTENT_FILTER.search(body_l):
        return True, "content_filter"

    # 2. Azure abuse-monitor temp-block — HTTP 403 with "temporarily blocked"
    #    or "unusual behavior" in body. The resource is already temp-blocked;
    #    retrying just extends the block window. Same fall-through logic for
    #    status: prefer status+body, fall back to body-only.
    if status == 403 and _RX_AZURE_TEMP_BLOCK.search(body_l):
        return True, "azure_temp_blocked"
    if _RX_AZURE_TEMP_BLOCK.search(body_l):
        return True, "azure_temp_blocked"

    return False, ""


# Gemini REST
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
_STRUCTURED_LLM_PROVIDER_LOCKS: dict[str, threading.Lock] = {
    "gemini": threading.Lock(),
}
_LAST_STRUCTURED_LLM_CALL = threading.local()
_LAST_PLAIN_LLM_CALL_ERROR = threading.local()  # stores last error from _call_openai/_call_azure_openai
_TERMINAL_LLM_FAILURES: dict[str, str] = {}
_TERMINAL_LLM_FAILURES_LOCK = threading.Lock()


# Models known to return empty `{}` (2-token "skeleton" response) when
# constrained by `response_format={"type": "json_object"}`. We've seen
# this with gpt-oss, gpt-5*, and NVIDIA NIM kimi-k2.x — all of which
# include their own prompt-driven JSON output and reject the constraint.
# Used by every chat-completion call path to decide whether to set the
# response_format field. Keeping the list in one place so adding a new
# quirky model only touches one site.
_JSON_OBJECT_FORMAT_QUIRKY_MARKERS = (
    "gpt-oss",
    "gpt_oss",
    "gpt oss",
    "gpt-5",
    "gpt_5",
    "moonshotai/kimi",
    "kimi-k2",
    "kimi_k2",
)


def _model_skips_json_object_format(model_name: str) -> bool:
    """Return True for chat-completion models that return empty `{}` when
    constrained by response_format=json_object. Caller should omit the
    response_format field and rely on the prompt for JSON shape."""
    lowered = str(model_name or "").strip().lower()
    return any(marker in lowered for marker in _JSON_OBJECT_FORMAT_QUIRKY_MARKERS)


def resolve_api_key_for_provider(provider: str, explicit_api_key: str | None = None) -> str:
    """Resolve provider API key from explicit value first, then provider-specific env vars.

    For local-CLI providers (claude-cli, codex-cli) that authenticate via
    the operator's subscription (no API key needed), return a sentinel
    non-empty string so the ``if not api_key`` short-circuit in callers
    doesn't skip the pipeline.
    """
    explicit = str(explicit_api_key or "").strip()
    if explicit:
        return explicit
    p = (provider or "gemini").strip().lower()
    if p == "azure":
        return str(os.environ.get("AZURE_OPENAI_API_KEY") or "").strip()
    if p == "openai":
        return str(os.environ.get("OPENAI_API_KEY") or "").strip()
    if p == "deepseek":
        return str(os.environ.get("DEEPSEEK_API_KEY") or "").strip()
    if p == "nvidia":
        return str(os.environ.get("NVIDIA_API_KEY") or "").strip()
    if p == "claude-cli":
        return "claude-cli-no-api-key"
    if p == "codex-cli":
        return "codex-cli-no-api-key"
    if p == "ollama":
        # Empty string is valid for local Ollama (no auth) — only Ollama
        # Cloud requires a Bearer token. Caller decides whether to enforce
        # non-empty (e.g., when the host suffix is ``ollama.com``).
        return str(os.environ.get("OLLAMA_API_KEY") or "").strip()
    return str(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or "").strip()


def _structured_run_usage_dict(result) -> dict[str, int]:
    """Best-effort extraction of token usage from a PydanticAI run result."""
    try:
        usage = result.usage()
    except Exception:
        usage = None
    if usage is None:
        return {}
    out: dict[str, int] = {}
    for field in (
        "requests",
        "tool_calls",
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
        "input_audio_tokens",
        "cache_audio_read_tokens",
    ):
        try:
            value = getattr(usage, field, None)
        except Exception:
            value = None
        if value is None:
            continue
        try:
            out[field] = int(value)
        except Exception:
            continue
    try:
        details = getattr(usage, "details", None)
        if isinstance(details, dict):
            for key, value in details.items():
                if value is None:
                    continue
                try:
                    out[f"detail_{key}"] = int(value)
                except Exception:
                    continue
    except Exception:
        pass
    return out


def _coerce_timeout_sec(timeout_sec: int | None) -> int:
    """Resolve timeout (seconds) from arg/env with a safe fallback."""
    if timeout_sec is None:
        timeout_sec = os.environ.get("LLM_REQUEST_TIMEOUT", "180")  # seconds
    try:
        t = int(timeout_sec)
    except Exception:
        t = 180
    return t if t > 0 else 180


def _backoff_sleep_seconds(attempt: int, base: float = 2.0, cap: float = 60.0) -> float:
    """Exponential backoff with jitter for retry attempt index starting at 0."""
    exp = min(cap, base * (2 ** max(0, attempt)))
    jitter = random.uniform(0, min(1.0, exp * 0.25))
    return exp + jitter


def _retry_after_seconds(headers: dict | None) -> int:
    """Parse HTTP Retry-After header (seconds). Returns 0 if not present/parseable."""
    if not headers:
        return 0
    ra = headers.get("Retry-After")
    if not ra:
        return 0
    try:
        return max(0, int(str(ra).strip()))
    except Exception:
        return 0


class _TokenRateLimiter:
    """Sliding-window token-per-minute rate limiter. Thread-safe."""

    def __init__(self, tokens_per_minute: int):
        self._limit = int(tokens_per_minute)
        self._window: deque = deque()  # (monotonic_time, tokens) pairs
        self._lock = threading.Lock()

    def acquire(self, estimated_tokens: int) -> float:
        """Block until there is token budget, then reserve estimated_tokens. Returns seconds waited."""
        estimated_tokens = max(1, int(estimated_tokens or 1))
        waited = 0.0
        while True:
            with self._lock:
                now = time.monotonic()
                cutoff = now - 60.0
                while self._window and self._window[0][0] <= cutoff:
                    self._window.popleft()
                used = sum(t for _, t in self._window)
                if used + estimated_tokens <= self._limit:
                    self._window.append((now, estimated_tokens))
                    return waited
                sleep_sec = max(0.5, 60.0 - (now - self._window[0][0]) + 0.1) if self._window else 1.0
            import sys
            print(
                f"[llm_utils] Rate limiter: {used}/{self._limit} tokens used in last 60s — waiting {sleep_sec:.1f}s...",
                file=sys.stderr,
                flush=True,
            )
            time.sleep(sleep_sec)
            waited += sleep_sec


# Per-model rate limiters — add entries here to throttle specific models.
# Key: lowercase model name as passed to the API (must match exactly).
_MODEL_RATE_LIMITERS: dict[str, _TokenRateLimiter] = {
    "kimi-k2.5": _TokenRateLimiter(100_000),
}


def _get_model_rate_limiter(model: str) -> _TokenRateLimiter | None:
    return _MODEL_RATE_LIMITERS.get((model or "").strip().lower())


class _RequestRateLimiter:
    """Sliding-window REQUEST-per-minute rate limiter with a minimum
    inter-request gap AND a circuit breaker. Thread-safe.

    Three complementary guards:
      * Sliding 60s window caps total RPM (`requests_per_minute`).
      * `min_interval_sec` enforces a gap between consecutive requests
        so bursts don't blow past providers' sub-minute hidden limits.
      * Circuit breaker: after N consecutive 429s (observed via the
        external `note_rate_limited()` hook) the limiter blocks all
        requests for `breaker_cooldown_sec`. This protects against
        providers whose rate-limit state persists across our process
        lifetime — once we've been put in NVIDIA's penalty box we
        stop hammering, wait, and let the window clear.
    """

    def __init__(
        self,
        requests_per_minute: int,
        min_interval_sec: float | None = None,
        *,
        breaker_threshold: int = 3,
        breaker_cooldown_sec: float = 120.0,
    ):
        self._limit = int(requests_per_minute)
        # Default: spread requests evenly across the window.
        if min_interval_sec is None and self._limit > 0:
            min_interval_sec = 60.0 / float(self._limit)
        self._min_interval = float(min_interval_sec or 0.0)
        self._window: deque = deque()  # monotonic timestamps
        self._lock = threading.Lock()
        # Circuit breaker state.
        self._breaker_threshold = max(1, int(breaker_threshold))
        self._breaker_cooldown = float(breaker_cooldown_sec)
        self._consecutive_429s = 0
        self._breaker_open_until = 0.0  # monotonic; >now means tripped

    def note_success(self) -> None:
        """Tell the limiter the most recent request succeeded. Resets
        the consecutive-429 counter; does NOT touch the cooldown timer
        (a tripped breaker still waits out its full cooldown)."""
        with self._lock:
            self._consecutive_429s = 0

    def note_rate_limited(self) -> None:
        """Tell the limiter the most recent request 429'd. After
        breaker_threshold consecutive 429s, open the breaker for
        breaker_cooldown_sec — subsequent acquire() calls will block
        until the cooldown elapses."""
        with self._lock:
            self._consecutive_429s += 1
            if self._consecutive_429s >= self._breaker_threshold:
                self._breaker_open_until = time.monotonic() + self._breaker_cooldown
                import sys
                print(
                    f"[llm_utils] RPM limiter: circuit breaker OPEN — "
                    f"{self._consecutive_429s} consecutive 429s, pausing "
                    f"requests for {self._breaker_cooldown:.0f}s",
                    file=sys.stderr, flush=True,
                )

    def acquire(self) -> float:
        """Block until there is request budget AND the min-gap has
        elapsed AND the circuit breaker (if any) is closed, then
        reserve one slot. Returns total seconds waited."""
        waited = 0.0
        while True:
            with self._lock:
                now = time.monotonic()
                # Circuit-breaker check FIRST. When tripped, every
                # acquire waits out the full cooldown before any
                # other budget logic runs.
                breaker_wait = 0.0
                if self._breaker_open_until > now:
                    breaker_wait = self._breaker_open_until - now
                cutoff = now - 60.0
                while self._window and self._window[0] <= cutoff:
                    self._window.popleft()
                used = len(self._window)
                # Enforce inter-request gap based on the most-recent slot.
                gap_wait = 0.0
                if self._window and self._min_interval > 0:
                    elapsed = now - self._window[-1]
                    if elapsed < self._min_interval:
                        gap_wait = self._min_interval - elapsed
                if breaker_wait <= 0 and used < self._limit and gap_wait <= 0:
                    self._window.append(now)
                    return waited
                if breaker_wait > 0:
                    sleep_sec = max(0.5, breaker_wait + 0.1)
                elif used >= self._limit:
                    sleep_sec = (
                        max(0.5, 60.0 - (now - self._window[0]) + 0.5)
                        if self._window else 1.0
                    )
                else:
                    sleep_sec = max(0.5, gap_wait + 0.05)
            import sys
            print(
                f"[llm_utils] RPM limiter: {used}/{self._limit} req in last 60s — "
                f"waiting {sleep_sec:.1f}s...",
                file=sys.stderr,
                flush=True,
            )
            time.sleep(sleep_sec)
            waited += sleep_sec


# Per-(provider, model) REQUEST-per-minute caps. Keyed by provider so a
# kimi-k2 model name through a non-NVIDIA proxy (Azure, OpenAI gateway,
# etc.) doesn't get throttled — only NVIDIA NIM has the rate-limit
# pathology that motivated this limiter.
#
# NVIDIA NIM kimi-k2.6 PUBLISHES 40 RPM but in production 429s appear
# at ~10 RPM, and rate-limit state carries across the published 60s
# window (bursts hours earlier still trigger 429 on the very first
# request of a new backtest). Capping at 5 RPM with a min 12s
# inter-request gap spreads calls evenly so we never trigger NIM's
# hidden sub-minute burst limit.
_PROVIDER_MODEL_REQUEST_RATE_LIMITERS: dict[tuple[str, str], _RequestRateLimiter] = {
    ("nvidia", "moonshotai/kimi-k2.6"): _RequestRateLimiter(5, min_interval_sec=12.0),
    ("nvidia", "moonshotai/kimi-k2.5"): _RequestRateLimiter(5, min_interval_sec=12.0),
    ("nvidia", "moonshotai/kimi-k2"): _RequestRateLimiter(5, min_interval_sec=12.0),
}


def _get_model_request_rate_limiter(model: str, provider: str = "nvidia") -> _RequestRateLimiter | None:
    """Return the RPM limiter for a (provider, model) pair, or None if
    no cap is configured. Defaults to provider='nvidia' so existing
    call sites inside _call_nvidia (which is NVIDIA-only) keep working
    without a signature change."""
    p = (provider or "").strip().lower()
    if not p:
        return None
    key = (p, (model or "").strip().lower())
    if key in _PROVIDER_MODEL_REQUEST_RATE_LIMITERS:
        return _PROVIDER_MODEL_REQUEST_RATE_LIMITERS[key]
    # Best-effort prefix match for `moonshotai/kimi-*` variants we
    # haven't enumerated explicitly (e.g. moonshotai/kimi-k2.7), but
    # only when the provider matches. Other providers exposing a
    # kimi-shaped model name go unthrottled.
    if p == "nvidia" and key[1].startswith("moonshotai/kimi"):
        return _PROVIDER_MODEL_REQUEST_RATE_LIMITERS.get(("nvidia", "moonshotai/kimi-k2"))
    return None


def _normalize_tools_to_openai_shape(tools) -> list[dict]:
    """Convert either Gemini- or OpenAI-shaped tool dicts into OpenAI shape.

    Gemini shape (used by ``call_gemini_with_tools``):
        ``[{"function_declarations": [{"name": ..., "parameters": ...}, ...]}]``
    OpenAI shape (used by Ollama, OpenAI, NVIDIA):
        ``[{"type": "function", "function": {"name": ..., "parameters": ...}}]``

    Returns a list copy in OpenAI shape. Empty/None input → ``[]``.
    Raises ``ValueError`` for any shape we don't recognise so we surface
    the mismatch loudly instead of silently sending an empty tool list.
    """
    if not tools:
        return []
    first = tools[0]
    if isinstance(first, dict) and first.get("type") == "function":
        # Already OpenAI shape — shallow-copy so callers can't mutate ours.
        return [dict(t) for t in tools]
    if isinstance(first, dict) and "function_declarations" in first:
        flattened: list[dict] = []
        for entry in tools:
            for fn in entry.get("function_declarations", []) or []:
                flattened.append({"type": "function", "function": dict(fn)})
        return flattened
    raise ValueError(
        "Unsupported tools shape: expected OpenAI-style "
        "[{type:function,...}] or Gemini-style "
        "[{function_declarations:[...]}]"
    )


def _omit_temperature(model: str) -> bool:
    """Return True for models that reject custom temperature and only accept the default."""
    lowered = (model or "").strip().lower()
    if "gpt-5" in lowered or "gpt_5" in lowered:
        return True
    # o-series reasoning models (o1, o3, o4-mini, etc.) also reject temperature
    if lowered in {"o1", "o1-mini", "o1-preview", "o3", "o3-mini", "o4-mini", "o4"}:
        return True
    if lowered.startswith(("o1-", "o3-", "o4-")):
        return True
    return False


def _http_retry_backoff_seconds(error_text: str, attempt: int) -> float:
    """Backoff for transient HTTP retries. Uses 60s base for 429/
    rate-limit errors (some providers — notably NVIDIA NIM — keep
    rate-limit state for multiple minutes, so a 30s wait is too short
    and just earns another 429), normal exponential otherwise."""
    lowered = str(error_text or "").lower()
    is_rate_limit = (
        "status_code: 429" in lowered
        or "status_code:429" in lowered
        or "too many requests" in lowered
        or "rate limit" in lowered
    )
    if is_rate_limit:
        base = 60.0
        exp = min(300.0, base * (2 ** max(0, attempt)))
        return exp + random.uniform(0, min(15.0, exp * 0.1))
    return _backoff_sleep_seconds(attempt)


def _truncate(s: str, n: int) -> str:
    if not s:
        return ""
    return s[:n]


def _normalize_azure_endpoint(endpoint: str) -> str:
    """Normalize Azure endpoints to the resource root expected by AzureProvider."""
    raw = str(endpoint or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlsplit(raw)
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
    except Exception:
        pass
    return raw.rstrip("/")


def normalize_reasoning_effort(value: Any) -> str:
    effort = str(value or "").strip().lower()
    return effort if effort in {"low", "medium", "high"} else ""


def llm_model_reference(model: str, reasoning_effort: Any = None) -> str:
    model_name = str(model or "").strip()
    effort = normalize_reasoning_effort(reasoning_effort)
    if not model_name or not effort:
        return model_name
    return f"{model_name}-{effort.upper()}"


def _resolve_provider_config(provider: str, provider_config: dict[str, Any] | None = None) -> dict[str, Any]:
    resolved = dict(provider_config or {})
    p = (provider or "gemini").strip().lower()
    if p == "azure":
        endpoint = str(
            resolved.get("azure_endpoint")
            or os.environ.get("AZURE_OPENAI_ENDPOINT")
            or ""
        ).strip()
        api_version = str(
            resolved.get("api_version")
            or os.environ.get("OPENAI_API_VERSION")
            or "2024-10-21"
        ).strip()
        endpoint = _normalize_azure_endpoint(endpoint)
        if endpoint:
            resolved["azure_endpoint"] = endpoint
        if api_version:
            resolved["api_version"] = api_version
        reasoning_effort = normalize_reasoning_effort(resolved.get("reasoning_effort"))
        if reasoning_effort:
            resolved["reasoning_effort"] = reasoning_effort
        else:
            resolved.pop("reasoning_effort", None)
    elif p == "openai":
        base_url = str(
            resolved.get("base_url")
            or os.environ.get("OPENAI_BASE_URL")
            or ""
        ).strip().rstrip("/")
        if base_url:
            resolved["base_url"] = base_url
        reasoning_effort = normalize_reasoning_effort(resolved.get("reasoning_effort"))
        if reasoning_effort:
            resolved["reasoning_effort"] = reasoning_effort
        else:
            resolved.pop("reasoning_effort", None)
    elif p == "nvidia":
        base_url = str(
            resolved.get("base_url")
            or os.environ.get("NVIDIA_BASE_URL")
            or "https://integrate.api.nvidia.com/v1"
        ).strip().rstrip("/")
        resolved["base_url"] = base_url
        reasoning_effort = normalize_reasoning_effort(resolved.get("reasoning_effort"))
        if reasoning_effort:
            resolved["reasoning_effort"] = reasoning_effort
        else:
            resolved.pop("reasoning_effort", None)
    elif p == "ollama":
        base = str(
            resolved.get("ollama_base_url")
            or os.environ.get("OLLAMA_BASE_URL")
            or "http://localhost:11434"
        ).strip().rstrip("/")
        resolved["ollama_base_url"] = base
        keep_alive_raw = resolved.get("ollama_keep_alive")
        keep_alive = str(keep_alive_raw or "").strip()
        if keep_alive:
            resolved["ollama_keep_alive"] = keep_alive
        else:
            resolved.pop("ollama_keep_alive", None)
        # Reasoning-effort is model-specific for Ollama (set via the
        # Modelfile, not a standard generation option), so we never
        # propagate the field — strip it if a strategy left it on.
        resolved.pop("reasoning_effort", None)
    return resolved


def _safe_provider_meta(provider: str, provider_config: dict[str, Any] | None = None) -> dict[str, Any]:
    config = _resolve_provider_config(provider, provider_config)
    p = (provider or "gemini").strip().lower()
    if p == "azure":
        meta = {
            "azure_endpoint": str(config.get("azure_endpoint") or ""),
            "api_version": str(config.get("api_version") or ""),
        }
        reasoning_effort = normalize_reasoning_effort(config.get("reasoning_effort"))
        if reasoning_effort:
            meta["reasoning_effort"] = reasoning_effort
        return meta
    if p == "openai":
        meta = {
            "base_url": str(config.get("base_url") or ""),
        }
        reasoning_effort = normalize_reasoning_effort(config.get("reasoning_effort"))
        if reasoning_effort:
            meta["reasoning_effort"] = reasoning_effort
        return meta
    if p == "nvidia":
        meta = {
            "base_url": str(config.get("base_url") or "https://integrate.api.nvidia.com/v1"),
        }
        reasoning_effort = normalize_reasoning_effort(config.get("reasoning_effort"))
        if reasoning_effort:
            meta["reasoning_effort"] = reasoning_effort
        return meta
    return {}


def _build_pydantic_ai_model(provider: str, api_key: str, model: str, provider_config: dict[str, Any] | None = None):
    """Build a PydanticAI model instance for Gemini, DeepSeek, OpenAI, or Azure OpenAI."""
    if not _PYDANTIC_AI_AVAILABLE or not api_key or not model:
        return None
    p = (provider or "gemini").strip().lower()
    resolved = _resolve_provider_config(provider, provider_config)

    def _prefers_prompted_structured_output(provider_name: str, model_name: str) -> bool:
        # NVIDIA NIM kimi-k2.x has the same shape-collapse pathology as
        # Azure gpt-oss / gpt-5 — empty `{}` (2 tokens) on the first call
        # when the structured-output schema is forced. Use prompted-JSON
        # mode for those models so PydanticAI re-prompts on the wire
        # without triggering the broken schema constraint.
        lowered_model = str(model_name or "").strip().lower()
        return (
            provider_name in {"azure", "openai", "nvidia"}
            and _model_skips_json_object_format(lowered_model)
        )

    def _prompted_json_profile() -> ModelProfile | None:
        if ModelProfile is None:
            return None
        return ModelProfile(
            supports_tools=True,
            supports_json_schema_output=False,
            supports_json_object_output=True,
            default_structured_output_mode="prompted",
        )

    if p == "azure":
        azure_endpoint = str(resolved.get("azure_endpoint") or "").strip()
        api_version = str(resolved.get("api_version") or "").strip()
        if not azure_endpoint:
            raise ValueError("Azure OpenAI requires azure_endpoint")
        if not api_version:
            raise ValueError("Azure OpenAI requires api_version")
        profile = _prompted_json_profile() if _prefers_prompted_structured_output(p, model) else None
        return OpenAIChatModel(
            model,
            provider=AzureProvider(
                azure_endpoint=azure_endpoint,
                api_version=api_version,
                api_key=api_key,
            ),
            profile=profile,
        )
    if p == "deepseek":
        lower_model = model.lower()
        if "reasoner" in lower_model and DeepSeekProvider is not None and ModelProfile is not None:
            # DeepSeek reasoner supports JSON output, but the local PydanticAI DeepSeek
            # profile still defaults structured mode to tools for this model. Override
            # it to prompted JSON-object mode, and keep chat as a later fallback only.
            reasoner_profile = ModelProfile(
                supports_tools=True,
                supports_json_schema_output=False,
                supports_json_object_output=True,
                default_structured_output_mode="prompted",
            )
            return OpenAIChatModel(
                model,
                provider=DeepSeekProvider(api_key=api_key),
                profile=reasoner_profile,
            )
        return OpenAIChatModel(
            model,
            provider=OpenAIProvider(
                base_url="https://api.deepseek.com/v1",
                api_key=api_key,
            ),
        )
    if p == "openai":
        base_url = str(resolved.get("base_url") or "").strip()
        profile = _prompted_json_profile() if _prefers_prompted_structured_output(p, model) else None
        if base_url:
            return OpenAIChatModel(
                model,
                provider=OpenAIProvider(
                    base_url=base_url,
                    api_key=api_key,
                ),
                profile=profile,
            )
        return OpenAIChatModel(
            model,
            provider=OpenAIProvider(api_key=api_key),
            profile=profile,
        )
    if p == "nvidia":
        base_url = str(resolved.get("base_url") or "https://integrate.api.nvidia.com/v1").strip()
        profile = _prompted_json_profile()
        return OpenAIChatModel(
            model,
            provider=OpenAIProvider(
                base_url=base_url,
                api_key=api_key,
            ),
            profile=profile,
        )
    return GoogleModel(
        model,
        provider=GoogleProvider(api_key=api_key),
    )


def _terminal_llm_failure_cache_key(provider: str, model: str, provider_config: dict[str, Any] | None = None) -> str:
    safe_meta = _safe_provider_meta(provider, provider_config)
    return hashlib.sha256(
        json.dumps(
            {
                "provider": (provider or "").strip().lower(),
                "model": (model or "").strip(),
                "provider_meta": safe_meta,
            },
            sort_keys=True,
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _is_transient_http_error(error_text: str) -> bool:
    """Return True if the error looks like a retriable HTTP/network error (502, 503, 429, 500, 504, connection reset)."""
    lowered = str(error_text or "").lower()
    for code in ("429", "500", "502", "503", "504"):
        if f"status_code: {code}" in lowered or f"status_code:{code}" in lowered or f"status code {code}" in lowered:
            return True
    return any(phrase in lowered for phrase in (
        "bad gateway", "service unavailable", "too many requests",
        "rate limit", "gateway timeout", "connection reset", "connection error",
        "temporarily unavailable",
    ))


def _is_terminal_provider_not_found(provider: str, exc: Exception) -> bool:
    """Return True if the exception means the model genuinely doesn't exist
    on this provider (a wrong-model-name configuration error), as opposed
    to a transient outage. Terminal errors should NOT be retried, and
    callers that batch-and-split prompts should NOT split on this — every
    sub-batch would fail with the identical error.

    Covered patterns:
      * Azure: 404, resource-not-found, deployment-not-found
      * Gemini: 404 NOT_FOUND with "models/X is not found" or
        "is not supported for generateContent"
      * OpenAI: 404 with "model_not_found" / "does not exist"
      * Anthropic: 404 with "not_found_error"
    """
    text = str(exc or "")
    lowered = text.lower()
    p = (provider or "").strip().lower()
    if p == "azure":
        return (
            "status_code: 404" in lowered
            or "resource not found" in lowered
            or "deploymentnotfound" in lowered
            or "model not found" in lowered
        )
    if p in ("gemini", "google", "google-gemini"):
        return (
            "404 not_found" in lowered
            or ("404" in lowered and "is not found" in lowered)
            or ("404" in lowered and "is not supported for generatecontent" in lowered)
            or "models/" in lowered and "is not found" in lowered
        )
    if p in ("openai", "nvidia"):
        return (
            "model_not_found" in lowered
            or ("404" in lowered and "does not exist" in lowered)
            or ("404" in lowered and "the model" in lowered)
        )
    if p in ("anthropic", "claude"):
        return (
            "not_found_error" in lowered
            # Require a specific "model doesn't exist" phrase, not just
            # any 404 that mentions "model" (which could be a URL path).
            or ("404" in lowered and ("not_found_error" in lowered or "model:" in lowered and "does not exist" in lowered))
        )
    # Generic fallback: require a specific "model doesn't exist" phrase.
    # We INTENTIONALLY don't match on the bare substring "model" — many
    # transient 404s from CDNs/gateways include the request path
    # (e.g. "/v1/models/foo") and would otherwise be cached as
    # permanently terminal, suppressing all future calls.
    return (
        "404" in lowered
        and (
            "model_not_found" in lowered
            or "is not found for" in lowered
            or "is not supported for generatecontent" in lowered
            or "deploymentnotfound" in lowered
            or "deployment not found" in lowered
            or "the model" in lowered and "does not exist" in lowered
        )
    )


def _terminal_provider_not_found_hint(provider: str, model: str, provider_config: dict[str, Any] | None = None) -> str:
    p = (provider or "").strip().lower()
    if p == "azure":
        resolved = _resolve_provider_config(provider, provider_config)
        endpoint = str(resolved.get("azure_endpoint") or "")
        return (
            "Azure 404 usually means the Azure endpoint root or deployment name is wrong. "
            "For this backend, set azure_openai_endpoint to the resource root like "
            "'https://<resource>.services.ai.azure.com' or 'https://<resource>.openai.azure.com', "
            f"not a full '/models/chat/completions' or '/openai/v1/' URL. Use the deployment name as llm_model "
            f"(current: {model!r}). Resolved endpoint: {endpoint!r}."
        )
    name = (model or "").strip()
    # Detect obvious provider-model mismatches so the operator sees the
    # likely root cause rather than digging through retry logs.
    lower_model = name.lower()
    cross_provider_hint = ""
    if p in ("gemini", "google", "google-gemini") and (
        lower_model.startswith("claude") or lower_model.startswith("gpt") or lower_model.startswith("o1") or lower_model.startswith("o3")
    ):
        cross_provider_hint = (
            f" The model name {name!r} doesn't belong to Gemini — it looks like a "
            "Claude/OpenAI model. Edit the Model record so provider matches: "
            "use 'anthropic' or 'claude-cli' for Claude, 'openai' for GPT/o-series."
        )
    elif p in ("openai", "nvidia") and lower_model.startswith("claude"):
        cross_provider_hint = (
            f" The model name {name!r} looks like a Claude model but the provider "
            "is OpenAI. Change provider to 'anthropic' or 'claude-cli'."
        )
    elif p in ("openai", "nvidia") and lower_model.startswith("gemini"):
        cross_provider_hint = (
            f" The model name {name!r} looks like a Gemini model but the provider "
            "is OpenAI. Change provider to 'gemini'."
        )
    elif p in ("anthropic", "claude") and (lower_model.startswith("gpt") or lower_model.startswith("gemini")):
        cross_provider_hint = (
            f" The model name {name!r} doesn't belong to Anthropic — change "
            "provider to match (openai or gemini)."
        )
    if cross_provider_hint:
        return cross_provider_hint.strip()
    return f"Provider {provider!r} returned 404 for model {name!r}. Check the model name spelling and the provider's available models."


def _structured_model_name(provider: str, model: str) -> str:
    """Normalize model names for structured-output compatibility."""
    model_name = (model or "").strip()
    p = (provider or "gemini").strip().lower()
    if model_name.startswith("models/"):
        model_name = model_name.split("/", 1)[1]
    if p == "azure" and not model_name:
        fallback = (
            os.environ.get("AZURE_OPENAI_DEPLOYMENT")
            or os.environ.get("AZURE_OPENAI_MODEL")
            or "gpt-4.1-mini"
        ).strip()
        return fallback
    if p == "gemini" and model_name in {"gemini-2.0-flash-exp", ""}:
        fallback = (os.environ.get("GEMINI_STRUCTURED_MODEL") or "gemini-3-flash-preview").strip() or "gemini-3-flash-preview"
        if model_name and fallback != model_name:
            import sys
            print(
                f"[llm_utils] Structured output is using {fallback!r} instead of deprecated/invalid Gemini model {model_name!r}.",
                file=sys.stderr,
                flush=True,
            )
        return fallback
    return model_name


def _structured_model_candidates(provider: str, model: str) -> list[str]:
    """Return structured-output model candidates in priority order."""
    structured_model = _structured_model_name(provider, model)
    candidates = [structured_model]
    p = (provider or "gemini").strip().lower()
    if p == "deepseek" and "reasoner" in structured_model.lower():
        fallback = (os.environ.get("DEEPSEEK_STRUCTURED_MODEL") or "deepseek-chat").strip() or "deepseek-chat"
        if fallback and fallback not in candidates:
            candidates.append(fallback)
    return candidates


def _structured_json_retry_enabled(error_text: str) -> bool:
    lowered = str(error_text or "").strip().lower()
    if not lowered:
        return False
    return (
        "output validation" in lowered
        or "validation" in lowered
        or "json" in lowered
        or "schema" in lowered
        or "unsupportedtooluse" in lowered
        or "unsupported tool use" in lowered
        or "tool_choice 'required' is not supported" in lowered
        or "got null" in lowered
        or ("content" in lowered and "null" in lowered)
    )


def _minify_schema_for_prompt(value: Any) -> Any:
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if key in {"title", "description", "default", "examples"}:
                continue
            out[key] = _minify_schema_for_prompt(item)
        return out
    if isinstance(value, list):
        return [_minify_schema_for_prompt(item) for item in value]
    return value


def _structured_output_schema_hint(output_type: Any) -> str:
    if TypeAdapter is None or output_type is None:
        return ""
    try:
        schema = TypeAdapter(output_type).json_schema()
    except Exception:
        return ""
    try:
        text = json.dumps(_minify_schema_for_prompt(schema), separators=(",", ":"), ensure_ascii=True)
    except Exception:
        return ""
    if len(text) > 4000:
        text = text[:4000] + "..."
    return text


def _raw_json_candidates(raw_text: str) -> list[str]:
    text = str(raw_text or "").strip()
    if not text:
        return []
    candidates: list[str] = []
    seen: set[str] = set()

    def _add(candidate: str) -> None:
        value = str(candidate or "").strip()
        if value and value not in seen:
            seen.add(value)
            candidates.append(value)

    _add(text)
    for match in re.finditer(r"```(?:json)?\s*(.*?)```", text, flags=re.IGNORECASE | re.DOTALL):
        _add(match.group(1))
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start >= 0 and end > start:
            _add(text[start:end + 1])
    return candidates


def _repair_common_json_text(candidate: str) -> str:
    text = str(candidate or "").strip()
    if not text:
        return ""
    if _repair_json_with_library is not None:
        try:
            repaired = _repair_json_with_library(text, skip_json_loads=True, return_objects=False)
            repaired = str(repaired or "").strip()
            if repaired:
                text = repaired
        except Exception:
            pass
    text = (
        text
        .replace("\u201c", '"')
        .replace("\u201d", '"')
        .replace("\u2018", "'")
        .replace("\u2019", "'")
        .replace("\ufeff", "")
    )
    text = re.sub(r",\s*([}\]])", r"\1", text)
    return text


def _structured_output_top_level_fields(output_type: Any) -> list[str]:
    """Return top-level field names, preferring aliases when available."""
    fields = getattr(output_type, "model_fields", None)
    if isinstance(fields, dict):
        result = []
        for name, info in fields.items():
            alias = getattr(info, "alias", None)
            result.append(str(alias if alias else name))
        return [r for r in result if r]
    return []


def _unwrap_nested_lists(data: Any) -> Any:
    """Unwrap common LLM error: list items wrapped in extra [] brackets.

    e.g. {"articles": [[{...}], [{...}]]} → {"articles": [{...}, {...}]}
    """
    if not isinstance(data, dict):
        return data
    changed = False
    result = {}
    for k, v in data.items():
        if isinstance(v, list) and v and all(isinstance(item, list) and len(item) == 1 for item in v):
            result[k] = [item[0] for item in v]
            changed = True
        else:
            result[k] = v
    return result if changed else data


def _coerce_structured_output_shape(output_type: Any, parsed: Any) -> Any:
    top_fields = _structured_output_top_level_fields(output_type)
    if len(top_fields) != 1:
        return parsed
    sole = top_fields[0]
    if isinstance(parsed, dict):
        if sole in parsed:
            return parsed
        if len(parsed) == 1:
            only_value = next(iter(parsed.values()))
            return {sole: only_value}
        return parsed
    return {sole: parsed}


def _decode_pipe_table(text: str) -> list[dict] | None:
    """Decode pipe-delimited table text into list[dict].

    Format:
        header1|header2|header3
        val1|val2|val3
        val4|val5|val6

    Handles: _ for empty/default, semicolon-separated lists, numeric coercion,
    true/false bools.
    """
    lines = [l.strip() for l in text.strip().split("\n") if l.strip() and not l.strip().startswith("#")]
    if len(lines) < 2 or "|" not in lines[0]:
        return None
    headers = [h.strip() for h in lines[0].split("|")]
    if len(headers) < 2:
        return None
    rows: list[dict] = []
    for line in lines[1:]:
        if set(line.strip()) <= {"-", "|", " ", "+", "="}:
            continue
        values = [v.strip() for v in line.split("|")]
        row: dict = {}
        for i, h in enumerate(headers):
            if not h:
                continue
            val = values[i] if i < len(values) else ""
            if val in ("_", ""):
                continue
            low = val.lower()
            if low in ("true", "false"):
                row[h] = low == "true"
                continue
            if ";" in val:
                row[h] = [v.strip() for v in val.split(";") if v.strip()]
                continue
            try:
                row[h] = float(val) if "." in val else int(val)
                continue
            except ValueError:
                pass
            row[h] = val
        rows.append(row)
    return rows if rows else None


def _try_pipe_table_as_structured(output_type: Any, raw_text: str) -> Any | None:
    """Try to parse raw_text as a pipe-delimited table and validate against output_type."""
    if TypeAdapter is None or output_type is None:
        return None
    # Extract pipe table from possible surrounding text
    text = raw_text.strip()
    # Try to find a pipe table block (skip markdown fences if any)
    for match in re.finditer(r"```(?:\w+)?\s*(.*?)```", text, flags=re.DOTALL):
        candidate = match.group(1).strip()
        if "|" in candidate:
            text = candidate
            break
    rows = _decode_pipe_table(text)
    if rows is None:
        return None
    adapter = TypeAdapter(output_type)
    # Build candidate wrappings
    candidates: list[Any] = []
    top_fields = _structured_output_top_level_fields(output_type)
    # Check for nested grouping (e.g., articles → classifications)
    ref_key = "ref" if (rows and "ref" in rows[0]) else ("r" if (rows and "r" in rows[0]) else None)
    if ref_key:
        grouped: dict[str, list[dict]] = {}
        for row in rows:
            ref = str(row.get(ref_key, ""))
            rest = {k: v for k, v in row.items() if k != ref_key}
            grouped.setdefault(ref, []).append(rest)
        # Try wrapping as {articles: [{ref: ref, classifications: [items]}]}
        nested_cls = [{"ref": ref, "classifications": items} for ref, items in grouped.items()]
        nested_plain = [{"ref": ref, **items[0]} for ref, items in grouped.items() if items]
        if len(top_fields) == 1:
            candidates.append({top_fields[0]: nested_cls})
            candidates.append({top_fields[0]: nested_plain})
        candidates.append({"articles": nested_cls})
        candidates.append({"articles": nested_plain})
    # Simple list wrapping
    if len(top_fields) == 1:
        candidates.append({top_fields[0]: rows})
    candidates.append({"articles": rows})
    candidates.append({"a": rows})
    candidates.append(rows)
    for candidate in candidates:
        try:
            return adapter.validate_python(candidate)
        except Exception:
            continue
    return None


def _validate_structured_output_from_raw_text(output_type: Any, raw_text: str) -> Any | None:
    if TypeAdapter is None or output_type is None:
        return None
    adapter = TypeAdapter(output_type)
    last_exc: Exception | None = None
    for candidate in _raw_json_candidates(raw_text):
        for variant in (candidate, _repair_common_json_text(candidate)):
            if not variant:
                continue
            try:
                parsed = json.loads(variant)
            except Exception as exc:
                last_exc = exc
                continue
            # If parsed is a single-key dict whose value is a JSON string, try unwrapping it
            # (e.g. {"final": "{\"ok\":true,...}"} → {"ok":true,...})
            # IMPORTANT: inner_parsed must be tried BEFORE parsed to avoid Pydantic extra="ignore"
            # silently validating {"final":"..."} as an empty result (e.g. articles=[]).
            unwrapped = _unwrap_nested_lists(parsed)
            unwrapped_candidates = [parsed, _coerce_structured_output_shape(output_type, parsed)]
            if unwrapped is not parsed:
                unwrapped_candidates.append(unwrapped)
                unwrapped_candidates.append(_coerce_structured_output_shape(output_type, unwrapped))
            if isinstance(parsed, dict) and len(parsed) == 1:
                inner_val = next(iter(parsed.values()))
                if isinstance(inner_val, str) and inner_val.strip().startswith("{"):
                    try:
                        inner_parsed = json.loads(inner_val)
                        # Prepend inner candidates so they are tried BEFORE the wrapper dict
                        unwrapped_candidates = [
                            inner_parsed,
                            _coerce_structured_output_shape(output_type, inner_parsed),
                        ] + unwrapped_candidates
                    except Exception:
                        pass
            for candidate_value in unwrapped_candidates:
                try:
                    return adapter.validate_python(candidate_value)
                except Exception as exc:
                    last_exc = exc
                    continue
    # Fallback: try pipe-delimited table format
    pipe_result = _try_pipe_table_as_structured(output_type, raw_text)
    if pipe_result is not None:
        return pipe_result
    if last_exc is not None:
        raise last_exc
    return None


_SKELETON_ID_KEYS = frozenset(("ref", "article_hash"))


def _is_skeleton_structured_output(obj: Any) -> bool:
    """Return True if a validated Pydantic output is skeleton — structure with no meaningful data.

    Uses ``model_dump(exclude_unset=True)`` so fields not present in the
    original JSON are ignored, but fields the model explicitly set (even to
    default values like ``[]``) are preserved.  For batch responses (those
    with list fields) we check that at least one list item carries data
    beyond bare identifiers (ref / article_hash).  For non-batch responses
    we check that at least one non-identifier key survived the filter.
    """
    if obj is None:
        return True
    if not hasattr(obj, "model_dump"):
        return False
    try:
        dumped = obj.model_dump(exclude_unset=True)
    except Exception:
        return False
    if not dumped:
        return True
    has_list = False
    for _key, val in dumped.items():
        if not isinstance(val, list):
            continue
        has_list = True
        for item in val:
            if isinstance(item, dict):
                if any(k not in _SKELETON_ID_KEYS for k in item):
                    return False  # at least one item has substantive fields
            elif item is not None:
                return False
    if has_list:
        return True  # had list(s) but every item was identifier-only
    # Non-batch: any non-ID key with a value means not skeleton
    return all(k in _SKELETON_ID_KEYS for k in dumped)


def _raw_structured_fallback_prompt(prompt: str, output_type: Any) -> str:
    schema_hint = _structured_output_schema_hint(output_type)
    top_fields = _structured_output_top_level_fields(output_type)
    base = (
        "Return ONLY valid JSON. No markdown, no prose, no comments. "
        "Do NOT wrap the JSON in any container object. "
        "Do NOT use wrapper keys like 'final', 'result', 'output', 'answer', or 'response'. "
        "Your response must begin with { and end with } — nothing else. "
        "Use double-quoted JSON keys and strings. "
        "Use ONLY the exact short property names from the schema below. "
        "Do NOT expand abbreviations (use 't' not 'ticker', 'et' not 'event_type', 'id' not 'impact_direction', etc). "
        "Keep string values concise (max 10 words). "
        "Every item MUST include its core fields filled in — never return stub/skeleton entries."
    )
    if top_fields:
        if len(top_fields) == 1:
            base += f"\nThe top-level JSON must be an object with the key \"{top_fields[0]}\"."
        else:
            base += f"\nThe top-level JSON must be an object using these keys: {', '.join(top_fields)}."
    if schema_hint:
        base += f"\nJSON schema: {schema_hint}"
    return f"{base}\n\nTask:\n{prompt}"


def _build_structured_model_settings(
    provider: str,
    max_output_tokens: int,
    timeout: float,
    temperature: float,
    model: str = "",
):
    """Build provider-appropriate PydanticAI model settings for structured output."""
    effective_max_tokens = max_output_tokens if max_output_tokens and max_output_tokens > 0 else None
    p = (provider or "gemini").strip().lower()
    skip_temp = _omit_temperature(model)
    if p == "gemini" and GoogleModelSettings is not None:
        if effective_max_tokens is None or effective_max_tokens < 256:
            effective_max_tokens = 256
        kwargs = dict(
            max_tokens=effective_max_tokens,
            timeout=timeout,
            parallel_tool_calls=False,
            google_thinking_config={"thinking_budget": 0, "include_thoughts": False},
        )
        if not skip_temp:
            kwargs["temperature"] = float(temperature)
        return GoogleModelSettings(**kwargs)
    kwargs = dict(max_tokens=effective_max_tokens, timeout=timeout, parallel_tool_calls=False)
    if not skip_temp:
        kwargs["temperature"] = float(temperature)
    return ModelSettings(**kwargs)


def _try_raw_structured_json_once(
    provider: str,
    api_key: str,
    model: str,
    prompt: str,
    output_type: Any,
    *,
    max_output_tokens: int,
    timeout_sec: int | None,
    provider_config: dict[str, Any] | None = None,
    system_prompt: str | None = None,
) -> Any | None:
    _LAST_PLAIN_LLM_CALL_ERROR.error = ""
    raw_prompt = _raw_structured_fallback_prompt(prompt, output_type)
    # Prepend caller's system prompt so alias key instructions reach the model
    if system_prompt and isinstance(system_prompt, str) and system_prompt.strip():
        raw_prompt = f"Instructions: {system_prompt.strip()}\n\n{raw_prompt}"
    raw_text = call_llm_by_provider(
        provider,
        api_key,
        model,
        raw_prompt,
        max_output_tokens=max_output_tokens,
        timeout_sec=timeout_sec,
        retries=0,
        response_mime_type="application/json",
        provider_config=provider_config,
    )
    if not raw_text:
        plain_err = getattr(_LAST_PLAIN_LLM_CALL_ERROR, "error", "") or ""
        raise RuntimeError(plain_err or f"LLM call returned empty response (provider={provider!r}, model={model!r})")
    try:
        result = _validate_structured_output_from_raw_text(output_type, raw_text)
    except Exception as val_exc:
        snippet = (raw_text or "")[:300].replace("\n", " ")
        raise RuntimeError(f"JSON validation failed: {val_exc} | raw[:{min(300, len(raw_text or ''))}]={snippet!r}") from val_exc
    if _is_skeleton_structured_output(result):
        snippet = (raw_text or "")[:200].replace("\n", " ")
        raise RuntimeError(f"Skeleton output: model returned structure with no meaningful data | raw[:{min(200, len(raw_text or ''))}]={snippet!r}")
    return result


def _call_claude_cli_plain(
    *,
    model: str,
    prompt: str,
    provider_config: dict[str, Any] | None = None,
    timeout_sec: int | None = None,
    retries: int = 0,
) -> str:
    """Plain-text claude-cli call. Returns the assistant's reply text, or
    empty string on failure. Mirrors the existing call_llm_by_provider
    contract (best-effort, never raises into the strategy)."""
    if not model:
        return ""
    cfg = provider_config or {}
    cli_path = (cfg.get("cli_path") or "claude") or "claude"
    raw_extra = cfg.get("extra_args")
    extra_args: list[str]
    # Validate cli_path + extra_args BEFORE spawning. Both go through the
    # same allowlist gates as the chatbot path so an authenticated user
    # can't pivot the strategy config into RCE.
    try:
        from chatbot.claude_cli_provider import (
            validate_extra_args, _resolve_cli_path, _classify_error_text,
            ClaudeCliNotInstalledError, ClaudeCliError,
        )
    except Exception as e:
        try:
            _LAST_PLAIN_LLM_CALL_ERROR.error = f"claude_cli_provider import failed: {e}"
        except Exception:
            pass
        return ""
    if isinstance(raw_extra, str):
        try:
            extra_args = validate_extra_args(raw_extra)
        except Exception as e:
            try:
                _LAST_PLAIN_LLM_CALL_ERROR.error = f"invalid extra_args: {e}"
            except Exception:
                pass
            return ""
    elif isinstance(raw_extra, (list, tuple)):
        extra_args = [str(x) for x in raw_extra]
    else:
        extra_args = []
    try:
        resolved_cli = _resolve_cli_path(cli_path)
    except (ClaudeCliNotInstalledError, ClaudeCliError) as e:
        try:
            _LAST_PLAIN_LLM_CALL_ERROR.error = str(e)
        except Exception:
            pass
        return ""

    # Map reasoning_effort → --effort. Empty/invalid values produce no
    # flag so CC runs with its default.
    effort = str(cfg.get("reasoning_effort") or "").strip().lower()
    if effort not in ("low", "medium", "high", "xhigh", "max"):
        effort = ""
    effort_args = ["--effort", effort] if effort else []
    # If extra_args already contains --effort, the user-typed value wins.
    if any(tok == "--effort" for tok in extra_args):
        effort_args = []

    import subprocess as _subprocess
    timeout = _coerce_timeout_sec(timeout_sec)
    last_err = ""
    for attempt in range(max(1, int(retries or 0) + 1)):
        argv = [
            resolved_cli, "-p",
            "--output-format", "json",
            "--model", model,
            "--tools", "",
            "--strict-mcp-config",
            "--no-session-persistence",
            "--disable-slash-commands",
            *effort_args,
            *extra_args,
        ]
        try:
            kwargs: dict[str, Any] = {}
            if sys.platform == "win32":
                kwargs["creationflags"] = getattr(_subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            proc = _subprocess.run(
                argv,
                input=prompt or "",
                capture_output=True,
                text=True,
                timeout=timeout,
                encoding="utf-8",
                errors="replace",
                **kwargs,
            )
        except FileNotFoundError:
            last_err = (
                f"claude binary not found at {resolved_cli!r}; "
                "install with `npm i -g @anthropic-ai/claude-code`."
            )
            break
        except _subprocess.TimeoutExpired:
            last_err = f"claude -p timed out after {timeout}s"
            if attempt < retries:
                time.sleep(_backoff_sleep_seconds(attempt))
                continue
            break
        stdout = (proc.stdout or "").strip()
        if not stdout:
            last_err = (proc.stderr or "").strip()[:300] or "no stdout from claude"
            if attempt < retries:
                time.sleep(_backoff_sleep_seconds(attempt))
                continue
            break
        try:
            envelope = json.loads(stdout)
        except Exception:
            last_err = f"claude returned non-JSON: {stdout[:200]!r}"
            if attempt < retries:
                time.sleep(_backoff_sleep_seconds(attempt))
                continue
            break
        if envelope.get("is_error"):
            # Classify so operators get an actionable error (e.g.
            # "log in on the server") instead of a generic empty string.
            classified = _classify_error_text(str(envelope.get("result") or "claude error"))
            last_err = str(classified)
            # Don't retry on hard failures.
            break
        result_text = envelope.get("result") or ""
        # Persist to prompt cache if enabled.
        try:
            _effort_key = str((provider_config or {}).get("reasoning_effort", "")).strip().lower()
            _store_prompt_cache(prompt, model, _effort_key, str(result_text))
        except Exception:
            pass
        # T10 critical-guard capture — CLI providers map signals to synthetic status
        try:
            _stash_last_http(status=200, body=None, exc=None)
        except Exception:
            pass
        return str(result_text)
    try:
        _LAST_PLAIN_LLM_CALL_ERROR.error = last_err or "claude-cli plain call failed"
    except Exception:
        pass
    # T10 critical-guard capture — synthesize status from last_err for CLI providers
    try:
        _body_l = (last_err or "").lower()
        if "usage_limit_reached" in _body_l or "quota" in _body_l:
            _stash_last_http(status=429, body=(last_err or "")[:1000], exc=None)
        else:
            _stash_last_http(status=500, body=(last_err or "claude-cli plain call failed")[:1000], exc=None)
    except Exception:
        pass
    return ""


def _call_codex_cli_plain(
    *,
    model: str,
    prompt: str,
    provider_config: dict[str, Any] | None = None,
    timeout_sec: int | None = None,
    retries: int = 0,
) -> str:
    """Plain-text codex-cli call. Returns the assistant's reply text or
    empty string on failure. The heavy lifting (subprocess management,
    allowlist validation, auth probe, retries) lives in
    ``backend/chatbot/codex_cli_provider.py``; this is a thin shim that
    matches the ``call_llm_by_provider`` contract and updates the
    shared ``_LAST_PLAIN_LLM_CALL_ERROR`` thread-local on failure."""
    # Clear the per-thread error state at the start of every call so a
    # stale message from a previous failed call doesn't leak into the
    # caller's diagnostics on success.
    try:
        _LAST_PLAIN_LLM_CALL_ERROR.error = ""
    except Exception:
        pass
    if not model:
        try:
            _LAST_PLAIN_LLM_CALL_ERROR.error = "codex-cli: model is required"
        except Exception:
            pass
        return ""
    try:
        from chatbot.codex_cli_provider import (
            call_codex_cli_plain as _impl,
            _get_last_error as _impl_last_err,
            _get_last_usage as _impl_last_usage,
        )
    except Exception as e:
        try:
            _LAST_PLAIN_LLM_CALL_ERROR.error = f"codex_cli_provider import failed: {e}"
        except Exception:
            pass
        return ""
    _t0 = time.monotonic()
    text = _impl(
        model=model,
        prompt=prompt,
        provider_config=provider_config,
        timeout_sec=timeout_sec,
        retries=retries,
    )
    # Surface token usage in telemetry even for plain (non-structured)
    # calls — otherwise the strategy's macro/sentiment LLM rows show
    # 0/0/$0.00 even though the upstream did real work.
    try:
        _codex_usage_plain = _impl_last_usage() or {}
    except Exception:
        _codex_usage_plain = {}
    if not text:
        try:
            _LAST_PLAIN_LLM_CALL_ERROR.error = _impl_last_err() or "codex-cli plain call failed"
        except Exception:
            pass
        # T10 critical-guard capture — CLI providers map stderr signals to
        # synthetic status: usage_limit_reached / quota → 429, else 500.
        try:
            _err_body = _impl_last_err() or "codex-cli plain call failed"
            _body_l = (_err_body or "").lower()
            if "usage_limit_reached" in _body_l or "quota" in _body_l:
                _stash_last_http(status=429, body=(_err_body or "")[:1000], exc=None)
            else:
                _stash_last_http(status=500, body=(_err_body or "")[:1000], exc=None)
        except Exception:
            pass
        try:
            _safe_record(
                provider="codex-cli", model=model, usage=_codex_usage_plain,
                ok=False,
                duration_ms=int((time.monotonic() - _t0) * 1000),
                retry_count=int(retries or 0),
                error=(_impl_last_err() or "codex-cli plain call failed")[:200],
                model_id=None,
            )
        except Exception:
            pass
        return text
    # T10 critical-guard capture — success
    try:
        _stash_last_http(status=200, body=None, exc=None)
    except Exception:
        pass
    try:
        _safe_record(
            provider="codex-cli", model=model, usage=_codex_usage_plain, ok=True,
            duration_ms=int((time.monotonic() - _t0) * 1000),
            retry_count=int(retries or 0), error=None,
            model_id=None,
        )
    except Exception:
        pass
    return text


def _call_codex_cli_structured_from_strategy(
    *,
    model: str,
    prompt: str,
    output_type: Any,
    system_prompt: str | Sequence[str] | None = None,
    provider_config: dict[str, Any] | None = None,
    timeout_sec: int | None = None,
    retries: int = 1,
    output_retries: int | None = None,
    use_prompt_cache: bool = False,
) -> Any:
    """Codex-cli structured-output adapter. Mirrors the
    ``_call_claude_cli_structured_from_strategy`` contract: spawns a
    codex app-server, sends the prompt with a JSON-schema instruction,
    parses + validates the model's text response against ``output_type``.
    Retries on invalid JSON up to ``output_retries`` (default 2).
    """
    _t0 = time.monotonic()
    if not model or output_type is None:
        return None
    if isinstance(system_prompt, (list, tuple)):
        sys_str = "\n\n".join([s for s in system_prompt if s])
    else:
        sys_str = system_prompt or ""
    cfg = provider_config or {}
    cli_path = (cfg.get("cli_path") or "codex") or "codex"

    # Scoped prompt cache (mirror the claude-cli adapter)
    cache_effort = ""
    if use_prompt_cache:
        cache_effort = str((provider_config or {}).get("reasoning_effort", "")).strip().lower()
        try:
            _cached_raw = _check_prompt_cache(prompt, model, cache_effort, force_cache=True)
        except Exception:
            _cached_raw = None
        if _cached_raw:
            try:
                _cached_obj = _validate_structured_output_from_raw_text(output_type, _cached_raw)
            except Exception:
                _cached_obj = None
            if _cached_obj is not None:
                _LAST_STRUCTURED_LLM_CALL.data = {
                    "provider": "codex-cli",
                    "requested_model": model,
                    "model_candidates": [model],
                    "attempted_models": [],
                    "provider_meta": {"cli_path": cli_path},
                    "effective_model": model,
                    "fallback_used": False,
                    "raw_json_fallback_used": False,
                    "ok": True,
                    "error": "",
                    "usage": {},
                    "suppressed": False,
                    "prompt_cache_hit": True,
                }
                _safe_record(
                    provider="codex-cli", model=model, usage={}, ok=True,
                    duration_ms=int((time.monotonic() - _t0) * 1000),
                    retry_count=0, error=None,
                    model_id=cfg.get("id") if isinstance(cfg, dict) else None,
                )
                return _cached_obj

    try:
        from chatbot.codex_cli_provider import (
            call_codex_cli_structured as _impl,
            _get_last_error as _impl_last_err,
            _get_last_usage as _impl_last_usage,
            CodexCliError, CodexCliNotInstalledError, CodexCliNotAuthenticatedError,
            CodexCliValidationError, CodexCliQuotaExceededError,
        )
    except Exception as e:
        _LAST_STRUCTURED_LLM_CALL.data = {
            "provider": "codex-cli",
            "requested_model": model,
            "model_candidates": [model],
            "attempted_models": [],
            "provider_meta": {"cli_path": cli_path},
            "effective_model": "",
            "fallback_used": False,
            "raw_json_fallback_used": False,
            "ok": False,
            "error": f"codex_cli_provider import failed: {e}",
            "usage": {},
            "suppressed": False,
        }
        return None

    # Without this collapse, the retry budget multiplies:
    # ``call_codex_cli_structured``'s outer loop (output_retries+1) calls
    # ``call_codex_cli_plain``'s inner loop (retries+1) → 6 spawns/call at
    # the default (retries=1, output_retries=2), or ~18s of startup
    # overhead. Mirror the claude-cli adapter's contract: use ONE budget
    # equal to ``max(retries, output_retries)`` and pass it as
    # output_retries, with the impl's inner retries=0.
    _codex_budget = max(int(retries or 0), int(output_retries or 0) if output_retries is not None else 0)
    try:
        result = _impl(
            model=model, prompt=prompt, output_type=output_type,
            system_prompt=sys_str or None,
            provider_config=provider_config,
            timeout_sec=timeout_sec,
            retries=0,
            output_retries=_codex_budget,
        )
    except CodexCliNotInstalledError as e:
        _LAST_STRUCTURED_LLM_CALL.data = _codex_failure_meta(
            model, cli_path, f"codex-cli not installed: {e}",
            terminal=True, attempted=False,
        )
        _safe_record(
            provider="codex-cli", model=model, usage={}, ok=False,
            duration_ms=int((time.monotonic() - _t0) * 1000),
            retry_count=0, error=str(e)[:200],
            model_id=cfg.get("id") if isinstance(cfg, dict) else None,
        )
        return None
    except CodexCliNotAuthenticatedError as e:
        _LAST_STRUCTURED_LLM_CALL.data = _codex_failure_meta(
            model, cli_path, f"codex-cli not authenticated: {e}", terminal=True,
        )
        _safe_record(
            provider="codex-cli", model=model, usage={}, ok=False,
            duration_ms=int((time.monotonic() - _t0) * 1000),
            retry_count=0, error=str(e)[:200],
            model_id=cfg.get("id") if isinstance(cfg, dict) else None,
        )
        return None
    except CodexCliQuotaExceededError as e:
        # Codex usage exhausted — terminal. Mark the meta with a
        # discriminator so callers (notably the AI backtest engine) can
        # tell quota exhaustion apart from other terminal failures and
        # take the appropriate action (Discord notify + abort job)
        # instead of just skipping the next strategy.
        _meta = _codex_failure_meta(
            model, cli_path, f"codex-cli quota exhausted: {e}", terminal=True,
        )
        _meta["error_kind"] = "quota_exhausted"
        _LAST_STRUCTURED_LLM_CALL.data = _meta
        _safe_record(
            provider="codex-cli", model=model, usage={}, ok=False,
            duration_ms=int((time.monotonic() - _t0) * 1000),
            retry_count=0, error=f"quota_exhausted: {str(e)[:180]}",
            model_id=cfg.get("id") if isinstance(cfg, dict) else None,
        )
        return None
    except CodexCliValidationError as e:
        _LAST_STRUCTURED_LLM_CALL.data = _codex_failure_meta(
            model, cli_path, f"codex-cli invalid extra_args: {e}",
            terminal=True, attempted=False,
        )
        _safe_record(
            provider="codex-cli", model=model, usage={}, ok=False,
            duration_ms=int((time.monotonic() - _t0) * 1000),
            retry_count=0, error=str(e)[:200],
            model_id=cfg.get("id") if isinstance(cfg, dict) else None,
        )
        return None
    except CodexCliError as e:
        _LAST_STRUCTURED_LLM_CALL.data = _codex_failure_meta(
            model, cli_path, str(e), terminal=False,
        )
        _safe_record(
            provider="codex-cli", model=model, usage={}, ok=False,
            duration_ms=int((time.monotonic() - _t0) * 1000),
            retry_count=int(retries or 0), error=str(e)[:200],
            model_id=cfg.get("id") if isinstance(cfg, dict) else None,
        )
        return None
    except Exception as e:
        _LAST_STRUCTURED_LLM_CALL.data = _codex_failure_meta(
            model, cli_path, f"unexpected error: {e}", terminal=False,
        )
        _safe_record(
            provider="codex-cli", model=model, usage={}, ok=False,
            duration_ms=int((time.monotonic() - _t0) * 1000),
            retry_count=int(retries or 0), error=str(e)[:200],
            model_id=cfg.get("id") if isinstance(cfg, dict) else None,
        )
        return None

    if result is None:
        # Pull the underlying reason from the provider's per-thread error
        # state — without this the operator sees only the generic "no
        # validated payload" string and has no way to diagnose whether
        # the failure was an HTTP 4xx from the Responses API, a JSON
        # validation miss, or an empty response.
        try:
            _impl_err = _impl_last_err() or ""
        except Exception:
            _impl_err = ""
        # Even failed validation rounds consumed tokens — include them
        # in the telemetry so the cost dashboard shows the real spend,
        # not a misleading $0.00 row.
        try:
            _codex_usage_fail = _impl_last_usage() or {}
        except Exception:
            _codex_usage_fail = {}
        _err_msg = (
            f"codex-cli returned no validated payload ({_impl_err})"
            if _impl_err else "codex-cli returned no validated payload"
        )
        _meta_fail = _codex_failure_meta(
            model, cli_path, _err_msg, terminal=False,
        )
        _meta_fail["usage"] = _codex_usage_fail
        _LAST_STRUCTURED_LLM_CALL.data = _meta_fail
        _safe_record(
            provider="codex-cli", model=model, usage=_codex_usage_fail, ok=False,
            duration_ms=int((time.monotonic() - _t0) * 1000),
            retry_count=int(retries or 0), error=_err_msg[:200],
            model_id=cfg.get("id") if isinstance(cfg, dict) else None,
        )
        return None

    # Pull the per-thread token usage the provider accumulated across
    # all Responses API calls in this structured turn (incl. output
    # retries). Empty dict if extraction failed — degrades gracefully
    # to the old "0 tokens" behaviour rather than breaking the call.
    try:
        _codex_usage = _impl_last_usage() or {}
    except Exception:
        _codex_usage = {}
    _LAST_STRUCTURED_LLM_CALL.data = {
        "provider": "codex-cli",
        "requested_model": model,
        "model_candidates": [model],
        "attempted_models": [model],
        "provider_meta": {"cli_path": cli_path},
        "effective_model": model,
        "fallback_used": False,
        # Codex's app-server has no native --json-schema flag, so we
        # always wrap the prompt with schema instructions and parse the
        # text response. That is the normal mode, not a fallback — keep
        # this False so dashboards joining on it don't mis-bucket every
        # codex row as "raw_json fallback used".
        "raw_json_fallback_used": False,
        "ok": True,
        "error": "",
        "usage": _codex_usage,
        "suppressed": False,
    }
    if use_prompt_cache and result is not None:
        try:
            if hasattr(result, "model_dump_json"):
                _raw_json = result.model_dump_json()
            elif hasattr(result, "dict"):
                _raw_json = json.dumps(result.dict())
            else:
                _raw_json = json.dumps(result)
            _store_prompt_cache(prompt, model, cache_effort, _raw_json, force_cache=True)
        except Exception:
            pass
    _safe_record(
        provider="codex-cli", model=model, usage=_codex_usage, ok=True,
        duration_ms=int((time.monotonic() - _t0) * 1000),
        retry_count=0, error=None,
        model_id=cfg.get("id") if isinstance(cfg, dict) else None,
    )
    return result


def _codex_failure_meta(
    model: str,
    cli_path: str,
    error: str,
    *,
    terminal: bool,
    attempted: bool = True,
) -> dict[str, Any]:
    """Shared structured-call failure metadata for the codex-cli path.

    ``attempted=False`` for pre-spawn rejections (NotInstalled, ValidationError,
    NotAuthenticated cache lookup) so dashboards don't falsely report an
    attempt that never reached the subprocess.
    """
    return {
        "provider": "codex-cli",
        "requested_model": model,
        "model_candidates": [model],
        "attempted_models": [model] if attempted else [],
        "provider_meta": {"cli_path": cli_path},
        "effective_model": "",
        "fallback_used": False,
        "raw_json_fallback_used": False,
        "ok": False,
        "error": error,
        "usage": {},
        "suppressed": bool(terminal),
        "is_terminal": bool(terminal),
    }


def _call_claude_cli_structured_from_strategy(
    *,
    model: str,
    prompt: str,
    output_type: Any,
    system_prompt: str | Sequence[str] | None = None,
    provider_config: dict[str, Any] | None = None,
    timeout_sec: int | None = None,
    retries: int = 1,
    output_retries: int | None = None,
    use_prompt_cache: bool = False,
) -> Any:
    """Adapter that lets the strategies' ``call_structured_llm_by_provider``
    contract run through the locally-installed ``claude`` CLI. Bypasses
    PydanticAI entirely — CC enforces structure via its native
    ``--json-schema`` flag and we re-validate with Pydantic on receipt.
    """
    _t0 = time.monotonic()
    if not model or output_type is None:
        return None

    # Normalise the system prompt to a single string (call_structured_llm_by_provider
    # historically accepts both a string and a sequence-of-strings shape).
    if isinstance(system_prompt, (list, tuple)):
        sys_str = "\n\n".join([s for s in system_prompt if s])
    else:
        sys_str = system_prompt or ""

    cfg = provider_config or {}
    cli_path = (cfg.get("cli_path") or "claude") or "claude"
    raw_extra = cfg.get("extra_args")
    if isinstance(raw_extra, str):
        try:
            from chatbot.claude_cli_provider import validate_extra_args
            extra_args = validate_extra_args(raw_extra)
        except Exception as e:
            _LAST_STRUCTURED_LLM_CALL.data = {
                "provider": "claude-cli",
                "requested_model": model,
                "model_candidates": [model],
                "attempted_models": [model],
                "provider_meta": {"cli_path": cli_path},
                "effective_model": "",
                "fallback_used": False,
                "raw_json_fallback_used": False,
                "ok": False,
                "error": f"invalid extra_args: {e}",
                "usage": {},
                "suppressed": False,
            }
            _safe_record(
                provider="claude-cli",
                model=model,
                usage={},
                ok=False,
                duration_ms=int((time.monotonic() - _t0) * 1000),
                retry_count=0,
                error=f"invalid extra_args: {str(e)[:200]}",
                model_id=cfg.get("id") if isinstance(cfg, dict) else None,
            )
            return None
    elif isinstance(raw_extra, (list, tuple)):
        extra_args = [str(x) for x in raw_extra]
    else:
        extra_args = []

    # ── Scoped prompt-cache lookup (mirror the main function's logic) ──
    cache_effort = ""
    if use_prompt_cache:
        cache_effort = str((provider_config or {}).get("reasoning_effort", "")).strip().lower()
        try:
            _cached_raw = _check_prompt_cache(prompt, model, cache_effort, force_cache=True)
        except Exception:
            _cached_raw = None
        if _cached_raw:
            try:
                _cached_obj = _validate_structured_output_from_raw_text(output_type, _cached_raw)
            except Exception:
                _cached_obj = None
            if _cached_obj is not None:
                _LAST_STRUCTURED_LLM_CALL.data = {
                    "provider": "claude-cli",
                    "requested_model": model,
                    "model_candidates": [model],
                    "attempted_models": [],
                    "provider_meta": {"cli_path": cli_path},
                    "effective_model": model,
                    "fallback_used": False,
                    "raw_json_fallback_used": False,
                    "ok": True,
                    "error": "",
                    "usage": {},
                    "suppressed": False,
                    "prompt_cache_hit": True,
                }
                _safe_record(
                    provider="claude-cli",
                    model=model,
                    usage={},
                    ok=True,
                    duration_ms=int((time.monotonic() - _t0) * 1000),
                    retry_count=0,
                    error=None,
                    model_id=cfg.get("id") if isinstance(cfg, dict) else None,
                )
                return _cached_obj

    from chatbot.claude_cli_provider import (
        call_claude_cli_structured,
        call_claude_cli_chat_structured,
        daemon_for_structured_enabled,
        _clear_structured_history,
        ClaudeCliError,
        ClaudeCliRateLimitError,
        ClaudeCliNotLoggedInError,
        ClaudeCliValidationError,
    )

    # Opt-in: when CLAUDE_CLI_DAEMON_FOR_STRUCTURED=1, route through the
    # long-lived chatbot subprocess. Each thread + (model, sys_hash) gets a
    # stable conversation_id so concurrent batch workers don't interleave on
    # one daemon and within-thread calls reuse the warm process. Default OFF
    # — the spawn-per-call path remains the production default until this
    # path is benchmarked.
    _use_daemon_path = daemon_for_structured_enabled()
    _conversation_id = ""
    if _use_daemon_path:
        _sys_hash = hashlib.sha256(
            (sys_str or "").encode("utf-8", errors="replace")
        ).hexdigest()[:12]
        # Bug #2 fix: include a schema fingerprint so two different output
        # schemas (e.g., company-classification vs macro-classification)
        # never collide on conversation_id. Without this, the daemon
        # subprocess keeps the first schema's system suffix forever
        # because _get_or_spawn deliberately ignores subsequent
        # system_prompt values when reusing a session.
        try:
            _schema_json = json.dumps(
                output_type.model_json_schema(), sort_keys=True
            )
        except Exception:
            _schema_json = f"{output_type.__module__}.{output_type.__qualname__}"
        _schema_hash = hashlib.sha256(
            _schema_json.encode("utf-8", errors="replace")
        ).hexdigest()[:12]
        _tid = threading.get_ident()
        _conversation_id = (
            f"nexus-structured-{model}-{_sys_hash}-schema{_schema_hash}-tid{_tid}"
        )

    last_err: Exception | None = None
    _attempted: list[str] = []
    # Honour the same retry contract as the rest of the function. The
    # dispatcher's ``output_retries`` is conceptually retries on Pydantic-
    # validation failure (vs ``retries`` on HTTP/connection failure). For
    # claude-cli the same retry loop covers BOTH classes, so use the max
    # of the two budgets — otherwise strategies that pass output_retries=2
    # silently get only 1 retry, masking validation failures behind too
    # few attempts (#632192 root cause).
    _retry_budget = max(int(retries or 1), int(output_retries or 0))
    for attempt in range(max(1, _retry_budget + 1)):
        _attempted.append(model)
        try:
            if _use_daemon_path:
                result = call_claude_cli_chat_structured(
                    conversation_id=_conversation_id,
                    model=model,
                    system_prompt=sys_str,
                    user_prompt=prompt or "",
                    output_schema=output_type,
                    cli_path=cli_path,
                    extra_args=extra_args,
                    reasoning_effort=cfg.get("reasoning_effort"),
                    timeout_sec=timeout_sec,
                )
            else:
                result = call_claude_cli_structured(
                    model=model,
                    system_prompt=sys_str,
                    user_prompt=prompt or "",
                    output_schema=output_type,
                    cli_path=cli_path,
                    extra_args=extra_args,
                    reasoning_effort=cfg.get("reasoning_effort"),
                    timeout_sec=timeout_sec,
                )
            # Pull the envelope's real usage block out of the claude-cli
            # provider's thread-local capture. Without this, every successful
            # structured call here would record an empty usage dict, and the
            # token-usage dashboard would always show zero for the project's
            # primary provider. The capture is set on success paths only;
            # falling back to {} for the rare race / shim case is safe.
            try:
                from chatbot.claude_cli_provider import get_last_struct_envelope_usage
                _envelope_usage = get_last_struct_envelope_usage()
            except Exception:
                _envelope_usage = {}
            _LAST_STRUCTURED_LLM_CALL.data = {
                "provider": "claude-cli",
                "requested_model": model,
                "model_candidates": [model],
                "attempted_models": _attempted,
                "provider_meta": {"cli_path": cli_path},
                "effective_model": model,
                "fallback_used": False,
                "raw_json_fallback_used": False,
                "ok": True,
                "error": "",
                "usage": _envelope_usage,
                "suppressed": False,
            }
            # Store in prompt cache if requested.
            if use_prompt_cache and result is not None:
                try:
                    if hasattr(result, "model_dump_json"):
                        raw_json = result.model_dump_json()
                    elif hasattr(result, "dict"):
                        raw_json = json.dumps(result.dict())
                    else:
                        raw_json = json.dumps(result)
                    _store_prompt_cache(prompt, model, cache_effort, raw_json, force_cache=True)
                except Exception:
                    pass
            try:
                _cc_usage = (
                    getattr(_LAST_STRUCTURED_LLM_CALL, "data", {}) or {}
                ).get("usage", {}) or {}
            except Exception:
                _cc_usage = {}
            _safe_record(
                provider="claude-cli",
                model=model,
                usage=_cc_usage,
                ok=True,
                duration_ms=int((time.monotonic() - _t0) * 1000),
                retry_count=max(0, len(_attempted) - 1),
                error=None,
                model_id=cfg.get("id") if isinstance(cfg, dict) else None,
            )
            return result
        except ClaudeCliNotLoggedInError as e:
            # Terminal — retrying won't help.
            last_err = e
            break
        except ClaudeCliRateLimitError as e:
            # Terminal for this attempt window. CC's subscription quota
            # resets at a fixed wall-clock time (often hours away), so
            # retrying immediately just burns more 429s. The Discord alert
            # was already emitted at the provider layer.
            last_err = e
            break
        except ClaudeCliValidationError as e:
            last_err = e
            if attempt < _retry_budget:
                _backoff = _backoff_sleep_seconds(attempt, base=2.0, cap=60.0)
                # Surface the retry in the log — without this, a successful
                # retry rolls up as ok=True and hides the wasted spawn time
                # from the per-day breakdown. Spawn #21 in backtest 419346
                # was the canonical case: 20s of failed spawn invisible.
                print(
                    f"[llm_utils] claude-cli retry {attempt + 1}/{_retry_budget} after "
                    f"validation error in {round(_backoff, 1)}s: {str(e)[:120]}",
                    file=sys.stderr, flush=True,
                )
                # Daemon subprocess may have died or returned garbage —
                # clear our cached history for this conversation_id so the
                # retry starts from a clean slate. The session manager
                # will respawn if needed.
                if _use_daemon_path and _conversation_id:
                    try:
                        _clear_structured_history(_conversation_id)
                    except Exception as _cleanup_err:
                        print(
                            f"[llm_utils] history cleanup failed (continuing retry): "
                            f"{_cleanup_err}",
                            file=sys.stderr, flush=True,
                        )
                time.sleep(_backoff)
                continue
            break
        except ClaudeCliError as e:
            last_err = e
            if attempt < _retry_budget:
                _backoff = _backoff_sleep_seconds(attempt)
                print(
                    f"[llm_utils] claude-cli retry {attempt + 1}/{_retry_budget} after "
                    f"transient error in {round(_backoff, 1)}s: {str(e)[:120]}",
                    file=sys.stderr, flush=True,
                )
                # Daemon subprocess may have died or returned garbage —
                # clear our cached history for this conversation_id so the
                # retry starts from a clean slate. The session manager
                # will respawn if needed.
                if _use_daemon_path and _conversation_id:
                    try:
                        _clear_structured_history(_conversation_id)
                    except Exception as _cleanup_err:
                        print(
                            f"[llm_utils] history cleanup failed (continuing retry): "
                            f"{_cleanup_err}",
                            file=sys.stderr, flush=True,
                        )
                time.sleep(_backoff)
                continue
            break
        except Exception as e:
            last_err = e
            break

    # ``ClaudeCliNotLoggedInError`` is permanent until the operator runs
    # ``claude`` on the host — surface it as terminal so callers that batch-
    # and-split prompts don't thrash through 14 sub-calls per article batch.
    # ``ClaudeCliValidationError`` after retry-budget exhaustion is also
    # terminal-for-this-shape: Pydantic validation is deterministic on the
    # prompt+schema combination, so splitting the batch into halves just
    # re-runs the same shape failure against sub-prompts. Marking it
    # terminal collapses worst-case chunk-split amplification (4 workers
    # × 4 leaves = 16 concurrent spawns) back to 1.
    # ``ClaudeCliRateLimitError`` is terminal-until-reset: CC's Pro/Max
    # subscription quota resets at a fixed wall-clock time, so chunk
    # splitting just creates more 429s. The provider layer already emits
    # a Discord notification once per reset window.
    _is_terminal_cc = isinstance(
        last_err,
        (
            ClaudeCliNotLoggedInError,
            ClaudeCliValidationError,
            ClaudeCliRateLimitError,
        ),
    )
    _LAST_STRUCTURED_LLM_CALL.data = {
        "provider": "claude-cli",
        "requested_model": model,
        "model_candidates": [model],
        "attempted_models": _attempted,
        "provider_meta": {"cli_path": cli_path},
        "effective_model": "",
        "fallback_used": False,
        "raw_json_fallback_used": False,
        "ok": False,
        "error": str(last_err or "claude-cli structured call failed"),
        "usage": {},
        "suppressed": False,
        "is_terminal": _is_terminal_cc,
    }
    _safe_record(
        provider="claude-cli",
        model=model,
        usage={},
        ok=False,
        duration_ms=int((time.monotonic() - _t0) * 1000),
        retry_count=max(0, len(_attempted) - 1),
        error=(str(last_err)[:200] if last_err else "claude-cli structured call failed"),
        model_id=cfg.get("id") if isinstance(cfg, dict) else None,
    )
    return None


def call_structured_llm_by_provider(
    provider: str,
    api_key: str,
    model: str,
    prompt: str,
    output_type: Any,
    *,
    system_prompt: str | Sequence[str] | None = None,
    tools: Sequence[Any] | None = None,
    max_output_tokens: int = 256,
    timeout_sec: int | None = None,
    retries: int = 1,
    output_retries: int | None = None,
    temperature: float = 0.2,
    provider_config: dict[str, Any] | None = None,
    prefer_raw_json: bool = False,
    http_retries: int = 2,
    use_prompt_cache: bool = False,
):
    """
    Call Gemini, DeepSeek, OpenAI, or Azure OpenAI through PydanticAI and return validated structured output.

    Returns the validated output object on success, or None on failure.

    When ``use_prompt_cache=True`` the function will check (and on success
    store) the prompt-hash cache via the scoped ``force_cache=True`` path,
    independent of the global ``_prompt_cache_enabled`` flag. Cache key is
    derived from ``prompt + model + reasoning_effort`` (system_prompt, tools
    and output_type schema are intentionally excluded — they are stable for
    a given role/model combination in the analyst-panel use case).
    """
    # ── claude-cli: bypass PydanticAI entirely (CC has no PydanticAI backend)
    # and use the locally-installed `claude` binary with --json-schema. No
    # api_key is required for this provider.
    if (provider or "").strip().lower() == "claude-cli":
        return _call_claude_cli_structured_from_strategy(
            model=model,
            prompt=prompt,
            output_type=output_type,
            system_prompt=system_prompt,
            provider_config=provider_config,
            timeout_sec=timeout_sec,
            retries=retries,
            output_retries=output_retries,
            use_prompt_cache=use_prompt_cache,
        )
    if (provider or "").strip().lower() == "codex-cli":
        return _call_codex_cli_structured_from_strategy(
            model=model,
            prompt=prompt,
            output_type=output_type,
            system_prompt=system_prompt,
            provider_config=provider_config,
            timeout_sec=timeout_sec,
            retries=retries,
            output_retries=output_retries,
            use_prompt_cache=use_prompt_cache,
        )
    if not _PYDANTIC_AI_AVAILABLE or not api_key or not model or output_type is None:
        return None
    # ── Scoped prompt-cache lookup ──
    _structured_cache_effort = ""
    if use_prompt_cache:
        _structured_cache_effort = str((provider_config or {}).get("reasoning_effort", "")).strip().lower()
        try:
            _cached_raw = _check_prompt_cache(prompt, model, _structured_cache_effort, force_cache=True)
        except Exception:
            _cached_raw = None
        if _cached_raw:
            try:
                _cached_obj = _validate_structured_output_from_raw_text(output_type, _cached_raw)
            except Exception:
                _cached_obj = None
            if _cached_obj is not None:
                _LAST_STRUCTURED_LLM_CALL.data = {
                    "provider": (provider or "").strip().lower(),
                    "requested_model": _structured_model_name(provider, model),
                    "model_candidates": _structured_model_candidates(provider, model),
                    "attempted_models": [],
                    "provider_meta": _safe_provider_meta(provider, provider_config),
                    "effective_model": model,
                    "fallback_used": False,
                    "raw_json_fallback_used": False,
                    "ok": True,
                    "error": "",
                    "usage": {},
                    "suppressed": False,
                    "prompt_cache_hit": True,
                }
                return _cached_obj

    def _maybe_store_structured_in_cache(structured_obj: Any) -> None:
        if not use_prompt_cache or structured_obj is None:
            return
        try:
            if hasattr(structured_obj, "model_dump_json"):
                _raw_json = structured_obj.model_dump_json()
            elif hasattr(structured_obj, "dict"):
                _raw_json = json.dumps(structured_obj.dict())
            else:
                _raw_json = json.dumps(structured_obj)
            _stores_before = int(_prompt_cache_stats.get("stores", 0) or 0)
            _store_prompt_cache(prompt, model, _structured_cache_effort, _raw_json, force_cache=True)
            _stores_after = int(_prompt_cache_stats.get("stores", 0) or 0)
            if _stores_after > _stores_before:
                _LAST_STRUCTURED_LLM_CALL.data["prompt_cache_stored"] = True
            else:
                _LAST_STRUCTURED_LLM_CALL.data["prompt_cache_stored"] = False
        except Exception as _store_exc:
            try:
                _LAST_STRUCTURED_LLM_CALL.data["prompt_cache_stored"] = False
                _LAST_STRUCTURED_LLM_CALL.data["prompt_cache_store_error"] = str(_store_exc)[:200]
            except Exception:
                pass
    failure_key = _terminal_llm_failure_cache_key(provider, model, provider_config)
    with _TERMINAL_LLM_FAILURES_LOCK:
        cached_failure = _TERMINAL_LLM_FAILURES.get(failure_key)
    if cached_failure:
        _LAST_STRUCTURED_LLM_CALL.data = {
            "provider": (provider or "").strip().lower(),
            "requested_model": _structured_model_name(provider, model),
            "model_candidates": _structured_model_candidates(provider, model),
            "attempted_models": [],
            "provider_meta": _safe_provider_meta(provider, provider_config),
            "effective_model": "",
            "fallback_used": False,
            "raw_json_fallback_used": False,
            "ok": False,
            "error": cached_failure,
            "usage": {},
            "suppressed": True,
            # A cached failure is by definition a previously-classified
            # terminal error. Surface it on the same flag callers check.
            "is_terminal": True,
        }
        return None
    timeout = float(_coerce_timeout_sec(timeout_sec))
    settings = _build_structured_model_settings(provider, max_output_tokens, timeout, temperature, model=model)
    model_candidates = _structured_model_candidates(provider, model)
    provider_lock = _STRUCTURED_LLM_PROVIDER_LOCKS.get((provider or "").strip().lower())
    last_exc = None
    _LAST_STRUCTURED_LLM_CALL.data = {
        "provider": (provider or "").strip().lower(),
        "requested_model": _structured_model_name(provider, model),
        "model_candidates": list(model_candidates),
        "attempted_models": [],
        "provider_meta": _safe_provider_meta(provider, provider_config),
        "effective_model": "",
        "fallback_used": False,
        "raw_json_fallback_used": False,
        "ok": False,
        "error": "",
        "usage": {},
        "suppressed": False,
    }
    for idx, structured_model in enumerate(model_candidates):
        http_attempt = 0
        is_terminal_not_found = False
        while True:  # inner retry loop for transient HTTP errors
            try:
                if http_attempt == 0:
                    _LAST_STRUCTURED_LLM_CALL.data["attempted_models"].append(structured_model)
                _model_forces_raw = (
                    (provider or "").strip().lower() in {"azure", "openai", "nvidia"}
                    and _model_skips_json_object_format(structured_model)
                )
                force_raw_json = prefer_raw_json or _model_forces_raw
                if force_raw_json:
                    _raw_retries = min(2, max(1, int(output_retries or 1)))  # up to 2 retries (3 total attempts) then fall through to split
                    _raw_last_exc = None
                    for _raw_attempt in range(_raw_retries + 1):
                        try:
                            repaired = _try_raw_structured_json_once(
                                provider,
                                api_key,
                                structured_model,
                                prompt,
                                output_type,
                                max_output_tokens=max_output_tokens,
                                timeout_sec=timeout_sec,
                                provider_config=provider_config,
                                system_prompt=system_prompt if isinstance(system_prompt, str) else (
                                    "\n".join(system_prompt) if isinstance(system_prompt, (list, tuple)) else None
                                ),
                            )
                            if repaired is not None:
                                _LAST_STRUCTURED_LLM_CALL.data.update({
                                    "effective_model": structured_model,
                                    "fallback_used": idx > 0,
                                    "raw_json_fallback_used": True,
                                    "ok": True,
                                    "error": "",
                                    "usage": {},
                                })
                                if _raw_attempt > 0:
                                    import sys
                                    print(
                                        f"[llm_utils] Structured LLM raw JSON preferred path succeeded on retry {_raw_attempt} for provider={provider!r}, model={structured_model!r}.",
                                        file=sys.stderr,
                                        flush=True,
                                    )
                                _maybe_store_structured_in_cache(repaired)
                                return repaired
                        except Exception as raw_pref_exc:
                            _raw_last_exc = raw_pref_exc
                            # Non-retryable filter / temp-block check FIRST — retrying
                            # these patterns is exactly what triggers Azure's
                            # abuse-monitor temp-block (see _is_non_retryable_filter_response).
                            _nr_filter, _nr_tag = _is_non_retryable_filter_response(exc=raw_pref_exc)
                            if _nr_filter:
                                import sys
                                print(
                                    f"[llm_utils] Raw JSON: non-retryable response ({_nr_tag}) for provider={provider!r}, model={structured_model!r}; skipping further retries to avoid Azure abuse-monitor cascade.",
                                    file=sys.stderr, flush=True,
                                )
                                break
                            # Terminal errors that won't resolve with retries — stop immediately
                            _exc_str = str(raw_pref_exc)
                            _is_terminal = any(kw in _exc_str for kw in [
                                "content filter policy", "DeploymentError", "DeploymentNotFound",
                                "model_not_found", "The model", "does not exist",
                            ])
                            if _is_terminal:
                                import sys
                                print(
                                    f"[llm_utils] TERMINAL error (no retry): provider={provider!r}, model={structured_model!r}: {_exc_str[:200]}",
                                    file=sys.stderr, flush=True,
                                )
                                break
                            if _raw_attempt < _raw_retries:
                                import sys
                                print(
                                    f"[llm_utils] Raw JSON attempt {_raw_attempt + 1}/{_raw_retries + 1} failed for provider={provider!r}, model={structured_model!r}: {raw_pref_exc!s:.200s} — retrying...",
                                    file=sys.stderr,
                                    flush=True,
                                )
                                time.sleep(_backoff_sleep_seconds(_raw_attempt, base=1.0, cap=5.0))
                                continue
                    if _raw_last_exc is not None:
                        _LAST_STRUCTURED_LLM_CALL.data["error"] = f"raw_json_preferred={_raw_last_exc}"
                    if _model_forces_raw:
                        # Model cannot use PydanticAI (rejects parallel_tool_calls, temperature, etc.) — skip to next candidate
                        break
                model_obj = _build_pydantic_ai_model(provider, api_key, structured_model, provider_config=provider_config)
                if model_obj is None:
                    break
                agent = Agent(
                    model_obj,
                    output_type=output_type,
                    system_prompt=system_prompt or (),
                    model_settings=settings,
                    retries=max(1, int(retries or 1)),
                    output_retries=max(0, int(output_retries if output_retries is not None else retries or 1)),
                    tools=tuple(tools or ()),
                    defer_model_check=True,
                    instrument=False,
                )
                _rate_limiter = _get_model_rate_limiter(structured_model)
                if _rate_limiter is not None:
                    system_text = system_prompt if isinstance(system_prompt, str) else " ".join(str(s) for s in (system_prompt or []))
                    _estimated_tokens = (len(prompt) + len(system_text)) // 4 + max(256, max_output_tokens or 256)
                    _rl_waited = _rate_limiter.acquire(_estimated_tokens)
                    if _rl_waited > 0:
                        import sys
                        print(
                            f"[llm_utils] Rate limiter: waited {_rl_waited:.1f}s before structured call to {structured_model!r}",
                            file=sys.stderr,
                            flush=True,
                        )
                # Proactive RPM throttle for providers with a hard
                # requests-per-minute cap (NVIDIA NIM kimi-k2.x).
                # Pass provider so the limiter only fires for the
                # exact (provider, model) pair — Azure/OpenAI/etc.
                # paths bypass it even if a model name collides.
                _rpm_limiter = _get_model_request_rate_limiter(structured_model, provider)
                if _rpm_limiter is not None:
                    _rpm_waited = _rpm_limiter.acquire()
                    if _rpm_waited > 0:
                        import sys
                        print(
                            f"[llm_utils] RPM throttle: waited {_rpm_waited:.1f}s before structured call to {provider}/{structured_model!r}",
                            file=sys.stderr,
                            flush=True,
                        )
                if provider_lock is not None:
                    with provider_lock:
                        result = agent.run_sync(prompt, infer_name=False)
                else:
                    result = agent.run_sync(prompt, infer_name=False)
                usage_data = _structured_run_usage_dict(result)
                _LAST_STRUCTURED_LLM_CALL.data.update({
                    "effective_model": structured_model,
                    "fallback_used": idx > 0,
                    "raw_json_fallback_used": False,
                    "ok": True,
                    "error": "",
                    "usage": usage_data,
                })
                if idx > 0:
                    import sys
                    print(
                        f"[llm_utils] Structured LLM fallback succeeded with {structured_model!r} after {model_candidates[0]!r} failed.",
                        file=sys.stderr,
                        flush=True,
                    )
                _maybe_store_structured_in_cache(result.output)
                return result.output
            except Exception as exc:
                error_text = str(exc)
                is_terminal_not_found = _is_terminal_provider_not_found(provider, exc)
                if is_terminal_not_found:
                    hint = _terminal_provider_not_found_hint(provider, structured_model, provider_config)
                    if hint:
                        error_text = f"{error_text} {hint}".strip()
                    with _TERMINAL_LLM_FAILURES_LOCK:
                        _TERMINAL_LLM_FAILURES[failure_key] = error_text
                    last_exc = RuntimeError(error_text)
                    _LAST_STRUCTURED_LLM_CALL.data.update({
                        "effective_model": structured_model,
                        "fallback_used": idx > 0,
                        "raw_json_fallback_used": False,
                        "ok": False,
                        "error": error_text,
                        "usage": {},
                        # Surface the terminal-error signal so batched
                        # callers (chunk-fallback, retry loops) can stop
                        # splitting/retrying on a permanent 404 instead
                        # of hammering the API hundreds of times per cycle.
                        "is_terminal": True,
                    })
                    break
                if _is_transient_http_error(error_text) and http_attempt < http_retries:
                    wait = _http_retry_backoff_seconds(error_text, http_attempt)
                    import sys
                    print(
                        f"[llm_utils] Transient HTTP error for provider={provider!r}, model={structured_model!r}: "
                        f"{error_text[:120].strip()} — retrying in {wait:.1f}s (attempt {http_attempt + 1}/{http_retries})...",
                        file=sys.stderr,
                        flush=True,
                    )
                    time.sleep(wait)
                    http_attempt += 1
                    continue
                last_exc = exc
                _LAST_STRUCTURED_LLM_CALL.data.update({
                    "effective_model": structured_model,
                    "fallback_used": idx > 0,
                    "raw_json_fallback_used": False,
                    "ok": False,
                    "error": error_text,
                    "usage": {},
                })
                if _structured_json_retry_enabled(error_text):
                    try:
                        repaired = _try_raw_structured_json_once(
                            provider,
                            api_key,
                            structured_model,
                            prompt,
                            output_type,
                            max_output_tokens=max_output_tokens,
                            timeout_sec=timeout_sec,
                            provider_config=provider_config,
                        )
                        if repaired is not None:
                            _LAST_STRUCTURED_LLM_CALL.data.update({
                                "effective_model": structured_model,
                                "fallback_used": idx > 0,
                                "raw_json_fallback_used": True,
                                "ok": True,
                                "error": "",
                                "usage": {},
                            })
                            import sys
                            print(
                                f"[llm_utils] Structured LLM raw JSON fallback succeeded for provider={provider!r}, model={structured_model!r}.",
                                file=sys.stderr,
                                flush=True,
                            )
                            _maybe_store_structured_in_cache(repaired)
                            return repaired
                    except Exception as raw_exc:
                        _LAST_STRUCTURED_LLM_CALL.data["error"] = f"{error_text} | raw_json_fallback={raw_exc}"
                        import sys
                        print(
                            f"[llm_utils] Structured LLM raw JSON fallback failed for provider={provider!r}, model={structured_model!r}: {raw_exc}",
                            file=sys.stderr,
                            flush=True,
                        )
                if idx + 1 < len(model_candidates):
                    import sys
                    print(
                        f"[llm_utils] Structured LLM call failed for provider={provider!r}, model={structured_model!r}: {error_text}. "
                        f"Retrying with fallback {model_candidates[idx + 1]!r}.",
                        file=sys.stderr,
                        flush=True,
                    )
                break  # exit inner while, advance outer for to next model candidate
        if is_terminal_not_found:
            break  # exit outer for loop
    if last_exc is not None:
        import sys
        print(
            f"[llm_utils] Structured LLM call failed for provider={provider!r}, model={model!r}: {last_exc}",
            file=sys.stderr,
            flush=True,
        )
    return None


def invalidate_terminal_failure_cache(provider: str | None = None, model: str | None = None) -> int:
    """Drop entries from the per-process terminal-failure cache.

    The cache (``_TERMINAL_LLM_FAILURES``) remembers provider+model combos
    that returned a permanent error (404 model-not-found, etc.) so we
    don't re-call them. WITHOUT explicit invalidation, the cache outlives
    a user fixing the offending Model row in the UI — every call would
    stay suppressed until the worker process restarts.

    Call this whenever a Model row is edited or deleted. Without args,
    drops everything. With args, drops entries whose key starts with the
    matching provider/model prefix (failure keys include other metadata,
    so we match on prefix rather than equality).

    Returns the number of entries removed.
    """
    with _TERMINAL_LLM_FAILURES_LOCK:
        if not provider and not model:
            count = len(_TERMINAL_LLM_FAILURES)
            _TERMINAL_LLM_FAILURES.clear()
            return count
        provider_norm = (provider or "").strip().lower()
        model_norm = (model or "").strip()
        to_drop = []
        for key in _TERMINAL_LLM_FAILURES:
            # Failure keys are produced by ``_terminal_llm_failure_cache_key``
            # which embeds provider + model + provider_config. Match on
            # substring presence rather than parsing the format so this
            # stays robust to key-format changes.
            if provider_norm and provider_norm not in key.lower():
                continue
            if model_norm and model_norm.lower() not in key.lower():
                continue
            to_drop.append(key)
        for key in to_drop:
            _TERMINAL_LLM_FAILURES.pop(key, None)
        return len(to_drop)


def get_last_structured_llm_call_metadata() -> dict[str, Any]:
    """Return metadata for the most recent structured LLM call on this thread."""
    data = getattr(_LAST_STRUCTURED_LLM_CALL, "data", None)
    return dict(data or {})


def get_last_plain_llm_call_error() -> str:
    """Return the per-thread error string from the most recent
    ``call_llm_by_provider`` invocation that returned empty text. Useful
    for surfacing the underlying provider reason ("Responses API HTTP
    400", "rate limited", etc.) in callers that only see an empty
    string back."""
    return getattr(_LAST_PLAIN_LLM_CALL_ERROR, "error", "") or ""


def _extract_chat_message_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                text = str(item.get("text") or "").strip()
                if text:
                    parts.append(text)
        return "\n".join(parts).strip()
    return ""


def google_cse_search(
    query: str,
    *,
    api_key: str | None = None,
    search_engine_id: str | None = None,
    num_results: int = 5,
    fetch_result_excerpt: bool = False,
    timeout_sec: int | None = None,
) -> list[dict[str, Any]]:
    """
    Query Google Programmable Search / Custom Search and return lightweight evidence records.

    Result shape:
      [{"title": "...", "snippet": "...", "url": "...", "display_link": "...", "excerpt": "..."}]
    """
    q = (query or "").strip()
    if not q:
        return []
    api_key = (api_key or os.environ.get("GOOGLE_SEARCH_API_KEY") or "").strip()
    search_engine_id = (search_engine_id or os.environ.get("GOOGLE_SEARCH_ENGINE_ID") or "").strip()
    if not api_key or not search_engine_id:
        return []
    timeout = float(_coerce_timeout_sec(timeout_sec))
    try:
        import requests
    except Exception:
        return []

    max_results = max(1, min(int(num_results or 5), 10))
    headers = {
        "Accept": "application/json",
        "User-Agent": "IntelliStockV4/GraphNexus GoogleSearchTool",
    }
    try:
        resp = requests.get(
            "https://www.googleapis.com/customsearch/v1",
            params={
                "key": api_key,
                "cx": search_engine_id,
                "q": q,
                "num": max_results,
            },
            headers=headers,
            timeout=timeout,
        )
        resp.raise_for_status()
        payload = resp.json() or {}
    except Exception:
        return []

    results: list[dict[str, Any]] = []
    for item in (payload.get("items") or []):
        url = str(item.get("link") or "").strip()
        if not url:
            continue
        record: dict[str, Any] = {
            "title": unescape(str(item.get("title") or "").strip()),
            "snippet": unescape(str(item.get("snippet") or "").strip()),
            "url": url,
            "display_link": str(item.get("displayLink") or "").strip(),
        }
        if fetch_result_excerpt:
            try:
                page = requests.get(url, headers=headers, timeout=min(timeout, 12.0))
                if page.ok and page.text:
                    excerpt = re.sub(r"\s+", " ", unescape(page.text))
                    if excerpt:
                        record["excerpt"] = excerpt[:1200]
            except Exception:
                pass
        results.append(record)
    return results


def _log_token_usage(provider: str, model: str, data: dict) -> None:
    """Log input/output token counts from LLM API response."""
    try:
        # OpenAI / Azure / DeepSeek format
        usage = data.get("usage")
        if usage and isinstance(usage, dict):
            inp = usage.get("prompt_tokens", 0)
            out = usage.get("completion_tokens", 0)
            total = usage.get("total_tokens", inp + out)
            # Check for reasoning tokens (reasoning models like o1, gpt-oss)
            details = usage.get("completion_tokens_details") or {}
            reasoning = details.get("reasoning_tokens", 0)
            extra = f" reasoning={reasoning}" if reasoning else ""
            print(f"[llm_utils] Tokens: provider={provider} model={model} input={inp} output={out}{extra} total={total}", flush=True)
            return
        # Gemini format
        usage = data.get("usageMetadata")
        if usage and isinstance(usage, dict):
            inp = usage.get("promptTokenCount", 0)
            out = usage.get("candidatesTokenCount", 0)
            total = usage.get("totalTokenCount", inp + out)
            thinking = usage.get("thoughtsTokenCount", 0)
            extra = f" thinking={thinking}" if thinking else ""
            print(f"[llm_utils] Tokens: provider={provider} model={model} input={inp} output={out}{extra} total={total}", flush=True)
            return
    except Exception:
        pass


def _call_gemini(
    api_key: str,
    model: str,
    prompt: str,
    max_output_tokens: int = 256,
    timeout_sec: int | None = None,
    retries: int = 0,
    response_mime_type: str | None = None,
) -> str:
    """Call Gemini generateContent REST API. Returns response text or empty string."""
    _t0 = time.monotonic()
    if not api_key:
        return ""
    url = f"{GEMINI_BASE}/models/{model}:generateContent"
    headers = {"Content-Type": "application/json"}
    params = {"key": api_key}
    gen_cfg: dict = {"temperature": 0.2}
    if max_output_tokens and max_output_tokens > 0:
        gen_cfg["maxOutputTokens"] = max_output_tokens
    if response_mime_type:
        gen_cfg["responseMimeType"] = response_mime_type
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": gen_cfg,
    }
    try:
        import requests

        timeout = _coerce_timeout_sec(timeout_sec)
        max_retries = max(0, int(retries or 0))
        retriable_status = {429, 500, 502, 503, 504}

        for attempt in range(max_retries + 1):
            # If we retry, give the model more time (up to 2x the user-provided timeout).
            attempt_timeout = timeout if attempt == 0 else timeout * 2

            try:
                connect_timeout = min(15, attempt_timeout)
                r = requests.post(
                    url,
                    headers=headers,
                    params=params,
                    json=body,
                    timeout=(connect_timeout, attempt_timeout),
                )
            except requests.exceptions.Timeout as _to_e:
                if attempt < max_retries:
                    time.sleep(_backoff_sleep_seconds(attempt))
                    continue
                import sys
                print(
                    f"[llm_utils] Gemini timeout after {attempt_timeout}s (model={model!r}). "
                    f"Set LLM_REQUEST_TIMEOUT to increase.",
                    file=sys.stderr, flush=True,
                )
                # T10 critical-guard capture
                try:
                    _stash_last_http(status=None, body=f"timeout after {attempt_timeout}s", exc=_to_e)
                except Exception:
                    pass
                _safe_record(
                    provider="gemini", model=model, usage={}, ok=False,
                    duration_ms=int((time.monotonic() - _t0) * 1000),
                    retry_count=attempt,
                    error=f"timeout after {attempt_timeout}s", model_id=None,
                )
                return ""
            except requests.exceptions.RequestException as _req_e:
                if attempt < max_retries:
                    time.sleep(_backoff_sleep_seconds(attempt))
                    continue
                # T10 critical-guard capture
                try:
                    _resp = getattr(_req_e, "response", None)
                    _stash_last_http(
                        status=getattr(_resp, "status_code", None),
                        body=(getattr(_resp, "text", "") if _resp is not None else str(_req_e))[:1000],
                        exc=_req_e,
                    )
                except Exception:
                    pass
                _safe_record(
                    provider="gemini", model=model, usage={}, ok=False,
                    duration_ms=int((time.monotonic() - _t0) * 1000),
                    retry_count=attempt, error=str(_req_e)[:200], model_id=None,
                )
                return ""

            status = getattr(r, "status_code", None)
            if status and status >= 400:
                # Extract error message (best-effort)
                err_msg = ""
                try:
                    err_msg = ((r.json() or {}).get("error") or {}).get("message") or ""
                except Exception:
                    err_msg = getattr(r, "text", "") or ""
                err_msg = _truncate(err_msg.strip(), 300)

                # Non-retryable filter check — see _is_non_retryable_filter_response.
                # Gemini's safety filter shape is different (no "content_filter"
                # string; uses "BLOCKED_REASON_SAFETY"), so this primarily defends
                # against pathological wrapped errors. Cheap to leave in place.
                _nr_filter, _nr_tag = _is_non_retryable_filter_response(
                    status=status, body=(getattr(r, "text", "") or err_msg or ""),
                )
                if _nr_filter:
                    import sys
                    print(
                        f"[llm_utils] Gemini {model!r}: non-retryable response ({_nr_tag}) at status {status}; skipping further retries.",
                        file=sys.stderr, flush=True,
                    )
                elif status in retriable_status and attempt < max_retries:
                    wait = _retry_after_seconds(getattr(r, "headers", None)) or _http_retry_backoff_seconds(f"status_code: {status}", attempt)
                    import sys
                    print(
                        f"[llm_utils] Gemini HTTP {status} (model={model!r}): {err_msg} "
                        f"Retrying in {wait:.1f}s...",
                        file=sys.stderr, flush=True,
                    )
                    time.sleep(wait)
                    continue

                # Non-retriable (or retries exhausted)
                import sys
                if err_msg:
                    print(
                        f"[llm_utils] Gemini HTTP {status} (model={model!r}): {err_msg}",
                        file=sys.stderr, flush=True,
                    )
                # T10 critical-guard capture
                try:
                    _stash_last_http(
                        status=status,
                        body=(getattr(r, "text", "") or err_msg or "")[:1000],
                        exc=None,
                    )
                except Exception:
                    pass
                _safe_record(
                    provider="gemini", model=model, usage={}, ok=False,
                    duration_ms=int((time.monotonic() - _t0) * 1000),
                    retry_count=attempt,
                    error=(f"HTTP {status}: {err_msg}"[:200] if err_msg else f"HTTP {status}"),
                    model_id=None,
                )
                return ""

            try:
                data = r.json()
            except Exception as _json_e:
                if attempt < max_retries:
                    time.sleep(_backoff_sleep_seconds(attempt))
                    continue
                _safe_record(
                    provider="gemini", model=model, usage={}, ok=False,
                    duration_ms=int((time.monotonic() - _t0) * 1000),
                    retry_count=attempt, error=f"json parse: {str(_json_e)[:180]}",
                    model_id=None,
                )
                return ""

            _log_token_usage("gemini", model, data)
            candidates = data.get("candidates") or []
            if not candidates:
                if attempt < max_retries:
                    time.sleep(_backoff_sleep_seconds(attempt))
                    continue
                _safe_record(
                    provider="gemini", model=model, usage={}, ok=False,
                    duration_ms=int((time.monotonic() - _t0) * 1000),
                    retry_count=attempt, error="no candidates", model_id=None,
                )
                return ""

            parts = (candidates[0].get("content") or {}).get("parts") or []
            if not parts:
                if attempt < max_retries:
                    time.sleep(_backoff_sleep_seconds(attempt))
                    continue
                _safe_record(
                    provider="gemini", model=model, usage={}, ok=False,
                    duration_ms=int((time.monotonic() - _t0) * 1000),
                    retry_count=attempt, error="no parts", model_id=None,
                )
                return ""

            text_parts = [(p.get("text") or "") for p in parts if isinstance(p, dict)]
            out = (" ".join(t for t in text_parts if t)).strip()
            if out:
                # T10 critical-guard capture (success)
                try:
                    _stash_last_http(status=200, body=None, exc=None)
                except Exception:
                    pass
                _gem_usage = data.get("usageMetadata") if isinstance(data, dict) else None
                _u = {}
                if isinstance(_gem_usage, dict):
                    _u = {
                        "input_tokens": int(_gem_usage.get("promptTokenCount", 0) or 0),
                        "output_tokens": int(_gem_usage.get("candidatesTokenCount", 0) or 0),
                        "reasoning_tokens": int(_gem_usage.get("thoughtsTokenCount", 0) or 0),
                    }
                _safe_record(
                    provider="gemini",
                    model=model,
                    usage=_u,
                    ok=True,
                    duration_ms=int((time.monotonic() - _t0) * 1000),
                    retry_count=attempt,
                    error=None,
                    model_id=None,
                )
                return out

            if attempt < max_retries:
                time.sleep(_backoff_sleep_seconds(attempt))
                continue
            _safe_record(
                provider="gemini", model=model, usage={}, ok=False,
                duration_ms=int((time.monotonic() - _t0) * 1000),
                retry_count=attempt, error="empty response", model_id=None,
            )
            return ""

        _safe_record(
            provider="gemini", model=model, usage={}, ok=False,
            duration_ms=int((time.monotonic() - _t0) * 1000),
            retry_count=max_retries, error="retries exhausted", model_id=None,
        )
        return ""
    except Exception as _e:
        import sys
        # T10 critical-guard capture — only stash if no inner handler did.
        try:
            tid = threading.get_ident()
            with _LAST_HTTP_LOCK:
                _already = tid in _LAST_HTTP_PER_THREAD
            if not _already:
                _resp = getattr(_e, "response", None)
                _stash_last_http(
                    status=getattr(_resp, "status_code", None),
                    body=(getattr(_resp, "text", "") if _resp is not None else str(_e))[:1000],
                    exc=_e,
                )
        except Exception:
            pass
        # Timeouts are common for long prompts; make them visible for easier debugging.
        try:
            import requests  # type: ignore

            if isinstance(_e, requests.exceptions.Timeout):
                timeout = _coerce_timeout_sec(timeout_sec)
                print(f"[llm_utils] Gemini timeout after {timeout}s (model={model!r}). Set LLM_REQUEST_TIMEOUT to increase.", file=sys.stderr, flush=True)
                _safe_record(
                    provider="gemini", model=model, usage={}, ok=False,
                    duration_ms=int((time.monotonic() - _t0) * 1000),
                    retry_count=0, error=f"timeout after {timeout}s", model_id=None,
                )
                return ""
        except Exception:
            pass
        if hasattr(_e, "response") and _e.response is not None:
            try:
                err_msg = _e.response.json().get("error", {}).get("message", "")[:300]
            except Exception:
                err_msg = (_e.response.text or "")[:300]
            print(
                f"[llm_utils] Gemini HTTP {_e.response.status_code} (model={model!r}): {err_msg}",
                file=sys.stderr, flush=True,
            )
        _safe_record(
            provider="gemini", model=model, usage={}, ok=False,
            duration_ms=int((time.monotonic() - _t0) * 1000),
            retry_count=0, error=str(_e)[:200], model_id=None,
        )
        return ""


def _call_deepseek(
    api_key: str,
    model: str,
    prompt: str,
    max_output_tokens: int = 256,
    timeout_sec: int | None = None,
    retries: int = 0,
) -> str:
    """Call DeepSeek chat/completions API. Returns response text or empty string.
    For deepseek-reasoner: uses message.content (final answer); if empty, falls back to reasoning_content."""
    _t0 = time.monotonic()
    if not api_key:
        return ""

    def _ds_usage_from_data(data: dict) -> dict:
        u = data.get("usage") if isinstance(data, dict) else None
        if not isinstance(u, dict):
            return {}
        details = u.get("completion_tokens_details") or {}
        prompt_details = u.get("prompt_tokens_details") or {}
        return {
            "input_tokens": int(u.get("prompt_tokens", 0) or 0),
            "output_tokens": int(u.get("completion_tokens", 0) or 0),
            "reasoning_tokens": int((details or {}).get("reasoning_tokens", 0) or 0),
            "cache_read_input_tokens": int(
                (prompt_details or {}).get("cached_tokens", 0) or 0
            ),
        }

    try:
        import requests
        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {"Content-Type": "application/json", "Authorization": "Bearer %s" % api_key}
        body: dict = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
        }
        if max_output_tokens and max_output_tokens > 0:
            body["max_tokens"] = max_output_tokens
        timeout = _coerce_timeout_sec(timeout_sec)
        max_retries = max(0, int(retries or 0))
        retriable_status = {429, 500, 502, 503, 504}

        for attempt in range(max_retries + 1):
            attempt_timeout = timeout if attempt == 0 else timeout * 2
            connect_timeout = min(15, attempt_timeout)
            try:
                r = requests.post(url, headers=headers, json=body, timeout=(connect_timeout, attempt_timeout))
            except requests.exceptions.Timeout as _to_e:
                if attempt < max_retries:
                    time.sleep(_backoff_sleep_seconds(attempt))
                    continue
                # T10 critical-guard capture
                try:
                    _stash_last_http(status=None, body=f"timeout after {attempt_timeout}s", exc=_to_e)
                except Exception:
                    pass
                _safe_record(
                    provider="deepseek", model=model, usage={}, ok=False,
                    duration_ms=int((time.monotonic() - _t0) * 1000),
                    retry_count=attempt, error="timeout", model_id=None,
                )
                return ""
            except requests.exceptions.RequestException as _req_e:
                if attempt < max_retries:
                    time.sleep(_backoff_sleep_seconds(attempt))
                    continue
                # T10 critical-guard capture
                try:
                    _resp = getattr(_req_e, "response", None)
                    _stash_last_http(
                        status=getattr(_resp, "status_code", None),
                        body=(getattr(_resp, "text", "") if _resp is not None else str(_req_e))[:1000],
                        exc=_req_e,
                    )
                except Exception:
                    pass
                _safe_record(
                    provider="deepseek", model=model, usage={}, ok=False,
                    duration_ms=int((time.monotonic() - _t0) * 1000),
                    retry_count=attempt, error=str(_req_e)[:200], model_id=None,
                )
                return ""

            if r.status_code in retriable_status and attempt < max_retries:
                # Non-retryable filter check — see _is_non_retryable_filter_response.
                _nr_filter, _nr_tag = _is_non_retryable_filter_response(
                    status=r.status_code, body=getattr(r, "text", "") or "",
                )
                if _nr_filter:
                    import sys
                    print(
                        f"[llm_utils] DeepSeek {model!r}: non-retryable response ({_nr_tag}) at status {r.status_code}; skipping further retries.",
                        file=sys.stderr, flush=True,
                    )
                else:
                    wait = _retry_after_seconds(getattr(r, "headers", None)) or _http_retry_backoff_seconds(f"status_code: {r.status_code}", attempt)
                    time.sleep(wait)
                    continue

            if r.status_code >= 400:
                try:
                    _err_body = r.json()
                except Exception:
                    _err_body = r.text[:500]
                # T10 critical-guard capture (stash BEFORE raising)
                try:
                    _stash_last_http(
                        status=r.status_code,
                        body=(r.text or "")[:1000] if hasattr(r, "text") else str(_err_body)[:1000],
                        exc=None,
                    )
                except Exception:
                    pass
                # Will be recorded by the outer except handler below.
                raise RuntimeError(f"HTTP {r.status_code}: {_err_body}")
            data = r.json()
            _log_token_usage("deepseek", model, data)
            choices = data.get("choices") or []
            if not choices:
                if attempt < max_retries:
                    time.sleep(_backoff_sleep_seconds(attempt))
                    continue
                _safe_record(
                    provider="deepseek", model=model,
                    usage=_ds_usage_from_data(data), ok=False,
                    duration_ms=int((time.monotonic() - _t0) * 1000),
                    retry_count=attempt, error="no choices", model_id=None,
                )
                return ""
            message = choices[0].get("message") or {}
            content = (message.get("content") or "").strip()
            if content:
                # T10 critical-guard capture (success)
                try:
                    _stash_last_http(status=200, body=None, exc=None)
                except Exception:
                    pass
                _safe_record(
                    provider="deepseek", model=model,
                    usage=_ds_usage_from_data(data), ok=True,
                    duration_ms=int((time.monotonic() - _t0) * 1000),
                    retry_count=attempt, error=None, model_id=None,
                )
                return content
            reasoning = (message.get("reasoning_content") or "").strip()
            if reasoning:
                # T10 critical-guard capture (success — reasoning fallback)
                try:
                    _stash_last_http(status=200, body=None, exc=None)
                except Exception:
                    pass
                _safe_record(
                    provider="deepseek", model=model,
                    usage=_ds_usage_from_data(data), ok=True,
                    duration_ms=int((time.monotonic() - _t0) * 1000),
                    retry_count=attempt, error=None, model_id=None,
                )
                return reasoning
            if attempt < max_retries:
                time.sleep(_backoff_sleep_seconds(attempt))
                continue
            _safe_record(
                provider="deepseek", model=model,
                usage=_ds_usage_from_data(data), ok=False,
                duration_ms=int((time.monotonic() - _t0) * 1000),
                retry_count=attempt, error="empty content", model_id=None,
            )
            return ""

        _safe_record(
            provider="deepseek", model=model, usage={}, ok=False,
            duration_ms=int((time.monotonic() - _t0) * 1000),
            retry_count=max_retries, error="retries exhausted", model_id=None,
        )
        return ""
    except Exception as _e:
        # T10 critical-guard capture — only stash if no inner handler did.
        try:
            tid = threading.get_ident()
            with _LAST_HTTP_LOCK:
                _already = tid in _LAST_HTTP_PER_THREAD
            if not _already:
                _resp = getattr(_e, "response", None)
                _stash_last_http(
                    status=getattr(_resp, "status_code", None),
                    body=(getattr(_resp, "text", "") if _resp is not None else str(_e))[:1000],
                    exc=_e,
                )
        except Exception:
            pass
        _safe_record(
            provider="deepseek", model=model, usage={}, ok=False,
            duration_ms=int((time.monotonic() - _t0) * 1000),
            retry_count=0, error=str(_e)[:200], model_id=None,
        )
        return ""


def _call_openai(
    api_key: str,
    model: str,
    prompt: str,
    max_output_tokens: int = 256,
    timeout_sec: int | None = None,
    retries: int = 0,
    base_url: str = "",
    response_mime_type: str | None = None,
    reasoning_effort: str = "",
) -> str:
    _t0 = time.monotonic()
    if not api_key:
        return ""

    def _oa_usage_from_data(data: dict) -> dict:
        u = data.get("usage") if isinstance(data, dict) else None
        if not isinstance(u, dict):
            return {}
        details = u.get("completion_tokens_details") or {}
        prompt_details = u.get("prompt_tokens_details") or {}
        return {
            "input_tokens": int(u.get("prompt_tokens", 0) or 0),
            "output_tokens": int(u.get("completion_tokens", 0) or 0),
            "reasoning_tokens": int((details or {}).get("reasoning_tokens", 0) or 0),
            "cache_read_input_tokens": int(
                (prompt_details or {}).get("cached_tokens", 0) or 0
            ),
        }

    try:
        import requests
        url = (base_url or "https://api.openai.com/v1").rstrip("/") + "/chat/completions"
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
        reasoning_effort = normalize_reasoning_effort(reasoning_effort)
        body: dict = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
        }
        if not _omit_temperature(model):
            body["temperature"] = 0.2
        if reasoning_effort:
            body["reasoning_effort"] = reasoning_effort
            if max_output_tokens and max_output_tokens > 0:
                body["max_completion_tokens"] = max_output_tokens
        else:
            if max_output_tokens and max_output_tokens > 0:
                body["max_tokens"] = max_output_tokens
        # Skip response_format for quirky models that return empty `{}`
        # when constrained by json_object mode. Same workaround as the
        # NVIDIA + Azure paths.
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
                r = requests.post(url, headers=headers, json=body, timeout=(connect_timeout, attempt_timeout))
            except requests.exceptions.Timeout as _to_e:
                if attempt < max_retries:
                    time.sleep(_backoff_sleep_seconds(attempt))
                    continue
                # T10 critical-guard capture
                try:
                    _stash_last_http(status=None, body=f"timeout after {attempt_timeout}s", exc=_to_e)
                except Exception:
                    pass
                _safe_record(
                    provider="openai", model=model, usage={}, ok=False,
                    duration_ms=int((time.monotonic() - _t0) * 1000),
                    retry_count=attempt, error="timeout", model_id=None,
                )
                return ""
            except requests.exceptions.RequestException as _req_e:
                if attempt < max_retries:
                    time.sleep(_backoff_sleep_seconds(attempt))
                    continue
                # T10 critical-guard capture
                try:
                    _resp = getattr(_req_e, "response", None)
                    _stash_last_http(
                        status=getattr(_resp, "status_code", None),
                        body=(getattr(_resp, "text", "") if _resp is not None else str(_req_e))[:1000],
                        exc=_req_e,
                    )
                except Exception:
                    pass
                _safe_record(
                    provider="openai", model=model, usage={}, ok=False,
                    duration_ms=int((time.monotonic() - _t0) * 1000),
                    retry_count=attempt, error=str(_req_e)[:200], model_id=None,
                )
                return ""

            if r.status_code in retriable_status and attempt < max_retries:
                # Non-retryable filter check — see _is_non_retryable_filter_response.
                _nr_filter, _nr_tag = _is_non_retryable_filter_response(
                    status=r.status_code, body=getattr(r, "text", "") or "",
                )
                if _nr_filter:
                    import sys
                    print(
                        f"[llm_utils] OpenAI {model!r}: non-retryable response ({_nr_tag}) at status {r.status_code}; skipping further retries.",
                        file=sys.stderr, flush=True,
                    )
                else:
                    wait = _retry_after_seconds(getattr(r, "headers", None)) or _http_retry_backoff_seconds(f"status_code: {r.status_code}", attempt)
                    time.sleep(wait)
                    continue

            if r.status_code >= 400:
                try:
                    _err_body = r.json()
                except Exception:
                    _err_body = r.text[:500]
                # T10 critical-guard capture (stash BEFORE raising)
                try:
                    _stash_last_http(
                        status=r.status_code,
                        body=(r.text or "")[:1000] if hasattr(r, "text") else str(_err_body)[:1000],
                        exc=None,
                    )
                except Exception:
                    pass
                # Will be recorded by the outer except handler below.
                raise RuntimeError(f"HTTP {r.status_code}: {_err_body}")
            data = r.json()
            _log_token_usage("openai", model, data)
            choices = data.get("choices") or []
            if not choices:
                if attempt < max_retries:
                    time.sleep(_backoff_sleep_seconds(attempt))
                    continue
                _safe_record(
                    provider="openai", model=model,
                    usage=_oa_usage_from_data(data), ok=False,
                    duration_ms=int((time.monotonic() - _t0) * 1000),
                    retry_count=attempt, error="no choices", model_id=None,
                )
                return ""
            message = choices[0].get("message") or {}
            text = _extract_chat_message_text(message)
            if text:
                # T10 critical-guard capture (success)
                try:
                    _stash_last_http(status=200, body=None, exc=None)
                except Exception:
                    pass
                _safe_record(
                    provider="openai", model=model,
                    usage=_oa_usage_from_data(data), ok=True,
                    duration_ms=int((time.monotonic() - _t0) * 1000),
                    retry_count=attempt, error=None, model_id=None,
                )
                return text
            if attempt < max_retries:
                time.sleep(_backoff_sleep_seconds(attempt))
                continue
            _safe_record(
                provider="openai", model=model,
                usage=_oa_usage_from_data(data), ok=False,
                duration_ms=int((time.monotonic() - _t0) * 1000),
                retry_count=attempt, error="empty text", model_id=None,
            )
            return ""
        _safe_record(
            provider="openai", model=model, usage={}, ok=False,
            duration_ms=int((time.monotonic() - _t0) * 1000),
            retry_count=max_retries, error="retries exhausted", model_id=None,
        )
        return ""
    except Exception as _exc:
        _LAST_PLAIN_LLM_CALL_ERROR.error = str(_exc)
        # T10 critical-guard capture — only stash if no inner handler did.
        try:
            tid = threading.get_ident()
            with _LAST_HTTP_LOCK:
                _already = tid in _LAST_HTTP_PER_THREAD
            if not _already:
                _resp = getattr(_exc, "response", None)
                _stash_last_http(
                    status=getattr(_resp, "status_code", None),
                    body=(getattr(_resp, "text", "") if _resp is not None else str(_exc))[:1000],
                    exc=_exc,
                )
        except Exception:
            pass
        _safe_record(
            provider="openai", model=model, usage={}, ok=False,
            duration_ms=int((time.monotonic() - _t0) * 1000),
            retry_count=0, error=str(_exc)[:200], model_id=None,
        )
        return ""


def _nvidia_reasoning_extra_body(reasoning_effort: str) -> dict[str, Any]:
    """Build NVIDIA-specific extra_body for reasoning control.

    Maps reasoning effort levels to NVIDIA's chat_template_kwargs:
    - none/empty: enable_thinking=False
    - low: enable_thinking=True, reasoning_budget=4096
    - medium: enable_thinking=True, reasoning_budget=8192
    - high: enable_thinking=True, reasoning_budget=16384
    """
    effort = normalize_reasoning_effort(reasoning_effort)
    if not effort or effort == "none":
        return {"chat_template_kwargs": {"enable_thinking": False}}
    budgets = {"low": 4096, "medium": 8192, "high": 16384}
    return {
        "chat_template_kwargs": {"enable_thinking": True},
        "reasoning_budget": budgets.get(effort, 8192),
    }


def _call_nvidia(
    api_key: str,
    model: str,
    prompt: str,
    max_output_tokens: int = 256,
    timeout_sec: int | None = None,
    retries: int = 0,
    base_url: str = "",
    response_mime_type: str | None = None,
    reasoning_effort: str = "",
) -> str:
    """Call NVIDIA NIM API (OpenAI-compatible with extra_body for reasoning)."""
    if not api_key:
        return ""
    # Proactive RPM throttle for NVIDIA NIM kimi-k2.x (40 req/min cap).
    # Without this the strategy's parallel event_maintenance batches
    # burst past the cap and burn 429s; backoff still recovers but each
    # 429 costs 30s+ of wall-clock time. Acquire BEFORE the request.
    _rpm_limiter = _get_model_request_rate_limiter(model)
    if _rpm_limiter is not None:
        _rpm_waited = _rpm_limiter.acquire()
        if _rpm_waited > 0:
            import sys
            print(
                f"[llm_utils] NVIDIA RPM throttle: waited {_rpm_waited:.1f}s before call to {model}",
                file=sys.stderr, flush=True,
            )
    try:
        import requests as _requests
        url = (base_url or "https://integrate.api.nvidia.com/v1").rstrip("/") + "/chat/completions"
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
        body: dict = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "top_p": 0.95,
        }
        if not _omit_temperature(model):
            body["temperature"] = 0.2
        if max_output_tokens and max_output_tokens > 0:
            body["max_tokens"] = max_output_tokens
        extra = _nvidia_reasoning_extra_body(reasoning_effort)
        body.update(extra)
        # Skip response_format for quirky NIM models that return empty
        # `{}` when constrained by json_object mode (moonshotai/kimi-*
        # has the same shape-collapse pathology as Azure gpt-oss).
        # The prompt itself already asks for JSON.
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
                    time.sleep(_backoff_sleep_seconds(attempt))
                    continue
                # T10 critical-guard capture
                try:
                    _stash_last_http(status=None, body=f"timeout after {attempt_timeout}s", exc=_to_e)
                except Exception:
                    pass
                return ""
            except _requests.exceptions.RequestException as _req_e:
                if attempt < max_retries:
                    time.sleep(_backoff_sleep_seconds(attempt))
                    continue
                # T10 critical-guard capture
                try:
                    _resp = getattr(_req_e, "response", None)
                    _stash_last_http(
                        status=getattr(_resp, "status_code", None),
                        body=(getattr(_resp, "text", "") if _resp is not None else str(_req_e))[:1000],
                        exc=_req_e,
                    )
                except Exception:
                    pass
                return ""

            if r.status_code in retriable_status and attempt < max_retries:
                # Non-retryable filter check — see _is_non_retryable_filter_response.
                # NVIDIA NIM is unlikely to surface Azure-style temp-blocks, but
                # content_filter responses can appear; same retry-cascade hazard.
                _nr_filter, _nr_tag = _is_non_retryable_filter_response(
                    status=r.status_code, body=getattr(r, "text", "") or "",
                )
                if _nr_filter:
                    import sys
                    print(
                        f"[llm_utils] NVIDIA {model!r}: non-retryable response ({_nr_tag}) at status {r.status_code}; skipping further retries.",
                        file=sys.stderr, flush=True,
                    )
                else:
                    # Tell the limiter about 429s so the circuit breaker
                    # can trip after sustained throttling and pause us
                    # globally instead of hammering NVIDIA's penalty box.
                    if r.status_code == 429 and _rpm_limiter is not None:
                        _rpm_limiter.note_rate_limited()
                    wait = _retry_after_seconds(getattr(r, "headers", None)) or _http_retry_backoff_seconds(f"status_code: {r.status_code}", attempt)
                    time.sleep(wait)
                    continue

            if r.status_code >= 400:
                if r.status_code == 429 and _rpm_limiter is not None:
                    _rpm_limiter.note_rate_limited()
                try:
                    _err_body = r.json()
                except Exception:
                    _err_body = r.text[:500]
                # T10 critical-guard capture (stash BEFORE raising)
                try:
                    _stash_last_http(
                        status=r.status_code,
                        body=(r.text or "")[:1000] if hasattr(r, "text") else str(_err_body)[:1000],
                        exc=None,
                    )
                except Exception:
                    pass
                raise RuntimeError(f"HTTP {r.status_code}: {_err_body}")
            data = r.json()
            _log_token_usage("nvidia", model, data)
            choices = data.get("choices") or []
            if not choices:
                if attempt < max_retries:
                    time.sleep(_backoff_sleep_seconds(attempt))
                    continue
                return ""
            message = choices[0].get("message") or {}
            text = _extract_chat_message_text(message)
            if text:
                # T10 critical-guard capture (success)
                try:
                    _stash_last_http(status=200, body=None, exc=None)
                except Exception:
                    pass
                if _rpm_limiter is not None:
                    _rpm_limiter.note_success()
                return text
            if attempt < max_retries:
                time.sleep(_backoff_sleep_seconds(attempt))
                continue
            return ""
        return ""
    except Exception as _exc:
        _LAST_PLAIN_LLM_CALL_ERROR.error = str(_exc)
        # T10 critical-guard capture — only stash if no inner handler did.
        try:
            tid = threading.get_ident()
            with _LAST_HTTP_LOCK:
                _already = tid in _LAST_HTTP_PER_THREAD
            if not _already:
                _resp = getattr(_exc, "response", None)
                _stash_last_http(
                    status=getattr(_resp, "status_code", None),
                    body=(getattr(_resp, "text", "") if _resp is not None else str(_exc))[:1000],
                    exc=_exc,
                )
        except Exception:
            pass
        return ""


def _call_azure_openai(
    api_key: str,
    deployment_name: str,
    prompt: str,
    *,
    azure_endpoint: str,
    api_version: str,
    max_output_tokens: int = 256,
    timeout_sec: int | None = None,
    retries: int = 0,
    response_mime_type: str | None = None,
    reasoning_effort: str = "",
) -> str:
    _t0 = time.monotonic()
    if not api_key or not deployment_name or not azure_endpoint:
        return ""

    def _az_usage_from_data(data: dict) -> dict:
        u = data.get("usage") if isinstance(data, dict) else None
        if not isinstance(u, dict):
            return {}
        details = u.get("completion_tokens_details") or {}
        prompt_details = u.get("prompt_tokens_details") or {}
        return {
            "input_tokens": int(u.get("prompt_tokens", 0) or 0),
            "output_tokens": int(u.get("completion_tokens", 0) or 0),
            "reasoning_tokens": int((details or {}).get("reasoning_tokens", 0) or 0),
            "cache_read_input_tokens": int(
                (prompt_details or {}).get("cached_tokens", 0) or 0
            ),
        }

    try:
        import requests
        endpoint = azure_endpoint.rstrip("/")
        url = f"{endpoint}/openai/v1/chat/completions"
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
        reasoning_effort = normalize_reasoning_effort(reasoning_effort)
        body: dict = {
            "model": deployment_name,
            "messages": [{"role": "user", "content": prompt}],
        }
        if not _omit_temperature(deployment_name):
            body["temperature"] = 0.2
        if reasoning_effort:
            body["reasoning_effort"] = reasoning_effort
        if max_output_tokens and max_output_tokens > 0:
            body["max_completion_tokens"] = max_output_tokens
        # Skip response_format for quirky models (gpt-oss, gpt-5*,
        # NIM-side kimi-k2 when exposed through Azure routing) — all
        # return empty `{}` when constrained by json_object mode. The
        # prompt already requests JSON output.
        if (
            str(response_mime_type or "").strip().lower() == "application/json"
            and not _model_skips_json_object_format(deployment_name)
        ):
            body["response_format"] = {"type": "json_object"}
        timeout = _coerce_timeout_sec(timeout_sec)
        max_retries = max(0, int(retries or 0))
        retriable_status = {429, 500, 502, 503, 504}
        _LOW_TOKEN_THRESHOLD = 5
        _LOW_TOKEN_MAX_RETRIES = 2


        for attempt in range(max_retries + _LOW_TOKEN_MAX_RETRIES + 1):
            attempt_timeout = timeout if attempt == 0 else timeout * 2
            connect_timeout = min(15, attempt_timeout)
            try:
                r = requests.post(url, headers=headers, json=body, timeout=(connect_timeout, attempt_timeout))
            except requests.exceptions.Timeout as _to_e:
                import sys
                print(f"[llm_utils] Azure {deployment_name!r} timed out after {attempt_timeout:.0f}s (attempt {attempt+1})", file=sys.stderr, flush=True)
                if attempt < max_retries:
                    time.sleep(_backoff_sleep_seconds(attempt))
                    continue
                # T10 critical-guard capture
                try:
                    _stash_last_http(status=None, body=f"timeout after {attempt_timeout}s", exc=_to_e)
                except Exception:
                    pass
                _safe_record(
                    provider="azure", model=deployment_name, usage={}, ok=False,
                    duration_ms=int((time.monotonic() - _t0) * 1000),
                    retry_count=attempt, error="timeout", model_id=None,
                )
                return ""
            except requests.exceptions.RequestException as _req_e:
                if attempt < max_retries:
                    time.sleep(_backoff_sleep_seconds(attempt))
                    continue
                # T10 critical-guard capture
                try:
                    _resp = getattr(_req_e, "response", None)
                    _stash_last_http(
                        status=getattr(_resp, "status_code", None),
                        body=(getattr(_resp, "text", "") if _resp is not None else str(_req_e))[:1000],
                        exc=_req_e,
                    )
                except Exception:
                    pass
                _safe_record(
                    provider="azure", model=deployment_name, usage={}, ok=False,
                    duration_ms=int((time.monotonic() - _t0) * 1000),
                    retry_count=attempt, error=str(_req_e)[:200], model_id=None,
                )
                return ""

            if r.status_code in retriable_status and attempt < max_retries:
                # Defensive: even though 400/403 aren't in retriable_status,
                # if Azure ever wraps a content_filter / temp-block under a
                # 5xx (or our retriable set grows later), don't retry these.
                # See _is_non_retryable_filter_response for the why.
                _nr_filter, _nr_tag = _is_non_retryable_filter_response(
                    status=r.status_code, body=getattr(r, "text", "") or "",
                )
                if _nr_filter:
                    import sys
                    print(
                        f"[llm_utils] Azure {deployment_name!r}: non-retryable response ({_nr_tag}) at status {r.status_code}; skipping further retries.",
                        file=sys.stderr, flush=True,
                    )
                    # Fall through to the >=400 branch so we stash + raise
                    # exactly once with the original error body.
                else:
                    wait = _retry_after_seconds(getattr(r, "headers", None)) or _http_retry_backoff_seconds(f"status_code: {r.status_code}", attempt)
                    time.sleep(wait)
                    continue

            if r.status_code >= 400:
                try:
                    _err_body = r.json()
                except Exception:
                    _err_body = r.text[:500]
                # T10 critical-guard capture (stash BEFORE raising so the
                # outer except can rely on it without re-parsing the message)
                try:
                    _stash_last_http(
                        status=r.status_code,
                        body=(r.text or "")[:1000] if hasattr(r, "text") else str(_err_body)[:1000],
                        exc=None,
                    )
                except Exception:
                    pass
                # Will be recorded by the outer except handler below.
                raise RuntimeError(f"HTTP {r.status_code}: {_err_body}")
            data = r.json()
            _log_token_usage("azure", deployment_name, data)
            choices = data.get("choices") or []
            if not choices:
                if attempt < max_retries:
                    time.sleep(_backoff_sleep_seconds(attempt))
                    continue
                _safe_record(
                    provider="azure", model=deployment_name,
                    usage=_az_usage_from_data(data), ok=False,
                    duration_ms=int((time.monotonic() - _t0) * 1000),
                    retry_count=attempt, error="no choices", model_id=None,
                )
                return ""
            # Check finish_reason — content_filter means retrying is pointless
            _finish_reason = str((choices[0].get("finish_reason") or "")).strip().lower()
            if _finish_reason == "content_filter":
                import sys
                print(f"[llm_utils] Content filter triggered for {deployment_name!r} — skipping retries, returning empty.", file=sys.stderr, flush=True)
                _safe_record(
                    provider="azure", model=deployment_name,
                    usage=_az_usage_from_data(data), ok=False,
                    duration_ms=int((time.monotonic() - _t0) * 1000),
                    retry_count=attempt, error="content_filter", model_id=None,
                )
                return ""
            # Detect empty responses by completion token count
            _completion_tokens = int((data.get("usage") or {}).get("completion_tokens", 999))
            if _completion_tokens <= _LOW_TOKEN_THRESHOLD:
                _low_token_attempt = attempt - max_retries
                if _low_token_attempt < _LOW_TOKEN_MAX_RETRIES:
                    import sys
                    _reason_hint = f" (finish_reason={_finish_reason})" if _finish_reason else ""
                    print(f"[llm_utils] Low-token response ({_completion_tokens} tokens{_reason_hint}) for {deployment_name!r}, retrying ({_low_token_attempt + 1}/{_LOW_TOKEN_MAX_RETRIES})...", file=sys.stderr, flush=True)
                    time.sleep(_backoff_sleep_seconds(_low_token_attempt, cap=8.0))
                    continue
                # Exhausted low-token retries — brief cooldown, return empty to trigger split fallback
                time.sleep(3.0)
                _safe_record(
                    provider="azure", model=deployment_name,
                    usage=_az_usage_from_data(data), ok=False,
                    duration_ms=int((time.monotonic() - _t0) * 1000),
                    retry_count=attempt, error="low-token exhausted", model_id=None,
                )
                return ""
            message = choices[0].get("message") or {}
            text = _extract_chat_message_text(message)
            if text:
                # T10 critical-guard capture (success)
                try:
                    _stash_last_http(status=200, body=None, exc=None)
                except Exception:
                    pass
                _safe_record(
                    provider="azure", model=deployment_name,
                    usage=_az_usage_from_data(data), ok=True,
                    duration_ms=int((time.monotonic() - _t0) * 1000),
                    retry_count=attempt, error=None, model_id=None,
                )
                return text
            if attempt < max_retries:
                time.sleep(_backoff_sleep_seconds(attempt))
                continue
            _safe_record(
                provider="azure", model=deployment_name,
                usage=_az_usage_from_data(data), ok=False,
                duration_ms=int((time.monotonic() - _t0) * 1000),
                retry_count=attempt, error="empty text", model_id=None,
            )
            return ""
        _safe_record(
            provider="azure", model=deployment_name, usage={}, ok=False,
            duration_ms=int((time.monotonic() - _t0) * 1000),
            retry_count=max_retries, error="retries exhausted", model_id=None,
        )
        return ""
    except Exception as _exc:
        _LAST_PLAIN_LLM_CALL_ERROR.error = str(_exc)
        # T10 critical-guard capture — only stash if an inner handler hasn't
        # already done so (the >=400 branch above stashes the raw response).
        try:
            tid = threading.get_ident()
            with _LAST_HTTP_LOCK:
                _already = tid in _LAST_HTTP_PER_THREAD
            if not _already:
                _resp = getattr(_exc, "response", None)
                _stash_last_http(
                    status=getattr(_resp, "status_code", None),
                    body=(getattr(_resp, "text", "") if _resp is not None else str(_exc))[:1000],
                    exc=_exc,
                )
        except Exception:
            pass
        _safe_record(
            provider="azure", model=deployment_name, usage={}, ok=False,
            duration_ms=int((time.monotonic() - _t0) * 1000),
            retry_count=0, error=str(_exc)[:200], model_id=None,
        )
        return ""


# ── Prompt-hash cache: reuse LLM responses across backtests ──────────────

_PROMPT_CACHE_TABLE = "GraphNexusLLMPromptCache"
_prompt_cache_lock = threading.Lock()
_prompt_cache_conn = None
_prompt_cache_enabled: bool = False
_prompt_cache_db: str = "IntelliStock"
_prompt_cache_tbl_ok: bool = False  # Renamed to avoid collision with line 2269
_prompt_cache_stats: dict[str, int] = {"hits": 0, "misses": 0, "stores": 0}
_prompt_cache_fail_count: int = 0
_PROMPT_CACHE_MAX_FAILS: int = 5  # Disable cache after N consecutive DB failures

# Error markers that indicate a bad LLM response — never cache these
_PROMPT_CACHE_POISON_MARKERS = (
    "rate limit", "quota", "503", "429", "content filter",
    "i'm sorry", "i cannot", "as an ai", "error:",
)


def _prompt_cache_new_conn():
    """Create a fresh RethinkDB connection for a single cache operation.
    Returns None silently on failure — no error output."""
    if not _rethink:
        return None
    import io, sys
    _orig_stderr = sys.stderr
    try:
        # Suppress RethinkDB driver connection noise (Errno 107, etc.)
        sys.stderr = io.StringIO()
        host = os.environ.get("RETHINKDB_HOST", "localhost")
        port = int(os.environ.get("RETHINKDB_PORT", "28015"))
        conn = _rethink.connect(host=host, port=port, timeout=5)
        sys.stderr = _orig_stderr
        return conn
    except Exception:
        sys.stderr = _orig_stderr
        return None


def _ensure_prompt_cache_table_if_needed() -> None:
    """Create the prompt-cache RethinkDB table if it doesn't exist.

    Idempotent. Sets ``_prompt_cache_tbl_ok`` on success. Used by both
    ``configure_llm_prompt_cache`` (global path) and the scoped ``force_cache``
    path so callers that never invoked ``configure_llm_prompt_cache`` (e.g.
    the analyst panel) can still rely on the table being present.
    """
    global _prompt_cache_tbl_ok
    if _prompt_cache_tbl_ok or not _rethink:
        return
    _setup_conn = _prompt_cache_new_conn()
    if _setup_conn is None:
        return
    try:
        tables = _rethink.db(_prompt_cache_db).table_list().run(_setup_conn)
        if _PROMPT_CACHE_TABLE not in tables:
            _rethink.db(_prompt_cache_db).table_create(_PROMPT_CACHE_TABLE).run(_setup_conn)
        _prompt_cache_tbl_ok = True
    except Exception:
        _prompt_cache_tbl_ok = False
    finally:
        try:
            _setup_conn.close()
        except Exception:
            pass


def configure_llm_prompt_cache(
    conn=None, enabled: bool = False, db: str = "IntelliStock",
) -> None:
    """Enable/disable prompt-hash caching.  Each operation creates its own connection."""
    global _prompt_cache_conn, _prompt_cache_enabled, _prompt_cache_db, _prompt_cache_tbl_ok, _prompt_cache_fail_count
    with _prompt_cache_lock:
        _prompt_cache_conn = None  # No longer used — kept for compat
        _prompt_cache_enabled = bool(enabled)
        _prompt_cache_db = db or "IntelliStock"
        _prompt_cache_tbl_ok = False
        _prompt_cache_fail_count = 0
        _prompt_cache_stats.update({"hits": 0, "misses": 0, "stores": 0})
        if enabled and _rethink:
            _ensure_prompt_cache_table_if_needed()


def get_prompt_cache_stats() -> dict[str, int]:
    """Return hit/miss/store counts since last configure."""
    return dict(_prompt_cache_stats)


def _prompt_cache_key(prompt: str, model: str, effort: str) -> str:
    """Compute a deterministic cache key from prompt + model + effort."""
    raw = f"{(model or '').strip().lower()}|{(effort or '').strip().lower()}|{(prompt or '')}"
    return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:48]


def _check_prompt_cache(prompt: str, model: str, effort: str, *, force_cache: bool = False) -> str | None:
    """Return cached LLM response if available, else None.

    When ``force_cache=True`` the global ``_prompt_cache_enabled`` flag is
    bypassed (used by scoped callers like the analyst panel that opt-in
    independently of the global cache toggle). The RethinkDB table is
    auto-ensured on the scoped path.
    """
    global _prompt_cache_fail_count
    if not _rethink:
        return None
    if not _prompt_cache_enabled and not force_cache:
        return None
    if force_cache and not _prompt_cache_tbl_ok:
        _ensure_prompt_cache_table_if_needed()
    if not _prompt_cache_tbl_ok:
        return None
    if _prompt_cache_fail_count >= _PROMPT_CACHE_MAX_FAILS:
        return None
    _conn = _prompt_cache_new_conn()
    if _conn is None:
        with _prompt_cache_lock:
            _prompt_cache_fail_count += 1
        return None
    try:
        key = _prompt_cache_key(prompt, model, effort)
        doc = _rethink.db(_prompt_cache_db).table(_PROMPT_CACHE_TABLE).get(key).run(_conn)
        if doc and doc.get("response"):
            with _prompt_cache_lock:
                _prompt_cache_stats["hits"] += 1
                _prompt_cache_fail_count = 0
            import sys
            print(
                f"[llm_utils] CACHE HIT: model={model!r} effort={effort!r} prompt_len={len(prompt)} key={key[:12]}… "
                f"(total hits={_prompt_cache_stats['hits']})",
                file=sys.stderr, flush=True,
            )
            return str(doc["response"])
    except Exception:
        with _prompt_cache_lock:
            _prompt_cache_fail_count += 1
    finally:
        try:
            _conn.close()
        except Exception:
            pass
    with _prompt_cache_lock:
        _prompt_cache_stats["misses"] += 1
    return None


def _store_prompt_cache(prompt: str, model: str, effort: str, response: str, *, force_cache: bool = False) -> None:
    """Store a new LLM response in the prompt cache.

    When ``force_cache=True`` the global ``_prompt_cache_enabled`` flag is
    bypassed (scoped opt-in path). The RethinkDB table is auto-ensured.
    """
    if not _rethink:
        return
    if not _prompt_cache_enabled and not force_cache:
        return
    if force_cache and not _prompt_cache_tbl_ok:
        _ensure_prompt_cache_table_if_needed()
    if not _prompt_cache_tbl_ok:
        return
    if not response or len(response) < 10:
        return
    # Reject error/truncated responses to prevent cache poisoning
    _lower = response[:200].lower()
    if any(marker in _lower for marker in _PROMPT_CACHE_POISON_MARKERS):
        return
    # Reject malformed JSON if the response looks like it should be JSON
    _stripped = response.strip()
    if _stripped.startswith("{") or _stripped.startswith("["):
        try:
            json.loads(_stripped)
        except (json.JSONDecodeError, ValueError):
            return  # truncated/malformed JSON — don't cache
    _conn = _prompt_cache_new_conn()
    if _conn is None:
        return
    try:
        key = _prompt_cache_key(prompt, model, effort)
        doc = {
            "id": key,
            "model": (model or "").strip().lower(),
            "effort": (effort or "").strip().lower(),
            "prompt_hash": hashlib.sha256((prompt or "").encode("utf-8", errors="replace")).hexdigest()[:32],
            "prompt_len": len(prompt or ""),
            "response": response,
            "cached_at": datetime.utcnow().isoformat() + "Z",
            "hit_count": 0,
        }
        _rethink.db(_prompt_cache_db).table(_PROMPT_CACHE_TABLE).insert(doc, conflict="replace").run(_conn, noreply=True)
        with _prompt_cache_lock:
            _prompt_cache_stats["stores"] += 1
        import sys
        print(
            f"[llm_utils] CACHE STORE: model={model!r} effort={effort!r} prompt_len={len(prompt)} resp_len={len(response)} "
            f"(total stored={_prompt_cache_stats['stores']})",
            file=sys.stderr, flush=True,
        )
    except Exception:
        pass
    finally:
        try:
            _conn.close()
        except Exception:
            pass


def _fire_llm_output_log(provider: str, model: str, prompt: str, raw_output: str) -> None:
    """Fire-and-forget: POST raw LLM output to the logging API in a background thread."""
    api_url = os.environ.get("LLM_LOG_API_URL", "http://api:8011").rstrip("/")
    if not api_url:
        return
    prompt_hash = hashlib.sha256((prompt or "").encode("utf-8")).hexdigest()[:16]

    def _post() -> None:
        try:
            import urllib.request as _req
            payload = json.dumps({
                "provider": provider or "",
                "model": model or "",
                "prompt_hash": prompt_hash,
                "raw_output": raw_output or "",
                "logged_at": datetime.utcnow().isoformat() + "Z",
            }).encode("utf-8")
            req = _req.Request(
                f"{api_url}/llm/outputs",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with _req.urlopen(req, timeout=5) as resp:
                import sys
                result = json.loads(resp.read().decode("utf-8"))
                output_id = result.get("id", "unknown")
                print(f"[llm_utils] LLM output log saved: llm/outputs/{output_id}", file=sys.stderr, flush=True)
        except Exception:
            pass  # best-effort, never crash the strategy

    threading.Thread(target=_post, daemon=True).start()


# ── Critical-guard HTTP capture (Wave 1, T10) ─────────────────────────────
#
# Each provider stashes the last (status, body, exc) it observed before
# returning to the caller. The retry wrapper (_call_llm_with_critical_guard)
# pops this value and feeds it to llm_critical_guard.classify(). Keyed by
# thread-ident so concurrent workers don't collide.

_LAST_HTTP_PER_THREAD: dict[int, dict[str, Any]] = {}
_LAST_HTTP_LOCK = threading.Lock()


def _stash_last_http(*, status: int | None, body: str | None, exc: BaseException | None) -> None:
    """Provider call sites invoke this just before returning so the retry
    wrapper can classify the response. Overwrites any prior stash for the
    current thread."""
    tid = threading.get_ident()
    with _LAST_HTTP_LOCK:
        _LAST_HTTP_PER_THREAD[tid] = {"status": status, "body": body, "exc": exc}


def _pop_last_http() -> dict[str, Any] | None:
    """Retry wrapper invokes this after each call_llm_by_provider() return.
    Returns None if the provider didn't stash anything (counts as 'no info' —
    classifier returns 'none')."""
    tid = threading.get_ident()
    with _LAST_HTTP_LOCK:
        return _LAST_HTTP_PER_THREAD.pop(tid, None)


def _call_with_capture(provider, api_key, model, prompt, **kw):
    """Wrap call_llm_by_provider so the caller gets (text, status, body, exc)
    instead of just text. status/body/exc come from _pop_last_http(); if the
    provider didn't stash, status=200/body=None/exc=None (treat as success
    when text was returned, or generic error when text is empty)."""
    # Clear any stale stash from a previous call on this thread.
    _pop_last_http()
    text = ""
    captured_exc = None
    try:
        text = call_llm_by_provider(provider, api_key, model, prompt, **kw)
    except Exception as e:
        captured_exc = e
    captured = _pop_last_http() or {}
    status = captured.get("status")
    body = captured.get("body")
    exc = captured.get("exc") or captured_exc
    # Heuristic: if no stash AND no exception AND non-empty text, treat as 200.
    if status is None and exc is None and text:
        status = 200
    return text, status, body, exc


def _call_llm_with_critical_guard(
    provider: str,
    api_key: str,
    model: str,
    prompt: str,
    *,
    attribution_keys: dict[str, Any] | None = None,
    **kw,
) -> str:
    """Wraps _call_with_capture with critical-class detection + retry escalation.

    Behavior:
      - Calls _call_with_capture, classifies the response.
      - If class is critical AND we've already raised once in this process,
        return the empty text from the underlying call (don't re-raise; live
        traffic shouldn't be blocked after abort triggers).
      - If class is critical and we haven't raised, retry up to 3x with
        exponential backoff (1s, 2s, 4s). If the 4th attempt is still
        critical, mark_raised() and raise LLMCriticalFailure.
      - If class is 'none' (normal), return text immediately.
    """
    import time as _time
    # Use the bare module name so that this resolves to the SAME module object
    # that broker.py imports via `from llm_critical_guard import ...`. If we
    # use `from backend import llm_critical_guard`, Python will instantiate
    # two distinct module objects (one under each path) with separate
    # _already_raised flags / counters / LLMCriticalFailure class identity —
    # breaking isinstance() in the broker outer-loop catch.
    try:
        import llm_critical_guard
    except ImportError:
        # Fallback for environments where `backend/` isn't on sys.path
        # (e.g. some test invocations from repo root).
        from backend import llm_critical_guard

    if llm_critical_guard.was_already_raised():
        # A different worker already triggered abort. Pass through one call
        # so the caller's normal error path runs; do not retry, do not raise.
        text, status, body, exc = _call_with_capture(provider, api_key, model, prompt, **kw)
        return text

    attempts: list[dict[str, Any]] = []
    text = ""  # ensure defined for the mid-retry short-circuit return below
    for attempt_idx in range(4):  # 1 original + 3 retries
        # Mid-retry short-circuit: if a sibling worker tripped the guard while
        # we were sleeping between attempts, don't compound the raise. Return
        # the last empty/error text from the previous attempt and let the
        # caller's normal None/empty handling fire. Only the FIRST worker's
        # LLMCriticalFailure propagates to broker.py — losing workers would
        # otherwise raise into a ThreadPoolExecutor Future that nobody awaits,
        # silently swallowing the failure.
        if attempt_idx > 0 and llm_critical_guard.was_already_raised():
            return text

        text, status, body, exc = _call_with_capture(provider, api_key, model, prompt, **kw)

        # Update 5xx-consecutive counter before classifying (counter feeds classify).
        llm_critical_guard.update_consecutive_state(
            tag="pending", status=status, provider=provider, model=model,
        )
        class_tag, is_critical = llm_critical_guard.classify(
            status=status, body=body, exc=exc, provider=provider, model=model,
        )

        if not is_critical:
            return text

        attempts.append({
            "attempt": attempt_idx + 1,
            "class_tag": class_tag,
            "http_status": status,
            "body_sample": (body or "")[:300],
            "ts": _time.time(),
        })

        if attempt_idx < 3:
            _time.sleep(2 ** attempt_idx)  # 1, 2, 4
            continue

        # 4th attempt also critical → escalate
        llm_critical_guard.mark_raised()
        raise llm_critical_guard.LLMCriticalFailure(
            class_tag=class_tag,
            provider=provider,
            model=model,
            attribution=dict(attribution_keys or {}),
            attempts=attempts,
        )

    return text  # unreachable, but mypy-friendly


def _parse_structured_error(error_str: str) -> tuple[int | None, str]:
    """Parse the _LAST_STRUCTURED_LLM_CALL error string into (status, body).

    Sample formats:
      'raw_json_preferred=HTTP 403: {...body...}'
      'raw_json_preferred=HTTP 401: {...}'
      'raw_json_preferred=Skeleton output: model=...'   (no status)
      'some other error string'  (no status)

    Returns (status_or_None, body_string).
    """
    import re as _re
    if not error_str:
        return None, ""
    m = _re.search(r"HTTP\s+(\d{3})", error_str)
    status = int(m.group(1)) if m else None
    return status, error_str


def _call_structured_llm_with_critical_guard(
    provider: str,
    api_key: str,
    model: str,
    prompt: str,
    output_type: Any,
    *,
    attribution_keys: dict[str, Any] | None = None,
    **kw,
):
    """Mirror of _call_llm_with_critical_guard for the structured-JSON path.

    After each call_structured_llm_by_provider invocation, reads
    _LAST_STRUCTURED_LLM_CALL.data to classify the response. If the response
    is critical (Azure 403 blocked / auth fail / codex quota / persistent 5xx),
    retries up to 3 times with exponential backoff. On the 4th critical
    attempt, raises LLMCriticalFailure so backtests can abort cleanly.

    Non-critical failures (None return with no recognised critical signal)
    pass straight through — the caller's existing None-handling fires.
    """
    import time as _time
    # Use the bare module name so this resolves to the SAME llm_critical_guard
    # module object that broker.py imports — see _call_llm_with_critical_guard
    # above for the full rationale.
    try:
        import llm_critical_guard
    except ImportError:
        from backend import llm_critical_guard

    if llm_critical_guard.was_already_raised():
        # Another worker already triggered abort. Pass the call through once so
        # the caller's normal error path runs; do not retry, do not re-raise.
        return call_structured_llm_by_provider(provider, api_key, model, prompt, output_type, **kw)

    attempts: list[dict[str, Any]] = []
    result = None
    for attempt_idx in range(4):  # 1 original + 3 retries
        # Mid-retry short-circuit: see _call_llm_with_critical_guard for the
        # full rationale. If a sibling worker tripped the guard while we slept
        # between attempts, return the last result (None / non-ok) instead of
        # compounding the raise into a stranded ThreadPoolExecutor Future.
        if attempt_idx > 0 and llm_critical_guard.was_already_raised():
            return result

        result = call_structured_llm_by_provider(provider, api_key, model, prompt, output_type, **kw)
        # Inspect _LAST_STRUCTURED_LLM_CALL to classify the response. The
        # structured helper always stashes (provider, error, ok, ...) just
        # before returning, but tolerate missing thread-local data defensively.
        try:
            data = _LAST_STRUCTURED_LLM_CALL.data or {}
        except Exception:
            data = {}
        ok = bool(data.get("ok"))
        if ok:
            return result
        error_str = str(data.get("error") or "")
        status, body = _parse_structured_error(error_str)

        # Update 5xx-consecutive counter before classifying (counter feeds classify).
        llm_critical_guard.update_consecutive_state(
            tag="pending", status=status, provider=provider, model=model,
        )
        class_tag, is_critical = llm_critical_guard.classify(
            status=status, body=body, exc=None, provider=provider, model=model,
        )

        if not is_critical:
            # Non-critical failure (skeleton output, transient parse error, etc.) —
            # let the caller's existing None-handling fire. Don't retry here.
            return result

        attempts.append({
            "attempt": attempt_idx + 1,
            "class_tag": class_tag,
            "http_status": status,
            "body_sample": body[:300],
            "ts": _time.time(),
        })

        if attempt_idx < 3:
            _time.sleep(2 ** attempt_idx)  # 1, 2, 4
            continue

        # 4th attempt also critical → escalate
        llm_critical_guard.mark_raised()
        raise llm_critical_guard.LLMCriticalFailure(
            class_tag=class_tag,
            provider=provider,
            model=model,
            attribution=dict(attribution_keys or {}),
            attempts=attempts,
        )

    return result  # unreachable, but mypy-friendly


def call_llm_by_provider(
    provider: str,
    api_key: str,
    model: str,
    prompt: str,
    max_output_tokens: int = 256,
    timeout_sec: int | None = None,
    retries: int = 0,
    response_mime_type: str | None = None,
    provider_config: dict[str, Any] | None = None,
) -> str:
    """
    Call LLM by provider. Supports 'gemini', 'deepseek', 'openai', 'azure',
    and 'claude-cli' (uses the locally-installed ``claude`` binary).
    Returns response text or empty string.

    timeout_sec: Optional override for LLM_REQUEST_TIMEOUT (seconds).
    retries: Number of retries on retriable failures (e.g. 503/429/timeouts).
    response_mime_type: Gemini-only response MIME type (e.g. application/json).
    """
    if (provider or "").strip().lower() == "claude-cli":
        return _call_claude_cli_plain(
            model=model,
            prompt=prompt,
            provider_config=provider_config,
            timeout_sec=timeout_sec,
            retries=retries,
        )
    if (provider or "").strip().lower() == "codex-cli":
        return _call_codex_cli_plain(
            model=model,
            prompt=prompt,
            provider_config=provider_config,
            timeout_sec=timeout_sec,
            retries=retries,
        )
    if not api_key or not model:
        return ""
    # ── Prompt cache check ──
    _effort_key = str((provider_config or {}).get("reasoning_effort", "")).strip().lower()
    _cached = _check_prompt_cache(prompt, model, _effort_key)
    if _cached is not None:
        return _cached
    _rl = _get_model_rate_limiter(model)
    if _rl is not None:
        _estimated = len(prompt) // 4 + max(256, max_output_tokens or 256)
        _rl_waited = _rl.acquire(_estimated)
        if _rl_waited > 0:
            import sys
            print(
                f"[llm_utils] Rate limiter: waited {_rl_waited:.1f}s before text call to {model!r}",
                file=sys.stderr,
                flush=True,
            )
    p = (provider or "gemini").strip().lower()
    resolved = _resolve_provider_config(provider, provider_config)
    if p == "azure":
        _result = _call_azure_openai(
            api_key,
            model,
            prompt,
            azure_endpoint=str(resolved.get("azure_endpoint") or ""),
            api_version=str(resolved.get("api_version") or ""),
            max_output_tokens=max_output_tokens,
            timeout_sec=timeout_sec,
            retries=retries,
            response_mime_type=response_mime_type,
            reasoning_effort=str(resolved.get("reasoning_effort") or ""),
        )
    elif p == "openai":
        _result = _call_openai(
            api_key,
            model,
            prompt,
            max_output_tokens=max_output_tokens,
            timeout_sec=timeout_sec,
            retries=retries,
            base_url=str(resolved.get("base_url") or ""),
            response_mime_type=response_mime_type,
            reasoning_effort=str(resolved.get("reasoning_effort") or ""),
        )
    elif p == "nvidia":
        _result = _call_nvidia(
            api_key,
            model,
            prompt,
            max_output_tokens=max_output_tokens,
            timeout_sec=timeout_sec,
            retries=retries,
            base_url=str(resolved.get("base_url") or ""),
            response_mime_type=response_mime_type,
            reasoning_effort=str(resolved.get("reasoning_effort") or ""),
        )
    elif p == "deepseek":
        _result = _call_deepseek(api_key, model, prompt, max_output_tokens, timeout_sec=timeout_sec, retries=retries)
    else:
        _result = _call_gemini(
            api_key,
            model,
            prompt,
            max_output_tokens,
            timeout_sec=timeout_sec,
            retries=retries,
            response_mime_type=response_mime_type,
        )
    if _result:
        _fire_llm_output_log(provider, model, prompt, _result)
        _store_prompt_cache(prompt, model, _effort_key, _result)
    return _result


# ── Gemini function-calling (tool use) ─────────────────────────────────────

def call_gemini_with_tools(
    api_key: str,
    model: str,
    prompt: str,
    tools: list[dict],
    tool_executor: callable,
    max_rounds: int = 2,
    max_output_tokens: int = 4096,
) -> str:
    """
    Gemini function-calling loop. Sends prompt + tool definitions,
    executes any function calls via tool_executor, sends results back,
    returns final text response. Capped at max_rounds to prevent token explosion.

    Args:
        api_key: Gemini API key.
        model: Model name (e.g. "gemini-2.0-flash-exp").
        prompt: Initial user prompt.
        tools: Gemini-format tool declarations, e.g.:
            [{"function_declarations": [{"name": "search_by_sector", "description": "...",
              "parameters": {"type": "object", "properties": {...}, "required": [...]}}]}]
        tool_executor: Callback function(name: str, args: dict) -> str.
            Called when the model requests a function call. Should return the result as a string.
        max_rounds: Maximum tool-calling round-trips (default 2).
        max_output_tokens: Max tokens per response.

    Returns:
        Final text response from the model, or empty string on failure.
    """
    if not api_key or not model:
        return ""
    import requests as _requests

    url = f"{GEMINI_BASE}/models/{model}:generateContent"
    headers = {"Content-Type": "application/json"}
    params = {"key": api_key}
    timeout = int(os.environ.get("LLM_REQUEST_TIMEOUT", "180"))

    # Build initial conversation
    contents = [{"role": "user", "parts": [{"text": prompt}]}]

    for _round in range(max_rounds + 1):  # +1 for the final text response
        body = {
            "contents": contents,
            "tools": tools,
            "generationConfig": {
                "temperature": 0.2,
                "maxOutputTokens": max_output_tokens,
            },
        }
        try:
            r = _requests.post(url, headers=headers, params=params, json=body, timeout=timeout)
            r.raise_for_status()
            data = r.json()
        except Exception:
            return ""

        candidates = data.get("candidates") or []
        if not candidates:
            return ""
        parts = (candidates[0].get("content") or {}).get("parts") or []
        if not parts:
            return ""

        # Check if model wants to call functions
        function_calls = [p for p in parts if "functionCall" in p]
        if not function_calls:
            # No function calls — extract final text
            text_parts = [p.get("text", "") for p in parts if "text" in p]
            return " ".join(text_parts).strip()

        # Append model response to conversation
        contents.append({"role": "model", "parts": parts})

        # Execute each function call and build response
        function_responses = []
        for fc_part in function_calls:
            fc = fc_part["functionCall"]
            fn_name = fc.get("name", "")
            fn_args = fc.get("args", {})
            try:
                result = tool_executor(fn_name, fn_args)
            except Exception as e:
                result = f"Error: {e}"
            function_responses.append({
                "functionResponse": {
                    "name": fn_name,
                    "response": {"result": result},
                }
            })

        # Append function responses to conversation
        contents.append({"role": "user", "parts": function_responses})

    # If we exhausted rounds, try to get whatever text the model last produced
    return ""


def call_gemini_with_grounding(
    api_key: str,
    model: str,
    prompt: str,
    max_output_tokens: int = 1024,
    timeout_sec: int | None = None,
) -> str:
    """
    Call Gemini with Google Search grounding enabled.
    The model will automatically search the web to answer questions requiring
    current/factual information (e.g. "Does company A currently own company B?").

    Returns the final text response, or empty string on failure.
    Requires a Gemini model that supports search grounding (gemini-2.0-flash or newer).
    """
    if not api_key or not model:
        return ""
    import requests as _requests

    timeout = _coerce_timeout_sec(timeout_sec)
    url = f"{GEMINI_BASE}/models/{model}:generateContent"
    headers = {"Content-Type": "application/json"}
    params = {"key": api_key}
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "tools": [{"googleSearch": {}}],
        "generationConfig": {
            "temperature": 0.1,
            **({"maxOutputTokens": max_output_tokens} if max_output_tokens and max_output_tokens > 0 else {}),
        },
    }
    try:
        r = _requests.post(url, headers=headers, params=params, json=body, timeout=timeout)
        r.raise_for_status()
        data = r.json()
        _log_token_usage("gemini-grounding", model, data)
        candidates = data.get("candidates") or []
        if not candidates:
            return ""
        parts = (candidates[0].get("content") or {}).get("parts") or []
        text_parts = [p.get("text", "") for p in parts if "text" in p]
        return " ".join(text_parts).strip()
    except Exception:
        return ""


# ── Prompt-hash cache (reusable) ───────────────────────────────────────────
_DEFAULT_DB_NAME = "IntelliStock"
_DEFAULT_PROMPT_CACHE_TABLE = "LLMPromptCache"
_prompt_cache_table_ensured: dict[str, bool] = {}  # table_name -> ensured


def _prompt_cache_ensure_table(conn, db_name: str, table_name: str) -> None:
    if _rethink is None or conn is None:
        return
    key = f"{db_name}:{table_name}"
    if _prompt_cache_table_ensured.get(key):
        return
    try:
        dbs = list(_rethink.db_list().run(conn))
        if db_name not in dbs:
            _rethink.db_create(db_name).run(conn)
        tables = list(_rethink.db(db_name).table_list().run(conn))
        if table_name not in tables:
            _rethink.db(db_name).table_create(table_name).run(conn)
        _prompt_cache_table_ensured[key] = True
    except Exception:
        pass


def _prompt_cache_get(conn, cache_ids: list[str], db_name: str, table_name: str) -> dict[str, str]:
    """Fetch raw LLM output by cache_id. Returns {cache_id: raw_output}."""
    if _rethink is None or conn is None or not cache_ids:
        return {}
    try:
        _prompt_cache_ensure_table(conn, db_name, table_name)
        cursor = _rethink.db(db_name).table(table_name).get_all(*cache_ids).run(conn)
        return {
            doc["id"]: (doc.get("raw_output") or "")
            for doc in cursor
            if doc and "id" in doc and "raw_output" in doc
        }
    except Exception:
        return {}


def _prompt_cache_save(
    conn,
    entries: list[dict],
    db_name: str,
    table_name: str,
) -> None:
    """Save raw LLM output. Each entry: id, model, prompt_hash, raw_output, cached_at."""
    if _rethink is None or conn is None or not entries:
        return
    try:
        _prompt_cache_ensure_table(conn, db_name, table_name)
        _rethink.db(db_name).table(table_name).insert(entries, conflict="replace").run(conn)
    except Exception:
        pass


def call_llm_with_prompt_cache(
    provider: str,
    api_key: str,
    model: str,
    prompt: str,
    max_output_tokens: int = 256,
    provider_config: dict[str, Any] | None = None,
    db_conn=None,
    db_name: str = _DEFAULT_DB_NAME,
    prompt_cache_table: str = _DEFAULT_PROMPT_CACHE_TABLE,
) -> tuple[str, bool]:
    """
    Call LLM with optional prompt-hash caching. Same (model, prompt) returns cached raw output.

    Args:
        provider: LLM provider (e.g. 'gemini', 'deepseek', 'openai', 'azure').
        api_key: Provider API key.
        model: Model name.
        prompt: Full prompt text (hashed for cache key).
        max_output_tokens: Max tokens for the call.
        provider_config: Optional provider-specific settings (e.g. Azure endpoint/api_version).
        db_conn: Optional RethinkDB connection. If None, cache is skipped.
        db_name: Database name for cache table.
        prompt_cache_table: Table name for raw output cache.

    Returns:
        (raw_output, from_cache). raw_output is the raw model response; from_cache True if from DB.
    """
    raw_empty = ("", False)
    if not api_key or not model:
        return raw_empty

    provider_name = (provider or "gemini").strip().lower()
    provider_meta = _safe_provider_meta(provider_name, provider_config)
    model_ref = llm_model_reference(model, provider_meta.get("reasoning_effort"))
    try:
        provider_meta_hash = hashlib.sha256(
            repr(sorted(provider_meta.items())).encode("utf-8")
        ).hexdigest()[:16]
    except Exception:
        provider_meta_hash = "default"
    prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    cache_id = f"{provider_name}|{model_ref}|{provider_meta_hash}|{prompt_hash}"

    if db_conn is not None and _rethink is not None:
        cached = _prompt_cache_get(db_conn, [cache_id], db_name, prompt_cache_table)
        if cache_id in cached and (cached[cache_id] or "").strip():
            return (cached[cache_id].strip(), True)

    raw = call_llm_by_provider(
        provider_name,
        api_key,
        model,
        prompt,
        max_output_tokens,
        provider_config=provider_config,
    )
    if not raw or not raw.strip():
        return ("", False)

    raw = raw.strip()
    if db_conn is not None and _rethink is not None:
        _prompt_cache_save(
            db_conn,
            [
                {
                    "id": cache_id,
                    "provider": provider_name,
                    "model": model_ref,
                    "provider_meta": provider_meta,
                    "prompt_hash": prompt_hash,
                    "raw_output": raw,
                    "cached_at": datetime.utcnow().isoformat() + "Z",
                }
            ],
            db_name,
            prompt_cache_table,
        )
    return (raw, False)


def parse_llm_signal(text: str) -> int:
    """Parse LLM response to -1, 0, or 1. Looks for -1/0/1 or sell/hold/buy."""
    if not text:
        return 0
    t = text.strip().upper()
    if "-1" in t or "SELL" in t:
        return -1
    if "1" in t and "BUY" in t:
        return 1
    if "BUY" in t:
        return 1
    if "HOLD" in t or "0" in t:
        return 0
    if "SELL" in t:
        return -1
    for part in t.replace(",", " ").split():
        if part == "-1":
            return -1
        if part == "1":
            return 1
        if part == "0":
            return 0
    return 0
