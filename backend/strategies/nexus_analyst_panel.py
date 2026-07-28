"""
Adversarial Analyst Panel for the Nexus strategy.
Round 1: Independent parallel analysis. Round 2: Debate with inter-agent views.
Round 3: Moderator synthesis. Accuracy tracked in RethinkDB; weights auto-calibrate.
"""
from __future__ import annotations
import math, os, sys, signal, threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from time import perf_counter, sleep as time_sleep
from typing import Any
from pydantic import BaseModel, ConfigDict, Field


def _rdb_run_safe(query, conn, timeout_sec: float = 5.0):
    """Run a RethinkDB query with a hard wall-clock timeout to prevent hangs on half-open connections.
    Returns the result on success, raises TimeoutError or the original exception on failure.
    On timeout, force-discards the panel connection so the next call creates a fresh one."""
    result_box: list = []
    error_box: list = []
    def _run():
        try:
            result_box.append(query.run(conn))
        except Exception as e:
            error_box.append(e)
    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout=timeout_sec)
    if t.is_alive():
        # The daemon thread is stuck on .run() holding the socket — connection is corrupted.
        # Force-discard so next _get_panel_db_conn() creates a fresh one.
        global _panel_db_conn
        _panel_db_conn = None
        raise TimeoutError(f"RethinkDB query timed out after {timeout_sec}s")
    if error_box:
        raise error_box[0]
    return result_box[0] if result_box else None

_backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

try:
    from llm_utils import (
        call_structured_llm_by_provider,
        _call_structured_llm_with_critical_guard as _scl_guarded,
        normalize_reasoning_effort,
        get_last_structured_llm_call_metadata,
    )
except ImportError:
    from strategies.llm_utils import (  # type: ignore[no-redef]
        call_structured_llm_by_provider,
        _call_structured_llm_with_critical_guard as _scl_guarded,
        normalize_reasoning_effort,
        get_last_structured_llm_call_metadata,
    )

try:
    from llm_telemetry import llm_call_context
except Exception:
    from contextlib import contextmanager
    @contextmanager
    def llm_call_context(**_kwargs):
        yield
try:
    from backend._phase_alpha_helpers import evidence_cache_read_allowed
    from backend.model_evidence import (
        ModelEvidenceContext,
        ModelEvidenceError,
        get_model_evidence_session,
    )
except ImportError:
    from _phase_alpha_helpers import evidence_cache_read_allowed
    from model_evidence import (
        ModelEvidenceContext,
        ModelEvidenceError,
        get_model_evidence_session,
    )
try:
    from strategies.graph_nexus_analysis import (
        _resolve_role_llm_config, _resolve_role_llm_provider_config,
        _to_toon, DB_NAME, _get_nexus_db_conn, _normalize_llm_provider,
    )
except ImportError:
    from graph_nexus_analysis import (  # type: ignore[no-redef]
        _resolve_role_llm_config, _resolve_role_llm_provider_config,
        _to_toon, DB_NAME, _get_nexus_db_conn, _normalize_llm_provider,
    )
try:
    from strategies.graph_nexus_analysis import _log
except ImportError:
    try:
        from graph_nexus_analysis import _log  # type: ignore[no-redef]
    except ImportError:
        def _log(msg, color="white"):  # type: ignore[misc]
            try: print(f"[AnalystPanel] {msg}")
            except Exception: pass
try:
    from strategies.graph_nexus_analysis import _format_stage_elapsed  # type: ignore[no-redef]
except ImportError:
    try: from graph_nexus_analysis import _format_stage_elapsed  # type: ignore[no-redef]
    except ImportError:
        def _format_stage_elapsed(s: float) -> str: return f"{s*1000:.0f}ms" if s < 1 else f"{s:.2f}s"  # type: ignore[misc]
try:
    from rethinkdb import RethinkDB
    _r = RethinkDB()
except Exception:
    _r = None

# --- Constants ---------------------------------------------------------------
PANEL_TABLE = "GraphNexusAnalystPanel"
_analyst_panel_table_ensured = False
_RATING_SCORES = {"strong_buy": 2.0, "buy": 1.0, "hold": 0.0, "sell": -1.0, "strong_sell": -2.0}
_DIR_SCORES = {"bullish": 1.0, "neutral": 0.0, "bearish": -1.0}


def _panel_evidence_context(
    config: dict,
    *,
    round_num: int,
    role_label: str,
    local_sequence: int,
) -> ModelEvidenceContext | None:
    session = get_model_evidence_session()
    if session is None or session.mode == "off":
        return None
    return ModelEvidenceContext(
        decision_at=str((config or {}).get("_date_key") or ""),
        call_site=f"nexus_analyst_panel.round{round_num}",
        role="analyst_panel",
        subject=role_label,
        local_sequence=local_sequence,
    )


def _raise_model_evidence_error(exc: Exception) -> None:
    if isinstance(exc, ModelEvidenceError):
        raise exc

