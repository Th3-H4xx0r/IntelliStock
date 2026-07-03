"""Locking tests for drawdown-halt precedence in live_mode_overrides.

Task 6 requirement: an EXPLICIT ``portfolio_drawdown_halt_pct`` in the user's
Strategies (doc-179) config must win over the live-mode default-tightening
override, while a config that OMITS the key still inherits the live-mode safety
default (10.0). All other LIVE_OVERRIDES must remain unconditional.

apply_live_overrides is called in live boot at broker.py:6034 (per-strategy
resolved DB config), broker.py:5584 (EHP config), and nexus_restamp.py:64
(identity resolution) — always with the user's resolved config dict, which is
exactly the surface exercised below.
"""

from __future__ import annotations

from live_mode_overrides import LIVE_OVERRIDES, apply_live_overrides


def test_explicit_drawdown_pct_survives_live_override():
    """User's explicit 8.0 (doc-179 Track-B value) must NOT be clobbered."""
    user_cfg = {"portfolio_drawdown_halt_pct": 8.0}

    merged = apply_live_overrides(user_cfg)

    assert merged["portfolio_drawdown_halt_pct"] == 8.0
    # Input dict is never mutated.
    assert user_cfg["portfolio_drawdown_halt_pct"] == 8.0


def test_absent_drawdown_pct_gets_live_default():
    """A config WITHOUT the key inherits the live-mode safety default 10.0."""
    user_cfg = {"some_other_key": 123}

    merged = apply_live_overrides(user_cfg)

    assert merged["portfolio_drawdown_halt_pct"] == 10.0
    assert merged["some_other_key"] == 123


def test_other_overrides_remain_unconditional():
    """Non-drawdown safety overrides still win over user config (unchanged)."""
    user_cfg = {
        "analyst_panel_enabled": True,
        "private_entity_bridge_enabled": True,
        "portfolio_drawdown_halt_enabled": False,
    }

    merged = apply_live_overrides(user_cfg)

    assert merged["analyst_panel_enabled"] is False
    assert merged["private_entity_bridge_enabled"] is False
    assert merged["portfolio_drawdown_halt_enabled"] is True
    # The override table itself is not mutated.
    assert LIVE_OVERRIDES["portfolio_drawdown_halt_pct"] == 10.0


def test_none_config_gets_all_defaults():
    """None config still yields the full live-mode default set."""
    merged = apply_live_overrides(None)

    assert merged["portfolio_drawdown_halt_pct"] == 10.0
    assert merged["analyst_panel_enabled"] is False


# --- Task 11: quality_filter_missing_metadata_policy is operator-owned -------
#
# Config archaeology found live silently forces the doc-179 value "warn" to
# "block", narrowing the LIVE buy universe relative to every backtest (a
# missing-metadata symbol every backtest scored is blocked live). The policy is
# the operator's chosen strictness, not a fail-closed clamp, so an explicit
# value must survive.


def test_explicit_quality_filter_policy_survives_live_override():
    """User's explicit "warn" (doc-179 value) must NOT be clobbered to "block"."""
    user_cfg = {"quality_filter_missing_metadata_policy": "warn"}

    merged = apply_live_overrides(user_cfg)

    assert merged["quality_filter_missing_metadata_policy"] == "warn"
    # Input dict is never mutated.
    assert user_cfg["quality_filter_missing_metadata_policy"] == "warn"


def test_absent_quality_filter_policy_gets_live_default():
    """A config WITHOUT the key inherits the live-mode safety default "block"."""
    user_cfg = {"some_other_key": 123}

    merged = apply_live_overrides(user_cfg)

    assert merged["quality_filter_missing_metadata_policy"] == "block"


# --- Task 11: break_glass_fresh_shield_enabled is operator-owned -------------
#
# Live silently flips the doc-179 value False to True. The fresh-shield is a
# behavioral toggle the operator tunes (it vetoes fresh-position exits), not a
# fail-closed safety clamp, so an explicit False must survive.


def test_explicit_fresh_shield_survives_live_override():
    """User's explicit False must NOT be clobbered to True."""
    user_cfg = {"break_glass_fresh_shield_enabled": False}

    merged = apply_live_overrides(user_cfg)

    assert merged["break_glass_fresh_shield_enabled"] is False
    # Input dict is never mutated.
    assert user_cfg["break_glass_fresh_shield_enabled"] is False


def test_absent_fresh_shield_gets_live_default():
    """A config WITHOUT the key inherits the live-mode safety default True."""
    user_cfg = {"some_other_key": 123}

    merged = apply_live_overrides(user_cfg)

    assert merged["break_glass_fresh_shield_enabled"] is True
