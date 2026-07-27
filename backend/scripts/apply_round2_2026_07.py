"""One-shot 2026-07 ROUND-2 tune for Strategies doc '179' (live ``alpaca-main``).

Applies the round-2 credit-safety levers to the INNER config
``Strategies.get(179)['strategies'][0]['config']`` (the outer ``doc['config']``
is legacy and is NEVER touched):

  * **R2 — config levers** (spec 5.1-5.5, 5.9): concentration tighten
    (max_positions 10->8, new-buys 10->6), winner room
    (profitable_min_hold_release_peak_drop_pct 8->12), backfill-rotation
    winner-lock bypass tighten (max_held_pnl 10->3, min_hold 10->15), reserve
    unlock (backfill_budget_reserve_pct 0.2->0.1), macro floor raise
    (macro_risk_scale_min 0.8->0.9), ETF sleeve trim (etf_portfolio_pct
    0.20->0.05), and the Task-12 rotation positive-graph gate enable
    (rotation_positive_graph_gate_enabled: shipped default-OFF; doc-179 opts in).
    Every key VERIFIED read by code (graph_nexus_analysis.py /
    nexus_broker_utils.py) — dead-key writes are the exact disease this round
    treats, so nothing lands here without a live ``config.get`` behind it.
    Recon-verified: NONE of these keys is in ``live_config_hash`` OR
    ``history_scope_id`` (nexus_config_identity.py reads per-role
    llm_provider/llm_model/prompt-version + article-count keys only) -> no
    nexus rebuild / restamp on apply.

  * **R2 — dead-secret scrub** (spec 5.10): DELETE every strategies[0].config
    key matching ``.*_llm_azure_openai_(api_key|endpoint|api_version|model_id)$``.
    Config archaeology: the key family code actually reads is
    ``{role}_azure_openai_*`` (model_resolver.py:149-191,
    interactive_utils.py:8719-8721, graph_nexus_analysis.py:1088-1164 — prefix
    is the role name stripped of ``llm_model_id``, never ``{role}_llm_``);
    ``grep -rn "llm_azure_openai" backend/ web/ mobile/`` returns ZERO hits.
    These are legacy misnamed rows carrying live credentials for nothing.

SAFETY
  - Defaults to ``--dry-run`` (read-only). Writes ONLY with ``--apply``.
  - A FRESH full doc-179 snapshot (secrets redacted to first-2-chars + length)
    is dumped to ``docs/superpowers/specs/2026-07-03-strategy-179-pre-round2-snapshot.json``
    BEFORE any mutation logic runs, in BOTH dry-run and apply.
  - Never prints secret VALUES anywhere — deleted-key old values render as
    fingerprints (first-2-chars + length) only.
  - Every prod write AND the preview call are wrapped so a RethinkDB driver
    error (whose str() renders the query term tree, which embeds the literal
    config incl. api keys) is re-raised as a scrubbed ``RuntimeError``
    (``from None``) — the term tree can never reach stderr.
  - ``preview_change`` runs on BOTH the current and the proposed strategies:
    both identities must be unchanged vs baseline AND ``alpaca-main`` must show
    would_rebuild=False, else ``--apply`` ABORTS before writing — that would
    mean a scope-id ingredient moved and the recon above is stale.
  - ``--apply`` edits Strategies doc-179, which the changefeed picks up and
    RESTARTS the live broker. The plan output warns about this loudly.

Usage (run where prod RETHINKDB_HOST resolves, e.g. repo root with .env):

    python3 backend/scripts/apply_round2_2026_07.py            # dry-run (default)
    python3 backend/scripts/apply_round2_2026_07.py --apply    # WRITES (user-gated)
"""

import argparse
import copy
import json
import os
import re
import sys

# Path setup: allow both `python3 backend/scripts/apply_round2_2026_07.py`
# (repo root) and `python3 scripts/apply_round2_2026_07.py` (in-container).
_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)
_REPO_ROOT = os.path.dirname(_BACKEND_ROOT)

DB_NAME = "IntelliStock"
DOC_179_ID = 179
INSTANCE_ID = "alpaca-main"

