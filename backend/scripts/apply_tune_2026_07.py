"""One-shot 2026-07 aggressive tune for Strategies doc '179' (live ``alpaca-main``).

Applies the approved aggressive tune to the INNER config
``Strategies.get(179)['strategies'][0]['config']`` (the outer ``doc['config']``
is legacy and is NEVER touched):

  * **B1 — trading keys.** Drawdown-halt 12->8, conviction-override off, budget
    unlocks (reserved 0.3->0.1, cash floor 0.05->0.02), signal-width unlocks
    (new-buys 6->10, scoring slots 20->40, max_positions 8->10), rotation
    tighten, and all Benzinga feeds off (sub lapsed). Recon-verified: NONE of
    these keys is in ``live_config_hash`` OR ``history_scope_id`` -> they do NOT
    trigger a nexus rebuild / restamp.

  * **B2 — LLM role migration.** The ``macro_article`` and
    ``lookback_macro_article`` roles move off ``codex-cli`` onto the doc's
    default-role OpenRouter Nemotron values (``llm_provider`` / ``llm_model`` /
    ``llm_model_id`` copied verbatim from the default role). ``macro_article_*``
    IS in ``history_scope_id`` -> this flips the scope id, so after ``--apply``
    the script re-stamps the ``alpaca-main`` saved state via
    ``nexus_restamp.restamp_instance`` so the next boot reuses history instead of
    running a destructive lookback + cleanup.

SAFETY
  - Defaults to ``--dry-run`` (read-only). Writes ONLY with ``--apply``.
  - A FRESH full doc-179 snapshot (secrets redacted to first-2-chars + length)
    is dumped to ``docs/superpowers/specs/2026-07-02-strategy-179-pre-tune-snapshot.json``
    BEFORE any mutation logic runs, in BOTH dry-run and apply.
  - Never prints secret VALUES anywhere — fingerprints / redactions only.
  - Every prod write AND the resolve/restamp calls are wrapped so a RethinkDB
    driver error (whose str() renders the query term tree, which embeds the
    literal config incl. api keys) is re-raised as a scrubbed ``RuntimeError``
    (``from None``) — the term tree can never reach stderr.
  - ``--apply`` edits Strategies doc-179, which the changefeed picks up and
    RESTARTS the live broker. The plan output warns about this loudly.

Usage (run where prod RETHINKDB_HOST resolves, e.g. repo root with .env):

    python3 backend/scripts/apply_tune_2026_07.py            # dry-run (default)
    python3 backend/scripts/apply_tune_2026_07.py --apply    # WRITES (user-gated)
"""

import argparse
import copy
import json
import os
import sys

# Path setup: allow both `python3 backend/scripts/apply_tune_2026_07.py` (repo
# root) and `python3 scripts/apply_tune_2026_07.py` (inside the container).
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
    "2026-07-02-strategy-179-pre-tune-snapshot.json",
)

# --------------------------------------------------------------------------- #
# The tune (verbatim from the task brief)                                      #
# --------------------------------------------------------------------------- #

B1_CHANGES = {
    "portfolio_drawdown_halt_pct": 8,            # was 12 (user circuit breaker)
    "profitable_min_hold_conviction_override_enabled": False,  # was True (confirmed drag)
    "new_entry_reserved_budget_pct": 0.1,        # was 0.3 (idle-cash fix)
    "cash_reserve_floor_pct": 0.02,              # was 0.05
    "allocation_max_new_stock_buys": 10,         # was 6 (matches +266% run)
    "max_propagated_scoring_slots": 40,          # was 20 (signal width)
    "max_positions": 10,                         # was 8 — replaces the spec's
        # "priority buys tap reserve": June's 28 blocked buys were
        # queue_status=full_priority_blocked (POSITION SLOTS full), not cash;
        # 2 more slots is the change that actually unblocks them
    "rotation_break_glass_delta": 2.5,           # was 1
    "rotation_break_glass_raw_score": 3.5,       # was 1.5
    "rotation_profitable_min_incoming_raw_score": 2.0,  # was 1.5
    # Benzinga sub lapsed — silence until renewed:
    "benzinga_company_actions_enabled": False, "benzinga_earnings_calendar_enabled": False,
    "benzinga_gov_trades_enabled": False, "benzinga_insider_trades_enabled": False,
    "benzinga_insights_enabled": False, "benzinga_ipo_enabled": False,
    "benzinga_ma_enabled": False, "benzinga_ratings_enabled": False,
    "benzinga_splits_enabled": False,
}

