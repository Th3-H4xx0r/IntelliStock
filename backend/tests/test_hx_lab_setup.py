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
    # PATCH /instances/{id} cannot do this: EditInstanceBody carries name,
    # granularity, max_usage and brokerage_id, and NOTHING else — a
    # strategy_id sent there is dropped and the printed line is a lie.
    assert ("POST", f"/instances/{setup.INSTANCE_ID}/link-strategy") in seen
    assert not any(m == "PATCH" for m, _ in seen)


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
    # `CreateInstanceBody` reads `granularity` (a string, parsed to seconds)
    # and has no `granularity_time_increment` field at all: sent under the
    # wrong name it is dropped and the instance is created at the 60s default,
    # which is 1-minute stepping over a five-year window.
    assert posted["body"]["granularity"] == "86400"
    assert "granularity_time_increment" not in posted["body"]
    assert posted["body"]["strategy_id"] == 555
    assert posted["body"]["stocks"] == setup.STOCKS


def test_a_duplicate_stock_is_tolerated_but_a_real_failure_is_not():
    """`except BaseException: pass` swallowed every outcome — an auth failure,
    a 500, a typo'd instance id — and left the instance short a leg. A leg the
    instance does not list has no bars and no price, and the strategy simply
    never trades it: silent, and indistinguishable from a working run."""
    setup = _module("hx_lab_setup")

    def base_call(method, path, body=None, **kwargs):
        if (method, path) == ("GET", "/strategies"):
            return 200, {"strategies": [{"id": 444, "name": setup.DOC_NAME}]}
        if path == f"/instances/{setup.INSTANCE_ID}" and method == "GET":
            return 200, {"id": setup.INSTANCE_ID, "strategy_id": 444}
        return 200, {}

    def duplicate(method, path, body=None, **kwargs):
        if path.endswith("/stocks") and method == "POST":
            raise SystemExit("HTTP 409 on POST /instances/strategy-hx-lab/"
                             "stocks\n{\"detail\":\"Symbol already added\"}")
        return base_call(method, path, body, **kwargs)

    assert setup.main(call=duplicate) == 0

    def broken(method, path, body=None, **kwargs):
        if path.endswith("/stocks") and method == "POST":
            raise SystemExit("HTTP 500 on POST /instances/strategy-hx-lab/"
                             "stocks\n{\"detail\":\"database is down\"}")
        return base_call(method, path, body, **kwargs)

    try:
        setup.main(call=broken)
    except SystemExit as error:
        assert "500" in str(error)
    else:
        raise AssertionError("a 500 on the stock loop was swallowed")


def test_the_api_client_retries_a_transient_5xx(monkeypatch):
    """`_http` raises SystemExit, which derives from BaseException, so the
    `except Exception` retry loop never caught anything: the documented 5xx
    retry was dead and a deploy rebuild's transient 502 killed the run on the
    spot. Loading the module runs `_load_dotenv`, which only reads .env into
    this process — no network, and `auth` is stubbed below so no login."""
    import time

    monkeypatch.setattr(time, "sleep", lambda *_: None)
    api = _module("_api")
    attempts = []

    def flaky(method, url, *, headers=None, body=None, timeout=60):
        attempts.append(url)
        if len(attempts) <= 2:
            raise SystemExit(f"HTTP 502 on {method} {url}\nBad Gateway")
        return 200, {"ok": True}

    api.auth = lambda: "token"
    api._http = flaky
    api.API = "https://example.invalid"
    assert api.call("GET", "/strategies", retries=4) == (200, {"ok": True})
    assert len(attempts) == 3

    # A 4xx is not transient: it must surface on the first attempt.
    del attempts[:]

    def refused(method, url, *, headers=None, body=None, timeout=60):
        attempts.append(url)
        raise SystemExit(f"HTTP 404 on {method} {url}\nNot Found")

    api._http = refused
    try:
        api.call("GET", "/instances/nope")
    except SystemExit as error:
        assert "404" in str(error)
    else:
        raise AssertionError("a 404 was retried or swallowed")
    assert len(attempts) == 1
