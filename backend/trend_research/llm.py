"""Quota-aware structured LLM calls for the TR research stages (plan Task 6).

``StageCaller.call`` wraps ``llm_utils.call_structured_llm_by_provider``
with tools off and the prompt cache on, and turns its silent ``None`` into
one of three outcomes the ledger builder (Task 7) acts on:

* ``QuotaExhausted(reset_at)`` -- a claude-cli subscription quota or session
  limit, or an HTTP 429. llm_utils may RAISE ``ClaudeCliRateLimitError`` or
  CATCH it and return None with the text left in its per-thread last-call
  metadata; both are detected. The metadata is cleared before each call so
  an earlier call's error is never read as this one's. ``reset_at`` comes
  from CC's "resets 11:30am (UTC)" wording or a ``retry-after`` seconds
  value; None when the text gives neither (the builder then waits a default
  interval). The builder discards the week and retries it after the reset.
* ``BudgetExceeded`` -- ``max_calls`` already made (spec C13). Every
  attempt counts, a failed or quota-hit one included, so a loop of failures
  cannot outrun the cap.
* ``StageFailed`` -- no valid result after llm_utils' own retries, or a
  reply that does not validate against ``output_type``.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ValidationError

import llm_utils
from trend_research import prompts

__all__ = ["StageCaller", "QuotaExhausted", "BudgetExceeded", "StageFailed",
           "MAX_OUTPUT_TOKENS", "SUPPORTED_PROVIDERS", "parse_reset_at", "is_quota_error"]

log = logging.getLogger(__name__)

SUPPORTED_PROVIDERS = ("claude-cli", "anthropic")

#: Output-token budget per stage; deep research needs room to reason.
MAX_OUTPUT_TOKENS = {"scan": 4000, "candidates": 2000, "research": 1500, "book": 2000}
DEFAULT_TIMEOUT_SEC = 300
TEMPERATURE = 0.2
#: Validation retries inside llm_utils before a stage counts as failed.
OUTPUT_RETRIES = 2

_ERR_CHARS = 300

# Phrases only a quota or rate-limit failure produces.
_QUOTA_NEEDLES = ("subscription quota", "quota hit", "quota exceeded", "session limit",
                  "usage limit", "usage_limit", "hit your limit", "hit my limit",
                  "rate limit", "rate_limit", "rate-limit", "too many requests",
                  "subscription limit")
# A bare "quota" is a quota failure unless the text is a validation error,
# which can quote the model's own reply ("... export quotas ...").
_VALIDATION_HINTS = ("validat", "schema", "did not match")
_HTTP_429 = re.compile(r"\b429\b")
_RESET = re.compile(
    r"resets?\s+(?:at\s+)?"
    r"(?:(?P<mon>[A-Za-z]{3,9})\.?\s+(?P<day>\d{1,2}),?\s+(?:at\s+)?)?"
    r"(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*(?P<ap>[ap]\.?m\.?)?"
    r"(?:\s*\((?P<tz>[^)]+)\))?",
    re.I)
_RETRY_AFTER = re.compile(r"retry[-_ ]after\D{0,5}(\d+(?:\.\d+)?)", re.I)
_MONTHS = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}


class QuotaExhausted(Exception):
    """The provider's quota or rate limit is hit; retry after ``reset_at``."""

    def __init__(self, reset_at: datetime | None, message: str = ""):
        super().__init__(message or "quota exhausted")
        self.reset_at = reset_at


class BudgetExceeded(Exception):
    """``max_calls`` reached for this build run."""


