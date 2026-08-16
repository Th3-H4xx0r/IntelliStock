#!/usr/bin/env python3
"""Ask whether a paired A/B is comparable BEFORE reading its return delta.

    python3 scripts/check_pair_validity.py <control_id> <treatment_id>

Named `check_pair_validity` rather than `pair_validity` on purpose: a script shares its
own directory as sys.path[0], so a same-named script would shadow the backend module it
imports and fail with a confusing ImportError.

Why this exists: `read_unseal_pair.py` scores six endpoints for a pair and has no notion
of whether the two arms are comparable at all. On 2026-08-16 bt 453789 vs bt 333727 —
same document, window, instance, cash, ONE flag apart — shared 20% of their traded names,
and the two names the experiment was built on were absent from the treatment. Reading
that delta as "the lever is neutral" would have been reporting a lottery draw.

Reads the runs' OWN end-of-run P&L blocks, never config. A config key proves what was
requested; only the log proves what ran.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "backend"))
sys.path.insert(0, str(_REPO / "scripts"))

from pair_validity import assess_pair, traded_symbols  # noqa: E402

_RETURN = re.compile(r"Profit & Loss:\s*[-+]?\$[\d,.]+\s*\(([-+]?[\d.]+)%\)")


def _log_for(backtest_id, cache_dir):
    """Fetch a run's log via the repo's own puller (it sets the User-Agent that
    Cloudflare requires — a raw urllib request 403s with error 1010)."""
    out = Path(cache_dir) / f"bt{backtest_id}.log"
    if not out.is_file():
        subprocess.run(
            [sys.executable, str(_REPO / "scripts" / "pull_backtest_logs.py"),
             str(backtest_id), "--out", str(out)],
            check=True, capture_output=True,
        )
    return out.read_text(encoding="utf-8", errors="replace")


def _arm(backtest_id, cache_dir):
    text = _log_for(backtest_id, cache_dir)
    m = _RETURN.search(text)
    return {
        "id": backtest_id,
        "symbols": traded_symbols(text),
        "return_pct": float(m.group(1)) if m else None,
    }


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("control_id")
    p.add_argument("treatment_id")
    p.add_argument("--cache-dir", default=".")
    p.add_argument("--min-overlap", type=float, default=0.60)
    a = p.parse_args(argv)

    ctl = _arm(a.control_id, a.cache_dir)
    trt = _arm(a.treatment_id, a.cache_dir)
    r = assess_pair(ctl, trt, min_overlap=a.min_overlap)

    print(f"control   bt {ctl['id']}: {len(ctl['symbols']):>3} names, "
          f"return {ctl['return_pct'] if ctl['return_pct'] is not None else 'MISSING'}")
    print(f"treatment bt {trt['id']}: {len(trt['symbols']):>3} names, "
          f"return {trt['return_pct'] if trt['return_pct'] is not None else 'MISSING'}")
    print()
    print(f"  shared         : {', '.join(r['shared']) or '(none)'}")
    print(f"  control only   : {', '.join(r['control_only']) or '(none)'}")
    print(f"  treatment only : {', '.join(r['treatment_only']) or '(none)'}")
    print()
    print(f"  overlap        : {r['overlap']:.0%}  (floor {r['min_overlap']:.0%})")
    if r["delta_pp"] is not None:
        print(f"  delta          : {r['delta_pp']:+.2f}pp")
    print()
    print(f"VERDICT: {r['verdict']} — {r['reason']}")
    # VOID is the case worth a non-zero exit: it is the one where a number is about
    # to be quoted that should not be.
    return 2 if r["verdict"] == "VOID" else 0


if __name__ == "__main__":
    sys.exit(main())
