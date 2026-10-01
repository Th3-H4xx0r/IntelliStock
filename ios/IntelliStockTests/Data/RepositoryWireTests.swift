import Foundation
import Testing
@testable import IntelliStock

/// Method, path, query and body for the repositories the Dart suite had no
/// fake-client test for, checked against the Dart call sites.
struct InstanceRepositoryWireTests {
    @Test func listInstancesDropsKalshiAndCrypto() async throws {
        let stub = DataStub(json: #"{"instances": [{"id": "a"}, {"id": "k", "kind": "kalshi"}, {"id": "c", "kind": "crypto"}, {"id": "e", "kind": "equity"}]}"#)
        let rows = try await InstanceRepository(client: stub.client).listInstances()
        #expect(rows.map(\.id) == ["a", "e"])
    }

    @Test func createInstanceBodyHasOnlyTheDartKeys() async throws {
        let stub = DataStub(json: #"{"instance": {"id": "n1", "name": "N"}}"#)
        let repo = InstanceRepository(client: stub.client)
        let created = try await repo.createInstance(id: "n1", name: "", brokerageId: "", strategyId: "")
        #expect(created.name == "N")
        #expect(stub.last?.jsonBody == ["id": "n1", "granularity": "60", "run_command": false])
        _ = try await repo.createInstance(
            id: "n2", name: "Two", granularity: "900", runCommand: true, brokerageId: "b", maxUsage: 0.5, strategyId: "7"
        )
        #expect(stub.last?.jsonBody == [
            "id": "n2", "name": "Two", "granularity": "900", "run_command": true,
            "brokerage_id": "b", "max_usage": 0.5, "strategy_id": "7",
        ])
    }

    @Test func deleteSendsForceOnlyWhenForced() async throws {
        let stub = DataStub()
        let repo = InstanceRepository(client: stub.client)
        try await repo.deleteInstance("i1")
        #expect(stub.last?.method == "DELETE")
        #expect(stub.last?.url?.query == nil)
        try await repo.deleteInstance("i1", force: true)
        #expect(stub.last?.queryItems == ["force": "true"])
    }

    @Test func clearStateBodies() async throws {
        let stub = DataStub()
        let repo = InstanceRepository(client: stub.client)
        try await repo.clearState("i1", "nexus")
        #expect(stub.last?.jsonBody == ["scope": "nexus", "apply": false])
        _ = try await repo.previewClearState("i1", "all")
        #expect(stub.last?.jsonBody == ["scope": "all", "apply": false])
        _ = try await repo.applyClearState("i1", "all")
        #expect(stub.last?.path == "/instances/i1/clear-state")
        #expect(stub.last?.jsonBody == ["scope": "all", "apply": true, "confirm": "i1"])
    }

    @Test func linkBodies() async throws {
        let stub = DataStub()
        let repo = InstanceRepository(client: stub.client)
        try await repo.linkStrategy("i1", "42")
        #expect(stub.last?.jsonBody == ["strategy_id": 42])
        try await repo.linkStrategy("i1", "abc")
        #expect(stub.last?.jsonBody == ["strategy_id": "abc"])
        try await repo.linkDataBrokerage("i1", nil)
        #expect(stub.last?.path == "/instances/i1/link-data-brokerage")
        #expect(stub.last?.jsonBody == ["brokerage_id": nil])
        try await repo.unlinkBrokerage("i1")
        #expect(stub.last?.method == "PATCH")
        #expect(stub.last?.jsonBody == ["brokerage_id": ""])
        try await repo.removeStock("i1", "AAPL")
        #expect(stub.last?.method == "DELETE")
        #expect(stub.last?.path == "/instances/i1/stocks/AAPL")
    }

