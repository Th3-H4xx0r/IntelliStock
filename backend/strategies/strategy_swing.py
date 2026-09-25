# INTELLISTOCK_SCHEMA: {"strategy": "strategy_swing", "weight": 1.0, "execution_position": 10, "decision_phase": "pre", "execution_scope": "run_once", "conditions": {}, "config": {"strategy_swing_enabled": false, "rsi_period": 14, "rsi_entry_max": 50, "rsi_overbought": 70, "sma_long": 200, "macd_fast": 12, "macd_slow": 26, "macd_signal": 9, "vol_avg_period": 20, "adx_period": 14, "adx_min": 15, "spy_buffer": 1.03, "vix_max": 25.0, "position_size_pct": 0.125, "max_positions": 8, "max_per_sector": 1, "profit_target": 0.09, "stop_loss": 0.06, "bear_regime_days": 10, "defensive_universe": ["XLP", "XLU", "XLV", "GLD", "SHY"], "earnings_hard_block_days": 5, "ai_gate_enabled": true, "ai_approve_threshold": 75, "ai_review_threshold": 50, "conviction_llm_model_id": "", "scan_time_et": "09:15", "live_max_order_fraction": 0.2, "live_max_symbol_fraction": 0.2, "live_max_leveraged_fraction": 0.2, "live_soft_drawdown": 0.25, "live_hard_drawdown": 0.35, "live_kill_drawdown": 0.45, "honour_single_position_cap": true, "broker_max_single_position_pct": 0.2}}
# INTELLISTOCK_DESCRIPTION: ST's swing strategy (github.com/tmasters2876/swing-trader), ported verbatim: buy an S&P 500 name when RSI(14) is under 50 and rising, the MACD histogram improves, volume beats its 20-day average, price is above its 200-day SMA and ADX is over 15 — while SPY is above its SMA200 × 1.03 and VIX is at most 25, else the defensive ETFs after 10 blocked sessions. 12.5% of equity per name, 8 names, 1 per sector, a −6%/+9% bracket anchored on the prior close, and an RSI-70 cross exit. Live, the linked conviction model scores each candidate: 75+ enters, 50–74 waits for your approval.
"""Strategy Swing wrapper: the swing lane of the swing-trader port.

ST's pure logic lives in backend/swing_trader/. This file owns what needs the
broker: the emulator (or live adapter), the strategy cache, the resumable
live scan, and the decision payload (interface contract §1).

Spec: docs/superpowers/specs/2026-09-24-swing-trader-port-design.md §5.1
"""
import math
import os
import re
import sys
from datetime import date, datetime, timezone

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from db import store  # noqa: E402  (tests monkeypatch this name)
from swing_trader import (  # noqa: E402
    account,
    ai_analyst,
    calibration,
    clock,
    indicators,
    market_data,
    notify,
    refdata,
    regime,
    sectors,
    signals,
    signals_store,
    universe,
)
from swing_trader.constants import SWING_DEFAULTS as DEFAULTS  # noqa: E402

# Route through intellistock_logger: the backtest engine discards container
# stdout on success, and BacktestResults.logs is the log an operator reads.
try:
    from intellistock_logger import intellistock_logger as _ilog  # type: ignore

    def _log(msg, color="white"):
        _ilog.log(str(msg), color, service="StrategySwing")
except Exception:  # pragma: no cover - standalone/test import
    def _log(msg, color="white"):
        print(f"[StrategySwing] {msg}")


INTENT_ENTRY = "swing_entry"
INTENT_DEFENSIVE = "swing_defensive_entry"
INTENT_EXIT = {"rsi_overbought": "swing_rsi_exit", "stop_loss": "swing_stop_exit",
               "profit_target": "swing_target_exit"}

