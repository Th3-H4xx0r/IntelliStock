import SwiftUI

/// A Stocks-style grid of figures: two or three equal columns of `StatCell`s,
/// filled left to right, then top to bottom.
///
/// Use it for numeric summaries, such as key statistics, a backtest's results,
/// or cash and buying power. It sits directly on its surface: a `List` row, a
/// `DSSection`, or a hero `Card`. Never put it on a grey tile, and never put a
/// tile inside it.
///
/// At accessibility text sizes the grid drops one column (3 → 2, 2 → 1), so
/// values wrap onto more rows instead of truncating
/// (`typography.md › Supporting Dynamic Type`).
///
///     DSSection("Key statistics") {
///         StatGrid(columns: 3) {
///             StatCell(label: "Open", value: "$182.10")
///             StatCell(label: "Prev close", value: "$180.55")
///             StatCell(label: "Day P&L", value: "+$62.13", valueColor: DS.Palette.up)
///         }
///     }
///
/// Cells can come from a `ForEach`, and `if` statements may leave gaps out.
struct StatGrid<Content: View>: View {
    /// 2 or 3 (clamped to 1...3).
    let columns: Int
    var horizontalSpacing: CGFloat = 16
    var verticalSpacing: CGFloat = 14
    private let content: Content

    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

    init(
        columns: Int = 2,
        horizontalSpacing: CGFloat = 16,
        verticalSpacing: CGFloat = 14,
        @ViewBuilder content: () -> Content
    ) {
        self.columns = columns
        self.horizontalSpacing = horizontalSpacing
        self.verticalSpacing = verticalSpacing
        self.content = content()
    }

    var body: some View {
        StatGridLayout(
            columns: Self.effectiveColumns(columns, dynamicTypeSize),
            horizontalSpacing: horizontalSpacing,
            verticalSpacing: verticalSpacing
        ) {
            content
        }
    }

    /// The columns actually laid out: the request clamped to 1...3, minus one
    /// at accessibility text sizes (never below one).
    static func effectiveColumns(_ requested: Int, _ size: DynamicTypeSize) -> Int {
        let clamped = min(max(requested, 1), 3)
        return size.isAccessibilitySize ? max(clamped - 1, 1) : clamped
    }
}

/// One figure in a `StatGrid`, in the style of Stocks' key statistics.
///
/// The label is `.caption` in `.secondary`, in sentence case ("Prev close",
/// "52W high"). The value below it is `.body` with monospaced digits, and an
/// optional footnote is `.caption2` in `.secondary`. VoiceOver reads the cell
/// as one element.
///
///     StatCell(label: "Win rate", value: "61%")
///     StatCell(label: "P&L", value: "+$1,204", valueColor: DS.Palette.up, footnote: "+12.4%")
///     StatCell(label: "Updated") { RelativeTimeText(date: updated) }
struct StatCell<Value: View>: View {
    let label: String
    var footnote: String?
    private let value: Value

    /// A cell with a custom value view (a relative time, an SF Symbol and a
    /// figure). It gets `.body` and monospaced digits unless it sets its own.
    init(label: String, footnote: String? = nil, @ViewBuilder value: () -> Value) {
        self.label = label
        self.footnote = footnote
        self.value = value()
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label)
                .font(.caption)
                .foregroundStyle(.secondary)
                .lineLimit(2)
            // Two lines before shrinking: a long value (a date range) wraps
            // rather than losing its end.
            value
                .font(.body)
                .monospacedDigit()
                .lineLimit(2)
                .minimumScaleFactor(0.8)
            if let footnote {
                Text(footnote)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityElement(children: .combine)
    }
}

extension StatCell where Value == Text {
    /// A cell showing `value` as text, in `valueColor` (primary by default).
    /// Keep colour for meaning: green or red P&L, or a warning.
    init(label: String, value: String, valueColor: Color? = nil, footnote: String? = nil) {
        self.init(label: label, footnote: footnote) {
            Text(value).foregroundStyle(valueColor ?? Color.primary)
        }
    }
}

/// Equal-width columns; each row is as tall as its tallest cell, and cells
/// are pinned to the top-leading corner of their slot.
private struct StatGridLayout: Layout {
    let columns: Int
    let horizontalSpacing: CGFloat
    let verticalSpacing: CGFloat

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        guard !subviews.isEmpty else { return .zero }
        let width = proposal.width.flatMap { $0.isFinite ? $0 : nil } ?? idealWidth(subviews)
        let heights = rowHeights(subviews, columnWidth: columnWidth(width))
        let height = heights.reduce(0, +) + verticalSpacing * CGFloat(max(heights.count - 1, 0))
        return CGSize(width: width, height: height)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        let colWidth = columnWidth(bounds.width)
        var y = bounds.minY
        for (row, height) in rowHeights(subviews, columnWidth: colWidth).enumerated() {
            for col in 0..<columns {
                let index = row * columns + col
                guard index < subviews.count else { break }
                let x = bounds.minX + CGFloat(col) * (colWidth + horizontalSpacing)
                subviews[index].place(
                    at: CGPoint(x: x, y: y),
                    anchor: .topLeading,
                    proposal: ProposedViewSize(width: colWidth, height: height)
                )
            }
            y += height + verticalSpacing
        }
    }

    private func columnWidth(_ total: CGFloat) -> CGFloat {
        max((total - horizontalSpacing * CGFloat(columns - 1)) / CGFloat(columns), 0)
    }

    private func rowHeights(_ subviews: Subviews, columnWidth: CGFloat) -> [CGFloat] {
        stride(from: 0, to: subviews.count, by: columns).map { start in
            subviews[start..<min(start + columns, subviews.count)]
                .map { $0.sizeThatFits(ProposedViewSize(width: columnWidth, height: nil)).height }
                .max() ?? 0
        }
    }

    /// The width with no proposal (inside a horizontal scroll view, say):
    /// every column as wide as the widest cell.
    private func idealWidth(_ subviews: Subviews) -> CGFloat {
        let widest = subviews.map { $0.sizeThatFits(.unspecified).width }.max() ?? 0
        return widest * CGFloat(columns) + horizontalSpacing * CGFloat(columns - 1)
    }
}

#Preview("In a list") {
    List {
        Section("Key statistics") {
            StatGrid(columns: 3) {
                StatCell(label: "Open", value: "$182.10")
                StatCell(label: "Prev close", value: "$180.55")
                StatCell(label: "Volume", value: "41.2M")
                StatCell(label: "52W high", value: "$199.62")
                StatCell(label: "52W low", value: "$164.08")
                StatCell(label: "Mkt cap", value: "2.81T")
            }
        }
        Section("Results") {
            StatGrid {
                StatCell(label: "P&L", value: "+$1,204.18", valueColor: DS.Palette.up, footnote: "+12.04%")
                StatCell(label: "Win rate", value: "61%", footnote: "41 of 67 trades")
                StatCell(label: "Max drawdown", value: "−8.2%", valueColor: DS.Palette.down)
                StatCell(label: "Elapsed") { Label("3h 12m", systemImage: "clock") }
            }
        }
    }
    .listStyle(.insetGrouped)
}

#Preview("In a card") {
    ScrollView {
        Card("Today") {
            StatGrid {
                StatCell(label: "Day P&L", value: "+$62.13", valueColor: DS.Palette.up)
                StatCell(label: "Trades", value: "4")
            }
        }
        .padding()
    }
    .background(DS.Surface.canvas)
}
