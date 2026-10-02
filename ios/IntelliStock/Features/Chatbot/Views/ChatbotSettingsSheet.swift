import SwiftUI

/// Chat settings — `ChatSettingsSheet` in `chat_settings_sheet.dart`: the
/// model, auto-run safe tools, conversation actions and the tool catalog.
///
/// Native form: a sheet with an inset-grouped form (SwiftUI has no Navigator
/// problem, so the in-tree scrim and panel are gone). Clear and Delete hand
/// their confirmation back to the chat panel, which shows it once this closes.
struct ChatSettingsSheet: View {
    let model: ChatbotModel
    /// Asks the panel to confirm (this sheet closes first).
    let onConfirm: (ConfirmRequest) -> Void

    @Environment(\.dismiss) private var dismiss
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        let st = model.state
        let convo = st.activeConversation
        NavigationStack {
            Form {
                Section("MODEL") {
                    if !st.modelsLoaded, st.models.isEmpty {
                        LoadingState(label: "Loading models…")
                    } else if st.modelsLoaded, st.models.isEmpty {
                        Text("No models configured yet. Add one on the Models page.")
                            .font(.subheadline)
                            .foregroundStyle(DS.Palette.onTint(DS.Palette.warning, in: colorScheme))
                    } else {
                        ForEach(st.models) { m in
                            let selected = convo?.modelId == m.id
                            Button {
                                Task { await model.setModel(m.id) }
                            } label: {
                                HStack {
                                    VStack(alignment: .leading, spacing: 2) {
                                        Text(m.name)
                                            .foregroundStyle(selected ? AnyShapeStyle(.tint) : AnyShapeStyle(.primary))
                                        let sub = ChatBlockFormat.modelSubtitle(provider: m.provider, model: m.model)
                                        if !sub.isEmpty {
                                            Text(sub)
                                                .font(.caption)
                                                .foregroundStyle(.secondary)
                                        }
                                    }
                                    Spacer()
                                    if selected {
                                        Image(systemName: Symbol.named("check_circle"))
                                            .foregroundStyle(.tint)
                                    }
                                }
                            }
                            .disabled(selected || st.busy)
                            .accessibilityAddTraits(selected ? .isSelected : [])
                        }
                    }
                }

                Section {
                    Toggle(isOn: Binding(
                        get: { convo?.autoConfirmSafeTools ?? false },
                        set: { value in Task { await model.setAutoConfirmSafe(value) } }
                    )) {
                        Text("Auto-run safe (read-only) tools")
                    }
                    .disabled(convo == nil)
                } header: {
                    Text("TOOLS")
                } footer: {
                    Text("When on, the assistant can call read-only tools like list_instances without asking. Mutating tools always require approval.")
                }

                Section("CONVERSATION") {
                    Button {
                        Task { await model.startNewConversationFromUI() }
                        dismiss()
                    } label: {
                        Label("New", systemImage: Symbol.named("add"))
                    }
                    Button {
                        onConfirm(ConfirmRequest(
                            title: "Clear conversation",
                            body: "This deletes all messages in this conversation. This cannot be undone.",
                            confirmLabel: "Clear",
                            role: nil,
                            onConfirm: { await model.clearConversation() },
                            onError: { _ in }
                        ))
                    } label: {
                        Label("Clear", systemImage: Symbol.named("delete_sweep"))
                            .foregroundStyle(convo == nil ? AnyShapeStyle(.tertiary) : AnyShapeStyle(DS.Palette.warning))
                    }
                    .disabled(convo == nil || st.busy)
                    Button(role: .destructive) {
                        onConfirm(ConfirmRequest(
                            title: "Delete conversation",
                            body: "This permanently deletes this conversation. This cannot be undone.",
                            confirmLabel: "Delete",
                            role: .destructive,
                            onConfirm: { await model.deleteConversation() },
                            onError: { _ in }
                        ))
                    } label: {
                        Label("Delete", systemImage: Symbol.named("delete_outline"))
                    }
                    .disabled(convo == nil || st.busy)
                }

                if !st.toolCatalog.isEmpty {
                    let groups = ChatToolGroups(st.toolCatalog)
                    Section("TOOLS THE ASSISTANT CAN USE") {
                        if !groups.safe.isEmpty {
                            ChatToolGroupRow(title: "Read-only · auto-runnable", color: DS.Palette.success, tools: groups.safe)
                        }
                        if !groups.confirm.isEmpty {
                            ChatToolGroupRow(title: "Confirm to run", color: DS.Palette.warning, tools: groups.confirm)
                        }
                        if !groups.destructive.isEmpty {
                            ChatToolGroupRow(title: "Destructive · always confirm", color: DS.Palette.danger, tools: groups.destructive)
                        }
                    }
                }
            }
            .navigationTitle("Chat Settings")
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
        .task { await model.loadModels() }
    }
}

/// One safety group of the catalog — `_ToolGroup`, as a disclosure group.
private struct ChatToolGroupRow: View {
    let title: String
    let color: Color
    let tools: [String]

    var body: some View {
        DisclosureGroup {
            ChatFlowLayout(spacing: 6) {
                ForEach(tools, id: \.self) { tool in
                    Text(verbatim: tool)
                        .font(.system(.caption2, design: .monospaced))
                        .foregroundStyle(.secondary)
                        .padding(.horizontal, 8)
                        .padding(.vertical, 3)
                        .background(DS.Surface.inset, in: .rect(cornerRadius: 6, style: .continuous))
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        } label: {
            HStack(spacing: 10) {
                Circle().fill(color).frame(width: 8, height: 8)
                Text("\(title) (\(tools.count))")
                    .font(.subheadline)
            }
        }
    }
}
