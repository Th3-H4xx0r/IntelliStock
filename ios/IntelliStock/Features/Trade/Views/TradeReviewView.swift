import SwiftUI

/// The order review — the web ticket's review step, on a card that follows
/// the finger: the symbol with side and type, "● Live money · Alpaca Live",
/// the estimate and size, the order's rows, the instance line and the
/// caveat; then a swipe up that names the action ("Sell 4.25 AAPL") and
/// sends it. The card follows the
/// finger, and only a drag to the very top sends (`SwipeUpHint.reachesTop`);
/// VoiceOver sends with the hint's default action.
///
/// A placed order shows its status and stays until Done. A refusal or an
/// unconfirmed send springs the card back with the server's reason on it;
/// nothing is retried automatically, and a swipe again reuses the ticket's
/// `client_order_id`, so it cannot place a second order.
struct TradeReviewView: View {
    let model: TradeTicketModel
    let item: TradeReviewItem
    let onSubmitted: (String) -> Void
    let onClose: () -> Void

    @State private var phase: Phase = .review
    /// The card's vertical drag; negative is up.
    @State private var drag: CGFloat = 0
    /// The card has flown off the top.
    @State private var launched = false
    /// Where the finger is during a drag, in the view's (unmoving) space.
    @State private var fingerY: CGFloat?

    enum Phase: Equatable {
        case review
        case sending
        case placed(BrokerOrder)
    }

    private static let space = "trade.order.review"

    var body: some View {
        Group {
            switch phase {
            case .review, .sending:
                reviewContent
            case .placed(let order):
                TradeOutcomeView(order: order, item: item)
            }
        }
        .background(DS.Surface.page)
        .navigationTitle(phase.isPlaced ? "" : "Review order")
        .navigationBarTitleDisplayMode(.inline)
        .navigationBarBackButtonHidden(phase != .review)
        .toolbar {
            if phase.isPlaced {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done", action: onClose)
                }
            }
        }
        .sensoryFeedback(trigger: phase) { _, new in
            if case .placed = new { return .success }
            return nil
        }
        .sensoryFeedback(.error, trigger: failureCount)
    }

    /// Bumps on each failed send, for the error haptic.
    @State private var failureCount = 0

    // MARK: Review

    private var reviewContent: some View {
        GeometryReader { geo in
            let height = geo.size.height
            let reached = fingerY.map { SwipeUpHint.reachesTop(fingerY: $0, travel: -drag, height: height) } ?? false
            let lift = drag < 0 ? drag : drag * 0.2
            let progress: CGFloat = reached ? 1 : min(max(0, -drag) / max(1, height * 0.6), 0.95)
            VStack(spacing: 0) {
                // Its own view, so a drag moves it without rebuilding it.
                let card = TradeReviewCard(
                    item: item,
                    lastPrice: model.lastPrice,
                    instanceWarning: TradeTicket.instanceWarning(model.instances ?? [], accountId: item.account.id),
                    failure: model.result
                )
                .equatable()
                ViewThatFits(in: .vertical) {
                    card
                    ScrollView { card }
                }
                Spacer(minLength: 0)
                SwipeUpHint(
                    label: fingerY == nil ? item.request.actionLabel : (reached ? "Release to send" : "All the way up"),
                    progress: progress,
                    onSend: launch
                )
            }
            .padding(.bottom, 12)
            .offset(y: launched ? -1400 : lift)
            .opacity(launched ? 0 : 1)
            .scaleEffect(launched ? 0.92 : 1, anchor: .top)
            .contentShape(Rectangle())
            .gesture(
                DragGesture(minimumDistance: 8, coordinateSpace: .named(Self.space))
                    .onChanged { value in
                        guard phase == .review, !launched else { return }
                        drag = value.translation.height
                        fingerY = value.location.y
                    }
                    .onEnded { value in
                        guard phase == .review, !launched else { return }
                        let sends = SwipeUpHint.reachesTop(fingerY: value.location.y, travel: -value.translation.height, height: height)
                        fingerY = nil
                        if sends {
                            launch()
                        } else {
                            withAnimation(DSMotion.ease(0.3)) { drag = 0 }
                        }
                    }
            )
            .sensoryFeedback(.impact(weight: .medium), trigger: reached)
            .overlay {
                if phase == .sending {
                    VStack(spacing: 10) {
                        ProgressView()
                        Text("Sending…")
                            .font(DS.Font.body)
                            .foregroundStyle(DS.Text.secondary)
                    }
                    .transition(.opacity)
                }
            }
        }
        .coordinateSpace(.named(Self.space))
    }

    // MARK: Actions

    /// The card flies off the top, then the order sends.
    private func launch() {
        guard phase == .review, !launched else { return }
        withAnimation(.easeIn(duration: 0.3)) { launched = true }
        withAnimation(.easeIn(duration: 0.2).delay(0.15)) { phase = .sending }
        Task {
            let result = await model.submit(item)
            switch result {
            case .placed(let order)?:
                onSubmitted(item.account.id)
                withAnimation(DSMotion.ease(0.25)) { phase = .placed(order) }
            case .unconfirmed?:
                onSubmitted(item.account.id)
                springBack()
            case .rejected?, nil:
                springBack()
            }
        }
    }

    private func springBack() {
        failureCount += 1
        withAnimation(DSMotion.ease(0.35)) {
            phase = .review
            launched = false
            drag = 0
        }
    }
}

