# INTELLISTOCK_SCHEMA: {"strategy": "strategy_x", "weight": 1.0, "execution_position": 10, "decision_phase": "pre", "execution_scope": "run_once", "conditions": {}, "config": {"strategy_x_enabled": false, "core_bull_symbol": "TQQQ", "core_chop_symbol": "SPY", "core_bear_symbol": "", "core_weight": 0.9, "core_band_pct": 0.05, "core_filter_symbol": "QQQ", "core_filter_ma_bars": 200, "core_vol_bars": 20, "core_vol_gate_mult": 2.25, "core_vol_median_bars": 252, "core_vol_median_min_samples": 60, "core_once_per_session": true, "satellite_pct": 0.0, "satellite_max_names": 6, "min_order_usd": 50.0, "cost_haircut_pct": 0.006, "broker_max_single_position_pct": 0.95, "commodity_pct": 0.0, "commodity_symbols": ["GLD", "SLV", "USO", "UNG", "GDX", "XLE", "DBA", "CPER"], "commodity_max_names": 2, "commodity_mom_bars": 60, "commodity_trend_bars": 100}}
# INTELLISTOCK_DESCRIPTION: Leveraged Nasdaq core (TQQQ) with a de-lever filter to SPY. Direction is NOT predicted — a trend + volatility filter decides only WHETHER to be levered. Replaying this module over 15.7y of real closes (next-bar fills, point-in-time): CAGR 33.97%, maxDD -48.5%, Sharpe 0.88, 99.6x vs SPY's 8.5x, 4 years above +100%. The inverse (SQQQ) leg and the stock satellite DEFAULT OFF because both were measured to destroy it (-4.2% CAGR and -4.0pp). Needs QQQ+TQQQ+SPY in the instance universe and granularity 86400. DIFFICULTY: 2
"""IntelliStock — Strategy X: leveraged core, filtered.

WHAT THIS IS
------------
One position at a time. When the Nasdaq is in an uptrend and volatility is
normal, hold TQQQ at `core_weight` of NAV. Otherwise hold SPY. That is the
entire strategy, and the simplicity is the finding, not a shortcut.

WHY IT IS NOT A "COUNCIL"
-------------------------
The original design scored five voters — price trend, realised vol, an LLM macro
read, LLM news breadth, and a Neo4j graph traversal — into a signed conviction
that chose between TQQQ and SQQQ. A pre-registered study
(`scripts/strategy_x_voter_study.py`) measured all of them against non-overlapping
5-day forward QQQ returns before any of it was written:

    fraction of 5d windows that are UP    0.6045   <- the bar to beat
    above MA200                           0.5827
    trend (repo's own regime rule)        0.5560
    news_breadth (n=84)                   0.6071   CI [0.500, 0.705]
    vol                                   0.4904
    macro_llm                             0.4762   <- worse than a coin flip

Nothing beat "always vote up". Directional timing was cut on that evidence.
`backend/strategy_x.py` carries the full measurement table; read it before
re-adding a voter.

WHAT THE GRAPH AND THE LLM STILL DO
-----------------------------------
Nothing, and this is now measured rather than assumed. The graph score has no
CROSS-SECTIONAL skill either: Spearman IC vs forward returns is negative in all
20 lag x horizon cells over 197,797 observations, never significant, and
orthogonal to momentum (rho -0.026). The "+0.17 IC" that used to justify it is a
publication-timestamp artifact — `date_key` is the UTC publication date and 52%
of ticker-tagged articles publish at or after the 20:00 UTC close; pre-close
articles give -0.015, post-close +0.109. See `scripts/graph_signal_ic_study.py`.

The score is also a constant in practice: across 506,498 stored trade contexts
it takes 3 distinct values, and `confidence` is bimodal with 77% of rows at
exactly 0.0 or 1.0. Ranking on it means ranking on a tie, broken by ticker
spelling.

THE STOCK SLEEVE (graph-ranked) — WIRED, BUT READ THIS
------------------------------------------------------
`satellite_pct > 0` funds a stock sleeve out of the core, ranked by Graph Nexus
conviction. The channel now EXISTS: graph_nexus_analysis publishes
`_nexus_conviction_scores` (its per-candidate `raw_net_score`), the broker pops
that and merges it into the shared `data["conviction_scores"]` map, and this
strategy reads it. Before 2026-08-23 that contract had two readers and zero
writers, so the sleeve was silently inert.

To use it, BOTH strategies go on the same document. Ordering is load-bearing:
graph_nexus_analysis ships `execution_position: 0` and this ships `10`, so Nexus
runs first and its scores are in `data` by the time this reads them. Reverse the
order and the sleeve sees an empty map and quietly holds nothing.

TWO LIMITS THAT ARE NOT FIXED HERE:
  * `raw_net_score` is saturated — 3 distinct values across 506,498 stored trade
    contexts — so the ranking is mostly a tie broken by ticker spelling. Nexus
    now LOGS the distinct-value count each bar, so this is visible rather than
    silent, but the repair is in the scorer, not here.
  * The broker passes `data=None` in LIVE mode (`price_history if mode ==
    MODE_BACKTEST else None`), so this channel is backtest-only. A live sleeve
    needs a separate carrier.

THE COMMODITY SLEEVE
--------------------
`commodity_pct > 0` holds the top-`commodity_max_names` commodity ETFs by 60d
momentum, among those above their own 100d MA, funded out of the core. It is the
one added sleeve that beat its control: against SPY dilution at the SAME weight
it delivers ~3pp better max drawdown at matched return, consistently at 10/15/20%.

It is OFF by default because it trades against the stated objective — at 15% it
costs 4.3pp of CAGR and halves the years above +100%. Turn it on to buy
drawdown, not return.

Today it is momentum-driven, not news-driven. That is deliberate: production
"commodity trends" in graph_nexus are already 20d/60d price momentum on
GLD/SLV/USO/UNG, and the news->commodity bridge does not exist (nothing reads
`affected_commodities`; only ~658 articles in three years name a tradable
commodity). `rank_commodities` is the seam a news signal would plug into.

EXECUTION NOTE — THE 15% CAP
---------------------------
`BROKER_MAX_SINGLE_POSITION_PCT` (env, default 0.15) clips every allocator-lane
buy to 15% of equity in BOTH backtest and live. A 90% core is unreachable
without raising it, which is why `broker_max_single_position_pct` is in this
strategy's config: `backend/engines/backtest_engine.py` reads it from the
instance's strategy document and injects it into that run's container only.
Instances without the key keep the 0.15 default — `alpaca-main` is untouched.

LIVE IS NOT ENABLED BY THIS. `live_risk_state.DEFAULT_MAX_LEVERAGED_FRACTION`
caps SQQQ/TQQQ/SPXU/UPRO/SOXL/SOXS at 10% of equity on the live order path, and
the gate BLOCKS rather than clips. Live deployment needs that addressed
separately and deliberately.

DEFAULT OFF. `strategy_x_enabled` is false, so attaching this changes nothing.
"""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from strategy_x import (  # noqa: E402
    DEFAULTS,
    core_signal,
    pit_daily_closes,
    plan_targets,
    rank_commodities,
    targets_to_orders,
)

