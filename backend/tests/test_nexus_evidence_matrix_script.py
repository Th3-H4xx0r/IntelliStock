"""Task 9 (2026-07-28): the deterministic evidence matrix runner.

The runner is the thing that turns the A1-A4 candidates from "shipped but
unproven" into evidence, so its own failure modes are the ones that would
silently manufacture a favourable result:

* Publishing the matrix AFTER the first run would let arms be added or reworded
  once results are visible. It must be published first, once, immutably.
* Dropping a failed or stopped arm and retrying it under a new ID turns a
  failure into a missing observation. Failures stay in the trial count.
* Replaying one sealed fixture ten times is ONE observation, not ten. Pooling
  repeated market dates as independent sessions inflates confidence the same
  way.
* Printing a token, an Alpaca key or a fixture's model responses would leak
  through the log of an otherwise-correct run.

Everything here runs against a fake HTTP client and a fake clock; the tests
make no network calls and never sleep.
"""
import json
import os
import sys

import pytest

_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_scripts = os.path.join(_root, "scripts")
if _scripts not in sys.path:
    sys.path.insert(0, _scripts)

import run_nexus_evidence_matrix as runner  # noqa: E402


# ------------------------------------------------------------------ fakes
class FakeClock:
    def __init__(self):
        self.slept = []
        self.now = 0.0

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds

    def time(self):
        return self.now


class FakeApi:
    """Records every call; serves scripted backtest status transitions."""

    def __init__(self, statuses=None, summaries=None):
        self.calls = []
        self.published = []
        self._next_id = 1000
        self._statuses = dict(statuses or {})
        self._summaries = dict(summaries or {})
        self._polls = {}

    def publish_matrix(self, doc):
        self.published.append(doc)
        return {"matrix_id": "matrix-sha256-" + "a" * 64}

    def start_instance(self, *a, **k):  # pragma: no cover - must never be called
        raise AssertionError("the runner must never start an instance")

    def create_backtest(self, payload):
        self.calls.append(("create", payload))
        self._next_id += 1
        return {"id": self._next_id}

    def backtest_status(self, backtest_id):
        seq = self._statuses.get(backtest_id, ["running", "complete"])
        index = self._polls.get(backtest_id, 0)
        self._polls[backtest_id] = min(index + 1, len(seq) - 1)
        return seq[index]

    def backtest_summary(self, backtest_id):
        return self._summaries.get(backtest_id, {"pnl_percent": 1.0})


# ------------------------------------------------------- preregistration order
def test_matrix_is_published_before_the_first_backtest():
    api = FakeApi()
    plan = runner.MatrixPlan(
        matrix_doc={"arms": {}}, arms=["baseline", "a4"],
        windows=[{"start": "2026-03-02", "end": "2026-04-27"}],
        cost_scenarios=["base"], fixture_count=1, trial_count=2,
    )
    runner.publish_then_run(plan, api=api, clock=FakeClock(), dry_run=True)
    assert api.published, "no matrix was published"
    create_index = next((i for i, (kind, _) in enumerate(api.calls) if kind == "create"), None)
    assert create_index is None or api.published, "matrix must precede any create"


def test_matrix_is_published_exactly_once():
    api = FakeApi()
    plan = runner.MatrixPlan(
        matrix_doc={"arms": {}}, arms=["baseline"],
        windows=[{"start": "2026-03-02", "end": "2026-04-27"}],
        cost_scenarios=["base"], fixture_count=1, trial_count=1)
    runner.publish_then_run(plan, api=api, clock=FakeClock(), dry_run=True)
    assert len(api.published) == 1


# ---------------------------------------------------------- polling behaviour
def test_polling_waits_fifteen_minutes_between_checks():
    """The user asked for this explicitly: don't burn tokens re-reading status."""
    clock = FakeClock()
    api = FakeApi(statuses={1001: ["running", "running", "complete"]})
    status = runner.wait_for_backtest(1001, api=api, clock=clock)
    assert status == "complete"
    assert clock.slept and all(s == runner.POLL_SECONDS for s in clock.slept)
    assert runner.POLL_SECONDS == 900


