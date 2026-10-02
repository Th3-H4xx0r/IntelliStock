import Foundation
import SwiftUI
import Testing
@testable import IntelliStock

// Tests for the Wave 1 fix round (reviewer findings C1, I1–I6, M1–M11).
// C1/I1/I3/I6 at the services level live in SessionAndServicesTests; I5 at
// the client level in ApiClientTests.

/// I2: a cancelled request is never an error, and never touches shared state.
@MainActor
struct CancellationTests {
    private let stub = DataStub()

    private static let engines = #"{"engines": [{"id": "price_engine", "status": "active"}]}"#
    private static let accounts = #"{"accounts": [{"id": "acct-1", "account_name": "Main", "brokerage_type": "alpaca", "status": "active"}]}"#

    @Test func captureTurnsCancellationIntoLoadingNotAFailure() async {
        let a = await Loadable<Int>.capture { throw CancellationError() }
        #expect(a.isLoading)
        #expect(a.error == nil)
        let b = await Loadable<Int>.capture { throw URLError(.cancelled) }
        #expect(b.isLoading)
        let c = await Loadable<Int>.capture { throw ApiError(message: "boom") }
        #expect(c.errorMessage == "boom")
    }

    @Test func refreshingLeavesTheStateUnchangedOnCancellation() async {
        let current = Loadable<Int>.loaded(3)
        #expect(await current.refreshing { throw CancellationError() }.value == 3)
        #expect(await current.refreshing { 4 }.value == 4)
        #expect(await current.refreshing { throw ApiError(message: "x") }.errorMessage == "x")
        #expect(await Loadable<Int>.capture(keeping: .loaded(7)) { throw URLError(.cancelled) }.value == 7)
    }

    @Test func cancellationIsRecognised() {
        #expect(CancellationError().isCancellation)
        #expect(URLError(.cancelled).isCancellation)
        #expect(!URLError(.timedOut).isCancellation)
        #expect(!ApiError(message: "x").isCancellation)
    }

    @Test func fetchServicesThrowsOnCancellation() async {
        stub.respond(json: Self.engines)
        let repo = DashboardRepository(client: stub.client)
        let task = Task { try await repo.fetchServices() }
        task.cancel()
        guard case .failure(let error) = await task.result else {
            Issue.record("expected a cancelled fetch to throw")
            return
        }
        #expect(error is CancellationError)
    }

    @Test func aCancelledServicesRefreshKeepsTheSnapshot() async {
        stub.respond(json: Self.engines)
        let client = stub.client
        let model = DashboardModel(repository: { DashboardRepository(client: client) })
        await model.refreshNow()
        #expect(model.services.value?.statusFor("price_engine") == "active")

        let task = Task { await model.refreshNow() }
        task.cancel()
        await task.value
        #expect(model.services.value?.statusFor("price_engine") == "active")
    }

    @Test func aCancelledBrokerageLoadKeepsTheList() async {
        stub.respond(json: Self.accounts)
        let client = stub.client
        let model = DashboardModel(repository: { DashboardRepository(client: client) })
        await model.loadBrokerages()
        #expect(model.brokerages.value?.count == 1)

        let task = Task { await model.loadBrokerages() }
        task.cancel()
        await task.value
        #expect(model.brokerages.value?.count == 1)
        #expect(model.brokerages.error == nil)
    }

    @Test func pushDevicesKeepTheListOnACancelledReload() async {
        stub.respond(json: #"{"devices": [{"device_token": "x"}]}"#)
        let client = stub.client
        let model = PushDevicesModel(repository: { PushRepository(client: client) })
        await model.load()
        let task = Task { await model.load() }
        task.cancel()
        await task.value
        #expect(model.devices.value?.map(\.deviceToken) == ["x"])
    }
}

/// I3: the shared dashboard polls through `PollingLoop`, pausing in the
/// background, and resets cleanly.
@MainActor
struct DashboardModelLifecycleTests {
    private let stub = DataStub(json: "{}")

