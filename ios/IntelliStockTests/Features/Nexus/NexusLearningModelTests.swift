import Foundation
import Testing
@testable import IntelliStock

/// The presentation helpers of nexus_screen.dart (the behaviour the shipped
/// screen had — see the nexus parity ruling on the Dart test replicas), the
/// Nexus controller, and the Learning provider and actions.
struct NexusFormatTests {
    @Test func fmtDurationMatchesTheScreen() {
        #expect(NexusFormat.fmtDuration(nil) == "")
        #expect(NexusFormat.fmtDuration(3.5) == "3.5s")
        #expect(NexusFormat.fmtDuration(59.9) == "59.9s")
        #expect(NexusFormat.fmtDuration(60) == "1m")
        #expect(NexusFormat.fmtDuration(90) == "1m 30s")
        #expect(NexusFormat.fmtDuration(3600) == "1h")
        #expect(NexusFormat.fmtDuration(3661) == "1h 1m")
        #expect(NexusFormat.fmtDuration(7260) == "2h 1m")
    }

    @Test func autoUpdateSummaryMatchesTheScreen() {
        #expect(NexusFormat.autoUpdateSummary(NexusControl(json: ["auto_update_enabled": false])) == "Disabled")
        #expect(NexusFormat.autoUpdateSummary(NexusControl(json: ["auto_update_enabled": true, "auto_update_interval_hours": 24])) == "Every 1 day")
        #expect(NexusFormat.autoUpdateSummary(NexusControl(json: ["auto_update_enabled": true, "auto_update_interval_hours": 168])) == "Every 7 days")
        #expect(NexusFormat.autoUpdateSummary(NexusControl(json: ["auto_update_enabled": true, "auto_update_interval_hours": 12])) == "Every 12 hours")
        #expect(NexusFormat.autoUpdateSummary(NexusControl(json: ["auto_update_enabled": true, "auto_update_interval_hours": 1])) == "Every 1 hour")
    }

    @Test func startBodyKeepsTheScreenKeys() {
        let off = NexusFormat.startBody(selectedPhases: [9, 1, 3], historyQuarters: 2, historicalMode: false, historicalStartDate: "2020-01-01", forceBootstrapRebuild: false)
        #expect(off.entries.map(\.key) == ["running", "phase7_history_quarters", "historical_mode_enabled", "selected_phases"])
        #expect(off["selected_phases"] == [1, 3, 9])
        let on = NexusFormat.startBody(selectedPhases: Array(1...14), historyQuarters: 1, historicalMode: true, historicalStartDate: "2019-01-01", forceBootstrapRebuild: true)
        #expect(on["historical_start_date"] == "2019-01-01")
        #expect(on["force_bootstrap_rebuild"] == true)
        #expect(on["selected_phases"]?.arrayValue.count == 14)
    }

    @Test func autoUpdateAndRebuildBodies() {
        let enabled = NexusFormat.autoUpdateBody(enabled: true, intervalHours: 24, startPhase: 3, endPhase: 14)
        #expect(enabled.entries.map(\.key) == ["auto_update_enabled", "auto_update_interval_hours", "auto_update_start_phase", "auto_update_end_phase", "running"])
        #expect(NexusFormat.autoUpdateBody(enabled: false, intervalHours: 24, startPhase: 3, endPhase: 14)["running"] == nil)
        let rebuild = NexusFormat.rebuildBody(destructive: true, forceBootstrap: false, cachePaths: ["/a"])
        #expect(rebuild.entries.map(\.key) == ["confirm", "destructive", "force_bootstrap_rebuild", "delete_cache_paths"])
        #expect(rebuild["confirm"] == true && rebuild["delete_cache_paths"] == ["/a"])
    }

    @Test func countsLabelsAndPills() {
        #expect(NexusFormat.fmtNum(JSON.int(1_234_567)) == "1.2M")
        #expect(NexusFormat.fmtNum(JSON.string("2500")) == "2.5k")
        #expect(NexusFormat.fmtNum(JSON.int(42)) == "42")
        #expect(NexusFormat.fmtNum(Int?.none) == "—")
        #expect(NexusFormat.fmtNum(JSON.string("x")) == "—")
        #expect(NexusFormat.bootstrapPill("completed").text == "Bootstrap Ready")
        #expect(NexusFormat.bootstrapPill("weird").text == "Bootstrap Pending")
        let c = NexusControl(json: ["auto_update_start_phase": 3, "auto_update_end_phase": 99])
        let r = NexusFormat.rangeLabels(c)
        #expect(r.start == "Phase 2b: SEC sector/industry" && r.end == "Phase 12: ETF universe")
        #expect(NexusFormat.friendlyStatus("building") == "Building")
        #expect(NexusFormat.friendlyStatus("none") == "Not running")
        #expect(NexusFormat.logFileName("build-1234567890") == "nexus-34567890.log")
        #expect(NexusFormat.logFileName(nil) == "nexus-latest.log")
    }
}

