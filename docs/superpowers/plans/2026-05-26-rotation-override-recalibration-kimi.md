# Rotation Override Recalibration for kimi-k2.5 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (inline, operator-chosen) to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let kimi-k2.5 act on the high-conviction signal it already produces by recalibrating the V28 rotation override floors to the 1.8 raw-score ceiling and adding a flag-gated conviction override to the `profitable_min_hold` branch — so a clearly-superior challenger can displace a marginally-profitable, merely time-locked incumbent.

**Architecture:** One additive, flag-gated code branch in `_rotation_candidate_allowed` (default off → no-op for existing/non-kimi deployments), four new config keys in the strategy schema (safe defaults), and a merge-only doc-179 apply script (dry-run by default) carrying six existing-knob recalibrations plus the four new keys. All validated by a new unit-test file and the full suite; the live doc-179 apply waits for the operator's backtest gate.

**Tech Stack:** Python, pytest, RethinkDB (`rethinkdb` driver) for the apply script.

**Spec:** `docs/superpowers/specs/2026-05-26-rotation-override-recalibration-kimi-design.md`

**Impact (gitnexus substitute — MCP not connected):** `_rotation_candidate_allowed` (`backend/strategies/graph_nexus_analysis.py:7524`) has 2 production callers (`:23642`, `:25554`) and 2 existing test files (`test_nexus_v9_preflight.py`, `test_phase_alpha_variance.py`). The change is additive + flag-gated, so existing callers and tests are unaffected.

---

## File Structure

- **Modify** `backend/strategies/graph_nexus_analysis.py`
  - `_rotation_candidate_allowed` (~line 7676): add the flag-gated `profitable_min_hold` conviction override.
  - `INTELLISTOCK_SCHEMA` (line 1): add 4 new config keys with safe (override-disabled) defaults.
- **Create** `backend/tests/test_rotation_override_recalibration.py` — unit tests for the override and the revived floors.
- **Create** `scripts/apply_doc179_rotation_override_fix.py` — merge-only doc-179 apply (dry-run by default), mirroring `scripts/apply_doc179_winner_depth_fix.py`.

---

## Task 1: Failing tests for the rotation override recalibration

**Files:**
- Create: `backend/tests/test_rotation_override_recalibration.py`

- [ ] **Step 1: Write the failing test file**

Mirror the import + kwargs-helper style of `backend/tests/test_phase_alpha_variance.py`. The helper builds a full explicit config so tests don't depend on schema defaults.