# --- Default agent roster (10 roles) ----------------------------------------
_AGENT_DEFS: list[tuple[str, str, str]] = [
    ("bull_analyst", "optimistic", "Bullish equity analyst. Focus on growth catalysts, momentum, upside surprises. Challenge bears on pessimism."),
    ("bear_analyst", "pessimistic", "Bearish equity analyst. Focus on risks, overvaluation, downside catalysts. Challenge bulls on complacency."),
    ("macro_strategist", "neutral", "Macro strategist. Focus on Fed policy, yields, sector rotation, regime. Provide macro frameworks."),
    ("technical_analyst", "data-driven", "Technical analyst. Focus on price action, support/resistance, momentum, volume. Challenge fundamentals with price data."),
    ("contrarian", "anti-consensus", "Contrarian analyst. Look for crowded trades, mean reversion, overreaction. Systematically challenge the majority."),
    ("risk_manager", "defensive", "Risk manager. Focus on drawdown, correlation, concentration, tail risk. Challenge position sizing."),
    ("sector_specialist", "deep-expertise", "Sector specialist with deep domain expertise. Provide sector knowledge others lack."),
    ("earnings_analyst", "forward-looking", "Earnings analyst. Focus on revenue estimates, guidance, margin trends. Challenge valuations with earnings data."),
    ("geopolitical_analyst", "global", "Geopolitical analyst. Focus on trade policy, sanctions, supply chain, geopolitics. Flag ignored risks."),
    ("sentiment_analyst", "behavioral", "Sentiment analyst. Focus on retail flow, options, social media, fear/greed. Challenge rational views with behavioral data."),
]
DEFAULT_AGENTS: list[dict[str, str]] = [{"role": r, "bias": b, "system": s} for r, b, s in _AGENT_DEFS]

# --- Pydantic response models ------------------------------------------------
_EC = ConfigDict(extra="ignore"); _EP = ConfigDict(extra="ignore", populate_by_name=True)

class _OutlookEntry(BaseModel):
    model_config = _EP; direction: str = Field("neutral", alias="d"); confidence: float = Field(0.5, alias="c")

class _StockPrediction(BaseModel):
    model_config = _EP; ticker: str = Field("", alias="t"); rating: str = Field("hold", alias="r")
    conviction: float = Field(0.5, alias="cv"); rationale: str = Field("", alias="ra")

class _AnalystPanelResponse(BaseModel):
    model_config = _EC
    outlook_1d: _OutlookEntry = Field(default_factory=_OutlookEntry); outlook_3d: _OutlookEntry = Field(default_factory=_OutlookEntry)
    outlook_1w: _OutlookEntry = Field(default_factory=_OutlookEntry); outlook_1m: _OutlookEntry = Field(default_factory=_OutlookEntry)
    stocks: list[_StockPrediction] = Field(default_factory=list); risks: list[str] = Field(default_factory=list); catalysts: list[str] = Field(default_factory=list)

class _DebateResponse(BaseModel):
    model_config = _EC; agreements: list[dict] = Field(default_factory=list); challenges: list[dict] = Field(default_factory=list)
    revised_outlook_1d: _OutlookEntry = Field(default_factory=_OutlookEntry); revised_stocks: list[_StockPrediction] = Field(default_factory=list)
    conviction_change: str = Field("unchanged"); defense: str = Field("")

class _ModeratorResponse(BaseModel):
    model_config = _EC
    consensus_outlook_1d: _OutlookEntry = Field(default_factory=_OutlookEntry); consensus_outlook_3d: _OutlookEntry = Field(default_factory=_OutlookEntry)
    consensus_outlook_1w: _OutlookEntry = Field(default_factory=_OutlookEntry); consensus_outlook_1m: _OutlookEntry = Field(default_factory=_OutlookEntry)
    stock_ratings: list[_StockPrediction] = Field(default_factory=list); agreements: list[str] = Field(default_factory=list)
    disagreements: list[str] = Field(default_factory=list); risk_warnings: list[str] = Field(default_factory=list)

# --- RethinkDB helpers -------------------------------------------------------
def _ensure_analyst_panel_table(conn):
    global _analyst_panel_table_ensured
    if conn is None or _r is None or _analyst_panel_table_ensured: return
    try:
        if PANEL_TABLE not in list(_rdb_run_safe(_r.db(DB_NAME).table_list(), conn, 5)): _rdb_run_safe(_r.db(DB_NAME).table_create(PANEL_TABLE), conn, 5)
        _analyst_panel_table_ensured = True
    except Exception as e: _log(f"Could not ensure analyst panel table: {e}", "yellow")

_panel_db_conn = None
_panel_db_lock = threading.Lock()

def _get_panel_db_conn():
    """Get a DEDICATED RethinkDB connection for the panel — separate from the main strategy's.
    Thread-safe: uses a lock to prevent race conditions from ThreadPoolExecutor workers."""
    global _panel_db_conn
    with _panel_db_lock:
        if _panel_db_conn is not None:
            try:
                if _panel_db_conn.is_open():
                    return _panel_db_conn
            except Exception:
                pass
            _panel_db_conn = None
        if _r is None:
            return None
        try:
            host = os.environ.get("RETHINKDB_HOST", "localhost")
            port = int(os.environ.get("RETHINKDB_PORT", 28015))
            _panel_db_conn = _r.connect(host=host, port=port, timeout=5)
            return _panel_db_conn
        except Exception:
            return None

