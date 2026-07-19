"""Phase-1 bull-participation levers on Strategies doc 179 (2026-07-19).

Grounded in the 3-regime forensics (backtests 868240/940004/851037):
- fast_loser_cut_recent_runup_block_pct 0 -> 20: stops day-1 whipsaw cuts on
  hot entries (NEUTRAL lost $415 to 49 fires; BEAR had 0 fires and must stay 0).
- max_positions 8 -> 14 with regime caps pinned: bull 14 (STX +56% starved
  behind headroom=0 in 105/106 queue drains), chop 8 and bear 8 EXPLICITLY
  frozen at today's effective values so NEUTRAL churn and BEAR protection
  invariants are untouched.
- etf_portfolio_pct -> 0.15 (trend-gated broad sleeve; self-disables in bear).
- deployment_bar1_cap_pct -> 0.9 (deploy faster in confirmed moves).

Dry-run by default; --apply writes; --revert <backup.json> restores.
Backup JSON: scripts/doc179_backup_bull_levers_<ts>.json (never committed).
"""
import argparse
import datetime
import json
import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO, "backend"))

DOC_ID = 179
CHANGES = {
    "fast_loser_cut_recent_runup_block_pct": 20.0,
    "max_positions": 14,
    "max_positions_bull": 14,
    "max_positions_chop": 8,
    "max_positions_bear": 8,
    "etf_portfolio_pct": 0.15,
    "deployment_bar1_cap_pct": 0.9,
}


def _conn():
    from dotenv import load_dotenv
    load_dotenv(os.path.join(_REPO, ".env"))
    from rethinkdb import RethinkDB
    r = RethinkDB()
    return r, r.connect(host=os.environ["RETHINKDB_HOST"],
                        port=int(os.environ.get("RETHINKDB_PORT", "28015")),
                        timeout=20)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--revert", default=None,
                        help="Path to a backup JSON to restore.")
    args = parser.parse_args(argv)

    r, conn = _conn()
    doc = r.db("IntelliStock").table("Strategies").get(DOC_ID).run(conn)
    if not doc:
        print(f"Strategies doc {DOC_ID} not found", file=sys.stderr)
        return 2
    specs = doc.get("strategies") or []
    idx = next((i for i, s in enumerate(specs)
                if str(s.get("strategy")) == "graph_nexus_analysis"), None)
    if idx is None:
        print("graph_nexus_analysis spec not found in doc", file=sys.stderr)
        return 2
    config = dict(specs[idx].get("config") or {})

    if args.revert:
        with open(args.revert) as fh:
            backup = json.load(fh)
        for key, value in backup["old_values"].items():
            if value is None:
                config.pop(key, None)
            else:
                config[key] = value
        specs[idx]["config"] = config
        r.db("IntelliStock").table("Strategies").get(DOC_ID).update(
            {"strategies": specs}).run(conn)
        print(f"REVERTED {len(backup['old_values'])} keys from {args.revert}")
        return 0

    old_values = {key: config.get(key) for key in CHANGES}
    mode = "APPLY" if args.apply else "DRY-RUN"
    for key, new in CHANGES.items():
        print(f"[{mode}] {key}: {old_values[key]!r} -> {new!r}")
    if not args.apply:
        return 0

    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = os.path.join(_REPO, "scripts",
                               f"doc179_backup_bull_levers_{ts}.json")
    with open(backup_path, "w") as fh:
        json.dump({"doc_id": DOC_ID, "applied_at": ts,
                   "old_values": old_values, "new_values": CHANGES}, fh, indent=1)
    config.update(CHANGES)
    specs[idx]["config"] = config
    r.db("IntelliStock").table("Strategies").get(DOC_ID).update(
        {"strategies": specs}).run(conn)
    print(f"APPLIED. Backup: {backup_path}")
    print("Revert with: python3 scripts/apply_doc179_bull_participation_levers.py "
          f"--revert {backup_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
