"""Anti-whipsaw hysteresis: strategies must not flip a position on a single-bar
cross of the moving average (which churned fees and lost to buy-and-hold).
core.above_ma_state keeps the current state through a neutral dead-band."""

from strategies.crypto import core


def _bars(closes):
    return [{"c": c} for c in closes]


def test_hysteresis_band_floor_and_default():
    # floored at the round-trip taker cost (0.005)
    assert core.hysteresis_band({"hysteresis_pct": 0.0}) == 0.005
    # explicit larger value respected
    assert core.hysteresis_band({"hysteresis_pct": 0.03}) == 0.03
    # default 1.5%
    assert core.hysteresis_band(None) == 0.015


def test_above_ma_state_decisive_moves():
    up = _bars([100.0] * 20 + [110.0])     # last close well above the ~100.5 SMA
    dn = _bars([100.0] * 20 + [90.0])      # well below
    assert core.above_ma_state(up, 20, is_held=False, band=0.02) is True
    assert core.above_ma_state(dn, 20, is_held=True, band=0.02) is False


def test_above_ma_state_neutral_band_keeps_state():
    # 0.5% above the SMA with a 2% band → neutral → keep current state
    flat = _bars([100.0] * 20 + [100.5])
    assert core.above_ma_state(flat, 20, is_held=True, band=0.02) is True    # held → stay long
    assert core.above_ma_state(flat, 20, is_held=False, band=0.02) is False  # flat → stay flat


def test_above_ma_state_too_few_bars():
    assert core.above_ma_state(_bars([1.0]), 20, is_held=False, band=0.02) is None


def test_hysteresis_kills_whipsaw_flip_count():
    """A choppy series oscillating +/-0.4% around a flat MA should produce ZERO
    state flips with a 1% band (all crosses are inside the dead-band), vs a naive
    close>MA rule which would flip on every bar."""
    import numpy as np
    base = [100.0] * 30
    chop = [100.0 + (0.4 if i % 2 == 0 else -0.4) for i in range(40)]  # ±0.4% wiggle
    closes = base + chop
    band = 0.01
    held = False
    flips = 0
    naive_flips = 0
    prev_naive = None
    for k in range(31, len(closes) + 1):
        window = _bars(closes[:k])
        st = core.above_ma_state(window, 20, held, band)
        if st is not None and st != held:
            flips += 1
            held = st
        # naive close>MA baseline
        ma = float(np.mean(closes[k - 20:k]))
        naive = closes[k - 1] > ma
        if prev_naive is not None and naive != prev_naive:
            naive_flips += 1
        prev_naive = naive
    assert flips == 0, f"hysteresis should not whipsaw, got {flips} flips"
    assert naive_flips > 5, "naive rule should whipsaw a lot on this series"