def _save_round_results(conn, instance_id, date_key, agent_role, round1=None, round2=None,
                        prediction_prices: dict[str, float] | None = None):
    if _r is None: return
    conn = _get_panel_db_conn()
    if conn is None: return
    _ensure_analyst_panel_table(conn)
    doc: dict[str, Any] = {"id": f"{instance_id}_{date_key}_{agent_role}", "instance_id": instance_id,
        "date_key": date_key, "agent_role": agent_role, "outcome_filled": False, "outcome_accuracy": {}}
    if round1 is not None: doc["round1"] = round1
    if round2 is not None: doc["round2"] = round2
    if prediction_prices: doc["_prediction_prices"] = prediction_prices
    try: _rdb_run_safe(_r.db(DB_NAME).table(PANEL_TABLE).insert(doc, conflict="update"), conn, 5)
    except Exception as e: _log(f"Failed to save panel result for {agent_role}: {e}", "yellow")
def _load_agent_memory(conn, instance_id: str, agent_role: str, date_key: str,
                       memory_days: int = 14) -> str:
    if not evidence_cache_read_allowed("analyst_panel"):
        return ""
    if _r is None:
        return ""
    conn = _get_panel_db_conn()
    if conn is None: return ""
    _ensure_analyst_panel_table(conn)
    try:
        cutoff = (datetime.strptime(date_key, "%Y-%m-%d") - timedelta(days=memory_days)).strftime("%Y-%m-%d")
        docs = list(_rdb_run_safe(_r.db(DB_NAME).table(PANEL_TABLE).filter(
            lambda doc: (doc["instance_id"] == instance_id) & (doc["agent_role"] == agent_role)
            & (doc["date_key"].ge(cutoff)) & (doc["date_key"].lt(date_key))
        ).order_by(_r.desc("date_key")).limit(5), conn, 5))
    except Exception:
        return ""
    if not docs:
        return ""
    ct = sum(1 for d in docs for v in (d.get("outcome_accuracy") or {}).values() if v.get("correct"))
    tp = sum(len(d.get("outcome_accuracy") or {}) for d in docs)
    _log(f"PANEL memory: loaded {len(docs)} past predictions for '{agent_role}' (accuracy: {ct/tp:.0%})" if tp else
         f"PANEL memory: loaded {len(docs)} past predictions for '{agent_role}' (no outcomes yet)", "cyan")
    lines = ["YOUR PAST PREDICTIONS:"]
    for d in docs:
        r1, acc = d.get("round1", {}), d.get("outcome_accuracy", {})
        ol = r1.get("outlook_1d", {})
        stk = ", ".join(f"{s.get('t', s.get('ticker','?'))}={s.get('r', s.get('rating','?'))}" for s in (r1.get("stocks") or [])[:5])
        acc_s = f" | accuracy={sum(1 for v in acc.values() if v.get('correct'))}/{len(acc)}" if acc else ""
        lines.append(f"  [{d.get('date_key')}] {ol.get('d', ol.get('direction','?'))}({ol.get('c', ol.get('confidence','?'))}) {stk}{acc_s}")
    return "\n".join(lines)
# --- Outcome tracking --------------------------------------------------------
def fill_analyst_panel_outcomes(conn, instance_id: str, date_key: str,
                                prices: dict[str, float]):
    if not evidence_cache_read_allowed("analyst_panel"):
        return
    if _r is None:
        return
    conn = _get_panel_db_conn()
    if conn is None: return
    _ensure_analyst_panel_table(conn)
    try:
        docs = list(_rdb_run_safe(_r.db(DB_NAME).table(PANEL_TABLE).filter(
            lambda doc: (doc["instance_id"] == instance_id) & (~doc["outcome_filled"]) & (doc["date_key"].lt(date_key))
        ).limit(200), conn, 5))
    except Exception:
        return
    n_filled = 0
    for doc in docs:
        stocks = (doc.get("round1") or {}).get("stocks") or []
        accuracy: dict[str, dict] = {}
        for s in stocks:
            tkr = s.get("t", s.get("ticker", ""))
            price_now = prices.get(tkr)
            if price_now is None or not tkr:
                continue
            rating = s.get("r", s.get("rating", "hold"))
            pred_price = doc.get("_prediction_prices", {}).get(tkr)
            if pred_price and pred_price > 0:
                price_chg = price_now - pred_price
                correct = (_RATING_SCORES.get(rating, 0) > 0) == (price_chg > 0)
            else:
                correct = False
            accuracy[tkr] = {"rating": rating, "correct": correct, "price": price_now, "pred_price": pred_price}
        if accuracy:
            try:
                _rdb_run_safe(_r.db(DB_NAME).table(PANEL_TABLE).get(doc["id"]).update({"outcome_filled": True, "outcome_accuracy": accuracy}), conn, 5)
                n_filled += 1
            except Exception:
                pass
    if n_filled:
        _log(f"PANEL outcomes: filled {n_filled}/{len(docs)} prediction docs with actual prices", "green")
