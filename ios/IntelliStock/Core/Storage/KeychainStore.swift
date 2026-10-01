import Foundation
import Security

/// Key-value strings in the iOS keychain, laid out exactly as the Flutter
/// build's `flutter_secure_storage` 9.x wrote them: generic passwords under
/// service `flutter_secure_storage_service`, account = key, UTF-8 data,
/// accessible when unlocked, not synchronizable. The native app installs over
/// the Flutter app with the same bundle ID, so it reads the existing session,
/// server URL and lock settings with no re-login.
nonisolated struct KeychainStore: Sendable {
    static let flutterService = "flutter_secure_storage_service"

    let service: String

    init(service: String = KeychainStore.flutterService) {
        self.service = service
    }

    func read(_ key: String) -> String? {
        var query = baseQuery(key)
        query[kSecReturnData] = true
        query[kSecMatchLimit] = kSecMatchLimitOne
        var result: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &result) == errSecSuccess,
              let data = result as? Data
        else { return nil }
        return String(data: data, encoding: .utf8)
    }

    func write(_ key: String, _ value: String) throws {
        let data = Data(value.utf8)
        let status = SecItemUpdate(baseQuery(key) as CFDictionary, [kSecValueData: data] as CFDictionary)
        if status == errSecSuccess { return }
        guard status == errSecItemNotFound else { throw KeychainError(status: status) }

        var add = baseQuery(key)
        add[kSecValueData] = data
        add[kSecAttrAccessible] = kSecAttrAccessibleWhenUnlocked
        add[kSecAttrSynchronizable] = false
        let added = SecItemAdd(add as CFDictionary, nil)
        guard added == errSecSuccess else { throw KeychainError(status: added) }
    }

    func delete(_ key: String) {
        SecItemDelete(baseQuery(key) as CFDictionary)
    }

    private func baseQuery(_ key: String) -> [CFString: Any] {
        [
            kSecClass: kSecClassGenericPassword,
            kSecAttrService: service,
            kSecAttrAccount: key,
        ]
    }
}

nonisolated struct KeychainError: Error, Equatable {
    let status: OSStatus
}
