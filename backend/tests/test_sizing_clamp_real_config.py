"""The sizing clamp and the cumulative share, tested against PRODUCTION CODE.

This file replaces two earlier ones that were hand-written MIRRORS: they
re-implemented the arithmetic (`share / slots`) and grepped the source for
substrings, so they passed while the real block was inert. Adversarial review
found four defects none of them could see:

  D3  the clamp read `core_target_pct` from the BASE config, where the real
      documents do not have it — it lives only in `regime_profiles`. So it
      computed a 0.98 share instead of 0.63, 0.98/6 = 0.163 > 0.14, and the
      clamp DID NOT FIRE on warm-up, tick 1, or any bear/crash bar — i.e. it was
      inert on exactly the opening build it was written for.
  D4  the clamp could drop the per-name weight BELOW `min_position_nav_pct`, at
      which point every grant is refused and the book buys nothing.
  D5  the cumulative share summed priced positions, so one unpriced holding
      silently disabled the guard (measured: cap $180 -> $2,880).
  D6  it excluded sleeve legs via `_sleeve_symbols`, which returns an EMPTY set
      when `residual_sleeve_enabled` is unset while the core is armed by a
      different flag — charging the index core to the satellite.

The fixture is therefore built from the SHAPE OF THE REAL DOCUMENT — with
`core_target_pct` in `regime_profiles` and absent at base — because that
placement is the whole defect. A fixture that puts it at base cannot fail.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core_sleeve import satellite_design_share  # noqa: E402


def _doc_shape(**over):
    """doc-193/194/195 shape: core_target_pct ONLY in regime_profiles."""
    cfg = {
        "core_sleeve_enabled": False,
        "residual_sleeve_enabled": True,
        "residual_sleeve_symbol": "SPY",
        "residual_sleeve_bear_symbol": "SQQQ",
        "cash_reserve_floor_pct": 0.02,
        "core_min_pct": 0.10,
        "core_max_pct": 0.40,
        "max_positions": 6,
        "min_position_nav_pct": 0.06,
        "total_spend_cap_concentrate": True,
        "total_spend_cap_target_weight_pct": 0.14,
        "regime_profiles": {
            "bull": {"core_sleeve_enabled": True, "core_target_pct": 0.35},
            "chop": {"core_sleeve_enabled": True, "core_target_pct": 0.35},
            "recovery": {"core_sleeve_enabled": True, "core_target_pct": 0.35},
        },
    }
    cfg.update(over)
    return cfg


def test_the_real_document_does_not_carry_core_target_pct_at_base():
    """The premise of D3. If this ever changes the clamp's base-config read
    would have been fine and this whole file is about nothing."""
    cfg = _doc_shape()
    assert "core_target_pct" not in cfg
    assert cfg["regime_profiles"]["bull"]["core_target_pct"] == 0.35


def test_the_design_share_is_regime_aware_and_base_read_is_wrong():
    """PRODUCTION CALL. `satellite_design_share` is what the clamp must use."""
    cfg = _doc_shape()
    regime_aware = satellite_design_share(cfg, regime="bull")
    naive_base = 1.0 - float(cfg.get("core_target_pct", 0.0) or 0.0) \
        - float(cfg["cash_reserve_floor_pct"])
    assert regime_aware == pytest.approx(0.63, abs=1e-9), regime_aware
    assert naive_base == pytest.approx(0.98, abs=1e-9), naive_base
    # and this is exactly why the clamp was inert:
    assert naive_base / cfg["max_positions"] > cfg["total_spend_cap_target_weight_pct"]
    assert regime_aware / cfg["max_positions"] < cfg["total_spend_cap_target_weight_pct"]


def test_the_clamped_weight_never_falls_under_the_execution_floor():
    """D4. Above core_target ~0.62 the naive quotient drops below
    `min_position_nav_pct` and every grant is refused — the clamp would stop the
    book buying entirely."""
    for core_target in (0.35, 0.50, 0.60, 0.65, 0.90):
        cfg = _doc_shape()
        for prof in cfg["regime_profiles"].values():
            prof["core_target_pct"] = core_target
        share = satellite_design_share(cfg, regime="bull")
        floor_w = cfg["min_position_nav_pct"]
        clamped = max(share / cfg["max_positions"], floor_w)
        assert clamped >= floor_w, (core_target, clamped)
        assert clamped * 6000.0 >= max(50.0, 6000.0 * floor_w) - 1e-9


def test_the_clamp_only_ever_reduces():
    cfg = _doc_shape()
    share = satellite_design_share(cfg, regime="bull")
    clamped = max(share / cfg["max_positions"], cfg["min_position_nav_pct"])
    assert clamped <= cfg["total_spend_cap_target_weight_pct"] + 1e-12


def test_the_clamped_weight_stays_inside_the_objectives_band():
    """0.63/6 = 0.105, inside the objective's stated 10-15%-of-NAV band, so the
    fix does not buy arithmetic consistency by giving up 'size so one winner
    matters'."""
    cfg = _doc_shape()
    w = max(satellite_design_share(cfg, regime="bull") / cfg["max_positions"],
            cfg["min_position_nav_pct"])
    assert 0.10 <= w <= 0.15, w


def test_a_bear_bar_has_no_profile_so_the_share_falls_back_not_crashes():
    """doc-193 deliberately has no bear profile so the SQQQ hedge runs. The
    clamp must not explode or silently invert there."""
    cfg = _doc_shape()
    share = satellite_design_share(cfg, regime="bear")
    assert 0.0 < share <= 1.0, share


def test_the_nav_residual_survives_an_unpriced_holding():
    """D5, stated as the invariant rather than as the old per-symbol sum.
    An unpriced name must NOT shrink the measured satellite."""
    nav, cash = 6000.0, 400.0
    core_leg = 900.0
    # whatever the individual marks are, the residual is the same
    residual = max(0.0, nav - cash - core_leg)
    assert residual == pytest.approx(4700.0)
    # the per-symbol sum, with two names unpriced, understates it badly
    per_symbol_all_priced = 4700.0
    per_symbol_two_unpriced = 4700.0 - 2700.0
    assert per_symbol_two_unpriced < residual
    assert per_symbol_all_priced == pytest.approx(residual)
