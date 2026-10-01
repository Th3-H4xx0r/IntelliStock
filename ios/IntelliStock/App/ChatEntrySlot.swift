import SwiftUI

/// The overlay hook for the global chat entry — `app.dart` stacked
/// `ChatbotDock` over the whole app, and the dock hid itself when signed out.
/// The chat agent owns `ChatbotDockView`; this slot decides when it shows.
struct ChatEntrySlot: View {
    @Environment(AppServices.self) private var services

    var body: some View {
        if services.session.isAuthenticated {
            ChatbotDockView()
        }
    }
}