private extension TradeReviewView.Phase {
    var isPlaced: Bool {
        if case .placed = self { return true }
        return false
    }
}

/// The order on a raised card: the side and symbol, the account with Live
/// money or Paper, the figures, the instance line, a failed send's reason,
/// and the order type's caveat. Equatable on its inputs, so the drag never
/// rebuilds it.
private struct TradeReviewCard: View, Equatable {
    let item: TradeReviewItem
    let lastPrice: Double?
    let instanceWarning: String?
    let failure: TradeSubmitResult?

    nonisolated static func == (a: Self, b: Self) -> Bool {
        a.item == b.item && a.lastPrice == b.lastPrice && a.instanceWarning == b.instanceWarning && a.failure == b.failure
    }

    var body: some View {
        let r = item.request
        let account = item.account
        let live = !account.alpacaPaper
        VStack(alignment: .leading, spacing: 0) {
            // Head (the web's `ui-modal-head`).
            VStack(alignment: .leading, spacing: 6) {
                HStack(alignment: .firstTextBaseline, spacing: 12) {
                    Text(stockDisplayTitle(r.symbol))
                        .font(DS.Font.pageTitle)
                        .foregroundStyle(DS.Text.primary)
                    Text("\(r.side.label) · \(r.type.label)")
                        .font(DS.Font.body)
                        .foregroundStyle(DS.Text.label)
                }
                HStack(spacing: 8) {
                    DSStatusDot(live ? .warn : .off)
                    Text(TradeTicket.modeText(account))
                        .foregroundStyle(live ? DS.Text.primary : DS.Text.secondary)
                    Text("·").foregroundStyle(DS.Text.faint)
                    Text(DashboardFormat.accountName(account))
                        .foregroundStyle(DS.Text.secondary)
                        .lineLimit(1)
                }
                .font(DS.Font.body)
            }
            .padding(.horizontal, 24)
            .padding(.top, 20)
            .padding(.bottom, 16)
            .accessibilityElement(children: .combine)
            DSDivider()

            VStack(alignment: .leading, spacing: 20) {
                StatGrid(columns: 2, horizontalSpacing: 16) {
                    StatCell(label: TradeTicket.estimateLabel(r.side), footnote: basis, value: { Text(fmtMoney(estimate)) })
                    StatCell(label: r.notional == nil ? "Shares" : "Amount") {
                        Text(r.qty.map { TradeTicket.exactQtyText($0.value) } ?? fmtMoney(r.notional?.value))
                    }
                }
                .statValueStyle(.figure)

                // The order's rows (the web's bordered `dl`).
                VStack(spacing: 0) {
                    ForEach(Array(rows.enumerated()), id: \.offset) { index, row in
                        if index > 0 { DSDivider(row: true) }
                        DSKeyValueRow(row.label, row.value)
                            .padding(.horizontal, 14)
                            .frame(minHeight: 40)
                    }
                }
                .overlay {
                    RoundedRectangle(cornerRadius: DS.Radius.control, style: .continuous)
                        .strokeBorder(DS.Line.card, lineWidth: DS.Line.width)
                }

                if let instanceWarning {
                    Text(instanceWarning)
                        .font(DS.Font.caption)
                        .foregroundStyle(DS.Text.muted)
                        .fixedSize(horizontal: false, vertical: true)
                }

                Text(caveat)
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Text.muted)
                    .lineSpacing(3)
                    .fixedSize(horizontal: false, vertical: true)

                if let failure, let message = failure.message {
                    TradeCallout(
                        title: failure.isUnconfirmed ? "Not confirmed" : "Not sent",
                        text: message,
                        warning: failure.isUnconfirmed
                    )
                }
            }
            .padding(.horizontal, 24)
            .padding(.vertical, 20)
        }
        .dsCardSurface(radius: DS.Radius.modal, border: DS.Line.input)
        .padding(.horizontal, 16)
        .padding(.top, 8)
    }

    /// The web's `reviewRows`, less those the head and the figures above
    /// already show (account, money, side, symbol, the estimate), so the
    /// card fits the sheet and the swipe is never a scroll.
    private var rows: [(label: String, value: String)] {
        let r = item.request
        var rows: [(String, String)] = [
            (r.notional == nil ? "Shares" : "Amount", r.qty.map { "\(TradeTicket.exactQtyText($0.value)) shares" } ?? fmtMoney(r.notional?.value)),
            ("Order type", r.type.label),
        ]
        if let limit = r.limitPrice { rows.append(("Limit price", TradeTicket.priceText(limit))) }
        rows.append(("Time in force", r.timeInForce.label))
        rows.append(("Last price", fmtMoney(lastPrice)))
        return rows.map { (label: $0.0, value: $0.1) }
    }

    /// Where the estimate's price came from.
    private var basis: String {
        let r = item.request
        if r.notional != nil { return "The dollar amount" }
        if let limit = r.limitPrice { return "At your limit of \(TradeTicket.priceText(limit))" }
        guard let lastPrice else { return "No recent price to estimate from" }
        return "At the last price of \(fmtMoney(lastPrice))"
    }

    /// Shares × the limit or last price, or the dollar amount.
    private var estimate: Double? {
        let r = item.request
        if let notional = r.notional { return notional.value }
        guard let qty = r.qty, let price = r.limitPrice?.value ?? lastPrice else { return nil }
        return qty.value * price
    }

    private var caveat: String {
        let r = item.request
        let money = item.account.alpacaPaper ? "Paper account: simulated money." : "Live account: this uses real money."
        if let limit = r.limitPrice {
            return "\(money) A limit order fills only at \(TradeTicket.priceText(limit)) or better, and may not fill at all"
        }
        return "\(money) A market order fills at the next available price, which can differ from the estimate"
    }
}

