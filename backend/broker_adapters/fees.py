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
