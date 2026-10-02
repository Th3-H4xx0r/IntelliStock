import Charts
import SwiftUI

/// The sector-allocation selection rules behind the donut — what
/// `_Sector3DChartState` tracked (`_selected`, `_advanceFlat`), kept pure
/// so they are testable.
nonisolated struct DashboardSectorSelection: Equatable, Sendable {
    let slices: [SectorSlice]
    /// The highlighted sector; the largest (index 0) to start.
    private(set) var selected = 0

    /// Horizontal travel per step of a swipe (`step = 44.0`).
    static let swipeStep: Double = 44

    init(slices: [SectorSlice], selected: Int = 0) {
        self.slices = slices
        self.selected = slices.isEmpty ? 0 : min(max(selected, 0), slices.count - 1)
    }

    /// Steps the highlight by `delta`, wrapping. Returns whether it moved.
    @discardableResult
    mutating func advance(_ delta: Int) -> Bool {
        let n = slices.count
        if n == 0 { return false }
        var next = (selected + delta) % n
        if next < 0 { next += n }
        if next == selected { return false }
        selected = next
        return true
    }

    /// Selects `index` (a tap on a sector or legend row).
    @discardableResult
    mutating func select(_ index: Int) -> Bool {
        guard slices.indices.contains(index), index != selected else { return false }
        selected = index
        return true
    }

    /// The slice under a `chartAngleSelection` value (a running total of
    /// `pct` from the top of the ring).
    func index(forAngleValue value: Double) -> Int? {
        var running = 0.0
        for (i, s) in slices.enumerated() {
            running += s.pct
            if value <= running { return i }
        }
        return slices.isEmpty ? nil : slices.count - 1
    }

    /// The whole steps a horizontal drag of `dx` points makes.
    static func steps(forDrag dx: Double) -> Int {
        Int((dx / swipeStep).rounded(.towardZero))
    }

    /// The centre readout: `Allocation` over `NAME  N%`.
    var caption: String { "Allocation" }
    var sectorName: String { slices.indices.contains(selected) ? slices[selected].sector : "" }
    var percentText: String {
        slices.indices.contains(selected) ? "\(Int(slices[selected].pct.rounded()))%" : ""
    }
}

/// The sector allocation donut — `Sector3DChart` in its native form (spec
/// §7): a Swift Charts `SectorMark` ring in the accent, the selected sector
/// grown outward, the centre reading `Allocation` / `NAME N%`. Tap a sector
/// to select it; swipe sideways to step through sectors with a selection
/// haptic. The per-slice percentages are a legend under the ring.
struct DashboardSectorDonut: View {
    let slices: [SectorSlice]

    @State private var selection: DashboardSectorSelection
    @State private var angleValue: Double?
    @State private var dragSteps = 0

    init(slices: [SectorSlice]) {
        self.slices = slices
        _selection = State(initialValue: DashboardSectorSelection(slices: slices))
    }

    var body: some View {
        if slices.isEmpty {
            Color.clear.frame(height: 8)
        } else {
            VStack(spacing: 16) {
                donut
                legend
            }
            .onChange(of: slices) { _, new in
                selection = DashboardSectorSelection(slices: new, selected: selection.selected)
            }
        }
    }

    private var donut: some View {
        let n = slices.count
        return Chart(Array(slices.enumerated()), id: \.offset) { i, slice in
            let isSelected = i == selection.selected
            SectorMark(
                angle: .value("Share", slice.pct),
                innerRadius: .ratio(0.62),
                outerRadius: .ratio(isSelected ? 1.0 : 0.9),
                angularInset: 1.5
            )
            .cornerRadius(4)
            .foregroundStyle(DS.Palette.accent.opacity(isSelected ? 1 : shade(i, of: n)))
            .accessibilityLabel(slice.sector)
            .accessibilityValue("\(Int(slice.pct.rounded()))%")
        }
        .chartLegend(.hidden)
        .chartAngleSelection(value: $angleValue)
        .chartBackground { proxy in
            GeometryReader { geo in
                if let frame = proxy.plotFrame {
                    let rect = geo[frame]
                    VStack(spacing: 2) {
                        Text(selection.caption)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                        Text(selection.sectorName)
                            .font(.subheadline.weight(.semibold))
                            .lineLimit(1)
                            .minimumScaleFactor(0.7)
                        Text(selection.percentText)
                            .font(.title2.weight(.heavy).monospacedDigit())
                            .foregroundStyle(.tint)
                            .contentTransition(.numericText())
                    }
                    .frame(width: rect.width * 0.5)
                    .position(x: rect.midX, y: rect.midY)
                }
            }
        }
        .frame(height: 220)
        .animation(.snappy, value: selection.selected)
        .onChange(of: angleValue) { _, value in
            guard let value, let i = selection.index(forAngleValue: value) else { return }
            selection.select(i)
        }
        .simultaneousGesture(
            DragGesture(minimumDistance: 12)
                .onChanged { g in
                    guard abs(g.translation.width) > abs(g.translation.height) else { return }
                    let steps = DashboardSectorSelection.steps(forDrag: g.translation.width)
                    if steps != dragSteps {
                        selection.advance(steps - dragSteps)
                        dragSteps = steps
                    }
                }
                .onEnded { _ in dragSteps = 0 }
        )
        .sensoryFeedback(.selection, trigger: selection.selected)
        .accessibilityElement(children: .contain)
        .accessibilityAdjustableAction { direction in
            switch direction {
            case .increment: selection.advance(1)
            case .decrement: selection.advance(-1)
            @unknown default: break
            }
        }
    }

    /// Every wedge is the accent; neighbours read apart by strength, larger
    /// sectors stronger (the Dart brightness ramp, flat).
    private func shade(_ i: Int, of n: Int) -> Double {
        n <= 1 ? 0.58 : 0.30 + 0.42 * (1 - Double(i) / Double(n - 1))
    }

    private var legend: some View {
        VStack(spacing: 0) {
            ForEach(Array(slices.enumerated()), id: \.offset) { i, slice in
                Button {
                    selection.select(i)
                } label: {
                    HStack(spacing: 10) {
                        Circle()
                            .fill(DS.Palette.accent.opacity(i == selection.selected ? 1 : shade(i, of: slices.count)))
                            .frame(width: 8, height: 8)
                        Text(slice.sector)
                            .font(.caption)
                            .foregroundStyle(i == selection.selected ? .primary : .secondary)
                            .frame(maxWidth: .infinity, alignment: .leading)
                        Text("\(Int(slice.pct.rounded()))%")
                            .font(.caption.weight(.bold).monospacedDigit())
                            .foregroundStyle(i == selection.selected ? AnyShapeStyle(.tint) : AnyShapeStyle(.secondary))
                    }
                    .padding(.vertical, 5)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityAddTraits(i == selection.selected ? .isSelected : [])
            }
        }
    }
}
