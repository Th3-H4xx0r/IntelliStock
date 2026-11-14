import SwiftUI

/// The price line with a marker on every event, and under it the daily-move
/// strip (one bar per session, gain or loss, flagged days at full strength,
/// the rest faint). Both are drawn on one x scale, sessions evenly spaced
/// (no weekend gaps), so a marker sits exactly over its bar.
///
/// A tap picks the nearest session; within `hitSlop` of a marker it picks
/// the marker's. `onTap` gets the bar index. The line is a neutral zinc so
/// the markers carry the colour; there is no fill under it.
struct AnalysisMoveChart: View {
    let bars: [MoveBar]
    let markers: [AnalysisChartMarker]
    let selectedIndex: Int?
    let range: AnalysisRange
    let onTap: (Int) -> Void

    static let priceHeight: CGFloat = 200
    static let stripHeight: CGFloat = 48
    static let gap: CGFloat = 12
    /// Keeps a marker on the first or last session whole.
    nonisolated static let inset: CGFloat = 8
    /// The price labels' column at the trailing edge, outside the plot, so
    /// they never sit under the line or a marker. The strip leaves it empty
    /// to stay aligned.
    nonisolated static let labelGutter: CGFloat = 52
    static let markerSize: CGFloat = 10
    nonisolated static let hitSlop: CGFloat = 14
    private static let labelRowHeight: CGFloat = 20

