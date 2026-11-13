import SwiftUI

/// Link or edit a brokerage account — `_LinkBrokerageSheet` in
/// `link_brokerage_sheet.dart`, shaped like the web's modal: the Alpaca /
/// Binance.US switch (create mode only), the credential fields with their
/// labels above, the save's result, then Cancel, Test (Alpaca) and Link
/// account / Save changes in the footer. Save anyway appears after a failed
/// probe.
struct LinkBrokerageSheet: View {
    let editAccount: Brokerage?
    /// Reloads the account list after a save.
    let onSaved: () async -> Void

    @Environment(AppServices.self) private var services
    @Environment(\.dismiss) private var dismiss
    @State private var form: LinkBrokerageFormModel?

    var body: some View {
        Group {
            if let form {
                LinkBrokerageForm(form: form, onSubmitted: finish, onClose: { dismiss() })
            } else {
                DS.Surface.card
            }
        }
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
    let onClose: () -> Void

    private var shownTab: LinkBrokerageFormModel.Tab { form.isEditing ? form.editForm : form.tab }

    var body: some View {
        FormSheet(
            form.isEditing ? "Edit brokerage account" : "Link brokerage account",
            closeDisabled: form.submitting,
            onClose: onClose
        ) {
            if !form.isEditing {
                DSSegmentedControl(
                    "Brokerage",
                    selection: $form.tab,
                    options: LinkBrokerageFormModel.Tab.allCases,
                    fullWidth: true,
                    segmentHeight: 32
                ) { $0 == .alpaca ? "Alpaca" : "Binance.US" }
                .disabled(form.submitting)
            }

            if let account = form.editAccount {
                BrokerageAccountInfo(account: account)
            }

            switch shownTab {
            case .alpaca: alpaca
            case .binanceus: binance
            }

            // The save's result, beside the footer's buttons.
            if let message = form.submitMsg, !message.isEmpty {
                FormMessage(message, tone: form.submitOk ? .ok : .error)
            }
            if shownTab == .alpaca, form.showSaveAnyway {
                Button("Save anyway") {
                    Task {
                        if await form.submitAlpaca(bypassTest: true) { await onSubmitted() }
                    }
                }
                .buttonStyle(.dsSecondary)
                .disabled(form.locked)
            }
            if shownTab == .alpaca, form.showTestPanel {
                AlpacaTestPanel(form: form)
            }
        } footer: {
            FormSheetActions(
                primary: form.isEditing ? "Save changes" : "Link account",
                busyLabel: form.isEditing ? "Saving…" : "Linking…",
                busy: form.submitting,
                primaryDisabled: form.locked,
                cancelDisabled: form.submitting,
                secondary: shownTab == .alpaca ? (form.testRunning ? "Testing…" : "Test") : nil,
                secondaryBusy: form.testRunning,
                secondaryDisabled: form.submitting || form.testRunning,
                onSecondary: { Task { await form.runAlpacaTest() } },
                onPrimary: submit,
                onCancel: onClose
            )
        }
    }

    /// Link account / Save changes for the form on screen, with the
    /// double-submit lock.
    private func submit() {
        Task {
            let saved: Bool
            switch shownTab {
            case .alpaca: saved = await form.submitAlpaca()
            case .binanceus: saved = await form.submitBinanceus()
            }
            if saved { await onSubmitted() }
        }
    }

    // MARK: Alpaca

    @ViewBuilder
    private var alpaca: some View {
        DSField("Account name") {
            FormTextField("e.g. My paper trading", text: $form.alpacaName)
        }
        DSField("API key ID") {
            FormTextField("PKXXXXXXXXXXXXXXXXXXXXXXXX", text: $form.alpacaKey, mono: true)
        }
        secretField(text: $form.alpacaSecret)
        FormToggle(
            "Paper trading",
            note: form.alpacaPaper ? "paper-api.alpaca.markets" : "api.alpaca.markets",
            isOn: $form.alpacaPaper
        )
        DSField("Market data feed", help: "Paper accounts have free IEX. Live accounts need a subscription for IEX or SIP.") {
            FormSelect(
                "Market data feed",
                display: LinkBrokerageFormModel.feeds.first { $0.value == form.alpacaFeed }?.label ?? form.alpacaFeed,
                selection: $form.alpacaFeed
            ) {
                ForEach(LinkBrokerageFormModel.feeds, id: \.value) { feed in
                    Text(feed.label).tag(feed.value)
                }
            }
        }
    }

    private func secretField(text: Binding<String>) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 4) {
                Text("Secret key")
                    .foregroundStyle(DS.Text.label)
                if form.isEditing {
                    Text("(leave blank to keep existing)")
                        .foregroundStyle(DS.Text.muted)
                }
            }
            .font(DS.Font.body)
            FormTextField(
                form.isEditing ? "Leave blank to keep existing" : "••••••••••••••••••••••••",
                text: text,
                mono: true,
                secure: true
            )
        }
    }

    // MARK: Binance.US

    @ViewBuilder
    private var binance: some View {
        // The fee note leads, as on the web.
        Text("Spot fees: \(Text("0.00% maker / 0.02% taker").foregroundStyle(DS.Text.soft)) — ~12× cheaper than Alpaca crypto (0.25%), which is what makes high-frequency strategies viable. Create a read+trade API key at binance.us (no withdrawal permission needed).")
            .font(DS.Font.caption)
            .foregroundStyle(DS.Text.secondary)
            .monospacedDigit()
            .fixedSize(horizontal: false, vertical: true)
            .padding(.horizontal, 16)
            .padding(.vertical, 12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .overlay {
                RoundedRectangle(cornerRadius: DS.Radius.control, style: .continuous)
                    .strokeBorder(DS.Line.card, lineWidth: DS.Line.width)
            }
        DSField("Account name") {
            FormTextField("e.g. Binance.US paper", text: $form.binanceName)
        }
        DSField("API key") {
            FormTextField("Binance.US API key", text: $form.binanceKey, mono: true)
        }
        secretField(text: $form.binanceSecret)
        FormToggle(
            "Paper trading",
            note: form.binancePaper ? "Simulated fills vs. live price" : "Live signed orders · real money",
            isOn: $form.binancePaper
        )
        if !form.binancePaper {
            FormNote("Live account — instances bound here place real Binance.US market orders with real funds.")
        }
    }
}

