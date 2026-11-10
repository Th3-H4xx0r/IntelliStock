# INTELLISTOCK_SCHEMA: {"strategy": "strategy_ht", "weight": 1.0, "execution_position": 10, "decision_phase": "pre", "execution_scope": "run_once", "conditions": {}, "config": {"strategy_ht_enabled": false, "filter_symbol": "SPY", "ma_bars": 200, "enter_band": 0.01, "exit_band": 0.03, "vol_target": 0.20, "vol_bars": 21, "exp_min": 0.5, "exp_max": 2.0, "exp_grid": 0.5, "exp_hyst": 0.15, "bounce_drop": 0.0, "in_symbol": "SSO", "one_x_symbol": "SPY", "cash_symbol": "BIL", "in_weight": 1.0, "out_symbols": {"SPY": 0.5, "BIL": 0.5}, "core_band_pct": 0.05, "min_order_usd": 50.0, "cost_haircut_pct": 0.006, "broker_max_single_position_pct": 1.0, "honour_single_position_cap": true, "backtest_credit_pending_sell_proceeds": true, "live_max_order_fraction": 1.0, "live_max_symbol_fraction": 1.0, "live_max_leveraged_fraction": 1.0, "live_soft_drawdown": 0.35, "live_hard_drawdown": 0.50, "live_kill_drawdown": 0.60}}
# INTELLISTOCK_DESCRIPTION: Hysteresis trend, Balanced profile — up to 2x S&P (SSO) while SPY holds above its 200-day average (re-enter 1% above, exit 3% below), with the exposure stepped down (2x / 1.5x / 1x / 0.5x) when 21-day realised vol exceeds a 20% target, and parked in 50% SPY / 50% BIL when the trend breaks.
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
    in_exposure,
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
        last = cache.get("_strategy_ht_last") if isinstance(cache.get("_strategy_ht_last"), dict) else {}
        # The previous grid level only carries over while the book was in the
        # market; a fresh entry rounds to the nearest level.
        prev_level = last.get("exposure") if last.get("in_market") else None
        exposure, why_exp = in_exposure(closes, cfg, prev_level)
        # The bounce overlay holds exp_max for a day regardless of trend state.
        bounced = why_exp.startswith("bounce")
        targets, notes = ht_targets(state.in_market or bounced, cfg, exposure)
        notes.append(why_exp)

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
        # Size buys off buying power when the emulator offers it: with the lane's
        # `backtest_credit_pending_sell_proceeds`, that counts this bar's own
        # funding sell (haircut), so a level change does not spend a session
        # with a quarter of NAV in cash (strategy_eb._spendable does the same).
        try:
            bp = portfolio_emulator.get_buying_power(prices=eff)
            if bp is not None:
                cash = max(cash, float(bp))
        except (AttributeError, TypeError):
            pass
        # `targets_to_orders` skips a top-up smaller than the drift band when the
        # position already exists, so the cash left over after a flip (sells fill
        # a bar before buys are sized) stayed idle: ~9% of NAV on average over
        # the engine's full-cycle run. Once idle cash exceeds the band, trade
        # the whole book to target with no band, once.
        order_cfg = cfg
        band = float(cfg.get("core_band_pct", 0.05) or 0.0)
        if nav > 0 and cash / nav > band:
            order_cfg = {**cfg, "core_band_pct": 0.0}
        decisions, sizes = targets_to_orders(
            targets, nav=nav, positions=positions, prices=eff, cash=cash,
            config=order_cfg, owned=set(universe))

        cache["_strategy_ht_last"] = {
            "in_market": bool(state.in_market), "decided": bool(state.decided),
            "reason": state.reason, "crossings": int(state.crossings),
            "exposure": float(exposure), "bounce": bool(bounced),
            "targets": dict(targets), "notes": list(notes),
        }
        _log(f"StrategyHt {'IN' if state.in_market else 'OUT'}{' +BOUNCE' if bounced else ''} | {state.reason} | {why_exp} | targets="
             + ", ".join(f"{s} {w:.0%}" for s, w in sorted(targets.items()))
             + f" | orders={len(decisions)} | nav=${nav:,.0f}", "cyan")

        if not decisions:
            return {}
        out = dict(decisions)
        sizes = dict(sizes)
        # The broker's buy gate reserves `_cash_reserve_floor_pct` (default
        # 0.10) of the STARTING value as an untouchable cash floor, sized for
        # a many-name discovery book. On this 1-2 leg ETF book it left 10% of
        # NAV idle on every OUT and IN buy (strategy-ht-paper tick #19,
        # 2026-11-10: $1,000 of $9,936 never reached BIL). The vol target is
        # the risk control here, as on EB/HX/X; the floor is zeroed the same
        # way those lanes do.
        sizes["_cash_reserve_floor_pct"] = 0.0
        out["_nexus_position_sizes"] = sizes
        out["_nexus_discovered"] = list(universe)
        out["_nexus_executable_buys"] = [s for s, d in decisions.items() if d == 1]
        out["_nexus_sell_enforcement"] = [s for s, d in decisions.items() if d == -1]
        out["_nexus_action_intents"] = {s: _SELL_INTENT for s, d in decisions.items() if d == -1}
        return out
