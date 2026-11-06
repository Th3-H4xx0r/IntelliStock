"""Quota-aware structured LLM calls for the TR research stages (plan Task 6).

``StageCaller.call`` returns a validated ``output_type`` for a prompt, or
raises one of four outcomes the ledger builder (Task 7) acts on:

* ``QuotaExhausted(reset_at)`` -- a claude-cli subscription quota or session
  limit, an Anthropic 429 that outlasts its retries, or exhausted Anthropic
  credit. llm_utils may RAISE ``ClaudeCliRateLimitError`` or CATCH it and
  return None with the text left in its per-thread last-call metadata; both
  are detected. The metadata is cleared before each call so an earlier
  call's error is never read as this one's. ``reset_at`` comes from CC's
  "resets 11:30am (UTC)" / "resets in 3 hours" wording or a ``retry-after``
  seconds value; None when the text gives neither (the builder then waits a
  default interval). The builder discards the week and retries it after the
  reset.
* ``BudgetExceeded`` -- ``max_calls`` already made (spec C13). A slot is
  reserved before each provider attempt, so no attempt starts once the cap
  is reached. Every attempt counts: each Anthropic HTTP request, and each
  claude-cli spawn llm_utils reports in ``attempted_models`` (its internal
  retry adds one more spawn than the reserved slot, so the count can pass
  the cap by one). A reply served from the prompt cache costs nothing.
* ``TransientError`` -- the call never produced a model answer: a timeout,
  a CLI crash or empty stdout, a network error, an HTTP 5xx / 529
  (overloaded) after retries, an auth failure, a missing CLI feature. The
  builder writes nothing for the week and stops with a resumable reason.
* ``StageFailed`` -- the model answered but no valid result came out: a
  reply that does not validate against ``output_type`` (after llm_utils'
  own retries), a refusal, a truncated reply, a rejected request.

Providers (spec 5.10):

* ``claude-cli`` goes through ``llm_utils.call_structured_llm_by_provider``.
  Its ``extra_args`` may carry only ``--effort`` and ``--max-budget-usd``
  (ruling R29: no ``--fallback-model`` or other model override, whose
  answers the ledger would record under the configured model), the model
  must be a full model id (an alias such as ``sonnet`` moves to newer
  models over time), and every spawn adds ``--safe-mode`` -- Claude Code
  then loads none of the operator's CLAUDE.md, skills, plugins, hooks or MCP
  servers, which would otherwise reach dated prompts unseen. A CLI without
  ``--safe-mode`` is refused (``TransientError``).
* ``anthropic`` calls the Messages API directly (llm_utils has no Anthropic
  backend and would route the key to Gemini): one tool whose
  ``input_schema`` is the output schema, forced with ``tool_choice``.
  429 / 529 / 5xx / network errors are retried with backoff (honouring
  ``retry-after``). The API key is sent only in the ``x-api-key`` header
  and scrubbed from every error text.

Prompt cache. Every prompt gets a first line ``TR prompt_version=<v>
schema=<sha8> system=<sha8>`` (the output type's JSON schema and the system
prompt), so the cache -- keyed on the prompt text -- misses when any of them
changes. Anthropic replies are cached under ``anthropic:<model>`` and never
shared with claude-cli.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ValidationError

import llm_utils
from trend_research import prompts

__all__ = ["StageCaller", "QuotaExhausted", "BudgetExceeded", "StageFailed", "TransientError",
           "MAX_OUTPUT_TOKENS", "SUPPORTED_PROVIDERS", "parse_reset_at", "is_quota_error",
           "classify_failure", "prompt_tag", "cli_extra_args"]

log = logging.getLogger(__name__)

SUPPORTED_PROVIDERS = ("claude-cli", "anthropic")

#: Output-token budget per stage (Anthropic honours it; claude-cli ignores
#: it). A scan of ~15 trends with evidence ids runs ~3.3k tokens, a list of
#: ~38 candidates ~2.4k.
MAX_OUTPUT_TOKENS = {"scan": 8000, "candidates": 4000, "research": 2500, "book": 3000}
DEFAULT_TIMEOUT_SEC = 300
TEMPERATURE = 0.2

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
ANTHROPIC_TOOL = "submit_result"
#: HTTP attempts per Anthropic call (429 / 529 / 5xx / network), and backoff.
ANTHROPIC_ATTEMPTS = 5
ANTHROPIC_BACKOFF_BASE = 5.0
ANTHROPIC_BACKOFF_CAP = 120.0
#: claude-cli extra_args flags TR accepts (ruling R29).
CLI_ALLOWED_FLAGS = ("--effort", "--max-budget-usd")
CLI_ISOLATION_FLAG = "--safe-mode"
#: Model names that are aliases, not dated model ids.
CLI_MODEL_ALIASES = frozenset({"opus", "sonnet", "haiku", "default", "best", "opusplan",
                               "sonnet[1m]", "opus[1m]"})

_ERR_CHARS = 300

# Phrases only claude-cli's own quota failures produce. They are trusted
# even inside a validation error.
_CC_QUOTA_PHRASES = ("subscription quota", "hit your limit", "session limit")
# Phrases a provider's quota or rate-limit failure produces, but which a
# model's reply can also contain ("$429 million", "export quotas", "the rate
# limit on ...", "debt limit reached"). They count only when the text is not
# a validation error. "overloaded" is deliberately absent: an overload is
# retried, not waited out.
_QUOTA_NEEDLES = ("quota", "usage limit", "usage_limit", "hit my limit", "rate limit",
                  "rate_limit", "rate-limit", "too many requests", "subscription limit",
                  "limit reached", "rate_limit_error")
# Signs that the text is a validation error, which can quote the reply.
_VALIDATION_HINTS = ("validat", "schema", "did not match", "pydantic", "not valid json",
                     "payload_preview", "json_invalid")
# A reply came back but carried no usable result.
_NO_RESULT_HINTS = ("empty result", "empty content", "refusal", "truncated")
_HTTP_429 = re.compile(r"\b429\b")
_RESET = re.compile(
    r"resets?\s+(?:at\s+)?"
    r"(?:(?P<mon>[A-Za-z]{3,9})\.?\s+(?P<day>\d{1,2}),?\s+(?:at\s+)?)?"
    r"(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*(?P<ap>[ap]\.?m\.?)?"
    r"(?:\s*\((?P<tz>[^)]+)\))?",
    re.I)
_RESET_IN = re.compile(r"resets?\s+in\s+(?P<n>\d+(?:\.\d+)?)\s*"
                       r"(?P<unit>min(?:ute)?s?|h(?:ou)?rs?|hours?|days?)\b", re.I)
_RETRY_AFTER = re.compile(r"retry[-_ ]after\D{0,5}(\d+(?:\.\d+)?)", re.I)
_MONTHS = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}
_CREDIT_NEEDLES = ("credit balance", "insufficient credit", "billing")


class QuotaExhausted(Exception):
    """The provider's quota or rate limit is hit; retry after ``reset_at``."""

    def __init__(self, reset_at: datetime | None, message: str = ""):
        super().__init__(message or "quota exhausted")
        self.reset_at = reset_at


