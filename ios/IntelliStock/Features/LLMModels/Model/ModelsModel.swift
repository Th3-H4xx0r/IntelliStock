import Foundation
import Observation

/// Dart's `e.toString()` for a caught error (`ApiError.toString()` is its message).
nonisolated func llmErrorText(_ error: any Error) -> String {
    (error as? ApiError)?.message ?? error.localizedDescription
}

/// The saved LLM models — `ModelsController` (auto-disposed; the screen owns it).
@Observable
final class ModelsModel {
    private(set) var models: Loadable<[LlmModel]> = .loading
    /// Per-row CLI test lines (`_ModelCardState`): id → (testing, ok, message).
    private(set) var cliTests: [String: CliTest] = [:]

    nonisolated struct CliTest: Equatable, Sendable {
        var testing = false
        var ok: Bool?
        var message: String?
    }

    @ObservationIgnored private let repository: () -> ModelRepository

    init(repository: @escaping () -> ModelRepository) {
        self.repository = repository
    }

    func load() async {
        let result = await Loadable.capture { try await self.repository().list() }
        if case .failed(let error) = result, error is CancellationError { return }
        models = result
    }

    func refresh() async {
        models = .loading
        await load()
    }

    /// `DELETE /models/:id` then refresh; throws so the confirmation can
    /// report `Delete failed: …`.
    func delete(_ id: String) async throws {
        try await repository().delete(id)
        await refresh()
    }

    /// `_testCli`: `POST /models/:id/test-cli`.
    func testCli(_ id: String) async {
        cliTests[id] = CliTest(testing: true, ok: nil, message: "Testing…")
        do {
            let result = LlmModelCells.cliTestMessage(try await repository().testCli(id))
            cliTests[id] = CliTest(testing: false, ok: result.ok, message: result.message)
        } catch {
            cliTests[id] = CliTest(testing: false, ok: false, message: llmErrorText(error))
        }
    }
}

/// The Add / Edit sheet — `_AddEditSheetState`.
@Observable
final class ModelEditorModel {
    let existing: LlmModel?
    var name: String
    var draft: LlmConfigDraft
    var inputCost = ""
    var outputCost = ""
    var cacheCreationCost = ""
    var cacheReadCost = ""
    private(set) var submitting = false
    private(set) var statusMsg = ""
    private(set) var statusOk = false
    private(set) var saved = false
    private(set) var testResult: LlmTestResult?

    var isEdit: Bool { existing != nil }

    @ObservationIgnored private let repository: () -> ModelRepository

    init(existing: LlmModel?, repository: @escaping () -> ModelRepository) {
        self.existing = existing
        self.repository = repository
        if let e = existing {
            name = e.name
            draft = LlmConfigDraft(editing: e)
            if let v = e.inputCostPer1m { inputCost = JSON.dartDoubleString(v) }
            if let v = e.outputCostPer1m { outputCost = JSON.dartDoubleString(v) }
            if let v = e.cacheCreationCostPer1m { cacheCreationCost = JSON.dartDoubleString(v) }
            if let v = e.cacheReadCostPer1m { cacheReadCost = JSON.dartDoubleString(v) }
        } else {
            name = ""
            draft = LlmConfigDraft()
        }
    }

    /// `_buildPayload`: the draft, the name and the valid pricing overrides.
    func buildPayload() -> JSONObject {
        var p = draft.toPayload()
        p["name"] = .string(name.trimmingCharacters(in: .whitespacesAndNewlines))
        if let v = JSON.parseDouble(inputCost) { p["input_cost_per_1m"] = .double(v) }
        if let v = JSON.parseDouble(outputCost) { p["output_cost_per_1m"] = .double(v) }
        if let v = JSON.parseDouble(cacheCreationCost) { p["cache_creation_cost_per_1m"] = .double(v) }
        if let v = JSON.parseDouble(cacheReadCost) { p["cache_read_cost_per_1m"] = .double(v) }
        return p
    }

    private func status(_ message: String, ok: Bool = false) {
        statusMsg = message
        statusOk = ok
    }

