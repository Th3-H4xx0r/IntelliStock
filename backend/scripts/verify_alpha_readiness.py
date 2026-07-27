#!/usr/bin/env python3
"""Read-only evaluator for immutable alpha promotion evidence."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from benchmark_alpha.promotion import (
    PromotionEvidence,
    evaluate_promotion,
    readiness_report_from_promotion,
)
from live_readiness import ReadinessState


_STATE_RANK = {
    ReadinessState.RESEARCH: 0,
    ReadinessState.PAPER_ELIGIBLE: 1,
    ReadinessState.CANARY_ELIGIBLE: 2,
    ReadinessState.LIVE_ELIGIBLE: 3,
}


def evaluate_evidence_mapping(payload: dict) -> dict:
    evidence = PromotionEvidence(**payload)
    decision = evaluate_promotion(evidence)
    report = readiness_report_from_promotion(evidence, decision)
    return {
        "instance_id": evidence.instance_id,
        "eligible_state": decision.eligible_state.value,
        "passed": decision.passed,
        "reasons": list(decision.reasons),
        "evidence_hash": decision.evidence_hash,
        "decision_hash": decision.decision_hash,
        "artifact_hash": evidence.artifact_hash,
        "checks": [asdict(check) for check in report.checks],
    }


def _human_table(result: dict) -> str:
    rows = [
        "READINESS CHECK                     RESULT",
        "-----------------------------------  ------",
    ]
    for check in result["checks"]:
        result_text = "PASS" if check["passed"] else f"FAIL ({check['reason']})"
        rows.append(f"{check['name']:<35}  {result_text}")
    rows.append(
        f"eligible_state={result['eligible_state']} "
        f"promotion_passed={str(result['passed']).lower()}"
    )
    return "\n".join(rows)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--evidence",
        required=True,
        help="Path to a JSON PromotionEvidence object",
    )
    parser.add_argument(
        "--requested-state",
        choices=tuple(state.value for state in _STATE_RANK),
        default=ReadinessState.LIVE_ELIGIBLE.value,
    )
    args = parser.parse_args(argv)
    try:
        payload = json.loads(Path(args.evidence).read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("evidence JSON must be an object")
        result = evaluate_evidence_mapping(payload)
    except Exception as exc:
        print(
            json.dumps(
                {
                    "passed": False,
                    "eligible_state": ReadinessState.RESEARCH.value,
                    "reasons": [f"invalid_evidence:{type(exc).__name__}"],
                },
                sort_keys=True,
            )
        )
        print(
            f"readiness evidence invalid ({type(exc).__name__})",
            file=sys.stderr,
        )
        return 2
    print(json.dumps(result, sort_keys=True))
    print(_human_table(result), file=sys.stderr)
    requested = ReadinessState(args.requested_state)
    eligible = ReadinessState(result["eligible_state"])
    if _STATE_RANK[eligible] < _STATE_RANK[requested]:
        return 1
    if requested is ReadinessState.LIVE_ELIGIBLE and not result["passed"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
