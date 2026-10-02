import Foundation

// Ported from features/dashboard/data/dashboard_repository.dart.
// `AccountPosition` and `AccountHoldings` live in AccountPosition.swift.

/// A single engine entry from `GET /status`.
nonisolated struct EngineStatus: Hashable, Sendable, Identifiable {
    let id: String
    let status: String
    let details: String?

    init(id: String, status: String, details: String? = nil) {
        self.id = id
        self.status = status
        self.details = details
    }

    init(json: JSON) {
        self.init(
            id: json["id"].stringOr(""),
            status: json["status"].stringOr("stopped"),
            details: json["details"].string
        )
    }
}

/// Full services snapshot (result of the parallel 4-endpoint fetch).
nonisolated struct ServicesSnapshot: Hashable, Sendable {
    let engines: [EngineStatus]
    let agentControl: JSONObject?
    let digestControl: JSONObject?
    let nexusStatus: JSONObject?

    init(
        engines: [EngineStatus],
        agentControl: JSONObject? = nil,
        digestControl: JSONObject? = nil,
        nexusStatus: JSONObject? = nil
    ) {
        self.engines = engines
        self.agentControl = agentControl
        self.digestControl = digestControl
        self.nexusStatus = nexusStatus
    }

    func engineById(_ id: String) -> EngineStatus? {
        engines.first { $0.id == id }
    }

    func statusFor(_ id: String) -> String {
        engineById(id)?.status.lowercased() ?? "stopped"
    }

    func isRunning(_ id: String) -> Bool { statusFor(id) == "running" }
    func isPaused(_ id: String) -> Bool { statusFor(id) == "paused" }
}

/// A single brokerage account from `GET /brokerages`.
nonisolated struct BrokerageAccount: Hashable, Sendable, Identifiable {
    let id: String
    let accountName: String
    let brokerageType: String
    let status: String
    let alpacaPaper: Bool
    /// Kalshi's `kalshi_environment`: `live` or `demo` (paper). Empty for
    /// other brokerages.
    let kalshiEnvironment: String

    init(
        id: String,
        accountName: String,
        brokerageType: String,
        status: String,
        alpacaPaper: Bool = false,
        kalshiEnvironment: String = ""
    ) {
        self.id = id
        self.accountName = accountName
        self.brokerageType = brokerageType
        self.status = status
        self.alpacaPaper = alpacaPaper
        self.kalshiEnvironment = kalshiEnvironment
    }

    init(json: JSON) {
        self.init(
            id: json["id"].stringOr(""),
            accountName: json["account_name"].stringOr(""),
            brokerageType: json["brokerage_type"].stringOr(""),
            status: json["status"].stringOr(""),
            alpacaPaper: json["alpaca_paper"].bool,
            kalshiEnvironment: json["kalshi_environment"].stringOr("")
        )
    }

    var isActive: Bool { status.lowercased() == "active" }

    /// A paper (simulated) account: Alpaca paper, or Kalshi's demo
    /// environment. The portfolio sheet's "Paper" / "Live" subtitle.
    var isPaper: Bool { alpacaPaper || kalshiEnvironment.lowercased() == "demo" }
}

/// One entry of `GET /widget/accounts` — the home-screen widget's
/// self-refresh payload, one per instance. Its value and day P&L are the
/// instance's brokerage's 1D portfolio history (`_widget_account` in
/// `backend/api/main.py`), so every instance on one brokerage carries that
/// brokerage's figures. Only the fields the portfolio sheet reads.
nonisolated struct DashboardWidgetAccount: Hashable, Sendable {
    /// The instance id.
    let id: String
    let accountValue: Double
    let dayPnlAbs: Double
    let dayPnlPct: Double

    init(id: String, accountValue: Double, dayPnlAbs: Double, dayPnlPct: Double) {
        self.id = id
        self.accountValue = accountValue
        self.dayPnlAbs = dayPnlAbs
        self.dayPnlPct = dayPnlPct
    }

    init(json: JSON) {
        self.init(
            id: json["id"].stringOr(""),
            accountValue: json["accountValue"].double ?? 0,
            dayPnlAbs: json["dayPnlAbs"].double ?? 0,
            dayPnlPct: json["dayPnlPct"].double ?? 0
        )
    }
}

