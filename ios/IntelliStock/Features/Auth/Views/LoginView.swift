import SwiftUI

/// The sign-in screen — `LoginScreen` in `login_screen.dart`.
///
/// The coin plays its entrance, then the form fades up beneath it. On
/// success the form collapses, lifting the coin while it turns over, and the
/// session lands after the hold so the gate takes over. `redirectPath` is
/// handled by the gate (`AppServices.didSignIn`), as the Dart router did.
///
/// Native form: the plain grouped background, inset-grouped field rows and the
/// shared glass `AuthPillButton` — no bloom, no frosted card.
struct LoginView: View {
    let redirectPath: String?

    @Environment(AppServices.self) private var services
    @State private var model: LoginModel?
    @State private var entrance = LoginEntranceModel()

    var body: some View {
        Group {
            if let model {
                LoginContent(model: model, entrance: entrance)
            } else {
                DS.Surface.canvas.ignoresSafeArea()
            }
        }
        .onAppear {
            if model == nil {
                let services = services
                model = LoginModel(repository: { services.authRepository }, session: services.session)
            }
        }
        .onDisappear { entrance.dispose() }
    }
}

private enum LoginField: Hashable {
    case username, password
}

private struct LoginContent: View {
    let model: LoginModel
    let entrance: LoginEntranceModel

    @Environment(AppServices.self) private var services
    @State private var username = ""
    @State private var password = ""
    @State private var showPassword = false
    @FocusState private var focus: LoginField?

    /// The shared measure of the auth flow (kept in step with the lock screen).
    private let columnWidth: CGFloat = 340

    var body: some View {
        let succeeded = model.state.succeeded
        let busy = model.busy

        GeometryReader { proxy in
            ScrollView {
                VStack(spacing: 0) {
                    LoginCoinView(phase: model.coinPhase, onEntranceStarted: entrance.onCoinEntranceStarted)

                    // Everything below the coin collapses on success, which
                    // lifts the coin to the middle without measuring anything.
                    if !succeeded {
                        form(busy: busy)
                            .opacity(entrance.showForm ? 1 : 0)
                            .offset(y: entrance.showForm ? 0 : 16)
                            .allowsHitTesting(entrance.showForm)
                            .animation(.timingCurve(0.215, 0.61, 0.355, 1, duration: 0.42), value: entrance.showForm)
                            .transition(.opacity.combined(with: .offset(y: 40)))
                    }
                }
                .frame(maxWidth: columnWidth)
                .padding(.horizontal, 24)
                .frame(maxWidth: .infinity)
                // Sits above centre: the coin needs headroom and the form
                // reads better high on the screen.
                .frame(minHeight: proxy.size.height * 0.84, alignment: .center)
            }
            .scrollBounceBehavior(.basedOnSize)
            .scrollDismissesKeyboard(.interactively)
            // The success coin grows beyond its slot; don't crop it.
            .scrollClipDisabled()
        }
        .background(DS.Surface.canvas.ignoresSafeArea())
        // Tapping the background gives up focus, so the keyboard drops.
        .contentShape(Rectangle())
        .onTapGesture { focus = nil }
        .animation(.timingCurve(0.215, 0.61, 0.355, 1, duration: 0.62), value: succeeded)
        .task { await model.resolveBiometrics(services.biometrics) }
    }

    @ViewBuilder
    private func form(busy: Bool) -> some View {
        VStack(spacing: 0) {
            Text("Welcome back")
                .font(.title2.weight(.semibold))
                .multilineTextAlignment(.center)
                .padding(.top, 20)
                .padding(.bottom, 28)
                .accessibilityAddTraits(.isHeader)

            VStack(spacing: 14) {
                if let error = model.displayedError {
                    LoginErrorBanner(message: error)
                        .transition(.opacity)
                }

                // The placeholder carries the field name, so there are no
                // labels above the inputs.
                VStack(spacing: 0) {
                    LoginFieldRow(systemImage: Symbol.named("person")) {
                        TextField("Username", text: $username)
                            .textContentType(.username)
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                            .submitLabel(.next)
                            .focused($focus, equals: .username)
                            .onSubmit { focus = .password }
                    }
                    Divider().padding(.leading, 48)
                    LoginFieldRow(systemImage: Symbol.named("lock")) {
                        Group {
                            if showPassword {
                                TextField("Password", text: $password)
                            } else {
                                SecureField("Password", text: $password)
                            }
                        }
                        .textContentType(.password)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .submitLabel(.done)
                        .focused($focus, equals: .password)
                        .onSubmit(submit)

                        Button {
                            showPassword.toggle()
                        } label: {
                            Image(systemName: Symbol.named(showPassword ? "visibility_off" : "visibility"))
                                .foregroundStyle(.secondary)
                                .frame(width: 44, height: 44)
                        }
                        .buttonStyle(.plain)
                        .disabled(busy)
                        .accessibilityLabel(showPassword ? "Hide password" : "Show password")
                    }
                }
                .background(DS.Surface.panel, in: .rect(cornerRadius: DS.Radius.control, style: .continuous))
                .disabled(busy)
                .opacity(busy ? 0.5 : 1)
                .onChange(of: username) { model.clearErrors() }
                .onChange(of: password) { model.clearErrors() }

                AuthPillButton(label: "Sign In", busy: busy, busyLabel: "Signing in\u{2026}", action: submit)
                    .padding(.top, 4)

                if model.biometricAvailable == true {
                    biometricRow(busy: busy)
                        .padding(.top, 4)
                }
            }
            .animation(.easeOut(duration: 0.2), value: model.displayedError)
        }
    }

    private func biometricRow(busy: Bool) -> some View {
        let disabled = model.biometricBusy || busy
        return Toggle(isOn: Binding(
            get: { services.lock.enabled },
            set: { want in Task { await model.toggleBiometricLock(want, lock: services.lock) } }
        )) {
            Label {
                Text("Unlock with \(model.biometricMethod)")
            } icon: {
                Image(systemName: model.biometricIsFace ? "faceid" : Symbol.named("fingerprint"))
                    .foregroundStyle(.tint)
            }
        }
        .padding(.horizontal, 16)
        .frame(minHeight: 52)
        .background(DS.Surface.panel, in: .rect(cornerRadius: DS.Radius.control, style: .continuous))
        .disabled(disabled)
        .opacity(disabled ? 0.5 : 1)
    }

    private func submit() {
        focus = nil
        Task { await model.submit(username: username, password: password) }
    }
}

/// A field row: a leading glyph and the input, Settings-style.
private struct LoginFieldRow<Content: View>: View {
    let systemImage: String
    @ViewBuilder let content: Content

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: systemImage)
                .foregroundStyle(.secondary)
                .frame(width: 24)
                .accessibilityHidden(true)
            content
        }
        .padding(.leading, 14)
        .padding(.trailing, 4)
        .frame(minHeight: 52)
    }
}

/// The inline error above the fields — `_ErrorBanner`.
private struct LoginErrorBanner: View {
    let message: String

    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        let ink = DS.Palette.onTint(DS.Palette.danger, in: colorScheme)
        HStack(spacing: 9) {
            Image(systemName: Symbol.named("error"))
                .foregroundStyle(ink)
                .accessibilityHidden(true)
            Text(message)
                .font(.footnote)
                .foregroundStyle(ink)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
        .padding(.horizontal, 13)
        .padding(.vertical, 11)
        .background(DS.Palette.danger.opacity(DS.tintFill), in: .rect(cornerRadius: DS.Radius.control, style: .continuous))
        .accessibilityElement(children: .combine)
    }
}