```python
"""Rotation override recalibration (kimi-k2.5 fix).

Spec: docs/superpowers/specs/2026-05-26-rotation-override-recalibration-kimi-design.md

Covers: (a) the new flag-gated profitable_min_hold conviction override, and
(b) the recalibrated break_glass / profitable_hold floors firing at the 1.8
raw-score ceiling. _rotation_candidate_allowed returns (allow, delta, reason).
"""
from backend.strategies.graph_nexus_analysis import _rotation_candidate_allowed


def _cfg(**overrides):
    base = {
        "rotation_min_delta": 0.15,
        "rotation_min_hold_days": 10,
        "rotation_profitable_min_delta": 1.0,
        "rotation_profitable_full_exit_min_hold_days": 20,
        "rotation_profitable_min_incoming_raw_score": 1.5,
        "rotation_profitable_hold_min_peak_drop_pct": 8.0,
        "rotation_winner_lock_enabled": True,
        "rotation_winner_lock_min_hold_days": 3,
        "rotation_winner_lock_min_pnl_pct": 2.0,
        "rotation_winner_lock_min_raw_score": -0.10,
        "rotation_winner_lock_max_peak_drawdown_pct": 8.0,
        "rotation_break_glass_raw_score": 1.5,
        "rotation_break_glass_delta": 1.0,
        "rotation_min_score": 0.40,
    }
    base.update(overrides)
    return base


def test_profitable_min_hold_conviction_override_fires_when_enabled():
    # Incumbent: +7% pnl, held 8d (< 20d profitable_full_exit), raw negative so
    # NOT winner-locked -> falls into the profitable_min_hold branch.
    allow, delta, reason = _rotation_candidate_allowed(
        held_pnl_pct=7.0,
        held_rotation_score=0.0,
        held_days=8,
        held_raw_score=-0.5,
        drop_from_peak_pct=0.0,
        is_equity=True,
        incoming_raw_score=1.65,
        incoming_rotation_score=1.3,  # delta = 1.3
        config=_cfg(
            profitable_min_hold_conviction_override_enabled=True,
            profitable_min_hold_conviction_min_raw_score=1.5,
            profitable_min_hold_conviction_min_delta=1.0,
            profitable_min_hold_conviction_max_held_pnl_pct=10.0,
        ),
        market_regime="bull",
    )
    assert allow is True
    assert reason == "profitable_min_hold_conviction_override"


def test_profitable_min_hold_blocks_when_override_disabled_default():
    # Same inputs, flag absent (default) -> preserves current behavior.
    allow, _delta, reason = _rotation_candidate_allowed(
        held_pnl_pct=7.0,
        held_rotation_score=0.0,
        held_days=8,
        held_raw_score=-0.5,
        drop_from_peak_pct=0.0,
        is_equity=True,
        incoming_raw_score=1.65,
        incoming_rotation_score=1.3,
        config=_cfg(),
        market_regime="bull",
    )
    assert allow is False
    assert reason == "profitable_min_hold"


def test_genuine_winner_protected_from_conviction_override():
    # Incumbent +15% pnl (>= max_held_pnl 10) -> override must NOT fire.
    allow, _delta, reason = _rotation_candidate_allowed(
        held_pnl_pct=15.0,
        held_rotation_score=0.0,
        held_days=8,
        held_raw_score=-0.5,
        drop_from_peak_pct=0.0,
        is_equity=True,
        incoming_raw_score=1.65,
        incoming_rotation_score=1.3,
        config=_cfg(
            profitable_min_hold_conviction_override_enabled=True,
            profitable_min_hold_conviction_min_raw_score=1.5,
            profitable_min_hold_conviction_min_delta=1.0,
            profitable_min_hold_conviction_max_held_pnl_pct=10.0,
        ),
        market_regime="bull",
    )
    assert allow is False
    assert reason == "profitable_min_hold"


def test_subthreshold_challenger_does_not_trigger_override():
    # incoming raw 1.4 < 1.5 floor -> no override.
    allow, _delta, reason = _rotation_candidate_allowed(
        held_pnl_pct=7.0,
        held_rotation_score=0.0,
        held_days=8,
        held_raw_score=-0.5,
        drop_from_peak_pct=0.0,
        is_equity=True,
        incoming_raw_score=1.40,
        incoming_rotation_score=1.3,
        config=_cfg(
            profitable_min_hold_conviction_override_enabled=True,
            profitable_min_hold_conviction_min_raw_score=1.5,
            profitable_min_hold_conviction_min_delta=1.0,
            profitable_min_hold_conviction_max_held_pnl_pct=10.0,
        ),
        market_regime="bull",
    )
    assert allow is False
    assert reason == "profitable_min_hold"


def test_break_glass_revived_at_ceiling():
    # Winner-locked incumbent (+5% pnl, 6d, raw positive, no drawdown).
    # Challenger raw 1.6 >= new break_glass 1.5, delta 1.1 >= 1.0 -> pierces lock.
    # With the old 3.50 floor this would return "winner_lock".
    allow, _delta, reason = _rotation_candidate_allowed(
        held_pnl_pct=5.0,
        held_rotation_score=0.0,
        held_days=6,
        held_raw_score=0.5,
        drop_from_peak_pct=0.0,
        is_equity=True,
        incoming_raw_score=1.6,
        incoming_rotation_score=1.1,  # delta = 1.1
        config=_cfg(),
        market_regime="bull",
    )
    assert allow is True
    assert reason in ("break_glass_trim", "gamma_winner_lock_bypass")


def test_profitable_hold_gate_revived_at_ceiling():
    # Held 22d (>= profitable_full_exit 20), +6% pnl, 10% off peak (>= 8 gate),
    # raw negative so not winner-locked. Challenger raw 1.6 >= new 1.5
    # profitable_min_incoming floor, delta 1.1 >= profitable_min_delta 1.0.
    allow, _delta, reason = _rotation_candidate_allowed(
        held_pnl_pct=6.0,
        held_rotation_score=0.0,
        held_days=22,
        held_raw_score=-0.5,
        drop_from_peak_pct=10.0,
        is_equity=True,
        incoming_raw_score=1.6,
        incoming_rotation_score=1.1,
        config=_cfg(),
        market_regime="bull",
    )
    assert allow is True
    assert reason == "profitable_hold"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest backend/tests/test_rotation_override_recalibration.py -v`