    /// `_testOnly`: `POST /llm/test` without saving.
    func testOnly() async {
        if draft.model.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            status("Model is required")
            return
        }
        if draft.provider == "claude-cli" {
            status("Use the cable icon on the saved row to test a claude-cli model.")
            return
        }
        submitting = true
        testResult = nil
        status("Testing LLM configuration…")
        defer { submitting = false }
        do {
            testResult = try await repository().testLlm(draft.toTestPayload())
            status("LLM test passed (not saved).", ok: true)
        } catch {
            status("LLM test failed: \(llmErrorText(error))")
        }
    }

    /// `_testAndSave`: test (where it can), then create or update; claude-cli
    /// runs the real CLI test against the saved model instead.
    func testAndSave() async {
        if name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            status("Name is required")
            return
        }
        if draft.model.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            status("Model is required")
            return
        }
        submitting = true
        testResult = nil
        statusOk = false
        defer { submitting = false }

        let skipTest = draft.provider == "claude-cli"
        let hasKey = !draft.apiKey.isEmpty
        let shouldTest = !skipTest && (draft.provider == "codex-cli" || draft.provider == "ollama" || hasKey)

        if shouldTest {
            statusMsg = "Testing LLM configuration…"
            do {
                testResult = try await repository().testLlm(draft.toTestPayload())
            } catch {
                status("LLM test failed: \(llmErrorText(error))")
                return
            }
        }

        statusMsg = existing != nil ? "Updating model…" : "Saving model…"
        do {
            let payload = buildPayload()
            let repo = repository()
            let savedModel: LlmModel
            if let existing {
                savedModel = try await repo.update(existing.id, payload)
            } else {
                savedModel = try await repo.create(payload)
            }

            if draft.provider == "claude-cli" {
                saved = true
                statusMsg = "Testing Claude CLI connection…"
                do {
                    let data = JSON.object(try await repo.testCli(savedModel.id))
                    if data["ok"].boolValue ?? false {
                        status("Model saved. Claude CLI connection OK (logged in).", ok: true)
                    } else {
                        status("Model saved, but Claude CLI test failed: \(data["error"].string ?? "unknown error")")
                    }
                } catch {
                    // The model is already saved — don't fail the save itself.
                    status("Model saved, but Claude CLI test failed: \(llmErrorText(error))")
                }
            } else {
                saved = true
                status(existing != nil ? "LLM test passed. Model updated." : "LLM test passed. Model saved.", ok: true)
            }
        } catch {
            status(llmErrorText(error))
        }
    }

    /// The primary button's label.
    var primaryLabel: String {
        if submitting {
            return draft.provider == "claude-cli" ? (isEdit ? "Updating…" : "Saving…") : "Testing…"
        }
        return isEdit ? "Test & Update" : "Test & Save"
    }
}

/// The form's live pickers — the Ollama, Bedrock and Claude model lists in
/// `_LlmConfigFormState`.
@Observable
final class LlmPickersModel {
    private(set) var ollamaModels: [JSONObject] = []
    private(set) var ollamaLoading = false
    private(set) var ollamaError = ""

    private(set) var bedrockModels: [JSONObject] = []
    private(set) var bedrockLoading = false
    private(set) var bedrockError = ""

    private(set) var claudeModels: [ClaudeModelOption] = []
    private(set) var claudeLoading = false
    private(set) var claudeError = ""
    /// The free-text field stays revealed once "Custom…" is chosen (or the
    /// saved model isn't in the list).
    var claudeCustom = false

    @ObservationIgnored private let repository: () -> ModelRepository

    init(repository: @escaping () -> ModelRepository) {
        self.repository = repository
    }

    /// `_fetchOllama`: needs a base URL.
    func fetchOllama(_ draft: LlmConfigDraft, force: Bool = false) async {
        let baseUrl = draft.ollamaBaseUrl
        if baseUrl.isEmpty { return }
        ollamaLoading = true
        ollamaError = ""
        var body: JSONObject = ["base_url": .string(baseUrl)]
        if !draft.apiKey.isEmpty { body["api_key"] = .string(draft.apiKey) }
        if force { body["force"] = true }
        do {
            ollamaModels = try await repository().ollamaModels(body)
            ollamaLoading = false
        } catch {
            if error is CancellationError { ollamaLoading = false; return }
            ollamaError = llmErrorText(error)
            ollamaLoading = false
        }
    }

    /// `_fetchBedrock`: needs a region and a key.
    func fetchBedrock(_ draft: LlmConfigDraft, force: Bool = false) async {
        let region = draft.bedrockRegion
        let key = draft.apiKey
        if region.isEmpty || key.isEmpty { return }
        bedrockLoading = true
        bedrockError = ""
        var body: JSONObject = ["region": .string(region), "api_key": .string(key)]
        if force { body["force"] = true }
        do {
            bedrockModels = try await repository().bedrockModels(body)
            bedrockLoading = false
        } catch {
            if error is CancellationError { bedrockLoading = false; return }
            bedrockError = llmErrorText(error)
            bedrockLoading = false
        }
    }

    /// `_fetchClaudeModels`.
    func fetchClaudeModels(_ draft: LlmConfigDraft) async {
        claudeLoading = true
        claudeError = ""
        do {
            let data = JSON.object(try await repository().claudeModels(cliPath: draft.cliPath))
            let models = data["models"].isArray
                ? data["models"].objectElements.map(ClaudeModelOption.init(json:)).filter { !$0.value.isEmpty }
                : []
            claudeModels = models
            if case .string(let err) = data["error"], !err.isEmpty {
                claudeError = err
            } else {
                claudeError = models.isEmpty ? "No models returned" : ""
            }
            let current = draft.model
            claudeCustom = !current.isEmpty && !models.contains { $0.value == current }
            claudeLoading = false
        } catch {
            if error is CancellationError { claudeLoading = false; return }
            claudeError = llmErrorText(error)
            claudeModels = []
            claudeLoading = false
        }
    }

    /// The Claude picker's selection: a known model, `Custom…`, or none.
    func claudeSelection(_ draft: LlmConfigDraft) -> String? {
        let known = claudeModels.contains { $0.value == draft.model }
        let useCustom = claudeCustom || (!draft.model.isEmpty && !known)
        return useCustom ? LlmOptions.claudeCustom : (known ? draft.model : nil)
    }
}
