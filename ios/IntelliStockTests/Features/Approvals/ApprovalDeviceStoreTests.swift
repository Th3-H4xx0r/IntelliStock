import Foundation
import Testing
@testable import IntelliStock

/// The keychain record of this iPhone's registered approval device.
struct ApprovalDeviceStoreTests {
    static let record = ApprovalDeviceRecord(
        baseURL: "https://api.test", deviceId: "dev_1", name: "iPhone",
        activeAt: Date(timeIntervalSince1970: 1_795_068_600)
    )

    @Test func readsTheRecordOnlyForItsServer() throws {
        let store = ApprovalDeviceStore(storage: InMemorySecureStorage())
        try store.save(Self.record)
        #expect(store.record(for: "https://api.test") == Self.record)
        #expect(store.record(for: "https://other.test") == nil)
    }

    @Test func clearRemovesItAndJunkReadsAsNone() throws {
        let storage = InMemorySecureStorage()
        let store = ApprovalDeviceStore(storage: storage)
        try store.save(Self.record)
        store.clear()
        #expect(store.record(for: "https://api.test") == nil)
        try storage.write(ApprovalDeviceStore.storageKey, "not json")
        #expect(store.record(for: "https://api.test") == nil)
    }
}
