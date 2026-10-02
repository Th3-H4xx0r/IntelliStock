import Foundation

/// The value object bound to the LLM config form — `LlmConfigDraft` in
/// `llm_config_form.dart`.
nonisolated struct LlmConfigDraft: Equatable, Sendable {
    var provider = "gemini"
    var model = ""
    var reasoningEffort = ""
    var apiKey = ""
    var openaiBaseUrl = ""
    var nvidiaBaseUrl = ""
    var azureOpenaiEndpoint = ""
    var azureOpenaiApiVersion = "2024-10-21"
    var cliPath = ""
    var extraArgs = ""
    var ollamaBaseUrl = "http://localhost:11434"
    var ollamaKeepAlive = ""
    var ollamaThink = ""
    var bedrockRegion = "us-east-1"
    var bedrockReasoning = "off"
    var openrouterBaseUrl = "https://openrouter.ai/api/v1"
    var openrouterReferer = ""
    var openrouterTitle = ""
    var modelCacheFamily = ""

    static let defaultOllamaBaseUrl = "http://localhost:11434"
    static let defaultOpenrouterBaseUrl = "https://openrouter.ai/api/v1"

    var isCli: Bool { provider == "claude-cli" || provider == "codex-cli" }

    /// Dart `copyWith`: a nil argument keeps the current value.
    func copyWith(
        provider: String? = nil, model: String? = nil, reasoningEffort: String? = nil, apiKey: String? = nil,
        openaiBaseUrl: String? = nil, nvidiaBaseUrl: String? = nil, azureOpenaiEndpoint: String? = nil,
        azureOpenaiApiVersion: String? = nil, cliPath: String? = nil, extraArgs: String? = nil,
        ollamaBaseUrl: String? = nil, ollamaKeepAlive: String? = nil, ollamaThink: String? = nil,
        bedrockRegion: String? = nil, bedrockReasoning: String? = nil, openrouterBaseUrl: String? = nil,
        openrouterReferer: String? = nil, openrouterTitle: String? = nil, modelCacheFamily: String? = nil
    ) -> LlmConfigDraft {
        var d = self
        if let provider { d.provider = provider }
        if let model { d.model = model }
        if let reasoningEffort { d.reasoningEffort = reasoningEffort }
        if let apiKey { d.apiKey = apiKey }
        if let openaiBaseUrl { d.openaiBaseUrl = openaiBaseUrl }
        if let nvidiaBaseUrl { d.nvidiaBaseUrl = nvidiaBaseUrl }
        if let azureOpenaiEndpoint { d.azureOpenaiEndpoint = azureOpenaiEndpoint }
        if let azureOpenaiApiVersion { d.azureOpenaiApiVersion = azureOpenaiApiVersion }
        if let cliPath { d.cliPath = cliPath }
        if let extraArgs { d.extraArgs = extraArgs }
        if let ollamaBaseUrl { d.ollamaBaseUrl = ollamaBaseUrl }
        if let ollamaKeepAlive { d.ollamaKeepAlive = ollamaKeepAlive }
        if let ollamaThink { d.ollamaThink = ollamaThink }
        if let bedrockRegion { d.bedrockRegion = bedrockRegion }
        if let bedrockReasoning { d.bedrockReasoning = bedrockReasoning }
        if let openrouterBaseUrl { d.openrouterBaseUrl = openrouterBaseUrl }
        if let openrouterReferer { d.openrouterReferer = openrouterReferer }
        if let openrouterTitle { d.openrouterTitle = openrouterTitle }
        if let modelCacheFamily { d.modelCacheFamily = modelCacheFamily }
        return d
    }

    /// The create/update payload.
    func toPayload() -> JSONObject {
        var m: JSONObject = ["provider": .string(provider), "model": .string(model.trimmed)]
        if !reasoningEffort.isEmpty { m["reasoning_effort"] = .string(reasoningEffort) }
        if !modelCacheFamily.isEmpty { m["model_cache_family"] = .string(modelCacheFamily.trimmed.lowercased()) }
        if isCli {
            if !cliPath.isEmpty { m["cli_path"] = .string(cliPath.trimmed) }
            if !extraArgs.isEmpty { m["extra_args"] = .string(extraArgs.trimmed) }
        } else {
            if !apiKey.isEmpty { m["api_key"] = .string(apiKey.trimmed) }
            if !openaiBaseUrl.isEmpty { m["openai_base_url"] = .string(openaiBaseUrl.trimmed) }
            if !nvidiaBaseUrl.isEmpty { m["nvidia_base_url"] = .string(nvidiaBaseUrl.trimmed) }
            if !azureOpenaiEndpoint.isEmpty { m["azure_openai_endpoint"] = .string(azureOpenaiEndpoint.trimmed) }
            if !azureOpenaiApiVersion.isEmpty { m["azure_openai_api_version"] = .string(azureOpenaiApiVersion.trimmed) }
        }
        if provider == "ollama" {
            m["ollama_base_url"] = .string(ollamaBaseUrl.isEmpty ? Self.defaultOllamaBaseUrl : ollamaBaseUrl.trimmed)
            if !ollamaKeepAlive.isEmpty { m["ollama_keep_alive"] = .string(ollamaKeepAlive.trimmed) }
            if !ollamaThink.isEmpty { m["ollama_think"] = .string(ollamaThink.trimmed) }
        }
        if provider == "bedrock" {
            if !bedrockRegion.isEmpty { m["bedrock_region"] = .string(bedrockRegion.trimmed) }
            if !bedrockReasoning.isEmpty { m["bedrock_reasoning"] = .string(bedrockReasoning.trimmed.lowercased()) }
        }
        if provider == "openrouter" {
            m["openrouter_base_url"] = .string(openrouterBaseUrl.isEmpty ? Self.defaultOpenrouterBaseUrl : openrouterBaseUrl.trimmed)
            if !openrouterReferer.isEmpty { m["openrouter_referer"] = .string(openrouterReferer.trimmed) }
            if !openrouterTitle.isEmpty { m["openrouter_title"] = .string(openrouterTitle.trimmed) }
        }
        return m
    }

    /// The `/llm/test` payload (a flat body matching the model fields).
    func toTestPayload() -> JSONObject { toPayload() }

    /// `_onProviderChanged`: switching provider resets the other providers'
    /// fields and fills this one's defaults.
    func changingProvider(to value: String) -> LlmConfigDraft {
        var next = copyWith(provider: value)
        if value == "claude-cli" || value == "codex-cli" {
            next = next.copyWith(reasoningEffort: "", apiKey: "", openaiBaseUrl: "", nvidiaBaseUrl: "",
                                 azureOpenaiEndpoint: "", azureOpenaiApiVersion: "2024-10-21")
        } else {
            next = next.copyWith(cliPath: "", extraArgs: "")
        }
        if value == "ollama" {
            if next.ollamaBaseUrl.isEmpty { next.ollamaBaseUrl = Self.defaultOllamaBaseUrl }
            next.reasoningEffort = ""
        } else {
            next = next.copyWith(ollamaBaseUrl: "", ollamaKeepAlive: "", ollamaThink: "")
        }
        if value == "bedrock" {
            if next.bedrockRegion.isEmpty { next.bedrockRegion = "us-east-1" }
            if next.bedrockReasoning.isEmpty { next.bedrockReasoning = "off" }
            next.reasoningEffort = ""
        } else {
            next = next.copyWith(bedrockRegion: "", bedrockReasoning: "")
        }
        if value == "openrouter" {
            if next.openrouterBaseUrl.isEmpty { next.openrouterBaseUrl = Self.defaultOpenrouterBaseUrl }
        } else {
            next = next.copyWith(openrouterBaseUrl: "", openrouterReferer: "", openrouterTitle: "")
        }
        return next
    }

    /// The edit sheet's prefill from a saved model (the key stays blank —
    /// it comes back masked).
    init(editing e: LlmModel) {
        provider = e.provider
        model = e.model
        reasoningEffort = e.reasoningEffort ?? ""
        apiKey = ""
        openaiBaseUrl = e.openaiBaseUrl ?? ""
        nvidiaBaseUrl = e.nvidiaBaseUrl ?? ""
        azureOpenaiEndpoint = e.azureOpenaiEndpoint ?? ""
        azureOpenaiApiVersion = e.azureOpenaiApiVersion ?? "2024-10-21"
        cliPath = e.cliPath ?? ""
        extraArgs = e.extraArgs ?? ""
        ollamaBaseUrl = e.ollamaBaseUrl?.isEmpty == false ? e.ollamaBaseUrl! : Self.defaultOllamaBaseUrl
        ollamaKeepAlive = e.ollamaKeepAlive ?? ""
        ollamaThink = e.ollamaThink ?? ""
        bedrockRegion = e.bedrockRegion?.isEmpty == false ? e.bedrockRegion! : "us-east-1"
        bedrockReasoning = e.bedrockReasoning ?? "off"
        openrouterBaseUrl = e.openrouterBaseUrl?.isEmpty == false ? e.openrouterBaseUrl! : Self.defaultOpenrouterBaseUrl
        openrouterReferer = e.openrouterReferer ?? ""
        openrouterTitle = e.openrouterTitle ?? ""
        modelCacheFamily = e.modelCacheFamily ?? ""
    }

    init() {}

    init(provider: String, model: String = "") {
        self.provider = provider
        self.model = model
    }
}

