"""Tests for the real-config fixture (backend/tests/nexus_real_config.py).

Guards the property that makes the fixture worth having: strategy tests built
on it run against the REAL live doc-179 config (grace keys present at their
live values), so the V31-grace config-vs-default blind spot cannot recur, and
that no live secret ever leaks into a test config.
"""

import os
import sys

_HERE = os.path.dirname(__file__)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from nexus_real_config import real_config  # noqa: E402


def test_grace_keys_present_at_live_values():
    """The V31 grace keys Round-1 fixtures lacked are present and ACTIVE."""
    cfg = real_config()
    assert cfg["initial_grace_enabled"] is True
    assert cfg["initial_grace_bars"] == 14
    assert cfg["initial_grace_catastrophic_loss_pct"] == -15
    assert cfg["initial_grace_cumulative_loss_pct"] == -10
    assert cfg["initial_grace_cumulative_min_days"] == 5
    assert cfg["initial_grace_regime_escape_enabled"] is True


def test_fast_loser_cut_pct_is_live_value():
    cfg = real_config()
    assert cfg["fast_loser_cut_pct"] == -10


def test_tune_overlay_applied():
    cfg = real_config()
    assert cfg["portfolio_drawdown_halt_pct"] == 8
    assert cfg["max_positions"] == 10
    assert cfg["cash_reserve_floor_pct"] == 0.02
    assert cfg["profitable_min_hold_conviction_override_enabled"] is False


def test_no_secret_like_keys_leak():
    cfg = real_config()
    for k in cfg:
        lk = str(k).lower()
        assert not any(
            s in lk for s in ("key", "secret", "password", "token")
        ), f"secret-like key leaked into test config: {k}"
    # Spot-check the specific live secrets are gone.
    for gone in ("alpaca_key", "alpaca_secret", "neo4j_password", "benzinga_api_key"):
        assert gone not in cfg


def test_overrides_win():
    cfg = real_config(initial_grace_enabled=False, fast_loser_cut_pct=-99)
    assert cfg["initial_grace_enabled"] is False
    assert cfg["fast_loser_cut_pct"] == -99
