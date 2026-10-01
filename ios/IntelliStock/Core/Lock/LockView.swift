import SwiftUI

/// The full-screen biometric gate shown while the app is locked and signed in
/// — `LockScreen` in `lock_screen.dart`, restyled Apple-native: the app tile
/// with no glow, system type, and a Face ID / Touch ID button.
///
/// - Prompts the moment it appears, and again whenever the app returns from a
///   real background (not `.inactive`, which the Face ID sheet itself causes —
///   that would loop after a cancel).
/// - Offers "Log Out" only after a failed attempt.
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
            Color(uiColor: .systemGroupedBackground)
                .ignoresSafeArea()

            VStack(spacing: 0) {
                Spacer()
                VStack(spacing: 0) {
                    AppLogoView(size: 92, radius: 26)
                        .overlay {
                            RoundedRectangle(cornerRadius: 26, style: .continuous)
                                .strokeBorder(Color(uiColor: .separator), lineWidth: 0.5)
                        }
                        .padding(.bottom, 32)

                    Text(headline)
                        .font(.title2.weight(.semibold))
                        .multilineTextAlignment(.center)
                        .accessibilityAddTraits(.isHeader)

                    Text(subtitle)
                        .font(.body)
                        .foregroundStyle(.secondary)
                        .multilineTextAlignment(.center)
                        .padding(.top, 10)

                    AuthPillButton(
                        label: "Login with \(method)",
                        systemImage: isFace ? "faceid" : "touchid",
                        busy: busy,
                        action: attempt
                    )
                    .padding(.top, 42)
                }
                .frame(maxWidth: 328)
                Spacer()

                // The escape hatch only appears once biometrics actually fail —
                // on a normal relaunch the prompt just succeeds.
                if failed {
                    Button("Log Out", action: exitToLogin)
                        .font(.body)
                        .foregroundStyle(.secondary)
                        .disabled(busy)
                        .frame(minHeight: 44)
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
        if failed { return "\(method) wasn\u{2019}t recognized." }
        return "IntelliStock is locked."
    }

    private var subtitle: String {
        if busy { return "Hold still." }
        if failed { return "Try again." }
        return "Verify your identity to continue."
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
    private func exitToLogin() {
        services.lock.releaseLock()
        Task { await services.session.clear() }
    }
}