/// The form's option lists and labels (`_kProviders`, `_kReasoningEffort`, …).
nonisolated enum LlmOptions {
    typealias Option = (value: String, label: String)

    static let providers: [Option] = [
        ("gemini", "Google Gemini"),
        ("deepseek", "DeepSeek"),
        ("openai", "OpenAI Compatible"),
        ("azure", "Azure OpenAI"),
        ("nvidia", "NVIDIA NIM"),
        ("ollama", "Ollama (local/cloud)"),
        ("bedrock", "AWS Bedrock"),
        ("openrouter", "OpenRouter"),
        ("claude-cli", "Claude Code CLI"),
        ("codex-cli", "OpenAI Codex CLI"),
    ]

    static let reasoningEffort: [Option] = [("", "Default"), ("low", "Low"), ("medium", "Medium"), ("high", "High")]
    static let nvidiaEffort: [Option] = [("", "Default"), ("none", "None (off)"), ("low", "Low"), ("medium", "Medium"), ("high", "High")]
    static let claudeCliEffort: [Option] = [("", "Default"), ("low", "Low"), ("medium", "Medium"), ("high", "High")]
    static let ollamaThink: [Option] = [("", "Default"), ("off", "Off"), ("on", "On"), ("low", "Low"), ("medium", "Medium"), ("high", "High")]
    static let bedrockReasoning: [Option] = [("off", "Off"), ("low", "Low"), ("medium", "Medium"), ("high", "High")]
    static let bedrockRegions = [
        "us-east-1", "us-west-2", "us-east-2",
        "eu-central-1", "eu-west-1", "eu-west-3",
        "ap-southeast-1", "ap-southeast-2", "ap-northeast-1",
    ]

    /// The "Custom…" entry of the Claude model picker.
    static let claudeCustom = "__custom__"

    static func effortOptions(_ provider: String) -> [Option] {
        switch provider {
        case "nvidia": nvidiaEffort
        case "claude-cli": claudeCliEffort
        default: reasoningEffort
        }
    }

    static func showsReasoningEffort(_ provider: String) -> Bool {
        ["azure", "openai", "nvidia", "openrouter", "claude-cli", "codex-cli"].contains(provider)
    }

    static func modelPlaceholder(_ provider: String) -> String {
        switch provider {
        case "azure": "e.g. gpt-5.2 deployment name"
        case "claude-cli": "claude-sonnet-4-6"
        case "codex-cli": "gpt-5-codex"
        default: "e.g. gemini-3-flash-preview"
        }
    }

    /// `_providerLabel` in `models_screen.dart`.
    static func providerLabel(_ provider: String) -> String {
        providers.first { $0.value == provider }?.label ?? provider
    }

    /// The Ollama picker row: `name · size · quant`.
    static func ollamaLabel(_ m: JSONObject) -> String {
        let json = JSON.object(m)
        var parts = [json["name"].string ?? ""]
        if let size = json["parameter_size"].string { parts.append(size) }
        if let quant = json["quantization_level"].string { parts.append(quant) }
        return parts.joined(separator: " · ")
    }

    /// The Bedrock picker row: `id · profile · provider`.
    static func bedrockLabel(_ m: JSONObject) -> String {
        let json = JSON.object(m)
        var parts = [json["id"].string ?? ""]
        if json["kind"].string == "inference_profile" { parts.append("profile") }
        if let provider = json["provider_name"].string { parts.append(provider) }
        return parts.joined(separator: " · ")
    }
}

