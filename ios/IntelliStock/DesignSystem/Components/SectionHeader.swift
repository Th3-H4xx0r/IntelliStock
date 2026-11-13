import SwiftUI

/// A heading over content outside a `List` (`ui-section-title` +
/// `ui-section-sub`): a 16/500 zinc-100 title, an optional 14 pt zinc-500
/// line, and an optional trailing view. Put it 12 pt above its card
/// (`DS.sectionHeaderGap`).
///
/// The redesign removed eyebrows: no upper case, no tracking, no tint. The
/// `eyebrow:` argument is still accepted so existing call sites compile, but it
/// draws nothing; drop it when you touch the call site. Inside a `List`, use
/// `DSSection` (or `Section("Title")`) instead, and never put a large-title
/// echo or a marketing paragraph under a navigation title (spec P7).
///
///     SectionHeader(title: "Sub-strategies (2)") {
///         Button("Add", systemImage: "plus") { … }
///     }
struct SectionHeader<Trailing: View>: View {
    let title: String
    var subtitle: String?
    private let trailing: Trailing

    /// `eyebrow` is ignored (see the type's documentation).
    init(title: String, eyebrow: String? = nil, subtitle: String? = nil, @ViewBuilder trailing: () -> Trailing) {
        self.title = title
        self.subtitle = subtitle
        self.trailing = trailing()
    }

    var body: some View {
        HStack(alignment: .center, spacing: 12) {
            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                    .font(DS.Font.sectionTitle)
                    .foregroundStyle(DS.Text.primary)
                    .accessibilityAddTraits(.isHeader)
                if let subtitle {
                    Text(subtitle)
                        .font(DS.Font.sectionSub)
                        .foregroundStyle(DS.Text.muted)
                        .monospacedDigit()
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            trailing
        }
    }
}

extension SectionHeader where Trailing == EmptyView {
    /// `eyebrow` is ignored (see the type's documentation).
    init(title: String, eyebrow: String? = nil, subtitle: String? = nil) {
        self.init(title: title, eyebrow: eyebrow, subtitle: subtitle) { EmptyView() }
    }
}

#Preview {
    ScrollView {
        VStack(alignment: .leading, spacing: 12) {
            SectionHeader(title: "Sub-strategies (2)", eyebrow: "COMPOSITION")
            Card { Text("strategy_swing") }
            SectionHeader(title: "Backtests (3)", subtitle: "Newest first") {
                Button("See all") {}
                    .buttonStyle(.dsGhost)
            }
        }
        .padding()
    }
    .background(DS.Surface.page)
    .dsAppearance()
}
