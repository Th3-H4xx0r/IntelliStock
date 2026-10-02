import SwiftUI

/// The selected account's uninvested cash and holdings under the hero chart
/// — `_HoldingsList` in `dashboard_screen.dart`. Hidden while it has no
/// data, so it never shows a blank box. Rows sit in one card, as in Stocks.
struct DashboardHoldingsList: View {
    let holdings: AccountHoldingsModel
    @Bindable var feed: DashboardFeedModel
    let brokerageId: String

    @Environment(AppServices.self) private var services

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
        return VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("Holdings")
                    .font(.headline)
                    .accessibilityAddTraits(.isHeader)
                Spacer()
                Picker("P&L", selection: $feed.pnlMode) {
                    Text(HoldingsPnlMode.total.label).tag(HoldingsPnlMode.total)
                    Text(HoldingsPnlMode.daily.label).tag(HoldingsPnlMode.daily)
                }
                .pickerStyle(.segmented)
                .fixedSize()
                .controlSize(.small)
            }
            .padding(.horizontal, 2)

            VStack(spacing: 0) {
                if let cash = data.cash {
                    DashboardCashRow(cash: cash, total: total)
                    if !positions.isEmpty { DashboardHoldingDivider() }
                }
                ForEach(Array(positions.enumerated()), id: \.offset) { i, p in
                    if i > 0 { DashboardHoldingDivider() }
                    DashboardHoldingRow(
                        position: p,
                        total: total,
                        spark: sparks?[p.symbol],
                        sparkLoading: sparksLoading,
                        mode: feed.pnlMode
                    ) {
                        services.router.push(.stock(StockRoute(
                            symbol: p.symbol,
                            position: p,
                            brokerageId: brokerageId,
                            portfolioTotal: total
                        )))
                    }
                }
            }
            .padding(.vertical, 4)
            .background(DS.Surface.panel, in: .rect(cornerRadius: DS.Radius.card, style: .continuous))
        }
        .padding(.top, 24)
    }
}

private struct DashboardHoldingDivider: View {
    var body: some View {
        Divider().padding(.leading, 70).padding(.trailing, 14)
    }
}

/// `_CashRow`: a teal ring, `Cash` / `Available to invest`, and the amount.
private struct DashboardCashRow: View {
    let cash: Double
    let total: Double

    var body: some View {
        HStack(spacing: 12) {
            DashboardAllocationRing(fraction: total > 0 ? cash / total : 0, color: DS.Palette.teal)
            VStack(alignment: .leading, spacing: 2) {
                Text("Cash")
                    .font(.subheadline.weight(.bold))
                Text("Available to invest")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            Text(fmtMoney(cash))
                .font(.subheadline.weight(.semibold).monospacedDigit())
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 11)
        .accessibilityElement(children: .combine)
    }
}

/// `_HoldingRow`: ring, symbol + quantity, sparkline, value + P&L for the
/// current mode. Tapping opens the stock screen with the position.
private struct DashboardHoldingRow: View {
    let position: AccountPosition
    let total: Double
    let spark: [Double]?
    let sparkLoading: Bool
    let mode: HoldingsPnlMode
    let onTap: () -> Void

    var body: some View {
        let p = position
        let pnl = HoldingRowPnl(position: p, spark: spark, mode: mode)
        let color: Color = !pnl.hasPnl ? .secondary : (pnl.up ? DS.Palette.success : DS.Palette.danger)
        Button(action: onTap) {
            HStack(spacing: 0) {
                DashboardAllocationRing(fraction: total > 0 ? p.marketValue / total : 0, color: color)
                VStack(alignment: .leading, spacing: 2) {
                    Text(p.symbol)
                        .font(.subheadline.weight(.bold))
                        .foregroundStyle(color)
                    Text(DashboardFormat.qtyLabel(p.qty))
                        .font(.caption)
                        .foregroundStyle(color.opacity(0.7))
                }
                .lineLimit(1)
                .frame(width: 64, alignment: .leading)
                .padding(.leading, 12)

                Group {
                    if sparkLoading {
                        Skeleton(height: 28, radius: 6)
                    } else if let spark {
                        DashboardMiniSpark(values: spark)
                            .id("\(p.symbol)-\(mode.rawValue)")
                    } else {
                        Color.clear.frame(height: 28)
                    }
                }
                .frame(maxWidth: .infinity)
                .padding(.leading, 10)
                .padding(.trailing, 16)

                VStack(alignment: .trailing, spacing: 3) {
                    Text(fmtMoney(p.marketValue))
                        .font(.subheadline.weight(.semibold).monospacedDigit())
                        .foregroundStyle(color)
                    Text(pnl.label)
                        .font(.caption.weight(.bold).monospacedDigit())
                        .foregroundStyle(color)
                }
                .lineLimit(1)
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 11)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityElement(children: .combine)
        .accessibilityHint("Opens \(p.symbol)")
    }
}
