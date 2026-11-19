import SwiftUI

/// One of Jarvis's requests: what it asks to do and what is at stake, the
/// exact parameters (checked against the server's fingerprint), the result
/// once it ran, and Approve and Deny at the bottom.
///
/// Approve asks once more, then Face ID signs the decision with this
/// iPhone's Secure Enclave key; Deny goes straight to Face ID. The model
/// sends a decision once and never retries it (`ApprovalDetailModel`).
struct ApprovalDetailView: View {
    let approvalId: String

    @Environment(AppServices.self) private var services
    @State private var model: ApprovalDetailModel?
    @State private var confirm: ConfirmRequest?
    @State private var confirmRunning = false
    @State private var toast: Toast?
    /// The decision last started here, for the "Checking…" title.
    @State private var lastDecision: ApprovalDecision?

    var body: some View {
        Group {
            if let model {
                content(model)
            } else {
                ApprovalDetailPlaceholder()
            }
        }
        .navigationTitle("Request")
        .navigationBarTitleDisplayMode(.inline)
        .confirmAlert($confirm, isRunning: $confirmRunning)
        .toast($toast)
        .onAppear {
            if model == nil {
                let services = services
                model = ApprovalDetailModel(
                    approvalId: approvalId,
                    source: { services.approvalsRepository },
                    signer: services.approvalSigner,
                    devices: services.approvalDeviceStore,
                    baseURL: { services.apiClient.baseURL }
                )
            }
        }
        .task(id: model == nil) {
            await model?.poll(lifecycle: services.lifecycle)
        }
    }

    @ViewBuilder
    private func content(_ model: ApprovalDetailModel) -> some View {
        switch model.state {
        case .loading:
            ApprovalDetailPlaceholder()
        case .failed(let error):
            List {
                Section {
                    ErrorRow(message: approvalsErrorText(error)) { Task { await model.load() } }
                        .dsClearRow(top: 8)
                }
            }
            .dsList()
            .refreshable { await model.refresh() }
        case .loaded(let approval):
            List {
                header(approval)
                if let refreshError = model.refreshError {
                    Section {
                        ErrorRow(message: refreshError)
                            .dsClearRow()
                    }
                }
                requestSection(model, approval)
                if !approvalParametersAreEmpty(approval.query) {
                    jsonSection("Query", approval.query)
                }
                jsonSection("Body", approval.body)
                if approval.resultStatus != nil || !approval.result.isNull {
                    resultSection(approval)
                }
                if approval.decidedAt != nil || approval.deviceId != nil {
                    decisionSection(model, approval)
                }
            }
            .dsList()
            .refreshable { await model.refresh() }
            .safeAreaInset(edge: .bottom, spacing: 0) {
                actionBar(model, approval)
            }
        }
    }

    // MARK: Sections

    private func header(_ approval: McpApproval) -> some View {
        Section {
            VStack(alignment: .leading, spacing: 10) {
                Text(verbatim: approval.summary)
                    .font(DS.Font.large)
                    .foregroundStyle(DS.Text.primary)
                    .fixedSize(horizontal: false, vertical: true)
                    .textSelection(.enabled)
                MoneyStateLine(state: approval.moneyState)
                TimelineView(.periodic(from: .now, by: 1)) { context in
                    let display = approvalDisplayStatus(approval, now: context.date)
                    StatusDot(approvalStatusLabel(display), tone: approvalStatusTone(display))
                }
            }
            .dsClearRow(top: 8)
        }
    }

    private func requestSection(_ model: ApprovalDetailModel, _ approval: McpApproval) -> some View {
        DSSection("Request", rowMinHeight: 44) {
            ApprovalMonoRow("Action", "\(approval.method) \(approval.path)")
            DSKeyValueRow("From", approval.client)
            if let createdAt = approval.createdAt {
                DSKeyValueRow("Asked", Self.time(createdAt))
            }
            DSKeyValueRow(
                "Fingerprint",
                "\(approval.bodySha256.prefix(12)) · " + (model.fingerprintMatches ? "matches" : "doesn't match"),
                valueColor: model.fingerprintMatches ? DS.Text.primary : DS.Palette.amber
            )
        }
    }

    private func jsonSection(_ title: String, _ json: JSON) -> some View {
        DSSection(title, style: .plain) {
            ApprovalJSONBlock(text: approvalParametersText(json))
                .dsClearRow()
        }
    }

    private func resultSection(_ approval: McpApproval) -> some View {
        DSSection("Result", style: .plain) {
            VStack(alignment: .leading, spacing: 8) {
                if let code = approval.resultStatus {
                    Text(verbatim: "HTTP \(code)")
                        .font(DS.Font.bodyMedium)
                        .foregroundStyle(DS.Text.label)
                        .monospacedDigit()
                }
                if !approval.result.isNull {
                    ApprovalJSONBlock(text: approvalParametersText(approval.result))
                }
                if approval.resultTruncated {
                    Text("The server cut this result to 256 KB.")
                        .font(DS.Font.caption)
                        .foregroundStyle(DS.Text.muted)
                }
            }
            .dsClearRow()
        }
    }

    private func decisionSection(_ model: ApprovalDetailModel, _ approval: McpApproval) -> some View {
        DSSection("Decision", rowMinHeight: 44) {
            if let decidedAt = approval.decidedAt {
                DSKeyValueRow("Decided", Self.time(decidedAt))
            }
            if let deviceId = approval.deviceId {
                ApprovalMonoRow("Device", deviceId == model.device?.deviceId ? "\(deviceId) · this iPhone" : deviceId)
            }
        }
    }

