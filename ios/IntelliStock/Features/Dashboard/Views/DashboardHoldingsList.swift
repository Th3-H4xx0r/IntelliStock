import SwiftUI

/// The selected account's uninvested cash and holdings under the hero chart
/// — the web's `DashboardHoldings` — as a "Holdings" card section: "N
/// positions" under the title and the Daily / Total control beside it. Hidden
/// while it has no data. Each holding row opens the stock screen.
struct DashboardHoldingsSection: View {
    let holdings: AccountHoldingsModel
    @Bindable var feed: DashboardFeedModel
    let brokerageId: String

    var body: some View {
        if let data = holdings.holdings.value, !data.isEmpty {
            content(data)
        }
    }

    private func content(_ data: AccountHoldings) -> some View {
        let positions = data.positions
        let sparks = holdings.displayedSparks
        // Skeleton is a first-load affordance: only when no curve has ever drawn.
        let sparksLoading = sparks == nil
        // Total account value → each row's ring shows its share of the portfolio.
        let total = (data.cash ?? 0) + positions.reduce(0) { $0 + $1.marketValue }
        return DSSection(
            "Holdings",
            subtitle: "\(positions.count) position\(positions.count == 1 ? "" : "s")"
        ) {
            if let cash = data.cash {
                DashboardCashRow(cash: cash, total: total)
            }
            ForEach(Array(positions.enumerated()), id: \.offset) { _, p in
                NavigationLink(value: Route.stock(StockRoute(
                    symbol: p.symbol,
                    position: p,
                    brokerageId: brokerageId,
                    portfolioTotal: total
                ))) {
                    DashboardHoldingRow(
                        position: p,
                        total: total,
                        spark: sparks?[p.symbol],
                        sparkLoading: sparksLoading,
                        mode: feed.pnlMode
                    )
                }
                .accessibilityHint("Opens \(p.symbol)")
                .entityRowTitleMedium()
            }
        } accessory: {
            DSSegmentedControl("P&L period", selection: $feed.pnlMode, options: [.daily, .total]) { $0.label }
        }
    }
}

/// `Cash` / `Available to invest` and the amount; the quiet ring is its
/// share of the account.
private struct DashboardCashRow: View {
    let cash: Double
    let total: Double

    var body: some View {
        EntityRow("Cash", subtitle: "Available to invest") {
            MiniAllocationRing(fraction: total > 0 ? cash / total : 0, color: DS.Text.muted, size: 20)
        } trailing: {
            EntityRowValue(fmtMoney(cash))
        }
    }
}

/// One holding (the web's row): the symbol and quantity, the period's
/// sparkline, then the value with the period's P&L under it in gain or
/// loss. The quiet ring before the symbol is its share of the account.
private struct DashboardHoldingRow: View {
    let position: AccountPosition
    let total: Double
    let spark: [Double]?
    let sparkLoading: Bool
    let mode: HoldingsPnlMode

    var body: some View {
        let p = position
        let pnl = HoldingRowPnl(position: p, spark: spark, mode: mode)
        let color: Color = !pnl.hasPnl ? DS.Text.faint : (pnl.up ? DS.Palette.gain : DS.Palette.loss)
        EntityRow(p.symbol, subtitle: DashboardFormat.qtyShort(p.qty)) {
            MiniAllocationRing(fraction: total > 0 ? p.marketValue / total : 0, color: DS.Text.secondary, size: 20)
        } trailing: {
            HStack(spacing: 12) {
                Group {
                    if sparkLoading {
                        // The web's placeholder: a 2 pt raised line.
                        Skeleton(height: 2, radius: 1)
                    } else if let spark {
                        Sparkline(values: spark, height: 24)
                            .id("\(p.symbol)-\(mode.rawValue)")
                    } else {
                        Color.clear
                    }
                }
                .frame(width: 72, height: 24)
                EntityRowValue(fmtMoney(p.marketValue), detail: pnl.label, detailColor: color)
            }
        }
    }
}
