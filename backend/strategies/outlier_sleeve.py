# INTELLISTOCK_SCHEMA: {"strategy": "outlier_sleeve", "weight": 1.0, "execution_position": 20, "decision_phase": "pre", "execution_scope": "run_once", "conditions": {}, "config": {"outlier_sleeve_enabled": false, "feature_dataset": "", "winner_add_enabled": false, "winner_add_fraction": 0.05, "winner_add_position_cap": 0.2, "winner_add_min_gain": 0.25, "winner_add_min_age": 20, "winner_add_min_gap": 20, "winner_add_max_count": 2, "sleeve_fraction": 0.15, "slot_fraction": 0.015, "max_slots": 10, "winner_cap_fraction": 0.3, "adv_min_usd": 10000000.0, "price_min": 3.0, "min_history_bars": 120, "breakout_tolerance": 0.02, "rs_decile_floor": 0.9, "confirm_enabled": true, "confirm_min_peers": 5, "confirm_frac": 0.25, "confirm_hot_rank": 0.75, "screen_weekdays": [2], "screen_every_n_weeks": 2, "exit_sma_bars": 200, "exit_below_sma_closes": 5, "time_stop_sessions": 60, "time_stop_gain": 0.15, "excluded_symbols": ["TQQQ", "SPY", "BIL", "QQQ", "GLD", "GDX", "XLE"], "min_order_usd": 25.0, "broker_max_single_position_pct": 0.95, "honour_single_position_cap": true, "live_max_order_fraction": 0.7, "live_max_symbol_fraction": 0.35, "live_max_leveraged_fraction": 0.7, "live_soft_drawdown": 0.25, "live_hard_drawdown": 0.35, "live_kill_drawdown": 0.45}}
# INTELLISTOCK_DESCRIPTION: Outlier sleeve — buys 52-week-high breakouts with top-decile six-month relative strength, confirmed by Nexus peer breadth, in 1.5%-of-NAV slices that are never rebalanced down, and exits only on five closes below the 200-day average or a 12-week time stop. Captures the power-law tail (SNDK, SMCI, CLS) beside the EB core. No LLM, no news.
"""Outlier sleeve wrapper: the broker's run-once contract around the pure rules.

Reads ONE visible session's cross-section from OutlierUniverseFeatures through
the injected `store`; never touches bars, never queries Neo4j.
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from db import store  # noqa: E402  (tests monkeypatch this name)
from outlier_features import cross_section, peers_for, visible_dates  # noqa: E402
from outlier_sleeve import (  # noqa: E402
    DEFAULTS, LAST_SCREEN_KEY, exit_decisions, new_slot_orders, ny_date,
    screen, should_screen, visible_session, winner_cap_trims, winner_add_orders,
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
PENDING_KEY = "_outlier_pending_entries"
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


def _reserved_for(reservations, symbol):
    return sum(float(v or 0.0) for k, v in reservations.items()
               if str(k).endswith(f"-{symbol}"))


def _open_basis(trades, symbol):
    """Actual remaining average-cost basis, including native fill fees."""
    quantity = cost = gross = 0.0
    first = None
    last_buy, buy_orders = None, set()
    for index, trade in enumerate(trades):
        if str(trade.get("ticker") or "").upper() != symbol:
            continue
        shares = float(trade.get("shares") or 0.0)
        if trade.get("action") == "buy":
            if quantity <= 1e-12:
                first = trade.get("timestamp")
                buy_orders = set()
            buy_orders.add(trade.get("order_id") or f"history-{index}")
            last_buy = trade.get("timestamp")
            quantity += shares
            cost += float(trade.get("total") or 0.0)
            gross += shares * float(trade.get("price") or 0.0)
        elif trade.get("action") == "sell" and quantity > 0:
            fraction = max(0.0, quantity - shares) / quantity
            quantity *= fraction
            cost *= fraction
            gross *= fraction
    if quantity <= 1e-12 or first is None:
        return None
    if isinstance(first, str):
        first = datetime.fromisoformat(first.replace("Z", "+00:00"))
    if isinstance(last_buy, str):
        last_buy = datetime.fromisoformat(last_buy.replace("Z", "+00:00"))
    return {"entry_cost": cost, "entry_px": gross / quantity,
            "entry_ordinal": session_ordinal(ny_date(first)),
            "buy_count": len(buy_orders),
            "last_buy_ordinal": session_ordinal(ny_date(last_buy))}


def _reconcile_entries(slots, pending, positions, emulator, session):
    reservations = getattr(emulator, "_execution_cash_reservations", {}) or {}
    history_reader = getattr(emulator, "get_trade_history", None)
    trades = history_reader() if callable(history_reader) else []
    for sym in set(slots) | set(pending):
        quantity = float(positions.get(sym) or 0.0)
        reserved = _reserved_for(reservations, sym)
        if quantity > 1e-12:
            basis = _open_basis(trades, sym)
            if basis:
                slot = slots.setdefault(sym, {"proven": False, "below": 0,
                                               "last_eval": ""})
                slot.update(basis)
                if sym in pending and reserved <= 1e-12:
                    pending.pop(sym)
                    _log(f"OutlierSleeve {session} | CONFIRMED {sym} "
                         f"cost=${basis['entry_cost']:.2f}", "cyan")
        else:
            slots.pop(sym, None)
        if sym in pending:
            if reserved > 1e-12:
                pending[sym]["entry_cost"] = reserved
            elif quantity <= 1e-12 and pending[sym]["signal_session"] < session:
                pending.pop(sym)
                _log(f"OutlierSleeve {session} | RELEASE unfilled {sym}", "yellow")


def _fundable_cash(portfolio, prices):
    reserved = sum(float(v or 0.0) for v in
                   (getattr(portfolio, "_execution_cash_reservations", {}) or {}).values())
    try:
        native_reader = getattr(portfolio, "get_buying_power", None)
        if callable(native_reader):
            return max(0.0, float(native_reader(reserved=reserved, prices=prices) or 0.0))
        available_reader = getattr(portfolio, "get_available_cash", None)
        if callable(available_reader):
            available = max(0.0, float(available_reader(reserved=reserved) or 0.0))
            broker_limit = getattr(portfolio, "_buying_power", None)
            if broker_limit is not None:
                available = min(available, max(0.0, float(broker_limit) - reserved))
            return available
    except Exception as error:
        _log(f"OutlierSleeve: new buys blocked; cash reader failed ({type(error).__name__})", "red")
        return 0.0
    _log("OutlierSleeve: new buys blocked; available cash reader missing", "red")
    return 0.0


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
        pending = cache.setdefault(PENDING_KEY, {})

        dataset = str(cfg.get("feature_dataset") or "")
        if dataset:
            if _truthy(cfg.get("confirm_enabled")):
                _log("OutlierSleeve: REFUSING undated Graph confirmation with versioned history", "red")
                return {}
            manifests = cache.setdefault("_outlier_dataset_manifests", {})
            manifest = manifests.get(dataset)
            if manifest is None:
                manifest = store.get("PointInTimeDatasetSnapshots", f"outlier:{dataset}")
                if (not manifest or manifest.get("complete") is not True
                        or manifest.get("kind") != "outlier_features"):
                    _log(f"OutlierSleeve: REFUSING incomplete dataset {dataset}", "red")
                    return {}
                manifests[dataset] = manifest
                _log(f"OutlierSleeve DATASET {dataset} build={manifest.get('build_id')}", "cyan")
            dates = manifest.get("dates") or []
        else:
            dates = visible_dates(store, ny_date(current_time))
        session = visible_session(current_time, dates)
        if dataset and session and (date.fromisoformat(ny_date(current_time))
                                    - date.fromisoformat(session)).days > 10:
            _log(f"OutlierSleeve: REFUSING stale dataset {dataset} session={session}", "red")
            return {}
        if session is None:
            _log("OutlierSleeve: REFUSING to trade — no visible feature row before "
                 f"{ny_date(current_time)}; run scripts/build_outlier_features.py", "red")
            return {}
        rows = cross_section(store, session, dataset=dataset)
        rows_by_sym = {str(r.get("symbol") or "").upper(): r for r in rows}

        eff = {str(s).upper(): float(v) for s, v in (prices or {}).items() if v}
        for sym in slots:
            if not eff.get(sym) and rows_by_sym.get(sym):
                eff[sym] = float(rows_by_sym[sym].get("close") or 0.0)
        nav = float(portfolio_emulator.get_portfolio_value(eff) or 0.0)
        if nav <= 0:
            return {}
        positions = portfolio_emulator.get_positions() or {}
        _reconcile_entries(slots, pending, positions, portfolio_emulator, session)
        sell_reservations = getattr(portfolio_emulator,
                                    "_execution_position_reservations", {}) or {}

        decisions, sizes = {}, {}
        # 1. exits — unconditional, every call, once per session
        exits = exit_decisions(slots, rows_by_sym, session, cfg)
        exits.update({s: slot["exit_reason"] for s, slot in slots.items()
                      if slot.get("exit_reason")})
        for sym, why in exits.items():
            slots[sym]["exit_reason"] = why
            if _reserved_for(sell_reservations, sym) > 1e-12:
                continue
            decisions[sym] = -1
            _log(f"OutlierSleeve {session} | EXIT {sym} ({why})", "yellow")
        # 2. winner cap — a partial sell that keeps the slot
        for sym, frac in winner_cap_trims(slots, positions, eff, nav, cfg).items():
            if sym in decisions or _reserved_for(sell_reservations, sym) > 1e-12:
                continue
            decisions[sym] = -1
            sizes[sym] = {"sell_fraction": frac}
            _log(f"OutlierSleeve {session} | CAP {sym} sell {frac:.1%}", "yellow")
        # 3. entries — on the screen cadence only
        if should_screen(session, cache, cfg):
            held = set(slots) | set(pending) | {str(s).upper() for s, q in positions.items()
                                 if float(q or 0) > 0}
            peers = (peers_for(store, [r["symbol"] for r in rows])
                     if _truthy(cfg.get("confirm_enabled")) else {})
            ranked = screen(rows, cfg, peers, held)
            cash = _fundable_cash(portfolio_emulator, eff)
            ordinal = session_ordinal(session)
            planning_slots = {s: dict(slot) for s, slot in slots.items()}
            for sym, entry in pending.items():
                planned = planning_slots.setdefault(sym, {"entry_cost": 0.0})
                planned["entry_cost"] += float(entry["entry_cost"])
            adds = winner_add_orders(planning_slots, rows_by_sym, positions,
                                     nav, cash, session, cfg, set(pending) | set(decisions), prices=eff)
            for sym, amount in adds.items():
                decisions[sym] = 1
                sizes[sym] = {"buy_cash": amount}
                pending[sym] = {"entry_cost": amount, "signal_session": session}
                planning_slots[sym]["entry_cost"] += amount
                cash -= amount
                _log(f"OutlierSleeve {session} | ADD {sym} ${amount:.2f}", "cyan")
            for sym, amt in new_slot_orders(ranked, planning_slots, nav, cash, cfg).items():
                decisions[sym] = 1
                sizes[sym] = {"buy_cash": amt}
                pending[sym] = {"entry_cost": amt, "signal_session": session}
            cache[LAST_SCREEN_KEY] = ordinal
            _log(f"OutlierSleeve {session} | screen: {len(ranked)} candidates, "
                 f"{sum(1 for d in decisions.values() if d == 1)} entries | nav=${nav:,.0f}",
                 "cyan")

        universe = set(slots) | set(pending) | set(decisions)
        cache[LAST_DECISION_KEY] = {"session": session, "slots": sorted(slots),
                                    "orders": len(decisions)}
        if not decisions:
            return _emit({s: 0 for s in universe}, {}, universe) if universe else {}
        return _emit(decisions, sizes, universe)
