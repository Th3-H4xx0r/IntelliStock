"""
IntelliStock REST API - Exposes all CLI commands as JSON endpoints.
Auth: JWT Bearer; signup requires SECRET_AUTH_KEY. Default admin created on server startup.
Run from backend: uvicorn api.main:app --reload --host 0.0.0.0 --port 8000
"""

import os
import sys

_backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)
os.chdir(_backend_dir)

from dotenv import load_dotenv
load_dotenv(os.path.join(_backend_dir, ".env"))
load_dotenv(os.path.join(os.path.dirname(_backend_dir), ".env"))

import asyncio
import threading
import time
from contextlib import contextmanager
from typing import Any, Dict, List, Optional, Union

from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from llm_utils import (
    call_llm_by_provider,
    call_structured_llm_by_provider,
    get_last_plain_llm_call_error,
    get_last_structured_llm_call_metadata,
    get_last_ollama_reasoning,
    normalize_reasoning_effort,
    resolve_api_key_for_provider,
)
from auth_utils import (
    r as _r_auth,  # R16 phase-3: shared RethinkDB driver instance for direct queries
    create_user,
    get_user_by_username,
    get_user_by_id,
    list_users,
    update_user,
    delete_user,
    verify_password,
    verify_secret_auth_key,
    create_access_token,
    decode_access_token,
    user_doc_to_public,
    ensure_users_table,
    ensure_default_admin,
    set_onboarding_completed,
)
from strategies_meta import get_available_strategies
from interactive_utils import (
    get_conn,
    parse_granularity_to_seconds,
    action_clear_instance_state,
    action_status,
    action_tickers,
    action_add_ticker,
    action_remove_ticker,
    action_prices,
    action_history,
    action_instances,
    action_create_instance,
    action_edit_instance,
    action_get_instance,
    action_delete_instance,
    action_add_stock,
    action_remove_stock,
    action_start_instance,
    action_stop_instance,
    action_terminate_price,
    action_terminate_discover,
    action_start_broker,
    action_discover_control_get,
    action_discover_control_set,
    action_strategies,
    action_get_strategy,
    action_create_strategy,
    action_edit_strategy,
    action_delete_strategy,
    action_link_strategy,
    action_unlink_strategy,
    action_link_brokerage_to_instance,
    action_link_data_brokerage_to_instance,
    action_list_backtests,
    action_create_backtest,
    action_delete_backtest,
    action_stop_backtest,
    action_stop_all_backtests,
    action_pause_backtest,
    action_resume_backtest,
    action_get_backtest_status,
    action_summarize_backtest,
    action_backtest_logs,
    action_list_nexus_graph_builds,
    action_get_nexus_graph_build,
    action_nexus_graph_build_logs,
    action_backtest_best_per_strategy,
    action_graph_backtest_data,
    action_get_backtest_playback_data,
    action_agent_control_get,
    action_agent_control_set,
    action_agent_restart,
    action_resume_timer,
    action_agent_increment_backtest_count,
    action_list_ai_backtest_results,
    action_insert_ai_backtest_result,
    action_agent_get_best,
    action_agent_set_best,
    action_agent_get_top5,
    action_agent_update_top5,
    action_digest_control_get,
    action_digest_control_set,
    action_digest_trigger_send_now,
    action_nexus_control_get,
    action_nexus_control_set,
    action_nexus_status,
    action_nexus_cache_entries,
    action_nexus_rebuild,
    action_nexus_delete_edges,
    action_list_trends,
    action_get_trend,
    action_end_trend,
    action_delete_trend,
    action_list_discovered_stocks,
    action_remove_discovered_stock,
    action_nexus_config_get,
    action_nexus_config_set,
    action_list_brokerages,
    action_link_alpaca,
    alpaca_run_diagnostic_suite,
    action_link_robinhood_tokens,
    action_fetch_robinhood_accounts,
    action_delete_brokerage,
    action_update_brokerage,
    action_refresh_robinhood,
    action_get_portfolio_history,
    action_ensure_ai_alpaca_brokerage,
    action_agent_cycle_log_create,
    action_agent_cycle_log_update,
    action_list_agent_runs,
    action_agent_run_force_stop,
    action_list_models,
    action_get_model,
    action_create_model,
    action_edit_model,
    action_delete_model,
    action_model_strategies,
    action_resolve_model_for_runtime,
    action_get_live_state,
    action_submit_live_command,
    action_get_live_command,
    action_live_trading_logs,
    LiveInstanceNotFoundError,
    action_get_notification_preferences,
    action_set_notification_preferences,
    action_register_push_device,
    action_delete_push_device,
)

# OpenAPI / Swagger UI gated to admins-only in production. The schema
# enumerates every endpoint and parameter — useful for development, an
# attack surface map for production.
_API_DOCS_PUBLIC = os.environ.get("API_DOCS_PUBLIC", "").strip().lower() in ("1", "true", "yes")
app = FastAPI(
    title="IntelliStock API",
    description="REST API for IntelliStock backend (CLI-equivalent).",
    docs_url="/docs" if _API_DOCS_PUBLIC else None,
    redoc_url="/redoc" if _API_DOCS_PUBLIC else None,
    openapi_url="/openapi.json" if _API_DOCS_PUBLIC else None,
)

# Allowed CORS origins. Default is "no cross-origin" (single-host install
# behind nginx in the same container fleet doesn't need CORS at all).
# Operators with split frontend/API hosts set CORS_ALLOW_ORIGINS=
# "https://app.example.com,https://staging.example.com".
_cors_origins_raw = os.environ.get("CORS_ALLOW_ORIGINS", "").strip()
_cors_origins = [o.strip() for o in _cors_origins_raw.split(",") if o.strip()]
if _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=False,  # JWT-in-Authorization-header is not a credential
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )

security = HTTPBearer(auto_error=True)


@app.on_event("startup")
def api_startup_ensure_default_admin():
    """Ensure Users table exists and default admin user is created if not already present. Retries for DB readiness."""
    import time
    import logging
    log = logging.getLogger("uvicorn.error")
    for attempt in range(1, 6):
        try:
            conn = get_conn()
            try:
                ensure_users_table(conn)
                ensure_default_admin(conn)
                # Chatbot conversations table — first per-user resource in this codebase.
                try:
                    from chatbot.conversations import ensure_chatbot_tables
                    ensure_chatbot_tables(conn)
                except Exception as ce:
                    log.warning("Chatbot table init failed: %s", ce)
                if attempt > 1:
                    log.info("Default admin setup succeeded on attempt %d", attempt)
                break
            finally:
                conn.close()
        except RuntimeError as cfg_err:
            # Unrecoverable misconfiguration (e.g. missing/weak DEFAULT_ADMIN_PASSWORD).
            # No point retrying — surface a clear message and stop.
            log.error(
                "Default admin setup failed due to misconfiguration: %s. "
                "API will start but login is unavailable until this is fixed.",
                cfg_err,
            )
            break
        except Exception as e:
            log.warning("Default admin setup (attempt %d/5): %s", attempt, e)
            if attempt == 5:
                break
            time.sleep(2)


@app.on_event("startup")
def _startup_init_telemetry():
    """Wire the LLM telemetry sink into the FastAPI lifecycle.

    The sink runs a background flusher that batches LLMUsage rows to
    RethinkDB. Pricing comes from backend/llm_pricing.yaml with optional
    per-model overrides via the Models table (see Task 10). The sink is
    idempotent — calling configure() twice just re-applies the last set
    of parameters; the flusher thread is reused.
    """
    import logging
    log = logging.getLogger("uvicorn.error")
    try:
        import llm_telemetry
        from interactive_utils import (
            r as _r_iu,
            RETHINKDB_HOST as _RDB_HOST,
            RETHINKDB_PORT as _RDB_PORT,
        )

        def _conn_factory():
            # Fresh, short-lived connection per flush attempt. Mirrors the
            # interactive_utils.get_conn pattern with the same 10s socket
            # timeout to avoid half-open hangs.
            return _r_iu.connect(host=_RDB_HOST, port=_RDB_PORT, timeout=10)

        _override_pm_cache = {}  # (provider, model) -> (ts, override|None)

        def _models_override_lookup(model_id, provider=None, model=None):
            """Return any cost-override fields set on a Models row, else None.
            Prefers an exact ``model_id`` match; falls back to a
            (provider, model) match so per-model pricing applies even when the
            call site didn't thread the Models-row id (model_id is None on the
            plain / raw-json structured paths). Used by the cost computer to
            prefer user-set per-model pricing over the YAML defaults."""
            keys = (
                "input_cost_per_1m",
                "output_cost_per_1m",
                "cache_creation_cost_per_1m",
                "cache_read_cost_per_1m",
            )

            def _extract(row):
                if not row:
                    return None
                out = {k: row.get(k) for k in keys if row.get(k) is not None}
                return out or None

            conn = None
            try:
                conn = _conn_factory()
                models = _r_iu.db("IntelliStock").table("Models")
                if model_id:
                    res = _extract(models.get(model_id).run(conn))
                    if res is not None:
                        return res
                if provider and model:
                    ck = (str(provider), str(model))
                    hit = _override_pm_cache.get(ck)
                    if hit and (time.time() - hit[0]) < 60.0:
                        return hit[1]
                    matches = list(models.filter({"provider": provider, "model": model}).run(conn))
                    chosen = next((m for m in matches if _extract(m) is not None), None)
                    res = _extract(chosen)
                    _override_pm_cache[ck] = (time.time(), res)
                    return res
                return None
            except Exception:
                return None
            finally:
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        pass

        pricing_path = os.path.join(_backend_dir, "llm_pricing.yaml")
        llm_telemetry.configure(
            db_conn_factory=_conn_factory,
            enabled=True,
            flush_interval_s=5.0,
            max_buffer=50,
            pricing_yaml_path=pricing_path,
            r_module=_r_iu,
            db_name="IntelliStock",
            models_override_lookup=_models_override_lookup,
        )
        try:
            from llm_telemetry import ensure_llm_usage_tables
            setup_conn = _conn_factory()
            try:
                ensure_llm_usage_tables(conn=setup_conn, r=_r_iu, db_name="IntelliStock")
            finally:
                try:
                    setup_conn.close()
                except Exception:
                    pass
        except Exception as e:
            log.warning("llm telemetry table setup failed: %s", e)
    except Exception as e:
        # Telemetry must never block API startup.
        log.warning("llm telemetry init failed: %s", e)


@app.on_event("shutdown")
def api_shutdown_close_claude_cli_sessions():
    """Gracefully close any persistent ``claude`` subprocesses still alive
    in the chatbot session pool when the API shuts down. codex-cli does
    not use a persistent session manager (each call spawns + closes its
    own ``codex app-server`` subprocess), so nothing to do for codex here.
    """
    try:
        from chatbot.claude_cli_provider import shutdown_session_manager
        shutdown_session_manager()
    except Exception:
        # Provider module may not be importable yet on a partial install;
        # never block API shutdown on this.
        pass


def conn_dependency():
    conn = get_conn()
    try:
        yield conn
    finally:
        conn.close()


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    conn=Depends(conn_dependency),
) -> dict:
    """Validate JWT and return current user dict (id, username, role). Raises 401 if invalid."""
    token = credentials.credentials
    payload = decode_access_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token")
    user = get_user_by_id(conn, user_id)
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return {
        "id": user.get("id"),
        "username": user.get("username"),
        "role": user.get("role", "user"),
    }


def _build_llm_test_provider_config(body: "LlmConfigTestBody") -> dict[str, Any]:
    provider = (body.provider or "").strip().lower()
    config: dict[str, Any] = {}
    if provider == "openai":
        base_url = str(body.openai_base_url or "").strip()
        if base_url:
            config["base_url"] = base_url
    if provider == "azure":
        endpoint = str(body.azure_openai_endpoint or "").strip()
        api_version = str(body.azure_openai_api_version or "").strip()
        if endpoint:
            config["azure_endpoint"] = endpoint
        if api_version:
            config["api_version"] = api_version
    if provider == "nvidia":
        config["base_url"] = "https://integrate.api.nvidia.com/v1"
    if provider == "ollama":
        base = str(body.ollama_base_url or "").strip()
        if base:
            config["ollama_base_url"] = base
        keep_alive = str(body.ollama_keep_alive or "").strip()
        if keep_alive:
            config["ollama_keep_alive"] = keep_alive
        think = str(body.ollama_think or "").strip()
        if think:
            config["ollama_think"] = think
    if provider == "bedrock":
        region = str(body.bedrock_region or "").strip()
        if region:
            config["bedrock_region"] = region
        reasoning = str(body.bedrock_reasoning or "").strip().lower()
        if reasoning:
            config["bedrock_reasoning"] = reasoning
    reasoning_effort = normalize_reasoning_effort(body.reasoning_effort)
    # claude-cli accepts ``--effort`` (low/medium/high/xhigh/max) too —
    # its session manager folds it into the spawn argv. Drop the value
    # only for providers where the LLM dispatcher doesn't actually
    # forward it (gemini/deepseek/anthropic-direct as of today).
    if provider in {"openai", "azure", "nvidia", "claude-cli", "codex-cli"} and reasoning_effort:
        config["reasoning_effort"] = reasoning_effort
    return config


# --- Request/response models (optional; we also accept dicts) ---


class AddTickerBody(BaseModel):
    symbols: List[str] = Field(..., min_length=1)


class CreateInstanceBody(BaseModel):
    id: str = Field(..., min_length=1)
    name: Optional[str] = None
    strategy_id: Optional[int] = None
    key: Optional[str] = None
    secret: Optional[str] = None
    granularity: Optional[str] = "60"
    run_command: Optional[bool] = False
    created_by: Optional[str] = "user"
    brokerage_id: Optional[str] = None
    max_usage: Optional[float] = None


class EditInstanceBody(BaseModel):
    name: Optional[str] = None
    granularity: Optional[str] = None
    max_usage: Optional[float] = None
    brokerage_id: Optional[str] = None


class DeleteInstanceBody(BaseModel):
    force: bool = False


class AddStockBody(BaseModel):
    symbol: str = Field(..., min_length=1)


class CreateStrategyBody(BaseModel):
    name: str = Field(..., min_length=1)
    strategies: List[dict] = Field(..., min_length=1)


class EditStrategyBody(BaseModel):
    name: Optional[str] = None
    strategies: Optional[List[dict]] = None


class DeleteStrategyBody(BaseModel):
    force: bool = False


class LinkStrategyBody(BaseModel):
    strategy_id: int


class LinkBrokerageToInstanceBody(BaseModel):
    brokerage_id: Optional[str] = None


class LlmConfigTestBody(BaseModel):
    provider: str = Field(..., min_length=1)
    model: str = Field(..., min_length=1)
    api_key: Optional[str] = None
    openai_base_url: Optional[str] = None
    nvidia_base_url: Optional[str] = None
    azure_openai_endpoint: Optional[str] = None
    azure_openai_api_version: Optional[str] = None
    reasoning_effort: Optional[str] = None
    # Ollama provider config — base_url defaults to http://localhost:11434
    # when omitted; keep_alive controls how long Ollama keeps the model
    # loaded between calls (e.g. "5m", "60m", "-1" for forever).
    ollama_base_url: Optional[str] = Field(default=None, max_length=512)
    ollama_keep_alive: Optional[str] = Field(default=None, max_length=16)
    # ollama_think: "" (default), "true"/"false" for binary thinking, or
    # "low"/"medium"/"high" for gpt-oss-style effort. Normalised in
    # llm_utils._normalize_ollama_think before reaching Ollama.
    ollama_think: Optional[str] = Field(default=None, max_length=16)
    # Bedrock provider config — region is required (AWS is regional);
    # bedrock_reasoning is "off"/"low"/"medium"/"high" (Claude 3.7+ only).
    bedrock_region: Optional[str] = Field(default=None, max_length=32)
    bedrock_reasoning: Optional[str] = Field(default=None, max_length=16)


class LlmConfigTestOutput(BaseModel):
    # ``ok`` is the real connectivity signal. provider/model are optional: the
    # test prompt asks the model to echo them, but terser models (e.g. Bedrock
    # GPT-OSS) return only {"ok": true}. Requiring the echo made the probe fail
    # for working models, so we don't — Stage 2's smoke generation still
    # exercises the real call path.
    ok: bool
    provider: Optional[str] = None
    model: Optional[str] = None


class CreateModelBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    provider: str = Field(..., min_length=1, max_length=32)
    model: str = Field(..., min_length=1, max_length=128)
    api_key: Optional[str] = Field(default=None, max_length=4096)
    openai_base_url: Optional[str] = Field(default=None, max_length=512)
    nvidia_base_url: Optional[str] = Field(default=None, max_length=512)
    azure_openai_endpoint: Optional[str] = Field(default=None, max_length=512)
    azure_openai_api_version: Optional[str] = Field(default=None, max_length=32)
    reasoning_effort: Optional[str] = Field(default=None, max_length=16)
    # claude-cli only — locally-installed `claude` binary path + free-text
    # extra args (whitelisted server-side, rejects --tools/--mcp-config/etc).
    # The caps prevent argv-length DoS and pathological allowed-flag
    # repetition; the allowlist itself caps token count too.
    cli_path: Optional[str] = Field(default=None, max_length=256)
    extra_args: Optional[str] = Field(default=None, max_length=1024)
    # Ollama provider config — base_url defaults to http://localhost:11434
    # when omitted (also accepts https://ollama.com/v1 for cloud).
    # keep_alive: how long Ollama keeps the model resident between calls
    # (e.g. "5m", "60m", "-1" for forever).
    ollama_base_url: Optional[str] = Field(default=None, max_length=512)
    ollama_keep_alive: Optional[str] = Field(default=None, max_length=16)
    ollama_think: Optional[str] = Field(default=None, max_length=16)
    # Bedrock provider config — region required; reasoning is the bedrock
    # equivalent of ollama_think ("off"/"low"/"medium"/"high", Claude 3.7+).
    bedrock_region: Optional[str] = Field(default=None, max_length=32)
    bedrock_reasoning: Optional[str] = Field(default=None, max_length=16)
    # Optional cache-grouping tag: rows sharing this value share LLM cache (same
    # underlying model across providers/names). See canonical_model_cache_key.
    model_cache_family: Optional[str] = Field(default=None, max_length=64)
    # Optional per-model pricing override ($/1M tokens). When set, these
    # win over backend/llm_pricing.yaml at telemetry-cost time. Leave
    # None to fall back to the YAML defaults.
    input_cost_per_1m: Optional[float] = Field(default=None, ge=0)
    output_cost_per_1m: Optional[float] = Field(default=None, ge=0)
    cache_creation_cost_per_1m: Optional[float] = Field(default=None, ge=0)
    cache_read_cost_per_1m: Optional[float] = Field(default=None, ge=0)


