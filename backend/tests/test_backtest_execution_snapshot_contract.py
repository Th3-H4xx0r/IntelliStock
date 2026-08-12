import copy
import datetime
import hashlib
import json
import math

import pytest

from backtest_execution_snapshot import (
    EXECUTION_SNAPSHOT_MAX_BYTES,
    EXECUTION_SNAPSHOT_MODE,
    ExecutionSnapshotError,
    build_execution_snapshot,
    canonical_execution_json,
    execution_snapshot_public_status,
    execution_snapshot_sha256,
    make_execution_snapshot_queue_fields,
    verify_execution_snapshot_queue_fields,
)


SIGNING_KEY = b"queue-execution-snapshot-test-key-v1-only"
CREATED = "2026-08-12T00:00:00Z"
ROW_ID = 123456


def _adapter():
    return {
        "openai_base_url": None,
        "nvidia_base_url": None,
        "azure_openai_endpoint": None,
        "azure_openai_api_version": None,
        "reasoning_effort": None,
        "cli_path": None,
        "extra_args": None,
        "ollama_base_url": None,
        "ollama_keep_alive": None,
        "ollama_think": None,
        "bedrock_region": None,
        "bedrock_reasoning": None,
        "openrouter_base_url": "https://openrouter.ai/api/v1",
        "openrouter_referer": None,
        "openrouter_title": None,
        "model_cache_family": None,
        "input_cost_per_1m": None,
        "output_cost_per_1m": None,
        "cache_creation_cost_per_1m": None,
        "cache_read_cost_per_1m": None,
    }


def _core():
    return {
        "run": {
            "instance_id": "v2-let-run-core",
            "symbol_mode": "explicit",
            "symbols": ["SPY"],
            "start_date": "2026-03-30",
            "end_date": "2026-04-27",
            "granularity_sec": 3600,
            "initial_cash": 6000.0,
            "fee": {
                "emulated": False,
                "requested_venue": "default",
                "resolved_venue": "alpaca",
                "taker_rate": 0.0,
            },
            "seed": {
                "algorithm": "intellistock-backtest-v1",
                "value": 424242,
                "python_hash_seed": "0",
            },
            "evidence": {
                "evidence_mode": "off",
                "fixture_build_id": None,
                "replay_fixture_id": None,
                "matrix_manifest_id": None,
                "matrix_arm_id": None,
                "cost_scenario_id": None,
                "equity_total_cost_bps": None,
                "nexus_candidate_overrides": {},
                "fixture_ordinal": None,
                "pit_mode": "research",
            },
        },
        "instance": {
            "record_id": "v2-let-run-core",
            "kind": "equity",
            "strategy_record_id": "193",
        },
        "strategy": {
            "record_id": "193",
            "name": "Nexus Only",
            "experiment_spec": None,
            "specs": [{
                "ordinal": 0,
                "strategy": "graph_nexus_analysis",
                "weight": 1.0,
                "execution_position": 0,
                "decision_phase": "pre",
                "execution_scope": "run_once",
                "conditions": {},
                "config": {
                    "anchor_reinforce_enabled": True,
                    "anchor_reinforce_execution_core_floor_enabled": True,
                    "anchor_reinforce_execution_enabled": True,
                    "anchor_reinforce_execution_max_position_pct": 20,
                    "anchor_reinforce_execution_turnover_ceiling_pct": 0.8,
                    "anchor_reinforce_target_pct": 12,
                    "llm_model_id": "model-7",
                    "llm_provider": "openrouter",
                    "llm_model": "vendor/model",
                    "model_name": "vendor/model",
                    "pit_mode": "research",
                },
            }],
        },
        "models": [{
            "spec_ordinal": 0,
            "role_prefix": "",
            "record_id": "model-7",
            "provider": "openrouter",
            "model": "vendor/model",
            "adapter": _adapter(),
            "runtime_access": {
                "kind": "models_row",
                "record_id": "model-7",
                "access_revision": 4,
                "required": True,
            },
        }],
        "broker_access": {
            "trading": {
                "kind": "brokerage_row",
                "record_id": "broker-trading",
                "access_revision": 8,
                "brokerage_type": "alpaca",
                "paper": True,
                "data_feed": "iex",
            },
            "market_data": {
                "kind": "brokerage_row",
                "record_id": "broker-data",
                "access_revision": 3,
                "brokerage_type": "alpaca",
                "paper": False,
                "data_feed": "sip",
            },
        },
        "runtime": {
            "source_tree_sha256": "0" * 64,
            "image_digest": "sha256:" + "2" * 64,
            "dependency_runtime_sha256": "3" * 64,
            "python_version": "3.11.15",
            "strategy_modules": [{
                "strategy": "graph_nexus_analysis",
                "module_sha256": "1" * 64,
            }],
            "environment": {
                "timezone": "UTC",
                "nexus_backtest_snapshot_write": "off",
            },
        },
    }


