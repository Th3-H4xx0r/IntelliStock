import SwiftUI

// MARK: - The web list

extension View {
    /// The web look for a `List`: grouped (full-width rows, headers that
    /// scroll with the content), on the page colour, system separators off.
    /// Its sections are `DSSection`s, which draw each section's rows as one
    /// card with 5% hairlines between them; swipe actions, context menus and
    /// `NavigationLink` rows keep working.
    ///
    ///     List {
    ///         DSSection("Holdings", subtitle: "4 positions") {
    ///             ForEach(rows) { row in NavigationLink(value: …) { EntityRow(…) } }
    ///         }
    ///     }
    ///     .dsList()
    func dsList(sectionSpacing: CGFloat = DS.sectionGap) -> some View {
        listStyle(.grouped)
            .scrollContentBackground(.hidden)
            .background(DS.Surface.page)
            .listSectionSpacing(sectionSpacing)
            .environment(\.defaultMinListRowHeight, 1)
            .environment(\.defaultMinListHeaderHeight, 1)
    }

    /// A list row that draws its own surface (a chart, a hero, a row of
    /// cards): no row background, no separator, the page gutter at the sides.
    func dsClearRow(top: CGFloat = 0, bottom: CGFloat = 0, horizontal: CGFloat = DS.pageGutter) -> some View {
        listRowBackground(Color.clear)
            .listRowSeparator(.hidden)
            .listRowInsets(EdgeInsets(top: top, leading: horizontal, bottom: bottom, trailing: horizontal))
    }

    /// One row of a card in a `.dsList()`: the card's segment behind it,
    /// 20 pt inside the card, no system separator. `DSSection` applies this
    /// to every row; call it yourself only for a row built outside one.
    func dsCardRow(_ position: DSRowPosition, minHeight: CGFloat = DS.rowMinHeight, fill: Color = DS.Surface.card, vertical: CGFloat = 8) -> some View {
        self
            .frame(minHeight: max(minHeight - vertical * 2, 0))
            .listRowBackground(DSCardRowBackground(position: position, fill: fill))
            .listRowSeparator(.hidden)
            .listRowInsets(EdgeInsets(
                top: vertical,
                leading: DS.pageGutter + DS.rowPadding,
                bottom: vertical,
                trailing: DS.pageGutter + DS.rowPadding
            ))
    }
}

/// Where a row sits in its card.
nonisolated struct DSRowPosition: Hashable, Sendable {
    var isFirst: Bool
    var isLast: Bool

    static let only = DSRowPosition(isFirst: true, isLast: true)
    static let first = DSRowPosition(isFirst: true, isLast: false)
    static let middle = DSRowPosition(isFirst: false, isLast: false)
    static let last = DSRowPosition(isFirst: false, isLast: true)

    init(isFirst: Bool, isLast: Bool) {
        self.isFirst = isFirst
        self.isLast = isLast
    }

    init(index: Int, count: Int) {
        self.init(isFirst: index == 0, isLast: index == count - 1)
    }
}

/// One row's slice of a card: the fill, the card's hairline where this row
/// meets the card's edge, the round corners on the first and last rows, and
/// a 5% hairline above every row but the first. Inset by the page gutter.
struct DSCardRowBackground: View {
    let position: DSRowPosition
    var fill: Color = DS.Surface.card

    var body: some View {
        let r = DS.Radius.card
        let shape = UnevenRoundedRectangle(
            topLeadingRadius: position.isFirst ? r : 0,
            bottomLeadingRadius: position.isLast ? r : 0,
            bottomTrailingRadius: position.isLast ? r : 0,
            topTrailingRadius: position.isFirst ? r : 0,
            style: .continuous
        )
        ZStack(alignment: .top) {
            shape.fill(fill)
            // The card's outline, run past the row's open edges and clipped
            // there, so only the sides (and the closed ends) show.
            RoundedRectangle(cornerRadius: r, style: .continuous)
                .strokeBorder(DS.Line.card, lineWidth: DS.Line.width)
                .padding(.top, position.isFirst ? 0 : -(r + 4))
                .padding(.bottom, position.isLast ? 0 : -(r + 4))
            if !position.isFirst {
                Rectangle()
                    .fill(DS.Line.row)
                    .frame(height: DS.Line.width)
                    .padding(.horizontal, DS.Line.width)
            }
        }
        .clipped()
        .padding(.horizontal, DS.pageGutter)
    }
}

// MARK: - DSSection

/// A `List` section in the web style: the header outside the card — a
/// 16/500 title, an optional 14 pt zinc-500 subtitle and a trailing action —
/// then the rows on one card, then an optional 12 pt footer. Use it in a
/// `.dsList()` the way you used `Section`.
///
/// - `action:` is a secondary, section-wide verb ("See all", "Add"), drawn
///   as a ghost text button. For a custom accessory (a segmented control),
///   pass `accessory:`.
/// - `style: .plain` keeps the header but draws no card: for rows that bring
///   their own surface (charts, cards), each made a `dsClearRow()`.
///
///     DSSection("Stocks", subtitle: "The instance trades only these",
///               action: DSSectionAction("Add", systemImage: "plus") { adding = true }) {
///         ForEach(symbols, id: \.self) { Text($0) }
///     }
struct DSSection<Content: View, Footer: View, Accessory: View>: View {
    enum Style { case card, plain }