class EditModelBody(BaseModel):
    name: Optional[str] = Field(default=None, max_length=128)
    provider: Optional[str] = Field(default=None, max_length=32)
    model: Optional[str] = Field(default=None, max_length=128)
    api_key: Optional[str] = Field(default=None, max_length=4096)
    openai_base_url: Optional[str] = Field(default=None, max_length=512)
    nvidia_base_url: Optional[str] = Field(default=None, max_length=512)
    azure_openai_endpoint: Optional[str] = Field(default=None, max_length=512)
    azure_openai_api_version: Optional[str] = Field(default=None, max_length=32)
    reasoning_effort: Optional[str] = Field(default=None, max_length=16)
    cli_path: Optional[str] = Field(default=None, max_length=256)
    extra_args: Optional[str] = Field(default=None, max_length=1024)
    ollama_base_url: Optional[str] = Field(default=None, max_length=512)
    ollama_keep_alive: Optional[str] = Field(default=None, max_length=16)
    ollama_think: Optional[str] = Field(default=None, max_length=16)
    bedrock_region: Optional[str] = Field(default=None, max_length=32)
    bedrock_reasoning: Optional[str] = Field(default=None, max_length=16)
    model_cache_family: Optional[str] = Field(default=None, max_length=64)
    input_cost_per_1m: Optional[float] = Field(default=None, ge=0)
    output_cost_per_1m: Optional[float] = Field(default=None, ge=0)
    cache_creation_cost_per_1m: Optional[float] = Field(default=None, ge=0)
    cache_read_cost_per_1m: Optional[float] = Field(default=None, ge=0)


class BenzingaTestBody(BaseModel):
    api_key: Optional[str] = None
    sources: Optional[List[str]] = None  # if None, test all 10


class CreateBacktestBody(BaseModel):
    instance_id: Optional[str] = None
    stocks: List[str] = Field(default_factory=list)  # V7.3: Allow empty for pure discovery
    start_date: str = Field(..., min_length=1)
    end_date: str = Field(..., min_length=1)
    granularity: Optional[str] = "60"
    key: Optional[str] = None
    secret: Optional[str] = None
    initial_cash: Optional[float] = 100000.0


class AgentControlBody(BaseModel):
    running: Optional[bool] = None
    paused: Optional[bool] = None
    special_request: Optional[str] = None


class ResumeTimerBody(BaseModel):
    days: Optional[int] = 0
    hours: Optional[int] = 0
    minutes: Optional[int] = 0
    seconds: Optional[int] = 0


class AgentRestartBody(BaseModel):
    special_request: Optional[str] = None


class AgentResultBody(BaseModel):
    strategy_snapshot: dict
    backtest_id: Optional[int] = None
    instance_id: Optional[str] = None
    strategy_id: Optional[int] = None
    overall_profit: Optional[float] = None
    pnl_percent: Optional[float] = None
    pnl_per_stock: Optional[dict] = None
    pnl_percent_per_stock: Optional[dict] = None
    stock_price_change: Optional[dict] = None
    start_date: str = ""
    end_date: str = ""
    stocks_used: List[str] = Field(default_factory=list)
    status: str = "passed"
    agent_notes: Optional[str] = None


class AgentSetBestBody(BaseModel):
    strategy_snapshot: dict
    overall_profit: Optional[float] = None
    pnl_percent: Optional[float] = None
    results_summary: Optional[dict] = None


class AgentUpdateTop5Body(BaseModel):
    strategy_snapshot: dict
    overall_profit: Optional[float] = None
    pnl_percent: Optional[float] = None
    strategy_id: Optional[int] = None
    backtest_id: Optional[int] = None
    results_summary: Optional[dict] = None


class AgentCycleLogCreateBody(BaseModel):
    cycle_id: str
    name: str


class AgentCycleLogUpdateBody(BaseModel):
    status: Optional[str] = None
    stages: Optional[List[Any]] = None
    final_result: Optional[str] = None


class DigestControlBody(BaseModel):
    running: Optional[bool] = None
    send_now: Optional[bool] = None


class DiscoverControlBody(BaseModel):
    running: Optional[bool] = None


class _LegacyNexusControlBody(BaseModel):
    running: Optional[bool] = None
    start_phase: Optional[Union[int, str]] = None  # execution-order 1-14 or selector like 2b / 6b
    end_phase: Optional[Union[int, str]] = None    # execution-order 1-14 or selector like 2b / 6b


class NexusControlBody(BaseModel):
    running: Optional[bool] = None
    start_phase: Optional[Union[int, str]] = None
    end_phase: Optional[Union[int, str]] = None
    selected_phases: Optional[List[Union[int, str]]] = None
    force_bootstrap_rebuild: Optional[bool] = None
    auto_update_enabled: Optional[bool] = None
    auto_update_interval_hours: Optional[int] = None
    auto_update_start_phase: Optional[Union[int, str]] = None
    auto_update_end_phase: Optional[Union[int, str]] = None
    phase7_history_quarters: Optional[int] = None
    historical_mode_enabled: Optional[bool] = None
    historical_start_date: Optional[str] = None


class NexusRebuildBody(BaseModel):
    confirm: bool = False
    destructive: bool = False
    force_bootstrap_rebuild: bool = False
    delete_cache_paths: List[str] = Field(default_factory=list)


class NexusDeleteBody(BaseModel):
    selected_phases: List[Union[int, str]] = Field(default_factory=list)


# --- Auth body models ---


class SignupBody(BaseModel):
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=6)
    secret_auth_key: str = Field(..., min_length=1)


class LoginBody(BaseModel):
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)


class CreateUserBody(BaseModel):
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=6)
    role: str = Field(default="user", pattern="^(admin|user)$")
    email: Optional[str] = None


class UpdateUserBody(BaseModel):
    password: Optional[str] = Field(None, min_length=6)
    role: Optional[str] = Field(None, pattern="^(admin|user)$")
    email: Optional[str] = None


class EndTrendBody(BaseModel):
    reason: Optional[str] = "ended via API"


class NexusConfigUpdateBody(BaseModel):
    trend_tracking_enabled: Optional[bool] = None
    stock_finder_enabled: Optional[bool] = None
    sell_enforcement_enabled: Optional[bool] = None
    max_discovered_stocks: Optional[int] = None
    trend_min_strength_to_buy: Optional[float] = None
    trend_max_age_days: Optional[int] = None
    nexus_portfolio_pct: Optional[float] = None
    google_news_enabled: Optional[bool] = None


class FetchRobinhoodAccountsBody(BaseModel):
    access_token: str
    refresh_token: Optional[str] = None
    device_token: Optional[str] = None


class LinkBrokerageBody(BaseModel):
    brokerage_type: str = Field(..., pattern="^(alpaca|robinhood)$")
    account_name: str = Field(..., min_length=1)
    # Alpaca
    key: Optional[str] = None
    secret: Optional[str] = None
    paper: Optional[bool] = True
    # 2026-04-23: alpaca bars-feed choice (iex=free, sip=paid). Backend
    # validates the live account has the subscription before accepting.
    alpaca_data_feed: Optional[str] = Field(default="iex", pattern="^(iex|sip)$")
    # Robinhood (token paste)
    access_token: Optional[str] = None
    refresh_token: Optional[str] = None
    device_token: Optional[str] = None
    expires_in: Optional[int] = None
    obtained_at_epoch: Optional[int] = None
    # Robinhood account selection (from preview step)
    account_number: Optional[str] = None


class UpdateBrokerageBody(BaseModel):
    account_name: Optional[str] = None
    # Alpaca
    key: Optional[str] = None
    secret: Optional[str] = None
    paper: Optional[bool] = None
    alpaca_data_feed: Optional[str] = Field(default=None, pattern="^(iex|sip)$")
    # Robinhood
    access_token: Optional[str] = None
    refresh_token: Optional[str] = None
    device_token: Optional[str] = None
    account_number: Optional[str] = None


def _run(f, *args, **kwargs) -> Any:
    try:
        return f(*args, **kwargs)
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except LookupError as e:
        # NexusBuildNotFoundError (and any other LookupError-derived "missing"
        # signal from an action) — distinct 404 so the UI can show "nothing yet"
        # instead of conflating with real backend failures (5xx).
        raise HTTPException(status_code=404, detail=str(e))


# --- Health check (public, no-auth) ---


@app.get("/health", response_class=JSONResponse)
def api_health():
    """Liveness probe for load balancers / Dockploy / monitoring.

    Returns 200 with RethinkDB ping result and version. No authentication
    required so external probes can hit it. Uses a 2-second timeout so a
    hung DB connection doesn't block the health response.

    Live-readiness OPS #1 (2026-04-29): added so Dockploy and external
    probes can verify the API container is responding (not just running).
    """
    import os as _os
    import socket as _sock
    _ok = True
    _db_status = "unknown"
    try:
        from rethinkdb import RethinkDB as _R
        _r = _R()
        _host = _os.environ.get("RETHINKDB_HOST", "localhost")
        _port = int(_os.environ.get("RETHINKDB_PORT", "28015"))
        # Q10 fix: rethinkdb-python `timeout` kwarg support varies by version;
        # try with timeout first, fall back to no-timeout if TypeError.
        try:
            _conn = _r.connect(host=_host, port=_port, timeout=2)
        except TypeError:
            _conn = _r.connect(host=_host, port=_port)
        try:
            list(_r.db_list().run(_conn))
            _db_status = "ok"
        finally:
            try:
                _conn.close()
            except Exception:
                pass
    except Exception as _e:
        _db_status = f"error: {type(_e).__name__}"
        _ok = False
    payload = {
        "status": "ok" if _ok else "degraded",
        "rethinkdb": _db_status,
        "host": _sock.gethostname(),
    }
    if not _ok:
        return JSONResponse(status_code=503, content=payload)
    return payload


# --- Help / commands list (public) ---


@app.get("/help", response_class=JSONResponse)
def api_help():
    """List all available API commands (CLI-equivalent). Public."""
    return {
        "commands": [
            {"path": "POST /auth/signup", "body": "username, password, secret_auth_key", "description": "Register (requires SECRET_AUTH_KEY)"},
            {"path": "POST /auth/login", "body": "username, password", "description": "Login, returns access_token"},
            {"path": "GET /auth/me", "description": "Current user (auth required)"},
            {"path": "GET /onboarding/state", "description": "Get current user's onboarding state + resource counts"},
            {"path": "POST /onboarding/complete", "description": "Mark onboarding as complete for current user"},
            {"path": "POST /onboarding/reset", "description": "Reset onboarding so it shows again"},
            {"path": "GET /chatbot/conversations", "description": "List current user's chatbot conversations"},
            {"path": "POST /chatbot/conversations", "body": "ChatbotCreateBody", "description": "Create a new chatbot conversation"},
            {"path": "GET /chatbot/conversations/{id}", "description": "Get a conversation with full message history"},
            {"path": "PATCH /chatbot/conversations/{id}", "body": "ChatbotUpdateBody", "description": "Update a conversation's title / model / settings"},
            {"path": "DELETE /chatbot/conversations/{id}", "description": "Delete a conversation"},
            {"path": "POST /chatbot/conversations/{id}/clear", "description": "Wipe a conversation's messages"},
            {"path": "POST /chatbot/conversations/{id}/turn", "body": "ChatbotTurnBody", "description": "Send a message and run the LLM↔tool loop"},
            {"path": "POST /chatbot/conversations/{id}/confirm-tool", "body": "ChatbotConfirmBody", "description": "Approve or decline a pending tool call"},
            {"path": "GET /chatbot/tools", "description": "Get the curated tool catalog the chatbot can use"},
            {"path": "GET /auth/users", "description": "List users"},
            {"path": "POST /auth/users", "body": "CreateUserBody", "description": "Create user"},
            {"path": "GET /auth/users/{id}", "description": "Get user"},
            {"path": "PUT /auth/users/{id}", "body": "UpdateUserBody", "description": "Update user"},
            {"path": "DELETE /auth/users/{id}", "description": "Delete user"},
            {"path": "GET /status", "description": "Config, service flags, and engines status table (all services/engines with running state and details)"},
            {"path": "GET /tickers", "description": "List tickers (LivePricesStocks)"},
            {"path": "POST /tickers", "body": {"symbols": ["AAPL", "MSFT"]}, "description": "Add ticker(s)"},
            {"path": "DELETE /tickers/{symbol}", "description": "Remove a ticker"},
            {"path": "GET /prices", "description": "Current prices (LivePrices)"},
            {"path": "GET /history", "params": "ticker, limit", "description": "Price history"},
            {"path": "GET /instances", "description": "List instances"},
            {"path": "POST /instances", "body": "CreateInstanceBody", "description": "Create instance"},
            {"path": "PATCH /instances/{id}", "body": "EditInstanceBody", "description": "Edit instance info"},
            {"path": "DELETE /instances/{id}", "body": "force?", "description": "Delete instance"},
            {"path": "POST /instances/{id}/stocks", "body": {"symbol": "AAPL"}, "description": "Add stock to instance"},
            {"path": "DELETE /instances/{id}/stocks/{symbol}", "description": "Remove stock from instance"},
            {"path": "POST /instances/{id}/start", "description": "Start instance"},
            {"path": "POST /instances/{id}/stop", "description": "Stop instance"},
            {"path": "POST /config/terminate-price", "description": "Terminate price service"},
            {"path": "POST /config/terminate-discover", "description": "Terminate discover service"},
            {"path": "POST /config/start-broker", "description": "Start broker"},
            {"path": "GET /strategies/available", "description": "List available strategy types with schema and description"},
            {"path": "GET /strategies", "description": "List strategies"},
            {"path": "GET /strategies/{id}", "description": "Get one strategy"},
            {"path": "POST /strategies", "body": "CreateStrategyBody", "description": "Create strategy"},
            {"path": "PUT /strategies/{id}", "body": "EditStrategyBody", "description": "Edit strategy"},
            {"path": "DELETE /strategies/{id}", "body": "force?", "description": "Delete strategy"},
            {"path": "POST /instances/{instance_id}/link-strategy", "body": {"strategy_id": 5}, "description": "Link strategy to instance"},
            {"path": "GET /backtests", "description": "List backtests"},
            {"path": "POST /backtests", "body": "CreateBacktestBody", "description": "Create backtest"},
            {"path": "DELETE /backtests/{id}", "description": "Delete backtest"},
            {"path": "POST /backtests/{id}/stop", "description": "Stop running backtest"},
            {"path": "POST /backtests/stop-all", "description": "Stop all running backtests and clear the queue"},
            {"path": "POST /backtests/{id}/pause", "description": "Pause running backtest"},
            {"path": "POST /backtests/{id}/resume", "description": "Resume paused backtest"},
            {"path": "GET /backtests/{id}/status", "description": "Backtest status and progress (from BacktestResults)"},
            {"path": "GET /backtests/{id}/summary", "description": "Backtest summary"},
            {"path": "GET /backtests/{id}/logs", "description": "Backtest logs"},
            {"path": "GET /backtests/{id}/graph-data", "description": "Backtest data for plotting"},
            {"path": "GET /agent/control", "description": "Get AI backtesting agent status (running, count_today)"},
            {"path": "POST /agent/control", "body": "AgentControlBody", "description": "Start or stop AI backtesting agent"},
            {"path": "GET /agent/results", "description": "List AI backtesting results (profitable strategies)"},
            {"path": "GET /agent/best", "description": "Get best strategy (tag Best) with details and settings"},
            {"path": "POST /agent/best", "body": "AgentSetBestBody", "description": "Set best strategy if outperforms current (persists in Strategies with tag Best)"},
            {"path": "GET /digest/control", "description": "Get daily digest engine status (running, send_now, last_sent_at)"},
            {"path": "POST /digest/control", "body": "DigestControlBody", "description": "Start or stop digest engine (running), or set send_now to trigger immediate send"},
            {"path": "POST /digest/send-now", "description": "Trigger digest engine to send a brief immediately (morning style)"},
            {"path": "GET /discover/control", "description": "Get discover engine status (running, terminate)"},
            {"path": "POST /discover/control", "body": "DiscoverControlBody", "description": "Start or stop the discover engine"},
            {"path": "GET /nexus/control", "description": "Get Graph Nexus service control (running)"},
            {"path": "POST /nexus/control", "body": "NexusControlBody", "description": "Start/stop Graph Nexus, request phase reruns, and configure auto-update scheduling."},
            {"path": "GET /nexus/status", "description": "Comprehensive Graph Nexus status: control, graph build progress, SEC scraper progress, graph_built"},
            {"path": "GET /nexus/cache", "description": "List top-level cache entries from the mounted Nexus cache root or the running Nexus container so the rebuild UI can offer selective cache deletion."},
            {"path": "POST /nexus/rebuild", "body": "NexusRebuildBody", "description": "Queue a Nexus rebuild from phase 1. Supports non-destructive in-place reruns or destructive Neo4j/Rethink resets, plus optional cache deletion from the mounted cache root or the running Nexus container."},
        ]
    }


# --- Auth endpoints (signup/login public; rest protected) ---


@app.post("/auth/signup", response_class=JSONResponse)
def api_signup(body: SignupBody, conn=Depends(conn_dependency)):
    """Register a new user. Requires SECRET_AUTH_KEY to match env."""
    if not verify_secret_auth_key(body.secret_auth_key):
        raise HTTPException(status_code=403, detail="Invalid signup key")
    try:
        user = create_user(conn, body.username, body.password, role="user")
        token = create_access_token(user["id"], user["username"], user["role"])
        return {"access_token": token, "token_type": "bearer", "user": user}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/auth/login", response_class=JSONResponse)