    @Test func pollServicesPausesInTheBackgroundAndResumes() async {
        let client = stub.client
        let model = DashboardModel(repository: { DashboardRepository(client: client) })
        let clock = ManualClock()
        let lifecycle = AppLifecycle()
        let task = Task { await model.pollServices(lifecycle: lifecycle, sleep: clock.sleep) }

        // First fetch: the four service endpoints, then the loop waits 10 s.
        #expect(await eventually { stub.requests.count == 4 && clock.pendingCount == 1 })
        #expect(clock.requested.last == DashboardModel.servicesInterval)
        await clock.advance(by: .seconds(10))
        #expect(await eventually { stub.requests.count == 8 && clock.pendingCount == 1 })

        lifecycle.handle(.background)
        #expect(await eventually { clock.pendingCount == 0 })
        await clock.advance(by: .seconds(60))
        try? await Task.sleep(for: .milliseconds(100))
        #expect(stub.requests.count == 8)

        lifecycle.handle(.active)
        #expect(await eventually { clock.pendingCount == 1 })
        await clock.advance(by: .seconds(10))
        #expect(await eventually { stub.requests.count == 12 })

        task.cancel()
    }

    @Test func resetReturnsToTheFreshState() async {
        stub.respond(json: #"{"accounts": [{"id": "a"}]}"#)
        let client = stub.client
        let model = DashboardModel(repository: { DashboardRepository(client: client) })
        await model.loadBrokerages()
        await model.refreshNow()
        model.portfolioUpdatedAt = Date()
        model.reset()
        #expect(model.brokerages.isLoading)
        #expect(model.brokeragesValue == nil)
        #expect(model.services.isLoading)
        #expect(model.busy.isEmpty)
        #expect(model.portfolioUpdatedAt == nil)
    }
}

/// I4: a confirmed action runs at most once at a time; failures surface.
@MainActor
struct ConfirmRunnerTests {
    @Test func runsOnceAtATimeAndReportsRunning() async {
        let runner = ConfirmRunner()
        var calls = 0
        var gate: CheckedContinuation<Void, Never>?
        runner.run({
            calls += 1
            await withCheckedContinuation { gate = $0 }
        }, onError: nil, isRunning: .constant(false))
        #expect(runner.isRunning)
        #expect(await eventually { gate != nil })

        // A second confirm while the first is in flight is ignored.
        runner.run({ calls += 1 }, onError: nil, isRunning: .constant(false))
        gate?.resume()
        #expect(await eventually { !runner.isRunning })
        #expect(calls == 1)

        runner.run({ calls += 1 }, onError: nil, isRunning: .constant(false))
        #expect(await eventually { !runner.isRunning && calls == 2 })
    }

    @Test func anUnhandledFailureBecomesAnErrorToast() async {
        let runner = ConfirmRunner()
        runner.run({ throw ApiError(message: "Rerun failed") }, onError: nil, isRunning: .constant(false))
        #expect(await eventually { runner.failure != nil })
        #expect(runner.failure?.message == "Rerun failed")
        #expect(runner.failure?.style == .error)
        #expect(!runner.isRunning)
    }

    @Test func onErrorTakesTheFailureInstead() async {
        let runner = ConfirmRunner()
        var handled: String?
        runner.run({ throw ApiError(message: "nope") }, onError: { handled = ($0 as? ApiError)?.message }, isRunning: .constant(false))
        #expect(await eventually { handled == "nope" })
        #expect(runner.failure == nil)
    }

    @Test func cancellationIsNotAFailure() async {
        let runner = ConfirmRunner()
        runner.run({ throw CancellationError() }, onError: nil, isRunning: .constant(false))
        #expect(await eventually { !runner.isRunning })
        #expect(runner.failure == nil)
    }
}

/// I5 (minimum), M5, I6 at the store level.
@MainActor
struct SessionStoreFixTests {
    private func make(_ initial: [String: String] = [:]) -> (SessionStore, InMemorySecureStorage, WidgetSyncProbe) {
        let storage = InMemorySecureStorage(initial)
        let probe = WidgetSyncProbe()
        return (SessionStore(storage: storage, widgetSync: probe.sync, apiBaseUrl: { "https://api.example.test" }), storage, probe)
    }

