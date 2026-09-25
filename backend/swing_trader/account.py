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
