"""Per-build history, log persistence, and summary stats for nexus_graph_engine.

Mirrors the BacktestResults / /app/backtest_logs pattern so operators can:
  - browse every past build in the UI (NexusGraphBuilds table)
  - download the full stdout log for any build (nexus_graph_logs volume)
  - see per-phase work_units (cached vs downloaded), nodes/edges delta, warnings

HARD RULE: This module must not touch the Strategies table. It owns three
new tables: NexusGraphBuilds, and optionally re-uses the existing
GraphNexusProgress singleton (not written here).
"""

from __future__ import annotations

import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from db import schema, store


NEXUS_BUILDS_TABLE = "NexusGraphBuilds"
DEFAULT_LOG_DIR = os.environ.get("NEXUS_GRAPH_LOG_DIR", "/app/nexus_graph_logs")
DEFAULT_RETENTION_DAYS = int(os.environ.get("NEXUS_GRAPH_LOG_RETENTION_DAYS", "30"))
DEFAULT_MAX_FILES = int(os.environ.get("NEXUS_GRAPH_LOG_MAX_FILES", "50"))
DB_NAME = "IntelliStock"


def _ensure_table(r=None, conn=None) -> None:
    """``r``/``conn`` are accepted and ignored: the store takes its own pooled
    connection per operation. The parameters stay so callers keep their arity."""
    try:
        schema.ensure_table(NEXUS_BUILDS_TABLE)
    except Exception:
        pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append_to_array(build_id: str, key: str, value) -> None:
    """Append one element to a JSON array on a build row.

    ReQL had ``r.row[key].append(v)``; a deep merge REPLACES arrays, so the
    read and the write are separate here. ReQL raised on a missing key and the
    caller swallowed it; a missing key starts a new array instead, which is
    strictly more forgiving and cannot lose a value.
    """
    row = store.get(NEXUS_BUILDS_TABLE, build_id)
    if row is None:
        return
    current = row.get(key)
    current = list(current) if isinstance(current, list) else []
    current.append(value)
    store.update(NEXUS_BUILDS_TABLE, build_id, {key: current})


def _make_build_id() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{ts}-{uuid.uuid4().hex[:8]}"


def _neo4j_counts(session) -> dict:
    """Return current {nodes, edges} counts from Neo4j. Non-fatal on failure."""
    out = {"nodes": 0, "edges": 0}
    if session is None:
        return out
    try:
        row = session.run("MATCH (n) RETURN count(n) AS c").single()
        if row:
            out["nodes"] = int(row["c"] or 0)
    except Exception:
        pass
    try:
        row = session.run("MATCH ()-[r]->() RETURN count(r) AS c").single()
        if row:
            out["edges"] = int(row["c"] or 0)
    except Exception:
        pass
    return out


def ensure_log_dir() -> str:
    log_dir = os.environ.get("NEXUS_GRAPH_LOG_DIR", DEFAULT_LOG_DIR)
    try:
        os.makedirs(log_dir, exist_ok=True)
    except Exception:
        pass
    return log_dir


def rotate_old_logs(log_dir: Optional[str] = None) -> int:
    """Delete log files older than retention or beyond max-files cap. Returns count deleted."""
    log_dir = log_dir or ensure_log_dir()
    if not os.path.isdir(log_dir):
        return 0
    try:
        entries = []
        for name in os.listdir(log_dir):
            if not name.startswith("build_") or not (name.endswith(".log") or name.endswith(".log.gz")):
                continue
            path = os.path.join(log_dir, name)
            try:
                entries.append((os.path.getmtime(path), path))
            except Exception:
                pass
        entries.sort(reverse=True)  # newest first
        cutoff_sec = time.time() - DEFAULT_RETENTION_DAYS * 86400
        deleted = 0
        for i, (mt, path) in enumerate(entries):
            if i >= DEFAULT_MAX_FILES or mt < cutoff_sec:
                try:
                    os.remove(path)
                    deleted += 1
                except Exception:
                    pass
        return deleted
    except Exception:
        return 0


def open_build_log(build_id: str, log_dir: Optional[str] = None) -> tuple[object, str]:
    """Open a line-buffered log file for the given build. Returns (file_obj, full_path)."""
    log_dir = log_dir or ensure_log_dir()
    path = os.path.join(log_dir, f"build_{build_id}.log")
    f = open(path, "w", buffering=1, encoding="utf-8")
    header = (
        "# nexus_graph_build\n"
        f"# build_id: {build_id}\n"
        f"# started_at: {_now_iso()}\n"
        f"# log_file: {path}\n"
        "# ---\n"
    )
    try:
        f.write(header)
    except Exception:
        pass
    return f, path


def close_build_log(file_obj, status: str, summary: dict | None = None) -> None:
    """Write footer + close the file. Safe to call multiple times."""
    if file_obj is None:
        return
    footer_lines = [
        "# ---",
        f"# completed_at: {_now_iso()}",
        f"# status: {status}",
    ]
    if summary:
        for k, v in summary.items():
            footer_lines.append(f"# {k}: {v}")
    footer = "\n".join(footer_lines) + "\n"
    try:
        file_obj.write(footer)
    except Exception:
        pass
    try:
        file_obj.flush()
        file_obj.close()
    except Exception:
        pass


