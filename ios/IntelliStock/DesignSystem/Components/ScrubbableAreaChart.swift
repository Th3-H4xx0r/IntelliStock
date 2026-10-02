import Charts
import SwiftUI

/// A point drawn over the chart's area, e.g. a buy or sell — the native form
/// of the Syncfusion `markerSeries` Dart passed in. A value type: markers
/// built from the same data are equal, so re-rendering a screen never makes
/// the chart redraw.
/// A labelled horizontal level behind the line — a strike, a breakeven.
struct ScrubbableChartLevel: Hashable {
    let value: Double
    let label: String
    let color: Color
}

/// A tinted band of prices behind the line, from `low` to `high` (nil runs
/// to the plot's edge), with an optional caption in its corner.
struct ScrubbableChartBand: Hashable {
    let low: Double?
    let high: Double?
    let color: Color
    var label: String?
}

struct ScrubbableChartMarker: Identifiable, Hashable {
    /// Derived from the contents, so it is stable across renders.
    var id: Int { hashValue }
    let date: Date
    let value: Double
    let color: Color

    init(date: Date, value: Double, color: Color) {
        self.date = date
        self.value = value
        self.color = color
    }
}

/// The equity/value chart in the app's one chart style (spec 2026-10-02) —
/// `ScrubbableAreaChart` in `scrubbable_area_chart.dart`, on Swift Charts.
///
/// - A 2 pt monotone line over a flat `DS.chartAreaOpacity` fill (never a
///   gradient), with hidden axes.
/// - One gridline only: Stocks' dotted baseline at the start value, or at
///   `baseline` when given (the starting equity, zero P&L). Set
///   `showsBaseline: false` to drop it.
/// - At most four date labels under the plot, in `.caption2` `.secondary`.
/// - A scrub: drag across it for a hairline and a dot on the line, with a
///   selection haptic each time the snapped point changes. `onScrub` reports
///   the index, then nil when the finger lifts.
/// - It draws itself in from the leading edge (`chartDrawIn`) when it first
///   appears and whenever `drawInKey` changes; polls that only append or
///   update points leave it still.
///
/// Plotted against real time, so unevenly spaced samples and `markers` stay
/// aligned; `indexed` spaces points evenly instead (no weekend or overnight
/// gaps). Give it horizontal margins (a `Card`, or the list's insets): no chart
/// runs edge to edge.
struct ScrubbableAreaChart: View {
    let timestamps: [Date]
    let values: [Double]
    let lineColor: Color
    var height: CGFloat = 200
    /// Where the dotted baseline sits, e.g. the starting equity. nil puts it
    /// at the first value.
    var baseline: Double?
    var markers: [ScrubbableChartMarker] = []
    var onScrub: ((Int?) -> Void)?
    /// Draw the line in from the leading edge on first appearance (skipped
    /// under Reduce Motion).
    var animate = true
    /// What names the series (its range, its account): a new key draws the
    /// chart in again. Leave it out for a chart whose series never changes in
    /// place.
    var drawInKey = AnyHashable(0)
    var indexed = false
    /// A live dot pulsing at the latest value while not scrubbing.
    var pulsingEndDot = false
    /// Draw the dotted baseline.
    var showsBaseline = true
    /// Labelled levels and tinted bands behind the line (an option's strike
    /// and its outcomes). Both widen the y range to stay in view.
    var levels: [ScrubbableChartLevel] = []
    var bands: [ScrubbableChartBand] = []

    @State private var selectedX: Double?
    @State private var scrub = ScrubController(onTick: {})
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    private static let labelRowHeight: CGFloat = 20

    var body: some View {
        let n = values.count
        if n == 0 || timestamps.count != n {
            Color.clear.frame(height: height)
        } else {
            chart(count: n)
        }
    }

    // MARK: Chart

