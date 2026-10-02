import SwiftUI

/// The selected account's uninvested cash and holdings under the hero chart
/// — `_HoldingsList` in `dashboard_screen.dart` — as a "Holdings" list
/// section with the Total / Daily switch in its header. Hidden while it has
/// no data, so it never shows an empty section. Each holding row opens the
/// stock screen with the position.
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
        return Section {
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
            }
        } header: {
            DashboardGroupHeader(group: "Holdings") {
                Picker("P&L", selection: $feed.pnlMode) {
                    Text(HoldingsPnlMode.total.label).tag(HoldingsPnlMode.total)
                    Text(HoldingsPnlMode.daily.label).tag(HoldingsPnlMode.daily)
                }
                .pickerStyle(.segmented)
                .fixedSize()
                .controlSize(.small)
            }
        }
    }
}

/// `_CashRow`: a teal ring, `Cash` / `Available to invest`, and the amount.
private struct DashboardCashRow: View {
    let cash: Double
    let total: Double

    var body: some View {
        EntityRow("Cash", subtitle: "Available to invest") {
            MiniAllocationRing(fraction: total > 0 ? cash / total : 0, color: DS.Palette.teal)
        } trailing: {
            EntityRowValue(fmtMoney(cash))
        }
    }
}

/// `_HoldingRow`: ring, symbol and quantity, a 60 × 24 sparkline, then the
/// value with the P&L for the current mode under it in green or red.
private struct DashboardHoldingRow: View {
    let position: AccountPosition
    let total: Double
    let spark: [Double]?
    let sparkLoading: Bool
    let mode: HoldingsPnlMode

    var body: some View {
        let p = position
        let pnl = HoldingRowPnl(position: p, spark: spark, mode: mode)
        let color: Color = !pnl.hasPnl ? .secondary : (pnl.up ? DS.Palette.up : DS.Palette.down)
        EntityRow(p.symbol, subtitle: DashboardFormat.qtyShort(p.qty)) {
            MiniAllocationRing(fraction: total > 0 ? p.marketValue / total : 0, color: DS.Palette.accent)
        } trailing: {
            HStack(spacing: 12) {
                Group {
                    if sparkLoading {
                        Skeleton(height: 18, radius: 5)
                    } else if let spark {
                        Sparkline(values: spark, height: 24)
                            .id("\(p.symbol)-\(mode.rawValue)")
                    } else {
                        Color.clear
                    }
                }
                .frame(width: 60, height: 24)
                EntityRowValue(fmtMoney(p.marketValue), detail: pnl.label, detailColor: color)
            }
        }
    }
}
