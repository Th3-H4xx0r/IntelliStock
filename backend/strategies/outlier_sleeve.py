# INTELLISTOCK_SCHEMA: {"strategy": "outlier_sleeve", "weight": 1.0, "execution_position": 20, "decision_phase": "pre", "execution_scope": "run_once", "conditions": {}, "config": {"outlier_sleeve_enabled": false, "sleeve_fraction": 0.15, "slot_fraction": 0.015, "max_slots": 10, "winner_cap_fraction": 0.3, "adv_min_usd": 10000000.0, "price_min": 3.0, "min_history_bars": 120, "breakout_tolerance": 0.02, "rs_decile_floor": 0.9, "confirm_enabled": true, "confirm_min_peers": 5, "confirm_frac": 0.25, "confirm_hot_rank": 0.75, "screen_weekdays": [2], "screen_every_n_weeks": 2, "exit_sma_bars": 200, "exit_below_sma_closes": 5, "time_stop_sessions": 60, "time_stop_gain": 0.15, "excluded_symbols": ["TQQQ", "SPY", "BIL", "QQQ", "GLD", "GDX", "XLE"], "min_order_usd": 25.0, "broker_max_single_position_pct": 0.95, "honour_single_position_cap": true, "live_max_order_fraction": 0.7, "live_max_symbol_fraction": 0.35, "live_max_leveraged_fraction": 0.7, "live_soft_drawdown": 0.25, "live_hard_drawdown": 0.35, "live_kill_drawdown": 0.45}}
# INTELLISTOCK_DESCRIPTION: Outlier sleeve — buys 52-week-high breakouts with top-decile six-month relative strength, confirmed by Nexus peer breadth, in 1.5%-of-NAV slices that are never rebalanced down, and exits only on five closes below the 200-day average or a 12-week time stop. Captures the power-law tail (SNDK, SMCI, CLS) beside the EB core. No LLM, no news.
"""Outlier sleeve wrapper: the broker's run-once contract around the pure rules.

Reads ONE visible session's cross-section from OutlierUniverseFeatures through
the injected `store`; never touches bars, never queries Neo4j.
"""
from __future__ import annotations

import os
import sys

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from db import store  # noqa: E402  (tests monkeypatch this name)
from outlier_features import cross_section, peers_for, visible_dates  # noqa: E402
from outlier_sleeve import (  # noqa: E402
    DEFAULTS, LAST_SCREEN_KEY, exit_decisions, new_slot_orders, ny_date,
    screen, should_screen, visible_session, winner_cap_trims,
)
from strategy_eb import session_ordinal  # noqa: E402

# Same sink as StrategyEb: the backtest log buffer that becomes
# BacktestResults.logs, the only log an operator actually reads.
try:
    from intellistock_logger import intellistock_logger as _ilog  # type: ignore

    def _log(msg, color="white"):
        _ilog.log(str(msg), color, service="OutlierSleeve")
except Exception:  # pragma: no cover - standalone/test import
    def _log(msg, color="white"):
        print(f"[OutlierSleeve] {msg}")

SLOTS_KEY = "_outlier_slots"
LAST_DECISION_KEY = "_outlier_last"
_SELL_INTENT = "etf_sell"


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _emit(decisions, sizes, universe) -> dict:
    out = dict(decisions)
    sizes = dict(sizes)
    sizes["_cash_reserve_floor_pct"] = 0.0
    out["_nexus_position_sizes"] = sizes
    out["_nexus_discovered"] = sorted(universe)
    out["_nexus_executable_buys"] = sorted(s for s, d in decisions.items() if d == 1)
    out["_nexus_sell_enforcement"] = sorted(s for s, d in decisions.items() if d == -1)
    out["_nexus_action_intents"] = {s: _SELL_INTENT for s, d in decisions.items() if d == -1}
    return out


class OutlierSleeve:
    # The class name is NOT free: broker.py CamelCases the module id to find it.

    def run_once(self, symbols, prices, current_time, config, conditions,
                 data=None, portfolio_emulator=None, strategy_cache=None,
                 time_increment=None, mode=None, **kwargs):
        cfg = {**DEFAULTS, **(config or {})}
        if not _truthy(cfg.get("outlier_sleeve_enabled", False)) or portfolio_emulator is None:
            return {}
        cache = strategy_cache if isinstance(strategy_cache, dict) else {}
        slots = cache.setdefault(SLOTS_KEY, {})

        session = visible_session(current_time, visible_dates(store, ny_date(current_time)))
        if session is None:
            _log("OutlierSleeve: REFUSING to trade — no visible feature row before "
                 f"{ny_date(current_time)}; run scripts/build_outlier_features.py", "red")
            return {}
        rows = cross_section(store, session)
        rows_by_sym = {str(r.get("symbol") or "").upper(): r for r in rows}

        eff = {str(s).upper(): float(v) for s, v in (prices or {}).items() if v}
        for sym in slots:
            if not eff.get(sym) and rows_by_sym.get(sym):
                eff[sym] = float(rows_by_sym[sym].get("close") or 0.0)
        nav = float(portfolio_emulator.get_portfolio_value(eff) or 0.0)
        if nav <= 0:
            return {}
        positions = portfolio_emulator.get_positions() or {}

        decisions, sizes = {}, {}
        # 1. exits — unconditional, every call, once per session
        for sym, why in exit_decisions(slots, rows_by_sym, session, cfg).items():
            decisions[sym] = -1
            slots.pop(sym, None)
            _log(f"OutlierSleeve {session} | EXIT {sym} ({why})", "yellow")
        # 2. winner cap — a partial sell that keeps the slot
        for sym, frac in winner_cap_trims(slots, positions, eff, nav, cfg).items():
            if sym in decisions:
                continue
            decisions[sym] = -1
            sizes[sym] = {"sell_fraction": frac}
            _log(f"OutlierSleeve {session} | CAP {sym} sell {frac:.1%}", "yellow")
        # 3. entries — on the screen cadence only
        if should_screen(session, cache, cfg):
            held = set(slots) | {str(s).upper() for s, q in positions.items()
                                 if float(q or 0) > 0}
            peers = (peers_for(store, [r["symbol"] for r in rows])
                     if _truthy(cfg.get("confirm_enabled")) else {})
            ranked = screen(rows, cfg, peers, held)
            try:
                cash = float(portfolio_emulator.get_buying_power(prices=eff) or 0.0)
            except (AttributeError, TypeError):
                cash = float(portfolio_emulator.get_cash() or 0.0)
            ordinal = session_ordinal(session)
            for sym, amt in new_slot_orders(ranked, slots, nav, cash, cfg).items():
                px = float(rows_by_sym[sym].get("close") or 0.0)
                decisions[sym] = 1
                sizes[sym] = {"buy_cash": amt}
                slots[sym] = {"entry_px": px, "entry_ordinal": ordinal, "entry_cost": amt,
                              "proven": False, "below": 0, "last_eval": session}
            cache[LAST_SCREEN_KEY] = ordinal
            _log(f"OutlierSleeve {session} | screen: {len(ranked)} candidates, "
                 f"{sum(1 for d in decisions.values() if d == 1)} entries | nav=${nav:,.0f}",
                 "cyan")

        universe = set(slots) | set(decisions)
        cache[LAST_DECISION_KEY] = {"session": session, "slots": sorted(slots),
                                    "orders": len(decisions)}
        if not decisions:
            return _emit({s: 0 for s in slots}, {}, universe) if slots else {}
        return _emit(decisions, sizes, universe)
