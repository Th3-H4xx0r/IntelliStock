import SwiftUI

/// Link or edit a brokerage account — `_LinkBrokerageSheet` in
/// `link_brokerage_sheet.dart`. Native form: a sheet with an inset-grouped
/// form; the Alpaca / Binance.US tab bar is a segmented control (create mode
/// only), credentials use secure fields.
struct LinkBrokerageSheet: View {
    let editAccount: Brokerage?
    /// Reloads the account list after a save.
    let onSaved: () async -> Void

    @Environment(AppServices.self) private var services
    @Environment(\.dismiss) private var dismiss
    @State private var form: LinkBrokerageFormModel?

    var body: some View {
        NavigationStack {
            Group {
                if let form {
                    LinkBrokerageForm(form: form, onSubmitted: finish, onCancel: { dismiss() })
                } else {
                    Color.clear
                }
            }
            .navigationTitle(editAccount == nil ? "Link Brokerage Account" : "Edit Brokerage Account")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        dismiss()
                    } label: {
                        Image(systemName: Symbol.named("close"))
                    }
                    .disabled(form?.submitting ?? false)
                    .accessibilityLabel("Close")
                }
            }
        }
        .presentationDragIndicator(.visible)
        .interactiveDismissDisabled(form?.submitting ?? false)
        .onAppear {
            if form == nil {
                let services = services
                form = LinkBrokerageFormModel(editAccount: editAccount, repository: { services.brokerageRepository })
            }
        }
    }

    /// After a save: refresh the list, hold the success line for 1.2 s, close.
    private func finish() async {
        await onSaved()
        try? await Task.sleep(for: .milliseconds(1200))
        dismiss()
    }
}

private struct LinkBrokerageForm: View {
    @Bindable var form: LinkBrokerageFormModel
    let onSubmitted: () async -> Void
    let onCancel: () -> Void

    var body: some View {
        Form {
            if !form.isEditing {
                Section {
                    Picker("Brokerage", selection: $form.tab) {
                        Text("Alpaca").tag(LinkBrokerageFormModel.Tab.alpaca)
                        Text("Binance.US").tag(LinkBrokerageFormModel.Tab.binanceus)
                    }
                    .pickerStyle(.segmented)
                    .listRowBackground(Color.clear)
                    .listRowInsets(EdgeInsets())
                    .disabled(form.submitting)
                }
            }

            switch form.isEditing ? form.editForm : form.tab {
            case .alpaca: alpaca
            case .binanceus: binance
            }
        }
    }

    // MARK: Alpaca

    @ViewBuilder
    private var alpaca: some View {
        Section {
            BrokerageFieldRow(label: "Account Name", placeholder: "e.g. My Paper Trading", text: $form.alpacaName)
            BrokerageFieldRow(label: "API Key ID", placeholder: "PKXXXXXXXXXXXXXXXXXXXXXXXX", text: $form.alpacaKey, mono: true)
            BrokerageFieldRow(
                label: "Secret Key" + (form.isEditing ? " (leave blank to keep existing)" : ""),
                placeholder: form.isEditing ? "Leave blank to keep existing" : "●●●●●●●●●●●●●●●●●●●●●●●●",
                text: $form.alpacaSecret,
                obscure: true,
                mono: true
            )
            Toggle(isOn: $form.alpacaPaper) {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Paper trading")
                    Text(verbatim: form.alpacaPaper ? "(paper-api.alpaca.markets)" : "(api.alpaca.markets)")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
            }
        }

        Section {
            Picker("Market Data Feed", selection: $form.alpacaFeed) {
                ForEach(LinkBrokerageFormModel.feeds, id: \.value) { feed in
                    Text(feed.label).tag(feed.value)
                }
            }
            .pickerStyle(.navigationLink)
        } footer: {
            Text("Paper accounts have free IEX. Live accounts need a subscription for IEX or SIP.")
        }

        if form.showTestPanel {
            Section {
                AlpacaTestPanel(form: form)
            }
        }

        Section {
            if let message = form.submitMsg, !message.isEmpty {
                BrokerageStatusRow(message: message, ok: form.submitOk)
            }
            HStack(spacing: 10) {
                Button {
                    Task { await form.runAlpacaTest() }
                } label: {
                    HStack(spacing: 6) {
                        if form.testRunning {
                            ProgressView()
                        } else {
                            Image(systemName: Symbol.named("network_check"))
                        }
                        Text(form.testRunning ? "Testing…" : "Test")
                    }
                    .frame(maxWidth: .infinity)
                }
                .buttonStyle(.bordered)
                .controlSize(.large)
                .disabled(form.submitting || form.testRunning)

                Button {
                    Task {
                        if await form.submitAlpaca() { await onSubmitted() }
                    }
                } label: {
                    HStack(spacing: 6) {
                        if form.submitting {
                            ProgressView().tint(DS.Palette.onAccent)
                        }
                        Text(form.isEditing ? "Save Changes" : "Link Account")
                    }
                    .frame(maxWidth: .infinity)
                }
                .dsProminentButton()
                .controlSize(.large)
                .layoutPriority(1)
                .disabled(form.locked)
            }
            .listRowBackground(Color.clear)
            .listRowInsets(EdgeInsets())

            if form.showSaveAnyway {
                Button {
                    Task {
                        if await form.submitAlpaca(bypassTest: true) { await onSubmitted() }
                    }
                } label: {
                    Text("Save Anyway")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.bordered)
                .controlSize(.large)
                .tint(DS.Palette.warning)
                .disabled(form.locked)
                .listRowBackground(Color.clear)
                .listRowInsets(EdgeInsets(top: 8, leading: 0, bottom: 0, trailing: 0))
            }
        }
    }