@MainActor
struct NexusModelTests {
    @Test func intervalFollowsBuildingAndActionsRefresh() async {
        let stub = DataStub(json: #"{"control": {"running": true}, "graph_build": {"status": "running"}}"#)
        let m = NexusModel(repository: { NexusRepository(client: stub.client) })
        await m.refreshNow()
        #expect(m.statusValue?.isBuilding == true)
        #expect(m.interval == .seconds(2))
        await m.postControl(["running": false])
        #expect(stub.requests.contains { $0.method == "POST" && $0.path == "/nexus/control" })
        #expect(!m.busy && m.errorMessage == nil)
    }

    @Test func actionFailureSetsTheMessage() async {
        let stub = DataStub()
        stub.handler = { req in
            req.httpMethod == "POST" ? (409, #"{"detail": "rebuild active"}"#) : (200, #"{"control": {}}"#)
        }
        let m = NexusModel(repository: { NexusRepository(client: stub.client) })
        await m.refreshNow()
        #expect(m.interval == .seconds(5))
        await m.deleteEdges(["selected_phases": [3]])
        #expect(m.errorMessage == "rebuild active")
        #expect(!m.busy)
    }

    @Test func firstLoadFailureShowsTheErrorView() async {
        let stub = DataStub(status: 500, json: #"{"detail": "down"}"#)
        let m = NexusModel(repository: { NexusRepository(client: stub.client) })
        await m.refreshNow()
        #expect(m.status.error != nil)
        #expect(await m.fetchCache() == nil)
    }
}

@MainActor
struct LearningModelTests {
    @Test func partialFailuresStillRender() async throws {
        let stub = DataStub()
        stub.handler = { req in
            switch req.url?.path {
            case "/learning/overview": return (200, #"{"mode": "observe", "open_findings": 2}"#)
            case "/learning/control": return (200, #"{"running": true, "config": {"mode": "propose"}}"#)
            case "/learning/approvals": return (200, #"{"pending": [{"id": "a1", "rung": "live_capped", "holds_forever": true}, {"id": "a2", "rung": "paper"}]}"#)
            default: return (500, #"{"detail": "missing"}"#)
            }
        }
        let snapshot = try await LearningModel.fetch(LearningRepository(client: stub.client))
        #expect(snapshot.engineRunning)
        #expect(snapshot.mode == "propose")
        #expect(snapshot.approvals.count == 2)
        #expect(snapshot.partialError == "findings: missing; runs: missing; noise floors: missing; targets: missing")
        #expect(snapshot.targetsLabel == "Documents & instances")
        #expect(!snapshot.isEmptyFailure)
    }

    @Test func nothingLoadedThrows() async {
        let stub = DataStub(status: 500, json: #"{"detail": "down"}"#)
        let m = LearningModel(repository: { LearningRepository(client: stub.client) })
        await m.load()
        #expect(m.state.errorMessage?.hasPrefix("overview: down") == true)
    }

    @Test func decideAndControlBodies() async {
        let stub = DataStub(json: "{}")
        let m = LearningModel(repository: { LearningRepository(client: stub.client) })
        #expect(await m.decide(LearningApproval(json: ["id": "ap1"]), "approved") == nil)
        #expect(stub.requests.first { $0.path == "/learning/approvals/ap1" }?.jsonBody == ["decision": "approved"])
        #expect(await m.setRunning(false) == nil)
        #expect(await m.setMode("act") == nil)
        let controls = stub.requests.filter { $0.path == "/learning/control" && $0.method == "POST" }.map(\.jsonBody)
        #expect(controls == [["running": false], ["config": ["mode": "act"]]])
        stub.respond(status: 400, json: #"{"detail": "nope"}"#)
        #expect(await m.decide(LearningApproval(json: ["id": "ap1"]), "rejected") == "Could not record that decision: nope")
        #expect(await m.setRunning(true) == "Could not change the engine: nope")
        #expect(await m.setMode("observe") == "Could not change the mode: nope")
    }

    @Test func saveTargetsWritesAllowlistThenWatched() async throws {
        let stub = DataStub(json: "{}")
        let m = LearningModel(repository: { LearningRepository(client: stub.client) })
        try await m.saveTargets(armed: ["200", "195"], watched: [])
        #expect(stub.requests.map(\.jsonBody) == [
            ["config": ["document_allowlist": ["200", "195"]]],
            ["config": ["watched_instances": []]],
        ])
    }

    @Test func severityColors() {
        #expect(LearningModel.severityColor("HIGH") == DS.Palette.danger)
        #expect(LearningModel.severityColor("medium") == DS.Palette.warning)
        #expect(LearningModel.ladder.count == 6)
    }
}
