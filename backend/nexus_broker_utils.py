from __future__ import annotations

import logging

_mpg_logger = logging.getLogger(__name__)


def _mpg_norm_set(value) -> set:
    """Upper-case, whitespace-trim, drop empties/None into a set of tickers."""
    out: set[str] = set()
    for s in (value or []):
        if s is None:
            continue
        t = str(s).strip().upper()
        if t:
            out.add(t)
    return out


def max_positions_projected_count(held_symbols, planned_sells_full_exit, emitted_new_names) -> int:
    """Projected open-position count used by the gate and its log line.

        projected = len(held − full_exit_sells) + len(new_names_already_emitted)

    Fail-open to -1 on malformed input (the caller only uses this for a log
    string; -1 signals "could not compute" without crashing the cycle).
    """
    try:
        held = _mpg_norm_set(held_symbols)
        full_exits = _mpg_norm_set(planned_sells_full_exit)
        emitted = _mpg_norm_set(emitted_new_names)
        return len(held - full_exits) + len(emitted)
    except Exception:
        return -1


def resolve_max_positions_cap(cached_strategies) -> tuple:
    """Resolve the hard max_positions cap from the loaded strategy specs.

    Returns ``(cap, reason)`` where ``cap`` is an int or None and ``reason`` is
    one of:
      * ``"armed"``                — cap resolved from the nexus config
      * ``"no_nexus_spec"``        — no graph_nexus_analysis strategy loaded
      * ``"no_max_positions_key"`` — nexus config exists but lacks max_positions
      * ``"invalid"``              — key present but not coercible to int

    Fail-open: any structural surprise resolves to ``(None, ...)`` — the broker
    gate stays inert rather than crashing the cycle.
    """
    try:
        spec = next(
            (s for s in (cached_strategies or [])
             if str((s or {}).get("strategy") or "").strip() == "graph_nexus_analysis"),
            None,
        )
        if spec is None:
            return (None, "no_nexus_spec")
        cfg = spec.get("config") or {}
        if "max_positions" not in cfg or cfg.get("max_positions") is None:
            return (None, "no_max_positions_key")
        return (int(cfg["max_positions"]), "armed")
    except Exception:
        return (None, "invalid")


def max_positions_arm_warning(reason) -> str | None:
    """Log line for a gate that could NOT arm; None when no warning is due.

    ``no_nexus_spec`` is silent — a portfolio without graph_nexus_analysis has
    no concept of this cap, and warning every tick would be noise.
    """
    if reason == "no_max_positions_key":
        return "MAX_POSITIONS_GATE not armed: no max_positions in config"
    if reason == "invalid":
        return "MAX_POSITIONS_GATE not armed: max_positions in config is not an integer"
    return None


def max_positions_gate(held_symbols, cap, planned_sells_full_exit, emitted_new_names, candidate) -> bool:
    """Hard max_positions gate — return True iff a BUY of ``candidate`` may emit.

    Single authoritative emission-time recount that closes the leak behind
    bt 586767 (13 held vs cap 10): GNA sizes NEW-name buys in several independent
    paths (main allocation, BFQ direct-reserved, BFQ dequeue, momentum-window
    adds) each against its OWN local headroom snapshot, and rotation buys are
    cap-EXEMPT on the assumption their paired full-exit sell nets the count to
    zero. Nothing recounted the TOTAL at order emission, so the paths' sums —
    plus any rotation whose sell didn't fully net — overshot the cap.

    Semantics:
      * Adds / winner-adds / amplifier-adds to a name ALREADY held are always
        allowed — they do not grow the open-position count.
      * A NEW name is blocked once the projected count would meet or exceed the
        cap:  ``len(held − full_exit_sells) + len(emitted_new_names) >= cap``.
      * A same-cycle rotation (full exit of A funding a new B) nets to zero: A's
        full-exit sell drops the projected count by 1, so B lands back at the cap.
      * Idempotent: a candidate already in ``emitted_new_names`` is allowed (it is
        already counted) so re-evaluation never double-blocks a legitimate buy.

    Fail-open: any malformed input logs a warning and ALLOWS the buy. This gate is
    a safety net, not a trade generator — an over-cap buy is a P&L nuisance, but a
    crashed cycle halts the whole strategy, so on a type error we never crash and
    never silently strand a buy.
    """
    try:
        cand = str(candidate).strip().upper()
        if not cand:
            return True
        held = _mpg_norm_set(held_symbols)
        # Add to an existing name never grows the count -> always allowed.
        if cand in held:
            return True
        emitted = _mpg_norm_set(emitted_new_names)
        # Already counted this cycle -> don't double-block on re-evaluation.
        if cand in emitted:
            return True
        if cap is None:
            return True
        cap_i = int(cap)
        full_exits = _mpg_norm_set(planned_sells_full_exit)
        projected = len(held - full_exits) + len(emitted)
        return projected < cap_i
    except Exception as exc:  # fail-open — never crash the trading cycle
        _mpg_logger.warning(
            "max_positions_gate fail-open on malformed input (%s): %r",
            type(exc).__name__, exc,
        )
        return True


