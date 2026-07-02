"""One-shot hygiene fix for Strategies doc '179' (the live ``alpaca-main`` instance).

Two prod problems this repairs:

  1. **Dead inner Alpaca key.** ``Strategies.get(179)['strategies'][0]['config']``
     carries an ``alpaca_key``/``alpaca_secret`` pair that now 401s (live AND
     paper). The WORKING live key lives in the ``BrokerageAccounts`` row
     ``08f683af-76f6-404d-872c-37baa45711ee`` ("Alpaca Live"). This copies that
     working key/secret INTO doc-179's inner config (in-DB copy — values are
     never printed; only sha1[:8] fingerprints + lengths are logged).

  2. **Stale halt.** Instances row ``alpaca-main`` still carries
     ``halt_reason``/``halted_at`` from a 06-23 halt. Commit 796f10d clears these
     on a healthy boot, but this fixes the live row immediately without waiting
     for a restart.

SAFETY
  - Defaults to ``--dry-run`` (read-only). Writes ONLY with ``--apply``.
  - Never prints key/secret VALUES — sha1[:8] fingerprints + lengths only.
  - No broker API calls. RethinkDB reads only in dry-run.
  - ``--apply`` edits Strategies doc-179, which the changefeed picks up and
    RESTARTS the live broker. The plan output warns about this loudly.

Usage (run where prod RETHINKDB_HOST resolves, e.g. repo root with .env):

    python3 backend/scripts/fix_doc179_hygiene.py            # dry-run (default)
    python3 backend/scripts/fix_doc179_hygiene.py --apply    # WRITES (user-gated)
"""

import argparse
import copy
import hashlib
import os
import sys

# Path setup: allow both `python3 backend/scripts/fix_doc179_hygiene.py` (repo
# root) and `python3 scripts/fix_doc179_hygiene.py` (inside the container).
_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

DB_NAME = "IntelliStock"
DOC_179_ID = 179
INSTANCE_ID = "alpaca-main"
BROKERAGE_ROW_ID = "08f683af-76f6-404d-872c-37baa45711ee"

# Redundant after Task 5 (commit 796f10d clears these on healthy boot), but this
# fixes the live row right now instead of waiting for a restart.
HALT_CLEAR = {"halt_reason": None, "halted_at": None}

# Candidate (key, secret) field-name pairs on the BrokerageAccounts row, most
# likely first. We VERIFY the actual field names at runtime rather than assume.
_CRED_FIELD_PAIRS = (
    ("key", "secret"),
    ("alpaca_key", "alpaca_secret"),
    ("api_key", "api_secret"),
    ("api_key_id", "api_secret_key"),
    ("apiKey", "apiSecret"),
)


def _fp(value):
    """sha1[:8] fingerprint of a string; safe stand-in for a secret in logs."""
    if not value:
        return "(empty)"
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:8]


def _length(value):
    return len(value) if isinstance(value, str) else 0


def extract_broker_creds(brokerage_row):
    """Return ``(key, secret, key_field, secret_field)`` from a BrokerageAccounts row.

    Tries known field-name pairs and returns the first pair that is present and
    non-empty. Raises ``ValueError`` if no usable credential pair is found — we
    never guess or fabricate a key.
    """
    if not isinstance(brokerage_row, dict):
        raise ValueError("brokerage_row must be a dict")
    for key_field, secret_field in _CRED_FIELD_PAIRS:
        k = brokerage_row.get(key_field)
        s = brokerage_row.get(secret_field)
        if isinstance(k, str) and k and isinstance(s, str) and s:
            return k, s, key_field, secret_field
    raise ValueError(
        "No usable (key, secret) pair on brokerage row; tried fields: "
        + ", ".join("%s/%s" % pair for pair in _CRED_FIELD_PAIRS)
    )


