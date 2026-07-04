"""Run-185254 leak #1: rotation executed the SELL leg, then the broker killed
the BUY on the $8 price floor (SLBT $3.17, ABSI $7.38) — sell-only rotations
stranded ~$17k in cash. The incoming leg must be validated BEFORE selling."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.strategies.graph_nexus_analysis import _rotation_incoming_executable


def test_sub_floor_incoming_rejected():
    ok, reason = _rotation_incoming_executable(
        "ABSI", 7.38, {"buy_price_floor": 8.0})
    assert ok is False
    assert "price floor" in reason.lower() or "8.00" in reason


def test_above_floor_incoming_accepted():
    ok, reason = _rotation_incoming_executable(
        "WDC", 562.88, {"buy_price_floor": 8.0})
    assert ok is True and reason == ""


def test_zero_floor_accepts_all():
    ok, _ = _rotation_incoming_executable("SLBT", 3.17, {"buy_price_floor": 0})
    assert ok is True


def test_etf_exempt_from_floor():
    # The broker gate exempts ETFs from the price floor; the pre-validation
    # must agree with the broker, not be stricter than it.
    ok, _ = _rotation_incoming_executable(
        "XA", 7.00, {"buy_price_floor": 8.0}, asset_class="etf")
    assert ok is True


def test_unknown_price_accepted():
    # Fail-open on missing price data: the broker re-checks at execution;
    # pre-validation must not veto on data it doesn't have.
    ok, _ = _rotation_incoming_executable("NEWCO", 0.0, {"buy_price_floor": 8.0})
    assert ok is True
