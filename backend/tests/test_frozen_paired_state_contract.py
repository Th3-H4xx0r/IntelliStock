from __future__ import annotations

import copy
import importlib.util
from pathlib import Path

import pytest


_PATH = Path(__file__).resolve().parents[1] / "frozen_paired_state.py"
_SPEC = importlib.util.spec_from_file_location("frozen_paired_state_under_test", _PATH)
mod = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(mod)


def digest(ch="a"):
    return "sha256:" + ch * 64


def core_manifest():
    return {
        "protocol_version": mod.FROZEN_STATE_PROTOCOL_VERSION,
        "pair_id": "anchor-target-w0-v1",
        "window": {
            "start": "2026-01-01",
            "end": "2026-03-01",
            "baseline_cutoff": "2025-12-31T21:00:00Z",
        },
        "logical_identity": {
            "base_instance_id": "v2-let-run-core",
            "history_scope_id": "scope-sha256-deadbeef",
            "history_scope_doc_sha256": digest("1"),
        },
        "execution": {
            "common_snapshot_sha256": digest("2"),
            "control_snapshot_sha256": digest("3"),
            "treatment_snapshot_sha256": digest("4"),
            "allowed_diff": {
                "path": "$.core.strategy.specs[0].config.anchor_reinforce_target_pct",
                "control": 12,
                "treatment": 20,
            },
            "source_tree_sha256": digest("5"),
            "image_digest": digest("6"),
            "dependency_runtime_sha256": digest("7"),
            "seed": 193,
        },
        "state": {
            "tables": {
                "GraphNexusDiscoveredStocks": {
                    "key_fields": ["id"],
                    "row_count": 2,
                    "rows_sha256": digest("8"),
                    "write_policy": "arm_local",
                },
                "GraphNexusNewsCache": {
                    "key_fields": ["id"],
                    "row_count": 3,
                    "rows_sha256": digest("9"),
                    "write_policy": "read_only",
                },
            },
            "runtime_state_sha256": digest("a"),
        },
        "external": {
            "pit": {"artifact_id": "pit-fixture-1", "sha256": digest("b"), "mode": "replay_only"},
            "graph": {"artifact_id": "graph-fixture-1", "sha256": digest("c"), "mode": "replay_only"},
            "model": {"artifact_id": "model-fixture-1", "sha256": digest("d"), "mode": "replay_only"},
            "market": {"artifact_id": "market-fixture-1", "sha256": digest("e"), "mode": "replay_only"},
            "benchmark": {"artifact_id": "benchmark-1", "sha256": digest("f"), "mode": "read_only"},
        },
        "clock": {"wall_time": "2025-12-31T21:00:00Z", "timezone": "UTC", "market_calendar": "XNYS"},
        "runtime": {"environment": {
            "timezone": "UTC", "python_hash_seed": "0",
            "nexus_backtest_snapshot_write": "off", "network_policy": "deny",
        }},
        "isolation": {
            "rethinkdb": "disposable_per_arm",
            "neo4j": "sealed_replay",
            "production_db_unreachable": True,
            "production_graph_unreachable": True,
            "external_network_unreachable": True,
        },
    }


def execution_snapshot(target):
    return {"core": {"strategy": {"specs": [{"config": {
        "anchor_reinforce_execution_enabled": True,
        "anchor_reinforce_target_pct": target,
        "unchanged": [1, 2, 3],
    }}]}}}


def receipt(ch="a"):
    value = {name: digest(ch) for name in mod._NEGATIVE_CONTROL_DIGEST_FIELDS}
    value["shared_store_before_sha256"] = digest("4")
    value["shared_store_after_sha256"] = digest("4")
    value["target_pct"] = 12
    value["complete"] = True
    value["audits_complete"] = True
    value["provider_fallback_used"] = False
    value["undeclared_read_occurred"] = False
    return value


def test_build_and_verify_manifest_is_deterministic_and_immutable_by_copy():
    core = core_manifest()
    built = mod.build_frozen_paired_state_manifest(core)
    again = mod.build_frozen_paired_state_manifest(copy.deepcopy(core))
    assert built == again
    assert built["bundle_sha256"].startswith("sha256:")
    built["state"]["tables"]["GraphNexusNewsCache"]["row_count"] = 9
    assert again["state"]["tables"]["GraphNexusNewsCache"]["row_count"] == 3


