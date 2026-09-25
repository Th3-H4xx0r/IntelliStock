"""AI conviction layer, ported from ST ai_analyst.py and wheel_trader.py.

    ai_analyst.py:48-92     SECTOR_ETF, _SECTOR_TO_ETF, _resolve_sector_etf
                            (the sector lookup is injected)
    ai_analyst.py:122-158   days_until_earnings (verbatim), sector_etf_rsi
    ai_analyst.py:163-198   fetch_news_summary -> llm_utils.call_llm_with_web_search
    ai_analyst.py:203-338   analyse: the prompt is ST's byte for byte; the call
                            goes through llm_utils.call_structured_llm_by_provider
                            with a JSON schema instead of the Anthropic SDK
    ai_analyst.py:343-384   _handle_result's checks; the pending_trades.json
                            write became a SwingSignals row (the caller's job)
    wheel_trader.py:958-1096 score_candidate and _fetch_news

The model is the one the operator links (`conviction_llm_model_id`, resolved
by model_resolver into `conviction_llm_provider/model/api_key`; spec §8).
Thresholds are re-applied in code whatever the model says. One deviation: a
score outside 0-100 is treated as a failed call, never as a verdict.

key_risks is a tuple on the wire models and a list in every result: llm_utils
rejects a reply whose only list is empty as a "skeleton" (the raw-JSON path
that gpt-5, gpt-oss and kimi models always take), and ST's prompt asks for an
empty list when a trade has no specific risk. Left a list, the cleanest setups
would fail to score on those providers.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import pandas as pd
import yfinance as yf
from pydantic import BaseModel

from swing_trader import market_data
from swing_trader.constants import (
    AI_APPROVE_THRESHOLD,
    AI_REVIEW_THRESHOLD,
    DAYS_TO_EXPIRY,
    RSI_MAX,
    RSI_MIN,
    WHEEL_APPROVE_THRESHOLD,
    WHEEL_REVIEW_THRESHOLD,
)
from swing_trader.indicators import rsi_last

try:
    from intellistock_logger import intellistock_logger as _ilog  # type: ignore

    def _log(msg, color="white"):
        _ilog.log(str(msg), color, service="SwingAI")
except Exception:  # pragma: no cover - standalone/test import
    def _log(msg, color="white"):
        print(f"[SwingAI] {msg}")

#: Hard bounds on each model call, so one slow provider costs a candidate and
#: never the broker's per-tick watchdog (clock.CANDIDATE_RESERVE_S).
NEWS_TIMEOUT_S = 25
SCORE_TIMEOUT_S = 25

#: Providers the models framework runs without an API key.
_KEYLESS_PROVIDERS = {"claude-cli", "codex-cli", "ollama"}

#: provider -> {key model_resolver injects: key llm_utils reads}.
_ENDPOINT_ALIASES = {
    "azure": {"azure_openai_endpoint": "azure_endpoint",
              "azure_openai_api_version": "api_version"},
    "openai": {"openai_base_url": "base_url"},
    "nvidia": {"nvidia_base_url": "base_url"},
}

# Explicit per-symbol overrides — ETFs/commodities that map to themselves and a
# few majors. Everything NOT here resolves dynamically by GICS sector (R5-01),
# which covers the full S&P 500 instead of this hand-maintained list (was 2.4%).
SECTOR_ETF: dict[str, str] = {
    "XLE":   "XLE",   # Energy (maps to itself)
    "GLD":   "GLD",   # Gold (maps to itself)
    "SPY":   "SPY",   # Broad market (maps to itself)
    "QQQ":   "XLK",   # Nasdaq proxy → tech
    # Defensive universe — used in bear mode (BEAR_REGIME_DAYS reached)
    "XLP":   "XLP",   # Consumer Staples
    "XLU":   "XLU",   # Utilities
    "XLV":   "XLV",   # Health Care
    "SHY":   "SHY",   # Short-term Treasuries
}

# GICS sector (normalized as paper_trader.get_symbol_sector returns:
# lowercase, spaces→underscores) → Select Sector SPDR ETF. This gives sector
# context for ANY symbol in the universe, not just hand-listed ones (R5-01).
_SECTOR_TO_ETF: dict[str, str] = {
    "technology":             "XLK",
    "financial_services":     "XLF",
    "healthcare":             "XLV",
    "consumer_cyclical":      "XLY",
    "consumer_defensive":     "XLP",
    "energy":                 "XLE",
    "industrials":            "XLI",
    "basic_materials":        "XLB",
    "utilities":              "XLU",
    "real_estate":            "XLRE",
    "communication_services": "XLC",
    "broad_market":           "SPY",
    "commodity":              "GLD",
}

#: Every sector ETF resolve_sector_etf can name: the bars a point-in-time
#: (backtest) sector RSI needs.
SECTOR_ETFS = tuple(sorted(set(SECTOR_ETF.values()) | set(_SECTOR_TO_ETF.values())))

#: The calendar days of bars the sector RSI reads, live and point-in-time.
SECTOR_RSI_DAYS = 90

#: Point-in-time mode (ai_gate_in_backtest): what the prompt carries where
#: live has a web-searched news summary, and the note on what is missing.
PIT_NEWS = "unavailable (point-in-time backtest: no news or web lookup)"
PIT_NOTE = ("point-in-time replay: the earnings date and news are not available, so days "
            "to earnings is unknown and the earnings block is not applied.")

SWING_NEWS_PROMPT = (
    "Search for '{symbol} stock news this week' and write "
    "1–2 sentences summarising recent news sentiment. "
    "Prioritise: analyst upgrades/downgrades, earnings beats/misses, "
    "guidance changes, or major corporate events."
)
WHEEL_NEWS_PROMPT = (
    "Search for '{symbol} stock news this week' and write "
    "1–2 sentences summarising recent news sentiment. "
    "Prioritise: analyst upgrades/downgrades, earnings, guidance changes."
)


class SwingConviction(BaseModel):
    """ST's swing JSON (ai_analyst.py:281-288). key_risks is a tuple so an
    empty one is not a skeleton reply (module docstring); _score lists it."""
    conviction_score: float
    recommendation: str = ""
    reasoning: str = ""
    position_size_adjustment: float = 1.0
    key_risks: tuple[str, ...] = ()


class WheelConviction(BaseModel):
    """ST's wheel JSON (wheel_trader.py:1027-1034); key_risks as above."""
    conviction_score: float
    recommendation: str = ""
    reasoning: str = ""
    position_size_contracts: int = 1
    key_risks: tuple[str, ...] = ()


