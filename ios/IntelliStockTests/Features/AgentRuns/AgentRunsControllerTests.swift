import Foundation
import Testing
@testable import IntelliStock

/// `test/features/agent_runs/agent_runs_test.dart` — the controller parts.
@Suite struct AgentRunsStateTests {
    private let now = Date(timeIntervalSince1970: 1_700_000_000)

    @Test func countdownFractionIsZeroWithoutASchedule() {
        #expect(AgentRunsState().countdownFraction(now: now) == 0)
    }

    @Test func countdownSecsRemainingIsZeroWithoutASchedule() {
        #expect(AgentRunsState().countdownSecsRemaining(now: now) == 0)
    }

    @Test func countdownFractionIsBetweenZeroAndOneDuringACountdown() {
        var s = AgentRunsState()
        s.scheduledResumeAt = now.addingTimeInterval(30)
        s.scheduledTotalMs = 60_000
        #expect(s.countdownFraction(now: now) == 0.5)
    }

    @Test func countdownFractionApproachesOneAsTimeElapses() {
        var s = AgentRunsState()
        s.scheduledResumeAt = now.addingTimeInterval(0.001)
        s.scheduledTotalMs = 60_000
        #expect(s.countdownFraction(now: now) > 0.99)
        #expect(s.countdownFraction(now: now.addingTimeInterval(10)) == 1)
    }

    @Test func countdownFractionIsZeroWhenTheTotalIsZero() {
        var s = AgentRunsState()
        s.scheduledResumeAt = now.addingTimeInterval(300)
        s.scheduledTotalMs = 0
        #expect(s.countdownFraction(now: now) == 0)
    }

    @Test func countdownSecsRemainingIsClamped() {
        var s = AgentRunsState()
        s.scheduledResumeAt = now.addingTimeInterval(15)
        s.scheduledTotalMs = 30_000
        #expect(s.countdownSecsRemaining(now: now) == 15)
        #expect(s.countdownSecsRemaining(now: now.addingTimeInterval(20)) == 0)
        s.scheduledTotalMs = 10_000
        #expect(s.countdownSecsRemaining(now: now) == 10)
    }

    @Test func countdownLabel() {
        #expect(agentCountdownLabel(0) == "00:00")
        #expect(agentCountdownLabel(65) == "01:05")
        #expect(agentCountdownLabel(3600) == "60:00")
    }

    @Test func paginationEllipsis() {
        #expect(AgentRunsPagination.items(page: 3, total: 5) == [1, 2, 3, 4, 5])
        let first = AgentRunsPagination.items(page: 1, total: 10)
        #expect(first.first == 1 && first.last == 10 && first.contains(nil))
        let middle = AgentRunsPagination.items(page: 5, total: 10)
        #expect(middle.filter { $0 == nil }.count == 2)
        #expect(middle.contains(5))
        let last = AgentRunsPagination.items(page: 10, total: 10)
        #expect(last.last == 10)
        #expect(last[last.count - 2] != nil)
        #expect(AgentRunsPagination.items(page: 1, total: 1) == [1])
    }

    @Test func cyclesGroupInFirstSeenOrder() {
        let runs = [
            AgentRun(json: ["id": "a", "cycle_id": "c1"]),
            AgentRun(json: ["id": "b", "cycle_id": "c2"]),
            AgentRun(json: ["id": "c", "cycle_id": "c1"]),
            AgentRun(json: ["id": "d"]),
        ]
        let cycles = AgentRunCycle.group(runs)
        #expect(cycles.map(\.cycleId) == ["c1", "c2", "d"])
        #expect(cycles[0].runs.map(\.id) == ["a", "c"])
    }
}

