"""Durable immutable preregistration for production alpha research.

The input file contains complete ``ExperimentSpec`` documents. Every document
is validated and persisted to the append-only AlphaExperiments ledger before
the caller may start model execution. This runner does not fabricate or
mutate research results.

Usage:
    python3 -m scripts.run_alpha_research --spec-file specs.json --list
    python3 -m scripts.run_alpha_research --spec-file specs.json --register-only
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sys

_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

from benchmark_alpha.rethink_store import AlphaRethinkStore
from experiment_registry import ExperimentRegistry, ExperimentSpec


def _load_specs(path) -> tuple[ExperimentSpec, ...]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read experiment spec file: {exc}") from exc
    rows = payload if isinstance(payload, list) else [payload]
    if not rows:
        raise ValueError("experiment spec file must contain at least one spec")
    specs = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"experiment spec at index {index} must be an object")
        specs.append(ExperimentSpec.from_doc(row))
    ids = [spec.experiment_id for spec in specs]
    if len(ids) != len(set(ids)):
        raise ValueError("experiment spec file contains duplicate experiment_id values")
    return tuple(specs)


def _build_production_registry() -> ExperimentRegistry:
    # R26: the store pools its own connection per operation, so the registry
    # needs neither a driver handle nor a connection factory.
    return ExperimentRegistry(store=AlphaRethinkStore())


def _register_all_before_execution(registry, specs):
    registrations = []
    for spec in specs:
        registrations.append(registry.register_before_run(spec))
    return tuple(registrations)


def main(argv=None, *, registry=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--spec-file", required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--list", action="store_true")
    mode.add_argument("--register-only", action="store_true")
    args = parser.parse_args(argv)

    try:
        specs = _load_specs(args.spec_file)
    except ValueError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2

    if args.list:
        for spec in specs:
            print(
                spec.experiment_id,
                spec.search_scope,
                spec.fingerprint,
            )
        print(f"{len(specs)} immutable experiment attempts declared")
        return 0

    try:
        active_registry = registry or _build_production_registry()
        registrations = _register_all_before_execution(
            active_registry,
            specs,
        )
    except Exception as exc:
        print(
            "REFUSED: durable preregistration failed "
            f"({type(exc).__name__}: {exc})",
            file=sys.stderr,
        )
        return 2

    for registration in registrations:
        print(
            registration.experiment_id,
            registration.search_scope,
            registration.fingerprint,
        )
    print(
        f"durably registered {len(registrations)} experiment attempts "
        "before model execution"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