def llm_role_from_config(cfg: dict, prefix: str = "conviction_"):
    """The linked model, read off an already-resolved config the way
    self_learning/roles.py:role_config does. None when nothing usable is
    linked: a missing model must never fall back to an ambient default."""
    cfg = cfg or {}
    provider = str(cfg.get(f"{prefix}llm_provider") or "").strip().lower()
    model = str(cfg.get(f"{prefix}llm_model") or "").strip()
    api_key = str(cfg.get(f"{prefix}llm_api_key") or "")
    if not provider or not model:
        return None
    if not api_key and provider not in _KEYLESS_PROVIDERS:
        return None
    # Every key the resolver injected under the prefix, minus the identity
    # fields and any secret (the key travels as api_key; model_resolver also
    # copies an Azure row's key to azure_openai_api_key).
    provider_config = {
        key[len(prefix):]: value for key, value in cfg.items()
        if isinstance(key, str) and key.startswith(prefix)
        and not key.endswith(("api_key", "llm_model_id", "llm_provider", "llm_model"))
    }
    effort = provider_config.pop("llm_reasoning_effort", None)
    if effort:
        provider_config["reasoning_effort"] = effort
    # model_resolver injects the Models row's endpoint fields under their
    # config names; the dispatcher reads them under these (the mapping
    # graph_nexus_analysis._resolve_role_llm_provider_config_fields applies).
    # Without it an Azure model loses its endpoint to the environment's.
    for source, target in (_ENDPOINT_ALIASES.get(provider) or {}).items():
        if provider_config.get(source) and not provider_config.get(target):
            provider_config[target] = provider_config[source]
    return {"provider": provider, "model": model, "api_key": api_key,
            "provider_config": provider_config}