private extension TradeSubmitResult {
    var isUnconfirmed: Bool {
        if case .unconfirmed = self { return true }
        return false
    }
}

/// A failed send's reason on the card: the web's error box (`ui-error`)
/// for a refusal; for an unconfirmed send, an amber dot and the warning on
/// the same faint surface, since the order may still exist.
private struct TradeCallout: View {
    let title: String
    let text: String
    let warning: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 8) {
                if warning { DSStatusDot(.warn) }
                Text(title)
                    .font(DS.Font.bodyMedium)
                    .foregroundStyle(warning ? DS.Text.primary : DS.Palette.loss)
            }
            Text(text)
                .font(DS.Font.body)
                .foregroundStyle(warning ? DS.Text.label : DS.Palette.loss)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .dsErrorSurface(radius: DS.Radius.control)
        .accessibilityElement(children: .combine)
    }
}

/// A placed order (the web's result step): a gain check that draws itself
/// in, the action, Alpaca's status for it as a dot and a word with the
/// account, the note, and the order's id.
private struct TradeOutcomeView: View {
    let order: BrokerOrder
    let item: TradeReviewItem

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var shown = false

    var body: some View {
        VStack(spacing: 12) {
            Spacer()
            ZStack {
                if shown {
                    let mark = Image(systemName: "checkmark.circle")
                        .font(.system(size: 40, weight: .light))
                        .foregroundStyle(DS.Palette.gain)
                    if reduceMotion {
                        mark.transition(.opacity)
                    } else {
                        mark.transition(.symbolEffect(.drawOn.byLayer))
                    }
                }
            }
            .frame(height: 48)
            .accessibilityHidden(true)
            Text(item.request.actionLabel)
                .font(DS.Font.large)
                .foregroundStyle(DS.Text.primary)
            HStack(spacing: 8) {
                DSStatusDot(orderStatusTone(order))
                Text(OrderRowText.statusText(order))
                    .foregroundStyle(DS.Text.label)
                Text("·").foregroundStyle(DS.Text.faint)
                Text("\(DashboardFormat.accountName(item.account)), \(TradeTicket.modeText(item.account))")
                    .foregroundStyle(DS.Text.secondary)
            }
            .font(DS.Font.body)
            Text("Alpaca has the order. It shows under Orders until it fills or is canceled")
                .font(DS.Font.caption)
                .foregroundStyle(DS.Text.muted)
                .multilineTextAlignment(.center)
                .frame(maxWidth: 300)
            Text(order.id)
                .font(DS.Font.mono)
                .foregroundStyle(DS.Text.muted)
                .textSelection(.enabled)
                .lineLimit(1)
                .truncationMode(.middle)
            Spacer()
            Spacer()
        }
        .padding(.horizontal, 32)
        .frame(maxWidth: .infinity)
        .accessibilityElement(children: .combine)
        .onAppear {
            withAnimation(DSMotion.ease(0.4).delay(0.05)) { shown = true }
        }
    }
}
