import Foundation
import Testing
@testable import IntelliStock

/// Ported from test/features/token_usage/token_usage_repository_test.dart,
/// plus the wire shape of `fetchAll` (paths, query keys, partial failure).
struct TokenUsageModelTests {
    @Test func usageSummaryParsesAllTopLevelFields() {
        let s = UsageSummary(json: [
            "total_cost_usd": 1.2345,
            "total_calls": 42,
            "total_tokens": 1500000,
            "max_plan_estimate_usd": 12.5,
            "by_provider": [
                ["provider": "gemini", "cost_usd": 0.5, "tokens": 800000, "calls": 20],
                ["provider": "openai", "cost_usd": 0.7, "tokens": 700000, "calls": 22],
            ],
            "telemetry_health": [
                "buffer_depth": 3,
                "last_flush_age_s": 5,
                "write_errors_24h": 0,
            ],
        ])
        #expect(close(s.totalCostUsd, 1.2345, 0.0001))
        #expect(s.totalCalls == 42)
        #expect(s.totalTokens == 1500000)
        #expect(close(s.maxPlanEstimateUsd, 12.5, 0.001))
        #expect(s.byProvider.count == 2)
        #expect(s.byProvider[0].provider == "gemini")
        #expect(s.telemetryHealth?.bufferDepth == 3)
        #expect(s.telemetryHealth?.writeErrors24h == 0)
    }

    @Test func usageSummaryHandlesMissingFieldsGracefully() {
        let s = UsageSummary(json: [:])
        #expect(s.totalCostUsd == nil)
        #expect(s.totalCalls == nil)
        #expect(s.byProvider.isEmpty)
        #expect(s.telemetryHealth == nil)
    }

    @Test func timeseriesRowParsesProviderAndBucketStartTs() {
        let r = TimeseriesRow(json: [
            "provider": "gemini",
            "bucket_start_ts": 1700000000000,
            "cost_usd": 0.0042,
            "tokens": 12000,
            "calls": 3,
        ])
        #expect(r.provider == "gemini")
        #expect(r.bucketStartTs == 1700000000000)
        #expect(close(r.costUsd, 0.0042, 0.0001))
    }

    @Test func timeseriesRowDefaultsProviderToUnknown() {
        #expect(TimeseriesRow(json: ["bucket_start_ts": 0]).provider == "unknown")
    }

    @Test func spenderRowParsesKeyAndNumericFields() {
        let r = SpenderRow(json: ["key": "gemini-3-flash", "calls": 15, "tokens": 200000, "cost_usd": 0.24])
        #expect(r.key == "gemini-3-flash")
        #expect(r.calls == 15)
        #expect(close(r.costUsd, 0.24, 0.001))
    }

    @Test func backtestUsageRowParsesFields() {
        let r = BacktestUsageRow(json: [
            "backtest_id": "abc123",
            "display_label": "Run #42",
            "kind": "backtest",
            "instance_id": "main",
            "first_ts": 1700000000000,
            "calls": 50,
            "tokens": 500000,
            "cost_usd": 5.0,
            "ok_calls": 48,
            "failed_calls": 2,
        ])
        #expect(r.backtestId == "abc123")
        #expect(r.displayLabel == "Run #42")
        #expect(r.kind == "backtest")
        #expect(r.okCalls == 48)
        #expect(r.failedCalls == 2)
    }

    @Test func recentCallParsesFieldsIncludingRaw() {
        let r = RecentCall(json: [
            "id": "call-001",
            "ts": 1700000000000,
            "provider": "openai",
            "model": "gpt-4o",
            "input_tokens": 1000,
            "output_tokens": 200,
            "total_cost_usd": 0.015,
            "strategy": "nexus",
            "call_site": "analyze",
        ])
        #expect(r.id == "call-001")
        #expect(r.provider == "openai")
        #expect(r.model == "gpt-4o")
        #expect(r.inputTokens == 1000)
        #expect(close(r.totalCostUsd, 0.015, 0.0001))
        #expect(r.raw["strategy"] == "nexus")
    }

    @Test func telemetryHealthStates() {
        let healthy = TelemetryHealth(json: ["buffer_depth": 2, "last_flush_age_s": 5, "write_errors_24h": 0])
        #expect(healthy.writeErrors24h == 0)
        #expect(!((healthy.lastFlushAgeS ?? 0) > 30))
        #expect(TelemetryHealth(json: ["write_errors_24h": 3]).writeErrors24h == 3)
        let lagging = TelemetryHealth(json: ["last_flush_age_s": 45, "write_errors_24h": 0])
        #expect((lagging.lastFlushAgeS ?? 0) > 30)
        // Dart kept the int: "5", not "5.0".
        #expect(healthy.lastFlushAgeS?.description == "5")
    }
}

struct TokenUsageRepositoryTests {
    /// The Dart "range parameter" group checked `range == '24h' ? 'hour' :
    /// 'day'` in isolation; here it is checked on the wire.
    @Test(arguments: [("24h", "hour"), ("7d", "day"), ("30d", "day")])
    func fetchAllPicksTheBucketFromTheRange(range: String, bucket: String) async {
        let stub = DataStub(json: "[]")
        _ = await TokenUsageRepository(client: stub.client).fetchAll(range)
        let timeseries = stub.requests.first { $0.path == "/llm-usage/timeseries" }
        #expect(timeseries?.queryItems == ["range": range, "bucket": bucket])
    }

    @Test func fetchAllIssuesTheSixRequestsWithTheirQueries() async {
        let stub = DataStub(json: "[]")
        let data = await TokenUsageRepository(client: stub.client).fetchAll("7d")
        #expect(data.partialError == nil)
        let byPath = Dictionary(grouping: stub.requests, by: \.path)
        #expect(byPath["/llm-usage/summary"]?.first?.queryItems == ["range": "7d"])
        let spenders = Set((byPath["/llm-usage/top-spenders"] ?? []).map { $0.queryItems })
        #expect(spenders == [
            ["range": "7d", "group_by": "model", "limit": "10"],
            ["range": "7d", "group_by": "call_site", "limit": "10"],
        ])
        #expect(byPath["/llm-usage/by-backtest"]?.first?.queryItems == ["range": "7d", "limit": "50"])
        #expect(byPath["/llm-usage/calls"]?.first?.queryItems == ["limit": "50", "range": "now"])
        #expect(stub.requests.count == 6)
    }

    @Test func fetchAllReportsPartialFailureWithTheFirstErrorInRequestOrder() async {
        let stub = DataStub()
        stub.handler = { request in
            switch request.path {
            case "/llm-usage/summary": (200, #"{"total_calls": 3}"#)
            case "/llm-usage/timeseries": (500, #"{"detail": "timeseries down"}"#)
            case "/llm-usage/calls": (502, #"{"detail": "calls down"}"#)
            default: (200, "[]")
            }
        }
        let data = await TokenUsageRepository(client: stub.client).fetchAll("24h")
        #expect(data.summary?.totalCalls == 3)
        #expect(data.timeseries.isEmpty)
        #expect(data.partialError == "2 of 6 requests failed: timeseries down")
    }

    @Test func timeseriesAcceptsARowsMap() async throws {
        let stub = DataStub(json: #"{"rows": [{"provider": "gemini", "bucket_start_ts": 5}]}"#)
        let rows = try await TokenUsageRepository(client: stub.client).timeseries("24h", "hour")
        #expect(rows.map(\.bucketStartTs) == [5])
    }
}
