import SwiftUI

/// Stands in for a route's screen until its feature is ported. Removed once
/// no placeholder view references it.
struct PlaceholderScreen: View {
    let title: String
    let systemImage: String

    var body: some View {
        ContentUnavailableView(title, systemImage: systemImage, description: Text("Not ported yet."))
            .navigationTitle(title)
    }
}