    @Test func setTokenIsANoOpWhenSignedOut() async {
        let (session, storage, probe) = make()
        session.load()
        await session.setToken("late.jwt")
        #expect(session.token == nil)
        #expect(storage.read(SessionStore.tokenKey) == nil)
        #expect(probe.defaults.string(forKey: WidgetSync.Key.token) == nil)
    }

    @Test func setSessionWritesTheKeychainBeforeMemory() async {
        struct Locked: Error {}
        let (session, storage, _) = make()
        storage.writeError = Locked()
        await #expect(throws: Locked.self) {
            try await session.setSession(token: "t", user: ["username": "a"])
        }
        #expect(!session.isAuthenticated)
        #expect(session.user == nil)
    }

    @Test func aLoadWithNoSessionLeavesTheWidgetCredentialsAlone() {
        let (session, _, probe) = make()
        probe.defaults.set("kept.jwt", forKey: WidgetSync.Key.token)
        #expect(session.load())
        #expect(probe.defaults.string(forKey: WidgetSync.Key.token) == "kept.jwt")
        #expect(probe.reloads.isEmpty)
    }

    @Test func loadReportsAnUnreadableKeychainAndChangesNothing() {
        let (session, storage, probe) = make([SessionStore.tokenKey: "jwt"])
        storage.readError = KeychainError.interactionNotAllowed
        #expect(!session.load())
        #expect(!session.isAuthenticated)
        #expect(probe.reloads.isEmpty)
        storage.readError = nil
        #expect(session.load())
        #expect(session.token == "jwt")
    }

    @Test func urlStoreLoadReportsAnUnreadableKeychain() {
        let storage = InMemorySecureStorage([ApiBaseUrlStore.storageKey: "https://a.example"])
        storage.readError = KeychainError.interactionNotAllowed
        let store = ApiBaseUrlStore(storage: storage)
        #expect(!store.load())
        #expect(!store.isConfigured)
        storage.readError = nil
        #expect(store.load())
        #expect(store.baseUrl == "https://a.example")
    }

    @Test func keychainReadCheckedSeparatesNotFoundFromFailure() throws {
        let keychain = KeychainStore(service: "test.fix1.\(UUID().uuidString)")
        defer { keychain.delete("k") }
        #expect(try keychain.readChecked("k") == nil)
        try keychain.write("k", "v")
        #expect(try keychain.readChecked("k") == "v")
        #expect(keychain.read("k") == "v")
    }
}

/// M1, M2, M3, M4, M10, M11.
struct MinorFixTests {
    @Test func numFormattersKeepDartsIntDoubleDistinction() {
        let ints: Num? = .int(950)
        let doubles: Num? = .double(950)
        #expect(fmtTokens(ints) == "950")
        #expect(fmtTokens(doubles) == "950.0")
        #expect(fmtTokens(Num?.none) == "—")
        #expect(fmtTokens(Num.int(1_200_000)) == "1.2M")
        #expect(fmtMoney(Num.int(5)) == "$5.00")
        #expect(fmtPct(Num.double(12.345)) == "+12.35%")
        #expect(fmtElapsed(Num.int(95)) == "1m 35s")
        #expect(fmtDuration(Num.double(1.5)) == "1.5s")
        #expect(fmtUsdCost(Num.double(0.0004)) == "$0.0004")
        #expect(pnlColor(Num.int(-1)) == DS.Palette.danger)
        #expect(parseDateTime(Num.int(1_700_000_000)) == parseDateTime(1_700_000_000))
        // Unchanged: nil and literals still resolve to the Int form.
        #expect(fmtTokens(nil) == "—")
        #expect(fmtTokens(950) == "950")
    }

