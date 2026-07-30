"""Helpers for resolving the resume window of the Nexus historic
lookback against RethinkDB.

Lives outside ``broker.py`` so it can be imported by tests — broker.py
is an entrypoint script that argparses and dispatches at module load
time, which makes ``import broker`` from a pytest run impractical.

The performance-critical detail here is the ``instance_id`` secondary
index on ``GraphNexusTradeContexts``. Without it the resume-date
query at lookback prep time was a full table scan over every backtest
and live row on the cluster — backtest #953929 stalled ~4 min on it.
With it, the scan is bounded to rows for the active instance only."""
from __future__ import annotations

import os
import time
from datetime import datetime
from typing import Iterable


DB_NAME = "IntelliStock"


# Module-level cache so the first call per process pays the index
# existence check and every subsequent call short-circuits.
_NEXUS_TRADE_CONTEXTS_INDEX_READY = False


def _strict_pit_trade_context_row(row: dict) -> bool:
    """Return whether a persisted row is complete strict-PIT evidence."""

    if not isinstance(row, dict):
        return False
    if str(row.get("pit_provenance") or "").strip() != "strict_verified":
        return False
    if not str(row.get("pit_manifest_id") or "").strip():
        return False
    raw_as_of = str(row.get("pit_as_of") or "").strip()
    if not raw_as_of:
        return False
    try:
        parsed = datetime.fromisoformat(raw_as_of.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def _resumable_trade_context_row(row: dict, *, allow_research: bool) -> bool:
    """Whether a persisted row lets a lookback day be SKIPPED on resume.

    A strict run may only resume from strict evidence -- resuming it from
    unverified rows would launder current-state data into a strict result.

    A research run is a different matter. It writes pit_provenance=
    "legacy_unverified" by design, so the strict predicate rejected every day
    it had just finished and each rerun restarted all 85 days from scratch. A
    research run resuming research rows is self-consistent, so accept those
    (and strict rows, which are strictly better evidence). The manifest/as_of
    completeness checks still apply, so a half-written row never counts.
    """
    if _strict_pit_trade_context_row(row):
        return True
    if not allow_research or not isinstance(row, dict):
        return False
    if str(row.get("pit_provenance") or "").strip() != "legacy_unverified":
        return False
    if not str(row.get("pit_manifest_id") or "").strip():
        return False
    raw_as_of = str(row.get("pit_as_of") or "").strip()
    if not raw_as_of:
        return False
    try:
        parsed = datetime.fromisoformat(raw_as_of.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def _log(msg: str, color: str = "white") -> None:
    """Best-effort log — falls back to print if intellistock_logger isn't
    available (e.g. in unit tests)."""
    try:
        from intellistock_logger import intellistock_logger

        intellistock_logger.log(msg, color, service="Broker")
    except Exception:
        try:
            print(f"[Broker] {msg}", flush=True)
        except Exception:
            pass


def _import_rethinkdb():
    """Lazy-import the RethinkDB driver so this module is importable in
    environments where the driver is absent (the tests inject a mock)."""
    from rethinkdb import RethinkDB  # type: ignore

    return RethinkDB()


def _connect():
    """Open a fresh RethinkDB connection using the same env contract as
    ``broker.get_conn``. Tests patch ``connect`` so the real driver is
    never spoken to."""
    r = _import_rethinkdb()
    host = os.environ.get("RETHINKDB_HOST", "localhost")
    port = int(os.environ.get("RETHINKDB_PORT", "28015"))
    return r, r.connect(host=host, port=port, timeout=10)


def ensure_nexus_trade_contexts_instance_id_index(r_module, conn) -> bool:
    """Lazy-create the ``instance_id`` secondary index on
    ``GraphNexusTradeContexts`` so ``load_nexus_processed_trade_context_dates``
    can switch from a full ``filter()`` scan to ``get_all(...,
    index="instance_id")``.

    ``r_module`` is the RethinkDB driver instance (broker has a module-
    level one; tests pass a MagicMock).

    Returns True when the index is ready (existing or freshly built),
    False if creation/wait failed — callers should fall back to the
    legacy filter scan in that case."""
    global _NEXUS_TRADE_CONTEXTS_INDEX_READY
    if _NEXUS_TRADE_CONTEXTS_INDEX_READY:
        return True
    try:
        existing = set(
            r_module.db(DB_NAME).table("GraphNexusTradeContexts").index_list().run(conn)
        )
        if "instance_id" in existing:
            _NEXUS_TRADE_CONTEXTS_INDEX_READY = True
            return True
        _log(
            "Creating index 'instance_id' on GraphNexusTradeContexts "
            "(one-time; may take a few minutes on a heavy table)...",
            "cyan",
        )
        _t0 = time.time()
        r_module.db(DB_NAME).table("GraphNexusTradeContexts").index_create(
            "instance_id"
        ).run(conn)
        r_module.db(DB_NAME).table("GraphNexusTradeContexts").index_wait(
            "instance_id"
        ).run(conn)
        _log(
            f"Index 'instance_id' on GraphNexusTradeContexts ready in "
            f"{time.time() - _t0:.1f}s",
            "cyan",
        )
        _NEXUS_TRADE_CONTEXTS_INDEX_READY = True
        return True
    except Exception as exc:
        _log(
            f"Could not ensure instance_id index on GraphNexusTradeContexts: {exc} "
            "(falling back to filter() scan)",
            "yellow",
        )
        return False


def load_nexus_processed_trade_context_dates(
    instance_id_value: str,
    date_keys: Iterable[str],
    *,
    require_strict: bool = False,
    allow_research: bool = False,
) -> set[str]:
    """Return the subset of ``date_keys`` for which at least one
    ``GraphNexusTradeContexts`` row exists for ``instance_id_value``.

    Uses the ``instance_id`` secondary index when available. Falls back
    to the legacy filter() scan if the index can't be created (e.g.
    permission issues) — slower but still correct."""
    date_keys = list(date_keys or [])
    if not date_keys or not instance_id_value:
        return set()
    try:
        r_module, conn = _connect()
    except Exception:
        return set()
    try:
        tables = set(r_module.db(DB_NAME).table_list().run(conn))
        if "GraphNexusTradeContexts" not in tables:
            return set()
        use_index = ensure_nexus_trade_contexts_instance_id_index(r_module, conn)
        if use_index:
            rows = list(
                r_module.db(DB_NAME)
                .table("GraphNexusTradeContexts")
                .get_all(instance_id_value, index="instance_id")
                .filter(lambda doc: r_module.expr(date_keys).contains(doc["date_key"]))
                .pluck(
                    "date_key",
                    "pit_provenance",
                    "pit_manifest_id",
                    "pit_as_of",
                )
                .run(conn)
            )
        else:
            rows = list(
                r_module.db(DB_NAME)
                .table("GraphNexusTradeContexts")
                .filter(
                    lambda doc: (doc["instance_id"] == instance_id_value)
                    & r_module.expr(date_keys).contains(doc["date_key"])
                )
                .pluck(
                    "date_key",
                    "pit_provenance",
                    "pit_manifest_id",
                    "pit_as_of",
                )
                .run(conn)
            )
        return {
            str(row.get("date_key") or "").strip()
            for row in rows
            if row.get("date_key")
            and (
                not require_strict
                or _resumable_trade_context_row(row, allow_research=allow_research)
            )
        }
    except Exception:
        return set()
    finally:
        try:
            conn.close()
        except Exception:
            pass


def historic_lookback_resume_dates(
    instance_id_value: str,
    lookback_opens: list,
    *,
    require_strict: bool = True,
    allow_research: bool = False,
) -> list:
    """Given the full ordered list of trading-session opens, return the
    suffix starting at the first day NOT yet processed. Returns the
    full list if no rows are persisted for this instance, and an
    empty list if every day is already processed."""
    if not lookback_opens:
        return []
    ordered = list(lookback_opens)
    date_keys = [dt.strftime("%Y-%m-%d") for dt in ordered]
    processed = load_nexus_processed_trade_context_dates(
        instance_id_value,
        date_keys,
        require_strict=require_strict,
        allow_research=allow_research,
    )
    if not processed:
        return ordered
    first_missing_idx = None
    for idx, dt in enumerate(ordered):
        if dt.strftime("%Y-%m-%d") not in processed:
            first_missing_idx = idx
            break
    if first_missing_idx is None:
        return []
    return ordered[first_missing_idx:]


def reset_index_ready_flag_for_tests() -> None:
    """Test hook: reset the per-process flag so the bootstrap path is
    actually exercised in each test."""
    global _NEXUS_TRADE_CONTEXTS_INDEX_READY
    _NEXUS_TRADE_CONTEXTS_INDEX_READY = False
