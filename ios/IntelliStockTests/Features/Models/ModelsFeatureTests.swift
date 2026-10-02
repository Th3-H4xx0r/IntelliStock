import Foundation
import Testing
@testable import IntelliStock

/// `test/features/models/llm_config_draft_test.dart`.
@Suite struct ModelsLlmConfigDraftTests {
    @Test func geminiSendsApiKeyNotCliFields() {
        var d = LlmConfigDraft(provider: "gemini", model: "gemini-3-flash")
        d.apiKey = "test-key"
        d.openaiBaseUrl = "should-be-excluded-for-gemini"
        let p = d.toPayload()
        #expect(p["provider"] == "gemini")
        #expect(p["model"] == "gemini-3-flash")
        #expect(p["api_key"] == "test-key")
        #expect(p["cli_path"] == nil)
        #expect(p["extra_args"] == nil)
    }

    @Test func claudeCliSendsCliFieldsNoApiKey() {
        var d = LlmConfigDraft(provider: "claude-cli", model: "claude-sonnet-4-6")
        d.cliPath = "/usr/local/bin/claude"
        d.extraArgs = "--effort high"
        d.apiKey = "should-be-ignored"
        let p = d.toPayload()
        #expect(p["provider"] == "claude-cli")
        #expect(p["cli_path"] == "/usr/local/bin/claude")
        #expect(p["extra_args"] == "--effort high")
        #expect(p["api_key"] == nil)
    }

    @Test func codexCliSendsCliFieldsNoApiKey() {
        var d = LlmConfigDraft(provider: "codex-cli", model: "gpt-5-codex")
        d.cliPath = "codex"
        d.apiKey = "ignored"
        let p = d.toPayload()
        #expect(p["provider"] == "codex-cli")
        #expect(p["cli_path"] == "codex")
        #expect(p["api_key"] == nil)
    }

    @Test func ollamaSendsOllamaFields() {
        var d = LlmConfigDraft(provider: "ollama", model: "llama3")
        d.ollamaBaseUrl = "http://localhost:11434"
        d.ollamaThink = "on"
        d.ollamaKeepAlive = "10m"
        let p = d.toPayload()
        #expect(p["provider"] == "ollama")
        #expect(p["ollama_base_url"] == "http://localhost:11434")
        #expect(p["ollama_think"] == "on")
        #expect(p["ollama_keep_alive"] == "10m")
    }

    @Test func ollamaFallsBackToTheDefaultBaseUrl() {
        var d = LlmConfigDraft(provider: "ollama", model: "llama3")
        d.ollamaBaseUrl = ""
        #expect(d.toPayload()["ollama_base_url"] == "http://localhost:11434")
    }

    @Test func bedrockSendsRegionAndReasoning() {
        var d = LlmConfigDraft(provider: "bedrock", model: "us.anthropic.claude-3-5-sonnet-20241022-v2:0")
        d.bedrockRegion = "us-west-2"
        d.bedrockReasoning = "high"
        d.apiKey = "bearer-token"
        let p = d.toPayload()
        #expect(p["provider"] == "bedrock")
        #expect(p["bedrock_region"] == "us-west-2")
        #expect(p["bedrock_reasoning"] == "high")
        #expect(p["api_key"] == "bearer-token")
    }

    @Test func openrouterSendsBaseUrlAndAttributionHeaders() {
        var d = LlmConfigDraft(provider: "openrouter", model: "anthropic/claude-3.5-sonnet")
        d.openrouterBaseUrl = "https://openrouter.ai/api/v1"
        d.openrouterReferer = "https://intellistock.app"
        d.openrouterTitle = "IntelliStock"
        d.apiKey = "sk-or-key"
        d.reasoningEffort = "high"
        let p = d.toPayload()
        #expect(p["provider"] == "openrouter")
        #expect(p["openrouter_base_url"] == "https://openrouter.ai/api/v1")
        #expect(p["openrouter_referer"] == "https://intellistock.app")
        #expect(p["openrouter_title"] == "IntelliStock")
        #expect(p["api_key"] == "sk-or-key")
        #expect(p["reasoning_effort"] == "high")
    }

