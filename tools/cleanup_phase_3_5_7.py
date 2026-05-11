import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from neo4j import GraphDatabase


ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")


def log(message: str) -> None:
    print(message, flush=True)


def scalar(session, query: str, **params) -> int:
    record = session.run(query, **params).single()
    if not record:
        return 0
    value = list(record.values())[0]
    return int(value or 0)


def run_iterate(session, match_query: str, delete_query: str, *, batch_size: int, **params) -> dict:
    query = (
        "CALL apoc.periodic.iterate("
        "$match_query,"
        "$delete_query,"
        "{batchSize:$batch_size, parallel:false, retries:0, params:$job_params}"
        ") "
        "YIELD batches, total, errorMessages "
        "RETURN batches, total, errorMessages"
    )
    record = session.run(
        query,
        match_query=match_query,
        delete_query=delete_query,
        batch_size=batch_size,
        job_params=params,
    ).single()
    return dict(record or {})


def cleanup_phase3_and_phase5(session) -> None:
    actions = [
        (
            "Phase 3 SUPPLIER_OF edges",
            "MATCH ()-[r:SUPPLIER_OF]->() WITH r LIMIT 1000000 DELETE r RETURN count(r) AS c",
        ),
        (
            "Phase 3 10-K STRATEGIC_PARTNER edges",
            "MATCH ()-[r:STRATEGIC_PARTNER {source_scope:'SEC_10K_STRATEGIC_PARTNER'}]-() "
            "WITH r LIMIT 1000000 DELETE r RETURN count(r) AS c",
        ),
        (
            "Phase 3 interval nodes",
            "MATCH (g:GraphEdgeInterval) "
            "WHERE g.rel_type='SUPPLIER_OF' "
            "   OR (g.rel_type='STRATEGIC_PARTNER' AND g.source_scope='SEC_10K_STRATEGIC_PARTNER') "
            "WITH g LIMIT 1000000 DETACH DELETE g RETURN count(g) AS c",
        ),
        (
            "Phase 5 SUPPLIES_TO_SECTOR edges",
            "MATCH ()-[r:SUPPLIES_TO_SECTOR]->() WITH r LIMIT 1000000 DELETE r RETURN count(r) AS c",
        ),
        (
            "Phase 5 EXPOSED_TO edges",
            "MATCH ()-[r:EXPOSED_TO]->() WITH r LIMIT 1000000 DELETE r RETURN count(r) AS c",
        ),
        (
            "Phase 5 interval nodes",
            "MATCH (g:GraphEdgeInterval) "
            "WHERE g.rel_type IN ['SUPPLIES_TO_SECTOR','EXPOSED_TO'] "
            "WITH g LIMIT 1000000 DETACH DELETE g RETURN count(g) AS c",
        ),
        (
            "Phase 5 Commodity nodes",
            "MATCH (c:Commodity) WITH c LIMIT 1000000 DETACH DELETE c RETURN count(c) AS c",
        ),
    ]
    for label, query in actions:
        deleted = scalar(session, query)
        log(f"{label}: deleted {deleted:,}")


def cleanup_phase7_holds(session, *, batch_size: int) -> None:
    periods = [
        row["period"]
        for row in session.run(
            "MATCH ()-[r:HOLDS {source_scope:'13F_HR'}]->() "
            "RETURN r.filing_period AS period, count(r) AS c ORDER BY period"
        )
        if row["period"]
    ]
    if not periods:
        log("Phase 7 HOLDS edges: already zero")
        return

    for period in periods:
        before = scalar(
            session,
            "MATCH ()-[r:HOLDS {source_scope:'13F_HR', filing_period:$period}]->() RETURN count(r) AS c",
            period=period,
        )
        if before == 0:
            continue
        log(f"Phase 7 HOLDS edges: deleting filing_period={period} ({before:,} edges)...")
        started = time.time()
        result = run_iterate(
            session,
            "MATCH ()-[r:HOLDS {source_scope:'13F_HR', filing_period:$period}]->() RETURN r",
            "DELETE r",
            batch_size=batch_size,
            period=period,
        )
        remaining = scalar(
            session,
            "MATCH ()-[r:HOLDS {source_scope:'13F_HR', filing_period:$period}]->() RETURN count(r) AS c",
            period=period,
        )
        log(
            f"Phase 7 HOLDS edges: {period} finished in {time.time() - started:.1f}s "
            f"(batches={result.get('batches', 0)}, total={result.get('total', 0)}, remaining={remaining:,})"
        )
        if result.get("errorMessages"):
            log(f"Phase 7 HOLDS edges: {period} errors -> {result['errorMessages']}")


