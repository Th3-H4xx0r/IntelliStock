# INTELLISTOCK_SCHEMA: {"strategy": "strategy_ht", "weight": 1.0, "execution_position": 10, "decision_phase": "pre", "execution_scope": "run_once", "conditions": {}, "config": {"strategy_ht_enabled": false, "filter_symbol": "SPY", "ma_bars": 200, "enter_band": 0.03, "exit_band": 0.03, "in_symbol": "SSO", "in_weight": 1.0, "out_symbols": {"SPY": 0.5, "BIL": 0.5}, "core_band_pct": 0.05, "min_order_usd": 50.0, "cost_haircut_pct": 0.006, "broker_max_single_position_pct": 1.0, "honour_single_position_cap": true}}
# INTELLISTOCK_DESCRIPTION: Hysteresis trend — 100% SSO (2x S&P) while SPY holds above its 200-day average by 3%, parked in 50% SPY / 50% BIL once SPY closes 3% below it; about one switch a year.
"""Strategy HT wrapper: broker contract, order emission, cache row.

Everything testable lives in `backend/strategy_ht.py`, which is pure. This
file owns only what needs the broker: the emulator, the cache, the decision row.
Pattern copied from strategies/strategy_xs.py.
"""
import os
import sys

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from strategy_ht import (  # noqa: E402
    DEFAULTS,
    ht_targets,
    strategy_ht_universe,
    trend_state,
)
from strategy_x import pit_daily_closes, targets_to_orders  # noqa: E402

try:
    from utils import log_message as _log
except Exception:  # pragma: no cover - broker-only import
    def _log(msg, color="white"):
        print(msg)

_SELL_INTENT = "etf_sell"
"""Every exit here is a rebalance of an ETF book: the broker's sell whitelist
calls that `etf_sell`, and a sell with no recognised intent logs
`would_block_in_phase2=True` (see strategies/strategy_xs.py)."""


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _bars_for(data, symbol):
    entry = (data or {}).get(symbol) or {}
    if isinstance(entry, dict):
        return entry.get("bars") or []
    return entry or []


class StrategyHt:
    """The class name is NOT free: broker.py resolves a run-once strategy by
    CamelCasing its id (`strategy_ht` -> `StrategyHt`) and runs the whole
    backtest inert when it misses (see strategies/strategy_xs.py)."""

    def run_once(self, symbols, prices, current_time, config, conditions,
                 data=None, portfolio_emulator=None, strategy_cache=None,
                 time_increment=None, mode=None, **kwargs):
        cfg = {**DEFAULTS, **(config or {})}
        if not _truthy(cfg.get("strategy_ht_enabled", False)):
            return {}
        if portfolio_emulator is None:
            return {}

        prices = prices or {}
        cache = strategy_cache if isinstance(strategy_cache, dict) else {}
        universe = strategy_ht_universe(cfg)
        filt = str(cfg.get("filter_symbol", "SPY") or "SPY").strip().upper()
        closes = pit_daily_closes(_bars_for(data, filt), current_time)

        ma_bars = max(2, int(cfg.get("ma_bars", 200) or 200))
        if len(closes) < ma_bars:
            _log(f"StrategyHt: REFUSING to trade — {len(closes)} daily closes "
                 f"for {filt}, need {ma_bars}. The filter cannot arm.", "red")
            return {}

        state = trend_state(closes, cfg)
        targets, notes = ht_targets(state.in_market, cfg)

        eff ={str(s).strip().upper(): v for s, v in prices.items()}
        for symbol in universe:
            if float(eff.get(symbol) or 0.0) > 0:
                continue
            visible = pit_daily_closes(_bars_for(data, symbol), current_time)
            if visible and float(visible[-1]) > 0:
                eff[symbol] = float(visible[-1])

        nav = float(portfolio_emulator.get_portfolio_value(eff) or 0.0)
        positions = portfolio_emulator.get_positions() or {}
        cash = float(portfolio_emulator.get_cash() or 0.0)
        decisions, sizes = targets_to_orders(
            targets, nav=nav, positions=positions, prices=eff, cash=cash,
            config=cfg, owned=set(universe))

        cache["_strategy_ht_last"] = {
            "in_market": bool(state.in_market), "decided": bool(state.decided),
            "reason": state.reason, "crossings": int(state.crossings),
            "targets": dict(targets), "notes": list(notes),
        }
        _log(f"StrategyHt {'IN' if state.in_market else 'OUT'} | {state.reason} | targets="
             + ", ".join(f"{s} {w:.0%}" for s, w in sorted(targets.items()))
             + f" | orders={len(decisions)} | nav=${nav:,.0f}", "cyan")

        if not decisions:
            return {}
        out = dict(decisions)
        out["_nexus_position_sizes"] = sizes
        out["_nexus_discovered"] = list(universe)
        out["_nexus_executable_buys"] = [s for s, d in decisions.items() if d == 1]
        out["_nexus_sell_enforcement"] = [s for s, d in decisions.items() if d == -1]
        out["_nexus_action_intents"] = {s: _SELL_INTENT for s, d in decisions.items() if d == -1}
        return out
