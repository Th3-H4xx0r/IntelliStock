import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from strategies.graph_nexus_analysis import _outcome_entry_price


class _Emu:
    """Matches the real portfolio-emulator price surface found in Step 1:
    PortfolioEmulator / broker adapters expose a `_last_prices` dict attribute
    (backend/portfolio_emulator.py:22), NOT a get_last_price() method."""

    def __init__(self, px):
        self._last_prices = dict(px)


def test_prices_dict_wins():
    assert _outcome_entry_price("CRWV", {}, {"CRWV": 104.55}, _Emu({})) == 104.55


def test_payload_price_fallback():
    # live buy/sell candidates are NOT in `prices` (root cause of 0 outcomes ever)
    px = _outcome_entry_price("GOOGL", {"current_price": 182.4}, {}, _Emu({}))
    assert px == 182.4


def test_payload_nested_quality_metadata_fallback():
    # the real enriched_scores payload carries current_price nested under
    # quality_metadata (graph_nexus_analysis.py:18754 / :18946)
    px = _outcome_entry_price(
        "NVDA", {"quality_metadata": {"current_price": 120.7}}, {}, _Emu({})
    )
    assert px == 120.7


def test_payload_price_key_fallback():
    px = _outcome_entry_price("AAPL", {"price": 211.3}, {}, _Emu({}))
    assert px == 211.3


def test_emulator_fallback():
    px = _outcome_entry_price("DAL", {}, {}, _Emu({"DAL": 55.1}))
    assert px == 55.1


def test_no_price_returns_none():
    assert _outcome_entry_price("XXXX", {}, {}, _Emu({})) is None


def test_zero_and_negative_rejected():
    assert _outcome_entry_price("Y", {"current_price": 0}, {}, _Emu({})) is None
    assert _outcome_entry_price("Z", {"current_price": -3.2}, {}, _Emu({})) is None


def test_prices_precedence_over_payload_and_emu():
    px = _outcome_entry_price(
        "MSFT", {"current_price": 200.0}, {"MSFT": 190.0}, _Emu({"MSFT": 180.0})
    )
    assert px == 190.0


def test_none_emulator_is_safe():
    assert _outcome_entry_price("DAL", {"current_price": 55.1}, {}, None) == 55.1
    assert _outcome_entry_price("DAL", {}, {}, None) is None