/// The model card's cells — `_reasoningCell` / `_keyCell`.
nonisolated enum LlmModelCells {
    static func reasoning(_ m: LlmModel) -> String {
        if m.provider == "claude-cli" || m.provider == "codex-cli" { return "—" }
        if m.provider == "ollama" {
            let t = (m.ollamaThink ?? "").trimmed.lowercased()
            if t.isEmpty { return "Default" }
            if t == "true" || t == "on" { return "On" }
            if t == "false" || t == "off" { return "Off" }
            return capitalised(t)
        }
        if m.provider == "bedrock" {
            let r = (m.bedrockReasoning ?? "").trimmed.lowercased()
            if r.isEmpty || r == "off" { return "Off" }
            return capitalised(r)
        }
        let e = (m.reasoningEffort ?? "").trimmed
        if e.isEmpty { return "Default" }
        return capitalised(e)
    }

    static func key(_ m: LlmModel) -> String {
        if m.provider == "claude-cli" { return m.cliPath?.isEmpty == false ? m.cliPath! : "claude" }
        if m.provider == "codex-cli" { return m.cliPath?.isEmpty == false ? m.cliPath! : "codex" }
        return m.apiKey?.isEmpty == false ? m.apiKey! : "—"
    }

    /// `t[0].toUpperCase() + t.substring(1)`.
    static func capitalised(_ s: String) -> String {
        guard let first = s.first else { return s }
        return first.uppercased() + s.dropFirst()
    }

    /// The card's CLI test line from `/models/:id/test-cli`.
    static func cliTestMessage(_ data: JSONObject) -> (ok: Bool, message: String) {
        let json = JSON.object(data)
        if json["ok"].boolValue ?? false {
            let version = json["version"].string ?? "?"
            let loggedIn = json["logged_in"].boolValue ?? false
            let response = json["model_response"].string
            return (true, "✓ v\(version), \(loggedIn ? "logged in" : "not logged in")\(response.map { ", response: \($0)" } ?? "")")
        }
        return (false, json["error"].string ?? "Unknown error")
    }
}

extension String {
    nonisolated fileprivate var trimmed: String { trimmingCharacters(in: .whitespacesAndNewlines) }
}