/// Thin data layer for all dashboard endpoints.
nonisolated struct DashboardRepository: Sendable {
    let client: ApiClient

    /// Fetches all four service-status endpoints in parallel. Each failure
    /// reads as an empty map, and an empty map as "absent" — but a
    /// cancellation (the caller went away) throws `CancellationError`
    /// instead of reading as every engine "stopped".
    func fetchServices() async throws -> ServicesSnapshot {
        async let status = objectOrEmpty("/status")
        async let agent = objectOrEmpty("/agent/control")
        async let digest = objectOrEmpty("/digest/control")
        async let nexus = objectOrEmpty("/nexus/status")
        let (statusData, agentData, digestData, nexusData) = try await (status, agent, digest, nexus)
        try Task.checkCancellation()

        let engines = JSON.object(statusData)["engines"].objectElements.map(EngineStatus.init(json:))
        return ServicesSnapshot(
            engines: engines,
            agentControl: agentData.isEmpty ? nil : agentData,
            digestControl: digestData.isEmpty ? nil : digestData,
            nexusStatus: nexusData.isEmpty ? nil : nexusData
        )
    }

    /// The non-throwing form, kept for source compatibility. It cannot
    /// express cancellation, so a cancelled fetch reads as an empty snapshot;
    /// anything that writes shared state uses `fetchServices()`.
    func services() async -> ServicesSnapshot {
        (try? await fetchServices()) ?? ServicesSnapshot(engines: [])
    }

    /// Dart `.catchError((_) => <String, dynamic>{})`, except cancellation,
    /// which propagates.
    private func objectOrEmpty(_ path: String) async throws -> JSONObject {
        do {
            return try await client.get(path).orderedObject ?? JSONObject()
        } catch where error.isCancellation {
            throw CancellationError()
        } catch {
            return JSONObject()
        }
    }

    /// GET /brokerages → {accounts: [...]}
    func brokerages() async throws -> [BrokerageAccount] {
        let data = try await client.get("/brokerages")
        return data["accounts"].objectElements.map(BrokerageAccount.init(json:))
    }

    /// GET /widget/accounts → {accounts: [...], synced_at} (read-only; the
    /// portfolio sheet's equity and day change per account).
    func widgetAccounts() async throws -> [DashboardWidgetAccount] {
        let data = try await client.get("/widget/accounts")
        return data["accounts"].objectElements.map(DashboardWidgetAccount.init(json:))
    }

    /// GET /brokerages/{id}/portfolio-history?range=
    func portfolioHistory(_ id: String, _ range: String) async throws -> PortfolioHistory {
        let data = try await client.get("/brokerages/\(id)/portfolio-history", query: ["range": .string(range)])
        return PortfolioHistory(json: data)
    }

    /// GET /brokerages/{id}/positions → uninvested cash + holdings (by value).
    func accountHoldings(_ id: String) async throws -> AccountHoldings {
        let data = try await client.get("/brokerages/\(id)/positions")
        return AccountHoldings(
            cash: data["cash"].double,
            positions: data["positions"].objectElements.map(AccountPosition.init(json:))
        )
    }

    // MARK: Nexus strategy telemetry (read-only)

    /// GET /brokerages/{id}/trends?status=&limit= → market trends.
    func nexusTrends(_ id: String, status: String = "active", limit: Int = 50) async throws -> [MarketTrend] {
        let data = try await client.get(
            "/brokerages/\(id)/trends",
            query: ["status": .string(status), "limit": .string("\(limit)")]
        )
        return data["trends"].objectElements.map(MarketTrend.init(json:))
    }

    /// GET /brokerages/{id}/backfill-queue → pending buy candidates.
    func backfillQueue(_ id: String) async throws -> [BackfillItem] {
        let data = try await client.get("/brokerages/\(id)/backfill-queue")
        return data["queue"].objectElements.map(BackfillItem.init(json:))
    }

    /// GET /brokerages/{id}/discovered → discover-engine opportunities.
    func discoveredStocks(_ id: String) async throws -> [DiscoveredStock] {
        let data = try await client.get("/brokerages/\(id)/discovered")
        return data["stocks"].objectElements.map(DiscoveredStock.init(json:))
    }

    /// GET /brokerages/{id}/trade-contexts → per-symbol bot rationale.
    func tradeContexts(_ id: String) async throws -> [TradeRationale] {
        let data = try await client.get("/brokerages/\(id)/trade-contexts")
        return data["contexts"].objectElements.map(TradeRationale.init(json:))
    }

    /// GET /brokerages/{id}/nexus-outcomes → signal→outcome scorecard.
    func nexusOutcomes(_ id: String) async throws -> OutcomeStats {
        OutcomeStats(json: try await client.get("/brokerages/\(id)/nexus-outcomes"))
    }

    /// GET /brokerages/{id}/momentum-watchlist → watchlist count + newest names.
    func momentumWatchlist(_ id: String) async throws -> WatchlistSummary {
        WatchlistSummary(json: try await client.get("/brokerages/\(id)/momentum-watchlist"))
    }

    // MARK: Control POSTs

    func startPriceService() async throws {
        _ = try await client.post("/config/run-price-service")
    }

    func terminatePrice() async throws {
        _ = try await client.post("/config/terminate-price")
    }

    func controlDiscover(running: Bool) async throws {
        _ = try await client.post("/discover/control", body: ["running": .bool(running)])
    }

    func controlAgent(running: Bool? = nil, paused: Bool? = nil, specialRequest: String? = nil) async throws {
        var body: JSONObject = [:]
        if let running { body["running"] = .bool(running) }
        if let paused { body["paused"] = .bool(paused) }
        if let specialRequest { body["special_request"] = .string(specialRequest) }
        _ = try await client.post("/agent/control", body: .object(body))
    }

    func controlDigest(running: Bool) async throws {
        _ = try await client.post("/digest/control", body: ["running": .bool(running)])
    }

    func digestSendNow() async throws {
        _ = try await client.post("/digest/send-now")
    }

    func controlNexus(running: Bool) async throws {
        _ = try await client.post("/nexus/control", body: ["running": .bool(running)])
    }
}
