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
    var valueColor: Color = .primary
    var sub: String?

    var body: some View {
        StatCell(label: label, value: value, valueColor: valueColor, footnote: sub)
    }
}

/// A coloured tag — `AppBadge`, in the app's one badge style (see
/// `StatusBadge`). Dart upper-cased the label; the native form shows it in
/// sentence case instead ("real money" → "Real money", "AI" stays "AI"),
/// because the redesign allows no upper case outside tickers and acronyms.
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

/// A tinted square holding a glyph — `IconTile`. The glyph draws at half the
/// tile size, and the continuous corners scale with it (30 % of the side, so
/// the 40 pt default keeps its 12 pt radius).
///
/// The redesign allows icon tiles only as the leading image of a row in a
/// navigation list (More, Settings) or an `EntityRow`, never in a card or
/// section header.
struct IconTile<Glyph: View>: View {
    var color: Color = DS.Palette.accent
    var size: CGFloat = 40
    private let glyph: Glyph

    /// `IconTile.custom`: any glyph (a brand mark, say) at the icon's size.
    init(color: Color = DS.Palette.accent, size: CGFloat = 40, @ViewBuilder glyph: () -> Glyph) {
        self.color = color
        self.size = size
        self.glyph = glyph()
    }

    var glyphSize: CGFloat { size * 0.5 }

    var body: some View {
        glyph
            .frame(width: glyphSize, height: glyphSize)
            .foregroundStyle(color)
            .frame(width: size, height: size)
            .background(color.opacity(DS.tintFill), in: .rect(cornerRadius: size * 0.3, style: .continuous))
            .accessibilityHidden(true)
    }
}

extension IconTile where Glyph == IconTileSymbol {
    /// A tile around an SF Symbol (`Symbol.named(<material name>)`).
    init(systemImage: String, color: Color = DS.Palette.accent, size: CGFloat = 40) {
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

/// A flat placeholder block for skeleton layouts — `Skeleton` without the
/// shimmer gradient. Prefer `.redacted(reason: .placeholder)` over sample
/// content; use this only where there is no content to redact.
struct Skeleton: View {
    var width: CGFloat?
    var height: CGFloat = 14
    var radius: CGFloat = 8

    /// A short text line.
    static func line(width: CGFloat? = nil, height: CGFloat = 12) -> Skeleton {
        Skeleton(width: width, height: height, radius: 6)
    }

    /// A circle (avatar or icon tile).
    static func circle(_ size: CGFloat) -> Skeleton {
        Skeleton(width: size, height: size, radius: size / 2)
    }

    var body: some View {
        RoundedRectangle(cornerRadius: radius, style: .continuous)
            .fill(Color(uiColor: .systemFill))
            .frame(width: width, height: height)
            .frame(maxWidth: width == nil ? .infinity : nil, alignment: .leading)
            .accessibilityHidden(true)
    }
}