# B2: migrate these roles onto the default role's OpenRouter Nemotron identity.
B2_ROLE_PREFIXES = ("macro_article_", "lookback_macro_article_")
# The default-role fields copied verbatim into each B2 role (bare = default role).
B2_SOURCE_SUFFIXES = ("llm_provider", "llm_model", "llm_model_id")

_ABSENT = "(absent)"

# Key-name fragments that mark a value as secret for snapshot redaction. Any
# string value whose key matches is reduced to first-2-chars + length.
_SECRET_MARKERS = ("secret", "password", "passwd", "token", "api_key", "apikey")


# --------------------------------------------------------------------------- #
# Pure builder                                                                 #
# --------------------------------------------------------------------------- #

def build_tuned_strategies(doc179):
    """Pure: ``doc179 -> (proposed_strategies, diff_rows)``.

    Applies B1 + B2 to a deep copy of ``doc179['strategies'][0]['config']`` and
    returns the new ``strategies`` list plus a list of ``{section, key, old,
    new}`` diff rows. Does NOT mutate its input. Raises ``ValueError`` if the
    doc shape is wrong or the default role is missing a B2 source field (a loud
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

    # --- B1: trading keys (no identity hash) ---
    for key, new_val in B1_CHANGES.items():
        old_val = cfg.get(key, _ABSENT)
        cfg[key] = new_val
        diff_rows.append({"section": "B1", "key": key, "old": old_val, "new": new_val})

    # --- B2: copy default-role Nemotron/OpenRouter identity into macro roles ---
    source = {}
    missing = []
    for suffix in B2_SOURCE_SUFFIXES:
        val = cfg.get(suffix, _ABSENT)
        if val == _ABSENT or (isinstance(val, str) and not val.strip()):
            missing.append(suffix)
        source[suffix] = val
    if missing:
        raise ValueError(
            "B2 migration cannot proceed: doc-179 default role is missing/empty "
            "required field(s): %s. (Expected the OpenRouter Nemotron default "
            "role to carry llm_provider/llm_model/llm_model_id.)" % ", ".join(missing)
        )

    for prefix in B2_ROLE_PREFIXES:
        for suffix in B2_SOURCE_SUFFIXES:
            key = prefix + suffix
            old_val = cfg.get(key, _ABSENT)
            new_val = source[suffix]
            cfg[key] = new_val
            diff_rows.append({"section": "B2", "key": key, "old": old_val, "new": new_val})

    return proposed, diff_rows


# --------------------------------------------------------------------------- #
# Snapshot redaction                                                           #
# --------------------------------------------------------------------------- #

def _redact_value(value):
    """first-2-chars + length stand-in for a secret string value."""
    if not isinstance(value, str):
        return value
    if not value:
        return "(empty)"
    return "%s…(len %d)" % (value[:2], len(value))


def _is_secret_key(key):
    k = str(key).lower()
    if any(m in k for m in _SECRET_MARKERS):
        return True
    return k == "key" or k.endswith("_key")  # alpaca_key, llm_api_key, ...


def redact_doc(obj, key_name=None):
    """Deep-copy ``obj`` with any secret-keyed string value redacted.

    Values are only redacted when their KEY name looks secret (so trading keys
    like ``llm_model_id`` are preserved for the revert record, but ``alpaca_key``
    / ``*_api_key`` / ``*_secret`` become ``first2…(len N)``).
    """
    if isinstance(obj, dict):
        return {k: redact_doc(v, key_name=k) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact_doc(v, key_name=key_name) for v in obj]
    if key_name is not None and _is_secret_key(key_name):
        return _redact_value(obj)
    return obj


def dump_pre_tune_snapshot(doc179, path=SNAPSHOT_PATH):
    """Write the FRESH redacted full doc-179 snapshot BEFORE any mutation."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    redacted = redact_doc(doc179)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(redacted, f, indent=2, sort_keys=True, default=str)
    return path


# --------------------------------------------------------------------------- #
# Plan / preview printing                                                      #
# --------------------------------------------------------------------------- #

