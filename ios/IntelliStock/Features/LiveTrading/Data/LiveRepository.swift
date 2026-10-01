import Foundation

// Ported from features/live_trading/data/live_repository.dart.

/// Result of a command submission.
nonisolated struct CommandResult: Hashable, Sendable {
    let commandId: String
    let status: String
    let result: [String: JSON]?
    let error: String?

    init(json j: JSON) {
        commandId = j["command_id"].string ?? ""
        status = j["status"].string ?? "pending"
        result = j["result"].object
        error = j["error"].string
    }

    var isTerminal: Bool { status == "completed" || status == "failed" }
}

/// A raw historical price point from the symbol-historicals endpoint.
nonisolated struct HistPoint: Hashable, Sendable {
    /// ISO string or epoch.
    let ts: JSON
    let value: Double
}

/// Accesses the live-trading REST endpoints for a single instance.
nonisolated struct LiveRepository: Sendable {
    let client: ApiClient

    /// `GET /instances/{id}/live-state` → nil on 404 (not running).
    func liveState(_ id: String) async throws -> LiveState? {
        do {
            return LiveState(json: try await client.get("/instances/\(id)/live-state"))
        } catch let error as ApiError where error.statusCode == 404 {
            return nil
        }
    }

    /// `GET /instances/{id}/portfolio-history?range=` → `PortfolioHistory`.
    func equityHistory(_ id: String, _ range: String) async throws -> PortfolioHistory {
        PortfolioHistory(json: try await client.get("/instances/\(id)/portfolio-history", query: ["range": .string(range)]))
    }

    /// `GET /symbol-historicals?symbols=&range=` → symbol → list of
    /// {ts, value} points.
    func symbolHistoricals(_ symbols: [String], _ range: String) async throws -> [String: [HistPoint]] {
        if symbols.isEmpty { return [:] }
        let data = try await client.get(
            "/symbol-historicals",
            query: ["symbols": .string(symbols.joined(separator: ",")), "range": .string(range)]
        )
        guard let results = data["results"].object else { return [:] }
        return results.mapValues { value in
            value.objectElements.map { HistPoint(ts: $0["ts"], value: $0["value"].doubleOr(0)) }
        }
    }

    /// `GET /brokerages/{id}/holding-opens` → symbol → acquisition date (when
    /// the current open position started). Empty for non-Alpaca accounts or
    /// when the open fill is outside the fetched order window; callers fall
    /// back to the full series in that case.
    func holdingOpens(_ brokerageId: String) async throws -> [String: Date] {
        let data = try await client.get("/brokerages/\(brokerageId)/holding-opens")
        guard let opens = data["opens"].object else { return [:] }
        var out: [String: Date] = [:]
        for (k, v) in opens {
            if let t = DartDateTime.tryParse(v.string ?? "") { out[k] = t }
        }
        return out
    }

    /// `POST /instances/{id}/live-command` → `CommandResult`.
    func sendCommand(_ id: String, _ type: String, _ payload: [String: JSON]) async throws -> CommandResult {
        let data = try await client.post(
            "/instances/\(id)/live-command",
            body: ["type": .string(type), "payload": .object(payload)]
        )
        return CommandResult(json: data)
    }

    /// `GET /live-commands/{commandId}` → `CommandResult`.
    func commandStatus(_ commandId: String) async throws -> CommandResult {
        CommandResult(json: try await client.get("/live-commands/\(commandId)"))
    }
}
