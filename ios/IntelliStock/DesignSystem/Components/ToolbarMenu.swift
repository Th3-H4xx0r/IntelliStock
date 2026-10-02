import SwiftUI

/// The symbols of the toolbar convention (spec 2026-10-02, G3), in one place.
nonisolated enum ToolbarSymbol {
    /// The More menu. iOS 26 draws every toolbar item on its own glass
    /// circle, so the bare `ellipsis` reads as Apple's "ellipsis in a circle";
    /// `ellipsis.circle` would draw a circle inside the circle
    /// (`toolbars.md › Actions`: "Prefer system-provided symbols without
    /// borders").
    static let more = "ellipsis"
    /// Create.
    static let add = "plus"
}

/// The toolbar's More menu: `Menu` with `Label("More", systemImage:
/// ToolbarSymbol.more)` (`toolbars.md › Best practices`: "Add a More menu to
/// contain additional actions").
///
/// Everything that is not the screen's single primary action goes here. Group
/// related items with `Section`s, and put destructive items last with
/// `role: .destructive`, still behind their confirmations. Sort and page-size
/// choices go here too, as `Picker`s, which draw as checkmarked submenus.
///
///     .toolbar {
///         ToolbarItem(placement: .primaryAction) {
///             Button("Start") { start() }.dsProminentButton()
///         }
///         ToolbarItem(placement: .topBarTrailing) {
///             ToolbarMenu {
///                 Section {
///                     Button("Live Trading", systemImage: "chart.xyaxis.line") { … }
///                     Button("Change Brokerage", systemImage: "arrow.left.arrow.right") { … }
///                 }
///                 Section {
///                     Button("Delete", systemImage: "trash", role: .destructive) { confirmDelete = true }
///                 }
///             }
///         }
///     }
struct ToolbarMenu<Content: View>: View {
    let title: String
    private let content: Content

    /// `title` names the menu for VoiceOver; the toolbar shows the symbol.
    init(_ title: String = "More", @ViewBuilder content: () -> Content) {
        self.title = title
        self.content = content()
    }

    var body: some View {
        Menu {
            content
        } label: {
            Label(title, systemImage: ToolbarSymbol.more)
        }
    }
}

/// The toolbar's create button: a bare `plus` whose `title` ("New Instance",
/// "Link Brokerage", "Add Model") is what VoiceOver reads. One per screen, on
/// the trailing side, replacing any "Create …" pill in the content.
///
///     ToolbarItem(placement: .primaryAction) {
///         ToolbarAddButton("New Instance") { creating = true }
///     }
struct ToolbarAddButton: View {
    let title: String
    let action: () -> Void

    init(_ title: String, action: @escaping () -> Void) {
        self.title = title
        self.action = action
    }

    var body: some View {
        Button(title, systemImage: ToolbarSymbol.add, action: action)
    }
}

#Preview {
    struct Demo: View {
        @State private var sort = "Name"

        var body: some View {
            NavigationStack {
                List {
                    Text("Sorted by \(sort)")
                }
                .navigationTitle("Strategies")
                .toolbar {
                    ToolbarItem(placement: .topBarTrailing) {
                        ToolbarMenu("Sort and Page Size") {
                            Picker("Sort", selection: $sort) {
                                ForEach(["Name", "Best P&L", "Best P&L %", "Backtests"], id: \.self) { Text($0) }
                            }
                            Section {
                                Button("Delete All", systemImage: "trash", role: .destructive) {}
                            }
                        }
                    }
                    ToolbarItem(placement: .primaryAction) {
                        ToolbarAddButton("Create Strategy") {}
                    }
                }
            }
        }
    }
    return Demo()
}
