"""Strategy HX must get bars for legs the watchlist never lists.

Missing the fetch site is SILENT: the reference index has no bars, the state
machine reads UNKNOWN forever, and the strategy parks the whole book in cash
for the length of the backtest while every unit test still passes.
"""
import ast
import os
import sys

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

_BROKER = os.path.join(_BACKEND, "broker.py")


def _module_assign(name):
    """A module-level literal assignment, read out of broker.py's source."""
    tree = ast.parse(open(_BROKER).read())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == name
                for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} not found at broker.py module scope")


def _extract(*names):
    """AST-extract broker functions into a stub namespace. broker.py argparses
    at module scope and SystemExits under pytest, so it cannot be imported."""
    tree = ast.parse(open(_BROKER).read())
    # `_truthy` comes along because the HX config reader calls it, and the
    # blanket `except Exception` would turn the resulting NameError into an
    # empty result rather than a failure.
    keep = set(names) | {"_truthy"}
    wanted = [n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name in keep]
    found = {n.name for n in wanted}
    assert set(names) <= found, f"missing from broker.py: {set(names) - found}"
    ns = {"mode": "backtest", "MODE_BACKTEST": "backtest",
          "MODE_LIVE": "live", "data_feed": None,
          "_LANE_ENABLE_FLAGS": _module_assign("_LANE_ENABLE_FLAGS"),
          "_log": lambda *a, **k: None}
    exec(compile(ast.Module(body=wanted, type_ignores=[]), _BROKER, "exec"), ns)
    return ns


def spec(**config):
    return [{"strategy": "strategy_hx", "config": config}]


def test_the_declared_hx_universe_is_returned():
    ns = _extract("_strategy_hx_universe_symbols")
    assert ns["_strategy_hx_universe_symbols"](spec(strategy_hx_enabled=True)) \
        == ["BIL", "PSQ", "QQQ", "TQQQ"]


def test_a_variant_bear_book_adds_its_legs():
    ns = _extract("_strategy_hx_universe_symbols")
    syms = ns["_strategy_hx_universe_symbols"](
        spec(strategy_hx_enabled=True, bear_book={"SQQQ": 0.25, "BIL": 0.75}))
    assert "SQQQ" in syms and "PSQ" not in syms


def test_a_disabled_or_absent_hx_contributes_no_symbols():
    """The string "false" counts as disabled: raw truthiness says
    bool("false") is True, so a config storing the flag as a string would
    otherwise fetch bars for an inert strategy."""
    ns = _extract("_strategy_hx_universe_symbols")
    fn = ns["_strategy_hx_universe_symbols"]
    assert fn(spec(strategy_hx_enabled=False)) == []
    assert fn(spec(strategy_hx_enabled="false")) == []
    assert fn([{"strategy": "graph_nexus_analysis", "config": {}}]) == []


def test_a_malformed_spec_list_does_not_raise():
    ns = _extract("_strategy_hx_universe_symbols")
    for junk in (None, [], [None], ["strategy_hx"], [{"strategy": None}]):
        assert ns["_strategy_hx_universe_symbols"](junk) == [], junk


def test_the_fetch_site_references_the_hx_universe():
    """A source assertion, because the fetch site is inline in a 4,000-line
    function and cannot be AST-extracted."""
    source = open(_BROKER).read()
    uses = source.count("_strategy_hx_universe_symbols(")
    assert uses >= 2, f"expected the definition and the fetch site, saw {uses}"


def test_the_deploy_check_hashes_both_hx_files():
    """2026-09-03: the EB pair was missing from FILES, so a push that changed
    only strategy_eb.py reported 'deployed' instantly and a pre-registered
    engine run started on the OLD image."""
    path = os.path.join(os.path.dirname(_BACKEND), "scripts",
                        "check_deployed_code.py")
    source = open(path).read()
    assert '"backend/strategy_hx.py"' in source
    assert '"backend/strategies/strategy_hx.py"' in source


def test_the_hx_lane_is_registered_for_the_live_single_position_cap():
    """The BACKTEST path reads `broker_max_single_position_pct` off any lane
    setting `honour_single_position_cap`; LIVE is narrower and honours it only
    for lanes named in `_LANE_ENABLE_FLAGS`. Unregistered, a live HX tick keeps
    the 15% failsafe and every 65%-of-NAV core buy is trimmed to $0.00 —
    BT102936, and the way Strategy XS shipped inert."""
    flags = _module_assign("_LANE_ENABLE_FLAGS")
    assert flags.get("strategy_hx") == "strategy_hx_enabled"
    ns = _extract("_strategy_eb_single_position_pct")
    cap = ns["_strategy_eb_single_position_pct"](
        spec(strategy_hx_enabled=True, honour_single_position_cap=True,
             broker_max_single_position_pct=0.95))
    assert cap == 0.95
    assert ns["_strategy_eb_single_position_pct"](
        spec(strategy_hx_enabled=False, honour_single_position_cap=True,
             broker_max_single_position_pct=0.95)) is None


def test_every_registered_lane_has_a_defaults_row():
    """`_strategy_eb_risk_limits` indexes `defaults_by_lane[name]` for every
    name in `_LANE_ENABLE_FLAGS`, OUTSIDE the per-lane except. A lane
    registered without a defaults row raises KeyError into the outer handler
    and returns None for the WHOLE document — strategy_eb's envelope thrown
    away and every live buy blocked on max_order_notional, silently. That is
    the 2026-09-03 regression the per-lane handler exists to prevent."""
    tree = ast.parse(open(_BROKER).read())
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
              and n.name == "_strategy_eb_risk_limits")
    node = next(n for n in ast.walk(fn) if isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "defaults_by_lane"
                        for t in n.targets))
    mapped = {k.value for k in node.value.keys}
    assert set(_module_assign("_LANE_ENABLE_FLAGS")) <= mapped


def test_the_health_fingerprint_and_the_deploy_check_list_the_same_files():
    """`scripts/check_deployed_code.py` compares local hashes against the ones
    GET /health publishes, and the API keys that response by BASENAME. A file
    in FILES but not in `_CODE_FINGERPRINT_FILES` has no server-side hash at
    all, so the check reports it "deployed <missing>" — green-looking output
    for a file nobody verified. That is the 2026-09-03 failure one layer up:
    there it was the local list, here it is the served one.

    The two lists differ only by the `backend/` prefix: the image is built with
    `context: ./backend`, so `backend/broker.py` in git is `/app/broker.py` in
    the container.
    """
    def _literal(path, name):
        for node in ast.parse(open(path).read()).body:
            if isinstance(node, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id == name
                    for t in node.targets):
                return list(ast.literal_eval(node.value))
        raise AssertionError(f"{name} not found in {path}")

    root = os.path.dirname(_BACKEND)
    served = set(_literal(os.path.join(_BACKEND, "api", "main.py"),
                          "_CODE_FINGERPRINT_FILES"))
    checked = {p[len("backend/"):] for p in _literal(
        os.path.join(root, "scripts", "check_deployed_code.py"), "FILES")}
    assert served == checked, {"served_only": sorted(served - checked),
                               "checked_only": sorted(checked - served)}
    assert {"strategy_hx.py", "strategies/strategy_hx.py"} <= served
    assert ({os.path.basename(p) for p in served}
            == {os.path.basename(p) for p in checked})
