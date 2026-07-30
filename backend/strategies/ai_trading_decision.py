# INTELLISTOCK_SCHEMA: {"strategy": "ai_trading_decision", "weight": 0.5, "execution_position": 100, "decision_phase": "post", "conditions": {}, "config": {"llm_provider": "gemini", "llm_api_key": "<optional>", "llm_model": "gemini-2.0-flash-exp", "openai_base_url": "<optional>", "azure_openai_api_key": "<optional>", "azure_openai_endpoint": "<optional>", "azure_openai_api_version": "2024-10-21"}}
# INTELLISTOCK_DESCRIPTION: Post-decision AI overlay. Given each strategy's vote, weight, reason, the current bot decision, and last 30 days price history, the AI decides whether to execute that trade or override to hold/buy/sell. This final decision is used for execution.
# DIFFICULTY: 3
"""
AI Trading Decision strategy. Post-decision (decision_phase: "post").
- Receives: list of strategies that ran (name, weight, decision, reason), the aggregated decision (1/0/-1), and last 30 days price history for the ticker.
- Calls an LLM to decide if the proposed trade is sound; returns final decision 1 (buy), 0 (hold), or -1 (sell) which overrides the bot's decision for execution.
- Implements get_final_decision(symbol, current_decision, strategy_summary, price_history_symbol, config, ...).
"""

from __future__ import annotations

import json
import os

try:
    import sys
    _broker_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _broker_dir not in sys.path:
        sys.path.insert(0, _broker_dir)
    from intellistock_logger import intellistock_logger
    def _log(msg, color="white"):
        intellistock_logger.log(msg, color, service="AITradingDecision")
except Exception:
    def _log(msg, color="white"):
        print(f"[AITradingDecision] {msg}")


def _call_llm(
    provider: str,
    api_key: str,
    model: str,
    prompt: str,
    max_tokens: int = 256,
    provider_config: dict | None = None,
) -> str:
    """Call LLM. Returns response text or empty string."""
    if not api_key and (provider or "").strip().lower() not in ("claude-cli", "codex-cli"):
        return ""
    try:
        from llm_utils import call_llm_by_provider  # noqa: F401  (kept for back-compat references)
        from llm_utils import _call_llm_with_critical_guard as _call_llm_guarded
        provider = (provider or "gemini").strip().lower()
        resolved_provider_config = dict(provider_config or {})
        if provider == "azure":
            endpoint = (
                str(resolved_provider_config.get("azure_endpoint") or "").strip()
                or os.environ.get("AI_TRADING_DECISION_AZURE_OPENAI_ENDPOINT", "").strip()
                or os.environ.get("AZURE_OPENAI_ENDPOINT", "").strip()
            )
            api_version = (
                str(resolved_provider_config.get("api_version") or "").strip()
                or os.environ.get("AI_TRADING_DECISION_AZURE_OPENAI_API_VERSION", "").strip()
                or os.environ.get("OPENAI_API_VERSION", "").strip()
                or "2024-10-21"
            )
            resolved_provider_config = {"azure_endpoint": endpoint, "api_version": api_version}
        elif provider == "openai":
            base_url = (
                str(resolved_provider_config.get("base_url") or "").strip()
                or os.environ.get("AI_TRADING_DECISION_OPENAI_BASE_URL", "").strip()
                or os.environ.get("OPENAI_BASE_URL", "").strip()
            )
            if base_url:
                resolved_provider_config = {"base_url": base_url}
        # Route through critical-guard wrapper so persistent auth/5xx failures
        # auto-abort the backtest (or halt live trading) after 3 retries.
        # TODO: thread `config` into _call_llm() so we can populate
        # backtest_id/instance_id in attribution_keys for telemetry.
        out = _call_llm_guarded(
            provider,
            api_key,
            model,
            prompt,
            attribution_keys={
                "call_site": "ai_trading_decision.get_final_decision",
            },
            # Live decision path: 180s x 3 attempts = 540s PER SYMBOL, serially.
        # A timeout here is indistinguishable from "no override", so it silently
        # became "keep current decision" after nine minutes of blocking.
        timeout_sec=int(os.environ.get("AI_TRADING_DECISION_LLM_TIMEOUT_SEC", "30") or 30),
        retries=1,
        max_output_tokens=max_tokens,
            provider_config=resolved_provider_config,
        )
        if not (out and out.strip()):
            _log(f"LLM returned empty response (provider={provider} model={model})", "yellow")
        return out or ""
    except Exception as e:
        _log(f"LLM call error: {type(e).__name__}: {e}", "yellow")
        return ""


