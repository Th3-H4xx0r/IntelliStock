// PortfolioWidget — IntelliStock iOS home-screen widgets (iOS 17+).
// Design: "Premium Editorial" (V5) — airy spacing, light-weight large value,
// curve + positions; 2×2 = curve on top half, positions on bottom half.
//
// Reads App-Group UserDefaults keys written by the Flutter WidgetSyncService:
//   "accounts_data"  → [WidgetAccount] JSON  (selectable portfolios + positions)
//   "instances_data" → [WidgetInstance] JSON
// See mobile/docs/NATIVE_SETUP.md.

import WidgetKit
import SwiftUI
import AppIntents

private let kAppGroup = "group.dev.pkrishna.intellistock"

// MARK: - Palette (IntelliStock dark theme)
private let cHi    = Color(red: 0.945, green: 0.961, blue: 0.976)
private let cDim   = Color(red: 0.58,  green: 0.64,  blue: 0.72)
private let cFaint = Color(red: 0.39,  green: 0.45,  blue: 0.55)
private let cGreen = Color(red: 0.204, green: 0.827, blue: 0.600)
private let cRed   = Color(red: 0.973, green: 0.443, blue: 0.443)
private let widgetBG = LinearGradient(
    colors: [Color(red: 0.090, green: 0.055, blue: 0.176),
             Color(red: 0.043, green: 0.031, blue: 0.094)],
    startPoint: .top, endPoint: .bottom)

private func dbl(_ v: Any?) -> Double { (v as? NSNumber)?.doubleValue ?? 0 }

private func money(_ v: Double) -> String {
    let f = NumberFormatter()
    f.numberStyle = .currency
    f.currencySymbol = "$"
    f.minimumFractionDigits = 2
    f.maximumFractionDigits = 2
    return f.string(from: NSNumber(value: v)) ?? String(format: "$%.2f", v)
}

// MARK: - Data

struct ChartPt: Identifiable {
    let id = UUID()
    let t: Double      // epoch seconds (x-axis is mapped over the data's own range)
    let value: Double
}

struct PositionData: Identifiable {
    let id = UUID()
    let symbol: String
    let pnlPct: Double
    let marketValue: Double
}

struct AccountData {
    let id: String
    let label: String
    let value: Double
    let pnlAbs: Double
    let pnlPct: Double
    let points: [ChartPt]
    let positions: [PositionData]
}

private func readAccounts() -> [AccountData] {
    let d = UserDefaults(suiteName: kAppGroup)
    guard let raw = d?.string(forKey: "accounts_data"),
          let data = raw.data(using: .utf8),
          let arr = (try? JSONSerialization.jsonObject(with: data)) as? [[String: Any]]
    else { return [] }
    return arr.map { j in
        let pts = (j["intradayPoints"] as? [[String: Any]] ?? [])
            .map { ChartPt(t: dbl($0["t"]), value: dbl($0["v"])) }
        let pos = (j["positions"] as? [[String: Any]] ?? []).map {
            PositionData(symbol: $0["symbol"] as? String ?? "",
                         pnlPct: dbl($0["unrealizedPnlPct"]),
                         marketValue: dbl($0["marketValue"]))
        }
        .sorted { $0.marketValue > $1.marketValue }  // biggest holdings first
        return AccountData(
            id: j["id"] as? String ?? "",
            label: j["label"] as? String ?? "Portfolio",
            value: dbl(j["accountValue"]),
            pnlAbs: dbl(j["dayPnlAbs"]),
            pnlPct: dbl(j["dayPnlPct"]),
            points: pts,
            positions: pos)
    }
}

private func resolveAccount(_ id: String?) -> AccountData? {
    let all = readAccounts()
    if let id = id, let m = all.first(where: { $0.id == id }) { return m }
    return all.first
}

// MARK: - Self-refresh
//
// The widget fetches fresh data itself on each timeline reload, so it stays
// current even when the Flutter app isn't open (the app writes the API base +
// auth token into the App Group on login). On any failure it keeps the last
// cached App-Group data. iOS still governs HOW OFTEN reloads happen (budgeted,
// ~every 15-30 min in the background) — opening the app forces an instant one.
private func fetchAndCacheAccounts() async {
    let d = UserDefaults(suiteName: kAppGroup)
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
        // Keep the cached App-Group data on any network/parse failure.
    }
}

// MARK: - Timeline entry

