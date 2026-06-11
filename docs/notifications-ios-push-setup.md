# iOS push (APNs) — operator setup

The notification routing + sender code is in place. iOS push delivery needs a
few one-time, operator-only steps (Apple credentials + an Xcode capability).
Until they're done, push simply doesn't deliver — Discord routing is unaffected.

## 1. Apple Developer: create an APNs auth key

1. developer.apple.com → Certificates, IDs & Profiles → **Keys** → **+**.
2. Enable **Apple Push Notifications service (APNs)**, create, and download the
   `.p8` file (you can only download it once). Note the **Key ID**.
3. Note your **Team ID** (top-right of the developer portal).
4. The app **Bundle ID** is `dev.pkrishna.intellistockMobile`.

## 2. Xcode: enable the Push Notifications capability

In `mobile/ios/Runner.xcworkspace` → Runner target → **Signing & Capabilities**:

- **+ Capability → Push Notifications** (this links `Runner.entitlements`, which
  already declares `aps-environment`).
- **+ Capability → Background Modes → Remote notifications** (already in
  `Info.plist` as `UIBackgroundModes`).

`Runner.entitlements` ships with `aps-environment = development` (sandbox), which
matches debug builds run from Xcode. App Store / TestFlight builds use the
production APNs environment automatically.

## 3. Backend: APNs env vars

Set these where the API runs (env or secret_store):

| var | value |
|-----|-------|
| `APNS_KEY_ID` | the Key ID from step 1 |
| `APNS_TEAM_ID` | your Apple Team ID |
| `APNS_BUNDLE_ID` | `dev.pkrishna.intellistockMobile` |
| `APNS_KEY_PATH` | path to the `.p8` file (or set `APNS_KEY` to its contents) |
| `APNS_ENV` | `sandbox` for debug-build device tokens, `prod` for release |

If any are absent the sender logs once and no-ops (push disabled).

## 4. Verify

1. Run the app on a **physical device** (push doesn't work in the simulator).
2. Log in → the app requests notification permission and registers the device
   token (`POST /push/devices`).
3. Settings → **Notifications** → toggle **iOS push** on for a category, then tap
   **Test iOS push**. You should get a banner. (Tap **Test Discord** to verify
   that channel independently.)

## How it fits together

`live_alerts.alert_*` → `notifications.notify(category, …)` reads the per-category
matrix (`NotificationPreferences`) and fans out to Discord (`enqueue_discord_message`)
and/or iOS push (`apns_sender.send_to_user` → registered `PushDevices`). Defaults are
Discord-only, so nothing changes until you opt a category into push.
