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
from datetime import date

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
_SCAN_KEY = "_swing_scan"                      # live: the resumable scan state
_SCAN_DONE_KEY = "_swing_scan_done_session"    # live: the session latch, on COMPLETION
_EMITTED_KEY = "_swing_emitted"                # live: entries sent this session
_NO_MODEL_KEY = "_swing_no_model_session"      # live: the no-model alert, once a session
_SECTOR_CACHE_KEY = "_swing_sector_cache"      # live: yfinance fallback sectors
_PENDING_EXIT_KEY = "_swing_pending_exits"     # live: exits re-sent until the stock is gone
_SCAN_FIRST_KEY = "_swing_scan_first_tick"     # live: {"session", "at", "late"} (FW-str d)

#: Hint flags the engine tests with `is True` (plan A-backtest Task 6).
_HINT_FLAGS = ("whole_shares", "fill_at_next_open")
#: A bar spacing at or above this is a daily bar (market_data uses the same cut).
_DAILY_MIN_GAP_S = 23 * 3600
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}
#: Review fix c: while yfinance returns no VIX, the live scan waits for it
#: until this ET time, then proceeds with the regime blocked, as ST did.
_VIX_RETRY_UNTIL_ET = "10:00"
#: FW-str minor (d): the regular-hours open. A session whose FIRST scan tick
#: is at or after it plans no entries (ST's cron only ever ran at 09:15).
_MARKET_OPEN_ET = "09:30"


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
                _log(f"StrategySwing | {symbol}: exit check skipped — the held position has "
                     "no entry price", "yellow")
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


def _yf_symbol(symbol) -> str:
    """yfinance spells class shares with a dash (BRK-B), Alpaca with a dot."""
    return str(symbol).replace(".", "-")


def _enum_text(value) -> str:
    return str(getattr(value, "value", value) or "").strip().lower().rsplit(".", 1)[-1]


def _working_exit(order) -> bool:
    """A working SELL that is not a bracket leg. Alpaca reports a bracket's
    take-profit and stop legs as sells of order_class "bracket" (the stop
    waits in "held"); those legs are what the engine cancels before it sells,
    so they never count as the exit being in flight."""
    return (_enum_text(getattr(order, "side", None)) == "sell"
            and _enum_text(getattr(order, "order_class", None)) not in {"bracket", "oco", "oto"})


_OCC_RE = re.compile(r"^[A-Z.]{1,6}\d{6}[CP]\d{8}$")


def _working_entry(order) -> bool:
    """A working stock BUY: an entry queued for the open (a bracket parent or
    an approved order). It holds a slot and a sector, as the backtest's
    pending_symbols do. A wheel buy-to-close is an option order, not one."""
    symbol = str(getattr(order, "symbol", "") or "").upper()
    return (_enum_text(getattr(order, "side", None)) == "buy"
            and _enum_text(getattr(order, "asset_class", None)) != "us_option"
            and not _OCC_RE.match(symbol))


def _symbols_of(orders, predicate) -> set:
    return {str(getattr(o, "symbol", "") or "").upper() for o in (orders or []) if predicate(o)}


def _valid_hhmm(value):
    """"HH:MM" for a real wall-clock time, else None (G2 minor): clock.parse_hhmm
    falls back to 00:00, which would scan at midnight."""
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", str(value if value is not None else ""))
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        return None
    return f"{int(m.group(1)):02d}:{m.group(2)}"


