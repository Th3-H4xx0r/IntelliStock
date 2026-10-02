import SwiftUI

/// The saved LLM models — `ModelsScreen` in `models_screen.dart`, as an
/// inset-grouped list with one section per model.
struct ModelsView: View {
    @Environment(AppServices.self) private var services
    @State private var model: ModelsModel?
    @State private var editor: ModelEditorTarget?
    @State private var confirm: ConfirmRequest?
    @State private var deleting = false
    @State private var toast: Toast?

    var body: some View {
        List {
            Section {
                SectionHeader(
                    title: "LLM Models",
                    eyebrow: "Models",
                    subtitle: "Centralized LLM model configurations. Strategies can reference these instead of storing credentials inline."
                ) {
                    Button {
                        editor = ModelEditorTarget(model: nil)
                    } label: {
                        Label("Add Model", systemImage: Symbol.named("add"))
                            .font(.subheadline.weight(.semibold))
                    }
                    .dsProminentButton()
                    .buttonBorderShape(.capsule)
                }
                .listRowBackground(Color.clear)
                .listRowInsets(EdgeInsets(top: 12, leading: 4, bottom: 8, trailing: 4))
            }

            switch model?.models ?? .loading {
            case .loading:
                ForEach(0..<4, id: \.self) { _ in
                    ModelCardSection(model: Self.placeholder, test: nil, onTest: {}, onEdit: {}, onDelete: {})
                        .redacted(reason: .placeholder)
                        .allowsHitTesting(false)
                }
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
                    ForEach(models) { m in
                        ModelCardSection(
                            model: m,
                            test: model?.cliTests[m.id],
                            onTest: { Task { await model?.testCli(m.id) } },
                            onEdit: { editor = ModelEditorTarget(model: m) },
                            onDelete: { confirmDelete(m) }
                        )
                    }
                }
            }
        }
        .listStyle(.insetGrouped)
        .navigationTitle("Models")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button {
                    Task { await model?.refresh() }
                } label: {
                    Image(systemName: Symbol.named("refresh"))
                }
                .accessibilityLabel("Refresh")
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

/// One saved model — `_ModelCard`, as a list section.
private struct ModelCardSection: View {
    let model: LlmModel
    let test: ModelsModel.CliTest?
    let onTest: () -> Void
    let onEdit: () -> Void
    let onDelete: () -> Void

    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        let m = model
        Section {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 4) {
                    Text(m.name).font(.headline)
                    Text(LlmOptions.providerLabel(m.provider))
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                HStack(spacing: 4) {
                    if m.provider == "claude-cli" {
                        Button(action: onTest) {
                            Group {
                                if test?.testing == true {
                                    ProgressView()
                                } else {
                                    Image(systemName: Symbol.named("cable"))
                                }
                            }
                            .frame(width: 44, height: 44)
                            .contentShape(Rectangle())
                        }
                        .disabled(test?.testing == true)
                        .accessibilityLabel("Test CLI connection")
                    }
                    Button(action: onEdit) {
                        Image(systemName: Symbol.named("edit")).frame(width: 44, height: 44).contentShape(Rectangle())
                    }
                    .accessibilityLabel("Edit")
                    Button(role: .destructive, action: onDelete) {
                        Image(systemName: Symbol.named("delete_outline")).frame(width: 44, height: 44).contentShape(Rectangle())
                    }
                    .accessibilityLabel("Delete")
                }
                .buttonStyle(.borderless)
            }

            VStack(alignment: .leading, spacing: 4) {
                ModelChip(label: "Model", value: m.model, mono: true)
                ModelChip(label: "Effort", value: LlmModelCells.reasoning(m))
                ModelChip(label: "Key/CLI", value: LlmModelCells.key(m), mono: true)
                if let created = m.createdAt {
                    ModelChip(label: "Created", value: fmtDate(parseDateTime(created)))
                }
            }

            if let message = test?.message {
                let color = test?.ok == true ? DS.Palette.success : DS.Palette.danger
                Text(message)
                    .font(.caption)
                    .foregroundStyle(DS.Palette.onTint(color, in: colorScheme))
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 10)
                    .padding(.vertical, 6)
                    .background(color.opacity(DS.tintFill), in: .rect(cornerRadius: 6, style: .continuous))
            }
        }
    }
}

/// A `label: value` line — `_chip`.
private struct ModelChip: View {
    let label: String
    let value: String
    var mono = false

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 4) {
            Text("\(label):")
                .foregroundStyle(.tertiary)
            Text(verbatim: value)
                .font(mono ? .system(.caption, design: .monospaced) : .caption)
                .foregroundStyle(.secondary)
                .lineLimit(1)
                .truncationMode(.middle)
        }
        .font(.caption)
        .accessibilityElement(children: .combine)
    }
}
