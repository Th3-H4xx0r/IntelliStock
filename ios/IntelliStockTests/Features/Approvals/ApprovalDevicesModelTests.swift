import Foundation
import Testing
@testable import IntelliStock

/// Registering this iPhone as a Jarvis approver: a new key, a Face ID
/// self-test, the public key posted, the record kept; and listing and
/// removing approver devices. No Secure Enclave, no prompt, no network.
@Suite(.serialized)
struct ApprovalDevicesModelTests {
    let clock = ApprovalTestClock(kT0)
    let base = "https://api.test"

    private func make(_ source: FakeApprovalsSource, signer: any ApprovalSigner = SoftwareApprovalSigner(storage: InMemorySecureStorage()),
                      store: ApprovalDeviceStore = ApprovalDeviceStore(storage: InMemorySecureStorage())) -> ApprovalDevicesModel {
        let clock = clock
        return ApprovalDevicesModel(source: { source }, signer: signer, store: store, baseURL: { "https://api.test" }, clock: { clock.now })
    }

    private func storedRecord(_ store: ApprovalDeviceStore, _ deviceId: String) throws -> ApprovalDeviceRecord {
        let record = ApprovalDeviceRecord(baseURL: base, deviceId: deviceId, name: "iPhone", activeAt: kT0 - 3600)
        try store.save(record)
        return record
    }

    @Test func registerCreatesAKeySelfTestsAndPostsThePublicKey() async throws {
        let source = FakeApprovalsSource()
        source.receipt = ApprovalDeviceReceipt(json: ["device_id": "dev_1", "active_at": "2026-11-19T06:10:00Z"])
        source.devicesList = [McpApprovalDevice(json: ["device_id": "dev_1", "name": "iPhone", "active_at": "2026-11-19T06:10:00Z"])]
        let signer = SoftwareApprovalSigner(storage: InMemorySecureStorage())
        let store = ApprovalDeviceStore(storage: InMemorySecureStorage())
        let m = make(source, signer: signer, store: store)
        #expect(await m.register(name: "  iPhone ") == nil)
        let call = try #require(source.registerCalls.first)
        #expect(call.name == "iPhone" && call.publicKey == signer.publicKey() && call.publicKey.count == 65)
        let record = try #require(store.record(for: base))
        #expect(record == ApprovalDeviceRecord(baseURL: base, deviceId: "dev_1", name: "iPhone", activeAt: kT0 + 600))
        #expect(m.status(now: kT0) == .activating(record))
        #expect(m.status(now: kT0 + 600) == .active(record))
        #expect(m.devices.value?.map(\.id) == ["dev_1"])
        #expect(!m.registering)
    }

    @Test func aServerWithoutApprovalsSaysSo() async {
        let source = FakeApprovalsSource()
        source.registerError = ApiError(message: "Not Found", statusCode: 404)
        source.devicesError = ApiError(message: "Not Found", statusCode: 404)
        let m = make(source)
        #expect(await m.register(name: "iPhone") == kApprovalsNotOnServer)
        await m.load()
        #expect(m.devices.error.map(approvalsErrorText) == kApprovalsNotOnServer)
    }

    @Test(arguments: [(572.2, "9:33"), (5.0, "0:05"), (0.4, "0:01"), (0.0, "0:00"), (-3.0, "0:00"), (3600.0, "60:00")])
    func countdownRoundsUpToWholeSeconds(remaining: Double, text: String) {
        #expect(approvalCountdown(remaining) == text)
    }

    @Test func aReceiptWithoutActiveAtAssumesTenMinutes() async throws {
        let source = FakeApprovalsSource()
        source.receipt = ApprovalDeviceReceipt(json: ["device_id": "dev_1"])
        let store = ApprovalDeviceStore(storage: InMemorySecureStorage())
        let m = make(source, store: store)
        #expect(await m.register(name: "iPhone") == nil)
        #expect(try #require(store.record(for: base)).activeAt == kT0 + 600)
    }

    @Test func registeringAgainRetiresTheOldDevice() async throws {
        let source = FakeApprovalsSource()
        source.receipt = ApprovalDeviceReceipt(json: ["device_id": "dev_2"])
        let store = ApprovalDeviceStore(storage: InMemorySecureStorage())
        _ = try storedRecord(store, "dev_old")
        let m = make(source, store: store)
        #expect(await m.register(name: "iPhone") == nil)
        #expect(source.deleteCalls == ["dev_old"])
        #expect(store.record(for: base)?.deviceId == "dev_2")
    }

    @Test func aFailedRegistrationLeavesNoKeyAndNoRecord() async {
        let source = FakeApprovalsSource()
        source.registerError = ApiError(message: "boom", statusCode: 500)
        let signer = SoftwareApprovalSigner(storage: InMemorySecureStorage())
        let store = ApprovalDeviceStore(storage: InMemorySecureStorage())
        let m = make(source, signer: signer, store: store)
        #expect(await m.register(name: "iPhone") == "boom")
        #expect(source.registerCalls.count == 1)
        #expect(signer.publicKey() == nil)
        #expect(store.record(for: base) == nil)
        #expect(m.status(now: kT0) == .notRegistered)
    }

    @Test func cancelledFaceIDRegistersNothing() async {
        let source = FakeApprovalsSource()
        let signer = FakeApprovalSigner()
        signer.authorizeError = .cancelled
        let store = ApprovalDeviceStore(storage: InMemorySecureStorage())
        let m = make(source, signer: signer, store: store)
        #expect(await m.register(name: "iPhone") == "Registration cancelled.")
        #expect(signer.reasons == ["Register this iPhone to approve Jarvis's requests"])
        #expect(source.registerCalls.isEmpty)
        #expect(signer.publicKey() == nil)
        #expect(store.record(for: base) == nil)
    }