# Route through intellistock_logger, NOT print(). The backtest engine runs the
# broker with `detach=False, remove=True` and DISCARDS container stdout on
# success — it only persists the tail in its `except` branch. So a print() goes
# nowhere durable, and the one line that would expose an inert run ("closes=65")
# would be invisible. intellistock_logger fans out to the backtest log buffer
# which becomes BacktestResults.logs, the only log an operator actually reads.
try:
    from intellistock_logger import intellistock_logger as _ilog  # type: ignore

    def _log(msg, color="white"):
        _ilog.log(str(msg), color, service="StrategyX")
except Exception:  # pragma: no cover - standalone/test import
    def _log(msg, color="white"):
        print(f"[StrategyX] {msg}")


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _ny_session(current_time):
    """The NY calendar date of a decision, or "" if it cannot be determined.

    Sessions are the unit this strategy decides in, and NY is the exchange
    calendar — a UTC date would roll over mid-session in the evening.
    """
    from datetime import datetime as _dt, timezone as _tz
    from zoneinfo import ZoneInfo

    ts = current_time
    if not isinstance(ts, _dt):
        return ""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=_tz.utc)
    try:
        return ts.astimezone(ZoneInfo("America/New_York")).date().isoformat()
    except Exception:
        return ""


def _bars_for(data, symbol):
    """The engine hands bars as either {sym: {"bars": [...]}} or {sym: [...]}."""
    if not isinstance(data, dict):
        return []
    entry = data.get(symbol)
    if isinstance(entry, dict):
        return entry.get("bars") or []
    return entry or []


