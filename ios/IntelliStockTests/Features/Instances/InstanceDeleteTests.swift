import Foundation
import SwiftUI
import Testing
@testable import IntelliStock

/// The one instance delete, used by the list and by Instance detail's
/// Delete Instance: the same confirmation, the same request, and a result
/// the detail screen reads to decide whether to go back.
struct InstanceDeleteTests {
    private func listStub(after: String = #"{"instances": [{"id": "i2"}]}"#) -> DataStub {
        let stub = DataStub()
        let deleted = InstanceDeleteFlag()
        stub.handler = { req in
            if req.method == "DELETE" {
                deleted.set()
                return (200, #"{"ok": true}"#)
            }
            return (200, deleted.isSet ? after : #"{"instances": [{"id": "i1"}, {"id": "i2"}]}"#)
        }
        return stub
    }

    @Test func deleteSendsTheListsRequestThenRefetches() async throws {
        let stub = listStub()
        let model = InstancesModel(repository: { InstanceRepository(client: stub.client) })
        await model.refreshNow()
        let result = await model.delete("i1")
        #expect((try? result.get()) != nil)
        let delete = try #require(stub.requests.first { $0.method == "DELETE" })
        #expect(delete.path == "/instances/i1")
        #expect(delete.queryItems.isEmpty)
        #expect(stub.requests.map { "\($0.method) \($0.path)" }.suffix(2) == ["DELETE /instances/i1", "GET /instances"])
        #expect(model.value?.instances.map(\.id) == ["i2"])
        #expect(model.value?.busyIds.isEmpty == true)
    }

    @Test func aFailedDeleteReportsTheErrorAndKeepsTheInstance() async {
        let stub = DataStub()
        stub.handler = { req in
            req.method == "DELETE" ? (409, #"{"detail": "instance is running"}"#) : (200, #"{"instances": [{"id": "i1"}]}"#)
        }
        let model = InstancesModel(repository: { InstanceRepository(client: stub.client) })
        await model.refreshNow()
        let result = await model.delete("i1")
        guard case .failure(let error) = result else {
            Issue.record("expected a failure")
            return
        }
        #expect((error as? ApiError)?.message == "instance is running")
        #expect(model.value?.errorMessage == "instance is running")
        #expect(model.value?.busyIds.isEmpty == true)
        #expect(model.value?.instances.map(\.id) == ["i1"])
    }

    @Test func aDeleteBeforeTheListLoadedStillRunsAndLoadsIt() async {
        // Instance detail reached without visiting the list first.
        let stub = listStub()
        let model = InstancesModel(repository: { InstanceRepository(client: stub.client) })
        let result = await model.delete("i1")
        #expect((try? result.get()) != nil)
        #expect(model.value?.instances.map(\.id) == ["i2"])
    }

    @Test func theListIsSharedAndASignOutClearsIt() async {
        let stub = listStub()
        let services = AppServices(apiClient: stub.client)
        #expect(services.instances === services.instances)
        await services.instances.refreshNow()
        #expect(services.instances.value != nil)
        services.didSignOut()
        #expect(services.instances.value == nil)
    }

    @Test func theConfirmationIsTheListsOwn() async throws {
        let inst = Instance(id: "alpaca-main", name: "Alpaca Main", createdBy: "user", runCommand: false)
        var ran = false
        let request = instanceDeleteRequest(inst, onConfirm: { ran = true }, onError: { _ in })
        #expect(request.title == "Delete Instance")
        #expect(request.body == "Delete \"Alpaca Main\"? This cannot be undone.")
        #expect(request.confirmLabel == "Delete")
        #expect(request.role == .destructive)
        #expect(!ran)
        try await request.onConfirm()
        #expect(ran)
        let unnamed = Instance(id: "x1", name: "", createdBy: "user", runCommand: false)
        #expect(instanceDeleteRequest(unnamed, onConfirm: {}, onError: { _ in }).body == "Delete \"x1\"? This cannot be undone.")
    }
}

/// A thread-safe flag a stub handler flips.
private nonisolated final class InstanceDeleteFlag: @unchecked Sendable {
    private let lock = NSLock()
    private var value = false

    var isSet: Bool { lock.withLock { value } }

    func set() { lock.withLock { value = true } }
}
