import SwiftUI

// Small building blocks shared by the markets screens (Kalshi, Crypto,
// Backtests, Strategies, Nexus, Learning). Not design-system components:
// each mirrors a recurring Dart idiom in those screens.

// The wrapping chip row is `FlowLayout` (DesignSystem), aliased as
// `MarketsFlowLayout`.

/// A short coloured label — the screens' `_pill` / `_badge` / `_chip`
/// containers, drawn in the app's one badge style (`.dsBadge`: a
/// `.caption2` semibold capsule, colour text on a 15 % fill). The text is
/// kept as written; callers pass it in sentence case.
struct MarketsTag: View {
    let text: String
    var color: Color = DS.Palette.accent

    var body: some View {
        Text(text)
            .dsBadge(color)
    }
}

/// A small neutral tag (coin tickers, symbols, sub-strategy names): secondary
/// text on a system fill capsule, Stocks-style. `tint` gives it a 15 % colour
/// fill instead (the Dynamic coin).
struct MarketsChip: View {
    let text: String
    var color: Color = .secondary
    var tint: Color?

    var body: some View {
        Text(text)
            .font(.caption.weight(.medium))
            .foregroundStyle(color)
            .lineLimit(1)
            .padding(.horizontal, 8)
            .padding(.vertical, 3)
            .background(tint?.opacity(DS.tintFill) ?? Color(uiColor: .tertiarySystemFill), in: Capsule())
    }
}

/// A form label with a tap-to-reveal explanation — the sheets' `_label(text,
/// info)` with its `Tooltip(triggerMode: tap)`, as an info button opening a
/// compact popover.
struct MarketsInfoLabel: View {
    let text: String
    let info: String

    @State private var showing = false

    var body: some View {
        HStack(spacing: 4) {
            Text(text)
            Button {
                showing = true
            } label: {
                Image(systemName: Symbol.named("info_outline"))
                    .font(.footnote)
                    .foregroundStyle(.tertiary)
            }
            .buttonStyle(.borderless)
            .accessibilityLabel("About \(text)")
            .popover(isPresented: $showing) {
                Text(info)
                    .font(.footnote)
                    .padding()
                    .frame(maxWidth: 300)
                    .presentationCompactAdaptation(.popover)
            }
        }
    }
}

/// A card header: an accent glyph and an upper-cased eyebrow title — the
/// screens' `_KCard` / `_card` header rows.
struct MarketsCardHeader: View {
    let icon: String
    let title: String
    var color: Color = DS.Palette.accent

    var body: some View {
        Label {
            Text(title.uppercased())
                .font(.footnote.weight(.semibold))
                .foregroundStyle(.secondary)
        } icon: {
            Image(systemName: Symbol.named(icon))
                .foregroundStyle(color)
        }
        .accessibilityAddTraits(.isHeader)
    }
}

/// A round team crest: the network image, else initials on a grey disc
/// (`_posCrest`, `_crest`, `_teamBadge`).
struct MarketsCrest: View {
    let url: String
    let initials: String
    var size: CGFloat = 36

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
        .clipShape(Circle())
        .accessibilityHidden(true)
    }

    private var fallback: some View {
        Text(initials)
            .font(.caption2.weight(.bold))
            .foregroundStyle(.secondary)
            .frame(width: size, height: size)
            .background(DS.Surface.inset)
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

/// A numeric form row: the Dart label on the left, a right-aligned decimal
/// field on the right.
struct MarketsNumberRow: View {
    let label: String
    @Binding var text: String
    var prompt: String?

    var body: some View {
        LabeledContent(label) {
            TextField(label, text: $text, prompt: prompt.map { Text($0) })
                .keyboardType(.decimalPad)
                .multilineTextAlignment(.trailing)
                .monospacedDigit()
        }
    }
}
