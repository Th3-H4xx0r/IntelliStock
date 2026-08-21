#!/usr/bin/env python3
"""Create the secondary indices the /backtests list fast path needs.

    python3 scripts/create_backtest_list_indices.py

Why: `GET /backtests` plucked EVERY BacktestResults row on every page load.
Pluck still makes RethinkDB load each full document (5-13MB of decisions/
prices/logs per row), so one list render read the entire table — measured
12.2s for 1,426 rows on 2026-08-21 — and pagination happened in Python after
the damage. With these indices the endpoint reads ~per_page documents.

Indices (idempotent; safe to run while the API serves traffic):
  * ``list_ts``      — plain index on ``timestamp`` (STRING on every row;
                       ISO-8601, so lexicographic order IS chronological;
                       ``completed_at``/``started_at``/``created_at`` are NULL
                       on all 1,426 rows — ``timestamp`` is the only real
                       time field).
  * ``status_norm``  — ``paused_<anything>`` collapses to ``paused``, else
                       the lower-cased status; lets the active rows (running/
                       queued/pending/paused) be fetched with one get_all.
  * ``instance_ts``  — compound [instance-as-string, timestamp] so the
                       per-instance list page is an index range scan ordered
                       by time (``instance_id`` is NUMBER on 592 rows and
                       STRING on 833 — the coercion matches the endpoint's
                       existing string comparison).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "scripts"))
from pull_backtest_logs import _load_dotenv  # noqa: E402

_load_dotenv(_REPO)

from rethinkdb import RethinkDB  # noqa: E402

DB = "IntelliStock"
TABLE = "BacktestResults"


def main() -> int:
    r = RethinkDB()
    conn = r.connect(host=os.environ["RETHINKDB_HOST"],
                     port=int(os.environ.get("RETHINKDB_PORT", "28015")),
                     timeout=30)
    tbl = r.db(DB).table(TABLE)
    have = set(tbl.index_list().run(conn))

    wanted = {
        "list_ts": lambda row: row["timestamp"].default(""),
        "status_norm": lambda row: r.branch(
            row["status"].default("").coerce_to("string").downcase()
            .match("^paused"),
            "paused",
            row["status"].default("").coerce_to("string").downcase(),
        ),
        "instance_ts": lambda row: [
            row["instance_id"].default(row["instance"]).default("")
            .coerce_to("string"),
            row["timestamp"].default(""),
        ],
    }

    created = []
    for name, fn in wanted.items():
        if name in have:
            print(f"  {name}: already exists")
            continue
        tbl.index_create(name, fn).run(conn)
        created.append(name)
        print(f"  {name}: created")
    if created:
        tbl.index_wait(*created).run(conn)
        print(f"ready: {', '.join(created)}")
    print("all indices:", sorted(tbl.index_list().run(conn)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
