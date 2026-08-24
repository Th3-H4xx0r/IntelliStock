"""The conviction-score channel: graph_nexus -> broker -> a sibling strategy.

Before 2026-08-23 `data["conviction_scores"]` had two readers and zero writers,
so any graph-ranked satellite sleeve was silently inert — it held nothing and
logged nothing. These tests pin each link so that cannot regress.
"""
import ast
import os
import sys
import types

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)


def _extract_broker_block():
    """broker.py is not import-safe (argparse at module scope SystemExits under
    pytest), so pull the run_once result handler out and exec it in a stub — the
    established pattern in test_residual_sleeve.py."""
    src = open(os.path.join(_backend, "broker.py")).read()
    return src


def test_broker_pops_and_injects_conviction_scores():
    """The pop + merge into `data` must exist and be spelled exactly as the
    consumers read it."""
    src = _extract_broker_block()
    assert 'raw.pop("_nexus_conviction_scores"' in src, (
        "broker no longer pops the conviction-score key")
    assert 'data["conviction_scores"] = merged' in src, (
        "broker no longer injects into the shared data map")


def test_graph_nexus_publishes_conviction_scores():
    src = open(os.path.join(_backend, "strategies",
                            "graph_nexus_analysis.py")).read()
    assert 'scores["_nexus_conviction_scores"] = _cs_map' in src, (
        "graph_nexus no longer publishes conviction scores")
    assert '"raw_net_score"' in src


def test_strategy_x_runs_after_graph_nexus():
    """Ordering is load-bearing: Nexus must publish before strategy_x reads."""
    import json
    import re

    def pos(path):
        src = open(os.path.join(_backend, "strategies", path)).read()
        m = re.search(r"# INTELLISTOCK_SCHEMA: (.*)", src)
        return json.loads(m.group(1)).get("execution_position")

    assert pos("strategy_x.py") > pos("graph_nexus_analysis.py"), (
        "strategy_x must have a HIGHER execution_position than "
        "graph_nexus_analysis, or it reads an empty conviction map")


def test_strategy_x_consumes_the_injected_scores():
    """End to end on the consumer side: given the map the broker would inject,
    the sleeve ranks and buys."""
    from strategies.strategy_x import StrategyX
    from datetime import datetime, timedelta, timezone

    now = datetime(2026, 6, 1, 20, 0, tzinfo=timezone.utc)

    def bars(n, start=100.0, step=0.5):
        return [{"t": (now - timedelta(days=(n - 1 - i))).isoformat(),
                 "c": start + i * step} for i in range(n)]

    class Emu:
        _positions = {}

        def get_cash(self):
            return 10000.0

        def get_positions(self):
            return {}

        def get_portfolio_value(self, prices=None):
            return 10000.0

    cfg = {"strategy_x_enabled": True, "satellite_pct": 0.2,
           "satellite_max_names": 2, "core_weight": 0.9,
           "core_filter_symbol": "QQQ", "core_bull_symbol": "TQQQ",
           "core_chop_symbol": "SPY"}
    prices = {"TQQQ": 50.0, "SPY": 500.0, "QQQ": 400.0,
              "AAPL": 200.0, "MSFT": 300.0}
    data = {
        "QQQ": {"bars": bars(260)},
        # exactly what the broker merges in from graph_nexus
        "conviction_scores": {"AAPL": 1.8, "MSFT": 0.4},
    }
    out = StrategyX().run_once(["TQQQ", "SPY"], prices, now, cfg, {},
                               data=data, portfolio_emulator=Emu(),
                               strategy_cache={})
    assert out.get("AAPL") == 1, "the graph-ranked sleeve did not buy"
    sizes = out["_nexus_position_sizes"]
    assert sizes["AAPL"]["buy_cash"] > 0


def test_sleeve_holds_nothing_when_no_scores_are_published():
    """The inert case must be SAFE, not a crash or an arbitrary pick."""
    from strategies.strategy_x import StrategyX
    from datetime import datetime, timedelta, timezone

    now = datetime(2026, 6, 1, 20, 0, tzinfo=timezone.utc)
    bars = [{"t": (now - timedelta(days=(259 - i))).isoformat(),
             "c": 100.0 + i * 0.5} for i in range(260)]

    class Emu:
        def get_cash(self):
            return 10000.0

        def get_positions(self):
            return {}

        def get_portfolio_value(self, prices=None):
            return 10000.0

    cfg = {"strategy_x_enabled": True, "satellite_pct": 0.2,
           "core_filter_symbol": "QQQ"}
    out = StrategyX().run_once(["TQQQ"], {"TQQQ": 50.0, "SPY": 500.0,
                                          "QQQ": 400.0},
                               now, cfg, {}, data={"QQQ": {"bars": bars}},
                               portfolio_emulator=Emu(), strategy_cache={})
    # core absorbs the sleeve; nothing else is bought
    assert set(k for k in out if not k.startswith("_")) <= {"TQQQ", "SPY"}
