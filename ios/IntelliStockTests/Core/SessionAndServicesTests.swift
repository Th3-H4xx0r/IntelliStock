import Foundation
import SwiftUI
import Testing
import UIKit
@testable import IntelliStock

/// Ported from test/core/network/api_base_url_test.dart.
struct ApiBaseUrlTests {
    @Test func trimsWhitespaceAndReducesToTheOrigin() {
        #expect(normalizeBaseUrl("  https://api.example.com/  ") == "https://api.example.com")
        #expect(normalizeBaseUrl("https://api.example.com") == "https://api.example.com")
        #expect(normalizeBaseUrl("http://1.2.3.4:8000/") == "http://1.2.3.4:8000")
    }

    @Test func stripsTrailingSlashesAndAnyPathQueryFragment() {
        #expect(normalizeBaseUrl("https://host//") == "https://host")
        #expect(normalizeBaseUrl("https://api.example.com/v1/") == "https://api.example.com")
        #expect(normalizeBaseUrl("https://host:8000/api?x=1#f") == "https://host:8000")
    }

    @Test func emptyOrWhitespaceIsEmpty() {
        #expect(normalizeBaseUrl("") == "")
        #expect(normalizeBaseUrl("   ") == "")
    }

    @Test func nonHttpOrHostlessInputsComeBackForIsValidToReject() {
        #expect(normalizeBaseUrl("notaurl") == "notaurl")
        #expect(normalizeBaseUrl("ftp://example.com") == "ftp://example.com")
    }

    /// Dart's `Uri` lower-cases the scheme and host and drops default ports.
    @Test func normalizesLikeDartUri() {
        #expect(normalizeBaseUrl("HTTPS://API.Example.COM/x") == "https://api.example.com")
        #expect(normalizeBaseUrl("https://host:443/") == "https://host")
        #expect(normalizeBaseUrl("http://host:80") == "http://host")
        #expect(normalizeBaseUrl("https://user:pw@host:9000/") == "https://host:9000")
    }

    @Test func acceptsHttpOrHttpsWithAHost() {
        #expect(isValidBaseUrl("https://api.example.com"))
        #expect(isValidBaseUrl("http://1.2.3.4:8000"))
        #expect(isValidBaseUrl("  https://api.example.com/  "))
        #expect(isValidBaseUrl("https://host/api"))
    }

    @Test func rejectsEmptySchemelessNonHttpHostless() {
        #expect(!isValidBaseUrl(""))
        #expect(!isValidBaseUrl("notaurl"))
        #expect(!isValidBaseUrl("ftp://example.com"))
        #expect(!isValidBaseUrl("https://"))
        #expect(!isValidBaseUrl("api.example.com"))
    }

    @Test @MainActor func storeLoadsNormalizedAndPersists() throws {
        let storage = InMemorySecureStorage([ApiBaseUrlStore.storageKey: "https://Host.example/path/"])
        let store = ApiBaseUrlStore(storage: storage)
        var changes: [String] = []
        store.onChange = { changes.append($0) }
        store.load()
        #expect(store.baseUrl == "https://host.example")
        #expect(store.isConfigured)

        try store.set(" http://other:8000/ ")
        #expect(store.baseUrl == "http://other:8000")
        #expect(storage.read(ApiBaseUrlStore.storageKey) == "http://other:8000")

        try store.set("")
        #expect(!store.isConfigured)
        #expect(storage.read(ApiBaseUrlStore.storageKey) == nil)
        #expect(changes == ["https://host.example", "http://other:8000", ""])
    }

    @Test @MainActor func aFailedPersistLeavesTheOldUrl() {
        struct Locked: Error {}
        let storage = InMemorySecureStorage([ApiBaseUrlStore.storageKey: "https://a.example"])
        let store = ApiBaseUrlStore(storage: storage)
        store.load()
        storage.writeError = Locked()
        #expect(throws: Locked.self) { try store.set("https://b.example") }
        #expect(store.baseUrl == "https://a.example")
    }
}

