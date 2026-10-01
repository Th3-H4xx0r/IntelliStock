import SwiftUI

// The small building blocks from `common_widgets.dart` (SectionHeader,
// EmptyState, LoadingState and ErrorBanner live in their own files).

/// A small labelled value tile — `StatTile`.
struct StatTile: View {
    let label: String
    let value: String
    var valueColor: Color = .primary
    var sub: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(label)
                .font(.caption2)
                .foregroundStyle(.secondary)
            Text(value)
                .font(.headline.monospacedDigit())
                .foregroundStyle(valueColor)
            if let sub {
                Text(sub)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(DS.Surface.inset, in: .rect(cornerRadius: DS.Radius.small, style: .continuous))
        .accessibilityElement(children: .combine)
    }
}

/// A coloured tag, upper-cased — `AppBadge`.
struct AppBadge: View {
    let label: String
    let color: Color

    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        Text(label.uppercased())
            .font(.caption2.weight(.bold))
            .tracking(0.5)
            .foregroundStyle(DS.Palette.onTint(color, in: colorScheme))
            .padding(.horizontal, 6)
            .padding(.vertical, 2)
            .background(color.opacity(DS.tintFill), in: .rect(cornerRadius: 5, style: .continuous))
    }
}

/// A tinted square holding a glyph, used in card headers — `IconTile`. The
/// glyph draws at half the tile size.
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
            .background(color.opacity(DS.tintFill), in: .rect(cornerRadius: DS.Radius.control, style: .continuous))
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
