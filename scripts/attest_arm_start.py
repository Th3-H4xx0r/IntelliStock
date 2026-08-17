#!/usr/bin/env python3
"""Fingerprint an instance's Nexus state, so a paired A/B can prove how it started.

    # before each arm
    python3 scripts/attest_arm_start.py v2-conv-trt --out arm_control.json
    python3 scripts/attest_arm_start.py v2-conv-trt --out arm_treatment.json

    # then, before quoting any delta
    python3 scripts/attest_arm_start.py --compare arm_control.json arm_treatment.json

Why: bt 333727 vs bt 453789 — same document, window, instance, granularity and cash, ONE
config flag apart — shared 4 of 20 traded names. Both arms carried the isolation recipe the
handoffs call the one that works. It was not enough, because per-instance Nexus rows survive
between runs and seed the next discovery differently.

Read-only. It never writes or deletes; clearing is `scripts/clear_backtest_state.py`.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "backend"))

from paired_state_attest import (  # noqa: E402
    ATTESTED_TABLES,
    compare_arm_starts,
    is_cold,
    state_fingerprint,
)

DB_NAME = "IntelliStock"

#: How each attested table is scoped to an instance. Mirrors
#: `clear_backtest_state._build_per_instance_targets`: a table worth clearing between arms
#: is exactly a table worth attesting between arms.
_SCOPE = {
    "GraphNexusDiscoveredStocks": ("instance_id", "exact"),
    "GraphNexusMarketTrends": ("instance_id", "exact"),
    "GraphNexusDiscoverySnapshots": ("id", "exact"),
    "GraphNexusRotationCooldown": ("id", "exact"),
    "GraphNexusLearningCache": ("id", "prefix"),
    "GraphNexusOutcomes": ("instance_id", "exact"),
    "GraphNexusOutcomeSeries": ("instance_id", "exact"),
    "GraphNexusTradeContexts": ("instance_id", "exact"),
    "GraphNexusTradeOutcomes": ("instance_id", "exact"),
    "GraphNexusAnalystPanel": ("instance_id", "exact"),
    "NexusStrategyCache": ("instance_id", "exact"),
    "NexusRuntimeState": ("id", "prefix"),
    "LiveState": ("id", "exact"),
}


def _connect():
    from rethinkdb import RethinkDB  # noqa: PLC0415

    r = RethinkDB()
    host = os.environ.get("RETHINKDB_HOST", "localhost")
    port = int(os.environ.get("RETHINKDB_PORT", "28015"))
    return r, r.connect(host=host, port=port, db=DB_NAME)


def _read_tables(instance_id):
    r, conn = _connect()
    try:
        present = set(r.db(DB_NAME).table_list().run(conn))
    except Exception:
        present = set()
    out = {}
    for name in ATTESTED_TABLES:
        if name not in present:
            continue                      # absent stays absent — a distinct claim
        field, _mode = _SCOPE.get(name, ("instance_id", "exact"))
        q = r.db(DB_NAME).table(name)
        try:
            # ALWAYS base-id prefix, never exact. Nexus tables key the instance
            # SCOPE-SUFFIXED (`v2-conv-trt|<config-hash>`), so an exact match on the
            # base id silently returns a fraction of the rows — and this tool would then
            # report an arm as COLD while a previous run's discoveries were still sitting
            # in a scoped row, which is the exact failure it exists to catch. The base-id
            # trap is already recorded in this project's memory for the dashboard cards.
            base = str(instance_id).split("|", 1)[0]
            q = q.filter(
                lambda row: (row[field].default("") == base)
                | row[field].default("").match(f"^{base}\\|")
                | row[field].default("").match(f"^{base}:")
            )
            out[name] = list(q.run(conn))
        except Exception as exc:
            print(f"  WARN {name}: {exc}", file=sys.stderr)
    try:
        conn.close()
    except Exception:
        pass
    return out


def _print(fp, label):
    print(f"{label}: {fp['total_rows']} row(s) across {len(fp['tables'])} attested table(s)"
          f"  cold={is_cold(fp)}")
    for name, spec in sorted(fp["tables"].items()):
        if spec.get("absent"):
            continue
        if spec.get("rows"):
            print(f"    {name:<32} {spec['rows']:>6} rows  {spec['sha256'][:19]}")
    print(f"  bundle: {fp['bundle_sha256']}")


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("instance_id", nargs="?")
    p.add_argument("--out")
    p.add_argument("--compare", nargs=2, metavar=("CONTROL", "TREATMENT"))
    p.add_argument("--allow-warm", action="store_true",
                   help="accept identical WARM starts (say so in the write-up)")
    p.add_argument("--for-mode", choices=("backtest", "live"), default="backtest",
                   help="which rows can steer the run. 'backtest' ignores "
                        "origin=backtest NexusStrategyCache snapshots, which "
                        "clear_instance_state preserves by design and which no backtest "
                        "reads; 'live' counts them, because live boot does read them.")
    a = p.parse_args(argv)

    if a.compare:
        ctl = json.loads(Path(a.compare[0]).read_text())
        trt = json.loads(Path(a.compare[1]).read_text())
        _print(ctl, "control  ")
        _print(trt, "treatment")
        r = compare_arm_starts(ctl, trt, require_cold=not a.allow_warm)
        print(f"\nVERDICT: {r['verdict']} — {r['reason']}")
        return 0 if r["verdict"].startswith("IDENTICAL") else 2

    if not a.instance_id:
        p.error("instance_id is required unless --compare is used")
    fp = state_fingerprint(_read_tables(a.instance_id), for_mode=a.for_mode)
    _print(fp, f"{a.instance_id} [{a.for_mode}]")
    if a.out:
        Path(a.out).write_text(json.dumps(fp, indent=1))
        print(f"\nwrote {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