def test_verify_rejects_tampering():
    built = mod.build_frozen_paired_state_manifest(core_manifest())
    built["execution"]["seed"] += 1
    with pytest.raises(mod.FrozenStateError) as exc:
        mod.verify_frozen_paired_state_manifest(built)
    assert exc.value.code == "bundle_hash_mismatch"


@pytest.mark.parametrize("mutation,code", [
    (lambda c: c["runtime"]["environment"].update({"network_policy": "allow"}), "runtime_environment_invalid"),
    (lambda c: c["isolation"].update({"production_db_unreachable": False}), "isolation_invalid"),
    (lambda c: c["external"]["model"].update({"mode": "record"}), "external_artifact_mode_invalid"),
    (lambda c: c["execution"]["allowed_diff"].update({"control": 0}), "allowed_diff_invalid"),
    (lambda c: c["state"]["tables"]["GraphNexusNewsCache"].update({"write_policy": "shared"}), "state_write_policy_invalid"),
])
def test_manifest_fails_closed_for_non_frozen_policy(mutation, code):
    core = core_manifest()
    mutation(core)
    with pytest.raises(mod.FrozenStateError) as exc:
        mod.build_frozen_paired_state_manifest(core)
    assert exc.value.code == code


def test_state_rows_hash_is_primary_key_order_invariant_and_db_numeric_stable():
    one = [{"id": "b", "x": 2.0}, {"id": "a", "x": 1}]
    two = [{"x": 1.0, "id": "a"}, {"x": 2, "id": "b"}]
    assert mod.state_rows_sha256(one) == mod.state_rows_sha256(two)


def test_state_rows_hash_detects_content_and_duplicate_keys():
    base = [{"id": "a", "x": 1}]
    changed = [{"id": "a", "x": 2}]
    assert mod.state_rows_sha256(base) != mod.state_rows_sha256(changed)
    with pytest.raises(mod.FrozenStateError) as exc:
        mod.state_rows_sha256([{"id": 1}, {"id": 1.0}])
    assert exc.value.code == "state_primary_key_duplicate"


def test_execution_snapshots_may_differ_only_at_target_12_to_20():
    result = mod.compare_execution_snapshots(execution_snapshot(12), execution_snapshot(20))
    assert result["control"] == 12
    assert result["treatment"] == 20


def test_execution_snapshot_rejects_second_difference_and_wrong_direction():
    control = execution_snapshot(12)
    treatment = execution_snapshot(20)
    treatment["core"]["strategy"]["specs"][0]["config"]["unchanged"] = []
    with pytest.raises(mod.FrozenStateError) as exc:
        mod.compare_execution_snapshots(control, treatment)
    assert exc.value.code == "execution_snapshot_diff_invalid"
    with pytest.raises(mod.FrozenStateError) as exc:
        mod.compare_execution_snapshots(execution_snapshot(20), execution_snapshot(12))
    assert exc.value.code == "treatment_values_invalid"
    with pytest.raises(mod.FrozenStateError) as exc:
        mod.compare_execution_snapshots(execution_snapshot(12), execution_snapshot(12.5))
    assert exc.value.code == "treatment_values_invalid"


def test_flat_key_named_like_the_treatment_path_is_not_the_treatment():
    path = "$.core.strategy.specs[0].config.anchor_reinforce_target_pct"
    with pytest.raises(mod.FrozenStateError) as exc:
        mod.compare_execution_snapshots({path: 12}, {path: 20})
    assert exc.value.code == "execution_snapshot_diff_invalid"


def test_treatment_parameters_are_fixed_protocol_constants():
    import inspect

    signature = inspect.signature(mod.compare_execution_snapshots)
    assert list(signature.parameters) == ["control", "treatment"]


def test_negative_control_requires_exact_artifact_identity():
    left = receipt()
    result = mod.verify_negative_control_receipts(left, copy.deepcopy(left))
    assert result["status"] == "identical"
    changed = copy.deepcopy(left)
    changed["fills_sha256"] = digest("9")
    with pytest.raises(mod.FrozenStateError) as exc:
        mod.verify_negative_control_receipts(left, changed)
    assert exc.value.code == "negative_control_mismatch"


def test_negative_control_rejects_shared_store_mutation_and_incomplete_run():
    left = receipt()
    changed = copy.deepcopy(left)
    changed["shared_store_after_sha256"] = digest("9")
    with pytest.raises(mod.FrozenStateError) as exc:
        mod.verify_negative_control_receipts(changed, changed)
    assert exc.value.code == "shared_store_mutated"
    changed = copy.deepcopy(left)
    changed["complete"] = False
    with pytest.raises(mod.FrozenStateError) as exc:
        mod.verify_negative_control_receipts(changed, changed)
    assert exc.value.code == "negative_control_invalid"