def _fields(core=None):
    return make_execution_snapshot_queue_fields(
        backtest_id=ROW_ID,
        created_at=CREATED,
        core=_core() if core is None else core,
        signing_key=SIGNING_KEY,
    )


def _error(call):
    with pytest.raises(ExecutionSnapshotError) as exc:
        call()
    return exc.value


def _error_code(call):
    return _error(call).code


def test_canonical_json_is_order_independent_and_db_numeric_stable():
    left = {"b": [1, 2], "a": {"z": "é", "x": -0.0}}
    right = {"a": {"x": 0, "z": "é"}, "b": [1.0, 2.0]}
    assert canonical_execution_json(left) == canonical_execution_json(right)
    assert canonical_execution_json(left) != canonical_execution_json({"b": [2, 1], "a": right["a"]})
    assert canonical_execution_json({"x": 1}) == canonical_execution_json({"x": 1.0})


def test_golden_canonical_bytes_snapshot_digest_and_hmac_are_stable():
    generic = canonical_execution_json({"b": [2, 1], "a": {"x": 0, "z": "é"}})
    assert generic == '{"a":{"x":0,"z":"é"},"b":[2,1]}'
    assert hashlib.sha256(generic.encode()).hexdigest() == (
        "d5eb594357ca24f7a929bcc687ec4ea5087d7df9a62a23d751a4f0668df581eb"
    )
    fields = _fields()
    assert fields["execution_snapshot_sha256"] == "sha256:36cdfcac7c4066beb394b3c117182d36f3d8d9e6a5d78f221131e4aee4b0c272"
    assert fields["execution_snapshot_hmac_sha256"] == "hmac-sha256:2ec43e13d446edf54681a2dbdf931e0c9029fdc3ef3d224473f6641dba708548"


def test_canonical_json_rejects_non_json_nonfinite_subclasses_and_unsafe_integers():
    class S(str):
        pass
    class D(dict):
        pass
    bad = [
        {"x": math.nan}, {"x": math.inf}, {"x": (1, 2)}, {"x": {1, 2}},
        {"x": b"bytes"}, {1: "non-string"},
        {"x": datetime.datetime.now(datetime.timezone.utc)}, {"x": object()},
        {"x": S("custom")}, D(x=1), {"x": 1 << 60},
    ]
    for value in bad:
        assert _error_code(lambda value=value: canonical_execution_json(value)).startswith("json_")


@pytest.mark.parametrize("value", ["\ud800", "ok\udfff", "\ud800key"])
def test_unpaired_surrogates_fail_with_stable_value_free_errors(value):
    exc = _error(lambda: canonical_execution_json({value: value}))
    assert exc.code == "json_invalid_unicode"
    assert "ud800" not in str(exc).lower()
    assert "udfff" not in str(exc).lower()


