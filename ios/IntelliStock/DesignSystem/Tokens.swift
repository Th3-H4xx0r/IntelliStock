import SwiftUI

/// Design tokens — spec §7. Surfaces and text use the system's semantic
/// colours directly; this file names only what the system does not.
///
/// No gradients anywhere (operator, 2026-10-01). Charts fill their area with a
/// flat `DS.chartAreaOpacity` tint of the line colour.
nonisolated enum DS {
    /// One colour, one meaning. All adapt to Light/Dark.
    enum Palette {
        static let accent = Color.accentColor
        static let success = Color.green
        static let danger = Color.red
        static let warning = Color.orange
        static let info = Color.blue
        static let teal = Color.teal
        /// Price/P&L up and down.
        static let up = Color.green
        static let down = Color.red
        /// Text and glyphs on an accent fill (prominent buttons). White on the
        /// light accent #6D28D9 (7.1:1); near-black on the dark accent
        /// #A78BFA (8:1), where white is only 2.7:1 — Flutter's `onPrimary`.
        static let onAccent = Color(uiColor: UIColor { traits in
            traits.userInterfaceStyle == .dark
                ? UIColor(red: 0.016, green: 0.016, blue: 0.047, alpha: 1)
                : .white
        })
    }

    /// The Flutter surfaces mapped onto the grouped background family
    /// (`AppColors.canvas` / `panel` / `surface`).
    enum Surface {
        /// The screen background (`canvas`).
        static let canvas = Color(uiColor: .systemGroupedBackground)
        /// Cards and grouped rows (`panel`, `GlassCard`).
        static let panel = Color(uiColor: .secondarySystemGroupedBackground)
        /// Insets inside a card (`surface`, inputs, stat tiles).
        static let inset = Color(uiColor: .tertiarySystemGroupedBackground)
    }

    enum Space {
        static let xxs: CGFloat = 2
        static let xs: CGFloat = 4
        static let s: CGFloat = 8
        static let m: CGFloat = 12
        static let l: CGFloat = 16
        static let xl: CGFloat = 20
        static let xxl: CGFloat = 28
    }

    enum Radius {
        static let card: CGFloat = 22
        static let control: CGFloat = 12
        static let small: CGFloat = 8
    }

    /// Opacity of a status colour behind status text (badges, banners).
    static let tintFill: Double = 0.15
    /// Opacity of the flat area fill under a chart line.
    static let chartAreaOpacity: Double = 0.12

    /// Padding inside a `Card` (spec 2026-10-02: 16 pt).
    static let cardPadding: CGFloat = 16
    /// Space between groups inside a `Card` (spec 2026-10-02: 12 pt).
    static let cardGroupSpacing: CGFloat = 12
    /// The dash of a chart's baseline rule — Stocks' dotted start-value line.
    static let baselineDash: [CGFloat] = [2, 3]
}
