import Foundation

// Ported from features/kalshi/data/kalshi_repository.dart. The Riverpod
// family providers declared there are view state, ported by the Kalshi
// feature's view models.

// MARK: - Models

nonisolated struct KalshiPortfolio: Hashable, Sendable {
    let value: Double
    let cash: Double
    let dayChange: Double
    /// Value points, chronological.
    let series: [Double]
    /// Matching timestamps for the scrubbable chart.
    let seriesTs: [Date]
    /// Paper P&L progress over time (present only for paper instances). When
    /// set, the hero shows this instead of the (static demo) portfolio value.
    let paperPnl: Double?
    let paperSeries: [Double]
    let paperSeriesTs: [Date]

    var isPaper: Bool { !paperSeries.isEmpty }

    init(json j: JSON) {
        let raw = j["series"].arrayValue
        let praw = j["paper_series"].arrayValue
        value = j["value"].doubleOr(0)
        cash = j["cash"].doubleOr(0)
        dayChange = j["day_change"].doubleOr(0)
        series = raw.map { $0["value"].doubleOr(0) }
        seriesTs = raw.map { DartDateTime.tryParse($0["ts"].stringOr("")) ?? Date() }
        paperPnl = j["paper_pnl"].double
        paperSeries = praw.map { $0["pnl"].doubleOr(0) }
        paperSeriesTs = praw.map { DartDateTime.tryParse($0["ts"].stringOr("")) ?? Date() }
    }
}

nonisolated struct KalshiEdge: Hashable, Sendable {
    let marketTicker: String
    let side: String
    let edge: Double

    init(json j: JSON) {
        marketTicker = j["market_ticker"].stringOr("")
        side = j["side"].stringOr("")
        edge = j["edge"].doubleOr(0)
    }
}

nonisolated struct KalshiPosition: Hashable, Sendable {
    let marketTicker: String
    let side: String
    let contracts: Int
    let unrealizedCents: Double?
    let match: String
    let pickLabel: String
    let pickLogo: String
    let maxPayout: Double
    let cost: Double
    let currentValue: Double?
    let oddsPct: Double?

    init(json j: JSON) {
        marketTicker = j["market_ticker"].stringOr("")
        side = j["side"].stringOr("")
        contracts = j["contracts"].intOr(0)
        unrealizedCents = j["unrealized_cents"].double
        match = j["match"].stringOr("")
        pickLabel = j["pick_label"].stringOr("")
        pickLogo = j["pick_logo"].stringOr("")
        maxPayout = j["max_payout"].doubleOr(0)
        cost = j["cost"].doubleOr(0)
        currentValue = j["current_value"].double
        oddsPct = j["odds_pct"].double
    }
}

nonisolated struct KalshiInstance: Hashable, Sendable, Identifiable {
    let id: String
    let name: String
    let running: Bool
    let liveEnabled: Bool

    init(json j: JSON) {
        id = j["id"].stringOr("")
        name = j["name"].stringOr("Kalshi instance")
        running = j["running"].bool
        liveEnabled = j["live_enabled"].bool
    }
}

// MARK: - Repository

nonisolated struct KalshiRepository: Sendable {
    let client: ApiClient

    func portfolio(_ bid: String) async throws -> KalshiPortfolio {
        KalshiPortfolio(json: try await client.get("/brokerages/\(bid)/kalshi/portfolio"))
    }

    func edges(_ bid: String) async throws -> [KalshiEdge] {
        let d = try await client.get("/brokerages/\(bid)/kalshi/edges", query: ["limit": 10])
        return d["edges"].objectElements.map(KalshiEdge.init(json:))
    }

    func positions(_ bid: String) async throws -> [KalshiPosition] {
        let d = try await client.get("/brokerages/\(bid)/kalshi/positions")
        return d["positions"].objectElements.map(KalshiPosition.init(json:))
    }

    func kill(_ bid: String) async throws -> JSONObject {
        try await client.post("/brokerages/\(bid)/kalshi/kill").orderedObjectValue
    }

    func instances(_ bid: String) async throws -> [KalshiInstance] {
        let d = try await client.get("/brokerages/\(bid)/kalshi/instances")
        return d["instances"].objectElements.map(KalshiInstance.init(json:))
    }

    func createInstance(_ bid: String, _ body: JSONObject) async throws {
        _ = try await client.post("/brokerages/\(bid)/kalshi/instances", body: .object(body))
    }

    func startInstance(_ id: String) async throws {
        _ = try await client.post("/instances/\(id)/start")
    }

    func stopInstance(_ id: String) async throws {
        _ = try await client.post("/instances/\(id)/stop")
    }

    func instanceDetail(_ id: String) async throws -> JSONObject {
        try await client.get("/instances/\(id)/kalshi/detail").orderedObjectValue
    }

    func instanceDecisions(_ id: String) async throws -> JSONObject {
        try await client.get("/instances/\(id)/kalshi/decisions", query: ["limit": 200]).orderedObjectValue
    }

    func instanceLive(_ id: String) async throws -> JSONObject {
        try await client.get("/instances/\(id)/kalshi/live").orderedObjectValue
    }

    func instanceOrders(_ id: String) async throws -> JSONObject {
        try await client.get("/instances/\(id)/kalshi/orders", query: ["limit": 50]).orderedObjectValue
    }

    /// GET /models: the `models` list (or a bare list), maps with an `id`.
    func models() async throws -> [JSONObject] {
        let d = try await client.get("/models")
        let list = d.isObject ? d["models"] : d
        return list.objectElements.filter { !$0["id"].isNull }.map(\.orderedObjectValue)
    }

    func updateInstance(_ id: String, _ body: JSONObject) async throws {
        _ = try await client.patch("/instances/\(id)/kalshi/config", body: .object(body))
    }

    func deleteInstance(_ id: String) async throws {
        _ = try await client.delete("/instances/\(id)", query: ["force": true])
    }

    // MARK: Backtests

    func createBacktest(_ bid: String, _ body: JSONObject) async throws -> String {
        let d = try await client.post("/brokerages/\(bid)/kalshi/backtests", body: .object(body))
        return d["id"].stringOr("")
    }

    func listBacktests(_ bid: String) async throws -> [JSONObject] {
        try await client.get("/brokerages/\(bid)/kalshi/backtests")["backtests"].objectElements.map(\.orderedObjectValue)
    }

    func backtestStatus(_ id: String) async throws -> JSONObject {
        try await client.get("/kalshi/backtests/\(id)/status").orderedObjectValue
    }

    func backtestResults(_ id: String) async throws -> JSONObject {
        try await client.get("/kalshi/backtests/\(id)/results").orderedObjectValue
    }

    func stopBacktest(_ id: String) async throws {
        _ = try await client.post("/kalshi/backtests/\(id)/stop")
    }

    func deleteBacktest(_ id: String) async throws {
        _ = try await client.delete("/kalshi/backtests/\(id)")
    }
}
