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
            let services = services
            let model = self.model ?? NotificationPrefsModel(repository: { services.notificationPrefsRepository })
            let devices = self.devices ?? PushDevicesModel(repository: { PushRepository(client: services.apiClient) })
            if self.model == nil { self.model = model }
            if self.devices == nil { self.devices = devices }
            // A first load cut off by leaving (or failed) loads again here.
            let loadPrefs = model.prefs.needsLoad
            let loadDevices = devices.devices.needsLoad
            async let prefs: Void = loadPrefs ? model.load() : ()
            async let list: Void = loadDevices ? devices.load() : ()
            _ = await (prefs, list)
        }
    }

    private func content(_ prefs: NotificationPrefs) -> some View {
        List {
            Section {
                NotificationTestButton(icon: "discord", label: "Test Discord") { await sendTest(.discord) }
                NotificationTestButton(icon: "notifications", label: "Test iOS Push") { await sendTest(.push) }
            } header: {
                Text("Test delivery")
            } footer: {
                Text("Send a sample notification to confirm a channel works.")
            }

            Section("Registered devices") {
                switch devices?.devices ?? .loading {
                case .loading:
                    HStack(spacing: 10) {
                        ProgressView()
                        Text("Checking registered devices…")
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
                            Text("Tap \"Enable push on this device\" and allow notifications. Requires a physical device with the app installed (push doesn't work in the simulator).")
                                .font(.footnote)
                                .foregroundStyle(.secondary)
                        }
                    } else {
                        ForEach(list) { device in
                            let busy = removing.contains(device.deviceToken)
                            EntityRow(device.tokenSuffix, subtitle: pushDeviceSubtitle(device), systemImage: Symbol.named("notifications")) {
                                if busy { ProgressView() }
                            }
                            // Swipe (or long-press) to remove; there was no
                            // confirmation before, and there is none now.
                            .swipeActions(edge: .trailing) {
                                Button("Remove", systemImage: Symbol.named("delete"), role: .destructive) {
                                    Task { await remove(device) }
                                }
                                .disabled(busy)
                            }
                            .contextMenu {
                                Button("Remove", systemImage: Symbol.named("delete"), role: .destructive) {
                                    Task { await remove(device) }
                                }
                                .disabled(busy)
                            }
                        }
                    }
                }
                InlineActionRow("Enable Push on This Device", systemImage: Symbol.named("notifications")) {
                    Task { await enableOnThisDevice() }
                }
            }

            // One section per alert type, titled with the type, holding its
            // two channel switches. The first type of a group carries the
            // group's name above its own.
            ForEach(NotificationPrefsModel.groupedTypes(prefs), id: \.group) { group in
                ForEach(Array(group.types.enumerated()), id: \.element.key) { index, type in
                    let route = prefs.routeFor(type.key)
                    Section {
                        Toggle("Discord", isOn: Binding(
                            get: { route.discord },
                            set: { value in Task { await toggle(type.key, .discord, value) } }
                        ))
                        Toggle("iOS push", isOn: Binding(
                            get: { route.push },
                            set: { value in Task { await toggle(type.key, .push, value) } }
                        ))
                    } header: {
                        NotificationTypeHeader(group: index == 0 ? group.group : nil, label: type.label)
                    } footer: {
                        Text(type.desc)
                    }
                }
            }
        }
        .listStyle(.insetGrouped)
        .refreshable {
            async let prefs: Void = model?.load() ?? ()
            async let list: Void = devices?.refresh() ?? ()
            _ = await (prefs, list)
        }
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

/// A type's section header: the group's name over the first type of each
/// group, then the type's label.
private struct NotificationTypeHeader: View {
    let group: String?
    let label: String

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            if let group {
                // Color.primary, not the hierarchical .primary, which the
                // header resolves to its own secondary grey.
                Text(group)
                    .font(.title3.bold())
                    .foregroundStyle(Color.primary)
                    .padding(.top, 8)
                    .accessibilityAddTraits(.isHeader)
            }
            Text(label)
        }
        .textCase(nil)
    }
}

/// A test-send action as a list row — `_TestButton`, with a spinner while
/// the send runs.
private struct NotificationTestButton: View {
    let icon: String
    let label: String
    let action: () async -> Void

    @State private var running = false

    var body: some View {
        InlineActionRow(label, systemImage: Symbol.named(icon), isBusy: running) {
            running = true
            Task {
                await action()
                running = false
            }
        }
    }
}
