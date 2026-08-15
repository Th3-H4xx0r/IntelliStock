"""The selection signal must have variance at the moment it is used.

THE DEFECT, measured across four runs (bt 866880 / 235194 / 559934 / 599773):

  * 717 of 723 buy candidates scored EXACTLY +1.000. Three of the four runs had
    exactly TWO distinct values.
  * `_conviction_allocation_schedule` — whose docstring promises "a stock with
    score 0.90 gets 3x the capital of one with score 0.30" — emitted IDENTICAL
    dollar amounts to every funded name in 154 of 159 events (97%), e.g.
    `funded 4 of 5 by conviction (RIVN@$836, MSFT@$836, XOM@$836, HAPN@$836)`.
  * `_sb_rank_key`'s "unchanged primary" is that same constant, so the buy sort
    collapses through `confidence` and `base_signal` onto `str(sym)` —
    alphabetical.

Three upstream death points:
  :19848  the graph aggregate is clamped to +/-1 while its seed sentiment is an
          INTEGER +/-1, so a single supporting path pins the ceiling.
  :21170  a name absent from the graph (~94% of samples) falls back to the
          TERNARY LLM label.
  :22213  every momentum score in [0.40, 1.50] maps to exactly 1.700.

`raw_net_natural` (:22219) carries the undamaged continuous signal and is read
by NOTHING — the comment at :22207 claiming eta.G consumes it is wrong; eta.G
reads `raw_net_score`.

WHY THIS OUTRANKS EVERY OTHER FINDING: while the score is a constant, no A/B on
any downstream lever measures anything, because a 97%-equal-weight allocator
returns the same book regardless of what is tuned. It also explains the window-d
loss directly — the book could not prefer INTC (+156%) over XOM (-12%) because
it scored them identically.

Default OFF (`selection_uses_natural_score_enabled`).
"""
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from strategies.graph_nexus_analysis import (  # noqa: E402
    _conviction_allocation_schedule as alloc,
)

_GNA = os.path.join(os.path.dirname(__file__), "..", "strategies",
                    "graph_nexus_analysis.py")
_SRC = open(_GNA, encoding="utf-8").read()

ON = {"selection_uses_natural_score_enabled": True}

# The measured shape: every candidate pinned at the same saturated score while
# their true momentum differs by 4x.
SATURATED = [
    {"raw_net_score": 1.700, "raw_net_natural": 1.94, "n_paths": 1},
    {"raw_net_score": 1.700, "raw_net_natural": 0.62, "n_paths": 1},
    {"raw_net_score": 1.700, "raw_net_natural": 0.45, "n_paths": 1},
]


def test_today_the_allocator_cannot_tell_them_apart():
    """Pin the defect. This is the 97%-equal-weight behaviour, reproduced."""
    w = alloc(SATURATED, {})
    assert max(w) - min(w) == pytest.approx(0.0, abs=1e-9), w
    assert all(x == pytest.approx(1 / 3, abs=1e-9) for x in w)


def test_the_natural_signal_restores_the_documented_behaviour():
    """The docstring promises 3x for 3x. Deliver it."""
    w = alloc(SATURATED, ON)
    assert w[0] > w[1] > w[2], w
    assert max(w) / min(w) > 4.0, (
        f"the strongest natural signal (1.94) must dominate the weakest (0.45); "
        f"got {max(w) / min(w):.2f}x")
    assert sum(w) == pytest.approx(1.0, abs=1e-9)


def test_absent_natural_is_byte_identical():
    """A lane that never carried the natural score must be untouched, not
    zeroed — that would silently defund an entire signal source."""
    no_nat = [{"raw_net_score": 1.7, "n_paths": 1},
              {"raw_net_score": 0.9, "n_paths": 1}]
    assert alloc(no_nat, {}) == alloc(no_nat, ON)


def test_a_zero_or_negative_natural_falls_back_rather_than_defunding():
    for bad in (0.0, -1.0, None, "", "abc"):
        item = [{"raw_net_score": 1.7, "raw_net_natural": bad, "n_paths": 1},
                {"raw_net_score": 0.9, "n_paths": 1}]
        assert alloc(item, ON) == alloc(item, {}), bad


def test_nan_natural_cannot_corrupt_the_weights():
    """NaN propagates silently through max/sum; the existing guard must survive
    the new path."""
    item = [{"raw_net_score": 1.7, "raw_net_natural": float("nan"), "n_paths": 1},
            {"raw_net_score": 0.9, "n_paths": 1}]
    w = alloc(item, ON)
    assert all(x == x for x in w), w
    assert sum(w) == pytest.approx(1.0, abs=1e-9)


def test_flag_off_is_byte_identical_over_a_grid():
    import itertools
    for raw, nat, paths in itertools.product(
            (0.0, 0.9, 1.7, 1.8), (0.0, 0.45, 1.94), (0, 1, 5)):
        ranked = [{"raw_net_score": raw, "raw_net_natural": nat, "n_paths": paths},
                  {"raw_net_score": 1.7, "raw_net_natural": 0.6, "n_paths": 1}]
        assert alloc(ranked, {}) == alloc(
            ranked, {"selection_uses_natural_score_enabled": False}), (raw, nat, paths)


def test_the_rank_key_also_reads_the_natural_score_and_is_default_off():
    """Sizing alone is not enough — the sort decides WHICH names get bought, and
    today it collapses to alphabetical."""
    assert '_sb_use_natural = bool(config.get(' in _SRC
    assert '"selection_uses_natural_score_enabled", False))' in _SRC
    assert "_primary = _n(\"raw_net_score\")" in _SRC
    assert "_nat = _n(\"raw_net_natural\")" in _SRC
    assert "-abs(_primary)," in _SRC, \
        "the sort's primary key must be the resolved score, not the constant"


def test_the_allocator_site_is_guarded_and_documented():
    i = _SRC.index("_use_natural = False")
    window = _SRC[max(0, i - 2000):i]
    assert "717 of 723" in window, (
        "the measurement that justifies this change must travel with it")
    assert re.search(
        r'\(config or \{\}\)\.get\(\s*\n?\s*"selection_uses_natural_score_enabled", False\)',
        _SRC), "must be behind its own default-OFF flag"
