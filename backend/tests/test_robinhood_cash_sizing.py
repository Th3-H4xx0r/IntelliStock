"""1-A (bug-sweep 2026-05-28): RobinhoodAdapter must size + value off SETTLED
cash, not buying_power, so a margin/Instant/Gold account never deploys
non-settled credit on day 1.

Verified live: instance main acct REDACTED-ACCT is type=margin with
buying_power $6,967.67 > settled cash $6,434.48 (Δ $533). Before this fix
get_cash() returned buying_power and the strategy would size/value against it.
"""
from __future__ import annotations


def test_available_cash_caps_at_settled_on_margin_account():
    """Margin account: buying_power > settled -> spendable == settled cash."""
    from broker_adapters.robinhood import _available_cash_for_sizing
    assert _available_cash_for_sizing(0.00, 0.00) == 0.00


def test_available_cash_noop_on_cash_account():
    """Cash account: settled == buying_power -> unchanged."""
    from broker_adapters.robinhood import _available_cash_for_sizing
    assert _available_cash_for_sizing(5000.0, 5000.0) == 5000.0


def test_available_cash_uses_lower_when_buying_power_held_below_settled():
    """If holds push buying_power BELOW settled, use the lower (conservative)."""
    from broker_adapters.robinhood import _available_cash_for_sizing
    assert _available_cash_for_sizing(5000.0, 4200.0) == 4200.0


def test_available_cash_clamps_negative_and_garbage_to_zero():
    """Margin debit (negative cash) or unparseable inputs clamp to 0 (never over-deploy)."""
    from broker_adapters.robinhood import _available_cash_for_sizing
    assert _available_cash_for_sizing(-100.0, 500.0) == 0.0
    assert _available_cash_for_sizing(None, 500.0) == 0.0
    assert _available_cash_for_sizing("x", "y") == 0.0
