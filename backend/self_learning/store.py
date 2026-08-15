"""The only module in `self_learning` that touches RethinkDB.

Everything else is pure so it unit-tests without a database. Writes are
`conflict="update"` on a content id, which makes both the changefeed path and
the historical backfill idempotent: re-reading a run updates its rows instead of
duplicating them.
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
}


def get_conn():
    return r.connect(host=RETHINKDB_HOST, port=RETHINKDB_PORT)


def ensure_tables(conn) -> None:
    existing = list(r.db(DB_NAME).table_list().run(conn))
    for table in LEARNING_TABLES:
        if table not in existing:
            r.db(DB_NAME).table_create(table).run(conn)
    idxs = list(r.db(DB_NAME).table(OBSERVATIONS).index_list().run(conn))
    for index in ("run_id", "strategy_id"):
        if index not in idxs:
            r.db(DB_NAME).table(OBSERVATIONS).index_create(index).run(conn)
            r.db(DB_NAME).table(OBSERVATIONS).index_wait(index).run(conn)


def merge_config(doc) -> dict:
    merged = dict(DEFAULT_CONFIG)
    if isinstance(doc, dict):
        merged.update({k: v for k, v in doc.items() if v is not None})
    return merged


def get_config(conn) -> dict:
    try:
        doc = r.db(DB_NAME).table(CONFIG).get(CONFIG_DOC_ID).run(conn)
    except Exception:
        doc = None
    return merge_config(doc)


def observation_payloads(observations) -> list:
    return [o.to_doc() for o in (observations or [])]


def put_observations(conn, observations) -> int:
    payloads = observation_payloads(observations)
    if not payloads:
        return 0
    r.db(DB_NAME).table(OBSERVATIONS).insert(
        payloads, conflict="update").run(conn)
    return len(payloads)


def put_findings(conn, findings) -> int:
    payloads = [f.to_doc() for f in (findings or [])]
    if not payloads:
        return 0
    r.db(DB_NAME).table(FINDINGS).insert(payloads, conflict="update").run(conn)
    return len(payloads)


def put_funnel(conn, run_id, summary, *, origin="backtest", target="",
               observed_at="") -> None:
    r.db(DB_NAME).table(FUNNELS).insert({
        "id": f"{origin}|{run_id}", "run_id": str(run_id), "origin": origin,
        "target": target, "observed_at": observed_at, **(summary or {}),
    }, conflict="update").run(conn)


def list_findings(conn, limit: int = 100) -> list:
    rows = list(r.db(DB_NAME).table(FINDINGS).run(conn))
    rows.sort(key=lambda d: str(d.get("detected_at") or ""), reverse=True)
    return rows[:max(1, int(limit))]


def list_observations(conn, run_id, limit: int = 500) -> list:
    rows = list(r.db(DB_NAME).table(OBSERVATIONS)
                .get_all(str(run_id), index="run_id").run(conn))
    rows.sort(key=lambda d: str(d.get("as_of") or ""))
    return rows[:max(1, int(limit))]


def list_funnels(conn, limit: int = 100) -> list:
    rows = list(r.db(DB_NAME).table(FUNNELS).run(conn))
    rows.sort(key=lambda d: str(d.get("observed_at") or ""), reverse=True)
    return rows[:max(1, int(limit))]
