import SwiftUI

/// An error (`ui-error`): loss-red 14 pt text on a faint loss-tinted card
/// (loss at 5%, a loss-20% hairline, 12 radius), with an optional Retry
/// (`ui-btn`). Failures stay in the interface, not in an alert.
struct ErrorRow: View {
    let message: String
    var onRetry: (() -> Void)?

    var body: some View {
        HStack(alignment: .center, spacing: 12) {
            Text(message)
                .font(DS.Font.body)
                .foregroundStyle(DS.Palette.loss)
                .frame(maxWidth: .infinity, alignment: .leading)
                .fixedSize(horizontal: false, vertical: true)
            if let onRetry {
                Button("Retry", action: onRetry)
                    .buttonStyle(.dsSecondary)
            }
        }
        .padding(.horizontal, 20)
        .padding(.vertical, 14)
        .dsErrorSurface()
        .accessibilityElement(children: .combine)
    }
}

extension View {
    /// The error box's surface (`ui-error`) behind any view.
    func dsErrorSurface(radius: CGFloat = DS.Radius.card) -> some View {
        self
            .background(DS.Palette.loss.opacity(DS.tintFill), in: .rect(cornerRadius: radius, style: .continuous))
            .overlay {
                RoundedRectangle(cornerRadius: radius, style: .continuous)
                    .strokeBorder(DS.Palette.loss.opacity(DS.tintBorder), lineWidth: DS.Line.width)
            }
    }
}
