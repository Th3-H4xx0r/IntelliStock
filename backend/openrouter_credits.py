"""OpenRouter credit preflight guard + 402 affordability de-cliff support.

Round-2 Task 4 (2026-07). Two independent safety layers against the incident
where OpenRouter credits died mid-run and the engine kept trading/simulating
LLM-blind:

  1. PREFLIGHT GUARD (proactive, coarse): ``check_credit_guard`` queries the
     account credit balance (``get_balance``) and returns ``"ok"|"warn"|"halt"``
     against the configured thresholds. Broker wires this at live FULL-run start
     + backtest start + every 5 sim days; a ``"halt"`` is escalated into the
     existing insufficient_credits critical path. The guard is FAIL-OPEN: any
     balance-fetch failure (None balance) degrades to ``"ok"`` so a flaky
     credits endpoint never blocks trading on its own — the reactive 402
     handling below still protects us.

  2. 402 DE-CLIFF (reactive, precise): lives in ``llm_utils._call_openrouter``.
     When a call 402s with a body like ``"... can only afford 10773 ..."`` we
     retry ONCE with ``max_tokens`` clamped to ``max(2048, N-512)`` instead of
     letting a single reasoning-token overshoot burn the whole call. The
     affordable-token count is cached here (``note_affordable_tokens``) so
     subsequent uncapped calls can be *pre-clamped* (``preclamp_max_tokens``)
     before they hit the wire — this is the pricing-free realisation of the
     spec's "pre-clamp only when a cached balance is known".

All network here is a single GET to the credits endpoint; tests mock
``requests.get``. No DB, no import-time side effects.
"""
from __future__ import annotations

import re
import threading
from typing import Any, Callable, Iterable, Optional

_CREDITS_URL = "https://openrouter.ai/api/v1/credits"

# Config keys + defaults (spec 1c). Warn once when balance dips below $3;
# halt (escalate to insufficient_credits critical) at/under $0.50.
_WARN_KEY = "openrouter_low_credit_warn_usd"
_HALT_KEY = "openrouter_halt_credit_usd"
_DEFAULT_WARN_USD = 3.0
_DEFAULT_HALT_USD = 0.5

# "can only afford <N>" — OpenRouter's 402 body tells us the max output tokens
# the remaining balance can pay for on this exact request.
_AFFORD_RE = re.compile(r"can only afford (\d+)")

_state_lock = threading.RLock()
# One-shot warn latch per process: the operator gets ONE low-credit page, not
# one every 5 sim days / every FULL tick.
_warn_latched = False
# Last successfully-fetched USD balance (used only for observability; the guard
# re-fetches live each call so a stale value can never cause a false halt).
_last_balance: Optional[float] = None
# Last affordable-token budget learned from a 402 de-cliff. Drives the proactive
# pre-clamp so we stop sending calls we already know will 402.
_last_affordable_tokens: Optional[int] = None


def reset_state() -> None:
    """Clear the warn latch + cached balances. For test isolation and for a
    broker resume after an operator tops up credits."""
    global _warn_latched, _last_balance, _last_affordable_tokens
    with _state_lock:
        _warn_latched = False
        _last_balance = None
        _last_affordable_tokens = None


# ── Balance fetch ────────────────────────────────────────────────────────────
def get_balance(api_key: str, timeout: float = 5.0) -> Optional[float]:
    """Return the OpenRouter account balance in USD (``total_credits -
    total_usage``), or ``None`` on ANY error (no key, timeout, non-200, bad
    JSON, missing/garbage fields). None means "don't know" — callers must
    degrade, never block.

    Endpoint: ``GET https://openrouter.ai/api/v1/credits`` →
    ``{"data": {"total_credits": <float>, "total_usage": <float>}}``.
    """
    if not api_key:
        return None
    try:
        import requests
        resp = requests.get(
            _CREDITS_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )
        if getattr(resp, "status_code", None) != 200:
            return None
        data = resp.json()
        d = (data or {}).get("data") or {}
        balance = float(d.get("total_credits")) - float(d.get("total_usage"))
    except Exception:
        return None
    with _state_lock:
        global _last_balance
        _last_balance = balance
    return balance


def get_cached_balance() -> Optional[float]:
    with _state_lock:
        return _last_balance


# ── Preflight guard ──────────────────────────────────────────────────────────
def _threshold(config: Optional[dict], key: str, default: float) -> float:
    try:
        val = (config or {}).get(key)
        if val is None:
            return default
        return float(val)
    except (TypeError, ValueError):
        return default


