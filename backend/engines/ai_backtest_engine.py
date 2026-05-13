"""
AI Backtesting Agent Service.
Polls AgentControl; when running, generates strategies via LLM, creates temp strategy/instance,
runs backtests (1d, max 6 months), validates results via LLM, stores profitable ones in AIBacktestingResults.
Controlled via API/CLI: POST /agent/control { "running": true/false }.
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import random
import sys
import threading
import time
import traceback
from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# Run from backend/engines/; backend is parent for imports and cwd
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)
os.chdir(BACKEND_DIR)

from dotenv import load_dotenv
load_dotenv(os.path.join(BACKEND_DIR, ".env"))
load_dotenv(os.path.join(os.path.dirname(BACKEND_DIR), ".env"))

from llm_utils import call_structured_llm_by_provider

# Discord status channel (ai-backtest-agent); messages go via RethinkDB outbox, bot posts to Discord
DISCORD_AGENT_CHANNEL = "ai-backtest-agent"

# Default stock pool for backtests (diversified)
DEFAULT_STOCK_POOL = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA", "JPM", "V", "WMT",
    "JNJ", "PG", "UNH", "HD", "DIS", "ADBE", "NFLX", "CRM", "PYPL", "INTC",
]
# Fixed date range for all backtests (overrides random date selection)
FIXED_START_DATE = "2025-11-10"
FIXED_END_DATE = "2026-02-24"
# Fixed stocks per stage (overrides random stock selection)
STAGE1_STOCKS = ["AMZN", "GLDM", "TSM", "META", "AAPL", "SNDK", "MSFT"]
# Stage 2 (negative-return validation) uses VALIDATION_NEGATIVE_RETURN_SYMBOLS = ["OKLO"]
STAGE3_STOCKS = ["PSLV", "OKLO", "SPY", "GOOGL", "TSLA", "MU"]
GRANULARITY_1D_SEC = 86400
MAX_BACKTEST_DAYS = 180  # ~6 months
BACKTEST_POLL_INTERVAL = 30
BACKTEST_TIMEOUT_SEC = 7200  # 2 hours
AGENT_LOOP_SLEEP = 60
MIN_STOCKS_PER_BACKTEST = 3
MAX_STOCKS_PER_BACKTEST = 8
VALIDATION_MIN_PNL_THRESHOLD = 100.0  # Minimum P&L to trigger validation retests
# First validation: test with a symbol that often has negative/weak returns (if strategy profits, it's a strong signal)
VALIDATION_NEGATIVE_RETURN_SYMBOLS = ["OKLO"]
# Second validation: test with random other stocks
VALIDATION_RANDOM_STOCKS_COUNT = 1  # number of random-stock validation backtests after the negative-return test

# OKLO (Stage 2) validation thresholds
# A strategy is allowed to have a small loss on OKLO only if it beats B&H and the loss is limited.
# Hard fail is checked first: strategy P&L% below this OR relative (vs B&H) below this → fail regardless of the other.
OKLO_HARD_FAIL_ABS_PCT = -2.0       # absolute strategy P&L% on OKLO below this → instant fail (e.g. -2.5% fails)
OKLO_HARD_FAIL_RELATIVE_PCT = -5.0  # strategy return minus OKLO B&H below this → hard fail
# Outright pass only when: profit (>=0) OR (modest loss and beat B&H). Large loss never passes by relative alone.
OKLO_MAX_ABS_LOSS_FOR_RELATIVE_PASS = -5.0  # allow "pass by relative" only when strategy P&L% >= this (e.g. -5%)
# Borderline: not hard fail, not outright pass — run stage 3 and rescue if stage 1 + 3 strong
OKLO_RESCUE_STAGE3_MIN_PCT = 3.0    # stage 3 avg pnl_pct needed to rescue a borderline OKLO result
OKLO_RESCUE_STAGE1_MIN_PCT = 3.0    # stage 1 (initial backtest) pnl_pct needed alongside stage 3 rescue

# Run up to this many strategy tests concurrently (each submits its own backtest to the engine)
MAX_PARALLEL_BACKTESTS = 3

# Stop requested by control (running=false); watcher thread sets this so main/cycle/wait loops exit
_stop_event = threading.Event()
# Pause requested by control (paused=true); watcher updates this; agent and its current backtest pause until resume
_pause_event = threading.Event()
CONTROL_POLL_INTERVAL = 5  # seconds between control checks in watcher thread


class _StrategyGenerationResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    strategies: list[str] = Field(default_factory=list)


class _ValidationDecisionResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    decision: str = "TOSS"
    reason: str = ""


class _BestSelectionDecisionResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    decision: str = "KEEP_CURRENT"
    reason: str = ""


def _stop_requested() -> bool:
    return _stop_event.is_set()


def _request_stop_all_backtests(client: "APIClient") -> None:
    """Stop all running and queued backtests (e.g. when agent stops so no agent-related backtests keep starting)."""
    try:
        client.post("/backtests/stop-all", {})
    except Exception:
        pass


def _pause_requested() -> bool:
    return _pause_event.is_set()


# Global flag for resume_at timer (separate from pause)
_resume_at_datetime = None

def _is_waiting_for_resume_timer() -> bool:
    """Check if we're waiting for resume_at timer (separate from pause)."""
    global _resume_at_datetime
    if _resume_at_datetime is None:
        return False
    from datetime import datetime
    now = datetime.utcnow()
    if _resume_at_datetime.tzinfo:
        now = now.replace(tzinfo=_resume_at_datetime.tzinfo)
    return _resume_at_datetime > now

def _watch_control(client: "APIClient") -> None:
    """Daemon thread: poll /agent/control; when running=False set _stop_event and exit; when paused=True/False update _pause_event; check resume_at for scheduled start."""
    global _resume_at_datetime
    from datetime import datetime
    while not _stop_event.is_set():
        try:
            control = client.get("/agent/control")
            if control is None:
                _stop_event.wait(timeout=CONTROL_POLL_INTERVAL)
                continue
            # Check for scheduled resume timer
            resume_at_str = control.get("resume_at")
            if resume_at_str:
                try:
                    resume_at_str_clean = resume_at_str.replace("Z", "+00:00")
                    if "+" not in resume_at_str_clean and resume_at_str_clean.count(":") == 2:
                        resume_at = datetime.fromisoformat(resume_at_str_clean.replace("Z", ""))
                    else:
                        resume_at = datetime.fromisoformat(resume_at_str_clean)
                    _resume_at_datetime = resume_at
                except Exception as e:
                    _log_error("PARSE_RESUME_AT", e)
                    _resume_at_datetime = None
            else:
                _resume_at_datetime = None  # Clear if resume_at is cleared
            paused = control.get("paused")
            if paused is True:
                _pause_event.set()
            elif paused is False:
                _pause_event.clear()
            # If paused is None/not set, leave event state unchanged
            
            # running=False always means stop, regardless of paused state.
            # (The API now clears paused when stopping, but we also honour it
            # here in case of any race between the two writes.)
            running = control.get("running", True)
            if running is False:
                _log("Agent control: running=false received; requesting exit.", "yellow")
                _stop_event.set()
                return
        except Exception as e:
            _log_error("CONTROL_WATCHER", e)
        _stop_event.wait(timeout=CONTROL_POLL_INTERVAL)


def _log(msg: str, color: str = "white"):
    try:
        from intellistock_logger import intellistock_logger
        intellistock_logger.log(msg, color, service="AI_AGENT")
    except Exception:
        print(f"[AI_AGENT] {msg}")


def _log_error(prefix: str, e: BaseException, context: str = "") -> None:
    """Log exception clearly in red with full traceback so it's easy to spot in dockploy logs. Does not re-raise."""
    msg = "[AI_AGENT_ERROR] %s: %s" % (prefix, e)
    if context:
        msg = "%s | %s" % (msg, context)
    _log(msg, "red")
    tb = traceback.format_exc()
    if tb and tb.strip() != "NoneType: None":
        for line in tb.strip().split("\n"):
            _log("  %s" % line, "red")


def _send_discord(content: str | None = None, embed: dict | None = None, channel: str | None = None) -> None:
    """Send a status message to a Discord channel via RethinkDB outbox. Fails silently. Default channel: ai-backtest-agent."""
    try:
        from interactive_utils import get_conn
        from discord_sender import enqueue_discord_message
        conn = get_conn()
        target = (channel or DISCORD_AGENT_CHANNEL).strip()
        enqueue_discord_message(conn, target, content=content or "", embed=embed)
        conn.close()
    except Exception as e:
        _log_error("DISCORD_SEND", e)


def _agent_slot_message_key(idx: int) -> str:
    """Message key for the N per-strategy messages in #ai-backtest-agent (one per slot, edited in place). Never raises."""
    try:
        return "ai_agent_%d" % int(idx)
    except (TypeError, ValueError):
        return "ai_agent_0"


def _send_discord_slot_initial(idx: int, name: str) -> None:
    """Send the initial message for strategy slot idx (so we can edit it later). One of N messages. Never raises."""
    try:
        try:
            title = "Strategy %d: %s" % (int(idx) + 1, _safe_str(name, 80))
        except (TypeError, ValueError):
            title = "Strategy %d" % (idx + 1 if isinstance(idx, int) else 0)
        from interactive_utils import get_conn
        from discord_sender import enqueue_discord_message
        conn = get_conn()
        enqueue_discord_message(
            conn,
            DISCORD_AGENT_CHANNEL,
            content="",
            embed={
                "title": title,
                "description": "Status: Queued",
                "color": 0x95A5A6,
                "fields": [],
            },
            message_key=_agent_slot_message_key(idx),
        )
        conn.close()
    except Exception as e:
        _log_error("DISCORD_SLOT_INITIAL", e, "idx=%s" % idx)


def _edit_discord_slot(idx: int, embed_dict: dict) -> None:
    """Edit the Discord message for strategy slot idx (replaces entire embed). Used for stage updates. Never raises."""
    try:
        if not isinstance(embed_dict, dict):
            embed_dict = {"title": "Strategy", "description": "—", "color": 0x95A5A6, "fields": []}
        from interactive_utils import get_conn
        from discord_sender import enqueue_discord_edit
        conn = get_conn()
        enqueue_discord_edit(conn, DISCORD_AGENT_CHANNEL, _agent_slot_message_key(idx), content="", embed=embed_dict)
        conn.close()
    except Exception as e:
        _log_error("DISCORD_SLOT_EDIT", e, "idx=%s" % idx)


def _safe_edit_discord_slot(idx: int, name: str, status: str, stage1=None, oklo=None, stage3=None, result=None, color: int = 0x3498DB, reason=None, period=None, stocks=None) -> None:
    """Build slot embed and edit in one step. Never raises; logs and continues on any error."""
    try:
        embed = _build_slot_embed(
            name=name, status=status, stage1=stage1, oklo=oklo, stage3=stage3,
            result=result, color=color, reason=reason, period=period, stocks=stocks,
        )
        _edit_discord_slot(idx, embed)
    except Exception as e:
        _log_error("DISCORD_SLOT_UPDATE", e, "idx=%s name=%s" % (idx, _safe_str(name, 40)))


def _safe_float(x, default: float = 0.0) -> float:
    """Return float(x) or default on any error. Used for embed values."""
    try:
        return float(x) if x is not None else default
    except (TypeError, ValueError):
        return default


def _safe_str(x, max_len: int = 1024) -> str:
    """Return str(x) truncated, or '—' on error."""
    try:
        s = str(x) if x is not None else "—"
        return (s or "—")[:max_len]
    except Exception:
        return "—"


def _build_slot_embed(
    name: str,
    status: str,
    stage1: dict | None = None,
    oklo: dict | None = None,
    stage3: dict | None = None,
    result: str | None = None,
    color: int = 0x3498DB,
    reason: str | None = None,
    period: str | None = None,
    stocks: list | None = None,
) -> dict:
    """Build the full embed for one strategy slot. Never raises; returns minimal embed on error."""
    try:
        try:
            color = int(color) if color is not None else 0x3498DB
        except (TypeError, ValueError):
            color = 0x3498DB
        fields = []
        if status:
            fields.append({"name": "Status", "value": _safe_str(status, 512), "inline": False})
        if isinstance(stage1, dict):
            v = "P&L $%.2f (%.2f%%) — **%s**\n%s" % (
                _safe_float(stage1.get("pnl")),
                _safe_float(stage1.get("pnl_pct")),
                _safe_str(stage1.get("decision"), 64),
                _safe_str(stage1.get("reason"), 400),
            )
            fields.append({"name": "Stage 1 — Initial Backtest", "value": v, "inline": False})
        if isinstance(oklo, dict):
            v = "P&L $%.2f (%.2f%%) — **%s**" % (
                _safe_float(oklo.get("pnl")),
                _safe_float(oklo.get("pnl_pct")),
                _safe_str(oklo.get("verdict"), 64),
            )
            if oklo.get("detail"):
                v += "\n%s" % _safe_str(oklo.get("detail"), 300)
            fields.append({"name": "Stage 2 — OKLO", "value": v, "inline": False})
        if isinstance(stage3, dict):
            v = "P&L $%.2f (%.2f%%) — **%s**" % (
                _safe_float(stage3.get("pnl")),
                _safe_float(stage3.get("pnl_pct")),
                _safe_str(stage3.get("verdict"), 64),
            )
            fields.append({"name": "Stage 3 — Validation", "value": v, "inline": False})
        if result:
            fields.append({"name": "Result", "value": _safe_str(result, 512), "inline": False})
        if reason:
            fields.append({"name": "Reason", "value": _safe_str(reason, 1024), "inline": False})
        if period:
            fields.append({"name": "Period", "value": _safe_str(period, 128), "inline": False})
        if stocks is not None:
            try:
                if hasattr(stocks, "__iter__") and not isinstance(stocks, (str, bytes)):
                    parts = []
                    for i, s in enumerate(stocks):
                        if i >= 20:
                            parts.append("...")
                            break
                        parts.append(_safe_str(s, 32).strip().upper() or "?")
                    if parts:
                        fields.append({"name": "Stocks", "value": "[%s]" % ", ".join(parts), "inline": False})
            except Exception:
                pass
        title = "Strategy: %s" % _safe_str(name, 80)
        return {
            "title": title,
            "description": "—",
            "color": color,
            "fields": fields,
        }
    except Exception as e:
        _log_error("BUILD_SLOT_EMBED", e)
        return {
            "title": "Strategy",
            "description": "—",
            "color": 0x95A5A6,
            "fields": [{"name": "Status", "value": _safe_str(str(e), 200), "inline": False}],
        }