# --- Agent weight computation ------------------------------------------------
def _compute_agent_weights(conn, instance_id: str, date_key: str, agents: list[dict],
                           memory_days: int = 14) -> dict[str, float]:
    default = {a["role"]: 1.0 for a in agents}
    if not evidence_cache_read_allowed("analyst_panel"):
        return default
    if _r is None:
        return default
    conn = _get_panel_db_conn()
    if conn is None: return default
    _ensure_analyst_panel_table(conn)
    cutoff = (datetime.strptime(date_key, "%Y-%m-%d") - timedelta(days=memory_days)).strftime("%Y-%m-%d")
    raw: dict[str, float] = {}
    for agent in agents:
        role = agent["role"]
        try: docs = list(_rdb_run_safe(_r.db(DB_NAME).table(PANEL_TABLE).filter(lambda doc, r=role: (doc["instance_id"] == instance_id) & (doc["agent_role"] == r) & (doc["outcome_filled"]) & (doc["date_key"].ge(cutoff))).limit(30), conn, 5))
        except Exception: docs = []
        if not docs: raw[role] = 0.0; continue
        c = sum(1 for d in docs for v in (d.get("outcome_accuracy") or {}).values() if v.get("correct"))
        t = sum(len(d.get("outcome_accuracy") or {}) for d in docs)
        raw[role] = (c / t) if t > 0 else 0.0
    if not raw or all(v == 0.0 for v in raw.values()):
        return default
    max_s = max(raw.values())
    exp_s = {k: math.exp(v - max_s) for k, v in raw.items()}
    t = sum(exp_s.values()) or 1.0
    n = len(agents)
    return {k: (v / t) * n for k, v in exp_s.items()}
# --- Prompt builders ---------------------------------------------------------
def _build_round1_prompt(agent, news, stocks, memory, prev_consensus, config):
    note = f" Sector: {config.get('analyst_panel_sector_specialist_sector','Technology')}." if agent["role"] == "sector_specialist" else ""
    p = [f"You are the {agent['role'].replace('_',' ')} (bias: {agent.get('bias','neutral')}).{note}",
         f"Date: {config.get('_date_key','today')}", "", "NEWS:", news, "", "STOCKS:", stocks, ""]
    if memory: p += [memory, ""]
    if prev_consensus: p += ["PREV CONSENSUS:", prev_consensus, ""]
    p.append(f"Analyze independently. Rate up to {config.get('analyst_panel_max_stocks',10)} stocks. Return JSON: "
             "outlook_1d/3d/1w/1m (d=bullish/bearish/neutral, c=0-1), stocks (t, r=strong_buy/buy/hold/sell/strong_sell, cv=0-1, ra), risks, catalysts.")
    return "\n".join(p)

def _build_round2_prompt(agent: dict, own_r1: dict, all_r1: dict[str, dict],
                         style: str, config: dict) -> str:
    others = []
    for role, r1 in all_r1.items():
        if role == agent["role"]: continue
        ol = r1.get("outlook_1d", {})
        stk = ", ".join(f"{s.get('t','?')}={s.get('r','?')}" for s in (r1.get("stocks") or [])[:5])
        others.append(f"[{role.replace('_',' ').title()}] {ol.get('d','?')}({ol.get('c','?')}) | {stk}\n  Risks: {'; '.join((r1.get('risks') or [])[:2])}")
    _DEBATE_INSTR = {"adversarial": "Aggressively challenge views you disagree with. Defend or concede with reasoning.",
                     "collaborative": "Build on others' views. Highlight agreements and suggest improvements.",
                     "structured": "Systematically address each agent. State agree/challenge with evidence."}
    own_ol = own_r1.get("outlook_1d", {})
    p = [f"You are the {agent['role'].replace('_',' ')}. ROUND 2 (Debate).", "",
         "OTHER ANALYSTS (Round 1):", "", "\n\n".join(others), "",
         f"YOUR R1: {own_ol.get('d','?')}({own_ol.get('c','?')})", "",
         _DEBATE_INSTR.get(style, "Challenge or support other agents' views with evidence."), "",
         "Return JSON: agreements [{with, on}], challenges [{against, point}], revised_outlook_1d (d, c), "
         "revised_stocks (t, r, cv, ra), conviction_change (increased/decreased/unchanged), defense."]
    return "\n".join(p)
def _build_round3_prompt(r1: dict[str, dict], r2: dict[str, dict],
                         weights: dict[str, float], config: dict) -> str:
    p = ["You are the Moderator synthesizing the analyst panel debate.", "", "ROUND 1 + ROUND 2 REVISIONS:"]
    for role in r1:
        d1, d2, w = r1[role], r2.get(role, {}), weights.get(role, 1.0)
        ol1, ol2 = d1.get("outlook_1d", {}), d2.get("revised_outlook_1d", d1.get("outlook_1d", {}))
        stk = ", ".join(f"{s.get('t','?')}={s.get('r','?')}" for s in (d2.get("revised_stocks") or d1.get("stocks") or [])[:5])
        p.append(f"\n[{role.replace('_',' ').title()}] w={w:.2f} conv={d2.get('conviction_change','unchanged')}")
        p.append(f"  R1:{ol1.get('d','?')}({ol1.get('c','?')}) R2:{ol2.get('d',ol1.get('d','?'))}({ol2.get('c',ol1.get('c','?'))}) | {stk}")
    p += ["", "Weight by accuracy. Favour agents who revised (intellectual honesty).", "",
          "Return JSON: consensus_outlook_1d/3d/1w/1m (d, c), stock_ratings (t, r, cv, ra), agreements, disagreements, risk_warnings."]
    return "\n".join(p)
