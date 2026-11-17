"""Pure tests for Strategy Pairs: signals, the z-score machine, the book."""
import math
import os
import sys

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from strategy_pairs import (  # noqa: E402
    DEFAULTS, REEL_PAIRS, aligned_closes, pair_signal, pairs_mode, pairs_of,
    pairs_targets, reversal_signal, spread_z, strategy_pairs_universe,
    zscore_signal,
)


def cfg(**overrides):
    value = dict(DEFAULTS)
    value.update(overrides)
    return value


def test_the_default_book_is_the_reels_twelve_pairs():
    assert len(pairs_of(DEFAULTS)) == 12
    assert pairs_of(DEFAULTS)[0] == ("KO", "PEP")
    assert len(strategy_pairs_universe(DEFAULTS)) == 24
    assert DEFAULTS["pairs"] is REEL_PAIRS


def test_malformed_self_and_duplicate_pairs_are_dropped():
    raw = [["ko", "pep"], ["PEP", "KO"], ["KO", "KO"], ["X"], "KO/PEP",
           ["", "A"], ["hd", " low "]]
    assert pairs_of({"pairs": raw}) == [("KO", "PEP"), ("HD", "LOW")]
    assert pairs_of({"pairs": "KO"}) == []


def test_an_unknown_mode_is_hold():
    assert pairs_mode({"mode": "revresal"}) == "hold"
    assert pairs_mode({"mode": " ZScore "}) == "zscore"


def test_aligned_closes_keeps_only_shared_sessions():
    a = [("d1", 1.0), ("d2", 2.0), ("d3", 3.0)]
    b = [("d1", 10.0), ("d3", 30.0), ("d4", 40.0)]
    assert aligned_closes(a, b) == ([1.0, 3.0], [10.0, 30.0])


def test_reversal_points_at_the_laggard():
    flat = [100.0] * 6
    up = [100.0] * 5 + [105.0]
    assert reversal_signal(flat, up, 5) == 1      # A lagged -> hold A
    assert reversal_signal(up, flat, 5) == -1     # B lagged -> hold B
    assert reversal_signal(up, up, 5) == 0        # tie


def test_reversal_uses_exactly_the_lookback_window():
    # A fell 10% six sessions ago, then both flat: over 5 sessions it is a tie.
    a = [100.0, 90.0, 90.0, 90.0, 90.0, 90.0, 90.0]
    b = [100.0] * 7
    assert reversal_signal(a, b, 5) == 0
    assert reversal_signal(a, b, 6) == 1


def test_reversal_is_flat_on_short_history_or_a_bad_close():
    assert reversal_signal([1.0] * 5, [1.0] * 5, 5) == 0
    bad = [100.0] * 5 + [float("nan")]
    assert reversal_signal(bad, [100.0] * 6, 5) == 0
    assert reversal_signal([0.0] + [100.0] * 5, [100.0] * 6, 5) == 0


def test_spread_z_matches_a_hand_computation():
    a = [100.0, 101.0, 99.0, 100.0, 110.0]
    b = [100.0] * 5
    spread = [math.log(x / 100.0) for x in a]
    mean = sum(spread) / 5
    sd = math.sqrt(sum((x - mean) ** 2 for x in spread) / 4)
    assert abs(spread_z(a, b, 5) - (spread[-1] - mean) / sd) < 1e-9
    assert spread_z(a, b, 6) is None                  # short history
    assert spread_z([100.0] * 5, [100.0] * 5, 5) is None  # zero variance


def test_the_zscore_machine_enters_holds_and_exits():
    assert zscore_signal(2.5, 0, 2.0, 0.0) == -1      # A rich -> hold B
    assert zscore_signal(-2.5, 0, 2.0, 0.0) == 1      # A cheap -> hold A
    assert zscore_signal(1.9, 0, 2.0, 0.0) == 0
    assert zscore_signal(0.5, -1, 2.0, 0.0) == -1     # still open
    assert zscore_signal(-0.1, -1, 2.0, 0.0) == 0     # crossed the mean
    assert zscore_signal(-3.0, -1, 2.0, 0.0) == 0     # exits, no same-day flip
    assert zscore_signal(0.1, 1, 2.0, 0.0) == 0
    assert zscore_signal(None, 1, 2.0, 0.0) == 1      # unmeasurable: keep


def test_pair_signal_by_mode():
    flat = [100.0] * 6
    up = [100.0] * 5 + [105.0]
    assert pair_signal(flat, up, 0, cfg(mode="hold")) == 0
    assert pair_signal(flat, up, 0, cfg(mode="reversal")) == 1
    assert pair_signal(flat, up, 0, cfg(mode="momentum")) == -1


def test_targets_split_tilt_and_sum_to_one():
    c = cfg(pairs=[["KO", "PEP"], ["HD", "LOW"]])
    hold = pairs_targets({}, c)
    assert hold == {"KO": 0.25, "PEP": 0.25, "HD": 0.25, "LOW": 0.25}
    tilted = pairs_targets({("KO", "PEP"): 1, ("HD", "LOW"): -1}, c)
    assert tilted == {"KO": 0.5, "LOW": 0.5}
    half = pairs_targets({("KO", "PEP"): 1}, {**c, "tilt": 0.5})
    assert half["KO"] == 0.375 and half["PEP"] == 0.125
    for book in (hold, tilted, half):
        assert abs(sum(book.values()) - 1.0) < 1e-9


def test_a_symbol_in_two_pairs_gets_both_slots():
    c = cfg(pairs=[["WMT", "TGT"], ["COST", "WMT"]])
    book = pairs_targets({("WMT", "TGT"): 1, ("COST", "WMT"): -1}, c)
    assert book == {"WMT": 1.0}


def test_the_header_config_is_exactly_the_module_defaults():
    import json
    import re
    wrapper = os.path.join(_backend, "strategies", "strategy_pairs.py")
    match = re.search(r"# INTELLISTOCK_SCHEMA: (.*)", open(wrapper).read())
    assert match, "the wrapper lost its INTELLISTOCK_SCHEMA header"
    schema = json.loads(match.group(1))
    assert schema["strategy"] == "strategy_pairs"
    assert schema["execution_scope"] == "run_once"
    assert schema["config"] == json.loads(json.dumps(DEFAULTS))
