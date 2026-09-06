"""Outlier sleeve — pure decision rules. No clock, no I/O, no store.

Buys 52-week-high breakouts with top-decile six-month relative strength,
confirmed by Nexus peer breadth, in small slices that are never rebalanced
down; exits only on a slow trend break or a time stop. Every rule here was
chosen by measurement (spec docs/superpowers/specs/2026-09-02-outlier-sleeve-
design.md §2): the SMA-200 exit is the load-bearing one — the fast exits sold
SMCI at +126% and CLS at +92% before their 10x-60x runs.
"""
from __future__ import annotations

from datetime import timezone
from zoneinfo import ZoneInfo

from strategy_eb import session_ordinal, session_weekday

_NY = ZoneInfo("America/New_York")

DEFAULTS: dict = {
    "outlier_sleeve_enabled": False,
    "feature_dataset": "",
    "winner_add_enabled": False,
    "winner_add_fraction": 0.05,
    "winner_add_position_cap": 0.20,
    "winner_add_min_gain": 0.25,
    "winner_add_min_age": 20,
    "winner_add_min_gap": 20,
    "winner_add_max_count": 2,
    "sleeve_fraction": 0.15,
    "slot_fraction": 0.015,
    "max_slots": 10,
    "winner_cap_fraction": 0.30,
    "adv_min_usd": 10_000_000.0,
    "price_min": 3.0,
    "min_history_bars": 120,
    "breakout_tolerance": 0.02,
    "rs_decile_floor": 0.90,
    "confirm_enabled": True,
    "confirm_min_peers": 5,
    "confirm_frac": 0.25,
    "confirm_hot_rank": 0.75,
    "screen_weekdays": [2],
    "screen_every_n_weeks": 2,
    "exit_sma_bars": 200,
    "exit_below_sma_closes": 5,
    "time_stop_sessions": 60,
    "time_stop_gain": 0.15,
    "excluded_symbols": ["TQQQ", "SPY", "BIL", "QQQ", "GLD", "GDX", "XLE"],
    "min_order_usd": 25.0,
    "broker_max_single_position_pct": 0.95,
    "honour_single_position_cap": True,
    "live_max_order_fraction": 0.7,
    "live_max_symbol_fraction": 0.35,
    "live_max_leveraged_fraction": 0.7,
    "live_soft_drawdown": 0.25,
    "live_hard_drawdown": 0.35,
    "live_kill_drawdown": 0.45,
}

LAST_SCREEN_KEY = "_outlier_last_screen_ordinal"


def _f(cfg, key) -> float:
    try:
        return float(cfg.get(key, DEFAULTS.get(key)))
    except (TypeError, ValueError):
        return float(DEFAULTS.get(key) or 0.0)


def _i(cfg, key) -> int:
    try:
        return int(cfg.get(key, DEFAULTS.get(key)))
    except (TypeError, ValueError):
        return int(DEFAULTS.get(key) or 0)


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def ny_date(current_time) -> str:
    """`YYYY-MM-DD` of the NY session the call falls in."""
    t = current_time
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t.astimezone(_NY).date().isoformat()


def visible_session(current_time, dates):
    """The latest date in `dates` STRICTLY earlier than the call's NY date.

    A daily row carries the 16:00 close; at 09:30 the same day it is six hours
    in the future. Comparing on the NY calendar date is the whole PIT rule.
    """
    today = ny_date(current_time)
    earlier = [str(d)[:10] for d in dates if str(d)[:10] < today]
    return max(earlier) if earlier else None


def _eligible(r, cfg, excluded, held) -> bool:
    sym = str(r.get("symbol") or "").upper()
    if not sym or sym in excluded or sym in held:
        return False
    try:
        close = float(r.get("nominal_close", r.get("close")) or 0.0)
        adv = float(r.get("adv20") or 0.0)
        n = int(r.get("n_bars") or 0)
    except (TypeError, ValueError):
        return False
    if close < _f(cfg, "price_min") or adv < _f(cfg, "adv_min_usd"):
        return False
    if n < _i(cfg, "min_history_bars"):
        return False
    return r.get("ret126") is not None and r.get("rs_rank") is not None


