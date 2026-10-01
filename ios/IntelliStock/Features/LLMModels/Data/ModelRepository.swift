import Foundation

// Ported from features/models/data/model_repository.dart.

/// Tolerant string coercion — the LLM-test endpoint sometimes returns object
/// values (e.g. structured `smoke_response`/`message`) where a string is
/// expected; stringify instead of failing (Dart `_asStr`).
nonisolated func llmAsString(_ v: JSON) -> String? {
    switch v {
    case .null: nil
    case .string(let s): s
    case .object, .array: (try? v.dartEncoded()) ?? v.dartDescription
    default: v.dartDescription
    }
}

// MARK: - Data model

nonisolated struct LlmModel: Hashable, Sendable, Identifiable {
    let id: String
    let name: String
    let provider: String
    let model: String
    let reasoningEffort: String?
    /// Masked on read.
    let apiKey: String?
    let cliPath: String?
    let extraArgs: String?
    let openaiBaseUrl: String?
    let nvidiaBaseUrl: String?
    let azureOpenaiEndpoint: String?
    let azureOpenaiApiVersion: String?
    let ollamaBaseUrl: String?
    let ollamaKeepAlive: String?
    let ollamaThink: String?
    let bedrockRegion: String?
    let bedrockReasoning: String?
    let openrouterBaseUrl: String?
    let openrouterReferer: String?
    let openrouterTitle: String?
    let modelCacheFamily: String?
    let inputCostPer1m: Double?
    let outputCostPer1m: Double?
    let cacheCreationCostPer1m: Double?
    let cacheReadCostPer1m: Double?
    let createdAt: String?

    init(json j: JSON) {
        id = llmAsString(j["id"]) ?? ""
        name = llmAsString(j["name"]) ?? ""
        provider = llmAsString(j["provider"]) ?? ""
        model = llmAsString(j["model"]) ?? ""
        reasoningEffort = llmAsString(j["reasoning_effort"])
        apiKey = llmAsString(j["api_key"])
        cliPath = llmAsString(j["cli_path"])
        extraArgs = llmAsString(j["extra_args"])
        openaiBaseUrl = llmAsString(j["openai_base_url"])
        nvidiaBaseUrl = llmAsString(j["nvidia_base_url"])
        azureOpenaiEndpoint = llmAsString(j["azure_openai_endpoint"])
        azureOpenaiApiVersion = llmAsString(j["azure_openai_api_version"])
        ollamaBaseUrl = llmAsString(j["ollama_base_url"])
        ollamaKeepAlive = llmAsString(j["ollama_keep_alive"])
        ollamaThink = llmAsString(j["ollama_think"])
        bedrockRegion = llmAsString(j["bedrock_region"])
        bedrockReasoning = llmAsString(j["bedrock_reasoning"])
        openrouterBaseUrl = llmAsString(j["openrouter_base_url"])
        openrouterReferer = llmAsString(j["openrouter_referer"])
        openrouterTitle = llmAsString(j["openrouter_title"])
        modelCacheFamily = llmAsString(j["model_cache_family"])
        inputCostPer1m = j["input_cost_per_1m"].double
        outputCostPer1m = j["output_cost_per_1m"].double
        cacheCreationCostPer1m = j["cache_creation_cost_per_1m"].double
        cacheReadCostPer1m = j["cache_read_cost_per_1m"].double
        createdAt = llmAsString(j["created_at"])
    }
}

/// One entry from `GET /claude/models`. The `requires_credits` flag marks
/// variants (e.g. the `[1m]` context models) that fail unless the Claude
/// subscription has usage credits enabled.
nonisolated struct ClaudeModelOption: Hashable, Sendable {
    let value: String
    let label: String
    let description: String?
    let requiresCredits: Bool

    init(json j: JSON) {
        let value = llmAsString(j["value"]) ?? ""
        let label = llmAsString(j["label"])
        self.value = value
        self.label = (label?.isEmpty == false) ? label! : value
        description = llmAsString(j["description"])
        requiresCredits = j["requires_credits"].boolValue ?? false
    }
}