    @Test func aKeyThatFailsItsSelfTestRegistersNothing() async {
        // The fake's fixed signature never verifies against its key.
        let source = FakeApprovalsSource()
        let signer = FakeApprovalSigner()
        let store = ApprovalDeviceStore(storage: InMemorySecureStorage())
        let m = make(source, signer: signer, store: store)
        #expect(await m.register(name: "iPhone") == "Couldn't use the new key: signature check failed")
        #expect(signer.messages == [ApprovalSigning.selfTestMessage])
        #expect(source.registerCalls.isEmpty)
        #expect(signer.publicKey() == nil)
        #expect(store.record(for: base) == nil)
    }

    @Test func removingThisIPhoneDeletesItsKeyAndRecord() async {
        let source = FakeApprovalsSource()
        source.receipt = ApprovalDeviceReceipt(json: ["device_id": "dev_1"])
        let signer = SoftwareApprovalSigner(storage: InMemorySecureStorage())
        let store = ApprovalDeviceStore(storage: InMemorySecureStorage())
        let m = make(source, signer: signer, store: store)
        #expect(await m.register(name: "iPhone") == nil)
        #expect(await m.remove("dev_1") == nil)
        #expect(source.deleteCalls == ["dev_1"])
        #expect(signer.publicKey() == nil)
        #expect(store.record(for: base) == nil)
        #expect(m.removing.isEmpty)
    }

    @Test func removingAnotherDeviceKeepsThisOne() async throws {
        let source = FakeApprovalsSource()
        let signer = SoftwareApprovalSigner(storage: InMemorySecureStorage())
        let key = try signer.createKey()
        let store = ApprovalDeviceStore(storage: InMemorySecureStorage())
        let record = try storedRecord(store, "dev_1")
        let m = make(source, signer: signer, store: store)
        #expect(await m.remove("dev_9") == nil)
        #expect(source.deleteCalls == ["dev_9"])
        #expect(store.record(for: base) == record)
        #expect(signer.publicKey() == key)
    }

    @Test func aFailedRemovalSaysWhyAndKeepsThisIPhone() async throws {
        let source = FakeApprovalsSource()
        source.deleteError = ApiError(message: "boom", statusCode: 500)
        let store = ApprovalDeviceStore(storage: InMemorySecureStorage())
        let record = try storedRecord(store, "dev_1")
        let m = make(source, store: store)
        #expect(await m.remove("dev_1") == "boom")
        #expect(store.record(for: base) == record)
        #expect(m.removing.isEmpty)
    }

    @Test func removingADeviceTheServerNoLongerHasStillForgetsIt() async throws {
        let source = FakeApprovalsSource()
        source.deleteError = ApiError(message: "Not Found", statusCode: 404)
        let signer = SoftwareApprovalSigner(storage: InMemorySecureStorage())
        _ = try signer.createKey()
        let store = ApprovalDeviceStore(storage: InMemorySecureStorage())
        _ = try storedRecord(store, "dev_1")
        let m = make(source, signer: signer, store: store)
        #expect(await m.remove("dev_1") == nil)
        #expect(signer.publicKey() == nil)
        #expect(store.record(for: base) == nil)
    }

    @Test func aRecordTheServerNoLongerHasIsFlagged() async throws {
        let source = FakeApprovalsSource()
        source.devicesList = [McpApprovalDevice(json: ["device_id": "dev_other", "name": "iPad"])]
        let store = ApprovalDeviceStore(storage: InMemorySecureStorage())
        let record = try storedRecord(store, "dev_1")
        let m = make(source, store: store)
        #expect(m.status(now: kT0) == .active(record))                  // nothing loaded yet
        await m.load()
        #expect(m.status(now: kT0) == .missingOnServer(record))
    }

    @Test func nameValidation() {
        #expect(approvalDeviceNameError("   ") == "Enter a name for this iPhone.")
        #expect(approvalDeviceNameError(String(repeating: "a", count: 65)) == "Use 64 characters or fewer.")
        #expect(approvalDeviceNameError(String(repeating: "a", count: 64)) == nil)
        #expect(approvalDeviceNameError(" iPhone ") == nil)
    }

    @Test func anInvalidNameIsRefusedBeforeAnyKeyIsMade() async {
        let source = FakeApprovalsSource()
        let signer = FakeApprovalSigner()
        let m = make(source, signer: signer)
        #expect(await m.register(name: "   ") == "Enter a name for this iPhone.")
        #expect(signer.reasons.isEmpty && signer.publicKey() == nil && source.registerCalls.isEmpty)
    }

    @Test func servicesBuildTheApprovalPieces() {
        let stub = DataStub()
        let services = AppServices(apiClient: stub.client)
        #expect(services.approvalDeviceStore.record(for: services.apiClient.baseURL) == nil)
        #expect(services.approvalsRepository.client.baseURL == services.apiClient.baseURL)
        #if targetEnvironment(simulator)
        #expect(!services.approvalSigner.isHardwareBacked)
        #endif
    }

    @Test func theDevicesRouteParsesAndPrints() {
        #expect(Route(path: "/settings/approval-devices") == .approvalDevices)
        #expect(Route.approvalDevices.path == "/settings/approval-devices")
    }
}
