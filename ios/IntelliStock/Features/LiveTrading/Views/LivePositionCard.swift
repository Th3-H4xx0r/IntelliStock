import Charts
import SwiftUI

/// One open position — `PositionCard` in `position_card.dart`: symbol and
/// badges, the range move, market value, a sparkline in the screen's chart
/// style, quantity / last / entry / P&L, and Close (stock only; the wheel
/// lane buys its puts back itself).
struct LivePositionCard: View {
    let position: Position
    let chartStyle: LiveChartStyle
    let range: String
    let historicals: [HistPoint]
    let closeDisabled: Bool
    let onClose: () -> Void

    var body: some View {
        let p = position
        let up = LivePositionMath.isUp(historicals, unrealizedPnl: p.unrealizedPnl)
        let sparkColor = up ? DS.Palette.up : DS.Palette.down
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 2) {
                    DashboardFlowLayout(spacing: 8) {
                        Text(p.symbol)
                            .font(.subheadline.weight(.heavy))
                            .tracking(0.5)
                        if p.isOption {
                            AppBadge(label: "Option", color: DS.Palette.accent)
                        }
                        if p.isOption, p.isShort {
                            AppBadge(label: "Short", color: DS.Palette.warning)
                        }
                        if p.avgEntryPrice != nil, let pct = p.unrealizedPnlPct {
                            Text(fmtPct(pct))
                                .font(.caption.weight(.bold).monospacedDigit())
                                .foregroundStyle(pnlColor(p.unrealizedPnl))
                        }
                    }
                    if p.isOption {
                        Text(p.optionDescription)
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    }
                    if historicals.count >= 2 {
                        HStack(spacing: 2) {
                            Image(systemName: Symbol.named(up ? "arrow_upward" : "arrow_downward"))
                                .accessibilityHidden(true)
                            Text(LivePositionMath.rangeText(historicals, range: range, unrealizedPnl: p.unrealizedPnl))
                        }
                        .font(.caption2.weight(.bold))
                        .foregroundStyle(sparkColor)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                VStack(alignment: .trailing, spacing: 1) {
                    Text("MARKET VALUE")
                        .font(.caption2)
                        .tracking(0.5)
                        .foregroundStyle(.secondary)
                    Text(fmtMoney(p.marketValue))
                        .font(.subheadline.weight(.heavy).monospacedDigit())
                }
            }

            Group {
                if historicals.isEmpty {
                    Text(p.isOption ? "No price chart for options" : "No price history")
                        .font(.caption2)
                        .foregroundStyle(.tertiary)
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else {
                    spark(sparkColor)
                }
            }
            .frame(height: chartStyle == .candle ? 90 : 64)

            HStack(alignment: .top) {
                stat(p.quantityLabel, p.quantityText)
                stat("LAST", fmtMoney(p.lastPrice))
                stat("ENTRY", p.avgEntryPrice != nil ? fmtMoney(p.avgEntryPrice) : "—")
                stat(
                    "P&L $",
                    p.avgEntryPrice != nil ? fmtPnl(p.unrealizedPnl) : "—",
                    color: p.avgEntryPrice != nil && p.unrealizedPnl != nil ? pnlColor(p.unrealizedPnl) : .secondary
                )
            }

            HStack {
                Spacer(minLength: 0)
                if p.canClose {
                    Button(role: .destructive, action: onClose) {
                        Label("Close", systemImage: Symbol.named("logout"))
                    }
                    .buttonStyle(.bordered)
                    .controlSize(.small)
                    .disabled(closeDisabled)
                } else {
                    Text("Managed by the wheel lane, which buys puts back automatically. Close it at the broker if needed.")
                        .font(.caption2)
                        .foregroundStyle(.tertiary)
                        .multilineTextAlignment(.trailing)
                }
            }
        }
        .padding(14)
        .background(DS.Surface.inset, in: .rect(cornerRadius: DS.Radius.control, style: .continuous))
    }

    private func stat(_ label: String, _ value: String, color: Color = .primary) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label)
                .font(.caption2)
                .tracking(0.4)
                .foregroundStyle(.secondary)
            Text(value)
                .font(.caption.weight(.bold).monospacedDigit())
                .foregroundStyle(color)
                .lineLimit(1)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
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
                ForEach(values.indices, id: \.self) { i in
                    if chartStyle == .area {
                        AreaMark(x: .value("i", i), yStart: .value("Floor", lo - pad), yEnd: .value("v", values[i]))
                            .foregroundStyle(color.opacity(DS.chartAreaOpacity))
                            .interpolationMethod(.monotone)
                    }
                    LineMark(x: .value("i", i), y: .value("v", values[i]))
                        .foregroundStyle(color)
                        .lineStyle(StrokeStyle(lineWidth: chartStyle == .area ? 1.5 : 1.25))
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

/// One execution (`_TradeRow`): side, symbol (+ Option badge), fill price;
/// when, quantity and total.
struct LiveTradeRow: View {
    let trade: Trade

    var body: some View {
        let isBuy = trade.side.lowercased() == "buy"
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .top, spacing: 8) {
                Text(trade.side.uppercased())
                    .font(.subheadline.weight(.heavy))
                    .tracking(0.4)
                    .foregroundStyle(isBuy ? DS.Palette.up : DS.Palette.down)
                HStack(spacing: 6) {
                    Text(trade.symbol)
                        .font(.subheadline.weight(.heavy))
                        .lineLimit(1)
                        .minimumScaleFactor(0.85)
                        .truncationMode(.tail)
                    if trade.isOption {
                        AppBadge(label: "Option", color: DS.Palette.accent).fixedSize()
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                VStack(alignment: .trailing, spacing: 0) {
                    Text("FILL PRICE")
                        .font(.caption2)
                        .tracking(0.5)
                        .foregroundStyle(.secondary)
                    Text(fmtMoney(trade.price))
                        .font(.subheadline.weight(.heavy).monospacedDigit())
                }
            }
            HStack(alignment: .top) {
                field("WHEN", fmtDateTime(trade.ts))
                field(trade.quantityLabel, trade.quantityText)
                field("TOTAL", fmtMoney(trade.total))
            }
        }
        .padding(10)
        .background(DS.Surface.inset, in: .rect(cornerRadius: DS.Radius.small, style: .continuous))
        .accessibilityElement(children: .combine)
    }

    private func field(_ label: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            Text(label)
                .font(.caption2)
                .tracking(0.5)
                .foregroundStyle(.secondary)
            Text(value)
                .font(.caption.weight(.bold).monospacedDigit())
                .lineLimit(1)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}
