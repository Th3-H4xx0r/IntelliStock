"""Task 4 (2026-07-28): the evidence/cost/override contract for one backtest.

Everything here crosses the API -> `BacktestInstances` row -> broker boundary,
so every check fails CLOSED. A half-configured evidence run is a bug, not a
default: it would produce artifacts that look like evidence but cannot be
replayed or attributed.

Three concerns:

* `validate_evidence_options` -- the queue-time contract. Applied again at
  broker startup so a hand-edited row cannot smuggle anything past the API.
* `apply_candidate_overrides` -- ONLY the four approved A1-A4 candidate keys
  may reach the in-memory Graph Nexus spec, and only on a copy. The live
  `Strategies` document is never touched.
* `resolve_execution_cost_model` -- exactly one immutable cost model per run,
  written into preregistration and handed to the emulator, so a receipt can
  never claim a cost basis the fills did not use.
"""
from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import replace

from simulated_execution import (
    DEFAULT_EQUITY_EXECUTION_COST_MODEL,
    ExecutionCostModel,
)


class EvidenceOptionError(ValueError):
    """Raised when a run's evidence/cost/override contract is not valid."""


EVIDENCE_MODES = frozenset({"off", "record", "record_extend", "replay"})

#: The only strategy keys a queued experiment may override. Each is a
#: default-off A1-A4 candidate; anything else must go through a reviewed
#: Strategies edit, not a backtest payload.
CANDIDATE_OVERRIDE_KEYS = frozenset({
    "regime_position_cap_recovery_hard_enforce",      # A1
    "momentum_breakout_max_nav_pct_by_regime",        # A2
    "deployment_ramp_caps_by_regime",                 # A3
    "circuit_breaker_regime_adjustment_semantics_v2",  # A4
})

#: Cost stress targets, in one-way basis points. `None` means nominal.
COST_SCENARIO_TARGETS_BPS = (25.0, 50.0)

_OPTION_KEYS = frozenset({
    "evidence_mode", "fixture_build_id", "replay_fixture_id",
    "matrix_manifest_id", "matrix_arm_id", "cost_scenario_id",
    "equity_total_cost_bps", "nexus_candidate_overrides", "fixture_ordinal",
    "pit_mode",
})

#: Point-in-time enforcement for a historical run.
#:   strict   -- replay from frozen, content-hashed snapshots; refuse if absent.
#:   research -- explicitly opt out and run the LEGACY current-state path.
#: Research runs carry real lookahead bias (the strategy sees today's graph,
#: universe, fundamentals and news for a past date), so they are stamped
#: provenance=legacy_unverified and can never be promotion-eligible. The point
#: of naming it is that the bias is declared rather than silently reintroduced.
PIT_MODES = frozenset({"strict", "research"})

_ID_KEYS = ("matrix_manifest_id", "matrix_arm_id", "cost_scenario_id")

# Anything that smells like a credential is refused outright rather than
# scrubbed, so a caller never believes a secret was accepted and stored.
_SECRETISH = re.compile(
    r"(key|secret|token|password|passwd|credential|authorization|bearer)",
    re.IGNORECASE,
)

_GRAPH_NEXUS = "graph_nexus_analysis"


def _text(name: str, value) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EvidenceOptionError(f"{name} must be a non-empty string")
    return value.strip()


