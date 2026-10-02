import SwiftUI

// The chat — `ChatbotDock` in `chatbot_dock.dart`, minus its entry point.
//
// The collapsed FAB is gone (spec 2026-10-02, G1): the entry is the tab bar's
// bottom accessory, `ChatAccessoryView`, and `chatbotPresenter()` hosts the
// model, the sheet (medium and large detents, presented from the tab view so
// it survives tab switches) and the navigate directives. This file keeps the
// session model and the expanded panel, unchanged.

/// The one chatbot model for the signed-in session — the keepAlive
/// `chatbotProvider`, held by `AppServices.chatbot` (rebuilt on sign-out), so
/// the shell's views can come and go (the lock tears them down) without losing
/// the conversation.
@MainActor
enum ChatbotSession {
    static func model(for services: AppServices) -> ChatbotModel {
        services.chatbot
    }

    /// Sign-out is handled by `AppServices.didSignOut`, which replaces the model.
    static func end() {}
}

// MARK: - Panel

/// The expanded chat — `_ExpandedPanel`: header, conversation switcher, body
/// and composer.
struct ChatbotPanel: View {
    let model: ChatbotModel
    let onNavigate: (String) -> Void

    @State private var detent: PresentationDetent = .large
    @State private var settingsOpen = false
    @State private var confirm: ConfirmRequest?
    /// A confirmation the settings sheet asked for; shown once it closes.
    @State private var pendingConfirm: ConfirmRequest?

    var body: some View {
        let st = model.state
        NavigationStack {
            ChatbotBody(model: model)
                .navigationTitle(st.activeConversation?.title ?? "Assistant")
                .navigationSubtitle(modelName)
                .navigationBarTitleDisplayMode(.inline)
                .toolbar { toolbar }
                .safeAreaInset(edge: .bottom, spacing: 0) {
                    ChatComposer(
                        busy: st.busy,
                        disabled: st.needsModel,
                        placeholder: st.needsModel ? "Pick a model first…" : "Ask me anything…"
                    ) { text in
                        Task { await model.send(text) }
                    }
                }
                .background(DS.Surface.canvas)
        }
        .presentationDetents([.medium, .large], selection: $detent)
        .presentationDragIndicator(.visible)
        .sheet(isPresented: $settingsOpen, onDismiss: {
            if let pendingConfirm {
                confirm = pendingConfirm
                self.pendingConfirm = nil
            }
        }) {
            ChatSettingsSheet(model: model) { request in
                pendingConfirm = request
                settingsOpen = false
            }
        }
        .confirmAlert($confirm)
    }

    private var modelName: String {
        let name = model.state.activeConversation?.modelName ?? ""
        return name.uppercased()
    }

    @ToolbarContentBuilder
    private var toolbar: some ToolbarContent {
        let st = model.state
        if st.conversations.count > 1 {
            ToolbarItem(placement: .topBarLeading) {
                Menu {
                    Button("New", systemImage: Symbol.named("add")) {
                        Task { await model.startNewConversationFromUI() }
                    }
                    Section("\(st.conversations.count) conversations") {
                        ForEach(st.conversations) { convo in
                            Button {
                                Task { await model.selectConversation(convo.id) }
                            } label: {
                                let active = convo.id == st.activeConversation?.id
                                Label(
                                    convo.title?.isEmpty == false ? convo.title! : "Untitled",
                                    systemImage: active ? Symbol.named("check") : Symbol.named("chat_bubble_outline")
                                )
                                Text("\(convo.messageCount) msg")
                            }
                        }
                    }
                } label: {
                    Image(systemName: Symbol.named("history"))
                }
                .accessibilityLabel("\(st.conversations.count) conversations")
            }
        }
        ToolbarItemGroup(placement: .topBarTrailing) {
            Button {
                settingsOpen = true
            } label: {
                Image(systemName: Symbol.named("settings"))
            }
            .accessibilityLabel("Settings")

            Button {
                confirm = ConfirmRequest(
                    title: "Clear conversation",
                    body: "This will delete all messages. This cannot be undone.",
                    confirmLabel: "Clear",
                    role: .destructive,
                    onConfirm: { await model.clearConversation() },
                    onError: { _ in }
                )
            } label: {
                Image(systemName: Symbol.named("delete_sweep"))
            }
            .disabled(st.busy)
            .accessibilityLabel("Clear conversation")

            Button {
                model.minimise()
            } label: {
                Image(systemName: Symbol.named("expand_more"))
            }
            .accessibilityLabel("Minimise")
        }
    }
}

// MARK: - Body

