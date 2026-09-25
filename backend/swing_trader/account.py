"""Read-only views of the book, shared by both lanes.

Platform glue with no ST source. Every reader tolerates an emulator or adapter
that lacks the method: it degrades to "no data" — which the callers treat as
"do not add exposure" — and never raises into run_once.
"""
from __future__ import annotations

import math


def spendable(emulator, prices) -> float:
    """Buying power when the emulator offers it, else settled cash."""
    try:
        bp = emulator.get_buying_power(prices=prices)
    except (AttributeError, TypeError):
        bp = None
    if bp is None:
        return float(emulator.get_cash() or 0.0)
    return float(bp or 0.0)


def pending_symbols(emulator) -> set:
    try:
        return {str(s).upper() for s in (emulator.pending_execution_symbols() or ())}
    except Exception:
        return set()


def entry_price_from_trades(emulator, symbol):
    """The fill price of the latest buy of `symbol` (ST read Alpaca's
    avg_entry_price; one swing position is one buy)."""
    try:
        trades = emulator.get_trade_history() or []
    except Exception:
        return None
    for t in reversed(trades):
        if not isinstance(t, dict):
            continue
        if (str(t.get("ticker") or t.get("symbol") or "").upper() == str(symbol).upper()
                and str(t.get("action") or t.get("side") or "").lower() == "buy"):
            try:
                return float(t.get("price"))
            except (TypeError, ValueError):
                return None
    return None


def equity_positions(emu) -> dict:
    """{symbol: {"qty", "avg_entry_price", "market_value"}} for long stock.
    ST read avg_entry_price off Alpaca positions (paper_trader.py:545); the
    adapter's refresh_positions returns the same PositionDTO."""
    dtos = []
    refresh = getattr(emu, "refresh_positions", None)
    if callable(refresh):
        try:
            dtos = list(refresh() or [])
        except Exception:
            dtos = []
    try:
        held = {str(s).upper(): float(q or 0.0) for s, q in (emu.get_positions() or {}).items()}
    except Exception:
        held = {}
    out = {}
    for d in dtos:
        sym = str(getattr(d, "symbol", "") or "").upper()
        qty = float(getattr(d, "qty", 0) or 0.0)
        if qty > 0 and sym in held:
            out[sym] = {"qty": qty,
                        "avg_entry_price": float(getattr(d, "avg_entry_price", 0) or 0.0),
                        "market_value": float(getattr(d, "market_value", 0) or 0.0)}
    for sym, qty in held.items():
        if qty > 0 and sym not in out:
            out[sym] = {"qty": qty, "avg_entry_price": entry_price_from_trades(emu, sym) or 0.0,
                        "market_value": 0.0}
    return out


def option_symbols(emu) -> set:
    """Open option contracts. ST's open_positions was the whole account, so
    option positions take swing slots (paper_trader.py:454, 596)."""
    try:
        return {str(p.symbol).upper() for p in (emu.list_option_positions() or [])}
    except Exception:
        return set()


def live_equity(emu, prices=None) -> float:
    try:
        return float(emu.refresh_account().equity)
    except Exception:
        return float(emu.get_portfolio_value(prices or {}) or 0.0)


def _live_cash_and_buying_power(emu):
    """(cash, buying_power) from one refresh_cash read; settled cash for
    both when the read fails, as before."""
    try:
        dto = emu.refresh_cash()
        return float(dto.cash or 0.0), float(dto.buying_power or 0.0)
    except Exception:
        cash = float(emu.get_cash() or 0.0)
        return cash, cash


def put_collateral(emu, book):
    """(collateral, reason): cash committed to short puts -- every open short
    put plus the unfilled remainder of every working sell-to-open put, at
    strike x 100 -- or (None, reason) when the option book cannot be read as
    a complete, current map (option_book). `book` is the strict working-order
    read (working_orders)."""
    rows, reason = option_book(emu)
    if reason is not None:
        return None, reason
    from swing_trader import wheel_rules
    held, _by = wheel_rules.short_put_collateral(rows)
    pending, _by = wheel_rules.pending_sto_collateral(book)
    return held + pending, None


def swing_live_budget(emu, book):
    """(budget, collateral, reason): what the swing lane's live entries may
    spend this scan.

    ST sized swing entries against account.buying_power (paper_trader.py:450),
    and with no short put open that is still the budget. FW-lo-I5 / spec fix
    8: the cash that secures short puts is not the swing lane's to spend, so
    with any committed the budget is the smaller of buying power and cash,
    less that collateral -- margin buying power cannot lift it, since the puts
    are cash-secured. (None, None, reason) when the option book cannot be
    read: the lane then plans no entries (its exits still run)."""
    cash, bp = _live_cash_and_buying_power(emu)
    collateral, reason = put_collateral(emu, book)
    if collateral is None:
        return None, None, reason
    if collateral <= 0:
        return bp, 0.0, None
    return max(0.0, min(bp, cash) - collateral), collateral, None


