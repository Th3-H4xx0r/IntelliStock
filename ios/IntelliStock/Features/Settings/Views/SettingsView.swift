import SwiftUI

/// Settings — `SettingsScreen` in `settings_screen.dart`, as the web's card
/// rows: Security, Preferences, AI analysis, Account, About. There is no
/// appearance setting: the app is dark only, like the web.
struct SettingsView: View {
    @Environment(AppServices.self) private var services

    /// Nil until `canCheck()` answers (`Checking…`).
    @State private var biometricsAvailable: Bool?
    @State private var lockToggleBusy = false
    @State private var confirm: ConfirmRequest?
    @State private var actionBusy = false
    @State private var toast: Toast?
    @State private var licensesOpen = false

    var body: some View {
        let lock = services.lock
        List {
            DSSection("Security") {
                biometricRow(lock)

                SettingsRow(
                    icon: "schedule",
                    title: "Auto-lock after",
                    subtitle: "Time before the app locks in the background"
                ) {
                    Menu {
                        Section("Lock the app after this much time in the background") {
                            Picker("Auto-lock timeout", selection: Binding(
                                get: { lock.timeout },
                                set: { value in Task { await lock.setTimeout(value) } }
                            )) {
                                ForEach(LockTimeout.allCases, id: \.self) { option in
                                    Text(option.label).tag(option)
                                }
                            }
                            .pickerStyle(.inline)
                        }
                    } label: {
                        // The web select's value and chevrons.
                        HStack(spacing: 6) {
                            Text(lock.timeout.label)
                                .monospacedDigit()
                            Image(systemName: "chevron.up.chevron.down")
                                .font(.system(size: 12))
                                .foregroundStyle(DS.Text.muted)
                        }
                        .font(DS.Font.body)
                        .foregroundStyle(lock.enabled ? DS.Text.secondary : DS.Text.faint)
                        .frame(minHeight: 44)
                        .contentShape(Rectangle())
                    }
                    .disabled(!lock.enabled)
                    .accessibilityLabel("Auto-lock timeout, \(lock.timeout.label)")
                }

                SettingsRow(
                    icon: "check_circle",
                    tile: Symbol.named("check"),
                    title: "Require unlock on launch",
                    subtitle: "Always prompt when the app is opened fresh"
                ) {
                    // A read-only state reads as quiet text.
                    Text(lock.enabled ? "On" : "Off")
                        .font(DS.Font.body)
                        .foregroundStyle(DS.Text.secondary)
                }
            }

            DSSection("Preferences") {
                NavigationLink(value: Route.notificationSettings) {
                    SettingsRow(
                        icon: "notifications",
                        title: "Notifications",
                        subtitle: "Discord and iOS push per alert category"
                    ) { EmptyView() }
                }
            }

            AnalysisSettingsSection(services: services)

            DSSection("Account") {
                SettingsRow(
                    icon: "person",
                    title: "Signed in as",
                    subtitle: services.session.username
                ) { EmptyView() }

                Button {
                    confirmReRunOnboarding()
                } label: {
                    SettingsRow(
                        icon: "replay",
                        title: "Re-run onboarding",
                        subtitle: "Reset and walk through setup again"
                    ) { EmptyView() }
                }
                .buttonStyle(.plain)
                .disabled(actionBusy)

                Button {
                    confirmLogout()
                } label: {
                    SettingsRow(
                        icon: "logout",
                        title: "Log out",
                        titleColor: DS.Palette.loss,
                        subtitle: "Sign out of your account"
                    ) { EmptyView() }
                }
                .buttonStyle(.plain)
                .disabled(actionBusy)
            }

            DSSection("About") {
                SettingsRow(
                    icon: "bolt",
                    tile: "bolt",
                    title: "Version",
                    subtitle: appVersionString()
                ) { EmptyView() }

                // Over-the-air updates. Brings the update sheet back even
                // after Later.
                Button {
                    Task { await services.appUpdates.checkNow() }
                } label: {
                    SettingsRow(
                        icon: "system_update",
                        tile: "arrow.down.app",
                        title: "Check for updates",
                        subtitle: appUpdateSubtitle(services.appUpdates.status)
                    ) {
                        if services.appUpdates.status == .checking {
                            ProgressView()
                                .controlSize(.small)
                                .tint(DS.Text.muted)
                        }
                    }
                }
                .buttonStyle(.plain)
                .disabled(services.appUpdates.status == .checking)

                NavigationLink(value: Route.connect) {
                    SettingsRow(
                        icon: "database",
                        title: "Backend",
                        subtitle: services.urlStore.baseUrl,
                        verbatimSubtitle: true
                    ) { EmptyView() }
                }

                Button {
                    licensesOpen = true
                } label: {
                    SettingsRow(
                        icon: "check",
                        tile: "doc.text",
                        title: "Open-source licenses",
                        subtitle: "Third-party package licenses"
                    ) { SettingsChevron() }
                }
                .buttonStyle(.plain)
            }
        }
        .dsList()
        .navigationTitle("Settings")
        .navigationBarTitleDisplayMode(.inline)
        .confirmAlert($confirm, isRunning: $actionBusy)
        .toast($toast)
        .sheet(isPresented: $licensesOpen) { SettingsLicensesSheet() }
        .task {
            biometricsAvailable = await services.biometrics.canCheck()
        }
    }

