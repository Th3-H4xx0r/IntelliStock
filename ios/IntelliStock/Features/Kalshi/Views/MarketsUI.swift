import SwiftUI

// Small building blocks shared by the markets screens (Kalshi, Crypto,
// Backtests, Strategies, Nexus, Learning), drawn in the web style. Not
// design-system components: each mirrors a recurring idiom in those screens.

// The wrapping chip row is `FlowLayout` (DesignSystem), aliased as
// `MarketsFlowLayout`.

/// A short label beside a title — the screens' `_pill` / `_badge` / `_chip`
/// flags. The web has no pills, so it is quiet 12 pt text (`.dsBadge`):
/// gain, loss and amber keep their colour, every other colour reads
/// zinc-400. The text is kept as written; callers pass it in sentence case.
struct MarketsTag: View {
    let text: String
    var color: Color = DS.Palette.accent

    var body: some View {
        Text(text)
            .dsBadge(color)
    }
}

/// A small neutral tag (coin tickers, symbols, sub-strategy names): the
/// web's symbol chip — 12/500 zinc-200 on the raised surface, a 6% hairline
/// and 6 radius. `color` and `tint` are kept so call sites compile; the
/// chip has no hue.
struct MarketsChip: View {
    let text: String
    var color: Color = .secondary
    var tint: Color?

    var body: some View {
        Text(text)
            .font(DS.Font.captionMedium)
            .foregroundStyle(DS.Text.soft)
            .monospacedDigit()
            .lineLimit(1)
            .padding(.horizontal, 8)
            .frame(minHeight: 24)
            .background(DS.Surface.raised, in: .rect(cornerRadius: DS.Radius.small, style: .continuous))
            .overlay {
                RoundedRectangle(cornerRadius: DS.Radius.small, style: .continuous)
                    .strokeBorder(DS.Line.divider, lineWidth: DS.Line.width)
            }
    }
}

/// A field label with a tap-to-reveal explanation — the web's
/// `ui-field-label` with its `InfoTip`: the label in 14 pt zinc-300, a
/// zinc-600 info glyph that opens a compact popover.
struct MarketsInfoLabel: View {
    let text: String
    let info: String

    @State private var showing = false

    var body: some View {
        HStack(spacing: 6) {
            Text(text)
                .font(DS.Font.body)
                .foregroundStyle(DS.Text.label)
            Button {
                showing = true
            } label: {
                Image(systemName: "info.circle")
                    .font(.system(size: 13))
                    .foregroundStyle(DS.Text.faint)
                    .frame(width: 28, height: 28)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .padding(.vertical, -6)
            .accessibilityLabel("About \(text)")
            .popover(isPresented: $showing) {
                Text(info)
                    .font(DS.Font.small)
                    .foregroundStyle(DS.Text.label)
                    .padding(16)
                    .frame(maxWidth: 300)
                    .presentationBackground(DS.Surface.raised)
                    .presentationCompactAdaptation(.popover)
            }
        }
    }
}

/// A round team crest: the network image on the raised disc, else its
/// initials in 11/500 zinc-400 (`_posCrest`, `_crest`, `_teamBadge`).
struct MarketsCrest: View {
    let url: String
    let initials: String
    var size: CGFloat = 32

    var body: some View {
        Group {
            if let u = URL(string: url), !url.isEmpty {
                AsyncImage(url: u) { phase in
                    if let image = phase.image {
                        image.resizable().scaledToFit()
                    } else {
                        fallback
                    }
                }
            } else {
                fallback
            }
        }
        .frame(width: size, height: size)
        .background(DS.Surface.raised)
        .clipShape(Circle())
        .accessibilityHidden(true)
    }

    private var fallback: some View {
        Text(initials)
            .font(DS.Font.inter(size <= 24 ? 10 : 11, .medium, relativeTo: .caption2))
            .foregroundStyle(DS.Text.secondary)
            .frame(width: size, height: size)
            .background(DS.Surface.raised)
    }
}

/// A sheet presenting a graphical date picker with Cancel / Done — the native
/// form of `showDatePicker` for a date that may still be unset.
struct MarketsDatePickerSheet: View {
    let title: String
    let initial: Date
    let range: ClosedRange<Date>
    let onPick: (Date) -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var date: Date

    init(title: String, initial: Date, range: ClosedRange<Date>, onPick: @escaping (Date) -> Void) {
        self.title = title
        self.initial = initial
        self.range = range
        self.onPick = onPick
        _date = State(initialValue: min(max(initial, range.lowerBound), range.upperBound))
    }

    var body: some View {
        NavigationStack {
            DatePicker(title, selection: $date, in: range, displayedComponents: .date)
                .datePickerStyle(.graphical)
                .padding()
                .navigationTitle(title)
                .navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) {
                        Button("Cancel") { dismiss() }
                    }
                    ToolbarItem(placement: .confirmationAction) {
                        Button("Done") {
                            onPick(Calendar.current.startOfDay(for: date))
                            dismiss()
                        }
                    }
                }
        }
        .presentationDetents([.medium, .large])
        .presentationDragIndicator(.visible)
    }
}