#: Every cache key this wrapper owns carries the `_swing_` prefix.
_BT_SESSION_KEY = "_swing_bt_session"          # backtest: the session decided
_IND_MEMO_KEY = "_swing_ind_memo"              # backtest: indicators of that session
_BEAR_KEY = "_swing_bear"                      # {"session", "blocked_days", ...} (fix 11)
_SECTOR_MAP_KEY = "_swing_sector_map"          # backtest: SwingSectorMap, read once
_LOGGED_KEY = "_swing_logged"                  # {reason: scope} for _log_once

#: Hint flags the engine tests with `is True` (plan A-backtest Task 6).
_HINT_FLAGS = ("whole_shares", "fill_at_next_open")
#: A bar spacing at or above this is a daily bar (market_data uses the same cut).
_DAILY_MIN_GAP_S = 23 * 3600
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def _log_once(cache, reason, scope, msg, color="white"):
    """Log a standing condition at most once per `scope` (a session)."""
    seen = cache.get(_LOGGED_KEY)
    if not isinstance(seen, dict):
        seen = {}
        cache[_LOGGED_KEY] = seen
    if seen.get(reason) == scope:
        return
    seen[reason] = scope
    _log(msg, color)


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _list(value) -> list:
    if isinstance(value, str):
        return [s.strip().upper() for s in value.split(",") if s.strip()]
    return [str(s).strip().upper() for s in (value or []) if str(s).strip()]


def _price(value):
    """A finite positive float, or None."""
    try:
        p = float(value)
    except (TypeError, ValueError):
        return None
    return p if math.isfinite(p) and p > 0 else None


def _sector_reader(smap, **kw):
    """sectors.get_symbol_sector with an empty answer read as "unknown"
    (G2 minor): sector_conflict exempts only the literal "unknown", so two
    names yfinance gives no sector must not share a "" sector."""
    def sector_of(symbol):
        return str(sectors.get_symbol_sector(symbol, smap, **kw) or "").strip() or "unknown"
    return sector_of


def swing_indicators(frames, cfg) -> dict:
    """ST's get_latest_indicators over {symbol: daily frame}. A module-level
    seam so the wrapper tests can hand in exact indicator snapshots.

    G1 minor 5: one symbol at a time, so a frame missing a column or holding
    a bad value skips that symbol (logged) and the scan carries on."""
    out, skipped = {}, []
    for symbol, frame in (frames or {}).items():
        try:
            out.update(indicators.indicators_for_frames({symbol: frame}, cfg))
        except Exception as exc:
            skipped.append(f"{symbol} ({type(exc).__name__}: {exc})")
    if skipped:
        _log(f"StrategySwing | indicators skipped for {len(skipped)} symbol(s), the scan "
             f"continues: {'; '.join(skipped[:10])}"
             + (f"; +{len(skipped) - 10} more" if len(skipped) > 10 else ""), "yellow")
    return out


def _emit(decisions, sizes, intents) -> dict:
    """The broker payload (contract §1). Nothing to do is {}, as EB returns."""
    if not decisions:
        return {}
    out = dict(decisions)
    sizes = dict(sizes)
    for hint in sizes.values():
        if isinstance(hint, dict):
            for flag in _HINT_FLAGS:
                if flag in hint:
                    hint[flag] = bool(hint[flag])   # a numpy.bool_ fails `is True`
    # The broker's buy gate otherwise reserves `_cash_reserve_floor_pct`
    # (default 0.10) of starting value as untouchable cash, sized for a
    # many-name discovery book. The swing lane's own sizing is ST's: 12.5% of
    # equity per name and a half-allocation buying-power check.
    sizes["_cash_reserve_floor_pct"] = 0.0
    out["_nexus_position_sizes"] = sizes
    out["_nexus_discovered"] = sorted(decisions)
    out["_nexus_executable_buys"] = sorted(s for s, d in decisions.items() if d == 1)
    out["_nexus_sell_enforcement"] = sorted(s for s, d in decisions.items() if d == -1)
    out["_nexus_action_intents"] = {s: intents[s] for s in sorted(decisions) if s in intents}
    return out


