"""The LIVE counterpart of ``PortfolioEmulator.pending_execution_symbols()``.

In a backtest the object a run-once strategy receives as ``portfolio_emulator``
is a ``PortfolioEmulator``, and it can say which symbols still have an order the
simulator has not resolved. Live, that same argument is the broker ADAPTER
(``broker.py``: ``portfolio_emulator = live_adapter``), which has no such
method — so Strategy EB's pending-buy guard, whose whole job is to refuse a
second buy while the first is unfilled, had nothing to read and blocked every
buy of every session. That is why ``pending_buy_guard_enabled`` defaults off.

This module supplies the missing reader, with the emulator's contract: a tuple
of non-empty, already-upper-cased symbols.

THE ONE RULE HERE IS FAIL CLOSED. ``AlpacaAdapter.list_open_orders`` swallows
every exception and returns ``[]``, so a dead orders endpoint and a genuinely
flat book give the same answer (``tests/test_zz_adversarial_sweep.py::
test_j_the_open_order_count_is_fail_open_not_minus_one`` documents exactly
that). A guard built on that answer would wave through the duplicate buy it
exists to stop. Every doubt here raises instead: the caller's guard treats an
exception as "pending state unavailable", blocks new buys and keeps sells,
which is the safe side of the error for an account that is already long.
"""
from __future__ import annotations

#: Order states from which no further fill can arrive. Anything else — `new`,
#: `accepted`, `pending_new`, `partially_filled`, `held`, or a state Alpaca
#: adds tomorrow — counts as unresolved. Unrecognised is not evidence of a
#: fill, so the unknown state blocks rather than releases a buy.
TERMINAL_ORDER_STATES = frozenset({
    "filled",
    "canceled",
    "cancelled",
    "rejected",
    "expired",
    "done_for_day",
    "replaced",
})


class LivePendingOrdersUnavailable(RuntimeError):
    """The live order book could not be read, so pending state is UNKNOWN.

    Never raised to mean "nothing is pending" — that answer is ``()``.
    """


def live_pending_symbols(adapter) -> tuple[str, ...]:
    """Symbols with an unresolved order at the broker, or an exception.

    Sourced from the broker itself rather than from ``LiveOrderService``'s
    local lifecycle store: the store knows only the orders this instance
    submitted through the typed path, while the guard's question — "could a
    buy I send now double an exposure?" — is answered only by the account's
    whole working-order book, legacy ``execute_signal`` submissions, a restart
    that lost in-memory state, and manual orders included.
    """
    reader = getattr(adapter, "list_open_orders_strict", None)
    if not callable(reader):
        raise LivePendingOrdersUnavailable(
            f"{type(adapter).__name__} exposes no fail-closed open-order "
            "reader; pending state is unknown, not empty")
    try:
        orders = reader()
    except Exception as error:
        raise LivePendingOrdersUnavailable(
            f"open-order read failed: {type(error).__name__}: {error}"
        ) from error
    if not isinstance(orders, (list, tuple)):
        raise LivePendingOrdersUnavailable(
            f"open-order read returned {type(orders).__name__}, not a list")

    pending: dict[str, None] = {}
    for order in orders:
        symbol = getattr(order, "symbol", None)
        status = getattr(order, "status", None)
        if not isinstance(symbol, str) or not symbol.strip():
            raise LivePendingOrdersUnavailable(
                "an open order carries no usable symbol")
        if not isinstance(status, str) or not status.strip():
            raise LivePendingOrdersUnavailable(
                f"the open order for {symbol.strip().upper()} carries no "
                "usable status")
        if status.strip().lower() in TERMINAL_ORDER_STATES:
            continue
        pending[symbol.strip().upper()] = None
    return tuple(pending)


class LivePendingOrderView:
    """A live adapter plus the one reader the emulator has and it does not.

    Read-through by design: every other attribute — ``get_portfolio_value``,
    ``get_positions``, ``get_cash``, the private attrs ``BrokerAdapter``
    deliberately mirrors — resolves on the adapter itself, so the strategy sees
    exactly the live account it saw before.

    The reader is attached HERE, on a per-lane wrapper, and not to the adapter
    class, because ``pending_execution_symbols`` is not EB's alone: Strategy X
    reads it off the emulator through ``getattr`` and today finds nothing, so
    giving the adapter the method would quietly change Strategy X's live
    kicker as a side effect of fixing EB's guard.
    """

    def __init__(self, adapter):
        self._adapter = adapter

    def pending_execution_symbols(self) -> tuple[str, ...]:
        return live_pending_symbols(self._adapter)

    def __getattr__(self, name):
        if name == "_adapter":  # only before __init__ ran; never recurse.
            raise AttributeError(name)
        return getattr(self._adapter, name)

    def __repr__(self) -> str:
        return f"LivePendingOrderView({self._adapter!r})"