def build_updates(doc179, brokerage_row):
    """Pure builder: (doc179, brokerage_row) -> plan of rethink-ready updates.

    Returns a dict with:
      - ``strategies_179_update``: partial update for ``Strategies.get(179)`` —
        touches ONLY the top-level ``strategies`` field, with a deep copy whose
        element[0].config.alpaca_key/alpaca_secret are swapped to the working
        brokerage creds. (Rethink merges top-level keys but replaces nested
        arrays wholesale, so we must resend the whole array.)
      - ``instance_halt_clear``: the ``halt_reason``/``halted_at`` clear for the
        ``alpaca-main`` Instances row.
      - ``meta``: redacted fingerprints + which brokerage fields were used. No
        raw key/secret values ever appear here.

    Does NOT mutate its inputs.
    """
    new_key, new_secret, key_field, secret_field = extract_broker_creds(brokerage_row)

    strategies = doc179.get("strategies")
    if not isinstance(strategies, list) or not strategies:
        raise ValueError("doc179 has no non-empty 'strategies' list")
    first = strategies[0]
    if not isinstance(first, dict) or not isinstance(first.get("config"), dict):
        raise ValueError("doc179 strategies[0].config is missing or not a dict")

    old_key = first["config"].get("alpaca_key")
    old_secret = first["config"].get("alpaca_secret")

    new_strategies = copy.deepcopy(strategies)
    new_strategies[0]["config"]["alpaca_key"] = new_key
    new_strategies[0]["config"]["alpaca_secret"] = new_secret

    meta = {
        "broker_key_field": key_field,
        "broker_secret_field": secret_field,
        "old_key_fp": _fp(old_key if isinstance(old_key, str) else None),
        "new_key_fp": _fp(new_key),
        "old_secret_fp": _fp(old_secret if isinstance(old_secret, str) else None),
        "new_secret_fp": _fp(new_secret),
        "old_key_len": _length(old_key),
        "new_key_len": _length(new_key),
        "old_secret_len": _length(old_secret),
        "new_secret_len": _length(new_secret),
        "key_changed": (old_key != new_key) or (old_secret != new_secret),
    }

    return {
        "strategies_179_update": {"strategies": new_strategies},
        "instance_halt_clear": dict(HALT_CLEAR),
        "meta": meta,
    }


def apply_updates(rdb, conn, updates, instance_row):
    """Perform the two writes. SECRET-SAFE on driver failure.

    The Strategies update payload contains the literal alpaca_key/alpaca_secret
    values. If the RethinkDB driver raises (ReqlRuntimeError/ReqlOpFailedError,
    ...), its stringified error renders the query TERM TREE — including those
    literal values — so letting it propagate would print the working secret in
    the traceback. Both writes are therefore wrapped and re-raised as a
    scrubbed RuntimeError (original exception CLASS NAME + fingerprint-only
    context), with ``from None`` to sever the exception chain so the driver's
    term-tree message can never reach stderr.
    """
    meta = updates["meta"]
    try:
        rdb.db(DB_NAME).table("Strategies").get(DOC_179_ID).update(
            updates["strategies_179_update"]
        ).run(conn)
    except Exception as e:  # noqa: BLE001 — deliberately broad: scrub everything
        raise RuntimeError(
            "[fix-179] Strategies doc-%s update FAILED (%s). Payload redacted; "
            "intended new key fp=%s, new secret fp=%s."
            % (DOC_179_ID, type(e).__name__, meta["new_key_fp"], meta["new_secret_fp"])
        ) from None
    if instance_row is not None:
        try:
            rdb.db(DB_NAME).table("Instances").get(INSTANCE_ID).update(
                updates["instance_halt_clear"]
            ).run(conn)
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(
                "[fix-179] Instances %r halt clear FAILED (%s). NOTE: the "
                "Strategies doc-%s key update already succeeded."
                % (INSTANCE_ID, type(e).__name__, DOC_179_ID)
            ) from None


