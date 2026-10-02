import SwiftUI

/// The saved LLM models — `ModelsScreen` in `models_screen.dart`, as an
/// inset-grouped list of rows. Tapping a row opens its editor; Edit, Delete
/// (and Test CLI Connection for Claude Code CLI models) are swipe actions and
/// context-menu items; `+` adds a model.
struct ModelsView: View {
    @Environment(AppServices.self) private var services
    @State private var model: ModelsModel?
    @State private var editor: ModelEditorTarget?
    @State private var confirm: ConfirmRequest?
    @State private var deleting = false
    @State private var toast: Toast?

    var body: some View {
        List {
            switch model?.models ?? .loading {
            case .loading:
                Section {
                    ForEach(0..<4, id: \.self) { _ in
                        ModelListRow(model: Self.placeholder, test: nil)
                    }
                }
                .redacted(reason: .placeholder)
                .allowsHitTesting(false)
            case .failed(let error):
                Section {
                    ErrorRow(message: llmErrorText(error)) { Task { await model?.refresh() } }
                        .listRowInsets(EdgeInsets())
                        .listRowBackground(Color.clear)
                }
            case .loaded(let models):
                if models.isEmpty {
                    Section {
                        EmptyState(
                            systemImage: Symbol.named("psychology"),
                            title: "No models saved yet",
                            subtitle: "Add a model to start using centralized LLM configurations.",
                            actionLabel: "Add Model",
                            onAction: { editor = ModelEditorTarget(model: nil) }
                        )
                        .listRowBackground(Color.clear)
                    }
                } else {
                    Section {
                        ForEach(models) { m in
                            modelRow(m)
                        }
                    } footer: {
                        Text("Centralized LLM model configurations. Strategies can reference these instead of storing credentials inline.")
                    }
                }
            }
        }
        .listStyle(.insetGrouped)
        .navigationTitle("Models")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .primaryAction) {
                ToolbarAddButton("Add Model") {
                    editor = ModelEditorTarget(model: nil)
                }
            }
        }
        .refreshable { await model?.refresh() }
        .sheet(item: $editor) { target in
            ModelEditorSheet(existing: target.model) {
                Task { await model?.refresh() }
            }
        }
        .confirmAlert($confirm, isRunning: $deleting)
        .toast($toast)
        .task {
            if model == nil {
                let services = services
                model = ModelsModel(repository: { services.modelRepository })
            }
            if let model, model.models.needsLoad { await model.load() }
        }
    }

    /// One model: the row opens the editor; the old card's buttons are on
    /// the swipe and the context menu. Delete keeps its confirmation.
    private func modelRow(_ m: LlmModel) -> some View {
        let test = model?.cliTests[m.id]
        let isCli = m.provider == "claude-cli"
        let testing = test?.testing == true
        return Button {
            editor = ModelEditorTarget(model: m)
        } label: {
            ModelListRow(model: m, test: test)
        }
        .foregroundStyle(.primary)
        .swipeActions(edge: .trailing, allowsFullSwipe: false) {
            // Red by tint, not by role: a destructive-role swipe button
            // animates the row away before the confirmation answers.
            Button("Delete", systemImage: Symbol.named("delete_outline")) { confirmDelete(m) }
                .tint(DS.Palette.danger)
            Button("Edit", systemImage: Symbol.named("edit")) { editor = ModelEditorTarget(model: m) }
                .tint(DS.Palette.accent)
        }
        .swipeActions(edge: .leading) {
            if isCli {
                Button("Test CLI Connection", systemImage: Symbol.named("cable")) {
                    Task { await model?.testCli(m.id) }
                }
                .tint(DS.Palette.info)
                .disabled(testing)
            }
        }
        .contextMenu {
            Button("Edit", systemImage: Symbol.named("edit")) { editor = ModelEditorTarget(model: m) }
            if isCli {
                Button("Test CLI Connection", systemImage: Symbol.named("cable")) {
                    Task { await model?.testCli(m.id) }
                }
                .disabled(testing)
            }
            Divider()
            Button("Delete", systemImage: Symbol.named("delete_outline"), role: .destructive) { confirmDelete(m) }
        }
    }

    private func confirmDelete(_ m: LlmModel) {
        confirm = ConfirmRequest(
            title: "Delete \"\(m.name)\"?",
            body: "Strategies referencing this model will revert to inline credentials.",
            confirmLabel: "Delete",
            role: .destructive,
            onConfirm: { try await model?.delete(m.id) },
            onError: { error in toast = Toast("Delete failed: \(llmErrorText(error))", style: .error) }
        )
    }

    private static let placeholder = LlmModel(json: [
        "id": "placeholder", "name": "Gemini Flash — Main", "provider": "gemini", "model": "gemini-flash",
        "api_key": "••••", "created_at": "2026-01-01T00:00:00Z",
    ])
}

private struct ModelEditorTarget: Identifiable {
    let id = UUID()
    let model: LlmModel?
}

/// The provider's glyph in a model row.
nonisolated enum ModelsProviderGlyph {
    static func symbol(_ provider: String) -> String {
        switch provider {
        case "gemini": "sparkle"
        case "deepseek": "water.waves"
        case "openai": "circle.hexagongrid"
        case "azure": "cloud"
        case "nvidia": "cpu"
        case "ollama": "desktopcomputer"
        case "bedrock": "shippingbox"
        case "openrouter": "arrow.triangle.branch"
        case "claude-cli", "codex-cli": "terminal"
        default: Symbol.named("psychology")
        }
    }

    /// "Azure OpenAI · Created Apr 12, 2026" — the provider, then the old
    /// card's Created line.
    static func subtitle(_ m: LlmModel) -> String {
        let provider = LlmOptions.providerLabel(m.provider)
        guard let created = m.createdAt else { return provider }
        return "\(provider) · Created \(fmtDate(parseDateTime(created)))"
    }
}

/// One saved model — `_ModelCard`, as a row in the `EntityRow` style: the
/// provider glyph, the name (up to two lines, so the effort suffix shows),
/// and the provider with its date. The model id, effort and masked key are in
/// the editor. A CLI test's spinner and result show on the row.
private struct ModelListRow: View {
    let model: LlmModel
    let test: ModelsModel.CliTest?

    @Environment(\.colorScheme) private var colorScheme
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

    var body: some View {
        let m = model
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 12) {
                IconTile(systemImage: ModelsProviderGlyph.symbol(m.provider), size: EntityRowMetrics.iconSize)
                VStack(alignment: .leading, spacing: 2) {
                    Text(m.name)
                        .font(.headline)
                        .lineLimit(dynamicTypeSize.isAccessibilitySize ? nil : 2)
                    Text(ModelsProviderGlyph.subtitle(m))
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                        .lineLimit(dynamicTypeSize.isAccessibilitySize ? nil : 1)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                if test?.testing == true {
                    ProgressView()
                        .accessibilityLabel("Testing CLI connection")
                }
            }
            if let message = test?.message {
                let color = test?.ok == true ? DS.Palette.success : DS.Palette.danger
                Label {
                    Text(message)
                        .foregroundStyle(DS.Palette.onTint(color, in: colorScheme))
                } icon: {
                    Image(systemName: test?.ok == true ? "checkmark.circle.fill" : "exclamationmark.circle.fill")
                        .foregroundStyle(color)
                }
                .font(.footnote)
                .padding(.leading, EntityRowMetrics.iconSize + 12)
            }
        }
        .padding(.vertical, 2)
        .accessibilityElement(children: .combine)
    }
}
