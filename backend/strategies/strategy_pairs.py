# INTELLISTOCK_SCHEMA: {"strategy": "strategy_pairs", "weight": 1.0, "execution_position": 10, "decision_phase": "pre", "execution_scope": "run_once", "conditions": {}, "config": {"strategy_pairs_enabled": false, "pairs": [["KO", "PEP"], ["WMT", "TGT"], ["HD", "LOW"], ["V", "MA"], ["XOM", "CVX"], ["UPS", "FDX"], ["F", "GM"], ["JPM", "BAC"], ["MCD", "YUM"], ["DAL", "UAL"], ["LMT", "NOC"], ["NKE", "UAA"]], "mode": "reversal", "lookback_bars": 5, "z_window": 60, "z_entry": 2.0, "z_exit": 0.0, "tilt": 1.0, "core_band_pct": 0.01, "min_order_usd": 25.0, "cost_haircut_pct": 0.006, "broker_max_single_position_pct": 1.0, "honour_single_position_cap": true, "backtest_credit_pending_sell_proceeds": true}}
# INTELLISTOCK_DESCRIPTION: Pairs laggard rotation (research lane, backtest only). Each pair of twin stocks gets an equal slice of the book; inside a pair the slice is held in whichever stock fell behind (reversal), in the one that is statistically cheap (zscore), or split 50/50 (hold, the control). Long-only: the return over the hold control is half of the long/short pair trade.
"""Strategy Pairs wrapper: broker contract, order emission, cache row.

Everything testable lives in `backend/strategy_pairs.py`, which is pure. This
file owns only what needs the broker: the point-in-time boundary, the
emulator, the cache and the decision row. Pattern copied from
strategies/strategy_ht.py.

BACKTEST ONLY. The lane is not registered in broker.py's live tables
(`_LANE_ENABLE_FLAGS`, the live daily-bar fetch), so a live tick passes
data=None and the lane refuses to trade.
"""
import os
import sys

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from strategy_pairs import (  # noqa: E402
    DEFAULTS,
    _f,
    aligned_closes,
    pair_signal,
    pairs_mode,
    pairs_of,
    pairs_targets,
    strategy_pairs_universe,
)
from strategy_x import pit_daily_observations, targets_to_orders  # noqa: E402

# intellistock_logger, not print(): the backtest engine discards container
# stdout, and BacktestResults.logs is the only log an operator reads.
try:
    from intellistock_logger import intellistock_logger as _ilog  # type: ignore

    def _log(msg, color="white"):
        _ilog.log(str(msg), color, service="StrategyPairs")
except Exception:  # pragma: no cover - standalone/test import
    def _log(msg, color="white"):
        print(f"[StrategyPairs] {msg}")


#: The broker's sell whitelist needs a recognised intent on every sell, or it
#: logs would_block_in_phase2=True. A rotation exit is a plain rebalance.
_SELL_INTENT = "etf_sell"

_SESSION_KEY = "_strategy_pairs_session"
_SIGNALS_KEY = "_strategy_pairs_signals"
_LAST_DECISION_KEY = "_strategy_pairs_last"
_LOGGED_KEY = "_strategy_pairs_logged"
#: Set when orders go out; the NEXT tick is sat out. An order placed on tick T
#: is not in the book on tick T+1 at daily steps (it fills at that session's
#: close), so deciding again on T+1 re-sends the same orders against a book
#: that has not moved yet.
_SIT_OUT_KEY = "_strategy_pairs_sit_out"
#: The share of a same-tick funding sell the broker lets a buy spend
#: (nexus_broker_utils.buy_ceiling).
_PENDING_SELL_CREDIT = 0.95


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _bars_for(data, symbol):
    """The engine hands bars as either {sym: {"bars": [...]}} or {sym: [...]}."""
    if not isinstance(data, dict):
        return []
    entry = data.get(symbol)
    if isinstance(entry, dict):
        return entry.get("bars") or []
    return entry or []


def _log_once(cache, reason, scope, msg, color="white"):
    """Log a standing condition at most once per `scope`."""
    seen = cache.get(_LOGGED_KEY)
    if not isinstance(seen, dict):
        seen = {}
        cache[_LOGGED_KEY] = seen
    if seen.get(reason) == scope:
        return
    seen[reason] = scope
    _log(msg, color)


def _key(pair) -> str:
    return f"{pair[0]}/{pair[1]}"


