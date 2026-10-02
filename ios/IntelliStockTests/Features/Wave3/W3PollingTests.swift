import Foundation
import Testing
@testable import IntelliStock

// Wave 3 fixes: polling and timers.

/// Finding 2: a scheduled agent resume survives leaving Agent Runs and a
/// failed poll, as Flutter's periodic timer did.
@MainActor
@Suite struct W3AgentResumeTests {
    private func make(_ clock: ManualClock, now: @escaping () -> Date) -> (AgentRunsModel, DataStub, ChatStubRoutes) {
        let stub = DataStub()
        let routes = ChatStubRoutes()
        routes.stub = stub
        routes.set("GET /agent/runs", #"{"runs":[],"total":0,"total_pages":1,"page":1}"#)
        routes.set("GET /agent/control", #"{"running":true,"paused":true}"#)
        routes.set("POST /agent/control", "{}")
        stub.handler = { routes.answer($0) }
        let client = stub.client
        let model = AgentRunsModel(repository: { AgentRepository(client: client) }, now: now, sleep: clock.sleep)
        return (model, stub, routes)
    }

    private func resumes(_ stub: DataStub) -> Int {
        stub.requests.filter { $0.method == "POST" && $0.jsonBody == ["paused": false] }.count
    }

    @Test func theCountdownSurvivesLeavingTheScreen() async {
        let clock = ManualClock()
        var current = Date(timeIntervalSince1970: 1_700_000_000)
        let (model, stub, _) = make(clock, now: { current })
        let poll = Task { await model.poll(lifecycle: nil) }
        #expect(await eventually { model.value != nil })
        await model.scheduleResume(1)
        poll.cancel()
        await poll.value

        let countdown = model.countdownTask
        #expect(countdown != nil)
        current = current.addingTimeInterval(61)
        await clock.advance(by: .seconds(1))
        await countdown?.value
        #expect(resumes(stub) == 1)
        #expect(model.value?.scheduledResumeAt == nil)
    }

    @Test func aFailedPollSkipsATickInsteadOfEndingTheCountdown() async {
        let clock = ManualClock()
        var current = Date(timeIntervalSince1970: 1_700_000_000)
        let (model, stub, routes) = make(clock, now: { current })
        await model.refreshNow()
        await model.scheduleResume(1)
        let countdown = model.countdownTask

        routes.set("GET /agent/control", #"{"detail":"down"}"#, status: 500)
        await model.refreshNow()
        #expect(model.state.error != nil)
        current = current.addingTimeInterval(61)
        await clock.advance(by: .seconds(1))
        #expect(resumes(stub) == 0)
        #expect(model.countdownTask != nil)

        routes.set("GET /agent/control", #"{"running":true,"paused":true}"#)
        await model.refreshNow()
        #expect(model.value?.scheduledResumeAt != nil)
        await clock.advance(by: .seconds(1))
        await countdown?.value
        #expect(resumes(stub) == 1)
    }

    @Test func pollRestartsAStoppedCountdown() async {
        let clock = ManualClock()
        var current = Date(timeIntervalSince1970: 1_700_000_000)
        let (model, stub, _) = make(clock, now: { current })
        await model.refreshNow()
        await model.scheduleResume(1)
        model.stop()
        #expect(model.countdownTask == nil)

        let poll = Task { await model.poll(lifecycle: nil) }
        #expect(await eventually { model.countdownTask != nil })
        let countdown = model.countdownTask
        current = current.addingTimeInterval(61)
        await clock.advance(by: .seconds(1))
        await countdown?.value
        #expect(resumes(stub) == 1)
        poll.cancel()
        await poll.value
    }

    @Test func aCancelledScheduleCannotComeBackAfterAnError() async {
        let clock = ManualClock()
        let (model, _, routes) = make(clock, now: { Date(timeIntervalSince1970: 1_700_000_000) })
        await model.refreshNow()
        await model.scheduleResume(5)
        routes.set("GET /agent/control", #"{"detail":"down"}"#, status: 500)
        await model.refreshNow()
        model.cancelCountdown()
        routes.set("GET /agent/control", #"{"running":true,"paused":true}"#)
        await model.refreshNow()
        #expect(model.value?.scheduledResumeAt == nil)
    }
}

/// Finding 3: pending swing signals keep polling after a failed first load.
@MainActor
@Suite struct W3PendingSignalsPollTests {
    @Test func aFailedFirstLoadKeepsPollingAndRecovers() async {
        let manual = ManualClock()
        let repo = SwingFakeSource([swingTestSignal("a1")])
        repo.listError = ApiError(message: "Cannot reach the server.")
        let model = PendingSignalsModel(instanceId: "i1", source: { repo }, clock: { Date() })
        let task = Task { await model.poll(lifecycle: nil, sleep: manual.sleep) }
        #expect(await eventually { model.state.error != nil })

        repo.listError = nil
        await manual.advance(by: PendingSignalsModel.pollEvery)
        #expect(await eventually { model.state.value?.signals.map(\.id) == ["a1"] })

        // Healthy again, the next tick is an ordinary refresh.
        repo.pending = [swingTestSignal("a1"), swingTestSignal("b2", createdAt: "2026-09-24T13:16:00Z")]
        await manual.advance(by: PendingSignalsModel.pollEvery)
        #expect(await eventually { model.state.value?.signals.count == 2 })
        task.cancel()
        await task.value
    }
}

/// Finding 4 and the lifecycle minor: Codex install and login polls survive
/// the Form row scrolling away, pause in the background, and stop with the
/// sheet (the model's lifetime).
@MainActor
@Suite struct W3CodexPollTests {
    private func routes() -> (DataStub, ChatStubRoutes) {
        let stub = DataStub()
        let routes = ChatStubRoutes()
        routes.stub = stub
        routes.set("POST /codex/install", #"{"job_id":"j1","state":"running"}"#)
        routes.set("GET /codex/install/j1", #"{"state":"running","log_tail":["a"]}"#)
        stub.handler = { routes.answer($0) }
        return (stub, routes)
    }

    private func polls(_ stub: DataStub) -> Int {
        stub.requests.filter { $0.path == "/codex/install/j1" }.count
    }

    @Test func theInstallPollWaitsOutTheBackground() async {
        let (stub, _) = routes()
        let client = stub.client
        let clock = ManualClock()
        let lifecycle = AppLifecycle()
        let setup = CodexSetupModel(cliPath: "", repository: { ModelRepository(client: client) }, lifecycle: lifecycle, sleep: clock.sleep)
        await setup.startInstall()
        lifecycle.setForeground(false)
        await clock.advance(by: CodexSetupModel.installPollInterval)
        #expect(polls(stub) == 0)

        lifecycle.setForeground(true)
        #expect(await eventually { polls(stub) == 1 })
        setup.stop()
    }

    @Test func pollsStopWhenTheModelGoes() async {
        let (stub, _) = routes()
        let client = stub.client
        let clock = ManualClock()
        var setup: CodexSetupModel? = CodexSetupModel(cliPath: "", repository: { ModelRepository(client: client) }, sleep: clock.sleep)
        await setup?.startInstall()
        weak var gone = setup
        setup = nil
        #expect(gone == nil)
        await clock.advance(by: CodexSetupModel.installPollInterval)
        await clock.advance(by: CodexSetupModel.installPollInterval)
        #expect(polls(stub) == 0)
        #expect(clock.pendingCount == 0)
    }
}

/// Finding 11: a failed instances poll keeps the filter, busy flags and
/// error banner for the next good fetch.
@MainActor
@Suite struct W3InstancesLastGoodTests {
    @Test func aFailedPollKeepsTheFilter() async {
        let stub = DataStub(json: #"{"instances": [{"id": "a", "created_by": "ai"}, {"id": "u", "created_by": "user"}]}"#)
        let client = stub.client
        let model = InstancesModel(repository: { InstanceRepository(client: client) })
        await model.refreshNow()
        model.setFilter(.ai)

        stub.respond(status: 500, json: #"{"detail": "down"}"#)
        await model.refreshNow()
        #expect(model.state.error != nil)

        stub.respond(json: #"{"instances": [{"id": "a", "created_by": "ai"}, {"id": "u", "created_by": "user"}]}"#)
        await model.refreshNow()
        #expect(model.value?.filter == .ai)
        #expect(model.value?.filtered.map(\.id) == ["a"])
    }

    @Test func retryKeepsTheFilterToo() async {
        let stub = DataStub(json: #"{"instances": [{"id": "a", "created_by": "ai"}]}"#)
        let client = stub.client
        let model = InstancesModel(repository: { InstanceRepository(client: client) })
        await model.refreshNow()
        model.setFilter(.user)
        stub.respond(status: 500, json: #"{"detail": "down"}"#)
        await model.refreshNow()
        stub.respond(json: #"{"instances": [{"id": "a", "created_by": "ai"}]}"#)
        await model.reload()
        #expect(model.value?.filter == .user)
    }
}

/// The lifecycle minor: the instance backtest-progress poll pauses in the
/// background.
@MainActor
@Suite struct W3InstanceProgressPollTests {
    @Test func theProgressPollSkipsBackgroundTicks() async {
        let stub = DataStub()
        stub.handler = { request in
            if request.path.hasSuffix("/status") { return (200, #"{"status": "running", "progress": 0.5}"#) }
            if request.path.hasSuffix("/backtests") { return (200, #"{"backtests": [{"id": "b1", "status": "running"}], "total": 1}"#) }
            return (200, #"{"id": "i1"}"#)
        }
        let client = stub.client
        let model = InstanceDetailModel(instanceId: "i1", repository: { InstanceRepository(client: client) })
        await model.load()
        #expect(model.hasRunningBacktests)

        let clock = ManualClock()
        let lifecycle = AppLifecycle(isForeground: false)
        let task = Task { await model.runProgressPoll(lifecycle: lifecycle, sleep: clock.sleep) }
        func statusReads() -> Int { stub.requests.filter { $0.path == "/backtests/b1/status" }.count }
        await clock.advance(by: InstanceDetailModel.btPollEvery)
        #expect(statusReads() == 0)

        lifecycle.setForeground(true)
        await clock.advance(by: InstanceDetailModel.btPollEvery)
        #expect(await eventually { statusReads() == 1 })
        task.cancel()
        await task.value
    }
}

/// The live-trading minors: a poll cycle's history fetches belong to the
/// cycle, an older range never overwrites a newer one, and the command
/// poll pauses in the background.
@MainActor
@Suite struct W3LiveTradingFetchTests {
    private func stub(slowRange: String? = nil) -> DataStub {
        let stub = DataStub()
        stub.handler = { request in
            switch request.path {
            case "/instances/i1/live-state":
                return (200, #"{"status": "active", "equity": 1000, "trading_active": true, "positions": [{"symbol": "AAPL", "qty": 1}]}"#)
            case "/instances/i1/portfolio-history":
                let range = request.queryItems["range"] ?? ""
                if range == slowRange {
                    Thread.sleep(forTimeInterval: 0.3)
                    return (200, #"{"timestamps": [1], "values": [111]}"#)
                }
                return (200, #"{"timestamps": [1], "values": [222]}"#)
            case "/symbol-historicals":
                return (200, #"{"results": {"AAPL": [{"ts": 1, "value": 3}]}}"#)
            case "/instances/i1/live-command":
                return (200, #"{"command_id": "c1", "status": "pending"}"#)
            case "/live-commands/c1":
                return (200, #"{"command_id": "c1", "status": "pending"}"#)
            default:
                return (404, #"{"detail": "Not Found"}"#)
            }
        }
        return stub
    }

    @Test func aPollCycleFinishesItsHistoryFetches() async {
        let stub = stub()
        let client = stub.client
        let model = LiveTradingModel(instanceId: "i1", repository: { LiveRepository(client: client) })
        await model.load()
        await model.pollCycle()
        #expect(model.value?.equityHistory?.values == [222])
        #expect(model.value?.positionHistoricals["AAPL"]?.count == 1)
    }

    @Test func anOlderRangeNeverOverwritesANewerOne() async {
        let stub = stub(slowRange: "1W")
        let client = stub.client
        let model = LiveTradingModel(instanceId: "i1", repository: { LiveRepository(client: client) })
        await model.load()
        let older = Task { await model.setRange("1W") }
        #expect(await eventually { stub.requests.contains { $0.queryItems["range"] == "1W" } })
        await model.setRange("1M")
        await older.value
        #expect(model.value?.currentRange == "1M")
        #expect(model.value?.equityHistory?.values == [222])
    }

    @Test func theCommandPollWaitsOutTheBackground() async {
        let stub = stub()
        let client = stub.client
        let clock = ManualClock()
        let lifecycle = AppLifecycle()
        let model = LiveTradingModel(instanceId: "i1", repository: { LiveRepository(client: client) }, sleep: clock.sleep)
        let poll = Task { await model.poll(lifecycle: lifecycle) }
        #expect(await eventually { model.value != nil })
        await model.runCommand("halt", ["reason": "risk breach"])
        func statusReads() -> Int { stub.requests.filter { $0.path == "/live-commands/c1" }.count }

        lifecycle.setForeground(false)
        await clock.advance(by: LiveTradingModel.commandPollEvery)
        #expect(statusReads() == 0)
        lifecycle.setForeground(true)
        #expect(await eventually { statusReads() == 1 })
        poll.cancel()
        await poll.value
        // Dismissing ends the command poll at its next wake.
        model.dismissToast()
        await clock.advance(by: LiveTradingModel.commandPollEvery)
        #expect(clock.pendingCount == 0)
    }
}

/// The per-tick minors: cached derivations match what they replaced.
@MainActor
@Suite struct W3CachedDerivationTests {
    @Test func playbackHistoryIsParsedOnceAndStillFollowsTheFrame() async {
        let stub = DataStub(json: #"""
        {"events": [
          {"type": "portfolio", "date": "2026-01-02", "value": 100},
          {"type": "date", "label": "D"},
          {"type": "portfolio", "date": "2026-01-03", "value": 110}
        ], "metadata": {"initial_cash": 100}}
        """#)
        let client = stub.client
        let clock = ManualClock()
        let model = BacktestPlaybackModel(repository: { BacktestRepository(client: client) }, sleep: clock.sleep)
        await model.load("1")
        #expect(model.portfolioHistory.isEmpty)
        model.togglePlay()
        await clock.advance(by: .seconds(1))
        #expect(model.frameIndex == 0)
        #expect(model.portfolioHistory.map(\.value) == [100])
        await clock.advance(by: .seconds(2))
        #expect(model.frameIndex == 2)
        #expect(model.portfolioHistory.map(\.value) == [100, 110])
        let range = model.xRange()
        #expect(range.min == DartDateTime.tryParse("2026-01-02")!.addingTimeInterval(-86400))
        #expect(range.max == DartDateTime.tryParse("2026-01-03")!)
        model.stop()
    }

    @Test func kalshiPregameGamesAreGroupedWhenDecisionsLand() async {
        let stub = DataStub()
        stub.handler = { request in
            if request.path.hasSuffix("/decisions") {
                return (200, #"{"decisions": [{"match": "A vs B", "fixture_id": "f1", "side": "home", "edge": 0.05, "kickoff_ts": 1}], "summary": {}}"#)
            }
            return (200, "{}")
        }
        let client = stub.client
        let model = KalshiInstanceDetailModel(instanceId: "k1", repository: { KalshiRepository(client: client) })
        await model.loadAll()
        let d = model.decisions.value ?? JSONObject()
        #expect(model.pregameGames.count == KalshiPregame.games(KalshiPregame.rows(d)).count)
        #expect(model.pregameRowsEmpty == KalshiPregame.rows(d).isEmpty)
    }
}
