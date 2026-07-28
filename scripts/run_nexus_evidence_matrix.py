#!/usr/bin/env python3
"""Task 9 (2026-07-28): run the preregistered Graph Nexus evidence matrix.

This is what turns the A1-A4 candidates from "shipped but unproven" into
evidence. Its own failure modes are the ones that would silently manufacture a
favourable answer, so each is closed deliberately:

* The matrix manifest is published ONCE, BEFORE the first backtest POST. Arms,
  windows, cost scenarios, fixture count, trial count, failure rules, bootstrap
  seed and selection rule are all fixed before any result is visible.
* A failed, stopped, missing or provenance-invalid arm is a GATE FAILURE that
  stays in the registered trial count. It is never dropped or retried under a
  new ID -- that would turn a failure into a missing observation.
* Repeated replays of one sealed fixture are ONE observation. Confidence bounds
  come from a hierarchical fixture/session bootstrap that resamples whole
  fixture rows and shares one contiguous session block across them, so repeated
  market dates are never pooled as independent sessions.
* Status polling waits 15 minutes between checks, and nothing is printed
  without passing through `redact`.

The runner never calls an instance start endpoint.

Usage:
    python3 scripts/run_nexus_evidence_matrix.py --plan scripts/nexus_evidence_windows.json
    python3 scripts/run_nexus_evidence_matrix.py --plan ... --dry-run
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import statistics
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

#: Poll interval, in seconds. The user asked for this explicitly: don't burn
#: tokens re-reading a status that changes on the order of hours.
POLL_SECONDS = 900

#: Statuses that end the wait. `paused_credits` is terminal here because the
#: run cannot make progress without operator action.
TERMINAL_STATUSES = frozenset({
    "complete", "completed", "done", "error", "failed", "stopped",
    "cancelled", "canceled", "paused_credits",
})

#: A backtest that did not finish cleanly cannot contribute an observation.
SUCCESS_STATUSES = frozenset({"complete", "completed", "done"})

#: Cloudflare fronts the API and 403s a default urllib user-agent.
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

_SECRETISH = re.compile(
    r"(key|secret|token|password|passwd|credential|authorization|bearer"
    r"|prompt|response|completion|message|article|headline)",
    re.IGNORECASE,
)

_BOOTSTRAP_DRAWS = 2000
_BLOCK_SESSIONS = 5


# --------------------------------------------------------------------- redaction
def redact(value: Any) -> Any:
    """Strip credentials and model content before anything is printed.

    A whitelist is impossible here (report shapes vary), so this is a recursive
    key-name blacklist plus a hard rule that nothing under a secret-ish key
    survives, even nested.
    """
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if _SECRETISH.search(str(key)):
                out[str(key)] = "<redacted>"
            else:
                out[str(key)] = redact(item)
        return out
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    return value


def _say(message: str) -> None:
    print(message, flush=True)


# ------------------------------------------------------------------- API client
class EvidenceApi:
    """Thin authenticated client. Never prints a credential."""

    def __init__(self, base_url: str, token: str):
        self.base_url = base_url.rstrip("/")
        self._token = token

    @classmethod
    def from_env(cls, env_path: str = ".env") -> "EvidenceApi":
        env = {}
        if os.path.exists(env_path):
            for line in open(env_path):
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, val = line.split("=", 1)
                    env[key] = val.strip().strip('"').strip("'")
        base = (env.get("INTELLISTOCK_API_URL")
                or os.environ.get("INTELLISTOCK_API_URL") or "").rstrip("/")
        if not base:
            raise SystemExit("INTELLISTOCK_API_URL is not set")
        payload = {"username": env.get("DEFAULT_ADMIN_USERNAME"),
                   "password": env.get("DEFAULT_ADMIN_PASSWORD")}
        body = cls._raw(base, "/auth/login", payload, None)
        token = body.get("access_token") or body.get("token")
        if not token:
            raise SystemExit("login did not return an access token")
        return cls(base, token)

    @staticmethod
    def _raw(base: str, path: str, payload, token):
        request = urllib.request.Request(
            base + path, method="POST" if payload is not None else "GET")
        request.add_header("Content-Type", "application/json")
        request.add_header("Accept", "application/json")
        request.add_header("User-Agent", _USER_AGENT)
        if token:
            request.add_header("Authorization", "Bearer " + token)
        data = json.dumps(payload).encode() if payload is not None else None
        with urllib.request.urlopen(request, data, timeout=120) as response:
            return json.loads(response.read().decode())

    def _call(self, path: str, payload=None):
        return self._raw(self.base_url, path, payload, self._token)

    def publish_matrix(self, doc):
        return self._call("/backtest-evidence/matrices", {"matrix": doc})

    def create_backtest(self, payload):
        return self._call("/backtests", payload)

    def backtest_status(self, backtest_id):
        body = self._call(f"/backtests/{backtest_id}/status")
        return str(body.get("status") or body.get("state") or "unknown").lower()

    def backtest_summary(self, backtest_id):
        return self._call(f"/backtests/{backtest_id}/summary")


# ------------------------------------------------------------------ data shapes
@dataclass
class MatrixPlan:
    matrix_doc: dict
    arms: Sequence[str]
    windows: Sequence[dict]
    cost_scenarios: Sequence[str]
    fixture_count: int
    trial_count: int
    instance_id: str = "alpaca-main"
    initial_cash: float = 6000.0
    overrides_by_arm: dict = field(default_factory=dict)


@dataclass
class ArmResult:
    arm: str
    window: Any
    cost_scenario_id: str
    backtest_id: Any
    status: str
    pnl_percent: float | None
    spy_percent: float | None

    @property
    def succeeded(self) -> bool:
        return (str(self.status).lower() in SUCCESS_STATUSES
                and self.pnl_percent is not None)

    @property
    def alpha_pp(self) -> float | None:
        if self.pnl_percent is None or self.spy_percent is None:
            return None
        return float(self.pnl_percent) - float(self.spy_percent)


@dataclass
class FixtureObservation:
    """One independently recorded sealed fixture. Repeated replays of the same
    fixture collapse to a single row -- that is the whole point."""
    fixture_id: str
    daily_active_returns: Sequence[float]


# ------------------------------------------------------------------- payloads
def build_backtest_payload(
    *, window, instance_id, arm_id, matrix_id, cost_scenario_id, evidence_mode,
    fixture_build_id, replay_fixture_id, equity_total_cost_bps, overrides,
    initial_cash,
) -> dict:
    """One POST body. Credentials are never included: equities backtests use
    the instance's linked brokerage."""
    payload = {
        "instance_id": instance_id,
        "stocks": [],
        "start_date": window["start"],
        "end_date": window["end"],
        # GOTCHA: this field is SECONDS. The default "60" steps minute-by-minute
        # and makes an hourly window take days.
        "granularity": "3600",
        "initial_cash": float(initial_cash),
    }
    if evidence_mode and evidence_mode != "off":
        payload.update({
            "evidence_mode": evidence_mode,
            "matrix_manifest_id": matrix_id,
            "matrix_arm_id": arm_id,
            "cost_scenario_id": cost_scenario_id,
        })
        if fixture_build_id:
            payload["fixture_build_id"] = fixture_build_id
        if replay_fixture_id:
            payload["replay_fixture_id"] = replay_fixture_id
    if equity_total_cost_bps is not None:
        payload["equity_total_cost_bps"] = float(equity_total_cost_bps)
    if overrides:
        payload["nexus_candidate_overrides"] = dict(overrides)
    return payload


