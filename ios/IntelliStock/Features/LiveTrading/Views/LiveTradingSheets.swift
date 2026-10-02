import SwiftUI

/// The manual order sheet — `_ManualOrderSheet` in `manual_order_sheet.dart`.
/// REAL money on alpaca-main: the validation is the guard, ported exactly,
/// and Submit Order stays disabled while the command is being sent.
struct LiveManualOrderSheet: View {
    let model: LiveTradingModel

    @Environment(\.dismiss) private var dismiss
    @State private var form = OrderForm()
    @State private var submitting = false
    @State private var error: String?

    var body: some View {
        NavigationStack {
            Form {
                Section("Symbol") {
                    TextField("AAPL", text: $form.symbol)
                        .font(.body.monospaced())
                        .textInputAutocapitalization(.characters)
                        .autocorrectionDisabled()
                }
                Section {
                    Picker("Side", selection: $form.side) {
                        Text("Buy").tag("buy")
                        Text("Sell").tag("sell")
                    }
                    Picker("Order Type", selection: Binding(get: { form.orderType }, set: { form.setOrderType($0) })) {
                        Text("Market").tag("market")
                        Text("Limit").tag("limit")
                    }
                }
                Section {
                    LabeledContent("Qty (Shares)") {
                        TextField("0", text: $form.qty)
                            .keyboardType(.decimalPad)
                            .multilineTextAlignment(.trailing)
                            .font(.body.monospaced())
                    }
                    LabeledContent("Notional ($)") {
                        TextField("0.00", text: $form.notional)
                            .keyboardType(.decimalPad)
                            .multilineTextAlignment(.trailing)
                            .font(.body.monospaced())
                    }
                } footer: {
                    Text("Fill qty OR notional, not both.")
                }
                if form.orderType == "limit" {
                    Section("Limit Price") {
                        TextField("0.00", text: $form.limitPrice)
                            .keyboardType(.decimalPad)
                            .font(.body.monospaced())
                    }
                }
                Section {
                    Picker("TIF", selection: Binding(get: { form.tif }, set: { form.setTif($0) })) {
                        ForEach(liveTifOptions, id: \.value) { Text($0.label).tag($0.value) }
                    }
                    Toggle("Extended hours", isOn: $form.extendedHours)
                        .disabled(!form.extendedHoursAllowed)
                }
                if let error {
                    Section {
                        ErrorRow(message: error)
                            .listRowInsets(EdgeInsets())
                            .listRowBackground(Color.clear)
                    }
                }
            }
            .navigationTitle("Manual Order")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    if submitting {
                        ProgressView()
                    } else {
                        Button("Submit Order", action: submit)
                    }
                }
            }
            .interactiveDismissDisabled(submitting)
        }
        .presentationDetents([.large])
        .presentationDragIndicator(.visible)
    }

    private func submit() {
        if let message = validateOrderForm(form) {
            error = message
            return
        }
        submitting = true
        error = nil
        let payload = buildOrderPayload(form)
        Task {
            // runCommand reports its own failures on the command toast.
            await model.runCommand("submit_order", payload)
            dismiss()
        }
    }
}

/// The halt confirmation — `_buildHaltModal`: a warning, an optional
/// reason, and `HALT` typed to enable Halt Now.
struct LiveHaltSheet: View {
    let model: LiveTradingModel

    @Environment(\.dismiss) private var dismiss
    @State private var reason = "risk breach"
    @State private var confirmed = false
    @State private var halting = false

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Label {
                        Text("This cancels all open orders and stops the running broker. It does NOT liquidate open positions.")
                            .font(.footnote)
                    } icon: {
                        Image(systemName: Symbol.named("warning"))
                    }
                    .foregroundStyle(DS.Palette.warning)
                }
                Section("Reason (Optional)") {
                    TextField("e.g. risk breach", text: $reason)
                }
                Section {
                    TypedConfirmField(phrase: "HALT") { confirmed = $0 }
                }
                Section {
                    Button(role: .destructive) {
                        halt()
                    } label: {
                        HStack {
                            Label("Halt Now", systemImage: Symbol.named("block"))
                            if halting {
                                Spacer()
                                ProgressView()
                            }
                        }
                    }
                    .disabled(!confirmed || halting)
                }
            }
            .navigationTitle("Halt Live Trading")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                        .disabled(halting)
                }
            }
            .interactiveDismissDisabled(halting)
        }
        .presentationDetents([.medium, .large])
        .presentationDragIndicator(.visible)
    }

    private func halt() {
        guard confirmed else { return }
        halting = true
        let payload: JSONObject = ["reason": .string(liveHaltReason(reason))]
        dismiss()
        Task { await model.runCommand("halt", payload) }
    }
}