struct PortfolioEntry: TimelineEntry {
    let date: Date
    let label: String
    let value: Double
    let pnlAbs: Double
    let pnlPct: Double
    let points: [ChartPt]
    let positions: [PositionData]
    let syncedAt: Double
    let hasData: Bool
}

private func lastSyncedAt() -> Double {
    UserDefaults(suiteName: kAppGroup)?.double(forKey: "synced_at") ?? 0
}

/// "30s ago" / "1m ago" / "2h ago" — relative time since the app last synced.
private func relativeAgo(_ epoch: Double) -> String {
    guard epoch > 0 else { return "" }
    let diff = max(0, Date().timeIntervalSince1970 - epoch)
    if diff < 5 { return "Just now" }
    if diff < 60 { return "\(Int(diff))s ago" }
    if diff < 3600 { return "\(Int(diff / 60))m ago" }
    if diff < 86400 { return "\(Int(diff / 3600))h ago" }
    return "\(Int(diff / 86400))d ago"
}

private func portfolioEntry(for id: String?, date: Date = Date()) -> PortfolioEntry {
    let synced = lastSyncedAt()
    if let a = resolveAccount(id) {
        return PortfolioEntry(date: date, label: a.label, value: a.value,
                              pnlAbs: a.pnlAbs, pnlPct: a.pnlPct,
                              points: a.points, positions: a.positions,
                              syncedAt: synced, hasData: true)
    }
    return PortfolioEntry(date: date, label: "Portfolio", value: 0, pnlAbs: 0,
                          pnlPct: 0, points: [], positions: [], syncedAt: synced, hasData: false)
}

// MARK: - View (V5 Premium Editorial, per family)

struct PortfolioWidgetView: View {
    @Environment(\.widgetFamily) private var family
    let entry: PortfolioEntry

    private var trend: Color { entry.pnlAbs >= 0 ? cGreen : cRed }
    private var pctText: String {
        "\(entry.pnlPct >= 0 ? "+" : "−")\(String(format: "%.2f%%", abs(entry.pnlPct)))"
    }
    private var absText: String {
        "\(entry.pnlAbs >= 0 ? "+$" : "−$")\(String(format: "%.2f", abs(entry.pnlAbs)))"
    }

    var body: some View {
        switch family {
        case .accessoryRectangular:
            rectangularLock.containerBackground(.clear, for: .widget)
        case .accessoryInline:
            inlineLock
        default:
            systemBody
        }
    }

    private var systemBody: some View {
        Group {
            if !entry.hasData {
                empty
            } else {
                switch family {
                case .systemSmall:  small
                case .systemMedium: medium
                default:            large
                }
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .overlay(alignment: .bottomTrailing) {
            if entry.hasData, !relativeAgo(entry.syncedAt).isEmpty {
                Text(relativeAgo(entry.syncedAt))
                    .font(.system(size: 9)).foregroundColor(cFaint)
                    .padding(.trailing, 10).padding(.bottom, 7)
            }
        }
        .containerBackground(widgetBG, for: .widget)
    }

    // ── Lock-screen accessory widgets (rendered monochrome by iOS) ──
    @ViewBuilder
    private var rectangularLock: some View {
        VStack(alignment: .leading, spacing: 1) {
            if entry.hasData {
                HStack(spacing: 3) {
                    Image(systemName: entry.pnlAbs >= 0 ? "arrowtriangle.up.fill" : "arrowtriangle.down.fill")
                        .font(.system(size: 9))
                    Text("$\(String(format: "%.2f", abs(entry.pnlAbs))) · \(String(format: "%.2f%%", abs(entry.pnlPct)))")
                        .font(.system(size: 12, weight: .semibold))
                }
                Text(money(entry.value)).font(.system(size: 17, weight: .bold))
                if !relativeAgo(entry.syncedAt).isEmpty {
                    Text(relativeAgo(entry.syncedAt))
                        .font(.system(size: 10)).foregroundStyle(.secondary)
                }
            } else {
                Text("IntelliStock").font(.system(size: 12, weight: .semibold))
                Text("Open app to sync").font(.system(size: 11)).foregroundStyle(.secondary)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
    }

    @ViewBuilder
    private var inlineLock: some View {
        if entry.hasData {
            Label {
                Text("\(money(entry.value)) · \(pctText)")
            } icon: {
                Image(systemName: entry.pnlAbs >= 0 ? "arrowtriangle.up.fill" : "arrowtriangle.down.fill")
            }
        } else {
            Text("IntelliStock — open app")
        }
    }

    // ── Empty state ──
    private var empty: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("INTELLISTOCK").font(.system(size: 10, weight: .semibold)).tracking(0.8).foregroundColor(cFaint)
            Spacer()
            Text("Open the app to sync your portfolio").font(.system(size: 12)).foregroundColor(cDim)
            Spacer()
        }
        .padding(12)
    }

    // ── 1×1 ──
    private var small: some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(entry.label.uppercased())
                .font(.system(size: 9, weight: .semibold)).tracking(0.7)
                .foregroundColor(cFaint).lineLimit(1)
            Text(money(entry.value))
                .font(.system(size: 22, weight: .semibold)).foregroundColor(cHi)
                .minimumScaleFactor(0.6).lineLimit(1)
            Text("\(pctText) today").font(.system(size: 11)).foregroundColor(trend).monospacedDigit()
            Spacer(minLength: 4)
            curve(height: 40)
        }
        .padding(12)
    }