class StageFailed(Exception):
    """No valid structured result for the stage."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def is_quota_error(text: str, exc: BaseException | None = None) -> bool:
    """A quota, session-limit or HTTP 429 failure, by exception type or text."""
    if exc is not None and any(c.__name__ == "ClaudeCliRateLimitError"
                               for c in type(exc).__mro__):
        return True
    lower = (text or "").lower()
    if any(n in lower for n in _QUOTA_NEEDLES) or _HTTP_429.search(lower):
        return True
    return "quota" in lower and not any(h in lower for h in _VALIDATION_HINTS)


def _zone(name: str | None):
    if not name:
        return timezone.utc
    try:
        return ZoneInfo(name.strip())
    except (ZoneInfoNotFoundError, ValueError):
        return timezone.utc


def parse_reset_at(text: str, now: datetime) -> datetime | None:
    """When the quota resets, in UTC: CC's "resets 11:30am (UTC)" (the next
    such wall-clock time after ``now``; UTC when no zone is given), else
    ``now`` + a ``retry-after`` seconds value, else None."""
    m = _RESET.search(text or "")
    if m:
        hour, minute = int(m.group("h")), int(m.group("m") or 0)
        ap = (m.group("ap") or "").replace(".", "").lower()
        if ap:
            hour = hour % 12 + (12 if ap == "pm" else 0)
        if hour < 24 and minute < 60:
            tz = _zone(m.group("tz"))
            local_now = now.astimezone(tz)
            mon = _MONTHS.get((m.group("mon") or "")[:3].lower())
            try:
                if mon:
                    at = local_now.replace(month=mon, day=int(m.group("day")), hour=hour,
                                           minute=minute, second=0, microsecond=0)
                    if at < local_now - timedelta(days=1):
                        at = at.replace(year=at.year + 1)
                else:
                    at = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                    if at <= local_now:
                        at = (at.replace(tzinfo=None) + timedelta(days=1)).replace(tzinfo=tz)
                return at.astimezone(timezone.utc)
            except ValueError:
                pass
    r = _RETRY_AFTER.search(text or "")
    if r:
        return (now + timedelta(seconds=float(r.group(1)))).astimezone(timezone.utc)
    return None


def _clear_last_call_metadata() -> None:
    # llm_utils returns None on failure and keeps the reason in a per-thread
    # slot that not every early-return path overwrites. Clearing it first
    # means what we read after the call belongs to this call.
    slot = getattr(llm_utils, "_LAST_STRUCTURED_LLM_CALL", None)
    if slot is not None:
        slot.data = {}


class StageCaller:
    """One build run's LLM access: a provider, a model and a call budget."""

    def __init__(self, provider: str, model: str, api_key: str | None, *, max_calls: int,
                 provider_config: dict | None = None,
                 timeout_sec: int = DEFAULT_TIMEOUT_SEC,
                 now: Callable[[], datetime] = _utcnow):
        p = (provider or "").strip().lower()
        if p not in SUPPORTED_PROVIDERS:
            raise ValueError("provider must be one of %s, got %r" % (SUPPORTED_PROVIDERS,
                                                                     provider))
        if not model:
            raise ValueError("model is required")
        if p == "anthropic" and not api_key:
            raise ValueError("the anthropic provider needs an API key")
        self.provider = p
        self.model = model
        self.api_key = api_key or ""
        self.max_calls = int(max_calls)
        self.provider_config = provider_config
        self.timeout_sec = timeout_sec
        self._now = now
        self.calls_used = 0

    def call(self, prompt: str, output_type: type[BaseModel], *,
             max_output_tokens: int) -> BaseModel:
        """A validated ``output_type`` for ``prompt``, or raise
        ``QuotaExhausted`` / ``BudgetExceeded`` / ``StageFailed``."""
        if self.calls_used >= self.max_calls:
            raise BudgetExceeded("LLM call budget of %d reached" % self.max_calls)
        self.calls_used += 1
        _clear_last_call_metadata()
        try:
            result = llm_utils.call_structured_llm_by_provider(
                self.provider, self.api_key, self.model, prompt, output_type,
                system_prompt=prompts.SYSTEM_PROMPT, tools=None,
                max_output_tokens=max_output_tokens, timeout_sec=self.timeout_sec,
                retries=1, output_retries=OUTPUT_RETRIES, temperature=TEMPERATURE,
                provider_config=self.provider_config, use_prompt_cache=True)
        except Exception as exc:
            text = "%s: %s" % (type(exc).__name__, exc)
            if is_quota_error(text, exc):
                raise self._quota(text) from exc
            raise StageFailed(text[:_ERR_CHARS]) from exc

        if result is None:
            meta = llm_utils.get_last_structured_llm_call_metadata() or {}
            text = str(meta.get("error") or "")
            if is_quota_error(text):
                raise self._quota(text)
            raise StageFailed((text or "no structured result")[:_ERR_CHARS])

        if isinstance(result, output_type):
            return result
        try:
            if isinstance(result, BaseModel):
                result = result.model_dump()
            return output_type.model_validate(result)
        except ValidationError as exc:
            raise StageFailed("reply did not validate as %s: %s"
                              % (output_type.__name__, str(exc)[:_ERR_CHARS])) from exc

    def _quota(self, text: str) -> QuotaExhausted:
        reset_at = parse_reset_at(text, self._now())
        log.warning("TR LLM quota hit (provider=%s, model=%s, reset_at=%s)",
                    self.provider, self.model, reset_at.isoformat() if reset_at else "unknown")
        return QuotaExhausted(reset_at, text[:_ERR_CHARS])