def _env(key: str, default: str = "") -> str:
    v = (os.environ.get(key) or "").strip() or default
    # Strip optional surrounding quotes (load_dotenv can leave them)
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ('"', "'"):
        v = v[1:-1].strip()
    return v


def _normalize_llm_provider(provider: str) -> str:
    name = (provider or "gemini").strip().lower()
    return name if name in ("gemini", "deepseek", "openai", "azure", "nvidia", "claude-cli", "anthropic") else "gemini"


def _default_model_for_provider(provider: str) -> str:
    provider = _normalize_llm_provider(provider)
    if provider == "deepseek":
        return "deepseek-reasoner"
    if provider == "openai":
        return "gpt-4.1-mini"
    if provider == "azure":
        return (_env("AZURE_OPENAI_DEPLOYMENT") or _env("AZURE_OPENAI_MODEL") or "gpt-4.1-mini").strip()
    if provider == "nvidia":
        return "nvidia/nemotron-3-super-120b-a12b"
    if provider in ("claude-cli", "anthropic"):
        return "claude-sonnet-4-6"
    return "gemini-3-flash-preview"


def _default_api_key_for_provider(provider: str) -> str:
    provider = _normalize_llm_provider(provider)
    if provider == "deepseek":
        return _env("DEEPSEEK_API_KEY")
    if provider == "openai":
        return _env("OPENAI_API_KEY")
    if provider == "azure":
        return _env("AZURE_OPENAI_API_KEY")
    if provider == "nvidia":
        return _env("NVIDIA_API_KEY")
    if provider == "anthropic":
        return _env("ANTHROPIC_API_KEY")
    if provider == "claude-cli":
        # Sentinel so ``if not api_key`` short-circuits don't skip the
        # whole pipeline — claude-cli authenticates via the local binary.
        return "claude-cli-no-api-key"
    return _env("GEMINI_API_KEY")


def _provider_config_from_env(prefix: str, provider: str) -> dict[str, str]:
    provider = _normalize_llm_provider(provider)
    if provider == "azure":
        endpoint = (
            _env(f"{prefix}_AZURE_OPENAI_ENDPOINT")
            or _env("AI_BACKTESTING_AGENT_AZURE_OPENAI_ENDPOINT")
            or _env("AZURE_OPENAI_ENDPOINT")
        )
        api_version = (
            _env(f"{prefix}_AZURE_OPENAI_API_VERSION")
            or _env("AI_BACKTESTING_AGENT_AZURE_OPENAI_API_VERSION")
            or _env("OPENAI_API_VERSION")
            or "2024-10-21"
        )
        return {
            "azure_endpoint": endpoint.strip(),
            "api_version": api_version.strip(),
        }
    if provider == "openai":
        base_url = (
            _env(f"{prefix}_OPENAI_BASE_URL")
            or _env("AI_BACKTESTING_AGENT_OPENAI_BASE_URL")
            or _env("OPENAI_BASE_URL")
        )
        return {"base_url": base_url.strip()} if base_url.strip() else {}
    if provider == "nvidia":
        base_url = (
            _env(f"{prefix}_NVIDIA_BASE_URL")
            or _env("NVIDIA_BASE_URL")
            or "https://integrate.api.nvidia.com/v1"
        )
        return {"base_url": base_url.strip()}
    return {}


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, default))
    except (TypeError, ValueError):
        return default


