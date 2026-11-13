import SwiftUI

/// The sign-in screen — `LoginScreen` in `login_screen.dart`.
///
/// The coin plays its entrance, then the form fades up beneath it. On
/// success the form collapses, lifting the coin while it turns over, and the
/// session lands after the hold so the gate takes over. `redirectPath` is
/// handled by the gate (`AppServices.didSignIn`), as the Dart router did.
///
/// Styled as the web's sign-in page under the coin: "Sign in to IntelliStock"
/// and its line, then a card with the labelled username and password fields
/// and the full-width primary button, then the invitation note. No bloom,
/// no glow, no frosted card.
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
                DS.Surface.page.ignoresSafeArea()
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

    /// The shared measure of the auth flow (the web's `max-w-sm`, kept in
    /// step with the lock screen).
    private let columnWidth: CGFloat = 384
    /// The coin at rest: smaller than the Dart 322 so the web's form card
    /// fits under it on a phone. It still grows to full size on success.
    private let coinSize: CGFloat = 240

    var body: some View {
        let succeeded = model.state.succeeded
        let busy = model.busy

        GeometryReader { proxy in
            ScrollView {
                VStack(spacing: 0) {
                    LoginCoinView(phase: model.coinPhase, size: coinSize, onEntranceStarted: entrance.onCoinEntranceStarted)

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
                // A fixed width: the coin's 470 pt viewport would otherwise
                // widen the column past the page gutter.
                .frame(width: max(min(columnWidth, proxy.size.width - DS.pageGutter * 2), 0))
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
        .background(DS.Surface.page.ignoresSafeArea())
        // Tapping the background gives up focus, so the keyboard drops.
        .contentShape(Rectangle())
        .onTapGesture { focus = nil }
        .animation(.timingCurve(0.215, 0.61, 0.355, 1, duration: 0.62), value: succeeded)
        .task { await model.resolveBiometrics(services.biometrics) }
    }

    @ViewBuilder
    private func form(busy: Bool) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            VStack(alignment: .leading, spacing: 8) {
                Text("Sign in to IntelliStock")
                    .font(DS.Font.pageTitle)
                    .foregroundStyle(DS.Text.primary)
                    .accessibilityAddTraits(.isHeader)
                Text("IntelliStock is invite-only. Sign in with your credentials.")
                    .font(DS.Font.body)
                    .foregroundStyle(DS.Text.secondary)
                    .lineSpacing(4)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .padding(.top, 12)

            // The form card (`ui-card p-6`).
            VStack(alignment: .leading, spacing: 20) {
                if let error = model.displayedError {
                    ErrorRow(message: error)
                        .transition(.opacity)
                }

                VStack(alignment: .leading, spacing: 20) {
                    DSField("Username") {
                        TextField(text: $username, prompt: Text("your username").foregroundStyle(DS.Text.faint)) {
                            Text("Username")
                        }
                        .textContentType(.username)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .submitLabel(.next)
                        .focused($focus, equals: .username)
                        .onSubmit { focus = .password }
                        .frame(minHeight: 40)
                        .dsInput(focused: focus == .username)
                    }
                    DSField("Password") {
                        HStack(spacing: 4) {
                            Group {
                                if showPassword {
                                    TextField(text: $password, prompt: Text("••••••••").foregroundStyle(DS.Text.faint)) {
                                        Text("Password")
                                    }
                                } else {
                                    SecureField(text: $password, prompt: Text("••••••••").foregroundStyle(DS.Text.faint)) {
                                        Text("Password")
                                    }
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
                            }
                            .buttonStyle(.dsIcon)
                            .padding(.trailing, -8)
                            .disabled(busy)
                            .accessibilityLabel(showPassword ? "Hide password" : "Show password")
                        }
                        .frame(minHeight: 40)
                        .dsInput(focused: focus == .password)
                    }
                }
                .disabled(busy)
                .onChange(of: username) { model.clearErrors() }
                .onChange(of: password) { model.clearErrors() }

                AuthPillButton(label: "Sign in", showChevron: false, busy: busy, busyLabel: "Signing in\u{2026}", action: submit)
            }
            .padding(24)
            .dsCardSurface()
            .padding(.top, 32)
            .animation(DSMotion.ease(0.2), value: model.displayedError)

            if model.biometricAvailable == true {
                biometricRow(busy: busy)
                    .padding(.top, 16)
            }

            Text("Access is by invitation only. Ask someone with an account to create one for you.")
                .font(DS.Font.caption)
                .foregroundStyle(DS.Text.muted)
                .lineSpacing(3)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.top, 24)
        }
    }

    /// The biometric switch as a plain row under the card: a muted glyph,
    /// the label and the web switch.
    private func biometricRow(busy: Bool) -> some View {
        let disabled = model.biometricBusy || busy
        return Toggle(isOn: Binding(
            get: { services.lock.enabled },
            set: { want in Task { await model.toggleBiometricLock(want, lock: services.lock) } }
        )) {
            HStack(spacing: 12) {
                SettingsIconTile(systemImage: model.biometricIsFace ? "faceid" : Symbol.named("fingerprint"))
                Text("Unlock with \(model.biometricMethod)")
                    .font(DS.Font.body)
                    .foregroundStyle(DS.Text.label)
            }
        }
        .padding(.horizontal, 4)
        .frame(minHeight: 44)
        .disabled(disabled)
    }

    private func submit() {
        focus = nil
        Task { await model.submit(username: username, password: password) }
    }
}
