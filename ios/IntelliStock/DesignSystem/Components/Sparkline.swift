import Charts
import SwiftUI

/// A tiny price line for a row — the dashboard's `_MiniSpark`, moved into the
/// design system (`DashboardMiniSpark` is a typealias of it).
///
/// A Swift Charts line, 1.5 pt wide and monotone, with no axes, grid or
/// legend. It is green when the last value is at or above the first and red
/// otherwise, unless you pass `color`. An optional `baseline` draws Stocks'
/// dotted reference line (the previous close, say). It draws itself in from
/// the left on first appearance (`chartDrawIn`; skipped under Reduce Motion,
/// or with `animated: false`); give it a new `.id` to replay. Decorative: the
/// row's text carries the numbers for VoiceOver.
///
///     Sparkline(values: closes).frame(width: 60, height: 24)
struct Sparkline: View {
    let values: [Double]
    var height: CGFloat = 28
    var color: Color?
    var baseline: Double?
    var animated = true

    init(values: [Double], height: CGFloat = 28, color: Color? = nil, baseline: Double? = nil, animated: Bool = true) {
        self.values = values
        self.height = height
        self.color = color
        self.baseline = baseline
        self.animated = animated
    }

    /// True when the line ends at or above where it started.
    nonisolated static func isUp(_ values: [Double]) -> Bool {
        guard let first = values.first, let last = values.last else { return true }
        return last >= first
    }

    var body: some View {
        if values.count < 2 {
            Color.clear.frame(height: height)
        } else {
            let tint = color ?? (Self.isUp(values) ? DS.Palette.up : DS.Palette.down)
            let all = baseline.map { values + [$0] } ?? values
            let lo = all.min()!
            let hi = all.max()!
            let span = abs(hi - lo) < 1e-9 ? 1 : hi - lo
            Chart {
                if let baseline {
                    RuleMark(y: .value("Baseline", baseline))
                        .foregroundStyle(Color(uiColor: .tertiaryLabel))
                        .lineStyle(StrokeStyle(lineWidth: 1, dash: DS.baselineDash))
                }
                ForEach(values.indices, id: \.self) { i in
                    LineMark(x: .value("i", i), y: .value("v", values[i]))
                        .foregroundStyle(tint)
                        .lineStyle(StrokeStyle(lineWidth: 1.5, lineCap: .round, lineJoin: .round))
                        .interpolationMethod(.monotone)
                }
            }
            .chartXAxis(.hidden)
            .chartYAxis(.hidden)
            .chartLegend(.hidden)
            .chartXScale(domain: 0...(values.count - 1))
            .chartYScale(domain: lo...(lo + span))
            .chartPlotStyle { $0.padding(.vertical, 2) }
            .chartDrawIn(duration: ChartDrawIn.sparkDuration, enabled: animated, bleed: 2)
            .frame(height: height)
            .accessibilityHidden(true)
        }
    }
}

/// A circular allocation ring — the dashboard's `_AllocationRing` /
/// `_DiversityGauge`, moved into the design system (`DashboardAllocationRing`
/// is a typealias of it). The arc is this item's share of the whole, in a flat
/// stroke over a `systemFill` track, with the percentage in the centre
/// (`showsLabel`). For a 24 pt row ring use `MiniAllocationRing`.
struct AllocationRing: View {
    let fraction: Double
    let color: Color
    var size: CGFloat = 44
    var lineWidth: CGFloat = 3.5
    var labelColor: Color?
    var showsLabel = true

    init(
        fraction: Double,
        color: Color,
        size: CGFloat = 44,
        lineWidth: CGFloat = 3.5,
        labelColor: Color? = nil,
        showsLabel: Bool = true
    ) {
        self.fraction = fraction
        self.color = color
        self.size = size
        self.lineWidth = lineWidth
        self.labelColor = labelColor
        self.showsLabel = showsLabel
    }

    /// `_AllocationRing._label`: "0%", "<1%", else the whole percentage.
    nonisolated static func percentLabel(_ fraction: Double) -> String {
        let pct = fraction * 100
        if pct <= 0 { return "0%" }
        if pct < 1 { return "<1%" }
        return "\(Int(pct.rounded()))%"
    }

    var body: some View {
        let f = min(max(fraction, 0), 1)
        ZStack {
            Circle()
                .stroke(Color(uiColor: .systemFill), lineWidth: lineWidth)
            Circle()
                .trim(from: 0, to: f)
                .stroke(color, style: StrokeStyle(lineWidth: lineWidth, lineCap: .round))
                .rotationEffect(.degrees(-90))
            if showsLabel {
                Text(Self.percentLabel(fraction))
                    .font(.caption2.weight(.bold))
                    .monospacedDigit()
                    .dsMinimumScaleFactor(0.7, textStyle: .caption2)
                    .lineLimit(1)
                    .foregroundStyle(labelColor ?? color)
                    .padding(.horizontal, 4)
            }
        }
        .padding(lineWidth / 2)
        .frame(width: size, height: size)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(Self.percentLabel(fraction)) of portfolio")
    }
}

/// The 24 pt allocation ring that leads a holdings row: the arc only, with no
/// centre label (the row's text carries the figures; VoiceOver still hears
/// "N% of portfolio").
struct MiniAllocationRing: View {
    let fraction: Double
    let color: Color
    var size: CGFloat = 24

    init(fraction: Double, color: Color, size: CGFloat = 24) {
        self.fraction = fraction
        self.color = color
        self.size = size
    }

    var body: some View {
        AllocationRing(fraction: fraction, color: color, size: size, lineWidth: 3, showsLabel: false)
    }
}

#Preview {
    List {
        HStack(spacing: 12) {
            MiniAllocationRing(fraction: 0.41, color: DS.Palette.up)
            Text("GLD").font(.headline)
            Sparkline(values: [10, 11, 10.5, 12, 12.4, 12.1, 13], baseline: 10.8)
                .frame(width: 60, height: 24)
            Spacer()
            EntityRowValue("$2,031.08", detail: "+2.08%", detailColor: DS.Palette.up)
        }
        HStack(spacing: 12) {
            MiniAllocationRing(fraction: 0.004, color: DS.Palette.teal)
            Text("TQQQ").font(.headline)
            Sparkline(values: [13, 12.5, 12.8, 11, 10.2, 10.9, 9.8])
                .frame(width: 60, height: 24)
            Spacer()
            EntityRowValue("$1,823.74", detail: "−0.68%", detailColor: DS.Palette.down)
        }
        AllocationRing(fraction: 0.62, color: DS.Palette.accent, size: 46, lineWidth: 5, labelColor: .primary)
    }
}