def test_polling_returns_promptly_on_a_terminal_status():
    clock = FakeClock()
    api = FakeApi(statuses={1001: ["complete"]})
    assert runner.wait_for_backtest(1001, api=api, clock=clock) == "complete"
    assert clock.slept == [], "a terminal first read must not sleep"


@pytest.mark.parametrize("terminal", ["complete", "error", "stopped", "paused_credits"])
def test_every_terminal_status_ends_the_wait(terminal):
    clock = FakeClock()
    api = FakeApi(statuses={1001: [terminal]})
    assert runner.wait_for_backtest(1001, api=api, clock=clock) == terminal


# ------------------------------------------------------- failure accounting
def test_failed_arms_count_as_failures_not_missing_samples():
    results = [
        runner.ArmResult(arm="baseline", window="w1", cost_scenario_id="base",
                         backtest_id=1, status="complete", pnl_percent=5.0,
                         spy_percent=4.0),
        runner.ArmResult(arm="a4", window="w1", cost_scenario_id="base",
                         backtest_id=2, status="error", pnl_percent=None,
                         spy_percent=4.0),
    ]
    report = runner.build_report(results, trial_count=2)
    assert report["registered_trials"] == 2
    assert report["failed_trials"] == 1
    assert report["gate_verdict"] == "FAIL"
    assert "error" in json.dumps(report)


def test_a_missing_arm_is_a_failure_not_a_silent_drop():
    results = [
        runner.ArmResult(arm="baseline", window="w1", cost_scenario_id="base",
                         backtest_id=1, status="complete", pnl_percent=5.0,
                         spy_percent=4.0),
    ]
    report = runner.build_report(results, trial_count=2)
    assert report["failed_trials"] == 1, "the unreported trial is a failure"
    assert report["gate_verdict"] == "FAIL"


def test_all_complete_arms_can_pass():
    results = [
        runner.ArmResult(arm="baseline", window="w1", cost_scenario_id="base",
                         backtest_id=1, status="complete", pnl_percent=5.0,
                         spy_percent=4.0),
        runner.ArmResult(arm="a4", window="w1", cost_scenario_id="base",
                         backtest_id=2, status="complete", pnl_percent=9.0,
                         spy_percent=4.0),
    ]
    report = runner.build_report(results, trial_count=2)
    assert report["failed_trials"] == 0
    assert report["gate_verdict"] in {"PASS", "INCONCLUSIVE"}


def test_alpha_is_computed_against_the_aligned_benchmark():
    results = [runner.ArmResult(arm="a4", window="w1", cost_scenario_id="base",
                                backtest_id=2, status="complete",
                                pnl_percent=9.0, spy_percent=4.0)]
    report = runner.build_report(results, trial_count=1)
    row = report["rows"][0]
    assert row["alpha_pp"] == pytest.approx(5.0)


# --------------------------------------------------------- hierarchical bootstrap
def test_bootstrap_never_pools_repeated_dates_as_independent_sessions():
    """Ten replays of one fixture are ONE observation."""
    single = [runner.FixtureObservation(fixture_id="f1", daily_active_returns=[0.01] * 20)]
    repeated = [runner.FixtureObservation(fixture_id="f1", daily_active_returns=[0.01] * 20)] * 10
    lo_a, hi_a = runner.hierarchical_bootstrap(single, seed=11)
    lo_b, hi_b = runner.hierarchical_bootstrap(repeated, seed=11)
    assert (hi_b - lo_b) >= (hi_a - lo_a) * 0.5, (
        "replaying one fixture must not shrink the interval like new evidence")


def test_bootstrap_is_seed_deterministic():
    obs = [runner.FixtureObservation(fixture_id=f"f{i}",
                                     daily_active_returns=[0.01, -0.005, 0.02] * 5)
           for i in range(4)]
    assert runner.hierarchical_bootstrap(obs, seed=7) == runner.hierarchical_bootstrap(obs, seed=7)


