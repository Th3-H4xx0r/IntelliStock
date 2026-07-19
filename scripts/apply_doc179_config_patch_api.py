"""API-based merge-patcher for Strategies doc 179 (fallback when direct
RethinkDB over Tailscale is unreachable). Same semantics and backup format
as apply_doc179_config_patch.py: GET the full doc, mutate ONLY the given
keys inside the graph_nexus_analysis config, PUT the doc back.

Usage:
  python3 scripts/apply_doc179_config_patch_api.py --patch patch.json [--apply]
  python3 scripts/apply_doc179_config_patch_api.py --revert <backup.json>
"""
import argparse
import datetime
import json
import os
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "scripts"))
from pull_backtest_logs import _load_dotenv, _login, _http, _fetch  # noqa: E402

DOC_ID = 179


def _auth():
    _load_dotenv(_REPO)
    api = (os.environ.get("INTELLISTOCK_API_URL") or os.environ.get("API_URL")
           or f"http://localhost:{os.environ.get('API_PORT', '8000')}").rstrip("/")
    token = os.environ.get("INTELLISTOCK_API_TOKEN") or _login(
        api,
        os.environ.get("INTELLISTOCK_USERNAME") or os.environ.get("DEFAULT_ADMIN_USERNAME", "admin"),
        os.environ.get("INTELLISTOCK_PASSWORD") or os.environ.get("DEFAULT_ADMIN_PASSWORD", ""),
    )
    return api, token


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--patch", default=None)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--revert", default=None)
    args = parser.parse_args(argv)
    if not args.patch and not args.revert:
        parser.error("need --patch or --revert")

    api, token = _auth()
    st, doc = _fetch(api, token, f"/strategies/{DOC_ID}")
    if st != 200 or not isinstance(doc, dict):
        print(f"GET /strategies/{DOC_ID} failed: {st}", file=sys.stderr)
        return 2
    doc = dict(doc.get("strategy") or doc)  # tolerate wrapper shapes
    specs = doc.get("strategies") or []
    idx = next((i for i, s in enumerate(specs)
                if str((s or {}).get("strategy")) == "graph_nexus_analysis"), None)
    if idx is None:
        print("graph_nexus_analysis spec not found", file=sys.stderr)
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
        doc["strategies"] = specs
        st, body = _http("PUT", f"{api}/strategies/{DOC_ID}",
                         headers={"Content-Type": "application/json",
                                  "Authorization": f"Bearer {token}"},
                         body=json.dumps(doc))
        print(f"REVERTED {len(backup['old_values'])} keys via API (status={st})")
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
    backup_path = _REPO / "scripts" / f"doc179_backup_patch_{ts}.json"
    with open(backup_path, "w") as fh:
        json.dump({"doc_id": DOC_ID, "applied_at": ts,
                   "old_values": old_values, "new_values": changes}, fh, indent=1)
    config.update(changes)
    specs[idx]["config"] = config
    doc["strategies"] = specs
    st, body = _http("PUT", f"{api}/strategies/{DOC_ID}",
                     headers={"Content-Type": "application/json",
                              "Authorization": f"Bearer {token}"},
                     body=json.dumps(doc))
    print(f"APPLIED via API (status={st}). Backup: {backup_path}")
    print(f"Revert with: python3 scripts/apply_doc179_config_patch_api.py --revert {backup_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