def resolve_sector_etf(symbol: str, sector_of):
    """Sector ETF for a symbol: explicit override first, else by GICS sector.
    Returns None only when the sector is unknown/unmapped — caller treats that
    as 'unavailable'."""
    explicit = SECTOR_ETF.get(symbol.upper())
    if explicit:
        return explicit
    try:
        return _SECTOR_TO_ETF.get(sector_of(symbol))
    except Exception:
        return None


def days_until_earnings(symbol: str) -> int | None:
    """Calendar days until the next confirmed earnings date, or None."""
    try:
        cal = yf.Ticker(symbol).calendar
        if not cal:
            return None
        raw = cal.get("Earnings Date")
        if raw is None:
            return None
        # raw may be a list of Timestamps or a single Timestamp
        items = raw if hasattr(raw, "__iter__") and not isinstance(raw, str) else [raw]
        today = date.today()
        future = sorted(
            pd.Timestamp(d).date() for d in items
            if pd.Timestamp(d).date() >= today
        )
        return (future[0] - today).days if future else None
    except Exception:
        return None


def sector_etf_rsi(symbol: str, *, sector_of, client):
    """RSI(14) of the sector ETF for this symbol (resolved by GICS sector)."""
    etf = resolve_sector_etf(symbol, sector_of)
    if not etf or client is None:
        # No bars client means unavailable, not a doomed fetch that
        # market_data would retry with a sleep.
        return None
    try:
        # Alpaca bars via market_data (R8-05); 90 calendar days ≈ the old
        # yfinance period="60d" (60 trading days) — EWM RSI is warmup-sensitive.
        df = market_data.get_daily_bars([etf], days=SECTOR_RSI_DAYS, client=client).get(etf)
        if df is None or df.empty:
            return None
        close = df["Close"].squeeze()
        val = rsi_last(close)
        return round(val, 1) if val is not None else None
    except Exception:
        return None


def sector_etf_rsi_before(etf: str, data, as_of):
    """sector_etf_rsi for a backtest: RSI(14) of `etf` over the run's bars in
    `data` dated strictly before the session of `as_of`, across the same
    SECTOR_RSI_DAYS calendar days the live read fetches. No client and no
    network: None when `data` holds no such bars."""
    try:
        frame = market_data.frames_from_engine_bars(
            data, [etf], as_of, lookback_days=SECTOR_RSI_DAYS).get(etf)
        if frame is None or frame.empty:
            return None
        val = rsi_last(frame["Close"])
        return round(val, 1) if val is not None else None
    except Exception:
        return None


def fetch_news_summary(symbol: str, role, *, prompt_template: str = SWING_NEWS_PROMPT,
                       web_search=None) -> str:
    """A 1–2 sentence news sentiment summary from a web-searching model call.
    Never raises; a provider without web search yields ST's fallback text."""
    if role is None:
        return "News unavailable: no conviction model linked"
    try:
        if web_search is None:
            from llm_utils import call_llm_with_web_search as web_search
        text = web_search(role["provider"], role["api_key"], role["model"],
                          prompt_template.format(symbol=symbol),
                          max_output_tokens=300, max_uses=2,
                          timeout_sec=NEWS_TIMEOUT_S,
                          provider_config=role.get("provider_config") or None)
        return (text or "").strip() or "No news summary available."
    except Exception as exc:
        return f"News fetch failed: {exc}"


