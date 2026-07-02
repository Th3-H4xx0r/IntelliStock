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
# Match auth failures across providers. Each branch is a real on-wire shape:
#   * invalid_api_key            — OpenAI / Azure
#   * API_KEY_INVALID            — Gemini ErrorInfo.reason
#   * api[ _]key[ _]not[ _]valid — Gemini human-readable + variants
#   * incorrect API key          — OpenAI 401 body
#   * authentication failed      — generic
# Gemini returns 400 (not 401) with this body when the key is wrong, so the
# body-pattern path is the only way the critical-guard catches it. Without
# this, backtests retry 6× per call and never pause.
_RX_AUTH = re.compile(
    r"("
    r"invalid_api_key"
    r"|API_KEY_INVALID"
    r"|api[ _]key[ _]not[ _]valid"
    r"|incorrect API key"
    r"|authentication.{0,20}failed"
    r"|unauthorized"
    # Bedrock auth/authorization error codes (data-plane Converse + control
    # plane). Bedrock returns these as the Error.Code; a bad/expired API key
    # surfaces as AccessDenied / UnrecognizedClient / ExpiredToken — without
    # these, backtests retry every call instead of pausing.
    r"|AccessDeniedException"
    r"|UnrecognizedClientException"
    r"|InvalidSignatureException"
    r"|ExpiredTokenException"
    r"|ForbiddenException"
    r")",
    re.I,
)
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


# Article-enrichment roles whose critical LLM failure must DEGRADE the run
# (empty signal) rather than halt live trading. Everything else — decision
# roles (sentiment/overlay/event_maintenance/generic ""), None, unknown — is
# halt-worthy by default (fail-safe).
#
# Incident 2026-06-22/23: the `macro_article` role (codex-cli, gpt-5.4-mini)
# hit weekly quota, LLMCriticalFailure bubbled to live_critical_abort.handle(),
# and the whole-instance kill switch flipped (runCommand=False) — alpaca-main
# lost two trading days. Macro/company articles are an enrichment signal; a
# codex-quota exhaustion there must never kill decision-making trades.
_NON_HALT_WORTHY_ROLES = frozenset({
    "macro_article",
    "company_article",
    "lookback_macro_article",
    "lookback_company_article",
})


def role_is_halt_worthy(role: str | None) -> bool:
    """Return True if a critical LLM failure in this role should HALT the
    instance, False if it should only DEGRADE the run.

    Fail-safe: only the four explicit article-enrichment roles degrade. Any
    None / unknown / decision role halts — the dangerous bug to avoid is the
    INVERSE (a decision-role failure that no longer halts), so unrecognised
    provenance defaults to halt.
    """
    if role is None:
        return True
    return str(role).strip().lower() not in _NON_HALT_WORTHY_ROLES


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
        role: str | None = None,
    ) -> None:
        super().__init__(
            f"LLM critical failure after {len(attempts)} attempts: "
            f"class={class_tag} provider={provider} model={model} role={role}"
        )
        self.class_tag = class_tag
        self.provider = provider
        self.model = model
        self.attribution = dict(attribution or {})
        self.attempts = list(attempts or [])
        # Optional caller role (e.g. "macro_article"). Consumed by
        # role_is_halt_worthy() so article-enrichment failures degrade rather
        # than halt. None (default) is fail-safe → halt-worthy.
        self.role = role
