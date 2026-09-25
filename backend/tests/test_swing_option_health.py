"""Plan B G7 review I-1 (A-live ledger): a public read of the option book's
health, so the wheel lane stops reading the adapter's private attributes.

``option_positions_health() -> {"complete": bool, "stale_since": ...}``.
``complete`` is False until one refresh has read every option row;
``stale_since`` is the positions-staleness stamp the adapter sets when a
positions refresh fails (None when fresh). The base-class default REFUSES,
like every other option method of the contract: an adapter that cannot
trade options must not be read as holding a complete, empty option book.
"""
import pytest

from broker_adapters.base import BrokerAdapter
from broker_adapters.binanceus import BinanceUSAdapter
from swing_alpaca_fakes import (
    FakeOptionsTradingClient,
    contract_row,
    make_adapter,
    option_position,
)

OCC = "APH261002P00130000"


def _healthy():
    client = FakeOptionsTradingClient(
        positions=[option_position()], contracts_by_symbol={OCC: contract_row()})
    return client, make_adapter(client)


def test_a_complete_fresh_book_reports_complete_and_not_stale():
    _client, adapter = _healthy()
    assert adapter._option_positions[OCC].qty == -1
    assert adapter.option_positions_health() == {"complete": True,
                                                 "stale_since": None}


def test_a_positions_outage_at_startup_is_incomplete():
    client = FakeOptionsTradingClient(positions=[option_position()])

    def _down():
        raise ConnectionError("positions endpoint down")

    client.get_all_positions = _down
    adapter = make_adapter(client, clean_room=True)
    health = adapter.option_positions_health()
    assert health["complete"] is False


def test_a_failed_refresh_reports_when_the_book_went_stale():
    client, adapter = _healthy()

    def _down():
        raise ConnectionError("positions endpoint down")

    client.get_all_positions = _down
    adapter.refresh_positions()
    health = adapter.option_positions_health()
    assert health["stale_since"] is not None
    assert health["stale_since"] == adapter._positions_stale_since
    client.get_all_positions = lambda: [option_position()]
    adapter.refresh_positions()
    assert adapter.option_positions_health() == {"complete": True,
                                                 "stale_since": None}


def test_an_unreadable_option_row_is_incomplete():
    client = FakeOptionsTradingClient(
        positions=[option_position(qty="garbage")],
        contracts_by_symbol={OCC: contract_row()})
    adapter = make_adapter(client)
    assert adapter.option_positions_health()["complete"] is False


def test_the_answer_is_a_copy_the_caller_cannot_use_to_write_back():
    _client, adapter = _healthy()
    health = adapter.option_positions_health()
    health["complete"] = False
    assert adapter._option_positions_complete is True


def test_the_contract_default_refuses_and_stays_non_abstract():
    assert "option_positions_health" not in BrokerAdapter.__abstractmethods__
    assert BinanceUSAdapter.option_positions_health is \
        BrokerAdapter.option_positions_health
    with pytest.raises(NotImplementedError, match="option_positions_health"):
        BrokerAdapter.option_positions_health(object())
