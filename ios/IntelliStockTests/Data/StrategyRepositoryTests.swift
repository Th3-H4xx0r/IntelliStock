import Foundation
import Testing
@testable import IntelliStock

/// Ported from test/features/strategies/strategy_repository_preserve_test.dart,
/// plus the client-side merge helpers and the encoded link path.
struct StrategyRepositoryPreserveTests {
    @Test func updateWithoutPreserveHistoryOmitsTheFlag() async throws {
        let stub = DataStub()
        _ = try await StrategyRepository(client: stub.client).update("179", ["name": "X", "strategies": []])
        #expect(stub.last?.method == "PUT")
        #expect(stub.last?.path == "/strategies/179")
        #expect(stub.last?.jsonBody["preserve_history"] == .null)
        #expect(stub.last?.jsonBody.object?.keys.contains("preserve_history") == false)
    }

    @Test func updateWithPreserveHistoryAddsPreserveHistoryTrue() async throws {
        let stub = DataStub()
        _ = try await StrategyRepository(client: stub.client)
            .update("179", ["name": "X", "strategies": []], preserveHistory: true)
        #expect(stub.last?.jsonBody["preserve_history"] == true)
        #expect(stub.last?.jsonBody["name"] == "X")
    }

    @Test func previewConfigChangePostsStrategiesToThePreviewEndpoint() async throws {
        let stub = DataStub(json: #"{"needs_prompt": true}"#)
        let out = try await StrategyRepository(client: stub.client)
            .previewConfigChange("179", [["strategy": "graph_nexus_analysis", "config": [:]]])
        #expect(stub.last?.method == "POST")
        #expect(stub.last?.path == "/strategies/179/config-change-preview")
        #expect(stub.last?.jsonBody["strategies"].isArray == true)
        #expect(out["needs_prompt"] == true)
    }
}

struct StrategyRepositoryWireTests {
    @Test func linkStrategyPercentEncodesTheInstanceIdAndSendsAnInt() async throws {
        let stub = DataStub()
        try await StrategyRepository(client: stub.client).linkStrategy("my inst/1", 7)
        #expect(stub.last?.url?.absoluteString.hasSuffix("/instances/my%20inst%2F1/link-strategy") == true)
        #expect(stub.last?.jsonBody == ["strategy_id": 7])
    }

    @Test func getUnwrapsTheStrategyEnvelopeOrFallsBackToTheBody() async throws {
        let stub = DataStub(json: #"{"strategy": {"id": 5}}"#)
        let repo = StrategyRepository(client: stub.client)
        #expect(try await repo.get("5")["id"] == 5)
        stub.respond(json: #"{"id": 6}"#)
        #expect(try await repo.get("6")["id"] == 6)
    }

    @Test func agentResultsAsksForTenThousand() async throws {
        let stub = DataStub(json: #"{"results": [{"backtest_id": "b", "strategy_id": 1}]}"#)
        let rows = try await StrategyRepository(client: stub.client).agentResults()
        #expect(stub.last?.queryItems == ["limit": "10000"])
        #expect(rows.map(\.backtestId) == ["b"])
    }

    @Test func agentBestIsNilOnAnyError() async {
        let stub = DataStub(status: 500, json: #"{"detail": "x"}"#)
        #expect(await StrategyRepository(client: stub.client).agentBest() == nil)
    }

    @Test func computeBestByStrategyFoldsPerStrategy() {
        let results = [
            AgentResult(json: ["backtest_id": "a", "strategy_id": 1, "overall_profit": 10, "pnl_percent": 1, "created_at": "2025-01-01"]),
            AgentResult(json: ["backtest_id": "b", "strategy_id": 1, "overall_profit": 30, "pnl_percent": 0.5, "created_at": "2025-03-01"]),
            AgentResult(json: ["backtest_id": "c", "strategy_id": 1, "overall_profit": 5, "pnl_percent": 9, "created_at": "2025-02-01"]),
            AgentResult(json: ["backtest_id": "d"]),
        ]
        let m = StrategyRepository.computeBestByStrategy(results)
        #expect(m.count == 1)
        let best = m[1]
        #expect(best?.bestPnl == 30)
        #expect(best?.bestPnlBid == "b")
        #expect(best?.bestPct == 9)
        #expect(best?.bestPctBid == "c")
        #expect(best?.count == 3)
        #expect(best?.latest == "2025-03-01")
    }

    @Test func mergeStrategyRowsFallsBackToTop5ThenAllBest() {
        let strategies: [[String: JSON]] = [
            ["id": 1, "name": "One", "strategies": [["strategy": "a"]]],
            ["id": 2, "name": "Two"],
            ["id": 3, "name": "Three"],
        ]
        let best = StrategyRepository.computeBestByStrategy([
            AgentResult(json: ["backtest_id": "x", "strategy_id": 1, "overall_profit": 4, "pnl_percent": 2]),
        ])
        let top5: [[String: JSON]] = [["strategy_id": 2, "rank": 1, "overall_profit": "12.5", "pnl_percent": 3, "backtest_id": "t2"]]
        let allBest: [String: JSON] = ["3": ["best_pnl": 7, "best_pct": 1.5, "backtest_id": "a3"]]

        let rows = StrategyRepository.mergeStrategyRows(strategies, best, top5, allBest)
        #expect(rows.map(\.id) == [1, 2, 3])
        #expect(rows[0].bestPnl == 4 && rows[0].bestPnlBid == "x" && rows[0].runCount == 1 && rows[0].rank == nil)
        #expect(rows[0].subStrategyNames == ["a"])
        #expect(rows[1].bestPnl == 12.5 && rows[1].bestPnlBid == "t2" && rows[1].bestPct == 3)
        #expect(rows[1].bestPctBid == "t2" && rows[1].rank == 1 && rows[1].isTop5)
        #expect(rows[2].bestPnl == 7 && rows[2].bestPnlBid == "a3" && rows[2].bestPct == 1.5)
    }
}