class APIClient:
    def __init__(self, base_url: str, username: str, password: str):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.token: str | None = None
        self._logged_in_once: bool = False  # True after first successful login; used to re-auth on 401

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def login(self) -> bool:
        try:
            # Username is stored lowercased in DB; send lowercase to avoid any case issues
            username = self.username.strip().lower() if self.username else ""
            payload = {"username": username, "password": self.password}
            r = __import__("urllib.request", fromlist=["request"])
            req = r.Request(
                f"{self.base_url}/auth/login",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with r.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                self.token = data.get("access_token")
                if self.token:
                    self._logged_in_once = True
                return bool(self.token)
        except Exception as e:
            _log(f"Login failed: {e}", "red")
            return False

    def get(self, path: str) -> dict | None:
        urllib_request = __import__("urllib.request", fromlist=["request"])
        urllib_error = __import__("urllib.error", fromlist=["HTTPError"])
        try:
            req = urllib_request.Request(f"{self.base_url}{path}", headers=self._headers(), method="GET")
            with urllib_request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib_error.HTTPError as e:
            if e.code == 401 and self._logged_in_once:
                _log("Access token expired (401). Re-authenticating...", "cyan")
                if self.login():
                    try:
                        req = urllib_request.Request(f"{self.base_url}{path}", headers=self._headers(), method="GET")
                        with urllib_request.urlopen(req, timeout=30) as resp:
                            return json.loads(resp.read().decode("utf-8"))
                    except Exception as retry_e:
                        _log(f"GET {path} failed after re-auth: {retry_e}", "red")
                        return None
            _log(f"GET {path} failed: HTTP Error {e.code}: {e.reason}", "red")
            return None
        except Exception as e:
            _log(f"GET {path} failed: {e}", "red")
            return None

    def post(self, path: str, body: dict) -> dict | None:
        urllib_request = __import__("urllib.request", fromlist=["request"])
        urllib_error = __import__("urllib.error", fromlist=["HTTPError"])
        try:
            req = urllib_request.Request(
                f"{self.base_url}{path}",
                data=json.dumps(body).encode("utf-8"),
                headers=self._headers(),
                method="POST",
            )
            with urllib_request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib_error.HTTPError as e:
            if e.code == 401 and self._logged_in_once:
                _log("Access token expired (401). Re-authenticating...", "cyan")
                if self.login():
                    try:
                        req = urllib_request.Request(
                            f"{self.base_url}{path}",
                            data=json.dumps(body).encode("utf-8"),
                            headers=self._headers(),
                            method="POST",
                        )
                        with urllib_request.urlopen(req, timeout=60) as resp:
                            return json.loads(resp.read().decode("utf-8"))
                    except Exception as retry_e:
                        _log(f"POST {path} failed after re-auth: {retry_e}", "red")
                        return None
            try:
                err_body = e.read().decode("utf-8", replace="?") if e.fp else ""
            except Exception:
                err_body = ""
            _log(f"POST {path} failed: HTTP {e.code} {e.reason} - {err_body[:500]}", "red")
            return None
        except Exception as e:
            _log(f"POST {path} failed: {e}", "red")
            return None

    def delete(self, path: str) -> bool:
        urllib_request = __import__("urllib.request", fromlist=["request"])
        urllib_error = __import__("urllib.error", fromlist=["HTTPError"])
        try:
            req = urllib_request.Request(f"{self.base_url}{path}", headers=self._headers(), method="DELETE")
            with urllib_request.urlopen(req, timeout=30) as resp:
                return 200 <= getattr(resp, "status", 200) < 300
        except urllib_error.HTTPError as e:
            if e.code == 401 and self._logged_in_once:
                _log("Access token expired (401). Re-authenticating...", "cyan")
                if self.login():
                    try:
                        req = urllib_request.Request(f"{self.base_url}{path}", headers=self._headers(), method="DELETE")
                        with urllib_request.urlopen(req, timeout=30) as resp:
                            return 200 <= getattr(resp, "status", 200) < 300
                    except Exception as retry_e:
                        _log(f"DELETE {path} failed after re-auth: {retry_e}", "red")
                        return False
            _log(f"DELETE {path} failed: HTTP Error {e.code}: {e.reason}", "red")
            return False
        except Exception as e:
            _log(f"DELETE {path} failed: {e}", "red")
            return False


def _get_llm_config(stage: str) -> dict:
    """Build LLM config for a stage from env. stage in: strategy_generation, validation, best_selection.
    Returns dict with provider, model, api_key, and optional provider_config."""
    prefix = f"AI_BACKTESTING_{stage.upper()}"
    provider = _normalize_llm_provider(_env(f"{prefix}_PROVIDER") or _env("AI_BACKTESTING_AGENT_PROVIDER") or "gemini")
    # Model: stage-specific, or legacy AI_BACKTESTING_AGENT_MODEL, or provider default
    model = _env(f"{prefix}_MODEL") or _env("AI_BACKTESTING_AGENT_MODEL")
    if not model:
        model = _default_model_for_provider(provider)
    # API key: stage-specific override, or provider default
    api_key = _env(f"{prefix}_API_KEY") or _env("AI_BACKTESTING_AGENT_API_KEY")
    if not api_key:
        api_key = _default_api_key_for_provider(provider)
    return {
        "provider": provider,
        "model": model.strip(),
        "api_key": (api_key or "").strip(),
        "provider_config": _provider_config_from_env(prefix, provider),
    }


def _call_structured_llm(prompt: str, config: dict, output_type: Any, system_prompt: str, max_output_tokens: int = 8192):
    """Call an LLM provider through the shared structured-output helper."""
    provider = _normalize_llm_provider(config.get("provider") or "gemini")
    model = (config.get("model") or "").strip()
    api_key = (config.get("api_key") or "").strip()
    provider_config = config.get("provider_config") or {}
    if not api_key or not model:
        return None
    try:
        return call_structured_llm_by_provider(
            provider,
            api_key,
            model,
            prompt,
            output_type,
            system_prompt=system_prompt,
            max_output_tokens=max_output_tokens,
            retries=2,
            output_retries=2,
            temperature=0.3,
            provider_config=provider_config,
        )
    except Exception as e:
        _log(f"Structured LLM call failed: {e}", "red")
        return None


def _parse_json_object_string(raw: Any) -> dict:
    """Parse a compact JSON object string returned by the structured generator."""
    if isinstance(raw, dict):
        return raw
    text = (str(raw).strip() if raw is not None else "")
    if not text:
        return {}
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _normalize_generated_strategies(structured: _StrategyGenerationResponse | None) -> list[dict]:
    """Convert structured LLM strategy output into the runtime strategy config shape."""
    out: list[dict] = []
    if structured is None:
        return out
    for raw_strategy in structured.strategies:
        try:
            parsed = json.loads((raw_strategy or "").strip())
        except Exception:
            parsed = None
        if not isinstance(parsed, dict):
            continue
        name = (str(parsed.get("name") or "").strip() or "AI Temp")
        subs_out = []
        for sub in (parsed.get("strategies") or []):
            if not isinstance(sub, dict):
                continue
            strategy_id = (str(sub.get("strategy") or "").strip())
            if not strategy_id:
                continue
            try:
                weight = float(sub.get("weight"))
            except Exception:
                weight = 0.0
            try:
                execution_position = int(sub.get("execution_position"))
            except Exception:
                execution_position = 0
            decision_phase = (str(sub.get("decision_phase") or "pre")).strip().lower()
            if decision_phase not in ("pre", "post"):
                decision_phase = "pre"
            execution_scope = (str(sub.get("execution_scope") or "per_symbol")).strip().lower()
            if execution_scope not in ("per_symbol", "run_once"):
                execution_scope = "per_symbol"
            subs_out.append(
                {
                    "strategy": strategy_id,
                    "weight": weight,
                    "execution_position": execution_position,
                    "decision_phase": decision_phase,
                    "execution_scope": execution_scope,
                    "conditions": _parse_json_object_string(sub.get("conditions")),
                    "config": _parse_json_object_string(sub.get("config")),
                }
            )
        if subs_out:
            out.append({"name": name, "strategies": subs_out})
    return out


def _normalize_strategy_ids(strategies: list[dict], schemas: list[dict]) -> list[dict]:
    """Replace sub-strategy 'strategy' value with the schema 'id' (e.g. Macd -> macd, ParabolicSar -> parabolic_sar)."""
    name_to_id = {}
    for s in schemas:
        sid = (s.get("id") or "").strip()
        sname = (s.get("name") or "").strip()
        if sid:
            name_to_id[sid.lower()] = sid
            name_to_id[sid] = sid
        if sname:
            name_to_id[sname.lower()] = sid
            name_to_id[sname] = sid
    out = []
    for strat in strategies:
        strat = dict(strat)
        subs = strat.get("strategies") or []
        new_subs = []
        for sub in subs:
            sub = dict(sub)
            val = (sub.get("strategy") or "").strip()
            if val and val in name_to_id:
                sub["strategy"] = name_to_id[val]
            elif val:
                # Try case-insensitive / underscore match
                val_lower = val.lower().replace(" ", "_")
                if val_lower in name_to_id:
                    sub["strategy"] = name_to_id[val_lower]
            new_subs.append(sub)
        strat["strategies"] = new_subs
        out.append(strat)
    return out


def _inject_earnings_config(strategies: list[dict]) -> list[dict]:
    """If any sub-strategy is Earnings, fill missing LLM/provider fields from env while preserving explicit config."""
    alpaca_key = _env("KEY")
    alpaca_secret = _env("SECRET")
    out = []
    for s in strategies:
        s = dict(s)
        subs = s.get("strategies") or []
        new_subs = []
        for sub in subs:
            sub = dict(sub)
            sid = (sub.get("strategy") or "").strip().lower()
            if sid == "earnings":
                cfg = dict(sub.get("config") or {})
                provider = _normalize_llm_provider(
                    cfg.get("llm_provider")
                    or _env("AI_BACKTESTING_EARNINGS_PROVIDER")
                    or _env("AI_BACKTESTING_AGENT_PROVIDER")
                    or "gemini"
                )
                cfg["llm_provider"] = provider
                cfg["model_name"] = (cfg.get("model_name") or cfg.get("llm_model") or _default_model_for_provider(provider)).strip()
                if not cfg.get("llm_api_key"):
                    cfg["llm_api_key"] = _default_api_key_for_provider(provider)
                provider_config = _provider_config_from_env("AI_BACKTESTING_EARNINGS", provider)
                if provider == "azure":
                    endpoint = (cfg.get("azure_openai_endpoint") or provider_config.get("azure_endpoint") or "").strip()
                    api_version = (cfg.get("azure_openai_api_version") or provider_config.get("api_version") or "").strip()
                    if endpoint:
                        cfg["azure_openai_endpoint"] = endpoint
                    if api_version:
                        cfg["azure_openai_api_version"] = api_version
                elif provider == "openai":
                    base_url = (cfg.get("openai_base_url") or provider_config.get("base_url") or "").strip()
                    if base_url:
                        cfg["openai_base_url"] = base_url
                if alpaca_key and not str(cfg.get("alpaca_key") or "").strip():
                    cfg["alpaca_key"] = alpaca_key
                if alpaca_secret and not str(cfg.get("alpaca_secret") or "").strip():
                    cfg["alpaca_secret"] = alpaca_secret
                sub["config"] = cfg
            new_subs.append(sub)
        s["strategies"] = new_subs
        out.append(s)
    return out


def _parse_strategies_from_llm(text: str) -> list[dict]:
    """Parse LLM output into list of { name, strategies: [ { strategy, weight, execution_position, conditions, config } ] }.
    Handles raw JSON array, code blocks (```json ... ```), and reasoning-then-JSON (e.g. DeepSeek) by finding last JSON array."""
    out = []
    raw = (text or "").strip()
    if not raw:
        return out

    def collect(item):
        if isinstance(item, dict) and item.get("strategies"):
            out.append(item)
            return True
        return False

    def try_parse(s: str) -> bool:
        s = (s or "").strip()
        if not s:
            return False
        try:
            if s.startswith("["):
                arr = json.loads(s)
                if isinstance(arr, list):
                    for item in arr:
                        collect(item)
                    return len(out) > 0
            if s.startswith("{"):
                obj = json.loads(s)
                if isinstance(obj, dict) and collect(obj):
                    return True
                if isinstance(obj, list):
                    for x in obj:
                        collect(x)
                    return len(out) > 0
        except json.JSONDecodeError:
            pass
        return False

    # 1) Direct array or object at start
    if try_parse(raw):
        return out

    # 2) Code block (first or any): take content and try parse
    if "```" in raw:
        start = raw.find("```")
        while start >= 0:
            end = raw.find("```", start + 3)
            if end > start:
                block = raw[start + 3 : end].strip()
                if block.lower().startswith("json"):
                    block = block[4:].strip()
                if try_parse(block):
                    return out
            start = raw.find("```", end + 1) if end > start else -1

    # 3) Find a JSON array in text (e.g. reasoning then [...] for DeepSeek-reasoner — use last "[" to get final answer)
    last_open = raw.rfind("[")
    if last_open >= 0:
        depth = 0
        for i in range(last_open, len(raw)):
            c = raw[i]
            if c == "[":
                depth += 1
            elif c == "]":
                depth -= 1
                if depth == 0:
                    try:
                        segment = raw[last_open : i + 1]
                        arr = json.loads(segment)
                        if isinstance(arr, list):
                            out = []
                            for item in arr:
                                collect(item)
                            if out:
                                return out
                    except (json.JSONDecodeError, ValueError):
                        pass
                    break
    return out


# Keys whose values should be redacted in strategy summaries (e.g. API keys, secrets)
_SENSITIVE_CONFIG_KEYS = frozenset(
    k.lower() for k in (
        "api_key", "llm_api_key", "secret", "token", "password", "auth", "apikey", "api_secret",
    )
)


def _is_sensitive_key(key: str) -> bool:
    """True if the config key looks like a secret/API key (for redaction)."""
    k = (key or "").strip().lower()
    if k in _SENSITIVE_CONFIG_KEYS:
        return True
    return any(s in k for s in ("key", "secret", "token", "password", "auth"))


def _redact_config(cfg: dict) -> dict:
    """Return a copy of config with sensitive values replaced by <redacted>."""
    if not cfg:
        return {}
    out = {}
    for k, v in cfg.items():
        if _is_sensitive_key(k):
            out[k] = "<redacted>"
        elif isinstance(v, dict):
            out[k] = _redact_config(v)
        else:
            out[k] = v
    return out


def _config_to_short_string(cfg: dict, max_pairs: int = 6, max_len: int = 100) -> str:
    """Format config as compact k=v string; redact sensitive keys. Schemas in prompt let LLM infer meaning."""
    cfg = _redact_config(cfg or {})
    if not cfg:
        return ""
    parts = []
    for i, (k, v) in enumerate(sorted(cfg.items())):
        if i >= max_pairs:
            break
        if v is None:
            s = "%s=null" % k
        elif isinstance(v, bool):
            s = "%s=%s" % (k, str(v).lower())
        elif isinstance(v, (int, float)):
            s = "%s=%s" % (k, v)
        else:
            s = "%s=%s" % (k, str(v)[:20])
        parts.append(s)
    joined = ",".join(parts)
    if len(joined) > max_len:
        joined = joined[: max_len - 3] + "..."
    return joined


def _summarize_past_strategies(past_list: list[dict], max_combos: int = 25, max_examples: int = 12) -> str:
    """Reduce past results to a short summary: combo + weight + config (sensitive values redacted). Keeps token use bounded."""
    combo_counts = {}
    for r in past_list:
        snap = r.get("strategy_snapshot") or {}
        subs = snap.get("strategies") or []
        parts = []
        for s in sorted(subs, key=lambda x: (x.get("strategy") or "")):
            sid = (s.get("strategy") or "?").strip() or "?"
            w = s.get("weight")
            wstr = "%.2f" % w if w is not None else "?"
            cfg_str = _config_to_short_string(s.get("config") or {})
            if cfg_str:
                parts.append("%s(%s){%s}" % (sid, wstr, cfg_str))
            else:
                parts.append("%s(%s)" % (sid, wstr))
        sig = "+".join(parts) if parts else "empty"
        combo_counts[sig] = combo_counts.get(sig, 0) + 1
    total = len(past_list)
    if not combo_counts:
        return "None yet."
    sorted_combos = sorted(combo_counts.items(), key=lambda x: -x[1])[:max_combos]
    lines = ["%s: %d" % (sig, count) for sig, count in sorted_combos]
    summary = "Total %d strategies tried. Combo+config breakdown (avoid duplicating these; schema above explains params): %s" % (
        total,
        "; ".join(lines[:max_examples]),
    )
    if len(lines) > max_examples:
        summary += " (+%d other combo types)" % (len(lines) - max_examples)
    summary += ". Sensitive keys (e.g. api_key, llm_api_key) are shown as <redacted>."
    return summary


def _generate_strategies(client: APIClient, llm_config: dict, batch_size: int, special_request: str = "") -> list[dict]:
    """Ask LLM to generate strategy configs. Returns list of { name, strategies }. Optional special_request: user instruction that must be followed (e.g. include a strategy in each)."""
    _log("Fetching available strategies from API ...", "cyan")
    available = client.get("/strategies/available")
    if not available or "strategies" not in available:
        _log("Could not fetch available strategies (response: %s)" % (available is None and "None" or "no 'strategies' key"), "red")
        return []
    schemas = available["strategies"]
    _log("  Available sub-strategies: %s" % [s.get("name") for s in schemas], "white")
    past = client.get("/agent/results?limit=50")
    past_list = (past or {}).get("results") or []
    past_summary_str = _summarize_past_strategies(past_list)
    _log("  Past results to avoid duplicating: %d (summary for prompt)" % len(past_list), "white")
    if not llm_config.get("api_key") or not llm_config.get("model"):
        _log("  Skipping LLM: no API key or model set for strategy generation.", "yellow")
        return []

    provider, model = llm_config.get("provider", "gemini"), llm_config.get("model", "")
    _log("Calling LLM to generate %d strategy config(s) (%s, model=%s) ..." % (batch_size, provider, model), "cyan")
    if special_request:
        _log("  Special request: %s" % special_request[:80] + ("..." if len(special_request) > 80 else ""), "cyan")
    special_section = ""
    if special_request and special_request.strip():
        special_section = f"""
SPECIAL REQUEST (you must follow this - the user gave this instruction):
{special_request.strip()}

"""
    prompt = f"""You are an expert quantitative trading assistant. Generate exactly {batch_size} different trading strategy configurations for backtesting.
{special_section}AVAILABLE SUB-STRATEGIES - use each entry's "id" (not "name") for the "strategy" field:
{json.dumps([{"id": s["id"], "name": s["name"], "schema": s["schema"], "description": s["description"]} for s in schemas], indent=2)}

STRATEGIES WE ALREADY TRIED (do not duplicate these combo/weight/configs; params match schema fields above):
{past_summary_str}

RULES:
- Each strategy has a "name" (string) and "strategies" (array of sub-strategies).
- Each sub-strategy must have "strategy" set to the "id" from the available list (e.g. "macd", "parabolic_sar", "tiered_risk", "rsi", "ml_news"), NOT the "name". Use lowercase with underscores (the "id" value exactly).
- Also include: "weight" (0.0-1.0), "execution_position" (int, usually 0), "decision_phase" ("pre" or "post"), "execution_scope" ("per_symbol" or "run_once"), "conditions" (object), and "config" (object).
- "decision_phase": "pre" = run before final decision (voting for buy/hold/sell). "post" = run after decision (order size, pricing). Use "post" only for position_sizing; use "pre" for all other strategies.
- Weights across sub-strategies in one strategy should sum to 1.0.
- Create diverse combinations: different weights, different config values (e.g. RSI period 7 or 14, MACD fast/slow).
- IMPORTANT: The "volatility" strategy is very slow (requires ML model training). Use it sparingly - include it in at most 1 out of every {batch_size} strategies, or avoid it entirely if possible. Prefer faster strategies like macd, rsi, parabolic_sar, candles, swing, ml_news, etc.
- For Earnings use "strategy": "earnings" and keep LLM config compatible with the schema. Missing provider/model/key fields are filled by system defaults.
- "execution_scope": "per_symbol" (default) or "run_once". Use "run_once" for graph_nexus_analysis (runs once per loop, outputs scores per ticker from news+graph).
- Return exactly {batch_size} entries in the structured "strategies" field.
- Each item in the structured "strategies" field must be a single compact JSON string for one full strategy object, for example:
  "{{\\"name\\":\\"Momentum Hybrid\\",\\"strategies\\":[{{\\"strategy\\":\\"rsi\\",\\"weight\\":0.5,\\"execution_position\\":0,\\"decision_phase\\":\\"pre\\",\\"execution_scope\\":\\"per_symbol\\",\\"conditions\\":{{}},\\"config\\":{{\\"period\\":14}}}}]}}"
- Do not use markdown fences or commentary."""

    resp = _call_structured_llm(
        prompt,
        llm_config,
        _StrategyGenerationResponse,
        "Return only structured strategy-generation data. The structured field strategies must be a list of compact JSON strings, one full strategy object per string.",
        max_output_tokens=0,
    )
    if not resp:
        _log("  Structured LLM returned no response.", "red")
        return []
    strategies = _normalize_generated_strategies(resp)
    strategies = _normalize_strategy_ids(strategies, schemas)
    strategies = _inject_earnings_config(strategies)[:batch_size]
    _log("  Parsed %d structured strategy config(s) from LLM (normalized to use strategy id)." % len(strategies), "white")
    for i, s in enumerate(strategies):
        name = (s.get("name") or "?").strip() or "?"
        subs = s.get("strategies") or []
        parts = ["%s(%.2f)" % (sub.get("strategy", "?"), float(sub.get("weight", 0))) for sub in subs]
        _log("  Generated strategy %d: '%s' -> %s" % (i + 1, name, ", ".join(parts)), "green")
    return strategies


def _validate_result_llm(summary: dict, strategy_snapshot: dict, llm_config: dict) -> tuple[bool, str]:
    """Ask LLM whether to keep this result. Returns (keep, notes)."""
    pnl = summary.get("pnl") if summary.get("pnl") is not None else 0
    pnl_pct = summary.get("pnl_percent") if summary.get("pnl_percent") is not None else 0
    per_stock = summary.get("pnl_per_stock") or {}
    per_stock_pct = summary.get("pnl_percent_per_stock") or {}
    price_change = summary.get("stock_price_change") or {}
    win_rate = summary.get("win_rate_percent") if summary.get("win_rate_percent") is not None else 0
    total_trades = summary.get("total_trades") if summary.get("total_trades") is not None else 0

    prompt = f"""You evaluate backtest results for a trading strategy.

BACKTEST SUMMARY (compact):
- Overall P&L: ${pnl:,.2f}, P&L %: {pnl_pct:+.2f}%
- Win rate: {win_rate:.1f}%, Total trades: {total_trades}
- Per-stock P&L (dollars): {json.dumps(per_stock)}
- Per-stock P&L % (return on capital invested in each stock): {json.dumps(per_stock_pct)}
- Stock price change (start to end) %: {json.dumps(price_change)}

STRATEGY USED: {json.dumps(strategy_snapshot, indent=2)}

Decide: Did this strategy make meaningful profit and is it worth keeping?
- Prefer KEEP when: overall account profit is clearly positive; strategy added value (e.g. high account growth even if concentrated in one or few names, or if account growth is strong relative to capital deployed). Do not require that the strategy beat buy-and-hold by a large margin in every stock; strategies that grow the account well (e.g. high overall P&L or high overall %) are valuable even when some underperformance vs. buy-and-hold or concentration in a single stock occurs.
- Consider TOSS only when: profit is trivial or negative, or results are clearly unreliable (e.g. no real edge, or strategy lost money).
- Return a structured decision with:
  decision: KEEP or TOSS
  reason: one short sentence"""

    _log("  Asking LLM to validate result (KEEP/TOSS) ...", "cyan")
    resp = _call_structured_llm(
        prompt,
        llm_config,
        _ValidationDecisionResponse,
        "Return only a structured validation decision with fields decision and reason.",
        max_output_tokens=0,
    )
    if not resp:
        _log("  LLM validation failed (no response).", "red")
        return False, "LLM failed"
    keep = (resp.decision or "").strip().upper() == "KEEP"
    reason = (resp.reason or "").strip()
    _log("  LLM decision: %s - %s" % ("KEEP" if keep else "TOSS", reason), "green" if keep else "yellow")
    return keep, reason


def _format_stage_detail(results_summary: dict) -> str:
    """Format a results_summary dict into a detailed human-readable string for LLM comparison."""
    if not results_summary:
        return "No detailed results available."
    parts = []
    # Initial backtest
    initial = results_summary.get("initial") or {}
    if initial:
        parts.append("=== INITIAL BACKTEST (Stage 1) ===")
        parts.append("  Overall P&L: $%.2f (%.2f%%)" % (float(initial.get("pnl") or 0), float(initial.get("pnl_percent") or 0)))
        parts.append("  Stocks: %s" % (initial.get("stocks_used") or initial.get("stocks") or "N/A"))
        parts.append("  Period: %s to %s" % (initial.get("start_date") or "?", initial.get("end_date") or "?"))
        trades = initial.get("total_trades")
        win_rate = initial.get("win_rate_percent")
        if trades is not None:
            parts.append("  Total trades: %s" % trades)
        if win_rate is not None:
            parts.append("  Win rate: %.1f%%" % float(win_rate))
        per_stock = initial.get("pnl_per_stock") or {}
        per_stock_pct = initial.get("pnl_percent_per_stock") or {}
        price_change = initial.get("stock_price_change") or {}
        if per_stock:
            parts.append("  Per-stock P&L (strategy $ | strategy % | B&H %):")
            for sym in sorted(per_stock.keys()):
                pnl_val = per_stock.get(sym) or 0
                pnl_pct_val = per_stock_pct.get(sym) or 0
                bnh_raw = price_change.get(sym)
                if isinstance(bnh_raw, dict):
                    bnh_str = "%.2f%%" % float(bnh_raw.get("change_percent") or 0)
                elif bnh_raw is not None:
                    bnh_str = "%.2f%%" % float(bnh_raw)
                else:
                    bnh_str = "N/A"
                parts.append("    %s: $%.2f | %.2f%% | B&H %s" % (sym, float(pnl_val), float(pnl_pct_val), bnh_str))
    # Validation notes
    val_notes = results_summary.get("validation_notes")
    if val_notes:
        parts.append("  LLM validation notes: %s" % val_notes)
    # Validation stages
    stages = results_summary.get("validation_stages") or []
    for i, vs in enumerate(stages):
        stage_label = (vs.get("stage") or "validation").upper()
        parts.append("=== VALIDATION %d (%s - Stage %d) ===" % (i + 1, stage_label, i + 2))
        parts.append("  Overall P&L: $%.2f (%.2f%%)" % (float(vs.get("pnl") or 0), float(vs.get("pnl_pct") or 0)))
        parts.append("  Stocks: %s" % (vs.get("stocks") or "N/A"))
        parts.append("  Period: %s to %s" % (vs.get("start_date") or "?", vs.get("end_date") or "?"))
        vs_trades = vs.get("total_trades")
        vs_win_rate = vs.get("win_rate_percent")
        if vs_trades is not None:
            parts.append("  Total trades: %s" % vs_trades)
        if vs_win_rate is not None:
            parts.append("  Win rate: %.1f%%" % float(vs_win_rate))
        vs_per_stock = vs.get("pnl_per_stock") or {}
        vs_per_stock_pct = vs.get("pnl_percent_per_stock") or {}
        vs_price_change = vs.get("stock_price_change") or {}
        if vs_per_stock:
            parts.append("  Per-stock P&L (strategy $ | strategy % | B&H %):")
            for sym in sorted(vs_per_stock.keys()):
                pnl_val = vs_per_stock.get(sym) or 0
                pnl_pct_val = vs_per_stock_pct.get(sym) or 0
                bnh_raw = vs_price_change.get(sym)
                if isinstance(bnh_raw, dict):
                    bnh_str = "%.2f%%" % float(bnh_raw.get("change_percent") or 0)
                elif bnh_raw is not None:
                    bnh_str = "%.2f%%" % float(bnh_raw)
                else:
                    bnh_str = "N/A"
                parts.append("    %s: $%.2f | %.2f%% | B&H %s" % (sym, float(pnl_val), float(pnl_pct_val), bnh_str))
    return "\n".join(parts) if parts else "No detailed results available."


def _decide_new_best_llm(
    candidate_snapshot: dict,
    candidate_profit: float,
    candidate_pnl_pct: float,
    candidate_results_summary: dict,
    current_best: dict | None,
    llm_config: dict,
) -> tuple[bool, str]:
    """Ask LLM whether the candidate should replace the current best. Returns (set_as_new_best, reason)."""
    if not llm_config.get("api_key") or not llm_config.get("model"):
        return False, "No LLM config for best selection"
    # Build detailed candidate info
    candidate_detail = _format_stage_detail(candidate_results_summary)
    candidate_strategy_str = json.dumps(candidate_snapshot, indent=2)
    # Build detailed current best info
    current_detail = "None (no current best set)."
    current_strategy_str = ""
    if current_best:
        current_detail = _format_stage_detail(current_best.get("results_summary") or {})
        current_strategy_str = json.dumps(current_best.get("strategy_snapshot") or {}, indent=2)
    prompt = f"""You compare two backtest outcomes and decide whether the CANDIDATE should replace the CURRENT BEST.

All backtests use the same fixed date range and stock sets per stage, so results are directly comparable.
- Stage 1 (Initial): {', '.join(STAGE1_STOCKS)} from {FIXED_START_DATE} to {FIXED_END_DATE}
- Stage 2 (OKLO validation): {', '.join(VALIDATION_NEGATIVE_RETURN_SYMBOLS)} from {FIXED_START_DATE} to {FIXED_END_DATE}
- Stage 3 (Validation): {', '.join(STAGE3_STOCKS)} from {FIXED_START_DATE} to {FIXED_END_DATE}

===== CANDIDATE (proposed new best) =====
Strategy: {candidate_snapshot.get("name") or "?"}
Overall P&L: ${candidate_profit:,.2f} ({candidate_pnl_pct:+.2f}%)
Strategy config:
{candidate_strategy_str}

Detailed results by stage:
{candidate_detail}

===== CURRENT BEST (incumbent) =====
{"Strategy: " + ((current_best.get("strategy_snapshot") or {}).get("name") or "?") if current_best else "None (no current best set)."}
{"Overall P&L: $%.2f (%.2f%%)" % (float(current_best.get("overall_profit") or 0), float(current_best.get("pnl_percent") or 0)) if current_best else ""}
{"Strategy config:" if current_best else ""}
{current_strategy_str}

{"Detailed results by stage:" if current_best else ""}
{current_detail}

EVALUATION CRITERIA:
- Compare overall P&L and P&L % across all stages
- Compare per-stock performance: does the strategy beat buy-and-hold (B&H) for individual stocks?
- Consider consistency: does the strategy perform well across all 3 stages (initial, OKLO, validation)?
- Consider win rate and total trades as secondary factors
- A strategy that performs strongly on the harder OKLO test is a good sign of robustness
- Prefer the candidate if it shows stronger or more consistent results, or clearly higher profit with comparable robustness

Return a structured decision with:
  decision: SET_AS_NEW_BEST or KEEP_CURRENT
  reason: one short sentence"""

    _log("  Asking LLM whether to set candidate as new best ...", "cyan")
    resp = _call_structured_llm(
        prompt,
        llm_config,
        _BestSelectionDecisionResponse,
        "Return only a structured best-selection decision with fields decision and reason.",
        max_output_tokens=0,
    )
    if not resp:
        _log("  LLM best-selection failed (no response). Keeping current best.", "red")
        return False, "LLM failed"
    set_new = (resp.decision or "").strip().upper() == "SET_AS_NEW_BEST"
    reason = (resp.reason or "").strip()
    _log("  LLM best-selection: %s - %s" % ("SET_AS_NEW_BEST" if set_new else "KEEP_CURRENT", reason), "green" if set_new else "yellow")
    return set_new, reason


def _random_date_range(max_days: int = MAX_BACKTEST_DAYS) -> tuple[str, str]:
    """Return (start_date, end_date) as YYYY-MM-DD with end_date - start_date <= max_days."""
    end = datetime.utcnow() - timedelta(days=random.randint(5, 30))  # end in the past
    start = end - timedelta(days=random.randint(30, max_days))
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


def _random_stocks(pool: list[str], min_n: int = MIN_STOCKS_PER_BACKTEST, max_n: int = MAX_STOCKS_PER_BACKTEST) -> list[str]:
    n = random.randint(min_n, min(max_n, len(pool)))
    return random.sample(pool, n)


def _cycle_log_create(client: "APIClient", cycle_id: str, name: str) -> "str | None":
    """Create a new AgentCycleLog entry. Returns log_id or None on failure."""
    try:
        resp = client.post("/agent/cycle-log", {"cycle_id": cycle_id, "name": name})
        return (resp or {}).get("id")
    except Exception:
        return None


def _cycle_log_update(
    client: "APIClient",
    log_id: "str | None",
    stages: list,
    status: str,
    final_result: "str | None" = None,
) -> None:
    """Update an AgentCycleLog entry. Silently ignores failures."""
    if not log_id:
        return
    try:
        body: dict = {"stages": stages, "status": status}
        if final_result is not None:
            body["final_result"] = final_result
        client.post(f"/agent/cycle-log/{log_id}/update", body)
    except Exception:
        pass


def _run_one_strategy(
    client: "APIClient",
    idx: int,
    total: int,
    strat_cfg: dict,
    validation_llm_config: dict,
    best_selection_llm_config: dict,
    daily_goal: int,
    stage1_seen_lock: "threading.Lock | None" = None,
    stage1_seen_fingerprints: "set[tuple] | None" = None,
    ai_brokerage_id: "str | None" = None,
    cycle_id: "str | None" = None,
) -> None:
    """Run the full lifecycle for a single generated strategy: create → backtest → validate → save.
    Designed to be called in a thread (via ThreadPoolExecutor) for parallel execution."""
    name = (strat_cfg.get("name") or "AI Temp").strip() or "AI Temp"
    _cycle_log_id: "str | None" = None
    _cycle_stages: list = []
    if cycle_id:
        _cycle_log_id = _cycle_log_create(client, cycle_id, name)
    subs = strat_cfg.get("strategies") or []
    if not subs:
        _log("  [%d/%d] Skipping '%s': no sub-strategies." % (idx + 1, total, name), "yellow")
        return
    _log("--- [%d/%d] Strategy '%s' (%d sub-strategies) ---" % (idx + 1, total, name, len(subs)), "cyan")
    temp_id = str(random.randint(900000, 999999))
    strategy_id = None
    instance_id = None
    backtest_id = None
    try:
        # Create strategy
        _log("  [%s] Creating strategy in DB ..." % name[:20], "white")
        cr = client.post("/strategies", {"name": f"AI Temp {name[:30]}", "strategies": subs})
        if not cr or "id" not in cr:
            _log("  [%s] Failed to create strategy: %s" % (name[:20], cr), "red")
            return
        strategy_id = cr["id"]
        strategy_snapshot = {"name": name, "strategies": subs}
        _log("  [%s] Created strategy id=%s" % (name[:20], strategy_id), "green")

        # Create instance with run_command=false
        inst_payload = {
            "id": temp_id,
            "name": temp_id,
            "strategy_id": strategy_id,
            "run_command": False,
            "created_by": "ai",
        }
        if ai_brokerage_id:
            inst_payload["brokerage_id"] = ai_brokerage_id
        ir = client.post("/instances", inst_payload)
        if not ir:
            _log("  [%s] Failed to create instance." % name[:20], "red")
            client.delete(f"/strategies/{strategy_id}?force=true")
            return
        instance_id = temp_id
        _log("  [%s] Created instance id=%s." % (name[:20], temp_id), "green")

        stocks = list(STAGE1_STOCKS)
        _log("  [%s] Stocks (stage 1): %s" % (name[:20], stocks), "white")

        # Increment count and check if daily goal is exceeded
        inc = client.post("/agent/increment-count", {})
        if inc:
            count_now = inc.get("count_today", 0)
            _log("  [%s] Backtest count today: %d" % (name[:20], count_now), "white")
            if count_now > daily_goal:
                _log("  [%s] Daily goal exceeded (%d/%d); aborting this strategy." % (name[:20], count_now, daily_goal), "yellow")
                client.delete(f"/instances/{temp_id}?force=true")
                client.delete(f"/strategies/{strategy_id}?force=true")
                return

        start_date, end_date = FIXED_START_DATE, FIXED_END_DATE
        granularity_sec = 86400
        _log("  [%s] Queuing backtest: %s to %s ..." % (name[:20], start_date, end_date), "white")
        br = client.post("/backtests", {
            "instance_id": temp_id,
            "stocks": stocks,
            "start_date": start_date,
            "end_date": end_date,
            "granularity": str(granularity_sec),
            "initial_cash": 10000.0,
        })
        if not br or "id" not in br:
            _log("  [%s] Failed to create backtest: %s" % (name[:20], br), "red")
            try:
                client.delete(f"/instances/{temp_id}?force=true")
                client.delete(f"/strategies/{strategy_id}?force=true")
            except Exception:
                pass
            return
        backtest_id = br["id"]
        _log("  [%s] Backtest id=%s. Polling every %ds, timeout %ds." % (name[:20], backtest_id, BACKTEST_POLL_INTERVAL, BACKTEST_TIMEOUT_SEC), "cyan")
        try:
            period_str = "%s → %s" % (start_date, end_date) if start_date and end_date else None
        except Exception:
            period_str = None
        _safe_edit_discord_slot(idx, name, "Running stage 1 backtest...", color=0x3498DB, period=period_str, stocks=stocks)
        _cycle_stages = [{"label": "Stage 1: Initial Backtest", "status": "running", "stocks": list(stocks)}]
        _cycle_log_update(client, _cycle_log_id, _cycle_stages, "running")

        # Poll for backtest completion
        deadline = time.time() + BACKTEST_TIMEOUT_SEC
        poll_count = 0
        while time.time() < deadline:
            if _stop_requested():
                _log("  [%s] Stop requested; exiting backtest wait." % name[:20], "yellow")
                break
            if _is_waiting_for_resume_timer():
                _log("  [%s] Waiting for resume timer; backtest state unchanged." % name[:20], "cyan")
                while _is_waiting_for_resume_timer() and not _stop_requested():
                    time.sleep(10)
                if _stop_requested():
                    break
                _log("  [%s] Resume timer reached; continuing." % name[:20], "green")
            elif _pause_requested():
                _log("  [%s] Agent paused; pausing backtest." % name[:20], "cyan")
                if backtest_id:
                    try:
                        client.post("/backtests/%s/pause" % backtest_id, {})
                    except Exception:
                        pass
                while _pause_requested() and not _stop_requested() and not _is_waiting_for_resume_timer():
                    time.sleep(2)
                if backtest_id and not _pause_requested() and not _is_waiting_for_resume_timer():
                    try:
                        client.post("/backtests/%s/resume" % backtest_id, {})
                        _log("  [%s] Agent resumed; backtest resumed." % name[:20], "cyan")
                    except Exception:
                        pass
            chunk = min(CONTROL_POLL_INTERVAL, BACKTEST_POLL_INTERVAL)
            remaining = BACKTEST_POLL_INTERVAL
            while remaining > 0 and not _stop_requested():
                _stop_event.wait(timeout=min(chunk, remaining))
                remaining -= chunk
            if _stop_requested():
                break
            poll_count += 1
            st = client.get("/backtests/%s/status" % backtest_id)
            if not st:
                if poll_count % 4 == 0:
                    _log("  [%s] Waiting for backtest %s... (no status yet)" % (name[:20], backtest_id), "yellow")
                continue
            status = (st.get("status") or "").lower()
            progress = st.get("progress")
            if poll_count % 2 == 0 or status not in ("pending", "running", ""):
                prog_str = (" progress=%s" % progress) if progress is not None else ""
                _log("  [%s] Backtest %s status=%s%s" % (name[:20], backtest_id, status, prog_str), "white")
                # Update Discord slot with backtest progress (same throttle as log)
                try:
                    if progress is not None:
                        _safe_edit_discord_slot(
                            idx, name, "Running stage 1 backtest... %.1f%%" % float(progress),
                            color=0x3498DB, period=period_str, stocks=stocks,
                        )
                    elif status and status not in ("pending", "running", ""):
                        _safe_edit_discord_slot(idx, name, "Stage 1: %s" % status, color=0x3498DB, period=period_str, stocks=stocks)
                except Exception:
                    pass
            if status in ("completed", "done", "complete", "finished"):
                _log("  [%s] Backtest %s completed." % (name[:20], backtest_id), "green")
                break
            if status in ("failed", "error", "cancelled", "stopped"):
                _log("  [%s] Backtest %s failed: %s" % (name[:20], backtest_id, status), "red")
                break
        else:
            if not _stop_requested():
                _log("  [%s] Backtest %s timed out." % (name[:20], backtest_id), "red")
            _safe_edit_discord_slot(idx, name, "Stage 1 timed out", result="Timed out", color=0xE74C3C, period=period_str, stocks=stocks)
            try:
                client.delete(f"/instances/{temp_id}?force=true")
                client.delete(f"/strategies/{strategy_id}?force=true")
            except Exception:
                pass
            return

        if _stop_requested():
            try:
                client.post("/backtests/%s/stop" % backtest_id, {})
            except Exception:
                pass
            _safe_edit_discord_slot(idx, name, "Strategy stopped/canceled", color=0x95A5A6, period=period_str, stocks=stocks)
            try:
                client.delete(f"/instances/{temp_id}?force=true")
            except Exception:
                pass
            try:
                client.delete(f"/strategies/{strategy_id}?force=true")
            except Exception:
                pass
            return

        _log("  [%s] Fetching backtest summary ..." % name[:20], "white")
        summary = client.get(f"/backtests/{backtest_id}/summary")
        if not summary:
            _log("  [%s] Could not get backtest summary." % name[:20], "red")
            try:
                client.delete(f"/instances/{temp_id}?force=true")
            except Exception:
                pass
            try:
                client.delete(f"/strategies/{strategy_id}?force=true")
            except Exception:
                pass
            return
        pnl = summary.get("pnl") if summary.get("pnl") is not None else 0
        pnl_pct = summary.get("pnl_percent") if summary.get("pnl_percent") is not None else 0
        _log("  [%s] Summary: P&L=$%.2f (%.2f%%), trades=%s" % (name[:20], pnl, pnl_pct, summary.get("total_trades")), "cyan")

        keep, notes = _validate_result_llm(summary, strategy_snapshot, validation_llm_config)
        decision = "KEEP" if keep else "TOSS"
        stage1_info = {"pnl": pnl, "pnl_pct": pnl_pct, "decision": decision, "reason": (notes or "—")[:400]}
        _safe_edit_discord_slot(idx, name, "Stage 1 complete", stage1=stage1_info, result=None if keep else "Tossed", color=0x2ECC71 if keep else 0xE67E22, reason=notes, period=period_str, stocks=stocks)
        _cycle_stages = [{"label": "Stage 1: Initial Backtest", "status": "passed" if keep else "tossed", "pnl": round(float(pnl), 2), "pnl_pct": round(float(pnl_pct), 2), "stocks": list(stocks), "details": (notes or "")[:300]}]
        _cycle_log_update(client, _cycle_log_id, _cycle_stages, "running" if keep else "tossed", None if keep else ("Tossed: %s" % (notes or "")[:150]))

        # ── Duplicate detection: skip if another strategy in this cycle produced identical Stage 1 results ──
        if keep and stage1_seen_lock is not None and stage1_seen_fingerprints is not None:
            total_trades = summary.get("total_trades") or 0
            # Fingerprint: rounded P&L (to 2 decimal places) + total trades count
            fingerprint = (round(pnl, 2), int(total_trades))
            with stage1_seen_lock:
                if fingerprint in stage1_seen_fingerprints:
                    is_duplicate = True
                else:
                    stage1_seen_fingerprints.add(fingerprint)
                    is_duplicate = False
            if is_duplicate:
                _log("  [%s] Duplicate detected: P&L $%.2f with %d trades matches a previous strategy in this cycle. Skipping." % (
                    name[:20], pnl, total_trades), "yellow")
                _safe_edit_discord_slot(
                    idx, name, "Skipped (duplicate)",
                    stage1=stage1_info,
                    result="Skipped — identical Stage 1 results as another strategy (P&L $%.2f, %d trades)" % (pnl, total_trades),
                    color=0x95A5A6, reason=notes, period=period_str, stocks=stocks,
                )
                _cycle_log_update(client, _cycle_log_id, _cycle_stages, "duplicate", "Skipped — identical Stage 1 results as another strategy in this cycle")
                # Clean up temporary instance and strategy before returning
                try:
                    client.delete(f"/instances/{temp_id}?force=true")
                except Exception:
                    pass
                try:
                    client.delete(f"/strategies/{strategy_id}?force=true")
                except Exception:
                    pass
                return

        if keep:
            # Validation stages (run sequentially within this strategy's thread)
            validation_passed = True
            validation_results = []
            if pnl >= VALIDATION_MIN_PNL_THRESHOLD:
                total_validation_runs = 1 + VALIDATION_RANDOM_STOCKS_COUNT
                _log("  [%s] Strategy passed (P&L $%.2f). Validating across %d stage(s)." % (name[:20], pnl, total_validation_runs), "cyan")
                validation_stages = []
                neg_symbols = [s for s in VALIDATION_NEGATIVE_RETURN_SYMBOLS if s]
                if neg_symbols:
                    validation_stages.append(("negative-return", [neg_symbols[0]]))
                for _ in range(VALIDATION_RANDOM_STOCKS_COUNT):
                    validation_stages.append(("random", list(STAGE3_STOCKS)))
                _safe_edit_discord_slot(idx, name, "Running validation (OKLO)...", stage1=stage1_info, color=0x3498DB, reason=notes, period=period_str, stocks=stocks)

                # Track for Discord slot embed (one message per strategy, edited in place)
                embed_oklo = None
                embed_stage3 = None
                # Track whether OKLO result was borderline (small loss but not hard fail)
                # so stage 3 + stage 1 strength can rescue it later.
                oklo_borderline = False
                oklo_relative_to_bnh = 0.0  # strategy pnl_pct minus OKLO B&H change %

                for validation_idx, (stage_name, validation_stocks) in enumerate(validation_stages):
                    if _stop_requested() or _pause_requested() or _is_waiting_for_resume_timer():
                        _log("  [%s] Validation interrupted." % name[:20], "yellow")
                        validation_passed = False
                        if _stop_requested():
                            _safe_edit_discord_slot(idx, name, "Strategy stopped/canceled", stage1=stage1_info, color=0x95A5A6, reason=notes, period=period_str, stocks=stocks)
                        break
                    stage_label = "Stage 2: OKLO Test" if stage_name == "negative-return" else "Stage 3: Validation"
                    stage_color = 0x9B59B6 if stage_name == "negative-return" else 0xE67E22
                    _log("  [%s] Validation %d/%d (%s): stocks %s" % (name[:20], validation_idx + 1, len(validation_stages), stage_name, validation_stocks), "white")
                    _cycle_stages.append({"label": stage_label, "status": "running", "stocks": list(validation_stocks)})
                    _cycle_log_update(client, _cycle_log_id, _cycle_stages, "running")
                    val_start_date, val_end_date = FIXED_START_DATE, FIXED_END_DATE
                    val_br = client.post("/backtests", {
                        "instance_id": temp_id,
                        "stocks": validation_stocks,
                        "start_date": val_start_date,
                        "end_date": val_end_date,
                        "granularity": str(GRANULARITY_1D_SEC),
                        "initial_cash": 10000.0,
                    })
                    if not val_br or "id" not in val_br:
                        _log("  [%s] Validation backtest %d failed to create." % (name[:20], validation_idx + 1), "yellow")
                        validation_passed = False
                        break
                    val_backtest_id = val_br["id"]
                    val_deadline = time.time() + BACKTEST_TIMEOUT_SEC
                    val_completed = False
                    val_poll_count = 0
                    while time.time() < val_deadline:
                        if _stop_requested() or _pause_requested() or _is_waiting_for_resume_timer():
                            if _stop_requested():
                                try:
                                    client.post("/backtests/%s/stop" % val_backtest_id, {})
                                except Exception:
                                    pass
                                _safe_edit_discord_slot(idx, name, "Strategy stopped/canceled", stage1=stage1_info, color=0x95A5A6, reason=notes, period=period_str, stocks=stocks)
                            break
                        time.sleep(BACKTEST_POLL_INTERVAL)
                        val_poll_count += 1
                        val_st = client.get("/backtests/%s/status" % val_backtest_id)
                        if val_st:
                            val_status = (val_st.get("status") or "").lower()
                            val_progress = val_st.get("progress")
                            if val_poll_count % 2 == 0 or val_status not in ("pending", "running", ""):
                                prog_str = (" progress=%s" % val_progress) if val_progress is not None else ""
                                _log("  [%s] [%s] Backtest %s status=%s%s" % (name[:20], stage_label, val_backtest_id, val_status, prog_str), "white")
                                # Update Discord slot with validation backtest progress
                                try:
                                    if val_progress is not None:
                                        _safe_edit_discord_slot(
                                            idx, name, "%s... %.1f%%" % (stage_label, float(val_progress)),
                                            stage1=stage1_info, color=stage_color, reason=notes, period=period_str, stocks=stocks,
                                        )
                                except Exception:
                                    pass
                            if val_status in ("completed", "done", "complete", "finished"):
                                val_completed = True
                                break
                            if val_status in ("failed", "error", "cancelled", "stopped"):
                                _log("  [%s] Validation backtest %d failed: %s" % (name[:20], validation_idx + 1, val_status), "yellow")
                                validation_passed = False
                                break
                        elif val_poll_count % 4 == 0:
                            _log("  [%s] [%s] Waiting for backtest %s... (no status yet)" % (name[:20], stage_label, val_backtest_id), "yellow")
                    if not val_completed:
                        _log("  [%s] Validation backtest %d timed out or interrupted." % (name[:20], validation_idx + 1), "yellow")
                        validation_passed = False
                        break
                    val_summary = client.get(f"/backtests/{val_backtest_id}/summary")
                    if not val_summary:
                        _log("  [%s] Validation backtest %d: could not get summary." % (name[:20], validation_idx + 1), "yellow")
                        validation_passed = False
                        break
                    val_pnl = val_summary.get("pnl") if val_summary.get("pnl") is not None else 0
                    val_pnl_pct = val_summary.get("pnl_percent") if val_summary.get("pnl_percent") is not None else 0
                    validation_results.append({
                        "pnl": val_pnl,
                        "pnl_pct": val_pnl_pct,
                        "stocks": validation_stocks,
                        "stage": stage_name,
                        "start_date": val_start_date,
                        "end_date": val_end_date,
                        "pnl_per_stock": val_summary.get("pnl_per_stock"),
                        "pnl_percent_per_stock": val_summary.get("pnl_percent_per_stock"),
                        "stock_price_change": val_summary.get("stock_price_change"),
                        "total_trades": val_summary.get("total_trades"),
                        "win_rate_percent": val_summary.get("win_rate_percent"),
                    })
                    _log("  [%s] Validation %d/%d (%s): P&L $%.2f (%.2f%%)" % (name[:20], validation_idx + 1, len(validation_stages), stage_name, val_pnl, val_pnl_pct), "cyan")

                    # Build per-stock breakdown (used for logging and Discord)
                    per_stock_lines = []
                    pnl_per_stock = val_summary.get("pnl_per_stock") or {}
                    pnl_pct_per_stock = val_summary.get("pnl_percent_per_stock") or {}
                    bnh_per_stock = val_summary.get("stock_price_change") or {}
                    for sym in validation_stocks:
                        sp = pnl_per_stock.get(sym) if pnl_per_stock else None
                        spp = pnl_pct_per_stock.get(sym) if pnl_pct_per_stock else None
                        bnh_raw = bnh_per_stock.get(sym) if bnh_per_stock else None
                        sp = 0 if sp is None else float(sp)
                        spp = 0.0 if spp is None else float(spp)
                        if isinstance(bnh_raw, dict):
                            bnh = float(bnh_raw.get("change_percent") or 0)
                        else:
                            bnh = float(bnh_raw or 0)
                        per_stock_lines.append("%s: $%.2f (%.2f%%) | B&H %.2f%%" % (sym, sp, spp, bnh))

                    # ── OKLO (Stage 2) evaluation — relative-to-B&H aware ────────────────
                    if stage_name == "negative-return":
                        oklo_sym = validation_stocks[0] if validation_stocks else "OKLO"
                        bnh_raw_oklo = bnh_per_stock.get(oklo_sym)
                        if isinstance(bnh_raw_oklo, dict):
                            oklo_bnh_pct = float(bnh_raw_oklo.get("change_percent") or 0)
                        else:
                            oklo_bnh_pct = float(bnh_raw_oklo or 0)
                        relative = val_pnl_pct - oklo_bnh_pct

                        # Classify the OKLO result (hard fail checked first — large loss never passes)
                        is_hard_fail = (
                            val_pnl_pct < OKLO_HARD_FAIL_ABS_PCT
                            or relative < OKLO_HARD_FAIL_RELATIVE_PCT
                        )
                        # Outright pass: profit, or modest loss that beats B&H (not large loss + good relative)
                        is_outright_pass = (
                            val_pnl_pct >= 0
                            or (val_pnl_pct >= OKLO_MAX_ABS_LOSS_FOR_RELATIVE_PASS and relative >= 0)
                        )
                        is_borderline = not is_hard_fail and not is_outright_pass

                        if is_hard_fail:
                            val_passed_stage = False
                            stage_verdict = "FAILED"
                            stage_icon = "\u274C"
                            stage_color = 0xE74C3C
                            _log("  [%s] [OKLO] Hard fail: strategy=%.2f%% vs B&H=%.2f%% (rel=%.2f%%) — "
                                 "below thresholds (abs<%.0f%% or rel<%.0f%%)." % (
                                     name[:20], val_pnl_pct, oklo_bnh_pct, relative,
                                     OKLO_HARD_FAIL_ABS_PCT, OKLO_HARD_FAIL_RELATIVE_PCT), "red")
                            validation_passed = False
                        elif is_outright_pass:
                            val_passed_stage = True
                            stage_verdict = "PASSED"
                            stage_icon = "\u2705"
                            stage_color = 0x2ECC71
                            _log("  [%s] [OKLO] Passed: strategy=%.2f%% vs B&H=%.2f%% (rel=+%.2f%%)" % (
                                name[:20], val_pnl_pct, oklo_bnh_pct, relative), "green")
                        elif is_borderline:
                            val_passed_stage = True   # borderline counts as provisional pass
                            oklo_borderline = True
                            oklo_relative_to_bnh = relative
                            stage_verdict = "BORDERLINE"
                            stage_icon = "\u26A0\uFE0F"
                            stage_color = 0xF39C12
                            _log("  [%s] [OKLO] Borderline (%.2f%% abs, %.2f%% rel to B&H %.2f%%) — "
                                 "continuing to stage 3 for rescue check." % (
                                     name[:20], val_pnl_pct, relative, oklo_bnh_pct), "yellow")

                        extra_fields = [
                            {"name": "OKLO B&H", "value": "%.2f%%" % oklo_bnh_pct, "inline": True},
                            {"name": "Rel. to B&H", "value": "%+.2f%%" % relative, "inline": True},
                        ]
                        if is_borderline:
                            extra_fields.append({"name": "Note", "value": "Borderline — stage 3 result will decide final outcome.", "inline": False})
                    # ── All other stages (Stage 3, etc.) — original P&L gate ─────────────
                    else:
                        val_passed_stage = val_pnl >= 0
                        stage_verdict = "PASSED" if val_passed_stage else "FAILED"
                        stage_icon = "\u2705" if val_passed_stage else "\u274C"
                        stage_color = 0x2ECC71 if val_passed_stage else 0xE74C3C
                        extra_fields = []
                        if not val_passed_stage:
                            _log("  [%s] Validation backtest %d: negative P&L ($%.2f)." % (name[:20], validation_idx + 1, val_pnl), "yellow")
                            validation_passed = False

                    if stage_name == "negative-return":
                        detail = "B&H %.2f%%, rel %+.2f%%" % (oklo_bnh_pct, relative)
                        embed_oklo = {"pnl": val_pnl, "pnl_pct": val_pnl_pct, "verdict": stage_verdict, "detail": detail}
                    else:
                        embed_stage3 = {"pnl": val_pnl, "pnl_pct": val_pnl_pct, "verdict": stage_verdict}
                    _cycle_stages[-1] = {"label": stage_label, "status": "passed" if val_passed_stage else "failed", "pnl": round(float(val_pnl), 2), "pnl_pct": round(float(val_pnl_pct), 2), "stocks": list(validation_stocks), "details": stage_verdict}
                    _cycle_log_update(client, _cycle_log_id, _cycle_stages, "running")
                    # Show "Validation complete" if this is the last stage OR if OKLO hard-failed (we'll break next)
                    oklo_hard_failed = (stage_name == "negative-return" and not val_passed_stage and is_hard_fail)
                    if oklo_hard_failed or validation_idx + 1 >= len(validation_stages):
                        next_status = "Validation complete"
                    else:
                        next_status = "Running validation (Stage 3)..."
                    _safe_edit_discord_slot(idx, name, next_status, stage1=stage1_info, oklo=embed_oklo, stage3=embed_stage3, color=stage_color, reason=notes, period=period_str, stocks=stocks)

                    # Break on failure: hard fail at OKLO (skip stage 3), or any non-OKLO stage fail.
                    # Borderline OKLO continues to stage 3 for rescue check.
                    if not val_passed_stage:
                        if stage_name == "negative-return" and is_hard_fail:
                            _log("  [%s] OKLO hard fail — skipping remaining validation stages." % name[:20], "red")
                            break
                        elif stage_name != "negative-return":
                            break
                # ── OKLO borderline rescue check ─────────────────────────────────────
                # If OKLO was borderline (small loss, not hard fail), allow the strategy
                # through only when BOTH stage 1 AND stage 3 show strong positive returns.
                if validation_passed and oklo_borderline:
                    stage3_results = [r for r in validation_results if r["stage"] == "random"]
                    avg_stage3_pct = (
                        sum(r["pnl_pct"] for r in stage3_results) / len(stage3_results)
                        if stage3_results else 0.0
                    )
                    stage1_strong = pnl_pct >= OKLO_RESCUE_STAGE1_MIN_PCT
                    stage3_strong = avg_stage3_pct >= OKLO_RESCUE_STAGE3_MIN_PCT
                    if stage1_strong and stage3_strong:
                        _log("  [%s] [OKLO borderline] Rescued — stage 1=%.2f%% (need≥%.0f%%) + "
                             "stage 3 avg=%.2f%% (need≥%.0f%%)." % (
                                 name[:20], pnl_pct, OKLO_RESCUE_STAGE1_MIN_PCT,
                                 avg_stage3_pct, OKLO_RESCUE_STAGE3_MIN_PCT), "green")
                        _safe_edit_discord_slot(idx, name, "Validation complete", stage1=stage1_info, oklo=embed_oklo, stage3=embed_stage3, result="OKLO borderline rescued (stage 1 + stage 3 strong)", color=0x2ECC71, reason=notes, period=period_str, stocks=stocks)
                    else:
                        _log("  [%s] [OKLO borderline] Not rescued — stage 1=%.2f%% (need≥%.0f%%), "
                             "stage 3 avg=%.2f%% (need≥%.0f%%)." % (
                                 name[:20], pnl_pct, OKLO_RESCUE_STAGE1_MIN_PCT,
                                 avg_stage3_pct, OKLO_RESCUE_STAGE3_MIN_PCT), "yellow")
                        _safe_edit_discord_slot(idx, name, "Validation complete", stage1=stage1_info, oklo=embed_oklo, stage3=embed_stage3, result="OKLO borderline — not rescued", color=0xE74C3C, reason=notes, period=period_str, stocks=stocks)
                        validation_passed = False

                if validation_passed:
                    avg_val_pnl = sum(r["pnl"] for r in validation_results) / len(validation_results) if validation_results else 0
                    _log("  [%s] Validation passed! Avg validation P&L: $%.2f across %d backtests." % (name[:20], avg_val_pnl, len(validation_results)), "green")
                    _safe_edit_discord_slot(idx, name, "Validation complete", stage1=stage1_info, oklo=embed_oklo, stage3=embed_stage3, result="All validation passed (avg P&L $%.2f)" % avg_val_pnl, color=0x2ECC71, reason=notes, period=period_str, stocks=stocks)
                    _cycle_log_update(client, _cycle_log_id, _cycle_stages, "passed", "All stages passed (avg P&L $%.2f)" % avg_val_pnl)
                else:
                    _log("  [%s] Validation failed: strategy did not perform consistently." % name[:20], "yellow")
                    failed_at = len(validation_results)
                    _safe_edit_discord_slot(idx, name, "Validation complete", stage1=stage1_info, oklo=embed_oklo, stage3=embed_stage3, result="Validation failed at stage %d/%d" % (failed_at + 1, len(validation_stages)), color=0xE74C3C, reason=notes, period=period_str, stocks=stocks)
                    _cycle_log_update(client, _cycle_log_id, _cycle_stages, "failed", "Validation failed at stage %d/%d" % (failed_at + 1, len(validation_stages)))
                    try:
                        client.delete(f"/instances/{temp_id}?force=true")
                    except Exception:
                        pass
                    try:
                        client.delete(f"/strategies/{strategy_id}?force=true")
                    except Exception:
                        pass
                    return

            # Build summarized results and store
            initial_summary = dict(summary)
            initial_summary["stocks_used"] = stocks
            initial_summary["start_date"] = start_date
            initial_summary["end_date"] = end_date
            candidate_results_summary = {
                "initial": initial_summary,
                "validation_notes": notes,
                "validation_stages": list(validation_results),
            }
            client.post("/agent/results", {
                "strategy_snapshot": strategy_snapshot,
                "backtest_id": backtest_id,
                "instance_id": instance_id,
                "strategy_id": strategy_id,
                "overall_profit": pnl,
                "pnl_percent": pnl_pct,
                "pnl_per_stock": summary.get("pnl_per_stock"),
                "pnl_percent_per_stock": summary.get("pnl_percent_per_stock"),
                "stock_price_change": summary.get("stock_price_change"),
                "start_date": start_date,
                "end_date": end_date,
                "stocks_used": stocks,
                "status": "passed",
                "agent_notes": notes + (" (validated with %d additional backtests)" % len(validation_results) if validation_results else ""),
            })
            _log("  [%s] Kept strategy (P&L $%.2f): %s" % (name[:20], pnl, notes), "green")

            # Best strategy selection
            best_resp = client.get("/agent/best")
            current_best = (best_resp or {}).get("best") if best_resp else None
            current_profit = float(current_best.get("overall_profit") or 0) if current_best else None
            should_set_best = False
            if best_selection_llm_config.get("api_key"):
                should_set_best, best_reason = _decide_new_best_llm(
                    strategy_snapshot, pnl, pnl_pct, candidate_results_summary, current_best, best_selection_llm_config
                )
            else:
                should_set_best = current_profit is None or pnl > current_profit
            if should_set_best:
                set_resp = client.post("/agent/best", {
                    "strategy_snapshot": strategy_snapshot,
                    "overall_profit": pnl,
                    "pnl_percent": pnl_pct,
                    "results_summary": candidate_results_summary,
                })
                if set_resp and set_resp.get("set"):
                    _log("  [%s] Set as new best strategy (P&L $%.2f)." % (name[:20], pnl), "cyan")
                    _safe_edit_discord_slot(idx, name, "Validation complete", stage1=stage1_info, oklo=embed_oklo, stage3=embed_stage3, result="Set new best ✓", color=0x2ECC71, reason=notes, period=period_str, stocks=stocks)
                    subs_snap = strategy_snapshot.get("strategies") or []
                    sub_fields = []
                    for s in subs_snap[:10]:
                        st = (s.get("strategy") or "?").strip() or "?"
                        w = s.get("weight")
                        wstr = ("%.2f" % w) if w is not None else "?"
                        phase = s.get("decision_phase") or "pre"
                        scope = s.get("execution_scope") or "per_symbol"
                        cfg = dict(s.get("config") or {})
                        conds = dict(s.get("conditions") or {})
                        for secret_key in ("llm_api_key", "alpaca_key", "alpaca_secret", "api_key"):
                            if secret_key in cfg:
                                cfg[secret_key] = "***"
                        detail_lines = [
                            "**Weight:** %s | **Phase:** %s | **Scope:** %s" % (wstr, phase, scope),
                        ]
                        if cfg:
                            cfg_str = ", ".join("%s=%s" % (k, v) for k, v in cfg.items())
                            detail_lines.append("**Config:** %s" % cfg_str)
                        if conds:
                            conds_str = ", ".join("%s=%s" % (k, v) for k, v in conds.items())
                            detail_lines.append("**Conditions:** %s" % conds_str)
                        sub_fields.append({"name": "📊 %s" % st, "value": "\n".join(detail_lines), "inline": False})
                    if len(subs_snap) > 10:
                        sub_fields.append({"name": "...", "value": "+%d more sub-strategies" % (len(subs_snap) - 10), "inline": False})
                    _send_discord(embed={
                        "title": "🏆 New Best Strategy",
                        "description": "The AI agent found a new best-performing strategy and saved it.",
                        "color": 0x2ECC71,
                        "fields": [
                            {"name": "Strategy Name", "value": strategy_snapshot.get("name") or "—", "inline": True},
                            {"name": "Strategy ID", "value": str(strategy_id) if strategy_id else "—", "inline": True},
                            {"name": "P&L", "value": "$%.2f (%.2f%%)" % (pnl, pnl_pct), "inline": True},
                            {"name": "Period", "value": "%s → %s" % (start_date, end_date), "inline": False},
                        ] + sub_fields,
                    }, channel="best-strategies")
            else:
                # Validation passed but did not set new best
                _safe_edit_discord_slot(idx, name, "Validation complete", stage1=stage1_info, oklo=embed_oklo, stage3=embed_stage3, result="Kept — did not set new best", color=0x2ECC71, reason=notes, period=period_str, stocks=stocks)

            # ── Top-5 update (every kept strategy competes, regardless of new-best status) ──
            try:
                top5_resp = client.post("/agent/top5", {
                    "strategy_snapshot": strategy_snapshot,
                    "overall_profit": pnl,
                    "pnl_percent": pnl_pct,
                    "strategy_id": strategy_id,
                    "backtest_id": backtest_id,
                    "results_summary": candidate_results_summary,
                })
                if top5_resp and top5_resp.get("entered"):
                    rank = top5_resp.get("rank") or 0
                    dethroned = top5_resp.get("dethroned")
                    rank_emoji = {1: "🥇", 2: "🥈", 3: "🥉"}.get(rank, "⭐")
                    rank_label = "#%d" % rank
                    _log("  [%s] Entered Top-5 at rank %s (P&L $%.2f / %.2f%%)." % (name[:20], rank_label, pnl, pnl_pct), "cyan")
                    # Build top-5 leaderboard for Discord
                    top5_entries = top5_resp.get("top5") or []
                    board_lines = []
                    for e in top5_entries:
                        r_emoji = {1: "🥇", 2: "🥈", 3: "🥉"}.get(e.get("rank"), "⭐")
                        ename = (e.get("strategy_snapshot") or {}).get("name") or "Strategy #%s" % e.get("strategy_id", "?")
                        board_lines.append("%s **%s** — $%.2f (%.2f%%)" % (
                            r_emoji, ename[:40],
                            float(e.get("overall_profit") or 0),
                            float(e.get("pnl_percent") or 0),
                        ))
                    leaderboard_text = "\n".join(board_lines) if board_lines else "—"
                    dethroned_text = ("Dethroned: **%s**" % dethroned) if dethroned else "First entry in Top 5"
                    subs_snap = strategy_snapshot.get("strategies") or []
                    sub_fields = []
                    for s in subs_snap[:5]:
                        st = (s.get("strategy") or "?").strip()
                        w = s.get("weight")
                        wstr = ("%.2f" % w) if w is not None else "?"
                        sub_fields.append({"name": "📊 %s" % st, "value": "weight=%s | phase=%s" % (wstr, s.get("decision_phase") or "pre"), "inline": True})
                    if len(subs_snap) > 5:
                        sub_fields.append({"name": "...", "value": "+%d more" % (len(subs_snap) - 5), "inline": True})
                    _send_discord(embed={
                        "title": "%s Top-5 Strategies — New Rank %s" % (rank_emoji, rank_label),
                        "description": "A strategy entered the Top-5 leaderboard.\n%s" % dethroned_text,
                        "color": 0xF1C40F if rank == 1 else (0xC0C0C0 if rank == 2 else (0xCD7F32 if rank == 3 else 0x3498DB)),
                        "fields": [
                            {"name": "Strategy", "value": strategy_snapshot.get("name") or "—", "inline": True},
                            {"name": "P&L", "value": "$%.2f (%.2f%%)" % (pnl, pnl_pct), "inline": True},
                            {"name": "Period", "value": "%s → %s" % (start_date, end_date), "inline": True},
                            {"name": "🏆 Top-5 Leaderboard", "value": leaderboard_text, "inline": False},
                        ] + sub_fields,
                    }, channel="best-strategies")
            except Exception as _top5_err:
                _log("  [%s] Warning: Top-5 update failed: %s" % (name[:20], _top5_err), "yellow")

        # Clean up temporary instance
        try:
            client.delete(f"/instances/{temp_id}?force=true")
            _log("  [%s] Temporary instance cleaned up." % name[:20], "green")
        except Exception as e:
            _log("  [%s] Warning: Failed to clean up instance: %s" % (name[:20], e), "yellow")

        if not keep:
            _log("  [%s] Tossed strategy: %s" % (name[:20], notes), "yellow")
            try:
                client.delete(f"/strategies/{strategy_id}?force=true")
                _log("  [%s] Temporary strategy cleaned up." % name[:20], "green")
            except Exception as e:
                _log("  [%s] Warning: Failed to clean up strategy: %s" % (name[:20], e), "yellow")

    except Exception as e:
        _log_error("STRATEGY_RUN", e, "name=%s" % _safe_str(name, 40))
        try:
            _safe_edit_discord_slot(idx, name, "Error", result=_safe_str(str(e), 300), color=0xE74C3C)
        except Exception:
            pass
        if instance_id:
            try:
                client.delete(f"/instances/{instance_id}?force=true")
            except Exception:
                pass
        if strategy_id:
            try:
                client.delete(f"/strategies/{strategy_id}?force=true")
            except Exception:
                pass


def run_one_cycle(
    client: APIClient,
    strategy_llm_config: dict,
    validation_llm_config: dict,
    best_selection_llm_config: dict,
    daily_goal: int,
    batch_size: int,
    stock_pool: list[str],
    ai_brokerage_id: "str | None" = None,
) -> None:
    if _stop_requested():
        return
    if _is_waiting_for_resume_timer():
        _log("Waiting for resume timer. Skipping cycle.", "cyan")
        return
    if _pause_requested():
        _log("Agent paused. Skipping cycle.", "cyan")
        return
    try:
        control = client.get("/agent/control")
        if not control:
            _log("GET /agent/control failed (no response or auth error).", "yellow")
            return
        running = control.get("running")
        paused = control.get("paused", False)
        count_today = int(control.get("count_today") or 0)
        last_date = control.get("last_run_date") or "-"
        special_request = (control.get("special_request") or "").strip() or ""
        _log("Agent control: running=%s, paused=%s, count_today=%d, last_run_date=%s" % (running, paused, count_today, last_date), "cyan")
        if special_request:
            _log("  Special request: %s" % (special_request[:60] + "..." if len(special_request) > 60 else special_request), "cyan")
        if not running:
            _log("Agent not running. Skipping cycle.", "white")
            return
        if paused:
            _log("Agent paused. Skipping cycle.", "cyan")
            return
    except Exception as e:
        _log_error("AGENT_CONTROL_CHECK", e)
        return
    if count_today >= daily_goal:
        _log("Daily goal reached (%d/%d). Ensuring best strategy is set and pausing until next day." % (count_today, daily_goal), "cyan")
        _send_discord(embed={
            "title": "Daily Goal Reached",
            "description": "AI backtest agent reached today's goal. Pausing until next day.",
            "color": 0x9B59B6,
            "fields": [
                {"name": "Backtests today", "value": str(count_today), "inline": True},
                {"name": "Daily goal", "value": str(daily_goal), "inline": True},
            ],
        })
        # Ensure best strategy is set from all results (best is set incrementally, but verify it's correct)
        try:
            results_resp = client.get("/agent/results?limit=1000")
            if results_resp and results_resp.get("results"):
                results = results_resp["results"]
                # Find the best result overall
                best_pnl = None
                best_result = None
                for r in results:
                    pnl = r.get("overall_profit")
                    if pnl is not None:
                        pnl_float = float(pnl)
                        if best_pnl is None or pnl_float > best_pnl:
                            best_pnl = pnl_float
                            best_result = r
                if best_result:
                    # Check if this is already the best (daily goal: numeric comparison only)
                    best_resp = client.get("/agent/best")
                    current_best = (best_resp or {}).get("best") if best_resp else None
                    current_profit = float(current_best.get("overall_profit") or 0) if current_best else None
                    if current_profit is None or best_pnl > current_profit:
                        results_summary_from_result = {
                            "initial": {
                                "pnl": best_result.get("overall_profit"),
                                "pnl_percent": best_result.get("pnl_percent"),
                                "pnl_per_stock": best_result.get("pnl_per_stock"),
                                "pnl_percent_per_stock": best_result.get("pnl_percent_per_stock"),
                                "stock_price_change": best_result.get("stock_price_change"),
                                "start_date": best_result.get("start_date"),
                                "end_date": best_result.get("end_date"),
                                "stocks_used": best_result.get("stocks_used"),
                            },
                            "validation_notes": best_result.get("agent_notes") or "",
                            "validation_stages": [],
                        }
                        set_resp = client.post("/agent/best", {
                            "strategy_snapshot": best_result.get("strategy_snapshot", {}),
                            "overall_profit": best_result.get("overall_profit"),
                            "pnl_percent": best_result.get("pnl_percent"),
                            "results_summary": results_summary_from_result,
                        })
                        if set_resp and set_resp.get("set"):
                            _log("  Set best strategy from results (P&L $%.2f)." % best_pnl, "green")
                        else:
                            _log("  Best strategy already set or update failed.", "yellow")
                    else:
                        _log("  Best strategy is already set correctly (current: $%.2f, best found: $%.2f)." % (current_profit, best_pnl), "cyan")
                else:
                    _log("  No profitable results found to set as best.", "yellow")
        except Exception as e:
            _log_error("ENSURE_BEST_STRATEGY", e)
        
        # Pause until next day (when last_run_date changes or count_today resets)
        _log("Pausing until next day (when count_today resets)...", "cyan")
        last_date_when_goal_reached = last_date
        while True:
            if _stop_requested():
                return
            if _pause_requested():
                # If manually paused, wait for resume (but still check for new day)
                while _pause_requested() and not _stop_requested():
                    time.sleep(2)
                if _stop_requested():
                    return
            time.sleep(60)  # Check every minute
            try:
                control_check = client.get("/agent/control")
                if control_check:
                    new_count = int(control_check.get("count_today") or 0)
                    new_last_date = control_check.get("last_run_date")
                    # If count reset to 0 or last_run_date changed, new day has started
                    if new_count == 0 or (new_last_date and new_last_date != last_date_when_goal_reached):
                        _log("New day detected (count_today=%d, last_run_date=%s). Resuming work." % (new_count, new_last_date), "green")
                        break
            except Exception as e:
                _log_error("CHECK_NEW_DAY", e)
        return

    to_run = min(batch_size, daily_goal - count_today)
    _log("Running cycle: generating up to %d strategies (goal %d/day, %d done today)." % (to_run, daily_goal, count_today), "cyan")
    strategies = _generate_strategies(client, strategy_llm_config, to_run, special_request=special_request)
    if not strategies:
        _log("No strategies generated by LLM. Check model/API key and prompt.", "yellow")
        return

    # Notify Discord: new strategies generated
    strategy_lines = []
    for i, s in enumerate(strategies[:10]):
        name = (s.get("name") or "?").strip() or "?"
        subs = s.get("strategies") or []
        parts = ["%s (%.2f)" % (sub.get("strategy", "?"), float(sub.get("weight", 0))) for sub in subs[:5]]
        if len(subs) > 5:
            parts.append("...")
        strategy_lines.append("**%d.** %s → %s" % (i + 1, name, ", ".join(parts)))
    _send_discord(embed={
        "title": "New Strategies Generated",
        "description": "LLM generated %d strategy config(s) for this cycle." % len(strategies),
        "color": 0x3498DB,
        "fields": [{"name": "Strategies", "value": "\n".join(strategy_lines) if strategy_lines else "—", "inline": False}],
    })

    # One message per strategy; each will be edited in place as that strategy progresses (no extra messages).
    # Track message keys for this cycle only — when agent stops we delete only these (current chunk), not previous cycles.
    current_chunk_message_keys = [_agent_slot_message_key(i) for i in range(len(strategies))]
    for idx in range(len(strategies)):
        try:
            name = (strategies[idx].get("name") or "?").strip() or "?"
            _send_discord_slot_initial(idx, name)
        except Exception as e:
                _log_error("DISCORD_INITIAL_SLOT", e, "idx=%d" % idx)

    # --- Parallel strategy execution ---
    # Submit all strategies to a thread pool capped at MAX_PARALLEL_BACKTESTS.
    # Each thread runs the full lifecycle for one strategy (create → submit backtest →
    # wait for backtest engine to finish → validate → save). CPU throttling is handled
    # by the backtest engine before it starts each Docker container.
    _log("Running %d strategies in parallel (max %d concurrent threads)." % (
        len(strategies), MAX_PARALLEL_BACKTESTS), "cyan")

    # Generate a shared cycle_id for all strategies in this cycle run.
    import uuid as _uuid_mod
    _cycle_id = str(_uuid_mod.uuid4())

    # Thread-safe set to detect duplicate Stage 1 results across parallel strategies.
    # Stores (rounded_pnl, total_trades) tuples; if a new strategy matches, skip validation.
    _stage1_seen_lock = threading.Lock()
    _stage1_seen_fingerprints: set[tuple] = set()

    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_PARALLEL_BACKTESTS) as executor:
        futures = []
        for idx, strat_cfg in enumerate(strategies):
            if _stop_requested():
                break
            _log("[Parallel] Submitting strategy %d/%d: '%s'" % (
                idx + 1, len(strategies), (strat_cfg.get("name") or "?")[:40]), "cyan")
            futures.append(executor.submit(
                _run_one_strategy, client, idx, len(strategies),
                strat_cfg, validation_llm_config, best_selection_llm_config, daily_goal,
                _stage1_seen_lock, _stage1_seen_fingerprints, ai_brokerage_id, _cycle_id,
            ))
        for f in concurrent.futures.as_completed(futures):
            try:
                f.result()
            except Exception as exc:
                _log_error("STRATEGY_THREAD", exc)
    # (end of parallel block — all threads have completed)

    # When agent was stopped mid-cycle, delete the current chunk's Discord slot messages (not previous cycles).
    if _stop_requested() and current_chunk_message_keys:
        try:
            from interactive_utils import get_conn
            from discord_sender import enqueue_discord_delete
            conn = get_conn()
            for key in current_chunk_message_keys:
                enqueue_discord_delete(conn, DISCORD_AGENT_CHANNEL, key)
            conn.close()
        except Exception as e:
            _log_error("DISCORD_DELETE_CHUNK", e)


