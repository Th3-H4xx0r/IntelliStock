import SwiftUI

/// The top of the app: the gates from `router.dart`'s redirect, the chat
/// overlay from `app.dart`, and the biometric lock over everything.
///
/// 1. No server URL → `ConnectView`.
/// 2. Signed out → `LoginView(redirectPath:)`.
/// 3. Onboarding incomplete → `OnboardingView`.
/// 4. Otherwise → `MainTabView`.
///
/// `LockView` covers all of it while locked AND signed in, so signing out
/// drops the gate instead of stranding someone behind Face ID.
struct RootView: View {
    @Environment(AppServices.self) private var services
    @Environment(\.scenePhase) private var scenePhase

    var body: some View {
        let session = services.session
        let gate = AppGate.resolve(
            isConfigured: services.urlStore.isConfigured,
            isAuthenticated: session.isAuthenticated,
            hasCompletedOnboarding: session.hasCompletedOnboarding
        )
        let locked = services.lock.locked && session.isAuthenticated

        ZStack {
            Group {
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
            }
            .allowsHitTesting(!locked)
            .accessibilityHidden(locked)

            ChatEntrySlot()
                .allowsHitTesting(!locked)
                .accessibilityHidden(locked)

            if locked {
                // Appears instantly (nothing unprotected may flash on resume);
                // fades away on unlock.
                LockView()
                    .transition(.asymmetric(insertion: .identity, removal: .opacity))
                    .zIndex(1)
            }
        }
        .animation(.easeOut(duration: 0.25), value: locked)
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