def check_credit_guard(
    api_key: str,
    config: Optional[dict],
    notify_fn: Optional[Callable[[str], Any]] = None,
) -> str:
    """Return ``"ok" | "warn" | "halt"`` for the current OpenRouter balance.

    - balance ``None`` (fetch failed)     → ``"ok"``  (fail-open; reactive 402
      handling still protects us — the guard NEVER blocks on its own failure).
    - balance ``<=`` halt threshold        → ``"halt"``
    - balance ``<=`` warn threshold        → ``"warn"`` (fires ``notify_fn``
      AT MOST ONCE per process via the warn latch).
    - otherwise                            → ``"ok"``.
    """
    global _warn_latched
    warn_usd = _threshold(config, _WARN_KEY, _DEFAULT_WARN_USD)
    halt_usd = _threshold(config, _HALT_KEY, _DEFAULT_HALT_USD)

    balance = get_balance(api_key)
    if balance is None:
        return "ok"

    if balance <= halt_usd:
        return "halt"

    if balance <= warn_usd:
        with _state_lock:
            already = _warn_latched
            _warn_latched = True
        if not already and notify_fn is not None:
            try:
                notify_fn(
                    f"OpenRouter credit low: ${balance:.2f} remaining "
                    f"(warn <= ${warn_usd:.2f}, halt <= ${halt_usd:.2f}); "
                    "top up soon to avoid a trading halt."
                )
            except Exception:
                pass
        return "warn"

    return "ok"


# ── 402 de-cliff helpers ─────────────────────────────────────────────────────
def parse_affordable_tokens(body_text: Any) -> Optional[int]:
    """Extract N from a 402 body containing ``"can only afford N"``; None if
    the pattern is absent or unparseable."""
    if not body_text:
        return None
    match = _AFFORD_RE.search(str(body_text))
    if not match:
        return None
    try:
        return int(match.group(1))
    except (TypeError, ValueError):
        return None


def decliff_max_tokens(affordable_n: int) -> int:
    """The clamped ``max_tokens`` for the one de-cliff retry: a small headroom
    below what the balance can afford, floored at 2048 so we never send a
    uselessly-tiny cap."""
    try:
        n = int(affordable_n)
    except (TypeError, ValueError):
        n = 0
    return max(2048, n - 512)


def note_affordable_tokens(affordable_n: int) -> None:
    """Cache the affordable-token budget learned from a 402 so future uncapped
    calls can be pre-clamped before they hit the wire."""
    global _last_affordable_tokens
    try:
        n = int(affordable_n)
    except (TypeError, ValueError):
        return
    with _state_lock:
        _last_affordable_tokens = n


def get_cached_affordable_tokens() -> Optional[int]:
    with _state_lock:
        return _last_affordable_tokens


def preclamp_max_tokens(current_max: Any, cached_affordable_tokens: Optional[int]) -> int:
    """Proactively clamp ``current_max`` down to the last-known affordable
    budget (minus the de-cliff headroom). Only ever REDUCES — an explicit
    caller cap smaller than the affordable budget is left untouched — and is a
    no-op when no affordable budget is cached (``None``). Never raises; returns
    ``current_max`` on bad input."""
    if cached_affordable_tokens is None:
        return current_max
    try:
        cur = int(current_max or 0)
        aff = int(cached_affordable_tokens)
    except (TypeError, ValueError):
        return current_max
    if cur <= 0:
        return current_max
    return min(cur, max(2048, aff - 512))


# ── Broker wiring helpers (testable; broker.py stays thin) ───────────────────
def _iter_configs(specs: Any) -> Iterable[dict]:
    """Yield config dicts from ``specs`` which may be: a single config dict, a
    single strategy spec ({"config": {...}}), or a list/tuple of either."""
    if specs is None:
        return
    if isinstance(specs, dict):
        candidates = [specs]
    elif isinstance(specs, (list, tuple)):
        candidates = list(specs)
    else:
        return
    for item in candidates:
        if not isinstance(item, dict):
            continue
        inner = item.get("config")
        if isinstance(inner, dict):
            yield inner
        else:
            yield item


def find_openrouter_credentials(specs: Any) -> Optional[tuple]:
    """Scan strategy specs/config dicts for the first role configured with
    provider ``openrouter`` and a non-empty api key. Returns
    ``(api_key, config)`` (config carries the warn/halt threshold keys), else
    None. The account balance is shared across roles, so the FIRST OpenRouter
    key found is sufficient to query it."""
    for cfg in _iter_configs(specs):
        for key, val in list(cfg.items()):
            if key.endswith("llm_provider") and str(val or "").strip().lower() == "openrouter":
                prefix = key[: -len("llm_provider")]
                api_key = str(cfg.get(prefix + "llm_api_key") or "").strip()
                if api_key:
                    return api_key, cfg
    return None


def run_credit_guard(
    specs: Any,
    notify_fn: Optional[Callable[[str], Any]] = None,
) -> str:
    """Broker entry point: locate OpenRouter creds among ``specs`` and run the
    preflight guard. Returns ``"ok"|"warn"|"halt"``, or ``"skip"`` when no
    OpenRouter role is configured (nothing to guard)."""
    found = find_openrouter_credentials(specs)
    if not found:
        return "skip"
    api_key, cfg = found
    return check_credit_guard(api_key, cfg, notify_fn)
