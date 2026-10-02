import SwiftUI

/// The chat's entry point: the tab bar's bottom accessory (iOS 26
/// `tabViewBottomAccessory`), which replaced the floating violet button
/// (spec 2026-10-02, G1). The system draws it as a compact glass capsule above
/// the tab bar, like the Music mini player, so it never covers content.
///
/// It shows `sparkles`, "Ask IntelliStock" and, once a model is picked, the
/// current conversation's model name in `.secondary`. Tapping it opens the
/// chat sheet that `chatbotPresenter()` hosts. It reads the session's
/// `ChatbotModel` from the environment that `chatbotPresenter()` sets, so
/// apply both to the same `TabView`:
///
///     TabView { … }
///         .tabViewBottomAccessory { ChatAccessoryView() }
///         .chatbotPresenter()
struct ChatAccessoryView: View {
    @Environment(ChatbotModel.self) private var model: ChatbotModel?
    @Environment(\.tabViewBottomAccessoryPlacement) private var placement

    var body: some View {
        let modelName = ChatAccessory.modelName(model?.state)
        Button {
            model?.open()
        } label: {
            HStack(spacing: 10) {
                Image(systemName: "sparkles")
                    .font(.body.weight(.semibold))
                    .foregroundStyle(.tint)
                    .accessibilityHidden(true)
                Text("Ask IntelliStock")
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(.primary)
                    .lineLimit(1)
                Spacer(minLength: 8)
                // Inline (beside a minimised tab bar) there is room for the title only.
                if placement != .inline, let modelName {
                    Text(ChatAccessory.shortName(modelName))
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }
            }
            .padding(.horizontal, 16)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(model == nil)
        .accessibilityLabel("Ask IntelliStock")
        .accessibilityValue(modelName ?? "")
        .accessibilityHint("Opens the chat")
    }
}

/// What the accessory shows, as pure logic.
nonisolated enum ChatAccessory {
    /// The model to name: the active conversation's `model_name`, else the
    /// catalog name for its `model_id`. nil while no model is picked (the
    /// chat opens on the model picker then) or the name is blank.
    static func modelName(_ state: ChatbotState?) -> String? {
        guard let state, !state.needsModel, let convo = state.activeConversation else { return nil }
        if let name = convo.modelName?.trimmingCharacters(in: .whitespacesAndNewlines), !name.isEmpty {
            return name
        }
        let catalog = state.models.first { $0.id == convo.modelId }?.name
            .trimmingCharacters(in: .whitespacesAndNewlines)
        return catalog?.isEmpty == false ? catalog : nil
    }

    /// The model part of a "Provider / Model" name, which is what fits beside
    /// the title ("OpenRouter / Gemini 3.7 Flash" → "Gemini 3.7 Flash"). A name
    /// without a provider prefix is returned whole. The chat sheet's subtitle
    /// still shows the full name.
    static func shortName(_ name: String) -> String {
        guard let range = name.range(of: " / ", options: .backwards) else { return name }
        let tail = name[range.upperBound...].trimmingCharacters(in: .whitespacesAndNewlines)
        return tail.isEmpty ? name : tail
    }
}

extension View {
    /// Hosts the chat for the signed-in shell. Apply it once, to the
    /// `TabView` that carries `ChatAccessoryView`.
    ///
    /// It holds the session's one `ChatbotModel` (`ChatbotSession`), shares it
    /// with the accessory through the environment, and bootstraps it on
    /// appear. It presents `ChatbotPanel` as a sheet (medium and large
    /// detents) while the model is open, and runs the panel's navigate
    /// directives: minimise the chat, then `router.open(route)`. Signing out
    /// ends the session's model, as the Dart provider reset. This is the
    /// wiring the floating dock had, moved unchanged.
    func chatbotPresenter() -> some View {
        modifier(ChatbotPresenter())
    }
}

/// The dock's model, sheet and navigation wiring, minus the floating button.
private struct ChatbotPresenter: ViewModifier {
    @Environment(AppServices.self) private var services
    @State private var model: ChatbotModel?

    func body(content: Content) -> some View {
        content
            .environment(model)
            .sheet(isPresented: sheetBinding) {
                if let model {
                    ChatbotPanel(model: model, onNavigate: navigate)
                }
            }
            .onChange(of: model?.state.messages ?? []) {
                guard let model else { return }
                if let route = model.takeNavigations().last { navigate(route) }
            }
            .onAppear {
                let model = ChatbotSession.model(for: services)
                self.model = model
                Task { await model.bootstrap() }
            }
            .onDisappear {
                // Signed out: the Dart provider reset to a blank state.
                if !services.session.isAuthenticated { ChatbotSession.end() }
            }
    }

    private var sheetBinding: Binding<Bool> {
        Binding(
            get: { model?.state.isOpen ?? false },
            set: { open in
                if !open { model?.minimise() }
            }
        )
    }

    /// A navigate directive: close the chat and go there.
    private func navigate(_ route: String) {
        model?.minimise()
        services.router.open(route)
    }
}

#Preview("Accessory") {
    TabView {
        Tab("Dashboard", systemImage: "square.grid.2x2.fill") {
            NavigationStack {
                List { Text("Content scrolls under the tab bar.") }
                    .navigationTitle("Dashboard")
            }
        }
        Tab("More", systemImage: "ellipsis") { Text("More") }
    }
    .tabViewBottomAccessory { ChatAccessoryView() }
}
