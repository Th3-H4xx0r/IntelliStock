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
}
