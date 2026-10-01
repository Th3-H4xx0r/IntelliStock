import Foundation

// Ported from features/token_usage/data/token_usage_repository.dart.

// MARK: - Data models

nonisolated struct TelemetryHealth: Hashable, Sendable {
    let bufferDepth: Int?
    let lastFlushAgeS: Num?
    let writeErrors24h: Int

    init(json j: JSON) {
        bufferDepth = j["buffer_depth"].int
        lastFlushAgeS = j["last_flush_age_s"].num
        writeErrors24h = j["write_errors_24h"].intOr(0)
    }
}

nonisolated struct ProviderBreakdown: Hashable, Sendable {
    let provider: String
    let costUsd: Double?
    let tokens: Int?
    let calls: Int?

    init(json j: JSON) {
        provider = j["provider"].string ?? ""
        costUsd = j["cost_usd"].double
        tokens = j["tokens"].int
        calls = j["calls"].int
    }
}

nonisolated struct UsageSummary: Hashable, Sendable {
    let totalCostUsd: Double?
    let totalCalls: Int?
    let totalTokens: Int?
    let maxPlanEstimateUsd: Double?
    let byProvider: [ProviderBreakdown]
    let telemetryHealth: TelemetryHealth?

    init(json j: JSON) {
        totalCostUsd = j["total_cost_usd"].double
        totalCalls = j["total_calls"].int
        totalTokens = j["total_tokens"].int
        maxPlanEstimateUsd = j["max_plan_estimate_usd"].double
        byProvider = j["by_provider"].objectElements.map(ProviderBreakdown.init(json:))
        telemetryHealth = j["telemetry_health"].isObject ? TelemetryHealth(json: j["telemetry_health"]) : nil
    }
}

nonisolated struct TimeseriesRow: Hashable, Sendable {
    let provider: String
    /// Epoch ms.
    let bucketStartTs: Int
    let costUsd: Double?
    let tokens: Int?
    let calls: Int?

    init(json j: JSON) {
        provider = j["provider"].string ?? "unknown"
        bucketStartTs = j["bucket_start_ts"].intOr(0)
        costUsd = j["cost_usd"].double
        tokens = j["tokens"].int
        calls = j["calls"].int
    }
}

nonisolated struct SpenderRow: Hashable, Sendable {
    let key: String
    let calls: Int?
    let tokens: Int?
    let costUsd: Double?

    init(json j: JSON) {
        key = j["key"].string ?? ""
        calls = j["calls"].int
        tokens = j["tokens"].int
        costUsd = j["cost_usd"].double
    }
}

nonisolated struct BacktestUsageRow: Hashable, Sendable {
    let backtestId: String?
    let displayLabel: String?
    let kind: String?
    let instanceId: String?
    /// Epoch ms.
    let firstTs: Int?
    let calls: Int?
    let tokens: Int?
    let costUsd: Double?
    let okCalls: Int?
    let failedCalls: Int?

    init(json j: JSON) {
        backtestId = j["backtest_id"].string
        displayLabel = j["display_label"].string
        kind = j["kind"].string
        instanceId = j["instance_id"].string
        firstTs = j["first_ts"].int
        calls = j["calls"].int
        tokens = j["tokens"].int
        costUsd = j["cost_usd"].double
        okCalls = j["ok_calls"].int
        failedCalls = j["failed_calls"].int
    }
}

nonisolated struct RecentCall: Hashable, Sendable {
    let id: String?
    /// Epoch ms.
    let ts: Int?
    let provider: String?
    let model: String?
    let inputTokens: Int?
    let outputTokens: Int?
    let totalCostUsd: Double?
    let strategy: String?
    let callSite: String?
    /// Full JSON for the detail dialog.
    let raw: [String: JSON]

    init(json j: JSON) {
        id = j["id"].string
        ts = j["ts"].int
        provider = j["provider"].string
        model = j["model"].string
        inputTokens = j["input_tokens"].int
        outputTokens = j["output_tokens"].int
        totalCostUsd = j["total_cost_usd"].double
        strategy = j["strategy"].string
        callSite = j["call_site"].string
        raw = j.objectValue
    }
}

