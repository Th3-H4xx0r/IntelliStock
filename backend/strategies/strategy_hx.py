# INTELLISTOCK_SCHEMA: {"strategy": "strategy_hx", "weight": 1.0, "execution_position": 10, "decision_phase": "pre", "execution_scope": "run_once", "conditions": {}, "config": {"strategy_hx_enabled": false, "reference_symbol": "QQQ", "core_symbol": "TQQQ", "core_leverage": 3.0, "target_vol": 0.2, "core_max_weight": 0.65, "weight_step": 0.05, "vol_fast_bars": 10, "vol_slow_bars": 40, "min_history_bars": 70, "bull_remainder_symbol": "QQQ", "cash_symbol": "BIL", "chop_core_damp": 0.5, "bear_book": {"PSQ": 0.6, "BIL": 0.4}, "fast_low_bars": 20, "sma_bars": 50, "drawdown_pct": 0.07, "drawdown_bars": 10, "exit_confirm_sessions": 5, "chop_slope_bars": 20, "chop_slope_pct": 0.015, "rebalance_band": 0.1, "min_order_usd": 25.0, "honour_single_position_cap": true, "max_single_position_pct": 0.95, "broker_max_single_position_pct": 0.95}}
# INTELLISTOCK_DESCRIPTION: Fast-in, slow-out bear hedge. A three-state QQQ machine enters a bear book on a single session (a 20-day low under the 50-day average, or a 7% fall from a 20-day high inside 10 sessions) and needs five consecutive closes above the average to leave it. BULL holds a volatility-targeted TQQQ core with the remainder in QQQ; CHOP halves the core into BIL. A risk transform, not an alpha.
"""Strategy HX wrapper: cache state, order emission, broker contract.

Everything testable lives in `backend/strategy_hx.py`, which is pure. This
file owns only what needs the broker: the point-in-time boundary, the
emulator, the cache, and the decision row.

Design: docs/superpowers/specs/2026-09-10-strategy-hx-design.md
"""
import os
import sys

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from strategy_hx import (  # noqa: E402
    DEFAULTS,
    _f,
    _s,
    hx_state,
    hx_targets,
    strategy_hx_universe,
)
from strategy_x import pit_daily_observations, targets_to_orders  # noqa: E402

# Route through intellistock_logger, NOT print() and NOT utils.log_message.
# The backtest engine runs the broker with `detach=False, remove=True` and
# DISCARDS container stdout on success. intellistock_logger fans out to
# BacktestResults.logs, the only log an operator reads; Strategy XS used
# utils.log_message and its lines never reached the sink.
try:
    from intellistock_logger import intellistock_logger as _ilog  # type: ignore

    def _log(msg, color="white"):
        _ilog.log(str(msg), color, service="StrategyHx")
except Exception:  # pragma: no cover - standalone/test import
    def _log(msg, color="white"):
        print(f"[StrategyHx] {msg}")


#: Every HX exit is a rebalance of an ETF book, which is what the broker's
#: sell whitelist calls `etf_sell`. broker.py's Z2.1 check reads it off the
#: strategy summary; a sell with no recognised intent logs
#: would_block_in_phase2=True — 965 of 965 sells on BT406990.
_SELL_INTENT = "etf_sell"

_STATE_KEY = "_strategy_hx_state"
_CONFIRM_KEY = "_strategy_hx_confirm"
_LAST_DECISION_KEY = "_strategy_hx_last"
_LOGGED_KEY = "_strategy_hx_logged"


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _log_once(cache, reason, scope, msg, color="white"):
    """Log a recurring condition at most once per `scope`. Every refusal here
    is a STANDING condition, so it repeats identically on every tick of a
    session; the sink is BacktestResults.logs, read by eye, and drowning it is
    how a real refusal goes unnoticed."""
    seen = cache.get(_LOGGED_KEY)
    if not isinstance(seen, dict):
        seen = {}
        cache[_LOGGED_KEY] = seen
    if seen.get(reason) == scope:
        return
    seen[reason] = scope
    _log(msg, color)


def _bars_for(data, symbol):
    """The engine hands bars as either {sym: {"bars": [...]}} or {sym: [...]}."""
    if not isinstance(data, dict):
        return []
    entry = data.get(symbol)
    if isinstance(entry, dict):
        return entry.get("bars") or []
    return entry or []


def _emit(decisions, sizes, universe) -> dict:
    """The broker payload for a set of decisions."""
    out = dict(decisions)
    sizes = dict(sizes)
    # The broker's buy gate reserves `_cash_reserve_floor_pct` (default 0.10)
    # of STARTING value as untouchable cash, sized for a many-name discovery
    # book. On a small ETF book it blocked 775 of 805 buys in BT 400783. This
    # book's risk control is the state machine, not a cash floor.
    sizes["_cash_reserve_floor_pct"] = 0.0
    out["_nexus_position_sizes"] = sizes
    out["_nexus_discovered"] = list(universe)
    out["_nexus_executable_buys"] = [s for s, d in decisions.items() if d == 1]
    out["_nexus_sell_enforcement"] = [s for s, d in decisions.items()
                                      if d == -1]
    out["_nexus_action_intents"] = {s: _SELL_INTENT
                                    for s, d in decisions.items() if d == -1}
    return out


