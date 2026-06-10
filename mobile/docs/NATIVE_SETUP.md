# Native Setup Guide — Biometrics, Widgets & Deep Links

This document lists every native-side change the orchestrator must apply after
integrating the biometric lock, iOS home-screen widget, and URL-scheme deep
links. All Flutter-side code is complete; only the native targets below require
manual configuration.

---

## 1. Biometric Authentication (`local_auth`)

### iOS — `ios/Runner/Info.plist`

Add the Face ID usage description key inside the top-level `<dict>`:

```xml
<key>NSFaceIDUsageDescription</key>
<string>IntelliStock uses Face ID to lock the app and protect your portfolio.</string>
```

Without this key iOS will crash when `local_auth` requests Face ID.

### Android — `android/app/src/main/AndroidManifest.xml`

Add the biometric permission inside `<manifest>`, before `<application>`:

```xml
<uses-permission android:name="android.permission.USE_BIOMETRIC" />
```

### Android — `android/app/src/main/kotlin/.../MainActivity.kt`

Change the base class from `FlutterActivity` to `FlutterFragmentActivity`:

```kotlin
import io.flutter.embedding.android.FlutterFragmentActivity

class MainActivity : FlutterFragmentActivity()
```

`FlutterFragmentActivity` is required for `local_auth` biometric prompts on
Android. If `MainActivity.java` is used instead, change the import and parent
class accordingly.

---

## 2. iOS WidgetKit Extension (`home_widget`)

### 2a. App Group — both targets

1. In Xcode → select the **Runner** target → Signing & Capabilities →
   "+ Capability" → **App Groups** → add:
   `group.dev.pkrishna.intellistock`

2. Create a new target: **File → New → Target → Widget Extension**.
   Name it `PortfolioWidget`. Uncheck "Include Live Activity".

3. In the new `PortfolioWidget` target → Signing & Capabilities →
   "+ Capability" → **App Groups** → add the same group:
   `group.dev.pkrishna.intellistock`

### 2b. Keychain Sharing (optional but recommended)

Add "Keychain Sharing" capability to both Runner and PortfolioWidget targets
and set the same keychain group (e.g. `dev.pkrishna.intellistock`) so
`flutter_secure_storage` tokens are accessible if you ever need them from
extensions.

### 2c. App Group ID in `home_widget`

The Flutter side already calls `HomeWidget.setAppGroupId('group.dev.pkrishna.intellistock')`.
No additional Dart config is needed.

---

## 3. SwiftUI Widget — `PortfolioWidget`

### 3a. App-Group JSON keys written by `WidgetSyncService`

| Key constant (Dart)       | String key written            | Value type       |
|---------------------------|-------------------------------|------------------|
| `kWidgetKeyPortfolio`     | `"portfolio_data"`            | JSON string      |
| `kWidgetKeyPositions`     | `"positions_data"`            | JSON string (array) |
| `kWidgetKeyInstances`     | `"instances_data"`            | JSON string (array) |

### 3b. `portfolio_data` JSON schema

```json
{
  "accountValue": 12345.67,
  "dayPnlAbs": 123.45,
  "dayPnlPct": 1.02,
  "intradayPoints": [
    {"t": 1700000000, "v": 12200.0},
    {"t": 1700003600, "v": 12345.67}
  ],
  "asOf": "2026-06-10T15:30:00Z"
}
```

### 3c. `positions_data` JSON schema (array)

```json
[
  {
    "symbol": "AAPL",
    "qty": 10.0,
    "marketValue": 1823.50,
    "unrealizedPnlAbs": 45.20,
    "unrealizedPnlPct": 2.54
  }
]
```

### 3d. `instances_data` JSON schema (array)

```json
[
  {
    "id": "abc123",
    "name": "Momentum v2",
    "running": true,
    "pnlAbs": 312.50,
    "pnlPct": 4.87
  }
]
```

### 3e. Stub SwiftUI widget structure

Create `ios/PortfolioWidget/PortfolioWidget.swift` with these widget families:

