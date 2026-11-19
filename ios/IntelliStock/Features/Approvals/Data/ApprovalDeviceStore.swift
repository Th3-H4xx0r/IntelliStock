import Foundation

/// This iPhone's approval device as the server registered it. Apart from the
/// key itself, it is the only approval data kept in the keychain.
nonisolated struct ApprovalDeviceRecord: Codable, Hashable, Sendable {
    let baseURL: String
    let deviceId: String
    let name: String
    /// The server accepts its signatures from this moment.
    let activeAt: Date
}

nonisolated struct ApprovalDeviceStore: Sendable {
    static let storageKey = "mcp_approval_device"

    let storage: any SecureStorage

    /// The record, nil unless it was stored for exactly this server.
    func record(for baseURL: String) -> ApprovalDeviceRecord? {
        guard let text = storage.read(Self.storageKey) else { return nil }
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .secondsSince1970
        guard let record = try? decoder.decode(ApprovalDeviceRecord.self, from: Data(text.utf8)),
              record.baseURL == baseURL else { return nil }
        return record
    }

    func save(_ record: ApprovalDeviceRecord) throws {
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .secondsSince1970
        let data = try encoder.encode(record)
        try storage.write(Self.storageKey, String(decoding: data, as: UTF8.self))
    }

    func clear() {
        storage.delete(Self.storageKey)
    }
}