def cleanup_phase7_intervals(session, *, batch_size: int) -> None:
    periods = [
        row["period"]
        for row in session.run(
            "MATCH (g:GraphEdgeInterval) "
            "WHERE g.rel_type='HOLDS' AND g.source_scope='13F_HR' "
            "RETURN g.filing_period AS period, count(g) AS c ORDER BY period"
        )
        if row["period"]
    ]
    if not periods:
        log("Phase 7 HOLDS interval history: already zero")
        return

    for period in periods:
        before = scalar(
            session,
            "MATCH (g:GraphEdgeInterval) "
            "WHERE g.rel_type='HOLDS' AND g.source_scope='13F_HR' AND g.filing_period=$period "
            "RETURN count(g) AS c",
            period=period,
        )
        if before == 0:
            continue
        log(f"Phase 7 HOLDS interval history: deleting filing_period={period} ({before:,} nodes)...")
        started = time.time()
        result = run_iterate(
            session,
            "MATCH (g:GraphEdgeInterval) "
            "WHERE g.rel_type='HOLDS' AND g.source_scope='13F_HR' AND g.filing_period=$period "
            "RETURN g",
            "DETACH DELETE g",
            batch_size=batch_size,
            period=period,
        )
        remaining = scalar(
            session,
            "MATCH (g:GraphEdgeInterval) "
            "WHERE g.rel_type='HOLDS' AND g.source_scope='13F_HR' AND g.filing_period=$period "
            "RETURN count(g) AS c",
            period=period,
        )
        log(
            f"Phase 7 HOLDS interval history: {period} finished in {time.time() - started:.1f}s "
            f"(batches={result.get('batches', 0)}, total={result.get('total', 0)}, remaining={remaining:,})"
        )
        if result.get("errorMessages"):
            log(f"Phase 7 HOLDS interval history: {period} errors -> {result['errorMessages']}")


def cleanup_institutions(session, *, batch_size: int) -> None:
    total_deleted = 0
    while True:
        deleted = scalar(
            session,
            "MATCH (i:Institution) WITH i LIMIT $batch_size DELETE i RETURN count(i) AS c",
            batch_size=batch_size,
        )
        total_deleted += deleted
        log(f"Phase 7 Institution nodes: deleted batch={deleted:,}, total={total_deleted:,}")
        if deleted < batch_size:
            break


def main() -> int:
    uri = os.environ.get("NEO4J_URI", "").strip()
    user = os.environ.get("NEO4J_USER", "").strip()
    password = os.environ.get("NEO4J_PASSWORD", "").strip()
    if not uri or not user or not password:
        log("Missing Neo4j credentials in .env")
        return 1

    holds_batch_size = int(os.environ.get("PHASE7_DELETE_HOLDS_BATCH_SIZE", "5000"))
    interval_batch_size = int(os.environ.get("PHASE7_DELETE_HOLDS_INTERVAL_BATCH_SIZE", "5000"))
    institution_batch_size = int(os.environ.get("PHASE7_DELETE_INSTITUTION_BATCH_SIZE", "1000"))

    log(f"Connecting to {uri} as {user}...")
    with GraphDatabase.driver(uri, auth=(user, password)) as driver, driver.session() as session:
        log("Starting Phase 3 / 5 cleanup...")
        cleanup_phase3_and_phase5(session)

        log("Starting Phase 7 HOLDS cleanup...")
        cleanup_phase7_holds(session, batch_size=holds_batch_size)

        log("Starting Phase 7 interval cleanup...")
        cleanup_phase7_intervals(session, batch_size=interval_batch_size)

        log("Removing now-orphan Institution nodes...")
        cleanup_institutions(session, batch_size=institution_batch_size)

        summary = {
            "phase3_supplier_edges": scalar(session, "MATCH ()-[r:SUPPLIER_OF]->() RETURN count(r) AS c"),
            "phase3_10k_partner_edges": scalar(session, "MATCH ()-[r:STRATEGIC_PARTNER {source_scope:'SEC_10K_STRATEGIC_PARTNER'}]-() RETURN count(r) AS c"),
            "phase5_supply_edges": scalar(session, "MATCH ()-[r:SUPPLIES_TO_SECTOR]->() RETURN count(r) AS c"),
            "phase5_exposed_edges": scalar(session, "MATCH ()-[r:EXPOSED_TO]->() RETURN count(r) AS c"),
            "phase5_commodities": scalar(session, "MATCH (c:Commodity) RETURN count(c) AS c"),
            "phase7_holds_edges": scalar(session, "MATCH ()-[r:HOLDS]->() RETURN count(r) AS c"),
            "phase7_holds_intervals": scalar(session, "MATCH (g:GraphEdgeInterval) WHERE g.rel_type='HOLDS' RETURN count(g) AS c"),
            "institutions": scalar(session, "MATCH (i:Institution) RETURN count(i) AS c"),
        }
        log(f"Final counts: {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
