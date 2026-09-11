"""Transport-only tests for the HX lab setup. No network, no engine."""
import importlib.util
import os
import sys

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ROOT = os.path.dirname(_BACKEND)
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from strategy_hx import DEFAULTS  # noqa: E402


def _module(name):
    """Load a scripts/ module without importing scripts._api, which reads .env
    and would try to log in."""
    path = os.path.join(_ROOT, "scripts", f"{name}.py")
    spec = importlib.util.spec_from_file_location(f"_hx_{name}", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_the_lane_is_a_run_once_lane_carrying_enabled_defaults():
    setup = _module("hx_lab_setup")
    lane = setup.lane(DEFAULTS)
    assert lane["strategy"] == "strategy_hx"
    assert lane["execution_scope"] == "run_once"
    assert lane["decision_phase"] == "pre"
    assert lane["execution_position"] == 10
    assert lane["weight"] == 1.0
    assert lane["conditions"] == {}
    assert lane["config"]["strategy_hx_enabled"] is True
    assert lane["config"]["core_symbol"] == "TQQQ"
    # Every other key is DEFAULTS untouched.
    assert set(lane["config"]) == set(DEFAULTS)


def test_the_lab_stocks_are_the_universe_plus_the_benchmark():
    setup = _module("hx_lab_setup")
    assert setup.STOCKS == ["BIL", "PSQ", "QQQ", "SPY", "TQQQ"]


def test_a_protected_doc_id_is_refused_outright():
    """Doc 200 is the live champion and 201 is the EB lab. A killed runner has
    already left doc 201 holding a candidate's config once; this script must
    never be the thing that writes to either."""
    setup = _module("hx_lab_setup")
    for doc_id in (200, "200", 201, "201"):
        try:
            setup.assert_writable(doc_id)
        except SystemExit as error:
            assert "200" in str(error) or "201" in str(error)
        else:
            raise AssertionError(f"{doc_id!r} was not refused")
    setup.assert_writable(444)


def test_an_existing_doc_is_updated_in_place_rather_than_duplicated():
    setup = _module("hx_lab_setup")
    seen = []

    def fake_call(method, path, body=None, **kwargs):
        seen.append((method, path))
        if (method, path) == ("GET", "/strategies"):
            return 200, {"strategies": [{"id": 444,
                                         "name": setup.DOC_NAME}]}
        if method == "PUT":
            return 200, {"id": 444}
        if path == f"/instances/{setup.INSTANCE_ID}":
            return 200, {"id": setup.INSTANCE_ID, "strategy_id": 444,
                         "granularity_time_increment": 86400}
        if path == "/instances/strategy-eb":
            return 200, {"brokerage_id": "brk-1"}
        return 200, {}

    assert setup.main(call=fake_call) == 0
    assert ("PUT", "/strategies/444") in seen
    assert not any(m == "POST" and p == "/strategies" for m, p in seen)


def test_a_missing_instance_is_cloned_from_the_eb_brokerage():
    setup = _module("hx_lab_setup")
    posted = {}

    def fake_call(method, path, body=None, **kwargs):
        if (method, path) == ("GET", "/strategies"):
            return 200, {"strategies": []}
        if (method, path) == ("POST", "/strategies"):
            return 200, {"id": 555}
        if path == f"/instances/{setup.INSTANCE_ID}" and method == "GET":
            if not posted:
                raise SystemExit("HTTP 404 on GET /instances/strategy-hx-lab")
            return 200, dict(posted["body"])
        if path == "/instances/strategy-eb":
            return 200, {"brokerage_id": "brk-1"}
        if (method, path) == ("POST", "/instances"):
            posted["body"] = body
            return 200, body
        return 200, {}

    assert setup.main(call=fake_call) == 0
    assert posted["body"]["brokerage_id"] == "brk-1"
    assert posted["body"]["granularity_time_increment"] == 86400
    assert posted["body"]["strategy_id"] == 555
    assert posted["body"]["stocks"] == setup.STOCKS
