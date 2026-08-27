# INTELLISTOCK_SCHEMA: {"strategy": "strategy_eb", "weight": 1.0, "execution_position": 10, "decision_phase": "pre", "execution_scope": "run_once", "conditions": {}, "config": {"strategy_eb_enabled": false, "core_symbol": "TQQQ", "core_leverage": 3.0, "reference_symbol": "QQQ", "off_symbol": "SPY", "cash_symbol": "BIL", "target_vol": 0.2, "core_max_weight": 0.65, "weight_step": 0.05, "vol_fast_bars": 20, "vol_slow_bars": 60, "min_history_bars": 70, "core_rebalance_band": 0.1, "rebalance_weekdays": [2], "remainder_bil_fraction": 0.0, "cash_sweep_min_pct": 0.02, "core_band_pct": 0.03, "min_order_usd": 25.0, "cost_haircut_pct": 0.005, "broker_max_single_position_pct": 0.95, "honour_single_position_cap": true, "live_max_order_fraction": 0.7, "live_max_symbol_fraction": 0.7, "live_max_leveraged_fraction": 0.7, "live_soft_drawdown": 0.25, "live_hard_drawdown": 0.35, "live_kill_drawdown": 0.45}}
# INTELLISTOCK_DESCRIPTION: Efficient beta — a volatility-targeted leveraged Nasdaq core with the de-levered remainder in SPY, rebalanced once a week on a fixed weekday, every weight quantized and banded so it trades rarely. A risk transform, not an alpha: it makes no directional prediction and holds less of the same position when that position is more dangerous. One lever, remainder_bil_fraction, moves the remainder from SPY toward T-bills, trading CAGR for drawdown.
"""Strategy EB wrapper: cache state, order emission, broker contract.

Everything testable lives in `backend/strategy_eb.py`, which is pure. This file
owns only what needs the broker: the emulator, the cache, and the decision row.

Design: docs/superpowers/specs/2026-08-27-strategy-eb-design.md
"""
import os
import sys

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from strategy_eb import (  # noqa: E402
    DEFAULTS,
    LAST_REBALANCE_KEY,
    _f,
    _s,
    eb_core_weight,
    eb_remainder_targets,
    eb_should_trade,
    eb_targets,
    strategy_eb_universe,
)
from strategy_x import (  # noqa: E402
    pit_daily_observations,
    targets_to_orders,
)

# Route through intellistock_logger, NOT print() and NOT utils.log_message. The
# backtest engine runs the broker with `detach=False, remove=True` and DISCARDS
# container stdout on success, so a print() goes nowhere durable — the one line
# that would expose an inert run would be invisible. intellistock_logger fans
# out to the backtest log buffer, which becomes BacktestResults.logs, the only
# log an operator actually reads. Strategy XS used utils.log_message and its
# lines never reached the sink.
try:
    from intellistock_logger import intellistock_logger as _ilog  # type: ignore

    def _log(msg, color="white"):
        _ilog.log(str(msg), color, service="StrategyEb")
except Exception:  # pragma: no cover - standalone/test import
    def _log(msg, color="white"):
        print(f"[StrategyEb] {msg}")


#: Every Strategy EB exit is a rebalance of an ETF book, which is what the
#: broker's sell whitelist calls `etf_sell`. Publishing it is not cosmetic:
#: broker.py's Z2.1 check reads `action_intent` off the strategy summary, and a
#: sell with no recognised intent logs would_block_in_phase2=True. Measured on
#: Strategy X's BT406990, that was 965 of 965 sells — the whole book.
_SELL_INTENT = "etf_sell"

#: Every cache key this wrapper owns carries the `_strategy_eb_` prefix, so a
#: shared `strategy_cache` shows at a glance which strategy wrote what.
_LAST_DECISION_KEY = "_strategy_eb_last"

#: The session an exit-to-zero was last ISSUED in. `eb_should_trade` lets an
#: exit bypass the same-session guard on purpose — waiting even one more session
#: to leave a 3x fund is the failure the vol transform exists to prevent — but
#: equity fills are NEXT-BAR, so at 15m granularity the emulator still reports
#: the position on each of the ~26 remaining ticks and the identical exit would
#: be re-sent every one of them. Deduping by session keeps the exit immediate
#: and idempotent: a LATER session re-arms it, because there the exit really did
#: fail to fill.
_EXIT_ISSUED_KEY = "_strategy_eb_exit_issued_session"

