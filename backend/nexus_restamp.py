"""Model-switch "preserve history" preview + re-stamp.

When an operator changes a ``graph_nexus_analysis`` instance's LLM model and
chooses "preserve history", we re-stamp the existing saved state to the NEW boot
identities so the next live boot reuses everything instead of running a
destructive lookback + cleanup:

* ``NexusStrategyCache`` snapshot row -- keyed by the **base** instance id;
  re-stamp its ``config_hash`` (and the hash embedded in ``id``) to the new
  ``live_config_hash``.
* ``GraphNexusLearningCache`` cleanup marker ``cleanup_done|<scoped>`` -- keyed
  by the **scoped** instance id; set its ``config_hash`` field to the new
  ``history_scope_id`` so the config-change cleanup sees "no change".

Identities come from :mod:`nexus_config_identity`, the same module the broker
boot path uses, so a re-stamped row can never carry an identity boot won't match.

DB access is funneled through the small ``_fetch_*`` / ``_write_*`` / ``_update_*``
helpers so tests can substitute in-memory fakes without emulating the store.

``conn`` and ``r`` stay on every helper's signature (R26) -- the CLI and
backend/tests/test_nexus_restamp.py both pass them -- and are ignored.
"""

from __future__ import annotations

import datetime as _dt
import time

from db import store
from db.store import P

from model_resolver import resolve_model_refs_in_config
import nexus_config_identity as nci

try:  # public name; broker aliases it to ``_apply_live_overrides``
    from live_mode_overrides import apply_live_overrides as _apply_live_overrides
except Exception:  # pragma: no cover - defensive: never block a save on overrides
    def _apply_live_overrides(cfg):
        return cfg

DB_NAME = "IntelliStock"
SNAPSHOT_TABLE = "NexusStrategyCache"
LEARNING_CACHE_TABLE = "GraphNexusLearningCache"
INSTANCES_TABLE = "Instances"
NEXUS_STRATEGY_NAME = nci.NEXUS_STRATEGY_NAME


# --------------------------------------------------------------------------- #
# Pure identity helpers                                                        #
# --------------------------------------------------------------------------- #

def resolve_for_identity(conn, raw_cfg: dict) -> dict:
    """Resolve a raw strategy config the same way live boot does.

    ``resolve_model_refs_in_config(force_refresh=True)`` expands ``*_llm_model_id``
    refs into inline provider/model, then live overrides are applied. The result
    is what the identity hashes must be computed from.

    A resolver FAILURE is propagated (NOT swallowed): if we silently fell back to
    the unresolved config, we would write an identity hash that boot — which
    resolves successfully later — will not reproduce, silently triggering the
    exact destructive rebuild this feature prevents. Letting it raise records a
    ``restamp_error`` instead (``_apply_preserve_history``), so the save is kept
    but the operator is told preserve did not take. Note: a deleted/missing
    ``model_id`` is NOT a failure here — ``resolve_model_refs_in_config`` skips it
    and falls through to env/provider defaults, exactly as boot does.
    """
    cfg = raw_cfg if isinstance(raw_cfg, dict) else {}
    cfg = resolve_model_refs_in_config(conn, cfg, force_refresh=True)
    return _apply_live_overrides(cfg)


def _identities_for(resolved_cfg: dict) -> tuple[str, str]:
    """Return ``(live_config_hash, history_scope_id)`` for a resolved config."""
    return (
        nci.live_config_hash(resolved_cfg),
        nci.history_scope_id(resolved_cfg),
    )


def _nexus_config_from_strategies(strategies) -> dict:
    """Pluck the ``graph_nexus_analysis`` sub-strategy config from a payload list.

    Reads ``config`` only (not ``conditions``). This matches boot's snapshot
    ``live_config_hash`` (config-only) and is also correct for the cleanup
    ``history_scope_id`` (merged conditions+config) because every strategy saved
    through ``action_edit_strategy`` is run through
    ``_normalize_strategy_payload_item``, which folds ``conditions`` into
    ``config`` and emits ``conditions: {}``. So for the only path that triggers a
    re-stamp, ``config`` already holds the merged keys.
    """
    for s in (strategies or []):
        if not isinstance(s, dict):
            continue
        if str(s.get("strategy") or "").strip().lower() == NEXUS_STRATEGY_NAME:
            return dict(s.get("config") or {})
    return {}


# --------------------------------------------------------------------------- #
# DB access helpers (monkeypatched in tests)                                   #
# --------------------------------------------------------------------------- #

def _now_iso() -> str:
    """R24 replacement for ``r.now()``; a jsonb-safe UTC ISO-8601 string."""
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _fetch_linked_base_instance_ids(conn, r, strategy_id) -> list[str]:
    rows = store.pluck(
        store.run(store.filter(INSTANCES_TABLE, {"strategy_id": strategy_id})),
        "id",
    )
    return [str(row.get("id")) for row in rows if row.get("id") is not None]


