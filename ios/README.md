# IntelliStock for iOS

The native SwiftUI app. It replaces the Flutter app, which now lives in `../mobile_flutter_depricated/`.

## Requirements

- Xcode 27 and iOS 26.0 or later.
- [XcodeGen](https://github.com/yonaskolb/XcodeGen): `brew install xcodegen`.
- No third-party packages.

## Install on the iPhone

```sh
ios/scripts/deploy.sh            # Release build, installs over the existing app
CONFIGURATION=Debug ios/scripts/deploy.sh
IOS_DEVICE_ID=<udid> ios/scripts/deploy.sh
```

- Keep the phone connected and unlocked, with Developer Mode on.
- The bundle ID matches the Flutter build, so the session, server URL, lock settings and home-screen
  widget carry over.

## Build and test

```sh
cd ios && xcodegen generate      # only after editing project.yml
xcodebuild -project IntelliStock.xcodeproj -scheme IntelliStock \
  -destination 'platform=iOS Simulator,name=iPhone 17 Pro' test
```

The read-only screenshot tour runs against a live backend:

```sh
TEST_RUNNER_IS_URL=… TEST_RUNNER_IS_USER=… TEST_RUNNER_IS_PASS=… \
TEST_RUNNER_IS_DETAILS="instance=/instances/<id>,…" \
xcodebuild -project IntelliStock.xcodeproj -scheme IntelliStockTour \
  -destination 'platform=iOS Simulator,name=iPhone 17 Pro' -resultBundlePath build/tour.xcresult test
xcrun xcresulttool export attachments --path build/tour.xcresult --output-path build/tour
```

The tour only navigates. It never starts, stops, trades, deletes or approves anything.

## Layout

| Path | What |
|---|---|
| `project.yml` | XcodeGen spec. Folders are synced, so a new `.swift` file needs no project change |
| `IntelliStock/App/` | Entry point, gates (Connect → Login → Onboarding → tabs), lock, `AppServices` |
| `IntelliStock/Core/` | `JSON`, `ApiClient`, keychain, session, lock, push, polling, formatters, charts, navigation |
| `IntelliStock/DesignSystem/` | Tokens, SF Symbol map and components. Read `DesignSystem/README.md` before building a screen |
| `IntelliStock/Features/<F>/` | Per feature: `Data/` (models + repository), `Model/` (view models), `Views/` |
| `PortfolioWidget/` | The WidgetKit home-screen widget |
| `IntelliStockTests/` | Swift Testing; network tests use `DataStub` |
| `IntelliStockUITests/` | The screenshot tour |
| `parity/` | The Flutter → Swift parity checklists and rulings |

## Rules

- No gradients, glows or decorative backgrounds.
- Liquid Glass only on floating controls.
- System colours and Dynamic Type.

The design and plan:
- `docs/superpowers/specs/2026-10-01-native-ios-port-design.md`;
- `docs/superpowers/specs/2026-10-02-ios-ui-redesign.md`;
- `docs/superpowers/plans/2026-10-01-native-ios-port.md`.
