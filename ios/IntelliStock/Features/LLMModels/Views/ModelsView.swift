import SwiftUI

/// The saved LLM models — `ModelsScreen` in `models_screen.dart`, as the
/// web's Models page: the subtitle, then one card of model rows. Tapping a
/// row opens its editor; Edit, Delete (and Test CLI connection for Claude
/// Code CLI models) are swipe actions and context-menu items; `+` adds a
/// model.
struct ModelsView: View {
    @Environment(AppServices.self) private var services
    @State private var model: ModelsModel?
    @State private var editor: ModelEditorTarget?
    @State private var confirm: ConfirmRequest?
    @State private var deleting = false
    @State private var toast: Toast?

    static let subtitle = "Saved LLM configurations. Strategies reference these instead of storing credentials inline"

    var body: some View {
        List {
            Section {
                Text(Self.subtitle)
                    .font(DS.Font.pageSub)
                    .foregroundStyle(DS.Text.muted)
                    .dsClearRow(top: 0)
            }
            switch model?.models ?? .loading {
            case .loading:
                DSSection {
                    ForEach(0..<4, id: \.self) { _ in
                        VStack(alignment: .leading, spacing: 8) {
                            Skeleton(width: 170, height: 14)
                            Skeleton(width: 120, height: 12)
                        }
                    }
                }
                .allowsHitTesting(false)
                .accessibilityLabel("Loading models")
            case .failed(let error):
                Section {
                    ErrorRow(message: llmErrorText(error)) { Task { await model?.refresh() } }
                        .dsClearRow()
                }
            case .loaded(let models):
                if models.isEmpty {
                    DSSection {
                        EmptyState(
                            title: "No models saved yet",
                            subtitle: "Add one to share an LLM configuration across strategies",
                            actionLabel: "Add model",
                            onAction: { editor = ModelEditorTarget(model: nil) }
                        )
                    }
                } else {
                    DSSection("", subtitle: "\(models.count) model\(models.count == 1 ? "" : "s")") {
                        ForEach(models) { m in
                            modelRow(m)
                        }
                    }
                }
            }
        }
        .dsList()
        .navigationTitle("Models")
        .navigationBarTitleDisplayMode(.large)
        .toolbar {
            ToolbarItem(placement: .primaryAction) {
                ToolbarAddButton("Add model") {
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
        .buttonStyle(.plain)
        .swipeActions(edge: .trailing, allowsFullSwipe: false) {
            // Red by tint, not by role: a destructive-role swipe button
            // animates the row away before the confirmation answers.
            Button("Delete", systemImage: Symbol.named("delete_outline")) { confirmDelete(m) }
                .tint(DS.Swipe.destructive)
            Button("Edit", systemImage: Symbol.named("edit")) { editor = ModelEditorTarget(model: m) }
                .tint(DS.Swipe.neutral)
        }
        .swipeActions(edge: .leading) {
            if isCli {
                Button("Test CLI connection", systemImage: Symbol.named("cable")) {
                    Task { await model?.testCli(m.id) }
                }
                .tint(DS.Swipe.neutral)
                .disabled(testing)
            }
        }
        .contextMenu {
            Button("Edit", systemImage: Symbol.named("edit")) { editor = ModelEditorTarget(model: m) }
            if isCli {
                Button("Test CLI connection", systemImage: Symbol.named("cable")) {
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
}

private struct ModelEditorTarget: Identifiable {
    let id = UUID()
    let model: LlmModel?
}

/// The provider's glyph (kept for other surfaces; the list rows draw none)
/// and a model row's subtitle.
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

/// One saved model — the web's model row: the name (up to two lines, so the
/// effort suffix shows) over the provider and its date. The model id,
/// effort and masked key are in the editor. A CLI test's spinner shows on
/// the row, and its result as a 12 pt line under it, gain or loss.
private struct ModelListRow: View {
    let model: LlmModel
    let test: ModelsModel.CliTest?

    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

    var body: some View {
        let m = model
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 12) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(m.name)
                        .font(DS.Font.body)
                        .foregroundStyle(DS.Text.primary)
                        .lineLimit(dynamicTypeSize.isAccessibilitySize ? nil : 2)
                    Text(ModelsProviderGlyph.subtitle(m))
                        .font(DS.Font.caption)
                        .foregroundStyle(DS.Text.muted)
                        .monospacedDigit()
                        .lineLimit(dynamicTypeSize.isAccessibilitySize ? nil : 1)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                if test?.testing == true {
                    ProgressView()
                        .controlSize(.small)
                        .accessibilityLabel("Testing CLI connection")
                }
            }
            if let message = test?.message {
                Text(message)
                    .font(DS.Font.caption)
                    .foregroundStyle(test?.ok == true ? DS.Palette.gain : DS.Palette.loss)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
    }
}