class BudgetExceeded(Exception):
    """``max_calls`` reached for this build run."""


class StageFailed(Exception):
    """The model answered, but no valid structured result came out."""


class TransientError(Exception):
    """The call produced no model answer (timeout, crash, network, 5xx,
    auth); nothing about the week is known, so nothing may be written."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _sha8(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


def prompt_tag(output_type: type[BaseModel], prompt_version: str) -> str:
    """The first line of every TR prompt: what the cached reply depends on
    beyond the stage text."""
    schema = json.dumps(output_type.model_json_schema(), sort_keys=True, separators=(",", ":"))
    return "TR prompt_version=%s schema=%s system=%s" % (prompt_version, _sha8(schema),
                                                         _sha8(prompts.SYSTEM_PROMPT))


def is_quota_error(text: str, exc: BaseException | None = None) -> bool:
    """A quota, session-limit or HTTP 429 failure, by exception type or text."""
    if exc is not None and any(c.__name__ == "ClaudeCliRateLimitError"
                               for c in type(exc).__mro__):
        return True
    lower = (text or "").lower()
    if any(n in lower for n in _CC_QUOTA_PHRASES):
        return True
    if any(h in lower for h in _VALIDATION_HINTS):
        return False
    return any(n in lower for n in _QUOTA_NEEDLES) or bool(_HTTP_429.search(lower))


def classify_failure(text: str, exc: BaseException | None = None) -> str:
    """``"quota"``, ``"validation"`` (the model answered, but not usably) or
    ``"transient"`` (no answer: timeout, crash, network, overload, auth)."""
    if is_quota_error(text, exc):
        return "quota"
    if exc is not None and (isinstance(exc, ValidationError) or any(
            c.__name__ == "ClaudeCliValidationError" for c in type(exc).__mro__)):
        return "validation"
    lower = (text or "").lower()
    if any(h in lower for h in _VALIDATION_HINTS + _NO_RESULT_HINTS):
        return "validation"
    return "transient"


def _zone(name: str | None):
    if not name:
        return timezone.utc
    try:
        return ZoneInfo(name.strip())
    except (ZoneInfoNotFoundError, ValueError):
        return timezone.utc


def parse_reset_at(text: str, now: datetime) -> datetime | None:
    """When the quota resets, in UTC: CC's "resets 11:30am (UTC)" (the next
    such wall-clock time after ``now``; UTC when no zone is given), "resets
    in 3 hours", else ``now`` + a ``retry-after`` seconds value, else None.

    CC prints the reset time in the host's local zone and names the zone in
    parentheses; a bare time is read as UTC because the backend hosts run
    on UTC. On a host with another local zone and no zone in the text, the
    reset would be off by the UTC offset."""
    rel = _RESET_IN.search(text or "")
    if rel:
        n, unit = float(rel.group("n")), rel.group("unit").lower()
        seconds = n * (86400 if unit.startswith("d") else 3600 if unit.startswith("h") else 60)
        return (now + timedelta(seconds=seconds)).astimezone(timezone.utc)
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


def _last_meta() -> dict:
    return llm_utils.get_last_structured_llm_call_metadata() or {}


# -- claude-cli isolation (ruling R29) ---------------------------------------------------

def cli_extra_args(raw) -> list:
    """The Models row's claude-cli ``extra_args`` as an argv list, keeping
    only ``CLI_ALLOWED_FLAGS``; ValueError for any other flag (notably
    ``--fallback-model``: its answers would be recorded under the configured
    model). Values go through claude_cli_provider's own validators."""
    if raw in (None, "", [], ()):
        return []
    from chatbot.claude_cli_provider import validate_extra_args
    text = raw if isinstance(raw, str) else " ".join(str(t) for t in raw)
    tokens = validate_extra_args(text)
    out, i = [], 0
    while i < len(tokens):
        flag = tokens[i]
        if flag not in CLI_ALLOWED_FLAGS:
            raise ValueError("TR refuses claude-cli extra_args flag %r: only %s are allowed "
                             "(no fallback or model override, ruling R29)"
                             % (flag, ", ".join(CLI_ALLOWED_FLAGS)))
        out += tokens[i:i + 2]
        i += 2
    return out


