"""Deterministic registered experiment runner (Task 16).

Registers the COMPLETE experiment matrix before any result exists — SPY
control, deterministic challenger, Graph fresh-direct, fresh-direct plus
each legacy lane as separately-registered ablations — across horizons
1/3/5 and active ceilings 40/60/80. It refuses to run without a frozen
point-in-time data manifest; it never fabricates results.

Usage:
    python3 -m scripts.run_alpha_research --list          # print the matrix
    python3 -m scripts.run_alpha_research --register-only
"""
import argparse
import os
import sys

_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

from benchmark_alpha.research import ExperimentRegistry, ExperimentSpec

MODEL_FAMILIES = (
    "spy_control",
    "deterministic_challenger",
    "graph_fresh_direct",
    "graph_fresh_direct_plus_backfill",
    "graph_fresh_direct_plus_propagation",
    "graph_scheduled_vote_lane",
    "graph_breakout_override_lane",
    "graph_reserved_slot_lane",
    "graph_high_conviction_lane",
)
ACTIVE_CEILINGS = (0.40, 0.60, 0.80)


def build_matrix(*, data_snapshot_id, code_hash, model_hash,
                 cost_model_version):
    specs = []
    for family in MODEL_FAMILIES:
        for ceiling in ACTIVE_CEILINGS:
            specs.append(ExperimentSpec(
                model_family=family, horizons=(1, 3, 5),
                active_ceiling=ceiling, train_months=12,
                calibration_months=3, test_months=3, embargo_days=5,
                data_snapshot_id=data_snapshot_id, code_hash=code_hash,
                model_hash=model_hash, cost_model_version=cost_model_version))
    return specs


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--register-only", action="store_true")
    parser.add_argument("--data-snapshot-id", default=None)
    parser.add_argument("--code-hash", default=None)
    parser.add_argument("--model-hash", default="unversioned")
    parser.add_argument("--cost-model-version", default="cm-1")
    args = parser.parse_args(argv)

    if not args.list and not (args.data_snapshot_id and args.code_hash):
        print("REFUSED: a frozen point-in-time --data-snapshot-id and "
              "--code-hash are required — current/mutable data cannot be "
              "registered (B02).", file=sys.stderr)
        return 2

    specs = build_matrix(
        data_snapshot_id=args.data_snapshot_id or "<unregistered>",
        code_hash=args.code_hash or "<unregistered>",
        model_hash=args.model_hash, cost_model_version=args.cost_model_version)

    if args.list:
        for spec in specs:
            print(f"{spec.model_family:44s} ceiling={spec.active_ceiling:.2f}")
        print(f"{len(specs)} experiments in the registered matrix")
        return 0

    registry = ExperimentRegistry()
    for spec in specs:
        print(registry.register(spec), spec.model_family, spec.active_ceiling)
    print(f"registered {registry.trial_count()} experiments "
          "(execution consumes the frozen manifest pipeline; results are "
          "never fabricated by this runner)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
