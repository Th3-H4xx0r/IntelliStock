import SwiftUI

/// The full-screen biometric gate shown while the app is locked and signed in
/// — `LockScreen` in `lock_screen.dart`, styled as the web's sign-in page:
/// the page colour, the app tile with a hairline and no glow, a 24/500 title
/// with a zinc-400 line, the full-width primary button for Face ID / Touch
/// ID, and "Log out" as a quiet text action.
///
/// - Prompts the moment it appears, and again whenever the app returns from a
///   real background (not `.inactive`, which the Face ID sheet itself causes —
///   that would loop after a cancel).
/// - Offers "Log out" only after a failed attempt.
struct LockView: View {
    @Environment(AppServices.self) private var services
    @Environment(\.scenePhase) private var scenePhase

    @State private var busy = false
    @State private var failed = false
    /// How this device authenticates. Optimistic default, corrected once the
    /// available types resolve; it only drives copy, never access.
    @State private var method = "Face ID"
    @State private var isFace = true
    @State private var wasInBackground = false

    var body: some View {
        ZStack {
            DS.Surface.page
                .ignoresSafeArea()

            VStack(spacing: 0) {
                Spacer()
                VStack(spacing: 0) {
                    AppLogoView(size: 72, radius: 18)
                        .overlay {
                            RoundedRectangle(cornerRadius: 18, style: .continuous)
                                .strokeBorder(DS.Line.card, lineWidth: DS.Line.width)
                        }
                        .padding(.bottom, 28)

                    Text(headline)
                        .font(DS.Font.pageTitle)
                        .foregroundStyle(DS.Text.primary)
                        .multilineTextAlignment(.center)
                        .accessibilityAddTraits(.isHeader)

                    Text(subtitle)
                        .font(DS.Font.body)
                        .foregroundStyle(DS.Text.secondary)
                        .multilineTextAlignment(.center)
                        .padding(.top, 8)

                    AuthPillButton(
                        label: "Unlock with \(method)",
                        systemImage: isFace ? "faceid" : "touchid",
                        showChevron: false,
                        busy: busy,
                        action: attempt
                    )
                    .padding(.top, 32)
                }
                .frame(maxWidth: 384)
                Spacer()

                // The escape hatch only appears once biometrics actually fail —
                // on a normal relaunch the prompt just succeeds.
                if failed {
                    Button("Log out", action: exitToLogin)
                        .buttonStyle(.dsGhost(size: .large))
                        .disabled(busy)
                }
            }
            .padding(.horizontal, 24)
            .padding(.vertical, 20)
        }
        .task {
            resolveMethod()
            attempt()
        }
        .onChange(of: scenePhase) { _, phase in
            switch phase {
            case .background:
                wasInBackground = true
            case .active:
                if wasInBackground {
                    wasInBackground = false
                    if services.lock.locked, !busy { attempt() }
                }
            default:
                break
            }
        }
    }

    private var headline: String {
        if busy { return "Authenticating…" }
        if failed { return "\(method) wasn\u{2019}t recognized" }
        return "IntelliStock is locked"
    }

    private var subtitle: String {
        if busy { return "Hold still" }
        if failed { return "Try again" }
        return "Verify your identity to continue"
    }

    private func resolveMethod() {
        Task {
            let types = await services.biometrics.availableTypes()
            let face = types.contains(.face)
            method = face ? "Face ID" : (types.contains(.fingerprint) ? "Touch ID" : "biometrics")
            isFace = face
        }
    }

    private func attempt() {
        guard !busy else { return }
        busy = true
        failed = false
        Task {
            let ok = await services.lock.unlock()
            busy = false
            // On success the gate removes this view.
            failed = !ok
        }
    }

    /// Signs out of the locked session without changing the lock setting.
    /// The session clears first, so the screen goes straight from the lock to
    /// Login; `AppServices.didSignOut()` then releases the lock.
    private func exitToLogin() {
        Task { await services.session.clear() }
    }
}
