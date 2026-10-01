import SwiftUI

/// The big empty state: icon, headline, subtext and an optional call to
/// action — `EmptyState` in `common_widgets.dart`, as the system
/// `ContentUnavailableView` with a prominent action (`writing.md`: empty
/// screens invite the next action).
struct EmptyState: View {
    /// An SF Symbol (`Symbol.named(<material name>)`).
    let systemImage: String
    let title: String
    var subtitle: String?
    var actionLabel: String?
    var onAction: (() -> Void)?

    var body: some View {
        ContentUnavailableView {
            Label(title, systemImage: systemImage)
        } description: {
            if let subtitle {
                Text(subtitle)
            }
        } actions: {
            if let actionLabel, let onAction {
                Button(actionLabel, action: onAction)
                    .buttonStyle(.borderedProminent)
            }
        }
    }
}

/// A full-screen or inline loading row: spinner + label — `LoadingState`.
struct LoadingState: View {
    var label = "Loading…"

    var body: some View {
        HStack(spacing: 12) {
            ProgressView()
            Text(label)
                .font(.subheadline)
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity)
        .accessibilityElement(children: .combine)
    }
}
