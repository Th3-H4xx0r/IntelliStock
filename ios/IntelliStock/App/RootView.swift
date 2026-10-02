import SwiftUI

/// What the root shows — `router.dart`'s redirect plus `app.dart`'s lock
/// gate, as a pure decision so it can be tested.
nonisolated enum RootScreen: Hashable, Sendable {
    /// The keychain is not readable yet (launched while the device was
    /// locked). Only ever seen briefly: the next `.active` retries.
    case waiting
    /// The keychain still refused with the app in the foreground — a real
    /// keychain error. Offers a retry.
    case storageUnavailable
    /// The biometric lock — shown INSTEAD of the app, as `app.dart` returned
    /// `LockScreen` in place of the navigator.
    case lock
    /// The app behind one of the router gates.
    case app(AppGate)

    static func resolve(
        storageReady: Bool,
        isConfigured: Bool,
        isAuthenticated: Bool,
        hasCompletedOnboarding: Bool,
        locked: Bool,
        storageUnavailable: Bool = false
    ) -> RootScreen {
        if !storageReady { return storageUnavailable ? .storageUnavailable : .waiting }
        // The lock only ever covers a session.
        if locked && isAuthenticated { return .lock }
        return .app(AppGate.resolve(
            isConfigured: isConfigured,
            isAuthenticated: isAuthenticated,
            hasCompletedOnboarding: hasCompletedOnboarding
        ))
    }

    /// Whether the signed-in content (tabs, sheets, alerts, chat) is on
    /// screen at all.
    var showsAppContent: Bool {
        if case .app = self { return true }
        return false
    }
}

/// The top of the app: the gates from `router.dart`'s redirect and the
/// biometric lock. The chat that `app.dart` overlaid on everything is now the
/// signed-in shell's tab-bar accessory (`MainTabView`).
///
/// 1. No server URL → `ConnectView`.
/// 2. Signed out → `LoginView(redirectPath:)`.
/// 3. Onboarding incomplete → `OnboardingView`.
/// 4. Otherwise → `MainTabView`.
///
/// While locked AND signed in, `LockView` replaces all of it: the app's views
/// leave the hierarchy, so every sheet, alert and confirmation they presented
/// closes, nothing polls or posts behind the lock, and nothing behind it is
/// readable or tappable. The router's stacks live in `AppRouter`, so the tabs
/// come back where they were on unlock. Signing out drops the lock instead of
/// stranding someone behind Face ID.
struct RootView: View {
    @Environment(AppServices.self) private var services
    @Environment(\.scenePhase) private var scenePhase

    var body: some View {
        let session = services.session
        let screen = RootScreen.resolve(
            storageReady: services.isStorageReady,
            isConfigured: services.urlStore.isConfigured,
            isAuthenticated: session.isAuthenticated,
            hasCompletedOnboarding: session.hasCompletedOnboarding,
            locked: services.lock.locked,
            storageUnavailable: services.isStorageUnavailable
        )

        Group {
            switch screen {
            case .waiting:
                Color(uiColor: .systemGroupedBackground)
                    .ignoresSafeArea()
            case .storageUnavailable:
                ContentUnavailableView {
                    Label("Can't Read Your Sign-In", systemImage: "lock.trianglebadge.exclamationmark")
                } description: {
                    Text("IntelliStock couldn't read its saved server and sign-in from the keychain. Make sure your iPhone is unlocked, then try again.")
                } actions: {
                    Button("Try Again") { services.retryStorageLoad() }
                        .dsProminentButton()
                    // The escape when the keychain never reads again.
                    Button("Sign Out", role: .destructive) { services.signOutOfUnavailableStorage() }
                }
            case .lock:
                LockView()
            case .app(let gate):
                switch gate {
                case .connect:
                    NavigationStack { ConnectView() }
                case .login:
                    LoginView(redirectPath: services.loginRedirect)
                case .onboarding:
                    OnboardingView()
                case .main:
                    // Carries the chat entry (the tab bar's bottom accessory).
                    MainTabView()
                }
            }
        }
        // The lock swaps in instantly, as Flutter's builder did, so nothing
        // (a dismissing sheet or alert) animates over it.
        .transaction { transaction in
            if screen == .lock {
                transaction.disablesAnimations = true
                transaction.animation = nil
            }
        }
        // Initial too: a launch straight into `.active` retries a deferred
        // keychain load without waiting for the next phase change.
        .onChange(of: scenePhase, initial: true) { _, phase in
            services.scenePhaseChanged(phase)
        }
        .onChange(of: screen) { old, new in
            // The onboarding gate handed back (a Settings re-run included).
            if old == .app(.onboarding), new == .app(.main) {
                Task { await services.didCompleteOnboarding() }
            }
        }
        .onChange(of: session.isAuthenticated) { _, authenticated in
            if authenticated {
                services.didSignIn()
            } else {
                services.didSignOut()
            }
        }
        .onOpenURL { services.openDeepLink($0) }
    }
}
