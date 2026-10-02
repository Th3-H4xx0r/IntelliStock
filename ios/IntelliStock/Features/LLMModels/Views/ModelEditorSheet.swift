import SwiftUI

/// Add or edit a model — `_AddEditSheet` in `models_screen.dart`: name, the
/// LLM config form, pricing overrides, status, the test-result panel and the
/// Cancel / Test only / Test & Save actions. Native form: a sheet with an
/// inset-grouped form and a bottom action bar.
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
            .navigationSubtitle("Save a reusable LLM configuration.")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        close()
                    } label: {
                        Image(systemName: Symbol.named("close"))
                    }
                    .disabled(editor?.submitting == true && editor?.saved != true)
                    .accessibilityLabel("Close")
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
    }

    /// After a save the close button refreshes the list (Dart's `onSaved`).
    private func close() {
        if editor?.saved == true { onSaved() }
        dismiss()
    }

    private func content(_ editor: ModelEditorModel, _ pickers: LlmPickersModel) -> some View {
        @Bindable var editor = editor
        return Form {
            Section("Name") {
                TextField("Name", text: $editor.name, prompt: Text("e.g. Gemini Flash — Main"))
                    .autocorrectionDisabled()
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

            if !editor.statusMsg.isEmpty || editor.testResult != nil || editor.isEdit {
                Section {
                    if !editor.statusMsg.isEmpty {
                        ModelInfoBox(text: editor.statusMsg, color: editor.statusOk ? DS.Palette.success : DS.Palette.danger)
                    }
                    if let result = editor.testResult {
                        LlmTestResultPanel(result: result)
                    }
                    if editor.isEdit {
                        ModelInfoBox(text: "Leave API Key empty to keep the existing key unchanged.", color: DS.Palette.warning)
                    }
                }
                .listRowBackground(Color.clear)
                .listRowInsets(EdgeInsets(top: 4, leading: 0, bottom: 4, trailing: 0))
            }
        }
        .safeAreaInset(edge: .bottom) { actions(editor) }
    }

    private func actions(_ editor: ModelEditorModel) -> some View {
        HStack(spacing: 10) {
            if editor.saved {
                Button {
                    close()
                } label: {
                    Text("Close").frame(maxWidth: .infinity)
                }
                .buttonStyle(.bordered)
                .tint(DS.Palette.success)
            } else {
                Button {
                    dismiss()
                } label: {
                    Text("Cancel").frame(maxWidth: .infinity)
                }
                .buttonStyle(.bordered)
                .tint(.secondary)
                .disabled(editor.submitting)

                if editor.draft.provider != "claude-cli" {
                    Button {
                        Task { await editor.testOnly() }
                    } label: {
                        Text(editor.submitting ? "Testing…" : "Test Only").frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.bordered)
                    .disabled(editor.submitting)
                }

                Button {
                    Task { await editor.testAndSave() }
                } label: {
                    HStack(spacing: 6) {
                        if editor.submitting { ProgressView().tint(DS.Palette.onAccent) }
                        Text(editor.primaryLabel).lineLimit(1).minimumScaleFactor(0.8)
                    }
                    .frame(maxWidth: .infinity)
                }
                .dsProminentButton()
                .disabled(editor.submitting)
            }
        }
        .controlSize(.large)
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
        .background(.bar)
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

/// A tinted note — the screen's status and info containers.
struct ModelInfoBox: View {
    let text: String
    let color: Color

    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        Text(text)
            .font(.footnote)
            .foregroundStyle(DS.Palette.onTint(color, in: colorScheme))
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(12)
            .background(color.opacity(DS.tintFill), in: .rect(cornerRadius: DS.Radius.small, style: .continuous))
    }
}

/// The `/llm/test` response — `_TestResultPanel`.
struct LlmTestResultPanel: View {
    let result: LlmTestResult

    @State private var reasoningOpen = false
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        let ink = DS.Palette.onTint(DS.Palette.success, in: colorScheme)
        VStack(alignment: .leading, spacing: 8) {
            Text("LLM connectivity test response")
                .font(.footnote.weight(.semibold))
                .foregroundStyle(ink)
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
                        .padding(8)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(DS.Palette.danger.opacity(DS.tintFill), in: .rect(cornerRadius: 4))
                }
                if result.smokeResponse == nil, result.smokeThinking == nil, result.smokeError == nil {
                    Text("smoke generation returned empty — structured check passed but the model did not produce free-form text.")
                        .font(.caption2)
                        .foregroundStyle(DS.Palette.onTint(DS.Palette.warning, in: colorScheme))
                        .padding(8)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(DS.Palette.warning.opacity(DS.tintFill), in: .rect(cornerRadius: 4))
                }
            }
            if !result.providerMeta.isNull {
                Text("provider meta:").font(.caption2).foregroundStyle(ink.opacity(0.8))
                codeBlock(Self.pretty(result.providerMeta))
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(DS.Palette.success.opacity(DS.tintFill), in: .rect(cornerRadius: DS.Radius.small, style: .continuous))
    }

    private func kv(_ label: String, _ value: String, _ ink: Color) -> some View {
        HStack(spacing: 2) {
            Text("\(label):").font(.caption2).foregroundStyle(ink.opacity(0.8))
            Text(verbatim: value).font(.system(.caption2, design: .monospaced)).foregroundStyle(ink)
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
