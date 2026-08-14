#!/usr/bin/env python3
"""Score a paired run against prereg-unseal-the-book-2026-08-14b.md.

    python3 scripts/read_unseal_pair.py <control_id> <treatment_id>

Why a script: the prereg fixes six endpoints, and the failure mode this project
keeps hitting is reading the one that moved. This computes all six for both arms
from the logs, in one pass, and refuses to report a benchmark it cannot support.

It reads LOGS, never config. A config key proves what was requested; only the log
proves what ran, and five levers have shipped inert here.
"""
from __future__ import annotations

import argparse
import collections
import re
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "scripts"))

RX = {
    # endpoint 1 — did the levers fire at all
    "alpha_headroom": re.compile(r"ALPHA HEADROOM: withheld \$([\d,\.]+)"),
    "cap_below_floor": re.compile(r"satellite_cap_below_floor"),
    "breach": re.compile(r"max_positions BREACH\] current=(\d+) > max=(\d+)"),
    # endpoint 2 — conversion
    "fill_buy": re.compile(
        r"FILL BUY (\S+) qty=([\d\.]+) cumulative=[\d\.]+ price=([\d\.]+)"
        r".*quote=(\d{4}-\d{2}-\d{2})"),
    "skip_buy": re.compile(r"SKIP BUY (\S+) [—-] (.*)"),
    "alloc": re.compile(r"\(allocated \$([\d,\.]+)\)"),
    # endpoint 4 — turnover
    "turnover": re.compile(r"TURNOVER BUDGET (?:BINDING|BLOCK)[^\d]*(\d+)% of NAV"),
    "convert": re.compile(r"V31\.7|CONVERT"),
    # endpoint 5 — benchmark
    "quote": re.compile(r"BENCHMARK QUOTE: (\S+) (\d{4}-\d{2}-\d{2}) \d{2}:\d{2} ([\d\.]+)"),
    # portfolio
    "final": re.compile(r"Final (?:portfolio )?value[^\d]*([\d,\.]+)"),
    "pnl": re.compile(r"P&L[^-\d]*(-?[\d,\.]+)\s*\(([-+]?[\d\.]+)%\)"),
    # funnel — a >=30% mover that received a buy intent
    "mover": re.compile(r"Discovered stock \(momentum\): (\S+) \(20d=([-+][\d\.]+)%, "
                        r"60d=([-+][\d\.]+)%\)"),
    # NOTE the timestamp contains colons, so a `[^:]*` between the date and
    # the verb never matches. That bug reported a 0.0% funnel for two runs
    # whose real funnel is ~18%, i.e. it would have shown any lever as a
    # total failure. Caught by smoke-testing the reader on two FINISHED runs
    # before trusting it on a live one.
    "intent": re.compile(
        r"\[BROKER\] (\S+) @ \d{4}-\d{2}-\d{2} [\d:]+ \(\$[\d\.]+\): buy "),
}


def _num(s):
    return float(str(s).replace(",", ""))


def _pull(bt_id, out):
    if out.exists() and out.stat().st_size > 0:
        return out
    subprocess.run([sys.executable, str(_REPO / "scripts" / "pull_backtest_logs.py"),
                    str(bt_id), "--out", str(out)],
                   check=False, capture_output=True, timeout=1800)
    return out