SNAPSHOT_PATH = os.path.join(
    _REPO_ROOT,
    "docs", "superpowers", "specs",
    "2026-07-03-strategy-179-pre-round2-snapshot.json",
)

# --------------------------------------------------------------------------- #
# The round-2 tune (every key verified read by code — see module docstring)    #
# --------------------------------------------------------------------------- #

R2_CHANGES = {
    "max_positions": 8,                                    # was 10 (5.2)
    "allocation_max_new_stock_buys": 6,                    # was 10 (5.2)
    "profitable_min_hold_release_peak_drop_pct": 12,       # was 8 (5.3)
    "backfill_rotation_winner_lock_bypass_max_held_pnl_pct": 3,  # was 10 (5.1)
    "backfill_rotation_min_hold_days": 15,                 # was 10 (5.1)
    "backfill_budget_reserve_pct": 0.1,                    # was 0.2 (5.9)
    "macro_risk_scale_min": 0.9,                           # was 0.8 (5.5) — GNA:9051
    "etf_portfolio_pct": 0.05,                             # was 0.20 (5.4) — GNA:20298
        # (the budget-split log's "etf_pct=" prints this key — GNA:23420)
    "rotation_positive_graph_gate_enabled": True,          # Task 12 gate shipped
        # default-OFF; doc-179 opts in (graph_nexus_analysis reads it)
}

# Dead keys carrying live credentials (5.10). Code reads {role}_azure_openai_*;
# the {role}_llm_azure_openai_* family has ZERO references repo-wide.
DEAD_SECRET_KEY_RE = re.compile(
    r".*_llm_azure_openai_(api_key|endpoint|api_version|model_id)$"
)

_ABSENT = "(absent)"

# Key-name fragments that mark a value as secret for snapshot redaction. Any
# string value whose key matches is reduced to first-2-chars + length.
_SECRET_MARKERS = ("secret", "password", "passwd", "token", "api_key", "apikey")


# --------------------------------------------------------------------------- #
# Fingerprinting / redaction                                                   #
# --------------------------------------------------------------------------- #

def _fp(value):
    """first-2-chars + length stand-in — never the value itself."""
    if not isinstance(value, str):
        return repr(value)
    if not value:
        return "(empty)"
    return "%s…(len %d)" % (value[:2], len(value))


def _is_secret_key(key):
    k = str(key).lower()
    if any(m in k for m in _SECRET_MARKERS):
        return True
    return k == "key" or k.endswith("_key")  # alpaca_key, llm_api_key, ...


def redact_doc(obj, key_name=None):
    """Deep-copy ``obj`` with any secret-keyed string value redacted."""
    if isinstance(obj, dict):
        return {k: redact_doc(v, key_name=k) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact_doc(v, key_name=key_name) for v in obj]
    if key_name is not None and _is_secret_key(key_name):
        return _fp(obj) if isinstance(obj, str) else obj
    return obj


