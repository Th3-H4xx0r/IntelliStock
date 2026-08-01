"""Split detection (2026-08-02). See backend/split_detect.py.

Anchored on the real event: VGT 8-for-1 on 2026-04-21, $808.88 -> $101.57
(ratio 7.964), which cost three backtest runs before it was found.
"""
import os
import sys

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from split_detect import (  # noqa: E402
    detect_split_ratio, adjust_closes_for_splits, MIN_MOVE_PCT,
)


def test_detects_the_real_vgt_split():
    assert detect_split_ratio(808.88, 101.57) == 8.0


def test_forward_split_returns_share_multiplier():
    for ratio in (2.0, 3.0, 4.0, 5.0, 10.0, 20.0):
        assert detect_split_ratio(1000.0, 1000.0 / ratio) == ratio


def test_reverse_split_returns_share_divisor():
    # 1-for-10 reverse: price x10, shares /10 -> ratio 0.1
    assert abs(detect_split_ratio(2.0, 20.0) - 0.1) < 1e-9


def test_ordinary_crash_is_not_a_split():
    """The property that matters most: never erase a real loss."""
    for pct in (0.36, 0.40, 0.45, 0.55, 0.60, 0.70):
        assert detect_split_ratio(100.0, 100.0 * (1 - pct)) is None, pct


def test_small_moves_are_ignored():
    assert detect_split_ratio(100.0, 100.0 * (1 - MIN_MOVE_PCT / 2)) is None
    assert detect_split_ratio(100.0, 99.0) is None


def test_near_miss_ratios_rejected():
    # 2.20x and 7.0x-adjacent values are not split factors within tolerance
    assert detect_split_ratio(110.0, 50.0) is None       # 2.20x
    assert detect_split_ratio(100.0, 11.5) is None       # 8.70x


def test_tolerance_boundary_is_respected():
    assert detect_split_ratio(100.0, 100.0 / 8.0) == 8.0           # exact
    assert detect_split_ratio(100.0, 100.0 / 7.90) == 8.0          # 1.25% off
    assert detect_split_ratio(100.0, 100.0 / 7.70) is None         # 3.8% off


def test_bad_input_is_safe():
    for a, b in ((None, 10.0), (10.0, None), (0.0, 10.0), (10.0, 0.0),
                 (-5.0, 10.0), ("x", 10.0)):
        assert detect_split_ratio(a, b) is None


def test_series_is_made_continuous():
    """A 20-bar series spanning an 8-for-1 split must show a real return, not -87%."""
    pre = [800.0, 802.0, 806.0, 808.0]
    post = [101.0, 102.0, 103.0]
    adj, found = adjust_closes_for_splits(pre + post)
    assert len(found) == 1 and found[0][1] == 8.0
    # pre-split prices restated onto the post-split scale
    assert abs(adj[0] - 100.0) < 0.01
    assert abs(adj[3] - 101.0) < 0.01
    # the series is now monotone-ish and the total return is sane, not -87%
    total = (adj[-1] / adj[0] - 1) * 100
    assert 0.0 < total < 10.0, total


def test_series_without_a_split_is_unchanged():
    closes = [100.0, 101.0, 99.0, 103.0, 97.0]
    adj, found = adjust_closes_for_splits(closes)
    assert found == []
    assert adj == closes


def test_series_with_a_real_crash_is_unchanged():
    closes = [100.0, 101.0, 55.0, 54.0, 56.0]   # -45%, not a split
    adj, found = adjust_closes_for_splits(closes)
    assert found == [] and adj == closes


def test_multiple_splits_compound_correctly():
    # 2-for-1 then 5-for-1: the earliest price divides by 10 overall
    closes = [1000.0, 500.0, 100.0]
    adj, found = adjust_closes_for_splits(closes)
    assert [r for _, r in found] == [2.0, 5.0]
    assert abs(adj[0] - 100.0) < 1e-6 and abs(adj[1] - 100.0) < 1e-6


def test_empty_and_short_series():
    assert adjust_closes_for_splits([]) == ([], [])
    assert adjust_closes_for_splits([100.0]) == ([100.0], [])


# --- the peak sign, and why bar inference is off by default ---

def test_price_divides_by_the_share_multiplier():
    """The contract: shares *= ratio, price /= ratio. Getting this backwards on
    a stored peak restated VGT's $808.88 to $6471 — a 98.4% drop-from-peak,
    worse than the 87.4% bug it was meant to fix — and persisted it."""
    ratio = detect_split_ratio(808.88, 101.57)
    assert ratio == 8.0
    assert abs(808.88 / ratio - 101.11) < 0.01      # correct
    assert abs(808.88 * ratio - 6471.04) < 0.01     # what the bug produced


def test_price_ratio_alone_cannot_separate_a_crash_from_a_split():
    """The limit of this heuristic, stated explicitly.

    Measured on the production caches: of 345 one-bar discontinuities only 96
    were splits — 176 were GENUINE crashes, a 65% false-positive rate. Most
    crashes land on no round ratio and are correctly rejected...
    """
    assert detect_split_ratio(100.0, 17.0) is None    # ATYR-style -83%
    assert detect_split_ratio(100.0, 12.0) is None    # MREO-style -88%
    assert detect_split_ratio(100.0, 55.0) is None    # -45%

    # ...but an exactly-90% collapse is ARITHMETICALLY IDENTICAL to a 10:1
    # split and is indistinguishable from price alone. This is why the ratio
    # test may gate an ORDER for one tick (cheap if wrong) but must never
    # silently rewrite stored prices (unbounded if wrong), and why bar-level
    # inference is opt-in while adjustment="split" from the venue is the
    # default. A corporate-actions feed is the only real disambiguator.
    assert detect_split_ratio(100.0, 10.0) == 10.0


# --- authoritative confirmation, the only real disambiguator ---

def test_corporate_action_confirms_and_returns_the_exact_ratio():
    """VGT printed 7.964 for a true 8.0. Restating by the observed value leaves
    a residual error in every downstream return, so the feed's value wins."""
    from split_detect import reconcile_with_corporate_action
    assert reconcile_with_corporate_action(7.964, 8.0) == 8.0


def test_corporate_action_rejects_a_crash_that_mimics_a_split():
    """A -90% collapse looks like a 10:1. If the feed says the action was 2:1
    (or says nothing), the price move was NOT that action."""
    from split_detect import reconcile_with_corporate_action
    assert reconcile_with_corporate_action(10.0, 2.0) is None
    assert reconcile_with_corporate_action(10.0, None) is None


def test_corporate_action_alone_is_authoritative():
    """A split that happened while the instance was stopped leaves no bar
    ratio to infer from — the feed still resolves it."""
    from split_detect import reconcile_with_corporate_action
    assert reconcile_with_corporate_action(None, 8.0) == 8.0


def test_corporate_action_handles_reverse_and_bad_input():
    from split_detect import reconcile_with_corporate_action
    assert reconcile_with_corporate_action(0.1, 0.1) == 0.1
    assert reconcile_with_corporate_action(8.0, 1.0) is None      # no-op ratio
    assert reconcile_with_corporate_action(8.0, "x") is None
