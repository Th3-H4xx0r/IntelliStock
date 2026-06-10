// PortfolioWidget — IntelliStock iOS home-screen widgets (iOS 17+).
//
// Reads App-Group UserDefaults keys written by the Flutter WidgetSyncService:
//   "accounts_data"  → [WidgetAccount] JSON  (selectable portfolios)
//   "instances_data" → [WidgetInstance] JSON
// See mobile/docs/NATIVE_SETUP.md.

import WidgetKit
import SwiftUI
import AppIntents

private let kAppGroup = "group.dev.pkrishna.intellistock"
private let kBg = Color(red: 0.02, green: 0.02, blue: 0.05)

// MARK: - Shared App-Group data access

struct AccountData {
    let id: String
    let label: String
    let value: Double
    let pnlAbs: Double
    let pnlPct: Double
}

private func readAccounts() -> [AccountData] {
    let d = UserDefaults(suiteName: kAppGroup)
    guard let raw = d?.string(forKey: "accounts_data"),
          let data = raw.data(using: .utf8),
          let arr = (try? JSONSerialization.jsonObject(with: data)) as? [[String: Any]]
    else { return [] }
    return arr.map { j in
        AccountData(
            id: j["id"] as? String ?? "",
            label: j["label"] as? String ?? "Portfolio",
            value: j["accountValue"] as? Double ?? 0,
            pnlAbs: j["dayPnlAbs"] as? Double ?? 0,
            pnlPct: j["dayPnlPct"] as? Double ?? 0
        )
    }
}

/// Resolve the configured account (or the first available).
private func resolveAccount(_ id: String?) -> AccountData? {
    let all = readAccounts()
    if let id = id, let m = all.first(where: { $0.id == id }) { return m }
    return all.first
}

// MARK: - Entry + view

struct PortfolioEntry: TimelineEntry {
    let date: Date
    let label: String
    let value: Double
    let pnlAbs: Double
    let pnlPct: Double
    let hasData: Bool
}

private func portfolioEntry(for id: String?, date: Date = Date()) -> PortfolioEntry {
    if let a = resolveAccount(id) {
        return PortfolioEntry(date: date, label: a.label, value: a.value,
                              pnlAbs: a.pnlAbs, pnlPct: a.pnlPct, hasData: true)
    }
    return PortfolioEntry(date: date, label: "Portfolio", value: 0,
                          pnlAbs: 0, pnlPct: 0, hasData: false)
}

struct PortfolioWidgetView: View {
    let entry: PortfolioEntry
    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(entry.hasData ? entry.label : "IntelliStock")
                .font(.caption2).foregroundColor(.secondary).lineLimit(1)
            Text(String(format: "$%.2f", entry.value))
                .font(.title3).bold().foregroundColor(.white).lineLimit(1)
            Text(entry.pnlAbs >= 0
                 ? String(format: "▲ +$%.2f (+%.2f%%)", entry.pnlAbs, entry.pnlPct)
                 : String(format: "▼ -$%.2f (%.2f%%)", abs(entry.pnlAbs), entry.pnlPct))
                .font(.footnote)
                .foregroundColor(entry.pnlAbs >= 0 ? .green : .red).lineLimit(1)
            if !entry.hasData {
                Spacer(minLength: 0)
                Text("Open IntelliStock to sync").font(.caption2).foregroundColor(.secondary)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .padding()
        .containerBackground(kBg, for: .widget)
    }
}

// MARK: - Configuration (portfolio + refresh interval)

struct AccountEntity: AppEntity, Identifiable {
    let id: String
    let label: String
    static var typeDisplayRepresentation: TypeDisplayRepresentation { "Portfolio" }
    var displayRepresentation: DisplayRepresentation { DisplayRepresentation(title: "\(label)") }
    static var defaultQuery = AccountQuery()
}

struct AccountQuery: EntityQuery {
    func entities(for identifiers: [String]) async throws -> [AccountEntity] {
        readAccounts().filter { identifiers.contains($0.id) }
            .map { AccountEntity(id: $0.id, label: $0.label) }
    }
    func suggestedEntities() async throws -> [AccountEntity] {
        readAccounts().map { AccountEntity(id: $0.id, label: $0.label) }
    }
    func defaultResult() async -> AccountEntity? {
        readAccounts().first.map { AccountEntity(id: $0.id, label: $0.label) }
    }
}

enum RefreshIntervalChoice: String, AppEnum {
    case s30, m1, m5, m10, m15, m30, h1
    static var typeDisplayRepresentation: TypeDisplayRepresentation { "Refresh interval" }
    static var caseDisplayRepresentations: [RefreshIntervalChoice: DisplayRepresentation] {
        [
            .s30: "30 seconds", .m1: "1 minute", .m5: "5 minutes",
            .m10: "10 minutes", .m15: "15 minutes", .m30: "30 minutes", .h1: "1 hour",
        ]
    }
    var seconds: TimeInterval {
        switch self {
        case .s30: return 30
        case .m1: return 60
        case .m5: return 300
        case .m10: return 600
        case .m15: return 900
        case .m30: return 1800
        case .h1: return 3600
        }
    }
}

struct SelectPortfolioIntent: WidgetConfigurationIntent {
    static var title: LocalizedStringResource { "Select Portfolio" }
    static var description: IntentDescription {
        IntentDescription("Choose which portfolio to show and how often to refresh.")
    }
    @Parameter(title: "Portfolio") var account: AccountEntity?
    @Parameter(title: "Refresh every", default: .m15) var refresh: RefreshIntervalChoice
}

struct ConfigProvider: AppIntentTimelineProvider {
    func placeholder(in context: Context) -> PortfolioEntry { portfolioEntry(for: nil) }
    func snapshot(for configuration: SelectPortfolioIntent, in context: Context) async -> PortfolioEntry {
        portfolioEntry(for: configuration.account?.id)
    }
    func timeline(for configuration: SelectPortfolioIntent, in context: Context) async -> Timeline<PortfolioEntry> {
        let e = portfolioEntry(for: configuration.account?.id)
        let next = Date().addingTimeInterval(configuration.refresh.seconds)
        return Timeline(entries: [e], policy: .after(next))
    }
}

struct PortfolioWidget: Widget {
    var body: some WidgetConfiguration {
        AppIntentConfiguration(kind: "PortfolioWidget",
                               intent: SelectPortfolioIntent.self,
                               provider: ConfigProvider()) { entry in
            PortfolioWidgetView(entry: entry)
        }
        .configurationDisplayName("Portfolio")
        .description("Live portfolio value & day P&L. Long-press → Edit to pick a portfolio and refresh rate.")
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
        let d = UserDefaults(suiteName: kAppGroup)
        let raw = d?.string(forKey: "instances_data") ?? "[]"
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
                            Text(name).font(.caption2).foregroundColor(.white).lineLimit(1)
                        }
                    }
                }
                if entry.instances.isEmpty {
                    Text("No instances").font(.caption2).foregroundColor(.secondary)
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            .padding()
            .containerBackground(kBg, for: .widget)
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
