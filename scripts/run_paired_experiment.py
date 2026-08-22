#!/usr/bin/env python3
"""Run a paired A/B the only way it can currently mean anything.

    # A/A determinism check — same config both arms
    python3 scripts/run_paired_experiment.py --instance v2-conv-trt \
        --start 2026-04-01 --end 2026-06-01

    # A/B — one lever, applied to the treatment arm only
    python3 scripts/run_paired_experiment.py --instance v2-conv-trt --doc 195 \
        --start 2026-04-01 --end 2026-06-01 \
        --treatment momentum_breakout_freshness_pct=5.0

Every paired result in this project has been unreadable for one measured reason: the arms did
not start from the same state. bt 333727 vs 453789 shared 20% of their traded names; 453789 vs
749060, 23%; 333727 vs 826225, 11% — all one config flag apart. The instance was carrying 4,213
rows of decision-steering state between runs (2,939 discovered stocks, 1,241 trends, 1,907
active events).

So this enforces the sequence by hand rather than trusting anyone to remember it:

    clear -> attest COLD -> apply arm config -> run -> wait
    clear -> attest COLD (must match arm A's) -> apply arm config -> run -> wait
    -> compare start fingerprints, traded-name overlap, and entry lag

and REFUSES to report a return delta when the arms are not comparable.

Backtests run one at a time — a second launch silently preempts the first — so the arms are
strictly sequential, and the document is never edited while a run is in flight (this codebase
reads config live).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "backend"))
sys.path.insert(0, str(_REPO / "scripts"))

from entry_lag import compare as lag_compare, measure as lag_measure  # noqa: E402
from pair_validity import assess_pair, traded_symbols  # noqa: E402
from paired_state_attest import compare_arm_starts, is_cold  # noqa: E402
from pull_backtest_logs import _http, _load_dotenv, _login  # noqa: E402

_load_dotenv(_REPO)
import os  # noqa: E402

API = (os.environ.get("INTELLISTOCK_API_URL") or "").rstrip("/")
_TOKEN = None


def _auth():
    global _TOKEN
    if _TOKEN is None:
        _TOKEN = os.environ.get("INTELLISTOCK_API_TOKEN") or _login(
            API, os.environ.get("DEFAULT_ADMIN_USERNAME", "admin"),
            os.environ.get("DEFAULT_ADMIN_PASSWORD", ""))
    return {"Authorization": f"Bearer {_TOKEN}", "Content-Type": "application/json"}


def _get(path):
    return _http("GET", API + path, headers=_auth(), timeout=180)[1]


def _post(path, body):
    return _http("POST", API + path, headers=_auth(), body=json.dumps(body), timeout=300)[1]


def _clear(instance):
    r = _post(f"/instances/{instance}/clear-state",
              {"scope": "full_instance", "apply": True, "confirm": instance})
    return sum(t.get("deleted", 0) for t in (r.get("tables") or []))


def _attest(instance):
    """Read the instance's start fingerprint via the real attestation script."""
    out = subprocess.run(
        [sys.executable, str(_REPO / "scripts" / "attest_arm_start.py"), instance,
         "--out", f"/tmp/_pair_{instance}.json"],
        capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit(f"attestation failed:\n{out.stdout}\n{out.stderr}")
    return json.loads(Path(f"/tmp/_pair_{instance}.json").read_text())


def _apply(doc, settings):
    if not settings:
        return
    args = [sys.executable, str(_REPO / "scripts" / "set_doc_config.py"), str(doc)]
    for kv in settings:
        args += ["--set", kv]
    args.append("--apply")
    r = subprocess.run(args, capture_output=True, text=True)
    print(r.stdout.strip()[-400:])
    if r.returncode != 0:
        raise SystemExit("config apply failed — refusing to run a mislabelled arm")


def _launch_and_wait(instance, start, end, cash, granularity, poll=120):
    r = _post("/backtests", {"instance_id": instance, "stocks": [], "start_date": start,
                             "end_date": end, "granularity": granularity,
                             "initial_cash": cash})
    bid = r.get("id")
    print(f"  launched bt {bid}")
    while True:
        time.sleep(poll)
        st = _get(f"/backtests/{bid}/status")
        s = str(st.get("status") or "").lower()
        if s in ("finished", "error", "stopped", "completed"):
            print(f"  bt {bid} -> {s} ({st.get('progress')}%)")
            return bid, s
        print(f"    {st.get('progress', 0):.1f}% …", flush=True)


def _log_text(bid):
    out = Path(f"/tmp/bt{bid}.log")
    if not out.is_file():
        subprocess.run([sys.executable, str(_REPO / "scripts" / "pull_backtest_logs.py"),
                        str(bid), "--out", str(out)], check=True, capture_output=True)
    return out.read_text(encoding="utf-8", errors="replace")


def _return_pct(text):
    import re
    m = re.search(r"Profit & Loss:\s*[-+]?\$[\d,.]+\s*\(([-+]?[\d.]+)%\)", text)
    return float(m.group(1)) if m else None


def _snapshot(action, instance, path, apply=False):
    args = [sys.executable, str(_REPO / "scripts" / "snapshot_instance_state.py"),
            action, instance]
    args += (["--out", path] if action == "export" else ["--in", path])
    if apply:
        args.append("--apply")
    r = subprocess.run(args, capture_output=True, text=True)
    print("  " + (r.stdout.strip().splitlines() or ["(no output)"])[-1])
    if r.returncode != 0:
        print(r.stdout[-800:])
        raise SystemExit(f"snapshot {action} failed — arms would not be comparable")


def _run_arm(label, args, settings, snapshot_path=None):
    """One arm. Cold protocol: clear -> attest cold -> run. Warm protocol
    (snapshot_path set): clear -> restore the warmup snapshot -> attest
    (IDENTICAL_WARM by construction) -> run."""
    print(f"\n=== ARM {label} ===")
    print(f"  cleared {_clear(args.instance)} row(s)")
    if snapshot_path:
        _snapshot("restore", args.instance, snapshot_path, apply=True)
    fp = _attest(args.instance)
    print(f"  start: {fp['total_rows']} steering row(s), cold={is_cold(fp)}")
    if snapshot_path is None and not is_cold(fp):
        print("  WARNING: arm did not start cold — the comparison will be flagged")
    _apply(args.doc, settings)
    bid, status = _launch_and_wait(args.instance, args.start, args.end,
                                   args.cash, args.granularity)
    return {"label": label, "bid": bid, "status": status, "fingerprint": fp}


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--instance", required=True)
    p.add_argument("--doc", type=int)
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--cash", type=float, default=6000.0)
    p.add_argument("--granularity", default="3600")
    p.add_argument("--control", action="append", default=[], metavar="KEY=VALUE")
    p.add_argument("--treatment", action="append", default=[], metavar="KEY=VALUE")
    p.add_argument("--keep-bar-snapshots", action="store_true", help=(
        "Keep per-bar LLM-crash rewind snapshots ON during the arms (the old "
        "behaviour, ~19min slower per run). Default: off for both arms — a "
        "deterministic cached run is cheaper to relaunch than to rewind."))
    p.add_argument("--snapshot", metavar="PATH", help=(
        "Warm protocol without re-running the warmup: start BOTH arms from "
        "this existing snapshot file (produced by an earlier --warmup-start "
        "run's export). The snapshot is a COMMON starting state, so pair "
        "comparability holds regardless of which code version built it."))
    p.add_argument("--warmup-start", metavar="DATE", help=(
        "Warm-but-clean protocol: run ONE warmup backtest from this date to "
        "--start under the doc's base config, snapshot the accumulated state, "
        "and start BOTH arms from that identical snapshot. Fixes the cold "
        "protocol's stripped discovery pool (cold runs understate the "
        "strategy) without cross-run contamination — every row in the pool "
        "was written by data from before the measurement window."))
    a = p.parse_args(argv)
    if (a.control or a.treatment) and not a.doc:
        p.error("--doc is required when --control/--treatment settings are given")

    snapshot_path = None
    if a.snapshot:
        if a.warmup_start:
            p.error("--snapshot and --warmup-start are mutually exclusive")
        if not Path(a.snapshot).is_file():
            p.error(f"snapshot file not found: {a.snapshot}")
        snapshot_path = a.snapshot
        print(f"=== WARM (reusing snapshot {a.snapshot}) ===")
    if a.warmup_start:
        print(f"=== WARMUP {a.warmup_start} -> {a.start} (base config) ===")
        print(f"  cleared {_clear(a.instance)} row(s)")
        fp0 = _attest(a.instance)
        if not is_cold(fp0):
            raise SystemExit("warmup must start cold — clear-state failed?")
        _launch_and_wait(a.instance, a.warmup_start, a.start,
                         a.cash, a.granularity)
        snapshot_path = f"/tmp/_pair_warm_{a.instance}_{a.start}.state.json"
        _snapshot("export", a.instance, snapshot_path)

    # 2026-08-22: per-bar rewind snapshots cost ~19min of a 52min run and
    # protect against a failure a cached deterministic run cannot have; a
    # crashed arm is relaunched byte-identically for $0. OFF for BOTH arms
    # (identical, so comparability is untouched); --keep-bar-snapshots
    # restores the old behaviour.
    if not a.keep_bar_snapshots:
        a.control = list(a.control) + ["backtest_bar_snapshot_enabled=false"]
        a.treatment = list(a.treatment) + ["backtest_bar_snapshot_enabled=false"]

    ctl = _run_arm("CONTROL", a, a.control, snapshot_path=snapshot_path)
    trt = _run_arm("TREATMENT", a, a.treatment, snapshot_path=snapshot_path)

    print("\n" + "=" * 72)
    # Warm protocol: IDENTICAL_WARM is by construction (same restored
    # snapshot), not the coincidence the cold-only rule guards against.
    start_verdict = compare_arm_starts(ctl["fingerprint"], trt["fingerprint"],
                                       require_cold=(snapshot_path is None))
    print(f"START STATE : {start_verdict['verdict']} — {start_verdict['reason']}")

    ctext, ttext = _log_text(ctl["bid"]), _log_text(trt["bid"])
    pair = assess_pair(
        {"symbols": traded_symbols(ctext), "return_pct": _return_pct(ctext)},
        {"symbols": traded_symbols(ttext), "return_pct": _return_pct(ttext)})
    print(f"BOOK OVERLAP: {pair['overlap']:.0%}  shared={pair['shared']}")

    lag = lag_compare(lag_measure(ctext), lag_measure(ttext))
    print(f"ENTRY LAG   : control median={lag['control'].get('median_days')} d "
          f"max={lag['control'].get('max_days')} d  |  treatment "
          f"median={lag['treatment'].get('median_days')} d "
          f"max={lag['treatment'].get('max_days')} d")

    print("-" * 72)
    ok = start_verdict["verdict"].startswith("IDENTICAL") and pair["verdict"] != "VOID"
    if ok:
        print(f"VERDICT: {pair['verdict']} — {pair['reason']}")
    else:
        print("VERDICT: VOID — arms are not comparable; the return delta is NOT reported.")
        print(f"         start: {start_verdict['verdict']}; books: {pair['reason']}")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