def _exits(ind, positions, entry_of, cfg, decisions, sizes, intents) -> dict:
    """paper_trader.py:536-587: ST's exit_signal on every held name with data.
    Returns {symbol: reason} for the exits emitted. One bad row skips its
    symbol (G1 minor 5)."""
    out = {}
    kw = signals.exit_kwargs(cfg)
    for symbol in sorted(positions):
        i = ind.get(symbol)
        if not i:
            continue
        try:
            entry = entry_of(symbol)
            if not entry or entry <= 0:
                continue
            should_exit, reason = signals.exit_signal(i, float(entry), **kw)
        except Exception as exc:
            _log(f"StrategySwing | {symbol}: exit check skipped ({type(exc).__name__}: "
                 f"{exc}) — the scan continues", "yellow")
            continue
        if should_exit:
            decisions[symbol] = -1
            sizes[symbol] = {"sell_fraction": 1.0, "fill_at_next_open": True}
            intents[symbol] = INTENT_EXIT[reason]
            out[symbol] = reason
    return out


def _increment_seconds(value):
    """The engine's time_increment ("900", "86400", "15m", "1d") in seconds,
    or None when it is absent or unreadable."""
    s = str(value or "").strip().lower()
    if not s:
        return None
    if s.isdigit():
        return int(s)
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([smhd])[a-z]*", s)
    return int(float(m.group(1)) * _UNIT_SECONDS[m.group(2)]) if m else None


def _entry_bars(data, symbol):
    entry = data.get(symbol) if isinstance(data, dict) else None
    if isinstance(entry, dict):
        return entry.get("bars") or []
    return entry or []


def _sub_daily_reason(data, time_increment):
    """Why the engine's bars are not daily, or None (G2 I2). ST's indicators
    are RTH daily bars; a 15-minute roll-up would fold extended-hours prints
    into SMA200 and RSI, so the swing lab runs at 86400 only."""
    secs = _increment_seconds(time_increment)
    if secs is not None and secs < 86400:
        return f"the run steps every {secs}s (time_increment {time_increment!r})"
    names = sorted(data) if isinstance(data, dict) else []
    for symbol in (["SPY"] if "SPY" in names else []) + [s for s in names if s != "SPY"][:5]:
        stamps = sorted(ts for ts in (clock.as_utc((b or {}).get("t") or (b or {}).get("timestamp")
                                                   or (b or {}).get("date"))
                                      for b in _entry_bars(data, symbol) if isinstance(b, dict))
                        if ts is not None)
        gaps = [(b - a).total_seconds() for a, b in zip(stamps, stamps[1:]) if b > a]
        if gaps:
            if min(gaps) < _DAILY_MIN_GAP_S:
                return f"{symbol} bars are {int(min(gaps))}s apart"
            return None
    return None