# --- Skeleton detection (empty/neutral responses) ----------------------------
def _is_skeleton_response(resp) -> bool:
    if not hasattr(resp, "stocks"): return False
    non_neutral = any(
        getattr(getattr(resp, f"outlook_{h}", None), "direction", "neutral") != "neutral"
        for h in ("1d", "3d", "1w", "1m")
    )
    return not resp.stocks and not non_neutral and not getattr(resp, "risks", None) and not getattr(resp, "catalysts", None)

# --- Single-agent LLM call ---------------------------------------------------
def _run_single_agent(sys_prompt: str, user_prompt: str, out_type: type, config: dict,
                      *, role_key: str = "analyst_panel", model_override: str | None = None,
                      timeout: int = 120, round_num: int = 0,
                      agent_role: str = "") -> Any | None:
    t0 = perf_counter()
    provider, api_key, model, _ = _resolve_role_llm_config(config, role_key)
    prov_cfg = _resolve_role_llm_provider_config(config, role_key)
    if model_override: model = model_override
    is_azure = _normalize_llm_provider(provider) == "azure"
    role_label = agent_role or role_key
    n_stocks = len(config.get("_panel_stock_tickers", []))
    # Scoped prompt-hash cache (independent of the global nexus_fast_mode cache).
    # When True, panel calls go through llm_utils' force_cache=True path so they
    # cache regardless of the global _prompt_cache_enabled flag.
    _panel_cache_enabled = bool(config.get("analyst_panel_cache_enabled", False))
    _log(f"PANEL agent '{role_label}' R{round_num}: start | model={model} | stocks={n_stocks}", "cyan")
    # Pass 0 for max_output_tokens → maps to None in llm_utils → uses model default (unlimited).
    # Reasoning models (gpt-5*, o1, o3) use output tokens for BOTH reasoning + content;
    # any hard cap risks reasoning consuming the entire budget, leaving 0 for JSON.
    # Retries kept minimal: retries=1 output_retries=1 http_retries=1 caps worst-case to
    # ~3 HTTP calls x timeout = 180s per agent. With 10 parallel agents, the as_completed
    # deadline of timeout+15s (75s) ensures the round never exceeds ~75s wall clock.
    try:
        with llm_call_context(strategy="NexusAnalystPanel", call_site="main"):
            resp = _scl_guarded(
                provider, api_key, model, user_prompt, output_type=out_type,
                attribution_keys={
                    "backtest_id": (config or {}).get("_telemetry_backtest_id"),
                    "instance_id": (config or {}).get("_telemetry_instance_id"),
                    "call_site": f"nexus_analyst_panel.round{round_num}",
                },
                evidence_context=_panel_evidence_context(
                    config,
                    round_num=round_num,
                    role_label=role_label,
                    local_sequence=0,
                ),
                system_prompt=sys_prompt, max_output_tokens=0,
                timeout_sec=timeout, temperature=0.4, provider_config=prov_cfg,
                retries=1, output_retries=1, http_retries=1,
                prefer_raw_json=is_azure,
                use_prompt_cache=_panel_cache_enabled)
        if _panel_cache_enabled and resp is not None:
            try:
                _meta = get_last_structured_llm_call_metadata()
                if _meta.get("prompt_cache_hit"):
                    _log(f"PANEL cache HIT: {role_label} R{round_num} | model={model}", "cyan")
                elif _meta.get("prompt_cache_stored"):
                    _log(f"PANEL cache STORE: {role_label} R{round_num} | model={model}", "cyan")
                else:
                    _err = _meta.get("prompt_cache_store_error") or "unknown reason"
                    _log(f"PANEL cache MISS+NO-STORE: {role_label} R{round_num} | model={model} | reason={_err}", "yellow")
            except Exception as _ce:
                _log(f"PANEL cache log error: {role_label} R{round_num} | {type(_ce).__name__}: {str(_ce)[:120]}", "yellow")
        # Skeleton detection: if response is empty/neutral, retry once with shorter prompt
        if resp is not None and _is_skeleton_response(resp):
            _log(f"PANEL agent '{role_label}' R{round_num}: skeleton response, retrying with shorter prompt", "yellow")
            short_prompt = user_prompt[:len(user_prompt) * 2 // 3] + "\n\nProvide concrete stock ratings and directional outlook. Do NOT return neutral/empty."
            with llm_call_context(strategy="NexusAnalystPanel", call_site="main"):
                resp = _scl_guarded(
                    provider, api_key, model, short_prompt, output_type=out_type,
                    attribution_keys={
                        "backtest_id": (config or {}).get("_telemetry_backtest_id"),
                        "instance_id": (config or {}).get("_telemetry_instance_id"),
                        "call_site": f"nexus_analyst_panel.round{round_num}.skeleton_retry",
                    },
                    evidence_context=_panel_evidence_context(
                        config,
                        round_num=round_num,
                        role_label=role_label,
                        local_sequence=1,
                    ),
                    system_prompt=sys_prompt, max_output_tokens=0,
                    timeout_sec=timeout, temperature=0.5, provider_config=prov_cfg,
                    retries=0, output_retries=0, http_retries=0, prefer_raw_json=is_azure,
                    use_prompt_cache=_panel_cache_enabled)
            if _panel_cache_enabled and resp is not None:
                try:
                    _meta = get_last_structured_llm_call_metadata()
                    if _meta.get("prompt_cache_hit"):
                        _log(f"PANEL cache HIT (skeleton retry): {role_label} R{round_num} | model={model}", "cyan")
                    elif _meta.get("prompt_cache_stored"):
                        _log(f"PANEL cache STORE (skeleton retry): {role_label} R{round_num} | model={model}", "cyan")
                    else:
                        _err = _meta.get("prompt_cache_store_error") or "unknown reason"
                        _log(f"PANEL cache MISS+NO-STORE (skeleton retry): {role_label} R{round_num} | model={model} | reason={_err}", "yellow")
                except Exception:
                    pass
        elapsed = _format_stage_elapsed(perf_counter() - t0)
        _log(f"PANEL agent '{role_label}' R{round_num}: done in {elapsed} | ok={bool(resp)}", "cyan" if resp else "yellow")
        return resp
    except Exception as e:
        _raise_model_evidence_error(e)
        elapsed = _format_stage_elapsed(perf_counter() - t0)
        _log(f"PANEL agent '{role_label}' R{round_num}: FAILED in {elapsed} | model={model} | error={type(e).__name__}: {str(e)[:200]}", "yellow")
        return None
# --- Round runners -----------------------------------------------------------
def _run_parallel_agents(agents: list[dict], build_fn, out_type: type, config: dict,
                         conn, instance_id: str, date_key: str, max_workers: int,
                         timeout: int, cooldown: float, max_calls: int,
                         save_key: str = "round1",
                         prediction_prices: dict[str, float] | None = None) -> dict[str, dict]:
    results: dict[str, dict] = {}
    used = 0
    round_num = 1 if save_key == "round1" else 2
    def _call(a: dict) -> tuple[str, dict | None]:
        prompt = build_fn(a)
        resp = _run_single_agent(a.get("system", ""), prompt, out_type, config,
                                 timeout=timeout, round_num=round_num, agent_role=a["role"])
        if resp is None: return a["role"], None
        return a["role"], resp.model_dump(by_alias=True) if hasattr(resp, "model_dump") else {}
    # Hard deadline: all agents must finish within timeout + 15s or they're abandoned.
    _deadline = perf_counter() + timeout + 15
    ex = ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(agents))))
    try:
        futures = {}
        for a in agents:
            if used >= max_calls: break
            futures[ex.submit(_call, a)] = a["role"]; used += 1
            if cooldown > 0 and used < len(agents): time_sleep(min(cooldown, 2.0))
        try:
            for fut in as_completed(futures, timeout=timeout + 15):
                remaining = max(0.1, _deadline - perf_counter())
                try:
                    role, data = fut.result(timeout=remaining)
                except Exception as e:
                    _raise_model_evidence_error(e)
                    role = futures[fut]
                    _log(f"PANEL agent '{role}' R{round_num}: FAILED | error={type(e).__name__}: {str(e)[:200]}", "yellow")
                    continue
                if data is not None:
                    results[role] = data
                    kw = {"round1": data} if save_key == "round1" else {"round2": data}
                    pp = prediction_prices if save_key == "round1" else None
                    _save_round_results(conn, instance_id, date_key, role, prediction_prices=pp, **kw)
        except TimeoutError:
            _log(f"PANEL R{round_num}: deadline reached, abandoning remaining agents", "yellow")
    finally:
        # shutdown(wait=False) so we don't block on leaked threads stuck in .run() or LLM calls
        ex.shutdown(wait=False, cancel_futures=True)
    return results

