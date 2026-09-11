"""A ticker is a ticker, not an argument to broker.py.

Symbols entered through POST /instances/{id}/stocks and the instance's stocks
list are stored verbatim and later spliced into the child process argv at
backend/instance.py:588-594:

    ['python', 'broker.py', instance_id, 'live', 'NULL', 'NULL', incr, *symbols]

Nothing validated them, so a "symbol" of "--INITIAL-CASH" (or anything else
broker.py's positional parsing would read) travelled from an API body into the
command line of the process that trades real money.
"""
from __future__ import annotations

import os
import sys

import pytest

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)


GOOD = [
    "AAPL", "TQQQ", "BRK.B", "RDS-A", "F", "GOOGL", "SNDK", "X",
    # The same stocks list carries a crypto instance's fixed universe.
    "BTC/USD", "ETH/USD", "MATIC/USDT",
]
BAD = [
    "--INITIAL-CASH",
    "-INITIAL",
    ".HIDDEN",
    "/etc/passwd",
    "AAPL;rm -rf /",
    "AAPL AAPL",
    "TOOLONGSYMBOL",
    "",
    "   ",
    "AAPL\n--flag",
    "$(whoami)",
    "1AAPL",
    "/USD",
    "A/../../ET",
    "BTC/USD/EUR",
    "BTC/",
]


# --- the shared validator -------------------------------------------------


@pytest.mark.parametrize("symbol", GOOD)
def test_good_symbols_pass_and_come_back_upper_case(symbol):
    from interactive_utils import validate_ticker

    assert validate_ticker(symbol.lower()) == symbol.upper()


@pytest.mark.parametrize("symbol", BAD)
def test_bad_symbols_are_refused(symbol):
    from interactive_utils import validate_ticker

    with pytest.raises(ValueError):
        validate_ticker(symbol)


# --- the API bodies -------------------------------------------------------


def test_add_stock_body_rejects_a_flag():
    from api.main import AddStockBody
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        AddStockBody(symbol="--INITIAL-CASH")


def test_add_stock_body_upper_cases_before_validating():
    from api.main import AddStockBody

    assert AddStockBody(symbol="  aapl ").symbol == "AAPL"


def test_create_instance_body_rejects_a_flag_in_stocks():
    from api.main import CreateInstanceBody
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        CreateInstanceBody(id="eb", stocks=["AAPL", "--INITIAL-CASH"])


def test_create_instance_body_upper_cases_stocks():
    from api.main import CreateInstanceBody

    assert CreateInstanceBody(id="eb", stocks=["aapl", "brk.b"]).stocks == ["AAPL", "BRK.B"]


def test_create_instance_body_accepts_no_stocks_at_all():
    from api.main import CreateInstanceBody

    assert CreateInstanceBody(id="eb").stocks is None


def test_edit_instance_body_rejects_a_flag_in_stocks():
    from api.main import EditInstanceBody
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        EditInstanceBody(stocks=["--INITIAL-CASH"])


# --- the actions re-validate ---------------------------------------------


def test_action_add_stock_rejects_a_flag(monkeypatch):
    import interactive_utils as iu

    monkeypatch.setattr(iu, "ensure_instances_table", lambda conn: None)
    monkeypatch.setattr(iu, "_resolve_instance_doc",
                        lambda conn, iid: {"id": iid, "stocks": []})
    monkeypatch.setattr(iu.store, "update", lambda *a, **k: pytest.fail(
        "the flag reached the write"))

    with pytest.raises(ValueError):
        iu.action_add_stock(None, "strategy-eb", "--INITIAL-CASH")


def test_action_add_stock_still_adds_a_real_ticker(monkeypatch):
    import interactive_utils as iu

    written = {}
    monkeypatch.setattr(iu, "ensure_instances_table", lambda conn: None)
    monkeypatch.setattr(iu, "ensure_live_prices_stocks_table", lambda conn: None)
    monkeypatch.setattr(iu, "_resolve_instance_doc",
                        lambda conn, iid: {"id": iid, "stocks": []})
    monkeypatch.setattr(iu.store, "update",
                        lambda table, key, patch: written.update(patch))
    monkeypatch.setattr(iu.store, "get", lambda table, key: {"id": key})
    out = iu.action_add_stock(None, "strategy-eb", "tqqq")
    assert out["symbol"] == "TQQQ"
    assert written["stocks"] == ["TQQQ"]


def test_action_create_instance_rejects_a_flag_in_stocks(monkeypatch):
    import interactive_utils as iu

    monkeypatch.setattr(iu, "ensure_instances_table", lambda conn: None)
    monkeypatch.setattr(iu.store, "get", lambda *a, **k: None)
    monkeypatch.setattr(iu.store, "insert", lambda *a, **k: pytest.fail(
        "the flag reached the write"))

    with pytest.raises(ValueError):
        iu.action_create_instance(None, "strategy-eb", stocks=["--INITIAL-CASH"])


def test_action_edit_instance_rejects_a_flag_in_stocks(monkeypatch):
    import interactive_utils as iu

    monkeypatch.setattr(iu, "ensure_instances_table", lambda conn: None)
    monkeypatch.setattr(iu, "_resolve_instance_doc",
                        lambda conn, iid: {"id": iid})
    monkeypatch.setattr(iu.store, "update", lambda *a, **k: pytest.fail(
        "the flag reached the write"))

    with pytest.raises(ValueError):
        iu.action_edit_instance(None, "strategy-eb", stocks=["--INITIAL-CASH"])
