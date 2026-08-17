"""Prove two A/B arms STARTED from the same state, or refuse to compare them.

## Why this exists, and what it is not

`frozen_paired_state.py` is the full contract: fixtures for PIT/graph/model/market/benchmark,
an image digest, a dependency-runtime digest, `network_policy: deny`, and a baseline sealed
strictly before the window. That bundle cannot be built today — the evidence store holds one
PIT manifest, dated *after* every preregistered window, and there is no network denial or
disposable-store restore. Its treatment path is also pinned to a single config key
(`anchor_reinforce_target_pct`, 12 -> 20), so it does not generalise to arbitrary levers.

This module is the tractable half, and it attacks the defect that was actually MEASURED:

    bt 333727 vs bt 453789 — same document, window, instance, granularity and cash, ONE
    config flag apart — shared 4 of 20 traded names. 20% overlap.
    bt 453789 vs bt 749060 — same again — 23%.

Both arms carried the isolation recipe the handoffs call the one that works (both salts set,
discovery bootstrap and snapshot off). It was not enough, because per-instance Nexus rows
survive between runs: restart cleanup only removes rows dated inside the window, so anything
older is immortal and seeds the next run's discovery differently.

So: fingerprint the per-instance state BEFORE each arm, and refuse to read a delta unless the
two fingerprints are identical. That converts an invisible confound into a hard precondition.

It uses `frozen_paired_state`'s real primitives — `state_rows_sha256` and
`canonical_state_json` — which is what finally imports a module that nothing imported.

## What a PASS here does and does not license

Identical start fingerprints mean the arms began from the same state. It does NOT mean the run
was deterministic afterwards (live news, provider drift and wall-clock all still enter), and it
is NOT the causal-attribution licence `frozen_paired_state` is designed to issue. Pair it with
`pair_validity.assess_pair` on the OUTPUT, which checks whether the books stayed comparable.
Start identical AND end comparable is the strongest claim currently available here.
"""
from __future__ import annotations

import hashlib

from frozen_paired_state import (
    FrozenStateError,
    canonical_state_json,
    state_rows_sha256,
)

#: Tables whose contents STEER THE NEXT RUN'S DECISIONS. Deliberately narrower than the set
#: `clear_backtest_state.py` wipes: `GraphNexusTradeContexts`, `GraphNexusOutcomes`,
#: `GraphNexusOutcomeSeries`, `GraphNexusTradeOutcomes` and `GraphNexusAnalystPanel` are
#: recorded OUTPUTS. Attesting them would fail a pair for rows that steer nothing — the same
#: argument as `_VOLATILE_FIELDS`, and a check that cries wolf is a check that gets switched
#: off. (`GraphNexusTradeContexts` alone reached 269k rows in this system.)
ATTESTED_TABLES = (
    "GraphNexusDiscoveredStocks",
    "GraphNexusMarketTrends",
    "GraphNexusDiscoverySnapshots",
    "GraphNexusRotationCooldown",
    "GraphNexusLearningCache",
    "NexusStrategyCache",
    "NexusRuntimeState",
    "LiveState",
)

#: `frozen_paired_state`'s header is explicit that callers "must export a deliberately
#: approved decision-state projection" — it caps a manifest at 16MB and rejects anything
#: larger. Embedding a whole `NexusStrategyCache.cache_json` blob blows that cap outright, so
#: any string longer than this is replaced by its own digest: identity-preserving (a changed
#: blob still changes the fingerprint) without carrying the payload.
_MAX_INLINE_STRING = 512

#: Row fields that legitimately differ between two runs without changing what the
#: strategy would decide — wall-clock stamps and RethinkDB bookkeeping. Attesting these
#: would make every pair diverge for reasons that carry no information.
_VOLATILE_FIELDS = frozenset({
    "updated_at", "updated_at_epoch", "created_at", "observed_at",
    "last_seen", "last_updated", "size_bytes", "run_id", "backtest_id",
})


def _digest_str(text):
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _project(row):
    """Decision-state projection of one row: volatile fields dropped, big blobs digested."""
    if type(row) is not dict:
        return row
    out = {}
    for k, v in row.items():
        if k in _VOLATILE_FIELDS:
            continue
        if type(v) is str and len(v) > _MAX_INLINE_STRING:
            out[k] = _digest_str(v)
        elif type(v) in (dict, list):
            # Nested payloads (cache bodies, trend arrays) are digested whole for the
            # same reason: their IDENTITY matters, their bulk does not.
            out[k] = _digest_str(canonical_state_json(v))
        else:
            out[k] = v
    return out


def _strip_volatile(row):
    """Back-compat alias: projection subsumes volatile-field stripping."""
    return _project(row)


def table_fingerprint(rows, key_fields=("id",)):
    """Content digest of one table's rows under the decision-state projection."""
    cleaned = [_project(r) for r in (rows or [])]
    return {
        "rows": len(cleaned),
        "sha256": state_rows_sha256(cleaned, key_fields=key_fields),
    }