def _run_round1_independent(agents, news, stocks, config, conn, iid, dk, max_w, to, cd, mx, prices=None):
    s1 = perf_counter()
    def build(a):
        mem = _load_agent_memory(conn, iid, a["role"], dk, config.get("analyst_panel_memory_days", 14))
        prev = config.get("_analyst_panel_last_consensus", "")
        return _build_round1_prompt(a, news, stocks, mem, prev, config)
    results = _run_parallel_agents(agents, build, _AnalystPanelResponse, config, conn, iid, dk, max_w, to, cd, mx, "round1",
                                   prediction_prices=prices)
    failed = [a["role"] for a in agents if a["role"] not in results]
    elapsed = _format_stage_elapsed(perf_counter() - s1)
    all_ok = len(failed) == 0
    _log(f"PANEL R1: {len(results)}/{len(agents)} agents succeeded in {elapsed} | failed: {failed if failed else 'none'}",
         "green" if all_ok else "yellow")
    return results

def _run_round2_debate(agents, r1, config, conn, iid, dk, max_w, to, cd, mx):
    s2 = perf_counter()
    style = config.get("analyst_panel_debate_style", "adversarial")
    active = [a for a in agents if a["role"] in r1]
    def build(a):
        return _build_round2_prompt(a, r1.get(a["role"], {}), r1, style, config)
    results = _run_parallel_agents(active, build, _DebateResponse, config, conn, iid, dk, max_w, to, cd, mx, "round2")
    elapsed = _format_stage_elapsed(perf_counter() - s2)
    _log(f"PANEL R2 debate: {len(active)} agents, style={style}, {elapsed}", "green")
    return results

