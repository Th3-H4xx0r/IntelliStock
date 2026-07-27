from __future__ import annotations

from pathlib import Path
import sys
import types

import pytest
import yaml
from pydantic import ValidationError


class _Store:
    def insert(self, _row):
        return None

    def update(self, _cid, _patch):
        return None

    def get(self, _cid):
        return None

    def list_open(self):
        return []


def test_factory_rejects_deprecated_stock_brokerage(monkeypatch):
    """Restoring the removed factory branch must make this return an adapter."""
    from broker_adapters.errors import BrokerError
    from broker_adapters.factory import build_adapter

    fake_module = types.ModuleType("broker_adapters.robinhood")
    fake_module.RobinhoodAdapter = lambda **_kwargs: object()
    monkeypatch.setitem(
        sys.modules,
        "broker_adapters.robinhood",
        fake_module,
    )
    with pytest.raises(BrokerError, match="unknown broker_type"):
        build_adapter(
            broker_type="robinhood",
            api_key="unused",
            api_secret="unused",
            paper=False,
            instance_id="alpaca-main",
            wal_store=_Store(),
        )


def test_api_schema_accepts_alpaca_but_rejects_deprecated_brokerage():
    """Adding the deprecated brokerage to the API discriminator must pass it."""
    from api.main import LinkBrokerageBody

    assert LinkBrokerageBody(
        brokerage_type="alpaca",
        account_name="Primary",
        key="key",
        secret="secret",
        paper=True,
    ).brokerage_type == "alpaca"
    with pytest.raises(ValidationError):
        LinkBrokerageBody(
            brokerage_type="robinhood",
            account_name="Deprecated",
        )


def test_live_gate_rejects_unknown_stock_brokerage_fail_closed():
    """Reintroducing a funded-mode classification must stop raising here."""
    from live_readiness import LiveReadinessError, brokerage_requires_live_gate

    with pytest.raises(LiveReadinessError, match="unsupported"):
        brokerage_requires_live_gate(
            {"kind": "equities", "brokerage_id": "legacy"},
            {"id": "legacy", "brokerage_type": "robinhood"},
        )


def test_compose_has_no_deprecated_credential_refresh_daemon():
    """Restoring the token daemon must make the inactive release include it."""
    root = Path(__file__).resolve().parents[2]
    compose = yaml.safe_load((root / "docker-compose.yml").read_text())

    assert "credential-service" not in compose["services"]


def test_symbol_history_uses_an_injected_market_data_provider():
    """Hard-wiring a removed brokerage endpoint must reject this provider."""
    from api.main import fetch_symbol_historicals

    calls = []

    def provider(symbols, range_name):
        calls.append((symbols, range_name))
        return {
            "AAPL": [
                {"ts": "2026-07-27T14:30:00+00:00", "value": 200.0},
            ]
        }

    result = fetch_symbol_historicals(
        "AAPL",
        "1D",
        history_provider=provider,
    )

    assert calls == [(["AAPL"], "1D")]
    assert result == {
        "range": "1D",
        "results": {
            "AAPL": [
                {"ts": "2026-07-27T14:30:00+00:00", "value": 200.0},
            ]
        },
    }
