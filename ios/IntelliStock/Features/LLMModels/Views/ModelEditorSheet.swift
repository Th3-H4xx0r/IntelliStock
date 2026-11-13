import SwiftUI

/// Add or edit a model — `_AddEditSheet` in `models_screen.dart`, shaped like
/// the web's modal: the name, the LLM config form, the pricing override, the
/// run's status and the test response, then Cancel (Close once saved), Test
/// only and Test & save in the footer.
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
        Group {
            if let editor, let pickers {
                content(editor, pickers)
            } else {
                DS.Surface.card
            }
        }
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

    private func content(_ editor: ModelEditorModel, _ pickers: LlmPickersModel) -> some View {
        @Bindable var editor = editor
        return FormSheet(
            existing == nil ? "Add model" : "Edit model",
            subtitle: "A reusable LLM configuration that strategies can reference",
            closeDisabled: editor.submitting && !editor.saved,
            onClose: { dismiss() }
        ) {
            if let existing {
                ModelSavedInfo(model: existing)
            }

            DSField("Name", help: "Shown in strategy dropdowns") {
                FormTextField("e.g. Gemini Flash — Main", text: $editor.name)
            }
            .disabled(editor.submitting)

            LlmConfigFormSections(draft: $editor.draft, pickers: pickers, disabled: editor.submitting)

            pricing(editor)

            if editor.isEdit {
                FormNote("Leave the API key empty to keep the existing key unchanged.", tone: .info)
            }

            if !editor.statusMsg.isEmpty {
                ModelInfoBox(text: editor.statusMsg, color: editor.statusOk ? DS.Palette.success : DS.Palette.danger)
            }

            if let result = editor.testResult {
                VStack(alignment: .leading, spacing: 8) {
                    Text("LLM connectivity test response")
                        .font(DS.Font.body)
                        .foregroundStyle(DS.Text.label)
                    LlmTestResultPanel(result: result)
                }
            }
        } footer: {
            footer(editor)
        }
        .llmPickerFetches(editor.draft, pickers)
    }

    /// Cancel (Close after a save), Test only (not for Claude Code CLI) and
    /// the primary Test & save until the save lands. Cancel and the X both
    /// dismiss, held while a save runs.
    @ViewBuilder
    private func footer(_ editor: ModelEditorModel) -> some View {
        if editor.saved {
            Button("Close") { dismiss() }
                .buttonStyle(.dsSecondary(size: .medium))
        } else {
            Button("Cancel") { dismiss() }
                .buttonStyle(.dsGhost(size: .medium))
                .disabled(editor.submitting)
            if editor.draft.provider != "claude-cli" {
                Button(editor.submitting ? "Testing…" : "Test only") {
                    Task { await editor.testOnly() }
                }
                .buttonStyle(.dsSecondary(size: .medium))
                .disabled(editor.submitting)
            }
            Button {
                Task { await editor.testAndSave() }
            } label: {
                HStack(spacing: 6) {
                    if editor.submitting { ProgressView().controlSize(.mini).tint(DS.Palette.onAccent) }
                    Text(editor.primaryLabel)
                }
            }
            .buttonStyle(.dsPrimary)
            .disabled(editor.submitting)
            .accessibilityLabel(editor.primaryLabel)
        }
    }

    /// Pricing override (optional): a disclosure row, then the help and the
    /// four $/1M-token fields two by two.
    private func pricing(_ editor: ModelEditorModel) -> some View {
        @Bindable var editor = editor
        return VStack(alignment: .leading, spacing: 12) {
            Button {
                withAnimation(DSMotion.standard) { pricingExpanded.toggle() }
            } label: {
                HStack(spacing: 6) {
                    Text("Pricing override")
                        .foregroundStyle(DS.Text.label)
                    Text("(optional)")
                        .foregroundStyle(DS.Text.muted)
                    Spacer(minLength: 8)
                    Image(systemName: "chevron.down")
                        .font(.system(size: 13))
                        .foregroundStyle(DS.Text.muted)
                        .rotationEffect(.degrees(pricingExpanded ? 180 : 0))
                }
                .font(DS.Font.body)
                .frame(minHeight: 32)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityValue(pricingExpanded ? "Expanded" : "Collapsed")

            if pricingExpanded {
                Text("Leave blank to use the backend's llm_pricing.yaml defaults. Values are $/1M tokens.")
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Text.muted)
                    .fixedSize(horizontal: false, vertical: true)
                FormGrid {
                    DSField("Input cost") { FormTextField("e.g. 3.00", text: $editor.inputCost, mono: true, keyboard: .decimalPad) }
                    DSField("Output cost") { FormTextField("e.g. 15.00", text: $editor.outputCost, mono: true, keyboard: .decimalPad) }
                    DSField("Cache creation") { FormTextField("e.g. 3.75", text: $editor.cacheCreationCost, mono: true, keyboard: .decimalPad) }
                    DSField("Cache read") { FormTextField("e.g. 0.30", text: $editor.cacheReadCost, mono: true, keyboard: .decimalPad) }
                }
            }
        }
        .disabled(editor.submitting)
    }
}

/// Edit mode: the saved model's details, which the list row no longer
/// shows — the old card's Model, Effort, Key/CLI (masked) and Created.
private struct ModelSavedInfo: View {
    let model: LlmModel

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            row("Model", model.model)
            DSKeyValueRow("Effort", LlmModelCells.reasoning(model))
            row("Key or CLI", LlmModelCells.key(model))
            if let created = model.createdAt {
                DSKeyValueRow("Created", fmtDate(parseDateTime(created)))
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 12)
        .overlay {
            RoundedRectangle(cornerRadius: DS.Radius.control, style: .continuous)
                .strokeBorder(DS.Line.card, lineWidth: DS.Line.width)
        }
    }

    /// A key-value row whose value is an id: verbatim, one line, cut in the
    /// middle.
    private func row(_ key: String, _ value: String) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 16) {
            Text(key)
                .foregroundStyle(DS.Text.secondary)
            Spacer(minLength: 8)
            Text(verbatim: value)
                .foregroundStyle(DS.Text.primary)
                .lineLimit(1)
                .truncationMode(.middle)
        }
        .font(DS.Font.body)
        .accessibilityElement(children: .combine)
    }
}