nonisolated struct LlmTestResult: Hashable, Sendable {
    let provider: String?
    let model: String?
    let effectiveModel: String?
    let latencyMs: Int?
    let result: JSON
    let providerMeta: JSON
    let smokePrompt: String?
    let smokeResponse: String?
    let smokeThinking: String?
    let smokeContentChars: Int?
    let smokeThinkingChars: Int?
    let smokeLatencyMs: Int?
    let smokeError: String?
    let message: String?

    init(json j: JSON) {
        provider = llmAsString(j["provider"])
        model = llmAsString(j["model"])
        effectiveModel = llmAsString(j["effective_model"])
        latencyMs = j["latency_ms"].int
        result = j["result"]
        providerMeta = j["provider_meta"]
        smokePrompt = llmAsString(j["smoke_prompt"])
        smokeResponse = llmAsString(j["smoke_response"])
        smokeThinking = llmAsString(j["smoke_thinking"])
        smokeContentChars = j["smoke_content_chars"].int
        smokeThinkingChars = j["smoke_thinking_chars"].int
        smokeLatencyMs = j["smoke_latency_ms"].int
        smokeError = llmAsString(j["smoke_error"])
        message = llmAsString(j["message"])
    }
}

nonisolated struct CodexStatus: Hashable, Sendable {
    let installed: Bool
    let version: String?
    let authenticated: Bool
    let authMessage: String?
    let installMethod: String

    init(installed: Bool = false, version: String? = nil, authenticated: Bool = false, authMessage: String? = nil, installMethod: String = "unknown") {
        self.installed = installed
        self.version = version
        self.authenticated = authenticated
        self.authMessage = authMessage
        self.installMethod = installMethod
    }

    init(json j: JSON) {
        self.init(
            installed: j["installed"].boolValue ?? false,
            version: llmAsString(j["version"]),
            authenticated: j["authenticated"].boolValue ?? false,
            authMessage: llmAsString(j["auth_message"]),
            installMethod: llmAsString(j["install_method"]) ?? "unknown"
        )
    }
}

// MARK: - Repository