def _run_round3_synthesis(r1, r2, weights, config, timeout):
    s3 = perf_counter()
    prompt = _build_round3_prompt(r1, r2, weights, config)
    mod = (config.get("analyst_panel_moderator_llm_model") or "").strip() or None
    sys_p = "You are the panel Moderator. Synthesize the debate into final consensus. Weight by accuracy and conviction changes."
    result = _run_single_agent(sys_p, prompt, _ModeratorResponse, config,
                               model_override=mod, timeout=timeout, round_num=3, agent_role="moderator")
    if result is None and len(r1) > 3:  # Outer retry with reduced agent data (top 5 by weight)
        _log("PANEL R3: moderator failed, retrying with top-5 agents", "yellow")
        top = sorted(weights, key=weights.get, reverse=True)[:5]
        prompt2 = _build_round3_prompt({k: v for k, v in r1.items() if k in top}, {k: v for k, v in r2.items() if k in top}, {k: v for k, v in weights.items() if k in top}, config)
        result = _run_single_agent(sys_p, prompt2, _ModeratorResponse, config, model_override=mod, timeout=timeout, round_num=3, agent_role="moderator_retry")
    elapsed = _format_stage_elapsed(perf_counter() - s3)
    ol3 = getattr(result, "consensus_outlook_1d", None) if result else None
    direction, conf = (getattr(ol3, "direction", "?"), getattr(ol3, "confidence", 0)) if ol3 else ("?", 0)
    n_sr = len(result.stock_ratings) if result and getattr(result, "stock_ratings", None) else 0
    _log(f"PANEL R3 synthesis: moderator={mod or 'default'} | consensus={direction}({conf:.2f}) | {n_sr} stocks rated | {elapsed}",
         "green" if result else "red")
    return result

# --- Consensus aggregation (fallback) ----------------------------------------
def _aggregate_consensus(r1: dict[str, dict], r2: dict[str, dict],
                         weights: dict[str, float]) -> tuple[dict, dict[str, float]]:
    ds = dw = 0.0; ss: dict[str, list[tuple[float, float]]] = {}
    def _a(role, ol, stocks):
        nonlocal ds, dw
        w = weights.get(role, 1.0)
        _conf = ol.get("c", ol.get("confidence", 0.5))
        ds += _DIR_SCORES.get(ol.get("d", ol.get("direction", "neutral")), 0.0) * float(0.5 if _conf is None else _conf) * w; dw += w
        for s in stocks:
            tkr = s.get("t", s.get("ticker", ""))
            _cv = s.get("cv", s.get("conviction", 0.5))
            if tkr: ss.setdefault(tkr, []).append((_RATING_SCORES.get(s.get("r", s.get("rating", "hold")), 0.0) * float(0.5 if _cv is None else _cv), w))
    for role, d2 in r2.items():
        _a(role, d2.get("revised_outlook_1d", r1.get(role, {}).get("outlook_1d", {})), d2.get("revised_stocks") or r1.get(role, {}).get("stocks") or [])
    for role, d1 in r1.items():
        if role not in r2: _a(role, d1.get("outlook_1d", {}), d1.get("stocks") or [])
    cd = (ds / dw) if dw else 0.0
    return ({"direction": "bullish" if cd > 0.15 else ("bearish" if cd < -0.15 else "neutral"), "confidence": min(abs(cd), 1.0)},
            {t: sum(s * w for s, w in v) / (sum(w for _, w in v) or 1.0) for t, v in ss.items()})

# --- Format consensus for additional_context ---------------------------------
def _format_consensus_context(outlook, adj, sw, mod=None):
    lines = ["ANALYST PANEL CONSENSUS:"]
    if mod:
        ol = getattr(mod, 'consensus_outlook_1d', None)
        if ol:
            lines.append(f"  Market 1d: {getattr(ol, 'direction', '?')}({getattr(ol, 'confidence', 0.5):.2f})")
        if mod and getattr(mod, 'risk_warnings', None):
            lines.append(f"  Risks: {'; '.join(str(r) for r in mod.risk_warnings[:3])}")
        if mod and getattr(mod, 'stock_ratings', None):
            _sr = [getattr(s, 'ticker', '?') + '=' + getattr(s, 'rating', '?') for s in mod.stock_ratings[:8] if s]
            lines.append(f"  Ratings: {', '.join(_sr)}")
    else: lines.append(f"  Market 1d: {outlook.get('direction','neutral')}({outlook.get('confidence',0.5):.2f})")
    top = sorted(adj.items(), key=lambda x: abs(x[1]), reverse=True)[:8]
    if top: lines.append(f"  Adj: {', '.join(f'{t}:{(v or 0):+.2f}' for t, v in top)}")
    return "\n".join(lines)

