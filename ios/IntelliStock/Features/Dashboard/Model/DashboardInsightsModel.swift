import Foundation
import Observation

// Ported from features/dashboard/application/insights_controller.dart.

/// A market news headline from `GET /market/news` (Google News).
nonisolated struct NewsArticle: Hashable, Sendable, Identifiable {
    let title: String
    let source: String
    let url: String
    let publishedAt: Date?

    var id: String { "\(title)|\(url)" }

    init(title: String, source: String = "", url: String = "", publishedAt: Date? = nil) {
        self.title = title
        self.source = source
        self.url = url
        self.publishedAt = publishedAt
    }

    init(json j: JSON) {
        self.init(
            title: j["title"].stringOr(""),
            source: j["source"].stringOr(""),
            url: j["url"].stringOr(""),
            publishedAt: parseDateTime(j["published_at"])
        )
    }
}

/// `MarketMover`: one screener row.
nonisolated struct MarketMover: Hashable, Sendable {
    let symbol: String
    let pct: Double?
    let price: Double?
}

/// `MoversData`: the account screener's gainers and losers.
nonisolated struct MoversData: Hashable, Sendable {
    let gainers: [MarketMover]
    let losers: [MarketMover]

    static let empty = MoversData(gainers: [], losers: [])
}

/// `MomentumPick`: one ranked nexus momentum name.
nonisolated struct MomentumPick: Hashable, Sendable {
    let symbol: String
    let score: Double
}

/// A market instrument's today snapshot (index or sector ETF): a display
/// label, today's % move, and the intraday series for a mini sparkline.
nonisolated struct MarketQuote: Hashable, Sendable, Identifiable {
    let symbol: String
    let label: String
    let pct: Double
    var values: [Double] = []

    var id: String { symbol }
}

/// Today's account change ($ and %) vs local midnight — `DayChange`.
nonisolated struct DayChange: Hashable, Sendable {
    let abs: Double
    let pct: Double?
}

/// Major index proxies, in display order (`_indexSymbols`).
nonisolated let dashboardIndexSymbols: [(symbol: String, label: String)] = [
    ("SPY", "S&P 500"),
    ("QQQ", "Nasdaq"),
    ("DIA", "Dow"),
    ("IWM", "Russell 2000"),
]

/// The 11 SPDR sector ETFs → sector display names (`_sectorEtfs`).
nonisolated let dashboardSectorEtfs: [(symbol: String, label: String)] = [
    ("XLK", "Technology"),
    ("XLF", "Financials"),
    ("XLV", "Health Care"),
    ("XLY", "Consumer Disc."),
    ("XLC", "Communication"),
    ("XLI", "Industrials"),
    ("XLP", "Consumer Staples"),
    ("XLE", "Energy"),
    ("XLU", "Utilities"),
    ("XLRE", "Real Estate"),
    ("XLB", "Materials"),
]