    @Test func backtestRequests() async throws {
        let stub = DataStub()
        let repo = InstanceRepository(client: stub.client)
        _ = try await repo.listBacktests("i1", page: 2)
        #expect(stub.last?.queryItems == ["page": "2", "per_page": "15", "sort_by": "completed_at", "sort_order": "desc"])
        try await repo.createBacktest(instanceId: "i1", stocks: ["A"], startDate: "2025-01-01", endDate: "2025-02-01")
        #expect(stub.last?.path == "/backtests")
        let body = stub.last?.jsonBody
        #expect(body?["instance_id"] == "i1" && body?["stocks"] == ["A"] && body?["granularity"] == "60")
        #expect(body?["initial_cash"].double == 100_000)
    }

    @Test func instanceParsesStocksAndGranularity() {
        let i = Instance(json: [
            "id": "x",
            "stocks": [["symbol": "A"], ["ticker": "B"], "C", "", ["symbol": ""], 5],
            "granularity": "300",
            "runCommand": true,
        ])
        #expect(i.name == "x")
        #expect(i.stocks == ["A", "B", "C"])
        #expect(i.granularityTimeIncrement == 300)
        #expect(i.runCommand)
        #expect(i.createdBy == "user")
        let row = InstanceBacktestRow(json: ["id": "b", "progress": 40.7]).copyWith(status: "running")
        #expect(row.progress == 40)
        #expect(row.status == "running")
    }
}

