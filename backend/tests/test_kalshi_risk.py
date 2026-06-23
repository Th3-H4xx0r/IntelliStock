from kalshi.risk import quarter_kelly_fraction, RiskCaps, size_order, check_caps


def test_quarter_kelly():
    f = quarter_kelly_fraction(edge=0.04, fair_prob=0.53)
    assert abs(f - 0.25 * 0.04 / 0.47) < 1e-9


def test_quarter_kelly_non_positive_edge_is_zero():
    assert quarter_kelly_fraction(edge=0.0, fair_prob=0.5) == 0.0
    assert quarter_kelly_fraction(edge=-0.01, fair_prob=0.5) == 0.0


def test_size_order_caps_contracts_per_market():
    caps = RiskCaps(max_contracts_per_market=50, bankroll_cents=100000)
    n = size_order(edge=0.20, fair_prob=0.55, yes_ask_cents=50, caps=caps)
    assert 0 < n <= 50


def test_size_order_zero_when_no_bankroll():
    caps = RiskCaps(max_contracts_per_market=50, bankroll_cents=0)
    assert size_order(edge=0.2, fair_prob=0.5, yes_ask_cents=50, caps=caps) == 0


def test_check_caps_blocks_daily_loss():
    caps = RiskCaps(daily_loss_cap_cents=40000)
    ok, reason = check_caps(
        caps, day_pnl_cents=-40000, open_exposure_frac=0.1,
        league="EPL", league_exposure_frac=0.1,
    )
    assert ok is False and "daily" in reason.lower()


def test_check_caps_blocks_exposure_and_league():
    caps = RiskCaps(max_open_exposure_frac=0.6, per_league_cap_frac=0.25)
    ok, _ = check_caps(caps, day_pnl_cents=0, open_exposure_frac=0.7,
                       league="EPL", league_exposure_frac=0.1)
    assert ok is False
    ok2, r2 = check_caps(caps, day_pnl_cents=0, open_exposure_frac=0.1,
                         league="Serie B", league_exposure_frac=0.4)
    assert ok2 is False and "Serie B" in r2


def test_check_caps_ok_path():
    caps = RiskCaps(daily_loss_cap_cents=40000, max_open_exposure_frac=0.6,
                    per_league_cap_frac=0.25)
    ok, reason = check_caps(caps, day_pnl_cents=-100, open_exposure_frac=0.2,
                            league="EPL", league_exposure_frac=0.1)
    assert ok is True and reason == ""