/// The insights providers' bodies. Every method that the Dart wrapped in a
/// catch-all returns its empty value instead of throwing.
nonisolated struct DashboardInsightsLoader: Sendable {
    let client: ApiClient

    var dashboard: DashboardRepository { DashboardRepository(client: client) }
    var live: LiveRepository { LiveRepository(client: client) }

    /// `marketNewsProvider`: `GET /market/news?limit=15`.
    func marketNews() async -> [NewsArticle] {
        do {
            let data = try await client.get("/market/news", query: ["limit": 15])
            guard data.isObject else { return [] }
            return data["articles"].objectElements.map(NewsArticle.init(json:)).filter { !$0.title.isEmpty }
        } catch {
            return []
        }
    }

    /// `marketMoversProvider(id)`: `GET /brokerages/{id}/movers?top=6`.
    func marketMovers(_ brokerageId: String) async -> MoversData {
        func parse(_ l: JSON) -> [MarketMover] {
            l.objectElements
                .map { MarketMover(symbol: $0["symbol"].stringOr(""), pct: $0["pct"].double, price: $0["price"].double) }
                .filter { !$0.symbol.isEmpty }
        }
        do {
            let data = try await client.get("/brokerages/\(brokerageId)/movers", query: ["top": 6])
            guard data.isObject else { return .empty }
            return MoversData(gainers: parse(data["gainers"]), losers: parse(data["losers"]))
        } catch {
            return .empty
        }
    }

    /// `nexusMomentumProvider(id)`: `GET /brokerages/{id}/nexus-momentum`.
    func nexusMomentum(_ brokerageId: String) async -> [MomentumPick] {
        do {
            let data = try await client.get("/brokerages/\(brokerageId)/nexus-momentum")
            guard data.isObject else { return [] }
            return data["momentum"].objectElements
                .map { MomentumPick(symbol: $0["symbol"].stringOr(""), score: $0["score"].double ?? 0) }
                .filter { !$0.symbol.isEmpty }
        } catch {
            return []
        }
    }

    /// `_quotesFor`: one 1D batch for the universe; symbols without data or
    /// without a computable move are skipped.
    func quotes(_ universe: [(symbol: String, label: String)], sortByPct: Bool) async throws -> [MarketQuote] {
        let hist = try await live.symbolHistoricals(universe.map(\.symbol), "1D")
        var out: [MarketQuote] = []
        for (sym, label) in universe {
            guard let pts = hist[sym] else { continue }
            let vals = pts.map(\.value)
            guard let pct = pctChangeOf(vals) else { continue }
            out.append(MarketQuote(symbol: sym, label: label, pct: pct, values: vals))
        }
        if sortByPct {
            // Dart's List.sort is not stable either; ties keep universe order here.
            out = out.enumerated()
                .sorted { $0.element.pct != $1.element.pct ? $0.element.pct > $1.element.pct : $0.offset < $1.offset }
                .map(\.element)
        }
        return out
    }

    /// `marketIndicesProvider`.
    func marketIndices() async -> [MarketQuote] {
        (try? await quotes(dashboardIndexSymbols, sortByPct: false)) ?? []
    }

    /// `sectorPerformanceProvider` (ranked best → worst).
    func sectorPerformance() async -> [MarketQuote] {
        (try? await quotes(dashboardSectorEtfs, sortByPct: true)) ?? []
    }

    /// `DayChangeNotifier._fetch`: 1D history re-based to local midnight.
    func dayChange(_ brokerageId: String, now: Date = Date()) async throws -> DayChange? {
        let h = try await dashboard.portfolioHistory(brokerageId, "1D").sinceLocalMidnight(now: now)
        if h.isEmpty { return nil }
        return DayChange(abs: h.changeAbs ?? 0, pct: h.changePct)
    }

    /// One `/symbols/{sym}/info` sector lookup, bounded to 8 s; nil on any
    /// failure or a non-string sector (`info['sector'] as String?`).
    func sector(_ symbol: String, timeout: Duration = .seconds(8)) async -> String? {
        let client = client
        return await withTaskGroup(of: String??.self) { group in
            group.addTask {
                guard let info = try? await client.get("/symbols/\(symbol)/info"), info.isObject else { return .some(nil) }
                if case .string(let s) = info["sector"] { return .some(s) }
                return .some(nil)
            }
            group.addTask {
                try? await Task.sleep(for: timeout)
                return nil
            }
            let first = await group.next() ?? nil
            group.cancelAll()
            return first ?? nil
        }
    }

    /// `riskMetricsProvider`: from the account's 1Y equity curve.
    func riskMetrics(_ brokerageId: String) async -> RiskMetrics {
        do {
            let h = try await dashboard.portfolioHistory(brokerageId, "1Y")
            return IntelliStock.riskMetrics(h.values)
        } catch {
            return RiskMetrics(volatility: 0, maxDrawdown: 0, sharpe: nil, points: 0)
        }
    }
}

/// `sectorAllocationProvider`'s aggregation step: positions with a symbol,
/// a positive value and no OCC option symbol.
nonisolated func sectorAllocationPositions(_ positions: [AccountPosition]) -> [AccountPosition] {
    positions.filter { !$0.symbol.isEmpty && $0.marketValue > 0 && !isOccOptionSymbol($0.symbol) }
}