    private func chart(count n: Int) -> some View {
        let plotHeight = min(max(height - Self.labelRowHeight, 40), height)
        let bounds = paddedBounds((baseline.map { values + [$0] } ?? values) + levels.map(\.value))
        let span = timestamps[n - 1].timeIntervalSince(timestamps[0])
        let labels = evenlySpacedLabelIndices(n, 4).map { formatChartDateBySpan(timestamps[$0], span) }
        let sample = scrub.value.flatMap { $0.index < n ? $0 : nil }

        return VStack(spacing: 0) {
            Chart {
                ForEach(bands, id: \.self) { band in
                    RectangleMark(
                        xStart: .value("Start", x(0)),
                        xEnd: .value("End", x(n - 1)),
                        yStart: .value("Low", band.low ?? bounds.min),
                        yEnd: .value("High", band.high ?? bounds.max)
                    )
                    .foregroundStyle(band.color.opacity(0.10))
                    .annotation(position: .overlay, alignment: band.high == nil ? .topLeading : .bottomLeading) {
                        if let label = band.label {
                            Text(label)
                                .font(.caption2.weight(.semibold))
                                .foregroundStyle(band.color)
                                .padding(4)
                        }
                    }
                }
                ForEach(levels, id: \.self) { level in
                    RuleMark(y: .value("Level", level.value))
                        .foregroundStyle(level.color)
                        .lineStyle(StrokeStyle(lineWidth: 1.5, dash: [5, 4]))
                        .annotation(position: .top, alignment: .trailing, spacing: 2) {
                            Text(level.label)
                                .font(.caption2.weight(.semibold))
                                .foregroundStyle(level.color)
                        }
                }
                // Behind the line: the one gridline, Stocks' dotted start value.
                if showsBaseline {
                    RuleMark(y: .value("Baseline", baseline ?? values[0]))
                        .foregroundStyle(Color(uiColor: .tertiaryLabel))
                        .lineStyle(StrokeStyle(lineWidth: 1, dash: DS.baselineDash))
                }

                ForEach(0..<n, id: \.self) { i in
                    AreaMark(
                        x: .value("Time", x(i)),
                        yStart: .value("Floor", bounds.min),
                        yEnd: .value("Value", values[i])
                    )
                    .foregroundStyle(lineColor.opacity(DS.chartAreaOpacity))
                    .interpolationMethod(.monotone)

                    LineMark(x: .value("Time", x(i)), y: .value("Value", values[i]))
                        .foregroundStyle(lineColor)
                        .lineStyle(StrokeStyle(lineWidth: 2, lineCap: .round, lineJoin: .round))
                        .interpolationMethod(.monotone)
                }

                // By position: two identical markers (same time, price and
                // colour) must both draw.
                ForEach(markers.indices, id: \.self) { index in
                    let marker = markers[index]
                    PointMark(x: .value("Time", markerX(marker, count: n)), y: .value("Value", marker.value))
                        .foregroundStyle(marker.color)
                        .symbolSize(64)
                }

                if let sample {
                    RuleMark(x: .value("Time", x(sample.index)))
                        .foregroundStyle(lineColor.opacity(0.55))
                        .lineStyle(StrokeStyle(lineWidth: 1.2))
                    PointMark(x: .value("Time", x(sample.index)), y: .value("Value", values[sample.index]))
                        .symbol { ScrubDot(color: lineColor) }
                } else if pulsingEndDot {
                    PointMark(x: .value("Time", x(n - 1)), y: .value("Value", values[n - 1]))
                        .symbol { LiveEndDot(color: lineColor, pulsing: !reduceMotion) }
                }
            }
            .chartXScale(domain: xDomain(count: n))
            .chartYScale(domain: bounds.min...bounds.max)
            .chartXAxis(.hidden)
            .chartYAxis(.hidden)
            .chartLegend(.hidden)
            .chartXSelection(value: $selectedX)
            .frame(height: plotHeight)
            .chartDrawIn(trigger: drawInKey, enabled: animate, interacting: selectedX != nil)

            ChartDateLabels(labels: labels)
                .frame(height: Self.labelRowHeight, alignment: .bottom)
        }
        .frame(height: height)
        .onChange(of: selectedX) { _, newValue in
            select(newValue, count: n)
        }
        .sensoryFeedback(.selection, trigger: scrub.value?.index) { _, new in new != nil }
    }

    // MARK: Geometry

    /// The plotted x of point `i`: its index when `indexed`, else epoch seconds.
    private func x(_ i: Int) -> Double {
        indexed ? Double(i) : timestamps[i].timeIntervalSince1970
    }

