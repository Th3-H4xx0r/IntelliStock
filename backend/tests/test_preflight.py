"""Preflight: quota budget + order gate."""
import os
import sys

_backend = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

import pytest

from broker_adapters._preflight import alpaca_quota_budget, preflight_order
from broker_adapters.errors import (
    InsufficientBuyingPower, PDTRestricted, AssetNotTradable, BrokerPreflightBlocked,
)


def test_quota_budget_ok():
    b = alpaca_quota_budget(n_symbols=25, calls_per_cycle=3, cycles_per_min=1.0)
    assert b.est_rpm == 75
    assert b.ok is True


def test_quota_budget_refuses():
    b = alpaca_quota_budget(n_symbols=100, calls_per_cycle=3, cycles_per_min=1.0)
    assert b.est_rpm == 300
    assert b.ok is False


def test_preflight_blocks_untradable():
    with pytest.raises(AssetNotTradable):
        preflight_order(
            symbol="XYZ", side="buy", qty=1.0, est_price=100.0,
            account_equity=50000, cash_available=50000,
            day_trade_count=0, asset_tradable=False,
            asset_fractionable=True, is_fractional=False,
        )


def test_preflight_blocks_insufficient_bp():
    with pytest.raises(InsufficientBuyingPower):
        preflight_order(
            symbol="AAPL", side="buy", qty=100.0, est_price=200.0,
            account_equity=50000, cash_available=1000,
            day_trade_count=0, asset_tradable=True,
            asset_fractionable=True, is_fractional=False,
        )


def test_preflight_blocks_pdt():
    with pytest.raises(PDTRestricted):
        preflight_order(
            symbol="AAPL", side="buy", qty=1.0, est_price=100.0,
            account_equity=10000, cash_available=5000,
            day_trade_count=3, asset_tradable=True,
            asset_fractionable=True, is_fractional=False,
        )


def test_preflight_blocks_fractional_on_non_fractionable():
    with pytest.raises(BrokerPreflightBlocked):
        preflight_order(
            symbol="OTC", side="buy", qty=0.5, est_price=10.0,
            account_equity=50000, cash_available=50000,
            day_trade_count=0, asset_tradable=True,
            asset_fractionable=False, is_fractional=True,
        )


def test_preflight_passes_normal():
    preflight_order(
        symbol="AAPL", side="buy", qty=1.0, est_price=200.0,
        account_equity=50000, cash_available=5000,
        day_trade_count=0, asset_tradable=True,
        asset_fractionable=True, is_fractional=False,
    )
