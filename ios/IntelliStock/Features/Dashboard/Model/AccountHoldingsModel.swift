import Foundation
import Observation

// Ported from features/dashboard/application/account_positions_controller.dart.


/// Which P&L each holding row shows: lifetime unrealized `total`, or `daily`
/// (since 12 AM, derived from the 1D sparkline) — `HoldingsPnlMode`.
nonisolated enum HoldingsPnlMode: String, Hashable, Sendable, CaseIterable {
    case total, daily

    /// Daily → intraday (since 12 AM); Total → full history.
    var sparkRange: String { self == .daily ? "1D" : "ALL" }

    /// The toggle's segment label.
    var label: String { self == .daily ? "Daily" : "Total" }
}

/// `_rangeForHoldingAge`: a `/symbol-historicals` range whose bar resolution
/// suits a holding `days` old, so a since-purchase slice has enough points.
/// Mirrors the backend range map.
nonisolated func rangeForHoldingAge(_ days: Int) -> String {
    if days <= 6 { return "1W" } // 10-min bars
    if days <= 31 { return "1M" } // hourly
    if days <= 93 { return "3M" } // daily
    if days <= 366 { return "1Y" } // daily
    return "ALL" // weekly
}

/// The body of `holdingsSparklinesProvider`: per-holding price sparklines
/// (`symbol → values`) for `range`.
nonisolated enum HoldingsSparklines {
    /// - 1D: one batch fetch, trimmed to the device's local midnight (fewer
    ///   than two post-midnight bars → the full series).
    /// - Any other range ("Total"): each holding from when it was bought,
    ///   fetched at a resolution matched to its age, anchored at the avg entry
    ///   and the current price so the line's direction matches the P&L.
    static func load(
        positions: [AccountPosition],
        range: String,
        opens: () async -> [String: Date],
        historicals: ([String], String) async throws -> [String: [HistPoint]],
        now: Date = Date(),
        calendar: Calendar = DartDateTime.localCalendar
    ) async throws -> [String: [Double]] {
        let symbols = positions.map(\.symbol).filter { !$0.isEmpty }
        if symbols.isEmpty { return [:] }

        if range == "1D" {
            let hist = try await historicals(symbols, "1D")
            let midnight = calendar.startOfDay(for: now)
            var out: [String: [Double]] = [:]
            for (sym, pts) in hist {
                var all: [Double] = []
                var since: [Double] = []
                for p in pts {
                    all.append(p.value)
                    let t = parseDateTime(p.ts)
                    if t == nil || !(t! < midnight) { since.append(p.value) }
                }
                // Right after 12 AM there may be < 2 post-midnight bars → fall back.
                let vals = since.count >= 2 ? since : all
                if vals.count >= 2 { out[sym] = vals }
            }
            return out
        }

        let openDates = await opens()
        // Cost basis + current price per symbol, to anchor the series ends.
        var entryOf: [String: Double?] = [:]
        var lastOf: [String: Double?] = [:]
        for p in positions {
            entryOf[p.symbol] = p.avgEntryPrice
            lastOf[p.symbol] = p.lastPrice
        }

        // Group by age-matched range, keeping first-seen order (Dart's
        // insertion-ordered map); one batch per range, one after another.
        var rangeOrder: [String] = []
        var byRange: [String: [String]] = [:]
        for s in symbols {
            let r: String
            if let boughtAt = openDates[s] {
                let days = Int((now.timeIntervalSince(boughtAt) / 86_400).rounded(.towardZero))
                r = rangeForHoldingAge(days)
            } else {
                r = "3M"
            }
            if byRange[r] == nil { rangeOrder.append(r) }
            byRange[r, default: []].append(s)
        }

        var out: [String: [Double]] = [:]
        for r in rangeOrder {
            let hist = try await historicals(byRange[r] ?? [], r)
            for (sym, pts) in hist {
                let boughtAt = openDates[sym]
                var all: [Double] = []
                var sinceBuy: [Double] = []
                for p in pts {
                    all.append(p.value)
                    let t = parseDateTime(p.ts)
                    if let boughtAt, t == nil || !(t! < boughtAt) {
                        sinceBuy.append(p.value)
                    }
                }
                if boughtAt != nil {
                    var series: [Double] = []
                    if let ent = entryOf[sym] ?? nil, ent > 0 { series.append(ent) }
                    series.append(contentsOf: sinceBuy)
                    if let last = lastOf[sym] ?? nil, last > 0 { series.append(last) }
                    if series.count >= 2 {
                        out[sym] = series
                        continue
                    }
                }
                // No purchase date (or too few points) → full age-matched series.
                if all.count >= 2 { out[sym] = all }
            }
        }
        return out
    }
}

/// One account's uninvested cash + holdings, polled so the dashboard's
/// Holdings section stays live — `AccountHoldingsNotifier`, plus the
/// account-scoped `holdingOpensProvider` and `holdingsSparklinesProvider`.
///
/// Lifecycle-aware polling every 15 s; a failed poll keeps the last good
/// data. One instance per selected account (the Dart family was keyed by
/// brokerage id and auto-disposed on switch).
@Observable
final class AccountHoldingsModel {
    static let interval: Duration = .seconds(15)

