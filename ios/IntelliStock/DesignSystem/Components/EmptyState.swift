import SwiftUI

/// The empty state (`ui-empty`): one short sentence and at most one action,
/// centred, 48 pt above and below. No icon (the web draws none): `systemImage`
/// is kept so call sites compile, and ignored.
///
/// - `title`: the sentence, 14 pt zinc-300 ("No orders for AAPL in this account").
/// - `subtitle`: an optional quieter line, 12 pt zinc-500 ("Your orders and
///   the bot's show here").
/// - The action is a secondary button (`ui-btn`).
///
/// Put it inside a card (`Card(padding: 0)`, or a `DSSection` row).
struct EmptyState: View {
    let systemImage: String
    let title: String
    var subtitle: String?
    var actionLabel: String?
    var onAction: (() -> Void)?

    init(systemImage: String = "", title: String, subtitle: String? = nil, actionLabel: String? = nil, onAction: (() -> Void)? = nil) {
        self.systemImage = systemImage
        self.title = title
        self.subtitle = subtitle
        self.actionLabel = actionLabel
        self.onAction = onAction
    }

    var body: some View {
        VStack(spacing: 4) {
            Text(title.dsTrimmingTitlePeriod)
                .font(DS.Font.body)
                .foregroundStyle(DS.Text.label)
            if let subtitle {
                Text(subtitle.dsTrimmingTitlePeriod)
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Text.muted)
            }
            if let actionLabel, let onAction {
                Button(actionLabel, action: onAction)
                    .buttonStyle(.dsSecondary)
                    .padding(.top, 12)
            }
        }
        .multilineTextAlignment(.center)
        .frame(maxWidth: .infinity)
        .padding(.horizontal, 24)
        .padding(.vertical, 40)
        .accessibilityElement(children: .contain)
    }
}

extension String {
    /// A one-line title or subtitle without a trailing period ("No data." →
    /// "No data"); a sentence ending "…" or a question keeps its mark.
    nonisolated var dsTrimmingTitlePeriod: String {
        hasSuffix(".") && !hasSuffix("..") ? String(dropLast()) : self
    }
}

/// A loading row: a small spinner and a 14 pt zinc-500 label — for an
/// action that is running. A first load of content uses `Skeleton` blocks
/// shaped like the content instead.
struct LoadingState: View {
    var label = "Loading…"

    var body: some View {
        HStack(spacing: 10) {
            ProgressView()
                .controlSize(.small)
            Text(label)
                .font(DS.Font.body)
                .foregroundStyle(DS.Text.muted)
        }
        .frame(maxWidth: .infinity)
        .accessibilityElement(children: .combine)
    }
}
