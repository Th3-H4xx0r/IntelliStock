import Foundation

/// Ported from features/backtests/data/backtest_repository.dart.
nonisolated struct BacktestRepository: Sendable {
    let client: ApiClient

    /// GET /backtests?page=&per_page=&sort_by=&sort_order=
    func list(
        page: Int = 1,
        perPage: Int = 15,
        sortBy: String = "completed_at",
        sortOrder: String = "desc"
    ) async throws -> BacktestListResponse {
        let data = try await client.get(
            "/backtests",
            query: [
                "page": .string(String(page)),
                "per_page": .string(String(perPage)),
                "sort_by": .string(sortBy),
                "sort_order": .string(sortOrder),
            ]
        )
        return BacktestListResponse(json: data)
    }

    /// GET /backtests/:id (summary by default; fallback used as detail root)
    func get(_ id: String) async throws -> BacktestSummary {
        BacktestSummary(json: try await client.get("/backtests/\(id)"))
    }

    /// DELETE /backtests/:id
    func delete(_ id: String) async throws {
        _ = try await client.delete("/backtests/\(id)")
    }

    /// GET /backtests/:id/status
    func status(_ id: String) async throws -> BacktestStatus {
        BacktestStatus(json: try await client.get("/backtests/\(id)/status"))
    }

    /// GET /backtests/:id/summary
    func summary(_ id: String) async throws -> BacktestSummary {
        BacktestSummary(json: try await client.get("/backtests/\(id)/summary"))
    }

    /// GET /backtests/:id/graph-data. Large; parsed off the main actor.
    @concurrent
    func graphData(_ id: String) async throws -> BacktestGraphData {
        BacktestGraphData(json: try await client.get("/backtests/\(id)/graph-data"))
    }

    /// GET /backtests/:id/playback-data. Large; parsed off the main actor.
    @concurrent
    func playbackData(_ id: String) async throws -> PlaybackData {
        PlaybackData(json: try await client.get("/backtests/\(id)/playback-data"))
    }

    /// GET /backtests/:id/logs?since_line=N
    func logs(_ id: String, sinceLine: Int = 0) async throws -> JSONObject {
        try await client.get("/backtests/\(id)/logs", query: ["since_line": .string(String(sinceLine))]).orderedObjectValue
    }

    /// GET /backtests/:id/llm-cost
    func llmCost(_ id: String) async throws -> LlmCost {
        LlmCost(json: try await client.get("/backtests/\(id)/llm-cost"))
    }

    /// POST /backtests/:id/:action (pause | resume | stop)
    func action(_ id: String, _ name: String) async throws -> JSONObject {
        try await client.post("/backtests/\(id)/\(name)").orderedObjectValue
    }

    /// POST /backtests
    func create(_ body: JSONObject) async throws -> JSONObject {
        try await client.post("/backtests", body: .object(body)).orderedObjectValue
    }
}
