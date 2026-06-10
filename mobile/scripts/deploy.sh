#!/usr/bin/env bash
#
# deploy.sh — build & install the IntelliStock mobile app (RELEASE) to a
# connected iOS OR Android device, one command, no Xcode/Android Studio.
#
# Asks which platform (1 = iOS, 2 = Android), then auto-detects the attached
# device and installs to it (iOS via `xcrun devicectl`, Android via `adb`),
# the same way scripts/deploy_both.sh does in jarvis-copilot.
#
# Usage:
#   scripts/deploy.sh            # interactive: prompts 1/2
#   scripts/deploy.sh 1          # iOS
#   scripts/deploy.sh 2          # Android
#   IOS_DEVICE_ID=<udid> scripts/deploy.sh 1     # force a specific device
#   ANDROID_DEVICE_ID=<serial> scripts/deploy.sh 2
#
# Prereqs (one-time): cd mobile && flutter pub get
#   iOS: device connected + unlocked + Developer Mode on; signing set up.
#   Android: device connected + USB debugging on.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$APP_DIR"

CHOICE="${1:-}"
if [ -z "$CHOICE" ]; then
  echo "Deploy IntelliStock mobile to a connected device:"
  echo "  1) iOS"
  echo "  2) Android"
  read -rp "Select [1/2]: " CHOICE
fi

case "$CHOICE" in
  1|ios|iOS|IOS)
    echo "▸ Detecting iOS device…"
    RE='[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}'
    UDID="${IOS_DEVICE_ID:-$(xcrun devicectl list devices 2>/dev/null | grep -i iphone | grep -ioE "$RE" | head -1 || true)}"
    [ -n "$UDID" ] || { echo "✗ No iPhone detected. Connect & unlock it (Developer Mode on)."; exit 1; }
    echo "  iPhone=$UDID"

    echo "▸ Building (flutter build ios --release)…"
    flutter build ios --release
    APP="build/ios/iphoneos/Runner.app"
    [ -d "$APP" ] || { echo "✗ iOS build failed (no Runner.app)"; exit 1; }

    echo "▸ Installing to iPhone…"
    xcrun devicectl device install app --device "$UDID" "$APP"
    echo "✓ Installed. Launch IntelliStock from the home screen."
    ;;

  2|android|Android|ANDROID)
    echo "▸ Detecting Android device…"
    SERIAL="${ANDROID_DEVICE_ID:-$(adb devices | awk 'NR>1 && $2=="device"{print $1; exit}')}"
    [ -n "$SERIAL" ] || { echo "✗ No Android device detected. Connect it & enable USB debugging."; exit 1; }
    echo "  device=$SERIAL"

    echo "▸ Building (flutter build apk --release)…"
    flutter build apk --release
    APK="build/app/outputs/flutter-apk/app-release.apk"
    [ -f "$APK" ] || { echo "✗ Android build failed (no app-release.apk)"; exit 1; }

    echo "▸ Installing to device…"
    adb -s "$SERIAL" install -r "$APK"
    echo "✓ Installed. Launch IntelliStock from the app drawer."
    ;;

  *)
    echo "✗ Invalid choice: '$CHOICE' (expected 1 for iOS or 2 for Android)"
    exit 1
    ;;
esac
