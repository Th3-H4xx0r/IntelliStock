import Foundation
import Observation
import SwiftUI

/// Which screens on display hide the floating chat button. A screen with its
/// own bottom floating layer (Live Trading's Halt button and command toast)
/// would otherwise sit under the chat button, which `RootView` stacks over
/// every tab. Each such screen registers while it is on screen.
@Observable
final class ChatDockChrome {
    private(set) var hidingScreens: Set<UUID> = []

    /// The chat button stays out of the way.
    var hidesButton: Bool { !hidingScreens.isEmpty }

    func hide(for screen: UUID) { hidingScreens.insert(screen) }
    func show(for screen: UUID) { hidingScreens.remove(screen) }
}

extension View {
    /// This screen floats its own controls at the bottom: hide the chat
    /// button while it is on screen (a tab switch or a push shows it again).
    func hidesChatButton() -> some View {
        modifier(HidesChatButton())
    }
}

private struct HidesChatButton: ViewModifier {
    @Environment(AppServices.self) private var services
    @State private var token = UUID()

    func body(content: Content) -> some View {
        content
            .onAppear { services.chatDock.hide(for: token) }
            .onDisappear { services.chatDock.show(for: token) }
    }
}
