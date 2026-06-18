"""Derive each currently-held position's *acquisition date* (when its current
open episode began) from filled order history.

Pure + dependency-free so it's unit-testable. Used by the
``/brokerages/{id}/holding-opens`` API endpoint to let the mobile Holdings
"Total" sparkline clip to the holding period instead of the stock's whole
history.

The walk handles sell-then-rebuy: a symbol's open date is the buy that took the
running quantity from ~0 up, in the *latest* still-open accumulation episode. If
the reconstructed final quantity doesn't match the broker's reported position
size (e.g. the opening fill is older than the fetched order window), the symbol
is omitted — the caller then falls back to the full series rather than guessing.
"""

from collections import defaultdict
from typing import Iterable, Mapping

_EPS = 1e-9


def derive_open_dates(
    fills: Iterable[Mapping],
    held_qty: Mapping[str, float],
    qty_tol: float = 0.02,
) -> dict:
    """Return ``{symbol: opened_at_iso}`` for held symbols whose current open
    episode start is reconstructable from ``fills``.

    fills: dicts with ``symbol`` (str), ``side`` ('buy'/'sell'), ``qty`` (float,
           filled qty), ``ts_iso`` (str), ``ts_sort`` (comparable for ordering).
    held_qty: ``{symbol: qty}`` current position size (only these are returned).
    qty_tol: relative tolerance when matching reconstructed vs. reported qty.
    """
    by_sym: dict = defaultdict(list)
    held = {str(k).upper(): float(v or 0.0) for k, v in held_qty.items()}
    for f in fills:
        sym = str(f.get("symbol") or "").upper()
        if sym and sym in held:
            by_sym[sym].append(f)

    out: dict = {}
    for sym, fl in by_sym.items():
        fl.sort(key=lambda x: x.get("ts_sort"))
        qty = 0.0
        open_ts = None
        for f in fl:
            q = float(f.get("qty") or 0.0)
            if q <= 0:
                continue
            side = str(f.get("side") or "").lower()
            if qty <= _EPS and side == "buy":
                open_ts = f.get("ts_iso")
            qty += q if side == "buy" else -q
            if qty <= _EPS:
                qty = 0.0
                open_ts = None

        target = held.get(sym, 0.0)
        if open_ts and target > 0:
            tol = max(qty_tol * target, 1e-3)
            if abs(qty - target) <= tol:
                out[sym] = open_ts
    return out
