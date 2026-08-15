"""The only module in `self_learning` that touches RethinkDB.

Everything else is pure so it unit-tests without a database. Writes are
`conflict="update"` on a content id, which makes both the changefeed path and
the historical backfill idempotent: re-reading a run updates its rows instead of
duplicating them.

Two constraints shape everything here, both learned from this deployment:

* **RethinkDB is the bottleneck.** `PriceHistory` at ~2.3M rows drove 17
  restarts in 12 days on a memory-starved VM, and a single `BacktestResults` row
  carries 5-13MB (`interactive_utils.py:5220`). So: no full-table reads, no
  full-document pulls, ordering and limits pushed into the database, and a
  retention sweep that deletes by RANGE rather than by loading the table.
* **Only actionable decisions are persisted as rows.** A 15-minute-bar run emits
  7-15k decisions, overwhelmingly HOLDs. Writing a row per hold is what would
  turn this into a second elephant, so holds live in the per-run aggregate and
  only buys/sells become rows. The variance guard still sees every observation —
  it runs in memory, before this filter.
"""
from __future__ import annotations

import os

from rethinkdb import RethinkDB

r = RethinkDB()
DB_NAME = "IntelliStock"
RETHINKDB_HOST = os.environ.get("RETHINKDB_HOST", "localhost")
RETHINKDB_PORT = int(os.environ.get("RETHINKDB_PORT", "28015"))

OBSERVATIONS = "LearningObservations"
ROLLUPS = "LearningObservationRollups"
FINDINGS = "LearningFindings"
FUNNELS = "LearningFunnels"
CONFIG = "LearningConfig"

LEARNING_TABLES = (OBSERVATIONS, ROLLUPS, FINDINGS, FUNNELS, CONFIG)

CONFIG_DOC_ID = "LearningConfig"

# A single insert of 15k documents is one oversized request that can fail as a
# unit; chunking keeps each write bounded.
WRITE_CHUNK = 500

DEFAULT_CONFIG = {
    "id": CONFIG_DOC_ID,
    # Phase 1 is observe-only. Later phases widen this; it is a stored value
    # rather than a code constant so widening it is an operator action.
    "mode": "observe",
    "enabled": True,
    "retain_days": 90,
    "variance_threshold": 0.95,
    "variance_min_n": 30,
    # Empty until an operator arms a document. Nothing is promotable on day one
    # anyway: no target has a measured noise floor yet.
    "document_allowlist": [],
    # Persisted watermark. Without it every container restart re-pulls every
    # completed run — multi-GB of document transfer and a multi-million-row
    # rewrite storm on a host that restarts often.
    "processed_run_ids": [],
}

_MUTABLE_KEYS = frozenset({
    "mode", "enabled", "retain_days", "variance_threshold", "variance_min_n",
    "document_allowlist",
})


def get_conn():
    return r.connect(host=RETHINKDB_HOST, port=RETHINKDB_PORT)


def ensure_tables(conn) -> None:
    dbs = list(r.db_list().run(conn))
    if DB_NAME not in dbs:
        r.db_create(DB_NAME).run(conn)
    existing = list(r.db(DB_NAME).table_list().run(conn))
    for table in LEARNING_TABLES:
        if table not in existing:
            r.db(DB_NAME).table_create(table).run(conn)
    idxs = list(r.db(DB_NAME).table(OBSERVATIONS).index_list().run(conn))
    # `run_id` serves the per-run read; `as_of` serves the retention range
    # delete. A `strategy_id` index was created by an earlier version and never
    # queried — pure write amplification on the largest table.
    for index in ("run_id", "as_of"):
        if index not in idxs:
            r.db(DB_NAME).table(OBSERVATIONS).index_create(index).run(conn)
            r.db(DB_NAME).table(OBSERVATIONS).index_wait(index).run(conn)
    for table, index in ((FINDINGS, "detected_at"), (FUNNELS, "observed_at")):
        existing_idx = list(r.db(DB_NAME).table(table).index_list().run(conn))
        if index not in existing_idx:
            r.db(DB_NAME).table(table).index_create(index).run(conn)
            r.db(DB_NAME).table(table).index_wait(index).run(conn)
    seed_config(conn)


def merge_config(doc) -> dict:
    merged = dict(DEFAULT_CONFIG)
    if isinstance(doc, dict):
        merged.update({k: v for k, v in doc.items() if v is not None})
    return merged


def seed_config(conn) -> None:
    """Write the defaults once, so the levers are reachable.

    A config nothing ever writes is a set of inert levers, and this repo has
    shipped thirteen of those.
    """
    try:
        if r.db(DB_NAME).table(CONFIG).get(CONFIG_DOC_ID).run(conn) is None:
            r.db(DB_NAME).table(CONFIG).insert(dict(DEFAULT_CONFIG)).run(conn)
    except Exception:
        pass


def get_config(conn) -> dict:
    """Read the config. Raises on failure — the caller decides how to degrade.

    Swallowing the error and returning defaults meant `enabled: True` during a
    database outage, i.e. the kill switch failed OPEN exactly when back-off was
    wanted.
    """
    doc = r.db(DB_NAME).table(CONFIG).get(CONFIG_DOC_ID).run(conn)
    return merge_config(doc)


def put_config(conn, patch: dict) -> dict:
    """Update the operator-settable keys. Unknown keys are ignored."""
    clean = {k: v for k, v in (patch or {}).items() if k in _MUTABLE_KEYS}
    if clean:
        clean["id"] = CONFIG_DOC_ID
        r.db(DB_NAME).table(CONFIG).insert(clean, conflict="update").run(conn)
    return get_config(conn)


