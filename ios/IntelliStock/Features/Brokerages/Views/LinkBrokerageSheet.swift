import SwiftUI

/// Link or edit a brokerage account — `_LinkBrokerageSheet` in
/// `link_brokerage_sheet.dart`. Native form: a sheet with an inset-grouped
/// form; the Alpaca / Binance.US tab bar is a segmented control (create mode
/// only), credentials use secure fields. Close is the leading toolbar item and
/// Link Account / Save Changes the trailing one; Test and Save Anyway are rows.
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
                    LinkBrokerageForm(form: form, onSubmitted: finish)
                } else {
                    Color.clear
                }
            }
            .navigationTitle(editAccount == nil ? "Link Brokerage Account" : "Edit Brokerage Account")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                // The Dart's close X; the Binance form's Cancel did the same
                // (dismiss, held while a save runs), so it folds in here.
                ToolbarItem(placement: .cancellationAction) {
                    Button {
                        dismiss()
                    } label: {
                        Image(systemName: Symbol.named("close"))
                    }
                    .disabled(form?.submitting ?? false)
                    .accessibilityLabel("Close")
                }
                ToolbarItem(placement: .confirmationAction) {
                    if let form {
                        LinkBrokerageSubmitButton(form: form, onSubmitted: finish)
                    }
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

/// Link Account / Save Changes for the form on screen (the edit form, else
/// the selected tab), with the double-submit lock.
private struct LinkBrokerageSubmitButton: View {
    let form: LinkBrokerageFormModel
    let onSubmitted: () async -> Void

    var body: some View {
        // The iOS 26 sheet idiom: an X leading, a checkmark trailing. The
        // label (Link Account / Save Changes) is what VoiceOver reads; as text
        // it pushed the title into truncation.
        Button(role: .confirm) {
            Task {
                let saved: Bool
                switch form.isEditing ? form.editForm : form.tab {
                case .alpaca: saved = await form.submitAlpaca()
                case .binanceus: saved = await form.submitBinanceus()
                }
                if saved { await onSubmitted() }
            }
        } label: {
            if form.submitting {
                ProgressView()
            } else {
                Label(form.isEditing ? "Save Changes" : "Link Account", systemImage: "checkmark")
            }
        }
        .disabled(form.locked)
        .accessibilityLabel(form.isEditing ? "Save Changes" : "Link Account")
    }
}

private struct LinkBrokerageForm: View {
    @Bindable var form: LinkBrokerageFormModel
    let onSubmitted: () async -> Void

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

            // The save's result line, at the top where the toolbar's submit
            // can be seen to answer.
            if let message = form.submitMsg, !message.isEmpty {
                Section {
                    BrokerageNoteRow(message, color: form.submitOk ? DS.Palette.success : DS.Palette.danger)
                }
            }

            if let account = form.editAccount {
                BrokerageAccountInfoSection(account: account)
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

        Section {
            InlineActionRow(form.testRunning ? "Testing…" : "Test",
                            systemImage: Symbol.named("network_check"),
                            isBusy: form.testRunning) {
                Task { await form.runAlpacaTest() }
            }
            .disabled(form.submitting)

            if form.showSaveAnyway {
                InlineActionRow("Save Anyway", systemImage: Symbol.named("warning")) {
                    Task {
                        if await form.submitAlpaca(bypassTest: true) { await onSubmitted() }
                    }
                }
                .tint(DS.Palette.warning)
                .disabled(form.locked)
            }
        }

        if form.showTestPanel {
            AlpacaTestPanel(form: form)
        }
    }

    // MARK: Binance.US

    @ViewBuilder
    private var binance: some View {
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
        } footer: {
            Text("Spot fees: \(Text("0.00% maker / 0.02% taker").fontWeight(.bold)) — ~12× cheaper than Alpaca crypto (0.25%), which is what makes high-frequency strategies viable. Create a read+trade API key at binance.us (no withdrawal permission needed).")
        }

        if !form.binancePaper {
            Section {
                BrokerageNoteRow("⚠ Live account — instances bound here place real Binance.US MARKET orders with real funds.",
                                 color: DS.Palette.warning)
            }
        }
    }
}

/// Edit mode: the account's read-only details (the list row shows only the
/// number) — the old card's Account # and Last refreshed lines.
private struct BrokerageAccountInfoSection: View {
    let account: Brokerage