/// Edit mode: the account's read-only details (the list row shows only the
/// number) — the old card's Account # and Last refreshed lines.
private struct BrokerageAccountInfo: View {
    let account: Brokerage

    var body: some View {
        if account.accountNumber != nil || account.lastRefreshAt != nil || account.lastError != nil {
            VStack(alignment: .leading, spacing: 10) {
                if let number = account.accountNumber {
                    DSKeyValueRow("Account number", number)
                }
                if let refreshed = account.lastRefreshAt {
                    DSKeyValueRow("Last refreshed", BrokeragesModel.refreshedLabel(refreshed))
                }
                if let error = account.lastError {
                    Text("Error: \(error)")
                        .font(DS.Font.caption)
                        .foregroundStyle(DS.Palette.loss)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 12)
            .overlay {
                RoundedRectangle(cornerRadius: DS.Radius.control, style: .continuous)
                    .strokeBorder(DS.Line.card, lineWidth: DS.Line.width)
            }
        }
    }
}

/// The Alpaca probe results — `_buildTestPanel`, as the web's connection
/// test: the headline as a dot and a word (with Hide), one row per endpoint
/// in a bordered list, then the hints.
private struct AlpacaTestPanel: View {
    let form: LinkBrokerageFormModel

    var body: some View {
        if form.testRunning {
            HStack(spacing: 10) {
                ProgressView().controlSize(.small)
                Text("Running 5-endpoint probe against Alpaca…")
                    .font(DS.Font.body)
                    .foregroundStyle(DS.Text.secondary)
            }
        } else if let result = form.testResult {
            let summary = LinkBrokerageFormModel.TestSummary(result)
            let tone: DSStatusTone = summary.total == 0 ? .warn : (summary.ok ? .positive : .error)
            VStack(alignment: .leading, spacing: 12) {
                HStack(spacing: 8) {
                    DSStatusDot(tone)
                    Text(summary.headline.dsTrimmingTitlePeriod)
                        .font(DS.Font.body)
                        .foregroundStyle(summary.total == 0 ? DS.Text.soft : (summary.ok ? DS.Palette.gain : DS.Palette.loss))
                        .monospacedDigit()
                        .frame(maxWidth: .infinity, alignment: .leading)
                    Button("Hide") { form.showTestPanel = false }
                        .buttonStyle(.dsGhost)
                        .padding(.trailing, -12)
                        .accessibilityLabel("Hide test results")
                }

                if !summary.tests.isEmpty {
                    VStack(spacing: 0) {
                        ForEach(Array(summary.tests.enumerated()), id: \.offset) { index, test in
                            if index > 0 { DSDivider(row: true) }
                            testRow(test)
                        }
                    }
                    .overlay {
                        RoundedRectangle(cornerRadius: DS.Radius.control, style: .continuous)
                            .strokeBorder(DS.Line.card, lineWidth: DS.Line.width)
                    }
                }

                if !summary.hints.isEmpty {
                    VStack(alignment: .leading, spacing: 6) {
                        Text("Hints")
                            .font(DS.Font.captionMedium)
                            .foregroundStyle(DS.Text.label)
                        ForEach(Array(summary.hints.enumerated()), id: \.offset) { _, hint in
                            Text(hint)
                                .font(DS.Font.caption)
                                .foregroundStyle(DS.Text.secondary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                }
            }
        }
    }

    private func testRow(_ test: JSON) -> some View {
        let ok = test["ok"].bool
        return VStack(alignment: .leading, spacing: 2) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                DSStatusDot(ok ? .positive : .error)
                Text(verbatim: test["name"].string ?? "")
                    .font(DS.Font.mono)
                    .foregroundStyle(DS.Text.soft)
                Spacer(minLength: 8)
                if !test["status"].isNull {
                    Text("HTTP \(test["status"].dartDescription)")
                        .font(DS.Font.caption)
                        .foregroundStyle(DS.Text.muted)
                        .monospacedDigit()
                }
            }
            if let message = test["message"].string, !message.isEmpty {
                Text(message)
                    .font(DS.Font.caption)
                    .foregroundStyle(ok ? DS.Text.muted : DS.Palette.loss)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.leading, 20)
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 10)
        .accessibilityElement(children: .combine)
    }
}