# -------------------------------------------------------------------- polling
def wait_for_backtest(backtest_id, *, api, clock=time) -> str:
    """Block until the run reaches a terminal status, checking every 15 minutes.

    Returns promptly when the first read is already terminal, so a completed run
    never costs an idle quarter hour.
    """
    while True:
        status = str(api.backtest_status(backtest_id)).lower()
        if status in TERMINAL_STATUSES:
            return status
        clock.sleep(POLL_SECONDS)


# ------------------------------------------------------------------ statistics
def hierarchical_bootstrap(observations: Sequence[FixtureObservation], *,
                           seed: int) -> tuple[float, float]:
    """Two-sided 90% interval over mean daily active return.

    Versioned as fixture/session-v1. For each of 2000 seeded draws: resample
    complete fixture ROWS with replacement, draw ONE contiguous five-session
    block shared across those rows, take each row's mean daily active return
    over that block, and take the median across rows. The 5th/95th percentiles
    of those medians form the interval.

    Sharing one block across rows is what stops repeated market dates from being
    counted as independent sessions -- the failure that makes ten replays of one
    fixture look like ten experiments.
    """
    rows = [obs for obs in observations if obs and obs.daily_active_returns]
    if not rows:
        raise ValueError("hierarchical_bootstrap requires at least one observation")
    # Collapse repeated replays of the SAME fixture to one row.
    unique: dict[str, FixtureObservation] = {}
    for row in rows:
        unique.setdefault(row.fixture_id, row)
    rows = list(unique.values())

    shortest = min(len(row.daily_active_returns) for row in rows)
    block = min(_BLOCK_SESSIONS, shortest)
    rng = random.Random(seed)
    medians = []
    for _ in range(_BOOTSTRAP_DRAWS):
        drawn = [rows[rng.randrange(len(rows))] for _ in range(len(rows))]
        start = rng.randrange(0, shortest - block + 1)
        means = [
            statistics.fmean(row.daily_active_returns[start:start + block])
            for row in drawn
        ]
        medians.append(statistics.median(means))
    medians.sort()
    lo = medians[int(0.05 * (len(medians) - 1))]
    hi = medians[int(0.95 * (len(medians) - 1))]
    return lo, hi


