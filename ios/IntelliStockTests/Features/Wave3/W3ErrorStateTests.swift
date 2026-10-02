import Foundation
import Testing
@testable import IntelliStock

// Wave 3 error and state minors.

/// Crypto card actions and Kalshi backtest stop/delete report their errors
/// (for a toast) instead of failing silently.
@MainActor
@Suite struct W3SilentFailureTests {
    @Test func cryptoActionsReturnTheirErrorAndStillRefetch() async {
        let stub = DataStub()
        stub.handler = { request in
            if request.method != "GET" { return (500, #"{"detail": "nope"}"#) }
            return (200, #"{"instances": [{"id": "c1", "kind": "crypto"}]}"#)
        }
        let client = stub.client
        let m = CryptoModel(repository: { CryptoRepository(client: client) })
        #expect(await m.start("c1") == "nope")
        #expect(await m.stop("c1") == "nope")
        #expect(await m.delete("c1") == "nope")
        #expect(m.instances.value?.map(\.id) == ["c1"])

        stub.handler = { _ in (200, #"{"instances": []}"#) }
        #expect(await m.start("c1") == nil)
    }

    @Test func kalshiBacktestStopAndDeleteReturnTheirError() async {
        let stub = DataStub()
        stub.handler = { request in
            if request.method != "GET" { return (409, #"{"detail": "already finished"}"#) }
            return (200, #"{"backtests": []}"#)
        }
        let client = stub.client
        let m = KalshiBacktestModel(instanceId: "i1", repository: { KalshiRepository(client: client) })
        #expect(await m.stopBacktest("bt1") == "already finished")
        #expect(await m.deleteBacktest("bt1") == "already finished")

        stub.handler = { _ in (200, #"{"backtests": []}"#) }
        #expect(await m.stopBacktest("bt1") == nil)
    }
}

/// Wave 1 deferred: one child request cancelled by the system (not the
/// caller) no longer throws away the five that answered.
@MainActor
@Suite struct W3TokenUsageCancelledChildTests {
    @Test func aCancelledChildIsOnePartialFailure() async throws {
        let stub = DataStub()
        stub.handler = { request in
            switch request.path {
            case "/llm-usage/summary": return (200, #"{"total_calls": 3}"#)
            case "/llm-usage/timeseries": throw URLError(.cancelled)
            default: return (200, "[]")
            }
        }
        let data = try await TokenUsageRepository(client: stub.client).fetchAllUnlessCancelled("7d")
        #expect(data.summary?.totalCalls == 3)
        #expect(data.partialError?.hasPrefix("1 of 6 requests failed") == true)
    }

    @Test func aCancelledCallerStillThrows() async {
        let stub = DataStub(json: "[]")
        let repo = TokenUsageRepository(client: stub.client)
        let task = Task { try await repo.fetchAllUnlessCancelled("7d") }
        task.cancel()
        await #expect(throws: CancellationError.self) { _ = try await task.value }
    }
}

/// The chat model picker can recover from a failed model list.
@MainActor
@Suite struct W3ChatModelsRetryTests {
    @Test func aFailedModelListReportsThenRetries() async {
        let stub = DataStub()
        stub.handler = { _ in (503, #"{"detail": "models down"}"#) }
        let client = stub.client
        let model = ChatbotModel(repository: { ChatbotRepository(client: client) })
        await model.loadModels()
        #expect(!model.state.modelsLoaded)
        #expect(model.state.error == "models down")

        stub.handler = { _ in (200, #"{"models": [{"id": "m1", "name": "One"}]}"#) }
        await model.retryModels()
        #expect(model.state.error == nil)
        #expect(model.state.modelsLoaded)
        #expect(model.state.models.map(\.id) == ["m1"])
    }
}
