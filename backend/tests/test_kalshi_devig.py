from kalshi.devig import proportional_devig, power_devig, shin_devig


def _implied(odds):
    return [1.0 / x for x in odds]


def test_proportional_normalizes_to_one():
    p = proportional_devig(_implied([2.0, 3.5, 4.0]))
    assert abs(sum(p) - 1.0) < 1e-9
    assert p[0] > p[1] > p[2]


def test_power_sums_to_one():
    p = power_devig(_implied([2.0, 3.5, 4.0]))
    assert abs(sum(p) - 1.0) < 1e-9


def test_power_shifts_vig_to_longshots():
    raw = _implied([1.5, 4.5, 7.0])  # heavy favorite + longshots
    p = power_devig(raw)
    prop = proportional_devig(raw)
    assert abs(sum(p) - 1.0) < 1e-9
    # power removes proportionally MORE from the longshot than proportional
    assert p[-1] < prop[-1]
    # favorite keeps a higher fair prob under power
    assert p[0] > prop[0]


def test_shin_sums_to_one_and_bounded():
    p = shin_devig(_implied([2.1, 3.3, 3.6]))
    assert abs(sum(p) - 1.0) < 1e-9
    assert all(0.0 < x < 1.0 for x in p)


def test_methods_agree_roughly_on_low_vig_book():
    # Pinnacle-like ~2% vig: power and shin should be close.
    raw = _implied([2.05, 3.40, 3.70])
    pw = power_devig(raw)
    sh = shin_devig(raw)
    assert max(abs(a - b) for a, b in zip(pw, sh)) < 0.03
