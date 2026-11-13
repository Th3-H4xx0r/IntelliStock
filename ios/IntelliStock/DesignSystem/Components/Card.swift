import SwiftUI

/// A card (`ui-card`): the surface fill, 12 radius and a 1 pt white-7%
/// hairline. No shadow, glass or material.
///
/// - `Card { … }` pads its content 20 pt (`p-5`).
/// - `Card("Sector performance") { … }` adds a header row: the title in
///   14/500 zinc-200, 48 tall, over a 6% divider (the web's card headers).
/// - `Card(padding: 0) { DSRows { … } }` holds rows edge to edge.
///
/// Never nest a card in a card. Rows in a `List` use `DSSection`, which
/// draws the same card around them.
///
///     Card { StatGrid { … } }
///     Card("Market movers") { … } accessory: { DSSegmentedControl(…) }
struct Card<Content: View, Accessory: View>: View {
    private let title: String?
    private let padding: EdgeInsets
    private let accessory: Accessory
    private let content: Content

    /// `padding` defaults to `DS.cardPadding` (20) on every side.
    init(padding: CGFloat = DS.cardPadding, @ViewBuilder content: () -> Content) where Accessory == EmptyView {
        self.title = nil
        self.padding = EdgeInsets(top: padding, leading: padding, bottom: padding, trailing: padding)
        self.accessory = EmptyView()
        self.content = content()
    }

    init(padding: EdgeInsets, @ViewBuilder content: () -> Content) where Accessory == EmptyView {
        self.title = nil
        self.padding = padding
        self.accessory = EmptyView()
        self.content = content()
    }

    /// A card with a header row (title, then a divider) over its content.
    init(_ title: String, padding: CGFloat = DS.cardPadding, @ViewBuilder content: () -> Content) where Accessory == EmptyView {
        self.title = title
        self.padding = EdgeInsets(top: padding, leading: padding, bottom: padding, trailing: padding)
        self.accessory = EmptyView()
        self.content = content()
    }

    /// A card with a header row carrying a trailing accessory (a segmented
    /// control, a count).
    init(_ title: String, padding: CGFloat = DS.cardPadding, @ViewBuilder content: () -> Content, @ViewBuilder accessory: () -> Accessory) {
        self.title = title
        self.padding = EdgeInsets(top: padding, leading: padding, bottom: padding, trailing: padding)
        self.accessory = accessory()
        self.content = content()
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            if let title {
                HStack(spacing: 12) {
                    Text(title)
                        .font(DS.Font.bodyMedium)
                        .foregroundStyle(DS.Text.soft)
                        .accessibilityAddTraits(.isHeader)
                    Spacer(minLength: 8)
                    accessory
                        .font(DS.Font.caption)
                        .foregroundStyle(DS.Text.muted)
                }
                .padding(.horizontal, DS.rowPadding)
                .frame(minHeight: 48)
                DSDivider()
            }
            content
                .padding(padding)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .dsCardSurface()
    }
}

extension View {
    /// The card surface (`ui-card`) behind any view: fill, 12 radius, 7%
    /// hairline. `fill` may be `DS.Surface.raised` for a selected card.
    func dsCardSurface(fill: Color = DS.Surface.card, radius: CGFloat = DS.Radius.card, border: Color = DS.Line.card) -> some View {
        self
            .background(fill, in: .rect(cornerRadius: radius, style: .continuous))
            .overlay {
                RoundedRectangle(cornerRadius: radius, style: .continuous)
                    .strokeBorder(border, lineWidth: DS.Line.width)
            }
            .clipShape(.rect(cornerRadius: radius, style: .continuous))
    }
}

/// A 1 pt hairline: 6% between groups (`divide`, a table header), 5% between
/// rows (`row: true`).
struct DSDivider: View {
    var row = false
    var vertical = false

    var body: some View {
        Rectangle()
            .fill(row ? DS.Line.row : DS.Line.divider)
            .frame(width: vertical ? DS.Line.width : nil, height: vertical ? nil : DS.Line.width)
            .accessibilityHidden(true)
    }
}

