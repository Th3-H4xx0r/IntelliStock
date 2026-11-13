import SwiftUI

/// Design tokens: the web app's (`frontend/tailwind.config.js` and the `ui-*`
/// classes in `frontend/src/style.css`), so the iPhone app reads as the same
/// product. Dark only, one graphite ramp, no hue in the chrome.
///
/// - Surfaces step page → card → raised; depth is a surface step plus a
///   hairline, never a shadow, glass or material.
/// - Text is the zinc ramp (`DS.Text`).
/// - Colour carries meaning only: gain and loss for signed money and
///   success/error, loss for destructive actions, amber for a warning or
///   paused dot. Nothing else on screen has a hue.
///
/// Type is Inter (`DS.Font`, in Typography.swift). Every component in
/// `DesignSystem/Components` mirrors one `ui-*` class; use them rather than
/// re-typing these values at a call site.
nonisolated enum DS {
    /// Meaning colours, plus the off-white accent.
    enum Palette {
        /// The off-white `primary` (#ededed): the primary button's fill,
        /// selection and emphasis. The app's global tint.
        static let accent = Color(hex: 0xEDEDED)
        /// Text and glyphs on an `accent` fill: the page colour.
        static let onAccent = Color(hex: 0x0F0F10)
        /// Gains, success, "live" (#4cc38a).
        static let gain = Color(hex: 0x4CC38A)
        /// Losses, errors, destructive actions (#e5484d).
        static let loss = Color(hex: 0xE5484D)
        /// Tailwind amber-400 (#fbbf24): a warning or paused dot, or a small
        /// warning word such as "Live money". Never a fill.
        static let amber = Color(hex: 0xFBBF24)

        // Legacy names, kept so feature code compiles; they resolve to the
        // tokens above. Prefer the token names in new code.
        static let success = gain
        static let danger = loss
        static let warning = amber
        /// There is no blue: "info" is plain secondary text.
        static let info = Text.secondary
        static let teal = Text.secondary
        static let up = gain
        static let down = loss

        /// A meaning colour as text on its own faint tint. The app is dark
        /// only, so the colour is already legible and is returned unchanged.
        static func onTint(_ color: Color, in scheme: ColorScheme) -> Color { color }
    }

    /// The surface ramp.
    enum Surface {
        /// The page (`background-dark`, #0f0f10). Screens, inputs.
        static let page = Color(hex: 0x0F0F10)
        /// A card (`surface`, #161617).
        static let card = Color(hex: 0x161617)
        /// Raised: a selected or pressed row, a secondary button, an active
        /// segment, a skeleton block (`raised`, #1e1e20).
        static let raised = Color(hex: 0x1E1E20)
        /// A secondary button's pressed fill (#26262a).
        static let hover = Color(hex: 0x26262A)
        /// The tab bar (`sidebar`, #0b0b0c).
        static let bar = Color(hex: 0x0B0B0C)

        // Legacy names.
        static let canvas = page
        static let panel = card
        static let inset = raised
    }

    /// The zinc text ramp.
    enum Text {
        /// zinc-100: titles, row text, figures.
        static let primary = Color(hex: 0xF4F4F5)
        /// zinc-200: slightly softer primary (input text, emphasis in a line).
        static let soft = Color(hex: 0xE4E4E7)
        /// zinc-300: field labels.
        static let label = Color(hex: 0xD4D4D8)
        /// zinc-400: secondary text, ghost buttons, key labels in key-value rows.
        static let secondary = Color(hex: 0xA1A1AA)
        /// zinc-500: sublines, captions, labels over figures, page subtitles, icons.
        static let muted = Color(hex: 0x71717A)
        /// zinc-600: placeholders, separators ("·"), the stopped dot.
        static let faint = Color(hex: 0x52525B)
    }

    /// Hairlines: white at a low opacity, 1 pt.
    enum Line {
        static let width: CGFloat = 1
        /// Around a card (white 7%).
        static let card = Color.white.opacity(0.07)
        /// Between groups, under a table header (white 6%).
        static let divider = Color.white.opacity(0.06)
        /// Between rows (white 5%).
        static let row = Color.white.opacity(0.05)
        /// Around an input or the modal (white 8%).
        static let input = Color.white.opacity(0.08)
        /// Around a focused input (white 25%).
        static let focus = Color.white.opacity(0.25)
        /// The 1 pt top highlight inside raised controls (white 5%).
        static let highlight = Color.white.opacity(0.05)
        /// The primary button's top highlight (white 40%).
        static let primaryHighlight = Color.white.opacity(0.4)
    }

    /// Charts: one lead series, zinc steps for the rest.
    enum Chart {
        /// A lead series that is not P&L (zinc-200).
        static let lead = Text.soft
        /// Further series, in order.
        static let series: [Color] = [Color(hex: 0x8B8B91), Color(hex: 0x5C5C62), Color(hex: 0x3F3F46)]
        /// Gridlines (white 5%).
        static let grid = Color.white.opacity(0.05)
        /// The dotted opening-value line (off-white 28%).
        static let baseline = Color(hex: 0xEDEDED).opacity(0.28)
        /// The scrub hairline (off-white 25%).
        static let scrubLine = Color(hex: 0xEDEDED).opacity(0.25)
        /// The top of the area fade under a P&L line; it fades to 0 at the floor.
        static let areaTopOpacity: Double = 0.12
    }

    /// Swipe-action tints. The system draws the action's label in white on
    /// its tint, so the off-white accent and zinc-400 are unreadable there.
    enum Swipe {
        /// Every non-destructive swipe action (zinc-700).
        static let neutral = Color(hex: 0x3F3F46)
        /// A destructive swipe action.
        static let destructive = Palette.loss
    }

    /// The 4 pt grid.
    enum Space {
        static let xxs: CGFloat = 2
        static let xs: CGFloat = 4
        static let s: CGFloat = 8
        static let m: CGFloat = 12
        static let l: CGFloat = 16
        static let xl: CGFloat = 20
        static let xxl: CGFloat = 24
        static let xxxl: CGFloat = 32
        static let huge: CGFloat = 48
    }

    enum Radius {
        /// A card (`rounded-xl`).
        static let card: CGFloat = 12
        /// A control, a button, an input (`rounded-lg`).
        static let control: CGFloat = 8
        /// A small or icon button, a segment (`rounded-md`).
        static let small: CGFloat = 6
        /// A modal card (`ui-modal`, `rounded-2xl`).
        static let modal: CGFloat = 16
        /// The smallest inner radius (skeleton blocks, `rounded`).
        static let tiny: CGFloat = 4

        /// An inner radius: the outer one minus the padding, at least 4.
        static func inner(_ outer: CGFloat, padding: CGFloat) -> CGFloat { max(outer - padding, tiny) }
    }

    /// The faint tint behind a meaning colour (the error box: loss at 5%).
    static let tintFill: Double = 0.05
    /// The faint tint's border (the error box: loss at 20%).
    static let tintBorder: Double = 0.2
    /// The top of the area fade under a chart line (it fades to 0).
    static let chartAreaOpacity: Double = Chart.areaTopOpacity

    /// Padding inside a `Card` (`p-5`).
    static let cardPadding: CGFloat = 20
    /// Space between groups inside a `Card`.
    static let cardGroupSpacing: CGFloat = 12
    /// The page's side margin on a phone (`px-4`).
    static let pageGutter: CGFloat = 16
    /// Between sections on a phone (`mt-12` is 48 on the web; 32 reads
    /// better at phone widths).
    static let sectionGap: CGFloat = 32
    /// A section header to its content (`mb-3`).
    static let sectionHeaderGap: CGFloat = 12
    /// A row's minimum height (`min-h-14`).
    static let rowMinHeight: CGFloat = 56
    /// A row's horizontal padding (`px-5`).
    static let rowPadding: CGFloat = 20
    /// A table header row's height (`ui-th`, `h-10`).
    static let tableHeaderHeight: CGFloat = 40
    /// The dash of the dotted opening-value line (`stroke-dasharray="2 3"`).
    static let baselineDash: [CGFloat] = [2, 3]
}