    // ── 1×2 ──
    private var medium: some View {
        HStack(spacing: 16) {
            VStack(alignment: .leading, spacing: 3) {
                Text(entry.label.uppercased())
                    .font(.system(size: 9, weight: .semibold)).tracking(0.7).foregroundColor(cFaint).lineLimit(1)
                Text(money(entry.value)).font(.system(size: 24, weight: .semibold))
                    .foregroundColor(cHi).minimumScaleFactor(0.6).lineLimit(1)
                Text("\(absText) · \(pctText)").font(.system(size: 11)).foregroundColor(trend).monospacedDigit()
                Spacer(minLength: 6)
                curve(height: 30)
            }
            positionsGrid(limit: 6, columns: 2, fontSize: 11)
                .frame(maxWidth: .infinity)
        }
        .padding(12)
    }

    // ── 2×2 : curve top, positions bottom ──
    private var large: some View {
        VStack(spacing: 0) {
            VStack(alignment: .leading, spacing: 3) {
                Text("\(entry.label.uppercased()) · TODAY")
                    .font(.system(size: 10, weight: .semibold)).tracking(0.7).foregroundColor(cFaint).lineLimit(1)
                Text(money(entry.value)).font(.system(size: 32, weight: .semibold))
                    .foregroundColor(cHi).minimumScaleFactor(0.6).lineLimit(1)
                Text("\(absText) · \(pctText)").font(.system(size: 12)).foregroundColor(trend).monospacedDigit()
                Spacer(minLength: 8)
                curve(height: 50)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.bottom, 10)

            Divider().overlay(Color.white.opacity(0.10))

            positionsGrid(limit: 8, columns: 2, fontSize: 12.5)
                .padding(.top, 12)
                .frame(maxWidth: .infinity, alignment: .top)
        }
        .padding(14)
    }

    // ── Positions — condensed: no dot, P&L right next to the ticker, packed
    //    into `columns` so two fit side by side and more holdings show. ──
    private func positionsGrid(limit: Int, columns: Int, fontSize: CGFloat) -> some View {
        let items = Array(entry.positions.prefix(limit))
        let rows = stride(from: 0, to: items.count, by: columns).map {
            Array(items[$0..<min($0 + columns, items.count)])
        }
        return VStack(alignment: .leading, spacing: fontSize * 0.7) {
            if items.isEmpty {
                Text("No open positions").font(.system(size: fontSize)).foregroundColor(cFaint)
            } else {
                ForEach(rows.indices, id: \.self) { ri in
                    HStack(spacing: 12) {
                        ForEach(rows[ri]) { p in
                            HStack(spacing: 5) {
                                Text(p.symbol)
                                    .font(.system(size: fontSize, weight: .semibold, design: .monospaced))
                                    .foregroundColor(cHi)
                                Text("\(p.pnlPct >= 0 ? "+" : "−")\(String(format: "%.2f%%", abs(p.pnlPct)))")
                                    .font(.system(size: fontSize)).monospacedDigit()
                                    .foregroundColor(p.pnlPct >= 0 ? cGreen : cRed)
                            }
                            .lineLimit(1).minimumScaleFactor(0.8)
                            .frame(maxWidth: .infinity, alignment: .leading)
                        }
                        if rows[ri].count < columns {
                            Color.clear.frame(maxWidth: .infinity)
                        }
                    }
                }
            }
            Spacer(minLength: 0)
        }
    }