#: The session a cash sweep was last issued in — same next-bar-fill reasoning:
#: the cash is still settled on the following tick, so an unguarded sweep would
#: re-send the identical buy on every one of them.
_SWEEP_ISSUED_KEY = "_strategy_eb_sweep_session"

#: {reason: scope} for refusals already logged, so a strategy that refuses all
#: day writes one line rather than ~26.
_LOGGED_KEY = "_strategy_eb_logged"


def _log_once(cache, reason, scope, msg, color="white"):
    """Log a recurring refusal at most once per `scope`.

    Every refusal in `run_once` is a STANDING condition — a short history, a
    blind tick — so it repeats identically on all ~26 ticks of a session. The
    sink is BacktestResults.logs, the log an operator actually reads by eye,
    and drowning it is how a real refusal goes unnoticed.
    """
    seen = cache.get(_LOGGED_KEY)
    if not isinstance(seen, dict):
        seen = {}
        cache[_LOGGED_KEY] = seen
    if seen.get(reason) == scope:
        return
    seen[reason] = scope
    _log(msg, color)


def _emit(decisions, sizes, universe) -> dict:
    """The broker payload for a set of decisions.

    `_nexus_action_intents` is not cosmetic: broker.py's Z2.1 check reads it off
    the strategy summary and a sell with no recognised intent logs
    would_block_in_phase2=True.
    """
    out = dict(decisions)
    out["_nexus_position_sizes"] = sizes
    out["_nexus_discovered"] = list(universe)
    out["_nexus_executable_buys"] = [s for s, d in decisions.items() if d == 1]
    out["_nexus_sell_enforcement"] = [s for s, d in decisions.items()
                                      if d == -1]
    out["_nexus_action_intents"] = {s: _SELL_INTENT
                                    for s, d in decisions.items() if d == -1}
    return out


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


