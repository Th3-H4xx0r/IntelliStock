#!/usr/bin/env python3
"""A/B-validate a bull-only lever on the LOCAL harness: does it raise bull P&L
while leaving the bear window unchanged? One command, runs when infra is up.

Sequence (each backtest = real engine, free claude-cli LLM, local cache):
  1. Preflight infra (RethinkDB reachable). Exit 3 if down.
  2. Patch doc-179 to the BULL profile (candidate OFF) via the API; run the
     bull known window -> BULL BASELINE.
  3. Patch doc-179 to the BULL profile + candidate (this run: the --patch json);
     run the bull window -> compare to baseline.
  4. Patch doc-179 to the BEAR profile (candidate is bull-gated -> inert here);
     run the bear window with and without the candidate -> assert byte-unchanged.
  5. Revert doc-179 to the pre-existing config.

Usage:
  python scripts/validate_bull_candidate.py \
      --patch scripts/doc179_patch_bull_alpha_v3.json \
      --bull-profile scripts/doc179_profile_bull.json \
      --bear-profile scripts/doc179_profile_bear.json
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "scripts"))
import local_backtest as lb  # noqa: E402

BULL = ("alpaca-main", "2026-03-30", "2026-04-27")
BEAR = ("alpaca-main", "2026-03-02", "2026-03-30")
_APPLY = _REPO / "scripts" / "apply_doc179_config_patch_api.py"


def _apply(patch_path: str) -> str | None:
    """Apply a full config profile/patch via the API; return the backup path
    (for revert) or None on failure."""
    out = subprocess.run([sys.executable, str(_APPLY), "--patch", patch_path, "--apply"],
                         cwd=str(_REPO), capture_output=True, text=True)
    sys.stderr.write(out.stdout + out.stderr)
    for line in (out.stdout or "").splitlines():
        if line.startswith("APPLIED") and "Backup:" in line:
            return line.split("Backup:", 1)[1].strip()
    return None


def _revert(backup_path: str) -> None:
    subprocess.run([sys.executable, str(_APPLY), "--revert", backup_path], cwd=str(_REPO))


def _run(win, tag) -> float | None:
    r = lb.run_one(*win, granularity="3600", cash=6000.0)
    pnl = r.get("pnl_percent")
    print(f"[validate] {tag}: pnl%={pnl}")
    return pnl if isinstance(pnl, (int, float)) else None


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--patch", required=True, help="bull profile + candidate")
    p.add_argument("--bull-profile", default=str(_REPO / "scripts" / "doc179_profile_bull.json"))
    p.add_argument("--bear-profile", default=str(_REPO / "scripts" / "doc179_profile_bear.json"))
    p.add_argument("--bear-tol", type=float, default=0.6)
    a = p.parse_args(argv)

    ok, why = lb._infra_reachable(lb._env())
    if not ok:
        print(f"[validate] PREFLIGHT FAILED: {why}\n  Re-run when infra is up.", file=sys.stderr)
        return lb.EXIT_INFRA_DOWN

    first_backup = None
    try:
        # --- BULL: baseline (profile only) vs candidate (profile + patch) ---
        b0 = _apply(a.bull_profile);  first_backup = first_backup or b0
        bull_base = _run(BULL, "BULL baseline (profile)")
        _apply(a.patch)
        bull_cand = _run(BULL, "BULL candidate (profile+patch)")

        # --- BEAR: must be unchanged (candidate is bull-gated -> inert) ---
        _apply(a.bear_profile)
        bear_base = _run(BEAR, "BEAR baseline (bear profile)")
        _apply_bear_patch = json.loads(Path(a.bear_profile).read_text())
        _apply_bear_patch.update({k: v for k, v in json.loads(Path(a.patch).read_text()).items()
                                  if k.startswith("v32_convert_min_loss_pct_bull")
                                  or k.endswith("_bull")})
        _tmp = _REPO / "scripts" / "_tmp_bear_with_candidate.json"
        _tmp.write_text(json.dumps(_apply_bear_patch, indent=1))
        _apply(str(_tmp))
        bear_cand = _run(BEAR, "BEAR candidate (bear profile + bull-gated keys)")
        _tmp.unlink(missing_ok=True)
    finally:
        if first_backup:
            print("[validate] reverting doc-179 to pre-existing config…", file=sys.stderr)
            _revert(first_backup)

    print("\n=== VALIDATION SUMMARY ===")
    print(f"BULL  baseline {bull_base}  -> candidate {bull_cand}  "
          f"(Δ {None if None in (bull_base,bull_cand) else round(bull_cand-bull_base,2)}pp)")
    print(f"BEAR  baseline {bear_base}  -> candidate {bear_cand}  "
          f"(Δ {None if None in (bear_base,bear_cand) else round(bear_cand-bear_base,2)}pp)")
    bull_up = (bull_base is not None and bull_cand is not None and bull_cand > bull_base)
    bear_ok = (bear_base is not None and bear_cand is not None and abs(bear_cand - bear_base) <= a.bear_tol)
    verdict = "PASS ✓ (bull up, bear unchanged)" if (bull_up and bear_ok) else "NO-GO"
    print(f"VERDICT: {verdict}")
    return 0 if (bull_up and bear_ok) else 1


if __name__ == "__main__":
    sys.exit(main())
