import SwiftUI

/// Eyebrow + title + subtitle with an optional trailing view —
/// `SectionHeader` in `common_widgets.dart`.
struct SectionHeader<Trailing: View>: View {
    let title: String
    var eyebrow: String?
    var subtitle: String?
    private let trailing: Trailing

    init(title: String, eyebrow: String? = nil, subtitle: String? = nil, @ViewBuilder trailing: () -> Trailing) {
        self.title = title
        self.eyebrow = eyebrow
        self.subtitle = subtitle
        self.trailing = trailing()
    }

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            VStack(alignment: .leading, spacing: 4) {
                if let eyebrow {
                    Text(eyebrow.uppercased())
                        .font(.footnote.weight(.bold))
                        .tracking(1.2)
                        .foregroundStyle(.tint)
                }
                Text(title)
                    .font(.title2.bold())
                    .accessibilityAddTraits(.isHeader)
                if let subtitle {
                    Text(subtitle)
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            trailing
        }
    }
}

extension SectionHeader where Trailing == EmptyView {
    init(title: String, eyebrow: String? = nil, subtitle: String? = nil) {
        self.init(title: title, eyebrow: eyebrow, subtitle: subtitle) { EmptyView() }
    }
}