Expected: `test_profitable_min_hold_conviction_override_fires_when_enabled` and `test_break_glass_revived_at_ceiling`/`test_profitable_hold_gate_revived_at_ceiling` FAIL — the override branch doesn't exist yet, and (if any default leaks) the floors differ. The `_default` and `_protected` and `_subthreshold` tests may already pass (they assert current blocking behavior).

- [ ] **Step 3: Commit the failing tests**

```bash
git add backend/tests/test_rotation_override_recalibration.py
git commit -m "$(cat <<'EOF'
test(nexus): failing tests for rotation override recalibration (kimi fix)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Implement the conviction override + schema keys

**Files:**
- Modify: `backend/strategies/graph_nexus_analysis.py` (`_rotation_candidate_allowed` ~line 7676; `INTELLISTOCK_SCHEMA` line 1)

- [ ] **Step 1: Add the flag-gated override branch**

In `_rotation_candidate_allowed`, locate (currently ~line 7676):

```python
        if held_days is not None and held_days < profitable_full_exit_min_hold_days:
            # V31.7 Fix F: release profitable_min_hold when the winner has faded
```

Insert the new branch immediately INSIDE that `if`, BEFORE the `# V31.7 Fix F:` line:

```python
        if held_days is not None and held_days < profitable_full_exit_min_hold_days:
            # Kimi-recalibration: the dominant rotation blocker (profitable_min_hold,
            # 52% of rejections) had no conviction path — a max-conviction challenger
            # (raw at the 1.8 model ceiling) could not displace a merely time-locked,
            # marginally-profitable incumbent. Mirror the gamma winner_lock bypass for
            # this branch. Flag-gated (default off) so non-kimi deployments are
            # unaffected. No ticker hardcoding — score/pnl-scoped only.
            if bool(config.get("profitable_min_hold_conviction_override_enabled", False)):
                _pmh_raw = float(config.get("profitable_min_hold_conviction_min_raw_score", 1.5) or 1.5)
                _pmh_delta = float(config.get("profitable_min_hold_conviction_min_delta", 1.0) or 1.0)
                _pmh_max_pnl = float(config.get("profitable_min_hold_conviction_max_held_pnl_pct", 10.0) or 10.0)
                if (
                    float(incoming_raw_score or 0.0) >= _pmh_raw
                    and delta >= _pmh_delta
                    and float(held_pnl_pct or 0.0) < _pmh_max_pnl
                ):
                    return True, delta, "profitable_min_hold_conviction_override"
            # V31.7 Fix F: release profitable_min_hold when the winner has faded
```

(Leave all existing lines below unchanged.)

- [ ] **Step 2: Add the 4 new keys to INTELLISTOCK_SCHEMA (safe defaults)**

On line 1, find the substring `"rotation_break_glass_sell_fraction": 0.5,` and insert the new keys immediately after it (override DISABLED by default — safe no-op):

```
"profitable_min_hold_conviction_override_enabled": false, "profitable_min_hold_conviction_min_raw_score": 1.5, "profitable_min_hold_conviction_min_delta": 1.0, "profitable_min_hold_conviction_max_held_pnl_pct": 10.0,
```

- [ ] **Step 3: Run the new tests to verify they pass**

Run: `python3 -m pytest backend/tests/test_rotation_override_recalibration.py -v`
Expected: all 6 PASS.

- [ ] **Step 4: Run the existing rotation tests to verify no regression**