    let brokerageId: String
    private(set) var holdings: Loadable<AccountHoldings> = .loading

    /// The sparkline map for the range most recently requested, nil while it
    /// loads (`sparksAsync.valueOrNull`).
    private(set) var freshSparks: [String: [Double]]?
    /// The range `freshSparks` was requested for.
    private(set) var sparksRange: String?
    /// The last non-nil sparkline map, kept so a Daily/Total toggle animates
    /// instead of blanking every row (`_lastSparks`).
    private(set) var lastSparks: [String: [Double]]?

    @ObservationIgnored private let repository: () -> DashboardRepository
    @ObservationIgnored private let live: () -> LiveRepository
    @ObservationIgnored private let now: () -> Date
    @ObservationIgnored private var firstLoad: Task<AccountHoldings, any Error>?
    @ObservationIgnored private var opensTask: Task<[String: Date], Never>?
    @ObservationIgnored private var sparkTasks: [String: Task<[String: [Double]], any Error>] = [:]

    init(
        brokerageId: String,
        repository: @escaping () -> DashboardRepository,
        live: @escaping () -> LiveRepository,
        now: @escaping () -> Date = Date.init
    ) {
        self.brokerageId = brokerageId
        self.repository = repository
        self.live = live
        self.now = now
    }

    /// The sparks to draw: fresh when loaded, else the previous curves.
    var displayedSparks: [String: [Double]]? { freshSparks ?? lastSparks }

    // MARK: Holdings (AccountHoldingsNotifier)

    /// The first fetch, shared by every caller (Riverpod's `.future`); after
    /// that, the latest polled value.
    func currentHoldings() async throws -> AccountHoldings {
        if let value = holdings.value { return value }
        if let firstLoad { return try await firstLoad.value }
        let id = brokerageId
        let repo = repository()
        let task = Task { () async throws -> AccountHoldings in
            try await repo.accountHoldings(id)
        }
        firstLoad = task
        do {
            let value = try await task.value
            if holdings.value == nil { holdings = .loaded(value) }
            return holdings.value ?? value
        } catch {
            firstLoad = nil
            if holdings.value == nil, !error.isCancellationOrTaskCancelled { holdings = .failed(error) }
            throw error
        }
    }

    /// One poll tick: replaces the data on success, keeps it on failure.
    func refresh() async {
        do {
            holdings = .loaded(try await repository().accountHoldings(brokerageId))
        } catch {
            // keep the last good data on a transient failure
        }
    }

    /// First fetch (unless already loaded), then every 15 s until cancelled,
    /// pausing in the background.
    func poll(lifecycle: AppLifecycle?, sleep: @escaping PollingSleep = realPollingSleep) async {
        if holdings.value == nil { _ = try? await currentHoldings() }
        await PollingLoop(interval: { Self.interval }, sleep: sleep) { [weak self] in
            await self?.refresh()
        }
        .run(lifecycle: lifecycle)
    }

    // MARK: Opens + sparklines

    /// `holdingOpensProvider`: never throws, `{}` on failure. Fetched once.
    func holdingOpens() async -> [String: Date] {
        if let opensTask { return await opensTask.value }
        let id = brokerageId
        let repo = live()
        let task = Task { () async -> [String: Date] in
            (try? await repo.holdingOpens(id)) ?? [:]
        }
        opensTask = task
        return await task.value
    }

    /// `holdingsSparklinesProvider((id, range)).future`: shares an in-flight
    /// or finished fetch for the same range.
    func sparklines(_ range: String) async throws -> [String: [Double]] {
        if let task = sparkTasks[range] { return try await task.value }
        let task = Task { [weak self] () async throws -> [String: [Double]] in
            guard let self else { return [:] }
            let holdings = try await self.currentHoldings()
            let live = self.live()
            return try await HoldingsSparklines.load(
                positions: holdings.positions,
                range: range,
                opens: { [weak self] in await self?.holdingOpens() ?? [:] },
                historicals: { try await live.symbolHistoricals($0, $1) },
                now: self.now()
            )
        }
        sparkTasks[range] = task
        do {
            return try await task.value
        } catch {
            sparkTasks[range] = nil
            throw error
        }
    }

    /// Shows `range`'s sparklines in the holdings list. A new range (the
    /// toggle) re-fetches — the Dart family was auto-disposed when unwatched —
    /// while the previous curves stay drawn.
    func showSparks(_ range: String) async {
        if sparksRange != range {
            sparksRange = range
            freshSparks = nil
            sparkTasks[range] = nil
        } else if freshSparks != nil {
            return
        }
        do {
            let value = try await sparklines(range)
            guard sparksRange == range else { return }
            freshSparks = value
            lastSparks = value
        } catch {
            // `valueOrNull` stays nil: the previous curves keep showing.
        }
    }

    /// Pull-to-refresh: refetch holdings and the current sparklines.
    func reload() async {
        await refresh()
        if let range = sparksRange {
            sparkTasks[range] = nil
            sparksRange = nil
            await showSparks(range)
        }
    }
}