def build_swing_prompt(*, symbol, entry_price, shares, stop_price, target_price,
                       risk_pct, reward_pct, rr_ratio, rsi, rsi_prev, macd_hist,
                       macd_hist_prev, etf_sym, etf_rsi, earnings_days, news,
                       backtest_note=None) -> str:
    """ai_analyst.py:239-288, byte for byte. `backtest_note` (point-in-time
    mode only) adds one CONTEXT line; without it the prompt is ST's."""
    note = f"\n  Backtest note:   {backtest_note}" if backtest_note else ""
    prompt = f"""You are an AI risk analyst for a swing trading system.
Evaluate this proposed trade and return ONLY a valid JSON object — no text outside it.

TRADE SIGNAL
  Symbol:          {symbol}
  Entry price:     ${entry_price:.2f}
  Shares:          {shares}
  Stop loss:       ${stop_price:.2f}  (risk: −{risk_pct:.1f}%)
  Profit target:   ${target_price:.2f}  (reward: +{reward_pct:.1f}%)
  Risk/reward:     1:{rr_ratio:.2f}

TECHNICAL INDICATORS
  RSI(14):         {rsi:.1f}  (prev: {rsi_prev:.1f}) — {'rising ✓' if rsi > rsi_prev else 'falling ✗'}
  MACD histogram:  {macd_hist:.4f}  (prev: {macd_hist_prev:.4f}) — {'improving ✓' if macd_hist > macd_hist_prev else 'weakening ✗'}

CONTEXT
  Sector ETF:      {etf_sym}  |  Sector RSI(14): {f'{etf_rsi:.1f}' if etf_rsi is not None else 'unavailable'}
  Days to earnings:{f' {earnings_days}' if earnings_days is not None else ' unknown'}
  Recent news:     {news}{note}

UPSTREAM FILTERS ALREADY PASSED
  RSI < 50 and rising (pullback in uptrend), MACD histogram improving, price > SMA(200),
  volume > 20-day avg, SPY regime (>SMA200×1.03), VIX ≤ 25.

SCORING GUIDE
  75–100 → approve   (strong setup, proceed)
  50–74  → review    (flag for human review)
  0–49   → reject    (too risky)

RISK FACTORS THAT SHOULD LOWER SCORE
  • Earnings within 5 days (gap risk)
  • Sector ETF RSI > 65 (overbought sector) or < 35 (sector breakdown) — ONLY when a value is shown above; if sector RSI is unavailable, do NOT treat its absence as a risk
  • Negative news catalyst (downgrade, miss, scandal)

key_risks RULES (critical):
  • Every entry MUST be specific to THIS trade and MUST inform the approve/reject decision.
  • Do NOT list structural constants that are identical on every trade — the
    risk/reward is always 1:1.50 and the stop/target are always −6%/+9% by
    system design, so NEVER cite the R/R ratio or the fixed bracket as a risk.
  • Do NOT cite "sector data unavailable" as a risk — it is a data-coverage note, not a trade weakness.
  • If there are no genuine trade-specific risks, return an empty list rather than filler.

Return exactly this JSON structure:
{{
  "conviction_score": <integer 0–100>,
  "recommendation": <"approve" | "review" | "reject">,
  "reasoning": "<2–4 sentences>",
  "position_size_adjustment": <1.0 | 0.5 | 0.25>,
  "key_risks": ["<trade-specific risk>", ...]
}}"""
    return prompt


