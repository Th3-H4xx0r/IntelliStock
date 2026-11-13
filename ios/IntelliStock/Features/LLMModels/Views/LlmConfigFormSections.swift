import SwiftUI

/// The reusable LLM configuration form — `LlmConfigForm` in
/// `llm_config_form.dart`, as the web's fields (labels above, `ui-input`
/// controls): provider, model (with the Claude model picker), cache family,
/// then the provider's own fields and pickers (CLI + setup panels, Ollama,
/// Bedrock, reasoning effort, key, base URLs, OpenRouter, Azure). It lays
/// out inside a `FormSheet`'s column, 20 pt between fields.
struct LlmConfigFormSections: View {
    @Binding var draft: LlmConfigDraft
    let pickers: LlmPickersModel
    var disabled = false

    var body: some View {
        let d = draft
        Group {
            DSField("Provider") {
                FormSelect("Provider", display: LlmOptions.providerLabel(d.provider), selection: Binding(
                    get: { draft.provider },
                    set: { draft = draft.changingProvider(to: $0) }
                )) {
                    ForEach(LlmOptions.providers, id: \.value) { Text($0.label).tag($0.value) }
                }
            }

            if d.provider == "claude-cli" {
                claudeModelField
            } else {
                DSField(d.provider == "azure" ? "Deployment / model name" : "Model") {
                    FormTextField(LlmOptions.modelPlaceholder(d.provider), text: $draft.model, mono: true)
                }
            }

            LlmField(label: "Cache family (optional)", placeholder: "auto (e.g. gpt-oss-120b)",
                     text: $draft.modelCacheFamily, mono: true,
                     hint: "Share the LLM cache across rows with the same underlying model. Leave blank to auto-detect.")

            if d.isCli { cliSection }
            if d.provider == "ollama" { ollamaSection }
            if d.provider == "bedrock" { bedrockSection }

            if LlmOptions.showsReasoningEffort(d.provider) {
                let options = LlmOptions.effortOptions(d.provider)
                DSField("Reasoning effort") {
                    FormSelect("Reasoning effort", display: options.first { $0.value == d.reasoningEffort }?.label ?? d.reasoningEffort, selection: $draft.reasoningEffort) {
                        ForEach(options, id: \.value) { Text($0.label).tag($0.value) }
                    }
                }
            }
            if !d.isCli && d.provider != "ollama" && d.provider != "bedrock" {
                LlmField(label: d.provider == "azure" ? "Azure API key" : "API key",
                         placeholder: d.provider == "nvidia" ? "NVIDIA API Key (nvapi-...)" : "Optional if provided by environment",
                         text: $draft.apiKey, secure: true)
            }
            if d.provider == "openai" {
                LlmField(label: "OpenAI base URL", placeholder: "Optional custom base URL", text: $draft.openaiBaseUrl, mono: true)
            }
            if d.provider == "nvidia" {
                LlmField(label: "NVIDIA NIM base URL", placeholder: "https://integrate.api.nvidia.com/v1",
                         text: $draft.nvidiaBaseUrl, mono: true)
            }

            if d.provider == "openrouter" { openrouterSection }
            if d.provider == "azure" { azureSection }
        }
        .disabled(disabled)
        // The picker fetch is NOT attached here: a modifier on this Group
        // lands on every field. The sheet attaches `llmPickerFetches` once.
    }

    // MARK: Claude model picker

