import SwiftUI

/// A `List` section with the standard iOS 26 header (title case, no upper
/// case or tracking, no icon), an optional trailing header action, and an
/// optional footer. Use it like `Section`; with no action it renders exactly
/// as `Section("Title")`.
///
/// The header action is for a secondary, section-wide verb such as "See All",
/// "Add" or "Edit", drawn as plain accent text. The screen's primary action
/// belongs in the toolbar instead. For a custom accessory (a segmented
/// `Picker`, a `Menu`), use `Section { … } header: { DSSectionHeader("Title") { … } }`.
///
///     DSSection("Stocks", action: DSSectionAction("Add", systemImage: "plus") { adding = true },
///               footer: "The instance trades only these symbols.") {
///         ForEach(symbols, id: \.self) { Text($0) }
///     }
struct DSSection<Content: View, Footer: View>: View {
    let title: String?
    var action: DSSectionAction?
    private let content: Content
    private let footer: Footer

    init(
        _ title: String? = nil,
        action: DSSectionAction? = nil,
        @ViewBuilder content: () -> Content,
        @ViewBuilder footer: () -> Footer
    ) {
        self.title = title
        self.action = action
        self.content = content()
        self.footer = footer()
    }

    var body: some View {
        Section {
            content
        } header: {
            if title != nil || action != nil {
                DSSectionHeader(title ?? "") {
                    if let action {
                        Button(action: action.action) {
                            if let systemImage = action.systemImage {
                                Label(action.title, systemImage: systemImage)
                            } else {
                                Text(action.title)
                            }
                        }
                        .disabled(action.isDisabled)
                    }
                }
            }
        } footer: {
            footer
        }
    }
}

extension DSSection where Footer == EmptyView {
    init(_ title: String? = nil, action: DSSectionAction? = nil, @ViewBuilder content: () -> Content) {
        self.init(title, action: action, content: content) { EmptyView() }
    }
}

extension DSSection where Footer == Text {
    /// A section with a plain-text footer: an explanation, a caveat, a count.
    init(_ title: String? = nil, action: DSSectionAction? = nil, footer: String, @ViewBuilder content: () -> Content) {
        self.init(title, action: action, content: content) { Text(footer) }
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

/// A list section header: the title as the system draws it (never upper-
/// cased), with an optional trailing accessory such as a button, a compact
/// segmented `Picker` or a `Menu`.
///
///     Section {
///         …
///     } header: {
///         DSSectionHeader("Holdings") {
///             Picker("P&L", selection: $mode) { … }
///                 .pickerStyle(.segmented)
///                 .fixedSize()
///         }
///     }
struct DSSectionHeader<Accessory: View>: View {
    let title: String
    private let accessory: Accessory

    init(_ title: String, @ViewBuilder accessory: () -> Accessory) {
        self.title = title
        self.accessory = accessory()
    }

    var body: some View {
        HStack(alignment: .center, spacing: 8) {
            Text(title)
                .accessibilityAddTraits(.isHeader)
            Spacer(minLength: 8)
            accessory
        }
        .textCase(nil)
    }
}

extension DSSectionHeader where Accessory == EmptyView {
    init(_ title: String) {
        self.init(title) { EmptyView() }
    }
}

/// A secondary action drawn as a list row, the way Settings shows "Add
/// Account" or "Sign Out": accent text (red for `role: .destructive`), an
/// optional leading SF Symbol, and a spinner while `isBusy`. Never a tinted
/// capsule.
///
/// In a `List` or `Form` the whole row is the tap target and highlights on
/// press. Outside a list it draws as a full-width text button. Keep the
/// action's confirmation where it has one.
///
///     DSSection("Engine") {
///         InlineActionRow("Clear State", systemImage: "eraser") { confirmClear = true }
///         InlineActionRow("Delete Instance", systemImage: "trash", role: .destructive) { confirmDelete = true }
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
        Button(role: role, action: action) {
            HStack(spacing: 8) {
                if let systemImage {
                    Label(title, systemImage: systemImage)
                } else {
                    Text(title)
                }
                Spacer(minLength: 8)
                if isBusy {
                    ProgressView()
                }
            }
            .frame(maxWidth: .infinity, minHeight: 28, alignment: .leading)
            .contentShape(Rectangle())
        }
        // A destructive row's glyph is red too, not just its text.
        .tint(role == .destructive ? DS.Palette.danger : DS.Palette.accent)
        .disabled(isBusy)
    }
}

#Preview {
    struct Demo: View {
        @State private var mode = 0

        var body: some View {
            NavigationStack {
                List {
                    DSSection("Stocks", action: DSSectionAction("Add", systemImage: "plus") {}) {
                        Text("AAPL")
                        Text("NVDA")
                    }
                    Section {
                        Text("GLD")
                    } header: {
                        DSSectionHeader("Holdings") {
                            Picker("P&L", selection: $mode) {
                                Text("Total").tag(0)
                                Text("Daily").tag(1)
                            }
                            .pickerStyle(.segmented)
                            .fixedSize()
                            .controlSize(.small)
                        }
                    }
                    DSSection("Engine", footer: "Clearing state wipes the instance's memory. It cannot be undone.") {
                        InlineActionRow("View Live Logs", systemImage: "text.alignleft") {}
                        InlineActionRow("Clear State", systemImage: "eraser", isBusy: true) {}
                        InlineActionRow("Delete Instance", systemImage: "trash", role: .destructive) {}
                    }
                }
                .navigationTitle("Sections")
            }
        }
    }
    return Demo()
}
