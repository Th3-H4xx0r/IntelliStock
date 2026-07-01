from kalshi.instance_config import inplay_caps_from_config, normalize_config


def test_normalize_config_persists_live_monitoring_fields():
    c = normalize_config({
        "bankroll_dollars": 50, "live_monitoring": False, "live_poll_seconds": 5,
        "inplay_exposure_frac": 0.4, "max_adds_per_match": 5, "no_add_after_min": 75,
        "stop_loss_frac": 0.6,
    }, live_enabled=False)
    assert c["live_monitoring"] is False
    assert c["live_poll_seconds"] == 10           # clamped to >= 10
    assert c["inplay_exposure_frac"] == 0.4
    assert c["max_adds_per_match"] == 5
    assert c["no_add_after_min"] == 75
    assert c["stop_loss_frac"] == 0.6


def test_normalize_config_defaults_live_monitoring_off():
    # In-match trading defaults OFF: only the pregame strategy is backtest-validated,
    # so in-play is opt-in. Explicitly enabling it still works (asserted below).
    c = normalize_config({"bankroll_dollars": 100}, live_enabled=False)
    assert c["live_monitoring"] is False
    assert c["live_poll_seconds"] == 30


def test_normalize_config_live_monitoring_opt_in():
    c = normalize_config({"bankroll_dollars": 100, "live_monitoring": True}, live_enabled=False)
    assert c["live_monitoring"] is True


def test_inplay_caps_from_config_maps_fields():
    c = normalize_config({"bankroll_dollars": 50, "max_contracts_per_market": 25,
                          "inplay_exposure_frac": 0.3}, live_enabled=False)
    caps = inplay_caps_from_config(c)
    assert caps.bankroll_cents == 5000
    assert caps.max_contracts_per_market == 25
    assert caps.inplay_exposure_frac == 0.3
    assert caps.stop_loss_frac == 0.35   # default (thesis-break is now the primary exit)
