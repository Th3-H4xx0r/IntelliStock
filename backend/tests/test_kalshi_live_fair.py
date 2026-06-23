from kalshi.live.live_fair import live_fair


def test_kickoff_blend_uses_full_model_weight():
    # elapsed 0 -> model weight = (1 - w_market). w_market 0.7 -> 0.7*mkt + 0.3*model.
    f = live_fair(0.50, 1.00, 0, 0.0, w_market=0.7)
    assert abs(f - (0.7 * 0.50 + 0.3 * 1.00)) < 1e-9


def test_full_time_is_market_plus_tilt():
    f = live_fair(0.40, 0.90, 115, 0.03, w_market=0.7, regulation_min=115)
    assert abs(f - (0.40 + 0.03)) < 1e-9   # model weight decayed to 0


def test_tilt_is_clamped():
    base = live_fair(0.50, 0.50, 115, 0.0)
    assert abs(live_fair(0.50, 0.50, 115, 0.99, tilt_cap=0.05) - (base + 0.05)) < 1e-9
    assert abs(live_fair(0.50, 0.50, 115, -0.99, tilt_cap=0.05) - (base - 0.05)) < 1e-9


def test_none_prematch_uses_market_and_clamps():
    assert live_fair(0.62, None, 30) == 0.62      # model == market -> equals market
    assert live_fair(1.5, 1.5, 0) == 1.0          # clamps to [0,1]
    assert live_fair(-0.2, -0.2, 0) == 0.0