/// The controller's fetch, actions and countdown against a stub.
@MainActor
@Suite struct AgentRunsModelTests {
    private func make(clock: ManualClock = ManualClock(), now: @escaping () -> Date = Date.init)
        -> (AgentRunsModel, DataStub, ChatStubRoutes)
    {
        let stub = DataStub()
        let routes = ChatStubRoutes()
        routes.stub = stub
        routes.set("GET /agent/runs", #"{"runs":[{"id":"r1","status":"running"}],"total":41,"total_pages":3,"page":1}"#)
        routes.set("GET /agent/control", #"{"running":true,"paused":false}"#)
        routes.set("POST /agent/control", "{}")
        stub.handler = { routes.answer($0) }
        let client = stub.client
        let model = AgentRunsModel(repository: { AgentRepository(client: client) }, now: now, sleep: clock.sleep)
        return (model, stub, routes)
    }

    private func body(_ request: URLRequest?) throws -> JSON {
        try JSON(data: request?.httpBody ?? Data())
    }

    @Test func fetchLoadsRunsAndControl() async {
        let (model, stub, _) = make()
        await model.refreshNow()
        let s = model.value
        #expect(s?.runs.map(\.id) == ["r1"])
        #expect(s?.total == 41 && s?.totalPages == 3)
        #expect(s?.control.isRunning == true)
        let runs = stub.requests.first { $0.path == "/agent/runs" }
        #expect(runs?.queryPairs.contains { $0 == ("page", "1") } == true)
        #expect(runs?.queryPairs.contains { $0 == ("per_page", "20") } == true)
    }

    @Test func actionsSendTheControlBodies() async throws {
        let (model, stub, _) = make()
        await model.refreshNow()

        await model.startAgent(specialRequest: "tech")
        #expect(try body(stub.requests.last { $0.method == "POST" }) == ["running": true, "special_request": "tech"])
        await model.pauseAgent()
        #expect(try body(stub.requests.last { $0.method == "POST" }) == ["paused": true])
        await model.resumeAgentNow()
        #expect(try body(stub.requests.last { $0.method == "POST" }) == ["paused": false])
        await model.stopAgent()
        #expect(try body(stub.requests.last { $0.method == "POST" }) == ["running": false])
        #expect(model.value?.busy == false)
    }

    @Test func aFailedActionShowsTheError() async {
        let (model, _, routes) = make()
        await model.refreshNow()
        routes.set("POST /agent/control", #"{"detail":"agent offline"}"#, status: 503)
        await model.pauseAgent()
        #expect(model.value?.errorMessage == "agent offline")
        #expect(model.value?.busy == false)
    }

    @Test func forceStopAndPaging() async {
        let (model, stub, routes) = make()
        routes.set("POST /agent/runs/r1/force-stop", "{}")
        await model.refreshNow()
        await model.forceStop("r1")
        #expect(stub.requests.contains { $0.method == "POST" && $0.path == "/agent/runs/r1/force-stop" })

        routes.set("GET /agent/runs", #"{"runs":[],"total":41,"total_pages":3,"page":2}"#)
        await model.goToPage(2)
        #expect(stub.requests.last { $0.path == "/agent/runs" }?.queryPairs.contains { $0 == ("page", "2") } == true)
        await model.setPerPage(50)
        let last = stub.requests.last { $0.path == "/agent/runs" }
        #expect(last?.queryPairs.contains { $0 == ("per_page", "50") } == true)
        #expect(last?.queryPairs.contains { $0 == ("page", "1") } == true)
    }

    @Test func scheduledResumeCountsDownThenResumes() async throws {
        let clock = ManualClock()
        var current = Date(timeIntervalSince1970: 1_700_000_000)
        let (model, stub, routes) = make(clock: clock, now: { current })
        routes.set("GET /agent/control", #"{"running":true,"paused":true}"#)
        await model.refreshNow()

        await model.scheduleResume(1)
        #expect(model.value?.scheduledTotalMs == 60_000)
        #expect(model.value?.countdownSecsRemaining(now: current) == 60)

        current = current.addingTimeInterval(30)
        await clock.advance(by: .seconds(1))
        #expect(model.tick == 1)
        #expect(model.value?.scheduledResumeAt != nil)

        current = current.addingTimeInterval(31)
        await clock.advance(by: .seconds(1))
        #expect(await eventually { stub.requests.contains { $0.method == "POST" } })
        #expect(try body(stub.requests.last { $0.method == "POST" }) == ["paused": false])
        #expect(model.value?.scheduledResumeAt == nil)
    }

    @Test func resumeNowAndCancel() async {
        let (model, stub, routes) = make()
        routes.set("GET /agent/control", #"{"running":true,"paused":true}"#)
        await model.refreshNow()
        await model.scheduleResume(5)
        model.cancelCountdown()
        #expect(model.value?.scheduledResumeAt == nil)
        #expect(model.value?.scheduledTotalMs == 0)
        #expect(!stub.requests.contains { $0.method == "POST" })

        await model.scheduleResume(0)
        #expect(stub.requests.contains { $0.method == "POST" })
    }

    @Test func anExternalResumeCancelsTheCountdown() async {
        let (model, _, routes) = make()
        routes.set("GET /agent/control", #"{"running":true,"paused":true}"#)
        await model.refreshNow()
        await model.scheduleResume(5)
        routes.set("GET /agent/control", #"{"running":true,"paused":false}"#)
        await model.refreshNow()
        #expect(model.value?.scheduledResumeAt == nil)
    }

    @Test func theFirstFetchFailingIsAnError() async {
        let (model, _, routes) = make()
        routes.set("GET /agent/control", #"{"detail":"down"}"#, status: 500)
        await model.refreshNow()
        #expect(model.state.errorMessage == "down")
    }
}
