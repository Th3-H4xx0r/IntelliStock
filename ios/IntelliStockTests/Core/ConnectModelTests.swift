import Foundation
import Testing
@testable import IntelliStock

/// The behaviour of connect_screen.dart (and its widget test), on the model.
@Suite(.serialized)
@MainActor
struct ConnectModelTests {
    init() { StubURLProtocol.reset() }

    private func services(_ initial: [String: String]) -> AppServices {
        AppServices(
            storage: InMemorySecureStorage(initial),
            biometrics: FakeBiometrics(available: false, authResult: false),
            widgetSync: WidgetSyncProbe().sync,
            urlSession: StubURLProtocol.session,
            pushRegistrar: FakePushRegistrar(grant: false)
        )
    }

    private static let signedIn: [String: String] = [
        ApiBaseUrlStore.storageKey: "https://old.example.test",
        SessionStore.tokenKey: "jwt",
        SessionStore.userKey: #"{"has_completed_onboarding": true}"#,
    ]

    /// connect_screen_test.dart: an invalid URL shows an inline error and
    /// does not save.
    @Test func invalidUrlShowsAnInlineErrorAndDoesNotSave() async {
        let services = services([:])
        var probed = false
        let model = ConnectModel(services: services, probe: { _ in probed = true; return true })
        model.url = "notaurl"
        let outcome = await model.submit()
        #expect(outcome == nil)
        #expect(model.error?.contains("valid URL") == true)
        #expect(model.error == "Enter a valid URL, e.g. https://your-instance.example.com")
        #expect(!probed)
        #expect(!services.urlStore.isConfigured)
    }

    @Test func firstRunProbesThenSavesTheNormalizedOrigin() async {
        let services = services([:])
        var probedUrl: String?
        let model = ConnectModel(services: services, probe: { probedUrl = $0; return true })
        #expect(!model.isEditing)
        #expect(model.url == "")
        model.url = " https://new.example.test/api/ "
        let outcome = await model.submit()
        #expect(outcome == .configured)
        #expect(probedUrl == "https://new.example.test")
        #expect(services.urlStore.baseUrl == "https://new.example.test")
        #expect(services.apiClient.baseURL == "https://new.example.test")
        #expect(!model.probing)
    }

    @Test func anUnreachableServerOffersSaveAnyway() async {
        let services = services([:])
        let model = ConnectModel(services: services, probe: { _ in false })
        model.url = "https://down.example.test"
        #expect(model.buttonLabel == "Test & Connect")
        #expect(await model.submit() == nil)
        #expect(model.probeFailed)
        #expect(model.error == "Couldn't reach https://down.example.test/health. Check the URL, or save anyway.")
        #expect(model.buttonLabel == "Save anyway")
        #expect(!services.urlStore.isConfigured)

        #expect(await model.submit() == .configured)
        #expect(services.urlStore.baseUrl == "https://down.example.test")
    }

    @Test func typingClearsTheErrorAndSaveAnyway() async {
        let model = ConnectModel(services: services([:]), probe: { _ in false })
        model.url = "https://down.example.test"
        _ = await model.submit()
        model.textChanged()
        #expect(model.error == nil)
        #expect(!model.probeFailed)
        #expect(model.buttonLabel == "Test & Connect")
    }

    @Test func editingWithTheSameServerKeepsTheSession() async {
        let services = services(Self.signedIn)
        let model = ConnectModel(services: services, probe: { _ in true })
        #expect(model.isEditing)
        #expect(model.url == "https://old.example.test")
        #expect(await model.submit() == .unchanged)
        #expect(services.session.isAuthenticated)
    }

    /// Review Focus 5: a new server clears the session, resets the shell and
    /// points the client at the new origin.
    @Test func editingToANewServerSignsOutOnAFreshShell() async {
        let services = services(Self.signedIn)
        services.router.tab = .more
        services.router.push(.settings)
        services.router.push(.connect)
        let model = ConnectModel(services: services, probe: { _ in true })
        model.url = "https://new.example.test"
        #expect(await model.submit() == .changed)
        #expect(!services.session.isAuthenticated)
        #expect(services.router.tab == .dashboard)
        #expect(services.router.stack(for: .more).isEmpty)
        #expect(services.apiClient.baseURL == "https://new.example.test")
    }

    @Test func healthProbeRequiresA200() async throws {
        let probe = ConnectModel.makeHealthProbe(session: StubURLProtocol.session)
        StubURLProtocol.respond(status: 200, json: #"{"status": "ok"}"#)
        #expect(await probe("https://api.example.test"))
        let req = try #require(StubURLProtocol.requests.first)
        #expect(req.url?.absoluteString == "https://api.example.test/health")
        #expect(req.value(forHTTPHeaderField: "Accept") == "application/json")
        #expect(req.value(forHTTPHeaderField: "Authorization") == nil)

        StubURLProtocol.respond(status: 503)
        #expect(await !probe("https://api.example.test"))
        StubURLProtocol.fail(.cannotConnectToHost)
        #expect(await !probe("https://api.example.test"))
    }
}