def dump_pre_round2_snapshot(doc179, path=SNAPSHOT_PATH):
    """Write the FRESH redacted full doc-179 snapshot BEFORE any mutation."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    redacted = redact_doc(doc179)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(redacted, f, indent=2, sort_keys=True, default=str)
    return path


# --------------------------------------------------------------------------- #
# Pure builder                                                                 #
# --------------------------------------------------------------------------- #

def build_round2_strategies(doc179):
    """Pure: ``doc179 -> (proposed_strategies, diff_rows, deleted_keys)``.

    Applies R2_CHANGES and deletes every DEAD_SECRET_KEY_RE-matching key on a
    deep copy of ``doc179['strategies'][0]['config']``. Does NOT mutate its
    input. Diff rows for deletions carry FINGERPRINTS only — the secret value
    never appears in any row. Raises ``ValueError`` on a wrong doc shape (loud
    discrepancy rather than a silent guess).
    """
    strategies = doc179.get("strategies")
    if not isinstance(strategies, list) or not strategies:
        raise ValueError("doc179 has no non-empty 'strategies' list")
    first = strategies[0]
    if not isinstance(first, dict) or not isinstance(first.get("config"), dict):
        raise ValueError("doc179 strategies[0].config is missing or not a dict")

    proposed = copy.deepcopy(strategies)
    cfg = proposed[0]["config"]
    diff_rows = []

    # --- R2: config levers (verified live keys; no identity hash) ---
    for key, new_val in R2_CHANGES.items():
        old_val = cfg.get(key, _ABSENT)
        cfg[key] = new_val
        diff_rows.append({"section": "R2", "key": key, "old": old_val, "new": new_val})

    # --- Dead-secret scrub: DELETE, never rewrite ---
    deleted_keys = sorted(k for k in cfg if DEAD_SECRET_KEY_RE.match(str(k)))
    for key in deleted_keys:
        old_val = cfg.pop(key)
        diff_rows.append({
            "section": "DEL", "key": key,
            "old": _fp(old_val),  # fingerprint ONLY — secret never leaves here
            "new": "(deleted)",
        })

    return proposed, diff_rows, deleted_keys


# --------------------------------------------------------------------------- #
# Plan / preview printing                                                      #
# --------------------------------------------------------------------------- #

def _print_plan(diff_rows, apply):
    print("=" * 72)
    print("DOC-179 2026-07 ROUND-2 PLAN  (%s)"
          % ("APPLY — WILL WRITE" if apply else "DRY-RUN — read-only"))
    print("=" * 72)
    print("\n[R2] Config levers (in NO identity hash — no rebuild/restamp):")
    for row in diff_rows:
        if row["section"] == "R2":
            print("    %-52s %r  ->  %r" % (row["key"], row["old"], row["new"]))
    print("\n[DEL] Dead-secret scrub (zero code refs; fingerprints only):")
    del_rows = [r for r in diff_rows if r["section"] == "DEL"]
    if not del_rows:
        print("    (no keys matched — nothing to scrub)")
    for row in del_rows:
        print("    %-52s %s  ->  %s" % (row["key"], row["old"], row["new"]))


def _print_preview(label, preview):
    print("\n%s preview_change verdict:" % label)
    print("    needs_prompt      : %r" % preview.get("needs_prompt"))
    print("    config_hash       : %s" % preview.get("config_hash"))
    print("    history_scope_id  : %s" % preview.get("history_scope_id"))
    instances = preview.get("instances") or []
    if not instances:
        print("    !! NO linked instances found for strategy_id=%r" % DOC_179_ID)
    for inst in instances:
        print("    instance %-16s would_rebuild=%r  snapshot_exists=%r"
              % (inst.get("base_instance_id"), inst.get("would_rebuild"),
                 inst.get("snapshot_exists")))


# --------------------------------------------------------------------------- #
# main                                                                         #
# --------------------------------------------------------------------------- #

def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true",
                        help="WRITE the changes. Without this flag the script is read-only (dry-run).")
    parser.add_argument("--host", default=None, help="RethinkDB host override (default: env RETHINKDB_HOST).")
    parser.add_argument("--port", default=None, help="RethinkDB port override (default: env RETHINKDB_PORT).")
    args = parser.parse_args(argv)

    # Load repo-root .env so RETHINKDB_HOST resolves the same way backend does.
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(_REPO_ROOT, ".env"))
    except Exception:
        pass

    # Some backend modules blow up on import without socketio; stub defensively.
    if "socketio" not in sys.modules:
        try:
            import socketio  # noqa: F401
        except Exception:
            import types
            sys.modules["socketio"] = types.ModuleType("socketio")

    from rethinkdb import RethinkDB
    import interactive_utils  # noqa: F401  (ensures backend import path is valid)
    import nexus_restamp

    r = RethinkDB()
    conn = interactive_utils.get_conn(host=args.host, port=args.port)
    try:
        doc179 = r.db(DB_NAME).table("Strategies").get(DOC_179_ID).run(conn)
        if doc179 is None:
            print("[r2-179] Strategies row %s not found." % DOC_179_ID, file=sys.stderr)
            return 2

        # --- FRESH pre-round2 snapshot BEFORE any mutation logic (both modes) ---
        snap_path = dump_pre_round2_snapshot(doc179)
        print("[r2-179] Fresh pre-round2 snapshot (secrets redacted) -> %s" % snap_path)

        proposed, diff_rows, deleted_keys = build_round2_strategies(doc179)
        _print_plan(diff_rows, apply=args.apply)
        print("\n[r2-179] %d dead-secret key(s) slated for deletion." % len(deleted_keys))

        # --- Identity preview (read-only) ---
        # Baseline previews the CURRENT (unchanged) strategies so a
        # pre-existing stale instance cannot masquerade as an identity flip
        # caused by THESE keys. Clean means:
        # both identities unchanged vs baseline AND alpaca-main itself would
        # not rebuild.
        try:
            baseline = nexus_restamp.preview_change(conn, r, DOC_179_ID, doc179["strategies"])
            preview = nexus_restamp.preview_change(conn, r, DOC_179_ID, proposed)
        except Exception as e:  # noqa: BLE001 — scrub: resolve may embed api keys
            raise RuntimeError(
                "[r2-179] preview_change FAILED (%s). Config redacted." % type(e).__name__
            ) from None
        _print_preview("BASELINE (current config)", baseline)
        _print_preview("PROPOSED", preview)
        _target_rows = {i.get("base_instance_id"): i for i in (preview.get("instances") or [])}
        _target = _target_rows.get(INSTANCE_ID) or {}
        identity_clean = (
            preview.get("config_hash") == baseline.get("config_hash")
            and preview.get("history_scope_id") == baseline.get("history_scope_id")
            and not _target.get("would_rebuild", True)
        )
        if identity_clean:
            print("    => VERIFIED: config_hash + history_scope_id unchanged vs baseline")
            print("       and %r would_rebuild=False — no rebuild, no restamp needed." % INSTANCE_ID)
        else:
            print("    !! UNEXPECTED: an identity flipped vs baseline (or %r would" % INSTANCE_ID)
            print("    !! rebuild). These keys are in no identity hash — recon is stale.")
            print("    !! DO NOT APPLY; investigate nexus_config_identity.py first.")

        print("\n" + "!" * 72)
        print("!! WARNING: writing Strategies doc-179 triggers the Strategies changefeed")
        print("!! and RESTARTS the live alpaca-main broker. Apply pre-market only.")
        print("!" * 72)

        if not args.apply:
            print("\n(dry-run) No writes performed. Re-run with --apply to write.")
            return 0

        if not identity_clean:
            print("\n[r2-179] ABORTED before write: preview reports an identity flip "
                  "vs baseline (or %r would rebuild) — see warning above." % INSTANCE_ID,
                  file=sys.stderr)
            return 3

        # --- WRITE PATH (only with --apply AND a clean identity preview) ---
        # The proposed strategies embed alpaca_key/secret + llm api keys; a driver
        # error renders the query term tree (with those literals), so scrub it.
        try:
            r.db(DB_NAME).table("Strategies").get(DOC_179_ID).update(
                {"strategies": r.literal(proposed)}
            ).run(conn)
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(
                "[r2-179] Strategies doc-%s update FAILED (%s). Payload redacted."
                % (DOC_179_ID, type(e).__name__)
            ) from None
        print("\n[r2-179] APPLIED. doc-179 inner config: %d lever(s) set, %d dead "
              "secret key(s) deleted." % (len(R2_CHANGES), len(deleted_keys)))

        # --- Verify: fresh preview should still show no rebuild needed ---
        try:
            verify = nexus_restamp.preview_change(conn, r, DOC_179_ID, proposed)
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(
                "[r2-179] post-apply preview_change FAILED (%s)." % type(e).__name__
            ) from None
        _print_preview("POST-APPLY", verify)
        verify_instances = {i.get("base_instance_id"): i for i in (verify.get("instances") or [])}
        target = verify_instances.get(INSTANCE_ID)
        if target is None:
            print("    !! WARNING: %r not found in post-apply preview — investigate." % INSTANCE_ID)
        elif target.get("would_rebuild"):
            print("    !! WARNING: %r would rebuild after apply — investigate before boot." % INSTANCE_ID)
        else:
            print("    => VERIFIED: %r needs no rebuild; boot reuses history." % INSTANCE_ID)

        print("[r2-179] The live broker will restart via the Strategies changefeed.")
        return 0
    finally:
        with_close = getattr(conn, "close", None)
        if callable(with_close):
            conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