def test_error_does_not_echo_hostile_state_values():
    core = core_manifest()
    secret = "DO-NOT-ECHO-MATERIAL"
    core["runtime"]["environment"] = {secret: secret}
    with pytest.raises(mod.FrozenStateError) as exc:
        mod.build_frozen_paired_state_manifest(core)
    assert secret not in str(exc.value)


def test_canonicalizer_rejects_bool_aliases_non_finite_and_unsupported_types():
    assert mod.canonical_state_json({"x": True}) != mod.canonical_state_json({"x": 1})
    with pytest.raises(mod.FrozenStateError):
        mod.canonical_state_json({"x": float("nan")})
    with pytest.raises(mod.FrozenStateError):
        mod.canonical_state_json({"x": {1, 2}})


def test_state_tables_must_be_declared_decision_state_tables():
    core = core_manifest()
    core["state"]["tables"]["TotallyUnknownTable"] = {
        "key_fields": ["id"], "row_count": 1,
        "rows_sha256": digest("7"), "write_policy": "arm_local",
    }
    with pytest.raises(mod.FrozenStateError) as exc:
        mod.build_frozen_paired_state_manifest(core)
    assert exc.value.code == "state_table_name_invalid"


def test_duplicate_key_fields_and_non_string_policies_fail_with_stable_codes():
    core = core_manifest()
    core["state"]["tables"]["GraphNexusNewsCache"]["key_fields"] = ["id", "id"]
    with pytest.raises(mod.FrozenStateError) as exc:
        mod.build_frozen_paired_state_manifest(core)
    assert exc.value.code == "state_key_fields_invalid"

    core = core_manifest()
    core["state"]["tables"]["GraphNexusNewsCache"]["write_policy"] = ["read_only"]
    with pytest.raises(mod.FrozenStateError) as exc:
        mod.build_frozen_paired_state_manifest(core)
    assert exc.value.code == "state_write_policy_invalid"

    core = core_manifest()
    core["isolation"]["neo4j"] = ["sealed_replay"]
    with pytest.raises(mod.FrozenStateError) as exc:
        mod.build_frozen_paired_state_manifest(core)
    assert exc.value.code == "isolation_invalid"


def test_table_names_are_never_echoed_in_errors():
    core = core_manifest()
    core["state"]["tables"]["GraphNexusNewsCache"]["row_count"] = -1
    with pytest.raises(mod.FrozenStateError) as exc:
        mod.build_frozen_paired_state_manifest(core)
    assert "GraphNexusNewsCache" not in str(exc.value)


@pytest.mark.parametrize("mutation,code", [
    (lambda c: c["window"].update({"start": "2026-03-01", "end": "2026-01-01"}), "window_invalid"),
    (lambda c: c["window"].update({"baseline_cutoff": "2026-02-01T00:00:00Z"}), "baseline_cutoff_invalid"),
    (lambda c: c["window"].update({"start": "not-a-date"}), "window_invalid"),
    (lambda c: c["clock"].update({"wall_time": "Z"}), "timestamp_invalid"),
])
def test_time_grammar_and_ordering_fail_closed(mutation, code):
    core = core_manifest()
    mutation(core)
    with pytest.raises(mod.FrozenStateError) as exc:
        mod.build_frozen_paired_state_manifest(core)
    assert exc.value.code == code


def test_negative_control_requires_the_full_artifact_set():
    left = receipt()
    incomplete = dict(left)
    del incomplete["nav_series_sha256"]
    with pytest.raises(mod.FrozenStateError) as exc:
        mod.verify_negative_control_receipts(incomplete, incomplete)
    assert exc.value.code == "negative_control_invalid"

    for name in ("provider_fallback_used", "undeclared_read_occurred"):
        changed = dict(left)
        changed[name] = True
        with pytest.raises(mod.FrozenStateError) as exc:
            mod.verify_negative_control_receipts(changed, changed)
        assert exc.value.code == "negative_control_invalid"

    float_target = dict(left)
    float_target["target_pct"] = 12.0
    with pytest.raises(mod.FrozenStateError) as exc:
        mod.verify_negative_control_receipts(float_target, float_target)
    assert exc.value.code == "negative_control_invalid"
