import Foundation
import Observation

// Ported from features/stock/application/stock_controller.dart and the
// pure helpers of features/stock/presentation/stock_screen.dart.

/// The stock screen's ranges.
nonisolated let stockRanges = ["1D", "1W", "1M", "3M", "YTD", "1Y", "ALL"]

/// A symbol's price series for a range — `StockSeries`.
nonisolated struct StockSeries: Hashable, Sendable {
    let ts: [Date]
    let vals: [Double]
}

/// One contributing strategy behind a bot decision (who else agreed).
nonisolated struct BotContributor: Hashable, Sendable {
    let strategy: String?
    let weight: Double?
    let reason: String?

    init(json j: JSON) {
        strategy = j["strategy"].string
        weight = j["weight"].double
        reason = j["reason"].string
    }
}

/// One real buy/sell the bot made for a symbol, with the reasoning that
/// drove it — from the backend's `BotTradeDecisions` log.
nonisolated struct BotTradeEvent: Hashable, Sendable {
    let symbol: String
    /// "buy" | "sell"
    let side: String
    let ts: Date?
    let price: Double?
    let strategy: String?
    let actionIntent: String?
    let reason: String
    let score: Double?
    let overrideApplied: Bool
    let contributors: [BotContributor]

    init(json j: JSON) {
        symbol = j["symbol"].stringOr("")
        side = j["side"].stringOr("buy")
        // Fall back to created_at when ts is missing/null/blank/unparseable.
        ts = parseDateTime(j["ts"]) ?? parseDateTime(j["created_at"])
        price = j["price"].double
        strategy = j["strategy"].string
        actionIntent = j["action_intent"].string
        reason = j["reason"].stringOr("")
        score = j["score"].double
        overrideApplied = j["override_applied"].bool
        contributors = j["contributors"].objectElements.map(BotContributor.init(json:))
    }

    var isBuy: Bool { side.lowercased() == "buy" }

    /// The driving strategy, else `Buy decision` / `Sell decision`.
    var title: String {
        let s = (strategy ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        return s.isEmpty ? (isBuy ? "Buy decision" : "Sell decision") : s
    }

    /// Other strategies that voted the same way (excluding the title), up
    /// to 3, first-seen order.
    var backers: [String] {
        var seen = Set<String>()
        var out: [String] = []
        for c in contributors {
            let s = (c.strategy ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
            guard !s.isEmpty, s != title, seen.insert(s).inserted else { continue }
            out.append(s)
            if out.count == 3 { break }
        }
        return out
    }
}

/// `_compact`: `1.23T` / `4.56B` / `7.89M` / `1.2K` / `950`.
nonisolated func stockCompact(_ v: Double) -> String {
    let a = abs(v)
    if a >= 1e12 { return "\(dartToStringAsFixed(v / 1e12, 2))T" }
    if a >= 1e9 { return "\(dartToStringAsFixed(v / 1e9, 2))B" }
    if a >= 1e6 { return "\(dartToStringAsFixed(v / 1e6, 2))M" }
    if a >= 1e3 { return "\(dartToStringAsFixed(v / 1e3, 1))K" }
    return dartToStringAsFixed(v, 0)
}

/// `_statsCard`'s cells: label → formatted value, zero/absent numbers
/// skipped, the range's open/high/low from the series.
nonisolated func stockStatCells(info: JSONObject, series: StockSeries?, range: String) -> [(label: String, value: String)] {
    var cells: [(label: String, value: String)] = []
    func add(_ label: String, _ key: String, _ format: (Num) -> String) {
        guard let raw = info[key], let n = raw.num, n.double != 0 else { return }
        cells.append((label, format(n)))
    }
    let money: (Num) -> String = { fmtMoney($0.double) }
    add("Prev close", "previousClose", money)
    if let v = series?.vals, v.count >= 2 {
        cells.append(("Open", fmtMoney(v[0])))
        cells.append(("\(range) high", fmtMoney(v.max()!)))
        cells.append(("\(range) low", fmtMoney(v.min()!)))
    }
    add("52W high", "fiftyTwoWeekHigh", money)
    add("52W low", "fiftyTwoWeekLow", money)
    add("Volume", "volume") { stockCompact($0.double) }
    add("Avg volume", "averageVolume") { stockCompact($0.double) }
    add("Market cap", "marketCap") { "$\(stockCompact($0.double))" }
    add("P/E", "trailingPE") { dartToStringAsFixed($0.double, 2) }
    add("Fwd P/E", "forwardPE") { dartToStringAsFixed($0.double, 2) }
    add("Beta", "beta") { dartToStringAsFixed($0.double, 2) }
    add("Analyst target", "targetMeanPrice", money)
    return cells
}

/// A trimmed string field of the info map (`(info['x'] as String?) ?? ''`).
nonisolated func stockInfoText(_ info: JSONObject, _ key: String) -> String {
    if case .string(let s)? = info[key] { return s.trimmingCharacters(in: .whitespacesAndNewlines) }
    return ""
}

/// The stock screen's data: the live price series (`StockHistoryNotifier`),
/// the info map, the bot's decisions and the recent fills.
@Observable
final class StockModel {
    let symbol: String
    let brokerageId: String?
    private(set) var range = "1D"
    /// The current range's series (`histAsync`); data stays during polls.
    private(set) var history: Loadable<StockSeries> = .loading
    /// True while the current range's first fetch is in flight.
    private(set) var historyLoading = true
    /// `stockInfoProvider`; nil while loading, `{}` on failure.
    private(set) var info: JSONObject?
    /// `stockBotActivityProvider`; nil while loading.
    private(set) var botEvents: [BotTradeEvent]?
    /// `stockOrdersProvider`; nil while loading.
    private(set) var orders: [Trade]?
    var scrubIndex: Int?

    @ObservationIgnored private let client: () -> ApiClient
    @ObservationIgnored private let now: () -> Date

    init(symbol: String, brokerageId: String?, client: @escaping () -> ApiClient, now: @escaping () -> Date = Date.init) {
        self.symbol = symbol
        self.brokerageId = brokerageId
        self.client = client
        self.now = now
    }

    /// Poll cadence: 10 s on 1D, 30 s on longer ranges.
    var interval: Duration { range == "1D" ? .seconds(10) : .seconds(30) }

    var series: StockSeries? { history.value }

    func setRange(_ r: String) {
        guard r != range else { return }
        scrubIndex = nil
        range = r
        history = .loading
        historyLoading = true
    }

    // MARK: History (StockHistoryNotifier)

    /// `_fetch`: unparseable timestamps skipped; 1D trimmed to local midnight
    /// when that leaves ≥ 2 points, else the full series.
    nonisolated static func series(from points: [HistPoint], range: String, now: Date, calendar: Calendar = DartDateTime.localCalendar) -> StockSeries {
        let midnight: Date? = range == "1D" ? calendar.startOfDay(for: now) : nil
        var allTs: [Date] = []
        var allVals: [Double] = []
        var dayTs: [Date] = []
        var dayVals: [Double] = []
        for p in points {
            guard let t = parseDateTime(p.ts) else { continue }
            allTs.append(t)
            allVals.append(p.value)
            if let midnight, !(t < midnight) {
                dayTs.append(t)
                dayVals.append(p.value)
            }
        }
        if midnight != nil, dayVals.count >= 2 { return StockSeries(ts: dayTs, vals: dayVals) }
        return StockSeries(ts: allTs, vals: allVals)
    }

    private func fetch(_ range: String) async throws -> StockSeries {
        let map = try await LiveRepository(client: client()).symbolHistoricals([symbol], range)
        return Self.series(from: map[symbol] ?? [], range: range, now: now())
    }

    func loadHistory() async {
        let r = range
        do {
            let s = try await fetch(r)
            guard r == range else { return }
            history = .loaded(s)
        } catch {
            guard r == range, !error.isCancellationOrTaskCancelled else { return }
            if history.value == nil { history = .failed(error) }
        }
        if r == range { historyLoading = false }
    }

    func refreshHistory() async {
        let r = range
        do {
            let s = try await fetch(r)
            guard r == range else { return }
            history = .loaded(s)
        } catch {
            // keep the last good series on a transient poll failure
        }
    }

    /// First fetch (unless loaded), then the range's cadence; restart it when
    /// the range changes.
    func pollHistory(lifecycle: AppLifecycle?, sleep: @escaping PollingSleep = realPollingSleep) async {
        if history.value == nil { await loadHistory() }
        await PollingLoop(interval: { [weak self] in self?.interval ?? .seconds(30) }, sleep: sleep) { [weak self] in
            await self?.refreshHistory()
        }
        .run(lifecycle: lifecycle)
    }

    // MARK: Info, bot activity, orders

    /// Loads the info, bot activity and orders once (never throwing).
    func loadDetails() async {
        let c = client()
        let sym = symbol
        let bid = brokerageId ?? ""
        let needInfo = info == nil
        let needEvents = botEvents == nil && brokerageId != nil
        let needOrders = orders == nil && brokerageId != nil
        async let infoValue: JSONObject? = needInfo ? Self.info(c, sym) : nil
        async let events: [BotTradeEvent]? = needEvents ? Self.botActivity(c, bid, sym) : nil
        async let fills: [Trade]? = needOrders ? Self.orders(c, bid, sym) : nil
        let (i, e, o) = await (infoValue, events, fills)
        if Task.isCancelled { return }
        if let i { info = i }
        if let e { botEvents = e }
        if let o { orders = o }
    }

    /// `stockInfoProvider`: `GET /symbols/{sym}/info`, `{}` on failure.
    nonisolated static func info(_ client: ApiClient, _ symbol: String) async -> JSONObject {
        (try? await client.get("/symbols/\(symbol)/info"))?.orderedObject ?? JSONObject()
    }

    /// `stockBotActivityProvider`: `GET /brokerages/{id}/bot-activity?symbol=&per_page=20`.
    nonisolated static func botActivity(_ client: ApiClient, _ brokerageId: String, _ symbol: String) async -> [BotTradeEvent] {
        do {
            let data = try await client.get(
                "/brokerages/\(brokerageId)/bot-activity",
                query: ["symbol": .string(symbol), "per_page": 20]
            )
            guard data.isObject else { return [] }
            return data["events"].objectElements.map(BotTradeEvent.init(json:))
        } catch {
            return []
        }
    }

    /// `stockOrdersProvider`: `GET /brokerages/{id}/orders?symbol=`.
    nonisolated static func orders(_ client: ApiClient, _ brokerageId: String, _ symbol: String) async -> [Trade] {
        do {
            let data = try await client.get("/brokerages/\(brokerageId)/orders", query: ["symbol": .string(symbol)])
            guard data.isObject else { return [] }
            return data["orders"].objectElements.map(Trade.init(json:))
        } catch {
            return []
        }
    }
}