    let title: String?
    var subtitle: String?
    var action: DSSectionAction?
    var style: Style = .card
    /// The rows' minimum height (56, the web's `min-h-14`).
    var rowMinHeight: CGFloat = DS.rowMinHeight
    private let content: Content
    private let footer: Footer
    private let accessory: Accessory

    init(
        _ title: String? = nil,
        subtitle: String? = nil,
        action: DSSectionAction? = nil,
        style: Style = .card,
        rowMinHeight: CGFloat = DS.rowMinHeight,
        @ViewBuilder content: () -> Content,
        @ViewBuilder footer: () -> Footer,
        @ViewBuilder accessory: () -> Accessory
    ) {
        self.title = title
        self.subtitle = subtitle
        self.action = action
        self.style = style
        self.rowMinHeight = rowMinHeight
        self.content = content()
        self.footer = footer()
        self.accessory = accessory()
    }

    var body: some View {
        Section {
            switch style {
            case .card:
                Group(subviews: content) { rows in
                    ForEach(Array(rows.enumerated()), id: \.element.id) { index, row in
                        row.dsCardRow(DSRowPosition(index: index, count: rows.count), minHeight: rowMinHeight)
                    }
                }
            case .plain:
                content
            }
        } header: {
            if title != nil || action != nil || Accessory.self != EmptyView.self {
                DSSectionHeader(title ?? "", subtitle: subtitle) {
                    accessory
                    if let action {
                        Button(action: action.action) {
                            if let systemImage = action.systemImage {
                                Label(action.title, systemImage: systemImage)
                            } else {
                                Text(action.title)
                            }
                        }
                        .buttonStyle(.dsGhost)
                        .padding(.trailing, -12)
                        .disabled(action.isDisabled)
                    }
                }
            }
        } footer: {
            if Footer.self != EmptyView.self {
                footer
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Text.muted)
                    .textCase(nil)
                    .listRowInsets(EdgeInsets(top: 8, leading: DS.pageGutter, bottom: 0, trailing: DS.pageGutter))
            }
        }
    }
}

extension DSSection where Accessory == EmptyView {
    init(
        _ title: String? = nil,
        subtitle: String? = nil,
        action: DSSectionAction? = nil,
        style: Style = .card,
        rowMinHeight: CGFloat = DS.rowMinHeight,
        @ViewBuilder content: () -> Content,
        @ViewBuilder footer: () -> Footer
    ) {
        self.init(title, subtitle: subtitle, action: action, style: style, rowMinHeight: rowMinHeight, content: content, footer: footer) { EmptyView() }
    }
}

extension DSSection where Footer == EmptyView, Accessory == EmptyView {
    init(
        _ title: String? = nil,
        subtitle: String? = nil,
        action: DSSectionAction? = nil,
        style: Style = .card,
        rowMinHeight: CGFloat = DS.rowMinHeight,
        @ViewBuilder content: () -> Content
    ) {
        self.init(title, subtitle: subtitle, action: action, style: style, rowMinHeight: rowMinHeight, content: content) { EmptyView() } accessory: { EmptyView() }
    }
}

extension DSSection where Footer == EmptyView {
    /// A section with a custom header accessory (a segmented control).
    init(
        _ title: String? = nil,
        subtitle: String? = nil,
        style: Style = .card,
        rowMinHeight: CGFloat = DS.rowMinHeight,
        @ViewBuilder content: () -> Content,
        @ViewBuilder accessory: () -> Accessory
    ) {
        self.init(title, subtitle: subtitle, action: nil, style: style, rowMinHeight: rowMinHeight, content: content) { EmptyView() } accessory: { accessory() }
    }
}

extension DSSection where Footer == Text, Accessory == EmptyView {
    /// A section with a plain-text footer: an explanation, a caveat, a count.
    init(
        _ title: String? = nil,
        subtitle: String? = nil,
        action: DSSectionAction? = nil,
        style: Style = .card,
        footer: String,
        @ViewBuilder content: () -> Content
    ) {
        self.init(title, subtitle: subtitle, action: action, style: style, content: content) { Text(footer) }
    }
}

/// The trailing verb in a `DSSection` header.
struct DSSectionAction {
    let title: String
    var systemImage: String?
    var isDisabled = false
    let action: () -> Void

    init(_ title: String, systemImage: String? = nil, isDisabled: Bool = false, action: @escaping () -> Void) {
        self.title = title
        self.systemImage = systemImage
        self.isDisabled = isDisabled
        self.action = action
    }
}