    @Test func openrouterFallsBackToTheDefaultBaseUrlAndOmitsEmptyHeaders() {
        var d = LlmConfigDraft(provider: "openrouter", model: "openai/gpt-4o-mini")
        d.openrouterBaseUrl = ""
        let p = d.toPayload()
        #expect(p["openrouter_base_url"] == "https://openrouter.ai/api/v1")
        #expect(p["openrouter_referer"] == nil)
        #expect(p["openrouter_title"] == nil)
    }

    @Test func openrouterCopyWithRoundTripsTheNewFields() {
        let d = LlmConfigDraft(provider: "openrouter", model: "x/y")
        let d2 = d.copyWith(openrouterReferer: "https://a", openrouterTitle: "T")
        #expect(d2.openrouterReferer == "https://a")
        #expect(d2.openrouterTitle == "T")
        #expect(d2.openrouterBaseUrl == "https://openrouter.ai/api/v1")
    }

    @Test func azureSendsEndpointAndApiVersion() {
        var d = LlmConfigDraft(provider: "azure", model: "gpt-5-deployment")
        d.azureOpenaiEndpoint = "https://my-resource.services.ai.azure.com"
        d.azureOpenaiApiVersion = "2024-10-21"
        d.apiKey = "azure-key"
        d.reasoningEffort = "medium"
        let p = d.toPayload()
        #expect(p["azure_openai_endpoint"] == "https://my-resource.services.ai.azure.com")
        #expect(p["azure_openai_api_version"] == "2024-10-21")
        #expect(p["api_key"] == "azure-key")
        #expect(p["reasoning_effort"] == "medium")
    }

    @Test func emptyOptionalFieldsAreExcluded() {
        let p = LlmConfigDraft(provider: "openai", model: "gpt-4o").toPayload()
        #expect(p["reasoning_effort"] == nil)
        #expect(p["model_cache_family"] == nil)
        #expect(p["openai_base_url"] == nil)
    }

    @Test func modelCacheFamilyIsLowercased() {
        var d = LlmConfigDraft(provider: "gemini", model: "gemini-pro")
        d.modelCacheFamily = "GPT-OSS-120B"
        #expect(d.toPayload()["model_cache_family"] == "gpt-oss-120b")
    }

    @Test func reasoningEffortIsIncludedWhenSet() {
        var d = LlmConfigDraft(provider: "openai", model: "o4-mini")
        d.reasoningEffort = "high"
        #expect(d.toPayload()["reasoning_effort"] == "high")
    }

    @Test func copyWithPreservesUnchangedFields() {
        var d = LlmConfigDraft(provider: "gemini", model: "gemini-pro")
        d.apiKey = "k"
        let d2 = d.copyWith(model: "gemini-flash")
        #expect(d2.provider == "gemini")
        #expect(d2.model == "gemini-flash")
        #expect(d2.apiKey == "k")
    }

    @Test func payloadKeyOrderFollowsTheDartMap() {
        var d = LlmConfigDraft(provider: "gemini", model: " m ")
        d.apiKey = " k "
        d.reasoningEffort = "low"
        #expect(d.toPayload().keys == ["provider", "model", "reasoning_effort", "api_key", "azure_openai_api_version"])
        #expect(d.toPayload()["model"] == "m")
    }