    // ── Curve — Canvas-drawn so the area reliably fades to transparent at the
    //    bottom of the tile. X-axis is the 09:30–16:00 ET session, so the line
    //    stops at "now" (empty space on the right until the session fills in).
    //    Dashed open / previous-close baseline runs full width. ──
    @ViewBuilder
    private func curve(height: CGFloat) -> some View {
        let pts = entry.points.sorted { $0.t < $1.t }
        if pts.count >= 2 {
            let open = entry.value - entry.pnlAbs
            let vals = pts.map(\.value) + [open]
            let lo = vals.min() ?? 0
            let hi = vals.max() ?? 1
            let range = max(hi - lo, 0.0001)
            // X-axis spans the data's own time range (works for RTH or the 24/5
            // continuous/overnight session), so the line fills the tile width.
            let tMin = pts.first!.t
            let tSpan = max(pts.last!.t - tMin, 1)
            Canvas { ctx, size in
                let w = size.width, h = size.height
                let padTop = h * 0.16, padBot = h * 0.06
                let plotH = max(h - padTop - padBot, 1)
                func px(_ t: Double) -> CGFloat { CGFloat((t - tMin) / tSpan) * w }
                func py(_ v: Double) -> CGFloat {
                    padTop + (1 - CGFloat((v - lo) / range)) * plotH
                }

                // Area under the line → bottom edge, faded to transparent.
                var area = Path()
                area.move(to: CGPoint(x: px(pts[0].t), y: h))
                for p in pts { area.addLine(to: CGPoint(x: px(p.t), y: py(p.value))) }
                area.addLine(to: CGPoint(x: px(pts.last!.t), y: h))
                area.closeSubpath()
                ctx.fill(area, with: .linearGradient(
                    Gradient(colors: [trend.opacity(0.45), trend.opacity(0.0)]),
                    startPoint: CGPoint(x: 0, y: padTop),
                    endPoint: CGPoint(x: 0, y: h)))

                // Line.
                var line = Path()
                line.move(to: CGPoint(x: px(pts[0].t), y: py(pts[0].value)))
                for p in pts.dropFirst() {
                    line.addLine(to: CGPoint(x: px(p.t), y: py(p.value)))
                }
                ctx.stroke(line, with: .color(trend),
                           style: StrokeStyle(lineWidth: 2, lineCap: .round, lineJoin: .round))

                // Dashed open / previous-close baseline, full width.
                var base = Path()
                base.move(to: CGPoint(x: 0, y: py(open)))
                base.addLine(to: CGPoint(x: w, y: py(open)))
                ctx.stroke(base, with: .color(.white.opacity(0.18)),
                           style: StrokeStyle(lineWidth: 1, dash: [2, 3]))
            }
            .frame(height: height)
        } else {
            Color.clear.frame(height: height)
        }
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
        // Pull fresh data ourselves so the widget doesn't depend on the app being open.
        await fetchAndCacheAccounts()
        let e = portfolioEntry(for: configuration.account?.id)
        // iOS budgets background widget reloads (~15-30 min apart); requesting
        // sub-10-min just burns the budget and makes it refresh LESS. Floor at 10m.
        let interval = max(configuration.refresh.seconds, 600)
        let next = Date().addingTimeInterval(interval)
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
        .description("Live portfolio value, day P&L & positions. Long-press → Edit to pick a portfolio and refresh rate.")
        .supportedFamilies([
            .systemSmall, .systemMedium, .systemLarge,
            .accessoryRectangular, .accessoryInline,
        ])
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
                Text("INSTANCES").font(.system(size: 9, weight: .semibold)).tracking(0.7).foregroundColor(cFaint)
                ForEach(Array(entry.instances.prefix(3).enumerated()), id: \.offset) { _, inst in
                    if let name = inst["name"] as? String {
                        HStack(spacing: 6) {
                            Circle()
                                .fill((inst["running"] as? Bool ?? false) ? cGreen : Color.gray)
                                .frame(width: 6, height: 6)
                            Text(name).font(.system(size: 12)).foregroundColor(cHi).lineLimit(1)
                        }
                    }
                }
                if entry.instances.isEmpty {
                    Text("No instances").font(.system(size: 12)).foregroundColor(cFaint)
                }
                Spacer(minLength: 0)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            .padding(12)
            .containerBackground(widgetBG, for: .widget)
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
