"""The Robinhood market-DATA fallback must be gated so a NON-Robinhood instance
(e.g. an Alpaca-trading instance) NEVER calls Robinhood's public historicals from
the server IP.

Background: even after the operator stopped TRADING on Robinhood, the live data
path kept hitting Robinhood's /quotes/historicals/ for any symbol Alpaca's free
IEX feed couldn't serve (broker.py _robinhood_bars_fallback + the
allow_non_alpaca_fallback price path) — which re-flagged the Robinhood account.
Gate: only a Robinhood-TRADING instance may use the RH data fallback, and even
then it can be killed via env ROBINHOOD_DATA_FALLBACK or the
robinhood_data_fallback_enabled config flag. yfinance fallback is unaffected.
"""
from __future__ import annotations


def test_non_robinhood_broker_blocks_fallback():
    from robinhood_data_policy import robinhood_data_fallback_allowed
    assert robinhood_data_fallback_allowed("alpaca") is False
    assert robinhood_data_fallback_allowed("") is False
    assert robinhood_data_fallback_allowed(None) is False


def test_robinhood_broker_allows_by_default():
    from robinhood_data_policy import robinhood_data_fallback_allowed
    assert robinhood_data_fallback_allowed("robinhood") is True
    assert robinhood_data_fallback_allowed(" Robinhood ") is True  # case/space-insensitive


def test_config_flag_disables_even_on_robinhood():
    from robinhood_data_policy import robinhood_data_fallback_allowed
    assert robinhood_data_fallback_allowed("robinhood", {"robinhood_data_fallback_enabled": False}) is False
    assert robinhood_data_fallback_allowed("robinhood", {"robinhood_data_fallback_enabled": True}) is True


def test_env_kill_switch_disables(monkeypatch):
    from robinhood_data_policy import robinhood_data_fallback_allowed
    for v in ("false", "0", "no", "off"):
        monkeypatch.setenv("ROBINHOOD_DATA_FALLBACK", v)
        assert robinhood_data_fallback_allowed("robinhood") is False
