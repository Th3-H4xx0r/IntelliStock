import SwiftUI

/// What opens a ticket from the stock screen. The model is built once per
/// tap, so each ticket has one `client_order_id` however often the screen
/// redraws.
struct TradeTicketRequest: Identifiable {
    let id = UUID()
    let model: TradeTicketModel
}

/// The trade ticket in a sheet — the web's `TradeTicket` modal, field for
/// field: the header ("Sell AAPL", "Last $188.15", close), Buy / Sell, the
/// account with what is held there and Sell all, the amount in shares or
/// dollars, the order type, the time in force, then the estimate. The footer
/// has Cancel and Review order, which pushes the order review, where a swipe
/// up sends it. Nothing is sent from this screen.
struct TradeTicketSheet: View {
    /// After a submit that placed the order or could not confirm it: the
    /// account it went to.
    @Bindable var model: TradeTicketModel
    let onSubmitted: (String) -> Void

    @State private var reviewing: TradeReviewItem?
    @FocusState private var focus: Field?
    @Environment(\.dismiss) private var dismiss

    private enum Field { case amount, limit }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    sideControl
                    accountField
                    amountField
                    orderTypeField
                    timeInForceField
                    estimateBox
                    if let message = model.check.message {
                        ErrorRow(message: message)
                    }
                }
                .padding(.horizontal, 24)
                .padding(.bottom, 24)
            }
            .scrollDismissesKeyboard(.interactively)
            .background(DS.Surface.card)
            .safeAreaInset(edge: .top, spacing: 0) { header }
            .safeAreaInset(edge: .bottom, spacing: 0) { footer }
            .toolbar(.hidden, for: .navigationBar)
            .toolbar {
                ToolbarItemGroup(placement: .keyboard) {
                    Spacer()
                    Button("Done") { focus = nil }
                }
            }
            .navigationTitle("\(model.draft.side.label) \(stockDisplayTitle(model.symbol))")
            .navigationDestination(item: $reviewing) { item in
                TradeReviewView(
                    model: model,
                    item: item,
                    onSubmitted: onSubmitted,
                    onClose: { dismiss() }
                )
            }
            .task(id: model.accountId) { await model.loadHeld() }
            .task { await model.loadInstances() }
        }
        .presentationBackground(DS.Surface.card)
        .interactiveDismissDisabled(model.submitting || model.isPlaced)
    }

    // MARK: Header and footer (`ui-modal-head`, `ui-modal-foot`)

    private var header: some View {
        HStack(alignment: .top, spacing: 12) {
            VStack(alignment: .leading, spacing: 4) {
                Text("\(model.draft.side.label) \(stockDisplayTitle(model.symbol))")
                    .font(DS.Font.sectionTitle)
                    .foregroundStyle(DS.Text.primary)
                    .accessibilityAddTraits(.isHeader)
                Text(model.lastPrice.map { "Last \(TradeTicket.priceText(TradeNumber(text: "", value: $0, places: $0 >= 1 ? 2 : 4)))" } ?? "No recent price")
                    .font(DS.Font.body)
                    .foregroundStyle(DS.Text.muted)
                    .monospacedDigit()
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            Button { dismiss() } label: {
                Image(systemName: "xmark")
            }
            .buttonStyle(.dsIcon)
            .accessibilityLabel("Close")
        }
        .padding(.horizontal, 24)
        .padding(.top, 20)
        .padding(.bottom, 16)
        .background(DS.Surface.card)
    }

    private var footer: some View {
        HStack(spacing: 8) {
            Spacer()
            Button("Cancel") { dismiss() }
                .buttonStyle(.dsGhost(size: .medium))
            Button("Review order") {
                focus = nil
                reviewing = model.reviewItem
            }
            .buttonStyle(.dsPrimary)
            .disabled(model.reviewItem == nil)
        }
        .padding(.horizontal, 24)
        .padding(.vertical, 16)
        .background {
            DS.Surface.card
                .overlay(alignment: .top) { DSDivider() }
                .ignoresSafeArea(edges: .bottom)
        }
    }

    // MARK: Side

    private var sideControl: some View {
        DSSegmentedControl("Side", selection: $model.draft.side, options: TradeSide.allCases, fullWidth: true, segmentHeight: 32) { $0.label }
    }

    // MARK: Account

    private var accountField: some View {
        DSField("Account") {
            if model.accounts.count > 1 {
                Menu {
                    Picker("Account", selection: Binding(get: { model.accountId }, set: { model.selectAccount($0) })) {
                        ForEach(model.accounts) { a in
                            Text("\(DashboardFormat.accountName(a)) · \(TradeTicket.modeText(a))").tag(a.id)
                        }
                    }
                } label: {
                    accountLabel(chevron: true)
                }
                .accessibilityLabel("Account")
            } else {
                accountLabel(chevron: false)
            }
            heldLine
        }
    }

    /// The selected account as the web's select draws it.
    private func accountLabel(chevron: Bool) -> some View {
        HStack(spacing: 8) {
            Text(model.account.map { "\(DashboardFormat.accountName($0)) · \(TradeTicket.modeText($0))" } ?? "—")
                .foregroundStyle(DS.Text.soft)
                .lineLimit(1)
                .frame(maxWidth: .infinity, alignment: .leading)
            if chevron {
                Image(systemName: "chevron.up.chevron.down")
                    .font(.system(size: 12))
                    .foregroundStyle(DS.Text.muted)
            }
        }
        .contentShape(Rectangle())
        .dsInput()
    }

    /// "You hold 4.25 sh" with Sell all, or its loading and failure states
    /// (12 pt, under the account, as the web draws it).
    @ViewBuilder
    private var heldLine: some View {
        switch model.heldState {
        case .none, .some(.loading):
            Text("Checking your position…")
                .font(DS.Font.caption)
                .foregroundStyle(DS.Text.muted)
        case .some(.failed(let error)):
            HStack(spacing: 8) {
                Text("Couldn't check what you hold. \((error as? ApiError)?.message ?? error.localizedDescription)")
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Palette.loss)
                    .frame(maxWidth: .infinity, alignment: .leading)
                Button("Retry") { Task { await model.loadHeld(force: true) } }
                    .buttonStyle(.dsSecondary)
            }
        case .some(.loaded(let qty)):
            HStack(spacing: 12) {
                Text(TradeTicket.heldText(qty, symbol: stockDisplayTitle(model.symbol)))
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Text.muted)
                    .monospacedDigit()
                    .frame(maxWidth: .infinity, alignment: .leading)
                if model.draft.side == .sell, qty > 0 {
                    Button {
                        focus = nil
                        model.sellAll()
                    } label: {
                        Text("Sell all")
                            .font(DS.Font.caption)
                            .foregroundStyle(DS.Text.label)
                            .underline(color: Color.white.opacity(0.2))
                    }
                    .buttonStyle(.plain)
                    .accessibilityHint("Fills in all \(TradeTicket.exactQtyText(qty)) shares")
                }
            }
        }
    }

    // MARK: Amount

    private var amountField: some View {
        let shares = model.draft.amountKind == .shares
        return DSField(shares ? "Shares" : "Amount") {
            DSSegmentedControl(
                "Order size in",
                selection: Binding(get: { model.draft.amountKind }, set: { model.setAmountKind($0) }),
                options: TradeAmountKind.allCases
            ) { $0.label }
        } content: {
            TextField(text: $model.draft.amountText, prompt: Text(shares ? "0" : "0.00").foregroundStyle(DS.Text.faint)) {
                Text(shares ? "Shares" : "Amount")
            }
            .keyboardType(.decimalPad)
            .monospacedDigit()
            .focused($focus, equals: .amount)
            .dsInput(focused: focus == .amount, prefix: shares ? nil : "$")
        }
    }

    // MARK: Order type

    private var orderTypeField: some View {
        let dollars = model.draft.amountKind == .dollars
        return DSField("Order type", help: dollars ? "Dollar amounts are market orders for the day" : nil) {
            VStack(alignment: .leading, spacing: 12) {
                DSSegmentedControl(
                    "Order type",
                    selection: Binding(get: { model.draft.effectiveOrderType }, set: { model.draft.orderType = $0 }),
                    options: TradeOrderType.allCases,
                    fullWidth: true,
                    isDisabled: { $0 == .limit && dollars }
                ) { $0.label }
                if !dollars, model.draft.orderType == .limit {
                    TextField(text: $model.draft.limitText, prompt: Text("Limit price").foregroundStyle(DS.Text.faint)) {
                        Text("Limit price")
                    }
                    .keyboardType(.decimalPad)
                    .monospacedDigit()
                    .focused($focus, equals: .limit)
                    .dsInput(focused: focus == .limit, prefix: "$")
                }
            }
        }
    }

    // MARK: Time in force

    private var timeInForceField: some View {
        let d = model.draft
        let help: String? = if d.requiresDay, d.amountKind == .shares {
            "Fractional shares are day orders only"
        } else if !d.requiresDay, d.timeInForce == .gtc {
            "Stays open until it fills or you cancel it"
        } else {
            nil
        }
        return DSField("Time in force", help: help) {
            DSSegmentedControl(
                "Time in force",
                selection: Binding(get: { model.draft.effectiveTimeInForce }, set: { model.draft.timeInForce = $0 }),
                options: TradeTimeInForce.allCases,
                fullWidth: true,
                isDisabled: { $0 == .gtc && d.requiresDay }
            ) { $0.label }
        }
    }

    /// The one caveat the order's settings call for.
    static func orderNote(_ d: TradeDraft) -> String? {
        if d.amountKind == .dollars { return "A dollar amount is a market order for the day." }
        if d.requiresDay { return "Fractional shares are day orders." }
        if d.orderType == .limit { return "A limit order fills only at your price or better, and may not fill." }
        if d.timeInForce == .gtc { return "Stays open until it fills or you cancel it." }
        return nil
    }

    // MARK: Estimate

    /// The web's estimate box: the label and figure, and where the price
    /// came from.
    private var estimateBox: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(alignment: .firstTextBaseline, spacing: 16) {
                Text(TradeTicket.estimateLabel(model.draft.side))
                    .font(DS.Font.body)
                    .foregroundStyle(DS.Text.secondary)
                Spacer()
                Text(model.estimate.map { fmtMoney($0) } ?? "—")
                    .font(DS.Font.baseMedium)
                    .foregroundStyle(DS.Text.primary)
                    .monospacedDigit()
            }
            Text(TradeTicketSheet.basis(model.draft, lastPrice: model.lastPrice))
                .font(DS.Font.caption)
                .foregroundStyle(DS.Text.muted)
                .monospacedDigit()
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 12)
        .overlay {
            RoundedRectangle(cornerRadius: DS.Radius.control, style: .continuous)
                .strokeBorder(DS.Line.card, lineWidth: DS.Line.width)
        }
        .accessibilityElement(children: .combine)
    }

    /// Where the estimate's price came from (the web's `estimateBasis`).
    static func basis(_ d: TradeDraft, lastPrice: Double?) -> String {
        if d.amountKind == .dollars { return "The dollar amount you enter" }
        if d.effectiveOrderType == .limit {
            guard let limit = d.limit, limit.value > 0 else { return "No limit price yet" }
            return "At your limit of \(TradeTicket.priceText(limit))"
        }
        guard let lastPrice, lastPrice > 0 else { return "No recent price to estimate from" }
        return "At the last price of \(fmtMoney(lastPrice))"
    }
}
