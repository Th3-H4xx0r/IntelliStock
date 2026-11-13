import Charts
import SwiftUI

/// The live equity chart — `EquityChart` in `equity_chart.dart`, drawn as
/// the web hero's: area (a faint same-hue fade), line or candles over a
/// gapless index axis (1D area/line on the fixed full-day minute axis), the
/// dotted opening value and white-5% price gridlines, with a scrub that
/// reports the nearest point. It
/// draws itself in from the leading edge for each range and style
/// (`chartDrawIn`); polls leave it still.
struct LiveEquityChart: View {
    let history: PortfolioHistory
    let style: LiveChartStyle
    let range: String
    var height: CGFloat = 280
    /// The scrubbed index, nil when the finger lifts.
    let onScrub: (Int?) -> Void

    @State private var selectedX: Double?
    @State private var scrubIndex: Int?

    private static let labelRowHeight: CGFloat = 20

    var body: some View {
        let n = min(history.timestamps.count, history.values.count)
        if n == 0 {
            Text("No equity data yet")
                .font(DS.Font.body)
                .foregroundStyle(DS.Text.muted)
                .frame(maxWidth: .infinity)
                .frame(height: height)
        } else {
            chart(count: n)
        }
    }

    private func chart(count n: Int) -> some View {
        let values = Array(history.values.prefix(n))
        let up = values.count < 2 || values[values.count - 1] >= values[0]
        let color = up ? DS.Palette.up : DS.Palette.down
        let bounds = paddedBounds(values)
        let timeAxis = LiveEquityGeometry.usesTimeAxis(range: range, style: style)
        let xs = LiveEquityGeometry.xs(history, range: range, style: style)
        let candles = style == .candle ? liveBucketCandles(values, count: 40) : []
        let plotHeight = min(max(height - Self.labelRowHeight, 40), height)
        let domain: ClosedRange<Double> = {
            if timeAxis { return 0...DashboardChartGeometry.dayMinutes }
            if style == .candle { return -0.5...(Double(max(candles.count, 1)) - 0.5) }
            return 0...Double(max(n - 1, 1))
        }()
        return VStack(spacing: 0) {
            Chart {
                switch style {
                case .candle:
                    ForEach(candles, id: \.x) { c in
                        let candleColor = c.close >= c.open ? DS.Palette.up : DS.Palette.down
                        RuleMark(
                            x: .value("i", Double(c.x)),
                            yStart: .value("Low", c.low),
                            yEnd: .value("High", c.high)
                        )
                        .foregroundStyle(candleColor)
                        .lineStyle(StrokeStyle(lineWidth: 1))
                        RectangleMark(
                            x: .value("i", Double(c.x)),
                            yStart: .value("Open", min(c.open, c.close)),
                            yEnd: .value("Close", max(c.open, c.close) == min(c.open, c.close) ? max(c.open, c.close) + (bounds.max - bounds.min) * 0.002 : max(c.open, c.close)),
                            width: .fixed(6)
                        )
                        .foregroundStyle(candleColor)
                    }
                case .area, .line:
                    // Stocks' dotted baseline at the range's opening value.
                    RuleMark(y: .value("Open", values[0]))
                        .foregroundStyle(DS.Chart.baseline)
                        .lineStyle(StrokeStyle(lineWidth: 1, dash: DS.baselineDash))
                    ForEach(0..<n, id: \.self) { i in
                        if style == .area {
                            AreaMark(
                                x: .value("x", xs[i]),
                                yStart: .value("Floor", bounds.min),
                                yEnd: .value("Equity", values[i])
                            )
                            // The faint same-hue fade under a P&L line (the web hero's).
                            .foregroundStyle(LinearGradient(
                                colors: [color.opacity(DS.Chart.areaTopOpacity), color.opacity(0)],
                                startPoint: .top,
                                endPoint: .bottom
                            ))
                            .interpolationMethod(.monotone)
                        }
                        LineMark(x: .value("x", xs[i]), y: .value("Equity", values[i]))
                            .foregroundStyle(color)
                            .lineStyle(StrokeStyle(lineWidth: style == .area ? 1.75 : 1.25, lineCap: .round, lineJoin: .round))
                            .interpolationMethod(.monotone)
                    }
                }
                if let idx = scrubIndex, idx < n {
                    let fraction = LiveEquityGeometry.hairlineFraction(idx, xs: xs, timeAxis: timeAxis)
                    let x = domain.lowerBound + fraction * (domain.upperBound - domain.lowerBound)
                    RuleMark(x: .value("Scrub", style == .candle ? candleX(fraction, candles.count) : x))
                        .foregroundStyle(DS.Chart.scrubLine)
                        .lineStyle(StrokeStyle(lineWidth: 1))
                    if style != .candle {
                        PointMark(x: .value("x", xs[idx]), y: .value("Equity", values[idx]))
                            .foregroundStyle(color)
                            .symbolSize(60)
                    }
                }
            }
            .chartXScale(domain: domain)
            .chartYScale(domain: bounds.min...bounds.max)
            .chartXAxis(.hidden)
            // The web hero's price gridlines: white 5%, 11 pt zinc-500 labels.
            .chartYAxis {
                AxisMarks(position: .trailing, values: .automatic(desiredCount: 4)) { value in
                    AxisGridLine(stroke: StrokeStyle(lineWidth: 1))
                        .foregroundStyle(DS.Chart.grid)
                    AxisValueLabel {
                        if let price = value.as(Double.self) {
                            Text(fmtMoney(price))
                                .font(DS.Font.micro)
                                .monospacedDigit()
                                .foregroundStyle(DS.Text.muted)
                        }
                    }
                }
            }
            .chartLegend(.hidden)
            .chartXSelection(value: $selectedX)
            .frame(height: plotHeight)
            .chartDrawIn(trigger: AnyHashable([range, style.rawValue]), interacting: selectedX != nil)
            .accessibilityElement()
            .accessibilityLabel("Equity chart")
            .accessibilityValue(fmtMoney(values.last))

            ChartDateLabels(labels: LiveEquityGeometry.labels(history, range: range, style: style))
        }
        .frame(height: height)
        .onChange(of: selectedX) { _, x in
            guard let x else {
                if scrubIndex != nil {
                    scrubIndex = nil
                    onScrub(nil)
                }
                return
            }
            let fraction = (x - domain.lowerBound) / (domain.upperBound - domain.lowerBound)
            let idx = LiveEquityGeometry.scrubIndex(fraction: fraction, xs: xs, timeAxis: timeAxis)
            if idx != scrubIndex {
                scrubIndex = idx
                onScrub(idx)
            }
        }
        .sensoryFeedback(.selection, trigger: scrubIndex) { _, new in new != nil }
    }

    /// A point's fraction on the candle axis (index space −0.5 … count − 0.5).
    private func candleX(_ fraction: Double, _ count: Int) -> Double {
        -0.5 + fraction * Double(max(count, 1))
    }
}