class StrategyHx:
    # The class name is NOT free. `broker.py` resolves a run-once strategy by
    # CamelCasing its id — `strategy_hx` -> `StrategyHx` — and logs
    # "Class not found ... has no run_once method; skipping" when it misses,
    # then runs the whole backtest inert.

    def run_once(self, symbols, prices, current_time, config, conditions,
                 data=None, portfolio_emulator=None, strategy_cache=None,
                 time_increment=None, mode=None, **kwargs):
        cfg = {**DEFAULTS, **(config or {})}
        if not _truthy(cfg.get("strategy_hx_enabled", False)):
            return {}
        if portfolio_emulator is None:
            return {}

        cache = strategy_cache if isinstance(strategy_cache, dict) else {}
        universe = strategy_hx_universe(cfg)
        reference = _s(cfg, "reference_symbol")

        # THE point-in-time boundary: strictly-earlier NY sessions only.
        observations = pit_daily_observations(_bars_for(data, reference),
                                              current_time)
        if not observations:
            # No session is knowable here, so the throttle falls back to the
            # call DATE. The state is deliberately NOT written: a blind tick
            # must leave the machine exactly as it found it.
            _log_once(cache, "blind", str(current_time)[:10],
                      f"StrategyHx: REFUSING to trade — no visible {reference}"
                      " daily closes. Live passes data=None; a strategy that "
                      "cannot see its regime must do NOTHING.", "red")
            return {}
        session_id = observations[-1][0]
        closes = [close for _, close in observations]

        prev_state = str(cache.get(_STATE_KEY) or "").strip().upper()
        state, confirm = hx_state(closes, prev_state,
                                  cache.get(_CONFIRM_KEY), cfg)
        cache[_STATE_KEY] = state
        cache[_CONFIRM_KEY] = confirm
        if state == "UNKNOWN":
            _log_once(cache, "unknown", session_id,
                      f"StrategyHx {session_id} | UNKNOWN — {len(closes)} "
                      f"{reference} closes, need "
                      f"{cfg.get('min_history_bars')}, or a close is not "
                      "finite and positive. Book is 100% cash; a cold start "
                      "never levers up.", "red")

        targets = hx_targets(closes, state, cfg)

        # Prices the broker did not carry: a declared leg absent from the
        # operator's watchlist falls back to the last VISIBLE close, which is
        # the same number a quote would carry on this bar.
        eff = {str(s).strip().upper(): v for s, v in (prices or {}).items()}
        for symbol in universe:
            if float(eff.get(symbol) or 0.0) > 0:
                continue
            visible = pit_daily_observations(_bars_for(data, symbol),
                                             current_time)
            if visible and float(visible[-1][1]) > 0:
                eff[symbol] = float(visible[-1][1])

        # A leg with no price at all is skipped by `targets_to_orders`, which
        # would silently CONCENTRATE the book into whatever is left. Move its
        # weight to cash explicitly instead.
        cash_symbol = _s(cfg, "cash_symbol")
        missing = sorted(s for s in targets if float(eff.get(s) or 0.0) <= 0)
        if missing:
            spare = 0.0
            for symbol in missing:
                spare += targets.pop(symbol, 0.0)
            if spare > 0 and float(eff.get(cash_symbol) or 0.0) > 0:
                targets[cash_symbol] = round(
                    targets.get(cash_symbol, 0.0) + spare, 6)
            _log_once(cache, "unpriced", f"{session_id}:{','.join(missing)}",
                      f"StrategyHx {session_id} | no price for "
                      f"{', '.join(missing)} — weight to {cash_symbol}", "red")

        nav = float(portfolio_emulator.get_portfolio_value(eff) or 0.0)
        if nav <= 0:
            return {}
        positions = portfolio_emulator.get_positions() or {}

        band = max(0.0, _f(cfg, "rebalance_band"))
        held = {}
        for symbol in set(list(targets) + list(universe)):
            held[symbol] = (float(positions.get(symbol) or 0.0)
                            * float(eff.get(symbol) or 0.0)) / nav
        drift = max((abs(targets.get(s, 0.0) - held.get(s, 0.0))
                     for s in held), default=0.0)
        changed = state != prev_state
        # A state change rebalances the FULL book immediately — that is the
        # whole point of a fast entry. Within a state the band suppresses
        # drift, which is what keeps turnover down.
        if not changed and drift <= band:
            return {}

        # `targets_to_orders` reads the band under the name `core_band_pct`.
        # HX calls it `rebalance_band`, so without this mapping the band would
        # silently fall back to strategy_x's own 0.05 default — half the
        # intended width, and twice the turnover.
        order_cfg = {**cfg, "core_band_pct": band}
        try:
            cash = float(portfolio_emulator.get_buying_power(prices=eff))
        except (AttributeError, TypeError):
            cash = float(portfolio_emulator.get_cash() or 0.0)

        decisions, sizes = targets_to_orders(
            targets, nav=nav, positions=positions, prices=eff, cash=cash,
            config=order_cfg, owned=set(universe))

        cache[_LAST_DECISION_KEY] = {
            "session": session_id, "state": state, "confirm": confirm,
            "drift": round(drift, 6), "targets": dict(targets),
            "orders": len(decisions),
        }
        reason = "state change" if changed else f"band breach {drift:.1%}"
        _log_once(cache, "decision", f"{session_id}:{state}:{reason}",
                  f"StrategyHx {session_id} | {state} ({reason}) | targets="
                  + ", ".join(f"{s} {w:.1%}"
                              for s, w in sorted(targets.items()))
                  + f" | orders={len(decisions)} | nav=${nav:,.0f}", "cyan")

        if not decisions:
            return {}
        return _emit(decisions, sizes, universe)