def screen(rows, cfg, peers, held) -> list:
    """Ranked breakout candidates for ONE visible session's cross-section."""
    cfg = {**DEFAULTS, **(cfg or {})}
    excluded = {str(s).upper() for s in (cfg.get("excluded_symbols") or [])}
    held = {str(s).upper() for s in (held or set())}
    tol = _f(cfg, "breakout_tolerance")
    floor = _f(cfg, "rs_decile_floor")
    rank = {str(r.get("symbol") or "").upper(): r.get("rs_rank")
            for r in rows if r.get("rs_rank") is not None}
    out = []
    for r in rows:
        if not _eligible(r, cfg, excluded, held):
            continue
        close = float(r["close"])
        hi = float(r.get("hi252") or 0.0)
        if hi <= 0 or close < hi * (1.0 - tol):
            continue
        if float(r["rs_rank"]) < floor:
            continue
        sym = str(r["symbol"]).upper()
        if _truthy(cfg.get("confirm_enabled")):
            ps = [p for p in (peers or {}).get(sym, []) if p in rank]
            if len(ps) < _i(cfg, "confirm_min_peers"):
                continue
            hot = sum(1 for p in ps if float(rank[p]) >= _f(cfg, "confirm_hot_rank"))
            if hot / len(ps) < _f(cfg, "confirm_frac"):
                continue
        out.append((float(r["ret126"]), sym))
    out.sort(key=lambda t: (-t[0], t[1]))
    return [s for _, s in out]


def should_screen(session_id, cache, cfg) -> bool:
    """Wednesday's close, every `screen_every_n_weeks` weeks, once per session."""
    cfg = {**DEFAULTS, **(cfg or {})}
    wd = session_weekday(session_id)
    days = {int(d) for d in (cfg.get("screen_weekdays") or DEFAULTS["screen_weekdays"])}
    if wd < 0 or wd not in days:
        return False
    ordinal = session_ordinal(session_id)
    last = (cache or {}).get(LAST_SCREEN_KEY)
    if last is None:
        return True
    try:
        gap = ordinal - int(last)
    except (TypeError, ValueError):
        return True
    # n weeks of sessions, tolerant of one holiday: 5n - 2 (n=2 -> 8).
    return gap >= max(1, 5 * _i(cfg, "screen_every_n_weeks") - 2)


def exit_decisions(slots, rows_by_sym, session_id, cfg) -> dict:
    """symbol -> "sma" | "time" for slots that must be closed this session.

    Mutates the slot counters. Each session is counted once: a second call in
    the same session (15m granularity fires ~26 times) is a no-op.
    """
    cfg = {**DEFAULTS, **(cfg or {})}
    ordinal = session_ordinal(session_id)
    out = {}
    for sym, slot in (slots or {}).items():
        r = (rows_by_sym or {}).get(sym)
        if not r or slot.get("last_eval") == session_id:
            continue
        try:
            close = float(r.get("close") or 0.0)
            sma = r.get("sma200")
            entry = float(slot.get("entry_px") or 0.0)
        except (TypeError, ValueError):
            continue
        if close <= 0 or entry <= 0:
            continue
        slot["last_eval"] = session_id
        if close / entry - 1.0 >= _f(cfg, "time_stop_gain"):
            slot["proven"] = True
        if sma is not None and close < float(sma):
            slot["below"] = int(slot.get("below") or 0) + 1
        else:
            slot["below"] = 0
        if slot["below"] >= _i(cfg, "exit_below_sma_closes"):
            out[sym] = "sma"
            continue
        held_for = ordinal - int(slot.get("entry_ordinal") or ordinal)
        if held_for >= _i(cfg, "time_stop_sessions") and not slot.get("proven"):
            out[sym] = "time"
    return out


