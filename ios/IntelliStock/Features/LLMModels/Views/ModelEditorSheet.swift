import SwiftUI

/// Add or edit a model — `_AddEditSheet` in `models_screen.dart`: name, the
/// LLM config form, pricing overrides, status, the test-result panel and the
/// Cancel / Test only / Test & Save actions. Native form: a sheet with an
/// inset-grouped form; Cancel (Close once saved) leads the toolbar, Test & Save
/// trails it, and Test Only is a row.
struct ModelEditorSheet: View {
    let existing: LlmModel?
    /// Reloads the list after a save.
    let onSaved: () -> Void

    @Environment(AppServices.self) private var services
    @Environment(\.dismiss) private var dismiss
    @State private var editor: ModelEditorModel?
    @State private var pickers: LlmPickersModel?
    @State private var pricingExpanded = false

    var body: some View {
        NavigationStack {
            Group {
                if let editor, let pickers {
                    content(editor, pickers)
                } else {
                    Color.clear
                }
            }
            .navigationTitle(existing == nil ? "Add Model" : "Edit Model")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                if let editor {
                    toolbar(editor)
                }
            }
        }
        .presentationDragIndicator(.visible)
        .interactiveDismissDisabled(editor?.submitting ?? false)
        .onAppear {
            guard editor == nil else { return }
            let services = services
            editor = ModelEditorModel(existing: existing, repository: { services.modelRepository })
            pickers = LlmPickersModel(repository: { services.modelRepository })
        }
        // Dart's `onSaved` on close. However the sheet goes (Close, Cancel or
        // a swipe), a save refreshes the list.
        .onDisappear {
            if editor?.saved == true { onSaved() }
        }
    }

    private func close() {
        dismiss()
    }

    /// The old bottom bar and close X: Cancel (Close after a save) leading,
    /// the primary Test & Save trailing until the save lands. Cancel and the
    /// X both dismissed, held while a save runs.
    @ToolbarContentBuilder
    private func toolbar(_ editor: ModelEditorModel) -> some ToolbarContent {
        ToolbarItem(placement: .cancellationAction) {
            if editor.saved {
                Button("Close") { close() }
            } else {
                Button("Cancel") { dismiss() }
                    .disabled(editor.submitting)
            }
        }
        if !editor.saved {
            ToolbarItem(placement: .confirmationAction) {
                Button {
                    Task { await editor.testAndSave() }
                } label: {
                    if editor.submitting {
                        ProgressView()
                    } else {
                        Text(editor.primaryLabel)
                    }
                }
                .dsProminentButton()
                .disabled(editor.submitting)
                .accessibilityLabel(editor.primaryLabel)
            }
        }
    }

    private func content(_ editor: ModelEditorModel, _ pickers: LlmPickersModel) -> some View {
        @Bindable var editor = editor
        return Form {
            if let existing {
                ModelSavedSection(model: existing)
            }

            // The sheet's subtitle is this section's footer: as a nav
            // subtitle it truncated beside the toolbar buttons.
            Section {
                TextField("Name", text: $editor.name, prompt: Text("e.g. Gemini Flash — Main"))
                    .autocorrectionDisabled()
            } header: {
                Text("Name")
            } footer: {
                Text("Save a reusable LLM configuration.")
            }
            .disabled(editor.submitting)

            LlmConfigFormSections(draft: $editor.draft, pickers: pickers, disabled: editor.submitting)

            Section {
                DisclosureGroup("Pricing override (optional)", isExpanded: $pricingExpanded) {
                    Text("Leave blank to use backend llm_pricing.yaml defaults. Values are $/1M tokens.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    ModelPricingField(label: "Input cost ($/1M tokens)", text: $editor.inputCost)
                    ModelPricingField(label: "Output cost ($/1M tokens)", text: $editor.outputCost)
                    ModelPricingField(label: "Cache creation cost ($/1M tokens)", text: $editor.cacheCreationCost)
                    ModelPricingField(label: "Cache read cost ($/1M tokens)", text: $editor.cacheReadCost)
                }
            }
            .disabled(editor.submitting)

            if editor.isEdit {
                Section {
                    ModelInfoBox(text: "Leave API Key empty to keep the existing key unchanged.", color: DS.Palette.warning)
                }
            }

            // The old bottom bar's Test Only (not for Claude Code CLI), and
            // the run's status line.
            if (!editor.saved && editor.draft.provider != "claude-cli") || !editor.statusMsg.isEmpty {
                Section {
                    if !editor.saved, editor.draft.provider != "claude-cli" {
                        InlineActionRow(editor.submitting ? "Testing…" : "Test Only",
                                        systemImage: Symbol.named("network_check"),
                                        isBusy: editor.submitting) {
                            Task { await editor.testOnly() }
                        }
                    }
                    if !editor.statusMsg.isEmpty {
                        ModelInfoBox(text: editor.statusMsg, color: editor.statusOk ? DS.Palette.success : DS.Palette.danger)
                    }
                }
            }

            if let result = editor.testResult {
                Section("LLM connectivity test response") {
                    LlmTestResultPanel(result: result)
                }
            }
        }
        .llmPickerFetches(editor.draft, pickers)
    }
}

/// Edit mode: the saved model's details, which the list row no longer
/// shows — the old card's Model, Effort, Key/CLI (masked) and Created.
private struct ModelSavedSection: View {
    let model: LlmModel