def build_wheel_prompt(candidate: dict, news: str, *, rsi_min=RSI_MIN, rsi_max=RSI_MAX,
                       days_to_expiry=DAYS_TO_EXPIRY) -> str:
    """wheel_trader.py:965-1034, byte for byte (the screening range and the
    expiry horizon follow the lane's config; ST's constants are the defaults)."""
    RSI_MIN, RSI_MAX, DAYS_TO_EXPIRY = rsi_min, rsi_max, days_to_expiry  # noqa: N806
    symbol      = candidate["symbol"]
    stock_price = candidate["stock_price"]
    strike      = candidate["strike_price"]
    otm_pct     = candidate["otm_pct"]
    expiry      = candidate["expiry"]
    est_premium = candidate["est_premium"]
    rsi         = candidate["rsi"]
    atr_pct     = candidate["atr_pct"]
    earnings    = candidate["earnings_days"]

    prompt = f"""You are an options income analyst evaluating a cash-secured put opportunity.
Score this trade 0–100 and return ONLY valid JSON — no text outside it.

PROPOSED TRADE
  Strategy:        Sell cash-secured put (Wheel income)
  Symbol:          {symbol}
  Stock price:     ${stock_price:.2f}
  Put strike:      ${strike:.2f}  ({otm_pct:.1f}% OTM)
  Expiry:          {expiry}  (weekly, ~{DAYS_TO_EXPIRY} days)
  Est. premium:    ${est_premium:.2f}/share  (~${est_premium*100:.0f}/contract)
  Est. premium%:   {candidate['est_premium_pct']:.3f}% of stock price

TECHNICALS
  RSI(14):         {rsi:.1f}  (screening range: {RSI_MIN}–{RSI_MAX})
  ATR%:            {atr_pct:.2f}%  (weekly volatility proxy)
  Above SMA(50):   Yes ✓

CONTEXT
  Days to earnings:{f' {earnings}' if earnings is not None else ' unknown'}
  Recent news:     {news}

MARKET CONTEXT — apply penalties as directed above:
  If recent news or your knowledge indicates the broad market (SPY) has declined
  > 2% over the past 3 trading days, apply the SPY weakness penalty.
  If recent news or your knowledge indicates the stock's sector ETF has declined
  > 3% over the past 5 trading days, apply the sector weakness penalty.
  If the stock price is within 5% of the put strike, apply the low margin of
  safety penalty. Current margin: {otm_pct:.1f}% OTM.

SCORING GUIDE
  75–100 → strong put-sell candidate
  50–74  → marginal — flag for human review
  0–49   → reject (too risky or poor premium)

RISK FACTORS THAT SHOULD LOWER SCORE
  • Earnings within 7 days (gap-down risk) — reduce score by 20 points
  • Stock in clear downtrend or recent sharp drop — reduce score by 15 points
  • Very low premium (< 0.5% weekly) — reduce score by 10 points
  • Negative news catalyst (downgrade, miss, scandal) — reduce score by 15 points
  • Sector ETF down > 3% over past 5 days (sector weakness) — reduce score by 15 points
  • SPY down > 2% over past 3 days (broad market weakness) — reduce score by 10 points
  • Stock price within 5% of strike (low margin of safety) — reduce score by 10 points

POSITIVE FACTORS THAT SHOULD RAISE SCORE
  • Stock near support level (put strike near 52-week support)
  • Bullish news or analyst upgrades
  • Premium > 1% weekly (excellent income yield)
  • RSI recovering from oversold (stock likely to stay above strike)

Return exactly this JSON:
{{
  "conviction_score": <integer 0–100>,
  "recommendation": <"approve" | "review" | "reject">,
  "reasoning": "<2–4 sentences explaining the score>",
  "position_size_contracts": <1 | 2 | 3>,
  "key_risks": ["<risk 1>", "<risk 2>"]
}}"""
    return prompt


def _score(prompt: str, output_type, role, llm=None) -> dict:
    """One structured scoring call. Raises when nothing usable came back."""
    if role is None:
        raise RuntimeError("no conviction model linked")
    if llm is None:
        from llm_utils import call_structured_llm_by_provider as llm
    out = llm(role["provider"], role["api_key"], role["model"], prompt, output_type,
              max_output_tokens=600, timeout_sec=SCORE_TIMEOUT_S, retries=0,
              provider_config=role.get("provider_config") or None)
    if out is None:
        raise ValueError("the model returned no valid JSON object")
    if hasattr(out, "model_dump"):
        result = dict(out.model_dump())
    elif isinstance(out, dict):
        result = dict(out)
    else:
        raise ValueError(f"unexpected scoring result {type(out).__name__}")
    result["key_risks"] = list(result.get("key_risks") or [])
    return result


