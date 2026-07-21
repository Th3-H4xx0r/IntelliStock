"""Persistent local disk cache for the graph_nexus LLM dispatch.

Purpose (2026-07-21 local backtest harness): we re-run the same backtest
windows over and over while tuning the strategy. The strategy already checks
its RethinkDB caches (sentiment / article-classification) BEFORE calling the
LLM dispatch, but ~75% of calls (the per-candidate "overlay" fan-out, plus
event-maintenance and macro-news) are NOT cached server-side and re-fire every
run. This module adds a project-local, persistent cache LAST in the chain:

    RethinkDB cache (existing, upstream)  ->  THIS local cache  ->  live Claude

so the first run of a window populates the local cache and every subsequent
re-test replays from disk — free, fast, and independent of whether the DB /
Tailscale is reachable. It is installed by monkeypatching the single dispatch
chokepoint (`llm_utils.call_structured_llm_by_provider` and the plain
`call_llm_by_provider`) from the launcher, so NO production code changes.

Keying mirrors the strategy's own prompt-cache philosophy: identity is
(provider, model, reasoning_effort, prompt, system_prompt, output schema name)
— api_key / timeouts / retries are excluded. Any error in cache logic falls
through to the real call: the cache can never break a backtest.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from typing import Any, Callable, Optional

_STATS = {"struct_hit": 0, "struct_miss": 0, "plain_hit": 0, "plain_miss": 0, "store": 0, "error": 0}
_STATS_LOCK = threading.Lock()

_ENV_DIR = "NEXUS_LOCAL_LLM_CACHE_DIR"


def cache_dir() -> Optional[str]:
    d = (os.environ.get(_ENV_DIR) or "").strip()
    return d or None


def _bump(key: str) -> None:
    with _STATS_LOCK:
        _STATS[key] = _STATS.get(key, 0) + 1


def stats() -> dict:
    with _STATS_LOCK:
        return dict(_STATS)


def _effort_of(provider_config: Any) -> str:
    if isinstance(provider_config, dict):
        for k in ("reasoning_effort", "effort", "_effort"):
            v = provider_config.get(k)
            if v:
                return str(v)
    return ""


def _schema_name(output_type: Any) -> str:
    try:
        return getattr(output_type, "__name__", None) or str(output_type)
    except Exception:
        return "?"


def make_key(
    provider: Any,
    model: Any,
    prompt: Any,
    output_type: Any,
    system_prompt: Any,
    provider_config: Any,
    kind: str = "struct",
) -> str:
    """Stable content hash of the semantically-identifying call inputs."""
    sys_repr = system_prompt
    if isinstance(system_prompt, (list, tuple)):
        sys_repr = list(system_prompt)
    payload = {
        "kind": kind,
        "provider": str(provider or "").strip().lower(),
        "model": str(model or ""),
        "effort": _effort_of(provider_config),
        "schema": _schema_name(output_type) if output_type is not None else None,
        "system": sys_repr,
        "prompt": prompt if isinstance(prompt, str) else str(prompt),
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8", "replace")).hexdigest()


def _path_for(d: str, key: str) -> str:
    return os.path.join(d, key[:2], key + ".json")


def get(key: str) -> Optional[dict]:
    d = cache_dir()
    if not d:
        return None
    try:
        with open(_path_for(d, key), "r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return None
    except Exception:
        _bump("error")
        return None


def put(key: str, value: dict) -> None:
    d = cache_dir()
    if not d:
        return
    try:
        sub = os.path.join(d, key[:2])
        os.makedirs(sub, exist_ok=True)
        fp = os.path.join(sub, key + ".json")
        tmp = f"{fp}.tmp.{os.getpid()}.{threading.get_ident()}"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(value, fh, ensure_ascii=False, default=str)
        os.replace(tmp, fp)  # atomic
        _bump("store")
    except Exception:
        _bump("error")


def wrap_structured(orig: Callable, force_provider=None, force_model=None) -> Callable:
    """Wrap `call_structured_llm_by_provider(provider, api_key, model, prompt,
    output_type, **kw)`. On a local-cache hit, reconstruct the validated model
    with `output_type.model_validate(...)`; on miss, call through and store.

    When `force_provider` is set, the provider/model are REWRITTEN before the
    real call — this is the guaranteed catch-all that forces every dispatch to
    claude-cli regardless of how the role resolved (the overlay role resolves
    via [ModelResolver], bypassing _resolve_role_llm_config). The cache key uses
    the rewritten (claude) identity so re-runs replay consistently.
    """
    def wrapped(provider, api_key, model, prompt, output_type, **kw):
        if force_provider:
            provider = force_provider
            model = force_model or model
        if cache_dir() is None or output_type is None:
            return orig(provider, api_key, model, prompt, output_type, **kw)
        key = None
        try:
            key = make_key(provider, model, prompt, output_type,
                           kw.get("system_prompt"), kw.get("provider_config"), kind="struct")
            hit = get(key)
            if hit is not None and "response" in hit:
                try:
                    obj = output_type.model_validate(hit["response"])
                    _bump("struct_hit")
                    return obj
                except Exception:
                    _bump("error")  # stale/incompatible cached shape → fall through
        except Exception:
            _bump("error")
            key = None
        _bump("struct_miss")
        result = orig(provider, api_key, model, prompt, output_type, **kw)
        if key is not None and result is not None:
            try:
                dumped = result.model_dump() if hasattr(result, "model_dump") else result
                put(key, {"response": dumped, "model": str(model), "provider": str(provider)})
            except Exception:
                _bump("error")
        return result
    wrapped.__wrapped__ = orig  # type: ignore[attr-defined]
    return wrapped


def wrap_plain(orig: Callable, force_provider=None, force_model=None) -> Callable:
    """Wrap the plain-text `call_llm_by_provider(provider, api_key, model,
    prompt, **kw) -> str`. Caches the returned string. `force_provider` rewrites
    the provider/model before the real call (see wrap_structured)."""
    def wrapped(provider, api_key, model, prompt, **kw):
        if force_provider:
            provider = force_provider
            model = force_model or model
        if cache_dir() is None:
            return orig(provider, api_key, model, prompt, **kw)
        key = None
        try:
            key = make_key(provider, model, prompt, None,
                           kw.get("system_prompt"), kw.get("provider_config"), kind="plain")
            hit = get(key)
            if hit is not None and "text" in hit:
                _bump("plain_hit")
                return hit["text"]
        except Exception:
            _bump("error")
            key = None
        _bump("plain_miss")
        result = orig(provider, api_key, model, prompt, **kw)
        if key is not None and isinstance(result, str):
            put(key, {"text": result, "model": str(model), "provider": str(provider)})
        return result
    wrapped.__wrapped__ = orig  # type: ignore[attr-defined]
    return wrapped
