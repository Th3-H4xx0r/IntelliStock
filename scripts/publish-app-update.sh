#!/usr/bin/env bash
#
# publish-app-update.sh — upload an iPhone build to the IntelliStock API so the
# app pops up "Update Available". Ported from JarvisCopilot's
# scripts/publish-app-update.sh, over HTTPS instead of ssh.
#
#   scripts/publish-app-update.sh --file ios/build/release/export/IntelliStock.ipa --notes "• line one"
#   scripts/publish-app-update.sh --rollback      # the previous build becomes current again
#
# The build number is read from the IPA's own Info.plist. The API is
# INTELLISTOCK_API_URL; credentials are the ones scripts/_api.py uses
# (INTELLISTOCK_API_TOKEN, or INTELLISTOCK_API_USERNAME / _PASSWORD — or the
# deprecated DEFAULT_ADMIN_* pair — from the repo .env). The token never
# reaches argv, a URL or the terminal.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
file="" notes="" rollback=0 platform="ios"
while [ $# -gt 0 ]; do
  case "$1" in
    --file) [ $# -ge 2 ] || { echo "--file needs a value" >&2; exit 2; }; file="$2"; shift 2 ;;
    --notes) [ $# -ge 2 ] || { echo "--notes needs a value" >&2; exit 2; }; notes="$2"; shift 2 ;;
    --platform) [ $# -ge 2 ] || { echo "--platform needs a value" >&2; exit 2; }; platform="$2"; shift 2 ;;
    --rollback) rollback=1; shift ;;
    -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
[ "$platform" = ios ] || { echo "--platform must be ios" >&2; exit 2; }
if [ "$rollback" = 0 ]; then
  [ -f "$file" ] || { echo "--file must be an existing .ipa" >&2; exit 2; }
fi
commit="$(git -C "$SCRIPT_DIR/.." rev-parse --short HEAD 2>/dev/null || true)"

exec python3 - "$SCRIPT_DIR" "$platform" "$rollback" "$file" "$notes" "$commit" <<'PY'
import http.client
import json
import os
import plistlib
import re
import sys
import zipfile
from urllib.parse import urlencode, urlsplit

scripts_dir, platform, rollback, path, notes, commit = sys.argv[1:7]
notes = notes.strip()[:2000]  # the server keeps 2000 characters; this keeps the query string short
sys.path.insert(0, scripts_dir)
import _api  # noqa: E402  (loads the .env, knows how to log in)

API = _api.API
if not API.startswith(("https://", "http://")):
    sys.exit("INTELLISTOCK_API_URL is not set (repo .env or the environment).")
if API.startswith("http://"):
    print("warning: INTELLISTOCK_API_URL is plain http; the phone's install links need https.", file=sys.stderr)
_base = urlsplit(API)
CHUNK = 1024 * 1024


def connection():
    cls = http.client.HTTPSConnection if _base.scheme == "https" else http.client.HTTPConnection
    return cls(_base.hostname, _base.port, timeout=900)


def request(method, route, query=None, body_file=None, size=0):
    """One call with the bearer token in a header. Returns (status, parsed body)."""
    target = (_base.path.rstrip("/") + route) + ("?" + urlencode(query) if query else "")
    conn = connection()
    conn.putrequest(method, target)
    conn.putheader("Authorization", "Bearer " + _api.auth())
    conn.putheader("Accept", "application/json")
    conn.putheader("User-Agent", "intellistock-publish-app-update/1.0")
    conn.putheader("Content-Length", str(size))
    if body_file is not None:
        conn.putheader("Content-Type", "application/octet-stream")
    conn.endheaders()
    if body_file is not None:
        sent = 0
        try:
            while True:
                chunk = body_file.read(CHUNK)
                if not chunk:
                    break
                conn.send(chunk)
                sent += len(chunk)
                print(f"\r    {sent * 100 // size:3d}%  {sent / 1048576:6.1f} / {size / 1048576:.1f} MB",
                      end="", file=sys.stderr, flush=True)
        except OSError:
            pass  # the server answered early and closed; its answer says why
        print(file=sys.stderr)
    resp = conn.getresponse()
    raw = resp.read().decode("utf-8", "replace")
    try:
        return resp.status, json.loads(raw)
    except ValueError:
        return resp.status, raw


def fail(status, body):
    detail = body.get("detail") if isinstance(body, dict) else body
    sys.exit(f"✗ HTTP {status}: {detail}")


if rollback == "1":
    # Stops phones still on the older build from being offered the bad one; a
    # phone that already installed it needs a new, higher build to get off it.
    status, body = request("POST", "/app-updates/rollback", {"platform": platform})
    if status != 200:
        fail(status, body)
    rec = body["build"]
    print(f"✓ Rolled back: build {rec['build']} is current again")
    sys.exit(0)

with zipfile.ZipFile(path) as zf:
    name = next((n for n in zf.namelist() if re.match(r"^Payload/[^/]+\.app/Info\.plist$", n)), None)
    if name is None:
        sys.exit("✗ not an iOS app archive (no Payload/*.app/Info.plist)")
    info = plistlib.loads(zf.read(name))
build = str(info.get("CFBundleVersion") or "")
if not build.isdigit():
    sys.exit(f"✗ the IPA's CFBundleVersion is {build!r}; release.sh stamps a numeric one")
size = os.path.getsize(path)

# Say why before streaming tens of MB into a request the server will refuse.
status, body = request("GET", "/app-updates/latest", {"platform": platform, "build": build})
if status != 200:
    fail(status, body)
current = int(body.get("build") or 0)
if current >= int(build):
    sys.exit(f"✗ build {build} is not newer than the current build {current}")

print(f"==> Publishing {platform} build {build} ({size / 1048576:.1f} MB) to {_base.hostname}", flush=True)
with open(path, "rb") as fh:
    status, body = request("POST", "/app-updates/upload",
                           {"platform": platform, "build": build, "commit": commit, "notes": notes},
                           body_file=fh, size=size)
if status != 200:
    fail(status, body)
rec = body["build"]
print(f"✓ Published build {rec['build']} (version {rec.get('version') or '?'}, sha256 {rec['sha256'][:12]}…)")
print("  The app offers it on its next launch or return to the foreground; registered iPhones get a push.")
PY
