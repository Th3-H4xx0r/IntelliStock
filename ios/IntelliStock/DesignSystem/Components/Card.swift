import SwiftUI

/// The container for a hero or a chart — the native form of `GlassCard`
/// (plain, liquid and frosted alike). A solid secondary grouped surface with
/// 22 pt continuous corners and 16 pt padding: no blur, border, gradient or
/// shadow, as in Stocks and Wallet. Glass stays off content (`liquid-glass.md`).
///
/// **Only for content that is not row-shaped:** a portfolio chart, an
/// allocation donut, a hero figure. Rows, key-value pairs and settings go in
/// an inset-grouped `List` (`DSSection`, `EntityRow`, `LabeledContent`). Never
/// nest a card in a card, and never put a grey tile inside one; use a
/// `StatGrid` instead.
///
///     Card("Sector allocation") {
///         SectorDonut(...)
///     }
struct Card<Content: View>: View {
    private let title: String?
    private let padding: EdgeInsets
    private let content: Content

    /// `padding` defaults to `DS.cardPadding` (16) on every side.
    init(padding: CGFloat = DS.cardPadding, @ViewBuilder content: () -> Content) {
        self.title = nil
        self.padding = EdgeInsets(top: padding, leading: padding, bottom: padding, trailing: padding)
        self.content = content()
    }

    init(padding: EdgeInsets, @ViewBuilder content: () -> Content) {
        self.title = nil
        self.padding = padding
        self.content = content()
    }

    /// A card with a `.headline` title over its content, 12 pt apart. The
    /// title is plain primary text: no icon, no colour, no upper case.
    init(_ title: String, padding: CGFloat = DS.cardPadding, @ViewBuilder content: () -> Content) {
        self.title = title
        self.padding = EdgeInsets(top: padding, leading: padding, bottom: padding, trailing: padding)
        self.content = content()
    }

    var body: some View {
        Group {
            if let title {
                VStack(alignment: .leading, spacing: DS.cardGroupSpacing) {
                    Text(title)
                        .font(.headline)
                        .accessibilityAddTraits(.isHeader)
                    content
                }
            } else {
                content
            }
        }
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

#Preview {
    ScrollView {
        VStack(spacing: 16) {
            Card {
                HeroValueHeader("$5,890.05", change: "+$62.13 (+1.07%)", direction: .up, status: "Markets closed")
            }
            Card("Sector allocation") {
                Text("A chart goes here.")
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, minHeight: 120)
            }
        }
        .padding()
    }
    .background(DS.Surface.canvas)
}
