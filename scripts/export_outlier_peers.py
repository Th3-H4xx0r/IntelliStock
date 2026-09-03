#!/usr/bin/env python3
"""Export Nexus peer sets to OutlierGraphPeers (one row per ticker).

    python3 scripts/export_outlier_peers.py

Peers = COMPETES_WITH | SUPPLIER_OF | STRATEGIC_PARTNER | PARENT_OF | CONTROLS,
either direction. Static structure (industry membership), not a dated signal;
the lane never queries Neo4j itself (strict-PIT replay forbids new Cypher).
Needs NEO4J_URI / NEO4J_USER / NEO4J_PASSWORD in the environment.
"""
from __future__ import annotations

import collections
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "backend"))

from outlier_features import PEERS_TABLE  # noqa: E402

_Q_NODES = ("MATCH (c:Company) WHERE c.ticker IS NOT NULL "
            "RETURN c.ticker AS t, c.sector AS s, c.industry AS i")
_Q_EDGES = ("MATCH (a:Company)-[:COMPETES_WITH|SUPPLIER_OF|STRATEGIC_PARTNER|PARENT_OF|CONTROLS]-(b:Company) "
            "WHERE a.ticker IS NOT NULL AND b.ticker IS NOT NULL RETURN a.ticker AS a, b.ticker AS b")


def main():
    from neo4j import GraphDatabase
    from db import store
    drv = GraphDatabase.driver(
        os.environ["NEO4J_URI"],
        auth=(os.environ.get("NEO4J_USER", "neo4j"), os.environ["NEO4J_PASSWORD"]),
        connection_timeout=15)
    sector, industry, peers = {}, {}, collections.defaultdict(set)
    with drv.session() as s:
        for r in s.run(_Q_NODES):
            sector[r["t"]] = r["s"]
            industry[r["t"]] = r["i"]
        for r in s.run(_Q_EDGES):
            peers[r["a"]].add(r["b"])
    drv.close()
    docs = [{"id": t.upper(), "sector": sector.get(t), "industry": industry.get(t),
             "peers": sorted(p.upper() for p in ps)} for t, ps in peers.items()]
    for i in range(0, len(docs), 2000):
        store.insert(PEERS_TABLE, docs[i:i + 2000], conflict="replace")
    print(f"exported {len(docs)} tickers with peers", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