    @ViewBuilder
    private var claudeModelField: some View {
        let d = draft
        VStack(alignment: .leading, spacing: 8) {
            LlmPickerHeader(title: "Model", loading: pickers.claudeLoading) {
                Task { await pickers.fetchClaudeModels(draft) }
            }
            if pickers.claudeModels.isEmpty {
                if !pickers.claudeError.isEmpty {
                    FormNote("Couldn't list Claude models: \(pickers.claudeError). Enter the model name manually below (e.g. claude-sonnet-4-6).")
                }
                FormTextField(LlmOptions.modelPlaceholder("claude-cli"), text: $draft.model, mono: true)
            } else {
                let selection = pickers.claudeSelection(d)
                let display: String = {
                    if selection == LlmOptions.claudeCustom { return "Custom…" }
                    guard let option = pickers.claudeModels.first(where: { $0.value == selection }) else { return "Select a model" }
                    return option.requiresCredits ? "\(option.label) — needs credits" : option.label
                }()
                FormSelect("Model", display: display, selection: Binding<String?>(
                    get: { selection },
                    set: { value in
                        if value == LlmOptions.claudeCustom {
                            pickers.claudeCustom = true
                        } else if let value {
                            pickers.claudeCustom = false
                            draft.model = value
                        }
                    }
                )) {
                    Text("Select a model").tag(String?.none)
                    ForEach(pickers.claudeModels, id: \.value) { option in
                        Text(option.requiresCredits ? "\(option.label) — needs credits" : option.label).tag(Optional(option.value))
                    }
                    Text("Custom…").tag(Optional(LlmOptions.claudeCustom))
                }
                if selection == LlmOptions.claudeCustom {
                    FormTextField(LlmOptions.modelPlaceholder("claude-cli"), text: $draft.model, mono: true)
                }
            }
            Text(Self.creditsHint)
                .font(DS.Font.caption)
                .foregroundStyle(DS.Text.muted)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    static let creditsHint = "1M-context models need usage credits (claude.ai/settings/usage); standard models don't."

    // MARK: Provider sections

    @ViewBuilder
    private var cliSection: some View {
        let d = draft
        LlmField(label: "CLI path", placeholder: d.provider == "codex-cli" ? "codex" : "claude", text: $draft.cliPath, mono: true)
        LlmField(label: "Extra args",
                 placeholder: d.provider == "codex-cli" ? "--sandbox read-only" : "--fallback-model claude-haiku-4-5",
                 text: $draft.extraArgs, mono: true,
                 hint: d.provider == "claude-cli"
                    ? "Uses the claude binary installed on the server (subscription auth). Tools are disabled: Claude Code runs as a text-only LLM. Use the panel below to re-authenticate when the token expires; no SSH needed."
                    : nil)
        if d.provider == "codex-cli" {
            CodexCliSetupPanel(cliPath: d.cliPath.isEmpty ? "codex" : d.cliPath)
        }
        if d.provider == "claude-cli" {
            ClaudeCliSetupPanel(cliPath: d.cliPath.isEmpty ? "claude" : d.cliPath)
        }
    }

    @ViewBuilder
    private var ollamaSection: some View {
        let d = draft
        LlmField(label: "Ollama base URL", placeholder: "http://localhost:11434 or https://ollama.com/v1",
                 text: $draft.ollamaBaseUrl, mono: true)
        LlmField(label: "API key (optional — local Ollama has no auth)",
                 placeholder: "Ollama Cloud Bearer token, or leave blank", text: $draft.apiKey, secure: true)
        VStack(alignment: .leading, spacing: 8) {
            LlmPickerHeader(title: "Pick from installed models", loading: pickers.ollamaLoading) {
                Task { await pickers.fetchOllama(draft, force: true) }
            }
            if !pickers.ollamaError.isEmpty {
                FormNote("Couldn't reach Ollama at this base URL: \(pickers.ollamaError)")
            }
            if !pickers.ollamaModels.isEmpty, pickers.ollamaError.isEmpty {
                let names = pickers.ollamaModels.map { JSON.object($0)["name"].string ?? "" }
                let index = names.firstIndex(of: d.model)
                FormSelect("Model", display: index.map { LlmOptions.ollamaLabel(pickers.ollamaModels[$0]) } ?? "Select a model", selection: Binding<String?>(
                    get: { names.contains(d.model) ? d.model : nil },
                    set: { draft.model = $0 ?? "" }
                )) {
                    Text("Select a model").tag(String?.none)
                    ForEach(Array(pickers.ollamaModels.enumerated()), id: \.offset) { index, m in
                        Text(LlmOptions.ollamaLabel(m)).tag(Optional(names[index]))
                    }
                }
            }
        }
        DSField("Thinking / effort") {
            FormSelect("Thinking / effort", display: LlmOptions.ollamaThink.first { $0.value == d.ollamaThink }?.label ?? d.ollamaThink, selection: $draft.ollamaThink) {
                ForEach(LlmOptions.ollamaThink, id: \.value) { Text($0.label).tag($0.value) }
            }
        }
        LlmField(label: "Keep alive (advanced)", placeholder: "5m", text: $draft.ollamaKeepAlive, mono: true,
                 hint: "A Go duration like 5m (default), 60m or 1h. -1 means never unload.")
    }

    @ViewBuilder
    private var bedrockSection: some View {
        let d = draft
        DSField("AWS region") {
            // The region chips, as one menu: picking a region fills the field.
            Menu {
                Picker("AWS region", selection: $draft.bedrockRegion) {
                    ForEach(LlmOptions.bedrockRegions, id: \.self) { region in
                        Text(verbatim: region).tag(region)
                    }
                }
            } label: {
                Text("Common regions")
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Text.secondary)
            }
        } content: {
            FormTextField("us-east-1", text: $draft.bedrockRegion, mono: true)
        }
        LlmField(label: "API key (required — Bedrock bearer token)", placeholder: "Bedrock API key (bearer token)",
                 text: $draft.apiKey, secure: true)
        VStack(alignment: .leading, spacing: 8) {
            LlmPickerHeader(title: "Pick from available models", loading: pickers.bedrockLoading) {
                Task { await pickers.fetchBedrock(draft, force: true) }
            }
            if !pickers.bedrockError.isEmpty {
                FormNote("Couldn't list Bedrock models: \(pickers.bedrockError). Enter the model id manually above (e.g. us.anthropic.claude-3-5-sonnet-20241022-v2:0).")
            }
            if !pickers.bedrockModels.isEmpty, pickers.bedrockError.isEmpty {
                let ids = pickers.bedrockModels.map { JSON.object($0)["id"].string ?? "" }
                let index = ids.firstIndex(of: d.model)
                FormSelect("Model", display: index.map { LlmOptions.bedrockLabel(pickers.bedrockModels[$0]) } ?? "Select a model", selection: Binding<String?>(
                    get: { ids.contains(d.model) ? d.model : nil },
                    set: { draft.model = $0 ?? "" }
                )) {
                    Text("Select a model").tag(String?.none)
                    ForEach(Array(pickers.bedrockModels.enumerated()), id: \.offset) { index, m in
                        Text(LlmOptions.bedrockLabel(m)).tag(Optional(ids[index]))
                    }
                }
            }
        }
        DSField("Reasoning") {
            FormSelect("Reasoning", display: LlmOptions.bedrockReasoning.first { $0.value == d.bedrockReasoning }?.label ?? "Off", selection: Binding(
                get: { draft.bedrockReasoning },
                set: { draft.bedrockReasoning = $0.isEmpty ? "off" : $0 }
            )) {
                ForEach(LlmOptions.bedrockReasoning, id: \.value) { Text($0.label).tag($0.value) }
            }
        }
    }

    @ViewBuilder
    private var openrouterSection: some View {
        LlmField(label: "OpenRouter base URL", placeholder: "https://openrouter.ai/api/v1", text: $draft.openrouterBaseUrl, mono: true,
                 hint: "Model ids are vendor/model, e.g. anthropic/claude-3.5-sonnet.")
        LlmField(label: "HTTP-Referer (optional)", placeholder: "https://your-site.example", text: $draft.openrouterReferer, mono: true)
        LlmField(label: "X-Title (optional)", placeholder: "IntelliStock", text: $draft.openrouterTitle, mono: true)
    }

    @ViewBuilder
    private var azureSection: some View {
        LlmField(label: "Azure endpoint", placeholder: "https://your-resource.services.ai.azure.com",
                 text: $draft.azureOpenaiEndpoint, mono: true,
                 hint: "Use the Azure resource root plus your deployment or model name. Not a full /models/chat/completions or /openai/v1/ URL.")
        LlmField(label: "API version", placeholder: "2024-10-21", text: $draft.azureOpenaiApiVersion, mono: true)
    }
}

extension View {
    /// `initState` / `didUpdateWidget` of the config form: refetch the
    /// provider's model list when it is shown or its inputs change. Attach
    /// once, to the `Form` holding `LlmConfigFormSections`.
    func llmPickerFetches(_ draft: LlmConfigDraft, _ pickers: LlmPickersModel) -> some View {
        task(id: PickerKey(draft)) {
            switch draft.provider {
            case "ollama": await pickers.fetchOllama(draft)
            case "bedrock": await pickers.fetchBedrock(draft)
            case "claude-cli": await pickers.fetchClaudeModels(draft)
            default: break
            }
        }
    }
}

/// What makes a provider's picker refetch (`didUpdateWidget`): Ollama's base
/// URL, Bedrock's region and key, the Claude CLI path.
private struct PickerKey: Hashable {
    let provider: String
    let first: String
    let second: String

