import Charts
import SwiftUI

/// The allocation donut for crypto screens — the native form of the dashboard
/// `Sector3DChart` the Dart sheet reused (spec §7: a Swift Charts `SectorMark`
/// donut). Tap or drag a sector to select it: it grows outward and the centre
/// reads "Allocation / SYM N%", with a selection haptic. Tap the centre to
/// clear.
struct CryptoAllocationChart: View {
    let slices: [SectorSlice]
    /// Per-slice colours matching a legend; defaults to the palette by slice
    /// position, Dynamic in its own colour.
    var colors: [Color]?

    @State private var selectedAngle: Double?
    @State private var selected: String?

    var body: some View {
        if slices.isEmpty {
            Circle()
                .stroke(Color(uiColor: .systemFill), lineWidth: 28)
                .padding(14)
                .aspectRatio(1, contentMode: .fit)
                .accessibilityLabel("No allocation")
        } else {
            Chart(Array(slices.enumerated()), id: \.offset) { i, s in
                SectorMark(
                    angle: .value("Weight", s.value),
                    innerRadius: .ratio(0.62),
                    outerRadius: .ratio(selected == s.sector ? 1.0 : 0.92),
                    angularInset: 1.5
                )
                .cornerRadius(3)
                .foregroundStyle(color(i, s))
                .opacity(selected == nil || selected == s.sector ? 1 : 0.45)
            }
            .chartAngleSelection(value: $selectedAngle)
            .chartLegend(.hidden)
            .chartBackground { _ in
                centre
            }
            .aspectRatio(1, contentMode: .fit)
            .onChange(of: selectedAngle) { _, angle in
                guard let angle else { return }
                let hit = sector(at: angle)
                if hit != selected { selected = hit }
            }
            .sensoryFeedback(.selection, trigger: selected)
            .animation(.snappy, value: selected)
            .accessibilityElement(children: .ignore)
            .accessibilityLabel("Allocation")
            .accessibilityValue(slices.map { "\($0.sector) \(Int($0.pct.rounded())) percent" }.joined(separator: ", "))
        }
    }

    @ViewBuilder
    private var centre: some View {
        if let selected, let s = slices.first(where: { $0.sector == selected }) {
            VStack(spacing: 2) {
                Text("Allocation")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                HStack(spacing: 6) {
                    Text(s.sector).font(.headline)
                    Text("\(Int(s.pct.rounded()))%").font(.headline).foregroundStyle(.tint)
                }
            }
            .onTapGesture { self.selected = nil }
        }
    }

    private func color(_ i: Int, _ s: SectorSlice) -> Color {
        if let colors, i < colors.count { return colors[i] }
        return s.sector == "Dynamic" ? CryptoCatalog.dynamicColor : CryptoCatalog.color(i)
    }

    private func sector(at angle: Double) -> String? {
        var running = 0.0
        for s in slices {
            running += s.value
            if angle <= running { return s.sector }
        }
        return slices.last?.sector
    }
}
