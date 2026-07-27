"""Independent-review regressions for immutable research promotion evidence."""

from __future__ import annotations

import ast
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest


BACKEND_ROOT = Path(__file__).resolve().parents[1]
CREATED_AT = "2026-07-27T06:30:00+00:00"


def _manifest(name: str, digit: str) -> dict:
    return {
        "manifest_id": name,
        "source_hashes": {"primary": f"sha256-{digit * 64}"},
        "created_at": CREATED_AT,
    }


def _spy_values() -> dict:
    return {
        "2026-07-08T20:00:00Z": 500.0,
        "2026-07-09T20:00:00Z": 502.0,
    }


def _spy_manifest(values=None) -> dict:
    from backtest_summary import canonical_spy_content_hash

    values = values or _spy_values()
    return {
        "manifest_id": "spy-adjusted-2026-07-08--2026-07-09",
        "symbol": "SPY",
        "timeframe": "1Day",
        "adjustment": "all",
        "price_field": "c",
        "total_return": True,
        "feed": "iex",
        "start_date": "2026-07-08",
        "end_date": "2026-07-09",
        "valuation_rule": "xnys_session_close",
        "valuation_timestamps": sorted(values),
        "content_hash": canonical_spy_content_hash(values),
    }


def _spec_fields(**overrides) -> dict:
    fields = {
        "experiment_id": "attempt-review-0001",
        "parent_experiment_id": None,
        "search_scope": "graph-fresh-direct/live-40",
        "commit_sha": "b25e2f1",
        "source_tree_hash": "sha256-" + "a" * 64,
        "effective_config": {
            "strategy_name": "equity-alpha",
            "strategies": [{"strategy": "graph_nexus_analysis"}],
        },
        "model_provider": "openai",
        "model_name": "research-model",
        "prompt_hashes": {"decision": "sha256-" + "b" * 64},
        "model_settings": {"temperature": 0.0},
        "seed": 179,
        "predeclared_repeats": 1,
        "dataset_manifest": _manifest("dataset-1", "1"),
        "graph_manifest": _manifest("graph-1", "2"),
        "universe_manifest": _manifest("universe-1", "3"),
        "benchmark_manifest": _spy_manifest(),
        "execution_cost_model": {
            "version": "cost-v1",
            "spread_bps": 1.0,
            "slippage_bps": 2.0,
            "fee_bps": 0.0,
            "latency_seconds": 0.25,
        },
        "start_date": "2026-07-08",
        "end_date": "2026-07-09",
        "fold": "walk-forward-03",
        "actor": "research-orchestrator",
    }
    fields.update(overrides)
    return fields


@pytest.mark.parametrize(
    ("field", "value", "missing_name"),
    [
        (
            "dataset_manifest",
            {"manifest_id": "dataset-1"},
            "source_hashes",
        ),
        (
            "graph_manifest",
            {"manifest_id": "graph-1", "source_hashes": {"graph": "hash"}},
            "created_at",
        ),
        (
            "universe_manifest",
            {"source_hashes": {"universe": "hash"}, "created_at": CREATED_AT},
            "manifest_id",
        ),
        (
            "execution_cost_model",
            {"version": "cost-v1", "fee_bps": 0.0},
            "spread_bps",
        ),
    ],
)
def test_experiment_spec_rejects_partial_provenance_schemas(
    field, value, missing_name
):
    from experiment_registry import ExperimentSpec

    with pytest.raises(ValueError, match=missing_name):
        ExperimentSpec(**_spec_fields(**{field: value}))


def test_experiment_fingerprint_preserves_large_integer_seed_identity():
    from experiment_registry import ExperimentSpec

    first = ExperimentSpec(**_spec_fields(seed=2**53))
    second = ExperimentSpec(
        **_spec_fields(
            experiment_id="attempt-review-0002",
            seed=2**53 + 1,
        )
    )

    assert first.fingerprint != second.fingerprint
    assert first.to_doc()["seed"] == 2**53
    assert second.to_doc()["seed"] == 2**53 + 1