nonisolated struct ModelRepository: Sendable {
    let client: ApiClient

    /// GET /models — returns `{models: [...]}` or bare `[...]`.
    func list() async throws -> [LlmModel] {
        let raw = try await client.get("/models")
        let items: [JSON]
        if raw.isArray {
            items = raw.arrayValue
        } else if raw.isObject {
            items = raw["models"].arrayValue
        } else {
            items = []
        }
        return items.filter(\.isObject).map(LlmModel.init(json:))
    }

    /// POST /models. The backend wraps the created doc as
    /// `{"created": true, "model": {…}}`, so `model` is unwrapped before
    /// parsing (falling back to the bare body). Without this the returned
    /// `LlmModel.id` is empty.
    func create(_ body: JSONObject) async throws -> LlmModel {
        LlmModel(json: Self.unwrapModel(try await client.post("/models", body: .object(body))))
    }

    /// PUT /models/:id. Returns `{"updated": true, "model": {…}}` — unwrapped
    /// as in `create`.
    func update(_ id: String, _ body: JSONObject) async throws -> LlmModel {
        LlmModel(json: Self.unwrapModel(try await client.put("/models/\(id)", body: .object(body))))
    }

    /// Pull the inner `model` doc out of a `{created/updated, model}` envelope.
    static func unwrapModel(_ data: JSON) -> JSON {
        data["model"].isObject ? data["model"] : data
    }

    /// DELETE /models/:id?force=true
    func delete(_ id: String) async throws {
        _ = try await client.delete("/models/\(id)", query: ["force": "true"])
    }

    /// POST /models/:id/test-cli
    func testCli(_ id: String) async throws -> JSONObject {
        try await client.post("/models/\(id)/test-cli").orderedObjectValue
    }

    /// POST /llm/test
    func testLlm(_ body: JSONObject) async throws -> LlmTestResult {
        LlmTestResult(json: try await client.post("/llm/test", body: .object(body)))
    }

    /// POST /ollama/list-models
    func ollamaModels(_ body: JSONObject) async throws -> [JSONObject] {
        Self.modelList(try await client.post("/ollama/list-models", body: .object(body)))
    }

    /// POST /bedrock/list-models
    func bedrockModels(_ body: JSONObject) async throws -> [JSONObject] {
        Self.modelList(try await client.post("/bedrock/list-models", body: .object(body)))
    }

    /// A bare list, or the `models` list of a map.
    private static func modelList(_ raw: JSON) -> [JSONObject] {
        if raw.isArray { return raw.objectElements.map(\.orderedObjectValue) }
        if raw.isObject, raw["models"].isArray { return raw["models"].objectElements.map(\.orderedObjectValue) }
        return []
    }

    // MARK: Codex

    /// GET /codex/status
    func codexStatus() async throws -> CodexStatus {
        CodexStatus(json: try await client.get("/codex/status"))
    }

    /// POST /codex/install
    func codexInstall() async throws -> JSONObject {
        try await client.post("/codex/install", body: .object([:])).orderedObjectValue
    }

    /// GET /codex/install/:jobId
    func codexInstallJob(_ jobId: String) async throws -> JSONObject {
        try await client.get("/codex/install/\(jobId)").orderedObjectValue
    }

    /// POST /codex/login/start
    func codexLoginStart(_ body: JSONObject) async throws -> JSONObject {
        try await client.post("/codex/login/start", body: .object(body)).orderedObjectValue
    }

    /// GET /codex/login/:jobId/status
    func codexLoginStatus(_ jobId: String) async throws -> JSONObject {
        try await client.get("/codex/login/\(jobId)/status").orderedObjectValue
    }

    /// POST /codex/login/:jobId/cancel
    func codexLoginCancel(_ jobId: String) async throws {
        _ = try await client.post("/codex/login/\(jobId)/cancel")
    }

    /// POST /codex/logout
    func codexLogout() async throws {
        _ = try await client.post("/codex/logout")
    }

    // MARK: Claude

    /// GET /claude/models — returns `{models: [...], error?: String}`.
    ///
    /// The model list is dynamic (it depends on the server's claude binary),
    /// so the form always fetches it rather than hardcoding. Pass `cliPath`
    /// to probe a specific binary.
    func claudeModels(cliPath: String? = nil) async throws -> JSONObject {
        let query: [String: JSON] = (cliPath?.isEmpty == false) ? ["cli_path": .string(cliPath!)] : [:]
        return try await client.get("/claude/models", query: query).orderedObjectValue
    }

    /// GET /claude/auth/status
    func claudeAuthStatus() async throws -> JSONObject {
        try await client.get("/claude/auth/status").orderedObjectValue
    }

    /// POST /claude/login/start
    func claudeLoginStart(_ body: JSONObject) async throws -> JSONObject {
        try await client.post("/claude/login/start", body: .object(body)).orderedObjectValue
    }

    /// POST /claude/login/:jobId/submit
    func claudeLoginSubmit(_ jobId: String, _ code: String) async throws -> JSONObject {
        try await client.post("/claude/login/\(jobId)/submit", body: ["code": .string(code)]).orderedObjectValue
    }

    /// GET /claude/login/:jobId/status
    func claudeLoginStatus(_ jobId: String) async throws -> JSONObject {
        try await client.get("/claude/login/\(jobId)/status").orderedObjectValue
    }

    /// POST /claude/login/:jobId/cancel
    func claudeLoginCancel(_ jobId: String) async throws {
        _ = try await client.post("/claude/login/\(jobId)/cancel")
    }

    /// POST /claude/logout
    func claudeLogout() async throws {
        _ = try await client.post("/claude/logout")
    }
}