def _fetch_snapshot_rows(conn, r, base_instance_id: str) -> list[dict]:
    """All new-schema snapshot rows for an instance that boot could load.

    ``load_with_fallback`` keys purely on ``[instance_id, config_hash]`` and does
    NOT filter on ``origin`` -- it will hydrate a ``backtest``-origin row just as
    readily as a ``live`` one (common right after a rebuild, before a full live
    day has been saved). So we must re-stamp every row the loader can consume,
    not just live ones, or boot finds a stale-hash backtest row and rebuilds.
    """
    rows = store.run(store.filter(
        SNAPSHOT_TABLE,
        P.field("instance_id").eq(base_instance_id)
        & P.field("strategy_name").eq(NEXUS_STRATEGY_NAME)
        & (P.field("origin").eq("live") | P.field("origin").eq("backtest"))))
    return [row for row in rows if row.get("config_hash") and row.get("end_date")]


def _write_snapshot_row(conn, r, row: dict) -> None:
    store.insert(SNAPSHOT_TABLE, row, conflict="replace")


def _fetch_cleanup_markers(conn, r, base_instance_id: str) -> list[dict]:
    """Cleanup markers whose scoped instance id belongs to this base id."""
    # R17: the ReQL regex anchor becomes a prefix scan, which is backed by
    # GraphNexusLearningCache's "_pfx" text_pattern_ops index.
    rows = store.run(store.filter(
        LEARNING_CACHE_TABLE, P.field("id").starts_with("cleanup_done|")))
    out = []
    for row in rows:
        iid = str(row.get("instance_id") or "")
        if iid == base_instance_id or iid.startswith(base_instance_id + "|"):
            out.append(row)
    return out


def _update_marker_hash(conn, r, marker_id: str, new_scope: str) -> None:
    store.update(LEARNING_CACHE_TABLE, marker_id, {"config_hash": new_scope})


# --------------------------------------------------------------------------- #
# Public API                                                                   #
# --------------------------------------------------------------------------- #

def linked_base_instance_ids(conn, r, strategy_id) -> list[str]:
    return _fetch_linked_base_instance_ids(conn, r, strategy_id)


def restamp_instance(conn, r, base_instance_id: str, resolved_cfg: dict) -> dict:
    """Re-stamp one instance's saved state to the new identities. Idempotent.

    Returns a summary ``{snapshots_restamped, markers_restamped, config_hash,
    history_scope_id}``.
    """
    new_hash, new_scope = _identities_for(resolved_cfg)
    snapshots_restamped = 0
    markers_restamped = 0

    for row in _fetch_snapshot_rows(conn, r, base_instance_id):
        if str(row.get("config_hash")) == new_hash:
            continue  # already current -> idempotent no-op
        end_date = row.get("end_date")
        # Preserve the row's own origin segment ("live" / "backtest") -- the PK
        # format is `{base}|{strategy}|{hash}|{origin}|{end_date}` and the
        # respective writers (save_strategy_cache_to_db / persist_backtest_snapshot)
        # expect it to match `origin`, or the next write inserts a duplicate.
        seg = str(row.get("origin") or "live")
        new_row = dict(row)
        new_row["config_hash"] = new_hash
        new_row["id"] = f"{base_instance_id}|{NEXUS_STRATEGY_NAME}|{new_hash}|{seg}|{end_date}"
        new_row["updated_at"] = _now_iso()
        new_row["updated_at_epoch"] = time.time()
        _write_snapshot_row(conn, r, new_row)
        snapshots_restamped += 1

    for marker in _fetch_cleanup_markers(conn, r, base_instance_id):
        if str(marker.get("config_hash")) == new_scope:
            continue  # already current -> idempotent no-op
        _update_marker_hash(conn, r, str(marker.get("id")), new_scope)
        markers_restamped += 1

    return {
        "base_instance_id": base_instance_id,
        "snapshots_restamped": snapshots_restamped,
        "markers_restamped": markers_restamped,
        "config_hash": new_hash,
        "history_scope_id": new_scope,
    }


def preview_change(conn, r, strategy_id, proposed_strategies) -> dict:
    """Read-only: would the proposed config change trigger a rebuild per instance?

    ``needs_prompt`` is true only when at least one linked instance both has
    existing live state (``snapshot_exists``) AND would otherwise rebuild
    (``would_rebuild``) -- i.e. there is something to preserve.
    """
    resolved = resolve_for_identity(conn, _nexus_config_from_strategies(proposed_strategies))
    new_hash, new_scope = _identities_for(resolved)

    instances = []
    needs_prompt = False
    for base in _fetch_linked_base_instance_ids(conn, r, strategy_id):
        snap_rows = _fetch_snapshot_rows(conn, r, base)
        snapshot_exists = bool(snap_rows)
        snap_hash_match = any(str(s.get("config_hash")) == new_hash for s in snap_rows)

        markers = _fetch_cleanup_markers(conn, r, base)
        marker_scope_match = bool(markers) and all(
            str(m.get("config_hash")) == new_scope for m in markers
        )

        would_rebuild = (snapshot_exists and not snap_hash_match) or (
            bool(markers) and not marker_scope_match
        )
        if would_rebuild and snapshot_exists:
            needs_prompt = True
        instances.append({
            "base_instance_id": base,
            "would_rebuild": would_rebuild,
            "snapshot_exists": snapshot_exists,
        })

    return {
        "needs_prompt": needs_prompt,
        "config_hash": new_hash,
        "history_scope_id": new_scope,
        "instances": instances,
    }