/// `SessionStore` — session.dart's persistence and widget mirroring.
@MainActor
struct SessionStoreTests {
    private func make(_ initial: [String: String] = [:], url: String = "https://api.example.test")
        -> (SessionStore, InMemorySecureStorage, WidgetSyncProbe)
    {
        let storage = InMemorySecureStorage(initial)
        let probe = WidgetSyncProbe()
        let session = SessionStore(storage: storage, widgetSync: probe.sync, apiBaseUrl: { url })
        return (session, storage, probe)
    }

    @Test func loadReadsTokenAndUserAndMirrorsTheWidget() {
        let (session, _, probe) = make([
            SessionStore.tokenKey: "jwt",
            SessionStore.userKey: #"{"username": "pk", "has_completed_onboarding": true}"#,
        ])
        session.load()
        #expect(session.token == "jwt")
        #expect(session.isAuthenticated)
        #expect(session.username == "pk")
        #expect(session.hasCompletedOnboarding)
        #expect(probe.defaults.string(forKey: WidgetSync.Key.token) == "jwt")
        #expect(probe.defaults.string(forKey: WidgetSync.Key.apiBase) == "https://api.example.test")
        #expect(probe.reloads == [WidgetSync.Kind.portfolio])
    }

    @Test func undecodableOrNonObjectUserIsDropped() {
        let (s1, _, _) = make([SessionStore.userKey: "{not json"])
        s1.load()
        #expect(s1.user == nil)
        let (s2, _, _) = make([SessionStore.userKey: #""just a string""#])
        s2.load()
        #expect(s2.user == nil)
        #expect(s2.username == "User")
    }

    @Test func usernameFallsBackToNameThenUser() {
        let (session, _, _) = make([SessionStore.userKey: #"{"name": "Pranav"}"#])
        session.load()
        #expect(session.username == "Pranav")
        #expect(!session.hasCompletedOnboarding)
    }

    @Test func emptyTokenIsSignedOut() {
        let (session, _, _) = make([SessionStore.tokenKey: ""])
        session.load()
        #expect(!session.isAuthenticated)
    }

    @Test func setSessionPersistsAndMirrors() async throws {
        let (session, storage, probe) = make()
        try await session.setSession(token: "t1", user: ["username": "a"])
        #expect(storage.read(SessionStore.tokenKey) == "t1")
        #expect(storage.read(SessionStore.userKey).flatMap { try? JSON(data: Data($0.utf8)) } == ["username": "a"])
        #expect(probe.defaults.string(forKey: WidgetSync.Key.token) == "t1")

        try await session.setSession(token: "t2", user: nil)
        #expect(storage.read(SessionStore.userKey) == nil)
        #expect(session.user == nil)
    }

    @Test func setTokenIsANoOpWhenEmptyOrUnchanged() async throws {
        let (session, _, probe) = make()
        try await session.setSession(token: "t1", user: nil)
        let reloads = probe.reloads.count
        await session.setToken("")
        await session.setToken("t1")
        #expect(session.token == "t1")
        #expect(probe.reloads.count == reloads)
        await session.setToken("t2")
        #expect(session.token == "t2")
        #expect(probe.defaults.string(forKey: WidgetSync.Key.token) == "t2")
    }

    @Test func setTokenPersistenceIsBestEffort() async throws {
        struct Locked: Error {}
        let (session, storage, _) = make()
        try await session.setSession(token: "t1", user: ["username": "a"])
        storage.writeError = Locked()
        await session.setToken("t2") // must not throw
        #expect(session.token == "t2")
        #expect(storage.read(SessionStore.tokenKey) == "t1")
        #expect(session.username == "a")
    }

    @Test func setUserPersistsWithoutTouchingTheToken() async throws {
        let (session, storage, probe) = make()
        try await session.setSession(token: "t1", user: nil)
        let reloads = probe.reloads.count
        try await session.setUser(["username": "b", "has_completed_onboarding": false])
        #expect(session.username == "b")
        #expect(storage.read(SessionStore.userKey) != nil)
        #expect(probe.reloads.count == reloads)
    }

    @Test func clearForgetsBothKeysAndTheWidgetToken() async throws {
        let (session, storage, probe) = make()
        try await session.setSession(token: "t1", user: ["username": "a"])
        await session.clear()
        #expect(!session.isAuthenticated)
        #expect(session.user == nil)
        #expect(storage.read(SessionStore.tokenKey) == nil)
        #expect(storage.read(SessionStore.userKey) == nil)
        #expect(probe.defaults.string(forKey: WidgetSync.Key.token) == "")
    }
}

/// `AppServices`: first-frame reads, client rebuild on a server change
/// (Review Focus 5), 401 handling (Review Focus 3), the router redirect, and
/// fix round 1 (lock release, session-scoped models, deferred keychain).
@MainActor
struct AppServicesTests {
    private let stub = DataStub()

    private func make(
        _ initial: [String: String],
        probe: WidgetSyncProbe = WidgetSyncProbe(),
        storage: InMemorySecureStorage? = nil,
        registrar: FakePushRegistrar = FakePushRegistrar(grant: false),
        notificationCenter: NotificationCenter = NotificationCenter()
    ) -> AppServices {
        AppServices(
            storage: storage ?? InMemorySecureStorage(initial),
            biometrics: FakeBiometrics(available: true, authResult: true),
            widgetSync: probe.sync,
            urlSession: DataStubProtocol.session,
            pushRegistrar: registrar,
            notificationCenter: notificationCenter
        )
    }

    private var signedIn: [String: String] {
        [
            ApiBaseUrlStore.storageKey: stub.baseURL,
            SessionStore.tokenKey: "jwt",
            SessionStore.userKey: #"{"username": "pk", "has_completed_onboarding": true}"#,
        ]
    }

    @Test func readsUrlSessionAndLockSynchronously() {
        var initial = signedIn
        initial[AppLock.enabledKey] = "true"
        initial[AppLock.timeoutKey] = "60"
        let services = make(initial)
        #expect(services.isStorageReady)
        #expect(services.urlStore.baseUrl == stub.baseURL)
        #expect(services.session.isAuthenticated)
        #expect(services.lock.locked)
        #expect(services.lock.timeout == .oneMinute)
        #expect(services.apiClient.baseURL == stub.baseURL)
    }

    @Test func theWidgetMirrorsTheUrlLoadedFirst() {
        let probe = WidgetSyncProbe()
        _ = make(signedIn, probe: probe)
        #expect(probe.defaults.string(forKey: WidgetSync.Key.apiBase) == stub.baseURL)
    }

    @Test func changingTheServerRebuildsTheClientAgainstTheNewOrigin() async throws {
        let newServer = DataStub(json: #"{"ok": true}"#)
        let services = make(signedIn)
        let old = services.apiClient
        try services.urlStore.set(newServer.baseURL + "/api/")
        #expect(services.apiClient !== old)
        #expect(services.apiClient.baseURL == newServer.baseURL)

        _ = try await services.apiClient.get("/health")
        #expect(newServer.requests.count == 1)
        #expect(stub.requests.isEmpty)
        #expect(newServer.requests.first?.value(forHTTPHeaderField: "Authorization") == "Bearer jwt")
    }

    @Test func anUnchangedUrlKeepsTheClient() throws {
        let services = make(signedIn)
        let old = services.apiClient
        try services.urlStore.set(stub.baseURL + "/")
        #expect(services.apiClient === old)
    }

    @Test func a401ClearsTheSession() async {
        stub.respond(status: 401, json: #"{"detail": "expired"}"#)
        let services = make(signedIn)
        _ = try? await services.apiClient.get("/instances")
        #expect(!services.session.isAuthenticated)
    }

    @Test func aRefreshedTokenPersists() async throws {
        stub.respond(json: "{}", headers: ["X-Refreshed-Token": "slid.jwt"])
        let storage = InMemorySecureStorage(signedIn)
        let services = make([:], storage: storage)
        _ = try await services.apiClient.get("/instances")
        #expect(await eventually { services.session.token == "slid.jwt" })
        #expect(storage.read(SessionStore.tokenKey) == "slid.jwt")
    }

    /// I5 end to end: a renewal that lands after Sign Out writes nothing back.
    @Test func aRefreshAfterSignOutDoesNotResurrectTheSession() async throws {
        let storage = InMemorySecureStorage(signedIn)
        let probe = WidgetSyncProbe()
        let services = make([:], probe: probe, storage: storage)
        stub.headers = ["X-Refreshed-Token": "slid.jwt"]
        stub.handler = { _ in
            DispatchQueue.main.sync {
                MainActor.assumeIsolated {
                    let session = services.session
                    Task { await session.clear() }
                }
            }
            return (200, "{}")
        }
        _ = try await services.apiClient.get("/instances")
        #expect(await eventually { !services.session.isAuthenticated })
        try await Task.sleep(for: .milliseconds(50))
        #expect(services.session.token == nil)
        #expect(storage.read(SessionStore.tokenKey) == nil)
        #expect(probe.defaults.string(forKey: WidgetSync.Key.token) == "")
    }

    @Test func scenePhasesReachTheLockAndTheLifecycle() {
        var initial = signedIn
        initial[AppLock.enabledKey] = "true"
        let services = make(initial)
        services.lock.releaseLock()
        services.scenePhaseChanged(.background)
        #expect(!services.lifecycle.isForeground)
        services.scenePhaseChanged(.active)
        #expect(services.lifecycle.isForeground)
        #expect(services.lock.locked)
    }

    // MARK: Lock and push (fix round 1, I1)

    /// A 401 or Sign Out while locked must not leave Login behind Face ID,
    /// nor demand Face ID again after the password sign-in.
    @Test func signOutReleasesTheLockAndKeepsThePreference() async {
        var initial = signedIn
        initial[AppLock.enabledKey] = "true"
        let services = make(initial)
        #expect(services.lock.locked)
        await services.session.clear()
        services.didSignOut()
        #expect(!services.lock.locked)
        #expect(services.lock.enabled)
    }

    @Test func pushDoesNotRegisterWhileLocked() async {
        var initial = signedIn
        initial[AppLock.enabledKey] = "true"
        let registrar = FakePushRegistrar(grant: true)
        let services = make(initial, registrar: registrar)
        #expect(services.lock.locked)
        await services.push.enable()
        #expect(registrar.authorizationRequests == 0)

        services.lock.releaseLock()
        await services.push.enable()
        #expect(registrar.authorizationRequests == 1)
        #expect(registrar.registrations == 1)
    }

    @Test func pushDoesNotRegisterWhenSignedOut() async {
        let registrar = FakePushRegistrar(grant: true)
        let services = make([ApiBaseUrlStore.storageKey: stub.baseURL], registrar: registrar)
        await services.push.enable()
        #expect(registrar.authorizationRequests == 0)
    }

    // MARK: Session-scoped shared models (fix round 1, I3)

    private nonisolated static let brokerageA = #"{"accounts": [{"id": "acct-A", "name": "Server A", "brokerage_type": "alpaca", "status": "active"}]}"#

    @Test func signOutResetsTheSharedDashboard() async {
        stub.respond(json: Self.brokerageA)
        let services = make(signedIn)
        await services.dashboard.loadBrokerages()
        #expect(services.dashboard.brokeragesValue?.first?.id == "acct-A")
        services.dashboard.portfolioUpdatedAt = Date()

        services.didSignOut()
        #expect(services.dashboard.brokeragesValue == nil)
        #expect(services.dashboard.brokerages.isLoading)
        #expect(services.dashboard.services.isLoading)
        #expect(services.dashboard.portfolioUpdatedAt == nil)
    }

    /// Server A's accounts must never show on server B, nor be sent to it.
    @Test func aServerChangeResetsTheSharedDashboard() async throws {
        stub.respond(json: Self.brokerageA)
        let services = make(signedIn)
        await services.dashboard.loadBrokerages()
        #expect(services.dashboard.brokeragesValue != nil)

        let serverB = DataStub(status: 500, json: #"{"detail": "down"}"#)
        try services.urlStore.set(serverB.baseURL)
        #expect(services.dashboard.brokeragesValue == nil)
        await services.dashboard.loadBrokerages()
        #expect(services.dashboard.brokeragesValue == nil)
        #expect(services.dashboard.brokerages.error != nil)
    }

    /// A response that lands after the reset is dropped.
    @Test func aLateBrokerageResponseAfterResetIsDropped() async {
        let services = make(signedIn)
        stub.handler = { _ in
            DispatchQueue.main.sync { MainActor.assumeIsolated { services.dashboard.reset() } }
            return (200, Self.brokerageA)
        }
        await services.dashboard.loadBrokerages()
        #expect(services.dashboard.brokeragesValue == nil)
        #expect(services.dashboard.brokerages.isLoading)
    }

    // MARK: Keychain before first unlock (fix round 1, I6)

    @Test func anUnreadableKeychainDefersLoadingAndKeepsTheWidgetCredentials() {
        var initial = signedIn
        initial[AppLock.enabledKey] = "true"
        let storage = InMemorySecureStorage(initial)
        storage.readError = KeychainError.interactionNotAllowed
        let probe = WidgetSyncProbe()
        probe.defaults.set("https://kept.example.test", forKey: WidgetSync.Key.apiBase)
        probe.defaults.set("kept.jwt", forKey: WidgetSync.Key.token)
        let center = NotificationCenter()

        let services = make([:], probe: probe, storage: storage, notificationCenter: center)
        #expect(!services.isStorageReady)
        #expect(!services.urlStore.isConfigured)
        #expect(!services.session.isAuthenticated)
        #expect(probe.defaults.string(forKey: WidgetSync.Key.token) == "kept.jwt")
        #expect(probe.defaults.string(forKey: WidgetSync.Key.apiBase) == "https://kept.example.test")
        #expect(RootScreen.resolve(
            storageReady: services.isStorageReady,
            isConfigured: services.urlStore.isConfigured,
            isAuthenticated: services.session.isAuthenticated,
            hasCompletedOnboarding: services.session.hasCompletedOnboarding,
            locked: services.lock.locked
        ) == .waiting)

        // Still locked: a notification changes nothing.
        center.post(name: UIApplication.protectedDataDidBecomeAvailableNotification, object: nil)
        #expect(!services.isStorageReady)

        storage.readError = nil
        center.post(name: UIApplication.protectedDataDidBecomeAvailableNotification, object: nil)
        #expect(services.isStorageReady)
        #expect(services.urlStore.baseUrl == stub.baseURL)
        #expect(services.apiClient.baseURL == stub.baseURL)
        #expect(services.session.isAuthenticated)
        #expect(services.lock.locked)
        #expect(probe.defaults.string(forKey: WidgetSync.Key.token) == "jwt")
    }

    // MARK: Redirect

    @Test func signOutRemembersTheLocationAndResetsTheShell() {
        let services = make(signedIn)
        services.router.tab = .instances
        services.router.push(.instance("abc"))
        services.didSignOut()
        #expect(services.loginRedirect == "/instances/abc")
        #expect(services.router.tab == .dashboard)
        #expect(services.router.stack(for: .instances).isEmpty)
    }

    @Test func signInHonoursASafeRedirectInItsTab() {
        let services = make(signedIn)
        services.router.tab = .more
        services.router.push(.settings)
        services.didSignOut()
        services.didSignIn()
        #expect(services.router.tab == .more)
        #expect(services.router.stack(for: .more) == [.settings])
        #expect(services.loginRedirect == nil)
    }

    @Test func signInToATabRootSelectsIt() {
        let services = make(signedIn)
        services.router.tab = .strategies
        services.didSignOut()
        #expect(services.loginRedirect == "/strategies")
        services.didSignIn()
        #expect(services.router.tab == .strategies)
    }

    @Test func signInWithOnboardingIncompleteDropsTheRedirect() async throws {
        let services = make(signedIn)
        services.loginRedirect = "/instances/abc"
        try await services.session.setUser(["username": "pk", "has_completed_onboarding": false])
        services.didSignIn()
        #expect(services.router.stack(for: .dashboard).isEmpty)
        #expect(services.loginRedirect == nil)
    }

    @Test func unsafeRedirectsAreIgnored() {
        for unsafe in ["//evil.example", "https://evil.example", "/a@b", "relative"] {
            let services = make(signedIn)
            services.loginRedirect = unsafe
            services.didSignIn()
            #expect(services.router.stack(for: .dashboard).isEmpty)
            #expect(services.router.tab == .dashboard)
        }
    }

    @Test func deepLinksOpenWhenSignedInAndWaitWhenSignedOut() {
        let services = make(signedIn)
        services.openDeepLink(URL(string: "intellistock://instances/abc/live")!)
        #expect(services.router.stack(for: .dashboard) == [.liveTrading("abc")])

        let signedOut = make([ApiBaseUrlStore.storageKey: stub.baseURL])
        signedOut.openDeepLink(URL(string: "intellistock://kalshi")!)
        #expect(signedOut.loginRedirect == "/kalshi")
        signedOut.openDeepLink(URL(string: "https://example.com/instances/x")!)
        #expect(signedOut.loginRedirect == "/kalshi")
    }
}

/// The root decision (fix round 1, C1): while locked and signed in, the lock
/// replaces the app — none of its content (tabs, sheets, alerts, chat) is on
/// screen.
struct RootScreenTests {
    private func screen(ready: Bool = true, configured: Bool = true, authed: Bool = true, onboarded: Bool = true, locked: Bool = false) -> RootScreen {
        RootScreen.resolve(storageReady: ready, isConfigured: configured, isAuthenticated: authed, hasCompletedOnboarding: onboarded, locked: locked)
    }

    @Test func lockedAndSignedInShowsOnlyTheLock() {
        let s = screen(locked: true)
        #expect(s == .lock)
        #expect(!s.showsAppContent)
        #expect(screen(onboarded: false, locked: true) == .lock)
    }

    @Test func theLockNeverCoversASignedOutApp() {
        #expect(screen(authed: false, locked: true) == .app(.login))
        #expect(screen(configured: false, authed: false, locked: true) == .app(.connect))
    }

    @Test func unlockedFollowsTheGates() {
        #expect(screen() == .app(.main))
        #expect(screen().showsAppContent)
        #expect(screen(onboarded: false) == .app(.onboarding))
    }

    @Test func anUnreadableKeychainWaits() {
        #expect(screen(ready: false) == .waiting)
        #expect(screen(ready: false, locked: true) == .waiting)
        #expect(!screen(ready: false).showsAppContent)
    }
}

struct AppGateTests {
    @Test func gatesFollowRouterDartOrder() {
        #expect(AppGate.resolve(isConfigured: false, isAuthenticated: true, hasCompletedOnboarding: true) == .connect)
        #expect(AppGate.resolve(isConfigured: true, isAuthenticated: false, hasCompletedOnboarding: true) == .login)
        #expect(AppGate.resolve(isConfigured: true, isAuthenticated: true, hasCompletedOnboarding: false) == .onboarding)
        #expect(AppGate.resolve(isConfigured: true, isAuthenticated: true, hasCompletedOnboarding: true) == .main)
    }

    @Test func safeRedirectRules() {
        #expect(AppGate.safeRedirect("/instances/a") == "/instances/a")
        #expect(AppGate.safeRedirect("/dashboard") == "/dashboard")
        #expect(AppGate.safeRedirect(nil) == nil)
        #expect(AppGate.safeRedirect("//host/x") == nil)
        #expect(AppGate.safeRedirect("/x:y") == nil)
        #expect(AppGate.safeRedirect("/a@b") == nil)
        #expect(AppGate.safeRedirect("dashboard") == nil)
    }
}

struct RoutePathTests {
    @Test(arguments: [
        Route.backtests, .backtest("b1"), .backtestPlayback("b1"), .kalshiInstance("k1"),
        .kalshiBacktest("k1"), .kalshiBacktestResult("kb1"), .instance("abc"), .liveTrading("abc"),
        .strategy("s1"), .brokerages, .crypto, .cryptoInstance("c1"), .search,
        .stock(StockRoute(symbol: "BRK/B")), .agentRuns, .nexus, .learning, .models, .tokenUsage,
        .settings, .notificationSettings, .connect, .onboarding,
    ])
    func pathRoundTrips(route: Route) {
        #expect(Route(path: route.path) == route)
    }

    @Test func pathsEncodeLikeDart() {
        #expect(Route.stock(StockRoute(symbol: "BRK/B")).path == "/stock/BRK%2FB")
        #expect(Route.instance("a b").path == "/instances/a%20b")
        #expect(Route.settings.path == "/settings")
    }

    @Test @MainActor func routerLocation() {
        let router = AppRouter()
        #expect(router.location == "/dashboard")
        router.tab = .more
        #expect(router.location == nil)
        router.push(.tokenUsage)
        #expect(router.location == "/token-usage")
    }
}
