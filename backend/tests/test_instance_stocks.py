"""Adding and removing a symbol on an instance.

`action_add_stock` / `action_remove_stock` back POST and DELETE
/instances/{id}/stocks, which the stock chips on the instances list and the
instance detail page are the only UI callers of.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest                                            # noqa: E402

import interactive_utils as iu                           # noqa: E402


@pytest.fixture
def instances(store, monkeypatch):
    monkeypatch.setattr(iu, "store", store)
    monkeypatch.setattr(iu, "ensure_instances_table", lambda conn: None)
    monkeypatch.setattr(iu, "ensure_live_prices_stocks_table", lambda conn: None)
    store.insert("Instances", {
        "id": "strategy-x", "name": "Strategy X", "stocks": ["QQQ", "TQQQ"]})
    return store


def test_add_appends_and_registers_a_price_row(instances):
    out = iu.action_add_stock(None, "strategy-x", " spy ")
    assert out == {"added": True, "symbol": "SPY", "stocks_count": 3}
    assert instances.get("Instances", "strategy-x")["stocks"] == ["QQQ", "TQQQ", "SPY"]
    assert instances.get("LivePricesStocks", "SPY") is not None


def test_add_of_a_held_symbol_changes_nothing(instances):
    out = iu.action_add_stock(None, "strategy-x", "qqq")
    assert out["added"] is False
    assert instances.get("Instances", "strategy-x")["stocks"] == ["QQQ", "TQQQ"]


def test_remove_drops_only_that_symbol(instances):
    out = iu.action_remove_stock(None, "strategy-x", "qqq")
    assert out == {"removed": True, "symbol": "QQQ", "stocks_count": 1}
    assert instances.get("Instances", "strategy-x")["stocks"] == ["TQQQ"]


def test_remove_of_an_absent_symbol_changes_nothing(instances):
    out = iu.action_remove_stock(None, "strategy-x", "SPY")
    assert out["removed"] is False
    assert instances.get("Instances", "strategy-x")["stocks"] == ["QQQ", "TQQQ"]


def test_unknown_instance_raises(instances):
    with pytest.raises(ValueError):
        iu.action_add_stock(None, "nope", "SPY")
    with pytest.raises(ValueError):
        iu.action_remove_stock(None, "nope", "SPY")


def test_empty_symbol_raises(instances):
    with pytest.raises(ValueError):
        iu.action_add_stock(None, "strategy-x", "")
    with pytest.raises(ValueError):
        iu.action_remove_stock(None, "strategy-x", "")