def test_alignment_refuses_same_date_at_different_valuation_times():
    from benchmark_alpha.metrics import (
        IncompleteBenchmarkError,
        align_return_series,
    )

    with pytest.raises(IncompleteBenchmarkError, match="coverage is incomplete"):
        align_return_series(
            {
                "2026-07-08T20:00:00Z": 100.0,
                "2026-07-09T20:00:00Z": 101.0,
            },
            {
                "2026-07-08T04:00:00Z": 500.0,
                "2026-07-09T04:00:00Z": 501.0,
            },
        )


def test_spy_bars_are_rekeyed_to_authoritative_session_closes():
    from backtest_summary import build_adjusted_spy_close_series

    closes = {
        "2026-07-08": datetime(2026, 7, 8, 20, tzinfo=timezone.utc),
        "2026-07-09": datetime(2026, 7, 9, 20, tzinfo=timezone.utc),
    }
    values = build_adjusted_spy_close_series(
        [
            {"t": "2026-07-08T04:00:00Z", "c": 500.0},
            {"t": "2026-07-09T04:00:00Z", "c": 502.0},
        ],
        start_date="2026-07-08",
        end_date="2026-07-09",
        session_close_resolver=lambda day: closes[day.isoformat()],
    )

    assert [stamp.isoformat() for stamp in values] == [
        "2026-07-08T20:00:00+00:00",
        "2026-07-09T20:00:00+00:00",
    ]


def test_benchmark_hash_covers_exact_timestamp_and_adjusted_close():
    from backtest_summary import canonical_spy_content_hash

    baseline = canonical_spy_content_hash(_spy_values())
    changed_time = canonical_spy_content_hash(
        {
            "2026-07-08T19:59:00Z": 500.0,
            "2026-07-09T20:00:00Z": 502.0,
        }
    )
    changed_close = canonical_spy_content_hash(
        {
            "2026-07-08T20:00:00Z": 500.0,
            "2026-07-09T20:00:00Z": 502.01,
        }
    )

    assert baseline.startswith("spy-sha256-")
    assert len({baseline, changed_time, changed_close}) == 3


def test_summary_uses_only_exact_registered_session_close_snapshots():
    from backtest_summary import compute_backtest_summary

    snapshots = [
        {"timestamp": "2026-07-08T14:30:00Z", "value": 10.0},
        {"timestamp": "2026-07-08T20:00:00Z", "value": 100.0},
        {"timestamp": "2026-07-08T21:00:00Z", "value": 999.0},
        {"timestamp": "2026-07-09T14:30:00Z", "value": 11.0},
        {"timestamp": "2026-07-09T20:00:00Z", "value": 102.0},
        {"timestamp": "2026-07-09T21:00:00Z", "value": 1000.0},
    ]
    summary = compute_backtest_summary(
        None,
        snapshots,
        100.0,
        benchmark_values=_spy_values(),
        benchmark_manifest=_spy_manifest(),
        trials=3,
    )

    assert summary["benchmark_complete"] is True
    assert summary["active_return"] == pytest.approx(
        (102.0 / 100.0 - 1.0) - (502.0 / 500.0 - 1.0)
    )


def test_summary_rejects_manifest_whose_content_hash_does_not_match_values():
    from backtest_summary import compute_backtest_summary

    summary = compute_backtest_summary(
        None,
        [
            {"timestamp": "2026-07-08T20:00:00Z", "value": 100.0},
            {"timestamp": "2026-07-09T20:00:00Z", "value": 102.0},
        ],
        100.0,
        benchmark_values=_spy_values(),
        benchmark_manifest={
            **_spy_manifest(),
            "content_hash": "spy-sha256-" + "0" * 64,
        },
        trials=3,
    )

    assert summary["benchmark_complete"] is False
    assert "content_hash" in summary["benchmark_incomplete_reason"]


def _extract_broker_functions(*names):
    path = BACKEND_ROOT / "broker.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    nodes = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in names
    ]
    found = {node.name for node in nodes}
    assert found == set(names), f"missing broker helpers: {set(names) - found}"
    namespace = {}
    exec(
        compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"),
        namespace,
    )
    return tuple(namespace[name] for name in names)


