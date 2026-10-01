import Foundation
import Observation
import SwiftUI

/// App-wide services, injected with `.environment(services)` — the native
/// form of the Riverpod providers `main.dart` and `core/**` set up.
///
/// It owns the server-URL store, the session, the lock, the router, push and
/// the API client. Repository accessors live in
/// `AppServices+Repositories.swift` (data agent) as computed properties over
/// `apiClient`, so every request goes to the current server.
@Observable
final class AppServices {
    let router: AppRouter
    let urlStore: ApiBaseUrlStore
    let session: SessionStore
    let lock: AppLock
    let lifecycle: AppLifecycle
    let widgetSync: WidgetSync

    /// The client every repository uses. Rebuilt against the new origin
    /// whenever the server URL changes (Dart's `dioProvider` watched the URL
    /// store); the session is its token source throughout.
    var apiClient: ApiClient

    /// Where to go after the next sign-in — Dart's `/login?redirect=`. Set to
    /// the current location on sign-out, or to a deep link opened while
    /// signed out.
    var loginRedirect: String?

    @ObservationIgnored let biometrics: any BiometricAuthenticating
    @ObservationIgnored private let urlSession: URLSession
    @ObservationIgnored private let pushRegistrar: any PushRegistering
    /// The tab that held `loginRedirect`, so a More destination returns to
    /// the More tab rather than over the Dashboard.
    @ObservationIgnored private var loginRedirectTab: AppTab?

    @ObservationIgnored private(set) lazy var push = PushService(
        repository: { [unowned self] in PushRepository(client: self.apiClient) },
        registrar: pushRegistrar
    )

    @ObservationIgnored private(set) lazy var widgetDataSyncer = WidgetDataSyncer(
        client: { [unowned self] in self.apiClient },
        widgetSync: widgetSync
    )

    // MARK: Shared models
    //
    // keepAlive view models shared across screens. Each reads its repository
    // through a closure over `self`, never a captured client, so a server
    // change reaches it too.

    /// `selectedAccountProvider`: the account the dashboard hero shows.
    let selectedAccount: SelectedAccountModel

    /// `dashboardController` / brokerages + services: shared by the
    /// dashboard, Kalshi, insights and the stock screen.
    @ObservationIgnored private(set) lazy var dashboard = DashboardModel(
        repository: { [unowned self] in self.dashboardRepository }
    )

    /// Reads the server URL, then the session, then the lock seed — all
    /// synchronously, in `main.dart`'s order — so the first frame is already
    /// correct and nothing unprotected flashes.
    init(
        storage: any SecureStorage = KeychainStore(),
        biometrics: any BiometricAuthenticating = BiometricService(),
        widgetSync: WidgetSync = WidgetSync(),
        urlSession: URLSession = ApiClient.makeSession(),
        pushRegistrar: any PushRegistering = SystemPushRegistrar(),
        now: @escaping () -> Date = Date.init
    ) {
        let urlStore = ApiBaseUrlStore(storage: storage)
        urlStore.load()
        let session = SessionStore(storage: storage, widgetSync: widgetSync, apiBaseUrl: { [urlStore] in urlStore.baseUrl })
        session.load()
        let lock = AppLock(
            seed: AppLock.seed(storage: storage, isAuthenticated: session.isAuthenticated),
            storage: storage,
            biometrics: biometrics,
            isAuthenticated: { [session] in session.isAuthenticated },
            now: now
        )

        self.router = AppRouter()
        self.urlStore = urlStore
        self.session = session
        self.lock = lock
        self.lifecycle = AppLifecycle(now: now)
        self.widgetSync = widgetSync
        self.biometrics = biometrics
        self.urlSession = urlSession
        self.pushRegistrar = pushRegistrar
        self.selectedAccount = SelectedAccountModel(store: storage)
        self.apiClient = ApiClient(baseURL: urlStore.baseUrl, tokens: session, session: urlSession)

        urlStore.onChange = { [weak self] _ in self?.rebuildClient() }
    }

    /// A services graph over an in-memory store, for previews and tests that
    /// only need a client.
    convenience init(apiClient: ApiClient) {
        self.init(storage: InMemorySecureStorage(), widgetSync: WidgetSync(defaults: nil), pushRegistrar: NoopPushRegistrar())
        self.apiClient = apiClient
    }

    private func rebuildClient() {
        guard apiClient.baseURL != urlStore.baseUrl else { return }
        apiClient = ApiClient(baseURL: urlStore.baseUrl, tokens: session, session: urlSession)
    }

    // MARK: Lifecycle

    /// Every `scenePhase` change: pollers pause in the background and the lock
    /// counts the absence.
    func scenePhaseChanged(_ phase: ScenePhase) {
        lifecycle.handle(phase)
        lock.handle(phase)
    }

    // MARK: Sign-in transitions (router.dart's redirect)

    /// The session ended (sign-out, 401, server change): remember where the
    /// person was, as `/login?redirect=<location>`, and start a fresh shell.
    func didSignOut() {
        loginRedirect = router.location
        loginRedirectTab = router.location == nil ? nil : router.tab
        router.reset()
    }

    /// A session began. With onboarding complete, honour a safe redirect;
    /// otherwise onboarding comes first and the redirect is dropped.
    func didSignIn() {
        defer {
            loginRedirect = nil
            loginRedirectTab = nil
        }
        guard session.hasCompletedOnboarding,
              let redirect = AppGate.safeRedirect(loginRedirect)
        else { return }
        if AppTab(rootPath: redirect) == nil, let tab = loginRedirectTab {
            router.tab = tab
        }
        router.go(redirect)
    }

    /// `intellistock://instances/abc` → `/instances/abc`: opened in the shell
    /// when signed in, otherwise kept as the post-login redirect.
    func openDeepLink(_ url: URL) {
        guard url.scheme == "intellistock" else { return }
        var path = "/" + (url.host(percentEncoded: true) ?? "")
        let rest = url.path(percentEncoded: true)
        if !rest.isEmpty, rest != "/" { path += rest }
        let gate = AppGate.resolve(
            isConfigured: urlStore.isConfigured,
            isAuthenticated: session.isAuthenticated,
            hasCompletedOnboarding: session.hasCompletedOnboarding
        )
        if gate == .main {
            router.open(path)
        } else if gate == .login {
            loginRedirect = path
            loginRedirectTab = nil
        }
    }
}

/// A registrar that does nothing, for previews and tests.
final class NoopPushRegistrar: PushRegistering {
    func requestAuthorization() async -> Bool { false }
    func registerForRemoteNotifications() {}
}
