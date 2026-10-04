// PortfolioWidget — IntelliStock home-screen and Lock Screen widgets.
//
// The views and pure helpers live in ../WidgetShared (compiled into the unit
// tests as well). This file holds the WidgetKit side: the App-Group store,
// the self-refresh, the "Select Portfolio" configuration intent, and the
// timeline providers.
//
// App-Group keys (written by the app's `WidgetSync`, and by the widget's own
// fetch of `/widget/accounts` with "widget_api_base" + "widget_token"):
//   "accounts_data"  → [WidgetAccount] JSON  (selectable portfolios + positions)
//   "instances_data" → [WidgetInstance] JSON
//   "synced_at"      → epoch seconds of the last sync

import AppIntents
import SwiftUI
import WidgetKit

private let kAppGroup = "group.dev.pkrishna.intellistock"

// MARK: - Store

enum PortfolioStore {
    private static var defaults: UserDefaults? { UserDefaults(suiteName: kAppGroup) }

    static func accounts() -> [PortfolioSnapshot] {
        let d = defaults
        let epoch = d?.double(forKey: "synced_at") ?? 0
        return PortfolioSnapshot.decodeAccounts(
            d?.string(forKey: "accounts_data"),
            syncedAt: epoch > 0 ? Date(timeIntervalSince1970: epoch) : nil)
    }

    /// The chosen account, or the first one when nothing is chosen or the
    /// chosen one is gone.
    static func account(_ id: String?) -> PortfolioSnapshot? {
        let all = accounts()
        if let id, let match = all.first(where: { $0.id == id }) { return match }
        return all.first
    }

    /// The cached accounts, fetched first when the cache is empty (a fresh
    /// install whose app has stored the token but no data yet).
    static func accountsFetchingIfEmpty() async -> [PortfolioSnapshot] {
        let cached = accounts()
        if !cached.isEmpty { return cached }
        await refresh()
        return accounts()
    }

    static func instances() -> [InstanceItem] {
        InstanceItem.decode(defaults?.string(forKey: "instances_data"))
    }

    /// Fetches `/widget/accounts` so the widget stays current while the app
    /// is closed. Any failure keeps the cached data. iOS budgets how often
    /// this runs; opening the app forces a reload.
    static func refresh() async {
        let d = defaults
        guard let base = d?.string(forKey: "widget_api_base"), !base.isEmpty,
              let token = d?.string(forKey: "widget_token"), !token.isEmpty
        else { return }
        let path = base.hasSuffix("/") ? "widget/accounts" : "/widget/accounts"
        guard let url = URL(string: base + path) else { return }
        var req = URLRequest(url: url)
        req.httpMethod = "GET"
        req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        req.setValue("application/json", forHTTPHeaderField: "Accept")
        req.timeoutInterval = 15
        do {
            let (data, resp) = try await URLSession.shared.data(for: req)
            guard let http = resp as? HTTPURLResponse, http.statusCode == 200,
                  let obj = try JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let accounts = obj["accounts"] as? [[String: Any]], !accounts.isEmpty,
                  let blob = try? JSONSerialization.data(withJSONObject: accounts),
                  let str = String(data: blob, encoding: .utf8)
            else { return }
            d?.set(str, forKey: "accounts_data")
            d?.set(Date().timeIntervalSince1970, forKey: "synced_at")
        } catch {
            // Keep the cached App-Group data on any network or parse failure.
        }
    }
}

// MARK: - Configuration ("Edit Widget" → Portfolio)

struct AccountEntity: AppEntity, Identifiable {
    let id: String
    let name: String
    let value: Double

    init(_ s: PortfolioSnapshot) {
        id = s.id
        name = PortfolioFormat.displayName(s.name)
        value = s.value
    }

    static var typeDisplayRepresentation: TypeDisplayRepresentation { "Portfolio" }
    var displayRepresentation: DisplayRepresentation {
        DisplayRepresentation(title: "\(name)", subtitle: "\(PortfolioFormat.money(value))")
    }
    static let defaultQuery = AccountQuery()
}

