from kalshi.instance_config import (
    normalize_config, build_kalshi_instance_doc, risk_caps_from_config, DEFAULT_LEAGUES,
)


def test_normalize_converts_dollars_to_cents_and_defaults():
    c = normalize_config(
        {"bankroll_dollars": 4820.14, "daily_loss_cap_dollars": 400, "edge_threshold": 0.04},
        live_enabled=True,
    )
    assert c["bankroll_cents"] == 482014
    assert c["daily_loss_cap_cents"] == 40000
    assert c["edge_threshold"] == 0.04
    assert c["kelly_fraction"] == 0.25          # default
    assert c["leagues"] == DEFAULT_LEAGUES       # default
    assert c["poll_seconds"] == 60
    assert c["live_enabled"] is True


def test_normalize_poll_floor_and_league_passthrough():
    c = normalize_config({"poll_seconds": 5, "leagues": ["EPL", " "]}, live_enabled=False)
    assert c["poll_seconds"] == 15               # floored
    assert c["leagues"] == ["EPL"]               # blanks dropped
    assert c["live_enabled"] is False


def test_build_doc_shape():
    cfg = normalize_config({}, live_enabled=False)
    doc = build_kalshi_instance_doc("inst-1", brokerage_id="bk-1", name="Demo bot", config=cfg)
    assert doc["kind"] == "kalshi"
    assert doc["brokerage_id"] == "bk-1"
    assert doc["runCommand"] is False
    assert doc["name"] == "Demo bot"
    assert "strategy_id" not in doc and "stocks" not in doc  # no equities fields
    assert doc["kalshi_config"] is cfg


def test_risk_caps_from_config():
    cfg = normalize_config({"bankroll_dollars": 1000, "max_contracts_per_market": 25}, live_enabled=False)
    caps = risk_caps_from_config(cfg)
    assert caps.bankroll_cents == 100000
    assert caps.max_contracts_per_market == 25
    assert caps.edge_threshold == 0.03
