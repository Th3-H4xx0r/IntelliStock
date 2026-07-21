#!/usr/bin/env python3
"""Run the REAL IntelliStock backtest engine locally with a cheap/free Claude
LLM and a persistent local LLM cache — instead of the metered SaaS backtest.

It shells out to `broker.py backtest` (the real loop + PortfolioEmulator + real
Neo4j/RethinkDB) via scripts/harness/run_broker_cached.py, injecting only:
  * a cheap LLM   — GRAPH_NEXUS_LLM_PROVIDER/MODEL/REASONING_EFFORT (default
                    claude-cli + haiku + medium; $0 metered, structured output
                    via the local `claude` binary)
  * determinism   — PYTHONHASHSEED=0 + BACKTEST_SEED (matches the engine)
  * a local cache — NEXUS_LOCAL_LLM_CACHE_DIR (project .backtest_llm_cache/)

Nothing in the engine is reimplemented, so fidelity is maximal; the only
difference from an official run is the sentiment MODEL (nemotron -> Claude),
which is cache-frozen. Max fidelity needs the real Neo4j graph + RethinkDB
(behind Tailscale); the runner preflights reachability and exits 3 if down.

Usage:
  # single window
  python scripts/local_backtest.py alpaca-main 2026-03-30 2026-04-27
  # options
  python scripts/local_backtest.py alpaca-main 2026-03-02 2026-03-30 \
      --cash 6000 --granularity 3600 --provider claude-cli --model haiku --effort medium
  # multi-window suite (anti-overfit) / bear guardrail
  python scripts/local_backtest.py --suite
  python scripts/local_backtest.py --assert-bear-unchanged
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_BACKEND = _REPO / "backend"
_HARNESS = _REPO / "scripts" / "harness" / "run_broker_cached.py"
_CACHE_DIR = _REPO / ".backtest_llm_cache"
_WINDOWS = _REPO / "scripts" / "local_backtest_windows.json"
_BEAR_BASELINE = _REPO / "scripts" / "local_backtest_bear_baseline.json"
_OUT_DEFAULT = _REPO / "backtests" / "local"

sys.path.insert(0, str(_REPO / "scripts"))
from pull_backtest_logs import _load_dotenv, _login, _fetch  # noqa: E402

# Preflight exit code distinct from a run failure, so callers/CI can tell
# "infra down, retry later" apart from "the backtest itself errored".
EXIT_INFRA_DOWN = 3


def _env() -> dict:
    _load_dotenv(_REPO)
    return dict(os.environ)


def _resolve_creds(instance: str, env: dict) -> tuple[str, str]:
    """Prefer the instance's linked market-data Alpaca creds (what the engine
    uses — paper accounts 401 on bars/news), else fall back to .env keys."""
    try:
        sys.path.insert(0, str(_BACKEND))
        from engines.backtest_engine import _resolve_data_brokerage_creds, get_conn  # type: ignore
        conn = get_conn()
        try:
            k, s = _resolve_data_brokerage_creds(conn, instance)
        finally:
            try:
                conn.close()
            except Exception:
                pass
        if k and s:
            return k, s
    except Exception as e:
        print(f"[local_backtest] data-brokerage cred resolution unavailable ({e}); using .env keys", file=sys.stderr)
    for kk, sk in (("ALPACA_KEY", "ALPACA_SECRET"), ("APCA_API_KEY_ID", "APCA_API_SECRET_KEY"), ("KEY", "SECRET")):
        k, s = (env.get(kk) or "").strip(), (env.get(sk) or "").strip()
        if k and s:
            return k, s
    return "", ""


def _infra_reachable(env: dict) -> tuple[bool, str]:
    host = (env.get("RETHINKDB_HOST") or "").strip()
    port = int((env.get("RETHINKDB_PORT") or "28015").strip() or "28015")
    if not host:
        return False, "RETHINKDB_HOST not set in .env"
    try:
        socket.create_connection((host, port), timeout=3).close()
        return True, f"RethinkDB {host}:{port} reachable"
    except Exception as e:
        return False, f"RethinkDB {host}:{port} unreachable ({type(e).__name__}) — Tailscale/infra down?"


def _new_backtest_id() -> int:
    # 6-digit int matching the BacktestInstances schema. Derive from the wall
    # clock so concurrent local runs don't collide; not required to be random.
    now = _dt.datetime.now(_dt.timezone.utc)
    return 100000 + (int(now.strftime("%H%M%S")) % 900000)


def _claude_allowlist() -> str:
    """The claude-cli provider validates the binary's realpath BASENAME against
    an allowlist; a versioned install (…/versions/2.1.216) resolves to a
    version-named file that isn't 'claude', so it's rejected. Auto-allowlist
    our own binary's realpath basename via CLAUDE_CLI_ALLOWED_BINARIES (safe:
    it's the operator's own local install, not attacker-supplied)."""
    import shutil
    found = shutil.which("claude")
    if not found:
        return ""
    return os.path.basename(os.path.realpath(found))


def _build_env(base: dict, provider: str, model: str, effort: str, key: str, secret: str) -> dict:
    env = dict(base)
    # Cheap LLM for ALL strategy roles (single resolver fallback).
    env["GRAPH_NEXUS_LLM_PROVIDER"] = provider
    env["GRAPH_NEXUS_LLM_MODEL"] = model
    env["GRAPH_NEXUS_LLM_REASONING_EFFORT"] = effort
    if provider == "claude-cli":
        base_name = _claude_allowlist()
        if base_name and base_name.lower() not in ("claude", "claude.exe"):
            existing = (base.get("CLAUDE_CLI_ALLOWED_BINARIES") or "").strip()
            env["CLAUDE_CLI_ALLOWED_BINARIES"] = f"{existing},{base_name}".strip(",") if existing else base_name
    # Persistent local LLM cache (project dir).
    env["NEXUS_LOCAL_LLM_CACHE_DIR"] = str(_CACHE_DIR)
    # Determinism (matches engine: PYTHONHASHSEED must be set before interp start).
    env.setdefault("PYTHONHASHSEED", "0")
    env.setdefault("BACKTEST_SEED", "0")
    # Faithful infra + creds (same vars the engine forwards to the container).
    env["KEY"] = key
    env["SECRET"] = secret
    env["NEO4J_URI"] = base.get("NEO4J_URI", "bolt://localhost:7687")
    env["NEO4J_USER"] = base.get("NEO4J_USER", "neo4j")
    env["NEO4J_PASSWORD"] = base.get("NEO4J_PASSWORD", "intellistock")
    env["INTELLISTOCK_BACKEND_DIR"] = str(_BACKEND)
    return env


def run_one(instance, start, end, *, granularity="3600", cash=6000.0,
            provider="claude-cli", model="haiku", effort="medium",
            symbols=None, out_dir=None, base_env=None) -> dict:
    base = base_env or _env()
    ok, why = _infra_reachable(base)
    if not ok:
        print(f"[local_backtest] PREFLIGHT FAILED: {why}\n"
              f"  Harness is ready — re-run when infra is up.", file=sys.stderr)
        return {"ok": False, "preflight": why, "exit": EXIT_INFRA_DOWN}
    key, secret = _resolve_creds(instance, base)
    if not key or not secret:
        print("[local_backtest] no Alpaca data creds (instance link or .env) — bars/news will 401", file=sys.stderr)
    bt_id = _new_backtest_id()
    out_dir = Path(out_dir or _OUT_DEFAULT)
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / f"{instance}_{start}_{end}_{bt_id}.log"

    cmd = [sys.executable, str(_HARNESS), instance, "backtest", start, end, str(granularity),
           key, secret] + list(symbols or []) + ["--initial-cash", str(cash), "--backtest-id", str(bt_id)]
    env = _build_env(base, provider, model, effort, key, secret)

    started = _dt.datetime.now(_dt.timezone.utc)
    print(f"[local_backtest] #{bt_id} {instance} {start}->{end} g={granularity} ${cash} "
          f"LLM={provider}/{model}/{effort} -> {log_path}", file=sys.stderr)
    with open(log_path, "w", encoding="utf-8") as logf:
        proc = subprocess.Popen(cmd, cwd=str(_REPO), env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, bufsize=1)
        for line in proc.stdout:  # tee
            sys.stdout.write(line)
            logf.write(line)
        rc = proc.wait()
    dur = (_dt.datetime.now(_dt.timezone.utc) - started).total_seconds()

    result = {"ok": rc == 0, "backtest_id": bt_id, "instance": instance, "start": start,
              "end": end, "granularity": granularity, "cash": cash, "provider": provider,
              "model": model, "effort": effort, "return_code": rc, "duration_sec": round(dur, 1),
              "log": str(log_path)}
    summary = _fetch_summary(bt_id, base)
    if summary:
        result.update({"pnl": summary.get("pnl"), "pnl_percent": summary.get("pnl_percent"),
                       "pnl_per_stock": summary.get("pnl_per_stock")})
    res_path = log_path.with_suffix(".result.json")
    res_path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    pnl = result.get("pnl_percent")
    print(f"[local_backtest] #{bt_id} done rc={rc} in {dur:.0f}s "
          f"pnl%={pnl if pnl is not None else '—'} -> {res_path}", file=sys.stderr)
    return result


def _fetch_summary(bt_id: int, env: dict) -> dict | None:
    api = (env.get("INTELLISTOCK_API_URL") or "").rstrip("/")
    if not api:
        return None
    try:
        tok = (env.get("INTELLISTOCK_API_TOKEN") or "").strip() or _login(
            api, env.get("INTELLISTOCK_USERNAME") or env.get("DEFAULT_ADMIN_USERNAME") or "admin",
            env.get("INTELLISTOCK_PASSWORD") or env.get("DEFAULT_ADMIN_PASSWORD") or "")
        st, body = _fetch(api, tok, f"/backtests/{bt_id}/summary", allow_404=True)
        return body if isinstance(body, dict) else None
    except SystemExit:
        return None
    except Exception:
        return None


def _load_windows() -> dict:
    if _WINDOWS.is_file():
        return json.loads(_WINDOWS.read_text())
    return {"windows": []}


def run_suite(regime_filter=None, **kw) -> list[dict]:
    base = _env()
    wins = _load_windows().get("windows", [])
    if regime_filter:
        wins = [w for w in wins if w.get("regime") == regime_filter]
    results = []
    for w in wins:
        r = run_one(w.get("instance", "alpaca-main"), w["start"], w["end"],
                    granularity=str(w.get("granularity", 3600)), cash=float(w.get("cash", 6000)),
                    base_env=base, **kw)
        r["window"] = w.get("name", f"{w['start']}_{w['end']}")
        r["regime"] = w.get("regime")
        results.append(r)
    _print_suite_table(results)
    return results


def _print_suite_table(results: list[dict]) -> None:
    print("\n=== SUITE RESULTS ===")
    print(f"{'window':28} {'regime':6} {'pnl%':>8} {'dur':>6}")
    bulls, bears = [], []
    for r in results:
        pnl = r.get("pnl_percent")
        print(f"{str(r.get('window'))[:28]:28} {str(r.get('regime')):6} "
              f"{(f'{pnl:+.2f}' if isinstance(pnl,(int,float)) else '—'):>8} {r.get('duration_sec','?'):>6}")
        if isinstance(pnl, (int, float)):
            (bulls if r.get("regime") == "bull" else bears if r.get("regime") == "bear" else []).append(pnl)
    if bulls:
        print(f"bull mean: {sum(bulls)/len(bulls):+.2f}%  ({len(bulls)} windows)")
    if bears:
        print(f"bear mean: {sum(bears)/len(bears):+.2f}%  ({len(bears)} windows)")


def assert_bear_unchanged(tol=0.6, **kw) -> int:
    """Re-run the bear suite; fail if any window moved beyond `tol` pp vs the
    saved baseline (tolerance covers the engine's own ~5% overlay-LLM noise)."""
    results = run_suite(regime_filter="bear", **kw)
    if not _BEAR_BASELINE.is_file():
        _BEAR_BASELINE.write_text(json.dumps(
            {r["window"]: r.get("pnl_percent") for r in results}, indent=2), encoding="utf-8")
        print(f"[local_backtest] wrote bear baseline -> {_BEAR_BASELINE} (no prior baseline to check)")
        return 0
    baseline = json.loads(_BEAR_BASELINE.read_text())
    breaches = []
    for r in results:
        base_pnl, now_pnl = baseline.get(r["window"]), r.get("pnl_percent")
        if isinstance(base_pnl, (int, float)) and isinstance(now_pnl, (int, float)):
            if abs(now_pnl - base_pnl) > tol:
                breaches.append((r["window"], base_pnl, now_pnl))
    if breaches:
        print("\n[local_backtest] BEAR CHANGED (bull edit degraded the bear!):")
        for w, b, n in breaches:
            print(f"  {w}: baseline {b:+.2f}% -> now {n:+.2f}% (Δ{n-b:+.2f}pp > {tol}pp)")
        return 1
    print(f"\n[local_backtest] bear unchanged within ±{tol}pp across {len(results)} window(s) ✓")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("instance", nargs="?", help="instance id (e.g. alpaca-main)")
    p.add_argument("start", nargs="?", help="YYYY-MM-DD")
    p.add_argument("end", nargs="?", help="YYYY-MM-DD")
    p.add_argument("--granularity", default="3600")
    p.add_argument("--cash", type=float, default=6000.0)
    p.add_argument("--provider", default="claude-cli")
    p.add_argument("--model", default="haiku")
    p.add_argument("--effort", default="medium")
    p.add_argument("--symbols", nargs="*", default=[])
    p.add_argument("--out", default=None)
    p.add_argument("--suite", action="store_true", help="run the multi-window suite (anti-overfit)")
    p.add_argument("--assert-bear-unchanged", action="store_true",
                   help="run the bear suite and fail if it moved vs the saved baseline")
    a = p.parse_args(argv)

    common = dict(provider=a.provider, model=a.model, effort=a.effort)
    if a.assert_bear_unchanged:
        return assert_bear_unchanged(**common)
    if a.suite:
        run_suite(**common)
        return 0
    if not (a.instance and a.start and a.end):
        p.error("need instance start end (or --suite / --assert-bear-unchanged)")
    r = run_one(a.instance, a.start, a.end, granularity=a.granularity, cash=a.cash,
                symbols=a.symbols, out_dir=a.out, **common)
    if not r.get("ok") and r.get("exit") == EXIT_INFRA_DOWN:
        return EXIT_INFRA_DOWN
    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