# --- Entry point -------------------------------------------------------------
def run_analyst_panel(config: dict, news_summary: str, stock_candidates: list[dict],
                      strategy_cache: dict, instance_id: str, date_key: str,
                      prices: dict[str, float] | None = None) -> tuple[str, dict[str, float]]:
    """Run the adversarial analyst panel. Returns (context_string, {ticker: score_adj})."""
    t0 = perf_counter()
    if not config.get("analyst_panel_enabled", False):
        return "", {}
    if (
        evidence_cache_read_allowed("analyst_panel")
        and strategy_cache.get("_analyst_panel_last_run_date") == date_key
    ):
        return (strategy_cache.get("_analyst_panel_last_consensus", ""),
                strategy_cache.get("_analyst_panel_last_adjustments", {}))

    rounds = int(config.get("analyst_panel_rounds", 3))
    max_w = int(config.get("analyst_panel_max_workers", 5))
    to = int(config.get("analyst_panel_timeout_sec", 120))
    cd = float(config.get("analyst_panel_cooldown_seconds", 0))
    max_calls = int(config.get("analyst_panel_max_llm_calls", 25))
    sw = float(config.get("analyst_panel_score_weight", 0.15))
    agents = config.get("analyst_panel_agents") or DEFAULT_AGENTS
    panel_timeout = int(config.get("analyst_panel_total_timeout_sec", to * rounds + 60))

    use_toon = config.get("use_toon_format", True)
    mx_stk = int(config.get("analyst_panel_max_stocks", 10))
    cands = [{"t": c.get("ticker", c.get("symbol", "")),
              "score": round(c.get("raw_score", c.get("score", 0)), 2)}
             for c in stock_candidates[:mx_stk]]
    stock_str = _to_toon(cands, use_toon) if use_toon else str(cands)
    # Store ticker list in config for per-agent logging
    config["_panel_stock_tickers"] = [c["t"] for c in cands if c.get("t")]

    # Log full panel config summary
    _log(f"PANEL config | rounds={rounds} agents={len(agents)} workers={max_w} timeout={to}s "
         f"cooldown={cd}s max_calls={max_calls} weight={sw} panel_timeout={panel_timeout}s "
         f"debate={config.get('analyst_panel_debate_style', 'adversarial')} stocks={len(cands)}", "cyan")

    conn = _get_nexus_db_conn()
    config["_date_key"] = date_key
    if prices and conn:
        try: fill_analyst_panel_outcomes(conn, instance_id, date_key, prices)
        except Exception as e: _log(f"Panel outcome fill error: {e}", "yellow")

    weights = _compute_agent_weights(conn, instance_id, date_key, agents,
                                     config.get("analyst_panel_memory_days", 14))
    strategy_cache["_analyst_panel_agent_weights"] = weights
    used = 0

    # Snapshot current prices for accuracy tracking (compare future price vs today's)
    _pred_prices = {t: float(v) for t, v in (prices or {}).items() if v is not None} if prices else {}
    r1 = _run_round1_independent(agents, news_summary, stock_str, config, conn,
                                 instance_id, date_key, max_w, to, cd, min(len(agents), max_calls),
                                 prices=_pred_prices)
    used += len(r1)

    r2: dict[str, dict] = {}
    moderator: _ModeratorResponse | None = None
    _panel_timed_out = False

    if rounds >= 2 and r1 and used < max_calls and (perf_counter() - t0) < panel_timeout:
        r2 = _run_round2_debate(agents, r1, config, conn, instance_id, date_key,
                                max_w, to, cd, min(len(r1), max_calls - used))
        used += len(r2)
    elif (perf_counter() - t0) >= panel_timeout:
        _panel_timed_out = True
        _log(f"PANEL total timeout reached ({_format_stage_elapsed(perf_counter() - t0)}), skipping R2+R3", "yellow")

    if rounds >= 3 and r1 and used < max_calls and not _panel_timed_out and (perf_counter() - t0) < panel_timeout:
        moderator = _run_round3_synthesis(r1, r2, weights, config, to)
        used += 1
    elif not _panel_timed_out and (perf_counter() - t0) >= panel_timeout:
        _log(f"PANEL total timeout reached ({_format_stage_elapsed(perf_counter() - t0)}), skipping R3", "yellow")

    if moderator is not None:
        _co = getattr(moderator, 'consensus_outlook_1d', None)
        outlook = {"direction": getattr(_co, 'direction', 'neutral') if _co else 'neutral',
                   "confidence": float(getattr(_co, 'confidence', 0.5) or 0.5) if _co else 0.5}
        adjustments: dict[str, float] = {}
        for s in (getattr(moderator, 'stock_ratings', None) or []):
            ticker = getattr(s, 'ticker', None)
            if ticker:
                adjustments[ticker] = _RATING_SCORES.get(getattr(s, 'rating', 'hold'), 0.0) * float(getattr(s, 'conviction', 0.5) or 0.5) * sw
    else:
        src = r2 if r2 else r1
        outlook, raw = _aggregate_consensus(r1, src, weights)
        adjustments = {t: v * sw for t, v in raw.items()}

    # Per-stock rating logging (top 5 by absolute adjustment)
    for ticker, adj in sorted(adjustments.items(), key=lambda x: abs(x[1]), reverse=True)[:5]:
        _log(f"PANEL score adj: {ticker} {adj:+.3f}", "cyan")

    ctx = _format_consensus_context(outlook, adjustments, sw, moderator)
    strategy_cache["_analyst_panel_last_consensus"] = ctx
    strategy_cache["_analyst_panel_last_adjustments"] = adjustments
    strategy_cache["_analyst_panel_last_run_date"] = date_key
    _log(f"ANALYST PANEL done: {used} calls in {_format_stage_elapsed(perf_counter() - t0)} | "
         f"{outlook.get('direction')}({outlook.get('confidence',0):.2f}) | {len(adjustments)} stocks", "cyan")
    return ctx, adjustments
