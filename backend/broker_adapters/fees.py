"""Per-venue crypto fee model (used by the backtest so a strategy is valued at
the fees of the venue it will actually trade on).

Alpaca crypto = 0.25% taker; Binance.US = 0.02% taker (0% maker). The taker rate
is what the market-order backtest applies. Import-safe (no broker.py deps)."""

from __future__ import annotations

_BINANCE = ("binanceus", "binance", "binance_us", "binance.us")

# Crypto taker fee by broker_type.
CRYPTO_TAKER_FEE_BY_BROKER = {
    "alpaca": 0.0025,
    "robinhood": 0.0025,
    "binanceus": 0.0002,
}


def crypto_taker_fee(broker_type: str | None) -> float:
    """Crypto taker fee (fraction) for ``broker_type``. Defaults to Alpaca's
    0.25% for unknown/None so existing crypto backtests are unchanged."""
    t = (broker_type or "").strip().lower()
    if t in _BINANCE:
        return 0.0002
    return CRYPTO_TAKER_FEE_BY_BROKER.get(t, 0.0025)


def resolve_broker_type(instance_doc, brokerage_doc=None) -> str | None:
    """Resolve an instance's venue for the fee model.

    The Instances row's own ``broker_type`` is authoritative when present, but
    ``action_create_instance`` only stores ``brokerage_id`` (not ``broker_type``),
    so fall back to the LINKED brokerage's ``brokerage_type``. Without this a
    Binance.US-linked crypto instance would backtest at Alpaca's 0.25% instead
    of 0.02%. Returns a lower-cased type string, or None if neither is set.
    """
    bt = None
    if isinstance(instance_doc, dict):
        bt = (instance_doc.get("broker_type") or "").strip().lower() or None
    if not bt and isinstance(brokerage_doc, dict):
        bt = (brokerage_doc.get("brokerage_type") or "").strip().lower() or None
    return bt


def resolve_crypto_taker_fee(instance_doc, brokerage_doc=None) -> float:
    """Crypto taker fee for an instance, resolving the venue from the instance
    row and falling back to its linked brokerage (see :func:`resolve_broker_type`)."""
    return crypto_taker_fee(resolve_broker_type(instance_doc, brokerage_doc))
