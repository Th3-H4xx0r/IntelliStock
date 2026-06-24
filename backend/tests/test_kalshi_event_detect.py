from kalshi.live.event_detect import PriceMove, detect_move


def test_detect_move_up_down_flat():
    up = detect_move([50, 51, 64], threshold_cents=8)
    assert up.moved and up.direction == "up" and up.delta_cents == 14

    down = detect_move([60, 59, 48], threshold_cents=8)
    assert down.moved and down.direction == "down" and down.delta_cents == -12

    flat = detect_move([50, 51, 53], threshold_cents=8)
    assert not flat.moved and flat.direction == "flat"


def test_detect_move_needs_two_points():
    assert detect_move([], threshold_cents=8) == PriceMove(False, 0.0, "flat")
    assert detect_move([50], threshold_cents=8) == PriceMove(False, 0.0, "flat")


def test_detect_move_lookback_window_and_threshold():
    # Reference is `lookback` points back; a gradual drift within window still trips.
    hist = [40, 44, 48, 52]
    assert detect_move(hist, threshold_cents=10, lookback=3).moved          # 52 vs 40 = 12
    assert not detect_move(hist, threshold_cents=10, lookback=1).moved      # 52 vs 48 = 4
    assert detect_move([50, None, 62], threshold_cents=8).moved             # None filtered