/// Motion: 150 ms on the dashboard's ease-out curve, `cubic-bezier(0.22, 1, 0.36, 1)`.
nonisolated enum DSMotion {
    /// A state change (selection, a segment, a toggle).
    static let standard = Animation.timingCurve(0.22, 1, 0.36, 1, duration: 0.15)

    /// The same curve over `duration` seconds (a page fade, a chart draw).
    static func ease(_ duration: Double) -> Animation {
        .timingCurve(0.22, 1, 0.36, 1, duration: duration)
    }

    /// `standard`, or none under Reduce Motion.
    static func standard(reduceMotion: Bool) -> Animation? {
        reduceMotion ? nil : standard
    }
}

extension Color {
    /// An sRGB colour from `0xRRGGBB`.
    nonisolated init(hex: UInt32, opacity: Double = 1) {
        self.init(
            .sRGB,
            red: Double((hex >> 16) & 0xFF) / 255,
            green: Double((hex >> 8) & 0xFF) / 255,
            blue: Double(hex & 0xFF) / 255,
            opacity: opacity
        )
    }
}

extension UIColor {
    /// A UIKit colour from `0xRRGGBB`, for the system bar appearances.
    nonisolated convenience init(hex: UInt32, alpha: CGFloat = 1) {
        self.init(
            red: CGFloat((hex >> 16) & 0xFF) / 255,
            green: CGFloat((hex >> 8) & 0xFF) / 255,
            blue: CGFloat(hex & 0xFF) / 255,
            alpha: alpha
        )
    }
}
