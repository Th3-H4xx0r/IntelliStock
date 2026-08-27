# INTELLISTOCK_SCHEMA: {"strategy": "strategy_xs", "weight": 1.0, "execution_position": 10, "decision_phase": "pre", "execution_scope": "run_once", "conditions": {}, "config": {"strategy_xs_enabled": false, "core_bull_symbol": "QLD", "core_leverage_factor": 2.0, "core_weight": 0.857143, "core_cash_symbol": "BIL", "core_off_symbol": "SPY", "core_band_pct": 0.05, "core_filter_symbol": "SPY", "core_filter_ma_bars": 200, "core_vol_bars": 20, "core_vol_slow_bars": 60, "core_vol_gate_mult": 2.25, "core_vol_median_bars": 252, "core_vol_median_min_samples": 60, "core_vol_target": 0.0, "core_vol_scale_min": 0.3, "core_vol_scale_max": 1.0, "diversifier_pct": 0.3, "diversifier_symbols": ["GLD", "UUP"], "diversifier_min_history_bars": 60, "satellite_pct": 0.0, "satellite_max_names": 6, "inverse_symbol": "", "inverse_pct": 0.0, "min_order_usd": 50.0, "cost_haircut_pct": 0.006}}
# INTELLISTOCK_DESCRIPTION: Stacked growth — a trend-filtered levered core over an always-on diversifier basket.
"""Strategy XS wrapper: cache state, order emission, broker contract.

Everything testable lives in `backend/strategy_xs.py`, which is pure. This file
owns only what needs the broker: the emulator, the cache, and the decision row.

Design: docs/superpowers/specs/2026-08-27-strategy-xs-design.md
"""
import os
import sys

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from strategy_x import (  # noqa: E402
    core_signal,
    core_vol_scale,
    pit_daily_closes,
    targets_to_orders,
)
from strategy_xs import (  # noqa: E402
    DEFAULTS,
    diversifier_basket,
    strategy_xs_universe,
    xs_targets,
)

try:
    from utils import log_message as _log
except Exception:  # pragma: no cover - broker-only import
    def _log(msg, color="white"):
        print(msg)

#: Every Strategy XS exit is a rebalance of an ETF book, which is what the
#: broker's sell whitelist calls `etf_sell`. Publishing it is not cosmetic:
#: `broker.py`'s Z2.1 check reads `action_intent` off the strategy summary, and
#: a sell with no recognised intent logs `would_block_in_phase2=True`. Measured
#: on Strategy X's BT406990, that was 965 of 965 sells — the whole book.
_SELL_INTENT = "etf_sell"


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _bars_for(data, symbol):
    entry = (data or {}).get(symbol) or {}
    if isinstance(entry, dict):
        return entry.get("bars") or []
    return entry or []


class StrategyXS:
    def run_once(self, symbols, prices, current_time, config, conditions,
                 data=None, portfolio_emulator=None, strategy_cache=None,
                 time_increment=None, mode=None, **kwargs):
        cfg = {**DEFAULTS, **(config or {})}
        if not _truthy(cfg.get("strategy_xs_enabled", False)):
            return {}
        if portfolio_emulator is None:
            return {}

        prices = prices or {}
        cache = strategy_cache if isinstance(strategy_cache, dict) else {}
        filt = str(cfg.get("core_filter_symbol", "QQQ") or "QQQ").strip().upper()
        closes = pit_daily_closes(_bars_for(data, filt), current_time)

        ma_bars = max(2, int(cfg.get("core_filter_ma_bars", 200) or 200))
        if len(closes) < ma_bars:
            _log(f"StrategyXS: REFUSING to trade — {len(closes)} daily closes "
                 f"for {filt}, need {ma_bars}. The filter cannot arm, and "
                 "'risk-off' would be a real cash buy, not a flat.", "red")
            return {}

        sig = core_signal(closes, cfg)
        vol_scale = core_vol_scale(closes, cfg)

        # Prices the broker did not carry: the declared legs are absent from
        # the operator's watchlist, so fall back to the last VISIBLE close,
        # which is the same number a quote would carry on this bar.
        eff = {str(s).strip().upper(): v for s, v in prices.items()}
        for symbol in strategy_xs_universe(cfg):
            if float(eff.get(symbol) or 0.0) > 0:
                continue
            visible = pit_daily_closes(_bars_for(data, symbol), current_time)
            if visible and float(visible[-1]) > 0:
                eff[symbol] = float(visible[-1])

        member_closes = {
            s: pit_daily_closes(_bars_for(data, s), current_time)
            for s in (cfg.get("diversifier_symbols") or [])
        }
        basket = diversifier_basket(member_closes, eff, cfg)

        targets, notes = xs_targets(risk_on=bool(sig.risk_on), config=cfg,
                                    basket=basket, satellite_ranked=None,
                                    vol_scale=vol_scale)

        nav = float(portfolio_emulator.get_portfolio_value(eff) or 0.0)
        positions = portfolio_emulator.get_positions() or {}
        cash = float(portfolio_emulator.get_cash() or 0.0)
        owned = set(strategy_xs_universe(cfg))
        decisions, sizes = targets_to_orders(
            targets, nav=nav, positions=positions, prices=eff, cash=cash,
            config=cfg, owned=owned)

        cache["_strategy_xs_last"] = {
            "risk_on": bool(sig.risk_on), "reason": sig.reason,
            "targets": dict(targets), "basket": list(basket),
            "vol_scale": vol_scale, "notes": list(notes),
        }
        _log(f"StrategyXS {'RISK-ON' if sig.risk_on else 'RISK-OFF'} | "
             f"{sig.reason} | targets="
             + ", ".join(f"{s} {w:.1%}" for s, w in sorted(targets.items()))
             + f" | orders={len(decisions)} | nav=${nav:,.0f}", "cyan")
        for note in notes:
            _log(f"  {note}", "white")

        if not decisions:
            return {}
        out = dict(decisions)
        out["_nexus_position_sizes"] = sizes
        out["_nexus_discovered"] = strategy_xs_universe(cfg)
        out["_nexus_executable_buys"] = [s for s, d in decisions.items()
                                         if d == 1]
        out["_nexus_sell_enforcement"] = [s for s, d in decisions.items()
                                          if d == -1]
        out["_nexus_action_intents"] = {
            s: _SELL_INTENT for s, d in decisions.items() if d == -1}
        return out
