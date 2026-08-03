"""Tests for the minimum holding period (backend/min_hold.py).

Pins two things above all: the DEFAULT-OFF contract, so an untouched doc-179
parses to a disabled gate; and the risk-exit exemption, because a holding floor
that traps a position a stop wants out of costs far more than the spread it
saves.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from min_hold import (  # noqa: E402
    MinHoldConfig,
    holding_days,
    min_hold_config,
    sell_is_blocked,
)

NOW = datetime(2026, 8, 3, 16, 0, tzinfo=timezone.utc)

# Trimmed from the live Strategies doc 179 (read-only). No min_hold_* key
# exists there, which is the point of the first test.
DOC179_SUBSET = {
    "nexus_portfolio_pct": 0.95,
    "cash_reserve_floor_pct": 0.02,
    "single_position_max_pct": 25,
    "max_positions": 14,
}
ON = dict(DOC179_SUBSET, min_hold_enabled=True, min_hold_days=30)


def _ago(days):
    return NOW - timedelta(days=days)


# ── default-off contract ──────────────────────────────────────────────────

def test_live_config_parses_to_disabled_gate():
    cfg = min_hold_config(DOC179_SUBSET)
    assert cfg.enabled is False


def test_disabled_gate_never_blocks():
    cfg = min_hold_config(DOC179_SUBSET)
    for risk in (True, False):
        blocked, reason = sell_is_blocked(
            cfg, entry_ts=_ago(0), now=NOW, is_risk_exit=risk)
        assert blocked is False and reason == "disabled"


def test_empty_and_garbage_config_never_raise():
    for bad in (None, {}, {"min_hold_enabled": "yes", "min_hold_days": "abc"},
                {"min_hold_enabled": None, "min_hold_days": None}):
        cfg = min_hold_config(bad)
        sell_is_blocked(cfg, entry_ts=_ago(1), now=NOW, is_risk_exit=False)


def test_truthy_spellings_enable_the_gate():
    for raw in (True, "true", "TRUE", "1", "yes", "on"):
        assert min_hold_config({"min_hold_enabled": raw}).enabled is True
    for raw in (False, "false", "0", "no", "off", "", None):
        assert min_hold_config({"min_hold_enabled": raw}).enabled is False


def test_zero_days_is_equivalent_to_off():
    cfg = min_hold_config(dict(ON, min_hold_days=0))
    blocked, reason = sell_is_blocked(
        cfg, entry_ts=_ago(0), now=NOW, is_risk_exit=False)
    assert blocked is False and reason == "disabled"


def test_negative_days_is_clamped_not_inverted():
    assert min_hold_config(dict(ON, min_hold_days=-5)).min_days == 0


# ── the gate itself ───────────────────────────────────────────────────────

def test_a_fresh_position_cannot_be_sold_on_signal():
    cfg = min_hold_config(ON)
    blocked, reason = sell_is_blocked(
        cfg, entry_ts=_ago(3), now=NOW, is_risk_exit=False)
    assert blocked is True
    assert "min_hold" in reason and "30" in reason


def test_a_matured_position_sells_normally():
    cfg = min_hold_config(ON)
    for age in (30, 30.5, 90):
        blocked, _ = sell_is_blocked(
            cfg, entry_ts=_ago(age), now=NOW, is_risk_exit=False)
        assert blocked is False, age


def test_the_boundary_is_inclusive():
    """Exactly min_days must be sellable, or a 30-day floor is really 31."""
    cfg = min_hold_config(ON)
    blocked, _ = sell_is_blocked(
        cfg, entry_ts=_ago(30), now=NOW, is_risk_exit=False)
    assert blocked is False
    blocked, _ = sell_is_blocked(
        cfg, entry_ts=_ago(29.9), now=NOW, is_risk_exit=False)
    assert blocked is True


# ── the exemption that matters most ───────────────────────────────────────

def test_risk_exits_are_never_blocked_at_any_age():
    """Stops, circuit breakers, forced exits and sell-enforcement must always
    get out. A floor that traps a loser costs more than the spread it saves."""
    cfg = min_hold_config(ON)
    for age in (0, 0.001, 1, 29.9):
        blocked, reason = sell_is_blocked(
            cfg, entry_ts=_ago(age), now=NOW, is_risk_exit=True)
        assert blocked is False, age
        assert reason == "risk_exit_exempt"


def test_an_undatable_position_fails_OPEN():
    """No entry timestamp -> allow the sell.

    Pre-existing holdings, a restart that lost state, or an externally-opened
    position have no recorded entry. Blocking those would trap capital
    indefinitely with no operator-visible cause — strictly worse than letting
    one extra sell through. The gate is a cost optimisation, never a liquidity
    trap.
    """
    cfg = min_hold_config(ON)
    for missing in (None, "", "   ", "not-a-date", 12345.6):
        blocked, reason = sell_is_blocked(
            cfg, entry_ts=missing, now=NOW, is_risk_exit=False)
        assert blocked is False, missing
        assert reason in ("no_entry_timestamp", "negative_age")


def test_clock_skew_does_not_block():
    """An entry stamped in the future must not read as 'held -3 days' and
    block forever."""
    cfg = min_hold_config(ON)
    blocked, reason = sell_is_blocked(
        cfg, entry_ts=NOW + timedelta(days=3), now=NOW, is_risk_exit=False)
    assert blocked is False and reason == "negative_age"


# ── timestamp handling ────────────────────────────────────────────────────

def test_naive_and_iso_and_z_timestamps_all_parse():
    cfg = min_hold_config(ON)
    for entry in (
        datetime(2026, 8, 1, 16, 0),                       # naive -> UTC
        datetime(2026, 8, 1, 16, 0, tzinfo=timezone.utc),  # aware
        "2026-08-01T16:00:00Z",                            # Z suffix
        "2026-08-01T16:00:00+00:00",                       # offset
    ):
        blocked, reason = sell_is_blocked(
            cfg, entry_ts=entry, now=NOW, is_risk_exit=False)
        assert blocked is True, entry
        assert reason.startswith("min_hold_2."), (entry, reason)


def test_holding_days_is_none_only_when_undatable():
    assert holding_days(None, NOW) is None
    assert holding_days("garbage", NOW) is None
    assert holding_days(_ago(2), NOW) == 2.0


def test_frozen_config_cannot_be_mutated_at_a_call_site():
    cfg = MinHoldConfig(enabled=True, min_days=30)
    try:
        cfg.min_days = 0  # type: ignore[misc]
    except Exception:
        return
    raise AssertionError("MinHoldConfig must be frozen")