Run: `python3 -m pytest backend/tests/test_phase_alpha_variance.py backend/tests/test_nexus_v9_preflight.py -q`
Expected: same pass/fail as before the change (the new branch is flag-gated off; these tests don't set the flag).

- [ ] **Step 5: Commit**

```bash
git add backend/strategies/graph_nexus_analysis.py
git commit -m "$(cat <<'EOF'
feat(nexus): conviction override for profitable_min_hold rotation branch

The dominant V28 rotation blocker (profitable_min_hold, ~52% of rejections)
had no conviction escape, so a max-conviction challenger at the 1.8 raw-score
ceiling could not displace a marginally-profitable, time-locked incumbent.
Add a flag-gated bypass (default off) mirroring the gamma winner_lock bypass:
a >=1.5-raw, >=1.0-delta challenger displaces a sub-10pct-pnl hold. New schema
keys default the override off; doc 179 enables it.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: doc-179 merge-only apply script

**Files:**
- Create: `scripts/apply_doc179_rotation_override_fix.py`

- [ ] **Step 1: Write the apply script**

Mirror `scripts/apply_doc179_winner_depth_fix.py` exactly (same `_connect`, dry-run/`--apply`, drift-advisory, confirm-after-write structure), changing only the docstring, `CHANGES`, and `ROLLBACK_HINT`:

```python
"""Apply the rotation override recalibration (kimi-k2.5 fix) to prod Strategies
doc 179 ("Nexus Only").

MERGE-ONLY: reads the full doc, mutates ONLY the target keys inside
doc['strategies'][0]['config'], and writes the strategies field back — every
other key (incl. plaintext secrets) and every other top-level field is preserved.

Read-only by default. Pass --apply to write. DO NOT --apply until a cold kimi
backtest of this config has cleared the +113% baseline (operator backtest gate).

Usage:
  RETHINKDB_HOST=REDACTED-IP python scripts/apply_doc179_rotation_override_fix.py          # dry-run
  RETHINKDB_HOST=REDACTED-IP python scripts/apply_doc179_rotation_override_fix.py --apply   # write
"""
from __future__ import annotations

import argparse
import os
import sys

DB_NAME = "IntelliStock"
DOC_ID = 179

# key -> (expected_current, target). expected_current is advisory: a mismatch
# warns (config drifted / key absent) but does NOT block setting the target.
# The dry-run prints the real current values regardless.
CHANGES: dict[str, tuple[object, object]] = {
    # Recalibrate override floors to the 1.8 raw-score ceiling (current floors
    # are above the ceiling -> mathematically dead).
    "rotation_winner_lock_bypass_min_raw_score": (1.8, 1.5),
    "rotation_break_glass_raw_score": (3.50, 1.5),
    "rotation_break_glass_delta": (2.50, 1.0),
    "rotation_profitable_min_incoming_raw_score": (2.0, 1.5),
    # Activate the existing peak-drop escape from the profitable_min_hold block.
    "profitable_min_hold_release_enabled": (False, True),
    "profitable_min_hold_release_peak_drop_pct": (12.0, 8.0),
    # New conviction override (Part 2 code) for the profitable_min_hold branch.
    "profitable_min_hold_conviction_override_enabled": ("<ABSENT>", True),
    "profitable_min_hold_conviction_min_raw_score": ("<ABSENT>", 1.5),
    "profitable_min_hold_conviction_min_delta": ("<ABSENT>", 1.0),
    "profitable_min_hold_conviction_max_held_pnl_pct": ("<ABSENT>", 10.0),
}

ROLLBACK_HINT = (
    "ROLLBACK: set rotation_winner_lock_bypass_min_raw_score=1.8, "
    "rotation_break_glass_raw_score=3.50, rotation_break_glass_delta=2.50, "
    "rotation_profitable_min_incoming_raw_score=2.0, "
    "profitable_min_hold_release_enabled=False, "
    "profitable_min_hold_release_peak_drop_pct=12.0, and DELETE the four "
    "profitable_min_hold_conviction_* keys."
)


def _connect():
    from rethinkdb import RethinkDB

    r = RethinkDB()
    host = os.environ.get("RETHINKDB_HOST", "localhost")
    port = int(os.environ.get("RETHINKDB_PORT", "28015"))
    conn = r.connect(host=host, port=port, timeout=15)
    return r, conn, host, port


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry-run)")
    args = ap.parse_args()

    r, conn, host, port = _connect()
    print(f"[connected] {host}:{port} db={DB_NAME}")

    doc = r.db(DB_NAME).table("Strategies").get(DOC_ID).run(conn)
    if not doc:
        print(f"[ERROR] Strategies doc {DOC_ID} not found")
        return 2
    strategies = doc.get("strategies") or []
    if not strategies or not isinstance(strategies[0], dict):
        print("[ERROR] doc['strategies'][0] missing/!dict")
        return 2
    cfg = strategies[0].get("config")
    if not isinstance(cfg, dict):
        print("[ERROR] doc['strategies'][0]['config'] missing/!dict")
        return 2

    print(f"[doc] id={doc.get('id')} name={doc.get('name')!r} config_keys={len(cfg)}")
    print(f"[mode] {'APPLY (write)' if args.apply else 'DRY-RUN (read-only)'}")
    print("-" * 72)
    print(f"{'knob':<48} {'current':>9} -> {'target':>7}")
    drift = []
    for key, (expected, target) in CHANGES.items():
        current = cfg.get(key, "<ABSENT>")
        flag = ""
        if current != expected:
            flag = "  <-- DRIFT (expected %r)" % (expected,)
            drift.append(key)
        print(f"{key:<48} {str(current):>9} -> {str(target):>7}{flag}")
    print("-" * 72)
    if drift:
        print(f"[warn] {len(drift)} knob(s) differ from the expected pre-state: {drift}")
        print("[warn] proceeding will still set the targets above.")

    if not args.apply:
        print("[dry-run] no write performed. Re-run with --apply to write.")
        print(ROLLBACK_HINT)
        conn.close()
        return 0

    for key, (_expected, target) in CHANGES.items():
        cfg[key] = target
    strategies[0]["config"] = cfg
    res = r.db(DB_NAME).table("Strategies").get(DOC_ID).update({"strategies": strategies}).run(conn)
    print(f"[write] update result: {res}")
    if res.get("errors"):
        print(f"[ERROR] update reported {res['errors']} error(s): {res.get('first_error')}; aborting before confirm.")
        conn.close()
        return 4

    doc2 = r.db(DB_NAME).table("Strategies").get(DOC_ID).run(conn)
    cfg2 = doc2["strategies"][0]["config"]
    print("[confirm] post-write values:")
    ok = True
    for key, (_expected, target) in CHANGES.items():
        got = cfg2.get(key, "<ABSENT>")
        match = "OK" if got == target else "MISMATCH"
        if got != target:
            ok = False
        print(f"  {key:<48} = {got}   [{match}]")
    print(f"[confirm] config_keys now = {len(cfg2)} (was {len(cfg)})")
    print(ROLLBACK_HINT)
    conn.close()
    return 0 if ok else 3


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Smoke-check the script imports and parses (no DB connection)**

