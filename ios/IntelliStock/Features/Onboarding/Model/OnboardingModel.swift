import Foundation
import Observation

/// Step identifiers for the onboarding wizard — `OnboardingStep`.
nonisolated enum OnboardingStep: Int, CaseIterable, Sendable {
    case welcome, about, addModel, linkBrokerage, createInstance, connect, complete
}

nonisolated enum OnboardingDirection: Sendable {
    case forward, back
}

/// `OnboardingState` in `onboarding_controller.dart`. Counts are refreshed as
/// resources are created, so the Complete step shows live numbers.
nonisolated struct OnboardingState: Equatable, Sendable {
    var stepIndex = 0
    var direction: OnboardingDirection = .forward
    var busy = false
    var error: String?
    var modelCount = 0
    var brokerageCount = 0
    var instanceCount = 0

    static let steps = OnboardingStep.allCases

    var currentStep: OnboardingStep { Self.steps[stepIndex] }
    var isFirstStep: Bool { stepIndex == 0 }
    var isLastStep: Bool { stepIndex == Self.steps.count - 1 }

    /// Step labels in the progress header (`_stepLabels`).
    static let labels = ["Welcome", "About", "Model", "Brokerage", "Instance", "Connect", "Done"]

    /// `_isSkippable`: the four resource steps.
    static func isSkippable(_ index: Int) -> Bool { index >= 2 && index <= 5 }
}

/// Drives the 7-step onboarding wizard — `OnboardingController`.
@Observable
final class OnboardingModel {
    private(set) var state = OnboardingState()

    @ObservationIgnored private let repository: () -> OnboardingRepository
    @ObservationIgnored private let session: SessionStore

    init(repository: @escaping () -> OnboardingRepository, session: SessionStore) {
        self.repository = repository
        self.session = session
    }

    /// Advance to the next step.
    func next() {
        if state.isLastStep { return }
        state.stepIndex += 1
        state.direction = .forward
        state.error = nil
    }

    /// Go back one step.
    func back() {
        if state.isFirstStep { return }
        state.stepIndex -= 1
        state.direction = .back
        state.error = nil
    }

    /// Skip this step (same as next; only offered on skippable steps).
    func skip() { next() }

    /// Update counts after a resource is created inside a step.
    func updateCounts(models: Int? = nil, brokerages: Int? = nil, instances: Int? = nil) {
        state.modelCount = models ?? state.modelCount
        state.brokerageCount = brokerages ?? state.brokerageCount
        state.instanceCount = instances ?? state.instanceCount
    }

    /// Loads the counts from `GET /onboarding/state` (on mount). Best-effort.
    func loadState() async {
        do {
            let data = try await repository().state()
            let counts = data["counts"] ?? .null
            state.modelCount = counts["models"].int ?? 0
            state.brokerageCount = counts["brokerages"].int ?? 0
            state.instanceCount = counts["instances"].int ?? 0
        } catch {
            // Counts default to 0.
        }
    }

    /// `POST /onboarding/complete`, then the session's user is updated (which
    /// moves the gate on). Returns true on success.
    func finish() async -> Bool {
        state.busy = true
        state.error = nil
        do {
            let data = try await repository().complete()
            if let user = data["user"], user.isObject {
                try await session.setUser(user)
            }
            state.busy = false
            return true
        } catch is CancellationError {
            state.busy = false
            return false
        } catch {
            state.busy = false
            state.error = onboardingErrorText(error)
            return false
        }
    }
}

/// Dart's `e.toString()` for a caught error (`ApiError.toString()` is its
/// message).
nonisolated func onboardingErrorText(_ error: any Error) -> String {
    (error as? ApiError)?.message ?? error.localizedDescription
}

// MARK: - Step forms (the steps' screen-local state)

/// `_StepAddModelState`: the lean inline model form.
@Observable
final class OnboardingAddModelForm {
    static let providers = ["gemini", "openai", "azure", "nvidia", "ollama", "bedrock", "claude-cli", "codex-cli"]