def new_slot_orders(candidates, slots, nav, cash, cfg) -> dict:
    """symbol -> buy_cash for free slots, inside the sleeve's NEW-money budget.

    Budget is `sleeve_fraction * nav` minus the COST BASIS of open slots, so a
    winner that has grown past the sleeve does not block new entries — only
    cash newly committed counts (spec §5).
    """
    cfg = {**DEFAULTS, **(cfg or {})}
    free = _i(cfg, "max_slots") - len(slots or {})
    if free <= 0 or nav <= 0:
        return {}
    committed = sum(float(s.get("entry_cost") or 0.0) for s in (slots or {}).values())
    budget = _f(cfg, "sleeve_fraction") * float(nav) - committed
    slice_ = _f(cfg, "slot_fraction") * float(nav)
    spendable = float(cash or 0.0)
    out = {}
    for sym in candidates:
        if len(out) >= free:
            break
        amt = min(slice_, budget, spendable)
        if amt < _f(cfg, "min_order_usd"):
            break
        out[sym] = round(amt, 2)
        budget -= amt
        spendable -= amt
    return out


def winner_add_orders(slots, rows_by_sym, positions, nav, cash, session, cfg, blocked=(), prices=None) -> dict:
    """Add only to seasoned winners, within the same new-money sleeve budget."""
    cfg = {**DEFAULTS, **(cfg or {})}
    if not _truthy(cfg["winner_add_enabled"]) or nav <= 0:
        return {}
    committed = sum(float(s.get("entry_cost") or 0) for s in slots.values())
    budget = min(float(cash), _f(cfg, "sleeve_fraction") * nav - committed)
    ordinal = session_ordinal(session)
    ranked = []
    for sym, slot in slots.items():
        row = rows_by_sym.get(sym)
        if sym in blocked or slot.get("exit_reason") or not row:
            continue
        if not _eligible({**row, "symbol": sym}, cfg, set(), set()):
            continue
        count = int(slot.get("buy_count") or 0)
        if count < 1 or count - 1 >= _i(cfg, "winner_add_max_count"):
            continue
        first, last = slot.get("entry_ordinal"), slot.get("last_buy_ordinal")
        if first is None or last is None:
            continue
        if (ordinal - int(first) < _i(cfg, "winner_add_min_age")
                or ordinal - int(last) < _i(cfg, "winner_add_min_gap")):
            continue
        px, entry = float(row.get("close") or 0), float(slot.get("entry_px") or 0)
        if entry <= 0 or px / entry - 1 < _f(cfg, "winner_add_min_gain"):
            continue
        high = float(row.get("hi252") or 0)
        if (high <= 0 or px < high * (1 - _f(cfg, "breakout_tolerance"))
                or float(row["rs_rank"]) < _f(cfg, "rs_decile_floor")):
            continue
        quantity = float(positions.get(sym) or 0)
        if quantity <= 0:
            continue
        mark = float((prices if prices is not None else {}).get(sym, px))
        if mark <= 0:
            continue
        room = _f(cfg, "winner_add_position_cap") * nav - quantity * mark
        ranked.append((-float(row["ret126"]), sym, room))
    out = {}
    for _, sym, room in sorted(ranked):
        amount = min(room, budget, _f(cfg, "winner_add_fraction") * nav)
        if amount < _f(cfg, "min_order_usd"):
            continue
        # Floor cents so rounding cannot spend more than the available budget.
        amount = int(amount * 100) / 100
        out[sym] = amount
        budget -= amount
    return out


def winner_cap_trims(slots, positions, prices, nav, cfg) -> dict:
    """symbol -> sell_fraction bringing a name above the cap back TO the cap."""
    cfg = {**DEFAULTS, **(cfg or {})}
    cap = _f(cfg, "winner_cap_fraction")
    if nav <= 0 or cap <= 0:
        return {}
    out = {}
    for sym in (slots or {}):
        try:
            mv = (float((positions or {}).get(sym) or 0.0)
                  * float((prices or {}).get(sym) or 0.0))
        except (TypeError, ValueError):
            continue
        if mv > cap * nav:
            out[sym] = round((mv - cap * nav) / mv, 6)
    return out