/// `todaysMoversProvider`'s transform: `(last/first − 1) × 100` per holding
/// with ≥ 2 points and a non-zero first, ranked biggest gainer first.
/// `order` fixes the tie order (holdings order).
nonisolated func todaysMoversFromSparks(_ sparks: [String: [Double]], order: [String]) -> [Mover] {
    var pct: [(symbol: String, pct: Double)] = []
    for sym in order {
        guard let vals = sparks[sym], vals.count >= 2, vals[0] != 0 else { continue }
        pct.append((sym, (vals[vals.count - 1] / vals[0] - 1) * 100))
    }
    return todaysMovers(pct)
}

/// The dashboard's app-lifetime caches — the keepAlive and non-autoDispose
/// providers of `insights_controller.dart`, plus the global P&L mode and the
/// session sector cache. Owned by `DashboardView`, which lives as long as
/// the signed-in shell.
@Observable
final class DashboardFeedModel {
    /// `holdingsPnlModeProvider` (default Daily).
    var pnlMode: HoldingsPnlMode = .daily

    // Global market data (autoDispose in Dart, but built once while the
    // dashboard branch stayed mounted). nil = loading.
    private(set) var news: [NewsArticle]?
    private(set) var indices: [MarketQuote]?
    private(set) var sectorPerformance: [MarketQuote]?

    /// `sectorAllocationProvider` (keepAlive per account). Missing = loading.
    private(set) var sectorAllocation: [String: [SectorSlice]] = [:]
    /// `riskMetricsProvider` (keepAlive per account). Missing = loading.
    private(set) var risk: [String: RiskMetrics] = [:]
    /// `dayChangeProvider` (non-autoDispose per account). Missing = loading.
    private(set) var dayChange: [String: DayChange?] = [:]

    /// `_sectorCache`: symbol → sector, resolved once per app run.
    @ObservationIgnored private(set) var sectorCache: [String: String?] = [:]

    @ObservationIgnored private let loader: () -> DashboardInsightsLoader
    @ObservationIgnored private let now: () -> Date

    static let dayChangeInterval: Duration = .seconds(5)

    init(loader: @escaping () -> DashboardInsightsLoader, now: @escaping () -> Date = Date.init) {
        self.loader = loader
        self.now = now
    }

    // MARK: Global market data

    func loadMarket(force: Bool = false) async {
        let l = loader()
        let (needN, needI, needS) = (force || news == nil, force || indices == nil, force || sectorPerformance == nil)
        async let n: [NewsArticle]? = needN ? l.marketNews() : nil
        async let i: [MarketQuote]? = needI ? l.marketIndices() : nil
        async let s: [MarketQuote]? = needS ? l.sectorPerformance() : nil
        let (news, indices, sectors) = await (n, i, s)
        // A cancelled load (the dashboard went away) must not cache empties.
        if Task.isCancelled { return }
        if let news { self.news = news }
        if let indices { self.indices = indices }
        if let sectors { sectorPerformance = sectors }
    }

    // MARK: Day change (DayChangeNotifier)

    /// First fetch (once per account), then every 5 s while `brokerageId` is
    /// the visible account and the app is in the foreground. A failed poll
    /// keeps the last value.
    func pollDayChange(_ brokerageId: String, lifecycle: AppLifecycle?, sleep: @escaping PollingSleep = realPollingSleep) async {
        if dayChange.index(forKey: brokerageId) == nil {
            // A failed first fetch reads as nil (`—`), as the Dart catch did.
            do {
                dayChange[brokerageId] = .some(try await loader().dayChange(brokerageId, now: now()))
            } catch {
                if error.isCancellationOrTaskCancelled { return }
                dayChange[brokerageId] = .some(nil)
            }
        }
        await PollingLoop(interval: { Self.dayChangeInterval }, sleep: sleep) { [weak self] in
            await self?.refreshDayChange(brokerageId)
        }
        .run(lifecycle: lifecycle)
    }

    func refreshDayChange(_ brokerageId: String) async {
        do {
            let value = try await loader().dayChange(brokerageId, now: now())
            dayChange[brokerageId] = .some(value)
        } catch {
            // keep last good value on a transient poll failure
        }
    }

    /// Whether the TODAY tile is still on its first load.
    func dayChangeLoaded(_ brokerageId: String) -> Bool {
        dayChange.index(forKey: brokerageId) != nil
    }

    // MARK: Sector allocation + risk (keepAlive)