/// Rows in a card outside a `List` (`ui-rows`): each child is a row,
/// separated by 5% hairlines. Pair with `DSRow` for the row metrics.
///
///     Card(padding: 0) {
///         DSRows {
///             ForEach(stats) { DSRow { DSKeyValueRow($0.label, $0.value) } }
///         }
///     }
struct DSRows<Content: View>: View {
    private let content: Content

    init(@ViewBuilder content: () -> Content) {
        self.content = content()
    }

    var body: some View {
        VStack(spacing: 0) {
            Group(subviews: content) { rows in
                ForEach(Array(rows.enumerated()), id: \.element.id) { index, row in
                    if index > 0 { DSDivider(row: true) }
                    row
                }
            }
        }
    }
}

/// One row's metrics (`ui-row`): at least 56 tall, 20 pt sides, 8 pt top
/// and bottom. `selected` raises it (`bg-raised`).
struct DSRow<Content: View>: View {
    var minHeight: CGFloat = DS.rowMinHeight
    var selected = false
    private let content: Content

    init(minHeight: CGFloat = DS.rowMinHeight, selected: Bool = false, @ViewBuilder content: () -> Content) {
        self.minHeight = minHeight
        self.selected = selected
        self.content = content()
    }

    var body: some View {
        content
            .padding(.horizontal, DS.rowPadding)
            .padding(.vertical, 8)
            .frame(maxWidth: .infinity, minHeight: minHeight, alignment: .leading)
            .background(selected ? DS.Surface.raised : Color.clear)
    }
}

/// A key-value row (the web's `dl` rows): the key in 14 zinc-400, the value
/// in 14 zinc-100 with monospaced digits, right-aligned.
struct DSKeyValueRow: View {
    let key: String
    let value: String
    var valueColor: Color = DS.Text.primary

    init(_ key: String, _ value: String, valueColor: Color = DS.Text.primary) {
        self.key = key
        self.value = value
        self.valueColor = valueColor
    }

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 16) {
            Text(key)
                .foregroundStyle(DS.Text.secondary)
            Spacer(minLength: 8)
            Text(value)
                .foregroundStyle(valueColor)
                .monospacedDigit()
                .multilineTextAlignment(.trailing)
        }
        .font(DS.Font.body)
        .accessibilityElement(children: .combine)
    }
}

/// A table header row (`ui-th`): 40 tall, 12 pt zinc-500 labels, a 6%
/// divider below. Lay the columns out yourself, numbers right-aligned.
///
///     DSTableHeader { Text("Order"); Spacer(); Text("Price") }
///     DSTableHeader("Symbol", trailing: "Value")
struct DSTableHeader<Content: View>: View {
    private let content: Content

    init(@ViewBuilder content: () -> Content) {
        self.content = content()
    }

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 12) { content }
                .font(DS.Font.caption)
                .foregroundStyle(DS.Text.muted)
                .padding(.horizontal, DS.rowPadding)
                .frame(maxWidth: .infinity, minHeight: DS.tableHeaderHeight, alignment: .leading)
            DSDivider()
        }
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(.isHeader)
    }
}

extension DSTableHeader where Content == TupleView<(Text, Spacer, Text)> {
    init(_ leading: String, trailing: String) {
        self.init { Text(leading); Spacer(); Text(trailing) }
    }
}

#Preview {
    ScrollView {
        VStack(spacing: 16) {
            Card {
                HeroValueHeader("$5,890.05", change: "+$62.13 (+1.07%)", direction: .up, changeLabel: "Today")
            }
            Card("Key statistics", padding: 0) {
                DSRows {
                    DSRow(minHeight: 44) { DSKeyValueRow("Prev close", "$188.42") }
                    DSRow(minHeight: 44) { DSKeyValueRow("Open", "$189.61") }
                }
            }
            Card(padding: 0) {
                DSTableHeader("Order", trailing: "Price")
                DSRows {
                    DSRow { EntityRow("Buy · Limit", subtitle: "2 sh · 3:27 PM") { EntityRowValue("$185.50", detail: "Open") } }
                    DSRow(selected: true) { EntityRow("Sell · Limit", subtitle: "1.25 sh") { EntityRowValue("$199.00", detail: "Open") } }
                }
            }
        }
        .padding()
    }
    .background(DS.Surface.page)
    .dsAppearance()
}
