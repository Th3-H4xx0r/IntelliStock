import Foundation

/// Thin API layer for all strategy-related endpoints, ported from
/// features/strategies/data/strategy_repository.dart.
nonisolated struct StrategyRepository: Sendable {
    let client: ApiClient

    // MARK: Strategy CRUD

    /// GET /strategies → {strategies: [...]}
    func list() async throws -> [[String: JSON]] {
        try await client.get("/strategies")["strategies"].objectElements.map(\.objectValue)
    }

    /// GET /strategies/:id
    func get(_ id: String) async throws -> [String: JSON] {
        let data = try await client.get("/strategies/\(id)")
        return data["strategy"].object ?? data.objectValue
    }

    /// PUT /strategies/:id
    ///
    /// When `preserveHistory` is true, the backend re-stamps existing Nexus
    /// saved-state to the new model identities so the next boot reuses it
    /// instead of running a destructive lookback + cleanup. Set it only after
    /// the operator confirms the preserve-history prompt (see
    /// `previewConfigChange`).
    func update(_ id: String, _ body: [String: JSON], preserveHistory: Bool = false) async throws -> [String: JSON] {
        var payload = body
        if preserveHistory { payload["preserve_history"] = true }
        return try await client.put("/strategies/\(id)", body: .object(payload)).objectValue
    }

    /// POST /strategies/:id/config-change-preview
    ///
    /// Read-only dry-run: returns `{needs_prompt, instances: [...]}`
    /// indicating whether saving `strategies` would rebuild Nexus history for
    /// any linked instance that has existing live state.
    func previewConfigChange(_ id: String, _ strategies: [JSON]) async throws -> [String: JSON] {
        try await client.post(
            "/strategies/\(id)/config-change-preview",
            body: ["strategies": .array(strategies)]
        ).objectValue
    }

    /// GET /strategies/available → list of available strategy type names.
    func available() async throws -> [String] {
        try await client.get("/strategies/available")["strategies"].stringElements
    }

    // MARK: Agent result endpoints

    /// GET /agent/results?limit=10000 → {results: [...]}
    func agentResults() async throws -> [AgentResult] {
        let data = try await client.get("/agent/results", query: ["limit": "10000"])
        return data["results"].objectElements.map(AgentResult.init(json:))
    }

    /// GET /agent/top5 → {top5: [...]}
    func top5() async throws -> [[String: JSON]] {
        try await client.get("/agent/top5")["top5"].objectElements.map(\.objectValue)
    }

    /// GET /agent/best → the single best backtest record; nil on any error.
    func agentBest() async -> [String: JSON]? {
        guard let data = try? await client.get("/agent/best") else { return nil }
        return data.object
    }

    /// GET /backtests/best-per-strategy → {by_strategy: {str(id): {...}}}
    func bestPerStrategy() async throws -> [String: JSON] {
        try await client.get("/backtests/best-per-strategy")["by_strategy"].objectValue
    }

    // MARK: Instances (for the backtest modal)

    /// GET /instances → {instances: [...]}
    func instances() async throws -> [[String: JSON]] {
        try await client.get("/instances")["instances"].objectElements.map(\.objectValue)
    }

    /// POST /instances (create new)
    func createInstance(_ body: [String: JSON]) async throws -> [String: JSON] {
        try await client.post("/instances", body: .object(body)).objectValue
    }

    /// POST /instances/:id/link-strategy
    func linkStrategy(_ instanceId: String, _ strategyId: Int) async throws {
        _ = try await client.post(
            "/instances/\(dartEncodeComponent(instanceId))/link-strategy",
            body: ["strategy_id": .int(strategyId)]
        )
    }

    /// POST /backtests (create backtest run)
    func createBacktest(_ body: [String: JSON]) async throws -> [String: JSON] {
        try await client.post("/backtests", body: .object(body)).objectValue
    }

    // MARK: Client-side merge helpers

    /// Best-backtest stats per strategy from `results`, mirroring the Vue
    /// `bestByStrategy` computed: strategyId → stats.
    static func computeBestByStrategy(_ results: [AgentResult]) -> [Int: BestPerStrategy] {
        var m: [Int: BestPerStrategy] = [:]
        for r in results {
            guard let sid = r.strategyId else { continue }
            if m[sid] == nil {
                m[sid] = BestPerStrategy(
                    strategyId: sid,
                    bestPnl: r.overallProfit ?? 0,
                    bestPnlBid: r.backtestId,
                    bestPct: r.pnlPercent ?? 0,
                    bestPctBid: r.backtestId,
                    count: 1,
                    latest: r.createdAt
                )
            } else {
                m[sid]!.fold(r)
            }
        }
        return m
    }

    /// Merge strategy list JSON with best-backtest stats and top-5 rank info.
    /// Mirrors the Vue `enrichedStrategies` computed.
    static func mergeStrategyRows(
        _ strategies: [[String: JSON]],
        _ bestByStrategy: [Int: BestPerStrategy],
        _ top5Entries: [[String: JSON]],
        _ allBestByStrat: [String: JSON]
    ) -> [StrategyListRow] {
        // Build rank map: strategy_id → rank
        var rankMap: [Int: Int] = [:]
        for e in top5Entries {
            let entry = JSON.object(e)
            if let sid = entry["strategy_id"].int, let rank = entry["rank"].int { rankMap[sid] = rank }
        }

        return strategies.map { s in
            let json = JSON.object(s)
            let sid = json["id"].intOr(0)
            let best = bestByStrategy[sid]
            let top5entry = top5Entries.first { JSON.object($0)["strategy_id"].int == sid }.map(JSON.object)
            let allBest = (allBestByStrat[String(sid)] ?? .null).object.map(JSON.object)
            let rank = rankMap[sid]

            var bestPnl = best?.bestPnl
            var bestPnlBid = best?.bestPnlBid
            var bestPct = best?.bestPct
            var bestPctBid = best?.bestPctBid
            let runCount = best?.count ?? 0

            if bestPnl == nil, let top5entry {
                bestPnl = strategyToDouble(top5entry["overall_profit"])
                bestPnlBid = top5entry["backtest_id"].string
            }
            if bestPnl == nil, let allBest {
                bestPnl = strategyToDouble(allBest["best_pnl"])
                bestPnlBid = allBest["backtest_id"].string
            }
            if bestPct == nil, let top5entry {
                bestPct = strategyToDouble(top5entry["pnl_percent"])
            }
            if bestPct == nil, let allBest {
                bestPct = strategyToDouble(allBest["best_pct"])
            }
            if bestPctBid == nil { bestPctBid = bestPnlBid }

            return StrategyListRow(
                strategyJson: json,
                bestPnl: bestPnl,
                bestPnlBid: bestPnlBid,
                bestPct: bestPct,
                bestPctBid: bestPctBid,
                runCount: runCount,
                rank: rank
            )
        }
    }
}

/// Dart `Uri.encodeComponent`: letters, digits and `-_.!~*'()` pass through;
/// everything else (a space too) is percent-encoded UTF-8, uppercase hex.
nonisolated func dartEncodeComponent(_ s: String) -> String {
    var out = ""
    for byte in s.utf8 {
        switch byte {
        case UInt8(ascii: "a")...UInt8(ascii: "z"), UInt8(ascii: "A")...UInt8(ascii: "Z"),
             UInt8(ascii: "0")...UInt8(ascii: "9"),
             UInt8(ascii: "-"), UInt8(ascii: "_"), UInt8(ascii: "."), UInt8(ascii: "!"),
             UInt8(ascii: "~"), UInt8(ascii: "*"), UInt8(ascii: "'"), UInt8(ascii: "("), UInt8(ascii: ")"):
            out.unicodeScalars.append(Unicode.Scalar(byte))
        default:
            out += String(format: "%%%02X", byte)
        }
    }
    return out
}
