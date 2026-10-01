import Foundation
import SwiftUI
import Testing
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
/// (Review Focus 5), 401 handling (Review Focus 3) and the router redirect.
@Suite(.serialized)
@MainActor
struct AppServicesTests {
    init() { StubURLProtocol.reset() }

    private func make(_ initial: [String: String], probe: WidgetSyncProbe = WidgetSyncProbe()) -> AppServices {
        AppServices(
            storage: InMemorySecureStorage(initial),
            biometrics: FakeBiometrics(available: true, authResult: true),
            widgetSync: probe.sync,
            urlSession: StubURLProtocol.session,
            pushRegistrar: FakePushRegistrar(grant: false)
        )
    }

    private static let signedIn: [String: String] = [
        ApiBaseUrlStore.storageKey: "https://old.example.test",
        SessionStore.tokenKey: "jwt",
        SessionStore.userKey: #"{"username": "pk", "has_completed_onboarding": true}"#,
    ]

    @Test func readsUrlSessionAndLockSynchronously() {
        var initial = Self.signedIn
        initial[AppLock.enabledKey] = "true"
        initial[AppLock.timeoutKey] = "60"
        let services = make(initial)
        #expect(services.urlStore.baseUrl == "https://old.example.test")
        #expect(services.session.isAuthenticated)
        #expect(services.lock.locked)
        #expect(services.lock.timeout == .oneMinute)
        #expect(services.apiClient.baseURL == "https://old.example.test")
    }

    @Test func theWidgetMirrorsTheUrlLoadedFirst() {
        let probe = WidgetSyncProbe()
        _ = make(Self.signedIn, probe: probe)
        #expect(probe.defaults.string(forKey: WidgetSync.Key.apiBase) == "https://old.example.test")
    }

    @Test func changingTheServerRebuildsTheClientAgainstTheNewOrigin() async throws {
        StubURLProtocol.respond(json: #"{"ok": true}"#)
        let services = make(Self.signedIn)
        let old = services.apiClient
        try services.urlStore.set("https://new.example.test/api/")
        #expect(services.apiClient !== old)
        #expect(services.apiClient.baseURL == "https://new.example.test")

        _ = try await services.apiClient.get("/health")
        let hosts = StubURLProtocol.requests.compactMap { $0.url?.host() }
        #expect(hosts == ["new.example.test"])
        #expect(StubURLProtocol.requests.first?.value(forHTTPHeaderField: "Authorization") == "Bearer jwt")
    }

    @Test func anUnchangedUrlKeepsTheClient() throws {
        let services = make(Self.signedIn)
        let old = services.apiClient
        try services.urlStore.set("https://old.example.test/")
        #expect(services.apiClient === old)
    }

    @Test func a401ClearsTheSession() async {
        StubURLProtocol.respond(status: 401, json: #"{"detail": "expired"}"#)
        let services = make(Self.signedIn)
        _ = try? await services.apiClient.get("/instances")
        #expect(!services.session.isAuthenticated)
    }

    @Test func aRefreshedTokenPersists() async throws {
        StubURLProtocol.respond(json: "{}", headers: ["X-Refreshed-Token": "slid.jwt"])
        let storage = InMemorySecureStorage(Self.signedIn)
        let services = AppServices(
            storage: storage,
            biometrics: FakeBiometrics(available: true, authResult: true),
            widgetSync: WidgetSyncProbe().sync,
            urlSession: StubURLProtocol.session,
            pushRegistrar: FakePushRegistrar(grant: false)
        )
        _ = try await services.apiClient.get("/instances")
        #expect(await eventually { services.session.token == "slid.jwt" })
        #expect(storage.read(SessionStore.tokenKey) == "slid.jwt")
    }

    @Test func scenePhasesReachTheLockAndTheLifecycle() {
        var initial = Self.signedIn
        initial[AppLock.enabledKey] = "true"
        let services = make(initial)
        services.lock.releaseLock()
        services.scenePhaseChanged(.background)
        #expect(!services.lifecycle.isForeground)
        services.scenePhaseChanged(.active)
        #expect(services.lifecycle.isForeground)
        #expect(services.lock.locked)
    }

    // MARK: Redirect

    @Test func signOutRemembersTheLocationAndResetsTheShell() {
        let services = make(Self.signedIn)
        services.router.tab = .instances
        services.router.push(.instance("abc"))
        services.didSignOut()
        #expect(services.loginRedirect == "/instances/abc")
        #expect(services.router.tab == .dashboard)
        #expect(services.router.stack(for: .instances).isEmpty)
    }

    @Test func signInHonoursASafeRedirectInItsTab() {
        let services = make(Self.signedIn)
        services.router.tab = .more
        services.router.push(.settings)
        services.didSignOut()
        services.didSignIn()
        #expect(services.router.tab == .more)
        #expect(services.router.stack(for: .more) == [.settings])
        #expect(services.loginRedirect == nil)
    }

    @Test func signInToATabRootSelectsIt() {
        let services = make(Self.signedIn)
        services.router.tab = .strategies
        services.didSignOut()
        #expect(services.loginRedirect == "/strategies")
        services.didSignIn()
        #expect(services.router.tab == .strategies)
    }

    @Test func signInWithOnboardingIncompleteDropsTheRedirect() async throws {
        let services = make(Self.signedIn)
        services.loginRedirect = "/instances/abc"
        try await services.session.setUser(["username": "pk", "has_completed_onboarding": false])
        services.didSignIn()
        #expect(services.router.stack(for: .dashboard).isEmpty)
        #expect(services.loginRedirect == nil)
    }

    @Test func unsafeRedirectsAreIgnored() {
        for unsafe in ["//evil.example", "https://evil.example", "/a@b", "relative"] {
            let services = make(Self.signedIn)
            services.loginRedirect = unsafe
            services.didSignIn()
            #expect(services.router.stack(for: .dashboard).isEmpty)
            #expect(services.router.tab == .dashboard)
        }
    }

    @Test func deepLinksOpenWhenSignedInAndWaitWhenSignedOut() {
        let services = make(Self.signedIn)
        services.openDeepLink(URL(string: "intellistock://instances/abc/live")!)
        #expect(services.router.stack(for: .dashboard) == [.liveTrading("abc")])

        let signedOut = make([ApiBaseUrlStore.storageKey: "https://old.example.test"])
        signedOut.openDeepLink(URL(string: "intellistock://kalshi")!)
        #expect(signedOut.loginRedirect == "/kalshi")
        signedOut.openDeepLink(URL(string: "https://example.com/instances/x")!)
        #expect(signedOut.loginRedirect == "/kalshi")
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