    @Test func oneToStringAsFixedAndOneEncodeComponent() {
        #expect(dartToStringAsFixed(1e21, 2) == DartNumberFormat.toStringAsFixed(1e21, 2))
        #expect(dartToStringAsFixed(1e21, 2) == "1e+21")
        #expect(dartToStringAsFixed(0.125, 2) == "0.13")
        #expect(dartToStringAsFixed(-0.001, 2) == "-0.00")
        #expect(Route.encodeComponent("a b/c?") == dartEncodeComponent("a b/c?"))
        #expect(dartEncodeComponent("a b/c?") == "a%20b%2Fc%3F")
    }

    @Test func doubleToIntSaturatesInsteadOfTrapping() {
        #expect(JSON.double(1e20).int == Int.max)
        #expect(JSON.double(-1e20).int == Int.min)
        #expect(JSON.double(.infinity).int == nil)
        #expect(JSON.double(.nan).int == nil)
        #expect(JSON.double(5.9).int == 5)
        #expect(JSON.double(-5.9).int == -5)
        #expect(Num.double(1e19).int == Int.max)
        #expect(Int(dartTruncating: 9.2e18) == 9_200_000_000_000_000_000)
        #expect(fmtElapsed(1e30) == fmtElapsed(Int.max))
        #expect(!fmtDuration(1e30).isEmpty)
    }

    @Test func portfolioHistoryDatesOutOfDartRangeAreDropped() {
        #expect(PortfolioHistory.toDate(.double(1e20)) == nil)
        #expect(PortfolioHistory.toDate(.int(9_000_000_000_000_000)) == nil)
        #expect(PortfolioHistory.toDate(.int(-9_000_000_000_000_000)) == nil)
        #expect(PortfolioHistory.toDate(.int(1_700_000_000))?.timeIntervalSince1970 == 1_700_000_000)
        #expect(PortfolioHistory.toDate(.int(1_700_000_000_000))?.timeIntervalSince1970 == 1_700_000_000)
    }

    @Test func routesIgnoreQueryAndFragment() {
        #expect(Route(path: "/instances/abc?tab=logs") == .instance("abc"))
        #expect(Route(path: "/settings#top") == .settings)
        #expect(Route(path: "/stock/AAPL?range=1D#x") == .stock(StockRoute(symbol: "AAPL")))
        #expect(AppTab(rootPath: "/kalshi?x=1") == .kalshi)
        #expect(AppGate.safeRedirect("/instances/abc?tab=logs") == "/instances/abc?tab=logs")
    }

    @Test func jsonNumbersCompareByValueLikeDart() {
        #expect(JSON.int(5) == JSON.double(5))
        #expect(JSON.double(5) == JSON.int(5))
        #expect(JSON.int(5).hashValue == JSON.double(5).hashValue)
        #expect(Set([JSON.int(1), JSON.double(1)]).count == 1)
        #expect(JSON.int(5) != JSON.double(5.5))
        #expect(JSON.int(9_007_199_254_740_993) != JSON.double(9_007_199_254_740_992))
        #expect(JSON.string("5") != JSON.int(5))
        #expect(JSON.bool(true) != JSON.int(1))
        #expect((["a": 5, "b": [1, 2]] as JSON) == (["a": 5.0, "b": [1.0, 2]] as JSON))
        // toString still tells them apart.
        #expect(JSON.int(5).dartDescription == "5")
        #expect(JSON.double(5).dartDescription == "5.0")
    }

    @Test func chartMarkersHaveStableIdentity() {
        let at = Date(timeIntervalSince1970: 1_700_000_000)
        let a = ScrubbableChartMarker(date: at, value: 10, color: .green)
        let b = ScrubbableChartMarker(date: at, value: 10, color: .green)
        let c = ScrubbableChartMarker(date: at, value: 11, color: .green)
        #expect(a == b)
        #expect(a.id == b.id)
        #expect(a != c)
    }
}
