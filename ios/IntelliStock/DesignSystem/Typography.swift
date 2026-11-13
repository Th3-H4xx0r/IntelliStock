import SwiftUI
import UIKit

/// Inter, the web app's typeface, bundled (Regular 400, Medium 500, SemiBold
/// 600; registered through `UIAppFonts` in project.yml). Every size scales
/// with Dynamic Type relative to the text style named beside it.
///
/// Weights 400 and 500 only; 600 for rare emphasis. Never `.bold()`,
/// `.fontWeight(_:)` or a system text style (`.headline`, `.body`, …): pick
/// the role below instead. Hierarchy comes from size and colour.
///
///     Text("Holdings").font(DS.Font.sectionTitle)
///     Text("$5,396.11").font(DS.Font.hero).monospacedDigit()
///     Text("4 positions").font(DS.Font.caption).foregroundStyle(DS.Text.muted)
extension DS {
    nonisolated enum Font {
        enum Weight: Sendable {
            case regular, medium, semibold

            /// The bundled face's PostScript name.
            var faceName: String {
                switch self {
                case .regular: "Inter-Regular"
                case .medium: "Inter-Medium"
                case .semibold: "Inter-SemiBold"
                }
            }
        }

        /// Inter at `size` and `weight`, scaling like `textStyle`.
        static func inter(_ size: CGFloat, _ weight: Weight = .regular, relativeTo textStyle: SwiftUI.Font.TextStyle = .body) -> SwiftUI.Font {
            .custom(weight.faceName, size: size, relativeTo: textStyle)
        }

        // MARK: Headings (`ui-page-title`, `ui-section-title`)

        /// A page title: 24/500 (`ui-page-title`, `text-2xl`).
        static let pageTitle = inter(24, .medium, relativeTo: .title2)
        /// Under a page title: 14 (`ui-page-sub`); colour `DS.Text.muted`.
        static let pageSub = inter(14, relativeTo: .subheadline)
        /// A section title: 16/500 (`ui-section-title`, `text-base`).
        static let sectionTitle = inter(16, .medium, relativeTo: .headline)
        /// Under a section title: 14 (`ui-section-sub`); colour `DS.Text.muted`.
        static let sectionSub = inter(14, relativeTo: .subheadline)

        // MARK: Text

        /// Body and row text: 14 (`text-sm`). The app's default font.
        static let body = inter(14, relativeTo: .body)
        /// Emphasised row text, a primary button: 14/500.
        static let bodyMedium = inter(14, .medium, relativeTo: .body)
        /// 16 (`text-base`): a card's lead line, a sheet title.
        static let base = inter(16, relativeTo: .body)
        /// 16/500.
        static let baseMedium = inter(16, .medium, relativeTo: .body)
        /// 13 (`text-[13px]`): dense text.
        static let small = inter(13, relativeTo: .footnote)
        /// 13/500: a secondary button (`ui-btn`).
        static let smallMedium = inter(13, .medium, relativeTo: .footnote)
        /// 12 (`text-xs`): labels over figures, sublines, help, meta.
        static let caption = inter(12, relativeTo: .caption)
        /// 12/500: segments (`ui-seg-btn`), range buttons.
        static let captionMedium = inter(12, .medium, relativeTo: .caption)
        /// 11: chart axis labels (always `.monospacedDigit()`).
        static let micro = inter(11, relativeTo: .caption2)
        /// 18/500 (`text-lg`): a result headline.
        static let large = inter(18, .medium, relativeTo: .title3)
        /// 600 for the rare emphasis the web gives `font-semibold`.
        static let bodySemibold = inter(14, .semibold, relativeTo: .body)

        // MARK: Figures (always `.monospacedDigit()`)

        /// A stat figure: 20/500 (`text-xl`).
        static let figure = inter(20, .medium, relativeTo: .title3)
        /// A large stat figure: 24/500 (`text-2xl`).
        static let figureLarge = inter(24, .medium, relativeTo: .title2)
        /// A hero figure: 40/500, the dashboard's account value and a
        /// stock's price (the web draws 44 px on a wider column).
        static let hero = inter(40, .medium, relativeTo: .largeTitle)

        /// Log lines and ids: the system monospace at 12.
        static let mono = SwiftUI.Font.system(size: 12, design: .monospaced)
        /// Small log lines: the system monospace at 11.
        static let monoSmall = SwiftUI.Font.system(size: 11, design: .monospaced)

        // MARK: UIKit (system bars)

        /// Inter for UIKit, scaled like `textStyle`.
        @MainActor static func uiFont(_ size: CGFloat, _ weight: Weight = .regular, textStyle: UIFont.TextStyle = .body) -> UIFont {
            let base = UIFont(name: weight.faceName, size: size) ?? .systemFont(ofSize: size, weight: weight == .regular ? .regular : .medium)
            return UIFontMetrics(forTextStyle: textStyle).scaledFont(for: base)
        }
    }
}

extension View {
    /// The hero figure (`DS.Font.hero`) with monospaced digits.
    func dsValueHero() -> some View {
        font(DS.Font.hero)
            .monospacedDigit()
            .kerning(-0.6)
    }

    /// The app-wide look, applied once at the root (and by the snapshot
    /// harness): dark only, the off-white tint, Inter 14 as the default font,
    /// the zinc ramp as the hierarchical foreground styles (so
    /// `.foregroundStyle(.secondary)` is zinc-400 and `.tertiary` zinc-500),
    /// the web switch for every `Toggle`, and list backgrounds hidden so the
    /// page colour shows through.
    func dsAppearance() -> some View {
        preferredColorScheme(.dark)
            .tint(DS.Palette.accent)
            .foregroundStyle(DS.Text.primary, DS.Text.secondary, DS.Text.muted)
            .font(DS.Font.body)
            .toggleStyle(DSSwitchToggleStyle())
            // Lists and forms show the screen's page colour behind them
            // (`dsScreenBackground`), not the system's black grouped fill.
            .scrollContentBackground(.hidden)
    }

    /// The page colour behind a whole screen, safe areas included. Every
    /// tab root and pushed route gets it from `MainTabView`; give it to a
    /// sheet's root yourself (or use `.presentationBackground(DS.Surface.page)`).
    func dsScreenBackground() -> some View {
        background(DS.Surface.page.ignoresSafeArea())
    }
}