struct CryptoRepositoryWireTests {
    @Test func listKeepsOnlyCryptoAndUnwrapsInstance() async throws {
        let stub = DataStub(json: #"{"instances": [{"id": "a"}, {"id": "c", "kind": "crypto"}]}"#)
        let repo = CryptoRepository(client: stub.client)
        #expect(try await repo.listInstances().map(\.id) == ["c"])
        stub.respond(json: #"{"instance": {"id": "c2", "kind": "crypto", "crypto_config": {"band": 5}}}"#)
        let one = try await repo.getInstance("c2")
        #expect(one.cryptoConfig == ["band": 5])
    }

    @Test func instanceBacktestsAndCreateBacktest() async throws {
        let stub = DataStub(json: #"{"backtests": [{"id": "b1", "stocks": ["BTC/USD"]}]}"#)
        let repo = CryptoRepository(client: stub.client)
        let rows = try await repo.instanceBacktests("c1")
        #expect(rows.map(\.id) == ["b1"])
        #expect(stub.last?.queryItems == ["page": "1", "per_page": "20", "sort_by": "completed_at", "sort_order": "desc"])
        _ = try await repo.createBacktest(instanceId: "c1", stocks: ["BTC/USD"], startDate: "s", endDate: "e")
        let body = stub.last?.jsonBody
        #expect(body?["granularity"] == "900")
        #expect(body?["emulate_fee_venue"] == "default")
        #expect(body?["initial_cash"].double == 10_000)
    }

    @Test func accountEquityIsCashPlusMarketValueAndZeroOnError() async {
        let stub = DataStub(json: #"{"cash": 100, "positions": [{"marketValue": 50.5}, {"marketValue": null}, {"x": 1}]}"#)
        let repo = CryptoRepository(client: stub.client)
        #expect(await repo.accountEquity("b1") == 150.5)
        #expect(stub.last?.path == "/brokerages/b1/positions")
        stub.respond(status: 500, json: "{}")
        #expect(await repo.accountEquity("b1") == 0)
    }
}

struct KalshiRepositoryWireTests {
    @Test func queriesAndPaths() async throws {
        let stub = DataStub(json: #"{"edges": [{"market_ticker": "M", "side": "yes", "edge": 0.1}]}"#)
        let repo = KalshiRepository(client: stub.client)
        let edges = try await repo.edges("b1")
        #expect(stub.last?.path == "/brokerages/b1/kalshi/edges")
        #expect(stub.last?.queryItems == ["limit": "10"])
        #expect(edges.first?.marketTicker == "M")
        _ = try await repo.instanceDecisions("i1")
        #expect(stub.last?.queryItems == ["limit": "200"])
        _ = try await repo.instanceOrders("i1")
        #expect(stub.last?.queryItems == ["limit": "50"])
        try await repo.deleteInstance("i1")
        #expect(stub.last?.method == "DELETE")
        #expect(stub.last?.queryItems == ["force": "true"])
        try await repo.updateInstance("i1", ["live_enabled": false])
        #expect(stub.last?.method == "PATCH")
        #expect(stub.last?.path == "/instances/i1/kalshi/config")
    }

    @Test func createBacktestReturnsTheIdAsAString() async throws {
        let stub = DataStub(json: #"{"id": 77}"#)
        #expect(try await KalshiRepository(client: stub.client).createBacktest("b1", [:]) == "77")
        #expect(stub.last?.path == "/brokerages/b1/kalshi/backtests")
    }

    @Test func modelsKeepsMapsWithAnId() async throws {
        let stub = DataStub(json: #"{"models": [{"id": "a"}, {"name": "no id"}, {"id": null}]}"#)
        #expect(try await KalshiRepository(client: stub.client).models().map { $0["id"] } == ["a"])
    }

    @Test func portfolioParsesSeries() {
        let p = KalshiPortfolio(json: [
            "value": 10,
            "series": [["value": 1, "ts": "2026-01-01T00:00:00Z"], ["value": 2, "ts": "2026-01-01T01:00:00Z"]],
            "paper_series": [["pnl": -1, "ts": "2026-01-01T00:00:00Z"]],
            "paper_pnl": -1,
        ])
        #expect(p.series == [1, 2])
        #expect(p.seriesTs.map(DartDateTime.millisecondsSinceEpoch) == [1_767_225_600_000, 1_767_229_200_000])
        #expect(p.isPaper)
        #expect(KalshiInstance(json: [:]).name == "Kalshi instance")
    }
}

struct LearningRepositoryWireTests {
    @Test func controlBodies() async throws {
        let stub = DataStub()
        let repo = LearningRepository(client: stub.client)
        try await repo.setDocumentAllowlist(["179", "200"])
        #expect(stub.last?.path == "/learning/control")
        #expect(stub.last?.jsonBody == ["config": ["document_allowlist": ["179", "200"]]])
        try await repo.setWatchedInstances([])
        #expect(stub.last?.jsonBody == ["config": ["watched_instances": []]])
        try await repo.setMode("observe")
        #expect(stub.last?.jsonBody == ["config": ["mode": "observe"]])
        try await repo.setRunning(true)
        #expect(stub.last?.jsonBody == ["running": true])
        try await repo.decide("a1", "approve")
        #expect(stub.last?.path == "/learning/approvals/a1")
        #expect(stub.last?.jsonBody == ["decision": "approve"])
    }

    @Test func listsReadTheirKeysWithALimit() async throws {
        let stub = DataStub(json: #"{"pending": [{"id": "p1", "holds_forever": true}]}"#)
        let approvals = try await LearningRepository(client: stub.client).approvals()
        #expect(stub.last?.queryItems == ["limit": "100"])
        #expect(approvals.first?.holdsForever == true)
    }

    @Test func overviewEnabledIsTrueUnlessExplicitlyFalse() {
        #expect(LearningOverview(json: [:]).enabled)
        #expect(!LearningOverview(json: ["enabled": false]).enabled)
        #expect(LearningOverview(json: ["enabled": "no"]).enabled)
        #expect(LearningFunnel(json: ["buy_decided": 4, "buy_executed": 1]).buyConversionPct == 25)
        #expect(LearningFunnel(json: [:]).buyConversionPct == nil)
    }
}

struct LiveRepositoryWireTests {
    @Test func liveStateIsNilOn404AndThrowsOtherwise() async throws {
        let stub = DataStub(status: 404, json: #"{"detail": "not running"}"#)
        let repo = LiveRepository(client: stub.client)
        #expect(try await repo.liveState("i1") == nil)
        #expect(stub.last?.path == "/instances/i1/live-state")
        stub.respond(status: 500, json: #"{"detail": "boom"}"#)
        await #expect(throws: ApiError.self) { try await repo.liveState("i1") }
    }

    @Test func symbolHistoricalsSkipsTheRequestWhenEmpty() async throws {
        let stub = DataStub(json: #"{"results": {"AAPL": [{"ts": 1, "value": 2}, "junk"], "X": null}}"#)
        let repo = LiveRepository(client: stub.client)
        #expect(try await repo.symbolHistoricals([], "1D").isEmpty)
        #expect(stub.requests.isEmpty)
        let out = try await repo.symbolHistoricals(["AAPL", "X"], "1D")
        #expect(stub.last?.queryItems == ["symbols": "AAPL,X", "range": "1D"])
        #expect(out["AAPL"] == [HistPoint(ts: 1, value: 2)])
        #expect(out["X"] == [])
    }

    @Test func holdingOpensDropsUnparseableDates() async throws {
        let stub = DataStub(json: #"{"opens": {"AAPL": "2026-01-01T00:00:00Z", "BAD": "soon", "N": null}}"#)
        let out = try await LiveRepository(client: stub.client).holdingOpens("b1")
        #expect(stub.last?.path == "/brokerages/b1/holding-opens")
        #expect(out.keys.sorted() == ["AAPL"])
    }

    @Test func sendCommandBody() async throws {
        let stub = DataStub(json: #"{"command_id": "c1", "status": "completed", "result": {"ok": true}}"#)
        let r = try await LiveRepository(client: stub.client).sendCommand("i1", "close_position", ["symbol": "AAPL"])
        #expect(stub.last?.path == "/instances/i1/live-command")
        #expect(stub.last?.jsonBody == ["type": "close_position", "payload": ["symbol": "AAPL"]])
        #expect(r.isTerminal)
        #expect(r.result == ["ok": true])
    }
}

struct ModelRepositoryWireTests {
    @Test func listAcceptsABareListOrAModelsMap() async throws {
        let stub = DataStub(json: #"[{"id": "a", "input_cost_per_1m": 3}]"#)
        let repo = ModelRepository(client: stub.client)
        let models = try await repo.list()
        #expect(models.map(\.id) == ["a"])
        #expect(models.first?.inputCostPer1m == 3)
        stub.respond(json: #"{"models": [{"id": "b"}]}"#)
        #expect(try await repo.list().map(\.id) == ["b"])
        stub.respond(json: #""nope""#)
        #expect(try await repo.list().isEmpty)
    }

    @Test func createAndUpdateUnwrapTheModelEnvelope() async throws {
        let stub = DataStub(json: #"{"created": true, "model": {"id": "m1", "name": "M"}}"#)
        let repo = ModelRepository(client: stub.client)
        #expect(try await repo.create(["name": "M"]).id == "m1")
        stub.respond(json: #"{"id": "bare"}"#)
        #expect(try await repo.update("bare", [:]).id == "bare")
        #expect(stub.last?.method == "PUT")
        #expect(stub.last?.path == "/models/bare")
    }

    @Test func deleteForcesAndClaudeModelsQueryOnlyWithAPath() async throws {
        let stub = DataStub()
        let repo = ModelRepository(client: stub.client)
        try await repo.delete("m1")
        #expect(stub.last?.queryItems == ["force": "true"])
        _ = try await repo.claudeModels()
        #expect(stub.last?.url?.query == nil)
        _ = try await repo.claudeModels(cliPath: "")
        #expect(stub.last?.url?.query == nil)
        _ = try await repo.claudeModels(cliPath: "/usr/bin/claude")
        #expect(stub.last?.queryItems == ["cli_path": "/usr/bin/claude"])
        _ = try await repo.codexInstall()
        #expect(stub.last?.jsonBody == [:])
        _ = try await repo.claudeLoginSubmit("j1", "CODE")
        #expect(stub.last?.path == "/claude/login/j1/submit")
        #expect(stub.last?.jsonBody == ["code": "CODE"])
    }

    @Test func llmTestResultStringifiesStructuredFields() {
        let r = LlmTestResult(json: ["smoke_response": ["text": "hi"], "latency_ms": 12.9, "message": 5])
        #expect(r.smokeResponse == #"{"text":"hi"}"#)
        #expect(r.latencyMs == 12)
        #expect(r.message == "5")
        #expect(ClaudeModelOption(json: ["value": "opus", "label": ""]).label == "opus")
        #expect(CodexStatus(json: [:]).installMethod == "unknown")
    }
}

struct SmallRepositoryWireTests {
    @Test func authAndOnboarding() async throws {
        let stub = DataStub(json: #"{"access_token": "t", "user": {"username": "u"}}"#)
        let auth = AuthRepository(client: stub.client)
        let login = try await auth.login("u", "p")
        #expect(stub.last?.method == "POST")
        #expect(stub.last?.path == "/auth/login")
        #expect(stub.last?.jsonBody == ["username": "u", "password": "p"])
        #expect(login["access_token"] == "t")
        _ = try await auth.fetchMe()
        #expect(stub.last?.path == "/auth/me")
        let onboarding = OnboardingRepository(client: stub.client)
        _ = try await onboarding.complete()
        #expect(stub.last?.path == "/onboarding/complete")
        _ = try await onboarding.reset()
        #expect(stub.last?.path == "/onboarding/reset")
        _ = try await onboarding.state()
        #expect(stub.last?.method == "GET")
    }

    @Test func backtestRepository() async throws {
        let stub = DataStub(json: #"{"backtests": [{"id": "b1"}], "total": 1}"#)
        let repo = BacktestRepository(client: stub.client)
        let page = try await repo.list(page: 3)
        #expect(stub.last?.queryItems == ["page": "3", "per_page": "15", "sort_by": "completed_at", "sort_order": "desc"])
        #expect(page.backtests.map(\.id) == ["b1"])
        _ = try await repo.logs("b1", sinceLine: 40)
        #expect(stub.last?.path == "/backtests/b1/logs")
        #expect(stub.last?.queryItems == ["since_line": "40"])
        _ = try await repo.action("b1", "pause")
        #expect(stub.last?.method == "POST")
        #expect(stub.last?.path == "/backtests/b1/pause")
        try await repo.delete("b1")
        #expect(stub.last?.method == "DELETE")
        _ = try await repo.graphData("b1")
        #expect(stub.last?.path == "/backtests/b1/graph-data")
    }

    @Test func brokerageRepository() async throws {
        let stub = DataStub(json: #"{"accounts": [{"id": "b1", "alpaca_paper": false, "equity": "1000.5", "alpaca_account_number": "PA1"}]}"#)
        let repo = BrokerageRepository(client: stub.client)
        let list = try await repo.list()
        #expect(list.first?.paper == false)
        #expect(list.first?.equity == 1000.5)
        #expect(list.first?.accountNumber == "PA1")
        #expect(list.first?.brokerageType == "alpaca")
        _ = try await repo.edit("b1", ["account_name": "X"])
        #expect(stub.last?.method == "PUT")
        #expect(stub.last?.path == "/brokerages/b1")
        _ = try await repo.testAlpaca(["key": "k"])
        #expect(stub.last?.path == "/brokerages/test-alpaca")
        #expect(Brokerage(json: [:]).paper)
        #expect(list.first?.toJSON()["equity"] == 1000.5)
    }

    @Test func nexusRepository() async throws {
        let stub = DataStub(json: #"{"control": {"running": true}}"#)
        let repo = NexusRepository(client: stub.client)
        #expect(try await repo.status().serviceRunning)
        try await repo.control(["action": "start", "selected_phases": [1, 2]])
        #expect(stub.last?.path == "/nexus/control")
        #expect(stub.last?.jsonBody == ["action": "start", "selected_phases": [1, 2]])
        try await repo.deleteEdges(["phases": [3]])
        #expect(stub.last?.path == "/nexus/delete-edges")
        _ = try await repo.cache()
        #expect(stub.last?.path == "/nexus/cache")
    }
}