    var body: some View {
        VStack(spacing: 0) {
            GeometryReader { geo in
                let width = geo.size.width
                VStack(spacing: Self.gap) {
                    Canvas { context, size in drawPrice(&context, size) }
                        .frame(height: Self.priceHeight)
                    Canvas { context, size in drawStrip(&context, size) }
                        .frame(height: Self.stripHeight)
                }
                .contentShape(Rectangle())
                .onTapGesture { location in
                    guard !bars.isEmpty else { return }
                    onTap(Self.index(at: location.x, width: width, count: bars.count, markers: markers.map(\.index)))
                }
            }
            .frame(height: Self.priceHeight + Self.gap + Self.stripHeight)
            ChartDateLabels(labels: dateLabels)
                .padding(.trailing, Self.labelGutter)
                .frame(height: Self.labelRowHeight, alignment: .bottom)
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Price and daily moves, \(AnalysisFormat.rangeCaption(range).lowercased())")
        .accessibilityValue(accessibilitySummary)
    }

    // MARK: Geometry (shared by both strips and the tap)

    /// The plot's width: the view's, less the label column.
    nonisolated static func plotWidth(_ width: CGFloat) -> CGFloat {
        max(width - labelGutter, 1)
    }

    /// The x of session `i` in a view `width` wide.
    nonisolated static func x(_ i: Int, count n: Int, width: CGFloat) -> CGFloat {
        let plot = plotWidth(width)
        guard n > 1 else { return plot / 2 }
        return inset + (plot - 2 * inset) * CGFloat(i) / CGFloat(n - 1)
    }

    /// The session under `x`, snapped to a marker within `hitSlop`.
    nonisolated static func index(at x: CGFloat, width: CGFloat, count n: Int, markers: [Int]) -> Int {
        guard n > 1 else { return 0 }
        let nearestMarker = markers
            .map { ($0, abs(Self.x($0, count: n, width: width) - x)) }
            .filter { $0.1 <= hitSlop }
            .min { $0.1 < $1.1 }
        if let nearestMarker { return nearestMarker.0 }
        let fraction = Double((x - inset) / max(plotWidth(width) - 2 * inset, 1))
        return fractionToIndex(fraction, n)
    }

    private var dateLabels: [String] {
        let n = bars.count
        return evenlySpacedLabelIndices(n, 4).compactMap { i in
            DartDateTime.tryParse(bars[i].date).map { formatChartDate($0, range.rawValue) }
        }
    }

    private var accessibilitySummary: String {
        let flagged = markers.count
        let last = bars.last(where: { $0.close != nil })?.close
        let moves = flagged == 1 ? "1 move marked" : "\(flagged) moves marked"
        return "Last close \(fmtMoney(last)), \(moves)"
    }

    // MARK: Price

    private func drawPrice(_ context: inout GraphicsContext, _ size: CGSize) {
        let n = bars.count
        let closes = bars.compactMap(\.close)
        guard n > 0, !closes.isEmpty else { return }
        let bounds = paddedBounds(closes, padFraction: 0.08)
        let pad: CGFloat = Self.markerSize
        func y(_ v: Double) -> CGFloat {
            pad + CGFloat(valueToY(v, bounds.min, bounds.max, Double(size.height - 2 * pad)))
        }

        // Three price gridlines across the plot, each labelled in the column.
        let plot = Self.plotWidth(size.width)
        for t in [0.15, 0.5, 0.85] {
            let v = bounds.min + (bounds.max - bounds.min) * t
            let gy = y(v)
            var line = Path()
            line.move(to: CGPoint(x: 0, y: gy))
            line.addLine(to: CGPoint(x: plot, y: gy))
            context.stroke(line, with: .color(DS.Chart.grid), lineWidth: 1)
            let label = context.resolve(
                Text(fmtMoney(v)).font(DS.Font.micro).monospacedDigit().foregroundStyle(DS.Text.muted)
            )
            context.draw(label, at: CGPoint(x: size.width, y: gy), anchor: .trailing)
        }

        // The line; a session without a close breaks it.
        var path = Path()
        var drawing = false
        for (i, bar) in bars.enumerated() {
            guard let c = bar.close else {
                drawing = false
                continue
            }
            let p = CGPoint(x: Self.x(i, count: n, width: size.width), y: y(c))
            if drawing { path.addLine(to: p) } else { path.move(to: p) }
            drawing = true
        }
        context.stroke(path, with: .color(DS.Chart.lead), style: StrokeStyle(lineWidth: 2, lineCap: .round, lineJoin: .round))

        if let selectedIndex, bars.indices.contains(selectedIndex) {
            drawHairline(&context, x: Self.x(selectedIndex, count: n, width: size.width), height: size.height)
        }

        // Markers, ringed in the page colour so overlapping ones separate.
        for marker in markers {
            guard bars.indices.contains(marker.index), let c = bars[marker.index].close else { continue }
            let center = CGPoint(x: Self.x(marker.index, count: n, width: size.width), y: y(c))
            let selected = marker.index == selectedIndex
            drawMarker(&context, at: center, style: AnalysisMarkerStyle(marker.event), size: selected ? Self.markerSize + 4 : Self.markerSize)
        }

        // The selected session's point when it carries no marker.
        if let selectedIndex, bars.indices.contains(selectedIndex), let c = bars[selectedIndex].close,
           !markers.contains(where: { $0.index == selectedIndex }) {
            let center = CGPoint(x: Self.x(selectedIndex, count: n, width: size.width), y: y(c))
            let ring = Path(ellipseIn: CGRect(x: center.x - 5.5, y: center.y - 5.5, width: 11, height: 11))
            context.fill(ring, with: .color(DS.Surface.page))
            let dot = Path(ellipseIn: CGRect(x: center.x - 3.5, y: center.y - 3.5, width: 7, height: 7))
            context.fill(dot, with: .color(DS.Chart.lead))
        }
    }

    private func drawMarker(_ context: inout GraphicsContext, at c: CGPoint, style: AnalysisMarkerStyle, size: CGFloat) {
        let r = size / 2
        let ring = Path(ellipseIn: CGRect(x: c.x - r - 2, y: c.y - r - 2, width: size + 4, height: size + 4))
        context.fill(ring, with: .color(DS.Surface.page))
        if style.isHollow {
            let outline = Path(ellipseIn: CGRect(x: c.x - r + 1, y: c.y - r + 1, width: size - 2, height: size - 2))
            context.stroke(outline, with: .color(style.color), lineWidth: 2)
        } else {
            let dot = Path(ellipseIn: CGRect(x: c.x - r, y: c.y - r, width: size, height: size))
            context.fill(dot, with: .color(style.color))
        }
    }

    private func drawHairline(_ context: inout GraphicsContext, x: CGFloat, height: CGFloat) {
        var line = Path()
        line.move(to: CGPoint(x: x, y: 0))
        line.addLine(to: CGPoint(x: x, y: height))
        context.stroke(line, with: .color(DS.Chart.scrubLine), lineWidth: 1)
    }

    // MARK: Daily-move strip

    private func drawStrip(_ context: inout GraphicsContext, _ size: CGSize) {
        let n = bars.count
        guard n > 0 else { return }
        let mid = size.height / 2
        let plot = Self.plotWidth(size.width)
        var baseline = Path()
        baseline.move(to: CGPoint(x: 0, y: mid))
        baseline.addLine(to: CGPoint(x: plot, y: mid))
        context.stroke(baseline, with: .color(DS.Chart.grid), lineWidth: 1)

        let maxAbs = max(bars.compactMap { $0.retPct.map(abs) }.max() ?? 0, 1)
        let slot = (plot - 2 * Self.inset) / CGFloat(max(n - 1, 1))
        let barWidth = min(max(slot * 0.6, 1), 6)
        let half = mid - 1
        for (i, bar) in bars.enumerated() {
            guard let r = bar.retPct, r != 0 else { continue }
            let h = max(1, CGFloat(abs(r) / maxAbs) * half)
            let x = Self.x(i, count: n, width: size.width)
            let rect = CGRect(x: x - barWidth / 2, y: r > 0 ? mid - h : mid, width: barWidth, height: h)
            let color = r > 0 ? DS.Palette.gain : DS.Palette.loss
            let shape = Path(roundedRect: rect, cornerRadius: min(barWidth / 2, 1.5))
            context.fill(shape, with: .color(color.opacity(bar.flagged ? 1 : 0.3)))
        }

        if let selectedIndex, bars.indices.contains(selectedIndex) {
            drawHairline(&context, x: Self.x(selectedIndex, count: n, width: size.width), height: size.height)
        }
    }
}
