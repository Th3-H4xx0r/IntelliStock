import SwiftUI

/// A wrapping row of views — Flutter's `Wrap(spacing:runSpacing:)`. The one
/// implementation behind the markets, chat and dashboard chip rows.
///
/// - `spacing` separates views in a line; `runSpacing` separates lines
///   (it defaults to `spacing`).
/// - `alignment` (or `centered`) places each line; views in a line are
///   centred vertically.
struct FlowLayout: Layout {
    var spacing: CGFloat = 6
    var runSpacing: CGFloat?
    var alignment: HorizontalAlignment = .leading
    /// `alignment: .center`, as the chat rows spelled it.
    var centered = false

    private var lineSpacing: CGFloat { runSpacing ?? spacing }

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let lines = lines(for: proposal.width ?? .infinity, subviews: subviews)
        let width = lines.map(\.width).max() ?? 0
        let height = lines.map(\.height).reduce(0, +) + CGFloat(max(lines.count - 1, 0)) * lineSpacing
        return CGSize(width: proposal.width ?? width, height: height)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        let horizontal = centered ? .center : alignment
        var y = bounds.minY
        for line in lines(for: bounds.width, subviews: subviews) {
            var x: CGFloat
            switch horizontal {
            case .center: x = bounds.minX + (bounds.width - line.width) / 2
            case .trailing: x = bounds.maxX - line.width
            default: x = bounds.minX
            }
            for index in line.indices {
                let size = subviews[index].sizeThatFits(.unspecified)
                subviews[index].place(at: CGPoint(x: x, y: y + (line.height - size.height) / 2), proposal: ProposedViewSize(size))
                x += size.width + spacing
            }
            y += line.height + lineSpacing
        }
    }

    private struct Line {
        var indices: [Int] = []
        var width: CGFloat = 0
        var height: CGFloat = 0
    }

    private func lines(for maxWidth: CGFloat, subviews: Subviews) -> [Line] {
        var lines: [Line] = []
        var current = Line()
        for (i, view) in subviews.enumerated() {
            let size = view.sizeThatFits(.unspecified)
            let needed = current.indices.isEmpty ? size.width : current.width + spacing + size.width
            if needed > maxWidth, !current.indices.isEmpty {
                lines.append(current)
                current = Line()
            }
            current.width = current.indices.isEmpty ? size.width : current.width + spacing + size.width
            current.height = max(current.height, size.height)
            current.indices.append(i)
        }
        if !current.indices.isEmpty { lines.append(current) }
        return lines
    }
}

// The names the feature screens use. Kept as aliases of the one layout so
// the call sites (restyled separately) need no edits.
typealias MarketsFlowLayout = FlowLayout
typealias ChatFlowLayout = FlowLayout
typealias DashboardFlowLayout = FlowLayout
