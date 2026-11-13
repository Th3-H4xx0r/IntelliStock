import SwiftUI

/// The selected stock account's orders, under its holdings — the web's
/// `DashboardOrders`: "Orders" with "2 working · 4 in history" under it and
/// the Open / History control beside it, then the rows on a card. An open
/// order cancels from a swipe or its context menu, behind the confirm
/// `DashboardView`'s list presents.
///
/// Hidden while the first load runs and for an account without an order
/// book; a failed first load shows the error with Retry. At most
/// `AccountOrdersModel.visibleLimit` rows show until "Show all N orders"
/// expands the list in place. The poll runs from `DashboardView`'s list.
struct DashboardOrdersSection: View {
    let orders: AccountOrdersModel

    var body: some View {
        if !orders.isHidden {
            DSSection("Orders", subtitle: subtitle) {
                rows
            } accessory: {
                if orders.book.value != nil {
                    DSSegmentedControl(
                        "Orders view",
                        selection: Binding(get: { orders.mode }, set: { orders.select($0) }),
                        options: OrdersMode.allCases
                    ) { mode in
                        // The web's words: "Open 2" / "History".
                        mode == .open ? (orders.openCount > 0 ? "Open \(orders.openCount)" : "Open") : "History"
                    }
                }
            }
        }
    }

    /// "2 working · 4 in history", "Nothing working · 4 in history".
    private var subtitle: String? {
        guard let book = orders.book.value else { return nil }
        let open = orders.openCount > 0 ? "\(orders.openCount) working" : "Nothing working"
        return "\(open) · \(book.recent.count) in history"
    }

    @ViewBuilder
    private var rows: some View {
        switch orders.book {
        case .loading:
            EmptyView()
        case .failed:
            HStack(spacing: 16) {
                Text(orders.book.errorMessage ?? "")
                    .font(DS.Font.body)
                    .foregroundStyle(DS.Text.secondary)
                    .frame(maxWidth: .infinity, alignment: .leading)
                Button("Retry") { Task { await orders.reload() } }
                    .buttonStyle(.dsSecondary)
            }
        case .loaded:
            let visible = orders.visibleOrders
            if let error = orders.cancelError {
                Text(error)
                    .font(DS.Font.body)
                    .foregroundStyle(DS.Palette.loss)
            }
            if visible.isEmpty {
                // The web's empty state: a sentence and a quieter line.
                VStack(spacing: 4) {
                    Text(orders.mode == .open ? "No working orders" : "No orders yet")
                        .font(DS.Font.body)
                        .foregroundStyle(DS.Text.label)
                    Text(orders.mode == .open ? "Orders show here until they fill or are canceled" : "Filled, canceled and expired orders show here")
                        .font(DS.Font.caption)
                        .foregroundStyle(DS.Text.muted)
                }
                .multilineTextAlignment(.center)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 24)
            } else {
                ForEach(Array(visible.enumerated()), id: \.offset) { _, order in
                    let cancelable = orders.canCancel(order)
                    DashboardOrderRow(order: order, canceling: orders.canceling.contains(order.id))
                        .entityRowTitleMedium()
                        .swipeActions(edge: .trailing) {
                            if cancelable {
                                Button("Cancel order", systemImage: "xmark") { orders.requestCancel(order) }
                                    .tint(DS.Palette.loss)
                            }
                        }
                        .contextMenu {
                            if cancelable {
                                Button("Cancel order", systemImage: "xmark", role: .destructive) { orders.requestCancel(order) }
                            }
                        }
                }
                if orders.hiddenCount > 0 {
                    // The web's footer row: "Show all N orders" and a chevron.
                    Button {
                        withAnimation(DSMotion.standard) { orders.showAll() }
                    } label: {
                        HStack {
                            Text("Show all \(orders.orders.count) orders")
                            Spacer()
                            Image(systemName: "chevron.down")
                                .font(.system(size: 14))
                        }
                        .font(DS.Font.body)
                        .foregroundStyle(DS.Text.secondary)
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                }
            }
        }
    }
}

/// One order (the web's phone row): the symbol (14/500) and "Buy · Limit ·
/// 3 sh" on the left; the figure and "● Filled · 2h ago" on the right, the
/// dot in the order's status tone. A closed order that never filled draws
/// its figure in zinc-500.
private struct DashboardOrderRow: View {
    let order: BrokerOrder
    /// Its cancel is in flight.
    var canceling = false

    var body: some View {
        // The relative time ticks over between polls.
        TimelineView(.everyMinute) { context in
            let text = OrderRowText(order, now: context.date)
            // Two lines rather than truncating the size on a narrow screen.
            EntityRow(text.title, subtitle: text.subtitle, subtitleLineLimit: 2) {
                if canceling {
                    ProgressView()
                        .controlSize(.small)
                } else {
                    OrderFigure(
                        value: text.value,
                        quiet: text.isQuiet,
                        detail: text.detail,
                        tone: orderStatusTone(order)
                    )
                }
            }
        }
    }
}

/// The web's `orderStatusDot`: working orders live, a fill zinc-300, a
/// rejection red, a pending cancel amber, anything else grey.
func orderStatusTone(_ order: BrokerOrder) -> DSStatusTone {
    switch order.status {
    case "rejected": return .error
    case "pending_cancel": return .warn
    case "filled": return .neutral
    case "", "new", "accepted", "pending_new", "accepted_for_bidding", "calculated", "partially_filled", "held":
        return .live
    default:
        return order.hasFill ? .neutral : .off
    }
}

/// An order's right-hand block: the figure, then the dot and status line.
struct OrderFigure: View {
    let value: String
    var quiet = false
    let detail: String
    let tone: DSStatusTone

    var body: some View {
        VStack(alignment: .trailing, spacing: 2) {
            Text(value)
                .font(DS.Font.body)
                .foregroundStyle(quiet ? DS.Text.muted : DS.Text.primary)
            HStack(spacing: 6) {
                DSStatusDot(tone)
                Text(detail)
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Text.muted)
            }
        }
        .monospacedDigit()
        .lineLimit(1)
        .accessibilityElement(children: .combine)
    }
}

#Preview("Orders") {
    let now = Date()
    let book = OrderBook(
        open: [
            BrokerOrder(id: "1", symbol: "QQQ", side: "buy", type: "limit", status: "partially_filled",
                        qty: 3, filledQty: 1, filledAvgPrice: 512.4, limitPrice: 510,
                        submittedAt: now.addingTimeInterval(-7_200)),
            BrokerOrder(id: "2", symbol: "TQQQ", side: "sell", type: "stop", status: "new",
                        qty: 10, stopPrice: 61.25, submittedAt: now.addingTimeInterval(-300)),
        ],
        recent: [
            BrokerOrder(id: "3", symbol: "GLD", side: "buy", type: "market", status: "filled",
                        qty: 2, filledQty: 2, filledAvgPrice: 241.1, filledAt: now.addingTimeInterval(-3_600)),
            BrokerOrder(id: "4", symbol: "SPY", side: "buy", type: "market", status: "filled",
                        notional: 250, filledQty: 0.4, filledAvgPrice: 625, filledAt: now.addingTimeInterval(-90_000)),
            BrokerOrder(id: "5", symbol: "XLE", side: "sell", type: "limit", status: "canceled",
                        qty: 5, limitPrice: 92, canceledAt: now.addingTimeInterval(-200_000)),
        ]
    )
    List {
        DSSection("Orders") {
            ForEach(book.open + book.recent) { DashboardOrderRow(order: $0) }
        }
    }
    .dsList()
    .dsAppearance()
}