nonisolated struct TokenUsageData: Hashable, Sendable {
    var summary: UsageSummary?
    var timeseries: [TimeseriesRow] = []
    var topByModel: [SpenderRow] = []
    var topByCallSite: [SpenderRow] = []
    var byBacktest: [BacktestUsageRow] = []
    var recentCalls: [RecentCall] = []
    var partialError: String?
}

// MARK: - Repository

nonisolated struct TokenUsageRepository: Sendable {
    let client: ApiClient

    /// GET /llm-usage/summary?range=
    func summary(_ range: String) async throws -> UsageSummary {
        UsageSummary(json: try await client.get("/llm-usage/summary", query: ["range": .string(range)]))
    }

    /// GET /llm-usage/timeseries?range=&bucket= — a bare list or `{rows}`.
    func timeseries(_ range: String, _ bucket: String) async throws -> [TimeseriesRow] {
        let raw = try await client.get("/llm-usage/timeseries", query: ["range": .string(range), "bucket": .string(bucket)])
        let list = raw.isArray ? raw : raw["rows"]
        return list.objectElements.map(TimeseriesRow.init(json:))
    }

    /// GET /llm-usage/top-spenders?range=&group_by=&limit=
    func topSpenders(_ range: String, _ groupBy: String, _ limit: Int) async throws -> [SpenderRow] {
        let raw = try await client.get(
            "/llm-usage/top-spenders",
            query: ["range": .string(range), "group_by": .string(groupBy), "limit": .string(String(limit))]
        )
        return raw.objectElements.map(SpenderRow.init(json:))
    }

    /// GET /llm-usage/by-backtest?range=&limit=
    func byBacktest(_ range: String, _ limit: Int) async throws -> [BacktestUsageRow] {
        let raw = try await client.get(
            "/llm-usage/by-backtest",
            query: ["range": .string(range), "limit": .string(String(limit))]
        )
        return raw.objectElements.map(BacktestUsageRow.init(json:))
    }

    /// GET /llm-usage/calls?limit=&range=
    func calls(_ limit: Int, _ range: String) async throws -> [RecentCall] {
        let raw = try await client.get(
            "/llm-usage/calls",
            query: ["limit": .string(String(limit)), "range": .string(range)]
        )
        return raw.objectElements.map(RecentCall.init(json:))
    }

    /// Fetch all 6 endpoints in parallel; partial failure populates
    /// `partialError` ("N of 6 requests failed: <first error, in request
    /// order>").
    func fetchAll(_ range: String) async -> TokenUsageData {
        let bucket = range == "24h" ? "hour" : "day"
        async let s = Result { try await summary(range) }
        async let t = Result { try await timeseries(range, bucket) }
        async let m = Result { try await topSpenders(range, "model", 10) }
        async let c = Result { try await topSpenders(range, "call_site", 10) }
        async let b = Result { try await byBacktest(range, 50) }
        async let r = Result { try await calls(50, "now") }
        let results = await (s, t, m, c, b, r)

        var failures = 0
        var firstError: String?
        func pick<T>(_ result: Result<T, any Error>) -> T? {
            switch result {
            case .success(let value):
                return value
            case .failure(let error):
                failures += 1
                if firstError == nil { firstError = tokenUsageErrorText(error) }
                return nil
            }
        }

        var data = TokenUsageData()
        data.summary = pick(results.0)
        data.timeseries = pick(results.1) ?? []
        data.topByModel = pick(results.2) ?? []
        data.topByCallSite = pick(results.3) ?? []
        data.byBacktest = pick(results.4) ?? []
        data.recentCalls = pick(results.5) ?? []
        data.partialError = failures > 0 ? "\(failures) of 6 requests failed: \(firstError ?? "")" : nil
        return data
    }
}

/// Dart `e.toString()`: an `ApiError` prints its message.
nonisolated private func tokenUsageErrorText(_ error: any Error) -> String {
    (error as? ApiError)?.message ?? String(describing: error)
}

nonisolated private extension Result where Failure == any Error {
    /// `Result(catching:)` for async work.
    init(_ body: () async throws -> Success) async {
        do {
            self = .success(try await body())
        } catch {
            self = .failure(error)
        }
    }
}