    @Test func changingProviderResetsTheOthers() {
        var d = LlmConfigDraft(provider: "openai", model: "x")
        d.apiKey = "k"
        d.reasoningEffort = "high"
        let cli = d.changingProvider(to: "claude-cli")
        #expect(cli.apiKey.isEmpty && cli.reasoningEffort.isEmpty)
        #expect(cli.ollamaBaseUrl.isEmpty && cli.bedrockRegion.isEmpty && cli.openrouterBaseUrl.isEmpty)

        let ollama = cli.changingProvider(to: "ollama")
        #expect(ollama.ollamaBaseUrl == "http://localhost:11434")
        let bedrock = ollama.changingProvider(to: "bedrock")
        #expect(bedrock.bedrockRegion == "us-east-1" && bedrock.bedrockReasoning == "off")
        #expect(bedrock.ollamaBaseUrl.isEmpty)
        let router = bedrock.changingProvider(to: "openrouter")
        #expect(router.openrouterBaseUrl == "https://openrouter.ai/api/v1")
        #expect(router.bedrockRegion.isEmpty)
    }

    @Test func editingPrefillsWithDefaults() {
        let m = LlmModel(json: ["id": "1", "name": "N", "provider": "ollama", "model": "llama", "api_key": "••••", "ollama_base_url": ""])
        let d = LlmConfigDraft(editing: m)
        #expect(d.apiKey.isEmpty)
        #expect(d.ollamaBaseUrl == "http://localhost:11434")
        #expect(d.azureOpenaiApiVersion == "2024-10-21")
        #expect(d.bedrockRegion == "us-east-1")
        #expect(d.openrouterBaseUrl == "https://openrouter.ai/api/v1")
    }
}

/// The screen's helpers and options.
@Suite struct ModelsCellTests {
    private func model(_ json: JSON) -> LlmModel { LlmModel(json: json) }

    @Test func providerLabels() {
        #expect(LlmOptions.providerLabel("gemini") == "Google Gemini")
        #expect(LlmOptions.providerLabel("codex-cli") == "OpenAI Codex CLI")
        #expect(LlmOptions.providerLabel("mystery") == "mystery")
    }

    @Test func reasoningCells() {
        #expect(LlmModelCells.reasoning(model(["provider": "claude-cli"])) == "—")
        #expect(LlmModelCells.reasoning(model(["provider": "ollama"])) == "Default")
        #expect(LlmModelCells.reasoning(model(["provider": "ollama", "ollama_think": "true"])) == "On")
        #expect(LlmModelCells.reasoning(model(["provider": "ollama", "ollama_think": "off"])) == "Off")
        #expect(LlmModelCells.reasoning(model(["provider": "ollama", "ollama_think": "HIGH"])) == "High")
        #expect(LlmModelCells.reasoning(model(["provider": "bedrock"])) == "Off")
        #expect(LlmModelCells.reasoning(model(["provider": "bedrock", "bedrock_reasoning": "medium"])) == "Medium")
        #expect(LlmModelCells.reasoning(model(["provider": "openai"])) == "Default")
        #expect(LlmModelCells.reasoning(model(["provider": "openai", "reasoning_effort": "low"])) == "Low")
    }

    @Test func keyCells() {
        #expect(LlmModelCells.key(model(["provider": "claude-cli"])) == "claude")
        #expect(LlmModelCells.key(model(["provider": "codex-cli", "cli_path": "/bin/codex"])) == "/bin/codex")
        #expect(LlmModelCells.key(model(["provider": "gemini", "api_key": "sk-…"])) == "sk-…")
        #expect(LlmModelCells.key(model(["provider": "gemini"])) == "—")
    }