class StrategySwing:
    # The class name is NOT free: broker.py resolves a run-once strategy by
    # CamelCasing its id — strategy_swing -> StrategySwing — and runs the whole
    # backtest inert when it misses.

    def run_once(self, symbols, prices, current_time, config, conditions,
                 data=None, portfolio_emulator=None, strategy_cache=None,
                 time_increment=None, mode=None, **kwargs):
        cfg = {**DEFAULTS, **(config or {})}
        if not _truthy(cfg.get("strategy_swing_enabled", False)):
            return {}
        cache = strategy_cache if isinstance(strategy_cache, dict) else {}
        if portfolio_emulator is None:
            _log_once(cache, "no-emulator", str(current_time)[:10],
                      "StrategySwing: REFUSING to trade — no portfolio emulator, so "
                      "the book cannot be read at all.", "red")
            return {}
        if data is not None:
            return self._backtest(prices, current_time, cfg, data, portfolio_emulator, cache,
                                  time_increment)
        return self._live(prices, current_time, cfg, portfolio_emulator, cache, mode)

    # -- backtest ------------------------------------------------------------

    def _sector_map(self, cache, names) -> dict:
        smap = cache.get(_SECTOR_MAP_KEY)
        if not isinstance(smap, dict):
            try:
                smap = refdata.sector_map(store, names)
            except Exception as exc:
                _log(f"StrategySwing: SwingSectorMap unreadable ({type(exc).__name__}: "
                     f"{exc}) — every sector reads 'unknown'", "yellow")
                smap = {}
            cache[_SECTOR_MAP_KEY] = smap
        return smap

    def _backtest(self, prices, current_time, cfg, data, emu, cache, time_increment=None):
        session = clock.ny_date(current_time)
        if cache.get(_BT_SESSION_KEY) == session:
            return {}
        # F6: the engine's legacy window ticks on weekdays, NYSE holidays
        # included; a holiday would re-decide the next session on the same
        # bars and could queue a second next-open sell.
        if not clock.is_trading_day(date.fromisoformat(session)):
            return {}
        sub_daily = _sub_daily_reason(data, time_increment)
        if sub_daily:
            _log_once(cache, "sub-daily", "run",
                      f"StrategySwing | REFUSING to trade — {sub_daily}. ST's rules read "
                      "RTH daily bars; run the swing lab at granularity 86400 (daily bars).",
                      "red")
            return {}
        names = sorted(str(s).upper() for s in data) if isinstance(data, dict) else []

        memo = cache.get(_IND_MEMO_KEY)
        if isinstance(memo, dict) and memo.get("session") == session:
            ind = memo["ind"]
        else:
            frames = market_data.frames_from_engine_bars(data, names, current_time)
            ind = swing_indicators(frames, cfg)
            cache[_IND_MEMO_KEY] = {"session": session, "ind": ind}

        spy = ind.get("SPY")
        if not spy:
            _log_once(cache, "no-spy", session,
                      f"StrategySwing {session} | REFUSING to trade — no SPY daily bars "
                      "before this session, so the regime cannot be read.", "red")
            return {}

        try:
            vix, vix_reason = refdata.vix_before(store, session)
        except Exception as exc:
            vix, vix_reason = None, f"SwingMacroDaily unreadable: {type(exc).__name__}: {exc}"
        if vix_reason:
            _log_once(cache, "vix", session,
                      f"StrategySwing {session} | VIX unavailable ({vix_reason}) — "
                      "regime blocked, as a missing VIX blocked ST.", "yellow")
        reg = regime.regime_decision(spy.get("close"), spy.get("sma200"), vix,
                                     spy_buffer=float(cfg["spy_buffer"]),
                                     vix_max=float(cfg["vix_max"]))
        bear = signals.update_regime_tracker(cache.get(_BEAR_KEY), reg["regime_ok"], session)
        cache[_BEAR_KEY] = bear

        decisions, sizes, intents = {}, {}, {}
        positions = {str(s).upper(): float(q or 0.0)
                     for s, q in (emu.get_positions() or {}).items() if float(q or 0.0) > 0}
        pending = account.pending_symbols(emu)
        # F6: a held name whose order has not executed yet is not sold again.
        sellable = {s: q for s, q in positions.items() if s not in pending}
        exited = _exits(ind, sellable, lambda s: account.entry_price_from_trades(emu, s), cfg,
                        decisions, sizes, intents)

        try:
            members = refdata.members_before(store, session)
        except Exception as exc:
            _log(f"StrategySwing {session} | SwingIndexMembership unreadable "
                 f"({type(exc).__name__}: {exc})", "red")
            members = None
        univ, phase = signals.select_entry_universe(
            reg["regime_ok"], bear["blocked_days"],
            bear_regime_days=int(cfg["bear_regime_days"]),
            live_universe=universe.order_like_live(list(members or []) + ["SPY", "QQQ"]),
            defensive_universe=_list(cfg["defensive_universe"]))
        if phase == "regime_ok" and members is None:
            _log_once(cache, "no-membership", session,
                      f"StrategySwing {session} | REFUSING entries — no SwingIndexMembership "
                      "row before this session; entering off today's list would be "
                      "survivorship-biased. Run scripts/build_swing_reference_data.py.", "red")
            univ = None

        if univ:
            smap = self._sector_map(cache, names)
            eff = {}
            for s, v in (prices or {}).items():
                p = _price(v)
                if p is not None:
                    eff[str(s).upper()] = p
            for s, i in ind.items():
                if s not in eff:
                    p = _price(i.get("close"))
                    if p is not None:
                        eff[s] = p
            equity = float(emu.get_portfolio_value(eff) or 0.0)
            bp = account.spendable(emu, eff)
            # F1: ST credited each exit's proceeds before the entry pass
            # (paper_trader.py:585), at the close the exit was decided on.
            bp += sum(positions[s] * float(ind[s]["close"]) for s in exited)
            active = (set(positions) | pending) - set(exited)
            self._entries(univ, ind, active, cfg, equity, bp, _sector_reader(smap), phase,
                          decisions, sizes, intents)

        cache[_BT_SESSION_KEY] = session
        if decisions:
            _log(f"StrategySwing {session} | regime "
                 f"{'ACTIVE' if reg['regime_ok'] else 'BLOCKED'} ({phase}) | "
                 f"entries={sorted(s for s, d in decisions.items() if d == 1)} "
                 f"exits={sorted(s for s, d in decisions.items() if d == -1)}", "cyan")
        return _emit(decisions, sizes, intents)

    def _entries(self, univ, ind, active, cfg, equity, bp, sector_of, phase,
                 decisions, sizes, intents):
        """paper_trader.py:626-756 without the AI gate or the earnings block
        (ST's backtester ran neither). One bad row skips its symbol (G1 minor 5)."""
        entries_placed = 0
        available_slots = int(cfg["max_positions"]) - len(active)
        ekw = signals.entry_kwargs(cfg)
        size_pct = float(cfg["position_size_pct"])
        stop_loss, profit_target = float(cfg["stop_loss"]), float(cfg["profit_target"])
        max_per_sector = int(cfg["max_per_sector"])
        for symbol in univ:
            if symbol in active or symbol not in ind:
                continue
            i = ind[symbol]
            try:
                if not signals.entry_signal(i, **ekw):
                    continue
                if signals.sector_conflict(symbol, active, sector_of=sector_of,
                                           max_per_sector=max_per_sector):
                    continue
                close = float(i["close"])
            except Exception as exc:
                _log(f"StrategySwing | {symbol}: entry check skipped ({type(exc).__name__}: "
                     f"{exc}) — the scan continues", "yellow")
                continue
            if entries_placed >= available_slots:
                continue
            alloc = equity * size_pct
            if bp < alloc * 0.5:
                continue
            use = min(alloc, bp)
            shares = int(use / close)
            if shares < 1:
                continue
            decisions[symbol] = 1
            sizes[symbol] = {
                "buy_cash": round(use, 2),
                "bracket": {"take_profit_price": round(close * (1 + profit_target), 2),
                            "stop_loss_price": round(close * (1 - stop_loss), 2)},
                "whole_shares": True,
                "fill_at_next_open": True,
            }
            intents[symbol] = INTENT_DEFENSIVE if phase == "bear_mode" else INTENT_ENTRY
            bp -= shares * close
            entries_placed += 1
            active.add(symbol)               # fix 4: the sector set follows each buy

    # -- live (Task 17) --------------------------------------------------------

    def _live(self, prices, current_time, cfg, emu, cache, mode):
        _log_once(cache, "live-not-built", clock.ny_date(current_time),
                  "StrategySwing: the live path is not built yet (plan B Task 17).", "red")
        return {}