    var name = ""
    var model = ""
    var apiKey = ""
    var provider = "gemini"
    private(set) var busy = false
    private(set) var message: String?
    private(set) var messageOk = false
    /// Models saved this session.
    private(set) var saved: [JSON] = []

    var canSubmit: Bool {
        !name.trimmed.isEmpty && !model.trimmed.isEmpty
    }

    /// `_testAndSave`: `POST /models`. Calls `onSaved` after a success (the
    /// controller's count update).
    func testAndSave(client: ApiClient, onSaved: () -> Void) async {
        guard canSubmit else {
            message = "Name and model are required."
            messageOk = false
            return
        }
        busy = true
        message = "Saving model…"
        messageOk = false
        var body: JSONObject = [
            "name": .string(name.trimmed),
            "provider": .string(provider),
            "model": .string(model.trimmed),
        ]
        let key = apiKey.trimmed
        if !key.isEmpty { body["api_key"] = .string(key) }
        do {
            let data = try await client.post("/models", body: .object(body))
            let savedName = data["name"].or(body["name"] ?? .null).dartDescription
            saved.append(data)
            message = "Model \"\(savedName)\" saved."
            messageOk = true
            busy = false
            name = ""
            model = ""
            apiKey = ""
            provider = "gemini"
            onSaved()
        } catch is CancellationError {
            busy = false
        } catch {
            message = onboardingErrorText(error)
            messageOk = false
            busy = false
        }
    }
}

/// `_StepLinkBrokerageState`: the minimal inline Alpaca form.
@Observable
final class OnboardingLinkBrokerageForm {
    var accountName = ""
    var apiKey = ""
    var apiSecret = ""
    var paper = true
    private(set) var busy = false
    private(set) var message: String?
    private(set) var messageOk = false
    private(set) var saved: [JSON] = []

    /// `_save`: `POST /brokerages`.
    func save(client: ApiClient, onSaved: () -> Void) async {
        if accountName.trimmed.isEmpty || apiKey.trimmed.isEmpty || apiSecret.trimmed.isEmpty {
            message = "Name, API key, and secret are required."
            messageOk = false
            return
        }
        busy = true
        message = "Saving brokerage…"
        messageOk = false
        let body: JSONObject = [
            "account_name": .string(accountName.trimmed),
            "brokerage_type": "alpaca",
            "api_key": .string(apiKey.trimmed),
            "api_secret": .string(apiSecret.trimmed),
            "paper": .bool(paper),
        ]
        do {
            let data = try await client.post("/brokerages", body: .object(body))
            let savedName = data["account_name"].or(body["account_name"] ?? .null).dartDescription
            saved.append(data)
            message = "Brokerage \"\(savedName)\" linked."
            messageOk = true
            busy = false
            accountName = ""
            apiKey = ""
            apiSecret = ""
            paper = true
            onSaved()
        } catch is CancellationError {
            busy = false
        } catch {
            message = onboardingErrorText(error)
            messageOk = false
            busy = false
        }
    }
}

/// `_StepCreateInstanceState`: the minimal instance form.
@Observable
final class OnboardingCreateInstanceForm {
    /// Cadence chips and the backend enum value of each.
    static let cadences = ["1min", "5min", "15min", "1hr"]
    static let cadenceValues = ["1m", "5m", "15m", "1h"]

    var instanceId = "" {
        didSet { validateId(instanceId) }
    }
    var name = ""
    var cadence = "5min"
    private(set) var busy = false
    private(set) var message: String?
    private(set) var messageOk = false
    private(set) var idError: String?
    private(set) var saved: [JSON] = []

    /// `^[a-z0-9_-]+$`.
    static func isValidId(_ id: String) -> Bool {
        !id.isEmpty && id.unicodeScalars.allSatisfy { scalar in
            ("a"..."z").contains(scalar) || ("0"..."9").contains(scalar) || scalar == "_" || scalar == "-"
        }
    }

    /// `_validateId` (on every change).
    func validateId(_ value: String) {
        if value.isEmpty {
            idError = nil
        } else if !Self.isValidId(value) {
            idError = "Only lowercase letters, digits, - and _ allowed."
        } else {
            idError = nil
        }
    }