    // MARK: Action bar

    private func actionBar(_ model: ApprovalDetailModel, _ approval: McpApproval) -> some View {
        TimelineView(.periodic(from: .now, by: 1)) { context in
            let gate = model.gate(now: context.date)
            let busy = model.isBusy || confirmRunning
            VStack(spacing: 10) {
                if approvalDisplayStatus(approval, now: context.date) == "pending", let expiresAt = approval.expiresAt {
                    Text(verbatim: expiryLabel(remaining: expiresAt.timeIntervalSince(context.date)))
                        .font(DS.Font.caption)
                        .foregroundStyle(DS.Text.label)
                        .monospacedDigit()
                }
                HStack(spacing: 12) {
                    Button(role: .destructive) {
                        Task { await decide(model, .deny) }
                    } label: {
                        Text(buttonTitle(model, .deny))
                    }
                    .buttonStyle(.dsSecondary(size: .large, fullWidth: true))
                    .disabled(!gate.canDeny || busy)

                    Button {
                        confirmApprove(model, approval)
                    } label: {
                        Text(buttonTitle(model, .approve))
                    }
                    .buttonStyle(.dsPrimary(size: .large, fullWidth: true))
                    .disabled(!gate.canApprove || busy)
                }
                if let reason = gate.reason, !reason.isEmpty {
                    Text(verbatim: reason)
                        .font(DS.Font.caption)
                        .foregroundStyle(DS.Text.muted)
                        .multilineTextAlignment(.center)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if gate.reason == kApprovalRegisterFirst {
                    NavigationLink(value: Route.approvalDevices) {
                        Text("Open Jarvis approvals")
                    }
                    .buttonStyle(.dsGhost)
                }
            }
            .frame(maxWidth: .infinity)
            .padding(.horizontal, DS.pageGutter)
            .padding(.vertical, 12)
            .background {
                DS.Surface.page
                    .overlay(alignment: .top) { DSDivider() }
                    .ignoresSafeArea(edges: .bottom)
            }
        }
    }

    /// The decision's own title, or where it is while one runs.
    private func buttonTitle(_ model: ApprovalDetailModel, _ decision: ApprovalDecision) -> String {
        let idle = decision == .approve ? "Approve" : "Deny"
        switch model.phase {
        case .idle: return idle
        case .authorizing(let running): return running == decision ? "Waiting for Face ID…" : idle
        case .sending(let running): return running == decision ? "Sending…" : idle
        case .checking: return lastDecision == decision ? "Checking…" : idle
        }
    }

    // MARK: Deciding

    private func confirmApprove(_ model: ApprovalDetailModel, _ approval: McpApproval) {
        confirm = ConfirmRequest(
            title: "Approve this request?",
            body: "\(approval.summary)\n\(moneyStateLabel(approval.moneyState)). It runs as soon as you confirm with Face ID.",
            confirmLabel: "Approve",
            role: nil,
            onConfirm: { await decide(model, .approve) }
        )
    }

    private func decide(_ model: ApprovalDetailModel, _ decision: ApprovalDecision) async {
        lastDecision = decision
        if let next = Self.toast(for: await model.decide(decision)) {
            toast = next
        }
    }

    /// The toast a decision ends with: none when Face ID was dismissed, for
    /// a second tap, or when there is nothing to say.
    static func toast(for result: ApprovalDecisionResult) -> Toast? {
        guard !result.message.isEmpty else { return nil }
        switch result.outcome {
        case .decided: return Toast(result.message, style: .success)
        case .rejected, .blocked: return Toast(result.message, style: .error)
        case .notPending, .uncertain: return Toast(result.message, style: .info)
        case .cancelled, .ignored: return nil
        }
    }

    /// "Nov 19, 6:00:12 AM" in the phone's time zone.
    private static func time(_ date: Date) -> String {
        date.formatted(.dateTime.month(.abbreviated).day().hour().minute().second())
    }
}

/// A key-value row whose value is monospaced: a method and path, an id.
private struct ApprovalMonoRow: View {
    let key: String
    let value: String

    init(_ key: String, _ value: String) {
        self.key = key
        self.value = value
    }

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 16) {
            Text(key)
                .font(DS.Font.body)
                .foregroundStyle(DS.Text.secondary)
            Spacer(minLength: 8)
            Text(verbatim: value)
                .font(DS.Font.mono)
                .foregroundStyle(DS.Text.primary)
                .multilineTextAlignment(.trailing)
                .textSelection(.enabled)
        }
        .accessibilityElement(children: .combine)
    }
}

/// The first load: the header and the request card as static blocks.
private struct ApprovalDetailPlaceholder: View {
    var body: some View {
        List {
            Section {
                VStack(alignment: .leading, spacing: 10) {
                    Skeleton(width: 260, height: 18)
                    Skeleton(width: 120, height: 12)
                }
                .dsClearRow(top: 8)
            }
            DSSection("Request", rowMinHeight: 44) {
                ForEach(0..<4, id: \.self) { _ in
                    Skeleton(width: 220, height: 14)
                }
            }
        }
        .dsList()
        .allowsHitTesting(false)
        .accessibilityLabel("Loading request")
    }
}
