import Foundation
import Testing
@testable import IntelliStock

/// Dart maps are insertion-ordered, so a screen that iterated a model's map
/// showed the server's key order. Each test parses JSON text whose keys are
/// NOT alphabetical (`zeta`, `alpha`, `mid`) and checks the model keeps that
/// order, including the bytes the repositories send.
private func parse(_ text: String) throws -> JSON { try JSON(data: Data(text.utf8)) }

private let unsorted = #"{"zeta": 1, "alpha": "two", "mid": [3]}"#
private let unsortedKeys = ["zeta", "alpha", "mid"]

struct ModelOrderTests {
    @Test func subStrategyConfigMergesConditionsThenConfigInOrder() throws {
        let sub = SubStrategy(json: try parse(#"""
        {"strategy": "rsi",
         "conditions": {"zeta": 1, "alpha": 2, "blank": ""},
         "config": {"mid": 3, "alpha": 4, "omega": null}}
        """#))
        // Dart: merged[key] = value keeps an existing key's slot and appends new keys.
        #expect(sub.config.keys == ["zeta", "alpha", "mid"])
        #expect(sub.config["alpha"] == 4)
    }

    @Test func backtestSubStrategyConditionsAndConfig() throws {
        let sub = BacktestSubStrategy(json: try parse(#"{"conditions": \#(unsorted), "config": {"z": 1, "a": 2}}"#))
        #expect(sub.conditions.keys == unsortedKeys)
        #expect(sub.config.keys == ["z", "a"])
    }

    @Test func learningFindingEvidence() throws {
        #expect(LearningFinding(json: try parse(#"{"evidence": \#(unsorted)}"#)).evidence.keys == unsortedKeys)
    }

    @Test func recentCallRawKeepsTheWholeCallInOrder() throws {
        let call = RecentCall(json: try parse(#"{"ts": 5, "provider": "x", "id": "c1", "call_site": "s"}"#))
        #expect(call.raw.keys == ["ts", "provider", "id", "call_site"])
        // The detail dialog's JsonEncoder.withIndent output follows that order.
        #expect(try JSON.object(call.raw).dartEncoded(indent: "  ").components(separatedBy: "\n")[1] == #"  "ts": 5,"#)
    }

    @Test func nexusGraphSummaryNodeCounts() throws {
        let summary = NexusGraphSummary(json: try parse(#"{"node_counts": {"companies": 9, "edge_intervals": 2, "agencies": 1}}"#))
        #expect(summary.nodeCounts.keys == ["companies", "edge_intervals", "agencies"])
    }

    @Test func toolCallArguments() throws {
        let tool = ToolCall(json: try parse(#"{"name": "t", "arguments": \#(unsorted)}"#))
        #expect(tool.arguments.keys == unsortedKeys)
        #expect(try JSON.object(tool.arguments).dartEncoded() == #"{"zeta":1,"alpha":"two","mid":[3]}"#)
    }

    @Test func chatMessageBlocks() throws {
        let msg = ChatMessage(json: try parse(#"{"blocks": [{"type": "stat", "value": "5", "label": "L"}]}"#))
        #expect(msg.blocks.first?.keys == ["type", "value", "label"])
    }

    @Test func backtestDecisionPlaybackEventAndMetadataRaw() throws {
        #expect(BacktestDecision(json: try parse(unsorted)).rawJson.keys == unsortedKeys)
        let playback = PlaybackData(json: try parse(#"{"events": [\#(unsorted)], "metadata": {"z": 1, "initial_cash": 5}}"#))
        #expect(playback.events.first?.raw.keys == unsortedKeys)
        #expect(playback.metadata.extra.keys == ["z", "initial_cash"])
    }

    @Test func instanceNestedMaps() throws {
        let instance = Instance(json: try parse(#"""
        {"id": "i", "brokerage": \#(unsorted), "strategy": {"z": 1, "a": 2},
         "crypto_config": {"band": "medium", "allocations": [{"symbol": "BTC/USD", "pct": 60}]}}
        """#))
        #expect(instance.brokerage?.keys == unsortedKeys)
        #expect(instance.strategy?.keys == ["z", "a"])
        #expect(instance.cryptoConfig?.keys == ["band", "allocations"])
    }

    @Test func commandResultResult() throws {
        #expect(CommandResult(json: try parse(#"{"result": \#(unsorted)}"#)).result?.keys == unsortedKeys)
    }

    @Test func swingSignalProposal() throws {
        let signal = SwingSignal(json: try parse(#"{"id": "s", "proposal": {"stop": 1, "entry": 2, "target": 3}}"#))
        #expect(signal.proposal.keys == ["stop", "entry", "target"])
    }

    @Test func nexusControlPhaseRowsAndBootstrapPhases() throws {
        let ctrl = NexusControl(json: try parse(#"{"delete_operation_phase_rows": [\#(unsorted)]}"#))
        #expect(ctrl.deleteOperationPhaseRows.first?.keys == unsortedKeys)
        let boot = NexusBootstrap(json: try parse(#"{"phases": [\#(unsorted)]}"#))
        #expect(boot.phases.first?.keys == unsortedKeys)
    }

    @Test func servicesSnapshotControlMaps() async throws {
        let stub = DataStub()
        stub.handler = { request in
            request.path == "/agent/control" ? (200, #"{"running": true, "paused": false, "cycle": 3}"#) : (200, "{}")
        }
        let snap = await DashboardRepository(client: stub.client).services()
        #expect(snap.agentControl?.keys == ["running", "paused", "cycle"])
    }

    @Test func notificationPrefsWriteCategoriesInServerOrder() throws {
        let prefs = NotificationPrefs(json: try parse(#"""
        {"categories": {"order_fill": {"discord": true, "push": false},
                        "halt": {"discord": false, "push": true},
                        "crash_loop": {"discord": true, "push": true}}}
        """#))
        #expect(prefs.categoryOrder == ["order_fill", "halt", "crash_loop"])
        let next = prefs
            .withRoute("halt", CategoryRoute(discord: true, push: true))
            .withRoute("drawdown_halt", CategoryRoute(discord: false, push: false))
        // An existing category keeps its slot; a new one is appended, as in Dart.
        #expect(try next.toJSON().dartEncoded() == #"{"categories":{"order_fill":{"discord":true,"push":false},"halt":{"discord":true,"push":true},"crash_loop":{"discord":true,"push":true},"drawdown_halt":{"discord":false,"push":false}}}"#)
    }

    @Test func brokerageToJSONFollowsTheDartLiteral() throws {
        let b = Brokerage(json: try parse(#"{"id": "b1", "status": "active", "account_name": "Main", "equity": 5}"#))
        #expect(b.toJSON().orderedObject?.keys == ["id", "brokerage_type", "account_name", "status", "paper", "equity"])
    }
}

struct RepositoryOrderTests {
    @Test func rawMapResponsesKeepServerOrder() async throws {
        let stub = DataStub(json: #"{"strategy": {"name": "N", "id": 7, "description": "d"}}"#)
        #expect(try await StrategyRepository(client: stub.client).get("7").keys == ["name", "id", "description"])
        stub.respond(json: unsorted)
        #expect(try await KalshiRepository(client: stub.client).instanceDetail("k1").keys == unsortedKeys)
        stub.respond(json: #"{"by_strategy": {"9": {}, "1": {}, "5": {}}}"#)
        #expect(try await StrategyRepository(client: stub.client).bestPerStrategy().keys == ["9", "1", "5"])
    }

    @Test func requestBodiesMatchDartBytes() async throws {
        let stub = DataStub()
        let instances = InstanceRepository(client: stub.client)
        _ = try await instances.createInstance(
            id: "n2", name: "Two", granularity: "900", runCommand: true, brokerageId: "b", maxUsage: 0.5, strategyId: "7"
        )
        #expect(stub.last?.httpBody.map { String(decoding: $0, as: UTF8.self) }
            == #"{"id":"n2","name":"Two","granularity":"900","run_command":true,"brokerage_id":"b","max_usage":0.5,"strategy_id":"7"}"#)
        try await instances.createBacktest(instanceId: "i1", stocks: ["A"], startDate: "s", endDate: "e")
        #expect(stub.last?.httpBody.map { String(decoding: $0, as: UTF8.self) }
            == #"{"instance_id":"i1","stocks":["A"],"start_date":"s","end_date":"e","granularity":"60","initial_cash":100000.0}"#)
        _ = try await StrategyRepository(client: stub.client).update("1", ["name": "X", "strategies": []], preserveHistory: true)
        #expect(stub.last?.httpBody.map { String(decoding: $0, as: UTF8.self) }
            == #"{"name":"X","strategies":[],"preserve_history":true}"#)
    }
}