def apply_thresholds(result: dict, approve_threshold, review_threshold) -> dict:
    """ai_analyst.py:306-313 — enforce thresholds regardless of what the model
    said in the text. A score outside 0-100 is a failed call (review focus 3)."""
    score = int(result.get("conviction_score", 0))
    if not 0 <= score <= 100:
        raise ValueError(f"conviction_score {score} is outside 0-100")
    result["conviction_score"] = score
    if score >= approve_threshold:
        result["recommendation"] = "approve"
    elif score >= review_threshold:
        result["recommendation"] = "review"
    else:
        result["recommendation"] = "reject"
    return result


def handle_result(result: dict) -> None:
    """ai_analyst.py:343-384 without the pending_trades.json write (the caller
    records a SwingSignals row). Raises ValueError for a REVIEW whose prices
    fail validation."""
    sym   = result["symbol"]
    score = result["conviction_score"]
    rec   = result["recommendation"]
    adj   = result["position_size_adjustment"]

    if rec == "approve":
        _log(f"  [AI] AUTO-APPROVED  {sym}  score={score}  size_adj={adj}x\n"
             f"       {result['reasoning'][:100]}")
        return

    if rec == "reject":
        _log(f"  [AI] REJECTED       {sym}  score={score}\n"
             f"       {result['reasoning'][:100]}")
        return

    # review — validate prices before recording the pending signal
    entry  = float(result.get("entry_price", 0))
    stop   = float(result.get("stop_price",  0))
    target = float(result.get("target_price", 0))
    if stop >= entry * 0.99:
        raise ValueError(
            f"Price validation failed for {sym}: "
            f"stop_price ${stop:.4f} must be < entry_price ${entry:.4f} × 0.99 "
            f"(got stop/entry = {stop/entry:.4f})"
        )
    if target <= entry * 1.01:
        raise ValueError(
            f"Price validation failed for {sym}: "
            f"target_price ${target:.4f} must be > entry_price ${entry:.4f} × 1.01 "
            f"(got target/entry = {target/entry:.4f})"
        )
    _log(f"  [AI] REVIEW NEEDED  {sym}  score={score}  size_adj={adj}x")


def analyse(signal: dict, *, role, sector_of, bars_client=None, llm=None,
            earnings_fn=None, news_fn=None,
            approve_threshold=AI_APPROVE_THRESHOLD,
            review_threshold=AI_REVIEW_THRESHOLD,
            point_in_time=False, sector_rsi_fn=None) -> dict:
    """ai_analyst.py:203-338 over the linked model. Raises on a failed call,
    an out-of-range score, or a REVIEW whose prices fail the checks; the
    caller skips that candidate and moves on (spec §9 fix 5).

    point_in_time=True (ai_gate_in_backtest) reads no live-only input: no
    earnings lookup (earnings_fn), no web-searched news (news_fn), no live
    bars client (bars_client). The sector ETF RSI comes only from
    `sector_rsi_fn(etf)` (bars dated before the session), else it is
    unavailable, and the prompt says what is missing."""
    if role is None:
        # Before the earnings, bars and news calls a score could never use.
        raise RuntimeError("no conviction model linked")
    symbol         = str(signal["symbol"]).upper()
    rsi            = float(signal["rsi"])
    rsi_prev       = float(signal["rsi_prev"])
    macd_hist      = float(signal["macd_hist"])
    macd_hist_prev = float(signal["macd_hist_prev"])
    entry_price    = float(signal["entry_price"])
    shares         = int(signal["shares"])
    stop_price     = float(signal["stop_price"])
    target_price   = float(signal["target_price"])

    risk_pct   = (entry_price - stop_price) / entry_price * 100
    reward_pct = (target_price - entry_price) / entry_price * 100
    rr_ratio   = reward_pct / risk_pct if risk_pct else 0.0

    # ── Enrichment (runs in serial to avoid rate-limiting) ────────────────
    note = None
    if point_in_time:
        earnings_days = None
        etf_sym = resolve_sector_etf(symbol, sector_of) or "unknown"
        etf_rsi = None
        if sector_rsi_fn is not None and etf_sym != "unknown":
            try:
                etf_rsi = sector_rsi_fn(etf_sym)
            except Exception:
                etf_rsi = None
        news, note = PIT_NEWS, PIT_NOTE
    else:
        earnings_days = (earnings_fn or days_until_earnings)(symbol)
        etf_sym  = resolve_sector_etf(symbol, sector_of) or "unknown"
        etf_rsi  = sector_etf_rsi(symbol, sector_of=sector_of, client=bars_client)
        news = (news_fn or fetch_news_summary)(symbol, role)

    prompt = build_swing_prompt(
        symbol=symbol, entry_price=entry_price, shares=shares, stop_price=stop_price,
        target_price=target_price, risk_pct=risk_pct, reward_pct=reward_pct,
        rr_ratio=rr_ratio, rsi=rsi, rsi_prev=rsi_prev, macd_hist=macd_hist,
        macd_hist_prev=macd_hist_prev, etf_sym=etf_sym, etf_rsi=etf_rsi,
        earnings_days=earnings_days, news=news, backtest_note=note)

    result = apply_thresholds(_score(prompt, SwingConviction, role, llm),
                              approve_threshold, review_threshold)

    # Validate position_size_adjustment
    valid_adj = {1.0, 0.5, 0.25}
    adj = float(result.get("position_size_adjustment", 1.0))
    result["position_size_adjustment"] = adj if adj in valid_adj else 1.0

    # Attach enrichment and identity fields
    result.update({
        "symbol":         symbol,
        "entry_price":    entry_price,
        "shares":         shares,
        "stop_price":     stop_price,
        "target_price":   target_price,
        "risk_pct":       round(risk_pct, 2),
        "reward_pct":     round(reward_pct, 2),
        "rr_ratio":       round(rr_ratio, 2),
        "earnings_days":  earnings_days,
        "sector_etf":     etf_sym,
        "sector_rsi":     etf_rsi,
        "news_summary":   news,
        "timestamp":      datetime.now(timezone.utc).isoformat(),
    })

    handle_result(result)
    return result