def api_login(body: LoginBody, conn=Depends(conn_dependency)):
    """Login with username/password. Returns JWT access_token. Creates default admin on first login if missing."""
    username = (body.username or "").strip().lower()
    user = get_user_by_username(conn, username)
    if not user or not verify_password(body.password, user.get("password_hash", "")):
        # If admin doesn't exist yet (e.g. startup ran before DB was ready), ensure default admin and retry
        default_user = (os.environ.get("DEFAULT_ADMIN_USERNAME") or "").strip().lower()
        if username == default_user and default_user:
            ensure_users_table(conn)
            ensure_default_admin(conn)
            user = get_user_by_username(conn, username)
            if user and verify_password(body.password, user.get("password_hash", "")):
                user_public = user_doc_to_public(user)
                token = create_access_token(user["id"], user["username"], user.get("role", "user"))
                return {"access_token": token, "token_type": "bearer", "user": user_public}
        raise HTTPException(status_code=401, detail="Invalid username or password")
    user_public = user_doc_to_public(user)
    token = create_access_token(user["id"], user["username"], user.get("role", "user"))
    return {"access_token": token, "token_type": "bearer", "user": user_public}


@app.get("/auth/me", response_class=JSONResponse)
def api_me(current_user: dict = Depends(get_current_user), conn=Depends(conn_dependency)):
    """Return current authenticated user."""
    user = get_user_by_id(conn, current_user["id"])
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user_doc_to_public(user)


@app.get("/auth/users", response_class=JSONResponse)
def api_list_auth_users(
    conn=Depends(conn_dependency),
    current_user: dict = Depends(get_current_user),
):
    """List all users. Any authenticated user."""
    users = list_users(conn)
    return {"users": users}


@app.post("/auth/users", response_class=JSONResponse)
def api_create_auth_user(
    body: CreateUserBody,
    conn=Depends(conn_dependency),
    current_user: dict = Depends(get_current_user),
):
    """Create a new user. Any authenticated user. No secret_auth_key required."""
    try:
        user = create_user(conn, body.username, body.password, role=body.role, email=body.email)
        return user
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/auth/users/{user_id}", response_class=JSONResponse)
def api_get_auth_user(
    user_id: str,
    conn=Depends(conn_dependency),
    current_user: dict = Depends(get_current_user),
):
    """Get user by id. Any authenticated user."""
    user = get_user_by_id(conn, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user_doc_to_public(user)


@app.put("/auth/users/{user_id}", response_class=JSONResponse)
def api_update_auth_user(
    user_id: str,
    body: UpdateUserBody,
    conn=Depends(conn_dependency),
    current_user: dict = Depends(get_current_user),
):
    """Update user. Any authenticated user can change password/email/role."""
    try:
        user = update_user(conn, user_id, password=body.password, role=body.role, email=body.email)
        return user
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/auth/users/{user_id}", response_class=JSONResponse)
def api_delete_auth_user(
    user_id: str,
    conn=Depends(conn_dependency),
    current_user: dict = Depends(get_current_user),
):
    """Delete user. Any authenticated user."""
    if current_user.get("id") == user_id:
        raise HTTPException(status_code=400, detail="Cannot delete yourself")
    try:
        delete_user(conn, user_id)
        return {"deleted": True, "id": user_id}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# --- Onboarding (per-user welcome flow flag) ---


@app.get("/onboarding/state", response_class=JSONResponse)
def api_onboarding_state(
    conn=Depends(conn_dependency),
    current_user: dict = Depends(get_current_user),
):
    """Return the current user's onboarding state.

    Also reports whether any models / brokerages / instances exist in the
    database — the onboarding UI uses these counts to surface existing
    resources so an already-configured user can re-onboard or top up.
    """
    user = get_user_by_id(conn, current_user["id"])
    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    def _safe_count(table_name: str) -> int:
        try:
            return int(_r_auth.db(os.environ.get("INTELLISTOCK_DB_NAME", "IntelliStock")).table(table_name).count().run(conn))
        except Exception as e:
            # Missing-table is expected on a brand-new install; logging keeps
            # connection-drop failures observable instead of silently reporting
            # "0 resources" to the UI (which would force re-onboarding).
            import logging
            logging.getLogger(__name__).debug("onboarding _safe_count(%s) failed: %s", table_name, e)
            return 0

    counts = {
        "models": _safe_count("Models"),
        "brokerages": _safe_count("BrokerageAccounts"),
        "instances": _safe_count("Instances"),
    }
    public = user_doc_to_public(user) or {}
    return {
        "has_completed_onboarding": bool(public.get("has_completed_onboarding", False)),
        "counts": counts,
        "user": public,
    }


@app.post("/onboarding/complete", response_class=JSONResponse)
def api_onboarding_complete(
    conn=Depends(conn_dependency),
    current_user: dict = Depends(get_current_user),
):
    """Mark the current user's onboarding flow as complete."""
    try:
        user = set_onboarding_completed(conn, current_user["id"], True)
        return {"ok": True, "user": user}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/onboarding/reset", response_class=JSONResponse)
def api_onboarding_reset(
    conn=Depends(conn_dependency),
    current_user: dict = Depends(get_current_user),
):
    """Reset the current user's onboarding flow so it shows again on next login."""
    try:
        user = set_onboarding_completed(conn, current_user["id"], False)
        return {"ok": True, "user": user}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# --- Chatbot (integrated assistant: tool-calling LLM over the API) ---


class ChatbotCreateBody(BaseModel):
    title: Optional[str] = None
    model_id: Optional[str] = None


class ChatbotUpdateBody(BaseModel):
    title: Optional[str] = None
    model_id: Optional[str] = None
    auto_confirm_safe_tools: Optional[bool] = None


class ChatbotTurnBody(BaseModel):
    content: str = Field(..., min_length=1, max_length=8000)


class ChatbotConfirmBody(BaseModel):
    message_id: str = Field(..., min_length=1)
    approved: bool


def _chatbot_model_name(conn, model_id: Optional[str]) -> Optional[str]:
    if not model_id:
        return None
    try:
        from chatbot.orchestration import _fetch_model_doc as _fetch
        doc = _fetch(conn, model_id)
        return (doc or {}).get("name")
    except Exception:
        return None


def _ensure_chatbot_ready(conn) -> None:
    """Lazily create the ChatbotConversations table on first use. The
    startup-hook already attempts this, but if RethinkDB was unavailable at
    boot the table is missing — recreate here so the user doesn't have to
    restart the API container."""
    try:
        from chatbot.conversations import ensure_chatbot_tables
        ensure_chatbot_tables(conn)
    except Exception as e:
        import logging
        logging.getLogger("uvicorn.error").warning(
            "ensure_chatbot_tables (lazy) failed: %s", e,
        )


@app.get("/chatbot/conversations", response_class=JSONResponse)
def api_chatbot_list(
    conn=Depends(conn_dependency),
    current_user: dict = Depends(get_current_user),
):
    """List the current user's chatbot conversations (most recently updated first)."""
    _ensure_chatbot_ready(conn)
    from chatbot import conversations as conv_store
    return {"conversations": conv_store.list_conversations(conn, current_user["id"])}


@app.post("/chatbot/conversations", response_class=JSONResponse)
def api_chatbot_create(
    body: ChatbotCreateBody,
    conn=Depends(conn_dependency),
    current_user: dict = Depends(get_current_user),
):
    """Create a new conversation. model_id is optional but required before sending the first message."""
    _ensure_chatbot_ready(conn)
    from chatbot import conversations as conv_store
    model_name = _chatbot_model_name(conn, body.model_id)
    convo = conv_store.create_conversation(
        conn,
        current_user["id"],
        model_id=body.model_id,
        model_name=model_name,
        title=body.title,
    )
    return convo


@app.get("/chatbot/conversations/{conv_id}", response_class=JSONResponse)
def api_chatbot_get(
    conv_id: str,
    conn=Depends(conn_dependency),
    current_user: dict = Depends(get_current_user),
):
    from chatbot import conversations as conv_store
    convo = conv_store.get_conversation(conn, current_user["id"], conv_id)
    if not convo:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return convo


@app.patch("/chatbot/conversations/{conv_id}", response_class=JSONResponse)
def api_chatbot_update(
    conv_id: str,
    body: ChatbotUpdateBody,
    conn=Depends(conn_dependency),
    current_user: dict = Depends(get_current_user),
):
    from chatbot import conversations as conv_store
    settings_update = None
    if body.auto_confirm_safe_tools is not None:
        settings_update = {"auto_confirm_safe_tools": bool(body.auto_confirm_safe_tools)}
    model_name = _chatbot_model_name(conn, body.model_id) if body.model_id else None
    convo = conv_store.update_conversation(
        conn,
        current_user["id"],
        conv_id,
        title=body.title,
        model_id=body.model_id,
        model_name=model_name,
        settings=settings_update,
    )
    if not convo:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return convo


@app.delete("/chatbot/conversations/{conv_id}", response_class=JSONResponse)
def api_chatbot_delete(
    conv_id: str,
    conn=Depends(conn_dependency),
    current_user: dict = Depends(get_current_user),
):
    from chatbot import conversations as conv_store
    if not conv_store.delete_conversation(conn, current_user["id"], conv_id):
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"deleted": True, "id": conv_id}


@app.post("/chatbot/conversations/{conv_id}/clear", response_class=JSONResponse)
def api_chatbot_clear(
    conv_id: str,
    conn=Depends(conn_dependency),
    current_user: dict = Depends(get_current_user),
):
    from chatbot import conversations as conv_store
    convo = conv_store.clear_messages(conn, current_user["id"], conv_id)
    if not convo:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return convo


@app.post("/chatbot/conversations/{conv_id}/turn", response_class=JSONResponse)
def api_chatbot_turn(
    conv_id: str,
    body: ChatbotTurnBody,
    conn=Depends(conn_dependency),
    current_user: dict = Depends(get_current_user),
):
    """Send a user message and run the LLM↔tool loop. Returns the new
    messages appended in this turn (user echo + assistant rounds + tool
    results). May end with a pending_confirmation message awaiting
    /confirm-tool when the model wanted to call a non-safe tool."""
    from chatbot.orchestration import run_turn
    try:
        appended = run_turn(conn, current_user, conv_id, body.content)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"messages": appended}


@app.post("/chatbot/conversations/{conv_id}/confirm-tool", response_class=JSONResponse)
def api_chatbot_confirm_tool(
    conv_id: str,
    body: ChatbotConfirmBody,
    conn=Depends(conn_dependency),
    current_user: dict = Depends(get_current_user),
):
    """Approve or decline a pending tool call and resume the conversation."""
    from chatbot.orchestration import confirm_pending_tool
    try:
        appended = confirm_pending_tool(
            conn, current_user, conv_id, body.message_id, bool(body.approved),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"messages": appended}


@app.get("/chatbot/tools", response_class=JSONResponse)
def api_chatbot_tools(current_user: dict = Depends(get_current_user)):
    """Return the curated tool catalog for display in the chatbot settings."""
    from chatbot.tools import tool_catalog
    return {"tools": tool_catalog()}


# --- Claude Code CLI MCP bridge ---


class _McpToolsListBody(BaseModel):
    conversation_id: str = Field(..., min_length=1)
    user_id: Optional[str] = ""


class _McpToolCallBody(BaseModel):
    conversation_id: str = Field(..., min_length=1)
    user_id: Optional[str] = ""
    tool_name: str = Field(..., min_length=1)
    arguments: Dict[str, Any] = Field(default_factory=dict)
    confirm_wait_timeout_sec: Optional[int] = 0


class _McpConfirmBody(BaseModel):
    message_id: str = Field(..., min_length=1)
    approved: bool


def _resolve_mcp_session(request, conv_id: str):
    """Validate the inbound MCP token and return the matching live
    session. Raises 401 if unrecognised, 403 if the token doesn't own
    the supplied ``conv_id``. Lets the IntelliStock backend trust that
    only the spawned MCP server (which has the token in its env) can
    drive tool execution on this conversation."""
    token = request.headers.get("X-IntelliStock-MCP-Token") or ""
    if not token:
        raise HTTPException(status_code=401, detail="Missing X-IntelliStock-MCP-Token header.")
    from chatbot.claude_cli_provider import get_session_manager
    mgr = get_session_manager()
    sess = mgr.lookup_by_token(token)
    if sess is None:
        raise HTTPException(status_code=401, detail="MCP token not recognised.")
    if sess.conversation_id != conv_id:
        raise HTTPException(status_code=403, detail="MCP token does not own this conversation.")
    return sess


@app.post("/chatbot/internal/mcp-tools-list", response_class=JSONResponse)
def api_mcp_tools_list(
    body: _McpToolsListBody,
    request: Request,
    conn=Depends(conn_dependency),
):
    """List the tool catalog for an MCP-driven CC session. Auth'd by the
    per-session token the IntelliStock backend wrote into the spawned
    CC's ``--mcp-config`` env."""
    _resolve_mcp_session(request, body.conversation_id)
    from chatbot.tools import openai_tool_definitions
    return {"tools": openai_tool_definitions()}


@app.post("/chatbot/internal/mcp-tool-call", response_class=JSONResponse)
def api_mcp_tool_call(
    body: _McpToolCallBody,
    request: Request,
    conn=Depends(conn_dependency),
):
    """Execute one tool on behalf of an MCP-driven CC session.

    Safe / render-only tools run synchronously and the result returns
    immediately. Non-safe (write / destructive) tools instead append a
    ``pending_confirmation`` message to the conversation and return a
    ``pending`` envelope to CC so the model can acknowledge the queued
    action; the user then approves or declines via
    ``/chatbot/conversations/{id}/mcp-confirm``.
    """
    sess = _resolve_mcp_session(request, body.conversation_id)
    from chatbot.mcp_bridge import dispatch_mcp_tool_call
    return dispatch_mcp_tool_call(
        conn=conn,
        user_id=sess.user_id or body.user_id or "",
        conversation_id=body.conversation_id,
        tool_name=body.tool_name,
        arguments=body.arguments,
        mcp_token=sess.mcp_token,
    )


@app.post("/chatbot/conversations/{conv_id}/mcp-confirm", response_class=JSONResponse)
def api_mcp_confirm(
    conv_id: str,
    body: _McpConfirmBody,
    conn=Depends(conn_dependency),
    current_user: dict = Depends(get_current_user),
):
    """User-side confirm/decline for a queued MCP tool call. Executes
    the tool (on approve) or records a declined-result message (on
    decline) and returns the new messages so the frontend can render
    the outcome alongside the conversation."""
    from chatbot.mcp_bridge import resolve_mcp_pending
    try:
        appended = resolve_mcp_pending(
            conn=conn, user=current_user, conversation_id=conv_id,
            message_id=body.message_id, approved=bool(body.approved),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"messages": appended}


# --- Config & Tickers (all protected) ---


