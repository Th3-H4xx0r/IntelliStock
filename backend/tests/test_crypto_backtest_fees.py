"""Crypto backtest fills must model the ~0.25% taker fee; equities stay commission-free.

Crypto is NEVER commission-free. A crypto buy+sell round trip should lose ~0.5%
(two legs at the taker fee) to fees, while an identical equity round trip loses
nothing. The fee is gated on ``"/" in symbol`` (equities never contain a slash),
so EQUITY behavior is byte-identical to the pre-change commission-free math.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from portfolio_emulator import PortfolioEmulator, _CRYPTO_TAKER_FEE  # noqa: E402


def _round_trip_loss_fraction(symbol: str, price: float) -> float:
    """Buy $1000 of ``symbol`` then sell the whole position at the same price;
    return the fraction of the $1000 notional lost to fees."""
    pe = PortfolioEmulator(initial_cash=10000.0)
    start_cash = pe.get_cash()
    assert pe.execute_signal(symbol, 1, price, cash_per_trade=1000.0)
    assert pe.execute_signal(symbol, -1, price, sell_fraction=1.0)
    end_cash = pe.get_cash()
    assert pe.get_positions() == {}  # fully sold, nothing left
    return (start_cash - end_cash) / 1000.0


def test_taker_fee_is_the_crypto_fee():
    # Sourced from strategies.crypto.core.CRYPTO_FEES["taker"], falling back to 0.0025.
    assert abs(_CRYPTO_TAKER_FEE - 0.0025) < 1e-12


def test_crypto_round_trip_loses_two_taker_fees():
    loss = _round_trip_loss_fraction("BTC/USD", 50000.0)
    expected = 1.0 - (1.0 - _CRYPTO_TAKER_FEE) ** 2  # ~0.005 (both legs)
    assert abs(loss - expected) < 1e-9
    assert abs(loss - 0.005) < 5e-4  # ~0.5%


def test_equity_round_trip_is_commission_free():
    loss = _round_trip_loss_fraction("AAPL", 100.0)
    assert abs(loss) < 1e-12  # exactly zero — commission-free


def test_equity_fill_shares_and_cash_unchanged():
    """Equity buy still credits the exact commission-free share count and cash."""
    pe = PortfolioEmulator(initial_cash=10000.0)
    assert pe.execute_signal("AAPL", 1, 100.0, cash_per_trade=1000.0)
    assert abs(pe.get_positions()["AAPL"] - 10.0) < 1e-12  # $1000 / $100
    assert abs(pe.get_cash() - 9000.0) < 1e-12


def test_crypto_buy_credits_fewer_coins_but_decrements_full_cash():
    pe = PortfolioEmulator(initial_cash=10000.0)
    assert pe.execute_signal("BTC/USD", 1, 50000.0, cash_per_trade=1000.0)
    # Commission-free would buy 0.02 BTC; the taker fee buys fewer coins.
    expected = 0.02 * (1.0 - _CRYPTO_TAKER_FEE)
    assert abs(pe.get_positions()["BTC/USD"] - expected) < 1e-12
    # Cash is still decremented by the FULL $1000 notional (the fee is embedded
    # as fewer coins, not left sitting in cash).
    assert abs(pe.get_cash() - 9000.0) < 1e-9


def test_crypto_sell_proceeds_are_net_of_fee():
    pe = PortfolioEmulator(initial_cash=10000.0)
    # Seed a clean position (buy on a crypto symbol also nets the fee, so seed via
    # a direct buy and read the actual filled qty).
    assert pe.buy("ETH/USD", 1.0, 2000.0)  # filled qty is 1.0 * (1 - taker)
    qty = pe.get_positions()["ETH/USD"]
    cash_before = pe.get_cash()
    assert pe.sell("ETH/USD", qty, 2000.0)
    proceeds = pe.get_cash() - cash_before
    assert abs(proceeds - qty * 2000.0 * (1.0 - _CRYPTO_TAKER_FEE)) < 1e-9
