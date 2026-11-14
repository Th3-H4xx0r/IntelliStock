import Foundation

/// The "why it moved" endpoints (all authenticated through `ApiClient`).
nonisolated struct AnalysisRepository: Sendable {
    let client: ApiClient

    /// `GET /analysis/settings`.
    func settings() async throws -> AnalysisSettings {
        AnalysisSettings(json: try await client.get("/analysis/settings"))
    }

    /// `PUT /analysis/settings` with only the changed fields; the reply is
    /// the full settings. An unknown model id is a 400.
    func updateSettings(_ change: AnalysisSettingsChange) async throws -> AnalysisSettings {
        AnalysisSettings(json: try await client.put("/analysis/settings", body: change.body))
    }

    /// `GET /analysis/moves?symbol=&range=`. An unknown symbol, or one with
    /// no price history, is a 404.
    func moves(symbol: String, range: AnalysisRange) async throws -> MoveAnalysis {
        let data = try await client.get(
            "/analysis/moves",
            query: ["symbol": .string(symbol), "range": .string(range.rawValue)]
        )
        return MoveAnalysis(json: data)
    }

    /// `POST /analysis/moves/explain`. `dates` nil means every unexplained
    /// or failed event in the range. `brokerageId` (an Alpaca account) is
    /// whose keys the server reads news with; left out when nil. Returns how
    /// many were queued; a 400 (with a readable message) when no model is
    /// chosen.
    func explain(symbol: String, range: AnalysisRange, dates: [String]?, force: Bool, brokerageId: String? = nil) async throws -> Int {
        var pairs: [(String, JSON)] = [("symbol", .string(symbol)), ("range", .string(range.rawValue))]
        if let dates { pairs.append(("dates", .array(dates.map(JSON.string)))) }
        pairs.append(("force", .bool(force)))
        if let brokerageId, !brokerageId.isEmpty { pairs.append(("brokerage_id", .string(brokerageId))) }
        let data = try await client.post("/analysis/moves/explain", body: .object(JSONObject(pairs)))
        return data["queued"].intOr(0)
    }

    /// `GET /analysis/portfolio?brokerage_id=`: the account's last session,
    /// per holding.
    func portfolio(brokerageId: String) async throws -> PortfolioMoves {
        PortfolioMoves(json: try await client.get("/analysis/portfolio", query: ["brokerage_id": .string(brokerageId)]))
    }
}