@app.get("/status", response_class=JSONResponse)
def api_status(conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    return _run(action_status, conn)


@app.post("/llm/test", response_class=JSONResponse)
def api_test_llm_config(body: LlmConfigTestBody, current_user: dict = Depends(get_current_user)):
    provider = str(body.provider or "").strip().lower()
    model = str(body.model or "").strip()
    if not provider:
        raise HTTPException(status_code=400, detail="LLM provider is required")
    if not model:
        raise HTTPException(status_code=400, detail="LLM model or deployment name is required")

    api_key = resolve_api_key_for_provider(provider, body.api_key)
    # Local Ollama legitimately has no api_key — only Ollama Cloud needs
    # a Bearer token. Every other provider still requires a key here.
    if not api_key and provider != "ollama":
        raise HTTPException(
            status_code=400,
            detail="No API key provided for the selected LLM provider, and no matching environment fallback was found.",
        )

    provider_config = _build_llm_test_provider_config(body)
    _reasoning_effort = normalize_reasoning_effort(body.reasoning_effort)
    _test_max_tokens = {"high": 2048, "medium": 1024, "low": 256}.get(_reasoning_effort, 64)

    # Stage 1: structured connectivity check. Verifies auth + the
    # structured-output path the strategy uses for sentiment/event LLMs.
    #
    # Single-shot, fast-fail: a UI test that takes minutes is worse
    # than a clean fail in 30s. ``output_retries=0`` + ``retries=0``
    # caps the wall-clock at one attempt per provider. For codex-cli
    # this matters most — its per-call cost is ~3s app-server spawn
    # + the model's first-turn latency, and a misconfigured model
    # (e.g. an Azure deployment name accidentally pointed at codex)
    # hangs each attempt for the full timeout.
    _structured_started = time.monotonic()
    result = call_structured_llm_by_provider(
        provider,
        api_key,
        model,
        (
            "Return ok=true if this provider configuration is valid. "
            f"Set provider={provider!r} and model={model!r}."
        ),
        LlmConfigTestOutput,
        system_prompt=(
            "You are a connectivity test. Return only the requested structured object. "
            "Do not add extra text."
        ),
        max_output_tokens=_test_max_tokens,
        timeout_sec=30,
        retries=0,
        output_retries=0,
        provider_config=provider_config,
    )
    _structured_elapsed_ms = int((time.monotonic() - _structured_started) * 1000)
    meta = get_last_structured_llm_call_metadata()
    if result is None:
        detail = str(meta.get("error") or "").strip() or "LLM connectivity test failed."
        raise HTTPException(status_code=400, detail=detail)
    try:
        _result_payload = result.dict() if hasattr(result, "dict") else dict(result)
    except Exception:
        _result_payload = {"_unserializable": str(result)[:512]}

    # Stage 2: real-generation smoke. The structured check above could
    # in theory be answered by a degenerate echo path; this prompt forces
    # the model to actually generate free-form text using the SAME
    # call_llm_by_provider hot path that strategies use for non-structured
    # completions. If this returns non-empty, you can be confident the
    # provider is fully usable for the strategy's runtime calls — not
    # just a canned 200 OK from a proxy.
    _smoke_prompt = (
        "In exactly one short sentence (max 20 words), name one common "
        "macroeconomic driver of equity returns. Reply with the sentence "
        "only — no preamble, no quotes, no markdown."
    )
    _smoke_started = time.monotonic()
    _smoke_text = ""
    _smoke_error = ""
    try:
        # Same fast-fail rationale as the structured probe above: one
        # attempt, 30s ceiling. Operators can re-click "Test" if they
        # want a retry rather than waiting silently for the second.
        # codex-cli used to skip this because the legacy app-server
        # spawn was ~3s and risked the proxy timeout; on the Responses
        # API path each call is ~1.5s so the smoke probe is cheap and
        # operators want to see real generated text.
        _smoke_text = call_llm_by_provider(
            provider,
            api_key,
            model,
            _smoke_prompt,
            max_output_tokens=128,
            timeout_sec=30,
            retries=0,
            provider_config=provider_config,
        ) or ""
    except Exception as _e:
        _smoke_error = str(_e)[:512]
    _smoke_elapsed_ms = int((time.monotonic() - _smoke_started) * 1000)
    _smoke_text = (_smoke_text or "").strip()
    # When the provider returned empty without raising, surface the
    # per-thread error string the dispatcher recorded — without it the
    # UI just says "smoke generation returned empty" and the operator
    # has no way to tell whether the model rejected the prompt, the
    # Responses API returned 200 with no output (common with bogus
    # model names), or the smoke prompt tripped a content filter.
    if not _smoke_text and not _smoke_error:
        try:
            _smoke_error = (get_last_plain_llm_call_error() or "")[:512]
        except Exception:
            pass

    # For Ollama, surface the content/thinking split from the smoke call
    # so reasoning text doesn't get mixed into the visible response. Other
    # providers leave this empty; the field is provider-agnostic on the wire.
    _smoke_thinking = ""
    _smoke_content_chars = None
    _smoke_thinking_chars = None
    if provider == "ollama":
        try:
            reasoning = get_last_ollama_reasoning() or {}
            _smoke_thinking = str(reasoning.get("thinking") or "")[:4096]
            _smoke_content_chars = reasoning.get("content_chars")
            _smoke_thinking_chars = reasoning.get("thinking_chars")
        except Exception:
            pass

    return {
        "ok": True,
        "provider": provider,
        "model": model,
        "effective_model": str(meta.get("effective_model") or model),
        "provider_meta": meta.get("provider_meta") or {},
        # Structured connectivity probe — proves auth + structured path.
        "result": _result_payload,
        "latency_ms": _structured_elapsed_ms,
        # Real-generation smoke — proves the model actually completes.
        "smoke_prompt": _smoke_prompt,
        "smoke_response": _smoke_text,
        "smoke_thinking": _smoke_thinking or None,
        "smoke_content_chars": _smoke_content_chars,
        "smoke_thinking_chars": _smoke_thinking_chars,
        "smoke_latency_ms": _smoke_elapsed_ms,
        "smoke_error": _smoke_error or None,
        "message": (
            f"{provider} connectivity test succeeded."
            if _smoke_text
            else f"{provider} structured check passed but real-generation smoke returned empty"
            + (f": {_smoke_error}" if _smoke_error else ".")
        ),
    }


@app.post("/benzinga/test", response_class=JSONResponse)
def api_test_benzinga_sources(body: BenzingaTestBody, current_user: dict = Depends(get_current_user)):
    """Test which Benzinga API sources are accessible with the given API key."""
    import requests as _req
    from datetime import datetime, timedelta

    api_key = (body.api_key or "").strip() or os.environ.get("BENZINGA_API_KEY", "").strip()
    if not api_key:
        raise HTTPException(status_code=400, detail="No Benzinga API key provided and no BENZINGA_API_KEY env var found.")

    today = datetime.utcnow().strftime("%Y-%m-%d")
    week_ago = (datetime.utcnow() - timedelta(days=7)).strftime("%Y-%m-%d")

    # All 10 Benzinga sources with their test endpoints
    _ALL_SOURCES = {
        "ratings": {
            "url": "https://api.benzinga.com/api/v2/calendar/ratings",
            "params": {"parameters[date_from]": week_ago, "parameters[date_to]": today, "pagesize": "1"},
            "label": "Analyst Ratings",
        },
        "insights": {
            "url": "https://api.benzinga.com/api/v2/analyst-insights",
            "params": {"parameters[date_from]": week_ago, "parameters[date_to]": today, "pagesize": "1"},
            "label": "Analyst Insights",
        },
        "insider_trades": {
            "url": "https://api.benzinga.com/api/v1/sec/insider_transactions/transactions",
            "params": {"date_from": week_ago, "date_to": today, "search_keys": "AAPL", "search_keys_type": "symbol"},
            "label": "Insider Transactions",
        },
        "gov_trades": {
            "url": "https://api.benzinga.com/api/v1/government_trades",
            "params": {"date_from": week_ago, "date_to": today, "search_keys": "AAPL", "search_keys_type": "ticker"},
            "label": "Government Trades",
        },
        "ma": {
            "url": "https://api.benzinga.com/api/v2/calendar/ma",
            "params": {"parameters[date_from]": week_ago, "parameters[date_to]": today, "pagesize": "1"},
            "label": "Mergers & Acquisitions",
        },
        "ipos": {
            "url": "https://api.benzinga.com/api/v2/calendar/ipos",
            "params": {"parameters[date_from]": week_ago, "parameters[date_to]": today, "pagesize": "1"},
            "label": "IPOs",
        },
        "splits": {
            "url": "https://api.benzinga.com/api/v2/calendar/splits",
            "params": {"parameters[date_from]": week_ago, "parameters[date_to]": today, "pagesize": "1"},
            "label": "Stock Splits",
        },
        "earnings": {
            "url": "https://api.benzinga.com/api/v2/calendar/earnings",
            "params": {"parameters[date_from]": week_ago, "parameters[date_to]": today, "pagesize": "1"},
            "label": "Earnings Calendar",
        },
        "company_actions": {
            "url": "https://api.benzinga.com/api/v2/calendar/dividends",
            "params": {"parameters[date_from]": week_ago, "parameters[date_to]": today, "pagesize": "1"},
            "label": "Company Actions / Dividends",
        },
        "prediction_markets": {
            "url": "https://api.benzinga.com/api/v1/bulls-bears",
            "params": {"tickers": "AAPL"},
            "label": "Prediction Markets",
        },
    }

    sources_to_test = body.sources if body.sources else list(_ALL_SOURCES.keys())
    results = []
    accessible_count = 0

    for source_key in sources_to_test:
        info = _ALL_SOURCES.get(source_key)
        if not info:
            results.append({"source": source_key, "label": source_key, "ok": False, "status": 0, "error": "Unknown source"})
            continue
        params = dict(info["params"])
        params["token"] = api_key
        try:
            resp = _req.get(info["url"], params=params, timeout=10)
            ok = resp.status_code == 200
            error = ""
            if resp.status_code == 401:
                error = "Invalid API key"
            elif resp.status_code == 403:
                error = "Not included in your plan"
            elif resp.status_code == 404:
                error = "Endpoint not available"
            elif resp.status_code == 429:
                ok = True  # rate limited but accessible
                error = "Rate limited (but accessible)"
            elif resp.status_code != 200:
                error = f"HTTP {resp.status_code}"
            if ok:
                accessible_count += 1
            results.append({
                "source": source_key,
                "label": info["label"],
                "ok": ok,
                "status": resp.status_code,
                "error": error,
            })
        except Exception as e:
            results.append({
                "source": source_key,
                "label": info["label"],
                "ok": False,
                "status": 0,
                "error": str(e)[:100],
            })

    return {
        "ok": accessible_count > 0,
        "accessible": accessible_count,
        "total": len(sources_to_test),
        "results": results,
        "message": f"{accessible_count}/{len(sources_to_test)} Benzinga sources accessible.",
    }


# --- Notification preferences (per-category Discord / iOS push routing) ---


class CategoryRoute(BaseModel):
    discord: bool = True
    push: bool = False


class NotificationPrefsBody(BaseModel):
    categories: Dict[str, CategoryRoute]


@app.get("/notification-preferences", response_class=JSONResponse)
def api_get_notification_prefs(conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    return _run(action_get_notification_preferences, conn, current_user["id"])


@app.put("/notification-preferences", response_class=JSONResponse)
def api_put_notification_prefs(body: NotificationPrefsBody, conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    # _validate_notification_categories raises ValueError on an unknown
    # category, which _run maps to HTTP 400.
    cats = {k: v.dict() for k, v in body.categories.items()}
    return _run(action_set_notification_preferences, conn, current_user["id"], cats)


# --- iOS push device registration (APNs tokens) ---


class PushDeviceBody(BaseModel):
    device_token: str
    platform: str = "ios"
    env: str = "prod"  # "sandbox" for debug builds, "prod" for release
    app_version: Optional[str] = None


@app.post("/push/devices", response_class=JSONResponse)
def api_register_push_device(body: PushDeviceBody, conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    return _run(
        action_register_push_device, conn, current_user["id"],
        body.device_token, body.platform, body.env, body.app_version,
    )


@app.delete("/push/devices/{token}", response_class=JSONResponse)
def api_delete_push_device(token: str, conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    # Scope the delete to the caller so one user can't unregister another's token.
    return _run(action_delete_push_device, conn, token, current_user["id"])


# --- Send-test-notification (verify each delivery option) ---


class TestNotificationBody(BaseModel):
    channel: str  # "discord" | "push"


@app.post("/notifications/test", response_class=JSONResponse)
def api_test_notification(body: TestNotificationBody, conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    """Send a sample notification through one sink directly (bypasses category
    preferences) so the operator can confirm a delivery option works."""
    channel = (body.channel or "").strip().lower()
    if channel == "discord":
        from notifications import _discord_sink
        _discord_sink(
            "notifications",
            "🔔 IntelliStock test notification (Discord)",
            {"title": "Test notification", "color": 0x3498DB,
             "description": "If you can see this, Discord delivery works."},
        )
        return {"ok": True, "channel": "discord"}
    if channel == "push":
        from apns_sender import send_to_user
        res = send_to_user(
            current_user["id"],
            title="IntelliStock",
            body="✅ Test notification — iOS push works.",
            category="test",
            data={"category": "test"},
        )
        sent = int(res.get("sent", 0) or 0)
        return {"ok": sent > 0, "channel": "push", **res}
    raise HTTPException(status_code=400, detail=f"unknown channel: {body.channel}")


@app.get("/tickers", response_class=JSONResponse)
def api_tickers(conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    return _run(action_tickers, conn)


@app.post("/tickers", response_class=JSONResponse)
def api_add_ticker(body: AddTickerBody, conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    return _run(action_add_ticker, conn, body.symbols)


@app.delete("/tickers/{symbol}", response_class=JSONResponse)
def api_remove_ticker(symbol: str, conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    return _run(action_remove_ticker, conn, symbol)


@app.get("/prices", response_class=JSONResponse)
def api_prices(conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    return _run(action_prices, conn)


@app.get("/history", response_class=JSONResponse)
def api_history(ticker: Optional[str] = None, limit: int = 30, conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    return _run(action_history, conn, ticker, limit)


# --- Instances ---


@app.get("/instances", response_class=JSONResponse)
def api_instances(conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    return _run(action_instances, conn)


@app.post("/instances", response_class=JSONResponse)
def api_create_instance(body: CreateInstanceBody, conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    return _run(
        action_create_instance,
        conn,
        body.id,
        name=body.name,
        strategy_id=body.strategy_id,
        key=body.key,
        secret=body.secret,
        granularity_time_increment=body.granularity,
        run_command=body.run_command if body.run_command is not None else False,
        created_by=body.created_by or "user",
        brokerage_id=body.brokerage_id,
        max_usage=body.max_usage,
    )


@app.get("/instances/{instance_id}", response_class=JSONResponse)
def api_get_instance(instance_id: str, conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    return _run(action_get_instance, conn, instance_id)


@app.patch("/instances/{instance_id}", response_class=JSONResponse)
def api_edit_instance(instance_id: str, body: EditInstanceBody, conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    return _run(
        action_edit_instance,
        conn,
        instance_id,
        name=body.name,
        granularity_time_increment=body.granularity,
        max_usage=body.max_usage,
        brokerage_id=body.brokerage_id,
    )


@app.delete("/instances/{instance_id}", response_class=JSONResponse)
def api_delete_instance(instance_id: str, force: bool = False, conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    return _run(action_delete_instance, conn, instance_id, force=force)


@app.post("/instances/{instance_id}/stocks", response_class=JSONResponse)
def api_add_stock(instance_id: str, body: AddStockBody, conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    return _run(action_add_stock, conn, instance_id, body.symbol)


@app.delete("/instances/{instance_id}/stocks/{symbol}", response_class=JSONResponse)
def api_remove_stock(instance_id: str, symbol: str, conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    return _run(action_remove_stock, conn, instance_id, symbol)


@app.post("/instances/{instance_id}/start", response_class=JSONResponse)
def api_start_instance(instance_id: str, conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    return _run(action_start_instance, conn, instance_id)


@app.post("/instances/{instance_id}/stop", response_class=JSONResponse)
def api_stop_instance(instance_id: str, conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    return _run(action_stop_instance, conn, instance_id)


class ClearInstanceStateBody(BaseModel):
    # ``lookback_only``: just GraphNexusTradeContexts + GraphNexusOutcomes.
    # ``full_instance``: all per-instance tables (shared caches preserved).
    scope: str = Field(default="lookback_only", max_length=32)
    # ``apply=False`` is a dry-run (counts only). The UI calls with
    # apply=false first to render a preview, then again with apply=true
    # after the operator confirms a typed phrase.
    apply: bool = Field(default=False)
    # Typed confirmation required for apply=true. Must equal the
    # instance_id from the URL. Stops curl-fired accidental wipes.
    confirm: Optional[str] = Field(default=None, max_length=256)


@app.post("/instances/{instance_id}/clear-state", response_class=JSONResponse)
def api_clear_instance_state(
    instance_id: str,
    body: ClearInstanceStateBody,
    conn=Depends(conn_dependency),
    current_user: dict = Depends(get_current_user),
):
    """Wipe per-instance lookback / decision / cache state.

    Always scoped to a single instance — never touches another
    instance's rows or shared caches (article cache, sentiment cache,
    FinBERT, etc.). See backend/clear_instance_state.py docstring for
    the full preserved/cleared list.

    Operator workflow (mirrored by the InstanceDetailView modal):
      1. POST with ``apply=false`` to get per-table counts.
      2. Review the preview.
      3. POST with ``apply=true`` and ``confirm=<instance_id>`` to
         actually delete. The confirm field stops bare curl-fired wipes.
    """
    scope = (body.scope or "lookback_only").strip()
    if scope not in ("lookback_only", "strategy_cache_only", "full_instance"):
        raise HTTPException(
            status_code=400,
            detail=(
                f"unknown scope {scope!r}; use one of "
                f"'lookback_only', 'strategy_cache_only', 'full_instance'"
            ),
        )
    if body.apply:
        confirm = (body.confirm or "").strip()
        if confirm != str(instance_id).strip():
            raise HTTPException(
                status_code=400,
                detail=(
                    "destructive apply requires confirm == instance_id "
                    "(received empty or mismatched confirm)"
                ),
            )
    try:
        return _run(
            action_clear_instance_state, conn,
            str(instance_id), scope, bool(body.apply),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# --- Config flags ---


@app.post("/config/terminate-price", response_class=JSONResponse)
def api_terminate_price(conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    return _run(action_terminate_price, conn)


@app.post("/config/terminate-discover", response_class=JSONResponse)
def api_terminate_discover(conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    return _run(action_terminate_discover, conn)


@app.post("/config/start-broker", response_class=JSONResponse)
def api_start_broker(conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    return _run(action_start_broker, conn)


# --- Strategies ---


@app.get("/strategies/available", response_class=JSONResponse)
def api_available_strategies(current_user: dict = Depends(get_current_user)):
    """Return all available strategy types from backend/strategies with schema and description."""
    try:
        strategies = get_available_strategies()
        return JSONResponse(content={"strategies": strategies})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/strategies", response_class=JSONResponse)
def api_strategies(conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    return _run(action_strategies, conn)


@app.get("/strategies/{strategy_id}", response_class=JSONResponse)
def api_get_strategy(strategy_id: int, conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    return _run(action_get_strategy, conn, strategy_id)


@app.post("/strategies", response_class=JSONResponse)
def api_create_strategy(body: CreateStrategyBody, conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    return _run(action_create_strategy, conn, body.name, body.strategies)


@app.put("/strategies/{strategy_id}", response_class=JSONResponse)
def api_edit_strategy(strategy_id: int, body: EditStrategyBody, conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    return _run(action_edit_strategy, conn, strategy_id, name=body.name, strategies=body.strategies)


@app.delete("/strategies/{strategy_id}", response_class=JSONResponse)
def api_delete_strategy(strategy_id: int, force: bool = False, conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    return _run(action_delete_strategy, conn, strategy_id, force=force)


@app.post("/instances/{instance_id}/link-strategy", response_class=JSONResponse)
def api_link_strategy(instance_id: str, body: LinkStrategyBody, conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    return _run(action_link_strategy, conn, instance_id, body.strategy_id)


@app.post("/instances/{instance_id}/unlink-strategy", response_class=JSONResponse)
def api_unlink_strategy(instance_id: str, conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    return _run(action_unlink_strategy, conn, instance_id)


@app.post("/instances/{instance_id}/link-brokerage", response_class=JSONResponse)
def api_link_brokerage_to_instance(instance_id: str, body: LinkBrokerageToInstanceBody, conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    """Set or clear the brokerage linked to an instance."""
    return _run(action_link_brokerage_to_instance, conn, instance_id, body.brokerage_id or None)


@app.post("/instances/{instance_id}/link-data-brokerage", response_class=JSONResponse)
def api_link_data_brokerage_to_instance(instance_id: str, body: LinkBrokerageToInstanceBody, conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    """Set or clear the market-data Alpaca brokerage for this instance.

    Separate from the trading brokerage so operators can pair a paper
    trading account with a live data-subscription account. Pass
    brokerage_id=null (or omit) to unlink; strategy then falls back to
    trading creds for data fetches.
    """
    return _run(action_link_data_brokerage_to_instance, conn, instance_id, body.brokerage_id or None)


# --- Models ---


@app.get("/models", response_class=JSONResponse)
def api_list_models(conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    return _run(action_list_models, conn)


@app.get("/models/{model_id}", response_class=JSONResponse)
def api_get_model(model_id: str, conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    return _run(action_get_model, conn, model_id)


@app.post("/models", response_class=JSONResponse)
def api_create_model(body: CreateModelBody, conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    """Create a Model record.

    The Models table is shared across all users. The cli_path allowlist
    and extra_args whitelist (see chatbot/claude_cli_provider.py) prevent
    an authenticated user from pivoting model config into RCE or
    re-enabling CC's filesystem/tool capabilities, so this endpoint is
    open to any authenticated user.
    """
    try:
        return _run(
            action_create_model, conn,
            name=body.name,
            provider=body.provider,
            model=body.model,
            api_key=body.api_key,
            openai_base_url=body.openai_base_url,
            nvidia_base_url=body.nvidia_base_url,
            azure_openai_endpoint=body.azure_openai_endpoint,
            azure_openai_api_version=body.azure_openai_api_version,
            reasoning_effort=body.reasoning_effort,
            cli_path=body.cli_path,
            extra_args=body.extra_args,
            ollama_base_url=body.ollama_base_url,
            ollama_keep_alive=body.ollama_keep_alive,
            ollama_think=body.ollama_think,
            bedrock_region=body.bedrock_region,
            bedrock_reasoning=body.bedrock_reasoning,
            model_cache_family=body.model_cache_family,
            input_cost_per_1m=body.input_cost_per_1m,
            output_cost_per_1m=body.output_cost_per_1m,
            cache_creation_cost_per_1m=body.cache_creation_cost_per_1m,
            cache_read_cost_per_1m=body.cache_read_cost_per_1m,
        )
    except ValueError as e:
        # claude-cli extra_args allowlist rejections surface here.
        raise HTTPException(status_code=400, detail=str(e))


@app.put("/models/{model_id}", response_class=JSONResponse)
def api_edit_model(model_id: str, body: EditModelBody, conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    """Edit a Model record. Open to any authenticated user; same safety
    reasoning as create (cli_path + extra_args allowlists).

    For the four pricing-override fields, an explicit ``null`` in the
    request body clears the override on the row (forces YAML fallback).
    Without this special-case, the generic ``v is not None`` filter below
    would silently drop the field, and once a user set a pricing override
    they could never remove it. We use Pydantic v2's ``model_fields_set``
    to distinguish "field was provided as null" (clear) from "field was
    omitted entirely" (leave unchanged).
    """
    _PRICING_FIELDS = (
        "input_cost_per_1m", "output_cost_per_1m",
        "cache_creation_cost_per_1m", "cache_read_cost_per_1m",
    )
    body_dict = body.dict()
    set_fields = getattr(body, "model_fields_set", None) or set(body_dict.keys())
    kwargs = {}
    for k, v in body_dict.items():
        if k in _PRICING_FIELDS:
            # If the caller explicitly sent the field (even as null), include
            # it so action_edit_model can clear the doc field.
            if k in set_fields:
                kwargs[k] = v
        elif v is not None:
            kwargs[k] = v
    try:
        return _run(action_edit_model, conn, model_id, **kwargs)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# Coarse in-process rate limit for /test-cli: each authenticated user
# spawns a real ``claude`` subprocess and burns subscription tokens, so
# we cap to roughly one probe per 5 s per user to deter spam/DoS.
_TEST_CLI_LAST_CALL: Dict[str, float] = {}
_TEST_CLI_LAST_CALL_LOCK = threading.Lock()
_TEST_CLI_MIN_INTERVAL_SEC = float(os.environ.get("CLAUDE_CLI_TEST_MIN_INTERVAL_SEC", "5"))


class OllamaListModelsBody(BaseModel):
    base_url: str = Field(..., min_length=1, max_length=512)
    api_key: Optional[str] = Field(default=None, max_length=512)


# Imported lazily so missing-module errors at boot time don't take down the
# whole API; instead the endpoint returns a 502 when the SDK isn't present.
import ollama_client  # noqa: E402


@app.post("/ollama/list-models", response_class=JSONResponse)
async def api_ollama_list_models(
    body: OllamaListModelsBody,
    current_user: dict = Depends(get_current_user),
):
    """Discovery endpoint: list models installed on an Ollama host.

    Body: ``{"base_url": str, "api_key": str | null}``. The api_key is
    only consulted when the host is Ollama Cloud (``ollama.com``); local
    hosts ignore it.

    Returns: ``{"models": [{"name", "model", "size_bytes",
    "parameter_size", "quantization_level", "context_length"}, …]}``.

    Errors:
      * 401 if Ollama Cloud rejects the key.
      * 502 if the host is unreachable or returns an upstream error.
    """
    try:
        models = await ollama_client.list_models(body.base_url, body.api_key)
        return {"models": models}
    except ollama_client.OllamaAuthError:
        return JSONResponse(
            status_code=401,
            content={"error": "Authentication failed"},
        )
    except ollama_client.OllamaConnectionError as e:
        return JSONResponse(
            status_code=502,
            content={
                "error": f"Could not reach Ollama at {body.base_url}",
                "detail": str(e),
            },
        )
    except ollama_client.OllamaProviderError as e:
        return JSONResponse(
            status_code=502,
            content={"error": str(e)},
        )


class BedrockListModelsBody(BaseModel):
    api_key: str = Field(..., min_length=1, max_length=4096)
    region: str = Field(..., min_length=1, max_length=32)


# Imported lazily (like ollama_client) so a missing boto3 at boot doesn't take
# down the whole API; the endpoint surfaces the error instead.
import bedrock_client  # noqa: E402


@app.post("/bedrock/list-models", response_class=JSONResponse)
def api_bedrock_list_models(
    body: BedrockListModelsBody,
    current_user: dict = Depends(get_current_user),
):
    """Discovery endpoint: list Bedrock foundation models + cross-region
    inference profiles available to the API key in a region.

    Sync handler (boto3 is synchronous) — FastAPI runs it in a threadpool, so
    the blocking control-plane call doesn't stall the event loop.

    Body: ``{"api_key": str, "region": str}``.
    Returns: ``{"models": [{"id", "name", "provider_name", "kind",
    "supports_tools", "modalities"}, …]}``.

    Errors:
      * 401 if the key is rejected OR lacks ``bedrock:ListFoundationModels``
        (Bedrock API keys can be narrowly scoped). The UI treats this as
        "discovery unavailable" and falls back to manual model-id entry.
      * 502 if the region endpoint is unreachable or returns an upstream error.
    """
    try:
        models = bedrock_client.list_models(body.api_key, body.region)
        return {"models": models}
    except bedrock_client.BedrockAuthError as e:
        return JSONResponse(
            status_code=401,
            content={"error": "Authentication failed", "detail": str(e)},
        )
    except bedrock_client.BedrockConnectionError as e:
        return JSONResponse(
            status_code=502,
            content={"error": f"Could not reach Bedrock in {body.region}", "detail": str(e)},
        )
    except bedrock_client.BedrockProviderError as e:
        return JSONResponse(
            status_code=502,
            content={"error": str(e)},
        )


@app.post("/models/{model_id}/test-cli", response_class=JSONResponse)
async def api_test_claude_cli(
    model_id: str,
    conn=Depends(conn_dependency),
    current_user: dict = Depends(get_current_user),
):
    """Test the Claude Code CLI for a specific model: checks the binary is
    installed, the host is logged in, and the chosen model alias resolves.
    Returns {ok, version, logged_in, model_response, error, elapsed_ms}.

    Open to any authenticated user. Per-user rate-limited (default 5 s
    between probes; override with ``CLAUDE_CLI_TEST_MIN_INTERVAL_SEC``)
    to deter quota burn and global-semaphore starvation.
    """
    now_mono = time.monotonic()
    uid = str(current_user.get("id") or "?")
    with _TEST_CLI_LAST_CALL_LOCK:
        prev = _TEST_CLI_LAST_CALL.get(uid, 0.0)
        if now_mono - prev < _TEST_CLI_MIN_INTERVAL_SEC:
            remaining = int(_TEST_CLI_MIN_INTERVAL_SEC - (now_mono - prev)) + 1
            raise HTTPException(
                status_code=429,
                detail=f"Test endpoint is rate-limited; retry in ~{remaining}s.",
            )
        _TEST_CLI_LAST_CALL[uid] = now_mono

    # Resolve the model doc *now*, then re-fetch under the same call so
    # we don't run the test against a stale/edited record (TOCTOU).
    doc = _run(action_resolve_model_for_runtime, conn, model_id)
    if not doc:
        raise HTTPException(status_code=404, detail=f"Model {model_id} not found")
    provider = (doc.get("provider") or "").strip().lower()
    if provider != "claude-cli":
        raise HTTPException(
            status_code=400,
            detail=f"Test endpoint only valid for provider='claude-cli'; this model uses {provider!r}.",
        )
    cli_path = (doc.get("cli_path") or "claude").strip() or "claude"
    model = (doc.get("model") or "claude-haiku-4-5").strip() or "claude-haiku-4-5"

    # ``test_claude_cli`` spawns subprocesses and waits up to ~30 s. Run
    # it off the event loop so the FastAPI threadpool slot isn't blocked.
    from chatbot.claude_cli_provider import test_claude_cli
    return await asyncio.to_thread(test_claude_cli, cli_path=cli_path, model=model)


# ── Codex CLI: install + auth from the web UI ─────────────────────────────
# These endpoints power the ModelsView "Install Codex" / "Sign in with
# OpenAI" flow. They are intentionally minimal-state: install jobs and
# login jobs live in-process and reap themselves after 30 min. The
# heavy-lifting is in chatbot/codex_cli_provider.py — these handlers
# just expose it over HTTP with auth + rate limits matching test-cli.

_CODEX_OP_LAST_CALL: Dict[str, float] = {}
_CODEX_OP_LAST_CALL_LOCK = threading.Lock()
_CODEX_OP_MIN_INTERVAL_SEC = float(os.environ.get("CODEX_CLI_OP_MIN_INTERVAL_SEC", "2"))


def _codex_op_rate_limit(uid: str, op: str) -> None:
    now_mono = time.monotonic()
    key = f"{uid}:{op}"
    with _CODEX_OP_LAST_CALL_LOCK:
        prev = _CODEX_OP_LAST_CALL.get(key, 0.0)
        if now_mono - prev < _CODEX_OP_MIN_INTERVAL_SEC:
            remaining = int(_CODEX_OP_MIN_INTERVAL_SEC - (now_mono - prev)) + 1
            raise HTTPException(
                status_code=429,
                detail=f"Codex {op} endpoint is rate-limited; retry in ~{remaining}s.",
            )
        _CODEX_OP_LAST_CALL[key] = now_mono


class CodexInstallBody(BaseModel):
    # Optional explicit method. If omitted, the server auto-detects
    # (brew on macOS, else npm).
    method: Optional[str] = Field(default=None, max_length=16)


@app.get("/codex/status", response_class=JSONResponse)
def api_codex_status(current_user: dict = Depends(get_current_user)):
    """Report whether the codex CLI is installed, its version, whether the
    host is authenticated against OpenAI, and which install method (npm/
    brew) is available. Cheap probe — no subprocess unless cache is cold.

    Open to any authenticated user. The codex CLI auth state is shared
    across the deployment (a named Docker volume holds the OAuth tokens),
    so any user picking codex-cli in /models needs full visibility into
    install + auth state to drive the setup flow.
    """
    try:
        from chatbot.codex_cli_provider import (
            detect_install_method,
            get_version,
            is_authenticated,
            is_installed,
        )
    except Exception as e:
        return {
            "installed": False,
            "version": None,
            "authenticated": False,
            "auth_message": "codex provider module not importable",
            "install_method": "unknown",
            "error": str(e)[:200],
        }
    installed = is_installed()
    version = get_version() if installed else None
    auth_ok, auth_msg = (False, "not installed")
    if installed:
        auth_ok, auth_msg = is_authenticated()
    method, _ = detect_install_method()
    return {
        "installed": bool(installed),
        "version": version,
        "authenticated": bool(auth_ok),
        "auth_message": auth_msg,
        "install_method": method,
    }


@app.post("/codex/install", response_class=JSONResponse)
def api_codex_install_start(
    body: CodexInstallBody,
    current_user: dict = Depends(get_current_user),
):
    """Kick off ``npm install -g @openai/codex`` (or brew). Returns a
    job_id the frontend polls via ``GET /codex/install/{job_id}``.

    Open to any authenticated user — the codex CLI is intentionally a
    shared deployment-level dependency. A class-level install semaphore
    + per-user rate limit prevents this from being a DoS vector even
    without an admin gate.
    """
    uid = str(current_user.get("id") or "?")
    _codex_op_rate_limit(uid, "install")
    try:
        from chatbot.codex_cli_provider import CodexInstaller, CodexCliError
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"codex provider unavailable: {e}") from e
    try:
        job = CodexInstaller.start(method=body.method)
    except CodexCliError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"job_id": job.job_id, "state": job.state}


@app.get("/codex/install/{job_id}", response_class=JSONResponse)
def api_codex_install_status(
    job_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Poll the install job. Returns state ∈ {running, success, failed},
    exit_code, last 50 log lines, and a high-level error message on
    failure. Open to any authenticated user — they need to see the
    install progress they kicked off."""
    try:
        from chatbot.codex_cli_provider import CodexInstaller
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"codex provider unavailable: {e}") from e
    snap = CodexInstaller.status(job_id)
    if snap is None:
        raise HTTPException(status_code=404, detail=f"Install job {job_id} not found")
    return snap


class CodexLoginStartBody(BaseModel):
    # Optional override for the cli_path (otherwise resolves "codex" on PATH).
    cli_path: Optional[str] = Field(default=None, max_length=256)


@app.post("/codex/login/start", response_class=JSONResponse)
def api_codex_login_start(
    body: CodexLoginStartBody,
    current_user: dict = Depends(get_current_user),
):
    """Spawn ``codex login --no-browser``, capture the OpenAI pairing URL
    + code, and return them so the frontend can display them. Returns
    immediately once the parse completes (or the spawn fails).

    Open to any authenticated user — the codex auth lives in a shared
    Docker volume and any user picking codex-cli in /models needs to
    drive this flow when the deployment isn't already authenticated.
    A hard cap on concurrent live login jobs + per-user rate limit
    keeps this from being a DoS vector.
    """
    uid = str(current_user.get("id") or "?")
    _codex_op_rate_limit(uid, "login")
    try:
        from chatbot.codex_cli_provider import (
            CodexCliError,
            CodexCliNotInstalledError,
            CodexDeviceCodeLogin,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"codex provider unavailable: {e}") from e
    cli_path = (body.cli_path or "codex").strip() or "codex"
    try:
        job = CodexDeviceCodeLogin.start(cli_path=cli_path)
    except CodexCliNotInstalledError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except CodexCliError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {
        "job_id": job.job_id,
        "state": job.state,
        "pairing_url": job.pairing_url,
        "pairing_code": job.pairing_code,
        "error": job.error,
    }


@app.get("/codex/login/{job_id}/status", response_class=JSONResponse)
def api_codex_login_status(
    job_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Poll the device-code login job. State transitions:
    pending → parsed → success | failed | expired | cancelled.
    """
    try:
        from chatbot.codex_cli_provider import CodexDeviceCodeLogin
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"codex provider unavailable: {e}") from e
    snap = CodexDeviceCodeLogin.status(job_id)
    if snap is None:
        raise HTTPException(status_code=404, detail=f"Login job {job_id} not found")
    return snap


@app.post("/codex/login/{job_id}/cancel", response_class=JSONResponse)
def api_codex_login_cancel(
    job_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Cancel an in-flight device-code login. Kills the subprocess if
    still alive. Idempotent — returns ok even if the job already finished."""
    try:
        from chatbot.codex_cli_provider import CodexDeviceCodeLogin
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"codex provider unavailable: {e}") from e
    ok = CodexDeviceCodeLogin.cancel(job_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Login job {job_id} not found")
    return {"ok": True}


@app.post("/codex/logout", response_class=JSONResponse)
def api_codex_logout(current_user: dict = Depends(get_current_user)):
    """Run ``codex logout`` to drop the stored OpenAI credentials.
    Open to any authenticated user — affects the shared deployment-wide
    auth, so any user who signed in should be able to sign out."""
    try:
        from chatbot.codex_cli_provider import logout as _codex_logout
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"codex provider unavailable: {e}") from e
    ok, msg = _codex_logout()
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"ok": True, "message": msg}


# ── Claude Code CLI: subscription re-auth from the web/mobile UI ───────────
# Mirrors the codex flow but Claude's OAuth needs the authorization code
# pasted BACK into the CLI, so it's a two-step flow: start (returns the
# claude.ai login URL) → submit (the operator pastes the code). The
# heavy-lifting lives in chatbot/claude_cli_provider.py.

_CLAUDE_OP_LAST_CALL: Dict[str, float] = {}
_CLAUDE_OP_LAST_CALL_LOCK = threading.Lock()
_CLAUDE_OP_MIN_INTERVAL_SEC = float(os.environ.get("CLAUDE_CLI_OP_MIN_INTERVAL_SEC", "2"))


def _claude_op_rate_limit(uid: str, op: str) -> None:
    now_mono = time.monotonic()
    key = f"{uid}:{op}"
    with _CLAUDE_OP_LAST_CALL_LOCK:
        prev = _CLAUDE_OP_LAST_CALL.get(key, 0.0)
        if now_mono - prev < _CLAUDE_OP_MIN_INTERVAL_SEC:
            remaining = int(_CLAUDE_OP_MIN_INTERVAL_SEC - (now_mono - prev)) + 1
            raise HTTPException(
                status_code=429,
                detail=f"Claude {op} endpoint is rate-limited; retry in ~{remaining}s.",
            )
        _CLAUDE_OP_LAST_CALL[key] = now_mono


class ClaudeLoginStartBody(BaseModel):
    cli_path: Optional[str] = Field(default=None, max_length=256)


class ClaudeLoginSubmitBody(BaseModel):
    code: str = Field(min_length=6, max_length=1024)


@app.get("/claude/auth/status", response_class=JSONResponse)
def api_claude_auth_status(
    cli_path: Optional[str] = None,
    current_user: dict = Depends(get_current_user),
):
    """Report whether the Claude Code CLI is installed and the deployment's
    Claude subscription is authenticated. Cheap read-only probe. Open to any
    authenticated user — claude-cli auth is shared deployment-wide, so any
    user picking claude-cli in /models needs visibility to drive re-auth."""
    try:
        from chatbot.claude_cli_provider import claude_auth_status
    except Exception as e:
        return {
            "installed": False, "version": None, "authenticated": False,
            "account": None, "auth_message": f"claude provider unavailable: {e}",
        }
    return claude_auth_status((cli_path or "claude").strip() or "claude")


@app.post("/claude/login/start", response_class=JSONResponse)
async def api_claude_login_start(
    body: ClaudeLoginStartBody,
    current_user: dict = Depends(get_current_user),
):
    """Spawn ``claude auth login --claudeai`` under a PTY, capture the
    claude.ai authorization URL, and return it so the UI can display it.
    The operator opens the URL, signs in, copies the code, then calls
    ``/claude/login/{job_id}/submit``.

    Open to any authenticated user — claude-cli auth lives in the shared
    operator HOME. A hard cap on live login jobs + per-user rate limit
    keeps this from being a DoS vector."""
    uid = str(current_user.get("id") or "?")
    _claude_op_rate_limit(uid, "login")
    try:
        from chatbot.claude_cli_provider import ClaudeCliLogin, ClaudeCliError
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"claude provider unavailable: {e}") from e
    cli_path = (body.cli_path or "claude").strip() or "claude"
    try:
        job = await asyncio.to_thread(ClaudeCliLogin.start, cli_path=cli_path)
    except ClaudeCliError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {
        "job_id": job.job_id,
        "state": job.state,
        "login_url": job.login_url,
        "error": job.error,
    }


@app.post("/claude/login/{job_id}/submit", response_class=JSONResponse)
async def api_claude_login_submit(
    job_id: str,
    body: ClaudeLoginSubmitBody,
    current_user: dict = Depends(get_current_user),
):
    """Deliver the operator-pasted authorization code to the waiting
    ``claude auth login`` process and wait for it to exchange the code +
    persist credentials. Returns the final job state."""
    uid = str(current_user.get("id") or "?")
    _claude_op_rate_limit(uid, "submit")
    try:
        from chatbot.claude_cli_provider import ClaudeCliLogin
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"claude provider unavailable: {e}") from e
    snap = await asyncio.to_thread(ClaudeCliLogin.submit_code, job_id, body.code)
    if snap is None:
        raise HTTPException(status_code=404, detail=f"Login job {job_id} not found")
    return snap


@app.get("/claude/login/{job_id}/status", response_class=JSONResponse)
def api_claude_login_status(
    job_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Poll the login job. State transitions:
    pending → parsed → awaiting_code → success | failed | expired | cancelled."""
    try:
        from chatbot.claude_cli_provider import ClaudeCliLogin
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"claude provider unavailable: {e}") from e
    snap = ClaudeCliLogin.status(job_id)
    if snap is None:
        raise HTTPException(status_code=404, detail=f"Login job {job_id} not found")
    return snap


@app.post("/claude/login/{job_id}/cancel", response_class=JSONResponse)
def api_claude_login_cancel(
    job_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Cancel an in-flight login. Kills the subprocess if still alive.
    Idempotent for already-finished jobs."""
    try:
        from chatbot.claude_cli_provider import ClaudeCliLogin
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"claude provider unavailable: {e}") from e
    ok = ClaudeCliLogin.cancel(job_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Login job {job_id} not found")
    return {"ok": True}


@app.post("/claude/logout", response_class=JSONResponse)
async def api_claude_logout(current_user: dict = Depends(get_current_user)):
    """Run ``claude auth logout`` to drop the deployment's Claude
    subscription credentials. Affects shared deployment-wide auth."""
    try:
        from chatbot.claude_cli_provider import claude_logout as _claude_logout
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"claude provider unavailable: {e}") from e
    ok, msg = await asyncio.to_thread(_claude_logout, "claude")
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"ok": True, "message": msg}


@app.delete("/models/{model_id}", response_class=JSONResponse)
def api_delete_model(model_id: str, force: bool = False, conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    return _run(action_delete_model, conn, model_id, force=force)


@app.get("/models/{model_id}/strategies", response_class=JSONResponse)
def api_model_strategies(model_id: str, conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    return _run(action_model_strategies, conn, model_id)


# --- Backtests ---


@app.get("/backtests", response_class=JSONResponse)
def api_list_backtests(
    instance_id: Optional[str] = None,
    page: int = 1,
    per_page: int = 20,
    sort_by: str = "completed_at",
    sort_order: str = "desc",
    conn=Depends(conn_dependency),
    current_user: dict = Depends(get_current_user),
):
    return _run(
        action_list_backtests,
        conn,
        instance_id=instance_id,
        page=page,
        per_page=per_page,
        sort_by=sort_by,
        sort_order=sort_order,
    )


@app.get("/instances/{instance_id}/backtests", response_class=JSONResponse)
def api_list_instance_backtests(
    instance_id: str,
    page: int = 1,
    per_page: int = 20,
    sort_by: str = "completed_at",
    sort_order: str = "desc",
    conn=Depends(conn_dependency),
    current_user: dict = Depends(get_current_user),
):
    return _run(
        action_list_backtests,
        conn,
        instance_id=instance_id,
        page=page,
        per_page=per_page,
        sort_by=sort_by,
        sort_order=sort_order,
    )


@app.post("/backtests", response_class=JSONResponse)
def api_create_backtest(body: CreateBacktestBody, conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    gran_sec = parse_granularity_to_seconds(body.granularity) if body.granularity else 60
    return _run(
        action_create_backtest,
        conn,
        body.instance_id,
        body.stocks,
        body.start_date,
        body.end_date,
        granularity_sec=gran_sec,
        key=body.key,
        secret=body.secret,
        initial_cash=body.initial_cash,
    )


@app.delete("/backtests/{backtest_id}", response_class=JSONResponse)
def api_delete_backtest(backtest_id: int, conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    return _run(action_delete_backtest, conn, backtest_id)


@app.post("/backtests/stop-all", response_class=JSONResponse)
def api_stop_all_backtests(conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    """Stop all running backtests and clear the queue."""
    return _run(action_stop_all_backtests, conn)


@app.post("/backtests/{backtest_id}/stop", response_class=JSONResponse)
def api_stop_backtest(backtest_id: int, conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    return _run(action_stop_backtest, conn, backtest_id)


@app.post("/backtests/{backtest_id}/pause", response_class=JSONResponse)
def api_pause_backtest(backtest_id: int, conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    return _run(action_pause_backtest, conn, backtest_id)


@app.post("/backtests/{backtest_id}/resume", response_class=JSONResponse)
def api_resume_backtest(backtest_id: int, conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    return _run(action_resume_backtest, conn, backtest_id)


@app.get("/backtests/{backtest_id}/status", response_class=JSONResponse)
def api_backtest_status(backtest_id: int, conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    """Return status and progress from BacktestResults (or queue if not yet run)."""
    return _run(action_get_backtest_status, conn, backtest_id)


@app.get("/backtests/{backtest_id}/summary", response_class=JSONResponse)
def api_summarize_backtest(backtest_id: int, conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    return _run(action_summarize_backtest, conn, backtest_id)


@app.get("/backtests/{backtest_id}/logs", response_class=JSONResponse)
def api_backtest_logs(backtest_id: int, conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    return _run(action_backtest_logs, conn, backtest_id)


# --- Nexus graph builds (history + log files) ---
#
# ROUTE-ORDER GUARD: literal /nexus-graph-builds/latest routes MUST be declared
# before /nexus-graph-builds/{build_id}. FastAPI matches in declaration order,
# so a later {build_id} route would otherwise capture "latest" and route all
# /latest traffic into the generic by-id handlers. Keep the order below intact.

@app.get("/nexus-graph-builds", response_class=JSONResponse)
def api_list_nexus_graph_builds(limit: int = 50, conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    """List recent nexus_graph_engine builds (newest first)."""
    return _run(action_list_nexus_graph_builds, conn, limit)


@app.get("/nexus-graph-builds/latest", response_class=JSONResponse)
def api_get_latest_nexus_graph_build(conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    """Return the most recent build's full document (shortcut for id=latest)."""
    return _run(action_get_nexus_graph_build, conn, "latest")


@app.get("/nexus-graph-builds/latest/logs", response_class=JSONResponse)
def api_get_latest_nexus_graph_build_logs(since_line: int = 0, conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    """Return the most recent build's log (shortcut for id=latest).

    Pass since_line=N to fetch only lines with 0-based index >= N. The response
    includes next_line which the client should echo on the next poll to tail
    efficiently. since_line=0 (default) returns the full log.
    """
    return _run(action_nexus_graph_build_logs, conn, "latest", since_line)


@app.get("/nexus-graph-builds/{build_id}", response_class=JSONResponse)
def api_get_nexus_graph_build(build_id: str, conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    """Return full build document. Pass build_id=latest to resolve to the newest build."""
    return _run(action_get_nexus_graph_build, conn, build_id)


@app.get("/nexus-graph-builds/{build_id}/logs", response_class=JSONResponse)
def api_get_nexus_graph_build_logs(build_id: str, since_line: int = 0, conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    """Return log file for a build. Pass build_id=latest to resolve to the newest build.

    Pass since_line=N to fetch only lines with 0-based index >= N. The response
    includes next_line which the client should echo on the next poll to tail
    efficiently. since_line=0 (default) returns the full log.
    """
    return _run(action_nexus_graph_build_logs, conn, build_id, since_line)


# --- Live trading state, commands, logs ---
#
# GET  /instances/{id}/live-state     — UI poll every 2s while active, 10s idle
# GET  /instances/{id}/live-logs      — since_line cursor tailing (same contract as nexus)
# POST /instances/{id}/live-command   — halt / close_position / submit_order
# GET  /live-commands/{command_id}    — UI polls command status until terminal
#
# The POST is open to any authenticated user; the destructive commands (halt,
# close_position, submit_order) can move real money, so the UI typed-confirm
# modals provide the safety gate. Reads are authenticated too.

class LiveCommandBody(BaseModel):
    type: str = Field(pattern="^(halt|close_position|submit_order)$")
    payload: Optional[dict] = None


@app.get("/instances/{instance_id}/live-state", response_class=JSONResponse)
def api_get_live_state(instance_id: str, conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    """Return the current LiveState row for an instance. 404 if the instance
    has never booted a broker, or has already been shut down (no row)."""
    return _run(action_get_live_state, conn, instance_id)


@app.get("/instances/{instance_id}/live-logs", response_class=JSONResponse)
def api_get_live_trading_logs(instance_id: str, since_line: int = 0, conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    """Live-tail of the broker's log for this instance. since_line cursor
    exactly mirrors /nexus-graph-builds/{id}/logs so the frontend can reuse
    the same polling logic with a different endpoint."""
    return _run(action_live_trading_logs, conn, instance_id, since_line)


@app.post("/instances/{instance_id}/live-command", response_class=JSONResponse)
def api_submit_live_command(instance_id: str, body: LiveCommandBody, conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    """Submit a command to the broker. Any authenticated user can halt,
    close a position, or submit a manual order — the UI typed-confirm modals
    already provide the safety gate. Broker picks up within ~1s.

    Body: {type: "halt"|"close_position"|"submit_order", payload: {...}}
    Payload shape depends on type — see the action docstring. The response
    includes {command_id} which the client should poll at
    /live-commands/{command_id} to see status and result.
    """
    return _run(
        action_submit_live_command, conn, instance_id, body.type, body.payload or {},
        current_user.get("id") or current_user.get("username"),
    )


@app.get("/live-commands/{command_id}", response_class=JSONResponse)
def api_get_live_command(command_id: str, conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    """Return the status and result of a previously-submitted live command.
    UI polls this until `status` ∈ {completed, failed}."""
    return _run(action_get_live_command, conn, command_id)


@app.get("/backtests/{backtest_id}/graph-data", response_class=JSONResponse)
def api_graph_backtest_data(backtest_id: int, conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    return _run(action_graph_backtest_data, conn, backtest_id)


@app.get("/backtests/{backtest_id}/playback-data", response_class=JSONResponse)
def api_backtest_playback_data(backtest_id: int, conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    """Return time-ordered event stream for the backtest playback UI."""
    return _run(action_get_backtest_playback_data, conn, backtest_id)


@app.get("/backtests/best-per-strategy", response_class=JSONResponse)
def api_backtest_best_per_strategy(conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    return _run(action_backtest_best_per_strategy, conn)


# --- AI Backtesting Agent ---


@app.get("/agent/control", response_class=JSONResponse)
def api_agent_control_get(conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    """Get agent status: running, last_run_date, count_today."""
    return _run(action_agent_control_get, conn)


@app.post("/agent/control", response_class=JSONResponse)
def api_agent_control_set(body: AgentControlBody, conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    """Start, stop, or pause/resume the AI backtesting agent. Send running and/or paused (omit to leave unchanged). Optional special_request: instruction for strategy generation (e.g. include a specific strategy in each)."""
    return _run(action_agent_control_set, conn, body.running, body.paused, body.special_request)


@app.post("/agent/resume-timer", response_class=JSONResponse)
def api_resume_timer(body: ResumeTimerBody, conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    """Schedule agent to resume after specified time. All time fields optional (default 0)."""
    return _run(action_resume_timer, conn, body.days or 0, body.hours or 0, body.minutes or 0, body.seconds or 0)


@app.post("/agent/restart", response_class=JSONResponse)
def api_agent_restart(body: AgentRestartBody = AgentRestartBody(), conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    """Restart the AI backtesting agent: stop, wait a few seconds, then start. Optional special_request for strategy generation."""
    return _run(action_agent_restart, conn, body.special_request)


@app.post("/agent/increment-count", response_class=JSONResponse)
def api_agent_increment_count(conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    """Increment today's backtest count (used by agent when it queues a backtest)."""
    count = action_agent_increment_backtest_count(conn)
    return {"count_today": count}


@app.get("/agent/results", response_class=JSONResponse)
def api_agent_results(conn=Depends(conn_dependency), limit: int = 100, current_user: dict = Depends(get_current_user)):
    """List AI backtesting results (profitable strategies)."""
    return _run(action_list_ai_backtest_results, conn, limit)


@app.post("/agent/results", response_class=JSONResponse)
def api_agent_results_post(body: AgentResultBody, conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    """Record an AI backtest result (used by the agent service)."""
    return _run(
        action_insert_ai_backtest_result,
        conn,
        strategy_snapshot=body.strategy_snapshot,
        backtest_id=body.backtest_id,
        instance_id=body.instance_id,
        strategy_id=body.strategy_id,
        overall_profit=body.overall_profit,
        pnl_percent=body.pnl_percent,
        pnl_per_stock=body.pnl_per_stock,
        pnl_percent_per_stock=body.pnl_percent_per_stock,
        stock_price_change=body.stock_price_change,
        start_date=body.start_date,
        end_date=body.end_date,
        stocks_used=body.stocks_used,
        status=body.status,
        agent_notes=body.agent_notes,
    )


@app.get("/agent/best", response_class=JSONResponse)
def api_agent_best(conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    """Get the current best strategy (tag Best in Strategies table). Returns null if none set."""
    best = action_agent_get_best(conn)
    return {"best": best}


@app.post("/agent/best", response_class=JSONResponse)
def api_agent_set_best_post(body: AgentSetBestBody, conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    """Set or update the best strategy if this result outperforms current best. Persists in Strategies with tag Best."""
    return _run(
        action_agent_set_best,
        conn,
        strategy_snapshot=body.strategy_snapshot,
        overall_profit=body.overall_profit,
        pnl_percent=body.pnl_percent,
        results_summary=body.results_summary,
    )


@app.get("/agent/top5", response_class=JSONResponse)
def api_agent_top5_get(conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    """Get the current top-5 best strategies ranked by P&L%."""
    return _run(action_agent_get_top5, conn)


@app.post("/agent/top5", response_class=JSONResponse)
def api_agent_top5_update(body: AgentUpdateTop5Body, conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    """Submit a candidate strategy to the top-5 list. Replaces worst entry if candidate qualifies."""
    return _run(
        action_agent_update_top5, conn,
        strategy_snapshot=body.strategy_snapshot,
        overall_profit=body.overall_profit,
        pnl_percent=body.pnl_percent,
        strategy_id=body.strategy_id,
        backtest_id=body.backtest_id,
        results_summary=body.results_summary,
    )


@app.post("/agent/cycle-log", response_class=JSONResponse)
def api_agent_cycle_log_create(body: AgentCycleLogCreateBody, conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    """Create a new agent cycle log entry (called by the agent at strategy start)."""
    return _run(action_agent_cycle_log_create, conn, body.cycle_id, body.name)


@app.post("/agent/cycle-log/{log_id}/update", response_class=JSONResponse)
def api_agent_cycle_log_update(log_id: str, body: AgentCycleLogUpdateBody, conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    """Update an agent cycle log entry with stage progress or final status."""
    return _run(action_agent_cycle_log_update, conn, log_id, body.status, body.stages, body.final_result)


@app.get("/agent/runs", response_class=JSONResponse)
def api_agent_runs(page: int = 1, per_page: int = 20, conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    """List agent strategy attempts (cycle log) with pagination."""
    return _run(action_list_agent_runs, conn, page, per_page)


@app.post("/agent/runs/{log_id}/force-stop", response_class=JSONResponse)
def api_agent_run_force_stop(log_id: str, conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    """Manually mark a stale 'running' agent cycle log entry as stopped."""
    return _run(action_agent_run_force_stop, conn, log_id)


# --- Daily Digest Engine ---


@app.get("/digest/control", response_class=JSONResponse)
def api_digest_control_get(conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    """Get daily digest engine status: running, send_now, last_sent_at, last_morning_at, last_evening_at."""
    return _run(action_digest_control_get, conn)


@app.post("/digest/control", response_class=JSONResponse)
def api_digest_control_set(body: DigestControlBody, conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    """Start or stop the digest engine (running), or set send_now to trigger an immediate send."""
    return _run(action_digest_control_set, conn, body.running, body.send_now)


@app.post("/digest/send-now", response_class=JSONResponse)
def api_digest_send_now(conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    """Trigger the digest engine to send a brief immediately (morning-style summary to #briefs)."""
    return _run(action_digest_trigger_send_now, conn)


# --- Discover Engine ---


@app.get("/discover/control", response_class=JSONResponse)
def api_discover_control_get(conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    """Get discover engine status: running and terminate."""
    return _run(action_discover_control_get, conn)


@app.post("/discover/control", response_class=JSONResponse)
def api_discover_control_set(body: DiscoverControlBody, conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    """Start or stop the discover engine."""
    return _run(action_discover_control_set, conn, body.running)


# --- Graph Nexus (server-controlled subprocess) ---


@app.get("/nexus/control", response_class=JSONResponse)
def api_nexus_control_get(conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    """Get Graph Nexus service control state (running)."""
    return _run(action_nexus_control_get, conn)


@app.post("/nexus/control", response_class=JSONResponse)
def api_nexus_control_set(body: NexusControlBody, conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    """Start or stop the Graph Nexus service. Optional phase selectors accept execution-order 1-14 or labels like 2B / 6B."""
    return _run(
        action_nexus_control_set,
        conn,
        body.running,
        body.start_phase,
        body.end_phase,
        body.force_bootstrap_rebuild,
        body.auto_update_enabled,
        body.auto_update_interval_hours,
        body.auto_update_start_phase,
        body.auto_update_end_phase,
        body.phase7_history_quarters,
        body.historical_mode_enabled,
        body.historical_start_date,
        body.selected_phases,
    )


@app.get("/nexus/status", response_class=JSONResponse)
def api_nexus_status(conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    """Comprehensive Graph Nexus status: control, graph build progress, SEC EDGAR scraper progress, graph_built."""
    return _run(action_nexus_status, conn)


@app.get("/nexus/cache", response_class=JSONResponse)
def api_nexus_cache(current_user: dict = Depends(get_current_user)):
    """List top-level cache entries from the mounted Nexus cache root or the running Nexus container."""
    return action_nexus_cache_entries()


@app.post("/nexus/rebuild", response_class=JSONResponse)
def api_nexus_rebuild(body: NexusRebuildBody, conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    """Queue a Nexus rebuild. Optionally select destructive mode and/or delete selected cache entries from the mounted cache root or the running Nexus container before restart."""
    if not body.confirm:
        raise HTTPException(status_code=400, detail="Send {\"confirm\": true} to confirm rebuild.")
    return _run(
        action_nexus_rebuild,
        conn,
        delete_cache_paths=body.delete_cache_paths,
        destructive=body.destructive,
        force_bootstrap_rebuild=body.force_bootstrap_rebuild,
    )


@app.post("/nexus/delete-edges", response_class=JSONResponse)
def api_nexus_delete_edges(body: NexusDeleteBody, conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    """Delete selected Nexus graph phase outputs and track live progress through the Nexus control document."""
    return _run(action_nexus_delete_edges, conn, body.selected_phases)


# ── Market Trends ─────────────────────────────────────────────────────────────

@app.get("/trends", response_class=JSONResponse)
def api_list_trends(instance_id: Optional[str] = None, status: Optional[str] = "active", conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    """List market trends tracked by the nexus strategy. Filter by instance_id and/or status."""
    return _run(action_list_trends, conn, instance_id=instance_id, status=status)


@app.get("/trends/{trend_id}", response_class=JSONResponse)
def api_get_trend(trend_id: str, conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    """Get a single trend by its ID."""
    return _run(action_get_trend, conn, trend_id)


@app.post("/trends/{trend_id}/end", response_class=JSONResponse)
def api_end_trend(trend_id: str, body: EndTrendBody = EndTrendBody(), conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    """End an active trend. Optionally provide a reason."""
    return _run(action_end_trend, conn, trend_id, reason=body.reason)


@app.delete("/trends/{trend_id}", response_class=JSONResponse)
def api_delete_trend(trend_id: str, conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    """Delete a trend by its ID."""
    return _run(action_delete_trend, conn, trend_id)


# ── Discovered Stocks ──────────────────────────────────────────────────────────

@app.get("/discovered", response_class=JSONResponse)
def api_list_discovered(instance_id: Optional[str] = None, status: Optional[str] = "active", conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    """List stocks discovered by the nexus trend tracking engine. Filter by instance_id and/or status."""
    return _run(action_list_discovered_stocks, conn, instance_id=instance_id, status=status)


@app.delete("/discovered/{instance_id}/{ticker}", response_class=JSONResponse)
def api_remove_discovered(instance_id: str, ticker: str, conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    """Remove a discovered stock from an instance (marks it as removed)."""
    return _run(action_remove_discovered_stock, conn, instance_id, ticker)


# ── Nexus Trend/Discovery Config ───────────────────────────────────────────────

@app.get("/nexus/config/{instance_id}", response_class=JSONResponse)
def api_nexus_config_get(instance_id: str, conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    """Get nexus trend tracking and stock discovery configuration for an instance."""
    return _run(action_nexus_config_get, conn, instance_id)


@app.patch("/nexus/config/{instance_id}", response_class=JSONResponse)
def api_nexus_config_set(instance_id: str, body: NexusConfigUpdateBody, conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    """Update nexus trend tracking and stock discovery configuration for an instance."""
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update.")
    return _run(action_nexus_config_set, conn, instance_id, updates)


# ── Brokerage Accounts ─────────────────────────────────────────────────────────

@app.get("/brokerages", response_class=JSONResponse)
def api_list_brokerages(conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    """List all linked brokerage accounts (credentials masked)."""
    return _run(action_list_brokerages, conn)


@app.post("/brokerages/robinhood/accounts", response_class=JSONResponse)
def api_fetch_robinhood_accounts(body: FetchRobinhoodAccountsBody, current_user: dict = Depends(get_current_user)):
    """Validate Robinhood tokens and return available accounts for account selection."""
    return _run(action_fetch_robinhood_accounts, body.access_token, body.refresh_token, body.device_token)


@app.post("/brokerages", response_class=JSONResponse)
def api_link_brokerage(body: LinkBrokerageBody, conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    """Link a new brokerage account (Alpaca or Robinhood). Validates credentials before saving."""
    if body.brokerage_type == "alpaca":
        if not body.key or not body.secret:
            raise HTTPException(status_code=400, detail="key and secret are required for Alpaca")
        return _run(
            action_link_alpaca, conn, body.account_name, body.key, body.secret,
            body.paper if body.paper is not None else True,
            (body.alpaca_data_feed or "iex"),
        )
    else:
        if not body.access_token or not body.refresh_token:
            raise HTTPException(status_code=400, detail="access_token and refresh_token are required for Robinhood")
        return _run(action_link_robinhood_tokens, conn, body.account_name, body.access_token, body.refresh_token,
                    body.device_token, body.expires_in, body.obtained_at_epoch, body.account_number)


@app.put("/brokerages/{brokerage_id}", response_class=JSONResponse)
def api_update_brokerage(brokerage_id: str, body: UpdateBrokerageBody, conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    """Update a linked brokerage account's name and/or credentials."""
    return _run(action_update_brokerage, conn, brokerage_id,
                account_name=body.account_name, key=body.key, secret=body.secret, paper=body.paper,
                access_token=body.access_token, refresh_token=body.refresh_token,
                device_token=body.device_token, account_number=body.account_number,
                alpaca_data_feed=body.alpaca_data_feed)


@app.delete("/brokerages/{brokerage_id}", response_class=JSONResponse)
def api_delete_brokerage(brokerage_id: str, conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    """Remove a linked brokerage account."""
    return _run(action_delete_brokerage, conn, brokerage_id)


class TestAlpacaBody(BaseModel):
    # R16 phase-3: key/secret are now optional. The endpoint accepts EITHER
    # raw creds (add-mode flow, before save) OR a brokerage_id (edit-mode
    # flow, where the form intentionally hides the stored secret). When
    # brokerage_id is provided and key/secret are blank, the endpoint
    # decrypts the stored creds from RethinkDB and tests those instead.
    # paper / alpaca_data_feed remain user-overridable so an operator can
    # test "what would happen if I switched feed=sip" without writing to DB.
    key: Optional[str] = ""
    secret: Optional[str] = ""
    brokerage_id: Optional[str] = None
    paper: Optional[bool] = None
    alpaca_data_feed: Optional[str] = Field(default=None, pattern="^(iex|sip)$")


@app.post("/brokerages/test-alpaca", response_class=JSONResponse)
def api_test_alpaca_brokerage(
    body: TestAlpacaBody,
    conn=Depends(conn_dependency),
    current_user: dict = Depends(get_current_user),
):
    """R16 (2026-04-25): Diagnostic test suite for Alpaca creds.

    Runs 5 endpoints (account, clock, bars, latest_quote, news) against the
    supplied key/secret and returns per-test PASS/FAIL plus aggregated hints.
    Used by the brokerage add/edit popup so the user can see at config time
    whether the creds will actually work for the bars/news/account paths the
    runtime broker needs. Probe-only — does NOT save anything.

    Two cred-source paths:
      1. Raw key+secret in body (add-mode / "test the creds I'm about to save").
      2. brokerage_id in body, no key/secret (edit-mode / "test the creds
         already stored for this brokerage"). Falls back to row's
         stored ``alpaca_paper`` and ``alpaca_data_feed`` when not overridden.
    """
    key = (body.key or "").strip()
    secret = (body.secret or "").strip()
    paper = body.paper
    feed = (body.alpaca_data_feed or "").strip().lower() or None

    # R16 phase-3: edit-mode path — pull stored creds when caller didn't pass them.
    if (not key or not secret) and body.brokerage_id:
        try:
            from secret_store import decrypt as _decrypt
            row = (
                _r_auth.db("IntelliStock")
                .table("BrokerageAccounts")
                .get(str(body.brokerage_id))
                .run(conn)
            )
            if not row:
                raise HTTPException(status_code=404, detail=f"Brokerage {body.brokerage_id!r} not found")
            if str(row.get("brokerage_type") or "").strip().lower() != "alpaca":
                raise HTTPException(
                    status_code=400,
                    detail=f"Brokerage {body.brokerage_id!r} is not an Alpaca account",
                )
            try:
                key = _decrypt(row.get("alpaca_key")) or ""
                secret = _decrypt(row.get("alpaca_secret")) or ""
            except Exception as _decrypt_exc:
                raise HTTPException(
                    status_code=500,
                    detail=(
                        f"Could not decrypt stored creds for {body.brokerage_id} "
                        f"(check INTELLISTOCK_CRED_KEY): "
                        f"{type(_decrypt_exc).__name__}: {_decrypt_exc}"
                    ),
                )
            if paper is None:
                paper = bool(row.get("alpaca_paper", False))
            if feed is None:
                _stored_feed = str(row.get("alpaca_data_feed") or "").strip().lower()
                feed = _stored_feed if _stored_feed in ("iex", "sip") else "iex"
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"Could not load brokerage {body.brokerage_id!r}: {type(e).__name__}: {e}",
            )

    if not key or not secret:
        raise HTTPException(
            status_code=400,
            detail="Provide either key+secret or brokerage_id of an existing Alpaca brokerage",
        )

    try:
        return alpaca_run_diagnostic_suite(
            key=key,
            secret=secret,
            feed=feed or "iex",
            paper=bool(paper) if paper is not None else True,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Alpaca test suite error: {type(e).__name__}: {e}")


@app.post("/brokerages/{brokerage_id}/refresh", response_class=JSONResponse)
def api_refresh_brokerage(brokerage_id: str, conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    """Manually refresh Robinhood tokens for a linked account."""
    return _run(action_refresh_robinhood, conn, brokerage_id)


@app.post("/brokerages/ensure-ai-alpaca", response_class=JSONResponse)
def api_ensure_ai_alpaca(conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    """Create (or find) an Alpaca brokerage for the AI engine using env vars. Returns brokerage_id."""
    brokerage_id = action_ensure_ai_alpaca_brokerage(conn)
    return {"brokerage_id": brokerage_id}


@app.get("/brokerages/{brokerage_id}/portfolio-history", response_class=JSONResponse)
def api_portfolio_history(
    brokerage_id: str,
    range: Optional[str] = "1M",
    conn=Depends(conn_dependency),
    current_user: dict = Depends(get_current_user),
):
    """
    Fetch portfolio equity history for a linked brokerage account.
    range: 1D | 1W | 1M | 3M | YTD | 1Y | ALL
    Returns timestamps (ms epoch), values, current_value, change_abs, change_pct.
    """
    return _run(action_get_portfolio_history, conn, brokerage_id, range)


# ── Instance-scoped portfolio history (proxies to broker's own API) ───────────
#
# Used by LiveTradingView so the frontend never has to learn the brokerage_id
# and never reads from the in-container snapshot writer (which samples sparsely
# at the strategy tick cadence). Always pulls from the broker's authoritative
# history endpoint — Alpaca /v2/account/portfolio/history or RH Bonfire.

@app.get("/instances/{instance_id}/portfolio-history", response_class=JSONResponse)
def api_instance_portfolio_history(
    instance_id: str,
    range: Optional[str] = "1D",
    conn=Depends(conn_dependency),
    current_user: dict = Depends(get_current_user),
):
    """Look up the linked brokerage_id for the instance, then fetch broker-side
    portfolio history (Alpaca/RH dispatch handled by action_get_portfolio_history)."""
    try:
        inst = action_get_instance(conn, instance_id)
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Instance not found: {e}")
    bid = (inst or {}).get("brokerage", {}).get("brokerage_id") or (inst or {}).get("brokerage_id")
    # Some action_get_instance shapes nest the id under brokerage.id; try that too.
    if not bid:
        bid = (inst or {}).get("brokerage", {}).get("id")
    if not bid:
        raise HTTPException(status_code=400, detail="Instance has no brokerage linked")
    return _run(action_get_portfolio_history, conn, bid, range)


# ── Per-symbol historicals (Robinhood public) ─────────────────────────────────
#
# Hits Robinhood's unauthenticated /quotes/historicals/ batch endpoint. Used by
# the live trading view to render per-position price charts that share the same
# range toggle as the equity curve. Cached briefly per (range, symbol-csv) so
# rapid repolls don't hammer Robinhood.

_SYMBOL_HISTORICALS_CACHE: dict = {}
_SYMBOL_HISTORICALS_TTL_SEC = 60.0  # cache for 60s — matches RH's data freshness

# Range → (interval, span) per RH's documented values.
# 1D uses 5-minute bars over a day; ALL falls back to weekly bars over 5 years.
_RH_RANGE_MAP = {
    "1D":  ("5minute",  "day"),
    "1W":  ("10minute", "week"),
    "1M":  ("hour",     "month"),
    "3M":  ("day",      "3month"),
    "YTD": ("day",      "year"),    # caller may filter to year-start
    "1Y":  ("day",      "year"),
    "ALL": ("week",     "5year"),
}


def fetch_symbol_historicals(symbols: str, range_str: str = "1D") -> dict:
    """Fetch close-price history for one or more tickers from Robinhood's
    public /quotes/historicals/ endpoint. Returned shape:
    ``{range, results: {SYMBOL: [{ts, value}, ...], ...}, error?: str}``.

    Cached for ``_SYMBOL_HISTORICALS_TTL_SEC`` seconds per (range, sorted-symbols)
    so rapid repolls don't hammer RH. Used by both the HTTP endpoint and the
    chatbot's get_price_history tool — single source of truth.
    """
    import time as _time
    import requests as _req

    rng = (range_str or "1D").strip().upper()
    if rng not in _RH_RANGE_MAP:
        raise ValueError(f"Invalid range: {rng}. Must be one of: {', '.join(_RH_RANGE_MAP.keys())}")
    interval, span = _RH_RANGE_MAP[rng]
    # RH rejects bounds=trading for spans > day. Use 'regular' for everything
    # except 1D where extended-hours truncation is fine.
    bounds = "trading" if span == "day" else "regular"

    syms = [s.strip().upper().replace(".", "-") for s in (symbols or "").split(",") if s.strip()]
    if not syms:
        return {"range": rng, "results": {}}
    if len(syms) > 75:
        syms = syms[:75]  # RH batch cap

    cache_key = (rng, ",".join(sorted(set(syms))))
    now = _time.time()
    cached = _SYMBOL_HISTORICALS_CACHE.get(cache_key)
    if cached and (now - cached[0]) < _SYMBOL_HISTORICALS_TTL_SEC:
        return {"range": rng, "results": cached[1]}

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
    }
    out: dict = {sym: [] for sym in syms}
    try:
        rsp = _req.get(
            "https://api.robinhood.com/quotes/historicals/",
            params={
                "symbols": ",".join(syms),
                "interval": interval,
                "span": span,
                "bounds": bounds,
            },
            headers=headers,
            timeout=15,
        )
        if not rsp.ok:
            return {"range": rng, "results": out, "error": f"RH HTTP {rsp.status_code}"}
        data = rsp.json() or {}
        for res in (data.get("results") or []):
            sym = (res.get("symbol") or "").upper()
            if not sym:
                continue
            points = []
            for h in (res.get("historicals") or []):
                ts = h.get("begins_at")
                close = h.get("close_price")
                if ts is None or close is None:
                    continue
                try:
                    points.append({"ts": ts, "value": float(close)})
                except (TypeError, ValueError):
                    continue
            # YTD: trim to start of current year (RH 'year' span returns trailing 1y).
            if rng == "YTD" and points:
                from datetime import datetime as _dt, timezone as _tz
                jan1 = _dt(_dt.now(_tz.utc).year, 1, 1, tzinfo=_tz.utc).isoformat()
                points = [p for p in points if p["ts"] >= jan1]
            out[sym] = points
    except Exception as e:
        return {"range": rng, "results": out, "error": str(e)}

    _SYMBOL_HISTORICALS_CACHE[cache_key] = (now, out)
    return {"range": rng, "results": out}


@app.get("/symbol-historicals", response_class=JSONResponse)
def api_symbol_historicals(
    symbols: str,
    range: str = "1D",
    current_user: dict = Depends(get_current_user),
):
    """
    Fetch OHLC bar history for one or more symbols from Robinhood's public
    /quotes/historicals/ endpoint. Returns close-price points keyed by symbol.

    Query params:
      symbols: CSV of tickers (max 75 per RH batch limit), e.g. "AAPL,MSFT,GOOG"
      range:   1D | 1W | 1M | 3M | YTD | 1Y | ALL

    Response: { range, results: { SYMBOL: [{ts, value}, ...], ... } }
    """
    try:
        return fetch_symbol_historicals(symbols, range)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── LLM Output Logs ────────────────────────────────────────────────────────────

_LLM_OUTPUT_LOG_DIR = os.path.join(os.environ.get("BACKTEST_LOG_DIR", "/app/backtest_logs"), "llm_outputs")


class LLMOutputBody(BaseModel):
    provider: Optional[str] = None
    model: Optional[str] = None
    prompt_hash: Optional[str] = None
    raw_output: str
    logged_at: Optional[str] = None


@app.post("/llm/outputs", response_class=JSONResponse)
def api_llm_output_save(body: LLMOutputBody):
    """Save a raw LLM output log. Unprotected — called internally by llm_utils."""
    import uuid as _uuid
    output_id = _uuid.uuid4().hex
    os.makedirs(_LLM_OUTPUT_LOG_DIR, exist_ok=True)
    path = os.path.join(_LLM_OUTPUT_LOG_DIR, f"{output_id}.txt")
    lines = []
    if body.logged_at:
        lines.append(f"logged_at: {body.logged_at}")
    if body.provider:
        lines.append(f"provider: {body.provider}")
    if body.model:
        lines.append(f"model: {body.model}")
    if body.prompt_hash:
        lines.append(f"prompt_hash: {body.prompt_hash}")
    lines.append("")
    lines.append(body.raw_output)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return {"id": output_id}


@app.get("/llm/outputs/{output_id}", response_class=JSONResponse)
def api_llm_output_get(output_id: str):
    """Retrieve a raw LLM output log by ID. Unprotected."""
    import re as _re
    if not _re.match(r"^[a-f0-9]{32}$", output_id):
        raise HTTPException(status_code=400, detail="Invalid output ID")
    path = os.path.join(_LLM_OUTPUT_LOG_DIR, f"{output_id}.txt")
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="LLM output log not found")
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()
    # Parse header lines (key: value) and JSON body into structured response
    result: dict = {"id": output_id}
    lines = content.split("\n")
    json_lines = []
    in_body = False
    for line in lines:
        if in_body:
            json_lines.append(line)
        elif line.strip() == "":
            in_body = True
        elif ": " in line:
            k, _, v = line.partition(": ")
            result[k.strip()] = v.strip()
    raw_json = "\n".join(json_lines).strip()
    if raw_json:
        try:
            result["output"] = json.loads(raw_json)
        except Exception:
            result["output"] = raw_json
    return result


# -- LLM Usage telemetry endpoints --------------------------------------------


@app.get("/llm-usage/summary", response_class=JSONResponse)
def api_llm_usage_summary(
    range: str = "24h",
    conn=Depends(conn_dependency),
    current_user: dict = Depends(get_current_user),
):
    return _llm_usage_summary(range_str=range, conn=conn)


@app.get("/llm-usage/timeseries", response_class=JSONResponse)
def api_llm_usage_timeseries(
    range: str = "24h",
    bucket: str = "hour",
    provider: str = "",
    conn=Depends(conn_dependency),
    current_user: dict = Depends(get_current_user),
):
    return _llm_usage_timeseries(
        range_str=range, bucket=bucket, provider=provider or None, conn=conn,
    )


@app.get("/llm-usage/top-spenders", response_class=JSONResponse)
def api_llm_usage_top_spenders(
    range: str = "24h",
    group_by: str = "model",
    limit: int = 10,
    conn=Depends(conn_dependency),
    current_user: dict = Depends(get_current_user),
):
    return _llm_usage_top_spenders(
        range_str=range, group_by=group_by, limit=limit, conn=conn,
    )


@app.get("/llm-usage/by-backtest", response_class=JSONResponse)
def api_llm_usage_by_backtest(
    range: str = "30d",
    limit: int = 100,
    conn=Depends(conn_dependency),
    current_user: dict = Depends(get_current_user),
):
    """Per-backtest aggregated LLM usage rows for the cost-screen table.

    Returns one row per ``backtest_id`` (untagged calls are excluded —
    they have no backtest to attribute cost to). Sorted by descending
    total cost so the most expensive runs surface first.
    """
    return _llm_usage_by_backtest(range_str=range, limit=limit, conn=conn)


@app.get("/backtests/{backtest_id}/llm-cost", response_class=JSONResponse)
def api_backtest_llm_cost(
    backtest_id: str,
    conn=Depends(conn_dependency),
    current_user: dict = Depends(get_current_user),
):
    """Full LLM cost / token breakdown for one backtest.

    Drives the AI-credits card on the backtest detail view. Returns
    totals + per-model + per-call_site (strategy stage) + per-strategy
    sub-aggregates so the operator can see where the cost came from.
    """
    return _llm_usage_for_backtest(backtest_id=str(backtest_id), conn=conn)


@app.get("/llm-usage/calls", response_class=JSONResponse)
def api_llm_usage_calls(
    limit: int = 50,
    offset: int = 0,
    range: str = "now",
    provider: str = "",
    model: str = "",
    backtest_id: str = "",
    strategy: str = "",
    conn=Depends(conn_dependency),
    current_user: dict = Depends(get_current_user),
):
    import llm_telemetry
    if (
        range == "now"
        and offset == 0
        and not provider
        and not model
        and not backtest_id
        and not strategy
    ):
        # Multi-process fix: the API process ring buffer only sees calls made
        # in this process. Merge persisted rows so active backtests show up
        # after their worker flushes.
        fast_rows = llm_telemetry.get_recent_calls(limit)
        db_rows = _llm_usage_calls_db(
            limit=max(50, int(limit) * 2),
            offset=0,
            range_str="now",
            provider=None,
            model=None,
            backtest_id=None,
            strategy=None,
            conn=conn,
        )
        return _merge_recent_usage_rows(
            limit=limit,
            in_memory_rows=fast_rows,
            db_rows=db_rows,
        )
    return _llm_usage_calls_db(
        limit=limit,
        offset=offset,
        range_str=range,
        provider=provider or None,
        model=model or None,
        backtest_id=backtest_id or None,
        strategy=strategy or None,
        conn=conn,
    )


@app.get("/llm-usage/health", response_class=JSONResponse)
def api_llm_usage_health(current_user: dict = Depends(get_current_user)):
    import llm_telemetry
    return {
        "buffer_depth": llm_telemetry.get_buffer_depth(),
        "last_flush_ts": llm_telemetry._state.get("last_flush_ts", 0),
        "write_errors_24h": llm_telemetry._state.get("write_errors_24h", 0),
    }


def _range_to_ms_window(range_str: str) -> tuple:
    now_ms = int(time.time() * 1000)
    if range_str == "24h":
        return now_ms - 24 * 3600 * 1000, now_ms
    if range_str == "7d":
        return now_ms - 7 * 24 * 3600 * 1000, now_ms
    if range_str == "30d":
        return now_ms - 30 * 24 * 3600 * 1000, now_ms
    return now_ms - 24 * 3600 * 1000, now_ms


def _llm_usage_summary(*, range_str: str, conn) -> dict:
    import llm_telemetry
    start, end = _range_to_ms_window(range_str)
    rows: list = []
    try:
        # Use the `ts` secondary index (created by ensure_llm_usage_tables)
        # so the dashboard stays sub-second at 100K+ rows. The .filter()
        # alternative forces a full table scan.
        rows = list(
            _r_auth.db("IntelliStock").table("LLMUsage")
            .between(start, end, index="ts")
            .run(conn)
        )
    except Exception:
        rows = []

    by_key: dict = {}
    total_tokens = 0
    total_cost = 0.0
    cli_cost_for_max_est = 0.0
    for row in rows:
        key = (row.get("provider"), row.get("model"))
        b = by_key.setdefault(key, {
            "provider": key[0], "model": key[1],
            "calls": 0, "tokens": 0, "cost_usd": 0.0,
        })
        b["calls"] += 1
        tk = int(row.get("input_tokens", 0) or 0) + int(row.get("output_tokens", 0) or 0)
        b["tokens"] += tk
        c = float(row.get("total_cost_usd", 0.0) or 0.0)
        b["cost_usd"] += c
        total_tokens += tk
        total_cost += c
        if row.get("provider") in ("claude-cli", "claude-cli-chat"):
            cli_cost_for_max_est += c

    cli_usage_file = llm_telemetry.probe_local_cli_usage_file()
    last_flush_ts = llm_telemetry._state.get("last_flush_ts", 0)
    last_flush_age_s = max(0, int((int(time.time() * 1000) - last_flush_ts) / 1000)) if last_flush_ts else 0
    return {
        "period_start": start,
        "period_end": end,
        "total_calls": len(rows),
        "total_tokens": total_tokens,
        "total_cost_usd": round(total_cost, 6),
        "by_provider": list(by_key.values()),
        "max_plan_estimate_usd": round(cli_cost_for_max_est, 6) if cli_cost_for_max_est else None,
        "cli_usage_file": cli_usage_file,
        "telemetry_health": {
            "buffer_depth": llm_telemetry.get_buffer_depth(),
            "last_flush_age_s": last_flush_age_s,
            "write_errors_24h": llm_telemetry._state.get("write_errors_24h", 0),
        },
    }


def _llm_usage_timeseries(*, range_str, bucket, provider, conn) -> list:
    start, end = _range_to_ms_window(range_str)
    bucket_ms = 3600_000 if bucket == "hour" else 86400_000
    try:
        rows = list(
            _r_auth.db("IntelliStock").table("LLMUsage")
            .between(start, end, index="ts")
            .run(conn)
        )
    except Exception:
        rows = []
    if provider:
        rows = [x for x in rows if x.get("provider") == provider]
    by_key: dict = {}
    for row in rows:
        bucket_start = (int(row.get("ts", 0)) // bucket_ms) * bucket_ms
        key = (bucket_start, row.get("provider"), row.get("model"))
        b = by_key.setdefault(key, {
            "bucket_start_ts": bucket_start,
            "provider": key[1],
            "model": key[2],
            "tokens": 0,
            "cost_usd": 0.0,
        })
        b["tokens"] += int(row.get("input_tokens", 0) or 0) + int(row.get("output_tokens", 0) or 0)
        b["cost_usd"] += float(row.get("total_cost_usd", 0.0) or 0.0)
    return sorted(by_key.values(), key=lambda x: x["bucket_start_ts"])


def _llm_usage_top_spenders(*, range_str, group_by, limit, conn) -> list:
    start, end = _range_to_ms_window(range_str)
    try:
        rows = list(
            _r_auth.db("IntelliStock").table("LLMUsage")
            .between(start, end, index="ts")
            .run(conn)
        )
    except Exception:
        rows = []
    key_field = group_by if group_by in ("model", "strategy", "call_site", "provider") else "model"
    by_key: dict = {}
    for row in rows:
        key = row.get(key_field) or "(unset)"
        b = by_key.setdefault(key, {"key": key, "calls": 0, "tokens": 0, "cost_usd": 0.0})
        b["calls"] += 1
        b["tokens"] += int(row.get("input_tokens", 0) or 0) + int(row.get("output_tokens", 0) or 0)
        b["cost_usd"] += float(row.get("total_cost_usd", 0.0) or 0.0)
    out = sorted(by_key.values(), key=lambda x: x["cost_usd"], reverse=True)
    return out[: max(1, int(limit))]


def _llm_usage_by_backtest(*, range_str: str, limit: int, conn) -> list:
    """Aggregate LLMUsage rows within ``range_str`` into per-run buckets.

    Rows with a non-empty ``backtest_id`` group by ``backtest_id`` and emit
    ``kind="backtest"`` (``display_label="Backtest #<id>"``). Rows with a null
    or empty ``backtest_id`` group by ``instance_id`` and emit ``kind="live"``
    (``display_label="Live: <instance_id>"``). Truly-untagged rows (no
    backtest AND no instance) are dropped so the table doesn't sprout
    an "(unset)" pseudo-row.

    Implementation: scan the time-bounded window via the ``ts`` index,
    group in-memory, sort by cost desc, cap at ``limit``.
    """
    start, end = _range_to_ms_window(range_str)
    try:
        rows = list(
            _r_auth.db("IntelliStock").table("LLMUsage")
            .between(start, end, index="ts")
            .run(conn)
        )
    except Exception:
        rows = []

    buckets: dict = {}

    def _get_bucket(kind: str, key: str, instance_id):
        bk = (kind, key)
        if bk not in buckets:
            label = f"Backtest #{key}" if kind == "backtest" else f"Live: {key}"
            buckets[bk] = {
                "kind": kind,
                "key": key,
                # back-compat with prior shape: backtest rows still have
                # `backtest_id`; live rows have it as None.
                "backtest_id": key if kind == "backtest" else None,
                "instance_id": instance_id,
                "display_label": label,
                "calls": 0,
                "tokens": 0,
                "cost_usd": 0.0,
                "first_ts": None,
                "last_ts": None,
                "ok_calls": 0,
                "failed_calls": 0,
            }
        return buckets[bk]

    for row in rows:
        bt_id = row.get("backtest_id")
        inst_id = row.get("instance_id")
        if bt_id is None or bt_id == "":
            # Live mode — bucket by instance_id. Rows with no instance
            # either (e.g. /llm/test smoke probes outside any run) are
            # dropped to avoid an "(unset)" pseudo-row.
            if inst_id is None or inst_id == "":
                continue
            b = _get_bucket("live", str(inst_id), str(inst_id))
        else:
            b = _get_bucket("backtest", str(bt_id), str(inst_id) if inst_id else None)

        b["calls"] += 1
        b["tokens"] += int(row.get("input_tokens", 0) or 0) + int(row.get("output_tokens", 0) or 0)
        b["cost_usd"] += float(row.get("total_cost_usd", 0.0) or 0.0)
        ts = int(row.get("ts", 0) or 0)
        if b["first_ts"] is None or ts < b["first_ts"]:
            b["first_ts"] = ts
        if b["last_ts"] is None or ts > b["last_ts"]:
            b["last_ts"] = ts
        if row.get("ok"):
            b["ok_calls"] += 1
        else:
            b["failed_calls"] += 1
        # Prefer non-empty instance_id from any row in the group.
        if not b.get("instance_id") and inst_id:
            b["instance_id"] = str(inst_id)

    out = sorted(buckets.values(), key=lambda x: x["cost_usd"], reverse=True)
    return out[: max(1, int(limit or 100))]


def _llm_usage_for_backtest(*, backtest_id: str, conn) -> dict:
    """Per-backtest cost detail. Uses the ``backtest_id`` secondary index
    so this stays O(rows-for-this-backtest), not full table scan."""
    rows: list = []
    try:
        rows = list(
            _r_auth.db("IntelliStock").table("LLMUsage")
            .get_all(backtest_id, index="backtest_id")
            .run(conn)
        )
    except Exception:
        rows = []
    total_calls = len(rows)
    total_tokens = 0
    total_input_tokens = 0
    total_output_tokens = 0
    total_reasoning_tokens = 0
    total_cache_read_tokens = 0
    total_cost = 0.0
    first_ts: Optional[int] = None
    last_ts: Optional[int] = None
    ok_calls = 0
    failed_calls = 0
    by_model: dict = {}
    by_call_site: dict = {}
    by_strategy: dict = {}
    by_provider: dict = {}
    for row in rows:
        in_t = int(row.get("input_tokens", 0) or 0)
        out_t = int(row.get("output_tokens", 0) or 0)
        reason_t = int(row.get("reasoning_tokens", 0) or 0)
        cache_t = int(row.get("cache_read_input_tokens", 0) or 0)
        cost = float(row.get("total_cost_usd", 0.0) or 0.0)
        total_input_tokens += in_t
        total_output_tokens += out_t
        total_reasoning_tokens += reason_t
        total_cache_read_tokens += cache_t
        total_tokens += in_t + out_t
        total_cost += cost
        ts = int(row.get("ts", 0) or 0)
        if first_ts is None or ts < first_ts:
            first_ts = ts
        if last_ts is None or ts > last_ts:
            last_ts = ts
        if row.get("ok"):
            ok_calls += 1
        else:
            failed_calls += 1

        def _bump(bucket: dict, key: Any) -> None:
            k = str(key or "(unset)")
            b = bucket.setdefault(k, {"key": k, "calls": 0, "tokens": 0, "cost_usd": 0.0})
            b["calls"] += 1
            b["tokens"] += in_t + out_t
            b["cost_usd"] += cost

        _bump(by_model, row.get("model"))
        _bump(by_call_site, row.get("call_site"))
        _bump(by_strategy, row.get("strategy"))
        _bump(by_provider, row.get("provider"))

    def _sort(bucket: dict) -> list:
        return sorted(bucket.values(), key=lambda x: x["cost_usd"], reverse=True)

    return {
        "backtest_id": str(backtest_id),
        "total_calls": total_calls,
        "ok_calls": ok_calls,
        "failed_calls": failed_calls,
        "total_input_tokens": total_input_tokens,
        "total_output_tokens": total_output_tokens,
        "total_reasoning_tokens": total_reasoning_tokens,
        "total_cache_read_tokens": total_cache_read_tokens,
        "total_tokens": total_tokens,
        "total_cost_usd": round(total_cost, 6),
        "first_ts": first_ts,
        "last_ts": last_ts,
        "by_provider": _sort(by_provider),
        "by_model": _sort(by_model),
        "by_call_site": _sort(by_call_site),
        "by_strategy": _sort(by_strategy),
    }


def _merge_recent_usage_rows(*, limit, in_memory_rows, db_rows) -> list:
    merged: dict = {}
    for row in list(in_memory_rows or []) + list(db_rows or []):
        if not isinstance(row, dict):
            continue
        row_id = row.get("id") or (
            f"{row.get('ts')}:{row.get('provider')}:{row.get('model')}:"
            f"{row.get('strategy')}:{row.get('call_site')}"
        )
        existing = merged.get(row_id)
        if existing is None or int(row.get("ts", 0) or 0) > int(existing.get("ts", 0) or 0):
            merged[row_id] = row
    out = sorted(
        merged.values(),
        key=lambda item: int(item.get("ts", 0) or 0),
        reverse=True,
    )
    return out[: max(0, int(limit))]


def _llm_usage_calls_db(*, limit, offset, range_str, provider, model,
                        backtest_id, strategy, conn) -> list:
    start, end = _range_to_ms_window(range_str if range_str != "now" else "24h")
    try:
        # Push the time-range + filters + ordering INTO the query so we
        # never materialize the whole window. between(index="ts") uses the
        # secondary index; order_by(index=r.desc("ts")) reverses the scan.
        q = _r_auth.db("IntelliStock").table("LLMUsage").between(
            start, end, index="ts"
        ).order_by(index=_r_auth.desc("ts"))
        if provider:
            q = q.filter({"provider": provider})
        if model:
            q = q.filter({"model": model})
        if backtest_id:
            q = q.filter({"backtest_id": backtest_id})
        if strategy:
            q = q.filter({"strategy": strategy})
        rows = list(
            q.skip(int(offset))
            .limit(int(limit))
            .run(conn)
        )
    except Exception:
        rows = []
    return rows


# Host/port from env (used when running this module; uvicorn CLI can override with --host/--port)
API_HOST = os.environ.get("API_HOST", "0.0.0.0")
API_PORT = int(os.environ.get("API_PORT", "8000"))
# Full URL for other services (e.g. frontend, backtest engine) to call the API. Set in .env.
API_URL = os.environ.get("API_URL", "").strip() or ("http://%s:%d" % (API_HOST if API_HOST != "0.0.0.0" else "localhost", API_PORT))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=API_HOST, port=API_PORT)