def test_spy_gate_requires_registered_promotable_equity_and_allows_no_non_equity():
    (uses_equity_benchmark,) = _extract_broker_functions(
        "_backtest_uses_equity_benchmark"
    )
    registered = SimpleNamespace(
        experiment_id="attempt-review-0001",
        spec=SimpleNamespace(benchmark_manifest=_spy_manifest()),
    )

    assert uses_equity_benchmark(
        {"name": "equity-alpha"},
        is_non_equity_runtime=False,
        registered_experiment=None,
    ) is False
    assert uses_equity_benchmark(
        {"name": "equity-alpha"},
        is_non_equity_runtime=True,
        registered_experiment=registered,
    ) is False
    assert uses_equity_benchmark(
        {"name": "equity-alpha"},
        is_non_equity_runtime=False,
        registered_experiment=registered,
    ) is True


@pytest.mark.parametrize(
    ("kind", "expected"),
    [("crypto", True), ("kalshi", True), ("equity", False), (None, False)],
)
def test_non_equity_runtime_classifier_excludes_crypto_and_kalshi(kind, expected):
    (classifier,) = _extract_broker_functions(
        "_is_non_equity_instance_runtime"
    )
    classifier.__globals__["_instance_kind_and_crypto_config"] = (
        lambda: (kind, {})
    )
    assert classifier() is expected


def test_backtest_preregistration_derives_scope_from_the_stored_registration():
    from backtest_experiments import preregister_backtest_experiment
    from experiment_registry import ExperimentRegistry, InMemoryExperimentStore

    events = []

    class RecordingStore(InMemoryExperimentStore):
        def insert_experiment_registration(self, registration):
            events.append(("registered", registration.experiment_id))
            return super().insert_experiment_registration(registration)

    registry = ExperimentRegistry(store=RecordingStore())
    declaration = _spec_fields(
        effective_config={"untrusted": "must be replaced"},
        seed=999,
    )
    context = preregister_backtest_experiment(
        declaration,
        effective_config={"runtime": "resolved-before-model"},
        seed=179,
        start_date="2026-07-08",
        end_date="2026-07-09",
        registry=registry,
    )
    events.append(("model", context.experiment_id))

    assert events == [
        ("registered", "attempt-review-0001"),
        ("model", "attempt-review-0001"),
    ]
    assert context.fingerprint == context.registration.fingerprint
    assert context.registration.spec.effective_config["runtime"] == (
        "resolved-before-model"
    )
    assert context.registration.spec.seed == 179
    assert context.trial_count() == 1
    assert context.registration.search_scope == (
        "graph-fresh-direct/live-40"
    )


def test_production_research_cli_registers_immutable_specs_before_execution(
    tmp_path
):
    from experiment_registry import ExperimentRegistry
    from scripts import run_alpha_research

    spec_path = tmp_path / "registered-experiments.json"
    spec_path.write_text(
        json.dumps([_spec_fields()]),
        encoding="utf-8",
    )
    registry = ExperimentRegistry()

    rc = run_alpha_research.main(
        ["--spec-file", str(spec_path), "--register-only"],
        registry=registry,
    )

    assert rc == 0
    assert registry.trial_count(
        scope="graph-fresh-direct/live-40"
    ) == 1


def test_broker_preregisters_before_any_strategy_model_execution():
    source = (BACKEND_ROOT / "broker.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    class ModuleCalls(ast.NodeVisitor):
        def __init__(self):
            self.calls = []

        def visit_FunctionDef(self, node):
            return None

        visit_AsyncFunctionDef = visit_FunctionDef
        visit_ClassDef = visit_FunctionDef

        def visit_Call(self, node):
            if isinstance(node.func, ast.Name):
                self.calls.append(node)
            self.generic_visit(node)

    visitor = ModuleCalls()
    visitor.visit(tree)
    preregistration_calls = [
        node
        for node in visitor.calls
        if node.func.id == "_preregister_backtest_experiment"
    ]
    model_calls = [
        node
        for node in visitor.calls
        if node.func.id == "run_run_once_strategies"
    ]

    assert preregistration_calls
    assert model_calls
    assert min(node.lineno for node in preregistration_calls) < min(
        node.lineno for node in model_calls
    )

    result_dicts = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Dict)
        and {
            key.value
            for key in node.keys
            if isinstance(key, ast.Constant) and isinstance(key.value, str)
        } >= {"experiment_id", "experiment_fingerprint"}
    ]
    assert len(result_dicts) >= 2
    assert "_backtest_experiment_context.experiment_id" in source
    assert "_backtest_experiment_context.fingerprint" in source