def _default_cli_help(cli_path: str) -> str:
    from chatbot.claude_cli_provider import _resolve_cli_path
    proc = subprocess.run([_resolve_cli_path(cli_path or "claude"), "--help"],
                          capture_output=True, text=True, timeout=60, check=False)
    return (proc.stdout or "") + (proc.stderr or "")


_CLI_HELP_CACHE: dict = {}
_CLI_HELP_LOCK = threading.Lock()


def _isolation_args(cli_path: str, cli_help: Callable[[str], str]) -> list:
    with _CLI_HELP_LOCK:
        if cli_path not in _CLI_HELP_CACHE:
            try:
                _CLI_HELP_CACHE[cli_path] = cli_help(cli_path) or ""
            except Exception as exc:          # noqa: BLE001 -- reported, not cached
                raise TransientError("could not run %s --help to check for %s: %s: %s"
                                     % (cli_path, CLI_ISOLATION_FLAG, type(exc).__name__,
                                        str(exc)[:200])) from None
        text = _CLI_HELP_CACHE[cli_path]
    if CLI_ISOLATION_FLAG not in text:
        raise TransientError(
            "the claude CLI (%s) has no %s flag; TR will not run claude-cli with the "
            "operator's CLAUDE.md, hooks, plugins or MCP servers (ruling R29). Upgrade "
            "Claude Code in the backend image." % (cli_path, CLI_ISOLATION_FLAG))
    return [CLI_ISOLATION_FLAG]


