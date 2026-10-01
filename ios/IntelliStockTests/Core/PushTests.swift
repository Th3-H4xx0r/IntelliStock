import Foundation
import Testing
@testable import IntelliStock

/// Ported from test/core/push/push_repository_test.dart, plus the
/// `PushService` bridge the MethodChannel used to be.
@Suite(.serialized)
@MainActor
struct PushRepositoryTests {
    init() { StubURLProtocol.reset() }

    private var repo: PushRepository {
        PushRepository(client: ApiClient(baseURL: "https://api.example.test", tokens: nil, session: StubURLProtocol.session))
    }

    @Test func registerTokenPostsTheDevicePayload() async throws {
        StubURLProtocol.respond(json: "{}")
        try await repo.registerToken("TOKEN123", env: "sandbox", appVersion: "1.0.0")
        let req = try #require(StubURLProtocol.requests.first)
        #expect(req.httpMethod == "POST")
        #expect(req.url?.path == "/push/devices")
        #expect(req.jsonBody == ["device_token": "TOKEN123", "platform": "ios", "env": "sandbox", "app_version": "1.0.0"])
    }

    @Test func registerTokenOmitsAMissingAppVersion() async throws {
        StubURLProtocol.respond(json: "{}")
        try await repo.registerToken("T", env: "prod")
        #expect(StubURLProtocol.requests.first?.jsonBody == ["device_token": "T", "platform": "ios", "env": "prod"])
    }

    @Test func unregisterDeletesByToken() async throws {
        StubURLProtocol.respond(json: "{}")
        try await repo.unregister("TOKEN123")
        let req = try #require(StubURLProtocol.requests.first)
        #expect(req.httpMethod == "DELETE")
        #expect(req.url?.path == "/push/devices/TOKEN123")
        #expect(StubURLProtocol.requests.count == 1)
    }

    @Test func listDevicesParsesTheDevicesArray() async throws {
        StubURLProtocol.respond(json: #"""
        {"devices": [{"device_token": "abcdef0123456789", "platform": "ios", "env": "sandbox",
                      "app_version": "1.2.0", "last_seen": "2026-06-11T00:00:00Z"}, "junk"]}
        """#)
        let devices = try await repo.listDevices()
        #expect(StubURLProtocol.requests.first?.httpMethod == "GET")
        #expect(StubURLProtocol.requests.first?.url?.path == "/push/devices")
        #expect(devices.count == 1)
        #expect(devices.first?.platform == "ios")
        #expect(devices.first?.env == "sandbox")
        #expect(devices.first?.appVersion == "1.2.0")
        #expect(devices.first?.tokenSuffix == "…23456789")
    }

    @Test func listDevicesToleratesAnEmptyOrMissingArray() async throws {
        StubURLProtocol.respond(json: #"{"devices": []}"#)
        #expect(try await repo.listDevices().isEmpty)
        StubURLProtocol.respond(json: "{}")
        #expect(try await repo.listDevices().isEmpty)
    }

    @Test func devicesModelLoadsAndRefreshes() async {
        StubURLProtocol.respond(json: #"{"devices": [{"device_token": "x"}]}"#)
        let repo = repo
        let model = PushDevicesModel(repository: { repo })
        #expect(model.devices.isLoading)
        await model.load()
        #expect(model.devices.value?.map(\.deviceToken) == ["x"])
        StubURLProtocol.respond(status: 500)
        await model.refresh()
        #expect(model.devices.error != nil)
    }
}

struct PushDeviceTests {
    @Test func fromJsonAndTokenSuffixMasksLongTokens() {
        let d = PushDevice(json: ["device_token": "0123456789abcdef", "platform": "ios", "env": "prod"])
        #expect(d.tokenSuffix == "…89abcdef")
        #expect(d.env == "prod")
    }

    @Test func shortTokenIsShownAsIs() {
        let d = PushDevice(json: ["device_token": "short", "platform": "ios", "env": "prod"])
        #expect(d.tokenSuffix == "short")
    }

    @Test func defaultsMatchDart() {
        let d = PushDevice(json: [:])
        #expect(d.deviceToken == "")
        #expect(d.platform == "ios")
        #expect(d.env == "prod")
        #expect(d.appVersion == nil)
        #expect(d.lastSeen == nil)
    }
}

@Suite(.serialized)
@MainActor
struct PushServiceTests {
    init() { StubURLProtocol.reset() }

    private func service(_ registrar: FakePushRegistrar) -> PushService {
        let client = ApiClient(baseURL: "https://api.example.test", tokens: nil, session: StubURLProtocol.session)
        return PushService(repository: { PushRepository(client: client) }, registrar: registrar, env: "sandbox", appVersion: "1.0.0")
    }

    @Test func enableRegistersWhenGranted() async {
        let registrar = FakePushRegistrar(grant: true)
        await service(registrar).enable()
        #expect(registrar.authorizationRequests == 1)
        #expect(registrar.registrations == 1)
    }

    @Test func enableDoesNotRegisterWhenDenied() async {
        let registrar = FakePushRegistrar(grant: false)
        await service(registrar).enable()
        #expect(registrar.registrations == 0)
    }

    @Test func aDeviceTokenIsForwardedAsHex() async throws {
        StubURLProtocol.respond(json: "{}")
        await service(FakePushRegistrar(grant: true)).didRegister(deviceToken: Data([0x0A, 0xFF, 0x01]))
        let req = try #require(StubURLProtocol.requests.first)
        #expect(req.url?.path == "/push/devices")
        #expect(req.jsonBody == ["device_token": "0aff01", "platform": "ios", "env": "sandbox", "app_version": "1.0.0"])
    }

    @Test func registrationFailuresAreSwallowed() async {
        StubURLProtocol.respond(status: 500)
        await service(FakePushRegistrar(grant: true)).register(token: "abc")
        await service(FakePushRegistrar(grant: true)).register(token: "")
        #expect(StubURLProtocol.requests.count == 1)
    }

    @Test func debugBuildsUseTheSandbox() {
        #expect(PushService.defaultEnv == "sandbox")
    }
}