    var body: some View {
        Section("Saved model") {
            LabeledContent("Model") {
                Text(verbatim: model.model).lineLimit(1).truncationMode(.middle)
            }
            LabeledContent("Effort", value: LlmModelCells.reasoning(model))
            LabeledContent("Key/CLI") {
                Text(verbatim: LlmModelCells.key(model)).lineLimit(1).truncationMode(.middle)
            }
            if let created = model.createdAt {
                LabeledContent("Created", value: fmtDate(parseDateTime(created)))
            }
        }
    }
}

private struct ModelPricingField: View {
    let label: String
    @Binding var text: String

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(label)
                .font(.footnote)
                .foregroundStyle(.secondary)
            TextField(label, text: $text, prompt: Text("e.g. 3.00"))
                .keyboardType(.decimalPad)
                .font(.system(.body, design: .monospaced))
        }
    }
}

/// A note — the screen's status and info lines: a coloured glyph and the
/// text, as a plain row (the redesign drops the tinted box).
struct ModelInfoBox: View {
    let text: String
    let color: Color

    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Image(systemName: Self.symbol(color))
                .foregroundStyle(color)
                .accessibilityHidden(true)
            Text(text)
                .font(.footnote)
                .foregroundStyle(color == DS.Palette.info ? AnyShapeStyle(.secondary) : AnyShapeStyle(DS.Palette.onTint(color, in: colorScheme)))
                .frame(maxWidth: .infinity, alignment: .leading)
        }
        .accessibilityElement(children: .combine)
    }

    static func symbol(_ color: Color) -> String {
        if color == DS.Palette.success { return "checkmark.circle.fill" }
        if color == DS.Palette.danger { return "exclamationmark.circle.fill" }
        if color == DS.Palette.warning { return "exclamationmark.triangle.fill" }
        return "info.circle"
    }
}

/// The `/llm/test` response — `_TestResultPanel`.
struct LlmTestResultPanel: View {
    let result: LlmTestResult

    @State private var reasoningOpen = false
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        let ink = Color.primary
        VStack(alignment: .leading, spacing: 8) {
            ChatFlowLayout(spacing: 12) {
                if let provider = result.provider { kv("provider", provider, ink) }
                if let model = result.model { kv("model", model, ink) }
                if let effective = result.effectiveModel, effective != result.model { kv("effective", effective, ink) }
                if let latency = result.latencyMs { kv("latency", "\(latency)ms", ink) }
            }
            if !result.result.isNull {
                Text("structured connectivity probe:").font(.caption2).foregroundStyle(ink.opacity(0.8))
                codeBlock(Self.pretty(result.result))
            }
            if result.smokePrompt != nil || result.smokeResponse != nil || result.smokeThinking != nil || result.smokeError != nil {
                HStack(spacing: 4) {
                    Text("real-generation smoke").foregroundStyle(ink.opacity(0.8))
                    if let ms = result.smokeLatencyMs { Text("(\(ms)ms)").foregroundStyle(.tertiary) }
                    if let chars = result.smokeContentChars { Text("· content \(chars) chars").foregroundStyle(.tertiary) }
                    if let chars = result.smokeThinkingChars { Text("· reasoning \(chars) chars").foregroundStyle(.tertiary) }
                }
                .font(.caption2)
                if let prompt = result.smokePrompt {
                    Text("prompt: \(prompt)").font(.caption2).foregroundStyle(.secondary)
                }
                if let response = result.smokeResponse, (result.smokeContentChars ?? 1) > 0 {
                    Text("content:").font(.caption2).foregroundStyle(.secondary)
                    codeBlock(response)
                }
                if let thinking = result.smokeThinking {
                    DisclosureGroup(isExpanded: $reasoningOpen) {
                        codeBlock(thinking)
                    } label: {
                        Text("reasoning (\(result.smokeThinkingChars ?? thinking.count) chars)")
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    }
                }
                if let error = result.smokeError {
                    Text("smoke generation failed: \(error)")
                        .font(.system(.caption2, design: .monospaced))
                        .foregroundStyle(DS.Palette.onTint(DS.Palette.danger, in: colorScheme))
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                if result.smokeResponse == nil, result.smokeThinking == nil, result.smokeError == nil {
                    Text("smoke generation returned empty — structured check passed but the model did not produce free-form text.")
                        .font(.caption2)
                        .foregroundStyle(DS.Palette.onTint(DS.Palette.warning, in: colorScheme))
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
            if !result.providerMeta.isNull {
                Text("provider meta:").font(.caption2).foregroundStyle(ink.opacity(0.8))
                codeBlock(Self.pretty(result.providerMeta))
            }
        }
        .padding(.vertical, 4)
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func kv(_ label: String, _ value: String, _ ink: Color) -> some View {
        HStack(spacing: 2) {
            Text("\(label):").font(.caption).foregroundStyle(.secondary)
            Text(verbatim: value).font(.system(.caption, design: .monospaced)).foregroundStyle(ink)
        }
    }

    private func codeBlock(_ text: String) -> some View {
        ScrollView(.horizontal, showsIndicators: false) {
            Text(verbatim: text)
                .font(.system(.caption2, design: .monospaced))
                .textSelection(.enabled)
                .padding(8)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(DS.Surface.inset, in: .rect(cornerRadius: 4))
    }

    /// `JsonEncoder.withIndent('  ')`, falling back to `toString()`.
    static func pretty(_ value: JSON) -> String {
        if value.isNull { return "" }
        return (try? value.dartEncoded(indent: "  ")) ?? value.dartDescription
    }
}
