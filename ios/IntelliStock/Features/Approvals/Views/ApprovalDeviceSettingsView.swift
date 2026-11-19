import SwiftUI

/// Settings › Jarvis approvals. Jarvis asks before it moves live money or
/// does something it can't undo; a registered iPhone approves or denies with
/// a Face ID-protected Secure Enclave signature. Here the operator registers
/// this iPhone, watches it become an approver (the server waits 10 minutes),
/// and removes approver devices.
struct ApprovalDeviceSettingsView: View {
    @Environment(AppServices.self) private var services
    @State private var model: ApprovalDevicesModel?
    @State private var name = "iPhone"
    @State private var confirm: ConfirmRequest?
    @State private var confirmRunning = false
    @State private var toast: Toast?

    static let intro = "Jarvis asks before it moves live money or does something it can't undo. Approve or deny those requests here, with Face ID."
    static let registerHelp = "The server sends a Discord alert, and this iPhone can approve 10 minutes after it registers."
    static let registeredMessage = "Registered. This iPhone can approve in 10 minutes."

    var body: some View {
        Group {
            if let model {
                content(model)
            } else {
                Color.clear
            }
        }
        .navigationTitle("Jarvis approvals")
        .navigationBarTitleDisplayMode(.inline)
        .confirmAlert($confirm, isRunning: $confirmRunning)
        .toast($toast)
        .onAppear {
            if model == nil {
                let services = services
                model = ApprovalDevicesModel(
                    source: { services.approvalsRepository },
                    signer: services.approvalSigner,
                    store: services.approvalDeviceStore,
                    baseURL: { services.apiClient.baseURL }
                )
            }
        }
        .task(id: model == nil) {
            await model?.load()
        }
    }

    private func content(_ model: ApprovalDevicesModel) -> some View {
        // Which rows show doesn't depend on the time; the status row's text
        // does, so it ticks on its own.
        let record = model.status(now: Date()).record
        return List {
            Section {
                Text(Self.intro)
                    .font(DS.Font.pageSub)
                    .foregroundStyle(DS.Text.muted)
                    .fixedSize(horizontal: false, vertical: true)
                    .dsClearRow(top: 8)
                if !model.isHardwareBacked {
                    Text("Simulator: this uses a software key, not the Secure Enclave.")
                        .font(DS.Font.caption)
                        .foregroundStyle(DS.Text.muted)
                        .fixedSize(horizontal: false, vertical: true)
                        .dsClearRow(top: 8)
                }
            }

            DSSection("This iPhone") {
                if let record {
                    registeredRows(model, record)
                } else {
                    registerForm(model)
                }
            }

            approversSection(model, thisDeviceId: record?.deviceId)
        }
        .dsList()
        .refreshable { await model.load() }
    }

    // MARK: This iPhone

    private func registerForm(_ model: ApprovalDevicesModel) -> some View {
        VStack(alignment: .leading, spacing: 16) {
            DSField("Name", help: Self.registerHelp) {
                DSTextField("iPhone", text: $name)
                    .textInputAutocapitalization(.words)
                    .submitLabel(.done)
            }
            Button {
                let name = name
                Task { showRegisterResult(await model.register(name: name)) }
            } label: {
                HStack(spacing: 8) {
                    if model.registering {
                        ProgressView()
                            .controlSize(.small)
                            .tint(DS.Palette.onAccent)
                    }
                    Text("Register this iPhone")
                }
            }
            .buttonStyle(.dsPrimary(fullWidth: true))
            .disabled(model.registering)
        }
        .padding(.vertical, 12)
    }

    @ViewBuilder
    private func registeredRows(_ model: ApprovalDevicesModel, _ record: ApprovalDeviceRecord) -> some View {
        let busy = model.registering || model.removing.contains(record.deviceId)
        TimelineView(.periodic(from: .now, by: 1)) { context in
            ApprovalDeviceStatusRow(status: model.status(now: context.date), now: context.date)
        }
        HStack(spacing: 8) {
            Button("Register again") { confirmRegisterAgain(model, record) }
                .buttonStyle(.dsSecondary)
            Button("Remove this iPhone", role: .destructive) { confirmRemoveThisIPhone(model, record) }
                .buttonStyle(.dsSecondary)
            Spacer(minLength: 0)
            if busy {
                ProgressView()
                    .controlSize(.small)
            }
        }
        .disabled(busy || confirmRunning)
    }

    // MARK: Registered approvers

    @ViewBuilder
    private func approversSection(_ model: ApprovalDevicesModel, thisDeviceId: String?) -> some View {
        switch model.devices {
        case .loading:
            DSSection("Registered approvers") {
                VStack(alignment: .leading, spacing: 8) {
                    Skeleton(width: 120, height: 14)
                    Skeleton(width: 80, height: 12)
                }
                .accessibilityElement()
                .accessibilityLabel("Loading approvers")
            }
        case .failed(let error):
            DSSection("Registered approvers", style: .plain) {
                ErrorRow(message: approvalsErrorText(error)) { Task { await model.load() } }
                    .dsClearRow()
            }
        case .loaded(let devices):
            DSSection("Registered approvers") {
                if devices.isEmpty {
                    Text("No approvers registered")
                        .font(DS.Font.body)
                        .foregroundStyle(DS.Text.muted)
                } else {
                    ForEach(devices) { device in
                        approverRow(model, device, isThisIPhone: device.id == thisDeviceId)
                    }
                }
            }
        }
    }