def _rows_that_steer(name, rows, for_mode):
    """Drop rows that exist in the table but cannot influence a run of `for_mode`.

    One case, and it is verified rather than assumed. `NexusStrategyCache` holds
    `origin="backtest"` end-of-run snapshots, keyed per window end-date, and
    `clear_instance_state.py` PRESERVES them by design in every scope — its docstring says
    so twice. That made a freshly-cleared instance attest as `cold=False` forever.

    But a BACKTEST never reads them: the snapshot boot path emits
    `[snapshot] decision: reason=…` and `[snapshot] hydrated N keys`, and neither line
    appears in ANY backtest log examined (bt 333727 / 826225 / 749060 — zero hits). It is a
    live-boot warm-start mechanism, so for backtest pairing these rows are write-only
    artifacts and must not block a cold verdict.

    They ARE retained for `for_mode="live"`, where that boot path is exactly what runs.
    """
    if for_mode != "backtest" or name != "NexusStrategyCache":
        return rows
    return [r for r in (rows or [])
            if not (type(r) is dict and str(r.get("origin", "")) == "backtest")]


def state_fingerprint(tables, key_fields=("id",), for_mode="backtest"):
    """Fingerprint an arm's starting state.

    `tables` maps table name -> list of rows. A table that is ABSENT is reported as absent
    and a table that is EMPTY is reported with rows=0; the two are not merged, because
    "the table was never read" and "the table was read and was cold" are different claims
    and only the second is evidence of a clean start.

    `for_mode` selects which rows can steer the run — see `_rows_that_steer`. Attesting rows
    the run cannot read would make every cleared instance look dirty, and a check that can
    never say PASS is a check that gets ignored.
    """
    if type(tables) is not dict:
        raise FrozenStateError("tables_invalid")
    if for_mode not in ("backtest", "live"):
        raise FrozenStateError("for_mode_invalid")
    per_table = {}
    for name in ATTESTED_TABLES:
        if name not in tables:
            per_table[name] = {"absent": True}
            continue
        per_table[name] = table_fingerprint(
            _rows_that_steer(name, tables[name], for_mode), key_fields=key_fields)
    bundle = canonical_state_json(
        {"version": "paired-start-v1", "tables": per_table}
    )
    return {
        "tables": per_table,
        "total_rows": sum(t.get("rows", 0) for t in per_table.values()),
        "bundle_sha256": state_rows_sha256(
            [{"id": "bundle", "body": bundle}], key_fields=("id",)
        ),
    }


def is_cold(fingerprint):
    """True when every attested table was READ and was empty.

    A cold start is the only state two arms can reach independently and repeatedly. Matching
    WARM fingerprints is a coincidence that will not survive the next run; matching cold ones
    is reproducible, which is the whole point.
    """
    if type(fingerprint) is not dict:
        return False
    tables = fingerprint.get("tables") or {}
    if not tables:
        return False
    for spec in tables.values():
        if spec.get("absent"):
            return False
        if spec.get("rows", 0) != 0:
            return False
    return True


def compare_arm_starts(control, treatment, *, require_cold=True):
    """Verdict on whether two arms may be compared at all.

    Returns a dict with `verdict`, `reason` and `diverged_tables`:

    * ``IDENTICAL_COLD``  — both arms started cold and identical. Compare away.
    * ``IDENTICAL_WARM``  — byte-identical but not cold. Accepted only when
                            ``require_cold=False``; flagged, because two runs that share a
                            warm state also share whatever that state biased.
    * ``DIVERGED``        — the arms did not start from the same state. Any delta between
                            them is uninterpretable and must not be quoted.
    """
    if type(control) is not dict or type(treatment) is not dict:
        raise FrozenStateError("fingerprint_invalid")

    ct, tt = control.get("tables") or {}, treatment.get("tables") or {}
    diverged = sorted(
        name for name in set(ct) | set(tt)
        if ct.get(name) != tt.get(name)
    )
    if diverged:
        return {
            "verdict": "DIVERGED",
            "diverged_tables": diverged,
            "reason": (
                f"{len(diverged)} attested table(s) differ at arm start "
                f"({', '.join(diverged[:6])}); the arms did not begin from the same "
                f"state, so any return delta measures that difference too"
            ),
        }

    cold = is_cold(control) and is_cold(treatment)
    if not cold and require_cold:
        return {
            "verdict": "DIVERGED",
            "diverged_tables": [],
            "reason": (
                f"arms are byte-identical but WARM ({control.get('total_rows', 0)} rows "
                f"carried in); a warm start is not reproducible and carries whatever bias "
                f"the previous run left. Clear state between arms, or pass require_cold=False "
                f"and say so in the write-up"
            ),
        }
    return {
        "verdict": "IDENTICAL_COLD" if cold else "IDENTICAL_WARM",
        "diverged_tables": [],
        "reason": (
            "both arms started from an identical cold state" if cold
            else "both arms started from an identical warm state (accepted explicitly)"
        ),
    }