    @Test func cliTestMessages() {
        #expect(LlmModelCells.cliTestMessage(["ok": true, "version": "2.1", "logged_in": true, "model_response": "pong"]).message
            == "✓ v2.1, logged in, response: pong")
        #expect(LlmModelCells.cliTestMessage(["ok": true]).message == "✓ v?, not logged in")
        #expect(LlmModelCells.cliTestMessage(["ok": false, "error": "401"]) == (false, "401"))
        #expect(LlmModelCells.cliTestMessage([:]).message == "Unknown error")
    }

    @Test func effortOptionsAndPlaceholders() {
        #expect(LlmOptions.effortOptions("nvidia").map(\.value) == ["", "none", "low", "medium", "high"])
        #expect(LlmOptions.showsReasoningEffort("openrouter"))
        #expect(!LlmOptions.showsReasoningEffort("gemini"))
        #expect(LlmOptions.modelPlaceholder("azure") == "e.g. gpt-5.2 deployment name")
        #expect(LlmOptions.ollamaLabel(["name": "llama3", "parameter_size": "8B", "quantization_level": "Q4"]) == "llama3 · 8B · Q4")
        #expect(LlmOptions.bedrockLabel(["id": "a.b", "kind": "inference_profile", "provider_name": "Anthropic"]) == "a.b · profile · Anthropic")
    }

    @Test func loginUrlAllowList() {
        let claude = ClaudeSetupModel.allowedLoginHosts
        #expect(isSafeCliLoginURL("https://claude.ai/oauth?x=1", allowedHosts: claude))
        #expect(isSafeCliLoginURL("https://CONSOLE.anthropic.com/a", allowedHosts: claude))
        #expect(!isSafeCliLoginURL("https://evil.com/claude.ai", allowedHosts: claude))
        #expect(!isSafeCliLoginURL("https://user@claude.ai/", allowedHosts: claude))
        #expect(!isSafeCliLoginURL("javascript:alert(1)", allowedHosts: claude))
        #expect(!isSafeCliLoginURL("", allowedHosts: claude))
        #expect(isSafeCliLoginURL("https://auth.openai.com/device", allowedHosts: CodexSetupModel.allowedPairingHosts))
    }

    @Test func tolerantStrings() {
        #expect(cliSetupString(.null) == nil)
        #expect(cliSetupString("v1") == "v1")
        #expect(cliSetupString(2) == "2")
        #expect(cliSetupString(["a": 1]) == #"{"a":1}"#)
    }
}

/// The Add / Edit sheet, the pickers and the CLI setup panels against a stub.
@MainActor
@Suite struct ModelsEditorTests {
    private func makeStub() -> (DataStub, ChatStubRoutes) {
        let stub = DataStub()
        let routes = ChatStubRoutes()
        routes.stub = stub
        stub.handler = { routes.answer($0) }
        return (stub, routes)
    }

    private func body(_ request: URLRequest?) throws -> JSON {
        try JSON(data: request?.httpBody ?? Data())
    }

    @Test func validationMessages() async {
        let (stub, _) = makeStub()
        let client = stub.client
        let editor = ModelEditorModel(existing: nil, repository: { ModelRepository(client: client) })
        await editor.testAndSave()
        #expect(editor.statusMsg == "Name is required")
        editor.name = "N"
        await editor.testAndSave()
        #expect(editor.statusMsg == "Model is required")
        await editor.testOnly()
        #expect(editor.statusMsg == "Model is required")
        editor.draft = editor.draft.changingProvider(to: "claude-cli")
        editor.draft.model = "claude-sonnet-4-6"
        await editor.testOnly()
        #expect(editor.statusMsg == "Use the cable icon on the saved row to test a claude-cli model.")
        #expect(stub.requests.isEmpty)
        #expect(editor.primaryLabel == "Test & Save")
    }

