"""The entry gate must measure EXTENSION, not range width.

`_recent_runup_protect` returns (max-min)/min over a sliding window. Its own
docstring says it exists to spare a HELD name from a forced EXIT at a local dip.
Reused as an entry blocker it fails three ways, all measured:

  DECAY: SNDK's own closes through the gate's formula read 129.2% on 01-30 and
    22.4% on 02-26 — while sitting +168% on the window and 6.8% off its high. It
    reads CLEAN exactly when the name is most extended.
  DIRECTION-BLIND: OMER's reading was pinned at +96.0% across six decision bars
    while its close FELL 18.7%. PLRZ was blocked at $8.11 — 14% up its own range
    and 44% BELOW its 20-day high.
  ANTI-MONOTONIC: PLRZ rose +62.7% while the reading fell 106.2% -> 78.8%.

Across three runs, 201 symbol-blocks produced 5 positions, ALL bought later on a
gate-off bar, 4 of 5 losers, net -$157.13. The gate does not prevent bad entries;
it delays them until the price is worse.

Distance above the prior N-bar HIGH is signed and base-relative. A name at a new
high is 0% extended by construction. MA-distance was ALSO tested and rejected:
cross-sectional rank-IC vs 20-day forward return over 160,645 name-days is +0.018
(close/MA20) — the wrong sign for a block.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "strategies"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from strategies.graph_nexus_analysis import (  # noqa: E402
    _extension_blocks_entry,
    _recent_runup_protect,
)

RANGE = {}
ANCHOR = {"entry_extension_metric": "anchor"}


def _bars(*closes):
    return {"X": [{"close": c} for c in closes]}


def test_default_is_byte_identical_to_the_legacy_gate():
    h = _bars(100, 120, 150, 140)
    legacy = _recent_runup_protect("X", h, 25.0, 20)
    blocked, reading, metric, _ = _extension_blocks_entry("X", h, 25.0, 20, RANGE)
    assert (blocked, reading) == legacy and metric == "range"


def test_a_name_breaking_to_a_NEW_HIGH_is_admitted():
    """SNDK at $333 with a prior 20-bar high near $328: +1.5% extended, not +73%."""
    h = _bars(237, 262, 270, 300, 328, 333)
    blocked_r, reading_r, _, _ = _extension_blocks_entry("X", h, 25.0, 20, RANGE)
    blocked_a, reading_a, _, _ = _extension_blocks_entry("X", h, 25.0, 20, ANCHOR)
    assert blocked_r is True and reading_r > 25          # legacy: blocked
    assert blocked_a is False and reading_a < 25         # anchor: admitted
    assert reading_a == pytest.approx((333 - 328) / 328 * 100, abs=0.01)


def test_a_genuine_parabolic_gap_is_still_blocked():
    """The gate must keep doing its job: a name 60% above its base is refused."""
    h = _bars(100, 101, 102, 103, 104, 170)
    blocked, reading, _, _ = _extension_blocks_entry("X", h, 25.0, 20, ANCHOR)
    assert blocked is True
    assert reading == pytest.approx((170 - 104) / 104 * 100, abs=0.01)


def test_it_is_directional_unlike_the_range(): 
    """OMER: reading pinned while price fell. Anchor falls with the price."""
    rising = _bars(100, 110, 120, 130)
    falling = _bars(100, 110, 120, 130, 106)
    _, up, _, _ = _extension_blocks_entry("X", rising, 25.0, 20, ANCHOR)
    _, down, _, _ = _extension_blocks_entry("X", falling, 25.0, 20, ANCHOR)
    assert down < up, "extension must fall when the price falls"
    assert down < 0, "below the prior high is NOT extended"
    # The legacy range is direction-BLIND: a 19% fall does not reduce it at all,
    # which is how OMER stayed pinned at +96.0% across six bars while its close
    # fell 18.7%, and how PLRZ was blocked at 44% BELOW its own 20-day high.
    _, r_up, _, _ = _extension_blocks_entry("X", rising, 25.0, 20, RANGE)
    _, r_down, _, _ = _extension_blocks_entry("X", falling, 25.0, 20, RANGE)
    assert r_down >= r_up, "the legacy range does not fall when the price falls"


def test_a_name_below_its_prior_high_is_never_blocked():
    h = _bars(100, 200, 150)
    blocked, reading, _, _ = _extension_blocks_entry("X", h, 25.0, 20, ANCHOR)
    assert blocked is False and reading < 0


def test_it_does_not_decay_when_a_name_consolidates_at_its_high():
    """The decay failure: legacy forgets the spike, anchor does not flatter it."""
    spike_then_flat = _bars(100, 300, 302, 301, 303, 302)
    _, legacy, _, _ = _extension_blocks_entry("X", spike_then_flat, 25.0, 5, RANGE)
    _, anchor, _, _ = _extension_blocks_entry("X", spike_then_flat, 25.0, 5, ANCHOR)
    assert anchor < legacy


def test_unmeasurable_extension_does_not_pass_as_zero():
    blocked, reading, _, diag = _extension_blocks_entry("X", {"X": []}, 25.0, 20, ANCHOR)
    assert blocked is False and diag.get("bars_used") == 0


def test_disabled_threshold_never_blocks():
    for cfg in (RANGE, ANCHOR):
        blocked, _, _, _ = _extension_blocks_entry("X", _bars(1, 100), 0.0, 20, cfg)
        assert blocked is False


def test_diagnostics_make_the_reading_reproducible():
    """The old reading could not be reconstructed from the log."""
    _, _, _, diag = _extension_blocks_entry("X", _bars(100, 110, 120), 25.0, 20, ANCHOR)
    assert diag["bars_used"] == 3
    assert diag["anchor"] == pytest.approx(110.0)
    assert diag["price"] == pytest.approx(120.0)
