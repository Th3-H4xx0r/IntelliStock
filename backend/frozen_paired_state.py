"""Pure content-addressed contract for frozen paired-research state.

This module performs no database, provider, credential, queue, or broker work.
It only validates and hashes already-exported public manifests.  Integrations
must restore manifests into disposable stores and separately enforce read/write
and network policy before a result can be called causal.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime
from typing import Mapping, Sequence


FROZEN_STATE_PROTOCOL_VERSION = "frozen-paired-state-v1"
FROZEN_STATE_MAX_BYTES = 16 * 1024 * 1024
FROZEN_STATE_MAX_ROWS_PER_TABLE = 2_000_000
FROZEN_STATE_MAX_TABLES = 256
FROZEN_STATE_MAX_NODES = 2_000_000
FROZEN_STATE_MAX_DEPTH = 100
FROZEN_STATE_MAX_STRING_BYTES = 4 * 1024 * 1024
SAFE_INTEGER_MAX = (1 << 53) - 1

_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
_TABLE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,127}$")
# The treatment path is a typed segment tuple, not a rendered string: a flat
# key literally named "$.core...anchor_reinforce_target_pct" must never be
# accepted as the nested config value.
_ALLOWED_TREATMENT_SEGMENTS = (
    "core", "strategy", "specs", 0, "config", "anchor_reinforce_target_pct",
)
_ALLOWED_TREATMENT_PATH = "$.core.strategy.specs[0].config.anchor_reinforce_target_pct"
_TREATMENT_CONTROL_VALUE = 12
_TREATMENT_VALUE = 20
_ALLOWED_STATE_TABLES = frozenset({
    "GraphNexusDiscoveredStocks", "GraphNexusMarketTrends", "GraphNexusTickerHistory",
    "GraphNexusDiscoverySnapshots", "GraphNexusActiveEvents",
    "GraphNexusActiveEventHistory", "GraphNexusActiveEventMaintenance",
    "GraphNexusNewsCache", "GraphNexusNewsRaw", "GraphNexusNewsFinBERT",
    "GraphNexusNewsLLMCompany", "GraphNexusNewsLLMMacro", "GraphNexusNewsDayFeatures",
    "GraphNexusLLMPromptCache", "GraphNexusBenzingaCache", "GraphNexusAnalystPanel",
    "GraphNexusOverlayBarsCache", "GraphNexusOverlayResultCache",
    "GraphNexusLearningCache", "GraphNexusOutcomes", "GraphNexusOutcomeSeries",
    "GraphNexusTradeContexts", "GraphNexusTradeOutcomes", "GraphNexusRotationCooldown",
    "NexusStrategyCache", "NexusRuntimeState", "LiveState",
})
_TIMESTAMP_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z$"
)
_DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_REQUIRED_EXTERNAL = frozenset({"pit", "graph", "model", "market", "benchmark"})
_REQUIRED_ENVIRONMENT = {
    "timezone": "UTC",
    "python_hash_seed": "0",
    "nexus_backtest_snapshot_write": "off",
    "network_policy": "deny",
}


class FrozenStateError(ValueError):
    """Stable, value-free frozen-state contract failure."""

    def __init__(self, code: str, *, paths=()):
        self.code = str(code)
        self.paths = tuple(sorted({str(item)[:160] for item in paths}))[:32]
        suffix = "" if not self.paths else " paths=" + ",".join(self.paths)
        super().__init__(self.code + suffix)


def _path(parent: str, raw_key: str) -> str:
    digest = hashlib.sha256(raw_key.encode("utf-8", "surrogatepass")).hexdigest()[:10]
    return f"{parent}.[field:{digest}]"


def _normalize(value, path="$", *, depth=0, budget=None):
    if budget is None:
        budget = {"nodes": 0, "text": 0}
    budget["nodes"] += 1
    if budget["nodes"] > FROZEN_STATE_MAX_NODES:
        raise FrozenStateError("json_node_limit")
    if depth > FROZEN_STATE_MAX_DEPTH:
        raise FrozenStateError("json_depth_limit", paths=(path,))
    if value is None or type(value) is bool:
        return value
    if type(value) is int:
        if abs(value) > SAFE_INTEGER_MAX:
            raise FrozenStateError("json_integer_range", paths=(path,))
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise FrozenStateError("json_non_finite", paths=(path,))
        if value == 0 or value.is_integer():
            integer = int(value)
            if abs(integer) > SAFE_INTEGER_MAX:
                raise FrozenStateError("json_integer_range", paths=(path,))
            return integer
        return value
    if type(value) is str:
        if len(value) > FROZEN_STATE_MAX_STRING_BYTES:
            raise FrozenStateError("json_text_limit", paths=(path,))
        if any(0xD800 <= ord(char) <= 0xDFFF for char in value):
            raise FrozenStateError("json_invalid_unicode", paths=(path,))
        raw = value.encode("utf-8")
        if len(raw) > FROZEN_STATE_MAX_STRING_BYTES:
            raise FrozenStateError("json_text_limit", paths=(path,))
        budget["text"] += len(raw)
        if budget["text"] > FROZEN_STATE_MAX_BYTES:
            raise FrozenStateError("manifest_too_large")
        return value
    if type(value) is list:
        return [
            _normalize(item, f"{path}[{index}]", depth=depth + 1, budget=budget)
            for index, item in enumerate(value)
        ]
    if type(value) is dict:
        normalized = {}
        for raw_key, item in value.items():
            if type(raw_key) is not str:
                raise FrozenStateError("json_non_string_key", paths=(path,))
            if any(0xD800 <= ord(char) <= 0xDFFF for char in raw_key):
                raise FrozenStateError("json_invalid_unicode", paths=(path,))
            if len(raw_key.encode("utf-8")) > 256:
                raise FrozenStateError("json_key_limit", paths=(path,))
            normalized[raw_key] = _normalize(
                item, _path(path, raw_key), depth=depth + 1, budget=budget
            )
        return normalized
    raise FrozenStateError("json_type_forbidden", paths=(path,))


def canonical_state_json(value) -> str:
    """Return deterministic, DB-numeric-stable JSON for a bounded value."""
    normalized = _normalize(value)
    try:
        result = json.dumps(
            normalized, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False,
        )
    except (TypeError, ValueError, UnicodeError):
        raise FrozenStateError("json_canonicalization_failed") from None
    try:
        encoded = result.encode("utf-8")
    except UnicodeError:
        raise FrozenStateError("json_invalid_unicode") from None
    if len(encoded) > FROZEN_STATE_MAX_BYTES:
        raise FrozenStateError("manifest_too_large")
    return result


def _sha256(value) -> str:
    return "sha256:" + hashlib.sha256(
        canonical_state_json(value).encode("utf-8")
    ).hexdigest()


def _identifier(value, path):
    if type(value) is not str or not _ID_RE.fullmatch(value):
        raise FrozenStateError("identity_invalid", paths=(path,))
    return value


def _digest(value, path):
    if type(value) is not str or not _DIGEST_RE.fullmatch(value):
        raise FrozenStateError("digest_invalid", paths=(path,))
    return value


def _exact(value, keys, code, path):
    if type(value) is not dict or set(value) != set(keys):
        raise FrozenStateError(code, paths=(path,))


def _primary_key(row, key_fields, path):
    try:
        return [row[name] for name in key_fields]
    except (KeyError, TypeError):
        raise FrozenStateError("state_primary_key_missing", paths=(path,)) from None


def state_rows_sha256(rows: Sequence[Mapping], *, key_fields=("id",)) -> str:
    """Hash exact rows in canonical primary-key order.

    Ordering is derived from canonical JSON of a non-empty, explicit primary
    key. Duplicate normalized keys fail closed. Rows are not sanitized: callers
    must export a deliberately approved decision-state projection.
    """
    if type(rows) not in (list, tuple) or len(rows) > FROZEN_STATE_MAX_ROWS_PER_TABLE:
        raise FrozenStateError("state_rows_invalid")
    if (type(key_fields) not in (list, tuple) or not key_fields
            or any(type(name) is not str or not name for name in key_fields)):
        raise FrozenStateError("state_key_fields_invalid")
    normalized = []
    seen = set()
    for index, row in enumerate(rows):
        if type(row) is not dict:
            raise FrozenStateError("state_row_invalid", paths=(f"$[{index}]",))
        clean = _normalize(row, f"$[{index}]")
        key = _primary_key(clean, key_fields, f"$[{index}]")
        key_json = canonical_state_json(key)
        if key_json in seen:
            raise FrozenStateError("state_primary_key_duplicate")
        seen.add(key_json)
        normalized.append((key_json, clean))
    normalized.sort(key=lambda pair: pair[0])
    return _sha256({
        "version": "frozen-state-rows-v1",
        "key_fields": list(key_fields),
        "rows": [row for _key, row in normalized],
    })


def _diff(left, right, path=()):
    """Return typed-segment differences; string keys and indexes never merge."""
    if type(left) is not type(right):
        return [(path, left, right)]
    if type(left) is dict:
        differences = []
        for key in sorted(set(left) | set(right)):
            child = path + (key,)
            if key not in left:
                differences.append((child, None, right[key]))
            elif key not in right:
                differences.append((child, left[key], None))
            else:
                differences.extend(_diff(left[key], right[key], child))
            if len(differences) > 8:
                return differences
        return differences
    if type(left) is list:
        if len(left) != len(right):
            return [(path, left, right)]
        differences = []
        for index, (l_item, r_item) in enumerate(zip(left, right)):
            differences.extend(_diff(l_item, r_item, path + (index,)))
            if len(differences) > 8:
                return differences
        return differences
    return [] if left == right else [(path, left, right)]


def compare_execution_snapshots(control: Mapping, treatment: Mapping) -> dict:
    """Require exactly the preregistered target 12 -> 20 difference.

    The treatment path and both values are fixed protocol constants.  A caller
    cannot redefine the experiment, and no error echoes snapshot keys/values.
    """
    if type(control) is not dict or type(treatment) is not dict:
        raise FrozenStateError("execution_snapshot_invalid")
    left = _normalize(control)
    right = _normalize(treatment)
    differences = _diff(left, right)
    if len(differences) != 1 or differences[0][0] != _ALLOWED_TREATMENT_SEGMENTS:
        raise FrozenStateError("execution_snapshot_diff_invalid")
    before, after = differences[0][1], differences[0][2]
    if (type(before) is not int or type(after) is not int
            or before != _TREATMENT_CONTROL_VALUE or after != _TREATMENT_VALUE):
        raise FrozenStateError("treatment_values_invalid")
    return {
        "path": _ALLOWED_TREATMENT_PATH,
        "control": before,
        "treatment": after,
        "control_sha256": _sha256(left),
        "treatment_sha256": _sha256(right),
    }


def _validate_table(table, name):
    # Paths are hashed: a hostile export must not be able to smuggle a table
    # name (or any raw value) into an error string.
    path = _path("$.state.tables", name)
    _exact(table, {"key_fields", "row_count", "rows_sha256", "write_policy"},
           "state_table_invalid", path)
    key_fields = table["key_fields"]
    if (type(key_fields) is not list or not 1 <= len(key_fields) <= 8
            or any(type(item) is not str or not _TABLE_RE.fullmatch(item)
                   for item in key_fields)
            or len(set(key_fields)) != len(key_fields)):
        raise FrozenStateError("state_key_fields_invalid", paths=(path,))
    if (type(table["row_count"]) is not int or table["row_count"] < 0
            or table["row_count"] > FROZEN_STATE_MAX_ROWS_PER_TABLE):
        raise FrozenStateError("state_row_count_invalid", paths=(path,))
    _digest(table["rows_sha256"], path)
    policy = table["write_policy"]
    if type(policy) is not str or policy not in {"read_only", "arm_local"}:
        raise FrozenStateError("state_write_policy_invalid", paths=(path,))


def _timestamp(value, path):
    if type(value) is not str or not _TIMESTAMP_RE.fullmatch(value):
        raise FrozenStateError("timestamp_invalid", paths=(path,))
    return value


def _date(value, path):
    if type(value) is not str or not _DATE_RE.fullmatch(value):
        raise FrozenStateError("window_invalid", paths=(path,))
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        raise FrozenStateError("window_invalid", paths=(path,)) from None


def _validate_external(external):
    _exact(external, _REQUIRED_EXTERNAL, "external_manifest_invalid", "$.external")
    for name in sorted(_REQUIRED_EXTERNAL):
        item = external[name]
        _exact(item, {"artifact_id", "sha256", "mode"},
               "external_artifact_invalid", f"$.external.{name}")
        _identifier(item["artifact_id"], f"$.external.{name}.artifact_id")
        _digest(item["sha256"], f"$.external.{name}.sha256")
        expected = "replay_only" if name in {"pit", "graph", "model", "market"} else "read_only"
        if item["mode"] != expected:
            raise FrozenStateError("external_artifact_mode_invalid", paths=(f"$.external.{name}",))


def verify_frozen_paired_state_manifest(manifest: Mapping) -> dict:
    """Validate an arm-neutral, content-addressed paired-state manifest."""
    if type(manifest) is not dict:
        raise FrozenStateError("manifest_invalid")
    normalized = _normalize(manifest)
    _exact(normalized, {
        "protocol_version", "pair_id", "window", "logical_identity", "execution",
        "state", "external", "clock", "runtime", "isolation", "bundle_sha256",
    }, "manifest_shape_invalid", "$")
    if normalized["protocol_version"] != FROZEN_STATE_PROTOCOL_VERSION:
        raise FrozenStateError("protocol_unsupported")
    _identifier(normalized["pair_id"], "$.pair_id")

    window = normalized["window"]
    _exact(window, {"start", "end", "baseline_cutoff"}, "window_invalid", "$.window")
    start = _date(window["start"], "$.window.start")
    end = _date(window["end"], "$.window.end")
    cutoff = _timestamp(window["baseline_cutoff"], "$.window.baseline_cutoff")
    if start >= end or (end - start).days > 3653:
        raise FrozenStateError("window_invalid", paths=("$.window",))
    # The neutral baseline must be sealed strictly BEFORE the measured window;
    # a cutoff inside the window would leak treated state into both arms.
    if cutoff[:10] >= window["start"]:
        raise FrozenStateError("baseline_cutoff_invalid", paths=("$.window",))

    logical = normalized["logical_identity"]
    _exact(logical, {"base_instance_id", "history_scope_id", "history_scope_doc_sha256"},
           "logical_identity_invalid", "$.logical_identity")
    _identifier(logical["base_instance_id"], "$.logical_identity.base_instance_id")
    _identifier(logical["history_scope_id"], "$.logical_identity.history_scope_id")
    _digest(logical["history_scope_doc_sha256"], "$.logical_identity.history_scope_doc_sha256")

    execution = normalized["execution"]
    _exact(execution, {
        "common_snapshot_sha256", "control_snapshot_sha256", "treatment_snapshot_sha256",
        "allowed_diff", "source_tree_sha256", "image_digest",
        "dependency_runtime_sha256", "seed",
    }, "execution_manifest_invalid", "$.execution")
    for name in ("common_snapshot_sha256", "control_snapshot_sha256",
                 "treatment_snapshot_sha256", "source_tree_sha256",
                 "image_digest", "dependency_runtime_sha256"):
        _digest(execution[name], f"$.execution.{name}")
    if type(execution["seed"]) is not int or not 0 <= execution["seed"] <= 0x7FFFFFFF:
        raise FrozenStateError("seed_invalid")
    allowed = execution["allowed_diff"]
    _exact(allowed, {"path", "control", "treatment"}, "allowed_diff_invalid", "$.execution.allowed_diff")
    if (allowed["path"] != _ALLOWED_TREATMENT_PATH
            or allowed["control"] != 12 or allowed["treatment"] != 20):
        raise FrozenStateError("allowed_diff_invalid")

    state = normalized["state"]
    _exact(state, {"tables", "runtime_state_sha256"}, "state_manifest_invalid", "$.state")
    tables = state["tables"]
    if type(tables) is not dict or not tables or len(tables) > FROZEN_STATE_MAX_TABLES:
        raise FrozenStateError("state_tables_invalid")
    for name, table in tables.items():
        # An arbitrary table name would make the allowlist meaningless: a
        # forgotten decision-state table could be silently excluded/renamed.
        if type(name) is not str or name not in _ALLOWED_STATE_TABLES:
            raise FrozenStateError("state_table_name_invalid")
        _validate_table(table, name)
    _digest(state["runtime_state_sha256"], "$.state.runtime_state_sha256")
    _validate_external(normalized["external"])

    clock = normalized["clock"]
    _exact(clock, {"wall_time", "timezone", "market_calendar"}, "clock_invalid", "$.clock")
    _timestamp(clock["wall_time"], "$.clock.wall_time")
    if clock["timezone"] != "UTC" or clock["market_calendar"] != "XNYS":
        raise FrozenStateError("clock_invalid")
    if clock["wall_time"] != window["baseline_cutoff"]:
        raise FrozenStateError("clock_invalid")

    runtime = normalized["runtime"]
    _exact(runtime, {"environment"}, "runtime_invalid", "$.runtime")
    if runtime["environment"] != _REQUIRED_ENVIRONMENT:
        raise FrozenStateError("runtime_environment_invalid")

    isolation = normalized["isolation"]
    _exact(isolation, {
        "rethinkdb", "neo4j", "production_db_unreachable",
        "production_graph_unreachable", "external_network_unreachable",
    }, "isolation_invalid", "$.isolation")
    neo4j = isolation["neo4j"]
    if (isolation["rethinkdb"] != "disposable_per_arm"
            or type(neo4j) is not str
            or neo4j not in {"disposable_per_arm", "sealed_replay"}
            or any(isolation[name] is not True for name in (
                "production_db_unreachable", "production_graph_unreachable",
                "external_network_unreachable"))):
        raise FrozenStateError("isolation_invalid")

    supplied = normalized["bundle_sha256"]
    _digest(supplied, "$.bundle_sha256")
    identity = dict(normalized)
    identity.pop("bundle_sha256")
    actual = _sha256(identity)
    if supplied != actual:
        raise FrozenStateError("bundle_hash_mismatch")
    return normalized


def build_frozen_paired_state_manifest(core: Mapping) -> dict:
    """Attach and verify the canonical bundle hash for an explicit core."""
    if type(core) is not dict or "bundle_sha256" in core:
        raise FrozenStateError("manifest_core_invalid")
    normalized = _normalize(core)
    candidate = dict(normalized)
    candidate["bundle_sha256"] = _sha256(normalized)
    return verify_frozen_paired_state_manifest(candidate)


_NEGATIVE_CONTROL_DIGEST_FIELDS = frozenset({
    "execution_snapshot_sha256", "bundle_sha256", "restore_receipt_sha256",
    "trade_ledger_sha256", "decision_ledger_sha256", "order_ledger_sha256",
    "fills_sha256", "nav_series_sha256", "position_series_sha256",
    "treatment_exposure_ledger_sha256", "runtime_state_first_sha256",
    "runtime_state_last_sha256", "write_set_sha256", "pit_occurrences_sha256",
    "model_occurrences_sha256", "graph_occurrences_sha256",
    "market_occurrences_sha256", "terminal_summary_sha256",
    "accounting_audit_sha256", "benchmark_audit_sha256",
    "shared_store_before_sha256", "shared_store_after_sha256",
})
_NEGATIVE_CONTROL_FLAG_FIELDS = frozenset({
    "target_pct", "complete", "audits_complete", "provider_fallback_used",
    "undeclared_read_occurred",
})
_NEGATIVE_CONTROL_FIELDS = _NEGATIVE_CONTROL_DIGEST_FIELDS | _NEGATIVE_CONTROL_FLAG_FIELDS


def verify_negative_control_receipts(left: Mapping, right: Mapping) -> dict:
    """Require exact equality of every preregistered target-12 replicate artifact.

    Equality here is the determinism gate for the later 12 -> 20 study: any
    unequal artifact is a determinism defect, not a tolerable difference.
    """
    if type(left) is not dict or type(right) is not dict:
        raise FrozenStateError("negative_control_invalid")
    _exact(left, _NEGATIVE_CONTROL_FIELDS, "negative_control_invalid", "$.left")
    _exact(right, _NEGATIVE_CONTROL_FIELDS, "negative_control_invalid", "$.right")
    for side, receipt in (("left", left), ("right", right)):
        for name in sorted(_NEGATIVE_CONTROL_DIGEST_FIELDS):
            _digest(receipt[name], f"$.{side}.{name}")
        # `12.0`/`True` must not pass for the control dose.
        if (type(receipt["target_pct"]) is not int
                or receipt["target_pct"] != _TREATMENT_CONTROL_VALUE
                or receipt["complete"] is not True
                or receipt["audits_complete"] is not True
                or receipt["provider_fallback_used"] is not False
                or receipt["undeclared_read_occurred"] is not False):
            raise FrozenStateError("negative_control_invalid", paths=(f"$.{side}",))
        if receipt["shared_store_before_sha256"] != receipt["shared_store_after_sha256"]:
            raise FrozenStateError("shared_store_mutated", paths=(f"$.{side}",))
    differences = [name for name in sorted(_NEGATIVE_CONTROL_FIELDS)
                   if left[name] != right[name]]
    if differences:
        raise FrozenStateError("negative_control_mismatch", paths=tuple(differences))
    return {
        "status": "identical",
        "target_pct": _TREATMENT_CONTROL_VALUE,
        "artifact_set_sha256": _sha256(left),
    }


__all__ = [
    "FROZEN_STATE_PROTOCOL_VERSION", "FrozenStateError",
    "build_frozen_paired_state_manifest", "canonical_state_json",
    "compare_execution_snapshots", "state_rows_sha256",
    "verify_frozen_paired_state_manifest", "verify_negative_control_receipts",
]