def test_build_returns_detached_db_stable_image_and_round_trips():
    core = _core()
    snapshot = build_execution_snapshot(core)
    core["run"]["symbols"].append("QQQ")
    assert snapshot["core"]["run"]["symbols"] == ["SPY"]
    assert snapshot["core"]["run"]["initial_cash"] == 6000
    assert isinstance(snapshot["core"]["run"]["initial_cash"], int)
    assert execution_snapshot_sha256(snapshot) == execution_snapshot_sha256(
        json.loads(canonical_execution_json(snapshot)))


def test_v1_positive_schema_rejects_empty_partial_extra_and_non_equity_cores():
    assert _error_code(lambda: build_execution_snapshot({})) == "schema_core_invalid"
    assert _error_code(lambda: build_execution_snapshot({"placeholder": 1})) == "schema_core_invalid"
    for mutation, code in [
        (lambda c: c.pop("runtime"), "schema_core_invalid"),
        (lambda c: c.__setitem__("extra", {}), "schema_core_invalid"),
        (lambda c: c["instance"].__setitem__("kind", "crypto"), "non_equity_forbidden"),
        (lambda c: c["strategy"]["specs"][0]["config"].__setitem__("unknown", 1), "schema_strategy_config_invalid"),
        (lambda c: c["strategy"]["specs"][0].__setitem__("conditions", {"x": 1}), "schema_strategy_spec_invalid"),
    ]:
        core = _core(); mutation(core)
        assert _error_code(lambda core=core: build_execution_snapshot(core)) == code


@pytest.mark.parametrize("injected", [
    {"material": "MZXW6YTBOI======1234567890ABCDEFGHIJKLMNOPQRSTUVWXYZ"},
    {"note": "  gAAAAABopaqueFernetCiphertext"},
    {"endpoint": "https://e.invalid/?client_secret=C4N4RYopaque987654"},
    {"endpoint": "//user:C4N4RYopaque987654@e.invalid"},
    {"box": {"redacted": True, "source": "runtime_secret", "material": "opaque"}},
    {"ghp_abcdefghijklmnopqrstuvwxyz": "x"},
])
def test_positive_schema_rejects_adversarial_secret_shapes_under_neutral_locations(injected):
    core = _core()
    core["strategy"].update(injected)
    exc = _error(lambda: build_execution_snapshot(core))
    assert exc.code.startswith("schema_") or "secret" in exc.code or "redaction" in exc.code
    rendered = str(exc)
    for canary in ("MZXW", "gAAAA", "client_secret", "C4N4RY", "ghp_", "opaque"):
        assert canary not in rendered


def test_allowed_endpoint_rejects_secret_query_fragment_userinfo_and_protocol_relative_url():
    values = [
        "https://e.invalid/?client_secret=opaque",
        "https://user:opaque@e.invalid/path",
        "https://e.invalid/path#access_code=opaque",
        "//user:opaque@e.invalid",
    ]
    for value in values:
        core = _core(); core["models"][0]["adapter"]["openrouter_base_url"] = value
        assert _error_code(lambda core=core: build_execution_snapshot(core)) in {
            "schema_url_invalid", "secretish_url_forbidden", "schema_model_adapter_invalid"
        }


def test_secret_and_control_characters_in_hostile_keys_never_reach_error_text():
    key = "password\nCANARY_SECRET_VALUE"
    exc = _error(lambda: build_execution_snapshot({key: "x"}))
    assert exc.code == "schema_core_invalid"
    assert "CANARY" not in str(exc)
    assert "password" not in str(exc)
    assert "\n" not in str(exc)
    assert all("CANARY" not in path for path in exc.paths)


def test_symbol_mode_explicit_and_discovery_semantics_are_unambiguous():
    for mode, symbols, accepted in [
        ("explicit", ["SPY"], True),
        ("discovery", [], True),
        ("explicit", [], False),
        ("discovery", ["SPY"], False),
    ]:
        core = _core(); core["run"].update(symbol_mode=mode, symbols=symbols)
        if accepted:
            assert build_execution_snapshot(core)["core"]["run"]["symbol_mode"] == mode
        else:
            assert _error_code(lambda core=core: build_execution_snapshot(core)) == "schema_symbols_invalid"


