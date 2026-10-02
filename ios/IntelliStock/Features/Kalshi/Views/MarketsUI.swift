import SwiftUI

// Small building blocks shared by the markets screens (Kalshi, Crypto,
// Backtests, Strategies, Nexus, Learning). Not design-system components:
// each mirrors a recurring Dart idiom in those screens.

/// A wrapping row of chips — Flutter's `Wrap(spacing:runSpacing:)`.
struct MarketsFlowLayout: Layout {
    var spacing: CGFloat = 6
    var runSpacing: CGFloat = 6
    var alignment: HorizontalAlignment = .leading

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let rows = rows(for: proposal.width ?? .infinity, subviews: subviews)
        let width = rows.map(\.width).max() ?? 0
        let height = rows.map(\.height).reduce(0, +) + CGFloat(max(rows.count - 1, 0)) * runSpacing
        return CGSize(width: proposal.width ?? width, height: height)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        var y = bounds.minY
        for row in rows(for: bounds.width, subviews: subviews) {
            var x: CGFloat
            switch alignment {
            case .center: x = bounds.minX + (bounds.width - row.width) / 2
            case .trailing: x = bounds.maxX - row.width
            default: x = bounds.minX
            }
            for index in row.indices {
                let size = subviews[index].sizeThatFits(.unspecified)
                subviews[index].place(at: CGPoint(x: x, y: y + (row.height - size.height) / 2), proposal: ProposedViewSize(size))
                x += size.width + spacing
            }
            y += row.height + runSpacing
        }
    }

    private struct Row {
        var indices: [Int] = []
        var width: CGFloat = 0
        var height: CGFloat = 0
    }

    private func rows(for maxWidth: CGFloat, subviews: Subviews) -> [Row] {
        var rows: [Row] = []
        var current = Row()
        for (i, view) in subviews.enumerated() {
            let size = view.sizeThatFits(.unspecified)
            let needed = current.indices.isEmpty ? size.width : current.width + spacing + size.width
            if needed > maxWidth, !current.indices.isEmpty {
                rows.append(current)
                current = Row()
            }
            current.width = current.indices.isEmpty ? size.width : current.width + spacing + size.width
            current.height = max(current.height, size.height)
            current.indices.append(i)
        }
        if !current.indices.isEmpty { rows.append(current) }
        return rows
    }
}

/// A short coloured label on a 15 % tint of its colour — the screens'
/// `_pill` / `_badge` / `_chip` containers (text kept as written, not
/// upper-cased).
struct MarketsTag: View {
    let text: String
    var color: Color = DS.Palette.accent
    var mono = false

    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        Text(text)
            .font(mono ? .caption2.monospaced().weight(.semibold) : .caption2.weight(.bold))
            .foregroundStyle(color == .secondary || color == .primary ? color : DS.Palette.onTint(color, in: colorScheme))
            .padding(.horizontal, 7)
            .padding(.vertical, 3)
            .background(color.opacity(DS.tintFill), in: .rect(cornerRadius: 6, style: .continuous))
    }
}

/// A neutral bordered chip (mono coin tickers, fixed-coin summaries).
struct MarketsChip: View {
    let text: String
    var color: Color = .primary
    var tint: Color?

    var body: some View {
        Text(text)
            .font(.caption.monospaced())
            .foregroundStyle(color)
            .padding(.horizontal, 8)
            .padding(.vertical, 3)
            .background(tint?.opacity(DS.tintFill) ?? DS.Surface.inset, in: .rect(cornerRadius: 6, style: .continuous))
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
