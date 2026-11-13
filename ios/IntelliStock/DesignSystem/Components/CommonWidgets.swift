import SwiftUI

// The small building blocks from `common_widgets.dart` (SectionHeader,
// EmptyState, LoadingState and ErrorBanner live in their own files).

/// A labelled value — `StatTile`. It now draws exactly as a `StatCell`
/// (a caption label over a body value, with no grey tile behind it: the
/// 2026-10-02 spec bans grey tiles inside cards), so existing grids read Stocks
/// style. New code uses `StatGrid` and `StatCell` directly.
struct StatTile: View {
    let label: String
    let value: String
    var valueColor: Color = DS.Text.primary
    var sub: String?

    var body: some View {
        StatCell(label: label, value: value, valueColor: valueColor, footnote: sub)
    }
}

/// A tag — `AppBadge`. The web has no pills, so it is quiet 12 pt text
/// (`dsBadge`: gain, loss or amber keep their colour, anything else is
/// zinc-400), in sentence case ("real money" → "Real money", "AI" stays "AI").
struct AppBadge: View {
    let label: String
    let color: Color

    var body: some View {
        Text(label.dsSentenceCased)
            .dsBadge(color)
    }
}

extension String {
    /// The first character upper-cased and the rest left alone: "running" →
    /// "Running", "approved ½" → "Approved ½", "AI" → "AI".
    nonisolated var dsSentenceCased: String {
        guard let first else { return self }
        return first.uppercased() + dropFirst()
    }
}

/// A leading glyph for a row — `IconTile`. The web has no icon tiles, so this
/// is the bare glyph in zinc-500 (`color` is ignored: icons take their text
/// role's colour), centred in a `size`-wide column so rows still line up.
/// The glyph draws at half of `size`, between 14 and 18 pt.
struct IconTile<Glyph: View>: View {
    var color: Color = DS.Text.muted
    var size: CGFloat = 40
    private let glyph: Glyph

    /// `IconTile.custom`: any glyph (a brand mark, say) at the icon's size.
    init(color: Color = DS.Text.muted, size: CGFloat = 40, @ViewBuilder glyph: () -> Glyph) {
        self.color = color
        self.size = size
        self.glyph = glyph()
    }

    var glyphSize: CGFloat { min(max(size * 0.5, 14), 18) }

    var body: some View {
        glyph
            .frame(width: glyphSize, height: glyphSize)
            .foregroundStyle(DS.Text.muted)
            .frame(width: size, height: size)
            .accessibilityHidden(true)
    }
}

extension IconTile where Glyph == IconTileSymbol {
    /// A tile around an SF Symbol (`Symbol.named(<material name>)`).
    init(systemImage: String, color: Color = DS.Text.muted, size: CGFloat = 40) {
        self.init(color: color, size: size) { IconTileSymbol(systemImage: systemImage) }
    }
}

/// The symbol inside an `IconTile`, scaled to fit the glyph box.
struct IconTileSymbol: View {
    let systemImage: String

    var body: some View {
        Image(systemName: systemImage)
            .resizable()
            .scaledToFit()
    }
}

/// A static skeleton block (`ui-skel`): the raised colour, 4 pt corners, no
/// shimmer and no pulse. Shape the loading state like the content: a 12 pt
/// line for a label, 20 pt for a figure, the chart's height for a chart.
struct Skeleton: View {
    var width: CGFloat?
    var height: CGFloat = 14
    var radius: CGFloat = DS.Radius.tiny

    /// A short text line.
    static func line(width: CGFloat? = nil, height: CGFloat = 12) -> Skeleton {
        Skeleton(width: width, height: height, radius: DS.Radius.tiny)
    }

    /// A circle (avatar or icon tile).
    static func circle(_ size: CGFloat) -> Skeleton {
        Skeleton(width: size, height: size, radius: size / 2)
    }

    var body: some View {
        RoundedRectangle(cornerRadius: radius, style: .continuous)
            .fill(DS.Surface.raised)
            .frame(width: width, height: height)
            .frame(maxWidth: width == nil ? .infinity : nil, alignment: .leading)
            .accessibilityHidden(true)
    }
}
