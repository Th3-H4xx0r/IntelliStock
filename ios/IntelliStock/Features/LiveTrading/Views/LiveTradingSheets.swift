import SwiftUI

/// The manual order sheet — `_ManualOrderSheet` in `manual_order_sheet.dart`,
/// styled as the web's manual order modal: a header ("Manual order", "Sent to
/// this instance's broker"), labelled fields, then Cancel and Submit order in
/// the footer. REAL money on alpaca-main: the validation is the guard, ported
/// exactly; a valid order then asks "Submit order?" with the order spelled
/// out, and only Submit sends it. Submit order stays inert while it is being
/// sent.
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
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    DSField("Symbol") {
                        DSTextField("AAPL", text: $order.form.symbol)
                            .textInputAutocapitalization(.characters)
                            .autocorrectionDisabled()
                    }
                    HStack(alignment: .top, spacing: 16) {
                        DSField("Side") {
                            DSSegmentedControl("Side", selection: $order.form.side, options: ["buy", "sell"], fullWidth: true, segmentHeight: 32) {
                                $0 == "buy" ? "Buy" : "Sell"
                            }
                        }
                        DSField("Order type") {
                            DSSegmentedControl(
                                "Order type",
                                selection: Binding(get: { order.form.orderType }, set: { order.form.setOrderType($0) }),
                                options: ["market", "limit"],
                                fullWidth: true,
                                segmentHeight: 32
                            ) { $0 == "market" ? "Market" : "Limit" }
                        }
                    }
                    VStack(alignment: .leading, spacing: 8) {
                        HStack(alignment: .top, spacing: 16) {
                            DSField("Qty (shares)") {
                                DSTextField("0", text: $order.form.qty)
                                    .keyboardType(.decimalPad)
                            }
                            DSField("Notional ($)") {
                                DSTextField("0.00", text: $order.form.notional)
                                    .keyboardType(.decimalPad)
                            }
                        }
                        Text("Fill qty or notional, not both")
                            .font(DS.Font.caption)
                            .foregroundStyle(DS.Text.muted)
                    }
                    if order.form.orderType == "limit" {
                        DSField("Limit price") {
                            DSTextField("0.00", text: $order.form.limitPrice)
                                .keyboardType(.decimalPad)
                        }
                    }
                    HStack(alignment: .bottom, spacing: 16) {
                        DSField("TIF") {
                            SelectInput(
                                label: "TIF",
                                selection: Binding(get: { order.form.tif }, set: { order.form.setTif($0) }),
                                valueText: liveTifOptions.first { $0.value == order.form.tif }?.label ?? order.form.tif
                            ) {
                                ForEach(liveTifOptions, id: \.value) { Text($0.label).tag($0.value) }
                            }
                        }
                        Toggle("Extended hours", isOn: $order.form.extendedHours)
                            .font(DS.Font.body)
                            .foregroundStyle(DS.Text.label)
                            .disabled(!order.form.extendedHoursAllowed)
                            .frame(minHeight: 36)
                    }
                    if let error {
                        ErrorRow(message: error)
                    }
                }
                .padding(.horizontal, 24)
                .padding(.bottom, 24)
            }
            .scrollDismissesKeyboard(.interactively)
            .background(DS.Surface.card)
            .safeAreaInset(edge: .top, spacing: 0) {
                LiveSheetHeader(title: "Manual order", subtitle: "Sent to this instance's broker") { dismiss() }
            }
            .safeAreaInset(edge: .bottom, spacing: 0) {
                LiveSheetFooter {
                    Button("Cancel") { dismiss() }
                        .buttonStyle(.dsGhost(size: .medium))
                    Button { order.requestSubmit() } label: {
                        if order.submitting {
                            ProgressView()
                                .controlSize(.small)
                                .tint(DS.Palette.onAccent)
                        } else {
                            Text("Submit order")
                        }
                    }
                    .buttonStyle(.dsPrimary)
                    .disabled(order.submitting)
                }
            }
            .toolbar(.hidden, for: .navigationBar)
            .navigationTitle("Manual order")
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
        .presentationBackground(DS.Surface.card)
    }

    /// Submit on the alert: the one place the order is sent.
    private func confirm() {
        Task {
            if await order.confirmSubmit() { dismiss() }
        }
    }
}

