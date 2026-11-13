import Charts
import SwiftUI

/// One open position — `PositionCard` in `position_card.dart` — as a card
/// row in the web's order: the symbol (with Option and Short), the option's
/// terms, the unrealized % and the range move on the left, the market value
/// on the right; a sparkline in the screen's chart style; then shares, last,
/// entry and P&L under a divider. Close lives in the row's swipe action and
/// context menu (stock only; the wheel lane buys its puts back itself, which
/// the row says instead).
struct LivePositionRow: View {
    let position: Position
    let chartStyle: LiveChartStyle
    let range: String
    let historicals: [HistPoint]
    /// The range `historicals` were fetched for (it lags `range` while a
    /// switch loads); with the style, it names the sparkline's series.
    var historicalsRange: String?

    var body: some View {
        let p = position
        let up = LivePositionMath.isUp(historicals, unrealizedPnl: p.unrealizedPnl)
        let sparkColor = up ? DS.Palette.up : DS.Palette.down
        let hasEntry = p.avgEntryPrice != nil
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .top, spacing: 12) {
                VStack(alignment: .leading, spacing: 2) {
                    HStack(alignment: .firstTextBaseline, spacing: 8) {
                        Text(p.symbol)
                            .font(DS.Font.baseMedium)
                            .foregroundStyle(DS.Text.primary)
                            .lineLimit(1)
                            .truncationMode(.middle)
                        if p.isOption {
                            Text("Option")
                                .font(DS.Font.caption)
                                .foregroundStyle(DS.Text.muted)
                            if p.isShort {
                                Text("Short")
                                    .font(DS.Font.caption)
                                    .foregroundStyle(DS.Text.label)
                            }
                        }
                    }
                    if p.isOption {
                        Text(p.optionDescription)
                            .font(DS.Font.caption)
                            .foregroundStyle(DS.Text.muted)
                    }
                    HStack(spacing: 4) {
                        Text(hasEntry ? fmtPct(p.unrealizedPnlPct) : "—")
                            .foregroundStyle(hasEntry ? livePnlTone(p.unrealizedPnlPct) : DS.Text.muted)
                        Text("unrealized")
                            .foregroundStyle(DS.Text.muted)
                    }
                    .font(DS.Font.caption)
                    if historicals.count >= 2 {
                        Text(LivePositionMath.rangeText(historicals, range: range, unrealizedPnl: p.unrealizedPnl))
                            .font(DS.Font.caption)
                            .foregroundStyle(sparkColor)
                    }
                }
                .monospacedDigit()
                .frame(maxWidth: .infinity, alignment: .leading)
                VStack(alignment: .trailing, spacing: 4) {
                    Text("Market value")
                        .font(DS.Font.caption)
                        .foregroundStyle(DS.Text.muted)
                    Text(fmtMoney(p.marketValue))
                        .font(DS.Font.baseMedium)
                        .foregroundStyle(DS.Text.primary)
                        .monospacedDigit()
                }
            }

            Group {
                if historicals.isEmpty {
                    Text(p.isOption ? "No price chart for options" : "No price history")
                        .font(DS.Font.caption)
                        .foregroundStyle(DS.Text.faint)
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else {
                    spark(sparkColor)
                }
            }
            .frame(height: chartStyle == .candle ? 90 : 64)

            DSDivider()

            HStack(alignment: .top, spacing: 12) {
                figure(p.isOption ? "Contracts" : "Shares", p.isOption ? p.quantityText : DashboardFormat.qtyCompact(p.qty))
                figure("Last", fmtMoney(p.lastPrice))
                figure("Entry", hasEntry ? fmtMoney(p.avgEntryPrice) : "—")
                figure("P&L", hasEntry ? fmtMoney(p.unrealizedPnl) : "—", color: hasEntry ? livePnlTone(p.unrealizedPnl) : DS.Text.muted)
            }

            if !p.canClose {
                Text("Managed by the wheel lane, which buys puts back automatically. Close it at the broker if needed.")
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Text.muted)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(.vertical, 8)
        .accessibilityElement(children: .combine)
    }

