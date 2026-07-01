"""Tests for BacktestConfig + config_from_body — Task 5 of the Kalshi backtest
replay engine. Reuses instance_config.risk_caps_from_config so the backtest's
risk knobs are built the SAME way as the live instance."""
from kalshi.backtest import BacktestConfig, config_from_body
from kalshi.risk import RiskCaps


def _body(**overrides):
    body = {
        "leagues": ["EPL"],
        "start_date": "2025-01-01",
        "end_date": "2025-06-01",
        "bankroll_dollars": 1000,
        "edge_threshold": 0.05,
        "no_sharp_edge_threshold": 0.12,
        "kelly_fraction": 0.2,
        "order_size_min_dollars": 3,
        "order_size_max_dollars": 9,
        "max_open_exposure_frac": 0.3,
        "per_bet_cap_frac": 0.08,
        "min_price_cents": 20,
        "max_price_cents": 85,
        "draw_min_edge": 0.15,
        "sharp_weight": 0.8,
        "devig_method": "shin",
    }
    body.update(overrides)
    return body


def test_config_from_body_maps_live_knobs():
    cfg = config_from_body(_body())
    assert isinstance(cfg, BacktestConfig)
    assert cfg.leagues == ["EPL"]
    assert cfg.start_date == "2025-01-01"
    assert cfg.end_date == "2025-06-01"
    assert cfg.bankroll_cents == 100000
    assert cfg.sharp_weight == 0.8
    assert cfg.devig_method == "shin"
    assert isinstance(cfg.caps, RiskCaps)
    assert cfg.caps.edge_threshold == 0.05
    assert cfg.caps.no_sharp_edge_threshold == 0.12
    assert cfg.caps.kelly_fraction == 0.2
    assert cfg.caps.order_size_min_cents == 300
    assert cfg.caps.order_size_max_cents == 900
    assert cfg.caps.max_open_exposure_frac == 0.3
    assert cfg.caps.per_bet_cap_frac == 0.08
    assert cfg.caps.min_price_cents == 20
    assert cfg.caps.max_price_cents == 85
    assert cfg.caps.draw_min_edge == 0.15
    assert cfg.caps.bankroll_cents == 100000


def test_config_from_body_defaults_when_missing():
    cfg = config_from_body({})
    assert cfg.leagues == []
    assert cfg.bankroll_cents == 0
    assert cfg.sharp_weight == 0.85          # instance_config default
    assert cfg.devig_method == "power"       # instance_config default
    assert cfg.decision_offsets_sec == (-3 * 3600,)
    assert cfg.fee_rate == 0.07              # fees.DEFAULT_FEE_RATE
    # caps still built via risk_caps_from_config's own defaults
    assert cfg.caps.edge_threshold == 0.04
    assert cfg.caps.kelly_fraction == 0.125


def test_config_from_body_honors_explicit_fee_rate_and_offsets():
    cfg = config_from_body(_body(fee_rate=0.0, decision_offsets_sec=[-7200, -3600]))
    assert cfg.fee_rate == 0.0
    assert cfg.decision_offsets_sec == (-7200, -3600)
