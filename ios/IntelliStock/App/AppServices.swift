import Foundation
import Observation
import SwiftUI
import UIKit

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
    /// Screens that hide the floating chat button while on screen.
    let chatDock = ChatDockChrome()

    /// The client every repository uses. Rebuilt against the new origin
    /// whenever the server URL changes (Dart's `dioProvider` watched the URL
    /// store); the session is its token source throughout.
    var apiClient: ApiClient

    /// Where to go after the next sign-in — Dart's `/login?redirect=`. Set to
    /// the current location on sign-out, or to a deep link opened while
    /// signed out.
    var loginRedirect: String?

    /// False while the keychain cannot be read — iOS may launch (prewarm) the
    /// app before the first unlock after a reboot. The URL, session and lock
    /// stay unloaded (and the widget's credentials untouched) until
    /// protected data becomes available; `RootView` shows a blank screen
    /// meanwhile rather than Connect.
    private(set) var isStorageReady = true

    /// True when the keychain still could not be read with the app in the
    /// foreground (a foreground scene means the device is unlocked, so this
    /// is a real keychain error, e.g. -34018). `RootView` then offers a
    /// retry instead of the blank waiting screen.
    private(set) var isStorageUnavailable = false

    @ObservationIgnored let biometrics: any BiometricAuthenticating
    @ObservationIgnored private let storage: any SecureStorage
    @ObservationIgnored private var protectedDataObserver: (any NSObjectProtocol)?
    @ObservationIgnored private let notificationCenter: NotificationCenter
    @ObservationIgnored private let urlSession: URLSession
    @ObservationIgnored private let pushRegistrar: any PushRegistering
    /// The tab that held `loginRedirect`, so a More destination returns to
    /// the More tab rather than over the Dashboard.
    @ObservationIgnored private var loginRedirectTab: AppTab?

    /// Registers only while signed in and unlocked: Dart sent nothing from
    /// behind the lock screen.
    @ObservationIgnored private(set) lazy var push = PushService(
        repository: { [unowned self] in PushRepository(client: self.apiClient) },
        registrar: pushRegistrar,
        shouldRegister: { [unowned self] in self.session.isAuthenticated && !self.lock.locked }
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
        now: @escaping () -> Date = Date.init,
        notificationCenter: NotificationCenter = .default
    ) {
        let urlStore = ApiBaseUrlStore(storage: storage)
        let urlLoaded = urlStore.load()
        let session = SessionStore(storage: storage, widgetSync: widgetSync, apiBaseUrl: { [urlStore] in urlStore.baseUrl })
        let sessionLoaded = session.load()
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
        self.storage = storage
        self.notificationCenter = notificationCenter
        self.urlSession = urlSession
        self.pushRegistrar = pushRegistrar
        self.selectedAccount = SelectedAccountModel(store: storage)
        self.apiClient = ApiClient(baseURL: urlStore.baseUrl, tokens: session, session: urlSession)

        urlStore.onChange = { [weak self] _ in self?.rebuildClient() }

        if !(urlLoaded && sessionLoaded) {
            isStorageReady = false
            protectedDataObserver = notificationCenter.addObserver(
                forName: UIApplication.protectedDataDidBecomeAvailableNotification,
                object: nil,
                queue: .main
            ) { [weak self] _ in
                MainActor.assumeIsolated { self?.reloadStorage() }
            }
        }
    }

    /// Loads what the keychain refused at launch, once it can be read: the
    /// URL (rebuilding the client), the session, the lock seed and the
    /// selected account. Idempotent: does nothing once loaded.
    ///
    /// Called when protected data becomes available (the background fast
    /// path) and on every `.active` scene phase — a suspended process is not
    /// guaranteed the notification, and the items are `WhenUnlocked`, so the
    /// read fails whenever the device is locked, not only before first
    /// unlock.
    func reloadStorage() {
        guard !isStorageReady else { return }
        guard urlStore.load(), session.load() else { return }
        lock.state = AppLock.seed(storage: storage, isAuthenticated: session.isAuthenticated)
        selectedAccount.reload()
        isStorageReady = true
        isStorageUnavailable = false
        if let protectedDataObserver {
            notificationCenter.removeObserver(protectedDataObserver)
            self.protectedDataObserver = nil
        }
    }

    /// The Retry button on the "keychain unavailable" screen, and the
    /// foreground attempt: reload, and flag a failure that persists while
    /// active.
    func retryStorageLoad() {
        reloadStorage()
        isStorageUnavailable = !isStorageReady
    }

    /// The Sign Out escape on the "keychain unavailable" screen: delete the
    /// stored session, then load whatever can be read. When the keychain
    /// still refuses, start signed out with no server, so the person can
    /// reconnect instead of retrying forever.
    func signOutOfUnavailableStorage() {
        storage.delete(SessionStore.tokenKey)
        storage.delete(SessionStore.userKey)
        reloadStorage()
        guard !isStorageReady else { return }
        lock.state = AppLock.seed(storage: storage, isAuthenticated: false)
        isStorageReady = true
        isStorageUnavailable = false
        if let protectedDataObserver {
            notificationCenter.removeObserver(protectedDataObserver)
            self.protectedDataObserver = nil
        }
    }

    /// The onboarding gate handed back to the app (first run, or a re-run
    /// from Settings): accounts linked during onboarding must show on the
    /// dashboard, so the shared brokerage list reloads.
    func didCompleteOnboarding() async {
        await dashboard.loadBrokerages()
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
        // A different server: nothing cached from the old one may show.
        resetSessionModels()
    }

    /// Returns the session-scoped shared models to their fresh state.
    private func resetSessionModels() {
        dashboard.reset()
    }

    // MARK: Lifecycle

    /// Every `scenePhase` change: a deferred keychain load is retried on
    /// `.active`, pollers pause in the background, and the lock counts the
    /// absence.
    func scenePhaseChanged(_ phase: ScenePhase) {
        if phase == .active, !isStorageReady {
            retryStorageLoad()
        }
        lifecycle.handle(phase)
        lock.handle(phase)
    }

    // MARK: Sign-in transitions (router.dart's redirect)

    /// The session ended (sign-out, 401, server change): remember where the
    /// person was, as `/login?redirect=<location>`, start a fresh shell, drop
    /// the shared models' data, and open the lock — it protects a session, so
    /// a password sign-in must not be followed by Face ID.
    func didSignOut() {
        loginRedirect = router.location
        loginRedirectTab = router.location == nil ? nil : router.tab
        router.reset()
        lock.releaseLock()
        resetSessionModels()
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
