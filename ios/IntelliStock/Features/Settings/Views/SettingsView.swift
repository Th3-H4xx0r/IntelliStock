import SwiftUI

/// Settings — `SettingsScreen` in `settings_screen.dart`, as a Settings-app
/// style inset-grouped list: Security, Preferences, Account, About. There is
/// deliberately no appearance setting (the app follows the system, per HIG).
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
            Section("Security") {
                SettingsRow(
                    icon: "lock",
                    color: .green,
                    title: "Biometric Lock",
                    subtitle: biometricsSubtitle
                ) {
                    if biometricsAvailable == nil || lockToggleBusy {
                        ProgressView()
                    } else {
                        Toggle("Biometric Lock", isOn: Binding(
                            get: { lock.enabled },
                            set: { value in Task { await handleLockToggle(value) } }
                        ))
                        .labelsHidden()
                        .disabled(biometricsAvailable != true)
                    }
                }

                SettingsRow(
                    icon: "schedule",
                    color: .indigo,
                    title: "Auto-lock after",
                    subtitle: "Time before the app locks in background"
                ) {
                    Menu {
                        Section("Lock the app after this much time in the background.") {
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
                        // The system menu-picker look: the value in secondary
                        // with the up-down chevrons.
                        HStack(spacing: 4) {
                            Text(lock.timeout.label)
                            Image(systemName: Symbol.named("unfold_more"))
                                .font(.caption.weight(.semibold))
                        }
                        .font(.body)
                        .foregroundStyle(lock.enabled ? AnyShapeStyle(.secondary) : AnyShapeStyle(.tertiary))
                    }
                    .disabled(!lock.enabled)
                    .accessibilityLabel("Auto-lock timeout, \(lock.timeout.label)")
                }

                SettingsRow(
                    icon: "check_circle",
                    tile: Symbol.named("check"),
                    color: .blue,
                    title: "Require unlock on launch",
                    subtitle: "Always prompt when the app is opened fresh"
                ) {
                    // Settings shows a read-only state as plain secondary text.
                    Text(lock.enabled ? "On" : "Off")
                        .foregroundStyle(.secondary)
                }
            }

            Section("Preferences") {
                NavigationLink(value: Route.notificationSettings) {
                    SettingsRow(
                        icon: "notifications",
                        color: .red,
                        title: "Notifications",
                        subtitle: "Discord & iOS push per alert category"
                    ) { EmptyView() }
                }
            }

            Section("Account") {
                SettingsRow(
                    icon: "person",
                    color: .gray,
                    title: "Signed in as",
                    subtitle: services.session.username
                ) { EmptyView() }

                Button {
                    confirmLogout()
                } label: {
                    SettingsRow(
                        icon: "logout",
                        color: .red,
                        title: "Log out",
                        titleColor: DS.Palette.danger,
                        subtitle: "Sign out of your account"
                    ) { EmptyView() }
                }
                .buttonStyle(.plain)
                .disabled(actionBusy)

                Button {
                    confirmReRunOnboarding()
                } label: {
                    SettingsRow(
                        icon: "replay",
                        color: .orange,
                        title: "Re-run Onboarding",
                        subtitle: "Reset and walk through setup again"
                    ) { EmptyView() }
                }
                .buttonStyle(.plain)
                .disabled(actionBusy)
            }

            Section("About") {
                SettingsRow(
                    icon: "bolt",
                    color: .purple,
                    title: "Version",
                    subtitle: appVersionString()
                ) { EmptyView() }

                NavigationLink(value: Route.connect) {
                    SettingsRow(
                        icon: "database",
                        color: .blue,
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
                        color: .gray,
                        title: "Open-source licenses",
                        subtitle: "Third-party package licenses"
                    ) { SettingsChevron() }
                }
                .buttonStyle(.plain)
            }
        }
        .listStyle(.insetGrouped)
        .navigationTitle("Settings")
        .navigationBarTitleDisplayMode(.inline)
        .confirmAlert($confirm, isRunning: $actionBusy)
        .toast($toast)
        .sheet(isPresented: $licensesOpen) { SettingsLicensesSheet() }
        .task {
            biometricsAvailable = await services.biometrics.canCheck()
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
                toast = Toast("Biometric authentication failed or unavailable.", style: .error)
            }
        } else {
            await services.lock.disable()
        }
    }

    private func confirmLogout() {
        confirm = ConfirmRequest(
            title: "Log out",
            body: "You will be signed out of IntelliStock. Your data stays on the server.",
            confirmLabel: "Log Out",
            role: .destructive,
            onConfirm: { await services.session.clear() },
            onError: { error in toast = Toast(settingsErrorText(error), style: .error) }
        )
    }

    private func confirmReRunOnboarding() {
        let services = services
        confirm = ConfirmRequest(
            title: "Re-run Onboarding",
            body: "This will reset your onboarding state on the server. Continue?",
            confirmLabel: "Reset & Re-run",
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

/// A settings row: a Settings-style icon tile, title + subtitle, and a
/// trailing view.
private struct SettingsRow<Trailing: View>: View {
    /// The Dart (Material) icon name, kept for traceability.
    let icon: String
    /// The tile's SF Symbol when the mapped one is circled or reads poorly
    /// filled; nil uses `Symbol.named(icon)`.
    var tile: String?
    let color: Color
    let title: String
    var titleColor: Color?
    let subtitle: String?
    var verbatimSubtitle = false
    @ViewBuilder let trailing: Trailing

    var body: some View {
        HStack(spacing: 12) {
            SettingsIconTile(systemImage: tile ?? Symbol.named(icon), color: color)
            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                    .font(.body)
                    .foregroundStyle(titleColor.map { AnyShapeStyle($0) } ?? AnyShapeStyle(.primary))
                if let subtitle, !subtitle.isEmpty {
                    Group {
                        if verbatimSubtitle {
                            Text(verbatim: subtitle)
                        } else {
                            Text(subtitle)
                        }
                    }
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
                }
            }
            Spacer(minLength: 8)
            trailing
        }
        .frame(minHeight: 44)
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
    }
}

private struct SettingsChevron: View {
    var body: some View {
        Image(systemName: "chevron.right")
            .font(.footnote.weight(.semibold))
            .foregroundStyle(.tertiary)
            .accessibilityHidden(true)
    }
}

/// `showLicensePage` → native form: the app name and version, and a note that
/// the native app bundles no third-party packages.
private struct SettingsLicensesSheet: View {
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            List {
                Section {
                    VStack(spacing: 8) {
                        AppLogoView(size: 56)
                        Text("IntelliStock").font(.title3.bold())
                        Text(appVersionString())
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    }
                    .frame(maxWidth: .infinity)
                    .listRowBackground(Color.clear)
                }
                Section {
                    Text("This app is built only with Apple frameworks and bundles no third-party packages.")
                        .foregroundStyle(.secondary)
                }
            }
            .navigationTitle("Open-source licenses")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }
                }
            }
        }
        .presentationDetents([.medium])
        .presentationDragIndicator(.visible)
    }
}
