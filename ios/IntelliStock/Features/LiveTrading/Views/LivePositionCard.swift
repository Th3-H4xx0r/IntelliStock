import Charts
import SwiftUI

/// One open position — `PositionCard` in `position_card.dart` — as a list
/// row: the symbol (with an Option or Short flag) and market value, the
/// range move and unrealized %, a sparkline in the screen's chart style, then
/// quantity / last / entry as a `StatGrid`, the P&L in dollars and percent
/// under the market value. Close lives in the row's
/// swipe action and context menu (stock only; the wheel lane buys its puts
/// back itself, which the row says instead).
struct LivePositionRow: View {
    let position: Position
    let chartStyle: LiveChartStyle
    let range: String
    let historicals: [HistPoint]

    var body: some View {
        let p = position
        let up = LivePositionMath.isUp(historicals, unrealizedPnl: p.unrealizedPnl)
        let sparkColor = up ? DS.Palette.up : DS.Palette.down
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text(p.symbol)
                    .font(.headline)
                if p.isOption {
                    StatusBadge(label: p.isShort ? "Short option" : "Option", color: p.isShort ? DS.Palette.warning : DS.Palette.accent)
                }
                Spacer(minLength: 8)
                Text(fmtMoney(p.marketValue))
                    .font(.headline.monospacedDigit())
            }
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                if historicals.count >= 2 {
                    HStack(spacing: 2) {
                        Image(systemName: up ? "arrow.up.right" : "arrow.down.right")
                            .accessibilityHidden(true)
                        Text(LivePositionMath.rangeText(historicals, range: range, unrealizedPnl: p.unrealizedPnl))
                    }
                    .foregroundStyle(sparkColor)
                } else if p.isOption {
                    Text(p.optionDescription)
                        .foregroundStyle(.secondary)
                }
                Spacer(minLength: 8)
                // The unrealized P&L, dollars then percent (`P&L $` and the
                // header % in Dart).
                if p.avgEntryPrice != nil {
                    Text([p.unrealizedPnl.map { fmtPnl($0) }, p.unrealizedPnlPct.map { fmtPct($0) }]
                        .compactMap { $0 }
                        .joined(separator: " · "))
                        .foregroundStyle(p.unrealizedPnl != nil ? pnlColor(p.unrealizedPnl) : .secondary)
                }
            }
            .font(.subheadline.monospacedDigit())
            if p.isOption, historicals.count >= 2 {
                Text(p.optionDescription)
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }

            Group {
                if historicals.isEmpty {
                    Text(p.isOption ? "No price chart for options" : "No price history")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else {
                    spark(sparkColor)
                }
            }
            .frame(height: chartStyle == .candle ? 90 : 64)

            StatGrid(columns: 3) {
                StatCell(label: p.isOption ? "Contracts" : "Shares", value: p.isOption ? p.quantityText : DashboardFormat.qtyCompact(p.qty))
                StatCell(label: "Last", value: fmtMoney(p.lastPrice))
                StatCell(label: "Entry", value: p.avgEntryPrice != nil ? fmtMoney(p.avgEntryPrice) : "—")
            }

            if !p.canClose {
                Text("Managed by the wheel lane, which buys puts back automatically. Close it at the broker if needed.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
        }
        .padding(.vertical, 6)
        .accessibilityElement(children: .combine)
    }

    @ViewBuilder
    private func spark(_ color: Color) -> some View {
        let values = historicals.map(\.value)
        let lo = values.min() ?? 0
        let hi = values.max() ?? 1
        let span = abs(hi - lo) < 1e-9 ? 1 : hi - lo
        let pad = span * 0.06
        Chart {
            if chartStyle == .candle {
                ForEach(liveBucketCandles(values, count: 20), id: \.x) { c in
                    let candleColor = c.close >= c.open ? DS.Palette.up : DS.Palette.down
                    RuleMark(x: .value("i", Double(c.x)), yStart: .value("Low", c.low), yEnd: .value("High", c.high))
                        .foregroundStyle(candleColor)
                    RectangleMark(
                        x: .value("i", Double(c.x)),
                        yStart: .value("Open", min(c.open, c.close)),
                        yEnd: .value("Close", max(max(c.open, c.close), min(c.open, c.close) + span * 0.004)),
                        width: .fixed(5)
                    )
                    .foregroundStyle(candleColor)
                }
            } else {
                if let first = values.first {
                    RuleMark(y: .value("Start", first))
                        .foregroundStyle(Color.secondary.opacity(0.5))
                        .lineStyle(StrokeStyle(lineWidth: 1, dash: DS.baselineDash))
                }
                ForEach(values.indices, id: \.self) { i in
                    if chartStyle == .area {
                        AreaMark(x: .value("i", i), yStart: .value("Floor", lo - pad), yEnd: .value("v", values[i]))
                            .foregroundStyle(color.opacity(DS.chartAreaOpacity))
                            .interpolationMethod(.monotone)
                    }
                    LineMark(x: .value("i", i), y: .value("v", values[i]))
                        .foregroundStyle(color)
                        .lineStyle(StrokeStyle(lineWidth: 1.5))
                        .interpolationMethod(.monotone)
                }
            }
        }
        .chartXAxis(.hidden)
        .chartYAxis(.hidden)
        .chartLegend(.hidden)
        .chartYScale(domain: (lo - pad)...(lo + span + pad))
        .accessibilityHidden(true)
    }
}

/// One execution (`_TradeRow`): "SELL EL" over when it filled, and
/// "131 sh @ $89.80" over the fill's total.
struct LiveTradeRow: View {
    let trade: Trade

    var body: some View {
        let isBuy = trade.side.lowercased() == "buy"
        HStack(alignment: .center, spacing: 12) {
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 6) {
                    Text("\(Text(trade.side.uppercased()).foregroundStyle(isBuy ? DS.Palette.up : DS.Palette.down)) \(trade.symbol)")
                        .font(.headline)
                        .lineLimit(1)
                        .truncationMode(.middle)
                    if trade.isOption {
                        StatusBadge(label: "Option", color: DS.Palette.accent)
                            .fixedSize()
                    }
                }
                Text(fmtDateTime(trade.ts))
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            EntityRowValue(liveFillText(trade), detail: fmtMoney(trade.total))
                .layoutPriority(1)
        }
        .accessibilityElement(children: .combine)
    }
}

/// `131 sh @ $89.80`, or `2 contracts @ $1.20` for an option.
nonisolated func liveFillText(_ t: Trade) -> String {
    if t.isOption {
        let n = t.quantityText
        return "\(n) contract\(n == "1" ? "" : "s") @ \(fmtMoney(t.price))"
    }
    let qty = DashboardFormat.qtyShort(t.qty)
    return "\(qty) @ \(fmtMoney(t.price))"
}