    // MARK: Binance.US

    @ViewBuilder
    private var binance: some View {
        Section {
            BrokerageInfoBox(color: DS.Palette.accent) {
                Text("Spot fees: \(Text("0.00% maker / 0.02% taker").fontWeight(.bold)) — ~12× cheaper than Alpaca crypto (0.25%), which is what makes high-frequency strategies viable. Create a read+trade API key at binance.us (no withdrawal permission needed).")
            }
            .listRowBackground(Color.clear)
            .listRowInsets(EdgeInsets())
        }

        Section {
            BrokerageFieldRow(label: "Account Name", placeholder: "e.g. Binance.US Paper", text: $form.binanceName)
            BrokerageFieldRow(label: "API Key", placeholder: "Binance.US API key", text: $form.binanceKey, mono: true)
            BrokerageFieldRow(
                label: "Secret Key" + (form.isEditing ? " (leave blank to keep existing)" : ""),
                placeholder: form.isEditing ? "Leave blank to keep existing" : "●●●●●●●●●●●●●●●●●●●●●●●●",
                text: $form.binanceSecret,
                obscure: true,
                mono: true
            )
            Toggle(isOn: $form.binancePaper) {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Paper trading")
                    Text(form.binancePaper ? "(simulated fills vs. live price)" : "(live signed orders · real money)")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
            }
        }

        if !form.binancePaper {
            Section {
                BrokerageInfoBox(color: DS.Palette.warning) {
                    Text("⚠ Live account — instances bound here place real Binance.US MARKET orders with real funds.")
                }
                .listRowBackground(Color.clear)
                .listRowInsets(EdgeInsets())
            }
        }

        Section {
            if let message = form.submitMsg, !message.isEmpty {
                BrokerageStatusRow(message: message, ok: form.submitOk)
            }
            HStack(spacing: 10) {
                Button(action: onCancel) {
                    Text("Cancel").frame(maxWidth: .infinity)
                }
                .buttonStyle(.bordered)
                .controlSize(.large)
                .disabled(form.submitting)

                Button {
                    Task {
                        if await form.submitBinanceus() { await onSubmitted() }
                    }
                } label: {
                    HStack(spacing: 6) {
                        if form.submitting {
                            ProgressView().tint(DS.Palette.onAccent)
                        }
                        Text(form.isEditing ? "Save Changes" : "Link Account")
                    }
                    .frame(maxWidth: .infinity)
                }
                .dsProminentButton()
                .controlSize(.large)
                .layoutPriority(1)
                .disabled(form.locked)
            }
            .listRowBackground(Color.clear)
            .listRowInsets(EdgeInsets())
        }
    }
}