class StrategyEb:
    # The class name is NOT free. `broker.py` resolves a run-once strategy by
    # CamelCasing its id — `strategy_eb` -> `StrategyEb` — and logs
    # "Class not found ... has no run_once method; skipping" when it misses,
    # then runs the whole backtest inert.

    def run_once(self, symbols, prices, current_time, config, conditions,
                 data=None, portfolio_emulator=None, strategy_cache=None,
                 time_increment=None, mode=None, **kwargs):
        cfg = {**DEFAULTS, **(config or {})}
        if not _truthy(cfg.get("strategy_eb_enabled", False)):
            return {}
        if portfolio_emulator is None:
            return {}

        cache = strategy_cache if isinstance(strategy_cache, dict) else {}
        universe = strategy_eb_universe(cfg)
        reference = _s(cfg, "reference_symbol")

        # THE point-in-time boundary: strictly-earlier NY sessions only. At the
        # 15m/1h cadence these backtests actually run, "today's daily bar" IS
        # that session's 16:00 close — six hours in the future of a 09:45
        # decision.
        observations = pit_daily_observations(_bars_for(data, reference),
                                              current_time)
        if not observations:
            # No session is knowable here, so the throttle falls back to the
            # call DATE — enough to collapse a whole day of blind ticks.
            _log_once(cache, "blind", str(current_time)[:10],
                      f"StrategyEb: REFUSING to trade — no visible {reference} "
                      "daily closes. Live passes data=None; a strategy that "
                      "cannot evaluate its own risk must do NOTHING.", "red")
            return {}
        session_id = observations[-1][0]
        closes = [close for _, close in observations]

        weight = eb_core_weight(closes, cfg)
        if weight is None:
            _log_once(cache, "unmeasurable", session_id,
                      f"StrategyEb: REFUSING to trade — {len(closes)} "
                      f"{reference} closes, need "
                      f"{cfg.get('min_history_bars')}, or realised volatility "
                      "is not finite and positive. A cold start must never "
                      "silently lever up.", "red")
            return {}

        # Prices the broker did not carry: the declared legs are absent from the
        # operator's watchlist, so fall back to the last VISIBLE close, which is
        # the same number a quote would carry on this bar.
        eff = {str(s).strip().upper(): v for s, v in (prices or {}).items()}
        for symbol in universe:
            if float(eff.get(symbol) or 0.0) > 0:
                continue
            visible = pit_daily_observations(_bars_for(data, symbol),
                                             current_time)
            if visible and float(visible[-1][1]) > 0:
                eff[symbol] = float(visible[-1][1])

        nav = float(portfolio_emulator.get_portfolio_value(eff) or 0.0)
        if nav <= 0:
            return {}
        positions = portfolio_emulator.get_positions() or {}
        core = _s(cfg, "core_symbol")
        held = (float(positions.get(core) or 0.0)
                * float(eff.get(core) or 0.0)) / nav

        trade, effective_weight = eb_should_trade(session_id, weight, held,
                                                  cfg, cache)
        if not trade:
            # No core order is due, so idle cash gets a second chance. The
            # sweep is deliberately outside the weekday cadence and the band:
            # both are about the CORE, and neither ever re-examines a balance
            # the core plan failed to spend.
            return self._sweep(cfg, cache, session_id, universe, eff, nav,
                               positions, portfolio_emulator, held)

        # An exit already issued in THIS session is in flight, not ignored. No
        # sweep here either: the core sell has not settled, so the cash it will
        # free is not the account's to spend yet.
        exiting = effective_weight <= 0.0
        if exiting and cache.get(_EXIT_ISSUED_KEY) == session_id:
            return {}

        targets = eb_targets(effective_weight, cfg)
        cash = float(portfolio_emulator.get_cash() or 0.0)
        decisions, sizes = targets_to_orders(
            targets, nav=nav, positions=positions, prices=eff, cash=cash,
            config=cfg, owned=set(universe))

        # Written whether or not orders came out: the session HAS been decided,
        # and at 15m granularity there are ~26 more ticks in it.
        cache[LAST_REBALANCE_KEY] = session_id
        if exiting:
            cache[_EXIT_ISSUED_KEY] = session_id
        cache[_LAST_DECISION_KEY] = {
            "session": session_id,
            "core_weight": weight,
            "effective_weight": effective_weight,
            "held_weight": round(held, 6),
            "targets": dict(targets),
            "orders": len(decisions),
        }
        _log(f"StrategyEb {session_id} | core {core} target {weight:.0%} "
             f"(held {held:.0%} -> {effective_weight:.0%}) | targets="
             + ", ".join(f"{s} {w:.1%}" for s, w in sorted(targets.items()))
             + f" | orders={len(decisions)} | nav=${nav:,.0f}", "cyan")

        if not decisions:
            return {}
        return _emit(decisions, sizes, universe)

    def _sweep(self, cfg, cache, session_id, universe, prices, nav, positions,
               emulator, held) -> dict:
        """Re-offer an idle cash balance to the REMAINDER legs.

        `targets_to_orders` sizes buys off SETTLED cash, and equity fills are
        next-bar, so the tick that sells the core cannot also fund the SPY leg
        that was meant to replace it — that buy is clipped to whatever cash had
        already settled. Nothing else ever revisits it: on later sessions the
        band sees no breach, and after a full exit the cadence rule returns
        (False, 0.0). Measured over a backtest that is a book drifting to cash,
        which is not the strategy at all.

        The core is outside both the targets and the `owned` scope, so a sweep
        can neither trim the core to fund the remainder nor top it up outside
        the weekly cadence. It only ever spends money that is already idle.
        """
        # A session that already sent a core plan has its remainder legs in
        # that plan, and those buys have NOT settled yet — cash still reads
        # full on every remaining tick. Sweeping on top of it spends the same
        # dollars twice. The sweep is for cash the core plan has finished with,
        # which means the NEXT session at the earliest.
        if cache.get(LAST_REBALANCE_KEY) == session_id:
            return {}
        if cache.get(_SWEEP_ISSUED_KEY) == session_id:
            return {}
        cash = float(emulator.get_cash() or 0.0)
        if nav <= 0 or cash <= 0 or (cash / nav) <= _f(cfg,
                                                       "cash_sweep_min_pct"):
            return {}

        targets = eb_remainder_targets(held, cfg)
        if not targets:
            return {}
        owned = {_s(cfg, "off_symbol"), _s(cfg, "cash_symbol")}
        owned.discard(_s(cfg, "core_symbol"))
        owned.discard("")

        decisions, sizes = targets_to_orders(
            targets, nav=nav, positions=positions, prices=prices, cash=cash,
            config=cfg, owned=owned)
        if not decisions:
            return {}

        cache[_SWEEP_ISSUED_KEY] = session_id
        _log(f"StrategyEb {session_id} | SWEEP ${cash:,.0f} idle "
             f"({cash / nav:.0%} of NAV) into "
             + ", ".join(f"{s} {w:.1%}" for s, w in sorted(targets.items()))
             + f" | orders={len(decisions)}", "cyan")
        return _emit(decisions, sizes, universe)
