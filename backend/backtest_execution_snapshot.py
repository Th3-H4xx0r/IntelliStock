"""Pure authenticated contract for queue-time backtest execution snapshots.

This module performs no database, provider, credential, or strategy work.  V1
is intentionally narrow and default-inert: only an explicit, positively
validated equity/Graph-Nexus public projection can be signed.  Integrations
must execute the verified snapshot; comparing it with mutable current rows is
only drift telemetry, never authority.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Mapping
from urllib.parse import parse_qsl, urlsplit

from persistence_safety import REDACTION_MARKER, assert_secret_free, sanitize_snapshot


EXECUTION_SNAPSHOT_MODE = "execute"
EXECUTION_SNAPSHOT_SCHEMA_VERSION = "queue-execution-snapshot-v1"
EXECUTION_SNAPSHOT_SIGNER = "queue-snapshot-hmac-v1"
EXECUTION_SNAPSHOT_MAX_BYTES = 512 * 1024

_MAX_NODES = 50_000
_MAX_COLLECTION_ITEMS = 10_000
_MAX_KEY_BYTES = 128
_MAX_STRING_BYTES = 256 * 1024
_MAX_INPUT_TEXT_BYTES = EXECUTION_SNAPSHOT_MAX_BYTES
_MAX_SIGNING_KEY_BYTES = 4096
_SAFE_INTEGER_MAX = (1 << 53) - 1

_SNAPSHOT_FIELDS = frozenset({
    "execution_snapshot_mode",
    "execution_snapshot",
    "execution_snapshot_sha256",
    "execution_snapshot_hmac_sha256",
    "execution_snapshot_signer",
})
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_HMAC_RE = re.compile(r"^hmac-sha256:[0-9a-f]{64}$")
_CREATED_AT_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z$"
)
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9._-]{0,31}$")
_MODEL_TEXT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+\-]{0,255}$")
_PROVIDER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
_SECRETISH_COMPACT_KEY_PARTS = (
    "apikey", "secret", "token", "password", "passwd", "credential",
    "authorization", "bearer", "accesscode", "ciphertext", "fernet",
)
_SECRETISH_QUERY_NAMES = frozenset({
    "access_code", "api_key", "apikey", "authorization", "bearer",
    "client_secret", "code", "key", "password", "passwd", "secret", "token",
})
_CANDIDATE_OVERRIDE_KEYS = frozenset({
    "regime_position_cap_recovery_hard_enforce",
    "circuit_breaker_regime_adjustment_semantics_v2",
    "momentum_breakout_max_nav_pct_by_regime",
    "deployment_ramp_caps_by_regime",
})
_SPEC_CONFIG_FIELDS = frozenset({
    "anchor_reinforce_enabled",
    "anchor_reinforce_execution_core_floor_enabled",
    "anchor_reinforce_execution_enabled",
    "anchor_reinforce_execution_max_position_pct",
    "anchor_reinforce_execution_turnover_ceiling_pct",
    "anchor_reinforce_target_pct",
    "llm_model_id",
    "llm_provider",
    "llm_model",
    "model_name",
    "pit_mode",
})
_ADAPTER_FIELDS = frozenset({
    "openai_base_url", "nvidia_base_url", "azure_openai_endpoint",
    "azure_openai_api_version", "reasoning_effort", "cli_path", "extra_args",
    "ollama_base_url", "ollama_keep_alive", "ollama_think", "bedrock_region",
    "bedrock_reasoning", "openrouter_base_url", "openrouter_referer",
    "openrouter_title", "model_cache_family", "input_cost_per_1m",
    "output_cost_per_1m", "cache_creation_cost_per_1m", "cache_read_cost_per_1m",
})


class ExecutionSnapshotError(ValueError):
    """A stable, value-free snapshot contract failure."""

    def __init__(self, code: str, *, paths=()):
        self.code = str(code)
        self.paths = tuple(sorted({str(path)[:160] for path in paths}))[:32]
        suffix = "" if not self.paths else " paths=" + ",".join(self.paths)
        super().__init__(self.code + suffix)


@dataclass(frozen=True)
class VerifiedExecutionSnapshot:
    """Immutable authenticated bytes; every materialization is a fresh copy."""

    canonical_json: str
    sha256: str
    signer: str

    @property
    def snapshot(self) -> dict:
        return json.loads(self.canonical_json)


@dataclass
class _Budget:
    nodes: int = 0
    text_bytes: int = 0


def _safe_path(path: str, key: str) -> str:
    digest = hashlib.sha256(key.encode("utf-8", "surrogatepass")).hexdigest()[:10]
    return f"{path}.[field:{digest}]"


def _strict_text(value: str, path: str, budget: _Budget, *, key=False) -> str:
    if type(value) is not str:
        raise ExecutionSnapshotError("json_type_forbidden", paths=(path,))
    limit = _MAX_KEY_BYTES if key else _MAX_STRING_BYTES
    # UTF-8 bytes are never fewer than Python code points. Reject obvious
    # oversize input before a full scan/encoding allocation.
    if len(value) > limit:
        raise ExecutionSnapshotError("json_text_too_large", paths=(path,))
    if any(0xD800 <= ord(char) <= 0xDFFF for char in value):
        raise ExecutionSnapshotError("json_invalid_unicode", paths=(path,))
    encoded = value.encode("utf-8")
    if len(encoded) > limit:
        raise ExecutionSnapshotError("json_text_too_large", paths=(path,))
    budget.text_bytes += len(encoded)
    if budget.text_bytes > _MAX_INPUT_TEXT_BYTES:
        raise ExecutionSnapshotError("snapshot_too_large")
    return value


def _normalize_strict_json(value, path="$", *, _depth=0, _budget=None):
    if _budget is None:
        _budget = _Budget()
    _budget.nodes += 1
    if _budget.nodes > _MAX_NODES:
        raise ExecutionSnapshotError("json_node_limit")
    if _depth > 100:
        raise ExecutionSnapshotError("json_depth_exceeded", paths=(path,))
    if value is None or type(value) is bool:
        return value
    if type(value) is str:
        return _strict_text(value, path, _budget)
    if type(value) is int:
        if abs(value) > _SAFE_INTEGER_MAX:
            raise ExecutionSnapshotError("json_integer_out_of_range", paths=(path,))
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ExecutionSnapshotError("json_non_finite", paths=(path,))
        # RethinkDB has one numeric datum type. Integral floats and ints must
        # share one authenticated representation to survive a queue round trip.
        if value == 0 or value.is_integer():
            integer = int(value)
            if abs(integer) > _SAFE_INTEGER_MAX:
                raise ExecutionSnapshotError("json_integer_out_of_range", paths=(path,))
            return integer
        return value
    if type(value) is list:
        if len(value) > _MAX_COLLECTION_ITEMS:
            raise ExecutionSnapshotError("json_collection_limit", paths=(path,))
        return [
            _normalize_strict_json(item, f"{path}[{index}]", _depth=_depth + 1, _budget=_budget)
            for index, item in enumerate(value)
        ]
    if type(value) is dict:
        if len(value) > _MAX_COLLECTION_ITEMS:
            raise ExecutionSnapshotError("json_collection_limit", paths=(path,))
        normalized = {}
        for key, item in value.items():
            if type(key) is not str:
                raise ExecutionSnapshotError("json_non_string_key", paths=(path,))
            # Validate/cap the raw key before hashing it into a safe path.
            clean_key = _strict_text(key, f"{path}.[field]", _budget, key=True)
            child = _safe_path(path, clean_key)
            normalized[clean_key] = _normalize_strict_json(
                item, child, _depth=_depth + 1, _budget=_budget)
        return normalized
    raise ExecutionSnapshotError("json_type_forbidden", paths=(path,))


def canonical_execution_json(value) -> str:
    """Return deterministic DB-stable JSON for a bounded strict JSON value."""
    normalized = _normalize_strict_json(value)
    try:
        return json.dumps(
            normalized,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError, UnicodeError):
        raise ExecutionSnapshotError("json_canonicalization_failed") from None


def _exact_keys(value, expected, code, path):
    if type(value) is not dict or set(value) != set(expected):
        raise ExecutionSnapshotError(code, paths=(path,))


def _identifier(value, path):
    if type(value) is not str or not _ID_RE.fullmatch(value):
        raise ExecutionSnapshotError("schema_identity_invalid", paths=(path,))
    return value


def _number(value, path, *, minimum=None, maximum=None, allow_none=False):
    if value is None and allow_none:
        return None
    if type(value) not in (int, float) or not math.isfinite(float(value)):
        raise ExecutionSnapshotError("schema_number_invalid", paths=(path,))
    number = float(value)
    if minimum is not None and number < minimum:
        raise ExecutionSnapshotError("schema_number_invalid", paths=(path,))
    if maximum is not None and number > maximum:
        raise ExecutionSnapshotError("schema_number_invalid", paths=(path,))
    return value


def _public_url(value, path, *, allow_none=True):
    if value is None and allow_none:
        return
    if type(value) is not str or not value or value != value.strip() or len(value) > 2048:
        raise ExecutionSnapshotError("schema_url_invalid", paths=(path,))
    try:
        parsed = urlsplit(value)
    except Exception:
        raise ExecutionSnapshotError("schema_url_invalid", paths=(path,)) from None
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ExecutionSnapshotError("schema_url_invalid", paths=(path,))
    if (parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment):
        raise ExecutionSnapshotError("secretish_url_forbidden", paths=(path,))


def _iter_public_projection(value, path="$"):
    if type(value) is dict:
        if (value == REDACTION_MARKER
                or (value.get("redacted") is True and value.get("source") == "runtime_secret")):
            raise ExecutionSnapshotError("redaction_marker_forbidden", paths=(path,))
        for key, item in value.items():
            child = _safe_path(path, key)
            lowered = key.strip().lower()
            compact = re.sub(r"[^a-z0-9]", "", lowered)
            segments = tuple(part for part in re.split(r"[^a-z0-9]+", lowered) if part)
            if (any(part in compact for part in _SECRETISH_COMPACT_KEY_PARTS)
                    or "key" in segments
                    or lowered == "secret_ref"):
                raise ExecutionSnapshotError("secretish_field_forbidden", paths=(child,))
            _iter_public_projection(item, child)
        return
    if type(value) is list:
        for index, item in enumerate(value):
            _iter_public_projection(item, f"{path}[{index}]")
        return
    if type(value) is not str:
        return
    lowered = value.strip().lower()
    if ("raw_secret_material" in lowered
            or re.search(r"(?i)(?:fernet:\s*)?gAAAAA[A-Za-z0-9_-]{8,}", value)
            or lowered.startswith("env:")
            or "private key-----" in lowered):
        raise ExecutionSnapshotError("secretish_value_forbidden", paths=(path,))
    if "://" in value or value.startswith("//"):
        try:
            parsed = urlsplit(value)
            if parsed.username is not None or parsed.password is not None:
                raise ExecutionSnapshotError("secretish_url_forbidden", paths=(path,))
            for name, _ in parse_qsl(parsed.query, keep_blank_values=True):
                if name.strip().lower() in _SECRETISH_QUERY_NAMES:
                    raise ExecutionSnapshotError("secretish_url_forbidden", paths=(path,))
            if parsed.fragment and any(part in parsed.fragment.lower() for part in _SECRETISH_QUERY_NAMES):
                raise ExecutionSnapshotError("secretish_url_forbidden", paths=(path,))
        except ExecutionSnapshotError:
            raise
        except Exception:
            raise ExecutionSnapshotError("url_parse_failed", paths=(path,)) from None


def _validate_evidence(value, path):
    expected = {
        "evidence_mode", "fixture_build_id", "replay_fixture_id",
        "matrix_manifest_id", "matrix_arm_id", "cost_scenario_id",
        "equity_total_cost_bps", "nexus_candidate_overrides", "fixture_ordinal",
        "pit_mode",
    }
    _exact_keys(value, expected, "schema_evidence_invalid", path)
    try:
        from backtest_evidence_options import validate_evidence_options
        validated = validate_evidence_options(value)
    except Exception:
        raise ExecutionSnapshotError("schema_evidence_invalid", paths=(path,)) from None
    if validated != value:
        raise ExecutionSnapshotError("schema_evidence_invalid", paths=(path,))
    patterns = {
        "matrix_manifest_id": re.compile(r"^matrix-sha256-[0-9a-f]{64}$"),
        "matrix_arm_id": re.compile(r"^arm-sha256-[0-9a-f]{64}$"),
        "replay_fixture_id": re.compile(r"^fixture-sha256-[0-9a-f]{64}$"),
        "fixture_build_id": re.compile(r"^build-[A-Za-z0-9._-]{1,64}$"),
    }
    for name, pattern in patterns.items():
        item = value[name]
        if item is not None and (type(item) is not str or not pattern.fullmatch(item)):
            raise ExecutionSnapshotError("schema_evidence_invalid", paths=(path,))
    if value["cost_scenario_id"] is not None and value["cost_scenario_id"] not in {"base", "25bps", "50bps"}:
        raise ExecutionSnapshotError("schema_evidence_invalid", paths=(path,))
    expected_cost = {"base": None, "25bps": 25.0, "50bps": 50.0}.get(
        value["cost_scenario_id"])
    if value["cost_scenario_id"] is not None and value["equity_total_cost_bps"] != expected_cost:
        raise ExecutionSnapshotError("schema_evidence_cost_mismatch", paths=(path,))


def _validate_config(config, path):
    if type(config) is not dict or not config or not set(config).issubset(_SPEC_CONFIG_FIELDS):
        raise ExecutionSnapshotError("schema_strategy_config_invalid", paths=(path,))
    required = {"anchor_reinforce_execution_enabled", "anchor_reinforce_target_pct", "pit_mode"}
    if not required.issubset(config):
        raise ExecutionSnapshotError("schema_strategy_config_invalid", paths=(path,))
    for name in ("anchor_reinforce_enabled", "anchor_reinforce_execution_core_floor_enabled", "anchor_reinforce_execution_enabled"):
        if name in config and type(config[name]) is not bool:
            raise ExecutionSnapshotError("schema_strategy_config_invalid", paths=(path,))
    for name in ("anchor_reinforce_target_pct", "anchor_reinforce_execution_max_position_pct"):
        if name in config:
            _number(config[name], path, minimum=0, maximum=100)
    if "anchor_reinforce_execution_turnover_ceiling_pct" in config:
        _number(config["anchor_reinforce_execution_turnover_ceiling_pct"], path, minimum=0, maximum=1)
    if config["pit_mode"] not in {"strict", "research"}:
        raise ExecutionSnapshotError("schema_strategy_config_invalid", paths=(path,))
    for name in ("llm_model_id", "llm_provider", "llm_model", "model_name"):
        if name in config:
            pattern = _ID_RE if name == "llm_model_id" else (_PROVIDER_RE if name == "llm_provider" else _MODEL_TEXT_RE)
            if type(config[name]) is not str or not pattern.fullmatch(config[name]):
                raise ExecutionSnapshotError("schema_strategy_config_invalid", paths=(path,))


def _validate_snapshot_v1(core):
    _exact_keys(core, {"run", "instance", "strategy", "models", "broker_access", "runtime"}, "schema_core_invalid", "$.core")
    run = core["run"]
    _exact_keys(run, {"instance_id", "symbol_mode", "symbols", "start_date", "end_date", "granularity_sec", "initial_cash", "fee", "seed", "evidence"}, "schema_run_invalid", "$.core.run")
    _identifier(run["instance_id"], "$.core.run.instance_id")
    if (run["symbol_mode"] not in {"explicit", "discovery"}
            or type(run["symbols"]) is not list
            or len(run["symbols"]) > 256
            or any(type(s) is not str or not _SYMBOL_RE.fullmatch(s) for s in run["symbols"])
            or (run["symbol_mode"] == "explicit" and not run["symbols"])
            or (run["symbol_mode"] == "discovery" and bool(run["symbols"]))):
        raise ExecutionSnapshotError("schema_symbols_invalid", paths=("$.core.run.symbols",))
    try:
        start = datetime.strptime(run["start_date"], "%Y-%m-%d").date()
        end = datetime.strptime(run["end_date"], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        raise ExecutionSnapshotError("schema_window_invalid", paths=("$.core.run",)) from None
    if start > end or (end - start).days > 3653:
        raise ExecutionSnapshotError("schema_window_invalid", paths=("$.core.run",))
    if type(run["granularity_sec"]) is not int or not 1 <= run["granularity_sec"] <= 86400:
        raise ExecutionSnapshotError("schema_granularity_invalid", paths=("$.core.run.granularity_sec",))
    user_slots = (
        ((end - start).days + 1) * 86400 + run["granularity_sec"] - 1
    ) // run["granularity_sec"]
    warmup_slots = max(
        700,
        (90 * 86400 + run["granularity_sec"] - 1) // run["granularity_sec"],
    )
    # This narrow v1 omits discovery controls, so executing the snapshot uses
    # Graph Nexus's source-pinned default fanout of 90 discovered stocks even
    # with explicit seed symbols. Count that expansion conservatively.
    theoretical_slots = (user_slots + warmup_slots) * (len(run["symbols"]) + 90)
    if theoretical_slots > 5_000_000:
        raise ExecutionSnapshotError("schema_workload_limit", paths=("$.core.run",))
    _number(run["initial_cash"], "$.core.run.initial_cash", minimum=0.01, maximum=1e12)
    fee = run["fee"]
    _exact_keys(fee, {"emulated", "requested_venue", "resolved_venue", "taker_rate"}, "schema_fee_invalid", "$.core.run.fee")
    if (fee["emulated"] is not False
            or fee["requested_venue"] != "default"
            or fee["resolved_venue"] != "alpaca"
            or type(fee["taker_rate"]) not in (int, float)
            or float(fee["taker_rate"]) != 0.0):
        raise ExecutionSnapshotError("schema_fee_invalid", paths=("$.core.run.fee",))
    seed = run["seed"]
    _exact_keys(seed, {"algorithm", "value", "python_hash_seed"}, "schema_seed_invalid", "$.core.run.seed")
    if seed["algorithm"] != "intellistock-backtest-v1" or type(seed["value"]) is not int or not 0 <= seed["value"] <= 0x7FFFFFFF or seed["python_hash_seed"] != "0":
        raise ExecutionSnapshotError("schema_seed_invalid", paths=("$.core.run.seed",))
    _validate_evidence(run["evidence"], "$.core.run.evidence")

    instance = core["instance"]
    _exact_keys(instance, {"record_id", "kind", "strategy_record_id"}, "schema_instance_invalid", "$.core.instance")
    if instance["kind"] != "equity":
        raise ExecutionSnapshotError("non_equity_forbidden", paths=("$.core.instance.kind",))
    if _identifier(instance["record_id"], "$.core.instance.record_id") != run["instance_id"]:
        raise ExecutionSnapshotError("schema_instance_invalid", paths=("$.core.instance.record_id",))
    _identifier(instance["strategy_record_id"], "$.core.instance.strategy_record_id")

    strategy = core["strategy"]
    _exact_keys(strategy, {"record_id", "name", "experiment_spec", "specs"}, "schema_strategy_invalid", "$.core.strategy")
    if _identifier(strategy["record_id"], "$.core.strategy.record_id") != instance["strategy_record_id"]:
        raise ExecutionSnapshotError("schema_strategy_invalid", paths=("$.core.strategy.record_id",))
    if (strategy["name"] != "Nexus Only"
            or strategy["experiment_spec"] is not None):
        raise ExecutionSnapshotError("schema_strategy_invalid", paths=("$.core.strategy",))
    specs = strategy["specs"]
    if type(specs) is not list or len(specs) != 1:
        raise ExecutionSnapshotError("schema_strategy_invalid", paths=("$.core.strategy.specs",))
    model_ids = set()
    for index, spec in enumerate(specs):
        path = f"$.core.strategy.specs[{index}]"
        _exact_keys(spec, {"ordinal", "strategy", "weight", "execution_position", "decision_phase", "execution_scope", "conditions", "config"}, "schema_strategy_spec_invalid", path)
        if type(spec["ordinal"]) is not int or spec["ordinal"] != index or spec["strategy"] != "graph_nexus_analysis":
            raise ExecutionSnapshotError("schema_strategy_spec_invalid", paths=(path,))
        _number(spec["weight"], path, minimum=0, maximum=1)
        if type(spec["execution_position"]) is not int or spec["decision_phase"] not in {"pre", "post"} or spec["execution_scope"] not in {"run_once", "per_symbol"} or spec["conditions"] != {}:
            raise ExecutionSnapshotError("schema_strategy_spec_invalid", paths=(path,))
        _validate_config(spec["config"], path + ".config")
        if spec["config"]["pit_mode"] != run["evidence"]["pit_mode"]:
            raise ExecutionSnapshotError("schema_pit_mismatch", paths=(path + ".config",))
        model_fields = {"llm_model_id", "llm_provider", "llm_model", "model_name"}
        present_model_fields = model_fields.intersection(spec["config"])
        if present_model_fields != model_fields:
            raise ExecutionSnapshotError("schema_model_binding_mismatch", paths=(path + ".config",))
        model_ids.add(spec["config"]["llm_model_id"])

    if run["evidence"]["nexus_candidate_overrides"]:
        # V1's narrow executable config schema has no override projection yet;
        # accepting metadata-only overrides would sign a false execution claim.
        raise ExecutionSnapshotError("schema_evidence_effective_mismatch", paths=("$.core.run.evidence",))

    models = core["models"]
    if type(models) is not list or len(models) != len(model_ids):
        raise ExecutionSnapshotError("schema_models_invalid", paths=("$.core.models",))
    seen_models = set()
    for index, model in enumerate(models):
        path = f"$.core.models[{index}]"
        _exact_keys(model, {"spec_ordinal", "role_prefix", "record_id", "provider", "model", "adapter", "runtime_access"}, "schema_model_invalid", path)
        if type(model["spec_ordinal"]) is not int or not 0 <= model["spec_ordinal"] < len(specs) or model["role_prefix"] != "":
            raise ExecutionSnapshotError("schema_model_invalid", paths=(path,))
        model_id = _identifier(model["record_id"], path)
        if model_id not in model_ids or model_id in seen_models or type(model["provider"]) is not str or not _PROVIDER_RE.fullmatch(model["provider"]) or type(model["model"]) is not str or not _MODEL_TEXT_RE.fullmatch(model["model"]):
            raise ExecutionSnapshotError("schema_model_invalid", paths=(path,))
        seen_models.add(model_id)
        bound_config = specs[model["spec_ordinal"]]["config"]
        if (bound_config.get("llm_model_id") != model_id
                or bound_config.get("llm_provider") != model["provider"]
                or bound_config.get("llm_model") != model["model"]
                or bound_config.get("model_name") != model["model"]):
            raise ExecutionSnapshotError("schema_model_binding_mismatch", paths=(path,))
        adapter = model["adapter"]
        _exact_keys(adapter, _ADAPTER_FIELDS, "schema_model_adapter_invalid", path + ".adapter")
        # Initial v1 is deliberately OpenRouter-only and binds one known public
        # endpoint. Every other adapter field must be null until it receives its
        # own positive, provider-specific schema.
        if model["provider"] != "openrouter" or adapter["openrouter_base_url"] != "https://openrouter.ai/api/v1":
            raise ExecutionSnapshotError("schema_model_adapter_invalid", paths=(path,))
        for name, value in adapter.items():
            if name != "openrouter_base_url" and value is not None:
                raise ExecutionSnapshotError("schema_model_adapter_invalid", paths=(path,))
        _public_url(adapter["openrouter_base_url"], path + ".adapter.openrouter_base_url", allow_none=False)
        access = model["runtime_access"]
        _exact_keys(access, {"kind", "record_id", "access_revision", "required"}, "schema_access_invalid", path + ".runtime_access")
        if access["kind"] != "models_row" or access["record_id"] != model_id or type(access["access_revision"]) is not int or access["access_revision"] < 0 or access["required"] is not True:
            raise ExecutionSnapshotError("schema_access_invalid", paths=(path,))

    access_root = core["broker_access"]
    _exact_keys(access_root, {"trading", "market_data"}, "schema_access_invalid", "$.core.broker_access")
    for purpose in ("trading", "market_data"):
        access = access_root[purpose]
        _exact_keys(access, {"kind", "record_id", "access_revision", "brokerage_type", "paper", "data_feed"}, "schema_access_invalid", f"$.core.broker_access.{purpose}")
        if access["kind"] != "brokerage_row" or access["brokerage_type"] != "alpaca" or type(access["access_revision"]) is not int or access["access_revision"] < 0 or type(access["paper"]) is not bool or access["data_feed"] not in {"iex", "sip"}:
            raise ExecutionSnapshotError("schema_access_invalid", paths=(f"$.core.broker_access.{purpose}",))
        _identifier(access["record_id"], f"$.core.broker_access.{purpose}.record_id")

    runtime = core["runtime"]
    _exact_keys(runtime, {"source_tree_sha256", "image_digest", "dependency_runtime_sha256", "python_version", "strategy_modules", "environment"}, "schema_runtime_invalid", "$.core.runtime")
    if (type(runtime["source_tree_sha256"]) is not str
            or not _SHA256_HEX_RE.fullmatch(runtime["source_tree_sha256"])
            or type(runtime["image_digest"]) is not str
            or not re.fullmatch(r"^sha256:[0-9a-f]{64}$", runtime["image_digest"])
            or type(runtime["dependency_runtime_sha256"]) is not str
            or not _SHA256_HEX_RE.fullmatch(runtime["dependency_runtime_sha256"])
            or type(runtime["python_version"]) is not str
            or not re.fullmatch(r"^[0-9]+\.[0-9]+\.[0-9]+$", runtime["python_version"])):
        raise ExecutionSnapshotError("schema_runtime_invalid", paths=("$.core.runtime",))
    modules = runtime["strategy_modules"]
    if type(modules) is not list or len(modules) != 1:
        raise ExecutionSnapshotError("schema_runtime_invalid", paths=("$.core.runtime.strategy_modules",))
    _exact_keys(modules[0], {"strategy", "module_sha256"}, "schema_runtime_invalid", "$.core.runtime.strategy_modules[0]")
    if modules[0]["strategy"] != "graph_nexus_analysis" or type(modules[0]["module_sha256"]) is not str or not _SHA256_HEX_RE.fullmatch(modules[0]["module_sha256"]):
        raise ExecutionSnapshotError("schema_runtime_invalid", paths=("$.core.runtime.strategy_modules[0]",))
    environment = runtime["environment"]
    _exact_keys(environment, {"timezone", "nexus_backtest_snapshot_write"}, "schema_runtime_invalid", "$.core.runtime.environment")
    if environment != {"timezone": "UTC", "nexus_backtest_snapshot_write": "off"}:
        raise ExecutionSnapshotError("schema_runtime_invalid", paths=("$.core.runtime.environment",))


def _public_snapshot(value) -> tuple[dict, str]:
    if type(value) is not dict:
        raise ExecutionSnapshotError("snapshot_not_mapping")
    if (len(value) != 2 or any(type(key) is not str for key in value)
            or set(value) != {"schema_version", "core"}):
        raise ExecutionSnapshotError("snapshot_shape_invalid", paths=("$",))
    # Validate the raw protocol control before DB-numeric normalization so
    # bool/float aliases can never be accepted as an integer schema version.
    if type(value["schema_version"]) is not str or value["schema_version"] != EXECUTION_SNAPSHOT_SCHEMA_VERSION:
        raise ExecutionSnapshotError("protocol_unsupported")
    normalized = _normalize_strict_json(value)
    if type(normalized["core"]) is not dict:
        raise ExecutionSnapshotError("snapshot_core_invalid")
    _validate_snapshot_v1(normalized["core"])
    canonical = canonical_execution_json(normalized)
    try:
        canonical_bytes = canonical.encode("utf-8")
    except UnicodeError:
        raise ExecutionSnapshotError("json_invalid_unicode") from None
    if len(canonical_bytes) > EXECUTION_SNAPSHOT_MAX_BYTES:
        raise ExecutionSnapshotError("snapshot_too_large")
    _iter_public_projection(normalized)
    if sanitize_snapshot(normalized) != normalized:
        raise ExecutionSnapshotError("snapshot_sanitizer_changed_payload")
    try:
        assert_secret_free(normalized)
    except Exception:
        raise ExecutionSnapshotError("snapshot_secret_check_failed") from None
    return json.loads(canonical), canonical


def build_execution_snapshot(core: Mapping) -> dict:
    """Build the narrow strict public v1 snapshot from a projected core."""
    if type(core) is not dict:
        raise ExecutionSnapshotError("snapshot_core_invalid")
    snapshot, _ = _public_snapshot({"schema_version": EXECUTION_SNAPSHOT_SCHEMA_VERSION, "core": core})
    return snapshot


def execution_snapshot_sha256(snapshot) -> str:
    _, canonical = _public_snapshot(snapshot)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _signing_key(signing_key) -> bytes:
    if type(signing_key) is not bytes or not 32 <= len(signing_key) <= _MAX_SIGNING_KEY_BYTES:
        raise ExecutionSnapshotError("signing_key_unavailable")
    return signing_key


def _normalized_identity(*, backtest_id, created_at):
    # RethinkDB has one numeric datum type; an integral float and int identify
    # the same numeric primary key. Strings/bools remain distinct and forbidden.
    if type(backtest_id) is int:
        normalized_id = backtest_id
    elif type(backtest_id) is float and math.isfinite(backtest_id) and backtest_id.is_integer():
        normalized_id = int(backtest_id)
    else:
        raise ExecutionSnapshotError("backtest_id_invalid")
    if not 1 <= normalized_id <= _SAFE_INTEGER_MAX:
        raise ExecutionSnapshotError("backtest_id_invalid")
    if type(created_at) is not str or not _CREATED_AT_RE.fullmatch(created_at):
        raise ExecutionSnapshotError("created_at_invalid")
    try:
        datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError:
        raise ExecutionSnapshotError("created_at_invalid") from None
    return normalized_id, created_at


def _attestation_payload(*, backtest_id, created_at, snapshot, digest) -> dict:
    backtest_id, created_at = _normalized_identity(backtest_id=backtest_id, created_at=created_at)
    return {
        "backtest_id": backtest_id,
        "created_at": created_at,
        "execution_snapshot_mode": EXECUTION_SNAPSHOT_MODE,
        "execution_snapshot_signer": EXECUTION_SNAPSHOT_SIGNER,
        "execution_snapshot_sha256": digest,
        "execution_snapshot": snapshot,
    }


def _hmac_sha256(*, backtest_id, created_at, snapshot, digest, signing_key) -> str:
    payload = _attestation_payload(backtest_id=backtest_id, created_at=created_at, snapshot=snapshot, digest=digest)
    mac = hmac.new(
        _signing_key(signing_key),
        canonical_execution_json(payload).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return "hmac-sha256:" + mac


def make_execution_snapshot_queue_fields(*, backtest_id, created_at, core: Mapping, signing_key) -> dict:
    """Build the complete true-only queue envelope in one value."""
    _normalized_identity(backtest_id=backtest_id, created_at=created_at)
    _signing_key(signing_key)
    snapshot = build_execution_snapshot(core)
    digest = execution_snapshot_sha256(snapshot)
    signature = _hmac_sha256(backtest_id=backtest_id, created_at=created_at, snapshot=snapshot, digest=digest, signing_key=signing_key)
    return {
        "execution_snapshot_mode": EXECUTION_SNAPSHOT_MODE,
        "execution_snapshot": snapshot,
        "execution_snapshot_sha256": digest,
        "execution_snapshot_hmac_sha256": signature,
        "execution_snapshot_signer": EXECUTION_SNAPSHOT_SIGNER,
    }


def verify_execution_snapshot_queue_fields(
    fields: Mapping,
    *,
    backtest_id,
    created_at,
    signing_key,
    required: bool = False,
    expected_sha256: str | None = None,
) -> VerifiedExecutionSnapshot | None:
    """Verify a complete queue envelope or fail closed with stable reason codes."""
    if type(fields) is not dict or any(type(key) is not str for key in fields):
        raise ExecutionSnapshotError("queue_row_unreadable")
    if type(required) is not bool:
        raise ExecutionSnapshotError("required_flag_invalid")
    if expected_sha256 is not None:
        if type(expected_sha256) is not str or not _DIGEST_RE.fullmatch(expected_sha256):
            raise ExecutionSnapshotError("engine_binding_invalid")
        required = True
    present = _SNAPSHOT_FIELDS.intersection(fields)
    if not present:
        if required:
            raise ExecutionSnapshotError("contract_missing")
        return None
    _normalized_identity(backtest_id=backtest_id, created_at=created_at)
    _signing_key(signing_key)
    mode_value = fields.get("execution_snapshot_mode")
    if type(mode_value) is not str or mode_value != EXECUTION_SNAPSHOT_MODE or present != _SNAPSHOT_FIELDS:
        raise ExecutionSnapshotError("partial_contract")
    signer_value = fields.get("execution_snapshot_signer")
    if type(signer_value) is not str or signer_value != EXECUTION_SNAPSHOT_SIGNER:
        raise ExecutionSnapshotError("signer_unsupported")
    digest = fields.get("execution_snapshot_sha256")
    signature = fields.get("execution_snapshot_hmac_sha256")
    if type(digest) is not str or not _DIGEST_RE.fullmatch(digest):
        raise ExecutionSnapshotError("digest_invalid")
    if type(signature) is not str or not _HMAC_RE.fullmatch(signature):
        raise ExecutionSnapshotError("attestation_invalid")
    snapshot, canonical = _public_snapshot(fields.get("execution_snapshot"))
    actual_digest = "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    if not hmac.compare_digest(actual_digest, digest):
        raise ExecutionSnapshotError("stored_hash_mismatch")
    if expected_sha256 is not None and not hmac.compare_digest(expected_sha256, digest):
        raise ExecutionSnapshotError("engine_binding_mismatch")
    expected_signature = _hmac_sha256(backtest_id=backtest_id, created_at=created_at, snapshot=snapshot, digest=digest, signing_key=signing_key)
    if not hmac.compare_digest(expected_signature, signature):
        raise ExecutionSnapshotError("attestation_mismatch")
    return VerifiedExecutionSnapshot(canonical_json=canonical, sha256=digest, signer=EXECUTION_SNAPSHOT_SIGNER)


def execution_snapshot_public_status(fields: Mapping) -> dict | None:
    """Return safe syntactic claim metadata; never copy unverified body values."""
    if (type(fields) is not dict
            or any(type(key) is not str for key in fields)
            or _SNAPSHOT_FIELDS.intersection(fields) != _SNAPSHOT_FIELDS):
        return None
    snapshot = fields.get("execution_snapshot")
    digest = fields.get("execution_snapshot_sha256")
    signature = fields.get("execution_snapshot_hmac_sha256")
    mode_value = fields.get("execution_snapshot_mode")
    signer_value = fields.get("execution_snapshot_signer")
    if (type(mode_value) is not str or mode_value != EXECUTION_SNAPSHOT_MODE
            or type(signer_value) is not str or signer_value != EXECUTION_SNAPSHOT_SIGNER
            or type(snapshot) is not dict
            or type(snapshot.get("schema_version")) is not str
            or snapshot.get("schema_version") != EXECUTION_SNAPSHOT_SCHEMA_VERSION
            or type(digest) is not str or not _DIGEST_RE.fullmatch(digest)
            or type(signature) is not str or not _HMAC_RE.fullmatch(signature)):
        return None
    return {
        "mode": EXECUTION_SNAPSHOT_MODE,
        "schema_version": EXECUTION_SNAPSHOT_SCHEMA_VERSION,
        "sha256": digest,
        "verification": "unverified_claim",
    }