    @Test func geminiWithoutAKeySavesWithoutTesting() async throws {
        let (stub, routes) = makeStub()
        routes.set("POST /models", #"{"model":{"id":"m1","name":"N","provider":"gemini","model":"g"}}"#)
        let client = stub.client
        let editor = ModelEditorModel(existing: nil, repository: { ModelRepository(client: client) })
        editor.name = " N "
        editor.draft.model = "g"
        editor.inputCost = "3"
        editor.outputCost = "x"
        await editor.testAndSave()
        #expect(stub.requests.map { "\($0.method) \($0.path)" } == ["POST /models"])
        let sent = try body(stub.last)
        #expect(sent["name"] == "N")
        #expect(sent["input_cost_per_1m"] == 3.0)
        #expect(sent["output_cost_per_1m"] == .null)
        #expect(editor.statusMsg == "LLM test passed. Model saved.")
        #expect(editor.statusOk && editor.saved)
    }

    @Test func aKeyMeansTestThenSave() async {
        let (stub, routes) = makeStub()
        routes.set("POST /llm/test", #"{"provider":"openai","model":"gpt","latency_ms":12}"#)
        routes.set("PUT /models/m1", #"{"id":"m1","name":"N","provider":"openai","model":"gpt"}"#)
        let client = stub.client
        let existing = LlmModel(json: ["id": "m1", "name": "N", "provider": "openai", "model": "gpt", "input_cost_per_1m": 3])
        let editor = ModelEditorModel(existing: existing, repository: { ModelRepository(client: client) })
        #expect(editor.inputCost == "3.0")
        editor.draft.apiKey = "sk"
        await editor.testAndSave()
        #expect(stub.requests.map { "\($0.method) \($0.path)" } == ["POST /llm/test", "PUT /models/m1"])
        #expect(editor.testResult?.latencyMs == 12)
        #expect(editor.statusMsg == "LLM test passed. Model updated.")
    }

    @Test func aFailedTestStopsTheSave() async {
        let (stub, routes) = makeStub()
        routes.set("POST /llm/test", #"{"detail":"bad key"}"#, status: 400)
        let client = stub.client
        let editor = ModelEditorModel(existing: nil, repository: { ModelRepository(client: client) })
        editor.name = "N"
        editor.draft = editor.draft.changingProvider(to: "ollama")
        editor.draft.model = "llama"
        await editor.testAndSave()
        #expect(editor.statusMsg == "LLM test failed: bad key")
        #expect(!editor.saved && !editor.submitting)
        #expect(stub.requests.count == 1)
    }

    @Test func claudeCliSavesThenRunsTheRealCliTest() async {
        let (stub, routes) = makeStub()
        routes.set("POST /models", #"{"id":"c1","name":"C","provider":"claude-cli","model":"claude-sonnet-4-6"}"#)
        routes.set("POST /models/c1/test-cli", #"{"ok":false,"error":"401 Invalid authentication credentials"}"#)
        let client = stub.client
        let editor = ModelEditorModel(existing: nil, repository: { ModelRepository(client: client) })
        editor.name = "C"
        editor.draft = editor.draft.changingProvider(to: "claude-cli")
        editor.draft.model = "claude-sonnet-4-6"
        await editor.testAndSave()
        #expect(stub.requests.map(\.path) == ["/models", "/models/c1/test-cli"])
        #expect(editor.statusMsg == "Model saved, but Claude CLI test failed: 401 Invalid authentication credentials")
        #expect(editor.saved && !editor.statusOk)

        routes.set("POST /models/c1/test-cli", #"{"ok":true}"#)
        await editor.testAndSave()
        #expect(editor.statusMsg == "Model saved. Claude CLI connection OK (logged in).")
    }

    @Test func testOnlyReportsWithoutSaving() async {
        let (stub, routes) = makeStub()
        routes.set("POST /llm/test", #"{"provider":"gemini"}"#)
        let client = stub.client
        let editor = ModelEditorModel(existing: nil, repository: { ModelRepository(client: client) })
        editor.draft.model = "g"
        await editor.testOnly()
        #expect(editor.statusMsg == "LLM test passed (not saved).")
        #expect(editor.statusOk && !editor.saved)
    }

    @Test func pickerRequests() async throws {
        let (stub, routes) = makeStub()
        routes.set("POST /ollama/list-models", #"{"models":[{"name":"llama3"}]}"#)
        routes.set("POST /bedrock/list-models", #"{"models":[{"id":"b1"}]}"#)
        routes.set("GET /claude/models", #"{"models":[{"value":"opus","label":"Opus"},{"value":""}]}"#)
        let client = stub.client
        let pickers = LlmPickersModel(repository: { ModelRepository(client: client) })

        var d = LlmConfigDraft(provider: "ollama")
        await pickers.fetchOllama(d, force: true)
        #expect(try body(stub.last) == ["base_url": "http://localhost:11434", "force": true])
        #expect(pickers.ollamaModels.count == 1)

        d = LlmConfigDraft(provider: "bedrock")
        await pickers.fetchBedrock(d)          // no key → no request
        #expect(stub.requests.count == 1)
        d.apiKey = "k"
        await pickers.fetchBedrock(d)
        #expect(try body(stub.last) == ["region": "us-east-1", "api_key": "k"])

        d = LlmConfigDraft(provider: "claude-cli", model: "my-alias")
        d.cliPath = "/bin/claude"
        await pickers.fetchClaudeModels(d)
        #expect(stub.last?.queryPairs.first?.0 == "cli_path")
        #expect(pickers.claudeModels.map(\.value) == ["opus"])
        #expect(pickers.claudeCustom)   // the saved model isn't listed
        #expect(pickers.claudeSelection(d) == LlmOptions.claudeCustom)
        pickers.claudeCustom = false
        d.model = "opus"
        #expect(pickers.claudeSelection(d) == "opus")
    }

    @Test func claudeSetupFlow() async throws {
        let (stub, routes) = makeStub()
        routes.set("GET /claude/auth/status", #"{"installed":true,"version":"2.0","authenticated":false,"auth_message":{"m":1}}"#)
        routes.set("POST /claude/login/start", #"{"job_id":"j1","state":"parsed","login_url":"https://claude.ai/oauth"}"#)
        let client = stub.client
        let setup = ClaudeSetupModel(cliPath: "", repository: { ModelRepository(client: client) })
        await setup.fetchStatus()
        #expect(setup.installed && !setup.authenticated)
        #expect(setup.authMessage == #"{"m":1}"#)

        await setup.startLogin()
        #expect(try body(stub.last) == ["cli_path": "claude"])
        #expect(setup.loginLive)

        await setup.submitCode()
        #expect(setup.submitMessage == "Paste the authorization code first.")

        routes.set("POST /claude/login/j1/submit", #"{"state":"failed","error":"bad code","output_tail":"tail"}"#)
        setup.code = " abc "
        await setup.submitCode()
        #expect(try body(stub.last) == ["code": "abc"])
        #expect(setup.submitMessage == "bad code\ntail")

        routes.set("POST /claude/login/start", #"{"job_id":"j2","login_url":"https://evil.example/x"}"#)
        await setup.startLogin()
        #expect(setup.loginUrl.isEmpty)
        #expect(setup.loginError == "claude returned a non-Anthropic login URL; ignoring for safety")

        routes.set("POST /claude/login/j2/cancel", "{}")
        await setup.cancelLogin()
        #expect(setup.loginState == "cancelled")
        #expect(setup.loginJobId == nil)
    }

    @Test func codexInstallAndLoginPoll() async throws {
        let (stub, routes) = makeStub()
        routes.set("GET /codex/status", #"{"installed":false,"install_method":"npm"}"#)
        routes.set("POST /codex/install", #"{"job_id":"i1","state":"running"}"#)
        routes.set("GET /codex/install/i1", #"{"state":"running","log_tail":["a","b"]}"#)
        let client = stub.client
        let clock = ManualClock()
        let setup = CodexSetupModel(cliPath: "", repository: { ModelRepository(client: client) }, sleep: clock.sleep)
        await setup.fetchStatus()
        #expect(setup.installMethod == "npm")

        await setup.startInstall()
        #expect(setup.installState == "running")
        await clock.advance(by: .milliseconds(1500))
        #expect(await eventually { setup.installLog == ["a", "b"] })

        routes.set("GET /codex/install/i1", #"{"state":"success","exit_code":0,"log_tail":["done"]}"#)
        routes.set("GET /codex/status", #"{"installed":true,"authenticated":false,"install_method":"npm"}"#)
        await clock.advance(by: .milliseconds(1500))
        #expect(await eventually { setup.installed })
        #expect(setup.installExitCode == 0)

        routes.set("POST /codex/login/start", #"{"job_id":"l1","state":"pending","pairing_url":"https://auth.openai.com/d","pairing_code":"ABCD"}"#)
        await setup.startLogin()
        #expect(setup.loginWaiting)
        #expect(setup.loginPairingCode == "ABCD")
        routes.set("GET /codex/login/l1/status", #"{"state":"success"}"#)
        routes.set("GET /codex/status", #"{"installed":true,"authenticated":true}"#)
        await clock.advance(by: .milliseconds(2000))
        #expect(await eventually { setup.authenticated })
        #expect(setup.loginState == "success")
        setup.stop()
        #expect(stub.requests.contains { $0.path == "/codex/login/l1/status" })
    }
}

/// Token usage — the controller and the screen's logic.
@MainActor
@Suite struct ModelsTokenUsageTests {
    @Test func rangeSelectsTheBucket() async {
        let stub = DataStub(json: "{}")
        let client = stub.client
        let model = TokenUsageModel(repository: { TokenUsageRepository(client: client) })
        await model.refreshNow()
        let series = stub.requests.first { $0.path == "/llm-usage/timeseries" }
        #expect(series?.queryPairs.contains { $0 == ("bucket", "hour") } == true)

        await model.setRange("7d")
        #expect(model.range == "7d")
        let weekly = stub.requests.last { $0.path == "/llm-usage/timeseries" }
        #expect(weekly?.queryPairs.contains { $0 == ("bucket", "day") } == true)
        #expect(weekly?.queryPairs.contains { $0 == ("range", "7d") } == true)
        #expect(model.data.value != nil)
    }

    @Test func telemetryStates() {
        #expect(TelemetryState(nil) == .awaiting)
        #expect(TelemetryState(TelemetryHealth(json: ["write_errors_24h": 0, "last_flush_age_s": 5])) == .healthy)
        #expect(TelemetryState(TelemetryHealth(json: ["write_errors_24h": 2, "last_flush_age_s": 5])) == .degraded)
        #expect(TelemetryState(TelemetryHealth(json: ["write_errors_24h": 0, "last_flush_age_s": 31])) == .lagging)
        #expect(TelemetryState.awaiting.label == "Awaiting data")
    }

    @Test func kpis() {
        let summary = UsageSummary(json: [
            "total_cost_usd": 3.0, "total_calls": 4, "total_tokens": 1000, "max_plan_estimate_usd": 150,
            "by_provider": [
                ["provider": "a", "cost_usd": 1], ["provider": "b", "cost_usd": 3],
                ["provider": "c", "cost_usd": 2], ["provider": "d", "cost_usd": 0.5],
            ],
        ])
        let k = TokenUsageKpis(summary)
        #expect(k.avgCost == 0.75)
        #expect(k.maxPlanFraction == 1)
        #expect(k.maxPlanLabel == "100% of $100 Claude Max budget")
        #expect(k.topProviders.map(\.provider) == ["b", "c", "a"])
        #expect(TokenUsageKpis(nil).avgCost == 0)
        #expect(TokenUsageKpis(UsageSummary(json: ["max_plan_estimate_usd": 12.5])).maxPlanLabel == "13% of $100 Claude Max budget")
    }

    @Test func spendTrendSumsPerProviderAndBucket() {
        let rows = [
            TimeseriesRow(json: ["provider": "openai", "bucket_start_ts": 2000, "cost_usd": 1]),
            TimeseriesRow(json: ["provider": "claude", "bucket_start_ts": 1000, "cost_usd": 2]),
            TimeseriesRow(json: ["provider": "openai", "bucket_start_ts": 1000, "cost_usd": 0.5]),
            TimeseriesRow(json: ["provider": "openai", "bucket_start_ts": 1000, "cost_usd": 0.25]),
        ]
        let trend = SpendTrend.points(rows)
        #expect(trend.providers == ["openai", "claude"])
        #expect(trend.points.map(\.cost) == [0.75, 1, 2])
        #expect(SpendTrend.axisLabel(0.5) == "$0.5000")
        #expect(SpendTrend.axisLabel(2) == "$2.00")
    }
}
