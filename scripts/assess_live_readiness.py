#!/usr/bin/env python3
"""Measure how far doc-193 / alpaca-main is from a real-money promotion.

This does not promote anything and writes nothing. It builds a PromotionEvidence
from what we ACTUALLY have today, hands it to the repo's own
`evaluate_promotion`, and prints the gate's own reasons for refusing. The point
is to replace an argument about readiness with the gate's verdict.

Evidence sources, all measured this session unless marked ASSUMED:
  * point_in_time_months = 0
        docs/runbooks/point-in-time-capture.md records 0 manifest rows, and
        interactive_utils.py:5607 forces pit_mode='research' on every equities
        backtest, so nothing measured so far is PIT-verified.
  * paper_trading_days = 0        - no paper run has been started.
  * regime_count = 2              - bull/chop (bt 820236) and bear (bt 342380).
  * unseen_months = 0             - every window so far overlaps the tuning set.
  * sealed_holdout_*              - none pre-registered.
  * chaos / rehearsal / audit fields default to NOT DONE. They are marked
    ASSUMED-FALSE rather than guessed True; if any has in fact been rehearsed,
    set it here and re-run.

Run:  python3 scripts/assess_live_readiness.py
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "backend"))

from benchmark_alpha.promotion import (  # noqa: E402
    PromotionEvidence,
    evaluate_promotion,
    readiness_report_from_promotion,
)

# Hashes are placeholders: nothing is artifact-bound yet, and the gate must
# refuse on that alone.
_H = "0" * 64

EVIDENCE = PromotionEvidence(
    instance_id="alpaca-main",
    artifact_hash=_H, deployed_artifact_hash=_H, paper_artifact_hash=_H,
    config_hash=_H, observed_config_hash=_H, paper_config_hash=_H,
    model_hash=_H, observed_model_hash=_H,
    data_manifest_hash=_H, observed_data_manifest_hash=_H,
    cost_model_hash=_H, risk_policy_hash=_H, broker_adapter_hash=_H,
    # --- measured ---
    point_in_time_months=0,          # zero PIT manifests exist
    unseen_months=0,                 # every window overlaps the tuning set
    regime_count=2,                  # bull/chop + bear
    purged_fold_count=0,
    sealed_holdout_preregistered=False,
    sealed_holdout_evaluations=0,
    aligned_spy_total_return=True,   # SPY total return is on the result row
    costed_fills=True,               # equity-measured-v3-nbbo23 model
    registered_trial_count=0,
    repeats_predeclared=False,
    median_repeat_used=False,
    median_annual_active_pp=0.0,
    target_annual_active_pp=100.0,   # the objective's 1x
    bootstrap_active_low=0.0,
    information_ratio=0.0,
    deflated_sharpe_probability=0.0,
    max_drawdown_magnitude=0.155,    # bt 820236 peak 6974.29 -> low 5869.45
    beta=0.0,
    profit_factor_after_costs=0.0,
    positive_unseen_quarter_fraction=0.0,
    parameter_stability_passed=False,
    leave_one_winner_out_passed=False,
    concentration_analysis_passed=False,
    ci_passed=True,                  # 4,537 passed / 13 skipped
    lifecycle_chaos_passed=False,
    dependency_chaos_passed=False,
    restart_state_passed=False,
    secret_migration_rehearsed=False,
    rollback_rehearsed=False,
    plaintext_credentials_found=0,
    unresolved_high_critical_findings=0,
    unresolved_ownership=0,
    watchdog_healthy=False,
    watchdog_age_seconds=1e9,
    degraded_audit_intervals=0,
    exact_build_paper=False,
    paper_trading_days=0,
    point_in_time_provenance_verified=False,
)


def main() -> int:
    decision = evaluate_promotion(EVIDENCE)
    report = readiness_report_from_promotion(EVIDENCE, decision)

    print("=" * 72)
    print("LIVE READINESS - alpaca-main (real money)")
    print("=" * 72)
    print(f"state          : {report.state.value}")
    print(f"artifact bound : {report.artifact_hash or '(none)'}")
    print("")

    failed = [c for c in report.checks if not c.passed]
    passed = [c for c in report.checks if c.passed]
    print(f"checks passed  : {len(passed)}/{len(report.checks)}")
    print("")
    if failed:
        print("BLOCKING:")
        for c in failed:
            print(f"  [FAIL] {c.name}")
            if c.reason:
                for line in str(c.reason).splitlines():
                    print(f"         {line}")
    if passed:
        print("")
        print("PASSING:")
        for c in passed:
            print(f"  [ok]   {c.name}")

    print("")
    print("-" * 72)
    print("Nothing was written. This script never promotes.")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