    init(_ d: LlmConfigDraft) {
        provider = d.provider
        switch d.provider {
        case "ollama": (first, second) = (d.ollamaBaseUrl, "")
        case "bedrock": (first, second) = (d.bedrockRegion, d.apiKey)
        case "claude-cli": (first, second) = (d.cliPath, "")
        default: (first, second) = ("", "")
        }
    }
}

/// A labelled text or secure field — `_label` + `_textField` /
/// `_passwordField`, as `ui-field-label` over `ui-input`, with an optional
/// help line.
struct LlmField: View {
    let label: String
    let placeholder: String
    @Binding var text: String
    var mono = false
    var secure = false
    var hint: String?

    var body: some View {
        DSField(label, help: hint) {
            FormTextField(placeholder, text: $text, mono: mono, secure: secure)
        }
    }
}

/// A picker's label with Refresh / Loading… — `_pickerLabel`: the field
/// label and a ghost text button at its end.
private struct LlmPickerHeader: View {
    let title: String
    let loading: Bool
    let onRefresh: () -> Void

    var body: some View {
        HStack {
            Text(title)
                .font(DS.Font.body)
                .foregroundStyle(DS.Text.label)
            Spacer()
            Button(loading ? "Loading…" : "Refresh", action: onRefresh)
                .buttonStyle(.dsGhost)
                .padding(.trailing, -12)
                .disabled(loading)
        }
    }
}
