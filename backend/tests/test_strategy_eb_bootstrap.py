"""The Strategy EB deployment bootstrap: what it sends, and what it insists on
getting back.

The script's whole reason to exist is the failure that reverted a Strategy XS
edit: the API normalises every lane on save (interactive_utils
_normalize_strategy_payload_item), so a lane can come back with a different
`strategy` id or a config short of keys and nothing says so. Writing is not the
risky part; believing the write is.

No network here: `call` is replaced by a recorder.
"""
import os
import sys

import pytest

_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _path in (os.path.join(_root, "scripts"), os.path.join(_root, "backend")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import strategy_eb_bootstrap as boot  # noqa: E402
from strategy_eb import DEFAULTS, strategy_eb_universe  # noqa: E402


class FakeAPI:
    """Records every call and replays canned responses by (method, path)."""

    def __init__(self, responses=None):
        self.calls = []
        self.responses = dict(responses or {})

    def __call__(self, method, path, body=None):
        self.calls.append((method, path, body))
        if (method, path) in self.responses:
            value = self.responses[(method, path)]
            return value(body) if callable(value) else value
        return 200, {}

    def bodies(self, method, path):
        return [b for m, p, b in self.calls if (m, p) == (method, path)]


def _saved_doc(doc_id=201, config=None, strategy="strategy_eb", lanes=1):
    lane = {"strategy": strategy, "weight": 1.0, "execution_position": 10,
            "decision_phase": "pre", "execution_scope": "run_once",
            "conditions": {}, "config": dict(config or boot.LANE["config"])}
    return {"id": doc_id, "name": boot.DOC_NAME, "strategies": [lane] * lanes}


def _saved_instance(doc_id=201, stocks=None):
    return {"strategy_id": doc_id,
            "stocks": list(stocks if stocks is not None else boot.STOCKS)}


# ------------------------------------------------------------------ payloads
def test_the_lane_carries_every_default_key_and_only_flips_enabled():
    config = boot.LANE["config"]
    assert set(config) == set(DEFAULTS)
    assert config["strategy_eb_enabled"] is True
    assert {k: v for k, v in config.items() if k != "strategy_eb_enabled"} == \
           {k: v for k, v in DEFAULTS.items() if k != "strategy_eb_enabled"}


def test_the_lane_id_is_the_lower_case_module_name():
    """The broker resolves the class from this string; CapitalCase silently
    resolves to a different (or no) strategy."""
    assert boot.LANE["strategy"] == "strategy_eb"
    assert boot.LANE["execution_scope"] == "run_once"
    assert boot.LANE["decision_phase"] == "pre"
    assert boot.LANE["weight"] == 1.0
    assert boot.LANE["execution_position"] == 10


def test_the_document_payload_is_exactly_one_lane():
    """A second enabled lane would inherit this document's 95% single-position
    cap, which becomes a process-wide env var in the backtest container."""
    payload = boot.doc_payload()
    assert payload["name"] == boot.DOC_NAME
    assert len(payload["strategies"]) == 1


def test_the_instance_payload_is_daily_and_not_running():
    payload = boot.instance_payload(201)
    assert payload["id"] == "strategy-eb"
    assert payload["granularity"] == "86400"
    assert payload["strategy_id"] == 201
    assert payload["run_command"] is False
    assert set(payload["stocks"]) == {"TQQQ", "SPY", "BIL", "QQQ"}


def test_the_watchlist_is_derived_from_the_config_not_written_out_by_hand():
    """Configure a remainder book and the instance must fetch its bars. A
    literal list would leave the book legs unpriced and the strategy silent."""
    assert boot.STOCKS == strategy_eb_universe(boot.LANE["config"])
    booked = dict(boot.LANE["config"], trend_on_book={"GLD": 0.5, "GDX": 0.5})
    assert "GLD" in strategy_eb_universe(booked)


def test_the_instance_payload_coerces_a_string_doc_id_to_int():
    assert boot.instance_payload("201")["strategy_id"] == 201
    assert boot.link_payload("201") == {"strategy_id": 201}


# ------------------------------------------------------------------ verify
def test_config_drift_is_empty_when_everything_round_trips():
    assert boot.config_drift(boot.LANE["config"]) == {}


def test_config_drift_names_a_coerced_value():
    saved = dict(boot.LANE["config"], remainder_bil_fraction=1.0)
    drift = boot.config_drift(saved)
    assert set(drift) == {"remainder_bil_fraction"}
    assert drift["remainder_bil_fraction"] == {"sent": 0.0, "saved": 1.0}


def test_config_drift_names_a_dropped_key():
    saved = {k: v for k, v in boot.LANE["config"].items() if k != "cash_sweep_min_pct"}
    drift = boot.config_drift(saved)
    assert "cash_sweep_min_pct" in drift
    assert drift["cash_sweep_min_pct"]["saved"] is None


def test_verify_accepts_a_faithful_round_trip():
    api = FakeAPI({("GET", "/strategies/201"): (200, _saved_doc()),
                   ("GET", "/instances/strategy-eb"): (200, _saved_instance())})
    boot.verify(201, call=api)
    assert ("GET", "/strategies/201", None) in api.calls


def test_verify_rejects_a_capitalcase_lane_id():
    api = FakeAPI({("GET", "/strategies/201"): (200, _saved_doc(strategy="StrategyEb")),
                   ("GET", "/instances/strategy-eb"): (200, _saved_instance())})
    with pytest.raises(SystemExit, match="StrategyEb"):
        boot.verify(201, call=api)


def test_verify_rejects_a_second_lane():
    api = FakeAPI({("GET", "/strategies/201"): (200, _saved_doc(lanes=2)),
                   ("GET", "/instances/strategy-eb"): (200, _saved_instance())})
    with pytest.raises(SystemExit, match="one lane"):
        boot.verify(201, call=api)


def test_verify_rejects_a_silently_changed_config_value():
    saved = dict(boot.LANE["config"], core_max_weight=0.15)
    api = FakeAPI({("GET", "/strategies/201"): (200, _saved_doc(config=saved)),
                   ("GET", "/instances/strategy-eb"): (200, _saved_instance())})
    with pytest.raises(SystemExit, match="core_max_weight"):
        boot.verify(201, call=api)


def test_verify_rejects_an_instance_missing_a_universe_symbol():
    """A declared leg with no bars is a silent no-op, so the watchlist is
    verified against the SAVED config's universe."""
    api = FakeAPI({("GET", "/strategies/201"): (200, _saved_doc()),
                   ("GET", "/instances/strategy-eb"):
                       (200, _saved_instance(stocks=["TQQQ", "SPY", "BIL"]))})
    with pytest.raises(SystemExit, match="QQQ"):
        boot.verify(201, call=api)


def test_verify_reads_the_universe_off_the_saved_config_not_the_sent_one():
    """The API coerced a book in; the instance was built for the old universe.
    Verifying against what we SENT would call that clean."""
    saved = dict(boot.LANE["config"], trend_on_book={"GLD": 1.0})
    api = FakeAPI({("GET", "/strategies/201"): (200, _saved_doc(config=saved)),
                   ("GET", "/instances/strategy-eb"): (200, _saved_instance())})
    with pytest.raises(SystemExit, match="GLD"):
        boot.verify(201, call=api)


def test_verify_accepts_a_watchlist_wider_than_the_universe():
    api = FakeAPI({("GET", "/strategies/201"): (200, _saved_doc()),
                   ("GET", "/instances/strategy-eb"):
                       (200, _saved_instance(stocks=list(boot.STOCKS) + ["AAPL"]))})
    boot.verify(201, call=api)


def test_verify_rejects_an_instance_linked_elsewhere():
    api = FakeAPI({("GET", "/strategies/201"): (200, _saved_doc()),
                   ("GET", "/instances/strategy-eb"): (200, _saved_instance(198))})
    with pytest.raises(SystemExit, match="198"):
        boot.verify(201, call=api)


def test_verify_reports_a_failed_read():
    api = FakeAPI({("GET", "/strategies/201"): (404, {"detail": "nope"})})
    with pytest.raises(SystemExit, match="404"):
        boot.verify(201, call=api)


# ------------------------------------------------------------------ create
def test_create_posts_the_document_the_instance_and_the_link():
    api = FakeAPI({
        ("GET", "/strategies"): (200, {"strategies": []}),
        ("POST", "/strategies"): (201, {"id": 201}),
        ("GET", "/instances/strategy-eb"): (404, {"detail": "not found"}),
        ("GET", "/strategies/201"): (200, _saved_doc()),
    })

    def _instance_after_create(_body):
        api.responses[("GET", "/instances/strategy-eb")] = (200, _saved_instance())
        return 201, {"id": "strategy-eb"}

    api.responses[("POST", "/instances")] = _instance_after_create
    assert boot.create(call=api) == 201
    assert api.bodies("POST", "/strategies") == [boot.doc_payload()]
    assert api.bodies("POST", "/instances") == [boot.instance_payload(201)]
    assert api.bodies("POST", "/instances/strategy-eb/link-strategy") == \
        [boot.link_payload(201)]


def test_create_is_idempotent_when_the_document_and_instance_exist():
    api = FakeAPI({
        ("GET", "/strategies"): (200, {"strategies": [_saved_doc()]}),
        ("GET", "/instances/strategy-eb"): (200, _saved_instance()),
        ("GET", "/strategies/201"): (200, _saved_doc()),
    })
    assert boot.create(call=api) == 201
    assert api.bodies("POST", "/strategies") == []
    assert api.bodies("POST", "/instances") == []


def test_create_relinks_an_existing_instance_pointing_at_another_document():
    api = FakeAPI({
        ("GET", "/strategies"): (200, {"strategies": [_saved_doc()]}),
        ("GET", "/instances/strategy-eb"): (200, _saved_instance(198)),
        ("GET", "/strategies/201"): (200, _saved_doc()),
    })

    def _link(_body):
        api.responses[("GET", "/instances/strategy-eb")] = (200, _saved_instance())
        return 200, {}

    api.responses[("POST", "/instances/strategy-eb/link-strategy")] = _link
    boot.create(call=api)
    assert api.bodies("POST", "/instances/strategy-eb/link-strategy") == \
        [boot.link_payload(201)]


def test_create_refuses_when_two_documents_share_the_name():
    api = FakeAPI({("GET", "/strategies"):
                   (200, {"strategies": [_saved_doc(201), _saved_doc(202)]})})
    with pytest.raises(SystemExit, match="201"):
        boot.create(call=api)


def test_a_failed_document_post_stops_before_the_instance():
    api = FakeAPI({("GET", "/strategies"): (200, {"strategies": []}),
                   ("POST", "/strategies"): (500, {"detail": "boom"})})
    with pytest.raises(SystemExit, match="500"):
        boot.create(call=api)
    assert api.bodies("POST", "/instances") == []


# ------------------------------------------------------------------ listings
def test_documents_reads_both_the_wrapped_and_the_bare_listing_shape():
    """GET /strategies returns {"strategies": [...]}; older deployments and
    fixtures return the bare list."""
    wrapped = {"strategies": [_saved_doc()]}
    assert boot.documents(wrapped)[0]["id"] == 201
    assert boot.documents([_saved_doc()])[0]["id"] == 201
    assert boot.documents(None) == []


def test_show_prints_the_lane_without_tracebacking_on_an_empty_listing(capsys):
    api = FakeAPI({("GET", "/strategies"): (200, {"strategies": []})})
    boot.show(call=api)
    assert capsys.readouterr().out.strip() == "no document named %r" % boot.DOC_NAME


def test_show_prints_the_dial_and_the_cadence(capsys):
    api = FakeAPI({("GET", "/strategies"): (200, {"strategies": [_saved_doc()]})})
    boot.show(call=api)
    out = capsys.readouterr().out
    assert "strategy_eb" in out and "enabled=True" in out
    assert "TQQQ" in out and "[2]" in out


# ------------------------------------------------------------------ dry run
def test_dry_run_prints_the_payloads_and_sends_nothing(capsys):
    api = FakeAPI()
    assert boot.main(["create", "--dry-run"], call=api) == 0
    assert api.calls == []
    out = capsys.readouterr().out
    assert "POST /strategies" in out and "POST /instances" in out
    assert "link-strategy" in out
    assert "cash_sweep_min_pct" in out


def test_main_routes_show_verify_and_create(capsys):
    api = FakeAPI({("GET", "/strategies"): (200, {"strategies": [_saved_doc()]}),
                   ("GET", "/strategies/201"): (200, _saved_doc()),
                   ("GET", "/instances/strategy-eb"): (200, _saved_instance())})
    assert boot.main(["show"], call=api) == 0
    assert boot.main(["verify", "201"], call=api) == 0
    capsys.readouterr()


def test_verify_without_an_id_finds_the_document_by_name(capsys):
    api = FakeAPI({("GET", "/strategies"): (200, {"strategies": [_saved_doc()]}),
                   ("GET", "/strategies/201"): (200, _saved_doc()),
                   ("GET", "/instances/strategy-eb"): (200, _saved_instance())})
    assert boot.main(["verify"], call=api) == 0
    capsys.readouterr()
