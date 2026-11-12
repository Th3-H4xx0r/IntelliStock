import SwiftUI

/// The selected stock account's orders, under its holdings: an "Orders"
/// group with an Open / Recent switch in its header, as Holdings has Total /
/// Daily. Read-only: no row cancels, places or changes anything.
///
/// Hidden while the first load runs and for an account without an order
/// book; a failed first load shows an `ErrorRow` with Retry. At most
/// `AccountOrdersModel.visibleLimit` rows show until "Show all" expands the
/// list in place. The poll runs from `DashboardView`'s list.
struct DashboardOrdersSection: View {
    let orders: AccountOrdersModel

    var body: some View {
        if !orders.isHidden {
            Section {
                rows
            } header: {
                DashboardGroupHeader(group: "Orders") {
                    if orders.book.value != nil {
                        Picker("Orders", selection: Binding(get: { orders.mode }, set: { orders.select($0) })) {
                            ForEach(OrdersMode.allCases, id: \.self) { mode in
                                Text(mode.label(openCount: orders.openCount)).tag(mode)
                            }
                        }
                        .pickerStyle(.segmented)
                        .fixedSize()
                        .controlSize(.small)
                    }
                }
            }
        }
    }

    @ViewBuilder
    private var rows: some View {
        switch orders.book {
        case .loading:
            EmptyView()
        case .failed:
            ErrorRow(message: orders.book.errorMessage ?? "") {
                Task { await orders.reload() }
            }
        case .loaded:
            let visible = orders.visibleOrders
            if visible.isEmpty {
                Text(orders.mode.emptyText)
                    .foregroundStyle(.secondary)
            } else {
                ForEach(Array(visible.enumerated()), id: \.offset) { _, order in
                    DashboardOrderRow(order: order)
                }
                if orders.hiddenCount > 0 {
                    InlineActionRow("Show all \(orders.orders.count)") {
                        withAnimation { orders.showAll() }
                    }
                    .font(.subheadline)
                }
            }
        }
    }
}

/// One order: the symbol and "Buy · Limit · 3 sh" on the left; the figure
/// and "Filled · 2h ago" on the right. No colour beyond the secondary text:
/// a closed order that never filled draws its figure secondary too.
private struct DashboardOrderRow: View {
    let order: BrokerOrder

    var body: some View {
        // The relative time ticks over between polls.
        TimelineView(.everyMinute) { context in
            let text = OrderRowText(order, now: context.date)
            // Two lines rather than truncating the size on a narrow screen.
            EntityRow(text.title, subtitle: text.subtitle, subtitleLineLimit: 2) {
                EntityRowValue(
                    text.value,
                    color: text.isQuiet ? Color.secondary : nil,
                    detail: text.detail
                )
            }
        }
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
        Section {
            ForEach(book.open + book.recent) { DashboardOrderRow(order: $0) }
        } header: {
            DashboardGroupHeader(group: "Orders")
        }
    }
    .listStyle(.insetGrouped)
}
