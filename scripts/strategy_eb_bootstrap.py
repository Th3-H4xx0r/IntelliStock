#!/usr/bin/env python3
"""Create the Strategy EB document and instance through the API, and verify.

    python3 scripts/strategy_eb_bootstrap.py show
    python3 scripts/strategy_eb_bootstrap.py create [--dry-run]
    python3 scripts/strategy_eb_bootstrap.py verify [doc_id]

Following `_sx_doc198_patch.py`: write, RE-FETCH, and verify every key round-
trips. The API normalises every lane on save, and a silent coercion there is
how a Strategy XS edit reverted (schema.strategy CapitalCase vs the lower-case
id) with nobody noticing.

`create` is idempotent: an existing document of this name is reused, an
existing instance is relinked rather than recreated, and both paths end in the
same verification.

The document carries ONE lane. `broker_max_single_position_pct` becomes a
process-wide env var inside the backtest container, so a second enabled lane
here would inherit a 95% cap it was never measured under.

The $6,000 of spec section 10 is not set here: initial cash is a parameter of
the backtest POST, not a property of the instance.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from strategy_eb import DEFAULTS, strategy_eb_universe  # noqa: E402

DOC_NAME = "Strategy EB"
INSTANCE_ID = "strategy-eb"
#: Daily. The strategy decides once per NY session; a finer granularity only
#: multiplies evaluations that the once-per-session guard then discards.
GRANULARITY = "86400"

LANE = {
    "strategy": "strategy_eb",
    "weight": 1.0,
    "execution_position": 10,
    "decision_phase": "pre",
    "execution_scope": "run_once",
    "conditions": {},
    # Enabled at creation so a backtest of this document actually runs it. The
    # rollback is setting this false; the gate in section 11 of the spec
    # decides whether it stays true for LIVE.
    "config": {**DEFAULTS, "strategy_eb_enabled": True},
}

#: DERIVED, never written out by hand. A hardcoded watchlist is exactly the
#: drift the strategy's own universe function exists to prevent: configure a
#: remainder book or a risk-off leg and a literal list silently leaves those
#: symbols without bars, at which point the strategy emits nothing and says
#: nothing about why.
STOCKS = strategy_eb_universe(LANE["config"])


def _call():
    from _api import call  # deferred: importing it reads .env and can log in

    return call


def _ok(status):
    return status in (200, 201)


# ------------------------------------------------------------------ payloads
def doc_payload():
    return {"name": DOC_NAME, "strategies": [LANE]}


def instance_payload(doc_id):
    return {"id": INSTANCE_ID, "name": DOC_NAME, "strategy_id": int(doc_id),
            "granularity": GRANULARITY, "run_command": False, "stocks": STOCKS}


def link_payload(doc_id):
    return {"strategy_id": int(doc_id)}


def documents(listing):
    """The document array out of either listing shape: GET /strategies returns
    {"strategies": [...]}, older deployments the bare list."""
    if isinstance(listing, dict):
        listing = listing.get("strategies")
    return [doc for doc in (listing or []) if isinstance(doc, dict)]


def config_drift(saved):
    """{key: {"sent": ..., "saved": ...}} for every config key that did not
    come back as it was sent. A dropped key reads as saved=None."""
    saved = saved or {}
    return {key: {"sent": value, "saved": saved.get(key)}
            for key, value in LANE["config"].items()
            if key not in saved or saved[key] != value}


# ------------------------------------------------------------------ actions
def _find_by_name(call):
    status, listing = call("GET", "/strategies")
    if not _ok(status):
        raise SystemExit("GET /strategies -> %s: %s" % (status, listing))
    return [doc for doc in documents(listing) if doc.get("name") == DOC_NAME]


def verify(doc_id, *, call=None):
    call = call or _call()
    status, doc = call("GET", "/strategies/%s" % doc_id)
    if not _ok(status):
        raise SystemExit("GET /strategies/%s -> %s: %s" % (doc_id, status, doc))
    lanes = (doc or {}).get("strategies") or []
    if len(lanes) != 1:
        raise SystemExit("expected exactly one lane, saw %d: a second lane "
                         "would inherit this document's 95%% single-position "
                         "cap" % len(lanes))
    lane = lanes[0]
    if str(lane.get("strategy")) != LANE["strategy"]:
        raise SystemExit("lane id came back as %r; the broker resolves the "
                         "class from this string" % (lane.get("strategy"),))
    saved = lane.get("config") or {}
    drift = config_drift(saved)
    if drift:
        raise SystemExit("NOT SAVED as requested:\n"
                         + json.dumps(drift, indent=2, default=str))

    status, inst = call("GET", "/instances/%s" % INSTANCE_ID)
    if not _ok(status):
        raise SystemExit("GET /instances/%s -> %s: %s"
                         % (INSTANCE_ID, status, inst))
    linked = (inst or {}).get("strategy_id")
    if str(linked) != str(doc_id):
        raise SystemExit("instance %s links strategy_id=%s, expected %s"
                         % (INSTANCE_ID, linked, doc_id))

    # The watchlist must cover the SAVED config's universe, not the one this
    # script sent: if the API coerced a book key the strategy will read the
    # coerced value, and a leg with no bars is a silent no-op.
    wanted = strategy_eb_universe(saved)
    have = {str(symbol).strip().upper() for symbol in (inst.get("stocks") or [])}
    absent = [symbol for symbol in wanted if symbol not in have]
    if absent:
        raise SystemExit(
            "instance %s has no bars for %s: the strategy reads %s and a leg "
            "without bars is silently skipped. PATCH /instances/%s with "
            "stocks=%s." % (INSTANCE_ID, ", ".join(absent), ", ".join(wanted),
                            INSTANCE_ID, wanted))
    print("verified: %d config keys round-tripped, instance %s linked to "
          "document %s, watchlist covers %s"
          % (len(saved), INSTANCE_ID, doc_id, ", ".join(wanted)))


def create(*, call=None):
    call = call or _call()
    existing = _find_by_name(call)
    if len(existing) > 1:
        raise SystemExit("%d documents already named %r (ids %s); delete the "
                         "duplicates before bootstrapping"
                         % (len(existing), DOC_NAME,
                            ", ".join(str(doc.get("id")) for doc in existing)))
    if existing:
        doc_id = existing[0].get("id")
        print("document %s — %s already exists, reusing it" % (doc_id, DOC_NAME))
    else:
        status, doc = call("POST", "/strategies", doc_payload())
        if not _ok(status):
            raise SystemExit("POST /strategies -> %s: %s" % (status, doc))
        doc_id = doc.get("id") if isinstance(doc, dict) else None
        if doc_id is None:
            # Some deployments answer {"ok": ...} only; find it by name.
            matches = _find_by_name(call)
            if not matches:
                raise SystemExit("created but not findable by name: %s" % (doc,))
            doc_id = matches[-1].get("id")
        print("created document %s — %s" % (doc_id, DOC_NAME))

    status, inst = call("GET", "/instances/%s" % INSTANCE_ID)
    if _ok(status) and isinstance(inst, dict):
        already_linked = str(inst.get("strategy_id")) == str(doc_id)
        print("instance %s already exists%s"
              % (INSTANCE_ID, "" if already_linked
                 else " (linked to %s, relinking)" % inst.get("strategy_id")))
    else:
        status, inst = call("POST", "/instances", instance_payload(doc_id))
        if not _ok(status):
            raise SystemExit("POST /instances -> %s: %s" % (status, inst))
        already_linked = False
        print("created instance %s" % INSTANCE_ID)

    if not already_linked:
        status, linked = call("POST", "/instances/%s/link-strategy" % INSTANCE_ID,
                              link_payload(doc_id))
        if not _ok(status):
            raise SystemExit("link-strategy -> %s: %s" % (status, linked))
        print("instance %s linked to document %s" % (INSTANCE_ID, doc_id))

    verify(doc_id, call=call)
    return doc_id


def show(*, call=None):
    call = call or _call()
    matches = _find_by_name(call)
    if not matches:
        print("no document named %r" % DOC_NAME)
        return
    for doc in matches:
        print("doc %s — %s" % (doc.get("id"), doc.get("name")))
        for lane in doc.get("strategies") or []:
            cfg = lane.get("config") or {}
            print("  %s enabled=%s core=%s dial=%s weekdays=%s"
                  % (lane.get("strategy"), cfg.get("strategy_eb_enabled"),
                     cfg.get("core_symbol"), cfg.get("remainder_bil_fraction"),
                     cfg.get("rebalance_weekdays")))


def dry_run():
    print("POST /strategies")
    print(json.dumps(doc_payload(), indent=2, default=str))
    print("\nPOST /instances")
    print(json.dumps(instance_payload(0), indent=2, default=str))
    print("\nPOST /instances/%s/link-strategy" % INSTANCE_ID)
    print(json.dumps(link_payload(0), indent=2, default=str))
    print("\n(dry run: nothing was sent; strategy_id is filled in at create)")


def main(argv=None, *, call=None):
    parser = argparse.ArgumentParser(
        description="Bootstrap the Strategy EB document and instance.")
    parser.add_argument("action", nargs="?", default="show",
                        choices=("show", "create", "verify"))
    parser.add_argument("doc_id", nargs="?",
                        help="verify: the document id (default: find by name)")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the payloads and exit without sending")
    args = parser.parse_args(argv)

    if args.dry_run:
        dry_run()
        return 0
    call = call or _call()
    if args.action == "create":
        create(call=call)
    elif args.action == "verify":
        doc_id = args.doc_id
        if doc_id is None:
            matches = _find_by_name(call)
            if len(matches) != 1:
                raise SystemExit("found %d documents named %r; pass the id"
                                 % (len(matches), DOC_NAME))
            doc_id = matches[0].get("id")
        verify(doc_id, call=call)
    else:
        show(call=call)
    return 0


if __name__ == "__main__":
    sys.exit(main())