struct AccountQuery: EntityQuery {
    func entities(for identifiers: [String]) async throws -> [AccountEntity] {
        await PortfolioStore.accountsFetchingIfEmpty()
            .filter { identifiers.contains($0.id) }
            .map(AccountEntity.init)
    }

    func suggestedEntities() async throws -> [AccountEntity] {
        await PortfolioStore.accountsFetchingIfEmpty().map(AccountEntity.init)
    }

    func defaultResult() async -> AccountEntity? {
        await PortfolioStore.accountsFetchingIfEmpty().first.map(AccountEntity.init)
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

// MARK: - Portfolio widget

struct PortfolioEntry: TimelineEntry {
    let date: Date
    let snapshot: PortfolioSnapshot?
}

struct PortfolioProvider: AppIntentTimelineProvider {
    func placeholder(in context: Context) -> PortfolioEntry {
        PortfolioEntry(date: Date(), snapshot: .sample)
    }

    func snapshot(for configuration: SelectPortfolioIntent, in context: Context) async -> PortfolioEntry {
        let s = PortfolioStore.account(configuration.account?.id)
        return PortfolioEntry(date: Date(), snapshot: s ?? (context.isPreview ? .sample : nil))
    }

    func timeline(for configuration: SelectPortfolioIntent, in context: Context) async -> Timeline<PortfolioEntry> {
        await PortfolioStore.refresh()
        let entry = PortfolioEntry(date: Date(), snapshot: PortfolioStore.account(configuration.account?.id))
        // iOS budgets background reloads; the interval is a request, not a promise.
        return Timeline(entries: [entry], policy: .after(Date().addingTimeInterval(configuration.refresh.seconds)))
    }
}

struct PortfolioWidgetEntryView: View {
    @Environment(\.widgetFamily) private var family
    @Environment(\.widgetRenderingMode) private var renderingMode
    let entry: PortfolioEntry

    var body: some View {
        PortfolioWidgetContent(snapshot: entry.snapshot, family: family, now: entry.date,
                               fullColor: renderingMode == .fullColor)
    }
}

struct PortfolioWidget: Widget {
    var body: some WidgetConfiguration {
        // The kind stays "PortfolioWidget": the app reloads timelines by it.
        AppIntentConfiguration(kind: "PortfolioWidget", intent: SelectPortfolioIntent.self,
                               provider: PortfolioProvider()) { entry in
            PortfolioWidgetEntryView(entry: entry)
        }
        .configurationDisplayName("Portfolio")
        .description("A portfolio's value, today's change and top holdings.")
        .supportedFamilies([
            .systemSmall, .systemMedium, .systemLarge,
            .accessoryRectangular, .accessoryInline,
        ])
    }
}

// MARK: - Instance status widget

struct InstanceEntry: TimelineEntry {
    let date: Date
    let items: [InstanceItem]
}

struct InstanceProvider: TimelineProvider {
    func placeholder(in context: Context) -> InstanceEntry { InstanceEntry(date: Date(), items: []) }
    func getSnapshot(in context: Context, completion: @escaping (InstanceEntry) -> Void) {
        completion(InstanceEntry(date: Date(), items: PortfolioStore.instances()))
    }
    func getTimeline(in context: Context, completion: @escaping (Timeline<InstanceEntry>) -> Void) {
        let entry = InstanceEntry(date: Date(), items: PortfolioStore.instances())
        completion(Timeline(entries: [entry], policy: .after(Date().addingTimeInterval(900))))
    }
}

struct InstanceStatusEntryView: View {
    @Environment(\.widgetFamily) private var family
    @Environment(\.widgetRenderingMode) private var renderingMode
    let entry: InstanceEntry

    var body: some View {
        InstanceStatusContent(items: entry.items, family: family, fullColor: renderingMode == .fullColor)
    }
}

struct InstanceStatusWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: "InstanceWidget", provider: InstanceProvider()) { entry in
            InstanceStatusEntryView(entry: entry)
        }
        .configurationDisplayName("Instance Status")
        .description("Which IntelliStock instances are running.")
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
