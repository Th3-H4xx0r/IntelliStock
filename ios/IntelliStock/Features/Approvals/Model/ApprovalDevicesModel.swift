import Foundation
import Observation

/// Shown when the server answers 404: it predates the approvals routes.
nonisolated let kApprovalsNotOnServer = "This server doesn't have Jarvis approvals yet. Update the backend, then try again."
/// The server accepts a new device's signatures this long after it registers
/// (contract §5). Used when the receipt carries no `active_at`.
nonisolated let kApprovalActivationDelay: TimeInterval = 600

/// This iPhone's standing as an approver on the current server.
nonisolated enum ApprovalDeviceStatus: Hashable, Sendable {
    case notRegistered
    /// Registered; the server accepts its signatures from `activeAt`.
    case activating(ApprovalDeviceRecord)
    case active(ApprovalDeviceRecord)
    /// The loaded server list lacks this record's device id.
    case missingOnServer(ApprovalDeviceRecord)
}

/// Why `name` can't name a device, or nil when it can. The name is trimmed first.
nonisolated func approvalDeviceNameError(_ name: String) -> String? {
    let trimmed = name.trimmingCharacters(in: .whitespacesAndNewlines)
    if trimmed.isEmpty { return "Enter a name for this iPhone." }
    if trimmed.count > 64 { return "Use 64 characters or fewer." }
    return nil
}

/// The message for an approvals request that failed: a 404 means the server
/// has no approvals routes; otherwise the server's or the client's message.
nonisolated func approvalsErrorText(_ error: any Error) -> String {
    if let api = error as? ApiError {
        return api.statusCode == 404 ? kApprovalsNotOnServer : api.message
    }
    return error.localizedDescription
}

/// Settings › Jarvis approvals: registers this iPhone as an approver, lists
/// the approver devices, and removes them.
///
/// Registering makes a new key, proves it signs (Face ID, then a signature
/// over `ApprovalSigning.selfTestMessage` checked against the new public
/// key), and only then posts the public key. A key that never reached the
/// server is deleted, so nothing is left half-registered. The server accepts
/// the device's signatures 10 minutes after it registers.
@Observable
final class ApprovalDevicesModel {
    static let faceIDReason = "Register this iPhone to approve Jarvis's requests"

    private(set) var devices: Loadable<[McpApprovalDevice]> = .loading
    private(set) var registering = false
    /// Device ids with a removal in flight.
    private(set) var removing: Set<String> = []

    @ObservationIgnored private let source: () -> any ApprovalDevicesSource
    @ObservationIgnored private let signer: any ApprovalSigner
    @ObservationIgnored private let store: ApprovalDeviceStore
    @ObservationIgnored private let baseURL: () -> String
    @ObservationIgnored private let clock: () -> Date

    init(source: @escaping () -> any ApprovalDevicesSource, signer: any ApprovalSigner, store: ApprovalDeviceStore,
         baseURL: @escaping () -> String, clock: @escaping () -> Date = Date.init) {
        self.source = source
        self.signer = signer
        self.store = store
        self.baseURL = baseURL
        self.clock = clock
    }

    /// False on the simulator, which signs with a software key.
    var isHardwareBacked: Bool { signer.isHardwareBacked }

    func status(now: Date) -> ApprovalDeviceStatus {
        guard let record = store.record(for: baseURL()) else { return .notRegistered }
        if let list = devices.value, !list.contains(where: { $0.id == record.deviceId }) {
            return .missingOnServer(record)
        }
        return now < record.activeAt ? .activating(record) : .active(record)
    }

    /// A failure becomes `.failed`; a cancelled load changes nothing.
    func load() async {
        devices = await devices.refreshing { try await source().devices() }
    }

    /// Registers this iPhone under `name` (trimmed), replacing any earlier
    /// registration on this server. Nil when it registered; otherwise the
    /// message to show. Does nothing while a registration runs.
    func register(name: String) async -> String? {
        guard !registering else { return nil }
        let trimmed = name.trimmingCharacters(in: .whitespacesAndNewlines)
        if let problem = approvalDeviceNameError(trimmed) { return problem }

        registering = true
        defer { registering = false }

        let base = baseURL()
        let old = store.record(for: base)

        let publicKey: Data
        do {
            publicKey = try signer.createKey()
        } catch ApprovalSignerError.unavailable {
            return "This iPhone has no Secure Enclave, so it can't approve requests."
        } catch {
            discardKey()
            return "Couldn't create a key: " + Self.signerText(error)
        }

        // Face ID, then one signature the new public key must verify.
        do {
            let grant = try await signer.authorize(reason: Self.faceIDReason)
            let signature = try grant.sign(ApprovalSigning.selfTestMessage)
            guard ApprovalSigning.verify(signature, ApprovalSigning.selfTestMessage, publicKey: publicKey) else {
                discardKey()
                return "Couldn't use the new key: signature check failed"
            }
        } catch {
            discardKey()
            if Self.isCancel(error) { return "Registration cancelled." }
            return "Couldn't use the new key: " + Self.signerText(error)
        }

        let receipt: ApprovalDeviceReceipt
        do {
            receipt = try await source().registerDevice(name: trimmed, publicKey: publicKey)
        } catch {
            discardKey()
            return approvalsErrorText(error)
        }

        let record = ApprovalDeviceRecord(baseURL: base, deviceId: receipt.deviceId, name: trimmed,
                                          activeAt: receipt.activeAt ?? clock() + kApprovalActivationDelay)
        do {
            try store.save(record)
        } catch {
            // The server knows a key this iPhone can't name: retire it.
            try? await source().deleteDevice(receipt.deviceId)
            discardKey()
            return "Couldn't save this iPhone's registration. Try again."
        }

        // The old key is gone, so the old device can never sign again.
        if let old, old.deviceId != receipt.deviceId {
            try? await source().deleteDevice(old.deviceId)
        }
        await load()
        return nil
    }

    /// Removes a device from the server; removing this iPhone's also deletes
    /// its key and record. Nil when removed; otherwise the message to show.
    /// A 404 means the server has no such device (or no approvals at all),
    /// so the device is gone either way and counts as removed.
    func remove(_ deviceId: String) async -> String? {
        guard !removing.contains(deviceId) else { return nil }
        removing.insert(deviceId)
        defer { removing.remove(deviceId) }
        do {
            try await source().deleteDevice(deviceId)
        } catch let error as ApiError where error.statusCode == 404 {
            // Already gone.
        } catch {
            return approvalsErrorText(error)
        }
        if store.record(for: baseURL())?.deviceId == deviceId {
            discardKey()
        }
        await load()
        return nil
    }

    // MARK: Helpers

    /// No key and no record: the state a failed registration leaves.
    private func discardKey() {
        signer.deleteKey()
        store.clear()
    }

    private static func isCancel(_ error: any Error) -> Bool {
        (error as? ApprovalSignerError) == .cancelled || error.isCancellation
    }

    /// A signer error in words, for "Couldn't use the new key: …".
    private static func signerText(_ error: any Error) -> String {
        switch error as? ApprovalSignerError {
        case .failed(let cause): cause
        case .unavailable: "no Secure Enclave"
        case .noKey: "the key is missing"
        case .keyInvalidated: "the key no longer works"
        case .cancelled: "cancelled"
        case nil: (error as? ApiError)?.message ?? error.localizedDescription
        }
    }
}
