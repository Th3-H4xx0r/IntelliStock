import SwiftUI

/// The stock screen's Sell and Buy, against the bottom safe area: the web
/// header's pair at the large size — Sell secondary (`ui-btn`), Buy the
/// primary (`ui-btn-primary`) — on a strip of the page colour under a 6%
/// hairline. Each opens the ticket on that side.
struct TradeActionBar: View {
    let onSell: () -> Void
    let onBuy: () -> Void

    var body: some View {
        HStack(spacing: 12) {
            Button("Sell", action: onSell)
                .buttonStyle(.dsSecondary(size: .large, fullWidth: true))
            Button("Buy", action: onBuy)
                .buttonStyle(.dsPrimary(size: .large, fullWidth: true))
        }
        .padding(.horizontal, DS.pageGutter)
        .padding(.top, 12)
        .padding(.bottom, 8)
        .background {
            DS.Surface.page
                .overlay(alignment: .top) { DSDivider() }
                .ignoresSafeArea(edges: .bottom)
        }
    }
}

/// The symbol's working orders on every tradable account, on the stock
/// screen — the web's "Orders" card with its Cancel buttons. Hidden while
/// none are open. Cancel (or swipe, or long-press) asks first.
struct StockOpenOrdersSection: View {
    let model: StockTradeModel
    /// Name the account on each row (more than one tradable account).
    let showsAccount: Bool

    var body: some View {
        if let orders = model.openOrders, !orders.isEmpty || model.cancelError != nil {
            DSSection("Open orders", subtitle: orders.isEmpty ? nil : "\(orders.count) working") {
                if let error = model.cancelError {
                    Text(error)
                        .font(DS.Font.body)
                        .foregroundStyle(DS.Palette.loss)
                }
                ForEach(orders) { item in
                    let cancelable = TradeTicket.isCancelable(item.order)
                    StockOpenOrderRow(
                        item: item,
                        showsAccount: showsAccount,
                        canceling: model.canceling.contains(item.id),
                        onCancel: cancelable ? { model.requestCancel(item) } : nil
                    )
                    .swipeActions(edge: .trailing) {
                        if cancelable {
                            Button("Cancel order", systemImage: "xmark") { model.requestCancel(item) }
                                .tint(DS.Palette.loss)
                        }
                    }
                    .contextMenu {
                        if cancelable {
                            Button("Cancel order", systemImage: "xmark", role: .destructive) { model.requestCancel(item) }
                        }
                    }
                }
            }
        } else if model.openOrdersFailed {
            DSSection("Open orders") {
                HStack(spacing: 16) {
                    Text("Couldn't load open orders")
                        .font(DS.Font.body)
                        .foregroundStyle(DS.Text.secondary)
                        .frame(maxWidth: .infinity, alignment: .leading)
                    Button("Retry") { Task { await model.reloadOpenOrders() } }
                        .buttonStyle(.dsSecondary)
                }
            }
        }
    }
}

/// One working order (the web's row): "Buy · Limit · 3 sh" and, with more
/// than one account, the account under it; the limit (or "Market") over
/// "● Open · 2h ago"; then Cancel (`ui-btn`, loss text).
private struct StockOpenOrderRow: View {
    let item: AccountOrder
    let showsAccount: Bool
    let canceling: Bool
    var onCancel: (() -> Void)?

    var body: some View {
        TimelineView(.everyMinute) { context in
            let text = OrderRowText(item.order, now: context.date)
            HStack(spacing: 12) {
                EntityRow(
                    text.subtitle ?? text.title,
                    subtitle: showsAccount ? "\(DashboardFormat.accountName(item.account)) · \(TradeTicket.modeText(item.account))" : nil
                ) {
                    OrderFigure(value: text.value, detail: text.detail, tone: orderStatusTone(item.order))
                }
                if canceling {
                    ProgressView()
                        .controlSize(.small)
                        .frame(width: 64)
                } else if let onCancel {
                    Button("Cancel", role: .destructive, action: onCancel)
                        .buttonStyle(.dsSecondary)
                        .accessibilityLabel("Cancel \(text.subtitle ?? text.title) order")
                }
            }
        }
        .accessibilityHint(TradeTicket.isCancelable(item.order) ? "Swipe or long-press to cancel" : "")
    }
}

extension View {
    /// The cancel confirmation for an open order (native alert, the web's
    /// words): "Cancel order" (red) or "Keep order", so the alert never
    /// offers two kinds of "Cancel".
    func tradeCancelAlert(_ prompt: Binding<TradeCancelPrompt?>, onConfirm: @escaping (TradeCancelPrompt) -> Void) -> some View {
        alert(
            "Cancel order",
            isPresented: Binding(
                get: { prompt.wrappedValue != nil },
                set: { if !$0 { prompt.wrappedValue = nil } }
            ),
            presenting: prompt.wrappedValue
        ) { p in
            Button("Cancel order", role: .destructive) { onConfirm(p) }
            Button("Keep order", role: .cancel) {}
        } message: { p in
            Text(p.message)
        }
    }
}