def score_candidate(candidate: dict, *, role, llm=None, news_fn=None,
                    approve_threshold=WHEEL_APPROVE_THRESHOLD,
                    review_threshold=WHEEL_REVIEW_THRESHOLD,
                    rsi_min=RSI_MIN, rsi_max=RSI_MAX,
                    days_to_expiry=DAYS_TO_EXPIRY) -> dict:
    """wheel_trader.py:958-1072: score a put-selling candidate 0–100 and return
    a copy enriched with the AI fields. Any failure is ST's REJECT, score 0."""
    symbol = candidate["symbol"]
    if news_fn is None:
        def news_fn(s, r):
            return fetch_news_summary(s, r, prompt_template=WHEEL_NEWS_PROMPT)
    news = news_fn(symbol, role)
    prompt = build_wheel_prompt(candidate, news, rsi_min=rsi_min, rsi_max=rsi_max,
                                days_to_expiry=days_to_expiry)
    try:
        result = _score(prompt, WheelConviction, role, llm)
        score = int(result.get("conviction_score", 0))
        if not 0 <= score <= 100:
            raise ValueError(f"conviction_score {score} is outside 0-100")
        result["conviction_score"] = score
        if score >= approve_threshold:
            result["recommendation"] = "approve"
        elif score >= review_threshold:
            result["recommendation"] = "review"
        else:
            result["recommendation"] = "reject"

        contracts = int(result.get("position_size_contracts", 1))
        result["position_size_contracts"] = max(1, min(contracts, 3))

    except Exception as exc:
        _log(f"  [AI-wheel] ERROR scoring {symbol}: {exc}", "yellow")
        result = {
            "conviction_score":      0,
            "recommendation":        "reject",
            "reasoning":             f"AI scoring failed: {exc}",
            "position_size_contracts": 1,
            "key_risks":             ["AI scoring error"],
        }

    scored = dict(candidate)
    scored.update(result)
    scored["timestamp"] = datetime.now(timezone.utc).isoformat()
    return scored