    var canSubmit: Bool {
        !instanceId.trimmed.isEmpty && !name.trimmed.isEmpty && idError == nil
    }

    /// `_create`: `POST /instances`.
    func create(client: ApiClient, onSaved: () -> Void) async {
        guard canSubmit else { return }
        busy = true
        message = "Creating instance…"
        messageOk = false
        let index = Self.cadences.firstIndex(of: cadence)
        let cadenceValue = index.map { Self.cadenceValues[$0] } ?? "5m"
        let body: JSONObject = [
            "instance_id": .string(instanceId.trimmed),
            "name": .string(name.trimmed),
            "cadence": .string(cadenceValue),
        ]
        do {
            let data = try await client.post("/instances", body: .object(body))
            let savedName = data["name"].or(body["name"] ?? .null).dartDescription
            saved.append(data)
            message = "Instance \"\(savedName)\" created."
            messageOk = true
            busy = false
            instanceId = ""
            name = ""
            cadence = "5min"
            idError = nil
            onSaved()
        } catch is CancellationError {
            busy = false
        } catch {
            message = onboardingErrorText(error)
            messageOk = false
            busy = false
        }
    }
}

/// `_StepConnectState`: load instances + brokerages, pick one of each, link.
@Observable
final class OnboardingConnectForm {
    private(set) var instances: [JSON] = []
    private(set) var brokerages: [JSON] = []
    private(set) var loading = true
    private(set) var loadError: String?
    var selectedInstance: String?
    var selectedBrokerage: String?
    private(set) var busy = false
    private(set) var message: String?
    private(set) var messageOk = false

    /// An instance's id (`instance_id ?? id`).
    static func instanceId(_ instance: JSON) -> String? {
        instance["instance_id"].string ?? instance["id"].string
    }

    /// `(name ?? instance_id ?? id).toString()`.
    static func instanceLabel(_ instance: JSON) -> String {
        instance["name"].or(instance["instance_id"]).or(instance["id"]).dartDescription
    }

    /// `'${account_name} (${brokerage_type})'`.
    static func brokerageLabel(_ brokerage: JSON) -> String {
        "\(brokerage["account_name"].dartDescription) (\(brokerage["brokerage_type"].dartDescription))"
    }

    /// `GET /instances` and `GET /brokerages` together.
    func load(client: ApiClient) async {
        loading = true
        loadError = nil
        do {
            async let instanceData = client.get("/instances")
            async let brokerageData = client.get("/brokerages")
            let (instData, brData) = try await (instanceData, brokerageData)
            let insts = instData["instances"].or(instData["items"]).objectElements
            let brs = brData["accounts"].or(brData["items"]).objectElements
            instances = insts
            brokerages = brs
            loading = false
            if insts.count == 1 { selectedInstance = Self.instanceId(insts[0]) }
            if brs.count == 1 { selectedBrokerage = brs[0]["id"].string }
        } catch is CancellationError {
            // The step went away; nothing to report.
        } catch {
            loading = false
            loadError = onboardingErrorText(error)
        }
    }

    /// `POST /instances/{id}/link-brokerage`.
    func link(client: ApiClient) async {
        guard let instance = selectedInstance, let brokerage = selectedBrokerage else {
            message = "Select both an instance and a brokerage."
            messageOk = false
            return
        }
        busy = true
        message = "Linking…"
        messageOk = false
        do {
            _ = try await client.post(
                "/instances/\(dartEncodeComponent(instance))/link-brokerage",
                body: ["brokerage_id": .string(brokerage)]
            )
            busy = false
            message = "Linked! Your instance can now place orders through this brokerage."
            messageOk = true
        } catch is CancellationError {
            busy = false
        } catch {
            message = onboardingErrorText(error)
            messageOk = false
            busy = false
        }
    }
}

private extension String {
    nonisolated var trimmed: String { trimmingCharacters(in: .whitespacesAndNewlines) }
}