def test_equity_symbol_and_alpaca_fee_schema_reject_crypto_semantics():
    core = _core(); core["run"]["symbols"] = ["BTC/USD"]
    assert _error_code(lambda: build_execution_snapshot(core)) == "schema_symbols_invalid"
    for mutation in [
        lambda fee: fee.__setitem__("requested_venue", "binance"),
        lambda fee: fee.__setitem__("resolved_venue", "binance"),
        lambda fee: fee.__setitem__("emulated", True),
        lambda fee: fee.__setitem__("taker_rate", 0.99),
    ]:
        core = _core(); mutation(core["run"]["fee"])
        assert _error_code(lambda core=core: build_execution_snapshot(core)) == "schema_fee_invalid"


def test_evidence_identities_use_bounded_content_address_grammars():
    core = _core()
    evidence = core["run"]["evidence"]
    evidence.update({
        "evidence_mode": "replay",
        "matrix_manifest_id": "matrix-sha256-" + "a" * 64,
        "matrix_arm_id": "arm-sha256-" + "b" * 64,
        "cost_scenario_id": "base",
        "replay_fixture_id": "fixture-sha256-" + "c" * 64,
        "fixture_ordinal": 0,
    })
    assert build_execution_snapshot(core)["core"]["run"]["evidence"]["replay_fixture_id"].startswith("fixture-sha256-")
    for name, value in [
        ("replay_fixture_id", "C4N4RY-opaque-access-material"),
        ("matrix_manifest_id", "matrix-opaque"),
        ("matrix_arm_id", "arm-opaque"),
        ("cost_scenario_id", "99bps"),
    ]:
        hostile = copy.deepcopy(core); hostile["run"]["evidence"][name] = value
        assert _error_code(lambda hostile=hostile: build_execution_snapshot(hostile)) == "schema_evidence_invalid"
    hostile = copy.deepcopy(core); hostile["run"]["evidence"]["replay_fixture_id"] = "x" * 200_000
    assert _error_code(lambda: build_execution_snapshot(hostile)) in {"schema_evidence_invalid", "snapshot_too_large"}


def test_window_and_symbol_granularity_workload_is_bounded():
    core = _core(); core["run"].update(
        symbols=[f"S{i}" for i in range(256)],
        start_date="2020-01-01", end_date="2020-12-31", granularity_sec=1,
    )
    assert _error_code(lambda: build_execution_snapshot(core)) == "schema_workload_limit"
    core = _core(); core["run"].update(start_date="2000-01-01", end_date="2020-01-01")
    assert _error_code(lambda: build_execution_snapshot(core)) == "schema_window_invalid"
    core = _core(); core["run"].update(
        symbol_mode="discovery", symbols=[], start_date="2025-01-01",
        end_date="2025-12-31", granularity_sec=60,
    )
    assert _error_code(lambda: build_execution_snapshot(core)) == "schema_workload_limit"
    core = _core(); core["run"].update(
        start_date="2025-01-01", end_date="2025-12-31", granularity_sec=60,
    )
    assert _error_code(lambda: build_execution_snapshot(core)) == "schema_workload_limit"


