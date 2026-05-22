"""Critical-class LLM failure classifier + per-process state.

Pure (no I/O, no DB, no Discord). Two callers:
  1. backend/llm_utils.py — the retry wrapper polls classify() after each call
  2. backend/broker.py — catches LLMCriticalFailure raised by the wrapper

Design doc: in-conversation sections 1–6 (approved 2026-05-22).
"""
from __future__ import annotations

import os
import re
import threading
from typing import Any


_GUARD_DISABLED = os.environ.get("LLM_CRITICAL_GUARD_DISABLED", "").strip() in ("1", "true", "yes")


# Module-level state (deliberately NOT threading.local — session #9 lesson:
# ThreadPoolExecutor workers don't see thread-local data; module state with
# an RLock is the safer default).
_state_lock = threading.RLock()
_consecutive_5xx: dict[tuple[str, str], int] = {}
_already_raised = False


# --- Classifier patterns ---

_RX_AZURE_BLOCKED = re.compile(r"(temporarily blocked|unusual behavior|abuse)", re.I)
_RX_AUTH = re.compile(r"(invalid_api_key|incorrect API key|authentication.{0,20}failed)", re.I)
_RX_CODEX_QUOTA = re.compile(r"(usage_limit_reached|quota.{0,30}exhausted|weekly quota)", re.I)


def classify(
    *,
    status: int | None,
    body: str | None,
    exc: BaseException | None = None,
    provider: str,
    model: str = "",
) -> tuple[str, bool]:
    """Return (class_tag, is_critical).

    Critical classes that abort:
      - azure_403_blocked   (azure only; 403 + 'temporarily blocked' body)
      - auth_failure        (any provider; 401 OR auth-failed body)
      - codex_quota_exhausted (codex_cli only; quota body)
      - provider_5xx_persistent (any provider; 3+ consecutive 5xx — set by update_consecutive_state)

    Returns ('none', False) for everything else.
    """
    if _GUARD_DISABLED:
        return "none", False

    body_l = (body or "").lower() if body is not None else ""
    prov = (provider or "").lower()

    # 1. Azure 403 'temporarily blocked' — provider-scoped
    if status == 403 and prov == "azure" and _RX_AZURE_BLOCKED.search(body_l):
        return "azure_403_blocked", True

    # 2. Auth failure — any provider, by status OR body match
    if status == 401:
        return "auth_failure", True
    if _RX_AUTH.search(body_l):
        return "auth_failure", True

    # 3. Codex-CLI quota exhausted — provider-scoped
    if prov in ("codex_cli", "codex-cli") and _RX_CODEX_QUOTA.search(body_l):
        return "codex_quota_exhausted", True

    # 4. Provider 5xx persistent — counter must already be ≥3 (caller increments
    # via update_consecutive_state BEFORE calling classify on the same response).
    # Strictly per-(provider, model): an Azure outage must not make a Gemini
    # first-5xx critical (Bug 2 fix — was: any(v >= 3 for v in values())).
    if status is not None and 500 <= int(status) < 600:
        with _state_lock:
            if _consecutive_5xx.get((provider, model), 0) >= 3:
                return "provider_5xx_persistent", True

    return "none", False


def update_consecutive_state(
    *,
    tag: str,
    status: int | None,
    provider: str,
    model: str,
) -> None:
    """Update the 5xx-consecutive counter per (provider, model).

    Rules:
      - 5xx → counter += 1 for (provider, model)
      - Any non-5xx (including 200) → counter reset to 0 ONLY for this
        (provider, model). A successful Gemini call says nothing about
        Azure's outage history; cross-provider reset (Bug 3) would zero out
        an Azure near-trip just because Gemini is healthy.
    """
    if _GUARD_DISABLED:
        return
    if status is None:
        return
    with _state_lock:
        if 500 <= int(status) < 600:
            key = (provider, model)
            _consecutive_5xx[key] = _consecutive_5xx.get(key, 0) + 1
        else:
            key = (provider, model)
            if key in _consecutive_5xx:
                del _consecutive_5xx[key]


def is_immediately_fatal(class_tag: str) -> bool:
    """True for classes that fail-fast regardless of counter state."""
    return class_tag in {"azure_403_blocked", "auth_failure", "codex_quota_exhausted"}


def is_consecutive_class(class_tag: str) -> bool:
    """True for classes that ONLY fire after a consecutive-counter trip."""
    return class_tag == "provider_5xx_persistent"


def mark_raised() -> None:
    """Set the once-per-process raised flag. Idempotent."""
    global _already_raised
    with _state_lock:
        _already_raised = True


def was_already_raised() -> bool:
    with _state_lock:
        return _already_raised


def reset_state() -> None:
    """Reset all module state. Called by backtest engine entrypoint per-run
    and by tests via the fixture."""
    global _already_raised
    with _state_lock:
        _consecutive_5xx.clear()
        _already_raised = False


class LLMCriticalFailure(BaseException):
    """Raised by the retry wrapper when 4 consecutive attempts on the same
    LLM call all classify as critical. Caught at broker.py's outer loop;
    triggers backtest_critical_abort or live_critical_abort.

    Inherits from BaseException (not Exception) so that bare ``except Exception:``
    blocks scattered throughout strategy code (e.g. graph_nexus_analysis.py)
    cannot silently swallow it before broker.py's outer-loop catch can route
    it to backtest_critical_abort / live_critical_abort.
    """

    _already_raised = False  # class-level mirror of module flag (test contract)

    def __init__(
        self,
        *,
        class_tag: str,
        provider: str,
        model: str,
        attribution: dict[str, Any],
        attempts: list[dict[str, Any]],
    ) -> None:
        super().__init__(
            f"LLM critical failure after {len(attempts)} attempts: "
            f"class={class_tag} provider={provider} model={model}"
        )
        self.class_tag = class_tag
        self.provider = provider
        self.model = model
        self.attribution = dict(attribution or {})
        self.attempts = list(attempts or [])