```swift
import WidgetKit
import SwiftUI

// MARK: - Data model (mirrors WidgetPayload JSON)

struct PortfolioEntry: TimelineEntry {
    let date: Date
    let accountValue: Double
    let dayPnlAbs: Double
    let dayPnlPct: Double
    let asOf: String
}

// MARK: - Provider

struct PortfolioProvider: TimelineProvider {
    let appGroup = "group.dev.pkrishna.intellistock"

    func placeholder(in context: Context) -> PortfolioEntry {
        PortfolioEntry(date: .now, accountValue: 0, dayPnlAbs: 0, dayPnlPct: 0, asOf: "")
    }

    func getSnapshot(in context: Context, completion: @escaping (PortfolioEntry) -> Void) {
        completion(entry())
    }

    func getTimeline(in context: Context, completion: @escaping (Timeline<PortfolioEntry>) -> Void) {
        let e = entry()
        // Reload every 15 minutes (Flutter app also pushes updates via HomeWidget.updateWidget).
        completion(Timeline(entries: [e], policy: .after(Date().addingTimeInterval(900))))
    }

    private func entry() -> PortfolioEntry {
        let defaults = UserDefaults(suiteName: appGroup)
        let raw = defaults?.string(forKey: "portfolio_data") ?? "{}"
        let data = raw.data(using: .utf8) ?? Data()
        let json = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] ?? [:]
        return PortfolioEntry(
            date: .now,
            accountValue: json["accountValue"] as? Double ?? 0,
            dayPnlAbs:    json["dayPnlAbs"]    as? Double ?? 0,
            dayPnlPct:    json["dayPnlPct"]    as? Double ?? 0,
            asOf:         json["asOf"]         as? String ?? ""
        )
    }
}

// MARK: - Views

struct PortfolioWidgetSmallView: View {
    let entry: PortfolioEntry
    var body: some View {
        VStack(alignment: .leading) {
            Text("Portfolio").font(.caption).foregroundColor(.secondary)
            Text("$\(entry.accountValue, specifier: "%.2f")").font(.headline)
            Text(entry.dayPnlAbs >= 0 ? "+$\(entry.dayPnlAbs, specifier: "%.2f")" : "-$\(abs(entry.dayPnlAbs), specifier: "%.2f")")
                .foregroundColor(entry.dayPnlAbs >= 0 ? .green : .red)
                .font(.subheadline)
        }
        .padding()
        .containerBackground(.fill.tertiary, for: .widget)
    }
}

// MARK: - Widget definition

@main
struct PortfolioWidgetBundle: WidgetBundle {
    var body: some Widget {
        PortfolioWidgetSmall()
        PortfolioWidgetMedium()
        PortfolioWidgetLarge()
        InstanceStatusWidget()
    }
}

struct PortfolioWidgetSmall: Widget {
    let kind = "PortfolioWidget"
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: kind, provider: PortfolioProvider()) { entry in
            PortfolioWidgetSmallView(entry: entry)
        }
        .configurationDisplayName("Portfolio")
        .description("Your portfolio value and day P&L.")
        .supportedFamilies([.systemSmall])
    }
}

struct PortfolioWidgetMedium: Widget {
    let kind = "PortfolioWidgetMedium"
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: kind, provider: PortfolioProvider()) { entry in
            // Medium layout — expand to show top positions.
            PortfolioWidgetSmallView(entry: entry)
        }
        .configurationDisplayName("Portfolio (medium)")
        .supportedFamilies([.systemMedium])
    }
}

struct PortfolioWidgetLarge: Widget {
    let kind = "PortfolioWidgetLarge"
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: kind, provider: PortfolioProvider()) { entry in
            // Large layout — show chart + positions list.
            PortfolioWidgetSmallView(entry: entry)
        }
        .configurationDisplayName("Portfolio (large)")
        .supportedFamilies([.systemLarge])
    }
}

// MARK: - Instance status widget

struct InstanceEntry: TimelineEntry {
    let date: Date
    let instances: [[String: Any]]
}

struct InstanceProvider: TimelineProvider {
    let appGroup = "group.dev.pkrishna.intellistock"

    func placeholder(in context: Context) -> InstanceEntry {
        InstanceEntry(date: .now, instances: [])
    }
    func getSnapshot(in context: Context, completion: @escaping (InstanceEntry) -> Void) {
        completion(entry())
    }
    func getTimeline(in context: Context, completion: @escaping (Timeline<InstanceEntry>) -> Void) {
        completion(Timeline(entries: [entry()], policy: .after(Date().addingTimeInterval(900))))
    }
    private func entry() -> InstanceEntry {
        let defaults = UserDefaults(suiteName: appGroup)
        let raw = defaults?.string(forKey: "instances_data") ?? "[]"
        let data = raw.data(using: .utf8) ?? Data()
        let arr = (try? JSONSerialization.jsonObject(with: data)) as? [[String: Any]] ?? []
        return InstanceEntry(date: .now, instances: arr)
    }
}

struct InstanceStatusWidget: Widget {
    let kind = "InstanceWidget"
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: kind, provider: InstanceProvider()) { entry in
            VStack(alignment: .leading) {
                Text("Instances").font(.caption).foregroundColor(.secondary)
                ForEach(entry.instances.prefix(3), id: \.self) { inst in
                    if let name = inst["name"] as? String,
                       let running = inst["running"] as? Bool {
                        HStack {
                            Circle().fill(running ? Color.green : Color.gray).frame(width: 6, height: 6)
                            Text(name).font(.caption2)
                        }
                    }
                }
            }
            .padding()
            .containerBackground(.fill.tertiary, for: .widget)
        }
        .configurationDisplayName("Instance Status")
        .description("Running/stopped status of your IntelliStock instances.")
        .supportedFamilies([.systemSmall, .systemMedium])
    }
}
```