def _print_plan(diff_rows, apply):
    print("=" * 72)
    print("DOC-179 2026-07 TUNE PLAN  (%s)"
          % ("APPLY — WILL WRITE" if apply else "DRY-RUN — read-only"))
    print("=" * 72)
    print("\n[B1] Trading keys (in NO identity hash — no rebuild/restamp):")
    for row in diff_rows:
        if row["section"] == "B1":
            print("    %-48s %r  ->  %r" % (row["key"], row["old"], row["new"]))
    print("\n[B2] Macro LLM role migration (in history_scope_id — restamp on apply):")
    for row in diff_rows:
        if row["section"] == "B2":
            print("    %-48s %r  ->  %r" % (row["key"], row["old"], row["new"]))


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
            print("[tune-179] Strategies row %s not found." % DOC_179_ID, file=sys.stderr)
            return 2

        # --- FRESH pre-tune snapshot BEFORE any mutation logic (both modes) ---
        snap_path = dump_pre_tune_snapshot(doc179)
        print("[tune-179] Fresh pre-tune snapshot (secrets redacted) -> %s" % snap_path)

        proposed, diff_rows = build_tuned_strategies(doc179)
        _print_plan(diff_rows, apply=args.apply)

        # --- Identity preview (read-only) ---
        try:
            preview = nexus_restamp.preview_change(conn, r, DOC_179_ID, proposed)
        except Exception as e:  # noqa: BLE001 — scrub: resolve may embed api keys
            raise RuntimeError(
                "[tune-179] preview_change FAILED (%s). Config redacted." % type(e).__name__
            ) from None
        _print_preview("PROPOSED", preview)
        if preview.get("needs_prompt"):
            print("    => Expected: B2 flips history_scope_id, so a restamp IS required on apply.")
        else:
            print("    => NOTE: preview says NO preservation prompt needed (unexpected for a B2 scope flip).")

        print("\n" + "!" * 72)
        print("!! WARNING: writing Strategies doc-179 triggers the Strategies changefeed")
        print("!! and RESTARTS the live alpaca-main broker. Apply pre-market only.")
        print("!" * 72)

        if not args.apply:
            print("\n(dry-run) No writes performed. Re-run with --apply to write.")
            return 0

        # --- WRITE PATH (only with --apply) ---
        # The proposed strategies embed alpaca_key/secret + llm api keys; a driver
        # error renders the query term tree (with those literals), so scrub it.
        try:
            r.db(DB_NAME).table("Strategies").get(DOC_179_ID).update(
                {"strategies": proposed}
            ).run(conn)
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(
                "[tune-179] Strategies doc-%s update FAILED (%s). Payload redacted."
                % (DOC_179_ID, type(e).__name__)
            ) from None
        print("\n[tune-179] APPLIED. doc-179 inner config tuned (B1 + B2).")

        # --- B2 flips history_scope_id -> restamp saved state (scrubbed) ---
        try:
            resolved_cfg = nexus_restamp.resolve_for_identity(
                conn, nexus_restamp._nexus_config_from_strategies(proposed)
            )
            report = nexus_restamp.restamp_instance(conn, r, INSTANCE_ID, resolved_cfg)
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(
                "[tune-179] restamp FAILED (%s). NOTE: the doc-179 tune write "
                "already succeeded; boot may run a destructive rebuild until "
                "restamp is re-run." % type(e).__name__
            ) from None
        print("[tune-179] RESTAMP report for %r:" % INSTANCE_ID)
        print("    snapshots_restamped : %r" % report.get("snapshots_restamped"))
        print("    markers_restamped   : %r" % report.get("markers_restamped"))
        print("    config_hash         : %s" % report.get("config_hash"))
        print("    history_scope_id    : %s" % report.get("history_scope_id"))

        # --- Verify: fresh preview should now show no rebuild needed ---
        try:
            verify = nexus_restamp.preview_change(conn, r, DOC_179_ID, proposed)
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(
                "[tune-179] post-restamp preview_change FAILED (%s)." % type(e).__name__
            ) from None
        _print_preview("POST-RESTAMP", verify)
        # Verify keys on the RESTAMPED instance specifically. Other linked
        # instances are outside this tune's alpaca-main scope.
        verify_instances = {i.get("base_instance_id"): i for i in (verify.get("instances") or [])}
        target = verify_instances.get(INSTANCE_ID)
        others_rebuilding = [
            iid for iid, i in verify_instances.items()
            if iid != INSTANCE_ID and i.get("would_rebuild")
        ]
        if target is None:
            print("    !! WARNING: %r not found in post-restamp preview — investigate." % INSTANCE_ID)
        elif target.get("would_rebuild"):
            print("    !! WARNING: %r STILL rebuilds after restamp — investigate before boot." % INSTANCE_ID)
        else:
            print("    => VERIFIED: %r needs no rebuild; boot will reuse history (gap_days=0)." % INSTANCE_ID)
        if others_rebuilding:
            print("    (info) other linked instance(s) still flagged (NOT restamped, "
                  "expected retired): %s" % ", ".join(others_rebuilding))

        print("[tune-179] The live broker will restart via the Strategies changefeed.")
        return 0
    finally:
        with_close = getattr(conn, "close", None)
        if callable(with_close):
            conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
