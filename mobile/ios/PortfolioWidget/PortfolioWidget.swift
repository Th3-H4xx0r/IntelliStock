// PortfolioWidget — IntelliStock iOS home-screen widgets.
//
// SCAFFOLD: this Swift source exists so the WidgetKit extension can be added.
// To activate, in Xcode: File → New → Target → Widget Extension named
// "PortfolioWidget", add it to the App Group `group.dev.pkrishna.intellistock`
// (same group on the Runner target), and point the target at this file.
// See mobile/docs/NATIVE_SETUP.md §2–§3 for the full checklist.
//
// The Flutter app's WidgetSyncService writes these App-Group UserDefaults keys:
//   "portfolio_data"  → WidgetPortfolio JSON
//   "positions_data"  → [WidgetPosition] JSON
//   "instances_data"  → [WidgetInstance] JSON

import WidgetKit
import SwiftUI

private let kAppGroup = "group.dev.pkrishna.intellistock"

// MARK: - Portfolio widget

struct PortfolioEntry: TimelineEntry {
    let date: Date
    let accountValue: Double
    let dayPnlAbs: Double
    let dayPnlPct: Double
    let asOf: String
}

struct PortfolioProvider: TimelineProvider {
    func placeholder(in context: Context) -> PortfolioEntry {
        PortfolioEntry(date: .now, accountValue: 0, dayPnlAbs: 0, dayPnlPct: 0, asOf: "")
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
            date: .now,
            accountValue: json["accountValue"] as? Double ?? 0,
            dayPnlAbs: json["dayPnlAbs"] as? Double ?? 0,
            dayPnlPct: json["dayPnlPct"] as? Double ?? 0,
            asOf: json["asOf"] as? String ?? ""
        )
    }
}

struct PortfolioWidgetView: View {
    let entry: PortfolioEntry
    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("IntelliStock").font(.caption2).foregroundColor(.secondary)
            Text(String(format: "$%.2f", entry.accountValue)).font(.headline)
            Text(entry.dayPnlAbs >= 0
                 ? String(format: "▲ +$%.2f (+%.2f%%)", entry.dayPnlAbs, entry.dayPnlPct)
                 : String(format: "▼ -$%.2f (%.2f%%)", abs(entry.dayPnlAbs), entry.dayPnlPct))
                .font(.subheadline)
                .foregroundColor(entry.dayPnlAbs >= 0 ? .green : .red)
        }
        .padding()
        .containerBackground(.fill.tertiary, for: .widget)
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
    func placeholder(in context: Context) -> InstanceEntry { InstanceEntry(date: .now, instances: []) }
    func getSnapshot(in context: Context, completion: @escaping (InstanceEntry) -> Void) { completion(entry()) }
    func getTimeline(in context: Context, completion: @escaping (Timeline<InstanceEntry>) -> Void) {
        completion(Timeline(entries: [entry()], policy: .after(Date().addingTimeInterval(900))))
    }
    private func entry() -> InstanceEntry {
        let defaults = UserDefaults(suiteName: kAppGroup)
        let raw = defaults?.string(forKey: "instances_data") ?? "[]"
        let arr = (try? JSONSerialization.jsonObject(
            with: raw.data(using: .utf8) ?? Data())) as? [[String: Any]] ?? []
        return InstanceEntry(date: .now, instances: arr)
    }
}

struct InstanceStatusWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: "InstanceWidget", provider: InstanceProvider()) { entry in
            VStack(alignment: .leading, spacing: 4) {
                Text("Instances").font(.caption2).foregroundColor(.secondary)
                ForEach(Array(entry.instances.prefix(3).enumerated()), id: \.offset) { _, inst in
                    if let name = inst["name"] as? String {
                        HStack(spacing: 6) {
                            Circle()
                                .fill((inst["running"] as? Bool ?? false) ? Color.green : Color.gray)
                                .frame(width: 6, height: 6)
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

// MARK: - Bundle

@main
struct IntelliStockWidgetBundle: WidgetBundle {
    var body: some Widget {
        PortfolioWidget()
        InstanceStatusWidget()
    }
}