    /// One of the four figures under the chart: a 12 pt label over a 13 pt
    /// zinc-300 value (the web's position card).
    private func figure(_ label: String, _ value: String, color: Color = DS.Text.label) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label)
                .font(DS.Font.caption)
                .foregroundStyle(DS.Text.muted)
            Text(value)
                .font(DS.Font.small)
                .foregroundStyle(color)
                .monospacedDigit()
                .lineLimit(1)
                .minimumScaleFactor(0.8)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
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
                        .foregroundStyle(DS.Chart.baseline)
                        .lineStyle(StrokeStyle(lineWidth: 1, dash: DS.baselineDash))
                }
                ForEach(values.indices, id: \.self) { i in
                    if chartStyle == .area {
                        AreaMark(x: .value("i", i), yStart: .value("Floor", lo - pad), yEnd: .value("v", values[i]))
                            // The faint same-hue fade under a P&L line (the web's).
                            .foregroundStyle(LinearGradient(
                                colors: [color.opacity(DS.Chart.areaTopOpacity), color.opacity(0)],
                                startPoint: .top,
                                endPoint: .bottom
                            ))
                            .interpolationMethod(.monotone)
                    }
                    LineMark(x: .value("i", i), y: .value("v", values[i]))
                        .foregroundStyle(color)
                        .lineStyle(StrokeStyle(lineWidth: chartStyle == .line ? 1.25 : 1.75))
                        .interpolationMethod(.monotone)
                }
            }
        }
        .chartXAxis(.hidden)
        .chartYAxis(.hidden)
        .chartLegend(.hidden)
        .chartYScale(domain: (lo - pad)...(lo + span + pad))
        // A row-sized chart: the sparkline's draw-in, again per style and
        // per loaded range.
        .chartDrawIn(
            trigger: AnyHashable([chartStyle.rawValue, historicalsRange ?? range]),
            duration: ChartDrawIn.sparkDuration,
            bleed: 2
        )
        .accessibilityHidden(true)
    }
}

/// One execution (`_TradeRow`), as the web's phone row: the side in 12 pt
/// zinc-400 before the symbol (with Option), when it filled and
/// "131 sh @ $89.80" under them, and the fill's total on the right.
struct LiveTradeRow: View {
    let trade: Trade

    var body: some View {
        HStack(alignment: .center, spacing: 12) {
            VStack(alignment: .leading, spacing: 2) {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text(trade.side.isEmpty ? "—" : trade.side.lowercased().dsSentenceCased)
                        .font(DS.Font.caption)
                        .foregroundStyle(DS.Text.secondary)
                    Text(trade.symbol)
                        .font(DS.Font.bodyMedium)
                        .foregroundStyle(DS.Text.primary)
                        .lineLimit(1)
                        .truncationMode(.middle)
                    if trade.isOption {
                        Text("Option")
                            .font(DS.Font.caption)
                            .foregroundStyle(DS.Text.muted)
                            .fixedSize()
                    }
                }
                Text("\(fmtDateTime(trade.ts)) · \(liveFillText(trade))")
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Text.muted)
                    .monospacedDigit()
                    .lineLimit(1)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            Text(fmtMoney(trade.total))
                .font(DS.Font.body)
                .foregroundStyle(DS.Text.primary)
                .monospacedDigit()
                .layoutPriority(1)
        }
        .accessibilityElement(children: .combine)
    }
}

/// The web's `pnlColorClass`: gain or loss, zinc-400 for zero or nothing.
nonisolated func livePnlTone(_ v: Double?) -> Color {
    guard let v, !v.isNaN, v != 0 else { return DS.Text.secondary }
    return v > 0 ? DS.Palette.gain : DS.Palette.loss
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