    private func xDomain(count n: Int) -> ClosedRange<Double> {
        let lo = x(0)
        let hi = x(n - 1)
        return lo...(hi > lo ? hi : lo + 1)
    }

    private func markerX(_ marker: ScrubbableChartMarker, count n: Int) -> Double {
        guard indexed else { return marker.date.timeIntervalSince1970 }
        let first = timestamps[0].timeIntervalSince1970
        let last = timestamps[n - 1].timeIntervalSince1970
        let fraction = last > first ? (marker.date.timeIntervalSince1970 - first) / (last - first) : 0
        return Double(nearestIndexByTime(timestamps, fraction))
    }

    /// Snaps a raw selection to a data point, ticking only when the index
    /// changes, as the Dart `_onDrag` did.
    private func select(_ selection: Double?, count n: Int) {
        guard let selection else {
            if scrub.value != nil {
                scrub.clear()
                onScrub?(nil)
            }
            return
        }
        let domain = xDomain(count: n)
        let fraction = min(max((selection - domain.lowerBound) / (domain.upperBound - domain.lowerBound), 0), 1)
        let index: Int
        let hairline: Double
        if indexed {
            index = fractionToIndex(fraction, n)
            hairline = indexToFraction(index, n)
        } else {
            index = nearestIndexByTime(timestamps, fraction)
            hairline = timeFractionOf(timestamps, index)
        }
        let previous = scrub.value?.index
        scrub.update(index, hairline)
        if index != previous { onScrub?(index) }
    }
}

/// The scrub dot: solid, ringed in the background colour so it reads on the
/// line in both appearances. No glow.
private struct ScrubDot: View {
    let color: Color

    var body: some View {
        Circle()
            .fill(color)
            .frame(width: 9, height: 9)
            .overlay(Circle().stroke(Color(uiColor: .systemBackground), lineWidth: 2))
    }
}

/// The latest-value dot with an expanding ring that fades — a "live" marker,
/// drawn as an outline rather than a glow, and still under Reduce Motion.
private struct LiveEndDot: View {
    let color: Color
    let pulsing: Bool

    var body: some View {
        ZStack {
            if pulsing {
                Circle()
                    .stroke(color, lineWidth: 1.5)
                    .frame(width: 8, height: 8)
                    .phaseAnimator([0.0, 1.0]) { ring, t in
                        ring.scaleEffect(1 + t * 2.5).opacity(1 - t)
                    } animation: { t in
                        t == 1 ? .easeOut(duration: 1.6) : .linear(duration: 0)
                    }
            }
            Circle()
                .fill(color)
                .frame(width: 8, height: 8)
                .overlay(Circle().stroke(Color(uiColor: .systemBackground), lineWidth: 1.5))
        }
        .frame(width: 32, height: 32)
    }
}

/// Evenly spaced x-axis labels under a plot (first leading, last trailing) —
/// `ChartDateLabels` in `chart_decorations.dart`.
struct ChartDateLabels: View {
    let labels: [String]

    var body: some View {
        if !labels.isEmpty {
            HStack(spacing: 0) {
                ForEach(labels.indices, id: \.self) { i in
                    if i > 0 { Spacer(minLength: 4) }
                    Text(labels[i])
                        .lineLimit(1)
                }
            }
            .font(.caption2)
            .monospacedDigit()
            .foregroundStyle(.secondary)
            .padding(.top, 6)
            .accessibilityHidden(true)
        }
    }
}

#Preview {
    let start = Date(timeIntervalSince1970: 1_790_000_000)
    let values = (0..<60).map { i in 5_800 + 60 * sin(Double(i) / 6) + Double(i) * 1.5 }
    return ScrollView {
        Card {
            VStack(alignment: .leading, spacing: DS.cardGroupSpacing) {
                HeroValueHeader(fmtMoney(values.last), change: "+$62.13 (+1.07%)", direction: .up, status: "Markets closed")
                ScrubbableAreaChart(
                    timestamps: values.indices.map { start.addingTimeInterval(Double($0) * 3_600) },
                    values: values,
                    lineColor: DS.Palette.up,
                    height: 200
                )
            }
        }
        .padding()
    }
    .background(DS.Surface.canvas)
}
