"""Immutable experiment-attempt provenance and trial accounting."""
from datetime import datetime, timezone

import pytest

from experiment_registry import (
    DuplicateExperimentError,
    ExperimentAlreadyTerminalError,
    ExperimentRegistry,
    ExperimentSpec,
)
from benchmark_alpha.rethink_store import (
    EXPERIMENTS_TABLE,
    AlphaRethinkStore,
)


REGISTERED_AT = datetime(2026, 7, 27, 6, 30, tzinfo=timezone.utc)


def _source_manifest(manifest_id, source, content_hash):
    return {
        "manifest_id": manifest_id,
        "source_hashes": {source: content_hash},
        "created_at": REGISTERED_AT.isoformat(),
    }


def _benchmark_manifest(
    *,
    manifest_id="spy-1",
    content_hash="spy-sha256-" + "4" * 64,
    start_date="2024-01-02",
    end_date="2026-06-30",
):
    return {
        "manifest_id": manifest_id,
        "symbol": "SPY",
        "timeframe": "1Day",
        "adjustment": "all",
        "price_field": "c",
        "total_return": True,
        "feed": "iex",
        "start_date": start_date,
        "end_date": end_date,
        "valuation_rule": "xnys_session_close",
        "valuation_timestamps": [
            f"{start_date}T20:00:00Z",
            f"{end_date}T20:00:00Z",
        ],
        "content_hash": content_hash,
    }


def _spec(**overrides):
    fields = {
        "experiment_id": "attempt-0001",
        "parent_experiment_id": None,
        "search_scope": "graph-fresh-direct/live-40",
        "commit_sha": "b25e2f1",
        "source_tree_hash": "sha256:source",
        "effective_config": {
            "active_ceiling": 0.40,
            "horizons": [1, 3, 5],
            "nested": {"feature": True},
        },
        "model_provider": "openai",
        "model_name": "research-model",
        "prompt_hashes": {"decision": "sha256:prompt"},
        "model_settings": {"temperature": 0.0},
        "seed": 179,
        "predeclared_repeats": 3,
        "dataset_manifest": _source_manifest(
            "dataset-1", "bars", "sha256:data"
        ),
        "graph_manifest": _source_manifest(
            "graph-1", "graph", "sha256:graph"
        ),
        "universe_manifest": _source_manifest(
            "universe-1", "universe", "sha256:universe"
        ),
        "benchmark_manifest": _benchmark_manifest(),
        "execution_cost_model": {
            "version": "cost-v1",
            "spread_bps": 1.0,
            "slippage_bps": 1.0,
            "fee_bps": 1.0,
            "latency_seconds": 0.0,
        },
        "start_date": "2024-01-02",
        "end_date": "2026-06-30",
        "fold": "walk-forward-03",
        "actor": "research-orchestrator",
    }
    fields.update(overrides)
    return ExperimentSpec(**fields)


def test_spec_deep_freezes_inputs_and_has_order_independent_fingerprint():
    config = {"z": [1, {"enabled": True}], "a": 2}
    spec = _spec(effective_config=config)
    same = _spec(effective_config={"a": 2, "z": [1, {"enabled": True}]})

    config["z"][1]["enabled"] = False

    assert spec.fingerprint == same.fingerprint
    assert spec.effective_config_hash == same.effective_config_hash
    assert spec.effective_config_hash.startswith("config-sha256-")
    assert _spec(
        effective_config={"a": 3, "z": [1, {"enabled": True}]}
    ).effective_config_hash != spec.effective_config_hash
    assert ExperimentSpec.from_doc(spec.to_doc()).effective_config_hash == (
        spec.effective_config_hash
    )
    assert spec.effective_config["z"][1]["enabled"] is True
    with pytest.raises(TypeError):
        spec.effective_config["a"] = 3


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("seed", True),
        ("seed", 179.5),
        ("predeclared_repeats", False),
        ("predeclared_repeats", 3.5),
        ("predeclared_repeats", 0),
    ],
)
def test_spec_rejects_non_integer_seed_and_repeat_provenance(field, value):
    with pytest.raises(ValueError):
        _spec(**{field: value})


def test_repeated_attempts_have_new_ids_but_the_same_configuration_fingerprint():
    first = _spec(experiment_id="attempt-0001")
    repeat = _spec(experiment_id="attempt-0002")

    assert repeat.experiment_id != first.experiment_id
    assert repeat.fingerprint == first.fingerprint


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("commit_sha", "different-commit"),
        ("source_tree_hash", "sha256:different-source"),
        ("effective_config", {"active_ceiling": 0.60}),
        ("model_provider", "different-provider"),
        ("model_name", "different-model"),
        ("prompt_hashes", {"decision": "sha256:different-prompt"}),
        ("model_settings", {"temperature": 0.2}),
        ("seed", 180),
        ("predeclared_repeats", 4),
        (
            "dataset_manifest",
            _source_manifest("dataset-2", "bars", "sha256:data2"),
        ),
        (
            "graph_manifest",
            _source_manifest("graph-2", "graph", "sha256:graph2"),
        ),
        (
            "universe_manifest",
            _source_manifest(
                "universe-2", "universe", "sha256:universe2"
            ),
        ),
        (
            "benchmark_manifest",
            _benchmark_manifest(
                manifest_id="spy-2",
                content_hash="spy-sha256-" + "5" * 64,
            ),
        ),
        (
            "execution_cost_model",
            {
                "version": "cost-v2",
                "spread_bps": 2.0,
                "slippage_bps": 2.0,
                "fee_bps": 2.0,
                "latency_seconds": 1.0,
            },
        ),
        ("start_date", "2024-02-01"),
        ("end_date", "2026-05-31"),
        ("fold", "walk-forward-04"),
    ],
)
def test_every_required_provenance_dimension_changes_fingerprint(
    field, replacement
):
    original = _spec()
    changes = {field: replacement}
    if field == "start_date":
        changes["benchmark_manifest"] = _benchmark_manifest(
            start_date=replacement
        )
    elif field == "end_date":
        changes["benchmark_manifest"] = _benchmark_manifest(
            end_date=replacement
        )
    changed = _spec(**changes)
    assert changed.fingerprint != original.fingerprint


