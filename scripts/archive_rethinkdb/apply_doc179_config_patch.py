"""Generic merge-patcher for prod Strategies doc 179 (graph_nexus_analysis config).

Applies {key: value} pairs from a JSON file onto doc-179's nexus config,
backing up prior values first; --revert restores a backup (null/absent old
values are deleted). Same merge-only pattern as
apply_doc179_bull_participation_levers.py.

Usage:
  python3 scripts/apply_doc179_config_patch.py --patch patch.json           # dry-run
  python3 scripts/apply_doc179_config_patch.py --patch patch.json --apply
  python3 scripts/apply_doc179_config_patch.py --revert <backup.json>
"""
import argparse
import datetime
import json
import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOC_ID = 179
_ABSENT = object()


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
    parser.add_argument("--patch", default=None, help="JSON file of key->value to apply.")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--revert", default=None, help="Backup JSON to restore.")
    args = parser.parse_args(argv)
    if not args.patch and not args.revert:
        parser.error("need --patch or --revert")

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

    with open(args.patch) as fh:
        changes = json.load(fh)
    old_values = {key: config.get(key) for key in changes}
    mode = "APPLY" if args.apply else "DRY-RUN"
    for key, new in changes.items():
        print(f"[{mode}] {key}: {old_values[key]!r} -> {new!r}")
    if not args.apply:
        return 0

    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = os.path.join(_REPO, "scripts", f"doc179_backup_patch_{ts}.json")
    with open(backup_path, "w") as fh:
        json.dump({"doc_id": DOC_ID, "applied_at": ts,
                   "old_values": old_values, "new_values": changes}, fh, indent=1)
    config.update(changes)
    specs[idx]["config"] = config
    r.db("IntelliStock").table("Strategies").get(DOC_ID).update(
        {"strategies": specs}).run(conn)
    print(f"APPLIED. Backup: {backup_path}")
    print(f"Revert with: python3 scripts/apply_doc179_config_patch.py --revert {backup_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
