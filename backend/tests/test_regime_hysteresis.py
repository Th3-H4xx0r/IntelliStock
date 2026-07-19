"""Asymmetric regime hysteresis (2026-07-19 regime-safety spec, Phase 2).

Downgrades (toward bear/crash) apply immediately — fast to de-risk.
Upgrades (toward bull) need K consecutive raw signals — slow to re-risk.
Cold start seeds directly from the first raw signal (history-derived).
"""
import os
import sys

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

import strategies.graph_nexus_analysis as g


def _run(seq, cfg=None):
    cache = {}
    return [g._apply_regime_hysteresis(cache, raw, cfg or {}) for raw in seq]


def test_cold_start_seeds_directly():
    assert _run(["chop"]) == ["chop"]
    assert _run(["bear"]) == ["bear"]
    assert _run(["bull"]) == ["bull"]


def test_downgrade_applies_immediately():
    assert _run(["bull", "chop"]) == ["bull", "chop"]
    assert _run(["bull", "bear"]) == ["bull", "bear"]
    assert _run(["chop", "crash"]) == ["chop", "crash"]


def test_upgrade_needs_k_consecutive():
    # K=3 default: two bull raws are not enough, the third flips.
    assert _run(["chop", "bull", "bull", "bull"]) == ["chop", "chop", "chop", "bull"]
    assert _run(["bear", "chop", "chop", "chop"]) == ["bear", "bear", "bear", "chop"]


def test_upgrade_counter_resets_on_interruption():
    assert _run(["chop", "bull", "bull", "chop", "bull", "bull", "bull"]) == \
        ["chop", "chop", "chop", "chop", "chop", "chop", "bull"]


def test_flapping_suppressed():
    # The June-2026 whipsaw sequence: raw bull/chop alternation stays chop
    # after the first downgrade instead of toggling lever profiles.
    assert _run(["bull", "chop", "bull", "chop", "bull"]) == \
        ["bull", "chop", "chop", "chop", "chop"]


def test_k_configurable():
    cfg = {"regime_upgrade_confirm_bars": 1}
    assert _run(["chop", "bull"], cfg) == ["chop", "bull"]


def test_unknown_raw_passthrough():
    # Defensive: an unexpected label neither crashes nor corrupts state.
    out = _run(["chop", "weird", "chop"])
    assert out[0] == "chop" and out[2] == "chop"
