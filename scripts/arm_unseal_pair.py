#!/usr/bin/env python3
"""Arm the two arms of the 2026-08-14b "unseal the book" pair.

See docs/investigations/prereg-unseal-the-book-2026-08-14b.md. DRY RUN by
default; pass --apply to write.

Why a script and not a dozen `set_doc_config.py` calls: the pair is only valid
if the two documents differ in the TREATMENT keys and nothing else, and a hand
sequence of edits is exactly how `residual_sleeve_bear_alloc_pct` was believed
to be 0.35 for a whole session while every run logged 0.70. This applies both
arms from one table and then re-reads both documents and prints the resulting
difference, so the pair is verified against the API rather than against
intention.

    python3 scripts/arm_unseal_pair.py            # show the plan and the diff
    python3 scripts/arm_unseal_pair.py --apply    # write it
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

CONTROL_DOC, TREATMENT_DOC = 194, 195

# Applied to BOTH arms. Isolation first: a fresh `history_scope_salt` alone does
# NOT isolate an arm — `_active_event_history_scope_id` hashes its own salt
# (graph_nexus_analysis.py:4617) and the two prior W0 arms shared active-event
# scope de83e7d59f26 with 17 vs 16 LLM-skip cache hits; and
# GraphNexusDiscoverySnapshots is BASE-instance-keyed and bootstraps precisely
# when the new scope is empty (:12930-12932), so a fresh salt invites an arm to
# import its sibling's discovered universe.
SHARED = {
    "benchmark_quote_logging_enabled": True,
    "nexus_discovery_bootstrap_enabled": False,
    "nexus_discovery_snapshot_enabled": False,
    # Explicit on BOTH arms rather than absent on one. It is already the code
    # default, so this changes no behaviour — but bt 718107 vs bt 523085 is a
    # near-clean A/B on this flag and it cost 1.12pp, so it must be pinned and
    # visibly identical rather than merely defaulted.
    "satellite_displacement_enabled": False,
    # The overlay result cache is COMPLETELY UNSCOPED: its key is
    # md5(symbol|date_key|round(raw_net_score,1)|event_types|model)
    # (graph_nexus_analysis.py:22490-22498) — no instance, no history scope, no
    # salt, and the score is bucketed to one decimal so near-miss scores
    # collide. The treatment changes slots and sizing, not scores, so the two
    # arms produce matching keys on most names and the SECOND arm replays the
    # FIRST arm's LLM overlay verdicts. That is order-dependent, not symmetric,
    # and neither salt reaches it. Off on both arms.
    "overlay_result_cache_enabled": False,
    # The fifth max_positions counter (graph_nexus_analysis.py:29522) is the one
    # site that never moved onto `slot_exclusions`. With the core leg held and a
    # cap of 4 it reads 5 > 4, latches a permanent V28.8.1 BREACH and blocks
    # every new-ticker buy — so the treatment would measure permanently-breached
    # rotation machinery rather than a four-name book, and the alpha book would
    # converge to THREE names while max_positions read 4. On both arms so the
    # counter cannot be the difference between them.
    "slot_exclusions_all_counters_enabled": True,
}
# Salts rotate per ARM **and per WINDOW**.
#
# Per-arm alone is not enough, and this is the exact trap the 2026-08-14 audit
# documented: the same scoped instance was reused across three different
# windows, so every row the W0 run wrote during Jan-Feb was `< date_key` for a
# March window and therefore IMMORTAL — presented to the later run as legitimate
# lookback. Measured: 178 / 242 / 285 trends inherited on one byte-identical
# scoped instance id. State became a function of which backtests happened to run
# before, in what order.
#
# So a generalisation sweep that reuses W0's salts on W1/W2/W3 rebuilds the
# contamination it is supposed to be testing through. One salt per (arm, window).
WINDOWS = {
    # label: (start, end, what it tests)
    "w0": ("2026-01-01", "2026-03-01", "reference bull, semiconductor-heavy"),
    "w1": ("2026-03-30", "2026-04-27", "OUT OF SAMPLE bull"),
    "w2": ("2026-03-02", "2026-03-30", "bear — safety veto, must not be harmed"),
    "w3": ("2026-06-01", "2026-07-01", "NON-SEMICONDUCTOR; currently -10.14pp vs SPY"),
}


def _salts(window):
    return {
        CONTROL_DOC: {"history_scope_salt": f"uns-ctl-{window}-0814b",
                      "active_event_history_scope_salt": f"uns-ctl-{window}-0814b"},
        TREATMENT_DOC: {"history_scope_salt": f"uns-trt-{window}-0814b",
                        "active_event_history_scope_salt": f"uns-trt-{window}-0814b"},
    }

# The treatment. Every key here must be ABSENT or at its off-value on 194.
TREATMENT = {
    # The book is sealed at six names: 6 x 14% = 84% of an 88% ceiling leaves
    # ~$250 against a ~$370 min-position floor, so nothing can enter and no
    # winner can be added. Four names reopens ~$1,920 of room.
    # `regime_chop_max_positions` and `regime_bear_max_positions` are NOT here:
    # broker.py:5906-5913 is an explicit dead-key registry and both are in it
    # ("no reader; the live key is max_positions_chop / max_positions_bear").
    # Setting them would be a no-op that reads like a lever.
    #
    # `max_positions_bear` is deliberately left at 2 on both arms, so the
    # treatment is INERT in bear — W0 opens in a bull, but any bear stretch
    # inside it measures nothing. `max_positions_crash` is absent on both
    # (default 0), also symmetric.
    "max_positions": 4,
    "max_positions_bull": 4,
    "max_positions_chop": 4,
    "max_positions_recovery": 4,
    # The passive core takes every dollar of cash (its deploy is CASH-bound on
    # 4 of 5 deploys) and its order pends into the next bar, where the
    # reservation refuses every alpha buy.
    "core_deploy_alpha_headroom_pct": 0.07,
    # The satellite cap declines instead of emitting an order the floor refuses.
    "conversion_fixes_enabled": True,
}
# The control's value for each treatment key. `None` means "remove the key".
CONTROL_OFF = {
    "max_positions": 6,
    "max_positions_bull": 14,
    "max_positions_chop": 8,
    "max_positions_recovery": 14,
    "core_deploy_alpha_headroom_pct": None,
    "conversion_fixes_enabled": None,
}


def _api():
    _load_dotenv(_REPO)
    api = (os.environ.get("INTELLISTOCK_API_URL")
           or f"http://localhost:{os.environ.get('API_PORT', '8000')}").rstrip("/")
    token = os.environ.get("INTELLISTOCK_API_TOKEN") or _login(
        api,
        os.environ.get("DEFAULT_ADMIN_USERNAME", "admin"),
        os.environ.get("DEFAULT_ADMIN_PASSWORD", ""))
    return api, {"Authorization": f"Bearer {token}"}


def _get(api, auth, doc_id):
    status, doc = _http("GET", f"{api}/strategies/{doc_id}", headers=auth)
    if status != 200 or not isinstance(doc, dict):
        raise SystemExit(f"GET /strategies/{doc_id} -> {status}: {str(doc)[:300]}")
    return doc


def _put(api, auth, doc_id, doc, lanes):
    status, body = _http(
        "PUT", f"{api}/strategies/{doc_id}",
        headers={**auth, "Content-Type": "application/json"},
        body=json.dumps({"name": doc.get("name"), "strategies": lanes,
                         "preserve_history": True}))
    if status != 200:
        raise SystemExit(f"PUT /strategies/{doc_id} -> {status}: {str(body)[:300]}")


def _plan(doc_id, window):
    want = dict(SHARED)
    want.update(_salts(window)[doc_id])
    want.update(TREATMENT if doc_id == TREATMENT_DOC else CONTROL_OFF)
    return want


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--window", default="w0", choices=sorted(WINDOWS),
                    help="which window this pair is for; rotates BOTH salts "
                         "so a later window cannot inherit an earlier run's "
                         "rows as immortal lookback")
    args = ap.parse_args(argv)
    start, end, why = WINDOWS[args.window]
    print(f"window {args.window}: {start} -> {end}  ({why})")
    api, auth = _api()
    projected = {}

    for doc_id in (CONTROL_DOC, TREATMENT_DOC):
        doc = _get(api, auth, doc_id)
        lanes = doc.get("strategies") or []
        if not lanes:
            raise SystemExit(f"doc {doc_id} has no strategy lane")
        cfg = dict(lanes[0].get("config") or {})
        want = _plan(doc_id, args.window)
        changes = []
        for key, value in want.items():
            before = cfg.get(key, "<absent>")
            if value is None:
                if key in cfg:
                    changes.append((key, before, "<removed>"))
                    del cfg[key]
                continue
            if before != value:
                changes.append((key, before, value))
                cfg[key] = value
        print(f"\n=== doc {doc_id} — {len(changes)} change(s) "
              f"{'APPLYING' if args.apply else 'would change'} ===")
        for key, before, after in changes:
            print(f"  {key}\n      {before!r}  ->  {after!r}")
        projected[doc_id] = cfg
        if args.apply and changes:
            new_lanes = copy.deepcopy(lanes)
            new_lanes[0]["config"] = cfg
            _put(api, auth, doc_id, doc, new_lanes)
            print(f"  written.")

    # The difference the pair is judged on. After --apply this is re-READ from
    # the API, so a write that silently did not land is caught here rather than
    # in the run; in dry-run it is the projected state, so the plan can be
    # checked before anything is written.
    if args.apply:
        print("\n=== VERIFY: resulting difference, read back from the API ===")
        a = (_get(api, auth, CONTROL_DOC).get("strategies") or [{}])[0].get("config") or {}
        b = (_get(api, auth, TREATMENT_DOC).get("strategies") or [{}])[0].get("config") or {}
    else:
        print("\n=== VERIFY (projected): the difference this plan would leave ===")
        a, b = projected[CONTROL_DOC], projected[TREATMENT_DOC]
    expected = set(TREATMENT) | {"history_scope_salt", "active_event_history_scope_salt"}
    unexpected = []
    for key in sorted(set(a) | set(b)):
        va, vb = a.get(key, "<absent>"), b.get(key, "<absent>")
        if va == vb:
            continue
        flag = "ok " if key in expected else "!! "
        if key not in expected:
            unexpected.append(key)
        print(f"  {flag}{key}: 194={va!r}  195={vb!r}")
    if unexpected:
        print(f"\nUNEXPECTED DIFFERENCES: {unexpected}\n"
              "The pair is NOT clean — every difference other than the treatment\n"
              "keys and the two salts confounds the result. Fix before launching.")
        return 1
    print("\nPair is clean: the arms differ only in the treatment keys and their salts.")
    if not args.apply:
        print("\ndry-run: nothing written. Re-run with --apply.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