# -- Anthropic Messages API ---------------------------------------------------------------

def _default_http_post(url, *, headers, json, timeout):
    import httpx
    return httpx.post(url, headers=headers, json=json, timeout=timeout)


def _cache_get(prompt: str, key: str) -> str | None:
    try:
        return llm_utils._check_prompt_cache(prompt, key, "", force_cache=True)
    except Exception:                         # noqa: BLE001 -- the cache is a speedup
        return None


def _cache_put(prompt: str, key: str, raw: str) -> None:
    try:
        llm_utils._store_prompt_cache(prompt, key, "", raw, force_cache=True)
    except Exception:                         # noqa: BLE001
        pass


def _inline_refs(schema: dict) -> dict:
    """``schema`` with ``#/$defs/...`` references inlined (TR schemas are not
    recursive), so the tool's ``input_schema`` is self-contained."""
    defs = schema.get("$defs") or {}

    def walk(node, depth=0):
        if depth > 32:
            raise ValueError("schema nests too deeply (recursive $ref?)")
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/$defs/"):
                target = walk(defs[ref.rsplit("/", 1)[1]], depth + 1)
                rest = {k: walk(v, depth + 1) for k, v in node.items() if k != "$ref"}
                return {**target, **rest}
            return {k: walk(v, depth + 1) for k, v in node.items() if k != "$defs"}
        if isinstance(node, list):
            return [walk(v, depth + 1) for v in node]
        return node
    return walk(schema)


def _header(resp, name: str) -> str | None:
    headers = getattr(resp, "headers", None) or {}
    try:
        value = headers.get(name)
    except Exception:                         # noqa: BLE001
        value = None
    return str(value) if value not in (None, "") else None