class StrategyX:
    """Run-once strategy. Returns `{symbol: 1|0|-1}` plus the
    `_nexus_position_sizes` channel the broker's run_once lane consumes."""

    def run_once(self, symbols, prices, current_time, config, conditions,
                 data=None, portfolio_emulator=None, strategy_cache=None,
                 time_increment=None, mode=None, **kwargs):
        cfg = {**DEFAULTS, **(config or {})}
        if not _truthy(cfg.get("strategy_x_enabled", False)):
            return {}
        if portfolio_emulator is None:
            return {}

        prices = prices or {}
        cache = strategy_cache if isinstance(strategy_cache, dict) else {}

        try:
            nav = float(portfolio_emulator.get_portfolio_value(prices) or 0.0)
            positions = portfolio_emulator.get_positions() or {}
            cash = float(portfolio_emulator.get_cash() or 0.0)
        except Exception as exc:
            _log(f"StrategyX: portfolio read failed ({type(exc).__name__}) — "
                 "no decision this bar", "yellow")
            return {}
        if nav <= 0:
            return {}

        filt = str(cfg.get("core_filter_symbol", "QQQ") or "QQQ").strip().upper()
        bull = str(cfg.get("core_bull_symbol", "TQQQ") or "TQQQ").strip().upper()
        chop = str(cfg.get("core_chop_symbol", "SPY") or "SPY").strip().upper()
        bear = str(cfg.get("core_bear_symbol", "") or "").strip().upper()
        owned = {s for s in (bull, chop, bear) if s}

        # ── ONE DECISION PER SESSION ──────────────────────────────────────
        # This is a daily strategy: the filter reads daily closes and it was
        # measured at one decision a day. The engine calls run_once on EVERY
        # tick, which at 900s bars is ~60 evaluations per session, and
        # `price > ma` is a bare comparison with no hysteresis — so a tape
        # sitting on its MA would round-trip ~90% of NAV in a 3x ETF dozens of
        # times a day. Decide once per NY session and hold.
        day_key = _ny_session(current_time)
        if cfg.get("core_once_per_session", True) and day_key:
            if cache.get("_sx_last_decision_day") == day_key:
                return {}

        closes = pit_daily_closes(_bars_for(data, filt), current_time)
        sig = core_signal(closes, cfg)

        held_core = ""
        for sym, qty in (positions or {}).items():
            if float(qty or 0) > 0 and str(sym).strip().upper() == bull:
                held_core = bull
                break

        ranked = []
        if float(cfg.get("satellite_pct", 0.0) or 0.0) > 0:
            ranked = self._ranked(cfg, data)
            owned |= set(ranked)

        com_ranked = []
        com_syms = [str(s).strip().upper()
                    for s in (cfg.get("commodity_symbols") or []) if s]
        if float(cfg.get("commodity_pct", 0.0) or 0.0) > 0 and com_syms:
            com_closes = {s: pit_daily_closes(_bars_for(data, s), current_time)
                          for s in com_syms}
            com_ranked = rank_commodities(com_closes, cfg)
            # Own the whole candidate set, not just today's picks — otherwise a
            # name that drops out of the ranking is outside `owned` and can
            # never be sold.
            owned |= set(com_syms)
            missing = [s for s in com_syms if not com_closes.get(s)]
            if missing:
                _log(f"StrategyX: commodity sleeve has NO bars for "
                     f"{', '.join(missing)} — those cannot be ranked or held. "
                     "Add them to the instance's stock list.", "red")

        targets, notes = plan_targets(risk_on=sig.risk_on, config=cfg,
                                      satellite_ranked=ranked,
                                      held_core=held_core,
                                      commodity_ranked=com_ranked)
        decisions, sizes = targets_to_orders(
            targets, nav=nav, positions=positions, prices=prices,
            cash=cash, config=cfg, owned=owned)

        # A traded leg with no quote is not a no-op — it is the whole strategy
        # silently not running, and the self-censor in targets_to_orders (skip
        # on px <= 0) makes it look like a bar with nothing to do.
        missing = [s for s in (bull, chop) if float(prices.get(s) or 0.0) <= 0]
        if missing:
            _log(f"StrategyX: NO PRICE for {', '.join(missing)} — this bar can "
                 f"emit no order. Are {', '.join(sorted(owned))} in the "
                 "instance's stock list?", "red")

        # The sleeve's recurring failure in this repo has been SILENCE — an
        # inert lever logs the same as a working one. State the decision every
        # bar, whether or not it produced an order.
        _log(f"StrategyX {'RISK-ON' if sig.risk_on else 'RISK-OFF'} | "
             f"{sig.reason} | targets="
             f"{ {k: round(v, 4) for k, v in targets.items()} } | "
             f"orders={len(decisions)} | nav=${nav:,.0f} | closes={len(closes)}",
             "cyan" if sig.risk_on else "yellow")
        for note in notes:
            _log(f"  {note}", "white")
        if not closes:
            _log(f"StrategyX: NO visible {filt} bars at {current_time} — the "
                 "filter is blind and the core is held flat. Is the filter "
                 "symbol in the instance's stock list?", "red")
        elif len(closes) < int(cfg.get("core_filter_ma_bars", 200) or 200):
            # THE false-negative trap. The broker's warmup is clamped to 90
            # CALENDAR days (~65 sessions) at any sub-daily granularity, so a
            # 200-day MA never forms and the strategy sits 100% in the chop
            # occupant for the whole run — reading as "Strategy X has no edge"
            # when it simply never ran. Say so loudly.
            _log(f"StrategyX: only {len(closes)} daily closes, need "
                 f"{cfg.get('core_filter_ma_bars')} — the filter CANNOT arm and "
                 "this run is 100% "
                 f"{cfg.get('core_chop_symbol')}. Use granularity=86400 (700-bar "
                 "warmup) or start the window >= 10 months after data begins. "
                 "This is NOT a result.", "red")

        cache["_strategy_x_last"] = {
            "risk_on": bool(sig.risk_on),
            "reason": sig.reason,
            "price": sig.price,
            "ma": sig.ma,
            "rvol": sig.rvol,
            "rvol_median": sig.rvol_median,
            "targets": dict(targets),
            "n_closes": len(closes),
        }

        if day_key:
            cache["_sx_last_decision_day"] = day_key
        if not decisions:
            return {}
        out = dict(decisions)
        # Release the broker's cash-reserve floor for this strategy. The floor
        # defaults to 10% of the INITIAL account value and can only be bypassed
        # by a high-conviction Nexus buy with >= 5 open positions — neither of
        # which this strategy ever produces. Targets that sum to 1.0 would then
        # leave the second leg permanently unfundable: it stays ~9.5% below
        # target, outside the band, and is re-issued every single bar for the
        # rest of the run. This strategy holds its own reserve via `core_weight`.
        sizes["_cash_reserve_floor_pct"] = 0.0
        sizes["_cash_reserve_floor_hard"] = False
        out["_nexus_position_sizes"] = sizes
        out["_nexus_executable_buys"] = [s for s, d in decisions.items() if d == 1]
        out["_nexus_sell_enforcement"] = [s for s, d in decisions.items() if d == -1]
        return out

    @staticmethod
    def _ranked(cfg, data):
        """Conviction-ordered satellite candidates, best first.

        Reads `data["conviction_scores"]` — the same channel `index_core_tilt`
        uses, which is how a graph/LLM ranking reaches this strategy. Falls back
        to an EMPTY list, never to an arbitrary order: a fake ranking makes a
        dead signal look alive, which is exactly how 677/677 `raw_score=0.000`
        stayed invisible for so long.
        """
        scores = {}
        if isinstance(data, dict):
            raw = data.get("conviction_scores") or {}
            if isinstance(raw, dict):
                skip = {
                    str(cfg.get("core_bull_symbol", "") or "").strip().upper(),
                    str(cfg.get("core_chop_symbol", "") or "").strip().upper(),
                    str(cfg.get("core_bear_symbol", "") or "").strip().upper(),
                }
                for sym, val in raw.items():
                    try:
                        score = float(val)
                    except (TypeError, ValueError):
                        continue
                    s = str(sym).strip().upper()
                    if s and s not in skip and score > 0:
                        scores[s] = score
        return [s for s, _ in sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))]