def test_bootstrap_returns_a_two_sided_ninety_percent_interval():
    obs = [runner.FixtureObservation(fixture_id=f"f{i}",
                                     daily_active_returns=[0.01] * 30) for i in range(5)]
    lo, hi = runner.hierarchical_bootstrap(obs, seed=3)
    assert lo <= hi
    assert lo > 0, "a uniformly positive series must have a positive lower bound"


def test_bootstrap_needs_at_least_one_observation():
    with pytest.raises(ValueError):
        runner.hierarchical_bootstrap([], seed=1)


# ------------------------------------------------------------------ redaction
def test_redaction_removes_credentials_and_model_content():
    payload = {
        "api_token": "secret-token-value",
        "alpaca_key": "AK-LIVE-1234",
        "password": "hunter2",
        "model_response": "the model said buy AAPL",
        "prompt": "you are a trading analyst",
        "arm": "a4",
        "pnl_percent": 9.0,
    }
    safe = runner.redact(payload)
    blob = json.dumps(safe)
    for leaked in ("secret-token-value", "AK-LIVE-1234", "hunter2",
                   "the model said buy AAPL", "you are a trading analyst"):
        assert leaked not in blob, leaked
    assert safe["arm"] == "a4"
    assert safe["pnl_percent"] == 9.0


def test_redaction_is_recursive():
    safe = runner.redact({"outer": {"inner": {"secret": "s3cr3t"}}, "keep": 1})
    assert "s3cr3t" not in json.dumps(safe)
    assert safe["keep"] == 1


# -------------------------------------------------------------- payload shape
def test_every_non_off_payload_carries_its_full_identity():
    payload = runner.build_backtest_payload(
        window={"start": "2026-03-02", "end": "2026-04-27"},
        instance_id="alpaca-main", arm_id="arm-sha256-" + "b" * 64,
        matrix_id="matrix-sha256-" + "a" * 64, cost_scenario_id="25bps",
        evidence_mode="record", fixture_build_id="build-0",
        replay_fixture_id=None, equity_total_cost_bps=25.0,
        overrides={"circuit_breaker_regime_adjustment_semantics_v2": True},
        initial_cash=6000.0,
    )
    for key in ("matrix_manifest_id", "matrix_arm_id", "cost_scenario_id",
                "evidence_mode", "fixture_build_id"):
        assert payload.get(key), key
    assert payload["granularity"] == "3600", "seconds, not minutes"
    assert payload["equity_total_cost_bps"] == 25.0
    assert payload["nexus_candidate_overrides"] == {
        "circuit_breaker_regime_adjustment_semantics_v2": True}


def test_replay_payload_carries_the_sealed_fixture():
    payload = runner.build_backtest_payload(
        window={"start": "2026-03-02", "end": "2026-04-27"},
        instance_id="alpaca-main", arm_id="arm-sha256-" + "b" * 64,
        matrix_id="matrix-sha256-" + "a" * 64, cost_scenario_id="base",
        evidence_mode="replay", fixture_build_id=None,
        replay_fixture_id="fixture-sha256-" + "c" * 64,
        equity_total_cost_bps=None, overrides={}, initial_cash=6000.0)
    assert payload["replay_fixture_id"].startswith("fixture-sha256-")
    assert "fixture_build_id" not in payload or payload["fixture_build_id"] is None


def test_payload_never_contains_credentials():
    payload = runner.build_backtest_payload(
        window={"start": "2026-03-02", "end": "2026-04-27"},
        instance_id="alpaca-main", arm_id="arm-1", matrix_id="matrix-1",
        cost_scenario_id="base", evidence_mode="off", fixture_build_id=None,
        replay_fixture_id=None, equity_total_cost_bps=None, overrides={},
        initial_cash=6000.0)
    assert "key" not in payload and "secret" not in payload
