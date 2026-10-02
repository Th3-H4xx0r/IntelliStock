import Foundation
import Observation

/// The logic of `connect_screen.dart`: validate the backend URL, probe
/// `GET {url}/health`, and save it — clearing the session when an existing
/// server changes. The view only lays it out.
@Observable
final class ConnectModel {
    /// What a save did, so the view knows whether to pop.
    enum Outcome: Equatable {
        /// First run: the gate moves on to Login by itself.
        case configured
        /// Edited from Settings, same server: return to Settings.
        case unchanged
        /// Edited from Settings, new server: signed out, back to Login.
        case changed
    }

    static let invalidMessage = "Enter a valid URL, e.g. https://your-instance.example.com"

    var url: String
    private(set) var error: String?
    private(set) var probing = false
    /// When true the button becomes "Save anyway".
    private(set) var probeFailed = false

    /// Reached from Settings with a server already set (Dart's `canPop`).
    let isEditing: Bool

    @ObservationIgnored private let services: AppServices
    @ObservationIgnored private let probe: (String) async -> Bool

    init(services: AppServices, probe: @escaping (String) async -> Bool = ConnectModel.makeHealthProbe()) {
        self.services = services
        self.probe = probe
        url = services.urlStore.baseUrl
        isEditing = services.urlStore.isConfigured
    }

    var buttonLabel: String { probeFailed ? "Save anyway" : "Test & Connect" }

    /// Typing clears the error and the "Save anyway" state.
    func textChanged() {
        if error != nil || probeFailed {
            error = nil
            probeFailed = false
        }
    }

    /// The primary button: "Test & Connect", or "Save anyway" after a failed
    /// probe.
    func submit() async -> Outcome? {
        // The keyboard's Go is not disabled like the button: guard here.
        guard !probing else { return nil }
        return probeFailed ? await save(url) : await testAndConnect()
    }

    func testAndConnect() async -> Outcome? {
        let raw = url
        guard isValidBaseUrl(raw) else {
            error = Self.invalidMessage
            probeFailed = false
            return nil
        }
        let target = normalizeBaseUrl(raw)
        error = nil
        probing = true
        probeFailed = false
        if await probe(target) {
            return await save(target)
        }
        probing = false
        probeFailed = true
        error = "Couldn't reach \(target)/health. Check the URL, or save anyway."
        return nil
    }

    func save(_ raw: String) async -> Outcome? {
        guard isValidBaseUrl(raw) else {
            error = Self.invalidMessage
            probing = false
            probeFailed = false
            return nil
        }
        let store = services.urlStore
        let wasConfigured = store.isConfigured
        let next = normalizeBaseUrl(raw)
        let changed = next != store.baseUrl
        do {
            try store.set(next)
        } catch {
            probing = false
            self.error = (error as? ApiError)?.message ?? "Something went wrong."
            return nil
        }
        probing = false
        // A different server invalidates the session. Reset the shell first,
        // so the next sign-in lands on the Dashboard (Dart: go('/login')).
        if wasConfigured, changed {
            services.router.reset()
            await services.session.clear()
        }
        guard isEditing else { return .configured }
        return changed ? .changed : .unchanged
    }

    /// `GET {url}/health` with 4 s timeouts; reachable only on HTTP 200.
    static func makeHealthProbe(session: URLSession? = nil) -> (String) async -> Bool {
        let session = session ?? {
            let config = URLSessionConfiguration.ephemeral
            config.timeoutIntervalForRequest = 4
            config.timeoutIntervalForResource = 8
            config.waitsForConnectivity = false
            return URLSession(configuration: config)
        }()
        return { base in
            guard let url = URL(string: base + "/health") else { return false }
            var request = URLRequest(url: url)
            request.timeoutInterval = 4
            request.setValue("application/json", forHTTPHeaderField: "Accept")
            do {
                let (_, response) = try await session.data(for: request)
                return (response as? HTTPURLResponse)?.statusCode == 200
            } catch {
                return false
            }
        }
    }
}