def _positions_for_scan(emu):
    """(positions, None, visible) — positions is {symbol: {"qty",
    "avg_entry_price", "market_value"}}, the book the scan counts exits, slots
    and sectors from — or (None, reason, visible) when the read is degraded
    (review fix a). `visible` is every name the read shows held, ready or not.
    account.equity_positions reads the same book but degrades to "no data",
    which here would mean free slots, skipped exits and forgotten ones.

    AlpacaAdapter.refresh_positions never raises. On a REST failure it returns
    the cached quantities with avg_entry_price=0.0; after 10 minutes, or in a
    process whose first refresh failed, it returns an empty book. Its
    staleness stamp (``stale_since`` of the public option_positions_health
    accessor, read through account.positions_health) is set while refreshes
    fail and is set BEFORE the call that clears the cache, so it is read on
    both sides; health that cannot be read is not ready.
    Every held name reading no entry price is that cached snapshot's
    signature; one such name among priced ones is only skipped by _exits
    (review round 2, finding 3)."""
    _complete, before, health_error = account.positions_health(emu)
    dtos, held, reason = [], None, None
    refresh = getattr(emu, "refresh_positions", None)
    if callable(refresh):
        try:
            dtos = list(refresh() or [])
        except Exception as exc:
            reason = f"refresh_positions failed ({type(exc).__name__}: {exc})"
    try:
        held = {str(s).upper(): float(q or 0.0) for s, q in (emu.get_positions() or {}).items()}
    except Exception as exc:
        reason = reason or f"get_positions failed ({type(exc).__name__}: {exc})"
    rows = {}
    for d in dtos:
        try:
            sym = str(getattr(d, "symbol", "") or "").upper()
            rows[sym] = {"qty": float(getattr(d, "qty", 0) or 0.0),
                         "avg_entry_price": float(getattr(d, "avg_entry_price", 0) or 0.0),
                         "market_value": float(getattr(d, "market_value", 0) or 0.0)}
        except (TypeError, ValueError):
            continue
    visible = ({s for s, q in held.items() if q > 0} if held is not None
               else {s for s, r in rows.items() if r["qty"] > 0})
    if reason:
        return None, reason, visible
    _complete, after, after_error = account.positions_health(emu)
    if health_error is not None or after_error is not None:
        return None, ("the broker's positions health could not be read "
                      f"({health_error or after_error})"), visible
    if before is not None or after is not None:
        return None, ("the broker's positions snapshot is stale (its REST refresh is "
                      "failing)"), visible
    out = {}
    for sym, qty in held.items():
        if qty > 0:
            row = rows.get(sym)
            out[sym] = (dict(row) if row and row["qty"] > 0
                        else {"qty": qty, "avg_entry_price": 0.0, "market_value": 0.0})
    entryless = []
    for sym, pos in sorted(out.items()):
        if _price(pos["avg_entry_price"]) is None:
            filled = _price(account.entry_price_from_trades(emu, sym))
            if filled is None:
                pos["avg_entry_price"] = 0.0      # _exits skips it, logged
                entryless.append(sym)
            else:
                pos["avg_entry_price"] = filled
    if out and len(entryless) == len(out):
        return None, (f"every held name reads no entry price ({', '.join(entryless)}) — "
                      "the signature of a cached snapshot"), visible
    return out, None, visible


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

    # -- live ----------------------------------------------------------------

    def _live(self, prices, current_time, cfg, emu, cache, mode):
        """Spec §5.1 live/paper. The scan starts at the first tick at or after
        scan_time_et, resumes on later ticks until every candidate is scored,
        and stamps the session latch when it COMPLETES: a crash mid-scan
        resumes from the persisted cursor instead of losing the day."""
        session = clock.ny_date(current_time)
        if not clock.is_trading_day(date.fromisoformat(session)):
            return {}
        iid = str(cfg.get("instance_id") or "swing")
        scan_time = _valid_hhmm(cfg.get("scan_time_et"))
        if scan_time is None:
            _log_once(cache, "bad-scan-time", session,
                      f"StrategySwing {session} | REFUSING the scan — scan_time_et "
                      f"{cfg.get('scan_time_et')!r} is not an HH:MM time (ET); fix it in the "
                      "strategy editor. Pending exits are still re-sent.", "red")
        scan_due = (scan_time is not None and cache.get(_SCAN_DONE_KEY) != session
                    and clock.at_or_after(current_time, scan_time))
        if scan_due:
            first = cache.get(_SCAN_FIRST_KEY)
            if not isinstance(first, dict) or first.get("session") != session:
                # Stamped on the first tick the scan is due, whatever happens
                # next: a pre-market tick that is not ready keeps its entries.
                cache[_SCAN_FIRST_KEY] = {
                    "session": session,
                    "at": clock.ny_now(current_time).strftime("%H:%M"),
                    "late": clock.at_or_after(current_time, _MARKET_OPEN_ET)}
        rearm = self._rearm_todo(cache, session)
        rearm_due = bool(rearm) and clock.is_rth(current_time)
        pending = cache.get(_PENDING_EXIT_KEY)
        if not (scan_due or rearm_due or (isinstance(pending, dict) and pending)):
            return {}
        # fix F3: each step below places orders on what the working-order book
        # says, read through the adapter's strict reader. Unreadable, the lane
        # places nothing this tick; the scan resumes and exits re-send next tick.
        book = account.working_orders(emu)
        if book is None:
            _log(f"StrategySwing {session} | REFUSING new orders this tick — the broker's "
                 "working-order book is unreadable; the lane retries next tick.", "red")
            return {}

        decisions, sizes, intents = {}, {}, {}
        memo = {}

        def positions_read():
            # One strict positions read (one REST refresh) per tick, shared by
            # the pending-exit re-send and the scan.
            if "read" not in memo:
                memo["read"] = _positions_for_scan(emu)
            return memo["read"]

        self._reemit_exits(session, positions_read, cache, book, decisions, sizes, intents)
        if rearm_due:
            self._rearm_stale(rearm, session, emu, cache, book, decisions, sizes, intents)

        if scan_due:
            signals_store.ensure_tables()
            deadline = clock.tick_deadline(current_time, mode)
            scan = cache.get(_SCAN_KEY)
            if not isinstance(scan, dict) or scan.get("session") != session:
                scan = None
                if clock.time_left(deadline) >= clock.PREPARE_RESERVE_S:
                    try:
                        scan = self._prepare(prices, current_time, session, iid, cfg, emu,
                                             positions_read, cache, book, decisions, sizes,
                                             intents)
                    except Exception as exc:
                        _log(f"StrategySwing {session} | scan preparation failed "
                             f"({type(exc).__name__}: {exc}); retried next tick", "red")
                        scan = None
                    if scan is not None:
                        cache[_SCAN_KEY] = scan
            if scan is not None:
                self._score_queue(scan, session, iid, cfg, emu, cache, deadline,
                                  decisions, sizes, intents)
                if scan["cursor"] >= len(scan["queue"]):
                    cache[_SCAN_DONE_KEY] = session
                    cache.pop(_SCAN_KEY, None)
                    self._run_summary(scan, session, iid, emu)

        self._remember_emitted(current_time, session, cache, decisions, sizes, intents)
        return _emit(decisions, sizes, intents)

    def _prepare(self, prices, current_time, session, iid, cfg, emu, positions_read, cache,
                 book, decisions, sizes, intents):
        """paper_trader.py:446-626 up to the entry loop: data, regime, exits,
        the bear counter, and the queue of names whose signal fired. Exits are
        merged into the payload only once everything above them succeeded.
        None means not ready: nothing is decided, and the next tick retries."""
        try:
            client = market_data.data_client(cfg.get("alpaca_key"), cfg.get("alpaca_secret"))
        except RuntimeError as exc:
            _log_once(cache, "no-creds", session,
                      f"StrategySwing {session} | REFUSING to scan — {exc}", "red")
            return None
        vix = regime.fetch_vix_close()
        if vix is None:
            # Review fix c: a VIX read that fails at 09:20 would block the
            # regime and advance the bear counter for the whole session.
            if not clock.at_or_after(current_time, _VIX_RETRY_UNTIL_ET):
                _log_once(cache, "vix-retry", session,
                          f"StrategySwing {session} | VIX unavailable — the scan waits and "
                          f"retries each tick until {_VIX_RETRY_UNTIL_ET} ET", "yellow")
                return None
            _log_once(cache, "vix-blocked", session,
                      f"StrategySwing {session} | VIX still unavailable at "
                      f"{_VIX_RETRY_UNTIL_ET} ET — the regime is blocked, as a missing VIX "
                      "blocked ST", "yellow")
        equity_pos, not_ready, _visible = positions_read()
        if equity_pos is None:
            _log_once(cache, "positions-not-ready", session,
                      f"StrategySwing {session} | scan not ready: positions unreadable — "
                      f"{not_ready}. Nothing is decided or latched; the scan retries next "
                      "tick.", "red")
            return None
        # FW-lo-I5: the cash securing short puts (open, and working
        # sell-to-open) is not the swing lane's to spend. Seams m1: collateral
        # that cannot be read is "not ready", like the positions above --
        # FW1's incompleteness triggers last only until the next ~3 s refresh,
        # so nothing is decided or latched and the scan retries next tick.
        bp, collateral, collateral_unread = account.swing_live_budget(emu, book)
        if bp is None:
            _log_once(cache, "put-collateral", session,
                      f"StrategySwing {session} | scan not ready: the cash securing short "
                      f"puts cannot be read ({collateral_unread}), so a swing entry could "
                      "spend it. Nothing is decided or latched; the scan retries next tick.",
                      "red")
            return None
        live_universe = [universe.norm_symbol(s) for s in universe.get_sp500_symbols()]
        defensive = _list(cfg["defensive_universe"])
        fetch_universe = list(dict.fromkeys(live_universe + defensive))
        bars = market_data.get_daily_bars(fetch_universe, days=market_data.LIVE_WINDOW_DAYS,
                                          client=client)
        ind = swing_indicators(bars, cfg)
        spy = ind.get("SPY")
        if not spy:
            _log_once(cache, "no-spy-live", session,
                      f"StrategySwing {session} | no SPY daily bars — the scan retries "
                      "next tick", "red")
            return None
        reg = regime.regime_decision(spy.get("close"), spy.get("sma200"), vix,
                                     spy_buffer=float(cfg["spy_buffer"]),
                                     vix_max=float(cfg["vix_max"]))

        option_syms = account.option_symbols(emu)
        equity = account.live_equity(emu, prices)
        if collateral:
            _log(f"StrategySwing {session} | ${collateral:,.0f} is committed to short puts; "
                 f"the swing budget is ${bp:,.2f} (cash and buying power less that "
                 "collateral)", "cyan")
        # G8a M4: a GTC entry still working at the broker is not "unfilled".
        calibration.record_outcomes(iid, emu, "swing", held=set(equity_pos),
                                    working=_symbols_of(book, _working_entry))
        # fix F3: a name with a working sell is already exiting (never stack a
        # second sell on it); a working buy is an entry queued for the open and
        # holds its slot and sector like a position.
        selling = _symbols_of(book, _working_exit)
        queued = _symbols_of(book, _working_entry)

        ex_dec, ex_sizes, ex_int = {}, {}, {}
        reasons = _exits(ind, {s: p["qty"] for s, p in equity_pos.items() if s not in selling},
                         lambda s: equity_pos[s]["avg_entry_price"], cfg,
                         ex_dec, ex_sizes, ex_int)
        for symbol, reason in reasons.items():
            close, pos = float(ind[symbol]["close"]), equity_pos[symbol]
            entry = pos["avg_entry_price"]
            notify.send("swing_exit", iid, f"SELL {symbol}",
                        f"{symbol} — {reason}\n{(close - entry) / entry * 100:+.1f}% | "
                        f"${(close - entry) * pos['qty']:+.0f}", priority=1)
            bp += pos["qty"] * close
        pending = cache.setdefault(_PENDING_EXIT_KEY, {})
        for symbol, reason in reasons.items():
            pending.setdefault(symbol, {"reason": reason, "intent": INTENT_EXIT[reason],
                                        "since": session})
        active = (set(equity_pos) | option_syms | queued) - set(reasons)

        bear = signals.update_regime_tracker(cache.get(_BEAR_KEY), reg["regime_ok"], session)
        cache[_BEAR_KEY] = bear
        univ, phase = signals.select_entry_universe(
            reg["regime_ok"], bear["blocked_days"],
            bear_regime_days=int(cfg["bear_regime_days"]),
            live_universe=live_universe, defensive_universe=defensive)
        first = cache.get(_SCAN_FIRST_KEY)
        if univ and isinstance(first, dict) and first.get("session") == session \
                and first.get("late"):
            # FW-str minor (d): a missed pre-market scan is no entries today.
            # Entries now would fill at intraday prices with legs anchored on
            # the prior close; ST's cron only ever ran at 09:15. Exits run.
            _log_once(cache, "late-scan", session,
                      f"StrategySwing {session} | NO ENTRIES today — the day's first scan "
                      f"tick came at {first.get('at')} ET, at or after the "
                      f"{_MARKET_OPEN_ET} open (a missed pre-market scan; ST's cron ran "
                      "only at 09:15). Exits still run.", "yellow")
            univ = None
        vix_str = f"{vix:.1f}" if vix is not None else "n/a"
        if phase == "bear_mode":
            notify.send("swing_run_summary", iid, f"🐻 Bear Mode Day {bear['blocked_days']}",
                        f"Regime blocked {bear['blocked_days']} consecutive days.\n"
                        f"Scanning defensive: {', '.join(defensive)}\n"
                        f"Reason: {reg['blocked_reason']}")

        queue = []
        if univ:
            ekw = signals.entry_kwargs(cfg)
            for symbol in univ:
                if symbol in active or symbol not in ind:
                    continue
                try:
                    fired = signals.entry_signal(ind[symbol], **ekw)
                except Exception as exc:
                    _log(f"StrategySwing {session} | {symbol}: entry check skipped "
                         f"({type(exc).__name__}: {exc}) — the scan continues", "yellow")
                    continue
                if fired:
                    queue.append({"symbol": symbol, "ind": ind[symbol]})
        _log(f"StrategySwing {session} | regime "
             f"{'ACTIVE' if reg['regime_ok'] else 'BLOCKED'} ({phase}) | SPY "
             f"{reg['spy_close']} vs SMA200 {reg['spy_sma200']} | VIX {vix_str} | "
             f"exits={sorted(reasons)} | signals={[c['symbol'] for c in queue]}", "cyan")

        decisions.update(ex_dec)
        sizes.update(ex_sizes)
        intents.update(ex_int)
        return {"session": session, "phase": phase, "regime_ok": reg["regime_ok"],
                "vix": vix, "queue": queue, "cursor": 0, "active": sorted(active),
                "option_symbols": sorted(option_syms), "equity": equity,
                "buying_power": bp, "entries_placed": 0,
                "available_slots": int(cfg["max_positions"]) - len(active),
                "counts": {"entered": 0, "pending": 0, "rejected": 0, "skipped": 0,
                           "ai_errors": 0}}

    def _score_queue(self, scan, session, iid, cfg, emu, cache, deadline,
                     decisions, sizes, intents):
        """paper_trader.py:626-756, one candidate at a time inside the tick
        budget. One failure skips its candidate (fix 5)."""
        gate = _truthy(cfg.get("ai_gate_enabled", True))
        role = ai_analyst.llm_role_from_config(cfg) if gate else None
        if gate and role is None:
            if cache.get(_NO_MODEL_KEY) != session:
                cache[_NO_MODEL_KEY] = session
                left = len(scan["queue"]) - scan["cursor"]
                msg = ("ai_gate_enabled is on but no conviction model is linked "
                       f"(conviction_llm_model_id); {left} swing entr(ies) refused this "
                       "session. Link a model in the strategy editor.")
                _log(f"StrategySwing {session} | REFUSING ENTRIES — {msg}", "red")
                notify.send("strategy_error", iid, "Swing AI gate: no model linked", msg,
                            priority=1)
            scan["counts"]["skipped"] += len(scan["queue"]) - scan["cursor"]
            scan["cursor"] = len(scan["queue"])
            return

        option_syms = set(scan.get("option_symbols") or [])
        try:
            smap = refdata.sector_map(store, [c["symbol"] for c in scan["queue"]]
                                      + list(scan["active"]))
        except Exception as exc:
            _log(f"StrategySwing {session} | SwingSectorMap unreadable "
                 f"({type(exc).__name__}: {exc}) — sectors fall back to yfinance", "yellow")
            smap = {}
        read_sector = _sector_reader(smap, allow_network=True,
                                     cache=cache.setdefault(_SECTOR_CACHE_KEY, {}))

        def sector_of(symbol):
            if symbol in option_syms:
                return "unknown"
            return read_sector(symbol)

        try:
            client = market_data.data_client(cfg.get("alpaca_key"), cfg.get("alpaca_secret"))
        except RuntimeError:
            client = None           # the sector-ETF RSI reads "unavailable"
        while scan["cursor"] < len(scan["queue"]):
            if clock.time_left(deadline) < clock.CANDIDATE_RESERVE_S:
                _log(f"StrategySwing {session} | tick budget spent — "
                     f"{len(scan['queue']) - scan['cursor']} candidate(s) resume next tick",
                     "cyan")
                return
            item = scan["queue"][scan["cursor"]]
            scan["cursor"] += 1
            try:
                self._consider(item, scan, session, iid, cfg, role, gate, sector_of,
                               client, emu, cache, decisions, sizes, intents)
            except Exception as exc:
                scan["counts"]["ai_errors"] += 1
                _log(f"StrategySwing {session} | {item['symbol']}: skipped after "
                     f"{type(exc).__name__}: {exc} — the scan continues (fix 5)", "yellow")

    def _consider(self, item, scan, session, iid, cfg, role, gate, sector_of, client,
                  emu, cache, decisions, sizes, intents):
        symbol, ind = item["symbol"], item["ind"]
        active = set(scan["active"])
        if symbol in active:
            return
        conflict = signals.sector_conflict(symbol, active, sector_of=sector_of,
                                           max_per_sector=int(cfg["max_per_sector"]))
        if conflict:
            _log(f"  {symbol}: SIGNAL blocked — sector conflict ({sector_of(symbol)} "
                 f"already held via {conflict})")
            scan["counts"]["skipped"] += 1
            return
        if scan["entries_placed"] >= scan["available_slots"]:
            _log(f"  {symbol}: Skipped (no open slots)")
            scan["counts"]["skipped"] += 1
            return
        alloc = scan["equity"] * float(cfg["position_size_pct"])
        if scan["buying_power"] < alloc * 0.5:
            _log(f"  {symbol}: Skipped (insufficient buying power: "
                 f"${scan['buying_power']:,.2f})")
            scan["counts"]["skipped"] += 1
            return
        use = min(alloc, scan["buying_power"])
        base_shares = int(use / ind["close"])
        if base_shares < 1:
            _log(f"  {symbol}: Skipped (share count rounds to 0)")
            scan["counts"]["skipped"] += 1
            return

        stop_price   = round(ind["close"] * (1 - float(cfg["stop_loss"])), 2)
        target_price = round(ind["close"] * (1 + float(cfg["profit_target"])), 2)

        # Hard earnings block — no exceptions regardless of AI score
        earnings_days = ai_analyst.days_until_earnings(_yf_symbol(symbol))
        block = int(cfg["earnings_hard_block_days"])
        if earnings_days is not None and earnings_days < block:
            _log(f"  [{symbol}] SKIP — earnings in {earnings_days} day(s) "
                 f"(hard block: < {block} days)")
            scan["counts"]["skipped"] += 1
            return

        sid = signals_store.signal_id_for(iid, "swing", session, symbol)
        existing = signals_store.get_signal(sid)
        proposal = {"entry": ind["close"], "stop": stop_price, "target": target_price,
                    "shares": base_shares}
        if existing is not None:
            # A resumed scan: the decision is already recorded; never re-score.
            result = {"conviction_score": existing.get("score"),
                      "recommendation": str(existing.get("recommendation") or "").lower(),
                      "reasoning": existing.get("reasoning") or "",
                      "position_size_adjustment": float(existing.get("size_adjustment") or 1.0),
                      "key_risks": existing.get("key_risks") or []}
        elif gate:
            result = ai_analyst.analyse(
                {"symbol": symbol, "rsi": ind["rsi"], "rsi_prev": ind["rsi_prev"],
                 "macd_hist": ind["macd_hist"], "macd_hist_prev": ind["macd_hist_prev"],
                 "entry_price": ind["close"], "shares": base_shares,
                 "stop_price": stop_price, "target_price": target_price},
                role=role, sector_of=sector_of, bars_client=client,
                earnings_fn=lambda s: earnings_days,
                approve_threshold=int(cfg["ai_approve_threshold"]),
                review_threshold=int(cfg["ai_review_threshold"]))
        else:
            result = {"conviction_score": None, "recommendation": "approve",
                      "reasoning": "AI gate disabled", "position_size_adjustment": 1.0,
                      "key_risks": []}
        rec = result["recommendation"]
        score = result.get("conviction_score")
        adj = float(result.get("position_size_adjustment") or 1.0)
        context = {k: result.get(k) for k in ("earnings_days", "sector_etf", "sector_rsi",
                                              "news_summary") if k in result}

        def record(status, reason=None):
            if existing is None:
                doc = signals_store.new_signal(
                    instance_id=iid, lane="swing", symbol=symbol, session=session,
                    score=score, recommendation=rec, reasoning=result.get("reasoning"),
                    key_risks=result.get("key_risks"), size_adjustment=adj,
                    proposal=proposal, status=status, context=context)
                if reason:
                    doc["decision_reason"] = reason
                signals_store.insert_signal(doc)
            elif reason:
                signals_store.update_signal(sid, {"status": status,
                                                  "decision_reason": reason})

        if rec == "reject":
            record("ai_rejected")
            scan["counts"]["rejected"] += 1
            if existing is None:
                notify.send("swing_run_summary", iid, f"REJECTED {symbol}",
                            f"{symbol} — Score {score}/100\n"
                            f"{str(result.get('reasoning') or '')[:100]}")
            return

        if rec == "review":
            record("pending")
            scan["counts"]["pending"] += 1
            if existing is None:
                notify.send("swing_pending_review", iid,
                            f"⚠️ REVIEW: {symbol} (score {score}/100)",
                            f"{symbol} @ ${ind['close']:.2f}\n"
                            f"Score: {score}/100 — needs your approval\n"
                            f"{str(result.get('reasoning') or '')[:120]}\n"
                            f"Risks: {', '.join(result.get('key_risks') or [])}\n"
                            f"Stop: ${stop_price:.2f} | Target: ${target_price:.2f}\n"
                            f"Approve or reject in IntelliStock (web or iOS).", priority=1)
            return

        # approve — apply position size adjustment before placing
        if existing is not None and existing.get("status") != "auto_approved":
            return          # the operator decided it, or it already failed
        shares = max(1, int(base_shares * adj))
        # The engine sizes a live whole-share bracket as floor(buy_cash / live
        # price) and reads no share count from the hint (plan A-live). buy_cash
        # is ST's allocation times the AI size adjustment, never below one
        # share at the prior close (ST's max(1, ...)).
        buy_cash = round(max(use * adj, ind["close"]), 2)
        emitted = (cache.get(_EMITTED_KEY) or {})
        sent = emitted.get("session") == session and symbol in (emitted.get("entries") or {})
        live_px = None if sent else account.latest_trade_price(emu, symbol)
        if live_px is not None and math.floor(buy_cash / live_px + 1e-9) < 1:
            # FW-str minor (a): the broker would floor this to 0 shares and
            # refuse it. It holds no slot, sector or buying power, so a later
            # candidate may take them.
            why = (f"the share count rounds to 0: ${buy_cash:,.2f} buys no whole share at "
                   f"the live ${live_px:,.2f} (prior close ${ind['close']:,.2f})")
            _log(f"  {symbol}: Skipped ({why}) — its slot and sector stay free")
            record("failed", why)
            scan["counts"]["skipped"] += 1
            return
        record("auto_approved")
        scan["buying_power"] -= shares * ind["close"]
        scan["entries_placed"] += 1
        scan["active"].append(symbol)            # fix 4: the sector set follows each buy
        scan["counts"]["entered"] += 1
        if sent:
            return          # already sent before a restart; the broker holds it
        decisions[symbol] = 1
        sizes[symbol] = {"buy_cash": buy_cash,
                         "bracket": {"take_profit_price": target_price,
                                     "stop_loss_price": stop_price},
                         "whole_shares": True, "fill_at_next_open": True}
        intents[symbol] = INTENT_DEFENSIVE if scan["phase"] == "bear_mode" else INTENT_ENTRY
        if existing is None:
            notify.send("swing_entry", iid, f"BUY {symbol}",
                        f"{symbol} — {shares} shares @ ${ind['close']:.2f}\n"
                        f"Stop ${stop_price:.2f} | Target ${target_price:.2f}", priority=1)

    def _reemit_exits(self, session, positions_read, cache, book, decisions, sizes, intents):
        """A-live contract addition 15: the engine never retries an exit it
        deferred. Re-send every pending exit whose stock is still held and has
        no working non-bracket sell; forget it once the stock is gone. `book`
        is the strict working-order read (fix F3).

        Review round 2, finding 1: only a READY positions read may forget a
        pending exit. A degraded read (a cached snapshot, or the empty book
        the adapter reports after clearing its cache) keeps every one, and
        still re-sends those for names it visibly shows held."""
        pending = cache.get(_PENDING_EXIT_KEY)
        if not isinstance(pending, dict) or not pending:
            return
        positions, not_ready, visible = positions_read()
        if positions is None:
            _log_once(cache, "exit-book", session,
                      f"StrategySwing {session} | positions read not ready ({not_ready}) — "
                      "every pending exit is kept; exits are re-sent only for names visibly "
                      "held", "yellow")
        held = set(positions) if positions is not None else set(visible)
        selling = _symbols_of(book, _working_exit)
        for symbol in sorted(pending):
            if symbol not in held:
                if positions is not None:
                    pending.pop(symbol)
                    _log(f"StrategySwing {session} | {symbol}: position gone — exit complete")
                continue
            if symbol in selling:
                continue
            decisions[symbol] = -1
            sizes[symbol] = {"sell_fraction": 1.0}
            intents[symbol] = pending[symbol]["intent"]
            _log_once(cache, f"reemit-{symbol}", session,
                      f"StrategySwing {session} | {symbol}: re-sending the "
                      f"{pending[symbol]['reason']} exit decided {pending[symbol]['since']} "
                      "— still held with no working sell (the engine does not retry a "
                      "deferred exit)", "yellow")

    def _rearm_todo(self, cache, session) -> list:
        """Pre-market entries of this session not yet re-armed."""
        em = cache.get(_EMITTED_KEY)
        if not isinstance(em, dict) or em.get("session") != session:
            return []
        return [s for s, e in (em.get("entries") or {}).items()
                if not e.get("at_rth") and not e.get("rearmed")]

    def _rearm_stale(self, todo, session, emu, cache, book, decisions, sizes, intents):
        """Spec §5.1: an entry the gate refused on a stale pre-market quote is
        re-emitted ONCE, at the first regular-hours tick. The lane cannot see
        the gate's reason, so it re-emits exactly the pre-market entries that
        left no trace at the broker: no working order, no order of any status
        since the session began, no position. An unreadable book re-emits
        nothing — a missed entry costs an opportunity, a duplicate costs money."""
        em = cache[_EMITTED_KEY]
        for s in todo:
            em["entries"][s]["rearmed"] = True
        try:
            seen = {str(o.symbol).upper() for o in (emu.list_closed_orders(todo, session) or [])}
            held = {str(s).upper() for s, q in (emu.get_positions() or {}).items()
                    if float(q or 0.0) > 0}
        except Exception as exc:
            _log(f"StrategySwing {session} | stale-quote re-arm skipped — the order book "
                 f"is unreadable ({type(exc).__name__}: {exc})", "yellow")
            return
        working = _symbols_of(book, lambda o: True)
        for s in todo:
            if s in working or s in seen or s in held:
                continue
            entry = em["entries"][s]
            decisions[s] = 1
            sizes[s] = dict(entry["hint"])
            intents[s] = entry["intent"]
            _log(f"StrategySwing {session} | {s}: re-emitting the entry once at the open — "
                 "nothing reached the broker pre-market (quote.stale)", "cyan")

    def _remember_emitted(self, current_time, session, cache, decisions, sizes, intents):
        buys = [s for s, d in decisions.items() if d == 1]
        if not buys:
            return
        em = cache.get(_EMITTED_KEY)
        if not isinstance(em, dict) or em.get("session") != session:
            em = {"session": session, "entries": {}}
            cache[_EMITTED_KEY] = em
        at_rth = clock.is_rth(current_time)
        for s in buys:
            if s in em["entries"]:
                continue            # a re-arm keeps its record, rearmed=True
            em["entries"][s] = {"hint": dict(sizes.get(s) or {}),
                                "intent": intents.get(s, INTENT_ENTRY),
                                "at_rth": at_rth, "rearmed": False}

    def _run_summary(self, scan, session, iid, emu):
        """paper_trader.py:790-809, the run-complete notification."""
        positions = account.equity_positions(emu)
        parts = []
        for s, p in sorted(positions.items()):
            if p["avg_entry_price"] > 0 and p["market_value"] > 0:
                pct = (p["market_value"] / (p["qty"] * p["avg_entry_price"]) - 1) * 100
                parts.append(f"{s} {pct:+.1f}%")
        c = scan["counts"]
        vix = scan.get("vix")
        lines = [f"{session} ET", f"Signals: {c['entered']} | Positions: {len(positions)}"]
        if parts:
            lines.append(" | ".join(parts))
        lines.append(f"Regime: {'ACTIVE' if scan.get('regime_ok') else 'BLOCKED'} | "
                     f"VIX {f'{vix:.1f}' if vix is not None else 'n/a'}")
        lines.append(f"Review: {c['pending']} | Rejected: {c['rejected']} | "
                     f"Skipped: {c['skipped']} | AI errors: {c['ai_errors']}")
        notify.send("swing_run_summary", iid, "Swing Trader ✅ Run Complete", "\n".join(lines))
