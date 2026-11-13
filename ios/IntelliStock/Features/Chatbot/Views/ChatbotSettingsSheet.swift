import SwiftUI

/// Chat settings — `ChatSettingsSheet` in `chat_settings_sheet.dart`, styled
/// as the web's `ChatSettings.vue`: the model, tool execution, conversation
/// actions and the tool catalog, each a `DSSection` on the page colour.
///
/// A sheet (SwiftUI has no Navigator problem, so the in-tree scrim and panel
/// are gone). Clear and Delete hand their confirmation back to the chat
/// panel, which shows it once this closes.
struct ChatSettingsSheet: View {
    let model: ChatbotModel
    /// Asks the panel to confirm (this sheet closes first).
    let onConfirm: (ConfirmRequest) -> Void

    @Environment(\.dismiss) private var dismiss

    var body: some View {
        let st = model.state
        let convo = st.activeConversation
        NavigationStack {
            List {
                DSSection("Model") {
                    if !st.modelsLoaded, st.models.isEmpty {
                        LoadingState(label: "Loading models…")
                    } else if st.modelsLoaded, st.models.isEmpty {
                        Text("No models configured yet. Add one on the Models page.")
                            .font(DS.Font.body)
                            .foregroundStyle(DS.Text.secondary)
                    } else {
                        ForEach(st.models) { m in
                            let selected = convo?.modelId == m.id
                            Button {
                                Task { await model.setModel(m.id) }
                            } label: {
                                HStack(spacing: 12) {
                                    VStack(alignment: .leading, spacing: 2) {
                                        Text(m.name)
                                            .font(DS.Font.body)
                                            .foregroundStyle(DS.Text.primary)
                                        let sub = ChatBlockFormat.modelSubtitle(provider: m.provider, model: m.model)
                                        if !sub.isEmpty {
                                            Text(sub)
                                                .font(DS.Font.caption)
                                                .foregroundStyle(DS.Text.muted)
                                        }
                                    }
                                    Spacer(minLength: 8)
                                    if selected {
                                        Image(systemName: "checkmark")
                                            .font(.system(size: 14, weight: .medium))
                                            .foregroundStyle(DS.Text.primary)
                                            .accessibilityHidden(true)
                                    }
                                }
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .contentShape(Rectangle())
                            }
                            .buttonStyle(ChatModelChoiceStyle(selected: selected))
                            .disabled(selected || st.busy)
                            .accessibilityAddTraits(selected ? .isSelected : [])
                        }
                    }
                }

                DSSection(
                    "Tool execution",
                    footer: "When on, the assistant can call read-only tools like list_instances without asking. Mutating tools always require approval."
                ) {
                    Toggle(isOn: Binding(
                        get: { convo?.autoConfirmSafeTools ?? false },
                        set: { value in Task { await model.setAutoConfirmSafe(value) } }
                    )) {
                        Text("Auto-run safe (read-only) tools")
                            .font(DS.Font.body)
                            .foregroundStyle(DS.Text.soft)
                    }
                    .disabled(convo == nil)
                }

                DSSection("Conversation") {
                    InlineActionRow("New conversation", systemImage: Symbol.named("add")) {
                        Task { await model.startNewConversationFromUI() }
                        dismiss()
                    }
                    InlineActionRow("Clear", systemImage: Symbol.named("delete_sweep")) {
                        onConfirm(ConfirmRequest(
                            title: "Clear conversation",
                            body: "This deletes all messages in this conversation. This cannot be undone.",
                            confirmLabel: "Clear",
                            role: nil,
                            onConfirm: { await model.clearConversation() },
                            onError: { _ in }
                        ))
                    }
                    .disabled(convo == nil || st.busy)
                    InlineActionRow("Delete", systemImage: Symbol.named("delete_outline"), role: .destructive) {
                        onConfirm(ConfirmRequest(
                            title: "Delete conversation",
                            body: "This permanently deletes this conversation. This cannot be undone.",
                            confirmLabel: "Delete",
                            role: .destructive,
                            onConfirm: { await model.deleteConversation() },
                            onError: { _ in }
                        ))
                    }
                    .disabled(convo == nil || st.busy)
                }

                if !st.toolCatalog.isEmpty {
                    let groups = ChatToolGroups(st.toolCatalog)
                    DSSection("Tools the assistant can use") {
                        if !groups.safe.isEmpty {
                            ChatToolGroupRow(title: "Read-only", tone: .off, tools: groups.safe, startsOpen: true)
                        }
                        if !groups.confirm.isEmpty {
                            ChatToolGroupRow(title: "Confirm to run", tone: .warn, tools: groups.confirm)
                        }
                        if !groups.destructive.isEmpty {
                            ChatToolGroupRow(title: "Destructive", tone: .error, tools: groups.destructive)
                        }
                    }
                }
            }
            .dsList()
            .navigationTitle("Chat settings")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        dismiss()
                    } label: {
                        Image(systemName: Symbol.named("close"))
                    }
                    .accessibilityLabel("Close")
                }
            }
        }
        .presentationDetents([.medium, .large])
        .presentationDragIndicator(.visible)
        .presentationBackground(DS.Surface.page)
        .task { await model.loadModels() }
    }
}

/// One safety group of the catalog — `_ToolGroup`, as the web's `details`
/// row: the tier's dot, its name and count, opening onto the tool names.
private struct ChatToolGroupRow: View {
    let title: String
    let tone: DSStatusTone
    let tools: [String]

    @State private var open: Bool
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    init(title: String, tone: DSStatusTone, tools: [String], startsOpen: Bool = false) {
        self.title = title
        self.tone = tone
        self.tools = tools
        _open = State(initialValue: startsOpen)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Button {
                withAnimation(DSMotion.standard(reduceMotion: reduceMotion)) { open.toggle() }
            } label: {
                HStack(spacing: 8) {
                    DSStatusDot(tone)
                    Text(title)
                        .foregroundStyle(DS.Text.soft)
                    Text("\(tools.count)")
                        .foregroundStyle(DS.Text.muted)
                        .monospacedDigit()
                    Spacer(minLength: 8)
                    Image(systemName: "chevron.right")
                        .font(.system(size: 12, weight: .medium))
                        .foregroundStyle(DS.Text.faint)
                        .rotationEffect(.degrees(open ? 90 : 0))
                        .accessibilityHidden(true)
                }
                .font(DS.Font.body)
                .frame(maxWidth: .infinity, minHeight: 28, alignment: .leading)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityAddTraits(open ? .isSelected : [])
            if open {
                ChatFlowLayout(spacing: 6) {
                    ForEach(tools, id: \.self) { tool in
                        Text(verbatim: tool)
                            .font(DS.Font.monoSmall)
                            .foregroundStyle(DS.Text.label)
                            .padding(.horizontal, 8)
                            .padding(.vertical, 3)
                            .background(DS.Surface.raised, in: .rect(cornerRadius: DS.Radius.small, style: .continuous))
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
    }
}

/// A model row's press style: the chosen row is inert (disabled) but drawn
/// at full strength with its check, not dimmed like an unavailable one.
private struct ChatModelChoiceStyle: ButtonStyle {
    let selected: Bool

    @Environment(\.isEnabled) private var isEnabled

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .opacity(isEnabled || selected ? (configuration.isPressed ? 0.7 : 1) : 0.4)
    }
}