/// A section header in the web style (`ui-section-title` + `ui-section-sub`),
/// with an optional trailing accessory. In a `List` it lines up with the
/// card's outer edge, 12 pt above the card; outside a list use
/// `SectionHeader`.
///
///     Section { … } header: {
///         DSSectionHeader("Holdings", subtitle: "4 positions") {
///             DSSegmentedControl("P&L", selection: $mode, options: …) { $0.label }
///         }
///     }
struct DSSectionHeader<Accessory: View>: View {
    let title: String
    var subtitle: String?
    private let accessory: Accessory

    init(_ title: String, subtitle: String? = nil, @ViewBuilder accessory: () -> Accessory) {
        self.title = title
        self.subtitle = subtitle
        self.accessory = accessory()
    }

    var body: some View {
        HStack(alignment: .center, spacing: 12) {
            VStack(alignment: .leading, spacing: 2) {
                if !title.isEmpty {
                    Text(title)
                        .font(DS.Font.sectionTitle)
                        .foregroundStyle(DS.Text.primary)
                        .accessibilityAddTraits(.isHeader)
                }
                if let subtitle {
                    Text(subtitle)
                        .font(DS.Font.sectionSub)
                        .foregroundStyle(DS.Text.muted)
                        .monospacedDigit()
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            accessory
        }
        .textCase(nil)
        .listRowInsets(EdgeInsets(top: 0, leading: DS.pageGutter, bottom: DS.sectionHeaderGap, trailing: DS.pageGutter))
    }
}

extension DSSectionHeader where Accessory == EmptyView {
    init(_ title: String, subtitle: String? = nil) {
        self.init(title, subtitle: subtitle) { EmptyView() }
    }
}

/// A secondary action drawn as a row, the way Settings shows "Add account"
/// or "Sign out": 14 pt off-white text (loss red for `role: .destructive`),
/// an optional leading glyph in zinc-500, and a spinner while `isBusy`.
///
///     DSSection("Engine") {
///         InlineActionRow("Clear state", systemImage: "eraser") { confirmClear = true }
///         InlineActionRow("Delete instance", systemImage: "trash", role: .destructive) { confirmDelete = true }
///     }
struct InlineActionRow: View {
    let title: String
    var systemImage: String?
    var role: ButtonRole?
    var isBusy = false
    let action: () -> Void

    init(
        _ title: String,
        systemImage: String? = nil,
        role: ButtonRole? = nil,
        isBusy: Bool = false,
        action: @escaping () -> Void
    ) {
        self.title = title
        self.systemImage = systemImage
        self.role = role
        self.isBusy = isBusy
        self.action = action
    }

    var body: some View {
        let ink = role == .destructive ? DS.Palette.loss : DS.Text.primary
        Button(role: role, action: action) {
            HStack(spacing: 12) {
                if let systemImage {
                    Image(systemName: systemImage)
                        .font(.system(size: 16))
                        .foregroundStyle(role == .destructive ? DS.Palette.loss : DS.Text.muted)
                        .frame(width: 20)
                        .accessibilityHidden(true)
                }
                Text(title)
                    .font(DS.Font.body)
                    .foregroundStyle(ink)
                Spacer(minLength: 8)
                if isBusy {
                    ProgressView()
                        .controlSize(.small)
                }
            }
            .frame(maxWidth: .infinity, minHeight: 28, alignment: .leading)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(isBusy)
    }
}

#Preview {
    struct Demo: View {
        @State private var mode = "Daily"

        var body: some View {
            NavigationStack {
                List {
                    DSSection("Stocks", subtitle: "The instance trades only these", action: DSSectionAction("Add", systemImage: "plus") {}) {
                        Text("AAPL")
                        Text("NVDA")
                    }
                    DSSection("Holdings", subtitle: "4 positions") {
                        NavigationLink(value: 1) { EntityRow("GLD", subtitle: "5.53 sh") { EntityRowValue("$2,018.58", detail: "−0.89%", detailColor: DS.Palette.loss) } }
                        NavigationLink(value: 2) { EntityRow("BIL", subtitle: "14.8 sh") { EntityRowValue("$1,357.52", detail: "0.00%") } }
                    } accessory: {
                        DSSegmentedControl("P&L", selection: $mode, options: ["Daily", "Total"]) { $0 }
                    }
                    DSSection("Engine", footer: "Clearing state wipes the instance's memory. It cannot be undone.") {
                        InlineActionRow("View live logs", systemImage: "text.alignleft") {}
                        InlineActionRow("Clear state", systemImage: "eraser", isBusy: true) {}
                        InlineActionRow("Delete instance", systemImage: "trash", role: .destructive) {}
                    }
                }
                .dsList()
                .navigationTitle("Sections")
                .navigationDestination(for: Int.self) { Text("Detail \($0)") }
            }
            .dsAppearance()
        }
    }
    return Demo()
}
