import SwiftUI

/// The reusable LLM configuration form — `LlmConfigForm` in
/// `llm_config_form.dart`, as form sections: provider, model (with the
/// Claude model picker), cache family, then the provider's own fields and
/// pickers (CLI + setup panels, Ollama, Bedrock, reasoning effort, key, base
/// URLs, OpenRouter, Azure).
struct LlmConfigFormSections: View {
    @Binding var draft: LlmConfigDraft
    let pickers: LlmPickersModel
    var disabled = false

    var body: some View {
        let d = draft
        Group {
            Section {
                Picker("Provider", selection: Binding(
                    get: { draft.provider },
                    set: { draft = draft.changingProvider(to: $0) }
                )) {
                    ForEach(LlmOptions.providers, id: \.value) { Text($0.label).tag($0.value) }
                }
                .pickerStyle(.menu)

                if d.provider == "claude-cli" {
                    claudeModelField
                } else {
                    LlmField(label: d.provider == "azure" ? "Deployment / Model Name" : "Model",
                             placeholder: LlmOptions.modelPlaceholder(d.provider), text: $draft.model, mono: true)
                }

                LlmField(label: "Cache Family (optional)", placeholder: "auto (e.g. gpt-oss-120b)",
                         text: $draft.modelCacheFamily, mono: true,
                         hint: "Share LLM cache across same-underlying-model rows. Leave blank to auto-detect.")
            }

            if d.isCli { cliSection }
            if d.provider == "ollama" { ollamaSection }
            if d.provider == "bedrock" { bedrockSection }

            if LlmOptions.showsReasoningEffort(d.provider) || (!d.isCli && d.provider != "ollama" && d.provider != "bedrock") {
                Section {
                    if LlmOptions.showsReasoningEffort(d.provider) {
                        Picker("Reasoning Effort", selection: $draft.reasoningEffort) {
                            ForEach(LlmOptions.effortOptions(d.provider), id: \.value) { Text($0.label).tag($0.value) }
                        }
                        .pickerStyle(.menu)
                    }
                    if !d.isCli && d.provider != "ollama" && d.provider != "bedrock" {
                        LlmField(label: d.provider == "azure" ? "Azure API Key" : "API Key",
                                 placeholder: d.provider == "nvidia" ? "NVIDIA API Key (nvapi-...)" : "Optional if provided by environment",
                                 text: $draft.apiKey, secure: true)
                    }
                    if d.provider == "openai" {
                        LlmField(label: "OpenAI Base URL", placeholder: "Optional custom base URL", text: $draft.openaiBaseUrl, mono: true)
                    }
                    if d.provider == "nvidia" {
                        LlmField(label: "NVIDIA NIM Base URL", placeholder: "https://integrate.api.nvidia.com/v1",
                                 text: $draft.nvidiaBaseUrl, mono: true)
                    }
                }
            }

            if d.provider == "openrouter" { openrouterSection }
            if d.provider == "azure" { azureSection }
        }
        .disabled(disabled)
        // The picker fetch is NOT attached here: a modifier on this Group
        // lands on every section, so it ran once per section. The form
        // attaches `llmPickerFetches` once.
    }

    // MARK: Claude model picker