def main():
    api_url = _env("API_URL", "http://localhost:8011")
    username = _env("AGENT_API_USERNAME") or _env("DEFAULT_ADMIN_USERNAME")
    password = _env("AGENT_API_PASSWORD") or _env("DEFAULT_ADMIN_PASSWORD")
    strategy_llm_config = _get_llm_config("strategy_generation")
    validation_llm_config = _get_llm_config("validation")
    best_selection_llm_config = _get_llm_config("best_selection")
    daily_goal = _env_int("AI_BACKTESTING_DAILY_GOAL", 100)
    batch_size = _env_int("AI_BACKTESTING_BATCH_SIZE", 5)
    stock_pool_str = _env("AI_BACKTESTING_STOCK_POOL")
    stock_pool = [s.strip().upper() for s in stock_pool_str.split(",") if s.strip()] if stock_pool_str else DEFAULT_STOCK_POOL

    if not username or not password:
        _log("Set AGENT_API_USERNAME/AGENT_API_PASSWORD or DEFAULT_ADMIN_USERNAME/DEFAULT_ADMIN_PASSWORD", "red")
        sys.exit(1)
    if not strategy_llm_config.get("api_key"):
        _log("Set API key for strategy generation: AI_BACKTESTING_STRATEGY_GENERATION_API_KEY, or GEMINI_API_KEY/DEEPSEEK_API_KEY/OPENAI_API_KEY/AZURE_OPENAI_API_KEY per provider", "yellow")
    if not validation_llm_config.get("api_key"):
        _log("Set API key for validation: AI_BACKTESTING_VALIDATION_API_KEY, or GEMINI_API_KEY/DEEPSEEK_API_KEY/OPENAI_API_KEY/AZURE_OPENAI_API_KEY per provider", "yellow")
    if not best_selection_llm_config.get("api_key"):
        _log("Set API key for best selection to use LLM when setting new best: AI_BACKTESTING_BEST_SELECTION_API_KEY (optional; else numeric comparison only)", "yellow")

    _log("Logging in to %s as %s ..." % (api_url, username.strip().lower() or "(empty)"), "cyan")
    client = APIClient(api_url, username, password)
    if not client.login():
        _log("Cannot login to API (401 Unauthorized).", "red")
        _log("  - Ensure API_URL is correct and the API is running (e.g. http://localhost:8011).", "yellow")
        _log("  - Start the API at least once so the default admin is created (startup creates it from DEFAULT_ADMIN_*).", "yellow")
        _log("  - Or create a user via POST /auth/signup, then set AGENT_API_USERNAME and AGENT_API_PASSWORD to that user.", "yellow")
        _log("  - Check .env: DEFAULT_ADMIN_USERNAME and DEFAULT_ADMIN_PASSWORD (no extra quotes).", "yellow")
        sys.exit(1)
    # Clean up any AI-created instances left over from previous runs (e.g. crash/timeout)
    try:
        inst_list = client.get("/instances") or {}
        for inst in inst_list.get("instances", []):
            if inst.get("created_by") == "ai" and not inst.get("runCommand", False):
                client.delete(f"/instances/{inst['id']}?force=true")
                _log("  Cleaned up orphaned AI instance: %s" % inst["id"], "yellow")
    except Exception as e:
        _log("Warning: startup cleanup failed: %s" % e, "yellow")

    # Ensure AI engine has its own Alpaca brokerage (created from env vars if not already present)
    ai_brokerage_id = None
    try:
        brok_resp = client.post("/brokerages/ensure-ai-alpaca", {})
        ai_brokerage_id = (brok_resp or {}).get("brokerage_id")
        if ai_brokerage_id:
            _log("AI brokerage ready: %s" % ai_brokerage_id, "green")
    except Exception as e:
        _log("Warning: could not ensure AI brokerage: %s" % e, "yellow")

    _log("AI Backtesting Agent started. Control watcher thread will exit when running=false.", "green")
    _send_discord(embed={
        "title": "AI Backtest Agent Started",
        "description": "Agent is running and will generate strategies, run backtests, and validate results.",
        "color": 0x2ECC71,
        "footer": "Control via CLI: !agent stop / !agent pause / !agent resume",
    })
    watcher = threading.Thread(target=_watch_control, args=(client,), daemon=True)
    watcher.start()

    # Check if agent is scheduled to resume later - wait until that time
    # Also check running status - if running=False, exit (don't wait for timer if explicitly stopped)
    try:
        control = client.get("/agent/control")
        if control:
            running = control.get("running", False)
            if not running:
                _log("Agent is not running (running=false). Exiting.", "yellow")
                _send_discord(embed={"title": "AI Backtest Agent Exited", "description": "Agent was not running (running=false) at startup.", "color": 0xF1C40F})
                sys.exit(0)
            resume_at_str = control.get("resume_at")
            if resume_at_str:
                from datetime import datetime
                try:
                    resume_at_str_clean = resume_at_str.replace("Z", "+00:00")
                    if "+" not in resume_at_str_clean and resume_at_str_clean.count(":") == 2:
                        resume_at = datetime.fromisoformat(resume_at_str_clean.replace("Z", ""))
                        now = datetime.utcnow()
                    else:
                        resume_at = datetime.fromisoformat(resume_at_str_clean)
                        now = datetime.utcnow().replace(tzinfo=resume_at.tzinfo) if resume_at.tzinfo else datetime.utcnow()
                    if resume_at > now:
                        delta = resume_at - now
                        total_sec = int(delta.total_seconds())
                        _log("Agent scheduled to resume at %s UTC (in %d:%02d:%02d:%02d). Waiting..." % (
                            resume_at.strftime("%Y-%m-%d %H:%M:%S"),
                            total_sec // 86400, (total_sec % 86400) // 3600,
                            (total_sec % 3600) // 60, total_sec % 60
                        ), "cyan")
                        # Wait until resume time, checking every 10 seconds
                        while total_sec > 0 and not _stop_requested():
                            # Re-check control to see if running changed
                            control_check = client.get("/agent/control")
                            if control_check and not control_check.get("running", True):
                                _log("Agent stopped (running=false) while waiting for timer. Exiting.", "yellow")
                                _send_discord(embed={"title": "AI Backtest Agent Stopped", "description": "Stopped (running=false) while waiting for resume timer.", "color": 0xE74C3C})
                                _request_stop_all_backtests(client)
                                sys.exit(0)
                            sleep_time = min(10, total_sec)
                            time.sleep(sleep_time)
                            total_sec -= sleep_time
                            if total_sec > 0 and total_sec % 60 == 0:  # Log every minute
                                _log("Still waiting... %d:%02d:%02d remaining" % (
                                    total_sec // 3600, (total_sec % 3600) // 60, total_sec % 60
                                ), "cyan")
                        if _stop_requested():
                            _log("Agent stopped while waiting for scheduled resume. Exiting.", "yellow")
                            _send_discord(embed={"title": "AI Backtest Agent Stopped", "description": "Stopped while waiting for scheduled resume.", "color": 0xE74C3C})
                            _request_stop_all_backtests(client)
                            sys.exit(0)
                        _log("Scheduled resume time reached! Starting work.", "green")
                except Exception as e:
                    _log_error("PARSE_RESUME_AT_STARTUP", e)
    except Exception as e:
        _log_error("CHECK_RESUME_AT_STARTUP", e)

    cycle_num = 0
    while True:
        if _stop_requested():
            _log("Agent stopped by control (running=false). Exiting.", "yellow")
            _send_discord(embed={"title": "AI Backtest Agent Stopped", "description": "Stopped by control (running=false).", "color": 0xE74C3C})
            _request_stop_all_backtests(client)
            sys.exit(0)
        # Check for pause - wait until resumed
        if _pause_requested():
            _log("Agent paused; waiting for resume...", "cyan")
            _send_discord(embed={"title": "AI Backtest Agent Paused", "description": "Agent is paused. Current backtest (if any) is also paused. Use !agent resume to continue.", "color": 0xE67E22})
            try:
                while _pause_requested() and not _stop_requested():
                    time.sleep(2)
            except KeyboardInterrupt:
                _log("Interrupted while paused. Exiting.", "yellow")
                _send_discord(embed={"title": "AI Backtest Agent Exited", "description": "Interrupted while paused.", "color": 0xE74C3C})
                sys.exit(0)
            except Exception as e:
                _log_error("MAIN_WHILE_PAUSED", e)
            if _stop_requested():
                _log("Agent stopped while paused. Exiting.", "yellow")
                _send_discord(embed={"title": "AI Backtest Agent Stopped", "description": "Stopped while paused.", "color": 0xE74C3C})
                _request_stop_all_backtests(client)
                sys.exit(0)
            _log("Agent resumed; continuing work.", "green")
            _send_discord(embed={"title": "AI Backtest Agent Resumed", "description": "Agent resumed. Continuing strategy generation and backtests.", "color": 0x2ECC71})
        cycle_num += 1
        try:
            _log("--- Cycle #%d ---" % cycle_num, "cyan")
            run_one_cycle(
                client,
                strategy_llm_config,
                validation_llm_config,
                best_selection_llm_config,
                daily_goal,
                batch_size,
                stock_pool,
                ai_brokerage_id,
            )
        except Exception as e:
            _log_error("MAIN_CYCLE", e)
        if _stop_requested():
            _log("Agent stopped by control. Exiting.", "yellow")
            _send_discord(embed={"title": "AI Backtest Agent Stopped", "description": "Stopped by control after cycle.", "color": 0xE74C3C})
            _request_stop_all_backtests(client)
            sys.exit(0)
        # Check for pause again before sleeping
        if _pause_requested():
            _log("Agent paused; waiting for resume...", "cyan")
            _send_discord(embed={"title": "AI Backtest Agent Paused", "description": "Agent is paused. Use !agent resume to continue.", "color": 0xE67E22})
            try:
                while _pause_requested() and not _stop_requested():
                    time.sleep(2)
            except KeyboardInterrupt:
                _log("Interrupted while paused. Exiting.", "yellow")
                _send_discord(embed={"title": "AI Backtest Agent Exited", "description": "Interrupted while paused.", "color": 0xE74C3C})
                sys.exit(0)
            except Exception as e:
                _log_error("MAIN_WHILE_PAUSED", e)
            if _stop_requested():
                _log("Agent stopped while paused. Exiting.", "yellow")
                _send_discord(embed={"title": "AI Backtest Agent Stopped", "description": "Stopped while paused.", "color": 0xE74C3C})
                _request_stop_all_backtests(client)
                sys.exit(0)
            _log("Agent resumed; continuing work.", "green")
            _send_discord(embed={"title": "AI Backtest Agent Resumed", "description": "Agent resumed.", "color": 0x2ECC71})
        _log("Sleeping %ds until next cycle." % AGENT_LOOP_SLEEP, "white")
        remaining = AGENT_LOOP_SLEEP
        while remaining > 0 and not _stop_requested():
            if _pause_requested():
                break  # Exit sleep early if paused
            _stop_event.wait(timeout=min(CONTROL_POLL_INTERVAL, remaining))
            remaining -= CONTROL_POLL_INTERVAL
        if _stop_requested():
            _log("Agent stopped by control. Exiting.", "yellow")
            _send_discord(embed={"title": "AI Backtest Agent Stopped", "description": "Stopped by control (during sleep).", "color": 0xE74C3C})
            _request_stop_all_backtests(client)
            sys.exit(0)


if __name__ == "__main__":
    main()