def _format_price_history(bars: list) -> str:
    """Format last 30 bars for prompt: date/seq, close, optional o,h,l,v."""
    if not bars:
        return "No price history provided."
    lines = []
    for i, b in enumerate(bars[-30:]):
        t = b.get("t") or b.get("date") or f"Bar_{i+1}"
        c = b.get("c") or b.get("close")
        o = b.get("o") or b.get("open")
        h = b.get("h") or b.get("high")
        l = b.get("l") or b.get("low")
        v = b.get("v") or b.get("volume")
        if c is not None:
            line = f"  {t}: close={c}"
            if o is not None:
                line += f" open={o}"
            if h is not None:
                line += f" high={h}"
            if l is not None:
                line += f" low={l}"
            if v is not None:
                line += f" vol={v}"
            lines.append(line)
    return "\n".join(lines) if lines else "No price history provided."


class AiTradingDecision:
    """Post-decision strategy: uses LLM to approve or override the aggregated trading decision."""

    def get_final_decision(
        self,
        symbol: str,
        current_decision: int,
        strategy_summary: list,
        price_history_symbol: list,
        config: dict,
        portfolio_emulator=None,
        prices=None,
    ):
        """
        Given the current decision (1=buy, 0=hold, -1=sell), strategy summary (each strategy's name, weight, decision, reason),
        and last 30 days of price history, return the final decision: 1 (buy), 0 (hold), or -1 (sell).
        Return None to leave the current decision unchanged (e.g. on LLM error).
        """
        action = {1: "buy", 0: "hold", -1: "sell"}.get(current_decision, "hold")
        n_strategies = len(strategy_summary) if strategy_summary else 0
        n_bars = len(price_history_symbol) if price_history_symbol else 0
        _log(f"get_final_decision: symbol={symbol} current_decision={current_decision} ({action}) strategies={n_strategies} price_bars={n_bars}", "white")

        provider = ((config.get("llm_provider") or "").strip() or os.environ.get("AI_TRADING_DECISION_PROVIDER", "gemini").strip()).lower()
        if provider not in (
            "gemini", "deepseek", "openai", "azure",
            "nvidia", "anthropic", "claude-cli", "codex-cli",
        ):
            provider = "gemini"
        api_key = (
            (config.get("azure_openai_api_key") or "").strip()
            or (config.get("llm_api_key") or "").strip()
            or os.environ.get("AI_TRADING_DECISION_API_KEY", "")
        )
        if not api_key:
            if provider == "deepseek":
                api_key = os.environ.get("DEEPSEEK_API_KEY", "")
            elif provider == "openai":
                api_key = os.environ.get("OPENAI_API_KEY", "")
            elif provider == "azure":
                api_key = os.environ.get("AZURE_OPENAI_API_KEY", "")
            elif provider == "claude-cli":
                api_key = ""  # claude-cli authenticates via the host's ~/.claude login
            elif provider == "codex-cli":
                api_key = ""  # codex-cli authenticates via OpenAI device-code login
            else:
                api_key = os.environ.get("GEMINI_API_KEY", "")
        model = (config.get("llm_model") or "").strip() or os.environ.get("AI_TRADING_DECISION_MODEL", "")
        if not model:
            if provider == "deepseek":
                model = "deepseek-chat"
            elif provider == "openai":
                model = "gpt-4.1-mini"
            elif provider == "azure":
                model = (os.environ.get("AZURE_OPENAI_DEPLOYMENT") or os.environ.get("AZURE_OPENAI_MODEL") or "gpt-4.1-mini").strip()
            elif provider == "claude-cli":
                # Use the same default as graph_nexus / earnings / ml_news
                # so a user who promotes a model to all-strategies via the
                # default doesn't get a quietly-different choice here.
                model = (os.environ.get("AI_TRADING_DECISION_MODEL") or "claude-sonnet-4-6").strip() or "claude-sonnet-4-6"
            elif provider == "codex-cli":
                model = (os.environ.get("AI_TRADING_DECISION_MODEL") or "gpt-5-codex").strip() or "gpt-5-codex"
            else:
                model = "gemini-2.0-flash-exp"
        if provider not in ("claude-cli", "codex-cli") and not api_key:
            _log("No LLM API key; skipping final decision override (keeping current decision).", "yellow")
            return None
        provider_config = {}
        if provider == "azure":
            endpoint = (
                (config.get("azure_openai_endpoint") or "").strip()
                or os.environ.get("AI_TRADING_DECISION_AZURE_OPENAI_ENDPOINT", "").strip()
                or os.environ.get("AZURE_OPENAI_ENDPOINT", "").strip()
            )
            api_version = (
                (config.get("azure_openai_api_version") or "").strip()
                or os.environ.get("AI_TRADING_DECISION_AZURE_OPENAI_API_VERSION", "").strip()
                or os.environ.get("OPENAI_API_VERSION", "").strip()
                or "2024-10-21"
            )
            provider_config = {"azure_endpoint": endpoint, "api_version": api_version}
        elif provider == "openai":
            base_url = (
                (config.get("openai_base_url") or "").strip()
                or os.environ.get("AI_TRADING_DECISION_OPENAI_BASE_URL", "").strip()
                or os.environ.get("OPENAI_BASE_URL", "").strip()
            )
            if base_url:
                provider_config = {"base_url": base_url}
        elif provider == "claude-cli":
            cli_path = (config.get("cli_path") or "claude").strip() or "claude"
            extra_args = config.get("extra_args") or ""
            provider_config = {"cli_path": cli_path}
            if extra_args:
                provider_config["extra_args"] = extra_args
        elif provider == "codex-cli":
            cli_path = (config.get("cli_path") or "codex").strip() or "codex"
            extra_args = config.get("extra_args") or ""
            provider_config = {"cli_path": cli_path}
            if extra_args:
                provider_config["extra_args"] = extra_args
            reasoning = (config.get("llm_reasoning_effort") or "").strip()
            if reasoning:
                provider_config["reasoning_effort"] = reasoning
        _log(f"LLM config: provider={provider} model={model}", "white")
        summary_lines = []
        for s in (strategy_summary or []):
            name = s.get("strategy") or "?"
            w = s.get("weight")
            dec = s.get("decision")
            reason = (s.get("reason") or "").strip()
            dec_str = {1: "buy", 0: "hold", -1: "sell"}.get(dec, "?")
            summary_lines.append(f"  - {name}: weight={w}, decision={dec_str}" + (f", reason: {reason}" if reason else ""))
        summary_text = "\n".join(summary_lines) if summary_lines else "No strategy summary."
        vote_counts = {}
        for s in (strategy_summary or []):
            dec = s.get("decision")
            key = {1: "buy", 0: "hold", -1: "sell"}.get(dec, "?")
            vote_counts[key] = vote_counts.get(key, 0) + 1
        _log(f"Strategy votes for {symbol}: {vote_counts}", "white")
        price_text = _format_price_history(price_history_symbol or [])
        prompt = f"""You are a trading risk overlay. The bot has aggregated several strategy signals into a single decision for symbol {symbol}.

Current aggregated decision: {action} (1=buy, 0=hold, -1=sell).

Strategy breakdown (each strategy's weight, decision, and reason):
{summary_text}

Last 30 periods of price history for {symbol}:
{price_text}

Based on the strategy reasons and price context, is the current decision ({action}) a good one? Reply with ONLY a single number: 1 to execute buy, 0 to hold (no trade), or -1 to execute sell. No explanation, just the number."""
        _log(f"Calling LLM for {symbol} (current={action})...", "white")
        # Use enough tokens for reasoner models (e.g. deepseek-reasoner) that output CoT before the final number
        resp = _call_llm(provider, api_key, model, prompt, max_tokens=512, provider_config=provider_config)
        if not resp:
            _log(f"No LLM response for {symbol}; keeping current decision ({action}).", "yellow")
            return None
        resp = resp.strip()
        _log(f"LLM raw response for {symbol}: {resp[:80]}{'...' if len(resp) > 80 else ''}", "white")
        # Parse 1/0/-1: strip punctuation (e.g. "0."), prefer *last* occurrence (reasoner concludes at end)
        candidates = []
        for part in resp.replace(",", " ").split():
            clean = part.strip(".,;:)!?\"").strip()
            if clean == "-1":
                candidates.append(-1)
            else:
                try:
                    n = int(clean)
                    if n in (1, 0, -1):
                        candidates.append(n)
                except ValueError:
                    continue
        if candidates:
            n = candidates[-1]
            _log(f"AI final decision for {symbol}: {n} ({'buy' if n == 1 else 'hold' if n == 0 else 'sell'}) (was {action})", "cyan")
            return (n, None, None, f"AI finalized decision: {n}")
        # Fallback: try to infer from keywords
        if "sell" in resp.lower() or "-1" in resp:
            _log(f"AI final decision for {symbol}: -1 (sell) from keyword fallback (was {action})", "cyan")
            return (-1, None, None, "Sell signal generated")
        if "buy" in resp.lower():
            _log(f"AI final decision for {symbol}: 1 (buy) from keyword fallback (was {action})", "cyan")
            return (1, None, None, "Buy signal generated")
        if "hold" in resp.lower():
            _log(f"AI final decision for {symbol}: 0 (hold) from keyword fallback (was {action})", "cyan")
            return (0, None, None, "No clear signal")
        _log(f"Could not parse LLM response for {symbol}; keeping current decision ({action}).", "yellow")
        return None