def test_evidence_cost_scenario_label_and_executable_cost_are_consistent():
    base = _core()
    evidence = base["run"]["evidence"]
    evidence.update({
        "evidence_mode": "record",
        "fixture_build_id": "build-0",
        "matrix_manifest_id": "matrix-sha256-" + "a" * 64,
        "matrix_arm_id": "arm-sha256-" + "b" * 64,
        "fixture_ordinal": 0,
    })
    for label, cost in [("base", None), ("25bps", 25.0), ("50bps", 50.0)]:
        core = copy.deepcopy(base)
        core["run"]["evidence"].update(cost_scenario_id=label, equity_total_cost_bps=cost)
        assert build_execution_snapshot(core)["core"]["run"]["evidence"]["cost_scenario_id"] == label
    for label, cost in [("base", 50.0), ("25bps", 50.0), ("50bps", 25.0)]:
        core = copy.deepcopy(base)
        core["run"]["evidence"].update(cost_scenario_id=label, equity_total_cost_bps=cost)
        assert _error_code(lambda core=core: build_execution_snapshot(core)) == "schema_evidence_cost_mismatch"


def test_pit_mode_model_identity_and_access_are_relationally_bound():
    mutations = [
        lambda c: c["strategy"]["specs"][0]["config"].__setitem__("pit_mode", "strict"),
        lambda c: c["strategy"]["specs"][0]["config"].__setitem__("llm_provider", "ollama"),
        lambda c: c["strategy"]["specs"][0]["config"].__setitem__("llm_model", "different/model"),
        lambda c: c["strategy"]["specs"][0]["config"].__setitem__("model_name", "different/model"),
        lambda c: c["strategy"]["specs"][0]["config"].pop("llm_model_id"),
        lambda c: c.__setitem__("models", []),
        lambda c: c["models"][0]["runtime_access"].__setitem__("record_id", "other-model"),
    ]
    for mutation in mutations:
        core = _core(); mutation(core)
        assert _error_code(lambda core=core: build_execution_snapshot(core)) in {
            "schema_pit_mismatch", "schema_model_binding_mismatch",
            "schema_models_invalid", "schema_access_invalid",
        }


def test_metadata_only_candidate_overrides_are_rejected_until_effective_schema_supports_them():
    core = _core()
    core["run"]["evidence"]["nexus_candidate_overrides"] = {
        "regime_position_cap_recovery_hard_enforce": True,
    }
    assert _error_code(lambda: build_execution_snapshot(core)) == "schema_evidence_effective_mismatch"


def test_fernet_token_embedded_in_an_otherwise_allowed_identity_is_rejected():
    token = "gAAAAABopaqueFernetCiphertext"
    core = _core()
    core["strategy"]["specs"][0]["config"]["llm_model_id"] = token
    core["models"][0]["record_id"] = token
    core["models"][0]["runtime_access"]["record_id"] = token
    assert _error_code(lambda: build_execution_snapshot(core)) == "secretish_value_forbidden"


def test_complete_envelope_verifies_and_verified_body_cannot_be_mutated_in_place():
    fields = _fields()
    verified = verify_execution_snapshot_queue_fields(
        fields,
        backtest_id=ROW_ID,
        created_at=CREATED,
        signing_key=SIGNING_KEY,
        required=True,
        expected_sha256=fields["execution_snapshot_sha256"],
    )
    assert verified.snapshot == fields["execution_snapshot"]
    first = verified.snapshot
    first["core"]["run"]["initial_cash"] = 1
    assert verified.snapshot["core"]["run"]["initial_cash"] == 6000
    assert execution_snapshot_sha256(verified.snapshot) == verified.sha256


def test_hostile_outer_control_types_fail_with_stable_value_free_results():
    class HostileStr(str):
        def __eq__(self, other):
            raise RuntimeError("CANARY_RAW_SECRET_MATERIAL")
        def __ne__(self, other):
            raise RuntimeError("CANARY_RAW_SECRET_MATERIAL")
        __hash__ = str.__hash__

    for field in ("execution_snapshot_mode", "execution_snapshot_signer"):
        fields = _fields(); fields[field] = HostileStr(fields[field])
        exc = _error(lambda fields=fields: verify_execution_snapshot_queue_fields(
            fields, backtest_id=ROW_ID, created_at=CREATED, signing_key=SIGNING_KEY
        ))
        assert exc.code in {"partial_contract", "signer_unsupported"}
        assert "CANARY" not in str(exc)
        assert execution_snapshot_public_status(fields) is None
    hostile_key = HostileStr("execution_snapshot_mode")
    fields = _fields(); fields[hostile_key] = fields.pop("execution_snapshot_mode")
    exc = _error(lambda: verify_execution_snapshot_queue_fields(
        fields, backtest_id=ROW_ID, created_at=CREATED, signing_key=SIGNING_KEY
    ))
    assert exc.code == "queue_row_unreadable"
    assert "CANARY" not in str(exc)
    assert execution_snapshot_public_status(fields) is None


