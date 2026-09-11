"""Strategy EB must get bars and prices for legs the watchlist never lists."""
import ast
import os
import sys

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

_BROKER = os.path.join(_BACKEND, "broker.py")


def _extract(*names):
    """AST-extract broker functions into a stub namespace. broker.py argparses
    at module scope and SystemExits under pytest, so it cannot be imported."""
    tree = ast.parse(open(_BROKER).read())
    # `_truthy` and `_merged_strategy_settings` come along because the EB
    # config readers call them, and their blanket `except Exception` would turn
    # the resulting NameError into an empty result rather than a failure.
    keep = set(names) | {"_truthy", "_merged_strategy_settings"}
    wanted = [n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name in keep]
    found = {n.name for n in wanted}
    assert set(names) <= found, f"missing from broker.py: {set(names) - found}"
    ns = {"mode": "backtest", "MODE_BACKTEST": "backtest",
          "MODE_LIVE": "live", "data_feed": None,
          "_log": lambda *a, **k: None}
    exec(compile(ast.Module(body=wanted, type_ignores=[]), _BROKER, "exec"), ns)
    return ns


def spec(**config):
    return [{"strategy": "strategy_eb", "config": config}]


def test_the_declared_eb_universe_is_returned():
    ns = _extract("_strategy_eb_universe_symbols")
    syms = ns["_strategy_eb_universe_symbols"](spec(strategy_eb_enabled=True))
    assert syms == ["QQQ", "TQQQ", "SPY", "BIL"]


def test_the_qld_variant_is_returned_when_configured():
    ns = _extract("_strategy_eb_universe_symbols")
    syms = ns["_strategy_eb_universe_symbols"](
        spec(strategy_eb_enabled=True, core_symbol="QLD", core_leverage=2.0))
    assert "QLD" in syms and "TQQQ" not in syms


def test_a_disabled_eb_contributes_no_symbols():
    ns = _extract("_strategy_eb_universe_symbols")
    assert ns["_strategy_eb_universe_symbols"](
        spec(strategy_eb_enabled=False)) == []


def test_an_absent_eb_contributes_no_symbols():
    ns = _extract("_strategy_eb_universe_symbols")
    assert ns["_strategy_eb_universe_symbols"](
        [{"strategy": "graph_nexus_analysis", "config": {}}]) == []


def test_the_legacy_unseparated_id_is_matched_too():
    ns = _extract("_strategy_eb_universe_symbols")
    assert ns["_strategy_eb_universe_symbols"](
        [{"strategy": "StrategyEb", "config": {"strategy_eb_enabled": True}}])


def test_a_malformed_spec_list_does_not_raise():
    ns = _extract("_strategy_eb_universe_symbols")
    for junk in (None, [], [None], ["strategy_eb"], [{"strategy": None}]):
        assert ns["_strategy_eb_universe_symbols"](junk) == [], junk


def test_both_wiring_points_reference_the_eb_universe():
    """A source assertion, because the fetch site is inline in a 4,000-line
    function and cannot be AST-extracted. Missing either point is silent."""
    source = open(_BROKER).read()
    uses = source.count("_strategy_eb_universe_symbols(")
    # one definition + the prepare (price) site + the fetch site
    assert uses >= 3, f"expected the EB universe at both wiring points, saw {uses}"


def test_the_vts_data_symbols_reach_the_fetch_site_when_the_overlay_is_on():
    """VTS adds two DATA symbols to the EB universe; the fetch site must see
    them or the overlay runs blind (and, before 2026-09-03, would then have
    kept its every-session cadence with no signal behind it)."""
    ns = _extract("_strategy_eb_universe_symbols")
    off = ns["_strategy_eb_universe_symbols"](spec(strategy_eb_enabled=True))
    assert "VIXY" not in off and "VIXM" not in off
    on = ns["_strategy_eb_universe_symbols"](spec(strategy_eb_enabled=True, trend_filter_bars=25, vts_enabled=True))
    assert "VIXY" in on and "VIXM" in on


# --- the live pending-order reader reaches EB and nothing else --------------

from types import SimpleNamespace  # noqa: E402

from broker_adapters.base import OrderRef  # noqa: E402


def adapter(*orders):
    """A live adapter surface: open orders, and no pending_execution_symbols."""
    return SimpleNamespace(
        list_open_orders_strict=lambda limit=200: [
            OrderRef(broker_order_id="b", client_order_id="c", symbol=s,
                     side="buy", qty=1.0, status=st) for s, st in orders])


def live(ns):
    ns["mode"] = "live"
    return ns


def test_live_mode_hands_eb_a_working_pending_order_reader():
    ns = live(_extract("_eb_live_portfolio_view"))
    live_adapter = adapter(("tqqq", "new"), ("SPY", "filled"))
    view = ns["_eb_live_portfolio_view"]("strategy_eb", live_adapter)
    assert view is not live_adapter
    assert view.pending_execution_symbols() == ("TQQQ",)


def test_the_legacy_unseparated_id_is_wired_too():
    ns = live(_extract("_eb_live_portfolio_view"))
    assert ns["_eb_live_portfolio_view"]("StrategyEb", adapter()) is not None
    assert hasattr(ns["_eb_live_portfolio_view"]("StrategyEb", adapter()),
                   "pending_execution_symbols")


def test_no_other_strategy_is_given_the_reader():
    """Strategy X reads the same method through getattr. Attaching it to the
    adapter itself would silently change its live kicker."""
    ns = live(_extract("_eb_live_portfolio_view"))
    for name in ("strategy_x", "graph_nexus_analysis", "", None):
        live_adapter = adapter(("TQQQ", "new"))
        assert ns["_eb_live_portfolio_view"](name, live_adapter) is live_adapter


def test_a_backtest_emulator_is_passed_through_untouched():
    ns = _extract("_eb_live_portfolio_view")  # ns["mode"] is "backtest"
    emulator = adapter(("TQQQ", "new"))
    assert ns["_eb_live_portfolio_view"]("strategy_eb", emulator) is emulator


def test_an_emulator_that_already_reads_pending_orders_is_not_wrapped():
    ns = live(_extract("_eb_live_portfolio_view"))
    emulator = SimpleNamespace(pending_execution_symbols=lambda: ("SPY",))
    assert ns["_eb_live_portfolio_view"]("strategy_eb", emulator) is emulator


def test_a_missing_emulator_stays_missing():
    ns = live(_extract("_eb_live_portfolio_view"))
    assert ns["_eb_live_portfolio_view"]("strategy_eb", None) is None


def test_the_run_once_call_site_passes_the_view():
    """A source assertion: the call site is inline in a 1,000-line function.
    Defining the helper and never calling it is a silent no-op live."""
    source = open(_BROKER).read()
    assert source.count("_eb_live_portfolio_view(") >= 2
    assert "portfolio_emulator=_eb_live_portfolio_view(" in source