/// `_Body`: the first-run model picker, the empty state, or the messages.
private struct ChatbotBody: View {
    let model: ChatbotModel

    var body: some View {
        let st = model.state
        if st.needsModel {
            ChatModelPicker(model: model) { chosen in
                Task { await model.setModel(chosen.id) }
            }
        } else if st.messages.isEmpty {
            ChatEmptyBody { suggestion in
                Task { await model.send(suggestion) }
            }
        } else {
            ChatMessageList(model: model)
        }
    }
}

/// `_EmptyBody`: what to ask, with three suggestions.
private struct ChatEmptyBody: View {
    let onSuggestion: (String) -> Void

    private static let suggestions = [
        "List my instances",
        "Show my portfolio over the last month",
        "How many backtests have I run?",
    ]

    var body: some View {
        ScrollView {
            VStack(spacing: 0) {
                IconTile(systemImage: Symbol.named("smart_toy"), size: 48)
                Text("How can I help?")
                    .font(.headline)
                    .padding(.top, 12)
                Text("Ask about your portfolio, run a backtest, link a brokerage, or just say hi. I can show charts, tables, and run actions on your behalf with your approval.")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
                    .padding(.top, 6)
                ChatFlowLayout(spacing: 8, centered: true) {
                    ForEach(Self.suggestions, id: \.self) { suggestion in
                        Button(suggestion) { onSuggestion(suggestion) }
                            .font(.footnote)
                            .buttonStyle(.bordered)
                            .buttonBorderShape(.capsule)
                            .tint(.secondary)
                    }
                }
                .padding(.top, 16)
            }
            .padding(24)
            .frame(maxWidth: .infinity)
        }
        .scrollBounceBehavior(.basedOnSize)
        .defaultScrollAnchor(.center)
    }
}

/// `_MessageList`: bubbles, tool-call cards, the thinking row and the error.
private struct ChatMessageList: View {
    let model: ChatbotModel

    var body: some View {
        let st = model.state
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(spacing: 12) {
                    ForEach(st.messages) { message in
                        if message.isPendingConfirmation {
                            ChatToolCallCard(
                                message: message,
                                busy: st.busy,
                                onApprove: { Task { await model.confirmTool(message.id, approved: true) } },
                                onDecline: { Task { await model.confirmTool(message.id, approved: false) } }
                            )
                            .frame(maxWidth: .infinity, alignment: .leading)
                        } else {
                            ChatMessageBubble(message: message)
                        }
                    }
                    if st.busy {
                        ChatThinkingRow()
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                    if let error = st.error {
                        ErrorRow(message: error) { model.clearError() }
                            .padding(.top, 8)
                    }
                    Color.clear.frame(height: 1).id(ChatMessageList.bottomID)
                }
                .padding(.horizontal, 12)
                .padding(.top, 12)
                .padding(.bottom, 8)
            }
            .defaultScrollAnchor(.bottom)
            .scrollDismissesKeyboard(.interactively)
            .onChange(of: st.messages.count) { scrollToBottom(proxy) }
            .onChange(of: st.busy) { scrollToBottom(proxy) }
            .onReceive(NotificationCenter.default.publisher(for: UIResponder.keyboardDidShowNotification)) { _ in
                scrollToBottom(proxy)
            }
        }
    }

    static let bottomID = "chat-bottom"

    private func scrollToBottom(_ proxy: ScrollViewProxy) {
        withAnimation(.easeOut(duration: 0.25)) {
            proxy.scrollTo(Self.bottomID, anchor: .bottom)
        }
    }
}

/// `_ThinkingIndicator`: three dots pulsing in turn, then `Thinking…`.
private struct ChatThinkingRow: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        HStack(spacing: 3) {
            TimelineView(.animation(paused: reduceMotion)) { context in
                let t = context.date.timeIntervalSinceReferenceDate.truncatingRemainder(dividingBy: 1.1) / 1.1
                HStack(spacing: 3) {
                    ForEach(0..<3, id: \.self) { i in
                        let phase = ((t - Double(i) * 0.15).truncatingRemainder(dividingBy: 1) + 1)
                            .truncatingRemainder(dividingBy: 1)
                        let wave = 1 - abs(2 * phase - 1)
                        Circle()
                            .fill(DS.Palette.accent)
                            .frame(width: 6, height: 6)
                            .scaleEffect(0.55 + 0.45 * wave)
                            .opacity(0.4 + 0.6 * wave)
                    }
                }
            }
            Text("Thinking…")
                .font(.caption)
                .foregroundStyle(.secondary)
                .padding(.leading, 5)
        }
        .padding(.vertical, 8)
        .padding(.horizontal, 4)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Thinking…")
    }
}

