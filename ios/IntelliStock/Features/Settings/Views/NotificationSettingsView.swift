import SwiftUI

/// Per-category notification routing — `NotificationSettingsScreen` in
/// `notification_settings_screen.dart`: test delivery, registered devices,
/// then one section per group with independent Discord and iOS-push switches.
/// SnackBars become toasts.
struct NotificationSettingsView: View {
    @Environment(AppServices.self) private var services
    @State private var model: NotificationPrefsModel?
    @State private var devices: PushDevicesModel?
    @State private var toast: Toast?
    @State private var removing: Set<String> = []

    var body: some View {
        Group {
            switch model?.prefs ?? .loading {
            case .loading:
                ProgressView()
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            case .failed(let error):
                Text("Failed to load preferences\n\(settingsErrorText(error))")
                    .font(.body)
                    .foregroundStyle(DS.Palette.danger)
                    .multilineTextAlignment(.center)
                    .padding(32)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            case .loaded(let prefs):
                content(prefs)
            }
        }
        .background(DS.Surface.canvas)
        .navigationTitle("Notifications")
        .navigationBarTitleDisplayMode(.inline)
        .toast($toast)
        .task {
            guard model == nil else { return }
            let services = services
            let model = NotificationPrefsModel(repository: { services.notificationPrefsRepository })
            let devices = PushDevicesModel(repository: { PushRepository(client: services.apiClient) })
            self.model = model
            self.devices = devices
            async let prefs: Void = model.load()
            async let list: Void = devices.load()
            _ = await (prefs, list)
        }
    }

    private func content(_ prefs: NotificationPrefs) -> some View {
        List {
            Section {
                Text("Send a sample notification to confirm a channel works.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                HStack(spacing: 10) {
                    NotificationTestButton(icon: "discord", label: "Test Discord") { await sendTest(.discord) }
                    NotificationTestButton(icon: "notifications", label: "Test iOS Push") { await sendTest(.push) }
                }
                .listRowBackground(Color.clear)
                .listRowInsets(EdgeInsets())
            } header: {
                Text("TEST DELIVERY")
            }

            Section("REGISTERED DEVICES") {
                switch devices?.devices ?? .loading {
                case .loading:
                    HStack(spacing: 10) {
                        ProgressView()
                        Text("Checking registered devices…")
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    }
                case .failed(let error):
                    Text("Could not load devices: \(settingsErrorText(error))")
                        .font(.footnote)
                        .foregroundStyle(DS.Palette.danger)
                case .loaded(let list):
                    if list.isEmpty {
                        VStack(alignment: .leading, spacing: 4) {
                            Text("No devices registered yet.")
                                .font(.body.weight(.semibold))
                            Text("Tap \"Enable push on this device\" and allow notifications. Requires a physical device with the app installed (push doesn't work in the simulator).")
                                .font(.footnote)
                                .foregroundStyle(.secondary)
                        }
                    } else {
                        ForEach(list) { device in
                            HStack(spacing: 10) {
                                Image(systemName: Symbol.named("notifications"))
                                    .foregroundStyle(.tint)
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(verbatim: device.tokenSuffix)
                                    Text(pushDeviceSubtitle(device))
                                        .font(.footnote)
                                        .foregroundStyle(.secondary)
                                }
                                Spacer()
                                Button {
                                    Task { await remove(device) }
                                } label: {
                                    Image(systemName: Symbol.named("delete"))
                                        .frame(width: 44, height: 44)
                                }
                                .buttonStyle(.borderless)
                                .tint(.secondary)
                                .disabled(removing.contains(device.deviceToken))
                                .accessibilityLabel("Remove")
                            }
                        }
                    }
                }
                Button {
                    Task { await enableOnThisDevice() }
                } label: {
                    Label("Enable Push on This Device", systemImage: Symbol.named("notifications"))
                }
            }

            ForEach(NotificationPrefsModel.groupedTypes(prefs), id: \.group) { group in
                Section(group.group.uppercased()) {
                    ForEach(group.types, id: \.key) { type in
                        NotificationCategoryRow(
                            label: type.label,
                            description: type.desc,
                            route: prefs.routeFor(type.key),
                            onDiscord: { value in Task { await toggle(type.key, .discord, value) } },
                            onPush: { value in Task { await toggle(type.key, .push, value) } }
                        )
                    }
                }
            }
        }
        .listStyle(.insetGrouped)
    }

    // MARK: Actions

    private func enableOnThisDevice() async {
        toast = Toast("Requesting push permission…")
        await services.push.enable()
        // The APNs token arrives asynchronously; give it a moment, then refresh.
        try? await Task.sleep(for: .seconds(2))
        await devices?.refresh()
    }

    private func remove(_ device: PushDevice) async {
        removing.insert(device.deviceToken)
        defer { removing.remove(device.deviceToken) }
        do {
            try await PushRepository(client: services.apiClient).unregister(device.deviceToken)
            await devices?.refresh()
            toast = Toast("Device removed", style: .success)
        } catch {
            if error is CancellationError { return }
            toast = Toast("Could not remove: \(settingsErrorText(error))", style: .error)
        }
    }

    private func toggle(_ category: String, _ channel: NotifChannel, _ value: Bool) async {
        if let error = await model?.toggle(category, channel, value) {
            toast = Toast("Could not save: \(error)", style: .error)
        }
    }

    private func sendTest(_ channel: NotifChannel) async {
        guard let model else { return }
        do {
            let result = try await model.sendTest(channel)
            let outcome = NotificationTestOutcome(channel: channel, result: result)
            toast = Toast(outcome.message, style: outcome.ok ? .success : .error)
            // The send may have auto-corrected a device's env; refresh the list.
            if channel == .push { await devices?.refresh() }
        } catch {
            if error is CancellationError { return }
            let outcome = NotificationTestOutcome(channel: channel, error: error)
            toast = Toast(outcome.message, style: .error)
        }
    }
}

/// One category — `_CategoryRow`: label, description, two switches.
private struct NotificationCategoryRow: View {
    let label: String
    let description: String
    let route: CategoryRoute
    let onDiscord: (Bool) -> Void
    let onPush: (Bool) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            VStack(alignment: .leading, spacing: 2) {
                Text(label).font(.body.weight(.semibold))
                Text(description)
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
            HStack(spacing: 20) {
                Toggle(isOn: Binding(get: { route.discord }, set: { onDiscord($0) })) {
                    Text("Discord")
                        .font(.footnote)
                        .foregroundStyle(route.discord ? .primary : .secondary)
                }
                .fixedSize()
                Toggle(isOn: Binding(get: { route.push }, set: { onPush($0) })) {
                    Text("iOS push")
                        .font(.footnote)
                        .foregroundStyle(route.push ? .primary : .secondary)
                }
                .fixedSize()
            }
        }
        .padding(.vertical, 4)
    }
}

/// A tinted action button — `_TestButton`.
private struct NotificationTestButton: View {
    let icon: String
    let label: String
    let action: () async -> Void

    @State private var running = false

    var body: some View {
        Button {
            running = true
            Task {
                await action()
                running = false
            }
        } label: {
            Label(label, systemImage: Symbol.named(icon))
                .font(.subheadline)
                .lineLimit(1)
                .frame(maxWidth: .infinity)
        }
        .buttonStyle(.bordered)
        .controlSize(.large)
        .disabled(running)
    }
}