/// A status or info line — the screen's notes, in the web's two shapes: a
/// save or test result is a `.msg` box (gain or loss), a warning is an amber
/// dot and zinc-300 text, information is quiet text.
struct ModelInfoBox: View {
    let text: String
    let color: Color

    var body: some View {
        if color == DS.Palette.success {
            FormMessage(text, tone: .ok)
        } else if color == DS.Palette.danger {
            FormMessage(text, tone: .error)
        } else if color == DS.Palette.warning {
            FormNote(text)
        } else {
            FormNote(text, tone: .info)
        }
    }
}

/// The `/llm/test` response — `_TestResultPanel`: the provider, model and
/// latency, the parsed probe, the free-form smoke generation and the provider
/// meta, in 12 pt type on a bordered panel; code in monospace on the page
/// colour.
struct LlmTestResultPanel: View {
    let result: LlmTestResult

    @State private var reasoningOpen = false

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            ChatFlowLayout(spacing: 16, runSpacing: 6) {
                if let provider = result.provider { kv("Provider", provider) }
                if let model = result.model { kv("Model", model) }
                if let effective = result.effectiveModel, effective != result.model { kv("Effective", effective) }
                if let latency = result.latencyMs { kv("Latency", "\(latency)ms") }
            }
            if !result.result.isNull {
                caption("Structured connectivity probe")
                codeBlock(Self.pretty(result.result))
            }
            if result.smokePrompt != nil || result.smokeResponse != nil || result.smokeThinking != nil || result.smokeError != nil {
                HStack(spacing: 4) {
                    Text("Real-generation smoke").foregroundStyle(DS.Text.label)
                    if let ms = result.smokeLatencyMs { Text("(\(ms)ms)").foregroundStyle(DS.Text.muted) }
                    if let chars = result.smokeContentChars { Text("· content \(chars) chars").foregroundStyle(DS.Text.muted) }
                    if let chars = result.smokeThinkingChars { Text("· reasoning \(chars) chars").foregroundStyle(DS.Text.muted) }
                }
                .font(DS.Font.caption)
                .monospacedDigit()
                if let prompt = result.smokePrompt {
                    Text("Prompt: \(prompt)")
                        .font(DS.Font.caption)
                        .foregroundStyle(DS.Text.muted)
                }
                if let response = result.smokeResponse, (result.smokeContentChars ?? 1) > 0 {
                    caption("Content")
                    codeBlock(response)
                }
                if let thinking = result.smokeThinking {
                    Button {
                        withAnimation(DSMotion.standard) { reasoningOpen.toggle() }
                    } label: {
                        HStack(spacing: 4) {
                            Text("Reasoning (\(result.smokeThinkingChars ?? thinking.count) chars)")
                            Image(systemName: "chevron.down")
                                .font(.system(size: 11))
                                .rotationEffect(.degrees(reasoningOpen ? 180 : 0))
                        }
                        .font(DS.Font.caption)
                        .foregroundStyle(DS.Text.secondary)
                        .monospacedDigit()
                    }
                    .buttonStyle(.plain)
                    if reasoningOpen { codeBlock(thinking) }
                }
                if let error = result.smokeError {
                    Text("Smoke generation failed: \(error)")
                        .font(DS.Font.mono)
                        .foregroundStyle(DS.Palette.loss)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                if result.smokeResponse == nil, result.smokeThinking == nil, result.smokeError == nil {
                    FormNote("Smoke generation returned empty — the structured check passed but the model produced no free-form text.")
                }
            }
            if !result.providerMeta.isNull {
                caption("Provider meta")
                codeBlock(Self.pretty(result.providerMeta))
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .overlay {
            RoundedRectangle(cornerRadius: DS.Radius.control, style: .continuous)
                .strokeBorder(DS.Line.card, lineWidth: DS.Line.width)
        }
    }

    private func caption(_ text: String) -> some View {
        Text(text)
            .font(DS.Font.caption)
            .foregroundStyle(DS.Text.muted)
    }

    private func kv(_ label: String, _ value: String) -> some View {
        HStack(spacing: 4) {
            Text(label)
                .font(DS.Font.caption)
                .foregroundStyle(DS.Text.muted)
            Text(verbatim: value)
                .font(DS.Font.mono)
                .foregroundStyle(DS.Text.soft)
        }
    }

    private func codeBlock(_ text: String) -> some View {
        ScrollView(.horizontal, showsIndicators: false) {
            Text(verbatim: text)
                .font(DS.Font.monoSmall)
                .foregroundStyle(DS.Text.label)
                .textSelection(.enabled)
                .padding(10)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(DS.Surface.page, in: .rect(cornerRadius: DS.Radius.control, style: .continuous))
        .overlay {
            RoundedRectangle(cornerRadius: DS.Radius.control, style: .continuous)
                .strokeBorder(DS.Line.divider, lineWidth: DS.Line.width)
        }
    }

    /// `JsonEncoder.withIndent('  ')`, falling back to `toString()`.
    static func pretty(_ value: JSON) -> String {
        if value.isNull { return "" }
        return (try? value.dartEncoded(indent: "  ")) ?? value.dartDescription
    }
}