def mark_processed(conn, run_id) -> None:
    """Record a run as handled, so a restart does not re-pull every document."""
    r.db(DB_NAME).table(CONFIG).get(CONFIG_DOC_ID).update(
        lambda doc: {"processed_run_ids": doc["processed_run_ids"]
                     .default([]).set_insert(str(run_id))}
    ).run(conn)


def persistable(observations) -> list:
    """The observations that become rows.

    Holds are the bulk of a run and carry no refusal signal; they survive in the
    per-run aggregate. This is the write-tier split the design calls for, and it
    is what keeps the table from reaching PriceHistory scale.
    """
    return [o for o in (observations or []) if o.decision != 0]


def observation_payloads(observations) -> list:
    return [o.to_doc() for o in persistable(observations)]


def _insert_chunked(conn, table, payloads) -> int:
    written = 0
    for start in range(0, len(payloads), WRITE_CHUNK):
        chunk = payloads[start:start + WRITE_CHUNK]
        r.db(DB_NAME).table(table).insert(chunk, conflict="update").run(conn)
        written += len(chunk)
    return written


def put_observations(conn, observations) -> int:
    payloads = observation_payloads(observations)
    if not payloads:
        return 0
    return _insert_chunked(conn, OBSERVATIONS, payloads)


def put_findings(conn, findings) -> int:
    payloads = [f.to_doc() for f in (findings or [])]
    if not payloads:
        return 0
    for payload in payloads:
        # Never clobber an operator's acknowledgement. `conflict="update"` on a
        # doc that always carries status="open" would silently reopen every
        # resolved finding on the next detection.
        payload.pop("status", None)
    r.db(DB_NAME).table(FINDINGS).insert(
        payloads, conflict=lambda _id, old, new: new.merge(
            {"status": old["status"].default("open"),
             "first_detected_at": old["first_detected_at"].default(
                 new["detected_at"])})
    ).run(conn)
    return len(payloads)


def put_funnel(conn, run_id, summary, *, origin="backtest", target="",
               observed_at="") -> None:
    """`observed_at` must be the RUN's time, not the processing time — stamping
    "now" makes every restart rewrite the ordering the UI reads."""
    r.db(DB_NAME).table(FUNNELS).insert({
        "id": f"{origin}|{run_id}", "run_id": str(run_id), "origin": origin,
        "target": target, "observed_at": observed_at, **(summary or {}),
    }, conflict="update").run(conn)


def list_findings(conn, limit: int = 100) -> list:
    """Ordered and limited in the DATABASE. Loading the table into Python and
    slicing afterwards is an OOM waiting for the table to grow."""
    limit = max(1, min(int(limit or 100), 1000))
    return list(r.db(DB_NAME).table(FINDINGS)
                .order_by(index=r.desc("detected_at")).limit(limit).run(conn))


def list_funnels(conn, limit: int = 100) -> list:
    limit = max(1, min(int(limit or 100), 1000))
    return list(r.db(DB_NAME).table(FUNNELS)
                .order_by(index=r.desc("observed_at")).limit(limit).run(conn))


def list_observations(conn, run_id, limit: int = 500) -> list:
    limit = max(1, min(int(limit or 500), 2000))
    return list(r.db(DB_NAME).table(OBSERVATIONS)
                .get_all(str(run_id), index="run_id")
                .order_by("as_of").limit(limit).run(conn))


def counts(conn) -> dict:
    """Totals computed BY the database.

    `overview` used to sum a 500-row slice and present it as a total, so both
    the run count and the decision count silently pinned once the table passed
    the limit.
    """
    findings_open = r.db(DB_NAME).table(FINDINGS).filter(
        lambda doc: doc["status"].default("open").eq("open")).count().run(conn)
    by_severity = r.db(DB_NAME).table(FINDINGS).filter(
        lambda doc: doc["status"].default("open").eq("open")
    ).group("severity").count().run(conn)
    runs = r.db(DB_NAME).table(FUNNELS).count().run(conn)
    sums = r.db(DB_NAME).table(FUNNELS).map(lambda doc: {
        "decided": doc["decided"].default(0),
        "refused": doc["refused"].default(0),
    }).reduce(lambda a, b: {"decided": a["decided"] + b["decided"],
                            "refused": a["refused"] + b["refused"]}
              ).default({"decided": 0, "refused": 0}).run(conn)
    return {
        "open_findings": int(findings_open or 0),
        "by_severity": {str(k): int(v) for k, v in (by_severity or {}).items()},
        "runs_observed": int(runs or 0),
        "decisions_observed": int((sums or {}).get("decided") or 0),
        "refusals_observed": int((sums or {}).get("refused") or 0),
    }


def sweep_expired(conn, *, cutoff: str) -> int:
    """Delete expired observations with a server-side RANGE delete.

    The pure `retention.expired_ids` takes a materialised list, which is the one
    thing that must never happen to a multi-million-row table — the remedy for
    the bottleneck would itself OOM. `cutoff` comes from `retention.cutoff_iso`.
    """
    if not cutoff:
        return 0
    result = (r.db(DB_NAME).table(OBSERVATIONS)
              .between(r.minval, cutoff, index="as_of")
              .delete().run(conn))
    return int((result or {}).get("deleted") or 0)