def build_row_start(
    r,
    conn,
    *,
    mode: str,
    selected_phases: list | None = None,
    requested_range: dict | None = None,
    session=None,
    log_file_path: str | None = None,
    build_id: str | None = None,
) -> str:
    """Insert a new NexusGraphBuilds row for this run. Returns build_id."""
    _ensure_table(r, conn)
    if not build_id:
        build_id = _make_build_id()
    before = _neo4j_counts(session) if session else {"nodes": 0, "edges": 0}
    doc = {
        "id": build_id,
        "started_at": _now_iso(),
        "started_at_iso": _now_iso(),
        "completed_at": None,
        "duration_seconds": None,
        "mode": mode,
        "selected_phases": selected_phases or None,
        "requested_range": requested_range or None,
        "phases_run": [],
        "warnings": [],
        "totals": {
            "nodes_before": int(before["nodes"]),
            "edges_before": int(before["edges"]),
            "nodes_after": None,
            "edges_after": None,
            "new_nodes": None,
            "new_edges": None,
        },
        "log_file_path": log_file_path,
        "final_status": "running",
    }
    # Narrow fallback: only a duplicate id falls through to conflict="replace".
    # Everything else — network loss, auth, serialization — raises StoreError
    # and surfaces, so we don't silently lose build rows. The store reports a
    # duplicate primary key in ``errors``/``first_error`` rather than raising,
    # which is what separates the two cases without a driver exception class.
    res = store.insert(NEXUS_BUILDS_TABLE, doc, conflict="error")
    if res["errors"]:
        # Duplicate id (extremely unlikely: UTC second + 8 hex chars of uuid4).
        # Fall back to replace so the build row still exists for phase appends.
        store.insert(NEXUS_BUILDS_TABLE, doc, conflict="replace")
    return build_id


def build_row_append_phase(
    r,
    conn,
    build_id: str,
    *,
    phase_key: str,
    phase_num: int,
    stage_index: int | None,
    label: str,
    started_at: str,
    duration_sec: float,
    status: str,
    work_units: dict,
    nodes_delta: int = 0,
    edges_delta: int = 0,
    warnings: list | None = None,
    error: str | None = None,
) -> None:
    phase_doc = {
        "phase_key": phase_key,
        "phase_num": phase_num,
        "stage_index": stage_index,
        "label": label,
        "started_at": started_at,
        "completed_at": _now_iso(),
        "duration_sec": float(duration_sec),
        "status": status,
        "work_units": dict(work_units or {}),
        "nodes_delta": int(nodes_delta),
        "edges_delta": int(edges_delta),
        "warnings": list(warnings or []),
        "error": error,
    }
    # r.row["phases_run"].append(x) is a server-side array append with no
    # store equivalent (a deep merge REPLACES arrays), so it becomes a
    # read-modify-write. Only nexus_graph_engine appends to a build row, and
    # only from the single thread that owns that build.
    try:
        _append_to_array(build_id, "phases_run", phase_doc)
    except Exception:
        pass


def build_row_append_warning(r, conn, build_id: str, warning: str) -> None:
    try:
        _append_to_array(build_id, "warnings", warning)
    except Exception:
        pass


def build_row_update_tail(r, conn, build_id: str, log_tail: list) -> None:
    """Periodically refresh the last-500 log lines onto the row for live UI tail."""
    try:
        store.update(NEXUS_BUILDS_TABLE, build_id,
                     {"log_tail": list(log_tail)[-500:],
                      "last_tail_update": _now_iso()})
    except Exception:
        pass


def build_row_finalize(
    r,
    conn,
    build_id: str,
    *,
    status: str,
    session=None,
    log_file_path: str | None = None,
    log_tail: list | None = None,
) -> dict:
    """Mark a build row completed/failed. Returns the summary dict written."""
    after = _neo4j_counts(session) if session else {}
    try:
        existing = store.get(NEXUS_BUILDS_TABLE, build_id) or {}
    except Exception:
        existing = {}
    totals = dict(existing.get("totals") or {})
    if after:
        totals["nodes_after"] = int(after.get("nodes", 0))
        totals["edges_after"] = int(after.get("edges", 0))
        if totals.get("nodes_before") is not None:
            totals["new_nodes"] = totals["nodes_after"] - int(totals["nodes_before"])
        if totals.get("edges_before") is not None:
            totals["new_edges"] = totals["edges_after"] - int(totals["edges_before"])
    started_at_iso = existing.get("started_at_iso")
    duration = None
    if started_at_iso:
        try:
            t0 = datetime.fromisoformat(started_at_iso.replace("Z", "+00:00"))
            duration = (datetime.now(timezone.utc) - t0).total_seconds()
        except Exception:
            duration = None
    updates = {
        "completed_at": _now_iso(),
        "completed_at_iso": _now_iso(),
        "duration_seconds": duration,
        "final_status": status,
        "totals": totals,
    }
    if log_file_path is not None:
        updates["log_file_path"] = log_file_path
    if log_tail is not None:
        updates["log_tail"] = list(log_tail)[-500:]
    try:
        # ``totals`` is rebuilt whole from the row that was just read, and a
        # deep merge of it over the same subtree is identical to a replace.
        store.update(NEXUS_BUILDS_TABLE, build_id, updates)
    except Exception:
        pass
    return {"duration_seconds": duration, **updates}


def read_build_log_file(build_id: str, log_dir: Optional[str] = None) -> Optional[str]:
    """Read the full log file for a build, or None if missing."""
    log_dir = log_dir or ensure_log_dir()
    path = os.path.join(log_dir, f"build_{build_id}.log")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except Exception:
        return None