    /// `sectorAllocationProvider(id)`: never throws; `[]` on failure.
    func loadSectorAllocation(_ brokerageId: String, holdings: () async throws -> AccountHoldings, force: Bool = false) async {
        if !force, sectorAllocation[brokerageId] != nil { return }
        do {
            let positions = sectorAllocationPositions(try await holdings().positions)
            if positions.isEmpty {
                sectorAllocation[brokerageId] = []
                return
            }
            let l = loader()
            let misses = positions.map(\.symbol).filter { sectorCache.index(forKey: $0) == nil }
            let resolved = await withTaskGroup(of: (String, String?).self) { group in
                for sym in misses {
                    group.addTask { (sym, await l.sector(sym)) }
                }
                var out: [(String, String?)] = []
                for await pair in group { out.append(pair) }
                return out
            }
            if Task.isCancelled { return }
            for (sym, sector) in resolved { sectorCache[sym] = .some(sector) }
            let valueBySymbol = positions.map { (symbol: $0.symbol, value: $0.marketValue) }
            var sectors: [String: String?] = [:]
            for p in positions { sectors[p.symbol] = sectorCache[p.symbol] ?? nil }
            sectorAllocation[brokerageId] = aggregateBySector(valueBySymbol, sectors)
        } catch {
            if error.isCancellationOrTaskCancelled { return }
            sectorAllocation[brokerageId] = []
        }
    }

    /// `riskMetricsProvider(id)`.
    func loadRisk(_ brokerageId: String, force: Bool = false) async {
        if !force, risk[brokerageId] != nil { return }
        let value = await loader().riskMetrics(brokerageId)
        if Task.isCancelled { return }
        risk[brokerageId] = value
    }

    /// Pull-to-refresh: every cache for the account, and the market data.
    func refreshAll(_ brokerageId: String, holdings: () async throws -> AccountHoldings) async {
        async let market: Void = loadMarket(force: true)
        async let day: Void = refreshDayChange(brokerageId)
        async let riskLoad: Void = loadRisk(brokerageId, force: true)
        _ = await (market, day, riskLoad)
        await loadSectorAllocation(brokerageId, holdings: holdings, force: true)
    }
}

/// Per-account insights that were autoDispose families in Dart: rebuilt when
/// the selected account changes. nil = loading.
@Observable
final class DashboardAccountInsightsModel {
    let brokerageId: String
    private(set) var concentration: ConcentrationStats?
    private(set) var todaysMovers: [Mover]?
    private(set) var marketMovers: MoversData?
    private(set) var momentum: [MomentumPick]?

    @ObservationIgnored private let loader: () -> DashboardInsightsLoader

    init(brokerageId: String, loader: @escaping () -> DashboardInsightsLoader) {
        self.brokerageId = brokerageId
        self.loader = loader
    }

    /// Loads anything not yet loaded (`force` reloads all).
    func load(holdings: AccountHoldingsModel, force: Bool = false) async {
        let l = loader()
        let id = brokerageId
        let (needMovers, needPicks) = (force || marketMovers == nil, force || momentum == nil)
        async let movers: MoversData? = needMovers ? l.marketMovers(id) : nil
        async let picks: [MomentumPick]? = needPicks ? l.nexusMomentum(id) : nil
        if force || concentration == nil {
            // `concentrationProvider`: never throws.
            let h = try? await holdings.currentHoldings()
            if Task.isCancelled { return }
            if let h {
                concentration = IntelliStock.concentration(h.positions.map(\.marketValue))
            } else {
                concentration = ConcentrationStats(topWeight: 0, count: 0, hhi: 0, score: 0)
            }
        }
        if force || todaysMovers == nil {
            // `todaysMoversProvider`: from the 1D holdings sparklines.
            do {
                let sparks = try await holdings.sparklines("1D")
                let order = (try? await holdings.currentHoldings().positions.map(\.symbol)) ?? Array(sparks.keys).sorted()
                todaysMovers = todaysMoversFromSparks(sparks, order: order)
            } catch {
                if error.isCancellationOrTaskCancelled { return }
                todaysMovers = []
            }
        }
        let (m, p) = await (movers, picks)
        if Task.isCancelled { return }
        if let m { marketMovers = m }
        if let p { momentum = p }
    }
}
