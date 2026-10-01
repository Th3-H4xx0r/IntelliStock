import SwiftUI

/// The content container — the native form of `GlassCard` (plain, liquid and
/// frosted alike). A solid secondary grouped surface with 22 pt continuous
/// corners: no blur, border, gradient or shadow, as in Apple's Stocks and
/// Wallet. Glass stays off content (`liquid-glass.md`).
///
/// Where the content is rows, prefer an inset-grouped `List` section.
struct Card<Content: View>: View {
    private let padding: EdgeInsets
    private let content: Content

    /// `padding` defaults to 20 on every side, as `GlassCard` did.
    init(padding: CGFloat = 20, @ViewBuilder content: () -> Content) {
        self.padding = EdgeInsets(top: padding, leading: padding, bottom: padding, trailing: padding)
        self.content = content()
    }

    init(padding: EdgeInsets, @ViewBuilder content: () -> Content) {
        self.padding = padding
        self.content = content()
    }

    var body: some View {
        content
            .padding(padding)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(DS.Surface.panel, in: .rect(cornerRadius: DS.Radius.card, style: .continuous))
    }
}

extension DS.Palette {
    /// A status colour made legible as text on its own 15 % tint: darkened in
    /// the light appearance (system green/orange text on a pale tint is about
    /// 2:1; darkened it clears 4.5:1), unchanged in dark, where it already
    /// passes.
    static func onTint(_ color: Color, in scheme: ColorScheme) -> Color {
        scheme == .dark ? color : color.mix(with: .black, by: 0.4)
    }
}
