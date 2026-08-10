#!/usr/bin/env python3
"""Set (or clear) config keys on a strategy document, with a printed diff.

Every config change so far has been made by hand, which is how
`residual_sleeve_bear_alloc_pct` was believed to be 0.35 for a whole session while
every run logged 0.70 (fix-generalize.md §4.3). This prints exactly what changes,
refuses to guess types, and defaults to a DRY RUN.

    # look
    python3 scripts/set_doc_config.py 193 --show residual_sleeve_bear regime_rally

    # dry-run a change
    python3 scripts/set_doc_config.py 193 \
        --set residual_sleeve_bear_block_at_fresh_low_bars=2 \
        --set regime_rally_onset_enabled=true

    # apply it
    python3 scripts/set_doc_config.py 193 --set ... --apply

Values are parsed as JSON, so `true`, `2`, `0.35`, `"text"`, `null` all work;
anything unparseable is kept as a string. `--unset KEY` removes the key entirely
(which is NOT the same as setting it false — a missing key takes the code default).

WARNING: a config change is picked up by the NEXT run. It does not touch a run
already in flight, but do not change the document between the two arms of a pair.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "scripts"))
from pull_backtest_logs import _load_dotenv, _login, _http  # noqa: E402

_MISSING = object()


def _api_and_token():
    _load_dotenv(_REPO)
    api = (os.environ.get("INTELLISTOCK_API_URL") or os.environ.get("API_URL")
           or f"http://localhost:{os.environ.get('API_PORT', '8000')}").rstrip("/")
    token = os.environ.get("INTELLISTOCK_API_TOKEN") or _login(
        api,
        os.environ.get("INTELLISTOCK_USERNAME") or os.environ.get("DEFAULT_ADMIN_USERNAME", "admin"),
        os.environ.get("INTELLISTOCK_PASSWORD") or os.environ.get("DEFAULT_ADMIN_PASSWORD", ""),
    )
    return api, token


def _parse(raw: str):
    try:
        return json.loads(raw)
    except ValueError:
        return raw


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("strategy_id", type=int)
    p.add_argument("--set", action="append", default=[], metavar="KEY=VALUE")
    p.add_argument("--unset", action="append", default=[], metavar="KEY")
    p.add_argument("--show", nargs="*", metavar="SUBSTR",
                   help="print matching keys and exit")
    p.add_argument("--lane", type=int, default=0,
                   help="index into strategies[] (default 0)")
    p.add_argument("--apply", action="store_true")
    a = p.parse_args(argv)

    api, token = _api_and_token()
    auth = {"Authorization": f"Bearer {token}"}
    status, doc = _http("GET", f"{api}/strategies/{a.strategy_id}", headers=auth)
    if status != 200 or not isinstance(doc, dict):
        print(f"GET /strategies/{a.strategy_id} -> {status}: {str(doc)[:300]}")
        return 1
    lanes = doc.get("strategies") or []
    if a.lane >= len(lanes):
        print(f"document has {len(lanes)} lane(s); --lane {a.lane} is out of range")
        return 1
    cfg = lanes[a.lane].get("config") or {}

    if a.show is not None:
        pats = [s.lower() for s in a.show]
        for k in sorted(cfg):
            if not pats or any(s in k.lower() for s in pats):
                print(f"{k} = {cfg[k]!r}")
        print(f"\n({len(cfg)} keys total in strategies[{a.lane}].config of doc {a.strategy_id})")
        return 0

    updates = {}
    for item in a.set:
        if "=" not in item:
            print(f"--set expects KEY=VALUE, got {item!r}")
            return 1
        k, v = item.split("=", 1)
        updates[k.strip()] = _parse(v.strip())

    changed = []
    new_cfg = dict(cfg)
    for k, v in updates.items():
        before = cfg.get(k, _MISSING)
        if before is not _MISSING and before == v:
            print(f"  unchanged  {k} = {v!r}")
            continue
        new_cfg[k] = v
        changed.append((k, before, v))
    for k in a.unset:
        k = k.strip()
        if k in new_cfg:
            changed.append((k, cfg[k], _MISSING))
            del new_cfg[k]
        else:
            print(f"  absent     {k} (nothing to unset)")

    if not changed:
        print("nothing to do.")
        return 0

    print(f"doc {a.strategy_id} strategies[{a.lane}].config — "
          f"{len(changed)} key(s) {'CHANGING' if a.apply else 'would change'}:")
    for k, before, after in changed:
        b = "<absent>" if before is _MISSING else repr(before)
        aft = "<removed>" if after is _MISSING else repr(after)
        print(f"  {k}\n      {b}  ->  {aft}")

    if not a.apply:
        print("\ndry-run: nothing written. Re-run with --apply.")
        return 0

    new_lanes = copy.deepcopy(lanes)
    new_lanes[a.lane]["config"] = new_cfg
    status, body = _http(
        "PUT", f"{api}/strategies/{a.strategy_id}",
        headers={**auth, "Content-Type": "application/json"},
        body=json.dumps({"name": doc.get("name"), "strategies": new_lanes,
                         "preserve_history": True}),
    )
    print(f"\nPUT -> {status}")
    if status not in (200, 201):
        print(str(body)[:600])
        return 1

    # Read it back. A config we did not verify is a config we do not know.
    status, doc2 = _http("GET", f"{api}/strategies/{a.strategy_id}", headers=auth)
    cfg2 = (doc2.get("strategies") or [{}])[a.lane].get("config") or {}
    ok = True
    for k, _before, after in changed:
        got = cfg2.get(k, _MISSING)
        if after is _MISSING:
            good = got is _MISSING
        else:
            good = got == after
        ok = ok and good
        print(f"  {'ok  ' if good else 'FAIL'} {k} = "
              f"{'<absent>' if got is _MISSING else repr(got)}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
