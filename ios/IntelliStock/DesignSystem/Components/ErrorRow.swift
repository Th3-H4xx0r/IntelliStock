import SwiftUI

/// An inline error with an optional Retry — `ErrorBanner` in
/// `common_widgets.dart`. Red text on a 15 % red tint; failures stay in the
/// interface rather than in an alert (`feedback.md`).
struct ErrorRow: View {
    let message: String
    var onRetry: (() -> Void)?

    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        let ink = DS.Palette.onTint(DS.Palette.danger, in: colorScheme)
        HStack(alignment: .firstTextBaseline, spacing: 12) {
            Image(systemName: Symbol.named("error_outline"))
                .foregroundStyle(ink)
                .accessibilityHidden(true)
            Text(message)
                .font(.subheadline)
                .foregroundStyle(ink)
                .frame(maxWidth: .infinity, alignment: .leading)
            if let onRetry {
                Button("Retry", action: onRetry)
                    .font(.subheadline.weight(.semibold))
                    .buttonStyle(.borderless)
            }
        }
        .padding(16)
        .background(DS.Palette.danger.opacity(DS.tintFill), in: .rect(cornerRadius: DS.Radius.control, style: .continuous))
    }
}