def working_orders(emu):
    """The working-order book, or None when it cannot be read.

    fix F3: through the adapter's STRICT reader when it has one —
    AlpacaAdapter.list_open_orders answers an outage with [], which reads as
    "nothing working" and would let a duplicate order through. None means
    unreadable: the caller places nothing that depends on the book."""
    try:
        reader = getattr(emu, "list_open_orders_strict", None) or emu.list_open_orders
        orders = reader()
    except Exception:
        return None
    if not isinstance(orders, (list, tuple)):
        return None
    return list(orders)


def positions_health(emu):
    """(complete, stale_since, error): the adapter's option-book health.

    Read through the public ``option_positions_health()`` accessor (A-live
    9b1c370) when the adapter has one. Only an adapter without it (the
    backtest emulator, older fakes) falls back to AlpacaAdapter's private
    flags, where a missing flag reads healthy as before. An accessor that
    refuses (the base adapter raises NotImplementedError), raises or answers
    anything but a dict gives an error and no verdict: the caller reads that
    as unknown and fails closed. So does a dict with no ``stale_since`` key
    (G8b minor 1): a missing stamp is not a fresh one. A dict without
    ``complete: True`` is incomplete."""
    accessor = getattr(emu, "option_positions_health", None)
    if callable(accessor):
        try:
            health = accessor()
        except Exception as exc:
            return False, None, f"{type(exc).__name__}: {exc}"
        if not isinstance(health, dict):
            return False, None, f"unexpected answer {health!r}"
        if "stale_since" not in health:
            return False, None, f"unexpected answer {health!r} (no stale_since)"
        return health.get("complete") is True, health.get("stale_since"), None
    return (getattr(emu, "_option_positions_complete", True) is not False,
            getattr(emu, "_positions_stale_since", None), None)


def option_book(emu):
    """(rows, reason): the option positions the adapter lists, and why they
    are not a complete, current map — reason is None when they are.

    I-1: AlpacaAdapter.list_option_positions never raises; it returns its
    in-memory map. That map reads incomplete until a refresh succeeds, after
    a failed one, and while a contract's fields could not be looked up (the
    row then keeps its signed quantity with an empty type, underlying and
    expiry). It reads stale while the REST refresh fails. Both come from
    positions_health (the adapter's public accessor), read on both sides of
    the call, as a refresh may land in between; health that cannot be read
    fails closed. rows is None when the call itself failed."""
    before = positions_health(emu)
    try:
        rows = list(emu.list_option_positions() or [])
    except Exception as exc:
        return None, f"list_option_positions failed ({type(exc).__name__}: {exc})"
    after = positions_health(emu)
    for _complete, _stale, error in (before, after):
        if error is not None:
            return rows, f"the broker's option-book health could not be read ({error})"
    if not (before[0] and after[0]):
        return rows, ("the broker's option map is incomplete (no successful refresh yet, a "
                      "failed one, or a contract whose fields could not be read)")
    if before[1] is not None or after[1] is not None:
        return rows, "the broker's positions snapshot is stale (its REST refresh is failing)"
    return rows, None


def option_positions(emu):
    """Open option positions (OptionPositionDTOs, contract §3), or None when
    the map is unreadable, incomplete or stale (I-1) — the wheel then sells
    nothing and latches nothing (fix 1 needs the whole map)."""
    rows, reason = option_book(emu)
    return rows if reason is None else None


def open_orders(emu):
    """Working orders (OrderRefs), or None when unreadable.

    fix F3: read through working_orders, the adapter's STRICT reader when it
    has one. An outage is None — the wheel then sells nothing that tick —
    never an empty book, which would let a duplicate put through (fix 1)."""
    return working_orders(emu)


def latest_trade_price(emu, symbol):
    """The adapter's latest trade price for `symbol` (plan A-live
    get_latest_trades, which the broker's approval path also prices a live
    entry at), or None when the adapter has none or the read fails."""
    reader = getattr(emu, "get_latest_trades", None)
    if not callable(reader):
        return None
    sym = str(symbol).strip().upper()
    try:
        trades = reader([sym]) or {}
    except Exception:
        return None
    entry = trades.get(sym) if isinstance(trades, dict) else None
    price = entry[0] if isinstance(entry, (tuple, list)) and entry else entry
    price = _finite(price)
    return price if price is not None and price > 0 else None


def _finite(value):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def account_options(emu):
    """{"cash", "equity", ...} for the wheel's caps (plan A-live
    get_account_options), falling back to settled cash and account equity —
    or None when either cannot be READ (M4). A cash of 0.0 invented because
    both reads failed would fail every candidate for "insufficient cash" and
    burn the week's scan; None is "not ready", and the scan retries next tick.
    A real 0.0 read from the broker is a real 0.0."""
    try:
        acct = dict(emu.get_account_options() or {})
    except Exception:
        acct = {}
    cash = _finite(acct.get("cash"))
    if cash is None:
        try:
            cash = _finite(emu.get_cash())
        except Exception:
            cash = None
    equity = _finite(acct.get("equity"))
    if equity is None:
        try:
            equity = _finite(emu.refresh_account().equity)
        except Exception:
            equity = None
    if cash is None or equity is None or equity <= 0:
        return None
    acct["cash"], acct["equity"] = cash, equity
    return acct