def buy_ceiling(cached_cash, submitted_sells, enabled=True, haircut=0.95) -> float:
    """Task 13 (spec 5.8) — live buy-sizing ceiling with same-cycle sell proceeds.

    Live sells only free cash when the async trade_updates WS fill lands
    (alpaca.py fill handler), but the broker reads the adapter's cached cash
    when it sizes buys later in the SAME cycle. A rotation (sell A, buy B)
    therefore sized B against pre-sell cash and starved the paired buy. The
    backtest emulator credits sell proceeds synchronously, so this asymmetry
    is live-only.

        ceiling = cached_cash + haircut × Σ(expected proceeds of this cycle's
                                            submit-SUCCESSFUL sells)

    * ``haircut`` (default 0.95) hedges partial fills — a submitted sell is
      not a filled sell. It is clamped to [0, 1] so the ceiling can NEVER
      exceed cash + proceeds.
    * ``enabled=False`` (config kill-switch ``live_credit_sell_proceeds_enabled``)
      returns the cached cash unchanged.
    * Fail-safe: malformed entries are ignored; negative/zero proceeds never
      credit; a malformed cash value degrades to 0.0 (sizes nothing, crashes
      nothing).
    """
    try:
        cash = float(cached_cash or 0.0)
    except (TypeError, ValueError):
        cash = 0.0
    if not enabled:
        return cash
    try:
        h = float(haircut)
    except (TypeError, ValueError):
        h = 0.95
    h = max(0.0, min(1.0, h))  # never exceed cash + proceeds
    total = 0.0
    for entry in (submitted_sells or []):
        try:
            v = float(entry or 0.0)
        except (TypeError, ValueError):
            continue
        if v > 0.0:
            total += v
    return cash + h * total


def _normalize_strategy_name(value) -> str:
    return str(value or "").strip().lower()


def build_nexus_buy_guard(
    strategy_summary: list[dict] | None,
    symbol: str,
    decision: int,
    nexus_executable_buys,
    nexus_position_sizes: dict | None,
) -> dict:
    summary = list(strategy_summary or [])
    buy_entries = []
    for entry in summary:
        try:
            entry_decision = int(entry.get("decision", 0))
        except Exception:
            entry_decision = 0
        if entry_decision == 1:
            buy_entries.append(entry)

    nexus_buy = any(_normalize_strategy_name(entry.get("strategy")) == "graph_nexus_analysis" for entry in buy_entries)
    other_buy = any(_normalize_strategy_name(entry.get("strategy")) != "graph_nexus_analysis" for entry in buy_entries)
    hint = (nexus_position_sizes or {}).get(symbol) if isinstance(nexus_position_sizes, dict) else {}
    if not isinstance(hint, dict):
        hint = {}
    executable_set = {str(sym).strip().upper() for sym in (nexus_executable_buys or []) if str(sym).strip()}
    return {
        "is_nexus_only_buy": int(decision or 0) == 1 and nexus_buy and not other_buy,
        "has_nexus_buy_vote": nexus_buy,
        "is_whitelisted": str(symbol or "").strip().upper() in executable_set,
        "has_buy_cash_hint": float(hint.get("buy_cash", 0.0) or 0.0) > 0.0,
        "asset_class": str(hint.get("asset_class") or "stock").strip().lower() or "stock",
        "buy_price_floor": float((nexus_position_sizes or {}).get("_buy_price_floor", 0.0) or 0.0),
        "hint": dict(hint) if isinstance(hint, dict) else {},
    }


def get_nexus_buy_block_details(symbol: str, price: float, guard: dict | None) -> dict | None:
    guard = guard or {}
    if not guard.get("is_nexus_only_buy"):
        return None
    if not guard.get("is_whitelisted"):
        return {
            "code": "not_whitelisted",
            "message": f"Nexus execution gate: {symbol} not in executable buy whitelist",
        }
    if not guard.get("has_buy_cash_hint"):
        return {
            "code": "missing_buy_cash_hint",
            "message": f"Nexus execution gate: {symbol} missing buy_cash hint",
        }
    buy_price_floor = float(guard.get("buy_price_floor", 0.0) or 0.0)
    is_etf = str(guard.get("asset_class") or "").strip().lower() == "etf"
    if buy_price_floor > 0.0 and not is_etf and float(price or 0.0) > 0.0 and float(price or 0.0) < buy_price_floor:
        return {
            "code": "buy_price_floor",
            "message": f"Nexus execution price floor: {symbol} at ${float(price):.2f} is below ${buy_price_floor:.2f}",
        }
    return None


def get_nexus_buy_block_reason(symbol: str, price: float, guard: dict | None) -> str | None:
    details = get_nexus_buy_block_details(symbol, price, guard)
    if not details:
        return None
    return str(details.get("message") or "") or None