def _nav_fraction(name: str, value) -> float:
    """A NAV fraction in (0, 1]. Bools and non-finite values are refused."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EvidenceOptionError(f"{name} must be a number in (0, 1]")
    number = float(value)
    if not math.isfinite(number) or not 0.0 < number <= 1.0:
        raise EvidenceOptionError(f"{name} must be a finite number in (0, 1]")
    return number


def _validate_breakout_mapping(value):
    if not isinstance(value, Mapping):
        raise EvidenceOptionError(
            "momentum_breakout_max_nav_pct_by_regime must be a mapping")
    required = {"default", "bull", "recovery"}
    if set(value) != required:
        raise EvidenceOptionError(
            "momentum_breakout_max_nav_pct_by_regime requires exactly "
            "default, bull and recovery")
    return {key: _nav_fraction(f"momentum_breakout_max_nav_pct_by_regime[{key}]",
                               value[key]) for key in sorted(required)}


def _validate_ramp_mapping(value):
    if not isinstance(value, Mapping):
        raise EvidenceOptionError("deployment_ramp_caps_by_regime must be a mapping")
    if set(value) != {"bull"}:
        raise EvidenceOptionError(
            "deployment_ramp_caps_by_regime accepts only a bull entry")
    caps = value["bull"]
    if isinstance(caps, (str, bytes)) or not isinstance(caps, Sequence) or len(caps) != 3:
        raise EvidenceOptionError(
            "deployment_ramp_caps_by_regime['bull'] must hold exactly three caps")
    return {"bull": [_nav_fraction("deployment_ramp_caps_by_regime['bull']", cap)
                     for cap in caps]}


def _validate_bool(name: str, value) -> bool:
    if not isinstance(value, bool):
        raise EvidenceOptionError(f"{name} must be a boolean")
    return value


_CANDIDATE_VALIDATORS = {
    "regime_position_cap_recovery_hard_enforce":
        lambda v: _validate_bool("regime_position_cap_recovery_hard_enforce", v),
    "circuit_breaker_regime_adjustment_semantics_v2":
        lambda v: _validate_bool("circuit_breaker_regime_adjustment_semantics_v2", v),
    "momentum_breakout_max_nav_pct_by_regime": _validate_breakout_mapping,
    "deployment_ramp_caps_by_regime": _validate_ramp_mapping,
}


def validate_candidate_overrides(value) -> dict:
    """Normalize the allowlisted candidate overrides, or raise."""
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise EvidenceOptionError("nexus_candidate_overrides must be a mapping")
    out = {}
    for key in sorted(value):
        name = _text("nexus_candidate_overrides key", key)
        if name not in CANDIDATE_OVERRIDE_KEYS:
            raise EvidenceOptionError(
                f"{name!r} is not an approved candidate override")
        out[name] = _CANDIDATE_VALIDATORS[name](value[key])
    return out


def _validate_pit_mode(value):
    if value is None:
        return "strict"
    if isinstance(value, bool) or not isinstance(value, str) or value not in PIT_MODES:
        raise EvidenceOptionError(f"pit_mode must be one of {sorted(PIT_MODES)}")
    return value


def _validate_fixture_ordinal(value, mode):
    """Which of the matrix's preregistered fixtures this run builds.

    Distinct ordinals give distinct build addresses, which is what lets several
    arms of one matrix record independently instead of colliding on a single
    construction address.
    """
    if mode == "off":
        if value is not None:
            raise EvidenceOptionError(
                "fixture_ordinal requires a non-off evidence_mode")
        return None
    if value is None:
        return 0
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise EvidenceOptionError("fixture_ordinal must be a non-negative integer")
    return value


def _validate_cost_bps(value):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EvidenceOptionError("equity_total_cost_bps must be 25 or 50")
    number = float(value)
    if not math.isfinite(number) or number not in COST_SCENARIO_TARGETS_BPS:
        raise EvidenceOptionError("equity_total_cost_bps must be 25 or 50")
    return number


def validate_evidence_options(payload) -> dict:
    """Normalize a queued run's evidence/cost/override contract, or raise.

    Returns every field explicitly (``None``/``{}``/``"off"`` when absent) so
    the broker loader reads one fixed shape and an ordinary backtest that
    supplies none of these fields stays byte-compatible.
    """
    if payload is None:
        payload = {}
    if not isinstance(payload, Mapping):
        raise EvidenceOptionError("evidence options must be a mapping")
    for key in payload:
        name = str(key)
        if _SECRETISH.search(name):
            raise EvidenceOptionError(
                f"{name!r} looks like a credential and is never accepted here")
        if name not in _OPTION_KEYS:
            raise EvidenceOptionError(f"{name!r} is not a recognized backtest option")

    raw_mode = payload.get("evidence_mode", "off")
    if isinstance(raw_mode, bool) or not isinstance(raw_mode, str) or raw_mode not in EVIDENCE_MODES:
        raise EvidenceOptionError(
            f"evidence_mode must be one of {sorted(EVIDENCE_MODES)}")
    mode = raw_mode

    supplied = {key: payload.get(key) for key in
                (*_ID_KEYS, "fixture_build_id", "replay_fixture_id")}
    if mode == "off":
        named = [key for key, value in supplied.items() if value is not None]
        if named:
            raise EvidenceOptionError(
                "evidence identifiers require a non-off evidence_mode: "
                + ", ".join(sorted(named)))
        resolved = {key: None for key in supplied}
    else:
        resolved = {}
        for key in _ID_KEYS:
            resolved[key] = _text(key, supplied[key])
        if mode in {"record", "record_extend"}:
            resolved["fixture_build_id"] = _text("fixture_build_id",
                                                 supplied["fixture_build_id"])
            resolved["replay_fixture_id"] = (
                None if supplied["replay_fixture_id"] is None
                else _text("replay_fixture_id", supplied["replay_fixture_id"]))
        else:  # replay
            resolved["replay_fixture_id"] = _text("replay_fixture_id",
                                                  supplied["replay_fixture_id"])
            resolved["fixture_build_id"] = (
                None if supplied["fixture_build_id"] is None
                else _text("fixture_build_id", supplied["fixture_build_id"]))

    return {
        "evidence_mode": mode,
        **resolved,
        "fixture_ordinal": _validate_fixture_ordinal(
            payload.get("fixture_ordinal"), mode),
        "pit_mode": _validate_pit_mode(payload.get("pit_mode")),
        "equity_total_cost_bps": _validate_cost_bps(payload.get("equity_total_cost_bps")),
        "nexus_candidate_overrides": validate_candidate_overrides(
            payload.get("nexus_candidate_overrides")),
    }


def apply_candidate_overrides(specs, overrides):
    """Return a COPY of ``specs`` with allowlisted overrides on the nexus spec.

    Never mutates the input. The broker holds the same spec dicts that were
    loaded from the live `Strategies` document, so mutating them in place would
    write an experiment's candidate into production.
    """
    normalized = validate_candidate_overrides(overrides)
    if not normalized:
        return list(specs or [])
    out = []
    for spec in (specs or []):
        if not isinstance(spec, Mapping):
            out.append(spec)
            continue
        copied = dict(spec)
        if str(copied.get("strategy") or "") == _GRAPH_NEXUS:
            config = dict(copied.get("config") or {})
            config.update(normalized)
            copied["config"] = config
        out.append(copied)
    return out


def resolve_execution_cost_model(target_one_way_bps, base=None) -> ExecutionCostModel:
    """Resolve the single immutable cost model for one run.

    ``None`` yields the nominal model. A stress target scales every component
    by ``target / (spread/2 + slippage + fee)``, which preserves their
    proportions -- the alternative, post-hoc subtracting embedded fill costs,
    would let a receipt claim a cost basis the fills never used.
    """
    model = base if base is not None else DEFAULT_EQUITY_EXECUTION_COST_MODEL
    if not isinstance(model, ExecutionCostModel):
        raise EvidenceOptionError("base must be an ExecutionCostModel")
    if target_one_way_bps is None:
        return model
    target = _validate_cost_bps(target_one_way_bps)
    nominal = model.spread_bps / 2.0 + model.slippage_bps + model.fee_bps
    if not math.isfinite(nominal) or nominal <= 0:
        raise EvidenceOptionError("base cost model has no positive one-way cost to scale")
    scale = target / nominal
    return replace(
        model,
        version=f"{model.version}+stress{cost_scenario_id(target)}",
        spread_bps=model.spread_bps * scale,
        slippage_bps=model.slippage_bps * scale,
        fee_bps=model.fee_bps * scale,
    )


#: Source files whose contents define "what executed". Tests and caches are
#: excluded so a test-only edit does not invalidate a fixture that is still
#: byte-identical in every executing path.
_SOURCE_SKIP_DIRS = ("__pycache__", "/tests/", "/test/")


def source_tree_digest(root=None) -> str:
    """Content digest of the backend source that actually executes.

    Deliberately NOT `git rev-parse HEAD^{tree}`: the deployed container has no
    `.git`, so a git-based identity would be uncomputable exactly where it
    matters. Hashing the file contents gives the same answer locally and in the
    container for the same commit, which is what lets a receipt prove the
    executed source matches its preregistration.
    """
    import hashlib
    import os as _os

    base = root or _os.path.dirname(_os.path.abspath(__file__))
    entries = []
    for dirpath, dirnames, filenames in _os.walk(base):
        dirnames[:] = sorted(d for d in dirnames if d != "__pycache__")
        for name in sorted(filenames):
            if not name.endswith(".py"):
                continue
            full = _os.path.join(dirpath, name)
            rel = _os.path.relpath(full, base).replace(_os.sep, "/")
            if any(skip.strip("/") in rel.split("/") for skip in ("tests", "test")):
                continue
            with open(full, "rb") as handle:
                entries.append((rel, hashlib.sha256(handle.read()).hexdigest()))
    digest = hashlib.sha256()
    for rel, file_hash in sorted(entries):
        digest.update(f"{rel}:{file_hash}\n".encode())
    return "sha256:" + digest.hexdigest()


def execution_cost_model_hash(model) -> str:
    """Canonical cost identity for an ExecutionCostModel.

    Mirrors ExperimentSpec.execution_cost_model_hash so a receipt can compare
    the model the EMULATOR actually used against the one that was
    preregistered. Deriving the executed hash from the experiment instead would
    be self-referential and always match.
    """
    import hashlib

    from experiment_registry import _canonical_json

    payload = model.as_dict() if hasattr(model, "as_dict") else dict(model)
    digest = hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
    return f"cost-sha256-{digest}"


def cost_scenario_id(target_one_way_bps) -> str:
    """Stable scenario label used in matrix manifests and receipts."""
    if target_one_way_bps is None:
        return "base"
    return f"{int(_validate_cost_bps(target_one_way_bps))}bps"