class StageCaller:
    """One build run's LLM access: a provider, a model and a call budget."""

    def __init__(self, provider: str, model: str, api_key: str | None, *, max_calls: int,
                 provider_config: dict | None = None,
                 timeout_sec: int = DEFAULT_TIMEOUT_SEC,
                 now: Callable[[], datetime] = _utcnow,
                 prompt_version: str = prompts.PROMPT_VERSION,
                 http_post: Callable | None = None,
                 sleep: Callable[[float], None] = time.sleep,
                 cli_help: Callable[[str], str] | None = None):
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
        self.provider_config = dict(provider_config or {})
        self.timeout_sec = timeout_sec
        self.prompt_version = prompt_version
        self._now = now
        self._http_post = http_post or _default_http_post
        self._sleep = sleep
        self._cli_help = cli_help or _default_cli_help
        self._lock = threading.Lock()
        self._calls_used = 0
        self._cli_args = None
        if p == "claude-cli":
            if model.strip().lower() in CLI_MODEL_ALIASES:
                raise ValueError("claude-cli model %r is an alias that moves to newer models; "
                                 "configure a full model id (e.g. claude-sonnet-4-5)" % model)
            self._cli_extra = cli_extra_args(self.provider_config.get("extra_args"))

    @property
    def calls_used(self) -> int:
        """Provider attempts made so far (prompt-cache hits excluded)."""
        with self._lock:
            return self._calls_used

    def _reserve(self) -> None:
        with self._lock:
            if self._calls_used >= self.max_calls:
                raise BudgetExceeded("LLM call budget of %d reached" % self.max_calls)
            self._calls_used += 1

    def _charge(self, n: int) -> None:
        with self._lock:
            self._calls_used += n

    def _scrub(self, text) -> str:
        out = str(text or "")
        if self.api_key and len(self.api_key) >= 6:
            out = out.replace(self.api_key, "[redacted]")
        return out[:_ERR_CHARS]

    def call(self, prompt: str, output_type: type[BaseModel], *,
             max_output_tokens: int) -> BaseModel:
        """A validated ``output_type`` for ``prompt``, or raise
        ``QuotaExhausted`` / ``BudgetExceeded`` / ``TransientError`` /
        ``StageFailed``."""
        full = prompt_tag(output_type, self.prompt_version) + "\n\n" + prompt
        if self.provider == "anthropic":
            return self._call_anthropic(full, output_type, max_output_tokens)
        return self._call_cli(full, output_type, max_output_tokens)

    def _raise_for(self, text: str, exc: BaseException | None = None):
        kind = classify_failure(text, exc)
        if kind == "quota":
            raise self._quota(text)
        if kind == "validation":
            raise StageFailed(self._scrub(text))
        raise TransientError(self._scrub(text or "no structured result and no error text"))

    # -- claude-cli, through llm_utils ------------------------------------------------

    def _cli_config(self) -> dict:
        if self._cli_args is None:
            cli_path = str(self.provider_config.get("cli_path") or "claude")
            self._cli_args = list(self._cli_extra) + _isolation_args(cli_path, self._cli_help)
        cfg = dict(self.provider_config)
        cfg["extra_args"] = list(self._cli_args)    # a list: used as given, already vetted
        return cfg

    def _call_cli(self, prompt, output_type, max_output_tokens):
        cfg = self._cli_config()
        self._reserve()
        _clear_last_call_metadata()
        try:
            result = llm_utils.call_structured_llm_by_provider(
                self.provider, self.api_key, self.model, prompt, output_type,
                system_prompt=prompts.SYSTEM_PROMPT, tools=None,
                max_output_tokens=max_output_tokens, timeout_sec=self.timeout_sec,
                retries=0, output_retries=0, temperature=TEMPERATURE,
                provider_config=cfg, use_prompt_cache=True)
        except (TypeError, AttributeError, NameError):
            raise                                   # a bug in the caller, not a stage failure
        except Exception as exc:
            self._charge_attempts(_last_meta())
            self._raise_for("%s: %s" % (type(exc).__name__, exc), exc)

        meta = _last_meta()
        if result is None:
            self._charge_attempts(meta)
            self._raise_for(str(meta.get("error") or ""))
        if meta.get("prompt_cache_hit"):
            self._charge(-1)                        # a replay: no real call was made
        else:
            self._charge_attempts(meta)

        if isinstance(result, output_type):
            return result
        try:
            if isinstance(result, BaseModel):
                result = result.model_dump()
            return output_type.model_validate(result)
        except ValidationError as exc:
            raise StageFailed("reply did not validate as %s: %s"
                              % (output_type.__name__, str(exc)[:_ERR_CHARS])) from exc

    def _charge_attempts(self, meta: dict) -> None:
        # One slot was reserved; llm_utils may have spawned more.
        extra = len(meta.get("attempted_models") or ()) - 1
        if extra > 0:
            self._charge(extra)

    # -- Anthropic, direct -----------------------------------------------------------------

    def _call_anthropic(self, prompt, output_type, max_output_tokens):
        cache_key = "anthropic:" + llm_utils.canonical_model_cache_key(self.model,
                                                                       self.provider_config)
        cached = _cache_get(prompt, cache_key)
        if cached:
            try:
                return output_type.model_validate_json(cached)
            except ValidationError:
                pass                                # a stale shape: ask again
        tool = {"name": ANTHROPIC_TOOL,
                "description": "Submit the %s result. Call this tool exactly once."
                               % output_type.__name__,
                "input_schema": _inline_refs(output_type.model_json_schema())}
        body = {"model": self.model, "max_tokens": int(max_output_tokens),
                "temperature": TEMPERATURE, "system": prompts.SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": prompt}],
                "tools": [tool], "tool_choice": {"type": "tool", "name": ANTHROPIC_TOOL}}
        headers = {"x-api-key": self.api_key, "anthropic-version": ANTHROPIC_VERSION,
                   "content-type": "application/json"}
        last, rate_limited, retry_after = "", False, None
        for attempt in range(ANTHROPIC_ATTEMPTS):
            self._reserve()
            try:
                resp = self._http_post(ANTHROPIC_URL, headers=headers, json=body,
                                       timeout=self.timeout_sec)
            except Exception as exc:              # noqa: BLE001 -- network: retried
                last = "network error: %s: %s" % (type(exc).__name__, self._scrub(exc))
                rate_limited, retry_after = False, None
            else:
                status = int(getattr(resp, "status_code", 0) or 0)
                try:
                    data = resp.json()
                except Exception:                 # noqa: BLE001
                    data = {}
                data = data if isinstance(data, dict) else {}
                err = data.get("error") if isinstance(data.get("error"), dict) else {}
                err_type = str(err.get("type") or "")
                msg = self._scrub(err.get("message") or "")
                if status == 200:
                    return self._parse_anthropic(prompt, cache_key, data, output_type)
                text = "anthropic HTTP %d %s: %s" % (status, err_type, msg)
                ra = _header(resp, "retry-after")
                retry_after = float(ra) if ra and re.match(r"^\d+(\.\d+)?$", ra) else None
                if status == 429 or err_type == "rate_limit_error":
                    last, rate_limited = text, True
                elif status in (500, 502, 503, 504, 529) or err_type in ("overloaded_error",
                                                                         "api_error"):
                    last, rate_limited = text, False
                elif any(n in msg.lower() for n in _CREDIT_NEEDLES):
                    raise QuotaExhausted(None, text)          # out of credit: wait it out
                elif status in (401, 403):
                    raise TransientError(text)                # fix the key, then resume
                else:
                    raise StageFailed(text)                   # a rejected request
            if attempt + 1 < ANTHROPIC_ATTEMPTS:
                delay = retry_after if retry_after is not None else min(
                    ANTHROPIC_BACKOFF_CAP, ANTHROPIC_BACKOFF_BASE * 2 ** attempt)
                log.warning("TR anthropic attempt %d failed (%s); retrying in %.0f s",
                            attempt + 1, last[:120], delay)
                self._sleep(delay)
        if rate_limited:
            reset = (self._now() + timedelta(seconds=retry_after)) if retry_after else None
            log.warning("TR anthropic rate limit outlasted %d attempts (reset_at=%s)",
                        ANTHROPIC_ATTEMPTS, reset.isoformat() if reset else "unknown")
            raise QuotaExhausted(reset, last)
        raise TransientError("%s (after %d attempts)" % (last, ANTHROPIC_ATTEMPTS))

    def _parse_anthropic(self, prompt, cache_key, data, output_type):
        stop = data.get("stop_reason")
        blocks = [b for b in data.get("content") or () if isinstance(b, dict)
                  and b.get("type") == "tool_use" and b.get("name") == ANTHROPIC_TOOL]
        if stop == "max_tokens":
            raise StageFailed("anthropic reply truncated at max_tokens")
        if not blocks:
            raise StageFailed("anthropic reply has no %s call (stop_reason=%s)"
                              % (ANTHROPIC_TOOL, stop))
        payload = blocks[0].get("input")
        try:
            out = output_type.model_validate(payload)
        except ValidationError as exc:
            raise StageFailed("reply did not validate as %s: %s"
                              % (output_type.__name__, str(exc)[:_ERR_CHARS])) from exc
        _cache_put(prompt, cache_key, out.model_dump_json())
        return out

    def _quota(self, text: str) -> QuotaExhausted:
        reset_at = parse_reset_at(text, self._now())
        log.warning("TR LLM quota hit (provider=%s, model=%s, reset_at=%s)",
                    self.provider, self.model, reset_at.isoformat() if reset_at else "unknown")
        return QuotaExhausted(reset_at, self._scrub(text))
