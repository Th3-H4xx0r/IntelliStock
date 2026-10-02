import Foundation
import Testing
@testable import IntelliStock

/// The model groups of test/features/agent_runs/agent_runs_test.dart. (The
/// `AgentRunsState` countdown/copyWith groups and the pagination helper are
/// the agent-runs controller and screen; that feature ports them.)
struct AgentControlTests {
    @Test func runningNotPausedIsRunning() {
        let ctrl = AgentControl(json: ["running": true, "paused": false])
        #expect(ctrl.isRunning)
        #expect(!ctrl.isPaused)
        #expect(!ctrl.isStopped)
    }

    @Test func runningAndPausedIsPaused() {
        let ctrl = AgentControl(json: ["running": true, "paused": true])
        #expect(ctrl.isPaused)
        #expect(!ctrl.isRunning)
    }

    @Test func notRunningIsStopped() {
        let ctrl = AgentControl(json: ["running": false, "paused": false])
        #expect(ctrl.isStopped)
        #expect(!ctrl.isRunning)
        #expect(!ctrl.isPaused)
    }

    @Test func emptyMapIsStopped() {
        #expect(AgentControl(json: [:]).isStopped)
    }
}

struct AgentRunTests {
    @Test func parsesAllStandardFields() {
        let run = AgentRun(json: [
            "id": "run-abc",
            "status": "completed",
            "cycle_id": "cycle-01",
            "name": "Morning run",
            "created_at": "2026-06-10T09:00:00Z",
            "final_result": "COMPLETED",
            "stages": [],
        ])
        #expect(run.id == "run-abc")
        #expect(run.status == "completed")
        #expect(run.cycleId == "cycle-01")
        #expect(run.name == "Morning run")
        #expect(run.finalResult == "COMPLETED")
        #expect(run.stages.isEmpty)
        #expect(run.createdAt != nil)
    }

    @Test func toleratesMissingOptionalFields() {
        let run = AgentRun(json: ["id": "x"])
        #expect(run.id == "x")
        #expect(run.status == "stopped")
        #expect(run.cycleId == nil)
        #expect(run.stages.isEmpty)
    }

    @Test func parsesNestedStages() {
        let run = AgentRun(json: [
            "id": "r1",
            "stages": [
                ["label": "Stock selection", "status": "completed", "stocks": ["AAPL", "TSLA"], "pnl": 250.0, "pnl_pct": 2.5],
                ["label": "Risk check", "status": "running"],
            ],
        ])
        #expect(run.stages.count == 2)
        #expect(run.stages[0].label == "Stock selection")
        #expect(close(run.stages[0].pnl?.double, 250.0, 0.01))
        #expect(run.stages[0].stocks == ["AAPL", "TSLA"])
        #expect(run.stages[1].pnl == nil)
    }
}

struct AgentStageTests {
    @Test func parsesPnlAndPnlPct() {
        let stage = AgentStage(json: [
            "label": "Eval",
            "status": "completed",
            "pnl": -150.75,
            "pnl_pct": -1.5,
            "stocks": ["NVDA"],
            "details": "All clear",
        ])
        #expect(stage.label == "Eval")
        #expect(close(stage.pnl?.double, -150.75, 0.01))
        #expect(close(stage.pnlPct?.double, -1.5, 0.001))
        #expect(stage.details == "All clear")
    }

    @Test func nullPnlTolerated() {
        let stage = AgentStage(json: ["label": "Start"])
        #expect(stage.pnl == nil)
        #expect(stage.pnlPct == nil)
        #expect(stage.stocks.isEmpty)
        #expect(stage.status == "pending")
    }
}

struct AgentRunsPageTests {
    @Test func parsesPaginationMetadata() {
        let page = AgentRunsPage(json: ["runs": [], "total": 47, "total_pages": 3, "page": 2])
        #expect(page.total == 47)
        #expect(page.totalPages == 3)
        #expect(page.page == 2)
        #expect(page.runs.isEmpty)
    }

    @Test func defaultsWhenMissing() {
        let page = AgentRunsPage(json: ["runs": []])
        #expect(page.page == 1)
        #expect(page.totalPages == 1)
        #expect(page.total == 0)
    }
}

struct AgentRepositoryTests {
    @Test func runsSendsPageAndPerPage() async throws {
        let stub = DataStub(json: #"{"runs": [], "total": 0}"#)
        _ = try await AgentRepository(client: stub.client).runs()
        #expect(stub.last?.path == "/agent/runs")
        #expect(stub.last?.queryItems == ["page": "1", "per_page": "20"])
    }

    @Test func setControlSendsOnlyGivenKeysAndSkipsAnEmptyRequest() async throws {
        let stub = DataStub()
        let repo = AgentRepository(client: stub.client)
        try await repo.setControl(running: true, specialRequest: "")
        #expect(stub.last?.jsonBody == ["running": true])
        try await repo.setControl(paused: false, specialRequest: "go")
        #expect(stub.last?.jsonBody == ["paused": false, "special_request": "go"])
        try await repo.forceStop("L1")
        #expect(stub.last?.path == "/agent/runs/L1/force-stop")
        #expect(stub.last?.method == "POST")
    }
}
