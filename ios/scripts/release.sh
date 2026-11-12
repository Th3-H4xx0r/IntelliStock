#!/usr/bin/env bash
#
# release.sh — build the iPhone app and publish it as an over-the-air update:
# the app pops up "Update Available" (what's new, Install Now / Later) and a
# closed app gets a push. Ported from JarvisCopilot's ios_app/scripts/release.sh.
#
# Usage:
#   ios/scripts/release.sh --notes "• Order history on the dashboard
#   • Faster charts"
#   ios/scripts/release.sh --no-publish          # just archive + export the IPA
#   BUILD_NUMBER=263170900 ios/scripts/release.sh --notes "…"
#
# Every build gets a build number (UTC yyDDDHHMM, e.g. 263161830) so the app can
# tell an older build from a newer one; BUILD_NUMBER=N overrides it. It must be
# above the last published build. Builds this checkout as it stands,
# uncommitted edits included.
#
# The IPA is development-signed (team VY5CNF8734, automatic signing), so it
# installs only on devices registered to the team — -allowProvisioningDeviceRegistration
# registers a connected one. Publishing uploads to the IntelliStock API over HTTPS
# (scripts/publish-app-update.sh).

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IOS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_DIR="$(cd "$IOS_DIR/.." && pwd)"
cd "$IOS_DIR"

TEAM="${TEAM:-VY5CNF8734}"
OUT="${OUT:-$IOS_DIR/build/release}"
BUILD_NUMBER="${BUILD_NUMBER:-$(date -u +%y%j%H%M)}"
notes="" publish=1
while [ $# -gt 0 ]; do
  case "$1" in
    --notes) [ $# -ge 2 ] || { echo "--notes needs a value" >&2; exit 2; }; notes="$2"; shift 2 ;;
    --no-publish) publish=0; shift ;;
    -h|--help) sed -n '2,22p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
case "$BUILD_NUMBER" in
  ''|*[!0-9]*) echo "BUILD_NUMBER must be digits (got '$BUILD_NUMBER')" >&2; exit 2 ;;
esac

# The project is generated from project.yml. Folders are synced, so only an
# edit to project.yml needs a regenerate; --use-cache skips it when the spec
# hasn't changed since the last generate on this Mac.
if command -v xcodegen >/dev/null 2>&1; then
  xcodegen generate --use-cache --quiet
elif [ ! -d IntelliStock.xcodeproj ]; then
  echo "✗ IntelliStock.xcodeproj missing and xcodegen not installed (brew install xcodegen)." >&2; exit 1
fi

mkdir -p "$OUT"
ARCHIVE="$OUT/IntelliStock.xcarchive"
echo "==> Archiving build $BUILD_NUMBER"
rm -rf "$ARCHIVE"
# CURRENT_PROJECT_VERSION on the command line stamps every target, so the
# widget extension's CFBundleVersion matches the app's.
xcodebuild -project IntelliStock.xcodeproj -scheme IntelliStock -configuration Release \
  -destination 'generic/platform=iOS' -archivePath "$ARCHIVE" \
  -allowProvisioningUpdates -allowProvisioningDeviceRegistration \
  DEVELOPMENT_TEAM="$TEAM" CODE_SIGN_STYLE=Automatic CURRENT_PROJECT_VERSION="$BUILD_NUMBER" \
  archive > "$OUT/archive.log" 2>&1 \
  || { grep -E "error:" "$OUT/archive.log" | sort -u | head -20; tail -15 "$OUT/archive.log"; echo "✗ archive failed — full log: $OUT/archive.log" >&2; exit 1; }

echo "==> Exporting development IPA"
cat > "$OUT/ExportOptions.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>method</key><string>debugging</string>
<key>teamID</key><string>$TEAM</string>
<key>signingStyle</key><string>automatic</string>
<key>thinning</key><string>&lt;none&gt;</string>
</dict></plist>
EOF
rm -rf "$OUT/export"
xcodebuild -exportArchive -archivePath "$ARCHIVE" -exportPath "$OUT/export" \
  -exportOptionsPlist "$OUT/ExportOptions.plist" -allowProvisioningUpdates > "$OUT/export.log" 2>&1 \
  || { tail -30 "$OUT/export.log"; echo "✗ export failed — full log: $OUT/export.log" >&2; exit 1; }
IPA="$(ls "$OUT"/export/*.ipa | head -1)"

# What the phone will compare against its own CFBundleVersion.
stamped="$(python3 - "$IPA" <<'PY'
import plistlib, re, sys, zipfile
with zipfile.ZipFile(sys.argv[1]) as zf:
    name = next(n for n in zf.namelist() if re.match(r"^Payload/[^/]+\.app/Info\.plist$", n))
    info = plistlib.loads(zf.read(name))
print(info["CFBundleVersion"], info.get("CFBundleIdentifier", ""))
PY
)"
read -r ipa_build ipa_bundle <<<"$stamped"
[ "$ipa_build" = "$BUILD_NUMBER" ] || { echo "✗ the IPA says build $ipa_build, expected $BUILD_NUMBER" >&2; exit 1; }
echo "ipa: $IPA"
echo "build: $BUILD_NUMBER ($ipa_bundle, $(( $(stat -f%z "$IPA") / 1048576 )) MB)"
[ "$publish" = 1 ] || exit 0
"$REPO_DIR/scripts/publish-app-update.sh" --file "$IPA" --notes "$notes"