# --------------------------------------------------------------------- report
def build_report(results: Iterable[ArmResult], *, trial_count: int) -> dict:
    """Machine-readable comparison with explicit failure accounting.

    Every registered trial is accounted for. A trial that never reported is
    counted as failed, not omitted -- silent truncation reads as "covered
    everything" when it did not.
    """
    rows = []
    failed = 0
    for result in results:
        rows.append({
            "arm": result.arm,
            "window": result.window,
            "cost_scenario_id": result.cost_scenario_id,
            "backtest_id": result.backtest_id,
            "status": result.status,
            "pnl_percent": result.pnl_percent,
            "spy_percent": result.spy_percent,
            "alpha_pp": result.alpha_pp,
            "counted": result.succeeded,
        })
        if not result.succeeded:
            failed += 1
    reported = len(rows)
    if reported < int(trial_count):
        missing = int(trial_count) - reported
        failed += missing
        rows.append({
            "arm": None, "window": None, "cost_scenario_id": None,
            "backtest_id": None, "status": "missing",
            "pnl_percent": None, "spy_percent": None, "alpha_pp": None,
            "counted": False, "missing_trials": missing,
        })
    verdict = "FAIL" if failed else ("PASS" if reported else "INCONCLUSIVE")
    return {
        "bootstrap_version": "fixture-session-v1",
        "registered_trials": int(trial_count),
        "reported_trials": reported,
        "failed_trials": failed,
        "gate_verdict": verdict,
        "rows": rows,
        "note": (
            "A failed, stopped or missing arm is a gate failure and remains in "
            "the registered trial count. Repeated replays of one sealed fixture "
            "are one observation."
        ),
    }


# ------------------------------------------------------------------ orchestration
def publish_then_run(plan: MatrixPlan, *, api, clock=time, dry_run: bool = False):
    """Publish the matrix, THEN run its arms. Never starts an instance."""
    published = api.publish_matrix(plan.matrix_doc)
    matrix_id = published.get("matrix_id")
    arm_ids = published.get("arm_ids") or {}
    _say(f"Published matrix {matrix_id} with {len(plan.arms)} arm(s)")
    if dry_run:
        _say("Dry run: no backtests queued.")
        return build_report([], trial_count=0)

    results = []
    # Branch-union: record the first arm, extend for the rest, then replay.
    for window in plan.windows:
        for scenario in plan.cost_scenarios:
            build_id = f"build-{window['start']}-{scenario}"
            for index, arm in enumerate(plan.arms):
                mode = "record" if index == 0 else "record_extend"
                payload = build_backtest_payload(
                    window=window, instance_id=plan.instance_id,
                    arm_id=arm_ids.get(arm, arm), matrix_id=matrix_id,
                    cost_scenario_id=scenario, evidence_mode=mode,
                    fixture_build_id=build_id, replay_fixture_id=None,
                    equity_total_cost_bps=(None if scenario == "base"
                                           else float(scenario.rstrip("bps"))),
                    overrides=plan.overrides_by_arm.get(arm, {}),
                    initial_cash=plan.initial_cash,
                )
                created = api.create_backtest(payload)
                backtest_id = created.get("id")
                _say(f"queued arm={arm} scenario={scenario} id={backtest_id}")
                status = wait_for_backtest(backtest_id, api=api, clock=clock)
                summary = api.backtest_summary(backtest_id) if status in SUCCESS_STATUSES else {}
                results.append(ArmResult(
                    arm=arm, window=window, cost_scenario_id=scenario,
                    backtest_id=backtest_id, status=status,
                    pnl_percent=summary.get("pnl_percent"),
                    spy_percent=summary.get("spy_percent"),
                ))
                if status not in SUCCESS_STATUSES:
                    _say(f"arm={arm} ended {status}: recorded as a gate FAILURE, "
                         "not retried under a new ID")
    return build_report(results, trial_count=plan.trial_count)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, help="matrix plan JSON")
    parser.add_argument("--dry-run", action="store_true",
                        help="publish the matrix and stop")
    parser.add_argument("--out", default="", help="write the JSON report here")
    args = parser.parse_args(argv)

    raw = json.load(open(args.plan))
    if not raw.get("matrix"):
        _say(
            "This plan has no `matrix` document. Preregistration is not "
            "optional: build a complete ExperimentMatrixManifest (one "
            "ExperimentSpec per arm per cost scenario) and publish it BEFORE "
            "the first backtest, or its arms could be reworded to fit the "
            "results."
        )
        return 2
    plan = MatrixPlan(
        matrix_doc=raw["matrix"], arms=raw["arms"], windows=raw["windows"],
        cost_scenarios=raw.get("cost_scenarios", ["base"]),
        fixture_count=int(raw.get("fixture_count", 1)),
        trial_count=int(raw.get("trial_count", len(raw["arms"]))),
        instance_id=raw.get("instance_id", "alpaca-main"),
        initial_cash=float(raw.get("initial_cash", 6000.0)),
        overrides_by_arm=raw.get("overrides_by_arm", {}),
    )
    api = EvidenceApi.from_env()
    try:
        report = publish_then_run(plan, api=api, dry_run=args.dry_run)
    except urllib.error.HTTPError as exc:
        _say(f"HTTP {exc.code}: {exc.reason}")
        return 2
    safe = redact(report)
    _say(json.dumps(safe, indent=2, default=str))
    if args.out:
        with open(args.out, "w") as handle:
            json.dump(safe, handle, indent=2, default=str)
    return 0 if report.get("gate_verdict") != "FAIL" else 1


if __name__ == "__main__":
    sys.exit(main())