    @ViewBuilder
    private var claudeModelField: some View {
        let d = draft
        LlmPickerHeader(title: "Available models", loading: pickers.claudeLoading) {
            Task { await pickers.fetchClaudeModels(draft) }
        }
        if pickers.claudeModels.isEmpty {
            if !pickers.claudeError.isEmpty {
                ModelInfoBox(text: "Couldn't list Claude models: \(pickers.claudeError)\nEnter the model name manually above (e.g. claude-sonnet-4-6).",
                             color: DS.Palette.warning)
            }
            LlmField(label: "Model", placeholder: LlmOptions.modelPlaceholder("claude-cli"), text: $draft.model, mono: true,
                     hint: Self.creditsHint)
        } else {
            let selection = pickers.claudeSelection(d)
            Picker("Model", selection: Binding<String?>(
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
            .pickerStyle(.menu)
            if selection == LlmOptions.claudeCustom {
                LlmField(label: "Model", placeholder: LlmOptions.modelPlaceholder("claude-cli"), text: $draft.model, mono: true)
            }
            Text(Self.creditsHint).font(.caption).foregroundStyle(.secondary)
        }
    }

    static let creditsHint = "1M-context models need usage credits (claude.ai/settings/usage); standard models don't."

    // MARK: Provider sections

    @ViewBuilder
    private var cliSection: some View {
        let d = draft
        Section {
            LlmField(label: "CLI Path", placeholder: d.provider == "codex-cli" ? "codex" : "claude", text: $draft.cliPath, mono: true)
            LlmField(label: "Extra Args",
                     placeholder: d.provider == "codex-cli" ? "--sandbox read-only" : "--fallback-model claude-haiku-4-5",
                     text: $draft.extraArgs, mono: true)
        } footer: {
            if d.provider == "claude-cli" {
                Text("Uses the locally-installed claude binary on the server (subscription auth). Tools are disabled — CC is used as a text-only LLM. Use the panel below to re-authenticate when the token expires; no SSH required.")
            }
        }
        if d.provider == "codex-cli" {
            Section {
                CodexCliSetupPanel(cliPath: d.cliPath.isEmpty ? "codex" : d.cliPath)
            }
        }
        if d.provider == "claude-cli" {
            Section {
                ClaudeCliSetupPanel(cliPath: d.cliPath.isEmpty ? "claude" : d.cliPath)
            }
        }
    }

    private var ollamaSection: some View {
        let d = draft
        return Section {
            LlmField(label: "Ollama Base URL", placeholder: "http://localhost:11434 or https://ollama.com/v1",
                     text: $draft.ollamaBaseUrl, mono: true)
            LlmField(label: "API Key (optional — local Ollama has no auth)",
                     placeholder: "Ollama Cloud Bearer token, or leave blank", text: $draft.apiKey, secure: true)
            LlmPickerHeader(title: "Pick from installed models", loading: pickers.ollamaLoading) {
                Task { await pickers.fetchOllama(draft, force: true) }
            }
            if !pickers.ollamaError.isEmpty {
                ModelInfoBox(text: "Couldn't reach Ollama at this base URL: \(pickers.ollamaError)", color: DS.Palette.warning)
            }
            if !pickers.ollamaModels.isEmpty, pickers.ollamaError.isEmpty {
                let names = pickers.ollamaModels.map { JSON.object($0)["name"].string ?? "" }
                Picker("Model", selection: Binding<String?>(
                    get: { names.contains(d.model) ? d.model : nil },
                    set: { draft.model = $0 ?? "" }
                )) {
                    Text("Select a model").tag(String?.none)
                    ForEach(Array(pickers.ollamaModels.enumerated()), id: \.offset) { index, m in
                        Text(LlmOptions.ollamaLabel(m)).font(.system(.caption, design: .monospaced)).tag(Optional(names[index]))
                    }
                }
                .pickerStyle(.menu)
            }
            Picker("Thinking / Effort", selection: $draft.ollamaThink) {
                ForEach(LlmOptions.ollamaThink, id: \.value) { Text($0.label).tag($0.value) }
            }
            .pickerStyle(.menu)
            DisclosureGroup("Keep Alive (Advanced)") {
                LlmField(label: "Keep Alive", placeholder: "5m", text: $draft.ollamaKeepAlive, mono: true,
                         hint: "Go duration like 5m (default), 60m, 1h. -1 = never unload.")
            }
        }
    }

    private var bedrockSection: some View {
        let d = draft
        return Section {
            LlmField(label: "AWS Region", placeholder: "us-east-1", text: $draft.bedrockRegion, mono: true)
            // The region chips, as one menu: picking a region fills the field.
            Menu {
                Picker("AWS Region", selection: $draft.bedrockRegion) {
                    ForEach(LlmOptions.bedrockRegions, id: \.self) { region in
                        Text(verbatim: region).tag(region)
                    }
                }
            } label: {
                Label("Common regions", systemImage: "globe")
            }
            LlmField(label: "API Key (required — Bedrock bearer token)", placeholder: "Bedrock API key (bearer token)",
                     text: $draft.apiKey, secure: true)
            LlmPickerHeader(title: "Pick from available models", loading: pickers.bedrockLoading) {
                Task { await pickers.fetchBedrock(draft, force: true) }
            }
            if !pickers.bedrockError.isEmpty {
                ModelInfoBox(text: "Couldn't list Bedrock models: \(pickers.bedrockError)\nEnter the model id manually above (e.g. us.anthropic.claude-3-5-sonnet-20241022-v2:0).",
                             color: DS.Palette.warning)
            }
            if !pickers.bedrockModels.isEmpty, pickers.bedrockError.isEmpty {
                let ids = pickers.bedrockModels.map { JSON.object($0)["id"].string ?? "" }
                Picker("Model", selection: Binding<String?>(
                    get: { ids.contains(d.model) ? d.model : nil },
                    set: { draft.model = $0 ?? "" }
                )) {
                    Text("Select a model").tag(String?.none)
                    ForEach(Array(pickers.bedrockModels.enumerated()), id: \.offset) { index, m in
                        Text(LlmOptions.bedrockLabel(m)).font(.system(.caption, design: .monospaced)).tag(Optional(ids[index]))
                    }
                }
                .pickerStyle(.menu)
            }
            Picker("Reasoning", selection: Binding(
                get: { draft.bedrockReasoning },
                set: { draft.bedrockReasoning = $0.isEmpty ? "off" : $0 }
            )) {
                ForEach(LlmOptions.bedrockReasoning, id: \.value) { Text($0.label).tag($0.value) }
            }
            .pickerStyle(.menu)
        }
    }

    private var openrouterSection: some View {
        Section {
            LlmField(label: "OpenRouter Base URL", placeholder: "https://openrouter.ai/api/v1", text: $draft.openrouterBaseUrl, mono: true)
            LlmField(label: "HTTP-Referer (optional)", placeholder: "https://your-site.example", text: $draft.openrouterReferer, mono: true)
            LlmField(label: "X-Title (optional)", placeholder: "IntelliStock", text: $draft.openrouterTitle, mono: true)
        } footer: {
            Text(verbatim: "Model ids are vendor/model, e.g. anthropic/claude-3.5-sonnet.")
        }
    }

    private var azureSection: some View {
        Section {
            LlmField(label: "Azure Endpoint", placeholder: "https://your-resource.services.ai.azure.com",
                     text: $draft.azureOpenaiEndpoint, mono: true)
            LlmField(label: "API Version", placeholder: "2024-10-21", text: $draft.azureOpenaiApiVersion, mono: true)
        } footer: {
            Text(verbatim: "Use the Azure resource root plus your deployment/model name. Do not use a full /models/chat/completions or /openai/v1/ URL here.")
        }
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

/// A labelled text / secure field row — `_label` + `_textField` / `_passwordField`.
struct LlmField: View {
    let label: String
    let placeholder: String
    @Binding var text: String
    var mono = false
    var secure = false
    var hint: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(label)
                .font(.footnote)
                .foregroundStyle(.secondary)
            Group {
                if secure {
                    SecureField(label, text: $text, prompt: Text(verbatim: placeholder))
                } else {
                    TextField(label, text: $text, prompt: Text(verbatim: placeholder))
                }
            }
            .font(mono || secure ? .system(.body, design: .monospaced) : .body)
            .textInputAutocapitalization(.never)
            .autocorrectionDisabled()
            if let hint {
                Text(hint)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .padding(.vertical, 2)
    }
}

/// A picker's label with Refresh / Loading... — `_pickerLabel`.
private struct LlmPickerHeader: View {
    let title: String
    let loading: Bool
    let onRefresh: () -> Void

    var body: some View {
        HStack {
            Text(title)
                .font(.footnote)
                .foregroundStyle(.secondary)
            Spacer()
            Button(loading ? "Loading..." : "Refresh", action: onRefresh)
                .font(.footnote)
                .buttonStyle(.borderless)
                .disabled(loading)
        }
    }
}
