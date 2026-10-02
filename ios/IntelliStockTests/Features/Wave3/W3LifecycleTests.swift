import Foundation
import Testing
@testable import IntelliStock

// Wave 3 fixes: stuck first loads, reappear and lifecycle.

/// Finding 1: a first load cancelled by leaving the screen (a tab switch
/// cancels `.task`) must be retried on return, never shown as empty.
@MainActor
@Suite struct W3StuckLoadingTests {
    @Test func loadableNeedsALoadUntilItHoldsAValue() {
        #expect(Loadable<Int>.loading.needsLoad)
        #expect(Loadable<Int>.failed(ApiError(message: "down")).needsLoad)
        #expect(!Loadable<Int>.loaded(1).needsLoad)
    }

    @Test func aCancelledStrategiesFetchStaysLoadingInsteadOfEmpty() async {
        let stub = DataStub(json: #"{"strategies": [{"id": 1, "name": "Alpha"}], "top5": [], "results": [], "by_strategy": {}}"#)
        let client = stub.client
        let model = StrategiesModel(repository: { StrategyRepository(client: client) })
        let task = Task { await model.fetchAll() }
        task.cancel()
        await task.value
        // Not "No strategies found.": the skeleton stays and the next appear retries.
        #expect(model.loading)
        #expect(model.needsLoad)

        await model.fetchAll()
        #expect(!model.loading)
        #expect(!model.needsLoad)
        #expect(model.rawStrategies.count == 1)
    }

    @Test func aCancelledStrategyDetailLoadIsRetried() async {
        let stub = DataStub(json: #"{"id": 7, "name": "Seven"}"#)
        let client = stub.client
        let model = StrategyDetailModel(strategyId: "7", repository: { StrategyRepository(client: client) })
        let task = Task { await model.load() }
        task.cancel()
        await task.value
        #expect(model.needsLoad)

        await model.load()
        #expect(!model.needsLoad)
        #expect(model.strategy != nil)
    }

    @Test func aCancelledPlaybackLoadIsRetried() async {
        let stub = DataStub(json: #"{"events": [{"type": "date", "label": "D1"}], "metadata": {}}"#)
        let client = stub.client
        let model = BacktestPlaybackModel(repository: { BacktestRepository(client: client) })
        let task = Task { await model.load("9") }
        task.cancel()
        await task.value
        #expect(model.needsLoad)

        await model.load("9")
        #expect(!model.needsLoad)
        #expect(model.events.count == 1)
    }
}

/// Finding 6: Learning never shows a cancellation as an error.
@MainActor
@Suite struct W3LearningCancellationTests {
    private func stub() -> DataStub {
        let stub = DataStub()
        stub.handler = { request in
            if request.url?.path == "/learning/findings" { throw URLError(.cancelled) }
            return (200, "{}")
        }
        return stub
    }

    @Test func aCancelledReadIsRethrownNotRecordedAsAPartialError() async {
        let stub = stub()
        let repo = LearningRepository(client: stub.client)
        await #expect(throws: CancellationError.self) {
            _ = try await LearningModel.fetch(repo)
        }
    }

    @Test func aCancelledLoadLeavesTheStateUntouched() async {
        let stub = stub()
        let client = stub.client
        let model = LearningModel(repository: { LearningRepository(client: client) })
        await model.load()
        #expect(model.state.isLoading)
    }
}

/// Finding 5: a detail screen that reappears reuses its model, so the form
/// keeps what was typed and only the polls restart.
@MainActor
@Suite struct W3KalshiBacktestReuseTests {
    @Test func reappearingKeepsTheFormAndOnlyRestartsTheBacktestPoll() async {
        let stub = DataStub()
        stub.handler = { request in
            if request.url?.path == "/models" { return (200, #"{"models": []}"#) }
            if request.url?.path.hasSuffix("/backtests") == true { return (200, #"{"backtests": []}"#) }
            return (200, #"{"brokerage_id": "b9", "config": {"edge_threshold": 0.05}}"#)
        }
        let client = stub.client
        let lifecycle = AppLifecycle()
        let model = KalshiBacktestModel(instanceId: "k1", repository: { KalshiRepository(client: client) })
        func count(_ suffix: String) -> Int { stub.requests.filter { $0.path.hasSuffix(suffix) }.count }

        let first = Task { await model.poll(lifecycle: lifecycle) }
        #expect(await eventually { count("/kalshi/backtests") == 1 })
        model.edit(.edge, "9")
        first.cancel()
        await first.value

        let second = Task { await model.poll(lifecycle: lifecycle) }
        #expect(await eventually { count("/kalshi/backtests") == 2 })
        second.cancel()
        await second.value

        #expect(count("/kalshi/detail") == 1)
        #expect(model.text(.edge) == "9")
        #expect(model.value(.edge) == 9)
    }
}

/// Finding 7 and the LiveLogsPanel minor: a log panel that leaves the screen
/// keeps its tailer and lines; coming back open resumes polling at once.
@MainActor
@Suite struct W3LogPanelReattachTests {
    private func tailer(_ stub: DataStub) -> LogTailer {
        LogTailer(client: stub.client, pathBuilder: { "/instances/i1/live-logs?since_line=\($0)" })
    }

    @Test func anOpenPanelResumesWhenItComesBack() async {
        let stub = DataStub(json: #"{"logs": ["one"], "next_line": 1, "final_status": "running"}"#)
        let tailer = tailer(stub)
        tailer.start()
        #expect(await eventually { tailer.state.lines.count == 1 })
        tailer.detach()
        #expect(tailer.isPaused)
        let before = stub.requests.count

        tailer.reattach(open: true, userPaused: false, foreground: true)
        #expect(await eventually { stub.requests.count == before + 1 })
        #expect(!tailer.isPaused)
        #expect(tailer.state.lines.count >= 1)
        tailer.dispose()
    }

    @Test func aClosedOrUserPausedPanelStaysQuiet() async {
        let stub = DataStub(json: #"{"logs": [], "next_line": 0}"#)
        let tailer = tailer(stub)
        tailer.start()
        #expect(await eventually { !tailer.state.loading })
        #expect(stub.requests.count == 1)
        tailer.detach()
        tailer.reattach(open: false, userPaused: false, foreground: true)
        tailer.reattach(open: true, userPaused: true, foreground: true)
        tailer.reattach(open: true, userPaused: false, foreground: false)
        #expect(tailer.isPaused)
        try? await Task.sleep(for: .milliseconds(100))
        #expect(stub.requests.count == 1)
        tailer.dispose()
    }
}

/// Lifecycle minors: playback stops playing when its screen goes, and a
/// backtest detail reload keeps what it shows.
@MainActor
@Suite struct W3ReappearMinorTests {
    @Test func stoppingPlaybackClearsPlaying() async {
        let stub = DataStub(json: #"{"events": [{"type": "date", "label": "D1"}, {"type": "date", "label": "D2"}], "metadata": {}}"#)
        let client = stub.client
        let clock = ManualClock()
        let model = BacktestPlaybackModel(repository: { BacktestRepository(client: client) }, sleep: clock.sleep)
        await model.load("9")
        model.togglePlay()
        #expect(model.isPlaying)
        model.stop()
        #expect(!model.isPlaying)
        // A resumed timer cannot advance a stopped playback.
        let frame = model.frameIndex
        await clock.advance(by: .seconds(5))
        #expect(model.frameIndex == frame)
    }

    @Test func aBacktestDetailReloadKeepsItsDataOnScreen() async {
        let stub = DataStub(json: #"{"status": "completed", "id": "7"}"#)
        let client = stub.client
        let model = BacktestDetailModel(id: "7", repository: { BacktestRepository(client: client) })
        await model.load()
        #expect(model.summary != nil)

        stub.handler = { _ in
            Thread.sleep(forTimeInterval: 0.3)
            return (500, #"{"detail": "down"}"#)
        }
        let before = stub.requests.count
        let task = Task { await model.load() }
        #expect(await eventually { stub.requests.count > before })
        #expect(!model.loading)
        await task.value
        #expect(!model.loading)
        #expect(model.summary != nil)
        #expect(model.error == nil)
    }
}

/// The dashboard minor: finishing a re-run of onboarding reloads the
/// brokerages, so accounts linked during it show on the dashboard.
@MainActor
@Suite struct W3OnboardingHandBackTests {
    @Test func completingOnboardingReloadsTheBrokerages() async {
        let stub = DataStub(json: #"{"accounts": [{"id": "a1"}]}"#)
        let services = AppServices(
            storage: InMemorySecureStorage([
                ApiBaseUrlStore.storageKey: stub.baseURL,
                SessionStore.tokenKey: "jwt",
                SessionStore.userKey: #"{"has_completed_onboarding": true}"#,
            ]),
            biometrics: FakeBiometrics(available: false, authResult: false),
            widgetSync: WidgetSyncProbe().sync,
            urlSession: DataStubProtocol.session,
            pushRegistrar: FakePushRegistrar(grant: false)
        )
        await services.dashboard.loadBrokerages()
        #expect(services.dashboard.brokerages.value?.count == 1)

        stub.respond(json: #"{"accounts": [{"id": "a1"}, {"id": "a2"}]}"#)
        await services.didCompleteOnboarding()
        #expect(services.dashboard.brokerages.value?.map(\.id) == ["a1", "a2"])
    }
}

/// Wave 1 deferred: the unreadable-keychain screen has a Sign Out escape.
@MainActor
@Suite struct W3StorageEscapeTests {
    private func services(_ storage: InMemorySecureStorage) -> AppServices {
        AppServices(
            storage: storage,
            biometrics: FakeBiometrics(available: false, authResult: false),
            widgetSync: WidgetSyncProbe().sync,
            urlSession: DataStubProtocol.session,
            pushRegistrar: FakePushRegistrar(grant: false)
        )
    }

    private func screen(_ services: AppServices) -> RootScreen {
        RootScreen.resolve(
            storageReady: services.isStorageReady,
            isConfigured: services.urlStore.isConfigured,
            isAuthenticated: services.session.isAuthenticated,
            hasCompletedOnboarding: services.session.hasCompletedOnboarding,
            locked: services.lock.locked,
            storageUnavailable: services.isStorageUnavailable
        )
    }

    @Test func signingOutOfAnUnreadableKeychainClearsTheSessionAndLeavesTheErrorScreen() {
        let storage = InMemorySecureStorage([
            ApiBaseUrlStore.storageKey: "https://saved.example.test",
            SessionStore.tokenKey: "jwt",
            SessionStore.userKey: #"{"has_completed_onboarding": true}"#,
        ])
        storage.readError = KeychainError.interactionNotAllowed
        let services = services(storage)
        services.scenePhaseChanged(.active)
        #expect(screen(services) == .storageUnavailable)

        services.signOutOfUnavailableStorage()
        #expect(storage.snapshot[SessionStore.tokenKey] == nil)
        #expect(storage.snapshot[SessionStore.userKey] == nil)
        #expect(!services.session.isAuthenticated)
        #expect(screen(services) == .app(.connect))
    }

    @Test func whenTheKeychainReadsAgainSignOutKeepsTheServer() {
        let storage = InMemorySecureStorage([
            ApiBaseUrlStore.storageKey: "https://saved.example.test",
            SessionStore.tokenKey: "jwt",
            SessionStore.userKey: #"{"has_completed_onboarding": true}"#,
        ])
        storage.readError = KeychainError.interactionNotAllowed
        let services = services(storage)
        services.scenePhaseChanged(.active)
        storage.readError = nil

        services.signOutOfUnavailableStorage()
        #expect(services.urlStore.baseUrl == "https://saved.example.test")
        #expect(!services.session.isAuthenticated)
        #expect(screen(services) == .app(.login))
    }
}