/// The Alpaca probe results — `_buildTestPanel`.
private struct AlpacaTestPanel: View {
    let form: LinkBrokerageFormModel

    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        if form.testRunning {
            HStack(spacing: 10) {
                ProgressView()
                Text("Running 5-endpoint probe against Alpaca…")
                    .font(.footnote)
                    .foregroundStyle(DS.Palette.onTint(DS.Palette.info, in: colorScheme))
            }
        } else if let result = form.testResult {
            let summary = LinkBrokerageFormModel.TestSummary(result)
            let tone = summary.total == 0 ? DS.Palette.warning : (summary.ok ? DS.Palette.success : DS.Palette.danger)
            let ink = DS.Palette.onTint(tone, in: colorScheme)
            VStack(alignment: .leading, spacing: 10) {
                HStack(spacing: 8) {
                    Image(systemName: Symbol.named(summary.total == 0 ? "info_outline" : (summary.ok ? "check_circle_outline" : "error_outline")))
                        .foregroundStyle(tone)
                    Text(summary.headline)
                        .font(.footnote.weight(.semibold))
                        .foregroundStyle(ink)
                        .frame(maxWidth: .infinity, alignment: .leading)
                    Button {
                        form.showTestPanel = false
                    } label: {
                        Image(systemName: Symbol.named("close"))
                            .foregroundStyle(.secondary)
                            .frame(width: 30, height: 30)
                    }
                    .buttonStyle(.borderless)
                    .accessibilityLabel("Hide test results")
                }

                ForEach(Array(summary.tests.enumerated()), id: \.offset) { _, test in
                    let ok = test["ok"].bool
                    let color = ok ? DS.Palette.success : DS.Palette.danger
                    HStack(alignment: .top, spacing: 8) {
                        Image(systemName: Symbol.named(ok ? "check_circle_outline" : "cancel"))
                            .font(.footnote)
                            .foregroundStyle(color)
                        VStack(alignment: .leading, spacing: 2) {
                            HStack {
                                Text(verbatim: test["name"].string ?? "")
                                    .font(.system(.caption, design: .monospaced))
                                Spacer()
                                if !test["status"].isNull {
                                    Text("HTTP \(test["status"].dartDescription)")
                                        .font(.caption2)
                                        .foregroundStyle(DS.Palette.onTint(color, in: colorScheme))
                                }
                            }
                            if let message = test["message"].string, !message.isEmpty {
                                Text(message)
                                    .font(.caption2)
                                    .foregroundStyle(ok ? AnyShapeStyle(.secondary) : AnyShapeStyle(DS.Palette.danger))
                            }
                        }
                    }
                    .padding(.horizontal, 10)
                    .padding(.vertical, 8)
                    .background(color.opacity(DS.tintFill), in: .rect(cornerRadius: DS.Radius.small, style: .continuous))
                }

                if !summary.hints.isEmpty {
                    VStack(alignment: .leading, spacing: 2) {
                        Label("Hints", systemImage: Symbol.named("lightbulb"))
                            .font(.caption2.weight(.semibold))
                        ForEach(Array(summary.hints.enumerated()), id: \.offset) { _, hint in
                            Text(hint).font(.caption2)
                        }
                    }
                    .foregroundStyle(DS.Palette.onTint(DS.Palette.warning, in: colorScheme))
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(10)
                    .background(DS.Palette.warning.opacity(DS.tintFill), in: .rect(cornerRadius: DS.Radius.small, style: .continuous))
                }
            }
        }
    }
}

/// A labelled credential field — `_field`.
private struct BrokerageFieldRow: View {
    let label: String
    let placeholder: String
    @Binding var text: String
    var obscure = false
    var mono = false

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(label)
                .font(.footnote)
                .foregroundStyle(.secondary)
            Group {
                if obscure {
                    SecureField(label, text: $text, prompt: Text(placeholder))
                } else {
                    TextField(label, text: $text, prompt: Text(placeholder))
                }
            }
            .font(mono ? .system(.body, design: .monospaced) : .body)
            .textContentType(obscure ? .password : nil)
            .textInputAutocapitalization(.never)
            .autocorrectionDisabled()
        }
        .padding(.vertical, 2)
    }
}

/// The shared status line — `_buildStatusMsg`.
private struct BrokerageStatusRow: View {
    let message: String
    let ok: Bool

    var body: some View {
        BrokerageInfoBox(color: ok ? DS.Palette.success : DS.Palette.danger) {
            Text(message)
        }
        .listRowBackground(Color.clear)
        .listRowInsets(EdgeInsets(top: 0, leading: 0, bottom: 10, trailing: 0))
    }
}

/// A tinted note — `_infoBox`.
private struct BrokerageInfoBox<Content: View>: View {
    let color: Color
    @ViewBuilder let content: Content

    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        content
            .font(.footnote)
            .foregroundStyle(DS.Palette.onTint(color, in: colorScheme))
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 12)
            .padding(.vertical, 10)
            .background(color.opacity(DS.tintFill), in: .rect(cornerRadius: DS.Radius.small, style: .continuous))
    }
}