> **Note:** The `@main` attribute goes on `PortfolioWidgetBundle` only.
> Remove any auto-generated `@main` from separate widget files if Xcode created
> multiple files.

---

## 4. URL Scheme Deep Links

Add a custom URL scheme so push notifications and web links can deep-link into
the app.

### iOS — `ios/Runner/Info.plist`

```xml
<key>CFBundleURLTypes</key>
<array>
  <dict>
    <key>CFBundleURLSchemes</key>
    <array>
      <string>intellistock</string>
    </array>
    <key>CFBundleURLName</key>
    <string>dev.pkrishna.intellistock</string>
  </dict>
</array>
```

### Android — `android/app/src/main/AndroidManifest.xml`

Inside the `<activity>` block:

```xml
<intent-filter>
  <action android:name="android.intent.action.VIEW" />
  <category android:name="android.intent.category.DEFAULT" />
  <category android:name="android.intent.category.BROWSABLE" />
  <data android:scheme="intellistock" />
</intent-filter>
```

Example deep links handled by GoRouter:

| URL                             | Destination        |
|---------------------------------|--------------------|
| `intellistock://app/settings`   | `/settings`        |
| `intellistock://app/dashboard`  | `/dashboard`       |
| `intellistock://app/instances`  | `/instances`       |

---

## 5. Android `home_widget` configuration

For Android Glance widgets (future work), add a receiver in `AndroidManifest.xml`:

```xml
<receiver android:name=".PortfolioWidgetProvider" android:exported="true">
  <intent-filter>
    <action android:name="android.appwidget.action.APPWIDGET_UPDATE" />
  </intent-filter>
  <meta-data
    android:name="android.appwidget.provider"
    android:resource="@xml/portfolio_widget_info" />
</receiver>
```

The `home_widget` package documentation covers the full Glance setup.
The Flutter `WidgetSyncService` already calls `updateWidget(androidName: 'PortfolioWidgetProvider')`.

---

## Summary checklist

- [ ] iOS `Info.plist`: `NSFaceIDUsageDescription` added
- [ ] Android `AndroidManifest.xml`: `USE_BIOMETRIC` permission added
- [ ] Android `MainActivity`: extended from `FlutterFragmentActivity`
- [ ] Xcode: App Group `group.dev.pkrishna.intellistock` on Runner + PortfolioWidget targets
- [ ] WidgetKit extension created, `PortfolioWidget.swift` stub added
- [ ] `@main` only on `PortfolioWidgetBundle`
- [ ] iOS `Info.plist`: `CFBundleURLTypes` for `intellistock://` scheme
- [ ] Android `AndroidManifest.xml`: intent-filter for `intellistock://` scheme
