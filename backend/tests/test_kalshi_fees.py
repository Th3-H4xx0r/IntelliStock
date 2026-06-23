from kalshi.fees import resolve_fee_rate, fee_as_prob, fee_cents_for_order, DEFAULT_FEE_RATE


def test_fee_as_prob_peaks_at_midprice():
    # symmetric P(1-P), max at 50c
    assert fee_as_prob(50) > fee_as_prob(20)
    assert abs(fee_as_prob(50) - DEFAULT_FEE_RATE * 0.25) < 1e-12


def test_resolve_fee_rate_honors_zero_promo():
    assert resolve_fee_rate({"fee_waived": True}) == 0.0
    assert resolve_fee_rate({"maker_fee_promo": True}) == 0.0


def test_resolve_fee_rate_explicit_and_default():
    assert resolve_fee_rate({"fee_rate": 0.03}) == 0.03
    assert resolve_fee_rate(None) == DEFAULT_FEE_RATE
    assert resolve_fee_rate({}) == DEFAULT_FEE_RATE


def test_promo_makes_fee_zero_in_edge():
    rate = resolve_fee_rate({"fee_waived": True})
    assert fee_as_prob(48, rate) == 0.0


def test_fee_cents_rounds_up():
    # 0.07 * 100 * 0.5 * 0.5 = 1.75 dollars -> 175 cents
    assert fee_cents_for_order(50, 100) == 175
    # tiny order still rounds up to >=1 cent when nonzero
    assert fee_cents_for_order(50, 1) >= 1