def test_public_status_is_syntactic_unverified_metadata_only_and_never_copies_body_values():
    fields = _fields()
    assert execution_snapshot_public_status(fields) == {
        "mode": EXECUTION_SNAPSHOT_MODE,
        "schema_version": "queue-execution-snapshot-v1",
        "sha256": fields["execution_snapshot_sha256"],
        "verification": "unverified_claim",
    }
    hostile = copy.deepcopy(fields)
    hostile["execution_snapshot"]["schema_version"] = {"private_payload": "LEAK-ME"}
    assert execution_snapshot_public_status(hostile) is None
    for version in (True, 1.0, 1, [1]):
        hostile = copy.deepcopy(fields); hostile["execution_snapshot"]["schema_version"] = version
        assert execution_snapshot_public_status(hostile) is None


def test_legacy_absence_is_inert_unless_external_policy_or_engine_binding_requires_snapshot():
    assert verify_execution_snapshot_queue_fields(
        {}, backtest_id=ROW_ID, created_at=CREATED, signing_key=SIGNING_KEY
    ) is None
    assert verify_execution_snapshot_queue_fields(
        {}, backtest_id=None, created_at=None, signing_key=None
    ) is None
    for kwargs in [
        {"required": True},
        {"expected_sha256": "sha256:" + "f" * 64},
    ]:
        assert _error_code(lambda kwargs=kwargs: verify_execution_snapshot_queue_fields(
            {}, backtest_id=ROW_ID, created_at=CREATED, signing_key=SIGNING_KEY, **kwargs
        )) == "contract_missing"


@pytest.mark.parametrize("mutation,expected", [
    (lambda row: row.pop("execution_snapshot"), "partial_contract"),
    (lambda row: row.__setitem__("execution_snapshot_mode", "off"), "partial_contract"),
    (lambda row: row.__setitem__("execution_snapshot_signer", "other"), "signer_unsupported"),
    (lambda row: row.__setitem__("execution_snapshot_sha256", "bad"), "digest_invalid"),
    (lambda row: row.__setitem__("execution_snapshot_hmac_sha256", "bad"), "attestation_invalid"),
    (lambda row: row["execution_snapshot"]["core"]["run"].__setitem__("initial_cash", 7000), "stored_hash_mismatch"),
])
def test_partial_and_tampered_envelopes_fail_closed(mutation, expected):
    fields = copy.deepcopy(_fields()); mutation(fields)
    assert _error_code(lambda: verify_execution_snapshot_queue_fields(
        fields, backtest_id=ROW_ID, created_at=CREATED, signing_key=SIGNING_KEY
    )) == expected


def test_recomputed_plain_hash_does_not_authorize_body_tampering():
    fields = copy.deepcopy(_fields())
    fields["execution_snapshot"]["core"]["run"]["initial_cash"] = 7000
    fields["execution_snapshot_sha256"] = execution_snapshot_sha256(fields["execution_snapshot"])
    assert _error_code(lambda: verify_execution_snapshot_queue_fields(
        fields, backtest_id=ROW_ID, created_at=CREATED, signing_key=SIGNING_KEY
    )) == "attestation_mismatch"