    var body: some View {
        if account.accountNumber != nil || account.lastRefreshAt != nil || account.lastError != nil {
            Section("Account") {
                if let number = account.accountNumber {
                    LabeledContent("Account #") { Text(verbatim: number) }
                }
                if let refreshed = account.lastRefreshAt {
                    LabeledContent("Last refreshed", value: BrokeragesModel.refreshedLabel(refreshed))
                }
                if let error = account.lastError {
                    Text("\(Text("Error: ").fontWeight(.semibold))\(error)")
                        .font(.footnote)
                        .foregroundStyle(DS.Palette.danger)
                }
            }
        }
    }
}

/// The Alpaca probe results — `_buildTestPanel`, as a form section: the
/// headline (with Hide) in the header, one row per endpoint, then the hints.
private struct AlpacaTestPanel: View {
    let form: LinkBrokerageFormModel

    var body: some View {
        if form.testRunning {
            Section {
                HStack(spacing: 10) {
                    ProgressView()
                    Text("Running 5-endpoint probe against Alpaca…")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
            }
        } else if let result = form.testResult {
            let summary = LinkBrokerageFormModel.TestSummary(result)
            let tone = summary.total == 0 ? DS.Palette.warning : (summary.ok ? DS.Palette.success : DS.Palette.danger)
            Section {
                ForEach(Array(summary.tests.enumerated()), id: \.offset) { _, test in
                    let ok = test["ok"].bool
                    let color = ok ? DS.Palette.success : DS.Palette.danger
                    HStack(alignment: .firstTextBaseline, spacing: 10) {
                        Image(systemName: Symbol.named(ok ? "check_circle_outline" : "cancel"))
                            .foregroundStyle(color)
                        VStack(alignment: .leading, spacing: 2) {
                            HStack(alignment: .firstTextBaseline) {
                                Text(verbatim: test["name"].string ?? "")
                                    .font(.system(.subheadline, design: .monospaced))
                                Spacer()
                                if !test["status"].isNull {
                                    Text("HTTP \(test["status"].dartDescription)")
                                        .font(.footnote.monospacedDigit())
                                        .foregroundStyle(.secondary)
                                }
                            }
                            if let message = test["message"].string, !message.isEmpty {
                                Text(message)
                                    .font(.footnote)
                                    .foregroundStyle(ok ? AnyShapeStyle(.secondary) : AnyShapeStyle(DS.Palette.danger))
                            }
                        }
                    }
                    .accessibilityElement(children: .combine)
                }

                if !summary.hints.isEmpty {
                    VStack(alignment: .leading, spacing: 4) {
                        Label("Hints", systemImage: Symbol.named("lightbulb"))
                            .font(.footnote.weight(.semibold))
                            .foregroundStyle(DS.Palette.warning)
                        ForEach(Array(summary.hints.enumerated()), id: \.offset) { _, hint in
                            Text(hint).font(.footnote)
                        }
                    }
                }
            } header: {
                HStack(spacing: 6) {
                    Image(systemName: Symbol.named(summary.total == 0 ? "info_outline" : (summary.ok ? "check_circle_outline" : "error_outline")))
                        .foregroundStyle(tone)
                    Text(summary.headline)
                    Spacer()
                    Button("Hide") { form.showTestPanel = false }
                        .accessibilityLabel("Hide test results")
                }
                .textCase(nil)
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

/// A status or warning line as a plain form row: a coloured glyph and the
/// text — `_buildStatusMsg` and `_infoBox`, without the tinted box.
private struct BrokerageNoteRow: View {
    let message: String
    let color: Color

    init(_ message: String, color: Color) {
        self.message = message
        self.color = color
    }

    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        let symbol = color == DS.Palette.success ? "checkmark.circle.fill"
            : (color == DS.Palette.danger ? "exclamationmark.circle.fill" : "exclamationmark.triangle.fill")
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            Image(systemName: symbol)
                .foregroundStyle(color)
                .accessibilityHidden(true)
            Text(message)
                .font(.subheadline)
                .foregroundStyle(DS.Palette.onTint(color, in: colorScheme))
                .frame(maxWidth: .infinity, alignment: .leading)
        }
        .accessibilityElement(children: .combine)
    }
}
