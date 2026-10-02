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

    /// The value, or nil — for any failure. Use `readChecked` where "the
    /// keychain is unavailable" must not read as "nothing stored".
    func read(_ key: String) -> String? {
        (try? readChecked(key)) ?? nil
    }

    /// The value, nil only when the item does not exist; any other keychain
    /// status throws (as `flutter_secure_storage` did). Before the first
    /// unlock after a reboot — when iOS may prewarm the app — the keychain
    /// answers `errSecInteractionNotAllowed`, which is not "signed out".
    func readChecked(_ key: String) throws -> String? {
        var query = baseQuery(key)
        query[kSecReturnData] = true
        query[kSecMatchLimit] = kSecMatchLimitOne
        var result: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess else { throw KeychainError(status: status) }
        guard let data = result as? Data else { return nil }
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
