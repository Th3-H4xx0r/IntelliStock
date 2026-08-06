#!/usr/bin/env python3
"""Is the deployed backend actually running the commit I pushed?

Twice this project has pushed a fix, redeployed, run a backtest, and drawn
conclusions from code that was never live. Both times it cost a paid run to
find out. This asks the same question for free:

    python3 scripts/check_deployed_code.py            # vs working tree
    python3 scripts/check_deployed_code.py HEAD~3     # vs any commit

It hashes the trade-deciding source files locally and compares them against
`GET /health`'s `code` block, which hashes the same files as they exist inside
the container. Exit 0 = deployed code matches. Exit 1 = it does not; do not
trust any backtest you run until you redeploy.
"""
import hashlib
import json
import subprocess
import sys
import urllib.request

API = "https://intellistock-api.pkrishna.dev"

# Repo-relative paths. The container sees these without the `backend/` prefix
# (the image is built with `context: ./backend`), which is why the API keys its
# response by basename.
FILES = (
    "backend/broker.py",
    "backend/strategies/graph_nexus_analysis.py",
    "backend/core_sleeve.py",
    "backend/api/main.py",
)


def local_hashes(ref=None):
    out = {}
    for rel in FILES:
        if ref:
            blob = subprocess.run(["git", "show", f"{ref}:{rel}"],
                                  capture_output=True, check=True).stdout
        else:
            with open(rel, "rb") as fh:
                blob = fh.read()
        out[rel.split("/")[-1]] = hashlib.sha256(blob).hexdigest()[:12]
    return out


def main():
    ref = sys.argv[1] if len(sys.argv) > 1 else None
    mine = local_hashes(ref)

    # The edge in front of the API 403s urllib's default User-Agent.
    req = urllib.request.Request(
        f"{API}/health", headers={"User-Agent": "intellistock-deploy-check/1.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        health = json.loads(resp.read())
    theirs = health.get("code")
    if not theirs:
        print("FAIL: /health has no `code` block — the API itself is running "
              "code older than this check. Redeploy the backend first.")
        return 1

    label = ref or "working tree"
    print(f"comparing {label}  vs  {API} (container {health.get('host')})\n")
    bad = 0
    for name, want in mine.items():
        got = theirs.get(name, "<missing>")
        mark = "ok  " if got == want else "DIFF"
        if got != want:
            bad += 1
        print(f"  {mark}  {name:32} local {want}   deployed {got}")

    if bad:
        print(f"\n{bad} file(s) differ. The deployed backend is NOT running {label}.")
        print("Do not draw conclusions from a backtest until this is clean.")
        return 1
    print(f"\nAll {len(mine)} files match. Deployed backend is running {label}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