    /// Biometric lock: the whole row is the switch (the web's switch rows),
    /// a spinner while the check or the toggle runs.
    @ViewBuilder
    private func biometricRow(_ lock: AppLock) -> some View {
        if biometricsAvailable == nil || lockToggleBusy {
            SettingsRow(icon: "lock", title: "Biometric lock", subtitle: biometricsSubtitle) {
                ProgressView()
                    .controlSize(.small)
            }
        } else {
            Toggle(isOn: Binding(
                get: { lock.enabled },
                set: { value in Task { await handleLockToggle(value) } }
            )) {
                SettingsRow(icon: "lock", title: "Biometric lock", subtitle: biometricsSubtitle) { EmptyView() }
            }
            .disabled(biometricsAvailable != true)
        }
    }

    private var biometricsSubtitle: String {
        switch biometricsAvailable {
        case nil: "Checking…"
        case true?: "Lock the app when you leave"
        case false?: "No biometrics enrolled on this device"
        }
    }

    // MARK: Actions

    private func handleLockToggle(_ newValue: Bool) async {
        if lockToggleBusy { return }
        lockToggleBusy = true
        defer { lockToggleBusy = false }
        if newValue {
            if !(await services.lock.enable()) {
                toast = Toast("Biometric authentication failed or is unavailable", style: .error)
            }
        } else {
            await services.lock.disable()
        }
    }

    private func confirmLogout() {
        confirm = ConfirmRequest(
            title: "Log out",
            body: "You will be signed out of IntelliStock. Your data stays on the server.",
            confirmLabel: "Log out",
            role: .destructive,
            onConfirm: { await services.session.clear() },
            onError: { error in toast = Toast(settingsErrorText(error), style: .error) }
        )
    }

    private func confirmReRunOnboarding() {
        let services = services
        confirm = ConfirmRequest(
            title: "Re-run onboarding",
            body: "This resets your onboarding state on the server. Continue?",
            confirmLabel: "Reset and re-run",
            role: nil,
            onConfirm: {
                // The gate shows onboarding as soon as the user says so.
                let res = try await services.onboardingRepository.reset()
                if let user = res["user"], user.isObject {
                    try await services.session.setUser(user)
                }
            },
            onError: { error in
                if error is CancellationError { return }
                toast = Toast("Failed to reset onboarding: \(settingsErrorText(error))", style: .error)
            }
        )
    }
}

/// A settings row (the web's `ui-row`): a muted glyph, the title in 14 pt
/// zinc-100 (or loss red for a destructive one) over a 12 pt zinc-500
/// subtitle, and a trailing view. Also used by `AnalysisSettingsSection`.
struct SettingsRow<Trailing: View>: View {
    /// The Dart (Material) icon name, kept for traceability.
    let icon: String
    /// The SF Symbol when the mapped one reads poorly; nil uses
    /// `Symbol.named(icon)`.
    var tile: String?
    let title: String
    var titleColor: Color?
    let subtitle: String?
    var verbatimSubtitle = false
    @ViewBuilder let trailing: Trailing

    var body: some View {
        HStack(spacing: 12) {
            SettingsIconTile(systemImage: tile ?? Symbol.named(icon))
            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                    .font(DS.Font.body)
                    .foregroundStyle(titleColor ?? DS.Text.primary)
                if let subtitle, !subtitle.isEmpty {
                    Group {
                        if verbatimSubtitle {
                            Text(verbatim: subtitle)
                        } else {
                            Text(subtitle)
                        }
                    }
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Text.muted)
                    .lineLimit(2)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            trailing
        }
        .frame(minHeight: 40)
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
    }
}

private struct SettingsChevron: View {
    var body: some View {
        Image(systemName: "chevron.forward")
            .font(.system(size: 13))
            .foregroundStyle(DS.Text.faint)
            .accessibilityHidden(true)
    }
}

/// `showLicensePage` → a short sheet: the app name and version, and a note
/// that the native app bundles no third-party packages.
private struct SettingsLicensesSheet: View {
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(spacing: 0) {
            HStack(alignment: .top, spacing: 12) {
                Text("Open-source licenses")
                    .font(DS.Font.sectionTitle)
                    .foregroundStyle(DS.Text.primary)
                    .accessibilityAddTraits(.isHeader)
                    .frame(maxWidth: .infinity, alignment: .leading)
                Button { dismiss() } label: {
                    Image(systemName: "xmark")
                }
                .buttonStyle(.dsIcon)
                .accessibilityLabel("Close")
            }
            .padding(.horizontal, 24)
            .padding(.top, 20)
            .padding(.bottom, 16)

            VStack(spacing: 8) {
                AppLogoView(size: 56)
                Text("IntelliStock")
                    .font(DS.Font.baseMedium)
                    .foregroundStyle(DS.Text.primary)
                Text(appVersionString())
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Text.muted)
                    .monospacedDigit()
            }
            .padding(.top, 8)

            Text("This app is built only with Apple frameworks and bundles no third-party packages.")
                .font(DS.Font.body)
                .foregroundStyle(DS.Text.secondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal, 32)
                .padding(.top, 24)
            Spacer(minLength: 0)
        }
        .frame(maxWidth: .infinity)
        .presentationDetents([.medium])
        .presentationDragIndicator(.visible)
        .presentationBackground(DS.Surface.card)
    }
}
