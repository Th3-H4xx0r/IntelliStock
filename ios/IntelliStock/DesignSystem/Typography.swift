import SwiftUI

// The Flutter type scale (`app_text_styles.dart`) mapped to Dynamic Type
// (spec §7). Use the text styles directly:
//
//   h1 24 → .title2.bold()      h2 20 → .title3.bold()     h3 16 → .headline
//   cardTitle 14 → .subheadline.weight(.semibold)
//   body 14 → .body in rows, .subheadline in dense cards
//   meta 12 → .footnote         micro 11 → .caption        nano 10 → .caption2
//   value* → .monospacedDigit() on the matching style
//   mono → .system(.caption, design: .monospaced)
//
// Only the hero figure needs a custom size; it scales with `.largeTitle`.

extension View {
    /// The dashboard hero balance (`valueHero`, 38 pt): 40 pt bold, scaled
    /// relative to `.largeTitle`, with monospaced digits.
    func dsValueHero() -> some View {
        modifier(ValueHeroStyle())
    }
}

private struct ValueHeroStyle: ViewModifier {
    @ScaledMetric(relativeTo: .largeTitle) private var size: CGFloat = 40

    func body(content: Content) -> some View {
        content
            .font(.system(size: size, weight: .bold))
            .monospacedDigit()
    }
}
