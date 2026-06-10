// PortfolioWidget — IntelliStock iOS home-screen widgets.
//
// Reads App-Group UserDefaults keys written by the Flutter WidgetSyncService:
//   "portfolio_data"  → WidgetPortfolio JSON
//   "positions_data"  → [WidgetPosition] JSON
//   "instances_data"  → [WidgetInstance] JSON
// See mobile/docs/NATIVE_SETUP.md.

import WidgetKit
import SwiftUI

private let kAppGroup = "group.dev.pkrishna.intellistock"

// Apply a widget container background on iOS 17+, fall back to a plain
// background on iOS 14–16 (containerBackground is iOS 17+).
extension View {
    @ViewBuilder
    func widgetContainerBackground(_ color: Color) -> some View {
        if #available(iOS 17.0, *) {
            self.containerBackground(color, for: .widget)
        } else {
            self.background(color)
        }
    }
}

private let kBg = Color(red: 0.02, green: 0.02, blue: 0.05)
private let kViolet = Color(red: 0.655, green: 0.545, blue: 0.980)

// MARK: - Portfolio widget

struct PortfolioEntry: TimelineEntry {
    let date: Date
    let accountValue: Double
    let dayPnlAbs: Double
    let dayPnlPct: Double
}

struct PortfolioProvider: TimelineProvider {
    func placeholder(in context: Context) -> PortfolioEntry {
        PortfolioEntry(date: Date(), accountValue: 0, dayPnlAbs: 0, dayPnlPct: 0)
    }
    func getSnapshot(in context: Context, completion: @escaping (PortfolioEntry) -> Void) {
        completion(entry())
    }
    func getTimeline(in context: Context, completion: @escaping (Timeline<PortfolioEntry>) -> Void) {
        completion(Timeline(entries: [entry()],
                            policy: .after(Date().addingTimeInterval(900))))
    }
    private func entry() -> PortfolioEntry {
        let defaults = UserDefaults(suiteName: kAppGroup)
        let raw = defaults?.string(forKey: "portfolio_data") ?? "{}"
        let json = (try? JSONSerialization.jsonObject(
            with: raw.data(using: .utf8) ?? Data())) as? [String: Any] ?? [:]
        return PortfolioEntry(
            date: Date(),
            accountValue: json["accountValue"] as? Double ?? 0,
            dayPnlAbs: json["dayPnlAbs"] as? Double ?? 0,
            dayPnlPct: json["dayPnlPct"] as? Double ?? 0
        )
    }
}

struct PortfolioWidgetView: View {
    let entry: PortfolioEntry
    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("IntelliStock").font(.caption2).foregroundColor(.secondary)
            Text(String(format: "$%.2f", entry.accountValue))
                .font(.headline).foregroundColor(.white)
            Text(entry.dayPnlAbs >= 0
                 ? String(format: "▲ +$%.2f (+%.2f%%)", entry.dayPnlAbs, entry.dayPnlPct)
                 : String(format: "▼ -$%.2f (%.2f%%)", abs(entry.dayPnlAbs), entry.dayPnlPct))
                .font(.subheadline)
                .foregroundColor(entry.dayPnlAbs >= 0 ? .green : .red)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .padding()
        .widgetContainerBackground(kBg)
    }
}

struct PortfolioWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: "PortfolioWidget", provider: PortfolioProvider()) { entry in
            PortfolioWidgetView(entry: entry)
        }
        .configurationDisplayName("Portfolio")
        .description("Your portfolio value and day P&L.")
        .supportedFamilies([.systemSmall, .systemMedium, .systemLarge])
    }
}

// MARK: - Instance status widget

struct InstanceEntry: TimelineEntry {
    let date: Date
    let instances: [[String: Any]]
}

struct InstanceProvider: TimelineProvider {
    func placeholder(in context: Context) -> InstanceEntry { InstanceEntry(date: Date(), instances: []) }
    func getSnapshot(in context: Context, completion: @escaping (InstanceEntry) -> Void) { completion(entry()) }
    func getTimeline(in context: Context, completion: @escaping (Timeline<InstanceEntry>) -> Void) {
        completion(Timeline(entries: [entry()], policy: .after(Date().addingTimeInterval(900))))
    }
    private func entry() -> InstanceEntry {
        let defaults = UserDefaults(suiteName: kAppGroup)
        let raw = defaults?.string(forKey: "instances_data") ?? "[]"
        let arr = (try? JSONSerialization.jsonObject(
            with: raw.data(using: .utf8) ?? Data())) as? [[String: Any]] ?? []
        return InstanceEntry(date: Date(), instances: arr)
    }
}

struct InstanceStatusWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: "InstanceWidget", provider: InstanceProvider()) { entry in
            VStack(alignment: .leading, spacing: 6) {
                Text("Instances").font(.caption2).foregroundColor(.secondary)
                ForEach(Array(entry.instances.prefix(3).enumerated()), id: \.offset) { _, inst in
                    if let name = inst["name"] as? String {
                        HStack(spacing: 6) {
                            Circle()
                                .fill((inst["running"] as? Bool ?? false) ? Color.green : Color.gray)
                                .frame(width: 6, height: 6)
                            Text(name).font(.caption2).foregroundColor(.white)
                        }
                    }
                }
                if entry.instances.isEmpty {
                    Text("No instances").font(.caption2).foregroundColor(.secondary)
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            .padding()
            .widgetContainerBackground(kBg)
        }
        .configurationDisplayName("Instance Status")
        .description("Running/stopped status of your IntelliStock instances.")
        .supportedFamilies([.systemSmall, .systemMedium])
    }
}

// MARK: - Bundle

@main
struct IntelliStockWidgetBundle: WidgetBundle {
    var body: some Widget {
        PortfolioWidget()
        InstanceStatusWidget()
    }
}
