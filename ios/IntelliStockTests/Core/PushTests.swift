import Foundation
import Testing
@testable import IntelliStock

/// Ported from test/core/push/push_repository_test.dart, plus the
/// `PushService` bridge the MethodChannel used to be.
@MainActor
struct PushRepositoryTests {
    private let stub = DataStub()

    private var repo: PushRepository {
        PushRepository(client: stub.client)
    }

    @Test func registerTokenPostsTheDevicePayload() async throws {
        stub.respond(json: "{}")
        try await repo.registerToken("TOKEN123", env: "sandbox", appVersion: "1.0.0")
        let req = try #require(stub.requests.first)
        #expect(req.httpMethod == "POST")
        #expect(req.url?.path == "/push/devices")
        #expect(req.jsonBody == ["device_token": "TOKEN123", "platform": "ios", "env": "sandbox", "app_version": "1.0.0"])
    }

    @Test func registerTokenOmitsAMissingAppVersion() async throws {
        stub.respond(json: "{}")
        try await repo.registerToken("T", env: "prod")
        #expect(stub.requests.first?.jsonBody == ["device_token": "T", "platform": "ios", "env": "prod"])
    }

    @Test func unregisterDeletesByToken() async throws {
        stub.respond(json: "{}")
        try await repo.unregister("TOKEN123")
        let req = try #require(stub.requests.first)
        #expect(req.httpMethod == "DELETE")
        #expect(req.url?.path == "/push/devices/TOKEN123")
        #expect(stub.requests.count == 1)
    }

    @Test func listDevicesParsesTheDevicesArray() async throws {
        stub.respond(json: #"""
        {"devices": [{"device_token": "abcdef0123456789", "platform": "ios", "env": "sandbox",
                      "app_version": "1.2.0", "last_seen": "2026-06-11T00:00:00Z"}, "junk"]}
        """#)
        let devices = try await repo.listDevices()
        #expect(stub.requests.first?.httpMethod == "GET")
        #expect(stub.requests.first?.url?.path == "/push/devices")
        #expect(devices.count == 1)
        #expect(devices.first?.platform == "ios")
        #expect(devices.first?.env == "sandbox")
        #expect(devices.first?.appVersion == "1.2.0")
        #expect(devices.first?.tokenSuffix == "…23456789")
    }

    @Test func listDevicesToleratesAnEmptyOrMissingArray() async throws {
        stub.respond(json: #"{"devices": []}"#)
        #expect(try await repo.listDevices().isEmpty)
        stub.respond(json: "{}")
        #expect(try await repo.listDevices().isEmpty)
    }

    @Test func devicesModelLoadsAndRefreshes() async {
        stub.respond(json: #"{"devices": [{"device_token": "x"}]}"#)
        let repo = repo
        let model = PushDevicesModel(repository: { repo })
        #expect(model.devices.isLoading)
        await model.load()
        #expect(model.devices.value?.map(\.deviceToken) == ["x"])
        stub.respond(status: 500)
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

@MainActor
struct PushServiceTests {
    private let stub = DataStub()

    private func service(_ registrar: FakePushRegistrar, shouldRegister: @escaping () -> Bool = { true }) -> PushService {
        let client = stub.client
        return PushService(
            repository: { PushRepository(client: client) },
            registrar: registrar,
            env: "sandbox",
            appVersion: "1.0.0",
            shouldRegister: shouldRegister
        )
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
        stub.respond(json: "{}")
        await service(FakePushRegistrar(grant: true)).didRegister(deviceToken: Data([0x0A, 0xFF, 0x01]))
        let req = try #require(stub.requests.first)
        #expect(req.url?.path == "/push/devices")
        #expect(req.jsonBody == ["device_token": "0aff01", "platform": "ios", "env": "sandbox", "app_version": "1.0.0"])
    }

    @Test func registrationFailuresAreSwallowed() async {
        stub.respond(status: 500)
        await service(FakePushRegistrar(grant: true)).register(token: "abc")
        await service(FakePushRegistrar(grant: true)).register(token: "")
        #expect(stub.requests.count == 1)
    }

    /// I1: nothing registers from behind the lock or while signed out.
    @Test func shouldRegisterGatesEnableAndTokens() async {
        let registrar = FakePushRegistrar(grant: true)
        var allowed = false
        let push = service(registrar, shouldRegister: { allowed })
        await push.enable()
        await push.register(token: "abc")
        #expect(registrar.authorizationRequests == 0)
        #expect(stub.requests.isEmpty)

        allowed = true
        await push.enable()
        await push.register(token: "abc")
        #expect(registrar.registrations == 1)
        #expect(stub.requests.count == 1)
    }

    @Test func debugBuildsUseTheSandbox() {
        #expect(PushService.defaultEnv == "sandbox")
    }
}