class StrategyPairs:
    """The class name is NOT free: broker.py resolves a run-once strategy by
    CamelCasing its id (`strategy_pairs` -> `StrategyPairs`) and runs the whole
    backtest inert when it misses."""

    def run_once(self, symbols, prices, current_time, config, conditions,
                 data=None, portfolio_emulator=None, strategy_cache=None,
                 time_increment=None, mode=None, **kwargs):
        cfg = {**DEFAULTS, **(config or {})}
        if not _truthy(cfg.get("strategy_pairs_enabled", False)):
            return {}
        if portfolio_emulator is None:
            return {}

        cache = strategy_cache if isinstance(strategy_cache, dict) else {}
        if cache.pop(_SIT_OUT_KEY, False):
            return {}
        pairs = pairs_of(cfg)
        universe = strategy_pairs_universe(cfg)
        if not pairs:
            _log_once(cache, "nopairs", "x",
                      "StrategyPairs: REFUSING to trade — no valid pairs.", "red")
            return {}

        # THE point-in-time boundary: strictly-earlier NY sessions only.
        observations = {sym: pit_daily_observations(_bars_for(data, sym),
                                                    current_time)
                        for sym in universe}
        sessions = [obs[-1][0] for obs in observations.values() if obs]
        if not sessions:
            _log_once(cache, "blind", str(current_time)[:10],
                      "StrategyPairs: REFUSING to trade — no visible daily "
                      "closes for any leg.", "red")
            return {}
        session_id = max(sessions)

        # ONCE PER SESSION. `run_once` is called per tick; the z-score machine
        # steps on sessions, and a second tick over the same closes must replay
        # the cached answer rather than advance it.
        stored = cache.get(_SIGNALS_KEY)
        stored = stored if isinstance(stored, dict) else {}
        if cache.get(_SESSION_KEY) == session_id and stored:
            signals = {pair: int(stored.get(_key(pair), 0)) for pair in pairs}
        else:
            signals = {}
            for pair in pairs:
                closes_a, closes_b = aligned_closes(observations[pair[0]],
                                                    observations[pair[1]])
                signals[pair] = pair_signal(closes_a, closes_b,
                                            stored.get(_key(pair), 0), cfg)
            cache[_SESSION_KEY] = session_id
            cache[_SIGNALS_KEY] = {_key(p): s for p, s in signals.items()}

        targets = pairs_targets(signals, cfg)

        # A declared leg the broker did not quote falls back to its last
        # VISIBLE close, the same number a quote would carry on this bar.
        eff = {str(s).strip().upper(): v for s, v in (prices or {}).items()}
        for symbol in universe:
            if float(eff.get(symbol) or 0.0) > 0:
                continue
            obs = observations.get(symbol)
            if obs and float(obs[-1][1]) > 0:
                eff[symbol] = float(obs[-1][1])

        # A leg with no price at all would be skipped by `targets_to_orders`
        # and its weight silently concentrated elsewhere. Leave it in cash.
        missing = sorted(s for s in targets if float(eff.get(s) or 0.0) <= 0)
        for symbol in missing:
            targets.pop(symbol, None)
        if missing:
            _log_once(cache, "unpriced", f"{session_id}:{','.join(missing)}",
                      f"StrategyPairs {session_id} | no price for "
                      f"{', '.join(missing)} — that weight stays in cash", "red")

        nav = float(portfolio_emulator.get_portfolio_value(eff) or 0.0)
        if nav <= 0:
            return {}
        positions = portfolio_emulator.get_positions() or {}
        cash = float(portfolio_emulator.get_cash() or 0.0)
        # Buying power counts this bar's own funding sell when the lane sets
        # `backtest_credit_pending_sell_proceeds`, so a rotation does not spend
        # a session in cash.
        try:
            bp = portfolio_emulator.get_buying_power(prices=eff)
            if bp is not None:
                cash = max(cash, float(bp))
        except (AttributeError, TypeError):
            pass

        # Idle cash above the band (a flip whose buy was clipped, a dividend)
        # is put to work by trading the whole book to target once, band off.
        order_cfg = cfg
        band = max(0.0, _f(cfg, "core_band_pct"))
        if cash / nav > band:
            order_cfg = {**cfg, "core_band_pct": 0.0}
        # A rotation sells one leg and buys the other in the SAME payload, and
        # `targets_to_orders` sizes the buy from cash on hand, which does not
        # yet hold the sale. Measured on BT 543938: a $97,543 sell went out
        # beside a $2,061 buy and the book averaged 35% cash. With the lane's
        # pending-sell credit on, the broker lets a buy spend 95% of the
        # same-tick sell, so the buy is sized against that here.
        if _truthy(cfg.get("backtest_credit_pending_sell_proceeds", False)):
            exits = 0.0
            for symbol in universe:
                value = (float(positions.get(symbol) or 0.0)
                         * float(eff.get(symbol) or 0.0))
                excess = value - targets.get(symbol, 0.0) * nav
                if excess > 0 and (targets.get(symbol, 0.0) <= 0
                                   or excess / nav > band):
                    exits += excess
            cash += _PENDING_SELL_CREDIT * exits
        decisions, sizes = targets_to_orders(
            targets, nav=nav, positions=positions, prices=eff, cash=cash,
            config=order_cfg, owned=set(universe))

        tilted = sorted(f"{_key(p)}:{'+' if s > 0 else '-'}"
                        for p, s in signals.items() if s)
        cache[_LAST_DECISION_KEY] = {
            "session": session_id, "mode": pairs_mode(cfg),
            "signals": {_key(p): s for p, s in signals.items()},
            "targets": dict(targets), "orders": len(decisions),
        }
        if decisions:
            _log(f"StrategyPairs {session_id} | {pairs_mode(cfg)} | tilts="
                 f"{' '.join(tilted) or 'none'} | orders={len(decisions)} | "
                 f"nav=${nav:,.0f}", "cyan")
        if not decisions:
            return {}
        cache[_SIT_OUT_KEY] = True

        out = dict(decisions)
        sizes = dict(sizes)
        # The broker's buy gate reserves 10% of STARTING value as a cash floor
        # sized for a discovery book; here it would leave a tenth of the book
        # idle and the comparison against the control would measure cash drag.
        sizes["_cash_reserve_floor_pct"] = 0.0
        out["_nexus_position_sizes"] = sizes
        out["_nexus_discovered"] = list(universe)
        out["_nexus_executable_buys"] = [s for s, d in decisions.items() if d == 1]
        out["_nexus_sell_enforcement"] = [s for s, d in decisions.items() if d == -1]
        out["_nexus_action_intents"] = {s: _SELL_INTENT
                                        for s, d in decisions.items() if d == -1}
        return out
