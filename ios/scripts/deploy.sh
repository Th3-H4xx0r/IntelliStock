#!/usr/bin/env bash
#
# deploy.sh — build & install the native IntelliStock iOS app (Release) to a
# connected iPhone, one command, no Xcode UI. The native successor of
# mobile_flutter_depricated/scripts/deploy.sh (iOS path).
#
# Usage:
#   ios/scripts/deploy.sh                          # auto-detect the iPhone
#   ios/scripts/deploy.sh 1                        # same ("1"/"ios" kept from the Flutter script)
#   IOS_DEVICE_ID=<udid> ios/scripts/deploy.sh     # force a specific device
#   CONFIGURATION=Debug ios/scripts/deploy.sh      # Debug build (APNs sandbox)
#
# Prereqs (one-time): Xcode 27, `brew install xcodegen`; iPhone connected by
# USB (or paired over Wi-Fi), UNLOCKED, Developer Mode on. Signing is
# automatic (team VY5CNF8734).
#
# Installs over the Flutter build: same bundle ID, so the keychain session,
# server URL, lock settings and home-screen widget carry over.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IOS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$IOS_DIR"

case "${1:-ios}" in
  1|ios|iOS|IOS) ;;
  *) echo "✗ This script only deploys iOS (got '$1'). Android left with the Flutter app."; exit 1 ;;
esac

CONFIGURATION="${CONFIGURATION:-Release}"
DERIVED="$IOS_DIR/build/deploy"
BUNDLE_ID="dev.pkrishna.intellistockMobile"

echo "▸ Detecting iPhone…"
# Read devicectl's JSON rather than grepping a UDID pattern: newer iPhones use
# the 8-16 form (00008150-001130180232401C) that an 8-4-4-4-12 regex misses.
detect_iphone() {
  local json
  json="$(mktemp -t devicectl)"
  xcrun devicectl list devices --json-output "$json" >/dev/null 2>&1 || { rm -f "$json"; return 0; }
  python3 - "$json" <<'PY'
import json, sys
devices = json.load(open(sys.argv[1])).get("result", {}).get("devices", [])
for d in devices:
    hw, cp = d.get("hardwareProperties", {}), d.get("connectionProperties", {})
    if hw.get("reality") != "physical" or hw.get("deviceType") != "iPhone":
        continue
    if cp.get("pairingState") not in (None, "paired"):
        continue
    print(hw.get("udid") or d.get("identifier", ""))
    break
PY
  rm -f "$json"
}
UDID="${IOS_DEVICE_ID:-$(detect_iphone)}"
[ -n "$UDID" ] || { echo "✗ No iPhone detected. Connect & unlock it (Developer Mode on)."; exit 1; }
echo "  iPhone=$UDID"

if command -v xcodegen >/dev/null 2>&1; then
  echo "▸ Generating the Xcode project (xcodegen)…"
  xcodegen generate --quiet
elif [ ! -d IntelliStock.xcodeproj ]; then
  echo "✗ IntelliStock.xcodeproj missing and xcodegen not installed (brew install xcodegen)."; exit 1
fi

echo "▸ Building ($CONFIGURATION, xcodebuild)…"
LOG="$IOS_DIR/build/deploy.log"
mkdir -p "$IOS_DIR/build"
if ! xcodebuild -project IntelliStock.xcodeproj -scheme IntelliStock \
      -configuration "$CONFIGURATION" -destination "generic/platform=iOS" \
      -derivedDataPath "$DERIVED" -allowProvisioningUpdates build >"$LOG" 2>&1; then
  grep -E "error:|BUILD FAILED" "$LOG" | sort -u | head -20
  echo "✗ iOS build failed — full log: $LOG"; exit 1
fi
APP="$DERIVED/Build/Products/$CONFIGURATION-iphoneos/IntelliStock.app"
[ -d "$APP" ] || { echo "✗ iOS build failed (no IntelliStock.app) — full log: $LOG"; exit 1; }

echo "▸ Installing to iPhone…"
xcrun devicectl device install app --device "$UDID" "$APP"
echo "✓ Installed ($BUNDLE_ID). Launch IntelliStock from the home screen."