/// One holding row's P&L for the current mode (`_HoldingRow.build`).
nonisolated struct HoldingRowPnl: Equatable, Sendable {
    let hasPnl: Bool
    let abs: Double
    let pct: Double

    /// Daily is derived from the 1D sparkline (start-of-day → now); Total is
    /// the lifetime unrealized P&L.
    init(position p: AccountPosition, spark: [Double]?, mode: HoldingsPnlMode) {
        let daily = mode == .daily
        var ratio: Double?
        if let s = spark, s.count >= 2, s[0] != 0 { ratio = s[s.count - 1] / s[0] }
        hasPnl = !daily || ratio != nil
        abs = daily ? (ratio.map { p.marketValue * (1 - 1 / $0) } ?? 0) : p.unrealizedPnl
        pct = daily ? (ratio.map { ($0 - 1) * 100 } ?? 0) : p.unrealizedPnlPct
    }

    var up: Bool { abs >= 0 }

    /// `'${fmtPnl(abs)} · ${fmtPct(pct)}'` or `—`.
    var label: String { hasPnl ? "\(fmtPnl(abs)) · \(fmtPct(pct))" : "—" }
}

/// Dashboard text helpers ported from the private Dart getters.
nonisolated enum DashboardFormat {
    /// `_HoldingRow._qtyLabel`: `5 shares`, `1 share`, `2.50 shares`.
    static func qtyLabel(_ q: Double) -> String {
        "\(qtyNumber(q)) \(q == 1 ? "share" : "shares")"
    }

    /// The holdings row's short quantity, so the full count always fits:
    /// `22.4 sh`, `5.29 sh`, `5 sh` (redesign spec 2026-10-02).
    static func qtyShort(_ q: Double) -> String {
        "\(qtyCompact(q)) sh"
    }

    /// `qtyNumber` without trailing zeros: `22.4`, `5.29`, `82`.
    static func qtyCompact(_ q: Double) -> String {
        var s = qtyNumber(q)
        if s.contains(".") {
            while s.hasSuffix("0") { s.removeLast() }
            if s.hasSuffix(".") { s.removeLast() }
        }
        return s
    }

    /// Whole quantities without decimals, else 2 dp.
    static func qtyNumber(_ q: Double) -> String {
        q == q.rounded() ? String(Int(q)) : dartToStringAsFixed(q, 2)
    }

    /// `_AllocationRing._label` / `_DiversityGauge._label` — the design
    /// system's `AllocationRing.percentLabel`, which the ring draws.
    static func allocationLabel(_ fraction: Double) -> String {
        AllocationRing.percentLabel(fraction)
    }

    /// `_accountLabel`.
    static func accountLabel(_ a: BrokerageAccount) -> String {
        if a.brokerageType == "alpaca" {
            return a.alpacaPaper ? "Alpaca · Paper" : "Alpaca"
        }
        return a.accountName.isEmpty ? a.brokerageType : a.accountName
    }

    /// The account's own name (`Swing Trade Paper`), for the hero's account
    /// label and the portfolio sheet; `accountLabel` when it has none. Three
    /// Alpaca paper accounts all read "Alpaca · Paper" in `accountLabel`, so
    /// a list of them needs the names; Live or Paper is the sheet's subtitle.
    static func accountName(_ a: BrokerageAccount) -> String {
        let name = a.accountName.trimmingCharacters(in: .whitespacesAndNewlines)
        return name.isEmpty ? accountLabel(a) : name
    }

    /// The selected account: the stored id when it is in the list, else the
    /// first (`_resolveSelected`). nil only for an empty list.
    static func resolveSelected(_ accounts: [BrokerageAccount], _ id: String?) -> BrokerageAccount? {
        if let id, let match = accounts.first(where: { $0.id == id }) { return match }
        return accounts.first
    }

    /// `_IndexCard._fmtLevel`: thousands-separated, 2 dp (`7,489.78`).
    static func indexLevel(_ v: Double) -> String {
        let s = dartToStringAsFixed(v, 2)
        let parts = s.split(separator: ".", omittingEmptySubsequences: false).map(String.init)
        let intPart = Array(parts[0])
        var buf = ""
        for i in intPart.indices {
            if i > 0, (intPart.count - i) % 3 == 0 { buf.append(",") }
            buf.append(intPart[i])
        }
        return "\(buf).\(parts.count > 1 ? parts[1] : "")"
    }

    /// `ServiceCard._pillLabel`: first letter upper-cased; empty → `Stopped`.
    static func pillLabel(_ s: String) -> String {
        guard let first = s.first else { return "Stopped" }
        return first.uppercased() + s.dropFirst()
    }

    /// Strategy section `_agoLabel`: `ended today` / `ended Nd ago`; empty when
    /// absent or unparseable.
    static func endedAgo(_ iso: String?, now: Date = Date()) -> String {
        guard let iso, !iso.isEmpty, let dt = DartDateTime.tryParse(iso) else { return "" }
        let days = Int((now.timeIntervalSince(dt) / 86_400).rounded(.towardZero))
        if days <= 0 { return "ended today" }
        return "ended \(days)d ago"
    }
}
