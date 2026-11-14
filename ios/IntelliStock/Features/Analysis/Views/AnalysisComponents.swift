import SwiftUI

/// The marker colours. The app has no categorical palette (colour means
/// gain, loss or a warning everywhere else), so this screen keeps its own,
/// limited to what stays distinguishable: three hues that pass every
/// colour-blind and normal-vision check against each other on the card
/// surface, all-pairs, plus two zinc neutrals. None is the gain green or the
/// loss red, so a marker never reads as a direction.
nonisolated enum AnalysisPalette {
    /// Company news (blue).
    static let company = Color(hex: 0x3987E5)
    /// Analysts and peers (orange).
    static let street = Color(hex: 0xD95926)
    /// Macro and policy (aqua).
    static let macro = Color(hex: 0x199E70)
    /// A market-wide day.
    static let market = DS.Text.secondary
    /// No catalyst found.
    static let noCatalyst = DS.Text.muted
    /// The outline of an unexplained or pending event.
    static let open = DS.Text.secondary
    /// The outline of a failed explanation.
    static let failed = DS.Palette.loss
}

extension AnalysisCategoryFamily {
    var color: Color {
        switch self {
        case .company: AnalysisPalette.company
        case .street: AnalysisPalette.street
        case .macro: AnalysisPalette.macro
        case .market: AnalysisPalette.market
        case .noCatalyst: AnalysisPalette.noCatalyst
        case .other: AnalysisPalette.market
        }
    }
}

extension AnalysisMarkerStyle {
    /// The fill, or the outline when hollow.
    var color: Color {
        switch self {
        case .solid(let family): family.color
        case .hollow: AnalysisPalette.open
        case .failed: AnalysisPalette.failed
        }
    }

    var isHollow: Bool {
        if case .solid = self { return false }
        return true
    }
}

/// An event's marker outside the chart (rows, the sheet, chips): filled in
/// its family's colour, or an outline while unexplained.
struct AnalysisMarkerDot: View {
    let style: AnalysisMarkerStyle
    var size: CGFloat = 10

    var body: some View {
        Group {
            if style.isHollow {
                Circle().strokeBorder(style.color, lineWidth: 2)
            } else {
                Circle().fill(style.color)
            }
        }
        .frame(width: size, height: size)
        .accessibilityHidden(true)
    }
}

/// A selectable chip (a symbol, a category filter): the segmented control's
/// active look when selected, a card with a hairline otherwise.
struct AnalysisChip<Leading: View>: View {
    let label: String
    var count: Int?
    let selected: Bool
    let action: () -> Void
    @ViewBuilder var leading: Leading

    var body: some View {
        Button(action: action) {
            HStack(spacing: 6) {
                leading
                Text(label)
                    .foregroundStyle(selected ? DS.Text.primary : DS.Text.secondary)
                if let count {
                    Text(String(count))
                        .foregroundStyle(DS.Text.muted)
                }
            }
            .font(DS.Font.captionMedium)
            .monospacedDigit()
            .lineLimit(1)
            .padding(.horizontal, 12)
            .frame(minHeight: 32)
            .background(selected ? DS.Surface.raised : DS.Surface.card, in: .rect(cornerRadius: DS.Radius.control, style: .continuous))
            .overlay {
                RoundedRectangle(cornerRadius: DS.Radius.control, style: .continuous)
                    .strokeBorder(selected ? DS.Line.focus.opacity(0.5) : DS.Line.card, lineWidth: DS.Line.width)
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityAddTraits(selected ? .isSelected : [])
        .accessibilityLabel(count.map { "\(label), \($0)" } ?? label)
    }
}

extension AnalysisChip where Leading == EmptyView {
    init(label: String, count: Int? = nil, selected: Bool, action: @escaping () -> Void) {
        self.init(label: label, count: count, selected: selected, action: action) { EmptyView() }
    }
}

/// One flagged day in the Moves list: the marker, the headline (or what is
/// happening), the date and category, and the day's move with its
/// stock-specific part.
struct AnalysisEventRow: View {
    let event: MoveEvent
    let currentYear: Int

    var body: some View {
        let explained = event.isExplained
        HStack(alignment: .firstTextBaseline, spacing: 12) {
            AnalysisMarkerDot(style: AnalysisMarkerStyle(event))
                .alignmentGuide(.firstTextBaseline) { $0[VerticalAlignment.center] + 4 }
            VStack(alignment: .leading, spacing: 4) {
                // Pending reads "Explaining…"; the section header carries the spinner.
                Text(AnalysisFormat.title(event))
                    .font(DS.Font.body)
                    .foregroundStyle(explained ? DS.Text.primary : (event.status == .failed ? DS.Palette.loss : DS.Text.secondary))
                    .lineLimit(2)
                    .fixedSize(horizontal: false, vertical: true)
                Text(AnalysisFormat.metaLine(event, currentYear: currentYear))
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Text.muted)
                    .lineLimit(1)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            VStack(alignment: .trailing, spacing: 4) {
                Text(fmtPct(event.retPct))
                    .font(DS.Font.bodyMedium)
                    .foregroundStyle(dashboardChangeColor(event.retPct))
                if let specific = event.specificPct {
                    Text("Stock \(fmtPct(specific))")
                        .font(DS.Font.caption)
                        .foregroundStyle(DS.Text.muted)
                }
            }
            .monospacedDigit()
        }
        .padding(.vertical, 4)
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
        .accessibilityHint("Shows why it moved")
    }
}

/// The three parts of a move as a key-value block: the whole move, then
/// market, sector and stock-specific, each signed and coloured.
struct AnalysisSplitGrid: View {
    let move: Double?
    let market: Double?
    let sector: Double?
    let specific: Double?
    var moveLabel = "Move"

    var body: some View {
        StatGrid(columns: 2) {
            cell(moveLabel, move)
            cell("Market", market)
            cell("Sector", sector)
            cell("Stock-specific", specific)
        }
    }

    private func cell(_ label: String, _ value: Double?) -> some View {
        StatCell(label: label, value: fmtPct(value), valueColor: dashboardChangeColor(value))
    }
}
