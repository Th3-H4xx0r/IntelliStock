import SwiftUI

/// What the root shows — `router.dart`'s redirect plus `app.dart`'s lock
/// gate, as a pure decision so it can be tested.
nonisolated enum RootScreen: Hashable, Sendable {
    /// The keychain is not readable yet (launched before first unlock).
    case waiting
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
        locked: Bool
    ) -> RootScreen {
        if !storageReady { return .waiting }
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

/// The top of the app: the gates from `router.dart`'s redirect, the chat
/// overlay from `app.dart`, and the biometric lock.
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
            locked: services.lock.locked
        )

        Group {
            switch screen {
            case .waiting:
                Color(uiColor: .systemGroupedBackground)
                    .ignoresSafeArea()
            case .lock:
                LockView()
            case .app(let gate):
                ZStack {
                    switch gate {
                    case .connect:
                        NavigationStack { ConnectView() }
                    case .login:
                        LoginView(redirectPath: services.loginRedirect)
                    case .onboarding:
                        OnboardingView()
                    case .main:
                        MainTabView()
                    }
                    ChatEntrySlot()
                }
            }
        }
        .onChange(of: scenePhase) { _, phase in
            services.scenePhaseChanged(phase)
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