def score(bt_id, path):
    lines = path.read_text(errors="replace").splitlines()
    out = {"id": bt_id, "lines": len(lines)}

    headroom = [_num(m.group(1)) for ln in lines
                if (m := RX["alpha_headroom"].search(ln))]
    out["headroom_fires"] = len(headroom)
    out["headroom_usd"] = sum(headroom)
    out["cap_below_floor"] = sum(1 for ln in lines if RX["cap_below_floor"].search(ln))
    out["breach_bars"] = sum(1 for ln in lines if RX["breach"].search(ln))

    fills = [(m.group(1), _num(m.group(2)), _num(m.group(3)), m.group(4))
             for ln in lines if (m := RX["fill_buy"].search(ln))]
    out["fills"] = len(fills)
    out["alpha_fills"] = sum(1 for f in fills if f[0] not in ("SPY", "SQQQ"))
    out["sndk_fills"] = [(f[3], f[2], round(f[1] * f[2], 2))
                         for f in fills if f[0] == "SNDK"]

    skips = [(m.group(1), m.group(2)) for ln in lines
             if (m := RX["skip_buy"].search(ln))]
    out["skips"] = len(skips)
    out["skip_notional"] = sum(
        _num(a.group(1)) for _s, rest in skips
        if (a := RX["alloc"].search(rest)))
    out["skips_inflight"] = sum(1 for _s, rest in skips if "in flight" in rest)

    turn = [int(m.group(1)) for ln in lines if (m := RX["turnover"].search(ln))]
    out["turnover_readings"] = len(turn)
    out["turnover_max_pct"] = max(turn) if turn else None
    out["turnover_last_pct"] = turn[-1] if turn else None
    out["convert_lines"] = sum(1 for ln in lines if RX["convert"].search(ln))

    quotes = {}
    for ln in lines:
        if (m := RX["quote"].search(ln)) and m.group(1) == "SPY":
            quotes[m.group(2)] = float(m.group(3))
    pts = sorted(quotes.items())
    out["spy_points"] = len(pts)
    out["spy_span"] = (pts[0][0], pts[-1][0]) if pts else None
    out["spy_return_pct"] = (
        100.0 * (pts[-1][1] - pts[0][1]) / pts[0][1] if len(pts) >= 3 else None)

    movers = {}
    for ln in lines:
        if (m := RX["mover"].search(ln)):
            movers[m.group(1)] = max(abs(float(m.group(2))), abs(float(m.group(3))))
    big = {s for s, mv in movers.items() if mv >= 30.0}
    intents = {m.group(1) for ln in lines if (m := RX["intent"].search(ln))}
    out["movers_30pct"] = len(big)
    out["movers_with_buy_intent"] = len(big & intents)
    out["funnel_pct"] = (100.0 * len(big & intents) / len(big)) if big else None

    # The summary block is ~50 lines from the end and reads
    #   Profit & Loss:     +$366.10 (+6.10%)
    # There is no "Run P&L" line; searching for one returned None for every run.
    tail = "\n".join(lines[-400:])
    m = re.search(r"Profit & Loss:\s*[-+]?\$([\d,\.]+)\s*\(([-+]?[\d\.]+)%\)", tail)
    out["return_pct"] = float(m.group(2)) if m else None
    m = re.search(r"Final Value:\s*\$([\d,\.]+)", tail)
    out["final_value"] = _num(m.group(1)) if m else None
    # A run that never printed the summary did not finish. Say so loudly: a
    # stopped run's P&L is meaningless and has been published as real twice.
    out["finished"] = out["return_pct"] is not None
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("control")
    ap.add_argument("treatment")
    ap.add_argument("--dir", default="/private/tmp/claude-501/"
                    "-Users-pranavkrishna-PranavFiles-coding-projects-IntelliStock/"
                    "df51be96-6c29-43b3-8917-1756634b59e5/scratchpad")
    a = ap.parse_args(argv)
    d = Path(a.dir)

    arms = {}
    for label, bt in (("control", a.control), ("treatment", a.treatment)):
        arms[label] = score(bt, _pull(bt, d / f"bt{bt}.log"))

    c, t = arms["control"], arms["treatment"]
    print(f"{'endpoint':38s} {'control':>16s} {'treatment':>16s}")
    print("-" * 72)

    def row(name, key, fmt="{}"):
        cv = c.get(key)
        tv = t.get(key)
        print(f"{name:38s} {fmt.format(cv) if cv is not None else '-':>16s} "
              f"{fmt.format(tv) if tv is not None else '-':>16s}")

    print("\n[1] DID THE LEVERS FIRE  (log, not config)")
    row("  ALPHA HEADROOM lines", "headroom_fires")
    row("  ...cash withheld", "headroom_usd", "${:,.0f}")
    row("  satellite_cap_below_floor", "cap_below_floor")
    row("  max_positions BREACH lines", "breach_bars")

    for label, arm in arms.items():
        if not arm["finished"]:
            print(f"\n!! {label} (bt {arm['id']}) has NO PORTFOLIO SUMMARY — it "
                  "did not finish. Its P&L is meaningless; a truncated run also "
                  "gives FALSE NEGATIVES on every endpoint below.")

    print("\n[2] CONVERSION")
    row("  alpha FILL BUY", "alpha_fills")
    row("  SKIP BUY", "skips")
    row("  ...of which in-flight", "skips_inflight")
    row("  refused notional", "skip_notional", "${:,.0f}")
    print(f"  SNDK fills  control={c['sndk_fills']}")
    print(f"              treatment={t['sndk_fills']}")

    print("\n[3] FUNNEL  (>=30% movers receiving a buy intent; 17-20% baseline)")
    row("  movers >=30%", "movers_30pct")
    row("  ...with buy intent", "movers_with_buy_intent")
    row("  funnel", "funnel_pct", "{:.1f}%")

    print("\n[4] TURNOVER  (any rise is disqualifying)")
    row("  max reading", "turnover_max_pct", "{}%")
    row("  last reading", "turnover_last_pct", "{}%")
    row("  CONVERT lines (confound)", "convert_lines")

    print("\n[5] RETURN vs SPY  (tick quotes only)")
    row("  strategy return", "return_pct", "{:+.2f}%")
    row("  SPY points", "spy_points")
    print(f"{'  SPY span':38s} {str(c['spy_span']):>16s} {str(t['spy_span']):>16s}")
    row("  SPY return", "spy_return_pct", "{:+.2f}%")
    for label, arm in arms.items():
        if arm["spy_points"] < 3:
            print(f"  REFUSING a benchmark for {label}: "
                  f"{arm['spy_points']} SPY points is not a benchmark.")
        elif arm["return_pct"] is not None and arm["spy_return_pct"] is not None:
            d_pp = arm["return_pct"] - arm["spy_return_pct"]
            verdict = ("beat" if d_pp > 10 else "LOSES" if d_pp < -10 else "NOISE")
            print(f"  {label:9s} vs SPY: {d_pp:+.2f}pp -> {verdict} "
                  f"(10pp floor; CHECK THE SPAN COVERS THE WINDOW)")

    print("\nRead the prereg before concluding. A single-window return difference "
          "under ~10pp is not evidence of anything.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