    @ViewBuilder
    private func approverRow(_ model: ApprovalDevicesModel, _ device: McpApprovalDevice, isThisIPhone: Bool) -> some View {
        let row = TimelineView(.periodic(from: .now, by: 1)) { context in
            EntityRow(device.name.isEmpty ? "Unnamed device" : device.name,
                      subtitle: Self.approverSubtitle(device, isThisIPhone: isThisIPhone, now: context.date)) {
                if model.removing.contains(device.id) {
                    ProgressView()
                        .controlSize(.small)
                }
            }
        }
        if isThisIPhone {
            row
        } else {
            row.swipeActions(edge: .trailing, allowsFullSwipe: false) {
                Button("Remove", systemImage: "trash") { confirmRemove(model, device) }
                    .tint(DS.Palette.loss)
            }
        }
    }

    /// "This iPhone", "Active", or "Active in m:ss".
    static func approverSubtitle(_ device: McpApprovalDevice, isThisIPhone: Bool, now: Date) -> String? {
        if isThisIPhone { return "This iPhone" }
        guard let activeAt = device.activeAt else { return nil }
        return now < activeAt ? "Active in \(approvalCountdown(activeAt.timeIntervalSince(now)))" : "Active"
    }

    // MARK: Actions

    private func confirmRegisterAgain(_ model: ApprovalDevicesModel, _ record: ApprovalDeviceRecord) {
        confirm = ConfirmRequest(
            title: "Register again",
            body: "This iPhone gets a new key. Its current registration stops working, and the new one can approve after 10 minutes.",
            confirmLabel: "Register again",
            role: nil,
            onConfirm: { showRegisterResult(await model.register(name: record.name)) }
        )
    }

    private func confirmRemoveThisIPhone(_ model: ApprovalDevicesModel, _ record: ApprovalDeviceRecord) {
        confirm = ConfirmRequest(
            title: "Remove this iPhone",
            body: "This iPhone stops approving Jarvis's requests until you register it again.",
            confirmLabel: "Remove",
            role: .destructive,
            onConfirm: { showRemoveResult(await model.remove(record.deviceId)) }
        )
    }

    private func confirmRemove(_ model: ApprovalDevicesModel, _ device: McpApprovalDevice) {
        let who = device.name.isEmpty ? "This device" : device.name
        confirm = ConfirmRequest(
            title: "Remove approver",
            body: "\(who) stops approving Jarvis's requests.",
            confirmLabel: "Remove",
            role: .destructive,
            onConfirm: { showRemoveResult(await model.remove(device.id)) }
        )
    }

    private func showRegisterResult(_ message: String?) {
        toast = message.map { Toast($0, style: .error) } ?? Toast(Self.registeredMessage, style: .success)
    }

    private func showRemoveResult(_ message: String?) {
        if let message { toast = Toast(message, style: .error) }
    }
}

/// This iPhone's standing: an amber countdown while the server waits, a live
/// dot once it approves, a warning when the server no longer lists it.
private struct ApprovalDeviceStatusRow: View {
    let status: ApprovalDeviceStatus
    let now: Date

    var body: some View {
        switch status {
        case .notRegistered:
            EmptyView()
        case .activating(let record):
            VStack(alignment: .leading, spacing: 4) {
                StatusDot("Becomes an approver in \(approvalCountdown(record.activeAt.timeIntervalSince(now)))",
                          tone: .warn, font: DS.Font.body.monospacedDigit())
                deviceName(record)
            }
        case .active(let record):
            VStack(alignment: .leading, spacing: 4) {
                StatusDot("Approves Jarvis's requests", tone: .live)
                deviceName(record)
            }
        case .missingOnServer:
            HStack(spacing: 6) {
                DSStatusDot(.warn)
                Text("The server no longer lists this iPhone. Register it again.")
                    .font(DS.Font.body)
                    .foregroundStyle(DS.Text.label)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .accessibilityElement(children: .combine)
        }
    }

    private func deviceName(_ record: ApprovalDeviceRecord) -> some View {
        Text(verbatim: record.name)
            .font(DS.Font.caption)
            .foregroundStyle(DS.Text.muted)
            .padding(.leading, 18)
    }
}

private extension ApprovalDeviceStatus {
    /// The stored registration, whatever its state.
    var record: ApprovalDeviceRecord? {
        switch self {
        case .notRegistered: nil
        case .activating(let record), .active(let record), .missingOnServer(let record): record
        }
    }
}