Run: `python3 -c "import ast; ast.parse(open('scripts/apply_doc179_rotation_override_fix.py').read()); print('parse OK')"`
Expected: `parse OK`. (Do NOT run the script itself — it connects to prod; that is the operator's gated step.)

- [ ] **Step 3: Commit**

```bash
git add scripts/apply_doc179_rotation_override_fix.py
git commit -m "$(cat <<'EOF'
chore(nexus): merge-only apply script for doc-179 rotation override knobs

Dry-run by default; --apply writes the 6 recalibrated rotation knobs + 4 new
conviction-override keys to doc 179. Gated: do NOT apply until a cold kimi
backtest clears the +113% baseline.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Full-suite verification

- [ ] **Step 1: Run the full backend suite**

Run: `python3 -m pytest backend/tests/ -q --ignore=backend/tests/test_intellistock_logger.py --ignore=backend/tests/test_redact_logger.py`
Expected: the documented baseline of **21 pre-existing failures and 0 NEW failures**; `test_rotation_override_recalibration.py` all green.

- [ ] **Step 2: If any NEW failure appears, fix it before proceeding** (the change is flag-gated and additive — a new failure means a real interaction; investigate `_rotation_candidate_allowed` callers).

---

## Self-Review

**Spec coverage:**
- Config recalibration (6 existing knobs) → Task 3 apply script ✓
- New conviction override code → Task 2 ✓
- New config keys + safe defaults → Task 2 (schema) + Task 3 (doc 179 values) ✓
- Tests (6 scenarios from spec) → Task 1 ✓
- Full-suite gate (21 baseline) → Task 4 ✓
- Rollback → apply-script `ROLLBACK_HINT` + flag-off ✓
- Backtest gate before live → documented in apply-script docstring + Task 3 commit body ✓

**Placeholder scan:** none — all steps show exact code/commands.

**Type consistency:** override returns the existing `(bool, float, str)` tuple; config keys used in the code (`profitable_min_hold_conviction_*`) match the schema keys (Task 2 Step 2) and the apply-script `CHANGES` keys (Task 3) exactly.

**Note:** Diagnostic-line surfacing of the new flag was intentionally omitted — the recalibrated `break=1.5/1.0` already shows in the effective-config log line (confirms config deploy), and the `profitable_min_hold_conviction_override` reason string appears in rotation logs when it fires (confirms code behavior + enables backtest decomposition). Adding it is optional polish, deferred unless the bug sweep flags a convention gap.