@pytest.mark.parametrize("bad_id", ["123456", True, 0, -1, 123456.5, 1 << 60, object()])
def test_backtest_identity_requires_one_exact_bounded_integer_type(bad_id):
    assert _error_code(lambda bad_id=bad_id: make_execution_snapshot_queue_fields(
        backtest_id=bad_id, created_at=CREATED, core=_core(), signing_key=SIGNING_KEY
    )) == "backtest_id_invalid"


def test_numeric_database_alias_for_backtest_id_has_one_attestation_identity():
    fields = _fields()
    assert verify_execution_snapshot_queue_fields(
        fields,
        backtest_id=float(ROW_ID),
        created_at=CREATED,
        signing_key=SIGNING_KEY,
    ).sha256 == fields["execution_snapshot_sha256"]


@pytest.mark.parametrize("bad_time", [123, True, "123", "", " 2026-08-12T00:00:00Z", "2026-08-12", object()])
def test_creation_time_requires_one_exact_canonical_utc_string(bad_time):
    assert _error_code(lambda bad_time=bad_time: make_execution_snapshot_queue_fields(
        backtest_id=ROW_ID, created_at=bad_time, core=_core(), signing_key=SIGNING_KEY
    )) == "created_at_invalid"


def test_attestation_binds_row_id_creation_time_signer_body_digest_and_key():
    fields = _fields()
    for kwargs in [
        {"backtest_id": ROW_ID + 1, "created_at": CREATED, "signing_key": SIGNING_KEY},
        {"backtest_id": ROW_ID, "created_at": "2026-08-12T00:00:01Z", "signing_key": SIGNING_KEY},
        {"backtest_id": ROW_ID, "created_at": CREATED, "signing_key": b"different-queue-snapshot-signing-key-32b"},
    ]:
        assert _error_code(lambda kwargs=kwargs: verify_execution_snapshot_queue_fields(fields, **kwargs)) == "attestation_mismatch"


def test_expected_digest_is_strictly_typed_formatted_and_bound():
    fields = _fields()
    for bad in (object(), 1, True, "bad", "sha256:" + "F" * 64):
        assert _error_code(lambda bad=bad: verify_execution_snapshot_queue_fields(
            fields, backtest_id=ROW_ID, created_at=CREATED,
            signing_key=SIGNING_KEY, expected_sha256=bad,
        )) == "engine_binding_invalid"
    assert _error_code(lambda: verify_execution_snapshot_queue_fields(
        fields, backtest_id=ROW_ID, created_at=CREATED,
        signing_key=SIGNING_KEY, expected_sha256="sha256:" + "f" * 64,
    )) == "engine_binding_mismatch"


def test_short_nonbytes_and_oversized_signing_keys_are_rejected():
    for bad in ("x" * 32, b"short", b"x" * 4097, bytearray(b"x" * 32), None):
        assert _error_code(lambda bad=bad: make_execution_snapshot_queue_fields(
            backtest_id=ROW_ID, created_at=CREATED, core=_core(), signing_key=bad
        )) == "signing_key_unavailable"


def test_resource_budgets_reject_before_unbounded_copy_or_canonical_materialization():
    assert _error_code(lambda: canonical_execution_json([None] * 10_001)) == "json_collection_limit"
    assert _error_code(lambda: canonical_execution_json({"x": "a" * (EXECUTION_SNAPSHOT_MAX_BYTES + 1)})) in {
        "json_text_too_large", "snapshot_too_large"
    }
    assert _error_code(lambda: canonical_execution_json({"x" * 129: 1})) == "json_text_too_large"
    nested = None
    for _ in range(102):
        nested = [nested]
    assert _error_code(lambda: canonical_execution_json(nested)) == "json_depth_exceeded"


def test_schema_version_requires_exact_protocol_string():
    snapshot = build_execution_snapshot(_core())
    for version in (True, 1.0, 1, "1", 2):
        hostile = copy.deepcopy(snapshot); hostile["schema_version"] = version
        assert _error_code(lambda hostile=hostile: execution_snapshot_sha256(hostile)) == "protocol_unsupported"
