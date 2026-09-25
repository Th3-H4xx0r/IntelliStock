"""Read-only views of the book, shared by both lanes.

Platform glue with no ST source. Every reader tolerates an emulator or adapter
that lacks the method: it degrades to "no data" — which the callers treat as
"do not add exposure" — and never raises into run_once.
"""
from __future__ import annotations


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


def live_buying_power(emu) -> float:
    """ST sized swing entries against account.buying_power (paper_trader.py:450)."""
    try:
        return float(emu.refresh_cash().buying_power)
    except Exception:
        return float(emu.get_cash() or 0.0)


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
