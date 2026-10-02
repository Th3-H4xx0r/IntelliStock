# Deprecated: the Flutter app

This folder is the original Flutter app, formerly `mobile/`. It is **deprecated** and kept for
reference only.

The IntelliStock mobile app is now the native SwiftUI app in [`../ios/`](../ios/README.md). It ports
every screen and behaviour of this app and restyles them to Apple's guidelines.

- **Retired on:** 2026-10-02 (branch `feat/native-ios-app`).
- **Why:** the operator wanted a pure native iOS app with an Apple-native design.
- **Replaces it:** `ios/`. Build and install with `ios/scripts/deploy.sh`.
- **Install over this app:** yes. The native app uses the same bundle ID, App Group and keychain
  layout, so an installed Flutter build is replaced in place, still signed in and pointed at the same
  server.
- **Android:** support ended with this app.

Nothing builds or ships from this folder any more. Don't add features here. If a behaviour question
comes up, read the Dart for reference and change the Swift.

The design and plan for the port:
- `docs/superpowers/specs/2026-10-01-native-ios-port-design.md`;
- `docs/superpowers/specs/2026-10-02-ios-ui-redesign.md`;
- `docs/superpowers/plans/2026-10-01-native-ios-port.md`.
