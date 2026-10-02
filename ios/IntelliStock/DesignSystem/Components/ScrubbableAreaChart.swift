import Charts
import SwiftUI

/// A point drawn over the chart's area, e.g. a buy or sell — the native form
/// of the Syncfusion `markerSeries` Dart passed in. A value type: markers
/// built from the same data are equal, so re-rendering a screen never makes
/// the chart redraw.
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

/// The equity/value chart with the dashboard's look and feel —
/// `ScrubbableAreaChart` in `scrubbable_area_chart.dart`, on Swift Charts.
///
/// Hidden axes, a monotone line over a flat `DS.chartAreaOpacity` fill (no
/// gradient), date labels under the plot, an optional baseline rule, and a
/// scrub: drag across it for a hairline and a dot on the line, with a
/// selection haptic each time the snapped point changes. `onScrub` reports the
/// index, then nil when the finger lifts.
///
/// Plotted against real time, so unevenly spaced samples and `markers` stay
/// aligned; `indexed` spaces points evenly instead (no weekend or overnight
/// gaps).
struct ScrubbableAreaChart: View {
    let timestamps: [Date]
    let values: [Double]
    let lineColor: Color
    var height: CGFloat = 200
    /// A horizontal reference line, e.g. the starting equity.
    var baseline: Double?
    var markers: [ScrubbableChartMarker] = []
    var onScrub: ((Int?) -> Void)?
    /// Grow the line in on first appearance (skipped under Reduce Motion).
    var animate = true
    var indexed = false
    /// A live dot pulsing at the latest value while not scrubbing.
    var pulsingEndDot = false

    @State private var selectedX: Double?
    @State private var scrub = ScrubController(onTick: {})
    @State private var revealed = false
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
        let bounds = paddedBounds(baseline.map { values + [$0] } ?? values)
        let span = timestamps[n - 1].timeIntervalSince(timestamps[0])
        let labels = evenlySpacedLabelIndices(n, 4).map { formatChartDateBySpan(timestamps[$0], span) }
        let showValues = revealed || !animate || reduceMotion
        let sample = scrub.value.flatMap { $0.index < n ? $0 : nil }

        return VStack(spacing: 0) {
            Chart {
                ForEach(0..<n, id: \.self) { i in
                    let v = showValues ? values[i] : bounds.min
                    AreaMark(
                        x: .value("Time", x(i)),
                        yStart: .value("Floor", bounds.min),
                        yEnd: .value("Value", v)
                    )
                    .foregroundStyle(lineColor.opacity(DS.chartAreaOpacity))
                    .interpolationMethod(.monotone)

                    LineMark(x: .value("Time", x(i)), y: .value("Value", v))
                        .foregroundStyle(lineColor)
                        .lineStyle(StrokeStyle(lineWidth: 2, lineCap: .round, lineJoin: .round))
                        .interpolationMethod(.monotone)
                }

                if let baseline {
                    RuleMark(y: .value("Baseline", baseline))
                        .foregroundStyle(Color(uiColor: .tertiaryLabel))
                        .lineStyle(StrokeStyle(lineWidth: 1))
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
                } else if pulsingEndDot, showValues {
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

            ChartDateLabels(labels: labels)
                .frame(height: Self.labelRowHeight, alignment: .bottom)
        }
        .frame(height: height)
        .onChange(of: selectedX) { _, newValue in
            select(newValue, count: n)
        }
        .sensoryFeedback(.selection, trigger: scrub.value?.index) { _, new in new != nil }
        .onAppear {
            guard !revealed else { return }
            if animate, !reduceMotion {
                withAnimation(.easeOut(duration: 0.7)) { revealed = true }
            } else {
                revealed = true
            }
        }
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