def test_failed_attempt_still_counts_as_a_trial_in_its_declared_scope():
    registry = ExperimentRegistry(clock=lambda: REGISTERED_AT)
    spec = _spec()

    registered = registry.register_before_run(spec)
    registry.fail(spec.experiment_id, "data_missing")

    assert registered.registered_at == REGISTERED_AT
    assert registry.status(spec.experiment_id) == "failed"
    assert registry.trial_count(scope=spec.search_scope) == 1
    assert registry.trial_count(scope="another-scope") == 0


def test_duplicate_attempt_id_cannot_hide_a_second_trial():
    registry = ExperimentRegistry(clock=lambda: REGISTERED_AT)
    registry.register_before_run(_spec())

    with pytest.raises(DuplicateExperimentError):
        registry.register_before_run(_spec())
    with pytest.raises(DuplicateExperimentError):
        registry.register_before_run(
            _spec(commit_sha="different", source_tree_hash="sha256:different")
        )


def test_terminal_result_is_append_only_and_does_not_mutate_registration():
    registry = ExperimentRegistry(clock=lambda: REGISTERED_AT)
    registered = registry.register_before_run(_spec())

    registry.complete_experiment("attempt-0001", {"active_return": 0.12})

    assert registered.status == "registered"
    assert registry.status("attempt-0001") == "completed"
    assert registry.outcome("attempt-0001").result["active_return"] == 0.12
    with pytest.raises(ExperimentAlreadyTerminalError):
        registry.fail("attempt-0001", "rewrite")
    with pytest.raises(ExperimentAlreadyTerminalError):
        registry.complete_experiment("attempt-0001", {"active_return": 0.99})


class _ImmutableBackend:
    def __init__(self):
        self.rows = {}
        self.writes = []

    def insert_record(self, table, doc, *, durability):
        self.writes.append((table, doc["id"], durability))
        key = (table, doc["id"])
        prior = self.rows.get(key)
        if prior is None:
            # Preserve the JSON numeric token type. Fingerprint
            # canonicalization intentionally distinguishes a declared integer
            # from a floating-point value.
            def normalize(value):
                if isinstance(value, bool):
                    return value
                if isinstance(value, int):
                    return int(value)
                if isinstance(value, float):
                    return float(value)
                if isinstance(value, dict):
                    return {str(k): normalize(v) for k, v in value.items()}
                if isinstance(value, list):
                    return [normalize(v) for v in value]
                return value

            self.rows[key] = normalize(dict(doc))
        return prior

    def get_record(self, table, record_id):
        return self.rows.get((table, record_id))

    def list_records(self, table, filters=None, order_field=None):
        rows = [
            dict(row)
            for (row_table, _), row in self.rows.items()
            if row_table == table
        ]
        for field, value in (filters or {}).items():
            rows = [row for row in rows if row.get(field) == value]
        if order_field:
            rows = sorted(
                rows,
                key=lambda row: (row.get(order_field, ""), row["id"]),
            )
        return rows

    def count_records(self, table, filters=None):
        return len(self.list_records(table, filters))


def test_rethink_store_persists_registration_before_terminal_outcome():
    backend = _ImmutableBackend()
    store = AlphaRethinkStore.for_backend(backend)
    registry = ExperimentRegistry(store=store, clock=lambda: REGISTERED_AT)

    registry.register_before_run(_spec())
    registry.complete_experiment("attempt-0001", {"active_return": 0.12})

    assert backend.writes == [
        (EXPERIMENTS_TABLE, "attempt-0001", "hard"),
        (EXPERIMENTS_TABLE, "attempt-0001:terminal", "hard"),
    ]
    assert backend.rows[
        (EXPERIMENTS_TABLE, "attempt-0001")
    ]["record_kind"] == "registration"
    assert backend.rows[
        (EXPERIMENTS_TABLE, "attempt-0001:terminal")
    ]["record_kind"] == "outcome"
    assert len(registry.all_experiments()) == 1
    assert registry.trial_count() == 1
    assert registry.trial_count(scope="graph-fresh-direct/live-40") == 1


def test_rethink_trial_count_includes_attempts_from_prior_processes():
    backend = _ImmutableBackend()
    first = ExperimentRegistry(
        store=AlphaRethinkStore.for_backend(backend),
        clock=lambda: REGISTERED_AT,
    )
    first.register_before_run(_spec())

    restarted = ExperimentRegistry(
        store=AlphaRethinkStore.for_backend(backend),
        clock=lambda: REGISTERED_AT,
    )

    assert restarted.trial_count(scope="graph-fresh-direct/live-40") == 1
    assert restarted.all_experiments("graph-fresh-direct/live-40")[0].fingerprint == (
        _spec().fingerprint
    )