/// The halt confirmation — `_buildHaltModal`, styled as the web's halt
/// modal: what halting does, an optional reason, and `HALT` typed to enable
/// Halt now.
struct LiveHaltSheet: View {
    let model: LiveTradingModel

    @Environment(\.dismiss) private var dismiss
    @State private var reason = "risk breach"
    @State private var confirmed = false
    @State private var halting = false

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    DSField("Reason (optional)") {
                        DSTextField("e.g. risk breach", text: $reason)
                    }
                    TypedConfirmField(phrase: "HALT") { confirmed = $0 }
                }
                .padding(.horizontal, 24)
                .padding(.bottom, 24)
            }
            .scrollDismissesKeyboard(.interactively)
            .background(DS.Surface.card)
            .safeAreaInset(edge: .top, spacing: 0) {
                LiveSheetHeader(
                    title: "Halt live trading",
                    subtitle: "This cancels all open orders and stops the running broker. It does not liquidate open positions.",
                    closeDisabled: halting
                ) { dismiss() }
            }
            .safeAreaInset(edge: .bottom, spacing: 0) {
                LiveSheetFooter {
                    Button("Cancel") { dismiss() }
                        .buttonStyle(.dsGhost(size: .medium))
                        .disabled(halting)
                    Button(role: .destructive) {
                        halt()
                    } label: {
                        HStack(spacing: 8) {
                            if halting {
                                ProgressView()
                                    .controlSize(.small)
                            }
                            Text("Halt now")
                        }
                    }
                    .buttonStyle(.dsSecondary(size: .medium))
                    .disabled(!confirmed || halting)
                }
            }
            .toolbar(.hidden, for: .navigationBar)
            .navigationTitle("Halt live trading")
            .interactiveDismissDisabled(halting)
        }
        .presentationDetents([.medium, .large])
        .presentationDragIndicator(.visible)
        .presentationBackground(DS.Surface.card)
    }

    private func halt() {
        guard confirmed else { return }
        halting = true
        let payload: JSONObject = ["reason": .string(liveHaltReason(reason))]
        dismiss()
        Task { await model.runCommand("halt", payload) }
    }
}

/// A live sheet's head (`ui-modal-head`): the title in 16/500, a 14 pt
/// zinc-500 line under it, and a close button.
private struct LiveSheetHeader: View {
    let title: String
    let subtitle: String
    var closeDisabled = false
    let onClose: () -> Void

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            VStack(alignment: .leading, spacing: 4) {
                Text(title)
                    .font(DS.Font.sectionTitle)
                    .foregroundStyle(DS.Text.primary)
                    .accessibilityAddTraits(.isHeader)
                Text(subtitle)
                    .font(DS.Font.body)
                    .foregroundStyle(DS.Text.muted)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            Button(action: onClose) {
                Image(systemName: "xmark")
            }
            .buttonStyle(.dsIcon)
            .disabled(closeDisabled)
            .accessibilityLabel("Close")
        }
        .padding(.horizontal, 24)
        .padding(.top, 20)
        .padding(.bottom, 16)
        .background(DS.Surface.card)
    }
}

/// A live sheet's foot (`ui-modal-foot`): its buttons at the trailing edge
/// over a 6% divider.
private struct LiveSheetFooter<Content: View>: View {
    @ViewBuilder let content: Content

    var body: some View {
        HStack(spacing: 8) {
            Spacer()
            content
        }
        .padding(.horizontal, 24)
        .padding(.vertical, 16)
        .background {
            DS.Surface.card
                .overlay(alignment: .top) { DSDivider() }
                .ignoresSafeArea(edges: .bottom)
        }
    }
}
