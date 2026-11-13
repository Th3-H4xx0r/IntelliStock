import SwiftUI

/// Per-category notification routing — `NotificationSettingsScreen` in
/// `notification_settings_screen.dart`, laid out as the web's preferences
/// page: test delivery, registered devices, then one card per alert group
/// whose rows carry independent Discord and iOS push switches. SnackBars
/// become toasts.
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
                NotificationSettingsSkeleton()
            case .failed(let error):
                ErrorRow(message: "Couldn't load preferences: \(settingsErrorText(error))")
                    .padding(.horizontal, DS.pageGutter)
                    .padding(.top, 16)
                    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
            case .loaded(let prefs):
                content(prefs)
            }
        }
        .background(DS.Surface.page)
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
            // Test delivery: the web's one-card row with its two buttons.
            DSSection(rowMinHeight: 68) {
                VStack(alignment: .leading, spacing: 12) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Test delivery")
                            .font(DS.Font.body)
                            .foregroundStyle(DS.Text.soft)
                        Text("Send a sample notification to confirm a channel works")
                            .font(DS.Font.caption)
                            .foregroundStyle(DS.Text.muted)
                    }
                    HStack(spacing: 8) {
                        NotificationTestButton(label: "Test Discord") { await sendTest(.discord) }
                        NotificationTestButton(label: "Test iOS push") { await sendTest(.push) }
                    }
                }
                .padding(.vertical, 6)
            }

            DSSection("Registered devices") {
                switch devices?.devices ?? .loading {
                case .loading:
                    HStack(spacing: 10) {
                        ProgressView()
                            .controlSize(.small)
                        Text("Checking registered devices…")
                            .font(DS.Font.body)
                            .foregroundStyle(DS.Text.muted)
                    }
                case .failed(let error):
                    Text("Couldn't load devices: \(settingsErrorText(error))")
                        .font(DS.Font.body)
                        .foregroundStyle(DS.Palette.loss)
                case .loaded(let list):
                    if list.isEmpty {
                        Text("No device registered yet. Tap Enable push on this device and allow notifications. Push needs a physical device; it doesn't work in the simulator.")
                            .font(DS.Font.body)
                            .foregroundStyle(DS.Text.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                            .padding(.vertical, 6)
                    } else {
                        ForEach(list) { device in
                            let busy = removing.contains(device.deviceToken)
                            EntityRow(device.tokenSuffix, subtitle: pushDeviceSubtitle(device)) {
                                if busy { ProgressView().controlSize(.small) }
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
                InlineActionRow("Enable push on this device", systemImage: Symbol.named("notifications")) {
                    Task { await enableOnThisDevice() }
                }
            }

            // One card per alert group (the web's routing table): each type
            // with its description, then its Discord and iOS push switches.
            ForEach(NotificationPrefsModel.groupedTypes(prefs), id: \.group) { group in
                DSSection(group.group) {
                    ForEach(group.types, id: \.key) { type in
                        let route = prefs.routeFor(type.key)
                        VStack(alignment: .leading, spacing: 10) {
                            VStack(alignment: .leading, spacing: 2) {
                                Text(type.label)
                                    .font(DS.Font.body)
                                    .foregroundStyle(DS.Text.primary)
                                if !type.desc.isEmpty {
                                    Text(type.desc)
                                        .font(DS.Font.caption)
                                        .foregroundStyle(DS.Text.muted)
                                        .fixedSize(horizontal: false, vertical: true)
                                }
                            }
                            HStack(spacing: 24) {
                                NotificationChannelSwitch(label: "Discord", isOn: Binding(
                                    get: { route.discord },
                                    set: { value in Task { await toggle(type.key, .discord, value) } }
                                ))
                                .accessibilityLabel("\(type.label): Discord")
                                NotificationChannelSwitch(label: "iOS push", isOn: Binding(
                                    get: { route.push },
                                    set: { value in Task { await toggle(type.key, .push, value) } }
                                ))
                                .accessibilityLabel("\(type.label): iOS push")
                                Spacer(minLength: 0)
                            }
                        }
                        .padding(.vertical, 6)
                    }
                }
            }
        }
        .dsList()
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

/// One channel's switch in a type's row: a 12 pt zinc-500 label, then the
/// web switch.
private struct NotificationChannelSwitch: View {
    let label: String
    @Binding var isOn: Bool

    var body: some View {
        Toggle(isOn: $isOn) {
            Text(label)
                .font(DS.Font.caption)
                .foregroundStyle(DS.Text.muted)
        }
        .fixedSize()
    }
}

/// A test send — `_TestButton`, as the web's secondary button: "Sending…"
/// with a spinner while the send runs.
private struct NotificationTestButton: View {
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
            HStack(spacing: 6) {
                if running { ProgressView().controlSize(.mini) }
                Text(running ? "Sending…" : label)
            }
        }
        .buttonStyle(.dsSecondary)
        .disabled(running)
    }
}

/// The first load: the test card and a group's rows as static blocks.
private struct NotificationSettingsSkeleton: View {
    var body: some View {
        List {
            DSSection(rowMinHeight: 68) {
                VStack(alignment: .leading, spacing: 10) {
                    Skeleton(width: 120, height: 14)
                    Skeleton(width: 220, height: 12)
                }
            }
            DSSection("Registered devices") {
                Skeleton(width: 180, height: 14)
            }
            DSSection {
                ForEach(0..<4, id: \.self) { _ in
                    VStack(alignment: .leading, spacing: 8) {
                        Skeleton(width: 140, height: 14)
                        Skeleton(width: 200, height: 12)
                    }
                }
            }
        }
        .dsList()
        .allowsHitTesting(false)
        .accessibilityLabel("Loading preferences")
    }
}
