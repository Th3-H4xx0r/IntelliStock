#!/usr/bin/env python3
"""Run one backtest per window, sequentially, and record the ids.

WHY. 52 of this project's first 100 backtests used ONE window
(2026-01-01..2026-03-01). Every mechanism in the codebase was found there, so
measuring there again cannot say whether any of it generalises. This steps
across the whole available range (2025-11-10 .. 2026-08-01).

Single-arm, not paired: the objective's bar is "beat SPY in every regime", and
`benchmark_quote_logging_enabled` now gives a full-span SPY series per run, so a
control arm would double the spend for a comparison nobody asked for.

Strictly sequential — the deployment runs ONE backtest at a time and a second
launch silently preempts the first.

    python3 scripts/run_generalisation_sweep.py a b c d e f
    python3 scripts/run_generalisation_sweep.py --dry-run a b c
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "scripts"))
from pull_backtest_logs import _load_dotenv, _login, _fetch, _http  # noqa: E402
from arm_unseal_pair import WINDOWS  # noqa: E402

# Hard spend guard. Each run costs roughly $1 of LLM calls; the operator's
# remaining budget was $17 when this was written. A runaway loop here spends
# real money, so the cap is in the code and not only in the invocation.
MAX_RUNS = 8
STATE = Path("/private/tmp/claude-501/-Users-pranavkrishna-PranavFiles-"
             "coding-projects-IntelliStock/df51be96-6c29-43b3-8917-1756634b59e5/"
             "scratchpad/sweep_state.json")


def _api():
    _load_dotenv(_REPO)
    api = (os.environ.get("INTELLISTOCK_API_URL") or "").rstrip("/")
    token = os.environ.get("INTELLISTOCK_API_TOKEN") or _login(
        api, os.environ.get("DEFAULT_ADMIN_USERNAME", "admin"),
        os.environ.get("DEFAULT_ADMIN_PASSWORD", ""))
    return api, token


def _status(api, token, bt):
    try:
        _, body = _fetch(api, token, f"/backtests/{bt}/status", allow_404=True)
        if isinstance(body, dict):
            return body.get("status"), body.get("progress")
    except Exception as exc:
        return f"pollerror:{type(exc).__name__}", None
    return None, None


def _say(msg):
    print(f"[sweep] {msg}", flush=True)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("windows", nargs="+", choices=sorted(WINDOWS))
    ap.add_argument("--instance", default="v2-conv-trt")
    ap.add_argument("--cash", default="6000")
    ap.add_argument("--granularity", default="3600")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)

    if len(a.windows) > MAX_RUNS:
        raise SystemExit(f"refusing {len(a.windows)} runs; MAX_RUNS={MAX_RUNS}")

    api, token = _api()
    results = []
    if STATE.exists():
        try:
            results = json.loads(STATE.read_text())
        except Exception:
            results = []
    done = {r["window"] for r in results if r.get("status") == "finished"}

    for label in a.windows:
        if label in done:
            _say(f"{label}: already finished, skipping")
            continue
        start, end, why = WINDOWS[label]
        _say(f"=== {label}: {start} -> {end}  ({why}) ===")

        # Rotate BOTH salts for this window. Reusing another window's salts
        # would serve this run the earlier run's rows as immortal lookback.
        arm = subprocess.run(
            [sys.executable, str(_REPO / "scripts" / "arm_unseal_pair.py"),
             "--window", label] + ([] if a.dry_run else ["--apply"]),
            capture_output=True, text=True, timeout=900)
        if "Pair is clean" not in arm.stdout:
            _say(f"{label}: ARMING DID NOT VERIFY — stopping.\n{arm.stdout[-1500:]}")
            break
        _say(f"{label}: armed")

        if a.dry_run:
            results.append({"window": label, "status": "dry-run"})
            continue

        launch = subprocess.run(
            [sys.executable, str(_REPO / "scripts" / "run_validation_backtest.py"),
             start, end, "--cash", a.cash, "--granularity", a.granularity,
             "--instance", a.instance],
            capture_output=True, text=True, timeout=900)
        try:
            bt = json.loads(launch.stdout.strip().splitlines()[-1])["id"]
        except Exception:
            _say(f"{label}: LAUNCH FAILED — stopping.\n{launch.stdout[-800:]}"
                 f"\n{launch.stderr[-800:]}")
            break
        _say(f"{label}: launched bt {bt}")

        last = None
        while True:
            st, pr = _status(api, token, bt)
            if (st, pr) != last:
                _say(f"{label}: bt {bt} status={st} progress={pr}")
                last = (st, pr)
            if st in ("finished", "stopped", "failed", "error", "cancelled"):
                break
            time.sleep(120)

        results.append({"window": label, "bt": bt, "status": st,
                        "start": start, "end": end, "why": why})
        STATE.write_text(json.dumps(results, indent=1))
        _say(f"{label}: bt {bt} TERMINAL status={st}")
        if st != "finished":
            _say(f"{label}: did not finish — stopping the sweep rather than "
                 "spending the rest of the budget on a broken configuration.")
            break

    STATE.write_text(json.dumps(results, indent=1))
    _say("SWEEP COMPLETE")
    for r in results:
        _say(f"  {r.get('window')}: bt {r.get('bt')} {r.get('status')} "
             f"({r.get('start')} -> {r.get('end')})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