def _print_plan(doc179, instance_row, updates, apply):
    meta = updates["meta"]
    print("=" * 72)
    print("DOC-179 HYGIENE PLAN  (%s)" % ("APPLY — WILL WRITE" if apply else "DRY-RUN — read-only"))
    print("=" * 72)

    print("\n[1] Replace dead inner Alpaca key in Strategies.get(%s)" % DOC_179_ID)
    print("    strategies[0].config.alpaca_key / .alpaca_secret")
    print("    brokerage source row : %s" % BROKERAGE_ROW_ID)
    print("    brokerage fields used: %r / %r" % (meta["broker_key_field"], meta["broker_secret_field"]))
    print("    alpaca_key    : %s (len %d)  ->  %s (len %d)"
          % (meta["old_key_fp"], meta["old_key_len"], meta["new_key_fp"], meta["new_key_len"]))
    print("    alpaca_secret : %s (len %d)  ->  %s (len %d)"
          % (meta["old_secret_fp"], meta["old_secret_len"], meta["new_secret_fp"], meta["new_secret_len"]))
    if meta["key_changed"]:
        print("    => MISMATCH CONFIRMED: doc-179 key fingerprint != brokerage key fingerprint.")
    else:
        print("    => No change: doc-179 already carries the brokerage key.")

    print("\n[2] Clear stale halt on Instances.get(%r)" % INSTANCE_ID)
    if instance_row is None:
        print("    !! instance row not found")
    else:
        print("    halt_reason : %r  ->  None" % instance_row.get("halt_reason"))
        print("    halted_at   : %r  ->  None" % instance_row.get("halted_at"))

    print("\n" + "!" * 72)
    print("!! WARNING: writing Strategies doc-179 triggers the Strategies changefeed")
    print("!! and RESTARTS the live alpaca-main broker. Apply pre-market only.")
    print("!" * 72)
    if not apply:
        print("\n(dry-run) No writes performed. Re-run with --apply to write.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true",
                        help="WRITE the changes. Without this flag the script is read-only (dry-run).")
    parser.add_argument("--host", default=None, help="RethinkDB host override (default: env RETHINKDB_HOST).")
    parser.add_argument("--port", default=None, help="RethinkDB port override (default: env RETHINKDB_PORT).")
    args = parser.parse_args(argv)

    # Load repo-root .env so RETHINKDB_HOST resolves the same way backend does.
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(os.path.dirname(_BACKEND_ROOT), ".env"))
    except Exception:
        pass

    # Some backend modules blow up on import without socketio; interactive_utils
    # itself imports cleanly, but stub defensively so the fetch path never dies.
    if "socketio" not in sys.modules:
        try:
            import socketio  # noqa: F401
        except Exception:
            import types
            sys.modules["socketio"] = types.ModuleType("socketio")

    from rethinkdb import RethinkDB
    import interactive_utils  # noqa: F401  (ensures backend import path is valid)

    r = RethinkDB()
    conn = interactive_utils.get_conn(host=args.host, port=args.port)
    try:
        doc179 = r.db(DB_NAME).table("Strategies").get(DOC_179_ID).run(conn)
        if doc179 is None:
            print("[fix-179] Strategies row %s not found." % DOC_179_ID, file=sys.stderr)
            return 2
        brokerage_row = r.db(DB_NAME).table("BrokerageAccounts").get(BROKERAGE_ROW_ID).run(conn)
        if brokerage_row is None:
            print("[fix-179] BrokerageAccounts row %s not found." % BROKERAGE_ROW_ID, file=sys.stderr)
            return 2
        instance_row = r.db(DB_NAME).table("Instances").get(INSTANCE_ID).run(conn)

        # Report the brokerage row's field NAMES (not values) so we can verify
        # which fields actually hold the creds.
        print("[fix-179] BrokerageAccounts %s field names: %s"
              % (BROKERAGE_ROW_ID, sorted(brokerage_row.keys())))

        updates = build_updates(doc179, brokerage_row)
        _print_plan(doc179, instance_row, updates, apply=args.apply)

        if not args.apply:
            return 0

        # --- WRITE PATH (only with --apply) ---
        # apply_updates scrubs driver exceptions so the raw key/secret in the
        # update payload can never leak into a traceback.
        apply_updates(r, conn, updates, instance_row)
        print("\n[fix-179] APPLIED. doc-179 key replaced; halt cleared on %r." % INSTANCE_ID)
        print("[fix-179] The live broker will restart via the Strategies changefeed.")
        return 0
    finally:
        with_close = getattr(conn, "close", None)
        if callable(with_close):
            conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
