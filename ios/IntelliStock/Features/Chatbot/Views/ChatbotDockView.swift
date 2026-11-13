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
/// and composer, on the web dock's surface colour.
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
                        placeholder: st.needsModel ? "Pick a model first…" : "Ask a question"
                    ) { text in
                        Task { await model.send(text) }
                    }
                }
                .background(DS.Surface.card)
        }
        .presentationDetents([.medium, .large], selection: $detent)
        .presentationBackground(DS.Surface.card)
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

    /// The model's own name, as the server spells it (no longer upper-cased).
    private var modelName: String {
        model.state.activeConversation?.modelName ?? ""
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
        // Settings and Clear share one menu (Clear last, destructive, still
        // confirmed); Minimise stays its own button.
        ToolbarItem(placement: .topBarTrailing) {
            ToolbarMenu("Chat options") {
                Button("Settings", systemImage: Symbol.named("settings")) {
                    settingsOpen = true
                }
                Section {
                    Button("Clear conversation", systemImage: Symbol.named("delete_sweep"), role: .destructive) {
                        confirm = ConfirmRequest(
                            title: "Clear conversation",
                            body: "This will delete all messages. This cannot be undone.",
                            confirmLabel: "Clear",
                            role: .destructive,
                            onConfirm: { await model.clearConversation() },
                            onError: { _ in }
                        )
                    }
                    .disabled(st.busy)
                }
            }
        }
        ToolbarItem(placement: .topBarTrailing) {
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

/// `_EmptyBody`: what to ask, with three suggestions as secondary buttons
/// (the web dock's empty state).
private struct ChatEmptyBody: View {
    let onSuggestion: (String) -> Void

    private static let suggestions = [
        "List my instances",
        "Show my portfolio over the last month",
        "How many backtests have I run?",
    ]

    var body: some View {
        ScrollView {
            VStack(spacing: 8) {
                Text("How can I help?")
                    .font(DS.Font.bodyMedium)
                    .foregroundStyle(DS.Text.soft)
                    .accessibilityAddTraits(.isHeader)
                Text("Ask about your portfolio, run a backtest or link a brokerage. I can show charts and tables, and run actions for you with your approval.")
                    .font(DS.Font.body)
                    .foregroundStyle(DS.Text.muted)
                    .lineSpacing(3)
                    .multilineTextAlignment(.center)
                    .frame(maxWidth: 320)
                VStack(spacing: 8) {
                    ForEach(Self.suggestions, id: \.self) { suggestion in
                        Button(suggestion) { onSuggestion(suggestion) }
                            .buttonStyle(.dsSecondary(size: .medium))
                    }
                }
                .padding(.top, 12)
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
                LazyVStack(spacing: 16) {
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
                        ChatErrorBox(message: error) { model.clearError() }
                            .padding(.top, 8)
                    }
                    Color.clear.frame(height: 1).id(ChatMessageList.bottomID)
                }
                .padding(.horizontal, DS.pageGutter)
                .padding(.top, 16)
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

/// The chat's error (the web dock's): loss text on a faint loss tint, with
/// an icon button that dismisses it.
private struct ChatErrorBox: View {
    let message: String
    let onDismiss: () -> Void

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            Text(message)
                .font(DS.Font.caption)
                .foregroundStyle(DS.Palette.loss)
                .frame(maxWidth: .infinity, alignment: .leading)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.vertical, 8)
            Button(action: onDismiss) {
                Image(systemName: "xmark")
            }
            .buttonStyle(.dsIcon)
            .accessibilityLabel("Dismiss error")
        }
        .padding(.leading, 12)
        .padding(.trailing, 2)
        .dsErrorSurface(radius: DS.Radius.control)
    }
}

/// `_ThinkingIndicator`: three zinc dots pulsing in turn, then `Thinking…`
/// (held still under Reduce Motion).
private struct ChatThinkingRow: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        HStack(spacing: 4) {
            TimelineView(.animation(paused: reduceMotion)) { context in
                let t = context.date.timeIntervalSinceReferenceDate.truncatingRemainder(dividingBy: 1.1) / 1.1
                HStack(spacing: 4) {
                    ForEach(0..<3, id: \.self) { i in
                        let phase = ((t - Double(i) * 0.15).truncatingRemainder(dividingBy: 1) + 1)
                            .truncatingRemainder(dividingBy: 1)
                        let wave = 1 - abs(2 * phase - 1)
                        Circle()
                            .fill(DS.Text.muted)
                            .frame(width: 5, height: 5)
                            .opacity(0.35 + 0.65 * wave)
                    }
                }
            }
            Text("Thinking…")
                .font(DS.Font.caption)
                .foregroundStyle(DS.Text.muted)
                .padding(.leading, 4)
        }
        .padding(.vertical, 4)
        .padding(.horizontal, 4)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Thinking…")
    }
}
