import SwiftUI

/// The manual order sheet — `_ManualOrderSheet` in `manual_order_sheet.dart`.
/// REAL money on alpaca-main: the validation is the guard, ported exactly;
/// a valid order then asks "Submit order?" with the order spelled out, and
/// only Submit sends it. Submit Order stays disabled while it is being sent.
struct LiveManualOrderSheet: View {
    @Environment(\.dismiss) private var dismiss
    @State private var order: LiveManualOrderModel

    init(model: LiveTradingModel) {
        _order = State(initialValue: LiveManualOrderModel { payload in
            // runCommand reports its own failures on the command toast.
            await model.runCommand("submit_order", payload)
        })
    }

    var body: some View {
        @Bindable var order = order
        let error = order.error
        NavigationStack {
            Form {
                Section("Symbol") {
                    TextField("AAPL", text: $order.form.symbol)
                        .font(.body.monospaced())
                        .textInputAutocapitalization(.characters)
                        .autocorrectionDisabled()
                }
                Section {
                    Picker("Side", selection: $order.form.side) {
                        Text("Buy").tag("buy")
                        Text("Sell").tag("sell")
                    }
                    Picker("Order Type", selection: Binding(get: { order.form.orderType }, set: { order.form.setOrderType($0) })) {
                        Text("Market").tag("market")
                        Text("Limit").tag("limit")
                    }
                }
                Section {
                    LabeledContent("Qty (Shares)") {
                        TextField("0", text: $order.form.qty)
                            .keyboardType(.decimalPad)
                            .multilineTextAlignment(.trailing)
                            .font(.body.monospaced())
                    }
                    LabeledContent("Notional ($)") {
                        TextField("0.00", text: $order.form.notional)
                            .keyboardType(.decimalPad)
                            .multilineTextAlignment(.trailing)
                            .font(.body.monospaced())
                    }
                } footer: {
                    Text("Fill qty OR notional, not both.")
                }
                if order.form.orderType == "limit" {
                    Section("Limit Price") {
                        TextField("0.00", text: $order.form.limitPrice)
                            .keyboardType(.decimalPad)
                            .font(.body.monospaced())
                    }
                }
                Section {
                    Picker("TIF", selection: Binding(get: { order.form.tif }, set: { order.form.setTif($0) })) {
                        ForEach(liveTifOptions, id: \.value) { Text($0.label).tag($0.value) }
                    }
                    Toggle("Extended hours", isOn: $order.form.extendedHours)
                        .disabled(!order.form.extendedHoursAllowed)
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
                    if order.submitting {
                        ProgressView()
                    } else {
                        Button("Submit Order") { order.requestSubmit() }
                    }
                }
            }
            .interactiveDismissDisabled(order.submitting)
            .alert(
                "Submit order?",
                isPresented: Binding(
                    get: { order.confirmation != nil },
                    set: { if !$0 { order.cancelConfirmation() } }
                ),
                presenting: order.confirmation
            ) { _ in
                Button("Cancel", role: .cancel) { order.cancelConfirmation() }
                Button("Submit") { confirm() }
                    .disabled(order.submitting)
            } message: { pending in
                Text(pending.summary)
            }
        }
        .presentationDetents([.large])
        .presentationDragIndicator(.visible)
    }

    /// Submit on the alert: the one place the order is sent.
    private func confirm() {
        Task {
            if await order.confirmSubmit() { dismiss() }
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
